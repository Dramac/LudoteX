"""
CONTRASTES — des ratios CALCULÉS, pas estimés (lot-12-pré-production).

Ce fichier empêche trois constats de l'audit d'accessibilité de revenir :

- `ACC-01` : le contour de focus avait la couleur même du bandeau ;
- `ACC-02` : la couleur d'identité, employée comme texte ou contour sur un fond
  clair, tombait à 1,2:1 pour un thème pâle — le numéro de pochette du
  transfert compris ;
- `ACC-03` / `ACC-04` : la bordure des champs (1,37:1) et le bleu des liens
  (4,05:1 dans le pied de page) sous leur seuil.

Les valeurs de la palette fixe sont LUES dans le `:root` d'app/static/css/
style.css, jamais recopiées : changer une couleur là-bas rejoue ces calculs.
Les ratios eux-mêmes viennent de `services.contraste`, la formule WCAG déjà
employée pour le texte du bandeau.
"""

import colorsys
import re
from pathlib import Path

import pytest

from app import services

CSS = Path("app/static/css/style.css").read_text(encoding="utf-8")


def _racine() -> dict[str, str]:
    """Les variables du PREMIER `:root` de style.css (celui de la palette)."""
    bloc = re.search(r":root\s*\{(.*?)\}", CSS, re.S).group(1)
    bloc = re.sub(r"/\*.*?\*/", "", bloc, flags=re.S)
    valeurs = dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", bloc))
    return {nom: _six(v) for nom, v in valeurs.items()}


def _six(valeur: str) -> str:
    """`#fff` → `#ffffff` : `contraste` n'accepte que la forme longue."""
    valeur = valeur.lower()
    if len(valeur) == 4:
        return "#" + "".join(c * 2 for c in valeur[1:])
    return valeur


RACINE = _racine()
FOND_PAGE = _six(re.search(r"\nbody\s*\{[^}]*?background:\s*(#[0-9a-fA-F]{3,6})",
                           CSS).group(1))

# Les couleurs de thème qui ont servi à trouver les cas limites. Aucune n'est
# rangée « facile » ou « difficile » à la main : le test les traite toutes de
# la même façon.
COULEURS_THEME = [
    services.COULEUR_ASSOCIATION_DEFAUT,
    "#ffffff",   # blanc pur
    "#000000",   # noir pur
    "#fffbe6",   # très pâle
    "#ffeb3b",   # jaune vif, pâle en luminance
    "#00bcd4",   # cyan : lumineux, saturé
    "#0a1a2f",   # très sombre
    "#1f5fa8",   # moyen, déjà conforme
    "#767676",   # gris à la frontière blanc / noir du texte du bandeau
    "#808080",
    "#e91e63",
    "#1b4d3e",
]


# ---------------------------------------------------------------------------
# 1. LES LITTÉRAUX DUPLIQUÉS SONT LES MÊMES DES DEUX CÔTÉS
# ---------------------------------------------------------------------------
def test_les_fonds_clairs_du_calcul_sont_ceux_de_la_feuille_de_style():
    """
    `couleur_lisible_sur_clair` vise les fonds réellement employés : s'ils
    changent dans style.css sans changer en Python, la garantie porte sur des
    fonds que plus personne ne voit.
    """
    assert set(services.FONDS_CLAIRS_FIXES) == {"#ffffff", FOND_PAGE,
                                                RACINE["--vert-clair"]}


def test_la_nuance_lisible_par_defaut_est_celle_du_calcul():
    """Même verrou que `COULEUR_ASSOCIATION_DEFAUT` dans tests/test_theme.py."""
    defaut = services.nuances_theme(services.COULEUR_ASSOCIATION_DEFAUT)
    assert RACINE["--primaire-lisible"] == defaut["lisible"]
    for nom, cle in [("--primaire", "primaire"), ("--primaire-survol", "survol"),
                     ("--primaire-clair", "clair"), ("--primaire-fond", "fond"),
                     ("--primaire-fond-leger", "fond_leger"),
                     ("--primaire-texte", "texte")]:
        assert RACINE[nom] == defaut[cle], nom


