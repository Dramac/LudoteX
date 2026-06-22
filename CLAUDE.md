# CLAUDE.md — Contexte projet pour l'assistant

Fichier de contexte relu à chaque session de développement. Le tenir à jour
à la fin de chaque étape. La **conception fait foi dans `docs/specification.md`** ;
ce fichier en est un résumé opérationnel, pas une source concurrente.

## État du projet (passage de relais)

**Brique de prêt : COMPLÈTE** — séquence §6 (points 1→10) faite, plus les
évolutions backlog (tournoi côté prêt, durées, jeux sortis, clôture, expiration
du jeton, menus, page d'aide), la gestion d'erreur (page 500 + logs) et les
artefacts de déploiement (`deploy/` + `docs/deploiement.md`). 37 tests verts.
Reste, côté Simon : exécuter le déploiement VPS, et imprimer les QR une fois le
domaine figé.

**Module TOURNOIS — SOCLE de la phase 1 : FAIT.** Sous-paquet `app/tournoi/`
(`models.py`, `db.py`, `services.py`, `routes.py`) + gabarits `tournoi_*.html`,
sur une **base SQLite séparée** `data/tournoi.db` (var. `.env`
`TOURNOI_DATABASE_PATH`, init au démarrage dans `main.py`, **mêmes jeton bénévole
+ mot de passe admin**). Trois tables (`tournois`, `inscriptions`, `rencontres` —
cette dernière créée d'avance pour les modes de scoring). Réalisé : CRUD bénévole
(créer/éditer/supprimer avec **double confirmation**), machine à états
`brouillon↔inscriptions(+termine)`, **inscription publique** (pseudo + **code de
désinscription** affiché à l'écran ; **e-mail jamais stocké**, champ non utilisé
en phase 1 par décision — envoi reporté en phase 2), désinscription par code,
gestion manuelle des participants, liste publique + page de suivi. Liens
`/tournois` ajoutés au menu bénévole et au pied de page. Helpers dates réutilisés
de `app/services.py`. **12 tests dédiés** (`tests/test_tournoi.py`), suite
globale **49 tests verts**.

**Mode de scoring HIGH SCORE : FAIT.** `services.lancer_tournoi(conn, id, mode)`
(transition `inscriptions→lance` + `mode_scoring`, refus si 0 participant /
mauvais état / mode inconnu) + init high score = **une ligne `rencontres` par
participant** (`participant_a`=joueur, `score_a`=points, `ronde` NULL).
`lignes_high_score` (création paresseuse des lignes manquantes, ex. participant
ajouté après lancement), `enregistrer_scores_high_score`, `classement_high_score`
(tri décroissant, **ex æquo en ranking sportif** 1-2-2-4, scores manquants en
fin sans rang). Routes bénévole `POST /tournoi/{id}/lancer` (menu de modes) et
`GET|POST /tournoi/{id}/scores` ; **classement public** sur la page de suivi dès
`lance`/`termine`. Gabarit `tournoi_scores.html`, `tournoi_gerer.html` (lancement
+ lien scores) et `tournoi_detail.html` (classement) mis à jour.
`MODES_SCORING` = {`high_score`} pour l'instant. **Suite globale : 57 tests verts.**

**Prochaines étapes : modes de scoring restants**, sur la même table `rencontres` :
(2) **élimination directe** (arbre, byes si pas une puissance de 2, option BO3),
(3) **ronde suisse simple** (appariement par score, sans rejouer, bye si impair,
nb de rondes fixé). Chaque mode = lancement + génération des appariements +
saisie des résultats par ronde. Puis phase 2 (double élimination, e-mails
robustes, sauvegarde externe). Points CA encore ouverts : voir §11 de
`docs/conception-tournois.md`.

Autres notes de conception : `docs/evolution-prets-longue-duree.md` (comptes /
prêts nominatifs, optionnel) et `docs/ameliorations-a-prevoir.md` (backlog,
points 1→8 déjà réalisés).

**Étude à mener (nouveau chat) : choix de l'hébergement** — cadrée dans
`docs/etude-hebergement-brief.md` (comparatif avec recherche web + reco, à partir
de `docs/budget.md` et spec §10).

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

- `titres` : `reference_titre` (PK), `nom`, `type_jeu` ("Jeu"/"Extension"),
  `categorie` + colonnes optionnelles nullables (`nb_joueurs_min/max`,
  `duree_min`, `age_min`, `editeur`, `auteur`, `annee_edition`, `descriptif`).
  Migration : `db._appliquer_migrations` ajoute les colonnes apparues après coup
  (ex. `type_jeu`) aux bases existantes via ALTER TABLE.
- `exemplaires` : `id_exemplaire` (PK, TEXT), `reference_titre` (FK).
- `prets` : `id_pret` (PK auto), `id_exemplaire` (FK), `numero_pochette`,
  `date_sortie`, `date_retour` (NULL tant que sorti). Historique jamais purgé.
- `pochettes` : `numero_pochette` (PK), `occupe` (0/1). Occupation du moment.
- `parametres` : `cle` (PK), `valeur`. Réglages persistants (ex. `admin_hash`).

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
   « Type »→`type_jeu` (Jeu/Extension), « Type jeu »→`categorie`, parsing
   « Nb joueurs » 2-4→min/max, « Age » 10+→10,
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
   prêt pour enchaîner. jsQR **hébergé en local** (`static/js/jsQR.js`, versionné,
   aucune dépendance CDN). Test route 200 + contenu.
7. [fait] Catalogue public `/catalogue` (`routes/catalogue.py`) : liste des
   titres triée par nom, dispo par titre (X/Y), lien vers la fiche d'un
   exemplaire représentatif (MIN id). Page d'accueil `/`→`/catalogue`. Template
   `catalogue.html`. **Recherche/filtres combinés** dans un panneau dépliable
   `<details>` (sans JS) : champ `q` (nom, LIKE NOCASE), `categorie` (égalité),
   `age` (age_min <= X, « accessible dès cet âge »), `joueurs` (nb_joueurs_min <=
   N <= nb_joueurs_max, nombre exact ; jeux sans bornes exclus si filtre actif).
   Services `lister_catalogue(categorie,q,age,joueurs)`, `lister_categories`,
   `ages_disponibles`, `max_joueurs`. Tests 200 + filtres.
8. [fait] Page statistiques `/stats` (`routes/stats.py`) : total des prêts +
   en cours + titres prêtés, palmarès des plus/moins prêtés par titre (zéros
   inclus via LEFT JOIN « catalogue d'abord »), histogramme prêts par heure
   (barres CSS, heures UTC). Double vue `?tri=total|exemplaire`. Services
   `stats_globales`, `palmares`, `prets_par_heure`. Lien dans le pied de page.
   **Filtre par période** `debut`/`fin` (saisies heure locale FR → UTC via
   `local_vers_utc_iso`, fuseau Europe/Paris) appliqué à tout + **liste
   détaillée** des prêts (`lister_prets_periode`). **Exports** Excel (openpyxl)
   et PDF (reportlab) via `app/exports.py` + `services.collecter_stats`, routes
   `/stats/export.xlsx|pdf` (filtres respectés). Alias `/stat`,`/statistique`,
   `/statistiques`→`/stats`. Logo de l'asso (`app/static/img/logo_djplm.jpg`,
   aussi `LOGO_DEFAUT` des étiquettes) affiché en tête du catalogue.
   Tests services + route + exports.
9. [fait] Auth bénévole par jeton + limitation de débit (`app/auth.py`,
   `routes/acces.py`). `/pret/*` et `/scanner` exigent un cookie = `PRET_TOKEN`
   (comparé en temps constant). Lien d'activation `/acces?jeton=…` pose le cookie
   (HttpOnly, SameSite=Lax, Secure si HTTPS, validité 3 jours) puis redirige vers /scanner.
   Limitation de débit par IP sur `/acces` (`RATE_LIMIT_PER_MINUTE`, en mémoire).
   Catalogue/fiches/stats restent publics. Si `PRET_TOKEN` non défini → mode
   ouvert + avertissement au démarrage (À DÉFINIR en prod). Page `acces_refuse`
   via gestionnaire 403. Rotation annuelle = changer `PRET_TOKEN`. Tests verts.
10. [artefacts prêts] Déploiement VPS + HTTPS. Fichiers dans `deploy/`
    (`pret-jeux.service` systemd 1 worker + `--proxy-headers`,
    `nginx-pret-jeux.conf` reverse proxy + static, `sauvegarde.sh` SQLite `.backup`
    + rotation + rclone optionnel) et guide pas à pas `docs/deploiement.md`
    (VPS, venv, `.env`, base + import, systemd, nginx, certbot Let's Encrypt,
    QR définitifs une fois le domaine figé, sauvegarde cron, mises à jour).
    Reste à exécuter sur le VPS par Simon quand l'hébergeur/domaine seront choisis.

`routes/catalogue.py` : `/jeu/<id>` + `/catalogue` faits.
`routes/pret.py` : `/pret/<id>` + actions prêter/rendre/re-prêter faits
(protégés par `exiger_jeton`). `routes/scanner.py`, `routes/stats.py`,
`routes/acces.py` faits.

## Espace d'administration (hors séquence initiale)

Écran `/admin` protégé par **mot de passe** (≠ jeton bénévole) : `app/admin_auth.py`
(hachage pbkdf2 stdlib, hash en table `parametres`, amorçage via `ADMIN_PASSWORD`
du `.env`, sessions en mémoire + cookie), `routes/admin.py`, templates `admin_*`.
Permet : créer une fiche de jeu (id_exemplaire AUTO, préfixe `A` via
`services.prochain_id_exemplaire`, voir `creer_jeu`/`ajouter_exemplaire`),
consulter une fiche et **(ré)imprimer l'étiquette** de chaque exemplaire
(`GET /admin/etiquette/<id>.png`), changer le mot de passe. Le **dessin
d'étiquette est mutualisé** dans `app/etiquettes.py` (partagé avec
`scripts/generate_qr.py`). Accès non authentifié → redirection vers /admin (pas
de 403). Le **tableau de bord** propose un menu vers les modules (catalogue,
stats, scanner) en plus des actions d'admin.

**Jeton bénévole en base** : `auth.jeton_actuel(conn)` lit d'abord `parametres`
(clé `pret_token`) puis l'env `PRET_TOKEN` (amorçage). Page `/admin/jeton` :
affiche le lien d'activation, permet de **réinitialiser** le jeton
(`auth.reinitialiser_jeton`, invalide les anciens cookies) et de le **partager**
(WhatsApp/e-mail/SMS + copier). `acces_valide` ouvre une connexion pour lire le
jeton courant.

**Export PDF à la carte** : `exports.construire_pdf(data, periode, sections)`
avec sections cochables (synthèse, plus, moins, detail — détail décoché par
défaut) ; route `/stats/export.pdf?sections=…`. L'export Excel reste complet.
Tests verts.

## Évolutions du backlog (points 1–8, juin 2026)

- **Sortie « tournoi »** : colonne `prets.motif` ('pret'/'tournoi', migration auto).
  `services.sortir_tournoi` (numero_pochette=0, sans emplacement), bouton « Sortir
  pour un tournoi » sur `/pret/<id>`. **Exclu de toutes les stats** (filtre
  `motif='pret'` dans stats_globales/palmares/prets_par_heure/lister_prets_periode).
- **Durées** : `services.format_duree`, durée par prêt (`duree_txt`, « depuis … »
  si en cours) dans la liste détaillée, **durée moyenne** (`stats_globales`,
  prêts terminés via `julianday`). Affichées page stats + exports Excel/PDF.
- **Vue « Jeux actuellement sortis »** (`/stats`, ancre `#sortis`) :
  `services.lister_prets_en_cours` → 2 blocs (prêtés au public / en tournoi).
- **Clôture de fin d'événement** : `services.cloturer_tous_les_prets` (clôt tout
  prêt non clos + libère les pochettes, **garde l'historique**), bouton admin
  `/admin/cloturer-prets` (section « Fin d'événement », confirmation).
- **Validité du jeton** : `parametres.pret_token_expire` (UTC). `auth.jeton_expire`
  (expiré = accès FERMÉ ≠ absent = ouvert), `reinitialiser_jeton(conn, expire_iso)`
  défaut **1 semaine** ; cookie d'`/acces` aligné sur l'expiration. Champ
  « valable jusqu'au » sur `/admin/jeton`.
- **Menu bénévole** : fragment `templates/_menu_benevole.html` (Catalogue,
  Scanner, Statistiques, Jeux sortis, Aide), affiché dans le bandeau **uniquement
  si `est_benevole(request)`** (global Jinja = `auth.acces_valide`), et réutilisé
  dans le dashboard admin (point unique de maintenance). Page **`/aide`** (mode
  d'emploi bénévole).

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
