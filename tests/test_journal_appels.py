"""
Journal d'activité — POINTS D'APPEL de priorité 1 (lot C, étape 4 de
docs/conception-journal.md § 2.1).

Ce fichier vérifie, pour chaque action d'administration et de configuration
du tableau § 2.1, qu'elle produit UNE ligne, avec le bon `module`, la bonne
`action` et le bon `qui`. Il ne teste pas le socle d'écriture (voir
`tests/test_journal.py`) ni les interdits (voir
`tests/test_journal_interdits.py`, qui est le garde-fou du chantier).

Deux propriétés structurantes s'y vérifient au passage :

- **appelé depuis les routes, jamais depuis les services** (§5.2) : le même
  import CSV lancé par `scripts.import_csv.importer()` n'écrit rien ;
- **l'ordre de détermination de `qui`** (§5.3) : les actions d'administration
  portent `qui: "admin"`, jamais `"benevole"` — `auth.peut_ecrire` renvoie
  vrai pour un administrateur aussi, et tester le jeton d'abord étiquetterait
  toute l'administration comme bénévole.
"""

import json
import zipfile

import pytest


# ---------------------------------------------------------------------------
# Fixtures — trois bases temporaires, patron de tests/test_appareils.py.
# La fixture autouse `_journal_isole` (tests/conftest.py) redirige déjà
# JOURNAL_PATH vers un fichier propre à chaque test.
# ---------------------------------------------------------------------------
MOT_DE_PASSE = "secret-admin-appels"


@pytest.fixture
def bases(tmp_path, monkeypatch):
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
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def client(bases, monkeypatch):
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def conn(bases):
    from app import db

    c = db.get_connection()
    yield c
    c.close()


