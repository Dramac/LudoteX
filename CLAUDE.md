# CLAUDE.md — Contexte projet pour l'assistant

> **Le projet s'appelle LudoteX** (anciennement `pret-jeux`), dépôt
> `https://github.com/Dramac/LudoteX`. Le nom évoque « ludique »,
> « technique » et LaTeX. L'utiliser dans tous les messages, commentaires,
> titres et documents.

Fichier relu au début de chaque session. C'est un **fichier d'état, pas un
journal** : on remplace ce qui a changé, on n'empile pas (voir « Tenir ce
fichier »).

## Où vit quoi — une information, un seul domicile

| Domicile | Contenu |
| --- | --- |
| `docs/specification.md` | Ce que fait LudoteX et pourquoi. Fait foi sur les **intentions et règles métier** ; le **code** fait foi sur le comportement ; toute divergence est un écart à signaler. Numérotation figée (`tests/test_renvois_specification.py`). |
| `docs/guide-developpeur.md` | Architecture, conventions, flux d'une requête, lancement local (§ 8), recettes d'extension, pièges connus. |
| `docs/conception-*.md` | La conception d'un module donné. |
| `docs/ui-composants.md` | Les composants d'interface canoniques et leurs règles d'emploi. |
| `wiki/` | Le **guide utilisateur** (bénévoles, bureau), sans jargon ni chemin de fichier. Dépôt git **séparé**, à committer à part. |
| `CHANGELOG.md`, `VERSION`, `app/version.py` | L'histoire livrée, tournée utilisateur, toujours au même numéro. |
| `docs/notes-de-deploiement.md` | Les gestes de serveur qu'une version demande en plus d'`update.sh` (jamais dans `CHANGELOG.md`, dont les puces partent sur `/apropos`). |
| `interne/chantiers.md` | Le **registre vivant** des chantiers : lots, états, enseignements, invariants. Hors Git. |
| `interne/comptes-rendus/` | Un compte rendu par lot livré, à lire avant le lot suivant. Hors Git. |
| `interne/historique-sessions.md` | Archive du journal de juin à août 2026, chiffres périmés. Hors Git. |
| `CLAUDE.md` (ce fichier) | L'état courant et les règles de travail. Rien d'autre. |

## Le projet

Application web de **prêt de jeux de société** pour l'événement annuel d'une
association (~700 jeux). Les bénévoles scannent un QR par exemplaire avec leur
smartphone pour enregistrer prêts et retours sur une base partagée, en
remplacement de la feuille papier. Anti-vol par **numéro de pochette** où l'on
dépose la pièce d'identité → **aucune donnée personnelle dans le modèle de
données** de l'application de prêt. Le site vitrine et la newsletter
(WordPress) sont une brique séparée, hors dépôt.

Utilisateurs : **bénévoles non techniciens sur smartphone**, en conditions
dégradées (wifi de salle lent, petit écran, geste répété des centaines de fois),
et un **bureau d'association** sans compétence informatique.

## Stack

Python 3 + **FastAPI** sous `uvicorn`, bases **SQLite**, pages rendues côté
serveur (Jinja2), **pas de SPA**. CSS mobile-first **sans framework ni build**.
JS marginal (scanner caméra, scripts inline), **sans aucune dépendance CDN**
(jsQR versionné dans `app/static/js/`). Ajout à l'écran d'accueil avec sa propre
icône, mais **ce n'est pas une PWA** (ni manifeste, ni service worker).
Déploiement : VPS Debian/Ubuntu, nginx, systemd, HTTPS Let's Encrypt.

## Règles métier non négociables

