"""
Fiche publique d'un jeu vue par un BÉNÉVOLE : chaque boîte et depuis combien
de temps elle est sortie (retour terrain, version 1.34 ;
`services.exemplaires_du_titre`).

Fixtures sur le modèle de `tests/test_transfert_routes.py` : trois boîtes,
001/Catan, 002/Dixit, 003/Catan.
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
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("001", "CATAN"), ("002", "DIXIT"), ("003", "CATAN")],
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Fiche publique : durée de sortie de chaque boîte, bénévoles seulement
# (même fixture : 001 et 003 sont deux boîtes de Catan)
# ---------------------------------------------------------------------------
def test_fiche_benevole_liste_les_boites_et_leur_duree(client, vieillir_prets):
    from app import db
    client.post("/pret/001/preter")
    conn = db.get_connection()
    try:
        vieillir_prets(conn, secondes=75 * 60)
    finally:
        conn.close()
    page = client.get("/jeu/003").text

    assert "Les boîtes de ce jeu" in page
    assert "<strong>001</strong>" in page and "sortie depuis 1 h 15" in page
    assert "<strong>003</strong> <span class=\"stats-note\">(cette boîte)</span>" in page
    assert "disponible" in page.split("<strong>003</strong>")[1].split("</li>")[0]
    # Jamais le numéro de pochette sur la fiche, même vue d'un bénévole.
    assert "pochette" not in page.split('class="boites-etat"')[1].split("</ul>")[0]


def test_fiche_benevole_tournoi(client):
    client.post("/pret/003/tournoi")
    assert "en tournoi depuis" in client.get("/jeu/001").text


def test_fiche_publique_sans_les_boites(client, monkeypatch):
    client.post("/pret/001/preter")
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    page = client.get("/jeu/001").text
    assert "Catan" in page
    assert "boites-etat" not in page
    assert "sortie depuis" not in page
