#!/usr/bin/env bash
#
# Installation interactive de l'application de prêt de jeux sur un VPS
# Debian/Ubuntu, à exécuter APRÈS clonage du dépôt (voir docs/deploiement.md).
#
# Usage :
#   sudo ./deploy/install.sh
#
# Le script est pensé pour être relançable sans casser une installation
# existante : il redemande confirmation avant d'écraser un .env déjà présent,
# et les autres étapes (paquets, service, nginx, certbot) sont idempotentes.
#
# Ce qu'il fait, dans l'ordre :
#   1. Vérifie/installe les paquets système nécessaires (Python 3.11+, nginx,
#      certbot, git, sqlite3...).
#   2. Pose les questions de configuration (domaine, e-mail, nom de
#      l'association, mot de passe admin, chemins d'installation).
#   3. Place le code à l'emplacement choisi, génère le `.env`.
#   4. Crée l'environnement virtuel Python + installe requirements.txt.
#   5. Initialise les trois bases SQLite (prêt, tournois, planning), pose le
#      mot de passe admin (haché, en base seulement) et génère le jeton
#      bénévole (validité 1 semaine).
#   6. Installe le service systemd et la configuration nginx.
#   7. Obtient le certificat HTTPS Let's Encrypt.
#   8. Propose la sauvegarde automatique (3h et 15h).
#   9. Affiche le lien d'activation bénévole et les prochaines étapes.
#
# Détail de chaque étape manuelle équivalente : docs/deploiement.md.

set -euo pipefail

# ============================================================================
# Constantes
# ============================================================================

# Dépôt du projet — fixé en dur (pas de question à l'utilisateur : une seule
# association utilise ce dépôt).
DEPOT_URL="https://github.com/Dramac/LudoteX"

INSTALL_DIR_DEFAUT="/opt/ludotex"
DATA_DIR_DEFAUT="/var/lib/ludotex"
SERVICE_USER="pretjeux"

# Répertoire où vit CE script au moment de l'exécution (racine du dépôt cloné,
# un niveau au-dessus de deploy/).
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ============================================================================
# Affichage
# ============================================================================
NB_ETAPES=10
ETAPE_COURANTE=0

etape() {
    ETAPE_COURANTE=$((ETAPE_COURANTE + 1))
    echo
    echo "=== [$ETAPE_COURANTE/$NB_ETAPES] $1 ==="
}

info()  { echo "    -> $1"; }
avert() { echo "    !! ATTENTION : $1" >&2; }
erreur_fatale() { echo "ERREUR : $1" >&2; exit 1; }

# ============================================================================
# 0. Doit être lancé en root (sudo)
# ============================================================================
if [[ "${EUID}" -ne 0 ]]; then
    erreur_fatale "Ce script doit être exécuté avec les droits root (sudo ./deploy/install.sh)."
fi

echo "############################################################"
echo "#  Installation — LudoteX, prêt de jeux de société         #"
echo "############################################################"
echo
echo "Ce script va configurer le serveur et déployer l'application."
echo "Répondre aux questions ci-dessous (une valeur entre crochets"
echo "est la valeur par défaut : appuyer sur Entrée pour la garder)."

# ============================================================================
# 1. Prérequis système
# ============================================================================
etape "Vérification des prérequis système"

info "Mise à jour de la liste des paquets (apt update)..."
apt-get update -y >/dev/null

# Paquets nécessaires à l'ensemble du processus. apt n'installe que ce qui
# manque réellement (idempotent) : pas besoin de tester chacun un par un.
# build-essential/libjpeg-dev/zlib1g-dev évitent un échec de compilation de
# Pillow (dépendance de qrcode[pil]) si aucune roue précompilée n'existe pour
# l'architecture du VPS.
PAQUETS_BASE=(
    python3 python3-venv python3-pip python3-dev
    git nginx sqlite3 certbot python3-certbot-nginx
    ufw curl dnsutils
    build-essential libjpeg-dev zlib1g-dev
)
info "Installation des paquets de base : ${PAQUETS_BASE[*]}"
apt-get install -y "${PAQUETS_BASE[@]}" >/dev/null

