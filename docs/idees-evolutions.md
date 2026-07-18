# Idées d'évolutions — exploration libre

Brainstorm sans contrainte de faisabilité (session du 2026-07-15). À trier avec
le CA : certaines idées iront au backlog (`docs/ameliorations-a-prevoir.md`),
d'autres à la corbeille. Les chantiers **déjà actés** (double élimination,
e-mails, sauvegarde externe automatisée, notifications planning) ne sont pas
repris ici, sauf pour les prolonger.

Chaque idée : intitulé, **valeur** pour l'asso ou les bénévoles, et une note de
mise en œuvre quand elle coule de source.

> **Revue de pertinence du 2026-07-18.** Relecture de chaque fiche au regard du
> code réellement en place (module rangement, sessions UX Q/M/S, mode formation,
> supervision). Chaque idée porte désormais une ligne **Pertinence** :
> `✅ RÉALISÉ`, `↑ renforcée` (le code livré depuis a rapproché ou facilité
> l'idée), `= intacte` (rien n'a bougé de ce côté) ou `↓ réduite`.
> Bilan chiffré et reliquats : voir la synthèse en fin de document.

---

## 1. Expérience visiteur (catalogue public)

### 1.1 Photos des boîtes
- **Valeur** : un catalogue illustré est infiniment plus engageant, surtout pour
  choisir un jeu qu'on ne connaît pas. Le CSV d'origine avait un « Lien image »
  inutilisable (chemins Windows) — le besoin existe donc déjà côté asso.
- **Note** : téléversement par l'admin sur la fiche, ou récupération automatique
  via l'API BoardGameGeek (voir 1.2). Miniatures générées par Pillow (déjà là).
- **Pertinence = intacte.** Aucune colonne image, aucun téléversement de fichier
  nulle part dans le code. Seul changement de contexte : `/admin/fonctionnalites`
  permet désormais de livrer un tel ajout derrière un interrupteur de module.

### 1.2 Enrichissement automatique via BoardGameGeek
- **Valeur** : descriptions, images, note communautaire, complexité (« weight »),
  sans saisie manuelle sur ~600 titres. Gros gain de qualité du catalogue pour
  un effort humain quasi nul.
- **Note** : matching par nom (l'API XML de BGG est publique), avec écran admin
  de validation des correspondances douteuses. Champs BGG stockés à part pour ne
  jamais écraser la saisie locale.
- **Pertinence = intacte.** Rien de comparable livré. À noter : l'UPSERT de
  l'import CSV a entre-temps adopté `COALESCE(excluded.x, table.x)` (une valeur
  vide n'écrase jamais l'existant) — le patron « ne jamais écraser la saisie
  locale » est donc déjà éprouvé dans le dépôt et réutilisable tel quel.

### 1.3 « Que jouer maintenant ? » — assistant de choix
- **Valeur** : le visiteur type ne cherche pas un titre, il cherche « un jeu
  pour 5, ~30 min, enfants de 8 ans ». Trois questions → suggestions (dispo
  uniquement) + bouton « surprends-moi ». Réduit la sollicitation des bénévoles.
- **Note** : les filtres existent déjà ; c'est surtout une présentation guidée
  + un ORDER BY RANDOM(). Idéal aussi sur une tablette « borne » dans la salle.
- **Pertinence ↑ renforcée.** `lister_catalogue(categorie, q, age, joueurs)` a
  été réutilisée telle quelle par l'écran « Ranger les jeux » — elle a fait la
  preuve qu'on peut bâtir un écran entier par-dessus sans réimplémenter de
  filtre. L'assistant de choix devient une simple 3ᵉ façade sur le même service.

### 1.4 Jeux similaires sur la fiche
- **Valeur** : rebond quand le jeu convoité est sorti — « dans la même veine,
  disponibles maintenant : … ». Transforme une frustration en découverte.
- **Note** : similarité simple par catégorie/âge/durée ; BGG (1.2) l'affinerait.
- **Pertinence = intacte.** Rien livré de ce côté sur la fiche.

### 1.5 File d'attente anonyme sur un jeu sorti
- **Valeur** : « prévenez-moi quand il revient » sans donnée perso : le visiteur
  laisse un pseudo, l'écran salle `/live` affiche « *Kaamelott* est de retour —
  demandé par Arthur ». Fidélise sans rien stocker de personnel.
- **Note** : table `attentes(id_exemplaire, pseudo, cree_le)`, purgée au retour
  ou à la clôture. Zéro RGPD, cohérent avec la philosophie du prêt.
