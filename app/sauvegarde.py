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
  actuel dans le dossier des sauvegardes juste avant de remplacer quoi que ce
  soit.
- Le remplacement se fait fichier par fichier : chaque route de l'application
  ouvre puis referme sa propre connexion (pas de pool ni de connexion
  persistante), donc remplacer les fichiers entre deux requêtes est sûr. Les
  éventuels fichiers annexes `-wal`/`-shm`/`-journal` de la base REMPLACÉE sont
  supprimés au préalable, pour ne jamais mélanger d'anciennes écritures non
  validées avec le contenu restauré.

MIGRATIONS APRÈS RESTAURATION
-----------------------------
Une sauvegarde ancienne contient une base au SCHÉMA DE SON ÉPOQUE. Or les
migrations (`init_db()` de chaque module) ne tournaient jusqu'ici qu'au
DÉMARRAGE du serveur (`app/main.py`), alors qu'une restauration se fait à
chaud, sans redémarrage : le code actuel se retrouvait donc à écrire dans une
base qui n'a jamais connu ses colonnes récentes — panne au pire moment, celui
où l'on restaure justement parce qu'un incident vient d'avoir lieu.
`restaurer_zip_sauvegarde` rejoue donc les trois `init_db()` juste après avoir
remplacé les fichiers. Ils sont idempotents par conception, donc sans risque
sur une base déjà à jour.

LES ARCHIVES DU SERVEUR : UNE NATURE, LUE DANS LE NOM
-----------------------------------------------------
Le dossier des sauvegardes (`dossier_sauvegardes`) reçoit trois sortes
d'archives, au même format, que seul leur NOM distingue :

- `ludotex-backup-*`     : la sauvegarde de ROUTINE (minuteur de nuit) ;
- `avant-mise-a-jour-*`  : le filet posé par `deploy/update.sh` ;
- `avant-restauration-*` : le filet posé par `sauvegarde_de_securite`.

Pourquoi le nom et pas l'heure : une archive de routine rattrapée au démarrage
du serveur porte l'heure du démarrage, et un filet de mise à jour peut tomber à
l'heure d'un passage de routine. Or la supervision doit prouver que la ROUTINE
tourne : un dossier qui ne contient que des filets n'est pas un dossier
sauvegardé — c'est ce qui a masqué, sept semaines durant, une sauvegarde de
nuit qui ne tournait pas.

Cette convention, la rotation et la purge vivent ICI et nulle part ailleurs :
`deploy/sauvegarde.sh` appelle `ecrire_archive` puis `purger_archives`, la
supervision et l'écran « Données & sauvegarde » appellent `lister_archives`.
Chaque nature a sa règle de fin de vie (`purger_archives`) : ces archives
contiennent les trois bases, dont les noms et contacts du planning, et une
archive qu'aucune règle ne purge resterait pour toujours.
"""

from __future__ import annotations

import re
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

# ---------------------------------------------------------------------------
# Archives du dossier des sauvegardes (voir « LES ARCHIVES DU SERVEUR »)
# ---------------------------------------------------------------------------
NATURE_ROUTINE = "routine"
NATURE_AVANT_MISE_A_JOUR = "avant-mise-a-jour"
NATURE_AVANT_RESTAURATION = "avant-restauration"

# Préfixe du nom de fichier de chaque nature. `ludotex-backup` ne doit PAS
# changer : les archives déjà présentes sur les serveurs le portent, et la
# documentation d'exploitation s'en sert pour reconnaître l'archive de la nuit.
PREFIXES_ARCHIVE: dict[str, str] = {
    NATURE_ROUTINE: "ludotex-backup",
    NATURE_AVANT_MISE_A_JOUR: "avant-mise-a-jour",
    NATURE_AVANT_RESTAURATION: "avant-restauration",
}

# Libellés affichés à l'administration (et cités mot pour mot par le wiki).
LIBELLES_NATURE: dict[str, str] = {
    NATURE_ROUTINE: "Sauvegarde de routine",
    NATURE_AVANT_MISE_A_JOUR: "Filet avant mise à jour",
    NATURE_AVANT_RESTAURATION: "Filet avant restauration",
}

# Nom d'une archive : un préfixe connu, puis seulement des chiffres et des
# tirets (« 20260913-030001 », ou « 2026-09-13 » pour un fichier téléchargé
# puis redéposé à la main), puis `.zip`. Ni point, ni barre oblique : un nom
# qui respecte ce motif ne peut désigner qu'un fichier du dossier lui-même.
MOTIF_ARCHIVE = re.compile(
    "(" + "|".join(re.escape(p) for p in PREFIXES_ARCHIVE.values()) + r")-[0-9][0-9-]*\.zip"
)

# Rotation de la routine : les 30 plus récentes sont gardées. Au rythme d'une
# par nuit, c'est un mois d'historique.
GARDER_ROUTINES = 30

# Fin de vie des deux filets (SEC-11, audit du 24/07/2026) : 30 jours laissent
# largement le temps de s'apercevoir d'une mise à jour ou d'une restauration
# malheureuse, sans garder indéfiniment les données qu'elles contiennent.
GARDER_JOURS_FILETS = 30


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


def dossier_sauvegardes() -> Path:
    """
    Dossier des archives du serveur : `sauvegardes/` à côté de la base de prêt.

    Dérivé du chemin configuré (`DATABASE_PATH`), jamais codé en dur — la même
    dérivation que `deploy/update.sh` et `deploy/sauvegarde.sh`, qui la
    recalculent en shell faute de pouvoir importer cette fonction.
    """
    return pret_db.get_database_path().parent / "sauvegardes"


def nom_archive(nature: str, maintenant: datetime) -> str:
    """Nom d'une archive : « <préfixe de la nature>-AAAAMMJJ-HHMMSS.zip »."""
    return f"{PREFIXES_ARCHIVE[nature]}-{maintenant.strftime('%Y%m%d-%H%M%S')}.zip"


