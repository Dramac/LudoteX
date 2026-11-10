"""
Routes du module « Programme du week-end » (docs/conception-programme.md,
jalon 2) — public (grille + `.ics` + aide) et bénévole (création/édition/
gestion, derrière le jeton). La configuration des TYPES reste dans
`app/routes/admin.py` (garde mot de passe, groupe « Événement »).

CONVENTIONS (alignées sur `app/tournoi/routes.py`)
---------------------------------------------------
- Pages servies par Jinja2, gabarits `programme_*.html`.
- ÉCRITURE bénévole protégée par `Depends(exiger_jeton)` (même jeton que /pret).
- LECTURE publique : grille, `.ics`, aide.
- Connexion à la base SÉPARÉE des tournois (`data/tournoi.db`, aucune FK vers la
  base de prêt) via `app.tournoi.db.get_connection`, toujours fermée en
  try/finally. La date de l'événement (bornes de la grille) est en revanche
  stockée dans la base de PRÊT (`parametres.evenement_date`, réglée depuis
  `/admin/evenement`, déjà utilisée par la frise des tournois sur l'accueil) :
  une lecture ouvre donc en plus, ponctuellement, une connexion à cette base.

CARTE DES URL
-------------
    /programme                              grille publique (filtres jour/type/public)
    /programme/aide                         aide / mode d'emploi (publique)
    /programme/{id}                         page publique d'un élément (publique)
    /programme/{id}/agenda.ics              « Ajouter à mon agenda » (publique)
    /programme/gestion         [bénévole]  liste de travail (brouillons compris,
                                           filtres type/lieu/public/état)
    /programme/gestion/en-lot  [bénévole]  « Tout publier » / « Tout dépublier »
                                           sur la liste filtrée : confirmation
                                           (GET) + POST
    /programme/nouveau         [bénévole]  formulaire de création
    /programme/{id}/editer     [bénévole]  formulaire d'édition + POST
    /programme/{id}/etat       [bénévole]  transition d'état (POST)
    /programme/{id}/dupliquer  [bénévole]  copie en brouillon, sans date (POST)
    /programme/{id}/supprimer  [bénévole]  page de confirmation (GET) + POST

NOTE : les paramètres d'id utilisent le convertisseur `:int`, ce qui évite toute
collision avec les segments littéraux (`gestion`, `nouveau`, `aide`).

Pas de route `/programme/{id}/gerer` : contrairement aux tournois (participants,
scores, rencontres), un élément de programme n'a que des champs simples — les
actions D'ÉDITION (modifier, changer d'état, dupliquer, supprimer) vivent
directement dans les lignes de la liste `/programme/gestion`, il n'y a toujours
aucun écran de GESTION dédié par élément. Il existe en revanche, depuis la
révision du 09/08/2026 (voir `docs/conception-programme.md`), une page
PUBLIQUE de LECTURE par élément (`/programme/{id}`, ci-dessus) — patron
`tournoi.routes.detail`, avec bouton `.ics` et bandeau « annulé ».
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from app import journal
from app.auth import exiger_jeton
from app.db import get_connection as get_pret_connection
from app.services import (
    lire_nom_evenement,
    lire_parametre,
    local_vers_utc_iso,
    nom_association,
)
from app.templating import templates
from app.tournoi import programme
from app.tournoi.db import get_connection
from app.tournoi.models import ETATS_PROGRAMME
from app.tournoi.services import iso_utc_vers_datetime_local

router = APIRouter(tags=["programme"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _int_ou_none(v: str) -> int | None:
    """Convertit une saisie en entier positif, ou None si vide/non numérique."""
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


def _types_pour_formulaire(conn, id_type_courant: int | None) -> list[dict]:
    """
    Types proposés dans le <select> du formulaire : les types actifs, plus le
    type courant de l'élément édité s'il a été archivé depuis — pour ne pas le
    perdre silencieusement à l'enregistrement (même patron que les emplacements
    de rangement sur `admin_fiche.html`).
    """
    types = programme.lister_types(conn, actifs_seulement=True)
    ids_actifs = {t["id_type"] for t in types}
    if id_type_courant is not None and id_type_courant not in ids_actifs:
        archive = programme.get_type(conn, id_type_courant)
        if archive is not None:
            types = types + [archive]
    return types


def _publics_pour_formulaire(conn, coches: list[int]) -> list[dict]:
    """
    Cases « Public visé » du formulaire : les publics actifs, plus ceux déjà
    cochés sur l'élément qui ont été archivés depuis (même règle que
    `_types_pour_formulaire` : rien ne se perd à l'enregistrement). Chaque
    public porte `coche`.
    """
    publics = [p for p in programme.lister_publics(conn, actifs_seulement=False)
               if p["actif"] or p["id_public"] in coches]
    for p in publics:
        p["coche"] = p["id_public"] in coches
    return publics


def _ids(valeurs: list[str]) -> list[int]:
    """Identifiants cochés d'un groupe de cases (saisies non numériques ignorées)."""
    return [i for i in (_int_ou_none(v) for v in valeurs) if i is not None]


