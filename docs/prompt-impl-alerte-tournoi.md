# Prompts d'implémentation — Alerte « rapportez les exemplaires » avant un tournoi

> La note de conception `docs/conception-alerte-tournoi.md` **fait foi**. Ce
> document ne la répète pas : il découpe le chantier en lots, donne à chacun ses
> pièges et ses tests attendus, et fournit un prompt autonome à copier dans une
> session neuve.

**Ordre imposé.** Le lot 2 suppose le lot 1 commité.

| Lot | Objet | Modèle |
|---|---|---|
| 1 | Calcul de l'alerte + affichage sur `/live` + tests | **Sonnet** |
| 2 | Réglages en administration, cohabitation, journal, wiki, version | **Opus** |

Le partage suit la règle habituelle : **Sonnet** quand la note tranche tout et
qu'il existe un patron à recopier dans le dépôt ; **Opus** quand il reste un
arbitrage à rendre ou qu'un libellé doit être écrit plutôt que transposé.

---

## Lot 1 — Calcul et affichage · modèle : **Sonnet**

Tout est tranché par la note, et le patron existe à l'identique dans le dépôt :
`live.annonce_active()` fait déjà, pour l'annonce libre, exactement ce que
l'alerte doit faire (calcul à la lecture, absence de clé dans le JSON, injection
par `textContent`).

### À produire

1. **`app/tournoi/services.py`** — `tournoi_a_annoncer(conn, delai_mini,
   delai_maxi)`, qui renvoie `(tournoi, minutes)` ou `None` :

   - appelle `tournois_imminents(conn, delai_maxi)` — la fenêtre la plus large
     possible — plutôt que de réécrire une requête ;
   - ne garde que les tournois de phase `a_venir` (`phase(d["etat"])`) ;
   - pour chacun, calcule `delai = max(mini, min(2 * duree_min, maxi))`, avec
     `delai = mini` si `duree_min` est `NULL` ou `0` ;
   - garde ceux dont les minutes restantes sont `<= delai`, et renvoie le plus
     proche ;
   - reste défensif si `maxi < mini` (retomber sur `mini`).

2. **`app/routes/live.py`** — trois clés, deux fonctions, un champ de plus dans
   `_collecter_donnees` :

   ```python
   CLE_ALERTE_MESSAGE = "alerte_tournoi_message"
   CLE_ALERTE_DELAI_MIN = "alerte_tournoi_delai_min"
   CLE_ALERTE_DELAI_MAX = "alerte_tournoi_delai_max"
   DELAI_MIN_DEFAUT = 15
   DELAI_MAX_DEFAUT = 90
   JETONS_ALERTE = ("jeu", "minutes", "heure", "lieu")
   ```

   - `formater_alerte(modele, tournoi, minutes)` — substitution **jeton par
     jeton** (voir piège 2), replis du §4 de la note ;
   - `alerte_tournoi(conn, conn_tournoi)` — lit les trois réglages dans la base
     de prêt, appelle `tournoi_a_annoncer`, renvoie le texte ou `None` ;
   - `_collecter_donnees` : appel dans le bloc qui ouvre déjà la connexion
     tournois, et clé `alerte_tournoi` **ajoutée au résultat seulement si elle
     existe**, exactement comme `annonce`.

3. **`app/templates/live.html`** — un second bandeau, sur le patron de
   `.bandeau-annonce` : couleur et pictogramme propres, `display: none` par
   défaut, rendu conditionnel en JS, `textContent`. Il **remplace** l'annonce
   libre quand les deux sont présentes (décision D8) : une seule bande visible.

4. **`tests/test_alerte_tournoi.py`** — les 14 cas du §8 de la note, hors ceux
   qui portent sur l'administration (reportés au lot 2).

### Pièges

1. **`tournois_imminents` ne filtre ni `lance` ni `termine`.** Vérifié dans le
   code : sa clause `WHERE` n'exclut que `brouillon` et les dates absentes. Le
   filtre par phase est à écrire.
2. **Ne pas utiliser `str.format`.** Une accolade solitaire dans un message
   saisi par le bureau ferait lever `KeyError`/`ValueError` en pleine page
   `/live`. Substituer jeton par jeton (`remplacer("{jeu}", …)`) : un texte
   bancal s'affiche tel quel, il ne casse rien. *Ne jamais bloquer* prime.
3. **`{heure}` est en heure locale.** `date_heure` est stockée en UTC ISO ;
   `_heure_locale` existe déjà dans le même fichier — l'utiliser, ne pas la
   dupliquer.
4. **Le bandeau ne doit pas décaler la mise en page** quand il apparaît :
   `live.html` s'en protège explicitement pour l'annonce (commentaire en tête
   de la feuille de style intégrée), la même précaution vaut ici.
5. **Aucune écriture en base.** L'alerte est un calcul de lecture, comme
   l'auto-masquage d'une annonce expirée. Rien à purger, rien à journaliser.
6. **Le message n'a pas de défaut implicite** : tant que `alerte_tournoi_message`
   est absente, `/live` n'affiche rien. Ne pas « aider » en écrivant une phrase
   par défaut dans la base.

### Prompt

