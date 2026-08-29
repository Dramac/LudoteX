"""
Nom de l'association (`parametres.asso_nom`) et écran « Identité de
l'association ».

Le nom était une constante lue dans le `.env` au démarrage ; il se règle
désormais depuis /admin/identite et vit en base. Quatre familles d'assertions,
dans cet ordre d'importance :

1. **La non-régression d'abord.** Tant que la clé n'a jamais été écrite —
   l'état de tout déploiement existant —, l'application affiche exactement ce
   qu'elle affichait avant : la valeur de `NOM_ASSOCIATION` (.env). C'est la
   raison d'être du repli de second rang.
2. **La cascade complète** dans ses trois cas : base -> .env -> « LudoteX ».
3. **La robustesse.** `nom_association()` est appelée à chaque rendu de page,
   y compris celui de la page d'erreur 500 : une lecture qui échoue ne doit
   jamais faire tomber le rendu.
4. Le reste : écriture/relecture depuis l'écran, effacement, échappement,
   garde admin, journal (une ligne seulement si la valeur change).

Le socle du journal est testé ailleurs (tests/test_journal.py) ; on ne
vérifie ici que la ligne propre à cet écran.
"""

import json

import pytest


MOT_DE_PASSE = "secret-admin-identite"
NOM = "Ludothèque du Bocage"


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
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _connexion(client):
    return client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _poser_nom(client, nom=NOM):
    """Enregistre le nom via la route admin (ouvre la session au besoin)."""
    _connexion(client)
    return client.post("/admin/identite", data={"nom_association": nom})


def _lignes(chemin):
    if not chemin.exists():
        return []
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ---------------------------------------------------------------------------
# 1. NON-RÉGRESSION — la clé n'a jamais été écrite
# ---------------------------------------------------------------------------
def test_sans_reglage_le_nom_est_celui_du_env(client):
    """
    Une base d'avant ce réglage affiche EXACTEMENT ce qu'elle affichait : la
    valeur de `NOM_ASSOCIATION`. Aucun déploiement existant ne change
    d'apparence du seul fait que le réglage existe.
    """
    from app.config import NOM_ASSOCIATION

    for url in ("/", "/catalogue", "/apropos"):
        page = client.get(url)
        assert page.status_code == 200, url
        assert NOM_ASSOCIATION in page.text, url


def test_sans_reglage_le_champ_du_formulaire_reste_vide(client):
    """
    Le champ n'est JAMAIS prérempli avec le repli.

    C'est exactement le défaut qu'a connu /admin/ecran-salle : son champ
    « Titre » prérempli avec la valeur par défaut se figeait en réglage
    explicite dès le premier enregistrement, après quoi plus rien ne pouvait le
    remplacer. Ici, un champ vide doit continuer à vouloir dire « je n'ai rien
    réglé, prends le repli ».
    """
    from app.config import NOM_ASSOCIATION

    _connexion(client)
    page = client.get("/admin/identite")
    assert page.status_code == 200
    assert 'name="nom_association"' in page.text
    assert f'value="{NOM_ASSOCIATION}"' not in page.text

    # Enregistrer la page sans rien saisir ne fige donc rien.
    client.post("/admin/identite", data={"nom_association": ""})
    from app import db, services

    conn = db.get_connection()
    try:
        assert services.lire_parametre(conn, services.CLE_ASSOCIATION_NOM) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. LA CASCADE — base -> .env -> « LudoteX »
# ---------------------------------------------------------------------------
def test_cascade_la_base_l_emporte(client, monkeypatch):
    from app import config, db, services

    monkeypatch.setattr(config, "NOM_ASSOCIATION", "Repli du .env")
    conn = db.get_connection()
    try:
        services.ecrire_parametre(conn, services.CLE_ASSOCIATION_NOM, NOM)
        assert services.lire_nom_association(conn) == NOM
    finally:
        conn.close()
    assert services.nom_association() == NOM


