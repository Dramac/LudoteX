"""
Client de l'API XML de BoardGameGeek — SEUL module de LudoteX qui parle au réseau.

Le bureau relie un jeu à sa fiche BoardGameGeek (BGG) par un identifiant
(`app/bgg.py`, qui reste pur : aucun réseau). Ce module-ci va chercher, SUR
DEMANDE, ce que BGG sait de ces fiches, pour que la fiche d'administration
puisse afficher « BGG : 3 » à côté de nos cases. La copie est gardée en base
(`fiches_bgg`, voir `app/models.py`) : ouvrir une page ne provoque JAMAIS
d'appel réseau, et une panne de BGG n'empêche jamais d'ouvrir quoi que ce soit.

LE JETON
--------
BGG exige un jeton d'accès (« Bearer »), délivré sur demande pour un usage non
commercial. Il vit dans le fichier d'environnement de l'instance, variable
`BGG_TOKEN`, et il est lu ICI SEULEMENT (`_jeton`), à chaque appel, sans copie
dans une variable de module. Il ne part que dans l'en-tête `Authorization` :
jamais dans une adresse, un message, une page, le journal, ni une exception
remontée (les erreurs de `urllib` sont remplacées par `EchecBGG`, sans chaînage,
pour qu'aucune trace ne transporte la requête). Sans jeton, `disponible()` est
faux, les boutons ne s'affichent pas, et rien d'autre ne change.

CE QUI ENTRE DANS L'ADRESSE
---------------------------
Des identifiants ENTIERS, déjà validés (`bgg.lire_identifiant` à la saisie,
entier strictement positif en base), et rien d'autre : aucun texte tapé par le
bureau n'est jamais transmis à BGG.

LA RÉPONSE, D'ORIGINE EXTÉRIEURE
--------------------------------
- Bornée en temps (`DELAI_APPEL_S` au total, lecture comprise) et en taille
  (`TAILLE_MAX_OCTETS`) AVANT toute analyse.
- Une réponse qui déclare un DOCTYPE ou une entité est refusée sans être
  analysée : BGG n'en envoie jamais, et c'est la seule porte des attaques par
  expansion d'entités. En plus de ce refus, l'expat embarqué par Python borne
  lui-même l'amplification depuis sa version 2.4.1 (les deux serveurs de
  production portaient 2.6.1 et 2.8.3 le 2026-10-14 :
  `python -c "import pyexpat; print(pyexpat.EXPAT_VERSION)"`), et
  `xml.etree` ne va jamais chercher une entité externe.
- BGG note 0 pour « non renseigné » (déjà relevé dans `app/classement.py`) :
  0 devient None.

Ce module ne touche pas à la base : il rend des dictionnaires, que la route
écrit par `services.enregistrer_fiches_bgg`. Aucun appel n'est fait sous
transaction.
"""

from __future__ import annotations

import http.client
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

# Adresse de l'API. Le seul point d'entrée employé : `thing`, qui décrit des
# fiches à partir de leurs identifiants.
URL_THING = "https://boardgamegeek.com/xmlapi2/thing"

# BGG accepte 20 identifiants par appel `thing` (mesuré par la sonde du
# 2026-10-09, et c'est aussi la borne de ses conditions d'utilisation).
IDS_PAR_APPEL = 20

# Délai TOTAL d'un appel, connexion et lecture comprises. Un appel de 20 fiches
# a été mesuré entre 0,5 et 1 s depuis une connexion domestique (2026-10-14) :
# 15 s laissent une large marge à un wifi ou à un BGG lent, sans laisser un
# bureau devant un écran figé une minute.
DELAI_APPEL_S = 15

# Taille maximale lue. La plus grosse réponse du cache de la collecte du
# 2026-10-10 fait 315 Ko pour 20 fiches AVEC statistiques ; ce module ne les
# demande pas. 2 Mio, c'est plus de six fois ce maximum.
TAILLE_MAX_OCTETS = 2 * 1024 * 1024