- **Deux clés stables**, quelles que soient les évolutions du CSV :
  `id_exemplaire` (boîte physique, encodée dans le QR en URL
  `/jeu/<id_exemplaire>`, ne change jamais une fois le QR imprimé) et
  `reference_titre` (regroupement des exemplaires d'un même jeu, stats).
- **Numéro de pochette** : commence à 1, toujours le **plus petit numéro
  libre**, recyclé au retour, **AUCUN plafond** (on ne refuse jamais un prêt).
  Un seul jeu par PI / par numéro, le numéro reste attaché à la PI.
- **Logique de scan** — **DISPONIBLE** : « Prêter », « Échanger » (transfert
  inverse), tournoi ou démo (sans pochette, hors stats). **SORTI** : « Rendre »
  (principale, libère le numéro), « Le re-prêter » (oubli de scan : clôt
  l'ancien prêt et en rouvre un), « Transférer » (rendre une boîte et en prêter
  une autre sans changer de pochette).
- **Ne jamais bloquer** : toute incohérence donne un message clair et une action
  de rattrapage **en un tap**, jamais une erreur brute. Cette règle prime sur
  tout le reste.
- **Séparation lecture / écriture** : catalogue et fiches publics et sans
  action ; prêt/retour derrière un **jeton bénévole** aléatoire long mémorisé
  sur l'appareil, + limitation de débit par IP. Pas de comptes individuels.
  Rotation du jeton par l'admin, expiration prolongeable sans changer le lien.
- **Aucune donnée personnelle dans le modèle de données** de l'application de
  prêt. Toute proposition qui en ferait entrer une doit être **signalée comme
  telle avant d'être écrite**. **Un champ de texte libre est une porte** :
  quelqu'un finira par y taper un nom. Portes ouvertes, chacune avec sa
  consigne sous le champ : détail d'un signalement (jamais public ni journalisé
  ni purgé), libellé d'appareil (bureau), annonces de l'écran de salle
  (publiques, 12 h au plus, jamais journalisées ni archivées), nom d'un jeu
  éphémère (tout bénévole). Données personnelles assumées : le planning (base
  séparée, finalité unique, purge par le bureau) et le pseudo de tournoi. Les
  textes que le bureau publie (identité, programme, tournois) et les noms
  d'objets ne sont pas des portes. Liste complète, avec qui lit et ce qui
  efface : `wiki/Rgpd.md`. Une suppression promise se vérifie sur les
  **octets** de la base et de l'archive (`secure_delete`, `VACUUM`), pas par un
  `SELECT`.

## Les trois bases — invariant

Trois bases SQLite **séparées et indépendantes**, chacune avec son schéma, ses
migrations et son initialisation : **prêt** (`app/models.py`), **tournois**
(`app/tournoi/models.py`, tournois et programme), **planning**
(`app/planning/models.py`). Les tables se lisent dans ces fichiers, commentés.

**Un service ne traverse jamais une autre base.** Quand un module a besoin d'un
réglage qui vit ailleurs (le nom de l'événement dans un `.ics`), **c'est la
route qui lit et transmet la valeur au service**.

Le **journal d'activité** (`app/journal.py`) n'est pas une table : un fichier à
rotation, à **vocabulaire fermé** (modules et actions énumérés), gardé par un
test qui interdit d'y écrire hors vocabulaire.

## Décisions de conception structurantes

- `id_exemplaire` en **TEXT** (préserve un zéro de tête, jamais réinterprété en
  entier). **État d'un exemplaire déduit** (prêt avec `date_retour IS NULL`),
  jamais stocké. `titres` : colonnes de cœur + colonnes optionnelles nullables.
- **Migrations idempotentes** par `ALTER TABLE` dans le `db.py` de chaque base.
  Une base d'avant doit toujours se comporter exactement comme avant.
- SQLite avec `PRAGMA foreign_keys = ON` et `journal_mode = WAL`. Écritures
  concurrentes sous `services.transaction(conn)` (`BEGIN IMMEDIATE` + timeout),
  voir `docs/protocole-stress-test.md`.
- **Visibilité des modules** (`app/modules.py`) : tournois, programme, stats,
  planning, écran de salle, « à propos », carnet de maintenance et tombola ont
  quatre états — *tous*, *bénévoles*, *discret* (URL ouverte, lien masqué),
  *désactivé* — réglés depuis `/admin/fonctionnalites`, stockés dans
  `parametres`, *tous* par défaut. Exceptions : tombola *désactivé* par défaut ;
  statistiques *bénévoles* par défaut, *tous* accepté (sans jeton propre sur
  `/stats`, à dessein) ; carnet *bénévoles* par défaut et *tous* refusé
  (`jamais_public`). La visibilité est un réglage d'affichage : une route qui
  exige un jeton porte son `Depends(exiger_jeton)` en propre. Le défaut d'un
  module ne joue que tant que le formulaire n'a jamais été enregistré (il
  inscrit tous les modules).
- **Identité paramétrable** : nom de l'association, « À propos », logo et
  couleur de thème sont des **données éditoriales** en base (`/admin/identite`,
  `/admin/evenement`). Ce qui engage l'infrastructure ou la sécurité reste dans
  `.env`.
