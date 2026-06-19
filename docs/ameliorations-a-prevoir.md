# Améliorations à prévoir — backlog

Liste vivante des modifications à intégrer lors d'une prochaine session de
développement. Alimentée par les retours du CA et des bénévoles. Quand un point
est traité, le cocher (et le décrire dans `CLAUDE.md`).

Format d'un point : intitulé, besoin, décisions/notes de mise en œuvre.

---

## À faire

### 1. Bouton admin « Clôturer tous les prêts en cours » (fin d'événement)
- **Besoin** : après l'événement, remettre tout le parc « disponible » et libérer
  les numéros d'emplacement, pour repartir propre à la session suivante.
- **Décision** : on CLÔTURE les prêts en cours, on **ne supprime PAS** l'historique
  (les statistiques restent ; les stats par édition s'obtiennent via le filtre de
  période). La suppression complète de l'historique n'est pas exposée.
- **Mise en œuvre prévue** :
  - `services.cloturer_tous_les_prets(conn)` : `UPDATE prets SET date_retour =
    maintenant WHERE date_retour IS NULL` + `UPDATE pochettes SET occupe = 0` ;
    renvoie le nombre de prêts clôturés.
  - Route POST protégée `/admin/cloturer-prets` + bouton dans une section
    « Fin d'événement » du tableau de bord admin.
  - Sécurité : confirmation explicite (comme la réinitialisation du jeton),
    message de retour « X prêts clôturés », et rappel d'exporter les
    statistiques au préalable.
  - Pas de changement du modèle de données.

### 2. Vue « Jeux actuellement sortis » (depuis la page statistiques)
- **Besoin** : voir d'un coup d'œil la liste des jeux encore dehors (relancer les
  retours manquants, surtout en fin d'événement).
- **Emplacement** : accessible depuis la page **Statistiques** (section dédiée,
  par ex. en haut, ou via un lien/onglet).
- **Contenu** : présentée en **deux blocs distincts** —
  1. « Prêtés au public » : nom, code (`id_exemplaire`), **numéro d'emplacement**,
     heure de sortie ;
  2. « En tournoi » : nom, code, heure de sortie (sans emplacement).
  Trié par date de sortie dans chaque bloc.
- **Mise en œuvre prévue** :
  - `services.lister_prets_en_cours(conn)` : `SELECT ... FROM prets p JOIN
    exemplaires e JOIN titres t WHERE p.date_retour IS NULL ORDER BY
    p.date_sortie` ; séparer par `motif` ('pret' vs 'tournoi', voir point 3).
  - Affichage dans `stats.html` (réutiliser le style de la table « Détail »).
  - Bon compagnon du bouton « clôturer tous les prêts » (point 1) : on visualise
    ce qui reste sorti avant de clôturer.

### 3. Sortie « tournoi » (jeux prélevés pour un tournoi, hors statistiques)
- **Besoin** : prélever des jeux pour un tournoi pendant l'événement. Ils doivent
  apparaître **sortis** (donc indisponibles), mais **sans PI / sans emplacement**,
  et **ne pas compter dans les statistiques** (ce ne sont pas des prêts au public).
- **Décision (validée)** :
  - Ajouter une colonne **`motif`** à `prets` : `pret` (défaut) ou `tournoi`.
    L'état « sorti » reste déduit de `date_retour IS NULL` → aucune modification
    de la logique de disponibilité (catalogue, fiche, scan).
  - Les **statistiques filtrent `motif = 'pret'`** (stats_globales, palmares,
    prets_par_heure, lister_prets_periode) → les tournois en sont exclus.
  - La sortie tournoi **n'attribue pas d'emplacement** ; `numero_pochette`
    recevra un marqueur « pas d'emplacement » (colonne NOT NULL aujourd'hui →
    sentinelle, sans rebuild de table).
- **UI** : sur l'écran `/pret/<id>` d'un jeu DISPONIBLE, bouton principal
  « Prêter » + bouton secondaire **« Sortir pour un tournoi »**. Au retour,
  l'écran affiche « Sorti (tournoi) » + « Rendre » (pas de pochette à libérer).
- **Lien avec le point 2** : la vue « Jeux actuellement sortis » inclut les
  tournois dans un bloc séparé (le parc doit les voir, même si les stats les
  ignorent).
- **Mise en œuvre prévue** : migration colonne `motif` (`_appliquer_migrations`) ;
  `services.sortir_tournoi(conn, id)` + prise en compte dans `rendre` ; filtre
  `motif='pret'` dans les fonctions de stats ; bouton + route POST
  `/pret/<id>/tournoi`.

### 4. Menu de navigation bénévole
- **Besoin** : un petit menu pour passer facilement d'un module à l'autre.
- **Emplacement** : bandeau en haut de page (présent partout).
- **Contenu validé** :
  - Catalogue
  - Scanner (prêt / retour)
  - Statistiques
  - Aide / mode d'emploi *(nouvelle page courte à créer — point 5)*
  - Jeux actuellement sortis *(point 2)*
