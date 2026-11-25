"""
JEUX DEMANDÉS — noter au comptoir (bénévoles) et relire au bureau (série
comptoir, lot 11 ; `docs/conception-jeux-demandes.md`).

- `/jeux-demandes` (jeton bénévole) : le champ **Nom du jeu**, sa consigne,
  les suggestions des demandes déjà notées (`<datalist>`, sans JS), le
  bouton **Noter la demande**, et les demandes du jour.
- `/admin/jeux-demandes` (mot de passe) : la liste groupée, fusionner,
  supprimer un groupe, tout purger, exporter.

AUCUN NOM DANS UNE ADRESSE. Le nom est un texte libre (une porte, voir
`app/jeux_demandes.py`) : il ne voyage que dans le CORPS d'un POST, jamais
dans une query string, que nginx écrit dans son journal d'accès. D'où le
pré-remplissage depuis le catalogue par un formulaire (`/jeux-demandes/preparer`)
et non par un lien, et les confirmations du bureau rendues en réponse à un
POST, pas atteintes par un lien qui porterait la clé du groupe.

JOURNAL : un geste, jamais un nom. Les lignes ne portent qu'un NOMBRE en
`objet` (`NOMS_JOURNALISABLES` de `tests/test_jeux_demandes.py`, contrôle
statique) ; un refus porte sa raison fermée (`vide`, `long`) en `detail`.
Un double envoi n'écrit rien.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from app import editions, exports, jeux_demandes, journal, sauvegarde, services
from app.auth import exiger_jeton
from app.db import get_connection
from app.routes.admin import _garde
from app.templating import templates

router = APIRouter(tags=["jeux-demandes"])
router_admin = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Bénévoles
# ---------------------------------------------------------------------------
def _debut_du_jour_utc() -> str | None:
    """Minuit local d'aujourd'hui, en UTC ISO : la borne des « demandes du jour »."""
    jour = datetime.now(services.FUSEAU_LOCAL).date().isoformat()
    return services.local_vers_utc_iso(f"{jour}T00:00")


def _page_benevole(request: Request, *, nom_saisi: str = "", erreur: str | None = None,
                   notee: jeux_demandes.DemandeNotee | None = None,
                   status_code: int = 200):
    """
    Rend la page. `nom_saisi` revient dans le champ (pré-remplissage, refus) ;
    `notee` est l'issue d'un envoi accepté. Le jeu du catalogue de même clé
    est cherché pour le nom saisi OU noté : « Ce jeu est au catalogue (…) »
    s'affiche sans bloquer.
    """
    conn = get_connection()
    try:
        nom_vu = notee.nom if notee else nom_saisi
        cle = notee.cle if notee else services.slug_titre(nom_saisi or "")
        titre = jeux_demandes.au_catalogue(conn, cle) if cle else None
        contexte = {
            "suggestions": jeux_demandes.suggestions(conn),
            "du_jour": jeux_demandes.groupes(conn, _debut_du_jour_utc()),
            "titre_catalogue": titre if nom_vu else None,
        }
    finally:
        conn.close()
    contexte.update({
        "nom_saisi": nom_saisi, "erreur": erreur, "notee": notee,
        "jeton": jeux_demandes.nouveau_jeton(),
        "longueur_max": jeux_demandes.LONGUEUR_MAX_NOM,
        "consigne": jeux_demandes.CONSIGNE,
    })
    return templates.TemplateResponse(request, "jeux_demandes.html", contexte,
                                      status_code=status_code)


@router.get("/jeux-demandes")
def jeux_demandes_page(request: Request, _=Depends(exiger_jeton)):
    """La page, champ vide. Aucun paramètre : un nom ne passe jamais par l'adresse."""
    return _page_benevole(request)


@router.post("/jeux-demandes/preparer")
def jeux_demandes_preparer(request: Request, nom: str = Form(""),
                           _=Depends(exiger_jeton)):
    """
    Arrivée depuis le catalogue (**Noter ce jeu comme demandé**) : la page,
    champ pré-rempli par la recherche. N'ÉCRIT RIEN — le bénévole corrige la
    graphie si besoin, puis appuie sur **Noter la demande**. Un texte
    démesuré est tronqué à la borne du champ : il n'est pas encore une saisie.
    """
    return _page_benevole(request,
                          nom_saisi=services.nettoyer_nom(nom)[:jeux_demandes.LONGUEUR_MAX_NOM])


