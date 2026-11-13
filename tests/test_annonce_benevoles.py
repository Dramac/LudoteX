"""
Annonces de l'écran de salle posées par les BÉNÉVOLES — `/live/annonce`
(série annonce, lots 1 et 2).

Ce qui est propre à l'ouverture aux bénévoles :

- l'accès : jeton exigé en propre (le garde du module `live` ne règle que la
  visibilité), et l'interrupteur du bureau, FERMÉ par défaut — pour ajouter
  comme pour effacer ;
- le lien « Annonce en salle » du menu bénévole, qui suit les deux règles ;
- les annonces s'AJOUTENT, d'où qu'elles viennent, et chacun peut effacer
  celles des autres, une par une ;
- la durée en boutons, bornée côté serveur quoi qu'on envoie ;
- la redirection 303 qui ne porte jamais le texte, sauf deux rendus directs
  (garde-fou atteint, saisie conservée ; retrait, annonce nommée) ;
- la liste : la plus récente d'abord, un bouton « Effacer » au nom
  accessible propre à chaque annonce ;
- la consigne, un seul domicile pour les deux pages.

Le journal sans le texte est verrouillé par tests/test_journal_interdits.py
(scénario étendu à cette page) ; les règles de l'annonce elle-même (bornes,
effacement, archives) par tests/test_annonce_ecran_salle.py.

Aucun exemple ne nomme personne : les textes d'annonce sont des situations.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from markupsafe import escape

from app import services
from app.routes import live

JETON = "jeton-test-secret-32-caracteres"
MOT_DE_PASSE = "secret-admin-123"
PANNEAUX = {"panneau_chiffres": "1", "panneau_tournois": "1",
            "panneau_programme": "1", "panneau_mouvements": "1"}
LIEN_MENU = '<a href="/live/annonce">Annonce en salle</a>'


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.setenv("PRET_TOKEN", JETON)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    db.init_db()
    return tmp_path


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def visiteur(bases):
    return _client()


@pytest.fixture
def benevole(bases):
    c = _client()
    # Comme un vrai téléphone : /acces pose le jeton ET le cookie d'appareil,
    # que le journal inscrit à côté de chaque action bénévole.
    c.get(f"/acces?jeton={JETON}", follow_redirects=False)
    return c


@pytest.fixture
def admin(bases):
    c = _client()
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return c


def _conn():
    from app import db
    return db.get_connection()


def _ouvrir(ouverte=True):
    conn = _conn()
    try:
        services.ecrire_annonce_benevoles(conn, ouverte)
    finally:
        conn.close()


def _parametre(cle):
    conn = _conn()
    try:
        return services.lire_parametre(conn, cle, None)
    finally:
        conn.close()


def _lignes():
    conn = _conn()
    try:
        return [dict(l) for l in conn.execute(
            "SELECT id, texte, expire_le FROM annonces_ecran ORDER BY id")]
    finally:
        conn.close()


def _minutes_jusqua_echeance():
    echeance = datetime.fromisoformat(_lignes()[-1]["expire_le"])
    return round((echeance - datetime.now(timezone.utc)).total_seconds() / 60)


def _poser(client, texte, duree="30", **kwargs):
    donnees = {"annonce": texte}
    if duree is not None:
        donnees["annonce_duree"] = duree
    return client.post("/live/annonce", data=donnees, **kwargs)


def _effacer(client, identifiant, **kwargs):
    return client.post("/live/annonce/effacer", data={"annonce_id": str(identifiant)},
                       **kwargs)


def _poser_au_bureau(admin, texte, duree="60"):
    return admin.post("/admin/ecran-salle/annonce",
                      data={"annonce": texte, "annonce_duree": duree})


def _en_salle(client):
    return [a["texte"] for a in client.get("/live/data").json().get("annonces", [])]


def _id(texte):
    return next(l["id"] for l in _lignes() if l["texte"] == texte)


# ---------------------------------------------------------------------------
# 1. Accès : le jeton, en propre
# ---------------------------------------------------------------------------
def test_sans_jeton_page_acces_reserve_et_rien_d_ecrit(visiteur):
    _ouvrir()
    r = visiteur.get("/live/annonce")
    assert r.status_code == 403
    assert "Accès réservé aux bénévoles" in r.text
    r = _poser(visiteur, "Tombola à 15 h")
    assert r.status_code == 403
    assert "Accès réservé aux bénévoles" in r.text
    assert _lignes() == []


def test_module_live_sur_tous_n_ouvre_pas_la_page(visiteur):
    """Le garde du module règle la VISIBILITÉ : « tous » laisse passer, le jeton non."""
    from app.modules import ecrire_etat_module

    conn = _conn()
    ecrire_etat_module(conn, "live", "tous")
    conn.close()
    _ouvrir()
    assert visiteur.get("/live/annonce").status_code == 403


# ---------------------------------------------------------------------------
# 2. L'interrupteur du bureau : fermé par défaut
# ---------------------------------------------------------------------------
def test_base_neuve_interrupteur_ferme_sans_ligne_ecrite(bases):
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) is None
    conn = _conn()
    try:
        assert services.lire_annonce_benevoles(conn) is False
        # Toute valeur autre que « 1 » laisse fermé : c'est l'état sûr.
        for valeur in ("0", "", "oui", "true"):
            services.ecrire_parametre(conn, services.CLE_ANNONCE_BENEVOLES, valeur)
            assert services.lire_annonce_benevoles(conn) is False
    finally:
        conn.close()


def test_interrupteur_ferme_lien_absent_page_claire_post_sans_effet(benevole):
    assert LIEN_MENU not in benevole.get("/catalogue").text
    r = benevole.get("/live/annonce")
    assert r.status_code == 200
    assert "Le bureau n'a pas ouvert l'annonce aux bénévoles" in r.text
    assert 'href="/scanner"' in r.text
    assert 'name="annonce"' not in r.text
    # Pas de lien d'administration pour un bénévole.
    assert 'href="/admin/ecran-salle"' not in r.text

    r = _poser(benevole, "Tombola à 15 h", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/live/annonce"
    assert _lignes() == []
    assert _en_salle(benevole) == []


def test_interrupteur_ferme_effacer_refuse_aussi(admin, benevole):
    """Arbitrage du lot 2 : même règle pour effacer que pour poser."""
    _poser_au_bureau(admin, "Tombola à 15 h")
    r = _effacer(benevole, _id("Tombola à 15 h"), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/live/annonce"
    assert _en_salle(admin) == ["Tombola à 15 h"]


def test_interrupteur_ferme_rien_au_journal(benevole, _journal_isole):
    _poser(benevole, "Tombola à 15 h")
    _effacer(benevole, 1)
    texte = _journal_isole.read_text(encoding="utf-8") if _journal_isole.exists() else ""
    assert "annonce" not in texte


def test_interrupteur_ferme_admin_connecte_ne_pose_pas_non_plus(admin):
    """Arbitrage du lot : même règle pour tous ; l'admin a sa propre page."""
    r = admin.get("/live/annonce")
    assert "Le bureau n'a pas ouvert l'annonce aux bénévoles" in r.text
    assert 'href="/admin/ecran-salle"' in r.text
    _poser(admin, "Tombola à 15 h")
    assert _lignes() == []
    assert LIEN_MENU not in admin.get("/admin").text


