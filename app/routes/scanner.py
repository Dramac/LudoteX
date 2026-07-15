"""
Route du SCANNER caméra embarqué (outil bénévole).

Sert la page `/scanner` qui active la caméra et décode les QR côté navigateur
(toute la logique caméra est dans static/js/scanner.js). Quand un QR est lu, le
JS extrait l'id et redirige vers l'écran prêt/retour `/pret/<id>`.

En secours (étiquette arrachée, QR illisible, caméra capricieuse), un petit
formulaire sous la caméra permet de TAPER le code de la boîte (`id_exemplaire`)
et d'arriver directement sur `/pret/<id>` — sans repasser par le catalogue.

Ces deux surfaces sont protégées par le jeton bénévole, comme /pret.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app import services
from app.auth import exiger_jeton
from app.db import get_connection
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


@router.get("/scanner/saisie")
def saisie_manuelle(request: Request, code: str = "", _=Depends(exiger_jeton)):
    """
    Saisie manuelle de secours : le bénévole tape le code de la boîte
    (`id_exemplaire`) et est redirigé vers l'écran prêt/retour.

    Jamais bloquant : un code vide ou inconnu ré-affiche le scanner avec un
    message clair et le champ prêt à resservir (pas d'erreur brute).

    `id_exemplaire` est du TEXT : on se contente de retirer les espaces autour
    de la saisie (les zéros de tête, ex. "00472", sont préservés — jamais
    d'interprétation en entier).

    Args:
        request: requête (nécessaire à Jinja2).
        code: code de la boîte tapé dans le formulaire (paramètre GET).
        _: dépendance d'authentification (valeur ignorée).

    Returns:
        Une redirection 303 vers /pret/<id> si le code existe, sinon la page
        scanner.html ré-affichée avec un message et la valeur saisie.
    """
    id_exemplaire = (code or "").strip()

    if not id_exemplaire:
        return templates.TemplateResponse(
            request, "scanner.html",
            {"erreur": "Veuillez saisir un code.", "code_saisi": ""},
        )

    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
    finally:
        conn.close()

    if info is None:
        return templates.TemplateResponse(
            request, "scanner.html",
            {"erreur": f"Aucune boîte ne porte le code « {id_exemplaire} ». "
                       "Vérifiez et réessayez.",
             "code_saisi": id_exemplaire},
        )

    return RedirectResponse(f"/pret/{id_exemplaire}", status_code=303)
