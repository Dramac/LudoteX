# Jeux éphémères — conception

Série « comptoir », lots 8a (imprimer, nommer, prêter) et 8b (libérer les
QR en fin d'événement), **livrés**.

## 1. Le besoin

Des jeux arrivent au comptoir **sans étiquette** : jeux apportés par des
bénévoles, jeux trop récents pour avoir été étiquetés. Prêtés hors
application, ils échappent à l'anti-vol (pochette) et aux statistiques.

Réponse retenue : une **feuille de QR éphémères** imprimée d'avance, sans nom
de jeu. Le jour J, on colle un QR vierge sur la boîte, on écrit le nom au
crayon, et le **premier scan** propose de nommer le jeu ; ensuite la boîte se
prête comme les autres.

## 2. Le code éphémère

- Forme : **`X` majuscule et deux chiffres**, de `X01` à `X99`.
  - `X` est le seul préfixe libre : chiffres seuls au tableur, `E…` pour les
    extensions venues du tableur, `A…` pour les boîtes créées en
    administration (`services.PREFIXE_ID_ADMIN`).
  - Trois signes : il se tape vite quand le QR ne se lit pas. 99 codes
    suffisent à une édition.
  - La saisie au clavier rapproche les codes (`services.cle_saisie` : casse,
    espaces, zéros de tête) : `x1`, `X01` et `X001` désignent `X01`. Aucun
    code du catalogue ne s'y rapproche (aucun ne commence par `X`).
- **Un seul domicile** : `services.est_code_ephemere` (forme exacte),
  `services.code_ephemere_saisi` (forme rapprochée d'une saisie),
  `services.condition_non_ephemere` (fragment SQL, vérifié contre la fonction
  par un test). Aucune colonne, aucune table : la forme suffit.
- **Un code n'existe en base qu'une fois nommé.** Imprimer ne crée rien. Un
  code **vierge** est un code de forme éphémère sans boîte en base.
- **Invariant** : seule la forme **exacte** du code imprimé est éphémère.
  Une boîte libérée en fin d'événement prend un code d'une autre forme
  (§ 7) ; elle cesse alors d'être éphémère, et le code imprimé redevient
  vierge. Vérifié par les tests : `X01-2026` n'est ni éphémère
  (`est_code_ephemere`), ni rapproché d'un code éphémère à la saisie
  (`code_ephemere_saisi`), ni écarté comme tel par l'import.
- Personne d'autre ne fabrique ce code : `prochain_id_exemplaire` a son
  préfixe ; l'import **met de côté** toute ligne dont le code se lit comme un
  code éphémère, et le dit (`import_csv.ecarter_ephemeres`).

## 3. La feuille

`/admin/etiquettes`, bloc « QR éphémères » : du numéro … au numéro …, bouton
**Imprimer une feuille de QR éphémères**. Même planche, même mise en page,
même logo que les étiquettes du catalogue (`etiquettes.image_etiquette`,
drapeau `ephemere`) :

- le nom laisse place à « Nom du jeu : » et une ligne de pointillés ;
- le code est **toujours** imprimé, quel que soit le réglage du bureau ;
- la case du code de classement porte le mot **« éphémère »** — dans le
  cadre existant, pas sous le QR : ajouter de la hauteur rétrécirait le QR ;
- l'étiquette a la taille d'une étiquette du catalogue (test).

Réimprimer un code déjà nommé donne la même étiquette vierge, voulu : le nom
est au crayon. La réimpression depuis la fiche d'administration d'une boîte
éphémère donne aussi l'étiquette vierge.

## 4. Nommer

Un QR vierge mène à `/jeu/<code>` (appareil photo) ou `/pret/<code>`
(scanner, saisie manuelle).

- **Bénévole** : « Ce QR éphémère n'est rattaché à aucun jeu ». Sur
  `/pret/<code>`, le formulaire est là ; sur `/jeu/<code>`, le bouton
  **Nommer ce jeu** y mène.
- **Visiteur** : « Ce jeu n'est pas encore enregistré. Demandez à un
  bénévole. », statut 404, aucun formulaire.
- **Code inconnu non éphémère** : inchangé (« Exemplaire inconnu »).

Le formulaire : **Nom du jeu**, 80 caractères au plus, suggestions du
catalogue par `<datalist>` (sans JS), consigne sous le champ.
`POST /pret/<code>/nom` (jeton), en une transaction
(`services.nommer_code_ephemere`) :

- même référence qu'un titre existant (`slug_titre`) : la boîte rejoint ce
  titre, **rien du titre ne change** (pas l'UPSERT de `creer_jeu`) ;
- sinon : un titre nouveau, **nom seul** ;
- puis l'écran de prêt de la boîte, **Prêter** à un tap.

Ne jamais bloquer : nom vide ou trop long → le formulaire revient, message et
saisie conservés ; QR nommé entre-temps par un autre bénévole → l'écran de
prêt montre le nom en base ; le même nom envoyé deux fois → succès.

**Corriger le nom** : lien sur l'écran de prêt d'une boîte éphémère non
retirée, pour tout bénévole tant qu'elle n'a **jamais été sortie** (aucun
prêt, quel qu'en soit le motif), puis pour l'**administrateur connecté**
seulement. Le titre quitté, s'il n'a plus de boîte, est supprimé ; un titre
du catalogue n'est jamais renommé depuis le comptoir ; les prêts suivent la
boîte. Écart au prompt du lot : il renvoyait le bureau au rattachement de la
fiche d'administration, mais celui-ci déplace **toutes** les boîtes d'un
jeu ; une boîte éphémère rattachée par erreur à un jeu du catalogue n'en
sortirait pas seule.

## 5. La porte

Le champ « Nom du jeu » est un texte libre saisi par n'importe quel
bénévole. Conditions :

- consigne visible sous le champ, reliée par `aria-describedby` : « Le nom
  du jeu seulement — jamais le nom de la personne qui l'a apporté. » ;
- au journal, le nom n'entre qu'en `objet` (`ephemere_nomme`,
  `ephemere_nom_corrige`), comme le nom du jeu de toute action de prêt ; un
  texte **refusé** n'y entre jamais ;
- `wiki/Rgpd.md` porte la ligne : qui saisit, qui lit, ce qui efface (la
  correction du nom, par le bénévole puis par le bureau).

Risque résiduel : le nom d'un jeu apporté peut rester visible au catalogue
public (le titre nouveau y est). La consigne est la seule barrière.

## 6. La boîte nommée

Une boîte ordinaire : prêt, retour, transfert, échange, tournoi, démo,
statistiques. Visible au catalogue public comme tout jeu, la fiche porte
« Boîte de passage : elle est prêtée le temps de l'événement. ».

Exclue de l'**export du catalogue** (et le titre qui n'a qu'elle) et des
**étiquettes en lot** : le tableur du bureau est le parc permanent. Les jamais
sortis d'une période (`app/achats.py`, `avec_retirees=True`) la décrivent.

## 7. Libérer les QR en fin d'événement

Après l'événement, les jeux de passage repartent et la feuille de QR doit
resservir. **Exception assumée** à « un code de boîte ne change jamais »
(`docs/specification.md` § 3.1), limitée aux codes de forme éphémère : leur
QR n'a jamais désigné un objet durable.

### 7.1 Le geste

Un geste du **bureau** (administration), après la clôture des prêts, jamais
lié à elle : la clôture est déjà chargée, et un bureau peut garder un jeu de
passage d'une édition à l'autre.

- `/admin/etiquettes`, bloc « QR éphémères » : lien **Libérer les QR
  éphémères** dès qu'un code est nommé. Après **Clôturer tous les prêts en
  cours**, le tableau de bord le propose aussi s'il reste des codes nommés.
- `/admin/etiquettes/ephemeres/liberer` (une **lecture**) : chaque code
  nommé, son jeu, le code qu'il prendra, ou « sortie : ne sera pas
  libérée ». Une liste, pas un tableau : elle se lit à 375 px.
- Bouton **Libérer ces QR éphémères** : libère les codes **montrés** (champs
  cachés), chacun dans sa transaction. Un QR nommé au comptoir entre
  l'affichage et l'envoi n'est pas libéré sans avoir été vu.
- Résultat sur le même écran : combien sont libérés (`role="status"`), les
  boîtes restées dehors nommées avec la marche à suivre — rendre au
  comptoir, ou clôturer les prêts — (`role="alert"`). **Une boîte sortie
  n'empêche jamais les autres.** Double envoi : « Ces QR éphémères étaient
  déjà libérés : rien n'a changé. »

Pas de libération boîte par boîte depuis la fiche d'administration : trente
gestes au lieu d'un, en fin de journée, pour un besoin que personne n'a
exprimé.

### 7.2 Le nouveau code

`services.code_libere`, seul domicile : l'ancien code, un tiret, l'année
(heure locale) de la libération — `X01-2026` ; `X01-2026-2`, `-3`… si la
**clé de saisie** est déjà prise (deux libérations du même QR la même
année, ou un code `X1-2026` venu du tableur, qui rendrait la saisie
ambiguë). Le bureau le lit (« c'était le X01 de 2026 ») ; la saisie
`x1-2026` le retrouve, `x1` désigne toujours le QR vierge ;
`prochain_id_exemplaire` ne fabrique que des `A…` ; une ligne de tableur
`X01-2026` est celle d'une boîte retirée, mise de côté comme telle.

### 7.3 Le renommage

`services.liberer_code_ephemere`, sous `transaction`, pour une boîte :

1. relire ; ne rien écrire si le code n'est pas éphémère (`non_ephemere`),
   s'il est vierge (`vierge` : double envoi, rien à faire) ou si la boîte
   est sortie (`sortie`, `pret_en_cours` : prêt, tournoi, démo) ;
2. recopier la ligne `exemplaires` sous le nouveau code, **toutes
   colonnes**, lues par `PRAGMA table_info` ;
3. repointer `services.TABLES_CODE_DE_BOITE` (`prets`, `signalements` :
   clés étrangères sans cascade). Aucune autre table des trois bases ne
   porte un code de boîte (relevé par `PRAGMA foreign_key_list` et
   `table_info`) ; un test compare la liste au schéma ;
4. supprimer l'ancienne ligne ;
5. retirer la nouvelle : maintenant, motif `passage` (« Jeu de passage,
   reparti »). Une boîte déjà retirée (perdue pendant l'événement) garde sa
   date et son motif.

Tout ou rien (test : un déclencheur fait échouer l'étape 4 ou 5, aucune
ligne n'a bougé). Une course avec un prêt : le prêt passe avant ou après,
jamais au milieu. Les prêts, le signalement ouvert (visible au carnet sous
le nouveau code) et le nom du jeu suivent la boîte ; les statistiques de
l'édition sont identiques avant et après (test sur les quatre lecteurs).

La boîte libérée est une boîte retirée ordinaire : hors catalogue, export,
étiquettes ; sa page de prêt dit « Boîte retirée ». **Remettre en service**
(le jeu a finalement été donné) en fait une boîte du parc au code
`X01-2026`, au catalogue, dans l'export et les étiquettes en lot (son
étiquette est à réimprimer).

Le motif `passage` n'est **pas proposé** au retrait ordinaire
(`services.MOTIFS_RETRAIT_PROPOSES`) : il n'a de sens que pour la
libération. Un envoi forgé est enregistré « sans motif ».

### 7.4 Les périodes passées : `entree_le`

Une boîte nommée en 2026 était jugée « au parc » pendant 2025 : rien ne
disait quand elle était entrée. Mesuré avant ce lot, sur une base d'essai :
le bilan 2025 passait de 2 à 3 jeux et listait le jeu de passage parmi les
**jamais sortis** de 2025 — dès le nommage, libération ou non.

`exemplaires.entree_le` (UTC ISO, nullable, migration idempotente) : posée
par `nommer_code_ephemere`, recopiée par la libération. NULL = au parc
depuis toujours (tableur, administration, toute base d'avant) : une base
existante donne exactement les mêmes chiffres. `condition_au_parc_pendant`
prend `debut` **et** `fin` : retirée après le début, **et** entrée avant la
fin (exclusive). Ni début ni fin (« toutes périodes ») : `1 = 1` ; une
période sans fin n'écarte personne selon son entrée.

### 7.5 Le journal

`ephemere_libere`, module `catalogue`, une ligne par boîte libérée : `ref` =
« X01 → X01-2026 », `objet` = le nom du jeu en base. Le retrait qui
l'accompagne n'écrit pas `exemplaire_retire` : c'est le même geste.

Le journal garde, dans ses rotations, des lignes où `X01` désignait la boîte
libérée (`ephemere_nomme`, prêts) : un code réemployé y désigne deux boîtes
successives. **On ne réécrit pas le journal** ; la ligne `ephemere_libere`
fait le lien.

### 7.6 La porte

La libération n'efface pas le nom du jeu : il reste en base, attaché au
titre (fiche d'administration, statistiques). Il quitte le catalogue public
avec la boîte, mais la page du code libéré (`/jeu/X01-2026`, qu'aucun QR ne
porte) le montre encore à qui tape l'adresse, comme pour toute boîte
retirée. Pour l'effacer : **Corriger le nom** avant la libération, ou la
fiche d'administration du jeu ensuite. La libération n'ajoute aucun champ
libre.
