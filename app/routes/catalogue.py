"""
Routes du CATALOGUE PUBLIC — consultation en lecture seule (spec §5.2).

Ces écrans sont PUBLICS : aucune donnée personnelle, aucun bouton d'action, pas
de jeton requis. Ils constituent la « couche de lecture » réutilisable.

Routes principales :
- GET /jeu/<id_exemplaire> : fiche d'un exemplaire (URL encodée dans le QR).
- GET /catalogue          : liste des titres + recherche/filtres.
- GET /apropos            : page « À propos » (association, contact, licence…).

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
from app.version import APP_VERSION

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
    from datetime import datetime, timedelta

    conn = get_connection()
    try:
        total, disponible = services.compter_exemplaires_disponibles(conn)
        date_evenement = services.lire_parametre(conn, "evenement_date")
    finally:
        conn.close()

    # Planning sur 2 jours (jour de l'événement + lendemain), si une date est
    # réglée en admin. La frise n'apparaît que si elle contient des tournois.
    jours = []
    if date_evenement:
        try:
            jour1 = datetime.strptime(date_evenement, "%Y-%m-%d").date()
            jours = [jour1, jour1 + timedelta(days=1)]
        except ValueError:
            jours = []

    conn_t = get_tournoi_connection()
    try:
        imminents = tournoi_services.tournois_imminents(conn_t)
        planning = tournoi_services.planning(conn_t, jours) if jours else []
    finally:
        conn_t.close()

    planning_non_vide = any(not j["vide"] for j in planning)
    return templates.TemplateResponse(
        request, "accueil.html",
        {"total": total, "disponible": disponible, "imminents": imminents,
         "planning": planning, "planning_non_vide": planning_non_vide},
    )


@router.get("/aide")
def aide(request: Request):
    """Page d'aide / mode d'emploi bénévole (publique, liée depuis le menu)."""
    return templates.TemplateResponse(request, "aide.html", {})


@router.get("/apropos")
def apropos(request: Request):
    """Page « À propos » (publique) : association, objet du site, contact,
    crédits, auteur, licence et version. Contenu statique, sans base de
    données ni jeton requis."""
    return templates.TemplateResponse(
        request, "apropos.html",
        {"version": APP_VERSION, "depot_url": "https://github.com/Dramac/LudoteX"},
    )


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
        derniers = services.derniers_achats(conn, 10)
    finally:
        conn.close()

    # `filtres_actifs` : sert au gabarit à ouvrir le panneau et afficher
    # « Réinitialiser » uniquement quand au moins un filtre est posé.
    actifs = bool(q or filtre_cat or age_i is not None or joueurs_i is not None)
    chips = _puces_filtres(q, filtre_cat, age_i, joueurs_i)
    return templates.TemplateResponse(
        request,
        "catalogue.html",
        {"jeux": jeux, "categories": categories, "ages": ages, "max_joueurs": max_j,
         "filtre": filtre_cat, "q": q, "age": age_i, "joueurs": joueurs_i,
         "filtres_actifs": actifs, "chips": chips, "derniers_achats": derniers},
    )


def _puces_filtres(q, categorie, age, joueurs):
    """
    Construit la liste des « puces » de filtres actifs, chacune avec le lien qui
    RETIRE ce seul filtre (en conservant les autres).

    Args:
        q, categorie, age, joueurs: filtres normalisés en cours (str/int ou None).

    Returns:
        Liste de dicts ``{"label": str, "url": str}``, dans l'ordre d'affichage.
        Vide si aucun filtre n'est posé.
    """
    from urllib.parse import urlencode

    tous = {"q": q, "categorie": categorie, "age": age, "joueurs": joueurs}

    def _url_sans(cle):
        params = {k: v for k, v in tous.items()
                  if v not in (None, "") and k != cle}
        requete = urlencode(params)
        return "/catalogue?" + requete if requete else "/catalogue"

    puces = []
    if q:
        puces.append({"label": f"« {q} »", "url": _url_sans("q")})
    if categorie:
        puces.append({"label": categorie, "url": _url_sans("categorie")})
    if age is not None:
        puces.append({"label": f"dès {age} ans", "url": _url_sans("age")})
    if joueurs is not None:
        pluriel = "s" if joueurs > 1 else ""
        puces.append({"label": f"{joueurs} joueur{pluriel}", "url": _url_sans("joueurs")})
    return puces
