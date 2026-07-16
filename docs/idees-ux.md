# Idées UX — qualité de vie, cohérence, finitions

Audit du 2026-07-15, mené sur le code (CSS + gabarits) **et** sur l'application
lancée en local avec les données du mode formation (pages publiques, bénévole,
tournois, planning, admin). Objectif : rendre l'outil simple et efficace pour
des non-développeurs, par améliorations **incrémentales** — aucune refonte.

Chaque point : où, pourquoi c'est un problème pour un utilisateur non
technique, et une suggestion concrète. Contrainte respectée : JS léger autorisé
(inline, sans dépendance), sinon CSS/serveur.

---

## 1. QUICK WINS (< 30 min chacun)

### Q1. ✅ FAIT — 🐛 Faute « 15 jeus disponibles » sur l'accueil
- **Où** : `accueil.html` ligne 10 : `jeu{{ 's' if disponible > 1 else '' }}`.
- **Pourquoi** : le pluriel de « jeu » est « jeu**x** » ; la faute est sur la
  première page que voit le public, en gros et en gras.
- **Suggestion** : remplacer `'s'` par `'x'`.
- **Corrigé** le 2026-07-17 : `'s'` → `'x'` dans `accueil.html` ; vérifié par
  grep qu'aucun autre gabarit ne construisait un pluriel « jeu + s ». Test
  ajouté (`test_accueil_pluriel_jeux`). Voir `CLAUDE.md`.

### Q2. ✅ FAIT — Pluriels paresseux « 20 jeu(x) », « 1 prêt(s) », « exemplaire(s) »
- **Où** : `catalogue.html` (compteur), `stats.html` (palmarès), `fiche.html`,
  `admin_donnees.html`.
- **Pourquoi** : les parenthèses font brouillon sur des pages publiques, alors
  que le vrai pluriel coûte une expression Jinja.
- **Suggestion** : macro Jinja unique `pluriel(n, "jeu", "jeux")` dans un
  fragment partagé, utilisée partout : « 20 jeux », « 1 prêt », « 2 prêts ».
- **Corrigé** le 2026-07-17 : implémentée comme fonction Python
  `services.pluriel(n, singulier, pluriel)` (grammaire FR : -1/0/1 = singulier,
  |n| ≥ 2 = pluriel) enregistrée comme **global Jinja**
  (`templating.py`, `{{ pluriel(n, 'jeu', 'jeux') }}`) — utilisable dans tous
  les gabarits sans `{% import %}`, plus simple qu'un fragment `_macros.html`
  à inclure partout (« ou équivalent » — cohérent avec `est_benevole`/
  `module_visible`, déjà des globals). Grep exhaustif sur `(s)`/`(x)` dans
  `app/templates` : **15 occurrences corrigées** dans 10 gabarits
  (`catalogue.html`, `admin_jeux.html` ×2, `admin_fiche.html`,
  `admin_donnees.html`, `fiche.html` ×2, `stats.html` ×2, `planning_gerer.html`
  ×2, `planning_admin.html`, `tournoi_arbre.html`, `tournoi_rondes.html`,
  `tournoi_supprimer.html`, `planning_case.html`). **Laissés hors scope**
  (décision assumée) : les 2 occurrences dans `admin_etiquettes.html` sont des
  chaînes JS construites côté client (pas du Jinja, la fonction ne s'y
  applique pas) ; le « (e) » de `planning_aide.html` (« placé(e) ») est un
  accord de genre, pas un pluriel. Tests ajoutés (`test_pluriel`,
  `test_catalogue_pluriel_jeux`). Voir `CLAUDE.md`.

### Q3. ✅ FAIT — Numéro d'emplacement minuscule au RETOUR d'un jeu
- **Où** : `pret.html`, résultat `rendu` : « Emplacement n°5 libéré —
  récupérez-y la pièce d'identité » en texte courant.
- **Pourquoi** : au prêt, le numéro s'affiche en 5 rem (illisible de rater) ;
  au retour, le bénévole doit *retrouver la bonne pochette* parmi des dizaines
  — c'est le même besoin de lisibilité, et le numéro est noyé dans une phrase.
- **Suggestion** : même mise en page que le prêt : libellé « Récupérer la pièce
  d'identité à l'emplacement n° » + numéro en classe `.pochette-num` (déclinée
  en bleu `#1a73e8` pour distinguer retour de prêt).
