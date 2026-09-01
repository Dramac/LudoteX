# Conception — Module « Programme du week-end »

**Objet :** élément de programme hors tournoi (animation, atelier, initiation,
temps fort, intervention partenaire), saisi dans l'application et annoncé sur
l'écran de salle, la page d'accueil et une page publique dédiée.
**Statut :** conception validée, prête à implémenter. Décisions du §2 arrêtées
avec Simon le 26 juillet 2026.
**Base de comparaison :** LudoteX 1.1.0, 410 tests verts.

---

## 1. Intention

Le public dispose aujourd'hui d'une information sur les tournois, et de rien
d'autre : animations ponctuelles, ateliers, initiations, temps forts et
interventions d'associations partenaires ne vivent que sur l'affichage papier et
le bouche-à-oreille. L'application porte déjà toute l'infrastructure d'affichage
horaire (frise de l'accueil, écran de salle, `.ics`) — il manque la donnée.

L'objectif n'est pas de faire un second module Tournois sans score : c'est de
répondre à une seule question, celle que se pose un visiteur planté au milieu de
la salle — **« qu'est-ce qui commence maintenant ? »** — sans qu'il ait à savoir
si la réponse est un tournoi ou un atelier.

---

## 2. Décisions arrêtées

| # | Sujet | Décision | Conséquence de conception |
|---|---|---|---|
| 1 | Écran de salle | **Troisième colonne dédiée**, panneau « Animations », avec un nombre de pistes **adaptatif** | La grille `.colonnes` passe de deux pistes figées à 1–3 pistes calculées ; aucune piste vide n'est conservée |
| 2 | Accès à la saisie | **Bénévole (jeton)**, comme les tournois | Routes de gestion derrière `exiger_jeton`, lien au menu bénévole ; l'entrée apparaît **aussi** au tableau de bord admin, qui inclut ce même fragment de menu |
| 3 | Surfaces publiques | **Page `/programme`** (grille du week-end) **et** bloc « ce qui commence » sur l'accueil, en plus de l'écran de salle | Trois surfaces, **un seul service** de fusion — jamais deux calculs concurrents |
| 4 | Types d'éléments | **Liste configurable en administration** | Petite table + CRUD admin sur le patron de `emplacements_rangement` ; aucune liste à figer aujourd'hui |

Deux précisions sur la décision 2, qui peut sembler s'écarter de la demande
initiale (« un nouveau sous-menu de l'interface d'administration ») :

- la **configuration** reste au bureau : la liste des types est gérée dans le
  groupe « Événement » du tableau de bord admin, à côté de « Date de
  l'événement » et « Écran de salle » ;
- l'**exploitation** est aux bénévoles : créer, décaler ou annuler un créneau le
  jour J ne doit pas exiger le mot de passe du bureau. Comme
  `admin_dashboard.html` inclut `_menu_benevole.html`, l'entrée « Programme »
  sera de toute façon présente dans l'interface d'administration.

---

## 3. Principes du projet, appliqués ici

- **Bases cloisonnées.** Tout vit dans `data/tournoi.db` (la base qui porte déjà
  la logique de créneau), **sans aucune clé étrangère** vers la base de prêt.
- **Zéro donnée personnelle.** Un élément de programme n'a ni inscription, ni
  participant, ni contact. La jauge est un nombre indicatif, pas une liste.
- **Jamais bloquant.** Un horaire incohérent (fin avant début), un type archivé,
  un identifiant inconnu : message clair et action de rattrapage, jamais
  d'erreur brute.
- **Séparation lecture / écriture.** `/programme` et l'écran de salle
  n'exposent aucune action.
- **Module désactivable.** Entrée dans `app/modules.py::MODULES`,
  `garde_module("programme")` sur le routeur, liens conditionnés par
  `module_visible` (leçon de la fiche A3 : pas de lien en dur vers un module
  masqué).
- **Vocabulaire.** `docs/vocabulaire.md` : on dit **« lieu »**, jamais
  « emplacement » (réservé au rangement) ni « pochette ».
- **Pas d'héritage de schéma.** Le tournoi n'est **pas** refactorisé en
  spécialisation d'un élément de programme : 17 colonnes de compétition et
  ~150 tests pour zéro bénéfice visible, contre le risque de régression. On
  mutualise ce qui l'est déjà (frise, `.ics`, helpers de fuseau) et rien de
  plus.

---

## 4. Modèle de données

Deux tables nouvelles dans `data/tournoi.db` (DDL dans
`app/tournoi/models.py`, exécution et migrations dans `app/tournoi/db.py`).

### 4.1 `types_programme` — la liste configurable (décision 4)

Calquée sur `emplacements_rangement` (patron éprouvé : liste, création,
renommage propagé, archivage doux, réordonnancement).

```
id_type    INTEGER PK AUTOINCREMENT
nom        TEXT NOT NULL          -- « Atelier », « Initiation », « Temps fort »…
icone      TEXT                   -- emoji facultatif, affiché sur /live et /programme
actif      INTEGER NOT NULL DEFAULT 1   -- 0 = archivé (retrait doux, jamais supprimé sous un élément)
ordre      INTEGER NOT NULL DEFAULT 0
```

Amorçage (`seed`) avec les cinq types de la note — animation, atelier,
initiation, temps fort, intervention partenaire — pour que le module soit
utilisable sans configuration préalable. Le bureau renomme ou archive ensuite.

### 4.2 `programme` — les éléments

```
id_element     INTEGER PK AUTOINCREMENT
intitule       TEXT NOT NULL              -- ce qui s'affiche en grand
description    TEXT                       -- description courte (facultative)
id_type        INTEGER                    -- FK -> types_programme, nullable (type supprimé/non renseigné)
date_heure     TEXT                       -- début, ISO 8601 UTC ; nullable comme pour les tournois
duree_min      INTEGER                    -- durée en minutes (déduite de l'heure de fin saisie)
lieu           TEXT                       -- zone ou table dans la salle
public_vise    TEXT                       -- indicatif (« tout public », « famille », « 8 ans et + »)
jauge          INTEGER                    -- purement informative, aucune réservation
etat           TEXT NOT NULL DEFAULT 'brouillon'   -- 'brouillon' | 'publie' | 'annule'
date_creation  TEXT NOT NULL
```

Trois choix à expliciter :

- **`duree_min` plutôt qu'une heure de fin**, alors que la note parle d'heure de
  fin. Motif : la frise horaire existante (`tournoi.services.planning`) calcule
  ses blocs à partir de `date_heure` + `duree_min`, avec un défaut
  `DUREE_DEFAUT_MIN` de 60 min. Stocker une durée permet de réutiliser la frise
  **sans la modifier**. Le formulaire, lui, demande bien une **heure de fin** et
  en déduit la durée — c'est exactement ce que fait déjà
  `planning.services.modifier_creneau`.
- **Un état à trois valeurs.** `brouillon` permet de préparer le programme des
  semaines à l'avance sans rien publier (les tournois ont le même besoin, réglé
  par le même mot). `annule` est nécessaire pour l'écran de salle : un atelier
  annulé doit pouvoir s'afficher barré pendant une heure plutôt que disparaître
  sans explication devant des gens qui l'attendent.
