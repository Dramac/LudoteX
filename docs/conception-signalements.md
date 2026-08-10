# Note de conception — Signalements d'état des boîtes (carnet de maintenance)

**Statut :** décisions actées avec Simon le 2026-08-10, avant tout
développement. Ce document fige le périmètre, les arbitrages et le point dur du
chantier : le champ de texte libre est **le seul endroit de l'application de
prêt par lequel une donnée personnelle peut entrer**.

Ce chantier réunit deux fiches de `docs/idees-evolutions.md` que la seconde
elle-même dit indissociables : **2.2 « Signalement d'état au retour »** (le
geste bénévole) et **6.2 « Carnet de maintenance du parc »** (sa face
administrative).

À lire avec `docs/specification.md` §6 (« ne jamais bloquer »),
`docs/conception-rangement.md` §6/§9 (emplacement affiché),
`docs/conception-journal.md` §8 (ce qui n'entre jamais dans le journal) et
`docs/vocabulaire.md`.

---

## 1. Le besoin

Un bénévole rend une boîte et constate qu'il manque un dé, que le livret de
règles a disparu, que le couvercle est déchiré. Aujourd'hui cette information
n'a **aucun réceptacle** : elle se dit à l'oral au voisin de comptoir, et elle
est perdue le soir même. Le parc de jeux est le principal actif de
l'association et son entretien ne laisse aucune trace.

Trois moments distincts à couvrir, et ils n'ont pas les mêmes contraintes :

1. **Constater** — au comptoir, en pleine journée, en trois secondes. Ce moment
   commande tout le reste : s'il coûte cher, rien ne sera jamais signalé.
2. **Prévenir** — au prêt suivant de cette boîte, avant qu'elle ne parte dans
   les mains d'un visiteur qui découvrira le problème sur sa table.
3. **Traiter** — après l'événement, au calme, avec une liste imprimable :
   qu'est-ce qu'on répare, qu'est-ce qu'on rachète, qu'est-ce qu'on retire.

## 2. Décisions actées (2026-08-10)

| Question | Décision |
|---|---|
| Quand peut-on signaler ? | **En permanence** sur `/pret/<id>`, quel que soit l'état de la boîte — pas seulement après un retour. |
| Qui voit le signalement ? | **Bénévoles et administrateurs.** Rien sur la fiche publique ni sur le catalogue. |
| Catégories | **Configurables depuis l'administration**, amorcées à cinq entrées. |
| Sortie pour le bureau | **Exports Excel et PDF depuis la page d'administration**, derrière le mot de passe. |

Décisions complémentaires, prises dans la foulée et sans objection :

| Question | Décision |
|---|---|
| Qui referme un signalement ? | **L'administrateur seul**, depuis sa page. |
| Clôture de fin d'événement | **Ne touche pas aux signalements.** C'est justement à ce moment-là que la liste sert. |
| Emplacement affiché en administration | **Les deux** — événement ET local — côte à côte, lus à l'affichage. |

### Les catégories sont configurables (arbitrage révisé le 2026-08-10)

La première rédaction de cette note proposait une constante fermée, sur le
modèle de `MODES_SCORING`. Simon a tranché dans l'autre sens : le bureau doit
pouvoir faire évoluer la liste sans passer par un développement. Décision
retenue, avec le patron déjà éprouvé deux fois dans le projet —
`emplacements_rangement` (`app/models.py`) et `types_programme`
(`app/tournoi/models.py`) : table dédiée, création, renommage, **archivage
doux**, réordonnancement, et suppression refusée si des signalements y sont
rattachés.

L'objection d'origine n'est pas annulée pour autant, elle est **déplacée sur
l'archivage** : une liste ouverte enfle, et un bénévole qui doit lire quinze
entrées entre deux visiteurs signalera moins. Deux garde-fous en découlent,
qui ne sont pas des détails d'implémentation :

- l'écran bénévole ne propose que les catégories **actives**, jamais les
  archivées — c'est ce qui permet au bureau de retirer une entrée devenue
  inutile sans perdre l'historique qui s'y rattache ;
- l'écran d'administration rappelle, à l'endroit où l'on crée une catégorie,
  que la liste est lue au comptoir en pleine journée. Même principe qu'ailleurs
  dans le projet : la consigne vit sur l'écran, pas dans le wiki.

Cinq entrées sont **amorcées** au premier démarrage : **pièce manquante**,
**règle manquante**, **boîte abîmée**, **matériel abîmé**, **autre**. Le seed
suit exactement `_seed_emplacements_rangement` (`app/db.py`) : il ne remplit
que si la table est vide, donc il ne duplique rien quand `init_db` est rappelé
et **ne ressuscite jamais** une entrée que le bureau aurait supprimée.

### Pourquoi « signalement » et pas « warning »

`docs/vocabulaire.md` fixe les termes officiels de l'interface, et l'application
parle français de bout en bout. « Warning » serait le seul anglicisme affiché à
un bénévole recruté le matin même. Le terme retenu est **signalement** — celui
qu'emploient déjà les deux fiches d'évolution — et le bouton dit « ⚠️ Signaler
un problème », qui décrit le geste plutôt que l'objet.

## 3. Le point dur — la seule porte d'entrée d'une donnée personnelle

L'application de prêt est conçue **sans aucune donnée personnelle**, et c'est
une propriété qu'on préserve, pas un état de fait qu'on constate. Le champ de
détail libre est la première occasion, depuis l'origine du projet, qu'un
bénévole a d'écrire une phrase de son choix dans la base de prêt. Rien
n'empêche techniquement « cassé par le gamin en pull rouge de la table 3 ».

Trois protections, et aucune n'est facultative :

1. **La consigne est sous le champ lui-même**, pas dans le wiki. Précédent
   direct : le libellé d'appareil de `/admin/jeton` porte sa consigne
   (« un poste, jamais une personne ») à l'endroit exact où l'on tape, parce
   qu'une règle qui vit ailleurs que sur l'écran n'est jamais lue. Formulation
   retenue : « Décrivez la boîte, jamais une personne. »
2. **Le texte n'entre jamais dans le journal d'activité.** La ligne portera le
   nom du jeu et le libellé de la catégorie. Le garde-fou d'interdiction
   (`tests/test_journal_interdits.py`) est étendu : un texte libre
   volontairement distinctif est injecté dans le scénario, puis son absence du
   fichier est vérifiée.

   ⚠️ **Conséquence de l'arbitrage sur les catégories** (§2) : le libellé
   n'étant plus une constante du code mais une saisie d'administration, il
   cesse d'être sûr *par construction*. Le risque reste très inférieur à celui
   du champ de terrain — une catégorie se crée au calme, par le bureau, et sert
   des centaines de fois — et le journal enregistre déjà d'autres libellés
   saisis par des humains (noms de tournois, de postes, de fichiers importés).
   Il est donc accepté, mais il est **noté ici** plutôt que découvert plus tard
   par un garde-fou qui échoue.
3. **Le texte n'est jamais public.** C'est la seconde raison, après la
   discrétion sur l'état du parc, de la décision « bénévoles et admin ».

Le champ reste **facultatif** : la catégorie seule suffit à créer un
signalement utile, et c'est le cas le plus fréquent (« pièce manquante » sur
un jeu se comprend sans commentaire).

