"""
Routes du module « Planning bénévole ».

CONVENTIONS (alignées sur le reste de l'app)
--------------------------------------------
- Pages servies par Jinja2 (`app.templating.templates`), gabarits `planning_*.html`.
- Trois niveaux d'accès (docs/conception-planning.md §3) :
  - PUBLIC : entrée /planning, formulaire de collecte, vue publiée, « mon planning ».
  - ADMIN (mot de passe, `app.admin_auth`) : trame, besoins, préremplissage,
    édition de la grille, états, exports, purge. Garde `_garde` (redirige vers
    /admin si non connecté), comme app/routes/admin.py.
- Connexion à la base SÉPARÉE du planning via `app.planning.db.get_connection`,
  toujours ouverte/fermée dans un try/finally.

CARTE DES URL
-------------
    /planning                                  entrée publique (planning publié)
    /planning/collecte/{ev}                    formulaire de souhaits (+ ?code=)
    /planning/collecte/{ev}/merci              confirmation (affiche le code)
    /planning/mon                              « mon planning » (?code=)
    /planning/admin                 [admin]    liste des événements + création + démo
    /planning/admin/{ev}            [admin]    gestion d'un événement (tout-en-un)
    /planning/admin/{ev}/...        [admin]    actions POST (trame, besoins, grille…)
    /planning/admin/{ev}/export.xlsx|pdf [admin] exports
"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app import admin_auth
from app.planning import demo, exports, services
from app.planning.db import get_connection
from app.services import FUSEAU_LOCAL
from app.templating import templates

router = APIRouter(tags=["planning"])


def _garde(request: Request):
    """Redirige vers la connexion admin si non authentifié, sinon None."""
    if not admin_auth.admin_connecte(request):
        return RedirectResponse("/admin", status_code=303)
    return None


def _int(v, defaut=0) -> int:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return defaut


# ===========================================================================
# PUBLIC — entrée, collecte, vue publiée, mon planning
# ===========================================================================
@router.get("/planning")
def public(request: Request):
    """
    Entrée publique : montre le planning PUBLIÉ (lecture seule) s'il existe, et
    propose le lien de collecte si une édition est en phase de collecte.
    """
    conn = get_connection()
    try:
        publie = services.evenement_publie(conn)
        grille = services.construire_grille(conn, publie["id_evenement"]) if publie else None
        # Événement le plus récent encore en collecte (pour le lien « déclarer »).
        collecte = next(
            (e for e in services.lister_evenements(conn) if e["etat"] == "collecte"),
            None,
        )
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "planning_public.html",
        {"publie": publie, "grille": grille, "collecte": collecte},
    )


@router.get("/planning/aide")
def aide(request: Request):
    """Aide / mode d'emploi du module planning (publique)."""
    return templates.TemplateResponse(request, "planning_aide.html", {})


@router.get("/planning/collecte/{ev:int}")
def collecte_form(request: Request, ev: int, code: str = ""):
    """Formulaire de souhaits. Avec `?code=`, recharge une réponse pour l'éditer."""
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        if evenement is None:
            conn.close()
            return RedirectResponse("/planning", status_code=303)
        postes = services.lister_postes(conn, ev)
        creneaux = services.lister_creneaux(conn, ev)
        benevole = services.get_benevole_par_code(conn, code, ev) if code else None
        dispos = services.dispos_du_benevole(conn, benevole["id_benevole"]) if benevole else set()
        prefs = services.prefs_du_benevole(conn, benevole["id_benevole"]) if benevole else {}
    finally:
        conn.close()
    # Regroupe les créneaux par jour pour l'affichage, dans l'ordre CHRONOLOGIQUE
    # (premier créneau de chaque jour), pas alphabétique — voir M2/idees-ux.md
    # et services.jours_chronologiques (même logique que construire_grille).
    idx: dict[str, dict] = {
        lib: {"libelle": lib, "creneaux": []} for lib in services.jours_chronologiques(creneaux)
    }
    for c in creneaux:
        idx[c["libelle_jour"]]["creneaux"].append(c)
    jours = list(idx.values())
    return templates.TemplateResponse(
        request, "planning_collecte.html",
        {"ev": evenement, "postes": postes, "jours": jours,
         "benevole": benevole, "dispos": dispos, "prefs": prefs,
         "niveaux": services.NIVEAUX_PREFERENCE, "code": code},
    )


