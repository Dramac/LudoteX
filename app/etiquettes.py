"""
Dessin des étiquettes QR — module PARTAGÉ.

Utilisé à la fois par :
- scripts/generate_qr.py (génération en lot : PNG individuels + planche PDF) ;
- app/routes/admin.py (étiquette d'un exemplaire à la demande, pour
  (ré)impression depuis l'écran d'administration).

Centraliser le rendu ici garantit que les deux produisent EXACTEMENT la même
étiquette. Ce module ne touche pas à la base : il reçoit les données déjà lues.

DISPOSITION DE L'ÉTIQUETTE (format paysage)
    +------------------+---------------------------+
    |                  |  [LOGO]          (gommette)|
    |     QR code      |        Nom du jeu          |
    |                  |     [ CODE CLASSEMENT ]    |
    +------------------+---------------------------+
"""

from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

# Couleurs de base (RVB).
NOIR = (0, 0, 0)
BLANC = (255, 255, 255)

# Logo par défaut : app/static/img/logo_djplm.jpg (servi aussi sous
# /static/img/logo_djplm.jpg par l'application).
LOGO_DEFAUT = Path(__file__).resolve().parent / "static" / "img" / "logo_djplm.jpg"


def url_fiche(base_url: str, id_exemplaire: str) -> str:
    """
    Construit l'URL de fiche encodée dans le QR : <base_url>/jeu/<id>.

    `rstrip('/')` évite un double slash si base_url se termine déjà par « / ».
    """
    return f"{base_url.rstrip('/')}/jeu/{id_exemplaire}"


def charger_logo(chemin: Path | None = None) -> Image.Image | None:
    """
    Charge le logo de l'association en image RGB, ou None s'il est absent.

    Args:
        chemin: chemin explicite ; par défaut, LOGO_DEFAUT (racine du dépôt).

    Returns:
        L'image PIL du logo, ou None (un placeholder « LOGO » sera dessiné).
    """
    chemin = chemin or LOGO_DEFAUT
    if chemin and Path(chemin).exists():
        return Image.open(chemin).convert("RGB")
    return None


def code_classement(ex: dict) -> str:
    """
    Code de classement type « EAM8-3-5-15 » (structure PROVISOIRE).

    Format : [3 lettres][âge]-[joueurs min]-[joueurs max]-[durée]
        E = cible (enfant…), A = ambiance (catégorie), M = mot (sous-catégorie).
    Les 3 lettres ne sont pas dérivables des données actuelles → placeholder
    « XXX ». La partie chiffrée vient de la base (« ? » si l'info manque).
    À FAIRE ÉVOLUER ICI quand la nomenclature des lettres sera fixée.

    Args:
        ex: dict contenant age_min, nb_joueurs_min, nb_joueurs_max, duree_min.

    Returns:
        La chaîne du code de classement.
    """
    def v(x):
        return str(x) if x not in (None, "") else "?"

    lettres = "XXX"  # placeholder : cible / catégorie / sous-catégorie
    return (f"{lettres}{v(ex.get('age_min'))}-{v(ex.get('nb_joueurs_min'))}"
            f"-{v(ex.get('nb_joueurs_max'))}-{v(ex.get('duree_min'))}")


# ---------------------------------------------------------------------------
# Helpers Pillow (préfixe _ = usage interne)
# ---------------------------------------------------------------------------
def _police(taille: int):
    """Charge une police TrueType (DejaVu/Arial) ou, à défaut, la police Pillow."""
    for chemin in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        if Path(chemin).exists():
            return ImageFont.truetype(chemin, taille)
    return ImageFont.load_default()


