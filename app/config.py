"""
Configuration partagée légère : nom de l'association affiché dans l'interface,
et bascule du MODE FORMATION.

POURQUOI UN MODULE DÉDIÉ
------------------------
Le nom de l'association apparaît à une quinzaine d'endroits (bandeau, pied de
page, page « À propos », exports Excel/PDF, fichiers .ics des tournois, écran
salle, message de partage du jeton…). Le centraliser ici évite les valeurs en
dur dispersées et permet à un autre déploiement de cette application (autre
association) de personnaliser l'affichage sans toucher au code.

CONFIGURATION
-------------
Lu dans la variable d'environnement ``NOM_ASSOCIATION`` (chargée depuis
`.env`), avec repli sur le nom historique "Des jeux plein la Manche" si la
variable est absente — compatibilité avec les déploiements existants qui n'ont
pas encore cette clé dans leur `.env`.

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
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Charge .env à la racine s'il existe (sans effet en test/sandbox, comme dans
# app/db.py et app/tournoi/db.py).
load_dotenv()

NOM_ASSOCIATION = os.getenv("NOM_ASSOCIATION", "Des jeux plein la Manche")

# Bascule mode formation : "1"/"true"/"on" (insensible à la casse) -> actif.
# Toute autre valeur (y compris absente) -> inactif, comportement inchangé.
MODE_FORMATION = os.getenv("MODE_FORMATION", "").strip().lower() in ("1", "true", "on")

# URL de l'instance de formation, affichée en lien depuis l'admin de PRODUCTION
# uniquement (None -> lien masqué). Sans effet si MODE_FORMATION est actif.
FORMATION_URL = os.getenv("FORMATION_URL", "").strip() or None
