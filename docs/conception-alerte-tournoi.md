# Note de conception — Alerte « rapportez les exemplaires » avant un tournoi

**Statut :** cadrage validé avec Simon le 2026-08-12, avant tout développement.
Ce document **fait foi** ; `docs/prompt-impl-alerte-tournoi.md` en découpe la
mise en œuvre.

À lire avec `docs/conception-tournois.md` (§3 RGPD, §11 point ouvert sur le jeu
en texte libre) et la fiche **5.2** de `docs/idees-evolutions.md` (annonces
libres, dont ce chantier reprend le patron).

---

## 1. Besoin exprimé

Quand un tournoi du jeu X, d'une durée type de Y minutes, est programmé à
l'heure H, l'application doit **déclencher automatiquement** un message
d'alerte à H − 2×Y, du type :

> « Le tournoi de X va commencer dans 2×Y minutes, nous vous demandons de
> rapporter tous les exemplaires au stand de prêt. »

Le message doit être **paramétrable dans l'espace administrateur**.

## 2. Ce que le code permet réellement (vérifié)

Quatre constats conditionnent toute la conception. Ils ont été relus dans le
dépôt, pas supposés.

1. **Il n'existe aucune tâche de fond dans l'application.** Ni `asyncio`, ni
   `threading`, ni ordonnanceur : `requirements.txt` ne contient que FastAPI,
   uvicorn, Jinja2, dotenv, multipart, qrcode/reportlab, openpyxl et les outils
   de test. Rien ne peut donc « se déclencher » à une heure donnée. Un
   déclenchement se **calcule à la lecture**, à chaque requête — exactement le
   patron de `live.annonce_active()` (auto-masquage d'une annonce expirée
   calculé à la volée, jamais purgé en base).