def _reglages_evenement() -> tuple[list, str | None]:
    """
    Les deux réglages de l'événement dont ce module a besoin, en UNE seule
    ouverture de la base de PRÊT (où ils vivent tous les deux) :

    - les deux jours couverts par la grille (le jour de l'événement + le
      lendemain), déduits de `parametres.evenement_date` — liste vide si aucune
      date n'est configurée (jamais bloquant : la page l'affiche alors
      clairement plutôt qu'une grille vide sans explication) ;
    - le nom de l'événement, ou None, pour la description des `.ics`.

    Une seule fonction pour les deux : la connexion de prêt était déjà ouverte
    ici, en rouvrir une seconde juste pour le nom serait un travail en double.
    Les appelants qui n'ont besoin que des jours ignorent simplement le second
    membre.
    """
    conn = get_pret_connection()
    try:
        valeur = lire_parametre(conn, "evenement_date")
        nom = lire_nom_evenement(conn)
    finally:
        conn.close()
    if not valeur:
        return [], nom
    try:
        jour1 = datetime.strptime(valeur, "%Y-%m-%d").date()
    except ValueError:
        return [], nom
    return [jour1, jour1 + timedelta(days=1)], nom


def _jours_evenement() -> list:
    """Raccourci : seulement les jours (voir `_reglages_evenement`)."""
    return _reglages_evenement()[0]


# ===========================================================================
# PUBLIC — grille, .ics, aide
# ===========================================================================
def _jour_valide(jour: str, jours_evenement: list) -> date | None:
    """Parse le paramètre `jour` (AAAA-MM-JJ) ; None si absent/invalide/hors fenêtre."""
    if not jour:
        return None
    try:
        valeur = datetime.strptime(jour, "%Y-%m-%d").date()
    except ValueError:
        return None
    return valeur if valeur in jours_evenement else None


@router.get("/programme")
def grille_publique(request: Request, jour: str = "", type: str = "", public: str = ""):
    """
    Grille horaire du week-end (éléments publiés uniquement), filtrable par
    jour, par type et par public visé. Réutilise le rendu de la frise des
    tournois (même structure de données, voir `programme.grille`).
    """
    jours_evenement = _jours_evenement()
    id_type = _int_ou_none(type)
    id_public = _int_ou_none(public)
    jour_choisi = _jour_valide(jour, jours_evenement)
    jours = [jour_choisi] if jour_choisi else jours_evenement

    conn = get_connection()
    try:
        types = programme.lister_types(conn, actifs_seulement=True)
        publics = programme.lister_publics(conn, actifs_seulement=True)
        filtre_type = next((t for t in types if t["id_type"] == id_type), None) if id_type else None
        filtre_public = (next((p for p in publics if p["id_public"] == id_public), None)
                         if id_public else None)
        donnees_grille = programme.grille(
            conn, jours,
            id_type=filtre_type["id_type"] if filtre_type else None,
            id_public=filtre_public["id_public"] if filtre_public else None,
        ) if jours else []
    finally:
        conn.close()

    from app.tournoi.creneau import label_jour

    chips = _puces_filtres(jour_choisi, filtre_type, filtre_public)
    jours_options = [{"value": j.isoformat(), "label": label_jour(j)} for j in jours_evenement]
    return templates.TemplateResponse(
        request, "programme_public.html",
        {"grille": donnees_grille, "jours_evenement": jours_evenement, "jours_options": jours_options,
         "types": types, "publics": publics, "jour": jour_choisi, "filtre_type": filtre_type,
         "filtre_public": filtre_public, "chips": chips, "filtres_actifs": bool(chips)},
    )


