"""
Import du catalogue : un CSV enregistré par Excel s'importe sans geste.

La consigne donnée au bureau est « enregistrez votre classeur au format CSV ».
Sous Windows, Excel l'écrit alors en Windows-1252, pas en UTF-8. L'import ne
lisait que l'UTF-8 : au premier accent — donc dès la ligne d'en-têtes,
« Durée », « Éditeur » — il rendait « 'utf-8' codec can't decode byte 0xe9 »,
une erreur brute là où la règle est de ne jamais bloquer.

L'UTF-8 reste tenté d'abord ; s'il casse, le fichier est lu comme un CSV
d'Excel, et le message de fin d'import le dit.
"""
import sqlite3

import pytest

from scripts import import_csv

MOT_DE_PASSE = "motdepasse-de-test"
CATALOGUE = (
    "Code jeu;Nom jeu;Durée;Éditeur;Année édition\n"
    "0901;Les Aventuriers du Rail;45 mn;Days of Wonder;2004\n"
    "0902;Château Aventure;30 mn;Éditions du Dé;2019\n"
)


@pytest.fixture
def fichier(tmp_path):
    def ecrire(encodage: str):
        chemin = tmp_path / f"catalogue-{encodage}.csv"
        chemin.write_bytes(CATALOGUE.encode(encodage))
        return chemin
    return ecrire


@pytest.mark.parametrize("encodage, attendu", [
    ("utf-8", "utf-8-sig"),
    ("utf-8-sig", "utf-8-sig"),
    ("cp1252", "cp1252"),
    ("utf-16", "utf-16"),
])
def test_l_encodage_est_reconnu(fichier, encodage, attendu):
    assert import_csv.detecter_encodage(fichier(encodage)) == attendu


@pytest.mark.parametrize("encodage", ["utf-8", "utf-8-sig", "cp1252", "utf-16"])
def test_les_en_tetes_accentues_sont_reconnus_quel_que_soit_l_encodage(fichier, encodage):
    lignes, index = import_csv.lire_csv(fichier(encodage))
    assert index["duree"] == "Durée"
    assert index["editeur"] == "Éditeur"
    assert index["annee"] == "Année édition"
    assert [l["Nom jeu"] for l in lignes] == ["Les Aventuriers du Rail", "Château Aventure"]
    assert lignes[1]["Éditeur"] == "Éditions du Dé"


def test_seul_le_repli_est_signale(fichier):
    assert import_csv.importer(fichier("cp1252"), dry_run=True)["encodage_repli"] is True
    assert import_csv.importer(fichier("utf-8"), dry_run=True)["encodage_repli"] is False
    assert import_csv.importer(fichier("utf-16"), dry_run=True)["encodage_repli"] is False


def test_un_fichier_sans_accent_n_est_pas_signale(tmp_path):
    """De l'ASCII pur est de l'UTF-8 valide : rien à dire."""
    chemin = tmp_path / "ascii.csv"
    chemin.write_bytes(b"Code jeu;Nom jeu\n0901;Catan\n")
    assert import_csv.importer(chemin, dry_run=True)["encodage_repli"] is False


def test_un_fichier_illisible_donne_un_message_pas_un_numero_d_octet(tmp_path):
    """Les cinq octets que cp1252 ne définit pas ne font pas lever la lecture."""
    chemin = tmp_path / "bruit.csv"
    chemin.write_bytes(b"\x81\x8d\x8f\x90\x9d;\xff\n\x00\x01;\x02\n")
    with pytest.raises(SystemExit, match="colonnes clés introuvables"):
        import_csv.importer(chemin, dry_run=True)


# ---------------------------------------------------------------------------
# Par l'écran « Données & sauvegarde »
# ---------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Trois bases temporaires, patron de tests/test_envois_bornes.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    client.base = tmp_path / "test.db"
    return client


def _envoyer(client, encodage: str):
    return client.post("/admin/donnees/import", files={
        "fichier": ("catalogue.csv", CATALOGUE.encode(encodage), "text/csv")})


def test_un_csv_d_excel_s_importe_avec_ses_accents_et_le_dit(client):
    r = _envoyer(client, "cp1252")
    assert "Import réussi : 2 exemplaire(s)" in r.text
    assert "lu comme un CSV enregistré par Excel" in r.text
    assert "codec" not in r.text
    conn = sqlite3.connect(client.base)
    try:
        noms = {l[0] for l in conn.execute("SELECT nom FROM titres")}
        editeurs = {l[0] for l in conn.execute("SELECT editeur FROM titres")}
    finally:
        conn.close()
    assert "Château Aventure" in noms
    assert "Éditions du Dé" in editeurs


def test_un_csv_en_utf8_s_importe_sans_remarque(client):
    r = _envoyer(client, "utf-8")
    assert "Import réussi : 2 exemplaire(s)" in r.text
    assert "enregistré par Excel" not in r.text
