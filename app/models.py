"""
Schéma de la base SQLite — définition des tables et index (le « modèle »).

Ce module ne contient QUE du DDL (Data Definition Language) sous forme de
chaînes SQL, plus quelques constantes. Il n'ouvre aucune connexion : c'est
``app/db.py`` qui exécute ces instructions. Cette séparation permet de relire le
schéma d'un coup d'œil, sans logique parasite.

MODÈLE DE DONNÉES (voir docs/specification.md §3) — quatre tables :

    titres        catalogue, niveau « référence » : un enregistrement par JEU.
    exemplaires   boîtes physiques, niveau « unité prêtable » : un par QR.
    prets         historique complet de tous les prêts (jamais purgé).
    pochettes     occupation du moment des numéros de pochette (recyclés).

LES DEUX CLÉS NON NÉGOCIABLES (stables même si le CSV évolue, voir spec §3.1) :

    id_exemplaire    identifiant unique d'une boîte physique. C'est ce que le QR
                     encode (URL /jeu/<id_exemplaire>). Stocké en TEXT pour
                     préserver un éventuel formatage (zéros de tête, ex. "00472")
                     et ne jamais le réinterpréter comme un entier.
    reference_titre  clé de regroupement des exemplaires d'un même jeu
                     (ex. "CATAN"), socle des statistiques par titre.

COMMENT FAIRE ÉVOLUER LE SCHÉMA
-------------------------------
- Ajouter une colonne optionnelle à `titres` : l'ajouter ici (nullable) ET dans
  l'INSERT de scripts/import_csv.py. Les `CREATE TABLE` étant en IF NOT EXISTS,
  pensez à une migration (ALTER TABLE) pour les bases déjà créées.
- Ne jamais renommer/retyper `id_exemplaire` ni `reference_titre`.
"""

# ---------------------------------------------------------------------------
# PRAGMA appliqués à CHAQUE connexion (depuis app/db.get_connection)
# ---------------------------------------------------------------------------
# SQLite ne vérifie pas les clés étrangères par défaut et utilise un journal de
# transactions classique. On corrige les deux :
#   - foreign_keys = ON  : refuse d'insérer un exemplaire/prêt orphelin.
#   - journal_mode = WAL : "Write-Ahead Logging", meilleure concurrence
#     lecture/écriture (plusieurs bénévoles écrivent quasi simultanément).
PRAGMAS = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
)

# ---------------------------------------------------------------------------
# titres — le catalogue, niveau « référence » (un par jeu).
# ---------------------------------------------------------------------------
# Colonnes de cœur : reference_titre (PK), nom, categorie.
# Colonnes optionnelles : toutes NULLABLES ; l'import CSV remplit ce qu'il
# trouve, et le schéma peut s'enrichir sans toucher aux deux clés stables.
SCHEMA_TITRES = """
CREATE TABLE IF NOT EXISTS titres (
    reference_titre  TEXT PRIMARY KEY,            -- clé de regroupement (slug du nom, ex. "CATAN")
    nom              TEXT NOT NULL,               -- nom d'affichage du jeu
    type_jeu         TEXT,                        -- "Jeu" ou "Extension" (CSV "Type")
    categorie        TEXT,                        -- catégorie pour le filtrage public (CSV "Type jeu")

    -- Colonnes optionnelles (nullables). NULL = information absente du CSV.
    nb_joueurs_min   INTEGER,                     -- ex. "2 - 4" -> 2
    nb_joueurs_max   INTEGER,                     -- ex. "2 - 4" -> 4
    duree_min        INTEGER,                     -- durée en minutes (borne basse si plage)
    age_min          INTEGER,                     -- âge minimum conseillé (ex. "10 +" -> 10)
    editeur          TEXT,                        -- CSV "Marque"
    auteur           TEXT,                        -- CSV "Auteur"
    annee_edition    INTEGER,                     -- CSV "Année édition"
    descriptif       TEXT                         -- CSV "Descriptif" (affiché sur la fiche)
);
"""

# ---------------------------------------------------------------------------
# exemplaires — les boîtes physiques, niveau « unité prêtable » (un par QR).
# ---------------------------------------------------------------------------
SCHEMA_EXEMPLAIRES = """
CREATE TABLE IF NOT EXISTS exemplaires (
    id_exemplaire    TEXT PRIMARY KEY,            -- encodé dans le QR ; TEXT (zéros de tête)
    reference_titre  TEXT NOT NULL,               -- FK -> titres.reference_titre
    FOREIGN KEY (reference_titre) REFERENCES titres (reference_titre)
);
"""

# ---------------------------------------------------------------------------
# prets — l'historique complet de tous les prêts (jamais purgé).
# ---------------------------------------------------------------------------
# Un exemplaire est SORTI s'il possède une ligne avec date_retour NULL,
# DISPONIBLE sinon (état déduit, pas stocké). numero_pochette est conservé même
# après retour (trace historique, sans incidence sur les statistiques).
SCHEMA_PRETS = """
CREATE TABLE IF NOT EXISTS prets (
    id_pret          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_exemplaire    TEXT NOT NULL,               -- FK -> exemplaires.id_exemplaire
    numero_pochette  INTEGER NOT NULL,            -- numéro attribué pour ce prêt
    date_sortie      TEXT NOT NULL,               -- horodatage ISO 8601 (UTC)
    date_retour      TEXT,                        -- NULL tant que l'exemplaire est sorti
    FOREIGN KEY (id_exemplaire) REFERENCES exemplaires (id_exemplaire)
);
"""

# ---------------------------------------------------------------------------
# pochettes — occupation DU MOMENT des numéros (recyclés, sans plafond).
# ---------------------------------------------------------------------------
# Une ligne est créée à la première attribution d'un numéro, puis réutilisée :
# `occupe` bascule 1 (prêt) / 0 (retour). On attribue toujours le plus petit
# numéro libre (voir services.plus_petit_numero_libre).
SCHEMA_POCHETTES = """
CREATE TABLE IF NOT EXISTS pochettes (
    numero_pochette  INTEGER PRIMARY KEY,         -- numéro (recyclé)
    occupe           INTEGER NOT NULL DEFAULT 0   -- 0 = libre, 1 = occupé
);
"""

# ---------------------------------------------------------------------------
# parametres — réglages applicatifs (clé/valeur), ex. hash du mot de passe admin.
# ---------------------------------------------------------------------------
# Table générique pour stocker des réglages persistants modifiables depuis
# l'application (sans toucher au .env ni au code). Aujourd'hui : "admin_hash".
SCHEMA_PARAMETRES = """
CREATE TABLE IF NOT EXISTS parametres (
    cle     TEXT PRIMARY KEY,   -- ex. "admin_hash"
    valeur  TEXT                -- valeur associée (ex. hash pbkdf2 du mot de passe)
);
"""

# ---------------------------------------------------------------------------
# Index — accélèrent les requêtes les plus fréquentes.
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

# Ordre d'exécution imposé par les clés étrangères : les tables référencées
# (titres, exemplaires) AVANT celles qui les référencent, puis les index.
SCHEMA_STATEMENTS = (
    SCHEMA_TITRES,
    SCHEMA_EXEMPLAIRES,
    SCHEMA_PRETS,
    SCHEMA_POCHETTES,
    SCHEMA_PARAMETRES,
    SCHEMA_INDEXES,
)
