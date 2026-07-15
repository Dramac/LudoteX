# Déploiement — application de prêt de jeux (étape 10)

Guide pour mettre l'application en ligne sur un VPS, en HTTPS, à l'usage de
quelqu'un qui n'est **pas développeur**. Un script (`deploy/install.sh`)
automatise la quasi-totalité des étapes ; ce guide explique quoi faire avant,
pendant et après son exécution.

> Ce guide part du principe que quelqu'un (le bureau, un prestataire...) a
> déjà souscrit un VPS et un nom de domaine. Le choix de l'hébergeur est
> traité dans `docs/etude-hebergement.md` / `docs/budget.md`.

---

## 0. Ce qu'il faut avoir en main avant de commencer

- Un **VPS** Debian 12 ou Ubuntu 22.04 (ou plus récent), avec un accès **SSH**
  (identifiant, adresse IP, et soit un mot de passe soit une clé fournis par
  l'hébergeur).
- Un **nom de domaine** (ou sous-domaine) déjà réservé, par exemple
  `jeux.monasso.fr`, avec son enregistrement DNS **A** (et **AAAA** en IPv6 si
  disponible) pointant vers l'adresse IP du VPS. C'est l'hébergeur du domaine
  (souvent différent du VPS) qui permet de régler ça, dans une section
  généralement appelée « zone DNS » ou « gestion DNS ».
- Une **adresse e-mail** valide (sert uniquement aux alertes de renouvellement
  du certificat HTTPS, envoyées par Let's Encrypt).
- Le **mot de passe** que l'on souhaite donner à l'espace d'administration du
  site (`/admin`) — à choisir dès maintenant, il sera demandé pendant
  l'installation.

Avant d'aller plus loin, vérifier que le domaine pointe déjà vers le VPS
(remplacer par le vrai domaine) :

```bash
dig +short jeux.monasso.fr
```

Le résultat doit être l'adresse IP du VPS. Si ce n'est pas encore le cas,
attendre la propagation DNS (de quelques minutes à quelques heures) avant
l'étape du certificat HTTPS — le reste de l'installation peut se faire entre
temps.

---

## 1. Se connecter au VPS

Depuis un ordinateur (Mac/Linux : Terminal ; Windows : PowerShell ou
[PuTTY](https://www.putty.org/)) :

```bash
ssh root@ADRESSE_IP_DU_VPS
```

(remplacer `ADRESSE_IP_DU_VPS` par l'IP fournie par l'hébergeur ; sur certains
hébergeurs le premier utilisateur n'est pas `root` mais `debian`/`ubuntu` — se
référer à l'e-mail de bienvenue de l'hébergeur). Accepter l'empreinte du
serveur si demandé, puis saisir le mot de passe (ou la clé sera utilisée
automatiquement).

Une fois connecté, toutes les commandes ci-dessous se tapent **dans ce
terminal SSH**, sur le serveur.

## 2. Récupérer le code de l'application

```bash
apt update && apt install -y git
git clone https://github.com/Dramac/LudoteX.git
cd LudoteX
```

## 3. Lancer le script d'installation

```bash
sudo ./deploy/install.sh
```

Le script est **interactif** : il pose des questions les unes après les
autres, avec une valeur par défaut entre crochets (appuyer sur **Entrée**
pour la garder). Dans l'ordre :

1. **Domaine** : le nom de domaine préparé à l'étape 0 (ex. `jeux.monasso.fr`).
2. **E-mail** : pour les alertes Let's Encrypt.
3. **Nom de l'association** : affiché dans le bandeau du site (par défaut
   « Des jeux plein la Manche »).
4. **Mot de passe administrateur** (saisie masquée, demandée deux fois).
5. **Chemin d'installation** : où vivra le code sur le serveur (par défaut
   `/opt/ludotex` — garder la valeur par défaut sauf besoin particulier).
6. **Chemin des bases SQLite** : où seront stockées les données (par défaut
   `/var/lib/ludotex`).

Un récapitulatif s'affiche avant de continuer — vérifier puis valider.

Le script s'occupe ensuite, tout seul, de :

- installer les paquets système nécessaires (Python 3.11+, nginx, certbot,
  git, sqlite3...) ;
- déployer le code à l'emplacement choisi et générer le fichier `.env` ;
- créer l'environnement Python et installer les dépendances ;
- initialiser les trois bases SQLite (prêt, tournois, planning) et générer un
  jeton bénévole valable une semaine ;
- installer le service systemd (l'application démarre automatiquement, y
  compris après un redémarrage du serveur) ;
- configurer nginx et obtenir le certificat HTTPS Let's Encrypt (si le DNS est
  déjà propagé — sinon le script l'indique et donne la commande à relancer
  plus tard) ;
- proposer de programmer la sauvegarde quotidienne automatique.

À la fin, le script affiche :

- l'adresse du site,
- le **lien d'activation bénévole** (`https://.../acces?jeton=...`) à
  partager aux bénévoles le jour de l'événement,
- les commandes pour importer le catalogue de jeux et imprimer les QR codes.

**Durée** : quelques minutes (selon la vitesse du VPS et du réseau).

> Le script peut être relancé sans risque si une étape a échoué : il ne
> réécrase pas un `.env` déjà en place sans redemander confirmation, et les
> autres étapes (paquets, service, nginx) sont sans effet si déjà en place.

## 4. Importer le catalogue de jeux

```bash
cd /opt/ludotex   # ou le chemin choisi à l'étape 3
sudo -u pretjeux .venv/bin/python -m scripts.import_csv chemin/vers/catalogue.csv
```

Le fichier CSV peut être envoyé sur le serveur avec `scp` depuis
l'ordinateur qui le possède :

```bash
scp catalogue.csv root@ADRESSE_IP_DU_VPS:/opt/ludotex/
```

L'import est **idempotent** (peut être relancé, met à jour sans dupliquer).

## 5. Vérifier que tout fonctionne

Depuis un navigateur, en remplaçant par le vrai domaine :

- `https://jeux.monasso.fr/sante` → doit afficher `{"statut":"ok"}`.
- `https://jeux.monasso.fr/catalogue` → le catalogue s'affiche (vide tant que
  l'étape 4 n'est pas faite).