def test_cascade_repli_sur_le_env(client, monkeypatch):
    from app import config, db, services

    monkeypatch.setattr(config, "NOM_ASSOCIATION", "Repli du .env")
    conn = db.get_connection()
    try:
        assert services.lire_nom_association(conn) == "Repli du .env"
    finally:
        conn.close()
    assert services.nom_association() == "Repli du .env"


def test_cascade_dernier_repli_ludotex(monkeypatch):
    """
    Ni base ni variable d'environnement : « LudoteX », le nom du logiciel.

    La valeur est relue depuis `app/config.py` — ce module est le SEUL domicile
    de ce dernier repli, `app/services.py` ne le redéfinit pas.

    `load_dotenv` est neutralisé le temps du rechargement : sans cela, le test
    dépendrait du `.env` de la machine qui l'exécute, où la variable peut très
    bien être posée.
    """
    import importlib

    import dotenv

    from app import config

    monkeypatch.delenv("NOM_ASSOCIATION", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
    try:
        importlib.reload(config)
        assert config.NOM_ASSOCIATION == "LudoteX"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


# ---------------------------------------------------------------------------
# 3. ROBUSTESSE — une lecture qui échoue ne fait pas tomber le rendu
# ---------------------------------------------------------------------------
def test_une_lecture_qui_echoue_retombe_sur_le_repli(client, monkeypatch):
    """
    `nom_association()` ne lève JAMAIS : elle est appelée à chaque rendu, y
    compris celui de la page d'erreur 500, où la base peut être en cause. Une
    exception ici transformerait une panne en boucle d'erreur.
    """
    import sqlite3

    from app import config, db, services

    monkeypatch.setattr(config, "NOM_ASSOCIATION", "Repli du .env")

    def _casse():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "get_connection", _casse)
    assert services.nom_association() == "Repli du .env"


