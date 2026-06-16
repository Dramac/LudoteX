"""
Point d'entrée de l'application FastAPI.

Lancement (développement) :
    uvicorn app.main:app --reload

Enregistre les routeurs (catalogue public en lecture, prêt/retour en écriture),
sert les fichiers statiques et expose un point de santé.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth
from app.routes import acces, catalogue, pret, scanner, stats
from app.templating import templates

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
app.include_router(acces.router)


@app.exception_handler(StarletteHTTPException)
async def gestion_http(request, exc: StarletteHTTPException):
    """Rend une page HTML conviviale pour les accès refusés (403)."""
    if exc.status_code == 403:
        return templates.TemplateResponse(
            request, "acces_refuse.html", {"motif": "reserve"}, status_code=403
        )
    return await http_exception_handler(request, exc)


if auth.jeton_configure() is None:
    logging.getLogger("uvicorn.error").warning(
        "PRET_TOKEN non défini : les écrans bénévole (/pret, /scanner) sont "
        "OUVERTS. Définir PRET_TOKEN dans .env pour la production."
    )


@app.get("/")
def racine():
    """Redirige vers le catalogue public."""
    return RedirectResponse(url="/catalogue")


@app.get("/sante", tags=["meta"])
def sante():
    """Point de santé minimal."""
    return {"statut": "ok"}