def _connexion(client):
    return client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _lignes(chemin):
    """Toutes les lignes du journal, désérialisées, dans l'ordre d'écriture."""
    if not chemin.exists():
        return []
    return [
        json.loads(l)
        for l in chemin.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


def _actions(chemin, action):
    return [l for l in _lignes(chemin) if l.get("action") == action]


def _derniere(chemin, action):
    """La dernière ligne portant cette action (assertion si aucune)."""
    trouvees = _actions(chemin, action)
    assert trouvees, f"aucune ligne « {action} » dans le journal"
    return trouvees[-1]


# ===========================================================================
# Connexion administrateur — réussie ET échouée
# ===========================================================================
def test_connexion_reussie_journalisee_comme_admin(client, _journal_isole):
    """
    La ligne dit `admin` et porte l'appareil, bien que ni la session ni le
    cookie d'appareil n'existent sur la requête ENTRANTE (ils naissent dans la
    réponse) : c'est le seul appel autorisé à passer `qui`/`appareil`
    explicitement, voir la docstring de journal.journaliser.
    """
    _connexion(client)
    ligne = _derniere(_journal_isole, "connexion_reussie")
    assert ligne["module"] == "admin"
    assert ligne["qui"] == "admin"
    assert ligne["ok"] is True
    assert len(ligne["appareil"]) == 6


def test_connexion_echouee_journalisee_avec_ok_false(client, _journal_isole):
    """
    Seul signal aujourd'hui d'une tentative d'intrusion : le compteur de
    limitation de débit vit en mémoire et repart à zéro à chaque redémarrage.
    """
    client.post("/admin/login", data={"mot_de_passe": "pas-le-bon"})
    ligne = _derniere(_journal_isole, "connexion_echouee")
    assert ligne["module"] == "admin"
    assert ligne["ok"] is False
    assert ligne["detail"] == "mot_de_passe"
    assert not _actions(_journal_isole, "connexion_reussie")


def test_connexion_bloquee_par_la_limite_de_debit_journalisee(client, _journal_isole, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    client.post("/admin/login", data={"mot_de_passe": "pas-le-bon"})
    client.post("/admin/login", data={"mot_de_passe": "pas-le-bon"})
    motifs = [l["detail"] for l in _actions(_journal_isole, "connexion_echouee")]
    assert "trop_de_tentatives" in motifs


# ===========================================================================
# Jeton bénévole, mot de passe
# ===========================================================================
def test_jeton_reinitialise_journalise_sans_le_jeton(client, _journal_isole):
    _connexion(client)
    client.post("/admin/jeton/reinitialiser", data={"expire": ""})

    ligne = _derniere(_journal_isole, "jeton_reinitialise")
    assert ligne["module"] == "admin"
    assert ligne["qui"] == "admin"
    # L'échéance, jamais le jeton (§8) — vérifié en dur ici, et globalement
    # par tests/test_journal_interdits.py.
    assert ligne["objet"].startswith("valable jusqu'au ")


def test_motdepasse_change_journalise_reussite_et_echec(client, _journal_isole):
    _connexion(client)

    client.post("/admin/motdepasse", data={
        "ancien": "pas-le-bon", "nouveau": "nouveau-secret", "confirmation": "nouveau-secret",
    })
    echec = _derniere(_journal_isole, "motdepasse_change")
    assert echec["ok"] is False and echec["detail"] == "ancien_incorrect"

    client.post("/admin/motdepasse", data={
        "ancien": MOT_DE_PASSE, "nouveau": "nouveau-secret", "confirmation": "nouveau-secret",
    })
    succes = _derniere(_journal_isole, "motdepasse_change")
    assert succes["ok"] is True and succes["module"] == "admin"
    # Ni l'ancien ni le nouveau mot de passe ne figurent dans la ligne.
    assert "nouveau-secret" not in json.dumps(succes)
    assert MOT_DE_PASSE not in json.dumps(succes)


# ===========================================================================
# Import CSV — et la règle « depuis les routes, jamais depuis les services »
# ===========================================================================
CSV_MINIMAL = "Code jeu;Nom jeu\n900;Jeu de test\n"


def test_import_csv_journalise_depuis_la_route(client, _journal_isole):
    _connexion(client)
    client.post(
        "/admin/donnees/import",
        files={"fichier": ("catalogue-2026.csv", CSV_MINIMAL.encode("utf-8"), "text/csv")},
    )
    ligne = _derniere(_journal_isole, "import_csv")
    assert ligne["module"] == "admin"
    assert ligne["qui"] == "admin"
    assert ligne["ok"] is True
    assert ligne["objet"] == "catalogue-2026.csv"


def test_import_csv_en_ligne_de_commande_n_ecrit_rien(bases, tmp_path, _journal_isole):
    """
    §5.2 : les services sont appelés par les tests, les scripts d'import et le
    script de formation, qui n'ont AUCUNE raison de polluer le journal — et
    n'ont d'ailleurs pas de requête à interroger.
    """
    from scripts import import_csv

    chemin = tmp_path / "catalogue.csv"
    chemin.write_text(CSV_MINIMAL, encoding="utf-8")
    import_csv.importer(chemin)

    assert _lignes(_journal_isole) == []


def test_import_csv_fichier_vide_journalise_l_echec(client, _journal_isole):
    _connexion(client)
    client.post("/admin/donnees/import", files={"fichier": ("vide.csv", b"", "text/csv")})
    ligne = _derniere(_journal_isole, "import_csv")
    assert ligne["ok"] is False and ligne["detail"] == "fichier_vide"


# ===========================================================================
# Restauration de sauvegarde
# ===========================================================================
def test_sauvegarde_restauree_journalisee_avant_le_remplacement(client, _journal_isole):
    """
    La ligne est écrite AVANT que les fichiers de base ne soient remplacés :
    après le basculement, `journal._qui()` lirait le jeton d'une AUTRE base
    que celle sur laquelle la requête a été authentifiée. Le fichier journal,
    lui, n'est pas dans l'archive — la ligne survit à la restauration.
    """
    from app import sauvegarde

    _connexion(client)
    archive = sauvegarde.creer_zip_sauvegarde()
    reponse = client.post(
        "/admin/sauvegarde/import",
        files={"fichier": ("ludotex-backup.zip", archive, "application/zip")},
    )
    assert reponse.status_code == 200

    ligne = _derniere(_journal_isole, "sauvegarde_restauree")
    assert ligne["module"] == "admin"
    assert ligne["qui"] == "admin"
    assert ligne["ok"] is True
    assert ligne["objet"] == "ludotex-backup.zip"


def test_sauvegarde_archive_refusee_journalisee_en_echec(client, _journal_isole, tmp_path):
    _connexion(client)
    incomplet = tmp_path / "incomplet.zip"
    with zipfile.ZipFile(incomplet, "w") as zf:
        zf.writestr("INFO.txt", "archive incomplète")

    client.post(
        "/admin/sauvegarde/import",
        files={"fichier": ("incomplet.zip", incomplet.read_bytes(), "application/zip")},
    )
    ligne = _derniere(_journal_isole, "sauvegarde_restauree")
    assert ligne["ok"] is False
    assert "manquant" in ligne["detail"]


# ===========================================================================
# Clôture de fin d'événement
# ===========================================================================
def test_cloture_prets_journalise_le_nombre(client, conn, _journal_isole):
    from app import services

    services.preter(conn, "001")
    _connexion(client)
    client.post("/admin/cloturer-prets")

    ligne = _derniere(_journal_isole, "cloture_prets")
    assert ligne["module"] == "pret"
    assert ligne["qui"] == "admin"
    assert ligne["objet"] == "1 prêt ou sortie"


# ===========================================================================
# Modules (fonctionnalités)
# ===========================================================================
def test_module_modifie_une_ligne_par_changement_reel(client, _journal_isole):
    """
    Le formulaire renvoie TOUS les modules à chaque enregistrement : sans
    comparaison à l'état précédent, chaque passage sur la page produirait une
    ligne par module, toutes identiques et toutes fausses.
    """
    from app.modules import ETAT_DEFAUT, MODULES

    _connexion(client)
    champs = {f"module_{nom}": ETAT_DEFAUT for nom in MODULES}

    # 1) Enregistrement sans rien changer -> aucune ligne.
    client.post("/admin/fonctionnalites", data=champs)
    assert _actions(_journal_isole, "module_modifie") == []

    # 2) Un seul module change -> une seule ligne, lisible telle quelle.
    champs["module_tournois"] = "desactive"
    client.post("/admin/fonctionnalites", data=champs)
    lignes = _actions(_journal_isole, "module_modifie")
    assert len(lignes) == 1
    assert lignes[0]["ref"] == "tournois"
    assert lignes[0]["objet"] == "Tournois → Désactivé"
    assert lignes[0]["qui"] == "admin"


# ===========================================================================
# Rangement — contexte, visibilité, affectation en lot
# ===========================================================================
def test_rangement_contexte_et_visibilite_journalises(client, _journal_isole):
    _connexion(client)
    client.post("/admin/rangement/contexte", data={"contexte": "local"})
    client.post("/admin/rangement/visibilite", data={"visibilite": "tous"})

    contexte = _derniere(_journal_isole, "rangement_contexte_modifie")
    assert contexte["module"] == "rangement" and contexte["objet"] == "local"

    visibilite = _derniere(_journal_isole, "rangement_visibilite_modifiee")
    assert visibilite["module"] == "rangement" and visibilite["objet"] == "tous"


def test_rangement_lot_applique_journalise_emplacement_et_nombres(client, _journal_isole):
    _connexion(client)
    client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "coches", "titres_coches": ["CATAN"], "emplacement_texte": "Table 12"},
    )
    ligne = _derniere(_journal_isole, "rangement_lot_applique")
    assert ligne["module"] == "rangement"
    assert ligne["ok"] is True
    assert ligne["objet"] == "Table 12 — 1 jeu, 1 boîte"


