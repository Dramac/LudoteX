# Personnaliser LudoteX — « je viens d'installer, comment ça devient chez moi ? »

Ce guide s'adresse à **la personne qui installe** LudoteX pour une association.
Il répond à une seule question : une fois `deploy/install.sh` terminé, que
reste-t-il à régler, et où.

La réponse tient en une phrase : **l'identité se règle depuis le site, dans
`/admin/identite` ; l'infrastructure reste dans le fichier `.env` du serveur.**
Le reste de cette page dit pourquoi, et ce qui tombe de chaque côté.

> **Le mode d'emploi détaillé de chaque réglage — ce qu'il change, ce qu'il
> devient s'il est laissé vide, que faire s'il ne s'applique pas — est dans le
> wiki**, page « Guide administrateur », section « L'identité de
> l'association ». Il est écrit pour le bureau, sans jargon. Cette page-ci ne le
> répète pas : elle donne la vue d'ensemble et la frontière entre les deux
> domiciles.

## 1. Ce que l'installation a déjà posé

`deploy/install.sh` écrit un `.env` sur le serveur et vous a demandé, au
passage, le **nom de l'association**. Ce nom part donc dans le `.env`, où il
sert de repli tant que rien n'est enregistré en administration.

Tout le reste — présentation, contact, logo, couleur — est vide à ce stade, et
le site tourne quand même : chaque réglage absent a un repli, et aucun ne
laisse un blanc à l'écran.

## 2. Les six réglages de `/admin/identite`

Connectez-vous à `/admin` avec le mot de passe choisi à l'installation, puis
ouvrez **Identité**. Six réglages, à poser une fois :

| Réglage | Où il apparaît | Laissé vide |
|---|---|---|
| **Nom de l'association** | bandeau, pied de page, titres d'onglet, page « À propos », en-tête des exports, fichiers d'agenda, message de partage de l'accès bénévole | repli `.env`, puis « LudoteX » |
| **Présentation** | section « L'association » de la page « À propos » | la section disparaît |
| **Adresse de contact** | section « Contact » de la page « À propos » | la section disparaît |
| **Adresse du code source** | lien « code source » de la page « À propos » | repli `.env`, puis l'adresse du dépôt d'origine |
| **Logo** | accueil, catalogue, onglet du navigateur, icône d'écran d'accueil, étiquettes des boîtes | le logo LudoteX (un meeple noir sur les étiquettes) |
| **Couleur du site** | bandeau, boutons, aplats colorés, tableaux des exports PDF | l'anthracite chaud par défaut |

Trois choses valent d'être sues avant de s'y mettre :

- **Une seule couleur se règle.** Les nuances (survol, fonds teintés) et la
  couleur du texte du bandeau en sont **déduites** — c'est ce qui garantit
  qu'aucun choix ne peut rendre le bandeau illisible. Certaines couleurs ne
  bougent jamais parce qu'elles portent un sens et non une identité : le vert du
  prêt, le bleu du retour, l'orange des avertissements, le rouge des erreurs.
- **Déposez le logo avant d'imprimer les étiquettes.** Elles portent le logo au
  moment de l'impression ; celles déjà imprimées ne changeront pas ensuite.
- **Le logo n'est pas dans les sauvegardes**, qui ne contiennent que les
  données. Après une restauration — ou après une mise à jour depuis une version
  antérieure à la 1.11.0 — il est à **redéposer** depuis cet écran.

Le **nom de l'événement**, lui, n'est pas ici : il change à chaque édition et
vit dans `/admin/evenement`.

## 3. Ce qui reste dans le `.env`, et pourquoi

Un réglage qui engage **l'infrastructure ou la sécurité** ne descend pas dans
une interface web : le modifier depuis un navigateur, c'est pouvoir casser le
site — voire le rendre inaccessible — sans rien pour rattraper le coup.

