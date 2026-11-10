"""
Annonce de l'écran de salle posée par les BÉNÉVOLES — `/live/annonce`
(série annonce, lot 1).

Ce qui est propre à l'ouverture aux bénévoles :

- l'accès : jeton exigé en propre (le garde du module `live` ne règle que la
  visibilité), et l'interrupteur du bureau, FERMÉ par défaut ;
- le lien « Annonce en salle » du menu bénévole, qui suit les deux règles ;
- la dernière annonce posée l'emporte, d'où qu'elle vienne ;
- la durée en boutons, bornée côté serveur quoi qu'on envoie ;
- la redirection 303 qui ne porte jamais le texte ;
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

from app import services

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


def _minutes_jusqua_echeance():
    echeance = datetime.fromisoformat(_parametre(services.CLE_ANNONCE_EXPIRE))
    return round((echeance - datetime.now(timezone.utc)).total_seconds() / 60)


def _poser(client, texte, duree="30", **kwargs):
    donnees = {"annonce": texte}
    if duree is not None:
        donnees["annonce_duree"] = duree
    return client.post("/live/annonce", data=donnees, **kwargs)


def _annonce_en_salle(client):
    return client.get("/live/data").json().get("annonce")


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
    assert _parametre(services.CLE_ANNONCE) is None


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
    assert _parametre(services.CLE_ANNONCE) is None
    assert _annonce_en_salle(benevole) is None


def test_interrupteur_ferme_rien_au_journal(benevole, _journal_isole):
    _poser(benevole, "Tombola à 15 h")
    _poser(benevole, "")
    texte = _journal_isole.read_text(encoding="utf-8") if _journal_isole.exists() else ""
    assert "annonce" not in texte


def test_interrupteur_ferme_admin_connecte_ne_pose_pas_non_plus(admin):
    """Arbitrage du lot : même règle pour tous ; l'admin a sa propre page."""
    r = admin.get("/live/annonce")
    assert "Le bureau n'a pas ouvert l'annonce aux bénévoles" in r.text
    assert 'href="/admin/ecran-salle"' in r.text
    _poser(admin, "Tombola à 15 h")
    assert _parametre(services.CLE_ANNONCE) is None
    assert LIEN_MENU not in admin.get("/admin").text


def test_interrupteur_ouvert_lien_present_et_pose_effective(benevole):
    _ouvrir()
    assert LIEN_MENU in benevole.get("/catalogue").text
    r = _poser(benevole, "Tombola à 15 h, stand accueil", "15")
    assert r.status_code == 200  # redirection suivie
    assert _annonce_en_salle(benevole) == "Tombola à 15 h, stand accueil"
    assert abs(_minutes_jusqua_echeance() - 15) <= 1
    assert "Annonce enregistrée, affichée en salle jusqu&#39;à" in r.text
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
    assert _parametre(services.CLE_ANNONCE) is None


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


def test_effacer_l_annonce_du_bureau_ne_ferme_pas_l_interrupteur(admin):
    _ouvrir()
    admin.post("/admin/ecran-salle", data={"annonce": "Tombola", "annonce_duree": "30",
                                           **PANNEAUX})
    admin.post("/admin/ecran-salle", data={"annonce": "", "annonce_duree": "", **PANNEAUX})
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) == "1"


def test_fermer_l_interrupteur_ne_retire_pas_l_annonce_en_cours(admin, benevole):
    _ouvrir()
    _poser(benevole, "Tombola à 15 h")
    r = admin.post("/admin/ecran-salle/benevoles", data={})
    assert "reste en place" in r.text
    assert _annonce_en_salle(admin) == "Tombola à 15 h"
    # Le bénévole, lui, ne peut plus la toucher.
    _poser(benevole, "")
    assert _annonce_en_salle(admin) == "Tombola à 15 h"


# ---------------------------------------------------------------------------
# 4. La dernière l'emporte
# ---------------------------------------------------------------------------
def test_le_benevole_remplace_l_annonce_du_bureau(admin, benevole):
    _ouvrir()
    admin.post("/admin/ecran-salle", data={"annonce": "Tombola à 15 h",
                                           "annonce_duree": "240", **PANNEAUX})
    page = benevole.get("/live/annonce").text
    # L'annonce en cours est sous les yeux, et le bouton dit ce qu'il fait.
    assert "Tombola à 15 h" in page
    assert "En cours, affichée en salle jusqu&#39;à" in page \
        or "En cours, affichée en salle jusqu'à" in page
    assert "Remplacer l&#39;annonce" in page or "Remplacer l'annonce" in page
    _poser(benevole, "Tournoi déplacé en salle B", "30")
    assert _annonce_en_salle(benevole) == "Tournoi déplacé en salle B"
    assert abs(_minutes_jusqua_echeance() - 30) <= 1


def test_le_bureau_remplace_l_annonce_du_benevole(admin, benevole):
    _ouvrir()
    _poser(benevole, "Tournoi déplacé en salle B", "15")
    admin.post("/admin/ecran-salle", data={"annonce": "Tombola à 15 h",
                                           "annonce_duree": "60", **PANNEAUX})
    assert _annonce_en_salle(benevole) == "Tombola à 15 h"
    page = admin.get("/admin/ecran-salle").text
    assert "Tombola à 15 h" in page


def test_page_sans_annonce(benevole):
    _ouvrir()
    page = benevole.get("/live/annonce").text
    assert "Aucune annonce n&#39;est affichée" in page or "Aucune annonce n'est affichée" in page
    assert "Afficher en salle" in page
    assert "Effacer l&#39;annonce" not in page and "Effacer l'annonce" not in page


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
    assert _annonce_en_salle(benevole) == "Tombola à 15 h"
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
    assert len(_annonce_en_salle(benevole)) == services.LONGUEUR_ANNONCE


# ---------------------------------------------------------------------------
# 6. Effacer : saisie vide, journal seulement si une annonce était affichée
# ---------------------------------------------------------------------------
def test_texte_vide_efface_et_journalise_seulement_si_affichee(benevole, _journal_isole):
    _ouvrir()

    def _effacees():
        if not _journal_isole.exists():
            return []
        return [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()
                if '"annonce_effacee"' in l]

    # Rien d'affiché : effacer ne laisse aucune ligne fictive.
    r = _poser(benevole, "", "")
    assert "Annonce effacée." in r.text
    assert _effacees() == []

    _poser(benevole, "Tombola à 15 h")
    page = benevole.get("/live/annonce").text
    assert "Effacer l&#39;annonce" in page or "Effacer l'annonce" in page
    _poser(benevole, "   ", "")
    assert _annonce_en_salle(benevole) is None
    assert _parametre(services.CLE_ANNONCE) is None
    assert _parametre(services.CLE_ANNONCE_EXPIRE) is None
    lignes = _effacees()
    assert len(lignes) == 1
    assert lignes[0]["qui"] == "benevole" and lignes[0].get("appareil")


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
    assert r.headers["location"] == "/live/annonce?ok=effacee"


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
    assert "Annonce enregistrée" not in r.text
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
    page = benevole.get("/live/annonce?ok=effacee").text
    assert page.count("<h1") == 1
    assert '<div role="status">' in page
    statut = page[page.index('<div role="status">'):]
    assert "Annonce effacée." in statut[:400]
