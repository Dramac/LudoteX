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
# - Rotation : garde les 30 sauvegardes les plus récentes.
# - Optionnel : envoi vers un stockage externe via rclone (Nextcloud, Drive…).
#
# IMPORTANT : les trois bases sont sauvegardées, pas seulement celle du prêt.
# tournoi.db et planning.db contiennent des données à ne pas perdre (le planning
# comporte des données personnelles de bénévoles).
#
# Usage manuel (à lancer en tant qu'utilisateur du service, ex. pretjeux) :
#   ./deploy/sauvegarde.sh
#   ./deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes
#     1er argument : dossier d'installation (contient .venv et le code + .env)
#     2e  argument : dossier de destination des archives
#
# Planification (cron, tous les jours à 3h) — voir deploy/install.sh, ou :
#   0 3 * * * /opt/ludotex/deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes >> /var/log/ludotex-sauvegarde.log 2>&1

set -euo pipefail

INSTALL_DIR="${1:-/opt/ludotex}"
DEST="${2:-$INSTALL_DIR/sauvegardes}"
GARDER=30

PYTHON="$INSTALL_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERREUR : interpréteur Python introuvable ($PYTHON). Passer le dossier d'installation en 1er argument." >&2
    exit 1
fi

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
FICHIER="$DEST/ludotex-backup-$STAMP.zip"

# Se placer dans le dossier d'installation : l'application lit .env (chemins des
# trois bases) et importe le paquet `app` depuis là.
cd "$INSTALL_DIR"
"$PYTHON" - "$FICHIER" <<'PY'
import sys
import pathlib

from app.sauvegarde import creer_zip_sauvegarde

pathlib.Path(sys.argv[1]).write_bytes(creer_zip_sauvegarde())
PY

# Rotation : supprime les archives les plus anciennes au-delà de $GARDER.
ls -1t "$DEST"/ludotex-backup-*.zip | tail -n "+$((GARDER + 1))" | xargs -r rm -f

# --- Envoi externe optionnel (décommenter après avoir configuré rclone) ---
# rclone copy "$FICHIER" nextcloud:sauvegardes/ludotex/ && echo "Copie externe OK"

echo "Sauvegarde créée : $FICHIER"
