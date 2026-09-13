"""
Supervision légère (LECTURE SEULE) de l'état de l'application — page admin
pensée pour un bureau non technicien : le jour de l'événement, vérifier en
5 secondes que tout va bien (bases saines, disque, sauvegarde de routine
récente, journal écrit, jeton valide, version déployée).

POURQUOI UN MODULE DÉDIÉ
-------------------------
Comme `app/sauvegarde.py`, la logique est isolée ici (testable sans passer
par le framework web) et ne duplique JAMAIS les chemins des 3 bases : ils
sont toujours lus via `get_database_path()` de chaque module
(`app.db` / `app.tournoi.db` / `app.planning.db`).

AUCUNE ÉCRITURE : ce module ne fait que lire le système de fichiers et la
base (jeton, dernière opération de prêt). La route qui l'utilise
(`routes/admin.py`) n'expose aucune action. Une exception héritée, signalée et
non corrigée : `espace_disque` crée le dossier des bases s'il manque. Le
contrôle d'intégrité, lui, ne crée ni base ni fichier annexe
(`_ouvrir_en_lecture`).

UN VOYANT NE MENT PAS, ET NE CRIE PAS À TORT
--------------------------------------------
Pendant sept semaines, le bloc « Sauvegarde » a été vert alors que la
sauvegarde de nuit ne tournait pas : il vérifiait qu'un fichier existait, pas
ce qu'il prouvait. Chaque bloc ci-dessous vérifie donc ce qu'il affirme, avec
un seuil nommé. Et chaque seuil est choisi pour ne jamais alerter sans raison :
un voyant qui crie à tort finit ignoré, ce qui revient au même qu'un voyant qui
ment. Le modèle est le bloc « Jeton bénévole », qui ne peut pas mentir.

NE JAMAIS BLOQUER : la supervision est incluse dans le tableau de bord, donc
lue à chaque visite de l'accueil de l'administration. Une base corrompue, un
dossier illisible ou un disque inaccessible s'affichent en « attention » ;
aucun ne fait tomber la page (`etat_supervision`).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from app import auth, journal, sauvegarde, services
from app import db as pret_db
from app.config import MODE_FORMATION
from app.planning import db as planning_db
from app.tournoi import db as tournoi_db
from app.version import APP_VERSION

# Ordre d'affichage sur la page de supervision.
_BASES = (
    ("Prêt de jeux", pret_db),
    ("Tournois", tournoi_db),
    ("Planning bénévole", planning_db),
)

# Fichier VERSION à la racine du dépôt (contenu libre, affiché tel quel).
_RACINE = Path(__file__).resolve().parent.parent
_FICHIER_VERSION = _RACINE / "VERSION"

# ---------------------------------------------------------------------------
# Seuils — un seul domicile chacun
# ---------------------------------------------------------------------------
# Âge au-delà duquel la dernière sauvegarde de ROUTINE est signalée.
# La routine passe chaque nuit à 3h, événement ou pas : d'une archive à la
# suivante, il y a 24 h. 26 h laissent deux heures de marge — une nuit de
# changement d'heure sur un serveur qui n'est pas en UTC (25 h), la durée de
# l'archive elle-même —, sans jamais alerter pour rien, et une nuit manquée se
# voit dès 5h (heure du serveur) le lendemain matin, avant l'ouverture.
# Si les passages deviennent plus fréquents, c'est cette valeur à resserrer.
SEUIL_AGE_SAUVEGARDE = timedelta(hours=26)

# Pourcentage d'espace libre sous lequel le disque est signalé. Sous 10 %, un
# petit VPS n'a plus beaucoup de marge pour une nuit d'archives et le journal.
SEUIL_DISQUE_LIBRE_POURCENT = 10

# Écart toléré entre une opération de prêt et la ligne de journal qui la suit.
# La ligne est écrite quelques millisecondes après : cinq minutes n'excusent
# qu'un disque lent, pas un journal qui a cessé d'être écrit.
TOLERANCE_JOURNAL = timedelta(minutes=5)

# Délai d'attente d'un verrou pour le contrôle d'intégrité : au-delà, la base
# est dite « illisible pour l'instant » plutôt que de faire attendre la page.
_DELAI_VERROU_S = 2

_LOGGER = logging.getLogger("uvicorn.error")


def _iso(horodatage: float) -> str:
    """Timestamp système (mtime) -> horodatage UTC ISO (pour le filtre dt_local)."""
    return datetime.fromtimestamp(horodatage, tz=timezone.utc).isoformat()


def duree_en_toutes_lettres(duree: timedelta) -> str:
    """« moins d'une heure », « 5 heures », « 3 jours » — l'âge lisible d'un coup d'œil."""
    heures = int(max(duree, timedelta(0)).total_seconds() // 3600)
    if heures < 1:
        return "moins d'une heure"
    if heures < 48:
        return f"{heures} {services.pluriel(heures, 'heure', 'heures')}"
    jours = heures // 24
    return f"{jours} jours"


# ===========================================================================
# Bases de données — EXP-03
# ===========================================================================
# État d'une base -> libellé de sa pastille (le texte dit l'état, pas seulement
# la couleur). Seul « ok » est vert.
LIBELLES_ETAT_BASE = {
    "ok": "Ok",
    "introuvable": "Introuvable",
    "vide": "Vide",
    "corrompue": "Corrompue",
    "illisible": "Illisible pour l'instant",
}


def _ouvrir_en_lecture(chemin: Path) -> sqlite3.Connection:
    """
    Connexion STRICTEMENT en lecture à une base qui existe : ni la base, ni ses
    fichiers annexes ne sont créés.

    Deux pièges, vérifiés :
    - ouvrir SQLite normalement sur un chemin absent CRÉE le fichier — une sonde
      de l'audit a fabriqué ainsi une base vide fantôme sur le serveur. D'où
      `mode=ro`, et l'appelant qui teste l'existence avant ;
    - sur une base en mode WAL, `mode=ro` crée les fichiers `-wal` et `-shm`
      s'ils manquent, et ne les retire pas en se fermant. Les bases tournois et
      planning, que rien d'autre n'ouvre parfois pendant des semaines, en
      auraient gardé deux de plus à chaque visite du tableau de bord.

    Donc : sans `-wal`, aucune connexion n'est ouverte sur la base, et
    `immutable=1` la lit telle qu'elle est sur le disque, sans verrou ni
    fichier annexe. Avec `-wal`, une connexion est vivante : `mode=ro` partage
    ses fichiers annexes, qui existent déjà, et lit un état cohérent.
    """
    annexe_wal = chemin.with_name(chemin.name + "-wal")
    options = "mode=ro" if annexe_wal.exists() else "mode=ro&immutable=1"
    return sqlite3.connect(f"file:{quote(str(chemin))}?{options}", uri=True,
                           timeout=_DELAI_VERROU_S)


def _verifier_base(chemin: Path) -> str:
    """Un passage du contrôle de `_controler_base` (voir sa docstring)."""
    try:
        conn = _ouvrir_en_lecture(chemin)
    except sqlite3.Error:
        return "illisible"
    try:
        resultat = conn.execute("PRAGMA quick_check").fetchall()
        if resultat != [("ok",)]:
            return "corrompue"
        nb_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        return "ok" if nb_tables else "vide"
    except sqlite3.OperationalError:
        # Verrou, permissions, fichier inaccessible : rien ne dit que la base
        # est abîmée.
        return "illisible"
    except sqlite3.DatabaseError:
        # « file is not a database », « database disk image is malformed ».
        return "corrompue"
    finally:
        conn.close()


def _controler_base(chemin: Path) -> str:
    """
    État d'une base : « ok », « introuvable », « vide », « corrompue » ou
    « illisible ». Ne lève jamais, n'écrit rien (`_ouvrir_en_lecture`).

    `PRAGMA quick_check` plutôt qu'`integrity_check` : ce contrôle tourne à
    chaque affichage du tableau de bord. Mesuré sur la production (base de prêt
    de 340 Ko) : 1 ms contre 2 ms, écart négligeable ; mais à 30 Mo, 42 ms
    contre 435 ms. `quick_check` trouve ce qui compte ici — un fichier tronqué,
    écrasé ou qui n'est pas une base — sans vérifier la cohérence des index.

    « vide » : un fichier de 0 octet ou sans aucune table passe `quick_check`
    (« ok ») alors que l'application n'y trouverait rien. C'est précisément la
    base fantôme de l'audit : elle ne doit pas être verte.

    Une base trouvée corrompue est relue une fois : une lecture sans verrou
    (`immutable=1`) qui croiserait l'instant où une connexion s'ouvre et
    recopie son journal dans la base verrait un état transitoire. Ne pas crier
    à tort vaut bien une seconde lecture, sur le seul chemin de l'alerte.
    """
    if not chemin.exists():
        return "introuvable"
    etat = _verifier_base(chemin)
    if etat == "corrompue":
        time.sleep(0.2)
        etat = _verifier_base(chemin) if chemin.exists() else "introuvable"
    return etat


def etat_bases() -> list[dict]:
    """
    État des 3 bases SQLite : nom, chemin, `etat` (voir `_controler_base`) et
    son `libelle`, `sain`, taille lisible, date de dernière modification (ISO
    UTC, à formater côté gabarit avec `dt_local`).
    """
    infos = []
    for nom, module in _BASES:
        chemin = module.get_database_path()
        etat = _controler_base(chemin)
        try:
            stat = chemin.stat() if etat != "introuvable" else None
        except OSError:
            stat = None
        infos.append({
            "nom": nom,
            "chemin": str(chemin),
            "existe": etat != "introuvable",
            "etat": etat,
            "libelle": LIBELLES_ETAT_BASE[etat],
            "sain": etat == "ok",
            "taille": services.format_taille(stat.st_size) if stat else None,
            "modifie": _iso(stat.st_mtime) if stat else None,
        })
    return infos


# ===========================================================================
# Espace disque — EXP-07
# ===========================================================================
def espace_disque() -> dict:
    """
    Espace disque du volume contenant les bases, et `sous_seuil` s'il reste
    moins de `SEUIL_DISQUE_LIBRE_POURCENT` % libres.
    """
    dossier = pret_db.get_database_path().parent
    # ⚠️ Écriture héritée, dans un module qui se déclare en lecture seule :
    # signalée au lot 4 de la série pré-production, non corrigée. Elle évite
    # que `shutil.disk_usage` lève `FileNotFoundError` sur un dossier absent
    # (vérifié) ; la retirer demande de mesurer le dossier parent existant.
    dossier.mkdir(parents=True, exist_ok=True)
    total, _utilise, libre = shutil.disk_usage(dossier)
    pourcentage = libre / total * 100 if total else 0
    return {
        "total": services.format_taille(total),
        "libre": services.format_taille(libre),
        "pourcentage_libre": round(pourcentage),
        "sous_seuil": pourcentage < SEUIL_DISQUE_LIBRE_POURCENT,
        "seuil": SEUIL_DISQUE_LIBRE_POURCENT,
    }


# ===========================================================================
# Sauvegarde — PROD-02
# ===========================================================================
def etat_sauvegarde(maintenant: datetime | None = None) -> dict:
    """
    Dernière sauvegarde de ROUTINE et son âge, et dernier filet, séparément.

    La routine seule prouve que la sauvegarde de nuit tourne : un filet (avant
    mise à jour, avant restauration) sert à revenir en arrière, pas à cette
    preuve. Il est affiché à part, sans pastille verte. La nature d'une archive
    se lit dans son nom (`app.sauvegarde`, « LES ARCHIVES DU SERVEUR »).

    `ok` : une routine existe et son âge ne dépasse pas `SEUIL_AGE_SAUVEGARDE`.
    Un dossier qui ne contient que des filets n'est PAS ok.

    Mode formation : `formation` vaut True et rien n'est signalé. Le minuteur
    de nuit ne sauvegarde que l'instance principale ; les données de la
    formation sont jetables, et un « Attention » permanent y apprendrait
    seulement au bureau à ignorer ce voyant.

    Limite connue : une archive déposée à la main avec un nom de routine, ou
    le filet de la mise à jour qui installe cette version (posé par l'ancien
    `update.sh`, donc sous l'ancien nom), compte comme une routine jusqu'à ce
    que son âge dépasse le seuil.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    dossier = sauvegarde.dossier_sauvegardes()
    archives = sauvegarde.lister_archives(dossier)
    routines = [a for a in archives if a["nature"] == sauvegarde.NATURE_ROUTINE]
    filets = [a for a in archives if a["nature"] != sauvegarde.NATURE_ROUTINE]

    routine = None
    if routines:
        age = maintenant - datetime.fromtimestamp(routines[0]["horodatage"], tz=timezone.utc)
        routine = {
            **routines[0],
            "age": duree_en_toutes_lettres(age),
            "trop_ancienne": age > SEUIL_AGE_SAUVEGARDE,
        }
    return {
        "dossier": str(dossier),
        "routine": routine,
        "filet": filets[0] if filets else None,
        "formation": MODE_FORMATION,
        "ok": routine is not None and not routine["trop_ancienne"],
        "seuil": duree_en_toutes_lettres(SEUIL_AGE_SAUVEGARDE),
    }


def annonce_ecran_salle(conn: sqlite3.Connection) -> str | None:
    """
    Annonce actuellement affichée sur l'écran de salle (/live), ou None. Réutilise
    `app.routes.live.annonce_active` (import différé, même motif que
    `routes/admin.py`, pour éviter tout souci d'import circulaire) plutôt que
    de dupliquer la lecture/logique d'expiration ici. Objectif (idée 5.2) :
    qu'une annonce ne puisse pas rester affichée toute la journée sans que le
    bureau ne la voie côté admin.
    """
    from app.routes.live import annonce_active

    return annonce_active(conn)


# ===========================================================================
# Journal d'activité — EXP-04
# ===========================================================================
def etat_journal(derniere_operation: str | None = None) -> dict:
    """
    État du journal d'activité (docs/conception-journal.md §9) : taille du
    fichier courant, date de sa dernière écriture, et `etat` :

    - « ok »         : le journal existe, et aucune opération de prêt ne lui
      est postérieure ;
    - « neutre »     : vide, et rien ne dit qu'il aurait dû être écrit ;
    - « introuvable » : le fichier manque, alors que l'application le crée à
      son démarrage ;
    - « en_retard »  : une opération de prêt (`derniere_operation`, ISO UTC,
      voir `services.derniere_operation_pret`) est plus récente que la
      dernière écriture du journal, au-delà de `TOLERANCE_JOURNAL`.

    POURQUOI PAS UN SEUIL DE FRAÎCHEUR. Le journal n'est écrit que s'il y a de
    l'activité : muet des semaines hors événement, muet chaque nuit pendant.
    Un seuil d'âge crierait à tort l'hiver, ou chaque matin avant l'ouverture ;
    le limiter aux dates de l'événement ne réglerait pas la nuit. Chaque route
    qui écrit dans `prets` journalise juste après : un prêt plus récent que le
    journal est donc la preuve, et la seule, qu'il a cessé d'être écrit. Sans
    activité, rien n'est affirmé au-delà de la date de dernière écriture.

    Mode formation : ce contrôle n'est pas fait. `python -m app.formation`
    peuple la base de prêts datés sans passer par une route, donc sans journal,
    et le voyant crierait à tort après chaque installation.

    LECTURE SEULE : on ne lit jamais le contenu du fichier ici (pas de fuite
    possible, et pas de coût même si le fichier est volumineux).
    """
    chemin = journal.chemin_journal()
    if not chemin.exists():
        return {"existe": False, "vide": True, "etat": "introuvable", "chemin": str(chemin)}
    stat = chemin.stat()
    etat = {
        "existe": True,
        "vide": stat.st_size == 0,
        "taille": services.format_taille(stat.st_size),
        "modifie": _iso(stat.st_mtime),
        "chemin": str(chemin),
        "derniere_operation": None,
    }
    if derniere_operation and not MODE_FORMATION:
        try:
            operation = datetime.fromisoformat(derniere_operation)
        except ValueError:
            operation = None
        ecriture = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if operation is not None and operation.tzinfo is not None \
                and operation - ecriture > TOLERANCE_JOURNAL:
            etat["derniere_operation"] = derniere_operation
            etat["etat"] = "en_retard"
            return etat
    etat["etat"] = "neutre" if etat["vide"] else "ok"
    return etat


def etat_jeton(conn: sqlite3.Connection) -> dict:
    """Jeton bénévole : défini ou non, date d'expiration, expiré ou valide."""
    jeton = auth.jeton_actuel(conn)
    if not jeton:
        return {"defini": False}
    return {
        "defini": True,
        "expire_iso": auth.expiration_jeton(conn),
        "expire": auth.jeton_expire(conn),
    }


def version_deployee() -> str:
    """Contenu du fichier VERSION à la racine (affiché tel quel), sinon repli."""
    if _FICHIER_VERSION.exists():
        contenu = _FICHIER_VERSION.read_text(encoding="utf-8").strip()
        if contenu:
            return contenu
    return APP_VERSION


def _sans_exception(bloc: str, fonction, *args):
    """
    Appelle `fonction` ; en cas d'exception, la consigne dans le journal du
    serveur et renvoie `{"erreur": True}`, que le gabarit affiche en
    « attention ». Une supervision qui plante ferait tomber l'accueil de
    l'administration — le pire moment pour ne plus rien voir.
    """
    try:
        return fonction(*args)
    except Exception as exc:  # noqa: BLE001 — filet voulu, voir la docstring
        _LOGGER.warning("Supervision : bloc « %s » illisible (%s)", bloc, type(exc).__name__)
        return {"erreur": True}


def etat_supervision(conn: sqlite3.Connection) -> dict:
    """
    Rassemble toutes les informations affichées sur la page de supervision.

    Les quatre blocs contrôlés (bases, disque, sauvegarde, journal) passent par
    `_sans_exception`. Le jeton et l'annonce restent appelés tels quels : un
    repli `{"erreur": True}` ferait dire au bloc « Jeton » qu'aucun jeton n'est
    défini, ce qui serait un mensonge.
    """
    try:
        derniere_operation = services.derniere_operation_pret(conn)
    except sqlite3.Error:
        derniere_operation = None
    return {
        "bases": _sans_exception("Bases de données", etat_bases),
        "disque": _sans_exception("Espace disque", espace_disque),
        "sauvegarde": _sans_exception("Sauvegarde", etat_sauvegarde),
        "jeton": etat_jeton(conn),
        "annonce": annonce_ecran_salle(conn),
        "journal": _sans_exception("Journal d'activité", etat_journal, derniere_operation),
        "version": version_deployee(),
    }
