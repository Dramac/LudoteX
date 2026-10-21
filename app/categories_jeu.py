"""
Catégories de jeu — découpage d'une case et clé de rapprochement. DOMICILE UNIQUE.

La case « Type jeu » du tableur (colonne `titres.categorie`) porte une ou
plusieurs catégories séparées par une virgule : « Cartes, Ambiance ». Depuis la
série « categories » (lot 1), la base en tient la LISTE : une table de
vocabulaire `categories_jeu` et une liaison `titres_categories` (voir
`app/models.py`). Ce module porte les deux règles dont tout le reste dépend :

- `decouper(case)` : la liste ordonnée des catégories d'une case ;
- `cle(nom)` : la forme sous laquelle deux graphies sont « la même catégorie »
  (« bluff » rejoint « Bluff », « strategie » rejoint « Stratégie »).

Il est PUR : ni base ni réseau, comme `app/classement.py`. Les écritures
vivent dans `app/services.py` (`ecrire_categories_titre`, seul chemin
d'écriture de la liaison et de la copie `titres.categorie`), l'import et la
migration lui passent ce qu'ils ont lu.

POURQUOI LE RAPPROCHEMENT SE FAIT ICI ET PAS EN SQL. `COLLATE NOCASE` de
SQLite ne replie que l'ASCII : « É » et « é » y restent différents, et les
accents ne sont jamais ignorés. La clé est donc calculée en Python et STOCKÉE
(`categories_jeu.cle`, UNIQUE) : la base compare des clés, jamais des
graphies.
"""

from __future__ import annotations

import unicodedata
from collections import Counter

# Le séparateur de la copie canonique, celle qu'écrit l'export : « Cartes,
# Ambiance ». À la lecture, seule la virgule compte ; les espaces autour sont
# indifférents (« Cartes,Coopération » se lit comme « Cartes, Coopération »).
SEPARATEUR = ", "

# Longueur maximale d'UNE catégorie, après nettoyage des espaces. La plus
# longue du catalogue réel fait 17 caractères (« Question-Réponses », mesuré
# au lot categories-1), la plus longue case entière 25 : 60 laissent plus du
# triple, et arrêtent un paragraphe collé par erreur dans la case. Au-delà, le
# segment est TRONQUÉ, jamais refusé : la ligne entre toujours, et le nom coupé
# apparaît dans le compte rendu d'import comme une nouvelle catégorie, ce qui
# le rend visible au bureau. La troncature est déterministe : le ré-import du
# fichier exporté retombe sur la même catégorie. Dans l'éditeur, le champ
# « Ajouter une catégorie » garde sa propre borne (`services.LONGUEUR_TEXTE_FICHE`).
LONGUEUR_CATEGORIE = 60


def nettoyer(segment: str | None) -> str:
    """
    La graphie à stocker d'une catégorie : NFC, espaces internes réduits à un,
    bords retirés, coupée à `LONGUEUR_CATEGORIE`. Chaîne vide s'il ne reste
    rien.

    NFC parce qu'un fichier d'Excel pour Mac peut arriver décomposé (le « é »
    en « e » + accent) : deux graphies identiques à l'œil doivent l'être en
    base.
    """
    texte = " ".join(unicodedata.normalize("NFC", segment or "").split())
    return texte[:LONGUEUR_CATEGORIE].rstrip()


def cle(nom: str | None) -> str:
    """
    La clé de rapprochement d'une catégorie : deux graphies de même clé sont
    la même catégorie.

    Dans l'ordre : `casefold` (minuscules, y compris hors ASCII), décomposition
    NFD et retrait des marques combinantes (les accents), espaces internes
    réduits à un et bords retirés. « Bluff », « bluff » et « BLUFF » ont la
    même clé ; « Stratégie », « strategie » et « STRATÉGIE » aussi.

    Ce qu'elle NE replie PAS, à dessein :
    - la ponctuation : « Question-Réponses » et « Question Réponses » restent
      deux catégories (un tiret peut porter du sens, et le replier fusionnerait
      sans prévenir) ;
    - les ligatures : « Œuvre » et « Oeuvre » restent deux catégories, NFD ne
      décompose pas « œ ». Noté, pas corrigé : aucun cas réel.

    Chaîne vide pour un nom vide ou fait d'espaces : ce n'est pas une
    catégorie.
    """
    texte = unicodedata.normalize("NFD", (nom or "").casefold())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(texte.split())


