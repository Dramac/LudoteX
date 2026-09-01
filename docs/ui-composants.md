# Inventaire des composants d'interface — LudoteX

Fait dans le cadre de S1 (`docs/idees-ux.md`, § Améliorations structurantes).
Ne remplace pas `docs/specification.md` ; c'est une référence de **classes CSS
canoniques**, pour que chaque nouveau gabarit choisisse la bonne variante au
lieu d'en réinventer une. Approche incrémentale : pas de refonte, pas de
renommage de masse — ce document fige ce qui existe déjà et sert de guide aux
prochaines retouches.

Toutes les classes vivent dans `app/static/css/style.css` (fichier unique,
pas de préprocesseur). Les couleurs passent par les variables `:root` :
d'un côté les couleurs de SENS (`--vert`, `--rouge`, `--orange`, `--gris`,
`--bord`, `--texte`), de l'autre les six variables de la couleur d'IDENTITÉ
(`--primaire` et ses nuances), réglable par l'association — voir le § 18, qui
dit lesquelles peuvent porter du texte et lesquelles sont décoratives.

## 1. Bouton principal — `.bouton.bouton-principal`

```html
<button type="submit" class="bouton bouton-principal">Enregistrer</button>
<a class="bouton bouton-principal" href="...">Continuer</a>
```

Grande cible tactile (padding 20px, texte 1.3rem), fond vert, pleine largeur.
**LA** action principale d'un écran : valider un formulaire, lancer/publier/
générer, confirmer une transition d'état. Un seul bouton principal par écran
en général (parfois deux side-by-side comme sur `pret.html`, `tournoi_detail.html`
quand deux actions ont un poids équivalent — voir « Rendre » / « Le re-prêter »).

Utilisé dans : prêt/retour, formulaires tournoi/planning, transitions d'état,
suppression confirmée (`tournoi_supprimer.html` — voir note dans le § 6 :
pas de variante « danger », le vert est réutilisé tel quel).

## 2. Bouton secondaire — `.bouton.bouton-secondaire`

```html
<button class="bouton bouton-secondaire">Annuler</button>
```

Même gabarit (pleine largeur, grande cible) mais fond blanc + bordure grise.
Action alternative de poids réel (« Le re-prêter » à côté de « Rendre »,
« Dupliquer » à côté de « Éditer »), jamais pour une simple navigation de
retour (voir `.lien` au § 5).

## 3. Bouton compact — `.bouton-filtrer`

```html
<button type="submit" class="bouton-filtrer">Filtrer</button>
```

Couleur d'identité (`--primaire`), compact (padding 10px 20px, texte 1rem,
pas pleine largeur). Le texte est `--primaire-texte`, calculé, jamais `#fff`
en dur — voir le § 18. Pour les
actions **denses/utilitaires** : filtres de recherche, actions d'un écran
admin (importer, créer un jeu, se connecter, copier un lien/code), boutons
`type="button"` avec `onclick` (copier-coller). Ne sert jamais de CTA
principal d'un flux bénévole (prêt/retour/scanner) — c'est le rôle de
`.bouton.bouton-principal`.

**Règle de choix bouton principal vs bouton compact** : si l'écran est un
grand geste répété à l'événement (scanner, prêt, retour, inscription
publique) → `.bouton`/`.bouton-principal`. Si l'écran est un formulaire admin
dense ou une action secondaire dans une liste → `.bouton-filtrer`.

## 4. Champ de formulaire — `.champ`

```html
<div class="champ">
  <label for="nom">Nom du tournoi *</label>
  <input type="text" id="nom" name="nom">
</div>
<!-- forme alternative équivalente, label enveloppant : -->
<label class="champ">Jour <input type="text" name="libelle_jour"></label>
```

Les deux formes (`div` + `label for=`, ou `label` enveloppant direct) sont
**visuellement identiques** (le CSS cible `.champ input/select/textarea`, peu
importe si `.champ` est porté par un `div` ou un `label`) — aucune des deux
n'est à corriger, choisir celle qui simplifie le gabarit du moment.

Cases à cocher/radio isolées : `.case` (`<label class="case"><input
type="checkbox">Texte</label>`), pas `.champ`.

**Hors périmètre volontaire** : les champs insérés dans un tableau dense
(scores par ronde, marges d'impression d'étiquettes, saisie inline de
rangement) n'utilisent pas `.champ` — legitimate, ce ne sont pas des champs de
formulaire autonomes mais des cellules de données ; les styliser comme
`.champ` (colonne, label au-dessus) casserait la lecture en ligne du tableau.

