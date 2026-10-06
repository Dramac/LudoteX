# LudoteX — spécification

**Objet :** ce que fait LudoteX et pourquoi il est fait ainsi — règles métier,
invariants, périmètre. Le *comment* (architecture, conventions, recettes) est
dans `docs/guide-developpeur.md` ; le mode d'emploi, dans le wiki ; l'histoire
livrée, dans `CHANGELOG.md`.

**Ce qui fait foi.** Ce document fait foi sur les **intentions et les règles
métier**. Le code fait foi sur le comportement effectif. Une divergence entre
les deux est un **écart à signaler**, jamais une préséance à appliquer en
silence.

> **Numérotation figée.** Le code et d'autres documents citent ce fichier par
> numéro de section (« §3.2 », « §6 »…). Les numéros 1 à 12 sont donc
> conservés d'une version à l'autre ; une section neuve prend un numéro
> libre (sous-section ou fin de document) plutôt que d'en décaler une autre.
> `tests/test_renvois_specification.py` vérifie que chaque renvoi tombe sur
> une section qui existe et qui porte le sujet annoncé.

---

## 1. Contexte et objectif

LudoteX est né dans une association qui prête un parc d'environ 700 jeux de
société pendant son événement annuel, dans une salle des fêtes. Le prêt s'y
faisait contre dépôt d'une pièce d'identité (PI), consignée sur une feuille
papier unique au comptoir : un système fiable mais centralisé, un seul point
d'écriture et de recherche, donc un goulet d'étranglement aux heures de pointe.

**Objectif :** permettre à plusieurs bénévoles, chacun muni d'un smartphone,
d'enregistrer prêts et retours en parallèle, en scannant un QR code collé sur
chaque boîte, sur une base partagée.

Le logiciel est **réutilisable par toute association** qui prête des jeux
selon le même principe : le nom, le logo, la couleur et les textes de
présentation sont des réglages de l'instance (§10), pas des constantes du code.

Les utilisateurs sont des **bénévoles non techniciens sur smartphone**, en
conditions dégradées (wifi de salle lent, petit écran, geste répété des
centaines de fois dans la journée), et un **bureau** sans compétence
informatique. Le public, lui, consulte sans compte.

Autour du prêt, LudoteX outille aussi le reste de l'événement — catalogue
public, statistiques, tournois, programme, planning des bénévoles, écran de
salle, rangement, carnet de maintenance (§5.4).

---

## 2. Principes de conception

Ces principes guident toutes les décisions du document.

1. **Aucune donnée personnelle dans le modèle de données du prêt.**
   L'emprunteur n'est jamais identifié nominativement. Le seul lien prêt ↔
   personne est un **numéro de pochette** physique où est glissée la PI. Les
   champs de texte libre qui pourraient malgré tout recevoir un nom sont
   recensés dans `wiki/Rgpd.md` (voir §9).
2. **Ne jamais bloquer le bénévole.** Toute incohérence est signalée par un
   message clair, accompagné d'une action de rattrapage en un tap, jamais
   d'une erreur brute. Cette règle prime sur les autres.
3. **Simplicité maximale de l'interface de prêt.** Le bénévole confirme une
   action présélectionnée ; on ne lui demande un choix explicite que dans les
   cas réellement ambigus.
4. **Séparer la lecture de l'écriture.** La consultation est publique ; les
   actions de prêt et de retour sont réservées aux bénévoles (§8).
5. **Souplesse du catalogue.** Le CSV peut évoluer librement, à l'exception
   de deux clés non négociables (§3.1).
6. **Pas de sur-ingénierie.** SQLite plutôt qu'un serveur de base, pages
   rendues côté serveur plutôt qu'une application JavaScript, aucun compte
   individuel (§10). Un module neuf ne se fait que s'il ne compromet aucun des
   principes ci-dessus.

---

## 3. Modèle de données

### 3.1 Les deux clés non négociables

Quelles que soient les évolutions du CSV, deux champs existent et restent
stables :

- **`id_exemplaire`** — identifiant unique et stable d'une **boîte
  physique**. C'est ce que le QR code encode. Il ne change jamais une fois le
  QR imprimé et collé, même si l'on modifie l'éditeur, la catégorie, etc. Il
  est stocké en **texte**, jamais réinterprété comme un entier, pour préserver
  un éventuel zéro de tête (`00472`). Une boîte ajoutée depuis
  l'administration reçoit un identifiant généré.
