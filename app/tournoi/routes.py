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
from fastapi.responses import RedirectResponse, Response

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


def _parser_manches(form) -> dict[int, tuple[int | None, int | None]]:
    """
    Extrait les manches gagnées (mode BO3) d'un formulaire : champs `ma_<id>`
    (manches A) et `mb_<id>` (manches B). Renvoie {id_rencontre: (a, b)}.
    """
    paires: dict[int, list] = {}
    for cle, valeur in form.items():
        for prefixe, index in (("ma_", 0), ("mb_", 1)):
            if cle.startswith(prefixe):
                try:
                    rid = int(cle.removeprefix(prefixe))
                except ValueError:
                    continue
                paires.setdefault(rid, [None, None])[index] = _int_ou_none(str(valeur))
    return {rid: (v[0], v[1]) for rid, v in paires.items()}


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


@router.get("/tournoi/aide")
def aide(request: Request):
    """Aide / mode d'emploi spécifique au module tournois (publique)."""
    return templates.TemplateResponse(request, "tournoi_aide.html", {})


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
    """
    Page publique d'un tournoi : infos, participants, état d'inscription. Une
    fois le tournoi lancé/terminé en mode high score, le CLASSEMENT remplace la
    simple liste des participants.
    """
    participants, ouverte, restantes = [], False, None
    classement, rondes, tours, vainqueur = None, None, None, None
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is not None:
            participants = services.lister_inscriptions(conn, id_tournoi)
            ouverte = services.inscription_ouverte(conn, t)
            restantes = services.places_restantes(conn, t)
            lance = t["etat"] in ("lance", "termine")
            if lance and t["mode_scoring"] == "high_score":
                classement = services.classement_high_score(conn, id_tournoi)
            elif lance and t["mode_scoring"] == "ronde_suisse":
                classement = services.classement_suisse(conn, id_tournoi)
                rondes = services.toutes_les_rondes(conn, id_tournoi)
            elif lance and t["mode_scoring"] == "elimination":
                tours = services.arbre(conn, id_tournoi)
                vainqueur = services.vainqueur(conn, id_tournoi)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "tournoi_detail.html",
        {"t": t, "participants": participants,
         "inscription_ouverte": ouverte, "places_restantes": restantes,
         "classement": classement, "rondes": rondes,
         "tours": tours, "vainqueur": vainqueur},
        status_code=200 if t else 404,
    )


@router.get("/tournoi/{id_tournoi:int}/agenda.ics")
def agenda_ics(request: Request, id_tournoi: int):
    """
    Télécharge l'événement du tournoi au format iCalendar (.ics) — « Ajouter à
    mon agenda ». Public, sans donnée personnelle. 404 si pas de date.
    """
    conn = get_connection()
    try:
        ics = services.ical_tournoi(conn, id_tournoi)
    finally:
        conn.close()
    if ics is None:
        return Response(status_code=404)
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tournoi-{id_tournoi}.ics"'},
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
):
    """Crée le tournoi (état 'brouillon') puis ouvre son tableau de gestion.

    Le format BO3 n'est PAS choisi ici : il se décide au lancement (voir
    lancer_action), car il ne concerne que les modes à base de matchs.
    """
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
         "transitions": services.TRANSITIONS.get(t["etat"], set()) if t else set(),
         "modes": services.MODES_SCORING},
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


# ===========================================================================
# BÉNÉVOLE — lancement + saisie des scores (modes de scoring)
# ===========================================================================
@router.post("/tournoi/{id_tournoi:int}/lancer")
def lancer_action(request: Request, id_tournoi: int,
                  _=Depends(exiger_jeton), mode_scoring: str = Form(""),
                  nb_rondes: str = Form(""), bo3: str = Form("")):
    """
    Lance le tournoi avec le mode choisi (et l'option BO3). En cas de succès,
    bascule vers l'écran de saisie adapté (scores pour high score, rondes pour la
    ronde suisse, arbre pour l'élimination) ; sinon, revient à la gestion.
    """
    mode = mode_scoring.strip()
    conn = get_connection()
    try:
        res = services.lancer_tournoi(conn, id_tournoi, mode,
                                      _int_ou_none(nb_rondes), bo3=bool(bo3))
    finally:
        conn.close()
    if res.get("ok") and mode == "high_score":
        return RedirectResponse(f"/tournoi/{id_tournoi}/scores", status_code=303)
    if res.get("ok") and mode == "ronde_suisse":
        return RedirectResponse(f"/tournoi/{id_tournoi}/rondes", status_code=303)
    if res.get("ok") and mode == "elimination":
        return RedirectResponse(f"/tournoi/{id_tournoi}/arbre", status_code=303)
    return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)