def _puces_filtres(jour_choisi, filtre_type, filtre_public=None) -> list[dict]:
    """Puces de retrait des filtres jour/type/public actifs (patron du catalogue)."""
    from app.tournoi.creneau import label_jour

    actifs = {}
    if jour_choisi is not None:
        actifs["jour"] = (jour_choisi.isoformat(), label_jour(jour_choisi))
    if filtre_type is not None:
        actifs["type"] = (filtre_type["id_type"], filtre_type["nom"])
    if filtre_public is not None:
        actifs["public"] = (filtre_public["id_public"], filtre_public["nom"])

    puces = []
    for cle, (_, libelle) in actifs.items():
        params = {k: v for k, (v, _) in actifs.items() if k != cle}
        url = "/programme" + (f"?{urlencode(params)}" if params else "")
        puces.append({"label": libelle, "url": url})
    return puces


@router.get("/programme/{id_element:int}")
def detail(request: Request, id_element: int):
    """
    Page publique d'un élément de programme (patron `tournoi.routes.detail`).
    Un BROUILLON n'est jamais public : traité comme un identifiant inconnu
    (404), pas comme un cas particulier. Un élément ANNULÉ reste accessible,
    avec un bandeau — cohérent avec `/live`, qui l'affiche déjà barré pendant
    sa fenêtre : quelqu'un qui a le lien ou l'a mis à son agenda doit
    apprendre l'annulation, pas tomber sur une page introuvable.
    """
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        if e is not None and e["etat"] == "brouillon":
            e = None
        elem_type = programme.get_type(conn, e["id_type"]) if e and e["id_type"] else None
        publics = programme.libelle_publics(programme.publics_element(conn, id_element)) if e else None
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "programme_detail.html",
        {"e": e, "type": elem_type, "publics": publics},
        status_code=200 if e else 404,
    )


@router.get("/programme/aide")
def aide(request: Request):
    """Aide / mode d'emploi spécifique au module programme (publique)."""
    return templates.TemplateResponse(request, "programme_aide.html", {})


@router.get("/programme/{id_element:int}/agenda.ics")
def agenda_ics(request: Request, id_element: int):
    """
    Télécharge l'événement au format iCalendar (.ics) — « Ajouter à mon
    agenda ». Public, sans donnée personnelle. 404 si introuvable ou sans date.
    """
    # Le nom de l'événement ET celui de l'association vivent dans la base de
    # PRÊT : c'est la route qui va les chercher et les transmet, jamais le
    # service (voir `ical_element`). Le nom de l'association n'est pas un
    # réglage de l'ÉVÉNEMENT : il a son propre accesseur, pas une place dans
    # `_reglages_evenement`.
    nom = _reglages_evenement()[1]
    asso = nom_association()
    conn = get_connection()
    try:
        ics = programme.ical_element(
            conn, id_element, nom_evenement=nom, nom_association=asso
        )
    finally:
        conn.close()
    if ics is None:
        return Response(status_code=404)
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="programme-{id_element}.ics"'},
    )


# ===========================================================================
# BÉNÉVOLE — liste de travail, création, édition, état, duplication, suppression
# ===========================================================================
# ---------------------------------------------------------------------------
# Filtres de la liste de gestion — conservés d'une action à l'autre
# ---------------------------------------------------------------------------
# Les actions de la liste (changer d'état, modifier, dupliquer, supprimer)
# reviennent sur la liste AVEC ses filtres : sur une quarantaine d'éléments,
# perdre le tri à chaque geste rendrait les filtres inutiles. Les filtres
# voyagent dans un champ caché `retour` (chaîne de requête), relu par
# `_filtres_depuis_retour` qui n'en garde que les clés connues : aucune
# redirection ne peut donc être détournée vers une autre adresse.
LIBELLES_ETAT = {"brouillon": "Brouillon", "publie": "Publié", "annule": "Annulé"}


