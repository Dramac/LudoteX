"""
Route du SCANNER caméra embarqué (outil bénévole).

Affiche la page qui active la caméra et décode les QR (voir
static/js/scanner.js). Chaque QR ouvre l'écran prêt/retour /pret/<id>.

Sécurité : comme les autres écrans bénévole, l'accès sera protégé par le jeton
à l'étape 9 (réutilise le placeholder `exiger_jeton`).
"""

from fastapi import APIRouter, Depends, Request

from app.routes.pret import exiger_jeton
from app.templating import templates

router = APIRouter(tags=["scanner"])


@router.get("/scanner")
def scanner(request: Request, _=Depends(exiger_jeton)):
    return templates.TemplateResponse(request, "scanner.html", {})
