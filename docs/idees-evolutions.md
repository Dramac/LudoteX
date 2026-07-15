# Idées d'évolutions — exploration libre

Brainstorm sans contrainte de faisabilité (session du 2026-07-15). À trier avec
le CA : certaines idées iront au backlog (`docs/ameliorations-a-prevoir.md`),
d'autres à la corbeille. Les chantiers **déjà actés** (double élimination,
e-mails, sauvegarde externe automatisée, notifications planning) ne sont pas
repris ici, sauf pour les prolonger.

Chaque idée : intitulé, **valeur** pour l'asso ou les bénévoles, et une note de
mise en œuvre quand elle coule de source.

---

## 1. Expérience visiteur (catalogue public)

### 1.1 Photos des boîtes
- **Valeur** : un catalogue illustré est infiniment plus engageant, surtout pour
  choisir un jeu qu'on ne connaît pas. Le CSV d'origine avait un « Lien image »
  inutilisable (chemins Windows) — le besoin existe donc déjà côté asso.
- **Note** : téléversement par l'admin sur la fiche, ou récupération automatique
  via l'API BoardGameGeek (voir 1.2). Miniatures générées par Pillow (déjà là).

### 1.2 Enrichissement automatique via BoardGameGeek
- **Valeur** : descriptions, images, note communautaire, complexité (« weight »),
  sans saisie manuelle sur ~600 titres. Gros gain de qualité du catalogue pour
  un effort humain quasi nul.
