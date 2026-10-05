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
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, envois, journal, services
from app.db import get_connection, init_db
from app.modules import ModuleDesactive, garde_module
from app.routes import (acces, admin, catalogue, images, live, maintenance, pret,
                        scanner, stats)
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
# Le garde de module règle la VISIBILITÉ ; l'accès, lui, tient au
# `Depends(exiger_jeton)` que chaque route de ce module porte en propre
# (app/routes/maintenance.py). Les deux ne font pas le même travail.
app.include_router(maintenance.router,      dependencies=[garde_module("maintenance")])  # /maintenance
app.include_router(live.router,             dependencies=[garde_module("live")])       # /live, /live/data
app.include_router(tournoi_routes.router,   dependencies=[garde_module("tournois")])   # /tournois, /tournoi/*
app.include_router(routes_programme.router, dependencies=[garde_module("programme")])  # /programme, /programme/*
app.include_router(planning_routes.router,  dependencies=[garde_module("planning")])   # /planning, /planning/*


@app.middleware("http")
async def rafraichir_cookie_benevole(request, call_next):
    """
    Repose le cookie de jeton sur la réponse d'une requête qu'il a autorisée.

    `auth.acces_valide` marque la requête (`auth.ETAT_RAFRAICHIR_COOKIE`)
    UNIQUEMENT quand le cookie présenté égale le jeton en vigueur et que celui-ci
    n'est pas expiré : un cookie refusé n'est jamais prolongé, et une
    réinitialisation du jeton révoque toujours tous les appareils.

    Pourquoi reposer (lot-5-pré-production) : les téléphones activés AVANT que
    le cookie ne soit découplé de l'échéance du jeton portent encore un cookie
    qui meurt à l'ancienne échéance. Sans ce passage, prolonger le jeton les
    couperait quand même, tous au même instant. Aucune écriture en base : un
    en-tête de réponse, rien de plus.

    Ne touche pas une réponse qui pose déjà ce cookie (l'activation /acces).
    """
    reponse = await call_next(request)
    if getattr(request.state, auth.ETAT_RAFRAICHIR_COOKIE, False):
        jeton = request.cookies.get(auth.COOKIE_NAME)
        deja_pose = any(
            nom == b"set-cookie" and valeur.startswith(auth.COOKIE_NAME.encode() + b"=")
            for nom, valeur in reponse.raw_headers
        )
        if jeton and not deja_pose:
            acces.poser_cookies_benevole(
                reponse, request, jeton, services.appareil_de(request)
            )
    return reponse


# SEC-14 élargi (lot-17-pré-production) : un envoi multipart n'atteint que les
# trois routes d'envoi de fichier ; partout ailleurs, refus 415 SANS lecture du
# corps — motifs dans `envois.MultipartHorsEnvois`. Déclaré APRÈS le middleware
# ci-dessus, donc placé AUTOUR de lui : c'est le premier à voir la requête. La
# liste blanche est celle des routes elles-mêmes (`admin.CHEMINS_ENVOI`).
app.add_middleware(envois.MultipartHorsEnvois, chemins_autorises=admin.CHEMINS_ENVOI)


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
    # Valeur que FastAPI n'a pas pu convertir (`RequestValidationError`, voir
    # `gestion_validation`) : un numéro de page « abc » dans une adresse, un
    # champ numérique vide dans un formulaire envoyé à la main. Aucun bouton
    # de l'application ne le produit ; un lien tronqué ou retouché, si.
    422: (
        "Cette demande contient une valeur inattendue",
        "Un élément de l'adresse ou du formulaire n'a pas la forme attendue — "
        "souvent un lien coupé ou recopié à la main. Rien n'a été enregistré. "
        "Repartez d'un des liens ci-dessous, puis refaites le geste depuis la "
        "page.",
    ),
}

