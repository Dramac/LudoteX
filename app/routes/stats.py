"""
Route des STATISTIQUES (post-événement) — page + exports.

Accès réglé par la visibilité du module « stats » (`garde_module` dans
main.py) : réservé aux bénévoles par défaut, ouvrable au public par le bureau
(lot bilan-2 ; le pourquoi est en commentaire de `MODULES["stats"]`).

Agrégations sans donnée personnelle (spec §7) : total des prêts, le
comptoir heure par heure (lot bilan-4, `app/comptoir.py`), la section « Pour
les achats » (lot bilan-5, `app/achats.py` : les jeux dont toutes les boîtes
étaient dehors, le palmarès des plus prêtés jusqu'à 50 jeux, le nombre de
jeux jamais sortis, dont la liste est dans l'export Excel), la section « Ce
qui plaît » (lot bilan-6, `app/gouts.py` : prêts par jeu par catégorie, par
nombre de joueurs et par âge, durée annoncée comparée à la durée du prêt) et
la section « Organisation » (lot bilan-7a, `app/organisation.py` : erreurs,
retours non scannés, prêts encore ouverts, signalements du carnet, et
repliés sous elle la tombola, les tournois et le programme, la liste
détaillée des prêts). Les données des sections publiques proviennent de
`services.collecter_stats`.

LA SECTION « ORGANISATION » N'EST RENDUE QU'AUX BÉNÉVOLES (lot bilan-7a,
`auth.peut_ecrire`, administrateur connecté compris), même quand le bureau a
rouvert `/stats` au public : elle parle du travail des bénévoles et des
boîtes abîmées, et le carnet est `jamais_public`. Pour un visiteur, rien
n'en est calculé (`_organisation` rend None) : ni signalements, ni tournois.
La tombola et la liste détaillée, repliées sous elle, disparaissent donc
aussi pour un visiteur. Les exports suivront la même règle au lot bilan-7b.
Les tournois et le programme vivent dans une autre base : c'est cette route
qui la lit et transmet des comptes (`_tournois_programme`), jamais un
service des statistiques. Le palmarès des moins prêtés a quitté la page ;
il reste dans le PDF jusqu'au lot bilan-7.

Paramètres de requête (combinables) :
    tri     = total | exemplaire     -> métrique des palmarès.
    edition = <id> | tout | autre    -> la période (lot bilan-3, voir plus bas).
    debut   = 'AAAA-MM-JJTHH:MM'     -> borne basse (heure LOCALE) sur date_sortie.
    fin     = 'AAAA-MM-JJTHH:MM'     -> borne haute (heure locale, EXCLUSIVE).

LA PÉRIODE (lot bilan-3, `_choisir_periode`). `edition` est résolu en `debut`
/ `fin` par la route, AVANT tout calcul : les services, la tombola, les
exports et le libellé reçoivent la même paire qu'avant ce lot.
- `edition=<id>` : les jours de cette édition (`editions.periode_locale`) ;
  `debut` et `fin` sont alors IGNORÉS — le menu l'emporte.
- `edition=tout` : toute la période. `edition=autre` : `debut` / `fin`.
- sans `edition` : `debut` / `fin` s'il y en a (liens d'avant ce lot) ;
  sinon l'édition par défaut (`editions.par_defaut`), et à défaut toute la
  période — une base sans édition se comporte exactement comme avant.
- une `edition` inconnue, supprimée ou forgée : toute la période, avec une
  phrase qui le dit. Jamais de 404 ni de 500.

Exports : /stats/export.xlsx et /stats/export.pdf (mêmes paramètres → mêmes
chiffres que la page, période comprise).

Alias : /stat, /statistique, /statistiques redirigent vers /stats.

« Jeux actuellement sortis » n'est plus ici (lot bilan-1) : c'est la page
bénévole `/sortis` (app/routes/sortis.py). Une recherche enregistrée en 1.34
(`/stats?q=…`) y est redirigée.
"""

import sqlite3
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import (achats, auth, comptoir, editions, exports, gouts, organisation,
                 services, tombola)
from app.db import get_connection
from app.modules import module_visible
from app.templating import templates
from app.tournoi import db as tournoi_db
from app.tournoi import programme as programme_services
from app.tournoi import services as tournoi_services