- `https://jeux.monasso.fr/admin` → se connecter avec le mot de passe défini
  pendant l'installation.
- Ouvrir sur un smartphone le **lien d'activation bénévole** affiché à la fin
  du script (ou depuis `/admin` → Accès bénévole → partager le lien), puis
  tester un scan depuis `/scanner`.

## 6. QR définitifs

**Une fois le domaine confirmé et le DNS/HTTPS actifs**, imprimer les
étiquettes définitives (le QR encode l'URL du domaine — ne pas imprimer
avant cette étape, sous peine de devoir tout réimprimer si le domaine change) :

```bash
cd /opt/ludotex
sudo -u pretjeux .venv/bin/python -m scripts.generate_qr --planche --grille 8x2
# le PDF qr/planche-qr.pdf encode https://jeux.monasso.fr/jeu/<id>
```

Ou depuis l'espace admin (`/admin/etiquettes`) pour une sélection de jeux à
la carte.

## 7. Sauvegarde de la base

Si acceptée pendant l'installation, une sauvegarde quotidienne automatique
(3h du matin) est déjà en place — vérifiable avec :

```bash
sudo crontab -u pretjeux -l
```

Pour une copie **hors serveur** (recommandé, protège contre une panne du VPS
lui-même) : installer `rclone`, configurer une cible (Nextcloud, Google
Drive...), puis décommenter la ligne `rclone copy` dans
`deploy/sauvegarde.sh`. Test manuel d'une sauvegarde :

```bash
sudo -u pretjeux /opt/ludotex/deploy/sauvegarde.sh /var/lib/ludotex/pret-jeux.db /var/lib/ludotex/sauvegardes
```

## 7bis. Site de formation (optionnel)

À la fin de son déroulement, `deploy/install.sh` propose d'installer aussi un
**site de formation** : une SECONDE INSTANCE de l'application (même code),
avec ses propres bases jetables, un bandeau et un filigrane bien visibles, et
un bouton pour réinitialiser ses données — pensé pour former les nouveaux
bénévoles sans risque de toucher aux vraies données. Détails et usage
quotidien : `docs/mode-formation.md`.

Si accepté, le script demande un **sous-domaine** dédié (défaut :
`formation.<domaine principal>` — ex. `formation.jeux.monasso.fr`, à
enregistrer en DNS comme le domaine principal), puis s'occupe de tout :
bases séparées (`<chemin des bases>-formation`), service systemd
`ludotex-formation` (port 8100, en parallèle du service `ludotex` existant),
bloc nginx + certificat HTTPS pour ce sous-domaine, peuplement des données de
démonstration, et ajout du lien correspondant au tableau de bord admin de la
production (`FORMATION_URL` dans le `.env` principal).

Pour l'installer après coup (script déjà passé sans cette étape) : relancer
`sudo ./deploy/install.sh` en conservant le `.env` existant — l'étape « Site
de formation » est reproposée à chaque exécution.

## 8. Mises à jour ultérieures

Quand du nouveau code est disponible (nouvelle fonctionnalité, correctif) :

```bash
cd /opt/ludotex
sudo git pull
sudo -u pretjeux .venv/bin/pip install -r requirements.txt
sudo systemctl restart ludotex
```

Le schéma des bases se met à jour tout seul au redémarrage (migrations
automatiques et sans perte de données). Vérifier ensuite que le service est
bien reparti :

```bash
sudo systemctl status ludotex
```

## 9. En cas de problème

