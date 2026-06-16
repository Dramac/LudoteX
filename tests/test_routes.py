"""Tests d'intégration des routes (fiche + prêt/retour) via une base temporaire."""

import os

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Base SQLite temporaire isolée par test.
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from app import db

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_catalogue(client):
    r = client.get("/catalogue")
    assert r.status_code == 200
    assert "Catalogue" in r.text and "Catan" in r.text
    assert "1 / 1 dispo" in r.text or "1 jeu" in r.text


def test_catalogue_recherche_par_nom(client):
    r = client.get("/catalogue", params={"q": "cat"})
    assert r.status_code == 200
    assert "Catan" in r.text


def test_catalogue_filtre_joueurs_exclut_hors_bornes(client):
    # Catan (jeu de test) n'a pas de bornes joueurs -> exclu si filtre joueurs actif
    r = client.get("/catalogue", params={"joueurs": "3"})
    assert r.status_code == 200
    assert "Catan" not in r.text


def test_catalogue_redirige_depuis_racine(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers["location"] == "/catalogue"


def test_stats_page(client):
    client.post("/pret/001/preter")          # un prêt pour alimenter les stats
    r = client.get("/stats")
    assert r.status_code == 200
    assert "Statistiques" in r.text
    assert "Catan" in r.text                 # apparaît dans le palmarès
    r2 = client.get("/stats", params={"tri": "exemplaire"})
    assert r2.status_code == 200
    assert "par exemplaire" in r2.text


def test_fiche_publique(client):
    r = client.get("/jeu/001")
    assert r.status_code == 200
    assert "Catan" in r.text
    assert "Disponible" in r.text


def test_fiche_inconnue(client):
    r = client.get("/jeu/999")
    assert r.status_code == 404
    assert "inconnu" in r.text.lower()


def test_auth_protege_pret_et_scanner(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")

    # Sans cookie : écrans bénévole refusés (403), mais public accessible.
    assert client.get("/pret/001").status_code == 403
    assert client.get("/scanner").status_code == 403
    assert client.get("/catalogue").status_code == 200
    assert client.get("/jeu/001").status_code == 200

    # Lien d'activation invalide : pas d'accès.
    assert client.get("/acces", params={"jeton": "mauvais"}).status_code == 403

    # Activation correcte : pose le cookie, puis accès autorisé.
    r = client.get("/acces", params={"jeton": "jeton-test-secret-32-caracteres"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/scanner"
    assert client.get("/scanner").status_code == 200
    assert client.get("/pret/001").status_code == 200


def test_scanner_page(client):
    r = client.get("/scanner")
    assert r.status_code == 200
    assert "<video" in r.text
    assert "/static/js/scanner.js" in r.text
    assert "/static/js/jsQR.js" in r.text   # jsQR hébergé en local
    assert "jsdelivr" not in r.text.lower() and "cdn.jsdelivr" not in r.text.lower()


def test_cycle_preter_puis_rendre(client):
    r = client.post("/pret/001/preter")
    assert r.status_code == 200
    assert "Emplacement" in r.text and ">1<" in r.text.replace(" ", "")

    # Re-prêter alors que déjà sorti -> message, pas d'erreur
    r2 = client.get("/pret/001")
    assert "Sorti" in r2.text

    r3 = client.post("/pret/001/rendre")
    assert r3.status_code == 200
    assert "libéré" in r3.text

    # Après retour : de nouveau disponible
    r4 = client.get("/pret/001")
    assert "Disponible" in r4.text