# Pause entre deux appels SUCCESSIFS d'un même lot. BGG demande de ne pas
# enchaîner les appels et répond 429 à qui va trop vite ; la sonde et la
# collecte ont tenu sans un seul refus à 3 s. 5 s gardent une marge, au prix
# d'un lot un peu plus court (voir `BORNE_LOT_S`).
PAUSE_ENTRE_APPELS_S = 5

# Durée au-delà de laquelle un lot ne lance plus d'appel. On ne commence un
# appel que s'il peut se terminer, dans le pire des cas (pause + délai total),
# avant cette borne : une requête du bouton « Récupérer les données BGG
# manquantes » dure donc au plus 40 s, loin des 60 s au bout desquelles nginx
# abandonne (délai par défaut de `proxy_read_timeout`, aucune configuration du
# dépôt ni des serveurs ne le change — vérifié le 2026-10-14). En pratique,
# cinq appels de 20 fiches, soit 100 fiches en 25 s environ.
BORNE_LOT_S = 40

# Ce que BGG voit de nous.
USER_AGENT = "LudoteX (logiciel libre de pret de jeux, usage non commercial)"

# Bornes de lecture. Une valeur hors bornes est tenue pour absente plutôt que
# recopiée : la reprise dans le formulaire passerait de toute façon par la
# validation de la fiche (`services.lire_saisie_fiche`).
VALEUR_MAX = 99_999
LONGUEUR_NOM = 200
LONGUEUR_AUTEURS = 200

# Les deux types de fiche que la fiche d'administration distingue.
TYPE_JEU = "boardgame"
TYPE_EXTENSION = "boardgameexpansion"

# Les issues d'un échec : un code FERMÉ, un message qui dit quoi faire. Aucun
# ne cite le jeton. Les mêmes codes servent à l'URL de redirection (après un
# bouton), au journal (`detail`) et au wiki.
MESSAGES_ECHEC = {
    "sans_jeton": (
        "Ce site n'a pas de clé d'accès à BoardGameGeek : la récupération des "
        "données BGG n'est pas disponible. Demandez à la personne qui "
        "administre le serveur."),
    "jeton_refuse": (
        "BoardGameGeek refuse la clé d'accès de ce site. Rien n'a été modifié. "
        "Prévenez la personne qui administre le serveur : la clé est peut-être "
        "expirée ou mal recopiée."),
    "bloque": (
        "BoardGameGeek refuse les demandes venant de ce serveur. Rien n'a été "
        "modifié. Réessayez plus tard ; si cela dure, prévenez la personne qui "
        "administre le serveur."),
    "attendre": (
        "BoardGameGeek demande d'attendre avant de nouvelles demandes. Rien "
        "n'est perdu : réessayez dans quelques minutes."),
    "panne": (
        "BoardGameGeek ne répond pas pour le moment (site en panne, ou "
        "connexion coupée). Rien n'a été modifié : réessayez plus tard."),
    "illisible": (
        "La réponse de BoardGameGeek est illisible. Rien n'a été modifié : "
        "réessayez plus tard ; si cela se répète, prévenez la personne qui "
        "administre le serveur."),
}


class EchecBGG(Exception):
    """
    Un appel à BGG n'a pas abouti. `code` est une clé de `MESSAGES_ECHEC`.

    Le texte de l'exception est le message destiné au bureau : il ne porte
    jamais le jeton, ni l'adresse appelée, ni la réponse reçue.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES_ECHEC[code])


def _jeton() -> str | None:
    """Le jeton BGG de l'instance, ou None. SEUL endroit où il est lu."""
    return os.getenv("BGG_TOKEN", "").strip() or None


def disponible() -> bool:
    """True si l'instance a un jeton : les boutons BGG sont alors offerts."""
    return _jeton() is not None


# ---------------------------------------------------------------------------
# Lecture d'une réponse `thing` — fonctions pures, sans réseau
# ---------------------------------------------------------------------------
_HOMONYME = re.compile(r"\s*\((?:[IVXLC]+)\)$")


