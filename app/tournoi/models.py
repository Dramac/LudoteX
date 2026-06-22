"""
Schéma de la base SQLite SÉPARÉE des tournois (`data/tournoi.db`).

Comme `app/models.py` pour la base de prêt, ce module ne contient QUE du DDL
(les `CREATE TABLE`/index) sous forme de chaînes SQL ; c'est `app/tournoi/db.py`
qui les exécute. La séparation des deux bases est VOLONTAIRE (voir
docs/conception-tournois.md §2) : aucune clé étrangère ne traverse les bases.

MODÈLE DE DONNÉES (docs/conception-tournois.md §4) — trois tables :

    tournois       un enregistrement par tournoi (infos + état + options).
    inscriptions   participants : pseudo + code de désinscription (PAS d'e-mail).
    rencontres     parties/matchs (alimentées par les modes de scoring, étape
                   suivante ; la table est créée dès maintenant pour stabiliser
                   le schéma).

ÉTATS D'UN TOURNOI (machine à états, §5) :
    brouillon -> inscriptions -> lance -> termine
Le mode de scoring (`mode_scoring`) est choisi AU LANCEMENT, pas à la création.
"""

# Réglages de connexion (identiques à la base de prêt) : intégrité des FK +
# meilleure concurrence d'écriture. Réutilisés par app/tournoi/db.py.
PRAGMAS = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
)

# Valeurs autorisées pour `etat` (machine à états). Exposées pour les services
# et les tests, afin d'éviter les chaînes « magiques » disséminées.
ETATS = ("brouillon", "inscriptions", "lance", "termine")

# ---------------------------------------------------------------------------
# tournois — un enregistrement par tournoi.
# ---------------------------------------------------------------------------
SCHEMA_TOURNOIS = """
CREATE TABLE IF NOT EXISTS tournois (
    id_tournoi            INTEGER PRIMARY KEY AUTOINCREMENT,
    nom                   TEXT NOT NULL,                 -- intitulé du tournoi
    jeu                   TEXT,                          -- jeu concerné (texte libre)
    date_heure            TEXT,                          -- début prévu (ISO 8601 UTC), nullable
    duree_min             INTEGER,                       -- durée approximative (minutes)
    nb_places             INTEGER,                       -- nombre de places (NULL = illimité)
    emplacement           TEXT,                          -- lieu/table
    inscription_en_ligne  INTEGER NOT NULL DEFAULT 1,    -- 0/1 : inscription publique en ligne
    etat                  TEXT NOT NULL DEFAULT 'brouillon', -- brouillon/inscriptions/lance/termine
    mode_scoring          TEXT,                          -- NULL jusqu'au lancement (étape scoring)
    bo3                   INTEGER NOT NULL DEFAULT 0,    -- 0/1 : best of 3 par rencontre
    restriction_nombre    INTEGER,                       -- plafond éventuel (arbre)
    date_creation         TEXT NOT NULL                  -- horodatage de création (ISO 8601 UTC)
);
"""

# ---------------------------------------------------------------------------
# inscriptions — participants. RGPD MINIMAL : pseudo + code, JAMAIS l'e-mail.
# ---------------------------------------------------------------------------
# ON DELETE CASCADE : supprimer un tournoi supprime ses inscriptions (et, via la
# table rencontres, ses parties). foreign_keys = ON rend la cascade effective.
SCHEMA_INSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS inscriptions (
    id_inscription        INTEGER PRIMARY KEY AUTOINCREMENT,
    id_tournoi            INTEGER NOT NULL,              -- FK -> tournois.id_tournoi
    pseudo                TEXT NOT NULL,                 -- nom affiché (saisi par le participant)
    code_desinscription   TEXT NOT NULL,                -- jeton aléatoire (pas d'e-mail !)
    date_inscription      TEXT NOT NULL,                 -- horodatage (ISO 8601 UTC)
    FOREIGN KEY (id_tournoi) REFERENCES tournois (id_tournoi) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# rencontres — parties/matchs (créée dès maintenant ; remplie par les modes de
# scoring à l'étape suivante).
# ---------------------------------------------------------------------------
# participant_b NULL = « bye » (exempt). ON DELETE SET NULL sur les participants
# pour ne pas casser l'historique si une inscription est retirée en cours de
# tournoi (cas limite ; les modes de scoring décideront du traitement).
SCHEMA_RENCONTRES = """
CREATE TABLE IF NOT EXISTS rencontres (
    id_rencontre          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_tournoi            INTEGER NOT NULL,              -- FK -> tournois.id_tournoi
    ronde                 INTEGER,                       -- n° de ronde (NULL en high score)
    participant_a         INTEGER,                       -- FK -> inscriptions.id_inscription
    participant_b         INTEGER,                       -- FK -> inscriptions ; NULL = bye
    score_a               INTEGER,                       -- score (ou manches gagnées si BO3)
    score_b               INTEGER,
    resultat              TEXT,                          -- 'a' / 'b' / 'nul' (gagnant), nullable
    FOREIGN KEY (id_tournoi)    REFERENCES tournois (id_tournoi)        ON DELETE CASCADE,
    FOREIGN KEY (participant_a) REFERENCES inscriptions (id_inscription) ON DELETE SET NULL,
    FOREIGN KEY (participant_b) REFERENCES inscriptions (id_inscription) ON DELETE SET NULL
);
"""

# ---------------------------------------------------------------------------
# Index — requêtes fréquentes.
# ---------------------------------------------------------------------------
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_inscriptions_tournoi ON inscriptions (id_tournoi);
CREATE INDEX IF NOT EXISTS idx_inscriptions_code    ON inscriptions (code_desinscription);
CREATE INDEX IF NOT EXISTS idx_rencontres_tournoi   ON rencontres (id_tournoi);
"""

# Ordre imposé par les FK : tournois (référencé) avant inscriptions/rencontres.
SCHEMA_STATEMENTS = (
    SCHEMA_TOURNOIS,
    SCHEMA_INSCRIPTIONS,
    SCHEMA_RENCONTRES,
    SCHEMA_INDEXES,
)
