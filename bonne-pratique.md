# Bonnes pratiques & conseils — projet pret-jeux

Mémo des conseils accumulés au fil du développement. Complété à chaque étape.
Pour le contexte technique du projet, voir `CLAUDE.md` ; pour la conception,
`docs/specification.md`.

## Tester en local

Sur Mac (la commande Python est `python3`). Première fois seulement :

```bash
cd ~/Documents/Claude/Projects/DJPLM
python3 -m venv .venv          # crée l'environnement virtuel (une seule fois)
source .venv/bin/activate      # l'invite passe à (.venv)
pip install -r requirements.txt
python -m app.db               # crée la base SQLite vide
python -m scripts.import_csv <chemin_du_catalogue.csv>   # remplit le catalogue
```

Ensuite, à chaque session, il suffit de :

```bash
cd ~/Documents/Claude/Projects/DJPLM
source .venv/bin/activate
uvicorn app.main:app --reload
```

Puis ouvrir `http://localhost:8000/pret/001` (écran bénévole) ou `/jeu/001`
(fiche publique). `deactivate` pour sortir de l'environnement.

> Si `source .venv/bin/activate` répond *no such file or directory*, c'est que
> le `.venv` n'a pas encore été créé → refaire `python3 -m venv .venv`.

### Tester sur smartphone (scan caméra) — tunnel HTTPS

Le scanner caméra exige HTTPS. Pour tester depuis un téléphone, exposer le
serveur local via un tunnel **Cloudflare** (gratuit, sans compte) :

```bash
brew install cloudflared            # une seule fois (nécessite Homebrew)
```

Deux fenêtres de Terminal : l'une lance `uvicorn app.main:app --reload`,
l'autre :

```bash
cloudflared tunnel --url http://localhost:8000
```

cloudflared affiche une URL `https://….trycloudflare.com` (elle change à chaque
lancement). L'ouvrir sur le téléphone, ex. `https://….trycloudflare.com/pret/001`.
`Ctrl+C` dans chaque fenêtre pour arrêter.

## Git & GitHub

- **Qui pousse ?** L'assistant édite et commit en local, mais **ne peut pas
  pousser** (pas de connecteur GitHub ni de CLI `gh` dans son environnement).
  C'est **toi** qui exécutes `git push` après validation de chaque étape.
- **Authentification (remote HTTPS).** GitHub n'accepte plus le mot de passe
  de compte au push : il faut un **Personal Access Token**.
  - *Fine-grained* limité au seul dépôt `pret-jeux`, permission
    **Contents : Read and write** (le plus sûr), ou *classic* avec le scope `repo`.
  - Le token se colle comme « mot de passe » **dans le Terminal uniquement** —
    jamais dans le chat, jamais committé.
- **Alternative SSH** (propre sur le long terme) : générer une clé
  (`ssh-keygen -t ed25519 -C "ton-email"`), ajouter la clé *publique*
  `~/.ssh/id_ed25519.pub` dans GitHub → Settings → SSH and GPG keys, et garder
  le remote `git@github.com:...`.
- **Créer le dépôt distant VIDE** (sans README ni .gitignore auto-générés) pour
  éviter un conflit d'historique au premier `push`.
- **Piège zsh : les chevrons `< >`.** Ne jamais laisser les `<...>` d'un
  exemple de commande : zsh les interprète comme des redirections de fichier
  (d'où l'erreur `read-only file system`). Remplacer par la vraie valeur, sans
  chevrons. En cas de doute, copier l'URL HTTPS via le bouton vert « Code » du
  dépôt.

## Sécurité du dépôt

- Ne **jamais** committer : le **jeton bénévole**, le fichier **`.env`**, la
  **base SQLite**. Ils sont exclus par `.gitignore` (vérifié). Utiliser
  `.env.example` comme modèle.
- Le **CSV du catalogue** n'est pas versionné non plus (c'est de la donnée) :
  l'import se lance à la demande, le fichier reste chez toi.

## Tests & scanner caméra

- **Le scanner caméra (`getUserMedia`) n'autorise la caméra qu'en contexte
  sécurisé : HTTPS ou `localhost`.** Un simple accès HTTP sur le réseau local
  ne permettra PAS la caméra sur le téléphone.
- **Tester depuis l'ordinateur** : `uvicorn app.main:app --reload`, puis
  `http://localhost:8000` (la caméra marche sur localhost).
- **Tester depuis le smartphone** : monter un **tunnel HTTPS** (Cloudflare
  Tunnel ou ngrok) au-dessus d'`uvicorn` local → URL HTTPS temporaire, sans rien
  déployer. C'est l'environnement de test retenu.
- **Production** : déploiement VPS + HTTPS Let's Encrypt, dans un second temps.

## Architecture & lien avec WordPress

- **L'application de prêt est autonome** : FastAPI sert ses propres pages, elle
  n'a besoin de WordPress ni pour tourner ni pour être testée. WordPress est la
  brique « site + newsletter », volontairement cloisonnée.
- **Surface depuis WordPress, deux options :**
  - *Un lien* vers l'app (`pret.<domaine>` pour les bénévoles, lien vers la
    consultation publique pour le public) — **recommandé**, conforme au design
    en deux briques.
  - *Une iframe* : à réserver à l'**incrustation du catalogue public**
    (consultation, sans action). Pour l'outil bénévole, préférer la PWA plein
    écran. Une caméra en iframe cross-domaine exige `allow="camera"` + HTTPS des
    deux côtés.

## Modèle de données

