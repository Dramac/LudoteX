# Protocole de test de charge — « La nuit du jeu »

Objet : vérifier que LudoteX, tel qu'il est déployé, tient les conditions
réelles d'un événement — 4 à 8 bénévoles au stand de prêt, des visiteurs qui
consultent le catalogue, un écran de salle, une soirée entière.

Rédigé pour être exécutable **par Simon seul**, avec une phase optionnelle à
1 ou 2 personnes équipées d'un smartphone.

---

## 1. Ce qu'on cherche vraiment

Le premier réflexe, face à « est-ce que le serveur tiendra ? », est de mesurer
un débit. Pour cet événement, c'est le mauvais indicateur.

Un bénévole au stand met entre trente secondes et deux minutes par opération :
trouver la boîte, prendre la pièce d'identité, scanner, glisser la PI dans la
pochette. Huit bénévoles à ce rythme, c'est de l'ordre de **0,2 écriture par
seconde**, avec des pointes à 2 ou 3 au moment de l'ouverture. Un VPS d'entrée
de gamme encaisse cela sans transpirer, et le savoir n'apprend rien.

Le risque n'est pas le volume, c'est la **simultanéité**. Deux bénévoles qui
appuient sur « Prêter » dans la même seconde, sur un serveur qui traite les
requêtes en parallèle et une base SQLite dont la logique d'attribution des
pochettes n'a pas été écrite pour cela. Ce protocole cherche donc, dans
l'ordre :

1. **des incohérences de données** sous accès simultané — deux boîtes qui
   reçoivent le même numéro de pochette, deux prêts ouverts sur une même
   boîte ;
2. **le dimensionnement** — temps de réponse, CPU, mémoire, marge disponible ;
3. **la tenue dans la durée** — mémoire, fichiers WAL, sauvegarde qui tourne
   pendant que les bénévoles prêtent.

---

## 2. Ce que la première passe a déjà trouvé (30 juillet 2026)

> **✅ CORRIGÉ le 2 août 2026.** Les trois défauts décrits ci-dessous sont
> traités ; le détail de ce qui a été décidé, et pourquoi, est en § 2.4. La
> description des défauts est conservée telle quelle : elle documente ce
> contre quoi le correctif protège, et sert de référence si le sujet ressort.

Les scripts ont été mis au point contre une instance locale de LudoteX
(données de formation, boucle locale, donc simultanéité parfaite). Deux
défauts sont apparus **immédiatement**, avant même d'avoir touché au VPS.

### 2.1 Un même numéro de pochette attribué à plusieurs boîtes

`services.plus_petit_numero_libre()` procède en deux temps :

```
SELECT MIN(numero_pochette) FROM pochettes WHERE occupe = 0   -- lecture
UPDATE pochettes SET occupe = 1 WHERE numero_pochette = ?      -- écriture
```

Entre les deux, rien ne réserve le numéro : la lecture n'ouvre pas de
transaction en écriture. Huit prêts simultanés sur huit boîtes différentes ont
donc tous lu « le plus petit numéro libre est 12 » et sont tous repartis avec
la pochette n°12. **Aucun message d'erreur** : chaque bénévole voit son grand
chiffre s'afficher normalement.

Traduit sur le stand : huit pièces d'identité pour une seule pochette. À la
restitution, la première boîte rendue libère le numéro 12 et les sept autres
prêts pointent vers une pochette qui sera réattribuée à quelqu'un d'autre.

Une variante plus brutale existe quand aucune pochette n'est libre — cas de
l'**ouverture de soirée**, table des pochettes vide : les requêtes calculent
toutes le même `MAX + 1` et l'insèrent, la clé primaire refuse les doublons,
ce qui donne une erreur 500 en plein coup de feu.

### 2.2 Deux prêts ouverts sur une même boîte

Dans `routes/pret.py`, `action_preter` vérifie « cette boîte est-elle déjà
sortie ? » puis appelle `preter()`. Deux appuis simultanés — double-tap sur un
wifi lent, ou deux bénévoles qui scannent la même boîte — passent le contrôle
tous les deux. Le test a ouvert **huit prêts sur une seule boîte** en une
salve. Le garde-fou côté navigateur ajouté en session UX (M3, boutons désactivés
au `submit`) protège le double-appui d'un même téléphone, pas deux téléphones.

### 2.3 Ce que cela ne prouve pas encore

Ces tirs étaient sur boucle locale : les huit requêtes arrivaient à la
microseconde près. Sur le VPS, à travers Internet, la latence disperse les
arrivées et la fenêtre se referme en grande partie. **La question qui reste
ouverte, et que ce protocole tranche, est : à quelle fréquence cela se produit
dans les conditions réelles de la salle ?** Une occurrence par soirée est déjà
un incident : c'est une pièce d'identité mal rendue.