def test_rangement_lot_emplacement_vide_journalise_le_refus(client, _journal_isole):
    _connexion(client)
    client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "coches", "titres_coches": ["CATAN"], "emplacement_texte": "  "},
    )
    ligne = _derniere(_journal_isole, "rangement_lot_applique")
    assert ligne["ok"] is False and ligne["detail"] == "emplacement_requis"


# ===========================================================================
# Écran de salle — annonce posée / effacée
# ===========================================================================
def _poster_ecran_salle(client, **champs):
    data = {"titre": "Écran", "annonce": "", "annonce_duree": "",
            "panneau_chiffres": "on", "panneau_tournois": "on",
            "panneau_programme": "on", "panneau_mouvements": "on"}
    data.update(champs)
    return client.post("/admin/ecran-salle", data=data)


def test_annonce_posee_puis_effacee(client, _journal_isole):
    _connexion(client)

    _poster_ecran_salle(client, annonce="Tombola à 15 h")
    posee = _derniere(_journal_isole, "annonce_posee")
    assert posee["module"] == "live" and posee["objet"] == "Tombola à 15 h"

    _poster_ecran_salle(client, annonce="")
    effacee = _derniere(_journal_isole, "annonce_effacee")
    assert effacee["module"] == "live" and effacee["objet"] == "Tombola à 15 h"


