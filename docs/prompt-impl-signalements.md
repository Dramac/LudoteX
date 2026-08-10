# Prompts d'implémentation — Signalements d'état des boîtes

> La note de conception `docs/conception-signalements.md` **fait foi**. Ce
> document ne la répète pas : il découpe le chantier en lots, donne à chacun ses
> pièges et ses tests attendus, et fournit un prompt autonome à copier dans une
> session neuve.

**Ordre imposé.** Chaque lot suppose le précédent commité. Le lot 1 est le seul
qui ne dépende de rien.

| Lot | Objet | Modèle |
|---|---|---|
| 1 | Socle : tables, seed, services, tests | **Sonnet** |
| 2 | Écrans bénévole : signaler, prévenir | **Sonnet** |
| 3 | Administration : CRUD des catégories, liste, exports | **Opus** |
| 4 | Journal, garde-fou, wiki, version | **Opus** |

Le partage suit une règle simple : **Sonnet** quand la note tranche tout et
qu'il existe un patron à recopier dans le dépôt ; **Opus** quand il reste un
arbitrage à rendre ou qu'un test doit être conçu plutôt qu'écrit.

---

## Lot 1 — Socle · modèle : **Sonnet**

Tout est tranché par la note, et les deux tables ont un patron exact dans le
dépôt (`emplacements_rangement` côté prêt, `types_programme` côté tournois).
C'est de la transposition soignée, pas de la conception.

### À produire

1. **`app/models.py`** — `SCHEMA_CATEGORIES_SIGNALEMENT` et
   `SCHEMA_SIGNALEMENTS` (DDL au §4 de la note), ajoutées à
   `SCHEMA_STATEMENTS` avec les **catégories avant les signalements** (la
   seconde référence la première en FK). Index de lecture dans
   `SCHEMA_INDEXES` — **pas** dans `SCHEMA_INDEXES_UNIQUES`, qui a sa
   mécanique propre (`db._creer_index_uniques`) et ne concerne que les filets
   d'unicité :

   ```sql
   CREATE INDEX IF NOT EXISTS idx_signalements_ouverts
       ON signalements (id_exemplaire) WHERE traite_le IS NULL;
   ```

2. **`app/db.py`** — `_CATEGORIES_SIGNALEMENT_SEED` (les cinq entrées du §2) et
   `_seed_categories_signalement`, **copie conforme** de
   `_seed_emplacements_rangement` : n'insère que si la table est vide. Appel
   dans `init_db` juste à côté de son modèle.

