# Notes de déploiement — ce que `update.sh` ne fait pas

À l'attention de la personne qui met à jour le serveur. Ce fichier porte, version
par version, les **gestes à faire à la main** en plus de
`sudo ./deploy/update.sh`. Une version qui n'en demande aucun n'y figure pas.

**Le lire avant chaque mise à jour**, pour toutes les versions entre celle qui
tourne (elle se lit sur `/apropos` et sur `/sante`) et celle qu'on installe.

## Pourquoi ce fichier, et pas `CHANGELOG.md`

`CHANGELOG.md` est tourné vers les bénévoles : les puces de sa dernière section
s'affichent telles quelles sur la page publique `/apropos`, **sous-titres
compris** — une rubrique « gestes de déploiement » glissée là partirait en
ligne. Une commande de serveur n'a rien à y faire.
`tests/test_version_coherente.py` refuse une puce qui en contient une.

`update.sh` sauvegarde, récupère le code, installe les dépendances, migre les
bases, redémarre et vérifie. Tout le reste ne suit **pas** tout seul. Quand le
dépôt change l'un de ces points, la version doit avoir sa section ici :

- un fichier de `deploy/` copié sur le serveur à l'installation : unités
  systemd (`*.service`, `*.timer`), configuration nginx (`nginx-*.conf`) ;
- une clé ajoutée à `.env.example` **avec une valeur** (une clé commentée ou
  vide y est facultative) ;
- ce que pose `deploy/install.sh` : tâche planifiée, permissions, paquets
  système ;
- **`deploy/update.sh` lui-même.** Le `git pull` de son étape 2 remplace le
  fichier pendant que bash le lit, et bash termine l'ancien : une modification
  de ce script ne s'applique qu'à la mise à jour **suivante**. Éprouvé sur un
  dépôt jetable lors de l'ajout du contrôle de report.

## Écrire une section

- **Tant que la version n'a pas de numéro**, les gestes s'accumulent sous
  « À paraître ». À la montée de version (`docs/versioning.md`), renommer ce
  titre au numéro, dans le même commit.
- **Des gestes, pas un récit** : les commandes dans l'ordre, ce qu'on doit voir
  à chaque étape, et comment revenir en arrière.
- **Rien qui soit propre à une instance** : ni domaine réel, ni nom
  d'association, ni valeur de `.env`.

## Le contrôle de report

L'étape 7 d'`update.sh` lance `scripts/controle_report.py`. Il compare le
serveur au dépôt, **nomme** ce qui diffère, **ne modifie rien** et ne peut pas
faire échouer la mise à jour. Il est le filet de ce fichier : un geste oublié
ici réapparaît à la mise à jour suivante sous la forme d'une ligne
« à examiner ».

Pour le lancer seul, sans mise à jour ni redémarrage :

```bash
sudo -u pretjeux /opt/ludotex/.venv/bin/python /opt/ludotex/scripts/controle_report.py
# hors chemins par défaut :
#   ... --install-dir <dossier d'installation> --data-dir <dossier des bases>
```

| Ce qu'il vérifie | Comment |
|---|---|
| Unités systemd | chaque `deploy/*.service` et `deploy/*.timer` comparé à `/etc/systemd/system/`, commentaires et chemin d'installation mis de côté ; unité activée ; systemd rechargé depuis la dernière copie |
| nginx | chaque `deploy/nginx-*.conf` comparé à `/etc/nginx/sites-available/`, domaine et lignes posées par certbot mis de côté ; site activé dans `sites-enabled` |
| Fichiers d'environnement | clés attendues par `.env.example` absentes du `.env`, et de `/etc/ludotex-formation.env` si la formation existe — **des noms, jamais une valeur** |
| Tâche de sauvegarde | chaque minuteur de `deploy/*.timer` **actif** (activé ne suffit pas : sans `--now`, il attend un redémarrage) ; aucune ligne de la crontab du service ne lance encore `sauvegarde.sh` (elle doublerait le minuteur) |
| Paquets système | la liste `PAQUETS_BASE` d'`install.sh` |
| Permissions | dossiers des sauvegardes en `700`, fichiers d'environnement en `600`, dossiers des bases, tous au nom du service |

Un fichier propre à l'instance de formation n'est attendu que si cette instance
est installée.

