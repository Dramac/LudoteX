"""
Route du TABLEAU DE BORD TEMPS RÉEL `/live` (affichage salle, écran 16:9).

But : projeter en salle, pendant l'événement, un panorama qui se rafraîchit tout
seul — jeux sortis / disponibles, tournois en cours et à venir, et le flux des
derniers prêts/retours. Page PUBLIQUE en LECTURE SEULE : aucune action, aucun
jeton bénévole requis, aucune donnée personnelle (le numéro de pochette, lié à
une pièce d'identité, n'est volontairement jamais affiché ici).

Deux routes publiques :
- GET /live      : la page (HTML plein écran, mise en page 16:9).
- GET /live/data : les données fraîches au format JSON (interrogé en boucle par
                   la page via fetch(), sans rechargement).

Et une page BÉNÉVOLE, la seule écriture de ce module (série annonce, lot 1) :
- GET/POST /live/annonce : poser, remplacer ou effacer l'annonce de l'écran,
                   derrière le jeton bénévole ET l'interrupteur du bureau
                   (`services.CLE_ANNONCE_BENEVOLES`, fermé par défaut).

Comme partout dans le projet, AUCUNE logique métier ici : on délègue à
`app.services` (prêt) et `app.tournoi.services` (tournois), on assemble, on rend.
"""

