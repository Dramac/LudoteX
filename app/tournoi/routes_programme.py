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
    /programme                              grille publique (filtres jour/type)
    /programme/aide                         aide / mode d'emploi (publique)
    /programme/{id}/agenda.ics              « Ajouter à mon agenda » (publique)
    /programme/gestion         [bénévole]  liste de travail (brouillons compris)
    /programme/nouveau         [bénévole]  formulaire de création
    /programme/{id}/editer     [bénévole]  formulaire d'édition + POST
    /programme/{id}/etat       [bénévole]  transition d'état (POST)
    /programme/{id}/dupliquer  [bénévole]  copie en brouillon, sans date (POST)
    /programme/{id}/supprimer  [bénévole]  page de confirmation (GET) + POST

NOTE : les paramètres d'id utilisent le convertisseur `:int`, ce qui évite toute
collision avec les segments littéraux (`gestion`, `nouveau`, `aide`).

Pas de route `/programme/{id}/gerer` : contrairement aux tournois (participants,
scores, rencontres), un élément de programme n'a que des champs simples — les
actions (modifier, changer d'état, dupliquer, supprimer) vivent directement
dans les lignes de la liste `/programme/gestion`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from app import journal
from app.auth import exiger_jeton
from app.db import get_connection as get_pret_connection
from app.services import local_vers_utc_iso, lire_parametre
from app.templating import templates
from app.tournoi import programme
from app.tournoi.db import get_connection
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


def _jours_evenement() -> list:
    """
    Les deux jours couverts par la grille (le jour de l'événement + le
    lendemain), déduits de `parametres.evenement_date` — réglage stocké dans la
    base de PRÊT, déjà utilisé par la frise des tournois sur l'accueil. Liste
    vide si aucune date n'est configurée (jamais bloquant : la page l'affiche
    alors clairement plutôt qu'une grille vide sans explication).
    """
    conn = get_pret_connection()
    try:
        valeur = lire_parametre(conn, "evenement_date")
    finally:
        conn.close()
    if not valeur:
        return []
    try:
        jour1 = datetime.strptime(valeur, "%Y-%m-%d").date()
    except ValueError:
        return []
    return [jour1, jour1 + timedelta(days=1)]


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
def grille_publique(request: Request, jour: str = "", type: str = ""):
    """
    Grille horaire du week-end (éléments publiés uniquement), filtrable par
    jour et par type. Réutilise le rendu de la frise des tournois (même
    structure de données, voir `programme.grille`).
    """
    jours_evenement = _jours_evenement()
    id_type = _int_ou_none(type)
    jour_choisi = _jour_valide(jour, jours_evenement)
    jours = [jour_choisi] if jour_choisi else jours_evenement

    conn = get_connection()
    try:
        types = programme.lister_types(conn, actifs_seulement=True)
        filtre_type = next((t for t in types if t["id_type"] == id_type), None) if id_type else None
        donnees_grille = programme.grille(conn, jours, id_type=id_type) if jours else []
    finally:
        conn.close()

    from app.tournoi.creneau import label_jour

    chips = _puces_filtres(jour_choisi, filtre_type)
    jours_options = [{"value": j.isoformat(), "label": label_jour(j)} for j in jours_evenement]
    return templates.TemplateResponse(
        request, "programme_public.html",
        {"grille": donnees_grille, "jours_evenement": jours_evenement, "jours_options": jours_options,
         "types": types, "jour": jour_choisi, "filtre_type": filtre_type, "chips": chips,
         "filtres_actifs": bool(chips)},
    )


def _puces_filtres(jour_choisi, filtre_type) -> list[dict]:
    """Puces de retrait des filtres jour/type actifs (patron du catalogue)."""
    from urllib.parse import urlencode

    from app.tournoi.creneau import label_jour

    puces = []
    if jour_choisi is not None:
        params = {"type": filtre_type["id_type"]} if filtre_type else {}
        url = "/programme" + (f"?{urlencode(params)}" if params else "")
        puces.append({"label": label_jour(jour_choisi), "url": url})
    if filtre_type is not None:
        params = {"jour": jour_choisi.isoformat()} if jour_choisi is not None else {}
        url = "/programme" + (f"?{urlencode(params)}" if params else "")
        puces.append({"label": filtre_type["nom"], "url": url})
    return puces


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
    conn = get_connection()
    try:
        ics = programme.ical_element(conn, id_element)
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
@router.get("/programme/gestion")
def gestion(request: Request, _=Depends(exiger_jeton)):
    """Liste de travail (brouillons compris) : toutes les actions y sont regroupées."""
    conn = get_connection()
    try:
        elements = programme.lister_elements(conn, inclure_brouillons=True)
        types_par_id = {t["id_type"]: t for t in programme.lister_types(conn, actifs_seulement=False)}
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "programme_gestion.html",
        {"elements": elements, "types_par_id": types_par_id,
         "transitions": programme.TRANSITIONS_PROGRAMME},
    )


