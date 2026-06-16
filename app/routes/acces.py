"""
Activation de l'accès bénévole — pose le cookie de jeton (voir spec §8).

Le lien `/acces?jeton=<JETON>` est distribué une fois par an aux bénévoles ;
l'appareil mémorise le jeton (cookie, validité 3 jours) et peut ensuite accéder
à /pret et /scanner. Au-delà, il faut rouvrir le lien d'activation. Rotation du
jeton = changer `PRET_TOKEN` (les anciens cookies cessent d'être valides).
"""

import os

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app import auth
from app.templating import templates

router = APIRouter(tags=["acces"])

# Durée de validité du cookie d'accès bénévole : 3 jours.
DUREE_COOKIE = 60 * 60 * 24 * 3


@router.get("/acces")
def acces(request: Request, jeton: str = ""):
    ip = request.client.host if request.client else "inconnu"
    limite = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    if auth.trop_de_tentatives(ip, limite):
        return templates.TemplateResponse(
            request, "acces_refuse.html", {"motif": "trop"}, status_code=429
        )

    attendu = auth.jeton_configure()
    if attendu and secrets.compare_digest(jeton, attendu):
        reponse = RedirectResponse("/scanner", status_code=303)
        reponse.set_cookie(
            auth.COOKIE_NAME, jeton,
            max_age=DUREE_COOKIE, httponly=True, samesite="lax",
            secure=(request.url.scheme == "https"),
        )
        return reponse

    motif = "ouvert" if attendu is None else "invalide"
    return templates.TemplateResponse(
        request, "acces_refuse.html", {"motif": motif}, status_code=403
    )
