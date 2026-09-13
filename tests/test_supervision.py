"""Tests de la supervision légère (app/supervision.py) — lecture seule."""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Initialise les 3 bases dans un dossier temporaire et renvoie leurs chemins."""
    chemin_pret = tmp_path / "pret-jeux.db"
    chemin_tournoi = tmp_path / "tournoi.db"
    chemin_planning = tmp_path / "planning.db"

    monkeypatch.setenv("DATABASE_PATH", str(chemin_pret))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(chemin_tournoi))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(chemin_planning))

    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: chemin_pret)
    monkeypatch.setattr(tdb, "get_database_path", lambda: chemin_tournoi)
    monkeypatch.setattr(pdb, "get_database_path", lambda: chemin_planning)

    db.init_db()
    tdb.init_db()
    pdb.init_db()

    return {"pret": chemin_pret, "tournoi": chemin_tournoi, "planning": chemin_planning}


def _age(chemin, heures):
    """Recule la date de modification d'un fichier de `heures` heures."""
    import os
    import time

    instant = time.time() - heures * 3600
    os.utime(chemin, (instant, instant))


def _archive(dossier, nom, heures=0):
    dossier.mkdir(exist_ok=True)
    chemin = dossier / nom
    chemin.write_bytes(b"zip")
    _age(chemin, heures)
    return chemin


def test_etat_bases_toutes_presentes(bases):
    from app import supervision

    infos = supervision.etat_bases()
    assert len(infos) == 3
    noms = {i["nom"] for i in infos}
    assert noms == {"Prêt de jeux", "Tournois", "Planning bénévole"}
    for i in infos:
        assert i["existe"] is True
        assert i["etat"] == "ok" and i["sain"] is True
        assert i["libelle"] == "Ok"
        assert i["taille"] is not None
        assert i["modifie"] is not None


def test_etat_bases_base_manquante(bases):
    from app import supervision

    bases["planning"].unlink()
    infos = {i["nom"]: i for i in supervision.etat_bases()}
    assert infos["Planning bénévole"]["existe"] is False
    assert infos["Planning bénévole"]["etat"] == "introuvable"
    assert infos["Planning bénévole"]["sain"] is False
    assert infos["Planning bénévole"]["taille"] is None
    assert infos["Prêt de jeux"]["existe"] is True


def test_une_base_absente_n_est_pas_creee_par_la_supervision(bases):
    """Ouvrir SQLite sur un chemin absent CRÉE le fichier : une sonde d'audit a
    fabriqué ainsi une base fantôme sur le serveur. La supervision, lue à chaque
    visite du tableau de bord, ne doit jamais le faire."""
    from app import supervision

    for chemin in bases.values():
        chemin.unlink()
    for _ in range(2):
        supervision.etat_bases()
    for chemin in bases.values():
        assert not chemin.exists()
        assert not chemin.with_name(chemin.name + "-wal").exists()


def test_le_controle_d_integrite_ne_laisse_aucun_fichier_annexe(bases):
    """`mode=ro` seul crée `-wal` et `-shm` à côté d'une base WAL et ne les
    retire pas : la supervision en aurait semé à chaque visite du tableau de
    bord, à côté de bases que rien d'autre n'ouvre pendant des semaines."""
    from app import supervision

    avant = sorted(p.name for p in bases["pret"].parent.iterdir())
    for _ in range(2):
        assert all(i["sain"] for i in supervision.etat_bases())
    assert sorted(p.name for p in bases["pret"].parent.iterdir()) == avant


def test_le_controle_d_integrite_voit_une_base_ouverte_en_ecriture(bases):
    """Une connexion vivante (fichier -wal présent) : lecture partagée, sans
    fausse alerte, y compris sur des écritures pas encore recopiées."""
    import sqlite3

    from app import supervision

    conn = sqlite3.connect(bases["tournoi"])
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS essai (x)")
        conn.execute("INSERT INTO essai VALUES (1)")
        conn.commit()
        assert bases["tournoi"].with_name("tournoi.db-wal").exists()
        infos = {i["nom"]: i for i in supervision.etat_bases()}
        assert infos["Tournois"]["etat"] == "ok"
    finally:
        conn.close()