- **`reference_titre`** — clé de regroupement commune à tous les exemplaires
  d'un **même jeu** : trois boîtes de Catan partagent la référence `CATAN`.
  Elle est dérivée du nom du jeu, par la même règle à l'import et dans
  l'administration. Elle sert à agréger les statistiques par titre (§7).

À l'import, seuls le code d'exemplaire et le nom du jeu sont exigés ; les
autres colonnes reconnues sont facultatives, et leurs intitulés tolérés sont
listés dans `scripts/import_csv.py`. Les colonnes d'état d'un CSV (« prêt en
cours »…) sont ignorées : l'état se déduit des prêts (§3.3).

### 3.2 Tables

**`titres`** — le catalogue, niveau « référence ».

| Champ | Description |
|---|---|
| `reference_titre` | clé primaire (ex. `CATAN`) |
| `nom` | nom affiché du jeu |
| `categorie` | catégorie pour le filtrage public |
| *(champs facultatifs)* | joueurs, durée, âge, éditeur, auteur, descriptif… — tous nullables |

**`exemplaires`** — les boîtes physiques, niveau « unité prêtable ».

| Champ | Description |
|---|---|
| `id_exemplaire` | clé primaire, encodée dans le QR |
| `reference_titre` | clé étrangère → `titres` |
| *(emplacements)* | où ranger la boîte, deux contextes (`docs/conception-rangement.md`) |

**`prets`** — l'historique complet de tous les prêts (jamais purgé).

