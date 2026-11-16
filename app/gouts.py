"""
Ce qui plaît (lot bilan-6) : le profil des jeux que le public emprunte, par
catégorie, par nombre de joueurs, par âge conseillé, et la durée annoncée
sur la boîte comparée à la durée réelle du prêt.

À quoi ça sert : orienter le catalogue (quels genres acheter) et la mise en
avant des jeux (quoi poser sur la table d'accueil). La page de `/stats`
montre les premiers groupes ; l'export Excel porte les répartitions en entier
(feuille « Ce qui plaît ») et la liste des jeux sans catégorie (feuille
« Sans catégorie »), qui est la liste de travail pour compléter le catalogue.

Module à part, sur le patron d'`app/achats.py`. Il ne lit que la base de
prêt, ne journalise rien et ne porte aucune donnée personnelle : des groupes
de jeux et des comptes.

LA MESURE : LES PRÊTS PAR JEU. Un compte brut de prêts par groupe ne mesure
que la taille du rayon : sur la base réelle de 2026, « Cartes » avait 189
prêts pour 187 jeux (1 par jeu) et « Réflexe » 24 prêts pour 6 jeux (4 par
jeu). On divise donc les prêts du groupe par son nombre de jeux, et c'est ce
chiffre qui classe les catégories.

- **Jeux d'un groupe** : les titres du catalogue qui ont au moins une boîte
  (un titre sans boîte ne peut pas être prêté), prêtés ou non.
- **Prêts** : les prêts au public sortis dans la période
  (`services.MOTIFS_COMPTES` : `pret` et `oubli`), comme le total des prêts.
  Ni les sorties tournoi ni les erreurs de prêt.
- **Un jeu à plusieurs catégories compte dans chacune** : la somme des prêts
  des catégories dépasse le total. Dit sous le tableau.
- **Petits groupes** : en dessous de `SEUIL_JEUX` jeux, un groupe donne un
  chiffre au hasard (2 jeux et 9 prêts font 4,5 par jeu). Une petite
  catégorie n'est pas classée : elle rejoint « Autres catégories » sur la
  page, et garde sa ligne dans l'Excel. Une tranche de joueurs ou d'âge, qui
  a sa place fixe, garde sa ligne, mais sans barre.
- **« Non renseigné »** n'est pas un groupe comme les autres : jamais classé,
  toujours montré quand il n'est pas vide, avec la part des prêts qui
  échappent ainsi au tableau.
- **Le catalogue d'aujourd'hui** : la base ne garde l'historique ni des
  catégories ni du parc. Dit une fois pour la section.
- La liste des catégories d'un jeu vient de `titres_categories` et
  `categories_jeu`, jamais de la copie `titres.categorie`.

LES TRANCHES.

- **Nombre de joueurs** : une fourchette (2 à 6) n'entre pas dans une seule
  case. Les groupes se CHEVAUCHENT, et un jeu compte dans chacun de ceux où
  il entre (`GROUPES_JOUEURS`). « Se joue à N » est la règle du filtre du
  catalogue (`min ≤ N ≤ max`). Les groupes « seul », « à 2 », « à 3 ou 4 »
  et « à 5 et plus » couvrent toutes les fourchettes : aucun jeu renseigné
  n'est perdu (un 3-4 n'entrait dans aucun des groupes proposés au lot, d'où
  « à 3 ou 4 »).
  Cas limites (`fourchette_joueurs`), jamais d'erreur : une borne absente
  prend la valeur de l'autre (un jeu « 4 » se joue à 4) ; des bornes
  inversées sont remises dans l'ordre ; une borne inférieure à 1, ou qui
  n'est pas un nombre, est ignorée ; sans aucune borne lisible, le jeu va
  dans « Non renseigné ». Une valeur haute (99 joueurs) est gardée : elle
  range le jeu dans « 8 et plus », ce qui est vrai.
- **Âge conseillé** : des tranches qui ne se chevauchent pas, bornes dans
  `BORNES_AGE`. Un âge de 0 (« dès la naissance ») va dans la première
  tranche, comme au filtre du catalogue (`age_min <= âge`) ; un âge négatif
  ou illisible dans « Non renseigné ».
- **Durée annoncée** : tranches de `BORNES_DUREE`. Une durée de 0 ou moins,
  ou illisible, va dans « Non renseigné ».

DURÉE ANNONCÉE ET DURÉE RÉELLE. Des MÉDIANES, pas des moyennes : quelques
prêts de plusieurs heures tireraient une moyenne vers le haut (constat sur
2026 : médiane d'environ 38 min, 16 prêts au-delà de 4 h).

- **Les prêts mesurés** : les prêts `pret` RENDUS, comme la durée moyenne de
  la synthèse. Un retour non scanné (`oubli`) a une durée inconnue ; un prêt
  en cours n'en a pas encore.
- **Les prêts qui passent la nuit sont écartés** : rendus après la fin de la
  journée de leur sortie (6 h le lendemain, `achats.fin_de_journee`, la règle
  de bilan-5). Un jeu rendu le lendemain matin n'a pas été joué dix heures ;
  le garder, c'est mesurer la nuit. Leur nombre est dit sous le tableau.
- **Une fermeture par la clôture n'est pas un retour** (règle du comptoir,
  `comptoir.instants_de_cloture`, reconnue par lui et passée par
  l'appelant) : sa date est celle de la clôture, pas celle du retour. Le
  prêt n'est pas mesuré ; leur nombre est dit.
- **Une date illisible**, ou un retour avant la sortie, écarte le prêt de la
  mesure (`comptoir.lire_instant`, même règle que le comptoir).
- La médiane annoncée se calcule sur les MÊMES prêts que la médiane réelle :
  les deux colonnes parlent de la même population.
- Un groupe sans prêt mesuré n'a pas de médiane : « — ».

PERFORMANCE : une lecture des prêts de la période, une des titres, une des
catégories ; puis des comptes en Python. Jamais une requête par groupe.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from statistics import median

from app.achats import fin_de_journee
from app.categories_jeu import cle as cle_pliee
from app.comptoir import lire_instant
from app.services import (MOTIF_OUBLI, MOTIFS_COMPTES, _filtre_periode,
                          format_duree, pluriel)

# Un groupe de moins de SEUIL_JEUX jeux n'est pas classé (voir l'en-tête).
# Sur 2026, une catégorie de 6 jeux (« Réflexe ») est le plus petit groupe
# qui disait quelque chose ; à 5, deux jeux très prêtés ne suffisent plus à
# faire un champion.
SEUIL_JEUX = 5

# Catégories classées montrées sur la page ; l'Excel les donne toutes.
LIGNES_PAGE = 10

# Âge conseillé : bornes BASSES des tranches, après « moins de 6 ans ». Les
# valeurs les plus fréquentes en 2026 (8, 10, 7 ans) tombent dans des
# tranches distinctes.
BORNES_AGE = (6, 8, 10, 12, 14)

# Durée annoncée, en minutes : bornes HAUTES incluses des tranches, avant
# « plus de 60 min ».
BORNES_DUREE = (15, 30, 60)

# Groupes de joueurs : (clé, libellé, condition sur la fourchette lue par
# `fourchette_joueurs`). Ils se chevauchent ; l'ordre est celui de la page.
GROUPES_JOUEURS = (
    ("seul", "Se joue seul", lambda mn, mx: mn <= 1),
    ("deux", "Se joue à 2", lambda mn, mx: mn <= 2 <= mx),
    ("deux_seulement", "Se joue à 2 seulement", lambda mn, mx: mn == mx == 2),
    ("trois_quatre", "Se joue à 3 ou 4", lambda mn, mx: mn <= 4 and mx >= 3),
    ("cinq", "Se joue à 5 et plus", lambda mn, mx: mx >= 5),
    ("huit", "Grands groupes, 8 et plus", lambda mn, mx: mx >= 8),
)

# --- Libellés, mot pour mot sur la page et dans l'export -------------------
TITRE_SECTION = "Ce qui plaît"
PHRASE_SENS = ("Les genres de jeux que le public emprunte le plus, pour "
               "orienter les achats et choisir les jeux à mettre en avant.")
NOTE_CATALOGUE = ("Catégories, nombre de joueurs, âge et durée sont ceux du "
                  "catalogue d'aujourd'hui : la base n'en garde pas "
                  "l'historique.")
NOTE_PRETS_PAR_JEU = (
    "Prêts par jeu : les prêts au public de la période, divisés par le nombre "
    "de jeux du groupe, prêtés ou non. Un grand rayon a beaucoup de prêts sans "
    "que chacun de ses jeux plaise davantage : c'est ce chiffre qui compare. "
    "Les sorties pour un tournoi et les erreurs de prêt ne sont pas comptées."
)
NON_RENSEIGNE = "Non renseigné"
AUTRES_CATEGORIES = "Autres catégories"

TITRE_CATEGORIES = "Par catégorie"
TITRE_JOUEURS = "Par nombre de joueurs"
TITRE_AGE = "Par âge conseillé"
TITRE_DUREES = "Durée sur la boîte et durée du prêt"
ENTETES_CATEGORIES = ("Catégorie", "Jeux", "Prêts", "Prêts par jeu")
ENTETES_JOUEURS = ("Nombre de joueurs", "Jeux", "Prêts", "Prêts par jeu")
ENTETES_AGE = ("Âge conseillé", "Jeux", "Prêts", "Prêts par jeu")
ENTETES_DUREES = ("Durée annoncée", "Prêts mesurés", "Sur la boîte",
                  "Durée du prêt")
TOUTES_DUREES = "Toutes durées"

NOTE_PLUSIEURS_CATEGORIES = (
    "Un jeu qui a plusieurs catégories compte dans chacune : la somme des "
    "prêts des catégories dépasse donc le total des prêts.")
NOTE_JOUEURS = (
    "Les groupes se chevauchent : un jeu de 2 à 6 joueurs compte dans « Se "
    "joue à 2 », « Se joue à 3 ou 4 » et « Se joue à 5 et plus ». Un nombre "
    "seul vaut pour les deux bornes.")
NOTE_DUREES = (
    "Médianes : la moitié des prêts a duré moins, l'autre moitié plus ; "
    "quelques prêts de plusieurs heures ne la déplacent pas. La durée du prêt "
    "comprend l'explication des règles, et les parties rejouées. Seuls les "
    "prêts rendus le jour même sont mesurés : un jeu rendu le lendemain n'a "
    "pas été joué toute la nuit.")
PHRASE_AUCUN_PRET = "Aucun prêt au public sur la période."
PHRASE_CATALOGUE_VIDE = "Le catalogue ne compte aucun jeu."
PHRASE_AUCUNE_CATEGORIE = "Aucun jeu du catalogue n'a de catégorie."
PHRASE_AUCUNE_MESURE = "Aucun prêt rendu le jour même sur la période : rien à mesurer."

FEUILLE_GOUTS = "Ce qui plaît"
FEUILLE_SANS_CATEGORIE = "Sans catégorie"
ENTETES_XLSX_REPARTITION = ("Jeux", "Prêts", "Prêts par jeu",
                            f"Moins de {SEUIL_JEUX} jeux")
ENTETES_XLSX_DUREES = ("Durée annoncée", "Prêts", "Prêts mesurés",
                       "Sur la boîte (médiane)", "Sur la boîte (minutes)",
                       "Durée du prêt (médiane)", "Durée du prêt (minutes)")
ENTETES_XLSX_SANS_CATEGORIE = ("Jeu", "Référence", "Boîtes", "Prêts")
NOTE_SANS_CATEGORIE = (
    "Les jeux du catalogue qui n'ont aucune catégorie, les plus prêtés "
    "d'abord. Pour les compléter : Administration, Rechercher un jeu, ouvrir "
    "le jeu, cocher ses catégories dans Modifier la fiche, puis "
    "Enregistrer la fiche.")


def _virgule(x: float) -> str:
    """1.25 -> « 1,3 » : une décimale, virgule française."""
    return f"{x:.1f}".replace(".", ",")


def _pourcent(part: int, total: int) -> str:
    """« 18 % » ; « moins de 1 % » plutôt qu'un « 0 % » qui dirait « rien »."""
    if total <= 0:
        return "0 %"
    p = round(100 * part / total)
    if p == 0 and part > 0:
        return "moins de 1 %"
    return f"{p} %"