- **`id_type` nullable.** Un type archivé ne doit jamais empêcher d'afficher un
  élément déjà saisi. Aucune suppression en cascade.

Index : `date_heure` (toutes les surfaces trient par heure), `etat`.

---

## 5. Services

Nouveau module `app/tournoi/programme.py` (logique) — le sous-paquet s'appelle
`tournoi` et hébergera du non-tournoi : à documenter en en-tête plutôt que de
renommer des dizaines de fichiers pour rien.

- CRUD : `creer_element`, `modifier_element`, `supprimer_element`,
  `changer_etat` (`brouillon` ↔ `publie` ↔ `annule`), `get_element`,
  `lister_elements(jour=None, id_type=None, inclure_brouillons=False)`.
- Types : `lister_types(actifs_seulement=True)`, `creer_type`,
  `renommer_type`, `archiver_type`, `reactiver_type`, `reordonner_types`,
  `supprimer_type` (refusé si des éléments y sont rattachés — patron identique à
  celui des emplacements de rangement).
- Frise : réutilisation **telle quelle** de `planning(conn, jours)` après
  extraction des helpers de créneau partagés (`SLOT_MIN`, `DUREE_DEFAUT_MIN`,
  `label_jour`, `_calculer_couloirs`) dans un module commun du sous-paquet.
  Aucune signature publique de `tournoi.services` ne change.
