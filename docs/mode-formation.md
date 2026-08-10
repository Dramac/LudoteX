# Mode formation — site bis pour former les bénévoles

## À quoi ça sert

Former les nouveaux bénévoles la semaine avant l'événement, sur une
application qui se comporte EXACTEMENT comme la vraie, sans aucun risque de
toucher aux vraies données (catalogue, prêts, tournois).

## Principe

Le site de formation n'est **pas** un mode caché dans l'application de
production : c'est une **SECONDE INSTANCE** du même code, qui tourne avec sa
propre configuration (`.env` séparé) et ses propres bases SQLite **jetables**.
Il n'y a aucun routage dynamique de connexion dans le code — l'isolation vient
simplement du fait que cette instance ne connaît que ses propres bases.

Elle est exposée sur un **sous-domaine dédié** (ex.
`https://formation.jeux.monasso.fr`), jamais un préfixe de chemin (les liens
absolus des gabarits casseraient).

Sur cette instance, deux choses changent visuellement, sur **toutes** les
pages (publiques, bénévole, admin) :

- un bandeau orange fixe en haut : « 🎓 SITE DE FORMATION — aucun effet sur
  LudoteX » ;
- un filigrane discret « FORMATION » en diagonale sur le fond de chaque page
  (masqué à l'impression).

L'instance de production, elle, n'est **pas modifiée** : sans la variable
`MODE_FORMATION`, aucun changement visuel ni fonctionnel.

## Accéder au site de formation

- Depuis le tableau de bord admin de la **production**, un lien « 🎓 Site de
  formation » apparaît automatiquement si `FORMATION_URL` est renseignée dans
  son `.env` (posée automatiquement par `deploy/install.sh` si le site de
  formation a été installé en même temps).
- Sinon, l'URL du sous-domaine dédié (ex. `https://formation.jeux.monasso.fr`),
  à partager directement aux bénévoles en formation.
- Le mot de passe admin et, selon l'installation, le jeton bénévole peuvent
  être différents de la production — voir ce qui a été choisi lors de
  l'installation (ou `.env` de l'instance de formation).

## Réinitialiser les données de formation

Deux façons, strictement équivalentes (la seconde est ce que fait la
première) :

1. Depuis le tableau de bord admin **de l'instance de formation**
   (visible uniquement là, jamais en production) : bouton
   « Réinitialiser les données de formation », avec confirmation.
2. En ligne de commande, sur le serveur :

   ```bash
   cd /opt/ludotex   # même code que la production
   sudo -u pretjeux bash -c \
     'set -o allexport; source /etc/ludotex-formation.env; set +o allexport; \
      exec .venv/bin/python -m app.formation'
   ```

Dans les deux cas, le script (`app/formation.py`) **vide puis repeuple**
entièrement les **trois** bases de l'instance de formation :

- **Catalogue & prêts** : soit une **copie du vrai catalogue** si
  `FORMATION_CATALOGUE_CSV` est renseignée (voir « Faire fonctionner les QR
  imprimés » plus bas — c'est ce qui permet de scanner de vraies boîtes pendant
  une formation), soit, par défaut, environ 60 jeux fictifs dont les noms sont
  tirés **au hasard du vrai catalogue** de l'association (pour une formation
  plus parlante que des « Jeu d'essai n°… »). Les prêts d'exemple portent dans
  les deux cas sur une soixantaine de boîtes au plus, pour que les statistiques
  de démonstration restent les mêmes. Ils sont **datés** pour simuler un événement en
  cours depuis plusieurs heures : quelques dizaines de prêts terminés aux durées
  variées (~15 min à ~2 h) répartis dans le temps, plus une douzaine encore en
  cours. Les **statistiques** (palmarès, histogramme horaire, durée moyenne,
  jeux actuellement sortis) sont ainsi fournies et crédibles — de quoi servir
  aussi de **démonstration au bureau**.
- **Tournois** : plusieurs tournois d'exemple couvrant les états et les modes —
  un brouillon, un ouvert aux inscriptions, un par équipes, un high score en
  cours (avec scores), une ronde suisse, une élimination directe, et un tournoi
  terminé avec classement. Leur intitulé est le **nom du jeu** seul, comme le
  propose le formulaire : ni le mot « Tournoi », ni l'état, ni le mode de
  scoring, que les écrans affichent déjà. Un seul porte un **titre spécifique**
  (« Coupe des familles »), pour illustrer le champ facultatif.
