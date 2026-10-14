"""
Chercher un jeu sur BoardGameGeek — le MOTEUR de « Chercher sur BGG ».

Série « fiche », lot 3. Porté du brouillon de la première passe d'association
(`collecte.py`, hors dépôt), qui a relié d'office les correspondances sûres du
catalogue de référence. Ce module PROPOSE des candidats classés ; c'est le
bureau qui décide (« C'est ce jeu »). Une égalité de nom n'est pas une
preuve : la première passe a vu un homonyme (« Far Away ») et un nom
alternatif trompeur (« Dobble Kids » tombé sur « Dobble »).

CE QUE FAIT UN CLIC
-------------------
1. Chercher les VARIANTES du nom (`variantes`), dans l'ordre, et s'arrêter à
   la première qui donne une égalité de nom. Si aucune ne donne rien du tout,
   essayer des formes RACCOURCIES (dernier mot ôté).
2. Garder les `NB_CANDIDATS` résultats les plus ressemblants (`ressemblance`),
   le type voulu d'abord à ressemblance égale.
3. Lire leurs fiches en UN appel `thing`, avec la popularité (`stats=1`).
4. Les classer (`note`) : ressemblance, accord de leurs valeurs avec les
   nôtres (`concordance`), type voulu, popularité.

Le réseau passe par `app/bgg_api.py` (seul module réseau), dans sa cadence de
processus. Tout le clic tient dans `BORNE_CLIC_S` : au-delà, on rend ce qu'on a.

RIEN N'EST GARDÉ DU TEXTE CHERCHÉ. Ni ici, ni dans le journal, ni dans une
adresse : la route ne transmet à la page que les IDENTIFIANTS des candidats.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from typing import Callable

from app import bgg_api

# Candidats montrés au bureau, et donc fiches demandées à BGG (un appel
# `thing`). Cinq tiennent sur un écran de téléphone en quelques gestes, et
# couvrent les homonymes vus à la première passe.
NB_CANDIDATS = 5

# Durée maximale d'un clic sur « Chercher sur BGG », réseau et attentes
# comprises : bien en deçà des 60 s de nginx, et de ce qu'un bénévole attend
# devant un écran blanc. Mesuré le 2026-10-14 depuis une connexion
# domestique : un appel `search` de 0,2 à 1,8 s, `thing` de cinq fiches 0,4 s ;
# avec l'écart de 3 s entre deux appels, un clic ordinaire (une variante +
# les fiches) dure 4 à 6 s, le plus long (toutes les variantes) une quinzaine.
BORNE_CLIC_S = 20

# Temps réservé à l'appel `thing` final : on ne lance plus de variante qui
# l'en priverait. Un écart de cadence (3 s) et un appel court (2 s) : sans
# ses fiches, un candidat ne peut pas être montré (la page ne lit que la
# copie en base).
RESERVE_FICHES_S = bgg_api.INTERVALLE_APPELS_S + bgg_api.DUREE_MIN_APPEL_S

TYPE_VOULU = {"Extension": bgg_api.TYPE_EXTENSION}


def norm(texte: str | None) -> str:
    """Un nom comparable : sans accents, en minuscules, lettres et chiffres."""
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in texte.lower()).split())


def variantes(nom: str) -> tuple[list[str], list[str]]:
    """
    Les formes du nom à chercher : (formes nettoyées, formes raccourcies).

    Nettoyées, dans l'ordre, sans doublon, quatre au plus :
    - le nom tel quel ;
    - sans guillemets (« Jeu "Extension" » → « Jeu Extension ») ;
    - l'extension après deux-points, comme BGG nomme les siennes
      (« Jeu "Extension" » → « Jeu: Extension ») ;
    - chiffres décollés des lettres (« 7Wonders » → « 7 Wonders ») ;
    - ponctuation retirée (« Taco Chat Bouc Cheese Pizza! » → sans « ! »).

    Raccourcies, essayées seulement si les premières ne trouvent RIEN : le nom
    sans son dernier mot (trois mots ou plus), sans ses deux derniers (cinq ou
    plus).
    """
    sans_guillemets = " ".join(re.sub(r'["“”«»]', " ", nom).split())
    deux_points = " ".join(re.sub(r'\s*["“«]\s*(.+?)\s*["”»]\s*', r": \1", nom).split())
    # Dans les deux sens : « Splendor2 » → « Splendor 2 », « 7Wonders » →
    # « 7 Wonders » (le brouillon ne décollait que le premier).
    decolle = re.sub(r"(?<=[A-Za-zÀ-ÿ])(?=\d)|(?<=\d)(?=[A-Za-zÀ-ÿ])", " ", sans_guillemets)
    sobre = " ".join(re.sub(r"[!?$.,;]", " ", decolle).split())
    propres: list[str] = []
    for forme in (" ".join(nom.split()), sans_guillemets, deux_points, decolle, sobre):
        if forme and forme not in propres:
            propres.append(forme)
    mots = sobre.split()
    courtes = []
    if len(mots) >= 3:
        courtes.append(" ".join(mots[:-1]))
    if len(mots) >= 5:
        courtes.append(" ".join(mots[:-2]))
    return propres[:4], [c for c in courtes if c not in propres]


def ressemblance(a: str, b: str) -> float:
    """Ressemblance de deux noms normalisés, de 0 à 1."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def concordance(titre: dict, fiche: dict) -> tuple[int, int]:
    """
    (points d'accord, points comparables) entre notre fiche et celle de BGG.

    Joueurs minimum et maximum égaux ; âge à deux ans près (les éditeurs
    arrondissent différemment) ; durée dans un rapport de 0,5 à 2 (BGG donne
    souvent une plage, nous une valeur).
    """
    accord = comparables = 0
    for champ in ("nb_joueurs_min", "nb_joueurs_max"):
        if titre.get(champ) and fiche.get(champ):
            comparables += 1
            accord += titre[champ] == fiche[champ]
    if titre.get("age_min") and fiche.get("age_min"):
        comparables += 1
        accord += abs(titre["age_min"] - fiche["age_min"]) <= 2
    if titre.get("duree_min") and fiche.get("duree_min"):
        comparables += 1
        accord += 0.5 <= titre["duree_min"] / fiche["duree_min"] <= 2
    return accord, comparables