def phrase_non_renseigne(nb_prets: int, total: int) -> str:
    """La phrase à côté de « Non renseigné » des catégories."""
    return (f"{nb_prets} {pluriel(nb_prets, 'prêt', 'prêts')} sur {total}, "
            f"soit {_pourcent(nb_prets, total)}, "
            f"{pluriel(nb_prets, 'porte', 'portent')} sur des jeux sans "
            "catégorie et échappent à ce tableau. Pour les compléter : "
            "Administration, Rechercher un jeu, puis la fiche du jeu. La "
            f"liste est dans l'export Excel, feuille « {FEUILLE_SANS_CATEGORIE} ».")


def phrase_autres(nb_suite: int, nb_petites: int) -> str:
    """Ce que réunit « Autres catégories »."""
    morceaux = []
    if nb_suite:
        morceaux.append("la catégorie suivante" if nb_suite == 1
                        else f"les {nb_suite} catégories suivantes")
    if nb_petites:
        if nb_petites == 1:
            debut = "la catégorie"
            fin = "trop petite pour être classée"
        else:
            debut = f"les {nb_petites} catégories"
            fin = "trop petites pour être classées"
        morceaux.append(f"{debut} de moins de {SEUIL_JEUX} jeux, {fin}")
    return (f"« {AUTRES_CATEGORIES} » réunit {' et '.join(morceaux)}. Toutes "
            f"sont dans l'export Excel, feuille « {FEUILLE_GOUTS} ».")


