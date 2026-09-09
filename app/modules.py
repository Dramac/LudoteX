"""
Gestion de la visibilité des FONCTIONNALITÉS (modules) de l'application.

Quatre états par module :
  "tous"      → accessible à tous les visiteurs (défaut de la plupart)
  "benevoles" → accessible uniquement aux bénévoles (jeton ou session admin)
  "discret"   → URL ouverte à tous, lien masqué dans la navigation
  "desactive" → routes bloquées, liens masqués dans la navigation

Les états sont stockés dans la table `parametres` (clé : `module_<nom>`).
L'état par défaut s'applique si aucune ligne n'existe en base, ce qui assure
la compatibilité avec les bases existantes sans migration. C'est "tous" pour
tous les modules SAUF le carnet de maintenance, qui vaut "benevoles" (voir
`etat_defaut_module` et le drapeau `jamais_public` plus bas).

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
    "programme": {
        "label": "Programme",
        "description": "Animations, ateliers et temps forts hors tournoi",
        "url": "/programme",
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
    # Carnet de maintenance ouvert aux bénévoles (lot agora-5). Deux clés
    # supplémentaires, propres à ce module :
    #
    # - `jamais_public` : cette liste nomme des boîtes abîmées et porte le
    #   seul champ de saisie libre de l'application de prêt
    #   (docs/conception-signalements.md §3 et §7). L'état "tous" ne lui est
    #   donc PAS proposé — l'écran d'administration masque la case, et
    #   `ecrire_etat_module` refuse la valeur même postée à la main. Sans ça
    #   l'interface annoncerait un état que la route refuserait de toute
    #   façon : la route porte son `Depends(exiger_jeton)` en propre, la
    #   visibilité de module n'étant pas une autorisation d'accès.
    # - `etat_defaut` : "benevoles" plutôt que le "tous" global, pour que les
    #   bases existantes — qui n'ont aucune ligne `module_maintenance` —
    #   n'ouvrent pas ce carnet au premier visiteur venu.
    "maintenance": {
        "label": "Carnet de maintenance",
        "description": "Boîtes signalées par les bénévoles : ce qu'il y a à réparer",
        "url": "/maintenance",
        "jamais_public": True,
        "etat_defaut": "benevoles",
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
def etat_defaut_module(nom: str) -> str:
    """
    État appliqué tant qu'aucune ligne `module_<nom>` n'existe en base.

    `ETAT_DEFAUT` ("tous") pour tous les modules, sauf ceux qui déclarent leur
    propre `etat_defaut` — le carnet de maintenance, qui ne doit jamais
    s'ouvrir tout seul aux visiteurs sur une base d'avant ce module.
    """
    return MODULES.get(nom, {}).get("etat_defaut", ETAT_DEFAUT)


def etats_valides_module(nom: str) -> tuple[str, ...]:
    """
    États réellement proposables pour ce module. Retire "tous" à ceux qui
    portent `jamais_public` : proposer une case que la route refuserait
    d'honorer serait une interface mensongère.
    """
    if MODULES.get(nom, {}).get("jamais_public"):
        return tuple(e for e in ETATS_VALIDES if e != "tous")
    return ETATS_VALIDES


def lire_etat_module(conn, nom: str) -> str:
    """Renvoie l'état d'un module (l'un des quatre d'`ETATS_VALIDES`)."""
    return services.lire_parametre(conn, f"module_{nom}", etat_defaut_module(nom))


def ecrire_etat_module(conn, nom: str, etat: str) -> None:
    """
    Enregistre l'état d'un module dans `parametres`.

    Refuse un état que ce module n'accepte pas — "tous" sur un module
    `jamais_public` : le garde-fou est ICI, pas seulement dans le gabarit,
    pour qu'un formulaire forgé ne puisse pas rendre le carnet public.
    """
    if etat not in etats_valides_module(nom):
        raise ValueError(f"État invalide pour {nom} : {etat!r}")
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
      - "tous" / "discret" → laisse passer

    ⚠️ Ce garde règle la VISIBILITÉ, pas l'autorisation d'accès : une route
    qui exige un jeton porte son `Depends(exiger_jeton)` en propre
    (`routes/maintenance.py`), sans quoi elle s'ouvrirait au premier visiteur
    le jour où le bureau règle son module sur "tous".
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
