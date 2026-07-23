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
#   5. Initialise les trois bases SQLite (prêt, tournois, planning) et génère
#      le jeton bénévole (validité 1 semaine).
#   6. Installe le service systemd et la configuration nginx.
#   7. Obtient le certificat HTTPS Let's Encrypt.
#   8. Propose la sauvegarde quotidienne automatique.
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
echo "#  Installation — Application de prêt de jeux (Des jeux    #"
echo "#  plein la Manche)                                        #"
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
demander "Nom de l'association (affiché dans le bandeau du site)" "Des jeux plein la Manche"
NOM_ASSOCIATION="$REPONSE"

echo
echo "--- Mot de passe administrateur ---"
echo "    (donne accès à /admin : création de fiches, jeton bénévole, exports...)"
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
demander "Chemin de stockage des bases SQLite" "$DATA_DIR_DEFAUT"
DATA_DIR="$REPONSE"

echo
echo "Récapitulatif :"
echo "  Domaine             : $DOMAINE"
echo "  E-mail (Let's Encrypt): $EMAIL"
echo "  Association          : $NOM_ASSOCIATION"
echo "  Dépôt                 : $DEPOT_URL"
echo "  Installation          : $INSTALL_DIR"
echo "  Bases SQLite          : $DATA_DIR"
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

ENV_FILE="$INSTALL_DIR/.env"
GENERER_ENV=1
if [[ -f "$ENV_FILE" ]]; then
    read -r -p ".env existe déjà à $ENV_FILE. L'écraser ? [o/N] : " ECRASER
    if [[ "${ECRASER,,}" != o* ]]; then
        GENERER_ENV=0
        info ".env conservé tel quel."
    fi
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
ADMIN_PASSWORD=$ADMIN_PASSWORD

# --- Bases de données -------------------------------------------------
DATABASE_PATH=$DATA_DIR/pret-jeux.db
TOURNOI_DATABASE_PATH=$DATA_DIR/tournoi.db
PLANNING_DATABASE_PATH=$DATA_DIR/planning.db

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

info "Initialisation de la base de prêt (app.db)..."
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m app.db)

info "Initialisation de la base des tournois (app.tournoi.db)..."
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m app.tournoi.db)

info "Initialisation de la base du planning (app.planning.db)..."
(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m app.planning.db)

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
read -r -p "Configurer la sauvegarde quotidienne automatique (3h du matin) ? [O/n] : " CONFIG_SAUVEGARDE
if [[ "${CONFIG_SAUVEGARDE,,}" != n* ]]; then
    LIGNE_CRON="0 3 * * * $INSTALL_DIR/deploy/sauvegarde.sh $INSTALL_DIR $DATA_DIR/sauvegardes >> /var/log/ludotex-sauvegarde.log 2>&1"
    CRON_ACTUEL="$(crontab -u "$SERVICE_USER" -l 2>/dev/null || true)"
    if echo "$CRON_ACTUEL" | grep -qF "sauvegarde.sh"; then
        info "Une tâche de sauvegarde existe déjà dans le crontab de $SERVICE_USER."
    else
        { echo "$CRON_ACTUEL"; echo "$LIGNE_CRON"; } | grep -v '^$' | crontab -u "$SERVICE_USER" -
        info "Sauvegarde quotidienne programmée (3h) vers $DATA_DIR/sauvegardes."
    fi
else
    info "Sauvegarde automatique non configurée. Voir docs/deploiement.md pour la mettre en place plus tard."
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

    # Appelle un module Python sur l'instance de FORMATION, sans jamais passer
    # par le .env de production (variables injectées directement, chacune un
    # argument bash correctement quoté -> aucun souci avec les valeurs
    # contenant des espaces, ex. NOM_ASSOCIATION).
    run_python_formation() {
        sudo -u "$SERVICE_USER" env \
            MODE_FORMATION=1 \
            ADMIN_PASSWORD="$ADMIN_PASSWORD" \
            DATABASE_PATH="$DATA_DIR_FORMATION/pret-jeux.db" \
            TOURNOI_DATABASE_PATH="$DATA_DIR_FORMATION/tournoi.db" \
            PLANNING_DATABASE_PATH="$DATA_DIR_FORMATION/planning.db" \
            BASE_URL="https://$DOMAINE_FORMATION" \
            NOM_ASSOCIATION="$NOM_ASSOCIATION" \
            APP_ENV=production \
            "$INSTALL_DIR/.venv/bin/python" "$@"
    }

    info "Initialisation des bases de formation..."
    (cd "$INSTALL_DIR" && run_python_formation -m app.db)
    (cd "$INSTALL_DIR" && run_python_formation -m app.tournoi.db)
    (cd "$INSTALL_DIR" && run_python_formation -m app.planning.db)

    info "Peuplement des données de démonstration (jeux fictifs, prêts, tournoi)..."
    (cd "$INSTALL_DIR" && run_python_formation -m app.formation)

    # Fichier lu par systemd (EnvironmentFile) : valeurs prises littéralement
    # ligne par ligne (pas d'interprétation shell, pas de souci de quoting ici).
    ENV_FORMATION="/etc/ludotex-formation.env"
    # Valeurs entre guillemets : valable pour systemd (EnvironmentFile,
    # cf. systemd.exec(5)) ET pour un `source` bash manuel de dépannage —
    # important pour NOM_ASSOCIATION, qui contient des espaces.
    cat > "$ENV_FORMATION" <<EOF
# Fichier généré par deploy/install.sh le $(date -Iseconds)
# Variables de l'INSTANCE DE FORMATION uniquement (lues par systemd via
# EnvironmentFile, voir deploy/ludotex-formation.service). PRET_TOKEN est
# volontairement absent : accès ouvert, plus simple pour la formation (aucune
# donnée réelle n'est en jeu sur cette instance).
MODE_FORMATION=1
ADMIN_PASSWORD="$ADMIN_PASSWORD"
DATABASE_PATH="$DATA_DIR_FORMATION/pret-jeux.db"
TOURNOI_DATABASE_PATH="$DATA_DIR_FORMATION/tournoi.db"
PLANNING_DATABASE_PATH="$DATA_DIR_FORMATION/planning.db"
BASE_URL="https://$DOMAINE_FORMATION"
NOM_ASSOCIATION="$NOM_ASSOCIATION"
APP_ENV=production
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FORMATION"
    chmod 600 "$ENV_FORMATION"
    info "$ENV_FORMATION généré."

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
echo "Espace admin         : https://$DOMAINE/admin  (mot de passe défini ci-dessus)"
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
    echo "    ou : cd $INSTALL_DIR && sudo -u $SERVICE_USER bash -c 'set -o allexport; source /etc/ludotex-formation.env; set +o allexport; exec .venv/bin/python -m app.formation'"
    echo "  - QR d'entraînement (optionnel) :"
    echo "      cd $INSTALL_DIR && sudo -u $SERVICE_USER .venv/bin/python -m scripts.generate_qr --base-url $FORMATION_URL_FINALE --planche"
    echo
fi
echo "Détails et dépannage : docs/deploiement.md"