def decouper(case: str | None) -> list[str]:
    """
    Les catégories d'une case du tableur, dans l'ordre de la case.

    Découpe à la virgule, nettoie chaque segment (`nettoyer`), ignore les
    segments vides (« Cartes,, Ambiance, » donne deux catégories) et ne garde
    qu'une fois une catégorie répétée, à la place de sa première occurrence
    et sous sa première graphie (« Cartes, cartes, CARTES » donne
    [« Cartes »]). None, « » et « , » ne donnent aucune catégorie.

    Jamais d'erreur : une case, si mauvaise soit-elle, donne une liste.
    """
    noms: list[str] = []
    vues: set[str] = set()
    for segment in (case or "").split(","):
        nom = nettoyer(segment)
        k = cle(nom)
        if k and k not in vues:
            vues.add(k)
            noms.append(nom)
    return noms


def joindre(noms: list[str]) -> str | None:
    """La copie canonique (« Cartes, Ambiance »), ou None sans catégorie."""
    return SEPARATEUR.join(noms) or None


def graphie_majoritaire(graphies: Counter) -> str:
    """
    La graphie retenue À LA MIGRATION pour une catégorie écrite de plusieurs
    façons sur le parc : la plus fréquente. À égalité, une initiale majuscule
    l'emporte (« Imagination » sur « imagination »), puis l'ordre
    alphabétique des chaînes. Déterministe : deux migrations de la même base
    retiennent la même graphie.
    """
    return min(graphies, key=lambda g: (-graphies[g], not g[:1].isupper(), g))


def vocabulaire_de_migration(cases: list[str | None],
                             connues: dict[str, str]) -> dict[str, str]:
    """
    Les catégories que la migration crée pour rattacher `cases` (les copies
    des titres encore sans liaison), clé -> graphie : celles dont la clé
    n'est pas déjà dans `connues` (clé -> graphie en base), sous leur graphie
    majoritaire (`graphie_majoritaire`). Une catégorie déjà connue garde sa
    graphie en base.

    Une occurrence par case : « Cartes, cartes » compte une fois.
    """
    comptes: dict[str, Counter] = {}
    for case in cases:
        for nom in decouper(case):
            k = cle(nom)
            if k not in connues:
                comptes.setdefault(k, Counter())[nom] += 1
    return {k: graphie_majoritaire(g) for k, g in comptes.items()}


class Vocabulaire:
    """
    Le vocabulaire des catégories au fil d'un import ou d'une saisie.

    Part des catégories connues (clé -> graphie en base) et retient, dans
    l'ordre où elles apparaissent, celles qu'on lui fait découvrir. Règle de
    graphie : une catégorie DÉJÀ CONNUE garde la sienne (« bluff » dans le
    fichier rejoint « Bluff » sans le renommer) ; une NOUVELLE prend celle de
    sa première occurrence, et deux graphies nouvelles de même clé comptent
    pour une seule.

    Pur : l'import s'en sert en simulation comme en réel, et la simulation
    annonce donc exactement les catégories que l'import réel créera.
    """

    def __init__(self, connues: dict[str, str] | None = None):
        self._graphies: dict[str, str] = dict(connues or {})
        self.nouvelles: list[str] = []

    def retenir(self, case: str | None) -> list[str]:
        """Les catégories de `case` sous leur graphie retenue, dans l'ordre."""
        noms = []
        for nom in decouper(case):
            k = cle(nom)
            if k not in self._graphies:
                self._graphies[k] = nom
                self.nouvelles.append(nom)
            noms.append(self._graphies[k])
        return noms


def canoniser(case: str | None, connues: dict[str, str]) -> str | None:
    """
    La copie canonique de `case` face au vocabulaire `connues`, sans rien
    retenir : la forme sous laquelle comparer une saisie à ce que porte la
    base (« cartes, ambiance » face à « Cartes, Ambiance » : la même).
    """
    return joindre(Vocabulaire(connues).retenir(case))


# ---------------------------------------------------------------------------
# Les écrans (lot categories-2) : le filtre du catalogue, les cases à cocher,
# la composition de la liste d'un titre depuis l'éditeur.
# ---------------------------------------------------------------------------

