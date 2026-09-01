"""
GARDE-FOU contre les liens morts vers `docs/` — né du lot 5 (tri de `docs/`,
ouverture publique de LudoteX).

Le défaut qui a motivé ce test : `docs/conception-journal.md` était cité par
49 fichiers du dépôt sans être lui-même versionné — un document que personne
d'autre que Simon ne possédait. Le lot 5 a corrigé ce cas précis, mais rien
n'empêchait qu'un déplacement ultérieur (le lot 5 lui-même en a fait une
douzaine, vers `interne/`, hors Git) en recrée un autre sans que personne ne
le remarque avant qu'un tiers clone le dépôt.

Ce test vérifie que tout chemin `docs/xxx.md` cité par un fichier SUIVI par
git correspond à un fichier qui existe réellement dans `docs/`. Portée
volontairement étroite :

- seuls les fichiers **suivis par git** sont examinés (`git ls-files`) —
  `interne/` est hors Git par construction, ce n'est pas son problème ;
- seuls les chemins de la forme `docs/....md` sont reconnus — un renvoi en
  langage naturel («la conception du module ») ne peut pas se détecter ainsi,
  et ce n'est pas ce qui a fait défaut au lot 5 ;
- le wiki est un dépôt git SÉPARÉ, non couvert ici.
"""

import re
import subprocess
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
MOTIF_CHEMIN_DOCS = re.compile(r"docs/[A-Za-z0-9_\-]+\.md")

# Extensions dans lesquelles une correspondance n'a aucune chance d'être du
# texte lisible (binaire, ou générateur d'images) : on ne les ouvre même pas.
EXTENSIONS_IGNOREES = {
    ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".ttf", ".db", ".zip",
}


def _fichiers_suivis():
    resultat = subprocess.run(
        ["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, check=True,
    )
    return [ligne for ligne in resultat.stdout.splitlines() if ligne.strip()]


def test_aucun_fichier_suivi_ne_cite_un_chemin_docs_inexistant():
    manquants = []
    for chemin in _fichiers_suivis():
        fichier = RACINE / chemin
        if fichier.suffix.lower() in EXTENSIONS_IGNOREES or not fichier.is_file():
            continue
        try:
            texte = fichier.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for cible in set(MOTIF_CHEMIN_DOCS.findall(texte)):
            if not (RACINE / cible).is_file():
                manquants.append(f"{chemin} -> {cible}")

    assert not manquants, (
        "chemin(s) docs/... cité(s) par un fichier suivi mais absent(s) du "
        "dépôt :\n" + "\n".join(sorted(manquants))
    )