def _filtres_gestion(type: str = "", lieu: str = "", etat: str = "", public: str = "") -> dict:
    """Filtres valides de la liste de gestion (clés absentes quand inactifs)."""
    filtres = {}
    if _int_ou_none(type) is not None:
        filtres["type"] = str(_int_ou_none(type))
    if _int_ou_none(public) is not None:
        filtres["public"] = str(_int_ou_none(public))
    if (lieu or "").strip():
        filtres["lieu"] = lieu.strip()
    if etat in ETATS_PROGRAMME:
        filtres["etat"] = etat
    return filtres


def _filtres_depuis_retour(retour: str) -> dict:
    """Relit le champ caché `retour` ; tout ce qui n'est pas un filtre connu est ignoré."""
    valeurs = parse_qs(retour or "")
    return _filtres_gestion(
        type=(valeurs.get("type") or [""])[0],
        lieu=(valeurs.get("lieu") or [""])[0],
        etat=(valeurs.get("etat") or [""])[0],
        public=(valeurs.get("public") or [""])[0],
    )


def _url_gestion(filtres: dict, **extra) -> str:
    """URL de la liste de gestion, filtres (et éventuel compte rendu) compris."""
    params = {**filtres, **extra}
    return "/programme/gestion" + ("?" + urlencode(params) if params else "")


def _elements_filtres(conn, filtres: dict) -> list[dict]:
    return programme.lister_elements(
        conn, inclure_brouillons=True,
        id_type=_int_ou_none(filtres.get("type", "")),
        lieu=filtres.get("lieu"), etat=filtres.get("etat"),
        id_public=_int_ou_none(filtres.get("public", "")),
    )


@router.get("/programme/gestion")
def gestion(request: Request, _=Depends(exiger_jeton),
            type: str = "", lieu: str = "", etat: str = "", public: str = "",
            fait: str = "", nb: str = ""):
    """
    Liste de travail (brouillons compris) : toutes les actions y sont
    regroupées. Filtrable par type, lieu et état ; « Tout publier » et « Tout
    dépublier » portent sur la liste telle qu'elle est filtrée.
    """
    filtres = _filtres_gestion(type, lieu, etat, public)
    conn = get_connection()
    try:
        elements = _elements_filtres(conn, filtres)
        publics = programme.lister_publics(conn, actifs_seulement=False)
        total = len(programme.lister_elements(conn, inclure_brouillons=True))
        types = programme.lister_types(conn, actifs_seulement=False)
        lieux = programme.lieux_distincts(conn)
    finally:
        conn.close()
    types_par_id = {t["id_type"]: t for t in types}

    # Puces de retrait : chacune ramène la liste sans CE filtre (patron du catalogue).
    puces = []
    if "type" in filtres:
        t = types_par_id.get(int(filtres["type"]))
        libelle = ((t["icone"] + " ") if t and t["icone"] else "") + (t["nom"] if t else "Type inconnu")
        puces.append({"label": libelle, "url": _url_gestion({k: v for k, v in filtres.items() if k != "type"})})
    if "lieu" in filtres:
        puces.append({"label": filtres["lieu"], "url": _url_gestion({k: v for k, v in filtres.items() if k != "lieu"})})
    if "public" in filtres:
        pu = next((p for p in publics if p["id_public"] == int(filtres["public"])), None)
        puces.append({"label": pu["nom"] if pu else "Public inconnu",
                      "url": _url_gestion({k: v for k, v in filtres.items() if k != "public"})})
    if "etat" in filtres:
        puces.append({"label": LIBELLES_ETAT[filtres["etat"]], "url": _url_gestion({k: v for k, v in filtres.items() if k != "etat"})})

    compte_rendu = None
    if fait in programme.SOURCES_EN_LOT and nb.isdigit():
        n = int(nb)
        verbe = "publié" if fait == "publie" else "repassé en brouillon"
        compte_rendu = f"{n} élément{'s' if n > 1 else ''} {verbe}{'s' if n > 1 else ''}."

    return templates.TemplateResponse(
        request, "programme_gestion.html",
        {"elements": elements, "types_par_id": types_par_id, "types": types,
         "lieux": lieux, "publics": publics, "libelles_etat": LIBELLES_ETAT,
         "transitions": programme.TRANSITIONS_PROGRAMME,
         "filtres": filtres, "puces": puces, "total": total,
         "retour": urlencode(filtres),
         "nb_brouillons": sum(1 for e in elements if e["etat"] == "brouillon"),
         "nb_publies": sum(1 for e in elements if e["etat"] == "publie"),
         "compte_rendu": compte_rendu},
    )


