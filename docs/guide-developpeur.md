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
  comme slug normalisé du nom à la création ou au premier import, puis
  **figée** : un jeu renommé à l'écran la garde (voir § 10, ré-import).

Ne jamais renommer/retyper ces deux colonnes : le reste du schéma peut évoluer.

## 4. Modèle de données (4 tables, voir `app/models.py`)

- `titres` (PK `reference_titre`) : catalogue + colonnes optionnelles nullables.
- `exemplaires` (PK `id_exemplaire`, FK → titres).
- `prets` (historique complet, jamais purgé) : `date_retour IS NULL` ⇒ sorti.
- `pochettes` : occupation du moment des numéros (recyclés, sans plafond).
- Tables annexes de la même base, dont `fiches_bgg` : copie datée des fiches
  BoardGameGeek, clé `bgg_id` (jamais `reference_titre`), hors export CSV ;
  elle garde l'adresse de la vignette de la boîte, jamais l'image.

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
  `_=Depends(exiger_jeton)` à la signature. Une route d'administration
  commence par `if (garde := _garde(request)): return garde`.
  `tests/test_gardes_routes.py` parcourt toutes les routes et exige une garde
  sur toute méthode d'écriture et sous `/admin`, `/pret`, `/scanner`,
  `/maintenance`, `/planning/admin`. `garde_module` n'en est **pas** une (il
  règle la visibilité). Une route qui doit rester ouverte s'ajoute à
  `EXCEPTIONS`, **avec sa raison**.
- **Écrire une cellule de tableur** : passer par `exports.ecrire_cellule`
  (Excel) ou `exports.neutraliser_csv` (CSV), jamais `ws.cell(value=…)` sur
  une donnée saisie : une valeur commençant par `=`, `+`, `-`, `@`, une
  tabulation ou un retour chariot deviendrait une formule (SEC-06).
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
- **Code de classement : un seul domicile, `app/classement.py`.** Listes de
  lettres, validation, lecture de la colonne CSV et les deux formats y vivent ;
  `app/etiquettes.py` ne fait que dessiner. Les **trois producteurs**
  d'étiquettes (route PNG, planche PDF, `scripts/generate_qr.py`) lisent
  chacun les trois lettres par **leur propre requête SQL** : une colonne
  ajoutée au code de classement doit l'être aux trois, sans quoi un producteur
  imprime sans elle, en silence (`tests/test_classement.py` les espionne).
  Les listes se remplissent dans les tests par `monkeypatch.setattr` — d'où
  `classement._listes()`, qui les relit à chaque appel.
- **Un jeu renommé à l'écran garde sa référence ; l'import doit le
  retrouver.** `reference_titre` est le slug du nom *d'origine*. Si l'import
  recalculait le slug du nom du fichier, « Catane » corrigé en « Catan »,
  exporté puis ré-importé, créerait un titre `CATAN`, y déplacerait les
  boîtes et laisserait `CATANE` vide, avec sa fiche BoardGameGeek, ses lettres
  et sa correction. D'où `scripts/import_csv.py::resoudre_reference`, qui lit
  l'état de la base **avant** d'écrire (simulation comprise) : une boîte déjà
  connue dont le nom a le même slug que le nom actuel de son titre y reste ;
  un nouveau nom qui ne désigne qu'un titre existant le rejoint ; sinon, le
  slug comme avant. La propriété « exporter puis ré-importer ne change rien »
  est verrouillée par `tests/test_fiche_jeu.py` ; toute colonne ajoutée à
  `titres` doit la tenir (une valeur que l'export écrit, l'import doit la
  relire à l'identique — d'où les bornes de `services.BORNES_FICHE`).
- **Colonnes de `titres` que l'import n'écrase pas** : les trois lettres,
  `bgg_id` (`COALESCE`) et la marque `bgg_absent`, parce qu'elles ne viennent
  pas du tableur du bureau. La marque voyage dans la colonne « Fiche
  BoardGameGeek » sous la valeur réservée « pas sur BGG » (`bgg.PAS_SUR_BGG`) ;
  un identifiant, du fichier ou de la base, la retire.
  Toutes les autres sont écrasées, case vide comprise ; celles que l'écran
  corrige sont listées par `services.CHAMPS_FICHE`, et un import qui en défait
  une le dit (`import_csv.corrections_defaites`, colonne `modifie_le`).
