"""
GARDE-FOU du jeu de données d'exemple — `exemples/catalogue-exemple.csv`.

Le fichier sert à évaluer LudoteX sur autre chose qu'une base vide : on
l'importe juste après l'installation pour voir le catalogue, les filtres, une
fiche et une étiquette avec de vraies données. **Un fichier d'exemple qui
échoue à l'import est pire que pas d'exemple du tout** — d'où ce test, qui
l'importe réellement dans une base jetable à chaque exécution de la suite.

Ce qui est vérifié, et pourquoi :

- les en-têtes sont EXACTEMENT ceux de l'export du catalogue
  (`services.EN_TETES_CATALOGUE`) : c'est ce qui garantit que l'exemple reste
  lisible par `scripts/import_csv.py` si les intitulés évoluent d'un côté ;
- l'import passe sans ligne ignorée, et les identifiants d'exemplaire gardent
  leurs zéros de tête (ils sont encodés dans les QR : `0001` n'est pas `1`) ;
- les colonnes optionnelles sont renseignées sur TOUS les titres — un exemple
  à moitié vide ne montrerait ni les filtres par âge, par catégorie et par
  nombre de joueurs, ni une fiche de jeu complète ;
- plusieurs exemplaires partagent un même titre : c'est le regroupement par
  `reference_titre`, sans lequel les statistiques par titre n'ont rien à
  montrer ;
- l'import est idempotent : le relancer ne duplique rien.

Aucun compte en dur : tout est recompté depuis le CSV lui-même, pour qu'ajouter
un jeu à l'exemple ne casse pas ce test.
"""

import csv
from pathlib import Path

import pytest

from app import services
from scripts import import_csv

CHEMIN_EXEMPLE = Path(__file__).resolve().parent.parent / "exemples" / "catalogue-exemple.csv"


def _lignes_csv() -> list[dict]:
    with CHEMIN_EXEMPLE.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


@pytest.fixture
def base_jetable(tmp_path, monkeypatch):
    """Pointe `DATABASE_PATH` vers une base neuve, propre à ce test."""
    chemin = tmp_path / "exemple.db"
    monkeypatch.setenv("DATABASE_PATH", str(chemin))
    return chemin


def test_le_fichier_dexemple_existe():
    assert CHEMIN_EXEMPLE.is_file(), (
        "exemples/catalogue-exemple.csv est cité par le README comme point de "
        "départ : il doit exister dans le dépôt."
    )


def test_les_entetes_sont_ceux_de_lexport_du_catalogue():
    with CHEMIN_EXEMPLE.open(encoding="utf-8-sig", newline="") as f:
        entetes = next(csv.reader(f, delimiter=";"))
    assert entetes == services.EN_TETES_CATALOGUE


def test_limport_reussit_sans_ligne_ignoree(base_jetable):
    resultat = import_csv.importer(CHEMIN_EXEMPLE)
    lignes = _lignes_csv()

    assert resultat["ignores"] == []
    assert resultat["exemplaires"] == len(lignes)
    assert resultat["titres"] == len({services.slug_titre(l["Nom jeu"]) for l in lignes})


def test_les_identifiants_gardent_leurs_zeros_de_tete(base_jetable):
    """Un `id_exemplaire` est encodé dans un QR : `0001` ne doit pas devenir `1`."""
    import_csv.importer(CHEMIN_EXEMPLE)

    conn = import_csv.get_connection()
    try:
        en_base = {ligne["id_exemplaire"] for ligne in
                   conn.execute("SELECT id_exemplaire FROM exemplaires")}
    finally:
        conn.close()

    assert en_base == {l["Code jeu"] for l in _lignes_csv()}


def test_tous_les_titres_sont_renseignes(base_jetable):
    """Un exemple à moitié vide ne montrerait ni les filtres ni une fiche complète."""
    import_csv.importer(CHEMIN_EXEMPLE)

    conn = import_csv.get_connection()
    try:
        titres = conn.execute(
            "SELECT nom, categorie, age_min, nb_joueurs_min, nb_joueurs_max, "
            "duree_min, editeur, auteur, annee_edition, descriptif FROM titres"
        ).fetchall()
    finally:
        conn.close()

    incomplets = [
        f"{t['nom']} ({colonne})"
        for t in titres
        for colonne in t.keys()
        if t[colonne] in (None, "")
    ]
    assert not incomplets, "champ(s) vide(s) dans le catalogue d'exemple : " + \
        ", ".join(sorted(incomplets))


def test_plusieurs_exemplaires_partagent_un_titre(base_jetable):
    """Sans regroupement, les statistiques par titre n'ont rien à montrer."""
    resultat = import_csv.importer(CHEMIN_EXEMPLE)
    assert len(resultat["groupes_multi"]) >= 2


def test_limport_est_idempotent(base_jetable):
    """Relancer l'import ne doit rien dupliquer (UPSERT sur les deux clés)."""
    premier = import_csv.importer(CHEMIN_EXEMPLE)
    second = import_csv.importer(CHEMIN_EXEMPLE)

    assert (second["exemplaires"], second["titres"]) == \
        (premier["exemplaires"], premier["titres"])

    conn = import_csv.get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
    finally:
        conn.close()
    assert total == premier["exemplaires"]
