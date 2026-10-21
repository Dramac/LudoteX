"""
Import / mise à jour du catalogue depuis le CSV de l'association.

Principes (voir docs/specification.md §3.1) :
- Tolérant aux colonnes variables : seules deux données sont exigées,
  l'identifiant d'exemplaire (CSV « Code jeu ») et le nom du jeu. Tout le
  reste est optionnel ; l'import remplit ce qu'il trouve.
- Deux clés stables :
    * id_exemplaire  <- « Code jeu » (TEXT, zéros de tête préservés).
    * reference_titre <- slug normalisé du nom -> REGROUPE les exemplaires
      d'un même jeu (ex. les 4 « Dobble » partagent une référence). Un titre
      RENOMMÉ À L'ÉCRAN garde sa référence : l'import le retrouve par le nom
      qu'il porte aujourd'hui (voir `resoudre_reference`).
- Les colonnes d'état du CSV (Suspendu, Prêt en cours, Réserv Active, Sur
  place, Manque) sont IGNORÉES : l'état d'un exemplaire est déduit des prêts.
- Idempotent : relançable sans créer de doublons (UPSERT sur les deux clés).

Usage :
    python -m scripts.import_csv chemin/vers/catalogue.csv
    python -m scripts.import_csv catalogue.csv --dry-run   # n'écrit rien
    python -m scripts.import_csv catalogue.csv --groupes    # liste les regroupements

Le séparateur (« ; » ou « , ») et l'encodage (UTF-8, avec ou sans BOM) sont
détectés automatiquement.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Permet « python scripts/import_csv.py » comme « python -m scripts.import_csv ».
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as _db  # noqa: E402
from app.db import get_connection, init_db  # noqa: E402
# slug_titre est PARTAGÉ avec l'app (création de jeu via l'admin) pour produire
# exactement les mêmes références de regroupement ; nettoyer_nom, avec
# l'éditeur de la fiche, pour qu'un nom corrigé à l'écran se reconnaisse dans
# l'export qui le porte. obtenir_ou_creer_emplacement_rangement sert à
# résoudre/créer tolérament l'« Emplacement local » du CSV
# (docs/conception-rangement.md §4.b, étape 7) sans dupliquer sa logique.
# CHAMPS_FICHE : les colonnes que l'écran corrige et que l'import écrase.
# ecrire_categories_titre : seul chemin d'écriture des catégories d'un titre
# (liaison et copie `titres.categorie`), partagé avec l'écran.
from app.services import (CHAMPS_FICHE, ecrire_categories_titre,  # noqa: E402
                          lire_vocabulaire_categories, nettoyer_nom,
                          obtenir_ou_creer_emplacement_rangement,
                          purger_categories_orphelines, slug_titre)
# Découpage d'une case « Type jeu » et rapprochement des catégories.
from app import categories_jeu  # noqa: E402
# L'identifiant BoardGameGeek se lit au même domicile qu'à l'écran.
from app.bgg import est_pas_sur_bgg  # noqa: E402
from app.bgg import lire_identifiant as lire_bgg_id  # noqa: E402
# Les lettres du code de classement se valident au domicile de leurs listes.
from app.classement import COLONNES_LETTRES, lire_saisie  # noqa: E402
# L'export du catalogue neutralise les formules par une apostrophe (SEC-06) :
# l'import la retire, avec la fonction inverse, au même domicile.
from app.exports import retirer_neutralisation_csv  # noqa: E402

# ---------------------------------------------------------------------------
# Bornes de l'import depuis l'administration (lot-11-pré-production)
# ---------------------------------------------------------------------------
# Le catalogue réel compte ~700 lignes pour ~179 Ko (septembre 2026, ~254
# octets par ligne). Les deux bornes laissent un facteur de l'ordre de trente,
# pour un catalogue qui grossit. Ce qu'elles arrêtent n'est pas un catalogue
# légitime trop gros, c'est le MAUVAIS fichier choisi dans le sélecteur.
#
# En lignes (ROB-06) : l'analyse d'un CSV est du Python pur, dans l'unique
# worker ; mesuré par l'audit, 400 000 lignes y retardaient un prêt concurrent
# de 7,9 s. 20 000 lignes s'importent en ~0,3 s (mesuré sur une copie locale).
MAX_LIGNES_CATALOGUE = 20_000
# En octets (ROB-03(a)) : 20 000 lignes à la densité réelle font ~5 Mo ; 8 Mo
# laissent de la place à des descriptifs plus longs. Reste sous
# `client_max_body_size` (20m) de nginx — vérifié par tests/test_envois_bornes.py.
TAILLE_MAX_CATALOGUE = 8 * 1024 * 1024


class CatalogueTropLong(Exception):
    """Le CSV dépasse `max_lignes` lignes de données. RIEN n'a été écrit."""

    def __init__(self, max_lignes: int):
        self.max_lignes = max_lignes
        super().__init__(
            f"Ce fichier compte plus de {max_lignes:,} lignes".replace(",", "\u202f")
            + " : il ne ressemble pas à un catalogue. Vérifiez qu'il s'agit du "
              "bon export, puis réessayez."
        )