def phrase_petites_tranches() -> str:
    return (f"Une tranche de moins de {SEUIL_JEUX} jeux n'a pas de barre : "
            "trop peu de jeux pour comparer.")


def phrase_non_mesures(oublis: int, nuit: int, en_cours: int, illisibles: int,
                       clotures: int = 0) -> str:
    """Les prêts écartés de la mesure, chaque cause seulement si elle compte."""
    morceaux = []
    if nuit:
        morceaux.append(f"{nuit} {pluriel(nuit, 'prêt rendu', 'prêts rendus')} "
                        "après la nuit")
    if oublis:
        morceaux.append(f"{oublis} {pluriel(oublis, 'retour non scanné', 'retours non scannés')}")
    if en_cours:
        morceaux.append(f"{en_cours} {pluriel(en_cours, 'prêt', 'prêts')} pas "
                        f"encore {pluriel(en_cours, 'rendu', 'rendus')}")
    if clotures:
        morceaux.append(f"{clotures} {pluriel(clotures, 'prêt fermé', 'prêts fermés')} "
                        "par la clôture")
    if illisibles:
        morceaux.append(f"{illisibles} {pluriel(illisibles, 'prêt', 'prêts')} "
                        "aux dates illisibles")
    if not morceaux:
        return ""
    total = oublis + nuit + en_cours + clotures + illisibles
    liste = (", ".join(morceaux[:-1]) + " et " + morceaux[-1]
             if len(morceaux) > 1 else morceaux[0])
    return (f"Non {pluriel(total, 'mesuré', 'mesurés')} : {liste}.")