def _entier(element) -> int | None:
    """La valeur entière d'un élément `<x value="…"/>`, None si 0 ou absente."""
    if element is None:
        return None
    valeur = (element.get("value") or "").strip()
    if not valeur.isascii() or not valeur.isdigit():
        return None
    nombre = int(valeur)
    return nombre if 0 < nombre <= VALEUR_MAX else None


def _couper(texte: str, longueur: int) -> str:
    texte = " ".join(texte.split())
    return texte if len(texte) <= longueur else texte[:longueur].rstrip()


def _auteurs(item) -> str | None:
    """
    Les auteurs (« designers » chez BGG), joints par des virgules.

    « (Uncredited) » n'est pas un auteur. Le suffixe « (I) », « (II) »… est la
    façon dont BGG distingue deux homonymes : il n'est imprimé sur aucune boîte.
    Au-delà de `LONGUEUR_AUTEURS`, on s'arrête au dernier nom entier.
    """
    noms: list[str] = []
    for lien in item.findall("link[@type='boardgamedesigner']"):
        nom = _HOMONYME.sub("", " ".join((lien.get("value") or "").split()))
        if not nom or nom == "(Uncredited)" or nom in noms:
            continue
        if len(", ".join([*noms, nom])) > LONGUEUR_AUTEURS:
            break
        noms.append(nom)
    return ", ".join(noms) or None


def lire_fiche(item) -> dict | None:
    """Un `<item>` de la réponse en dictionnaire, ou None s'il est inutilisable."""
    identifiant = (item.get("id") or "").strip()
    if not identifiant.isascii() or not identifiant.isdigit() or int(identifiant) <= 0:
        return None
    type_fiche = (item.get("type") or "").strip()
    nom = None
    for element in item.findall("name"):
        if element.get("type") == "primary":
            nom = _couper(element.get("value") or "", LONGUEUR_NOM) or None
            break
    duree = _entier(item.find("playingtime"))
    return {
        "bgg_id": int(identifiant),
        "type": type_fiche if re.fullmatch(r"[a-z]{1,40}", type_fiche) else None,
        "nom": nom,
        "nb_joueurs_min": _entier(item.find("minplayers")),
        "nb_joueurs_max": _entier(item.find("maxplayers")),
        "age_min": _entier(item.find("minage")),
        # Notre `duree_min` est la borne basse ; BGG donne une plage et une
        # durée « moyenne » (`playingtime`), qui sert de repli à chaque borne.
        "duree_min": _entier(item.find("minplaytime")) or duree,
        "duree_max": _entier(item.find("maxplaytime")) or duree,
        "auteurs": _auteurs(item),
    }


def lire_reponse(octets: bytes) -> dict[int, dict]:
    """
    Les fiches d'une réponse `thing`, par identifiant.

    Un identifiant demandé et absent de la réponse est inconnu de BGG : c'est
    à l'appelant de le constater. Lève `EchecBGG("illisible")` sur tout ce qui
    n'est pas une liste `<items>` bien formée.
    """
    # Refusé AVANT analyse. Aucune réponse de BGG ne déclare de DOCTYPE.
    if b"<!DOCTYPE" in octets or b"<!ENTITY" in octets:
        raise EchecBGG("illisible")
    try:
        racine = ET.fromstring(octets)
    except (ET.ParseError, ValueError):
        raise EchecBGG("illisible") from None
    if racine.tag != "items":
        raise EchecBGG("illisible")
    fiches: dict[int, dict] = {}
    for item in racine.findall("item"):
        fiche = lire_fiche(item)
        if fiche is not None:
            fiches[fiche["bgg_id"]] = fiche
    return fiches