| Variable | Rôle | Pourquoi elle ne se règle pas en ligne |
|---|---|---|
| `BASE_URL` | l'adresse publique du site | **Elle est encodée dans les QR codes déjà imprimés.** La changer rend muettes toutes les étiquettes collées sur les boîtes. C'est le réglage le plus définitif de l'installation : choisissez le domaine avant d'imprimer. |
| `PRET_TOKEN` | jeton d'écriture des bénévoles | c'est un secret ; il se renouvelle depuis `/admin`, jamais à la main |
| `ADMIN_PASSWORD` | amorçage du mot de passe admin | haché au premier démarrage, puis changé depuis `/admin` |
| `DATABASE_PATH`, `TOURNOI_DATABASE_PATH`, `PLANNING_DATABASE_PATH` | emplacement des trois bases | déplacer une base depuis un formulaire web, c'est perdre ses données |
| `JOURNAL_PATH`, `JOURNAL_CONSOLE` | journal d'activité | même raison |
| `RATE_LIMIT_PER_MINUTE`, `APP_ENV` | limitation de débit, environnement | réglages de sécurité et d'exploitation |
| `MODE_FORMATION`, `FORMATION_URL`, `FORMATION_CATALOGUE_CSV` | seconde instance d'entraînement | ils distinguent deux instances : une instance ne doit pas pouvoir se déclarer « de formation » toute seule |
| `NOM_ASSOCIATION`, `DEPOT_URL` | **replis** du nom et de l'adresse du code source | ils ne sont là que pour le temps qui précède le premier réglage en administration (voir ci-dessous) |

Après modification du `.env`, redémarrer le service :
`sudo systemctl restart ludotex`.

## 4. La cascade des replis

Deux valeurs ne peuvent jamais être vides — le nom, parce qu'il s'affiche
partout ; l'adresse du code source, parce que la GPL exige qu'un utilisateur
puisse atteindre la source de la version qu'il fait tourner. Elles suivent donc
une cascade à trois étages :

```
valeur enregistrée dans /admin/identite   (le domicile normal)
        ↓ absente
variable du .env                          (posée par l'installation)
        ↓ absente ou vide
littéral du code                          (« LudoteX », le dépôt d'origine)
```

Une conséquence à connaître : une variable **présente mais vide** dans le `.env`
(`NOM_ASSOCIATION=`, comme dans `.env.example`) n'est pas une erreur — elle
retombe sur le littéral, pas sur du vide. Vous pouvez donc laisser ces deux
lignes vides et tout régler depuis l'administration ; c'est même le parcours
recommandé.

## 5. Ce qui ne se personnalise pas

- **La page « À propos » n'est pas éditable en entier.** L'essentiel y est de la
  documentation produit, identique pour tout déploiement ; seuls la
  présentation, le contact et les crédits sont à vous.
- **Pas de HTML libre saisi en administration.** Les textes s'affichent tels
  qu'ils sont écrits, échappés : ni gras, ni couleur, ni lien cliquable. C'est ce
  qui garantit qu'un copier-coller depuis un traitement de texte ne peut rien
  casser sur un site public.
- **Les étiquettes ne prennent pas la couleur du site.** Elles restent en noir
  et blanc : plusieurs centaines d'impressions, autant économiser l'encre.
- **Le SVG n'est pas accepté pour le logo** : ce format peut contenir des
  instructions exécutables, qu'un site public n'a pas à afficher. PNG, JPEG ou
  WebP.

## 6. Si vous publiez votre propre version

La GPLv3 vous y autorise, et demande en retour que le lien « code source » de
votre site mène à **votre** dépôt : renseignez alors l'adresse du code source
dans `/admin/identite`.

Le nom et le logo LudoteX, eux, font l'objet d'une **demande — pas d'une
obligation** : donnez un autre nom et un autre logo à une version modifiée, pour
qu'un utilisateur n'attribue pas au projet d'origine un comportement qui ne
vient pas de lui. Le détail est dans le [README](../README.md#licence).
