# Note de conception — Suivi de l'emplacement de rangement

**Statut :** proposition à valider par Simon **AVANT tout développement**. Ce
document fige le périmètre et les décisions ; il ne code rien.

À lire avec `docs/conception-tournois.md` et `docs/conception-planning.md` (même
logique de module intégré) et surtout `docs/evolution-prets-longue-duree.md`, qui
décrit le futur module « prêts longue durée au local » que cette fonctionnalité
doit préparer sans le bloquer.

---

## 1. Objectif

Aujourd'hui, au **retour** d'une boîte, l'écran bénévole indique où récupérer la
pièce d'identité (numéro d'emplacement, en très gros). Il faut lui ajouter une
seconde information, tout aussi utile pour fluidifier la journée : **où reranger
le jeu** (ex. « Étagère 2, 3ᵉ étage, zone Jeux duel »).

La même donnée d'emplacement doit pouvoir servir, plus tard et **hors événement**,
au rangement du **local de l'association** (pour d'éventuels prêts longue durée).
Ce module futur n'est **pas dans le périmètre** ; mais le modèle retenu ici ne
doit rien lui interdire.

Contrainte cardinale, répétée partout : **le rangement ne touche JAMAIS à la
logique de prêt / pochettes.** Deux clés stables intactes, zéro donnée
personnelle (un nom d'étagère n'en est pas une), jamais bloquant. Renseigner ou
non un emplacement n'a **aucun effet** sur l'état prêté/disponible d'une boîte.

## 2. Deux contextes de rangement (décision actée)

Un même exemplaire a **deux emplacements possibles**, portés par la boîte
physique (pas par le titre : deux copies d'un même jeu peuvent vivre à deux
endroits) :

- **Emplacement ÉVÉNEMENT** — où se range la boîte dans la salle pendant le
  festival. La salle change chaque année → **texte libre**.
- **Emplacement LOCAL** — où se range la boîte au local de l'asso, hors
  événement → **choix dans une liste** gérée en admin (voir §5).

Les deux sont **nullables**. Un **réglage global « contexte de rangement
actif »** (`parametres`, valeurs `evenement` — défaut — ou `local`) détermine
lequel des deux les écrans lisent et écrivent à un instant donné. Pendant le
festival : `evenement`. Le futur module longue durée n'aura qu'à basculer ce
réglage sur `local` — **aucun routage conditionnel dans le code**, l'isolation
tient au seul réglage (même esprit que le `MODE_FORMATION`).

## 3. Modèle de données (base de prêt existante)

L'emplacement décrit une **boîte** → il vit dans la table `exemplaires` de la
base de prêt (`app.db`). Pas de base séparée : contrairement au planning
(données perso), un nom d'étagère n'a aucune sensibilité RGPD et est
intrinsèquement lié à l'exemplaire.

**Deux colonnes ajoutées à `exemplaires`** (nullables), via le mécanisme de
migration existant (`_MIGRATIONS_COLONNES` dans `app/db.py`, ALTER TABLE
idempotent — compatibilité totale avec les bases déjà en service) :

| colonne | type | rôle |
|---|---|---|
| `emplacement_evenement` | `TEXT` | libellé libre saisi par le bénévole (contexte événement) |
| `emplacement_local_id` | `INTEGER` (FK nullable) | référence une ligne de `emplacements_rangement` (contexte local) |

**Pourquoi une FK pour le local et du texte libre pour l'événement ?** Parce que
les deux formats de saisie décidés l'imposent (§2), et parce que la FK donne au
local une **source unique de vérité** : renommer « valise 1 » en « Valise bleue »
dans la liste se **répercute automatiquement** sur toutes les boîtes qui la
pointent, sans réécriture. Le texte libre événement n'a pas besoin de ça (il est
refait chaque année).

**Nouvelle petite table `emplacements_rangement`** (base de prêt) — la liste
gérée en admin :

| champ | rôle |
|---|---|
| `id_emplacement` | PK auto |
| `nom` | libellé affiché (ex. « Totem », « valise 1 ») |
| `actif` | 0/1 — 1 = proposé dans les menus ; 0 = **archivé** (voir §5) |
| `ordre` | entier pour trier l'affichage |

Premier remplissage (seed à la création de la table) : **Totem, Puzzle, P'tits
potes, valise 1, valise 2**.

Réglages globaux, dans `parametres` (clé/valeur, pas de nouvelle table) :

- `rangement_contexte` → `evenement` (défaut) / `local`.
- `rangement_visibilite` → `tous` / `benevoles` (défaut) / `admin` (voir §7).

## 4. Saisie — trois canaux cumulés

### 4.a Mode rangement au scanner (canal principal, pour équiper ~700 boîtes)

