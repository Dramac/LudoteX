# Jeux demandés au comptoir — conception

Série « comptoir », lot 11, **livré**.

## 1. Le besoin

Au comptoir, des visiteurs demandent des jeux que la ludothèque n'a pas, ou
plus à ce moment-là : toutes les boîtes sont dehors. Rien ne les notait. Le
bureau voudrait s'en servir pour ses achats.

Réponse retenue : les bénévoles **notent chaque demande** en un geste ; les
demandes déjà notées sont **suggérées** à la frappe, pour qu'une même demande
se compte au lieu de s'écrire de dix façons ; le **bureau** relit la liste
groupée, fusionne les graphies, exporte, puis purge.

## 2. Modèle

Une table de la base de **prêt**, `jeux_demandes` (`app/models.py`) :

| Colonne | Rôle |
| --- | --- |
| `id` | `AUTOINCREMENT` : un numéro n'est jamais réattribué après une purge |
| `nom` | le nom tel que tapé, nettoyé des espaces, 80 caractères au plus |
| `cle` | clé de regroupement, `services.slug_titre(nom)` : majuscules, sans accents ni ponctuation — la clé des titres du catalogue |
| `demandee_le` | date UTC ISO |
| `jeton_formulaire` | valeur aléatoire du formulaire affiché, UNIQUE (§ 4) |

**Une ligne par demande**, pas un compteur par jeu : les dates permettent de
filtrer par édition (`editions_evenement`) et de dater la première et la
dernière demande ; fusionner deux graphies revient à réécrire `cle`. Le coût
(quelques centaines de lignes par édition) est négligeable.

**Rien d'autre** : aucune colonne d'origine (ni appareil, ni bénévole), aucun
champ « pour qui ». Table neuve, donc `CREATE TABLE IF NOT EXISTS` dans
`SCHEMA_STATEMENTS` suffit : `update.sh` redémarre le service, `init_db` la
crée.

Le **nom affiché** d'un groupe est sa graphie la plus fréquente, à égalité la
plus récente (`jeux_demandes._noms_affiches`), calculée sur toute la table :
il ne change pas selon la période regardée.

Un groupe est **au catalogue** quand un titre porte la même clé, par son nom
(`slug_titre(titres.nom)`) ou par sa référence. Aucun lien en base : la
comparaison se fait à la lecture.

## 3. La porte et ses conditions

Le nom est un texte libre tapé par n'importe quel bénévole : quelqu'un finira
par y écrire « Catan pour Mme Untel ». Simon a accepté cette porte le
2026-11-18 à cinq conditions :

| Condition | Où elle est tenue |
| --- | --- |
| Champ court, consigne visible | `maxlength` 80 et `valider_nom` côté serveur ; consigne `jeux_demandes.CONSIGNE` sous le champ, reliée par `aria-describedby` |
| Bénévoles et bureau seulement, jamais public | `Depends(exiger_jeton)` sur les trois routes `/jeux-demandes`, `_garde` sur les six routes `/admin/jeux-demandes` ; lien du catalogue sous `est_benevole` ; aucune lecture depuis `/stats`, `/live`, l'accueil ni un export public ; `/jeux-demandes` dans les préfixes réservés de `tests/test_gardes_routes.py` |
| Jamais au journal | les routes journalisent un nombre, jamais un nom (§ 6) |
| Le bureau relit, fusionne, purge ; suppression vérifiée sur les octets | `fusionner`, `supprimer_groupe`, `purger` sous `secure_delete` ; tests qui lisent le fichier de la base et une archive |
| Suggestions sans JS | `<datalist>` natif |

**Aucun nom dans une adresse.** Une adresse finit dans le journal d'accès de
nginx. Le nom ne voyage donc que dans le corps d'un POST : le passage depuis le
catalogue est un petit formulaire (`POST /jeux-demandes/preparer`, qui
pré-remplit sans rien noter), et les confirmations du bureau sont rendues en
réponse à un POST, pas atteintes par un lien qui porterait la clé. Le texte
cherché au catalogue (`/catalogue?q=…`), lui, était déjà dans ce journal avant
ce lot : c'est une recherche publique.