> Tu reprends le projet LudoteX. Lis `CLAUDE.md` et
> `docs/conception-alerte-tournoi.md` (en entier — elle est courte).
>
> Objet de cette session : le **lot 1** de
> `docs/prompt-impl-alerte-tournoi.md` — le calcul de l'alerte, son affichage
> sur `/live`, et les tests. **Ne touche pas à l'administration** : c'est le
> lot 2.
>
> Le patron à recopier est `live.annonce_active()` : calcul à la lecture, aucune
> écriture en base, clé absente du JSON quand il n'y a rien à dire, texte injecté
> par `textContent`. Réutilise `tournois_imminents` et `_heure_locale` plutôt que
> d'écrire une requête ou un formatage de plus.
>
> Attention à deux choses que la note signale et que le code confirme :
> `tournois_imminents` ne filtre ni `lance` ni `termine`, et `str.format` est
> inutilisable sur un texte saisi par le bureau (une accolade solitaire ferait
> planter `/live`) — substitue jeton par jeton.
>
> Un commit par point traité, message en français, sans emoji. Termine par le
> total de la suite de tests.

---

## Lot 2 — Administration, cohabitation, journal, wiki · modèle : **Opus**

Ce lot contient les deux seuls vrais arbitrages restants : la validation des
jetons (refuser sans piéger le bureau) et la phrase qui explique qu'une alerte
masque temporairement l'annonce libre. Ce sont des libellés à écrire, pas à
transposer.

### À produire

1. **`app/routes/admin.py`** — extension de `/admin/ecran-salle` (GET et POST),
   **aucune nouvelle page** :

   - trois champs de plus : message, délai plancher, délai plafond ;
   - la liste des jetons acceptés sous le champ, et le texte proposé du §4 de la
     note, reprenable en un clic ;
   - validations : message ≤ 200 caractères et espaces normalisés (patron de
     `saisie_annonce`) ; jetons inconnus refusés avec la liste des jetons
     valides ; délais dans `[0, 1440]` ; `maxi >= mini` ;
   - **aperçu** de ce qui est réellement affiché en salle, calculé avec
     `live.alerte_tournoi` — le formulaire calcule déjà `annonce_affichee` de
     cette façon, c'est le même geste ;
   - quand une alerte occupe le bandeau alors qu'une annonce libre est
     enregistrée, le dire avec l'heure de reprise (§5 de la note).

2. **`app/templates/admin_live.html`** — les trois champs et la note
   d'explication.

3. **`app/journal.py`** — une entrée de vocabulaire fermé pour l'enregistrement
   du modèle de message, sur le patron exact de l'annonce libre (qui est le seul
   `objet` du journal issu d'une saisie libre — relire le commentaire qui
   l'explique avant d'ajouter le second).

4. **`tests/`** — les cas d'administration du §8 : message vide, jeton inconnu
   refusé, `maxi < mini` refusé, aperçu conforme, cohabitation signalée.

5. **`wiki/`** — la page de l'écran de salle : ce que fait l'alerte, quand elle
   apparaît, comment la couper (vider le message), et le fait qu'elle prend
   temporairement la place de l'annonce.

6. **Proposition de montée de version** (SemVer, sans l'appliquer) et total de
   la suite de tests.

### Pièges

1. **Refuser sans piéger.** Un jeton mal orthographié doit produire un message
   qui dit lequel et lesquels sont acceptés — pas un « saisie invalide ». Et le
   texte saisi doit être **réaffiché** dans le champ, jamais perdu.
2. **Ne jamais laisser croire qu'une annonce a été perdue.** C'est la
   contrepartie explicite de la décision D8 : sans la phrase d'explication et
   son heure de reprise, le lot est incomplet.
3. **Enregistrer les délais seuls ne doit pas produire une ligne de journal
   imaginaire** sur le message — le formulaire `/admin/ecran-salle` a déjà
   rencontré ce défaut exact avec l'annonce et les panneaux ; relire comment il
   a été traité (lecture de la valeur précédente avant écriture) plutôt que le
   redécouvrir.
4. **`wiki/` est un dépôt git séparé**, dans le `.gitignore` du dépôt principal :
   commits à part, jamais mélangés au code. Le wiki ne parle jamais de code et
   cite les libellés de boutons mot pour mot.

### Prompt

> Tu reprends le projet LudoteX. Lis `CLAUDE.md` et
> `docs/conception-alerte-tournoi.md` (en entier — elle est courte). Le **lot 1**
> de `docs/prompt-impl-alerte-tournoi.md` est fait et commité.
>
> Objet de cette session : le **lot 2** — les réglages en administration, la
> cohabitation avec l'annonce libre, le journal, le wiki, la version.
>
> Le cœur du lot n'est pas technique : ce sont deux libellés. Celui qui refuse un
> jeton inconnu doit dire lequel et lesquels sont acceptés, sans jamais perdre la
> saisie ; celui qui explique qu'une alerte de tournoi occupe le bandeau doit
> donner l'heure à laquelle l'annonce du bureau reprendra. Sans eux, un membre du
> bureau conclut que son annonce a disparu.
>
> Ne crée aucune page d'administration : les trois réglages rejoignent
> `/admin/ecran-salle`, dont ils sont les voisins naturels.
>
> `wiki/` est un **dépôt git séparé** : commits et push à part, jamais mélangés
> au dépôt principal.
>
> Termine par une proposition de montée de version (sans l'appliquer) et le total
> de la suite de tests.
