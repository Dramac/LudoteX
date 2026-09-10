"""
LES TROIS PORTEURS DU NUMÉRO DE VERSION RESTENT COHÉRENTS.

Le numéro vit à trois endroits PAR CONSTRUCTION, et chacun a une raison
d'exister : `app/version.py` est le numéro canonique lu par le code,
`VERSION` porte en plus la date et le résumé lus par l'écran de supervision,
`CHANGELOG.md` détaille chaque version et alimente « Nouveautés de cette
version » sur /apropos. `docs/versioning.md` décrit comment on les tient
ensemble ; ce fichier vérifie qu'on l'a fait.

Aucun test ne le faisait jusqu'au lot code-boite-3 — et la marche à suivre ne
tenait que par la relecture. Un oubli sur un seul des trois affiche à un
bénévole une version qui n'est pas celle qui tourne, ou des nouveautés qui ne
sont pas les siennes : c'est silencieux, et ça ne se voit qu'en production.
"""

import re
from pathlib import Path

from app.version import APP_VERSION, nouveautes_recentes

_RACINE = Path(__file__).resolve().parent.parent


def test_le_numero_est_bien_du_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


def test_le_fichier_version_porte_le_meme_numero():
    ligne = (_RACINE / "VERSION").read_text(encoding="utf-8").strip()
    assert f" {APP_VERSION} " in f" {ligne} ", ligne


def test_le_fichier_version_porte_une_date_et_un_resume():
    # L'écran de supervision les affiche : un fichier réduit au seul numéro
    # y laisserait deux cases vides.
    ligne = (_RACINE / "VERSION").read_text(encoding="utf-8").strip()
    assert re.search(r"\d{4}-\d{2}-\d{2}", ligne), ligne
    assert len(ligne.split("—")) >= 3, ligne


def test_le_changelog_ouvre_sur_la_version_courante():
    # `nouveautes_recentes` ne lit QUE la première section : une section
    # ajoutée ailleurs qu'en tête ne serait jamais annoncée sur /apropos.
    lignes = (_RACINE / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    premiere = next(l for l in lignes if l.startswith("## "))
    assert premiere.startswith(f"## {APP_VERSION} "), premiere


def test_la_version_courante_annonce_des_nouveautes():
    puces = nouveautes_recentes()
    assert puces, "aucune puce : /apropos afficherait une rubrique vide"
    # Tournées UTILISATEUR : elles s'affichent telles quelles sur /apropos.
    for puce in puces:
        assert ".py" not in puce and "tests/" not in puce, puce
