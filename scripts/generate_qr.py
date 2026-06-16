"""
Génération des QR codes — un par exemplaire.

Chaque QR encode l'URL de la fiche de l'exemplaire :
    <BASE_URL>/jeu/<id_exemplaire>
C'est cette URL que scanne le bénévole (scanner embarqué) ou l'appareil photo
natif. Le contenu ne comporte aucun secret (même accès que le catalogue public).

>>> IMPORTANT : l'URL encodée est DÉFINITIVE. Un QR collé sur une boîte ne doit
    jamais changer. Ne lancer le tirage définitif qu'une fois le NOM DE DOMAINE
    figé. Avant cela, générer des QR de TEST (BASE_URL = tunnel HTTPS ou
    http://localhost:8000). <<<

Source des exemplaires : la base SQLite (lancer d'abord `scripts/import_csv.py`).

Usage :
    python -m scripts.generate_qr                 # PNG individuels dans qr/
    python -m scripts.generate_qr --planche       # + planche PDF A4 à imprimer
    python -m scripts.generate_qr --base-url https://abcd.trycloudflare.com
    python -m scripts.generate_qr --limit 10      # échantillon (tests)
    python -m scripts.generate_qr --out /tmp/qr --no-label

Sortie : un fichier `<id_exemplaire>.png` par exemplaire, avec sous le QR un
libellé « code — nom » (sauf --no-label) pour identifier l'étiquette à l'œil.
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


def url_fiche(base_url: str, id_exemplaire: str) -> str:
    """Construit l'URL de fiche, en évitant les doubles slash."""
    return f"{base_url.rstrip('/')}/jeu/{id_exemplaire}"


def charger_exemplaires(limit: int | None = None) -> list[tuple[str, str]]:
    """Retourne [(id_exemplaire, nom)] depuis la base (nom pour le libellé)."""
    conn = get_connection()
    try:
        sql = """
            SELECT e.id_exemplaire, t.nom
            FROM exemplaires e
            JOIN titres t ON t.reference_titre = e.reference_titre
            ORDER BY e.id_exemplaire
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [(r["id_exemplaire"], r["nom"]) for r in conn.execute(sql)]
    finally:
        conn.close()


def _charger_police(taille: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Police TrueType si disponible, sinon police par défaut de Pillow."""
    for chemin in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        if Path(chemin).exists():
            return ImageFont.truetype(chemin, taille)
    return ImageFont.load_default()


def image_qr(url: str, libelle: str | None, taille_box: int = 10) -> Image.Image:
    """Génère l'image PNG d'un QR, avec un libellé optionnel en dessous."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=taille_box,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    if not libelle:
        return img

    police = _charger_police(max(14, taille_box * 2))
    marge = taille_box * 2
    tmp = ImageDraw.Draw(img)
    bbox = tmp.textbbox((0, 0), libelle, font=police)
    th = bbox[3] - bbox[1]
    canevas = Image.new("RGB", (img.width, img.height + th + marge), "white")
    canevas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canevas)
    tw = draw.textbbox((0, 0), libelle, font=police)[2]
    draw.text(((canevas.width - tw) // 2, img.height + marge // 2),
              libelle, fill="black", font=police)
    return canevas


def libelle_pour(id_ex: str, nom: str, largeur_max: int = 28) -> str:
    nom = nom if len(nom) <= largeur_max else nom[: largeur_max - 1] + "…"
    return f"{id_ex} — {nom}"


def generer_pngs(exemplaires, base_url, out: Path, label: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for id_ex, nom in exemplaires:
        lib = libelle_pour(id_ex, nom) if label else None
        img = image_qr(url_fiche(base_url, id_ex), lib)
        img.save(out / f"{id_ex}.png")
    return len(exemplaires)


def generer_planche(exemplaires, base_url, chemin_pdf: Path,
                    colonnes: int = 4, lignes: int = 6) -> int:
    """
    Planche A4 (300 DPI) prête à imprimer : grille de QR + libellés.
    colonnes x lignes QR par page.
    """
    A4 = (2480, 3508)  # px à 300 DPI
    par_page = colonnes * lignes
    cw, ch = A4[0] // colonnes, A4[1] // lignes
    qr_box = max(4, min(cw, ch) // 33)  # QR ~29 modules + bordure

    pages: list[Image.Image] = []
    for i in range(0, len(exemplaires), par_page):
        page = Image.new("RGB", A4, "white")
        for j, (id_ex, nom) in enumerate(exemplaires[i:i + par_page]):
            cell = image_qr(url_fiche(base_url, id_ex), libelle_pour(id_ex, nom), qr_box)
            cell.thumbnail((cw - 30, ch - 30))
            col, row = j % colonnes, j // colonnes
            x = col * cw + (cw - cell.width) // 2
            y = row * ch + (ch - cell.height) // 2
            page.paste(cell, (x, y))
        # Conversion en 1-bit par seuil (sans tramage) : net pour l'impression
        # et évite le codec JPEG (absent de certains builds Pillow).
        page = page.convert("L").point(lambda v: 0 if v < 128 else 255, mode="1")
        pages.append(page)

    chemin_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(chemin_pdf, save_all=True, append_images=pages[1:], resolution=300.0)
    return len(pages)


def main() -> None:
    p = argparse.ArgumentParser(description="Génère les QR codes des exemplaires.")
    p.add_argument("--base-url", default=os.getenv("BASE_URL"),
                   help="URL de base (défaut : BASE_URL du .env).")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Dossier de sortie.")
    p.add_argument("--no-label", action="store_true", help="QR sans libellé.")
    p.add_argument("--planche", action="store_true",
                   help="Génère aussi une planche PDF A4 à imprimer.")
    p.add_argument("--limit", type=int, help="Limiter le nombre d'exemplaires (tests).")
    args = p.parse_args()

    if not args.base_url:
        raise SystemExit(
            "ERREUR : aucune URL de base. Renseigner BASE_URL dans .env ou "
            "passer --base-url. Rappel : l'URL encodée est définitive."
        )

    exemplaires = charger_exemplaires(args.limit)
    if not exemplaires:
        raise SystemExit(
            "Aucun exemplaire en base. Lancer d'abord : "
            "python -m scripts.import_csv <catalogue.csv>"
        )

    print(f"URL de base : {args.base_url}")
    if "example" in args.base_url or "localhost" in args.base_url or "trycloudflare" in args.base_url:
        print("  (URL de TEST — ne pas utiliser pour le tirage définitif.)")

    n = generer_pngs(exemplaires, args.base_url, args.out, label=not args.no_label)
    print(f"{n} QR PNG écrits dans : {args.out}/")

    if args.planche:
        pdf = args.out / "planche-qr.pdf"
        pages = generer_planche(exemplaires, args.base_url, pdf)
        print(f"Planche PDF : {pdf} ({pages} page(s))")


if __name__ == "__main__":
    main()