2. **Personne ne peut être prévenu individuellement.** L'emprunteur n'est
   identifié que par un numéro de pochette, volontairement jamais affiché sur
   `/live` (il désigne un casier contenant une pièce d'identité) ; le module
   tournois ne conserve aucune adresse (`conception-tournois.md` §3). L'alerte
   est donc un **affichage collectif**, pas une notification. C'est une limite
   assumée, pas un manque à combler.
3. **L'application ignore quels exemplaires sont concernés.** `tournois.jeu`
   est un `TEXT` libre dans une base séparée (`data/tournoi.db`), sans clé
   étrangère vers `titres.reference_titre`. Impossible, en l'état, d'écrire
   « 3 exemplaires encore sortis ». C'est le point §11 de la note tournois,
   toujours ouvert, et la fiche 3.2 des idées d'évolutions.
4. **`/live` se rafraîchit toutes les 10 s** (`INTERVALLE_MS` dans
   `live.html`, via `GET /live/data`). Un compte à rebours recalculé côté
   serveur à chaque appel reste donc juste **sans une ligne de JS de plus**.

Le constat 4 règle au passage un piège du besoin tel qu'énoncé : un message
figé disant « dans 90 minutes », affiché pendant 90 minutes, devient faux au
bout de cinq. Le nombre de minutes doit être **recalculé**, pas gravé.

## 3. Décisions

| # | Décision | Motif |
|---|---|---|
| D1 | **Surface unique : l'écran de salle `/live`.** | Le seul canal qui touche des visiteurs en train de jouer. Les pages tournoi ne sont pas consultées le jour J ; l'écran bénévole est écarté pour l'instant (voir §9). |
| D2 | **Calcul à la lecture**, aucune écriture en base, aucun cron. | Constat 1. Idempotent, testable, rien à purger, aucun risque d'alerte fantôme après un redémarrage. |
| D3 | **Délai borné** : `délai = max(mini, min(2 × duree_min, maxi))`. | La règle littérale 2×Y donne 6 h d'avance pour un tournoi de 3 h et 40 min pour un tournoi de 20 min. Le bornage garde l'esprit (proportionnel à la durée) sans les extrêmes. Un `maxi` très élevé restaure la règle littérale. |
| D4 | **`duree_min` absente ou nulle → `délai = mini`.** | La colonne est *nullable* et le champ facultatif au formulaire. Prévenir tard vaut mieux que ne pas prévenir, et mieux que monopoliser le bandeau plusieurs heures pour une durée inconnue. |
| D5 | **Fenêtre d'affichage : `[H − délai, H[`.** | L'alerte s'efface d'elle-même à l'heure de début. Rien à désactiver à la main. |
| D6 | **Un seul tournoi annoncé à la fois : le plus proche.** | Un écran lu de loin porte une consigne, pas deux. Le suivant prend la place dès que le premier a commencé. |
| D7 | **États retenus : phase `a_venir` uniquement** (donc `inscriptions` ; `brouillon` est déjà exclu par `tournois_imminents`, `lance` et `termine` le sont par la phase). | Un tournoi lancé n'a plus besoin qu'on lui rapporte des boîtes. |
| D8 | **L'alerte l'emporte sur l'annonce libre** (`live_annonce`), une seule bande. | Deux bandeaux empilés déforment la zone haute, que `live.html` protège explicitement. L'alerte est datée et s'efface seule : l'annonce du bureau revient sans intervention. Contrepartie obligatoire : l'écran d'administration doit **le dire** (§5). |
| D9 | **Message paramétrable à jetons fermés** : `{jeu}`, `{minutes}`, `{heure}`, `{lieu}`. | Vocabulaire clos, validé à l'enregistrement, dans l'esprit du journal. |
| D10 | **Message vide = alerte éteinte.** Pas d'interrupteur supplémentaire. | Même convention que l'annonce libre (champ vidé ⇒ plus rien sur `/live`). Un bouton de plus dirait la même chose deux fois. |
| D11 | **Rien au journal pour l'affichage** ; seul l'enregistrement du réglage en admin est journalisé. | Un calcul de lecture n'est pas un événement. Patron exact de l'annonce libre. |
| D12 | **Aucun lien avec le catalogue de prêt dans ce chantier.** Le message nomme le jeu, sans compter les exemplaires. | Constat 3. Le couplage est un chantier à part (§9). |

## 4. Réglages

Trois clés dans la table `parametres` de la **base de prêt** — même domicile
que `live_annonce`, lues par `services.lire_parametre` :

| Clé | Contenu | Défaut |
|---|---|---|
| `alerte_tournoi_message` | modèle de message, ≤ 200 caractères | absente ⇒ alerte éteinte |
| `alerte_tournoi_delai_min` | plancher du délai, en minutes | `15` |
| `alerte_tournoi_delai_max` | plafond du délai, en minutes | `90` |

Les défauts des deux délais sont des **constantes du code**, pas des lignes
écrites en base au démarrage : une base existante se comporte comme si elles y
étaient, et le jour où la valeur juste change, un seul endroit bouge.

Le modèle de message **n'a pas de défaut implicite** : tant que le bureau n'a
rien saisi, aucune alerte n'apparaît. Un texte proposé est en revanche affiché
sous le champ, en un clic pour le reprendre :

> `Le tournoi de {jeu} commence dans {minutes} minutes — merci de rapporter tous les exemplaires au stand de prêt.`

Ce choix évite qu'une mise à jour fasse surgir toute seule, sur l'écran de la
salle, une phrase que personne n'a relue.

### Jetons

| Jeton | Valeur | Repli |
|---|---|---|
| `{jeu}` | `tournois.jeu` | l'intitulé `tournois.nom` s'il est vide (tournois créés avant le formulaire à deux champs) |
| `{minutes}` | minutes entières avant `H`, arrondi supérieur | — (toujours ≥ 1 dans la fenêtre) |
| `{heure}` | heure de début, **locale**, `HH:MM` | chaîne vide |
| `{lieu}` | `tournois.emplacement` | chaîne vide |

`date_heure` est stockée en UTC ISO : `{heure}` passe obligatoirement par
`FUSEAU_LOCAL` (`live._heure_locale` fait déjà exactement cela).

## 5. Écrans

**`/live`** — un bandeau distinct de celui de l'annonce libre : pictogramme et
couleur propres (l'ambre `--annonce-fond` reste à l'annonce), afin qu'un coup
d'œil suffise à distinguer « information du bureau » de « consigne minutée ».
Même règle de sécurité que l'annonce : texte injecté par `textContent`, jamais
`innerHTML`.

**`/live/data`** — clé `alerte_tournoi` **absente** du JSON quand il n'y a rien
à annoncer, comme `annonce` aujourd'hui (règle « ne jamais afficher une valeur
absente » ; la page se fie à la présence de la clé).

**`/admin/ecran-salle`** — pas de nouvelle page : le réglage rejoint celui de
l'annonce, dont il est le voisin naturel. Trois champs (message, délai
plancher, délai plafond), la liste des jetons acceptés, et surtout un
**aperçu de ce qui est réellement affiché en salle en ce moment** — le
formulaire calcule déjà `annonce_affichee` de cette façon. Quand une alerte
occupe le bandeau alors qu'une annonce libre est enregistrée, l'écran doit
l'écrire noir sur blanc, avec l'heure de reprise :

> « Une alerte de tournoi occupe le bandeau jusqu'à 14:30 ; votre annonce
> reprendra ensuite. »

