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
| Tâche de sauvegarde | la ligne de crontab du service comparée à celle que pose `install.sh` |
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
| Le jeton bénévole, les données de démonstration de la formation | ce sont des données, pas de la configuration du serveur |
| `FORMATION_URL` dans le `.env` de production | clé facultative, propre à une instance ; son absence masque seulement un lien |

---

## À paraître

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

Retour en arrière : aucun. Ces gestes ne font que lire.

À partir de la mise à jour suivante, l'étape 7 s'exécute d'elle-même.
