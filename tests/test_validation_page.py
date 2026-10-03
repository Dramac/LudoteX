"""
Plus de JSON brut sur une valeur non convertible (lot 13 de la série
pré-production, constat UX-01 ; même famille que ROB-04).

Deux étages :

- un paramètre de CHEMIN typé porte son convertisseur (`{x:int}`) : une
  valeur non entière ne correspond à aucune route et tombe sur la 404
  conviviale. Un garde-fou parcourt toutes les routes, pour que le cas
  corrigé ici ne revienne pas par une route ajoutée demain ;
- tout le reste (requête, formulaire) passe par le gestionnaire de
  `RequestValidationError` de app/main.py, qui rend la page générique.
"""

import re

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
                 "VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _pas_du_json(r):
    assert r.headers["content-type"].startswith("text/html")
    assert '"detail"' not in r.text
    assert "int_parsing" not in r.text


def test_identifiant_de_signalement_non_entier_rend_la_page_404(client):
    """Le cas de l'audit, sur la fiche de la boîte : 422 en JSON avant."""
    r = client.post("/pret/001/signalements/abc/traiter")

    assert r.status_code == 404
    _pas_du_json(r)


def test_la_route_jumelle_du_carnet_fait_de_meme(client):
    """Le modèle suivi : `routes/maintenance.py`."""
    r = client.post("/maintenance/abc/traiter")

    assert r.status_code == 404
    _pas_du_json(r)


def test_un_identifiant_entier_inconnu_garde_son_message_metier(client):
    """Le convertisseur ne change rien au cas nominal : la route répond."""
    r = client.post("/pret/001/signalements/999/traiter")

    assert r.status_code == 200
    assert "ne correspond pas" in r.text or "n'existe plus" in r.text


def test_une_valeur_de_requete_non_entiere_rend_la_page_generique(client):
    """
    Un cas que l'audit n'avait pas listé : `?page=abc` sur un écran du bureau.
    Le code 422 est conservé ; la page, elle, est celle de l'application.
    """
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})

    r = client.get("/admin/rangement/ranger", params={"page": "abc"})

    assert r.status_code == 422
    _pas_du_json(r)
    assert "Cette demande contient une valeur inattendue" in r.text
    # La valeur envoyée n'est pas recopiée dans la page.
    assert "abc" not in r.text


def test_tout_parametre_de_chemin_type_porte_son_convertisseur():
    """
    Garde-fou : un paramètre de chemin annoncé `int` sans `{x:int}` dans le
    chemin est routé PUIS refusé à la conversion — c'est le défaut UX-01. Le
    gestionnaire de main.py le rattraperait en page générique (422), mais la
    bonne réponse à une adresse qui ne peut désigner rien est la 404.
    """
    from fastapi.routing import APIRoute

    from app.main import app

    fautifs = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        convertis = set(re.findall(r"\{(\w+):\w+\}", route.path))
        for param in route.dependant.path_params:
            if param.field_info.annotation is not str and param.name not in convertis:
                fautifs.append(f"{route.path} ({param.name})")
    assert fautifs == []