@router.post("/planning/collecte/{ev:int}")
async def collecte_post(request: Request, ev: int):
    """Enregistre (ou met à jour) la réponse d'un bénévole, puis confirme."""
    form = await request.form()
    nom = form.get("nom", "")
    contact = form.get("contact", "")
    note = form.get("note", "")
    max_heures = form.get("max_heures", "")
    code = form.get("code", "") or None
    dispos = {_int(v) for v in form.getlist("dispo")}
    preferences = {}
    for cle in form.keys():
        if cle.startswith("pref_"):
            niveau = form.get(cle)
            if niveau:
                preferences[_int(cle.removeprefix("pref_"))] = niveau

    conn = get_connection()
    try:
        r = services.enregistrer_souhaits(
            conn, ev, nom, contact=contact, max_heures=max_heures, note=note,
            dispos=dispos, preferences=preferences, code_modif=code,
        )
    finally:
        conn.close()
    if not r["ok"]:
        return RedirectResponse(f"/planning/collecte/{ev}", status_code=303)
    return RedirectResponse(
        f"/planning/collecte/{ev}/merci?code={r['code']}", status_code=303
    )


@router.get("/planning/collecte/{ev:int}/merci")
def collecte_merci(request: Request, ev: int, code: str = ""):
    """Confirmation : affiche le code de modification (filet de sécurité)."""
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        benevole = services.get_benevole_par_code(conn, code, ev) if code else None
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "planning_collecte_ok.html",
        {"ev": evenement, "code": code, "benevole": benevole},
    )


@router.get("/planning/mon")
def mon_planning(request: Request, code: str = ""):
    """
    « Mon planning » : à partir du code de modification, montre les affectations
    du bénévole si l'événement est publié, sinon un rappel de ses souhaits.
    """
    conn = get_connection()
    try:
        benevole = services.get_benevole_par_code(conn, code) if code else None
        evenement = mon = None
        if benevole:
            evenement = services.get_evenement(conn, benevole["id_evenement"])
            mon = services.planning_du_benevole(conn, benevole["id_benevole"])
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "planning_mon.html",
        {"benevole": benevole, "ev": evenement, "mon": mon, "code": code},
    )


@router.get("/planning/mon.ics")
def mon_planning_ics(code: str = ""):
    """
    Télécharge tout « mon planning » au format iCalendar (.ics) — « Ajouter
    tout mon planning à mon agenda ». Public, sans donnée personnelle.
    404 si code invalide ou aucune affectation (jamais d'erreur brute).
    """
    conn = get_connection()
    try:
        benevole = services.get_benevole_par_code(conn, code) if code else None
        ics = services.ical_planning_benevole(conn, benevole["id_benevole"]) if benevole else None
    finally:
        conn.close()
    if ics is None:
        return Response(status_code=404)
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mon-planning.ics"'},
    )


# ===========================================================================
# ADMIN — liste des événements, création, démo
# ===========================================================================
@router.get("/planning/admin")
def admin_liste(request: Request):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        evenements = services.lister_evenements(conn)
        # Compte des réponses par événement (pour l'aperçu).
        for e in evenements:
            e["nb_reponses"] = services.compter_reponses(conn, e["id_evenement"])
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "planning_admin.html",
        {"evenements": evenements, "message": request.query_params.get("msg")},
    )


@router.post("/planning/admin/creer")
def admin_creer(
    request: Request,
    nom: str = Form(""),
    source: str = Form(""),
    date_debut: str = Form(""),
):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        id_source = int(source) if source.strip().isdigit() else None
        if id_source is not None and services.get_evenement(conn, id_source) is None:
            id_source = None  # source invalide/disparue : jamais bloquant

        if id_source is None:
            ev = services.creer_evenement(conn, nom)
        else:
            decalage = _decalage_depuis_saisie(conn, id_source, date_debut)
            ev = services.dupliquer_trame(conn, id_source, nom, decalage)
    finally:
        conn.close()
    return RedirectResponse(f"/planning/admin/{ev}", status_code=303)