À noter : le numéro de pochette étant effacé à la clôture d'un prêt (décision
D5), **on ne peut pas rechercher rétroactivement** dans les données des
événements passés si le cas s'est déjà produit. Le contrôle n'est possible que
sur des prêts **en cours** — donc pendant l'événement, avant la clôture.

### 2.4 Le correctif (session du 2 août 2026)

**Reproduction préalable.** Le défaut a d'abord été rejoué sur instance locale
(bases jetables, données de formation), dans les deux configurations : table
`pochettes` peuplée — 39 anomalies sur 20 manches, dont « pochette n°13
attribuée à 8 boîtes à la fois » et huit prêts ouverts sur une seule boîte — et
table `pochettes` vide, où s'ajoutaient 24 `IntegrityError: UNIQUE constraint
failed: pochettes.numero_pochette`, soit autant de 500. Un point non prévu par
le § 2.2 est apparu à cette occasion : en scénario B, les huit prêts ouverts
sur la même boîte repartaient **tous avec la même pochette** — les deux défauts
se composent.

**Ce qui a été retenu, des trois pistes du § 6.1 : les trois, dans cet ordre.**

1. **`services.transaction(conn)`**, gestionnaire de contexte qui ouvre la
   transaction en écriture (`BEGIN IMMEDIATE`) AVANT toute lecture. Appliqué à
   `preter`, `rendre`, `sortir_tournoi`, `repreter` et
   `cloturer_tous_les_prets`. C'est le correctif de fond ; le reste est du
   filet.
   - Il est **réentrant** : si une transaction est déjà ouverte, il s'y greffe
     sans rien ouvrir ni committer. C'est ce qui permet à `repreter()`, qui
     écrit avant d'appeler `preter()`, de continuer à fonctionner — un `BEGIN`
     naïf y aurait levé « cannot start a transaction within a transaction ».
     Se greffer est sûr : en mode legacy, une transaction ne s'ouvre
     implicitement que sur une ÉCRITURE (un SELECT seul laisse
     `in_transaction` à False), donc « déjà en transaction » implique « verrou
     d'écriture déjà tenu ».
   - `conn.isolation_level = None` a été **écarté** : en mode autocommit, les
     ~20 `conn.commit()` de `services.py` deviendraient des non-opérations et
     le contrat « ne committe pas, c'est l'appelant qui committe » de
     `plus_petit_numero_libre`/`liberer_numero`/`_effacer_pochette` tomberait
     silencieusement. Le `BEGIN IMMEDIATE` explicite fonctionne très bien en
     mode legacy (vérifié avant de coder).
   - `rendre` et `cloturer_tous_les_prets` ont été inclus **au-delà de ce que
     décrivait le § 2** : `rendre` lit puis écrit lui aussi, et deux retours
     simultanés sur la même boîte libéreraient deux fois la même pochette.

2. **Deux index UNIQUE partiels** (`models.SCHEMA_INDEXES_UNIQUES`) : un prêt
   ouvert au maximum par boîte, une pochette détenue par un seul prêt à la
   fois. Ils ne corrigent rien seuls — ils transforment un silence en erreur
   visible si un chemin d'écriture nous échappait un jour.
   - Le second exclut `numero_pochette IS NULL` (prêts clos, D5) et
     `numero_pochette <> 0` (marqueur des sorties tournoi, forcément partagé).
   - **Base déjà incohérente** : `db._creer_index_uniques` rattrape
     l'`IntegrityError`, journalise un avertissement explicite et continue.
     `init_db()` tournant au démarrage ET après une restauration de sauvegarde,
     lever à cet endroit mettrait le site par terre ou casserait une
     restauration en pleine soirée. **Aucune réparation automatique** : la base
     ne peut pas savoir quelle pièce d'identité est dans quel casier.
   - `idx_prets_retour_null` (non unique, même expression) est **conservé** :
     redondant quand l'index UNIQUE existe, il redevient l'index de requête
     quand celui-ci n'a pas pu être créé.
   - Ces index ne sont **ni dans `SCHEMA_STATEMENTS`, ni dans la liste que
     `db._migrer_pochette_nullable` recrée** : ils sont créés en un seul
     endroit, celui qui sait échouer sans casser. Le piège du « double
     domicile » disparaît au lieu d'être entretenu.

3. **Le contrôle « déjà sortie ? » est entré dans la transaction**
   (`services.preter_si_disponible`, `services.sortir_tournoi_si_disponible`,
   appelées par `routes/pret.py`). L'index seul aurait transformé le doublon
   silencieux en 500 : on aurait échangé une pièce d'identité perdue contre une
   erreur brute. `preter()` et `sortir_tournoi()` gardent leur signature et leur
   contrat « ne refuse jamais ».

**Délai d'attente et message.** `db.get_connection()` fixe explicitement
`timeout=15 s` (`TIMEOUT_ECRITURE_S`) au lieu de subir le défaut implicite de
5 s. Si le verrou n'est pas obtenu, ou si un index refuse un doublon, la route
affiche un résultat `occupe` — « Un autre bénévole enregistrait une opération
au même moment. **Rien n'a été enregistré.** » — avec les boutons d'action
toujours en place : reprise en un tap, jamais un 500 (règle « ne jamais
bloquer »). Documenté dans `wiki/Module-Pret.md`, section « Si ça ne marche
pas ». À huit bénévoles, ce message ne devrait jamais apparaître : les
transactions durent une fraction de milliseconde.

**Migration et retour en arrière.** Rien d'autre que des index : aucune donnée
modifiée, aucune table reconstruite. Réversible par
`DROP INDEX idx_prets_un_seul_ouvert;` et `DROP INDEX idx_pochettes_un_seul_pret;`.
⚠️ Rétrograder le CODE sans exécuter ces deux `DROP` laisserait la contrainte
active face à un code qui, lui, produit des doublons : le retour en arrière
complet suppose les deux.

**Tests.** `tests/test_concurrence_pochettes.py` (15 tests, sur base FICHIER —
deux connexions `:memory:` ouvrent deux bases différentes et ne se disputent
aucun verrou). Le test déterministe porte sur la seule propriété qui
discrimine : entrer dans le bloc, avant toute lecture, doit DÉJÀ interdire à un
autre d'écrire. Une première rédaction observait le moment de l'écriture — elle
passait au vert même correctif neutralisé, puisque SQLite verrouille de
lui-même dès la première écriture. Chaque test a été vérifié en neutralisant le
`BEGIN IMMEDIATE` : 4 tombent, dont le déterministe.

**Le même motif dans les inscriptions de tournoi — corrigé dans la foulée.**
`app/tournoi/services.py::inscrire` lisait `places_restantes()` puis insérait :
deux inscriptions simultanées sur la dernière place passaient le contrôle
toutes les deux. Conséquence sans commune mesure avec une pièce d'identité
perdue (une chaise en trop), d'où un traitement séparé, après coup. Le remède
est le même : `services.transaction` est **importée** depuis le module de prêt
plutôt que dupliquée — `app/tournoi/` et `app/planning/` importent déjà neuf
helpers de `app/services.py` (`maintenant`, `FUSEAU_LOCAL`,
`local_vers_utc_iso`…), la duplication d'`_ics_horodatage` était un cas
particulier et non une règle. `_inserer_inscription` ne committe plus (les deux
appelants publics délimitent la transaction, comme les helpers de pochette).

Différence notable avec le module de prêt : **aucun index ne peut servir de
filet ici**. Un plafond de places est un COMPTAGE, pas une unicité — aucune
contrainte de schéma ne l'exprime. La transaction est le seul garde-fou, d'où
6 tests dédiés (`tests/test_concurrence_inscriptions.py`). Le test déterministe
observe `conn.in_transaction` **au moment du comptage** : c'est la seule
formulation qui discrimine, puisqu'une fois l'écriture commencée SQLite
verrouille de lui-même. Vérifié en neutralisant le correctif : 2 tests tombent.

Restent non audités, faute de périmètre : le reste du module tournois
(désinscription, lancement, génération de rondes), le planning et le programme.
Aucun n'a de contrainte de comptage comparable, mais rien n'a été vérifié
systématiquement.

**Vérification (critère d'acceptation du § 5), instance locale, bases jetables :**

```
course --tireurs 8 --manches 50              A/prete 400 · B/prete 50 · B/deja_sorti 350
                                             ✓ Aucune anomalie — 0 erreur 500
