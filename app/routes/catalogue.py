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


def _entier_ou_none(valeur: str | None) -> int | None:
    """Convertit une valeur de formulaire en entier, ou None si vide/invalide."""
    if valeur is None or valeur.strip() == "":
        return None
    try:
        return int(valeur)
    except ValueError:
        return None


@router.get("/catalogue")
def catalogue(request: Request, categorie: str | None = None, q: str | None = None,
              age: str | None = None, joueurs: str | None = None):
    """Catalogue public avec recherche et filtres combinés (catégorie, âge, joueurs)."""
    q = (q or "").strip() or None
    age_i = _entier_ou_none(age)
    joueurs_i = _entier_ou_none(joueurs)

    conn = get_connection()
    try:
        categories = services.lister_categories(conn)
        ages = services.ages_disponibles(conn)
        max_j = services.max_joueurs(conn)
        # On ignore une catégorie inconnue (filtre réinitialisé).
        filtre_cat = categorie if categorie in categories else None
        jeux = services.lister_catalogue(conn, filtre_cat, q, age_i, joueurs_i)
    finally:
        conn.close()

    actifs = bool(q or filtre_cat or age_i is not None or joueurs_i is not None)
    return templates.TemplateResponse(
        request,
        "catalogue.html",
        {"jeux": jeux, "categories": categories, "ages": ages, "max_joueurs": max_j,
         "filtre": filtre_cat, "q": q, "age": age_i, "joueurs": joueurs_i,
         "filtres_actifs": actifs},
    )
