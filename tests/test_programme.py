"""
Tests du module « Programme du week-end » — jalon 1 (docs/conception-programme.md) :
schéma, migrations, seed des types, CRUD (types + éléments), machine à états,
`duree_depuis_fin` et la fusion `imminents`.

Aucune route, aucun gabarit à ce stade : uniquement `app/tournoi/models.py`,
`app/tournoi/db.py` et `app/tournoi/programme.py`.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.tournoi import db as tournoi_db
from app.tournoi import models, programme
from app.tournoi import services as tournoi_services


# ===========================================================================
# Schéma + migrations + seed (base réelle temporaire, comme test_rangement.py)
# ===========================================================================
@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Chemin de base temporaire, isolé de la vraie base (monkeypatch TOURNOI_DATABASE_PATH)."""
    chemin = tmp_path / "test-tournoi.db"
    monkeypatch.setattr(tournoi_db, "get_database_path", lambda: chemin)
    return chemin


def test_tables_programme_creees_sur_base_neuve(db_path):
    tournoi_db.init_db()
    conn = tournoi_db.get_connection()
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"types_programme", "programme"} <= tables
    finally:
        conn.close()


def test_seed_types_programme(db_path):
    tournoi_db.init_db()
    conn = tournoi_db.get_connection()
    try:
        lignes = conn.execute(
            "SELECT nom, icone, actif, ordre FROM types_programme ORDER BY ordre"
        ).fetchall()
        noms = [r["nom"] for r in lignes]
        assert noms == [
            "Animation", "Atelier", "Initiation", "Temps fort", "Intervention partenaire",
        ]
        assert all(r["actif"] == 1 for r in lignes)
        assert all(r["icone"] for r in lignes)  # une icône par défaut pour chacun
        assert [r["ordre"] for r in lignes] == [0, 1, 2, 3, 4]
    finally:
        conn.close()


def test_migrations_et_seed_idempotents(db_path):
    tournoi_db.init_db()
    tournoi_db.init_db()  # second passage volontaire : sans effet
    conn = tournoi_db.get_connection()
    try:
        (nb,) = conn.execute("SELECT COUNT(*) FROM types_programme").fetchone()
        assert nb == 5
        # Les tables tournoi existantes ne sont pas perturbées par le second passage.
        colonnes = [r[1] for r in conn.execute("PRAGMA table_info(tournois)")]
        assert "nb_rondes" in colonnes
    finally:
        conn.close()


def test_seed_ne_ressuscite_pas_un_type_supprime(db_path):
    tournoi_db.init_db()
    conn = tournoi_db.get_connection()
    try:
        types = programme.lister_types(conn, actifs_seulement=False)
        premier = types[0]["id_type"]
        assert programme.supprimer_type(conn, premier) is True
        tournoi_db.init_db(conn)  # ré-appel : ne doit pas recréer l'entrée supprimée
        (nb,) = conn.execute("SELECT COUNT(*) FROM types_programme").fetchone()
        assert nb == 4
    finally:
        conn.close()


# ===========================================================================
# Base en mémoire pour les tests de logique (patron test_tournoi.py::conn)
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