course --tireurs 8 --manches 5 --laisser-en-place
coherence.py                                 ✓ 7/7 invariants tenus
… les deux mêmes passages, table `pochettes` VIDE :
course --tireurs 8 --manches 50              A/prete 400 · B/prete 50 · B/deja_sorti 350
                                             ✓ Aucune anomalie — 0 erreur 500
coherence.py                                 ✓ 7/7 invariants tenus
```

En scénario B, « B/prete 50 » sur 50 manches est exactement l'attendu : un seul
prêt ouvert par salve de huit appuis simultanés sur la même boîte.

Reste à faire côté VPS : rejouer ces quatre passages sur le site de formation
après mise à jour (voir la marche à suivre en tête de session), puis les phases
2 à 5 du présent protocole, qui n'ont pas encore été exécutées.

---

## 3. Le matériel de test

### 3.1 La cible : l'instance de formation

Tous les tests écrivent réellement en base — prêts, retours, pochettes. Ils
visent donc `formation.<domaine>` : même code, même machine, même version de
Python, mêmes réglages nginx, mais des **bases jetables**. On mesure la vraie
performance du serveur sans jamais toucher aux données de production.

`scripts/stress/charge.py` refuse de démarrer si la page d'accueil de la cible
ne porte pas le bandeau « SITE DE FORMATION ». C'est un garde-fou, pas une
garantie : vérifier l'URL avant chaque lancement reste la règle.

Si le site de formation n'est pas installé, l'étape 9 de `deploy/install.sh`
le met en place ; voir `docs/mode-formation.md`.

### 3.2 Les scripts

Tous dans `scripts/stress/`, sans dépendance nouvelle (`httpx` est déjà au
`requirements.txt`). Ils se lancent depuis le poste de Simon, jamais depuis le
serveur : le but est de mesurer ce que voit un téléphone, réseau compris.

| Script | Rôle |
|---|---|
| `charge.py` | Simule bénévoles, visiteurs, écran de salle. Mesure les temps de réponse, relève les incidents. |
| `course.py` | Provoque volontairement les accès simultanés du § 2 et dit s'ils cassent quelque chose. Depuis le correctif du § 2.4, c'est le test de non-régression du prêt. |
| `coherence.py` | Contrôle 8 invariants directement dans la base SQLite, dont la présence des index UNIQUE (I8). Lecture seule. À lancer sur le serveur. |
| `mesures.sh` | Échantillonne CPU, mémoire, disque, WAL et journal du service pendant la séance. À lancer sur le serveur. |

### 3.3 Préparatifs

```bash
# Sur le poste de test, dans le dépôt :
source .venv/bin/activate
export CIBLE=https://formation.example.fr
export JETON='…'          # jeton bénévole de l'instance de FORMATION
```

Le jeton se lit sur `/admin/jeton` de l'instance de formation. **Ne jamais
utiliser celui de production.**

Sur le site de formation, avant chaque séance : tableau de bord admin →
**« Réinitialiser les données de formation »**, pour repartir d'un état connu.

Deux sessions SSH ouvertes sur le VPS sont confortables : une pour `mesures.sh`,
une pour `coherence.py`.

---

## 4. Déroulé

Compter **2 h 30** pour les phases 0 à 3, faisables seul, plus une nuit en
arrière-plan pour la phase 4 et 30 minutes à plusieurs pour la phase 5.

### Phase 0 — Point de départ (20 min, seul)

1. Réinitialiser les données de formation.
2. Noter la version déployée (`/apropos`) et les caractéristiques du VPS
   (`nproc`, `free -m`, `df -h`).
3. Lancer un contrôle à vide :

   ```bash
   # sur le VPS
   cd /opt/ludotex && source .venv/bin/activate
   python -m scripts.stress.coherence /var/lib/ludotex-formation/pret-jeux.db
   ```

   Les **8** invariants doivent être verts. Si non, le reste du protocole est
   sans objet : la base part déjà bancale.

   Le 8ᵉ est né du correctif du § 2.4 : il vérifie que les deux index UNIQUE
   sont réellement posés. Ils peuvent manquer **sans que rien ne le signale à
   l'écran**, `db._creer_index_uniques` étant conçu pour avertir et laisser
   démarrer (§ 2.4, point 2). Si I8 est rouge, l'application tourne sans son
   filet.

4. Regarder le journal du démarrage, seul endroit où cet avertissement
   apparaît :

   ```bash
   journalctl -u ludotex-formation --since "10 min ago" | grep -i "filet de sécurité"
   ```

   Rien = tout va bien.

### Phase 1 — Accès simultanés (20 min, seul)

C'est la phase la plus importante. Elle répond à « peut-on perdre une pièce
d'identité ? ».

Depuis le correctif du § 2.4, **elle a changé de nature** : ce n'est plus une
recherche de défaut, c'est un **test de non-régression** — le premier à
rejouer après toute mise à jour touchant au prêt, et le seul qui vérifie le
correctif sur la machine réelle plutôt qu'en pytest.

```bash
python -m scripts.stress.course --url "$CIBLE" --jeton "$JETON" \
    --tireurs 8 --manches 30
