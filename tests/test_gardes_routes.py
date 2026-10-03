"""
Garde-fou : toute route qui écrit, ou qui vit sous un préfixe réservé, porte
une garde (lot 14 — pré-production).

L'audit de sécurité a vérifié une à une que chaque route portait sa garde.
C'était vrai ce jour-là ; rien n'empêchait la suivante de l'oublier, et c'est
exactement le genre d'erreur qui ne se voit pas. Sur le patron de
`test_neutralisation.py` et `test_journal_interdits.py`, ce test interdit la
CLASSE d'erreur plutôt qu'un cas.

RÈGLE. Toute route dont une méthode n'est pas une lecture (POST…), et toute
route sous `PREFIXES_RESERVES`, doit porter l'une des gardes reconnues :

- `Depends(auth.exiger_jeton)` — dans la signature, dans `dependencies=`,
  ou sur le routeur ;
- un appel à `_garde(...)` dans le corps de la fonction, où `_garde` est
  BIEN l'une des deux gardes d'administration du projet (`routes/admin.py`,
  `planning/routes.py`) — une fonction locale du même nom ne compte pas ;
- la classe de route `RouteEnvoiAdmin`, qui garde avant de lire le corps.

Le test vérifie la PRÉSENCE d'une garde, pas sa forme : la redirection vers
/admin reste la règle des routes d'administration.

`garde_module` N'EST PAS une garde : il règle la VISIBILITÉ d'un module, et
s'ouvre au premier visiteur le jour où le bureau règle le module sur « tous »
(voir sa docstring, et la garde doublée du carnet de maintenance).

LES EXCEPTIONS SONT LE CŒUR DU TEST. Chacune porte sa raison ; une exception
sans raison, ou qui ne correspond plus à une route existante et non gardée,
fait échouer le test — sans quoi la liste deviendrait un tampon.

LE TEST ÉCHOUE POUR DE VRAI : `test_le_detecteur_voit_les_oublis` le fait
tourner sur une petite application témoin qui contient chaque oubli possible,
à chaque exécution de la suite.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.routing import APIRoute

from app import auth
from app.modules import garde_module
from app.planning import routes as routes_planning
from app.routes import admin as routes_admin
# Importée sous ce nom pour l'application témoin : une route témoin qui
# appelle `_garde(request)` doit être reconnue comme une vraie garde.
from app.routes.admin import _garde  # noqa: F401

PREFIXES_RESERVES = ("/admin", "/pret", "/scanner", "/maintenance",
                     # Ajouté au périmètre du prompt : l'administration du
                     # planning est de l'administration, sous un autre préfixe.
                     "/planning/admin")

METHODES_LECTURE = frozenset({"GET", "HEAD", "OPTIONS"})

GARDES_DEPENDANCE = (auth.exiger_jeton,)
GARDES_CORPS = (routes_admin._garde, routes_planning._garde)

# (méthode, chemin tel que FastAPI le déclare) → pourquoi aucune garde.
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("GET", "/admin"): (
        "porte d'entrée de l'administration : affiche le formulaire de "
        "connexion à qui n'est pas connecté, le tableau de bord sinon — le "
        "choix est fait dans le corps, par admin_connecte"),
    ("POST", "/admin/login"): (
        "la connexion elle-même : on ne peut pas exiger d'être connecté pour "
        "se connecter. Freinée par la limitation de débit de la connexion"),
    ("GET", "/admin/logout"): (
        "ferme la session de qui l'appelle ; n'ouvre ni ne montre rien"),
    ("POST", "/tournoi/{id_tournoi:int}/inscription"): (
        "inscription PUBLIQUE à un tournoi (le pseudo est une donnée "
        "personnelle assumée). Quand le bureau réserve l'inscription aux "
        "bénévoles, le contrôle est fait dans le corps"),
    ("POST", "/tournoi/desinscription"): (
        "désinscription publique : c'est le code de désinscription remis à "
        "l'inscription qui fait office de garde"),
    ("POST", "/planning/collecte/{ev:int}"): (
        "questionnaire public de disponibilités des bénévoles, qui n'ont pas "
        "de compte : la collecte se ferme par l'état de l'événement"),
}


# ---------------------------------------------------------------------------
# Le détecteur
# ---------------------------------------------------------------------------
def _dependances(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependances(dep)


def _garde_par_dependance(route: APIRoute) -> bool:
    return any(appel in GARDES_DEPENDANCE for appel in _dependances(route.dependant))


def _garde_dans_le_corps(route: APIRoute) -> bool:
    """Un appel à `_garde(...)` dans le corps, ET ce `_garde` est une vraie garde."""
    if route.endpoint.__globals__.get("_garde") not in GARDES_CORPS:
        return False
    arbre = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint)))
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_garde" for n in ast.walk(arbre))


def est_gardee(route: APIRoute) -> bool:
    return (_garde_par_dependance(route) or _garde_dans_le_corps(route)
            or isinstance(route, routes_admin.RouteEnvoiAdmin))


def doit_etre_gardee(route: APIRoute) -> bool:
    ecrit = bool(set(route.methods) - METHODES_LECTURE)
    reserve = any(route.path == p or route.path.startswith(p + "/")
                  for p in PREFIXES_RESERVES)
    return ecrit or reserve


def _cles(route: APIRoute):
    return [(m, route.path) for m in sorted(route.methods)]


def routes_sans_garde(application, exceptions=EXCEPTIONS) -> list[str]:
    """Les routes qui devraient porter une garde et n'en portent aucune."""
    manquantes = []
    for route in application.routes:
        if not isinstance(route, APIRoute) or not doit_etre_gardee(route):
            continue
        if est_gardee(route):
            continue
        if all(cle in exceptions for cle in _cles(route)):
            continue
        manquantes.append(f"{','.join(sorted(route.methods))} {route.path} "
                          f"({route.endpoint.__module__}.{route.endpoint.__name__})")
    return manquantes