- **Corrigé** le 2026-07-17 : `pret.html` (résultat `rendu`) reprend le même
  gabarit `.resultat-libelle` + `.pochette-num` qu'au prêt, avec la nouvelle
  variante `.pochette-num--retour` (bleu `#1a73e8`, `style.css`). `rendu_tournoi`
  non touché (pas d'emplacement). Test ajouté. Voir `CLAUDE.md`.

### Q4. ✅ FAIT — « Scanner le jeu suivant » : l'action la plus fréquente est un petit lien
- **Où** : `pret.html`, pied de carte.
- **Pourquoi** : après chaque prêt/retour, l'enchaînement vers le scan suivant
  est LE geste répété toute la journée ; c'est aujourd'hui la plus petite cible
  tactile de l'écran.
- **Suggestion** : quand un `resultat` vient d'être affiché, montrer un vrai
  bouton `a.bouton.bouton-secondaire` « 📷 Scanner le jeu suivant » sous le
  bandeau de résultat (le petit lien peut rester en l'absence de résultat).
- **Corrigé** le 2026-07-17 : bouton pleine largeur ajouté juste sous le
  bandeau de résultat (tous les types de résultat). Le petit lien « Scanner le
  jeu suivant » en pied de carte disparaît alors (redondant, seul « Voir la
  fiche publique » reste) ; en simple consultation (pas de résultat), le pied
  de carte est inchangé. Test ajouté. Voir `CLAUDE.md`.

### Q5. ✅ FAIT — Titre d'onglet incohérent sur la fiche publique
- **Où** : `fiche.html` (« Jeu d'essai n°1 — Prêt de jeux ») et le fallback de
  `base.html` (`{% block titre %}Prêt de jeux{% endblock %}`).
- **Pourquoi** : toutes les autres pages titrent avec `{{ nom_association }}` ;
  « Prêt de jeux » générique dépareille dans l'historique/les onglets.
- **Suggestion** : `{% block titre %}{{ nom_association }}{% endblock %}` en
  fallback, et `fiche.html` aligné sur le motif des autres pages.
- **Corrigé** le 2026-07-17 : les deux occurrences remplacées (fallback
  `base.html` et `fiche.html`, motif « `<jeu>` — `{{ nom_association }}` »
  comme `catalogue.html`/`stats.html`/etc.). Grep confirmé : plus aucun
  gabarit ne titre « Prêt de jeux » en dur (`live.html`, autonome, n'étend pas
  `base.html` et n'est pas concerné). Test ajouté. Voir `CLAUDE.md`.

### Q6. ✅ FAIT — Gris trop clair sur les petits textes
- **Où** : `style.css` : `.stats-note` et `.palmares-val small` en `#9aa0a6`
  sur fond blanc, en 0.8rem.
- **Pourquoi** : contraste ≈ 2,8:1 (minimum recommandé 4,5:1 pour du petit
  texte) — illisible pour les presbytes, nombreux chez les bénévoles.
- **Suggestion** : passer `#9aa0a6` en `#6b7075` (les `--gris: #5f6368`
  existants sont bons, ne pas y toucher).
- **Corrigé** le 2026-07-17 : les 4 occurrences de `#9aa0a6` remplacées
  (`.palmares-val small`, `.stats-note`, `.planning-bloc--termine`
  `border-left-color`, `.rr-vide`) ; `--gris: #5f6368` non touché. Purement
  cosmétique, pas de test dédié (suite globale vérifiée verte).

### Q7. ✅ FAIT — Jargon « session » sur le scanner
- **Où** : `scanner.html` : « Une seule autorisation caméra par session. »
- **Pourquoi** : « session » ne veut rien dire pour un bénévole ; la phrase
  inquiète plus qu'elle ne rassure.
- **Suggestion** : « Votre téléphone ne demandera l'autorisation caméra qu'une
  seule fois. »
- **Corrigé** le 2026-07-17 : phrase remplacée telle quelle. Test ajouté.

### Q8. ✅ FAIT — Statut du scanner invisible pour les lecteurs d'écran
- **Où** : `scanner.html`, `<p id="statut">` mis à jour par `scanner.js`.
- **Pourquoi** : les changements (« Démarrage… », « QR détecté ») ne sont pas
  annoncés ; et visuellement rien ne bouge si la caméra met du temps.
- **Suggestion** : ajouter `aria-live="polite"` sur `#statut` (1 attribut).
- **Corrigé** le 2026-07-17 : attribut ajouté tel quel. Test ajouté.

### Q9. ✅ FAIT — « 0 min » de durée moyenne quand il n'y a rien à moyenner
- **Où** : `/stats`, carte de synthèse (constaté avec les données de démo).
- **Pourquoi** : « 0 min durée moyenne » se lit comme « les prêts durent
  0 minute » alors qu'aucun prêt n'est terminé — chiffre faux au premier regard.
- **Suggestion** : afficher « — » (avec `title="aucun prêt terminé sur la
  période"`) quand le dénominateur est nul.
- **Corrigé** le 2026-07-17 : vérification faite, `stats_globales` affichait
  déjà « — » dans ce cas (`AVG` SQL sur 0 ligne renvoie `NULL` → `None` côté
  Python, déjà intercepté par `format_duree(moyenne) if moyenne is not None
  else "—"`) — pas de « 0 min » erroné en pratique. La partie manquante était
  la précision au survol : `stats.html` ajoute désormais
  `title="Aucun prêt terminé sur la période"` sur `.chiffre-val` quand la
  valeur affichée est « — » (rien ne change quand une durée est calculée).
  2 tests ajoutés (service + rendu HTML).

### Q10. ✅ FAIT — Mot de passe admin sans focus automatique
- **Où** : `admin_login.html`.
- **Pourquoi** : l'écran n'a qu'un champ ; devoir taper dedans avant d'écrire
  est une micro-friction quotidienne pour le bureau.
- **Suggestion** : `autofocus` sur le champ (le motif existe déjà sur la saisie
  manuelle du scanner).
- **Constaté** le 2026-07-17 : l'attribut `autofocus` était déjà présent sur
  le champ (`admin_login.html` ligne 23) — rien à corriger côté code. Test de
  non-régression ajouté.

### ✅ FAIT — Q11. Favicon JPEG rectangulaire
- **Où** : `base.html` : `<link rel="icon" href="/static/img/logo_djplm.jpg">`.
- **Pourquoi** : les navigateurs rendent mal un JPEG non carré (fond blanc,
  déformation) — visible sur chaque onglet et sur l'écran d'accueil PWA.
- **Suggestion** : générer un `favicon-192.png` et `favicon-512.png` carrés
  (Pillow est déjà là), déclarés en `rel="icon"` + `apple-touch-icon`.
- **Constaté** le 2026-07-17 : le JPEG source (`logo_djplm.jpg`) est en fait
  déjà carré (1509×1509) — le problème réel n'est pas le format mais le
  **cadrage** : le sorcier est décentré (2/3 gauche du canevas), donc illisible
  une fois réduit à 16-32 px.
- **Corrigé** le 2026-07-17 : recadrage centré sur la tête/chapeau/barbe du
  sorcier (zone la plus reconnaissable en petit), redimensionné en PNG carré
  192×192 et 512×512 (Pillow, rééchantillonnage LANCZOS). Vérifié visuellement
  jusqu'à 32×32 : silhouette violette du chapeau + barbe blanche restent
  identifiables. `base.html` référence désormais les deux PNG
  (`rel="icon"` par taille + `apple-touch-icon`), le JPEG original n'est plus
  utilisé dans `<head>`. PNG versionnés (`app/static/img/`, non exclus du
  dépôt — vérifié, seul `*.qr.png` est ignoré). Test ajouté
  (`test_favicon_carre`).

### ✅ FAIT — Q12. Deux familles de boutons aux angles différents
- **Où** : `style.css` : `.bouton` (12 px de rayon, padding 20) vs
  `.bouton-filtrer` (8 px, padding 10) — utilisés parfois côte à côte
  (formulaires tournois, saisie manuelle).
- **Pourquoi** : l'œil perçoit deux « générations » d'interface ; l'utilisateur
  se demande si la différence a un sens (elle n'en a pas).
- **Suggestion** : aligner `.bouton-filtrer` sur `border-radius: 12px` et lui
  donner la même transition `filter/transform` que `.bouton` (déjà partiel).
- **Corrigé** le 2026-07-17 : `.bouton-filtrer` passe à `border-radius: 12px`.
  La transition `filter/transform` + les états `:hover`/`:active` étaient déjà
  mutualisés avec `.bouton` (règle groupée existante, ligne 137-139 de
  `style.css`) — rien à ajouter de ce côté. Contrôle visuel des deux familles
  côte à côte (formulaires tournois, saisie manuelle du scanner) : angles
  désormais identiques. Purement cosmétique, pas de test dédié ; suite
  complète (205 tests) toujours verte.

---

## 2. UX MOYENNE PRIORITÉ (flux, clarté, feedback)

### M1. ✅ FAIT — 🐛 Histogramme des prêts en heures UTC
- **Où** : `services.prets_par_heure` (docstring l'assume : « par heure
  (UTC) ») ; affiché tel quel sur `/stats`.
- **Pourquoi** : un prêt fait à 15 h apparaît dans la barre « 13 h » (été).
  Pour le bureau qui analyse « la pointe de l'après-midi », les données mentent
  de deux heures — alors que le reste de la page (filtres, détail) est déjà
  converti en heure locale.
- **Suggestion** : convertir la clé horaire en Europe/Paris avant le GROUP BY
  côté Python (les helpers de fuseau existent dans `services.py`), et n'afficher
  que « 15 h » plutôt que `2026-07-16T13`.
- **Corrigé** le 2026-07-17 : `prets_par_heure` récupère désormais les
  `date_sortie` bruts et groupe en Python après conversion en heure locale
  (`.astimezone(FUSEAU_LOCAL)`, pas de logique de fuseau en SQL). Chaque entrée
  porte un `label` prêt à afficher (« 15h », ou « 17/07 15h » si la période
  couvre plusieurs jours locaux) ; `stats.html` l'utilise directement au lieu
  de découper la chaîne ISO. Aucun export Excel/PDF ne reprenait `par_heure`
  (vérifié) — rien à adapter de ce côté. 2 tests ajoutés (conversion simple,
  bascule de jour 23:30 UTC → 01:30 local le lendemain). Voir `CLAUDE.md`.

### M2. ✅ FAIT — 🐛 Planning bénévole : dimanche affiché avant samedi
- **Où** : `app/planning/services.py` — créneaux triés par
  `ORDER BY libelle_jour` (alphabétique : « Dimanche 13 sept. » < « Samedi
  12 sept. ») ; constaté sur `/planning` avec la démo.
- **Pourquoi** : un planning qui commence par le deuxième jour désoriente tout
  le monde, et un non-technicien ne peut pas deviner que c'est l'ordre
  alphabétique.
- **Suggestion** : trier les jours par leur premier créneau (`MIN(debut)` en
  UTC, sous-requête ou tri Python dans `construire_grille`) au lieu du libellé.
  Aucun changement de schéma nécessaire.
- **Corrigé** le 2026-07-17 : nouvelle fonction `services.jours_chronologiques`
  (tri Python par `MIN(debut)`, UTC ISO triable lexicalement), utilisée par
  `construire_grille` (dont héritent la page publique, la grille admin et les
  exports Excel/PDF) et par le formulaire de collecte (`routes.py`), qui
  dupliquait le même groupement par jour. Vérifié avec `python -m
  app.planning.demo` : « Samedi 12 sept. » puis « Dimanche 13 sept. ». 3 tests
  ajoutés. Voir `CLAUDE.md`.

### M3. ✅ FAIT — Aucune protection contre le double-appui sur « Prêter »
- **Où** : `pret.html` (et tous les POST d'action).
- **Pourquoi** : sur un wifi de salle lent, le bénévole re-tape le bouton qui
  « ne répond pas » ; le second POST déclenche « déjà sorti » (orange), qui se
  lit comme une erreur alors que tout s'est bien passé. Stress inutile en pleine
  affluence.
- **Suggestion** : 3 lignes de JS global dans `base.html` : sur `submit`,
  `button[type=submit]` passe `disabled` et son libellé devient « Un
  instant… ». Zéro dépendance, couvre tous les formulaires du site.
- **Corrigé** le 2026-07-17 : script inline dans `base.html` (aucune
  dépendance) — au `submit`, désactive les boutons de type submit du
  formulaire et remplace leur libellé (`innerHTML` sauvegardé dans
  `dataset.libelle` pour le restaurer). Respecte les `confirm()` existants via
  `e.defaultPrevented` (rien n'est désactivé si l'utilisateur a annulé).
  Réactivation au `pageshow` (cas du bouton « page précédente » qui sert une
  page en cache avec des boutons désactivés). Test de présence ajouté ;
  logique JS non exécutable sous pytest (pas de moteur JS), vérifiée
  manuellement (`node --check`) et par relecture. Voir `CLAUDE.md`.

### M4. Le code de désinscription tournoi ne se copie pas en un tap
- **Où** : `tournoi_inscription_ok.html` (le code s'affiche, à noter soi-même) ;
  même besoin pour le code bénévole du planning (`planning_collecte_ok.html`).
- **Pourquoi** : le public note le code en photo ou pas du tout ; celui qui le
  perd doit passer par un bénévole. Un bouton copier réduit directement ces
  sollicitations.
- **Suggestion** : réutiliser le motif « Copier » de `/admin/jeton` (bouton +
  `navigator.clipboard.writeText` + confirmation `.copie-ok`) sur les deux
  pages de confirmation.

### M5. Formulaire de lancement de tournoi : tous les champs pour tous les modes
- **Où** : `tournoi_gerer.html`, bloc « Lancer le tournoi » (mode, nombre de
  rondes, BO3, + un paragraphe d'explication de ce qui s'applique à quoi).
- **Pourquoi** : le bénévole doit lire une notice pour savoir que « nombre de
  rondes » ne sert qu'au suisse et BO3 pas au high score — la notice compense
  une interface qui montre l'inapplicable.
- **Suggestion** : JS léger (5 lignes) : au changement du mode,
  activer/désactiver (`disabled` + `opacity:.45`) le champ rondes et la case
  BO3 selon le mode. Le serveur garde ses validations (déjà en place).

### M6. Menu bénévole : 8 liens qui s'empilent sur 3 lignes en mobile
- **Où** : `_menu_benevole.html` + `.menu-benevole` (flex wrap) dans le bandeau
  sticky.
- **Pourquoi** : sur téléphone, le bandeau collant occupe jusqu'à un tiers de
  l'écran du scanner — précisément la page où l'on a besoin de voir la caméra.
- **Suggestion** : sous 640 px, passer `.menu-benevole` en une seule ligne
  défilante : `flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling:
  touch;` + `white-space: nowrap` sur les liens (motif déjà utilisé par
  `.pl-scroll`). Les entrées les plus utilisées (Scanner, Catalogue) en premier.

### M7. Catalogue : 600 titres, un seul long défilement
- **Où** : `/catalogue` (`catalogue.html`).
- **Pourquoi** : le panneau de recherche disparaît dès qu'on défile ; arrivé en
  bas de 600 jeux, remonter pour re-filtrer décourage — le visiteur abandonne
  ou sollicite un bénévole.
- **Suggestion** : bouton flottant « ↑ Recherche » (lien ancre `#haut`,
  `position: fixed; bottom: 16px; right: 16px`), affiché en CSS pur. Pas de
  pagination (la recherche reste le chemin principal), pas de JS.

### M8. ✅ FAIT — Messages de résultat sans hiérarchie « succès / information »
- **Où** : `pret.html` : le retour rendu est en bleu `resultat-info`, comme la
  sortie tournoi ; seuls les prêts sont en vert.
- **Pourquoi** : pour le bénévole, « rendu » est un succès au même titre que
  « prêté » ; le bleu se lit comme « notice » et affaiblit la confirmation.
- **Suggestion** : passer `rendu` et `rendu_tournoi` en `resultat-ok` (vert),
  réserver le bleu aux informations neutres et l'orange aux « rien n'a été
  modifié » (déjà le cas).
- **Corrigé** le 2026-07-17 : `rendu` et `rendu_tournoi` passent en
  `resultat-ok` (vert, même famille que `prete`/`repret`). `tournoi_sorti`
  reste en bleu `resultat-info` (information neutre) ; `deja_sorti` /
  `deja_disponible` restent en orange (rien n'a été modifié). Test ajouté.
  Voir `CLAUDE.md`.

### M9. Confirmations natives `confirm()` au ton très technique
- **Où** : une dizaine de `onsubmit="return confirm('…')"` (clôture des prêts,
  restauration, jeton, planning…).
- **Pourquoi** : le mécanisme est sain (et sans JS lourd — à garder), mais
  certains textes énumèrent des détails techniques au moment où l'utilisateur
  est le moins disposé à lire. Ex. restauration : 3 phrases.
- **Suggestion** : réécrire chaque message sur le patron « Action ? +
  conséquence principale + porte de sortie », une idée par phrase, max 2
  phrases. Ex. : « Remplacer TOUTES les données par cette sauvegarde ? L'état
  actuel sera d'abord mis de côté automatiquement. »

---

## 3. AMÉLIORATIONS STRUCTURANTES

### S1. Deux « designs de formulaire » cohabitent
- **Constat** : les écrans du prêt (2023-style : gros boutons `.bouton`,
  cartes aérées) et les écrans tournois/planning/admin (formulaires plus denses,
  `.bouton-filtrer`, tableaux `pl-*`) n'ont pas la même densité ni les mêmes
  composants. Chaque module a été bien conçu isolément ; c'est la juxtaposition
  qui trahit trois générations de développement.
- **Pourquoi c'est un problème** : le bénévole qui navigue du scanner à la
  gestion d'un tournoi change visuellement « d'application », ce qui érode la
  confiance (« suis-je toujours au bon endroit ? »).
- **Approche incrémentale** (pas de refonte) : figer un petit « inventaire de
  composants » dans `docs/ui-composants.md` — LE bouton principal, LE bouton
  secondaire, LE champ, LA carte, LE message de résultat, avec leurs classes
  canoniques — puis, au fil des retouches de chaque gabarit, remplacer les
  variantes locales par le composant canonique. Q12 en est la première brique.

### S2. Le feedback après action repose entièrement sur le rechargement de page
- **Constat** : chaque action = POST + redirection + nouvelle page. C'est
  robuste (et cohérent avec le choix « sans JS ») mais sur le réseau de salle,
  entre l'appui et la nouvelle page, il ne se passe RIEN de visible — d'où les
  double-appuis (M3) et l'impression de lenteur.
- **Approche incrémentale** : M3 (désactivation au submit) traite l'urgence.
  L'étape suivante, toujours légère : un indicateur de chargement global (barre
  fine animée sous le bandeau, affichée au `submit` et par l'événement
  `pageshow`, ~10 lignes de JS + CSS dans `base.html`). À évaluer seulement si
  les retours terrain confirment la gêne.

### S3. Les jours du planning n'ont pas d'existence propre (cause racine de M2)
- **Constat** : un « jour » n'est qu'un libellé texte libre sur chaque créneau
  (`creneaux.libelle_jour`). Conséquences : tri alphabétique (M2), doublons
  possibles (« Samedi » vs « samedi »), impossibilité d'afficher une vraie date.
- **Approche incrémentale** : corriger M2 d'abord (tri par premier créneau,
  sans migration). Si le module planning continue de grossir, envisager une
  colonne `creneaux.jour` (date ISO) alimentée par migration depuis les données
  existantes, le libellé devenant un simple format d'affichage.

### S4. L'aide est dans des pages, pas dans les écrans
- **Constat** : les pages d'aide (`/aide`, `/tournoi/aide`, `/planning/aide`)
  sont bien faites, mais l'utilisateur coincé sur un écran ne va pas les lire ;
  les explications longues finissent incrustées dans les formulaires (ex. bloc
  de lancement de tournoi, légende des fonctionnalités admin).
- **Approche incrémentale** : motif unique « aide contextuelle repliée » — un
  `<details class="aide-inline"><summary>❓ Comment ça marche ?</summary>…
  </details>` par écran complexe, contenant 2-3 phrases + lien vers la page
  d'aide complète. Sans JS, déjà dans le langage visuel du site (`<details>`
  du catalogue). Appliquer d'abord aux 3 écrans les plus denses : lancement de
  tournoi, grille planning admin, page fonctionnalités.

---

## Ordre d'attaque suggéré

1. ✅ FAIT — Les 3 bugs : Q1 (faute), M2 (ordre des jours), M1 (heures UTC).
   Corrigés le 2026-07-17, un commit par bug (voir CLAUDE.md).
2. ✅ FAIT — Le lot « bénévole au prêt » : Q3, Q4, M3, M8 — une seule session,
   c'est le cœur de l'outil le jour J. Corrigés le 2026-07-17, un commit par
   point (voir CLAUDE.md).
3. Les finitions transverses : Q2, Q5–Q12.
4. M4–M9 un par un, à prioriser selon tes retours terrain.
5. S1/S4 en tâche de fond, au fil des retouches ; S2/S3 seulement si le besoin
   se confirme.