```

Puis, pour voir l'état réel de la base plutôt que ce que l'écran affiche :

```bash
python -m scripts.stress.course --url "$CIBLE" --jeton "$JETON" \
    --tireurs 8 --manches 5 --laisser-en-place
# puis, sur le VPS :
python -m scripts.stress.coherence /var/lib/ludotex-formation/pret-jeux.db
```

> **Lire le résultat.** Le script conclut lui-même, mais vérifier le détail
> plutôt que le seul verdict :
>
> - scénario A — **autant de numéros que de tireurs, tous distincts**, à
>   chaque manche. Un verdict vert avec moins de numéros que de tireurs
>   signalerait des prêts refusés, pas une course évitée ;
> - scénario B — **exactement un `prete`**, les autres en `deja_sorti` ;
> - **`occupe` : attendu à zéro.** C'est le message de conflit ajouté par le
>   correctif. Sa présence n'est pas une anomalie (rien n'a été enregistré, la
>   reprise est en un tap) mais elle indique que le verrou d'écriture a été
>   disputé plus de 15 s — ce qui, à huit bénévoles, ne devrait jamais arriver.
>   Quelques `occupe` dans une salve de 8 = à comprendre ; en phase 2 = un vrai
>   sujet.
>
> Si une anomalie réapparaît, c'est une **régression** : voir § 6.1.

**Refaire ensuite la même chose sur une table de pochettes vide**, c'est-à-dire
juste après une réinitialisation des données de formation et sans avoir prêté
quoi que ce soit. C'est la configuration de l'ouverture de soirée, celle qui
produisait des erreurs 500 avant correctif (branche `MAX + 1`, § 2.1) — le
chemin de code est différent, il se teste donc à part. Attendu : les numéros
1 à 8, zéro 500.

### Phase 2 — Charge nominale, puis pointe (45 min, seul)

Sur le VPS, dans une session SSH :

```bash
cd /opt/ludotex/scripts/stress
./mesures.sh 900 ludotex-formation /var/lib/ludotex-formation
```

Depuis le poste, dans la foulée :

```bash
python -m scripts.stress.charge --url "$CIBLE" --jeton "$JETON" \
    --profil nominal --duree 600
