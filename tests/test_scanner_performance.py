"""
Scanner : mode diagnostic et garde-fous de performance (lot agora-3).

CE QUI EST TESTABLE ICI, ET CE QUI NE L'EST PAS
------------------------------------------------
Le cœur du lot vit dans `app/static/js/scanner.js` (réduction de l'image avant
décodage, arrêt des pistes caméra, mesure des temps). Le projet n'a AUCUN
outillage de test JavaScript, et le lot a explicitement écarté d'en introduire
un : ces comportements se vérifient à la main, sur un vrai téléphone en HTTPS
(procédure dans `interne/comptes-rendus/lot-agora-3-scanner-performance.md`).

Deux choses restent vérifiables depuis pytest, et elles le sont ici :

1. **Le drapeau du mode diagnostic**, qui est un comportement de ROUTE et de
   GABARIT : `?debug=1` pose `data-scan-debug="1"` sur <body>, et RIEN d'autre
   ne le pose — sans le paramètre, le script ne mesure rien.
2. **Le contrat entre le gabarit et le script** : le nom de l'attribut est la
   seule chose qui les relie. Un renommage d'un seul côté éteindrait le mode
   diagnostic en silence, sans qu'aucun autre test ne bronche. Les quelques
   assertions sur le CONTENU de `scanner.js` qui suivent ne prétendent pas
   tester son comportement : elles verrouillent des correctifs invisibles, à
   la manière de `tests/test_theme.py` pour le littéral de couleur partagé.
"""

import re

import pytest

from app.templating import BASE_DIR

SCANNER_JS = BASE_DIR / "static" / "js" / "scanner.js"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
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
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ===========================================================================
# 1. Le drapeau du mode diagnostic
# ===========================================================================
def test_scanner_sans_parametre_n_a_pas_de_mode_diagnostic(client):
    r = client.get("/scanner")
    assert r.status_code == 200
    assert "data-scan-debug" not in r.text


def test_scanner_avec_debug_1_allume_le_mode_diagnostic(client):
    r = client.get("/scanner?debug=1")
    assert r.status_code == 200
    assert 'data-scan-debug="1"' in r.text


@pytest.mark.parametrize("valeur", ["0", "", "oui", "true", "2"])
def test_seule_la_valeur_1_allume_le_mode_diagnostic(client, valeur):
    # Un `?debug=0` collé dans une barre d'adresse ne doit pas l'allumer :
    # la valeur est comparée à « 1 », pas évaluée comme un booléen.
    r = client.get(f"/scanner?debug={valeur}")
    assert r.status_code == 200
    assert "data-scan-debug" not in r.text


def test_les_autres_routes_qui_rendent_scanner_html_restent_eteintes(client):
    # `scan_debug` a un défaut explicite dans `_contexte_scanner` : ces deux
    # routes rendent le même gabarit et ne doivent ni planter ni allumer la
    # mesure (ni la propager depuis une page précédente).
    saisie = client.get("/scanner/saisie?code=inconnu")
    assert saisie.status_code == 200
    assert "data-scan-debug" not in saisie.text

    ranger = client.get("/scanner/ranger?code=001")
    assert ranger.status_code == 200
    assert "data-scan-debug" not in ranger.text


def test_mode_rangement_et_mode_diagnostic_coexistent(client):
    # Les deux drapeaux vivent dans le même bloc `body_attrs` : l'un ne doit
    # pas chasser l'autre.
    client.post("/scanner/rangement/activer", data={"emplacement_texte": "Table 3"})
    r = client.get("/scanner?debug=1")
    assert r.status_code == 200
    assert 'data-rangement="1"' in r.text
    assert 'data-scan-debug="1"' in r.text


def test_page_de_transfert_ne_propose_pas_le_mode_diagnostic(client):
    # Hors périmètre du lot : le mode se pilote depuis /scanner. Le gabarit du
    # transfert charge le même script, qui doit y rester silencieux.
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert?debug=1")
    assert r.status_code == 200
    assert "data-scan-debug" not in r.text


# ===========================================================================
# 2. Contrat gabarit / script, et correctifs invisibles à verrouiller
# ===========================================================================
def test_le_script_lit_le_meme_attribut_que_le_gabarit():
    # `data-scan-debug` côté HTML se lit `dataset.scanDebug` côté JS. Rien
    # d'autre ne relie les deux : un renommage d'un seul côté éteindrait le
    # mode diagnostic sans aucun symptôme.
    assert "dataset.scanDebug" in SCANNER_JS.read_text(encoding="utf-8")


def test_le_script_relache_les_pistes_de_la_camera():
    # Correctif du lot agora-3 : sans `stop()`, la caméra reste allumée
    # pendant le chargement de la page suivante. Rien à l'écran ne le
    # signale — d'où ce garde-fou.
    source = SCANNER_JS.read_text(encoding="utf-8")
    assert "getTracks()" in source
    assert ".stop()" in source
    assert '"pagehide"' in source


def test_le_script_reduit_l_image_avant_de_la_decoder():
    # Le coût de jsQR est proportionnel au nombre de pixels : décoder à la
    # résolution native de la caméra est la première cause de lenteur au
    # scan. La largeur d'analyse doit rester bornée et raisonnable.
    source = SCANNER_JS.read_text(encoding="utf-8")
    assert "LARGEUR_ANALYSE" in source
    assert "canvas.width = video.videoWidth" not in source


def test_les_contraintes_camera_restent_souples():
    # Une contrainte `exact` fait ÉCHOUER l'ouverture de la caméra sur les
    # appareils qui ne savent pas la satisfaire : on se priverait du scanner
    # au lieu de l'accélérer. Tout doit rester en `ideal`.
    # On cherche la SYNTAXE de contrainte (`exact:` / `ideal:`), pas les mots :
    # les commentaires du fichier les emploient tous les deux en prose.
    source = SCANNER_JS.read_text(encoding="utf-8")
    assert re.search(r"exact\s*:", source) is None
    assert len(re.findall(r"ideal\s*:", source)) >= 2  # largeur et hauteur