def test_interrupteur_ouvert_lien_present_et_pose_effective(benevole):
    _ouvrir()
    assert LIEN_MENU in benevole.get("/catalogue").text
    r = _poser(benevole, "Tombola à 15 h, stand accueil", "15")
    assert r.status_code == 200  # redirection suivie
    assert _en_salle(benevole) == ["Tombola à 15 h, stand accueil"]
    assert abs(_minutes_jusqua_echeance() - 15) <= 1
    assert "Annonce ajoutée, affichée en salle jusqu&#39;à" in r.text
    assert "resultat-ok" in r.text


def test_lien_place_juste_apres_ecran_salle(benevole):
    _ouvrir()
    page = benevole.get("/catalogue").text
    menu = page[page.index('<nav class="menu-benevole">'):]
    menu = menu[:menu.index("</nav>")]
    liens = [l.split('"')[1] for l in menu.split('<a href=')[1:]]
    assert liens[liens.index("/live") + 1] == "/live/annonce"


def test_admin_connecte_voit_le_lien_quand_c_est_ouvert(admin):
    _ouvrir()
    assert LIEN_MENU in admin.get("/admin").text
    assert LIEN_MENU in admin.get("/catalogue").text


def test_module_live_desactive_lien_absent_route_refusee(benevole):
    from app.modules import ecrire_etat_module

    _ouvrir()
    conn = _conn()
    ecrire_etat_module(conn, "live", "desactive")
    conn.close()
    assert LIEN_MENU not in benevole.get("/catalogue").text
    r = benevole.get("/live/annonce")
    assert r.status_code == 404
    assert "Module indisponible" in r.text
    r = _poser(benevole, "Tombola à 15 h")
    assert r.status_code == 404
    assert _lignes() == []


