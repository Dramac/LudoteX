"""
GARDE-FOU des renvois vers `docs/specification.md` (lot-18-pré-production).

Le code et la documentation citent la spécification PAR NUMÉRO DE SECTION
(« voir docs/specification.md §5.1, §6, §8 », « spec §3.1 »…). Une
renumérotation casserait ces renvois sans qu'aucun autre test ne bronche — et un
renvoi qui tombe sur une section existante mais parle d'autre chose est pire
qu'un renvoi cassé, parce qu'on le croit. C'est arrivé : trois renvois
annonçaient « ne jamais bloquer » au §6, qui traite des numéros de pochette ; la
règle vit au §2.

Trois contrôles :

1. **Chaque renvoi vers la spécification désigne une section qui existe.**
2. **Chaque numéro cité figure dans `SUJETS`, avec un fragment de son titre**,
   et le titre de la section porte bien ce fragment. Une section renumérotée ou
   retitrée fait donc échouer le test, et un renvoi vers une section jamais
   citée jusque-là oblige à l'inscrire ici — c'est-à-dire à aller lire la
   section pour vérifier qu'elle parle de ce que le renvoi annonce.
3. **Mémoire de l'erreur trouvée** : un renvoi écrit sur une ligne qui parle de
   « bloquer » doit viser le §2.

Les renvois internes de la spécification (« voir §7 ») sont soumis au contrôle
1. Le wiki est un dépôt git séparé, non couvert (même motif que
`tests/test_liens_documentation.py`).

COMMENT UN RENVOI EST RECONNU. On repère les mentions de document
(`quelque-chose.md`, ainsi que « spec » et « spécification » écrits seuls) et
les « §N » ; chaque « §N » est rattaché à la mention qui le précède de près, sans
fin de phrase entre les deux. « docs/conception-journal.md §8 » vise donc la
note du journal, pas la spécification, même écrit dans la spécification
elle-même.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SPECIFICATION = RACINE / "docs" / "specification.md"
CHEMIN_SPEC = "docs/specification.md"

# Numéro cité -> fragment (minuscules) que le titre de la section doit porter.
SUJETS = {
    "2": "principes",
    "3": "modèle de données",
    "3.1": "deux clés",
    "3.2": "tables",
    "5": "écrans",
    "5.1": "prêt / retour",
    "5.2": "catalogue",
    "6": "pochette",
    "7": "statistiques",
    "8": "accès",
}

# Une mention de document : un fichier .md, ou « spec » / « spécification »
# employés seuls (« la spécification URL » n'est suivie d'aucun §, elle ne
# produit donc aucun renvoi).
MENTION = re.compile(
    r"[\w./-]+\.md|\bspec\b|\bspécification\b", re.IGNORECASE
)
RENVOI = re.compile(r"§\s?(\d+(?:\.\d+)?)")
TITRE = re.compile(r"^#{2,3}\s+(\d+(?:\.\d+)?)\.?\s+(.*)$", re.MULTILINE)
# Entre une mention et son renvoi : pas de fin de phrase ni de parenthèse, et
# pas plus de cette distance. La parenthèse ouvrante compte : dans
# « `wiki/Rgpd.md` (voir §9) », le § vise la spécification, pas le wiki.
DISTANCE_MAX = 40
COUPURE = re.compile(r"[.;:!?]\s|[()]")

EXTENSIONS_IGNOREES = {
    ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".ttf", ".db", ".zip",
    ".pdf", ".svg",
}


def _sections() -> dict[str, str]:
    texte = SPECIFICATION.read_text(encoding="utf-8")
    return {num: titre.strip().lower() for num, titre in TITRE.findall(texte)}


def _renvois(texte: str, fichier: str) -> list[tuple[str, int, str]]:
    """Renvois vers la spécification : (numéro, ligne, texte de la ligne)."""
    mentions = list(MENTION.finditer(texte))
    lignes = texte.splitlines()
    trouves = []
    for renvoi in RENVOI.finditer(texte):
        precedentes = [m for m in mentions if m.end() <= renvoi.start()]
        cible = None
        if precedentes:
            m = precedentes[-1]
            entre = texte[m.end():renvoi.start()]
            if len(entre) <= DISTANCE_MAX and not COUPURE.search(entre):
                cible = m.group(0)
        if cible is None:
            # Sans mention proche, un « § » ne vise la spécification que s'il
            # est écrit dans la spécification elle-même.
            if fichier != CHEMIN_SPEC:
                continue
        elif not (cible.endswith(CHEMIN_SPEC) or cible == "specification.md"
                  or cible.lower() in ("spec", "spécification")):
            continue
        numero_ligne = texte.count("\n", 0, renvoi.start()) + 1
        trouves.append((renvoi.group(1), numero_ligne, lignes[numero_ligne - 1]))
    return trouves


def _fichiers_suivis() -> list[str]:
    resultat = subprocess.run(
        ["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, check=True,
    )
    return [ligne for ligne in resultat.stdout.splitlines() if ligne.strip()]


def _tous_les_renvois() -> list[tuple[str, str, int, str]]:
    fichiers = set(_fichiers_suivis()) | {CHEMIN_SPEC}
    tous = []
    for chemin in sorted(fichiers):
        fichier = RACINE / chemin
        if fichier.suffix.lower() in EXTENSIONS_IGNOREES or not fichier.is_file():
            continue
        try:
            texte = fichier.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for numero, ligne, contenu in _renvois(texte, chemin):
            tous.append((chemin, numero, ligne, contenu))
    return tous


def test_la_detection_reconnait_les_formes_connues():
    """La recherche se vérifie sur des occurrences connues avant qu'on se fie à
    son résultat (enseignement du chantier) : chaque forme employée dans le
    dépôt, et les deux pièges du rattachement."""
    exemples = {
        "voir docs/specification.md §5.1, §6, §8": ["5.1", "6", "8"],
        "(voir spec §3.1) :": ["3.1"],
        "la règle de la\n    spécification §6, et": ["6"],
        "`docs/specification.md` §2 (« ne jamais bloquer »),\n"
        "`docs/conception-rangement.md` §6/§9": ["2"],
        "voir `docs/conception-journal.md` §8": [],
        "la spécification URL normalise": [],
    }
    for texte, attendus in exemples.items():
        assert [n for n, _, _ in _renvois(texte, "app/x.py")] == attendus, texte
    # Dans la spécification : un « § » isolé la vise, un « § » qui suit une
    # autre note vise cette note, même par-delà un saut de ligne.
    interne = ("(§3.4), voir (`docs/conception-transfert-pochette.md`\n§6 bis)"
               " ; recensés dans `wiki/Rgpd.md` (voir §9)")
    assert [n for n, _, _ in _renvois(interne, CHEMIN_SPEC)] == ["3.4", "9"]


def test_le_depot_cite_bien_la_specification_par_section():
    """Sans renvoi trouvé, les contrôles suivants seraient verts pour rien."""
    hors_spec = [r for r in _tous_les_renvois() if r[0] != CHEMIN_SPEC]
    assert len(hors_spec) >= 20, hors_spec
    assert any(c == "app/routes/pret.py" and n == "5.1" for c, n, _, _ in hors_spec)


def test_chaque_renvoi_vise_une_section_existante():
    sections = _sections()
    casses = [f"{c}:{l} -> §{n}" for c, n, l, _ in _tous_les_renvois()
              if n not in sections]
    assert not casses, (
        "renvoi(s) vers une section absente de docs/specification.md :\n"
        + "\n".join(casses)
    )


def test_chaque_section_citee_porte_le_sujet_attendu():
    sections = _sections()
    non_inscrits = sorted({
        f"§{n} (cité par {c}:{l})" for c, n, l, _ in _tous_les_renvois()
        if c != CHEMIN_SPEC and n not in SUJETS
    })
    assert not non_inscrits, (
        "section(s) citée(s) hors de la spécification mais absente(s) de SUJETS "
        "— lire la section, vérifier qu'elle parle de ce que le renvoi annonce, "
        "puis l'inscrire :\n" + "\n".join(non_inscrits)
    )
    faux = [f"§{n} : titre « {sections.get(n)} », attendu « {fragment} »"
            for n, fragment in SUJETS.items()
            if fragment not in sections.get(n, "")]
    assert not faux, (
        "section(s) renumérotée(s) ou retitrée(s) alors que le code les cite :\n"
        + "\n".join(faux)
    )


def test_ne_jamais_bloquer_renvoie_aux_principes():
    faux = [f"{c}:{l} -> §{n}" for c, n, l, contenu in _tous_les_renvois()
            if c != CHEMIN_SPEC and "bloqu" in contenu.lower() and n != "2"]
    assert not faux, (
        "« ne jamais bloquer » est le principe n° 2 de la spécification (§2) :\n"
        + "\n".join(faux)
    )
