# Déploiement — application de prêt de jeux (étape 10)

Guide pas à pas pour mettre l'application en production sur un VPS Debian/Ubuntu,
derrière nginx en HTTPS (Let's Encrypt). Les fichiers de configuration prêts à
l'emploi sont dans `deploy/`.

Conventions de ce guide (à adapter) :
- Sous-domaine : `pret.example.fr`
- Dossier de l'app : `/opt/pret-jeux`
- Utilisateur système : `pretjeux`

> Rappel : c'est **Simon** qui exécute ces commandes sur le serveur (l'assistant
> n'a pas d'accès SSH). En cas de doute à une étape, demander.

---

## 0. Prérequis

- Un **VPS** Debian 12 / Ubuntu 22.04+ avec accès SSH (sudo).
- Un **nom de domaine** et un **sous-domaine** `pret.example.fr` dont
  l'enregistrement DNS **A** (et **AAAA** si IPv6) pointe vers l'IP du VPS.
- Le scénario d'hébergement choisi par le bureau (cf. `docs/budget.md`).

Vérifier que le DNS est propagé avant de demander le certificat :
`dig +short pret.example.fr` doit renvoyer l'IP du VPS.

## 1. Préparer le serveur

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git nginx sqlite3 \
                    certbot python3-certbot-nginx ufw

# Utilisateur système dédié (sans login)
sudo adduser --system --group pretjeux

# Pare-feu : SSH + web
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 2. Récupérer le code

```bash
sudo mkdir -p /opt/pret-jeux
sudo chown pretjeux:pretjeux /opt/pret-jeux
# Cloner le dépôt privé (HTTPS + token, ou clé de déploiement) :
sudo -u pretjeux git clone https://github.com/<compte>/pret-jeux.git /opt/pret-jeux
```

## 3. Environnement Python

```bash
cd /opt/pret-jeux
sudo -u pretjeux python3 -m venv .venv
sudo -u pretjeux .venv/bin/pip install -r requirements.txt
```

## 4. Configuration (`.env`)

```bash
sudo -u pretjeux cp .env.example .env
sudo -u pretjeux nano .env
```

À renseigner :
- `PRET_TOKEN` : générer un jeton —
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
  (on pourra ensuite le régénérer depuis `/admin`).
- `ADMIN_PASSWORD` : mot de passe initial de l'espace admin (changeable ensuite
  dans l'appli).
- `BASE_URL` : **`https://pret.example.fr`** — l'URL DÉFINITIVE encodée dans les QR.
- `DATABASE_PATH` : `data/pret-jeux.db` (défaut, sous `/opt/pret-jeux/data`).
- `RATE_LIMIT_PER_MINUTE`, `APP_ENV=production`.

Le `.env` n'est jamais committé (déjà dans `.gitignore`).

## 5. Initialiser la base + importer le catalogue

```bash
cd /opt/pret-jeux
sudo -u pretjeux .venv/bin/python -m app.db                    # crée la base
sudo -u pretjeux .venv/bin/python -m scripts.import_csv chemin/vers/catalogue.csv
```

## 6. Service systemd (uvicorn)

```bash
sudo cp deploy/pret-jeux.service /etc/systemd/system/pret-jeux.service
# adapter User/WorkingDirectory/chemins dans le fichier si besoin
sudo systemctl daemon-reload
sudo systemctl enable --now pret-jeux
sudo systemctl status pret-jeux        # doit être "active (running)"
```

## 7. nginx (reverse proxy)

```bash
sudo cp deploy/nginx-pret-jeux.conf /etc/nginx/sites-available/pret-jeux
sudo sed -i 's/pret.example.fr/VOTRE_SOUS_DOMAINE/' /etc/nginx/sites-available/pret-jeux
sudo ln -s /etc/nginx/sites-available/pret-jeux /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 8. HTTPS (Let's Encrypt)

```bash
sudo certbot --nginx -d pret.example.fr
```

certbot ajoute le bloc HTTPS, la redirection 80→443, et programme le
renouvellement automatique. Le **HTTPS est indispensable** : le scanner caméra ne
fonctionne qu'en contexte sécurisé.

## 9. Vérifier

- `https://pret.example.fr/sante` → `{"statut":"ok"}`
- `https://pret.example.fr/catalogue` → le catalogue s'affiche.
- `https://pret.example.fr/admin` → connexion avec `ADMIN_PASSWORD`.
- Activer un téléphone : `/admin` → Accès bénévole → partager le lien, l'ouvrir,
  tester un scan.

## 10. QR définitifs

Le domaine est maintenant figé : on peut tirer les étiquettes définitives.

```bash
cd /opt/pret-jeux
sudo -u pretjeux .venv/bin/python -m scripts.generate_qr --planche --grille 8x2
# le PDF qr/planche-qr.pdf encode https://pret.example.fr/jeu/<id>
```

> Ne pas imprimer les étiquettes **avant** cette étape : l'URL du QR est définitive.

## 11. Sauvegarde de la base

```bash
chmod +x /opt/pret-jeux/deploy/sauvegarde.sh
sudo -u pretjeux /opt/pret-jeux/deploy/sauvegarde.sh        # test manuel
sudo -u pretjeux crontab -e
# ajouter (sauvegarde quotidienne à 3h) :
# 0 3 * * * /opt/pret-jeux/deploy/sauvegarde.sh >> /var/log/pret-jeux-sauvegarde.log 2>&1
```

Pour une copie **hors serveur** (recommandé) : installer `rclone`, configurer une
cible (Nextcloud/Drive), puis décommenter la ligne `rclone copy` dans
`deploy/sauvegarde.sh`.

## 12. Mises à jour ultérieures

```bash
cd /opt/pret-jeux
sudo -u pretjeux git pull
sudo -u pretjeux .venv/bin/pip install -r requirements.txt
sudo systemctl restart pret-jeux
```

Le schéma se met à jour tout seul au démarrage (`init_db` + migrations
idempotentes). Aucune perte de données.

## 13. Exploitation

- **Rotation du jeton bénévole** : depuis `/admin` → Accès bénévole (pas besoin de
  toucher au serveur).
- **Logs** : `journalctl -u pret-jeux -f`.
- **Référent technique** : prévoir une personne pour les mises à jour de sécurité
  du VPS (`apt upgrade`) et la surveillance des sauvegardes.
