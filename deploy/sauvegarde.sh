#!/usr/bin/env bash
#
# Sauvegarde COMPLÈTE des trois bases SQLite de l'application (prêt, tournois,
# planning), dans une seule archive .zip.
#
# - Réutilise la logique déjà testée de l'application
#   (`app.sauvegarde.creer_zip_sauvegarde`) : copie COHÉRENTE de chaque base via
#   « .backup » SQLite (sûre même en mode WAL, jamais un simple cp qui pourrait
#   capturer une base à mi-écriture), regroupées avec un INFO.txt.
# - L'archive produite est directement RESTAURABLE depuis l'espace admin
#   (/admin/données → « Restaurer une sauvegarde »), au même format que l'export
#   manuel.
# - Nature de l'archive, lue dans son nom (voir app/sauvegarde.py, « LES
#   ARCHIVES DU SERVEUR ») : « routine » (ludotex-backup-*.zip, le minuteur de
#   nuit, par défaut) ou « avant-mise-a-jour » (avant-mise-a-jour-*.zip,
#   demandée par update.sh). La supervision ne compte que la routine.
# - Rotation et purge, à chaque passage, pour CHAQUE nature
#   (app.sauvegarde.purger_archives) : les 30 sauvegardes de routine les plus
#   récentes sont gardées ; les filets (avant mise à jour, et avant
#   restauration, posés par l'application) sont supprimés au-delà de 30 jours
#   (SEC-11). Ce dossier est sensible — les trois bases, dont les noms et
#   contacts du planning et les numéros de pochette des prêts en cours —, voir
#   install.sh qui le pose en 0700.
# - Optionnel : envoi vers un stockage externe via rclone (Nextcloud, Drive…).
#
# IMPORTANT : les trois bases sont sauvegardées, pas seulement celle du prêt.
# tournoi.db et planning.db contiennent des données à ne pas perdre (le planning
# comporte des données personnelles de bénévoles).
#
# Usage manuel (à lancer en tant qu'utilisateur du service, ex. pretjeux) :
#   ./deploy/sauvegarde.sh
#   ./deploy/sauvegarde.sh /opt/ludotex
#   ./deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes
#   ./deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes avant-mise-a-jour
#     1er argument : dossier d'installation (contient .venv et le code + .env)
#     2e  argument, facultatif (vide = absent) : dossier de destination des
#       archives. Absent, c'est « sauvegardes/ » à côté de la base de prêt
#       (DATABASE_PATH du .env) : le dossier où l'application pose ses filets
#       de sécurité et où la supervision cherche la dernière sauvegarde.
#     3e  argument, facultatif : nature de l'archive, « routine » par défaut.
#       Une sauvegarde lancée à la main sans 3e argument compte donc comme une
#       sauvegarde de routine.
#
# Planification (tous les jours à 3h) : deploy/ludotex-sauvegarde.timer, posé
# par deploy/install.sh. PAS de cron : voir ludotex-sauvegarde.service pour la
# raison. Ce script écrit sur la sortie standard ; ne jamais la rediriger vers
# /var/log/, où l'utilisateur du service ne peut pas créer de fichier
# (tests/test_sauvegarde_planifiee.py le refuse dans tout le dépôt).

set -euo pipefail

INSTALL_DIR="${1:-/opt/ludotex}"
NATURE="${3:-routine}"

PYTHON="$INSTALL_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERREUR : interpréteur Python introuvable ($PYTHON). Passer le dossier d'installation en 1er argument." >&2
    exit 1
fi

# Se placer dans le dossier d'installation : l'application lit .env (chemins des
# trois bases) et importe le paquet `app` depuis là.
cd "$INSTALL_DIR"

# Destination : le 2e argument, sinon le dossier que l'application elle-même
# utilise (même dérivation qu'update.sh et qu'app.sauvegarde) — jamais une
# valeur écrite en dur dans l'unité systemd, qui divergerait du .env.
if [[ -n "${2:-}" ]]; then
    DEST="$2"
else
    DEST="$("$PYTHON" -c 'from app.db import get_database_path; print((get_database_path().parent / "sauvegardes").resolve())')" || DEST=""
    if [[ -z "$DEST" ]]; then
        echo "ERREUR : dossier des sauvegardes introuvable (lecture de DATABASE_PATH dans $INSTALL_DIR/.env). Le passer en 2e argument." >&2
        exit 1
    fi
fi

# Écriture, puis rotation et purge : les noms, la nature et les règles de fin de
# vie vivent dans app/sauvegarde.py, pas ici. Une nature inconnue est refusée
# avant toute écriture. Le script Python n'écrit sur la sortie standard que le
# chemin de l'archive créée.
FICHIER="$("$PYTHON" - "$DEST" "$NATURE" <<'PY'
import sys
from pathlib import Path

from app.sauvegarde import ecrire_archive, purger_archives

dossier, nature = Path(sys.argv[1]), sys.argv[2]
try:
    chemin = ecrire_archive(nature, dossier)
except ValueError as exc:
    sys.exit(f"ERREUR : {exc}.")
purger_archives(dossier)
print(chemin)
PY
)"
# Dernière ligne seulement : un message imprimé par un import ne doit pas
# se retrouver dans le chemin annoncé.
FICHIER="${FICHIER##*$'\n'}"

# --- Envoi externe optionnel (décommenter après avoir configuré rclone) ---
# rclone copy "$FICHIER" nextcloud:sauvegardes/ludotex/ && echo "Copie externe OK"

echo "Sauvegarde créée : $FICHIER"