| Ce qu'il ne vérifie pas | Pourquoi |
|---|---|
| La configuration nginx **chargée** | `nginx -T` exige root. Le contrôle compare le fichier sur le disque : un fichier copié mais pas rechargé n'est pas vu |
| Les certificats HTTPS | leurs lignes sont volontairement mises de côté ; leur renouvellement relève de `certbot.timer` |
| Le pare-feu (`ufw`) | `ufw status` exige root ; une règle manquante se voit tout de suite (site ou SSH injoignable) |
| L'utilisateur du service, le propriétaire de chaque fichier du code | `update.sh` agit sous cet utilisateur dès sa première ligne et échoue bruyamment s'il manque ou si le code ne lui appartient pas |
| Le bit exécutable des scripts de `deploy/` | git le porte lui-même, le `git pull` le rétablit |
| Environnement Python, dépendances, schéma des bases | `update.sh` les pose à chaque passage (étapes 3 et 4) |
| Le **résultat** de la dernière sauvegarde automatique | un minuteur actif peut lancer un script qui échoue : la preuve est une sauvegarde de routine de moins de 14 heures, à vérifier selon `docs/deploiement.md` § 7 |
| Le jeton bénévole, les données de démonstration de la formation | ce sont des données, pas de la configuration du serveur |
| `FORMATION_URL` dans le `.env` de production | clé facultative, propre à une instance ; son absence masque seulement un lien |

---

## À paraître

### nginx : attrape-tout, un journal par site, une page quand l'application est arrêtée, un frein devant `/admin` — à faire plus tard, si vous le souhaitez, hors événement

Les trois fichiers nginx changent, et un troisième apparaît :

- **`deploy/nginx-attrape-tout.conf`** (nouveau) remplace le site `default` de
  Debian : une visite qui ne désigne ni le site ni la formation — l'adresse IP
  nue, un nom inconnu, en pratique des robots — n'obtient **aucune réponse**.
  Sans certificat : la poignée de main HTTPS est refusée
  (`ssl_reject_handshake`).
- **Un journal d'accès par site** : `/var/log/nginx/ludotex-access.log` et
  `/var/log/nginx/ludotex-formation-access.log`. Format inchangé, jetons et
  codes toujours retirés. `/var/log/nginx/access.log` ne reçoit plus que les
  redirections HTTP → HTTPS ; ses archives gardent l'historique d'avant, les
  deux sites mêlés, pendant 14 jours.
- **Une page quand l'application est arrêtée** : nginx sert
  `app/static/indisponible.html` (code `502`) au lieu de sa page d'erreur
  brute. Elle dit aux bénévoles de continuer sur papier.
- **Un frein devant `/admin`** : 2 requêtes par seconde et par visiteur,
  rafale de 40 sans attente. Aucun effet sur le prêt, le scanner ni `/acces`.

**Facultatif au sens de `docs/versioning.md`** : sans ces gestes, les deux
sites fonctionnent exactement comme avant ; le contrôle de report signale les
trois fichiers. **Hors événement uniquement** : une erreur dans un fichier
nginx, rechargée, fait tomber les deux sites. Rien de ce qui suit ne recharge
nginx sans que `sudo nginx -t` ait répondu `test is successful`.

`update.sh` n'est **pas** modifié par cette version : aucun décalage d'une mise
à jour. `install.sh` l'est (il installe l'attrape-tout, crée les journaux, et
**conserve** désormais `/etc/ludotex-formation.env` s'il existe au lieu de le
réécrire), mais il ne tourne pas pendant une mise à jour : rien à faire pour
lui.

**Prérequis** : `sudo ./deploy/update.sh` de cette version déjà passé, pour que
`/opt/ludotex/deploy/` porte les nouveaux fichiers et `app/static/` la page :
```bash
ls /opt/ludotex/deploy/nginx-attrape-tout.conf /opt/ludotex/app/static/indisponible.html
```
*À voir :* les deux chemins, sans « No such file ».

Chemins par défaut ci-dessous (`/opt/ludotex`) ; `<domaine>` et
`<domaine de formation>` sont les adresses des deux sites, sans `https://`.

1. **Sauvegarder `/etc/nginx` en entier**, hors de `/etc/nginx` :
   ```bash
   sudo mkdir -p /root/sauvegarde-nginx
   sudo tar -C /etc -czf /root/sauvegarde-nginx/etc-nginx.$(date +%Y%m%d-%H%M%S).tar.gz nginx
   sudo ls -l /root/sauvegarde-nginx/
   ```
   *À voir :* une archive `etc-nginx.<horodatage>.tar.gz` datée du jour. Ne
   pas continuer sans elle.