```

Puis, après une réinitialisation, le coup de feu de l'ouverture :

```bash
python -m scripts.stress.charge --url "$CIBLE" --jeton "$JETON" \
    --profil pointe --duree 600
```

Le profil « pointe » inclut un export PDF des statistiques toutes les dix
minutes : c'est la requête la plus lourde du site, et il est utile de savoir ce
que les bénévoles ressentent pendant qu'elle s'exécute.

> **Nouveauté depuis le correctif.** Les écritures de prêt sont désormais
> **sérialisées** : `BEGIN IMMEDIATE` fait que deux opérations concurrentes
> s'attendent au lieu de se marcher dessus. Cette phase est donc la seule à
> pouvoir en mesurer le coût réel, sur des rafales soutenues plutôt que sur des
> salves isolées. Deux choses à lire dans le rapport, en plus des seuils
> habituels :
>
> - la **médiane et le p99 de `POST preter`** — si le p99 s'écarte franchement
>   de la médiane alors que le CPU reste bas, c'est de l'attente de verrou, pas
>   de la charge ;
> - le **nombre de `occupe`** dans « Résultats métier » : attendu à zéro. Il
>   compte les fois où le verrou n'a pas été obtenu en 15 s
>   (`db.TIMEOUT_ECRITURE_S`).
>
> Les transactions durent une fraction de milliseconde : à ce rythme, l'attente
> doit rester invisible. Si elle ne l'est pas, c'est un résultat en soi.

### Phase 3 — Où ça casse (20 min, seul)

Chercher le point de rupture, non pour le corriger, mais pour connaître la
marge. Cette phase a gagné en intérêt depuis que les écritures se sérialisent :
elle situe le nombre de bénévoles simultanés à partir duquel la file d'attente
devient perceptible, puis celui où le délai de 15 s est dépassé et où les
`occupe` apparaissent.

```bash
for n in 8 16 32 64; do
  echo "=== $n bénévoles simulés ==="
  python -m scripts.stress.charge --url "$CIBLE" --jeton "$JETON" \
      --profil rupture --benevoles $n --duree 120
done
```

Relever à quel palier la médiane des `POST preter` dépasse une seconde, et à
quel palier apparaissent les premières erreurs. Le rapport entre ce palier et
les 8 bénévoles réels est la marge de sécurité.

### Phase 4 — La nuit (à lancer et laisser)

```bash
# VPS
./mesures.sh 28800 ludotex-formation /var/lib/ludotex-formation
# poste, dans un terminal qui peut rester ouvert (ou tmux/screen sur une machine allumée)
python -m scripts.stress.charge --url "$CIBLE" --jeton "$JETON" \
    --profil soak --duree 28800