# ---------------------------------------------------------------------------
# 2. LA COULEUR D'IDENTITÉ, QUELLE QU'ELLE SOIT
# ---------------------------------------------------------------------------
def _fonds_clairs(nuances):
    return services.FONDS_CLAIRS_FIXES + (nuances["fond"], nuances["fond_leger"])


@pytest.mark.parametrize("couleur", COULEURS_THEME)
def test_la_nuance_lisible_atteint_4_5_sur_tous_les_fonds_clairs(couleur):
    """ACC-02 : texte, contour, numéro de pochette du transfert."""
    n = services.nuances_theme(couleur)
    for fond in _fonds_clairs(n):
        assert services.contraste(n["lisible"], fond) >= 4.5, (couleur, fond)


@pytest.mark.parametrize("couleur", COULEURS_THEME)
def test_le_texte_tient_sur_la_couleur_et_sur_son_survol(couleur):
    """
    Le texte du bouton primaire ne change pas au survol, son fond si : avant
    ce lot, un bleu moyen sous texte blanc tombait à 4,2:1 en s'éclaircissant.
    """
    n = services.nuances_theme(couleur)
    assert services.contraste(n["primaire"], n["texte"]) >= 4.5
    assert services.contraste(n["survol"], n["texte"]) >= 4.5


@pytest.mark.parametrize("couleur", COULEURS_THEME)
def test_le_survol_se_distingue_de_la_couleur(couleur):
    """Corriger le contraste ne doit pas faire disparaître le survol."""
    n = services.nuances_theme(couleur)
    assert n["survol"] != n["primaire"]


@pytest.mark.parametrize("couleur", ["#2a2724", "#000000", "#0a1a2f",
                                     "#1f5fa8", "#1b4d3e"])
def test_une_couleur_deja_conforme_n_est_pas_modifiee(couleur):
    """C'est la couleur de la charte : on n'y touche que si elle ne se lit pas."""
    assert services.nuances_theme(couleur)["lisible"] == couleur


