"""
Module « Tournois » — gestion des tournois de l'événement.

Voir docs/conception-tournois.md pour le cadrage complet. Points clés :

- MODULE INTÉGRÉ à l'application de prêt (sous-paquet `app/tournoi/`), mais avec
  sa PROPRE base SQLite `data/tournoi.db` : aucun couplage avec la base de prêt
  (cycles de vie, réinitialisation et sauvegarde indépendants).
- ACCÈS MUTUALISÉS : mêmes jeton bénévole (app/auth.py) et mot de passe admin,
  même bandeau/menu, même CSS. Un seul déploiement, un seul domaine.
- SÉPARATION public / bénévole (comme le prêt) : le public voit les tournois et
  s'inscrit ; le bénévole (jeton) crée, édite, gère les participants et les états.
- RGPD MINIMAL : on stocke uniquement le pseudo + un code de désinscription
  aléatoire. L'e-mail N'EST JAMAIS stocké (en phase 1, le code est seulement
  affiché à l'écran ; l'envoi e-mail est reporté en phase 2).

Phasage (docs/conception-tournois.md §10) — ce sous-paquet implémente le SOCLE
de la phase 1 : CRUD tournois, inscription publique, gestion des participants et
écrans de suivi. Les modes de scoring (high score, élimination directe, ronde
suisse) viennent ensuite, un par un, en s'appuyant sur la table `rencontres`
déjà prévue ici.
"""