def _page_en_lot(request: Request, vers: str, filtres: dict, *, erreur: str | None = None):
    """Page de confirmation d'un changement d'état groupé (liste recalculée)."""
    source = programme.SOURCES_EN_LOT[vers]
    conn = get_connection()
    try:
        concernes = [e for e in _elements_filtres(conn, filtres) if e["etat"] == source]
    finally:
        conn.close()
    if not concernes:
        return RedirectResponse(_url_gestion(filtres), status_code=303)
    return templates.TemplateResponse(
        request, "programme_en_lot.html",
        {"vers": vers, "concernes": concernes, "filtres": filtres,
         "retour": urlencode(filtres), "url_gestion": _url_gestion(filtres),
         "erreur": erreur},
        status_code=503 if erreur else 200,
    )


@router.get("/programme/gestion/en-lot")
def en_lot_confirmation(request: Request, _=Depends(exiger_jeton), vers: str = "",
                        type: str = "", lieu: str = "", etat: str = "", public: str = ""):
    """
    Confirmation de « Tout publier » (`vers=publie` : les brouillons affichés)
    ou de « Tout dépublier » (`vers=brouillon` : les publiés affichés). Les
    éléments annulés ne sont jamais concernés (`programme.SOURCES_EN_LOT`).
    """
    filtres = _filtres_gestion(type, lieu, etat, public)
    if vers not in programme.SOURCES_EN_LOT:
        return RedirectResponse(_url_gestion(filtres), status_code=303)
    return _page_en_lot(request, vers, filtres)


@router.post("/programme/gestion/en-lot")
def en_lot_action(request: Request, _=Depends(exiger_jeton), vers: str = Form(""),
                  ids: list[int] = Form([]), retour: str = Form("")):
    """
    Applique le changement groupé aux éléments CONFIRMÉS (les identifiants de
    la page de confirmation : ce qui change est ce qui a été montré, même si
    quelqu'un a ajouté un brouillon entre-temps). Tout ou rien ; un verrou
    trop long donne un message et un bouton pour réessayer, jamais une erreur
    brute.
    """
    filtres = _filtres_depuis_retour(retour)
    if vers not in programme.SOURCES_EN_LOT:
        return RedirectResponse(_url_gestion(filtres), status_code=303)
    conn = get_connection()
    try:
        changes = programme.changer_etat_en_lot(conn, ids, vers)
    except sqlite3.OperationalError:
        return _page_en_lot(
            request, vers, filtres,
            erreur="La base était occupée, rien n'a été changé. Vous pouvez réessayer.",
        )
    finally:
        conn.close()
    # Une ligne par élément, avec l'action déjà connue du vocabulaire fermé :
    # le journal permet toujours de retrouver qui a publié quoi.
    for e in changes:
        journal.journaliser(
            request, "programme", "programme_etat_change",
            objet=f"{e['intitule']} → {vers}", ref=str(e["id_element"]),
        )
    return RedirectResponse(_url_gestion(filtres, fait=vers, nb=str(len(changes))), status_code=303)


@router.get("/programme/nouveau")
def nouveau_formulaire(request: Request, _=Depends(exiger_jeton)):
    """Formulaire de création d'un élément de programme."""
    conn = get_connection()
    try:
        types = _types_pour_formulaire(conn, None)
        publics = _publics_pour_formulaire(conn, [])
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "programme_form.html",
        {"e": None, "types": types, "publics": publics,
         "valeur_date": "", "valeur_fin": "", "erreur": None},
    )


