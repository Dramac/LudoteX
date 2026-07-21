"""
Peuplement de données de démonstration pour le MODE FORMATION.

Voir `docs/mode-formation.md` pour le principe général : une SECONDE INSTANCE
de l'application (mêmes code, même dépôt), avec ses propres bases SQLite
jetables, exposée sur un sous-domaine séparé (ex. formation.<domaine>) ou, en
local, sur un second port (voir `lancer.py --formation`).

DIFFÉRENCE AVEC `app/planning/demo.py`
---------------------------------------
La démo du planning AJOUTE un événement de démonstration sans toucher à
l'existant. Ce script-ci est au contraire volontairement IDEMPOTENT AU SENS
FORT : il VIDE d'abord les données déjà présentes des bases ciblées puis
repeuple — pratique pour repartir d'un état propre entre deux sessions de
formation. Relancer le script produit toujours le même TYPE d'état final (les
noms de jeux et l'ordre des tirages aléatoires peuvent varier d'une passe à
l'autre, cf. ci-dessous).

DONNÉES CRÉÉES (trois bases de l'instance courante)
---------------------------------------------------
- PRÊT : ~60 jeux dont les noms sont tirés AU HASARD du vrai catalogue de
  l'association (lecture seule ; repli sur une liste intégrée de jeux connus si
  le catalogue n'est pas accessible). Des noms réels rendent la formation plus
  parlante qu'une suite « Jeu d'essai n°… » ; l'isolation reste assurée par la
  base jetable et le bandeau « SITE DE FORMATION », pas par le libellé des jeux.
  Les prêts sont DATÉS pour simuler un événement en cours depuis plusieurs
  heures : quelques dizaines de prêts terminés aux durées variées (~15 min à
  ~2 h) répartis dans le temps, plus une douzaine de prêts encore en cours — de
  quoi peupler des statistiques crédibles (palmarès, histogramme horaire, durée
  moyenne, jeux actuellement sortis) pour une démonstration au bureau.
- TOURNOIS : plusieurs tournois d'exemple couvrant les états et les modes de
  scoring (brouillon, inscriptions ouvertes, par équipes, high score en cours,
  ronde suisse en cours, élimination directe, tournoi terminé avec classement).
- PLANNING BÉNÉVOLE : un planning prérempli complet (postes, créneaux, ~28
  bénévoles fictifs, préremplissage) réutilisant la démo du planning, plus un
  jumeau resté « collecte ouverte ».

SÉCURITÉ — À LIRE AVANT DE LANCER CE SCRIPT
--------------------------------------------
Ce script écrit dans les bases pointées par les variables d'environnement
courantes (`DATABASE_PATH`, `TOURNOI_DATABASE_PATH`, `PLANNING_DATABASE_PATH`),
et COMMENCE PAR TOUT SUPPRIMER dans ces bases. Il n'y a AUCUNE garde technique
ici : la protection vient entièrement de l'isolation de l'instance de formation
(ses propres bases jetables, jamais celles de production) — ne JAMAIS lancer ce
script en pointant vers les bases de production.

La LECTURE du vrai catalogue (pour en tirer des noms de jeux) se fait en
lecture seule, sur la base pointée par `FORMATION_SOURCE_DB` si définie, sinon
sur le chemin de production par défaut (`data/pret-jeux.db`). Elle ne modifie
jamais cette base ; en cas d'échec, on retombe sur une liste de noms intégrée.

USAGE
-----
    python -m app.formation
"""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import db as pret_db
from app import services
from app.planning import db as planning_db
from app.planning import demo as planning_demo
from app.planning import services as planning_services
from app.services import FUSEAU_LOCAL, local_vers_utc_iso, slug_titre
from app.tournoi import db as tournoi_db
from app.tournoi import services as tournoi_services

# Catalogue de formation volontairement fourni, pour que les STATISTIQUES
# ressemblent à un vrai événement en cours (démo crédible pour le bureau).
NB_JEUX = 60
# Prêts EN COURS (exemplaires actuellement sortis, pochette attribuée).
NB_PRETS_EN_COURS = 12
# Simule un événement démarré il y a plusieurs heures.
DUREE_EVENEMENT_H = 5
# Durées de prêt (minutes) parcourues en boucle : un panachage réaliste allant
# du prêt éclair (~15-20 min) à la longue partie (~2 h), en passant par ~1 h.
_DUREES_PRET_MIN = [17, 20, 30, 45, 60, 60, 75, 90, 105, 120]
# Nombre d'exemplaires (parmi les prêts terminés) prêtés DEUX fois, pour un
# palmarès plus contrasté (certains titres ressortent nettement).
NB_TITRES_DOUBLE_PRET = 12