## 4. Saisie

Page `/jeux-demandes` : **Nom du jeu**, sa consigne, le `<datalist>` des
demandes déjà notées (un nom par groupe), **Noter la demande**, puis les
demandes du jour avec leur nombre.

- **Depuis le catalogue** : un bénévole qui cherche un jeu sans résultat, ou
  dont toutes les boîtes sont dehors, voit **Noter ce jeu comme demandé**,
  qui ouvre la page champ pré-rempli. Pas d'entrée dans le menu bénévole
  partagé, déjà long sur téléphone.
- **Jeu du catalogue** : « Ce jeu est au catalogue (n boîtes, n dehors) »,
  et la demande est notée quand même. Ne jamais bloquer.
- **Refus** (vide, trop long) : le nom revient dans le champ, phrase en
  `role="alert"`, `aria-invalid` ; rien n'est écrit.
- **Double envoi** : le formulaire porte un jeton aléatoire renouvelé à
  chaque affichage, UNIQUE en base (patron de `tombola_tirages`). Un second
  envoi du même formulaire n'écrit rien et le dit (« Cette demande était déjà
  notée… »). Deux demandes réelles coup sur coup passent, chacune depuis une
  page neuve. Écarté : une fenêtre de quelques secondes par clé, qui aurait
  confondu deux visiteurs demandant le même jeu à deux bénévoles.

## 5. Bureau

`/admin/jeux-demandes`, lien **Jeux demandés** du tableau de bord : la liste
groupée (nom, nombre, première et dernière demande, lien vers la fiche
d'administration si le jeu est au catalogue), filtrable par édition.

- **Fusionner** un groupe dans un autre : ses lignes prennent la clé **et le
  nom** de la cible ; dates et nombre sont gardés. Le nom tapé de la source
  n'est pas conservé à côté : moins de texte libre en base. Confirmation.
- **Supprimer** un groupe, **tout purger** : confirmation qui dit que c'est
  définitif.
- **Exporter** (Excel) les groupes et leur nombre, filtre compris.
- Corriger un nom tapé de travers : fusionner suffit (on fond la faute dans la
  bonne graphie). Pas de geste à part.
- Pas de résumé dans `/stats` : la page peut être réglée sur *tous*, et
  l'écran du bureau suffit.

## 6. Journal

Les gestes s'y inscrivent **sans texte** (`docs/conception-journal.md`) :
`jeu_demande_notee` (sans objet), `jeux_demandes_fusionnes`,
`jeux_demandes_supprimes`, `jeux_demandes_purges`, `jeux_demandes_exportes`
(un nombre en `objet`). Motif : comme les annonces, savoir qu'un geste a eu
lieu (et qu'un fichier est sorti) sert la supervision, sans rien garder de la
porte. Un contrôle statique (liste d'autorisation des noms qu'un appel peut
citer) fait tomber la suite si un nom y entre.

## 7. Effacement, archives, formation

- **Clôture de fin d'événement** : les demandes restent, le bureau s'en sert
  après.
- **Réinitialisation de la formation** : effacées (`jeux_demandes.purger`),
  comme les annonces.
- **Archives** : précédent du **planning**, pas celui des annonces. Les
  demandes sont des données durables, utiles d'une édition à l'autre : elles
  partent dans les archives, et une restauration les ramène. Une purge les
  retire des archives **suivantes** (`VACUUM` des copies,
  `sauvegarde._compacter`) ; les archives antérieures les gardent jusqu'à leur
  rotation (`sauvegarde.DUREE_VIE_ARCHIVES_JOURS`, que l'écran de purge cite). Les retirer de toute archive, comme les annonces, les
  aurait perdues à une restauration.