# ---------------------------------------------------------------------------
# Les tranches : lecture d'une valeur saisie, jamais d'erreur
# ---------------------------------------------------------------------------
def _entier(valeur) -> int | None:
    """Un entier lisible, ou None (texte, vide, booléen, flottant non entier)."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, int):
        return valeur
    if isinstance(valeur, float) and valeur.is_integer():
        return int(valeur)
    return None


def fourchette_joueurs(mn, mx) -> tuple[int, int] | None:
    """La fourchette (min, max) lue selon les cas limites de l'en-tête, ou None."""
    a, b = _entier(mn), _entier(mx)
    a = a if a is not None and a >= 1 else None
    b = b if b is not None and b >= 1 else None
    if a is None and b is None:
        return None
    if a is None:
        a = b
    if b is None:
        b = a
    return (a, b) if a <= b else (b, a)


def groupes_joueurs(mn, mx) -> list[str]:
    """Les clés des groupes de joueurs où entre ce jeu ; [] = non renseigné."""
    f = fourchette_joueurs(mn, mx)
    if f is None:
        return []
    return [cle for cle, _, condition in GROUPES_JOUEURS if condition(*f)]


def _tranches(bornes: tuple[int, ...], premiere: str, milieu, derniere: str):
    """[(cle, libellé)] des tranches définies par des bornes."""
    out = [(0, premiere)]
    for i in range(len(bornes) - 1):
        out.append((i + 1, milieu(bornes[i], bornes[i + 1])))
    out.append((len(bornes), derniere))
    return out


