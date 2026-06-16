"""
Génération des étiquettes QR — une par exemplaire.

Chaque étiquette porte :
  - un QR encodant l'URL de la fiche : <BASE_URL>/jeu/<id_exemplaire> ;
  - un emplacement LOGO de l'association (placeholder tant que le logo manque,
    remplaçable par --logo chemin.png) ;
  - un cercle réservé pour une GOMMETTE de couleur (collée à la main) ;
  - le code jeu + le nom ;
  - un CODE DE CLASSEMENT (type « EAM8-3-5-15 ») : partie chiffrée déduite des
    données (âge, joueurs, durée), lettres en placeholder tant que la
    nomenclature n'est pas figée (voir code_classement()).

>>> IMPORTANT : l'URL encodée est DÉFINITIVE. Un QR collé sur une boîte ne doit
    jamais changer. Ne lancer le tirage définitif qu'une fois le NOM DE DOMAINE
    figé. Avant cela, générer des étiquettes de TEST (BASE_URL = tunnel HTTPS ou
    http://localhost:8000). <<<

Source des exemplaires : la base SQLite (lancer d'abord `scripts/import_csv.py`).

Usage :
    python -m scripts.generate_qr                      # PNG individuels dans qr/
    python -m scripts.generate_qr --planche            # + planche PDF A4
    python -m scripts.generate_qr --planche --grille 8x3   # 8 lignes x 3 colonnes
    python -m scripts.generate_qr --logo logo.png          # insère le vrai logo
    python -m scripts.generate_qr --base-url https://abcd.trycloudflare.com
    python -m scripts.generate_qr --limit 12               # échantillon (tests)
    python -m scripts.generate_qr --simple                 # QR nu, sans décor
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection  # noqa: E402

DEFAULT_OUT = Path("qr")

# Couleurs
NOIR = (0, 0, 0)
BLANC = (255, 255, 255)


# ---------------------------------------------------------------------------
# URL et données
# ---------------------------------------------------------------------------
def url_fiche(base_url: str, id_exemplaire: str) -> str:
    """Construit l'URL de fiche, en évitant les doubles slash."""
    return f"{base_url.rstrip('/')}/jeu/{id_exemplaire}"