2. **Constater l'état d'avant** — l'adresse IP nue répond :
   ```bash
   IP=$(hostname -I | awk '{print $1}'); echo "$IP"
   curl -s  -o /dev/null -w 'http  IP : %{http_code}\n' "http://$IP/"
   curl -sk -o /dev/null -w 'https IP : %{http_code}\n' "https://$IP/"
   ```
   *À voir :* `200` et `200` (page « Welcome to nginx! », puis l'application).

3. **Créer les deux journaux** avec les droits que `logrotate` leur donnera
   (sinon nginx, qui tourne en root, les crée lisibles par tous jusqu'à la
   première rotation) :
   ```bash
   for f in /var/log/nginx/ludotex-access.log /var/log/nginx/ludotex-formation-access.log; do
       [ -e "$f" ] || sudo install -o www-data -g adm -m 640 /dev/null "$f"
   done
   ls -l /var/log/nginx/ludotex*-access.log
   ```
   *À voir :* deux fichiers vides, `-rw-r----- www-data adm`.

4. **Formation d'abord** : `docs/deploiement.md`, « Mettre à jour la
   configuration nginx », bloc **Formation**, étapes 1 à 5 — avec le
   `grep -n server_name` de l'étape 2, qui doit montrer
   `<domaine de formation>` exactement. Puis constater :
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://<domaine de formation>/catalogue
   curl -sI https://<domaine de formation>/ | grep -ciE 'strict-transport|frame-options|content-type-options|referrer-policy|content-security-policy'
   sudo tail -1 /var/log/nginx/ludotex-formation-access.log
   ```
   *À voir :* `200` ; `5` ; une ligne qui se termine par `rt=… uct=…`.

   **La page d'indisponibilité, éprouvée sur la formation seule** (quelques
   secondes, personne n'est gêné) :
   ```bash
   sudo systemctl stop ludotex-formation
   curl -s -D - -o /tmp/indispo.html https://<domaine de formation>/scanner | grep -iE '^HTTP|cache-control|content-security'
   grep -o '<title>.*</title>' /tmp/indispo.html; rm -f /tmp/indispo.html
   sudo systemctl start ludotex-formation
   systemctl is-active ludotex-formation
   ```
   *À voir :* `HTTP/1.1 502`, la ligne `Content-Security-Policy`, **aucune**
   ligne `Cache-Control` ; puis `<title>Site momentanément
   indisponible</title>` ; puis `active`.

   **Le frein devant `/admin`**, sur la formation :
   ```bash
   for i in $(seq 50); do curl -s -o /dev/null -w '%{http_code}\n' https://<domaine de formation>/admin; done | sort | uniq -c
   curl -s -o /dev/null -w '%{http_code}\n' https://<domaine de formation>/catalogue
   ```
   *À voir :* une quarantaine de `200`, puis des `429` ; et `200` sur le
   catalogue, que le frein ne touche pas.

5. **Production ensuite** : même section, bloc **Production**, étapes 1 à 5,
   `grep -n server_name` → `<domaine>` exactement. Puis :
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://<domaine>/catalogue
   curl -sI https://<domaine>/ | grep -ciE 'strict-transport|frame-options|content-type-options|referrer-policy|content-security-policy'
   sudo tail -1 /var/log/nginx/ludotex-access.log
   ```
   *À voir :* `200` ; `5` ; une ligne `rt=… uct=…`. Ne **pas** arrêter la
   production pour voir la page : la formation l'a déjà montrée, c'est le même
   fichier.

6. **L'attrape-tout, en dernier** — copier, activer, **désactiver `default`**,
   tester, et recharger **seulement si le test est vert** :
   ```bash
   sudo cp /opt/ludotex/deploy/nginx-attrape-tout.conf /etc/nginx/sites-available/attrape-tout
   sudo rm /etc/nginx/sites-enabled/default
   sudo ln -s /etc/nginx/sites-available/attrape-tout /etc/nginx/sites-enabled/attrape-tout
   sudo nginx -t
   ```
   *À voir :* `syntax is ok` puis `test is successful`. **Alors seulement** :
   ```bash
   sudo systemctl reload nginx
   ```
   Si `nginx -t` signale une erreur, **ne pas recharger** : rien n'a changé
   pour les visiteurs. Remettre les deux liens comme avant, puis revérifier :
   ```bash
   sudo rm /etc/nginx/sites-enabled/attrape-tout
   sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
   sudo nginx -t
   ```
   Deux messages possibles, et leur cause : `a duplicate default server` →
   `default` est encore activé ; `duplicate listen options` → une ligne
   `listen [::]:443` porte `ipv6only=on` ailleurs que dans le bloc de la
   production (le fichier du dépôt n'en porte pas).

7. **Constater** — l'adresse IP nue ne répond plus, les deux sites si :
   ```bash
   IP=$(hostname -I | awk '{print $1}')
   curl -sk -o /dev/null "https://$IP/"; echo "https IP : sortie $?"
   curl -s  -o /dev/null "http://$IP/";  echo "http  IP : sortie $?"
   curl -s -o /dev/null -w '%{http_code}\n' https://<domaine>/
   curl -s -o /dev/null -w '%{http_code}\n' https://<domaine de formation>/
   curl -s -o /dev/null -w '%{http_code}\n' http://<domaine>/
   ```
   *À voir :* `sortie 35` (connexion HTTPS refusée), `sortie 52` (connexion
   fermée sans réponse), `200`, `200`, `301`. Les deux `200` sont obtenus
   **sans** `-k` : le certificat est valide. Dans un navigateur, les deux
   sites s'ouvrent avec le cadenas.

   **Ce qui change pour les robots** : ils n'obtiennent plus rien, et ne sont
   plus écrits nulle part. Environ 40 % des lignes du journal d'accès
   venaient d'eux ; c'est le but.

8. **Relancer le contrôle de report** :
   ```bash
   sudo -u pretjeux /opt/ludotex/.venv/bin/python /opt/ludotex/scripts/controle_report.py
   ```
   *À voir :* « Configuration nginx » conforme, 3 fichiers comparés.

9. **Le lendemain** : les deux journaux ont tourné comme les autres.
   ```bash
   ls -l /var/log/nginx/ludotex*-access.log*
   ```
   *À voir :* un `.1` pour chacun, `www-data adm`.

**Retour en arrière**, par partie :

- *Un seul site* : la sauvegarde horodatée prise à son étape 1
  (`docs/deploiement.md` § 9, « Une page semble cassée après une mise à jour
  de nginx »).
- *L'attrape-tout* : remettre `default`, retirer l'attrape-tout, tester, puis
  recharger :
  ```bash
  sudo rm /etc/nginx/sites-enabled/attrape-tout
  sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
  sudo nginx -t && sudo systemctl reload nginx
  ```
- *Tout* : l'archive de l'étape 1. Retirer d'abord le lien que l'archive ne
  connaît pas, puis la déployer par-dessus :
  ```bash
  sudo rm -f /etc/nginx/sites-enabled/attrape-tout
  sudo tar -C /etc -xzf /root/sauvegarde-nginx/etc-nginx.<horodatage>.tar.gz
  sudo nginx -t && sudo systemctl reload nginx
  ```
  Les certificats vivent dans `/etc/letsencrypt`, que ces gestes ne touchent
  pas. Les deux nouveaux journaux peuvent rester : plus rien n'y écrit.

## 1.15.0 — 2026-09-25

Quatre sections, deux échéances. **Le jour même du déploiement** : la
première ci-dessous, et en particulier la copie du minuteur — sans elle, le
nouveau seuil de supervision (14 h) fait passer le bloc « Sauvegarde » en
« Attention » chaque après-midi, faute du passage de 15h qui n'existe pas
encore sur le serveur : le voyant dit vrai, mais le bureau verra de l'orange
sans comprendre. **Plus tard, si vous le souhaitez** : la purge de journald
et le retrait du mot de passe en clair, toutes deux facultatives et
différables — la purge, elle, uniquement hors événement.

### Unités systemd, fichier de la formation, sauvegarde à 3h et à 15h — à faire le jour même du déploiement

Trois changements, **un seul passage** : ils touchent tous aux fichiers que
systemd lit, et `update.sh` n'en copie aucun.

- **Plus aucune requête journalisée par l'application** (`--no-access-log`
  dans `ludotex.service` et `ludotex-formation.service`). Sans cette option,
  uvicorn écrivait chaque requête dans journald, **chaîne de requête
  comprise** : liens d'activation (`?jeton=…`) et codes personnels du planning
  (`?code=…`), lisibles par tout membre du groupe `adm`. nginx journalise déjà
  chaque requête, secrets retirés : rien n'est perdu.
- **Le site de formation ne lit plus le `.env` de la production.** Son unité
  désigne son propre fichier à l'application (`LUDOTEX_ENV_FILE`). Jusqu'ici,
  toute clé absente de `/etc/ludotex-formation.env` lui venait de la
  production — le jeton bénévole compris : `/scanner` y répondait `403`.
- **Deux sauvegardes par jour**, à 3h et à 15h (heure du serveur), et 60
  archives de routine gardées au lieu de 30 : toujours un mois. La
  supervision passe « Attention » au-delà de 14 heures sans sauvegarde de
  routine (26 heures avant).

**Facultatif au sens de `docs/versioning.md`** : sans ces gestes, rien ne
casse, chaque instance se comporte comme avant. Deux effets visibles à
connaître si on les remet à plus tard : le contrôle de report signale les
quatre unités et le fichier de la formation ; et, le minuteur n'ayant qu'un
passage, le bloc « Sauvegarde » de `/admin/supervision` passe **« Attention »
chaque après-midi**, de 17h à 3h (heure du serveur) — c'est exact : il manque
le passage de 15h.

`update.sh` n'est **pas** modifié par cette version : aucun décalage d'une mise
à jour. `install.sh` l'est (il écrit les deux nouvelles lignes du fichier de
formation, et ne redemande plus le chemin des bases quand on garde le `.env`),
mais il ne tourne pas pendant une mise à jour : rien à faire pour lui.