def nature_archive(nom: str) -> str | None:
    """Nature d'une archive d'après son nom, ou None s'il n'est pas un nom d'archive."""
    correspondance = MOTIF_ARCHIVE.fullmatch(nom)
    if not correspondance:
        return None
    prefixe = correspondance.group(1)
    return next(n for n, p in PREFIXES_ARCHIVE.items() if p == prefixe)


def lister_archives(dossier: Path | None = None) -> list[dict]:
    """
    Archives réellement présentes dans le dossier des sauvegardes, de la plus
    récente à la plus ancienne. LECTURE SEULE.

    Chaque entrée : `nom`, `nature`, `libelle`, `horodatage` (mtime, secondes),
    `modifie` (ISO UTC, pour le filtre `dt_local`), `octets`.

    Ne retient que les fichiers ordinaires dont le nom respecte
    `MOTIF_ARCHIVE` ; un lien symbolique est écarté même s'il porte un nom
    d'archive, puisqu'il pourrait désigner un fichier hors du dossier. Un
    dossier absent donne une liste vide ; un dossier illisible lève `OSError`,
    à l'appelant de le dire.
    """
    dossier = dossier if dossier is not None else dossier_sauvegardes()
    if not dossier.exists():
        return []
    archives = []
    for fichier in dossier.iterdir():
        nature = nature_archive(fichier.name)
        if nature is None or fichier.is_symlink() or not fichier.is_file():
            continue
        etat = fichier.stat()
        archives.append({
            "nom": fichier.name,
            "nature": nature,
            "libelle": LIBELLES_NATURE[nature],
            "horodatage": etat.st_mtime,
            "modifie": datetime.fromtimestamp(etat.st_mtime, tz=timezone.utc).isoformat(),
            "octets": etat.st_size,
        })
    archives.sort(key=lambda a: a["horodatage"], reverse=True)
    return archives


def archive_telechargeable(nom: str) -> Path | None:
    """
    Chemin de l'archive `nom` si, et seulement si, elle peut être servie en
    téléchargement ; None sinon.

    Deux conditions, toutes deux nécessaires : le nom respecte `MOTIF_ARCHIVE`
    ET il figure dans la liste réellement lue dans le dossier. Le nom reçu de
    l'URL n'est jamais ouvert tel quel : c'est l'entrée de la liste qui l'est.
    """
    if not isinstance(nom, str) or not MOTIF_ARCHIVE.fullmatch(nom):
        return None
    dossier = dossier_sauvegardes()
    for archive in lister_archives(dossier):
        if archive["nom"] == nom:
            return dossier / archive["nom"]
    return None


