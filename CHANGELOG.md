# Journal des versions

Toutes les évolutions notables de LudoteX, de la plus récente à la plus
ancienne. Les versions suivent le schéma `MAJEUR.MINEUR.CORRECTIF` (voir
`docs/versioning.md`). Les puces de la version la plus récente sont aussi
affichées sur la page « À propos » du site : les garder claires et tournées
vers l'utilisateur.

## 1.9.0 — 2026-08-10

- Un jeu **rendu moins d'une minute après avoir été prêté** n'est plus compté comme un prêt : c'est presque toujours une mauvaise boîte scannée, ou un visiteur qui se ravise pendant qu'on lui prend sa pièce d'identité. Ces cas apparaissent désormais à part, sous le libellé **erreurs de prêt**, sur la page des statistiques.
- Ils sortent de **tous** les autres chiffres — total, palmarès du jeu concerné, durée moyenne qu'un prêt de dix secondes tirait vers le bas, histogramme, liste détaillée, exports Excel et PDF. Rien n'est effacé pour autant : le compteur dit combien de fois c'est arrivé.
- Au comptoir, **rien ne change** : le bénévole rend la pièce d'identité comme d'habitude, à la pochette indiquée. Une simple ligne sous le numéro le prévient que ce prêt ne sera pas compté.
- Nouveau réglage dans **Gestion de l'événement** : l'**inscription aux tournois** peut être réservée aux bénévoles. La page d'un tournoi reste alors publique et complète — horaire, lieu, places, classement, **📅 Ajouter à mon agenda** — mais le bouton **S'inscrire** cède la place à « Inscriptions auprès d'un bénévole, sur place. » Chacun peut toujours se désinscrire seul avec son code.
- Cinq écrans étaient bridés à une largeur de smartphone sur ordinateur : la **gestion d'un tournoi**, la **liste des tournois**, l'**accès bénévole**, la **gestion de l'événement** et l'**aide** occupent maintenant la place disponible.

## 1.8.0 — 2026-08-10

- Le **site de formation** peut désormais reprendre le **vrai catalogue** de l'association. Les étiquettes déjà collées sur les boîtes y fonctionnent alors : un bénévole s'entraîne avec de vraies boîtes en main, et rien de ce qu'il fait ne compte pour de bon. Jusqu'ici, scanner une vraie boîte sur le site de formation affichait « boîte inconnue », le site ne connaissant qu'une soixantaine de jeux inventés.
- La mise en place se fait en une fois, par la personne qui gère l'application : exporter le catalogue depuis **Données & sauvegarde**, déposer le fichier sur le serveur, réinitialiser les données de formation. C'est une **copie figée**, à rafraîchir quand on le décide — les deux sites restent totalement indépendants.
- Le message qui suit **Réinitialiser les données de formation** annonce maintenant d'où viennent les jeux — « copie du vrai catalogue » ou « jeux fictifs » —, de quoi vérifier d'un coup d'œil que la copie a bien été prise en compte.
- Pendant une session de formation, toujours scanner avec le **bouton Scanner de l'application**, jamais avec l'appareil photo du téléphone : celui-ci ouvre l'adresse inscrite dans le QR, c'est-à-dire le vrai site.

## 1.7.0 — 2026-08-10

