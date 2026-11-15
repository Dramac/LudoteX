"""
Changer le numéro de pochette proposé juste après un prêt (retour terrain,
version 1.34) : `services.changer_pochette` et la route
`POST /pret/<id>/pochette`.

Fixtures sur le modèle de `tests/test_transfert_routes.py` : trois boîtes,
001/Catan, 002/Dixit, 003/Catan.
"""

import json

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


def _lire(sql, *params):
    from app import db
    conn = db.get_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _pochette_du_pret(id_exemplaire):
    rows = _lire("SELECT numero_pochette FROM prets "
                 "WHERE id_exemplaire = ? AND date_retour IS NULL", id_exemplaire)
    return rows[0][0] if rows else None


def _pochettes():
    return dict(_lire("SELECT numero_pochette, occupe FROM pochettes"))


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def test_le_formulaire_suit_le_numero_propose(client):
    r = client.post("/pret/001/preter")
    assert "Utiliser une autre pochette" in r.text
    assert 'action="/pret/001/pochette"' in r.text
    # Replié par défaut : le cas courant ne doit voir qu'une ligne de plus.
    assert '<details class="pochette-forcer">' in r.text


def test_le_formulaire_n_apparait_pas_sur_un_simple_affichage(client):
    client.post("/pret/001/preter")
    assert "Utiliser une autre pochette" not in client.get("/pret/001").text


# ---------------------------------------------------------------------------
# Changement réussi
# ---------------------------------------------------------------------------
def test_changer_vers_un_numero_libre(client):
    client.post("/pret/001/preter")                  # pochette 1
    r = client.post("/pret/001/pochette", data={"numero": "4"})

    assert r.status_code == 200
    assert '<p class="pochette-num">4</p>' in r.text
    assert "la pochette n°1" in r.text
    assert "ne sera plus proposée jusqu'à la fin de l'événement" in r.text
    assert _pochette_du_pret("001") == 4
    # 2 et 3 créés LIBRES : sinon le prochain prêt partirait sur 5.
    assert _pochettes() == {1: 0, 2: 0, 3: 0, 4: 1}


def _ecartees():
    return [n for (n,) in _lire(
        "SELECT numero_pochette FROM pochettes WHERE ecartee = 1 ORDER BY 1")]


def test_le_numero_ignore_n_est_plus_propose(client):
    # Retour terrain : la pochette proposée manque ou est abîmée ; la
    # reproposer au prêt suivant ferait refaire le même détour.
    client.post("/pret/001/preter")                  # proposé : 1
    client.post("/pret/001/pochette", data={"numero": "3"})
    assert _ecartees() == [1]
    r = client.post("/pret/002/preter")
    assert '<p class="pochette-num">2</p>' in r.text


def test_le_numero_ignore_reste_ecarte_apres_les_retours(client):
    client.post("/pret/001/preter")                  # 1 → changé en 2
    client.post("/pret/001/pochette", data={"numero": "2"})
    client.post("/pret/001/rendre")                  # 2 libre
    r = client.post("/pret/002/preter")
    assert '<p class="pochette-num">2</p>' in r.text  # pas 1
    client.post("/pret/002/rendre")
    client.post("/pret/003/preter")
    assert _pochette_du_pret("003") == 2


def test_seuls_libres_ecartes_on_cree_le_suivant(client):
    client.post("/pret/001/preter")                  # 1
    client.post("/pret/001/pochette", data={"numero": "2"})   # 1 écartée
    r = client.post("/pret/002/preter")
    assert '<p class="pochette-num">3</p>' in r.text


def test_choisir_a_la_main_un_numero_ecarte_le_remet_en_service(client):
    client.post("/pret/001/preter")
    client.post("/pret/001/pochette", data={"numero": "4"})   # 1 écartée
    client.post("/pret/002/preter")                           # 2
    client.post("/pret/002/pochette", data={"numero": "1"})   # reprend 1
    assert _pochette_du_pret("002") == 1
    assert _ecartees() == [2]
    client.post("/pret/002/rendre")
    r = client.post("/pret/003/preter")
    assert '<p class="pochette-num">1</p>' in r.text


def test_la_cloture_remet_les_numeros_ecartes_en_service(client):
    from app import db, services
    client.post("/pret/001/preter")
    client.post("/pret/001/pochette", data={"numero": "5"})
    assert _ecartees() == [1]
    conn = db.get_connection()
    try:
        services.cloturer_evenement(conn)
    finally:
        conn.close()
    assert _ecartees() == []
    r = client.post("/pret/002/preter")
    assert '<p class="pochette-num">1</p>' in r.text


def test_numero_inferieur_deja_cree_et_libre(client):
    client.post("/pret/001/preter")                  # 1
    client.post("/pret/002/preter")                  # 2
    client.post("/pret/001/rendre")                  # 1 libre
    client.post("/pret/003/preter")                  # reprend 1
    client.post("/pret/002/rendre")                  # 2 libre

    client.post("/pret/003/pochette", data={"numero": "2"})
    assert _pochette_du_pret("003") == 2
    assert _pochettes() == {1: 0, 2: 1}
    assert _ecartees() == [1]


