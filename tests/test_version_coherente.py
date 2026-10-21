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

import pytest

from app.version import APP_VERSION, nouveautes_recentes, puce_en_html

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


# Un geste de serveur n'a rien à faire sur /apropos. `nouveautes_recentes`
# ramasse TOUTES les puces de la première section `## `, sous-titres `###`
# compris : une rubrique « gestes de déploiement » glissée dans CHANGELOG.md
# partirait telle quelle sur la page publique. Ces gestes ont leur propre
# domicile, docs/notes-de-deploiement.md.
_MARQUES_DE_GESTE_SERVEUR = ("/etc/", "systemctl", "sudo", "nginx", "deploy/", "crontab", "certbot")


def test_aucun_geste_de_serveur_dans_les_nouveautes_affichees():
    for puce in nouveautes_recentes():
        for marque in _MARQUES_DE_GESTE_SERVEUR:
            assert marque not in puce, (
                f"« {marque} » dans une puce de /apropos : ce geste va dans "
                f"docs/notes-de-deploiement.md, pas dans CHANGELOG.md — {puce}"
            )


def test_les_gestes_de_deploiement_ont_un_domicile_cite_par_la_marche_a_suivre():
    assert (_RACINE / "docs/notes-de-deploiement.md").is_file()
    for document in ("docs/versioning.md", "docs/deploiement.md"):
        texte = (_RACINE / document).read_text(encoding="utf-8")
        assert "notes-de-deploiement.md" in texte, document


# Les puces s'affichent sur /apropos MISES EN FORME (correctif 1.25.1) : avant,
# le texte était échappé tel quel et le bénévole lisait les « ** » du Markdown.
@pytest.mark.parametrize("puce, attendu", [
    ("**Gras** puis texte", "<strong>Gras</strong> puis texte"),
    ("Ouvrir *nom du jeu* ici", "Ouvrir <em>nom du jeu</em> ici"),
    ("L'**écran** (`/live`) et *lui*",
     "L&#39;<strong>écran</strong> (<code>/live</code>) et <em>lui</em>"),
    ("**Gras avec *italique* dedans**",
     "<strong>Gras avec <em>italique</em> dedans</strong>"),
    # Échappé AVANT la mise en forme : aucune balise ne passe.
    ("<script>x</script> & **<b>y</b>**",
     "&lt;script&gt;x&lt;/script&gt; &amp; <strong>&lt;b&gt;y&lt;/b&gt;</strong>"),
    # Le contenu d'un code n'est pas mis en forme.
    ("`**pas gras**`", "<code>**pas gras**</code>"),
    # Sans son pair, ou entouré d'espaces, un astérisque reste un astérisque.
    ("2 * 3 = 6", "2 * 3 = 6"),
    ("une *seule étoile", "une *seule étoile"),
    ("** pas gras **", "** pas gras **"),
    ("rien du tout", "rien du tout"),
])
def test_une_puce_est_mise_en_forme_sans_laisser_passer_de_html(puce, attendu):
    assert str(puce_en_html(puce)) == attendu


def test_aucune_puce_du_changelog_ne_garde_de_markdown_brut():
    # Toutes les sections, pas seulement la première : une forme nouvelle
    # glissée dans une version future se verrait ici avant d'arriver sur
    # /apropos (lien, souligné…), et c'est ce test qu'il faudra étendre.
    for ligne in (_RACINE / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if ligne.startswith("- "):
            html = str(puce_en_html(ligne[2:]))
            assert "**" not in html and "`" not in html, ligne

