"""
Garde-fous du script de captures du wiki (`scripts/captures_wiki.py`).

Aucun test ici ne lance de navigateur : la suite doit tourner sans
Playwright, sur un clone neuf. Le texte alternatif d'une capture a un seul
domicile, le script ; le wiki en porte une copie, que ces tests verrouillent.
Le wiki est un dépôt séparé : les tests qui le lisent sont ignorés s'il
n'est pas là.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from scripts import captures_wiki as cw

RACINE = Path(__file__).resolve().parent.parent
WIKI = RACINE / "wiki"
IMAGE_WIKI = re.compile(r"!\[([^\]]*)\]\(images/([^)\s]+)\)")

avec_wiki = pytest.mark.skipif(not WIKI.is_dir(), reason="dépôt wiki absent")


def _slug_page(page: str) -> str:
    sans = unicodedata.normalize("NFKD", page)
    return "".join(c for c in sans if not unicodedata.combining(c)).lower()


def _images_du_wiki() -> list[tuple[str, str, str]]:
    """(page, texte alternatif, fichier) de chaque image citée par le wiki."""
    trouvees = []
    for page in sorted(WIKI.glob("*.md")):
        for alt, fichier in IMAGE_WIKI.findall(page.read_text(encoding="utf-8")):
            trouvees.append((page.stem, alt, fichier))
    return trouvees


# ---------------------------------------------------------------------------
# La liste, sans navigateur
# ---------------------------------------------------------------------------
def test_la_liste_se_charge_sans_playwright():
    """Importer le script ne charge pas Playwright : l'import est différé."""
    code = ("import sys; import scripts.captures_wiki as cw; "
            "assert cw.CAPTURES; assert 'playwright' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], cwd=RACINE, check=True)


def test_option_liste_ne_lance_rien(capsys):
    cw.main(["--liste"])
    sortie = capsys.readouterr().out
    for capture in cw.CAPTURES:
        assert capture.nom in sortie


def test_noms_uniques_et_conformes_a_la_convention():
    noms = [c.nom for c in cw.CAPTURES]
    assert len(noms) == len(set(noms))
    for c in cw.CAPTURES:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", c.nom), c.nom
        assert c.nom.startswith(_slug_page(c.page) + "-"), c.nom


def test_champs_de_chaque_capture():
    for c in cw.CAPTURES:
        assert c.format in cw.FORMATS, c.nom
        assert c.profil in cw.PROFILS, c.nom
        assert c.priorite in ("rouge", "orange", "jaune"), c.nom
        assert c.alt.strip() and "]" not in c.alt, c.nom
        assert c.url is not None or c.preparation is not None, c.nom


def test_option_seulement_refuse_un_nom_inconnu():
    with pytest.raises(SystemExit):
        cw.main(["--seulement", "capture-qui-n-existe-pas"])


# ---------------------------------------------------------------------------
# L'instance jetable
# ---------------------------------------------------------------------------
def _env(tmp_path) -> dict[str, str]:
    texte = cw.contenu_env(tmp_path, 8123, "jeton-test", "mdp-test")
    return dict(ligne.split("=", 1) for ligne in texte.splitlines() if ligne)


def test_environnement_sans_formation_ni_bgg(tmp_path):
    env = _env(tmp_path)
    assert "MODE_FORMATION" not in env
    assert "BGG_TOKEN" not in env
    assert env["NOM_ASSOCIATION"] == ""


def test_environnement_tous_les_chemins_sous_le_dossier(tmp_path):
    env = _env(tmp_path)
    for cle in ("DATABASE_PATH", "TOURNOI_DATABASE_PATH",
                "PLANNING_DATABASE_PATH", "JOURNAL_PATH", "FORMATION_SOURCE_DB"):
        assert Path(env[cle]).parent == tmp_path, cle
    # La source des noms de jeux n'existe pas : le peuplement prend les noms
    # de secours, les mêmes sur tous les postes.
    assert not Path(env["FORMATION_SOURCE_DB"]).exists()
    assert env["BASE_URL"] == "http://127.0.0.1:8123"


def test_sous_processus_purge_les_cles_heritees(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", "/chemin/de/la/vraie/base.db")
    monkeypatch.setenv("MODE_FORMATION", "1")
    monkeypatch.setenv("BGG_TOKEN", "jeton-de-l-appelant")
    env = cw.environnement_sous_processus(tmp_path / "x.env")
    assert "DATABASE_PATH" not in env
    assert "MODE_FORMATION" not in env
    assert "BGG_TOKEN" not in env
    assert env["LUDOTEX_ENV_FILE"] == str(tmp_path / "x.env")


def test_toute_cle_posee_par_l_instance_est_purgee_de_l_heritage(tmp_path):
    """Une clé de l'instance ne doit jamais pouvoir être écrasée par l'appelant."""
    purgees = cw.cles_instance()
    for cle in _env(tmp_path):
        assert cle in purgees, cle


def test_nom_fictif_refuse_s_il_contient_un_nom_banni(tmp_path):
    bannis = tmp_path / "bannis.txt"
    bannis.write_text("# commentaire\nvaljoli\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        cw.verifier_nom_fictif("Ludothèque de Valjoli-sur-Dés", bannis)
    cw.verifier_nom_fictif("Autre nom", bannis)


def test_nom_fictif_absent_des_noms_bannis():
    if not cw.NOMS_BANNIS.is_file():
        pytest.skip("liste des noms bannis absente (clone public)")
    cw.verifier_nom_fictif()


# ---------------------------------------------------------------------------
# Le wiki : une copie verrouillée
# ---------------------------------------------------------------------------
@avec_wiki
def test_chaque_page_cible_existe():
    for c in cw.CAPTURES:
        assert (WIKI / f"{c.page}.md").is_file(), c.page


@avec_wiki
def test_chaque_capture_est_citee_par_sa_page_avec_le_meme_texte():
    citees = {(page, fichier): alt for page, alt, fichier in _images_du_wiki()}
    for c in cw.CAPTURES:
        cle = (c.page, f"{c.nom}.png")
        assert cle in citees, f"{c.nom} absente de {c.page}.md"
        assert citees[cle] == c.alt, f"texte alternatif divergent : {c.nom}"


@avec_wiki
def test_chaque_image_du_wiki_est_declaree_dans_le_script():
    declarees = {f"{c.nom}.png" for c in cw.CAPTURES}
    for page, _alt, fichier in _images_du_wiki():
        assert fichier in declarees, f"{page}.md cite {fichier}, inconnue du script"


@avec_wiki
def test_aucune_image_orpheline():
    dossier = WIKI / "images"
    if not dossier.is_dir():
        pytest.skip("aucune image dans le wiki")
    citees = {fichier for _p, _a, fichier in _images_du_wiki()}
    for png in dossier.glob("*.png"):
        assert png.name in citees, f"{png.name} n'est citée par aucune page"