def ecrire_archive(nature: str, dossier: Path, maintenant: datetime | None = None) -> Path:
    """
    Écrit une archive complète de la nature donnée dans `dossier` (appelé par
    `deploy/sauvegarde.sh`). Lève `ValueError` pour une nature inconnue, avant
    d'avoir rien écrit.

    L'horodatage du nom est l'heure LOCALE du serveur, comme le faisait
    `date +%Y%m%d-%H%M%S` dans le script avant ce module : c'est l'heure que
    règle le minuteur (`OnCalendar=`), et celle que la documentation demande de
    reconnaître dans le nom de l'archive de la nuit.
    """
    if nature not in PREFIXES_ARCHIVE:
        raise ValueError(
            f"nature de sauvegarde inconnue « {nature} » "
            f"(attendu : {', '.join(PREFIXES_ARCHIVE)})"
        )
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom_archive(nature, maintenant or datetime.now())
    chemin.write_bytes(creer_zip_sauvegarde())
    return chemin


def purger_archives(dossier: Path, maintenant: float | None = None) -> list[str]:
    """
    Applique à `dossier` la règle de fin de vie de CHAQUE nature ; renvoie les
    noms supprimés.

    - routine : les `GARDER_ROUTINES` plus récentes sont gardées ;
    - filets (avant mise à jour, avant restauration) : supprimés au-delà de
      `GARDER_JOURS_FILETS` jours.

    Toute archive reconnue par `lister_archives` relève de l'une de ces deux
    règles : aucune n'échappe à la purge. Un fichier dont le nom n'est pas un
    nom d'archive n'est jamais touché. Une suppression impossible lève
    `OSError` : le script échoue alors visiblement plutôt que de laisser des
    archives s'accumuler en silence.
    """
    maintenant = maintenant if maintenant is not None else datetime.now().timestamp()
    limite_filets = maintenant - GARDER_JOURS_FILETS * 86400
    archives = lister_archives(dossier)
    routines = [a for a in archives if a["nature"] == NATURE_ROUTINE]
    a_supprimer = routines[GARDER_ROUTINES:] + [
        a for a in archives
        if a["nature"] != NATURE_ROUTINE and a["horodatage"] < limite_filets
    ]
    for archive in a_supprimer:
        (dossier / archive["nom"]).unlink()
    return [a["nom"] for a in a_supprimer]


def sauvegarde_de_securite() -> Path:
    """
    Filet de sécurité SILENCIEUX : exporte l'état ACTUEL des 3 bases dans une
    archive `avant-restauration-*` du dossier des sauvegardes (voir
    `dossier_sauvegardes` ; exclu de git), appelé automatiquement avant toute
    restauration. Horodatage en UTC, comme depuis toujours pour ce filet.

    Returns:
        Le chemin du zip de sécurité créé.
    """
    dossier = dossier_sauvegardes()
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom_archive(NATURE_AVANT_RESTAURATION, datetime.now(timezone.utc))
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


def _migrer_bases_restaurees() -> None:
    """
    Rejoue `init_db()` sur les 3 bases juste restaurées (voir « MIGRATIONS
    APRÈS RESTAURATION » dans la docstring du module).

    Chaque `init_db()` est idempotent par conception (`CREATE TABLE IF NOT
    EXISTS` + migrations conditionnelles) : l'appel est sans effet sur une
    base déjà à jour, et ne touche jamais aux données.
    """
    for module in _MODULES.values():
        module.init_db()


def restaurer_zip_sauvegarde(chemin_zip: Path) -> None:
    """
    Restaure les 3 bases depuis un zip de sauvegarde, en REMPLAÇANT
    l'intégralité des données actuelles.

    Étapes :
        1. Valide le zip (lève `ZipInvalide` sinon, sans rien modifier).
        2. Crée le filet de sécurité de l'état actuel (`sauvegarde_de_securite`).
        3. Remplace chaque fichier de base par son contenu dans l'archive.
        4. Rejoue les migrations de schéma sur les bases restaurées (voir
           `_migrer_bases_restaurees`).
    """
    valider_zip_sauvegarde(chemin_zip)
    sauvegarde_de_securite()

    with zipfile.ZipFile(chemin_zip) as zf, tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for nom, module in _MODULES.items():
            extrait = tmp_path / nom
            extrait.write_bytes(zf.read(nom))
            _remplacer_fichier(module.get_database_path(), extrait)

    _migrer_bases_restaurees()