- **Un seul fichier d'environnement par instance**, chargé par
  `app/environnement.py` et nulle part ailleurs : le `.env` de la racine, ou
  celui que désigne `LUDOTEX_ENV_FILE` (posé par l'unité de la formation, qui
  ne lit jamais le `.env` de la production).
- **Réutiliser plutôt que dupliquer** : un composant, une constante ou une règle
  a un seul domicile (étiquettes dans `app/etiquettes.py`, partagé avec
  `scripts/generate_qr.py` ; menu bénévole dans un fragment unique ; helpers de
  dates dans `app/services.py`). Les rares duplications sont justifiées en
  commentaire.

## État de l'application

**Dernière version au dépôt : 1.36.0** (`VERSION` et `CHANGELOG.md`, verrouillés
par `tests/test_version_coherente.py`). **La version servie en production ne
figure pas ici** : elle se lit sur `/apropos`, puis sur `/sante` dès qu'une
version qui l'y publie est déployée. Le **total de tests** non plus : il se lit
par `pytest -q` et se consigne au lot dans `interne/chantiers.md`.

Modules livrés (le détail est dans `CHANGELOG.md` et les `conception-*.md`) :

- **Prêt** — catalogue public, fiche par exemplaire, QR éphémères (`X01`…),
  scanner jsQR et saisie de secours, prêt / retour / re-prêt / transfert de
  pochette, sorties tournoi et démo, erreurs de prêt, retours non scannés,
  « pochette pas vide », clôture de fin d'événement.
- **Statistiques** (`/stats`) — comptoir, achats, ce qui plaît, organisation
  (bénévoles seuls), période par édition, exports Excel et PDF.
- **Tournois** (`/tournois`) — quatre modes de scoring (`MODES_SCORING`),
  équipes, BO3, inscription publique avec code de désinscription (**e-mail
  jamais stocké**), `.ics`, duplication.
- **Programme** (`/programme`) — grille publique filtrable, jeux liés par
  référence seule (sans clé étrangère vers la base de prêt).
- **Planning bénévoles** (`/planning`, base séparée) — questionnaire,
  préremplissage glouton, grille d'ajustement **sans JS**, `.ics`.
- **Écran de salle** (`/live`) — tableau temps réel, annonces qui défilent
  (bénévoles via `/live/annonce` si le bureau l'a permis), alerte automatique
  avant chaque tournoi.
- **Rangement**, **carnet de maintenance** (`/maintenance`,
  `/admin/signalements`), **tombola** (désactivée par défaut), **journal
  d'activité** (`/admin/journal`, `scripts/journal.py`).
- **Administration** (`/admin`, mot de passe distinct du jeton) — jeton et
  appareils, fiches de jeux (correction, rattachement, retrait réversible),
  données **BoardGameGeek** (seul site appelé, vignettes copiées en WebP,
  CSP élargie dans la seule `location /admin/`), étiquettes, import/export,
  sauvegarde et restauration des trois bases, supervision, identité, événement,
  visibilité des fonctionnalités.
- **Mode formation** — seconde instance du même code, sans rien inscrire pour
  de bon.
- **Exploitation** — `deploy/` (install.sh, update.sh, systemd, nginx,
  sauvegarde à 3 h et 15 h par **minuteur systemd, jamais par cron**), contrôle
  de report `scripts/controle_report.py` (lecture seule), lanceur local
  `lancer.py` / `.command` / `.bat`.

## Chantiers en cours

**Le registre fait foi : `interne/chantiers.md`** (état des séries et lots,
enseignements, invariants). Ne pas le recopier ici. Lire le compte rendu du lot
précédent (`interne/comptes-rendus/`) avant d'attaquer le suivant.

## Ce qu'un nouveau chantier ne doit pas casser

Acquis de l'ouverture publique, qui se défont sans bruit. À vérifier avant tout
commit, quel que soit le sujet.

1. **Aucune mention de l'association** — code, docs, commits, valeurs par
   défaut. Elle ne vit que dans le `.env` de l'instance. Contrôle automatique :
   `tests/test_neutralisation.py` cherche la suite des mots du nom séparés par
   n'importe quoi, sauts de ligne compris (un `git grep` du nom complet est
   aveugle aux découpes). Noms surveillés : `interne/noms-bannis.txt`, hors
   dépôt ; sans lui le test est ignoré.