@router.post("/jeux-demandes")
def jeux_demandes_noter(request: Request, nom: str = Form(""), jeton: str = Form(""),
                        _=Depends(exiger_jeton)):
    """
    Note la demande. Refus (vide, trop long) : la page revient avec le nom
    dans le champ et la phrase en `role="alert"` ; rien n'est écrit, ni en
    base ni au journal (hors la raison fermée). Double envoi : la page dit
    que la demande était déjà notée, rien n'est écrit.
    """
    conn = get_connection()
    try:
        try:
            notee = jeux_demandes.noter(conn, nom, jeton)
        except jeux_demandes.NomRefuse as refus:
            raison = refus.raison
            notee = None
    finally:
        conn.close()
    if notee is None:
        journal.journaliser(request, "pret", "jeu_demande_notee", ok=False, detail=raison)
        return _page_benevole(request, nom_saisi=(nom or "")[:500],
                              erreur=services.MESSAGES_NOM_EPHEMERE[raison],
                              status_code=400)
    if not notee.doublon:
        journal.journaliser(request, "pret", "jeu_demande_notee")
    return _page_benevole(request, notee=notee)


# ---------------------------------------------------------------------------
# Bureau
# ---------------------------------------------------------------------------
EDITION_TOUT = "tout"


def _periode(conn, edition: str | None) -> tuple[str, dict | None, str | None, str | None]:
    """
    La période regardée : `(choix, édition, début UTC, fin UTC)`. Sans
    paramètre, toutes les demandes (le bureau relit tout avant de purger) ;
    une édition inconnue revient à tout, sans erreur.
    """
    choix = (edition or "").strip()
    trouvee = editions.lire(conn, int(choix)) if choix.isdigit() else None
    if trouvee is None:
        return EDITION_TOUT, None, None, None
    debut, fin = editions.periode_locale(trouvee)
    return str(trouvee["id"]), trouvee, services.local_vers_utc_iso(debut), \
        services.local_vers_utc_iso(fin)


def _page_admin(request: Request, edition: str | None = None,
                message: tuple[str, str] | None = None, status_code: int = 200):
    conn = get_connection()
    try:
        choix, trouvee, debut, fin = _periode(conn, edition)
        liste = jeux_demandes.groupes(conn, debut, fin)
        toutes = jeux_demandes.groupes(conn)
        options = [(str(e["id"]), editions.libelle_menu(e)) for e in editions.lister(conn)]
        nb_total = jeux_demandes.total(conn)
    finally:
        conn.close()
    for g in liste:
        g["premiere_txt"] = services.format_local(g["premiere"])
        g["derniere_txt"] = services.format_local(g["derniere"])
    return templates.TemplateResponse(
        request, "admin_jeux_demandes.html",
        {"groupes": liste, "tous_groupes": toutes, "choix": choix, "edition": trouvee,
         "options_editions": options, "nb_total": nb_total, "message": message,
         "url_export": "/admin/jeux-demandes/export.xlsx"
                       + (f"?edition={choix}" if trouvee else "")},
        status_code=status_code)


@router_admin.get("/jeux-demandes")
def admin_jeux_demandes(request: Request, edition: str | None = None):
    """La liste groupée, filtrable par édition (la période nommée des statistiques)."""
    if (garde := _garde(request)):
        return garde
    return _page_admin(request, edition)


MESSAGE_RIEN = ("attention", "Rien n'a changé : ces demandes ont sans doute déjà "
                "été fusionnées ou supprimées depuis une autre page.")


def _groupe(conn, cle: str) -> dict | None:
    return next((g for g in jeux_demandes.groupes(conn) if g["cle"] == cle), None)


@router_admin.post("/jeux-demandes/fusionner")
def admin_jeux_demandes_fusionner(request: Request, source: str = Form(""),
                                  cible: str = Form(""), confirmation: str = Form("")):
    """
    Sans la case cochée : la page de confirmation (ce qui va se passer, en
    nombres). Avec : la fusion. Un groupe disparu entre-temps, ou une cible
    identique à la source : rien n'est écrit, la liste le dit.
    """
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        g_source, g_cible = _groupe(conn, source), _groupe(conn, cible)
        if g_source is None or g_cible is None or source == cible:
            fusionnees = 0
        elif confirmation != "oui":
            return templates.TemplateResponse(
                request, "admin_jeux_demandes_confirmer.html",
                {"geste": "fusionner", "source": g_source, "cible": g_cible,
                 "erreur": "Cochez la case pour confirmer." if confirmation else None},
                status_code=400 if confirmation else 200)
        else:
            fusionnees = jeux_demandes.fusionner(conn, source, cible)
    finally:
        conn.close()
    if not fusionnees:
        return _page_admin(request, message=MESSAGE_RIEN, status_code=409)
    journal.journaliser(request, "admin", "jeux_demandes_fusionnes",
                        objet=f"{fusionnees} {services.pluriel(fusionnees, 'demande', 'demandes')}")
    return _page_admin(request, message=(
        "succes", f"« {g_source['nom']} » fusionné dans « {g_cible['nom']} » : "
        f"{fusionnees} {services.pluriel(fusionnees, 'demande', 'demandes')} "
        f"{services.pluriel(fusionnees, 'déplacée', 'déplacées')}."))