Chemins par défaut ci-dessous (`/opt/ludotex`, `/var/lib/ludotex…`) : les
adapter si l'installation est ailleurs. **Hors d'un moment de prêt** : les
deux sites redémarrent (quelques secondes).

1. **Avant**, constater que la production journalise encore les requêtes :
   ```bash
   curl -s -o /dev/null "http://127.0.0.1:8000/sante?essai=journald-avant"
   journalctl -u ludotex --since "2 min ago" --no-pager | grep -c "journald-avant"
   ```
   *À voir :* `1`. (Une adresse sans effet, avec une chaîne de requête
   factice : rien n'est écrit nulle part ailleurs.)

2. Ajouter au fichier de la formation les deux clés qui lui venaient de la
   production. D'abord compter, sans rien afficher :
   ```bash
   sudo grep -c -E '^(PRET_TOKEN|RATE_LIMIT_PER_MINUTE)=' /etc/ludotex-formation.env
   ```
   *À voir :* `0`. **Autre chose que `0` : ne pas ajouter**, passer à
   l'étape 3 (une ligne existe déjà ; ne pas la dupliquer). Sinon :
   ```bash
   printf '\n# Accès bénévole ouvert : vide = pas de jeton (lot-7-pré-production).\nPRET_TOKEN=\nRATE_LIMIT_PER_MINUTE=60\n' | sudo tee -a /etc/ludotex-formation.env > /dev/null
   sudo grep -c -E '^(PRET_TOKEN|RATE_LIMIT_PER_MINUTE)=' /etc/ludotex-formation.env
   sudo stat -c '%a %U:%G' /etc/ludotex-formation.env
   ```
   *À voir :* `2`, puis `600 pretjeux:pretjeux` (`tee -a` ajoute sans toucher
   aux droits). Rien d'autre à reporter : les autres clés attendues y sont
   déjà (relevé du 2026-09-18, noms seuls).

3. Copier les quatre unités, recharger systemd :
   ```bash
   cd /opt/ludotex
   sudo cp deploy/ludotex.service deploy/ludotex-formation.service \
           deploy/ludotex-sauvegarde.service deploy/ludotex-sauvegarde.timer \
           /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
   Installation hors de `/opt/ludotex` : remplacer ce chemin dans les quatre
   fichiers copiés (`sudo sed -i 's#/opt/ludotex#<dossier>#g' …`), comme le
   fait `install.sh`, avant le `daemon-reload`.

4. Redémarrer les deux sites et le minuteur :
   ```bash
   sudo systemctl restart ludotex ludotex-formation
   sudo systemctl restart ludotex-sauvegarde.timer
   systemctl is-active ludotex ludotex-formation ludotex-sauvegarde.timer
   ```
   *À voir :* trois fois `active`. Si l'heure de 15:00 est déjà passée depuis
   la dernière sauvegarde, `Persistent=true` peut lancer tout de suite une
   sauvegarde de rattrapage : c'est sans danger, une archive de plus.

5. **Constater** — la production ne journalise plus les requêtes :
   ```bash
   curl -s -o /dev/null "http://127.0.0.1:8000/sante?essai=journald-apres"
   journalctl -u ludotex --since "2 min ago" --no-pager | grep -c "journald-apres"
   ```
   *À voir :* `0` (l'étape 1 donnait `1`). Même contrôle sur la formation avec
   le port `8100` et `-u ludotex-formation`.

6. **Constater** — la formation est ouverte, la production ne l'est pas :
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/scanner
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/scanner
   ```
   *À voir :* `200`, puis `403` (sans jeton, la production refuse ; c'est
   voulu). **`403` sur la formation** : sa propre base porte un jeton, posé un
   jour par « Réinitialiser » sur son écran d'administration — le jeton en
   base l'emporte sur le fichier. Le vérifier en lecture seule :
   ```bash
   sudo -u pretjeux sqlite3 "file:/var/lib/ludotex-formation/pret-jeux.db?mode=ro" "SELECT count(*) FROM parametres WHERE cle='pret_token' AND valeur <> '';"
   ```
   `1` confirme. Si l'on veut la formation ouverte (données fictives, aucun
   enjeu), retirer ce jeton de **la base de formation seulement** :
   ```bash
   sudo -u pretjeux sqlite3 /var/lib/ludotex-formation/pret-jeux.db "DELETE FROM parametres WHERE cle IN ('pret_token','pret_token_expire');"
   ```
   puis refaire la première commande de cette étape : `200`, sans
   redémarrage.