@router.get("/tournoi/{id_tournoi:int}/scores")
def scores_formulaire(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Écran de saisie des scores (high score)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        lignes = (services.lignes_high_score(conn, id_tournoi)
                  if t and t["mode_scoring"] == "high_score" else [])
    finally:
        conn.close()
    if t is None:
        return RedirectResponse("/tournois", status_code=303)
    if t["mode_scoring"] != "high_score":
        return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)
    return templates.TemplateResponse(
        request, "tournoi_scores.html", {"t": t, "lignes": lignes, "enregistre": False}
    )


@router.post("/tournoi/{id_tournoi:int}/scores")
async def scores_action(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """
    Enregistre les scores saisis. Les champs du formulaire sont nommés
    `score_<id_inscription>` (vide = score effacé/non saisi).
    """
    form = await request.form()
    scores: dict[int, int | None] = {}
    for cle, valeur in form.items():
        if not cle.startswith("score_"):
            continue
        try:
            id_inscription = int(cle.removeprefix("score_"))
        except ValueError:
            continue
        scores[id_inscription] = _int_ou_none(str(valeur))

    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t and t["mode_scoring"] == "high_score":
            services.enregistrer_scores_high_score(conn, id_tournoi, scores)
        lignes = services.lignes_high_score(conn, id_tournoi) if t else []
    finally:
        conn.close()
    if t is None:
        return RedirectResponse("/tournois", status_code=303)
    return templates.TemplateResponse(
        request, "tournoi_scores.html", {"t": t, "lignes": lignes, "enregistre": True}
    )


# --- Ronde suisse : écran des rondes -----------------------------------------
def _contexte_rondes(conn, t, message=None) -> dict:
    """Contexte commun de l'écran des rondes (rondes + classement + état)."""
    id_tournoi = t["id_tournoi"]
    courante = services.ronde_courante(conn, id_tournoi)
    return {
        "t": t,
        "rondes": services.toutes_les_rondes(conn, id_tournoi),
        "classement": services.classement_suisse(conn, id_tournoi),
        "ronde_courante": courante,
        "ronde_courante_complete": services.ronde_complete(conn, id_tournoi, courante) if courante else False,
        "peut_generer": bool(t["nb_rondes"]) and courante < t["nb_rondes"],
        "message": message,
    }


@router.get("/tournoi/{id_tournoi:int}/rondes")
def rondes_formulaire(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Écran bénévole des rondes (saisie des résultats, génération, classement)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            return RedirectResponse("/tournois", status_code=303)
        if t["mode_scoring"] != "ronde_suisse":
            return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)
        contexte = _contexte_rondes(conn, t)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "tournoi_rondes.html", contexte)


@router.post("/tournoi/{id_tournoi:int}/rondes/{ronde:int}/resultats")
async def rondes_resultats(request: Request, id_tournoi: int, ronde: int,
                           _=Depends(exiger_jeton)):
    """
    Enregistre les résultats d'une ronde. En BO3 : manches gagnées (champs
    `ma_<id>`/`mb_<id>`, vainqueur déduit, nul autorisé). Sinon : vainqueur
    direct (champs `res_<id_rencontre>` ∈ {a,b,nul}).
    """
    form = await request.form()
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            return RedirectResponse("/tournois", status_code=303)
        if t["bo3"]:
            services.enregistrer_manches(conn, id_tournoi, ronde,
                                         _parser_manches(form), autoriser_nul=True)
        else:
            resultats = {int(c.removeprefix("res_")): (str(v).strip() or None)
                         for c, v in form.items()
                         if c.startswith("res_") and c.removeprefix("res_").isdigit()}
            services.enregistrer_resultats_suisse(conn, id_tournoi, ronde, resultats)
        contexte = _contexte_rondes(conn, t, message=("succes", "Résultats enregistrés."))
    finally:
        conn.close()
    return templates.TemplateResponse(request, "tournoi_rondes.html", contexte)


