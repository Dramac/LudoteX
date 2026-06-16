"""
Accès à la base SQLite : ouverture de connexions et initialisation du schéma.

RÔLE
----
Ce module est le SEUL endroit qui ouvre la base. Les autres modules reçoivent
une connexion (`services.py`) ou en demandent une via `get_connection()`
(les routes). Le schéma (les `CREATE TABLE`) vit dans `app/models.py` ; ici on
ne fait que l'exécuter.

CONFIGURATION
-------------
Le chemin du fichier SQLite est lu dans la variable d'environnement
``DATABASE_PATH`` (chargée depuis `.env` via python-dotenv), avec un repli sur
``data/pret-jeux.db`` — dossier volontairement non versionné (cf .gitignore).

USAGE
-----
    # En ligne de commande : créer/mettre à jour une base vide.
    python -m app.db

    # Dans le code applicatif :
    from app.db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM titres").fetchall()
    finally:
        conn.close()
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from app import models

# Charge les variables d'environnement depuis un fichier .env à la racine, s'il
# existe. Sans effet si .env est absent (cas des tests, du sandbox, etc.).
load_dotenv()

# Chemin par défaut si DATABASE_PATH n'est pas défini. Sous data/ (non versionné).
DEFAULT_DATABASE_PATH = "data/pret-jeux.db"


def get_database_path() -> Path:
    """
    Retourne le chemin du fichier SQLite à utiliser.

    Isolée dans une fonction (plutôt qu'une constante) pour que les tests
    puissent la remplacer (monkeypatch) et pointer vers une base temporaire.

    Returns:
        Le chemin (`pathlib.Path`) de la base.
    """
    return Path(os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH))


def get_connection() -> sqlite3.Connection:
    """
    Ouvre et configure une connexion SQLite.

    Configuration appliquée :
    - ``row_factory = sqlite3.Row`` : les lignes sont accessibles par nom de
      colonne (``row["nom"]``) en plus de l'index.
    - PRAGMA recommandés (voir ``models.PRAGMAS``) : `foreign_keys = ON` pour
      faire respecter les clés étrangères, et `journal_mode = WAL` pour une
      meilleure concurrence en écriture entre bénévoles.
    Le dossier parent de la base est créé si nécessaire.

    Returns:
        Une connexion SQLite prête à l'emploi. L'appelant doit la fermer.
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
    Crée les tables et les index s'ils n'existent pas. Idempotent.

    Tous les ``CREATE`` de ``models.SCHEMA_STATEMENTS`` utilisent
    ``IF NOT EXISTS`` : relancer cette fonction sur une base déjà initialisée ne
    casse rien et ne perd aucune donnée.

    Args:
        conn: connexion existante (utile pour les tests, qui passent une base en
            mémoire). Si ``None``, une connexion est ouverte puis refermée ici.
    """
    own_connection = conn is None  # a-t-on ouvert la connexion nous-mêmes ?
    if own_connection:
        conn = get_connection()
    try:
        for statement in models.SCHEMA_STATEMENTS:
            conn.executescript(statement)
        conn.commit()
    finally:
        # On ne ferme que si on a ouvert : ne pas fermer la connexion du test.
        if own_connection:
            conn.close()


# Exécuté quand on lance « python -m app.db » : initialise la base puis confirme.
if __name__ == "__main__":
    init_db()
    print(f"Base initialisée : {get_database_path()}")
