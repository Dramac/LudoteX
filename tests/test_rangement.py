"""
Tests du suivi de l'emplacement de rangement (docs/conception-rangement.md).

Étape 1 (schéma) : colonnes ajoutées à `exemplaires`, table
`emplacements_rangement` créée et seedée, migrations idempotentes. Les
étapes suivantes (écran admin, mode rangement au scanner, affichages...)
ajouteront leurs tests dans ce même fichier.
"""

import sqlite3

import pytest

from app import db


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Chemin de base temporaire, isolé de la vraie base (monkeypatch DATABASE_PATH)."""
    chemin = tmp_path / "test.db"
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    return chemin


def _colonnes(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_colonnes_emplacement_presentes_sur_base_neuve(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        colonnes = _colonnes(conn, "exemplaires")
        assert "emplacement_evenement" in colonnes
        assert "emplacement_local_id" in colonnes
    finally:
        conn.close()


def test_table_emplacements_rangement_creee_et_seedee(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        lignes = conn.execute(
            "SELECT nom, actif, ordre FROM emplacements_rangement ORDER BY ordre"
        ).fetchall()
        noms = [r["nom"] for r in lignes]
        assert noms == ["Totem", "Puzzle", "P'tits potes", "valise 1", "valise 2"]
        assert all(r["actif"] == 1 for r in lignes)
        assert [r["ordre"] for r in lignes] == [0, 1, 2, 3, 4]
    finally:
        conn.close()


def test_seed_idempotent_ne_duplique_pas(db_path):
    db.init_db()
    db.init_db()  # ré-appel volontaire, doit être sans effet
    conn = db.get_connection()
    try:
        (nb,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
        assert nb == 5
    finally:
        conn.close()


def test_seed_ne_ressuscite_pas_une_entree_supprimee(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM emplacements_rangement WHERE nom = 'valise 2'")
        conn.commit()
    finally:
        conn.close()

    db.init_db()  # ne doit PAS recréer "valise 2" : la table n'est plus vide
    conn = db.get_connection()
    try:
        noms = [
            r["nom"]
            for r in conn.execute("SELECT nom FROM emplacements_rangement").fetchall()
        ]
        assert "valise 2" not in noms
        assert len(noms) == 4
    finally:
        conn.close()


def test_migration_sur_base_existante_sans_les_colonnes(db_path):
    # Simule une base créée AVANT cette évolution : schéma sans les 2 colonnes
    # ni la table emplacements_rangement.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE titres (
            reference_titre TEXT PRIMARY KEY,
            nom TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE exemplaires (
            id_exemplaire TEXT PRIMARY KEY,
            reference_titre TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')"
    )
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    db.init_db()  # doit migrer sans toucher aux données existantes

    conn = db.get_connection()
    try:
        colonnes = _colonnes(conn, "exemplaires")
        assert "emplacement_evenement" in colonnes
        assert "emplacement_local_id" in colonnes
        # La boîte existante est intacte.
        row = conn.execute(
            "SELECT id_exemplaire, reference_titre FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["reference_titre"] == "CATAN"
        # La liste des emplacements a bien été seedée sur la base migrée.
        (nb,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
        assert nb == 5
    finally:
        conn.close()
