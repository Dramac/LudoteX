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

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app import services
from app.db import get_connection
from app.modules import lire_etat_module
from app.services import FUSEAU_LOCAL
from app.templating import templates
from app.tournoi import programme
from app.tournoi import services as tournoi_services
from app.tournoi.db import get_connection as get_tournoi_connection

router = APIRouter(tags=["live"])

# Fenêtre « prochains tournois » : 2 heures (en minutes).
FENETRE_A_VENIR_MIN = 120
# Nombre de lignes du flux des derniers mouvements. Le flux occupe désormais un
# demi-bloc de la zone haute (et non plus une colonne pleine hauteur) : au-delà,
# les lignes en trop ne tiendraient de toute façon pas à l'écran — la page les
# tronque avec un « et N autres… », mais autant ne pas les transporter.
NB_MOUVEMENTS = 8
# Le titre de l'écran de salle n'a plus de réglage à lui : c'est le NOM DE
# L'ÉVÉNEMENT (« Gestion de l'événement »), et à défaut le nom de l'association.
# L'ancienne clé `live_titre` a été reprise dans `evenement_nom` par
# `db._migrer_titre_live_vers_nom_evenement` ; elle n'a plus aucun lecteur.

# Annonce libre affichée en bandeau sur l'écran de salle (idée 5.2). Une seule
# annonce à la fois, pas d'historique. `CLE_ANNONCE_EXPIRE` est optionnelle :
# horodatage UTC ISO au-delà duquel l'annonce s'auto-masque (calculé à la
# volée, jamais purgé en base — voir `annonce_active`). Sans date, l'annonce
# reste affichée indéfiniment jusqu'à effacement manuel en admin.
CLE_ANNONCE = "live_annonce"
CLE_ANNONCE_EXPIRE = "live_annonce_expire"

# Alerte tournoi « rapportez les exemplaires » (docs/conception-alerte-tournoi.md).
# Calcul à la lecture, aucune écriture en base : exactement le patron de
# l'annonce libre ci-dessus. Les deux délais ont un défaut CODE (pas une ligne
# écrite en base au démarrage) : une base existante se comporte comme s'ils y
# étaient déjà. Le modèle de message, lui, n'a AUCUN défaut implicite (D10) :
# tant que le bureau n'a rien saisi, `alerte_tournoi` ci-dessous renvoie
# toujours None.
CLE_ALERTE_MESSAGE = "alerte_tournoi_message"
CLE_ALERTE_DELAI_MIN = "alerte_tournoi_delai_min"
CLE_ALERTE_DELAI_MAX = "alerte_tournoi_delai_max"
DELAI_MIN_DEFAUT = 15
DELAI_MAX_DEFAUT = 90
JETONS_ALERTE = ("jeu", "minutes", "heure", "lieu")
# Borne haute des deux délais, en minutes (24 h) : au-delà, le bureau s'est
# trompé d'unité. Refusée à l'enregistrement (/admin/ecran-salle), jamais à la
# lecture — `alerte_tournoi` reste défensif quoi qu'il trouve en base.
DELAI_BORNE_MAX = 1440
# Texte PROPOSÉ sous le champ en administration, repris en un clic. Ce n'est
# pas un défaut : tant que le bureau n'a rien enregistré, aucune alerte
# n'apparaît (D10). Une mise à jour ne doit jamais faire surgir toute seule,
# sur l'écran de la salle, une phrase que personne n'a relue.
MESSAGE_ALERTE_PROPOSE = (
    "Le tournoi de {jeu} commence dans {minutes} minutes — merci de rapporter "
    "tous les exemplaires au stand de prêt."
)
# Un jeton = une paire d'accolades et ce qu'elles contiennent. Une accolade
# SOLITAIRE n'est volontairement pas reconnue : elle ne casse rien à
# l'affichage (voir `formater_alerte`) et refuser un texte pour cela serait
# incompréhensible côté bureau.
_MOTIF_JETON = re.compile(r"\{[^{}]*\}")

# ---------------------------------------------------------------------------
# Panneaux affichables, réglables depuis /admin/ecran-salle
# ---------------------------------------------------------------------------
# Le bureau peut éteindre chaque bloc de l'écran projeté (un week-end sans
# tournoi, un écran d'annonces seules, un flux de prêts qu'on ne souhaite pas
# projeter en continu). Défaut : TOUT est affiché — une base existante, où
# aucune de ces clés n'a jamais été écrite, se comporte exactement comme avant.
#
# ⚠️ Ne pas confondre avec /admin/fonctionnalites : l'état d'un MODULE dit si
# la fonctionnalité existe pour toute l'application ; ce réglage-ci dit ce que
# CET écran projeté montre. Le module l'emporte toujours (voir
# `panneaux_actifs`).
CLES_PANNEAUX = {
    "chiffres":   "live_panneau_chiffres",
    "tournois":   "live_panneau_tournois",
    "programme":  "live_panneau_programme",
    "mouvements": "live_panneau_mouvements",
}