def test_titre_seul_sans_annonce_n_ecrit_rien(client, _journal_isole):
    """Sans cette précaution, chaque passage sur la page produirait une ligne
    « annonce effacée » alors qu'il n'y a jamais eu d'annonce."""
    _connexion(client)
    _poster_ecran_salle(client, titre="Nouveau titre", annonce="")
    assert _actions(_journal_isole, "annonce_effacee") == []
    assert _actions(_journal_isole, "annonce_posee") == []


def test_annonce_longue_tronquee_a_120_caracteres(client, _journal_isole):
    """
    L'annonce est la SEULE saisie libre du lot : elle est bornée à 200
    caractères par le formulaire, la troncature du journal (120) joue donc
    réellement ici — une ligne de journal doit rester une ligne.
    """
    _connexion(client)
    _poster_ecran_salle(client, annonce="A" * 200)
    posee = _derniere(_journal_isole, "annonce_posee")
    assert len(posee["objet"]) == 120


# ===========================================================================
# Date de l'événement
# ===========================================================================
def test_evenement_date_journalisee_et_date_invalide_en_echec(client, _journal_isole):
    _connexion(client)

    client.post("/admin/evenement", data={"date_evenement": "2026-08-15"})
    ok = _derniere(_journal_isole, "evenement_date_modifiee")
    assert ok["module"] == "admin" and ok["objet"] == "2026-08-15" and ok["ok"] is True

    client.post("/admin/evenement", data={"date_evenement": "15 août"})
    ko = _derniere(_journal_isole, "evenement_date_modifiee")
    assert ko["ok"] is False and ko["detail"] == "date_invalide"

    client.post("/admin/evenement", data={"date_evenement": ""})
    efface = _derniere(_journal_isole, "evenement_date_modifiee")
    assert efface["objet"] == "effacée" and efface["ok"] is True


# ===========================================================================
# Fiches de jeu
# ===========================================================================
def test_jeu_cree_et_exemplaire_ajoute(client, _journal_isole):
    _connexion(client)

    client.post("/admin/jeu-nouveau", data={"nom": "Dobble", "type_jeu": "Jeu"})
    cree = _derniere(_journal_isole, "jeu_cree")
    assert cree["module"] == "catalogue" and cree["objet"] == "Dobble"
    assert cree["ref"]  # reference_titre

    client.post(f"/admin/jeu/{cree['ref']}/exemplaire")
    ajoute = _derniere(_journal_isole, "exemplaire_ajoute")
    assert ajoute["module"] == "catalogue" and ajoute["objet"] == "Dobble"
    assert ajoute["ref"]  # id_exemplaire de la boîte créée


# ===========================================================================
# Purge RGPD d'une édition du planning
# ===========================================================================
def test_planning_purge_journalise_le_nom_de_l_edition(client, bases, _journal_isole):
    from app.planning import services as planning_services
    from app.planning.db import get_connection as get_planning_connection

    conn = get_planning_connection()
    try:
        ev = planning_services.creer_evenement(conn, "Week-end jeux 2026")
    finally:
        conn.close()

    _connexion(client)
    client.post(f"/planning/admin/{ev}/purger")

    ligne = _derniere(_journal_isole, "planning_purge")
    assert ligne["module"] == "planning"
    assert ligne["qui"] == "admin"
    # Le nom de l'ÉDITION, jamais celui d'un bénévole : cette base est
    # justement celle qui en contient, et la purge qu'on vient d'exécuter
    # serait contournée par une trace ailleurs (§8).
    assert ligne["objet"] == "Week-end jeux 2026"
    assert ligne["ref"] == str(ev)


# ===========================================================================
# Mode formation
# ===========================================================================
def test_formation_reinitialisee_journalisee(client, monkeypatch, _journal_isole):
    from app.routes import admin as routes_admin

    monkeypatch.setattr(routes_admin, "MODE_FORMATION", True)
    _connexion(client)
    client.post("/admin/formation/reinitialiser")

    ligne = _derniere(_journal_isole, "formation_reinitialisee")
    assert ligne["module"] == "admin" and ligne["qui"] == "admin"


def test_peuplement_formation_en_ligne_de_commande_n_ecrit_rien(bases, _journal_isole):
    """Comme l'import CSV : `python -m app.formation` n'a pas de requête et
    n'a aucune raison d'écrire au journal."""
    from app import formation

    formation.peupler()
    assert _lignes(_journal_isole) == []
