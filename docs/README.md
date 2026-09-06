# docs/ — documentation technique de LudoteX

Ce dossier rassemble la documentation destinée à qui installe, exploite,
comprend ou reprend le code de LudoteX. Rien ici ne s'adresse aux bénévoles
ni au bureau d'une association : ce guide-là vit dans le wiki du dépôt.

## Par où commencer, pour reprendre le projet

1. **`specification.md`** — la conception **fait foi**. En cas de divergence
   avec le code ou un autre document, c'est elle qui a raison.
2. **`guide-developpeur.md`** — architecture, conventions, flux d'une
   requête, recettes d'extension, pièges connus. Le point d'entrée pour
   reprendre le code.
3. **`lancement-local.md`** — installer et lancer l'application sur son
   propre poste, avec ou sans ligne de commande.
4. **`deploiement.md`** — mettre l'application en ligne sur un VPS.
5. **`personnaliser.md`** — ce qui reste à régler une fois l'installation
   terminée : l'identité depuis `/admin/identite`, l'infrastructure dans le
   `.env`, et la frontière entre les deux.

## Le reste du dossier

- **`conception-*.md`** — la conception détaillée de chaque module livré
  (tournois, planning, rangement, programme, signalements, alerte avant
  tournoi, transfert de pochette, journal d'activité).
- **`ui-composants.md`** — les composants d'interface canoniques et leurs
  règles d'emploi.
- **`vocabulaire.md`** — le lexique commun à l'application et à sa
  documentation.
- **`mode-formation.md`** — la seconde instance dédiée à l'entraînement des
  bénévoles, sans toucher aux vraies données.
- **`versioning.md`** — la marche à suivre pour faire évoluer le numéro de
  version.
- **`protocole-stress-test.md`** — le protocole qui a mis au jour, puis
  vérifié le correctif, d'une course sur l'attribution des numéros de
  pochette.
- **`idees-ux.md`**, **`idees-evolutions.md`** — pistes non arbitrées, à
  lire comme des propositions, pas comme des engagements.
- **`audit-ux-2026-07-18.md`** — un audit d'ergonomie ponctuel, avec son
  état de traitement.

## Ce qui ne vit pas ici

Les documents propres à une association donnée — budget, présentations,
audits de sécurité datés, notes de travail internes — ne sont pas publiés :
reprendre LudoteX n'a pas besoin de savoir comment une association en
particulier a choisi son hébergeur ou financé son déploiement. `CLAUDE.md`,
à la racine du dépôt, dit où vit chaque autre type d'information.
