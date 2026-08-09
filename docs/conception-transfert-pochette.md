# Note de conception — Transfert de pochette (rendre et prêter d'un geste)

**Statut :** décisions actées avec Simon le 2026-08-09, avant tout
développement. Ce document fige le périmètre, les arbitrages et surtout
l'**exception** qu'il introduit dans une règle métier jusqu'ici non négociable.

À lire avec `docs/specification.md` §5.1 et §6 (logique de scan, règle du plus
petit numéro libre), `docs/protocole-stress-test.md` §2 (pourquoi toute écriture
de prêt tient dans une seule transaction) et `docs/vocabulaire.md` (le mot
« pochette »).

---

## 1. Le geste qu'on supprime

Un visiteur rapporte une boîte et repart aussitôt avec une autre. C'est un cas
fréquent, et l'application lui fait faire l'aller-retour complet :

1. scan de la boîte rendue → **Rendre** → « pochette n°7 » ;
2. le bénévole sort la pièce d'identité du casier 7 et la rend au visiteur ;
3. scan de la nouvelle boîte → **Prêter** → attribution du **plus petit numéro
   libre**, qui est très souvent le 7 qu'on vient de libérer ;
4. le visiteur retend sa pièce d'identité, le bénévole la remet dans le casier 7.

Les étapes 2 et 4 s'annulent. Sur une journée, à plusieurs centaines de
passages, elles coûtent du temps au comptoir et créent la seule occasion de la
journée où une pièce d'identité circule sans nécessité.

**L'objectif n'est donc pas « ne pas rendre la pièce d'identité » : c'est ne pas
déplacer la pochette.** Elle reste dans son casier, avec son numéro, et c'est le
JEU associé qui change.

## 2. Décisions actées (2026-08-09)

| Question | Décision |
|---|---|
| Enchaînement des deux scans | **Écran de transfert avec scan intégré.** Rien n'est écrit tant que la seconde boîte n'est pas scannée. |
| Point d'entrée | **Uniquement l'écran de la boîte rendue**, à l'état « Sorti », à côté de « Rendre ». |
| Libellé du bouton | **« Rendre et prêter un nouveau jeu sans retour PI »** (formulation de Simon, conservée telle quelle). |
| Numéro de pochette | **Conservé à l'identique.** Le nouveau prêt réutilise le n°7. |

