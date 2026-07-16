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

from app import auth, modules, services
from app.config import FORMATION_URL, MODE_FORMATION, NOM_ASSOCIATION

# Dossier contenant les gabarits HTML (app/templates/).
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Nom de l'association, personnalisable via NOM_ASSOCIATION (.env) — voir
# app/config.py. Disponible dans TOUS les gabarits : {{ nom_association }}.
templates.env.globals["nom_association"] = NOM_ASSOCIATION

# Mode formation (voir app/config.py + docs/mode-formation.md) : bandeau et
# filigrane dans base.html, condition du bouton de réinitialisation en admin.
templates.env.globals["mode_formation"] = MODE_FORMATION
# URL de l'instance de formation (lien admin côté PRODUCTION), None si absente.
templates.env.globals["formation_url"] = FORMATION_URL

# Fonction disponible dans tous les gabarits : `est_benevole(request)` indique si
# l'appareil peut accéder aux écrans bénévole — jeton bénévole activé OU session
# admin ouverte. Sert à n'afficher le menu bénévole qu'aux personnes autorisées.
templates.env.globals["est_benevole"] = auth.peut_ecrire

# Indique si un module est visible pour ce visiteur (tient compte de son état
# et de l'accès bénévole). Usage dans les gabarits :
#   {% if module_visible(request, "tournois") %} ... {% endif %}
templates.env.globals["module_visible"] = modules.module_visible

# Accord singulier/pluriel disponible dans TOUS les gabarits, sans import :
# {{ n }} {{ pluriel(n, 'jeu', 'jeux') }} -- remplace les pluriels parenthésés
# type « jeu(x) », « prêt(s) » (docs/idees-ux.md Q2).
templates.env.globals["pluriel"] = services.pluriel

# Filtre d'affichage : un horodatage UTC ISO -> heure locale 'JJ/MM/AAAA HH:MM'.
# Utilisé par les gabarits des tournois ({{ t.date_heure | dt_local }}).
templates.env.filters["dt_local"] = services.format_local

# Filtre d'affichage : un horodatage UTC ISO -> heure locale courte 'HH:MM'.
# Utilisé par les gabarits du planning ({{ c.debut | heure_local }}).
templates.env.filters["heure_local"] = (
    lambda iso: (services.format_local(iso).split(" ")[-1] if iso else "")
)


def _dt_input(iso_utc: str | None) -> str:
    """UTC ISO -> 'AAAA-MM-JJTHH:MM' (heure locale) pour un input datetime-local."""
    if not iso_utc:
        return ""
    from datetime import datetime

    from app.services import FUSEAU_LOCAL
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return ""
    return dt.astimezone(FUSEAU_LOCAL).strftime("%Y-%m-%dT%H:%M")


# Filtre : pré-remplir un champ <input type="datetime-local"> depuis l'UTC ISO.
templates.env.filters["dt_input"] = _dt_input

# Version du CSS pour « casser » le cache navigateur : la date de modification du
# fichier style.css. Recalculée au démarrage (uvicorn --reload redémarre quand le
# fichier change), donc le navigateur recharge automatiquement la bonne version.
_CSS = BASE_DIR / "static" / "css" / "style.css"
templates.env.globals["static_v"] = int(_CSS.stat().st_mtime) if _CSS.exists() else 0