- **Note** : matching par nom (l'API XML de BGG est publique), avec écran admin
  de validation des correspondances douteuses. Champs BGG stockés à part pour ne
  jamais écraser la saisie locale.

### 1.3 « Que jouer maintenant ? » — assistant de choix
- **Valeur** : le visiteur type ne cherche pas un titre, il cherche « un jeu
  pour 5, ~30 min, enfants de 8 ans ». Trois questions → suggestions (dispo
  uniquement) + bouton « surprends-moi ». Réduit la sollicitation des bénévoles.
- **Note** : les filtres existent déjà ; c'est surtout une présentation guidée
  + un ORDER BY RANDOM(). Idéal aussi sur une tablette « borne » dans la salle.

### 1.4 Jeux similaires sur la fiche
- **Valeur** : rebond quand le jeu convoité est sorti — « dans la même veine,
  disponibles maintenant : … ». Transforme une frustration en découverte.
- **Note** : similarité simple par catégorie/âge/durée ; BGG (1.2) l'affinerait.

### 1.5 File d'attente anonyme sur un jeu sorti
- **Valeur** : « prévenez-moi quand il revient » sans donnée perso : le visiteur
  laisse un pseudo, l'écran salle `/live` affiche « *Kaamelott* est de retour —
  demandé par Arthur ». Fidélise sans rien stocker de personnel.
- **Note** : table `attentes(id_exemplaire, pseudo, cree_le)`, purgée au retour
  ou à la clôture. Zéro RGPD, cohérent avec la philosophie du prêt.

### 1.6 Avis éclair au retour
- **Valeur** : au moment du retour, le bénévole demande « ça vous a plu ? » et
  tape 👍/👎 (ou 1–5). Sur une édition, cela produit une carte de satisfaction du
  parc : quoi racheter, quoi désherber (cf. 6.1). Donnée précieuse, coût de
  collecte quasi nul.
- **Note** : colonne `prets.avis` nullable + deux boutons sur l'écran de retour.
  Optionnel et jamais bloquant, comme le reste.

### 1.7 Localisation physique (zone / étagère)
- **Valeur** : avec ~700 boîtes, retrouver un jeu précis est un vrai temps de
  bénévole. Champ « emplacement rayon » affiché sur la fiche et l'étiquette
  (le code de classement `EAM8-3-5-15` y fait déjà allusion).
- **Note** : colonne nullable sur `exemplaires`, import CSV, filtre catalogue.

### 1.8 Version anglaise minimale du public
- **Valeur** : la Manche est touristique en été ; catalogue et pages tournois en
  EN élargissent l'audience. Les écrans bénévole/admin restent FR.
- **Note** : gros chantier si i18n complète — une alternative pragmatique est
  une page « How it works » statique + libellés clés bilingues.

## 2. Côté bénévoles au prêt

### 2.1 Mode dégradé hors-ligne (PWA renforcée)
- **Valeur** : le wifi de salle est le maillon faible. Si le réseau tombe 10
  minutes en pleine pointe, aujourd'hui tout s'arrête. Une file locale
  d'actions (prêts/retours en attente, resynchronisés au retour du réseau)
  éliminerait le pire scénario de l'événement.
- **Note** : service worker + IndexedDB ; attention aux conflits de numéros de
  pochette (attribution à la resynchro, pas au scan). C'est LE chantier
  technique ambitieux mais à plus forte valeur assurantielle.

### 2.2 Signalement d'état au retour
- **Valeur** : « il manque un dé », « boîte déchirée » — aujourd'hui cette info
  se perd oralement. Un bouton « signaler un problème » au retour alimente une
  liste de maintenance consultable par l'admin (cf. 6.2).
- **Note** : table `signalements(id_exemplaire, texte, cree_le, traite)` ;
  pastille « ⚠ signalement en cours » sur la fiche.

### 2.3 Statut d'exemplaire « retiré / en réparation »
- **Valeur** : un jeu incomplet ne doit plus être proposé sans être « sorti ».
  Aujourd'hui l'état est binaire (dispo/sorti). Un statut administratif le
  masquerait du catalogue proprement.
- **Note** : prolonge 2.2 ; l'état déduit reste la règle, ce statut est un
  drapeau administratif par-dessus.

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

## 3. Tournois

### 3.1 Appariements de la ronde courante sur l'écran salle
- **Valeur** : au lancement d'une ronde, les joueurs cherchent leur table.
  Afficher « Ronde 2 : Alice–Bob (table 3)… » sur `/live` évite au bénévole de
  crier les noms. Prolongement naturel de l'écran salle existant.
- **Note** : les données sont déjà dans `rencontres` ; ajouter un numéro de
  table serait le seul vrai ajout.

### 3.2 Lien tournoi ↔ catalogue
- **Valeur** : répond au point §11 resté ouvert (« jeu en texte libre ? »).
  Choisir le jeu depuis le catalogue permettrait en plus de **sortir
  automatiquement l'exemplaire** (motif tournoi) au lancement et de le rendre à
  la clôture — deux modules qui se parlent enfin.
- **Note** : champ `reference_titre` nullable côté tournoi ; le texte libre
  reste possible (jeux apportés par des joueurs).

### 3.3 Liste d'attente d'inscription
- **Valeur** : tournoi complet ≠ joueur perdu. File d'attente avec promotion
  automatique en cas de désinscription ; le pseudo suivant s'affiche sur le
  suivi public.
- **Note** : même mécanique pseudo + code que l'inscription.

### 3.4 Round robin (championnat toutes rondes)
- **Valeur** : pour 4–6 joueurs, c'est le format le plus juste et le plus
  convivial ; la ronde suisse est taillée pour de plus gros effectifs.
- **Note** : la table `rencontres` absorbe déjà ce mode ; génération = produit
  cartésien ordonné (algorithme des cercles).

### 3.5 Tournois par équipes
- **Valeur** : beaucoup de jeux d'ambiance se jouent en équipes ; inscription
  « nom d'équipe + pseudos » ouvrirait ces formats.
- **Note** : phase lointaine — touche inscription, appariement et affichage.

### 3.6 Palmarès & diplômes
- **Valeur** : un PDF « diplôme » (vainqueur, jeu, date, logo) imprimé en fin de
  tournoi coûte trois clics et fait un souvenir apprécié, surtout des enfants.
  Et une page « palmarès des éditions » valorise l'événement dans la durée.
- **Note** : reportlab et le logo sont déjà là ; le palmarès découle de
  `vainqueur()` + archivage des tournois `termine`.

## 4. Planning bénévoles

### 4.1 « Mon planning » en .ics
- **Valeur** : le bénévole ajoute ses créneaux à son agenda en un tap, comme
  pour les tournois. Réduit les oublis, zéro e-mail nécessaire.
- **Note** : quick win — `ical_tournoi` existe déjà, à généraliser (VEVENT
  multiples), route `/planning/mon.ics?code=`.

### 4.2 Pointage du jour J
- **Valeur** : « qui est arrivé ? » — cocher les présences sur la grille et
  voir en rouge les trous *réels* (affecté mais absent). C'est le stress
  principal du bureau le matin de l'événement.
- **Note** : drapeau `present` sur `affectations` + couleur d'état dédiée
  (la mécanique `pl-etat-*` existe).

### 4.3 Échange de créneaux entre bénévoles
- **Valeur** : « je ne peux plus samedi matin » se règle aujourd'hui par
  téléphone au bureau. Une demande d'échange (via `code_modif`) validée en un
  clic par l'admin fluidifierait sans perdre le contrôle.
- **Note** : table de demandes + notification sur l'écran de gestion.

### 4.4 Bilan d'engagement annuel
- **Valeur** : total d'heures par bénévole sur l'année → remerciements ciblés à
  l'AG, justificatifs de bénévolat (certaines demandes de subvention les
  valorisent), détection des piliers à ménager.
- **Note** : simple agrégation sur `affectations` multi-événements + export.

## 5. Écran salle & communication

### 5.1 Écran salle en « slides » tournantes
- **Valeur** : `/live` affiche tout en même temps. Une rotation (stats → 
  tournois → planning des animations → annonce) rend l'écran lisible de loin et
  hiérarchise l'information au fil de la journée.
- **Note** : pur JS côté page, l'endpoint `/live/data` suffit presque déjà.

