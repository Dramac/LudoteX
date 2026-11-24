# Jeux éphémères — conception

Série « comptoir », lots 8a (imprimer, nommer, prêter : **livré**) et 8b
(libérer les QR en fin d'événement : **à venir**).

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
- **Invariant pour 8b** : seule la forme **exacte** du code imprimé est
  éphémère. Une boîte renommée en fin d'événement prendra un code d'une autre
  forme ; elle cessera alors d'être éphémère, et le code imprimé redeviendra
  vierge.
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

## 7. Ce que le lot 8b devra faire

Libérer les QR en fin d'événement :

1. **Renommer** la boîte d'un code éphémère vers un code d'une autre forme
   (à choisir en 8b, jamais reconnu par `est_code_ephemere`). Exception
   assumée à « un code ne change jamais », pour les seuls codes éphémères.
2. Cascade **manuelle**, sous une transaction : `prets.id_exemplaire` et
   `signalements.id_exemplaire` référencent `exemplaires.id_exemplaire`
   **sans** `ON UPDATE CASCADE` (relevé par `PRAGMA foreign_key_list`).
   Avec `foreign_keys = ON`, il faut insérer la boîte sous son nouveau code,
   repointer prêts et signalements, puis supprimer l'ancienne ligne.
3. **Retirer** la boîte renommée (`retiree_le`), pour qu'elle quitte le
   catalogue en gardant ses prêts. Limite connue de comptoir-6 : une seule
   date de retrait, aucune date d'entrée.
4. Le code imprimé redevient **vierge** : la feuille resservira.
5. Refuser de renommer une boîte **sortie** (prêt ouvert).
6. Le journal porte des codes en `ref` (`exemplaire_retire`,
   `ephemere_nomme`) : un code réemployé y désignera deux boîtes
   successives ; c'est un fichier à rotation, à ne pas réécrire.