| Champ | Description |
|---|---|
| `id_pret` | clé primaire |
| `id_exemplaire` | clé étrangère → `exemplaires` |
| `numero_pochette` | numéro attribué pour ce prêt ; **effacé une fois le prêt clos** (voir ci-dessous) |
| `date_sortie` | horodatage de sortie |
| `date_retour` | horodatage de retour ; **vide tant que le jeu est sorti** |
| `motif` | `pret` (au public), `tournoi` (sortie pour un tournoi, sans pochette), `erreur` (rendu en moins d'une minute), `oubli` (retour jamais scanné, clos plus tard) — voir §7 |

> **Durée de vie du numéro de pochette — décision du 2026-07-18.**
> Le numéro de pochette est une donnée **sensible** : il désigne le casier où
> se trouve une pièce d'identité. Il n'est donc jamais montré à un visiteur
> (seuls bénévoles et administrateurs le voient), et il n'a d'utilité que
> **pendant** le prêt. Une fois `date_retour` posée, le numéro ne renseigne
> plus sur rien d'utile mais resterait exposé dans les exports et les
> sauvegardes : il est **effacé à la clôture du prêt** (retour, re-prêt,
> transfert, clôture de fin d'événement).
>
> La règle « historique jamais purgé » tient toujours : la **ligne** de prêt
> reste, avec ses dates et son motif — seul le numéro est effacé. Aucune
> statistique ne lit ce champ. Les prêts **en cours** gardent leur numéro, donc
> une sauvegarde prise pendant l'événement permet la reprise après incident.
> Mise en œuvre : fiche D5 de `docs/audit-ux-2026-07-18.md`.

**`pochettes`** — l'occupation du moment (quels numéros sont utilisés).

| Champ | Description |
|---|---|
| `numero_pochette` | numéro (recyclé, voir §6) |
| `occupe` | libre / occupé |

La base de prêt porte aussi les réglages (`parametres`), le registre des
appareils autorisés à écrire (`appareils`), les emplacements de rangement et
le carnet de maintenance (`signalements`, `categories_signalement`). Détail
des colonnes : `app/models.py`, commenté.

### 3.3 Règles dérivées

- Un exemplaire est **disponible** s'il n'a aucun prêt avec `date_retour`
  vide ; il est **sorti** sinon. Cet état est **déduit, jamais stocké**.
- Le numéro de pochette n'est qu'une occupation du moment : libéré au retour,
  réutilisable, et effacé de la ligne de prêt à la clôture (§3.2).
- L'historique des prêts n'est jamais supprimé : il alimente les
  statistiques.

### 3.4 Trois bases indépendantes — invariant

LudoteX tient **trois bases SQLite séparées**, chacune avec son schéma, ses
migrations et son initialisation :

| Base | Contenu | Données personnelles |
|---|---|---|
| Prêt | catalogue, exemplaires, prêts, pochettes, réglages, rangement, carnet de maintenance | aucune dans le modèle (§9.1) |
| Tournois | tournois, inscriptions, rencontres, programme du week-end | pseudo des inscrits |
| Planning | événements, postes, créneaux, bénévoles, disponibilités, affectations | assumées (§9.2) |

**L'invariant : un service ne lit jamais une autre base que la sienne.**
Quand un module a besoin d'un réglage qui vit ailleurs (le nom de l'événement
dans un en-tête de calendrier, par exemple), c'est la route qui le lit et le
transmet. Ce cloisonnement est ce qui garantit l'absence de donnée personnelle
dans la base de prêt, et ce qui permet de purger le planning sans toucher au
reste. C'est aussi l'invariant le plus facile à rompre par commodité.

Le **journal d'activité** n'est pas une base : c'est un fichier à rotation, à
vocabulaire fermé (`docs/conception-journal.md`, et son §8 pour ce qui n'y
entre jamais).

---

## 4. QR codes

- **Un QR unique par exemplaire physique** (pas par titre). Deux boîtes du
  même jeu ont deux QR distincts.
- Le QR encode une **URL** de la forme `https://pret.example.fr/jeu/00472`.
  L'adresse du site est donc figée par les étiquettes imprimées : elle relève
  de l'infrastructure (§10), pas d'un réglage d'interface.
- **Un seul format, deux modes de lecture :**
  - le **scanner embarqué** dans l'application — mode principal au comptoir :
    le bénévole enchaîne les scans sans quitter la page ;
  - l'**appareil photo natif** du téléphone — filet de sécurité : le QR mène
    à la fiche publique, qui offre au bénévole un accès direct à l'écran de
    prêt.
- **Saisie de secours** : si l'étiquette est illisible ou la caméra
  capricieuse, le code de la boîte se tape sous le scanner. La saisie tolère
  la casse et les zéros de tête, et propose les codes proches plutôt que de
  répondre « introuvable ». L'étiquette imprimée peut afficher ce code en
  clair.
- Le QR ne comporte **aucun secret** : il donne le même accès que le catalogue
  public (lecture seule).

---

## 5. Écrans et interface

### 5.1 Écran de prêt / retour (bénévole)

Déclenché par un scan. Le système connaît l'état de l'exemplaire et
**présélectionne l'action la plus probable** :

**Cas — exemplaire DISPONIBLE** (pas d'ambiguïté)
→ Action principale : **Prêter**. Le système attribue le plus petit numéro de
pochette libre et l'affiche en grand. Un seul tap. Une action secondaire sort
la boîte **pour un tournoi** : sans pièce d'identité ni pochette, hors
statistiques.

**Cas — exemplaire SORTI** (ambiguïté possible, choix explicite requis)
- **Rendre** *(action principale)* — clôt le prêt en cours et libère la
  pochette, dont le numéro est rappelé sur le bouton. Un retour en moins d'une
  minute est requalifié en **erreur de prêt** (mauvaise boîte scannée,
  visiteur qui se ravise) et sort des statistiques.
- **Transférer** (rendre et prêter un nouveau jeu) — le visiteur rapporte une boîte et repart avec une autre :
  la pièce d'identité reste dans sa pochette, c'est le jeu rattaché au numéro
  qui change (`docs/conception-transfert-pochette.md`).
- **Le re-prêter** *(cas d'oubli de scan)* — l'ancien prêt est clos
  (motif `oubli`, §7), puis un nouveau prêt s'ouvre, avec le plus petit numéro
  libre (§6) — souvent celui qu'on vient de libérer.

Quand un geste rencontre un **retour jamais scanné** — la pochette libérée
peut encore contenir une pièce d'identité —, l'écran demande de vérifier que
la pochette est vide et offre, si elle ne l'est pas, de **prévenir le
bureau** : un signalement rédigé par l'application, sans champ à remplir (`docs/conception-transfert-pochette.md`
§6 bis et §6 ter).

C'est le seul écran où un choix explicite est demandé, parce que la réalité
physique peut diverger de la base. Un conflit entre deux bénévoles sur la même
boîte donne un message, jamais une erreur (§2).

### 5.2 Catalogue public (consultation)

- Accès en **lecture seule**, sans donnée personnelle, sans bouton d'action.
- Recherche, filtres (catégorie, nombre de joueurs, âge…) et fiche par
  exemplaire.
- Disponibilité affichée au niveau du titre (combien d'exemplaires
  disponibles).
- Consultable **à l'année**, y compris en amont de l'événement.

### 5.3 Page de statistiques

Voir §7.

### 5.4 Modules autour du prêt

Chacun a sa note de conception quand elle existe ; on ne la recopie pas ici.

- **Tournois** — quatre modes de scoring (high score, ronde suisse, round
  robin, élimination directe), tournois par équipes, inscription publique par
  pseudo avec un code de désinscription (**l'e-mail n'est jamais demandé ni
  stocké**), inscription réservable aux bénévoles, export calendrier.
  `docs/conception-tournois.md`.
- **Programme du week-end** — animations et temps forts hors tournoi, grille
  publique, reprise sur l'écran de salle et l'accueil.
  `docs/conception-programme.md`.
- **Planning bénévoles** — questionnaire de disponibilités et de préférences,
  préremplissage automatique, ajustement à la main par le bureau, publication
  aux bénévoles. Base séparée (§3.4), données personnelles assumées (§9.2).
  `docs/conception-planning.md`.
- **Écran de salle** — tableau de bord projeté (chiffres du prêt, derniers
  mouvements, tournois, programme), annonces du bureau limitées dans le temps, alerte « rapportez
  les exemplaires » avant chaque tournoi. Aucun numéro de pochette n'y
  apparaît. `docs/conception-alerte-tournoi.md`.
- **Rangement** — où ranger chaque boîte, selon deux contextes
  interchangeables (l'événement, le local de l'association), affectation par
  scan ou en lot. `docs/conception-rangement.md`.
- **Carnet de maintenance** — signalements d'état des boîtes (pièce
  manquante, livret abîmé), rappelés au prêt suivant, consultables par les
  bénévoles et traités par le bureau ; jamais public.
  `docs/conception-signalements.md`.
- **Journal d'activité** — qui a fait quoi, à quel appareil près, jamais à
  quelle personne. `docs/conception-journal.md`.
- **Administration** — mot de passe distinct du jeton bénévole : catalogue et
  étiquettes, jeton et appareils, sauvegarde et restauration des trois bases,
  supervision, identité, visibilité des modules, clôture de fin d'événement.
- **Mode formation** — une seconde instance du même code, sur ses propres
  bases, pour s'entraîner sur de vraies boîtes sans rien inscrire pour de bon.
  `docs/mode-formation.md`.

Chaque module optionnel a quatre états de visibilité : *tous*, *bénévoles*,
*discret* (adresse ouverte, lien masqué), *désactivé*. Le carnet de
maintenance ne peut pas être rendu public. La visibilité règle l'affichage ;
la protection d'un écran d'écriture, elle, ne dépend jamais de ce réglage (§8).

---

## 6. Gestion des numéros de pochette

Le numéro identifie une **pochette numérotée** (ou un ticket agrafé à la PI),
pas un emplacement de meuble en nombre fixe. Le principe est donc **sans
plafond** : on ne refuse jamais un prêt.

- Numérotation **à partir de 1** ; à chaque nouveau prêt, attribution du
  **plus petit numéro libre**.
- La PI est glissée dans la pochette portant ce numéro ; les pochettes sont
  rangées dans l'ordre pour une récupération rapide au retour.
- Un numéro libéré au retour est immédiatement **recyclé**.
- **Aucune limite logicielle** : en cas d'affluence record, le système
  continue d'attribuer des numéros croissants. Le stock physique de pochettes
  est l'affaire de l'association.
- Le numéro doit rester **physiquement attaché à la PI**. Une PI rangée « en
  vrac » casserait le lien numéro → casier et réintroduirait la recherche
  manuelle au retour — précisément le goulet à supprimer.
- Le numéro identifie *la PI déposée*, pas la personne : **un seul jeu par PI
  et par numéro**.
- **Une seule exception, assumée : le transfert** (§5.1) réutilise le numéro
  du prêt qu'il clôt, même si un plus petit est libre — la pochette n'a jamais
  été vidée (`docs/conception-transfert-pochette.md` §3).
- Deux bénévoles qui prêtent au même instant ne peuvent pas recevoir le même
  numéro : l'attribution se fait dans une transaction exclusive
  (`docs/protocole-stress-test.md`).

---

## 7. Statistiques

Les statistiques s'appuient sur l'historique complet de la table `prets` et
ne contiennent aucune donnée personnelle.

Elles comptent les **prêts au public** : motif `pret`, et motif `oubli` — un
prêt dont le retour n'a pas été scanné, clos plus tard par un re-prêt ou un
transfert. Ce dernier est un vrai prêt, mais sa durée est inconnue : il est
exclu de la durée moyenne et affiché à part (« retours non scannés »). Les
sorties tournoi et les erreurs de prêt sont hors statistiques.

Indicateurs :

- **Nombre total de prêts**, durée moyenne, filtrables par période.
- **Palmarès des jeux les plus et les moins prêtés**, agrégé **par titre**
  (`reference_titre`), en deux vues : total brut, ou rapporté au nombre
  d'exemplaires (pour ne pas avantager mécaniquement les titres présents en
  plusieurs boîtes).
