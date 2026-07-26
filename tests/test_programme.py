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
