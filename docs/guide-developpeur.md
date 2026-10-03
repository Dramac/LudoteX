# Guide développeur — LudoteX

Point d'entrée pour toute personne (ou IA) qui reprend le code. Il donne la vue
d'ensemble, les conventions, le flux d'une requête et la marche à suivre pour
étendre le projet. La conception de référence reste `docs/specification.md` ;
l'état d'avancement vit dans `CLAUDE.md`.

## 1. Vue d'ensemble

Application web de prêt de jeux de société pour un événement associatif. Les
bénévoles scannent un QR par boîte pour enregistrer prêts et retours ; le public
consulte un catalogue. Anti-vol par **numéro de pochette** (pièce d'identité
déposée), donc **aucune donnée personnelle dans le modèle de données du prêt**.
Les champs de texte libre qui peuvent malgré tout en recevoir une sont
recensés dans `CLAUDE.md` (« Règles métier ») : en ajouter un, c'est ouvrir
une porte.

Stack : **Python + FastAPI**, **SQLite**, templates **Jinja2**, un peu de **JS**
pour le scanner caméra. Servi par **uvicorn**.

## 2. Conventions (respectées partout)

- **Langue : français** pour le code, les variables, fonctions, colonnes,
  commentaires et messages. (Quelques noms d'API FastAPI/SQLite restent en
  anglais : `request`, `router`, `conn`…).
- **Séparation des responsabilités** :
  - `app/models.py` : schéma SQL (aucune logique).
  - `app/db.py` : ouverture de connexion + initialisation.
  - `app/services.py` : **toute** la logique métier et les requêtes SQL.
  - `app/routes/*.py` : HTTP uniquement (lire les paramètres, appeler les
    services, rendre un gabarit). **Pas de SQL ni de logique métier ici.**
  - `app/templates/*.html` : présentation (Jinja2).
  - `app/static/` : CSS et JS.
- **Connexion SQLite** : les services reçoivent `conn` en paramètre (testables) ;
  les routes l'ouvrent via `get_connection()` et la ferment en `try/finally`.
- **Fonctions internes** : préfixe `_` (ex. `_rendu`, `_police`).
- **Docstrings** : chaque module et chaque fonction non triviale en possède une
  (rôle, Args, Returns, cas limites).

## 3. Les deux clés non négociables

- `id_exemplaire` (TEXT) : une boîte physique, encodée dans le QR
  (`/jeu/<id_exemplaire>`). Stockée en TEXT pour préserver les zéros de tête.
- `reference_titre` : regroupe les exemplaires d'un même jeu (stats). Générée
  comme slug normalisé du nom à l'import.

Ne jamais renommer/retyper ces deux colonnes : le reste du schéma peut évoluer.

## 4. Modèle de données (4 tables, voir `app/models.py`)

- `titres` (PK `reference_titre`) : catalogue + colonnes optionnelles nullables.
- `exemplaires` (PK `id_exemplaire`, FK → titres).
- `prets` (historique complet, jamais purgé) : `date_retour IS NULL` ⇒ sorti.
- `pochettes` : occupation du moment des numéros (recyclés, sans plafond).

**État déduit, pas stocké** : un exemplaire est *sorti* s'il a un prêt non clos.

## 5. Flux d'une requête (exemple : prêter un jeu)

1. Le bénévole ouvre `/scanner` (protégé par jeton) → `static/js/scanner.js`
   décode le QR et redirige vers `/pret/<id>`.
2. `routes/pret.py` (`ecran`) appelle `services.info_exemplaire` /
   `pret_en_cours`, puis rend `templates/pret.html`.
3. Le bénévole tape « Prêter » → POST `/pret/<id>/preter`.
4. `routes/pret.py` (`action_preter`) vérifie l'état puis appelle
   `services.preter`, qui attribue le plus petit numéro libre et insère le prêt.
5. La page est re-rendue avec un `resultat` affichant l'emplacement.

`app/main.py` assemble le tout : montage de `/static`, enregistrement des
routeurs, gestionnaire d'erreur 403 (page « accès réservé »).

## 6. Authentification (voir `app/auth.py`)

Pas de comptes : un **jeton** unique (`PRET_TOKEN`) protège `/pret/*` et
`/scanner`. Lien d'activation `/acces?jeton=…` → cookie de
400 jours (`auth.DUREE_COOKIE_JETON`), reposé à chaque requête autorisée ;
l'échéance du jeton, elle, est vérifiée par le serveur à chaque requête et se
prolonge sans changer le jeton (`auth.prolonger_jeton`). Sans jeton
configuré → **mode ouvert** (dev) avec avertissement au démarrage. Le reste
(catalogue, fiches, stats) est public.

## 7. Scripts hors-web (`scripts/`)

- `import_csv.py` : importe le catalogue (tolérant aux colonnes, idempotent).
  `python -m scripts.import_csv <fichier.csv>`.
- `generate_qr.py` : génère les étiquettes QR (PNG + planche PDF).
  `python -m scripts.generate_qr --planche`.
- `reinitialiser_mot_de_passe.py` : remplace le mot de passe admin en base
  (mot de passe oublié ; aussi appelé par `install.sh`). Mot de passe lu sans
  écho, jamais en argument ; refuse une base absente plutôt que d'en créer une
  vide ; `--env` vise une autre instance. Procédure : `docs/deploiement.md` § 9.