# ---------------------------------------------------------------------------
# Correspondance colonnes CSV -> champs du modèle
# ---------------------------------------------------------------------------
# Insensible à la casse et aux espaces. On liste plusieurs intitulés possibles
# pour rester tolérant si le CSV évolue.
COLONNES = {
    "id_exemplaire": ["code jeu", "id_exemplaire", "code"],
    "nom":           ["nom jeu", "nom", "titre"],
    "type_jeu":      ["type"],                # "Jeu" ou "Extension"
    "categorie":     ["type jeu", "categorie", "catégorie", "classification"],
    "nb_joueurs":    ["nb joueurs", "nombre de joueurs", "joueurs"],
    "age":           ["age joueurs", "âge joueurs", "age", "âge"],
    "duree":         ["temps jeu", "durée", "duree", "temps"],
    "editeur":       ["marque", "editeur", "éditeur"],
    "auteur":        ["auteur", "auteurs"],
    "annee":         ["année édition", "annee edition", "année", "annee"],
    "descriptif":    ["descriptif", "description"],
    "date_achat":    ["date achat", "date d'achat", "achat"],
    # Rangement (docs/conception-rangement.md §4.b) — colonnes PAR EXEMPLAIRE
    # (pas agrégées au niveau titre, contrairement à tout ce qui précède) :
    # alias choisis pour correspondre exactement à EN_TETES_CATALOGUE
    # (app/services.py), donc à un export ré-importable sans réglage.
    "emplacement_evenement": ["emplacement événement", "emplacement evenement"],
    "emplacement_local":     ["emplacement local"],
    # Code de classement (app/classement.py) : UNE colonne où l'on tape `TPC`,
    # découpée en trois lettres. Ni « classification » (déjà pris par
    # `categorie` ci-dessus) ni « classement » seul (trop vague).
    "lettres_classement":    ["lettres classement"],
    # Fiche BoardGameGeek (app/bgg.py) : le numéro, ou l'adresse de la page.
    "bgg_id":                ["fiche boardgamegeek"],
}

# Mois français (abrégés ou complets, sans accents) -> numéro, pour parser les
# dates d'achat du type « 16-sept-19 ».
_MOIS_FR = {
    "janv": 1, "janvier": 1, "fevr": 2, "fev": 2, "fevrier": 2, "mars": 3,
    "avr": 4, "avril": 4, "mai": 5, "juin": 6, "juil": 7, "juillet": 7,
    "aout": 8, "sept": 9, "septembre": 9, "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11, "dec": 12, "decembre": 12,
}


def _norm_header(h: str) -> str:
    """Normalise un intitulé de colonne pour la comparaison (trim + minuscules)."""
    return (h or "").strip().lower()


def construire_index_colonnes(entetes: list[str]) -> dict[str, str | None]:
    """
    Fait correspondre chaque champ logique du modèle à la colonne réelle du CSV.

    Pour chaque champ (clé de COLONNES), on cherche le premier alias présent
    dans les en-têtes (comparaison normalisée). Cela rend l'import tolérant aux
    variations d'intitulé d'un export à l'autre.

    Args:
        entetes: liste des noms de colonnes lus en tête du CSV.

    Returns:
        dict {champ_logique: intitulé_réel_ou_None}. None = colonne absente.
    """
    presentes = {_norm_header(h): h for h in entetes}
    index: dict[str, str | None] = {}
    for champ, alias in COLONNES.items():
        index[champ] = next((presentes[a] for a in alias if a in presentes), None)
    return index


# ---------------------------------------------------------------------------
# Nettoyage et parsing des valeurs
# ---------------------------------------------------------------------------
def _entiers(valeur: str) -> list[int]:
    """Tous les entiers présents dans une chaîne, dans l'ordre."""
    return [int(n) for n in re.findall(r"\d+", valeur or "")]


def parse_nb_joueurs(valeur: str) -> tuple[int | None, int | None]:
    """'2 - 4' -> (2, 4) ; '2' -> (2, 2) ; vide -> (None, None)."""
    nums = _entiers(valeur)
    if not nums:
        return None, None
    return nums[0], nums[-1]


def parse_age(valeur: str) -> int | None:
    """'10 +' -> 10 ; '8 ans' -> 8 ; vide -> None."""
    nums = _entiers(valeur)
    return nums[0] if nums else None


def parse_duree(valeur: str) -> int | None:
    """'30 mn' -> 30 ; '<= 15 mn' -> 15 ; '30 - 60 mn' -> 30 (borne basse)."""
    nums = _entiers(valeur)
    return nums[0] if nums else None


def parse_annee(valeur: str) -> int | None:
    """Premier nombre à 4 chiffres trouvé (année), sinon None."""
    m = re.search(r"(19|20)\d{2}", valeur or "")
    return int(m.group(0)) if m else None


def parse_date_achat(valeur: str) -> str | None:
    """
    Convertit une date d'achat en ISO 'AAAA-MM-JJ', ou None.

    Gère « 16-sept-19 » (jour-mois_fr-année 2 chiffres), « 16/09/2019 »,
    « 16-09-19 »… Le mois peut être un nombre ou un mois français (abrégé/complet,
    accents ignorés). Une année à 2 chiffres est interprétée en 20xx.

    Returns:
        La date ISO triable (ex. '2019-09-16'), ou None si non reconnue.
    """
    v = unicodedata.normalize("NFKD", (valeur or "").strip())
    v = v.encode("ascii", "ignore").decode("ascii").lower()
    if not v:
        return None
    parts = re.split(r"[\s/.\-]+", v)
    if len(parts) != 3:
        return None
    jour_s, mois_s, an_s = parts
    try:
        jour = int(jour_s)
        mois = int(mois_s) if mois_s.isdigit() else _MOIS_FR.get(mois_s[:4]) or _MOIS_FR.get(mois_s)
        an = int(an_s)
    except (ValueError, TypeError):
        return None
    if not mois or not (1 <= mois <= 12) or not (1 <= jour <= 31):
        return None
    if an < 100:
        an += 2000
    return f"{an:04d}-{mois:02d}-{jour:02d}"


def _ou_none(valeur: str) -> str | None:
    valeur = (valeur or "").strip()
    return valeur or None