# Python 3.11+ : Debian 12 et Ubuntu 22.04+ le proposent nativement. On tente
# de l'installer explicitement (au cas où seul un python3 plus ancien serait
# présent par défaut) puis on choisit le binaire le plus récent disponible.
version_python_ok() {
    "$1" -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

PYTHON_BIN=""
for candidat in python3.12 python3.11 python3; do
    if command -v "$candidat" >/dev/null 2>&1 && version_python_ok "$candidat"; then
        PYTHON_BIN="$(command -v "$candidat")"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    info "Aucun Python >= 3.11 trouvé, tentative d'installation de python3.11..."
    apt-get install -y python3.11 python3.11-venv >/dev/null 2>&1 || true
    if command -v python3.11 >/dev/null 2>&1 && version_python_ok python3.11; then
        PYTHON_BIN="$(command -v python3.11)"
    fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
    erreur_fatale "Python 3.11 ou supérieur est requis et n'a pas pu être installé automatiquement. Installer python3.11 manuellement puis relancer ce script."
fi
info "Python retenu : $PYTHON_BIN ($($PYTHON_BIN --version))"

# Pare-feu : SSH + web, sans casser une configuration déjà active.
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 'Nginx Full' >/dev/null 2>&1 || true
if ! ufw status | grep -q "Status: active"; then
    info "Activation du pare-feu (ufw) : SSH + HTTP/HTTPS autorisés."
    ufw --force enable >/dev/null
else
    info "Pare-feu déjà actif : règles SSH/Nginx ajoutées si besoin."
fi

# ============================================================================
# 2. Questions de configuration
# ============================================================================
etape "Questions de configuration"

demander() {
    # demander "Question" "defaut" -> écrit la réponse dans REPONSE
    local question="$1" defaut="${2:-}" saisie
    if [[ -n "$defaut" ]]; then
        read -r -p "$question [$defaut] : " saisie
        REPONSE="${saisie:-$defaut}"
    else
        while true; do
            read -r -p "$question : " saisie
            if [[ -n "$saisie" ]]; then
                REPONSE="$saisie"
                break
            fi
            echo "    (obligatoire, ne peut pas rester vide)"
        done
    fi
}

echo
echo "--- Nom de domaine ---"
demander "Domaine de l'application (ex. jeux.monasso.fr)" ""
DOMAINE="$REPONSE"

echo
echo "--- Contact Let's Encrypt ---"
while true; do
    demander "Adresse e-mail (alertes de renouvellement du certificat HTTPS)" ""
    if [[ "$REPONSE" == *"@"*"."* ]]; then
        EMAIL="$REPONSE"
        break
    fi
    echo "    (adresse e-mail invalide, réessayer)"
done

echo
echo "--- Association ---"
demander "Nom de l'association (affiché dans le bandeau du site)" ""
NOM_ASSOCIATION="$REPONSE"

echo
echo "--- Mot de passe administrateur ---"
echo "    (donne accès à /admin : création de fiches, jeton bénévole, exports...)"
# Il n'est écrit dans AUCUN fichier : il est haché en base à l'étape 5 (SEC-17).
# Le seuil 8 est une COPIE de admin_auth.LONGUEUR_MIN_MDP, verrouillée par
# tests/test_porte_administration.py : les changer ensemble.
while true; do
    read -r -s -p "Mot de passe administrateur : " ADMIN_PASSWORD; echo
    if [[ ${#ADMIN_PASSWORD} -lt 8 ]]; then
        echo "    (au moins 8 caractères, réessayer)"
        continue
    fi
    read -r -s -p "Confirmer le mot de passe : " ADMIN_PASSWORD_CONFIRM; echo
    if [[ "$ADMIN_PASSWORD" == "$ADMIN_PASSWORD_CONFIRM" ]]; then
        break
    fi
    echo "    (les deux saisies ne correspondent pas, réessayer)"
done

echo
echo "--- Emplacements sur le serveur ---"
demander "Chemin d'installation de l'application" "$INSTALL_DIR_DEFAUT"
INSTALL_DIR="$REPONSE"

# Un .env déjà présent : on demande MAINTENANT s'il faut l'écraser, parce que
# la réponse décide de la question suivante. Conservé, c'est LUI qui dit où
# sont les bases (DATABASE_PATH) : le chemin est lu après l'installation de
# Python (étape 5), exactement comme update.sh le lit, et n'est pas redemandé.
# Le demander ici permettait d'y répondre autre chose que le .env : le dossier
# 0700 des sauvegardes, les bases de formation et le message final seraient
# alors partis sur un chemin qui n'est pas celui des bases.
ENV_FILE="$INSTALL_DIR/.env"
GENERER_ENV=1
if [[ -f "$ENV_FILE" ]]; then
    read -r -p ".env existe déjà à $ENV_FILE. L'écraser ? [o/N] : " ECRASER
    if [[ "${ECRASER,,}" != o* ]]; then
        GENERER_ENV=0
    fi
fi

if [[ "$GENERER_ENV" -eq 1 ]]; then
    demander "Chemin de stockage des bases SQLite" "$DATA_DIR_DEFAUT"
    DATA_DIR="$REPONSE"
    LIBELLE_DATA_DIR="$DATA_DIR"
else
    DATA_DIR=""   # lu dans le .env conservé, à l'étape 5
    LIBELLE_DATA_DIR="celui du .env conservé (DATABASE_PATH)"
fi

echo
echo "Récapitulatif :"
echo "  Domaine             : $DOMAINE"
echo "  E-mail (Let's Encrypt): $EMAIL"
echo "  Association          : $NOM_ASSOCIATION"
echo "  Dépôt                 : $DEPOT_URL"
echo "  Installation          : $INSTALL_DIR"
echo "  Bases SQLite          : $LIBELLE_DATA_DIR"
echo
read -r -p "Continuer avec ces valeurs ? [O/n] : " CONFIRME
if [[ "${CONFIRME,,}" == n* ]]; then
    echo "Installation annulée."
    exit 0
fi

# ============================================================================
# 3. Mise en place du code
# ============================================================================
etape "Mise en place du code à $INSTALL_DIR"

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    info "Création de l'utilisateur système '$SERVICE_USER' (sans login)."
    adduser --system --group "$SERVICE_USER"
else
    info "Utilisateur système '$SERVICE_USER' déjà présent."
fi

mkdir -p "$INSTALL_DIR"

if [[ ! -f "$SOURCE_DIR/requirements.txt" ]]; then
    # Cas de secours : le script n'est pas lancé depuis un clone valide du
    # dépôt (ex. copié isolément sur le serveur). On clone directement.
    info "Dépôt source introuvable autour du script : clonage direct depuis $DEPOT_URL."
    git clone "$DEPOT_URL" "$INSTALL_DIR"
elif [[ "$SOURCE_DIR" == "$INSTALL_DIR" ]]; then
    info "Le script est déjà exécuté depuis $INSTALL_DIR : rien à copier."
elif [[ -d "$INSTALL_DIR/.git" ]]; then
    info "$INSTALL_DIR contient déjà un dépôt git : mise à jour (git pull)."
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "Copie du dépôt cloné ($SOURCE_DIR) vers $INSTALL_DIR..."
    cp -a "$SOURCE_DIR"/. "$INSTALL_DIR"/
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ============================================================================
# 4. Fichier .env
# ============================================================================
etape "Génération du fichier .env"

# Écraser ou non : décidé à l'étape 2 (GENERER_ENV).
if [[ "$GENERER_ENV" -eq 0 ]]; then
    info ".env conservé tel quel."
fi

if [[ "$GENERER_ENV" -eq 1 ]]; then
    # Jeton bénévole de démarrage : sera remplacé par un jeton définitif (avec
    # expiration à 1 semaine) une fois les bases initialisées (étape 5). On
    # écrit ici une valeur temporaire aléatoire, jamais le placeholder du
    # .env.example, pour ne pas laisser passer un déploiement "mode ouvert".
    JETON_TEMPORAIRE="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"

    mkdir -p "$DATA_DIR"
    chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"

    cat > "$ENV_FILE" <<EOF
# Fichier généré par deploy/install.sh le $(date -Iseconds)
# Ne JAMAIS committer ce fichier (déjà exclu par .gitignore).

# --- Jeton d'écriture bénévole ---------------------------------------
# Remplacé par un jeton définitif (expiration 1 semaine) juste après
# l'initialisation des bases ; cette valeur ne sert qu'à l'amorçage.
PRET_TOKEN=$JETON_TEMPORAIRE

# --- Mot de passe administrateur -------------------------------------
# Volontairement absent : il est haché en base à l'installation, et ne vit
# nulle part en clair. Oublié : scripts/reinitialiser_mot_de_passe.py (voir
# docs/deploiement.md, « Mot de passe admin oublié »).

# --- Bases de données -------------------------------------------------
DATABASE_PATH=$DATA_DIR/pret-jeux.db
TOURNOI_DATABASE_PATH=$DATA_DIR/tournoi.db
PLANNING_DATABASE_PATH=$DATA_DIR/planning.db

# --- Journal d'activité -------------------------------------------------
JOURNAL_PATH=$DATA_DIR/journal.log
JOURNAL_CONSOLE=0

# --- Domaine / URL publique ------------------------------------------
BASE_URL=https://$DOMAINE

# --- Nom de l'association ---------------------------------------------
NOM_ASSOCIATION=$NOM_ASSOCIATION

# --- Limitation de débit (écriture) ----------------------------------
RATE_LIMIT_PER_MINUTE=60

# --- Environnement ---------------------------------------------------
APP_ENV=production
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    info ".env généré ($ENV_FILE)."
fi

# ============================================================================
# 5. Environnement Python + initialisation des bases
# ============================================================================
etape "Environnement Python et initialisation des bases"

if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    info "Création de l'environnement virtuel..."
    sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
fi

info "Installation des dépendances (requirements.txt)..."
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# .env conservé : le dossier des bases est celui que l'application y lit,
# par la même fonction qu'update.sh (app.db.get_database_path), résolu en
# chemin absolu depuis le dossier d'installation — jamais une seconde lecture
# du fichier en bash, qui pourrait l'interpréter autrement que l'application.
if [[ "$GENERER_ENV" -eq 0 ]]; then
    DATA_DIR="$(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -c \
        'from app.db import get_database_path; print(get_database_path().parent.resolve())')" || DATA_DIR=""
    if [[ -z "$DATA_DIR" ]]; then
        erreur_fatale "Impossible de lire le dossier des bases dans $ENV_FILE (DATABASE_PATH). Le vérifier, ou relancer en acceptant de l'écraser."
    fi
    info "Dossier des bases, lu dans le .env conservé : $DATA_DIR"
fi

info "Initialisation de la base de prêt (app.db)..."
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m app.db)

info "Initialisation de la base des tournois (app.tournoi.db)..."
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m app.tournoi.db)

info "Initialisation de la base du planning (app.planning.db)..."
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m app.planning.db)

# Mot de passe admin : haché directement en base, jamais écrit dans .env
# (SEC-17). Passé sur l'entrée standard — ni en argument, ni en variable
# d'environnement, qui s'afficheraient dans la liste des processus.
# `--si-absent` : une base qui a déjà un mot de passe le GARDE, comme avant ce
# changement (ADMIN_PASSWORD n'était lue que sans hash en base). Pour changer
# un mot de passe existant, c'est le script seul, sans cette option.
poser_mot_de_passe_admin() {
    # $@ : la commande python de l'instance (production ou formation).
    if ! "$@" "$INSTALL_DIR/scripts/reinitialiser_mot_de_passe.py" --stdin --si-absent \
            <<< "$ADMIN_PASSWORD"; then
        avert "Mot de passe admin NON posé. L'écran /admin le signalera ; le poser avec :"
        avert "  cd $INSTALL_DIR && sudo -u $SERVICE_USER .venv/bin/python scripts/reinitialiser_mot_de_passe.py"
    fi
}
info "Mot de passe administrateur (base de prêt)..."
(cd "$INSTALL_DIR" && poser_mot_de_passe_admin sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python")

if [[ "$GENERER_ENV" -eq 1 ]]; then
    info "Génération du jeton bénévole définitif (expiration : 1 semaine)..."
    # Le code est passé au python de pretjeux via STDIN (heredoc), sans fichier
    # temporaire : un mktemp créé par root (droits 600) serait illisible par
    # l'utilisateur pretjeux -> "Permission denied".
    JETON="$(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" - <<'PYEOF'
from app.db import get_connection
from app import auth

conn = get_connection()
try:
    jeton = auth.reinitialiser_jeton(conn)
finally:
    conn.close()
print(jeton)
PYEOF
)"
    # On aligne le .env sur le jeton réellement actif (purement informatif :
    # l'application lit d'abord la base, cf app/auth.jeton_actuel).
    sed -i "s#^PRET_TOKEN=.*#PRET_TOKEN=$JETON#" "$ENV_FILE"
else
    info "Jeton non régénéré (.env existant conservé) — voir /admin/jeton si besoin."
    JETON="(voir /admin/jeton sur le site pour le lien d'activation)"
fi

# ============================================================================
# 6. Service systemd
# ============================================================================
etape "Service systemd"

cp "$INSTALL_DIR/deploy/ludotex.service" /etc/systemd/system/ludotex.service
sed -i "s#/opt/ludotex#${INSTALL_DIR}#g" /etc/systemd/system/ludotex.service

systemctl daemon-reload
systemctl enable --now ludotex
sleep 1
if systemctl is-active --quiet ludotex; then
    info "Service ludotex actif."
else
    avert "Le service ludotex ne semble pas démarré. Voir : journalctl -u ludotex -e"
fi

# ============================================================================
# 7. nginx + HTTPS
# ============================================================================
etape "Configuration nginx et certificat HTTPS"

cp "$INSTALL_DIR/deploy/nginx-ludotex.conf" /etc/nginx/sites-available/ludotex
sed -i "s#/opt/ludotex#${INSTALL_DIR}#g" /etc/nginx/sites-available/ludotex
sed -i "s/pret\.example\.fr/${DOMAINE}/g" /etc/nginx/sites-available/ludotex

ln -sf /etc/nginx/sites-available/ludotex /etc/nginx/sites-enabled/ludotex
nginx -t
systemctl reload nginx
info "nginx configuré pour $DOMAINE."

IP_SERVEUR="$(curl -s -4 ifconfig.me || true)"
IP_DOMAINE="$(dig +short "$DOMAINE" | tail -n1 || true)"
if [[ -n "$IP_SERVEUR" && -n "$IP_DOMAINE" && "$IP_SERVEUR" != "$IP_DOMAINE" ]]; then
    avert "Le DNS de $DOMAINE (résout vers $IP_DOMAINE) ne pointe pas encore vers cette machine ($IP_SERVEUR)."
    avert "Le certificat Let's Encrypt va probablement échouer tant que le DNS n'est pas propagé."
    read -r -p "Tenter quand même l'obtention du certificat maintenant ? [o/N] : " TENTER_CERTBOT
else
    TENTER_CERTBOT="o"
fi

if [[ "${TENTER_CERTBOT,,}" == o* ]]; then
    if certbot --nginx -d "$DOMAINE" -m "$EMAIL" --agree-tos --redirect --non-interactive; then
        info "Certificat HTTPS obtenu pour $DOMAINE."
    else
        avert "Échec de l'obtention du certificat. Réessayer plus tard avec :"
        avert "  sudo certbot --nginx -d $DOMAINE -m $EMAIL"
    fi
else
    info "Certificat HTTPS non demandé. Une fois le DNS propagé, lancer :"
    info "  sudo certbot --nginx -d $DOMAINE -m $EMAIL"
fi

# ============================================================================
# 8. Sauvegarde automatique
# ============================================================================
etape "Sauvegarde automatique"

chmod +x "$INSTALL_DIR/deploy/sauvegarde.sh"

# SEC-11 (audit du 24/07/2026) : ce dossier reçoit à la fois les sauvegardes
# de routine (ludotex-backup-*.zip, si le minuteur ci-dessous est accepté),
# les filets posés par update.sh AVANT CHAQUE mise à jour
# (avant-mise-a-jour-*.zip) ET les filets posés par l'application AVANT
# CHAQUE restauration (avant-restauration-*.zip, voir
# app.sauvegarde.sauvegarde_de_securite — dossier dérivé du chemin de
# DATABASE_PATH, donc systématiquement $DATA_DIR/sauvegardes quelle que
# soit l'acceptation du minuteur). Ces TROIS types d'archive contiennent
# les TROIS bases en clair, dont les noms et contacts du planning et les
# numéros de pochette des prêts EN COURS au moment de chaque archive (D5 ne
# les efface qu'à la clôture) — sensible, donc posé en 0700 propriétaire du
# service, créé ici
# de façon inconditionnelle (l'app peut créer ce dossier à tout moment via
# une restauration, avec les permissions par défaut du umask si on ne le
# fait pas nous-mêmes en amont).
SAUVEGARDES_DIR="$DATA_DIR/sauvegardes"
mkdir -p "$SAUVEGARDES_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$SAUVEGARDES_DIR"
chmod 700 "$SAUVEGARDES_DIR"
info "Dossier des sauvegardes sécurisé (0700, propriétaire $SERVICE_USER) : $SAUVEGARDES_DIR"

# La sauvegarde de nuit est un minuteur systemd (deploy/ludotex-sauvegarde.timer),
# plus une ligne de cron. L'ancienne ligne écrivait son journal dans /var/log/,
# où l'utilisateur du service ne peut pas créer de fichier : la redirection
# échouait avant le lancement du script, sans aucun message visible, et aucune
# archive de nuit n'a jamais été produite. Voir ludotex-sauvegarde.service.

# La crontab du service, amputée de toute ligne active qui lance sauvegarde.sh
# (les commentaires et les autres tâches sont gardés tels quels). Un seul awk
# qui lit toute l'entrée : pas de « grep -q » en fin de tube, qui peut faire
# échouer le tube entier sous pipefail.
CRON_MARQUE="deploy/sauvegarde.sh"
crontab_sans_sauvegarde() {
    printf '%s\n' "$1" | awk -v marque="$CRON_MARQUE" '/^[[:space:]]*#/ || index($0, marque) == 0'
}

# Retire l'ancienne tâche cron de sauvegarde, quelle que soit sa redirection :
# cassée, elle ne sert à rien ; réparée à la main, elle doublerait le minuteur
# (deux archives par nuit). Sans ligne à retirer, ne touche à rien : relancé,
# ce bloc ne fait rien de plus.
retirer_ancienne_tache_cron() {
    local actuel restant
    actuel="$(crontab -u "$SERVICE_USER" -l 2>/dev/null || true)"
    restant="$(crontab_sans_sauvegarde "$actuel")"
    if [[ "$restant" == "$(printf '%s\n' "$actuel" | awk '1')" ]]; then
        return 0
    fi
    if [[ -z "${restant//[[:space:]]/}" ]]; then
        crontab -u "$SERVICE_USER" -r
    else
        printf '%s\n' "$restant" | crontab -u "$SERVICE_USER" -
    fi
    info "Ancienne tâche cron de sauvegarde retirée de la crontab de $SERVICE_USER (remplacée par le minuteur)."
}

read -r -p "Configurer la sauvegarde automatique (3h et 15h, chaque jour) ? [O/n] : " CONFIG_SAUVEGARDE
if [[ "${CONFIG_SAUVEGARDE,,}" != n* ]]; then
    for UNITE in ludotex-sauvegarde.service ludotex-sauvegarde.timer; do
        cp "$INSTALL_DIR/deploy/$UNITE" "/etc/systemd/system/$UNITE"
        sed -i "s#/opt/ludotex#${INSTALL_DIR}#g" "/etc/systemd/system/$UNITE"
    done
    systemctl daemon-reload
    systemctl enable --now ludotex-sauvegarde.timer
    # Seulement une fois le minuteur activé : si l'activation échoue, le script
    # s'arrête là et l'ancienne tâche reste en place.
    retirer_ancienne_tache_cron
    if systemctl is-active --quiet ludotex-sauvegarde.timer; then
        info "Sauvegarde programmée à 3h et à 15h vers $SAUVEGARDES_DIR."
        info "Prochain passage : systemctl list-timers ludotex-sauvegarde.timer"
    else
        avert "Le minuteur de sauvegarde ne semble pas actif. Voir : systemctl status ludotex-sauvegarde.timer"
    fi
else
    info "Sauvegarde automatique non configurée. Voir docs/deploiement.md pour la mettre en place plus tard."
    CRON_ACTUEL="$(crontab -u "$SERVICE_USER" -l 2>/dev/null || true)"
    if [[ "$(crontab_sans_sauvegarde "$CRON_ACTUEL")" != "$(printf '%s\n' "$CRON_ACTUEL" | awk '1')" ]]; then
        avert "La crontab de $SERVICE_USER lance encore sauvegarde.sh : ancienne méthode, laissée en place."
        avert "Relancer ce script en acceptant la sauvegarde automatique pour la remplacer par le minuteur."
    fi
fi

# ============================================================================
# 9. Site de formation (optionnel)
# ============================================================================
etape "Site de formation (optionnel)"

echo
echo "Le site de formation est une SECONDE INSTANCE de l'application (même"
echo "code), avec ses propres données jetables, pour former les nouveaux"
echo "bénévoles sans risque de toucher aux vraies données. Voir docs/mode-formation.md."
read -r -p "Installer aussi le site de formation ? [o/N] : " INSTALLER_FORMATION

if [[ "${INSTALLER_FORMATION,,}" == o* ]]; then
    demander "Sous-domaine du site de formation" "formation.$DOMAINE"
    DOMAINE_FORMATION="$REPONSE"
    DATA_DIR_FORMATION="${DATA_DIR}-formation"

    info "Bases jetables : $DATA_DIR_FORMATION"
    mkdir -p "$DATA_DIR_FORMATION"
    chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR_FORMATION"

    # SEC-11 : même précaution que pour la production (voir l'étape
    # « Sauvegarde automatique » ci-dessus) — l'app.sauvegarde de cette
    # instance écrit ses propres filets de sécurité dans
    # $DATA_DIR_FORMATION/sauvegardes dès qu'une restauration y est faite.
    # Données fictives (moins sensible que la production), mais coût nul.
    mkdir -p "$DATA_DIR_FORMATION/sauvegardes"
    chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR_FORMATION/sauvegardes"
    chmod 700 "$DATA_DIR_FORMATION/sauvegardes"

    # Fichier d'environnement de l'instance : lu par systemd (EnvironmentFile)
    # ET par l'application, qui en fait son SEUL fichier (LUDOTEX_ENV_FILE,
    # voir deploy/ludotex-formation.service et app/environnement.py) : le .env
    # de production n'est jamais relu derrière lui. Il doit donc porter TOUTES
    # les clés attendues par .env.example ; le contrôle de report le vérifie à
    # chaque mise à jour, et tests/test_environnement.py le vérifie sur ce
    # modèle. Écrit AVANT les initialisations ci-dessous, qui le lisent.
    ENV_FORMATION="/etc/ludotex-formation.env"
    # Valeurs entre guillemets : valable pour systemd (EnvironmentFile,
    # cf. systemd.exec(5)), pour python-dotenv ET pour un `source` bash manuel
    # de dépannage — important pour NOM_ASSOCIATION, qui contient des espaces.
    cat > "$ENV_FORMATION" <<EOF
# Fichier généré par deploy/install.sh le $(date -Iseconds)
# Variables de l'INSTANCE DE FORMATION, et d'elle seule : ni la production ni
# son .env n'y ajoutent quoi que ce soit (voir deploy/ludotex-formation.service).
# Une clé ajoutée à .env.example doit être reportée ici aussi.

# Accès bénévole OUVERT : aucun jeton exigé, plus simple pour la formation
# (aucune donnée réelle n'est en jeu). La ligne est présente et VIDE à dessein :
# vide = pas de jeton. Pour fermer ce site, y mettre une valeur, ou réinitialiser
# le jeton depuis son écran d'administration.
PRET_TOKEN=
RATE_LIMIT_PER_MINUTE=60
MODE_FORMATION=1
DATABASE_PATH="$DATA_DIR_FORMATION/pret-jeux.db"
TOURNOI_DATABASE_PATH="$DATA_DIR_FORMATION/tournoi.db"
PLANNING_DATABASE_PATH="$DATA_DIR_FORMATION/planning.db"
# Chemin DISTINCT de celui de la production (voir plus haut) : sans quoi les
# deux instances écriraient dans le même fichier journal.
JOURNAL_PATH="$DATA_DIR_FORMATION/journal.log"
JOURNAL_CONSOLE=0
BASE_URL="https://$DOMAINE_FORMATION"
NOM_ASSOCIATION="$NOM_ASSOCIATION"
APP_ENV=production
# Catalogue recopié dans la base de formation, à la place des jeux fictifs.
# Décommentez cette ligne APRÈS avoir déposé le fichier (export du catalogue
# depuis « Données & sauvegarde » de la production), puis redémarrez le service
# et réinitialisez les données de formation. Sans lui, une vraie boîte scannée
# pendant une formation affiche « boîte inconnue » : les QR imprimés portent
# les identifiants du vrai catalogue. Voir docs/mode-formation.md.
# FORMATION_CATALOGUE_CSV="$DATA_DIR_FORMATION/catalogue.csv"
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FORMATION"
    chmod 600 "$ENV_FORMATION"
    info "$ENV_FORMATION généré."

    # Appelle un module Python sur l'instance de FORMATION, avec le fichier
    # ci-dessus pour seul environnement : exactement ce que verra le service.
    # `sudo` repart d'un environnement vierge, rien de la session courante ne
    # s'y ajoute. Aucun mot de passe ici (SEC-17) : il est posé plus bas, sur
    # l'entrée standard.
    run_python_formation() {
        sudo -u "$SERVICE_USER" env LUDOTEX_ENV_FILE="$ENV_FORMATION" \
            "$INSTALL_DIR/.venv/bin/python" "$@"
    }

    info "Initialisation des bases de formation..."
    (cd "$INSTALL_DIR" && run_python_formation -m app.db)
    (cd "$INSTALL_DIR" && run_python_formation -m app.tournoi.db)
    (cd "$INSTALL_DIR" && run_python_formation -m app.planning.db)

    info "Peuplement des données de démonstration (jeux fictifs, prêts, tournoi)..."
    (cd "$INSTALL_DIR" && run_python_formation -m app.formation)

    # Même mot de passe que la production. La réinitialisation des données de
    # formation ne vide pas `parametres` : il survit (test_porte_administration).
    info "Mot de passe administrateur (base de formation)..."
    (cd "$INSTALL_DIR" && poser_mot_de_passe_admin run_python_formation)

    info "Service systemd ludotex-formation..."
    cp "$INSTALL_DIR/deploy/ludotex-formation.service" /etc/systemd/system/ludotex-formation.service
    sed -i "s#/opt/ludotex#${INSTALL_DIR}#g" /etc/systemd/system/ludotex-formation.service
    systemctl daemon-reload
    systemctl enable --now ludotex-formation
    sleep 1
    if systemctl is-active --quiet ludotex-formation; then
        info "Service ludotex-formation actif."
    else
        avert "Le service ludotex-formation ne semble pas démarré. Voir : journalctl -u ludotex-formation -e"
    fi

    info "Configuration nginx pour $DOMAINE_FORMATION..."
    cp "$INSTALL_DIR/deploy/nginx-ludotex-formation.conf" /etc/nginx/sites-available/ludotex-formation
    sed -i "s#/opt/ludotex#${INSTALL_DIR}#g" /etc/nginx/sites-available/ludotex-formation
    sed -i "s/formation\.pret\.example\.fr/${DOMAINE_FORMATION}/g" /etc/nginx/sites-available/ludotex-formation
    ln -sf /etc/nginx/sites-available/ludotex-formation /etc/nginx/sites-enabled/ludotex-formation
    nginx -t
    systemctl reload nginx

    IP_SERVEUR_F="$(curl -s -4 ifconfig.me || true)"
    IP_DOMAINE_F="$(dig +short "$DOMAINE_FORMATION" | tail -n1 || true)"
    if [[ -n "$IP_SERVEUR_F" && -n "$IP_DOMAINE_F" && "$IP_SERVEUR_F" == "$IP_DOMAINE_F" ]]; then
        if certbot --nginx -d "$DOMAINE_FORMATION" -m "$EMAIL" --agree-tos --redirect --non-interactive; then
            info "Certificat HTTPS obtenu pour $DOMAINE_FORMATION."
        else
            avert "Échec de l'obtention du certificat pour $DOMAINE_FORMATION. Réessayer plus tard avec :"
            avert "  sudo certbot --nginx -d $DOMAINE_FORMATION -m $EMAIL"
        fi
    else
        avert "Le DNS de $DOMAINE_FORMATION ne pointe pas (encore) vers ce serveur : certificat non demandé."
        avert "Une fois le DNS propagé : sudo certbot --nginx -d $DOMAINE_FORMATION -m $EMAIL"
    fi

    # Lien affiché au tableau de bord admin de la PRODUCTION.
    if grep -q '^FORMATION_URL=' "$ENV_FILE" 2>/dev/null; then
        sed -i "s#^FORMATION_URL=.*#FORMATION_URL=https://$DOMAINE_FORMATION#" "$ENV_FILE"
    else
        echo "FORMATION_URL=https://$DOMAINE_FORMATION" >> "$ENV_FILE"
    fi
    systemctl restart ludotex

    FORMATION_URL_FINALE="https://$DOMAINE_FORMATION"
    info "Site de formation prêt : $FORMATION_URL_FINALE"
    info "Pour que les QR déjà imprimés fonctionnent aussi sur ce site, voir"
    info "  la section « Faire fonctionner les QR imprimés » de docs/mode-formation.md."
else
    info "Site de formation non installé. Réalisable plus tard, voir docs/mode-formation.md."
    FORMATION_URL_FINALE=""
fi

# ============================================================================
# 10. Récapitulatif final
# ============================================================================
etape "Terminé"

echo
echo "############################################################"
echo "#  Installation terminée                                   #"
echo "############################################################"
echo
echo "Site                 : https://$DOMAINE"
echo "Espace admin         : https://$DOMAINE/admin  (mot de passe défini ci-dessus,"
echo "                       ou celui déjà en place si la base en avait un)"
echo "Lien d'activation bénévole (à partager aux bénévoles) :"
echo "  https://$DOMAINE/acces?jeton=$JETON"
echo "  (valable 1 semaine ; renouvelable depuis /admin/jeton)"
echo
echo "Prochaines étapes :"
echo "  - Importer le catalogue de jeux :"
echo "      cd $INSTALL_DIR && sudo -u $SERVICE_USER .venv/bin/python -m scripts.import_csv <catalogue.csv>"
echo "  - Une fois le domaine confirmé, imprimer les QR définitifs :"
echo "      cd $INSTALL_DIR && sudo -u $SERVICE_USER .venv/bin/python -m scripts.generate_qr --planche --grille 8x2"
echo "  - Vérifier le service : sudo systemctl status ludotex"
echo "  - Suivre les logs      : sudo journalctl -u ludotex -f"
echo
if [[ -n "$FORMATION_URL_FINALE" ]]; then
    echo "Site de formation    : $FORMATION_URL_FINALE  (accès ouvert, données fictives)"
    echo "  - Réinitialiser ses données : bouton dans son tableau de bord admin,"
    echo "    ou : cd $INSTALL_DIR && sudo -u $SERVICE_USER env LUDOTEX_ENV_FILE=/etc/ludotex-formation.env .venv/bin/python -m app.formation"
    echo "  - QR d'entraînement (optionnel) :"
    echo "      cd $INSTALL_DIR && sudo -u $SERVICE_USER env LUDOTEX_ENV_FILE=/etc/ludotex-formation.env .venv/bin/python -m scripts.generate_qr --base-url $FORMATION_URL_FINALE --planche"
    echo
fi
echo "Détails et dépannage : docs/deploiement.md"