### 5.2 Annonces libres pilotées par l'admin
- **Valeur** : « Tombola à 15 h », « portefeuille trouvé à l'accueil » — un
  champ texte en admin, affiché en bandeau sur `/live`. L'écran devient l'outil
  de communication central de la salle.
- **Note** : clé `parametres` + slide dédiée (5.1). Quick win.

### 5.3 Widget pour le site vitrine WordPress
- **Valeur** : le site vitrine (brique séparée) pourrait afficher en direct
  « 542 jeux disponibles — 3 tournois aujourd'hui » via un mini endpoint JSON
  public + un embed. Fait vivre le site pendant l'événement, draine du public.
- **Note** : `/live/data` existe ; ajouter CORS restreint + un iframe stylé.

### 5.4 Flux iCal public de l'événement
- **Valeur** : tout le programme des tournois en un seul abonnement agenda
  (`/tournois/agenda.ics`), publiable sur le site et les réseaux avant
  l'événement.
- **Note** : généralisation directe de `ical_tournoi` (multi-VEVENT).

## 6. Gestion associative & pilotage

### 6.1 Aide au désherbage et aux achats
- **Valeur** : croiser prêts par titre sur plusieurs éditions + avis (1.6) →
  « jamais sorti en 3 ans » (candidats don/vente) et « toujours en rupture »
  (candidats rachat d'exemplaires). Décisions d'achat argumentées devant le CA.
- **Note** : l'historique n'est jamais purgé — la donnée est déjà là. Un écran
  « vie du parc » avec préréglages de période par édition suffirait.

### 6.2 Carnet de maintenance du parc
- **Valeur** : réceptacle des signalements (2.2), suivi « à réparer / réparé /
  retiré », pièces à racheter. Le parc est le principal actif de l'asso ;
  aujourd'hui son entretien ne laisse aucune trace.

### 6.3 Rapport d'édition auto-généré
- **Valeur** : après clôture, un PDF « Bilan de l'édition 2026 » (fréquentation
  du prêt, top jeux, tournois et vainqueurs, heures de bénévolat) prêt pour
  l'AG et les dossiers de subvention. Les exports existent en pièces détachées ;
  les assembler ferait gagner une soirée au bureau chaque année.
- **Note** : compose `collecter_stats` + données tournois + planning dans un
  seul document reportlab.

### 6.4 Notion d'« édition » de l'événement
- **Valeur** : socle des points 6.1/6.3 et de comparaisons année par année
  (« +12 % de prêts vs 2025 ») sans manipuler des filtres de dates à la main.
- **Note** : la clé `evenement_date` existe ; il s'agirait d'en faire une
  petite table `editions` (nom, début, fin) référencée par les stats.

### 6.5 Prêts longue durée aux adhérents
- **Valeur** : faire vivre la ludothèque *entre* les événements (déjà cadré
  dans `docs/evolution-prets-longue-duree.md`). Change la nature du service —
  et la donne RGPD — donc décision CA avant tout.

## 7. Technique & robustesse

### 7.1 Mode bac à sable / formation
- **Valeur** : former les nouveaux bénévoles la semaine avant, sans polluer la
  vraie base. Un « mode démo » (bases jetables, bandeau visible) comme celui
  qui existe déjà pour le planning.
- **Note** : la démo planning donne le patron ; généraliser au prêt (jeux
  fictifs + QR d'entraînement imprimables).

### 7.2 Supervision légère en admin
- **Valeur** : le jour J, savoir en 5 s que tout va bien : taille et date de
  dernière sauvegarde des 3 bases, espace disque, version déployée, état du
  jeton. Rassure un bureau non technique.
- **Note** : page admin en lecture seule, stdlib uniquement.

### 7.3 Test de charge avant l'événement
- **Valeur** : ~700 jeux, des dizaines de scans/minute en pointe : vérifier une
  fois que SQLite/WAL + 1 worker uvicorn tiennent, plutôt que le découvrir en
  salle. Ajuster `busy_timeout` si besoin.
- **Note** : script locust/hey rejouant des scénarios prêter/rendre.

### 7.4 Accessibilité
- **Valeur** : public familial et intergénérationnel → contrastes, tailles de
  police, navigation clavier (déjà amorcée avec `:focus-visible`), labels ARIA
  sur les formulaires publics. Peu coûteux, image très positive pour une asso.

---

## Synthèse — par où commencer ?

**Quick wins** (petits, forte valeur) : saisie manuelle scanner (2.4), annonces
sur `/live` (5.2), .ics planning (4.1) et flux iCal tournois (5.4), avis éclair
au retour (1.6), diplômes (3.6).

**Chantiers structurants** (à valider en CA) : lien tournoi↔catalogue (3.2),
notion d'édition + rapport d'édition (6.4, 6.3), enrichissement BGG + photos
(1.2, 1.1), signalements + maintenance (2.2, 6.2).

**Ambitieux mais assurantiel** : mode hors-ligne (2.1) — le seul vrai point de
défaillance du dispositif actuel.