def _iso(minutes_depuis_maintenant: float) -> str:
    """Horodatage UTC ISO à `minutes_depuis_maintenant` minutes de maintenant."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_depuis_maintenant)
    return dt.isoformat(timespec="seconds")


# ===========================================================================
# CRUD des types
# ===========================================================================
def test_creer_type_ajoute_en_fin_de_liste(conn):
    id1 = programme.creer_type(conn, "Atelier", icone="🛠️")
    id2 = programme.creer_type(conn, "Temps fort")
    types = programme.lister_types(conn, actifs_seulement=False)
    assert [t["id_type"] for t in types] == [id1, id2]
    assert types[0]["icone"] == "🛠️"
    assert types[1]["icone"] is None


def test_creer_type_nom_vide_ne_cree_rien(conn):
    assert programme.creer_type(conn, "   ") is None
    assert programme.lister_types(conn, actifs_seulement=False) == []


def test_renommer_type_propage_et_met_a_jour_icone(conn):
    id1 = programme.creer_type(conn, "Atelier")
    assert programme.renommer_type(conn, id1, "Grand atelier", icone="🎨") is True
    t = programme.get_type(conn, id1)
    assert t["nom"] == "Grand atelier"
    assert t["icone"] == "🎨"


def test_renommer_type_nom_vide_refuse(conn):
    id1 = programme.creer_type(conn, "Atelier")
    assert programme.renommer_type(conn, id1, "   ") is False
    assert programme.get_type(conn, id1)["nom"] == "Atelier"


def test_archiver_reactiver_type(conn):
    id1 = programme.creer_type(conn, "Atelier")
    programme.archiver_type(conn, id1)
    assert programme.lister_types(conn, actifs_seulement=True) == []
    assert len(programme.lister_types(conn, actifs_seulement=False)) == 1
    programme.reactiver_type(conn, id1)
    assert len(programme.lister_types(conn, actifs_seulement=True)) == 1


def test_supprimer_type_refuse_si_rattache(conn):
    id_type = programme.creer_type(conn, "Atelier")
    programme.creer_element(conn, "Atelier peinture", id_type=id_type)
    assert programme.supprimer_type(conn, id_type) is False
    assert programme.get_type(conn, id_type) is not None


def test_supprimer_type_ok_si_libre(conn):
    id_type = programme.creer_type(conn, "Atelier")
    assert programme.supprimer_type(conn, id_type) is True
    assert programme.get_type(conn, id_type) is None


def test_archiver_ou_supprimer_type_ninflue_pas_sur_un_element_existant(conn):
    id_type = programme.creer_type(conn, "Atelier")
    id_element = programme.creer_element(conn, "Atelier peinture", id_type=id_type)
    programme.archiver_type(conn, id_type)
    # L'élément garde son type et reste consultable.
    e = programme.get_element(conn, id_element)
    assert e["id_type"] == id_type
    assert e["intitule"] == "Atelier peinture"


def test_reordonner_types(conn):
    id1 = programme.creer_type(conn, "A")
    id2 = programme.creer_type(conn, "B")
    id3 = programme.creer_type(conn, "C")
    programme.reordonner_types(conn, id2, "haut")
    ordre = [t["id_type"] for t in programme.lister_types(conn, actifs_seulement=False)]
    assert ordre == [id2, id1, id3]
    # Sans effet en tête de liste.
    programme.reordonner_types(conn, id2, "haut")
    ordre = [t["id_type"] for t in programme.lister_types(conn, actifs_seulement=False)]
    assert ordre == [id2, id1, id3]
    # Sans effet pour un id inconnu.
    programme.reordonner_types(conn, 9999, "bas")


# ===========================================================================
# CRUD des éléments + machine à états
# ===========================================================================
def test_creer_et_get_element(conn):
    id_element = programme.creer_element(
        conn, "Loto des familles", description="Un loto convivial",
        date_heure=_iso(60), duree_min=45, lieu="Table 1",
        public_vise="famille", jauge=20,
    )
    e = programme.get_element(conn, id_element)
    assert e["intitule"] == "Loto des familles"
    assert e["description"] == "Un loto convivial"
    assert e["duree_min"] == 45
    assert e["etat"] == "brouillon"
    assert e["jauge"] == 20


def test_modifier_element(conn):
    id_element = programme.creer_element(conn, "Loto")
    programme.modifier_element(conn, id_element, intitule="Grand loto", jauge=30)
    e = programme.get_element(conn, id_element)
    assert e["intitule"] == "Grand loto"
    assert e["jauge"] == 30


def test_modifier_element_ignore_colonnes_inconnues(conn):
    id_element = programme.creer_element(conn, "Loto")
    programme.modifier_element(conn, id_element, colonne_inexistante="x")  # ne doit pas planter
    assert programme.get_element(conn, id_element)["intitule"] == "Loto"


def test_supprimer_element(conn):
    id_element = programme.creer_element(conn, "Loto")
    programme.supprimer_element(conn, id_element)
    assert programme.get_element(conn, id_element) is None


def test_machine_a_etats_programme(conn):
    id_element = programme.creer_element(conn, "Loto")
    # brouillon -> annule refusé, brouillon -> publie OK.
    assert programme.changer_etat(conn, id_element, "annule") is False
    assert programme.changer_etat(conn, id_element, "publie") is True
    assert programme.get_element(conn, id_element)["etat"] == "publie"
    # publie -> annule OK, annule -> publie OK (réinstauration).
    assert programme.changer_etat(conn, id_element, "annule") is True
    assert programme.changer_etat(conn, id_element, "publie") is True
    # publie -> brouillon OK.
    assert programme.changer_etat(conn, id_element, "brouillon") is True
    # État inconnu refusé.
    assert programme.changer_etat(conn, id_element, "n_importe_quoi") is False
    # Élément absent refusé.
    assert programme.changer_etat(conn, 9999, "publie") is False


def test_lister_elements_filtre_jour_type_brouillons(conn):
    id_type = programme.creer_type(conn, "Atelier")
    autre_type = programme.creer_type(conn, "Animation")
    jour = datetime.now(timezone.utc).date()
    demain = jour + timedelta(days=1)

    id_a = programme.creer_element(conn, "A", id_type=id_type,
                                    date_heure=f"{jour.isoformat()}T10:00:00+00:00")
    id_b = programme.creer_element(conn, "B", id_type=autre_type,
                                    date_heure=f"{jour.isoformat()}T11:00:00+00:00")
    id_c = programme.creer_element(conn, "C", id_type=id_type,
                                    date_heure=f"{demain.isoformat()}T10:00:00+00:00")
    for i in (id_a, id_b, id_c):
        programme.changer_etat(conn, i, "publie")

    # Filtre par jour.
    du_jour = [e["id_element"] for e in programme.lister_elements(conn, jour=jour)]
    assert set(du_jour) == {id_a, id_b}

    # Filtre par type.
    du_type = [e["id_element"] for e in programme.lister_elements(conn, id_type=id_type)]
    assert set(du_type) == {id_a, id_c}

    # Brouillons masqués par défaut.
    id_brouillon = programme.creer_element(conn, "D")
    visibles = [e["id_element"] for e in programme.lister_elements(conn)]
    assert id_brouillon not in visibles
    avec_brouillons = [e["id_element"] for e in programme.lister_elements(conn, inclure_brouillons=True)]
    assert id_brouillon in avec_brouillons


def test_dupliquer_element(conn):
    id_type = programme.creer_type(conn, "Atelier")
    id_element = programme.creer_element(
        conn, "Loto", description="desc", id_type=id_type,
        date_heure=_iso(60), duree_min=30, lieu="Table 2",
        public_vise="tout public", jauge=10,
    )
    programme.changer_etat(conn, id_element, "publie")
    nouvel_horaire = _iso(24 * 60)
    id_copie = programme.dupliquer_element(conn, id_element, nouvel_horaire)
    copie = programme.get_element(conn, id_copie)
    assert copie["intitule"] == "Loto"
    assert copie["id_type"] == id_type
    assert copie["duree_min"] == 30
    assert copie["etat"] == "brouillon"  # repart propre
    assert copie["date_heure"] == nouvel_horaire


def test_dupliquer_element_source_introuvable(conn):
    assert programme.dupliquer_element(conn, 9999) is None


# ===========================================================================
# duree_depuis_fin
# ===========================================================================
def test_duree_depuis_fin_cas_normal():
    debut = "2026-08-01T10:00:00+00:00"
    fin = "2026-08-01T10:45:00+00:00"
    assert programme.duree_depuis_fin(debut, fin) == 45


def test_duree_depuis_fin_fin_avant_debut():
    debut = "2026-08-01T10:00:00+00:00"
    fin = "2026-08-01T09:00:00+00:00"
    assert programme.duree_depuis_fin(debut, fin) is None


def test_duree_depuis_fin_fin_egale_debut():
    debut = "2026-08-01T10:00:00+00:00"
    assert programme.duree_depuis_fin(debut, debut) is None


def test_duree_depuis_fin_bornes_absentes():
    assert programme.duree_depuis_fin(None, "2026-08-01T10:00:00+00:00") is None
    assert programme.duree_depuis_fin("2026-08-01T10:00:00+00:00", None) is None
    assert programme.duree_depuis_fin(None, None) is None


def test_duree_depuis_fin_format_invalide():
    assert programme.duree_depuis_fin("pas une date", "2026-08-01T10:00:00+00:00") is None


# ===========================================================================
# ical_element
# ===========================================================================
def test_ical_element(conn):
    id_element = programme.creer_element(
        conn, "Loto", description="Un loto", date_heure=_iso(60),
        duree_min=45, lieu="Table 3",
    )
    ics = programme.ical_element(conn, id_element)
    assert ics is not None
    assert "SUMMARY:Loto" in ics
    assert "LOCATION:Table 3" in ics


def test_ical_element_sans_date(conn):
    id_element = programme.creer_element(conn, "Loto")
    assert programme.ical_element(conn, id_element) is None


def test_ical_element_introuvable(conn):
    assert programme.ical_element(conn, 9999) is None


# ===========================================================================
# Fusion imminents()
# ===========================================================================
def test_imminents_ordre_chronologique_et_melange_des_sources(conn):
    id_type = programme.creer_type(conn, "Atelier")
    id_prog = programme.creer_element(conn, "Atelier peinture", id_type=id_type,
                                       date_heure=_iso(10))
    programme.changer_etat(conn, id_prog, "publie")

    id_tournoi = tournoi_services.creer_tournoi(conn, "Tournoi Catan", date_heure=_iso(30))
    tournoi_services.changer_etat(conn, id_tournoi, "inscriptions")

    resultat = programme.imminents(conn, 60)
    assert [e["source"] for e in resultat] == ["programme", "tournoi"]
    assert resultat[0]["intitule"] == "Atelier peinture"
    assert resultat[1]["intitule"] == "Tournoi Catan"
    assert resultat[0]["minutes_avant"] <= resultat[1]["minutes_avant"]
    assert resultat[0]["icone"] is None or isinstance(resultat[0]["icone"], str)
    assert resultat[1]["icone"] is None  # pas de champ icône pour un tournoi


def test_imminents_respecte_la_fenetre(conn):
    id_dans_fenetre = programme.creer_element(conn, "Proche", date_heure=_iso(30))
    id_hors_fenetre = programme.creer_element(conn, "Loin", date_heure=_iso(180))
    programme.changer_etat(conn, id_dans_fenetre, "publie")
    programme.changer_etat(conn, id_hors_fenetre, "publie")

    resultat = programme.imminents(conn, 60)
    assert [e["id"] for e in resultat] == [id_dans_fenetre]


def test_imminents_brouillons_exclus(conn):
    programme.creer_element(conn, "Brouillon", date_heure=_iso(10))  # jamais publié
    assert programme.imminents(conn, 60) == []


def test_imminents_annules_exclus_par_defaut_et_inclus_sur_demande(conn):
    id_element = programme.creer_element(conn, "Atelier", date_heure=_iso(10))
    programme.changer_etat(conn, id_element, "publie")
    programme.changer_etat(conn, id_element, "annule")

    assert programme.imminents(conn, 60) == []
    resultat = programme.imminents(conn, 60, inclure_annules=True)
    assert len(resultat) == 1
    assert resultat[0]["etat"] == "annule"


def test_imminents_liste_vide_quand_il_ny_a_rien(conn):
    assert programme.imminents(conn, 60) == []


# ===========================================================================
# fin_iso (jalon 2 : préremplissage du champ « heure de fin » à l'édition)
# ===========================================================================
def test_fin_iso_cas_normal():
    debut = "2026-08-01T10:00:00+00:00"
    assert programme.fin_iso(debut, 45) == "2026-08-01T10:45:00+00:00"


def test_fin_iso_sans_duree():
    assert programme.fin_iso("2026-08-01T10:00:00+00:00", None) is None
    assert programme.fin_iso("2026-08-01T10:00:00+00:00", 0) is None


def test_fin_iso_sans_debut():
    assert programme.fin_iso(None, 45) is None


def test_fin_iso_format_invalide():
    assert programme.fin_iso("pas une date", 45) is None


# ===========================================================================
# grille() — grand horaire publique (jalon 2, patron tournoi.services.planning)
# ===========================================================================
def test_grille_ne_montre_que_les_elements_publies(conn):
    jour = datetime.now(timezone.utc).date()
    id_brouillon = programme.creer_element(conn, "Brouillon", date_heure=f"{jour.isoformat()}T10:00:00+00:00")
    id_publie = programme.creer_element(conn, "Publié", date_heure=f"{jour.isoformat()}T11:00:00+00:00")
    id_annule = programme.creer_element(conn, "Annulé", date_heure=f"{jour.isoformat()}T12:00:00+00:00")
    programme.changer_etat(conn, id_publie, "publie")
    programme.changer_etat(conn, id_annule, "publie")
    programme.changer_etat(conn, id_annule, "annule")

    resultat = programme.grille(conn, [jour])
    assert len(resultat) == 1
    noms = [b["intitule"] for b in resultat[0]["blocs"]]
    assert noms == ["Publié"]


def test_grille_filtre_par_type(conn):
    jour = datetime.now(timezone.utc).date()
    id_type = programme.creer_type(conn, "Atelier")
    autre_type = programme.creer_type(conn, "Animation")
    id_a = programme.creer_element(conn, "A", id_type=id_type,
                                    date_heure=f"{jour.isoformat()}T10:00:00+00:00")
    id_b = programme.creer_element(conn, "B", id_type=autre_type,
                                    date_heure=f"{jour.isoformat()}T11:00:00+00:00")
    programme.changer_etat(conn, id_a, "publie")
    programme.changer_etat(conn, id_b, "publie")

    resultat = programme.grille(conn, [jour], id_type=id_type)
    noms = [b["intitule"] for b in resultat[0]["blocs"]]
    assert noms == ["A"]


def test_grille_jour_vide():
    conn_ = sqlite3.connect(":memory:")
    conn_.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        conn_.execute(p)
    for s in models.SCHEMA_STATEMENTS:
        conn_.executescript(s)
    jour = datetime.now(timezone.utc).date()
    resultat = programme.grille(conn_, [jour])
    conn_.close()
    assert resultat[0]["vide"] is True
    assert resultat[0]["blocs"] == []


# ===========================================================================
# Routes — TestClient avec les DEUX bases temporaires (patron test_tournoi.py)
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")

    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    db.init_db()
    tdb.init_db()
    pdb.init_db()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _connecter_admin(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})


def _regler_date_evenement(client, iso_jour="2026-08-01"):
    """Règle `evenement_date` (base de PRÊT) — ouvre une session admin (au besoin)
    pour poser le réglage ; la session admin reste ouverte ensuite (sans effet
    sur les assertions de ces tests, qui ne portent pas sur le statut visiteur)."""
    _connecter_admin(client)
    client.post("/admin/evenement", data={"date_evenement": iso_jour})


def test_gestion_bloque_sans_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-programme-secret")
    assert client.get("/programme/gestion").status_code == 403
    assert client.get("/programme/nouveau").status_code == 403
    assert client.post("/programme/nouveau", data={"intitule": "X"}).status_code == 403


def test_creation_edition_publication_et_liste(client):
    r = client.post("/programme/nouveau", data={
        "intitule": "Atelier peinture",
        "date_heure": "2026-08-01T10:00",
        "heure_fin": "2026-08-01T11:00",
        "lieu": "Table 1",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/programme/gestion"

    gestion = client.get("/programme/gestion")
    assert gestion.status_code == 200
    assert "Atelier peinture" in gestion.text
    assert "brouillon" in gestion.text

    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_element = conn.execute(
            "SELECT id_element FROM programme WHERE intitule = ?", ("Atelier peinture",)
        ).fetchone()[0]
        # La durée est bien déduite du début/fin saisis (1h -> 60 min).
        duree = conn.execute(
            "SELECT duree_min FROM programme WHERE id_element = ?", (id_element,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert duree == 60

    # Édition.
    r = client.post(f"/programme/{id_element}/editer", data={
        "intitule": "Grand atelier peinture",
        "date_heure": "2026-08-01T10:00",
        "heure_fin": "2026-08-01T12:00",
        "lieu": "Table 1",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Grand atelier peinture" in client.get("/programme/gestion").text

    # Publication.
    r = client.post(f"/programme/{id_element}/etat", data={"etat": "publie"}, follow_redirects=False)
    assert r.status_code == 303
    assert "publie" in client.get("/programme/gestion").text.lower()


def test_creation_intitule_vide_refusee(client):
    r = client.post("/programme/nouveau", data={"intitule": "   "})
    assert r.status_code == 400
    assert "obligatoire" in r.text.lower()


def test_dupliquer_cree_un_brouillon_sans_date_et_ouvre_edition(client):
    r = client.post("/programme/nouveau", data={
        "intitule": "Loto", "date_heure": "2026-08-01T10:00", "heure_fin": "2026-08-01T11:00",
    }, follow_redirects=False)
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_element = conn.execute("SELECT id_element FROM programme").fetchone()[0]
    finally:
        conn.close()

    r = client.post(f"/programme/{id_element}/dupliquer", follow_redirects=False)
    assert r.status_code == 303
    nouvel_id = r.headers["location"].split("/")[2]
    assert r.headers["location"] == f"/programme/{nouvel_id}/editer"

    conn = tdb.get_connection()
    try:
        copie = conn.execute(
            "SELECT intitule, date_heure, etat FROM programme WHERE id_element = ?", (nouvel_id,)
        ).fetchone()
    finally:
        conn.close()
    assert copie["intitule"] == "Loto"
    assert copie["date_heure"] is None
    assert copie["etat"] == "brouillon"


def test_suppression_double_confirmation(client):
    r = client.post("/programme/nouveau", data={"intitule": "À supprimer"}, follow_redirects=False)
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_element = conn.execute("SELECT id_element FROM programme").fetchone()[0]
    finally:
        conn.close()

    page = client.get(f"/programme/{id_element}/supprimer")
    assert page.status_code == 200 and "irréversible" in page.text.lower()

    # Sans la case cochée : rien n'est supprimé, on revient sur la confirmation.
    r = client.post(f"/programme/{id_element}/supprimer", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/programme/{id_element}/supprimer"

    r = client.post(f"/programme/{id_element}/supprimer", data={"confirmation": "oui"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/programme/gestion"
    assert "À supprimer" not in client.get("/programme/gestion").text


def test_aide_programme(client):
    r = client.get("/programme/aide")
    assert r.status_code == 200 and "Aide" in r.text
    assert 'href="/programme/aide"' in client.get("/aide").text


# ===========================================================================
# Admin — CRUD des types (garde, création, archivage, refus de suppression)
# ===========================================================================
def test_programme_types_redirige_sans_session_admin(client):
    r = client.get("/admin/programme-types", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_programme_types_crud(client):
    _connecter_admin(client)
    r = client.post("/admin/programme-types", data={"nom": "Blind-test", "icone": "🎵"},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/admin/programme-types")
    assert "Blind-test" in page.text

    from app.tournoi import db as tdb, programme as prog
    conn = tdb.get_connection()
    try:
        id_type = [t for t in prog.lister_types(conn, actifs_seulement=False)
                   if t["nom"] == "Blind-test"][0]["id_type"]
    finally:
        conn.close()

    # Archivage puis réactivation.
    client.post(f"/admin/programme-types/{id_type}/archiver")
    assert "Archivé" in client.get("/admin/programme-types").text
    client.post(f"/admin/programme-types/{id_type}/reactiver")

    # Suppression refusée si rattaché.
    client.post("/programme/nouveau", data={"intitule": "Session", "id_type": str(id_type)})
    r = client.post(f"/admin/programme-types/{id_type}/supprimer", follow_redirects=False)
    assert "refusée" in r.headers["location"].lower() or "refus" in r.headers["location"].lower()
    assert client.get("/admin/programme-types").text.count("Blind-test") >= 1

    # Un type libre, lui, se supprime.
    client.post("/admin/programme-types", data={"nom": "Libre"})
    conn = tdb.get_connection()
    try:
        id_libre = [t for t in prog.lister_types(conn, actifs_seulement=False)
                    if t["nom"] == "Libre"][0]["id_type"]
    finally:
        conn.close()
    client.post(f"/admin/programme-types/{id_libre}/supprimer")
    assert "Libre" not in client.get("/admin/programme-types").text


def test_programme_types_nom_vide_ne_cree_rien(client):
    _connecter_admin(client)
    r = client.post("/admin/programme-types", data={"nom": "   "}, follow_redirects=False)
    assert "manquant" in r.headers["location"].lower()


# ===========================================================================
# /programme — page publique (grille, filtres, puces, module désactivé)
# ===========================================================================
def test_programme_page_sans_date_evenement(client):
    r = client.get("/programme")
    assert r.status_code == 200
    assert "aucune date" in r.text.lower()


def test_programme_page_affiche_seulement_les_publies(client):
    _regler_date_evenement(client, "2026-08-01")
    client.post("/programme/nouveau", data={
        "intitule": "Brouillon invisible", "date_heure": "2026-08-01T10:00",
    })
    r = client.post("/programme/nouveau", data={
        "intitule": "Loto public", "date_heure": "2026-08-01T14:00",
    }, follow_redirects=False)
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_element = conn.execute(
            "SELECT id_element FROM programme WHERE intitule = ?", ("Loto public",)
        ).fetchone()[0]
    finally:
        conn.close()
    client.post(f"/programme/{id_element}/etat", data={"etat": "publie"})

    page = client.get("/programme")
    assert "Loto public" in page.text
    assert "Brouillon invisible" not in page.text


def test_programme_page_filtre_jour_et_puces(client):
    _regler_date_evenement(client, "2026-08-01")
    r = client.post("/programme/nouveau", data={
        "intitule": "Jour 1", "date_heure": "2026-08-01T10:00",
    }, follow_redirects=False)
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_j1 = conn.execute(
            "SELECT id_element FROM programme WHERE intitule = ?", ("Jour 1",)
        ).fetchone()[0]
    finally:
        conn.close()
    client.post(f"/programme/{id_j1}/etat", data={"etat": "publie"})

    r = client.post("/programme/nouveau", data={
        "intitule": "Jour 2", "date_heure": "2026-08-02T10:00",
    }, follow_redirects=False)
    conn = tdb.get_connection()
    try:
        id_j2 = conn.execute(
            "SELECT id_element FROM programme WHERE intitule = ?", ("Jour 2",)
        ).fetchone()[0]
    finally:
        conn.close()
    client.post(f"/programme/{id_j2}/etat", data={"etat": "publie"})

    page = client.get("/programme?jour=2026-08-01")
    assert "Jour 1" in page.text and "Jour 2" not in page.text
    assert "Retirer ce filtre" in page.text  # puce affichée

    page = client.get("/programme?jour=2026-08-02")
    assert "Jour 2" in page.text and "Jour 1" not in page.text


def test_module_programme_desactive(client):
    from app.db import get_connection as get_pret_connection
    from app.modules import ecrire_etat_module

    conn = get_pret_connection()
    try:
        ecrire_etat_module(conn, "programme", "desactive")
    finally:
        conn.close()

    r = client.get("/programme")
    assert r.status_code == 404
    assert "indisponible" in r.text.lower()
    assert 'href="/programme"' not in client.get("/catalogue").text


# ===========================================================================
# .ics — contenu, 404 sans date, aucune donnée personnelle
# ===========================================================================
def test_ics_programme(client):
    r = client.post("/programme/nouveau", data={
        "intitule": "Loto", "description": "Un loto convivial",
        "date_heure": "2026-08-01T10:00", "heure_fin": "2026-08-01T10:45",
        "lieu": "Table 3",
    }, follow_redirects=False)
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_element = conn.execute("SELECT id_element FROM programme").fetchone()[0]
    finally:
        conn.close()

    r = client.get(f"/programme/{id_element}/agenda.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "SUMMARY:Loto" in r.text
    assert "LOCATION:Table 3" in r.text
    # Aucune donnée personnelle : ni participant, ni organisateur, ni contact.
    assert "ATTENDEE" not in r.text and "ORGANIZER" not in r.text


def test_ics_programme_sans_date_404(client):
    client.post("/programme/nouveau", data={"intitule": "Sans date"})
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        id_element = conn.execute("SELECT id_element FROM programme").fetchone()[0]
    finally:
        conn.close()
    assert client.get(f"/programme/{id_element}/agenda.ics").status_code == 404


def test_ics_programme_introuvable_404(client):
    assert client.get("/programme/9999/agenda.ics").status_code == 404