# ---------------------------------------------------------------------------
# 3. L'interrupteur côté bureau
# ---------------------------------------------------------------------------
def test_ecran_admin_porte_l_interrupteur_decoche_par_defaut(admin):
    page = admin.get("/admin/ecran-salle").text
    assert 'action="/admin/ecran-salle/benevoles"' in page
    assert "Les bénévoles peuvent afficher une annonce" in page
    case = page[page.index('name="annonce_benevoles"'):][:200]
    assert "checked" not in case.split(">")[0]


def test_ouvrir_puis_fermer_depuis_l_admin_et_journal(admin, _journal_isole):
    r = admin.post("/admin/ecran-salle/benevoles", data={"annonce_benevoles": "1"})
    assert "le lien" in r.text and "Annonce en salle" in r.text
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) == "1"
    # Réenregistrer sans changement n'écrit pas de seconde ligne.
    admin.post("/admin/ecran-salle/benevoles", data={"annonce_benevoles": "1"})
    admin.post("/admin/ecran-salle/benevoles", data={})
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) == "0"
    lignes = [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()
              if '"annonce_benevoles_modifiee"' in l]
    assert [l["objet"] for l in lignes] == ["ouverte", "fermée"]
    assert all(l["qui"] == "admin" for l in lignes)


def test_l_interrupteur_exige_le_mot_de_passe(benevole):
    r = benevole.post("/admin/ecran-salle/benevoles", data={"annonce_benevoles": "1"},
                      follow_redirects=False)
    assert r.status_code in (302, 303)
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) is None


def test_retirer_une_annonce_ne_ferme_pas_l_interrupteur(admin):
    _ouvrir()
    _poser_au_bureau(admin, "Tombola")
    admin.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": str(_id("Tombola"))})
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) == "1"


def test_fermer_l_interrupteur_ne_retire_aucune_annonce(admin, benevole):
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    _poser(benevole, "Vestiaire fermé à 18 h")
    r = admin.post("/admin/ecran-salle/benevoles", data={})
    assert "restent en place" in r.text
    assert _en_salle(admin) == ["Tombola à 15 h", "Vestiaire fermé à 18 h"]
    # Le bénévole, lui, ne peut plus y toucher.
    _effacer(benevole, _id("Tombola à 15 h"))
    assert _en_salle(admin) == ["Tombola à 15 h", "Vestiaire fermé à 18 h"]


def test_la_note_de_l_interrupteur_dit_que_les_annonces_s_ajoutent(admin):
    page = admin.get("/admin/ecran-salle").text
    note = page[page.index('id="annonce-benevoles-note"'):]
    note = " ".join(note[:note.index("</p>")].split())
    assert "s&#39;ajoutent aux vôtres" in note or "s'ajoutent aux vôtres" in note
    assert "effacer celles des autres" in note
    assert "ne retire aucune annonce" in note
    assert "remplace" not in note


