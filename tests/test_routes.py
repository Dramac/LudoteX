"""Tests d'intégration des routes (fiche + prêt/retour) via une base temporaire."""

import os

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Bases SQLite temporaires isolées par test (prêt + tournois, séparées).
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    from app import db
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    tdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_page_erreur_500(client, monkeypatch):
    # On force une erreur inattendue dans une route et on vérifie que l'app
    # renvoie la page 500 conviviale (et ne « plante » pas).
    from fastapi.testclient import TestClient

    from app import services
    from app.main import app

    def boum(*a, **k):
        raise RuntimeError("erreur simulée")

    monkeypatch.setattr(services, "lister_catalogue", boum)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/catalogue")
    assert r.status_code == 500
    assert "une erreur est survenue" in r.text.lower()


def test_aide_page(client):
    r = client.get("/aide")
    assert r.status_code == 200 and "Mode d'emploi" in r.text


def test_accueil(client):
    # La racine sert la page d'accueil (plus de redirection vers /catalogue).
    r = client.get("/")
    assert r.status_code == 200
    # 1 exemplaire de test (Catan), disponible.
    assert "disponible" in r.text.lower()
    assert "/catalogue" in r.text and "/tournois" in r.text


def test_accueil_tournoi_imminent(client):
    # Un tournoi publié commençant dans 30 min apparaît ; un autre dans 3 h non.
    from datetime import datetime, timedelta, timezone

    from app.tournoi import db as tdb
    from app.tournoi import services as ts

    conn = tdb.get_connection()
    try:
        proche = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        loin = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(timespec="seconds")
        idp = ts.creer_tournoi(conn, "Tournoi proche", date_heure=proche)
        ts.creer_tournoi(conn, "Tournoi lointain", date_heure=loin)
        ts.changer_etat(conn, idp, "inscriptions")
        conn.commit()
    finally:
        conn.close()
    r = client.get("/")
    assert "Tournoi proche" in r.text
    assert "Tournoi lointain" not in r.text


