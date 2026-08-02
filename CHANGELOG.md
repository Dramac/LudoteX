# Journal des versions

Toutes les évolutions notables de LudoteX, de la plus récente à la plus
ancienne. Les versions suivent le schéma `MAJEUR.MINEUR.CORRECTIF` (voir
`docs/versioning.md`). Les puces de la version la plus récente sont aussi
affichées sur la page « À propos » du site : les garder claires et tournées
vers l'utilisateur.

## 1.2.1 — 2026-08-02

- Deux bénévoles qui appuient sur « Prêter » au même instant obtiennent désormais deux numéros de pochette différents. Ils pouvaient jusqu'ici recevoir le même numéro, sans aucun message : deux pièces d'identité se retrouvaient alors dans la même pochette, et la restitution du soir partait de travers.
- Une même boîte ne peut plus être prêtée deux fois en même temps, que le double appui vienne d'un seul téléphone ou de deux. Le second bénévole voit le message « déjà sorti » habituel.
- Plus d'erreur au tout début de la soirée : quand aucune pochette n'était encore attribuée, plusieurs prêts simultanés provoquaient une page d'erreur.
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