# ---------------------------------------------------------------------------
# 4. Les annonces s'ajoutent ; chacun efface celles des autres
# ---------------------------------------------------------------------------
def test_le_benevole_ajoute_a_l_annonce_du_bureau(admin, benevole):
    _ouvrir()
    _poser_au_bureau(admin, "Tombola à 15 h", "240")
    page = benevole.get("/live/annonce").text
    # Les annonces qui défilent sont sous les yeux, et le bouton dit ce
    # qu'il fait.
    assert "Tombola à 15 h" in page
    assert "Ajouter une annonce" in page
    assert "Remplacer l" not in page and "Afficher en salle" not in page
    _poser(benevole, "Tournoi déplacé en salle B", "30")
    assert _en_salle(benevole) == ["Tombola à 15 h", "Tournoi déplacé en salle B"]
    assert abs(_minutes_jusqua_echeance() - 30) <= 1


def test_le_benevole_efface_l_annonce_du_bureau(admin, benevole, _journal_isole):
    _ouvrir()
    _poser_au_bureau(admin, "Tombola à 15 h")
    _poser(benevole, "Tournoi déplacé en salle B")
    r = _effacer(benevole, _id("Tombola à 15 h"))
    assert r.status_code == 200
    assert "Annonce retirée de l&#39;écran : « Tombola à 15 h »." in r.text
    assert _en_salle(benevole) == ["Tournoi déplacé en salle B"]
    ligne = [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()
             if '"annonce_effacee"' in l][-1]
    assert ligne["qui"] == "benevole" and ligne.get("appareil")
    assert "objet" not in ligne and "detail" not in ligne


def test_le_bureau_efface_l_annonce_du_benevole(admin, benevole):
    _ouvrir()
    _poser(benevole, "Tournoi déplacé en salle B", "15")
    r = admin.post("/admin/ecran-salle/annonce/effacer",
                   data={"annonce_id": str(_id("Tournoi déplacé en salle B"))})
    assert "Annonce retirée de l&#39;écran : « Tournoi déplacé en salle B »." in r.text
    assert _en_salle(benevole) == []


def test_effacer_deux_fois_message_neutre(admin, benevole, _journal_isole):
    """
    Un onglet du bureau a effacé l'annonce ; le téléphone, resté sur sa liste,
    la retire à son tour (ou recharge la page après un retrait : le
    navigateur renvoie le même formulaire).
    """
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    identifiant = _id("Tombola à 15 h")
    admin.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": str(identifiant)})
    avant = _journal_isole.read_text(encoding="utf-8")
    r = _effacer(benevole, identifiant)
    assert r.status_code == 200
    assert str(escape(live.MESSAGE_PLUS_AFFICHEE)) in r.text
    assert _journal_isole.read_text(encoding="utf-8") == avant


@pytest.mark.parametrize("valeur", ["999", "abc", "", "1;DROP", "-3"])
def test_identifiant_forge_jamais_d_erreur(benevole, valeur):
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    r = benevole.post("/live/annonce/effacer", data={"annonce_id": valeur})
    assert r.status_code == 200
    assert str(escape(live.MESSAGE_PLUS_AFFICHEE)) in r.text
    assert _en_salle(benevole) == ["Tombola à 15 h"]


def test_effacer_sans_jeton_refuse(visiteur):
    _ouvrir()
    conn = _conn()
    try:
        services.poser_annonce(conn, "Tombola à 15 h", 30)
    finally:
        conn.close()
    r = _effacer(visiteur, _id("Tombola à 15 h"))
    assert r.status_code == 403
    assert len(_lignes()) == 1


