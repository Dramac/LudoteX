"""
Point d'entrée de l'application FastAPI : assemble tout le reste.

RÔLE
----
- Crée l'objet `app` (l'application ASGI servie par uvicorn).
- Monte les fichiers statiques (CSS, JS du scanner) sous /static.
- Enregistre les routeurs (un module par domaine fonctionnel dans app/routes/).
- Définit les gestionnaires d'erreur : 403 (page « accès réservé »), 404 (page
  « introuvable »), 500 (page conviviale + journalisation) et un repli HTML
  générique pour tout autre code — aucune réponse ne sort en JSON brut.
- Émet un avertissement au démarrage si aucun jeton bénévole n'est configuré.

CARTE DES URL
-------------
    /                 -> page d'accueil publique (outils + dispo + tournois) [public]
    /catalogue        -> liste publique des jeux (+ recherche/filtres)   [public]
    /jeu/<id>         -> fiche d'un exemplaire (encodée dans le QR)       [public]
    /stats            -> statistiques de prêt                             [public]
    /live             -> tableau de bord temps réel (écran salle 16:9)    [public]
    /live/data        -> données JSON du tableau de bord (auto-refresh)   [public]
    /scanner          -> scanner caméra (ouvre /pret/<id>)              [bénévole]
    /pret/<id>        -> écran prêt/retour + actions POST               [bénévole]
    /acces?jeton=...  -> active l'accès bénévole (pose le cookie)
    /image/logo.png   -> logo (déposé en admin, sinon identité LudoteX) [public]
    /image/favicon-*  -> icônes d'onglet / écran d'accueil                [public]
    /sante            -> point de santé (supervision)

Lancement (développement) :
    uvicorn app.main:app --reload
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, journal
from app.db import get_connection, init_db
from app.modules import ModuleDesactive, garde_module
from app.routes import acces, admin, catalogue, images, live, pret, scanner, stats
from app.templating import templates
from app.tournoi import routes as tournoi_routes
from app.tournoi import routes_programme
from app.tournoi.db import init_db as init_tournoi_db
from app.planning import routes as planning_routes
from app.planning.db import init_db as init_planning_db
from app.version import APP_VERSION

# Répertoire du paquet `app/`, pour localiser le dossier static/.
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Prêt de jeux",
    description="Système de prêt de jeux de société par QR code (brique de prêt).",
    version=APP_VERSION,
)

# Sert les ressources statiques (style.css, jsQR.js, scanner.js) sous /static.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Chaque routeur regroupe les routes d'un domaine (voir app/routes/*.py).
# Les modules configurables reçoivent une dépendance garde_module(nom) :
# → "desactive" : lève ModuleDesactive (page conviviale)
# → "benevoles" sans jeton : lève HTTPException(403)
app.include_router(catalogue.router)                                           # /catalogue, /jeu/<id>   (public, cœur)
app.include_router(pret.router)                                                # /pret/<id> + actions     (bénévole, cœur)
app.include_router(scanner.router)                                             # /scanner                 (bénévole, cœur)
app.include_router(acces.router)                                               # /acces                   (activation)
app.include_router(images.router)                                              # /image/*                 (logo + icônes)
app.include_router(admin.router)                                               # /admin                   (mot de passe)
app.include_router(stats.router,            dependencies=[garde_module("stats")])      # /stats
app.include_router(live.router,             dependencies=[garde_module("live")])       # /live, /live/data
app.include_router(tournoi_routes.router,   dependencies=[garde_module("tournois")])   # /tournois, /tournoi/*
app.include_router(routes_programme.router, dependencies=[garde_module("programme")])  # /programme, /programme/*
app.include_router(planning_routes.router,  dependencies=[garde_module("planning")])   # /planning, /planning/*


@app.exception_handler(ModuleDesactive)
async def gestion_module_desactive(request, exc: ModuleDesactive):
    """
    Module désactivé → page conviviale plutôt qu'une erreur 404 brute.
    Toujours rendu avec status 404 (le module n'existe pas pour ce visiteur).
    """
    return templates.TemplateResponse(
        request, "module_desactive.html", {}, status_code=404
    )


# Titre et explication de la page d'erreur GÉNÉRIQUE (probleme.html), par code
# HTTP. 403 et 404 n'y figurent pas : ils ont leur propre gabarit, donc un
# message autrement plus utile.
#
# Le 405 est le seul cas vraiment atteignable ici, et il a une cause concrète :
# un favori ou un lien enregistré sur l'URL d'une ACTION (les `POST` de prêt,
# de retour, d'inscription…) plutôt que sur la page qui porte le bouton. Le
# dire en clair vaut mieux que « Method Not Allowed », qui n'aide personne.
_MESSAGES_HTTP = {
    405: (
        "Cette adresse ne s'ouvre pas directement",
        "Elle correspond à une action — ce qu'un bouton déclenche — et non à "
        "une page à consulter. C'est en général un favori enregistré au mauvais "
        "moment. Repartez d'un des liens ci-dessous, puis refaites le geste "
        "depuis la page.",
    ),
}

_MESSAGE_HTTP_DEFAUT = (
    "Cette page n'a pas pu s'afficher",
    "La demande n'a pas abouti. Vous pouvez réessayer, ou repartir d'un des "
    "liens ci-dessous.",
)


@app.exception_handler(StarletteHTTPException)
async def gestion_http(request, exc: StarletteHTTPException):
    """
    Gestionnaire global des erreurs HTTP — AUCUN code ne sort plus en JSON brut.

    - 403 : levé par `auth.exiger_jeton` quand l'appareil n'a pas activé
      l'accès bénévole, ou par `modules.garde_module` sur un module réservé.
    - 404 : adresse ne correspondant à AUCUNE route (faute de frappe, vieux
      lien, slash final).
    - TOUT LE RESTE : page générique `probleme.html`. Le cas atteignable est le
      405 (`GET` sur une route qui n'accepte que `POST`) ; il sortait jusqu'ici
      en `{"detail": "Method Not Allowed"}`, la « technique » que le projet
      chasse partout ailleurs (fiche ROB-04 de l'audit du 24/07). Le repli est
      volontairement GÉNÉRIQUE plutôt qu'une liste de codes à tenir à jour :
      un code oublié retomberait sinon en JSON, ce qu'on vient de corriger.

    Le code HTTP d'origine est TOUJOURS conservé dans la réponse : la page
    devient lisible, la sémantique ne bouge pas (indexation, supervision).

    NE PASSENT PAS ICI, et gardent donc leur message spécifique :
    - les 404 MÉTIER (« exemplaire inconnu », « tournoi inconnu ») — elles
      RETOURNENT leur propre gabarit avec `status_code=404` au lieu de lever ;
    - le module désactivé, qui a son propre gestionnaire (`ModuleDesactive`) ;
    - les erreurs de validation de FastAPI (`RequestValidationError`), qui ne
      sont pas des `HTTPException`.
    """
    if exc.status_code == 403:
        return templates.TemplateResponse(
            request, "acces_refuse.html", {"motif": "reserve"}, status_code=403
        )
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request, "introuvable.html", {}, status_code=404
        )
    titre, message = _MESSAGES_HTTP.get(exc.status_code, _MESSAGE_HTTP_DEFAUT)
    return templates.TemplateResponse(
        request, "probleme.html",
        {"titre": titre, "message": message}, status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def gestion_erreur(request, exc: Exception):
    """
    Filet de sécurité pour toute exception NON anticipée.

    Le serveur reste en ligne (uvicorn isole déjà chaque requête) ; ici on
    journalise l'erreur complète (pour le référent technique, visible via
    `journalctl -u ludotex`) et on renvoie une page 500 conviviale plutôt
    qu'un message technique brut.
    """
    logging.getLogger("uvicorn.error").exception("Erreur non gérée : %s", exc)
    return templates.TemplateResponse(request, "erreur.html", {}, status_code=500)


# S'assure que le schéma existe / est à jour au démarrage (idempotent). Crée les
# tables manquantes sur une base déjà existante (ex. nouvelle table `parametres`).
init_db()
# Base SÉPARÉE du module tournois (data/tournoi.db). Indépendante de la base de
# prêt : son init est distinct mais lui aussi idempotent.
init_tournoi_db()
# Base SÉPARÉE du module planning bénévole (data/planning.db), idempotente elle aussi.
init_planning_db()

# Journal d'activité (JSON Lines) : logger dédié, fichier tournant + console
# optionnelle (JOURNAL_CONSOLE). Aucun point d'appel métier n'est encore posé
# (lot C de docs/conception-journal.md) ; le socle est prêt à les recevoir.
journal.configurer()

# Garde-fou de déploiement : si aucun jeton n'est en vigueur, les écrans
# bénévole sont ouverts à tous. On le signale fort dans les logs au démarrage.
_conn_demarrage = get_connection()
try:
    _jeton_absent = auth.jeton_actuel(_conn_demarrage) is None
finally:
    _conn_demarrage.close()
if _jeton_absent:
    logging.getLogger("uvicorn.error").warning(
        "Aucun jeton bénévole en vigueur : les écrans bénévole (/pret, /scanner) "
        "sont OUVERTS. Définir PRET_TOKEN dans .env ou réinitialiser le jeton "
        "depuis /admin pour la production."
    )


@app.get("/sante", tags=["meta"])
def sante():
    """
    Point de santé pour la supervision (monitoring, reverse proxy).

    Returns:
        {"statut": "ok"} avec un code 200 si l'application répond.
    """
    return {"statut": "ok"}