## 4. Modèle de données

**Deux** tables dans la base de **prêt** (`data/app.db`) :

```sql
CREATE TABLE IF NOT EXISTS categories_signalement (
    id_categorie  INTEGER PRIMARY KEY AUTOINCREMENT,
    nom           TEXT NOT NULL,              -- « Pièce manquante », « Boîte abîmée »…
    actif         INTEGER NOT NULL DEFAULT 1, -- 0 = archivée (retrait doux)
    ordre         INTEGER NOT NULL DEFAULT 0  -- tri d'affichage au comptoir
);

CREATE TABLE IF NOT EXISTS signalements (
    id_signalement  INTEGER PRIMARY KEY AUTOINCREMENT,
    id_exemplaire   TEXT NOT NULL,            -- FK -> exemplaires
    id_categorie    INTEGER,                  -- FK -> categories_signalement, nullable, SANS cascade
    texte           TEXT,                     -- détail libre, facultatif, borné
    cree_le         TEXT NOT NULL,            -- horodatage ISO (UTC)
    traite_le       TEXT,                     -- NULL tant que le signalement est ouvert
    FOREIGN KEY (id_exemplaire) REFERENCES exemplaires (id_exemplaire),
    FOREIGN KEY (id_categorie) REFERENCES categories_signalement (id_categorie)
);
```

