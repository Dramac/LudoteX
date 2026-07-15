# Lancement local sans ligne de commande (Windows)

Ce mode de lancement sert à tester ou faire fonctionner LudoteX **sur un poste
Windows de l'association**, sans terminal, en double-cliquant sur un fichier.
Il ouvre un tunnel HTTPS public (Cloudflare) au-dessus de l'application locale
— nécessaire pour que le scanner caméra fonctionne depuis un smartphone
(`getUserMedia` exige un contexte sécurisé HTTPS).

Pour un déploiement permanent sur un vrai serveur, voir plutôt
`docs/deploiement.md` (VPS + domaine + HTTPS Let's Encrypt).

## Prérequis (à faire une fois)

1. **Le projet installé** avec son environnement virtuel `.venv` à la racine
   (voir la section « Lancer en local » du `CLAUDE.md`/`README.md` :
   `python -m venv .venv`, `pip install -r requirements.txt`, `.env` renseigné,
   base initialisée).

2. **`cloudflared`** (l'utilitaire de tunnel de Cloudflare), accessible d'une
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

- Pour arrêter : cliquer sur **« Arrêter LudoteX »** dans la page ouverte
  (confirmation demandée), ou fermer la console si lancé via `lancer.bat`
  (Ctrl+C).

## Ce que fait `lancer.py`

1. Vérifie que `.venv` et `cloudflared` sont bien présents, et que les ports
   8000 (application) et 8001 (contrôle) sont libres — sinon ouvre une page
   d'erreur claire et s'arrête.
2. Démarre `uvicorn` en arrière-plan (port 8000, local uniquement).
3. Démarre `cloudflared tunnel --url http://localhost:8000`, qui expose
   l'application sur une URL publique `https://xxxx.trycloudflare.com`.
4. Génère une page HTML temporaire (QR + URL + statut + bouton d'arrêt) et
   l'ouvre dans le navigateur par défaut.
5. Démarre un petit serveur de contrôle local (port 8001) qui permet à cette
   page d'afficher le statut en temps réel et de tout arrêter proprement.

## Limites à connaître

- **L'URL change à chaque lancement** (tunnel Cloudflare gratuit, sans compte).
  Les QR imprimés à l'avance ne fonctionnent donc **pas** avec ce mode — ils
  sont réservés au déploiement définitif sur le domaine fixe (voir
  `docs/deploiement.md`). En attendant, scanner le QR affiché sur l'écran du
  lanceur, ou utiliser le lien partagé aux bénévoles pour l'activation
  (`/admin/jeton`).
- Le poste Windows doit rester allumé et connecté à Internet pendant toute la
  durée d'utilisation (le tunnel et l'application tournent dessus).
- Fermer LudoteX (bouton « Arrêter ») avant d'éteindre le poste, pour une
  coupure propre.