def test_l_adresse_d_effacement_en_get_ramene_a_la_page(benevole):
    _ouvrir()
    r = benevole.get("/live/annonce/effacer", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/live/annonce"


def test_page_sans_annonce(benevole):
    _ouvrir()
    page = benevole.get("/live/annonce").text
    assert "Aucune annonce n&#39;est affichée" in page or "Aucune annonce n'est affichée" in page
    assert "Ajouter une annonce" in page
    assert 'action="/live/annonce/effacer"' not in page
    assert "Tour complet" not in page


# ---------------------------------------------------------------------------
# 4 bis. La liste : la plus récente d'abord, et l'ordre de la page
# ---------------------------------------------------------------------------
def test_liste_la_plus_recente_d_abord_avec_heures(benevole):
    _ouvrir()
    _poser(benevole, "Tombola à 15 h", "15")
    _poser(benevole, "Vestiaire fermé à 18 h", "60")
    page = benevole.get("/live/annonce").text
    assert page.index("Vestiaire fermé à 18 h") < page.index("Tombola à 15 h")
    assert page.count("Posée à ") == 2
    assert page.count(", affichée jusqu&#39;à ") + page.count(", affichée jusqu'à ") == 2
    assert f"Tour complet de l'écran : {live.duree_tour(2)}." in page


def test_formulaire_avant_la_liste(benevole):
    """Le geste principal en haut : son bouton reste visible sans défiler."""
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    page = benevole.get("/live/annonce").text
    assert page.index("Ajouter une annonce</button>") < page.index("Affichées en salle")
    assert page.index("Affichées en salle") < page.index("Tombola à 15 h")


def test_chaque_bouton_effacer_a_un_nom_accessible_distinct(benevole):
    """
    Plusieurs boutons « Effacer » identiques ne se distinguent pas au lecteur
    d'écran. L'heure seule ne suffit pas (mesuré à l'essai : trois annonces
    posées la même minute) : le nom porte le début du texte, déjà public.
    """
    import re

    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    _poser(benevole, "Vestiaire fermé à 18 h")
    _poser(benevole, "x" * 120)
    page = benevole.get("/live/annonce").text
    boutons = re.findall(r'<button type="submit" class="bouton bouton-secondaire '
                         r'bouton-effacer-annonce">(.*?)</button>', page, re.S)
    assert len(boutons) == 3
    noms = [re.sub(r"<[^>]+>", "", b) for b in boutons]
    assert len(set(noms)) == 3
    for b in boutons:
        # Texte visible « Effacer », complété en texte masqué ; pas
        # d'aria-label qui contredirait le texte visible.
        assert b.startswith('Effacer<span class="sr-only"> l\'annonce « ')
        assert "aria-label" not in b
    assert any("Vestiaire fermé à 18 h », posée à " in n for n in noms)
    # Tronqué à 40 caractères : jamais 200 caractères lus à chaque bouton.
    assert any("x" * 39 + "… »" in n for n in noms)


def test_cible_tactile_du_bouton_effacer():
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css"
           / "style.css").read_text(encoding="utf-8")
    regle = css[css.index(".bouton.bouton-effacer-annonce {"):]
    regle = regle[:regle.index("}")]
    assert "min-width: 44px;" in regle and "min-height: 44px;" in regle


# ---------------------------------------------------------------------------
# 5. Durée : boutons, bornée côté serveur, jamais refusée
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("saisie, attendue", [
    ("0", services.DUREE_ANNONCE_DEFAUT_MIN),
    ("-5", services.DUREE_ANNONCE_DEFAUT_MIN),
    ("abc", services.DUREE_ANNONCE_DEFAUT_MIN),
    ("99999", services.DUREE_ANNONCE_MAX_MIN),
    (None, services.DUREE_ANNONCE_DEFAUT_MIN),
])
def test_duree_forgee_bornee_jamais_refusee(benevole, saisie, attendue):
    _ouvrir()
    r = _poser(benevole, "Tombola à 15 h", saisie, follow_redirects=False)
    assert r.status_code == 303
    assert "corrigee=1" in r.headers["location"]
    assert _en_salle(benevole) == ["Tombola à 15 h"]
    assert abs(_minutes_jusqua_echeance() - attendue) <= 1
    page = benevole.get(r.headers["location"]).text
    assert "resultat-attention" in page
    assert "a été corrigée" in page
    # La saisie forgée n'est ni dans l'adresse, ni reprise à l'écran.
    if saisie:
        assert saisie not in r.headers["location"].split("fin=")[0]
        assert f"« {saisie} »" not in page


