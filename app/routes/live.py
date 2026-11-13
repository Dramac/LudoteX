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

Et une page BÉNÉVOLE, la seule écriture de ce module (série annonce) :
- GET/POST /live/annonce : ajouter une annonce à l'écran, voir celles qui y
                   défilent ;
- POST /live/annonce/effacer : en retirer une, n'importe laquelle ;
                   toutes deux derrière le jeton bénévole ET l'interrupteur du
                   bureau (`services.CLE_ANNONCE_BENEVOLES`, fermé par défaut).

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

# Annonces libres affichées en bandeau sur l'écran de salle (idée 5.2). Depuis
# la série annonce, lot 2, il peut y en avoir PLUSIEURS : elles défilent l'une
# après l'autre dans la bande unique, chacune avec son échéance. La table et
# les règles (durée obligatoire, garde-fou, effacement) vivent dans
# `app.services`, section « Annonces de l'écran de salle ».
#
# Durée d'affichage de CHAQUE annonce quand plusieurs défilent, en secondes.
# UN SEUL DOMICILE : la page /live la reçoit du gabarit (jamais recopiée en
# JS), et le « Tour complet de l'écran » des deux pages de saisie la
# multiplie par le nombre d'annonces (`duree_tour`). Huit secondes : de quoi
# lire deux lignes de loin, sans qu'une salle qui attend une information la
# guette trop longtemps.
DUREE_DEFILEMENT_S = 8

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


def annonces_affichees(conn) -> list[dict]:
    """
    Les annonces actuellement affichables sur /live, dans l'ordre de pose —
    la lecture de `services.lister_annonces_actives`, que /live, /live/data,
    les deux pages de saisie et la supervision partagent. LECTURE SEULE.
    """
    return services.lister_annonces_actives(conn)


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
    seule raison d'être : quand une alerte occupe le bandeau alors que des
    annonces sont posées, les pages de saisie doivent dire à quelle heure
    elles reprendront. Sans cela, on conclut qu'une annonce a été perdue —
    c'est la contrepartie explicite de D8.

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
    ne qualifie. Calcul de lecture, comme les annonces : aucune écriture
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
# Ajouter et retirer une annonce — un seul domicile pour le bureau et les
# bénévoles
# ---------------------------------------------------------------------------
# Les trois issues d'un ajout. Pas d'issue « effacée » : depuis la série
# annonce, lot 2, un texte vide n'efface plus rien (on efface une annonce par
# son propre bouton), c'est un envoi sans effet.
AJOUT_POSEE = "posee"
AJOUT_VIDE = "vide"
AJOUT_PLAFOND = "plafond"

MESSAGE_TEXTE_VIDE = "Rien à afficher : le texte est vide."
MESSAGE_PLAFOND = (f"L'écran affiche déjà {services.ANNONCES_MAX_ACTIVES} "
                   "annonces : effacez-en une avant d'en ajouter.")
MESSAGE_PLUS_AFFICHEE = "Cette annonce n'était plus affichée."


class AnnonceEnregistree(NamedTuple):
    """
    Ce qu'un ajout a fait, SANS le texte : l'issue (`AJOUT_POSEE`,
    `AJOUT_VIDE`, `AJOUT_PLAFOND`), l'heure locale de fin « HH:MM » et la
    phrase de correction de durée (`services.duree_annonce`), None quand la
    saisie a été prise telle quelle.
    """
    issue: str
    heure_fin: str | None = None
    correction: str | None = None