- **Programme du week-end** (même base que les tournois) : quatre éléments
  couvrant les états ET les surfaces d'affichage — un publié qui commence dans
  20 min (donc visible tout de suite sur l'accueil et dans la colonne
  « Animations » de l'écran de salle), un publié plus tard dans la journée
  (frise), un brouillon (invisible du public) et un annulé dans sa fenêtre
  d'affichage (rendu barré en salle). Les éléments se rattachent aux cinq types
  amorcés par défaut ; `types_programme` n'est **jamais vidée** (c'est de la
  configuration, pas une donnée d'exemple).
- **Planning bénévole** : un planning prérempli complet (postes, créneaux, ~28
  bénévoles fictifs, préremplissage) plus un jumeau resté « collecte ouverte ».
- **Date de l'événement** réglée sur **aujourd'hui**. Sans ce réglage, la frise
  de la page d'accueil et la page `/programme` restent vides quoi qu'on y
  saisisse — sur un site de formation, ce serait un écran mort à expliquer
  plutôt qu'un outil à découvrir. (Manque préexistant, découvert en branchant
  le module Programme : la frise des tournois ne s'affichait pas davantage.)

Il est **idempotent** : le relancer repart d'un état propre (seuls les noms de
jeux tirés au hasard peuvent varier d'une fois à l'autre).

> **D'où viennent les noms de jeux ?** Si `FORMATION_CATALOGUE_CSV` est
> renseignée (voir la section suivante), la question ne se pose pas : le
> catalogue est une copie du vrai, noms compris. Sinon, le script LIT le
> catalogue de production en **lecture seule** pour en tirer des noms — jamais
> il ne l'écrit. Il utilise la base pointée par `FORMATION_SOURCE_DB` si elle
> est définie, sinon le chemin de production par défaut (`data/pret-jeux.db`,
> relatif au dossier d'installation). S'il n'y accède pas — **c'est le cas sur
> le VPS**, où la base de production est à `/var/lib/ludotex/pret-jeux.db` et où
> `install.sh` ne pose pas cette variable —, il retombe sur une **liste
> intégrée** de jeux connus, sans que rien ne le signale à l'écran. Pour des
> noms fidèles au vrai catalogue, ajouter
> `FORMATION_SOURCE_DB=/var/lib/ludotex/pret-jeux.db` dans
> `/etc/ludotex-formation.env`. À noter : cette variable fait lire à l'instance
> de formation un fichier de la production ; l'instantané CSV de la section
> suivante, lui, n'établit aucun lien entre les deux.

> Ce script vide les bases qu'il cible — ne jamais le lancer en pointant vers
> les bases de PRODUCTION (`DATABASE_PATH`/`TOURNOI_DATABASE_PATH`/
> `PLANNING_DATABASE_PATH` de l'instance de prod). Sur un poste local (hors
> serveur), vérifier son `.env` avant de taper `python -m app.formation`.
> En local, `python lancer.py --formation` s'occupe de tout (bases jetables
> `data/formation-*.db`, peuplement au premier lancement).

## Faire fonctionner les QR imprimés sur le site de formation

### Le problème

Constaté à la première session de formation en conditions réelles : les
bénévoles avaient les **vraies boîtes** en main, les liens d'activation du
**site de formation** sur leur téléphone, et aucun scan n'aboutissait.

L'explication tient en une ligne : les QR collés sur les boîtes encodent
`<domaine>/jeu/<identifiant de la boîte>`, et le site de formation ne
connaissait que 60 jeux **fictifs**, aux identifiants inventés. Le scan
fonctionnait — c'est bien l'écran prêt/retour du site de formation qui
s'ouvrait, le scanner n'ayant que faire du domaine inscrit dans le QR — mais il
tombait sur « boîte inconnue ».

### La solution : recopier le catalogue

Donner au site de formation **les mêmes boîtes que la production**, en trois
gestes. C'est un **instantané** : un fichier déposé à la main, rafraîchi quand
le bureau le décide. Les deux instances ne communiquent jamais entre elles.

1. **Sur la production**, tableau de bord admin → **Données & sauvegarde** →
   exporter le catalogue au format **CSV**.
