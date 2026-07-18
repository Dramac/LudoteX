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


def test_etat_bases_toutes_presentes(bases):
    from app import supervision

    infos = supervision.etat_bases()
    assert len(infos) == 3
    noms = {i["nom"] for i in infos}
    assert noms == {"Prêt de jeux", "Tournois", "Planning bénévole"}
    for i in infos:
        assert i["existe"] is True
        assert i["taille"] is not None
        assert i["modifie"] is not None


def test_etat_bases_base_manquante(bases):
    from app import supervision

    bases["planning"].unlink()
    infos = {i["nom"]: i for i in supervision.etat_bases()}
    assert infos["Planning bénévole"]["existe"] is False
    assert infos["Planning bénévole"]["taille"] is None
    assert infos["Prêt de jeux"]["existe"] is True


def test_espace_disque(bases):
    from app import supervision

    disque = supervision.espace_disque()
    assert disque["total"] and disque["libre"]
    assert 0 <= disque["pourcentage_libre"] <= 100


def test_derniere_sauvegarde_aucune(bases):
    from app import supervision

    resultat = supervision.derniere_sauvegarde()
    assert resultat == {"existe": False}


def test_derniere_sauvegarde_plus_recente(bases):
    import time

    from app import supervision

    dossier = bases["pret"].parent / "sauvegardes"
    dossier.mkdir()
    (dossier / "avant-restauration-ancienne.zip").write_bytes(b"vieux")
    time.sleep(0.01)
    (dossier / "avant-restauration-recente.zip").write_bytes(b"neuf")

    resultat = supervision.derniere_sauvegarde()
    assert resultat["existe"] is True
    assert resultat["nom"] == "avant-restauration-recente.zip"
    assert resultat["modifie"]


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
    assert set(etat) == {"bases", "disque", "sauvegarde", "jeton", "annonce", "version"}
    assert len(etat["bases"]) == 3