@pytest.mark.parametrize("contenu, etat_attendu", [
    (b"SQLite format 3\x00" + b"\x00" * 4000, "corrompue"),
    (b"ceci n'est pas une base " * 200, "corrompue"),
    (b"", "vide"),
])
def test_base_abimee_ou_vide_en_attention_sans_exception(bases, contenu, etat_attendu):
    from app import supervision

    bases["tournoi"].write_bytes(contenu)
    infos = {i["nom"]: i for i in supervision.etat_bases()}
    assert infos["Tournois"]["etat"] == etat_attendu
    assert infos["Tournois"]["sain"] is False
    assert infos["Prêt de jeux"]["sain"] is True


def test_base_tronquee_est_corrompue(bases):
    import sqlite3

    from app import supervision

    conn = sqlite3.connect(bases["planning"])
    conn.execute("CREATE TABLE remplissage (x TEXT)")
    conn.executemany("INSERT INTO remplissage VALUES (?)", [("x" * 500,)] * 400)
    conn.commit()
    conn.close()
    donnees = bases["planning"].read_bytes()
    bases["planning"].write_bytes(donnees[: len(donnees) // 2])

    infos = {i["nom"]: i for i in supervision.etat_bases()}
    assert infos["Planning bénévole"]["etat"] == "corrompue"


def test_espace_disque(bases):
    from app import supervision

    disque = supervision.espace_disque()
    assert disque["total"] and disque["libre"]
    assert 0 <= disque["pourcentage_libre"] <= 100


def test_espace_disque_sous_le_seuil(bases, monkeypatch):
    from app import supervision

    seuil = supervision.SEUIL_DISQUE_LIBRE_POURCENT
    total = 1000 * 1024 * 1024
    juste_sous = total * (seuil - 0.5) / 100
    monkeypatch.setattr(supervision.shutil, "disk_usage",
                        lambda _d: (total, total - juste_sous, juste_sous))
    assert supervision.espace_disque()["sous_seuil"] is True

    au_seuil = total * seuil / 100
    monkeypatch.setattr(supervision.shutil, "disk_usage",
                        lambda _d: (total, total - au_seuil, au_seuil))
    assert supervision.espace_disque()["sous_seuil"] is False


# ---------------------------------------------------------------------------
# Sauvegarde (PROD-02) : la ROUTINE, et son âge
# ---------------------------------------------------------------------------
def test_sauvegarde_aucune(bases):
    from app import supervision

    etat = supervision.etat_sauvegarde()
    assert etat["ok"] is False
    assert etat["routine"] is None and etat["filet"] is None
    assert etat["dossier"] == str(bases["pret"].parent / "sauvegardes")


def test_sauvegarde_routine_recente_ok(bases):
    from app import supervision

    dossier = bases["pret"].parent / "sauvegardes"
    _archive(dossier, "ludotex-backup-20260913-030001.zip", heures=5)

    etat = supervision.etat_sauvegarde()
    assert etat["ok"] is True
    assert etat["routine"]["nom"] == "ludotex-backup-20260913-030001.zip"
    assert etat["routine"]["age"] == "5 heures"
    assert etat["filet"] is None


def test_sauvegarde_routine_trop_vieille_meme_avec_un_filet_recent(bases):
    """PROD-02 exactement : un filet récent ne prouve pas que la routine tourne."""
    from app import supervision

    dossier = bases["pret"].parent / "sauvegardes"
    heures_seuil = supervision.SEUIL_AGE_SAUVEGARDE.total_seconds() / 3600
    _archive(dossier, "ludotex-backup-20260701-030001.zip", heures=heures_seuil + 1)
    _archive(dossier, "avant-mise-a-jour-20260913-185012.zip", heures=0)
    _archive(dossier, "avant-restauration-20260912-101010.zip", heures=20)

    etat = supervision.etat_sauvegarde()
    assert etat["ok"] is False
    assert etat["routine"]["trop_ancienne"] is True
    assert etat["filet"]["nom"] == "avant-mise-a-jour-20260913-185012.zip"
    assert etat["filet"]["libelle"] == "Filet avant mise à jour"


def test_sauvegarde_dossier_ne_contenant_que_des_filets(bases):
    from app import supervision

    dossier = bases["pret"].parent / "sauvegardes"
    _archive(dossier, "avant-mise-a-jour-20260913-185012.zip")
    _archive(dossier, "avant-restauration-20260913-101010.zip")
    (dossier / "notes.txt").write_text("pas une archive")

    etat = supervision.etat_sauvegarde()
    assert etat["ok"] is False
    assert etat["routine"] is None
    assert etat["filet"] is not None


def test_sauvegarde_seuil_ne_crie_pas_apres_une_nuit_normale(bases):
    """24 h entre deux passages de nuit, plus une marge : pas d'alerte à 25 h."""
    from app import supervision

    dossier = bases["pret"].parent / "sauvegardes"
    _archive(dossier, "ludotex-backup-20260912-030001.zip", heures=25)
    assert supervision.etat_sauvegarde()["ok"] is True
    assert supervision.SEUIL_AGE_SAUVEGARDE > timedelta(hours=25)


def test_format_taille():
    from app import services

    assert services.format_taille(512) == "512 o"
    assert services.format_taille(340 * 1024) == "340 Ko"
    assert services.format_taille(int(3.2 * 1024 ** 2)) == "3,2 Mo"
    assert services.format_taille(16 * 1024 ** 3) == "16,0 Go"


def test_sauvegarde_ne_crie_pas_en_mode_formation(bases, monkeypatch):
    """Le minuteur de nuit ne sauvegarde que l'instance principale."""
    from app import supervision

    monkeypatch.setattr(supervision, "MODE_FORMATION", True)
    etat = supervision.etat_sauvegarde()
    assert etat["formation"] is True
    assert etat["routine"] is None


def test_page_supervision_en_mode_formation_sans_attention_de_sauvegarde(bases, monkeypatch):
    from fastapi.testclient import TestClient

    from app import supervision
    from app.main import app

    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    monkeypatch.setattr(supervision, "MODE_FORMATION", True)
    client = TestClient(app)
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r = client.get("/admin/supervision")
    assert r.status_code == 200
    assert "Instance de formation" in r.text
    assert "aucune sauvegarde de routine" not in r.text


def test_duree_en_toutes_lettres():
    from app import supervision

    assert supervision.duree_en_toutes_lettres(timedelta(minutes=10)) == "moins d'une heure"
    assert supervision.duree_en_toutes_lettres(timedelta(hours=1)) == "1 heure"
    assert supervision.duree_en_toutes_lettres(timedelta(hours=26)) == "26 heures"
    assert supervision.duree_en_toutes_lettres(timedelta(days=12, hours=3)) == "12 jours"
    assert supervision.duree_en_toutes_lettres(timedelta(hours=-2)) == "moins d'une heure"


def test_etat_jeton_non_defini(bases):
    from app import supervision
    from app.db import get_connection

    conn = get_connection()
    try:
        assert supervision.etat_jeton(conn) == {"defini": False}
    finally:
        conn.close()


def test_etat_jeton_valide_et_expire(bases):
    from app import auth, supervision
    from app.db import get_connection

    conn = get_connection()
    try:
        futur = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
        auth.reinitialiser_jeton(conn, futur)
        etat = supervision.etat_jeton(conn)
        assert etat["defini"] is True
        assert etat["expire"] is False
        assert etat["expire_iso"] == futur

        passe = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        auth.reinitialiser_jeton(conn, passe)
        etat2 = supervision.etat_jeton(conn)
        assert etat2["defini"] is True
        assert etat2["expire"] is True
    finally:
        conn.close()


def test_version_deployee_fallback(bases, monkeypatch):
    from app import supervision

    monkeypatch.setattr(supervision, "_FICHIER_VERSION", bases["pret"].parent / "VERSION_absente")
    assert supervision.version_deployee() == supervision.APP_VERSION


def test_version_deployee_depuis_fichier(bases, monkeypatch, tmp_path):
    from app import supervision

    fichier = tmp_path / "VERSION"
    fichier.write_text("LudoteX 1.2.3 — test\n", encoding="utf-8")
    monkeypatch.setattr(supervision, "_FICHIER_VERSION", fichier)
    assert supervision.version_deployee() == "LudoteX 1.2.3 — test"


def test_etat_supervision_rassemble_tout(bases):
    from app import supervision
    from app.db import get_connection

    conn = get_connection()
    try:
        etat = supervision.etat_supervision(conn)
    finally:
        conn.close()
    assert set(etat) == {
        "bases", "disque", "sauvegarde", "jeton", "annonce", "journal", "version",
    }
    assert len(etat["bases"]) == 3


def test_etat_supervision_un_bloc_qui_plante_ne_fait_pas_tomber_les_autres(bases, monkeypatch):
    from app import supervision
    from app.db import get_connection

    def _plante():
        raise PermissionError("dossier illisible")

    monkeypatch.setattr(supervision, "etat_sauvegarde", _plante)
    monkeypatch.setattr(supervision, "espace_disque", _plante)
    conn = get_connection()
    try:
        etat = supervision.etat_supervision(conn)
    finally:
        conn.close()
    assert etat["sauvegarde"] == {"erreur": True}
    assert etat["disque"] == {"erreur": True}
    assert len(etat["bases"]) == 3


# ---------------------------------------------------------------------------
# Journal (EXP-04) : ni mentir, ni crier à tort
# ---------------------------------------------------------------------------
def test_etat_journal_vide_par_defaut(_journal_isole):
    """Le fichier est créé (vide) dès la configuration du logger (fixture
    autouse) : « vide » doit rester vrai tant qu'aucune ligne n'est écrite, et
    un journal vide sans activité ne crie pas."""
    from app import supervision

    etat = supervision.etat_journal()
    assert etat["vide"] is True
    assert etat["etat"] == "neutre"


def test_etat_journal_affiche_taille_et_date(bases, _journal_isole):
    from app import journal, supervision

    class _Faux:
        cookies: dict = {}

    journal.journaliser(_Faux(), "pret", "pret", objet="Catan")

    etat = supervision.etat_journal()
    assert etat["existe"] is True
    assert etat["vide"] is False
    assert etat["taille"]
    assert etat["modifie"]
    assert etat["etat"] == "ok"


def test_etat_journal_introuvable(_journal_isole):
    from app import supervision

    _journal_isole.unlink()
    assert supervision.etat_journal()["etat"] == "introuvable"


def _iso_dans(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def test_etat_journal_en_retard_sur_un_pret(_journal_isole):
    """Un prêt plus récent que la dernière écriture du journal : il n'est plus écrit."""
    from app import supervision

    _age(_journal_isole, heures=2)
    etat = supervision.etat_journal(_iso_dans(0))
    assert etat["etat"] == "en_retard"
    assert etat["derniere_operation"]


def test_etat_journal_ne_crie_pas_pour_un_pret_juste_anterieur_ou_dans_la_tolerance(_journal_isole):
    from app import supervision

    assert supervision.etat_journal(_iso_dans(-60))["etat"] == "neutre"
    tolerance_min = supervision.TOLERANCE_JOURNAL.total_seconds() / 60
    assert supervision.etat_journal(_iso_dans(tolerance_min - 1))["etat"] == "neutre"


def test_etat_journal_ne_crie_pas_des_semaines_sans_activite(bases, _journal_isole):
    """Hors événement, le journal peut se taire longtemps : ce n'est pas une panne."""
    from app import journal, supervision

    class _Faux:
        cookies: dict = {}

    journal.journaliser(_Faux(), "pret", "pret", objet="Catan")
    _age(_journal_isole, heures=24 * 60)
    ancien = (datetime.now(timezone.utc) - timedelta(days=61)).isoformat(timespec="seconds")
    assert supervision.etat_journal(ancien)["etat"] == "ok"


def test_etat_journal_pas_de_controle_en_mode_formation(_journal_isole, monkeypatch):
    """`python -m app.formation` peuple des prêts datés sans journal, par construction."""
    from app import supervision

    monkeypatch.setattr(supervision, "MODE_FORMATION", True)
    _age(_journal_isole, heures=2)
    assert supervision.etat_journal(_iso_dans(0))["etat"] == "neutre"


def test_derniere_operation_pret_prend_sortie_ou_retour(bases, vieillir_prets):
    from app import services
    from app.db import get_connection

    conn = get_connection()
    try:
        assert services.derniere_operation_pret(conn) is None
        conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('T', 'Jeu')")
        conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'T')")
        conn.execute(
            "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, date_retour) "
            "VALUES ('001', NULL, '2026-09-13T10:00:00+00:00', '2026-09-13T12:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie) "
            "VALUES ('001', 1, '2026-09-13T11:00:00+00:00')"
        )
        conn.commit()
        assert services.derniere_operation_pret(conn) == "2026-09-13T12:00:00+00:00"
    finally:
        conn.close()
