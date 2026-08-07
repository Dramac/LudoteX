"""
Accès à la base SQLite SÉPARÉE des tournois (`data/tournoi.db`).

Pendant de `app/db.py`, mais pour la base des tournois. Volontairement DISTINCT
afin que les deux bases (prêt / tournoi) restent indépendantes (ouverture,
init, migrations, sauvegarde). Aucun module de prêt n'ouvre cette base, et
réciproquement.

DÉLAI D'ATTENTE DU VERROU D'ÉCRITURE
------------------------------------
`TIMEOUT_ECRITURE_S` est IMPORTÉE de `app/db.py` plutôt que redéfinie ici.
L'indépendance revendiquée ci-dessus porte sur les DONNÉES (aucune FK, aucune
requête croisée, sauvegardes séparées), pas sur les constantes de réglage : la
patience d'un écrivain face au verrou SQLite est un choix d'exploitation unique,
et deux valeurs qui dérivent l'une de l'autre seraient un piège silencieux — une
base répondrait au bout de 15 s, l'autre abandonnerait à 5 s, sans que rien ne
le signale. C'est aussi le précédent du projet : `tournoi/` et `planning/`
importent déjà une dizaine d'helpers de `app/services.py` (`maintenant`,
`FUSEAU_LOCAL`, `local_vers_utc_iso`…), et `services.transaction` a été importée
plutôt que dupliquée (02/08). La duplication d'`_ics_horodatage` était un cas
particulier assumé — quelques lignes de formatage sans réglage à tenir en
accord — pas une règle contre l'import.

CONFIGURATION
-------------
Le chemin du fichier SQLite est lu dans la variable d'environnement
``TOURNOI_DATABASE_PATH`` (chargée depuis `.env`), avec un repli sur
``data/tournoi.db`` (dossier non versionné, cf .gitignore).

USAGE
-----
    # En ligne de commande : créer/mettre à jour la base des tournois.
    python -m app.tournoi.db

    # Dans le code applicatif :
    from app.tournoi.db import get_connection
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

from app.db import TIMEOUT_ECRITURE_S
from app.tournoi import models

# Charge .env à la racine s'il existe (sans effet en test/sandbox).
load_dotenv()

# Chemin par défaut si TOURNOI_DATABASE_PATH n'est pas défini.
DEFAULT_DATABASE_PATH = "data/tournoi.db"


def get_database_path() -> Path:
    """
    Retourne le chemin du fichier SQLite des tournois.

    Isolée dans une fonction (et non une constante) pour que les tests puissent
    la remplacer (monkeypatch) et pointer vers une base temporaire.
    """
    return Path(os.getenv("TOURNOI_DATABASE_PATH", DEFAULT_DATABASE_PATH))


def get_connection() -> sqlite3.Connection:
    """
    Ouvre et configure une connexion vers la base des tournois.

    Mêmes réglages que la base de prêt : `row_factory = Row` (accès par nom),
    `foreign_keys = ON` (cascade de suppression effective), WAL, et le même
    délai d'attente du verrou d'écriture (`db.TIMEOUT_ECRITURE_S`) — voir le
    commentaire d'import en tête de module. Le dossier parent est créé si
    nécessaire. L'appelant doit fermer la connexion.
    """
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=TIMEOUT_ECRITURE_S)
    conn.row_factory = sqlite3.Row
    for pragma in models.PRAGMAS:
        conn.execute(pragma)
    return conn


# Colonnes ajoutées APRÈS la première mise en service (ALTER TABLE des bases déjà
# créées ; les CREATE TABLE IF NOT EXISTS ne le font pas). Format : (table,
# colonne, type_sql). Vide pour l'instant — à étendre à chaque évolution.
_MIGRATIONS_COLONNES: list[tuple[str, str, str]] = [
    ("tournois", "nb_rondes", "INTEGER"),
    ("tournois", "age", "TEXT"),
    ("tournois", "par_equipes", "INTEGER NOT NULL DEFAULT 0"),
    ("tournois", "taille_equipe", "INTEGER"),
    ("inscriptions", "membres", "TEXT"),
]

# Premier remplissage de la liste des types de programme (docs/conception-
# programme.md §4.1) : les 5 types de la note d'intention, pour que le module
# soit utilisable sans configuration préalable. Le bureau renomme/archive
# ensuite depuis l'administration (jalon 2).
_TYPES_PROGRAMME_SEED = [
    ("Animation", "🎉"),
    ("Atelier", "🛠️"),
    ("Initiation", "🎓"),
    ("Temps fort", "✨"),
    ("Intervention partenaire", "🤝"),
]


def _appliquer_migrations(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes des bases déjà créées (idempotent)."""
    for table, colonne, type_sql in _MIGRATIONS_COLONNES:
        existantes = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if colonne not in existantes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} {type_sql}")
    conn.commit()


def _seed_types_programme(conn: sqlite3.Connection) -> None:
    """
    Premier remplissage de `types_programme` (idempotent) : n'insère les types
    par défaut QUE si la table est vide, pour ne rien dupliquer si `init_db`
    est rappelé et ne jamais recréer un type que le bureau aurait supprimé
    depuis (même patron que `_seed_emplacements_rangement` côté prêt).
    """
    (nb,) = conn.execute("SELECT COUNT(*) FROM types_programme").fetchone()
    if nb:
        return
    conn.executemany(
        "INSERT INTO types_programme (nom, icone, actif, ordre) VALUES (?, ?, 1, ?)",
        [(nom, icone, ordre) for ordre, (nom, icone) in enumerate(_TYPES_PROGRAMME_SEED)],
    )
    conn.commit()


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """
    Crée les tables/index manquants de la base des tournois et applique les
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
        _seed_types_programme(conn)
    finally:
        if own_connection:
            conn.close()


# Exécuté via « python -m app.tournoi.db ».
if __name__ == "__main__":
    init_db()
    print(f"Base des tournois initialisée : {get_database_path()}")
