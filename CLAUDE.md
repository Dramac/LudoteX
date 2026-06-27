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

**Mode de scoring RONDE SUISSE : FAIT.** Colonne `tournois.nb_rondes` (schéma +
migration `app/tournoi/db.py`). `lancer_tournoi(conn, id, mode, nb_rondes)` gère
le suisse (refus si < 2 participants → `pas_assez`, ou `nb_rondes` manquant) et
génère la ronde 1. Barème `POINTS_VICTOIRE=1 / NUL=0,5 / BYE=1`. Algorithme
(`services`) : `points_suisse`, `_adversaires_passes`, `_ont_eu_un_bye`,
`_apparier` (glouton, tri par points décroissants, **évite les revanches** avec
repli si inévitable), `_generer_ronde_suisse` (**bye au moins bien classé sans
bye antérieur**, victoire auto), `ronde_courante`/`ronde_complete`,
`enregistrer_resultats_suisse` (résultat ∈ {a,b,nul} ; byes non modifiables),
`generer_ronde_suivante` (refus si ronde incomplète/terminée),
`classement_suisse` (ranking sportif). Routes bénévole : lancement avec
`nb_rondes`, `GET /tournoi/{id}/rondes` (écran rondes), `POST .../rondes/{r}/resultats`,
`POST .../rondes/suivante`. Gabarit `tournoi_rondes.html` ; `tournoi_detail.html`
affiche classement + rondes en lecture publique ; `tournoi_gerer.html` adapté.
**Suite globale : 64 tests verts** (cas limites vérifiés : revanche forcée si
rondes > round-robin, rotation des byes).

**Mode de scoring ÉLIMINATION DIRECTE : FAIT.** Arbre à élimination simple dans
`rencontres` (`ronde`=n° de tour, B NULL=bye victoire auto, resultat 'a'/'b').
`lancer_tournoi(..., "elimination")` (refus si < 2 → `pas_assez`) : seeding par
ordre d'inscription vers la **puissance de 2 supérieure**, byes aux mieux classés
et **répartis** via `_ordre_places` (seeding standard 1-8-4-5-2-7-3-6…),
`nb_rondes` = nombre de tours déduit (`_nb_tours_elimination`). Fonctions :
`_generer_premier_tour_elimination`, `_gagnants_du_tour`, `generer_tour_suivant`
(apparie les vainqueurs ; refus si incomplet/terminé), `vainqueur`, `nom_tour`
(Finale/Demi-finales/Quarts…), `arbre`. Routes bénévole : lancement,
`GET /tournoi/{id}/arbre`, `POST .../arbre/{tour}/resultats` (vainqueur a/b, pas
de nul), `POST .../arbre/suivant`. Gabarit `tournoi_arbre.html` ; page publique
affiche l'arbre + le vainqueur. **Suite globale : 71 tests verts.**

**Option BO3 (best of 3) : FONCTIONNELLE.** Choisie **au lancement** (plus à la
création : `bo3` retiré du formulaire), via `lancer_tournoi(..., bo3=True)` —
n'a d'effet que pour suisse/élimination. Quand activée, la saisie d'une rencontre
se fait en **manches gagnées** A–B (`score_a`/`score_b`) et le vainqueur est
déduit (`_resultat_depuis_manches` : égalité = `nul` en suisse, pas de vainqueur
en élimination ; `enregistrer_manches`). Les écrans rondes/arbre affichent deux
champs `ma_<id>`/`mb_<id>` au lieu du sélecteur ; le score « 2–1 » apparaît dans
le suivi public. Sans BO3, saisie « vainqueur » inchangée. **77 tests verts.**

**Planning public (vue 2 jours) : FAIT.** Sur la page d'accueil (`/`,
`accueil.html`). Date de l'événement réglée en admin (`GET|POST /admin/evenement`,
clé `parametres.evenement_date` dans la base de PRÊT ; helpers
`services.lire_parametre`/`ecrire_parametre`). La frise couvre ce jour + le
lendemain. `tournoi.services.planning(conn, jours)` : tournois non-brouillon
groupés par jour local, **couloirs calculés par chevauchement**
(`_calculer_couloirs`, partition d'intervalles), coordonnées de grille (slots de
`SLOT_MIN`=30 min, durée par défaut `DUREE_DEFAUT_MIN`=60), étiquettes d'heures,
`label_jour` (FR). Rendu **hybride sans JS** : CSS grid (gouttière d'heures +
couloirs en colonnes) sur grand écran, agenda empilé chronologique sous 640 px
(styles `.planning-*`). La section n'apparaît que si la fenêtre contient des
tournois. **84 tests verts.**

