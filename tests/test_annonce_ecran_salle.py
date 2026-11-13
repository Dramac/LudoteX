"""
Annonces de l'écran de salle : une porte d'entrée à donnée personnelle,
refermée (constat RGPD-01 de l'audit de pré-production), et devenue PLURIELLE
(série annonce, lot 2 : table `annonces_ecran`, défilement en salle).

Les annonces sont des textes libres tapés par le bureau (et les bénévoles, si
le bureau l'a permis) et servis AU PUBLIC sur /live et /live/data. Ce fichier
verrouille :

1. une durée d'affichage obligatoire et bornée, propre à chaque annonce,
   jamais bloquante ;
2. une consigne sous le champ ;
3. (le journal sans le texte : tests/test_journal_interdits.py et
   tests/test_journal_appels.py) ;
4. des textes EFFACÉS, pas seulement masqués, et prouvés SUR LES OCTETS du
   fichier — aux cinq chemins d'effacement : démarrage, sauvegarde,
   archives, clôture, réinitialisation de la formation ;
5. la reprise de l'annonce unique d'avant la 1.32 (clés de `parametres`) ;
6. un identifiant jamais réutilisé (`AUTOINCREMENT`), le garde-fou des 50
   annonces, sa tenue sous accès simultanés ;
7. /live/data en liste, et la durée de défilement à un seul domicile.

Les pages de saisie (liste, Effacer) côté bénévole sont dans
tests/test_annonce_benevoles.py.

Aucun exemple ne nomme personne : les textes d'annonce sont des situations.
"""

import sqlite3
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from markupsafe import escape

from app import services
from app.routes import live

MOT_DE_PASSE = "secret-admin-123"
PANNEAUX = {"panneau_chiffres": "1", "panneau_tournois": "1",
            "panneau_programme": "1", "panneau_mouvements": "1"}
CLE_TEXTE, CLE_ECHEANCE = services.CLES_ANNONCE_HISTORIQUES


@pytest.fixture
def chemins(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    db.init_db()
    return tmp_path


@pytest.fixture
def client(chemins, monkeypatch):
    from fastapi.testclient import TestClient

    from app import admin_auth
    from app.main import app

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    c = TestClient(app)
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return c


def _conn():
    from app import db
    return db.get_connection()


def _parametre(cle):
    conn = _conn()
    try:
        ligne = conn.execute(
            "SELECT valeur FROM parametres WHERE cle = ?", (cle,)).fetchone()
    finally:
        conn.close()
    return ligne[0] if ligne else None


def _ecrire(**valeurs):
    """Écrit directement dans `parametres` (annonce d'avant la 1.32…)."""
    conn = _conn()
    try:
        for cle, valeur in valeurs.items():
            services.ecrire_parametre(conn, cle, valeur)
    finally:
        conn.close()


def _lignes():
    conn = _conn()
    try:
        return [dict(l) for l in conn.execute(
            "SELECT id, texte, posee_le, expire_le FROM annonces_ecran ORDER BY id")]
    finally:
        conn.close()


def _poser_service(texte, minutes=30):
    conn = _conn()
    try:
        return services.poser_annonce(conn, texte, minutes)
    finally:
        conn.close()


def _inserer_expiree(texte):
    """
    Une annonce déjà expirée, écrite directement. Jamais par un UPDATE de
    l'échéance : l'application ne met jamais une annonce à jour, et un UPDATE
    sans `secure_delete` laisserait dans la page une ancienne copie de la
    ligne que l'application, elle, ne produit pas.
    """
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO annonces_ecran (texte, posee_le, expire_le) VALUES (?, ?, ?)",
            (texte, _passee(60), _passee()))
        conn.commit()
    finally:
        conn.close()


def _poser(client, texte, duree):
    return client.post("/admin/ecran-salle/annonce",
                       data={"annonce": texte, "annonce_duree": duree})


def _minutes_jusqua_echeance():
    echeance = datetime.fromisoformat(_lignes()[-1]["expire_le"])
    return round((echeance - datetime.now(timezone.utc)).total_seconds() / 60)


