"""
Schéma de la base SQLite SÉPARÉE du planning bénévole (`data/planning.db`).

Comme `app/models.py` (prêt) et `app/tournoi/models.py` (tournois), ce module ne
contient QUE du DDL (les `CREATE TABLE`/index) sous forme de chaînes SQL ; c'est
`app/planning/db.py` qui les exécute. La base est VOLONTAIREMENT distincte des
deux autres (voir docs/conception-planning.md §3-4) : aucune clé étrangère ne
traverse les bases, le cycle de vie et la purge sont indépendants.

RGPD (docs/conception-planning.md §4) : rupture assumée avec le « zéro donnée
personnelle » du prêt. On stocke des noms, un contact, des disponibilités et des
affectations. La finalité est unique (organiser l'événement) et la base est
purgée après l'événement.

MODÈLE DE DONNÉES (docs/conception-planning.md §5) — huit tables :

    evenements      un planning par édition (état : collecte/brouillon/publie).
    postes          les « colonnes » du tableau (Accueil, Bar, Ludothèque…).
    creneaux        la trame horaire (créneaux de poste + tâches ponctuelles).
    besoins         nombre de personnes requis par (créneau × poste).
    benevoles       répondants (nom, contact, plafond d'heures…).
    disponibilites  par (bénévole × créneau) : disponible ou non.
    preferences     par (bénévole × poste) : prefere/ok/si_vraiment/surtout_pas.
    affectations    le planning lui-même (qui, quel créneau, quel poste).

ÉTATS D'UN ÉVÉNEMENT (machine à états, §9) :
    collecte -> brouillon -> publie
"""

# Réglages de connexion (identiques aux autres bases) : intégrité des FK +
# meilleure concurrence d'écriture. Réutilisés par app/planning/db.py.
PRAGMAS = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
)

# Valeurs autorisées pour `evenements.etat` (machine à états). Exposées pour les
# services et les tests, afin d'éviter les chaînes « magiques » disséminées.
ETATS = ("collecte", "brouillon", "publie")

# Niveaux de préférence d'un bénévole pour un poste (§5, §7). Ordonnés du plus
# souhaité au moins souhaité ; `surtout_pas` est une contrainte DURE (exclusion).
NIVEAUX_PREFERENCE = ("prefere", "ok", "si_vraiment", "surtout_pas")

# Types de créneau : un créneau « poste » se remplit par poste (avec besoins) ;
# un créneau « tache » (installation/rangement) regroupe des bénévoles sans poste.
TYPES_CRENEAU = ("poste", "tache")

# ---------------------------------------------------------------------------
# evenements — un enregistrement par édition de l'événement.
# ---------------------------------------------------------------------------
SCHEMA_EVENEMENTS = """
CREATE TABLE IF NOT EXISTS evenements (
    id_evenement   INTEGER PRIMARY KEY AUTOINCREMENT,
    nom            TEXT NOT NULL,                       -- ex. « Festival 2026 »
    etat           TEXT NOT NULL DEFAULT 'collecte',    -- collecte/brouillon/publie
    date_creation  TEXT NOT NULL                        -- horodatage (ISO 8601 UTC)
);
"""