_MESSAGE_HTTP_DEFAUT = (
    "Cette page n'a pas pu s'afficher",
    "La demande n'a pas abouti. Vous pouvez réessayer, ou repartir d'un des "
    "liens ci-dessous.",
)


def _contexte_refus(request) -> dict:
    """
    Motif de la page « accès réservé » servie pour un 403.

    Deux gardes lèvent ce 403 : `auth.exiger_jeton` (écrans bénévole) et
    `modules.garde_module` (module réglé sur « bénévoles »). Le module
    DÉSACTIVÉ, lui, ne passe jamais ici : il a son propre gestionnaire
    (`ModuleDesactive`, page « module désactivé », 404).

    « expire » n'est pas choisi selon la garde, mais selon la CAUSE : le jeton
    est expiré ET le cookie de l'appareil est bien le jeton en vigueur
    (`auth.jeton_expire_reconnu`). C'est exactement la condition sous laquelle
    cet appareil serait entré sans l'échéance — donc la seule où « rouvrez le
    lien d'activation » est faux, et où « ce n'est pas votre lien qui est en
    cause » est vrai. Un visiteur sans cookie, sur un module réservé comme sur
    le scanner, garde le message habituel : il n'a pas encore activé l'accès,
    et c'est /acces qui lui dira que le jeton a expiré s'il ouvre le bon lien.
    """
    conn = get_connection()
    try:
        if auth.jeton_expire_reconnu(conn, request.cookies.get(auth.COOKIE_NAME)):
            return {"motif": "expire",
                    "expire_local": services.format_local(auth.expiration_jeton(conn))}
    finally:
        conn.close()
    return {"motif": "reserve"}


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
      sont pas des `HTTPException` : elles ont leur gestionnaire,
      `gestion_validation`, juste en dessous.
    """
    if exc.status_code == 403:
        return templates.TemplateResponse(
            request, "acces_refuse.html", _contexte_refus(request), status_code=403
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


@app.exception_handler(RequestValidationError)
async def gestion_validation(request, exc: RequestValidationError):
    """
    Erreur de VALIDATION de FastAPI → page générique, plus jamais de JSON nu
    (constat UX-01 de l'audit pré-production, même famille que ROB-04).

    FastAPI route d'abord, puis convertit les paramètres déclarés : une valeur
    qui ne se convertit pas (`?page=abc`, champ `int` d'un formulaire vide)
    lève `RequestValidationError`, qui n'est PAS une `HTTPException` et
    échappait donc à `gestion_http`. Le constat visait une seule route ;
    l'inventaire de toutes les routes en a trouvé d'autres, en requête et en
    formulaire (écrans du bureau : rangement, étiquettes, planning), d'où un
    gestionnaire plutôt qu'un correctif route par route — un paramètre typé
    ajouté demain serait couvert sans y penser. Un test parcourt les routes
    pour que les paramètres de CHEMIN, eux, gardent leur convertisseur
    (`{x:int}`), qui donne la réponse juste : 404, « adresse inconnue ».

    Le code 422 est conservé, comme `gestion_http` conserve le sien. Le détail
    de l'erreur n'est ni affiché ni journalisé : il peut recopier la valeur
    envoyée, et une valeur de formulaire n'a rien à faire dans un journal.
    """
    titre, message = _MESSAGES_HTTP[422]
    return templates.TemplateResponse(
        request, "probleme.html",
        {"titre": titre, "message": message}, status_code=422,
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

    Porte aussi le numéro de la version qui répond : c'est ce que
    `deploy/update.sh` affiche en fin de mise à jour, pour prouver que le
    nouveau code est bien celui qui tourne, et pas seulement qu'un code tourne.
    Aucune fuite : `/apropos` publie déjà ce numéro.

    Returns:
        {"statut": "ok", "version": "<APP_VERSION>"} avec un code 200 si
        l'application répond. `statut` reste la clé qu'un outil externe lit.
    """
    return {"statut": "ok", "version": APP_VERSION}