- Quand une personne rend un jeu et en reprend un autre dans la foulée, un nouveau bouton **« Rendre et prêter un nouveau jeu sans retour PI »** évite de lui rendre sa pièce d'identité pour la reprendre aussitôt : elle **reste dans sa pochette**, et c'est le jeu qui lui est associé qui change. Scanner la nouvelle boîte, confirmer, c'est fait.
- Avant d'enregistrer quoi que ce soit, un écran de confirmation montre **les deux jeux** (« Catan → Dixit ») et le numéro de pochette conservé : c'est là qu'on rattrape un scan de la mauvaise boîte. Tant que l'on n'a pas confirmé, rien n'est enregistré — abandonner en cours de route ne casse rien, le bouton **Rendre** habituel reste à portée.
- Après un transfert, le numéro s'affiche dans une **troisième couleur**, avec la consigne « la pièce d'identité reste en place ». Impossible de le confondre avec un prêt (vert, on dépose la pièce d'identité) ou un retour (bleu, on va la récupérer). L'emplacement où ranger le jeu rendu s'affiche comme d'habitude.
- Le bouton n'apparaît **que sur un prêt au public** — jamais sur un jeu sorti pour un tournoi, qui n'a pas de pièce d'identité. Et si la personne se ravise et repart avec le même jeu, il suffit de le rescanner : elle garde sa pochette.
- **Écran de salle** : le titre projeté n'a plus qu'un seul réglage, le **nom de l'événement** (Administration → **Gestion de l'événement**). Le champ « Titre » disparaît de l'écran de salle, où il pouvait figer le nom de l'association et empêcher ensuite le nom de l'événement de s'afficher. Un titre déjà enregistré est repris automatiquement comme nom d'événement : rien à ressaisir.

## 1.6.0 — 2026-08-09

- L'application connaît désormais le **nom de l'événement** (« Festival du Jeu 2026 »), en plus de sa date. Il se règle dans **Administration → Gestion de l'événement**, l'ancien écran « Date de l'événement », qui rassemble maintenant les deux réglages et renvoie vers l'écran de salle, les types de programme et le planning bénévole.
- Le nom est rappelé sur la **page d'accueil**, sur le **programme du week-end**, sur la **liste des tournois** et sur la page d'un tournoi, ainsi que dans les fichiers **ajoutés à l'agenda**. Tant qu'aucun nom n'est saisi, rien ne change nulle part.
- Sur l'**écran de salle**, le titre affiché est le titre saisi s'il y en a un, sinon le nom de l'événement, sinon le nom de l'association. Le champ « titre » reste disponible pour donner un titre différent à l'écran projeté.
- Chaque **animation du programme a maintenant sa page**, comme les tournois : depuis le programme du week-end ou la page d'accueil, un clic ouvre le détail (type, horaire, durée, lieu, public visé, places, description) avec le bouton **« 📅 Ajouter à mon agenda »**.
- Une animation **annulée** garde sa page, avec un bandeau qui l'annonce clairement : quelqu'un qui a le lien ou l'a mise à son agenda apprend l'annulation au lieu de tomber sur une page introuvable.
- Le **numéro de version** est rappelé en bas de chaque page, à côté de la licence : plus besoin de chercher pour savoir quelle version tourne.

## 1.5.0 — 2026-08-06

- **Créer un tournoi** commence maintenant par le **nom du jeu**, seul champ obligatoire : c'est le cas de très loin le plus fréquent. Le titre du tournoi n'est plus à saisir séparément — il vaut le nom du jeu.
- Un champ facultatif **« Titre spécifique du tournoi »** reste disponible quand le tournoi porte un autre nom (« Coupe des familles », « Grand défi du dimanche ») ou mêle plusieurs jeux. Inutile d'y écrire « Tournoi » ou le mode de jeu : les écrans l'indiquent déjà.
- Le nom du jeu n'apparaît plus **deux fois** sur les pages d'un tournoi quand il sert aussi de titre — dans la liste, sur la page publique, sur l'écran de gestion, sur l'accueil et dans le fichier ajouté à l'agenda.
- Les tournois créés avant cette version s'ouvrent et se modifient sans rien ressaisir, et gardent leur titre tel quel.
- Sur le **site de formation**, les tournois d'exemple s'appellent désormais simplement « Catan » ou « Wingspan », au lieu de « Tournoi Catan — ronde suisse ».

## 1.4.0 — 2026-08-06