# Nombre maximal de catégories qu'un filtre prend en compte. Chacune devient
# une condition `EXISTS` de la requête du catalogue : une adresse fabriquée
# avec mille valeurs ne doit pas en fabriquer mille. Le filtre est en ET, et
# aucun titre du catalogue réel ne porte plus de deux catégories (mesuré au lot
# categories-2, 51 catégories en tout) : au-delà de quelques cases, le
# résultat est vide de toute façon. 10 laisse une large marge à un parc plus
# finement classé. Les valeurs suivantes sont ignorées, sans erreur.
FILTRE_CATEGORIES_MAX = 10


def cles_du_filtre(valeurs: list[str] | None, connues) -> list[str]:
    """
    Les clés de catégorie d'un filtre reçu dans l'adresse, prêtes pour la
    requête : chaque valeur est DÉCOUPÉE puis mise en clé, les clés inconnues
    (absentes de `connues`, un ensemble ou un dict de clés) sont ignorées, les
    doublons retirés, l'ordre d'arrivée gardé, au plus
    `FILTRE_CATEGORIES_MAX`.

    Le découpage donne la compatibilité des anciennes adresses : la fiche
    publique et les signets d'avant le lot categories-2 portaient la case
    entière (« ?categorie=Cartes, Ambiance »), qui se lit maintenant comme
    `cartes` ET `ambiance` ; « Stratégie » ou « STRATÉGIE » donnent
    `strategie`. Jamais d'erreur : une lecture publique n'a rien à refuser.
    """
    cles: list[str] = []
    for valeur in valeurs or ():
        for nom in decouper(valeur):
            k = cle(nom)
            if k in connues and k not in cles:
                cles.append(k)
                if len(cles) == FILTRE_CATEGORIES_MAX:
                    return cles
    return cles


def cases_a_cocher(connues: dict[str, str], cochees: list[str]) -> list[dict]:
    """
    Les cases d'un bloc de catégories : une par catégorie connue (clé ->
    graphie), plus une par catégorie cochée INCONNUE — une saisie réaffichée
    après un refus garde ainsi une catégorie purgée entre-temps (par un
    import), cochée, au lieu de la perdre sans bruit.

    `cochees` : des noms ou des clés, comparés par leur clé.

    Ordre alphabétique sur la CLÉ, puis sur la graphie : trié sur la graphie,
    « Énigmes » partirait après « Stratégie » (le « É » vient après toutes les
    lettres sans accent).

    Returns:
        Liste de dicts {cle, nom, coche}.
    """
    graphies = dict(connues)
    marquees: set[str] = set()
    for nom in cochees or ():
        k = cle(nom)
        if k:
            marquees.add(k)
            graphies.setdefault(k, nettoyer(nom))
    return sorted(({"cle": k, "nom": n, "coche": k in marquees}
                   for k, n in graphies.items()),
                  key=lambda c: (c["cle"], c["nom"]))


def composer(actuelles: list[str], cochees: list[str],
             ajout: str | None) -> str | None:
    """
    La case à écrire pour un titre depuis l'éditeur de fiche : ce qui était
    porté et reste coché, DANS L'ORDRE ACTUEL ; puis ce qui vient d'être coché,
    dans l'ordre de la page ; puis le champ « Ajouter une catégorie », découpé
    à la virgule.

    POURQUOI CET ORDRE. La liste d'un titre est ordonnée (celle du tableur),
    les cases arrivent dans l'ordre de la page (alphabétique). Les recomposer
    naïvement réordonnerait « Cartes, Ambiance » en « Ambiance, Cartes » au
    premier « Enregistrer » sans rien changer : une écriture, `modifie_le`
    posé, et un faux « correction défaite » au ré-import suivant.

    Chaque valeur cochée est elle-même découpée : une ancienne saisie
    « Cartes, Ambiance » arrivée en une seule valeur vaut deux cases.
    Comparaison par la clé ; les doublons tombent au découpage final.

    Args:
        actuelles: les catégories portées, dans leur ordre (`decouper` de la
            copie, ou la liaison).
        cochees: les valeurs des cases cochées (des noms), ordre de la page.
        ajout: le champ libre, brut.
    """
    noms_coches = [n for valeur in cochees or () for n in decouper(valeur)]
    cles_cochees = {cle(n) for n in noms_coches}
    gardees = [n for n in actuelles if cle(n) in cles_cochees]
    cles_gardees = {cle(n) for n in gardees}
    nouvelles = [n for n in noms_coches if cle(n) not in cles_gardees]
    return joindre(decouper(SEPARATEUR.join([*gardees, *nouvelles, *decouper(ajout)])))
