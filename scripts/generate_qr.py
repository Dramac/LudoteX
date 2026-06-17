"""
Génération en lot des étiquettes QR — une par exemplaire.

Le DESSIN de l'étiquette (QR, logo, gommette, nom, code de classement) est
mutualisé dans app/etiquettes.py — partagé avec l'écran d'administration, pour un
rendu identique. Ce script s'occupe de : lire les exemplaires en base, produire
les PNG individuels, et assembler une planche PDF prête à imprimer.

>>> IMPORTANT : l'URL encodée est DÉFINITIVE. Un QR collé sur une boîte ne doit
    jamais changer. Ne lancer le tirage définitif qu'une fois le NOM DE DOMAINE
    figé. Avant cela, générer des étiquettes de TEST (BASE_URL = tunnel HTTPS ou
    http://localhost:8000). <<<

Source des exemplaires : la base SQLite (lancer d'abord `scripts/import_csv.py`).

Usage :
    python -m scripts.generate_qr                      # PNG individuels dans qr/
    python -m scripts.generate_qr --planche            # + planche PDF A4
    python -m scripts.generate_qr --planche --grille 8x3   # 8 lignes x 3 colonnes
    python -m scripts.generate_qr --logo logo.png          # logo explicite
    python -m scripts.generate_qr --base-url https://abcd.trycloudflare.com
    python -m scripts.generate_qr --limit 12               # échantillon (tests)
    python -m scripts.generate_qr --simple                 # QR nu, sans décor
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Permet « python scripts/generate_qr.py » comme « python -m scripts.generate_qr ».
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection  # noqa: E402
from app.etiquettes import (  # noqa: E402
    charger_logo,
    image_etiquette,
    image_qr_nu,
    url_fiche,
)

DEFAULT_OUT = Path("qr")


def charger_exemplaires(limit: int | None = None) -> list[dict]:
    """
    Charge les exemplaires depuis la base, avec les champs du titre nécessaires
    à l'étiquette (nom) et au code de classement (âge, joueurs, durée).

    Args:
        limit: si fourni, ne renvoie que les N premiers (échantillon de test).

    Returns:
        Liste de dicts (un par exemplaire), triés par id_exemplaire.
    """
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


def generer_pngs(exemplaires, base_url, out: Path, logo, simple: bool) -> int:
    """
    Écrit un PNG par exemplaire dans `out` (fichier `<id>.png`).

    Args:
        exemplaires: liste de dicts (voir charger_exemplaires).
        base_url: base de l'URL encodée dans le QR.
        out: dossier de sortie (créé au besoin).
        logo: image PIL du logo, ou None (placeholder).
        simple: True = QR nu ; False = étiquette complète.

    Returns:
        Le nombre d'étiquettes générées.
    """
    out.mkdir(parents=True, exist_ok=True)
    for ex in exemplaires:
        url = url_fiche(base_url, ex["id_exemplaire"])
        img = image_qr_nu(url) if simple else image_etiquette(url, ex, logo)
        img.save(out / f"{ex['id_exemplaire']}.png")
    return len(exemplaires)


def generer_planche(exemplaires, base_url, chemin_pdf: Path, lignes: int,
                    colonnes: int, logo, simple: bool, marge_mm: float = 2.0) -> int:
    """
    Génère une planche A4 multipage prête à imprimer.

    Grille `lignes` x `colonnes` d'étiquettes par page. On utilise reportlab
    (et non Pillow) pour le PDF car il gère les images couleur (logo) sans
    dépendre du codec JPEG, absent de certains builds Pillow.

    Args:
        exemplaires, base_url, logo, simple: voir generer_pngs.
        chemin_pdf: fichier PDF de sortie.
        lignes, colonnes: disposition de la grille.
        marge_mm: marge intérieure de chaque cellule, en millimètres.

    Returns:
        Le nombre de pages générées.
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    def lecteur_png(im) -> ImageReader:
        # Passer un PNG (et non l'objet PIL) : préserve la couleur et évite
        # le recours au codec JPEG (absent de certains builds Pillow).
        buf = BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)

    page_w, page_h = A4
    cw, ch = page_w / colonnes, page_h / lignes
    marge = marge_mm * mm
    par_page = lignes * colonnes

    chemin_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(chemin_pdf), pagesize=A4)

    pages = 0
    for i in range(0, len(exemplaires), par_page):
        for j, ex in enumerate(exemplaires[i:i + par_page]):
            url = url_fiche(base_url, ex["id_exemplaire"])
            label = image_qr_nu(url) if simple else image_etiquette(url, ex, logo)
            iw, ih = label.size
            # Position dans la grille (colonne, ligne) à partir de l'indice j.
            col, row = j % colonnes, j // colonnes
            # Échelle pour tenir dans la cellule (marges déduites), proportions
            # conservées.
            avail_w, avail_h = cw - 2 * marge, ch - 2 * marge
            scale = min(avail_w / iw, avail_h / ih)
            w, h = iw * scale, ih * scale
            # Centrage. NB : origine reportlab en bas-gauche, d'où le calcul de y
            # depuis le haut de page (page_h).
            x = col * cw + (cw - w) / 2
            y = page_h - (row + 1) * ch + (ch - h) / 2
            c.drawImage(lecteur_png(label), x, y, w, h, preserveAspectRatio=True)
        c.showPage()
        pages += 1

    c.save()
    return pages