- **Décision (validée)** : le menu n'apparaît **que sur les appareils ayant
  activé le jeton bénévole** (cookie valide). Le public ordinaire ne voit que le
  bandeau simple → pas de « Accès réservé » intempestif. Le tableau de bord admin
  affiche toujours le menu (admin connecté, voir point 6).
- **Mise en œuvre prévue** :
  - Exposer aux gabarits un indicateur `est_benevole` (calculé via
    `auth.acces_valide(request)`), p. ex. en global Jinja ou context processor.
  - Dans `base.html` : `{% if est_benevole %}` … menu … `{% endif %}` + style CSS.
  - Aucune autre logique : `acces_valide` existe déjà.

### 5. Page d'aide / mode d'emploi bénévole
- **Besoin** : page courte expliquant aux bénévoles comment scanner, prêter,
  rendre, re-prêter, et le principe du numéro d'emplacement.
- **Mise en œuvre prévue** : un gabarit statique `aide.html` + une route GET
  `/aide` (publique ou bénévole), liée depuis le menu (point 4).

### 6. Menu admin = menu bénévole + actions d'administration
- **Besoin** : le tableau de bord admin doit **inclure automatiquement** toutes
  les entrées du menu bénévole (point 4), en plus des actions d'admin (créer un
  jeu, réimprimer, jeton, mot de passe, clôturer les prêts…).
- **Décision** : définir le menu bénévole **une seule fois** dans un fragment de
  gabarit partagé (ex. `templates/_menu_benevole.html`), inclus à la fois par le
  bandeau (point 4) et par `admin_dashboard.html`. Ainsi toute évolution du menu
  bénévole se répercute automatiquement côté admin (pas de double maintenance).
- **Mise en œuvre prévue** : `{% include "_menu_benevole.html" %}` dans le
  dashboard admin, sous une section « Modules » ; les actions d'admin restent
  dans leur propre section.

### 7. Durée de validité du jeton bénévole (fusionnée avec le cookie)
- **Besoin** : choisir, à la création/réinitialisation du jeton, jusqu'à quand
  l'accès bénévole est valable (ex. le week-end de l'événement).
- **Décision (validée)** — un **seul** réglage « valable jusqu'au » :
  - Champ date/heure de fin sur l'écran de réinitialisation (`/admin/jeton`).
  - Cette date régit À LA FOIS l'expiration du **cookie** (max_age = date − maintenant)
    et celle du **jeton** côté serveur (accès refusé au-delà).
  - **Sans date de fin** : durée par défaut **1 semaine** (7 jours), et cette
    valeur par défaut est **affichée sur l'écran de création du jeton**.
- **Point de logique à respecter** : distinguer « pas de jeton configuré » (=
  accès OUVERT, mode dev) de « jeton EXPIRÉ » (= accès FERMÉ, refusé). L'expiration
  ferme l'accès, ne l'ouvre pas.
- **Mise en œuvre prévue** :
  - Stocker en base (table `parametres`) le jeton + sa date d'expiration
    (`pret_token_expire`, UTC ISO).
  - `acces_valide` : si un jeton existe et est expiré → refus ; si valide →
    comparer le cookie ; si aucun jeton → ouvert.
  - `/acces` pose le cookie avec `max_age` = (expiration − maintenant), ou 7 j
    par défaut.
  - Page `/admin/jeton` : champ « valable jusqu'au » + mention du défaut (1 semaine).

### 8. Durée de prêt (par prêt + durée moyenne)
- **Besoin** : afficher la durée de chaque prêt dans la liste détaillée, et une
  **durée moyenne de prêt** dans les indicateurs de synthèse.
- **Décisions** :
  - Par prêt : `date_retour − date_sortie`. Pour un prêt **en cours**, afficher
    « en cours (depuis X) » plutôt qu'une durée figée.
  - Moyenne : calculée sur les prêts **terminés** uniquement (ceux ayant une
    `date_retour`), **hors tournois** (`motif = 'pret'`, cf. point 3), et dans la
    **période filtrée** si active.
- **Mise en œuvre prévue** :
  - Helper `format_duree(secondes)` → « 45 min », « 2 h 15 », « 3 j 4 h ».
  - `lister_prets_periode` : ajouter `duree_txt` (et l'inclure dans les exports
    Excel/PDF — colonne « Durée »).
  - `stats_globales` : ajouter `duree_moyenne` (AVG via `julianday(date_retour) −
    julianday(date_sortie)` côté SQL, ou calcul Python), affichée dans la synthèse
    et les exports.

---

## Retours en attente de tri
_(à compléter au fil des messages du CA — idées, bugs, ajustements)_

-