@router.post("/programme/nouveau")
def nouveau_creer(
    request: Request,
    _=Depends(exiger_jeton),
    intitule: str = Form(""),
    description: str = Form(""),
    id_type: str = Form(""),
    date_heure: str = Form(""),
    heure_fin: str = Form(""),
    lieu: str = Form(""),
    publics: list[str] = Form([]),
    jauge: str = Form(""),
):
    """Crée l'élément (état 'brouillon') puis ouvre la liste de gestion."""
    conn = get_connection()
    try:
        if not intitule.strip():
            types = _types_pour_formulaire(conn, _int_ou_none(id_type))
            return templates.TemplateResponse(
                request, "programme_form.html",
                {"e": None, "types": types,
                 "publics": _publics_pour_formulaire(conn, _ids(publics)),
                 "valeur_date": date_heure, "valeur_fin": heure_fin,
                 "erreur": "L'intitulé est obligatoire."},
                status_code=400,
            )
        debut_utc = local_vers_utc_iso(date_heure.strip() or None)
        fin_utc = local_vers_utc_iso(heure_fin.strip() or None)
        duree_min = programme.duree_depuis_fin(debut_utc, fin_utc)
        id_element = programme.creer_element(
            conn, intitule.strip(),
            description=description,
            id_type=_int_ou_none(id_type),
            date_heure=debut_utc,
            duree_min=duree_min,
            lieu=lieu,
            publics=_ids(publics),
            jauge=_int_ou_none(jauge),
        )
    finally:
        conn.close()
    journal.journaliser(
        request, "programme", "programme_cree",
        objet=intitule.strip(), ref=str(id_element),
    )
    return RedirectResponse("/programme/gestion", status_code=303)


@router.get("/programme/{id_element:int}/editer")
def editer_formulaire(request: Request, id_element: int, _=Depends(exiger_jeton),
                      retour: str = ""):
    """Formulaire d'édition pré-rempli (début + fin déduite de la durée stockée)."""
    filtres = _filtres_depuis_retour(retour)
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        types = _types_pour_formulaire(conn, e["id_type"] if e else None)
        publics = _publics_pour_formulaire(
            conn, [p["id_public"] for p in programme.publics_element(conn, id_element)])
    finally:
        conn.close()
    if e is None:
        return RedirectResponse(_url_gestion(filtres), status_code=303)
    fin_utc = programme.fin_iso(e["date_heure"], e["duree_min"])
    return templates.TemplateResponse(
        request, "programme_form.html",
        {"e": e, "types": types, "publics": publics,
         "valeur_date": iso_utc_vers_datetime_local(e["date_heure"]),
         "valeur_fin": iso_utc_vers_datetime_local(fin_utc),
         "erreur": None,
         "retour": urlencode(filtres), "url_gestion": _url_gestion(filtres)},
    )


@router.post("/programme/{id_element:int}/editer")
def editer_action(
    request: Request,
    id_element: int,
    _=Depends(exiger_jeton),
    intitule: str = Form(""),
    description: str = Form(""),
    id_type: str = Form(""),
    date_heure: str = Form(""),
    heure_fin: str = Form(""),
    lieu: str = Form(""),
    publics: list[str] = Form([]),
    jauge: str = Form(""),
    retour: str = Form(""),
):
    """Applique les modifications d'un élément puis revient à la liste de gestion (filtres compris)."""
    filtres = _filtres_depuis_retour(retour)
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        if e is None:
            return RedirectResponse(_url_gestion(filtres), status_code=303)
        if not intitule.strip():
            types = _types_pour_formulaire(conn, _int_ou_none(id_type))
            return templates.TemplateResponse(
                request, "programme_form.html",
                {"e": e, "types": types,
                 "publics": _publics_pour_formulaire(conn, _ids(publics)),
                 "valeur_date": date_heure, "valeur_fin": heure_fin,
                 "erreur": "L'intitulé est obligatoire.",
                 "retour": urlencode(filtres), "url_gestion": _url_gestion(filtres)},
                status_code=400,
            )
        debut_utc = local_vers_utc_iso(date_heure.strip() or None)
        fin_utc = local_vers_utc_iso(heure_fin.strip() or None)
        duree_min = programme.duree_depuis_fin(debut_utc, fin_utc)
        programme.modifier_element(
            conn, id_element,
            intitule=intitule.strip(),
            description=(description or "").strip() or None,
            id_type=_int_ou_none(id_type),
            date_heure=debut_utc,
            duree_min=duree_min,
            lieu=(lieu or "").strip() or None,
            jauge=_int_ou_none(jauge),
        )
        programme.definir_publics(conn, id_element, _ids(publics))
    finally:
        conn.close()
    journal.journaliser(
        request, "programme", "programme_modifie",
        objet=intitule.strip(), ref=str(id_element),
    )
    return RedirectResponse(_url_gestion(filtres), status_code=303)