2. **Déposer le fichier sur le serveur**, à un emplacement lisible par le
   service (par exemple `/var/lib/ludotex-formation/catalogue.csv`) :

   ```bash
   # depuis votre poste, remplacez l'utilisateur et le domaine
   scp catalogue.csv root@mon-serveur:/var/lib/ludotex-formation/catalogue.csv
   # sur le serveur : donner le fichier au service et le rendre lisible par lui
   chown pretjeux:pretjeux /var/lib/ludotex-formation/catalogue.csv
   ```

3. **Renseigner la variable**, dans `/etc/ludotex-formation.env` (la ligne y est
   déjà, en commentaire — il suffit de retirer le `#`) :

   ```
   FORMATION_CATALOGUE_CSV="/var/lib/ludotex-formation/catalogue.csv"
   ```

   puis redémarrer l'instance et réinitialiser ses données :

   ```bash
   systemctl restart ludotex-formation
   ```

   puis, sur le site de formation, tableau de bord admin →
   **Réinitialiser les données de formation**.

Le message de confirmation annonce alors *« … 703 jeux (copie du vrai
catalogue) … »*. S'il dit *« (jeux fictifs) »*, le fichier n'a pas été trouvé
ou n'a pas pu être lu : la raison est écrite dans les journaux du serveur
(`journalctl -u ludotex-formation -e`). Dans tous les cas le site de formation
continue de fonctionner — un catalogue fictif vaut mieux qu'une base vide.

Une fois le catalogue recopié, **les étiquettes déjà collées sur les boîtes
fonctionnent** sur le site de formation : le bénévole scanne une vraie boîte,
et c'est l'écran prêt/retour du site de formation qui s'ouvre, avec le bon jeu.
Ce qu'il y fait n'a toujours aucun effet sur la production (bases séparées).

### Ce que cela ne règle pas

Si le bénévole scanne avec l'**appareil photo natif** de son téléphone (le
repli proposé par la page d'aide quand la caméra de l'application refuse de
démarrer), le QR ouvre l'adresse qui y est inscrite : le **site de
production**. Aucun réglage du site de formation ne peut l'intercepter — cette
adresse est dans l'encre.

En pratique, deux précautions suffisent :

- rappeler en début de session de passer par le bouton **Scanner** de
  l'application, jamais par l'appareil photo du téléphone ;
- ne pas activer l'accès à la production sur les téléphones des personnes en
  formation : sans jeton valide, un scan égaré tombe sur « accès refusé » et
  n'enregistre rien.

Pour s'en affranchir complètement, imprimer une petite planche de QR
d'entraînement (section suivante) et former sur ces boîtes-là.

## Imprimer des QR d'entraînement

Mêmes outils que pour la production (`scripts/generate_qr.py`), en pointant
simplement l'URL de base vers le sous-domaine de formation :

```bash
cd /opt/ludotex
sudo -u pretjeux .venv/bin/python -m scripts.generate_qr \
    --base-url https://formation.jeux.monasso.fr --planche --grille 8x2
```

Le PDF généré encode des URL vers l'instance de formation : le scan avec
`/scanner` fonctionne exactement comme en vrai, sans risque. Les étiquettes
portent les mêmes indications visuelles habituelles (rien de spécifique
« formation » n'est ajouté aux étiquettes elles-mêmes — c'est le contenu de
la base, entièrement fictif, qui les distingue).

## Installer le site de formation

Voir `docs/deploiement.md` (section « Site de formation ») pour l'installation
via `deploy/install.sh`, qui automatise tout : sous-domaine, service systemd
dédié (`ludotex-formation`), bloc nginx, certificat HTTPS, bases jetables et
peuplement initial.

## Tout supprimer

Le site de formation ne contient par construction aucune donnée réelle. Pour
le retirer complètement d'un serveur :

```bash
sudo systemctl disable --now ludotex-formation
sudo rm /etc/systemd/system/ludotex-formation.service
sudo systemctl daemon-reload

sudo rm /etc/nginx/sites-enabled/ludotex-formation
sudo rm /etc/nginx/sites-available/ludotex-formation
sudo systemctl reload nginx

sudo rm /etc/ludotex-formation.env
sudo rm -rf /var/lib/ludotex-formation      # ou le chemin choisi à l'installation

# Optionnel : retirer le certificat HTTPS du sous-domaine.
sudo certbot delete --cert-name formation.jeux.monasso.fr
```

Puis, dans le `.env` de la production, effacer ou commenter la ligne
`FORMATION_URL=...` (le lien disparaît du tableau de bord admin) et redémarrer
le service (`sudo systemctl restart ludotex`).