def test_durees_proposees_dans_les_bornes_et_defaut_compris():
    from app.routes.live import DUREES_ANNONCE_BENEVOLE

    valeurs = [minutes for minutes, _ in DUREES_ANNONCE_BENEVOLE]
    assert valeurs
    assert all(1 <= v <= services.DUREE_ANNONCE_MAX_MIN for v in valeurs)
    assert services.DUREE_ANNONCE_DEFAUT_MIN in valeurs
    assert len(set(valeurs)) == len(valeurs)


def test_durees_en_boutons_radio_defaut_coche(benevole):
    from app.routes.live import DUREES_ANNONCE_BENEVOLE

    _ouvrir()
    page = benevole.get("/live/annonce").text
    assert "<fieldset" in page and "<legend>Durée d&#39;affichage</legend>" in page \
        or "<legend>Durée d'affichage</legend>" in page
    assert 'type="number"' not in page
    for minutes, _ in DUREES_ANNONCE_BENEVOLE:
        assert f'name="annonce_duree" value="{minutes}"' in page
    defaut = page[page.index(f'value="{services.DUREE_ANNONCE_DEFAUT_MIN}"'):][:80]
    assert "checked" in defaut.split(">")[0]
    assert page.count("checked") == 1


def test_texte_borne_comme_au_bureau(benevole):
    _ouvrir()
    page = benevole.get("/live/annonce").text
    assert f'maxlength="{services.LONGUEUR_ANNONCE}"' in page
    _poser(benevole, "x" * (services.LONGUEUR_ANNONCE + 50))
    assert len(_en_salle(benevole)[0]) == services.LONGUEUR_ANNONCE


