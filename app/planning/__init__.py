"""
Module « Planning bénévole » — collecte des souhaits, préremplissage dégrossi du
planning, validation par un admin, puis publication sur l'interface bénévole.

Voir docs/conception-planning.md pour le cadrage complet. Points clés :

- MODULE INTÉGRÉ à l'application de prêt (sous-paquet `app/planning/`), mais avec
  sa PROPRE base SQLite `data/planning.db` : aucun couplage avec la base de prêt
  ni celle des tournois (cycles de vie, purge et sauvegarde indépendants).
- ACCÈS : la collecte des souhaits est ouverte (lien partagé) ; la consultation
  du planning publié utilise le jeton bénévole ; la trame, le préremplissage,
  l'édition et la publication sont réservés à l'ADMIN (mot de passe).
- RGPD (§4) : rupture ASSUMÉE avec le « zéro donnée personnelle » du prêt. On
  stocke noms, contact, disponibilités et affectations, dans une base séparée, à
  finalité unique, purgée après l'événement.

Phasage (docs/conception-planning.md §10) — ce sous-paquet implémente le SOCLE
de la phase 1 : trame (postes/créneaux/besoins), collecte (dispos + préférences
à 4 niveaux + plafond d'heures), préremplissage GLOUTON « dégrossi » (contraintes
dures seulement), grille éditable avec verrouillage, publication et exports
PDF/Excel. Les affinements (continuité, expérience, équité fine) relèvent de la
phase 2.
"""