**« Ajouter à mon agenda » (.ics) : FAIT.** `services.ical_tournoi(conn, id)`
génère un VEVENT iCalendar (début UTC, fin = durée ou défaut 60, titre, jeu en
description, lieu ; échappement RFC 5545 ; None sans date). Route publique
`GET /tournoi/{id}/agenda.ics` (`text/calendar`, attachment ; 404 sans date).
Bouton « 📅 Ajouter à mon agenda » sur la confirmation d'inscription et la page
du tournoi (si date). Aucune donnée perso. **89 tests verts.**

**Champ « âge » (info, texte libre) : FAIT.** Colonne `tournois.age` TEXT (schéma
+ migration `app/tournoi/db.py`), propagée à `creer_tournoi`/`modifier_tournoi`/
`dupliquer_tournoi`, au formulaire création/édition et à l'affichage (page
publique, gestion, duplication). Indication libre type « 10+ », « tout public ».

**Ouverture groupée du jour : FAIT.** `services.ouvrir_tournois_du_jour(conn,
jour)` passe en 'inscriptions' tous les tournois EN BROUILLON datés ce jour-là
(heure locale ; ignore autres jours / sans date / déjà ouverts). Route bénévole
`POST /tournoi/ouvrir-aujourdhui` + bouton « Ouvrir tous les tournois du jour »
(avec confirmation JS) sur `/tournois`, message de retour. Gain de temps le jour
de l'événement.

