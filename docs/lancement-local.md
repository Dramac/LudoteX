# Lancement local sur un poste

Ce mode de lancement sert à tester ou faire fonctionner LudoteX **sur un poste
de l'association**. **Le démarrage de tous les jours se fait en double-cliquant
sur un fichier, sans terminal** ; en revanche, **l'installation de départ, elle,
demande un terminal, une seule fois** (une dizaine de commandes à recopier, plus
bas). Il vise d'abord Windows, mais fonctionne aussi sous macOS
(`lancer.command`) et depuis un terminal sur n'importe quel système.
Il ouvre un tunnel HTTPS public (Cloudflare) au-dessus de l'application locale
— nécessaire pour que le scanner caméra fonctionne depuis un smartphone
(`getUserMedia` exige un contexte sécurisé HTTPS).

Pour un déploiement permanent sur un vrai serveur, voir plutôt
`docs/deploiement.md` (VPS + domaine + HTTPS Let's Encrypt).

## Prérequis (à faire une fois)

1. **Python 3.11 ou plus récent**, installé sur le poste (python.org, ou le
   Microsoft Store sous Windows). Le vérifier dans un terminal :
   `python --version` (ou `py --version` sous Windows).

2. **Le projet installé**, avec son environnement virtuel `.venv` à la racine.
   Dans un terminal, depuis le dossier du projet :

   ```
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt          # macOS, Linux
   .venv\Scripts\pip install -r requirements.txt      # Windows
   ```

   Sans ce dossier `.venv`, aucun des fichiers de lancement ne démarre :
   `lancer.command` et `lancer.bat` le disent, `lancer.vbs` s'arrête sans
   rien afficher — c'est la première chose à vérifier si « rien ne se passe ».

3. **Le fichier `.env`**, copié depuis le modèle (`cp .env.example .env`, ou
   copier-coller sous Windows), puis **deux valeurs à changer**. Recopier le
   modèle tel quel est une erreur à ne pas faire, parce que le tunnel rend le
   site **public** :

   - `PRET_TOKEN` : le modèle porte une valeur d'exemple, **que l'application
     ignore** — le site est alors en « mode ouvert », où n'importe quelle
     personne qui connaît l'adresse peut enregistrer des prêts. Générer un
     vrai jeton et le coller à la place :
     `.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"`
     (`.venv\Scripts\python` sous Windows).
   - le **mot de passe administrateur** : la valeur d'exemple `ADMIN_PASSWORD`
     du modèle est **refusée**, l'administration reste fermée tant qu'elle est
     là. Ne pas y toucher et poser le mot de passe par le script prévu pour
     cela (étape 4) — il le lit au clavier, sans l'écrire dans un fichier.

   Les autres lignes du modèle conviennent telles quelles pour un essai
   (`BASE_URL` ne sert qu'aux QR imprimés, voir « Limites » plus bas).

4. **La base initialisée, puis le mot de passe administrateur** :

   ```
   .venv/bin/python -m app.db
   .venv/bin/python scripts/reinitialiser_mot_de_passe.py
   ```

   (même commande avec `.venv\Scripts\python` sous Windows). Le second script
   affiche la base qu'il va modifier, demande confirmation, puis lit le mot de
   passe deux fois, au clavier, sans écho ; il en refuse un de moins de huit
   caractères. C'est aussi le recours en cas de mot de passe oublié : le
   relancer suffit.

5. **`cloudflared`** (l'utilitaire de tunnel de Cloudflare), accessible d'une
   des deux façons :
   - installé et présent dans le PATH Windows, ou
   - son exécutable `cloudflared.exe` simplement déposé **à la racine du
     projet** (à côté de `lancer.py`).

   ### Installer `cloudflared` sur Windows

   Deux options, au choix :

   - **Téléchargement direct** (le plus simple, sans droits admin) :
     télécharger `cloudflared-windows-amd64.exe` depuis la page des
     [releases GitHub de cloudflared](https://github.com/cloudflare/cloudflared/releases),
     le renommer en `cloudflared.exe`, et le placer à la racine du projet
     (à côté de `lancer.py`, `lancer.vbs`, `lancer.bat`).

   - **Via winget** (si disponible sur le poste) :
     ```
     winget install --id Cloudflare.cloudflared
     ```
     Après installation, `cloudflared` est disponible dans n'importe quel
     terminal (PATH) — pas besoin de le copier dans le projet.

   Vérifier l'installation en ouvrant une invite de commande et en tapant
   `cloudflared --version` (ou en exécutant `cloudflared.exe --version` depuis
   le dossier du projet si déposé localement).

## Utilisation au quotidien

- **Double-cliquer sur `lancer.vbs`** : démarre tout en arrière-plan, sans
  fenêtre console. Une page s'ouvre dans le navigateur avec :
  - un QR code à scanner depuis un smartphone (accès direct à l'application),
  - l'URL publique en grand, cliquable/copiable,
  - un indicateur de statut (application / tunnel),
  - un bouton rouge **« Arrêter LudoteX »**.

- **Double-cliquer sur `lancer.bat`** à la place si quelque chose ne
  fonctionne pas comme prévu : la console reste visible et affiche les
  messages (démarrage d'uvicorn, URL du tunnel, erreurs éventuelles).

- **Sous macOS**, double-cliquer sur **`lancer.command`** : c'est le pendant de
  `lancer.bat`, le Terminal s'ouvre et reste visible. Si le double-clic ouvre
  le fichier dans un éditeur au lieu de l'exécuter, son bit d'exécution a été
  perdu ; le remettre une fois, dans le Terminal : `chmod +x lancer.command`.

- **Depuis un terminal**, sur n'importe quel système : `python lancer.py`.
  Peu importe l'interpréteur employé — le lanceur se remet de lui-même dans le
  `.venv` du projet s'il n'y est pas, et l'annonce (« Relance avec
  l'interpréteur du projet : … »).

- Pour arrêter : cliquer sur **« Arrêter LudoteX »** dans la page ouverte
  (confirmation demandée), ou fermer la console si lancé via `lancer.bat` /
  `lancer.command` (Ctrl+C). Le site de formation, s'il a été lancé, s'arrête
  en même temps.

### Si l'application « n'a pas démarré à temps »

Le lanceur attend 30 secondes qu'uvicorn ouvre son port. Passé ce délai, il
affiche la **fin de l'erreur** et le **chemin complet** du journal, à recopier
tel quel :

```
data/uvicorn-lancer.log             (application)
data/uvicorn-formation-lancer.log   (site de formation)
```

Ce journal est écrasé à chaque démarrage : il contient toujours la cause de la
dernière tentative, jamais un historique à faire défiler.

## Lancer aussi le site de formation (`--formation`)

Pour former des bénévoles sans toucher aux vraies données, on peut démarrer en
plus une **seconde instance** en mode formation (bandeau + filigrane, bases
jetables). Depuis un terminal, à la racine du projet :

```
python lancer.py --formation
```

N'importe quel interpréteur convient : le lanceur bascule seul sur celui du
`.venv`. On peut aussi créer un raccourci Windows vers `lancer.bat --formation`,
ou lancer `./lancer.command --formation` sous macOS.

Cela démarre, **en plus** de l'application normale :

- le **site de formation** sur `http://localhost:8100` (accessible sur cet
  ordinateur ; il n'a pas de tunnel Cloudflare, il reste local) ;
- ses propres bases jetables `data/formation-*.db` (jamais celles de
  production). Le **premier** lancement les peuple de données fictives (jeux
  d'essai, un tournoi d'exemple) ; les lancements suivants les conservent.

Le lien **« 🎓 Site de formation »** apparaît alors dans le tableau de bord
admin de l'application normale, et l'URL de formation est rappelée sur la page
du lanceur. Pour repartir d'un état propre, utiliser le bouton
**« Réinitialiser les données de formation »** dans l'admin du site de
formation (ou supprimer les fichiers `data/formation-*.db`).

## Ce que fait `lancer.py`

0. Se relance avec l'interpréteur du `.venv` du projet s'il n'y est pas déjà
   (une seule fois : un `.venv` présent mais incomplet donne un message clair,
   pas une boucle).
1. Vérifie que `.venv` et `cloudflared` sont bien présents, et que les ports
   8000 (application) et 8001 (contrôle) sont libres — sinon ouvre une page
   d'erreur claire et s'arrête.
2. Démarre `uvicorn` en arrière-plan (port 8000, local uniquement).
3. Démarre `cloudflared tunnel --url http://localhost:8000`, qui expose
   l'application sur une URL publique `https://xxxx.trycloudflare.com`.
4. Démarre un petit serveur de contrôle local (port 8001), qui sert la page
   du lanceur (QR + URL + statut + bouton d'arrêt), son statut en temps réel
   et l'arrêt propre.
5. Ouvre `http://127.0.0.1:8001/` dans le navigateur par défaut. La page est
   servie par le serveur de contrôle lui-même, et non plus ouverte comme un
   fichier : elle n'a donc besoin d'aucune autorisation « cross-origin », et
   aucune autre page du navigateur ne peut lire l'URL du tunnel ni arrêter
   LudoteX.

## Limites à connaître

- **L'URL change à chaque lancement** (tunnel Cloudflare gratuit, sans compte).
  Les QR imprimés à l'avance ne fonctionnent donc **pas** avec ce mode — ils
  sont réservés au déploiement définitif sur le domaine fixe (voir
  `docs/deploiement.md`). En attendant, scanner le QR affiché sur l'écran du
  lanceur, ou utiliser le lien partagé aux bénévoles pour l'activation
  (`/admin/jeton`).
- Le poste doit rester allumé et connecté à Internet pendant toute la durée
  d'utilisation (le tunnel et l'application tournent dessus).
- Fermer LudoteX (bouton « Arrêter ») avant d'éteindre le poste, pour une
  coupure propre.
