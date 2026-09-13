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
#   6. Vérification que chaque instance répond, et avec quelle version.
#   7. Contrôle de report : ce que le dépôt porte et que ce script ne pose pas
#      (unités systemd, nginx, .env, crontab, paquets, permissions) est-il en
#      place sur le serveur ? Il NOMME les écarts, ne corrige rien, et ne peut
#      pas faire échouer la mise à jour.
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
etape "[1/7] Sauvegarde des trois bases avant mise à jour"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/deploy/sauvegarde.sh" "$INSTALL_DIR" "$DATA_DIR/sauvegardes"

# --- 2. Récupération du code -------------------------------------------------
# ATTENTION : ce pull peut réécrire CE fichier. git le remplace par un nouveau
# fichier, et bash continue de lire l'ancien jusqu'au bout : une modification
# d'update.sh ne s'applique donc qu'à la mise à jour SUIVANTE. C'est pourquoi le
# contrôle de l'étape 7 vit dans un script à part, relu à neuf à chaque appel ;
# et c'est un geste à écrire dans docs/notes-de-deploiement.md chaque fois
# qu'une version modifie ce fichier.
etape "[2/7] Récupération du nouveau code (git pull)"
sudo -u "$SERVICE_USER" git -C "$INSTALL_DIR" pull --ff-only

# --- 3. Dépendances ----------------------------------------------------------
etape "[3/7] Mise à jour des dépendances Python"
sudo -u "$SERVICE_USER" "$PIP" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$PIP" install --quiet -r "$INSTALL_DIR/requirements.txt"

# --- 4. Migrations -----------------------------------------------------------
# Idempotentes : elles n'ajoutent que ce qui manque. Jouées explicitement pour
# repérer un souci AVANT le redémarrage plutôt qu'au premier accès.
etape "[4/7] Migrations des bases (prêt, tournois, planning)"
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -m app.db)
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -m app.tournoi.db)
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -m app.planning.db)

# --- 5. Redémarrage ----------------------------------------------------------
etape "[5/7] Redémarrage du/des service(s)"
systemctl restart ludotex
info "ludotex redémarré."
# Instance de formation : redémarrée seulement si elle a été installée (ses
# propres bases sont migrées à son démarrage, via son EnvironmentFile dédié).
if systemctl list-unit-files | grep -q '^ludotex-formation\.service'; then
    systemctl restart ludotex-formation
    info "ludotex-formation redémarré."
fi

# --- 6. Vérification ---------------------------------------------------------
etape "[6/7] Vérification"
sleep 2

# Version que porte le code tout juste récupéré : chaque instance doit
# répondre avec celle-là, sinon le redémarrage n'a pas pris.
VERSION_DEPOT="$(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" -c 'from app.version import APP_VERSION; print(APP_VERSION)')" || VERSION_DEPOT=""

# Port lu dans l'unité INSTALLÉE, jamais écrit ici : c'est elle qui décide où
# l'instance écoute.
port_du_service() {
    local unite="/etc/systemd/system/$1.service"
    [[ -r "$unite" ]] || return 0
    sed -n '/--port/{s/.*--port[[:space:]=]*\([0-9][0-9]*\).*/\1/p;q;}' "$unite"
}

verifier_instance() {
    local service="$1" port reponse version
    if systemctl is-active --quiet "$service"; then
        info "Service $service actif."
    else
        avert "$service n'est PAS actif. Voir : journalctl -u $service -e"
    fi
    port="$(port_du_service "$service")" || port=""
    if [[ -z "$port" ]]; then
        avert "Port de $service introuvable dans /etc/systemd/system/$service.service : /sante non vérifié."
        return 0
    fi
    # --retry-connrefused : l'instance peut mettre quelques secondes à ouvrir
    # son port après le redémarrage ; ne pas crier avant de lui avoir laissé
    # le temps.
    if reponse="$(curl -fsS --max-time 5 --retry 10 --retry-delay 1 --retry-connrefused "http://127.0.0.1:$port/sante" 2>/dev/null)"; then
        version="$(printf '%s' "$reponse" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')" || version=""
        if [[ -z "$version" ]]; then
            info "$service répond sur /sante (port $port), sans annoncer de version."
        elif [[ -n "$VERSION_DEPOT" && "$version" != "$VERSION_DEPOT" ]]; then
            avert "$service répond en version $version, alors que le dépôt porte la $VERSION_DEPOT. Voir : journalctl -u $service -e"
        else
            info "$service répond sur /sante (port $port) : version $version."
        fi
    else
        avert "$service ne répond pas sur /sante (port $port). Voir : journalctl -u $service -e"
    fi
}

verifier_instance ludotex
# Le fichier d'unité plutôt qu'un « systemctl list-unit-files | grep -q » :
# sous pipefail, un grep qui s'arrête au premier résultat peut faire échouer
# le tube entier. C'est aussi ce fichier qui donne le port.
if [[ -f /etc/systemd/system/ludotex-formation.service ]]; then
    verifier_instance ludotex-formation
fi

# --- 7. Contrôle de report ---------------------------------------------------
# Lecture seule, sous l'utilisateur du service (il lit tout ce qu'il faut, voir
# l'en-tête du script). Le script termine toujours en code 0 ; le « || » ne
# sert qu'au cas où Python lui-même serait introuvable ou planterait, et
# empêche alors « set -e » d'interrompre la fin de la mise à jour.
etape "[7/7] Contrôle de report : le serveur porte-t-il tout ce que porte le dépôt ?"
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$PYTHON" "$INSTALL_DIR/scripts/controle_report.py" \
    --install-dir "$INSTALL_DIR" --data-dir "$DATA_DIR" --utilisateur "$SERVICE_USER") \
    || avert "Le contrôle de report n'a pas pu s'exécuter : le lancer à la main (docs/notes-de-deploiement.md)."

echo
echo "Mise à jour terminée. En cas de souci, restaurer la sauvegarde faite à"
echo "l'étape 1 depuis /admin/données, ou consulter : journalctl -u ludotex -e"