def _decalage_depuis_saisie(conn, id_source: int, date_debut: str) -> int:
    """
    Calcule le décalage (en jours) entre le 1er jour de la trame source et la
    date saisie pour la nouvelle édition. Repli sur 0 (recopie verbatim) si la
    saisie est absente/invalide ou si la source n'a aucun créneau — jamais
    bloquant.
    """
    if not date_debut:
        return 0
    try:
        cible = date.fromisoformat(date_debut)
    except ValueError:
        return 0

    creneaux = services.lister_creneaux(conn, id_source)
    if not creneaux:
        return 0
    premier_debut = min(c["debut"] for c in creneaux)
    try:
        origine = datetime.fromisoformat(premier_debut).astimezone(FUSEAU_LOCAL).date()
    except (ValueError, TypeError):
        return 0
    return (cible - origine).days


@router.post("/planning/admin/demo")
def admin_demo(request: Request):
    """Crée un événement de DÉMONSTRATION (reproduit le tableur du bureau)."""
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        ev = demo.creer_demo(conn)
    finally:
        conn.close()
    return RedirectResponse(f"/planning/admin/{ev}", status_code=303)


# ===========================================================================
# ADMIN — gestion d'un événement (écran tout-en-un)
# ===========================================================================
@router.get("/planning/admin/{ev:int}")
def admin_gerer(request: Request, ev: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        if evenement is None:
            conn.close()
            return RedirectResponse("/planning/admin", status_code=303)
        postes = services.lister_postes(conn, ev)
        creneaux_service = services.lister_creneaux(conn, ev, "poste")
        besoins = services.matrice_besoins(conn, ev)
        benevoles = services.lister_benevoles(conn, ev)
        grille = services.construire_grille(conn, ev)
        couverture = services.analyser_couverture(conn, ev)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "planning_gerer.html",
        {"ev": evenement, "postes": postes, "creneaux_service": creneaux_service,
         "besoins": besoins, "benevoles": benevoles, "grille": grille,
         "couverture": couverture, "message": request.query_params.get("msg")},
    )


def _retour(ev: int, msg: str | None = None, retour: str = "") -> RedirectResponse:
    """Redirige vers `retour` (s'il est fourni et interne), sinon vers la grille."""
    from urllib.parse import quote

    if retour.startswith("/planning/"):
        if msg:
            retour += ("&" if "?" in retour else "?") + f"msg={quote(msg)}"
        return RedirectResponse(retour, status_code=303)
    url = f"/planning/admin/{ev}"
    if msg:
        url += f"?msg={quote(msg)}"
    return RedirectResponse(url, status_code=303)