- **Pertinence = intacte.** Prérequis toujours réunis (écran `/live` en place,
  motif « pseudo + code » éprouvé par les inscriptions tournoi). L'écran de
  retour `/pret/<id>` a en revanche gagné du contenu depuis (emplacement de
  rangement en grand, bouton « Scanner le jeu suivant ») : penser la place d'un
  éventuel bandeau « ce jeu était attendu par X » en conséquence.

### 1.6 Avis éclair au retour
- **Valeur** : au moment du retour, le bénévole demande « ça vous a plu ? » et
  tape 👍/👎 (ou 1–5). Sur une édition, cela produit une carte de satisfaction du
  parc : quoi racheter, quoi désherber (cf. 6.1). Donnée précieuse, coût de
  collecte quasi nul.
- **Note** : colonne `prets.avis` nullable + deux boutons sur l'écran de retour.
  Optionnel et jamais bloquant, comme le reste.
- **Pertinence ↓ légèrement réduite (à arbitrer).** L'idée reste bonne, mais
  l'écran de retour est devenu le point de convergence de tout ce qu'on ajoute
  (emplacement de rangement en 5 rem, bouton « Scanner le jeu suivant » pleine
  largeur ajouté en Q4 précisément pour accélérer le geste répété). Y greffer une
  question supplémentaire va à l'encontre du travail de fluidification récent.
  Si retenu : deux boutons discrets **après** le bouton de scan suivant, jamais
  entre le résultat et lui.

### 1.7 Localisation physique (zone / étagère) — ✅ RÉALISÉ (et dépassé)
- **Valeur** : avec ~700 boîtes, retrouver un jeu précis est un vrai temps de
  bénévole. Champ « emplacement rayon » affiché sur la fiche et l'étiquette
  (le code de classement `EAM8-3-5-15` y fait déjà allusion).
- **Note** : colonne nullable sur `exemplaires`, import CSV, filtre catalogue.
- **Fait** : livré bien au-delà de la fiche d'origine, cadré dans
  `docs/conception-rangement.md` (phase 1 + addendum §13). **Deux contextes**
  interchangeables par un réglage global — Événement (texte libre par
  exemplaire, `exemplaires.emplacement_evenement`) et Local (liste
  d'emplacements fixes gérée en admin, FK vers `emplacements_rangement`) — qui
  coexistent sans s'écraser. Écran `/admin/rangement` (bascule de contexte,
  CRUD de la liste locale, réglage de **visibilité publique** tous/bénévoles/
  admins), **mode rangement au scanner** (cookie dédié, chaque scan propose de
  saisir l'emplacement au lieu d'enregistrer un prêt), **affichage en grand au
  retour** sur `/pret/<id>`, édition à l'unité sur la fiche admin,
  import/export CSV (UPSERT `COALESCE`, création automatique d'un emplacement
  local inconnu), et **affectation en lot par titre** (`/admin/rangement/ranger`,
  filtre rejoué côté serveur pour couvrir tout le résultat multi-pages). Aide
  dédiée `/rangement/aide`. Voir CLAUDE.md (`tests/test_rangement.py`).
- **Reliquats de la fiche d'origine, NON faits** (à trancher, mineurs) :
  (a) l'emplacement **n'apparaît pas sur l'étiquette** (`app/etiquettes.py` ne
  connaît pas les colonnes de rangement) — discutable de toute façon, puisque
  l'emplacement change d'une édition à l'autre alors que l'étiquette est
  imprimée une fois pour toutes ; (b) **pas de filtre catalogue par
  emplacement** — l'affichage existe sur `/jeu/<id>` (soumis au réglage de
  visibilité) mais on ne peut pas lister « tous les jeux de l'étagère B ».

### 1.8 Version anglaise minimale du public
- **Valeur** : la Manche est touristique en été ; catalogue et pages tournois en
  EN élargissent l'audience. Les écrans bénévole/admin restent FR.
- **Note** : gros chantier si i18n complète — une alternative pragmatique est
  une page « How it works » statique + libellés clés bilingues.