def titre_ecran(conn) -> str:
    """
    Le texte affiché en haut de l'écran projeté : le NOM DE L'ÉVÉNEMENT s'il est
    renseigné, sinon le nom de l'association.

    UN SEUL RÉGLAGE, ET C'EST VOULU. Il a existé un champ « Titre de l'écran de
    salle » distinct, qui l'emportait sur le nom de l'événement : deux réglages
    sur deux pages disant presque toujours la même chose, dont l'un rendait
    l'autre sans effet. Pire, le champ était prérempli avec la valeur par défaut,
    si bien qu'enregistrer une annonce suffisait à figer le nom de l'association
    comme titre explicite — après quoi renseigner le nom de l'événement ne
    changeait plus rien à l'écran, sans que rien ne l'explique. Le champ a été
    supprimé et son ancienne valeur reprise (voir
    `db._migrer_titre_live_vers_nom_evenement`).

    C'est aussi la seule façon dont le nom de l'événement apparaît sur /live :
    pas de ligne à lui, la refonte du 06/08 a rendu cette hauteur au contenu
    utile.
    """
    return services.lire_nom_evenement(conn) or services.lire_nom_association(conn)


def reglages_panneaux(conn) -> dict[str, bool]:
    """
    Les réglages TELS QUE SAISIS en administration (sans la précédence des
    modules) : c'est ce que le formulaire /admin/ecran-salle doit réafficher,
    pour ne pas donner l'impression d'avoir perdu un choix du bureau quand un
    module est désactivé par ailleurs.
    """
    return {
        nom: services.lire_parametre(conn, cle, "1") != "0"
        for nom, cle in CLES_PANNEAUX.items()
    }


def panneaux_actifs(conn) -> dict[str, bool]:
    """
    Les panneaux RÉELLEMENT affichés sur /live : les réglages ci-dessus, plus
    la précédence d'un module désactivé (qui l'emporte toujours).

    Corrige au passage un défaut préexistant : /live interrogeait la base des
    tournois sans vérifier l'état du module `tournois`, alors que la page
    d'accueil, elle, saute entièrement ce calcul (fiche A3). L'écran de salle
    annonçait donc des tournois d'un module masqué.
    """
    actifs = reglages_panneaux(conn)
    if lire_etat_module(conn, "tournois") == "desactive":
        actifs["tournois"] = False
    if lire_etat_module(conn, "programme") == "desactive":
        actifs["programme"] = False
    return actifs


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


def formater_alerte(modele: str, tournoi: dict, minutes: int) -> str:
    """
    Substitue les jetons `{jeu}`, `{minutes}`, `{heure}`, `{lieu}` dans
    `modele`, JETON PAR JETON — jamais `str.format` : une accolade solitaire
    dans un texte saisi par le bureau ferait lever `KeyError`/`ValueError` en
    pleine page `/live`, alors qu'un texte bancal doit s'afficher tel quel
    (*ne jamais bloquer* prime). `{jeu}` retombe sur l'intitulé du tournoi
    quand le champ jeu est vide (tournois créés avant le formulaire à deux
    champs) ; `{heure}` passe par `_heure_locale`, comme partout ailleurs sur
    cet écran, pour ne jamais afficher l'heure UTC stockée en base.
    """
    valeurs = {
        "{jeu}": tournoi["jeu"] or tournoi["nom"],
        "{minutes}": str(minutes),
        "{heure}": _heure_locale(tournoi["date_heure"]),
        "{lieu}": tournoi["emplacement"] or "",
    }
    texte = modele
    for jeton, valeur in valeurs.items():
        texte = texte.replace(jeton, valeur)
    return texte


def jetons_inconnus(modele: str) -> list[str]:
    """
    Les jetons du modèle qui ne font pas partie du vocabulaire fermé
    `JETONS_ALERTE`, dans l'ordre d'apparition et sans doublon.

    Sert au REFUS à l'enregistrement (D9) : un `{jouer}` ou un `{Jeu}` mal
    orthographié s'afficherait tel quel en salle, entre accolades, sans que
    personne ne comprenne pourquoi. Le vocabulaire n'a qu'un domicile, ici,
    à côté de `formater_alerte` qui le consomme.

    La casse compte : `{Jeu}` est bien signalé comme inconnu, puisque
    `formater_alerte` ne le substituerait pas.
    """
    connus = {"{" + jeton + "}" for jeton in JETONS_ALERTE}
    trouves: list[str] = []
    for jeton in _MOTIF_JETON.findall(modele or ""):
        if jeton not in connus and jeton not in trouves:
            trouves.append(jeton)
    return trouves