- L'**écran de salle** (`/live`) a été repensé pour un téléviseur : les tournois et les animations occupent désormais les deux tiers du bas de l'écran, tandis que les chiffres et le flux des prêts se partagent une bande en haut. Le flux des prêts prenait jusqu'ici un tiers de l'écran sur toute la hauteur, au détriment de ce qu'un visiteur vient chercher.
- Les **tournois à venir** et les **animations** se lisent comme un horaire : l'heure en gros à gauche, puis le nom, le lieu et les places — « 4 places libres / 12 », « complet », ou « inscriptions ouvertes » quand le nombre de places n'est pas fixé. Le prochain est mis en avant avec son délai (« dans 12 min »).
- Le **lieu des tournois** s'affiche enfin en salle, comme celui des animations. À l'inverse, le mode de jeu (« Ronde suisse ») n'y figure plus : il n'aidait aucun visiteur et prenait le pas sur le nom du tournoi, remplacé par le nombre de joueurs.
- Dans le flux des prêts, les mots « PRÊT » et « RETOUR » laissent la vedette au **nom du jeu** : une pastille de couleur porte l'information (orange pour une sortie, vert pour un retour).
- Une liste plus longue que la place disponible se termine maintenant par **« et 3 autres… »**. Auparavant, les éléments en trop disparaissaient sans que rien ne le signale.
- Marges élargies pour les téléviseurs qui rognent les bords, et taille de texte adaptée aussi bien au 16/9 qu'au 16/10.

## 1.3.2 — 2026-08-05

- Correctif : le téléphone d'un membre du bureau qui active l'accès bénévole puis se connecte en administration reste désormais compté et affiché comme actif sur les deux, y compris après un redémarrage du service. Jusqu'ici, la connexion administration effaçait par erreur son accès bénévole dans la liste des appareils (sans toucher à son fonctionnement réel), ce qui faussait le compteur d'« appareils bénévoles actifs ».

## 1.3.1 — 2026-08-05