def _passee(minutes=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(
        timespec="seconds")


def _future(minutes=30):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds")


def _octets_de_la_base(chemins) -> bytes:
    """
    Les OCTETS du fichier de la base de prêt, journal WAL compris s'il existe
    encore : une suppression promise se vérifie là, pas par un SELECT (une
    ligne supprimée reste lisible dans les pages libres).
    """
    base = chemins / "test.db"
    octets = base.read_bytes()
    wal = chemins / "test.db-wal"
    if wal.exists():
        octets += wal.read_bytes()
    return octets


def _base_de_pret_archivee(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return zf.read("pret-jeux.db")


def _textes_en_salle(client):
    return [a["texte"] for a in client.get("/live/data").json().get("annonces", [])]


# ---------------------------------------------------------------------------
# 1. Durée obligatoire, bornée, jamais bloquante — propre à chaque annonce
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("saisie, attendue, phrase", [
    ("", 30, "Aucune durée indiquée : affichage limité à 30 minutes."),
    ("0", 30, "Une annonce a toujours une durée : affichage limité à 30 minutes."),
    ("-5", 30, "Une annonce a toujours une durée : affichage limité à 30 minutes."),
    ("une heure", 30, "Durée « une heure » illisible : affichage limité à 30 minutes."),
    ("5000", 720, "Durée ramenée à 12 heures, le maximum pour une annonce."),
])
def test_duree_corrigee_jamais_refusee_jamais_illimitee(client, saisie, attendue, phrase):
    r = _poser(client, "Tombola à 15 h, stand accueil", saisie)
    assert r.status_code == 200
    # Publiée quand même (geste d'urgence) …
    assert _textes_en_salle(client) == ["Tombola à 15 h, stand accueil"]
    # … avec une échéance, jamais « sans limite » …
    assert _lignes()[-1]["expire_le"]
    assert abs(_minutes_jusqua_echeance() - attendue) <= 1
    assert "sans limite" not in r.text
    # … et le message le dit, en « attention » pour qu'on le voie.
    assert phrase in r.text
    assert "resultat-attention" in r.text


def test_duree_valide_prise_telle_quelle(client):
    r = _poser(client, "Tombola à 15 h", "90")
    assert abs(_minutes_jusqua_echeance() - 90) <= 1
    assert "Annonce ajoutée, affichée en salle jusqu&#39;à" in r.text
    assert "resultat-ok" in r.text
    assert "limité à" not in r.text


def test_duree_maximum_acceptee_sans_correction(client):
    r = _poser(client, "Tombola à 15 h", str(services.DUREE_ANNONCE_MAX_MIN))
    assert abs(_minutes_jusqua_echeance() - services.DUREE_ANNONCE_MAX_MIN) <= 1
    assert "ramenée" not in r.text


def test_chaque_annonce_garde_sa_propre_duree(client):
    _poser(client, "Tombola à 15 h", "15")
    _poser(client, "Vestiaire fermé à 18 h", "240")
    debut, fin = (datetime.fromisoformat(l["expire_le"]) for l in _lignes())
    assert 220 <= (fin - debut).total_seconds() / 60 <= 230


def test_formulaire_prerempli_court_et_consigne_sous_le_champ(client):
    page = client.get("/admin/ecran-salle").text
    assert 'value="30"' in page
    assert 'max="720"' in page
    assert "placeholder=\"illimitée\"" not in page
    assert ">illimitée<" not in page
    # Patron de la consigne du signalement, avec ce qu'il faut écrire à la place.
    assert "Décrivez une situation, jamais" in page
    assert "«&nbsp;Un enfant attend ses parents à\n        l'accueil&nbsp;», sans son nom." in page
    assert 'aria-describedby="annonce-consigne"' in page


def test_duree_annonce_regles():
    assert services.duree_annonce("45") == (45, None)
    assert services.duree_annonce(" 45 ")[0] == 45
    assert services.duree_annonce(None)[0] == services.DUREE_ANNONCE_DEFAUT_MIN
    assert services.duree_annonce("721")[0] == services.DUREE_ANNONCE_MAX_MIN


# ---------------------------------------------------------------------------
# 2. Ajouter ne remplace plus ; un texte vide ne fait rien
# ---------------------------------------------------------------------------
def test_ajouter_s_ajoute_aux_annonces_deja_affichees(client):
    _poser(client, "Tombola à 15 h", "30")
    _poser(client, "Vestiaire fermé à 18 h", "30")
    assert _textes_en_salle(client) == ["Tombola à 15 h", "Vestiaire fermé à 18 h"]


def test_texte_vide_envoi_sans_effet_jamais_une_erreur(client, _journal_isole):
    _poser(client, "Tombola à 15 h", "30")
    r = _poser(client, "   ", "30")
    assert r.status_code == 200
    assert str(escape(live.MESSAGE_TEXTE_VIDE)) in r.text
    assert "resultat-attention" in r.text
    assert _textes_en_salle(client) == ["Tombola à 15 h"]
    assert "annonce_effacee" not in _journal_isole.read_text(encoding="utf-8")


def test_texte_borne_a_200_caracteres(client):
    _poser(client, "x" * 300, "30")
    assert len(_textes_en_salle(client)[0]) == services.LONGUEUR_ANNONCE


# ---------------------------------------------------------------------------
# 3. Effacer par identifiant : un tap, l'annonce nommée, jamais une erreur
# ---------------------------------------------------------------------------
def test_effacer_une_annonce_la_nomme_et_laisse_les_autres(client):
    _poser(client, "Tombola à 15 h", "30")
    _poser(client, "Vestiaire fermé à 18 h", "30")
    tombola = _lignes()[0]["id"]
    r = client.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": str(tombola)})
    assert r.status_code == 200
    assert "Annonce retirée de l&#39;écran : « Tombola à 15 h »." in r.text
    assert _textes_en_salle(client) == ["Vestiaire fermé à 18 h"]


@pytest.mark.parametrize("valeur", ["999", "abc", "", "-1", "1 OR 1=1", "1.5"])
def test_identifiant_inconnu_ou_forge_message_neutre_rien_d_ecrit(client, valeur,
                                                                 _journal_isole):
    _poser(client, "Tombola à 15 h", "30")
    avant = _journal_isole.read_text(encoding="utf-8")
    r = client.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": valeur})
    assert r.status_code == 200
    assert str(escape(live.MESSAGE_PLUS_AFFICHEE)) in r.text
    assert _textes_en_salle(client) == ["Tombola à 15 h"]
    assert _journal_isole.read_text(encoding="utf-8") == avant


def test_effacer_une_annonce_deja_expiree_l_efface_en_silence(client, _journal_isole):
    _inserer_expiree("Tombola à 15 h")
    (ligne,) = _lignes()
    r = client.post("/admin/ecran-salle/annonce/effacer",
                    data={"annonce_id": str(ligne["id"])})
    assert str(escape(live.MESSAGE_PLUS_AFFICHEE)) in r.text
    assert _lignes() == []
    assert "annonce_effacee" not in _journal_isole.read_text(encoding="utf-8")


def test_le_retrait_exige_le_mot_de_passe(chemins):
    from fastapi.testclient import TestClient

    from app.main import app

    _poser_service("Tombola à 15 h")
    (ligne,) = _lignes()
    r = TestClient(app).post("/admin/ecran-salle/annonce/effacer",
                             data={"annonce_id": str(ligne["id"])},
                             follow_redirects=False)
    assert r.status_code == 303
    assert len(_lignes()) == 1


# ---------------------------------------------------------------------------
# 4. AUTOINCREMENT : un identifiant n'est jamais réutilisé
# ---------------------------------------------------------------------------
def test_aucun_identifiant_reutilise_apres_effacement_du_plus_grand(client):
    """
    Sans AUTOINCREMENT, SQLite réattribue le plus grand identifiant supprimé :
    le bouton « Effacer » d'une page restée ouverte retirerait alors une AUTRE
    annonce, posée entre-temps sous le même numéro.
    """
    _poser(client, "Tombola à 15 h", "30")
    _poser(client, "Vestiaire fermé à 18 h", "30")
    perime = _lignes()[-1]["id"]                     # le plus grand
    client.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": str(perime)})
    _poser(client, "Tournoi déplacé en salle B", "30")
    nouvel = _lignes()[-1]["id"]
    assert nouvel > perime

    # Le formulaire périmé (onglet resté ouvert) renvoie l'ancien numéro.
    r = client.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": str(perime)})
    assert str(escape(live.MESSAGE_PLUS_AFFICHEE)) in r.text
    assert "Tournoi déplacé en salle B" in _textes_en_salle(client)


def test_schema_en_autoincrement(chemins):
    conn = _conn()
    try:
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'annonces_ecran'").fetchone()
    finally:
        conn.close()
    assert "AUTOINCREMENT" in sql.upper()


# ---------------------------------------------------------------------------
# 5. Le garde-fou des 50 annonces actives
# ---------------------------------------------------------------------------
def _remplir(nb):
    conn = _conn()
    try:
        for i in range(nb):
            assert services.poser_annonce(conn, f"Annonce numéro {i}", 30) is not None
    finally:
        conn.close()


def test_plafond_50_acceptees_la_suivante_refusee_saisie_conservee(client, _journal_isole):
    _remplir(services.ANNONCES_MAX_ACTIVES - 1)
    r = _poser(client, "La cinquantième", "30")
    assert "Annonce ajoutée" in r.text
    assert len(_lignes()) == services.ANNONCES_MAX_ACTIVES
    poses = _journal_isole.read_text(encoding="utf-8").count('"annonce_posee"')

    r = _poser(client, "Tournoi déplacé en salle B", "45")
    assert r.status_code == 200
    # Rien d'écrit, rien au journal …
    assert len(_lignes()) == services.ANNONCES_MAX_ACTIVES
    assert _journal_isole.read_text(encoding="utf-8").count('"annonce_posee"') == poses
    # … un message clair, chiffre lu dans la constante …
    assert str(escape(live.MESSAGE_PLAFOND)) in r.text
    assert f"déjà {services.ANNONCES_MAX_ACTIVES} annonces" in live.MESSAGE_PLAFOND
    assert "resultat-attention" in r.text
    # … et la saisie n'est pas perdue : texte et durée réaffichés.
    assert ">Tournoi déplacé en salle B</textarea>" in r.text
    assert 'value="45"' in r.text


def test_les_annonces_expirees_ne_comptent_pas_et_partent_a_la_pose(client):
    _remplir(services.ANNONCES_MAX_ACTIVES - 1)
    for i in range(5):
        _inserer_expiree(f"Expirée {i}")
    r = _poser(client, "La cinquantième", "30")
    assert "Annonce ajoutée" in r.text
    textes = [l["texte"] for l in _lignes()]
    assert len(textes) == services.ANNONCES_MAX_ACTIVES
    assert not any(t.startswith("Expirée") for t in textes)


def test_plafond_tenu_sous_poses_simultanees(chemins):
    """
    Le compte et l'insertion dans la MÊME transaction (`BEGIN IMMEDIATE`) :
    huit poses lancées au même instant sur une base à 49 annonces, une seule
    passe. Sans la transaction, toutes liraient 49 et toutes écriraient.
    Patron de tests/test_concurrence_pochettes.py (base sur fichier, une
    connexion par fil, barrière de départ).
    """
    from app import db

    _remplir(services.ANNONCES_MAX_ACTIVES - 1)
    nb_fils = 8
    depart = threading.Barrier(nb_fils)
    resultats = [None] * nb_fils

    def poser(i):
        conn = db.get_connection()
        try:
            depart.wait()
            resultats[i] = services.poser_annonce(conn, f"Simultanée {i}", 30)
        except Exception as erreur:   # remontée au test, jamais avalée
            resultats[i] = erreur
        finally:
            conn.close()

    fils = [threading.Thread(target=poser, args=(i,)) for i in range(nb_fils)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=30)
    assert not [r for r in resultats if isinstance(r, Exception)]
    assert sum(r is not None for r in resultats) == 1
    assert len(_lignes()) == services.ANNONCES_MAX_ACTIVES


# ---------------------------------------------------------------------------
# 6. /live/data : une liste, dans l'ordre de pose, absente si vide
# ---------------------------------------------------------------------------
def test_live_data_liste_dans_l_ordre_de_pose(client):
    assert "annonces" not in client.get("/live/data").json()
    assert "annonce" not in client.get("/live/data").json()
    _poser(client, "Tombola à 15 h", "240")
    _poser(client, "Vestiaire fermé à 18 h", "15")
    d = client.get("/live/data").json()
    ids = [l["id"] for l in _lignes()]
    assert d["annonces"] == [{"id": ids[0], "texte": "Tombola à 15 h"},
                             {"id": ids[1], "texte": "Vestiaire fermé à 18 h"}]
    # Compatibilité d'une version : la plus récente, pour un poste de
    # projection chargé avant la mise à jour.
    assert d["annonce"] == "Vestiaire fermé à 18 h"


def test_live_data_ne_publie_ni_heure_de_pose_ni_echeance(client):
    _poser(client, "Tombola à 15 h", "30")
    (annonce,) = client.get("/live/data").json()["annonces"]
    assert set(annonce) == {"id", "texte"}


def test_echeance_illisible_masque_plutot_qu_afficher(client):
    conn = _conn()
    try:
        conn.execute("INSERT INTO annonces_ecran (texte, posee_le, expire_le) "
                     "VALUES ('Tombola à 15 h', ?, 'pas une date')", (_passee(),))
        conn.commit()
    finally:
        conn.close()
    assert "annonces" not in client.get("/live/data").json()
    assert client.get("/live").status_code == 200


def test_live_data_n_expose_plus_un_texte_expire(client):
    _inserer_expiree("Tombola à 15 h")
    assert "annonces" not in client.get("/live/data").json()
    assert "Tombola" not in client.get("/live").text


def test_les_lectures_n_ecrivent_jamais(client):
    """
    /live, /live/data, le tableau de bord, la supervision et la page de
    saisie lisent les annonces : un texte expiré reste en base après eux. Ce
    n'est pas à un chemin de lecture de l'effacer.
    """
    _inserer_expiree("Tombola à 15 h")
    for url in ("/live", "/live/data", "/admin", "/admin/supervision",
                "/admin/ecran-salle"):
        client.get(url)
    assert [l["texte"] for l in _lignes()] == ["Tombola à 15 h"]


# ---------------------------------------------------------------------------
# 7. Le défilement : une constante, un seul domicile
# ---------------------------------------------------------------------------
def test_la_page_porte_la_duree_de_defilement_de_la_constante(client):
    page = client.get("/live").text
    assert f"const DUREE_DEFILEMENT_MS = {live.DUREE_DEFILEMENT_S} * 1000;" in page
    assert "setInterval(avancer, DUREE_DEFILEMENT_MS)" in page


@pytest.mark.parametrize("nb, attendu", [
    (1, "8 s"), (2, "16 s"), (5, "40 s"), (10, "1 min 20"), (15, "2 min"),
])
def test_duree_tour_formatee(monkeypatch, nb, attendu):
    monkeypatch.setattr(live, "DUREE_DEFILEMENT_S", 8)
    assert live.duree_tour(nb) == attendu


def test_tour_complet_affiche_a_partir_de_deux_annonces(client):
    _poser(client, "Tombola à 15 h", "30")
    assert "Tour complet de l" not in client.get("/admin/ecran-salle").text
    _poser(client, "Vestiaire fermé à 18 h", "30")
    page = client.get("/admin/ecran-salle").text
    assert f"Tour complet de l'écran : {live.duree_tour(2)}." in page
    assert live.duree_tour(2) == f"{2 * live.DUREE_DEFILEMENT_S} s"


def test_le_bandeau_ne_porte_pas_d_aria_live(client):
    page = client.get("/live").text
    bandeau = page[page.index('<section class="bandeau-annonce"'):]
    bandeau = bandeau[:bandeau.index("</section>")]
    assert "aria-live" not in bandeau
    pile = page[page.index("function synchroniserPile"):
                page.index("function montrerCourante")]
    assert ".innerHTML" not in pile and "el.textContent = a.texte" in pile


def test_avec_une_seule_annonce_aucun_repere(client):
    """
    Le repère « 2/4 » est masqué par défaut et n'apparaît qu'à partir de deux
    annonces : avec une seule, l'écran reste celui d'avant (comparaison des
    rendus faite au navigateur, voir le compte rendu du lot).
    """
    page = client.get("/live").text
    regle = page[page.index(".bandeau-annonce .repere-annonces {"):]
    assert "display: none;" in regle[:regle.index("}")]
    assert "if (annonces.length >= 2)" in page


# ---------------------------------------------------------------------------
# 8. Reprise de l'annonce unique d'avant la 1.32 (clés de `parametres`)
# ---------------------------------------------------------------------------
def test_migration_reprend_une_annonce_affichable_avec_la_meme_echeance(chemins):
    from app import db

    echeance = _future(40)
    _ecrire(**{CLE_TEXTE: "Tombola à 15 h", CLE_ECHEANCE: echeance})
    db.init_db()
    (ligne,) = _lignes()
    assert ligne["texte"] == "Tombola à 15 h"
    assert ligne["expire_le"] == echeance
    assert ligne["posee_le"]
    assert _parametre(CLE_TEXTE) is None and _parametre(CLE_ECHEANCE) is None


def test_migration_idempotente(chemins):
    from app import db

    _ecrire(**{CLE_TEXTE: "Tombola à 15 h", CLE_ECHEANCE: _future(40)})
    db.init_db()
    db.init_db()
    assert len(_lignes()) == 1


@pytest.mark.parametrize("echeance", [None, "pas une date", "passee"])
def test_migration_ne_reprend_pas_une_annonce_expiree_ou_sans_echeance(chemins, echeance):
    from app import db

    marque = "AnnonceAvantMigrationExpiree"
    valeurs = {CLE_TEXTE: marque}
    if echeance is not None:
        valeurs[CLE_ECHEANCE] = _passee() if echeance == "passee" else echeance
    _ecrire(**valeurs)
    db.init_db()
    assert _lignes() == []
    assert _parametre(CLE_TEXTE) is None and _parametre(CLE_ECHEANCE) is None
    assert marque.encode() not in _octets_de_la_base(chemins)


def test_migration_d_une_echeance_seule(chemins):
    from app import db

    _ecrire(**{CLE_ECHEANCE: _future()})
    db.init_db()
    assert _lignes() == [] and _parametre(CLE_ECHEANCE) is None


def test_migration_ne_touche_pas_l_interrupteur_des_benevoles(chemins):
    from app import db

    _ecrire(**{CLE_TEXTE: "Tombola", CLE_ECHEANCE: _future(),
               services.CLE_ANNONCE_BENEVOLES: "1"})
    db.init_db()
    assert _parametre(services.CLE_ANNONCE_BENEVOLES) == "1"


def test_base_d_avant_l_annonce_en_cours_reste_affichee(client, chemins):
    """« Une base d'avant doit se comporter comme avant » : même texte à l'écran."""
    from app import db

    _ecrire(**{CLE_TEXTE: "Tombola à 15 h", CLE_ECHEANCE: _future(40)})
    db.init_db()
    assert _textes_en_salle(client) == ["Tombola à 15 h"]
    assert client.get("/live/data").json()["annonce"] == "Tombola à 15 h"


# ---------------------------------------------------------------------------
# 9. Les CINQ chemins d'effacement — vérifiés sur les OCTETS
# ---------------------------------------------------------------------------
def test_chemin_demarrage_efface_les_expirees_sur_les_octets(chemins):
    """Le chemin de la mise à jour : `update.sh` redémarre le service."""
    from app import db

    marque = "TemoinDemarrageQ7ZK"
    _poser_service("Tombola à 15 h")
    _inserer_expiree(marque)   # après la pose, qui purge les expirées
    assert marque.encode() in _octets_de_la_base(chemins)   # le témoin témoigne
    db.init_db()
    assert [l["texte"] for l in _lignes()] == ["Tombola à 15 h"]
    assert marque.encode() not in _octets_de_la_base(chemins)


def test_chemin_sauvegarde_efface_les_expirees_de_la_base_vivante(chemins):
    """La borne dans le temps : 3 h et 15 h, sans geste humain."""
    from app import sauvegarde

    marque = "TemoinSauvegardeW3XP"
    _poser_service("Tombola à 15 h")
    _inserer_expiree(marque)   # après la pose, qui purge les expirées
    sauvegarde.ecrire_archive(sauvegarde.NATURE_ROUTINE, chemins / "sauvegardes")
    assert [l["texte"] for l in _lignes()] == ["Tombola à 15 h"]
    assert marque.encode() not in _octets_de_la_base(chemins)


def test_la_sauvegarde_ne_fait_jamais_echouer_sur_une_base_verrouillee(chemins,
                                                                        monkeypatch):
    from app import db, sauvegarde

    _inserer_expiree("Tombola à 15 h")
    original = db.get_connection

    def connexion_impatiente():
        # Délai d'attente court pour ce test : la purge abandonne vite.
        conn = original()
        conn.execute("PRAGMA busy_timeout = 50")
        return conn

    verrou = original()
    verrou.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(sauvegarde.pret_db, "get_connection", connexion_impatiente)
    try:
        chemin = sauvegarde.ecrire_archive(sauvegarde.NATURE_ROUTINE,
                                           chemins / "sauvegardes")
    finally:
        verrou.rollback()
        verrou.close()
    assert chemin.exists()
    assert len(_lignes()) == 1      # le ménage attendra le passage suivant


def test_chemin_archives_aucune_annonce_sur_les_octets(chemins):
    """
    Une archive de routine est gardée un mois : aucune annonce n'y entre,
    même affichée. Recherche OCTET PAR OCTET dans le fichier de base
    archivé — une ligne supprimée reste lisible dans les pages libres tant
    qu'elles ne sont pas réécrites, d'où le `VACUUM` de la copie.
    """
    from app import sauvegarde

    marques = ["TemoinArchiveUnM4RT", "TemoinArchiveDeuxB8NV"]
    for marque in marques:
        _poser_service(marque)
    _ecrire(evenement_nom="Fête du jeu")

    base = _base_de_pret_archivee(sauvegarde.creer_zip_sauvegarde())
    for marque in marques:
        assert marque.encode() not in base
    # La base vivante, elle, n'est pas touchée par l'archivage.
    assert [l["texte"] for l in _lignes()] == marques
    # Et le reste des réglages voyage bien dans l'archive.
    assert "Fête du jeu".encode() in base


def test_chemin_archives_retire_aussi_les_cles_historiques(chemins):
    """
    Une base restaurée depuis une vieille archive peut porter les deux clés
    de l'annonce unique tant que le démarrage ne les a pas reprises : elles
    ne repartent dans aucune archive.
    """
    from app import sauvegarde

    marque = "TemoinCleHistoriqueH2JD"
    _ecrire(**{CLE_TEXTE: marque, CLE_ECHEANCE: _future()})
    base = _base_de_pret_archivee(sauvegarde.creer_zip_sauvegarde())
    assert marque.encode() not in base
    assert _parametre(CLE_TEXTE) == marque    # base vivante intacte


def test_chemin_archives_ne_recopie_pas_une_annonce_deja_effacee(chemins):
    """Les pages libres de la base vivante ne passent pas dans l'archive."""
    from app import sauvegarde

    marque = "TemoinArchiveEffaceeK5QZ" * 4
    conn = _conn()
    try:
        # secure_delete désactivé ici exprès : on veut un résidu en pages
        # libres, pour prouver que l'archive ne le recopie pas.
        conn.execute("PRAGMA secure_delete = OFF")
        conn.execute("INSERT INTO annonces_ecran (texte, posee_le, expire_le) "
                     "VALUES (?, ?, ?)", (marque, _passee(), _future()))
        conn.commit()
        conn.execute("DELETE FROM annonces_ecran")
        conn.commit()
    finally:
        conn.close()

    base = _base_de_pret_archivee(sauvegarde.creer_zip_sauvegarde())
    assert marque.encode() not in base
    # L'archive reste restaurable.
    chemin = chemins / "archive.zip"
    chemin.write_bytes(sauvegarde.creer_zip_sauvegarde())
    sauvegarde.valider_zip_sauvegarde(chemin)


def test_chemin_cloture_efface_tout_sur_les_octets_et_laisse_les_panneaux(client, chemins):
    client.post("/admin/ecran-salle", data={"panneau_chiffres": "1",
                                            "panneau_mouvements": "1"})
    marques = ["TemoinClotureUnF6HS", "TemoinClotureDeuxP1LC"]
    for marque in marques:
        _poser(client, marque, "60")
    _inserer_expiree("TemoinClotureExpireeY9TG")
    avant = client.get("/live/data").json()["panneaux"]

    r = client.post("/admin/cloturer-prets")
    assert "les annonces de l&#39;écran de salle sont effacées" in r.text
    assert _lignes() == []
    d = client.get("/live/data").json()
    assert "annonces" not in d
    assert d["panneaux"] == avant == {"chiffres": True, "tournois": False,
                                      "programme": False, "mouvements": True}
    octets = _octets_de_la_base(chemins)
    for marque in marques + ["TemoinClotureExpireeY9TG"]:
        assert marque.encode() not in octets


def test_la_cloture_efface_dans_la_meme_transaction(chemins):
    conn = _conn()
    try:
        services.poser_annonce(conn, "Tombola à 15 h", 30)
        services.cloturer_tous_les_prets(conn)
        assert not conn.in_transaction
    finally:
        conn.close()
    assert _lignes() == []


def test_chemin_formation_efface_tout_sur_les_octets(client, chemins, monkeypatch):
    from app.routes import admin as admin_routes

    monkeypatch.setattr(admin_routes, "MODE_FORMATION", True)
    monkeypatch.setenv("FORMATION_SOURCE_DB", "/formation-source-inexistante.db")
    # Un mot de passe admin changé depuis l'interface : il vit dans
    # `parametres`, et doit survivre à la remise à zéro.
    client.post("/admin/motdepasse",
                data={"ancien": MOT_DE_PASSE, "nouveau": "nouveau-secret-456",
                      "confirmation": "nouveau-secret-456"})
    empreinte = _parametre("admin_hash")
    assert empreinte
    _ecrire(evenement_nom="Fête du jeu")
    marques = ["TemoinFormationUnZ2WE", "TemoinFormationDeuxR7MA"]
    for marque in marques:
        _poser(client, marque, "60")

    r = client.post("/admin/formation/reinitialiser")
    assert "réinitialisées" in r.text
    assert _lignes() == []
    assert _parametre("admin_hash") == empreinte
    assert _parametre("evenement_nom") == "Fête du jeu"
    assert "annonces" not in client.get("/live/data").json()
    octets = _octets_de_la_base(chemins)
    for marque in marques:
        assert marque.encode() not in octets


def test_effacer_par_le_bouton_efface_sur_les_octets(client, chemins):
    marque = "TemoinBoutonEffacerN4VB"
    _poser(client, marque, "30")
    (ligne,) = _lignes()
    client.post("/admin/ecran-salle/annonce/effacer", data={"annonce_id": str(ligne["id"])})
    assert marque.encode() not in _octets_de_la_base(chemins)


def test_inventaire_des_suppressions_sous_secure_delete():
    """
    Garde-fou de classe : toute requête de `app/` qui SUPPRIME dans
    `annonces_ecran` vit dans une fonction qui pose `secure_delete` — sauf
    la copie archivée, que le `VACUUM` reconstruit.
    """
    import ast
    from pathlib import Path

    racine = Path(__file__).resolve().parent.parent / "app"
    vues = []
    for fichier in racine.rglob("*.py"):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for fonction in ast.walk(arbre):
            if not isinstance(fonction, ast.FunctionDef):
                continue
            chaines = [n.value for n in ast.walk(fonction)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if not any("DELETE FROM annonces_ecran" in c for c in chaines):
                continue
            nom = f"{fichier.name}:{fonction.name}"
            vues.append(nom)
            if nom in ("sauvegarde.py:_retirer_annonce", "services.py:_effacer_expirees"):
                continue
            assert any("secure_delete = ON" in c for c in chaines), nom
    assert {"services.py:effacer_annonce", "services.py:effacer_toutes_les_annonces",
            "services.py:_effacer_expirees", "sauvegarde.py:_retirer_annonce"} <= set(vues)
    # `_effacer_expirees` ne committe pas : ses deux appelants posent le pragma.
    import inspect
    for appelant in (services.poser_annonce, services.purger_annonces_expirees):
        source = inspect.getsource(appelant)
        assert "secure_delete = ON" in source and "_effacer_expirees" in source
