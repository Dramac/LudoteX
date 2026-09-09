"""
GARDE-FOU DE NEUTRALISATION — aucun nom banni dans un fichier suivi par git.

LudoteX est un produit sans marque d'association : le nom de l'association qui
l'exploite est une donnée de configuration, et ne doit exister que dans le
`.env` de l'instance (CLAUDE.md, « Ouverture publique », point 2).

CE TEST EXISTE PARCE QUE LE CONTRÔLE MANUEL A ÉCHOUÉ DEUX FOIS. Le contrôle
d'origine — un `git grep` du nom complet — est aveugle à toute découpe :

1. `deploy/install.sh` portait le nom réparti sur DEUX lignes `echo`
   successives, la fin du nom seule sur la seconde ;
2. les trois exports `.ics` portaient le nom SOUDÉ, sans aucun espace, dans
   le domaine de leurs UID.

Les deux formes ont traversé six lots de neutralisation et ont été publiées.

MÉTHODE. On ne cherche pas la chaîne, on cherche la SUITE DE SES MOTS séparés
par n'importe quoi, jusqu'à 40 caractères entre deux mots, sauts de ligne
compris. C'est ce qui attrape les deux cas ci-dessus, là où un mot isolé serait
inexploitable (« manche » trouve « dimanche » dans 27 fichiers).

⚠️ LE NOM N'EST PAS ÉCRIT ICI. Il est lu dans `interne/noms-bannis.txt`, hors
dépôt : l'écrire dans ce fichier reviendrait à commettre la faute qu'il
surveille. Sans ce fichier — sur un clone public, en intégration continue — le
test est IGNORÉ plutôt qu'en échec.

⚠️ VAUT AUSSI POUR LE LOT 7. `git filter-repo --replace-text` travaille ligne
par ligne : nourri du seul nom complet, il laisserait les deux formes
ci-dessus dans l'historique réécrit, définitivement, après un `push --force`
qui ne se rejoue pas. Sa liste de remplacement doit porter chaque variante.
"""

import re
import subprocess
from pathlib import Path

import pytest


RACINE = Path(__file__).resolve().parent.parent
FICHIER_NOMS = RACINE / "interne" / "noms-bannis.txt"

# Bruit toléré entre deux mots du nom. 40 caractères couvrent largement une fin
# de ligne, un préfixe de commentaire ou un `echo "#  ` de bannière.
ECART_MAXIMAL = 40


def _noms_bannis():
    if not FICHIER_NOMS.exists():
        return []
    lignes = FICHIER_NOMS.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lignes if l.strip() and not l.startswith("#")]


def _motif(nom):
    mots = re.findall(r"[^\W\d_]+", nom, re.UNICODE)
    assert mots, f"nom banni sans aucun mot : {nom!r}"
    return re.compile(
        ("." + "{0,%d}" % ECART_MAXIMAL).join(re.escape(m) for m in mots),
        re.IGNORECASE | re.DOTALL | re.UNICODE,
    )


def _fichiers_suivis():
    sortie = subprocess.run(
        ["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, check=True
    )
    return sortie.stdout.split()


def test_aucun_nom_banni_dans_un_fichier_suivi():
    noms = _noms_bannis()
    if not noms:
        pytest.skip(
            "interne/noms-bannis.txt absent ou vide : rien à surveiller ici "
            "(comportement attendu sur un clone public)"
        )

    motifs = [(nom, _motif(nom)) for nom in noms]
    trouvailles = []
    for chemin in _fichiers_suivis():
        fichier = RACINE / chemin
        try:
            contenu = fichier.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binaire ou illisible : hors de portée d'une recherche de texte
        for nom, motif in motifs:
            for trouve in motif.finditer(contenu):
                ligne = contenu[: trouve.start()].count("\n") + 1
                trouvailles.append(f"{chemin}:{ligne}")

    assert not trouvailles, (
        "nom banni présent dans le dépôt suivi — il ne doit vivre que dans le "
        ".env de l'instance :\n  " + "\n  ".join(trouvailles)
    )


def test_le_motif_attrape_les_deux_formes_qui_ont_echappe_au_controle():
    """
    Vérifie la MÉTHODE elle-même, sur les deux formes réellement publiées,
    reconstituées ici sans écrire aucun nom : sans cette assertion, une
    régression du motif rendrait le test précédent vert et inutile.
    """
    motif = _motif("Alpha Bravo Charlie")

    coupe_par_une_fin_de_ligne = 'echo "#  Alpha Bravo    #"\necho "#  Charlie  #"'
    soude_sans_espaces = "UID:planning-12-20260815T080000Z@alphabravocharlie"

    assert motif.search(coupe_par_une_fin_de_ligne)
    assert motif.search(soude_sans_espaces)
    # Et le contrôle d'origine, lui, ne trouve ni l'une ni l'autre :
    for texte in (coupe_par_une_fin_de_ligne, soude_sans_espaces):
        assert "alpha bravo charlie" not in texte.lower()
