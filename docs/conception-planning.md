# Note de conception — Module « Planning bénévole »

**Statut :** proposition à valider par le bureau / CA. Hors périmètre de la v1
« prêt ». Ce document fige le périmètre et les décisions avant tout
développement ; il ne code rien.

À lire avec `docs/conception-tournois.md` (même logique de cloisonnement, de
module intégré et de RGPD) et `docs/ameliorations-a-prevoir.md`.

---

## 1. Objectif

Remplacer le tableur Excel préparé à la main par le bureau. Trois temps :

1. **Collecte des souhaits** : chaque bénévole déclare ses disponibilités
   horaires et ses préférences d'affectation par poste.
2. **Préremplissage automatique** : génération d'un **brouillon dégrossi** du
   planning (et non d'un optimum), à partir des souhaits et des besoins par
   poste.
3. **Validation / modification par un admin**, puis **publication** en lecture
   seule sur l'interface bénévole, avec un « mon planning » par personne.

Parti pris assumé (cf. §6) : l'auto-remplissage vise une **ébauche honnête qui
montre clairement les trous**, retouchée à la main par l'admin. Un vrai moteur
d'optimisation (équité fine, continuité, expérience) est renvoyé en phase 2.

## 2. Ce que dit le tableur actuel

Le fichier du dernier événement mélange deux choses dans une même grille :

- **Des postes à pourvoir par créneau**, avec un **besoin** (nombre de cases)
  propre à chaque poste : Accueil (≈2), Ludothèque (≈3), Inscription tournois
  (≈1), Bar (≈3, dont une colonne souvent grisée), Explication jeux (≈3),
  Partage un jeu (≈1). Une **case grisée = besoin nul** sur ce créneau.
- **Des tâches ponctuelles sans découpage horaire** : Installation samedi matin,
  Rangement samedi soir, Rangement dimanche soir — de simples listes de noms.

Trame temporelle sur deux jours, **créneaux de 2 h** mais différents d'un jour à
l'autre : samedi 6 créneaux (14h30 → 2h30), dimanche 4 créneaux (10h → 18h).

Signaux utiles repérés : des bénévoles reviennent sur plusieurs créneaux
consécutifs (continuité souhaitable), et certains postes (Explication jeux, Bar)
demandent plutôt des personnes expérimentées. Ces deux points sont notés comme
**affinements de phase 2** : le brouillon dégrossi ne les garantit pas.

## 3. Intégration & architecture

- **Module intégré** à l'application existante : sous-paquet `app/planning/`
  (ses `models.py`, `db.py`, `services.py`, `routes.py`, gabarits dédiés), sur
  le modèle de `app/tournoi/`.
- **Base de données SÉPARÉE** : `data/planning.db` (SQLite), avec son propre
  accès. Aucune dépendance avec la base de prêt ni celle des tournois → cycles
  de vie indépendants, purge séparée, aucune contamination.
- **Accès mutualisés** : mêmes **jeton bénévole** et **mot de passe admin**, même
  bandeau/menu, même CSS. Un seul déploiement, un seul domaine.
- **Trois niveaux d'accès** :
  - *Public / lien de collecte* : un bénévole remplit son formulaire de souhaits
    (lien partagé ; voir §7 pour la protection).
  - *Bénévole (jeton)* : consulter le planning **publié** et son « mon planning ».
  - *Admin (mot de passe)* : définir la trame (postes, créneaux, besoins),
    lancer le préremplissage, éditer la grille, publier, exporter.

## 4. Données personnelles / RGPD

**Rupture explicite avec la brique de prêt** (dont la fierté est « zéro donnée
personnelle ») : un planning nominatif stocke forcément des **noms**, des
**disponibilités** et un **contact** pour recontacter. Ce n'est pas bloquant —
le bureau le fait déjà dans Excel — mais cela doit être décidé en conscience.

Approche minimale retenue :

- On stocke : **nom ou pseudo**, un **moyen de contact** (e-mail ou téléphone,
  pour recontacter), les **disponibilités** et **préférences** déclarées, et les
  **affectations**.
- **Base séparée** `data/planning.db`, **finalité unique** (organiser l'événement),
  **purge après l'événement** (bouton admin de remise à zéro, comme la clôture de
  fin d'événement côté prêt).
- **Courte note d'information** sur le formulaire de collecte (qui voit les
  données, combien de temps, comment se faire retirer).
- Ne pas mélanger avec la base de prêt : aucune jointure, aucun export croisé.

> Point à trancher (§11) : conserve-t-on le contact, ou bien on se contente du
> nom et l'admin gère les relances par ses propres moyens ?

## 5. Modèle de données (base `data/planning.db`)

Esquisse (à affiner) :

**`evenements`** — un planning par édition de l'événement
| champ | rôle |
|---|---|
| `id_evenement` | PK |
| `nom` | ex. « Festival 2026 » |
| `etat` | `collecte` / `brouillon` / `publie` |
| `date_creation` | horodatage |

**`creneaux`** — la trame horaire (définie par l'admin)
| champ | rôle |
|---|---|
| `id_creneau` | PK |
| `id_evenement` | FK |
| `libelle_jour` | ex. « Samedi », « Dimanche » |
| `debut` / `fin` | horaires (peuvent différer d'un jour à l'autre) |
| `type` | `poste` (créneau normal) ou `tache` (installation/rangement, sans poste) |

**`postes`** — les colonnes du tableau
| champ | rôle |
|---|---|
| `id_poste` | PK |
| `id_evenement` | FK |
| `nom` | Accueil, Ludothèque, Bar… |
| `demande_experience` | 0/1 (info, exploitée en phase 2) |

**`besoins`** — combien de personnes par (créneau × poste)
| champ | rôle |
|---|---|
| `id_creneau` / `id_poste` | FK |
| `nb_requis` | 0 = case grisée (pas de besoin) |

**`benevoles`**
| champ | rôle |
|---|---|
| `id_benevole` | PK |
| `id_evenement` | FK |
| `nom` | nom ou pseudo affiché |
| `contact` | e-mail/téléphone (voir §4) |
| `max_heures` | plafond d'heures à ne pas dépasser (NULL = pas de plafond) |
| `note` | mot libre |
| `code_modif` | jeton aléatoire pour rouvrir/modifier sa réponse |

**`disponibilites`** — par créneau, le bénévole est dispo ou non
| champ | rôle |
|---|---|
| `id_benevole` / `id_creneau` | FK |
| `disponible` | 0/1 |

**`preferences`** — par poste, l'envie du bénévole
| champ | rôle |
|---|---|
| `id_benevole` / `id_poste` | FK |
| `niveau` | `prefere` / `ok` / `si_vraiment` / `surtout_pas` |

**`affectations`** — le planning lui-même
| champ | rôle |
|---|---|
| `id_creneau` / `id_poste` | FK |
| `id_benevole` | FK |
| `verrouille` | 0/1 (l'admin fige une case ; le re-préremplissage la respecte) |
| `origine` | `auto` / `manuel` (traçabilité) |

## 6. Préremplissage « brouillon dégrossi » (cœur du module)

Problème d'affectation sous contraintes, résolu par un **algorithme glouton**
simple et lisible (esprit de l'appariement suisse côté tournois), **sans solveur**.

Principe pressenti :

1. Ne considérer que les couples (créneau × poste) où `nb_requis > 0`, ordonnés
   du plus tendu au moins tendu (le moins de candidats disponibles d'abord).
2. Pour chaque case, piocher parmi les bénévoles **disponibles** sur ce créneau,
   par ordre de préférence (`prefere` → `ok` → `si_vraiment`), en **excluant
   `surtout_pas`** et ceux qui atteindraient leur **plafond d'heures**.
3. Départager à envie égale en favorisant celui qui a le **moins d'heures déjà
   affectées** (équité de base) et qui n'est pas déjà placé sur ce créneau.
4. Laisser **vide** toute case qu'on ne peut pas remplir proprement, plutôt que
   de forcer.

Contraintes **dures** respectées : disponibilité, `surtout_pas`, plafond
d'heures, pas deux postes en même temps. Contraintes **molles** reportées en
phase 2 : continuité sur créneaux consécutifs, expérience requise, équité fine.

L'admin obtient une grille partiellement remplie + une **liste claire des trous
et sur-affectations** à corriger.

## 7. Écrans

**Collecte (lien partagé, par bénévole)**
- Formulaire : nom/pseudo, contact, **plafond d'heures**, note libre.
- Grille de **disponibilités** : cocher les créneaux où l'on peut venir
  (ou saisir des plages horaires qui se rabattent sur les créneaux).
- Pour chaque poste, choisir **préféré / ok / si il faut vraiment / surtout pas**.
- À l'envoi : récap + **code de modification** affiché, pour revenir éditer sa
  réponse tant que la collecte est ouverte.

**Admin (mot de passe)**
- Définir la trame : postes, créneaux par jour, **besoins** par case (0 = grisé),
  tâches ponctuelles. Possibilité de **dupliquer la trame** de l'édition
  précédente (gain de temps annuel).
- Voir qui a répondu, le **taux de couverture prévisible** par créneau.
- **Lancer le préremplissage** → brouillon.
- **Éditer la grille** facon Excel : ajouter/retirer un nom, **verrouiller** une
  case, « régénérer le reste », visualiser trous et conflits.
- **Publier** (passe l'état à `publie`).
- **Purger** les données après l'événement.

**Bénévole (jeton) — après publication**
- Vue d'ensemble du planning publié (grille en lecture seule, proche du tableur).
- **« Mon planning »** : ses propres créneaux/postes, en évidence.

## 8. Exports PDF et Excel

Réutiliser le socle `app/exports.py` (openpyxl + reportlab) déjà en place pour
les stats.

- **Export Excel** (`.xlsx`) : la grille complète, **une feuille par jour**,
  lignes = créneaux, colonnes = postes, cases grisées = besoin nul — pour rester
  proche du format de travail actuel du bureau, et permettre une retouche hors
  ligne si besoin.
- **Export PDF** : version **imprimable / affichable** du planning publié (titre,
  date, logo de l'asso, une page par jour). Pensé pour l'impression A4 et
  l'affichage le jour J.
- Les deux respectent l'état courant (brouillon ou publié) et sont déclenchés
  depuis l'écran admin.
- Une variante utile : un **PDF « par bénévole »** (un mini-planning individuel),
  à confirmer selon le besoin.

## 9. États (machine à états)

`collecte` (formulaires ouverts) → `brouillon` (préremplissage généré, édition
admin) → `publie` (visible des bénévoles). Retours en arrière possibles côté
admin tant que ce n'est pas figé.

## 10. Phasage proposé

- **Phase 1** : trame admin (postes/créneaux/besoins + tâches ponctuelles) ;
  formulaire de collecte (dispos + préférences 4 niveaux + plafond d'heures) ;
  préremplissage **dégrossi** ; édition admin avec verrouillage ; publication +
  « mon planning » ; **exports PDF & Excel**.
- **Phase 2** : continuité sur créneaux consécutifs, prise en compte de
  l'expérience requise, équité fine, relances/notifications, PDF individuel
  automatisé.

## 11. Points à trancher

- [ ] Validation du périmètre et du phasage (brouillon dégrossi d'abord).
- [ ] **Contact conservé** (e-mail/téléphone) ou **nom seul** ? (RGPD §4)
- [ ] **Saisie des dispos** : cases à cocher par créneau (simple) **ou** plages
      horaires libres rabattues sur les créneaux (plus souple, plus de code) ?
- [ ] **Protection du lien de collecte** : ouvert à tous (lien long) ou derrière
      le jeton bénévole ?
- [ ] Niveaux de préférence : garde-t-on bien **préféré / ok / si il faut
      vraiment / surtout pas** ?
- [ ] Faut-il un **PDF par bénévole** dès la phase 1 ?
- [ ] La trame change-t-elle vraiment chaque année, ou peut-on partir d'un
      gabarit fixe dupliqué ?
