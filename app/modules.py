"""
Gestion de la visibilité des FONCTIONNALITÉS (modules) de l'application.

Trois états par module :
  "tous"      → accessible à tous les visiteurs (comportement par défaut)
  "benevoles" → accessible uniquement aux bénévoles (jeton ou session admin)
  "desactive" → routes bloquées, liens masqués dans la navigation

Les états sont stockés dans la table `parametres` (clé : `module_<nom>`).
L'état par défaut "tous" s'applique si aucune ligne n'existe en base, ce qui
assure la compatibilité avec les bases existantes sans migration.

USAGE DANS main.py
------------------
    from app.modules import garde_module, ModuleDesactive

    # Exception handler (page conviviale quand un module est désactivé)
    @app.exception_handler(ModuleDesactive)
    async def gestion_module_desactive(request, exc):
        ...

    # Bloquer toutes les routes d'un routeur
    app.include_router(tournoi_routes.router, dependencies=[garde_module("tournois")])

USAGE DANS LES GABARITS
-----------------------
    {{ module_visible(request, "tournois") }}   -> True/False
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app import auth, services
from app.db import get_connection

# ---------------------------------------------------------------------------
# Catalogue des modules configurables
# ---------------------------------------------------------------------------
# Ordre d'affichage dans la page d'administration.
MODULES: dict[str, dict] = {
    "tournois": {
        "label": "Tournois",
        "description": "Gestion et inscription aux tournois de l'événement",
        "url": "/tournois",
    },
    "stats": {
        "label": "Statistiques",
        "description": "Statistiques et historique détaillé des prêts",
        "url": "/stats",
    },
    "planning": {
        "label": "Planning bénévoles",
        "description": "Collecte des souhaits et planning de l'équipe",
        "url": "/planning",
    },
    "live": {
        "label": "Écran de salle",
        "description": "Tableau de bord temps réel (projecteur / TV)",
        "url": "/live",
    },
    "apropos": {
        "label": "À propos",
        "description": "Informations sur l'application et l'association",
        "url": "/apropos",
    },
}

ETATS_VALIDES = ("tous", "benevoles", "discret", "desactive")
ETAT_DEFAUT = "tous"

LABELS_ETATS = {
    "tous":      "Visible par tous",
    "benevoles": "Bénévoles uniquement",
    "discret":   "Accessible, lien masqué",
    "desactive": "Désactivé",
}

DESCRIPTIONS_ETATS = {
    "tous": (
        "Le module apparaît dans les menus et son URL fonctionne "
        "pour tous les visiteurs."
    ),
    "benevoles": (
        "Le module n'est visible et accessible qu'aux bénévoles. "
        "Un visiteur qui taperait l'URL directement serait bloqué."
    ),
    "discret": (
        "L'URL fonctionne pour tout le monde, mais le lien "
        "n'apparaît pas dans les menus des visiteurs. "
        "Pratique pour l'écran de salle : on l'ouvre sur le projecteur "
        "via son adresse sans l'afficher dans la navigation."
    ),
    "desactive": (
        "Le module est complètement désactivé. "
        "L'URL renvoie une page « module indisponible »."
    ),
}


# ---------------------------------------------------------------------------
# Exception levée quand une route appartenant à un module désactivé est visitée
# ---------------------------------------------------------------------------
class ModuleDesactive(Exception):
    """
    Levée par `garde_module` quand le module est en état 'desactive'.
    Attrapée dans app/main.py → page conviviale « module désactivé ».
    """
    def __init__(self, nom: str):
        self.nom = nom
        super().__init__(f"Module désactivé : {nom}")


# ---------------------------------------------------------------------------
# Lecture / écriture des états
# ---------------------------------------------------------------------------
def lire_etat_module(conn, nom: str) -> str:
    """Renvoie l'état d'un module ('tous', 'benevoles' ou 'desactive')."""
    return services.lire_parametre(conn, f"module_{nom}", ETAT_DEFAUT)


def ecrire_etat_module(conn, nom: str, etat: str) -> None:
    """Enregistre l'état d'un module dans `parametres`."""
    if etat not in ETATS_VALIDES:
        raise ValueError(f"État invalide : {etat!r}")
    services.ecrire_parametre(conn, f"module_{nom}", etat)


def lire_etats_modules(conn) -> dict[str, str]:
    """Renvoie un dict {nom_module: état} pour tous les modules configurables."""
    return {nom: lire_etat_module(conn, nom) for nom in MODULES}


# ---------------------------------------------------------------------------
# Dépendance FastAPI : garde-module
# ---------------------------------------------------------------------------
def garde_module(nom: str):
    """
    Fabrique une dépendance FastAPI à passer en ``dependencies=[...]``
    lors de ``app.include_router(...)`` ou d'une route individuelle.

    Comportement selon l'état du module :
      - "desactive" → lève ModuleDesactive (→ gestionnaire → page conviviale)
      - "benevoles" + visiteur sans jeton → lève HTTPException(403)
      - "tous"      → laisse passer
    """
    async def _dep(request: Request):
        conn = get_connection()
        try:
            etat = lire_etat_module(conn, nom)
        finally:
            conn.close()
        if etat == "desactive":
            raise ModuleDesactive(nom)
        if etat == "benevoles" and not auth.peut_ecrire(request):
            raise HTTPException(status_code=403)

    return Depends(_dep)


# ---------------------------------------------------------------------------
# Visibilité dans les gabarits Jinja2
# ---------------------------------------------------------------------------
def module_visible(request: Request, nom: str) -> bool:
    """
    Indique si un module doit apparaître dans la navigation pour ce visiteur.

    Enregistré comme global Jinja2 dans app/templating.py :
        templates.env.globals["module_visible"] = module_visible

    Utilisation dans un gabarit :
        {% if module_visible(request, "tournois") %}
          <a href="/tournois">Tournois</a>
        {% endif %}
    """
    conn = get_connection()
    try:
        etat = lire_etat_module(conn, nom)
    finally:
        conn.close()
    if etat == "desactive":
        return False
    if etat in ("benevoles", "discret"):
        return auth.peut_ecrire(request)
    return True  # "tous"