import re
from datetime import datetime, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import journal, services
from app.auth import exiger_jeton
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
# annonce à la fois, pas d'historique, et toujours une échéance. Les clés et
# les règles (durée obligatoire, effacement) vivent dans `app.services`,
# section « Annonce de l'écran de salle » ; elles sont reprises ici pour les
# lecteurs historiques de ce module.
CLE_ANNONCE = services.CLE_ANNONCE
CLE_ANNONCE_EXPIRE = services.CLE_ANNONCE_EXPIRE

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
    enregistrée, si sa durée d'affichage est dépassée, ou si elle n'a pas
    d'échéance lisible (voir `services.annonce_affichable`).

    LECTURE SEULE, et ça doit le rester : /live/data l'appelle en boucle et la
    supervision à chaque visite du tableau de bord. Le texte d'une annonce
    expirée n'est plus « rappelable » : il est effacé aux moments d'écriture
    énumérés dans `services.purger_annonce_expiree`, jamais ici.
    """
    annonce = services.lire_parametre(conn, CLE_ANNONCE, None)
    if not annonce:
        return None
    if not services.annonce_affichable(
            services.lire_parametre(conn, CLE_ANNONCE_EXPIRE, None)):
        return None
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


def lire_alerte_detaillee(conn) -> tuple[str, str] | None:
    """
    `alerte_tournoi_detaillee` telle que la lisent les deux pages qui RÈGLENT
    le bandeau (/admin/ecran-salle et /live/annonce) : avec la précédence du
    module — un module `tournois` désactivé n'a plus de tournoi à annoncer, et
    /live se comporte déjà ainsi — et en ouvrant elle-même la base des
    tournois. C'est une fonction de ROUTE : le pont entre les deux bases se
    fait ici, jamais dans un service (invariant des trois bases).

    Un seul domicile pour cette lecture : sans lui, les deux pages auraient
    chacune leur copie du test de module et de l'ouverture de connexion.
    """
    if lire_etat_module(conn, "tournois") == "desactive":
        return None
    conn_t = get_tournoi_connection()
    try:
        return alerte_tournoi_detaillee(conn, conn_t)
    finally:
        conn_t.close()


# ---------------------------------------------------------------------------
# Enregistrer l'annonce — un seul domicile pour le bureau et les bénévoles
# ---------------------------------------------------------------------------
class AnnonceEnregistree(NamedTuple):
    """
    Ce qu'un enregistrement a fait, sans le texte : `posee` (sinon effacée),
    l'heure locale de fin « HH:MM » et la phrase de correction de durée
    (`services.duree_annonce`), None quand la saisie a été prise telle quelle.
    """
    posee: bool
    heure_fin: str | None = None
    correction: str | None = None


def enregistrer_annonce(request: Request, annonce: str,
                        annonce_duree: str) -> AnnonceEnregistree:
    """
    Pose ou efface l'annonce à partir de la SAISIE BRUTE d'un formulaire, et
    journalise ce qui a eu lieu. Appelée par `/admin/ecran-salle` (le bureau)
    et par `/live/annonce` (les bénévoles) : la normalisation, les bornes, le
    choix de la ligne de journal et l'heure de fin n'ont qu'un domicile.

    - Annonce vide => elle est EFFACÉE de la base, pas seulement masquée — y
      compris un texte expiré qui y traînait encore.
    - Durée OBLIGATOIRE, bornée (`services.duree_annonce`) : une saisie vide,
      nulle, négative, illisible ou trop longue n'est jamais refusée (geste
      d'urgence) mais corrigée, et `correction` le dit. Une valeur forgée sur
      la page bénévole, dont les durées sont des boutons, passe par la même
      borne : elle ne contourne rien.

    Elle vit dans la route `live` et non dans `services` parce que
    `journaliser` réclame la requête, que les services ne connaissent pas.
    La dernière annonce posée l'emporte, d'où qu'elle vienne : pas de verrou,
    pas de marque d'origine en base. L'appareil est dans le journal, comme
    pour toute action bénévole (`journal._qui`).
    """
    saisie_annonce = " ".join(annonce.split())[:services.LONGUEUR_ANNONCE]
    duree_min, correction = (services.duree_annonce(annonce_duree)
                             if saisie_annonce else (None, None))
    conn = get_connection()
    try:
        # Lu AVANT écriture : sert uniquement à savoir s'il y avait une annonce
        # AFFICHÉE à effacer. Enregistrer les panneaux seuls (bureau) ne doit
        # rien écrire au journal — sinon chaque passage sur cette page
        # produirait une ligne « annonce effacée » qui n'a jamais eu lieu. Un
        # texte déjà expiré est effacé lui aussi, mais en silence : personne ne
        # l'a retiré de l'écran.
        annonce_precedente = annonce_active(conn)
        if saisie_annonce:
            expire_iso = services.poser_annonce(conn, saisie_annonce, duree_min)
        else:
            services.effacer_annonce(conn)
    finally:
        conn.close()

    # L'annonce est une SAISIE LIBRE, servie au public : son texte ne part
    # JAMAIS au journal, ni entier, ni tronqué, ni à la pose, ni à
    # l'effacement (constat RGPD-01 ; même règle que le détail d'un
    # signalement). Le journal dit qu'une annonce a été posée, pour combien de
    # temps, ou retirée — ce qu'on cherche après coup, sans rien de ce
    # qu'elle disait. Verrouillé par tests/test_journal_interdits.py.
    if saisie_annonce:
        journal.journaliser(request, "live", "annonce_posee", objet=f"{duree_min} min")
        return AnnonceEnregistree(
            True, services.format_local(expire_iso).split(" ")[-1], correction)
    if annonce_precedente:
        journal.journaliser(request, "live", "annonce_effacee")
    return AnnonceEnregistree(False)


def message_annonce(resultat: AnnonceEnregistree) -> tuple[str, str]:
    """
    Le message `(niveau, texte)` d'un enregistrement. Une durée corrigée
    s'annonce en « attention » : la personne qui a posé l'annonce doit la voir.
    """
    if not resultat.posee:
        return "succes", "Annonce effacée."
    texte = f"Annonce enregistrée, affichée en salle jusqu'à {resultat.heure_fin}."
    if resultat.correction:
        return "attention", f"{texte} {resultat.correction}"
    return "succes", texte


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


# ---------------------------------------------------------------------------
# /live/annonce — l'annonce posée par les bénévoles (série annonce, lot 1)
# ---------------------------------------------------------------------------
# Durées proposées en BOUTONS RADIO sur téléphone, en minutes, avec leur
# libellé. La valeur par défaut (`services.DUREE_ANNONCE_DEFAUT_MIN`) doit en
# faire partie, et toutes restent sous `services.DUREE_ANNONCE_MAX_MIN` :
# verrouillé par tests/test_annonce_benevoles.py. Plus court que les raccourcis
# du bureau (jusqu'à 12 h) : un appel du stand dure le temps d'un appel ; une
# information de la journée relève du bureau.
DUREES_ANNONCE_BENEVOLE = ((15, "15 min"), (30, "30 min"), (60, "1 h"), (120, "2 h"))
# Phrase qui remplace, après la redirection, la correction de durée de
# `services.duree_annonce` : celle-ci cite la saisie reçue (« Durée « abc »
# illisible… »), qui n'a rien à faire dans une adresse. Sur cette page, les
# durées sont des boutons : une correction ne survient que sur un envoi forgé
# ou tronqué.
CORRECTION_BENEVOLE = ("La durée reçue n'a pas été reconnue : l'heure de fin "
                       "a été corrigée.")
_MOTIF_HEURE = re.compile(r"^\d{2}:\d{2}$")
URL_ANNONCE_BENEVOLE = "/live/annonce"


def _vers_annonce(resultat: AnnonceEnregistree | None = None) -> RedirectResponse:
    """
    Redirection 303 après un envoi : un rechargement du navigateur ne
    repostera pas l'annonce. Le message voyage en CODES dans l'adresse
    (patron de `routes/admin.py::_vers_fiche`) : `ok`, l'heure de fin
    « HH:MM » et un drapeau de correction. JAMAIS le texte de l'annonce, ni la
    saisie de durée reçue.
    """
    if resultat is None:
        return RedirectResponse(URL_ANNONCE_BENEVOLE, status_code=303)
    if not resultat.posee:
        return RedirectResponse(f"{URL_ANNONCE_BENEVOLE}?ok=effacee", status_code=303)
    url = f"{URL_ANNONCE_BENEVOLE}?ok=posee&fin={resultat.heure_fin}"
    if resultat.correction:
        url += "&corrigee=1"
    return RedirectResponse(url, status_code=303)


def _message_depuis_adresse(ok: str, fin: str, corrigee: str) -> tuple[str, str] | None:
    """
    Reconstruit le message de `message_annonce` à partir des codes de
    `_vers_annonce`. Un code inconnu ou une heure mal formée (adresse forgée)
    ne donnent aucun message, jamais une erreur.
    """
    if ok == "effacee":
        return message_annonce(AnnonceEnregistree(False))
    if ok == "posee" and _MOTIF_HEURE.match(fin or ""):
        return message_annonce(AnnonceEnregistree(
            True, fin, CORRECTION_BENEVOLE if corrigee else None))
    return None


@router.get(URL_ANNONCE_BENEVOLE)
def annonce_benevole(request: Request, ok: str = "", fin: str = "",
                     corrigee: str = "", _=Depends(exiger_jeton)):
    """
    La page bénévole de l'annonce, pensée téléphone et sans JS : l'annonce en
    cours (calculée par `annonce_active`, la même fonction que /live) et son
    heure de fin, l'alerte tournoi si elle occupe le bandeau, puis le champ,
    la durée en boutons et « Afficher en salle » / « Remplacer l'annonce ».

    Le garde du module `live` s'applique (routeur) ; il règle la visibilité,
    pas l'accès, d'où le `Depends(exiger_jeton)` en propre.

    Interrupteur fermé (lien ancien, page restée ouverte) : une page qui le
    dit, jamais une erreur. La règle vaut AUSSI pour un administrateur
    connecté, que `exiger_jeton` laisse passer : il a sa propre page,
    /admin/ecran-salle, vers laquelle le gabarit lui donne un lien. Une règle
    qui changerait selon la personne connectée serait impossible à expliquer
    dans le guide bénévole.
    """
    conn = get_connection()
    try:
        ouverte = services.lire_annonce_benevoles(conn)
        annonce = annonce_active(conn) if ouverte else None
        expire_iso = (services.lire_parametre(conn, services.CLE_ANNONCE_EXPIRE, None)
                      if annonce else None)
        detail_alerte = lire_alerte_detaillee(conn) if ouverte else None
    finally:
        conn.close()

    return templates.TemplateResponse(
        request, "live_annonce.html",
        {"ouverte": ouverte,
         "annonce": annonce,
         "annonce_fin": _heure_locale(expire_iso),
         "alerte_reprise": detail_alerte[1] if detail_alerte else None,
         "alerte_active": detail_alerte is not None,
         "durees": DUREES_ANNONCE_BENEVOLE,
         "duree_defaut": services.DUREE_ANNONCE_DEFAUT_MIN,
         "longueur_annonce": services.LONGUEUR_ANNONCE,
         "message": _message_depuis_adresse(ok, fin, corrigee) if ouverte else None},
    )


@router.post(URL_ANNONCE_BENEVOLE)
def annonce_benevole_enregistrer(request: Request, annonce: str = Form(""),
                                 annonce_duree: str = Form(""),
                                 _=Depends(exiger_jeton)):
    """
    Pose, remplace ou efface l'annonce (saisie vide = effacer), par la même
    fonction que le bureau (`enregistrer_annonce`), puis redirige (303).

    Interrupteur fermé : rien n'est écrit, rien n'est journalisé, et l'on
    revient à la page, qui explique pourquoi.

    Pas de limitation de débit propre à cette route (arbitrage du lot) : le
    jeton, l'interrupteur et l'appareil inscrit au journal suffisent, et une
    limite ne protège pas de la seule menace réelle — un jeton qui a circulé
    et UNE annonce déplacée, que le bureau efface d'un bouton.
    """
    conn = get_connection()
    try:
        ouverte = services.lire_annonce_benevoles(conn)
    finally:
        conn.close()
    if not ouverte:
        return _vers_annonce()
    return _vers_annonce(enregistrer_annonce(request, annonce, annonce_duree))
