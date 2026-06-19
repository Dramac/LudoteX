# Note de conception — Module « Tournois »

**Statut :** proposition à valider par le CA. Hors périmètre de la v1 « prêt »
(qui part en déploiement d'abord). Ce document fige le périmètre et les
décisions avant tout développement ; il ne code rien.

À lire avec `docs/evolution-prets-longue-duree.md` (même logique de cloisonnement
et de RGPD) et `docs/ameliorations-a-prevoir.md`.

---

## 1. Objectif

Gérer les tournois de l'événement : les créer à l'avance, permettre une
inscription publique en ligne sous pseudo, afficher le suivi (participants,
scores, arbre/classement), et saisir les résultats côté bénévole.

## 2. Intégration & architecture

- **Module intégré** à l'application existante : sous-paquet `app/tournoi/`
  (ses `models.py`, `services.py`, `routes.py`, gabarits dédiés).
- **Base de données SÉPARÉE** : `data/tournoi.db` (SQLite), avec son propre
  accès. Aucune dépendance avec la base de prêt → cycles de vie indépendants,
  réinitialisation séparée, aucune contamination des données.
- **Accès mutualisés** : mêmes **jeton bénévole** et **mot de passe admin**, même
  bandeau/menu et même CSS. Un seul déploiement, un seul domaine.
- **Séparation public / bénévole** (comme le prêt) :
  - *Public* (sans jeton) : voir les tournois, s'inscrire, suivre les scores.
  - *Bénévole* (jeton) : créer/éditer, gérer les participants, saisir les scores,
    lancer, supprimer.
- Le champ « jeu » d'un tournoi est du **texte libre** (la base étant séparée, pas
  de clé étrangère vers le catalogue de prêt ; on pourra proposer une aide à la
  saisie depuis le catalogue plus tard).

## 3. Données personnelles / RGPD (décision validée)

Approche **minimale** retenue :
- À l'inscription, le participant donne un **pseudo** et (optionnellement) un
  **e-mail**.
- Le système enregistre **uniquement** : le pseudo + un **code de désinscription
  aléatoire**. **L'e-mail N'EST PAS stocké.**
- L'e-mail sert seulement, à l'instant de l'inscription, à **envoyer le code**
  (confirmation + lien de désinscription). Le code est **aussi affiché à
  l'écran** (filet de sécurité si le mail n'arrive pas).
- Désinscription = ouvrir le lien `/tournoi/desinscription?code=…` → l'inscription
  est supprimée. Aucun compte, aucun mot de passe.

Conséquences assumées :
- On ne peut **ni renvoyer le code ni envoyer de rappels** (l'adresse n'est pas
  conservée) — cohérent avec l'esprit minimal.
- Nuances (avis non juridique) : l'e-mail est tout de même *traité* le temps de
  l'envoi (le prestataire d'envoi en garde une trace dans ses logs) ; et si un
  participant saisit son vrai nom comme pseudo, la donnée redevient nominative.
  À mentionner dans une courte note d'information à l'inscription.

## 4. Modèle de données (base `data/tournoi.db`)

Esquisse (à affiner) :

**`tournois`**
| champ | rôle |
|---|---|
| `id_tournoi` | PK |
| `nom` / `jeu` | jeu concerné (texte) |
| `date_heure` | début prévu |
| `duree_min` | durée approximative (minutes) |
| `nb_places` | nombre de places |
| `emplacement` | lieu/table |
| `inscription_en_ligne` | 0/1 (avec ou sans inscription en ligne) |
| `etat` | brouillon / inscriptions / lance / termine |
| `mode_scoring` | NULL jusqu'au lancement (voir §6) |
| `bo3` | 0/1 (best of 3 par rencontre) |
| `restriction_nombre` | plafond éventuel (arbre) |
| `date_creation` | horodatage |