TRANCHES_AGE = _tranches(
    BORNES_AGE, f"Moins de {BORNES_AGE[0]} ans",
    lambda a, b: f"{a} et {a + 1} ans" if b - a == 2 else f"{a} à {b - 1} ans",
    f"{BORNES_AGE[-1]} ans et plus")
TRANCHES_DUREE = _tranches(
    BORNES_DUREE, f"{BORNES_DUREE[0]} min ou moins",
    lambda a, b: f"{a + 1} à {b} min", f"Plus de {BORNES_DUREE[-1]} min")


def tranche_age(age) -> int | None:
    """L'indice de la tranche d'âge (`TRANCHES_AGE`), ou None si non renseigné."""
    a = _entier(age)
    if a is None or a < 0:
        return None
    return sum(a >= b for b in BORNES_AGE)


def tranche_duree(minutes) -> int | None:
    """L'indice de la tranche de durée (`TRANCHES_DUREE`), ou None."""
    m = _entier(minutes)
    if m is None or m <= 0:
        return None
    return sum(m > b for b in BORNES_DUREE)


# ---------------------------------------------------------------------------
# Les comptes
# ---------------------------------------------------------------------------
def _ligne(cle, nom: str, refs: set[str], prets: Counter) -> dict:
    nb_titres = len(refs)
    nb_prets = sum(prets[r] for r in refs)
    par = nb_prets / nb_titres if nb_titres else None
    return {"cle": cle, "nom": nom, "nb_titres": nb_titres, "nb_prets": nb_prets,
            "par_titre": par,
            "par_titre_txt": _virgule(par) if par is not None else "—",
            "petit": 0 < nb_titres < SEUIL_JEUX, "barre": None}


def _barres(lignes: list[dict]) -> None:
    """Largeur de la barre (en % de la plus longue) des lignes comparables :
    ni les petits groupes ni les groupes vides."""
    comparables = [l for l in lignes if not l["petit"] and l["par_titre"] is not None]
    plus_haut = max((l["par_titre"] for l in comparables), default=0)
    for l in comparables:
        l["barre"] = round(100 * l["par_titre"] / plus_haut) if plus_haut else 0


def _non_renseigne(refs: set[str], prets: Counter, total: int) -> dict | None:
    if not refs:
        return None
    l = _ligne(None, NON_RENSEIGNE, refs, prets)
    l["part_txt"] = _pourcent(l["nb_prets"], total)
    return l


def _tri(l: dict):
    return (-l["par_titre"], -l["nb_prets"], cle_pliee(l["nom"]))