def note(titre: dict, candidat: dict, fiche: dict | None) -> float:
    """
    Le score d'un candidat : plus il est haut, plus le candidat vient tôt.

    Ressemblance (pèse 3), un point par valeur qui concorde, un demi-point
    pour le type voulu (une « Extension » chez nous cherche d'abord une
    extension chez BGG), et la popularité, en logarithme : elle ne départage
    que des candidats par ailleurs proches.
    """
    voulu = TYPE_VOULU.get(titre.get("type_jeu"), bgg_api.TYPE_JEU)
    score = candidat["ressemblance"] * 3
    type_fiche = (fiche or {}).get("type") or candidat.get("type")
    score += 0.5 if type_fiche == voulu else 0
    if fiche:
        score += concordance(titre, fiche)[0]
        score += 0.3 * math.log10((fiche.get("votes") or 0) + 1)
    return score


def chercher(titre: dict, requete: str | None = None, *,
             rechercher: Callable[..., list[dict]] | None = None,
             recuperer: Callable[..., dict] | None = None,
             horloge: Callable[[], float] | None = None,
             ) -> tuple[list[int], dict[int, dict | None], str | None]:
    """
    Les candidats BGG pour `titre`, classés, en un clic borné.

    Args:
        titre: notre titre (ses colonnes de `titres`).
        requete: le nom à chercher, si le bureau en a tapé un autre ; sinon
            le nom du titre.
        rechercher, recuperer, horloge: le réseau et l'heure — par défaut
            `bgg_api.rechercher`, `bgg_api.recuperer` et l'horloge de
            `bgg_api.CADENCE` ; les tests les remplacent.

    Returns:
        (identifiants classés, fiches lues pour eux — à garder en copie —,
        code d'échec ou None). Après un échec, ce qui a été trouvé avant est
        rendu, et seulement ce qui a ses fiches : la page ne montre qu'un
        candidat dont la copie est en base. Un refus de BGG pendant les
        variantes (429, panne…) arrête tout : rien ne lui est redemandé.
    """
    rechercher = rechercher or bgg_api.rechercher
    recuperer = recuperer or bgg_api.recuperer
    horloge = horloge or bgg_api.CADENCE.horloge
    fin = horloge() + BORNE_CLIC_S

    nom = bgg_api.nettoyer_requete(requete) or bgg_api.nettoyer_requete(titre.get("nom"))
    if not nom:
        return [], {}, None
    cible = norm(nom)
    propres, courtes = variantes(nom)
    vus: dict[int, dict] = {}
    echec: str | None = None

    def absorber(forme: str) -> bool:
        """Cherche `forme` ; True si un résultat porte exactement le nom."""
        exact = False
        for resultat in rechercher(forme, fin=fin - RESERVE_FICHES_S):
            nom_bgg = norm(resultat["nom"])
            r = ressemblance(cible, nom_bgg)
            # Un nom BGG égal à une forme nettoyée vaut une égalité.
            if nom_bgg in (cible, norm(forme)) and forme in propres:
                r = 1.0
            ancien = vus.get(resultat["bgg_id"])
            if ancien is None or r > ancien["ressemblance"]:
                vus[resultat["bgg_id"]] = {**resultat, "ressemblance": r}
            exact = exact or r == 1.0
        return exact

    try:
        for forme in propres:
            if absorber(forme):
                break
        else:
            if not vus:
                for forme in courtes:
                    absorber(forme)
                    if vus:
                        break
    except bgg_api.EchecBGG as erreur:
        echec = erreur.code

    voulu = TYPE_VOULU.get(titre.get("type_jeu"), bgg_api.TYPE_JEU)
    retenus = sorted(vus.values(), key=lambda c: (-c["ressemblance"], c["type"] != voulu))
    retenus = retenus[:NB_CANDIDATS]
    if not retenus:
        return [], {}, echec
    # Après un refus de BGG (429, panne…), on ne lui redemande rien.
    if echec in (None, "trop_long"):
        try:
            fiches = recuperer([c["bgg_id"] for c in retenus], popularite=True, fin=fin)
        except bgg_api.EchecBGG as erreur:
            return [], {}, erreur.code
    else:
        return [], {}, echec
    retenus = [c for c in retenus if fiches.get(c["bgg_id"])]
    retenus.sort(key=lambda c: -note(titre, c, fiches[c["bgg_id"]]))
    # Le temps a manqué pour essayer toutes les variantes, mais ce qui a été
    # trouvé est montré : on rend ce qu'on a, sans alarmer.
    if retenus and echec == "trop_long":
        echec = None
    return [c["bgg_id"] for c in retenus], fiches, echec
