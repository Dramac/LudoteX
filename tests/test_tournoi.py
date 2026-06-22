"""
Tests du module « Tournois » (socle phase 1) : logique métier (base séparée en
mémoire) + routes (public/bénévole) via TestClient.
"""

import sqlite3

import pytest

from app.tournoi import models, services


# ===========================================================================
# Services — base des tournois en mémoire
# ===========================================================================
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        c.execute(p)
    for s in models.SCHEMA_STATEMENTS:
        c.executescript(s)
    yield c
    c.close()


def test_creer_et_get(conn):
    tid = services.creer_tournoi(conn, "Carcassonne du soir", jeu="Carcassonne",
                                 nb_places=8, emplacement="Table 3")
    t = services.get_tournoi(conn, tid)
    assert t["nom"] == "Carcassonne du soir"
    assert t["jeu"] == "Carcassonne"
    assert t["etat"] == "brouillon"
    assert t["nb_places"] == 8


def test_machine_a_etats(conn):
    tid = services.creer_tournoi(conn, "T")
    # brouillon -> inscriptions OK ; brouillon -> termine refusé.
    assert services.changer_etat(conn, tid, "termine") is False
    assert services.changer_etat(conn, tid, "inscriptions") is True
    assert services.get_tournoi(conn, tid)["etat"] == "inscriptions"
    # état inconnu refusé.
    assert services.changer_etat(conn, tid, "n_importe_quoi") is False
    # inscriptions -> termine OK.
    assert services.changer_etat(conn, tid, "termine") is True


def test_inscription_publique_et_etat(conn):
    tid = services.creer_tournoi(conn, "T", nb_places=2)
    # Inscriptions fermées (brouillon) -> refus.
    assert services.inscrire(conn, tid, "Alice")["raison"] == "fermee"
    services.changer_etat(conn, tid, "inscriptions")
    r = services.inscrire(conn, tid, "Alice")
    assert r["ok"] and r["code"]
    assert services.compter_inscriptions(conn, tid) == 1
    # Pseudo vide -> refus.
    assert services.inscrire(conn, tid, "   ")["raison"] == "pseudo_vide"
    # 2e place puis complet.
    assert services.inscrire(conn, tid, "Bob")["ok"]
    assert services.inscrire(conn, tid, "Chloé")["raison"] == "complet"


def test_places_illimitees(conn):
    tid = services.creer_tournoi(conn, "T")  # nb_places None
    services.changer_etat(conn, tid, "inscriptions")
    assert services.places_restantes(conn, services.get_tournoi(conn, tid)) is None
    for nom in ("a", "b", "c"):
        assert services.inscrire(conn, tid, nom)["ok"]


def test_desinscription_par_code(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    code = services.inscrire(conn, tid, "Alice")["code"]
    assert services.desinscrire(conn, "mauvais")["ok"] is False
    res = services.desinscrire(conn, code)
    assert res["ok"] and res["pseudo"] == "Alice"
    assert services.compter_inscriptions(conn, tid) == 0


def test_ajout_manuel_ignore_plafond(conn):
    # Le bénévole peut ajouter au-delà du plafond et même hors état "inscriptions".
    tid = services.creer_tournoi(conn, "T", nb_places=1)
    assert services.ajouter_participant(conn, tid, "Alice")["ok"]
    assert services.ajouter_participant(conn, tid, "Bob")["ok"]
    assert services.compter_inscriptions(conn, tid) == 2


def test_suppression_cascade(conn):
    tid = services.creer_tournoi(conn, "T")
    services.ajouter_participant(conn, tid, "Alice")
    services.supprimer_tournoi(conn, tid)
    assert services.get_tournoi(conn, tid) is None
    # Plus aucune inscription orpheline (cascade FK).
    assert conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0] == 0


# --- High score ---
def _tournoi_lance_high_score(conn, pseudos=("Alice", "Bob", "Chloé")):
    tid = services.creer_tournoi(conn, "HS")
    services.changer_etat(conn, tid, "inscriptions")
    for p in pseudos:
        services.ajouter_participant(conn, tid, p)
    return tid


