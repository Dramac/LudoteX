"""
Journal d'activité — POINTS D'APPEL de priorité 1 (lot C, étape 4 de
docs/conception-journal.md § 2.1) ET de priorités 2/3 (lot D, § 2.2/§2.3).

Ce fichier vérifie, pour chaque action d'administration/configuration (§2.1),
de tournois/programme/planning (§2.2) et de prêt (§2.3), qu'elle produit UNE
ligne, avec le bon `module`, la bonne `action` et le bon `qui`. Il ne teste
pas le socle d'écriture (voir `tests/test_journal.py`) ni les interdits (voir
`tests/test_journal_interdits.py`, qui est le garde-fou du chantier — étendu
au lot D pour couvrir ces nouvelles routes).

Deux propriétés structurantes s'y vérifient au passage :

- **appelé depuis les routes, jamais depuis les services** (§5.2) : le même
  import CSV lancé par `scripts.import_csv.importer()` n'écrit rien, pas plus
  que `app.formation.peupler()` en ligne de commande (lot D) ;
- **l'ordre de détermination de `qui`** (§5.3) : les actions d'administration
  portent `qui: "admin"`, jamais `"benevole"` — `auth.peut_ecrire` renvoie
  vrai pour un administrateur aussi, et tester le jeton d'abord étiquetterait
  toute l'administration comme bénévole. `Depends(exiger_jeton)` accepte donc
  une session admin (voir `_connexion`) pour toutes les routes ci-dessous,
  y compris celles des tournois/programme/planning normalement bénévole.
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
# Écran de salle — alerte « rapportez les exemplaires » avant un tournoi
# ===========================================================================
def _poster_alerte(client, message="", mini="", maxi=""):
    return client.post("/admin/ecran-salle/alerte",
                       data={"alerte_message": message,
                             "alerte_delai_min": mini, "alerte_delai_max": maxi})


def test_alerte_posee_puis_effacee(client, _journal_isole):
    _connexion(client)

    _poster_alerte(client, "Rapportez {jeu} au stand !")
    posee = _derniere(_journal_isole, "alerte_posee")
    assert posee["module"] == "live" and posee["objet"] == "Rapportez {jeu} au stand !"

    _poster_alerte(client, "")
    effacee = _derniere(_journal_isole, "alerte_effacee")
    assert effacee["module"] == "live" and effacee["objet"] == "Rapportez {jeu} au stand !"


def test_delais_seuls_ne_produisent_aucune_ligne_sur_le_message(client, _journal_isole):
    """
    Les trois réglages voyagent dans le même formulaire : sans lecture de la
    valeur précédente, ajuster un délai produirait une ligne « alerte posée »
    identique à la précédente. Le défaut exact déjà rencontré sur cette page
    avec l'annonce et les panneaux.
    """
    _connexion(client)
    _poster_alerte(client, "Rapportez {jeu} !", "15", "90")
    assert len(_actions(_journal_isole, "alerte_posee")) == 1

    _poster_alerte(client, "Rapportez {jeu} !", "20", "120")
    assert len(_actions(_journal_isole, "alerte_posee")) == 1
    assert _actions(_journal_isole, "alerte_effacee") == []


def test_alerte_jamais_effacee_quand_il_n_y_en_avait_pas(client, _journal_isole):
    _connexion(client)
    _poster_alerte(client, "", "15", "90")
    assert _actions(_journal_isole, "alerte_effacee") == []
    assert _actions(_journal_isole, "alerte_posee") == []


def test_alerte_refusee_n_ecrit_rien(client, _journal_isole):
    """Un enregistrement refusé n'écrit rien en base : il n'a donc rien à
    écrire au journal non plus (l'affichage, lui, n'est jamais journalisé —
    c'est un calcul de lecture, décision D11)."""
    _connexion(client)
    _poster_alerte(client, "Rapportez {jouer} !")
    assert _actions(_journal_isole, "alerte_posee") == []


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


# ===========================================================================
# LOT D — Priorité 2 : Tournois (§2.2)
# ===========================================================================
def test_tournoi_cree_modifie_supprime(client, _journal_isole):
    _connexion(client)
    client.post("/tournoi/nouveau", data={"jeu": "Tournoi de test"})
    cree = _derniere(_journal_isole, "tournoi_cree")
    assert cree["module"] == "tournois" and cree["objet"] == "Tournoi de test"
    id_tournoi = cree["ref"]

    client.post(f"/tournoi/{id_tournoi}/editer", data={"jeu": "Tournoi modifié"})
    modifie = _derniere(_journal_isole, "tournoi_modifie")
    assert modifie["objet"] == "Tournoi modifié" and modifie["ref"] == id_tournoi

    client.post(f"/tournoi/{id_tournoi}/dupliquer", data={"date_heure": ""})
    duplique = _derniere(_journal_isole, "tournoi_cree")
    assert duplique["objet"] == "Tournoi modifié" and duplique["ref"] != id_tournoi

    client.post(f"/tournoi/{id_tournoi}/supprimer", data={"confirmation": "oui"})
    supprime = _derniere(_journal_isole, "tournoi_supprime")
    assert supprime["objet"] == "Tournoi modifié" and supprime["ref"] == id_tournoi


def test_tournoi_etat_change_succes_et_refus(client, _journal_isole):
    _connexion(client)
    client.post("/tournoi/nouveau", data={"jeu": "Chaussette"})
    id_tournoi = _derniere(_journal_isole, "tournoi_cree")["ref"]

    client.post(f"/tournoi/{id_tournoi}/etat", data={"etat": "inscriptions"})
    ok = _derniere(_journal_isole, "tournoi_etat_change")
    assert ok["ok"] is True and "inscriptions" in ok["objet"]

    client.post(f"/tournoi/{id_tournoi}/etat", data={"etat": "brouillon_inexistant"})
    ko = _derniere(_journal_isole, "tournoi_etat_change")
    assert ko["ok"] is False and ko["detail"] == "transition_refusee"


def test_tournoi_lance_echec_puis_succes_sans_fuite_de_pseudo(client, _journal_isole):
    _connexion(client)
    client.post("/tournoi/nouveau", data={"jeu": "Catan"})
    id_tournoi = _derniere(_journal_isole, "tournoi_cree")["ref"]
    client.post(f"/tournoi/{id_tournoi}/etat", data={"etat": "inscriptions"})

    # 0 participant : le lancement est refusé, et c'est journalisé (§2.3
    # côté prêt, même principe côté tournois : un échec est une info utile).
    client.post(f"/tournoi/{id_tournoi}/lancer", data={"mode_scoring": "high_score"})
    echec = _derniere(_journal_isole, "tournoi_lance")
    assert echec["ok"] is False

    client.post(f"/tournoi/{id_tournoi}/participant", data={"pseudo": "Zorglub"})
    client.post(f"/tournoi/{id_tournoi}/participant", data={"pseudo": "Gudule"})
    ajout = _derniere(_journal_isole, "participant_ajoute")
    # JAMAIS le pseudo (§8) : objet = le tournoi, pas qui y a été ajouté.
    assert ajout["objet"] == "Catan"
    assert "Zorglub" not in json.dumps(ajout) and "Gudule" not in json.dumps(ajout)

    client.post(f"/tournoi/{id_tournoi}/lancer", data={"mode_scoring": "high_score"})
    succes = _derniere(_journal_isole, "tournoi_lance")
    assert succes["ok"] is True and "high_score" in succes["objet"]

    # Suppression manuelle d'un participant : même règle, aucun pseudo.
    conn = None
    from app.tournoi.db import get_connection as get_tournoi_connection
    conn = get_tournoi_connection()
    try:
        insc = conn.execute(
            "SELECT id_inscription FROM inscriptions WHERE id_tournoi = ?", (id_tournoi,)
        ).fetchone()
    finally:
        conn.close()
    client.post(f"/tournoi/{id_tournoi}/participant/{insc[0]}/supprimer")
    retrait = _derniere(_journal_isole, "participant_supprime")
    assert retrait["objet"] == "Catan"


def test_tournois_jour_ouverts(client, _journal_isole):
    _connexion(client)
    client.post("/tournoi/ouvrir-aujourdhui")
    ligne = _derniere(_journal_isole, "tournois_jour_ouverts")
    assert ligne["module"] == "tournois"


# ===========================================================================
# LOT D — Priorité 2 : Programme du week-end (§2.2)
# ===========================================================================
def test_programme_cree_modifie_etat_supprime(client, _journal_isole):
    _connexion(client)
    client.post("/programme/nouveau", data={"intitule": "Atelier peinture"})
    cree = _derniere(_journal_isole, "programme_cree")
    assert cree["module"] == "programme" and cree["objet"] == "Atelier peinture"
    id_element = cree["ref"]

    client.post(f"/programme/{id_element}/editer", data={"intitule": "Atelier figurines"})
    modifie = _derniere(_journal_isole, "programme_modifie")
    assert modifie["objet"] == "Atelier figurines"

    client.post(f"/programme/{id_element}/etat", data={"etat": "publie"})
    etat = _derniere(_journal_isole, "programme_etat_change")
    assert etat["ok"] is True and "publie" in etat["objet"]

    client.post(f"/programme/{id_element}/supprimer", data={"confirmation": "oui"})
    supprime = _derniere(_journal_isole, "programme_supprime")
    assert supprime["objet"] == "Atelier figurines"


def test_programme_type_cree_modifie_supprime(client, _journal_isole):
    _connexion(client)
    client.post("/admin/programme-types", data={"nom": "Animation", "icone": "🎲"})
    cree = _derniere(_journal_isole, "programme_type_cree")
    assert cree["module"] == "programme" and cree["objet"] == "Animation"
    id_type = cree["ref"]

    client.post(f"/admin/programme-types/{id_type}/renommer",
                data={"nom": "Animation jeune public", "icone": "🎲"})
    modifie = _derniere(_journal_isole, "programme_type_modifie")
    assert modifie["objet"] == "Animation jeune public"

    client.post(f"/admin/programme-types/{id_type}/supprimer")
    supprime = _derniere(_journal_isole, "programme_type_supprime")
    assert supprime["ok"] is True and supprime["objet"] == "Animation jeune public"


# ===========================================================================
# LOT D — Priorité 2 : Planning (§2.2)
# ===========================================================================
def test_planning_fermeture_questionnaire_et_publication(client, _journal_isole):
    from app.planning import services as planning_services
    from app.planning.db import get_connection as get_planning_connection

    conn = get_planning_connection()
    try:
        ev = planning_services.creer_evenement(conn, "Week-end jeux 2026")
    finally:
        conn.close()

    _connexion(client)
    client.post(f"/planning/admin/{ev}/etat", data={"etat": "brouillon"})
    ferme = _derniere(_journal_isole, "planning_questionnaire_ferme")
    assert ferme["module"] == "planning" and ferme["objet"] == "Week-end jeux 2026"

    client.post(f"/planning/admin/{ev}/etat", data={"etat": "publie"})
    publie = _derniere(_journal_isole, "planning_publie")
    assert publie["objet"] == "Week-end jeux 2026"


def test_planning_genere_et_case_modifiee_sans_nom_de_benevole(client, _journal_isole):
    from app.planning import services as planning_services
    from app.planning.db import get_connection as get_planning_connection

    conn = get_planning_connection()
    try:
        ev = planning_services.creer_evenement(conn, "Édition de test")
        id_poste = planning_services.ajouter_poste(conn, ev, "Accueil")
        id_creneau = planning_services.ajouter_creneau(
            conn, ev, "samedi", "2026-08-08T10:00", "2026-08-08T12:00",
            type_creneau="poste",
        )
        souhaits = planning_services.enregistrer_souhaits(
            conn, ev, "BenevoleDeTest", dispos={id_creneau},
        )
        id_benevole = souhaits["id"]
    finally:
        conn.close()

    _connexion(client)
    client.post(f"/planning/admin/{ev}/prefiller")
    genere = _derniere(_journal_isole, "planning_genere")
    assert genere["module"] == "planning" and "Édition de test" in genere["objet"]
    assert "BenevoleDeTest" not in json.dumps(genere)

    client.post(
        f"/planning/admin/{ev}/affecter",
        data={"id_creneau": id_creneau, "id_poste": id_poste, "id_benevole": id_benevole},
    )
    case = _derniere(_journal_isole, "planning_case_modifiee")
    # JAMAIS de nom de bénévole (§8) : objet = la case (poste × créneau).
    assert "BenevoleDeTest" not in json.dumps(case)
    assert "Accueil" in case["objet"]


# ===========================================================================
# LOT D — Priorité 3 : Prêts, RÉUSSITE ET ÉCHECS (§2.3)
# ===========================================================================
def test_pret_retour_journalises_avec_le_nom_du_jeu(client, _journal_isole):
    _connexion(client)
    client.post("/pret/001/preter")
    prete = _derniere(_journal_isole, "pret")
    assert prete["module"] == "pret" and prete["ok"] is True
    assert prete["objet"] == "Catan" and prete["ref"] == "CATAN"
    # Jamais le numéro de pochette.
    assert "numero" not in json.dumps(prete).lower() or "numero_pochette" not in json.dumps(prete)

    client.post("/pret/001/rendre")
    rendu = _derniere(_journal_isole, "retour")
    assert rendu["ok"] is True and rendu["objet"] == "Catan"


def test_pret_deja_sorti_et_retour_deja_disponible_journalises_en_echec(client, _journal_isole):
    _connexion(client)
    client.post("/pret/001/preter")
    client.post("/pret/001/preter")  # déjà sorti
    echec_pret = _derniere(_journal_isole, "pret")
    assert echec_pret["ok"] is False and echec_pret["detail"] == "deja_sorti"

    client.post("/pret/001/rendre")
    client.post("/pret/001/rendre")  # déjà disponible
    echec_retour = _derniere(_journal_isole, "retour")
    assert echec_retour["ok"] is False and echec_retour["detail"] == "deja_disponible"


def test_re_pret_et_sortie_tournoi_journalises(client, _journal_isole):
    _connexion(client)
    client.post("/pret/001/repreter")
    repret = _derniere(_journal_isole, "re_pret")
    assert repret["ok"] is True and repret["objet"] == "Catan"

    client.post("/pret/001/rendre")
    client.post("/pret/001/tournoi")
    tournoi = _derniere(_journal_isole, "sortie_tournoi")
    assert tournoi["ok"] is True and tournoi["objet"] == "Catan"


# ===========================================================================
# LOT 4 des signalements — Carnet de maintenance
# (docs/conception-signalements.md §10)
#
# Les interdits (le détail libre, surtout) sont couverts par le garde-fou
# `tests/test_journal_interdits.py`, qui joue le scénario complet ; ici on
# vérifie la présence, le contenu utile et les DEUX absences délibérées :
# archiver/réactiver/réordonner ne produisent rien, comme pour les types de
# programme, et un second « Marquer traité » non plus.
# ===========================================================================
TEXTE_SIGNALEMENT = "DetailLibreQuiNeDoitPasSortir"


def _premiere_categorie(conn):
    return conn.execute(
        "SELECT id_categorie, nom FROM categories_signalement "
        "WHERE actif = 1 ORDER BY ordre LIMIT 1"
    ).fetchone()


def test_signalement_cree_journalise_le_jeu_et_la_categorie(client, conn, _journal_isole):
    """
    `objet` porte le jeu ET le libellé de la catégorie — ce dernier ne peut
    pas voyager dans `detail`, que `journaliser` ne conserve que sur un
    échec (§3 de docs/conception-journal.md). Le détail libre, lui, ne
    voyage nulle part.
    """
    _connexion(client)
    categorie = _premiere_categorie(conn)
    client.post("/pret/001/signaler", data={
        "id_categorie": str(categorie["id_categorie"]), "texte": TEXTE_SIGNALEMENT,
    })

    ligne = _derniere(_journal_isole, "signalement_cree")
    assert ligne["module"] == "pret" and ligne["ok"] is True
    assert ligne["objet"] == f"Catan — {categorie['nom']}"
    assert ligne["ref"] == "CATAN"
    assert TEXTE_SIGNALEMENT not in json.dumps(ligne)


def test_signalement_refuse_journalise_le_motif_sans_la_saisie(client, _journal_isole):
    """
    La branche de refus réaffiche le formulaire avec la saisie conservée :
    elle journalise donc, elle aussi, et c'est le seul endroit du carnet où
    `detail` est écrit. Il porte le motif, jamais ce que le bénévole avait
    tapé. Une catégorie archivée entre l'affichage et l'envoi est exactement
    le genre de surprise qu'on cherche après coup (§2.3).
    """
    _connexion(client)
    client.post("/pret/001/signaler", data={"texte": TEXTE_SIGNALEMENT})

    ligne = _derniere(_journal_isole, "signalement_cree")
    assert ligne["ok"] is False and ligne["detail"] == "categorie_manquante"
    assert TEXTE_SIGNALEMENT not in json.dumps(ligne)


def test_signalement_traite_journalise_une_seule_fois(client, conn, _journal_isole):
    """
    `traiter_signalement` est idempotent (UPDATE ... WHERE traite_le IS
    NULL) : un second appui ne change rien en base et ne doit donc rien
    écrire non plus — une ligne « traité » affirmerait un fait qui n'a pas eu
    lieu. Même précaution que « annonce effacée » sur /admin/ecran-salle.
    """
    _connexion(client)
    categorie = _premiere_categorie(conn)
    client.post("/pret/001/signaler", data={"id_categorie": str(categorie["id_categorie"])})
    (id_signalement,) = conn.execute(
        "SELECT id_signalement FROM signalements ORDER BY id_signalement DESC LIMIT 1"
    ).fetchone()

    client.post(f"/admin/signalements/{id_signalement}/traiter")
    ligne = _derniere(_journal_isole, "signalement_traite")
    assert ligne["module"] == "pret" and ligne["qui"] == "admin"
    assert ligne["objet"].startswith("Catan") and ligne["ref"] == "CATAN"

    client.post(f"/admin/signalements/{id_signalement}/traiter")  # sans effet
    assert len(_actions(_journal_isole, "signalement_traite")) == 1

    # Identifiant inconnu : pas davantage de ligne.
    client.post("/admin/signalements/999999/traiter")
    assert len(_actions(_journal_isole, "signalement_traite")) == 1


def test_signalement_traite_depuis_la_fiche_journalise_pareillement(client, conn, _journal_isole):
    """
    Lot agora-4 : la fiche de la boîte referme aussi, plus seulement
    l'administrateur (§2 de la note, corrigé). Même action de journal, même
    idempotence — la logique est FACTORISÉE avec la route admin ci-dessus
    (`routes/pret.py::_signalement_a_fermer` / `_journaliser_signalement_traite`),
    pas recopiée.
    """
    _connexion(client)
    categorie = _premiere_categorie(conn)
    client.post("/pret/001/signaler", data={"id_categorie": str(categorie["id_categorie"])})
    (id_signalement,) = conn.execute(
        "SELECT id_signalement FROM signalements ORDER BY id_signalement DESC LIMIT 1"
    ).fetchone()

    client.post(f"/pret/001/signalements/{id_signalement}/traiter")
    ligne = _derniere(_journal_isole, "signalement_traite")
    assert ligne["module"] == "pret" and ligne["objet"].startswith("Catan")
    assert ligne["ref"] == "CATAN"

    client.post(f"/pret/001/signalements/{id_signalement}/traiter")  # second appui
    assert len(_actions(_journal_isole, "signalement_traite")) == 1

    # Une boîte différente de celle de l'URL : rien n'est journalisé non plus.
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'DIXIT')"
    )
    conn.commit()
    client.post("/pret/002/signaler", data={"id_categorie": str(categorie["id_categorie"])})
    (id_signalement_2,) = conn.execute(
        "SELECT id_signalement FROM signalements ORDER BY id_signalement DESC LIMIT 1"
    ).fetchone()
    client.post(f"/pret/001/signalements/{id_signalement_2}/traiter")  # mauvaise boîte
    assert len(_actions(_journal_isole, "signalement_traite")) == 1


