"""
Supervision légère (LECTURE SEULE) de l'état de l'application — page admin
pensée pour un bureau non technicien : le jour de l'événement, vérifier en
5 secondes que tout va bien (bases présentes, disque, sauvegarde récente,
jeton valide, version déployée).

POURQUOI UN MODULE DÉDIÉ
-------------------------
Comme `app/sauvegarde.py`, la logique est isolée ici (testable sans passer
par le framework web) et ne duplique JAMAIS les chemins des 3 bases : ils
sont toujours lus via `get_database_path()` de chaque module
(`app.db` / `app.tournoi.db` / `app.planning.db`).

AUCUNE ÉCRITURE : ce module ne fait que lire le système de fichiers et la
base (jeton). La route qui l'utilise (`routes/admin.py`) n'expose aucune
action.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import auth
from app import db as pret_db
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


def _formater_taille(octets: int) -> str:
    """Taille lisible : « 512 o », « 84 Ko », « 3,2 Mo »."""
    if octets < 1024:
        return f"{octets} o"
    if octets < 1024 * 1024:
        return f"{octets / 1024:.0f} Ko"
    return f"{octets / (1024 * 1024):.1f} Mo"


def _iso(horodatage: float) -> str:
    """Timestamp système (mtime) -> horodatage UTC ISO (pour le filtre dt_local)."""
    return datetime.fromtimestamp(horodatage, tz=timezone.utc).isoformat()


def etat_bases() -> list[dict]:
    """
    État des 3 bases SQLite : nom, chemin, présence, taille lisible, date de
    dernière modification (ISO UTC, à formater côté gabarit avec `dt_local`).
    """
    infos = []
    for nom, module in _BASES:
        chemin = module.get_database_path()
        existe = chemin.exists()
        infos.append({
            "nom": nom,
            "chemin": str(chemin),
            "existe": existe,
            "taille": _formater_taille(chemin.stat().st_size) if existe else None,
            "modifie": _iso(chemin.stat().st_mtime) if existe else None,
        })
    return infos


def espace_disque() -> dict:
    """Espace disque du volume contenant les bases (données de l'association)."""
    dossier = pret_db.get_database_path().parent
    dossier.mkdir(parents=True, exist_ok=True)
    total, _utilise, libre = shutil.disk_usage(dossier)
    return {
        "total": _formater_taille(total),
        "libre": _formater_taille(libre),
        "pourcentage_libre": round(libre / total * 100) if total else 0,
    }


def derniere_sauvegarde() -> dict:
    """
    Fichier le plus récent de `data/sauvegardes/` (dossier des filets de
    sécurité automatiques, voir `app.sauvegarde.sauvegarde_de_securite`, et des
    sauvegardes complètes qu'on y dépose). Le dossier est dérivé du chemin de
    la base de prêt, jamais codé en dur.
    """
    dossier = pret_db.get_database_path().parent / "sauvegardes"
    fichiers = [f for f in dossier.iterdir() if f.is_file()] if dossier.exists() else []
    if not fichiers:
        return {"existe": False}
    plus_recent = max(fichiers, key=lambda f: f.stat().st_mtime)
    return {
        "existe": True,
        "nom": plus_recent.name,
        "modifie": _iso(plus_recent.stat().st_mtime),
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


def etat_supervision(conn: sqlite3.Connection) -> dict:
    """Rassemble toutes les informations affichées sur la page de supervision."""
    return {
        "bases": etat_bases(),
        "disque": espace_disque(),
        "sauvegarde": derniere_sauvegarde(),
        "jeton": etat_jeton(conn),
        "annonce": annonce_ecran_salle(conn),
        "version": version_deployee(),
    }