3. **`app/services.py`** — deux familles de services.

   *CRUD des catégories*, patron ligne à ligne des
   `*_emplacement_rangement` déjà présents :
   `lister_categories_signalement` (toutes, pour l'administration),
   `categories_signalement_actives` (pour le formulaire bénévole),
   `creer_categorie_signalement`, `renommer_categorie_signalement`,
   `archiver_`, `reactiver_`, `deplacer_` (haut/bas),
   `compteur_usage_categorie_signalement`, `supprimer_categorie_signalement`
   (refuse si le compteur d'usage n'est pas nul).

   *Signalements* : `creer_signalement(conn, id_exemplaire, id_categorie,
   texte)`, `signalements_ouverts(conn, id_exemplaire)`,
   `lister_signalements(conn, etat, id_categorie)`,
   `traiter_signalement(conn, id_signalement)`,
   `compter_signalements_ouverts(conn)`.

### Pièges

1. **Pas de `services.transaction`.** Un signalement est un `INSERT` autonome :
   ni lecture-puis-écriture, ni ressource à attribuer. `BEGIN IMMEDIATE` n'a
   rien à faire ici (§4 de la note). Ne pas « harmoniser » avec les services de
   prêt.
2. **`traiter_signalement` est idempotent** : `UPDATE ... WHERE traite_le IS
   NULL`. Deux appels donnent le même résultat, sans erreur.
3. **`lister_signalements` ramène les DEUX emplacements** — événement et local
   — par jointure, dans sa propre requête. Ne **pas** appeler
   `services.emplacement_actuel` : elle choisit selon le contexte réglé et
   l'écran de retour bénévole en dépend (§7 de la note).
4. **Ne pas toucher à `info_exemplaire`.** Elle est réutilisée par les gabarits
   publics ; tout ce qu'on y ajoute fuit sur la fiche du catalogue. Même
   raison qu'`emplacement_actuel`, dont la docstring l'explique déjà.
5. **Bornage du texte** à l'enregistrement, sans refus (patron de l'annonce
   d'écran de salle, `routes/live.py`).

### Tests attendus

Nouveau `tests/test_signalements.py` : schéma et seed sur une base fichier
réelle, idempotence du seed, seed non ressuscité après suppression, CRUD des
catégories (dont refus de suppression sous usage, et archivage qui n'efface
rien), création/lecture/traitement d'un signalement, idempotence du
traitement, bornage du texte, les deux emplacements ramenés par
`lister_signalements`.

Plus, dans `tests/test_sauvegarde.py`, un test au **patron D5** : restauration
d'une archive antérieure à ce lot → les deux tables apparaissent, les
catégories sont amorcées, et les données déjà présentes ne sont pas perdues.

### Commits

Un par point traité : schéma+seed, CRUD des catégories, services de
signalement.

### Prompt

> Tu reprends le projet LudoteX. Lis `CLAUDE.md`, puis
> `docs/conception-signalements.md`, qui **fait foi** — en particulier ses §2,
> §4 et §7.
>
> Objet de cette session : le **lot 1** de
> `docs/prompt-impl-signalements.md` (socle : les deux tables, le seed, les
> services et leurs tests). Ne fais **que** ce lot : aucune route, aucun
> gabarit.
>
> Les deux tables ont un patron exact dans le dépôt —
> `emplacements_rangement` (`app/models.py`, `app/db.py`,
> `app/services.py::*_emplacement_rangement`) et `types_programme` côté
> tournois. Transpose-les plutôt que d'inventer, et signale-moi tout endroit où
> le patron ne conviendrait pas.
>
> Lis attentivement les cinq pièges du lot 1 : ils portent chacun sur une
> tentation d'« harmonisation » qui serait une régression. Un commit par point
> traité, message en français, sans emoji. Je pousse moi-même : ne suppose
> jamais que le code est parti. Consigne le total de la suite en fin de session.

---

## Lot 2 — Écrans bénévole · modèle : **Sonnet**

Le déroulé est fixé au §5 et §6 de la note, et le patron du transfert de
pochette (un lien qui **ouvre un écran** sans rien écrire) est fraîchement
livré dans les mêmes fichiers.

### À produire

1. **`app/routes/pret.py`** — `GET /pret/{id}/signaler` (formulaire) et
   `POST /pret/{id}/signaler` (écrit, puis rend l'écran de prêt habituel avec
   un bandeau de confirmation). Toutes deux sous `Depends(exiger_jeton)`.
   Ajouter `{"type": "signale"}` au **dictionnaire `resultat` documenté en
   tête du module** — il est à jour et doit le rester.
2. **`app/templates/pret_signaler.html`** — catégories actives en boutons
   radio empilés, cibles tactiles pleine largeur ; champ de détail avec sa
   consigne **sous le champ** ; bouton d'envoi ; lien de retour.
3. **Lien permanent** en pied de carte de `pret.html` (`.lien-fiche`), quel
   que soit l'état de la boîte.
4. **Bandeau d'alerte** en tête de `pret.html` (`.resultat.resultat-attention`)
   listant les signalements ouverts, **visible dès l'ouverture de l'écran**,
   avant toute action.

### Pièges

1. **Le bandeau se voit AVANT l'action.** C'est tout l'objet du §6 : prévenir
   avant de tendre la boîte. Il ne doit donc pas être conditionné à la
   présence d'un `resultat`, contrairement à l'encart de rangement juste
   à côté — qui, lui, ne concerne que le retour.
2. **La lecture se fait dans `_rendu()`**, via `signalements_ouverts`, et
   seulement si l'exemplaire existe. Ne pas la glisser dans
   `info_exemplaire` (piège 4 du lot 1).
3. **Le signalement ne bloque jamais le prêt.** Aucun bouton masqué, aucune
   confirmation supplémentaire. Si l'envie d'ajouter un `confirm()` se
   présente, c'est la fiche 2.3 qui est en train d'être écrite par erreur.
4. **Rattrapage sans perte de saisie** : catégorie absente, inconnue ou
   archivée entre l'affichage et l'envoi → formulaire réaffiché avec message
   **et saisie conservée**. Patron : le correctif de
   `POST /planning/collecte/{ev}` (lot B de sécurité), qui a précisément
   corrigé un formulaire réaffiché vierge.
5. **Chercher le composant avant d'en écrire un.** Les boutons radio empilés
   existent déjà (`planning_collecte.html`, préférences à quatre niveaux) et
   `docs/ui-composants.md` fixe les variantes de bouton. Aucune classe
   nouvelle sans y être documentée.

### Tests attendus

Accès sans jeton refusé sur les deux routes ; formulaire affichant les
catégories actives et **pas** les archivées ; création réussie et bandeau de
confirmation ; catégorie manquante/archivée → message et saisie conservée ;
bandeau d'alerte présent dès le `GET` sur une boîte signalée, absent sinon ;
lien de signalement présent dans les trois états de la boîte (sortie, sortie
tournoi, disponible) ; texte trop long borné.

### Prompt

> Tu reprends le projet LudoteX. Lis `CLAUDE.md`, puis
> `docs/conception-signalements.md` (§3, §5, §6 et §8), qui fait foi. Le
> **lot 1** de `docs/prompt-impl-signalements.md` est fait et commité : les
> services existent et sont testés.
>
> Objet de cette session : le **lot 2** — les écrans bénévole. Rien du côté
> administration.
>
> Le patron à suivre est celui du **transfert de pochette**, livré la semaine
> dernière dans les mêmes fichiers (`app/routes/pret.py`, `pret.html`) : un
> lien discret qui **ouvre un écran**, et l'écriture seulement au bouton. Lis
> les cinq pièges du lot 2 avant de commencer — le premier (« le bandeau se
> voit avant l'action ») est celui qui rate le plus facilement.
>
> ⚠️ Le champ de détail libre est le seul endroit de l'application de prêt par
> lequel une donnée personnelle peut entrer : la consigne « Décrivez la boîte,
> jamais une personne » va **sous le champ lui-même**, pas dans le wiki.
>
> Un commit par point traité, en français, sans emoji. Je pousse moi-même.

---

## Lot 3 — Administration · modèle : **Opus**

Le plus gros lot, et le seul qui laisse un arbitrage ouvert (le réemploi de
`exports.catalogue_xlsx`). Il touche aussi à un écran très fréquenté.

### À produire

1. **CRUD des catégories** — `GET|POST /admin/categories-signalement`, patron
   **exact** de `/admin/rangement` : créer, renommer, archiver/réactiver,
   réordonner, supprimer si aucun signalement rattaché. Consigne rappelant que
   la liste est lue au comptoir (§2 de la note).
2. **Liste des signalements** — `GET /admin/signalements` : une ligne par
   signalement (jeu, code de boîte, catégorie, détail, date, **les deux
   emplacements**, bouton « Marquer traité »), filtres ouverts/traités/tous et
   par catégorie, compteur d'ouverts en tête.
   `POST /admin/signalements/{id}/traiter`.
3. **Exports** Excel et PDF depuis cette page — **jamais** depuis `/stats`.
4. **Tableau de bord** : entrée dans le groupe « Jeux & étiquettes », avec le
   compteur d'ouverts. Bloc `.aide-inline` renvoyant vers une section nouvelle
   de `/admin/aide`.

### L'arbitrage à rendre

`exports.catalogue_xlsx(entetes, lignes)` est **générique malgré son nom** et
conviendrait telle quelle. Trois voies : la réutiliser sans rien changer (nom
devenu trompeur), la renommer (deux appelants à suivre), ou écrire une
fonction dédiée (duplication assumée). **Trancher explicitement avec Simon et
écrire la raison en commentaire** — c'est le genre de choix qu'on ne
comprend plus six mois après.

Le PDF, lui, demande une fonction propre : `construire_pdf` est spécifique aux
statistiques (période, sections cochables).

### Pièges

1. **Les deux emplacements viennent de `lister_signalements`** (lot 1), pas de
   `emplacement_actuel`, et chacun ne s'affiche que s'il est renseigné —
   jamais de « non renseigné ».
2. **Rien de tout cela sur `/stats`**, qui est publique. Le précédent D5 est
   net.
3. **Filtres : puces de retrait**, patron `catalogue._puces_filtres`, déjà
   repris par l'écran du journal.
4. **Pagination** : la vue « Ranger les jeux » a introduit la première
   pagination server-side du projet. À reprendre **seulement si le volume le
   justifie** — quelques dizaines de signalements par édition ne le justifient
   probablement pas. Instruire, ne pas copier par réflexe.
5. **Largeur d'écran** : un tableau dense a besoin de `.contenu-large` (bloc
   Jinja `conteneur_extra`), sinon le `max-width: 540px` global le bride sur
   ordinateur. Ce piège s'est déjà présenté six fois dans le projet.

### Tests attendus

Garde admin sur toutes les routes ; CRUD complet des catégories dont le refus
de suppression sous usage ; liste et chacun de ses filtres ; « Marquer
traité » ; les deux emplacements affichés, et rien quand aucun n'est
renseigné ; exports Excel (lu par `openpyxl`) et PDF ; compteur du tableau de
bord.

### Prompt

> Tu reprends le projet LudoteX. Lis `CLAUDE.md`, puis
> `docs/conception-signalements.md` (§2, §7 et §9), qui fait foi. Les **lots 1
> et 2** de `docs/prompt-impl-signalements.md` sont faits et commités.
>
> Objet de cette session : le **lot 3** — la face administration. Lis la
> section « L'arbitrage à rendre » et **pose-moi la question avant de coder** :
> je veux trancher le sort de `exports.catalogue_xlsx`, pas le découvrir dans
> un diff.
>
> Le CRUD des catégories se transpose de `/admin/rangement` ; ne réinvente
> rien. Lis les cinq pièges du lot 3, dont deux sont des erreurs que le projet
> a déjà commises (la largeur d'écran bridée à 540 px, et une donnée réservée
> qui sort par une route publique).
>
> Un commit par point traité, en français, sans emoji. Je pousse moi-même.

---

## Lot 4 — Journal, garde-fou, wiki, version · modèle : **Opus**

Le garde-fou d'interdiction est le test le plus important du lot, et il se
conçoit plus qu'il ne s'écrit.

### À produire

1. **`app/journal.py`** — actions au vocabulaire fermé : `signalement_cree`,
   `signalement_traite`, et pour la configuration
   `categorie_signalement_creee` / `_modifiee` / `_supprimee`. L'archivage, la
   réactivation et le réordonnancement restent **hors journal** (même
   arbitrage que les types de programme).
2. **Points d'appel** dans les routes — jamais dans les services (§5.2 de
   `docs/conception-journal.md`). `objet` = nom du jeu, `ref` =
   `reference_titre`, `detail` = libellé de la catégorie. **Jamais le texte
   libre.**