router = APIRouter(tags=["stats"])

# Limite de la liste détaillée AFFICHÉE (l'export, lui, prend tout).
LIMITE_PRETS_PAGE = 1000

# Sections du PDF cochées par défaut. « tombola » s'y ajoute seulement quand
# la section existe (`_tombola`).
SECTIONS_PDF_DEFAUT = ("synthese", "plus", "moins")


def _tombola(request: Request, conn, debut_utc: str | None,
             fin_utc: str | None) -> dict | None:
    """
    Les statistiques de la tombola pour la période, ou None : module non
    visible pour ce visiteur (fiche A3 : on ne calcule pas les données d'un
    module masqué), ou aucun tirage dans la période. C'est la ROUTE qui lit
    et transmet à `exports`, comme pour la couleur. Même règle pour la page
    et les deux exports.
    """
    if not module_visible(request, "tombola"):
        return None
    return tombola.statistiques(conn, debut_utc, fin_utc)


def _tournois_programme(request: Request, debut_utc: str | None,
                        fin_utc: str | None) -> dict | None:
    """
    Les comptes des tournois et du programme pour la période, lus dans la
    base des TOURNOIS par la route (invariant des trois bases), ou None si
    les deux modules sont masqués pour ce visiteur : rien n'est alors calculé
    (motif « fiche A3 », comme `_tombola`).

    Une base absente ou illisible ne lève jamais : le bloc dit qu'il n'a pas
    pu être calculé (`illisible`). Une base absente n'est pas créée ici :
    on vérifie le fichier avant d'ouvrir, une lecture ne doit rien écrire.

    Returns:
        {"voir_tournois", "voir_programme", "illisible",
         "tournois": `tournoi.services.bilan_periode` ou None,
         "programme": `programme.compter_publies` ou None}
    """
    voir_tournois = module_visible(request, "tournois")
    voir_programme = module_visible(request, "programme")
    if not (voir_tournois or voir_programme):
        return None
    resultat = {"voir_tournois": voir_tournois, "voir_programme": voir_programme,
                "illisible": False, "tournois": None, "programme": None}
    try:
        if not tournoi_db.get_database_path().is_file():
            raise FileNotFoundError("base des tournois absente")
        conn_t = tournoi_db.get_connection()
        try:
            if voir_tournois:
                resultat["tournois"] = tournoi_services.bilan_periode(conn_t, debut_utc, fin_utc)
            if voir_programme:
                resultat["programme"] = programme_services.compter_publies(conn_t, debut_utc, fin_utc)
        finally:
            conn_t.close()
    except (sqlite3.Error, OSError, ValueError):
        resultat.update(illisible=True, tournois=None, programme=None)
    return resultat


def _organisation(request: Request, conn, debut_utc: str | None,
                  fin_utc: str | None) -> dict | None:
    """
    La section « Organisation » (lot bilan-7a), ou None pour un visiteur :
    elle n'est rendue qu'aux bénévoles (règle en tête de module), et rien
    n'en est calculé pour les autres.

    Les erreurs, retours non scannés et prêts encore ouverts ne sont pas
    recalculés : ce sont ceux de la synthèse (`g`), déjà passés au gabarit.

    Returns:
        {"signalements", "voir_carnet", "tournois_programme",
         "sorties_tournoi" (None si le module tournois est masqué),
         "periode_finie"}
    """
    if not auth.peut_ecrire(request):
        return None
    tp = _tournois_programme(request, debut_utc, fin_utc)
    return {
        "signalements": organisation.signalements(conn, debut_utc, fin_utc),
        "voir_carnet": module_visible(request, "maintenance"),
        "tournois_programme": tp,
        "sorties_tournoi": (organisation.sorties_tournoi(conn, debut_utc, fin_utc)
                            if tp and tp["voir_tournois"] else None),
        # Une période dont la fin est passée : des prêts encore ouverts y
        # veulent dire que la clôture n'a pas été faite.
        "periode_finie": bool(fin_utc) and fin_utc <= services.maintenant(),
    }


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