Le geste de masse. Le bénévole **active le mode**, choisit **une fois**
l'emplacement, puis scanne les boîtes en rafale : chaque scan **affecte
l'emplacement actif** à la boîte (dans le contexte actif) au lieu d'ouvrir
l'écran de prêt. Confirmation visible, enchaînement immédiat.

**Où vit « l'emplacement actif » ? → cookie d'appareil (recommandé).** C'est le
point le plus structurant. Trois pistes ont été pesées :

- **Paramètre serveur partagé** — *rejeté* : plusieurs bénévoles rangent en
  parallèle dans des zones différentes. Un réglage global unique les ferait se
  marcher dessus (le choix de l'un écraserait celui de l'autre). Rédhibitoire.
- **Query param transporté à chaque redirection** — *rejeté* : fragile (perdu au
  moindre écart de navigation), URLs sales, et il faut le réinjecter partout.
- **Cookie d'appareil** — *retenu*. Comme le jeton bénévole : **propre à chaque
  téléphone**, donc chaque bénévole garde SON emplacement actif sans interférence.
  Persiste d'un scan à l'autre. Un cookie `rangement_actif` porte, selon le
  contexte, soit le texte libre (événement) soit l'`id_emplacement` (local).
  Lu **côté serveur** à chaque scan. Sortie du mode = suppression du cookie.

**Ergonomie sur `/scanner`** (JS léger inline, aucune dépendance nouvelle, dans
l'esprit minimaliste du reste du site) :

1. Sous la caméra, un bouton **« Activer le mode rangement »** déplie un petit
   formulaire : en contexte événement, un champ **texte libre** ; en contexte
   local, un **menu déroulant** des emplacements actifs. Validation → POST qui
   pose le cookie et revient sur `/scanner`.
2. Mode actif → un **bandeau d'état bien visible** (couleur distincte, ex. le
   bleu de rangement déjà utilisé pour le retour) reste en tête : « 🗄️ Mode
   rangement — Emplacement : **Étagère 2** · [Changer] · [Quitter le mode] ».
3. Tant que le bandeau est là, `scanner.js` lit un drapeau rendu côté serveur
   (ex. `<body data-rangement="1">`) et redirige chaque QR vers
   **`/scanner/ranger?code=<id>`** au lieu de `/pret/<id>`. Le serveur lit le
   cookie, **affecte** l'emplacement à la boîte (colonne du contexte actif), puis
   réaffiche `/scanner` avec une confirmation « ✓ *<nom du jeu>* rangé en
   **Étagère 2** », caméra prête pour la suivante.
4. **Saisie manuelle de secours** (`/scanner/saisie`) : en mode rangement, elle
   **affecte** aussi (même endpoint logique) au lieu d'ouvrir l'écran de prêt —
   cohérent avec le canal caméra. Hors mode, comportement inchangé.
5. **Quitter le mode** efface le cookie → retour au scan-vers-prêt normal.