@router.post("/programme/{id_element:int}/etat")
def changer_etat_action(request: Request, id_element: int,
                        _=Depends(exiger_jeton), etat: str = Form(""),
                        retour: str = Form("")):
    """Effectue une transition d'état (publier / dépublier / annuler / réinstaurer)."""
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        res = programme.changer_etat(conn, id_element, etat.strip())
    finally:
        conn.close()
    journal.journaliser(
        request, "programme", "programme_etat_change",
        objet=f"{e['intitule']} → {etat.strip()}" if e else etat.strip(),
        ref=str(id_element), ok=bool(res), detail=None if res else "transition_refusee",
    )
    return RedirectResponse(_url_gestion(_filtres_depuis_retour(retour)), status_code=303)


@router.post("/programme/{id_element:int}/dupliquer")
def dupliquer_action(request: Request, id_element: int, _=Depends(exiger_jeton),
                     retour: str = Form("")):
    """
    Duplique l'élément en un clic : la copie repart en BROUILLON, SANS date
    (contrairement à la duplication d'un tournoi, qui demande le nouvel horaire
    dans un formulaire dédié) — on ouvre directement son édition pour fixer le
    nouvel horaire, un atelier se rejouant souvent à une heure différente de
    l'original.
    """
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        nouveau = programme.dupliquer_element(conn, id_element, date_heure=None)
    finally:
        conn.close()
    filtres = _filtres_depuis_retour(retour)
    if nouveau is None:
        return RedirectResponse(_url_gestion(filtres), status_code=303)
    journal.journaliser(
        request, "programme", "programme_cree",
        objet=e["intitule"] if e else None, ref=str(nouveau),
    )
    suite = f"?{urlencode({'retour': urlencode(filtres)})}" if filtres else ""
    return RedirectResponse(f"/programme/{nouveau}/editer{suite}", status_code=303)


@router.get("/programme/{id_element:int}/supprimer")
def supprimer_confirmation(request: Request, id_element: int, _=Depends(exiger_jeton),
                           retour: str = ""):
    """Page de confirmation de suppression (1re des deux confirmations)."""
    filtres = _filtres_depuis_retour(retour)
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
    finally:
        conn.close()
    if e is None:
        return RedirectResponse(_url_gestion(filtres), status_code=303)
    return templates.TemplateResponse(
        request, "programme_supprimer.html",
        {"e": e, "retour": urlencode(filtres), "url_gestion": _url_gestion(filtres)},
    )


@router.post("/programme/{id_element:int}/supprimer")
def supprimer_action(request: Request, id_element: int,
                     _=Depends(exiger_jeton), confirmation: str = Form(""),
                     retour: str = Form("")):
    """Supprime définitivement l'élément (2de confirmation : la case doit être cochée)."""
    filtres = _filtres_depuis_retour(retour)
    if confirmation != "oui":
        suite = f"?{urlencode({'retour': urlencode(filtres)})}" if filtres else ""
        return RedirectResponse(f"/programme/{id_element}/supprimer{suite}", status_code=303)
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        programme.supprimer_element(conn, id_element)
    finally:
        conn.close()
    journal.journaliser(
        request, "programme", "programme_supprime",
        objet=e["intitule"] if e else None, ref=str(id_element),
    )
    return RedirectResponse(_url_gestion(filtres), status_code=303)