```

Pendant ce temps, **déclencher une sauvegarde complète** depuis
`/admin/donnees` de l'instance de formation, et vérifier qu'aucune erreur
n'apparaît côté charge : c'est le geste qu'on voudra faire en pleine soirée.

Au réveil : `coherence.py`, puis relire la colonne mémoire du CSV de
`mesures.sh` (une consommation qui monte sans jamais redescendre est le signe
d'une fuite) et la colonne WAL.

### Phase 5 — Passage terrain (30 min, à 2 ou 3)

Les scripts ne testent pas la caméra, le wifi de la salle, ni le geste. Cette
phase-là ne se simule pas.

Sur place si possible, sinon avec le wifi le plus proche des conditions
réelles. Chacun sur son téléphone, jeton de formation activé :

1. **Le compte à rebours.** Se placer devant la même boîte, compter à voix
   haute « trois, deux, un », et appuyer sur « Prêter » ensemble. Comparer les
   numéros de pochette affichés à l'écran de chacun. Recommencer dix fois,
   avec des boîtes différentes puis avec la même boîte. C'est le § 2 rejoué à
   la main, dans les conditions du stand.

   Depuis le correctif, l'intérêt n'est plus de reproduire le défaut — trois
   personnes ne synchronisent pas à la milliseconde, le script le fait bien
   mieux. Ce qu'on regarde ici, c'est **ce que voient les bénévoles quand ça se
   produit** : sur la même boîte, un seul doit obtenir un numéro et les autres
   lire « Cet exemplaire était déjà sorti », message qu'il faut vérifier
   compréhensible sans explication. Et si « Rien n'a été enregistré » apparaît,
   vérifier que la personne comprend qu'elle doit réappuyer.
2. **Le scan en rafale.** Chacun scanne dix boîtes d'affilée, en même temps que
   les autres. Chronométrer le temps entre le scan et l'affichage du numéro.
3. **Le réseau qui flanche.** Passer un téléphone en mode avion pendant qu'une
   page charge, revenir. Vérifier qu'on retombe sur un message compréhensible
   et une action de rattrapage, jamais sur une erreur brute.
4. **Le double-appui.** Appuyer deux fois vite sur « Prêter ». Vérifier que le
   bouton se grise (« Un instant… ») et qu'un seul prêt est ouvert.
5. **Retour en arrière.** Utiliser le bouton « page précédente » après un prêt,
   réappuyer sur « Prêter ». Vérifier le message.

Noter tout ce qui surprend, même mineur : un libellé mal compris compte autant
qu'une erreur technique le soir de l'événement.

---

## 5. Seuils de réussite

| Indicateur | Attendu | Où le lire |
|---|---|---|
| Invariants de la base | **8/8 verts**, en toute circonstance | `coherence.py` |
| Index UNIQUE en place (I8) | **présents** — sinon le filet est absent | `coherence.py`, journal du service |
| Numéro de pochette attribué deux fois | **zéro**, y compris phase 1 | `course.py`, `coherence.py` |
| Prêts multiples sur une boîte | **zéro** | `course.py`, `coherence.py` |
| Résultats `occupe` (conflit d'accès) | **zéro** jusqu'au profil « pointe » | « Résultats métier » de `charge.py`, verdict de `course.py` |
| Erreurs HTTP 5xx | **zéro** | rapport de `charge.py` |
| `POST preter` — médiane | < 300 ms | rapport de `charge.py` |
| `POST preter` — p90 | < 800 ms | rapport de `charge.py` |
| `GET /catalogue` — p90 | < 1,5 s (609 titres, page lourde) | rapport de `charge.py` |
| CPU du service au pic | < 60 % d'un cœur | `mesures.sh` |
| Mémoire du service sur la nuit | stable (±20 %) | CSV de `mesures.sh` |
| Fichiers WAL | < 10 Mo, redescendent | `coherence.py`, `mesures.sh` |
| Espace disque libre | > 1 Go à tout moment | `mesures.sh` |

Une ligne rouge sur les quatre premières, ou sur « Erreurs HTTP 5xx », est
**bloquante** : elle touche l'intégrité des données. « `occupe` » est à part —
rien n'est abîmé quand ce message apparaît, mais à huit bénévoles il ne devrait
jamais se montrer, donc sa présence est un signal à comprendre avant
l'événement. Les autres lignes sont des indicateurs de confort, à arbitrer.

---

## 6. Que faire des résultats

### 6.1 Si les accès simultanés cassent quelque chose

> Les trois pistes ci-dessous ont été **mises en œuvre toutes les trois** le
> 2 août 2026 — voir § 2.4 pour ce qui a été décidé et les écarts. Elles
> restent décrites ici comme grille de lecture si un défaut du même genre
> réapparaît ailleurs.

Trois pistes, dans l'ordre de solidité :

1. **Rendre l'attribution indivisible.** Ouvrir la transaction en écriture
   *avant* la lecture (`BEGIN IMMEDIATE`) dans `plus_petit_numero_libre`, de
   sorte qu'aucune autre requête ne puisse lire le même « plus petit numéro
   libre » avant que la réservation ne soit écrite. C'est le correctif
   minimal, et il porte sur une seule fonction.
2. **Poser un filet dans le schéma.** Un index UNIQUE partiel sur les prêts
   ouverts (une pochette ne peut être détenue que par un prêt à la fois, une
   boîte ne peut avoir qu'un prêt ouvert). Ne corrige rien tout seul, mais
   transforme un silence en erreur visible — ce qui vaut mieux qu'une pièce
   d'identité perdue.
3. **Étendre le même traitement au contrôle « déjà sorti »** de
   `routes/pret.py`, qui souffre du même écart entre la vérification et
   l'action.

Ces correctifs touchent le cœur métier : ils méritent leur propre session,
avec des tests dédiés (une salve de prêts concurrents dans la suite pytest,
sur le modèle de `course.py` mais en process unique).

### 6.2 Si une anomalie réapparaît malgré le correctif

Ce n'est plus une découverte, c'est une **régression**, et l'ordre du
diagnostic compte : commencer par vérifier que la protection est bien là avant
de soupçonner qu'elle est insuffisante.

1. **I8 est-il vert ?** Si les index UNIQUE manquent, la première question
   n'est pas « pourquoi le doublon ? » mais « pourquoi le filet n'est-il pas
   posé ? ». Le journal du service au démarrage porte la réponse.
2. **La base est-elle antérieure au correctif ?** Une restauration de
   sauvegarde ancienne rejoue bien `init_db()` (volet 3 de D5), mais si la base
   restaurée contenait déjà une incohérence, la création des index a été
   refusée — silencieusement pour l'utilisateur, avec un avertissement au
   journal. C'est le scénario le plus probable en pratique.
3. **Un chemin d'écriture a-t-il échappé à `services.transaction` ?** Le § 2.4
   liste les cinq fonctions couvertes. Une écriture ajoutée depuis, ou un accès
   direct à `prets`/`pochettes` ailleurs, sortirait de la protection.
4. **Le module est-il celui qu'on croit ?** Le § 2.4 signale que le reste du
   module tournois, le planning et le programme n'ont pas été audités.

### 6.3 Si des « occupe » apparaissent

Le message « Rien n'a été enregistré » signifie que le verrou d'écriture n'a
pas été obtenu en 15 s, ou qu'un index a refusé un doublon. Aucune donnée n'est
abîmée, et le bénévole réappuie — mais à l'échelle de cet événement, ce message
ne devrait pas exister.

Regarder d'abord si une écriture longue tient le verrou : un import de
catalogue, une restauration de sauvegarde, une clôture de tous les prêts. Ces
opérations-là sont légitimement lentes, et il est utile de savoir qu'elles
bloquent le stand pendant qu'elles tournent — c'est une consigne d'exploitation
(« ne pas importer le catalogue pendant l'événement »), pas forcément un
correctif.

Si aucune écriture longue n'était en cours, c'est que la contention vient du
prêt lui-même, et le § 6.4 s'applique.

### 6.4 Si les temps de réponse décrochent

Regarder d'abord **où** : si c'est `GET /catalogue` et `GET /stats` mais pas
`POST preter`, le stand n'est pas gêné et le sujet peut attendre. Le VPS n'est
probablement pas en cause avant plusieurs dizaines de bénévoles simultanés.

L'application tourne avec **un seul worker uvicorn** — décision assumée, les
sessions admin et la limitation de débit vivent en mémoire du processus. En
ajouter demanderait de déplacer ces deux mécanismes ; ce n'est pas un réglage
à changer à la légère, et ce n'est pas nécessaire à cette échelle.

### 6.5 Dans tous les cas

Consigner les chiffres dans ce fichier (§ 8) : la prochaine montée de version
aura une référence à laquelle se comparer.

---

## 7. Et un agent Claude dans tout ça ?

La question posée était : peut-on confier le test multi-accès à un agent ?

**Pour générer la charge, non.** Un agent pilotant un navigateur agit à la
seconde, pas à la milliseconde ; il coûte cher, ne reproduit pas deux fois la
même séquence, et surtout il est incapable de faire partir huit requêtes
ensemble — ce qui est précisément le phénomène à observer. Les défauts du § 2
seraient passés inaperçus.

**Pour tout le reste, oui, et c'est ainsi que ce protocole a été produit** :
lire le code pour identifier où sont les zones à risque, écrire les scripts,
les faire tourner contre une instance locale, interpréter les rapports,
proposer les correctifs. La simultanéité vient d'un script ; l'analyse peut
venir d'un agent.

La phase 5 (passage terrain) ne s'automatise pas non plus : caméra, wifi de
salle, geste du bénévole. Deux personnes et vingt minutes valent mieux que
n'importe quel outil.

---

## 8. Feuille de relevé

À remplir pendant la séance, une colonne par passage.

```
Date : ……………………   Version déployée : ……………   VPS : ……… vCPU / ……… Go RAM