- **Pertinence ↓ réduite (coût en hausse).** Aucune i18n amorcée, et le nombre
  de gabarits et de libellés en dur a nettement augmenté depuis (rangement,
  planning, aides contextuelles, pages d'aide). L'alternative pragmatique
  (page statique « How it works ») reste la seule raisonnable ; une i18n
  complète est désormais franchement hors de proportion.

## 2. Côté bénévoles au prêt

### 2.1 Mode dégradé hors-ligne (PWA renforcée)
- **Valeur** : le wifi de salle est le maillon faible. Si le réseau tombe 10
  minutes en pleine pointe, aujourd'hui tout s'arrête. Une file locale
  d'actions (prêts/retours en attente, resynchronisés au retour du réseau)
  éliminerait le pire scénario de l'événement.
- **Note** : service worker + IndexedDB ; attention aux conflits de numéros de
  pochette (attribution à la resynchro, pas au scan). C'est LE chantier
  technique ambitieux mais à plus forte valeur assurantielle.
- **Pertinence = intacte, mais point de départ à corriger.** Vérification faite
  ce jour : **il n'y a en réalité aucune PWA** — ni `manifest.webmanifest`, ni
  service worker dans `app/static/` (seuls `scanner.js` et `jsQR.js` y sont).
  La mention « PWA » de CLAUDE.md et de la spec décrit une intention, pas du
  code. Le chantier ne part donc pas d'une « PWA à renforcer » mais de zéro :
  manifeste + service worker + file d'actions, à chiffrer en conséquence.
  La valeur assurantielle, elle, est inchangée — c'est toujours le seul vrai
  point de défaillance du dispositif.

### 2.2 Signalement d'état au retour
- **Valeur** : « il manque un dé », « boîte déchirée » — aujourd'hui cette info
  se perd oralement. Un bouton « signaler un problème » au retour alimente une
  liste de maintenance consultable par l'admin (cf. 6.2).
- **Note** : table `signalements(id_exemplaire, texte, cree_le, traite)` ;
  pastille « ⚠ signalement en cours » sur la fiche.
- **Pertinence ↑ renforcée.** Aucun code livré, mais deux patrons directement
  réutilisables sont apparus depuis : la pastille `.badge` (déjà déclinée en
  `.badge-ok`/`.badge-attention` pour la supervision) et surtout le CRUD admin
  complet de `emplacements_rangement`, qui donne le squelette exact d'une
  petite table administrative annexe (liste, création, état, archivage).
  Même remarque qu'en 1.6 sur l'encombrement de l'écran de retour.

### 2.3 Statut d'exemplaire « retiré / en réparation »
- **Valeur** : un jeu incomplet ne doit plus être proposé sans être « sorti ».
  Aujourd'hui l'état est binaire (dispo/sorti). Un statut administratif le
  masquerait du catalogue proprement.
- **Note** : prolonge 2.2 ; l'état déduit reste la règle, ce statut est un
  drapeau administratif par-dessus.
- **Pertinence = intacte.** L'état reste strictement binaire et déduit. Le
  principe « drapeau administratif par-dessus l'état déduit » a toutefois été
  validé ailleurs entre-temps (colonne `actif` des emplacements, interrupteurs
  de `/admin/fonctionnalites`) : l'approche ne heurte plus aucune règle du
  projet.

### 2.4 Saisie manuelle de secours sur le scanner — ✅ RÉALISÉ
- **Valeur** : étiquette arrachée, QR illisible, caméra capricieuse : un champ
  « tapez le code de la boîte » sur `/scanner` évite l'aller-retour catalogue.
- **Note** : trivial — formulaire GET vers `/pret/<id>`. Quick win.
- **Fait** : formulaire GET sous la caméra → `GET /scanner/saisie?code=…`
  (`routes/scanner.py`), protégé par `exiger_jeton`, sans JS ajouté. Code inconnu
  → message clair + champ prérempli (jamais bloquant) ; zéros de tête préservés
  (TEXT). 4 tests dédiés. Voir CLAUDE.md.

### 2.5 Mode inventaire par scan
- **Valeur** : avant/après l'événement, scanner les boîtes en rayon et obtenir
  l'écart avec la base (manquants, non répertoriés). Remplace un inventaire
  papier d'une journée.
- **Note** : réutilise le scanner ; une table de session d'inventaire + rapport.
- **Pertinence ↑↑ fortement renforcée — meilleur rapport valeur/coût du
  document aujourd'hui.** Le **mode rangement au scanner**, livré depuis,
  implémente déjà toute la mécanique difficile : un mode d'appareil mémorisé
  par cookie dédié (`rangement_actif`, sur le patron de `PRET_TOKEN`), un
  bandeau d'activation/sortie de mode, et un scan qui **déclenche une action
  alternative au prêt** au lieu d'enregistrer une sortie — plus la saisie
  manuelle de secours (2.4) intégrée au flux. Un mode inventaire serait un
  troisième mode bâti sur le même squelette : il ne reste réellement à écrire
  que la table de session et le rapport d'écart.

## 3. Tournois

### 3.1 Appariements de la ronde courante sur l'écran salle
- **Valeur** : au lancement d'une ronde, les joueurs cherchent leur table.
  Afficher « Ronde 2 : Alice–Bob (table 3)… » sur `/live` évite au bénévole de
  crier les noms. Prolongement naturel de l'écran salle existant.
- **Note** : les données sont déjà dans `rencontres` ; ajouter un numéro de
  table serait le seul vrai ajout.
- **Pertinence ↑ renforcée.** Deux modes de scoring supplémentaires ont été
  livrés depuis la rédaction (round robin, et l'affichage des rondes est
  partagé avec le suisse) : l'écran salle a donc plus de matière à afficher
  qu'à l'époque. `/live/data` reste le bon point d'entrée. À coupler avec 5.1
  (rotation de slides) sous peine de surcharger un écran déjà dense.

### 3.2 Lien tournoi ↔ catalogue
- **Valeur** : répond au point §11 resté ouvert (« jeu en texte libre ? »).
  Choisir le jeu depuis le catalogue permettrait en plus de **sortir
  automatiquement l'exemplaire** (motif tournoi) au lancement et de le rendre à
  la clôture — deux modules qui se parlent enfin.
- **Note** : champ `reference_titre` nullable côté tournoi ; le texte libre
  reste possible (jeux apportés par des joueurs).
- **Pertinence ↑ renforcée.** `tournois.jeu` est toujours un `TEXT` libre
  (vérifié : `app/tournoi/models.py`), le point §11 reste donc ouvert. Mais
  l'automatisation entrevue devient plus intéressante : la sortie « motif
  tournoi » existe, et **le rangement s'y ajoute** — sortir automatiquement
  l'exemplaire au lancement permettrait aussi de rappeler au bénévole où le
  reposer à la clôture. Attention en revanche : c'est le seul point du
  document qui fasse **écrire une base dans l'autre** (tournoi → prêt, bases
  SQLite séparées et volontairement indépendantes) — le vrai sujet de
  conception est là, pas dans le champ nullable.