def enregistrer_annonce(request: Request, annonce: str,
                        annonce_duree: str) -> AnnonceEnregistree:
    """
    AJOUTE une annonce à partir de la SAISIE BRUTE d'un formulaire, et
    journalise la pose. Appelée par `/admin/ecran-salle/annonce` (le bureau)
    et par `/live/annonce` (les bénévoles) : la normalisation, les bornes, le
    garde-fou, la ligne de journal et l'heure de fin n'ont qu'un domicile.

    - Texte vide (ou fait d'espaces) : RIEN n'est écrit ni journalisé
      (`AJOUT_VIDE`). Ce n'est plus un effacement.
    - Durée OBLIGATOIRE, bornée (`services.duree_annonce`) : une saisie vide,
      nulle, négative, illisible ou trop longue n'est jamais refusée (geste
      d'urgence) mais corrigée, et `correction` le dit. Une valeur forgée sur
      la page bénévole, dont les durées sont des boutons, passe par la même
      borne : elle ne contourne rien.
    - Garde-fou `services.ANNONCES_MAX_ACTIVES` atteint : rien n'est écrit
      ni journalisé (`AJOUT_PLAFOND`) ; c'est à l'appelant de rendre la
      saisie, qui ne doit pas être perdue.

    Elle vit dans la route `live` et non dans `services` parce que
    `journaliser` réclame la requête, que les services ne connaissent pas.
    Les annonces s'ajoutent les unes aux autres, d'où qu'elles viennent : pas
    de marque d'origine en base. L'appareil est dans le journal, comme pour
    toute action bénévole (`journal._qui`).
    """
    saisie_annonce = " ".join(annonce.split())[:services.LONGUEUR_ANNONCE]
    if not saisie_annonce:
        return AnnonceEnregistree(AJOUT_VIDE)
    duree_min, correction = services.duree_annonce(annonce_duree)
    conn = get_connection()
    try:
        posee = services.poser_annonce(conn, saisie_annonce, duree_min)
    finally:
        conn.close()
    if posee is None:
        return AnnonceEnregistree(AJOUT_PLAFOND)

    # L'annonce est une SAISIE LIBRE, servie au public : son texte ne part
    # JAMAIS au journal, ni entier, ni tronqué, ni à la pose, ni à
    # l'effacement (constat RGPD-01 ; même règle que le détail d'un
    # signalement). Le journal dit qu'une annonce a été posée et pour combien
    # de temps — ce qu'on cherche après coup, sans rien de ce qu'elle disait.
    # Verrouillé par tests/test_journal_interdits.py (scénario ET lecture
    # statique de toute fonction qui écrit une annonce).
    journal.journaliser(request, "live", "annonce_posee", objet=f"{duree_min} min")
    return AnnonceEnregistree(AJOUT_POSEE, _heure_locale(posee.expire_le), correction)


def message_annonce(resultat: AnnonceEnregistree) -> tuple[str, str]:
    """
    Le message `(niveau, texte)` d'un ajout. Une durée corrigée, un texte
    vide et le garde-fou s'annoncent en « attention » : la personne qui a
    posé l'annonce doit le voir. Aucun n'est une erreur.
    """
    if resultat.issue == AJOUT_VIDE:
        return "attention", MESSAGE_TEXTE_VIDE
    if resultat.issue == AJOUT_PLAFOND:
        return "attention", MESSAGE_PLAFOND
    texte = f"Annonce ajoutée, affichée en salle jusqu'à {resultat.heure_fin}."
    if resultat.correction:
        return "attention", f"{texte} {resultat.correction}"
    return "succes", texte


def retirer_annonce(request: Request, annonce_id: str) -> str | None:
    """
    Retire de l'écran l'annonce `annonce_id` (valeur BRUTE d'un champ caché)
    et renvoie son TEXTE, pour que le message la nomme — ou None si elle
    n'était plus affichée : identifiant inconnu, déjà effacé par quelqu'un
    d'autre, déjà expiré, formulaire périmé, valeur forgée ou non numérique.
    Dans ce cas rien n'est journalisé, et ce n'est jamais une erreur.

    Un seul domicile pour le bureau (`/admin/ecran-salle/annonce/effacer`) et
    les bénévoles (`/live/annonce/effacer`). N'importe qui peut retirer
    n'importe quelle annonce, d'où qu'elle vienne : il n'y a pas de marque
    d'origine.

    Journal : `annonce_effacee`, SANS objet. Jamais le texte (RGPD-01), et pas
    l'identifiant non plus : un numéro interne n'apprend rien à un lecteur du
    journal, qui cherche quand et depuis quel appareil.
    """
    try:
        identifiant = int((annonce_id or "").strip())
    except ValueError:
        return None
    conn = get_connection()
    try:
        retiree = services.effacer_annonce(conn, identifiant)
    finally:
        conn.close()
    if retiree is None:
        return None
    journal.journaliser(request, "live", "annonce_effacee")
    return retiree["texte"]