- **Un seul module parle au réseau : `app/bgg_api.py`** (client de l'API de
  BoardGameGeek). `app/bgg.py`, lui, ne fait que lire un identifiant : un test
  par AST verrouille l'un et l'autre. Trois règles tiennent le reste :
  **jamais d'appel à l'affichage** (la route lit la copie `fiches_bgg` ; seul
  un bouton d'administration appelle BGG), **jamais d'appel sous
  transaction** (la base est écrite après, en une transaction courte), et
  **le jeton lu au seul `bgg_api._jeton`**, à chaque appel, envoyé en en-tête
  et nulle part ailleurs — les erreurs de `urllib` sont remplacées par
  `EchecBGG` sans chaînage, pour qu'aucune trace ne transporte la requête.
  Un lot (`recuperer_lot`) ne lance un appel que s'il peut finir avant
  `BORNE_LOT_S` (40 s), sous les 60 s du `proxy_read_timeout` par défaut de
  nginx : en changeant le délai ou la pause, refaire ce calcul.
- **La cadence et le moteur de recherche BGG** (série « fiche », lot 3). Tous
  les appels du processus passent par `bgg_api._appeler`, qui réserve un
  créneau dans `bgg_api.CADENCE` : deux appels quelconques sont espacés d'au
  moins `INTERVALLE_APPELS_S` (3 s), qu'ils viennent du même clic, de deux
  clics ou de deux personnes. Le verrou est en mémoire : il suppose l'unique
  worker de `deploy/ludotex.service` (comme le limiteur de débit). Il ne
  couvre que la réservation, jamais l'appel réseau. Un lot de la liste des
  jeux garde sa pause de 5 s ; une recherche simultanée peut retarder d'un
  créneau (3 s) le dernier appel qu'il lance, soit 43 s au pire au lieu de
  40 — encore sous les 60 s de nginx (chaque contrôle de la borne compte le
  temps réellement écoulé, attentes comprises). Le moteur (`app/bgg_recherche.py`,
  qui n'importe aucune bibliothèque réseau : il appelle `bgg_api` par
  défaut, et les tests lui passent un double) essaie les variantes du nom et
  s'arrête à la première égalité, garde cinq candidats, lit leurs fiches en un
  appel `thing` avec `stats=1` (la popularité départage des homonymes), puis
  les classe. Un clic est borné à `BORNE_CLIC_S` (20 s) par une échéance
  passée à chaque appel : la cadence refuse un créneau trop tardif
  (`EchecBGG("trop_long")`) et le délai d'un appel est raccourci pour finir
  avant elle ; les variantes laissent toujours le temps de lire les fiches.
  **Le texte cherché est un champ libre** : il part en POST (nginx journalise
  la ligne de requête, pas le corps), n'entre dans l'adresse de BGG qu'encodé,
  et la redirection qui suit ne porte que les identifiants des candidats —
  la page ne lit que leur copie en base, la recharger ne relance rien. La
  fixture autouse `_cadence_bgg_simulee` de `tests/conftest.py` pose une
  horloge simulée : aucun test n'attend les 3 s pour de vrai.
- **Vignettes BGG : une liste d'hôtes, deux copies** (série « fiche », lot
  4). L'adresse de l'image d'une boîte (`<thumbnail>`) est gardée dans
  `fiches_bgg.vignette` après contrôle par `bgg.lire_vignette` (`https:`,
  hôte exactement dans `bgg.HOTES_VIGNETTE`, longueur bornée), et
  recontrôlée à la lecture (`services.lire_fiche_bgg`). Le navigateur la
  charge chez BGG : aucune image n'est téléchargée ni stockée. Pour qu'elle
  s'affiche en production, la CSP doit autoriser ces hôtes dans `img-src` :
  c'est la `location /admin/` de `deploy/nginx-ludotex.conf` qui le fait,
  **et elle seule** (pages publiques et tableau de bord gardent la CSP stricte
  du niveau `server` ; la formation n'a ni clé BGG ni copie). La liste vit
  donc à deux endroits qui ne partagent aucun fichier ;
  `tests/test_nginx_configuration.py` vérifie qu'ils disent la même chose.
  Ajouter un hôte = les deux fichiers, plus le geste nginx sur le serveur.
  La colonne a trois états (NULL jamais lue, '' lue sans image, adresse) :
  `services.ids_bgg_a_recuperer` redemande les seules copies jamais lues.
- **`add_header` de nginx n'est pas cumulatif.** Une `location` qui déclare
  un seul `add_header` perd **tous** ceux du niveau `server` : HSTS,
  X-Frame-Options, nosniff, Referrer-Policy et CSP. C'est pourquoi
  `/static/` et les pages d'erreur n'en déclarent aucun, et pourquoi la
  `location /admin/` de la production, qui élargit `img-src`, **recopie les
  cinq**. Le test cité au point précédent refuse toute `location` qui pose
  des en-têtes sans les recopier tous, à la seule différence d'`img-src` :
  modifier un en-tête du niveau `server` sans le reporter dans `/admin/` le
  fait échouer.
- **Le `.env` local atteint les tests.** `app/environnement.py` charge le
  `.env` de la racine dès l'import : un `BGG_TOKEN` posé là pour un essai
  réel rendait les boutons BGG actifs dans les tests, et un test a appelé BGG
  pour de vrai pendant l'écriture du lot fiche-2. D'où la fixture autouse
  `_aucun_reseau` de `tests/conftest.py` : elle retire `BGG_TOKEN` et fait
  échouer toute connexion vers une autre machine que la boucle locale. Un
  test qui veut un jeton en pose un, factice (`tests/test_bgg_donnees.py`).
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
- **Le multipart n'atteint que les routes d'envoi.** Le middleware ASGI
  `app.envois.MultipartHorsEnvois` refuse en `415`, sans lire le corps, tout
  envoi `multipart/*` adressé ailleurs qu'à un chemin de
  `admin.CHEMINS_ENVOI` — liste que `_envoi_admin` remplit à la déclaration.
  Sans lui, toute route à `Form(...)`, `/admin/login` compris, analysait un
  fichier qu'on lui postait. Un nouveau formulaire `enctype="multipart/form-data"`
  vise donc forcément une route déclarée par `_envoi_admin`, sans quoi il
  est refusé dès le premier essai ; un test relie gabarits et liste blanche.
  Au-delà de `client_max_body_size` (20 Mo), c'est nginx qui refuse, avec la
  page statique `app/static/envoi-trop-volumineux.html`.
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