def _parse_grille(valeur: str) -> tuple[int, int]:
    """Convertit '8x3' en (8 lignes, 3 colonnes). Erreur claire si format invalide."""
    try:
        lignes, colonnes = (int(x) for x in valeur.lower().split("x"))
        if lignes < 1 or colonnes < 1:
            raise ValueError
        return lignes, colonnes
    except ValueError:
        raise SystemExit("ERREUR : --grille attend un format 'lignesxcolonnes', ex. 8x3.")


def main() -> None:
    """
    Point d'entrée CLI : options, chargement logo + exemplaires, génération des
    PNG (et de la planche si --planche).
    """
    p = argparse.ArgumentParser(description="Génère les étiquettes QR des exemplaires.")
    p.add_argument("--base-url", default=os.getenv("BASE_URL"),
                   help="URL de base (défaut : BASE_URL du .env).")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Dossier de sortie.")
    p.add_argument("--logo", type=Path, help="Image du logo (sinon logo_djplm.jpg).")
    p.add_argument("--planche", action="store_true", help="Planche PDF A4 à imprimer.")
    p.add_argument("--grille", default="8x2",
                   help="Disposition planche 'lignesxcolonnes' (défaut 8x2, paysage).")
    p.add_argument("--simple", action="store_true", help="QR nu, sans décor.")
    p.add_argument("--limit", type=int, help="Limiter le nombre d'exemplaires (tests).")
    args = p.parse_args()

    if not args.base_url:
        raise SystemExit("ERREUR : aucune URL de base. Renseigner BASE_URL dans "
                         ".env ou passer --base-url. L'URL encodée est définitive.")

    # Logo : si --logo est passé, il doit exister ; sinon on tente logo_djplm.jpg.
    if args.logo and not args.logo.exists():
        raise SystemExit(f"Logo introuvable : {args.logo}")
    logo = charger_logo(args.logo)

    exemplaires = charger_exemplaires(args.limit)
    if not exemplaires:
        raise SystemExit("Aucun exemplaire en base. Lancer d'abord : "
                         "python -m scripts.import_csv <catalogue.csv>")

    print(f"URL de base : {args.base_url}")
    if any(s in args.base_url for s in ("example", "localhost", "trycloudflare", "ngrok")):
        print("  (URL de TEST — ne pas utiliser pour le tirage définitif.)")
    print(f"Logo : {'fourni' if logo else 'PLACEHOLDER (logo_djplm.jpg absent)'}")

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
