"""
Réglage « Inscription aux tournois : visiteurs / bénévoles »
(`parametres.tournoi_inscription`, réglé depuis /admin/evenement).

Deux familles d'assertions, dans cet ordre d'importance :

1. **La non-régression d'abord.** Tant que la clé n'a jamais été écrite —
   l'état de toute base existante — tout se comporte EXACTEMENT comme avant :
   les visiteurs s'inscrivent en ligne. Le réglage ne doit jamais fermer les
   inscriptions par accident, d'où le repli sur « visiteurs » pour toute
   valeur absente ou inattendue.
2. **Ce que voit un visiteur quand le réglage est actif** : la page du tournoi
   reste entière (horaire, lieu, places, agenda, classement) et seul le bouton
   « S'inscrire » cède la place à une orientation. Le bénévole, lui, garde le
   formulaire, et la désinscription par code reste ouverte à tous.

Sans `PRET_TOKEN`, l'application est en MODE OUVERT : `auth.peut_ecrire` est
vrai pour tout le monde, donc personne n'est « visiteur ». Les tests qui
mettent en scène un visiteur configurent donc un jeton, puis retirent le
cookie posé par son activation — patron de `test_stats_pochette_cloisonnee`.
"""

import json

import pytest


MOT_DE_PASSE = "secret-admin-inscription"
JETON = "jeton-inscription-tournoi-xyz"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Trois bases temporaires + un exemplaire, patron de tests/test_routes.py."""
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

    return TestClient(app)


def _creer_tournoi(client):
    """Crée un tournoi et ouvre ses inscriptions ; renvoie son id."""
    r = client.post(
        "/tournoi/nouveau",
        data={"jeu": "Catan", "date_heure": "2026-08-15T14:00", "emplacement": "Salle A",
              "inscription_en_ligne": "on"},
        follow_redirects=False,
    )
    id_tournoi = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{id_tournoi}/etat", data={"etat": "inscriptions"})
    return id_tournoi


def _reserver_aux_benevoles(client, valeur="benevoles"):
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    r = client.post(
        "/admin/evenement",
        data={"nom_evenement": "", "date_evenement": "",
              "inscription_tournoi": valeur},
    )
    assert r.status_code == 200
    return r


def _devenir_visiteur(client, monkeypatch):
    """
    Sort du mode ouvert (jeton configuré) et retire toute trace d'accès :
    cookie bénévole ET session admin. Sans quoi `peut_ecrire` reste vrai et
    aucun test de visiteur ne prouverait quoi que ce soit.
    """
    monkeypatch.setenv("PRET_TOKEN", JETON)
    client.cookies.clear()


# ---------------------------------------------------------------------------
# 1. Non-régression : sans réglage, rien ne change
# ---------------------------------------------------------------------------
def test_par_defaut_les_visiteurs_s_inscrivent(client, monkeypatch):
    id_t = _creer_tournoi(client)
    _devenir_visiteur(client, monkeypatch)

    page = client.get(f"/tournoi/{id_t}")
    assert page.status_code == 200
    assert f'href="/tournoi/{id_t}/inscription"' in page.text
    assert "Inscriptions auprès d'un bénévole" not in page.text
    assert client.get(f"/tournoi/{id_t}/inscription").status_code == 200


def test_une_valeur_inattendue_n_ouvre_pas_le_verrou(client, monkeypatch):
    """
    Le repli est « visiteurs » et jamais « bénévoles » : une clé corrompue ou
    un formulaire trafiqué ne doit pas fermer les inscriptions.
    """
    from app import db, services

    id_t = _creer_tournoi(client)
    conn = db.get_connection()
    try:
        services.ecrire_parametre(conn, services.CLE_INSCRIPTION_TOURNOI, "n'importe quoi")
        assert services.lire_inscription_tournoi(conn) == "visiteurs"
    finally:
        conn.close()
    _devenir_visiteur(client, monkeypatch)

    assert f'href="/tournoi/{id_t}/inscription"' in client.get(f"/tournoi/{id_t}").text


def test_le_formulaire_admin_refuse_une_valeur_inattendue(client):
    from app import db, services

    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    client.post("/admin/evenement",
                data={"nom_evenement": "", "date_evenement": "",
                      "inscription_tournoi": "tout-le-monde"})
    conn = db.get_connection()
    try:
        assert services.lire_inscription_tournoi(conn) == "visiteurs"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Réglage actif : ce que voit un visiteur, ce que garde un bénévole
# ---------------------------------------------------------------------------
def test_le_visiteur_perd_le_bouton_mais_garde_la_page(client, monkeypatch):
    id_t = _creer_tournoi(client)
    _reserver_aux_benevoles(client)
    _devenir_visiteur(client, monkeypatch)

    page = client.get(f"/tournoi/{id_t}")
    assert page.status_code == 200
    assert f'href="/tournoi/{id_t}/inscription"' not in page.text
    assert "Inscriptions auprès d'un bénévole, sur place." in page.text
    # La page reste ENTIÈRE : ce n'est pas une page fermée, c'est une page
    # d'information (c'est tout l'objet du réglage).
    assert "Catan" in page.text
    assert "Salle A" in page.text
    assert "Inscrits" in page.text
    assert f'href="/tournoi/{id_t}/agenda.ics"' in page.text
    # Et surtout pas un message qui laisserait croire que c'est terminé.
    assert "Complet" not in page.text


def test_le_visiteur_est_renvoye_du_formulaire(client, monkeypatch):
    id_t = _creer_tournoi(client)
    _reserver_aux_benevoles(client)
    _devenir_visiteur(client, monkeypatch)

    r = client.get(f"/tournoi/{id_t}/inscription", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/tournoi/{id_t}"


def test_le_post_direct_est_refuse_sans_rien_ecrire(client, monkeypatch):
    """
    Le contrôle est refait au POST : le formulaire peut avoir été ouvert avant
    le changement de réglage, ou l'adresse appelée directement.
    """
    from app.tournoi import db as tdb

    id_t = _creer_tournoi(client)
    _reserver_aux_benevoles(client)
    _devenir_visiteur(client, monkeypatch)

    r = client.post(f"/tournoi/{id_t}/inscription", data={"pseudo": "Intrus"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/tournoi/{id_t}"
    conn = tdb.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0] == 0
    finally:
        conn.close()


def test_le_benevole_garde_le_formulaire(client, monkeypatch):
    """C'est lui qui inscrit au comptoir : le réglage ne doit pas le gêner."""
    id_t = _creer_tournoi(client)
    _reserver_aux_benevoles(client)
    monkeypatch.setenv("PRET_TOKEN", JETON)
    client.cookies.clear()
    client.get("/acces", params={"jeton": JETON})

    assert client.get(f"/tournoi/{id_t}/inscription").status_code == 200
    r = client.post(f"/tournoi/{id_t}/inscription", data={"pseudo": "Alice"})
    assert r.status_code == 200
    assert "Alice" in r.text
    assert f'href="/tournoi/{id_t}/inscription"' in client.get(f"/tournoi/{id_t}").text


