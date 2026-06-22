"""
Routes du module « Tournois » — public (lecture + inscription) et bénévole
(création/édition/gestion, derrière le jeton).

CONVENTIONS (alignées sur le reste de l'app)
--------------------------------------------
- Pages servies par Jinja2 (`app.templating.templates`), gabarits `tournoi_*.html`.
- ÉCRITURE bénévole protégée par `Depends(exiger_jeton)` (même jeton que /pret).
- LECTURE publique : liste, page de suivi, inscription, désinscription.
- Connexion à la base SÉPARÉE des tournois via `app.tournoi.db.get_connection`,
  toujours ouverte/fermée dans un try/finally.

CARTE DES URL
-------------
    /tournois                              liste publique
    /tournoi/nouveau            [bénévole] formulaire de création
    /tournoi/desinscription                désinscription par code (?code=…)
    /tournoi/{id}                          page publique (suivi + inscription)
    /tournoi/{id}/inscription              formulaire + POST d'inscription
    /tournoi/{id}/gerer         [bénévole] tableau de gestion
    /tournoi/{id}/editer        [bénévole] formulaire d'édition + POST
    /tournoi/{id}/etat          [bénévole] transition d'état (POST)
    /tournoi/{id}/participant   [bénévole] ajout manuel (POST)
    /tournoi/{id}/participant/{id_inscription}/supprimer [bénévole] (POST)
    /tournoi/{id}/supprimer     [bénévole] page de confirmation (GET) + POST

NOTE : les paramètres d'id utilisent le convertisseur `:int`, ce qui évite toute
collision avec les segments littéraux (`nouveau`, `desinscription`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import auth
from app.auth import exiger_jeton
from app.services import local_vers_utc_iso
from app.templating import templates
from app.tournoi import services
from app.tournoi.db import get_connection

router = APIRouter(tags=["tournoi"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _int_ou_none(v: str) -> int | None:
    """Convertit une saisie en entier positif, ou None si vide/non numérique."""
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


# ===========================================================================
# PUBLIC — liste, page de suivi, inscription, désinscription
# ===========================================================================
@router.get("/tournois")
def liste(request: Request):
    """
    Liste des tournois. Le public ne voit pas les brouillons ; un bénévole
    connecté les voit (pour les gérer).
    """
    benevole = auth.acces_valide(request)
    conn = get_connection()
    try:
        tournois = services.lister_tournois(conn, inclure_brouillons=benevole)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "tournoi_liste.html", {"tournois": tournois}
    )


@router.get("/tournoi/desinscription")
def desinscription_page(request: Request, code: str = ""):
    """
    Page de désinscription : affiche un bouton de confirmation si un `code` est
    fourni (la suppression se fait par POST, pour éviter toute suppression
    accidentelle au simple chargement du lien).
    """
    return templates.TemplateResponse(
        request, "tournoi_desinscription.html",
        {"code": code.strip(), "resultat": None},
    )


@router.post("/tournoi/desinscription")
def desinscription_action(request: Request, code: str = Form("")):
    """Supprime l'inscription correspondant au code, puis affiche le résultat."""
    conn = get_connection()
    try:
        res = services.desinscrire(conn, code)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "tournoi_desinscription.html",
        {"code": code.strip(), "resultat": res},
    )


@router.get("/tournoi/{id_tournoi:int}")
def detail(request: Request, id_tournoi: int):
    """Page publique d'un tournoi : infos, participants, état d'inscription."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            participants, ouverte, restantes = [], False, None
        else:
            participants = services.lister_inscriptions(conn, id_tournoi)
            ouverte = services.inscription_ouverte(conn, t)
            restantes = services.places_restantes(conn, t)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "tournoi_detail.html",
        {"t": t, "participants": participants,
         "inscription_ouverte": ouverte, "places_restantes": restantes},
        status_code=200 if t else 404,
    )


@router.get("/tournoi/{id_tournoi:int}/inscription")
def inscription_formulaire(request: Request, id_tournoi: int):
    """Formulaire d'inscription publique (si les inscriptions sont ouvertes)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        ouverte = bool(t) and services.inscription_ouverte(conn, t)
    finally:
        conn.close()
    if t is None:
        return templates.TemplateResponse(
            request, "tournoi_detail.html",
            {"t": None, "participants": [], "inscription_ouverte": False,
             "places_restantes": None},
            status_code=404,
        )
    if not ouverte:
        return RedirectResponse(f"/tournoi/{id_tournoi}", status_code=303)
    return templates.TemplateResponse(
        request, "tournoi_inscription.html", {"t": t, "erreur": None}
    )