# ---------------------------------------------------------------------------
# Lecture du CSV
# ---------------------------------------------------------------------------
# Encodage de repli : celui dans lequel Excel, sous Windows, enregistre un
# « CSV (séparateur : point-virgule) ». C'est le fichier que produit quelqu'un
# qui suit la consigne « enregistrez votre classeur au format CSV ».
ENCODAGE_EXCEL = "cp1252"
_BOM_UTF16 = (b"\xff\xfe", b"\xfe\xff")


def detecter_encodage(chemin: Path) -> str:
    """
    L'encodage dans lequel lire le CSV : UTF-8 s'il en est, sinon celui d'Excel.

    L'UTF-8 se vérifie : un texte accentué écrit autrement n'en est presque
    jamais un valide. On tente donc l'UTF-8 sur tout le fichier (par morceaux,
    sans le garder en mémoire) et, s'il casse, on retient `ENCODAGE_EXCEL`.
    L'inverse ne se vérifie pas — n'importe quelle suite d'octets ou presque se
    lit en cp1252 — d'où l'ordre, et d'où le message que l'appelant affiche
    quand le repli a servi (`importer`, clé `encodage_repli`) : un fichier venu
    d'ailleurs (ancien Excel pour Mac) passerait avec des accents faux, jamais
    en silence.

    Un « Texte Unicode » d'Excel (UTF-16) se reconnaît à ses deux premiers
    octets.
    """
    with open(chemin, "rb") as brut:
        if brut.read(2) in _BOM_UTF16:
            return "utf-16"
    try:
        with open(chemin, encoding="utf-8-sig", newline="") as fh:
            while fh.read(65536):
                pass
    except UnicodeDecodeError:
        return ENCODAGE_EXCEL
    return "utf-8-sig"


def lire_csv(
    chemin: Path, max_lignes: int | None = None,
) -> tuple[list[dict], dict[str, str | None]]:
    """
    Lit le CSV et détecte automatiquement le séparateur.

    L'encodage est choisi par `detecter_encodage` : UTF-8 (avec ou sans BOM),
    sinon celui d'un CSV enregistré par Excel. Le séparateur est deviné par `csv.Sniffer` parmi `;`, `,` et
    tabulation ; à défaut, on retombe sur `;`.

    Args:
        chemin: chemin du fichier CSV.
        max_lignes: plafond de lignes de données (en-tête exclu). Compté
            PENDANT la lecture, qui s'arrête à la première ligne de trop et
            lève `CatalogueTropLong` : un fichier démesuré n'est ni lu en
            entier ni analysé deux fois. None (ligne de commande) : aucun
            plafond.

    Returns:
        (lignes, index_colonnes) où `lignes` est une liste de dicts (une par
        ligne, clés = en-têtes réels) et `index_colonnes` la correspondance
        champ logique → en-tête réel.
    """
    # `errors="replace"` : cp1252 laisse cinq octets sans caractère. Un fichier
    # qui en porte n'est pas un catalogue ; il échouera plus loin sur ses
    # colonnes clés, avec un message lisible, plutôt qu'ici sur un numéro d'octet.
    with open(chemin, encoding=detecter_encodage(chemin), errors="replace",
              newline="") as fh:
        echantillon = fh.read(4096)
        fh.seek(0)
        try:
            dialecte = csv.Sniffer().sniff(echantillon, delimiters=";,\t")
            sep = dialecte.delimiter
        except csv.Error:
            sep = ";"  # repli : la liste de l'asso utilise « ; »
        lecteur = csv.DictReader(fh, delimiter=sep)
        lignes = []
        for ligne in lecteur:
            if max_lignes is not None and len(lignes) >= max_lignes:
                raise CatalogueTropLong(max_lignes)
            # Une ligne trop longue range son surplus sous la clé None, en
            # liste : on ne touche qu'aux chaînes.
            lignes.append({cle: retirer_neutralisation_csv(val)
                           for cle, val in ligne.items()})
        entetes = lecteur.fieldnames or []
    index = construire_index_colonnes(entetes)
    return lignes, index


# ---------------------------------------------------------------------------
# Ce que la base contient déjà — lu AVANT d'écrire, simulation comprise
# ---------------------------------------------------------------------------
@dataclass
class EtatBase:
    """
    L'état de la base dont l'import a besoin avant d'écrire.

    - `exemplaires` : id_exemplaire -> reference_titre ;
    - `titres` : reference_titre -> la ligne du titre (dict), réduite aux
      colonnes utiles (nom, `CHAMPS_FICHE`, `modifie_le`) ;
    - `categories` : le vocabulaire des catégories de jeu tel qu'il sera au
      moment d'écrire, clé de rapprochement -> graphie (voir
      `lire_etat_base`). Une catégorie du fichier absente d'ici est NOUVELLE.

    Lu par `lire_etat_base`, en simulation comme en import réel : la
    simulation annonce ainsi exactement ce que l'import fera.
    """
    exemplaires: dict[str, str] = field(default_factory=dict)
    titres: dict[str, dict] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)
    # slug du nom ACTUEL -> références des titres qui portent ce nom.
    _par_slug: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        par_slug: dict[str, list[str]] = defaultdict(list)
        for ref, titre in self.titres.items():
            par_slug[slug_titre(titre["nom"] or "")].append(ref)
        self._par_slug = dict(par_slug)

    def titres_au_nom(self, nom: str) -> list[str]:
        """Les titres dont le nom ACTUEL a le même slug que `nom`."""
        return self._par_slug.get(slug_titre(nom), [])