## 8. Lancer et tester en local

Pour tester le scanner caméra depuis un smartphone (tunnel HTTPS), voir
`docs/lancement-local.md`. En bref, pour lancer l'application :

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.db
python -m scripts.import_csv <catalogue.csv>
uvicorn app.main:app --reload
python -m pytest -q          # tests
```

Tests : `tests/test_services.py` (logique métier, base en mémoire) et
`tests/test_routes.py` (routes via `TestClient`, base temporaire par test).

## 9. Comment étendre — recettes

- **Ajouter une colonne au catalogue** : 1) l'ajouter (nullable) dans
  `SCHEMA_TITRES` de `models.py` ; 2) la mapper dans `COLONNES` + l'INSERT de
  `import_csv.py` ; 3) prévoir une migration `ALTER TABLE` pour les bases déjà
  créées ; 4) l'afficher dans le gabarit voulu.
- **Ajouter une page** : créer `app/routes/xxx.py` exposant un `router`, l'inclure
  dans `app/main.py`, ajouter un gabarit. Mettre la logique dans `services.py`.
- **Ajouter un filtre catalogue** : étendre `services.lister_catalogue` (clause
  WHERE paramétrée) + le formulaire de `catalogue.html` + la normalisation dans
  `routes/catalogue.py`.
- **Protéger une nouvelle route bénévole** : ajouter
  `_=Depends(exiger_jeton)` à la signature.
- **Paramètre de chemin numérique** : le déclarer avec son convertisseur,
  `/{id_x:int}`, et pas seulement par l'annotation `int`. Sans convertisseur,
  une valeur non entière est routée puis refusée en 422 ; avec, elle ne
  correspond à aucune route et tombe sur la 404 conviviale.
  `tests/test_validation_page.py` parcourt toutes les routes et le vérifie.
- **Module « prêts longue durée »** (comptes, e-mails) : chantier non encore
  spécifié publiquement (cloisonnement + RGPD à traiter comme pour le planning).

## 10. Pièges connus

- `templates.TemplateResponse` : signature **(request, nom, contexte, …)** —
  `request` en premier (version récente de Starlette).
- Planche PDF : passer un **PNG** à reportlab (pas l'objet PIL) pour préserver la
  couleur sans dépendre du codec JPEG.
- QR : l'URL encodée est **définitive** — ne tirer les étiquettes qu'une fois le
  domaine figé.
- Horodatages **UTC** en base ; conversion en heure locale à l'affichage si
  besoin.
- Limiteur de débit **en mémoire** : valable pour un seul worker uvicorn.
- **Une erreur de validation n'est pas une `HTTPException`.** Un paramètre de
  requête ou de formulaire qui ne se convertit pas (`?page=abc`) lève
  `RequestValidationError` ; `app/main.py::gestion_validation` la rend en page
  générique (code 422 conservé), jamais en JSON. Le détail de l'erreur n'est
  ni affiché ni journalisé : il recopie la valeur envoyée.
- **Statistiques : `motif IN MOTIFS_COMPTES`, pas `motif = 'pret'`.** Un prêt
  au public clos sans retour scanné porte le motif `oubli` : il compte dans les
  totaux, pas dans la durée moyenne (`app/services.py`, `MOTIF_OUBLI`). Une
  nouvelle requête de statistiques qui filtrerait `= 'pret'` en perdrait.
- **Un `Depends` ne passe pas avant le corps d'un formulaire.** FastAPI lit
  et analyse un envoi multipart (`UploadFile = File(...)`) **avant** de
  résoudre la moindre dépendance : une garde en `Depends` ou en tête de
  fonction arrive après que le fichier a été reçu et mis sur disque. Une
  route d'administration qui reçoit un fichier se déclare donc par
  `_envoi_admin` (`app/routes/admin.py`), dont la classe de route
  `RouteEnvoiAdmin` garde avant la lecture. Elle lit ensuite le fichier par
  `app.envois.lire_borne`, avec la borne de son usage, jamais par
  `fichier.file.read()` sans borne. `tests/test_envois_bornes.py` en est le
  modèle.
- **Pas de file d'écritures hors ligne** (service worker qui garderait prêts et
  retours pour les rejouer plus tard) : le numéro de pochette est attribué par
  le serveur, deux téléphones déconnectés attribueraient le même. La
  continuité d'une coupure se fait sur papier (wiki, « Si le site ne répond
  plus ») ; quand seule l'application est arrêtée, nginx sert
  `app/static/indisponible.html` (`error_page` des deux fichiers
  `deploy/nginx-ludotex*.conf`). Motif détaillé : `docs/idees-evolutions.md`
  § 2.1.
- **Fichier d'environnement : un seul domicile, `app/environnement.py`.**
  Aucun module n'appelle `load_dotenv()` lui-même
  (`tests/test_environnement.py` le refuse). Sans `LUDOTEX_ENV_FILE`, c'est le
  `.env` à la racine du code, quel que soit le dossier courant ; avec, ce
  fichier-là et lui seul (l'instance de formation). Laissé à python-dotenv, le
  choix dépendait de la façon de lancer Python — dossier du module pour `-m`,
  dossier **courant** pour `-c` — et la formation héritait des clés de la
  production. Un chemin **relatif** dans une valeur reste lu depuis le dossier
  courant.