C'est la contrepartie de D8 : sans cette phrase, un membre du bureau conclurait
que son annonce a été perdue.

## 6. Où vit quoi

Le pont entre les deux bases se fait **dans la route**, jamais dans un service
— précédent établi par `routes/tournoi.py::_inscription_au_comptoir`, qui lit
un réglage de la base de prêt depuis une route du module tournois.

| Fonction | Domicile | Rôle |
|---|---|---|
| `tournoi_a_annoncer(conn, delai_mini, delai_maxi)` | `app/tournoi/services.py` | ne touche que la base tournois ; renvoie `(tournoi, minutes)` ou `None`. S'appuie sur `tournois_imminents(conn, delai_maxi)` puis filtre par phase et par délai propre à chaque tournoi. |
| `formater_alerte(modele, tournoi, minutes)` | `app/routes/live.py` | substitution des jetons — présentation pure, admise dans la route au même titre que `_heure_locale` et `_minutes_avant`. |
| `alerte_tournoi(conn_pret, conn_tournoi)` | `app/routes/live.py` | lit les trois réglages, appelle les deux précédentes, renvoie le texte ou `None`. Un seul domicile : `/admin/ecran-salle` l'importe pour son aperçu, comme il importe déjà `annonce_active`. |

## 7. Pièges identifiés

1. **`tournois_imminents` ne filtre ni `lance` ni `termine`** (vérifié : sa
   clause `WHERE` n'exclut que `brouillon` et les dates absentes). Le filtre
   par phase est donc à ajouter, pas à supposer.
2. **`duree_min` peut valoir `NULL` ou `0`** : les deux mènent à D4.
3. **`maxi` inférieur à `mini`** si le bureau se trompe : refuser à
   l'enregistrement avec un message clair, **et** rester défensif à la lecture
   (retomber sur `mini`). Ne jamais bloquer prime.
4. **Bornes du délai** : refuser hors de `[0, 1440]` minutes.
5. **Deux tournois du même jeu dans la fenêtre** : D6 tranche, mais le test
   doit exister.
6. **Jeton inconnu** dans le modèle (`{jouer}`, `{Jeu}`) : refus à
   l'enregistrement, avec la liste des jetons acceptés. Une accolade solitaire
   ne doit jamais faire lever d'exception à l'affichage — `str.format` n'est
   pas utilisable tel quel, la substitution se fait jeton par jeton.
7. **Longueur** : borner le message à 200 caractères comme l'annonce, et
   normaliser les espaces (`" ".join(saisie.split())`) — même traitement.
8. **Le bandeau ne doit pas décaler la mise en page** de `/live` quand il
   apparaît : `live.html` s'en protège explicitement pour l'annonce, la même
   précaution vaut ici.

## 8. Tests attendus (~14)

- délai calculé : durée courte (plancher), durée moyenne (2×Y), durée longue
  (plafond), durée `NULL`, durée `0` ;
- fenêtre : juste avant l'ouverture (rien), à l'ouverture (alerte), une minute
  avant `H` (alerte), à `H` (plus rien) ;
- états : `brouillon`, `lance`, `termine` n'alertent jamais ;
- deux tournois dans la fenêtre : seul le plus proche est annoncé ;
- jetons : substitution complète, `{jeu}` vide → repli sur l'intitulé,
  `{heure}` en heure locale et non UTC ;
- réglages : message vide → aucune alerte ; jeton inconnu refusé ;
  `maxi < mini` refusé à l'enregistrement et non bloquant à la lecture ;
- `/live/data` : clé `alerte_tournoi` absente quand il n'y a rien ;
- cohabitation : alerte active **et** annonce libre enregistrée → le JSON porte
  l'alerte, et `/admin/ecran-salle` le signale.

## 9. Hors périmètre

- **Notifier les emprunteurs.** Impossible sans donnée personnelle (constat 2).
  Toute proposition en ce sens devra d'abord passer par un arbitrage RGPD.
- **Compter les exemplaires encore sortis.** Suppose la fiche 3.2 (lien
  tournoi ↔ catalogue). Le modèle de message est conçu pour l'accueillir plus
  tard sous la forme d'un jeton supplémentaire, sans rien réécrire.
- **Alerter le bénévole au comptoir.** Écarté pour l'instant, mais c'est le
  seul canal qui touche quelqu'un capable d'agir : à reconsidérer après le
  premier événement, une fois qu'on saura si l'écran de salle suffit.
- **Rotation de slides sur `/live`** (fiche 5.1) : la vraie réponse structurelle
  à la concurrence entre bandeaux, hors de ce chantier.
