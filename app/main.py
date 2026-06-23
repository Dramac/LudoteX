"""
Point d'entrée de l'application FastAPI : assemble tout le reste.

RÔLE
----
- Crée l'objet `app` (l'application ASGI servie par uvicorn).
- Monte les fichiers statiques (CSS, JS du scanner) sous /static.
- Enregistre les routeurs (un module par domaine fonctionnel dans app/routes/).
- Définit les gestionnaires d'erreur : 403 (page « accès réservé ») et 500
  (page d'erreur conviviale + journalisation) pour rester robuste en ligne.
- Émet un avertissement au démarrage si aucun jeton bénévole n'est configuré.

CARTE DES URL
-------------
    /                 -> page d'accueil publique (outils + dispo + tournois) [public]
    /catalogue        -> liste publique des jeux (+ recherche/filtres)   [public]
    /jeu/<id>         -> fiche d'un exemplaire (encodée dans le QR)       [public]
    /stats            -> statistiques de prêt                             [public]
    /scanner          -> scanner caméra (ouvre /pret/<id>)              [bénévole]
    /pret/<id>        -> écran prêt/retour + actions POST               [bénévole]
    /acces?jeton=...  -> active l'accès bénévole (pose le cookie)
    /sante            -> point de santé (supervision)

Lancement (développement) :
    uvicorn app.main:app --reload
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth
from app.db import get_connection, init_db
from app.routes import acces, admin, catalogue, pret, scanner, stats
from app.templating import templates
from app.tournoi import routes as tournoi_routes
from app.tournoi.db import init_db as init_tournoi_db

# Répertoire du paquet `app/`, pour localiser le dossier static/.
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Prêt de jeux",
    description="Système de prêt de jeux de société par QR code (brique de prêt).",
    version="0.1.0",
)

# Sert les ressources statiques (style.css, jsQR.js, scanner.js) sous /static.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Chaque routeur regroupe les routes d'un domaine (voir app/routes/*.py).
app.include_router(catalogue.router)   # /catalogue, /jeu/<id>   (public)
app.include_router(pret.router)        # /pret/<id> + actions     (bénévole)
app.include_router(scanner.router)     # /scanner                 (bénévole)
app.include_router(stats.router)       # /stats                   (public)
app.include_router(acces.router)       # /acces                   (activation)
app.include_router(admin.router)       # /admin                   (mot de passe)
app.include_router(tournoi_routes.router)  # /tournois, /tournoi/* (module tournois)


@app.exception_handler(StarletteHTTPException)
async def gestion_http(request, exc: StarletteHTTPException):
    """
    Gestionnaire global des erreurs HTTP.

    Cas spécial : un 403 (levé par `auth.exiger_jeton` quand l'appareil n'a pas
    activé l'accès bénévole) renvoie une PAGE HTML conviviale plutôt qu'une
    erreur JSON brute. Tous les autres codes retombent sur le comportement par
    défaut de FastAPI.
    """
    if exc.status_code == 403:
        return templates.TemplateResponse(
            request, "acces_refuse.html", {"motif": "reserve"}, status_code=403
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def gestion_erreur(request, exc: Exception):
    """
    Filet de sécurité pour toute exception NON anticipée.

    Le serveur reste en ligne (uvicorn isole déjà chaque requête) ; ici on
    journalise l'erreur complète (pour le référent technique, visible via
    `journalctl -u pret-jeux`) et on renvoie une page 500 conviviale plutôt
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
