"""
Schéma de la base SQLite — système de prêt de jeux.

Ce module ne contient QUE la définition du schéma (DDL) sous forme de
chaînes SQL et quelques constantes. L'ouverture de la connexion et
l'initialisation de la base sont dans ``app/db.py``.

Modèle de données (voir docs/specification.md §3) — quatre tables :

    titres        catalogue, niveau « référence » (un par jeu)
    exemplaires   boîtes physiques, niveau « unité prêtable » (un par QR)
    prets         historique complet de tous les prêts (jamais purgé)
    pochettes     occupation du moment des numéros de pochette (recyclés)

Deux clés non négociables et stables, quelles que soient les évolutions
du CSV (voir spec §3.1) :

    id_exemplaire    identifiant unique d'une boîte physique, encodé dans
                     le QR sous forme d'URL /jeu/<id_exemplaire>. Stocké en
                     TEXT pour préserver un éventuel formatage (zéros de
                     tête, ex. "00472") et ne jamais le réinterpréter.
    reference_titre  clé de regroupement des exemplaires d'un même jeu
                     (ex. "CATAN"), base des statistiques par titre.
"""

# ---------------------------------------------------------------------------
# Réglages SQLite recommandés (appliqués à chaque connexion, voir db.py)
# ---------------------------------------------------------------------------
# - foreign_keys = ON  : les clés étrangères ne sont pas vérifiées par défaut
#   sous SQLite ; on les active explicitement.
# - journal_mode = WAL : meilleure concurrence lecture/écriture, utile quand
#   plusieurs bénévoles écrivent en parallèle (quelques écritures/minute).
PRAGMAS = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
)

# ---------------------------------------------------------------------------
# Définition des tables
# ---------------------------------------------------------------------------

# titres — le catalogue, niveau « référence ».
#
# Colonnes de cœur : reference_titre (PK), nom, categorie.
# Colonnes optionnelles courantes pour des jeux de société : toutes
# nullables, l'import CSV remplit ce qu'il trouve. Le CSV n'étant pas figé,
# le schéma pourra évoluer (ajout de colonnes via le script d'import) sans
# remettre en cause les deux clés non négociables.
SCHEMA_TITRES = """
CREATE TABLE IF NOT EXISTS titres (
    reference_titre  TEXT PRIMARY KEY,            -- clé de regroupement (ex. "CATAN")
    nom              TEXT NOT NULL,               -- nom affiché du jeu
    categorie        TEXT,                        -- catégorie pour le filtrage public

    -- Colonnes optionnelles (nullables) — peuvent évoluer librement
    nb_joueurs_min   INTEGER,
    nb_joueurs_max   INTEGER,
    duree_min        INTEGER,                     -- durée approximative en minutes
    age_min          INTEGER,                     -- âge minimum conseillé
    editeur          TEXT
);
"""

# exemplaires — les boîtes physiques, niveau « unité prêtable ».
SCHEMA_EXEMPLAIRES = """
CREATE TABLE IF NOT EXISTS exemplaires (
    id_exemplaire    TEXT PRIMARY KEY,            -- encodé dans le QR
    reference_titre  TEXT NOT NULL,               -- FK -> titres
    FOREIGN KEY (reference_titre) REFERENCES titres (reference_titre)
);
"""

# prets — l'historique complet de tous les prêts (jamais purgé).
#
# Un exemplaire est SORTI s'il possède un prêt dont date_retour est NULL ;
# il est DISPONIBLE sinon. date_retour reste NULL tant que le jeu est sorti.
# numero_pochette est conservé dans l'historique même après le retour
# (sans incidence sur les statistiques).
SCHEMA_PRETS = """
CREATE TABLE IF NOT EXISTS prets (
    id_pret          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_exemplaire    TEXT NOT NULL,               -- FK -> exemplaires
    numero_pochette  INTEGER NOT NULL,            -- numéro attribué pour ce prêt
    date_sortie      TEXT NOT NULL,               -- horodatage ISO 8601 (UTC)
    date_retour      TEXT,                        -- NULL tant que l'exemplaire est sorti
    FOREIGN KEY (id_exemplaire) REFERENCES exemplaires (id_exemplaire)
);
"""

# pochettes — l'occupation du moment (quels numéros sont actuellement pris).
#
# Le numéro commence à 1 ; à chaque prêt on attribue le plus petit numéro
# libre ; il est recyclé au retour. AUCUN plafond : on ne refuse jamais un
# prêt (voir spec §6). Une ligne est créée à la volée lors de la première
# attribution d'un numéro, puis réutilisée (occupe bascule 0/1).
SCHEMA_POCHETTES = """
CREATE TABLE IF NOT EXISTS pochettes (
    numero_pochette  INTEGER PRIMARY KEY,         -- numéro (recyclé)
    occupe           INTEGER NOT NULL DEFAULT 0   -- 0 = libre, 1 = occupé
);
"""

# ---------------------------------------------------------------------------
# Index — accélèrent les requêtes les plus fréquentes
# ---------------------------------------------------------------------------
SCHEMA_INDEXES = """
-- État d'un exemplaire : retrouver vite son éventuel prêt non clos.
CREATE INDEX IF NOT EXISTS idx_prets_exemplaire        ON prets (id_exemplaire);
CREATE INDEX IF NOT EXISTS idx_prets_retour_null
    ON prets (id_exemplaire) WHERE date_retour IS NULL;

-- Statistiques et catalogue : regrouper les exemplaires par titre.
CREATE INDEX IF NOT EXISTS idx_exemplaires_titre       ON exemplaires (reference_titre);

-- Recherche du plus petit numéro de pochette libre.
CREATE INDEX IF NOT EXISTS idx_pochettes_occupe        ON pochettes (occupe);
"""

# Ordre d'exécution : les tables référencées en premier (FK), puis les index.
SCHEMA_STATEMENTS = (
    SCHEMA_TITRES,
    SCHEMA_EXEMPLAIRES,
    SCHEMA_PRETS,
    SCHEMA_POCHETTES,
    SCHEMA_INDEXES,
)
