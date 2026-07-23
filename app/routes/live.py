"""
Route du TABLEAU DE BORD TEMPS RÉEL `/live` (affichage salle, écran 16:9).

But : projeter en salle, pendant l'événement, un panorama qui se rafraîchit tout
seul — jeux sortis / disponibles, tournois en cours et à venir, et le flux des
derniers prêts/retours. Page PUBLIQUE en LECTURE SEULE : aucune action, aucun
jeton bénévole requis, aucune donnée personnelle (le numéro de pochette, lié à
une pièce d'identité, n'est volontairement jamais affiché ici).

Deux routes :
- GET /live      : la page (HTML plein écran, mise en page 16:9).
- GET /live/data : les données fraîches au format JSON (interrogé en boucle par
                   la page via fetch(), sans rechargement).

Comme partout dans le projet, AUCUNE logique métier ici : on délègue à
`app.services` (prêt) et `app.tournoi.services` (tournois), on assemble, on rend.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app import services
from app.config import NOM_ASSOCIATION
from app.db import get_connection
from app.services import FUSEAU_LOCAL
from app.templating import templates
from app.tournoi import services as tournoi_services
from app.tournoi.db import get_connection as get_tournoi_connection

router = APIRouter(tags=["live"])

# Fenêtre « prochains tournois » : 2 heures (en minutes).
FENETRE_A_VENIR_MIN = 120
# Nombre de lignes du flux des derniers mouvements.
NB_MOUVEMENTS = 10
# Clé du paramètre « titre de l'écran salle » (réglable en admin) + valeur par
# défaut si rien n'est encore renseigné.
CLE_TITRE = "live_titre"
TITRE_DEFAUT = NOM_ASSOCIATION

# Annonce libre affichée en bandeau sur l'écran de salle (idée 5.2). Une seule
# annonce à la fois, pas d'historique. `CLE_ANNONCE_EXPIRE` est optionnelle :
# horodatage UTC ISO au-delà duquel l'annonce s'auto-masque (calculé à la
# volée, jamais purgé en base — voir `annonce_active`). Sans date, l'annonce
# reste affichée indéfiniment jusqu'à effacement manuel en admin.
CLE_ANNONCE = "live_annonce"
CLE_ANNONCE_EXPIRE = "live_annonce_expire"


def annonce_active(conn) -> str | None:
    """
    Annonce actuellement affichable sur /live, ou None si aucune n'est
    configurée OU si sa durée d'affichage est dépassée. Ne modifie jamais la
    base (l'auto-masquage est un simple calcul de lecture) : le texte reste
    tel quel en admin tant que personne ne le change, pour qu'une annonce
    expirée reste rappelable/modifiable sans avoir à la retaper.
    """
    annonce = services.lire_parametre(conn, CLE_ANNONCE, None)
    if not annonce:
        return None
    expire_iso = services.lire_parametre(conn, CLE_ANNONCE_EXPIRE, None)
    if expire_iso:
        try:
            if datetime.now(timezone.utc) > datetime.fromisoformat(expire_iso):
                return None
        except ValueError:
            pass  # valeur corrompue : jamais bloquant, on affiche plutôt que planter
    return annonce


def _heure_locale(date_heure_utc: str | None) -> str:
    """Horodatage UTC ISO -> 'HH:MM' en heure locale (chaîne vide si invalide)."""
    if not date_heure_utc:
        return ""
    try:
        dt = datetime.fromisoformat(date_heure_utc)
    except (ValueError, TypeError):
        return ""
    return dt.astimezone(FUSEAU_LOCAL).strftime("%H:%M")


def _minutes_avant(date_heure_utc: str | None) -> int | None:
    """
    Minutes avant le début d'un tournoi (arrondi supérieur, jamais négatif),
    ou None si la date est absente/invalide. Calcul de présentation, admis
    dans la route au même titre que `_heure_locale` (point E) : met en
    évidence le tournoi le plus proche sur l'écran de salle.
    """
    if not date_heure_utc:
        return None
    try:
        dt = datetime.fromisoformat(date_heure_utc)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    reste = dt - datetime.now(timezone.utc)
    minutes = -(-int(reste.total_seconds()) // 60)  # arrondi supérieur
    return max(0, minutes)


def _collecter_donnees() -> dict:
    """
    Rassemble toutes les données du tableau de bord (partagé par la page et
    l'endpoint JSON, pour garantir des chiffres identiques).
    """
    # --- Base de PRÊT : disponibilité + derniers mouvements ---
    conn = get_connection()
    try:
        total, disponibles = services.compter_exemplaires_disponibles(conn)
        mouvements = services.derniers_mouvements(conn, NB_MOUVEMENTS)
        titre = services.lire_parametre(conn, CLE_TITRE, TITRE_DEFAUT)
        annonce = annonce_active(conn)
    finally:
        conn.close()

    # --- Base des TOURNOIS : en cours + à venir (2 h) ---
    conn_t = get_tournoi_connection()
    try:
        tournois = tournoi_services.lister_tournois(conn_t, inclure_brouillons=False)
        imminents = tournoi_services.tournois_imminents(conn_t, FENETRE_A_VENIR_MIN)
    finally:
        conn_t.close()

    en_cours = [
        {
            "nom": t["nom"],
            "mode": tournoi_services.MODES_SCORING.get(t["mode_scoring"], "—"),
            "etat": "En cours",
            "nb_inscrits": t["nb_inscrits"],
        }
        for t in tournois
        if t["etat"] == "lance"
    ]

    a_venir = [
        {
            "nom": t["nom"],
            "heure": _heure_locale(t["date_heure"]),
            "minutes_avant": _minutes_avant(t["date_heure"]),
            "places_restantes": t["places_restantes"],
        }
        for t in imminents
    ]

    resultat = {
        "titre": titre,
        "jeux": {"total": total, "disponibles": disponibles,
                 "sortis": total - disponibles},
        "nb_tournois_en_cours": len(en_cours),
        "tournois_en_cours": en_cours,
        "tournois_a_venir": a_venir,
        "mouvements": [
            {
                "type": m["type"],
                "nom": m["nom"],
                "motif": m["motif"],
                "heure": m["heure_locale"],
            }
            for m in mouvements
        ],
        "horodatage": datetime.now(FUSEAU_LOCAL).strftime("%H:%M"),
    }
    # Jamais de champ "annonce" quand il n'y en a pas (ne jamais afficher une
    # valeur absente, cf. rangement) : le bandeau de /live se fie à sa présence.
    if annonce:
        resultat["annonce"] = annonce
    return resultat


@router.get("/live")
def live(request: Request):
    """Page du tableau de bord (rendue une fois ; le contenu se met à jour en JS)."""
    return templates.TemplateResponse(request, "live.html", {"data": _collecter_donnees()})


@router.get("/live/data")
def live_data():
    """Données fraîches du tableau de bord (JSON), interrogées en boucle par la page."""
    return _collecter_donnees()