def test_lancer_refus_sans_participant(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    assert services.lancer_tournoi(conn, tid, "high_score")["raison"] == "sans_participant"


def test_lancer_refus_mode_inconnu_et_etat(conn):
    tid = _tournoi_lance_high_score(conn)
    assert services.lancer_tournoi(conn, tid, "inexistant")["raison"] == "mode_inconnu"
    # Depuis 'brouillon', lancement interdit (transition).
    tid2 = services.creer_tournoi(conn, "T2")
    services.ajouter_participant(conn, tid2, "X")
    assert services.lancer_tournoi(conn, tid2, "high_score")["raison"] == "etat"


def test_lancer_high_score_cree_les_lignes(conn):
    tid = _tournoi_lance_high_score(conn)
    assert services.lancer_tournoi(conn, tid, "high_score")["ok"]
    t = services.get_tournoi(conn, tid)
    assert t["etat"] == "lance" and t["mode_scoring"] == "high_score"
    # Une ligne de score par participant.
    assert conn.execute("SELECT COUNT(*) FROM rencontres WHERE id_tournoi = ?",
                        (tid,)).fetchone()[0] == 3


def test_scores_et_classement_ex_aequo(conn):
    tid = _tournoi_lance_high_score(conn)
    services.lancer_tournoi(conn, tid, "high_score")
    lignes = {l["pseudo"]: l["id_inscription"] for l in services.lignes_high_score(conn, tid)}
    services.enregistrer_scores_high_score(conn, tid, {
        lignes["Alice"]: 10, lignes["Bob"]: 10, lignes["Chloé"]: 5,
    })
    cl = services.classement_high_score(conn, tid)
    # Alice et Bob ex æquo en tête (rang 1), Chloé rang 3.
    rangs = {c["pseudo"]: c["rang"] for c in cl}
    assert rangs["Alice"] == 1 and rangs["Bob"] == 1
    assert rangs["Chloé"] == 3


def test_classement_score_manquant_en_dernier(conn):
    tid = _tournoi_lance_high_score(conn, pseudos=("Alice", "Bob"))
    services.lancer_tournoi(conn, tid, "high_score")
    lignes = {l["pseudo"]: l["id_inscription"] for l in services.lignes_high_score(conn, tid)}
    services.enregistrer_scores_high_score(conn, tid, {lignes["Alice"]: 7})
    cl = services.classement_high_score(conn, tid)
    assert cl[0]["pseudo"] == "Alice" and cl[0]["rang"] == 1
    # Bob, sans score, finit sans rang.
    assert cl[-1]["pseudo"] == "Bob" and cl[-1]["rang"] is None


def test_participant_ajoute_apres_lancement_a_une_ligne(conn):
    tid = _tournoi_lance_high_score(conn, pseudos=("Alice",))
    services.lancer_tournoi(conn, tid, "high_score")
    services.ajouter_participant(conn, tid, "Tardif")
    # lignes_high_score crée paresseusement la ligne manquante.
    pseudos = {l["pseudo"] for l in services.lignes_high_score(conn, tid)}
    assert "Tardif" in pseudos


# ===========================================================================
# Routes — TestClient avec les DEUX bases temporaires
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))

    from app import db
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    db.init_db()
    tdb.init_db()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_liste_publique(client):
    r = client.get("/tournois")
    assert r.status_code == 200 and "Tournois" in r.text