def test_la_desinscription_par_code_reste_ouverte(client, monkeypatch):
    """
    Décision Simon : quelqu'un inscrit au comptoir doit pouvoir se désister
    seul, sans remobiliser un bénévole.
    """
    id_t = _creer_tournoi(client)
    monkeypatch.setenv("PRET_TOKEN", JETON)
    client.get("/acces", params={"jeton": JETON})
    inscription = client.post(f"/tournoi/{id_t}/inscription", data={"pseudo": "Bob"})
    from app.tournoi import db as tdb

    conn = tdb.get_connection()
    try:
        code = conn.execute(
            "SELECT code_desinscription FROM inscriptions"
        ).fetchone()[0]
    finally:
        conn.close()
    assert code in inscription.text

    _reserver_aux_benevoles(client)
    client.cookies.clear()          # visiteur pur
    assert client.get("/tournoi/desinscription", params={"code": code}).status_code == 200
    r = client.post("/tournoi/desinscription", data={"code": code})
    assert r.status_code == 200
    conn = tdb.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. L'écran d'administration
# ---------------------------------------------------------------------------
def _coche(html: str, valeur: str) -> bool:
    """L'option `valeur` du groupe radio est-elle cochée dans la page rendue ?"""
    marqueur = f'name="inscription_tournoi" value="{valeur}"'
    assert marqueur in html, f"option {valeur} absente du formulaire"
    return "checked" in html.split(marqueur, 1)[1].split(">", 1)[0]


def test_l_ecran_admin_montre_le_reglage_et_le_conserve(client):
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    page = client.get("/admin/evenement")
    assert page.status_code == 200
    assert 'name="inscription_tournoi"' in page.text
    assert "Réservée aux bénévoles" in page.text
    # Par défaut, « visiteurs » est le choix coché — et lui seul.
    assert _coche(page.text, "visiteurs") is True
    assert _coche(page.text, "benevoles") is False

    _reserver_aux_benevoles(client)
    relu = client.get("/admin/evenement")
    assert _coche(relu.text, "benevoles") is True
    assert _coche(relu.text, "visiteurs") is False
    # Le message de succès appartient au POST : il ne colle pas à la page.
    assert "Inscriptions aux tournois réservées aux bénévoles." not in relu.text


def test_la_garde_admin_protege_le_reglage(client):
    r = client.post("/admin/evenement",
                    data={"nom_evenement": "", "date_evenement": "",
                          "inscription_tournoi": "benevoles"},
                    follow_redirects=False)
    assert r.status_code in (303, 307)
    from app import db, services

    conn = db.get_connection()
    try:
        assert services.lire_inscription_tournoi(conn) == "visiteurs"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Journal — une ligne au changement, aucune sinon
# ---------------------------------------------------------------------------
def _lignes(chemin, action):
    contenu = chemin.read_text(encoding="utf-8") if chemin.exists() else ""
    return [json.loads(l) for l in contenu.splitlines()
            if l.strip() and json.loads(l).get("action") == action]


def test_le_changement_est_journalise_une_seule_fois(client, _journal_isole):
    _reserver_aux_benevoles(client)
    lignes = _lignes(_journal_isole, "inscription_tournoi_modifiee")
    assert len(lignes) == 1
    assert lignes[0]["objet"] == "benevoles"
    assert lignes[0]["ok"] is True

    # Réenregistrer la même valeur ne produit RIEN : les trois réglages
    # voyagent dans le même formulaire, une ligne à chaque envoi serait fausse.
    _reserver_aux_benevoles(client)
    assert len(_lignes(_journal_isole, "inscription_tournoi_modifiee")) == 1

    # Le retour en arrière, lui, est bien tracé.
    _reserver_aux_benevoles(client, "visiteurs")
    lignes = _lignes(_journal_isole, "inscription_tournoi_modifiee")
    assert len(lignes) == 2 and lignes[1]["objet"] == "visiteurs"