def message_retrait(texte_retire: str | None) -> tuple[str, str]:
    """
    Le message `(niveau, texte)` d'un retrait, qui NOMME l'annonce retirée
    (le texte était déjà public). Un retrait sans effet a son message
    neutre, jamais une erreur.
    """
    if texte_retire is None:
        return "succes", MESSAGE_PLUS_AFFICHEE
    return "succes", f"Annonce retirée de l'écran : « {texte_retire} »."


def duree_tour(nb_annonces: int) -> str:
    """
    Durée d'un « Tour complet de l'écran » pour `nb_annonces` annonces qui
    défilent, en toutes lettres courtes : « 16 s », « 1 min 20 », « 2 min ».
    UNE seule fonction de formatage, partagée par les deux pages de saisie.
    C'est une information, pas une limite.
    """
    secondes = max(0, nb_annonces) * DUREE_DEFILEMENT_S
    if secondes < 60:
        return f"{secondes} s"
    minutes, reste = divmod(secondes, 60)
    return f"{minutes} min {reste:02d}" if reste else f"{minutes} min"


def annonces_pour_liste(annonces: list[dict]) -> list[dict]:
    """
    Les annonces affichées telles que les LISTENT les deux pages de saisie :
    la plus récente d'abord, avec l'heure de pose et l'heure de fin en heure
    locale. Le temps restant se dit « jusqu'à HH:MM », jamais « encore
    25 min » : sur une page sans JS restée ouverte, une durée vieillit et
    ment, une heure de fin reste vraie.
    """
    return [{"id": a["id"], "texte": a["texte"],
             "posee": _heure_locale(a["posee_le"]),
             "fin": _heure_locale(a["expire_le"])}
            for a in reversed(annonces)]


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
        annonces = annonces_affichees(conn)
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
    # Jamais de champ "annonces" quand il n'y en a pas (ne jamais afficher une
    # valeur absente, cf. rangement) : le bandeau de /live se fie à sa
    # présence. L'ordre est celui de POSE (identifiant croissant) : c'est
    # l'ordre du défilement, et une annonce ajoutée rejoint la fin du tour.
    # L'identifiant sert au défilement à reconnaître l'annonce courante d'un
    # rafraîchissement à l'autre ; ni l'heure de pose ni l'échéance ne sont
    # publiées.
    if annonces:
        resultat["annonces"] = [{"id": a["id"], "texte": a["texte"]}
                                for a in annonces]
        # COMPATIBILITÉ, le temps d'une version (1.32.x) : un poste de
        # projection qui a chargé /live AVANT la mise à jour continue
        # d'interroger /live/data sans se recharger, et lit `annonce`. Il
        # reçoit la plus récente et continue d'afficher une annonce au lieu
        # d'un bandeau vide. À retirer à la version suivante.
        resultat["annonce"] = annonces[-1]["texte"]
    # Même règle pour l'alerte tournoi : absente du JSON quand il n'y a rien à
    # dire. Les clés peuvent cohabiter dans le JSON (chacune calculée
    # indépendamment) — c'est au gabarit de trancher quelle bande s'affiche
    # (D8 : l'alerte l'emporte sur le défilement, une seule bande visible).
    if alerte:
        resultat["alerte_tournoi"] = alerte
    return resultat