Réserve consignée sur le libellé, sans effet sur la décision : « PI » n'apparaît
nulle part ailleurs dans l'interface et n'est pas décodable par un bénévole
recruté le matin même. Le **sous-titre** du bouton porte donc la formulation
explicite (« la pièce d'identité reste dans la pochette n°7 »), au même endroit
et sur le même modèle que le bouton « Rendre », qui annonce déjà « libère la
pochette n°7 ».

## 3. Le point dur — une exception assumée à une règle non négociable

`CLAUDE.md` et la spécification §6 posent : *« on attribue toujours le plus
petit numéro libre »*. Le transfert y **déroge délibérément** : il réutilise le
numéro du prêt qu'il vient de clore, même si un numéro plus petit est libre.

C'est la seule façon d'atteindre l'objectif. Réattribuer le plus petit libre
afficherait « déplacez la pièce d'identité en n°3 » — soit exactement le geste
qu'on cherche à supprimer.

La dérogation est en réalité une **lecture plus juste de la règle** : la
pochette n'est jamais devenue libre. La pièce d'identité ne quitte pas le
casier, le casier ne redevient donc pas disponible et n'a pas à repasser par
l'attribution. La règle continue de s'appliquer sans réserve partout ailleurs,
`plus_petit_numero_libre()` n'est pas touchée.

⚠️ **À ne pas « corriger » plus tard.** Le service porte un commentaire renvoyant
à cette section, et un test verrouille explicitement le fait qu'un numéro plus
petit disponible n'est PAS pris.

## 4. Modèle de données — rien à changer

Aucune table, aucune colonne, aucune migration. Un transfert produit exactement
ce que produirait un retour suivi d'un prêt :

- la ligne de prêt de la boîte rendue est close (`date_retour` posée) et son
  numéro effacé, conformément à D5 (`_effacer_pochette`) ;
- une nouvelle ligne de prêt est ouverte sur la nouvelle boîte, portant le
  **même** `numero_pochette`, `motif = 'pret'` ;
- la table `pochettes` n'est **pas** touchée : le n°7 reste `occupe = 1` du
  début à la fin. C'est la traduction exacte de « la pièce d'identité n'a pas
  bougé ».

Zéro donnée personnelle, comme le reste du module de prêt.

### Ordre imposé à l'intérieur de la transaction

L'index UNIQUE partiel `idx_pochettes_un_seul_pret` (`models.py`) interdit que
deux prêts **ouverts** portent le même numéro. La clôture de l'ancien prêt doit
donc précéder l'insertion du nouveau, dans la **même** transaction : la ligne
close sort du prédicat partiel (`date_retour IS NULL`) avant que la nouvelle
n'entre. Inverser les deux ferait échouer l'écriture.

Le second index UNIQUE (`idx_prets_un_seul_ouvert`, un seul prêt ouvert par
boîte) est satisfait par le contrôle d'état fait en tête de transaction.

## 5. Déroulé à l'écran

1. Le bénévole scanne la boîte rendue. Écran habituel, état « Sorti — pochette
   n°7 », avec un bouton de plus sous « Rendre ».
2. Ce bouton ouvre un **écran de transfert** dédié, qui embarque la caméra
   (même composant que `/scanner`) et rappelle en tête ce qui est en cours :
   « Pochette n°7 conservée — scannez le nouveau jeu ».
3. Le scan de la seconde boîte déclenche l'**opération unique** : clôture +
   ouverture, en une transaction.
4. L'écran de résultat porte trois informations, dans cet ordre : la pochette
   n°7 **inchangée**, où ranger la boîte rendue, et le nouveau jeu prêté.

**Rien n'est écrit avant l'étape 3.** C'est ce qui rend l'abandon inoffensif :
si le visiteur change d'avis, si la caméra refuse de démarrer, si le bénévole
ferme l'écran, la base est intacte — la boîte rendue est simplement encore
« sortie », et le bouton **Rendre** classique reste à un tap. Aucun état en
suspens, aucune pochette occupée sans prêt ouvert, aucun rattrapage à écrire.

Une **saisie manuelle de secours** est disponible sur l'écran de transfert, sur
le modèle exact de celle du scanner (`/scanner/saisie`) : le geste ne doit pas
dépendre d'une caméra capricieuse.

## 6. Cas limites — jamais bloquant

| Situation | Comportement |
|---|---|
| La seconde boîte scannée est **la même** que la première | Accepté : prêt clos et rouvert **sur le même numéro**. C'est un re-prêt sans mouvement de pièce d'identité — plus juste que « Le re-prêter », qui change de numéro. Message explicite. |
| La seconde boîte est **déjà sortie** | Rien n'est écrit. Message, et on reste sur l'écran de transfert pour rescanner autre chose. |
| La seconde boîte n'existe pas dans le catalogue | Message, écran de transfert réaffiché, champ prérempli (patron de `/scanner/saisie`). |
| La première boîte a été rendue entre-temps par un autre bénévole | Rien n'est écrit. Message : il n'y a plus de pochette à transférer. |
| La première boîte est une **sortie tournoi** (pochette n°0) | Le bouton n'est pas affiché — il n'y a pas de pièce d'identité. Le service refuse également, l'URL étant forgeable. |
| Deux bénévoles transfèrent la même boîte au même instant | Le second se heurte au verrou d'écriture ou aux index UNIQUE, et reçoit le message « occupé » existant. Rien n'est enregistré deux fois. |

## 7. Ce que le transfert ne change pas

- **Statistiques** : deux prêts distincts, comptés normalement. La durée du prêt
  clos est exacte. Aucun filtre à ajouter.
- **D5** (purge du numéro de pochette sur les lignes closes) : respecté. Le n°7
  disparaît de la ligne close et vit désormais sur la nouvelle.
- **Un seul jeu par pièce d'identité** : le transfert est 1 pour 1 par
  construction, la règle est préservée sans contrôle supplémentaire.
- **Rangement** : la boîte rendue doit toujours être rangée, donc l'écran de
  résultat continue d'afficher son emplacement.

## 8. Affichage du résultat — une troisième variante, obligatoire

L'écran de résultat parle aujourd'hui par la couleur d'un grand numéro :
**vert** = glissez la pièce d'identité dans ce casier, **bleu** = allez l'y
récupérer. Un transfert dit une troisième chose, incompatible avec les deux
autres : **ne touchez pas à la pochette**.

Réutiliser l'une des deux variantes existantes conduirait le bénévole à faire
précisément le geste qu'on supprime. Le transfert a donc son propre traitement
visuel, à définir dans `docs/ui-composants.md` en même temps que le gabarit.

## 9. Journal d'activité

Une action nouvelle au vocabulaire fermé (`app/journal.py`), module `pret`.
L'objet porte les **noms des deux jeux** — jamais le numéro de pochette, comme
partout ailleurs dans le journal (`docs/conception-journal.md` §8). Le garde-fou
d'interdiction (`tests/test_journal_interdits.py`) est étendu au scénario.

## 10. Hors périmètre

- Le point d'entrée inverse (« prêter en réutilisant une pochette », depuis
  l'écran d'une boîte disponible) : écarté, deux chemins pour un même geste à
  expliquer à des bénévoles qui découvrent l'outil.
- Un mode « transfert en cours » mémorisé sur l'appareil (cookie, à la manière
  du mode rangement) : écarté avec l'atomicité, qui rend l'état en suspens et
  son rattrapage inutiles.
- Le transfert vers **plusieurs** jeux : interdit par la règle « un seul jeu par
  pièce d'identité », rien à prévoir.

## 11. Séquence de mise en œuvre

1. Cette note.
2. Service `transferer_pochette()` + tests (transaction unique, index UNIQUE,
   D5, cas limites, concurrence).
3. Routes de l'écran de transfert + rattrapages + saisie manuelle.
4. Gabarits, bouton, troisième variante de bandeau, CSS.
5. Journal : action, point d'appel, garde-fou.
6. Relecture croisée et suite complète.
7. Wiki (`Module-Pret`, `Guide-Benevole`, `Journal-Activite`), puis proposition
   de montée de version **mineure**.