2. **Aucune couleur en dur.** Les sept variables du `:root` de
   `app/static/css/style.css` font foi. Exception : couleurs sémantiques (vert,
   rouge, orange) et bleu des liens.
3. **Aucun chemin de logo en dur** : accesseurs d'`app/logo.py`. Les étiquettes
   ont leur propre règle de repli, en commentaire — ne pas l'uniformiser.
4. **L'identité se lit par la route, pas par le service** dans `planning` et
   `tournoi`.
5. **Les notes de travail vont dans `interne/`**, jamais dans `docs/`. Un
   document que le code cite reste dans le dépôt.
6. **Rien de personnel ne rentre** sans avoir été signalé **avant** d'être écrit.
7. **Les captures du wiki portent une identité fictive** (régénérées par
   `scripts/captures_wiki.py`) — jamais le vrai nom ni le vrai logo.

## Workflow de développement

- L'assistant édite et **commit en local**. **C'est Simon qui exécute `git
  push`**, après validation : ne jamais supposer que le code est parti. Remote
  en HTTPS, jeton côté terminal de Simon, jamais dans le chat.
- **Un commit par point traité**, message en français, sans emoji.
- **Chaque changement de comportement s'accompagne de ses tests** ; la suite
  reste verte.
- **Scanner** : tester derrière un tunnel HTTPS au-dessus d'`uvicorn` local
  (`getUserMedia` exige HTTPS ou `localhost`).
- **Vérifier avant d'affirmer.** Ne jamais documenter ni promettre un recours
  sans l'avoir retrouvé dans le code. Une fonction qu'aucune route n'appelle, le
  dire franchement.
- **Signaler les écarts** : une note périmée, contredite par le code ou
  discutable se signale avec un arbitrage proposé, plutôt que de l'appliquer à
  la lettre.
- **Accessibilité** : contrastes, focus clavier visible, hiérarchie des titres,
  lecteurs d'écran, `prefers-reduced-motion`.

### Tenir ce fichier

En fin de session, on **remplace** la ligne d'état concernée, on n'ajoute pas de
paragraphe. Le récit va dans le **message de commit**, dans `CHANGELOG.md`
(tourné utilisateur) et, pour un lot, dans `interne/comptes-rendus/`. Rien de
daté, aucun total de tests, aucun récit de session ici. Plafond : **30 Ko** ;
le dépasser, c'est que la règle a lâché (le fichier avait atteint 225 Ko avant
sa refonte, dont 87 % de journal).

### Tenir le wiki à jour — contrainte de CHAQUE session

`wiki/` est corrigé **dans la même session** que le code qui le périme (commit à
part, dépôt séparé) : rien d'autre ne signale qu'une page devient fausse.
Avant de clore, si le travail a touché à l'un de ces points, chercher la page
qui le mentionne et la corriger :

- un **écran** ajouté, supprimé ou réorganisé ;
- un **libellé de bouton, de menu ou de champ** (le guide les cite mot pour
  mot) ;
- une **URL publique** ajoutée ou renommée ;
- un **comportement métier** visible (pochette, états d'un tournoi, scoring,
  rangement, visibilité) ;
- un **nouveau module** ou une nouvelle option d'administration.

Jamais dans le wiki : refactorisations, tests, migrations, noms de fichiers ou
de fonctions. **Conventions** : infinitif pour les gestes, « vous », jamais de
tutoiement ; libellés en gras et identiques à l'écran ; section « Si ça ne
marche pas » sur toute page décrivant une action ; diagrammes en **Mermaid** ;
captures dans `wiki/images/`, **sans numéro de pochette réel, pseudo, nom de
bénévole ni jeton visible**.

### Proposer une montée de version — à la fin de CHAQUE session

Versionnage SemVer `MAJEUR.MINEUR.CORRECTIF` depuis la 1.0.0 ; marche à suivre
complète et règle du MAJEUR : `docs/versioning.md`, qui fait foi.

