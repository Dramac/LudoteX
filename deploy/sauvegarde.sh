#!/usr/bin/env bash
#
# Sauvegarde de la base SQLite de l'application de prêt.
#
# - Utilise « .backup » de sqlite3 → copie COHÉRENTE même en mode WAL (jamais
#   un simple cp, qui pourrait capturer une base à mi-écriture).
# - Rotation : garde les 30 sauvegardes les plus récentes.
# - Optionnel : envoi vers un stockage externe via rclone (Nextcloud, Drive…).
#
# Usage manuel :
#   ./deploy/sauvegarde.sh
#   ./deploy/sauvegarde.sh /chemin/base.db /chemin/dossier_sauvegardes
#
# Planification (cron, tous les jours à 3h) — éditer avec `crontab -e` :
#   0 3 * * * /opt/ludotex/deploy/sauvegarde.sh >> /var/log/ludotex-sauvegarde.log 2>&1

set -euo pipefail

DB="${1:-/opt/ludotex/data/pret-jeux.db}"
DEST="${2:-/opt/ludotex/sauvegardes}"
GARDER=30

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
FICHIER="$DEST/ludotex-$STAMP.db"

sqlite3 "$DB" ".backup '$FICHIER'"

# Rotation : supprime les plus anciennes au-delà de $GARDER.
ls -1t "$DEST"/ludotex-*.db | tail -n "+$((GARDER + 1))" | xargs -r rm -f

# --- Envoi externe optionnel (décommenter après avoir configuré rclone) ---
# rclone copy "$FICHIER" nextcloud:sauvegardes/ludotex/ && echo "Copie externe OK"

echo "Sauvegarde créée : $FICHIER"
