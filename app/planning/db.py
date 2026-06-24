"""
Accès à la base SQLite SÉPARÉE du planning bénévole (`data/planning.db`).

Pendant de `app/db.py` (prêt) et `app/tournoi/db.py` (tournois), mais pour la
base du planning. Volontairement DISTINCT afin que les trois bases restent
indépendantes (ouverture, init, migrations, purge, sauvegarde). Aucun autre
module n'ouvre cette base, et réciproquement.

CONFIGURATION
-------------
Le chemin du fichier SQLite est lu dans la variable d'environnement
``PLANNING_DATABASE_PATH`` (chargée depuis `.env`), avec un repli sur
``data/planning.db`` (dossier non versionné, cf .gitignore).

USAGE
-----
    # En ligne de commande : créer/mettre à jour la base du planning.
    python -m app.planning.db

    # Dans le code applicatif :
    from app.planning.db import get_connection
    conn = get_connection()
    try:
        ...
    finally:
        conn.close()
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from app.planning import models

# Charge .env à la racine s'il existe (sans effet en test/sandbox).
load_dotenv()

# Chemin par défaut si PLANNING_DATABASE_PATH n'est pas défini.
DEFAULT_DATABASE_PATH = "data/planning.db"


def get_database_path() -> Path:
    """
    Retourne le chemin du fichier SQLite du planning.

    Isolée dans une fonction (et non une constante) pour que les tests puissent
    la remplacer (monkeypatch) et pointer vers une base temporaire.
    """
    return Path(os.getenv("PLANNING_DATABASE_PATH", DEFAULT_DATABASE_PATH))


def get_connection() -> sqlite3.Connection:
    """
    Ouvre et configure une connexion vers la base du planning.

    Mêmes réglages que les autres bases : `row_factory = Row` (accès par nom),
    `foreign_keys = ON` (cascade de suppression effective) et WAL. Le dossier
    parent est créé si nécessaire. L'appelant doit fermer la connexion.
    """
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for pragma in models.PRAGMAS:
        conn.execute(pragma)
    return conn


# Colonnes ajoutées APRÈS la première mise en service (ALTER TABLE des bases déjà
# créées ; les CREATE TABLE IF NOT EXISTS ne le font pas). Format : (table,
# colonne, type_sql). Vide pour l'instant — à étendre à chaque évolution.
_MIGRATIONS_COLONNES: list[tuple[str, str, str]] = []


def _appliquer_migrations(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes des bases déjà créées (idempotent)."""
    for table, colonne, type_sql in _MIGRATIONS_COLONNES:
        existantes = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if colonne not in existantes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} {type_sql}")
    conn.commit()


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """
    Crée les tables/index manquants de la base du planning et applique les
    migrations de colonnes. Idempotent : sans danger pour les données existantes.

    Args:
        conn: connexion existante (tests, base en mémoire). Si ``None``, une
            connexion est ouverte puis refermée ici.
    """
    own_connection = conn is None
    if own_connection:
        conn = get_connection()
    try:
        for statement in models.SCHEMA_STATEMENTS:
            conn.executescript(statement)
        conn.commit()
        _appliquer_migrations(conn)
    finally:
        if own_connection:
            conn.close()


# Exécuté via « python -m app.planning.db ».
if __name__ == "__main__":
    init_db()
    print(f"Base du planning initialisée : {get_database_path()}")