def alerte_tournoi_detaillee(conn, conn_tournoi) -> tuple[str, str] | None:
    """
    Comme `alerte_tournoi`, mais renvoie aussi l'HEURE LOCALE À LAQUELLE LE
    BANDEAU SE LIBÈRE — c'est-à-dire l'heure de début du tournoi annoncé,
    puisque la fenêtre d'affichage est `[H - délai, H[` (D5).

    Cette heure n'a qu'un seul consommateur, `/admin/ecran-salle`, et une
    seule raison d'être : quand une alerte occupe le bandeau alors qu'une
    annonce du bureau est enregistrée, l'écran d'administration doit dire à
    quelle heure l'annonce reprendra. Sans cela, un membre du bureau conclut
    que son annonce a été perdue — c'est la contrepartie explicite de D8.

    Returns:
        `(texte de l'alerte, heure de reprise "HH:MM")`, ou None.
    """
    modele = services.lire_parametre(conn, CLE_ALERTE_MESSAGE, None)
    if not modele:
        return None
    try:
        delai_mini = int(services.lire_parametre(
            conn, CLE_ALERTE_DELAI_MIN, str(DELAI_MIN_DEFAUT)))
    except (TypeError, ValueError):
        delai_mini = DELAI_MIN_DEFAUT  # réglage corrompu : jamais bloquant
    try:
        delai_maxi = int(services.lire_parametre(
            conn, CLE_ALERTE_DELAI_MAX, str(DELAI_MAX_DEFAUT)))
    except (TypeError, ValueError):
        delai_maxi = DELAI_MAX_DEFAUT
    resultat = tournoi_services.tournoi_a_annoncer(conn_tournoi, delai_mini, delai_maxi)
    if resultat is None:
        return None
    tournoi, minutes = resultat
    return formater_alerte(modele, tournoi, minutes), _heure_locale(tournoi["date_heure"])


def alerte_tournoi(conn, conn_tournoi) -> str | None:
    """
    Message d'alerte « rapportez les exemplaires » actuellement affichable sur
    /live, ou None si le modèle n'est pas configuré (D10) ou si aucun tournoi
    ne qualifie. Calcul de lecture, comme `annonce_active` : aucune écriture
    en base. Lit les trois réglages dans la base de PRÊT (`conn`), appelle
    `tournoi_a_annoncer` sur la base des TOURNOIS (`conn_tournoi`) — le pont
    entre les deux bases se fait ici, dans la route, jamais dans un service
    (précédent : `routes/tournoi.py::_inscription_au_comptoir`).

    Le calcul vit dans `alerte_tournoi_detaillee`, qui rend en plus l'heure de
    reprise du bandeau : un seul domicile, deux façons de le lire selon qu'on
    projette l'écran (ici) ou qu'on le règle (/admin/ecran-salle).
    """
    detail = alerte_tournoi_detaillee(conn, conn_tournoi)
    return detail[0] if detail else None