def test_cycle_complet_route(client):
    # Création (mode ouvert : pas de PRET_TOKEN -> accès bénévole autorisé).
    r = client.post("/tournoi/nouveau", data={"nom": "Tournoi Test",
                    "jeu": "Catan", "nb_places": "4",
                    "inscription_en_ligne": "on"}, follow_redirects=False)
    assert r.status_code == 303
    tid = r.headers["location"].split("/")[2]

    # Brouillon : pas d'inscription publique possible.
    detail = client.get(f"/tournoi/{tid}")
    assert detail.status_code == 200 and "Tournoi Test" in detail.text
    assert "S'inscrire" not in detail.text

    # Ouvrir les inscriptions.
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    assert "S'inscrire" in client.get(f"/tournoi/{tid}").text

    # Inscription publique : le code est affiché.
    insc = client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Alice"})
    assert insc.status_code == 200 and "code de désinscription" in insc.text.lower()
    # Le pseudo apparaît dans la gestion.
    assert "Alice" in client.get(f"/tournoi/{tid}/gerer").text

    # Désinscription via le formulaire (code récupéré en base).
    from app.tournoi import db as tdb
    c = tdb.get_connection()
    code = c.execute("SELECT code_desinscription FROM inscriptions").fetchone()[0]
    c.close()
    d = client.post("/tournoi/desinscription", data={"code": code})
    assert "enregistrée" in d.text.lower()


def test_inscription_complet_route(client):
    r = client.post("/tournoi/nouveau", data={"nom": "T", "nb_places": "1",
                    "inscription_en_ligne": "on"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    assert client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Alice"}).status_code == 200
    plein = client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Bob"})
    assert plein.status_code == 400 and "complet" in plein.text.lower()


def test_suppression_double_confirmation(client):
    r = client.post("/tournoi/nouveau", data={"nom": "À supprimer"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    # Sans la case cochée : pas de suppression (renvoi vers la confirmation).
    sans = client.post(f"/tournoi/{tid}/supprimer", data={}, follow_redirects=False)
    assert sans.status_code == 303 and sans.headers["location"].endswith("/supprimer")
    assert client.get(f"/tournoi/{tid}").status_code == 200
    # Avec confirmation : supprimé.
    ok = client.post(f"/tournoi/{tid}/supprimer", data={"confirmation": "oui"},
                     follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/tournois"
    assert client.get(f"/tournoi/{tid}").status_code == 404


def test_high_score_route_complet(client):
    # Création + ouverture + 2 participants manuels.
    r = client.post("/tournoi/nouveau", data={"nom": "HS Route"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    client.post(f"/tournoi/{tid}/participant", data={"pseudo": "Alice"})
    client.post(f"/tournoi/{tid}/participant", data={"pseudo": "Bob"})

    # Lancement high score -> redirige vers la saisie des scores.
    lance = client.post(f"/tournoi/{tid}/lancer",
                        data={"mode_scoring": "high_score"}, follow_redirects=False)
    assert lance.status_code == 303 and lance.headers["location"].endswith("/scores")

    # Récupère les id d'inscription pour nommer les champs score_<id>.
    from app.tournoi import db as tdb
    c = tdb.get_connection()
    ids = {row["pseudo"]: row["id_inscription"] for row in
           c.execute("SELECT id_inscription, pseudo FROM inscriptions WHERE id_tournoi = ?", (tid,))}
    c.close()

    save = client.post(f"/tournoi/{tid}/scores", data={
        f"score_{ids['Alice']}": "12", f"score_{ids['Bob']}": "30"})
    assert save.status_code == 200 and "enregistrés" in save.text.lower()

    # Le classement public montre Bob en tête (30 > 12).
    detail = client.get(f"/tournoi/{tid}").text
    assert "Classement" in detail
    pos_bob, pos_alice = detail.index("Bob"), detail.index("Alice")
    assert pos_bob < pos_alice


def test_lancer_protege_par_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-hs-secret-123")
    assert client.post("/tournoi/1/lancer", data={"mode_scoring": "high_score"}).status_code == 403
    assert client.get("/tournoi/1/scores").status_code == 403


def test_routes_benevole_protegees(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-tournoi-secret-123")
    # Public accessible, gestion refusée sans jeton.
    assert client.get("/tournois").status_code == 200
    assert client.get("/tournoi/nouveau").status_code == 403
    r = client.post("/tournoi/nouveau", data={"nom": "X"})
    assert r.status_code == 403