**`inscriptions`**
| champ | rôle |
|---|---|
| `id_inscription` | PK |
| `id_tournoi` | FK |
| `pseudo` | nom affiché |
| `code_desinscription` | jeton aléatoire (pas d'e-mail !) |
| `date_inscription` | horodatage |

**`rencontres`** (parties / matchs)
| champ | rôle |
|---|---|
| `id_rencontre` | PK |
| `id_tournoi` | FK |
| `ronde` | n° de ronde (NULL en high score) |
| `participant_a` / `participant_b` | FK inscriptions (B NULL = bye) |
| `score_a` / `score_b` | scores (ou manches gagnées si BO3) |
| `resultat` | gagnant A / gagnant B / nul |

(High score : une table de points par participant, ou agrégation des
`rencontres`. À trancher selon les modes retenus.)

## 5. États d'un tournoi (machine à états)

`brouillon` → `inscriptions` (ouvertes) → `lance` (mode de scoring choisi +
appariements générés) → `termine`. Le **mode de scoring se choisit AU LANCEMENT**,
pas à la création (souplesse demandée).

## 6. Modes de scoring

Par ordre de complexité (et de phasage suggéré) :
- **High score** *(simple)* : classement par points cumulés.
- **Élimination directe** *(simple/moyen)* : arbre ; gestion des « byes » si le
  nombre n'est pas une puissance de 2 ; option BO3.
- **Ronde suisse** *(moyen/complexe)* : appariement par score, sans rejouer les
  mêmes adversaires, gestion d'un bye si nombre impair ; nombre de rondes fixé.
  Algorithme à spécifier soigneusement.
- **Double élimination (looser bracket)** *(complexe — PHASE 2)* : deux arbres
  synchronisés (vainqueurs / repêchage). À reporter.
- Options transverses : **victoire / nul / défaite**, **BO3** par rencontre.

## 7. Écrans

**Public**
- Liste des tournois (à venir / en cours / terminés).
- Page d'un tournoi : infos (jeu, heure, durée, places, emplacement),
  participants, scores/arbre/classement, bouton « S'inscrire » (si places + en
  ligne).
- Formulaire d'inscription : pseudo + e-mail (optionnel) → écran de confirmation
  avec le **code de désinscription** affiché.
- Page de désinscription via `?code=…`.

**Bénévole (jeton)**
- Créer / éditer un tournoi (heure, places, emplacement, options…).
- Ajouter / supprimer manuellement un participant.
- Lancer le tournoi : choix du mode de scoring → génération des appariements.
- Saisir les scores ronde par ronde (par rencontre).
- Supprimer un tournoi : **doubles confirmations**.

## 8. E-mail (envoi du code)

- Service d'envoi externe (**Brevo** ou SMTP Infomaniak, déjà au budget).
- Un seul e-mail par inscription : confirmation + lien de désinscription.
- Config dans `.env` (clé API / SMTP), jamais committée. Code par inscription =
  `secrets.token_urlsafe`. Aucune adresse conservée.

## 9. Sauvegarde externe de la base

- Objectif : récupérer les données en cas de crash, depuis un compte tiers.
- Moyen recommandé : **rclone** (gère **Nextcloud** en WebDAV et **Google
  Drive**) en tâche planifiée (cron sur le VPS), copiant `data/tournoi.db` **et**
  `data/pret-jeux.db`. Hors code applicatif — relève du déploiement.

## 10. Phasage proposé

- **Phase 1** : tournois (CRUD bénévole) + inscription publique (pseudo + code,
  e-mail optionnel) + écran de suivi + **high score** + **élimination directe** +
  **ronde suisse simple** + saisie des scores.
- **Phase 2** : **double élimination**, affinements BO3, e-mails robustes,
  sauvegarde externe automatisée.

## 11. Points à trancher

- [ ] Validation du périmètre et du phasage par le CA.
- [ ] E-mail : confirmé « pseudo + code stockés, e-mail non conservé ». OK ?
- [ ] « Jeu » en texte libre, ou aide à la saisie depuis le catalogue ?
- [ ] Gestion des tournois ouverte à **tous les bénévoles** (jeton commun) ou à un
      **rôle dédié** ?
- [ ] Service e-mail retenu (Brevo / Infomaniak).
- [ ] Cible de sauvegarde externe (Nextcloud / Google Drive / autre).
- [ ] Modes de scoring réellement nécessaires en phase 1 (tout ? un sous-ensemble ?).