# Pseudos fictifs pour les inscriptions de tournoi (assez pour le plus gros).
_PSEUDOS = [
    "Alice", "Bruno", "Camille", "David", "Élodie", "Farid", "Gaëlle", "Hugo",
    "Inès", "Jules", "Karim", "Léa",
]

# Équipes fictives (nom d'équipe + membres) pour le tournoi par équipes.
_EQUIPES = [
    ("Les Renards", ["Alice", "Bruno"]),
    ("Les Hiboux", ["Camille", "David"]),
    ("Les Loutres", ["Élodie", "Farid"]),
    ("Les Blaireaux", ["Gaëlle", "Hugo"]),
]

# Liste de SECOURS : jeux de société connus, utilisée si le vrai catalogue
# n'est pas accessible (ex. sur le VPS de formation sans accès au catalogue de
# prod). Uniquement des titres génériques et bien réels.
_NOMS_SECOURS = [
    "Catan", "Carcassonne", "Dixit", "Les Aventuriers du Rail", "7 Wonders",
    "Pandemic", "Azul", "Splendor", "Ticket to Ride", "Cluedo", "Monopoly",
    "Risk", "Time's Up", "Dobble", "Uno", "Jungle Speed", "Concept",
    "Codenames", "Blokus", "Qwirkle", "Kingdomino", "Takenoko", "Patchwork",
    "Small World", "Terraforming Mars", "Wingspan", "Everdell", "Skull King",
    "Loup-Garou", "Perudo", "Abalone", "Puissance 4", "Les Colons",
    "Mysterium", "6 qui prend", "Love Letter", "Gloomhaven", "Scythe",
    "Root", "Brass", "7 Wonders Duel", "Agricola", "Caverna", "Puerto Rico",
    "Stone Age", "Les Loups-Garous de Thiercelieux", "Dominion", "Hanabi",
    "The Crew", "Cascadia", "It's a Wonderful World", "Kingdom Builder",
    "Bohnanza", "Citadelles", "Les Bâtisseurs", "Sushi Go", "Karuba",
    "Machi Koro", "Coup", "The Mind", "Onitama", "Santorini", "Tsuro",
    "Draftosaurus", "Trekking", "Cartographers", "Res Arcana", "Barrage",
    "Ark Nova", "Le Roi des Nains", "Nidavellir", "Marrakech", "Sagrada",
]


# ---------------------------------------------------------------------------
# Noms de jeux tirés du vrai catalogue
# ---------------------------------------------------------------------------
def _chemin_catalogue_source() -> Path:
    """
    Chemin de la base de PRÊT à lire pour en tirer des noms de jeux réels.

    `FORMATION_SOURCE_DB` (si définie) prime — utile pour pointer explicitement
    vers le catalogue de production. Sinon on retombe sur le chemin de prod par
    défaut (`data/pret-jeux.db`), ce qui suffit en local (un seul dépôt).
    IMPORTANT : on n'utilise PAS `get_database_path()`, qui renverrait la base
    de formation elle-même (celle qu'on est en train de vider).
    """
    return Path(os.getenv("FORMATION_SOURCE_DB") or pret_db.DEFAULT_DATABASE_PATH)


def _lire_noms_catalogue_source() -> list[str]:
    """Noms de titres du vrai catalogue (lecture seule). [] si indisponible."""
    chemin = _chemin_catalogue_source()
    if not chemin.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
        try:
            lignes = conn.execute("SELECT nom FROM titres").fetchall()
        finally:
            conn.close()
        return [ligne[0] for ligne in lignes if ligne and ligne[0]]
    except sqlite3.Error:
        # Base absente, illisible ou schéma inattendu : on retombe sur le repli.
        return []


def noms_jeux_formation(n: int) -> list[str]:
    """
    `n` noms de jeux DISTINCTS (par slug) pour peupler la base de formation.

    Tirés au hasard du vrai catalogue quand il est accessible, complétés au
    besoin par la liste de secours, puis — cas extrême — par des libellés
    numérotés. Toujours exactement `n` noms, tous distincts au sens du
    `reference_titre` (slug), pour ne jamais fusionner deux titres à l'import.
    """
    candidats = _lire_noms_catalogue_source()
    random.shuffle(candidats)
    secours = list(_NOMS_SECOURS)
    random.shuffle(secours)
    candidats += secours

    choisis: list[str] = []
    slugs_vus: set[str] = set()
    for nom in candidats:
        s = slug_titre(nom)
        if s and s not in slugs_vus:
            slugs_vus.add(s)
            choisis.append(nom)
            if len(choisis) >= n:
                return choisis

    # Dernier recours : compléter avec des libellés numérotés distincts.
    i = 1
    while len(choisis) < n:
        nom = f"Jeu d'essai n°{i}"
        i += 1
        s = slug_titre(nom)
        if s not in slugs_vus:
            slugs_vus.add(s)
            choisis.append(nom)
    return choisis


