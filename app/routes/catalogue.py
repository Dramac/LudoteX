"""
Routes du CATALOGUE PUBLIC — consultation en lecture seule.

Accès public, SANS donnée personnelle, SANS bouton d'action (voir spec §5.2).
La fiche `/jeu/<id_exemplaire>` est l'URL encodée dans le QR.

À venir (étape 7) : GET /catalogue (liste, filtre par catégorie).
"""

from fastapi import APIRouter, Request

from app import services
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["catalogue"])


@router.get("/jeu/{id_exemplaire}")
def fiche(request: Request, id_exemplaire: str):
    """Fiche d'un exemplaire (lecture seule)."""
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        total = disponible = 0
        if info is not None:
            total, disponible = services.dispo_par_titre(conn, info["reference_titre"])
    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "fiche.html",
        {"id_exemplaire": id_exemplaire, "info": info,
         "total": total, "disponible": disponible},
        status_code=200 if info else 404,
    )


@router.get("/catalogue")
def catalogue(request: Request, categorie: str | None = None):
    """Liste publique des titres (vrac ou filtrée par catégorie), avec dispo."""
    conn = get_connection()
    try:
        categories = services.lister_categories(conn)
        # On ignore une catégorie inconnue (filtre réinitialisé).
        filtre = categorie if categorie in categories else None
        jeux = services.lister_catalogue(conn, filtre)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "catalogue.html",
        {"jeux": jeux, "categories": categories, "filtre": filtre},
    )