Phase 0 — point de départ
  coherence.py                      invariants verts : ………/8   (I8 : ……)
  journal « filet de sécurité »     absent / présent : ………

Phase 1 — accès simultanés (non-régression)
  scénario A (boîtes différentes)   numéros distincts : ………/8 par manche
                                    doublons : ………/……… manches
  scénario A, pochettes vides       numéros obtenus ……… → ………   erreurs 500 : ………
  scénario B (même boîte)           prêts ouverts par salve : ……… (attendu 1)
  résultats « occupe »              ………  (attendu 0)
  coherence.py                      invariants verts : ………/8

Phase 2 — nominal (600 s)
  POST preter   méd. ……… ms   p90 ……… ms   p99 ……… ms
  GET catalogue méd. ……… ms   p90 ……… ms
  erreurs 5xx ………   incidents ………   « occupe » ………
  CPU pic ……… %   RAM ……… Mo

Phase 2 — pointe (600 s)
  POST preter   méd. ……… ms   p90 ……… ms   p99 ……… ms
  écart p99/médiane (attente de verrou ?) : ………
  effet d'un export PDF sur les temps de prêt : ………………………………
  erreurs 5xx ………   « occupe » ………
  CPU pic ……… %   RAM ……… Mo

Phase 3 — rupture
  décrochage (méd. > 1 s) à ……… bénévoles simulés
  premiers « occupe » à ……… bénévoles simulés
  premières erreurs à ……… bénévoles simulés

