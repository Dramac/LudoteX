"""
Point d'entrée de l'application FastAPI.

Lancement (développement) :
    uvicorn app.main:app --reload

Enregistre les routeurs (catalogue public en lecture, prêt/retour en écriture),
sert les fichiers statiques et expose un point de santé.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routes import catalogue, pret, scanner, stats

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Prêt de jeux",
    description="Système de prêt de jeux de société par QR code (brique de prêt).",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(catalogue.router)
app.include_router(pret.router)
app.include_router(scanner.router)
app.include_router(stats.router)


@app.get("/")
def racine():
    """Redirige vers le catalogue public."""
    return RedirectResponse(url="/catalogue")


@app.get("/sante", tags=["meta"])
def sante():
    """Point de santé minimal."""
    return {"statut": "ok"}