# Valeurs du menu « Période » qui ne sont pas des éditions.
EDITION_TOUT = "tout"
EDITION_AUTRE = "autre"
MESSAGE_EDITION_INCONNUE = ("Cette édition n'existe pas ou a été supprimée : "
                            "les chiffres portent sur toute la période.")


def _choisir_periode(conn, edition: str | None, debut: str | None,
                     fin: str | None, aujourd_hui=None) -> dict:
    """
    Résout le choix de période (règles en tête de module).

    Returns:
        {"choix": "<id>" | "tout" | "autre", "edition": dict | None,
         "debut", "fin" (heures locales saisies, ou None),
         "message": phrase à afficher, ou None}
    """
    edition = (edition or "").strip()
    if not edition:
        if debut or fin:
            edition = EDITION_AUTRE
        else:
            defaut = editions.par_defaut(conn, aujourd_hui)
            edition = str(defaut["id"]) if defaut else EDITION_TOUT
    if edition == EDITION_AUTRE:
        return {"choix": EDITION_AUTRE, "edition": None, "debut": debut,
                "fin": fin, "message": None}
    if edition == EDITION_TOUT:
        return {"choix": EDITION_TOUT, "edition": None, "debut": None,
                "fin": None, "message": None}
    trouvee = editions.lire(conn, int(edition)) if edition.isdigit() else None
    if trouvee is None:
        return {"choix": EDITION_TOUT, "edition": None, "debut": None,
                "fin": None, "message": MESSAGE_EDITION_INCONNUE}
    debut, fin = editions.periode_locale(trouvee)
    return {"choix": str(trouvee["id"]), "edition": trouvee, "debut": debut,
            "fin": fin, "message": None}


def _libelle(periode: dict) -> str:
    """Le libellé de la période : l'édition nommée, sinon les heures saisies."""
    if periode["edition"]:
        return editions.libelle_periode(periode["edition"])
    return _periode_txt(periode["debut"], periode["fin"])


def _qs_periode(periode: dict) -> dict:
    """
    Les paramètres qui reproduisent ce choix dans un lien (exports, bascule
    du classement) : toujours explicites, pour qu'un export téléchargé
    demain porte la même période que la page vue aujourd'hui.
    """
    qs = {"edition": periode["choix"]}
    if periode["choix"] == EDITION_AUTRE:
        qs.update({k: periode[k] for k in ("debut", "fin") if periode[k]})
    return qs


@router.get("/stats")
def stats(request: Request, tri: str = "total", edition: str | None = None,
          debut: str | None = None, fin: str | None = None):
    """
    Page des statistiques (avec choix de la période et liste détaillée).

    Un paramètre `q` vient d'un ancien lien : la recherche des jeux sortis
    (1.34), partie avec sa section sur `/sortis`. On l'y renvoie, avec le
    même code que les alias ; sans jeton, `/sortis` affiche « accès
    réservé » et son lien vers le catalogue.
    """
    if "q" in request.query_params:
        recherche = urlencode({"q": request.query_params["q"]})
        return RedirectResponse(f"/sortis?{recherche}", status_code=307)
    metrique, debut, fin = _params(tri, debut, fin)

    conn = get_connection()
    try:
        periode = _choisir_periode(conn, edition, debut, fin)
        liste_editions = editions.lister(conn)
        # Conversion des bornes locales -> UTC pour interroger la base.
        debut_utc = services.local_vers_utc_iso(periode["debut"])
        fin_utc = services.local_vers_utc_iso(periode["fin"])
        data = services.collecter_stats(conn, metrique, debut_utc, fin_utc,
                                         limite_prets=LIMITE_PRETS_PAGE)
        stats_tombola = _tombola(request, conn, debut_utc, fin_utc)
        stats_organisation = _organisation(request, conn, debut_utc, fin_utc)
    finally:
        conn.close()

    # Query string pour conserver le tri et la période dans les liens d'export,
    # et la période seule dans la bascule du classement.
    qs_periode = _qs_periode(periode)
    qs = urlencode({"tri": metrique, **qs_periode})
    return templates.TemplateResponse(
        request, "stats.html",
        {"g": data["globales"], "plus": data["plus"], "moins": data["moins"],
         "comptoir": data["comptoir"],
         "graphiques": comptoir.geometrie(data["comptoir"]), "cpt": comptoir,
         "achats": data["achats"], "ach": achats,
         "gouts": data["gouts"], "gts": gouts,
         "palmares_visible": services.LIMITE_PALMARES_VISIBLE,
         "metrique": metrique, "prets": data["prets"],
         "choix": periode["choix"], "message_periode": periode["message"],
         "options_editions": [{"id": str(e["id"]), "libelle": editions.libelle_menu(e)}
                              for e in liste_editions],
         "debut": (periode["debut"] or "") if periode["choix"] == EDITION_AUTRE else "",
         "fin": (periode["fin"] or "") if periode["choix"] == EDITION_AUTRE else "",
         "periode_txt": _libelle(periode), "qs": qs,
         "periode_qs": urlencode(qs_periode), "qs_periode": qs_periode,
         "tombola": stats_tombola,
         "org": stats_organisation, "orga": organisation,
         "libelle_sessions_tombola": exports.TITRE_TOMBOLA_SESSIONS},
    )


