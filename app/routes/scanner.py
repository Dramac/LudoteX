"""
Route du SCANNER caméra embarqué (outil bénévole).

Sert la page `/scanner` qui active la caméra et décode les QR côté navigateur
(toute la logique caméra est dans static/js/scanner.js). Quand un QR est lu, le
JS extrait l'id et redirige vers l'écran prêt/retour `/pret/<id>`.

Le serveur n'a presque rien à faire ici : il rend juste le gabarit. La page est
protégée par le jeton bénévole, comme /pret.
"""

from fastapi import APIRouter, Depends, Request

from app.auth import exiger_jeton
from app.templating import templates

router = APIRouter(tags=["scanner"])


@router.get("/scanner")
def scanner(request: Request, _=Depends(exiger_jeton)):
    """
    Affiche la page du scanner caméra (protégée par jeton).

    Args:
        request: requête (nécessaire à Jinja2).
        _: dépendance d'authentification (valeur ignorée).

    Returns:
        La page scanner.html.
    """
    return templates.TemplateResponse(request, "scanner.html", {})
