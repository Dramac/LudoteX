"""
Configuration partagée légère : replis du nom de l'association et de l'URL du
dépôt, et bascules du MODE FORMATION et du JOURNAL D'ACTIVITÉ.

NOM DE L'ASSOCIATION — CE MODULE N'EN EST PLUS LE DOMICILE
----------------------------------------------------------
Le nom de l'association est une donnée ÉDITORIALE : un bureau non technicien
doit pouvoir la corriger seul, sans éditer un fichier sur le serveur ni
redémarrer le service. Elle se règle donc depuis ``/admin/identite`` et vit en
base, dans la table `parametres` de la base de PRÊT.

Le domicile de la LECTURE est ``app/services.py``, section « Identité de
l'ASSOCIATION » : le couple ``lire_nom_association(conn)`` / ``nom_association()``,
sur le patron de l'identité de l'événement. Les gabarits reçoivent la valeur
par un CONTEXT PROCESSOR (voir app/templating.py), donc ``{{ nom_association }}``
y reste écrit tel quel.

``NOM_ASSOCIATION`` ci-dessous n'est plus qu'un REPLI DE SECOND RANG dans cette
cascade :

    valeur en base  ->  NOM_ASSOCIATION (.env)  ->  "LudoteX"

Il reste utile à deux titres : un déploiement existant qui a déjà réglé cette
variable dans son `.env` ne change pas d'apparence, et ``deploy/install.sh``
peut la poser dès l'installation, avant tout passage en administration.

Le défaut est « LudoteX », le nom du logiciel : une association qui vient de
l'installer voit un nom neutre et juste, jamais celui d'une autre association.

URL DU DÉPÔT DU CODE SOURCE
---------------------------
Même statut que le nom de l'association, et pour la même raison : c'est une
donnée ÉDITORIALE, réglable depuis ``/admin/identite``, qui vit en base.

    valeur en base  ->  DEPOT_URL (.env)  ->  "https://github.com/Dramac/LudoteX"

``DEPOT_URL`` ci-dessous n'est donc, lui aussi, qu'un REPLI DE SECOND RANG. Le
dernier repli est ce littéral, et il n'a QUE ce domicile.

Cette valeur ne peut jamais être vide : elle atterrit dans le ``href`` du lien
« code source » de la page « À propos », et la GPL veut qu'un utilisateur
puisse atteindre la source de la version qu'il fait tourner. D'où le
``.strip() or`` : une variable présente mais VIDE (``DEPOT_URL=`` dans un
`.env` recopié depuis `.env.example`) doit retomber sur le littéral, ce que le
seul défaut d'``os.getenv`` ne fait pas — il ne s'applique qu'à une variable
ABSENTE. Même précaution sur ``NOM_ASSOCIATION``, pour la même raison.

MODE FORMATION
--------------
``MODE_FORMATION`` (0/1, défaut 0/absent) distingue une instance de
FORMATION d'une instance de PRODUCTION. Voir `docs/mode-formation.md` pour le
principe complet (une SECONDE INSTANCE du même code, ses propres bases
jetables, aucun routage dynamique de connexion). Quand actif :
- un bandeau + un filigrane s'affichent sur toutes les pages (`base.html`) ;
- le bouton « Réinitialiser les données de formation » apparaît au tableau de
  bord admin (voir `routes/admin.py`).
Quand absent/0 (déploiement de production normal) : **aucun changement visuel
ni fonctionnel** — c'est le comportement historique de l'application.

``FORMATION_URL`` (optionnelle) n'a de sens QUE sur l'instance de PRODUCTION :
elle affiche un simple lien « Site de formation » au tableau de bord admin,
pointant vers l'instance de formation (sous-domaine séparé). Masqué si absente.

JOURNAL D'ACTIVITÉ
-------------------
``JOURNAL_PATH`` (défaut ``data/journal.log``) : fichier JSON Lines du journal
d'activité (voir `app/journal.py` et `docs/conception-journal.md`). Sur une
instance de formation, à régler sur un chemin DISTINCT de la production (même
principe que les bases jetables) — sinon les deux instances écrivent dans le
même fichier.

``JOURNAL_CONSOLE`` (0/1, défaut 0) : recopie chaque ligne, formatée pour
l'œil, sur la console du process (utile en phase de test, à côté des logs
uvicorn). Explicitement désactivé par défaut pour qu'une production n'hérite
pas par inadvertance d'un comportement pensé pour le développement.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Charge .env à la racine s'il existe (sans effet en test/sandbox, comme dans
# app/db.py et app/tournoi/db.py).
load_dotenv()

# Repli de SECOND RANG du nom de l'association : la valeur réglée en
# administration (base) l'emporte. Voir la docstring du module.
# `.strip() or` et non le seul défaut d'`os.getenv` : `.env.example` livre la
# ligne `NOM_ASSOCIATION=` vide, et une variable présente mais vide rendrait
# une chaîne vide — donc un bandeau et un titre d'onglet sans nom.
NOM_ASSOCIATION = os.getenv("NOM_ASSOCIATION", "").strip() or "LudoteX"

# Domaine des UID iCalendar, partagé par les TROIS exports .ics (planning,
# tournoi, programme). DOMICILE UNIQUE : les trois modules portaient jusqu'ici
# le même littéral recopié, qui contenait le nom de l'association en toutes
# lettres et SOUDÉ (sans espaces) — invisible à toute recherche de ce nom, et
# publié tel quel dans chaque .ics exporté par un bénévole.
#
# C'est le nom du PRODUIT, jamais celui d'un déploiement, et il est FIGÉ : un
# agenda reconnaît un événement déjà importé à son UID. Le changer transforme
# une mise à jour en doublon chez tous ceux qui ont déjà importé un .ics.
# Ne pas le dériver d'un réglage administrable, pour cette raison exactement.
DOMAINE_UID_ICS = "ludotex"

# Repli de SECOND RANG de l'URL du dépôt du code source, et DOMICILE UNIQUE du
# dernier repli de la cascade. Voir la docstring du module.
DEPOT_URL = (os.getenv("DEPOT_URL", "").strip()
             or "https://github.com/Dramac/LudoteX")

# Bascule mode formation : "1"/"true"/"on" (insensible à la casse) -> actif.
# Toute autre valeur (y compris absente) -> inactif, comportement inchangé.
MODE_FORMATION = os.getenv("MODE_FORMATION", "").strip().lower() in ("1", "true", "on")

# URL de l'instance de formation, affichée en lien depuis l'admin de PRODUCTION
# uniquement (None -> lien masqué). Sans effet si MODE_FORMATION est actif.
FORMATION_URL = os.getenv("FORMATION_URL", "").strip() or None

# Fichier du journal d'activité (JSON Lines) et bascule de la recopie console.
JOURNAL_PATH = os.getenv("JOURNAL_PATH", "data/journal.log")
JOURNAL_CONSOLE = os.getenv("JOURNAL_CONSOLE", "").strip().lower() in ("1", "true", "on")
