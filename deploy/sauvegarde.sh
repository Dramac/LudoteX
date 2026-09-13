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
# - Rotation : garde les 30 sauvegardes de routine les plus récentes, ET
#   purge (SEC-11) les filets de sécurité automatiques
#   (avant-restauration-*.zip, posés par l'application juste avant chaque
#   restauration — voir app.sauvegarde.sauvegarde_de_securite) au-delà de
#   30 jours : ce dossier est sensible (numéros de pochette des prêts en
#   cours au moment de chaque restauration passée), voir install.sh qui le
#   pose en 0700.
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
#     1er argument : dossier d'installation (contient .venv et le code + .env)
#     2e  argument, facultatif : dossier de destination des archives. Absent,
#       c'est « sauvegardes/ » à côté de la base de prêt (DATABASE_PATH du
#       .env) : le dossier où l'application pose ses filets de sécurité et où
#       la supervision cherche la dernière sauvegarde.
#
# Planification (tous les jours à 3h) : deploy/ludotex-sauvegarde.timer, posé
# par deploy/install.sh. PAS de cron : voir ludotex-sauvegarde.service pour la
# raison. Ce script écrit sur la sortie standard ; ne jamais la rediriger vers
# /var/log/, où l'utilisateur du service ne peut pas créer de fichier
# (tests/test_sauvegarde_planifiee.py le refuse dans tout le dépôt).

set -euo pipefail

INSTALL_DIR="${1:-/opt/ludotex}"
GARDER=30

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

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
FICHIER="$DEST/ludotex-backup-$STAMP.zip"

"$PYTHON" - "$FICHIER" <<'PY'
import sys
import pathlib

from app.sauvegarde import creer_zip_sauvegarde

pathlib.Path(sys.argv[1]).write_bytes(creer_zip_sauvegarde())
PY

# Rotation : supprime les archives les plus anciennes au-delà de $GARDER.
ls -1t "$DEST"/ludotex-backup-*.zip | tail -n "+$((GARDER + 1))" | xargs -r rm -f

# SEC-11 (audit du 24/07/2026) — filets de sécurité automatiques
# (avant-restauration-*.zip, posés par app.sauvegarde.sauvegarde_de_securite
# juste AVANT chaque restauration, dans ce même dossier $DEST tel que
# configuré par deploy/install.sh) : la rotation ci-dessus ne les voit
# JAMAIS (elle ne filtre que ludotex-backup-*.zip), donc sans purge dédiée
# elles s'accumulaient indéfiniment. Ce ne sont pas des sauvegardes de
# routine : elles contiennent les TROIS bases telles qu'elles étaient juste
# avant CHAQUE restauration passée, donc les numéros de pochette des prêts
# EN COURS à ce moment-là (D5 ne les efface qu'à la clôture) — une donnée
# sensible à ne pas laisser traîner plus que nécessaire, dans un dossier
# déjà posé en 0700 par install.sh mais dont le contenu doit aussi avoir une
# fin de vie. Restaurer étant un geste rare et déjà validé par l'admin au
# moment où il se produit, 30 jours laissent largement le temps de
# s'apercevoir d'une mauvaise restauration sans garder ces archives ad
# vitam ; ajuster GARDER_JOURS_FILETS si besoin.
GARDER_JOURS_FILETS=30
find "$DEST" -maxdepth 1 -name 'avant-restauration-*.zip' -mtime "+$GARDER_JOURS_FILETS" -delete

# --- Envoi externe optionnel (décommenter après avoir configuré rclone) ---
# rclone copy "$FICHIER" nextcloud:sauvegardes/ludotex/ && echo "Copie externe OK"

echo "Sauvegarde créée : $FICHIER"