Quand une session produit un changement destiné à être déployé, **proposer à
Simon** le numéro (sans l'appliquer d'office) : **CORRECTIF** pour bugs,
retouches d'UI, de texte, d'accessibilité, refactorisations ; **MINEUR** pour
une fonctionnalité ou un module neuf sans casse ; **MAJEUR** pour une grande
étape ou une mise à jour qui exige un geste serveur en plus d'`update.sh` (un
geste facultatif signalé par le contrôle de report n'en est pas un). En cas de
doute, le plus élevé, en expliquant. Rien pour de la documentation seule.
Exception : un lot d'une série qui partage une version ne propose rien et
prépare ses puces de `CHANGELOG.md` dans son compte rendu.

**Si Simon accepte** : dans le même commit, `app/version.py` (`APP_VERSION`),
`VERSION` (numéro + date + résumé) et `CHANGELOG.md` (section en tête, puces
tournées utilisateur), puis rappeler de poser le tag `vX.Y.Z` après le push.

## Sécurité du dépôt

Ne **jamais** committer le jeton bénévole, `.env`, les bases SQLite ni
`interne/` (exclus par `.gitignore` ; modèle : `.env.example`). Les secrets ne
passent ni par les URL ni par les logs.

## Lancer en local

`docs/guide-developpeur.md` § 8 (commandes) et `docs/lancement-local.md`
(tunnel, poste sans ligne de commande : `lancer.command`, `lancer.bat`).

## Ouverture publique de LudoteX — arbitrages

Décisions arrêtées le 2026-08-29 ; **cette section fait foi**. L'avancement est
dans `interne/chantiers.md`. Les numéros sont cités ailleurs : ne pas les
renuméroter.

**1. Cible.** Un outil réutilisable par d'autres associations : dépôt public
lisible, vitrine indexable, produit sans marque d'association. Le dépôt et son
wiki sont **déjà publics**.

**2. Le nom de l'association est une donnée de configuration.** Aucune mention
du nom que porte le `.env` de notre instance dans le code, les docs, le wiki,
les captures ni la vitrine. Aucune exception : Simon est **seul titulaire** des
droits sur le code (tranché le 2026-09-01, `LICENSE` : « Copyright (C) 2026
Simon »).

**3. L'identité en base, l'infrastructure en `.env`.** Administrable
(`parametres`, `/admin/identite`, `/admin/evenement`) : nom, présentation,
contact, URL du dépôt, logo, couleur — données éditoriales qu'un bureau non
technicien corrige seul. Dans `.env` : `BASE_URL` (figée par les QR imprimés),
`MODE_FORMATION`, `FORMATION_URL`, chemins, secrets. Motif à suivre :
`app/services.py`, section « Identité de l'événement » (lecture en base avec
repli, context processor ; route qui lit et transmet pour `planning` et
`tournoi`). « À propos » n'est pas éditable en entier : seuls le paragraphe
« L'association », le contact et les crédits le sont, en texte brut échappé,
**jamais de HTML libre saisi en admin**.

**4. Tri de `docs/`.** Restent ce qui permet d'installer, d'exploiter, de
comprendre et de contribuer (spécification, guide développeur, déploiement,
lancement local, formation, vocabulaire, versioning, composants, stress-test,
`conception-*.md` des modules livrés). Sortent prompts, budgets, audits datés,
études, notes d'idées. **Un document cité par le code reste**, sauf l'audit de
sécurité. Motif : un dépôt GPLv3 sans conception n'est pas reprenable.

**5. Les notes internes vivent dans `interne/`**, hors Git mais dans la
sauvegarde de la machine (sans historique fin, assumé).

**6. Historique : réécrit une seule fois** (`git filter-repo`, fait par Simon le
2026-09-01, chemins internes purgés, nom remplacé dans contenu et messages).
Leçon pour toute réécriture : `--replace-text` travaille ligne par ligne sur des
chaînes exactes, sa liste doit porter **chaque variante** ayant existé (coupée
par une fin de ligne, soudée). Ces opérations s'exécutent dans le terminal de
Simon, jamais par l'assistant.

**7. Vitrine.** Page statique, dépôt séparé `ludotex-site`, GitHub Pages, sur un
**autre sous-domaine** que `ludotex.nicaro.eu`, URL figée par les QR imprimés.

**8. Méthode.** Un fil par lot, ouvert depuis un **prompt autonome** (`/lot`)
qui ne couvre qu'**un seul commit** : objectif, fichiers à lire, contraintes,
définition de fini (suite verte, wiki à jour, commit en français), aucun chiffre
non mesuré, tests qui vont bouger annoncés.