def test_crud_des_categories_de_signalement_journalise(client, conn, _journal_isole):
    _connexion(client)
    client.post("/admin/categories-signalement", data={"nom": "Notice envolée"})
    creee = _derniere(_journal_isole, "categorie_signalement_creee")
    assert creee["module"] == "pret" and creee["ok"] is True
    assert creee["objet"] == "Notice envolée"

    id_categorie = int(creee["ref"])
    client.post(f"/admin/categories-signalement/{id_categorie}/renommer",
                data={"nom": "Notice manquante"})
    modifiee = _derniere(_journal_isole, "categorie_signalement_modifiee")
    assert modifiee["objet"] == "Notice manquante" and modifiee["ok"] is True

    client.post(f"/admin/categories-signalement/{id_categorie}/supprimer")
    supprimee = _derniere(_journal_isole, "categorie_signalement_supprimee")
    # Le libellé est lu AVANT la suppression : après, la ligne ne pourrait
    # plus dire que « la catégorie 6 ».
    assert supprimee["objet"] == "Notice manquante" and supprimee["ok"] is True


def test_suppression_refusee_de_categorie_journalisee_en_echec(client, conn, _journal_isole):
    _connexion(client)
    categorie = _premiere_categorie(conn)
    client.post("/pret/001/signaler", data={"id_categorie": str(categorie["id_categorie"])})

    client.post(f"/admin/categories-signalement/{categorie['id_categorie']}/supprimer")
    ligne = _derniere(_journal_isole, "categorie_signalement_supprimee")
    assert ligne["ok"] is False and ligne["detail"] == "rattachee"


def test_archivage_et_reordonnancement_restent_hors_journal(client, conn, _journal_isole):
    """
    Décision explicite du §10 de la note, reprise des types de programme : ce
    sont des ajustements de PRÉSENTATION, pas des faits qu'on cherche après
    coup. Ce test existe pour que l'absence reste un choix, pas un oubli
    qu'on « corrigerait » un jour sans s'en apercevoir.
    """
    _connexion(client)
    categorie = _premiere_categorie(conn)
    id_categorie = categorie["id_categorie"]
    avant = len(_lignes(_journal_isole))

    client.post(f"/admin/categories-signalement/{id_categorie}/archiver")
    client.post(f"/admin/categories-signalement/{id_categorie}/reactiver")
    client.post(f"/admin/categories-signalement/{id_categorie}/descendre")
    client.post(f"/admin/categories-signalement/{id_categorie}/monter")

    assert len(_lignes(_journal_isole)) == avant
