# pret-jeux

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

## Stack

- **Backend :** Python + [FastAPI](https://fastapi.tiangolo.com/), servi par `uvicorn`.
- **Base de données :** SQLite (charge faible, sauvegarde simple).
- **Front :** pages servies par le backend + un peu de JS pour le scanner caméra
  embarqué dans la page.
- **PWA :** « ajouter à l'écran d'accueil » pour un lancement en un tap, sans installation.
- **Déploiement cible :** VPS Lite (Debian/Ubuntu), HTTPS via Let's Encrypt.

## Modèle de données — deux clés non négociables

| Clé | Rôle |
|---|---|
| `id_exemplaire` | identifiant **unique d'une boîte physique**, encodé dans le QR (`/jeu/<id_exemplaire>`). Ne change jamais une fois le QR imprimé. |
| `reference_titre` | clé de **regroupement des exemplaires d'un même jeu** (ex. `CATAN`), indispensable aux statistiques par titre. |

Quatre tables : `titres`, `exemplaires`, `prets` (historique complet, jamais purgé) et
`pochettes` (occupation du moment, numéro recyclé = plus petit libre, **sans plafond**).

## Démarrage rapide (développement)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # puis éditer .env (jeton, chemin base, domaine)
python -m app.db              # initialise la base SQLite vide
uvicorn app.main:app --reload
```

## Structure

```
pret-jeux/
├── app/
│   ├── main.py          # point d'entrée FastAPI
│   ├── models.py        # schéma SQLite des 4 tables
│   ├── db.py            # init + accès base
│   ├── routes/
│   │   ├── pret.py      # prêt / retour (écriture, protégé par jeton)  — squelette
│   │   └── catalogue.py # consultation publique (lecture seule)        — squelette
│   ├── static/          # JS du scanner embarqué, CSS
│   └── templates/       # pages (fiche jeu, écran prêt/retour, catalogue, stats)
├── scripts/
│   ├── import_csv.py    # import / mise à jour du catalogue (à venir)
│   └── generate_qr.py   # génération des QR (à venir)
├── data/                # base SQLite (NON versionnée)
├── docs/                # spécification, budget, brief de passation
├── tests/
├── requirements.txt
├── .gitignore
└── .env.example
```

## Documentation

La conception fait foi : voir **[docs/specification.md](docs/specification.md)**.
Contexte budgétaire et d'hébergement : [docs/budget.md](docs/budget.md).
Brief de passation : [docs/brief-handoff.md](docs/brief-handoff.md).

## Sécurité

- Ne **jamais** committer le jeton bénévole, le fichier `.env`, ni la base SQLite de
  production. Utiliser `.env.example` comme modèle.
- Séparation lecture / écriture : les fiches publiques (`/jeu/...`) n'ont aucune action ;
  les opérations de prêt/retour sont protégées par un **jeton aléatoire long** mémorisé
  côté appareil, avec limitation de débit par IP. Rotation annuelle du jeton.