- Le palmarès des moins prêtés inclut les jeux **jamais sortis** : on part de
  **tous les titres du catalogue** et on y rattache les prêts (« catalogue
  d'abord »), faute de quoi les jeux à zéro prêt seraient invisibles.
- **Prêts par heure** (histogramme), liste détaillée, jeux actuellement
  sortis — le numéro de pochette n'y est montré qu'aux bénévoles.
- Exports Excel et PDF.

---

## 8. Contrôle d'accès et sécurité

L'association fait confiance à ses bénévoles : **pas de comptes
individuels**. La distinction nécessaire n'est pas entre bénévoles, mais
entre **public (lecture)**, **bénévoles (écriture)** et **bureau
(administration)**.

- **Jeton bénévole** — un secret aléatoire long, partagé, distribué par un
  lien d'activation. L'appareil le mémorise dans un cookie et peut ensuite
  écrire. Un tel jeton ne se devine pas par force brute, contrairement à un
  mot de passe court.
- **Échéance prolongeable** — le jeton porte une date de fin. Le bureau est
  averti à l'approche, et peut la repousser **sans changer le lien**. Passée
  l'échéance, l'accès en écriture se ferme pour tous les appareils (§12).
- **Rotation** — réinitialiser le jeton invalide d'un coup tous les appareils
  activés ; c'est le seul geste de révocation.
- **Séparation lecture / écriture** — les fiches (`/jeu/...`) sont publiques
  et sans action ; les opérations de prêt passent par des adresses
  distinctes, chacune protégée par le jeton dans le code, indépendamment de la
  visibilité des modules.
- **Administration** — un mot de passe distinct, haché en base, avec une
  session à durée limitée.
- **Limitation de débit** par adresse IP sur l'activation du jeton et sur la
  connexion d'administration.
- **Registre des appareils** — chaque appareil autorisé reçoit un
  identifiant tiré au hasard, qui dit « c'est le même téléphone », jamais
  « c'est le téléphone de quelqu'un ». Le bureau peut lui donner un libellé.

---

## 9. RGPD

### 9.1 Application de prêt — aucune donnée personnelle dans le modèle de données

Par conception, le modèle de données du prêt **ne prévoit aucune donnée
personnelle**. L'emprunteur est représenté par un numéro de pochette ; sa
pièce d'identité reste physiquement au comptoir et lui est rendue au retour du
jeu. Quelques champs de texte libre (détail d'un signalement, libellé d'un
appareil, annonce de l'écran de salle) pourraient recevoir un nom : ils portent
une consigne, et leur liste, avec ce que chacun garde, vit dans
`wiki/Rgpd.md`. Cette propriété doit être préservée dans les évolutions
futures (§11) : **toute proposition qui ferait entrer une donnée personnelle
se signale comme telle avant d'être écrite**. Ce qu'elle implique pour les
obligations d'une association relève de son appréciation ; ce document ne
tranche pas.

### 9.2 Modules qui traitent des données personnelles

Deux modules s'écartent assumément du principe, chacun cloisonné dans une
autre base que celle du prêt (§3.4) :

- **Planning bénévoles** — noms, contact facultatif, disponibilités et
  affectations des bénévoles. Finalité unique (organiser l'événement), base
  séparée, purge par le bureau. Une suppression promise s'entend **sur les
  octets** de la base et de ses archives, pas seulement sur le résultat d'une
  requête.
- **Tournois** — le pseudo (ou nom d'équipe) des inscrits ; jamais d'e-mail.

### 9.3 Site vitrine et newsletter — hors de ce dépôt

Une association peut adjoindre à LudoteX un site vitrine avec newsletter. Ce
n'est **pas** une brique de ce dépôt. Une newsletter collecte des adresses
e-mail et impose les obligations habituelles (consentement explicite, lien de
désinscription, politique de confidentialité, finalité unique) ; garder les
deux briques séparées est ce qui préserve l'anonymat de l'application de prêt.

---

## 10. Architecture technique et hébergement

Le *comment* est dans `docs/guide-developpeur.md` et `docs/deploiement.md` ;
voici les choix et leur raison.

- **Application web**, pas d'application native : les bénévoles ouvrent une
  adresse. Le site s'ajoute à l'écran d'accueil du téléphone avec sa propre
  icône, sans store. **Ce n'est pas une PWA** : ni manifeste ni service
  worker, donc **aucun fonctionnement hors ligne** — chaque scan lit et écrit
  l'état courant sur le serveur.
- **Python + FastAPI**, pages **rendues côté serveur** (Jinja2), CSS sans
  framework ni étape de compilation. Le JavaScript reste marginal (scanner
  caméra, rafraîchissement de l'écran de salle) et ne dépend d'aucun CDN.
- **SQLite**, largement suffisant pour la charge réelle (quelques écritures
  par minute, une poignée de bénévoles). Les écritures concurrentes sur les
  pochettes sont sérialisées par transaction (§6).
- **Pas de temps réel complexe** (pas de websocket) : chaque scan lit l'état
  courant ; l'écran de salle interroge le serveur à intervalle régulier.
- **Un seul processus** applicatif : les sessions d'administration vivent en
  mémoire.
- **Hébergement : un VPS** Debian/Ubuntu, derrière nginx, servi par systemd,
  en HTTPS (Let's Encrypt), avec sauvegarde automatique deux fois par jour.
  Une instance de formation peut tourner à côté (§5.4). Un lanceur local
  permet aussi de la faire tourner sur un simple ordinateur, sans serveur
  distant.
- **Ce qui se règle où.** L'identité (nom de l'association, présentation,
  contact, logo, couleur) est une donnée **éditoriale**, en base, modifiable
  depuis l'administration sans redéploiement. Ce qui engage l'infrastructure
  ou la sécurité (adresse du site figée par les QR, chemins, secrets, mode
  formation) reste dans le fichier d'environnement de l'instance
  (`docs/personnaliser.md`).

---

## 11. Hors périmètre — ce que LudoteX ne fait pas

*Relu le 2026-10-06.* À ne pas développer sans décision, et à ne pas
compromettre :

- **Comptes individuels**, pour les bénévoles comme pour le public. Ils
  rouvriraient la question des données personnelles (§9.1) pour un gain que le
  jeton partagé couvre déjà.
- **Favoris du public** — non réalisés. S'ils l'étaient, d'abord **en local
  sur l'appareil du visiteur**, sans rien côté serveur.
- **Fonctionnement hors ligne** au comptoir (§10).
- **Envoi d'e-mails** par l'application (code de désinscription d'un tournoi,
  rappels du planning) : le code s'affiche à l'écran, rien ne part.
- **Prêts longue durée aux adhérents** : ils exigeraient de savoir *qui* a
  emprunté, donc une donnée personnelle dans le prêt — contraire au §9.1.
- **Plusieurs associations sur une même instance** : une instance sert une
  association.

Les idées non retenues ou non instruites sont rassemblées, datées, dans
`docs/idees-evolutions.md`.

---

## 12. Points encore ouverts

*Relu le 2026-10-06.* Les points de la première version de ce document (colonnes du
CSV, double vue du palmarès, cas limites du scan) sont tranchés et intégrés
ci-dessus ; ceux qui ne relevaient que d'une association (budget, nom de
domaine, politique de confidentialité du site) sont sortis du document.

- [ ] **Échéance du jeton** (2026-09) : passée la date, l'accès en écriture
  se ferme pour tous les appareils. L'alternative — continuer d'écrire en
  avertissant — n'est pas tranchée ; c'est la fermeture qui s'applique.
- [ ] **Planning, avant son premier usage réel** (2026-09) : la publication
  montre aux bénévoles les noms des autres bénévoles ; il n'existe pas encore
  de retrait individuel d'un bénévole, seulement la purge d'un événement
  entier. À trancher quand le module servira.
- [ ] **Conservation des signalements** (2026-09) : le détail d'un
  signalement n'est jamais purgé. Acceptable tant que le carnet reste peu
  rempli ; à rouvrir s'il accumule des textes libres.
