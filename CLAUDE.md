# CLAUDE.md — Contexte projet pour l'assistant

> **Le projet s'appelle désormais LudoteX** (anciennement `pret-jeux`).
> Dépôt GitHub : `https://github.com/Dramac/LudoteX`
> Ce nom évoque « ludique », « technique » et fait écho à LaTeX.
> L'utiliser dans tous les messages, commentaires, titres et documents.

Fichier de contexte relu à chaque session de développement. Le tenir à jour
à la fin de chaque étape. La **conception fait foi dans `docs/specification.md`** ;
ce fichier en est un résumé opérationnel, pas une source concurrente.

## État du projet (passage de relais)

**Brique de prêt : COMPLÈTE** — séquence §6 (points 1→10) faite, plus les
évolutions backlog (tournoi côté prêt, durées, jeux sortis, clôture, expiration
du jeton, menus, page d'aide), la gestion d'erreur (page 500 + logs) et les
artefacts de déploiement (`deploy/` + `docs/deploiement.md`). 37 tests verts.
Reste, côté Simon : exécuter le déploiement VPS (**script d'installation prêt,
voir plus bas**), et imprimer les QR une fois le domaine figé.

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

**Mode de scoring ROUND ROBIN (championnat) : FAIT.** `"round_robin"` ajouté à
`MODES_SCORING`. `lancer_tournoi(..., "round_robin")` (refus si < 3 →
`pas_assez`, BO3 compatible) génère **toutes les rondes d'emblée** via la
**méthode des cercles** (`_generer_round_robin` : un joueur fixe, les autres
tournent ; joueur « fantôme » None si impair → repos parfaitement équilibrés, un
par joueur). `nb_rondes` = n-1 (pair) ou n (impair). Barème/points identiques au
suisse (réutilise `points_suisse`) ; `classement_round_robin` délègue à
`classement_suisse`. **Saisie via l'écran des rondes existant** (routes
`/tournoi/{id}/rondes*` étendues à `round_robin` ; pas de « ronde suivante »
puisque tout est généré au lancement). Affichage public : classement + **tableau
croisé des confrontations** (`table_confrontations` : V/N/D ou score BO3,
ordonné par classement ; gabarit `tournoi_detail.html`, styles `.rr-*`).
**Suite globale : 156 tests verts.**

**Tournois PAR ÉQUIPES : FAIT.** Colonnes `tournois.par_equipes` (0/1) +
`tournois.taille_equipe`, et `inscriptions.membres` (liste JSON) — schéma +
migrations `app/tournoi/db.py`. Principe : **une équipe = un participant** (le
`pseudo` porte le nom d'équipe), donc appariements et modes de scoring INCHANGÉS
(compatible avec les 4 modes). `creer_tournoi`/`modifier_tournoi`/
`dupliquer_tournoi` gèrent les 2 champs ; `inscrire(conn,id,pseudo,membres)`
valide EXACTEMENT `taille_equipe` membres non vides (→ `equipe_incomplete`),
`ajouter_participant` (bénévole) reste permissif. `_nettoyer_membres`,
`parse_membres`, `lister_inscriptions` ajoute `membres_liste`. Routes :
inscription/ajout passent en `async` et collectent `membre_<n>`
(`_membres_du_formulaire`) ; création/édition lisent `par_equipes`/`taille_equipe`.
Gabarits : `tournoi_form.html` (case + taille), `tournoi_inscription.html` (nom
d'équipe + N champs membres), `tournoi_gerer.html` (membres sous chaque équipe,
ajout adapté). **Code de désinscription par équipe** ; **affichage public = nom
d'équipe seul** (membres visibles seulement côté bénévole). Décisions Simon :
tous modes, taille configurable à la création, code par équipe, membres non
publics. **Suite globale : 164 tests verts.**

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

**Planning — correctif « une personne par créneau » : FAIT.** Bug : l'ajout/
remplacement manuel passait par `affecter()` qui ne refusait qu'un doublon
EXACT (même créneau+poste+bénévole), permettant de placer quelqu'un sur deux
postes du même créneau (le préremplissage, lui, l'évitait via son suivi
`occupe`). Désormais `affecter()` REFUSE si le bénévole a déjà une affectation
sur ce créneau (quel que soit le poste) ; `remplacer_affectation()` vérifie le
conflit **avant** de supprimer l'ancienne (sinon perte de la case). La page de
case exclut des propositions les bénévoles déjà occupés sur le créneau et affiche
un message si l'action est refusée. **2 tests** ajoutés. Suite **126 tests verts.**

**Planning — menus de case groupés par préférence : FAIT.** Sur la page d'édition
de case, les menus « Ajouter » et « Remplacer par » classent les bénévoles par
**niveau de préférence déclaré pour ce poste** (⭐ Préféré → OK → Sans préférence
→ Si nécessaire), via des `<optgroup>` ; en bas, un groupe « Non disponible sur
ce créneau / à éviter » (non dispos + « surtout pas »). La route `admin_case`
construit ces groupes (`groupes` + `autres`) ; le gabarit `planning_case.html`
utilise un macro Jinja `options_benevoles`. Aide à prioriser d'un coup d'œil.

**Planning — .ics (idée 4.1) : FAIT.** « Mon planning » exportable en un tap,
sur le patron d'`ical_tournoi` (`app/tournoi/services.py`). Nouvelle fonction
`app/planning/services.py::ical_planning_benevole(conn, id_benevole)` :
construit un flux iCalendar **multi-VEVENT** (un par affectation, à partir de
`planning_du_benevole`) — résumé = nom du poste ou libellé de la tâche
(`type='tache'`), description = jour (`libelle_jour`) + nom de l'association,
`UID` du type `planning-{id_affectation}-...@desjeuxpleinlamanche`. `None` si
aucune affectation. **Helpers `_ics_horodatage`/`_ics_echappe` dupliqués**
localement (pas de facteur commun avec `tournoi` — modules indépendants, bases
séparées, décision maintenue). Route publique `GET /planning/mon.ics?code=`
(`app/planning/routes.py`) : même lookup par code que `/planning/mon`, 404 si
code invalide ou aucune affectation (jamais d'erreur brute), en-tête
`Content-Disposition: attachment`. Bouton « 📅 Ajouter tout mon planning à mon
agenda » sur `planning_mon.html` (visible seulement s'il y a des affectations,
même style que le bouton équivalent des tournois). Aucune donnée personnelle
dans le fichier (pas de nom de bénévole). **3 tests dédiés** (contenu multi-
VEVENT, code invalide, bénévole sans affectation). **Suite globale : 182 tests
verts.**

**Script d'installation VPS (`deploy/install.sh`) : FAIT.** Script bash
interactif à lancer sur le VPS après `git clone` (`sudo ./deploy/install.sh`),
pensé pour quelqu'un de non-développeur. Vérifie/installe les prérequis
(Python 3.11+ — tente `python3.11` via apt si absent —, nginx, certbot, git,
sqlite3, ufw, dépendances de compilation pour Pillow). Questions posées dans
l'ordre : domaine, e-mail (Let's Encrypt), **nom de l'association**, mot de
passe admin (saisie masquée, confirmée), chemin d'installation (défaut
`/opt/ludotex`), chemin des bases SQLite (défaut `/var/lib/ludotex`).
L'URL du dépôt GitHub n'est **pas** demandée : fixée en dur
(`https://github.com/Dramac/LudoteX`, décision Simon). Génère le `.env`
(jeton bénévole temporaire puis **régénéré définitivement avec expiration à 1
semaine** via `auth.reinitialiser_jeton` une fois les bases initialisées),
crée le venv + installe `requirements.txt`, initialise les trois bases
(`app.db`, `app.tournoi.db`, `app.planning.db`), installe le service systemd
et la config nginx (chemins réécrits via `sed` selon les réponses), obtient le
certificat Let's Encrypt (vérifie d'abord que le DNS pointe vers le serveur),
propose la sauvegarde quotidienne automatique (crontab), puis affiche le lien
d'activation bénévole. Relançable sans casser une install existante (ne
réécrase pas un `.env` déjà présent sans confirmation). `docs/deploiement.md`
réécrit autour de ce script (accès SSH, clonage, exécution, vérification,
QR définitifs, sauvegarde, mise à jour `git pull` + `restart`, dépannage), avec
les étapes manuelles détaillées conservées en annexe. `README.md` : nouvelle
section « Installation en production » (résumé + lien).