@router.post("/tournoi/{id_tournoi:int}/inscription")
def inscription_action(request: Request, id_tournoi: int, pseudo: str = Form("")):
    """
    Enregistre une inscription publique. En cas de succès, affiche l'écran de
    confirmation AVEC le code de désinscription (RGPD : e-mail jamais stocké).
    """
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        res = services.inscrire(conn, id_tournoi, pseudo) if t else {"ok": False, "raison": "introuvable"}
    finally:
        conn.close()

    if res.get("ok"):
        return templates.TemplateResponse(
            request, "tournoi_inscription_ok.html",
            {"t": t, "pseudo": res["pseudo"], "code": res["code"]},
        )
    messages = {
        "fermee": "Les inscriptions en ligne ne sont pas ouvertes pour ce tournoi.",
        "complet": "Ce tournoi est complet.",
        "pseudo_vide": "Merci d'indiquer un pseudo.",
        "introuvable": "Tournoi introuvable.",
    }
    erreur = messages.get(res.get("raison"), "Inscription impossible.")
    return templates.TemplateResponse(
        request, "tournoi_inscription.html",
        {"t": t, "erreur": erreur}, status_code=400,
    )


# ===========================================================================
# BÉNÉVOLE — création, édition, gestion (protégé par le jeton)
# ===========================================================================
@router.get("/tournoi/nouveau")
def nouveau_formulaire(request: Request, _=Depends(exiger_jeton)):
    """Formulaire de création d'un tournoi."""
    return templates.TemplateResponse(
        request, "tournoi_form.html", {"t": None, "valeur_date": "", "erreur": None}
    )


@router.post("/tournoi/nouveau")
def nouveau_creer(
    request: Request,
    _=Depends(exiger_jeton),
    nom: str = Form(""),
    jeu: str = Form(""),
    date_heure: str = Form(""),
    duree_min: str = Form(""),
    nb_places: str = Form(""),
    emplacement: str = Form(""),
    inscription_en_ligne: str = Form(""),
    bo3: str = Form(""),
):
    """Crée le tournoi (état 'brouillon') puis ouvre son tableau de gestion."""
    if not nom.strip():
        return templates.TemplateResponse(
            request, "tournoi_form.html",
            {"t": None, "valeur_date": date_heure, "erreur": "Le nom est obligatoire."},
            status_code=400,
        )
    conn = get_connection()
    try:
        id_tournoi = services.creer_tournoi(
            conn, nom,
            jeu=jeu,
            date_heure=local_vers_utc_iso(date_heure.strip() or None),
            duree_min=_int_ou_none(duree_min),
            nb_places=_int_ou_none(nb_places),
            emplacement=emplacement,
            inscription_en_ligne=bool(inscription_en_ligne),
            bo3=bool(bo3),
        )
    finally:
        conn.close()
    return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)


@router.get("/tournoi/{id_tournoi:int}/gerer")
def gerer(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Tableau de gestion bénévole : infos, état, participants, actions."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        participants = services.lister_inscriptions(conn, id_tournoi) if t else []
        restantes = services.places_restantes(conn, t) if t else None
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "tournoi_gerer.html",
        {"t": t, "participants": participants, "places_restantes": restantes,
         "transitions": services.TRANSITIONS.get(t["etat"], set()) if t else set()},
        status_code=200 if t else 404,
    )


