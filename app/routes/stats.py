"""
Route des STATISTIQUES (post-événement) — page publique.

Agrégations sans donnée personnelle (spec §7) : total des prêts, palmarès des
jeux les plus / les moins prêtés (par titre, jamais-sortis inclus), et prêts par
heure. Tout le calcul est dans app.services ; la route assemble et rend.

Double vue du palmarès via le paramètre `?tri=` :
    tri=total       -> classement par nombre brut de prêts (défaut).
    tri=exemplaire  -> classement par prêts rapportés au nombre d'exemplaires.
"""

from fastapi import APIRouter, Request

from app import services
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(request: Request, tri: str = "total"):
    """
    Page des statistiques de prêt.

    Args:
        request: requête (nécessaire à Jinja2).
        tri: "total" (défaut) ou "exemplaire" — choisit la métrique des
            palmarès. Toute autre valeur retombe sur "total" (normalisation
            défensive : `metrique` est ensuite injecté dans le SQL).

    Returns:
        La page stats.html avec les indicateurs, les deux palmarès (plus/moins
        prêtés) et l'histogramme horaire.
    """
    # Normalisation : on n'accepte que deux valeurs connues (jamais l'entrée brute
    # directement dans le SQL de services.palmares).
    metrique = "exemplaire" if tri == "exemplaire" else "total"
    conn = get_connection()
    try:
        globales = services.stats_globales(conn)
        plus = services.palmares(conn, sens="desc", metrique=metrique)
        moins = services.palmares(conn, sens="asc", metrique=metrique)
        par_heure = services.prets_par_heure(conn)
    finally:
        conn.close()

    # max_heure : sert au gabarit à dimensionner les barres de l'histogramme
    # (largeur = n / max_heure). `default=0` évite une erreur si aucune donnée.
    max_heure = max((h["n"] for h in par_heure), default=0)
    return templates.TemplateResponse(
        request,
        "stats.html",
        {"g": globales, "plus": plus, "moins": moins, "par_heure": par_heure,
         "max_heure": max_heure, "metrique": metrique},
    )
