# CLAUDE.md — Contexte projet pour l'assistant

> **Le projet s'appelle LudoteX** (anciennement `pret-jeux`).
> Dépôt GitHub : `https://github.com/Dramac/LudoteX`
> Ce nom évoque « ludique », « technique » et fait écho à LaTeX.
> L'utiliser dans tous les messages, commentaires, titres et documents.

Fichier relu au début de chaque session. C'est un **fichier d'état, pas un
journal** : on remplace ce qui a changé, on n'empile pas. L'histoire d'une
session va dans le message de commit, dans `CHANGELOG.md` et dans
`interne/comptes-rendus/` — voir « Tenir ce fichier » plus bas.

## Où vit quoi — une information, un seul domicile

| Domicile | Contenu |
| --- | --- |
| `docs/specification.md` | La conception **fait foi**. En cas de divergence, c'est elle qui a raison. |
| `docs/guide-developpeur.md` | Architecture, conventions, flux d'une requête, recettes d'extension, pièges connus. |
| `docs/conception-*.md` | La conception d'un module donné (tournois, planning, journal, rangement, programme, signalements…). |
| `docs/ui-composants.md` | Les dix composants d'interface canoniques et leurs règles d'emploi. |
| `wiki/` | Le **guide utilisateur** (bénévoles, bureau). Aucun jargon, aucun chemin de fichier. Dépôt git **séparé**, à committer à part. |
| `CHANGELOG.md`, `VERSION`, `app/version.py` | L'histoire livrée, tournée utilisateur. Les trois portent toujours le même numéro. |
| `interne/chantiers.md` | Le **registre vivant** du chantier en cours : lots, états, enseignements, invariants. Hors Git. |
| `interne/comptes-rendus/` | Un compte rendu par lot livré, à lire avant d'attaquer le lot suivant. Hors Git. |
| `interne/historique-sessions.md` | Archive du journal détaillé de juin à août 2026. Lecture d'appoint, chiffres périmés. Hors Git. |
| `CLAUDE.md` (ce fichier) | L'état courant et les règles de travail. Rien d'autre. |

## Le projet

