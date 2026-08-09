"""
Nom de l'événement (`parametres.evenement_nom`) et page « Gestion de
l'événement ».

Trois familles d'assertions, dans cet ordre d'importance :

1. **La non-régression d'abord.** Tant que la clé n'a jamais été écrite —
   l'état de toute base existante —, les cinq surfaces concernées (/live,
   /programme, l'accueil, /tournois, les deux .ics) doivent se comporter
   EXACTEMENT comme avant l'introduction du réglage. C'est la règle « nom
   absent = rien du tout », déjà appliquée au rangement et à l'annonce de
   l'écran de salle : jamais de libellé vide, jamais de « aucun nom
   d'événement ».
2. **La cascade du titre de /live** dans ses trois cas.
3. Le reste : écriture/effacement/bornage, garde admin, journal, présence du
   nom dans les deux .ics.

Le socle du journal est testé ailleurs (tests/test_journal.py) ; on ne vérifie
ici que les deux lignes propres à cet écran.
"""

import json

import pytest


MOT_DE_PASSE = "secret-admin-evenement"
NOM = "Festival du Jeu 2026"


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


def _poser_nom(client, nom=NOM, date="2026-08-15"):
    """Enregistre nom et date via la route admin (ouvre la session au besoin)."""
    _connexion(client)
    return client.post(
        "/admin/evenement", data={"nom_evenement": nom, "date_evenement": date}
    )


def _creer_tournoi(client):
    """Crée un tournoi daté et publié, et renvoie son id."""
    r = client.post(
        "/tournoi/nouveau",
        data={"jeu": "Catan", "date_heure": "2026-08-15T14:00"},
        follow_redirects=False,
    )
    id_tournoi = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{id_tournoi}/etat", data={"etat": "inscriptions"})
    return id_tournoi