**Nom de l'association configurable (`NOM_ASSOCIATION`) : FAIT.** Corollaire
de la question posée par `install.sh` : « Des jeux plein la Manche » était
codé en dur à une quinzaine d'endroits (bandeau, pied de page, page « À
propos », écran `/live`, message de partage du jeton bénévole, exports
Excel/PDF des stats, fichiers `.ics` des tournois). Décision Simon (option
« partout dans l'app ») : nouveau module `app/config.py`
(`NOM_ASSOCIATION = os.getenv("NOM_ASSOCIATION", "Des jeux plein la Manche")`),
exposé comme global Jinja (`{{ nom_association }}`, voir `app/templating.py`)
et importé directement dans `routes/live.py` (`TITRE_DEFAUT`), `routes/admin.py`
(message de partage), `exports.py` et `tournoi/services.py` (`.ics`). Défaut
inchangé si la variable est absente → aucune régression sur les déploiements
existants. `.env.example` complété (`NOM_ASSOCIATION` + `PLANNING_DATABASE_PATH`,
qui manquait). **Suite globale toujours verte (142 tests)**.

**Sauvegarde & restauration complète (3 bases) : FAIT.** Logique isolée dans
`app/sauvegarde.py` (testable) + routes `GET /admin/sauvegarde/export`,
`POST /admin/sauvegarde/import` dans `routes/admin.py`. **Export** : chaque
base (prêt, tournois, planning) copiée **à chaud** via
`sqlite3.Connection.backup()` (cohérent même en WAL, app non interrompue),
regroupées dans un zip `ludotex-backup-AAAA-MM-JJ.zip` sous des noms **FIXES**
(`pret-jeux.db`/`tournoi.db`/`planning.db`, indépendants du chemin réellement
configuré en `.env` — lu via `get_database_path()` de chaque module, jamais
dupliqué en dur) + `INFO.txt` (date/heure/version). **Import** : validation
stricte AVANT toute modification (`valider_zip_sauvegarde` — 3 fichiers
présents + `PRAGMA integrity_check` sur chacun, lève `ZipInvalide` sinon, page
réaffichée avec message clair, jamais d'erreur brute) ; **filet de sécurité
silencieux** (`sauvegarde_de_securite`) qui exporte l'état actuel dans
`data/sauvegardes/avant-restauration-<horodatage>.zip` juste avant de
remplacer quoi que ce soit ; remplacement fichier par fichier
(`_remplacer_fichier`, purge les éventuels `-wal`/`-shm`/`-journal` de la
destination pour ne jamais mélanger d'anciennes écritures avec le contenu
restauré) — sûr car chaque route ouvre/ferme sa propre connexion (pas de pool).
**9 tests** (`tests/test_sauvegarde.py` : zip complet avec les 3 bases +
INFO.txt, rejet d'une archive incomplète, rejet d'une base corrompue, rejet
d'un fichier non-zip, restauration qui remplace bien les données + filet de
sécurité vérifié, routes export/import protégées par la garde admin).

**Fusion des sous-menus « Base de données » et « Sauvegarde » : FAIT.** Une
seule page `/admin/donnees` (gabarit `admin_donnees.html`) regroupe désormais
le catalogue (import/export CSV/Excel) ET la sauvegarde complète des 3 bases
(export/import zip) — un seul lien de menu « 🗄️ Données & sauvegarde » dans
`admin_dashboard.html` au lieu de deux. `_page_donnees` (dans `routes/admin.py`)
sert de rendu commun (compteurs catalogue + message), réutilisé par
`donnees_import` et par `sauvegarde_import` (qui n'a plus de page dédiée, juste
les actions `GET /admin/sauvegarde/export` et `POST /admin/sauvegarde/import`).
Gabarit `admin_sauvegarde.html` supprimé (devenu inutile). **Suite globale :
151 tests verts** (inchangée, aucun test ne visait la page fusionnée).

**Lanceur local sans ligne de commande (`lancer.py` + `lancer.vbs`/`lancer.bat`) :
FAIT.** Pour un poste Windows de bénévole, sans terminal : double-clic sur
`lancer.vbs` (silencieux, `pythonw.exe`) ou `lancer.bat` (console visible,
débogage). `lancer.py` orchestre tout, sans dépendance supplémentaire
(réutilise `qrcode`/`pillow` déjà présents, et `http.server` stdlib) : vérifie
les prérequis (`.venv`, `cloudflared` dans le PATH ou à la racine du projet,
ports 8000/8001 libres) — sinon page HTML d'erreur claire, jamais de plantage
brut ; démarre `uvicorn` en sous-processus caché (port 8000) ; démarre
`cloudflared tunnel --url http://localhost:8000` et **lit stderr ligne par
ligne** pour en extraire l'URL publique par regex ; génère une page HTML
**temporaire** (QR via `app.etiquettes.image_qr_nu` — même dessin que le reste
de l'appli — encodé en base64, URL en grand, statut, bouton rouge « Arrêter
LudoteX ») et l'ouvre dans le navigateur par défaut ; démarre un micro-serveur
de contrôle (`http.server.ThreadingHTTPServer`, port 8001, `/status` JSON +
`/stop`) que la page interroge par polling JS (5 s) et que le bouton d'arrêt
appelle en `fetch()` ; termine proprement les sous-processus sur arrêt (bouton
ou Ctrl+C). Fichiers HTML temporaires nommés `lancer-ludotex-*.html`
(`tempfile`, dossier temp du système) → motif ajouté au `.gitignore` par
prudence. Doc dédiée `docs/lancement-local.md` (installation de `cloudflared`
sur Windows — téléchargement direct ou `winget` —, usage, limites : **URL du
tunnel différente à chaque lancement**, donc incompatible avec des QR
imprimés à l'avance — réservés au déploiement définitif sur domaine fixe).
`lancer.py`/`lancer.vbs`/`lancer.bat` **versionnés** (pas dans `.gitignore`).

**Saisie manuelle de secours sur le scanner (idée 2.4) : FAIT.** Sous la zone
caméra de `/scanner`, un petit formulaire GET (« Saisie manuelle » →
`GET /scanner/saisie?code=…`, `routes/scanner.py`) permet de TAPER le code de la
boîte (`id_exemplaire`) quand le QR est illisible / la caméra capricieuse, et
d'arriver directement sur `/pret/<id>` sans repasser par le catalogue. **Aucun
JS ajouté.** `id_exemplaire` reste du TEXT (zéros de tête préservés, jamais
d'interprétation en entier ; seuls les espaces autour sont retirés via
`.strip()`). **Jamais bloquant** : code vide/inconnu → la page scanner est
ré-affichée avec un message clair (`services.info_exemplaire` valide l'existence)
et le champ prérempli/`autofocus`, prêt à corriger — pas d'erreur brute. **Même
protection que le scanner** (`exiger_jeton`, aucune nouvelle surface publique).
Style mobile-first (`.saisie-manuelle`). **4 tests** (présence du formulaire,
code valide → 303 vers `/pret/<id>` avec espaces tolérés, code inconnu →
message + valeur préremplie, accès sans jeton → 403). **Suite globale : 168
tests verts.**

**Habillage UI du catalogue (léger, sans framework ni JS ajouté) : FAIT.**
Purement CSS (`app/static/css/style.css`) + gabarits. (1) **Ouverture animée**
des panneaux `<details class="recherche">` (keyframe `recherche-ouverture` :
fondu + léger glissement) — joue à l'ouverture uniquement (le `<details>` natif
masque instantanément à la fermeture, non animé, assumé). (2) **Puces de filtres
actifs** sur `/catalogue` : chaque filtre posé s'affiche en pastille cliquable
qui le retire seul (les autres conservés) + « Tout effacer » ; liens de retrait
calculés côté serveur (`routes/catalogue.py:_puces_filtres`, `urlencode`, passés
en `chips`). (3) **Relief au survol** des cartes `.jeu` (ombre + léger
soulèvement). (4) **Focus clavier visible** homogène (`:focus-visible`,
liseré violet) + retour visuel à l'appui des boutons/puces, **tout le site**.
(5) `<meta name="theme-color" content="#4a148c">` + favicon/`apple-touch-icon`
(logo) dans `base.html`. Tout est **coupé sous `prefers-reduced-motion`**.
Aucune régression (**151 tests verts**, purement cosmétique).

**Point 7.2 — Supervision légère en admin : FAIT.** Page `GET /admin/supervision`
(lecture seule, protégée par la garde admin existante, liée depuis le tableau
de bord) pour qu'un bureau non technicien vérifie en 5 secondes que tout va
bien le jour de l'événement. Logique isolée dans `app/supervision.py`
(testable, **stdlib uniquement** : `shutil.disk_usage`, `pathlib`) : (1) état
des **3 bases** (chemin/taille/date de dernière modification), chemins
toujours lus via `get_database_path()` de chaque module — **jamais dupliqués
en dur** ; (2) **espace disque** restant du volume contenant `data/` ; (3)
**dernière sauvegarde** trouvée dans `data/sauvegardes/` (fichier le plus
récent par mtime — dossier des filets de sécurité automatiques de
`app.sauvegarde.sauvegarde_de_securite` — ou mention claire si vide) ; (4)
**état du jeton bénévole** (défini/non, date d'expiration, expiré ou valide,
via `auth.jeton_actuel`/`expiration_jeton`/`jeton_expire`) ; (5) **version
déployée**, lue telle quelle depuis un fichier `VERSION` à la racine (contenu
libre ; repli sur `APP_VERSION` si absent). **Aucune action d'écriture** sur
cette page — uniquement des liens vers `/admin/donnees` et `/admin/jeton` pour
agir. Libellés en français clair (« Aucune sauvegarde trouvée… » plutôt que
des détails techniques bruts). **13 tests dédiés**
(`tests/test_supervision.py`, service pur) + test de route (garde + rendu des
5 sections). **Suite globale : 179 tests verts.**

**Point 7.1 — Mode bac à sable / formation : FAIT.** Architecture retenue
(décidée avant implémentation, cadrage affiné) : un **« SITE BIS »** = une
**SECONDE INSTANCE** de la même application (même code, même dépôt), lancée
avec son propre `.env` (`MODE_FORMATION=1` + bases SQLite **jetables**
séparées), exposée sur un **sous-domaine dédié** (ex. `formation.<domaine>`,
jamais un préfixe de chemin). **Aucun routage dynamique de connexion** dans le
code — l'isolation vient uniquement du fait que l'instance ne connaît que ses
propres bases. `app/config.py` expose `MODE_FORMATION` (bool, env
`1`/`true`/`on`) et `FORMATION_URL` (optionnelle, lien admin côté PRODUCTION
uniquement), tous deux injectés comme globals Jinja (`app/templating.py`) —
**absent/0 : zéro changement visuel ni fonctionnel côté production** (vérifié
par test). Quand actif : bandeau fixe orange (`#e65100`, différent du violet
habituel `#4a148c`) « 🎓 SITE DE FORMATION — aucun effet sur LudoteX » sur
**toutes** les pages (public/bénévole/admin, un seul point de contrôle dans
`base.html`), et un filigrane diagonal « FORMATION » en pur CSS (image de fond
SVG encodée en donnée, `body.mode-formation::after`, `pointer-events: none`),
coupé à l'impression (`@media print`). Bandeau formation + bandeau habituel
regroupés dans `.bandeau-groupe` (sticky commun) pour ne pas se chevaucher.
Script de peuplement `app/formation.py` (`python -m app.formation`, modèle
`app/planning/demo.py` mais **idempotent au sens fort** : VIDE puis repeuple —
contrairement à la démo planning qui ajoute sans toucher à l'existant) : 20
jeux fictifs (« Jeu d'essai n°1 »…, catégorie « Formation »), 5 prêts en cours
+ 5 prêts rendus, 1 tournoi d'exemple (état `inscriptions`, 4 inscrits) —
touche les DEUX bases (prêt + tournois) de l'instance courante. Bouton
« Réinitialiser les données de formation » au tableau de bord admin, **visible
uniquement si `MODE_FORMATION=1`** (`POST /admin/formation/reinitialiser`,
revérifie `MODE_FORMATION` côté serveur — 404 sinon, même si le bouton n'est
jamais rendu en production) ; sûr par construction (bases jetables propres à
l'instance). Sur la PRODUCTION : lien « 🎓 Site de formation » au tableau de
bord si `FORMATION_URL` définie (masqué sinon). Déploiement : `deploy/install.sh`
propose une étape 9 optionnelle « Site de formation » — sous-domaine, bases
`<chemin>-formation`, service systemd dédié `ludotex-formation` (port 8100,
`EnvironmentFile=/etc/ludotex-formation.env` pour ne jamais toucher au `.env`
de prod tout en partageant le même code), bloc nginx + certificat Let's
Encrypt pour le sous-domaine, peuplement initial, et pose automatiquement
`FORMATION_URL` dans le `.env` de production. Nouveaux gabarits
`deploy/ludotex-formation.service` et `deploy/nginx-ludotex-formation.conf`
(mêmes conventions que leurs équivalents production). `docs/deploiement.md`
(section « Site de formation ») et nouvelle doc `docs/mode-formation.md`
(accès, réinitialisation, QR d'entraînement via `scripts/generate_qr.py
--base-url`, suppression complète). QR d'entraînement : aucun script
réécrit, juste `--base-url https://formation.<domaine>`. **11 tests dédiés**
(`tests/test_formation.py` : comptes + idempotence des deux bases) + 3 tests
de route (`tests/test_routes.py` : bandeau absent par défaut sur pages
publique/bénévole/admin + bouton/lien absents + reset 404 ; bandeau présent
partout + bouton fonctionnel quand actif ; lien admin conditionné à
`FORMATION_URL` en production). **Suite globale : 190 tests verts.**

**Tableau de bord admin en deux colonnes sur grand écran : FAIT.** Corollaire
de `.contenu { max-width: 540px }` (global, `style.css`) qui bridait `/admin`
en une seule colonne même sur ordinateur — même cause déjà rencontrée pour
`planning_public`/`stats`/etc. (voir plus haut), traitée ici avec le même
principe (`.contenu-large` + bloc Jinja `conteneur_extra`) plutôt qu'une
nouvelle mécanique. `admin_dashboard.html` passe en `.contenu-large`, avec une
grille CSS deux colonnes **à partir de 900px** (`.admin-dashboard-grille`,
media query dédiée dans `style.css`) : colonne gauche = menu « Gérer » (+
modules, fin d'événement), colonne droite = **supervision légère en direct**
(mêmes informations que `/admin/supervision` : bases, disque, sauvegarde,
jeton, version). Le contenu de la supervision est **factorisé** dans
`app/templates/_supervision_contenu.html` (fragment paramétré par `{{ etat }}`),
inclus à la fois par `admin_supervision.html` (page dédiée, sous son propre
`<h1>`) et par `admin_dashboard.html` (carte « 🩺 Supervision » de la colonne
droite). Route : `etat_supervision(conn)` est désormais calculé pour **toutes**
les routes qui réaffichent le tableau de bord (connexion, clôture des prêts,
réinitialisation formation), via un helper commun `_rendre_dashboard()` dans
`routes/admin.py` (évite la triplication). **Sous 900px, rien ne change** :
la colonne supervision est masquée en CSS (`display:none`, le lien
« 🩺 Supervision » du menu « Gérer » reste alors le seul accès), et le menu
« Gérer » lui-même est resté une liste plate visuellement identique à l'ancienne
(les nouveaux sous-groupes — Jeux & étiquettes / Données & accès / Événement /
Configuration — sont dans le HTML pour la hiérarchie visuelle du bureau, mais
leurs titres `<h3>` sont masqués sous 900px et les `<ul>` n'ajoutent aucune
marge propre, donc l'empilement mobile reste identique au pixel près). Au-delà
de 900px : titres de sous-groupes visibles, séparateurs entre groupes, lignes
de menu plus aérées avec surbrillance au survol. Aucune dépendance JS ajoutée
(CSS pur, comme le reste du projet). **1 test ajouté**
(`test_admin_dashboard_supervision_embarquee`), **191 tests verts**.

**Tableau de bord admin — resserrage + libellés courts + menu « Gérer » sur 2
colonnes (grand écran) : FAIT.** Suite du passage en deux colonnes ci-dessus :
objectif tenir la page sans défilement sur un écran d'ordinateur courant.
(1) **Titres resserrés** : `h1`/`h2` du tableau de bord et `h3` de la colonne
supervision passent à une taille et des marges réduites, `.carte` gagne un
padding/margin-bottom un peu plus compact — le tout **scopé à
`.admin-dashboard-grille`** (`style.css`, media `min-width:900px`) pour ne pas
toucher `.carte`/`h1`/`h2` des autres pages en `.contenu-large`
(stats/planning/tournois). (2) **Libellés du menu « Gérer » raccourcis**
(ex. « Imprimer des étiquettes (par lot) » → « Étiquettes (lot) »), le texte
complet reporté en attribut `title` (info-bulle au survol, aucun JS) ; icônes
mises dans un `<span class="admin-icone">` dédié et **agrandies**
(`1.25em` de base, `1.4em` dès 900px) pour rester repérables malgré le texte
plus court. (3) **Sous-groupes en grille 2×2** dès 900px
(`.admin-groupes { display:grid; grid-template-columns: 1fr 1fr }`), au lieu
d'empiler les 4 groupes (Jeux & étiquettes / Données & accès / Événement /
Configuration) verticalement — divise la hauteur du menu par ~2 ; une bordure
haute marque la 2ᵉ rangée. **Mobile inchangé pour la structure** (une seule
colonne, groupes empilés, titres de groupe masqués) ; les libellés raccourcis
et l'agrandissement léger des icônes s'appliquent en revanche **aussi sur
mobile** (amélioration de lisibilité assumée des deux côtés, contrairement à
la mise en page qui reste strictement scopée au grand écran). **191 tests
toujours verts** (aucune assertion ne portait sur le texte long des liens du
tableau de bord — vérifié).

**Session de correction — 3 bugs de l'audit UX (`docs/idees-ux.md`) : FAIT.**
Un commit par bug, dans l'ordre demandé.
**Q1** : pluriel « 15 jeus » sur l'accueil (`accueil.html`) — `'s'` → `'x'` ;
vérifié par grep qu'aucun autre gabarit ne construisait ce pluriel-là (les
autres pluriels du site, « inscrit(s) », « joueur(s) », étaient déjà corrects).
**M2** : le planning bénévole triait les jours par `libelle_jour` alphabétique
(« Dimanche » avant « Samedi »). Nouvelle fonction
`app/planning/services.py::jours_chronologiques(creneaux)` (tri Python sur
`MIN(debut)`, UTC ISO triable lexicalement, aucun changement de schéma),
utilisée par `construire_grille` — dont héritent la page publique, la grille
admin **et** les exports Excel/PDF (ils partent tous de `construire_grille`) —
et par le formulaire de collecte (`routes.py`), qui dupliquait le même
groupement à la main. La liste à plat des créneaux dans les sections
« Trame »/« Besoins » de l'écran admin reste hors scope (pas un groupement par
jour). Vérifié avec `python -m app.planning.demo`.
**M1** : l'histogramme `/stats` groupait les prêts par heure UTC
(`substr(date_sortie,1,13)`) alors que le reste de la page est en heure
locale. `services.prets_par_heure` récupère maintenant les `date_sortie` bruts
et groupe en Python après `.astimezone(FUSEAU_LOCAL)` (toujours aucune logique
de fuseau en SQL, cohérent avec le reste du module). Chaque entrée porte un
`label` prêt à afficher (« 15h », ou « 17/07 15h » si la période couvre
plusieurs jours locaux) ; `stats.html` l'utilise directement au lieu de
découper la chaîne ISO. Aucun export Excel/PDF ne reprenait `par_heure` (aucun
changement nécessaire de ce côté). Tests ajoutés pour les 3 bugs (dont le cas
de bascule de jour 23:30 UTC → 01:30 local le lendemain pour M1). **Suite
globale : 197 tests verts.**

**Session UX — lot « bénévole au prêt » (`docs/idees-ux.md` Q3/Q4/M3/M8) :
FAIT.** Un commit par point, dans l'ordre demandé. Objectif commun : fluidifier
`/pret/<id>`, le geste répété toute la journée de l'événement.
**Q3** : au retour, le numéro d'emplacement était noyé dans une phrase alors
qu'il s'affiche en 5 rem au prêt. `pret.html` (résultat `rendu`) reprend
désormais le même gabarit `.resultat-libelle` + `.pochette-num` qu'au prêt
(« Récupérer la pièce d'identité à l'emplacement n° » + numéro géant), avec une
nouvelle variante `.pochette-num--retour` (bleu `#1a73e8`, `style.css`) pour
distinguer d'un coup d'œil un retour d'un prêt (vert). `rendu_tournoi` non
touché (pas d'emplacement).
**Q4** : le « Scanner le jeu suivant » — le geste le plus répété — n'était
qu'un petit lien en pied de carte. Un vrai bouton pleine largeur
`a.bouton.bouton-secondaire` (« 📷 Scanner le jeu suivant ») apparaît
désormais juste sous le bandeau de résultat, pour TOUS les types de résultat.
Le petit lien du pied de carte disparaît alors (redondant, seul « Voir la
fiche publique » reste) ; en simple consultation (pas de résultat), le pied de
carte est inchangé.
**M3** : aucune protection contre le double-appui sur un wifi de salle lent
(le second POST affichait « déjà sorti », lu comme une erreur). Script inline
dans `base.html` (aucune dépendance) : au `submit`, désactive les boutons
`type=submit` du formulaire soumis et remplace leur libellé par
« Un instant… » (`innerHTML` sauvegardé dans `dataset.libelle`). Respecte les
`onsubmit="return confirm(...)"` existants via `e.defaultPrevented` (le submit
event bubble jusqu'à `document` APRÈS le handler du formulaire cible, donc un
`confirm()` refusé a déjà marqué `defaultPrevented` — rien n'est désactivé
dans ce cas). Réactivation au `pageshow` (bouton « page précédente » du
navigateur, qui sert une page en cache avec des boutons restés désactivés).
Logique JS non exécutable sous pytest (pas de moteur JS dans les tests) :
vérifiée manuellement (`node --check` + relecture), test de présence du script
ajouté.
**M8** : un retour était affiché en bleu `resultat-info` (notice) alors que
c'est un succès au même titre qu'un prêt. `rendu` et `rendu_tournoi` passent
en `resultat-ok` (vert). `tournoi_sorti` (sortie, pas un retour) reste en bleu
(information neutre) ; `deja_sorti`/`deja_disponible` restent en orange (rien
n'a été modifié). Tests ajoutés pour les 4 points. **Suite globale : 200 tests
verts.**

**Session UX — lot « finitions transverses » (`docs/idees-ux.md` Q2, Q5–Q12) :
FAIT.** Neuf points indépendants, un commit chacun, purement cosmétiques/
accessibilité (aucune nouvelle dépendance, aucun changement fonctionnel).
**Q2** : global Jinja `pluriel(n, singulier, pluriel)` (`app/services.py`,
enregistré dans `app/templating.py`) — grammaire FR -1/0/1 singulier, |n|≥2
pluriel. Remplace ~15 pluriels parenthésés type « jeu(x) », « prêt(s) »,
« exemplaire(s) » dans `catalogue.html`, `fiche.html`, `stats.html`,
`admin_jeux.html`, `admin_fiche.html`, `admin_donnees.html`,
`planning_gerer.html`, `planning_admin.html`, `planning_case.html`,
`tournoi_arbre.html`, `tournoi_rondes.html`, `tournoi_supprimer.html`.
**Q5** : `base.html` — `{% block titre %}` par défaut pointe sur
`{{ nom_association }}` au lieu de « Prêt de jeux » en dur ; `fiche.html`
aligné sur le même motif que les autres pages (`<nom du jeu> —
{{ nom_association }}`). **Q6** : `#9aa0a6` (contraste insuffisant) remplacé
par `#6b7075` sur 4 règles CSS (`.stats-note`, `.palmares-val small`,
`.planning-bloc--termine`, `.rr-vide`) — `--gris: #5f6368` non touché.
**Q7** : texte scanner « Une seule autorisation caméra par session » →
« Votre téléphone ne demandera l'autorisation caméra qu'une seule fois »
(moins jargonneux). **Q8** : `aria-live="polite"` sur le statut du scanner
(lecteurs d'écran). **Q9** et **Q10** : investigation sans bug réel trouvé —
`stats_globales` renvoyait déjà « — » (pas « 0 min ») sans prêt terminé
(`AVG` SQL sur 0 ligne → `NULL`/`None`), il ne manquait qu'un `title` explicite
sur le chiffre ; le champ mot de passe admin avait déjà `autofocus`. Dans les
deux cas : constat documenté + test de non-régression ajouté, sans changement
de code inutile. **Q11** : le JPEG source (`logo_djplm.jpg`) était en fait
déjà carré (1509×1509) — le vrai défaut était le **cadrage** (sorcier décentré
sur 2/3 gauche du canevas), illisible une fois réduit en icône. Recadrage
centré tête/chapeau/barbe, régénéré en `favicon-192.png`/`favicon-512.png`
(Pillow, LANCZOS), vérifié lisible jusqu'à 32×32. `base.html` référence ces
PNG (`rel="icon"` par taille + `apple-touch-icon`), PNG versionnés (seul
`*.qr.png` est exclu du dépôt). **Q12** : `.bouton-filtrer` aligné sur
`border-radius: 12px` comme `.bouton` (transitions/hover/active déjà
mutualisées). **Suite globale : 205 tests verts.**

**Corrections mobile — retour terrain iPhone 13 mini (menu bandeau + tableau
supervision) : FAIT.** Deux points indépendants, un commit chacun, remontés
après test réel sur smartphone (capture à l'appui). **Menu du bandeau** : le
menu bénévole/visiteur (`_menu_benevole.html`/`_menu_visiteur.html`, inclus
par `base.html`) s'affichait à plat et passait sur 3 lignes sur petit écran —
plus de 40 % de la hauteur visible mangée par le bandeau sticky avant même le
contenu. Ne concerne QUE l'instance du bandeau : l'inclusion du même fragment
dans la section « Aller aux modules » du tableau de bord admin
(`admin_dashboard.html`) n'est pas touchée, ce n'est pas une barre fixe qui
mange l'espace là-bas. **Tableau « Bases de données » de la supervision** :
`.admin-table` (utilisée par `/admin/supervision` et `/admin/fonctionnalites`)
n'avait jamais eu de règle CSS dédiée → largeur au contenu par défaut du
navigateur, débordement à droite sur petit écran (colonnes tronquées,
« Dernière modification » invisible). Même recette que `.detail` (déjà
éprouvée sur mobile ailleurs, ex. « Détail des prêts ») : largeur 100 %,
cellules qui s'enveloppent (`word-break`) plutôt que de déborder. Aucun
changement visuel sur grand écran. **2 tests ajoutés** (structure `<details>`
+ présence de la règle CSS via `/static/css/style.css`). **Suite globale :
207 tests verts.**

**Correctifs suite au retour ci-dessus (2ᵉ passage, menu invisible sur
ordinateur + lisibilité supervision) : FAIT.** Deux nouveaux points
indépendants, un commit chacun. **Régression du menu bandeau** : la première
version du repli mobile reposait sur un unique `<details class="menu-
bandeau">` qu'on tentait de « forcer ouvert » en CSS dès 640px
(`display:flex !important` sur son contenu). Repéré cassé sur ordinateur —
menu resté invisible. Cause probable : certains moteurs de rendu appliquent
un traitement interne (proche de `content-visibility:hidden`) au contenu d'un
`<details>` fermé, qu'une règle `display` d'auteur ne suffit pas toujours à
surcharger, même avec `!important`. Remplacé par une approche sans piège :
DEUX rendus séparés du même menu (le fragment de liens est inclus deux fois
dans `base.html`, un seul point de maintenance des liens) — un `<details>`
replié (affiché sous 640px) et une copie à plat dans une `<div class="menu-
bandeau-large">` (affichée dès 640px) ; CSS bascule laquelle des deux est
visible via un simple `display:none/block`, sans forcer l'état d'un
`<details>`. Coût : `module_visible()` appelé deux fois par page (lecture
SQLite locale déjà bon marché et déjà appelée plusieurs fois par page
ailleurs — négligeable). **Lisibilité du tableau de supervision** : l'état de
chaque base (« Présente »/« Introuvable ») réutilisait `.resultat` — une
bannière pleine largeur (padding 20px, prévue pour les écrans de prêt/retour)
compressée en `display:inline-block` dans une cellule, disproportionnée et
malaisée à lire une fois le tableau compressé sur mobile. Remplacée par
`.badge-ok`/`.badge-attention`, deux nouvelles variantes de la classe
`.badge` **déjà existante** (pastille compacte utilisée pour dispo/sorti sur
le catalogue, les tournois et les fiches admin — pas de nouvelle classe
redondante, cohérence visuelle avec le reste du site). « Présente » → « Ok »
(la pastille verte porte déjà le sens visuel) ; « Introuvable » inchangé.
**Suite globale toujours 207 tests verts** (tests mis à jour, pas de test
supplémentaire).

**Lien « Administration » dans le menu du bandeau : FAIT.** Un administrateur
connecté n'avait aucun moyen rapide de revenir au tableau de bord depuis les
autres pages (catalogue, scanner, stats…) — il fallait retaper `/admin` dans
la barre d'adresse. Nouveau global Jinja `est_admin(request)`
(`app/templating.py`), qui réutilise `admin_auth.admin_connecte` (session
admin par mot de passe, **distincte** du jeton bénévole — un administrateur
peut être connecté sans avoir activé le jeton, et inversement).
`_menu_benevole.html` (fragment partagé bandeau + tableau de bord admin) :
lien « Administration » → `/admin` ajouté en fin de liste, visible
UNIQUEMENT si `est_admin(request)`. Un seul point de maintenance : le lien
apparaît automatiquement dans les deux rendus du bandeau (replié/à plat, cf.
correctif menu ci-dessus) sans toucher à `base.html`. Portée volontairement
limitée à `_menu_benevole.html` : un admin connecté a TOUJOURS
`est_benevole(request)` vrai (`peut_ecrire` teste `admin_connecte` en
premier), donc `_menu_visiteur.html` n'a jamais besoin de ce lien. **1 test
ajouté** (absent sans session, présent dans les 2 rendus une fois connecté,
disparaît après déconnexion). **Suite globale : 208 tests verts.**

**M4 — Copier le code de désinscription/modification en un tap : FAIT.**
`tournoi_inscription_ok.html` (code de désinscription tournoi) et
`planning_collecte_ok.html` (code de modification des souhaits bénévole)
n'affichaient le code qu'en texte brut, à noter soi-même. Motif « Copier »
de `/admin/jeton` **réutilisé tel quel** (pas de nouvelle abstraction) sur
les deux pages : bouton `.bouton-filtrer` « Copier le code » +
`navigator.clipboard.writeText(...)` + confirmation `<span class="copie-
ok">copié ✓</span>` (classe CSS déjà existante) réaffichée 2 s. Le code est
injecté dans le script via `{{ code | tojson }}` (échappement JS sûr).
Sur la page planning (accessible SANS code par filet de sécurité), bouton
et script sont conditionnés à `{% if code %}` — rien à copier n'apparaît
alors, jamais bloquant. Chaque page garde sa propre fonction `copierCode()`
(pas de mutualisation avec `admin_jeton.html`), cohérent avec l'existant
(`copierLien`/`copierDiscord` y sont déjà deux fonctions séparées plutôt
qu'une abstraction commune). **3 tests ajoutés** (bouton + script présents
avec le bon code à l'inscription tournoi ; idem sur la confirmation
planning ; absence du bouton quand la page planning est atteinte sans
code).

**M5 — Griser les champs inapplicables au lancement d'un tournoi : FAIT.**
`tournoi_gerer.html`, bloc « Lancer le tournoi » : le nombre de rondes et la
case BO3 s'affichaient toujours actifs, alors qu'ils ne s'appliquent qu'à
certains modes de scoring (notice texte en compensation). Script inline
(IIFE, aucune dépendance) sur `#mode_scoring` : au chargement et à chaque
`change`, grise (`disabled` + `opacity:.45`) `#champ_rondes` (rondes actives
seulement pour `ronde_suisse`) et `#champ_bo3` (BO3 désactivé seulement pour
`high_score`, actif pour suisse/round robin/élimination) — deux `id`
ajoutés aux conteneurs existants, aucune restructuration. Logique alignée
sur les règles RÉELLES de `services.lancer_tournoi` (pas sur le texte de la
notice, qui omettait déjà round robin pour le BO3). **La notice reste
affichée telle quelle** : repli utile si JS est indisponible, et le serveur
revalide de toute façon tout — aucune règle dupliquée côté client, purement
cosmétique. **1 test ajouté** (script, deux `id`, conditions exactes, notice
toujours présente). **Suite globale : 320 tests verts.**

**M6 — Menu bénévole empilé sur mobile : déjà couvert (aucun code).** En
relisant la fiche avant de coder M6, constat que le besoin qu'elle décrit
(le menu du bandeau s'empilait sur 3 lignes sur petit écran, mangeant
l'écran du scanner) a été résolu entre-temps par la session « retour
terrain iPhone 13 mini », avec un mécanisme DIFFÉRENT de la suggestion
écrite dans la fiche (`overflow-x: auto` sur `.menu-benevole`). Le menu du
bandeau est désormais replié par défaut sous 640px dans un `<details
class="menu-bandeau">` (accordéon natif) et redevient à plat au-delà — voir
plus haut. Décision (validée avec Simon) : ne pas superposer le motif
`overflow-x` de la suggestion d'origine par-dessus un correctif qui
fonctionne déjà et qui atteint le même objectif ; M6 marqué FAIT dans
`docs/idees-ux.md` avec un renvoi vers le correctif réel, sans modification
de code ni de test supplémentaire.

**M7 — Bouton flottant « ↑ Recherche » sur le catalogue : FAIT.** 600 titres
= un seul long défilement, panneau de recherche hors champ dès qu'on avance
dans la liste. `id="haut"` posé sur la section de tête de `catalogue.html`
(celle du panneau « Rechercher / filtrer ») ; lien `<a href="#haut">
↑ Recherche</a>` **toujours visible**, `position: fixed; bottom: 16px;
right: 16px` — pas de logique d'apparition au défilement, aucun JS, comme
demandé. Habillage **réutilisé** de `.bouton-filtrer` (couleur, hover/
active, `prefers-reduced-motion` déjà mutualisés) ; seule `.bouton-haut`
(nouvelle) ajoute le positionnement flottant + l'ombre portée. Masqué à
l'impression (`@media print`, même motif que le bandeau de formation).
Pas de pagination ajoutée (hors périmètre de la suggestion retenue). **1
test ajouté** (ancre + bouton présents). **Suite globale : 321 tests
verts.**

**S1 — Inventaire des composants d'interface : EN COURS.** Fiche
`docs/idees-ux.md` (§ Améliorations structurantes) : deux « designs de
formulaire » cohabiteraient (prêt vs tournois/planning/admin). Constat
réévalué avant de coder : moins sévère qu'à l'audit — `.champ`/`.carte`/
`.resultat` sont déjà largement réutilisés partout, `.bouton-filtrer` (action
compacte/filtre) et `.pl-*` (grille planning dense) répondent à des besoins
réellement différents, pas à une duplication accidentelle. Nouveau document
**`docs/ui-composants.md`** : 10 composants canoniques (bouton principal/
secondaire/compact, champ, lien, carte, message de résultat, badge, tableau
de données) avec règle de choix entre variantes — référence à consulter/
étendre au fil des prochaines retouches, pas une passe unique refermée. Revue
systématique de tous les gabarits pour trouver les vrais écarts (plutôt que
ceux supposés par l'audit initial) ; **4 corrigés** : (1) **7 boutons**
`class="bouton"` seuls, sans `-principal`/`-secondaire` donc **sans couleur
de fond** (le CSS force pourtant un texte blanc → peu ou pas lisible selon le
navigateur), dans le module planning (`planning_case.html`,
`planning_admin.html`, `planning_collecte.html`, `planning_gerer.html` ×4,
`planning_creneau.html`) → `bouton-principal` ; (2) même défaut sur le lien
de retour de `module_desactive.html`, aligné sur le motif `.lien` des pages
sœurs (`acces_refuse.html`, `erreur.html`) ; (3) `admin_fonctionnalites.html` :
bouton désactivé bricolé en style inline + classe ajoutée en JS, faute de
composant partagé → nouvelle règle générique **`.bouton:disabled`** (CSS),
gabarit simplifié ; (4) **`.detail` et `.admin-table` identifiés comme le
même composant sous deux noms** (tableau de données dense, apparus à deux
moments du projet) → règles CSS fusionnées, `.detail` hérite au passage du
correctif anti-débordement mobile qui n'existait jusqu'ici que pour
`.admin-table`. **2 tests garde-fous ajoutés** (plus aucun `.bouton` sans
variante dans les gabarits ; présence de `.bouton:disabled`), 1 test existant
adapté à la fusion CSS. **Suite globale : 325 tests verts.** Reste ouvert
(documenté dans `docs/ui-composants.md` §11, pas de code) : pas de variante
« danger » pour un bouton destructeur — pas une incohérence en soi (aucune
page du site n'a de bouton rouge aujourd'hui), à ajouter si le besoin se
confirme ailleurs.

**S4 — Aide contextuelle repliée : EN COURS.** Fiche `docs/idees-ux.md`
(§ Améliorations structurantes) : les explications longues finissent
incrustées dans les formulaires plutôt que dans les pages d'aide dédiées
(`/aide`, `/tournoi/aide`, `/planning/aide`). Nouveau composant
**`.aide-inline`** (`app/static/css/style.css`) : `<details
class="aide-inline"><summary>❓ …</summary>…</details>`, même langage visuel
que `.recherche` (bordure, `<details>` natif, sans JS), teinte de fond
différente pour ne pas être confondu avec un panneau de filtre. Appliqué aux
3 écrans cités par la fiche, chacun avec un traitement différent selon ce que
la revue du code a montré : (1) `admin_fonctionnalites.html` — la légende des
états (`<dl class="fonct-legende">`), affichée en permanence sans aucun lien
d'aide, est repliée telle quelle ; (2) `planning_gerer.html` (grille) — pas
une longue explication à déplacer, mais un vrai trou : les 5 couleurs de la
grille (grisé/trou/partiel/complet/surcharge) n'étaient expliquées nulle
part, ni à l'écran ni sur `/planning/aide` ; la note d'interaction existante
est remplacée par un `aide-inline` qui ajoute la légende manquante + un lien
vers l'aide complète ; (3) `tournoi_gerer.html` (lancement) — la notice
existante (mode/rondes/BO3, voir M5) sert aussi de **repli visible sans JS**
pour le grisage de champs : la remplacer l'aurait cachée derrière un clic, un
`aide-inline` **distinct** est donc ajouté EN PLUS (la notice reste
inchangée). **4 tests ajoutés/étendus** (présence + contenu du bloc sur les
3 écrans, notice M5 toujours présente). **Suite globale : 326 tests verts.**
Reste ouvert : le motif n'a été appliqué qu'à ces 3 écrans (portée de cette
session) ; d'autres écrans denses (ex. rondes/arbre de tournoi) pourraient en
bénéficier plus tard, au fil des retouches.

**M9 — Confirmations natives `confirm()` reformulées : FAIT.** Passage en
revue des 17 `confirm()` du dépôt (mécanisme natif conservé partout, jamais
remplacé par une modale JS). **5 réécrits** sur le patron « Action ? +
conséquence + porte de sortie », ceux qui énuméraient réellement des
détails techniques : restauration de sauvegarde (`admin_donnees.html`,
reprend **l'exemple exact de la fiche** — la liste des 3 bases entre
parenthèses disparaît) ; clôture des prêts (`admin_dashboard.html`, la
parenthèse « (L'historique et les statistiques sont conservés.) » devient
une clause naturelle) ; réinitialisation formation (`admin_dashboard.html`,
la liste `(jeux, prêts, tournoi)` et « Vider et repeupler » remplacés par le
libellé déjà utilisé sur le bouton) ; purge RGPD planning
(`planning_gerer.html`, `(RGPD)` et « DÉFINITIVEMENT » remplacés par
l'idiome **déjà existant** « Cette action est irréversible. », réutilisé
tel quel depuis `admin_rangement.html`) ; ouverture groupée des tournois du
jour (`tournoi_liste.html`, le nom d'état interne « en brouillon »
disparaît). **12 laissés tels quels** : déjà courts et sans jargon, ou déjà
accompagnés d'un texte de contexte visible à l'écran (suppression de
tournoi : la bannière « Cette action est irréversible. » est déjà affichée
avant le clic, le `confirm()` final reste volontairement minimal). **4
tests ajoutés/étendus** (nouveaux libellés vérifiés à l'écran + anciennes
formulations techniques absentes). **Suite globale : 323 tests verts.**

**Suivi de l'emplacement de rangement : FAIT** (phase 1 complète, conception
gravée dans `docs/conception-rangement.md`, tous les arbitrages de son §12
tranchés en amont). Objectif : savoir où ranger chaque boîte, à l'événement
comme au local hors événement, **sans jamais toucher à la logique métier du
prêt** (numéro de pochette, deux clés stables) et **sans donnée personnelle**.
**Deux contextes** interchangeables via un seul réglage global
(`services.rangement_contexte`/`ecrire_rangement_contexte`, table
`parametres`) : **Événement** (texte libre par exemplaire, colonne
`exemplaires.emplacement_evenement`) et **Local** (liste d'emplacements fixes
gérée en admin, FK `exemplaires.emplacement_local_id` →
`emplacements_rangement.id_emplacement`, table créée avec un **seed** de 5
emplacements par défaut, migrations idempotentes dans `app/db.py`). Les deux
valeurs coexistent indépendamment ; changer de contexte ne touche pas
l'autre. **Écran admin `/admin/rangement`** : bascule de contexte, réglage de
**visibilité publique** (tous / bénévoles par défaut / administrateurs,
`rangement_visibilite`), et **CRUD de la liste locale** (créer, renommer —
répercuté automatiquement sur toutes les boîtes —, archiver/réactiver,
réordonner, supprimer si plus aucune boîte rattachée). **Mode rangement au
scanner** (`/scanner`) : bandeau d'activation, cookie dédié côté appareil
(`rangement_actif`, même mécanique que `PRET_TOKEN`), chaque scan propose de
saisir/choisir l'emplacement au lieu d'enregistrer un prêt (`/scanner/ranger`),
saisie manuelle de secours intégrée, sortie de mode à tout moment. **Affichage
au retour** (`/pret/<id>`, `rendu`/`rendu_tournoi`) : l'emplacement s'affiche
en grand si renseigné, **toujours visible du bénévole quel que soit le
réglage de visibilité publique**. **Visibilité catalogue/fiche** :
`rangement_visible(request)` (global Jinja, imports locaux pour éviter tout
cycle) gouverne l'affichage sur `/jeu/<id>` selon le réglage — jamais
d'affichage d'une valeur vide (« non renseigné » proscrit, jamais bloquant).
**Édition à l'unité** sur la fiche admin d'un jeu (`/admin/jeu/<ref>`) : les
deux champs (événement/local) de chaque exemplaire sont **toujours
indépendamment éditables**, deux formulaires séparés, réutilisés ensuite via
un paramètre `retour` (validé pour n'accepter que les chemins internes
`/admin/*`, jamais de redirection ouverte) qui permet à d'autres pages
d'appeler les mêmes routes d'édition et de revenir sur elles-mêmes. **Import/
export CSV** : les deux colonnes rangement font partie de
`EN_TETES_CATALOGUE`/`lignes_export_catalogue`, ré-importables ; UPSERT en
**`COALESCE(excluded.x, table.x)`** (une case laissée vide n'efface jamais
une valeur déjà en base) ; un nom d'emplacement local inconnu à l'import est
**créé automatiquement** (résolution/création mise en cache par run
d'import). **Page des manques** (`/admin/rangement/manques`) : liste
filtrable (catégorie, recherche texte) et **paginée** (`PAR_PAGE_MANQUES`=50,
première pagination server-side du projet) des exemplaires sans emplacement
dans le contexte actif, avec **saisie rapide en ligne** réutilisant les
routes d'édition à l'unité via `retour` ; lien de comptage sur
`/admin/rangement` et sur `/admin/donnees` après un import CSV laissant des
boîtes non rangées. **Aide dédiée** : page publique `GET /rangement/aide`
(`rangement_aide.html`, mode d'emploi des deux contextes, du mode scanner, de
la visibilité et de la page des manques), liée depuis `/admin/rangement` et
`/apropos`. **294 tests verts** au total (dont l'essentiel de
`tests/test_rangement.py`, un fichier dédié couvrant schéma/migrations,
services, et routes pour chacune des 9 étapes).

**Rangement — addendum §13, affectation en lot par jeu : FAIT.** Amélioration
post-phase 1 (conception dans `docs/conception-rangement.md` §13), pour
équiper rapidement ~700 boîtes plutôt qu'une à une. **Remplace** l'ancienne
page des manques (grain exemplaire) par la vue **`/admin/rangement/ranger`**
(« Ranger les jeux »), au grain **TITRE** : une ligne par jeu (ex. « Catan —
3 boîtes »), emplacement courant du contexte actif affiché (libellé / `—` si
aucune boîte affectée / **« mixte »** si les copies diffèrent ou
l'affectation est partielle). Filtres **réutilisant tel quel**
`services.lister_catalogue()` (categorie/q/age/joueurs, même panneau que le
catalogue public — aucune logique de filtre réimplémentée) + interrupteur
« afficher aussi les jeux déjà rangés » (défaut : seulement ceux à ranger ;
« déjà rangé » = **toutes** les boîtes du titre partagent le même emplacement
non vide — décision d'implémentation prise faute de détail dans la conception
sur ce point précis). Bandeau **« Contexte actif »** en tête (§13.7), avec
accès rapide pour changer. **Sélection robuste** (§13.3) : le bouton
« Appliquer à X jeux » **rejoue le filtre côté serveur** (POST transporte les
critères, pas une liste d'ids) — couvre tout le résultat même multi-pages ;
des cases à cocher permettent en plus de restreindre aux jeux de la page
courante (« Appliquer aux jeux cochés »). JS inline minimal, repris tel quel
du motif déjà en place sur `/admin/etiquettes` (tout cocher/décocher +
compteur live). **Écrasement** annoncé sur le bouton (« dont N déjà rangés —
seront remplacés »), jamais silencieux ; case **« ne pas écraser »** pour ne
combler que les trous. **Emplacement vide refusé en lot** (pas de wipe de
masse — retrait toujours à l'unité, fiche admin). Nouveaux services :
`_resume_emplacement_titres`, `rangement_par_titre`, `affecter_emplacement_lot`
(UPDATE en lot en une requête, pas une boucle Python, adapté à ~700 boîtes).
`compter_exemplaires_sans_emplacement` et `_clause_sans_emplacement`
conservés (compteur de `/admin/rangement`, message post-import, option
« ne pas écraser »). **Correctif transverse** découvert en cours de route :
le limiteur de débit de connexion admin (`app.auth._tentatives`) est un
dictionnaire global au process, non remis à zéro entre tests — les
connexions cumulées sur toute la suite finissaient par dépasser le seuil et
faisaient échouer des tests sans rapport, plus loin dans l'ordre d'exécution ;
fixture autouse ajoutée dans `tests/conftest.py` (nouveau fichier) pour
réinitialiser ce compteur avant chaque test. **25 tests ajoutés** pour cet
addendum. **Suite globale : 319 tests verts.**

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
  `duree_min`, `age_min`, `editeur`, `auteur`, `annee_edition`, `descriptif`,
  `date_achat` — ISO, la + récente des exemplaires ; alimente
  `services.derniers_achats` → panneau « Dernières acquisitions » du catalogue).
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
    (`ludotex.service` systemd 1 worker + `--proxy-headers`,
    `nginx-ludotex.conf` reverse proxy + static, `sauvegarde.sh` SQLite `.backup`
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
(`GET /admin/etiquette/<id>.png`), **imprimer des étiquettes EN LOT**
(`/admin/etiquettes` : sélection de jeux cochables + filtre catégorie + tout/aucun ;
mise en page A4 réglable — 4 marges mm + colonnes×lignes, compteur live JS ;
`POST /admin/etiquettes/pdf` → PDF couleur via `etiquettes.planche_pdf`, qui
imprime toutes les boîtes des jeux choisis ; services `titres_pour_etiquettes` /
`exemplaires_pour_etiquettes`), **importer/exporter le catalogue**
(`/admin/donnees` : import d'un CSV téléversé via `scripts.import_csv.importer` ;
export CSV/Excel ré-importable via `services.lignes_export_catalogue` +
`exports.catalogue_csv`/`catalogue_xlsx`, en-têtes = `EN_TETES_CATALOGUE`),
changer le mot de passe. Le **dessin
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
