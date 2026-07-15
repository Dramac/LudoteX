"""Tests du peuplement de données du mode formation (app/formation.py)."""

import pytest


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Bases de prêt + tournois temporaires, isolées par test."""
    chemin_pret = tmp_path / "pret-jeux.db"
    chemin_tournoi = tmp_path / "tournoi.db"

    monkeypatch.setenv("DATABASE_PATH", str(chemin_pret))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(chemin_tournoi))

    from app import db
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: chemin_pret)
    monkeypatch.setattr(tdb, "get_database_path", lambda: chemin_tournoi)

    db.init_db()
    tdb.init_db()

    return {"pret": chemin_pret, "tournoi": chemin_tournoi}


def test_peupler_pret_compte_correct(bases):
    from app import formation
    from app.db import get_connection

    conn = get_connection()
    try:
        resume = formation.peupler_pret(conn)
        assert resume == {"jeux": 20, "prets_en_cours": 5, "prets_termines": 5}

        nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
        nb_ex = conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
        assert nb_titres == 20 and nb_ex == 20

        # 5 sortis actuellement (date_retour NULL) + 5 déjà rendus.
        sortis = conn.execute(
            "SELECT COUNT(*) FROM prets WHERE date_retour IS NULL"
        ).fetchone()[0]
        rendus = conn.execute(
            "SELECT COUNT(*) FROM prets WHERE date_retour IS NOT NULL"
        ).fetchone()[0]
        assert sortis == 5 and rendus == 5

        # Noms explicitement fictifs.
        noms = [r[0] for r in conn.execute("SELECT nom FROM titres").fetchall()]
        assert all(n.startswith("Jeu d'essai n°") for n in noms)
    finally:
        conn.close()


def test_peupler_pret_idempotent(bases):
    from app import formation
    from app.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_pret(conn)
        # Un bénévole modifie l'état (prête un jeu de plus) avant la 2e passe.
        resume2 = formation.peupler_pret(conn)
        assert resume2 == {"jeux": 20, "prets_en_cours": 5, "prets_termines": 5}

        nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
        nb_prets = conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
        # Pas d'accumulation d'une passe à l'autre : la base est vidée avant.
        assert nb_titres == 20
        assert nb_prets == 10
    finally:
        conn.close()


def test_peupler_tournoi(bases):
    from app import formation
    from app.tournoi.db import get_connection
    from app.tournoi.services import get_tournoi

    conn = get_connection()
    try:
        resume = formation.peupler_tournoi(conn)
        assert resume == {"tournois": 1, "inscrits": 4}

        row = conn.execute("SELECT id_tournoi FROM tournois").fetchone()
        assert row is not None
        t = get_tournoi(conn, row[0])
        assert t["etat"] == "inscriptions"
        assert "formation" in t["nom"].lower()

        nb_inscrits = conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0]
        assert nb_inscrits == 4
    finally:
        conn.close()


def test_peupler_tournoi_idempotent(bases):
    from app import formation
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_tournoi(conn)
        formation.peupler_tournoi(conn)
        nb_tournois = conn.execute("SELECT COUNT(*) FROM tournois").fetchone()[0]
        nb_inscrits = conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0]
        assert nb_tournois == 1
        assert nb_inscrits == 4
    finally:
        conn.close()


def test_peupler_orchestre_les_deux_bases(bases):
    from app import formation

    resume = formation.peupler()
    assert resume["jeux"] == 20
    assert resume["tournois"] == 1
    assert resume["inscrits"] == 4