# ---------------------------------------------------------------------------
# Les tests
# ---------------------------------------------------------------------------
def test_toute_route_qui_ecrit_ou_reservee_porte_une_garde():
    from app.main import app

    manquantes = routes_sans_garde(app)
    assert not manquantes, (
        "Route(s) sans garde. Ajoutez Depends(exiger_jeton), ou "
        "`if (garde := _garde(request)): return garde` en tête de la fonction "
        "— ou, si elle doit VRAIMENT rester ouverte, une entrée motivée dans "
        "EXCEPTIONS :\n  " + "\n  ".join(manquantes))


def test_les_exceptions_sont_motivees_et_a_jour():
    """Une exception sans raison, ou qui ne sert plus, fait échouer le test."""
    from app.main import app

    routes = {cle: r for r in app.routes if isinstance(r, APIRoute) for cle in _cles(r)}
    for cle, raison in EXCEPTIONS.items():
        assert len(raison.split()) >= 5, f"Exception sans vraie raison : {cle}"
        assert cle in routes, f"Exception pour une route qui n'existe plus : {cle}"
        assert not est_gardee(routes[cle]), (
            f"{cle} porte désormais une garde : retirez son exception.")


def test_garde_module_seul_n_est_pas_une_garde():
    """Le carnet de maintenance garde ses DEUX dépendances (visibilité ET
    jeton) : retirer `exiger_jeton` doit être vu, `garde_module` ne suffit pas."""
    from app.main import app

    traiter = next(r for r in app.routes if isinstance(r, APIRoute)
                   and r.path == "/maintenance/{id_signalement:int}/traiter")
    assert _garde_par_dependance(traiter)
    appels = [getattr(a, "__qualname__", "") for a in _dependances(traiter.dependant)]
    assert any("garde_module" in a for a in appels)


def test_le_detecteur_voit_les_oublis():
    """
    Le rouge, démontré à chaque exécution : une application témoin porte
    chaque forme d'oubli, et chaque forme de garde. Un garde-fou qu'on n'a pas
    vu échouer ne protège rien.
    """
    def _garde_factice(request):          # même rôle, mais pas une vraie garde
        return None

    temoin = FastAPI()
    module_seul = APIRouter(dependencies=[garde_module("stats")])

    @temoin.post("/libre/ecrire")
    def oubli_post():
        return {}

    @temoin.get("/admin/oubli")
    def oubli_admin(request: Request):
        return {}

    @temoin.get("/pret/{x}/oubli")
    def oubli_pret(x: str):
        return {}

    @module_seul.post("/maintenance/oubli")
    def oubli_visibilite_seule():
        return {}

    @temoin.post("/admin/faux")
    def oubli_fausse_garde(request: Request):
        if (garde := _garde_factice(request)):
            return garde
        return {}

    @temoin.post("/admin/exiger")
    def garde_jeton(_=Depends(auth.exiger_jeton)):
        return {}

    @temoin.post("/admin/corps")
    def garde_corps(request: Request):
        if (garde := _garde(request)):
            return garde
        return {}

    @temoin.get("/catalogue")
    def lecture_publique():
        return {}

    temoin.include_router(module_seul)

    manquantes = {ligne.split(" ")[1] for ligne in routes_sans_garde(temoin, exceptions={})}
    assert manquantes == {"/libre/ecrire", "/admin/oubli", "/pret/{x}/oubli",
                          "/maintenance/oubli", "/admin/faux"}
