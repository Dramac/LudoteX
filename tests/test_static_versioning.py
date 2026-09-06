"""
Version des ressources statiques (`?v=` sur `/static/css/style.css`,
`/static/js/jsQR.js`, `/static/js/scanner.js`) — lot agora-2.

GÉNÉRALISATION testée ici : l'ancien `static_v` ne datait que `style.css`,
en dur ; `asset_v(chemin)` fait la même chose pour n'importe quel fichier de
`app/static/`, motif déjà éprouvé par `logo_v()` (tests/test_logo.py) pour
les images d'identité. Trois choses à couvrir, propres à ce lot :

1. le helper rend bien la date de modification du FICHIER DEMANDÉ ;
2. il ne lève jamais, y compris sur un fichier absent (un global Jinja tourne
   aussi pendant le rendu de la page d'erreur 500 — enseignement du lot
   agora-1, déjà vérifié en pratique par tests/test_routes.py::
   test_page_erreur_500, qui exercerait ce chemin si `asset_v` levait) ;
3. les trois gabarits qui chargent ces ressources (`base.html`,
   `scanner.html`, `transfert_scan.html`) portent bien `?v=` dessus, avec la
   valeur rendue par le helper lui-même — pas une chaîne en dur qui pourrait
   diverger.

Fixture `client` sur le modèle de tests/test_routes.py (une seule base de
prêt, un exemplaire 001/Catan) : aucun scénario métier n'est nécessaire ici,
seul le rendu des gabarits est en jeu.
"""

import pytest

from app import templating
from app.templating import BASE_DIR, asset_v


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
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _mtime(chemin_relatif: str) -> int:
    return int((BASE_DIR / "static" / chemin_relatif).stat().st_mtime)


def test_rend_la_date_de_modification_du_fichier_demande():
    assert asset_v("css/style.css") == _mtime("css/style.css")


def test_fonctionne_pour_chacune_des_trois_ressources_versionnees():
    for chemin in ("css/style.css", "js/jsQR.js", "js/scanner.js"):
        assert asset_v(chemin) == _mtime(chemin)


def test_ne_leve_jamais_sur_un_fichier_absent():
    assert asset_v("js/ce-fichier-n-existe-pas.js") == 0


def test_static_v_a_disparu_un_seul_domicile():
    # Un seul mécanisme de version pour les ressources statiques : l'ancien
    # global `static_v` (constante figée à l'import, une seule ressource) ne
    # doit plus coexister avec `asset_v`.
    assert "static_v" not in templating.templates.env.globals
    assert "asset_v" in templating.templates.env.globals


def test_base_html_versionne_style_css(client):
    r = client.get("/aide")
    assert r.status_code == 200
    assert f"/static/css/style.css?v={asset_v('css/style.css')}" in r.text


def test_scanner_html_versionne_les_deux_scripts(client):
    r = client.get("/scanner")
    assert r.status_code == 200
    assert f"/static/js/jsQR.js?v={asset_v('js/jsQR.js')}" in r.text
    assert f"/static/js/scanner.js?v={asset_v('js/scanner.js')}" in r.text


def test_transfert_scan_html_versionne_les_deux_scripts(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert")
    assert r.status_code == 200
    assert f"/static/js/jsQR.js?v={asset_v('js/jsQR.js')}" in r.text
    assert f"/static/js/scanner.js?v={asset_v('js/scanner.js')}" in r.text
