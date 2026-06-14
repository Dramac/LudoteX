"""
Point d'entrée de l'application FastAPI.

Lancement (développement) :
    uvicorn app.main:app --reload

À ce stade, l'application se contente d'enregistrer les routeurs (catalogue
public et prêt/retour, encore en squelettes) et d'exposer un point de santé.
La logique métier sera ajoutée ensuite (voir docs/specification.md §5).
"""

from fastapi import FastAPI

from app.routes import catalogue, pret

app = FastAPI(
    title="Prêt de jeux",
    description="Système de prêt de jeux de société par QR code (brique de prêt).",
    version="0.1.0",
)

# Catalogue public (lecture seule) et prêt/retour (écriture protégée).
app.include_router(catalogue.router)
app.include_router(pret.router)


@app.get("/sante", tags=["meta"])
def sante():
    """Point de santé minimal pour vérifier que l'application répond."""
    return {"statut": "ok"}