### 3.3 Liste d'attente d'inscription
- **Valeur** : tournoi complet ≠ joueur perdu. File d'attente avec promotion
  automatique en cas de désinscription ; le pseudo suivant s'affiche sur le
  suivi public.
- **Note** : même mécanique pseudo + code que l'inscription.
- **Pertinence = intacte.** À noter : les tournois **par équipes** livrés
  depuis compliquent un peu la promotion automatique (promouvoir une équipe
  suppose de vérifier qu'elle a bien `taille_equipe` membres). Prévoir le cas
  dès la conception plutôt que de le découvrir à l'usage.

### 3.4 Round robin (championnat toutes rondes) — ✅ RÉALISÉ
- **Valeur** : pour 4–6 joueurs, c'est le format le plus juste et le plus
  convivial ; la ronde suisse est taillée pour de plus gros effectifs.
- **Note** : la table `rencontres` absorbe déjà ce mode ; génération = produit
  cartésien ordonné (algorithme des cercles).
- **Fait** : `"round_robin"` ajouté à `MODES_SCORING`, génération de toutes les
  rondes d'emblée par la méthode des cercles (`_generer_round_robin`, joueur
  fantôme si effectif impair), classement délégué à `classement_suisse`, saisie
  via l'écran des rondes existant, tableau croisé des confrontations sur la page
  publique. Voir CLAUDE.md (156 tests verts).

### 3.5 Tournois par équipes — ✅ RÉALISÉ
- **Valeur** : beaucoup de jeux d'ambiance se jouent en équipes ; inscription
  « nom d'équipe + pseudos » ouvrirait ces formats.
- **Note** : phase lointaine — touche inscription, appariement et affichage.
- **Fait** : principe « une équipe = un participant » (compatible avec les 4
  modes sans toucher aux appariements), inscription avec nombre exact de
  membres, code de désinscription par équipe, membres visibles seulement côté
  bénévole (affichage public = nom d'équipe seul). Voir CLAUDE.md (164 tests
  verts).

### 3.6 Palmarès & diplômes
- **Valeur** : un PDF « diplôme » (vainqueur, jeu, date, logo) imprimé en fin de
  tournoi coûte trois clics et fait un souvenir apprécié, surtout des enfants.
  Et une page « palmarès des éditions » valorise l'événement dans la durée.
- **Note** : reportlab et le logo sont déjà là ; le palmarès découle de
  `vainqueur()` + archivage des tournois `termine`.