@router.post("/tournoi/{id_tournoi:int}/rondes/suivante")
def rondes_suivante(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Génère la ronde suivante (si la courante est complète et qu'il en reste)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            return RedirectResponse("/tournois", status_code=303)
        res = services.generer_ronde_suivante(conn, id_tournoi)
        messages = {
            "incomplete": ("erreur", "Saisissez tous les résultats de la ronde en cours d'abord."),
            "terminee": ("erreur", "Toutes les rondes prévues ont été générées."),
            "mode": ("erreur", "Action indisponible."),
        }
        message = (("succes", f"Ronde {res['ronde']} générée.") if res.get("ok")
                   else messages.get(res.get("raison")))
        contexte = _contexte_rondes(conn, t, message=message)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "tournoi_rondes.html", contexte)


# --- Élimination directe : écran de l'arbre ----------------------------------
def _contexte_arbre(conn, t, message=None) -> dict:
    """Contexte commun de l'écran de l'arbre (tours + vainqueur + état)."""
    id_tournoi = t["id_tournoi"]
    courant = services.ronde_courante(conn, id_tournoi)
    return {
        "t": t,
        "tours": services.arbre(conn, id_tournoi),
        "vainqueur": services.vainqueur(conn, id_tournoi),
        "tour_courant": courant,
        "tour_courant_complet": services.ronde_complete(conn, id_tournoi, courant) if courant else False,
        "peut_generer": bool(t["nb_rondes"]) and courant < t["nb_rondes"],
        "message": message,
    }


@router.get("/tournoi/{id_tournoi:int}/arbre")
def arbre_formulaire(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Écran bénévole de l'arbre (saisie des vainqueurs, génération des tours)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            return RedirectResponse("/tournois", status_code=303)
        if t["mode_scoring"] != "elimination":
            return RedirectResponse(f"/tournoi/{id_tournoi}/gerer", status_code=303)
        contexte = _contexte_arbre(conn, t)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "tournoi_arbre.html", contexte)


@router.post("/tournoi/{id_tournoi:int}/arbre/{tour:int}/resultats")
async def arbre_resultats(request: Request, id_tournoi: int, tour: int,
                          _=Depends(exiger_jeton)):
    """
    Enregistre les vainqueurs d'un tour. En BO3 : manches gagnées (`ma_`/`mb_`,
    pas de nul → égalité = pas de vainqueur). Sinon : vainqueur direct
    (`res_<id_rencontre>` ∈ {a,b}).
    """
    form = await request.form()
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            return RedirectResponse("/tournois", status_code=303)
        if t["bo3"]:
            services.enregistrer_manches(conn, id_tournoi, tour,
                                         _parser_manches(form), autoriser_nul=False)
        else:
            # Pas de match nul en élimination : on n'accepte que 'a' ou 'b'.
            resultats = {int(c.removeprefix("res_")): (str(v).strip() if str(v).strip() in ("a", "b") else None)
                         for c, v in form.items()
                         if c.startswith("res_") and c.removeprefix("res_").isdigit()}
            services.enregistrer_resultats_suisse(conn, id_tournoi, tour, resultats)
        contexte = _contexte_arbre(conn, t, message=("succes", "Résultats enregistrés."))
    finally:
        conn.close()
    return templates.TemplateResponse(request, "tournoi_arbre.html", contexte)


@router.post("/tournoi/{id_tournoi:int}/arbre/suivant")
def arbre_suivant(request: Request, id_tournoi: int, _=Depends(exiger_jeton)):
    """Génère le tour suivant de l'arbre (vainqueurs du tour courant appariés)."""
    conn = get_connection()
    try:
        t = services.get_tournoi(conn, id_tournoi)
        if t is None:
            return RedirectResponse("/tournois", status_code=303)
        res = services.generer_tour_suivant(conn, id_tournoi)
        messages = {
            "incomplete": ("erreur", "Désignez tous les vainqueurs du tour en cours d'abord."),
            "terminee": ("erreur", "La finale a déjà été générée."),
            "mode": ("erreur", "Action indisponible."),
        }
        message = (("succes", f"{services.nom_tour(res['ronde'], t['nb_rondes'])} généré(e).")
                   if res.get("ok") else messages.get(res.get("raison")))
        contexte = _contexte_arbre(conn, t, message=message)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "tournoi_arbre.html", contexte)
