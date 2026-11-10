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

MODULE « PROGRAMME DU WEEK-END » (docs/conception-programme.md §4) — deux tables
supplémentaires, DANS CETTE MÊME BASE (`data/tournoi.db`, aucune FK vers la base
de prêt) : le sous-paquet s'appelle `tournoi` mais héberge aussi ce module,
documenté ici plutôt que renommé.

    types_programme   liste configurable des types d'éléments (animation,
                      atelier, initiation…), gérée en admin. Patron identique à
                      `emplacements_rangement` (app/models.py) : archivage doux,
                      jamais de suppression sous un élément déjà saisi.
    programme         les éléments eux-mêmes (animations/ateliers/temps forts),
                      `id_type` nullable et SANS cascade : un type archivé ou
                      supprimé n'efface jamais un élément déjà saisi.

ÉTATS D'UN ÉLÉMENT DE PROGRAMME (§4.2) : brouillon | publie | annule.
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

# États d'un élément de programme (docs/conception-programme.md §4.2) — machine
# à états distincte de celle des tournois (pas d'inscriptions à gérer).
ETATS_PROGRAMME = ("brouillon", "publie", "annule")

# ---------------------------------------------------------------------------
# tournois — un enregistrement par tournoi.
# ---------------------------------------------------------------------------
SCHEMA_TOURNOIS = """
CREATE TABLE IF NOT EXISTS tournois (
    id_tournoi            INTEGER PRIMARY KEY AUTOINCREMENT,
    nom                   TEXT NOT NULL,                 -- intitulé du tournoi
    jeu                   TEXT,                          -- jeu concerné (texte libre)
    age                   TEXT,                          -- âge conseillé (texte libre, ex. « 10+ »)
    date_heure            TEXT,                          -- début prévu (ISO 8601 UTC), nullable
    duree_min             INTEGER,                       -- durée approximative (minutes)
    nb_places             INTEGER,                       -- nombre de places (NULL = illimité)
    emplacement           TEXT,                          -- lieu/table
    inscription_en_ligne  INTEGER NOT NULL DEFAULT 1,    -- 0/1 : inscription publique en ligne
    etat                  TEXT NOT NULL DEFAULT 'brouillon', -- brouillon/inscriptions/lance/termine
    mode_scoring          TEXT,                          -- NULL jusqu'au lancement (étape scoring)
    nb_rondes             INTEGER,                       -- nombre de rondes (ronde suisse) ; NULL sinon
    bo3                   INTEGER NOT NULL DEFAULT 0,    -- 0/1 : best of 3 par rencontre
    par_equipes           INTEGER NOT NULL DEFAULT 0,    -- 0/1 : inscription par ÉQUIPE (nom + membres)
    taille_equipe         INTEGER,                       -- nb de membres attendus par équipe (si par_equipes)
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
    pseudo                TEXT NOT NULL,                 -- pseudo OU nom d'équipe (si tournoi par équipes)
    membres               TEXT,                          -- liste JSON des pseudos membres (tournoi par équipes) ; NULL sinon
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
# types_programme — liste configurable des types d'éléments de programme
# (docs/conception-programme.md §4.1). Patron identique à
# `emplacements_rangement` (app/models.py) : archivage doux (`actif`), jamais de
# suppression sous un élément déjà rattaché. Doit précéder `programme` dans
# SCHEMA_STATEMENTS : celle-ci la référence en FK (nullable, sans cascade).
# ---------------------------------------------------------------------------
SCHEMA_TYPES_PROGRAMME = """
CREATE TABLE IF NOT EXISTS types_programme (
    id_type    INTEGER PRIMARY KEY AUTOINCREMENT,
    nom        TEXT NOT NULL,               -- « Atelier », « Initiation », « Temps fort »…
    icone      TEXT,                        -- emoji facultatif, affiché sur /live et /programme
    actif      INTEGER NOT NULL DEFAULT 1,  -- 0 = archivé (retrait doux)
    ordre      INTEGER NOT NULL DEFAULT 0
);
"""

# ---------------------------------------------------------------------------
# programme — éléments de programme hors tournoi (animation, atelier,
# initiation, temps fort, intervention partenaire — docs/conception-programme.md
# §4.2). `id_type` NULLABLE et SANS cascade : un type archivé/supprimé ne doit
# jamais faire disparaître un élément déjà saisi. Zéro donnée personnelle
# (`jauge` est un nombre indicatif, pas une liste d'inscrits).
# ---------------------------------------------------------------------------
SCHEMA_PROGRAMME = """
CREATE TABLE IF NOT EXISTS programme (
    id_element     INTEGER PRIMARY KEY AUTOINCREMENT,
    intitule       TEXT NOT NULL,              -- ce qui s'affiche en grand
    description    TEXT,                       -- description courte (facultative)
    id_type        INTEGER,                    -- FK -> types_programme, nullable
    date_heure     TEXT,                       -- début, ISO 8601 UTC ; nullable
    duree_min      INTEGER,                    -- durée en minutes (déduite de l'heure de fin saisie)
    lieu           TEXT,                       -- zone ou table dans la salle
    public_vise    TEXT,                       -- ANCIEN texte libre, plus lu ni écrit : repris
                                               -- dans programme_publics (tournoi/db.py), gardé
                                               -- pour qu'un retour de version retrouve ses données
    jauge          INTEGER,                    -- purement informative, aucune réservation
    etat           TEXT NOT NULL DEFAULT 'brouillon',   -- 'brouillon' | 'publie' | 'annule'
    date_creation  TEXT NOT NULL,              -- horodatage de création (ISO 8601 UTC)
    FOREIGN KEY (id_type) REFERENCES types_programme (id_type)
);
"""

# ---------------------------------------------------------------------------
# publics_programme — liste configurable des PUBLICS visés (« Enfants moins de
# 8 ans », « Familles »…), même patron que `types_programme` (archivage doux,
# ordre, suppression refusée tant qu'un élément y est rattaché). Remplace le
# texte libre `programme.public_vise`, qu'on ne pouvait pas filtrer.
#
# programme_publics — liaison PLUSIEURS-À-PLUSIEURS : les publics se
# recouvrent (un atelier tout public convient aux familles comme aux ados), le
# bureau coche donc tous ceux qui conviennent et le filtre public reste un
# simple « porte ce public ». Cascade à la suppression d'un ÉLÉMENT ; aucune
# cascade côté public (la suppression d'un public rattaché est refusée).
# ---------------------------------------------------------------------------
SCHEMA_PUBLICS_PROGRAMME = """
CREATE TABLE IF NOT EXISTS publics_programme (
    id_public  INTEGER PRIMARY KEY AUTOINCREMENT,
    nom        TEXT NOT NULL,               -- « Enfants moins de 8 ans », « Familles »…
    actif      INTEGER NOT NULL DEFAULT 1,  -- 0 = archivé (retrait doux)
    ordre      INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA_PROGRAMME_PUBLICS = """
CREATE TABLE IF NOT EXISTS programme_publics (
    id_element  INTEGER NOT NULL,
    id_public   INTEGER NOT NULL,
    PRIMARY KEY (id_element, id_public),
    FOREIGN KEY (id_element) REFERENCES programme (id_element) ON DELETE CASCADE,
    FOREIGN KEY (id_public) REFERENCES publics_programme (id_public)
);
"""

# ---------------------------------------------------------------------------
# programme_jeux — jeux du catalogue présentés par un élément (une initiation
# « jeux d'ambiance » en montre souvent trois ou quatre). `reference_titre` est
# la clé STABLE du titre dans la base de PRÊT (figée au renommage) : AUCUNE FK,
# l'autre base est hors de portée (invariant des trois bases). C'est la ROUTE
# qui lit les noms dans la base de prêt et les transmet ; une référence dont le
# titre a disparu (jeu vidé par un rattachement puis supprimé) est masquée en
# public et signalée en gestion, jamais une erreur.
# ---------------------------------------------------------------------------
SCHEMA_PROGRAMME_JEUX = """
CREATE TABLE IF NOT EXISTS programme_jeux (
    id_element       INTEGER NOT NULL,
    reference_titre  TEXT NOT NULL,     -- titres.reference_titre de la base de prêt (sans FK)
    PRIMARY KEY (id_element, reference_titre),
    FOREIGN KEY (id_element) REFERENCES programme (id_element) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# Index — requêtes fréquentes.
# ---------------------------------------------------------------------------
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_inscriptions_tournoi ON inscriptions (id_tournoi);
CREATE INDEX IF NOT EXISTS idx_inscriptions_code    ON inscriptions (code_desinscription);
CREATE INDEX IF NOT EXISTS idx_rencontres_tournoi   ON rencontres (id_tournoi);
CREATE INDEX IF NOT EXISTS idx_programme_date_heure ON programme (date_heure);
CREATE INDEX IF NOT EXISTS idx_programme_etat       ON programme (etat);
CREATE INDEX IF NOT EXISTS idx_programme_publics_public ON programme_publics (id_public);
"""

# Ordre imposé par les FK : tournois (référencé) avant inscriptions/rencontres ;
# types_programme (référencé) avant programme.
SCHEMA_STATEMENTS = (
    SCHEMA_TOURNOIS,
    SCHEMA_INSCRIPTIONS,
    SCHEMA_RENCONTRES,
    SCHEMA_TYPES_PROGRAMME,
    SCHEMA_PROGRAMME,
    SCHEMA_PUBLICS_PROGRAMME,
    SCHEMA_PROGRAMME_PUBLICS,
    SCHEMA_PROGRAMME_JEUX,
    SCHEMA_INDEXES,
)