3. **Extension du garde-fou** `tests/test_journal_interdits.py` : le scénario
   joue un signalement dont le texte libre porte une valeur volontairement
   distinctive, puis son absence du fichier est vérifiée — sur les deux plans
   déjà en place, littéral **et** structurel.
4. **Wiki** (dépôt git séparé, à committer et pousser à part) :
   `Module-Pret`, `Guide-Benevole`, `Guide-Admin`, `Journal-Activite`, `Rgpd`,
   `Glossaire`. Section « Si ça ne marche pas » obligatoire sur toute page
   décrivant une action.
5. **Raccord** : ajouter dans `docs/idees-evolutions.md` §6.3 la mention que la
   liste des signalements en constituera une section (§12 de la note).
6. **Proposition** de montée de version **mineure** — à proposer, pas à
   appliquer.

### Pièges

1. **Vérifier chaque test du garde-fou en injectant sa régression.** C'est la
   méthode qui a fait ses preuves au lot C du journal : un garde-fou qu'on n'a
   pas vu échouer ne prouve rien.
2. **Le libellé de catégorie n'est plus sûr par construction** depuis
   l'arbitrage « catégories configurables » (§3 de la note, point 2). Le
   risque est accepté et documenté ; ne pas le redécouvrir et ne pas
   surréagir.