def _texte_centre(draw, cx, y, texte, police, fill=NOIR):
    """Dessine `texte` centré horizontalement sur `cx`. Renvoie sa hauteur."""
    bbox = draw.textbbox((0, 0), texte, font=police)
    draw.text((cx - (bbox[2] - bbox[0]) // 2, y), texte, fill=fill, font=police)
    return bbox[3] - bbox[1]


def _tronquer(draw, texte, police, largeur_max):
    """Tronque `texte` avec « … » s'il dépasse `largeur_max` pixels."""
    if draw.textbbox((0, 0), texte, font=police)[2] <= largeur_max:
        return texte
    while texte and draw.textbbox((0, 0), texte + "…", font=police)[2] > largeur_max:
        texte = texte[:-1]
    return texte + "…"


def _wrap(draw, texte, police, largeur_max, max_lignes=3):
    """Découpe `texte` en lignes tenant dans `largeur_max` (≤ `max_lignes`)."""
    lignes, cur = [], ""
    for mot in (texte or "").split():
        essai = (cur + " " + mot).strip()
        if draw.textbbox((0, 0), essai, font=police)[2] <= largeur_max:
            cur = essai
        else:
            if cur:
                lignes.append(cur)
            cur = mot
            if len(lignes) >= max_lignes:
                break
    if cur and len(lignes) < max_lignes:
        lignes.append(cur)
    lignes = [_tronquer(draw, l, police, largeur_max) for l in lignes[:max_lignes]]
    return lignes or [""]


def image_qr_nu(url: str, box: int = 8) -> Image.Image:
    """
    Image d'un QR « nu » (sans décor) encodant `url`, en RGB.

    `ERROR_CORRECT_M` ≈ 15 % de correction ; `box_size` = pixels par module ;
    `border` = marge en modules.
    """
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def image_etiquette(url: str, ex: dict, logo: Image.Image | None = None,
                    box: int = 8) -> Image.Image:
    """
    Compose l'étiquette complète d'un exemplaire (format paysage).

    QR à gauche ; à droite : logo (ou placeholder), cercle gommette, nom du jeu,
    et code de classement. Le numéro de base n'est pas affiché (il est dans le
    QR). Dimensionnement dynamique pour qu'un nom long ne déborde pas.

    Args:
        url: URL encodée dans le QR (voir url_fiche).
        ex: dict avec au moins `nom` + les champs de code_classement.
        logo: image du logo, ou None (placeholder « LOGO »).
        box: taille de module du QR.

    Returns:
        Une image PIL RGB prête à enregistrer/placer.
    """
    qr = image_qr_nu(url, box)
    pad, gap = 18, 22
    panel_w = 400
    logo_w, logo_h = 240, 150
    gom_d = 64
    classif_h = 46

    f_nom = _police(24)
    f_classif = _police(24)
    f_small = _police(13)
    f_logo = _police(30)

    # Mesure préalable (sur une image jetable) pour calculer la hauteur finale
    # en fonction du nombre de lignes du nom, et éviter tout débordement.
    mesure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lignes = _wrap(mesure, ex.get("nom", ""), f_nom, panel_w, max_lignes=3)
    lh = mesure.textbbox((0, 0), "Ag", font=f_nom)[3] + 8
    header_h = max(logo_h, gom_d + 16)
    panel_h = header_h + 22 + len(lignes) * lh + 22 + classif_h

    W = pad + qr.width + gap + panel_w + pad
    H = pad + max(qr.height, panel_h) + pad

    img = Image.new("RGB", (W, H), BLANC)
    d = ImageDraw.Draw(img)

    # QR à gauche, centré verticalement.
    img.paste(qr, (pad, (H - qr.height) // 2))

    px = pad + qr.width + gap          # bord gauche du panneau de droite
    panel_cx = px + panel_w // 2

    # Logo (ou cadre placeholder « LOGO / (asso) »).
    if logo is not None:
        vignette = logo.copy()
        vignette.thumbnail((logo_w, logo_h))
        img.paste(vignette, (px + (logo_w - vignette.width) // 2,
                             pad + (logo_h - vignette.height) // 2))
    else:
        d.rectangle([px, pad, px + logo_w, pad + logo_h], outline=NOIR, width=3)
        _texte_centre(d, px + logo_w // 2, pad + logo_h // 2 - 18, "LOGO", f_logo)
        _texte_centre(d, px + logo_w // 2, pad + logo_h // 2 + 18, "(asso)", f_small)

    # Gommette : cercle réservé en haut à droite.
    gx0 = W - pad - gom_d
    d.ellipse([gx0, pad, gx0 + gom_d, pad + gom_d], outline=NOIR, width=3)
    _texte_centre(d, gx0 + gom_d // 2, pad + gom_d + 1, "gommette", f_small)

    # Nom du jeu, centré verticalement entre l'en-tête et le code.
    haut_entete = pad + header_h
    bas_code = H - pad - classif_h
    ny = haut_entete + (bas_code - haut_entete - len(lignes) * lh) // 2
    for ligne in lignes:
        _texte_centre(d, panel_cx, ny, ligne, f_nom)
        ny += lh

    # Code de classement, encadré, en bas du panneau.
    cy = H - pad - classif_h
    d.rectangle([px, cy, px + panel_w, cy + classif_h], outline=NOIR, width=3)
    _texte_centre(d, panel_cx, cy + 11, code_classement(ex), f_classif)

    return img