- Le **journal d'activité** couvre désormais aussi les tournois, le programme du week-end, le planning bénévole et les prêts — pas seulement l'administration. Création, modification, suppression, changements d'état, lancement d'un tournoi, saisie de résultats, ajout/retrait d'un participant, modification manuelle d'une case du planning, chaque prêt et chaque retour : tout y laisse une ligne, avec le nom du jeu ou du tournoi concerné.
- Les **échecs de prêt** y apparaissent aussi (« déjà sorti », « déjà disponible », conflit d'accès simultané) : c'est un bénévole qui a vu un message inattendu, exactement ce qu'on cherche à comprendre après coup.
- Comme toujours : jamais un pseudo, un nom d'équipe, un nom de bénévole ou un numéro de pochette dans ces lignes.
- La supervision (`/admin/supervision` et le tableau de bord) affiche maintenant l'état du journal (taille, dernière écriture).

## 1.3.0 — 2026-08-04

- Nouveau **journal d'activité** en administration : un historique en lecture seule de qui a fait quoi et quand, à ouvrir quand quelque chose paraît anormal. Il enregistre les gestes d'administration et de configuration, ceux dont rien ne gardait trace jusqu'ici — connexions, réinitialisation de l'accès bénévole, imports de catalogue, sauvegardes restaurées, modules activés ou désactivés, annonces de l'écran de salle, clôture de fin d'événement. Filtrable par module, type de visiteur, action, appareil ou période, avec téléchargement du fichier complet.
- Les **tentatives de connexion ratées** à l'administration y apparaissent en orange. C'est le seul endroit où une tentative d'intrusion se voit : jusqu'ici, elle ne laissait aucune trace.
- Nouvelle **liste des appareils** sur la page « Accès bénévole » : combien de téléphones ont réellement activé l'accès, depuis quand, jusqu'à quand, et à quand remonte leur dernière action. Chaque appareil peut recevoir un libellé libre pour s'y retrouver (« comptoir 2 », « accueil »).
- Chaque bénévole peut lire l'identifiant de son propre appareil en bas de son écran « Scanner un jeu », pour pouvoir le dire au bureau en cas de dépannage.
- Le journal n'enregistre **jamais** de numéro de pochette, de pseudo de tournoi, de nom de bénévole, de code personnel, de mot de passe ni d'adresse internet. Les consultations (catalogue, fiches, tournois) n'y figurent pas non plus : ce n'est pas un compteur de visites.

## 1.2.1 — 2026-08-02

- Deux bénévoles qui appuient sur « Prêter » au même instant obtiennent désormais deux numéros de pochette différents. Ils pouvaient jusqu'ici recevoir le même numéro, sans aucun message : deux pièces d'identité se retrouvaient alors dans la même pochette, et la restitution du soir partait de travers.
- Une même boîte ne peut plus être prêtée deux fois en même temps, que le double appui vienne d'un seul téléphone ou de deux. Le second bénévole voit le message « déjà sorti » habituel.
- Plus d'erreur au tout début de la soirée : quand aucune pochette n'était encore attribuée, plusieurs prêts simultanés provoquaient une page d'erreur.
- Inscriptions aux tournois : deux personnes qui valident le formulaire au même instant sur la dernière place ne peuvent plus s'inscrire toutes les deux. Le tournoi ne part plus avec une chaise de trop.
- Nouveau message, très rare, si deux enregistrements se croisent malgré tout : « Un autre bénévole enregistrait une opération au même moment. Rien n'a été enregistré. » Il suffit de réappuyer sur le bouton, sans risque de doublon.

## 1.2.0 — 2026-07-26

- Nouveau module « Programme du week-end » : annoncer tout ce qui se passe en dehors des tournois (animations, ateliers, initiations, temps forts, interventions de partenaires), avec une page publique filtrable par jour et par type, et un ajout à l'agenda personnel (.ics).
- Types d'éléments de programme configurables en administration (renommer, archiver, réordonner), sans passer par le code.
- Page d'accueil : le bloc « Ça commence bientôt » et la frise du week-end réunissent désormais les tournois ET les animations, triés par heure — une seule information à consulter pour savoir ce qui commence.
- Écran de salle : nouvelle colonne « Animations » (un élément annulé reste annoncé, barré, pendant son créneau).
- Écran de salle : chaque panneau (chiffres, tournois, animations, prêts et retours) s'affiche ou se masque depuis « Écran de salle » en administration ; les colonnes restantes s'élargissent pour occuper la place.

## 1.1.0 — 2026-07-23

- Planning bénévole : à la création d'un nouveau planning, possibilité de reprendre la structure d'une édition existante (postes, créneaux et besoins), avec recalage automatique des dates sur le 1er jour de la nouvelle édition.

## 1.0.1 — 2026-07-23

- Écrans d'administration (Données et sauvegarde, Nouveau jeu, Jeux, Étiquettes, Rangement) mieux répartis en deux colonnes sur ordinateur — affichage mobile inchangé.

## 1.0.0 — 2026-07-23

Première version mise en production.

- Prêt et retour des jeux par scan du QR code, avec numéro de pochette pour la
  pièce d'identité (aucune donnée personnelle stockée).
- Catalogue public des jeux, avec recherche, filtres et fiche par jeu.
- Tournois : inscription en ligne, suivi et classements, avec quatre modes
  (high score, ronde suisse, élimination directe, championnat), option
  best-of-3, tournois par équipes et export agenda (.ics).
- Planning des bénévoles : collecte des disponibilités, préremplissage
  automatique, grille éditable, publication et export.
- Suivi de l'emplacement de rangement des boîtes (contexte événement ou local),
  avec affectation en lot par jeu.
- Écran de salle temps réel à projeter, avec annonces libres.
- Statistiques de prêt (palmarès, durées, histogramme) et exports Excel/PDF.
- Espace d'administration : gestion du catalogue, étiquettes QR, jeton
  bénévole, supervision, sauvegarde et restauration complète des trois bases.
- Site de formation optionnel (données fictives) pour l'entraînement des
  nouveaux bénévoles.

<!--
Modèle pour la prochaine version (copier-coller et adapter) :

## X.Y.Z — AAAA-MM-JJ

- Description d'une évolution, côté utilisateur.
- ...
-->