7. **Constater** — deux passages de sauvegarde :
   ```bash
   systemctl list-timers ludotex-sauvegarde.timer --no-pager
   ```
   *À voir :* la colonne `NEXT` au prochain 03:00 ou 15:00. Le lendemain, la
   vérification de `docs/deploiement.md` § 7.

8. Relancer le contrôle de report :
   ```bash
   sudo -u pretjeux /opt/ludotex/.venv/bin/python /opt/ludotex/scripts/controle_report.py
   ```
   *À voir :* « Unités systemd » et « Fichiers d'environnement » conformes.

**Retour en arrière**, par partie :

- *Unités* : recopier celles de la version précédente, puis recharger et
  redémarrer comme aux étapes 3 et 4 :
  ```bash
  cd /opt/ludotex
  for u in ludotex.service ludotex-formation.service ludotex-sauvegarde.service ludotex-sauvegarde.timer; do
      sudo -u pretjeux git show v1.14.0:deploy/$u | sudo tee /etc/systemd/system/$u > /dev/null
  done
  ```
  (étiquette de la version précédente ; l'adapter). Sans `LUDOTEX_ENV_FILE`,
  la formation relit le `.env` de la production, comme avant.
- *Fichier de la formation* : les deux lignes peuvent rester, elles sont sans
  effet sur une ancienne version (le jeton de la production y comblait déjà
  l'absence de la clé ; une clé vide l'ouvre).
- *Rétention* : revenir à l'ancienne version ramène la rotation à 30 : les
  archives de routine au-delà sont supprimées à la sauvegarde suivante. Les
  télécharger avant, si on y tient.

### Purger les journaux de journald — à faire plus tard, si vous le souhaitez, hors événement et après la section précédente

**Facultatif, et seulement une fois les étapes 3 à 5 ci-dessus faites** (sinon
de nouvelles lignes arrivent aussitôt). Le jeton bénévole a été réinitialisé le
2026-09-13 : les jetons présents dans journald ne sont plus valables. Restent
des codes personnels du planning. Relevé le 2026-09-18, en comptant sans
afficher : 290 lignes `jeton=` et 14 lignes `code=` sur les deux services.

**Ce que la purge détruit** : journald ne s'expurge pas ligne à ligne. On ne
peut que supprimer des fichiers de journal entiers, **tous services
confondus** : l'historique de diagnostic de LudoteX (erreurs, redémarrages),
mais aussi celui du système (connexions SSH, usage de `sudo`, noyau, mises à
jour). Ce qui n'est **pas** touché : les journaux de nginx (fichiers de
`/var/log/nginx/`), le journal d'activité de l'application, les bases.