def _collecter_donnees() -> dict:
    """
    Rassemble toutes les données du tableau de bord (partagé par la page et
    l'endpoint JSON, pour garantir des chiffres identiques).
    """
    # --- Base de PRÊT : réglages, disponibilité, derniers mouvements ---
    # Un panneau éteint n'est PAS collecté : ni requête inutile, ni champ vide
    # dans /live/data (« ne jamais afficher une valeur absente »).
    conn = get_connection()
    try:
        panneaux = panneaux_actifs(conn)
        titre = titre_ecran(conn)
        annonce = annonce_active(conn)
        # Le compteur « Tournois en cours » vit dans la barre de chiffres, pas
        # dans le panneau : il suit donc le réglage des chiffres, mais reste
        # soumis à la même précédence de module.
        compteur_tournois = (
            panneaux["chiffres"] and lire_etat_module(conn, "tournois") != "desactive"
        )
        jeux = (services.compter_exemplaires_disponibles(conn)
                if panneaux["chiffres"] else None)
        mouvements = (services.derniers_mouvements(conn, NB_MOUVEMENTS)
                      if panneaux["mouvements"] else [])
    finally:
        conn.close()

    # --- Base des TOURNOIS : tournois en cours + à venir, et animations ---
    # Une seule connexion pour les deux panneaux : ils vivent dans la même base.
    en_cours: list[dict] = []
    a_venir: list[dict] = []
    animations: list[dict] = []
    tournois: list = []
    imminents: list = []
    if panneaux["tournois"] or compteur_tournois or panneaux["programme"]:
        conn_t = get_tournoi_connection()
        try:
            if panneaux["tournois"] or compteur_tournois:
                tournois = tournoi_services.lister_tournois(conn_t, inclure_brouillons=False)
                imminents = tournoi_services.tournois_imminents(conn_t, FENETRE_A_VENIR_MIN)
            if panneaux["programme"]:
                # `imminents` est LA fusion des deux sources : on ne garde ici
                # que les éléments de programme, les tournois ayant déjà leur
                # propre panneau. Les annulés sont demandés pour être affichés
                # barrés pendant leur créneau (§6.4) plutôt que de disparaître
                # sans explication devant des gens qui les attendent.
                animations = [
                    {
                        "nom": e["intitule"],
                        "icone": e["icone"],
                        "lieu": e["lieu"],
                        "heure": e["heure_locale"],
                        "minutes_avant": e["minutes_avant"],
                        "annule": e.get("etat") == "annule",
                    }
                    for e in programme.imminents(
                        conn_t, FENETRE_A_VENIR_MIN, inclure_annules=True
                    )
                    if e["source"] == "programme"
                ]
        finally:
            conn_t.close()

        # Le MODE DE SCORING n'est volontairement plus transmis : sur un écran lu
        # de loin, « Ronde suisse » n'aide aucun visiteur à décider quoi que ce
        # soit et concurrençait le titre. Le nombre de joueurs le remplace — il
        # dit l'ampleur de ce qui se joue, ce qui est l'information attendue.
        en_cours = [
            {
                "nom": t["nom"],
                "nb_inscrits": t["nb_inscrits"],
                "lieu": t["emplacement"],
            }
            for t in tournois
            if t["etat"] == "lance"
        ]

        # `nb_places` accompagne `places_restantes` : l'écran affiche une jauge
        # (« 4 places libres / 12 »), qui n'a de sens qu'avec son total. Reste
        # None quand le tournoi n'a pas de plafond — la page dit alors
        # « inscriptions ouvertes » plutôt que d'inventer un chiffre.
        a_venir = [
            {
                "nom": t["nom"],
                "heure": _heure_locale(t["date_heure"]),
                "minutes_avant": _minutes_avant(t["date_heure"]),
                "places_restantes": t["places_restantes"],
                "nb_places": t["nb_places"],
                "lieu": t["emplacement"],
            }
            for t in imminents
        ]

    # --- Alerte tournoi « rapportez les exemplaires » (D1 : bandeau distinct,
    # pas un panneau désactivable) ---
    # Indépendante des réglages d'affichage ci-dessus, mais nécessite les deux
    # bases en même temps (réglages en base de prêt, tournoi en base des
    # tournois) : deux connexions courtes, dédiées, plutôt que d'étirer la
    # durée de vie de `conn` ouverte plus haut. Soumise à la même précédence
    # de module que le panneau tournois (voir `panneaux_actifs`) : un module
    # désactivé n'a plus de tournois à annoncer.
    alerte = None
    conn = get_connection()
    try:
        if lire_etat_module(conn, "tournois") != "desactive":
            conn_t = get_tournoi_connection()
            try:
                alerte = alerte_tournoi(conn, conn_t)
            finally:
                conn_t.close()
    finally:
        conn.close()

    resultat = {
        "titre": titre,
        "panneaux": panneaux,
        "horodatage": datetime.now(FUSEAU_LOCAL).strftime("%H:%M"),
    }
    if jeux is not None:
        total, disponibles = jeux
        resultat["jeux"] = {"total": total, "disponibles": disponibles,
                            "sortis": total - disponibles}
    if compteur_tournois:
        resultat["nb_tournois_en_cours"] = len(en_cours)
    if panneaux["tournois"]:
        resultat["tournois_en_cours"] = en_cours
        resultat["tournois_a_venir"] = a_venir
    if panneaux["programme"]:
        resultat["animations"] = animations
    if panneaux["mouvements"]:
        resultat["mouvements"] = [
            {
                "type": m["type"],
                "nom": m["nom"],
                "motif": m["motif"],
                "heure": m["heure_locale"],
            }
            for m in mouvements
        ]
    # Jamais de champ "annonce" quand il n'y en a pas (ne jamais afficher une
    # valeur absente, cf. rangement) : le bandeau de /live se fie à sa présence.
    if annonce:
        resultat["annonce"] = annonce
    # Même règle pour l'alerte tournoi : absente du JSON quand il n'y a rien à
    # dire. Les deux clés peuvent cohabiter dans le JSON (chacune calculée
    # indépendamment) — c'est au gabarit de trancher laquelle des deux bandes
    # s'affiche (D8 : l'alerte l'emporte, une seule bande visible).
    if alerte:
        resultat["alerte_tournoi"] = alerte
    return resultat


@router.get("/live")
def live(request: Request):
    """Page du tableau de bord (rendue une fois ; le contenu se met à jour en JS)."""
    return templates.TemplateResponse(request, "live.html", {"data": _collecter_donnees()})


@router.get("/live/data")
def live_data():
    """Données fraîches du tableau de bord (JSON), interrogées en boucle par la page."""
    return _collecter_donnees()