def charger_exemplaires(limit: int | None = None) -> list[dict]:
    """Retourne les exemplaires + champs du titre (pour étiquette et code)."""
    conn = get_connection()
    try:
        sql = """
            SELECT e.id_exemplaire, t.nom, t.categorie,
                   t.age_min, t.nb_joueurs_min, t.nb_joueurs_max, t.duree_min
            FROM exemplaires e
            JOIN titres t ON t.reference_titre = e.reference_titre
            ORDER BY e.id_exemplaire
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def code_classement(ex: dict) -> str:
    """
    Code de classement type « EAM8-3-5-15 ».

    Structure PROVISOIRE (nomenclature pas encore arrêtée) :
        [3 lettres][âge]-[joueurs min]-[joueurs max]-[durée]
        E = cible (enfant…), A = ambiance (catégorie), M = mot (sous-catégorie).

    Les 3 lettres ne sont pas dérivables proprement des données actuelles : on
    les laisse en placeholder « XXX ». La partie chiffrée est remplie depuis la
    base (âge, joueurs, durée) ; « ? » si l'info manque. À faire évoluer ici
    quand la nomenclature des lettres sera fixée.
    """
    def v(x):
        return str(x) if x not in (None, "") else "?"

    lettres = "XXX"  # placeholder : cible / catégorie / sous-catégorie
    age = v(ex.get("age_min"))
    jmin = v(ex.get("nb_joueurs_min"))
    jmax = v(ex.get("nb_joueurs_max"))
    duree = v(ex.get("duree_min"))
    return f"{lettres}{age}-{jmin}-{jmax}-{duree}"


# ---------------------------------------------------------------------------
# Outils image
# ---------------------------------------------------------------------------
def _police(taille: int):
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
    bbox = draw.textbbox((0, 0), texte, font=police)
    w = bbox[2] - bbox[0]
    draw.text((cx - w // 2, y), texte, fill=fill, font=police)
    return bbox[3] - bbox[1]


def _tronquer(draw, texte, police, largeur_max):
    if draw.textbbox((0, 0), texte, font=police)[2] <= largeur_max:
        return texte
    while texte and draw.textbbox((0, 0), texte + "…", font=police)[2] > largeur_max:
        texte = texte[:-1]
    return texte + "…"


def image_qr_nu(url: str, box: int = 8) -> Image.Image:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def image_etiquette(url: str, ex: dict, logo: Image.Image | None = None,
                    box: int = 8) -> Image.Image:
    """
    Compose une étiquette complète. Tous les cadres de placeholder sont tracés
    en NOIR pour rester visibles après conversion 1-bit de la planche PDF.
    """
    qr = image_qr_nu(url, box)
    pad = 18
    logo_w, logo_h = 150, 74          # cadre logo (haut gauche)
    gom_d = 58                        # diamètre gommette (haut droite)
    header_h = max(logo_h, gom_d + 18)

    f_code = _police(26)
    f_nom = _police(20)
    f_classif = _police(24)
    f_small = _police(13)

    content_w = max(qr.width, logo_w + 40 + gom_d)
    W = content_w + 2 * pad

    # Hauteurs des blocs
    cap_h = 34 + 26                    # code jeu + nom
    classif_h = 46
    qr_y = pad + header_h + 10
    cap_y = qr_y + qr.height + 8
    classif_y = cap_y + cap_h + 6
    H = classif_y + classif_h + pad

    img = Image.new("RGB", (W, H), BLANC)
    d = ImageDraw.Draw(img)

    # --- Logo (haut gauche) ---
    lx0, ly0 = pad, pad
    if logo is not None:
        vignette = logo.copy()
        vignette.thumbnail((logo_w, logo_h))
        img.paste(vignette, (lx0 + (logo_w - vignette.width) // 2,
                             ly0 + (logo_h - vignette.height) // 2))
    else:
        d.rectangle([lx0, ly0, lx0 + logo_w, ly0 + logo_h], outline=NOIR, width=3)
        _texte_centre(d, lx0 + logo_w // 2, ly0 + logo_h // 2 - 10, "LOGO", f_nom)
        _texte_centre(d, lx0 + logo_w // 2, ly0 + logo_h // 2 + 12, "(asso)", f_small)

    # --- Gommette de couleur (haut droite) ---
    gx0 = W - pad - gom_d
    gy0 = pad
    d.ellipse([gx0, gy0, gx0 + gom_d, gy0 + gom_d], outline=NOIR, width=3)
    _texte_centre(d, gx0 + gom_d // 2, gy0 + gom_d + 1, "gommette", f_small)

    # --- QR (centré) ---
    img.paste(qr, ((W - qr.width) // 2, qr_y))

    # --- Code jeu + nom ---
    cx = W // 2
    _texte_centre(d, cx, cap_y, ex["id_exemplaire"], f_code)
    nom = _tronquer(d, ex.get("nom", ""), f_nom, W - 2 * pad)
    _texte_centre(d, cx, cap_y + 34, nom, f_nom)

    # --- Code de classement (bas, encadré) ---
    box_w = W - 2 * pad
    d.rectangle([pad, classif_y, pad + box_w, classif_y + classif_h],
                outline=NOIR, width=3)
    _texte_centre(d, cx, classif_y + 11, code_classement(ex), f_classif)

    return img


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------
def generer_pngs(exemplaires, base_url, out: Path, logo, simple: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for ex in exemplaires:
        url = url_fiche(base_url, ex["id_exemplaire"])
        img = image_qr_nu(url) if simple else image_etiquette(url, ex, logo)
        img.save(out / f"{ex['id_exemplaire']}.png")
    return len(exemplaires)


def generer_planche(exemplaires, base_url, chemin_pdf: Path, lignes: int,
                    colonnes: int, logo, simple: bool) -> int:
    """Planche A4 (300 DPI) : grille lignes x colonnes d'étiquettes."""
    A4 = (2480, 3508)
    par_page = lignes * colonnes
    cw, ch = A4[0] // colonnes, A4[1] // lignes

    pages: list[Image.Image] = []
    for i in range(0, len(exemplaires), par_page):
        page = Image.new("RGB", A4, BLANC)
        for j, ex in enumerate(exemplaires[i:i + par_page]):
            url = url_fiche(base_url, ex["id_exemplaire"])
            cell = image_qr_nu(url) if simple else image_etiquette(url, ex, logo)
            cell.thumbnail((cw - 24, ch - 24))
            col, row = j % colonnes, j // colonnes
            x = col * cw + (cw - cell.width) // 2
            y = row * ch + (ch - cell.height) // 2
            page.paste(cell, (x, y))
        # 1-bit par seuil : net à l'impression, évite le codec JPEG.
        page = page.convert("L").point(lambda v: 0 if v < 128 else 255, mode="1")
        pages.append(page)

    chemin_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(chemin_pdf, save_all=True, append_images=pages[1:], resolution=300.0)
    return len(pages)