- `.ics` : `ical_element(conn, id)`, sur le patron de `ical_tournoi`.
- **Fusion — le cœur du module :**

```
imminents(conn, minutes) -> list[dict]
```

Renvoie tournois **et** éléments de programme dont le début tombe entre
maintenant et +`minutes`, triés par heure croissante, chaque entrée portant un
champ `source` (`"tournoi"` / `"programme"`), son `icone`, son heure locale
formatée et son `minutes_avant`. **Une seule implémentation**, consommée par
les trois surfaces avec des fenêtres différentes : 60 min pour l'accueil,
120 min pour l'écran de salle. `tournois_imminents` reste en place (utilisée
telle quelle à l'intérieur) : aucune régression sur l'existant.

---

## 6. Écrans et routes

### 6.1 Public

- `GET /programme` — grille horaire du week-end, filtrable par **jour** et par
  **type**. Réutilise le panneau `<details class="recherche">` et le patron des
  puces de filtres actifs du catalogue (retrait d'un filtre en un tap). Rendu
  hybride sans JS de la frise existante : grille CSS sur grand écran, agenda
  empilé sous 640 px. Page `.contenu-large` (leçon du bridage à 540 px).
- `GET /programme/{id}/agenda.ics` — « Ajouter à mon agenda », comme les
  tournois.
- `GET /programme/aide` — mode d'emploi (convention : chaque module a sa page
  d'aide, liée depuis `/aide` sous garde `module_visible`).

### 6.2 Bénévole (jeton)

- `GET /programme/gestion` — liste de travail, brouillons compris.
- `GET|POST /programme/nouveau`, `GET|POST /programme/{id}/editer`,
  `GET|POST /programme/{id}/supprimer` (double confirmation, comme les
  tournois), `POST /programme/{id}/etat`.
- `POST /programme/{id}/dupliquer` — un atelier se rejoue souvent le dimanche à
  la même heure ; le patron `dupliquer_tournoi` existe et se recopie.

### 6.3 Administration (mot de passe)

- `GET|POST /admin/programme-types` — CRUD de la liste des types, dans le groupe
  **« Événement »** du tableau de bord, entre « Date de l'événement » et
  « Écran de salle ».

### 6.4 Écran de salle — la troisième colonne

État actuel : `.colonnes` est une grille `1fr 1fr` (Tournois | Derniers prêts &
retours) avec un unique repli `sans-tournois` qui masque le panneau des tournois
et passe à `1fr`. Cette mécanique binaire ne suffit plus à trois panneaux : elle
est remplacée par un **nombre de pistes calculé**, le panneau des mouvements
étant le seul toujours présent.

| Tournois | Animations | Rendu |
|---|---|---|
| oui | oui | 3 pistes égales |
| oui | non | 2 pistes (état actuel) |
| non | oui | 2 pistes : Animations \| Mouvements |
| non | non | 1 piste : les mouvements en pleine largeur |

**Panneaux activables en administration** (décision Simon du 26 juillet 2026).
Les quatre blocs de l'écran — panneau Tournois, panneau Derniers prêts &
retours, barre de chiffres, panneau Animations — deviennent activables ou
désactivables depuis `/admin/ecran-salle`, la page qui porte déjà le titre,
l'annonce et sa durée : quatre clés dans `parametres`, via
`lire_parametre`/`ecrire_parametre`, **aucune page nouvelle**, aucun mécanisme
nouveau. Réglage **global** : un seul jeu d'interrupteurs vaut pour tout écran
ouvrant `/live` (la surcharge par l'URL a été écartée — deux sources de vérité
à expliquer pour un besoin hypothétique).

Trois règles encadrent ce réglage :

- **Précédence.** Un module désactivé dans `/admin/fonctionnalites` l'emporte
  toujours : son panneau ne s'affiche pas, quel que soit le réglage de l'écran.
  Les deux réglages ne disent pas la même chose — le module dit *si la
  fonctionnalité existe pour l'application*, l'écran de salle dit *ce que cet
  écran projeté montre*. À écrire tel quel dans l'aide, faute de quoi le bureau
  cherchera dix minutes le jour J pourquoi un panneau reste vide.
- **Un panneau désactivé n'est pas collecté.** `_collecter_donnees()` ne calcule
  ni n'expose ses données dans `/live/data` — pas de champ vide, et une requête
  de moins.
- **Cas dégénéré assumé.** Tout désactiver laisse un écran titre + horloge +
  annonce. C'est un usage légitime (écran d'annonces seules), donc pas de
  blocage : un simple avertissement en administration, dans l'esprit
  « jamais bloquant ».