`categories_signalement` doit **précéder** `signalements` dans
`SCHEMA_STATEMENTS`, qui la référence. La FK est **nullable et sans cascade**,
comme `programme.id_type` : archiver ou supprimer une catégorie ne doit jamais
effacer un signalement déjà saisi. La suppression pure reste par ailleurs
refusée tant qu'un signalement s'y rattache (patron
`supprimer_emplacement_rangement`, qui compte l'usage avant de supprimer) —
l'archivage est la voie normale.

**Référence, et non libellé recopié** : renommer une catégorie se répercute
donc sur tout l'historique, y compris les signalements déjà traités. C'est le
comportement de `emplacements_rangement` (« renommage répercuté
automatiquement sur toutes les boîtes »), et c'est celui qu'on veut ici :
corriger une faute de frappe ne doit pas couper la liste en deux.

**Tables neuves** : `CREATE TABLE IF NOT EXISTS` dans `SCHEMA_STATEMENTS`
suffit, aucune migration de colonne. Elles entrent automatiquement dans les
sauvegardes (la base de prêt y est copiée à chaud en entier) et sont recréées
après restauration d'une archive antérieure, `_migrer_bases_restaurees`
rejouant les trois `init_db()` — le seed des cinq catégories s'y rejoue aussi,
puisqu'il ne remplit que si la table est vide. Un test au patron D5 le vérifie.

Quatre choix de structure méritent leur justification :

- **Plusieurs signalements par boîte, pas un drapeau.** « Il manque un dé » et
  « la boîte est déchirée » sont deux faits distincts, réparés séparément. Une
  colonne unique sur `exemplaires` obligerait à écraser le premier avec le
  second.
- **L'état est déduit, pas stocké** : ouvert = `traite_le IS NULL`. Même
  principe que l'état d'un exemplaire (prêt avec `date_retour IS NULL`), et
  même bénéfice — aucune colonne à maintenir en accord avec la réalité.
- **Aucun index UNIQUE.** Deux signalements ouverts identiques sur la même
  boîte sont légitimes (deux bénévoles, deux moments) et sans conséquence :
  l'administrateur en traite deux au lieu d'un. Rien à interdire.
- **Un index partiel de lecture** sur `(id_exemplaire) WHERE traite_le IS NULL`
  : c'est la requête jouée à chaque ouverture de `/pret/<id>`.

**Pas de transaction `BEGIN IMMEDIATE`.** Contrairement au prêt, il n'y a ici
ni lecture-puis-écriture ni ressource à attribuer : un signalement est un
`INSERT` autonome. La course aux numéros de pochette n'a pas d'équivalent, et
`services.transaction` n'est pas appelée. Le passage à « traité » est un
`UPDATE ... WHERE traite_le IS NULL`, donc idempotent : deux appuis produisent
le même résultat, sans message d'erreur.

## 5. Le geste bénévole — un écran dédié, pas un menu de plus

L'écran `/pret/<id>` est déjà dense : bandeau de résultat, grand numéro de
pochette, état, jusqu'à trois boutons d'action, encart de rangement. L'audit de
`docs/idees-evolutions.md` le note deux fois (fiches 1.6 et 2.2) : cet écran ne
supporte pas un formulaire de plus.

Le patron retenu est **exactement celui du transfert de pochette** : un lien
discret en pied de carte (`.lien-fiche`) qui **ouvre un écran** sans rien
écrire.

1. `GET /pret/<id>/signaler` — la catégorie en boutons radio (les **actives**
   uniquement, dans l'ordre réglé en administration, empilées, cibles tactiles
   pleine largeur), le détail libre en dessous avec sa consigne, un bouton
   « Envoyer le signalement ».
2. `POST /pret/<id>/signaler` — écrit, puis rend l'écran de prêt habituel avec
   un bandeau de confirmation.

Un tap de plus qu'un menu déroulant posé sous le bouton « Rendre », et c'est
assumé : l'écran de prêt reste lisible, le bénévole relit avant d'envoyer, et
un appui malheureux n'écrit rien.

**Le lien est permanent**, quel que soit l'état de la boîte — sortie, rendue à
l'instant, ou simplement disponible. Le cas « une boîte est ouverte sur une
table, il manque une pièce » est au moins aussi fréquent que la découverte au
retour, et il ne doit pas obliger à simuler un prêt pour être signalé.

## 6. Prévenir au prêt suivant

Un bandeau `.resultat.resultat-attention` en tête de `/pret/<id>`, listant les
signalements **ouverts** de la boîte : catégorie en gras, détail en dessous,
date. Visible **avant l'action, dès l'ouverture de l'écran** — c'est avant de
tendre la boîte qu'il faut prévenir, pas dans la confirmation qui suit.

⚠️ **Le signalement n'empêche jamais le prêt.** « Ne jamais bloquer » vaut ici
comme ailleurs : une boîte à qui il manque un dé se prête très bien, le
bénévole prévient le visiteur, et c'est tout. Aucun bouton n'est masqué, aucune
confirmation supplémentaire n'est demandée.

Retirer une boîte du catalogue relève de la fiche **2.3 (« Statut d'exemplaire
retiré / en réparation »)**, un chantier distinct qui n'est pas ouvert ici.

La lecture se fait par un service dédié, `signalements_ouverts(conn, id)`, et
**pas** en enrichissant `info_exemplaire` — même raison qu'`emplacement_actuel`
(`app/services.py`) : `info` est réutilisée par les gabarits publics, et tout ce
qu'on y ajoute fuit sur la fiche du catalogue.

## 7. L'écran d'administration

`/admin/signalements`, sur le patron de `/admin/rangement/ranger` (liste dense,
actions en `.bouton-filtrer`).

Une ligne par signalement, portant : le **nom du jeu**, le **code de la boîte**,
la **catégorie**, le **détail**, la **date**, les **deux emplacements**, et un
bouton « Marquer traité ».

Filtres : ouverts / traités / tous, et par catégorie. Compteur d'ouverts en
tête, et repris sur le lien du tableau de bord.

Entrée dans le groupe **« Jeux & étiquettes »** du tableau de bord — c'est du
soin apporté au parc, au même titre que la création de fiche et les étiquettes,
pas de la configuration.

### Les deux emplacements, pas celui du contexte actif

Décision de Simon : la page affiche **l'emplacement événement ET l'emplacement
local**, côte à côte, pour aider à retrouver physiquement la boîte incriminée.

C'est un écart délibéré avec le reste du module rangement, où
`services.emplacement_actuel` choisit seul selon le contexte réglé en
administration. Il se justifie par le moment d'usage : cette liste se traite
**après** l'événement, quand le contexte est repassé en « local » alors que la
boîte a été signalée en salle — n'afficher que le contexte courant montrerait
précisément l'emplacement qui ne sert plus.

Conséquence pratique : **`emplacement_actuel` ne convient pas ici** et ne doit
pas être tordue pour l'occasion (l'écran de retour bénévole en dépend, et lui a
bien besoin du seul contexte actif). Les deux valeurs sont ramenées par la
requête de `lister_signalements` elle-même, en une jointure — un seul domicile,
pas deux lectures par ligne.