@router.get("/tournoi/{id_tournoi:int}/editer")
def editer_formulaire(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Formulaire d'édition pré-rempli."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
    finally:
        conn.close()
    if t is None:
        return RedirectResponse("/tournois", status_code=303)
    return templates.TemplateResponse(
        request, "tournoi_form.html",
        {"t": t, "valeur_date": services.iso_utc_vers_datetime_local(t["date_heure"]),
         "erreur": None},
    )


@router.post("/tournoi/{id_tournoi:int}/editer")
def editer_action(
    request: Request,
    id_tournoi: int,
    _=Depends(exiger_jeton),
    nom: str = Form(""),
    jeu: str = Form(""),
    date_heure: str = Form(""),
    duree_min: str = Form(""),
    nb_places: str = Form(""),
    emplacement: str = Form(""),
    inscription_en_ligne: str = Form(""),
    bo3: str = Form(""),
):
    """Applique les modifications d'un tournoi puis revient à la gestion."""
    if not nom.strip():
        conn = get_connection()
        try:
            t = services.get_tournoi(conn, id_tournoi)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "tournoi_form.html",
            {"t": t, "valeur_date": date_heure, "erreur": "Le nom est obligatoire."},
            status_code=400,
        )
    conn = get_connection()
    try:
        services.modifier_tournoi(
            conn, id_tournoi,
            nom=nom.strip(),
            jeu=(jeu or "").strip() or None,
            date_heure=local_vers_utc_iso(date_heure.strip() or None),
            duree_min=_int_ou_none(duree_min),
            nb_places=_int_ou_none(nb_places),
            emplacement=(emplacement or "").strip() or None,
            inscription_en_ligne=1 if inscription_en_ligne else 0,
            bo3=1 if bo3 else 0,
        )
    finally:
        conn.close()
    return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)


@router.post("/tournoi/{id_tournoi:int}/etat")
def changer_etat_action(request: Request, id_tournoi: int,
                        _=Depends(exiger_jeton), etat: str = Form("")):
    """Effectue une transition d'état (ouvrir/fermer les inscriptions, terminer)."""
    conn = get_connection()
    try:
        services.changer_etat(conn, id_tournoi, etat.strip())
    finally:
        conn.close()
    return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)


@router.post("/tournoi/{id_tournoi:int}/participant")
def ajouter_participant_action(request: Request, id_tournoi: int,
                               _=Depends(exiger_jeton), pseudo: str = Form("")):
    """Ajout manuel d'un participant (bénévole)."""
    conn = get_connection()
    try:
        services.ajouter_participant(conn, id_tournoi, pseudo)
    finally:
        conn.close()
    return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)


@router.post("/tournoi/{id_tournoi:int}/participant/{id_inscription:int}/supprimer")
def supprimer_participant_action(request: Request, id_tournoi: int,
                                 id_inscription: int, _=Depends(exiger_jeton)):
    """Retire un participant d'un tournoi (bénévole)."""
    conn = get_connection()
    try:
        services.supprimer_participant(conn, id_inscription)
    finally:
        conn.close()
    return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)


@router.get("/tournoi/{id_tournoi:int}/supprimer")
def supprimer_confirmation(request: Request, id_tournoi: int,
                           _=Depends(exiger_jeton)):
    """Page de confirmation de suppression (1re des deux confirmations)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        nb = services.compter_inscriptions(conn, id_tournoi) if t else 0
    finally:
        conn.close()
    if t is None:
        return RedirectResponse("/tournois", status_code=303)
    return templates.TemplateResponse(
        request, "tournoi_supprimer.html", {"t": t, "nb_inscrits": nb}
    )


@router.post("/tournoi/{id_tournoi:int}/supprimer")
def supprimer_action(request: Request, id_tournoi: int,
                     _=Depends(exiger_jeton), confirmation: str = Form("")):
    """
    Supprime définitivement le tournoi (2de confirmation : la case doit être
    cochée). Sans confirmation, on renvoie à la page de confirmation.
    """
    if confirmation != "oui":
        return RedirectResponse(f"/tournoi/{id_tournoi}/supprimer", status_code=303)
    conn = get_connection()
    try:
        services.supprimer_tournoi(conn, id_tournoi)
    finally:
        conn.close()
    return RedirectResponse("/tournois", status_code=303)