Le nombre de pistes se calcule donc sur les panneaux **activés ET non vides** :
la table ci-dessus reste vraie, avec « oui » = « activé et non vide ».

Le calcul se fait **dans `rendre(d)`**, donc au chargement comme à chaque
rafraîchissement (précédent : la bascule `sans-tournois`). Points de vigilance
propres à un écran projeté lu depuis le fond de la salle :

- à trois pistes, chaque panneau perd un tiers de largeur : les tailles de
  police du panneau et le nombre de lignes du nom (`-webkit-line-clamp`, deux
  lignes aujourd'hui) doivent être réglés **à trois colonnes**, puis vérifiés à
  deux ;
- le plus proche élément à venir garde le surlignage « BIENTÔT » et le
  « dans N min » déjà en place pour les tournois — même traitement pour les
  deux sources, sinon l'œil croit à une hiérarchie qui n'existe pas ;
- un élément `annule` s'affiche barré avec la mention « annulé » pendant sa
  fenêtre, puis disparaît.

**Sécurité (raison invoquée par Simon pour cloisonner) :** l'intitulé, la
description et le lieu sont de la **saisie libre affichée sur une page
publique** ; ils sont injectés par `textContent`, jamais `innerHTML` — même
règle que les annonces de salle, déjà appliquée dans `live.html`.

**Deux gardes à ne pas oublier dans `_collecter_donnees()` :**

1. si le module `programme` est désactivé, aucune donnée de programme n'est
   collectée ni exposée par `/live/data` (le champ est absent, jamais vide) ;
2. **défaut préexistant à corriger au passage** : `routes/live.py` interroge la
   base des tournois **sans vérifier l'état du module `tournois`**, alors que la
   page d'accueil, elle, saute entièrement ce calcul quand le module est
   désactivé (correctif de la fiche A3). L'écran de salle annonce donc
   aujourd'hui des tournois d'un module masqué. À aligner dans le même lot,
   puisqu'on touche exactement à ce code.

### 6.5 Page d'accueil

Le bloc « tournois qui commencent dans l'heure » devient « **ce qui commence
dans l'heure** » et consomme `imminents(conn, 60)`. La frise deux jours
existante gagne les éléments de programme (mêmes couloirs, même rendu). Si l'un
des deux modules est désactivé, sa source disparaît sans que le bloc casse.

---

## 7. Non-régression exigée

- Les ~150 tests du module Tournois doivent passer **sans modification** : aucun
  changement de schéma des tournois, aucune signature publique de
  `tournoi.services` modifiée (les helpers extraits sont réexportés).
- Les tests existants de `/live` (route 200, endpoint JSON, **absence du numéro
  de pochette**, `minutes_avant`) restent verts ; celui de l'accueil aussi.
- `app/sauvegarde.py` valide les trois bases et les migre après restauration :
  ajouter un test de **restauration d'une sauvegarde antérieure** aux deux
  nouvelles tables, comme cela a été fait pour D5.

---

## 8. Plan d'implémentation — un commit par étape

1. **Schéma + migrations + seed des types.** `models.py`, `db.py`. Tests :
   création, idempotence des migrations, seed, restauration d'une sauvegarde
   ancienne.
2. **Services types + éléments (CRUD, états).** Tests unitaires, dont les cas
   limites : fin avant début, type archivé, suppression d'un type rattaché.
3. **Service de fusion `imminents` + extraction des helpers de créneau.** Tests :
   ordre chronologique, fenêtre, sources mélangées, brouillons exclus, absence
   d'un module.
4. **Écrans bénévole** (liste, création, édition, suppression, duplication,
   état) + entrée `MODULES` + `garde_module` + lien de menu gardé. Tests de
   routes, dont l'accès sans jeton.
5. **CRUD admin des types** dans le groupe « Événement ». Tests : garde admin,
   archivage, refus de suppression si rattaché.
6. **Page publique `/programme`** (grille, filtres, puces), `.ics`,
   `/programme/aide`, lien depuis `/aide`.
7. **Écran de salle** — sécable en deux moitiés, dont la première **ne dépend
   pas du jalon 1** et peut donc être menée en parallèle :
   - **7a (indépendante)** : interrupteurs des panneaux sur
     `/admin/ecran-salle`, passage du repli binaire `sans-tournois` à un nombre
     de pistes calculé, correctif de la garde `tournois` manquante, aide et
     wiki correspondants ;
   - **7b (après le jalon 1)** : branchement du panneau Animations sur
     `imminents`, réglages typographiques à trois pistes, éléments annulés
     barrés.
8. **Page d'accueil** : bloc fusionné + frise.
9. **Wiki** (dans les commits concernés, pas en fin de course) : nouvelle page
   `Module-Programme.md` ; mises à jour de `Fonctionnalites.md`, `Home.md`,
   `Module-Ecran-Salle.md` (la troisième colonne, et l'annonce de salle qui n'y
   est toujours pas documentée), `Module-Tournois.md` (frontière entre tournoi
   et animation), `Guide-Benevole.md`, `Glossaire.md` (« élément de
   programme »), `Avant-pendant-apres.md`.
10. **Version** : montée **mineure** proposée en fin de chantier (`1.2.0`) —
    nouveau module, aucune casse, aucune intervention de déploiement au-delà
    d'`update.sh`. Les trois porteurs du numéro (`app/version.py`, `VERSION`,
    `CHANGELOG.md`) mis à jour dans le même commit, tag `v1.2.0` à poser après
    le push.

