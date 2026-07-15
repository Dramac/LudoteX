"""
Sauvegarde et restauration COMPLÈTE des trois bases SQLite de l'application
(prêt de jeux, tournois, planning bénévole) — logique métier isolée, appelée
depuis l'espace d'administration (`routes/admin.py`).

POURQUOI UN MODULE DÉDIÉ
------------------------
Regrouper cette logique ici (plutôt que dans routes/admin.py) permet de la
tester indépendamment du framework web, et de ne dupliquer nulle part les
chemins des 3 bases : ils sont toujours lus via `get_database_path()` des
modules `app.db` / `app.tournoi.db` / `app.planning.db`.

FORMAT DE L'ARCHIVE
--------------------
Un zip contenant les 3 bases sous des noms FIXES (indépendants du chemin
réellement configuré via `.env`) — `pret-jeux.db`, `tournoi.db`, `planning.db`
— plus un fichier `INFO.txt` (date, heure, version). Ces noms fixes sont ce qui
permet, à l'import, de savoir sans ambiguïté quelle base est quelle.

COPIE À CHAUD
-------------
Chaque base est copiée via `sqlite3.Connection.backup()`, qui produit une
copie cohérente même en mode WAL avec des écritures concurrentes — jamais un
simple `cp`, qui pourrait capturer un fichier à mi-écriture. Même technique que
`deploy/sauvegarde.sh` (ligne de commande `.backup`).

RESTAURATION : SÛRETÉ
----------------------
- Le zip est entièrement VALIDÉ (présence des 3 bases + `PRAGMA
  integrity_check` sur chacune) avant toute modification.
- Un filet de sécurité silencieux (`sauvegarde_de_securite`) exporte l'état
  actuel dans `data/sauvegardes/` juste avant de remplacer quoi que ce soit.
- Le remplacement se fait fichier par fichier : chaque route de l'application
  ouvre puis referme sa propre connexion (pas de pool ni de connexion
  persistante), donc remplacer les fichiers entre deux requêtes est sûr. Les
  éventuels fichiers annexes `-wal`/`-shm`/`-journal` de la base REMPLACÉE sont
  supprimés au préalable, pour ne jamais mélanger d'anciennes écritures non
  validées avec le contenu restauré.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import ModuleType

from app import db as pret_db
from app.planning import db as planning_db
from app.tournoi import db as tournoi_db
from app.version import APP_VERSION

# Noms FIXES utilisés DANS L'ARCHIVE (voir docstring du module). Ordre = ordre
# d'écriture dans le zip.
_MODULES: dict[str, ModuleType] = {
    "pret-jeux.db": pret_db,
    "tournoi.db": tournoi_db,
    "planning.db": planning_db,
}

NOMS_BASES: tuple[str, ...] = tuple(_MODULES)
NOM_INFO = "INFO.txt"


class ZipInvalide(Exception):
    """Levée quand un zip de sauvegarde est incomplet, corrompu ou invalide."""


def nom_fichier_zip(maintenant: datetime | None = None) -> str:
    """Nom de fichier suggéré pour le téléchargement : « ludotex-backup-AAAA-MM-JJ.zip »."""
    maintenant = maintenant or datetime.now(timezone.utc)
    return f"ludotex-backup-{maintenant.strftime('%Y-%m-%d')}.zip"


def _copie_a_chaud(source: Path, destination: Path) -> None:
    """Copie cohérente d'une base SQLite via l'API `backup()` (sûre même en WAL)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    dest_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()