@router.get("/live")
def live(request: Request):
    """Page du tableau de bord (rendue une fois ; le contenu se met à jour en JS)."""
    return templates.TemplateResponse(
        request, "live.html",
        {"data": _collecter_donnees(), "duree_defilement_s": DUREE_DEFILEMENT_S})


@router.get("/live/data")
def live_data():
    """Données fraîches du tableau de bord (JSON), interrogées en boucle par la page."""
    return _collecter_donnees()


# ---------------------------------------------------------------------------
# /live/annonce — les annonces posées par les bénévoles (série annonce)
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
URL_EFFACER_BENEVOLE = "/live/annonce/effacer"


def _vers_annonce(resultat: AnnonceEnregistree | None = None) -> RedirectResponse:
    """
    Redirection 303 après un ajout : un rechargement du navigateur ne
    repostera pas l'annonce. Le message voyage en CODES dans l'adresse
    (patron de `routes/admin.py::_vers_fiche`) : `ok`, l'heure de fin
    « HH:MM » et un drapeau de correction. JAMAIS le texte de l'annonce, ni la
    saisie de durée reçue. Le garde-fou atteint ne passe PAS par ici : la
    saisie, qu'une adresse ne peut pas porter, serait perdue.
    """
    if resultat is None:
        return RedirectResponse(URL_ANNONCE_BENEVOLE, status_code=303)
    if resultat.issue == AJOUT_VIDE:
        return RedirectResponse(f"{URL_ANNONCE_BENEVOLE}?ok=vide", status_code=303)
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
    if ok == "vide":
        return message_annonce(AnnonceEnregistree(AJOUT_VIDE))
    if ok == "posee" and _MOTIF_HEURE.match(fin or ""):
        return message_annonce(AnnonceEnregistree(
            AJOUT_POSEE, fin, CORRECTION_BENEVOLE if corrigee else None))
    return None


def _interrupteur_ouvert() -> bool:
    conn = get_connection()
    try:
        return services.lire_annonce_benevoles(conn)
    finally:
        conn.close()


def _page_annonce_benevole(request: Request, message: tuple[str, str] | None = None,
                           saisie: dict | None = None):
    """
    Rendu commun de la page bénévole : le GET, l'ajout refusé par le
    garde-fou (rendu direct, `saisie` remise dans le champ et la durée
    choisie recochée) et le retrait (rendu direct, message qui nomme
    l'annonce).

    Interrupteur fermé : une page qui le dit, jamais une erreur, et aucun
    message ni aucune liste.
    """
    conn = get_connection()
    try:
        ouverte = services.lire_annonce_benevoles(conn)
        annonces = annonces_affichees(conn) if ouverte else []
        detail_alerte = lire_alerte_detaillee(conn) if ouverte else None
    finally:
        conn.close()

    duree_choisie = services.DUREE_ANNONCE_DEFAUT_MIN
    if saisie and saisie.get("duree"):
        try:
            duree_choisie = int(saisie["duree"])
        except ValueError:
            pass
    return templates.TemplateResponse(
        request, "live_annonce.html",
        {"ouverte": ouverte,
         "annonces": annonces_pour_liste(annonces),
         "tour_complet": duree_tour(len(annonces)),
         "action_effacer": URL_EFFACER_BENEVOLE,
         "alerte_reprise": detail_alerte[1] if detail_alerte else None,
         "alerte_active": detail_alerte is not None,
         "durees": DUREES_ANNONCE_BENEVOLE,
         "duree_defaut": duree_choisie,
         "saisie_texte": (saisie or {}).get("texte", ""),
         "longueur_annonce": services.LONGUEUR_ANNONCE,
         "message": message if ouverte else None},
    )


