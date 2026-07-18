"""
Route des STATISTIQUES (post-événement) — page publique + exports.

Agrégations sans donnée personnelle (spec §7) : total des prêts, palmarès des
jeux les plus / les moins prêtés (par titre, jamais-sortis inclus), histogramme
horaire, et liste détaillée des prêts. Toutes les données proviennent de
`services.collecter_stats`.

Paramètres de requête (combinables) :
    tri    = total | exemplaire     -> métrique des palmarès.
    debut  = 'AAAA-MM-JJTHH:MM'      -> borne basse (heure LOCALE) sur date_sortie.
    fin    = 'AAAA-MM-JJTHH:MM'      -> borne haute (heure locale, EXCLUSIVE).

Exports : /stats/export.xlsx et /stats/export.pdf (mêmes paramètres → mêmes
chiffres que la page, période comprise).

Alias : /stat, /statistique, /statistiques redirigent vers /stats.
"""

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import auth, exports, services
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["stats"])

# Limite de la liste détaillée AFFICHÉE (l'export, lui, prend tout).
LIMITE_PRETS_PAGE = 1000


def _joli(saisie: str | None) -> str:
    """'2026-06-21T20:00' -> '21/06/2026 20:00' (pour le libellé de période)."""
    try:
        return datetime.fromisoformat(saisie).strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return saisie or ""


def _periode_txt(debut: str | None, fin: str | None) -> str:
    """Libellé lisible de la période sélectionnée (heures locales saisies)."""
    if debut and fin:
        return f"du {_joli(debut)} au {_joli(fin)}"
    if debut:
        return f"depuis le {_joli(debut)}"
    if fin:
        return f"jusqu'au {_joli(fin)}"
    return "toutes périodes"


def _params(tri: str, debut: str | None, fin: str | None) -> tuple[str, str | None, str | None]:
    """Normalise les paramètres : métrique connue + bornes nettoyées."""
    metrique = "exemplaire" if tri == "exemplaire" else "total"
    return metrique, (debut or "").strip() or None, (fin or "").strip() or None


@router.get("/stats")
def stats(request: Request, tri: str = "total", debut: str | None = None,
          fin: str | None = None):
    """Page des statistiques (avec filtre de période et liste détaillée)."""
    metrique, debut, fin = _params(tri, debut, fin)
    # Conversion des bornes locales saisies -> UTC pour interroger la base.
    debut_utc = services.local_vers_utc_iso(debut)
    fin_utc = services.local_vers_utc_iso(fin)

    conn = get_connection()
    try:
        data = services.collecter_stats(conn, metrique, debut_utc, fin_utc,
                                         limite_prets=LIMITE_PRETS_PAGE)
        # Vue « actuellement sortis » : indépendante du filtre de période, inclut
        # les tournois (état du parc à l'instant T).
        en_cours = services.lister_prets_en_cours(conn)
    finally:
        conn.close()

    max_heure = max((h["n"] for h in data["par_heure"]), default=0)
    # Query string pour conserver les filtres dans les liens d'export.
    qs = urlencode({k: v for k, v in
                    {"tri": tri, "debut": debut, "fin": fin}.items() if v})
    return templates.TemplateResponse(
        request, "stats.html",
        {"g": data["globales"], "plus": data["plus"], "moins": data["moins"],
         "par_heure": data["par_heure"], "max_heure": max_heure,
         "metrique": metrique, "prets": data["prets"],
         "debut": debut or "", "fin": fin or "",
         "periode_txt": _periode_txt(debut, fin), "qs": qs,
         "sortis_pret": en_cours["pret"], "sortis_tournoi": en_cours["tournoi"]},
    )


def _exporter(tri: str, debut: str | None, fin: str | None):
    """Collecte les données complètes (toutes lignes) pour un export."""
    metrique, debut, fin = _params(tri, debut, fin)
    conn = get_connection()
    try:
        data = services.collecter_stats(
            conn, metrique,
            services.local_vers_utc_iso(debut), services.local_vers_utc_iso(fin),
            limite_prets=None,  # toutes les lignes dans l'export
        )
    finally:
        conn.close()
    return data, _periode_txt(debut, fin)


@router.get("/stats/export.xlsx")
def export_xlsx(request: Request, tri: str = "total", debut: str | None = None,
                fin: str | None = None):
    """Télécharge les statistiques au format Excel (.xlsx)."""
    data, periode = _exporter(tri, debut, fin)
    # Numéro de pochette réservé aux bénévoles/admin (CLAUDE.md, fiche D5) :
    # /stats/export.xlsx est public, donc la colonne dépend du demandeur.
    contenu = exports.construire_xlsx(data, periode,
                                      avec_pochette=auth.peut_ecrire(request))
    return Response(
        content=contenu,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="statistiques-prets.xlsx"'},
    )


@router.get("/stats/export.pdf")
def export_pdf(request: Request, tri: str = "total", debut: str | None = None,
               fin: str | None = None):
    """
    Télécharge les statistiques au format PDF, avec sections au choix.

    Les sections à inclure sont passées en paramètres `sections` (multivalués) :
    synthese, plus, moins, detail. Par défaut (aucune fournie), on inclut
    synthèse + les deux palmarès (détail décoché par défaut côté page).
    """
    sections = set(request.query_params.getlist("sections")) or {"synthese", "plus", "moins"}
    sections &= set(exports.SECTIONS_PDF)  # ne garder que les sections connues
    data, periode = _exporter(tri, debut, fin)
    # Numéro de pochette réservé aux bénévoles/admin (CLAUDE.md, fiche D5) :
    # /stats/export.pdf est public, donc la colonne dépend du demandeur.
    contenu = exports.construire_pdf(data, periode, sections,
                                     avec_pochette=auth.peut_ecrire(request))
    return Response(
        content=contenu,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="statistiques-prets.pdf"'},
    )


# --- Alias : /stat, /statistique, /statistiques -> /stats (filtres conservés) ---
@router.get("/stat")
@router.get("/statistique")
@router.get("/statistiques")
def alias_stats(request: Request):
    """Redirige les variantes d'URL vers /stats en conservant la query string."""
    q = request.url.query
    return RedirectResponse("/stats" + (f"?{q}" if q else ""), status_code=307)
