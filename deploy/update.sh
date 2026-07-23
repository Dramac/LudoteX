#!/usr/bin/env bash
#
# Mise à jour de LudoteX sur le VPS.
#
# Enchaîne, en sécurité, les gestes d'une mise à jour :
#   1. Sauvegarde des trois bases AVANT toute modification (filet de sécurité).
#   2. Récupération du nouveau code (git pull --ff-only).
#   3. Mise à jour des dépendances Python (requirements.txt).
#   4. Migrations des bases (idempotentes).
#   5. Redémarrage du service (et de l'instance de formation si présente).
#   6. Vérification que l'application répond.
#
# À lancer APRÈS avoir poussé le nouveau code sur GitHub (git push), depuis le
# serveur :
#   cd /opt/ludotex && sudo ./deploy/update.sh
#
# Argument optionnel : le dossier d'installation (défaut /opt/ludotex).
#   sudo ./deploy/update.sh /chemin/vers/ludotex
#
# Le script est sûr à relancer : si le code est déjà à jour, il réinstalle les
# dépendances (rapide), rejoue les migrations (sans effet) et redémarre.

set -euo pipefail

INSTALL_DIR="${1:-/opt/ludotex}"
SERVICE_USER="pretjeux"
PYTHON="$INSTALL_DIR/.venv/bin/python"
PIP="$INSTALL_DIR/.venv/bin/pip"

info()          { echo "    -> $1"; }
avert()         { echo "    !! ATTENTION : $1" >&2; }
erreur_fatale() { echo "ERREUR : $1" >&2; exit 1; }
etape()         { echo; echo "=== $1 ==="; }

# --- Vérifications préalables ------------------------------------------------
[[ "${EUID}" -eq 0 ]] || erreur_fatale "À lancer avec les droits root (sudo ./deploy/update.sh)."
[[ -d "$INSTALL_DIR/.git" ]] || erreur_fatale "$INSTALL_DIR n'est pas un dépôt git (installation introuvable ?)."
[[ -x "$PYTHON" ]] || erreur_fatale "Environnement Python introuvable ($PYTHON). Mauvais dossier d'installation ?"

# Refuse d'avancer si des fichiers SUIVIS ont été modifiés à la main sur le
# serveur : un « git pull --ff-only » échouerait, autant le dire clairement.
if ! sudo -u "$SERVICE_USER" git -C "$INSTALL_DIR" diff --quiet; then
    erreur_fatale "Des fichiers suivis ont été modifiés dans $INSTALL_DIR. Annuler ces modifications (git checkout -- .) puis relancer."
fi

# Dossier des bases, dérivé du .env (jamais codé en dur).
DATA_DIR="$(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -c 'from app.db import get_database_path; print(get_database_path().parent)')"

# --- 1. Sauvegarde de sécurité ----------------------------------------------
etape "[1/6] Sauvegarde des trois bases avant mise à jour"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/deploy/sauvegarde.sh" "$INSTALL_DIR" "$DATA_DIR/sauvegardes"

# --- 2. Récupération du code -------------------------------------------------
etape "[2/6] Récupération du nouveau code (git pull)"
sudo -u "$SERVICE_USER" git -C "$INSTALL_DIR" pull --ff-only

# --- 3. Dépendances ----------------------------------------------------------
etape "[3/6] Mise à jour des dépendances Python"
sudo -u "$SERVICE_USER" "$PIP" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$PIP" install --quiet -r "$INSTALL_DIR/requirements.txt"

# --- 4. Migrations -----------------------------------------------------------
# Idempotentes : elles n'ajoutent que ce qui manque. Jouées explicitement pour
# repérer un souci AVANT le redémarrage plutôt qu'au premier accès.
etape "[4/6] Migrations des bases (prêt, tournois, planning)"
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -m app.db)
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -m app.tournoi.db)
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -m app.planning.db)

# --- 5. Redémarrage ----------------------------------------------------------
etape "[5/6] Redémarrage du/des service(s)"
systemctl restart ludotex
info "ludotex redémarré."
# Instance de formation : redémarrée seulement si elle a été installée (ses
# propres bases sont migrées à son démarrage, via son EnvironmentFile dédié).
if systemctl list-unit-files | grep -q '^ludotex-formation\.service'; then
    systemctl restart ludotex-formation
    info "ludotex-formation redémarré."
fi

# --- 6. Vérification ---------------------------------------------------------
etape "[6/6] Vérification"
sleep 2
if systemctl is-active --quiet ludotex; then
    info "Service ludotex actif."
else
    avert "ludotex n'est PAS actif. Voir : journalctl -u ludotex -e"
fi
if curl -fsS http://127.0.0.1:8000/sante >/dev/null 2>&1; then
    info "L'application répond (/sante OK)."
else
    avert "Pas de réponse sur /sante. Voir les logs : journalctl -u ludotex -e"
fi

echo
echo "Mise à jour terminée. En cas de souci, restaurer la sauvegarde faite à"
echo "l'étape 1 depuis /admin/données, ou consulter : journalctl -u ludotex -e"