# ---------------------------------------------------------------------------
# 6. Texte vide : sans effet ; garde-fou : la saisie n'est pas perdue
# ---------------------------------------------------------------------------
def test_texte_vide_sans_effet_ni_journal(benevole, _journal_isole):
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    r = _poser(benevole, "   ", "", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/live/annonce?ok=vide"
    page = benevole.get(r.headers["location"]).text
    assert str(escape(live.MESSAGE_TEXTE_VIDE)) in page
    assert _en_salle(benevole) == ["Tombola à 15 h"]
    assert "annonce_effacee" not in _journal_isole.read_text(encoding="utf-8")


def test_plafond_atteint_page_rendue_saisie_et_duree_conservees(benevole, _journal_isole):
    _ouvrir()
    conn = _conn()
    try:
        for i in range(services.ANNONCES_MAX_ACTIVES):
            services.poser_annonce(conn, f"Annonce numéro {i}", 30)
    finally:
        conn.close()
    avant = _journal_isole.read_text(encoding="utf-8") if _journal_isole.exists() else ""
    r = _poser(benevole, "Tournoi déplacé en salle B", "60", follow_redirects=False)
    # Pas de redirection : l'adresse ne pourrait pas porter la saisie.
    assert r.status_code == 200
    assert str(escape(live.MESSAGE_PLAFOND)) in r.text
    assert ">Tournoi déplacé en salle B</textarea>" in r.text
    coche = r.text[r.text.index('name="annonce_duree" value="60"'):][:60]
    assert "checked" in coche.split(">")[0]
    assert r.text.count("checked") == 1
    assert len(_lignes()) == services.ANNONCES_MAX_ACTIVES
    apres = _journal_isole.read_text(encoding="utf-8") if _journal_isole.exists() else ""
    assert apres == avant


def test_journal_de_la_pose_benevole(benevole, _journal_isole):
    _ouvrir()
    _poser(benevole, "Tournoi déplacé en salle B", "60")
    lignes = [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()
              if '"annonce_posee"' in l]
    assert len(lignes) == 1
    assert lignes[0]["qui"] == "benevole"
    assert lignes[0].get("appareil")
    assert lignes[0]["objet"] == "60 min"
    assert "detail" not in lignes[0]
    assert "salle B" not in _journal_isole.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 7. La redirection : 303, jamais le texte
# ---------------------------------------------------------------------------
def test_redirection_303_sans_le_texte(benevole):
    _ouvrir()
    texte = "Tournoi déplacé en salle B"
    r = _poser(benevole, texte, "30", follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/live/annonce?ok=posee&fin=")
    for fragment in ("Tournoi", "salle", "B", "d%C3%A9plac"):
        assert fragment not in location
    r = _poser(benevole, "", "", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/live/annonce?ok=vide"


@pytest.mark.parametrize("adresse", [
    "/live/annonce?ok=posee&fin=<script>",
    "/live/annonce?ok=posee",
    "/live/annonce?ok=inconnu",
    "/live/annonce?ok=posee&fin=Appelez%20le%20bureau",
])
def test_adresse_forgee_aucun_message_jamais_d_erreur(benevole, adresse):
    _ouvrir()
    r = benevole.get(adresse)
    assert r.status_code == 200
    assert "Annonce ajoutée" not in r.text
    assert "Appelez le bureau" not in r.text


# ---------------------------------------------------------------------------
# 8. L'alerte tournoi occupe le bandeau : la page le dit
# ---------------------------------------------------------------------------
def _alerte_qualifiante():
    from app.routes import live
    from app.tournoi import db as tdb
    from app.tournoi import services as tournoi_services

    conn = _conn()
    services.ecrire_parametre(conn, live.CLE_ALERTE_MESSAGE, "Rapportez {jeu} !")
    conn.close()
    debut = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    conn_t = tdb.get_connection()
    try:
        tid = tournoi_services.creer_tournoi(conn_t, "T", jeu="Catan", duree_min=None,
                                             date_heure=debut)
        tournoi_services.changer_etat(conn_t, tid, "inscriptions")
    finally:
        conn_t.close()
    return datetime.fromisoformat(debut).astimezone(services.FUSEAU_LOCAL).strftime("%H:%M")


def test_alerte_tournoi_signalee_avec_l_heure_de_reprise(benevole):
    _ouvrir()
    reprise = _alerte_qualifiante()
    page = benevole.get("/live/annonce").text
    assert "Une alerte de tournoi occupe le bandeau de la salle" in page
    assert f"jusqu&#39;à {reprise}" in page or f"jusqu'à {reprise}" in page
    assert "les annonces s&#39;afficheront" in page or "les annonces s'afficheront" in page


def test_alerte_muette_si_module_tournois_desactive(benevole):
    from app.modules import ecrire_etat_module

    _ouvrir()
    _alerte_qualifiante()
    conn = _conn()
    ecrire_etat_module(conn, "tournois", "desactive")
    conn.close()
    assert "Une alerte de tournoi occupe" not in benevole.get("/live/annonce").text


# ---------------------------------------------------------------------------
# 9. La consigne : sur les deux pages, un seul domicile
# ---------------------------------------------------------------------------
def test_consigne_sur_les_deux_pages(admin, benevole):
    _ouvrir()
    for page in (admin.get("/admin/ecran-salle").text, benevole.get("/live/annonce").text):
        assert 'id="annonce-consigne"' in page
        assert 'aria-describedby="annonce-consigne"' in page
        assert "Décrivez une situation, jamais" in page


def test_texte_de_la_consigne_a_un_seul_domicile():
    gabarits = Path(__file__).resolve().parent.parent / "app" / "templates"
    porteurs = [f.name for f in gabarits.rglob("*.html")
                if "Décrivez une situation, jamais" in f.read_text(encoding="utf-8")]
    assert porteurs == ["_consigne_annonce.html"]


def test_accessibilite_de_la_page(benevole):
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    page = _effacer(benevole, _id("Tombola à 15 h")).text
    assert page.count("<h1") == 1
    assert page.count("<h2") == 1
    assert '<div role="status">' in page
    statut = page[page.index('<div role="status">'):]
    assert "Annonce retirée de l&#39;écran" in statut[:400]