def ce_qui_plait(conn: sqlite3.Connection, debut: str | None,
                 fin: str | None, instants_cloture=frozenset()) -> dict:
    """
    Les chiffres de la section « Ce qui plaît » (définitions en tête).

    Args:
        conn: connexion à la base de prêt.
        debut, fin: bornes UTC ISO optionnelles (fin exclusive), celles de
            toutes les statistiques.
        instants_cloture: les `date_retour` posées par une clôture, telles
            que le comptoir les a reconnues (`comptoir.heure_par_heure`, clé
            `instants_cloture`) : il lit tous les prêts, tous motifs, ce que
            la reconnaissance demande ; on ne les relit pas.

    Returns:
        dict {
          "nb_titres", "total_prets",
          "categories": {"toutes" (Excel, triées), "lignes" (page, classées,
              au plus `LIGNES_PAGE`), "autres" (ligne agrégée ou None),
              "nb_suite", "nb_petites", "non_renseigne" (ligne ou None),
              "aucune" (aucune catégorie au catalogue)},
          "joueurs": {"lignes", "non_renseigne", "petites"},
          "age": {"lignes", "non_renseigne", "petites"},
          "durees": {"lignes", "ensemble", "non_renseigne", "nb_mesures",
              "oublis", "nuit", "en_cours", "clotures", "illisibles"},
          "sans_categorie": [{reference_titre, nom, nb_exemplaires, nb_prets}],
              les plus prêtés d'abord,
        }
        Une ligne : {cle, nom, nb_titres, nb_prets, par_titre,
        par_titre_txt, petit, barre (0-100, ou None sans barre)}.
    """
    titres = conn.execute(
        """
        SELECT t.reference_titre, t.nom, t.nb_joueurs_min, t.nb_joueurs_max,
               t.age_min, t.duree_min, COUNT(e.id_exemplaire) AS nb
        FROM titres t
        JOIN exemplaires e ON e.reference_titre = t.reference_titre
        GROUP BY t.reference_titre
        """
    ).fetchall()
    liaisons = conn.execute(
        """
        SELECT tc.reference_titre, c.nom, c.cle
        FROM titres_categories tc
        JOIN categories_jeu c ON c.id_categorie_jeu = tc.id_categorie_jeu
        """
    ).fetchall()
    f, params = _filtre_periode("p.date_sortie", debut, fin)
    motifs = ", ".join("?" for _ in MOTIFS_COMPTES)
    lignes_prets = conn.execute(
        f"""
        SELECT p.date_sortie, p.date_retour, p.motif, e.reference_titre
        FROM prets p
        JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
        WHERE p.motif IN ({motifs}){f}
        """,
        [*MOTIFS_COMPTES, *params],
    ).fetchall()

    par_ref = {t["reference_titre"]: t for t in titres}
    prets: Counter = Counter(l["reference_titre"] for l in lignes_prets)
    total = sum(prets[r] for r in par_ref)

    # --- Catégories ---------------------------------------------------------
    membres: dict[str, set[str]] = defaultdict(set)
    noms: dict[str, str] = {}
    avec_categorie: set[str] = set()
    for l in liaisons:
        if l["reference_titre"] in par_ref:
            membres[l["cle"]].add(l["reference_titre"])
            noms[l["cle"]] = l["nom"]
            avec_categorie.add(l["reference_titre"])
    toutes = sorted((_ligne(c, noms[c], refs, prets) for c, refs in membres.items()),
                    key=_tri)
    classees = [l for l in toutes if not l["petit"]]
    page, suite = classees[:LIGNES_PAGE], classees[LIGNES_PAGE:]
    petites = [l for l in toutes if l["petit"]]
    autres = None
    if suite or petites:
        refs_autres = set().union(*(membres[l["cle"]] for l in suite + petites))
        autres = _ligne(None, AUTRES_CATEGORIES, refs_autres, prets)
        autres["petit"] = False      # un agrégat : comparable, sans barre
    _barres(page)
    sans = set(par_ref) - avec_categorie

    # --- Joueurs et âge -----------------------------------------------------
    def tranches(definitions, ranger) -> dict:
        groupes: dict = defaultdict(set)
        inconnus: set[str] = set()
        for ref, t in par_ref.items():
            cles = ranger(t)
            if not cles:
                inconnus.add(ref)
            for c in cles:
                groupes[c].add(ref)
        lignes = [_ligne(c, nom, groupes[c], prets) for c, nom in definitions]
        _barres(lignes)
        return {"lignes": lignes, "non_renseigne": _non_renseigne(inconnus, prets, total),
                "petites": any(l["petit"] for l in lignes)}

    joueurs = tranches([(c, nom) for c, nom, _ in GROUPES_JOUEURS],
                       lambda t: groupes_joueurs(t["nb_joueurs_min"], t["nb_joueurs_max"]))
    age = tranches(TRANCHES_AGE,
                   lambda t: [] if (i := tranche_age(t["age_min"])) is None else [i])

    # --- Durées -------------------------------------------------------------
    mesures: dict = defaultdict(list)          # tranche -> [(annoncée min, réelle s)]
    compte_tranche: Counter = Counter()        # tous les prêts, par tranche
    oublis = nuit = en_cours = clotures = illisibles = 0
    for l in lignes_prets:
        t = par_ref.get(l["reference_titre"])
        if t is None:
            continue
        tranche = tranche_duree(t["duree_min"])
        compte_tranche[tranche] += 1
        if l["motif"] == MOTIF_OUBLI:
            oublis += 1
            continue
        if l["date_retour"] is None:
            en_cours += 1
            continue
        if l["date_retour"] in instants_cloture:
            clotures += 1
            continue
        sortie, retour = lire_instant(l["date_sortie"]), lire_instant(l["date_retour"])
        if sortie is None or retour is None or retour <= sortie:
            illisibles += 1
            continue
        if retour > fin_de_journee(sortie):
            nuit += 1
            continue
        annoncee = t["duree_min"] if tranche is not None else None
        mesures[tranche].append((annoncee, (retour - sortie).total_seconds()))

    def ligne_duree(cle, nom, valeurs, nb_prets) -> dict:
        reelles = [r for _, r in valeurs]
        annoncees = [a for a, _ in valeurs if a is not None]
        m_reelle = median(reelles) if reelles else None
        m_annoncee = median(annoncees) * 60 if annoncees else None
        return {"cle": cle, "nom": nom, "nb_prets": nb_prets,
                "nb_mesures": len(valeurs),
                "annoncee_s": m_annoncee, "annoncee_txt": format_duree(m_annoncee),
                "reelle_s": m_reelle, "reelle_txt": format_duree(m_reelle)}

    tout = [v for vs in mesures.values() for v in vs]
    durees = {
        "lignes": [ligne_duree(c, nom, mesures[c], compte_tranche[c])
                   for c, nom in TRANCHES_DUREE],
        "non_renseigne": (ligne_duree(None, NON_RENSEIGNE, mesures[None],
                                      compte_tranche[None])
                          if compte_tranche[None] else None),
        "ensemble": ligne_duree(None, TOUTES_DUREES, tout, sum(compte_tranche.values())),
        "nb_mesures": len(tout),
        "oublis": oublis, "nuit": nuit, "en_cours": en_cours,
        "clotures": clotures, "illisibles": illisibles,
    }

    sans_categorie = sorted(
        ({"reference_titre": r, "nom": par_ref[r]["nom"],
          "nb_exemplaires": par_ref[r]["nb"], "nb_prets": prets[r]} for r in sans),
        key=lambda x: (-x["nb_prets"], cle_pliee(x["nom"]), x["reference_titre"]))

    return {
        "nb_titres": len(par_ref), "total_prets": total,
        "categories": {"toutes": toutes, "lignes": page, "autres": autres,
                       "nb_suite": len(suite), "nb_petites": len(petites),
                       "non_renseigne": _non_renseigne(sans, prets, total),
                       "aucune": not toutes},
        "joueurs": joueurs, "age": age, "durees": durees,
        "sans_categorie": sans_categorie,
    }