def test_une_lecture_qui_echoue_ne_casse_pas_le_rendu(client, monkeypatch):
    """
    La page reste servie, avec le repli affiché.

    On casse ici la LECTURE elle-même (`lire_nom_association`), pas l'accesseur
    qui l'enveloppe : c'est bien le `try/except` de `nom_association()` qui est
    éprouvé, et à travers lui le context processor qui l'appelle à chaque rendu.
    """
    import sqlite3

    from app import config, services

    monkeypatch.setattr(config, "NOM_ASSOCIATION", "Repli du .env")

    def _casse(conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(services, "lire_nom_association", _casse)
    page = client.get("/aide")
    assert page.status_code == 200
    assert "Repli du .env" in page.text


# ---------------------------------------------------------------------------
# 4. L'ÉCRAN — écriture, effacement, échappement, garde, journal
# ---------------------------------------------------------------------------
def test_ecriture_puis_relecture(client):
    r = _poser_nom(client)
    assert r.status_code == 200
    assert "Nom enregistré." in r.text

    # Relu par l'écran…
    page = client.get("/admin/identite")
    assert f'value="{NOM}"' in page.text
    # …et affiché sur une page publique, sans redémarrage.
    assert NOM in client.get("/catalogue").text


def test_champ_vide_revient_au_repli(client):
    from app.config import NOM_ASSOCIATION

    _poser_nom(client)
    r = client.post("/admin/identite", data={"nom_association": "  "})
    assert r.status_code == 200
    assert "Nom effacé" in r.text
    assert NOM not in client.get("/catalogue").text
    assert NOM_ASSOCIATION in client.get("/catalogue").text


def test_nom_borne_et_normalise(client):
    from app import db, services

    _connexion(client)
    client.post("/admin/identite",
                data={"nom_association": "  Trop   espacé  " + "X" * 200})
    conn = db.get_connection()
    try:
        valeur = services.lire_parametre(conn, services.CLE_ASSOCIATION_NOM)
    finally:
        conn.close()
    assert len(valeur) == services.LONGUEUR_NOM_ASSOCIATION
    assert valeur.startswith("Trop espacé X")


def test_nom_avec_du_html_est_echappe(client):
    """
    Le nom est du texte brut, échappé par Jinja — jamais du HTML libre saisi en
    administration. Il apparaît sur toutes les pages, y compris publiques.
    """
    _poser_nom(client, '<script>alert("x")</script>')
    page = client.get("/catalogue")
    assert page.status_code == 200
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;" in page.text


def test_garde_admin(client):
    """Sans session administrateur : redirection vers /admin, jamais un 403."""
    for methode, args in ((client.get, {}),
                          (client.post, {"data": {"nom_association": NOM}})):
        r = methode("/admin/identite", follow_redirects=False, **args)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin"


def test_lien_depuis_le_tableau_de_bord(client):
    _connexion(client)
    assert '/admin/identite' in client.get("/admin").text


def test_journal_une_ligne_seulement_si_la_valeur_change(client, tmp_path):
    from app import journal

    chemin = journal.chemin_journal()
    _poser_nom(client)
    lignes = [l for l in _lignes(chemin)
              if l.get("action") == "association_nom_modifie"]
    assert len(lignes) == 1
    assert lignes[0]["module"] == "admin"
    assert lignes[0]["qui"] == "admin"
    assert lignes[0]["objet"] == NOM

    # Réenregistrer la même valeur n'écrit rien de plus.
    client.post("/admin/identite", data={"nom_association": NOM})
    assert len([l for l in _lignes(chemin)
                if l.get("action") == "association_nom_modifie"]) == 1

    # L'effacement, lui, en est un.
    client.post("/admin/identite", data={"nom_association": ""})
    lignes = [l for l in _lignes(chemin)
              if l.get("action") == "association_nom_modifie"]
    assert len(lignes) == 2
    assert lignes[-1]["objet"] == "effacé"


# ---------------------------------------------------------------------------
# 5. LES APPELANTS QUI NE SONT PAS DES GABARITS
# ---------------------------------------------------------------------------
def test_le_nom_regle_apparait_dans_les_ics(client):
    """
    Les trois `.ics` travaillent sur une AUTRE base : c'est la route qui lit le
    nom et le transmet en paramètre. Vérifié sur les deux `.ics` que la fixture
    permet de produire sans donnée personnelle.
    """
    _poser_nom(client)
    r = client.post("/tournoi/nouveau",
                    data={"jeu": "Catan", "date_heure": "2026-08-15T14:00"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    ics = client.get(f"/tournoi/{tid}/agenda.ics").text
    assert f"Tournoi — {NOM}" in ics
    assert f"PRODID:-//{NOM}//Tournois//FR" in ics

    client.post("/programme/nouveau",
                data={"intitule": "Initiation au go",
                      "date_heure": "2026-08-15T10:00",
                      "heure_fin": "2026-08-15T11:00"})
    from app.tournoi import db as tdb

    conn = tdb.get_connection()
    try:
        eid = conn.execute(
            "SELECT id_element FROM programme ORDER BY id_element DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()
    client.post(f"/programme/{eid}/etat", data={"etat": "publie"})
    ics = client.get(f"/programme/{eid}/agenda.ics").text
    assert f"Programme — {NOM}" in ics
    assert f"PRODID:-//{NOM}//Programme//FR" in ics


def test_le_nom_regle_apparait_dans_les_exports(client):
    """Les exports de statistiques nomment l'association (feuille « Synthèse »)."""
    import io

    import openpyxl

    _poser_nom(client)
    r = client.get("/stats/export.xlsx")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb["Synthèse"]["A1"].value == f"Statistiques de prêt — {NOM}"


def test_le_nom_regle_apparait_dans_le_partage_du_jeton(client):
    """
    Le message de partage de l'accès bénévole nomme l'association.

    Il faut un jeton pour que la page construise ce message : la fixture n'en
    pose aucun (mode ouvert), on le crée donc explicitement.
    """
    _poser_nom(client)
    client.post("/admin/jeton/reinitialiser", follow_redirects=False)
    page = client.get("/admin/jeton")
    assert page.status_code == 200
    assert f"Accès bénévole — {NOM}" in page.text