## 5. Lien simple — `.lien`

```html
<a class="lien" href="/catalogue">Retour au catalogue</a>
```

Texte bleu, pas de cadre. Pour une navigation secondaire (retour, annuler,
lien vers l'aide) — jamais un `.bouton` plein cadre pour un simple retour en
arrière (voir correctif du § 6, `module_desactive.html`).

## 6. Carte — `.carte`

```html
<section class="carte">...</section>
```

Conteneur blanc, bord arrondi 12px, padding 20px. **Déjà le composant le plus
unifié du site** : quasiment chaque gabarit (prêt, catalogue, tournois,
planning, admin) enveloppe son contenu dans une ou plusieurs `.carte`. Rien à
changer ici.

## 7. Message de résultat — `.resultat` + variantes

```html
<div class="resultat resultat-ok">...</div>       <!-- succès (vert) -->
<div class="resultat resultat-info">...</div>     <!-- information (bleu) -->
<div class="resultat resultat-attention">...</div> <!-- avertissement (orange) -->
```

Autre composant déjà **totalement unifié** : utilisé dans la quasi-totalité
des gabarits (~30), du prêt à l'admin en passant par tournois/planning. La
fiche S1 pointait un risque de désunification général ; ce composant précis
n'y est pas exposé, à préserver tel quel.

## 8. Pastille compacte — `.badge`

```html
<span class="badge badge-dispo">Disponible</span>
<span class="badge badge-ok">Ok</span>
```

Pour un état court dans un contexte dense (liste, cellule de tableau) où
`.resultat` serait disproportionné (padding 20px prévu pour un écran
prêt/retour). Distinction déjà tranchée lors du correctif « retour terrain
iPhone 13 mini » (voir CLAUDE.md) : `.resultat` = écran de résultat d'action,
`.badge` = état affiché en flux.

## 9. Tableau de données — `.detail` **et** `.admin-table`

Ces deux classes définissent en réalité **le même composant** (tableau dense,
100% de largeur, cellules avec bordure basse) — apparues à des moments
différents du projet (`.detail` pour les stats/tournois, `.admin-table`
ajouté plus tard pour l'admin, avec en prime le correctif anti-débordement
mobile `word-break`/`vertical-align` de la session « retour terrain iPhone 13
mini »). C'est exactement le cas de figure que S1 décrit : deux noms pour un
seul besoin, par juxtaposition de sessions. **Retouche appliquée cette
session** : les deux classes partagent maintenant les mêmes règles CSS
(y compris le correctif mobile, qui manquait à `.detail`) — voir
`app/static/css/style.css`. Pas de renommage des gabarits (`.detail` reste
utilisé tel quel dans stats/tournois, `.admin-table` en admin) : le nom
importe peu, le rendu et le comportement mobile sont désormais identiques.

Tableau de la grille planning bénévole : `.pl-grille` (préfixe `pl-*`) reste
**volontairement séparé** — grille éditable avec cases colorées par état,
besoin réellement différent d'un tableau de lecture. Round robin :
`.detail.rr-table` (tableau `.detail` standard + classes `.rr-*` pour les
couleurs V/N/D) — déjà une bonne réutilisation, pas d'écart.

## 10. Écarts corrigés cette session

- **Boutons `.bouton` sans variante** (`planning_case.html`, `planning_admin.html`,
  `planning_collecte.html`, `planning_gerer.html` ×4, `planning_creneau.html`) :
  `.bouton` seul n'a pas de couleur de fond (seules `.bouton-principal`/
  `.bouton-secondaire` en définissent une) — ces boutons s'affichaient donc
  avec le gris par défaut du navigateur et un texte **blanc forcé par le CSS**,
  peu ou pas lisible selon le navigateur. Corrigé en ajoutant
  `bouton-principal` (ce sont toutes des actions principales de leur écran).
- **`module_desactive.html`** : lien de retour en `<a class="bouton">` (même
  défaut que ci-dessus) alors que les pages sœurs du même type
  (`acces_refuse.html`, `erreur.html`) utilisent `.lien` pour ce genre de
  navigation de secours. Aligné sur `.lien`.
