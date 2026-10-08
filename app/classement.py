"""
Code de classement imprimé sur l'étiquette — DOMICILE UNIQUE.

Le cadre du bas de l'étiquette porte, à côté du code de la boîte, un code qui
résume le jeu : trois lettres de classement, puis l'âge, le nombre de joueurs
et la durée. Ce module porte tout ce qui le compose — les listes de lettres,
leur validation, la lecture d'une saisie CSV et les deux formats d'affichage.
`app/etiquettes.py` l'appelle pour dessiner, `scripts/import_csv.py` pour lire
la colonne « Lettres classement », `app/services.py` pour l'export.

LES TROIS LETTRES, PAR POSITION — la position porte le sens :

    1. le public, sur le modèle des catégories de l'As d'Or ;
    2. la façon de jouer (ambiance, mots, bluff, placement…) ;
    3. le matériel dominant (cartes, dés, tuiles…).

POURQUOI LES LISTES VIVENT DANS LE CODE, ET NON DANS UN ÉCRAN D'ADMINISTRATION.
Une lettre imprimée est collée sur une boîte pour des années. Changer le sens
d'une lettre après l'impression rendrait fausses, sans que rien ne le signale,
des étiquettes déjà collées. Une modification de liste doit donc passer par un
commit, une version et un CHANGELOG — pas par un formulaire.

JAMAIS DE LETTRES PARTIELLES. Les lettres s'impriment si et seulement si les
trois sont renseignées ET chacune appartient à sa liste. Sinon, aucune : `TC`
ne dirait pas si `C` est la façon de jouer ou le matériel.

Le code de classement n'est PAS un identifiant : il ne sert ni à chercher, ni
à construire une URL, ni à saisir quoi que ce soit. Les deux clés stables
restent `id_exemplaire` et `reference_titre`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Les listes de lettres : lettre majuscule -> libellé
# ---------------------------------------------------------------------------
# Position 1 — le public. Décidé : les quatre catégories de l'As d'Or.
LETTRES_PUBLIC: dict[str, str] = {
    "E": "Enfant",
    "T": "Tout public",
    "I": "Initié",
    "X": "Expert",
}

# Position 2 — la façon de jouer. VIDE À DESSEIN : la nomenclature n'est pas
# arrêtée, et les lettres ne seront remplies que par la classification via
# BoardGameGeek (série « classement », lots 2 et 3). Tant qu'elle est vide,
# AUCUN triplet n'est imprimable — c'est voulu, et un test le dit.
LETTRES_JEU: dict[str, str] = {}

# Position 3 — le matériel dominant. Vide pour la même raison que ci-dessus.
LETTRES_MATERIEL: dict[str, str] = {}

# Colonnes de `titres` qui portent les trois lettres, dans l'ordre des
# positions. Une colonne par axe, et non une chaîne : chaque axe se valide à
# part, et la classification automatique pourra en proposer un sans toucher
# aux deux autres.
COLONNES_LETTRES = ("lettre_public", "lettre_jeu", "lettre_materiel")


def _listes() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """
    Les trois listes dans l'ordre des positions, lues À CHAQUE APPEL.

    Une fonction et non un tuple de module : un tuple figerait les
    dictionnaires du premier import, et les tests qui remplissent les listes
    le temps d'un test (`monkeypatch.setattr`) ne seraient pas vus.
    """
    return LETTRES_PUBLIC, LETTRES_JEU, LETTRES_MATERIEL


def triplet_imprimable(public, jeu, materiel) -> bool:
    """
    True si les trois lettres sont présentes et chacune dans SA liste.

    Aucune tolérance ici (ni minuscule, ni espace) : ce qui est en base a été
    normalisé à l'entrée par `lire_saisie`. Une valeur inattendue rend
    simplement le triplet non imprimable — jamais une erreur.
    """
    return all(
        isinstance(lettre, str) and lettre in liste
        for lettre, liste in zip((public, jeu, materiel), _listes())
    )


def lettres_imprimables(ex: dict) -> str:
    """
    Les trois lettres à imprimer pour `ex` (`"TPC"`), ou `""` s'il n'y en a pas.

    `ex` est un dict portant les colonnes `COLONNES_LETTRES` ; une clé absente
    vaut une lettre absente — un producteur dont la requête oublierait ces
    colonnes imprimerait donc sans lettres, d'où le test qui fait passer un
    triplet par les trois producteurs.
    """
    triplet = tuple(ex.get(c) for c in COLONNES_LETTRES)
    return "".join(triplet) if triplet_imprimable(*triplet) else ""


def lire_saisie(texte: str | None) -> tuple[str, str, str] | None:
    """
    Découpe une saisie CSV (`" tpc "`) en triplet (`("T", "P", "C")`).

    Tolère les espaces autour et la casse. Refuse — en renvoyant None — tout ce
    qui n'est pas EXACTEMENT trois lettres valides, chacune dans sa liste. Le
    cas « case vide » se distingue AVANT d'appeler cette fonction : une case
    vide n'est pas une erreur, elle ne touche à rien.
    """
    valeur = (texte or "").strip().upper()
    if len(valeur) != 3:
        return None
    triplet = (valeur[0], valeur[1], valeur[2])
    return triplet if triplet_imprimable(*triplet) else None


# ---------------------------------------------------------------------------
# Les deux formats d'affichage
# ---------------------------------------------------------------------------
# Choisis par le réglage `etiquette_format` (`app/services.py`), que ce module
# ne lit pas : comme `app/etiquettes.py`, il reçoit la valeur de ses appelants.
FORMAT_DENSE = "dense"
FORMAT_LISIBLE = "lisible"
FORMATS = (FORMAT_DENSE, FORMAT_LISIBLE)
# Défaut, y compris sur une base qui n'a jamais vu le réglage : le format que
# le bureau préfère, et aucune étiquette n'était encore imprimée quand il a
# été choisi — rien de collé ne diverge.
FORMAT_DEFAUT = FORMAT_LISIBLE

# Séparateur du format lisible : point médian (U+00B7) entouré d'espaces.
SEPARATEUR_LISIBLE = " · "


# Le jeu fictif qui illustre chaque format sur l'écran de réglage : l'exemple
# affiché est RENDU par `code_classement`, jamais recopié dans le gabarit — il
# ne peut donc pas mentir sur ce que l'étiquette imprimera.
EXEMPLE_FORMAT = {"age_min": 8, "nb_joueurs_min": 2, "nb_joueurs_max": 4,
                  "duree_min": 30}


def format_connu(valeur: str | None) -> str:
    """`valeur` si c'est un format connu, sinon le défaut. Jamais d'erreur."""
    return valeur if valeur in FORMATS else FORMAT_DEFAUT


def _dense(ex: dict, lettres: str) -> str:
    """`TPC8-2-4-30` — inchangé depuis l'origine, `?` pour une valeur absente."""
    def v(x):
        return str(x) if x not in (None, "") else "?"

    return (f"{lettres}{v(ex.get('age_min'))}-{v(ex.get('nb_joueurs_min'))}"
            f"-{v(ex.get('nb_joueurs_max'))}-{v(ex.get('duree_min'))}")