@router.get(URL_ANNONCE_BENEVOLE)
def annonce_benevole(request: Request, ok: str = "", fin: str = "",
                     corrigee: str = "", _=Depends(exiger_jeton)):
    """
    La page bénévole des annonces, pensée téléphone et sans JS : le
    formulaire « Ajouter une annonce » en haut (le geste principal), puis la
    liste des annonces affichées en salle, la plus récente d'abord, chacune
    avec son bouton « Effacer ». L'alerte tournoi est signalée si elle occupe
    le bandeau.

    Le garde du module `live` s'applique (routeur) ; il règle la visibilité,
    pas l'accès, d'où le `Depends(exiger_jeton)` en propre.

    Interrupteur fermé (lien ancien, page restée ouverte) : une page qui le
    dit, jamais une erreur. La règle vaut AUSSI pour un administrateur
    connecté, que `exiger_jeton` laisse passer : il a sa propre page,
    /admin/ecran-salle, vers laquelle le gabarit lui donne un lien. Une règle
    qui changerait selon la personne connectée serait impossible à expliquer
    dans le guide bénévole.
    """
    return _page_annonce_benevole(request, _message_depuis_adresse(ok, fin, corrigee))


@router.post(URL_ANNONCE_BENEVOLE)
def annonce_benevole_enregistrer(request: Request, annonce: str = Form(""),
                                 annonce_duree: str = Form(""),
                                 _=Depends(exiger_jeton)):
    """
    AJOUTE une annonce, par la même fonction que le bureau
    (`enregistrer_annonce`), puis redirige (303) avec des codes.

    Garde-fou atteint : la redirection ne pourrait pas porter la saisie (le
    texte ne passe jamais dans une adresse) ; la page est donc RENDUE
    DIRECTEMENT, le texte remis dans le champ et la durée recochée. Un
    rechargement renverrait le formulaire, ce qui est le geste voulu une fois
    une annonce effacée.

    Interrupteur fermé : rien n'est écrit, rien n'est journalisé, et l'on
    revient à la page, qui explique pourquoi.

    Pas de limitation de débit propre à cette route (arbitrage du lot 1) : le
    jeton, l'interrupteur et l'appareil inscrit au journal suffisent, et le
    garde-fou des 50 annonces borne le pire cas.
    """
    if not _interrupteur_ouvert():
        return _vers_annonce()
    resultat = enregistrer_annonce(request, annonce, annonce_duree)
    if resultat.issue == AJOUT_PLAFOND:
        return _page_annonce_benevole(
            request, message_annonce(resultat),
            saisie={"texte": annonce[:services.LONGUEUR_ANNONCE],
                    "duree": annonce_duree})
    return _vers_annonce(resultat)


@router.post(URL_EFFACER_BENEVOLE)
def annonce_benevole_effacer(request: Request, annonce_id: str = Form(""),
                             _=Depends(exiger_jeton)):
    """
    Retire UNE annonce de l'écran, n'importe laquelle — y compris celle du
    bureau (décision de la série annonce, lot 2) —, par la même fonction que
    le bureau (`retirer_annonce`). Un tap, sans confirmation.

    RENDU DIRECT, sans redirection (arbitrage du lot) : le message NOMME
    l'annonce retirée, et son texte ne passe jamais dans une adresse. Un
    rechargement renvoie l'effacement d'un identifiant disparu : sans effet,
    message « Cette annonce n'était plus affichée. ».

    Interrupteur fermé : refusé comme la pose, rien n'est écrit (arbitrage du
    lot : même règle pour poser et pour effacer ; le bureau a sa page).
    """
    if not _interrupteur_ouvert():
        return _vers_annonce()
    return _page_annonce_benevole(request, message_retrait(retirer_annonce(request, annonce_id)))


@router.get(URL_EFFACER_BENEVOLE)
def annonce_benevole_effacer_get(_=Depends(exiger_jeton)):
    """
    L'adresse d'effacement ouverte en GET (historique du navigateur, onglet
    restauré après un retrait) : retour à la page, jamais une erreur 405.
    """
    return _vers_annonce()