D'où : **jamais pendant un événement ni juste après un incident** que l'on
voudrait encore comprendre.

1. Compter, sans rien afficher :
   ```bash
   sudo journalctl -u ludotex -u ludotex-formation --no-pager | grep -c "jeton="
   sudo journalctl -u ludotex -u ludotex-formation --no-pager | grep -c "code="
   journalctl --disk-usage
   ```
2. Clore le fichier en cours, puis supprimer tous les fichiers clos :
   ```bash
   sudo journalctl --rotate
   sudo journalctl --vacuum-time=1s
   ```
   *À voir :* une liste de fichiers `Deleted archived journal …` et l'espace
   libéré.
3. Recompter comme à l'étape 1. *À voir :* `0` et `0`, et `journalctl
   --disk-usage` presque nul.

Retour en arrière : aucun, la suppression est définitive.

### Filets de mise à jour renommés : rien à faire, un décalage à connaître

Cette version donne un nom propre aux archives qu'`update.sh` pose avant chaque
mise à jour (`avant-mise-a-jour-*.zip` au lieu de `ludotex-backup-*.zip`), pour
que la supervision ne les prenne plus pour la sauvegarde de nuit. Comme elle
modifie `update.sh`, **la mise à jour qui installe cette version pose encore son
archive sous l'ancien nom** : c'est l'ancien script qui s'exécute jusqu'au bout.

Aucun geste. Ce que l'on constate, et pourquoi c'est sans danger :

1. Juste après la mise à jour, lister le dossier :
   ```bash
   sudo ls -1t /var/lib/ludotex/sauvegardes/ | head -3
   ```
   *À voir :* en tête, une archive `ludotex-backup-…` à l'heure de la mise à
   jour. Elle tourne avec les sauvegardes de routine (les 60 plus récentes sont
   gardées) et en sortira d'elle-même, comme les filets des mises à jour
   précédentes, qui portent le même nom.
2. Pendant les 14 heures qui suivent, `/admin/supervision` peut compter cette
   archive comme la dernière sauvegarde de routine. La preuve que la
   sauvegarde automatique tourne reste le passage suivant du minuteur (03:00
   ou 15:00) : `docs/deploiement.md` § 7, commande 1.
3. **À la mise à jour suivante**, l'archive de l'étape 1 s'appelle
   `avant-mise-a-jour-…`, et la fin d'`update.sh` indique où la télécharger.

Retour en arrière : revenir à la version précédente ne demande rien non plus.
Les archives `avant-mise-a-jour-…` déjà posées restent restaurables, mais
l'ancien `sauvegarde.sh` ne les voit plus : elles ne seraient alors plus
purgées, à supprimer à la main au-delà de 30 jours.

### Mot de passe admin : retirer la copie en clair des fichiers d'environnement — à faire plus tard, si vous le souhaitez

**Facultatif** : l'application fonctionne à l'identique avec ou sans la ligne,
et le contrôle de report ne la réclame plus. Motif : `install.sh` écrivait le
mot de passe admin initial en clair dans `/opt/ludotex/.env` et
`/etc/ludotex-formation.env`. Il n'y sert plus à rien — le mot de passe vit
haché en base, et `ADMIN_PASSWORD` n'est plus jamais relue une fois ce hash
posé — mais il y reste lisible, périmé s'il a été changé depuis l'écran.
Cette version n'écrit plus cette ligne ; sur un serveur existant, elle est à
retirer à la main.

Chemins par défaut ci-dessous : les adapter si l'installation est ailleurs.
Faire la formation d'abord, la production ensuite. **Ne jamais afficher la
valeur de la ligne** (pas de `cat`, pas de `grep` sans `-c`).

1. Vérifier, **en lecture seule**, que chaque base porte déjà le mot de passe
   haché :
   ```bash
   sudo -u pretjeux sqlite3 "file:/var/lib/ludotex-formation/pret-jeux.db?mode=ro" "SELECT count(*) FROM parametres WHERE cle='admin_hash';"
   sudo -u pretjeux sqlite3 "file:/var/lib/ludotex/pret-jeux.db?mode=ro" "SELECT count(*) FROM parametres WHERE cle='admin_hash';"
   ```
   *À voir :* `1` pour chacune. **`0` : s'arrêter là pour cette instance.**
   Le hash n'est posé qu'à la première visite de `/admin` : ouvrir une fois
   l'écran de connexion de cette instance, puis relancer la commande.
2. Se connecter à `/admin` sur chacun des deux sites, avec le mot de passe
   que l'on connaît. **Échec : ne pas relire la ligne** pour le retrouver ;
   remplacer le mot de passe avec `scripts/reinitialiser_mot_de_passe.py`
   (`docs/deploiement.md` § 9), puis reprendre ici.
3. Retirer la ligne des deux fichiers, puis vérifier :
   ```bash
   sudo sed -i '/^ADMIN_PASSWORD=/d' /etc/ludotex-formation.env
   sudo sed -i '/^ADMIN_PASSWORD=/d' /opt/ludotex/.env
   sudo grep -c '^ADMIN_PASSWORD=' /etc/ludotex-formation.env /opt/ludotex/.env
   sudo stat -c '%a %U:%G %n' /etc/ludotex-formation.env /opt/ludotex/.env
   ```
   *À voir :* `…:0` pour les deux fichiers, puis `600 pretjeux:pretjeux` pour
   les deux (`sed -i` garde droits et propriétaire). `grep -c` compte la ligne
   sans l'afficher : si elle n'existait déjà plus, il affichait `0` avant le
   `sed`, sans conséquence.
4. Aucun redémarrage nécessaire : le service en marche garde l'ancienne valeur
   en mémoire, sans s'en servir. Pour le prouver tout de suite sur la
   formation :
   ```bash
   sudo systemctl restart ludotex-formation
   ```
   puis se reconnecter à `/admin` du site de formation. *À voir :* la
   connexion réussit. La production le constatera à son prochain redémarrage
   (mise à jour, ou redémarrage automatique du système).

Retour en arrière : sans objet tant que l'étape 1 a répondu `1` — la ligne
n'était plus lue. Si une base perdait un jour son hash (restauration d'une
archive très ancienne), l'administration afficherait « Aucun mot de passe
administrateur n'est défini » : le poser avec
`scripts/reinitialiser_mot_de_passe.py`, jamais en remettant la ligne.

---

## 1.14.0 — 2026-09-13

### Premier passage du contrôle de report

Cette version ajoute l'étape 7 (contrôle de report) et fait afficher, à
l'étape 6, la version qui répond. Comme elle modifie `update.sh`, **la mise à
jour qui l'installe s'exécute encore avec l'ancien script** : elle s'arrête à
une étape 6 sans numéro de version, et sans étape 7. C'est attendu.

1. Lancer la mise à jour comme d'habitude :
   ```bash
   cd /opt/ludotex && sudo ./deploy/update.sh
   ```
2. Juste après, lancer le contrôle une première fois à la main :
   ```bash
   sudo -u pretjeux /opt/ludotex/.venv/bin/python /opt/ludotex/scripts/controle_report.py
   ```
   *À voir :* six lignes, chacune « conforme » ou « à examiner », puis un
   bilan. Rien n'a été modifié.
3. Vérifier que le nouveau code répond :
   ```bash
   curl -s http://127.0.0.1:8000/sante
   ```
   *À voir :* `{"statut":"ok","version":"…"}`, avec le numéro de la version
   installée. Pour l'instance de formation, le port est celui de son unité
   (`grep -- --port /etc/systemd/system/ludotex-formation.service`).
4. Traiter chaque ligne « à examiner » **après décision**, jamais à la chaîne :
   nginx selon `docs/deploiement.md` § 8, le reste selon la section de la
   version qui l'a introduit.

Deux cas, propres à une installation antérieure à cette version, peuvent
apparaître dans les lignes « à examiner » de ce premier passage :

- **le dossier des sauvegardes de l'instance de formation, si elle existe,
  peut être absent** — si elle a été installée avant qu'`install.sh` ne le
  crée. Le créer, avec les droits et le propriétaire attendus :
  ```bash
  sudo install -d -m 700 -o pretjeux -g pretjeux /var/lib/ludotex-formation/sauvegardes
  ```
  (chemin par défaut ; l'adapter si l'installation est ailleurs).
- **le fichier d'environnement de l'instance de formation, si elle existe,
  peut ne porter ni le jeton bénévole ni la limite de débit.** Laisser cette
  ligne « à examiner » pour l'instant : la correction attend une version à
  venir, et un geste improvisé ici la devancerait mal.

Retour en arrière : aucun. Ces gestes ne font que lire.

À partir de la mise à jour suivante, l'étape 7 s'exécute d'elle-même.

### Sauvegarde de nuit : passer de cron au minuteur systemd

La tâche cron posée par `install.sh` écrivait son journal dans `/var/log/`, où
`pretjeux` ne peut pas créer de fichier : elle échouait chaque nuit avant même
de lancer la sauvegarde. Cette version la remplace par deux unités,
`ludotex-sauvegarde.service` et `ludotex-sauvegarde.timer`. `update.sh` ne pose
pas d'unité : **ces gestes sont nécessaires sur tout serveur existant**, que sa
crontab soit restée cassée ou qu'elle ait été réparée à la main. Réparée, elle
fonctionne — mais laissée à côté du minuteur, elle produirait deux archives par
nuit.

Chemins par défaut ci-dessous (`/opt/ludotex`, `/var/lib/ludotex`) : les
adapter si l'installation est ailleurs.

1. Faire la mise à jour comme d'habitude, puis constater l'état de départ :
   ```bash
   sudo crontab -u pretjeux -l
   systemctl list-timers ludotex-sauvegarde.timer
   ```
   *À voir :* une ligne `0 3 * * * …/deploy/sauvegarde.sh …` (redirigée vers
   `/var/log/…` si elle n'a jamais été réparée, vers `/var/lib/…` sinon) — ou
   rien, si aucune tâche n'avait été posée — puis `0 timers listed.`
   L'étape 7 de la mise à jour nomme les mêmes écarts : unités absentes, tâche
   cron encore présente.

2. Installer et démarrer le minuteur :
   ```bash
   sudo cp /opt/ludotex/deploy/ludotex-sauvegarde.service /opt/ludotex/deploy/ludotex-sauvegarde.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now ludotex-sauvegarde.timer
   systemctl list-timers ludotex-sauvegarde.timer
   ```
   *À voir :* `1 timers listed.`, et dans la colonne `NEXT` la prochaine nuit à
   03:00. `LAST` est vide tant qu'il n'a jamais tourné.

3. Lancer **une** sauvegarde tout de suite, par le service lui-même — c'est ce
   qui prouve que l'unité fonctionne, pas seulement le script. La commande rend
   la main une fois la sauvegarde finie :
   ```bash
   sudo systemctl start ludotex-sauvegarde.service
   systemctl status ludotex-sauvegarde.service --no-pager
   sudo ls -1t /var/lib/ludotex/sauvegardes/ | head -1
   ```
   *À voir :* `status=0/SUCCESS` et la ligne `Sauvegarde créée : …` ; la
   dernière archive porte l'heure de la commande.
   **Si le statut est `failed`, s'arrêter là** et lire
   `sudo journalctl -u ludotex-sauvegarde -e --no-pager` : la tâche cron est
   encore en place, rien n'est perdu.

4. **Seulement une fois l'étape 3 réussie**, retirer la tâche cron. Les autres
   lignes éventuelles de la crontab sont gardées :
   ```bash
   sudo crontab -u pretjeux -l | grep -v 'deploy/sauvegarde.sh' | sudo crontab -u pretjeux -
   sudo crontab -u pretjeux -l
   ```
   *À voir :* plus aucune ligne qui mentionne `sauvegarde.sh` (sur une crontab
   qui ne contenait qu'elle : aucune sortie). Sans crontab au départ, la
   première commande affiche `no crontab for pretjeux` et n'a aucun effet
   gênant.

5. Relancer le contrôle de report :
   ```bash
   sudo -u pretjeux /opt/ludotex/.venv/bin/python /opt/ludotex/scripts/controle_report.py
   ```
   *À voir :* « Unités systemd : conforme » et « Tâche de sauvegarde planifiée :
   conforme ». Les autres lignes ne dépendent pas de ce geste.

6. **Le lendemain matin**, vérifier le résultat selon `docs/deploiement.md`
   § 7 : `status=0/SUCCESS` à 03:00, et une archive `ludotex-backup-AAAAMMJJ-03xxxx.zip`
   à la date du jour. C'est la seule preuve que la sauvegarde de nuit tourne.

Retour en arrière (revenir à cron) :

```bash
sudo systemctl disable --now ludotex-sauvegarde.timer
( sudo crontab -u pretjeux -l 2>/dev/null; echo '0 3 * * * /opt/ludotex/deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes >> /var/lib/ludotex/sauvegarde.log 2>&1' ) | sudo crontab -u pretjeux -
```

Le journal reste alors dans `/var/lib/ludotex/`, **jamais dans `/var/log/`**.
