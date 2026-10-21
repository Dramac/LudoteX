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

from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request

from app import bgg, bgg_api, categories_jeu, services, vignettes
from app.db import get_connection
from app.modules import module_visible
from app.templating import templates
from app.tournoi import services as tournoi_services
from app.tournoi.db import get_connection as get_tournoi_connection
from app.version import APP_VERSION, nouveautes_recentes, puce_en_html

# `tags` regroupe ces routes dans la doc auto (/docs).
router = APIRouter(tags=["catalogue"])

# Fenêtre du bloc « ça commence bientôt » de l'accueil (minutes). L'écran de
# salle utilise la même fusion avec une fenêtre plus large (2 h) : il est lu de
# loin, de passage, tandis qu'on consulte l'accueil pour savoir quoi faire tout
# de suite.
FENETRE_IMMINENTS_MIN = 60


@router.get("/")
def accueil(request: Request):
    """
    Page d'accueil publique du système (remplace le catalogue comme point
    d'entrée). Donne accès aux outils publics (catalogue, tournois, programme),
    rappelle le nombre de jeux disponibles au prêt et annonce **ce qui commence
    dans l'heure** — tournois ET animations mélangés, triés par heure.
    """
    from datetime import datetime, timedelta

    from app.tournoi import programme

    conn = get_connection()
    try:
        total, disponible = services.compter_exemplaires_disponibles(conn)
        date_evenement = services.lire_parametre(conn, "evenement_date")
    finally:
        conn.close()

    # Fiche A3 : si un module est désactivé (ou réservé aux bénévoles, pour un
    # visiteur non activé), on ne calcule même pas ses données — sinon
    # l'accueil afficherait le contenu d'un module que l'administrateur vient
    # de masquer. Les deux sources sont indépendantes : l'une peut manquer
    # sans que l'autre disparaisse.
    voir_tournois = module_visible(request, "tournois")
    voir_programme = module_visible(request, "programme")

    jours = []
    if date_evenement:
        try:
            jour1 = datetime.strptime(date_evenement, "%Y-%m-%d").date()
            jours = [jour1, jour1 + timedelta(days=1)]
        except ValueError:
            jours = []

    imminents = []
    planning = []
    if voir_tournois or voir_programme:
        conn_t = get_tournoi_connection()
        try:
            # UNE seule fusion, la même que celle de l'écran de salle : deux
            # implémentations divergeraient le jour de l'événement.
            imminents = [
                e for e in programme.imminents(conn_t, FENETRE_IMMINENTS_MIN)
                if (e["source"] == "tournoi" and voir_tournois)
                or (e["source"] == "programme" and voir_programme)
            ]
            planning = programme.planning_fusionne(
                conn_t, jours,
                avec_tournois=voir_tournois, avec_programme=voir_programme,
            ) if jours else []
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


@router.get("/rangement/aide")
def rangement_aide(request: Request):
    """Aide / mode d'emploi spécifique au rangement des boîtes (publique)."""
    return templates.TemplateResponse(request, "rangement_aide.html", {})


