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


# Colonnes ajoutées APRÈS la première mise en service : on les ajoute aux bases
# existantes via ALTER TABLE (les CREATE TABLE IF NOT EXISTS ne le font pas).
# Format : (table, colonne, type_sql). Étendre cette liste à chaque évolution.
_MIGRATIONS_COLONNES = [
    ("titres", "type_jeu", "TEXT"),
    ("prets", "motif", "TEXT NOT NULL DEFAULT 'pret'"),
    ("titres", "date_achat", "TEXT"),
    ("exemplaires", "emplacement_evenement", "TEXT"),
    ("exemplaires", "emplacement_local_id", "INTEGER"),
]

# Premier remplissage de la liste des emplacements de rangement LOCAL (voir
# docs/conception-rangement.md §3). Appliqué une seule fois : si la table est
# déjà peuplée (première init passée, ou admin qui a modifié la liste), on ne
# touche à rien.
_EMPLACEMENTS_RANGEMENT_SEED = [
    "Totem",
    "Puzzle",
    "P'tits potes",
    "valise 1",
    "valise 2",
]


def _appliquer_migrations(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes des bases déjà créées (idempotent)."""
    for table, colonne, type_sql in _MIGRATIONS_COLONNES:
        existantes = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if colonne not in existantes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} {type_sql}")
    conn.commit()


# Colonnes de `prets`, dans l'ordre, telles que définies par models.SCHEMA_PRETS.
# Utilisées par _migrer_pochette_nullable, qui recopie la table ligne à ligne :
# toute divergence avec le schéma réel FAIT ÉCHOUER la migration plutôt que de
# laisser tomber silencieusement une colonne. Tenir à jour avec models.py.
_COLONNES_PRETS = (
    "id_pret",
    "id_exemplaire",
    "numero_pochette",
    "date_sortie",
    "date_retour",
    "motif",
)


def _pochette_deja_nullable(conn: sqlite3.Connection) -> bool:
    """Vrai si `prets.numero_pochette` a déjà perdu sa contrainte NOT NULL."""
    for _, nom, _type, notnull, _defaut, _pk in conn.execute("PRAGMA table_info(prets)"):
        if nom == "numero_pochette":
            return not notnull
    # Colonne absente : base inattendue, on ne touche à rien.
    return True


def _migrer_pochette_nullable(conn: sqlite3.Connection) -> None:
    """
    Rend `prets.numero_pochette` NULLABLE et purge l'historique déjà constitué
    (fiche D5 ; voir le commentaire de models.SCHEMA_PRETS).

    SQLite ne sait pas retirer un NOT NULL par ALTER TABLE : il faut
    reconstruire la table. `prets` étant la table la plus sensible du projet
    (son historique n'est jamais purgé), la procédure suit à la lettre le motif
    officiel SQLite et s'entoure de trois garde-fous :

    - IDEMPOTENCE : on sort immédiatement si la colonne est déjà nullable —
      c'est le drapeau `notnull` de PRAGMA table_info qui sert de témoin, pas
      une table de versions à maintenir.
    - CLÉS ÉTRANGÈRES : `PRAGMA foreign_keys` est un NO-OP à l'intérieur d'une
      transaction ; il est donc désactivé AVANT le BEGIN, et l'intégrité est
      revérifiée après coup par `PRAGMA foreign_key_check`.
    - AUCUNE LIGNE PERDUE : les lignes sont comptées avant et après, DANS la
      même transaction. Tout écart lève, donc annule le DROP — la base reste
      exactement dans son état d'origine.
    """
    if _pochette_deja_nullable(conn):
        return

    reelles = tuple(r[1] for r in conn.execute("PRAGMA table_info(prets)"))
    if reelles != _COLONNES_PRETS:
        raise RuntimeError(
            "Migration de `prets` impossible : les colonnes réelles "
            f"{reelles} ne correspondent pas à _COLONNES_PRETS "
            f"{_COLONNES_PRETS}. Mettre les deux listes en accord (voir "
            "app/models.py) avant de relancer."
        )

    colonnes = ", ".join(_COLONNES_PRETS)
    conn.commit()                       # rien ne doit rester en cours...
    conn.execute("PRAGMA foreign_keys = OFF")   # ... le pragma serait sinon ignoré
    try:
        conn.execute("BEGIN")
        (avant,) = conn.execute("SELECT COUNT(*) FROM prets").fetchone()

        conn.execute(
            """
            CREATE TABLE prets_nouveau (
                id_pret          INTEGER PRIMARY KEY AUTOINCREMENT,
                id_exemplaire    TEXT NOT NULL,
                numero_pochette  INTEGER,
                date_sortie      TEXT NOT NULL,
                date_retour      TEXT,
                motif            TEXT NOT NULL DEFAULT 'pret',
                FOREIGN KEY (id_exemplaire) REFERENCES exemplaires (id_exemplaire)
            )
            """
        )
        # id_pret est recopié explicitement : la numérotation ne repart pas de
        # zéro et sqlite_sequence se recale sur le maximum inséré.
        conn.execute(
            f"INSERT INTO prets_nouveau ({colonnes}) SELECT {colonnes} FROM prets"
        )

        (apres,) = conn.execute("SELECT COUNT(*) FROM prets_nouveau").fetchone()
        if apres != avant:
            raise RuntimeError(
                f"Migration de `prets` interrompue : {avant} ligne(s) avant, "
                f"{apres} après. Aucune modification n'a été validée."
            )

        # Purge rétroactive : sans elle, tout l'historique déjà constitué
        # resterait en clair (fiche D5). Règle uniforme, sorties tournoi
        # closes comprises — leur marqueur `0` n'est lu nulle part sur une
        # ligne close, c'est `motif` qui porte l'information.
        conn.execute(
            "UPDATE prets_nouveau SET numero_pochette = NULL "
            "WHERE date_retour IS NOT NULL"
        )

        # DROP TABLE emporte aussi les index de `prets` : les recréer ici, car
        # models.SCHEMA_INDEXES a déjà été exécuté (avant les migrations).
        conn.execute("DROP TABLE prets")
        conn.execute("ALTER TABLE prets_nouveau RENAME TO prets")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prets_exemplaire ON prets (id_exemplaire)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prets_retour_null "
            "ON prets (id_exemplaire) WHERE date_retour IS NULL"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    orphelines = conn.execute("PRAGMA foreign_key_check(prets)").fetchall()
    if orphelines:
        raise RuntimeError(
            f"Migration de `prets` : {len(orphelines)} ligne(s) orpheline(s) "
            "détectée(s) après reconstruction."
        )


def _seed_emplacements_rangement(conn: sqlite3.Connection) -> None:
    """
    Premier remplissage de `emplacements_rangement` (idempotent).

    N'insère les emplacements par défaut QUE si la table est vide : ne
    duplique rien si `init_db` est rappelé, et ne recrée jamais une entrée
    qu'un admin aurait supprimée/archivée depuis.
    """
    (nb,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
    if nb:
        return
    conn.executemany(
        "INSERT INTO emplacements_rangement (nom, actif, ordre) VALUES (?, 1, ?)",
        [(nom, ordre) for ordre, nom in enumerate(_EMPLACEMENTS_RANGEMENT_SEED)],
    )
    conn.commit()


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """
    Crée les tables/index manquants et applique les migrations de colonnes.
    Idempotent : sans danger pour les données existantes.

    - ``CREATE ... IF NOT EXISTS`` crée ce qui manque mais ne MODIFIE pas une
      table déjà présente ; d'où l'étape de migration (``_appliquer_migrations``)
      qui ajoute les colonnes apparues après coup.

    Args:
        conn: connexion existante (utile pour les tests, base en mémoire). Si
            ``None``, une connexion est ouverte puis refermée ici.
    """
    own_connection = conn is None  # a-t-on ouvert la connexion nous-mêmes ?
    if own_connection:
        conn = get_connection()
    try:
        for statement in models.SCHEMA_STATEMENTS:
            conn.executescript(statement)
        conn.commit()
        _appliquer_migrations(conn)
        # Après les migrations de colonnes : celle-ci recopie la table `prets`
        # et suppose donc `motif` déjà présent.
        _migrer_pochette_nullable(conn)
        _seed_emplacements_rangement(conn)
    finally:
        # On ne ferme que si on a ouvert : ne pas fermer la connexion du test.
        if own_connection:
            conn.close()


# Exécuté quand on lance « python -m app.db » : initialise la base puis confirme.
if __name__ == "__main__":
    init_db()
    print(f"Base initialisée : {get_database_path()}")