def _date_locale_dans(minutes: int) -> str | None:
    """Horodatage UTC ISO correspondant à « maintenant + `minutes` » (heure locale)."""
    dt = datetime.now(FUSEAU_LOCAL) + timedelta(minutes=minutes)
    return local_vers_utc_iso(dt.strftime("%Y-%m-%dT%H:%M"))


# ---------------------------------------------------------------------------
# Base de PRÊT
# ---------------------------------------------------------------------------
def _vider_base_pret(conn: sqlite3.Connection) -> None:
    """Supprime toutes les lignes des 4 tables de la base de prêt (schéma conservé)."""
    for table in ("prets", "pochettes", "exemplaires", "titres"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def peupler_pret(conn: sqlite3.Connection, noms: list[str] | None = None) -> dict:
    """
    Vide puis repeuple la base de PRÊT avec des jeux (noms réels tirés du
    catalogue) et quelques prêts (en cours et terminés).

    Args:
        noms: liste de noms de jeux à utiliser (au moins `NB_JEUX`). Si None,
            elle est tirée par `noms_jeux_formation`.

    Returns:
        Résumé {"jeux": n, "prets_en_cours": n, "prets_termines": n}.
    """
    _vider_base_pret(conn)

    if noms is None:
        noms = noms_jeux_formation(NB_JEUX)

    ids_exemplaires = []
    for nom in noms[:NB_JEUX]:
        resultat = services.creer_jeu(
            conn, nom,
            type_jeu="Jeu", categorie="Formation",
            nb_joueurs_min=2, nb_joueurs_max=6, duree_min=30, age_min=8,
            descriptif=("Fiche FICTIVE créée par le mode formation — sans "
                        "rapport avec le vrai exemplaire du même nom."),
        )
        ids_exemplaires.append(resultat["id_exemplaire"])

    prets_termines = _peupler_prets_dates(conn, ids_exemplaires)

    return {
        "jeux": len(ids_exemplaires),
        "prets_en_cours": NB_PRETS_EN_COURS,
        "prets_termines": prets_termines,
    }


def _iso_utc(dt: datetime) -> str:
    """Horodatage UTC ISO à la seconde (même format que `services.maintenant`)."""
    return dt.replace(microsecond=0).isoformat()


def _peupler_prets_dates(conn: sqlite3.Connection, ids_exemplaires: list[str]) -> int:
    """
    Insère des prêts DATÉS pour simuler un événement en cours depuis plusieurs
    heures : quelques dizaines de prêts terminés aux durées variées (~15 min à
    ~2 h) répartis sur la durée de l'événement, plus des prêts encore en cours.

    On écrit directement dans `prets`/`pochettes` (plutôt que `services.preter`,
    qui horodate « maintenant ») pour maîtriser les dates. Règles respectées :
    - un prêt terminé porte `date_retour` et `numero_pochette` NULL (cf. D5) ;
    - un prêt en cours occupe une pochette (numéro attribué, `occupe = 1`).
    Les exemplaires « en cours » et « terminés » sont disjoints : aucun
    exemplaire n'est à la fois sorti et porteur d'un historique qui chevauche.

    Returns:
        Le nombre de prêts TERMINÉS créés.
    """
    maintenant_utc = datetime.now(timezone.utc).replace(microsecond=0)
    debut_evenement = maintenant_utc - timedelta(hours=DUREE_EVENEMENT_H)

    # --- Prêts EN COURS : les premiers exemplaires, sortis dans les 2 dernières
    #     heures, chacun sur sa pochette. ---
    en_cours = ids_exemplaires[:NB_PRETS_EN_COURS]
    for numero, id_ex in enumerate(en_cours, start=1):
        sortie = maintenant_utc - timedelta(minutes=random.randint(10, 120))
        conn.execute(
            "INSERT INTO pochettes (numero_pochette, occupe) VALUES (?, 1)",
            (numero,),
        )
        conn.execute(
            "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, "
            "date_retour, motif) VALUES (?, ?, ?, NULL, 'pret')",
            (id_ex, numero, _iso_utc(sortie)),
        )

    # --- Prêts TERMINÉS : chaque exemplaire restant en a un ; les premiers en
    #     ont un second (plus tôt) pour un palmarès contrasté. ---
    restants = ids_exemplaires[NB_PRETS_EN_COURS:]
    a_prêter = list(restants) + restants[:NB_TITRES_DOUBLE_PRET]

    for k, id_ex in enumerate(a_prêter):
        duree = timedelta(minutes=_DUREES_PRET_MIN[k % len(_DUREES_PRET_MIN)])
        # Départ tiré au hasard dans une fenêtre telle que le retour tombe avant
        # « maintenant » (le prêt est bien terminé).
        dernier_depart = maintenant_utc - duree
        span = max(0.0, (dernier_depart - debut_evenement).total_seconds())
        sortie = debut_evenement + timedelta(seconds=random.random() * span)
        retour = sortie + duree
        conn.execute(
            "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, "
            "date_retour, motif) VALUES (?, NULL, ?, ?, 'pret')",
            (id_ex, _iso_utc(sortie), _iso_utc(retour)),
        )

    conn.commit()
    return len(a_prêter)


# ---------------------------------------------------------------------------
# Base des TOURNOIS
# ---------------------------------------------------------------------------
def _vider_base_tournoi(conn: sqlite3.Connection) -> None:
    """Supprime toutes les lignes des 3 tables de la base des tournois."""
    for table in ("rencontres", "inscriptions", "tournois"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def _inscrire_plusieurs(conn: sqlite3.Connection, id_tournoi: int, n: int) -> int:
    """Inscrit `n` pseudos fictifs distincts ; renvoie le nombre inscrit."""
    for pseudo in _PSEUDOS[:n]:
        tournoi_services.inscrire(conn, id_tournoi, pseudo)
    return n


def _scores_high_score(conn: sqlite3.Connection, id_tournoi: int) -> None:
    """Attribue des scores décroissants aux inscrits (pour un classement lisible)."""
    inscrits = tournoi_services.lister_inscriptions(conn, id_tournoi)
    scores = {row["id_inscription"]: max(0, 100 - 15 * i)
              for i, row in enumerate(inscrits)}
    tournoi_services.enregistrer_scores_high_score(conn, id_tournoi, scores)


def _tournoi_lance(conn: sqlite3.Connection, nom: str, jeu: str, n: int,
                   mode: str, nb_rondes: int | None = None) -> int:
    """Crée un tournoi, l'ouvre, inscrit `n` joueurs, puis le lance en `mode`."""
    id_t = tournoi_services.creer_tournoi(
        conn, nom, jeu=jeu, age="tout public",
        nb_places=max(8, n), duree_min=60, date_heure=_date_locale_dans(0),
    )
    tournoi_services.changer_etat(conn, id_t, "inscriptions")
    _inscrire_plusieurs(conn, id_t, n)
    tournoi_services.lancer_tournoi(conn, id_t, mode, nb_rondes=nb_rondes)
    return id_t


def peupler_tournoi(conn: sqlite3.Connection, noms: list[str] | None = None) -> dict:
    """
    Vide puis repeuple la base des TOURNOIS avec plusieurs tournois d'exemple
    couvrant les différents états et modes de scoring.

    Args:
        noms: noms de jeux à réutiliser pour les intitulés (mêmes que le
            catalogue de formation). Si None, ils sont tirés à part.

    Returns:
        Résumé {"tournois": n, "inscrits": n}.
    """
    _vider_base_tournoi(conn)

    if noms is None:
        noms = noms_jeux_formation(NB_JEUX)
    jeux = random.sample(noms, min(len(noms), 7))

    def jeu(i: int) -> str:
        return jeux[i % len(jeux)]

    nb_tournois = 0
    total_inscrits = 0

    # 1. Brouillon (pas encore ouvert aux inscriptions).
    tournoi_services.creer_tournoi(
        conn, f"Tournoi {jeu(0)} (brouillon)", jeu=jeu(0),
        age="tout public", nb_places=8, duree_min=90,
    )
    nb_tournois += 1

    # 2. Inscriptions ouvertes, imminent (apparaît sur l'accueil et /live).
    id_t = tournoi_services.creer_tournoi(
        conn, f"Tournoi {jeu(1)} — inscriptions ouvertes", jeu=jeu(1),
        age="10+", nb_places=8, duree_min=60, date_heure=_date_locale_dans(45),
    )
    tournoi_services.changer_etat(conn, id_t, "inscriptions")
    total_inscrits += _inscrire_plusieurs(conn, id_t, 5)
    nb_tournois += 1

    # 3. Par équipes, inscriptions ouvertes.
    id_t = tournoi_services.creer_tournoi(
        conn, f"Tournoi par équipes — {jeu(2)}", jeu=jeu(2),
        age="tout public", nb_places=8, duree_min=60,
        par_equipes=True, taille_equipe=2, date_heure=_date_locale_dans(180),
    )
    tournoi_services.changer_etat(conn, id_t, "inscriptions")
    for nom_equipe, membres in _EQUIPES:
        tournoi_services.inscrire(conn, id_t, nom_equipe, membres=membres)
        total_inscrits += 1
    nb_tournois += 1

    # 4. High score en cours (avec scores saisis).
    id_t = _tournoi_lance(conn, f"Tournoi {jeu(3)} — high score", jeu(3),
                          n=5, mode="high_score")
    _scores_high_score(conn, id_t)
    total_inscrits += 5
    nb_tournois += 1

    # 5. Ronde suisse en cours (ronde 1 générée).
    _tournoi_lance(conn, f"Tournoi {jeu(4)} — ronde suisse", jeu(4),
                   n=6, mode="ronde_suisse", nb_rondes=3)
    total_inscrits += 6
    nb_tournois += 1

    # 6. Élimination directe en cours.
    _tournoi_lance(conn, f"Tournoi {jeu(5)} — élimination directe", jeu(5),
                   n=8, mode="elimination")
    total_inscrits += 8
    nb_tournois += 1

    # 7. Terminé (high score + classement figé).
    id_t = _tournoi_lance(conn, f"Tournoi {jeu(6)} — terminé", jeu(6),
                          n=5, mode="high_score")
    _scores_high_score(conn, id_t)
    tournoi_services.changer_etat(conn, id_t, "termine")
    total_inscrits += 5
    nb_tournois += 1

    return {"tournois": nb_tournois, "inscrits": total_inscrits}


# ---------------------------------------------------------------------------
# Base du PLANNING bénévole
# ---------------------------------------------------------------------------
def _vider_base_planning(conn: sqlite3.Connection) -> None:
    """
    Supprime toutes les lignes des 8 tables du planning, dans l'ordre inverse
    des dépendances de clés étrangères (PRAGMA foreign_keys est actif).
    """
    for table in ("affectations", "preferences", "disponibilites", "besoins",
                  "benevoles", "creneaux", "postes", "evenements"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def peupler_planning(conn: sqlite3.Connection) -> dict:
    """
    Vide puis repeuple la base du PLANNING avec le jeu de démonstration complet
    (postes, créneaux, ~28 bénévoles, préremplissage, publication + jumeau en
    collecte), réutilisé depuis `app.planning.demo`.

    Returns:
        Résumé {"planning_evenements": n, "benevoles": n}.
    """
    _vider_base_planning(conn)
    ev = planning_demo.creer_demo(conn)
    nb_benevoles = len(planning_services.lister_benevoles(conn, ev))
    # creer_demo crée l'événement publié + un jumeau resté en collecte.
    return {"planning_evenements": 2, "benevoles": nb_benevoles}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def peupler() -> dict:
    """
    Vide puis repeuple les TROIS bases (prêt + tournois + planning) de
    l'instance courante (selon les variables d'environnement actives).
    Idempotent au sens fort. Renvoie un résumé fusionné.
    """
    noms = noms_jeux_formation(NB_JEUX)

    conn_pret = pret_db.get_connection()
    try:
        resume = peupler_pret(conn_pret, noms)
    finally:
        conn_pret.close()

    conn_tournoi = tournoi_db.get_connection()
    try:
        resume.update(peupler_tournoi(conn_tournoi, noms))
    finally:
        conn_tournoi.close()

    conn_planning = planning_db.get_connection()
    try:
        resume.update(peupler_planning(conn_planning))
    finally:
        conn_planning.close()

    return resume


# Exécuté via « python -m app.formation ».
if __name__ == "__main__":
    pret_db.init_db()
    tournoi_db.init_db()
    planning_db.init_db()
    resume = peupler()
    print("Données de formation (re)créées :")
    print(f"  - {resume['jeux']} jeux (noms tirés du catalogue) "
          f"({resume['prets_en_cours']} prêtés, {resume['prets_termines']} rendus)")
    print(f"  - {resume['tournois']} tournois d'exemple "
          f"({resume['inscrits']} inscrits au total)")
    print(f"  - planning bénévole prérempli "
          f"({resume['benevoles']} bénévoles fictifs)")
    print()
    print("RAPPEL : ce script vide puis repeuple les bases pointées par .env "
          "(DATABASE_PATH / TOURNOI_DATABASE_PATH / PLANNING_DATABASE_PATH). "
          "Ne jamais le lancer en pointant vers les bases de PRODUCTION.")