def _connu(x) -> bool:
    """
    Une valeur à imprimer en format lisible.

    ZÉRO VAUT ABSENT, en lisible seulement : « 0j » ou « 0min » ne disent rien
    de vrai, et BoardGameGeek — la future source des données — note 0 pour
    « non renseigné ». L'import tel qu'il est peut produire un 0 (une case
    « 0 » se lit comme 0), l'écran de création d'un jeu aussi (champ âge à
    minimum 0). Le format dense, lui, reste inchangé et imprime `0`.
    """
    return x not in (None, "", 0)


def _lisible(ex: dict, lettres: str) -> str:
    """
    `TPC · 8+ · 2-4j · 30min` — une valeur absente DISPARAÎT, au lieu d'un `?`.

    Unités collées au nombre (`2-4j`, `30min`) : densification voulue, elle
    fait gagner la place qui manquait au pire cas réel.
    """
    age, jmin, jmax, duree = (ex.get(c) for c in
                              ("age_min", "nb_joueurs_min", "nb_joueurs_max",
                               "duree_min"))
    morceaux = [lettres] if lettres else []
    if _connu(age):
        morceaux.append(f"{age}+")
    if _connu(jmin) and _connu(jmax) and jmin != jmax:
        morceaux.append(f"{jmin}-{jmax}j")
    elif _connu(jmin) or _connu(jmax):
        # min = max, ou une seule borne connue : un seul nombre.
        morceaux.append(f"{jmin if _connu(jmin) else jmax}j")
    if _connu(duree):
        morceaux.append(f"{duree}min")
    return SEPARATEUR_LISIBLE.join(morceaux)


def code_classement(ex: dict, format_code: str | None = FORMAT_DEFAUT) -> str:
    """
    Le code de classement de `ex` dans le format demandé.

    Args:
        ex: dict portant `age_min`, `nb_joueurs_min`, `nb_joueurs_max`,
            `duree_min` et, s'il y en a, les colonnes `COLONNES_LETTRES`.
        format_code: `FORMAT_DENSE` ou `FORMAT_LISIBLE` ; toute autre valeur
            vaut le défaut.

    Returns:
        La chaîne à imprimer — vide en format lisible quand rien n'est connu.
    """
    lettres = lettres_imprimables(ex)
    if format_connu(format_code) == FORMAT_DENSE:
        return _dense(ex, lettres)
    return _lisible(ex, lettres)
