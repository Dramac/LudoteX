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

# Meeple LudoteX en noir, imprimé sur l'étiquette quand l'association n'a
# déposé aucun logo — voir `charger_logo`. Fichier VERSIONNÉ, copié depuis
# `logo/01_Symbole_sans_logotype/png/512/LudoteX_A2_symbol_mono_noir_512px.png`
# (même duplication assumée que les fichiers listés dans app/logo.py :
# `logo/` est l'atelier, `app/static/img/` est ce que l'application sert).
# Un fichier DISTINCT de `app/static/img/logo.png` : celui-ci sert l'écran en
# couleur, celui-ci sert le papier — une seule encre, jamais de dégradé ni de
# nuance perdue à l'impression.
_MEEPLE_DEFAUT = Path(__file__).resolve().parent / "static" / "img" / "logo-etiquette-defaut.png"


def url_fiche(base_url: str, id_exemplaire: str) -> str:
    """
    Construit l'URL de fiche encodée dans le QR : <base_url>/jeu/<id>.

    `rstrip('/')` évite un double slash si base_url se termine déjà par « / ».
    """
    return f"{base_url.rstrip('/')}/jeu/{id_exemplaire}"


def chemin_logo_defaut() -> Path | None:
    """
    Chemin du logo DÉPOSÉ par l'association (`data/logo.png`), ou None.

    Une FONCTION et non une constante de module : le dossier `data/` se déduit
    du chemin de la base de prêt, qui est réglable (`DATABASE_PATH`) et que les
    tests remplacent. Une constante calculée à l'import figerait le chemin du
    premier processus venu. Le domicile du calcul est `app/logo.py`.
    """
    from app.logo import chemin_logo_etiquettes

    return chemin_logo_etiquettes()


def _aplati_sur_blanc(image: Image.Image) -> Image.Image:
    """
    Compose `image` sur un fond blanc en tenant compte du canal alpha, puis
    renvoie une image RGB.

    `Image.open(...).convert("RGB")` SEUL ignore la transparence : Pillow garde
    les canaux RVB bruts sous les pixels transparents, souvent noirs. Sur une
    étiquette imprimée en noir et blanc, un logo à fond transparent
    ressortirait donc entouré d'un rectangle noir — pas théorique :
    `app/static/img/logo.png` est lui-même un PNG RVBA, et le meeple par
    défaut ci-dessous aussi. Convertir en RGBA PUIS composer sur du blanc en
    utilisant le canal alpha comme masque est la seule façon d'obtenir un fond
    réellement blanc, que l'image de départ ait ou non une transparence (sans
    canal alpha, `.convert("RGBA")` en ajoute un opaque à 255 partout : le
    résultat est alors identique à un simple `.convert("RGB")`).
    """
    rgba = image.convert("RGBA")
    fond = Image.new("RGB", rgba.size, BLANC)
    fond.paste(rgba, mask=rgba.split()[-1])
    return fond


