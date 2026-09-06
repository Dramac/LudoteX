# LudoteX

[![Licence : GPLv3](https://img.shields.io/badge/licence-GPLv3-blue.svg)](LICENSE)

**LudoteX enregistre les prêts de jeux de société d'un événement, par scan d'un
QR code sur la boîte, depuis le téléphone de n'importe quel bénévole.** Il
remplace la feuille de prêt papier — celle qu'un seul bénévole peut tenir à la
fois, et devant laquelle la file s'allonge aux heures d'affluence.

L'application est **en production depuis juillet 2026** sur l'instance d'une
association, avec plusieurs centaines de boîtes étiquetées. Autour du prêt, elle
gère aussi les tournois, le programme des animations, le planning des bénévoles
et l'écran de salle.

## À qui ça s'adresse

À une association qui **prête des jeux le temps d'un événement** — un festival,
une nuit du jeu, un salon — et qui les récupère à la fin.

Ceux qui s'en servent le jour J sont des **bénévoles non techniciens, sur leur
propre smartphone**, dans un gymnase au wifi capricieux. Ceux qui l'administrent
sont un **bureau d'association** : ils règlent, impriment, exportent, sans
jamais ouvrir de terminal.

## Ce qui le distingue

- **Aucune donnée personnelle.** L'anti-vol repose sur un **numéro de
  pochette** : la pièce d'identité de l'emprunteur est glissée dans une pochette
  numérotée, et seul ce numéro relie un prêt à une personne. L'application ne
  stocke ni nom, ni téléphone, ni e-mail — y compris pour les inscriptions aux
  tournois. Il n'y a donc pas de registre de traitement à tenir, pas de durée de
  conservation à justifier, et rien à effacer sur demande.
- **Rien à installer pour les bénévoles.** Pas d'application mobile : un lien,
  ouvert dans le navigateur du téléphone, ajouté à l'écran d'accueil en un tap.
- **Des pages construites par le serveur**, sans framework JavaScript. Un vieux
  téléphone et un wifi de salle suffisent : ce qui arrive sur l'écran est une
  page terminée, pas une application à télécharger puis à faire tourner.
- **Une dépendance lourde en moins.** Les données vivent dans des fichiers
  SQLite : aucun serveur de base de données à installer, à surveiller ni à
  sauvegarder à part. Une sauvegarde, c'est une copie de fichiers.
- **Jamais bloquant.** Toute incohérence rencontrée au scan donne un message en
  français et une action de rattrapage en un tap — jamais une erreur brute
  devant une file d'attente.

## Ce qu'il y a dedans

- **Prêt** — catalogue public avec recherche et filtres, fiche par exemplaire,
  scanner caméra avec saisie manuelle de secours, prêt / retour / re-prêt /
  transfert de pochette, sortie « tournoi », clôture de fin d'événement.
- **Statistiques** — totaux, palmarès par titre, histogramme horaire, durées,
  filtre par période, exports Excel et PDF.
- **Tournois** — inscription publique (pseudo + code de désinscription, sans
  e-mail), quatre modes de scoring, tournois par équipes, export d'agenda.
- **Programme du week-end** — animations, ateliers et temps forts, avec leur
  grille publique.
- **Planning des bénévoles** — questionnaire de disponibilités, préremplissage
  automatique, grille d'ajustement, export « mon planning ».
- **Écran de salle** — tableau de bord à projeter, annonces du bureau, rappel
  automatique avant chaque tournoi.
- **Rangement et carnet de maintenance** — où va chaque boîte, ce qu'il y a à
  réparer ou à racheter.
- **Administration** — étiquettes QR à imprimer, import/export du catalogue,
  sauvegarde et restauration, jeton bénévole, journal d'activité, identité de
  l'association, mode formation pour s'entraîner sans toucher aux vraies
  données.

## Ce qu'il faut pour l'exploiter

- Un **VPS d'entrée de gamme** sous Debian 12 ou Ubuntu 22.04+ (1 vCPU, 2 Go de
  RAM), avec un accès SSH.
- Un **nom de domaine**, dont le DNS pointe vers ce VPS.
- **Quelqu'un capable de suivre une procédure**, une fois. Pas un
  administrateur système : le script d'installation pose des questions et fait
  le reste. C'est l'affaire d'une première mise en ligne, pas d'un poste
  d'exploitation à tenir toute l'année.

Ordre de grandeur du coût annuel : **40 à 80 €** — le VPS d'entrée de gamme et
le nom de domaine, rien d'autre. Ce n'est pas un devis : les tarifs varient
d'un hébergeur à l'autre et dans le temps.

## Ce que ça ne fait pas

Dire non tout de suite fait gagner du temps à tout le monde.

- **Ce n'est pas un logiciel de ludothèque à l'année.** LudoteX est pensé pour
  un événement : on ouvre, on prête, on récupère tout, on clôt. Pas de prêts sur
  plusieurs semaines, pas de relances, pas d'amendes de retard.
- **Il n'y a pas de gestion des adhérents.** Ni fichier de membres, ni
  cotisations, ni cartes — c'est le corollaire direct du « aucune donnée
  personnelle » plus haut, et c'est assumé.
- **Il n'y a pas d'application mobile** à installer sur les téléphones, ni sur
  les magasins d'applications. C'est un site web.
- **Il n'y a pas de réservation en ligne** d'un jeu par le public, ni de compte
  utilisateur.
- **Il n'y a pas de multi-association** : une instance = une association. Deux
  associations, ce sont deux installations.
- **Il n'y a pas de mode hors ligne** : les téléphones doivent atteindre le
  serveur. Un wifi lent suffit, une absence de réseau non.

## Démarrer

**Installer sur un serveur.** Prérequis : un VPS Debian 12 / Ubuntu 22.04+ avec
accès SSH, et un nom de domaine dont le DNS pointe déjà vers lui.

```bash
git clone https://github.com/Dramac/LudoteX.git
cd LudoteX
sudo ./deploy/install.sh
```

Le script est **interactif** : il installe les paquets nécessaires, pose
quelques questions (domaine, e-mail, nom de l'association, mot de passe admin),
puis configure tout — service systemd, reverse proxy nginx, certificat HTTPS
Let's Encrypt, sauvegarde quotidienne — et affiche à la fin le lien d'activation
des bénévoles. Guide pas à pas, dépannage et mises à jour :
[docs/deploiement.md](docs/deploiement.md).

**Essayer sur son poste**, sans serveur ni ligne de commande :
[docs/lancement-local.md](docs/lancement-local.md).

**Remplir le catalogue pour voir à quoi ça ressemble.** Une base vide ne montre
rien : le dépôt contient un petit catalogue fictif d'une vingtaine de jeux, au
format attendu par l'import.

```bash
python -m scripts.import_csv exemples/catalogue-exemple.csv
```

Le même import accepte votre propre export de catalogue, en CSV, depuis
l'écran « Données & sauvegarde » de l'administration.

**Faire que ça devienne chez vous** — nom, logo, couleur, présentation,
contact : [docs/personnaliser.md](docs/personnaliser.md).

## Sous le capot

- **Backend :** Python 3.11+ et [FastAPI](https://fastapi.tiangolo.com/), servi
  par `uvicorn` derrière nginx.
- **Données :** SQLite en mode WAL. **Trois bases indépendantes** — prêt,
  tournois, planning — sans aucune clé étrangère entre elles.
- **Front :** pages Jinja2 rendues par le serveur, CSS mobile-first sans
  framework ni build. Le JavaScript se limite au scanner caméra (jsQR,
  versionné dans le dépôt) et à quelques scripts courts : **aucune dépendance
  CDN**.
- **PWA :** « ajouter à l'écran d'accueil » pour un lancement en un tap.

### Deux clés non négociables

| Clé | Rôle |
|---|---|
| `id_exemplaire` | identifiant **unique d'une boîte physique**, encodé dans le QR (`/jeu/<id_exemplaire>`). Ne change jamais une fois le QR imprimé. |
| `reference_titre` | clé de **regroupement des exemplaires d'un même jeu** (ex. `CATAN`), indispensable aux statistiques par titre. |

Base de prêt : `titres`, `exemplaires`, `prets` (historique complet, jamais
purgé), `pochettes` (occupation du moment, numéro recyclé = plus petit libre,
sans plafond) et `parametres` (réglages persistants). Base des tournois et base
du planning : séparées, indépendantes.

### Structure du dépôt

```
LudoteX/
├── app/
│   ├── main.py          # point d'entrée FastAPI (routeurs, gestion d'erreurs)
│   ├── models.py        # schéma SQLite (base de prêt)
│   ├── db.py            # init + accès base + migrations
│   ├── services.py      # logique métier du prêt (état déduit, pochettes, stats)
│   ├── auth.py          # jeton bénévole + limitation de débit
│   ├── etiquettes.py    # dessin des étiquettes QR (partagé avec scripts/)
│   ├── exports.py       # exports Excel / PDF
│   ├── routes/          # catalogue, pret, scanner, stats, acces, admin
│   ├── tournoi/         # module Tournois (base, modèles, services, routes séparés)
│   ├── planning/        # module Planning bénévoles (base séparée)
│   ├── static/          # CSS, jsQR local, logo LudoteX par défaut
│   └── templates/       # pages Jinja2
├── scripts/             # import du catalogue, génération des QR, journal
├── deploy/              # install.sh, update.sh, systemd, nginx, sauvegarde
├── exemples/            # catalogue fictif prêt à importer
├── docs/                # conception, déploiement, personnalisation, vocabulaire
├── logo/                # sources de l'identité LudoteX (CC0, voir LICENCE.md)
├── tests/               # suite pytest
├── data/                # bases SQLite + logo déposé en admin (NON versionnés)
├── requirements.txt
└── .env.example
```

## Documentation

- **Utiliser l'application** (bénévoles, bureau) : le
  [wiki du dépôt](https://github.com/Dramac/LudoteX/wiki), écrit sans jargon.
- **Installer et exploiter** : [docs/deploiement.md](docs/deploiement.md),
  [docs/personnaliser.md](docs/personnaliser.md).
- **Comprendre et reprendre le code** :
  [docs/README.md](docs/README.md) donne l'ordre de lecture.
  La conception fait foi — [docs/specification.md](docs/specification.md).

## Sécurité

- Ne **jamais** committer le jeton bénévole, le fichier `.env`, ni les bases
  SQLite de production. `.env.example` sert de modèle.
- Séparation lecture / écriture : les fiches publiques (`/jeu/...`) n'ont aucune
  action ; les opérations de prêt et de retour sont protégées par un **jeton
  aléatoire long** mémorisé côté appareil, avec limitation de débit par IP, et
  renouvelé à chaque édition.
- L'espace d'administration est protégé par un **mot de passe distinct** du
  jeton bénévole.
- **Zéro donnée personnelle** dans l'application : propriété centrale à
  préserver, y compris dans les contributions.

## Contribuer

Signalements de bugs, propositions, périmètre du projet :
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

**Le code** est publié sous licence **[GNU GPLv3](LICENSE)** : libre de
réutilisation, modification et redistribution, à condition que toute version
modifiée et redistribuée reste elle aussi publiée sous GPLv3 (copyleft).

**Le logo** (dossier [`logo/`](logo/), et ses copies servies par l'application
dans `app/static/img/`) est versé au **domaine public**, sous
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/deed.fr) — et non
sous GPLv3. Le dessin d'origine a été produit par un outil d'intelligence
artificielle générative : une image purement générée n'a vraisemblablement
aucun titulaire de droits, et concéder des droits qu'on n'est pas sûr de
détenir serait une affirmation de trop. Le détail est dans
[`logo/LICENCE.md`](logo/LICENCE.md).

**Nom et logo — une demande, pas une obligation.** LudoteX n'est pas une marque
déposée, et rien ici ne vous interdit juridiquement de les reprendre. Mais si
vous publiez une version modifiée, **donnez-lui un autre nom et un autre
logo** : cela évite qu'un utilisateur attribue au projet d'origine un
comportement, un défaut ou un engagement de support qui ne viennent pas de lui.
Parler de LudoteX — un article, une présentation, une capture d'écran — est
naturellement libre.

**Le logo affiché par une instance déployée n'est pas dans ce dépôt** : chaque
association dépose le sien depuis `/admin/identite`, et il vit dans `data/`.
Sans dépôt, c'est le logo LudoteX qui s'affiche.

## Support

Autant le dire franchement : **LudoteX est développé et maintenu par une seule
personne**, sur son temps libre, à côté d'un usage associatif réel.

- Les **tickets sont lus**, et les questions trouvent en général une réponse.
- **Aucun délai n'est garanti**, ni aucune correction. Selon la période, une
  réponse peut prendre des semaines.
- Il n'y a **ni contrat de support, ni feuille de route publique, ni engagement
  de compatibilité** au-delà de ce que la licence dit.

C'est un logiciel libre : si vous l'exploitez, prévoyez de savoir le remettre
en marche vous-même, ou de trouver quelqu'un qui le sait. La documentation est
écrite pour ça.
