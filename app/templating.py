"""
Objet Jinja2 partagé pour le rendu des pages HTML.

POURQUOI UN MODULE DÉDIÉ
------------------------
`Jinja2Templates` doit être instancié une seule fois et réutilisé par toutes les
routes. Le placer ici (plutôt que dans app/main.py) évite les imports circulaires
(les routes importent `templates` sans dépendre de `main`, et `main` importe les
routes).

USAGE DANS UNE ROUTE
--------------------
    from app.templating import templates

    @router.get("/exemple")
    def exemple(request: Request):
        # Signature : (request, nom_du_template, contexte, status_code=...)
        return templates.TemplateResponse(request, "exemple.html", {"cle": valeur})

Les gabarits se trouvent dans app/templates/ ; tous héritent de base.html.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

# Dossier contenant les gabarits HTML (app/templates/).
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