Jamais bloquant : code inconnu → message + champ prêt à resservir (comme la
saisie manuelle existante). Affecter un emplacement à une boîte **actuellement
sortie** est permis (on enregistre son étagère d'origine pour son retour) et
**ne modifie ni prêt ni pochette**.

Protégé par le jeton bénévole (déjà le cas de tout `/scanner*`).

### 4.b Import / export CSV

Deux colonnes ajoutées aux en-têtes du catalogue (`EN_TETES_CATALOGUE` +
alias dans `scripts/import_csv.COLONNES`), export **ré-importable** :
**« Emplacement événement »** et **« Emplacement local »**. On exporte des
**libellés lisibles** (le NOM de l'emplacement local, pas son id).

Comportement à l'import :

- Événement : texte libre, stocké tel quel (trim).
- Local : on cherche le libellé dans `emplacements_rangement` (comparaison
  insensible à la casse/espaces). **Trouvé** → on pose la FK. **Absent et
  non vide** → **tolérant, jamais bloquant : on crée l'emplacement** dans la
  liste (actif=1) puis on l'affecte *(décidé)*. Le compte-rendu d'import signale
  « N nouveaux emplacements créés : … » pour que rien ne soit **silencieux**.
- **Case vide → n'efface jamais** *(décidé)* : un import laissant la colonne
  blanche **ne touche pas** à l'emplacement déjà posé (au scanner ou à la main).
  Un réimport partiel ne peut donc pas détruire du travail par mégarde. Pour
  **retirer** un emplacement, on passe explicitement par la fiche admin
  (« — aucun — ») ou la page des manques (§4.d).
- À la fin d'un import, un lien direct « il reste X boîtes sans emplacement —
  [les compléter] » renvoie vers la **page des manques** (§4.d).

### 4.d Page « boîtes non rangées » (combler les trous)

Une vue admin listant les exemplaires **sans emplacement dans le contexte
actif** — le pendant ciblé du mode rangement de masse. Un tableau avec **saisie
rapide en ligne** (texte libre pour l'événement, menu déroulant pour le local) et
la possibilité de **laisser vide** si vraiment il n'y a rien à mettre.

Sert après un import (« X boîtes à compléter ») comme en permanence (checklist
des trous). Sur ~700 boîtes la liste peut être longue au démarrage → **filtrable**
(catégorie / nom) et paginée sobrement. Jamais bloquant : rien n'oblige à tout
remplir.

### 4.c Fiche admin (édition à l'unité)

Sur la fiche admin d'un exemplaire : **texte libre** pour l'événement, **menu
déroulant** (emplacements actifs) pour le local. Le menu inclut une option
« — aucun — » pour retirer, et affiche l'emplacement courant même s'il est
archivé (voir §5).

## 5. Gestion de la liste des emplacements locaux

Écran admin dédié (voir §8) : **ajouter**, **renommer**, **réordonner**,
**retirer**. Le point délicat est le sort des boîtes qui pointent vers un
emplacement modifié — objectif : **jamais bloquant, pas de perte silencieuse**.

- **Renommer** = `UPDATE` du `nom`. Grâce à la FK, ça se répercute partout
  automatiquement. Zéro divergence.
- **Retirer** = **archivage doux** (`actif = 0`), **pas de suppression dure par
  défaut**. L'emplacement disparaît des menus de saisie, mais les boîtes qui le
  pointent **gardent leur référence et continuent de l'afficher**, marqué
  « (archivé) » côté admin. On peut le **réactiver** ou **réaffecter** les boîtes
  concernées. Aucune donnée perdue en silence.
- **Suppression dure** proposée **seulement** si l'emplacement n'est référencé
  par aucune boîte (compteur affiché à côté de chaque entrée). Sinon, l'admin est
  invité à réaffecter d'abord — jamais un `DELETE` qui orpheline une FK.

## 6. Affichage au retour (écran bénévole)

Sur `/pret/<id>`, résultat **`rendu`** : sous le numéro de pochette (déjà en très
gros), un **second bloc « 🗄️ Où ranger le jeu »** affichant l'emplacement du
**contexte actif**. Même gabarit visuel que le bloc pochette, teinte de rangement
distincte du vert de retour et du numéro.

Cas **`rendu_tournoi`** (retour d'une sortie tournoi, sans pochette) : la boîte
doit **aussi** être rerangée → on affiche le même bloc « où ranger » (c'est le
seul contenu utile de cet écran, aujourd'hui presque vide). La **sortie** tournoi
elle-même, comme le prêt et le re-prêt, **n'affiche rien** sur le rangement (on
sort la boîte, on ne la range pas) et **ne touche pas** aux colonnes
d'emplacement.

**Jamais bloquant** : emplacement non renseigné → **on n'affiche rien** (pas de
« non renseigné » anxiogène). Le mode rangement (§4.a) est justement le moment où
le bénévole comble ces trous.

## 7. Affichage catalogue & fiche publique (visibilité réglable)

L'emplacement peut aider un visiteur à reranger, ou rester réservé à l'équipe.
Réglage admin **à trois niveaux**, inspiré de la visibilité par module de
`/admin/fonctionnalites` **sans en importer la complexité** (pas un module routé,
juste un attribut d'affichage) :

| niveau | catalogue / fiche publique |
|---|---|
| `tous` | visible de tous les visiteurs |
| `benevoles` (défaut) | visible seulement si `est_benevole(request)` |
| `admin` | visible seulement si `est_admin(request)` |

Un petit helper `rangement_visible(request)` (global Jinja), lisant
`parametres.rangement_visibilite`, suffit — pas besoin d'inscrire « rangement »
dans le catalogue `MODULES`. Défaut recommandé : **`benevoles`** (utile à
l'équipe, sans encombrer la fiche publique ; trivial à ouvrir à tous).

**Important — l'écran de retour (§6) n'est PAS concerné par ce réglage** : il est
déjà derrière le jeton bénévole et affiche toujours l'emplacement. Le réglage ne
gouverne QUE le catalogue et la fiche publique. Là aussi, emplacement vide → rien
d'affiché.

## 8. Réglages & écrans d'administration

Un **nouvel écran `/admin/rangement`** (groupe « Configuration » du tableau de
bord), regroupant tout le sujet en un endroit cohérent :

- **Contexte actif** : bascule `evenement` / `local`.
- **Visibilité publique** : les trois niveaux du §7.
- **Liste des emplacements locaux** : ajout / renommage / réordre / archivage
  (§5), avec le compteur d'utilisation par entrée.
- **Accès à la page des manques** (§4.d).

Pourquoi une **page neuve** plutôt que `/admin/fonctionnalites` ? Cette dernière
gère l'activation on/off des modules ; y greffer contexte + visibilité +
gestion de liste la **complexifierait** (ce que la décision 4b interdit
explicitement). Une page dédiée reste lisible pour un bureau non technicien.

## 9. « Sortir pour un tournoi » et le rangement

Rappel (déjà tranché plus haut, regroupé ici pour lever l'ambiguïté) :

- La **sortie** tournoi ne concerne pas le rangement : aucun affichage, aucune
  écriture d'emplacement.
- Le **retour** de tournoi (`rendu_tournoi`) affiche « où ranger » comme un
  retour normal (§6).
- L'affectation d'un emplacement (mode rangement) est **indépendante de l'état de
  prêt** : ranger une boîte sortie en tournoi est permis et sans effet sur le
  prêt.

## 10. Ce que ça ne casse pas (invariants)

- `id_exemplaire` et `reference_titre` **inchangés**. Colonnes d'emplacement
  **nullables**, migrées sans toucher aux clés.
- **Zéro donnée perso** préservé : un nom d'étagère n'est pas une donnée
  personnelle. Aucune jointure avec le planning.
- **Jamais bloquant** : toute valeur absente/inconnue → rien affiché ou
  rattrapage en un tap, jamais d'erreur.
- **Mobile-first**, JS léger inline pour le seul mode rangement (drapeau +
  redirection), **aucune dépendance nouvelle**.

## 11. Phasage

**Phase 1 (ce périmètre) :**

1. Schéma : 2 colonnes sur `exemplaires` + table `emplacements_rangement` (seed)
   + 2 réglages `parametres`.
2. Écran `/admin/rangement` : contexte actif, visibilité (défaut `benevoles`),
   gestion de la liste (archivage doux, compteur d'usage).
3. **Mode rangement** au scanner : cookie d'appareil, bandeau d'état,
   `/scanner/ranger`, saisie manuelle intégrée, sortie du mode. Les deux
   contextes (texte libre / menu).
4. Affichage « où ranger » au retour (`rendu` **et** `rendu_tournoi`).
5. Affichage catalogue / fiche publique selon le niveau de visibilité.
6. Édition à l'unité sur la fiche admin.
7. Import / export CSV des deux colonnes : création auto des valeurs inconnues,
   case vide qui n'efface jamais (§4.b).
8. **Page des manques** (§4.d) : boîtes sans emplacement, saisie rapide,
   filtrable.
9. Tests dédiés + note dans CLAUDE.md et une petite aide.

**Phase 2 (hors périmètre — le futur module « longue durée au local ») :**

- Bascule opérationnelle du contexte sur `local` et parcours de rerangement hors
  événement, prêts nominatifs (cf. `docs/evolution-prets-longue-duree.md`).
- Éventuels : historique des changements d'emplacement, réaffectation en lot lors
  d'un archivage, vue « plan » du local. **Rien de tout ça en phase 1.**

## 12. Arbitrages (tous tranchés)

- [x] **Local en FK vers la liste** (renommage propagé). Événement en texte libre.
- [x] **Emplacement actif du mode rangement = cookie d'appareil** (paramètre
      serveur partagé écarté : collision entre bénévoles).
- [x] **Import CSV, valeur locale inconnue** → **création automatique** tolérante,
      signalée dans le compte-rendu.
- [x] **Import CSV, case vide** → **n'efface jamais** ; retrait explicite via la
      fiche admin ou la page des manques.
- [x] **Page des manques** (§4.d) : boîtes sans emplacement, saisie rapide,
      filtrable — validée en remplacement du choix binaire efface/ignore.
- [x] **Visibilité publique par défaut = `benevoles`** (réglable à tout moment).
- [x] **Retrait d'un emplacement = archivage doux seul** ; suppression dure
      uniquement si aucune boîte ne le pointe.
- [x] **Nouvel écran `/admin/rangement`** dédié (pas de greffe sur
      `/admin/fonctionnalites`).
- [x] **Habillage** : bloc de retour « 🗄️ Où ranger le jeu », bandeau de mode
      « 🗄️ Mode rangement — Emplacement : … » en teinte bleue. *(cosmétique,
      ajustable en cours de route)*

Tout est validé par Simon (y compris l'écran admin dédié et l'habillage). La
phase 1 peut être lancée.

---

**STOP.** Rien n'est implémenté. J'attends ta validation explicite pour démarrer
la phase 1.
