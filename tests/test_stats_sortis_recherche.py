"""
Recherche dans « Jeux actuellement sortis » sur /stats (retour terrain,
version 1.34) : nom du jeu ou code de la boîte, sans casse ni accents, et
rien d'autre de la page ne bouge.
"""

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.tournoi import db as tdb
    from app.planning import db as pdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('ECLIPSE', 'Éclipse')")
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("001", "CATAN"), ("002", "DIXIT"), ("003", "CATAN"), ("042", "ECLIPSE")],
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _section_sortis(page):
    return page.split('id="sortis"')[1].split("</section>")[0]


@pytest.fixture
def quatre_sorties(client):
    for code in ("001", "002", "042"):
        client.post(f"/pret/{code}/preter")
    client.post("/pret/003/tournoi")
    return client


def test_sans_recherche_tout_est_la(quatre_sorties):
    section = _section_sortis(quatre_sorties.get("/stats").text)
    for nom in ("Catan", "Dixit", "Éclipse"):
        assert nom in section
    assert "trouvée" not in section


def test_recherche_par_nom_sans_accent_ni_majuscule(quatre_sorties):
    section = _section_sortis(quatre_sorties.get("/stats", params={"q": "ECLI"}).text)
    assert "Éclipse" in section
    assert "Dixit" not in section and "Catan" not in section
    assert "1 boîte trouvée pour « ECLI »," in section
    assert "sur 4 sorties." in section


def test_recherche_par_code_de_boite(quatre_sorties):
    section = _section_sortis(quatre_sorties.get("/stats", params={"q": "42"}).text)
    assert "Éclipse" in section and "Dixit" not in section


def test_recherche_couvre_aussi_les_tournois(quatre_sorties):
    section = _section_sortis(quatre_sorties.get("/stats", params={"q": "catan"}).text)
    # 001 prêté et 003 en tournoi : les deux tableaux.
    assert "<td>001</td>" in section and "<td>003</td>" in section
    assert "2 boîtes trouvées" in section


def test_recherche_sans_resultat(quatre_sorties):
    section = _section_sortis(quatre_sorties.get("/stats", params={"q": "zzz"}).text)
    assert "0 boîte trouvée" in section
    assert "Tout afficher" in section


def test_la_recherche_ne_touche_pas_au_reste_de_la_page(quatre_sorties):
    sans = quatre_sorties.get("/stats").text
    avec = quatre_sorties.get("/stats", params={"q": "zzz"}).text
    assert sans.split('id="sortis"')[0] == avec.split('id="sortis"')[0]


def test_la_periode_est_reportee_dans_le_formulaire(quatre_sorties):
    section = _section_sortis(quatre_sorties.get(
        "/stats", params={"debut": "2026-01-01T00:00", "tri": "exemplaire"}).text)
    assert 'name="debut" value="2026-01-01T00:00"' in section
    assert 'name="tri" value="exemplaire"' in section
