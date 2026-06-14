# Brief de passation — Démarrage du développement

**But de ce document :** servir de point d'entrée à une session de développement (par ex. dans Claude Cowork) pour initialiser le dépôt GitHub et démarrer l'application de prêt. À lire avec les deux documents joints : `specification-systeme-pret-jeux.md` (conception) et `topo-budgetaire-systeme-pret.md` (budget).

---

## 1. Le projet en bref

Application web de **prêt de jeux de société** pour un événement annuel d'association (~700 jeux). Chaque exemplaire porte un QR code ; les bénévoles scannent avec leur smartphone pour enregistrer prêts et retours sur une base partagée, remplaçant la feuille papier unique (goulet d'étranglement). Anti-vol par **numéro de pochette** (pochette/ticket numéroté où l'on dépose la pièce d'identité) → **aucune donnée personnelle**.

Deux briques cloisonnées (voir spec §10) :
- **Brique de prêt** (objet de ce dépôt) : web-app Python + SQLite, sur VPS Lite.
- **Brique site + newsletter** : WordPress sur hébergement mutualisé, hors de ce dépôt.

---

## 2. Stack technique retenue

- **Langage :** Python (compétence confirmée du porteur ; pas de PHP/JS au-delà du nécessaire côté client, que le code embarquera).
- **Backend :** FastAPI (recommandé) ou Flask.
- **Base de données :** SQLite (charge faible, simplicité de sauvegarde).
- **Front :** pages servies par le backend + un peu de JS pour le scanner caméra embarqué (zone à isoler proprement).
- **Déploiement cible :** VPS Lite Infomaniak (Debian/Ubuntu), HTTPS via Let's Encrypt. Dev/test possible sur NAS Synology (Docker).
- **PWA :** « ajouter à l'écran d'accueil » pour lancement en un tap.

---

## 3. Modèle de données (rappel — détails en spec §3)

Deux clés non négociables, stables même si le CSV évolue :
- `id_exemplaire` — identifiant unique d'une **boîte physique**, encodé dans le QR.
- `reference_titre` — clé de regroupement des exemplaires d'un **même jeu** (pour les stats par titre).

Tables : `titres`, `exemplaires`, `prets` (historique complet, jamais purgé), `pochettes` (occupation du moment, numéro recyclé = plus petit libre, **sans plafond**).

État d'un exemplaire = déduit de l'existence d'un prêt non clos. Un seul jeu par PI / par numéro de pochette. Le numéro reste physiquement attaché à la PI (jamais de PI « en vrac »).

---

## 4. Arborescence GitHub proposée

```
pret-jeux/
├── README.md                 # présentation + lien vers la spec
├── docs/
│   ├── specification.md      # copie de la spec de conception
│   └── budget.md             # copie du topo budgétaire
├── app/
│   ├── main.py               # point d'entrée FastAPI
│   ├── models.py             # schéma SQLite (titres, exemplaires, prets, pochettes)
│   ├── db.py                 # init + accès base
│   ├── routes/
│   │   ├── pret.py           # prêt / retour / cas limites (écriture, protégé par jeton)
│   │   └── catalogue.py      # consultation publique (lecture seule)
│   ├── static/               # JS du scanner embarqué, CSS
│   └── templates/            # pages (fiche jeu, écran prêt/retour, catalogue, stats)
├── scripts/
│   ├── import_csv.py         # import / mise à jour du catalogue depuis le CSV
│   └── generate_qr.py        # génération des QR (URL .../jeu/<id_exemplaire>)
├── data/
│   └── .gitignore            # la base SQLite n'est PAS versionnée
├── tests/
├── requirements.txt
├── .gitignore
└── .env.example              # jeton bénévole, chemin base, domaine (jamais committer .env)
```

> **Sécurité dépôt :** ne jamais committer le jeton bénévole, le `.env`, ni la base SQLite de production. Utiliser `.env.example` comme modèle.

---

## 5. Règles métier à implémenter en priorité (voir spec §5 et §8)

1. **Scan d'un exemplaire DISPONIBLE** → action unique « Prêter » : attribuer le plus petit numéro de pochette libre, l'afficher en grand.
2. **Scan d'un exemplaire SORTI** → deux actions : « Rendre » (principale, libère le numéro) et « Le re-prêter » (cas d'oubli de scan : clôt l'ancien prêt puis en ouvre un nouveau).
3. **Ne jamais bloquer** : toute incohérence → message + action de rattrapage en un tap.
4. **Séparation lecture/écriture** : fiches publiques sans action ; écritures derrière un **jeton aléatoire long** (≈32 car.), mémorisé côté appareil, + limitation de débit par IP. Rotation annuelle.

---

## 6. Séquence de développement suggérée

1. Initialiser le dépôt + structure ci-dessus + `requirements.txt` + README.
2. Définir le schéma SQLite et le script d'init.
3. `import_csv.py` : import du catalogue (tolérant aux colonnes variables ; n'exige que les deux clés).
4. `generate_qr.py` : génération des QR (format URL).
5. Endpoint fiche jeu `/jeu/<id>` (lecture) + écran prêt/retour (écriture, logique des deux cas).
6. Scanner caméra embarqué dans la page.
7. Catalogue public (vrac + filtre catégorie).
8. Page statistiques (agrégation par titre, jeux à zéro inclus).
9. Authentification par jeton + limitation de débit.
10. Déploiement VPS + HTTPS.

---

## 7. Points encore ouverts (à trancher avant ou pendant le dev)

- Colonnes exactes du **CSV** (non encore disponible, évolutif) — seules certitudes : `id_exemplaire`, `reference_titre`.
- **Stock de pochettes numérotées** à prévoir (extensible, sans plafond) — pas de dimensionnement par le pic.
- Double vue du **palmarès** (par titre / par exemplaire) : à confirmer.
- **Nom de domaine** à réserver.
- Rédaction de la **politique de confidentialité** (liée à la newsletter, hors ce dépôt).

---

## 8. Pour connecter GitHub dans Cowork

1. Réglages → Connecteurs → ajouter **GitHub** (autorisation OAuth, choisir les dépôts autorisés).
2. Demander à l'agent de créer le dépôt `pret-jeux` et d'y générer la structure du §4.
3. Les actions en écriture (création de dépôt, commits) demandent une **approbation explicite** — valider au cas par cas.

*Les modalités exactes peuvent évoluer ; vérifier l'interface de l'application au moment de la connexion.*