Application web de **prêt de jeux de société** pour l'événement annuel d'une
association (~700 jeux). Les bénévoles scannent un QR par exemplaire avec leur
smartphone pour enregistrer prêts et retours sur une base partagée, en
remplacement de la feuille papier (goulet d'étranglement). Anti-vol par
**numéro de pochette** où l'on dépose la pièce d'identité → **zéro donnée
personnelle** dans l'application de prêt, hors champ RGPD.

Ce dépôt = **la brique logicielle uniquement**. Le site vitrine + newsletter
(WordPress, hébergement mutualisé) est une brique séparée, hors dépôt.

Les utilisateurs finaux sont des **bénévoles non techniciens sur smartphone**,
en conditions dégradées (wifi de salle lent, petit écran, geste répété des
centaines de fois dans la journée), et un **bureau d'association** sans
compétence informatique.

## Stack

Python 3 + **FastAPI**, servi par `uvicorn`. Bases **SQLite**. Pages rendues
côté serveur (Jinja2), **pas de SPA**. CSS mobile-first **sans framework ni
build**. Le JS reste marginal (scanner caméra, quelques scripts inline) et
**sans aucune dépendance CDN** (jsQR est versionné dans `app/static/js/`).
**PWA** (« ajouter à l'écran d'accueil »). Déploiement : VPS Debian/Ubuntu,
nginx, systemd, HTTPS Let's Encrypt.

## Règles métier non négociables

- **Deux clés stables**, quelles que soient les évolutions du CSV :
  - `id_exemplaire` — boîte physique unique, encodée dans le QR sous forme
    d'URL `/jeu/<id_exemplaire>`. Ne change jamais une fois le QR imprimé.
  - `reference_titre` — regroupement des exemplaires d'un même jeu (stats).
- **Numéro de pochette** : commence à 1, on attribue toujours le **plus petit
  numéro libre**, recyclé au retour, **AUCUN plafond** (on ne refuse jamais un
  prêt). Un seul jeu par PI / par numéro. Le numéro reste physiquement attaché
  à la PI.
- **Logique de scan** :
  - exemplaire **DISPONIBLE** → action unique « Prêter » (attribue + affiche le
    numéro de pochette en grand).
  - exemplaire **SORTI** → « Rendre » (principale, libère le numéro), « Le
    re-prêter » (oubli de scan : clôt l'ancien prêt et en rouvre un) et
    « Transférer » (rendre une boîte et en prêter une autre sans changer de
    pochette).
- **Ne jamais bloquer** : toute incohérence donne un message clair et une action
  de rattrapage **en un tap**, jamais une erreur brute. Cette règle prime sur
  tout le reste.
- **Séparation lecture / écriture** : catalogue et fiches publics et sans
  action ; prêt/retour derrière un **jeton bénévole** aléatoire long mémorisé
  côté appareil, + limitation de débit par IP. Pas de comptes individuels.
  Rotation du jeton par l'admin, avec date d'expiration.
- **Zéro donnée personnelle** dans l'application de prêt — propriété à
  préserver. Toute proposition qui ferait entrer une donnée personnelle doit
  être **signalée comme telle avant d'être écrite**. Deux exceptions assumées et
  cloisonnées : le module planning (base séparée, finalité unique) et le champ
  libre des signalements du carnet de maintenance.

## Les trois bases — invariant

L'application tient **trois bases SQLite séparées et indépendantes**, chacune
avec son propre schéma, ses migrations et son initialisation :

| Base | Schéma | Tables |
| --- | --- | --- |
| Prêt | `app/models.py` | `titres`, `exemplaires`, `prets`, `pochettes`, `parametres`, `appareils`, `emplacements_rangement`, `signalements`, `categories_signalement` |
| Tournois | `app/tournoi/models.py` | `tournois`, `inscriptions`, `rencontres`, `types_programme`, `programme` |
| Planning | `app/planning/models.py` | `evenements`, `postes`, `creneaux`, `besoins`, `benevoles`, `disponibilites`, `preferences`, `affectations` |

**L'invariant à ne jamais perdre de vue : un service ne traverse jamais une
autre base.** Quand un module a besoin d'un réglage qui vit ailleurs (le nom de
l'événement dans un en-tête `.ics`, par exemple), **c'est la route qui lit et
transmet la valeur au service**.

Le **journal d'activité** (`app/journal.py`) n'est pas une table : c'est un
fichier à rotation, à **vocabulaire fermé** (modules et actions énumérés), avec
un test garde-fou qui interdit d'y écrire quoi que ce soit hors vocabulaire.

Détail des colonnes : lire les fichiers `models.py`, ils sont commentés.

## Décisions de conception structurantes

- `id_exemplaire` stocké en **TEXT** (préserve un éventuel zéro de tête, ex.
  `00472` ; jamais réinterprété comme un entier).
- **État d'un exemplaire déduit** (prêt avec `date_retour IS NULL`), jamais
  stocké.
- `titres` : colonnes de cœur + colonnes optionnelles nullables. Le schéma peut
  évoluer sans toucher aux deux clés.
- **Migrations idempotentes** par `ALTER TABLE` dans le `db.py` de chaque base
  (`CREATE TABLE IF NOT EXISTS` ne met pas à niveau une base existante). Une
  base d'avant doit toujours se comporter exactement comme avant.
- SQLite ouvert avec `PRAGMA foreign_keys = ON` et `journal_mode = WAL`.
  Écritures concurrentes sous `services.transaction(conn)` (`BEGIN IMMEDIATE`
  + timeout) — voir le correctif de course sur les numéros de pochette,
  `docs/protocole-stress-test.md`.
- **Visibilité des modules** (`app/modules.py`) : tournois, programme, stats,
  planning, écran de salle et « à propos » ont chacun quatre états — *tous*,
  *bénévoles*, *discret* (URL ouverte, lien masqué), *désactivé* — réglés depuis
  `/admin/fonctionnalites` et stockés dans `parametres`. L'état par défaut est
  *tous*, donc aucune migration pour les bases existantes.
- **Identité paramétrable** : le nom de l'association, la page « À propos », le
  logo et la couleur de thème sont des **données éditoriales** en base
  (`parametres`, écrans `/admin/identite` et `/admin/evenement`), pas des
  constantes. Ce qui engage l'infrastructure ou la sécurité reste dans `.env`.
- **Réutiliser plutôt que dupliquer** : un composant, une constante ou une règle
  métier a un seul domicile (le dessin d'étiquette dans `app/etiquettes.py`,
  partagé avec `scripts/generate_qr.py` ; le menu bénévole dans un fragment
  unique ; les helpers de dates dans `app/services.py`). Les rares duplications
  assumées sont justifiées en commentaire.

## État de l'application — au 2026-09-01

**Dernière version publiée : 1.10.0** (2026-08-14, voir `VERSION` et
`CHANGELOG.md`). L'application est **en production**. Des commits postérieurs
(lots 1 à 3c du chantier d'ouverture publique) attendent la **1.11.0**, qui est
le lot 8 du registre. Ce qui est livré, par module :

- **Prêt** — catalogue public avec recherche et filtres, fiche par exemplaire,
  scanner caméra (jsQR) avec saisie manuelle de secours, prêt / retour /
  re-prêt / **transfert de pochette**, sortie « tournoi » exclue des stats,
  **erreurs de prêt** (retour en moins d'une minute, motif `erreur`), clôture de
  fin d'événement.
- **Statistiques** (`/stats`) — totaux, palmarès, histogramme horaire, durées,
  filtre par période, liste détaillée, jeux actuellement sortis, exports Excel
  et PDF à sections cochables.
- **Tournois** (`/tournois`) — quatre modes de scoring (`app/tournoi/services.py`,
  `MODES_SCORING` : high score, ronde suisse, round robin, élimination directe),
  tournois **par équipes**, option BO3, inscription publique avec code de
  désinscription (**e-mail jamais stocké**), inscription réservable aux
  bénévoles, export `.ics`, duplication, ouverture groupée du jour.
- **Programme du week-end** (`/programme`) — types d'éléments administrables,
  grille publique filtrable, page publique par élément, reprise sur l'écran de
  salle et sur l'accueil.
- **Planning bénévoles** (`/planning`, base séparée) — questionnaire de
  disponibilités et de préférences, préremplissage glouton (continuité, équité),
  grille d'ajustement **sans JS**, export « mon planning » en `.ics`.
- **Écran de salle** (`/live`) — tableau de bord temps réel pour projecteur,
  annonces libres du bureau, **alerte automatique « rapportez les exemplaires »**
  avant chaque tournoi.
- **Rangement** — emplacements, deux contextes interchangeables, affectation en
  lot, mode rangement visible sur toutes les pages.
- **Carnet de maintenance** — signalements d'état des boîtes et catégories
  administrables.
- **Journal d'activité** — socle à vocabulaire fermé, écran `/admin/journal`,
  outil terminal `scripts/journal.py`, registre des appareils.
- **Administration** (`/admin`, mot de passe distinct du jeton) — jeton et
  appareils, fiches et étiquettes en lot, import/export du catalogue,
  **sauvegarde et restauration des trois bases**, supervision, identité,
  gestion de l'événement, visibilité des fonctionnalités, aide admin.
- **Mode formation** — seconde instance du même code, catalogue importable
  depuis un CSV pour scanner de vraies boîtes sans rien inscrire pour de bon.
- **Exploitation** — `deploy/` (install.sh, update.sh, systemd, nginx,
  sauvegarde) et lanceur local sans ligne de commande (`lancer.py`,
  `lancer.command`, `lancer.bat`).

**Le total de tests ne figure pas ici**, volontairement : c'est le chiffre qui a
fait diverger l'ancien fichier soixante et une fois. Il se lit en lançant la
suite (`pytest -q`) et se consigne au lot correspondant dans
`interne/chantiers.md`.

## Chantier en cours — ouverture publique de LudoteX

**Le registre fait foi : `interne/chantiers.md`.** Il porte l'état de chaque lot,
les enseignements à reporter dans les prompts suivants et les invariants. Ne pas
le recopier ici. Les décisions de fond, elles, sont en fin de ce fichier.

Le compte rendu du lot précédent est **à lire avant d'attaquer le suivant**
(`interne/comptes-rendus/`).

## Workflow de développement

- L'assistant édite les fichiers et **commit en local**. Il ne peut pas pousser :
  **c'est Simon qui exécute `git push`**, après validation. Ne jamais supposer
  que le code est parti.
- Remote en **HTTPS** (jeton personnel côté terminal de Simon ; il ne transite
  jamais par le chat).
- **Un commit par point traité**, message en français, sans emoji.
- **Chaque changement de comportement s'accompagne de ses tests** ; la suite doit
  rester verte.
- **Environnement de test du scanner : tunnel HTTPS** au-dessus d'`uvicorn`
  local. `getUserMedia` exige un contexte sécurisé (HTTPS ou `localhost`).
- **Vérifier avant d'affirmer.** Ne jamais documenter, ni promettre à
  l'utilisateur, un recours ou un comportement sans l'avoir retrouvé dans le
  code. Si une fonction existe mais n'est appelée par aucune route, le dire
  franchement plutôt que d'inventer une procédure plausible.
- **Signaler les écarts.** Quand une note de conception ou une fiche d'audit est
  périmée, contredite par le code, ou demande quelque chose de discutable : le
  dire et proposer un arbitrage, plutôt que de l'appliquer à la lettre.
- **Accessibilité** : contrastes, focus clavier visible, hiérarchie des titres,
  lecteurs d'écran, `prefers-reduced-motion`.

### Tenir ce fichier — règle née de sa refonte du 2026-09-01

`CLAUDE.md` avait atteint **225 Ko**, dont 87 % de journal de sessions accumulé
sans jamais rien retirer. Relu en entier à chaque démarrage, il coûtait cher,
noyait les règles qui comptent et se contredisait. Il a été ramené à une
vingtaine de kilo-octets, le journal archivé dans `interne/historique-sessions.md`.

**La règle, pour que ça ne recommence pas** : en fin de session, on **remplace**
la ligne d'état concernée, on n'ajoute pas un paragraphe. Ce qui a été fait, avec
son raisonnement, va dans le **message de commit**, dans `CHANGELOG.md` (tourné
utilisateur) et, pour un lot de chantier, dans `interne/comptes-rendus/`. Rien de
daté, aucun total de tests, aucun récit de session dans ce fichier. S'il repasse
au-dessus de **30 Ko**, c'est que la règle a lâché.

### Tenir le wiki à jour — contrainte de CHAQUE session

Le dossier `wiki/` est **le guide utilisateur** du projet : bénévoles et bureau,
public non développeur. Il vit dans le dépôt précisément pour être corrigé
**dans le même commit** que le code qui le périme — c'est un **dépôt git
séparé**, à committer à part.

**Le constat qui motive cette règle** (audit interne du 2026-07-18) : quatre
pages étaient fausses ou
incomplètes, non par négligence ponctuelle, mais parce que **rien ne signalait,
au moment du changement, que la doc devait suivre**.

**La règle.** Avant de clore une session, si le travail a touché à l'un de ces
points, vérifier si une page de `wiki/` le mentionne — et la corriger :

- un **écran** ajouté, supprimé ou réorganisé ;
- un **libellé de bouton, de menu ou de champ** modifié (le guide cite les
  libellés mot pour mot : s'ils divergent, c'est le guide qu'on accusera) ;
- une **URL publique** ajoutée ou renommée ;
- un **comportement métier** visible de l'utilisateur (règle de pochette,
  états d'un tournoi, modes de scoring, contextes de rangement, visibilité) ;
- un **nouveau module** ou une nouvelle option d'administration.

Ne PAS documenter dans `wiki/` : refactorisations internes, tests, migrations,
noms de fichiers ou de fonctions. Le wiki ne parle jamais de code.

**Conventions rédactionnelles** : infinitif pour les gestes, « vous » pour s'adresser au lecteur, jamais de
tutoiement ; libellés de boutons en gras et identiques à l'écran ; section « Si
ça ne marche pas » obligatoire sur toute page décrivant une action ; diagrammes
en **Mermaid** ; captures dans `wiki/images/`, **jamais de numéro de pochette
réel, de pseudo, de nom de bénévole ni de jeton visible**.

### Proposer une montée de version — à la fin de CHAQUE session

Depuis la **1.0.0** (première mise en production, 2026-07-23), l'application est
versionnée en **`MAJEUR.MINEUR.CORRECTIF`** (SemVer). Marche à suivre complète :
`docs/versioning.md`.

**La règle.** Quand une session a produit un changement destiné à être déployé,
**proposer à Simon** (sans l'appliquer d'office — c'est lui qui tranche) le
numéro adapté :

- **CORRECTIF** (`x.y.Z`) — corrections de bugs, retouches d'UI, de texte ou
  d'accessibilité, refactorisations : rien de neuf pour l'utilisateur.
- **MINEUR** (`x.Y.0`) — une nouvelle fonctionnalité ou un nouveau module, sans
  casse.
- **MAJEUR** (`X.0.0`) — grande étape, ou évolution qui demande une intervention
  à la mise à jour au-delà d'`update.sh`.

En cas de doute entre deux niveaux, proposer le plus élevé en expliquant
pourquoi. Ne PAS proposer de montée pour un travail qui ne change rien au
déploiement (documentation seule, notes, exploration).

**Si Simon accepte**, mettre à jour dans le même commit les **trois** porteurs du
numéro — `app/version.py` (`APP_VERSION`), `VERSION` (numéro + date + résumé),
`CHANGELOG.md` (nouvelle section en tête, puces tournées utilisateur : elles
s'affichent sur `/apropos`) — puis rappeler à Simon de poser le tag git `vX.Y.Z`
après le push.

## Sécurité du dépôt

Ne **jamais** committer : le jeton bénévole, `.env`, les bases SQLite, le dossier
`interne/`. Ils sont exclus par `.gitignore`. Utiliser `.env.example` comme
modèle. Les secrets ne passent ni par les URL ni par les logs.

## Lancer en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # éditer le jeton, le chemin des bases, le domaine
python -m app.db            # initialise la base de prêt
uvicorn app.main:app --reload
```

Sans ligne de commande : `lancer.command` (macOS), `lancer.bat` (Windows).

## Ouverture publique de LudoteX — arbitrages

Décisions arrêtées le 2026-08-29 ; **cette section fait foi**. L'avancement,
lui, est dans `interne/chantiers.md`.

**1. Cible.** LudoteX devient un outil réutilisable par d'autres associations :
dépôt public lisible, page vitrine indexable, produit sans marque d'association.
Le dépôt `Dramac/LudoteX` et son wiki sont **déjà publics**.

**2. Le nom de l'association est une donnée de configuration.** Aucune mention du
nom de l'association — celui que porte le `.env` de notre instance — ne doit
subsister dans le code, les docs, le wiki, les captures ni la vitrine.

*Titularité des droits — tranchée le 2026-09-01.* Simon est **seul titulaire**
des droits d'auteur sur le code, indépendamment de l'association : le travail a
été réalisé bénévolement, et le bénévolat n'emporte aucune cession automatique.
`LICENSE` a été corrigée en conséquence (nom du programme « LudoteX »,
« Copyright (C) 2026 Simon »). Il n'y a donc plus aucune exception à la règle
ci-dessus, et le lot 7 — la réécriture d'historique — n'est plus bloqué.

**3. Paramétrage : l'identité en base, l'infrastructure en `.env`.** *Appliqué
aux lots 1 à 3c.*

- **Administrable** (table `parametres` de la base de prêt, écrans
  `/admin/identite` et `/admin/evenement`) : nom de l'association, texte de
  présentation, adresse de contact, URL du dépôt, logo, couleur de thème. Ce
  sont des données **éditoriales** : elles changent sans redéploiement et un
  bureau non technicien doit pouvoir les corriger seul.
- **Reste dans `.env`** : `BASE_URL` (figée par les QR imprimés),
  `MODE_FORMATION`, `FORMATION_URL`, chemins des bases et du journal, secrets.
  Un réglage qui engage l'infrastructure ou la sécurité ne descend pas dans une
  interface web.
- **Le motif à suivre** vit dans `app/services.py`, section « Identité de
  l'événement » (`lire_nom_evenement` / `nom_evenement`) : lecture en base avec
  repli, exposition aux gabarits par **context processor**, et pour `planning` et
  `tournoi` c'est **la route qui lit et transmet** — l'indépendance des trois
  bases est un invariant.
- **La page « À propos » n'est pas éditable en entier** : l'essentiel y est de la
  documentation produit, identique pour tout déploiement. Seuls le paragraphe
  « L'association », le contact et les crédits sont paramétrables. Texte brut
  échappé par Jinja, **jamais de HTML libre saisi en admin**.

**4. Tri de `docs/`.** Le dépôt public garde ce qui permet d'installer,
d'exploiter, de comprendre et de contribuer : `specification.md` (fait foi),
`guide-developpeur.md`, `deploiement.md`, `lancement-local.md`,
`mode-formation.md`, `vocabulaire.md`, `versioning.md`, `ui-composants.md`,
`protocole-stress-test.md`, et les `conception-*.md` des modules **livrés**.
Sortent du dépôt : prompts d'implémentation, `budget.md`, présentation au CA,
audits datés, `plan-action-securite.md`, études d'hébergement, notes d'idées et
d'évolutions, veille, cadrages du wiki, `bonne-pratique.md`. Règle d'arbitrage
ajoutée au lot 5 : **un document cité par le code reste**, sauf l'audit de
sécurité.

*Motif du refus de tout sortir : un dépôt GPLv3 sans document de conception n'est
pas reprenable. Pour un tiers, l'absence de documentation technique est le
premier motif d'abandon d'une reprise.*

**5. Les notes internes vivent en local, hors Git, mais sauvegardées.** Dossier
`interne/`, exclu par `.gitignore`, inclus dans la sauvegarde de la machine.
Conséquence assumée : pas d'historique fin ni de diff sur ces documents.

**6. Historique : une réécriture complète, une seule fois.** `git filter-repo`
pour (a) purger les chemins internes de tout l'historique et (b) remplacer le nom
de l'association dans le contenu des fichiers **et** dans les messages de commit
(`--replace-text` + `--replace-message`). Un seul `push --force`.

Ordre impératif : **neutralisation terminée et commitée → puis réécriture → puis
force-push.** Préalables : arbre de travail propre, vérifier les forks sur la
page GitHub, copie du dépôt avant. **À exécuter par Simon dans son terminal**,
pas via l'assistant : `filter-repo` doit supprimer des fichiers dans `.git`, ce
que le montage utilisé par l'assistant interdit. Le wiki est un dépôt distinct :
même opération, et ses captures sont à refaire **avant**.

**7. Vitrine.** Page statique, dépôt séparé `ludotex-site`, GitHub Pages, CNAME
vers un **autre sous-domaine** que `ludotex.nicaro.eu` — cette URL est figée par
les QR déjà imprimés.

**8. Méthode de travail.** Un fil « chantier » par lot, ouvert depuis un **prompt
autonome** rédigé dans le fil centralisateur. Chaque prompt rappelle l'objectif,
les fichiers à lire d'abord, les contraintes du projet, la définition de fini
(suite verte, wiki à jour, un commit en français) et ne couvre qu'**un seul
commit**. Ne pas y citer de chiffres non mesurés ; annoncer les tests qui vont
bouger. Le registre des lots vit sur disque (`interne/chantiers.md`), pas dans la
mémoire d'une conversation.
