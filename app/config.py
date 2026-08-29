"""
Configuration partagée légère : repli du nom de l'association, et bascules du
MODE FORMATION et du JOURNAL D'ACTIVITÉ.

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
NOM_ASSOCIATION = os.getenv("NOM_ASSOCIATION", "LudoteX")

# Bascule mode formation : "1"/"true"/"on" (insensible à la casse) -> actif.
# Toute autre valeur (y compris absente) -> inactif, comportement inchangé.
MODE_FORMATION = os.getenv("MODE_FORMATION", "").strip().lower() in ("1", "true", "on")

# URL de l'instance de formation, affichée en lien depuis l'admin de PRODUCTION
# uniquement (None -> lien masqué). Sans effet si MODE_FORMATION est actif.
FORMATION_URL = os.getenv("FORMATION_URL", "").strip() or None

# Fichier du journal d'activité (JSON Lines) et bascule de la recopie console.
JOURNAL_PATH = os.getenv("JOURNAL_PATH", "data/journal.log")
JOURNAL_CONSOLE = os.getenv("JOURNAL_CONSOLE", "").strip().lower() in ("1", "true", "on")