- **Pertinence ↑ renforcée.** `vainqueur()` existe pour l'élimination directe,
  et les classements sont désormais disponibles pour les **quatre** modes
  (high score, suisse, round robin, élimination) — un diplôme peut donc être
  produit quel que soit le format, ce qui n'était pas acquis à la rédaction.
  `NOM_ASSOCIATION` étant devenu configurable, le diplôme sera correct sur
  n'importe quel déploiement sans retouche.

## 4. Planning bénévoles

### 4.1 « Mon planning » en .ics — ✅ RÉALISÉ
- **Valeur** : le bénévole ajoute ses créneaux à son agenda en un tap, comme
  pour les tournois. Réduit les oublis, zéro e-mail nécessaire.
- **Note** : quick win — `ical_tournoi` existe déjà, à généraliser (VEVENT
  multiples), route `/planning/mon.ics?code=`.
- **Fait** : `ical_planning_benevole` (`app/planning/services.py`), sur le
  patron d'`ical_tournoi` mais en **multi-VEVENT** (un par affectation, poste
  ou tâche) ; helpers `_ics_horodatage`/`_ics_echappe` dupliqués localement
  (modules `tournoi`/`planning` indépendants). Route publique
  `GET /planning/mon.ics?code=` (même lookup que `/planning/mon`, 404 si code
  invalide ou aucune affectation), bouton « Ajouter tout mon planning à mon
  agenda » sur `planning_mon.html`. Aucune donnée personnelle dans le fichier.
  Voir CLAUDE.md (182 tests verts).

### 4.2 Pointage du jour J
- **Valeur** : « qui est arrivé ? » — cocher les présences sur la grille et
  voir en rouge les trous *réels* (affecté mais absent). C'est le stress
  principal du bureau le matin de l'événement.
- **Note** : drapeau `present` sur `affectations` + couleur d'état dédiée
  (la mécanique `pl-etat-*` existe).
- **Pertinence ↑ renforcée.** La grille admin est passée entre-temps à
  l'**édition « au clic » rendue côté serveur** (page de case dédiée par
  POST classiques, sans JS lourd) et les couleurs `pl-etat-*` ont gagné une
  légende (S4). Cocher une présence s'insère dans un mécanisme déjà en place —
  une action de plus sur la page de case, une teinte de plus dans la légende.

### 4.3 Échange de créneaux entre bénévoles
- **Valeur** : « je ne peux plus samedi matin » se règle aujourd'hui par
  téléphone au bureau. Une demande d'échange (via `code_modif`) validée en un
  clic par l'admin fluidifierait sans perdre le contrôle.
- **Note** : table de demandes + notification sur l'écran de gestion.
- **Pertinence = intacte.** `code_modif` et `remplacer_affectation` (livrée
  depuis, avec sa vérification de conflit « une personne par créneau ») sont
  les deux briques nécessaires ; il ne manque que la table de demandes et son
  point d'entrée sur l'écran de gestion.

### 4.4 Bilan d'engagement annuel
- **Valeur** : total d'heures par bénévole sur l'année → remerciements ciblés à
  l'AG, justificatifs de bénévolat (certaines demandes de subvention les
  valorisent), détection des piliers à ménager.