def _exporter(request: Request, tri: str, edition: str | None,
              debut: str | None, fin: str | None):
    """
    Collecte les données complètes (toutes lignes) pour un export, et celles
    de la tombola (None si la section n'a pas lieu d'être). Même résolution
    de la période que la page : mêmes paramètres, mêmes chiffres.
    """
    metrique, debut, fin = _params(tri, debut, fin)
    conn = get_connection()
    try:
        periode = _choisir_periode(conn, edition, debut, fin)
        debut_utc = services.local_vers_utc_iso(periode["debut"])
        fin_utc = services.local_vers_utc_iso(periode["fin"])
        data = services.collecter_stats(
            conn, metrique, debut_utc, fin_utc,
            limite_prets=None,  # toutes les lignes dans l'export
        )
        stats_tombola = _tombola(request, conn, debut_utc, fin_utc)
        # La feuille « Jamais sortis » : de quoi retrouver chaque boîte
        # (emplacements, date d'achat). Lu pour l'export seulement.
        data["achats"]["jamais_detail"] = achats.detailler_jamais_sortis(
            conn, data["achats"]["jamais_sortis"])
    finally:
        conn.close()
    return data, _libelle(periode), stats_tombola


@router.get("/stats/export.xlsx")
def export_xlsx(request: Request, tri: str = "total", edition: str | None = None,
                debut: str | None = None, fin: str | None = None):
    """Télécharge les statistiques au format Excel (.xlsx)."""
    data, periode, stats_tombola = _exporter(request, tri, edition, debut, fin)
    contenu = exports.construire_xlsx(data, periode, tombola=stats_tombola)
    return Response(
        content=contenu,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="statistiques-prets.xlsx"'},
    )


@router.get("/stats/export.pdf")
def export_pdf(request: Request, tri: str = "total", edition: str | None = None,
               debut: str | None = None, fin: str | None = None):
    """
    Télécharge les statistiques au format PDF, avec sections au choix.

    Les sections à inclure sont passées en paramètres `sections` (multivalués) :
    synthese, plus, moins, tombola, detail. Par défaut (aucune fournie), on
    inclut synthèse + les deux palmarès, et la tombola quand elle a des
    tirages dans la période et que son module est visible (détail décoché
    par défaut côté page). Une section « tombola » demandée sans données ne
    produit rien.
    """
    data, periode, stats_tombola = _exporter(request, tri, edition, debut, fin)
    defaut = set(SECTIONS_PDF_DEFAUT) | ({"tombola"} if stats_tombola else set())
    sections = set(request.query_params.getlist("sections")) or defaut
    sections &= set(exports.SECTIONS_PDF)  # ne garder que les sections connues
    # Couleur d'identité (lot 3c) : la ROUTE la lit et la transmet — voir la
    # note en tête d'app/exports.py. `theme_association()` ne lève jamais et
    # renvoie toujours un `#rrggbb` effectif (l'anthracite par défaut).
    couleur = services.theme_association()["couleur"]
    contenu = exports.construire_pdf(data, periode, sections, couleur,
                                     tombola=stats_tombola)
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