Estimation : 3 à 4 sessions. Les étapes 1 à 4 forment un premier jalon
utilisable (le programme se saisit et se consulte côté bénévole) ; 6 à 8 sont
les surfaces publiques et peuvent être livrées séparément.

---

## 9. Points laissés ouverts

- **Inscription à une animation** : hors périmètre, comme le dit la note. Si le
  besoin apparaît, le modèle sans e-mail des tournois (pseudo + code de
  désinscription) se transpose tel quel.
- **Flux iCal de tout l'événement** (idée 5.4 de `docs/idees-evolutions.md`) :
  devient presque gratuit une fois `imminents` et `ical_element` écrits — à
  proposer après, pas dans ce lot.
- **Appariements de tournoi sur l'écran de salle** (idée 3.1) : la troisième
  colonne consomme la place qu'aurait prise cette idée. À rouvrir en connaissance
  de cause, une fois la disposition à trois pistes éprouvée sur le projecteur.
- **Liste des types définitive** : le bureau tranchera dans l'écran
  d'administration, sans développement (c'était l'arbitrage 3 de la note, qui
  n'est donc plus bloquant).

---

## 10. Révision (09/08/2026) — une page publique par élément

Le §6.1 ci-dessus écartait délibérément une page de détail pour un élément de
programme (« un élément n'a que des champs simples »). À l'usage, l'absence se
voyait : dans la frise de l'accueil et sur `/programme`, un tournoi est
cliquable et une animation ne l'est pas, sans que rien n'explique au visiteur
la différence — et un élément de programme n'avait aucun moyen d'apprendre son
annulation une fois mis à l'agenda.

**Décision révisée** : `GET /programme/{id}` (patron `tournoi.routes.detail`),
page publique en LECTURE seule (toujours pas d'écran de *gestion* dédié — les
actions bénévole restent sur `/programme/gestion`, §6.2 inchangé). Un
**brouillon** y est traité comme un identifiant inconnu (404, jamais public).
Un élément **annulé** reste accessible avec un bandeau, cohérent avec `/live`
qui l'affiche déjà barré pendant sa fenêtre. Le bouton « 📅 Ajouter à mon
agenda » (`/programme/{id}/agenda.ics`, déjà écrit au jalon 2) **quitte le bloc
de grille** pour vivre sur cette page — l'avoir gardé dans un bloc devenu
cliquable aurait produit un `<a>` imbriqué. Les blocs de `/programme`, de la
frise de l'accueil et du bloc « ça commence bientôt » pointent désormais vers
cette page, comme un bloc de tournoi pointe vers la sienne.