- **Note** : simple agrégation sur `affectations` multi-événements + export.
- **Pertinence = intacte, mais dépendance à clarifier.** Le calcul d'heures par
  bénévole existe déjà **en interne** (le préremplissage l'utilise pour son
  arbitrage d'équité et son plafond `max_heures`) : l'agrégation n'est pas à
  inventer, seulement à exposer et à cumuler sur plusieurs événements. Point
  d'attention RGPD : c'est la seule idée qui suppose de **conserver** les
  données planning d'une année sur l'autre, alors que `purger_evenement` a
  précisément été écrite pour permettre de les effacer. Arbitrage CA.

## 5. Écran salle & communication

### 5.1 Écran salle en « slides » tournantes
- **Valeur** : `/live` affiche tout en même temps. Une rotation (stats → 
  tournois → planning des animations → annonce) rend l'écran lisible de loin et
  hiérarchise l'information au fil de la journée.
- **Note** : pur JS côté page, l'endpoint `/live/data` suffit presque déjà.
- **Pertinence ↑ renforcée.** Confirmé après relecture de `routes/live.py` :
  `_collecter_donnees()` renvoie déjà toutes les rubriques en un seul JSON et
  la page fait du polling toutes les 10 s — la rotation est **purement une
  affaire de présentation côté page**, aucun changement serveur. C'est le
  préalable naturel de 3.1 et 5.2, qui veulent tous deux ajouter du contenu à
  un écran déjà chargé.

### 5.2 Annonces libres pilotées par l'admin — ✅ RÉALISÉ (et un peu dépassé)
- **Valeur** : « Tombola à 15 h », « portefeuille trouvé à l'accueil » — un
  champ texte en admin, affiché en bandeau sur `/live`. L'écran devient l'outil
  de communication central de la salle.
- **Note** : clé `parametres` + slide dédiée (5.1). Quick win.
- **Fait** : seconde clé `live_annonce` sur le patron du titre de l'écran
  (`live.annonce_active()`, exposée par `_collecter_donnees()` donc par
  `/live` et `/live/data`, absente du JSON quand il n'y en a pas). Champ +
  bouton « Effacer l'annonce » ajoutés sur l'écran admin existant
  (`/admin/ecran-salle`, aucune nouvelle page). Bandeau conditionnel sur
  `/live`, apparition/disparition sans rechargement, texte injecté via
  `textContent` (jamais `innerHTML`). **Ajout en cours de route, non prévu par
  cette fiche** : une **durée d'affichage optionnelle en minutes** — passé ce
  délai, l'annonce s'auto-masque (calcul à la lecture, rien n'est purgé en
  base : elle reste éditable/rappelable en admin) ; vide = illimité comme
  imaginé à l'origine. **Rappel dans la carte Supervision** du tableau de bord
  quand une annonce est active, pour qu'elle ne reste jamais affichée toute la
  journée sans que le bureau ne la voie. **8 tests dédiés.** Voir CLAUDE.md
  (334 tests verts).

### 5.3 Widget pour le site vitrine WordPress
- **Valeur** : le site vitrine (brique séparée) pourrait afficher en direct
  « 542 jeux disponibles — 3 tournois aujourd'hui » via un mini endpoint JSON
  public + un embed. Fait vivre le site pendant l'événement, draine du public.
- **Note** : `/live/data` existe ; ajouter CORS restreint + un iframe stylé.
- **Pertinence = intacte.** `/live/data` est bien public et sans donnée
  personnelle (le n° de pochette en a été délibérément exclu) — l'endpoint est
  donc exposable tel quel. Reste le CORS restreint et l'embed. Seule réserve,
  inchangée : cela dépend du choix d'hébergement, encore ouvert
  (`docs/etude-hebergement-brief.md`).

### 5.4 Flux iCal public de l'événement
- **Valeur** : tout le programme des tournois en un seul abonnement agenda
  (`/tournois/agenda.ics`), publiable sur le site et les réseaux avant
  l'événement.
- **Note** : généralisation directe de `ical_tournoi` (multi-VEVENT).
- **Pertinence ↑ renforcée — le patron a été écrit depuis.** Le multi-VEVENT
  n'est plus à inventer : `ical_planning_benevole` (4.1) le fait déjà, avec ses
  helpers d'horodatage et d'échappement RFC 5545. Un `/tournois/agenda.ics`
  serait la **troisième** occurrence du même motif — c'est le moment de juger
  si les helpers `_ics_*` méritent enfin d'être factorisés, la décision de les
  dupliquer ayant été prise à deux occurrences seulement.

## 6. Gestion associative & pilotage

### 6.1 Aide au désherbage et aux achats
- **Valeur** : croiser prêts par titre sur plusieurs éditions + avis (1.6) →
  « jamais sorti en 3 ans » (candidats don/vente) et « toujours en rupture »
  (candidats rachat d'exemplaires). Décisions d'achat argumentées devant le CA.
- **Note** : l'historique n'est jamais purgé — la donnée est déjà là. Un écran
  « vie du parc » avec préréglages de période par édition suffirait.
- **Pertinence = intacte, dépendance allégée.** Reste subordonnée à 6.4
  (éditions), non faite. En revanche la partie « avis » (1.6) n'est PAS un
  prérequis : le croisement prêts×titre×période suffit déjà à produire les deux
  listes utiles (« jamais sorti » / « toujours en rupture »), `palmares` incluant
  déjà les zéros par LEFT JOIN. À découpler pour ne pas bloquer 6.1 sur 1.6.

### 6.2 Carnet de maintenance du parc
- **Valeur** : réceptacle des signalements (2.2), suivi « à réparer / réparé /
  retiré », pièces à racheter. Le parc est le principal actif de l'asso ;
  aujourd'hui son entretien ne laisse aucune trace.
- **Pertinence = intacte.** Indissociable de 2.2 (dont elle est la face admin) ;
  à instruire comme un seul chantier. Même remarque qu'en 2.2 : le CRUD des
  emplacements de rangement fournit désormais le squelette d'écran.

### 6.3 Rapport d'édition auto-généré
- **Valeur** : après clôture, un PDF « Bilan de l'édition 2026 » (fréquentation
  du prêt, top jeux, tournois et vainqueurs, heures de bénévolat) prêt pour
  l'AG et les dossiers de subvention. Les exports existent en pièces détachées ;
  les assembler ferait gagner une soirée au bureau chaque année.
- **Note** : compose `collecter_stats` + données tournois + planning dans un
  seul document reportlab.
- **Pertinence ↑ renforcée.** Les « pièces détachées » à assembler sont plus
  nombreuses et plus mûres qu'à la rédaction : `collecter_stats` +
  `construire_pdf` à sections cochables côté prêt, exports Excel/PDF côté
  planning (`app/planning/exports.py`), classements et vainqueurs pour les
  quatre modes de tournoi. Le rapport d'édition est de plus en plus un travail
  d'**assemblage** et de moins en moins de production de données.

### 6.4 Notion d'« édition » de l'événement
- **Valeur** : socle des points 6.1/6.3 et de comparaisons année par année
  (« +12 % de prêts vs 2025 ») sans manipuler des filtres de dates à la main.
- **Note** : la clé `evenement_date` existe ; il s'agirait d'en faire une
  petite table `editions` (nom, début, fin) référencée par les stats.
- **Pertinence ↑ renforcée — devient le prérequis structurant n°1.** Toujours
  aucune table `editions`, et le besoin s'est étendu au-delà des seules stats :
  le module **rangement** a introduit un contexte « Événement » dont les
  emplacements n'ont de sens que pour une édition donnée (aujourd'hui écrasés
  d'une année sur l'autre, sans mémoire) ; le **planning** a ses propres
  `evenements`, dans une base séparée. Trois modules manipulent désormais la
  notion d'édition, chacun à sa façon. Instruire 6.4 avant 6.1 et 6.3, et en
  profiter pour trancher si la notion reste locale à chaque module ou devient
  commune.

### 6.5 Prêts longue durée aux adhérents
- **Valeur** : faire vivre la ludothèque *entre* les événements (déjà cadré
  dans `docs/evolution-prets-longue-duree.md`). Change la nature du service —
  et la donne RGPD — donc décision CA avant tout.
- **Pertinence = intacte, et précédent RGPD désormais disponible.** Le module
  planning a déjà acté une **rupture assumée** avec le « zéro donnée perso »
  (noms + contacts, base séparée à finalité unique, `purger_evenement`) : le
  débat CA ne part plus de zéro, un patron d'isolement existe et fonctionne.
  Le contexte « Local » du rangement va par ailleurs dans le même sens — faire
  vivre la ludothèque entre les événements. La décision reste politique.

## 7. Technique & robustesse

### 7.1 Mode bac à sable / formation — RÉALISÉ
- **Valeur** : former les nouveaux bénévoles la semaine avant, sans polluer la
  vraie base. Un « mode démo » (bases jetables, bandeau visible) comme celui
  qui existe déjà pour le planning.
- **Note** : la démo planning donne le patron ; généraliser au prêt (jeux
  fictifs + QR d'entraînement imprimables).
- **Fait** : SECONDE INSTANCE (même code, sous-domaine dédié, bases jetables,
  `MODE_FORMATION=1`), pas de routage dynamique de connexion. Voir CLAUDE.md et
  `docs/mode-formation.md`.

### 7.2 Supervision légère en admin — RÉALISÉ
- **Valeur** : le jour J, savoir en 5 s que tout va bien : taille et date de
  dernière sauvegarde des 3 bases, espace disque, version déployée, état du
  jeton. Rassure un bureau non technique.
- **Note** : page admin en lecture seule, stdlib uniquement. Voir CLAUDE.md.

### 7.3 Test de charge avant l'événement
- **Valeur** : ~700 jeux, des dizaines de scans/minute en pointe : vérifier une
  fois que SQLite/WAL + 1 worker uvicorn tiennent, plutôt que le découvrir en
  salle. Ajuster `busy_timeout` si besoin.
- **Note** : script locust/hey rejouant des scénarios prêter/rendre.
- **Pertinence ↑ renforcée, et l'urgence a changé de camp.** La suite de tests
  est passée d'une centaine à ~326 tests, mais ce sont **exclusivement des
  tests fonctionnels** : rien ne couvre la concurrence ni la charge. Or
  l'application écrit maintenant dans **trois** bases SQLite (prêt, tournois,
  planning) et le déploiement VPS est le dernier vrai jalon avant l'événement.
  Un incident de verrouillage se découvrirait en salle. À planifier avec la
  mise en production, pas après.

### 7.4 Accessibilité — 🟡 PARTIELLEMENT RÉALISÉ
- **Valeur** : public familial et intergénérationnel → contrastes, tailles de
  police, navigation clavier (déjà amorcée avec `:focus-visible`), labels ARIA
  sur les formulaires publics. Peu coûteux, image très positive pour une asso.
- **Fait depuis** (sessions UX, sans que la fiche ait été relue) : **contrastes**
  corrigés (Q6 — `#9aa0a6`, sous le seuil, remplacé par `#6b7075` sur les 4
  règles concernées) ; `aria-live="polite"` sur le statut du scanner (Q8) ;
  libellés déjargonnés (Q7) et confirmations reformulées (M9) — bénéfice direct
  de charge cognitive ; `prefers-reduced-motion` respecté sur toutes les
  animations ajoutées ; lisibilité mobile (débordements de tableaux, menu du
  bandeau replié, `.contenu-large`) ; favicon recadré lisible en 32×32 (Q11).
- **Reste à faire** : **labels ARIA sur les formulaires publics** — c'est le
  point le moins avancé, seuls `catalogue.html` et `scanner.html` contiennent
  un attribut `aria-*` (une occurrence chacun) ; **tailles de police** (aucune
  revue systématique) ; et aucun **audit outillé** n'a jamais été passé, les
  corrections ci-dessus étant venues d'observations ponctuelles. Une passe
  Lighthouse/axe donnerait enfin une liste fermée plutôt qu'un flux d'idées.

---

## Synthèse — par où commencer ?

_(Synthèse d'origine conservée plus bas, telle qu'écrite le 2026-07-15.)_

### Synthèse révisée (2026-07-18)

**Bilan : 9 idées sur 36 ont avancé** depuis la rédaction, dont **8 entièrement
livrées** — 1.7 (rangement, largement au-delà de la fiche), 2.4 (saisie
manuelle scanner), 3.4 (round robin), 3.5 (tournois par équipes), 4.1 (.ics
planning), 5.2 (annonces libres sur `/live`, avec en plus une durée
d'affichage auto-masquante non prévue à l'origine), 7.1 (mode formation),
7.2 (supervision) — et **1 partiellement**, 7.4 (accessibilité : contrastes et
lisibilité faits, labels ARIA non).

**Quick wins restants**, par ordre de rapport valeur/coût :

1. **Flux iCal tournois (5.4)** — le multi-VEVENT est écrit deux fois ; c'est
   de la reprise de patron, pas de la conception.
2. **Diplômes (3.6)** — reportlab, logo et classements des 4 modes sont là.

**Le meilleur candidat non-quick-win** : le **mode inventaire par scan (2.5)**,
dont le mode rangement au scanner a livré, sans le viser, toute la mécanique
difficile (mode d'appareil par cookie, scan → action alternative au prêt).
Reste la table de session et le rapport d'écart. Valeur métier forte (remplace
un inventaire papier d'une journée), coût désormais modeste.

**Prérequis structurant à instruire en premier** : la **notion d'édition
(6.4)**. Elle conditionne 6.1 et 6.3 comme prévu, mais trois modules
manipulent maintenant cette notion chacun à sa façon (stats par dates,
`evenements` du planning, contexte « Événement » du rangement sans mémoire
d'une année sur l'autre). Plus on attend, plus la convergence coûte.

**À planifier avec la mise en production, pas après** : le **test de charge
(7.3)**. ~326 tests fonctionnels, zéro test de concurrence, trois bases
SQLite en écriture, et le déploiement VPS comme dernier jalon.

**Point à corriger dans nos propres documents** : le **mode hors-ligne (2.1)**
ne part pas d'une « PWA à renforcer ». Il n'y a aucune PWA — ni manifeste, ni
service worker. La mention de CLAUDE.md et de la spec décrit une intention.
Le chiffrage du chantier doit en tenir compte ; sa valeur assurantielle, elle,
reste la plus élevée du document.

---

### Synthèse d'origine (2026-07-15, non révisée)

**Quick wins** (petits, forte valeur) : saisie manuelle scanner (2.4), annonces
sur `/live` (5.2), .ics planning (4.1) et flux iCal tournois (5.4), avis éclair
au retour (1.6), diplômes (3.6).

**Chantiers structurants** (à valider en CA) : lien tournoi↔catalogue (3.2),
notion d'édition + rapport d'édition (6.4, 6.3), enrichissement BGG + photos
(1.2, 1.1), signalements + maintenance (2.2, 6.2).

**Ambitieux mais assurantiel** : mode hors-ligne (2.1) — le seul vrai point de
défaillance du dispositif actuel.