# ---------------------------------------------------------------------------
# postes — les « colonnes » du tableau de service.
# ---------------------------------------------------------------------------
SCHEMA_POSTES = """
CREATE TABLE IF NOT EXISTS postes (
    id_poste            INTEGER PRIMARY KEY AUTOINCREMENT,
    id_evenement        INTEGER NOT NULL,               -- FK -> evenements
    nom                 TEXT NOT NULL,                  -- Accueil, Bar, Ludothèque…
    demande_experience  INTEGER NOT NULL DEFAULT 0,     -- 0/1 (info, exploité en phase 2)
    ordre               INTEGER NOT NULL DEFAULT 0,     -- ordre d'affichage des colonnes
    FOREIGN KEY (id_evenement) REFERENCES evenements (id_evenement) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# creneaux — la trame horaire. `debut`/`fin` en ISO 8601 UTC (durée déduite).
# `type` = 'poste' (rempli par poste) ou 'tache' (sans poste : installation…).
# ---------------------------------------------------------------------------
SCHEMA_CRENEAUX = """
CREATE TABLE IF NOT EXISTS creneaux (
    id_creneau     INTEGER PRIMARY KEY AUTOINCREMENT,
    id_evenement   INTEGER NOT NULL,                    -- FK -> evenements
    libelle_jour   TEXT NOT NULL,                       -- ex. « Samedi », « Dimanche »
    debut          TEXT NOT NULL,                       -- début (ISO 8601 UTC)
    fin            TEXT NOT NULL,                        -- fin   (ISO 8601 UTC)
    type           TEXT NOT NULL DEFAULT 'poste',       -- 'poste' / 'tache'
    libelle        TEXT,                                -- libellé d'une tâche (ex. « Installation »)
    ordre          INTEGER NOT NULL DEFAULT 0,          -- ordre d'affichage des lignes
    FOREIGN KEY (id_evenement) REFERENCES evenements (id_evenement) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# besoins — nombre de personnes requis par (créneau × poste). 0 = case grisée.
# ---------------------------------------------------------------------------
SCHEMA_BESOINS = """
CREATE TABLE IF NOT EXISTS besoins (
    id_creneau   INTEGER NOT NULL,                      -- FK -> creneaux
    id_poste     INTEGER NOT NULL,                      -- FK -> postes
    nb_requis    INTEGER NOT NULL DEFAULT 0,            -- 0 = pas de besoin (grisé)
    PRIMARY KEY (id_creneau, id_poste),
    FOREIGN KEY (id_creneau) REFERENCES creneaux (id_creneau) ON DELETE CASCADE,
    FOREIGN KEY (id_poste)   REFERENCES postes (id_poste)     ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# benevoles — répondants au formulaire de souhaits. `code_modif` permet de
# rouvrir sa réponse tant que la collecte est ouverte.
# ---------------------------------------------------------------------------
SCHEMA_BENEVOLES = """
CREATE TABLE IF NOT EXISTS benevoles (
    id_benevole   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_evenement  INTEGER NOT NULL,                     -- FK -> evenements
    nom           TEXT NOT NULL,                        -- nom ou pseudo affiché
    contact       TEXT,                                 -- e-mail/téléphone (nullable, §4)
    max_heures    REAL,                                 -- plafond d'heures (NULL = pas de plafond)
    note          TEXT,                                 -- mot libre
    code_modif    TEXT NOT NULL,                        -- jeton aléatoire (édition de la réponse)
    date_reponse  TEXT NOT NULL,                        -- horodatage (ISO 8601 UTC)
    FOREIGN KEY (id_evenement) REFERENCES evenements (id_evenement) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# disponibilites — par (bénévole × créneau). On ne stocke que les créneaux où le
# bénévole s'est positionné ; absent = non disponible.
# ---------------------------------------------------------------------------
SCHEMA_DISPONIBILITES = """
CREATE TABLE IF NOT EXISTS disponibilites (
    id_benevole  INTEGER NOT NULL,                      -- FK -> benevoles
    id_creneau   INTEGER NOT NULL,                      -- FK -> creneaux
    disponible   INTEGER NOT NULL DEFAULT 1,            -- 0/1
    PRIMARY KEY (id_benevole, id_creneau),
    FOREIGN KEY (id_benevole) REFERENCES benevoles (id_benevole) ON DELETE CASCADE,
    FOREIGN KEY (id_creneau)  REFERENCES creneaux (id_creneau)   ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# preferences — par (bénévole × poste). Absent = poste non renseigné (neutre).
# ---------------------------------------------------------------------------
SCHEMA_PREFERENCES = """
CREATE TABLE IF NOT EXISTS preferences (
    id_benevole  INTEGER NOT NULL,                      -- FK -> benevoles
    id_poste     INTEGER NOT NULL,                      -- FK -> postes
    niveau       TEXT NOT NULL,                         -- prefere/ok/si_vraiment/surtout_pas
    PRIMARY KEY (id_benevole, id_poste),
    FOREIGN KEY (id_benevole) REFERENCES benevoles (id_benevole) ON DELETE CASCADE,
    FOREIGN KEY (id_poste)    REFERENCES postes (id_poste)       ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# affectations — le planning. Une ligne = un bénévole placé sur un créneau (et un
# poste pour les créneaux de type 'poste' ; id_poste NULL pour les tâches).
# `verrouille` : l'admin fige la case, le re-préremplissage la conserve.
# `origine` : 'auto' (généré) / 'manuel' (posé par l'admin).
# ---------------------------------------------------------------------------
SCHEMA_AFFECTATIONS = """
CREATE TABLE IF NOT EXISTS affectations (
    id_affectation  INTEGER PRIMARY KEY AUTOINCREMENT,
    id_creneau      INTEGER NOT NULL,                   -- FK -> creneaux
    id_poste        INTEGER,                            -- FK -> postes ; NULL pour une tâche
    id_benevole     INTEGER NOT NULL,                   -- FK -> benevoles
    verrouille      INTEGER NOT NULL DEFAULT 0,         -- 0/1
    origine         TEXT NOT NULL DEFAULT 'auto',       -- 'auto' / 'manuel'
    FOREIGN KEY (id_creneau)  REFERENCES creneaux (id_creneau)   ON DELETE CASCADE,
    FOREIGN KEY (id_poste)    REFERENCES postes (id_poste)       ON DELETE CASCADE,
    FOREIGN KEY (id_benevole) REFERENCES benevoles (id_benevole) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# Index — requêtes fréquentes (par événement, et jointures du planning).
# ---------------------------------------------------------------------------
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_postes_evenement      ON postes (id_evenement);
CREATE INDEX IF NOT EXISTS idx_creneaux_evenement    ON creneaux (id_evenement);
CREATE INDEX IF NOT EXISTS idx_benevoles_evenement   ON benevoles (id_evenement);
CREATE INDEX IF NOT EXISTS idx_benevoles_code        ON benevoles (code_modif);
CREATE INDEX IF NOT EXISTS idx_besoins_creneau       ON besoins (id_creneau);
CREATE INDEX IF NOT EXISTS idx_dispos_benevole       ON disponibilites (id_benevole);
CREATE INDEX IF NOT EXISTS idx_prefs_benevole        ON preferences (id_benevole);
CREATE INDEX IF NOT EXISTS idx_affectations_creneau  ON affectations (id_creneau);
CREATE INDEX IF NOT EXISTS idx_affectations_benevole ON affectations (id_benevole);
"""

# Ordre imposé par les FK : evenements d'abord, puis postes/creneaux, puis le
# reste qui les référence.
SCHEMA_STATEMENTS = (
    SCHEMA_EVENEMENTS,
    SCHEMA_POSTES,
    SCHEMA_CRENEAUX,
    SCHEMA_BESOINS,
    SCHEMA_BENEVOLES,
    SCHEMA_DISPONIBILITES,
    SCHEMA_PREFERENCES,
    SCHEMA_AFFECTATIONS,
    SCHEMA_INDEXES,
)