@router.get("/apropos")
def apropos(request: Request):
    """
    Page « À propos » (publique) : association, objet du site, contact,
    crédits, auteur, licence et version. Sans jeton requis.

    Trois de ces contenus sont des RÉGLAGES et non du texte en dur (présentation
    de l'association, adresse de contact, URL du dépôt du code) : cette page
    n'est donc plus statique, elle ouvre UNE connexion de prêt et lit les trois
    valeurs d'un coup. Une seule connexion, et non trois lectures successives :
    ce sont trois lignes de la même table.

    Ces valeurs ne passent PAS par le context processor d'app/templating.py, à
    la différence du nom de l'association : tout ce qui y passe est injecté dans
    le rendu de TOUTES les pages, page d'erreur 500 comprise, et elles ne
    servent qu'ici.

    LA PAGE RÉPOND MÊME SI LA BASE EST INDISPONIBLE. C'est la seule page qui
    porte la licence, le crédit d'auteur et le lien vers le code source : elle
    doit rester lisible quand le reste du site ne l'est plus. Une lecture qui
    échoue retombe donc sur « aucun réglage » — sections masquées, URL du dépôt
    par repli — plutôt que sur une erreur 500.
    """
    from app.config import DEPOT_URL

    presentation = None
    contact = None
    depot_url = DEPOT_URL
    # Mention « Powered by BGG » (conditions d'utilisation de BoardGameGeek) :
    # dès que l'instance UTILISE BGG — jeton configuré, lien public coché,
    # images publiques cochées, ou copies déjà en base. Ce dernier cas compte :
    # une valeur reprise de BGG finit sur la fiche publique et sur l'étiquette,
    # même une fois le jeton retiré. La case des images est couverte en
    # pratique par les copies (pas d'image sans fiche) ; elle compte quand même,
    # par cohérence avec le lien. Aucun appel réseau : lectures locales.
    mention_bgg = bgg_api.disponible()
    try:
        conn = get_connection()
        try:
            presentation = services.lire_presentation_association(conn)
            contact = services.lire_contact_association(conn)
            depot_url = services.lire_depot_url(conn)
            mention_bgg = (mention_bgg or services.lire_bgg_lien_public(conn)
                           or services.lire_bgg_images_publiques(conn)
                           or services.copies_bgg_presentes(conn))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - repli volontairement total, voir docstring
        pass

    return templates.TemplateResponse(
        request, "apropos.html",
        {"version": APP_VERSION,
         # Puces du changelog : Markdown simple mis en forme, texte échappé.
         "nouveautes": [puce_en_html(p) for p in nouveautes_recentes()],
         "presentation": services.paragraphes(presentation),
         "contact": contact,
         "depot_url": depot_url,
         "lien_versions": services.lien_journal_versions(depot_url),
         "mention_bgg": mention_bgg},
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
        emplacement = None
        lien_bgg = None
        image_boite = None
        categories = []
        if info is not None:
            total, disponible = services.dispo_par_titre(conn, info["reference_titre"])
            categories = services.categories_du_titre(conn, info["reference_titre"])
            # Lien vers la fiche BoardGameGeek : seulement si l'instance l'a
            # choisi (/admin/identite, décoché par défaut). Un simple lien,
            # construit du seul identifiant : aucun appel réseau.
            if info["bgg_id"] and services.lire_bgg_lien_public(conn):
                lien_bgg = bgg.url_fiche(info["bgg_id"])
            # Image de la boîte (série « images », lot 2) : seulement si
            # l'instance l'a choisi (/admin/identite, décoché par défaut) et
            # que la copie locale existe pour l'adresse en base. Jamais
            # l'adresse BGG : la page publique ne charge rien d'ailleurs (CSP).
            # Sans copie, rien — ni balise, ni cadre vide.
            if info["bgg_id"] and services.lire_bgg_images_publiques(conn):
                copie = services.lire_fiche_bgg(conn, info["bgg_id"])
                if copie and copie["trouve"]:
                    image_boite = vignettes.url(info["bgg_id"], copie.get("vignette"))
            # Emplacement de rangement (docs/conception-rangement.md §7) :
            # seulement si le réglage de visibilité l'autorise pour ce
            # visiteur ET qu'un emplacement est effectivement renseigné
            # (jamais de « non renseigné » anxiogène).
            if services.rangement_visible(request):
                emplacement = services.emplacement_actuel(conn, id_exemplaire)
    finally:
        conn.close()

    # Un lien par catégorie vers le catalogue filtré sur sa CLÉ. Jeu tout
    # sorti : vers les jeux DISPONIBLES de cette catégorie (règle « A4 »,
    # voir fiche.html), sinon vers tous les autres.
    tout_sorti = disponible == 0
    liens_categories = [
        {"nom": c["nom"],
         "url": _url_catalogue({"cles": [c["cle"]], "dispo": tout_sorti})}
        for c in categories]
    return templates.TemplateResponse(
        request,
        "fiche.html",
        {"id_exemplaire": id_exemplaire, "info": info,
         "total": total, "disponible": disponible, "emplacement": emplacement,
         "lien_bgg": lien_bgg, "image_boite": image_boite,
         "liens_categories": liens_categories},
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
def catalogue(request: Request, categorie: list[str] = Query(default=[]),
              q: str | None = None, age: str | None = None,
              joueurs: str | None = None, dispo: str | None = None):
    """
    Liste publique des jeux, avec recherche et filtres combinés.

    Les filtres (tous optionnels) viennent du panneau dépliable de
    catalogue.html, en GET : `q` (texte), `categorie` (RÉPÉTÉ, une valeur par
    case cochée), `age`, `joueurs`, `dispo` (case « disponibles seulement »,
    `1` si cochée). On les normalise ici (chaînes vides → None, conversion
    entière/booléenne) avant de déléguer la requête à
    `services.lister_catalogue`.

    `categorie` est déclaré en LISTE : en `str | None`, FastAPI ne garderait
    que la dernière valeur d'un paramètre répété. Chaque valeur est découpée
    puis mise en clé (`categories_jeu.cles_du_filtre`) : les anciennes
    adresses, qui portaient la case entière (« Cartes, Ambiance ») ou une
    graphie (« Stratégie »), filtrent comme avant le passage aux cases. Une
    clé inconnue est ignorée (lecture publique : rien à refuser).

    Args:
        request: requête (nécessaire à Jinja2).
        categorie, q, age, joueurs, dispo: paramètres de requête bruts (chaînes).

    Returns:
        La page catalogue.html, avec la liste filtrée, les valeurs possibles des
        menus, et l'état courant des filtres (pour ré-afficher le formulaire).
    """
    # Normalisation des entrées : "" -> None, "12" -> 12.
    q = (q or "").strip() or None
    age_i = _entier_ou_none(age)
    joueurs_i = _entier_ou_none(joueurs)
    dispo_seulement = dispo == "1"

    conn = get_connection()
    try:
        vocabulaire = {c["cle"]: c["nom"] for c in services.lister_categories_jeu(conn)}
        cles = categories_jeu.cles_du_filtre(categorie, vocabulaire)
        ages = services.ages_disponibles(conn)
        max_j = services.max_joueurs(conn)
        jeux = services.lister_catalogue(conn, cles, q, age_i, joueurs_i,
                                          dispo_seulement)
        derniers = services.derniers_achats(conn, 10)
    finally:
        conn.close()

    filtres = {"q": q, "cles": cles, "age": age_i, "joueurs": joueurs_i,
               "dispo": dispo_seulement}
    # `filtres_actifs` : sert au gabarit à ouvrir le panneau et afficher
    # « Réinitialiser » uniquement quand au moins un filtre est posé.
    actifs = bool(q or cles or age_i is not None or joueurs_i is not None
                  or dispo_seulement)
    chips = _puces_filtres(filtres, vocabulaire)
    autres_filtres = bool(q or age_i is not None or joueurs_i is not None
                          or dispo_seulement)
    return templates.TemplateResponse(
        request,
        "catalogue.html",
        {"jeux": jeux,
         "cases_categories": categories_jeu.cases_a_cocher(vocabulaire, cles),
         "nb_categories": len(cles),
         # Liste vide avec au moins une catégorie cochée : le message le dit,
         # et « Retirer les catégories » garde tous les autres filtres.
         "url_sans_categories": _url_catalogue({**filtres, "cles": []}),
         "autres_filtres": autres_filtres,
         "ages": ages, "max_joueurs": max_j,
         "q": q, "age": age_i, "joueurs": joueurs_i,
         "dispo": dispo_seulement,
         "filtres_actifs": actifs, "chips": chips, "derniers_achats": derniers},
    )


def _url_catalogue(filtres: dict) -> str:
    """
    L'adresse du catalogue pour ces filtres (`q`, `cles`, `age`, `joueurs`,
    `dispo`) : `categorie` y est RÉPÉTÉ, une fois par clé.
    """
    params: list[tuple[str, object]] = []
    if filtres.get("q"):
        params.append(("q", filtres["q"]))
    params += [("categorie", k) for k in filtres.get("cles") or ()]
    if filtres.get("age") is not None:
        params.append(("age", filtres["age"]))
    if filtres.get("joueurs") is not None:
        params.append(("joueurs", filtres["joueurs"]))
    if filtres.get("dispo"):
        params.append(("dispo", "1"))
    requete = urlencode(params)
    return "/catalogue?" + requete if requete else "/catalogue"


def _puces_filtres(filtres: dict, vocabulaire: dict[str, str]) -> list[dict]:
    """
    Construit la liste des « puces » de filtres actifs, chacune avec le lien qui
    RETIRE ce seul filtre (en conservant les autres). Une puce PAR CATÉGORIE
    cochée : la sienne retire cette seule catégorie.

    Args:
        filtres: filtres normalisés en cours (`q`, `cles`, `age`, `joueurs`,
            `dispo`), comme pour `_url_catalogue`.
        vocabulaire: clé -> graphie, pour le libellé des puces de catégorie.

    Returns:
        Liste de dicts ``{"label": str, "url": str}``, dans l'ordre d'affichage.
        Vide si aucun filtre n'est posé.
    """
    def _url_sans(**retrait):
        return _url_catalogue({**filtres, **retrait})

    puces = []
    q, cles = filtres.get("q"), filtres.get("cles") or []
    age, joueurs = filtres.get("age"), filtres.get("joueurs")
    if q:
        puces.append({"label": f"« {q} »", "url": _url_sans(q=None)})
    for k in cles:
        puces.append({"label": vocabulaire.get(k, k),
                      "url": _url_sans(cles=[c for c in cles if c != k])})
    if age is not None:
        puces.append({"label": f"dès {age} ans", "url": _url_sans(age=None)})
    if joueurs is not None:
        pluriel = "s" if joueurs > 1 else ""
        puces.append({"label": f"{joueurs} joueur{pluriel}", "url": _url_sans(joueurs=None)})
    if filtres.get("dispo"):
        puces.append({"label": "disponibles seulement", "url": _url_sans(dispo=False)})
    return puces