def _parse_grille(valeur: str) -> tuple[int, int]:
    """'8x3' -> (8 lignes, 3 colonnes)."""
    try:
        lignes, colonnes = (int(x) for x in valeur.lower().split("x"))
        if lignes < 1 or colonnes < 1:
            raise ValueError
        return lignes, colonnes
    except ValueError:
        raise SystemExit("ERREUR : --grille attend un format 'lignesxcolonnes', ex. 8x3.")


def main() -> None:
    p = argparse.ArgumentParser(description="Génère les étiquettes QR des exemplaires.")
    p.add_argument("--base-url", default=os.getenv("BASE_URL"),
                   help="URL de base (défaut : BASE_URL du .env).")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Dossier de sortie.")
    p.add_argument("--logo", type=Path, help="Image du logo (sinon placeholder).")
    p.add_argument("--planche", action="store_true", help="Planche PDF A4 à imprimer.")
    p.add_argument("--grille", default="6x4",
                   help="Disposition planche 'lignesxcolonnes' (défaut 6x4).")
    p.add_argument("--simple", action="store_true", help="QR nu, sans décor.")
    p.add_argument("--limit", type=int, help="Limiter le nombre d'exemplaires (tests).")
    args = p.parse_args()

    if not args.base_url:
        raise SystemExit("ERREUR : aucune URL de base. Renseigner BASE_URL dans "
                         ".env ou passer --base-url. L'URL encodée est définitive.")

    logo = None
    if args.logo:
        if not args.logo.exists():
            raise SystemExit(f"Logo introuvable : {args.logo}")
        logo = Image.open(args.logo).convert("RGB")

    exemplaires = charger_exemplaires(args.limit)
    if not exemplaires:
        raise SystemExit("Aucun exemplaire en base. Lancer d'abord : "
                         "python -m scripts.import_csv <catalogue.csv>")

    print(f"URL de base : {args.base_url}")
    if any(s in args.base_url for s in ("example", "localhost", "trycloudflare", "ngrok")):
        print("  (URL de TEST — ne pas utiliser pour le tirage définitif.)")
    print(f"Logo : {'fourni' if logo else 'PLACEHOLDER (à remplacer via --logo)'}")

    n = generer_pngs(exemplaires, args.base_url, args.out, logo, args.simple)
    print(f"{n} étiquette(s) PNG écrites dans : {args.out}/")

    if args.planche:
        lignes, colonnes = _parse_grille(args.grille)
        pdf = args.out / "planche-qr.pdf"
        pages = generer_planche(exemplaires, args.base_url, pdf, lignes, colonnes,
                                logo, args.simple)
        print(f"Planche PDF : {pdf} — grille {lignes}x{colonnes} "
              f"({lignes * colonnes}/page), {pages} page(s).")


if __name__ == "__main__":
    main()