# ---------------------------------------------------------------------------
# L'appel réseau
# ---------------------------------------------------------------------------
def _telecharger(url: str, jeton: str) -> bytes:
    """
    GET `url` avec le jeton en en-tête, borné en temps et en taille.

    Lève `EchecBGG` pour toute issue autre qu'un 200 lisible. Les exceptions
    de `urllib` ne remontent JAMAIS : `from None` coupe la chaîne, pour
    qu'aucune trace ne transporte la requête et son en-tête.
    """
    requete = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {jeton}",
        "User-Agent": USER_AGENT,
        "Accept": "application/xml",
    })
    fin = time.monotonic() + DELAI_APPEL_S
    try:
        with urllib.request.urlopen(requete, timeout=DELAI_APPEL_S) as reponse:
            statut = reponse.status
            if statut == 202:          # BGG a mis la demande en file d'attente
                raise EchecBGG("attendre")
            if statut != 200:
                raise EchecBGG("panne")
            morceaux: list[bytes] = []
            lus = 0
            while True:
                if time.monotonic() > fin:
                    raise EchecBGG("panne")
                morceau = reponse.read(64 * 1024)
                if not morceau:
                    break
                lus += len(morceau)
                if lus > TAILLE_MAX_OCTETS:
                    raise EchecBGG("illisible")
                morceaux.append(morceau)
            return b"".join(morceaux)
    except EchecBGG:
        raise
    except urllib.error.HTTPError as erreur:
        code = erreur.code
        erreur.close()
        if code == 401:
            raise EchecBGG("jeton_refuse") from None
        if code == 403:
            raise EchecBGG("bloque") from None
        if code in (202, 429):
            raise EchecBGG("attendre") from None
        raise EchecBGG("panne") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            http.client.HTTPException):
        # Réseau coupé, nom introuvable, délai dépassé, connexion réinitialisée,
        # réponse coupée en cours de lecture (`IncompleteRead`, qui n'est pas
        # une OSError).
        raise EchecBGG("panne") from None


def recuperer(ids: list[int]) -> dict[int, dict | None]:
    """
    Les fiches BGG de `ids` (20 au plus), en UN appel.

    Returns:
        {identifiant: fiche, ou None si BGG ne connaît pas cet identifiant}.
        Chaque identifiant demandé a sa clé.

    Raises:
        EchecBGG: jeton absent, refusé, BGG qui demande d'attendre, panne,
            réponse illisible. Rien n'est alors à enregistrer.
    """
    ids = sorted({int(i) for i in ids if int(i) > 0})
    if not ids:
        return {}
    if len(ids) > IDS_PAR_APPEL:
        raise ValueError(f"{IDS_PAR_APPEL} identifiants au plus par appel")
    jeton = _jeton()
    if jeton is None:
        raise EchecBGG("sans_jeton")
    url = f"{URL_THING}?id={','.join(str(i) for i in ids)}"
    fiches = lire_reponse(_telecharger(url, jeton))
    return {i: fiches.get(i) for i in ids}


def recuperer_lot(ids: list[int], *,
                  horloge: Callable[[], float] = time.monotonic,
                  dormir: Callable[[float], None] = time.sleep,
                  ) -> tuple[dict[int, dict | None], EchecBGG | None]:
    """
    Récupère autant de fiches de `ids` que la borne de temps le permet.

    Appels de `IDS_PAR_APPEL`, séparés de `PAUSE_ENTRE_APPELS_S`. Un appel
    n'est lancé que s'il peut finir, au pire, avant `BORNE_LOT_S`. Le premier
    échec arrête le lot : ce qui a été obtenu avant reste acquis et est rendu.

    Returns:
        (fiches obtenues — même forme que `recuperer` —, échec éventuel).
    """
    debut = horloge()
    obtenues: dict[int, dict | None] = {}
    for rang in range(0, len(ids), IDS_PAR_APPEL):
        if rang:
            ecoule = horloge() - debut
            if ecoule + PAUSE_ENTRE_APPELS_S + DELAI_APPEL_S > BORNE_LOT_S:
                break
            dormir(PAUSE_ENTRE_APPELS_S)
        try:
            obtenues.update(recuperer(ids[rang:rang + IDS_PAR_APPEL]))
        except EchecBGG as echec:
            return obtenues, echec
    return obtenues, None