Affichage : chaque emplacement n'apparaît que s'il est renseigné, jamais un
« non renseigné » anxiogène (règle déjà appliquée au rangement et à l'annonce
d'écran de salle). Une boîte sans aucun des deux n'affiche rien dans cette
colonne.

⚠️ **L'emplacement « événement » est écrasé d'une année sur l'autre** (le module
rangement n'a pas de mémoire par édition, cf. fiche 6.4). Un signalement de six
mois pourra donc pointer une place qui n'existe plus — raison de plus
d'afficher aussi le local, qui, lui, est stable. Figer les valeurs au moment du
signalement créerait une seconde vérité qui divergerait de la première dès le
lendemain : écarté.

### Exports

Excel et PDF, depuis cette page uniquement — jamais depuis `/stats`, qui est
**publique**. La liste nomme des boîtes abîmées et peut contenir du texte
libre : la faire sortir par une route publique, fût-ce derrière une case à
cocher, répéterait l'écart corrigé par D5 sur le numéro de pochette.

Réemploi à trancher au moment d'écrire : `exports.catalogue_xlsx(entetes,
lignes)` est en réalité **générique** malgré son nom et conviendrait telle
quelle. Soit on la réutilise et on la renomme (deux appelants), soit on écrit
une fonction dédiée. Le PDF, lui, demande une petite fonction propre :
`construire_pdf` est spécifique aux statistiques (période, sections cochables).

**Tranché le 2026-08-10 (Simon), au moment d'écrire le lot 3 :** renommage en
`exports.tableau_xlsx(entetes, lignes, titre_feuille="Feuille")`. Le constat
qui a emporté la décision est que la fonction n'avait **qu'un seul appelant en
production et aucun en test** — le renommage ne coûtait donc presque rien — et
que sa seule partie non générique, le titre de feuille codé en dur, aurait
intitulé « Catalogue » le classeur des signalements, sous les yeux du bureau à
l'ouverture du fichier. Le titre devient un paramètre. La raison est répétée en
docstring, à l'endroit où on se posera la question.

Le PDF suit l'analyse d'origine : fonction propre `exports.signalements_pdf`
(A4 **paysage**, sept colonnes dont deux de texte libre), une quinzaine de
lignes de style recopiées assumées plutôt qu'un paramètre de plus sur
`construire_pdf`.

## 8. Cas limites — jamais bloquant

| Situation | Comportement |
|---|---|
| Aucune catégorie choisie | Formulaire réaffiché avec message et **saisie conservée**, jamais un formulaire vierge (patron du correctif de la collecte planning, lot B de sécurité). |
| Catégorie inconnue ou archivée (URL forgée, ou archivée pendant la saisie) | Refusée côté serveur, même écran réaffiché avec la saisie conservée. La validation relit la liste des catégories **actives**, elle ne fait jamais confiance au formulaire. |
| Catégorie archivée après coup | Les signalements existants la gardent et continuent de l'afficher (FK sans cascade). Seule la saisie de nouveaux signalements cesse de la proposer. |
| Détail plus long que la limite | Borné à l'enregistrement, sans refus (patron de l'annonce d'écran de salle). `maxlength` côté champ pour que la limite se voie en tapant. |
| Boîte inconnue | Écran « Exemplaire inconnu » habituel, 404. |
| Double appui sur « Envoyer » | Le script anti-double-appui de `base.html` couvre le cas courant. Deux lignes identiques, si elles passent, ne cassent rien — l'administrateur en traite deux. |
| Signalement déjà traité | `UPDATE ... WHERE traite_le IS NULL` : idempotent, aucun message d'erreur. |
| Boîte signalée puis supprimée | Ne peut pas arriver : aucune route de suppression d'exemplaire n'existe (vérifié — c'est déjà ce qui a fait corriger une procédure de `/admin/aide`). |

## 9. Ce que ce chantier ne change pas

- **La logique de prêt** : aucune. Ni les états, ni les numéros de pochette, ni
  les transactions, ni les index.
- **Les statistiques** : un signalement n'est pas un prêt, aucun filtre à
  ajouter nulle part.
- **La clôture de fin d'événement** : elle ferme les prêts, purge le registre
  des appareils et les rotations du journal. Elle ne touche pas aux
  signalements — un carnet de maintenance sans mémoire ne sert à rien.
- **Le catalogue public et la fiche publique** : inchangés, au pixel près.
- **Le RGPD** : la propriété « zéro donnée personnelle » est **maintenue**, et
  c'est tout l'objet du §3. Ce n'est pas une rupture assumée comme l'a été le
  module planning.

## 10. Journal d'activité

Actions nouvelles au vocabulaire fermé (`app/journal.py`), module `pret` — qui
existe déjà : `signalement_cree` et `signalement_traite`.

- `objet` : le **nom du jeu** (ce que la table ne porte pas), comme les quatre
  actions de prêt existantes.
- `ref` : le `reference_titre`.
- `detail` : le **libellé de la catégorie**, et lui seul. Le texte libre
  n'apparaît nulle part — voir la réserve du §3, point 2, sur ce que
  l'arbitrage « catégories configurables » change à cette garantie.

Le CRUD des catégories relève, lui, de la **configuration** : il suit les
autres actions d'administration (`categorie_signalement_creee` / `_modifiee` /
`_supprimee`), sur le patron exact des types de programme. Comme pour eux,
l'archivage, la réactivation et le réordonnancement restent **hors journal** :
ce sont des ajustements de présentation, pas des faits qu'on cherche après
coup.

Garde-fou d'interdiction étendu (voir §3, point 2).

## 11. Hors périmètre

- **Statut « retiré / en réparation »** (fiche 2.3) : un drapeau administratif
  qui masquerait la boîte du catalogue. Prolonge naturellement ce chantier,
  mais touche au catalogue public et à la disponibilité par titre — à
  instruire séparément.
- **Liste des signalements côté bénévole** : le bandeau sur la boîte concernée
  suffit au comptoir. Une liste de plus dans le menu bénévole serait consultée
  une fois puis jamais.
- **Photo du problème** : demanderait du stockage de fichiers, absent du projet
  de bout en bout, et ouvrirait une seconde porte à la donnée personnelle
  (un visiteur dans le cadre).
- **Notification au bureau** : aucun envoi d'e-mail n'existe dans
  l'application, et le module tournois a déjà reporté le sien en phase 2.

## 12. Le « bilan de l'événement »

⚠️ **Il n'existe pas.** C'est la fiche **6.3 (« Rapport d'édition
auto-généré »)** de `docs/idees-evolutions.md`, non réalisée : il n'y a
aujourd'hui que les exports Excel/PDF de `/stats`, à sections cochables, et les
exports du planning. Aucune page ne les assemble.

La liste des signalements n'y est donc pas branchée par ce chantier — on ne
peut pas ajouter une section à un document qui n'est pas écrit. Elle sort par
les exports de sa propre page (§7), qui sont livrables tout de suite, et
constituera l'une des sections du rapport d'édition le jour où 6.3 sera
instruite. À noter dans la fiche 6.3 pour que le raccord ne se perde pas.

## 13. Séquence de mise en œuvre

Découpage détaillé, avec un prompt autonome par lot :
`docs/prompt-impl-signalements.md`.

1. Cette note.
2. **Lot 1 — socle.** Les deux tables, l'index partiel, le seed, les services
   (CRUD des catégories + `creer_signalement`, `signalements_ouverts`,
   `lister_signalements`, `traiter_signalement`) et leurs tests, dont la
   restauration au patron D5.
3. **Lot 2 — écrans bénévole.** Lien permanent, formulaire dédié, rattrapages,
   bandeau de confirmation, puis bandeau d'alerte en tête de `/pret/<id>`.
4. **Lot 3 — administration.** CRUD des catégories, liste des signalements,
   filtres, « Marquer traité », compteur, lien au tableau de bord, exports
   Excel et PDF.
5. **Lot 4 — journal et finitions.** Les actions, les points d'appel,
   l'extension du garde-fou d'interdiction, la relecture croisée et la suite
   complète.
6. Wiki (`Module-Pret`, `Guide-Benevole`, `Guide-Admin`, `Journal-Activite`,
   `Rgpd`, `Glossaire`), puis proposition de montée de version **mineure**.
