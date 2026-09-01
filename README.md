# LudoteX — brique de prêt

[![Licence : GPLv3](https://img.shields.io/badge/licence-GPLv3-blue.svg)](LICENSE)

Application web de **prêt de jeux de société** pour l'événement annuel d'une association
(~700 jeux). Chaque exemplaire porte un QR code ; les bénévoles scannent avec leur
smartphone pour enregistrer prêts et retours sur une base partagée, en remplacement de la
feuille papier unique (goulet d'étranglement aux heures de pointe).

L'anti-vol repose sur un **numéro de pochette** : la pièce d'identité de l'emprunteur est
glissée dans une pochette numérotée, et seul ce numéro relie un prêt à une personne.
L'application **ne stocke aucune donnée personnelle** et reste donc hors du champ du RGPD.

> Ce dépôt couvre uniquement la **brique de prêt** (web-app Python + SQLite, sur VPS).
> La brique « site vitrine + newsletter » (WordPress sur hébergement mutualisé) est
> volontairement cloisonnée et hors de ce dépôt.

## Fonctionnalités

La **phase 1 est complète**. L'application propose aujourd'hui :

- **Page d'accueil publique** (`/`) : accès aux outils publics, nombre de jeux
  disponibles au prêt, et tournois qui commencent dans l'heure.
- **Catalogue public** (`/catalogue`) : liste des jeux, disponibilité par titre,
  recherche et filtres combinés (nom, catégorie, âge, nombre de joueurs).
- **Fiche d'un exemplaire** (`/jeu/<id>`) : cible des QR codes, en lecture seule.
- **Scanner caméra** embarqué (`/scanner`) : décodage du QR dans le navigateur
  (jsQR, compatible iOS/Android), puis ouverture de l'écran de prêt.
- **Prêt / retour** (`/pret/<id>`) : « Prêter » (attribue le plus petit numéro de
  pochette libre), « Rendre », « Le re-prêter », et « Sortir pour un tournoi ».
  Jamais bloquant : toute incohérence donne un message + une action de rattrapage.
- **Statistiques** (`/stats`) : prêts totaux / en cours, palmarès par titre,
  histogramme par heure, durées, filtre par période, exports **Excel** et **PDF**.
- **Espace d'administration** (`/admin`, mot de passe) : création de fiches,
  (ré)impression d'étiquettes, gestion du jeton bénévole, clôture de fin
  d'événement.
- **Module Tournois** (`/tournois`) : création/gestion par les bénévoles,
  inscription publique (pseudo + code de désinscription, **sans e-mail**), suivi
  public, et trois modes de scoring — **high score**, **ronde suisse** et
  **élimination directe** — avec option **best of 3**.

Le catalogue réel importé compte **609 titres / 703 exemplaires**. La suite de tests
compte **79 tests** (`pytest`).

## Stack

- **Backend :** Python + [FastAPI](https://fastapi.tiangolo.com/), servi par `uvicorn`.
- **Base de données :** SQLite (charge faible, sauvegarde simple), ouverte en mode WAL.
- **Front :** pages servies par le backend (Jinja2) + un peu de JS pour le scanner caméra.
- **PWA :** « ajouter à l'écran d'accueil » pour un lancement en un tap, sans installation.
- **Déploiement cible :** VPS Lite (Debian/Ubuntu), HTTPS via Let's Encrypt.

## Modèle de données — deux clés non négociables

| Clé | Rôle |
|---|---|
| `id_exemplaire` | identifiant **unique d'une boîte physique**, encodé dans le QR (`/jeu/<id_exemplaire>`). Ne change jamais une fois le QR imprimé. |
| `reference_titre` | clé de **regroupement des exemplaires d'un même jeu** (ex. `CATAN`), indispensable aux statistiques par titre. |

Base de prêt (`data/pret-jeux.db`) : `titres`, `exemplaires`, `prets` (historique
complet, jamais purgé), `pochettes` (occupation du moment, numéro recyclé = plus petit
libre, **sans plafond**) et `parametres` (réglages persistants : hash admin, jeton…).

Base des tournois **séparée** (`data/tournoi.db`, aucune clé étrangère entre les deux) :
`tournois`, `inscriptions` (pseudo + code, jamais d'e-mail) et `rencontres` (matchs).

## Démarrage rapide (développement)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # puis éditer .env (jeton, chemin base, domaine)
python -m app.db              # initialise la base SQLite vide
python scripts/import_csv.py <catalogue.csv>   # (optionnel) importe le catalogue
uvicorn app.main:app --reload
```

> Le scanner caméra exige un contexte sécurisé (HTTPS ou `localhost`). Pour tester
> le scan depuis un smartphone, exposer `uvicorn` via un tunnel HTTPS
> (Cloudflare Tunnel / ngrok). Déploiement VPS : voir `docs/deploiement.md`.

## Installation en production (VPS)

Prérequis : un VPS Debian 12 / Ubuntu 22.04+ avec accès SSH, et un nom de
domaine dont le DNS pointe déjà vers le VPS.

```bash
git clone https://github.com/Dramac/LudoteX.git
cd LudoteX
sudo ./deploy/install.sh
```

Le script `deploy/install.sh` est **interactif** : il installe les paquets
système nécessaires (Python 3.11+, nginx, certbot...), pose quelques
questions (domaine, e-mail, nom de l'association, mot de passe admin,
chemins d'installation), puis configure entièrement l'application — service
systemd, reverse proxy nginx, certificat HTTPS Let's Encrypt, sauvegarde
quotidienne — et affiche à la fin le lien d'activation bénévole.

Guide détaillé (pas à pas, dépannage, mises à jour) :
[docs/deploiement.md](docs/deploiement.md).

## Structure

```
ludotex/
├── app/
│   ├── main.py          # point d'entrée FastAPI (routeurs, gestion d'erreurs)
│   ├── config.py        # nom de l'association (NOM_ASSOCIATION), personnalisable
│   ├── models.py        # schéma SQLite (base de prêt)
│   ├── db.py            # init + accès base + migrations
│   ├── services.py      # logique métier du prêt (état déduit, pochettes, stats)
│   ├── auth.py          # jeton bénévole + limitation de débit
│   ├── admin_auth.py    # mot de passe admin (pbkdf2)
│   ├── etiquettes.py    # dessin des étiquettes QR (partagé avec scripts/)
│   ├── exports.py       # exports Excel / PDF des stats
│   ├── routes/          # catalogue, pret, scanner, stats, acces, admin
│   ├── tournoi/         # module Tournois (base, modèles, services, routes séparés)
│   ├── static/          # CSS, JS du scanner (jsQR local), logo LudoteX servi par défaut
│   └── templates/       # pages Jinja2 (accueil, fiche, prêt, catalogue, stats, tournois…)
├── scripts/
│   ├── import_csv.py    # import / mise à jour tolérant du catalogue (UPSERT)
│   └── generate_qr.py   # génération des QR (PNG individuels + planche A4)
├── deploy/              # install.sh (installation interactive), systemd, nginx, sauvegarde
├── data/                # bases SQLite + logo déposé en admin (NON versionnés)
├── logo/                # sources de l'identité LudoteX (CC0, voir LICENCE.md)
├── docs/                # spécification, conception des modules, déploiement, vocabulaire…
├── tests/               # test_services, test_routes, test_tournoi (79 tests)
├── requirements.txt
├── .gitignore
└── .env.example
```

## Documentation

La conception fait foi : voir **[docs/specification.md](docs/specification.md)** et
**[docs/conception-tournois.md](docs/conception-tournois.md)**.
Déploiement pas à pas : [docs/deploiement.md](docs/deploiement.md).
Index complet de la documentation technique, et ordre de lecture pour reprendre
le projet : [docs/README.md](docs/README.md).

## Sécurité

- Ne **jamais** committer le jeton bénévole, le fichier `.env`, ni les bases SQLite de
  production. Utiliser `.env.example` comme modèle.
- Séparation lecture / écriture : les fiches publiques (`/jeu/...`) n'ont aucune action ;
  les opérations de prêt/retour (`/pret`, `/scanner`) sont protégées par un **jeton
  aléatoire long** mémorisé côté appareil, avec limitation de débit par IP. Rotation
  annuelle du jeton (réinitialisation depuis `/admin`).
- L'espace d'administration est protégé par un **mot de passe distinct** du jeton bénévole.
- **Zéro donnée personnelle** dans l'application : propriété centrale à préserver.

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