@router.post("/planning/admin/{ev:int}/poste")
def admin_poste(request: Request, ev: int, nom: str = Form(""),
                experience: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        if nom.strip():
            services.ajouter_poste(conn, ev, nom, demande_experience=bool(experience))
    finally:
        conn.close()
    return _retour(ev)


@router.post("/planning/admin/{ev:int}/poste/{id_poste:int}/supprimer")
def admin_poste_suppr(request: Request, ev: int, id_poste: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        services.supprimer_poste(conn, id_poste)
    finally:
        conn.close()
    return _retour(ev)


@router.post("/planning/admin/{ev:int}/creneau")
def admin_creneau(request: Request, ev: int, libelle_jour: str = Form(""),
                  debut: str = Form(""), fin: str = Form(""),
                  type_creneau: str = Form("poste"), libelle: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        cr = services.ajouter_creneau(conn, ev, libelle_jour, debut, fin,
                                      type_creneau=type_creneau, libelle=libelle)
        msg = None if cr else "Créneau ignoré : horaires invalides."
    finally:
        conn.close()
    return _retour(ev, msg)


@router.post("/planning/admin/{ev:int}/creneau/{id_creneau:int}/supprimer")
def admin_creneau_suppr(request: Request, ev: int, id_creneau: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        services.supprimer_creneau(conn, id_creneau)
    finally:
        conn.close()
    return _retour(ev)


@router.post("/planning/admin/{ev:int}/besoins")
async def admin_besoins(request: Request, ev: int):
    """Enregistre toute la matrice de besoins (champs `b_{creneau}_{poste}`)."""
    if (r := _garde(request)) is not None:
        return r
    form = await request.form()
    conn = get_connection()
    try:
        for cle in form.keys():
            if cle.startswith("b_"):
                _, c, p = cle.split("_")
                services.definir_besoin(conn, int(c), int(p), _int(form.get(cle)))
    finally:
        conn.close()
    return _retour(ev, "Besoins enregistrés.")


@router.post("/planning/admin/{ev:int}/prefiller")
def admin_prefiller(request: Request, ev: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        bilan = services.prefiller(conn, ev)
    finally:
        conn.close()
    msg = (f"Préremplissage : {bilan['places']} affectation(s), "
           f"{bilan['cases_completes']}/{bilan['cases_total']} cases complètes.")
    return _retour(ev, msg)


@router.post("/planning/admin/{ev:int}/affecter")
def admin_affecter(request: Request, ev: int, id_creneau: int = Form(...),
                   id_poste: str = Form(""), id_benevole: int = Form(...),
                   retour: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    poste = _int(id_poste) if id_poste.strip() else None
    conn = get_connection()
    try:
        res = services.affecter(conn, id_creneau, poste, id_benevole,
                                origine="manuel", verrouille=True)
    finally:
        conn.close()
    msg = None if res else "Ce bénévole est déjà affecté sur ce créneau."
    return _retour(ev, msg, retour)


@router.post("/planning/admin/{ev:int}/affectation/{id_aff:int}/retirer")
def admin_aff_retirer(request: Request, ev: int, id_aff: int, retour: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        services.retirer_affectation(conn, id_aff)
    finally:
        conn.close()
    return _retour(ev, retour=retour)


@router.post("/planning/admin/{ev:int}/affectation/{id_aff:int}/verrou")
def admin_aff_verrou(request: Request, ev: int, id_aff: int, retour: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        services.basculer_verrou(conn, id_aff)
    finally:
        conn.close()
    return _retour(ev, retour=retour)


@router.post("/planning/admin/{ev:int}/affectation/{id_aff:int}/remplacer")
def admin_aff_remplacer(request: Request, ev: int, id_aff: int,
                        nouveau: int = Form(...), retour: str = Form("")):
    """Remplace le bénévole d'une affectation par un autre (même case)."""
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        res = services.remplacer_affectation(conn, id_aff, nouveau)
    finally:
        conn.close()
    msg = None if res else "Ce bénévole est déjà affecté sur ce créneau."
    return _retour(ev, msg, retour)


# ---------------------------------------------------------------------------
# Édition « au clic » : page d'une case (créneau × poste) et d'un créneau.
# « Boîte de dialogue » rendue côté serveur (POST classiques, sans JS).
# ---------------------------------------------------------------------------
@router.get("/planning/admin/{ev:int}/case/{id_creneau:int}/{id_poste:int}")
def admin_case(request: Request, ev: int, id_creneau: int, id_poste: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        creneau = services.get_creneau(conn, id_creneau)
        poste = next((p for p in services.lister_postes(conn, ev)
                      if p["id_poste"] == id_poste), None)
        if evenement is None or creneau is None or poste is None:
            conn.close()
            return RedirectResponse(f"/planning/admin/{ev}", status_code=303)
        besoin = services.matrice_besoins(conn, ev).get((id_creneau, id_poste), 0)
        affectes = services.affectations_de_case(conn, id_creneau, id_poste)
        # Bénévoles DÉJÀ occupés sur CE créneau (quel que soit le poste) : exclus
        # des propositions (un bénévole ne peut pas tenir deux postes à la fois).
        occupes = {
            r["id_benevole"] for r in conn.execute(
                "SELECT id_benevole FROM affectations WHERE id_creneau = ?",
                (id_creneau,),
            )
        }
        # Dispo + préférence de chacun pour CE poste (chargés une fois).
        benevoles = services.lister_benevoles(conn, ev)
        info = {}
        for b in benevoles:
            bid = b["id_benevole"]
            dispo = id_creneau in services.dispos_du_benevole(conn, bid)
            niveau = services.prefs_du_benevole(conn, bid).get(id_poste)
            info[bid] = (dispo, niveau)

        def _libre(b):
            return b["id_benevole"] not in occupes

        # Proposables (disponibles, pas « surtout pas ») GROUPÉS par préférence,
        # pour voir d'un coup d'œil qui mettre en priorité sur ce créneau.
        ordre = [("prefere", "⭐ Préféré"), ("ok", "OK"),
                 (None, "Sans préférence"), ("si_vraiment", "Si nécessaire")]
        groupes = []
        for niveau, label in ordre:
            membres = [b for b in benevoles
                       if _libre(b) and info[b["id_benevole"]][0]
                       and info[b["id_benevole"]][1] == niveau]
            if membres:
                groupes.append({"label": label, "benevoles": membres})
        # Au fond : non disponibles sur ce créneau, ou ayant coché « surtout pas ».
        autres = [b for b in benevoles
                  if _libre(b) and (not info[b["id_benevole"]][0]
                                    or info[b["id_benevole"]][1] == "surtout_pas")]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "planning_case.html",
        {"ev": evenement, "creneau": creneau, "poste": poste, "besoin": besoin,
         "affectes": affectes, "groupes": groupes, "autres": autres,
         "message": request.query_params.get("msg")},
    )


@router.get("/planning/admin/{ev:int}/creneau/{id_creneau:int}/editer")
def admin_creneau_editer(request: Request, ev: int, id_creneau: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        creneau = services.get_creneau(conn, id_creneau)
    finally:
        conn.close()
    if evenement is None or creneau is None:
        return RedirectResponse(f"/planning/admin/{ev}", status_code=303)
    return templates.TemplateResponse(
        request, "planning_creneau.html", {"ev": evenement, "creneau": creneau}
    )


@router.post("/planning/admin/{ev:int}/creneau/{id_creneau:int}/editer")
def admin_creneau_modifier(request: Request, ev: int, id_creneau: int,
                           libelle_jour: str = Form(""), debut: str = Form(""),
                           fin: str = Form(""), libelle: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        ok = services.modifier_creneau(conn, id_creneau, libelle_jour=libelle_jour,
                                       debut_local=debut, fin_local=fin,
                                       libelle=libelle or None)
    finally:
        conn.close()
    return _retour(ev, None if ok else "Horaires invalides : créneau inchangé.")


@router.post("/planning/admin/{ev:int}/etat")
def admin_etat(request: Request, ev: int, etat: str = Form("")):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        ok = services.changer_etat(conn, ev, etat)
    finally:
        conn.close()
    return _retour(ev, "État mis à jour." if ok else "Transition d'état refusée.")


@router.post("/planning/admin/{ev:int}/purger")
def admin_purger(request: Request, ev: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        services.purger_evenement(conn, ev)
    finally:
        conn.close()
    return RedirectResponse("/planning/admin?msg=Événement+purgé.", status_code=303)


@router.get("/planning/admin/{ev:int}/export.xlsx")
def admin_export_xlsx(request: Request, ev: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        grille = services.construire_grille(conn, ev)
    finally:
        conn.close()
    contenu = exports.construire_xlsx(grille, evenement["nom"] if evenement else "Planning")
    return Response(
        content=contenu,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="planning.xlsx"'},
    )


@router.get("/planning/admin/{ev:int}/export.pdf")
def admin_export_pdf(request: Request, ev: int):
    if (r := _garde(request)) is not None:
        return r
    conn = get_connection()
    try:
        evenement = services.get_evenement(conn, ev)
        grille = services.construire_grille(conn, ev)
    finally:
        conn.close()
    contenu = exports.construire_pdf(grille, evenement["nom"] if evenement else "Planning")
    return Response(
        content=contenu, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="planning.pdf"'},
    )