- **`admin_fonctionnalites.html`** : le bouton « Enregistrer », désactivé tant
  qu'aucun changement n'est fait, utilisait un style inline
  (`style="background:#9e9e9e"`) + une classe ajoutée en JS à l'activation —
  solution ad hoc faute de règle générique pour un bouton désactivé. Ajout
  d'une règle `.bouton:disabled` générique (grisée, curseur `not-allowed`) ;
  le gabarit revient à `.bouton.bouton-principal` posé une fois pour toutes,
  le JS ne fait plus que lever l'attribut `disabled`.
- **`.detail`/`.admin-table`** : voir § 9 ci-dessus.

## 11. Écarts identifiés mais non traités (hors périmètre de cette session)

- **Pas de variante « danger »** pour un bouton destructeur : la confirmation
  de suppression d'un tournoi (`tournoi_supprimer.html`) réutilise
  `bouton-principal` (vert) plutôt qu'une couleur d'alerte. Cohérent avec le
  reste du site (aucune page n'a de bouton rouge), donc pas une incohérence
  en soi — mais si une vraie variante « danger » devient nécessaire ailleurs,
  elle devra être ajoutée ici en premier.
- **S4 (aide contextuelle repliée)** : périmètre distinct, non traité ici.
- **S2/S3** : la fiche S1 ne les couvre pas ; à traiter seulement si le
  besoin se confirme (voir `docs/idees-ux.md`).

## 12. Liens d'aide — convention de libellé

Ajouté par la fiche **B2** (`docs/audit-ux-2026-07-18.md`). Les liens vers les
pages d'aide n'avaient aucune convention : on relevait « ❓ Aide », « Aide »,
« aide », « Aide — comment organiser un tournoi », « Voir l'aide complète du
planning », « ❓ Aide — rangement des boîtes ». Chaque module avait inventé la
sienne au moment où il a été écrit.

**Deux formes, et deux seulement :**

```html
<!-- 1. NAVIGATION vers une page d'aide (pied de carte, barre de liens) -->
<a class="lien" href="/tournoi/aide">❓ Aide</a>

<!-- 2. SORTIE d'un bloc .aide-inline, vers l'aide complète du sujet -->
<a class="lien" href="/admin/aide#probleme-jeton">Voir l'aide complète — accès bénévole</a>
```

Le sujet de la seconde forme est **le domaine, pas la page** : « — tournois »,
« — planning bénévole », « — accès bénévole ». Il répond à « l'aide complète
de quoi ? », ce que « Voir l'aide complète » seul ne dit pas quand plusieurs
pages d'aide coexistent.

**Trois exceptions, par décision et non par oubli** (le test garde-fou
`tests/test_routes.py::test_convention_des_libelles_de_liens_daide` les
exclut explicitement, avec leur raison) :

- **Fragments de menu du bandeau** (`_menu_benevole.html`,
  `_menu_visiteur.html`) : leurs entrées sont des mots simples sans icône
  (« Catalogue », « Scanner »…). Poser une icône sur la seule entrée « Aide »
  jurerait dans la ligne, et le bandeau est l'élément le plus contraint sur
  petit écran. La cohérence interne du menu prime.
- **`aide.html`**, le hub : ses liens sont volontairement descriptifs
  (« Organiser un tournoi », « Ranger les boîtes ») — le rôle d'un index est
  de dire ce qu'on trouve derrière chaque lien, pas de répéter « Aide ».
- **`apropos.html`** : les libellés y sont des mots au fil d'une phrase, pas
  des liens de navigation.

Le composant `.aide-inline` lui-même (accordéon `<details>` d'aide
contextuelle, introduit par S4) n'est pas décrit dans ce document — voir
`app/static/css/style.css` et la fiche S4 de `docs/idees-ux.md`. Cette
section ne porte que sur le **libellé des liens**.

## 13. Code personnel — `.code-personnel`

```html
<p class="code-personnel">{{ code }}</p>
```

Ajouté par la fiche **D4** (`docs/audit-ux-2026-07-18.md`). Pour un code que
la personne doit noter ou recopier à la main (code de désinscription tournoi,
code de modification planning) : chasse fixe (`ui-monospace`) + espacement des
caractères, qui lève l'ambiguïté 0/O et 1/l — exactement ce que demande un
code recopié à la main. Remplace deux traitements qui n'avaient aucun rapport
pour un même besoin : `.pochette-num` était **détournée** côté tournoi (cette
classe désigne officiellement le numéro de pochette depuis la fiche D3,
voir `docs/vocabulaire.md` — un détournement qui aurait piégé quiconque
retoucherait un jour le style des numéros de pochette) ; `.pl-code`, plus
petite, faisait déjà l'essentiel du travail côté planning mais son préfixe
`pl-` la réservait au seul module planning. Structure commune des deux pages
qui l'utilisent : titre → code → bouton copier → phrase disant à quoi il sert
→ « et si je le perds ? » → liens d'action.

Composant distinct de `.pochette-num`, qui reste réservée à son objet
d'origine (numéro de pochette sur `/pret`) — et de `.rangement-valeur`, pensée
pour un texte parfois long (nom d'étagère) plutôt qu'un code court à chasse
fixe.

## 14. Titre d'onglet — convention unique

Ajouté par la fiche **D1** (`docs/audit-ux-2026-07-18.md`). Trois conventions
coexistaient (`Sujet — {{ nom_association }}`, `Administration — Sujet` **et**
`Sujet — Administration` dans le même module, ou ni l'un ni l'autre) : 39
gabarits sur 55 ne portaient même pas `{{ nom_association }}`.

**Convention unique, du plus spécifique au plus général :**

```
{% block titre %}<Sujet spécifique> — <Module> — {{ nom_association }}{% endblock %}
```

Le `<Module>` est **omis** quand il n'apporte rien :

- la page EST le module (sa propre page d'accueil) : `Catalogue — LudoteX`,
  `Statistiques — LudoteX`, `Tournois — LudoteX`, `Planning — LudoteX` ;
- la page est un point d'entrée générique sans rapport avec un module
  particulier : `Aide — LudoteX`, `À propos — LudoteX`, l'accueil
  (`{{ nom_association }}` seul) ;
- le sujet contient déjà l'information (`Mon planning — LudoteX` : « planning »
  est déjà dans le sujet, ajouter « — Planning — » serait redondant).

Sinon, le module apparaît explicitement : `Scores — Coup de cœur — Tournois —
LudoteX`, `Mes disponibilités — Planning — LudoteX`, `Supervision —
Administration — LudoteX`. Le module utilise le nom court du bandeau/menu
(« Tournois », « Planning »), pas le nom long des libellés de titre `<h1>`
(« Planning bénévole ») — ce ne sont pas la même chose : le `<h1>` s'adresse
au lecteur de la page, le titre d'onglet sert à distinguer des onglets entre
eux.

**Exception assumée** : `live.html` n'étend pas `base.html` — c'est une page
autonome pensée pour un projecteur/TV, avec son propre `<title>{{ data.titre
}} — Tableau de bord</title>`. `data.titre` est réglable en admin
(`/admin/ecran-salle` ; à défaut, cascade sur le nom de l'événement puis sur
celui de l'association — voir `live.titre_defaut`) : lui ajouter
inconditionnellement `— {{ nom_association }}` doublonnerait le nom par
défaut et braderait la personnalisation admin. Laissé tel quel, décision
prise dans la fiche elle-même.

Garde-fou : `tests/test_routes.py::test_d1_titre_onglet_se_termine_par_nom_association`
(paramétré sur les routes principales de chaque famille) et
`test_d1_titre_onglet_tournoi_et_planning_avec_objet` (pages qui exigent un
tournoi/événement existant).

---

## 15. Rappel du nom de l'événement — `.rappel-evenement`

Le nom de l'édition en cours (« Festival du Jeu 2026 »), réglé depuis
`/admin/evenement`, est rappelé sur quatre surfaces publiques : la page
d'accueil, `/programme`, `/tournois` et la page publique d'un tournoi.

```html
{% set evenement = nom_evenement() %}
{% if evenement %}<p class="rappel-evenement">{{ evenement }}</p>{% endif %}
```

**Trois règles.**

1. **Toujours sous un `<h1>`, jamais à sa place.** C'est un rappel de contexte,
   pas le sujet de la page : le `<h1>` reste « Tournois », « Programme du
   week-end », le nom du tournoi. La classe est volontairement plus discrète
   qu'un titre et plus lisible que `.stats-note` (réservée aux notes
   explicatives de bas de champ).
2. **Toujours conditionné.** `nom_evenement()` vaut `None` tant que le réglage
   n'a pas été renseigné : sans le `{% if %}`, la page afficherait un rappel
   vide. Règle « ne jamais afficher une valeur absente », déjà appliquée au
   rangement et à l'annonce de l'écran de salle.
3. **Ne pas confondre avec `nom_association`.** Le nom de l'association vient
   du `.env` et ne change pas d'une édition à l'autre ; le nom de l'événement
   se règle en administration et change chaque année.

**Où il n'apparaît PAS**, et pourquoi : sur `/live`, il passe par la cascade du
titre (`live.titre_defaut`) et non par une ligne à lui — la refonte du 06/08 a
rendu cette hauteur au contenu utile. Sur les écrans bénévole et
d'administration, il n'apporte rien : les personnes qui les utilisent savent
quel événement elles préparent.

---

## 16. Trois variantes de `.pochette-num` — règle de choix

Le grand numéro de pochette affiché sur `/pret/<id>` parle par la couleur,
sans texte à lire de loin. Trois variantes, trois gestes différents :

```html
<p class="pochette-num">7</p>              <!-- vert  : DÉPOSEZ la PI ici -->
<p class="pochette-num pochette-num--retour">7</p>     <!-- bleu   : RÉCUPÉREZ la PI ici -->
<p class="pochette-num pochette-num--transfert">7</p>  <!-- identité : NE TOUCHEZ PAS à la pochette -->
```

Ajoutée par le **transfert de pochette**
(`docs/conception-transfert-pochette.md` §8) : rendre une boîte et en prêter
une autre sans faire ressortir la pièce d'identité de son casier. Réutiliser
le vert ou le bleu aurait fait faire au bénévole le geste exact que la
fonctionnalité supprime — d'où une troisième couleur, celle d'identité du site
(`--primaire`, déjà `.bouton-filtrer`, `theme-color`), qui ne porte par
ailleurs aucun sens de dépôt/retrait sur cet écran.

⚠️ **Depuis que cette couleur est réglable** (§ 18), c'est la seule
signification portée par `--primaire`. Une association qui choisirait un vert
ou un bleu proches de ceux du prêt et du retour affaiblirait la distinction —
sans la supprimer, la phrase explicite ci-dessous restant affichée dans tous
les cas. Le jour où cela se produit, la réponse est de donner à cette variante
sa propre couleur de sens, pas de restreindre le réglage.

**Règle de choix** : le vert et le bleu se choisissent déjà tout seuls (prêt
vs retour). La couleur d'identité ne s'utilise QUE quand le numéro affiché correspond à
une pochette qui ne bouge pas — à ce jour, uniquement l'écran de résultat du
transfert (`pret.html`, `resultat.type == "transfert"`). Toujours accompagné
d'une phrase explicite (« La pièce d'identité reste en place — ne touchez pas
à la pochette. »), jamais de la seule couleur : un bénévole qui découvre
l'écran pour la première fois ne connaît pas encore le code couleur.

---

## 17. Largeur de page — `.contenu` (540 px) ou `.contenu-large`

`.contenu` est plafonné à **540 px** dans `style.css` : le site est
mobile-first, et une colonne de texte étroite se lit mieux. Ce plafond étant
GLOBAL, il s'applique aussi sur un écran d'ordinateur — c'est la cause qu'on
a déjà diagnostiquée six fois (grille du planning, `/stats`, arbre et rondes
de tournoi, tableau de bord admin, « Ranger les jeux »), toujours pour la
même raison et toujours avec le même remède :

```jinja
{% block conteneur_extra %}contenu-large{% endblock %}
```

**Règle de choix.** Poser `contenu-large` dès qu'une page contient l'une de
ces trois choses :

1. un **tableau de données** (`.detail` / `.admin-table`) de plus de trois
   colonnes ;
2. une **grille** ou une frise horaire (planning, programme, arbre de
   tournoi) ;
3. une **liste de travail dense** que l'on parcourt du regard plutôt qu'on ne
   lit — une ligne par objet, avec ses actions au bout.

Les autres restent étroites : catalogue, fiche d'un jeu, écran de prêt,
formulaires de saisie courts, formulaire de collecte du planning.

**Décision Simon du 2026-08-10** — cinq écrans passent en `contenu-large` :
`/tournoi/<id>/gerer` et `/tournois` (listes de travail, critère 3),
`/admin/jeton` (tableau des appareils, critère 1), `/admin/evenement` et
`/aide`. Ces deux derniers sont des **exceptions assumées** à la règle
ci-dessus : ce sont un formulaire court et une page de lecture, que le
critère de largeur n'aurait pas retenus. Ils ont été élargis sur demande, la
lecture à 540 px sur un grand écran ayant été jugée trop contrainte à
l'usage. Si d'autres pages de lecture suivent un jour, c'est le plafond
global qu'il faudra rediscuter, pas la liste des exceptions.

---

## 18. Couleur de thème — six variables, une seule saisie

L'association qui déploie LudoteX règle **une** couleur depuis
`/admin/identite`. Les six variables du `:root` en découlent :

| Variable | Rôle | Peut porter du texte ? |
|---|---|---|
| `--primaire` | bandeau, bouton compact, indicateurs de focus, chiffres de statistiques | oui, avec `--primaire-texte` |
| `--primaire-survol` | survol du bouton compact | oui (même texte) |
| `--primaire-clair` | **décoratif seulement** : barres de l'histogramme, bordure de survol d'une carte | **non** |
| `--primaire-fond` | aplats teintés (puces de filtre) | oui, avec `--primaire` |
| `--primaire-fond-leger` | aplats les plus pâles (aide en ligne, cartes de chiffres) | oui, avec `--texte` ou `--gris` |
| `--primaire-texte` | texte posé sur `--primaire` — **noir ou blanc, calculé** | — |

**Règle de dérivation** (implémentée dans `app/services.py`, section « Identité
de l'ASSOCIATION » ; `nuances_theme`) : conversion en HSL, **teinte et
saturation conservées**, luminosité imposée.

| Nuance | Luminosité | Saturation |
|---|---|---|
| `survol` | ± 12 points (− au-dessus de 50 %, voir plus bas) | inchangée |
| `clair` | 62 % | + 10 points |
| `fond` | 94 % | + 25 points |
| `fond_leger` | 97 % | + 25 points |

Quatre décisions à ne pas défaire :

1. **Pas de mélange vers le blanc.** Il vire au gris et perd la teinte — sur
   l'anthracite `#2a2724`, le fond clair tombait sur `#efeeec` au lieu de
   `#f5f0eb`. Le supplément de saturation des aplats a la même raison d'être :
   sans lui, une couleur peu saturée ne se voit plus nulle part ailleurs que
   sur le bandeau.
2. **`--primaire-clair` ne porte jamais d'information.** À 62 % de luminosité,
   son contraste sur blanc tourne autour de 2,5:1, en dessous des 3:1 exigés
   d'un élément graphique porteur de sens (et loin des 4,5:1 d'un texte). C'est
   pour cela que les indicateurs de focus utilisent `--primaire`, et que les
   barres de l'histogramme sont toujours doublées de leur valeur écrite.
3. **`--primaire-texte` n'est jamais un choix de l'utilisateur.** Noir ou
   blanc, celui des deux qui contraste le mieux au sens WCAG. Le pire cas de
   cette règle vaut 4,58:1 : aucune couleur saisie ne peut rendre le bandeau
   illisible. Ajouter un champ « couleur du texte » romprait cette garantie.
4. **Le survol s'inverse au-dessus de 50 % de luminosité.** Ajouter 12 points à
   une couleur déjà très claire donnerait du blanc pur, et le bouton
   disparaîtrait au survol sur une page blanche. C'est l'ÉCART qui compte, pas
   son sens.

**Le calcul est fait en Python, jamais en CSS.** Ni `color-mix()`, ni les
fonctions de couleur récentes : sur un téléphone qui ne les connaît pas, le
thème perdrait ses nuances sans que personne le sache. Le serveur envoie six
valeurs hexadécimales dans un `<style>` en ligne de `base.html`, alimenté par
le context processor d'`app/templating.py`.

**Tant qu'aucune couleur n'est réglée, RIEN n'est injecté** : le `:root` de
`style.css` fait foi, et le fichier reste en cache. Le thème par défaut
(anthracite chaud `#2a2724`) est donc écrit à deux endroits — le `:root` et
`services.COULEUR_ASSOCIATION_DEFAUT`, dont le `<meta name="theme-color">` a
besoin puisqu'un attribut ne peut pas porter une variable CSS. Un test
(`tests/test_theme.py`) compare les deux valeurs : elles ne peuvent pas
diverger en silence.

**Ce qui ne suit PAS le thème, et ne doit pas le suivre** : le bleu des liens
(`#1a73e8`), le vert / rouge / orange sémantiques, l'orange du mode formation,
le bleu du mode rangement et le violet des blocs « programme » du planning. Ce
sont des significations, pas une identité : elles doivent rester les mêmes
quelle que soit la couleur choisie.