def charger_logo(chemin: Path | None = None) -> Image.Image:
    """
    Charge le logo à imprimer sur l'étiquette : celui DÉPOSÉ PAR L'ASSOCIATION
    s'il existe, sinon le meeple LudoteX par défaut (`_MEEPLE_DEFAUT`).

    CE RAISONNEMENT A CHANGÉ AU LOT 3c — LA VERSION PRÉCÉDENTE RENVOYAIT NONE.
    L'ancien argument (sur sept cents boîtes, un logo est une marque
    PERMANENTE apposée sur le bien d'une association : imprimer l'emblème du
    logiciel qu'elle utilise serait une signature qu'elle n'a pas demandée)
    comparait les mauvaises choses. Le vrai choix n'est pas « le meeple ou
    rien », c'est « le meeple ou un cadre imprimé contenant le mot LOGO » — un
    marqueur de développement transformé en encre permanente sur tout un
    tirage. Entre les deux, le meeple gagne largement.

    Le SIGNAL que portait l'ancien cadre (« pense à déposer ton logo avant
    d'imprimer ») n'a pas disparu : il a déménagé de l'étiquette vers l'écran
    de génération (`/admin/etiquettes`, voir `app.logo.logo_regle`), où il peut
    encore être lu et corrigé — un cadre sur sept cents boîtes déjà imprimées
    ne peut plus l'être.

    Ne pas réintroduire de branche « None -> cadre placeholder » ici ni dans
    `image_etiquette`/`planche_pdf` : c'est précisément ce que ce lot retire.

    Args:
        chemin: chemin explicite (option `--logo` de scripts/generate_qr.py) ;
            par défaut, le logo déposé dans `data/` s'il existe.

    Returns:
        L'image PIL du logo, prête à imprimer (RGB, fond blanc même si la
        source a un canal alpha — voir `_aplati_sur_blanc`). Ne renvoie JAMAIS
        None.
    """
    chemin = chemin or chemin_logo_defaut()
    if chemin and Path(chemin).exists():
        return _aplati_sur_blanc(Image.open(chemin))
    return _aplati_sur_blanc(Image.open(_MEEPLE_DEFAUT))


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

    QR à gauche ; à droite : logo, cercle gommette, nom du jeu, et code de
    classement. Le numéro de base n'est pas affiché (il est dans le QR).
    Dimensionnement dynamique pour qu'un nom long ne déborde pas.

    Il n'existe plus de cadre placeholder « LOGO » depuis le lot 3c — voir
    `charger_logo` pour le raisonnement.

    Args:
        url: URL encodée dans le QR (voir url_fiche).
        ex: dict avec au moins `nom` + les champs de code_classement.
        logo: image du logo à imprimer, ou None pour reprendre le repli de
            `charger_logo()` (le logo déposé par l'association, sinon le
            meeple LudoteX par défaut).
        box: taille de module du QR.

    Returns:
        Une image PIL RGB prête à enregistrer/placer.
    """
    if logo is None:
        logo = charger_logo()
    qr = image_qr_nu(url, box)
    pad, gap = 18, 22
    panel_w = 400
    logo_w, logo_h = 240, 150
    gom_d = 64
    classif_h = 46

    f_nom = _police(24)
    f_classif = _police(24)
    f_small = _police(13)

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

    # Logo : jamais None ici (voir la docstring), toujours une vignette.
    vignette = logo.copy()
    vignette.thumbnail((logo_w, logo_h))
    img.paste(vignette, (px + (logo_w - vignette.width) // 2,
                         pad + (logo_h - vignette.height) // 2))

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


# Espace intérieur de chaque cellule (mm) pour ne pas coller les étiquettes.
ESPACE_CELLULE_MM = 1.5


def planche_pdf(exemplaires, base_url, logo=None, *, lignes=8, colonnes=2,
                marge_gauche_mm=8.0, marge_droite_mm=8.0,
                marge_haut_mm=8.0, marge_bas_mm=8.0) -> bytes:
    """
    Construit une planche A4 d'étiquettes (PDF couleur) et renvoie les octets.

    La zone utile = A4 moins les 4 marges de page ; elle est découpée en une
    grille `lignes` x `colonnes`. Chaque étiquette est mise à l'échelle pour
    tenir dans sa cellule (proportions conservées), avec un petit espace
    intérieur (ESPACE_CELLULE_MM).

    Utilise reportlab (gère la couleur du logo, sans dépendre du codec JPEG).

    Args:
        exemplaires: liste de dicts (nom + champs de code_classement + id).
        base_url: base de l'URL encodée dans le QR.
        logo: image PIL du logo, ou None pour reprendre le repli de
            `charger_logo()` — résolu UNE SEULE FOIS ici, pas à chaque
            étiquette : sur sept cents exemplaires, relire le fichier à chaque
            tour de boucle serait un aller-retour disque inutile.
        lignes, colonnes: disposition de la grille (≥ 1).
        marge_*_mm: marges de page en millimètres.

    Returns:
        Le contenu binaire du PDF.

    Raises:
        ValueError: si la grille est invalide ou si les marges ne laissent pas de
            place utile sur la page.
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if logo is None:
        logo = charger_logo()

    if lignes < 1 or colonnes < 1:
        raise ValueError("Le nombre de lignes et de colonnes doit être ≥ 1.")

    page_w, page_h = A4
    ml, mr, mt, mb = (v * mm for v in
                      (marge_gauche_mm, marge_droite_mm, marge_haut_mm, marge_bas_mm))
    zone_w = page_w - ml - mr
    zone_h = page_h - mt - mb
    if zone_w <= 0 or zone_h <= 0:
        raise ValueError("Les marges sont trop grandes pour la page A4.")

    cw, ch = zone_w / colonnes, zone_h / lignes
    pad = ESPACE_CELLULE_MM * mm
    par_page = lignes * colonnes

    def lecteur_png(im) -> ImageReader:
        buf = BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)

    sortie = BytesIO()
    c = canvas.Canvas(sortie, pagesize=A4)
    for i in range(0, len(exemplaires), par_page):
        for j, ex in enumerate(exemplaires[i:i + par_page]):
            url = url_fiche(base_url, ex["id_exemplaire"])
            label = image_etiquette(url, ex, logo)
            iw, ih = label.size
            col, row = j % colonnes, j // colonnes
            avail_w, avail_h = cw - 2 * pad, ch - 2 * pad
            scale = min(avail_w / iw, avail_h / ih)
            w, h = iw * scale, ih * scale
            # Origine reportlab en bas-gauche : on calcule depuis le haut de la
            # zone utile (page_h - marge_haut).
            cell_x = ml + col * cw
            cell_haut = page_h - mt - row * ch
            x = cell_x + (cw - w) / 2
            y = cell_haut - ch + (ch - h) / 2
            c.drawImage(lecteur_png(label), x, y, w, h, preserveAspectRatio=True)
        c.showPage()
    c.save()
    return sortie.getvalue()
