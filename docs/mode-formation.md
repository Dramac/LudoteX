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

- **Catalogue & prêts** : environ 60 jeux dont les noms sont tirés **au hasard
  du vrai catalogue** de l'association (pour une formation plus parlante que des
  « Jeu d'essai n°… »). Les prêts sont **datés** pour simuler un événement en
  cours depuis plusieurs heures : quelques dizaines de prêts terminés aux durées
  variées (~15 min à ~2 h) répartis dans le temps, plus une douzaine encore en
  cours. Les **statistiques** (palmarès, histogramme horaire, durée moyenne,
  jeux actuellement sortis) sont ainsi fournies et crédibles — de quoi servir
  aussi de **démonstration au bureau**.
- **Tournois** : plusieurs tournois d'exemple couvrant les états et les modes —
  un brouillon, un ouvert aux inscriptions, un par équipes, un high score en
  cours (avec scores), une ronde suisse, une élimination directe, et un tournoi
  terminé avec classement.
- **Planning bénévole** : un planning prérempli complet (postes, créneaux, ~28
  bénévoles fictifs, préremplissage) plus un jumeau resté « collecte ouverte ».

Il est **idempotent** : le relancer repart d'un état propre (seuls les noms de
jeux tirés au hasard peuvent varier d'une fois à l'autre).

> **D'où viennent les noms de jeux ?** Le script LIT le catalogue de production
> en **lecture seule** pour en tirer des noms — jamais il ne l'écrit. Il utilise
> la base pointée par `FORMATION_SOURCE_DB` si elle est définie, sinon le chemin
> de production par défaut (`data/pret-jeux.db`). S'il n'y accède pas (cas
> fréquent sur un serveur de formation isolé), il retombe sur une **liste
> intégrée** de jeux connus — la formation fonctionne quand même. Pour des noms
> fidèles au vrai catalogue sur le VPS, ajouter
> `FORMATION_SOURCE_DB=/var/lib/ludotex/app.db` (chemin de la base de prod) dans
> `/etc/ludotex-formation.env`.

> Ce script vide les bases qu'il cible — ne jamais le lancer en pointant vers
> les bases de PRODUCTION (`DATABASE_PATH`/`TOURNOI_DATABASE_PATH`/
> `PLANNING_DATABASE_PATH` de l'instance de prod). Sur un poste local (hors
> serveur), vérifier son `.env` avant de taper `python -m app.formation`.
> En local, `python lancer.py --formation` s'occupe de tout (bases jetables
> `data/formation-*.db`, peuplement au premier lancement).

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
