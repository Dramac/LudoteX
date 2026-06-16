# CLAUDE.md — Contexte projet pour l'assistant

Fichier de contexte relu à chaque session de développement. Le tenir à jour
à la fin de chaque étape. La **conception fait foi dans `docs/specification.md`** ;
ce fichier en est un résumé opérationnel, pas une source concurrente.

## Le projet

Application web de **prêt de jeux de société** pour l'événement annuel d'une
association (~700 jeux). Les bénévoles scannent un QR par exemplaire avec leur
smartphone pour enregistrer prêts et retours sur une base partagée, en
remplacement de la feuille papier (goulet d'étranglement). Anti-vol par
**numéro de pochette** où l'on dépose la pièce d'identité → **zéro donnée
personnelle**, hors champ RGPD.

Ce dépôt = **brique de prêt uniquement**. Le site vitrine + newsletter
(WordPress, hébergement mutualisé) est une brique séparée, hors dépôt.

## Stack

Python + **FastAPI**, servi par `uvicorn`. Base **SQLite**. Pages servies par
le backend (Jinja2) + un peu de **JS uniquement** pour le scanner caméra
embarqué. **PWA** (« ajouter à l'écran d'accueil »). Déploiement cible : VPS
Lite (Debian/Ubuntu), HTTPS Let's Encrypt.

## Règles métier non négociables

- **Deux clés stables**, quelles que soient les évolutions du CSV :
  - `id_exemplaire` — boîte physique unique, encodée dans le QR sous forme
    d'URL `/jeu/<id_exemplaire>`. Ne change jamais une fois le QR imprimé.
  - `reference_titre` — regroupement des exemplaires d'un même jeu (stats).
- **Numéro de pochette** : commence à 1, on attribue toujours le **plus petit
  numéro libre**, recyclé au retour, **AUCUN plafond** (on ne refuse jamais un
  prêt). Un seul jeu par PI / par numéro. Le numéro reste physiquement attaché
  à la PI.
- **Logique de scan** :
  - exemplaire **DISPONIBLE** → action unique « Prêter » (attribue + affiche le
    numéro de pochette en grand).
  - exemplaire **SORTI** → deux actions : « Rendre » (principale, libère le
    numéro) et « Le re-prêter » (cas d'oubli de scan : clôt l'ancien prêt puis
    en rouvre un avec un nouveau numéro).
- **Ne jamais bloquer** : toute incohérence → message + action de rattrapage en
  un tap, jamais d'erreur bloquante.
- **Séparation lecture / écriture** : fiches/catalogue publics et sans action ;
  prêt/retour derrière un **jeton aléatoire long** (~32 car.) mémorisé côté
  appareil, + **limitation de débit par IP**. Pas de comptes individuels.
  Rotation annuelle du jeton.
- **Zéro donnée personnelle** dans l'app de prêt — propriété à préserver.

## Modèle de données (4 tables — voir spec §3 et `app/models.py`)

- `titres` : `reference_titre` (PK), `nom`, `categorie` + colonnes optionnelles
  nullables (`nb_joueurs_min/max`, `duree_min`, `age_min`, `editeur`, `auteur`,
  `annee_edition`, `descriptif`).
- `exemplaires` : `id_exemplaire` (PK, TEXT), `reference_titre` (FK).
- `prets` : `id_pret` (PK auto), `id_exemplaire` (FK), `numero_pochette`,
  `date_sortie`, `date_retour` (NULL tant que sorti). Historique jamais purgé.
- `pochettes` : `numero_pochette` (PK), `occupe` (0/1). Occupation du moment.

## Décisions de conception déjà prises

- `id_exemplaire` stocké en **TEXT** (préserve un éventuel zéro de tête, ex.
  `00472` ; jamais réinterprété comme un entier).
- `titres` : colonnes de cœur + colonnes optionnelles nullables (choix validé).
  L'import CSV remplira ce qu'il trouve ; le schéma peut évoluer sans toucher
  aux deux clés.
- SQLite ouvert avec `PRAGMA foreign_keys = ON` et `journal_mode = WAL`
  (concurrence d'écriture entre bénévoles).
- État d'un exemplaire **déduit** (prêt avec `date_retour IS NULL`), pas stocké.

## Workflow de développement

- L'assistant édite les fichiers dans le dossier local et commit en local.
  **L'assistant ne peut PAS pousser** (pas de connecteur GitHub ni de CLI `gh`
  dans son environnement) → **c'est Simon qui exécute `git push`** après
  validation de chaque étape.
- Remote configuré en **HTTPS** (auth par Personal Access Token côté Terminal
  de Simon ; le token ne transite jamais par le chat).
- **Environnement de test retenu : tunnel HTTPS** (type Cloudflare Tunnel /
  ngrok) au-dessus de `uvicorn` local, pour tester le **scan caméra depuis un
  smartphone**. Raison : le scanner caméra (`getUserMedia`) exige un contexte
  sécurisé (HTTPS ou `localhost`). Déploiement VPS dans un second temps.

## Séquence de dev (brief §6) — état

1. [fait] Structure du dépôt + `requirements.txt` + README.
2. [fait] Schéma SQLite (`app/models.py`) + init (`app/db.py`).
3. [fait] `scripts/import_csv.py` — import tolérant. CSV réel reçu
   (`Liste_Jeux_Etendue_140626.csv`, 703 lignes, séparateur `;`, UTF-8 BOM).
   Mapping : « Code jeu »→`id_exemplaire` (TEXT, zéros de tête), nom nettoyé,
   `reference_titre`=slug du nom (REGROUPEMENT par nom, validé par Simon),
   « Type jeu »→`categorie`, parsing « Nb joueurs » 2-4→min/max, « Age » 10+→10,
   « Temps jeu »→`duree_min`, « Marque »→`editeur`, + descriptif/auteur/année.
   Colonnes d'état du CSV ignorées (état déduit des prêts). Idempotent (UPSERT).
   Résultat : **609 titres / 703 exemplaires**, 0 FK orpheline. Regroupements
   à noms divergents (28) tous vérifiés corrects (casse/accents). « Lien image »
   non importé (chemins Windows locaux inutilisables).
4. [fait] `scripts/generate_qr.py` — un QR par exemplaire encodant
   `<BASE_URL>/jeu/<id_exemplaire>`. Lit les exemplaires en base. PNG individuels
   `<id>.png` avec libellé « code — nom » ; option `--planche` → PDF A4 (grille
   4×6, pages converties 1-bit pour éviter le codec JPEG absent de Pillow).
   `BASE_URL` depuis `.env`, surchargeable par `--base-url`. Décodage vérifié
   (OpenCV) : URL exacte. **URL définitive : ne tirer les étiquettes qu'une fois
   le domaine figé** ; avant, QR de test (tunnel/localhost). QR exclus du dépôt
   (`qr/` dans `.gitignore`).
   Étiquette **format paysage** (QR à gauche, panneau à droite) : placeholder
   LOGO (option `--logo`), cercle GOMMETTE, nom du jeu, et CODE DE CLASSEMENT
   type `EAM8-3-5-15` (fonction `code_classement()` : chiffres âge/joueurs/durée
   depuis la base, lettres `XXX` en placeholder tant que la nomenclature n'est
   pas figée). Le numéro de base n'est PAS affiché (présent dans le QR). Planche
   A4 (reportlab, **couleur** pour le logo) à grille **configurable**
   `--grille LxC` (défaut 8x2). Logo réel : `logo_djplm.jpg` à la racine.
5. [fait] Fiche jeu `/jeu/<id>` (lecture publique) + écran prêt/retour
   `/pret/<id>` (écriture). Logique métier isolée dans `app/services.py` (état
   déduit, plus petit n° de pochette libre recyclé sans plafond, prêter / rendre
   / re-prêter, dispo par titre). Contrôle d'état côté serveur → jamais bloquant
   (déjà sorti / déjà dispo = message). Templates Jinja2 (`base/fiche/pret.html`)
   mobile-first + `static/css/style.css`. `main.py` : StaticFiles + redirection
   `/`→`/catalogue`. Auth jeton = placeholder `exiger_jeton` (étape 9). 10 tests
   verts (services + routes via TestClient), flux validé sous uvicorn.
6. [fait] Scanner caméra embarqué : page `/scanner` (`routes/scanner.py`) +
   `static/js/scanner.js`. getUserMedia caméra arrière + décodage **jsQR**
   (compatible iOS/Android ; `BarcodeDetector` absent d'iOS). Extrait l'id de
   l'URL `/jeu/<id>` et redirige vers `/pret/<id>`. Repli si caméra indispo
   (message → appareil photo natif). Lien « Scanner le jeu suivant » sur l'écran
   prêt pour enchaîner. jsQR chargé via CDN — **à héberger en local pour la
   prod**. Test route 200 + contenu.
7. [à faire] Catalogue public (vrac + filtre catégorie).
8. [à faire] Page statistiques (agrégation par titre, jeux à zéro inclus).
9. [à faire] Auth par jeton + limitation de débit.
10. [à faire] Déploiement VPS + HTTPS.

`routes/catalogue.py` : `/jeu/<id>` fait (le `/catalogue` reste à faire, étape 7).
`routes/pret.py` : `/pret/<id>` + actions prêter/rendre/re-prêter faits.

## Sécurité du dépôt

Ne **jamais** committer : le jeton bénévole, `.env`, la base SQLite. Ils sont
exclus par `.gitignore` (vérifié). Utiliser `.env.example` comme modèle.

## Lancer en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # éditer le jeton, le chemin base, le domaine
python -m app.db            # initialise la base SQLite
uvicorn app.main:app --reload
```