Phase 4 — nuit (8 h)
  RAM début ……… Mo   fin ……… Mo
  WAL max ……… Kio    disque libre min ……… Mo
  sauvegarde pendant charge : OK / KO
  « occupe » pendant la sauvegarde : ………
  invariants au réveil : ………/8

Phase 5 — terrain (…… personnes)
  compte à rebours, même boîte      : un seul numéro attribué ? ………
                                      message des autres compris ? ………
  compte à rebours, boîtes ≠        : numéros en double ? ………
  scan → affichage du numéro        : ……… s en moyenne
  coupure réseau                    : message clair ? ………
  double-appui                      : un seul prêt ? ………
  observations : ………………………………………………………………………………
```

---

## 9. Limites connues de ce protocole

- Il ne teste pas la **production**, seulement une instance identique sur la
  même machine. Les volumes diffèrent : 703 boîtes et 609 titres en production
  contre une vingtaine sur le site de formation. Les temps de réponse du
  catalogue et des statistiques sont donc **optimistes**. Pour les mesurer
  fidèlement, importer le vrai catalogue CSV sur le site de formation avant la
  phase 2 (`/admin/donnees`) — les prêts, eux, restent jetables.
- Il ne teste ni le **module tournois**, ni le **planning bénévole**, ni le
  **programme du week-end** : ces modules n'ont pas d'écriture concurrente
  comparable, et ce ne sont pas eux qui tiennent le stand.
- **La course sur les inscriptions de tournoi n'est vérifiée que par pytest.**
  Le § 2.4 la corrige, mais aucun script ne la rejoue en HTTP, et le § 2.4
  rappelle qu'**aucun index ne peut lui servir de filet** : un plafond de
  places est un comptage, pas une unicité. La couverture est donc plus mince
  que côté prêt. Un `course_inscriptions.py` sur le modèle de `course.py`
  (N inscriptions simultanées sur la dernière place d'un tournoi) reste à
  écrire si le besoin se confirme — l'enjeu, une chaise en trop, ne le
  justifiait pas jusqu'ici.
- La **limitation de débit par IP** (`RATE_LIMIT_PER_MINUTE`) ne s'applique
  qu'à la page d'activation et à la connexion admin, pas aux actions de prêt.
  Tous les bénévoles étant derrière la même IP publique dans la salle, cela
  aurait pu poser problème ; ce n'est pas le cas. Rien à tester, mais bon à
  savoir.
- Le scanner caméra (`getUserMedia`, jsQR) ne peut être évalué qu'en phase 5,
  sur de vrais téléphones.
- `charge.py` lit le résultat des actions dans le **HTML** de `pret.html`. Si
  ce gabarit change, les expressions de `scripts/stress/commun.py` sont à
  revoir — sinon le script rapportera « inconnu » partout.
