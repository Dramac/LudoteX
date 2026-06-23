"""
Routes du CATALOGUE PUBLIC — consultation en lecture seule (spec §5.2).

Ces écrans sont PUBLICS : aucune donnée personnelle, aucun bouton d'action, pas
de jeton requis. Ils constituent la « couche de lecture » réutilisable.

Deux routes :
- GET /jeu/<id_exemplaire> : fiche d'un exemplaire (URL encodée dans le QR).
- GET /catalogue          : liste des titres + recherche/filtres.

Pattern commun : on ouvre une connexion, on délègue tout le calcul à
`app.services`, on ferme la connexion (try/finally), puis on rend un gabarit.
Les routes ne contiennent PAS de logique métier ni de SQL.
"""

from fastapi import APIRouter, Request

from app import services
from app.db import get_connection
from app.templating import templates
from app.tournoi import services as tournoi_services
from app.tournoi.db import get_connection as get_tournoi_connection

# `tags` regroupe ces routes dans la doc auto (/docs).
router = APIRouter(tags=["catalogue"])


@router.get("/")
def accueil(request: Request):
    """
    Page d'accueil publique du système (remplace le catalogue comme point
    d'entrée). Donne accès aux outils publics (catalogue, tournois), rappelle le
    nombre de jeux disponibles au prêt et liste les tournois imminents (qui
    commencent dans l'heure).
    """
    conn = get_connection()
    try:
        total, disponible = services.compter_exemplaires_disponibles(conn)
    finally:
        conn.close()
    conn_t = get_tournoi_connection()
    try:
        imminents = tournoi_services.tournois_imminents(conn_t)
    finally:
        conn_t.close()
    return templates.TemplateResponse(
        request, "accueil.html",
        {"total": total, "disponible": disponible, "imminents": imminents},
    )


@router.get("/aide")
def aide(request: Request):
    """Page d'aide / mode d'emploi bénévole (publique, liée depuis le menu)."""
    return templates.TemplateResponse(request, "aide.html", {})


@router.get("/jeu/{id_exemplaire}")
def fiche(request: Request, id_exemplaire: str):
    """
    Affiche la fiche publique d'un exemplaire.

    C'est la cible des QR codes (``/jeu/<id>``). On montre les caractéristiques
    du jeu et sa disponibilité au niveau titre (X/Y exemplaires dispo).

    Args:
        request: requête (nécessaire à Jinja2).
        id_exemplaire: identifiant lu dans l'URL (TEXT).

    Returns:
        La page fiche.html. Statut 200 si l'exemplaire existe, 404 sinon (le
        gabarit gère le cas « inconnu » avec un message convivial).
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        total = disponible = 0
        if info is not None:
            total, disponible = services.dispo_par_titre(conn, info["reference_titre"])
    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "fiche.html",
        {"id_exemplaire": id_exemplaire, "info": info,
         "total": total, "disponible": disponible},
        status_code=200 if info else 404,
    )


def _entier_ou_none(valeur: str | None) -> int | None:
    """
    Convertit une valeur de formulaire (chaîne) en entier, ou None.

    Les menus du filtre renvoient "" pour « tous » et une valeur numérique
    sinon. On accepte donc des chaînes et on renvoie None si vide ou non
    numérique (plutôt que de laisser FastAPI rejeter la requête en 422).

    Args:
        valeur: la valeur brute du paramètre de requête.

    Returns:
        L'entier correspondant, ou None.
    """
    if valeur is None or valeur.strip() == "":
        return None
    try:
        return int(valeur)
    except ValueError:
        return None


@router.get("/catalogue")
def catalogue(request: Request, categorie: str | None = None, q: str | None = None,
              age: str | None = None, joueurs: str | None = None):
    """
    Liste publique des jeux, avec recherche et filtres combinés.

    Les quatre filtres (tous optionnels) viennent du panneau dépliable de
    catalogue.html, en GET : `q` (texte), `categorie`, `age`, `joueurs`. On les
    normalise ici (chaînes vides → None, conversion entière) avant de déléguer la
    requête à `services.lister_catalogue`.

    Args:
        request: requête (nécessaire à Jinja2).
        categorie, q, age, joueurs: paramètres de requête bruts (chaînes).

    Returns:
        La page catalogue.html, avec la liste filtrée, les valeurs possibles des
        menus, et l'état courant des filtres (pour ré-afficher le formulaire).
    """
    # Normalisation des entrées : "" -> None, "12" -> 12.
    q = (q or "").strip() or None
    age_i = _entier_ou_none(age)
    joueurs_i = _entier_ou_none(joueurs)

    conn = get_connection()
    try:
        categories = services.lister_categories(conn)
        ages = services.ages_disponibles(conn)
        max_j = services.max_joueurs(conn)
        # Sécurité/robustesse : on n'accepte qu'une catégorie réellement existante
        # (une valeur inconnue dans l'URL revient à « pas de filtre catégorie »).
        filtre_cat = categorie if categorie in categories else None
        jeux = services.lister_catalogue(conn, filtre_cat, q, age_i, joueurs_i)
    finally:
        conn.close()

    # `filtres_actifs` : sert au gabarit à ouvrir le panneau et afficher
    # « Réinitialiser » uniquement quand au moins un filtre est posé.
    actifs = bool(q or filtre_cat or age_i is not None or joueurs_i is not None)
    return templates.TemplateResponse(
        request,
        "catalogue.html",
        {"jeux": jeux, "categories": categories, "ages": ages, "max_joueurs": max_j,
         "filtre": filtre_cat, "q": q, "age": age_i, "joueurs": joueurs_i,
         "filtres_actifs": actifs},
    )