def lire_etat_base(conn: sqlite3.Connection | None) -> EtatBase:
    """
    L'état utile de la base, ou un état vide si elle n'existe pas encore
    (premier import, simulation sur une installation neuve). Lecture seule.
    """
    if conn is None:
        return EtatBase()
    colonnes = ", ".join(dict.fromkeys(("reference_titre", "nom", *CHAMPS_FICHE,
                                        "modifie_le")))
    try:
        titres = {r["reference_titre"]: dict(r)
                  for r in conn.execute(f"SELECT {colonnes} FROM titres")}
        exemplaires = {r[0]: r[1] for r in conn.execute(
            "SELECT id_exemplaire, reference_titre FROM exemplaires")}
    except sqlite3.OperationalError:
        # Tables absentes (base jamais initialisée) : rien à retrouver.
        return EtatBase()
    return EtatBase(exemplaires=exemplaires, titres=titres,
                    categories=_lire_vocabulaire(conn))


def _lire_vocabulaire(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Le vocabulaire des catégories tel que l'import le trouvera en écrivant :
    celui de la base, PLUS ce que la migration (`db._migrer_categories_jeu`,
    rejouée par l'`init_db` de l'import réel) créera pour les titres pas encore
    rattachés — même règle, même fonction. Sans cela, une simulation sur une
    base d'avant le lot (la ligne de commande ne migre pas en simulation)
    annoncerait comme nouvelles des catégories que l'import réel trouverait
    déjà là.
    """
    try:
        connues = lire_vocabulaire_categories(conn)
        a_rattacher = conn.execute(
            "SELECT categorie FROM titres t WHERE NOT EXISTS ("
            "SELECT 1 FROM titres_categories tc "
            "WHERE tc.reference_titre = t.reference_titre)").fetchall()
    except sqlite3.OperationalError:
        # Base d'avant le lot : aucune catégorie en liste, tout est à rattacher.
        connues = {}
        a_rattacher = conn.execute("SELECT categorie FROM titres").fetchall()
    return {**connues, **categories_jeu.vocabulaire_de_migration(
        [r[0] for r in a_rattacher], connues)}


def resoudre_reference(id_exemplaire: str, nom: str, base: EtatBase | None) -> str:
    """
    La référence du titre auquel rattacher une ligne du fichier.

    Sans titre renommé à l'écran, c'est toujours `slug_titre(nom)` — la règle
    d'avant, inchangée. Un titre RENOMMÉ à l'écran garde, lui, la référence
    tirée de son ANCIEN nom (« Catane » corrigé en « Catan » reste `CATANE`) :
    recalculer le slug du nom du fichier créerait un second titre `CATAN`, y
    déplacerait les exemplaires et laisserait `CATANE` vide, avec sa fiche
    BoardGameGeek, ses lettres et sa correction. D'où, dans l'ordre :

    1. La boîte est déjà en base, et le nom du fichier a le même slug que le
       nom ACTUEL de son titre : elle reste dans ce titre. C'est l'aller-retour
       export → import d'un jeu renommé. (« Même slug » plutôt que « même
       nom » : c'est la règle de regroupement de toujours, et une casse
       corrigée dans le tableur ne scinde pas un titre.)
    2. Le slug du nom désigne un titre existant : ce titre (règle d'avant).
    3. Un seul titre porte aujourd'hui ce nom (au slug près) : ce titre. C'est
       le nouvel exemplaire ajouté dans le tableur sous le nom corrigé.
    4. Sinon : le slug du nom — un nouveau titre, comme avant.

    Un nom qui DIFFÈRE dans le fichier (au slug près) de celui de la base reste
    un geste volontaire : la boîte change de titre, comme avant.
    """
    ref = slug_titre(nom)
    if base is None:
        return ref
    actuelle = base.exemplaires.get(id_exemplaire)
    if actuelle in base.titres and slug_titre(base.titres[actuelle]["nom"] or "") == ref:
        return actuelle
    if ref in base.titres:
        return ref
    candidats = base.titres_au_nom(nom)
    if len(candidats) == 1:
        return candidats[0]
    return ref


def construire_donnees(lignes: list[dict], index: dict[str, str | None],
                       base: EtatBase | None = None):
    """
    Transforme les lignes CSV en :
      - exemplaires : liste de dicts {id_exemplaire, reference_titre,
        emplacement_evenement, emplacement_local_nom} — PAR EXEMPLAIRE
        (contrairement à `titres`, aucune agrégation : chaque boîte a sa
        propre valeur). Les deux champs d'emplacement sont `None` si la
        colonne est absente/vide (§4.b : une case vide ne doit RIEN écraser
        à l'import — voir `importer`, qui applique cette règle via COALESCE).
      - titres      : dict reference_titre -> champs agrégés (1re valeur non vide)
      - groupes     : reference_titre -> liste d'id_exemplaire (pour le rapport)
      - lettres_refusees : nombre de lignes dont la case « Lettres classement »
        est remplie mais invalide (la ligne s'importe, sans ses lettres)
      - bgg_refusees : même chose pour la case « Fiche BoardGameGeek »
      - categories_nouvelles : les catégories de jeu que l'import créera,
        absentes du vocabulaire de `base`, sous la graphie de leur première
        occurrence (voir plus bas)
    Lève une erreur si les colonnes clés (Code jeu, Nom jeu) sont absentes.

    CATÉGORIES. La case « Type jeu » d'un titre est la première case non vide
    de ses lignes, comme pour les autres colonnes — « non vide » au sens de
    `categories_jeu.decouper` : une case « , » ne dit rien. Elle est ensuite
    mise sous forme CANONIQUE (`categories_jeu.Vocabulaire`) : graphie en base
    pour une catégorie connue, graphie de la première occurrence pour une
    nouvelle. « Première occurrence » parmi les cases RETENUES, dans l'ordre
    des lignes où elles l'ont été : une case ignorée (titre déjà renseigné)
    n'est pas écrite, elle ne crée donc rien et n'impose aucune graphie.

    `base` : l'état de la base (`lire_etat_base`), qui sert à retrouver un
    titre renommé à l'écran (`resoudre_reference`). None : la règle d'avant,
    référence = slug du nom.

    Pure (aucun accès base, l'état est passé en argument) : utilisable en
    toute sécurité par le mode --dry-run. La résolution/création de l'emplacement local (qui ÉCRIT en
    base si le nom est inconnu) est déléguée à `importer`, seule fonction qui
    ouvre une connexion.
    """
    if not index["id_exemplaire"] or not index["nom"]:
        raise SystemExit(
            "ERREUR : colonnes clés introuvables. Le CSV doit comporter au "
            "minimum un identifiant d'exemplaire (« Code jeu ») et un nom "
            "(« Nom jeu »)."
        )

    def val(ligne, champ):
        col = index[champ]
        return ligne.get(col, "") if col else ""

    exemplaires: list[dict] = []
    titres: dict[str, dict] = {}
    groupes: dict[str, list[str]] = defaultdict(list)
    ignores: list[str] = []
    vus: set[str] = set()
    lettres_refusees = 0
    bgg_refusees = 0
    # (référence, case) dans l'ordre où chaque titre a retenu sa case.
    cases_categories: list[tuple[str, str]] = []

    for ligne in lignes:
        id_ex = (val(ligne, "id_exemplaire") or "").strip()
        nom = nettoyer_nom(val(ligne, "nom"))
        if not id_ex or not nom:
            ignores.append(id_ex or "(code vide)")
            continue
        if id_ex in vus:
            ignores.append(f"{id_ex} (doublon de Code jeu)")
            continue
        vus.add(id_ex)

        ref = resoudre_reference(id_ex, nom, base)
        exemplaires.append({
            "id_exemplaire": id_ex,
            "reference_titre": ref,
            "emplacement_evenement": _ou_none(val(ligne, "emplacement_evenement")),
            "emplacement_local_nom": _ou_none(val(ligne, "emplacement_local")),
        })
        groupes[ref].append(id_ex)

        nb_min, nb_max = parse_nb_joueurs(val(ligne, "nb_joueurs"))
        # Lettres : une case vide ne dit rien (None partout, voir le COALESCE
        # d'`importer`) ; une case remplie mais invalide non plus, mais elle
        # est COMPTÉE pour le rapport. Jamais de lettre partielle en base.
        saisie = (val(ligne, "lettres_classement") or "").strip()
        triplet = lire_saisie(saisie) if saisie else None
        if saisie and triplet is None:
            lettres_refusees += 1
        lettres = dict(zip(COLONNES_LETTRES, triplet or (None, None, None)))
        # Fiche BoardGameGeek : même règle que les lettres — case vide neutre,
        # case illisible neutre mais comptée. La valeur réservée « pas sur
        # BGG » (app/bgg.py, lot fiche-3) pose la marque, sans identifiant.
        saisie_bgg = (val(ligne, "bgg_id") or "").strip()
        pas_sur_bgg = est_pas_sur_bgg(saisie_bgg)
        bgg_id = lire_bgg_id(saisie_bgg) if saisie_bgg and not pas_sur_bgg else None
        if saisie_bgg and not pas_sur_bgg and bgg_id is None:
            bgg_refusees += 1
        champs = {
            "reference_titre": ref,
            "nom": nom,
            "type_jeu": _ou_none(val(ligne, "type_jeu")),
            "categorie": (_ou_none(val(ligne, "categorie"))
                          if categories_jeu.decouper(val(ligne, "categorie"))
                          else None),
            "nb_joueurs_min": nb_min,
            "nb_joueurs_max": nb_max,
            "duree_min": parse_duree(val(ligne, "duree")),
            "age_min": parse_age(val(ligne, "age")),
            "editeur": _ou_none(val(ligne, "editeur")),
            "auteur": _ou_none(val(ligne, "auteur")),
            "annee_edition": parse_annee(val(ligne, "annee")),
            "descriptif": _ou_none(val(ligne, "descriptif")),
            "date_achat": parse_date_achat(val(ligne, "date_achat")),
            "bgg_id": bgg_id,
            # None : la case ne dit rien de la marque (elle n'est pas touchée).
            "bgg_absent": 1 if pas_sur_bgg else None,
            **lettres,
        }
        # Agrégation au niveau titre : on conserve la 1re valeur non vide
        # rencontrée parmi les exemplaires d'un même titre. Les trois lettres
        # voyagent ENSEMBLE : un titre prend le premier triplet valide, jamais
        # une lettre d'une ligne et deux d'une autre.
        if ref not in titres:
            titres[ref] = champs
            if champs["categorie"]:
                cases_categories.append((ref, champs["categorie"]))
        else:
            if titres[ref]["categorie"] is None and champs["categorie"]:
                cases_categories.append((ref, champs["categorie"]))
            for k, v in champs.items():
                if k in COLONNES_LETTRES:
                    continue
                if titres[ref].get(k) in (None, "") and v not in (None, ""):
                    titres[ref][k] = v
            if triplet and titres[ref][COLONNES_LETTRES[0]] is None:
                titres[ref].update(lettres)
        # Exception : date_achat = la PLUS RÉCENTE parmi les exemplaires du titre
        # (dernière acquisition). Les dates ISO se comparent comme des chaînes.
        da = champs["date_achat"]
        if da and (titres[ref].get("date_achat") is None or da > titres[ref]["date_achat"]):
            titres[ref]["date_achat"] = da

    vocabulaire = categories_jeu.Vocabulaire(base.categories if base else None)
    for ref, case in cases_categories:
        titres[ref]["categorie"] = categories_jeu.joindre(vocabulaire.retenir(case))
    # `titres` remis dans l'ordre où les cases ont été retenues (les titres
    # sans catégorie à la suite) : `importer` écrit les catégories dans
    # l'ordre de ce dictionnaire, et crée donc les nouvelles dans l'ordre même
    # où la simulation les annonce. Rien d'autre ne dépend de cet ordre.
    ordre = {ref: i for i, (ref, _) in enumerate(cases_categories)}
    titres = dict(sorted(titres.items(),
                         key=lambda item: ordre.get(item[0], len(ordre))))

    return (exemplaires, titres, groupes, ignores, lettres_refusees, bgg_refusees,
            vocabulaire.nouvelles)


# ---------------------------------------------------------------------------
# Corrections faites à l'écran que le fichier défait
# ---------------------------------------------------------------------------
def corrections_defaites(titres: dict[str, dict], exemplaires: list[dict],
                         base: EtatBase) -> list[dict]:
    """
    Les fiches corrigées à l'écran (`modifie_le` posé) dont le fichier porte une
    valeur DIFFÉRENTE de celle de la base, sur une colonne que l'import écrase.

    Relevé AVANT l'écriture : l'import se fait quand même — ni blocage ni
    confirmation —, mais le message de fin le dit, fiche par fiche, avec la
    valeur d'avant et celle du fichier. Un fichier identique à la base (l'export
    fait après la correction) ne relève rien.

    Deux cas :
    - le titre reçoit du fichier une autre valeur : écart sur ce champ ;
    - une boîte quitte un titre corrigé parce que le fichier lui donne un autre
      nom : écart sur le nom.

    La catégorie se compare sous forme CANONIQUE des deux côtés : un fichier
    qui porte « bluff » face à une fiche corrigée « Bluff » ne dit rien de
    différent, et ne doit rien signaler. Le côté fichier l'est déjà
    (`construire_donnees`) ; le côté base l'est ici, face au même vocabulaire.

    Returns:
        Liste triée par nom de {"reference_titre", "nom", "ecrase" (True si
        l'import écrit sur la fiche), "ecarts": [(libellé, avant, fichier)]}.
    """
    ecarts: dict[str, dict] = {}

    def noter(ref, champ, avant, fichier, ecrase):
        fiche = ecarts.setdefault(ref, {
            "reference_titre": ref, "nom": base.titres[ref]["nom"],
            "ecrase": False, "ecarts": {}})
        fiche["ecrase"] = fiche["ecrase"] or ecrase
        fiche["ecarts"].setdefault(champ, (CHAMPS_FICHE[champ], avant, fichier))

    for ref, champs in titres.items():
        actuel = base.titres.get(ref)
        if not actuel or not actuel.get("modifie_le"):
            continue
        for champ in CHAMPS_FICHE:
            valeur = actuel.get(champ)
            if champ == "categorie":
                valeur = categories_jeu.canoniser(valeur, base.categories)
            if champs.get(champ) != valeur:
                noter(ref, champ, actuel.get(champ), champs.get(champ), True)
    for ex in exemplaires:
        ancienne = base.exemplaires.get(ex["id_exemplaire"])
        actuel = base.titres.get(ancienne) if ancienne else None
        if (actuel and actuel.get("modifie_le")
                and ex["reference_titre"] != ancienne):
            noter(ancienne, "nom", actuel["nom"],
                  titres[ex["reference_titre"]]["nom"], False)

    return sorted(
        ({**f, "ecarts": list(f["ecarts"].values())} for f in ecarts.values()),
        key=lambda f: (f["nom"] or "").lower())


# ---------------------------------------------------------------------------
# Écriture en base
# ---------------------------------------------------------------------------
def importer(chemin: Path, dry_run: bool = False,
             max_lignes: int | None = None) -> dict:
    """
    Importe (ou simule) le catalogue en base.

    Idempotence : les deux INSERT utilisent `ON CONFLICT … DO UPDATE` (UPSERT)
    sur les clés primaires (`reference_titre`, `id_exemplaire`). Relancer
    l'import après mise à jour du CSV met simplement à jour les lignes existantes
    sans créer de doublon. `executemany` fait l'insertion en lot (performant).

    RANGEMENT (docs/conception-rangement.md §4.b, étape 7) : `emplacement_
    evenement` est réécrit UNIQUEMENT si la colonne CSV est non vide pour
    cette ligne (`COALESCE(excluded.x, exemplaires.x)` — une case vide ne
    touche JAMAIS à un emplacement déjà posé au scanner ou à la main). Même
    règle pour `emplacement_local_id`, résolu depuis le NOM de la colonne
    « Emplacement local » via `obtenir_ou_creer_emplacement_rangement`
    (comparaison insensible casse/espaces ; un nom inconnu est CRÉÉ à la
    volée, tolérant — jamais bloquant — et signalé dans le rapport). Un cache
    évite d'appeler cette résolution plusieurs fois pour un même nom répété
    sur de nombreuses boîtes d'un même import.

    Args:
        chemin: chemin du CSV.
        dry_run: si True, ne touche pas la base (analyse + rapport seulement) —
            aucun emplacement n'est donc créé non plus.
        max_lignes: plafond de lignes (voir `lire_csv`), vérifié AVANT toute
            écriture en base. L'administration passe `MAX_LIGNES_CATALOGUE` ;
            la ligne de commande n'en passe pas — elle tourne dans son propre
            processus, sans prêt concurrent à retarder.

    Returns:
        dict de synthèse (compteurs, regroupements multi-exemplaires, lignes
        ignorées, données titres pour le rapport, la liste des noms
        d'emplacements locaux créés à la volée, et le nombre de lignes dont
        les lettres de classement ont été refusées).
    """
    lignes, index = lire_csv(chemin, max_lignes=max_lignes)
    # L'état de la base est lu dans les DEUX modes : la simulation doit
    # rattacher chaque ligne au même titre que l'import réel. Une simulation
    # sur une installation neuve ne crée pas le fichier de base pour autant.
    base = EtatBase()
    if not dry_run or _db.get_database_path().exists():
        conn = get_connection()
        try:
            base = lire_etat_base(conn)
        finally:
            conn.close()
    (exemplaires, titres, groupes, ignores, lettres_refusees,
     bgg_refusees, categories_nouvelles) = construire_donnees(lignes, index, base)
    defaites = corrections_defaites(titres, exemplaires, base)
    emplacements_locaux_crees: list[str] = []

    if not dry_run:
        conn = get_connection()
        try:
            init_db(conn)  # garantit que les tables existent
            # UPSERT des titres (clé : reference_titre) — paramètres nommés.
            # La catégorie n'y est pas : elle s'écrit juste après, par le seul
            # chemin d'écriture de la liaison et de sa copie.
            conn.executemany(
                """
                INSERT INTO titres (reference_titre, nom, type_jeu,
                    nb_joueurs_min, nb_joueurs_max, duree_min, age_min,
                    editeur, auteur, annee_edition, descriptif, date_achat,
                    lettre_public, lettre_jeu, lettre_materiel, bgg_id)
                VALUES (:reference_titre, :nom, :type_jeu,
                    :nb_joueurs_min, :nb_joueurs_max, :duree_min, :age_min,
                    :editeur, :auteur, :annee_edition, :descriptif, :date_achat,
                    :lettre_public, :lettre_jeu, :lettre_materiel, :bgg_id)
                ON CONFLICT(reference_titre) DO UPDATE SET
                    nom=excluded.nom, type_jeu=excluded.type_jeu,
                    nb_joueurs_min=excluded.nb_joueurs_min,
                    nb_joueurs_max=excluded.nb_joueurs_max,
                    duree_min=excluded.duree_min, age_min=excluded.age_min,
                    editeur=excluded.editeur, auteur=excluded.auteur,
                    annee_edition=excluded.annee_edition,
                    descriptif=excluded.descriptif,
                    date_achat=excluded.date_achat,
                    -- EXCEPTION VOULUE à la règle des titres, qui écrase : une
                    -- case vide, une valeur invalide ou une colonne absente ne
                    -- touchent JAMAIS aux lettres déjà en base, comme les
                    -- colonnes de rangement. Les lettres viendront de la
                    -- classification automatique, pas du tableur du bureau :
                    -- avec la règle générale, le premier ré-import d'un vieux
                    -- tableur effacerait d'un coup tout ce travail. Le triplet
                    -- est complet ou absent (construire_donnees), les trois
                    -- COALESCE basculent donc ensemble.
                    lettre_public=COALESCE(excluded.lettre_public,
                                           titres.lettre_public),
                    lettre_jeu=COALESCE(excluded.lettre_jeu, titres.lettre_jeu),
                    lettre_materiel=COALESCE(excluded.lettre_materiel,
                                             titres.lettre_materiel),
                    -- Même exception pour la fiche BoardGameGeek : elle se
                    -- saisit à l'écran, un vieux fichier ne l'efface pas.
                    bgg_id=COALESCE(excluded.bgg_id, titres.bgg_id),
                    -- Un jeu relié n'est jamais « pas sur BGG » (contrainte de
                    -- `titres.bgg_absent`) : l'identifiant, du fichier ou de
                    -- la base, retire la marque. Les expressions d'un UPDATE
                    -- lisent toutes la ligne d'AVANT.
                    bgg_absent=CASE
                        WHEN COALESCE(excluded.bgg_id, titres.bgg_id) IS NOT NULL THEN 0
                        ELSE titres.bgg_absent END
                """,
                list(titres.values()),
            )
            # Catégories : la case du fichier ÉCRASE celles du titre, comme
            # avant (case vide comprise), dans l'ordre des titres du fichier —
            # celui où `construire_donnees` a retenu les graphies nouvelles :
            # les catégories créées ici sont donc exactement celles que la
            # simulation annonce. Purge des orphelines une seule fois, à la fin.
            categories_creees: list[str] = []
            for ref, champs in titres.items():
                categories_creees += ecrire_categories_titre(
                    conn, ref, champs["categorie"], purger=False)
            purger_categories_orphelines(conn)
            categories_nouvelles = categories_creees
            # « pas sur BGG » dans le fichier : la marque, sauf sur un jeu que
            # la base relie déjà (une liaison faite à l'écran après l'export
            # est plus récente que le fichier). Une case vide n'y touche pas.
            conn.executemany(
                "UPDATE titres SET bgg_absent = 1 "
                "WHERE reference_titre = ? AND bgg_id IS NULL",
                [(ref,) for ref, t in titres.items() if t.get("bgg_absent")],
            )
            # Une correction faite à l'écran, écrasée par ce fichier : la
            # fiche n'a plus rien à protéger, le tableur fait de nouveau foi.
            # `modifie_le` repart à vide — sinon chaque ré-import du même
            # fichier redirait le même écart, et le bureau apprendrait à ne
            # plus lire l'avertissement (arbitrage du lot fiche-1).
            conn.executemany(
                "UPDATE titres SET modifie_le = NULL WHERE reference_titre = ?",
                [(f["reference_titre"],) for f in defaites if f["ecrase"]],
            )

            # Résolution des emplacements locaux (nom -> id), création
            # tolérante des noms inconnus (§4.b).
            cache_local: dict[str, int | None] = {}
            lignes_exemplaires = []
            for ex in exemplaires:
                id_local = None
                nom_local = ex["emplacement_local_nom"]
                if nom_local:
                    cle = nom_local.strip().lower()
                    if cle not in cache_local:
                        resultat = obtenir_ou_creer_emplacement_rangement(conn, nom_local)
                        if resultat is not None:
                            id_emp, cree = resultat
                            cache_local[cle] = id_emp
                            if cree:
                                emplacements_locaux_crees.append(nom_local.strip())
                        else:
                            cache_local[cle] = None
                    id_local = cache_local[cle]
                lignes_exemplaires.append({
                    "id_exemplaire": ex["id_exemplaire"],
                    "reference_titre": ex["reference_titre"],
                    "emplacement_evenement": ex["emplacement_evenement"],
                    "emplacement_local_id": id_local,
                })

            conn.executemany(
                """
                INSERT INTO exemplaires (id_exemplaire, reference_titre,
                    emplacement_evenement, emplacement_local_id)
                VALUES (:id_exemplaire, :reference_titre,
                    :emplacement_evenement, :emplacement_local_id)
                ON CONFLICT(id_exemplaire) DO UPDATE SET
                    reference_titre=excluded.reference_titre,
                    emplacement_evenement=COALESCE(excluded.emplacement_evenement,
                                                    exemplaires.emplacement_evenement),
                    emplacement_local_id=COALESCE(excluded.emplacement_local_id,
                                                   exemplaires.emplacement_local_id)
                """,
                lignes_exemplaires,
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "exemplaires": len(exemplaires),
        "titres": len(titres),
        "groupes_multi": {r: ids for r, ids in groupes.items() if len(ids) > 1},
        "ignores": ignores,
        "titres_data": titres,
        "emplacements_locaux_crees": emplacements_locaux_crees,
        # Catégories de jeu absentes de la base au début de l'import, créées
        # (ou, en simulation, à créer), graphie de leur première occurrence.
        "categories_nouvelles": categories_nouvelles,
        "lettres_refusees": lettres_refusees,
        "bgg_refusees": bgg_refusees,
        # Fiches corrigées à l'écran que ce fichier défait (voir
        # `corrections_defaites`), relevées avant l'écriture.
        "corrections_defaites": defaites,
        # True : le fichier n'était pas de l'UTF-8, il a été lu comme un CSV
        # d'Excel. À dire à l'utilisateur (voir `detecter_encodage`).
        "encodage_repli": detecter_encodage(chemin) == ENCODAGE_EXCEL,
    }


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------
def afficher_rapport(res: dict, montrer_groupes: bool) -> None:
    """
    Affiche un récapitulatif lisible de l'import sur la sortie standard.

    Compteurs (exemplaires/titres), lignes ignorées, taux de remplissage de
    chaque colonne optionnelle, et — si `montrer_groupes` — la liste des titres
    présents en plusieurs exemplaires (utile pour vérifier les regroupements).
    """
    print(f"Exemplaires : {res['exemplaires']}")
    print(f"Titres      : {res['titres']}")
    multi = res["groupes_multi"]
    print(f"Titres en plusieurs exemplaires : {len(multi)}")
    if res["ignores"]:
        print(f"Lignes ignorées : {len(res['ignores'])} -> {res['ignores'][:10]}")
    if res.get("emplacements_locaux_crees"):
        noms = res["emplacements_locaux_crees"]
        print(f"Emplacements locaux créés : {len(noms)} -> {noms}")
    if res.get("categories_nouvelles"):
        noms = res["categories_nouvelles"]
        print(f"Nouvelles catégories : {len(noms)} -> {', '.join(noms)}")
    if res.get("encodage_repli"):
        print("Encodage : le fichier n'est pas en UTF-8, il a été lu comme un "
              "CSV enregistré par Excel. Vérifier les accents dans le catalogue.")
    if res.get("lettres_refusees"):
        print(f"Lettres de classement non reconnues : {res['lettres_refusees']} "
              "ligne(s), importées sans leurs lettres (attendu tant que les "
              "listes de lettres ne sont pas complètes)")
    if res.get("bgg_refusees"):
        print(f"Fiches BoardGameGeek non reconnues : {res['bgg_refusees']} "
              "ligne(s), importées sans leur fiche BoardGameGeek")
    for fiche in res.get("corrections_defaites") or []:
        print(f"Correction faite à l'écran défaite : {fiche['nom']} "
              f"[{fiche['reference_titre']}]")
        for libelle, avant, fichier in fiche["ecarts"]:
            print(f"    {libelle} : {avant!r} -> {fichier!r}")

    # Taux de remplissage par colonne optionnelle
    champs = ["categorie", "nb_joueurs_min", "duree_min", "age_min",
              "editeur", "auteur", "annee_edition", "descriptif"]
    total = max(res["titres"], 1)
    print("\nRemplissage des colonnes (au niveau titre) :")
    for c in champs:
        n = sum(1 for t in res["titres_data"].values() if t.get(c) not in (None, ""))
        print(f"  {c:16} {n:4}/{total}")

    if montrer_groupes:
        print(f"\n=== Regroupements (titres à plusieurs exemplaires) ===")
        for ref, ids in sorted(multi.items()):
            nom = res["titres_data"][ref]["nom"]
            print(f"  {nom!r:40} [{ref}] : {len(ids)} exemplaires -> {ids}")


def main() -> None:
    """Point d'entrée CLI : analyse les arguments, lance l'import, affiche le rapport."""
    p = argparse.ArgumentParser(description="Import du catalogue de jeux depuis un CSV.")
    p.add_argument("csv", type=Path, help="Chemin du fichier CSV.")
    p.add_argument("--dry-run", action="store_true", help="N'écrit rien en base.")
    p.add_argument("--groupes", action="store_true",
                   help="Affiche la liste des regroupements par titre.")
    args = p.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"Fichier introuvable : {args.csv}")

    res = importer(args.csv, dry_run=args.dry_run)
    mode = "DRY-RUN (aucune écriture)" if args.dry_run else "importé en base"
    print(f"--- Catalogue {mode} ---")
    afficher_rapport(res, montrer_groupes=args.groupes)


if __name__ == "__main__":
    main()