- **Deux clés stables, non négociables** : `id_exemplaire` (boîte physique,
  dans le QR) et `reference_titre` (regroupement par jeu, pour les stats).
- **`id_exemplaire` en TEXT** pour préserver un éventuel zéro de tête (`001`,
  `00472`) — ne jamais le réinterpréter comme un entier.
- **`reference_titre` dérivée du nom normalisé** (majuscules, sans accents ni
  ponctuation) → regroupe automatiquement les exemplaires d'un même jeu. La
  normalisation rattrape les variantes de casse/accents (« Jamaica »/« Jamaïca »).
  Toujours **vérifier les fusions** sur le rapport généré avant de figer.
- **Découper les plages en valeurs numériques** : « 2 - 4 joueurs » →
  `nb_joueurs_min`/`max`. Indispensable pour une recherche correcte
  (`min <= X AND max >= X`) — impossible de façon fiable sur du texte. L'affichage
  « 2 à 4 joueurs » se reconstruit à partir des deux nombres.
- **L'état d'un exemplaire est déduit** (prêt avec `date_retour IS NULL`), jamais
  stocké en dur. Les colonnes d'état du CSV (Suspendu, Prêt en cours, etc.) sont
  donc ignorées à l'import.
- **Import idempotent** (UPSERT sur les clés) : relançable sans créer de doublon,
  pour réimporter après mise à jour du CSV.
- **« Lien image » du CSV inutilisable tel quel** : ce sont des chemins Windows
  locaux (`C:\Ludopret\...`). Les images devront être réhébergées plus tard.

## QR codes

- **L'URL encodée dans un QR est DÉFINITIVE.** Une fois imprimé et collé, un QR
  ne doit jamais changer (la fiche `/jeu/<id>` doit rester accessible à cette
  adresse). **Ne lancer le tirage des ~700 étiquettes qu'une fois le nom de
  domaine réservé et figé.** Avant ça, générer des QR de test (BASE_URL = tunnel
  HTTPS ou `http://localhost:8000`).
- Le QR ne contient **aucun secret** : il donne le même accès que le catalogue
  public (lecture seule). Pas de jeton dans l'URL.
- `BASE_URL` vient du `.env` → le jour J, on régénère tout avec la bonne URL en
  une commande, sans toucher au code.
- La planche PDF est produite avec **reportlab** : elle gère les images
  **couleur** (logo) sans dépendre du codec JPEG de Pillow (absent de certains
  builds). Chaque étiquette est mise à l'échelle dans sa cellule en conservant
  ses proportions ; marge de cellule réglable.
- **Étiquette format paysage** = QR à gauche ; à droite : emplacement logo
  agrandi (placeholder, `--logo logo_djplm.jpg` pour le vrai), cercle gommette de
  couleur, nom du jeu, code de classement. Pas de numéro affiché (il est dans le
  QR). La grille d'impression est réglable (`--grille LxC`, défaut 8x2) pour
  coller aux planches autocollantes du commerce.
- **Code de classement** (type `EAM8-3-5-15`) : nomenclature des lettres pas
  encore figée → la partie chiffrée (âge, joueurs, durée) est déduite des
  données, les lettres restent `XXX`. Tout est centralisé dans `code_classement()`.

## Scanner caméra

- Décodage QR via **jsQR** (canvas + getUserMedia), choisi pour sa compatibilité
  **iOS Safari ET Android** ; l'API native `BarcodeDetector` est plus rapide mais
  absente d'iOS — à éviter comme unique solution.
- `getUserMedia` n'autorise la caméra qu'en **contexte sécurisé** (HTTPS ou
  localhost) → tester via le tunnel. La balise `<video>` doit avoir `playsinline`
  et `muted` (autoplay iOS).
- jsQR est **hébergé en local** (`static/js/jsQR.js`, versionné) : aucune
  dépendance CDN, tout est servi depuis notre origine.
- Repli prévu : si la caméra est indisponible/refusée, message invitant à scanner
  le QR avec l'appareil photo natif (qui ouvre la fiche publique `/jeu/<id>`).

## Authentification bénévole (jeton)

- Un seul **jeton** (`PRET_TOKEN` dans `.env`) protège `/pret/*` et `/scanner` ;
  le reste (catalogue, fiches, stats) est public. Pas de comptes individuels.
- **Activation** : distribuer aux bénévoles le lien `/acces?jeton=<JETON>` (via
  le canal interne). L'appareil mémorise le jeton dans un cookie (1 an). Le jeton
  n'apparaît jamais dans les pages.
- **Rotation annuelle** : changer `PRET_TOKEN` invalide tous les anciens cookies.
  Générer : `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **Sécurité** : comparaison en temps constant (`secrets.compare_digest`), cookie
  HttpOnly + SameSite=Lax + Secure (en HTTPS), limitation de débit par IP sur
  `/acces` (`RATE_LIMIT_PER_MINUTE`).
- **Dev/local** : si `PRET_TOKEN` n'est pas défini, l'accès est ouvert (pratique
  pour tester) et un avertissement s'affiche au démarrage. **NE PAS déployer sans
  jeton.**
- Le limiteur de débit est **en mémoire** (un seul process uvicorn). Avec
  plusieurs workers, prévoir un store partagé (étape déploiement).

## Mémoire & continuité

- **`CLAUDE.md`** (versionné) = contrat du projet, relu à chaque session : stack,
  règles métier, décisions, état d'avancement. Tenu à jour en fin d'étape.
- **`bonne-pratique.md`** (ce fichier) = conseils transverses, complété au fil de
  l'eau.