@router.get("/programme/nouveau")
def nouveau_formulaire(request: Request, _=Depends(exiger_jeton)):
    """Formulaire de création d'un élément de programme."""
    conn = get_connection()
    try:
        types = _types_pour_formulaire(conn, None)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "programme_form.html",
        {"e": None, "types": types, "valeur_date": "", "valeur_fin": "", "erreur": None},
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
    public_vise: str = Form(""),
    jauge: str = Form(""),
):
    """Crée l'élément (état 'brouillon') puis ouvre la liste de gestion."""
    conn = get_connection()
    try:
        if not intitule.strip():
            types = _types_pour_formulaire(conn, _int_ou_none(id_type))
            return templates.TemplateResponse(
                request, "programme_form.html",
                {"e": None, "types": types, "valeur_date": date_heure, "valeur_fin": heure_fin,
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
            public_vise=public_vise,
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
def editer_formulaire(request: Request, id_element: int, _=Depends(exiger_jeton)):
    """Formulaire d'édition pré-rempli (début + fin déduite de la durée stockée)."""
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        types = _types_pour_formulaire(conn, e["id_type"] if e else None)
    finally:
        conn.close()
    if e is None:
        return RedirectResponse("/programme/gestion", status_code=303)
    fin_utc = programme.fin_iso(e["date_heure"], e["duree_min"])
    return templates.TemplateResponse(
        request, "programme_form.html",
        {"e": e, "types": types,
         "valeur_date": iso_utc_vers_datetime_local(e["date_heure"]),
         "valeur_fin": iso_utc_vers_datetime_local(fin_utc),
         "erreur": None},
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
    public_vise: str = Form(""),
    jauge: str = Form(""),
):
    """Applique les modifications d'un élément puis revient à la liste de gestion."""
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
        if e is None:
            return RedirectResponse("/programme/gestion", status_code=303)
        if not intitule.strip():
            types = _types_pour_formulaire(conn, _int_ou_none(id_type))
            return templates.TemplateResponse(
                request, "programme_form.html",
                {"e": e, "types": types, "valeur_date": date_heure, "valeur_fin": heure_fin,
                 "erreur": "L'intitulé est obligatoire."},
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
            public_vise=(public_vise or "").strip() or None,
            jauge=_int_ou_none(jauge),
        )
    finally:
        conn.close()
    journal.journaliser(
        request, "programme", "programme_modifie",
        objet=intitule.strip(), ref=str(id_element),
    )
    return RedirectResponse("/programme/gestion", status_code=303)


@router.post("/programme/{id_element:int}/etat")
def changer_etat_action(request: Request, id_element: int,
                        _=Depends(exiger_jeton), etat: str = Form("")):
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
    return RedirectResponse("/programme/gestion", status_code=303)


@router.post("/programme/{id_element:int}/dupliquer")
def dupliquer_action(request: Request, id_element: int, _=Depends(exiger_jeton)):
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
    if nouveau is None:
        return RedirectResponse("/programme/gestion", status_code=303)
    journal.journaliser(
        request, "programme", "programme_cree",
        objet=e["intitule"] if e else None, ref=str(nouveau),
    )
    return RedirectResponse(f"/programme/{nouveau}/editer", status_code=303)


@router.get("/programme/{id_element:int}/supprimer")
def supprimer_confirmation(request: Request, id_element: int, _=Depends(exiger_jeton)):
    """Page de confirmation de suppression (1re des deux confirmations)."""
    conn = get_connection()
    try:
        e = programme.get_element(conn, id_element)
    finally:
        conn.close()
    if e is None:
        return RedirectResponse("/programme/gestion", status_code=303)
    return templates.TemplateResponse(request, "programme_supprimer.html", {"e": e})


@router.post("/programme/{id_element:int}/supprimer")
def supprimer_action(request: Request, id_element: int,
                     _=Depends(exiger_jeton), confirmation: str = Form("")):
    """Supprime définitivement l'élément (2de confirmation : la case doit être cochée)."""
    if confirmation != "oui":
        return RedirectResponse(f"/programme/{id_element}/supprimer", status_code=303)
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
    return RedirectResponse("/programme/gestion", status_code=303)