**Dupliquer un tournoi (programmer à plusieurs horaires) : FAIT.**
`services.dupliquer_tournoi(conn, id, date_heure)` recopie nom/jeu/durée/places/
emplacement/inscription_en_ligne dans une **copie indépendante** repartant en
brouillon (sans inscrit, sans mode/BO3/rondes ; seul l'horaire change). Routes
bénévole `GET|POST /tournoi/{id}/dupliquer` (formulaire minimal : nouvel
horaire), gabarit `tournoi_dupliquer.html`, lien « Dupliquer à un autre horaire »
sur l'écran de gestion. Pour plusieurs créneaux : dupliquer autant de fois.
**92 tests verts.**

**Aide dédiée** : page publique `GET /tournoi/aide` (`tournoi_aide.html`, mode
d'emploi : cycle d'un tournoi, inscription/RGPD, les 3 modes + saisie, suppression),
liée depuis `/tournois` (bénévole) et l'écran de gestion.

**Page d'accueil publique : FAIT.** `GET /` ne redirige plus vers `/catalogue`
mais sert `accueil.html` (route dans `routes/catalogue.py`) : liens vers les
outils publics (catalogue, tournois), rappel du **nombre de jeux disponibles au
prêt** (`services.compter_exemplaires_disponibles` → total/dispo, tous motifs) et
**tournois imminents** (`tournoi.services.tournois_imminents`, fenêtre 1 h :
publiés, non-brouillon, début entre maintenant et +60 min). Le titre du bandeau
pointe désormais vers `/`. **Suite globale : 79 tests verts.**

**Tableau de bord temps réel `/live` (écran salle 16:9) : FAIT.** Page PUBLIQUE
en lecture seule (aucune action, aucun jeton) destinée à un projecteur/TV.
`routes/live.py` : `GET /live` (gabarit `live.html`, plein cadre, grandes
polices, fort contraste, autonome — CSS/JS inline, aucune dépendance externe) +
`GET /live/data` (JSON). La page s'auto-rafraîchit par **polling AJAX** toutes
les 10 s (pas de rechargement). Affiche : jeux sortis / disponibles / total,
nombre de tournois en cours, tournois en cours (`lance`), prochains tournois sur
2 h, et le flux des 10 derniers prêts/retours. **SÉCURITÉ : aucune mention du
numéro de pochette** (rattaché à une pièce d'identité) sur cet écran public — ni
carte « pochettes », ni numéro dans le flux. Nouveau service (réutilisé, pas de
logique dupliquée) : `services.derniers_mouvements` (fusion sorties/retours triée
par instant, sans n° de pochette). Lien « Écran salle » au pied de page.
**2 tests** (route 200 + endpoint données, dont l'absence de pochette).
**Suite globale : 99 tests verts.**

**PHASE 1 COMPLÈTE** (tournois + inscription + suivi + high score + ronde suisse
+ élimination directe). Reste la **phase 2** : double élimination (looser
bracket), affinements BO3 (manches), e-mails robustes (envoi du code), sauvegarde
externe automatisée. Points CA encore ouverts : voir §11 de
`docs/conception-tournois.md`.

**Module PLANNING BÉNÉVOLE — SOCLE (data + logique) : FAIT.** Cadré dans
`docs/conception-planning.md` (remplace le tableur Excel du bureau ; collecte des
souhaits → préremplissage dégrossi → validation admin → publication). Sous-paquet
`app/planning/` (`models.py`, `db.py`, `services.py`) sur une **base SQLite
séparée** `data/planning.db` (var. `.env` `PLANNING_DATABASE_PATH`). **8 tables**
(`evenements`, `postes`, `creneaux`, `besoins`, `benevoles`, `disponibilites`,
`preferences`, `affectations`). RGPD : **rupture assumée** avec le « zéro donnée
perso » du prêt (noms + contact + dispos stockés), base séparée à finalité unique,
`purger_evenement`. Réalisé : machine à états `collecte→brouillon→publie` ; trame
admin (postes, créneaux poste/tâche, besoins par case, `dupliquer_trame`) ;
collecte (`enregistrer_souhaits` : dispos + préférences **prefere/ok/si_vraiment/
surtout_pas** + plafond d'heures `max_heures`, édition par `code_modif`) ;
**préremplissage GLOUTON dégrossi** (`prefiller`) respectant les contraintes
DURES (disponibilité, surtout_pas, plafond d'heures, pas deux postes en même
temps) et **laissant les trous** ; grille (`construire_grille`), couverture
(`analyser_couverture`), « mon planning » (`planning_du_benevole`), verrouillage
de cases. Créneaux stockés en UTC ISO (durée déduite), helpers de fuseau
réutilisés.

**Module PLANNING BÉNÉVOLE — ROUTES & ÉCRANS : FAIT.** `app/planning/routes.py`
branché dans `app/main.py` (init au démarrage + routeur). PUBLIC : `/planning`
(grille publiée en lecture seule + lien collecte si une édition est en collecte),
`/planning/collecte/{ev}` (formulaire de souhaits : dispos cochées par jour,
préférences 4 niveaux en radios, plafond d'heures ; ré-édition par `?code=`),
`/planning/collecte/{ev}/merci` (affiche le code), `/planning/mon?code=` (mon
planning). ADMIN (mot de passe, garde `_garde` comme `routes/admin.py`) :
`/planning/admin` (liste + création + **bouton démo**), `/planning/admin/{ev}`
(écran tout-en-un : trame postes/créneaux, matrice de besoins, préremplissage,
**grille éditable** avec ajout/retrait/verrou par case, transitions d'état,
purge) + exports `export.xlsx`/`export.pdf`. Exports dans `app/planning/exports.py`
(Excel une feuille/jour façon tableur ; PDF A4 paysage imprimable), filtre Jinja
`heure_local` ajouté. CSS préfixé **`pl-*`** (évite la collision avec la frise
`planning-*` des tournois). Démo `app/planning/demo.py` (reproduit le tableur du
bureau : 6 postes, créneaux samedi/dimanche, tâches, ~28 bénévoles fictifs,
préremplissage laissant 3 trous réalistes, publication + jumeau resté en
collecte) ; lançable aussi via `python -m app.planning.demo`. Liens « Planning »
au menu bénévole, au pied de page et au dashboard admin. **17 tests planning**
(13 services + 4 routes via TestClient, dont démo + exports), fixtures de
`test_routes.py`/`test_tournoi.py` étendues à `PLANNING_DATABASE_PATH`.

**Planning — aide + largeur d'écran : FAIT.** Page publique `GET /planning/aide`
(`planning_aide.html` : cycle collecte→brouillon→publié, déclaration des
souhaits, sens des 4 niveaux de préférence, fonctionnement du préremplissage
dégrossi, note RGPD), liée depuis `/planning`, `/planning/admin` et l'écran de
gestion. Correctif d'affichage : `.contenu` est limité à 540 px (mobile-first),
ce qui bridait la grille sur ordinateur (scroll horizontal) ; ajout d'un bloc
Jinja `conteneur_extra` dans `base.html` + classe `.contenu-large`
(`max-width: min(1180px, 96vw)`) appliquée aux pages grille (`planning_public`,
`planning_gerer`). **Généralisé** ensuite aux autres écrans à tableaux larges
(même cause, le 540px global) : `stats`, `tournoi_arbre`, `tournoi_rondes`,
`tournoi_scores`, `tournoi_detail`. Les pages de lecture/formulaires (catalogue,
fiche, prêt, formulaires admin, collecte) restent **volontairement étroites**
(mobile-first, confort de lecture). Suite globale **117 tests verts.**

**Planning — PHASE 2 (qualité du préremplissage) : continuité + équité FAIT.**
`prefiller` enrichi (mêmes contraintes dures) : à préférence égale, l'arbitrage
se fait sur une **charge effective** = heures déjà affectées (équité, le moins
chargé d'abord) MOINS un **rabais de continuité** `CONTINUITE_BONUS_H` (=2 h)
accordé au bénévole déjà sur le **même poste à un créneau CONTIGU** (fin d'un =
début de l'autre, comparaison ISO UTC ; carte `adjacents`). La continuité
l'emporte à charge comparable, l'équité reprend le dessus si un autre est
nettement moins chargé (écart > rabais). Suivi par `place_sur`
(bénévole → {(créneau, poste)}). **3 tests dédiés** (continuité sur créneaux
contigus, équité si non contigus, équité prime si écart > rabais). Démo : 96/99
couvert, heures réparties 4–12 h. Suite globale **120 tests verts.** RESTE
phase 2 : prise en compte de l'expérience (postes « expérience » ; nécessite de
collecter qui est expérimenté), équité encore plus fine, notifications/PDF
individuel.

**Planning — PHASE 2 (pilotage + grille d'ajustement SANS JS) : FAIT.**
Déroulé cible gravé dans `docs/conception-planning.md` (compte admin = bureau ;
pas de date limite : boutons explicites). Écran de gestion : **« Fermer le
questionnaire »** (collecte→brouillon, avec confirmation) puis **« Générer le
planning »** (préremplissage). **Édition « au clic » rendue côté serveur**
(POST classiques, aucun JS lourd) : chaque case de la grille est un lien vers
`GET /planning/admin/{ev}/case/{cr}/{poste}` (`planning_case.html`) qui permet
de **remplacer** un bénévole (`remplacer_affectation` : même case, verrou
conservé), ajouter, retirer, verrouiller — toutes ces actions acceptent un champ
`retour` pour revenir à la page de la case (`_retour` redirige si l'URL commence
par `/planning/`). L'horaire de chaque ligne est un lien vers
`GET|POST /planning/admin/{ev}/creneau/{cr}/editer` (`planning_creneau.html`,
`modifier_creneau` : jour/début/fin → la durée en découle ; bornes invalidées
refusées). **Couleurs par ÉTAT de case** (CSS pur `pl-etat-*`) : grisé / trou
(rouge) / partiel (orange) / complet (vert) / surcharge (bleu). Filtre Jinja
`dt_input` (UTC→`datetime-local`). Drag'n'drop renvoyé à plus tard (assumé).
**Expérience abandonnée** : case retirée de l'UI, colonne `demande_experience`
conservée au schéma mais non exposée/exploitée. Helpers ajoutés : `get_creneau`,
`get_affectation`, `affectations_de_case`. **7 nouveaux tests** (remplacement,
durée, pages d'édition, redirection `retour`). Suite globale **124 tests verts.**

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
