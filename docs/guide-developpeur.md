# Guide développeur — LudoteX

Point d'entrée pour toute personne (ou IA) qui reprend le code. Il donne la vue
d'ensemble, les conventions, le flux d'une requête et la marche à suivre pour
étendre le projet. La conception de référence reste `docs/specification.md` ;
l'état d'avancement vit dans `CLAUDE.md` ; les conseils transverses dans
`bonne-pratique.md`.

## 1. Vue d'ensemble

Application web de prêt de jeux de société pour un événement associatif. Les
bénévoles scannent un QR par boîte pour enregistrer prêts et retours ; le public
consulte un catalogue. Anti-vol par **numéro d'emplacement** (pièce d'identité
déposée), donc **zéro donnée personnelle**.

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
`/scanner`. Lien d'activation `/acces?jeton=…` → cookie (3 jours). Sans jeton
configuré → **mode ouvert** (dev) avec avertissement au démarrage. Le reste
(catalogue, fiches, stats) est public.

## 7. Scripts hors-web (`scripts/`)

- `import_csv.py` : importe le catalogue (tolérant aux colonnes, idempotent).
  `python -m scripts.import_csv <fichier.csv>`.
- `generate_qr.py` : génère les étiquettes QR (PNG + planche PDF).
  `python -m scripts.generate_qr --planche`.

## 8. Lancer et tester en local

Voir `bonne-pratique.md` (section « Tester en local ») pour le détail. En bref :

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
- **Module « prêts longue durée »** (comptes, e-mails) : voir la note dédiée
  `docs/evolution-prets-longue-duree.md` (cloisonnement + RGPD).

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