@router_admin.post("/jeux-demandes/supprimer")
def admin_jeux_demandes_supprimer(request: Request, cle: str = Form(""),
                                  confirmation: str = Form("")):
    """Supprime un groupe, après confirmation (définitif, sous `secure_delete`)."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        groupe = _groupe(conn, cle)
        if groupe is None:
            effacees = 0
        elif confirmation != "oui":
            return templates.TemplateResponse(
                request, "admin_jeux_demandes_confirmer.html",
                {"geste": "supprimer", "source": groupe,
                 "erreur": "Cochez la case pour confirmer." if confirmation else None},
                status_code=400 if confirmation else 200)
        else:
            effacees = jeux_demandes.supprimer_groupe(conn, cle)
    finally:
        conn.close()
    if not effacees:
        return _page_admin(request, message=MESSAGE_RIEN, status_code=409)
    journal.journaliser(request, "admin", "jeux_demandes_supprimes",
                        objet=f"{effacees} {services.pluriel(effacees, 'demande', 'demandes')}")
    return _page_admin(request, message=(
        "succes", f"« {groupe['nom']} » supprimé : {effacees} "
        f"{services.pluriel(effacees, 'demande', 'demandes')} "
        f"{services.pluriel(effacees, 'effacée', 'effacées')} définitivement."))


@router_admin.get("/jeux-demandes/purger")
def admin_jeux_demandes_purger_confirmation(request: Request):
    """Page de confirmation de la purge complète (aucun nom dans l'adresse)."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        nb = jeux_demandes.total(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_jeux_demandes_confirmer.html",
        {"geste": "purger", "nb_total": nb, "erreur": None,
         "duree_archives": sauvegarde.DUREE_VIE_ARCHIVES_JOURS})


@router_admin.post("/jeux-demandes/purger")
def admin_jeux_demandes_purger(request: Request, confirmation: str = Form("")):
    """Efface toutes les demandes si la case est cochée ; sinon la confirmation revient."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        if confirmation != "oui":
            return templates.TemplateResponse(
                request, "admin_jeux_demandes_confirmer.html",
                {"geste": "purger", "nb_total": jeux_demandes.total(conn),
                 "erreur": "Cochez la case pour confirmer la purge.",
                 "duree_archives": sauvegarde.DUREE_VIE_ARCHIVES_JOURS},
                status_code=400)
        effacees = jeux_demandes.purger(conn)
    finally:
        conn.close()
    if effacees:
        journal.journaliser(request, "admin", "jeux_demandes_purges",
                            objet=f"{effacees} {services.pluriel(effacees, 'demande', 'demandes')}")
        message = ("succes", f"{effacees} {services.pluriel(effacees, 'demande', 'demandes')} "
                   f"{services.pluriel(effacees, 'effacée', 'effacées')} définitivement.")
    else:
        message = ("attention", "Il n'y avait aucune demande à effacer.")
    return _page_admin(request, message=message)


EN_TETES_EXPORT = ["Jeu demandé", "Demandes", "Première demande", "Dernière demande",
                   "Au catalogue", "Boîtes au catalogue"]


@router_admin.get("/jeux-demandes/export.xlsx")
def admin_jeux_demandes_export(request: Request, edition: str | None = None):
    """
    Les groupes et leur nombre, pour les achats : réservé au bureau, jamais
    depuis /stats. Respecte le filtre d'édition. Journalisé (le nombre de
    lignes) : un fichier sorti du serveur est la seule trace possible.
    """
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        _choix, _ed, debut, fin = _periode(conn, edition)
        liste = jeux_demandes.groupes(conn, debut, fin)
    finally:
        conn.close()
    donnees = [{
        "Jeu demandé": g["nom"], "Demandes": g["nombre"],
        "Première demande": services.format_local(g["premiere"]),
        "Dernière demande": services.format_local(g["derniere"]),
        "Au catalogue": "oui" if g["titre"] else "non",
        "Boîtes au catalogue": g["titre"]["total"] if g["titre"] else "",
    } for g in liste]
    journal.journaliser(request, "admin", "jeux_demandes_exportes",
                        objet=f"{len(donnees)} {services.pluriel(len(donnees), 'jeu', 'jeux')}")
    return Response(
        content=exports.tableau_xlsx(EN_TETES_EXPORT, donnees, "Jeux demandés"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="jeux-demandes.xlsx"'},
    )
