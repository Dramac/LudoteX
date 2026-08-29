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
from starlette.requests import Request

from app import admin_auth, auth, modules, services
from app.config import FORMATION_URL, MODE_FORMATION
from app.version import APP_VERSION

# Dossier contenant les gabarits HTML (app/templates/).
BASE_DIR = Path(__file__).resolve().parent


def _identite(request: Request) -> dict:
    """
    Valeurs calculées À CHAQUE RENDU et injectées dans tous les gabarits.

    POURQUOI UN CONTEXT PROCESSOR ET NON UN GLOBAL JINJA
    ----------------------------------------------------
    `nom_association` était un global posé à l'import, depuis une constante
    d'app/config.py : sa valeur était donc figée au démarrage du serveur. Le
    nom se règle désormais depuis /admin/identite et vit en base (voir
    app/services.py, section « Identité de l'ASSOCIATION ») : il doit être relu
    à chaque requête, sans quoi une modification n'apparaîtrait qu'au
    redémarrage suivant.

    Un context processor est ce qui permet ce changement SANS toucher aux
    gabarits : `{{ nom_association }}` y reste écrit tel quel — il y est lu dans
    66 fichiers — au lieu de devenir un appel `{{ nom_association() }}`.

    `services.nom_association()` ne lève jamais et ouvre puis referme sa propre
    connexion : cette fonction est appelée y compris pendant le rendu de la
    page d'erreur 500, où la base peut précisément être en cause.
    """
    return {"nom_association": services.nom_association()}


templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates"), context_processors=[_identite]
)

# Mode formation (voir app/config.py + docs/mode-formation.md) : bandeau et
# filigrane dans base.html, condition du bouton de réinitialisation en admin.
templates.env.globals["mode_formation"] = MODE_FORMATION
# URL de l'instance de formation (lien admin côté PRODUCTION), None si absente.
templates.env.globals["formation_url"] = FORMATION_URL

# Fonction disponible dans tous les gabarits : `est_benevole(request)` indique si
# l'appareil peut accéder aux écrans bénévole — jeton bénévole activé OU session
# admin ouverte. Sert à n'afficher le menu bénévole qu'aux personnes autorisées.
templates.env.globals["est_benevole"] = auth.peut_ecrire

# Fonction disponible dans tous les gabarits : `est_admin(request)` indique si
# une SESSION ADMIN (mot de passe, distincte du jeton bénévole) est active sur
# cet appareil. Sert à afficher un lien de retour vers /admin dans le menu du
# bandeau, pour qu'un administrateur connecté puisse y revenir depuis
# n'importe quelle page sans repasser par l'URL /admin à la main.
templates.env.globals["est_admin"] = admin_auth.admin_connecte

# Indique si un module est visible pour ce visiteur (tient compte de son état
# et de l'accès bénévole). Usage dans les gabarits :
#   {% if module_visible(request, "tournois") %} ... {% endif %}
templates.env.globals["module_visible"] = modules.module_visible

# Indique si l'emplacement de rangement doit être affiché à ce visiteur sur le
# catalogue / la fiche publique (3 niveaux réglables en admin, voir
# docs/conception-rangement.md §7). Usage : {% if rangement_visible(request) %}
templates.env.globals["rangement_visible"] = services.rangement_visible

# État du MODE RANGEMENT de cet appareil (cookie de 12 h), pour le bandeau
# global de base.html : tant qu'il est actif, scanner un QR range la boîte au
# lieu d'ouvrir l'écran de prêt. Le mode n'était visible que sur /scanner, ce
# qui en faisait le seul mode caché de l'application (fiche B1 de
# docs/audit-ux-2026-07-18.md). Usage : {% set rgt = rangement_actif(request) %}
templates.env.globals["rangement_actif"] = services.rangement_actif

# Nom de l'événement en cours (« Festival du Jeu 2026 »), réglé depuis
# /admin/evenement — à ne pas confondre avec `nom_association`, qui vient du
# .env et ne change pas d'une édition à l'autre. Vaut None tant qu'il n'a pas
# été renseigné : les gabarits doivent donc TOUJOURS le tester avant de
# l'afficher ({% if nom_evenement() %}), pour ne jamais montrer un rappel vide.
# Ouvre sa propre connexion, comme `rangement_visible`/`rangement_actif` (seule
# la requête est disponible dans un gabarit) — mais sans paramètre : le nom ne
# dépend pas du visiteur.
templates.env.globals["nom_evenement"] = services.nom_evenement

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

# Numéro de version de l'application, rappelé dans le pied de page de toutes
# les pages (à côté de la licence). Constante importée d'`app/version.py`, le
# porteur canonique du numéro — jamais recopié en dur dans un gabarit.
templates.env.globals["app_version"] = APP_VERSION

# Version du CSS pour « casser » le cache navigateur : la date de modification du
# fichier style.css. Recalculée au démarrage (uvicorn --reload redémarre quand le
# fichier change), donc le navigateur recharge automatiquement la bonne version.
_CSS = BASE_DIR / "static" / "css" / "style.css"
templates.env.globals["static_v"] = int(_CSS.stat().st_mtime) if _CSS.exists() else 0