- **Le site ne répond pas** : `sudo systemctl status ludotex` puis
  `sudo journalctl -u ludotex -e` (dernières lignes de log).
- **Erreur nginx** : `sudo nginx -t` (vérifie la configuration) puis
  `sudo systemctl reload nginx`.
- **Certificat HTTPS manquant ou expiré** :
  `sudo certbot --nginx -d jeux.monasso.fr -m contact@monasso.fr`
  (le renouvellement est normalement automatique, `certbot` programme sa
  propre tâche).
- **Mot de passe admin oublié** : pas de récupération automatique par
  e-mail (aucune donnée personnelle stockée) — il faut réinitialiser le hash
  en base ou redéfinir `ADMIN_PASSWORD` dans `.env` puis relancer
  `sudo ./deploy/install.sh` en choisissant de conserver le `.env` existant
  après y avoir édité la ligne à la main, ou demander de l'aide au référent
  technique.
- **Jeton bénévole expiré** : se reconnecter à `/admin` (le mot de passe
  admin reste valide) → « Accès bénévole » → réinitialiser.

## 10. Exploitation au quotidien

- **Rotation du jeton bénévole** : depuis `/admin` → Accès bénévole, sans
  avoir besoin de toucher au serveur.
- **Logs** : `sudo journalctl -u ludotex -f` (suivi en direct).
- **Référent technique** : prévoir une personne pour les mises à jour de
  sécurité du système (`sudo apt update && sudo apt upgrade`) et la
  surveillance des sauvegardes.

---

## Annexe — étapes manuelles équivalentes

Cette section détaille ce que `deploy/install.sh` automatise, utile pour
dépanner une étape précise ou personnaliser au-delà de ce que le script
propose. Conventions utilisées ci-dessous (à adapter) : sous-domaine
`pret.example.fr`, dossier `/opt/ludotex`, utilisateur système `pretjeux`.

### A. Préparer le serveur

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git nginx sqlite3 \
                    certbot python3-certbot-nginx ufw

sudo adduser --system --group pretjeux

sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

### B. Récupérer le code

```bash
sudo mkdir -p /opt/ludotex
sudo chown pretjeux:pretjeux /opt/ludotex
sudo -u pretjeux git clone https://github.com/Dramac/LudoteX.git /opt/ludotex
```

### C. Environnement Python

```bash
cd /opt/ludotex
sudo -u pretjeux python3 -m venv .venv
sudo -u pretjeux .venv/bin/pip install -r requirements.txt
```

### D. Configuration (`.env`)

```bash
sudo -u pretjeux cp .env.example .env
sudo -u pretjeux nano .env
```

À renseigner (voir `.env.example` pour la liste complète et à jour) :
`PRET_TOKEN` (générer avec
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`),
`ADMIN_PASSWORD`, `BASE_URL` (l'URL **définitive**, ex.
`https://pret.example.fr`), `NOM_ASSOCIATION`, `DATABASE_PATH`,
`TOURNOI_DATABASE_PATH`, `PLANNING_DATABASE_PATH`, `RATE_LIMIT_PER_MINUTE`,
`APP_ENV=production`.

### E. Initialiser les bases

```bash
cd /opt/ludotex
sudo -u pretjeux .venv/bin/python -m app.db
sudo -u pretjeux .venv/bin/python -m app.tournoi.db
sudo -u pretjeux .venv/bin/python -m app.planning.db
```

Puis générer le jeton bénévole définitif (expiration 1 semaine) :

```bash
cd /opt/ludotex
sudo -u pretjeux .venv/bin/python -c "
from app.db import get_connection
from app import auth
conn = get_connection()
print(auth.reinitialiser_jeton(conn))
conn.close()
"
```

### F. Service systemd (uvicorn)

```bash
sudo cp deploy/ludotex.service /etc/systemd/system/ludotex.service
# adapter User/WorkingDirectory/chemins dans le fichier si besoin
sudo systemctl daemon-reload
sudo systemctl enable --now ludotex
sudo systemctl status ludotex
```

### G. nginx (reverse proxy)

```bash
sudo cp deploy/nginx-ludotex.conf /etc/nginx/sites-available/ludotex
sudo sed -i 's/pret.example.fr/VOTRE_SOUS_DOMAINE/' /etc/nginx/sites-available/ludotex
sudo ln -s /etc/nginx/sites-available/ludotex /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### H. HTTPS (Let's Encrypt)

```bash
sudo certbot --nginx -d pret.example.fr
```

### I. Sauvegarde de la base

```bash
chmod +x /opt/ludotex/deploy/sauvegarde.sh
sudo -u pretjeux /opt/ludotex/deploy/sauvegarde.sh
sudo -u pretjeux crontab -e
# ajouter (sauvegarde quotidienne à 3h) :
# 0 3 * * * /opt/ludotex/deploy/sauvegarde.sh >> /var/log/ludotex-sauvegarde.log 2>&1
```
