"""
Import / mise à jour du catalogue depuis le CSV de l'association.

Principes (voir docs/specification.md §3 et §5) :
- Tolérant aux colonnes variables : seules deux données sont exigées,
  l'identifiant d'exemplaire (CSV « Code jeu ») et le nom du jeu. Tout le
  reste est optionnel ; l'import remplit ce qu'il trouve.
- Deux clés stables :
    * id_exemplaire  <- « Code jeu » (TEXT, zéros de tête préservés).
    * reference_titre <- slug normalisé du nom -> REGROUPE les exemplaires
      d'un même jeu (ex. les 4 « Dobble » partagent une référence).
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
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

# Permet « python scripts/import_csv.py » comme « python -m scripts.import_csv ».
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db  # noqa: E402
# slug_titre est PARTAGÉ avec l'app (création de jeu via l'admin) pour produire
# exactement les mêmes références de regroupement.
from app.services import slug_titre  # noqa: E402

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
def nettoyer_nom(valeur: str) -> str:
    """Espaces superflus réduits ; le reste est conservé tel quel."""
    return re.sub(r"\s+", " ", (valeur or "").strip())


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
def lire_csv(chemin: Path) -> tuple[list[dict], dict[str, str | None]]:
    """
    Lit le CSV et détecte automatiquement le séparateur.

    `encoding="utf-8-sig"` gère le BOM éventuel (fréquent sur les exports
    Windows/Excel). Le séparateur est deviné par `csv.Sniffer` parmi `;`, `,` et
    tabulation ; à défaut, on retombe sur `;`.

    Args:
        chemin: chemin du fichier CSV.

    Returns:
        (lignes, index_colonnes) où `lignes` est une liste de dicts (une par
        ligne, clés = en-têtes réels) et `index_colonnes` la correspondance
        champ logique → en-tête réel.
    """
    with open(chemin, encoding="utf-8-sig", newline="") as fh:
        echantillon = fh.read(4096)
        fh.seek(0)
        try:
            dialecte = csv.Sniffer().sniff(echantillon, delimiters=";,\t")
            sep = dialecte.delimiter
        except csv.Error:
            sep = ";"  # repli : la liste de l'asso utilise « ; »
        lecteur = csv.DictReader(fh, delimiter=sep)
        lignes = list(lecteur)
        entetes = lecteur.fieldnames or []
    index = construire_index_colonnes(entetes)
    return lignes, index


def construire_donnees(lignes: list[dict], index: dict[str, str | None]):
    """
    Transforme les lignes CSV en :
      - exemplaires : liste de (id_exemplaire, reference_titre)
      - titres      : dict reference_titre -> champs agrégés (1re valeur non vide)
      - groupes     : reference_titre -> liste d'id_exemplaire (pour le rapport)
    Lève une erreur si les colonnes clés (Code jeu, Nom jeu) sont absentes.
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

    exemplaires: list[tuple[str, str]] = []
    titres: dict[str, dict] = {}
    groupes: dict[str, list[str]] = defaultdict(list)
    ignores: list[str] = []
    vus: set[str] = set()

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

        ref = slug_titre(nom)
        exemplaires.append((id_ex, ref))
        groupes[ref].append(id_ex)

        nb_min, nb_max = parse_nb_joueurs(val(ligne, "nb_joueurs"))
        champs = {
            "reference_titre": ref,
            "nom": nom,
            "type_jeu": _ou_none(val(ligne, "type_jeu")),
            "categorie": _ou_none(val(ligne, "categorie")),
            "nb_joueurs_min": nb_min,
            "nb_joueurs_max": nb_max,
            "duree_min": parse_duree(val(ligne, "duree")),
            "age_min": parse_age(val(ligne, "age")),
            "editeur": _ou_none(val(ligne, "editeur")),
            "auteur": _ou_none(val(ligne, "auteur")),
            "annee_edition": parse_annee(val(ligne, "annee")),
            "descriptif": _ou_none(val(ligne, "descriptif")),
            "date_achat": parse_date_achat(val(ligne, "date_achat")),
        }
        # Agrégation au niveau titre : on conserve la 1re valeur non vide
        # rencontrée parmi les exemplaires d'un même titre.
        if ref not in titres:
            titres[ref] = champs
        else:
            for k, v in champs.items():
                if titres[ref].get(k) in (None, "") and v not in (None, ""):
                    titres[ref][k] = v
        # Exception : date_achat = la PLUS RÉCENTE parmi les exemplaires du titre
        # (dernière acquisition). Les dates ISO se comparent comme des chaînes.
        da = champs["date_achat"]
        if da and (titres[ref].get("date_achat") is None or da > titres[ref]["date_achat"]):
            titres[ref]["date_achat"] = da

    return exemplaires, titres, groupes, ignores


# ---------------------------------------------------------------------------
# Écriture en base
# ---------------------------------------------------------------------------
def importer(chemin: Path, dry_run: bool = False) -> dict:
    """
    Importe (ou simule) le catalogue en base.

    Idempotence : les deux INSERT utilisent `ON CONFLICT … DO UPDATE` (UPSERT)
    sur les clés primaires (`reference_titre`, `id_exemplaire`). Relancer
    l'import après mise à jour du CSV met simplement à jour les lignes existantes
    sans créer de doublon. `executemany` fait l'insertion en lot (performant).

    Args:
        chemin: chemin du CSV.
        dry_run: si True, ne touche pas la base (analyse + rapport seulement).

    Returns:
        dict de synthèse (compteurs, regroupements multi-exemplaires, lignes
        ignorées, et données titres pour le rapport).
    """
    lignes, index = lire_csv(chemin)
    exemplaires, titres, groupes, ignores = construire_donnees(lignes, index)

    if not dry_run:
        conn = get_connection()
        try:
            init_db(conn)  # garantit que les tables existent
            # UPSERT des titres (clé : reference_titre) — paramètres nommés.
            conn.executemany(
                """
                INSERT INTO titres (reference_titre, nom, type_jeu, categorie,
                    nb_joueurs_min, nb_joueurs_max, duree_min, age_min,
                    editeur, auteur, annee_edition, descriptif, date_achat)
                VALUES (:reference_titre, :nom, :type_jeu, :categorie,
                    :nb_joueurs_min, :nb_joueurs_max, :duree_min, :age_min,
                    :editeur, :auteur, :annee_edition, :descriptif, :date_achat)
                ON CONFLICT(reference_titre) DO UPDATE SET
                    nom=excluded.nom, type_jeu=excluded.type_jeu,
                    categorie=excluded.categorie,
                    nb_joueurs_min=excluded.nb_joueurs_min,
                    nb_joueurs_max=excluded.nb_joueurs_max,
                    duree_min=excluded.duree_min, age_min=excluded.age_min,
                    editeur=excluded.editeur, auteur=excluded.auteur,
                    annee_edition=excluded.annee_edition,
                    descriptif=excluded.descriptif,
                    date_achat=excluded.date_achat
                """,
                list(titres.values()),
            )
            conn.executemany(
                """
                INSERT INTO exemplaires (id_exemplaire, reference_titre)
                VALUES (?, ?)
                ON CONFLICT(id_exemplaire) DO UPDATE SET
                    reference_titre=excluded.reference_titre
                """,
                exemplaires,
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