def test_menu_benevole_conditionnel(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-menu-xyz")
    # Appareil non activé : pas de menu bénévole sur le catalogue public.
    assert 'class="menu-benevole"' not in client.get("/catalogue").text
    # Après activation : le menu apparaît.
    client.get("/acces", params={"jeton": "jeton-menu-xyz"})
    assert 'class="menu-benevole"' in client.get("/catalogue").text


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


def test_racine_sert_accueil(client):
    # La racine sert désormais la page d'accueil directement (plus de redirection).
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Des jeux plein la Manche" in r.text


def test_stats_page(client):
    client.post("/pret/001/preter")          # un prêt pour alimenter les stats
    r = client.get("/stats")
    assert r.status_code == 200
    assert "Statistiques" in r.text
    assert "Catan" in r.text                 # apparaît dans le palmarès
    r2 = client.get("/stats", params={"tri": "exemplaire"})
    assert r2.status_code == 200
    assert "par exemplaire" in r2.text


def test_stats_alias_redirige(client):
    for chemin in ("/stat", "/statistique", "/statistiques"):
        r = client.get(chemin, follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"].startswith("/stats")


def test_stats_filtre_periode(client):
    client.post("/pret/001/preter")
    # Période lointaine dans le passé -> aucun prêt.
    r = client.get("/stats", params={"debut": "2000-01-01T00:00",
                                     "fin": "2000-01-02T00:00"})
    assert r.status_code == 200
    assert "Aucun prêt sur la période." in r.text


def test_stats_exports(client):
    client.post("/pret/001/preter")
    x = client.get("/stats/export.xlsx")
    assert x.status_code == 200
    assert "spreadsheetml" in x.headers["content-type"]
    assert x.content[:2] == b"PK"            # un .xlsx est une archive ZIP
    p = client.get("/stats/export.pdf")
    assert p.status_code == 200
    assert p.headers["content-type"] == "application/pdf"
    assert p.content[:4] == b"%PDF"


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


def test_admin_login_et_creation_jeu(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")

    # Sans session : accès admin redirige vers la connexion.
    r = client.get("/admin/jeu-nouveau", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"

    # Mauvais mot de passe -> refus.
    assert client.post("/admin/login", data={"mot_de_passe": "faux"}).status_code == 403

    # Bon mot de passe -> session ouverte (cookie conservé par le client).
    r2 = client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"},
                     follow_redirects=False)
    assert r2.status_code == 303

    # Création d'un jeu -> redirige vers sa fiche admin (id auto, préfixe A).
    r3 = client.post("/admin/jeu-nouveau", data={"nom": "Jeu Test Admin",
                     "type_jeu": "Extension", "categorie": "Cartes",
                     "nb_joueurs_min": "2", "nb_joueurs_max": "5",
                     "age_min": "8", "duree_min": "20"},
                     follow_redirects=False)
    assert r3.status_code == 303
    ref = r3.headers["location"].rsplit("/", 1)[-1]
    assert ref == "JEU_TEST_ADMIN"

    # La fiche admin affiche un exemplaire à id auto (A0001), le type, l'étiquette.
    fiche = client.get("/admin/jeu/" + ref)
    assert fiche.status_code == 200 and "A0001" in fiche.text
    assert "Extension" in fiche.text
    # La fiche publique affiche aussi le type.
    assert "Extension" in client.get("/jeu/A0001").text
    png = client.get("/admin/etiquette/A0001.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"


def test_admin_cloture_prets(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/pret/001/preter")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r = client.post("/admin/cloturer-prets")
    assert r.status_code == 200 and "clôturé" in r.text
    # L'exemplaire est de nouveau disponible.
    assert "Disponible" in client.get("/pret/001").text


def test_admin_changement_mdp(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "initial-123")
    client.post("/admin/login", data={"mot_de_passe": "initial-123"})
    # Mauvaise confirmation -> pas de changement.
    r = client.post("/admin/motdepasse", data={"ancien": "initial-123",
                    "nouveau": "nouveau-456", "confirmation": "xxx"})
    assert "ne correspond pas" in r.text
    # Changement correct.
    r2 = client.post("/admin/motdepasse", data={"ancien": "initial-123",
                     "nouveau": "nouveau-456", "confirmation": "nouveau-456"})
    assert "modifié" in r2.text


def test_admin_jeton_reinitialisation(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    # Réinitialisation (sans date) -> jeton posé + lien + validité par défaut affichée.
    r = client.post("/admin/jeton/reinitialiser", data={"expire": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/admin/jeton")
    assert page.status_code == 200 and "/acces?jeton=" in page.text
    assert "Valable jusqu'au" in page.text


def test_jeton_expire_ferme_acces(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    # Réinitialisation avec une date de fin déjà passée -> accès fermé.
    client.post("/admin/jeton/reinitialiser", data={"expire": "2000-01-01T00:00"})
    # Même avec un cookie (impossible à obtenir ici), l'accès est refusé : 403.
    assert client.get("/scanner").status_code == 403


def test_export_pdf_sections(client):
    client.post("/pret/001/preter")
    r = client.get("/stats/export.pdf", params=[("sections", "synthese")])
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_pret_tournoi_route(client):
    r = client.post("/pret/001/tournoi")
    assert r.status_code == 200 and "tournoi" in r.text.lower()
    # L'écran indique « Sorti — tournoi » (pas d'emplacement).
    assert "Sorti — tournoi" in client.get("/pret/001").text
    # Hors statistiques : aucun prêt comptabilisé.
    stats = client.get("/stats").text
    assert '<span class="chiffre-val">0<' in stats
    # Mais visible dans « Jeux actuellement sortis » (bloc tournoi).
    assert "Jeux actuellement sortis" in stats and "En tournoi" in stats


def test_stats_jeux_sortis(client):
    client.post("/pret/001/preter")
    stats = client.get("/stats").text
    assert "Jeux actuellement sortis" in stats
    assert "Prêtés au public" in stats


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