def creer_zip_sauvegarde() -> bytes:
    """
    Crée une sauvegarde complète des 3 bases (+ `INFO.txt`) et renvoie le
    contenu de l'archive zip (bytes), prêt à être servi en téléchargement ou
    écrit sur disque.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for nom_archive, module in _MODULES.items():
                source = module.get_database_path()
                copie = tmp_path / nom_archive
                if source.exists():
                    _copie_a_chaud(source, copie)
                else:
                    # Base jamais initialisée (cas improbable : app.main l'init
                    # toujours au démarrage) : on écrit un fichier SQLite vide
                    # plutôt que de faire échouer tout l'export.
                    sqlite3.connect(copie).close()
                zf.write(copie, nom_archive)

            maintenant = datetime.now(timezone.utc)
            info = (
                "Sauvegarde LudoteX\n"
                f"Date : {maintenant.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                f"Version de l'application : {APP_VERSION}\n"
            )
            zf.writestr(NOM_INFO, info)
        return buffer.getvalue()


def _integrite_ok(chemin: Path) -> bool:
    """`PRAGMA integrity_check` : True si le fichier est une base SQLite saine."""
    try:
        conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return False
    try:
        resultat = conn.execute("PRAGMA integrity_check").fetchone()
        return resultat is not None and resultat[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def valider_zip_sauvegarde(chemin_zip: Path) -> None:
    """
    Vérifie qu'un zip de sauvegarde est exploitable ; lève `ZipInvalide` sinon.

    Contrôles, dans l'ordre : le fichier est bien une archive zip lisible,
    elle contient les 3 bases attendues (`NOMS_BASES`), et chacune est un
    fichier SQLite valide (`PRAGMA integrity_check`). Ne modifie rien.
    """
    try:
        zf = zipfile.ZipFile(chemin_zip)
    except zipfile.BadZipFile as exc:
        raise ZipInvalide("Le fichier n'est pas une archive zip valide.") from exc

    with zf:
        noms = set(zf.namelist())
        manquants = [n for n in NOMS_BASES if n not in noms]
        if manquants:
            raise ZipInvalide(
                "Archive incomplète : fichier(s) manquant(s) : " + ", ".join(manquants)
            )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for nom in NOMS_BASES:
                extrait = tmp_path / nom
                try:
                    extrait.write_bytes(zf.read(nom))
                except zipfile.BadZipFile as exc:
                    raise ZipInvalide(f"Archive corrompue (« {nom} » illisible).") from exc
                if not _integrite_ok(extrait):
                    raise ZipInvalide(f"Base « {nom} » corrompue ou invalide dans l'archive.")


def sauvegarde_de_securite() -> Path:
    """
    Filet de sécurité SILENCIEUX : exporte l'état ACTUEL des 3 bases dans un
    zip horodaté sous `data/sauvegardes/` (dossier déjà exclu de git, voir
    .gitignore), appelé automatiquement avant toute restauration.

    Le dossier est dérivé du chemin configuré de la base de prêt (et non codé
    en dur), pour rester cohérent avec un déploiement qui personnalise
    `DATABASE_PATH`.

    Returns:
        Le chemin du zip de sécurité créé.
    """
    dossier = pret_db.get_database_path().parent / "sauvegardes"
    dossier.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    chemin = dossier / f"avant-restauration-{horodatage}.zip"
    chemin.write_bytes(creer_zip_sauvegarde())
    return chemin


def _remplacer_fichier(destination: Path, source: Path) -> None:
    """
    Remplace le fichier de base `destination` par le contenu de `source`.

    Supprime d'abord les éventuels fichiers annexes `-wal`/`-shm`/`-journal` de
    la destination : sans ça, d'anciennes pages WAL non validées pourraient se
    mélanger avec le contenu tout juste restauré à la prochaine ouverture.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    for suffixe in ("-wal", "-shm", "-journal"):
        sidecar = destination.parent / (destination.name + suffixe)
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(source, destination)


def restaurer_zip_sauvegarde(chemin_zip: Path) -> None:
    """
    Restaure les 3 bases depuis un zip de sauvegarde, en REMPLAÇANT
    l'intégralité des données actuelles.

    Étapes :
        1. Valide le zip (lève `ZipInvalide` sinon, sans rien modifier).
        2. Crée le filet de sécurité de l'état actuel (`sauvegarde_de_securite`).
        3. Remplace chaque fichier de base par son contenu dans l'archive.
    """
    valider_zip_sauvegarde(chemin_zip)
    sauvegarde_de_securite()

    with zipfile.ZipFile(chemin_zip) as zf, tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for nom, module in _MODULES.items():
            extrait = tmp_path / nom
            extrait.write_bytes(zf.read(nom))
            _remplacer_fichier(module.get_database_path(), extrait)
