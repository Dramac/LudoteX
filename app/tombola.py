"""
Tombola — tirages, remise des lots, édition, lectures publiques et
statistiques (série tombola, lots 1 et 2).

Toute la logique du module vit ICI ; `routes/tombola.py` ne fait qu'assembler
(lire le formulaire, appeler, journaliser, rendre). Conception complète :
`docs/conception-tombola.md`.

POURQUOI UN MODULE À PART, ET PAS UNE SECTION DE `services.py`
-------------------------------------------------------------
`services.py` dépasse cinq mille lignes et porte le cœur du prêt. La tombola
a ses trois tables, son vocabulaire (édition, tirage, remise) et ses propres
constantes ; le lot 2 y a ajouté les lectures du panneau, de l'accueil et des
statistiques. Un module dédié, comme `app/carnet.py`, garde ce domaine
lisible d'un bloc. Il s'appuie sur `services` pour ce qui est commun
(`transaction`, `lire_parametre`) ; `services.cloturer_evenement`, à
l'inverse, l'importe en différé pour terminer l'édition dans la transaction
de la clôture.

INVARIANTS
----------
- Tout vit dans la base de PRÊT. Aucune fonction d'ici ne lit la base des
  tournois ni celle du planning.
- Aucune donnée personnelle, aucun texte libre : des numéros de 1 à 9999,
  des heures, des codes fermés.
- Aucune journalisation ici : seules les routes journalisent
  (docs/conception-journal.md § 5.2).
- Chaque écriture se fait sous `services.transaction` (`BEGIN IMMEDIATE`),
  la lecture de l'état comprise : c'est ce qui rend un tirage ou une remise
  indivisibles face à deux bénévoles qui agissent en même temps.
- Les fonctions renvoient une ISSUE (code fermé) que `message_*` traduit en
  phrase. Aucune n'est une erreur : « ne jamais bloquer ».
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Sequence

from app import services

# ---------------------------------------------------------------------------
# Constantes — un seul domicile ; les tests et les gabarits les importent.
# ---------------------------------------------------------------------------
NUMERO_MIN = 1
NUMERO_MAX = 9999
# Cinq champs pour la saisie manuelle, cinq numéros pour le tirage de secours.
NUMEROS_PAR_TIRAGE = 5

MODE_MANUEL = "manuel"
MODE_ALEATOIRE = "aleatoire"

MOTIF_REMIS = "remis"
MOTIF_ERREUR_SAISIE = "erreur_saisie"
MOTIF_NON_RECLAME = "non_reclame"

# Une remise peut être annulée pendant une minute (décision 5). Distincte de
# `services.SEUIL_ERREUR_PRET_S`, même si elle vaut aussi 60 : les deux règles
# n'ont rien à voir et peuvent diverger.
DELAI_ANNULATION_REMISE_S = 60

# Un numéro peut être retiré comme « erreur de saisie » pendant cinq minutes
# après son affichage (arbitrage du lot 1). Une faute de frappe se voit à la
# relecture du tirage ; plus tard, un bouton toujours présent deviendrait un
# moyen d'effacer un gagnant sans trace claire.
DELAI_ERREUR_SAISIE_S = 300

# Un tirage apparu depuis moins de cinq minutes porte la marque « Nouveau »
# sur l'écran de salle (série tombola, lot 2). Calculée par le SERVEUR à
# partir de l'heure du tirage : l'horloge du poste de projection n'entre pas
# en jeu.
DUREE_NOUVEAU_TIRAGE_S = 300

# Le plus grand numéro vendu, retenu d'un tirage par l'application à l'autre
# pour préremplir le champ. Aucune valeur par défaut écrite en base.
CLE_NUMERO_MAX = "tombola_numero_max"

# Longueur maximale d'un jeton de formulaire accepté tel quel (le nôtre en
# fait 22). Au-delà, ou vide, c'est un envoi forgé ou venu d'une page sans
# jeton : on en tire un neuf plutôt que de refuser.
_LONGUEUR_JETON_MAX = 64
_MOTIF_JETON = re.compile(r"^[A-Za-z0-9_-]+$")

# Une saisie de numéro, une fois les espaces retirés : des chiffres seulement
# (« 0045 » est lu 45, « 4 5 » aussi ; « 45a », « -3 » ou « 4,5 » ne le sont
# pas).
_MOTIF_NUMERO = re.compile(r"^[0-9]+$")


def _instant(instant: datetime | None) -> datetime:
    return instant or datetime.now(timezone.utc)


def _iso(instant: datetime) -> str:
    return instant.isoformat(timespec="seconds")


def _depuis_iso(iso: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso or "")
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def heure_locale(iso: str | None) -> str:
    """« 15:12 » en heure locale, ou une chaîne vide pour un horodatage illisible."""
    dt = _depuis_iso(iso)
    return dt.astimezone(services.FUSEAU_LOCAL).strftime("%H:%M") if dt else ""


def _ecart_s(depuis_iso: str | None, instant: datetime) -> float | None:
    dt = _depuis_iso(depuis_iso)
    return (instant - dt).total_seconds() if dt else None


# ---------------------------------------------------------------------------
# Lecture des saisies
# ---------------------------------------------------------------------------
def lire_numero(saisie: str | None) -> int | None:
    """
    Le numéro d'une saisie, ou None si elle n'en est pas un entre 1 et 9999.
    Les espaces sont retirés, les zéros de tête tolérés (« 0045 » → 45).
    """
    texte = "".join((saisie or "").split())
    if not _MOTIF_NUMERO.match(texte) or len(texte) > 12:
        return None
    valeur = int(texte)
    return valeur if NUMERO_MIN <= valeur <= NUMERO_MAX else None


def nouveau_jeton() -> str:
    """Jeton de formulaire : aléatoire, renouvelé à chaque affichage de la page."""
    return secrets.token_urlsafe(16)


def _jeton_valide(jeton: str | None) -> str:
    jeton = (jeton or "").strip()
    if jeton and len(jeton) <= _LONGUEUR_JETON_MAX and _MOTIF_JETON.match(jeton):
        return jeton
    return nouveau_jeton()


def lire_numero_max(conn: sqlite3.Connection) -> int | None:
    """Le plus grand numéro vendu retenu, ou None (jamais tiré, valeur illisible)."""
    return lire_numero(services.lire_parametre(conn, CLE_NUMERO_MAX))


# ---------------------------------------------------------------------------
# Édition
# ---------------------------------------------------------------------------
def edition_ouverte(conn: sqlite3.Connection) -> int | None:
    """Identifiant de l'édition ouverte, ou None s'il n'y en a pas."""
    ligne = conn.execute(
        "SELECT id FROM tombola_editions WHERE close_le IS NULL "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return ligne[0] if ligne else None


def _numeros_sortis(conn: sqlite3.Connection, edition_id: int | None) -> set[int]:
    """Numéros déjà sortis dans l'édition, ceux en erreur de saisie exceptés."""
    if edition_id is None:
        return set()
    return {ligne[0] for ligne in conn.execute(
        "SELECT numero FROM tombola_numeros WHERE edition_id = ? "
        "AND (motif IS NULL OR motif <> ?)",
        (edition_id, MOTIF_ERREUR_SAISIE),
    )}


def terminer_edition(conn: sqlite3.Connection,
                     instant: datetime | None = None) -> int:
    """
    Ferme l'édition ouverte : `close_le` posé, et ses numéros encore affichés
    passent en `non_reclame` avec `sorti_le` = maintenant. Renvoie le nombre
    de numéros ainsi passés (0 sans édition ouverte : base qui n'a jamais
    tiré, ou clôture répétée).

    Se greffe sur la transaction de la clôture de fin d'événement
    (`services.cloturer_evenement`), ou ouvre la sienne.
    """
    horodatage = _iso(_instant(instant))
    with services.transaction(conn):
        edition_id = edition_ouverte(conn)
        if edition_id is None:
            return 0
        cur = conn.execute(
            "UPDATE tombola_numeros SET motif = ?, sorti_le = ? "
            "WHERE edition_id = ? AND motif IS NULL",
            (MOTIF_NON_RECLAME, horodatage, edition_id),
        )
        nb = cur.rowcount
        conn.execute(
            "UPDATE tombola_editions SET close_le = ? WHERE id = ?",
            (horodatage, edition_id),
        )
    return nb


def vider(conn: sqlite3.Connection) -> None:
    """
    Vide les trois tables et le réglage du plus grand numéro (réinitialisation
    du site de formation). Ordre des clés étrangères : numéros, tirages,
    éditions. Ne committe pas : l'appelant (`formation._vider_base_pret`) le
    fait.
    """
    for table in ("tombola_numeros", "tombola_tirages", "tombola_editions"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM parametres WHERE cle = ?", (CLE_NUMERO_MAX,))


# ---------------------------------------------------------------------------
# Tirages
# ---------------------------------------------------------------------------
# Issues d'un tirage.
TIRAGE_ENREGISTRE = "enregistre"
TIRAGE_REJOUE = "rejoue"            # même jeton : rien de créé, résultat du premier
TIRAGE_AUCUN_VALIDE = "aucun_valide"  # saisie manuelle : rien d'écrit
TIRAGE_VIDE = "vide"                # saisie manuelle : aucun champ rempli
TIRAGE_MAX_INVALIDE = "max_invalide"  # tirage par l'application : rien d'écrit
TIRAGE_EPUISE = "epuise"            # tirage par l'application : plus aucun numéro

# Raisons d'écarter un numéro de la saisie manuelle.
ECART_ILLISIBLE = "illisible"
ECART_DOUBLON = "doublon"
ECART_DEJA_SORTI = "deja_sorti"


class Tirage(NamedTuple):
    """Ce qu'un envoi de formulaire de tirage a produit."""
    issue: str
    mode: str
    numeros: tuple[int, ...] = ()                 # affichés par ce tirage
    ecartes: tuple[tuple[str, str], ...] = ()      # (saisie, raison)
    tirage_id: int | None = None
    numero_max: int | None = None
    possibles: int | None = None                   # aléatoire : numéros qui restaient
    saisie_max: str = ""


def _tirage_existant(conn: sqlite3.Connection, jeton: str) -> tuple[int, str, int | None, tuple[int, ...]] | None:
    ligne = conn.execute(
        "SELECT id, mode, numero_max FROM tombola_tirages WHERE jeton_formulaire = ?",
        (jeton,),
    ).fetchone()
    if ligne is None:
        return None
    numeros = tuple(n for (n,) in conn.execute(
        "SELECT numero FROM tombola_numeros WHERE tirage_id = ? AND "
        "(motif IS NULL OR motif <> ?) ORDER BY numero",
        (ligne["id"], MOTIF_ERREUR_SAISIE),
    ))
    return ligne["id"], ligne["mode"], ligne["numero_max"], numeros


def _enregistrer_tirage(conn: sqlite3.Connection, edition_id: int | None,
                        mode: str, numeros: Sequence[int], jeton: str,
                        numero_max: int | None, instant: datetime
                        ) -> tuple[int | None, tuple[int, ...], tuple[int, ...]]:
    """
    Écrit le tirage et ses numéros, en ouvrant l'édition s'il n'y en a pas.
    À appeler DANS la transaction du tirage. Renvoie (tirage_id, numéros
    écrits, numéros refusés par l'index d'unicité).

    Le refus par l'index n'arrive pas en temps normal : la transaction a lu
    les numéros sortis sous le verrou. Il est rattrapé numéro par numéro
    (SQLite n'annule que l'instruction fautive) ; si plus rien ne reste, le
    tirage — et l'édition qu'il venait d'ouvrir — sont retirés : jamais de
    tirage vide.
    """
    horodatage = _iso(instant)
    ouverte_ici = edition_id is None
    if ouverte_ici:
        edition_id = conn.execute(
            "INSERT INTO tombola_editions (ouverte_le) VALUES (?)", (horodatage,)
        ).lastrowid
    tirage_id = conn.execute(
        "INSERT INTO tombola_tirages (edition_id, tire_le, mode, numero_max, "
        "jeton_formulaire) VALUES (?, ?, ?, ?, ?)",
        (edition_id, horodatage, mode, numero_max, jeton),
    ).lastrowid
    ecrits, refuses = [], []
    for numero in numeros:
        try:
            conn.execute(
                "INSERT INTO tombola_numeros (tirage_id, edition_id, numero, "
                "affiche_le) VALUES (?, ?, ?, ?)",
                (tirage_id, edition_id, numero, horodatage),
            )
            ecrits.append(numero)
        except sqlite3.IntegrityError:
            refuses.append(numero)
    if not ecrits:
        conn.execute("DELETE FROM tombola_tirages WHERE id = ?", (tirage_id,))
        if ouverte_ici:
            conn.execute("DELETE FROM tombola_editions WHERE id = ?", (edition_id,))
        return None, (), tuple(refuses)
    return tirage_id, tuple(sorted(ecrits)), tuple(refuses)


def saisir_tirage(conn: sqlite3.Connection, saisies: Sequence[str | None],
                  jeton: str | None, instant: datetime | None = None) -> Tirage:
    """
    Saisie MANUELLE de 1 à 5 numéros tirés à la main (le cas normal).

    Chaque numéro est jugé seul : les valides sont affichés, les autres
    écartés avec leur raison (illisible ou hors bornes, tapé deux fois dans
    ce tirage, déjà sorti pendant l'édition). Sans aucun numéro valide, RIEN
    n'est écrit — ni tirage vide, ni édition — et la route réaffiche la
    saisie.

    Un second envoi avec le même `jeton` ne crée rien : il renvoie le tirage
    du premier (issue `TIRAGE_REJOUE`).
    """
    moment = _instant(instant)
    jeton = _jeton_valide(jeton)
    remplies = [s for s in saisies if (s or "").strip()]
    if not remplies:
        return Tirage(TIRAGE_VIDE, MODE_MANUEL)
    with services.transaction(conn):
        existant = _tirage_existant(conn, jeton)
        if existant:
            tirage_id, mode, numero_max, numeros = existant
            return Tirage(TIRAGE_REJOUE, mode, numeros, (), tirage_id, numero_max)
        edition_id = edition_ouverte(conn)
        sortis = _numeros_sortis(conn, edition_id)
        retenus: list[int] = []
        ecartes: list[tuple[str, str]] = []
        for saisie in remplies:
            numero = lire_numero(saisie)
            texte = " ".join((saisie or "").split())
            if numero is None:
                ecartes.append((texte, ECART_ILLISIBLE))
            elif numero in retenus:
                ecartes.append((str(numero), ECART_DOUBLON))
            elif numero in sortis:
                ecartes.append((str(numero), ECART_DEJA_SORTI))
            else:
                retenus.append(numero)
        if not retenus:
            return Tirage(TIRAGE_AUCUN_VALIDE, MODE_MANUEL, (), tuple(ecartes))
        tirage_id, ecrits, refuses = _enregistrer_tirage(
            conn, edition_id, MODE_MANUEL, retenus, jeton, None, moment)
        ecartes.extend((str(n), ECART_DEJA_SORTI) for n in refuses)
    if tirage_id is None:
        return Tirage(TIRAGE_AUCUN_VALIDE, MODE_MANUEL, (), tuple(ecartes))
    return Tirage(TIRAGE_ENREGISTRE, MODE_MANUEL, ecrits, tuple(ecartes), tirage_id)


def tirer_au_hasard(conn: sqlite3.Connection, saisie_max: str | None,
                    jeton: str | None, instant: datetime | None = None,
                    generateur=None) -> Tirage:
    """
    Tirage PAR L'APPLICATION, en secours : 5 numéros au hasard parmi
    {1 … max} moins ceux déjà sortis dans l'édition (sauf erreurs de saisie).
    ENREGISTRÉ ET AFFICHÉ DÈS L'APPUI : il n'existe aucun moyen de le
    reprendre, car pouvoir refaire un tirage, c'est pouvoir choisir les
    gagnants (décision 3).

    Moins de 5 possibles : on tire ceux qui restent. Aucun : rien n'est
    écrit. Maximum illisible ou hors bornes : rien n'est écrit. Un maximum
    valide est retenu dans `tombola_numero_max`, dans la même transaction.

    `generateur` : un objet doté de `.sample`, injecté par les tests. En
    production, `secrets.SystemRandom()` — jamais le module `random`, dont
    la suite se devine.
    """
    moment = _instant(instant)
    jeton = _jeton_valide(jeton)
    saisie_max = " ".join((saisie_max or "").split())[:20]
    numero_max = lire_numero(saisie_max)
    if numero_max is None:
        return Tirage(TIRAGE_MAX_INVALIDE, MODE_ALEATOIRE, saisie_max=saisie_max)
    generateur = generateur or secrets.SystemRandom()
    with services.transaction(conn):
        existant = _tirage_existant(conn, jeton)
        if existant:
            tirage_id, mode, ancien_max, numeros = existant
            return Tirage(TIRAGE_REJOUE, mode, numeros, (), tirage_id, ancien_max)
        # `services.ecrire_parametre` committe : on écrit ici sous le même
        # verrou, pour que tout le tirage reste indivisible.
        conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (CLE_NUMERO_MAX, str(numero_max)),
        )
        edition_id = edition_ouverte(conn)
        sortis = _numeros_sortis(conn, edition_id)
        possibles = [n for n in range(NUMERO_MIN, numero_max + 1) if n not in sortis]
        if not possibles:
            return Tirage(TIRAGE_EPUISE, MODE_ALEATOIRE, numero_max=numero_max,
                          possibles=0)
        tires = generateur.sample(possibles, min(NUMEROS_PAR_TIRAGE, len(possibles)))
        tirage_id, ecrits, _ = _enregistrer_tirage(
            conn, edition_id, MODE_ALEATOIRE, tires, jeton, numero_max, moment)
    if tirage_id is None:
        return Tirage(TIRAGE_EPUISE, MODE_ALEATOIRE, numero_max=numero_max, possibles=0)
    return Tirage(TIRAGE_ENREGISTRE, MODE_ALEATOIRE, ecrits, (), tirage_id,
                  numero_max, len(possibles))


# ---------------------------------------------------------------------------
# Remise d'un lot, annulation, erreur de saisie
# ---------------------------------------------------------------------------
REMISE_FAITE = "remis"
REMISE_DEJA = "deja_remis"
REMISE_JAMAIS_TIRE = "jamais_tire"
REMISE_ERREUR_SAISIE = "erreur_saisie"
REMISE_ILLISIBLE = "illisible"
REMISE_NON_RECLAME = "non_reclame"

ANNULATION_FAITE = "annulee"
ANNULATION_TROP_TARD = "trop_tard"
ANNULATION_DEJA_AFFICHE = "deja_affiche"
ANNULATION_IMPOSSIBLE = "impossible"   # ligne inconnue, ou plus « remis »

RETRAIT_FAIT = "retire"
RETRAIT_TROP_TARD = "trop_tard"
RETRAIT_DEJA = "deja_retire"
RETRAIT_PLUS_AFFICHE = "plus_affiche"   # remis ou non réclamé entre-temps
RETRAIT_INCONNU = "inconnu"


class Geste(NamedTuple):
    """Issue d'une remise, d'une annulation ou d'un retrait pour erreur."""
    issue: str
    numero: int | None = None
    numero_id: int | None = None
    sorti_le: str | None = None      # pour « déjà remis à 15:12 »
    annulable: bool = False          # remise annulable à l'instant du geste
    ecrit: bool = False              # le geste a changé la base (→ journal)


def _ligne(conn: sqlite3.Connection, numero_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT n.id, n.numero, n.affiche_le, n.sorti_le, n.motif, "
        "       e.close_le IS NULL AS edition_ouverte "
        "FROM tombola_numeros n JOIN tombola_editions e ON e.id = n.edition_id "
        "WHERE n.id = ?",
        (numero_id,),
    ).fetchone()


def _annulable(sorti_le: str | None, moment: datetime) -> bool:
    ecart = _ecart_s(sorti_le, moment)
    return ecart is not None and 0 <= ecart < DELAI_ANNULATION_REMISE_S


def _remettre(conn: sqlite3.Connection, ligne: sqlite3.Row, moment: datetime) -> Geste:
    """Remise d'une ligne lue sous le verrou. Ne réécrit jamais un `sorti_le`."""
    base = {"numero": ligne["numero"], "numero_id": ligne["id"]}
    if ligne["motif"] is None:
        horodatage = _iso(moment)
        conn.execute(
            "UPDATE tombola_numeros SET motif = ?, sorti_le = ? "
            "WHERE id = ? AND motif IS NULL",
            (MOTIF_REMIS, horodatage, ligne["id"]),
        )
        return Geste(REMISE_FAITE, sorti_le=horodatage, annulable=True, ecrit=True, **base)
    if ligne["motif"] == MOTIF_REMIS:
        return Geste(REMISE_DEJA, sorti_le=ligne["sorti_le"],
                     annulable=bool(ligne["edition_ouverte"])
                     and _annulable(ligne["sorti_le"], moment), **base)
    if ligne["motif"] == MOTIF_ERREUR_SAISIE:
        return Geste(REMISE_ERREUR_SAISIE, **base)
    return Geste(REMISE_NON_RECLAME, **base)


def remettre_par_numero(conn: sqlite3.Connection, saisie: str | None,
                        instant: datetime | None = None) -> Geste:
    """
    « Lot remis » par le NUMÉRO PRÉSENTÉ (champ du haut de la page).
    Cherche le numéro dans l'édition ouverte ; une ligne en erreur de saisie
    ne compte que s'il n'en existe pas d'autre (numéro ressaisi correctement).
    """
    moment = _instant(instant)
    numero = lire_numero(saisie)
    if numero is None:
        return Geste(REMISE_ILLISIBLE)
    with services.transaction(conn):
        edition_id = edition_ouverte(conn)
        ligne = None
        if edition_id is not None:
            ligne = conn.execute(
                "SELECT n.id, n.numero, n.affiche_le, n.sorti_le, n.motif, "
                "       1 AS edition_ouverte "
                "FROM tombola_numeros n WHERE n.edition_id = ? AND n.numero = ? "
                "ORDER BY (n.motif IS NOT NULL AND n.motif = ?), n.id DESC LIMIT 1",
                (edition_id, numero, MOTIF_ERREUR_SAISIE),
            ).fetchone()
        if ligne is None:
            return Geste(REMISE_JAMAIS_TIRE, numero=numero)
        return _remettre(conn, ligne, moment)


def remettre_par_id(conn: sqlite3.Connection, numero_id: int,
                    instant: datetime | None = None) -> Geste:
    """
    « Lot remis » par le bouton d'un numéro en attente. Vise la LIGNE, pas le
    numéro : un bouton resté ouvert ne peut pas remettre un autre lot.
    """
    moment = _instant(instant)
    with services.transaction(conn):
        ligne = _ligne(conn, numero_id)
        if ligne is None:
            return Geste(REMISE_JAMAIS_TIRE)
        if not ligne["edition_ouverte"] and ligne["motif"] is None:
            # Ne devrait pas exister : la clôture passe tout en non réclamé.
            return Geste(REMISE_NON_RECLAME, ligne["numero"], ligne["id"])
        return _remettre(conn, ligne, moment)


def annuler_remise(conn: sqlite3.Connection, numero_id: int,
                   instant: datetime | None = None) -> Geste:
    """
    Annule une remise faite il y a moins de `DELAI_ANNULATION_REMISE_S` : le
    numéro revient à l'affichage (`motif` et `sorti_le` à NULL). Vise
    l'identifiant de la LIGNE. Au-delà du délai, ou pour une ligne qui n'est
    plus « remis » (édition close, autre geste entre-temps), rien ne change.
    """
    moment = _instant(instant)
    with services.transaction(conn):
        ligne = _ligne(conn, numero_id)
        if ligne is None:
            return Geste(ANNULATION_IMPOSSIBLE)
        base = {"numero": ligne["numero"], "numero_id": ligne["id"]}
        if ligne["motif"] is None:
            return Geste(ANNULATION_DEJA_AFFICHE, **base)
        if ligne["motif"] != MOTIF_REMIS or not ligne["edition_ouverte"]:
            return Geste(ANNULATION_IMPOSSIBLE, **base)
        if not _annulable(ligne["sorti_le"], moment):
            return Geste(ANNULATION_TROP_TARD, sorti_le=ligne["sorti_le"], **base)
        conn.execute(
            "UPDATE tombola_numeros SET motif = NULL, sorti_le = NULL "
            "WHERE id = ? AND motif = ?",
            (ligne["id"], MOTIF_REMIS),
        )
    return Geste(ANNULATION_FAITE, ecrit=True, **base)


def erreur_possible(affiche_le: str | None, moment: datetime) -> bool:
    """Vrai si un numéro affiché à `affiche_le` peut encore être retiré pour erreur."""
    ecart = _ecart_s(affiche_le, moment)
    return ecart is not None and 0 <= ecart < DELAI_ERREUR_SAISIE_S


def retirer_erreur_saisie(conn: sqlite3.Connection, numero_id: int,
                          instant: datetime | None = None) -> Geste:
    """
    Retire de l'affichage un numéro mal tapé, SANS le compter comme un lot
    remis (motif `erreur_saisie`, hors statistiques et hors unicité : il peut
    être ressaisi). Seulement pendant `DELAI_ERREUR_SAISIE_S` après son
    affichage, et seulement s'il est encore affiché.
    """
    moment = _instant(instant)
    with services.transaction(conn):
        ligne = _ligne(conn, numero_id)
        if ligne is None:
            return Geste(RETRAIT_INCONNU)
        base = {"numero": ligne["numero"], "numero_id": ligne["id"]}
        if ligne["motif"] == MOTIF_ERREUR_SAISIE:
            return Geste(RETRAIT_DEJA, **base)
        if ligne["motif"] is not None or not ligne["edition_ouverte"]:
            return Geste(RETRAIT_PLUS_AFFICHE, **base)
        if not erreur_possible(ligne["affiche_le"], moment):
            return Geste(RETRAIT_TROP_TARD, **base)
        conn.execute(
            "UPDATE tombola_numeros SET motif = ?, sorti_le = ? "
            "WHERE id = ? AND motif IS NULL",
            (MOTIF_ERREUR_SAISIE, _iso(moment), ligne["id"]),
        )
    return Geste(RETRAIT_FAIT, ecrit=True, **base)


# ---------------------------------------------------------------------------
# Corriger une faute de frappe dans le tirage d'origine (lot 2, POINT 5)
# ---------------------------------------------------------------------------
REMPLACEMENT_FAIT = "remplace"
REMPLACEMENT_REJOUE = "rejoue"           # même numéro déjà mis à sa place
REMPLACEMENT_DEJA = "deja_remplace"      # un autre numéro a déjà pris la place
REMPLACEMENT_VIDE = "vide"               # rien de tapé : le retrait seul reste
REMPLACEMENT_ILLISIBLE = "illisible"
REMPLACEMENT_DEJA_SORTI = "deja_sorti"
REMPLACEMENT_TROP_TARD = "trop_tard"
REMPLACEMENT_IMPOSSIBLE = "impossible"   # ligne inconnue, pas en erreur, édition close


class Remplacement(NamedTuple):
    """Issue d'un « Remplacer » : le numéro retiré, et celui qui le remplace."""
    issue: str
    ancien: int | None = None
    nouveau: int | None = None
    retire_id: int | None = None     # ligne retirée : le formulaire peut revenir
    ecrit: bool = False


def remplacer_numero(conn: sqlite3.Connection, retire_id: int,
                     saisie: str | None, instant: datetime | None = None
                     ) -> Remplacement:
    """
    Met le numéro correct à la place d'un numéro retiré pour erreur de
    saisie, DANS LE MÊME TIRAGE : un tirage de 15:02 reste un seul tirage sur
    l'écran de salle et dans les statistiques, au lieu d'en devenir deux.

    - `affiche_le` = maintenant : c'est l'instant réel où le gagnant peut lire
      son numéro, et c'est lui qui compte pour le délai de remise.
    - Possible pendant `DELAI_ERREUR_SAISIE_S` après le RETRAIT (arbitrage du
      lot 2) : compté depuis l'affichage d'origine, un retrait fait à 4 min 50
      ne laisserait que dix secondes pour taper le bon numéro.
    - Au plus UN remplacement par ligne retirée (`remplace_id`, index unique
      partiel) : un double appui, ou deux bénévoles qui corrigent la même
      faute, trouvent la correction déjà faite. Un second envoi du MÊME
      numéro est un rejeu (rien d'écrit, message de succès).
    - Un numéro déjà sorti dans l'édition est refusé, comme à la saisie.
    - Sans saisie, rien ne se passe : le retrait seul reste valable.
    """
    moment = _instant(instant)
    texte = (saisie or "").strip()
    with services.transaction(conn):
        ligne = conn.execute(
            "SELECT n.id, n.numero, n.tirage_id, n.edition_id, n.sorti_le, n.motif, "
            "       e.close_le IS NULL AS edition_ouverte "
            "FROM tombola_numeros n JOIN tombola_editions e ON e.id = n.edition_id "
            "WHERE n.id = ?",
            (retire_id,),
        ).fetchone()
        if (ligne is None or ligne["motif"] != MOTIF_ERREUR_SAISIE
                or not ligne["edition_ouverte"]):
            return Remplacement(REMPLACEMENT_IMPOSSIBLE,
                                ligne["numero"] if ligne else None)
        base = {"ancien": ligne["numero"], "retire_id": ligne["id"]}
        deja = conn.execute(
            "SELECT numero FROM tombola_numeros WHERE remplace_id = ?",
            (ligne["id"],),
        ).fetchone()
        numero = lire_numero(texte)
        if deja is not None:
            issue = REMPLACEMENT_REJOUE if deja[0] == numero else REMPLACEMENT_DEJA
            return Remplacement(issue, ligne["numero"], deja[0])
        if not texte:
            return Remplacement(REMPLACEMENT_VIDE, **base)
        if numero is None:
            return Remplacement(REMPLACEMENT_ILLISIBLE, **base)
        ecart = _ecart_s(ligne["sorti_le"], moment)
        if ecart is None or not 0 <= ecart < DELAI_ERREUR_SAISIE_S:
            return Remplacement(REMPLACEMENT_TROP_TARD, ligne["numero"], numero)
        if numero in _numeros_sortis(conn, ligne["edition_id"]):
            return Remplacement(REMPLACEMENT_DEJA_SORTI, nouveau=numero, **base)
        try:
            conn.execute(
                "INSERT INTO tombola_numeros (tirage_id, edition_id, numero, "
                "affiche_le, remplace_id) VALUES (?, ?, ?, ?, ?)",
                (ligne["tirage_id"], ligne["edition_id"], numero, _iso(moment),
                 ligne["id"]),
            )
        except sqlite3.IntegrityError:
            # Filets des index : ne devrait pas arriver sous le verrou.
            return Remplacement(REMPLACEMENT_DEJA_SORTI, nouveau=numero, **base)
    return Remplacement(REMPLACEMENT_FAIT, ligne["numero"], numero, ecrit=True)


# ---------------------------------------------------------------------------
# Lecture pour la page de gestion
# ---------------------------------------------------------------------------
def numeros_en_attente(conn: sqlite3.Connection,
                       instant: datetime | None = None) -> list[dict]:
    """
    Les numéros AFFICHÉS (`motif IS NULL`) de l'édition ouverte, groupés par
    tirage, le plus récent d'abord ; dans un tirage, par ordre croissant.
    Chaque groupe : `{tirage_id, heure, mode, numeros: [{id, numero,
    erreur_possible}]}`. Lecture seule.
    """
    moment = _instant(instant)
    edition_id = edition_ouverte(conn)
    if edition_id is None:
        return []
    groupes: dict[int, dict] = {}
    for ligne in conn.execute(
        "SELECT n.id, n.numero, n.affiche_le, t.id AS tirage_id, t.tire_le, t.mode "
        "FROM tombola_numeros n JOIN tombola_tirages t ON t.id = n.tirage_id "
        "WHERE n.edition_id = ? AND n.motif IS NULL "
        "ORDER BY t.id DESC, n.numero",
        (edition_id,),
    ):
        groupe = groupes.setdefault(ligne["tirage_id"], {
            "tirage_id": ligne["tirage_id"],
            "heure": heure_locale(ligne["tire_le"]),
            "mode": ligne["mode"],
            "numeros": [],
        })
        groupe["numeros"].append({
            "id": ligne["id"],
            "numero": ligne["numero"],
            "erreur_possible": erreur_possible(ligne["affiche_le"], moment),
        })
    return list(groupes.values())


# ---------------------------------------------------------------------------
# Lecture PUBLIQUE — écran de salle, page /tombola, accueil (lot 2, POINT 1)
# ---------------------------------------------------------------------------
def numeros_affiches(conn: sqlite3.Connection,
                     instant: datetime | None = None) -> list[dict]:
    """
    Les numéros AFFICHÉS de l'édition ouverte, groupés par tirage, le plus
    récent d'abord ; dans un tirage, par ordre croissant. Chaque groupe :
    `{tirage_id, heure, numeros: [int, …], nouveau}`. LECTURE SEULE, sans
    aucune écriture : `/live/data` la relit toutes les dix secondes.

    Sœur publique de `numeros_en_attente`, et SEUL domicile de ce que voit le
    public : ni identifiant de ligne, ni drapeau de gestion (`erreur_possible`),
    ni mode du tirage. `tirage_id` sert au défilement de l'écran de salle à
    reconnaître la page courante d'un rafraîchissement à l'autre (patron des
    annonces) ; ce n'est pas une donnée sensible. `nouveau` : tirage de moins
    de `DUREE_NOUVEAU_TIRAGE_S`.
    """
    moment = _instant(instant)
    edition_id = edition_ouverte(conn)
    if edition_id is None:
        return []
    groupes: dict[int, dict] = {}
    for ligne in conn.execute(
        "SELECT n.numero, t.id AS tirage_id, t.tire_le "
        "FROM tombola_numeros n JOIN tombola_tirages t ON t.id = n.tirage_id "
        "WHERE n.edition_id = ? AND n.motif IS NULL "
        "ORDER BY t.id DESC, n.numero",
        (edition_id,),
    ):
        groupe = groupes.get(ligne["tirage_id"])
        if groupe is None:
            ecart = _ecart_s(ligne["tire_le"], moment)
            groupe = groupes[ligne["tirage_id"]] = {
                "tirage_id": ligne["tirage_id"],
                "heure": heure_locale(ligne["tire_le"]),
                "numeros": [],
                "nouveau": ecart is not None and 0 <= ecart < DUREE_NOUVEAU_TIRAGE_S,
            }
        groupe["numeros"].append(ligne["numero"])
    return list(groupes.values())


# ---------------------------------------------------------------------------
# Statistiques (lot 2, POINT 6) — page /stats et ses deux exports
# ---------------------------------------------------------------------------
LIBELLES_MODE = {MODE_MANUEL: "à la main", MODE_ALEATOIRE: "par l'application"}


def _mediane(delais: list[float]) -> str:
    """Médiane formatée (`services.format_duree`), « — » sans aucun délai."""
    return services.format_duree(statistics.median(delais)) if delais else "—"


def statistiques(conn: sqlite3.Connection, debut_utc: str | None = None,
                 fin_utc: str | None = None) -> dict | None:
    """
    Les chiffres de la tombola pour une période (bornes UTC ISO, `fin`
    exclusive, comme `services.collecter_stats`), ou None s'il n'y a eu
    AUCUN tirage dans la période : la section n'apparaît alors nulle part.

    Un numéro compte dans la période de SON TIRAGE (`tire_le`), quelle que
    soit l'heure de sa remise ou de son remplacement.

    - `tires` : numéros tirés, hors erreurs de saisie ;
    - `remis`, `affiches`, `non_reclames` : leur partage (somme = `tires`) ;
    - `erreurs_saisie` : comptées à part, hors de TOUS les autres chiffres ;
    - `delai_median` : `sorti_le − affiche_le` sur les seuls `remis` — jamais
      les `non_reclame`, dont `sorti_le` est l'heure de la clôture. Un délai
      négatif (horloge du serveur revenue en arrière) est ÉCARTÉ et compté
      dans `delais_ecartes` : le ramener à zéro tirerait la médiane vers le
      bas sans que rien ne le dise ;
    - `par_tirage`, `par_mode`, `par_edition` : mêmes règles, ventilées.
      `par_edition` n'a de sens qu'à partir de deux éditions dans la période
      (le gabarit ne l'affiche qu'alors).

    Aucun identifiant de ligne n'en sort : les tirages sont désignés par leur
    heure.
    """
    conditions, params = [], []
    if debut_utc:
        conditions.append("t.tire_le >= ?")
        params.append(debut_utc)
    if fin_utc:
        conditions.append("t.tire_le < ?")
        params.append(fin_utc)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    lignes = conn.execute(
        "SELECT t.id AS tirage_id, t.tire_le, t.mode, t.edition_id, "
        "       e.ouverte_le, e.close_le, n.motif, n.affiche_le, n.sorti_le "
        "FROM tombola_tirages t "
        "JOIN tombola_editions e ON e.id = t.edition_id "
        "JOIN tombola_numeros n ON n.tirage_id = t.id"
        f"{where} ORDER BY t.tire_le, t.id",
        params,
    ).fetchall()
    if not lignes:
        return None

    def compteur() -> dict:
        return {"tires": 0, "remis": 0, "affiches": 0, "non_reclames": 0,
                "erreurs_saisie": 0, "delais": [], "tirages": set()}

    total = compteur()
    ecartes = 0
    par_tirage: dict[int, dict] = {}
    par_mode: dict[str, dict] = {}
    par_edition: dict[int, dict] = {}
    for l in lignes:
        tirage = par_tirage.setdefault(l["tirage_id"], {
            **compteur(), "quand": services.format_local(l["tire_le"]),
            "mode": LIBELLES_MODE.get(l["mode"], l["mode"])})
        mode = par_mode.setdefault(l["mode"], {
            **compteur(), "mode": LIBELLES_MODE.get(l["mode"], l["mode"])})
        edition = par_edition.setdefault(l["edition_id"], {
            **compteur(), "ouverte": services.format_local(l["ouverte_le"]),
            "close": services.format_local(l["close_le"]) if l["close_le"] else ""})
        cibles = (total, tirage, mode, edition)
        for c in cibles:
            c["tirages"].add(l["tirage_id"])
        if l["motif"] == MOTIF_ERREUR_SAISIE:
            for c in cibles:
                c["erreurs_saisie"] += 1
            continue
        champ = {None: "affiches", MOTIF_REMIS: "remis",
                 MOTIF_NON_RECLAME: "non_reclames"}.get(l["motif"])
        for c in cibles:
            c["tires"] += 1
            if champ:
                c[champ] += 1
        if l["motif"] == MOTIF_REMIS:
            debut, fin = _depuis_iso(l["affiche_le"]), _depuis_iso(l["sorti_le"])
            if debut is None or fin is None or fin < debut:
                ecartes += 1
                continue
            delai = (fin - debut).total_seconds()
            for c in cibles:
                c["delais"].append(delai)

    def figer(c: dict) -> dict:
        resultat = {k: v for k, v in c.items() if k not in ("delais", "tirages")}
        resultat["nb_tirages"] = len(c["tirages"])
        resultat["delai_median"] = _mediane(c["delais"])
        return resultat

    resultat = figer(total)
    resultat["delais_ecartes"] = ecartes
    resultat["par_tirage"] = [figer(t) for t in par_tirage.values()]
    resultat["par_mode"] = [figer(par_mode[m]) for m in (MODE_MANUEL, MODE_ALEATOIRE)
                            if m in par_mode]
    resultat["par_edition"] = [figer(e) for e in par_edition.values()]
    return resultat


# ---------------------------------------------------------------------------
# Messages — un seul domicile pour les phrases ; (niveau, texte, numéros en
# grand). Aucun n'est une erreur : « succes » ou « attention ».
# ---------------------------------------------------------------------------
class Message(NamedTuple):
    niveau: str                      # "succes" | "attention"
    texte: str
    details: tuple[str, ...] = ()    # une phrase par numéro écarté
    en_grand: tuple[int, ...] = ()   # numéros à afficher en grand
    annuler_id: int | None = None    # bouton « Annuler » de la remise
    remplacer_id: int | None = None  # formulaire « Numéro correct » (lot 2)


def _liste(numeros: Sequence[int]) -> str:
    return ", ".join(str(n) for n in numeros)


BORNES = f"entre {NUMERO_MIN} et {NUMERO_MAX}"

RAISONS_ECART = {
    ECART_ILLISIBLE: f"ce n'est pas un numéro {BORNES}",
    ECART_DOUBLON: "tapé deux fois dans ce tirage",
    ECART_DEJA_SORTI: "déjà sorti pendant cette tombola",
}


def message_tirage(t: Tirage) -> Message:
    """La phrase qui suit un envoi de formulaire de tirage."""
    details = tuple(f"« {saisie[:20]} » écarté : {RAISONS_ECART[raison]}."
                    for saisie, raison in t.ecartes)
    if t.issue == TIRAGE_VIDE:
        return Message("attention", "Tapez au moins un numéro : rien n'a été affiché.")
    if t.issue == TIRAGE_AUCUN_VALIDE:
        return Message("attention",
                       "Aucun numéro affiché : corrigez la saisie, puis envoyez-la à nouveau.",
                       details)
    if t.issue == TIRAGE_MAX_INVALIDE:
        saisie = f"« {t.saisie_max} » : " if t.saisie_max else ""
        return Message("attention",
                       f"{saisie}tapez le plus grand numéro vendu, {BORNES}. "
                       "Rien n'a été tiré.")
    if t.issue == TIRAGE_EPUISE:
        return Message("attention",
                       f"Tous les numéros de {NUMERO_MIN} à {t.numero_max} sont déjà "
                       "sortis : rien n'a été tiré. Vérifiez le plus grand numéro vendu.")
    if t.issue == TIRAGE_REJOUE:
        return Message("succes",
                       f"Numéros affichés : {_liste(t.numeros)}. Le formulaire est "
                       "parti deux fois : un seul tirage a été enregistré.",
                       en_grand=t.numeros)
    if t.mode == MODE_ALEATOIRE:
        texte = f"Numéros tirés : {_liste(t.numeros)}."
        if len(t.numeros) < NUMEROS_PAR_TIRAGE:
            texte = (f"Il ne restait que {len(t.numeros)} "
                     f"{services.pluriel(len(t.numeros), 'numéro', 'numéros')} "
                     f"possible{'s' if len(t.numeros) > 1 else ''} entre "
                     f"{NUMERO_MIN} et {t.numero_max} : {_liste(t.numeros)}.")
        return Message("succes", texte, en_grand=t.numeros)
    niveau = "attention" if details else "succes"
    return Message(niveau, f"Numéros affichés : {_liste(t.numeros)}.", details,
                   en_grand=t.numeros)


def message_remise(g: Geste) -> Message:
    """La phrase qui suit « Lot remis », par le champ ou par un bouton."""
    if g.issue == REMISE_FAITE:
        return Message("succes", f"Lot remis : numéro {g.numero}.",
                       annuler_id=g.numero_id)
    if g.issue == REMISE_DEJA:
        return Message("attention",
                       f"Le lot du numéro {g.numero} a déjà été remis à "
                       f"{heure_locale(g.sorti_le)}.",
                       annuler_id=g.numero_id if g.annulable else None)
    if g.issue == REMISE_ERREUR_SAISIE:
        return Message("attention", f"Le numéro {g.numero} a été retiré : erreur de saisie.")
    if g.issue == REMISE_NON_RECLAME:
        return Message("attention",
                       f"Le numéro {g.numero} n'est plus affiché : la tombola a été clôturée.")
    if g.issue == REMISE_JAMAIS_TIRE and g.numero is not None:
        return Message("attention", f"Le numéro {g.numero} n'a pas été tiré.")
    if g.issue == REMISE_JAMAIS_TIRE:
        return Message("attention", "Ce numéro n'est plus dans la tombola en cours.")
    return Message("attention", f"Tapez un numéro {BORNES}.")


def message_annulation(g: Geste) -> Message:
    """La phrase qui suit « Annuler »."""
    if g.issue == ANNULATION_FAITE:
        return Message("succes",
                       f"Remise annulée : le numéro {g.numero} est de nouveau affiché.")
    if g.issue == ANNULATION_DEJA_AFFICHE:
        return Message("attention", f"Le numéro {g.numero} est déjà affiché.")
    if g.issue == ANNULATION_TROP_TARD:
        return Message("attention",
                       f"La remise du numéro {g.numero} date de plus d'une minute : "
                       "elle ne peut plus être annulée.")
    return Message("attention", "Cette remise ne peut pas être annulée.")


def message_retrait(g: Geste) -> Message:
    """La phrase qui suit « Erreur de saisie »."""
    if g.issue == RETRAIT_FAIT:
        return Message("succes",
                       f"Numéro {g.numero} retiré : erreur de saisie. Tapez le "
                       "numéro correct pour le remplacer dans le même tirage.",
                       remplacer_id=g.numero_id)
    if g.issue == RETRAIT_DEJA:
        return Message("attention", f"Le numéro {g.numero} a déjà été retiré : erreur de saisie.")
    if g.issue == RETRAIT_TROP_TARD:
        return Message("attention",
                       f"Le numéro {g.numero} est affiché depuis plus de "
                       f"{DELAI_ERREUR_SAISIE_S // 60} minutes : il ne peut plus être "
                       "retiré comme erreur de saisie.")
    if g.issue == RETRAIT_PLUS_AFFICHE:
        return Message("attention", f"Le numéro {g.numero} n'est plus affiché.")
    return Message("attention", "Ce numéro n'est plus dans la tombola en cours.")


def message_remplacement(r: Remplacement) -> Message:
    """La phrase qui suit « Remplacer ». Le formulaire revient tant qu'on peut corriger."""
    if r.issue == REMPLACEMENT_FAIT:
        return Message("succes",
                       f"Numéro {r.nouveau} affiché à la place du {r.ancien}, "
                       "dans le même tirage.", en_grand=(r.nouveau,))
    if r.issue == REMPLACEMENT_REJOUE:
        return Message("succes",
                       f"Numéro {r.nouveau} affiché à la place du {r.ancien}. Le "
                       "formulaire est parti deux fois : un seul remplacement a "
                       "été enregistré.", en_grand=(r.nouveau,))
    if r.issue == REMPLACEMENT_DEJA:
        return Message("attention",
                       f"Le numéro {r.ancien} a déjà été remplacé par le {r.nouveau}.")
    if r.issue == REMPLACEMENT_VIDE:
        return Message("attention",
                       f"Aucun numéro tapé : le {r.ancien} reste retiré, rien n'a "
                       "été ajouté.", remplacer_id=r.retire_id)
    if r.issue == REMPLACEMENT_ILLISIBLE:
        return Message("attention",
                       f"Tapez un numéro {BORNES} pour remplacer le {r.ancien}.",
                       remplacer_id=r.retire_id)
    if r.issue == REMPLACEMENT_DEJA_SORTI:
        return Message("attention",
                       f"Le numéro {r.nouveau} est déjà sorti pendant cette tombola : "
                       f"il ne peut pas remplacer le {r.ancien}.",
                       remplacer_id=r.retire_id)
    if r.issue == REMPLACEMENT_TROP_TARD:
        return Message("attention",
                       f"Le numéro {r.ancien} a été retiré il y a plus de "
                       f"{DELAI_ERREUR_SAISIE_S // 60} minutes : saisissez le "
                       "numéro correct comme un nouveau tirage.")
    return Message("attention", "Ce numéro ne peut plus être remplacé.")
