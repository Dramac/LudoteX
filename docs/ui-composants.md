# Inventaire des composants d'interface — LudoteX

Fait dans le cadre de S1 (`docs/idees-ux.md`, § Améliorations structurantes).
Ne remplace pas `docs/specification.md` ; c'est une référence de **classes CSS
canoniques**, pour que chaque nouveau gabarit choisisse la bonne variante au
lieu d'en réinventer une. Approche incrémentale : pas de refonte, pas de
renommage de masse — ce document fige ce qui existe déjà et sert de guide aux
prochaines retouches.

Toutes les classes vivent dans `app/static/css/style.css` (fichier unique,
pas de préprocesseur). Les couleurs passent par les variables `:root`
(`--vert`, `--rouge`, `--orange`, `--gris`, `--bord`, `--texte`).

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

Violet, compact (padding 10px 20px, texte 1rem, pas pleine largeur). Pour les
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