@pytest.mark.parametrize("couleur", ["#fffbe6", "#ffeb3b", "#00bcd4", "#e91e63"])
def test_la_nuance_lisible_garde_la_teinte(couleur):
    """Assombrie, pas remplacée : un jaune reste un jaune (olive), pas un gris."""
    def teinte(valeur):
        r, v, b = (int(valeur[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return colorsys.rgb_to_hls(r, v, b)[0] * 360

    lisible = services.nuances_theme(couleur)["lisible"]
    assert lisible != couleur
    ecart = abs(teinte(lisible) - teinte(couleur))
    assert min(ecart, 360 - ecart) <= 3.0, lisible


def test_le_blanc_pur_donne_un_gris_et_pas_du_noir():
    """
    Le cas extrême du haut : la recherche s'arrête au premier gris qui se lit,
    elle ne descend pas jusqu'au noir.
    """
    lisible = services.nuances_theme("#ffffff")["lisible"]
    assert lisible not in ("#ffffff", "#000000")
    r, v, b = (lisible[i:i + 2] for i in (1, 3, 5))
    assert r == v == b


@pytest.mark.parametrize("couleur", ["#fffbe6", "#ffffff", "#ffeb3b"])
def test_le_style_injecte_porte_la_nuance_lisible(couleur, monkeypatch):
    monkeypatch.setattr(services, "lire_couleur_association", lambda conn: couleur)
    style = services.theme_association()["style"]
    attendu = services.nuances_theme(couleur)["lisible"]
    assert f"--primaire-lisible:{attendu}" in style


# ---------------------------------------------------------------------------
# 3. LA FEUILLE DE STYLE N'EMPLOIE --primaire QU'EN FOND
# ---------------------------------------------------------------------------
def test_primaire_nue_ne_sert_jamais_de_texte_ni_de_contour():
    """
    Partout où la couleur d'identité est un texte ou un contour sur clair,
    c'est `--primaire-lisible`. `--primaire` nue reste réservée aux FONDS, sur
    lesquels `--primaire-texte` est posée. Vu rouge avant correction : neuf
    déclarations (focus, numéro du transfert, puces, chiffres…).
    """
    sans_commentaires = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    proprietes = re.findall(r"([a-z-]+)\s*:[^;{}]*var\(--primaire\)", sans_commentaires)
    assert proprietes, "recherche à revoir : elle ne trouve plus le bandeau"
    assert set(proprietes) == {"background"}, proprietes


def test_le_contour_de_focus_suit_la_couleur_de_la_surface():
    """
    ACC-01. Sur le bandeau, le contour prend la couleur du texte du bandeau,
    garantie à 4,5:1 sur lui ; ailleurs, la nuance lisible (≥ 3:1 exigés).
    """
    assert re.search(r"\.bandeau :focus-visible\s*\{\s*outline-color:\s*var\(--primaire-texte\)",
                     CSS)
    assert re.search(r"outline:\s*3px solid var\(--primaire-lisible\)", CSS)
    assert re.search(r"\.rangement-bandeau-global :focus-visible\s*\{\s*outline-color:\s*currentColor",
                     CSS)
    for couleur in COULEURS_THEME:
        n = services.nuances_theme(couleur)
        assert services.contraste(n["texte"], n["primaire"]) >= 3, couleur
    # Le bandeau du mode rangement : blanc sur son bleu fixe.
    assert services.contraste("#ffffff", RACINE["--bleu"]) >= 3


# ---------------------------------------------------------------------------
# 4. LA PALETTE FIXE — chaque couple réellement employé
# ---------------------------------------------------------------------------
# (couleur, fond, seuil, où) — lus dans le :root, sauf les fonds écrits en
# clair dans une règle (cités avec leur sélecteur).
COUPLES_FIXES = [
    ("--texte", FOND_PAGE, 4.5, "texte courant sur la page"),
    ("--gris", "#ffffff", 4.5, "notes et libellés sur carte"),
    ("--gris", FOND_PAGE, 4.5, "pied de page"),
    ("--bleu", "#ffffff", 4.5, "liens sur carte"),
    ("--bleu", FOND_PAGE, 4.5, "lien du pied de page (ACC-04)"),
    ("--bleu", "#e8f0fe", 4.5, ".resultat-info, .rr-n"),
    ("--bleu", "--vert-clair", 3.0, "numéro de pochette du retour (5 rem)"),
    ("--vert", "--vert-clair", 4.5, "libellé du prêt"),
    ("--orange", "--orange-clair", 4.5, ".resultat-attention"),
    ("--rouge", "--rouge-clair", 4.5, "erreurs"),
    ("--bord-champ", "#ffffff", 3.0, "bordure d'un champ (ACC-03)"),
]


@pytest.mark.parametrize("couleur,fond,seuil,ou", COUPLES_FIXES,
                         ids=[c[3] for c in COUPLES_FIXES])
def test_les_couples_de_la_palette_fixe_atteignent_leur_seuil(couleur, fond, seuil, ou):
    valeur = RACINE[couleur]
    fond = RACINE.get(fond, fond)
    assert services.contraste(valeur, fond) >= seuil, (ou, valeur, fond)


def test_le_bleu_n_est_plus_ecrit_en_dur():
    """
    Une seule déclaration du bleu, dans le `:root` : c'est ce qui permet de le
    corriger d'un geste, et à ce test de le mesurer.
    """
    sans_commentaires = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert "#1a73e8" not in sans_commentaires.lower()
    assert sans_commentaires.count(RACINE["--bleu"]) == 1


@pytest.mark.parametrize("selecteur", [
    ".bouton-secondaire", ".champ input, .champ select", ".champ textarea",
    ".grille-params input", ".pl-besoin",
])
def test_les_champs_sont_delimites_par_la_bordure_contrastee(selecteur):
    regle = re.search(re.escape(selecteur) + r"\s*\{[^}]*\}", CSS).group(0)
    assert "var(--bord-champ)" in regle, regle
