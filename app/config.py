"""
Configuration partagée légère : nom de l'association affiché dans l'interface.

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
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Charge .env à la racine s'il existe (sans effet en test/sandbox, comme dans
# app/db.py et app/tournoi/db.py).
load_dotenv()

NOM_ASSOCIATION = os.getenv("NOM_ASSOCIATION", "Des jeux plein la Manche")
