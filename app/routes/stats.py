"""
Route des STATISTIQUES (post-événement).

Agrégations publiques sans donnée personnelle (voir spec §7) : total des prêts,
palmarès des jeux les plus / les moins prêtés (par titre, zéros inclus), et
prêts par heure. Double vue du palmarès via ?tri=total|exemplaire.
"""

from fastapi import APIRouter, Request

from app import services
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(request: Request, tri: str = "total"):
    metrique = "exemplaire" if tri == "exemplaire" else "total"
    conn = get_connection()
    try:
        globales = services.stats_globales(conn)
        plus = services.palmares(conn, sens="desc", metrique=metrique)
        moins = services.palmares(conn, sens="asc", metrique=metrique)
        par_heure = services.prets_par_heure(conn)
    finally:
        conn.close()

    max_heure = max((h["n"] for h in par_heure), default=0)
    return templates.TemplateResponse(
        request,
        "stats.html",
        {"g": globales, "plus": plus, "moins": moins, "par_heure": par_heure,
         "max_heure": max_heure, "metrique": metrique},
    )
