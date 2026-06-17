"""
Activation de l'accès bénévole — pose le cookie de jeton (voir spec §8).

Le lien `/acces?jeton=<JETON>` est distribué aux bénévoles via le canal interne.
En l'ouvrant, l'appareil mémorise le jeton dans un cookie (validité 3 jours) et
peut ensuite accéder à /pret et /scanner. Passé ce délai, on rouvre le lien.
Rotation du jeton = changer `PRET_TOKEN` (les anciens cookies cessent d'être
valides).

Sécurité : limitation de débit par IP (anti-force brute) et comparaison du jeton
en temps constant. Le cookie est HttpOnly (inaccessible au JS), SameSite=Lax, et
Secure dès que la connexion est en HTTPS.
"""

import os
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app import auth
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["acces"])

# Durée de validité du cookie d'accès bénévole : 3 jours (en secondes).
DUREE_COOKIE = 60 * 60 * 24 * 3


@router.get("/acces")
def acces(request: Request, jeton: str = ""):
    """
    Vérifie le jeton fourni et, s'il est correct, pose le cookie d'accès.

    Déroulé :
    1. Limitation de débit par IP : au-delà de RATE_LIMIT_PER_MINUTE tentatives
       par minute, on répond 429 (anti-force brute).
    2. Si un jeton est configuré ET correspond (comparaison temps constant) :
       on pose le cookie et on redirige vers /scanner (303 = "See Other").
    3. Sinon : page « accès réservé » avec un motif explicatif :
       - "ouvert"   : aucun jeton requis sur cette installation (mode dev).
       - "invalide" : le lien/jeton est erroné.

    Args:
        request: requête (pour l'IP, le schéma http/https et le rendu).
        jeton: valeur du paramètre `?jeton=` (vide par défaut).

    Returns:
        Une redirection 303 vers /scanner (succès), ou la page acces_refuse.html
        (429 si trop de tentatives, 403 sinon).
    """
    ip = request.client.host if request.client else "inconnu"
    limite = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    if auth.trop_de_tentatives(ip, limite):
        return templates.TemplateResponse(
            request, "acces_refuse.html", {"motif": "trop"}, status_code=429
        )

    conn = get_connection()
    try:
        attendu = auth.jeton_actuel(conn)
    finally:
        conn.close()
    if attendu and secrets.compare_digest(jeton, attendu):
        # 303 force le navigateur à faire un GET sur /scanner après l'activation.
        reponse = RedirectResponse("/scanner", status_code=303)
        reponse.set_cookie(
            auth.COOKIE_NAME, jeton,
            max_age=DUREE_COOKIE, httponly=True, samesite="lax",
            secure=(request.url.scheme == "https"),
        )
        return reponse

    # Échec : on distingue le mode ouvert (pas de jeton configuré) du jeton erroné.
    motif = "ouvert" if attendu is None else "invalide"
    return templates.TemplateResponse(
        request, "acces_refuse.html", {"motif": motif}, status_code=403
    )