def _creer_element(client):
    """
    Crée un élément de programme daté et publié, et renvoie son id.

    La création redirige vers `/programme/gestion` (et non vers l'élément) :
    l'id se relit en base, comme le fait déjà tests/test_programme.py.
    """
    client.post(
        "/programme/nouveau",
        data={"intitule": "Initiation au go", "date_heure": "2026-08-15T10:00",
              "heure_fin": "2026-08-15T11:00"},
    )
    from app.tournoi import db as tdb

    conn = tdb.get_connection()
    try:
        id_element = conn.execute(
            "SELECT id_element FROM programme ORDER BY id_element DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()
    client.post(f"/programme/{id_element}/etat", data={"etat": "publie"})
    return id_element


# ---------------------------------------------------------------------------
# 1. NON-RÉGRESSION — sans la clé, rien ne change nulle part
# ---------------------------------------------------------------------------
def test_sans_nom_les_surfaces_publiques_sont_inchangees(client):
    """
    Une base où `evenement_nom` n'a jamais été écrite (toute base existante).
    Aucune des quatre pages ne doit montrer de rappel — ni vide, ni de repli
    du genre « aucun nom d'événement ».
    """
    _connexion(client)
    client.post("/admin/evenement", data={"date_evenement": "2026-08-15"})
    _creer_tournoi(client)
    _creer_element(client)

    for url in ("/", "/programme", "/tournois"):
        page = client.get(url)
        assert page.status_code == 200, url
        assert "rappel-evenement" not in page.text, url

    # /live : le titre reste le nom de l'association, et aucun champ nouveau
    # n'apparaît dans les données.
    data = client.get("/live/data").json()
    assert data["titre"] == "LudoteX"


def test_sans_nom_les_ics_sont_inchanges(client):
    """
    Les deux .ics gardent la description qu'ils avaient : le paramètre
    `nom_evenement` est optionnel et vaut None par défaut.
    """
    _connexion(client)
    client.post("/admin/evenement", data={"date_evenement": "2026-08-15"})
    id_tournoi = _creer_tournoi(client)
    id_element = _creer_element(client)

    ics_t = client.get(f"/tournoi/{id_tournoi}/agenda.ics").text
    assert "DESCRIPTION:Tournoi — LudoteX" in ics_t

    ics_p = client.get(f"/programme/{id_element}/agenda.ics").text
    assert "DESCRIPTION:Programme — LudoteX" in ics_p


def test_sans_nom_le_service_renvoie_none(client):
    """`lire_nom_evenement` renvoie None — pas une chaîne vide, pas un repli."""
    from app import db, services

    conn = db.get_connection()
    try:
        assert services.lire_nom_evenement(conn) is None
    finally:
        conn.close()
    assert services.nom_evenement() is None


# ---------------------------------------------------------------------------
# 2. CASCADE DU TITRE DE /live — trois cas
# ---------------------------------------------------------------------------
def test_cascade_titre_live_sans_rien(client):
    """Ni titre saisi, ni nom d'événement : le nom de l'association."""
    assert client.get("/live/data").json()["titre"] == "LudoteX"


def test_cascade_titre_live_nom_evenement_seul(client):
    """Nom d'événement sans titre saisi : c'est le nom qui s'affiche."""
    _poser_nom(client)
    assert client.get("/live/data").json()["titre"] == NOM
    # Le titre par défaut proposé en admin suit la même cascade.
    page = client.get("/admin/ecran-salle")
    assert NOM in page.text


def test_cascade_titre_live_titre_saisi_l_emporte(client):
    """Un titre saisi l'emporte sur le nom de l'événement, qui l'emporte lui-même
    sur le nom de l'association."""
    _poser_nom(client)
    client.post(
        "/admin/ecran-salle",
        data={"titre": "Grand tournoi du dimanche", "annonce": "",
              "panneau_chiffres": "1", "panneau_tournois": "1",
              "panneau_programme": "1", "panneau_mouvements": "1"},
    )
    assert client.get("/live/data").json()["titre"] == "Grand tournoi du dimanche"

    # Titre vidé : on retombe sur le nom de l'événement, pas sur l'association.
    client.post(
        "/admin/ecran-salle",
        data={"titre": "", "annonce": "", "panneau_chiffres": "1",
              "panneau_tournois": "1", "panneau_programme": "1",
              "panneau_mouvements": "1"},
    )
    assert client.get("/live/data").json()["titre"] == NOM


# ---------------------------------------------------------------------------
# 3. ÉCRAN ADMIN — écriture, effacement, bornage, garde
# ---------------------------------------------------------------------------
def test_post_ecrit_la_cle_et_le_champ_vide_efface(client):
    _poser_nom(client)
    from app import db, services

    conn = db.get_connection()
    try:
        assert services.lire_nom_evenement(conn) == NOM
    finally:
        conn.close()

    # Champ vidé => la clé redevient absente, et le rappel disparaît des pages.
    client.post("/admin/evenement", data={"nom_evenement": "", "date_evenement": ""})
    assert services.nom_evenement() is None
    assert "rappel-evenement" not in client.get("/").text


def test_longueur_bornee_et_espaces_normalises(client):
    """Bornage identique au titre de l'écran de salle : jamais bloquant, on
    tronque et on continue."""
    from app import services

    _connexion(client)
    client.post(
        "/admin/evenement",
        data={"nom_evenement": "  Festival   " + "x" * 200, "date_evenement": ""},
    )
    valeur = services.nom_evenement()
    assert len(valeur) == services.LONGUEUR_NOM_EVENEMENT
    assert valeur.startswith("Festival x")  # espaces multiples normalisés


def test_garde_admin_sur_les_deux_routes(client):
    """Motif `_garde` : redirection vers /admin, pas un 403."""
    for reponse in (
        client.get("/admin/evenement", follow_redirects=False),
        client.post("/admin/evenement", data={"nom_evenement": "X"},
                    follow_redirects=False),
    ):
        assert reponse.status_code == 303
        assert reponse.headers["location"] == "/admin"
    # Et la clé n'a évidemment pas été écrite.
    from app import services

    assert services.nom_evenement() is None


def test_page_gestion_evenement_porte_les_deux_champs_et_les_renvois(client):
    _connexion(client)
    page = client.get("/admin/evenement")
    assert page.status_code == 200
    assert "<h1>Gestion de l'événement</h1>" in page.text
    assert 'name="nom_evenement"' in page.text and 'name="date_evenement"' in page.text
    # Renvois vers les pages qui gardent leurs réglages propres.
    for cible in ("/admin/ecran-salle", "/admin/programme-types", "/planning/admin"):
        assert f'href="{cible}"' in page.text, cible


def test_date_invalide_refuse_tout_et_conserve_la_saisie(client):
    """Une date invalide n'enregistre RIEN — pas même le nom, qu'on croirait
    passé — et le formulaire est réaffiché avec la saisie intacte."""
    from app import services

    _connexion(client)
    r = client.post(
        "/admin/evenement",
        data={"nom_evenement": NOM, "date_evenement": "15 août"},
    )
    assert r.status_code == 400
    assert NOM in r.text and "15 août" in r.text
    assert services.nom_evenement() is None


# ---------------------------------------------------------------------------
# 4. JOURNAL
# ---------------------------------------------------------------------------
def _lignes(chemin, action):
    if not chemin.exists():
        return []
    return [
        json.loads(l)
        for l in chemin.read_text(encoding="utf-8").splitlines()
        if l.strip() and json.loads(l).get("action") == action
    ]


def test_journal_nom_modifie_et_efface(client, _journal_isole):
    _poser_nom(client, date="")
    lignes = _lignes(_journal_isole, "evenement_nom_modifie")
    assert len(lignes) == 1
    assert lignes[0]["module"] == "admin"
    assert lignes[0]["objet"] == NOM
    assert lignes[0]["ok"] is True
    assert lignes[0]["qui"] == "admin"

    client.post("/admin/evenement", data={"nom_evenement": "", "date_evenement": ""})
    assert _lignes(_journal_isole, "evenement_nom_modifie")[-1]["objet"] == "effacé"


def test_journal_pas_de_ligne_quand_la_cle_ne_change_pas(client, _journal_isole):
    """
    Les deux champs voyageant dans le même formulaire, réenregistrer à
    l'identique ne doit produire AUCUNE ligne — sinon régler le nom écrirait
    une « date modifiée » qui ne s'est jamais produite, et réciproquement.
    """
    _poser_nom(client)
    avant_nom = len(_lignes(_journal_isole, "evenement_nom_modifie"))
    avant_date = len(_lignes(_journal_isole, "evenement_date_modifiee"))

    # Même nom, même date : rien de neuf.
    client.post("/admin/evenement",
                data={"nom_evenement": NOM, "date_evenement": "2026-08-15"})
    assert len(_lignes(_journal_isole, "evenement_nom_modifie")) == avant_nom
    assert len(_lignes(_journal_isole, "evenement_date_modifiee")) == avant_date

    # Seul le nom change : une ligne de nom, aucune de date.
    client.post("/admin/evenement",
                data={"nom_evenement": "Autre nom", "date_evenement": "2026-08-15"})
    assert len(_lignes(_journal_isole, "evenement_nom_modifie")) == avant_nom + 1
    assert len(_lignes(_journal_isole, "evenement_date_modifiee")) == avant_date


# ---------------------------------------------------------------------------
# 5. AFFICHAGE — rappel public et .ics
# ---------------------------------------------------------------------------
def test_le_nom_est_rappele_sur_les_quatre_surfaces(client):
    _poser_nom(client)
    id_tournoi = _creer_tournoi(client)
    _creer_element(client)

    for url in ("/", "/programme", "/tournois", f"/tournoi/{id_tournoi}"):
        page = client.get(url)
        assert page.status_code == 200, url
        assert NOM in page.text, url
        assert "rappel-evenement" in page.text, url
        # Le rappel ne remplace jamais le <h1> de la page.
        assert f"<h1>{NOM}</h1>" not in page.text, url


def test_le_nom_apparait_dans_les_deux_ics(client):
    _poser_nom(client)
    id_tournoi = _creer_tournoi(client)
    id_element = _creer_element(client)

    ics_t = client.get(f"/tournoi/{id_tournoi}/agenda.ics").text
    assert f"DESCRIPTION:{NOM} — Tournoi — LudoteX" in ics_t

    ics_p = client.get(f"/programme/{id_element}/agenda.ics").text
    assert f"{NOM} — Programme — LudoteX" in ics_p
