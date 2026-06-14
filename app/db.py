"""
Connexion et initialisation de la base SQLite.

Usage :

    # Initialiser une base vide (crée les tables si absentes) :
    python -m app.db

    # Dans le code applicatif :
    from app.db import get_connection
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM titres").fetchall()

Le chemin de la base est lu depuis la variable d'environnement
``DATABASE_PATH`` (voir .env.example), avec une valeur par défaut sous
``data/`` — dossier non versionné.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from app import models

# Charge les variables depuis un éventuel fichier .env à la racine du projet.
load_dotenv()

# Chemin de la base — par défaut data/pret-jeux.db (dossier ignoré par git).
DEFAULT_DATABASE_PATH = "data/pret-jeux.db"


def get_database_path() -> Path:
    """Retourne le chemin de la base SQLite (depuis l'environnement ou défaut)."""
    return Path(os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH))


def get_connection() -> sqlite3.Connection:
    """
    Ouvre une connexion SQLite configurée.

    - ``row_factory`` = sqlite3.Row pour un accès aux colonnes par nom.
    - Applique les PRAGMA recommandés (clés étrangères, mode WAL).
    Le dossier parent de la base est créé au besoin.
    """
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for pragma in models.PRAGMAS:
        conn.execute(pragma)
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """
    Crée les tables et index s'ils n'existent pas (idempotent).

    Peut être appelée avec une connexion existante (tests) ou sans, auquel
    cas une connexion est ouverte puis fermée.
    """
    own_connection = conn is None
    if own_connection:
        conn = get_connection()
    try:
        for statement in models.SCHEMA_STATEMENTS:
            conn.executescript(statement)
        conn.commit()
    finally:
        if own_connection:
            conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Base initialisée : {get_database_path()}")