3. **Le wiki ne parle jamais de code** : ni chemin de fichier, ni nom de
   fonction, ni table. Et il cite les libellés de boutons **mot pour mot**.
4. **`wiki/` est un dépôt git séparé** et figure dans le `.gitignore` du dépôt
   principal : ses pages ne peuvent pas être corrigées dans le même commit que
   le code, contrairement à ce qu'affirme encore une section de `CLAUDE.md`.

### Prompt

> Tu reprends le projet LudoteX. Lis `CLAUDE.md`, `docs/conception-journal.md`
> (§2.3, §5.2 et §8) et `docs/conception-signalements.md` (§3 et §10). Les
> **lots 1 à 3** de `docs/prompt-impl-signalements.md` sont faits et commités.
>
> Objet de cette session : le **lot 4** — journal, garde-fou d'interdiction,
> wiki, proposition de version.
>
> Le cœur du lot est l'extension de `tests/test_journal_interdits.py`. Vérifie
> chaque assertion **en injectant volontairement la fuite qu'elle est censée
> attraper**, puis en la retirant : un garde-fou qu'on n'a pas vu échouer ne
> prouve rien. Dis-moi franchement si tu trouves une fuite réelle.
>
> `wiki/` est un **dépôt git séparé** : commits et push à part, jamais mélangés
> au dépôt principal. Le wiki ne parle jamais de code et cite les libellés de
> boutons mot pour mot.
>
> Termine par une proposition de montée de version (sans l'appliquer) et le
> total de la suite de tests.
