"""
Numéro de version de l'application — source unique partagée.

Évite de dupliquer la chaîne de version entre `app/main.py` (métadonnées
FastAPI, visibles sur /docs), la page publique `/apropos`, l'`INFO.txt` des
sauvegardes et l'écran de supervision.

VERSIONNAGE
-----------
On suit le schéma `MAJEUR.MINEUR.CORRECTIF` (SemVer). La marche à suivre
complète à chaque montée de version est dans `docs/versioning.md`. En résumé :
- MAJEUR : grande étape / changement de cap (1.0.0 = première mise en production) ;
- MINEUR : nouvelle fonctionnalité ou nouveau module, sans casse ;
- CORRECTIF : corrections de bugs et retouches, sans nouveauté.

`APP_VERSION` ci-dessous est le numéro canonique. Le fichier `VERSION` à la
racine reprend le même numéro (+ date + résumé) pour l'écran de supervision ;
`CHANGELOG.md` détaille chaque version. Les trois doivent rester cohérents.
"""

from __future__ import annotations

import re
from pathlib import Path

from markupsafe import Markup, escape

APP_VERSION = "1.27.0"

# Racine du dépôt (deux niveaux au-dessus de ce fichier : app/version.py -> /).
_RACINE = Path(__file__).resolve().parent.parent
_FICHIER_CHANGELOG = _RACINE / "CHANGELOG.md"


def nouveautes_recentes() -> list[str]:
    """
    Puces de la version la plus récente de `CHANGELOG.md`.

    Sert à afficher, sur la page « À propos », les évolutions apportées depuis
    la version précédente. Renvoie la liste des lignes (sans le tiret) de la
    PREMIÈRE section « ## ... » du changelog, ou une liste vide si le fichier
    est absent ou illisible (jamais d'erreur : la page reste affichable).
    """
    if not _FICHIER_CHANGELOG.exists():
        return []
    try:
        lignes = _FICHIER_CHANGELOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    puces: list[str] = []
    dans_section = False
    for ligne in lignes:
        if ligne.startswith("## "):
            if dans_section:
                break  # on atteint la section (version) suivante : on s'arrête
            dans_section = True
            continue
        if dans_section and ligne.strip().startswith("- "):
            puces.append(ligne.strip()[2:].strip())
    return puces


# Les trois mises en forme Markdown que portent les puces de `CHANGELOG.md`
# (inventaire fait sur tout le fichier au correctif 1.25.1) : `code`,
# **gras** et *italique*. Rien d'autre : ni lien, ni titre, ni HTML.
_CODE = re.compile(r"`([^`]+)`")
_GRAS = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# Une étoile seule, jamais collée à une autre : sans quoi les « ** » restés
# sans pair (« ** pas gras ** ») se liraient comme une italique.
_ITALIQUE = re.compile(r"(?<!\*)\*(?![\s*])(.+?)(?<![\s*])\*(?!\*)")


def puce_en_html(puce: str) -> Markup:
    """
    Une puce de `CHANGELOG.md` mise en forme pour /apropos.

    Le texte est ÉCHAPPÉ D'ABORD, en entier : un chevron ou une esperluette
    tapés dans le changelog restent du texte, et aucune balise ne peut passer
    par cette page publique. Seules les trois formes de `_CODE`, `_GRAS` et
    `_ITALIQUE` deviennent ensuite `<code>`, `<strong>` et `<em>`, sur ce
    texte déjà échappé (l'échappement ne touche ni « * » ni « ` »).

    Le contenu d'un `code` n'est pas mis en forme : « `**x**` » s'affiche tel
    quel. Un astérisque sans son pair reste un astérisque.
    """
    morceaux = _CODE.split(str(escape(puce)))
    # `split` avec un groupe alterne texte, code, texte, code… : les rangs
    # impairs sont le contenu des `code`.
    html = []
    for rang, morceau in enumerate(morceaux):
        if rang % 2:
            html.append(f"<code>{morceau}</code>")
        else:
            morceau = _GRAS.sub(r"<strong>\1</strong>", morceau)
            html.append(_ITALIQUE.sub(r"<em>\1</em>", morceau))
    return Markup("".join(html))

