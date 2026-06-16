# Bonnes pratiques & conseils — projet pret-jeux

Mémo des conseils accumulés au fil du développement. Complété à chaque étape.
Pour le contexte technique du projet, voir `CLAUDE.md` ; pour la conception,
`docs/specification.md`.

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
- La planche PDF est en **1-bit (noir/blanc par seuil)** : net à l'impression et
  compatible avec les builds Pillow sans codec JPEG. Conséquence : les cadres de
  placeholder sont tracés en **noir** (le gris disparaîtrait au seuillage).
- **Étiquette** = QR + emplacement logo (placeholder, `--logo` pour le vrai) +
  cercle gommette de couleur + code jeu/nom + code de classement. La grille
  d'impression est réglable (`--grille LxC`) pour coller aux planches
  autocollantes du commerce.
- **Code de classement** (type `EAM8-3-5-15`) : nomenclature des lettres pas
  encore figée → la partie chiffrée (âge, joueurs, durée) est déduite des
  données, les lettres restent `XXX`. Tout est centralisé dans `code_classement()`.
  Un logo en couleur nécessitera d'adapter la planche (le 1-bit l'écraserait).

## Mémoire & continuité

- **`CLAUDE.md`** (versionné) = contrat du projet, relu à chaque session : stack,
  règles métier, décisions, état d'avancement. Tenu à jour en fin d'étape.
- **`bonne-pratique.md`** (ce fichier) = conseils transverses, complété au fil de
  l'eau.