def test_meme_numero_ne_change_rien(client):
    client.post("/pret/001/preter")
    r = client.post("/pret/001/pochette", data={"numero": "1"})
    assert '<p class="pochette-num">1</p>' in r.text
    assert "ne sera plus proposée" not in r.text
    assert _pochettes() == {1: 1}
    assert _ecartees() == []


def test_le_retour_libere_le_numero_choisi(client):
    client.post("/pret/001/preter")
    client.post("/pret/001/pochette", data={"numero": "5"})
    r = client.post("/pret/001/rendre")
    assert 'pochette-num--retour">5</p>' in r.text
    assert _pochettes()[5] == 0


# ---------------------------------------------------------------------------
# Refus : rien n'est écrit, le formulaire reste ouvert
# ---------------------------------------------------------------------------
def test_numero_deja_pris_par_un_autre_pret(client):
    client.post("/pret/001/preter")                  # 1
    client.post("/pret/002/preter")                  # 2
    r = client.post("/pret/002/pochette", data={"numero": "1"})

    assert "La pochette n°1 est déjà prise" in r.text
    assert "Catan" in r.text
    assert "Rien n'a été modifié" in r.text
    assert "Le prêt reste sur la pochette" in r.text
    assert '<details class="pochette-forcer" open>' in r.text
    assert _pochette_du_pret("001") == 1
    assert _pochette_du_pret("002") == 2


@pytest.mark.parametrize("saisie", ["", "abc", "0", "-3", "2.5", "10000"])
def test_saisie_invalide(client, saisie):
    client.post("/pret/001/preter")
    r = client.post("/pret/001/pochette", data={"numero": saisie})

    assert r.status_code == 200
    assert "Rien n'a été modifié" in r.text
    assert '<details class="pochette-forcer" open>' in r.text
    assert _pochette_du_pret("001") == 1
    assert _pochettes() == {1: 1}


def test_boite_rendue_entre_temps(client):
    client.post("/pret/001/preter")
    client.post("/pret/001/rendre")                  # un autre bénévole
    r = client.post("/pret/001/pochette", data={"numero": "3"})
    assert "déjà disponible" in r.text
    assert _pochettes() == {1: 0}


def test_sortie_tournoi_sans_pochette(client):
    client.post("/pret/003/tournoi")
    r = client.post("/pret/003/pochette", data={"numero": "3"})
    assert r.status_code == 200
    assert _pochette_du_pret("003") == 0
    assert _pochettes() == {}


def test_exige_le_jeton(client, monkeypatch):
    client.post("/pret/001/preter")
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    r = client.post("/pret/001/pochette", data={"numero": "3"})
    assert r.status_code == 403
    assert _pochette_du_pret("001") == 1


# ---------------------------------------------------------------------------
# Journal : l'action, jamais le numéro
# ---------------------------------------------------------------------------
def test_journal_sans_numero(client, _journal_isole):
    client.post("/pret/001/preter")
    client.post("/pret/001/pochette", data={"numero": "7"})

    lignes = [json.loads(l) for l in
              _journal_isole.read_text(encoding="utf-8").splitlines() if l.strip()]
    changees = [l for l in lignes if l["action"] == "pochette_changee"]
    assert len(changees) == 1
    assert changees[0]["objet"] == "Catan"
    # Ni clé ni valeur ne porte le numéro (l'horodatage `t` est écarté : il
    # peut contenir un 7 par hasard).
    sans_heure = {k: v for k, v in changees[0].items() if k != "t"}
    assert "7" not in json.dumps(sans_heure, ensure_ascii=False)


def test_un_refus_n_ecarte_rien(client):
    client.post("/pret/001/preter")                  # 1
    client.post("/pret/002/preter")                  # 2
    client.post("/pret/002/pochette", data={"numero": "1"})   # occupée
    client.post("/pret/002/pochette", data={"numero": "abc"})
    assert _ecartees() == []


def test_migration_d_une_base_sans_la_colonne(tmp_path, monkeypatch):
    # Base d'avant la 1.35 : la colonne arrive à 0, l'attribution est
    # exactement celle d'avant.
    import sqlite3
    from app import db, services

    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.execute("CREATE TABLE pochettes (numero_pochette INTEGER PRIMARY KEY, "
                     "occupe INTEGER NOT NULL DEFAULT 0)")
    ancienne.executemany("INSERT INTO pochettes VALUES (?, ?)", [(1, 1), (2, 0)])
    ancienne.commit()
    ancienne.close()

    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        colonnes = [r[1] for r in conn.execute("PRAGMA table_info(pochettes)")]
        assert "ecartee" in colonnes
        with services.transaction(conn):
            assert services.plus_petit_numero_libre(conn) == 2
    finally:
        conn.close()
