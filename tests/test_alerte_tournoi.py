"""
Alerte tournoi « rapportez les exemplaires » avant un tournoi
(docs/conception-alerte-tournoi.md, lot 1 de docs/prompt-impl-alerte-tournoi.md).

Trois niveaux, du plus isolé au plus intégré :

1. `tournoi_services.tournoi_a_annoncer` — logique pure sur la base des
   tournois EN MÉMOIRE (patron de `tests/test_tournoi.py::conn`) : délai
   calculé, fenêtre d'affichage, filtre par phase, choix du plus proche.
2. `live.formater_alerte` — substitution des jetons, sans base du tout.
3. `live.alerte_tournoi` puis `/live/data` — le pont entre les deux bases
   (réglages en base de prêt, tournois en base séparée), jusqu'au JSON exposé
   à l'écran de salle. Patron `client` de `tests/test_transfert_routes.py`.

Administration (validation des réglages à l'enregistrement, aperçu, journal,
cohabitation signalée en admin) : hors périmètre, reportée au lot 2.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import services as app_services
from app.routes import live
from app.tournoi import models as tournoi_models
from app.tournoi import services as tournoi_services

FUSEAU_UTC = timezone.utc


def _dans(minutes: float) -> str:
    """Horodatage UTC ISO à `minutes` minutes de maintenant (peut être négatif)."""
    return (datetime.now(FUSEAU_UTC) + timedelta(minutes=minutes)).isoformat()


# ===========================================================================
# 1. tournoi_a_annoncer — base des tournois en mémoire
# ===========================================================================
@pytest.fixture
def conn_t():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in tournoi_models.PRAGMAS:
        c.execute(p)
    for s in tournoi_models.SCHEMA_STATEMENTS:
        c.executescript(s)
    yield c
    c.close()


def _tournoi_a_venir(conn_t, **kwargs) -> int:
    """Crée un tournoi et l'amène en phase 'a_venir' (état 'inscriptions')."""
    tid = tournoi_services.creer_tournoi(conn_t, kwargs.pop("nom", "T"), **kwargs)
    tournoi_services.changer_etat(conn_t, tid, "inscriptions")
    return tid


# --- Délai calculé (D3/D4) ---
def test_delai_plancher_pour_duree_courte(conn_t):
    # duree_min=5 -> 2*5=10, mais le plancher (15) l'emporte.
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=5, date_heure=_dans(15))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is not None
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(16),))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


def test_delai_deux_fois_la_duree_pour_duree_moyenne(conn_t):
    # duree_min=30 -> 2*30=60, entre plancher (15) et plafond (90).
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=30, date_heure=_dans(60))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is not None
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(61),))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


def test_delai_plafond_pour_duree_longue(conn_t):
    # duree_min=120 -> 2*120=240, mais le plafond (90) l'emporte.
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=120, date_heure=_dans(90))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is not None
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(91),))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


def test_delai_plancher_si_duree_absente(conn_t):
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(15))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is not None
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(16),))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


def test_delai_plancher_si_duree_nulle(conn_t):
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=0, date_heure=_dans(15))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is not None
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(16),))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


# --- Fenêtre d'affichage (D5 : [H - délai, H[) ---
def test_rien_juste_avant_la_fenetre(conn_t):
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(21))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 20, 20) is None


def test_alerte_a_l_ouverture_de_la_fenetre(conn_t):
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(20))
    resultat = tournoi_services.tournoi_a_annoncer(conn_t, 20, 20)
    assert resultat is not None
    assert resultat[1] == 20


def test_alerte_une_minute_avant_le_debut(conn_t):
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(1))
    resultat = tournoi_services.tournoi_a_annoncer(conn_t, 20, 20)
    assert resultat is not None
    assert resultat[1] == 1


def test_rien_a_h_tournoi_deja_commence(conn_t):
    # Le tournoi n'a pas été lancé (oubli du bénévole), mais son heure est
    # passée : `tournois_imminents` l'exclut déjà (fenêtre demi-ouverte).
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(-1))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 20, 20) is None


# --- États (seule la phase 'a_venir' alerte, D7) ---
def test_brouillon_n_alerte_jamais(conn_t):
    tournoi_services.creer_tournoi(conn_t, "T", jeu="Catan", duree_min=None,
                                   date_heure=_dans(5))  # reste en 'brouillon'
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


def test_tournoi_lance_n_alerte_jamais(conn_t):
    tid = _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(5))
    tournoi_services.changer_etat(conn_t, tid, "lance")
    # Piège de la note : `tournois_imminents` ne filtre PAS 'lance', c'est
    # `tournoi_a_annoncer` qui doit le faire.
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


def test_tournoi_termine_n_alerte_jamais(conn_t):
    tid = _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(5))
    tournoi_services.changer_etat(conn_t, tid, "termine")
    assert tournoi_services.tournoi_a_annoncer(conn_t, 15, 90) is None


# --- Un seul tournoi annoncé à la fois (D6) ---
def test_seul_le_plus_proche_est_annonce(conn_t):
    _tournoi_a_venir(conn_t, nom="Loin", jeu="Dixit", duree_min=None, date_heure=_dans(10))
    _tournoi_a_venir(conn_t, nom="Proche", jeu="Catan", duree_min=None, date_heure=_dans(5))
    resultat = tournoi_services.tournoi_a_annoncer(conn_t, 15, 90)
    assert resultat is not None
    assert resultat[0]["nom"] == "Proche"


# --- Défensif si maxi < mini (piège 3, refusé à l'enregistrement en lot 2) ---
def test_defensif_si_maxi_inferieur_a_mini(conn_t):
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(25))
    # maxi (10) < mini (30) : la lecture ne doit pas planter, et retombe sur
    # mini (30) pour le délai ET la fenêtre de `tournois_imminents`.
    resultat = tournoi_services.tournoi_a_annoncer(conn_t, 30, 10)
    assert resultat is not None
    assert resultat[1] == 25
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(35),))
    assert tournoi_services.tournoi_a_annoncer(conn_t, 30, 10) is None


# ===========================================================================
# 2. formater_alerte — substitution des jetons, sans base
# ===========================================================================
def test_substitution_complete_des_jetons():
    tournoi = {"jeu": "Catan", "nom": "Tournoi du soir",
               "date_heure": "2026-08-15T18:30:00+00:00", "emplacement": "Salle A"}
    heure_attendue = live._heure_locale(tournoi["date_heure"])
    modele = "Le tournoi de {jeu} commence dans {minutes} minutes à {heure} ({lieu})."
    texte = live.formater_alerte(modele, tournoi, 12)
    assert texte == (
        f"Le tournoi de Catan commence dans 12 minutes à {heure_attendue} (Salle A)."
    )


def test_jeu_vide_replie_sur_l_intitule():
    tournoi = {"jeu": None, "nom": "Tournoi X",
               "date_heure": "2026-08-15T18:30:00+00:00", "emplacement": None}
    assert live.formater_alerte("{jeu}", tournoi, 5) == "Tournoi X"


def test_heure_en_heure_locale_et_non_utc():
    # Mi-août : Europe/Paris est en heure d'été (UTC+2).
    tournoi = {"jeu": "Catan", "nom": "Catan",
               "date_heure": "2026-08-15T20:00:00+00:00", "emplacement": None}
    assert live.formater_alerte("{heure}", tournoi, 5) == "22:00"


def test_accolade_solitaire_ne_casse_pas():
    # str.format() lèverait ici (accolade non refermée / jeton inconnu) : la
    # substitution jeton par jeton ne doit jamais planter sur une saisie
    # bancale (piège 2 de la note).
    tournoi = {"jeu": "Catan", "nom": "Catan",
               "date_heure": "2026-08-15T18:30:00+00:00", "emplacement": None}
    modele = "Une { accolade seule et un {jouer} inconnu, dans {minutes} min"
    texte = live.formater_alerte(modele, tournoi, 7)
    assert texte == "Une { accolade seule et un {jouer} inconnu, dans 7 min"


# ===========================================================================
# 3. alerte_tournoi — le pont entre les deux bases (routes/live.py)
# ===========================================================================
@pytest.fixture
def conn_p():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from app import models as pret_models
    for p in pret_models.PRAGMAS:
        c.execute(p)
    for s in pret_models.SCHEMA_STATEMENTS:
        c.executescript(s)
    yield c
    c.close()


def test_message_absent_aucune_alerte(conn_p, conn_t):
    # Un tournoi qualifie, mais aucun modèle n'est réglé (D10) : le champ n'a
    # pas de défaut implicite, contrairement aux deux délais.
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(5))
    assert live.alerte_tournoi(conn_p, conn_t) is None


def test_alerte_tournoi_bout_en_bout(conn_p, conn_t):
    app_services.ecrire_parametre(conn_p, live.CLE_ALERTE_MESSAGE,
                                  "Rapportez {jeu} au stand !")
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=None, date_heure=_dans(5))
    assert live.alerte_tournoi(conn_p, conn_t) == "Rapportez Catan au stand !"


def test_delais_par_defaut_utilises_si_non_regles(conn_p, conn_t):
    # Aucune des deux clés de délai n'est réglée : DELAI_MIN_DEFAUT (15) et
    # DELAI_MAX_DEFAUT (90) s'appliquent comme si elles étaient en base.
    app_services.ecrire_parametre(conn_p, live.CLE_ALERTE_MESSAGE, "{jeu} !")
    # duree_min=45 -> 2*45=90, pile le plafond par défaut.
    _tournoi_a_venir(conn_t, jeu="Catan", duree_min=45, date_heure=_dans(90))
    assert live.alerte_tournoi(conn_p, conn_t) == "Catan !"
    conn_t.execute("UPDATE tournois SET date_heure = ?", (_dans(91),))
    assert live.alerte_tournoi(conn_p, conn_t) is None


# ===========================================================================
# 4. /live/data — clé absente/présente, cohabitation avec l'annonce libre
# ===========================================================================
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
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _creer_tournoi_qualifiant(tmp_path, monkeypatch, **kwargs):
    """Crée, sur la base de tournois de test, un tournoi en phase 'a_venir'."""
    from app.tournoi import db as tdb

    conn_t = tdb.get_connection()
    try:
        kwargs.setdefault("jeu", "Catan")
        kwargs.setdefault("duree_min", None)
        kwargs.setdefault("date_heure", _dans(5))
        tid = tournoi_services.creer_tournoi(conn_t, kwargs.pop("nom", "T"), **kwargs)
        tournoi_services.changer_etat(conn_t, tid, "inscriptions")
    finally:
        conn_t.close()


def test_live_data_alerte_absente_par_defaut(client):
    assert "alerte_tournoi" not in client.get("/live/data").json()
    r = client.get("/live")
    assert "bandeau-alerte-tournoi" in r.text
    assert '"alerte_tournoi"' not in r.text  # rien dans le JSON embarqué non plus


def test_live_data_alerte_tournoi_presente(client, tmp_path, monkeypatch):
    from app import db as pret_db

    conn = pret_db.get_connection()
    app_services.ecrire_parametre(conn, live.CLE_ALERTE_MESSAGE, "Rapportez {jeu} !")
    conn.close()
    _creer_tournoi_qualifiant(tmp_path, monkeypatch, jeu="Catan")

    assert client.get("/live/data").json()["alerte_tournoi"] == "Rapportez Catan !"


def test_cohabitation_le_json_porte_les_deux(client, tmp_path, monkeypatch):
    from app import db as pret_db

    conn = pret_db.get_connection()
    app_services.ecrire_parametre(conn, live.CLE_ALERTE_MESSAGE, "Rapportez {jeu} !")
    app_services.ecrire_parametre(conn, live.CLE_ANNONCE, "Tombola à 15 h")
    conn.close()
    _creer_tournoi_qualifiant(tmp_path, monkeypatch, jeu="Catan")

    d = client.get("/live/data").json()
    assert d["alerte_tournoi"] == "Rapportez Catan !"
    assert d["annonce"] == "Tombola à 15 h"


def test_module_tournois_desactive_aucune_alerte(client, tmp_path, monkeypatch):
    from app import db as pret_db
    from app.modules import ecrire_etat_module

    conn = pret_db.get_connection()
    app_services.ecrire_parametre(conn, live.CLE_ALERTE_MESSAGE, "Rapportez {jeu} !")
    ecrire_etat_module(conn, "tournois", "desactive")
    conn.close()
    _creer_tournoi_qualifiant(tmp_path, monkeypatch, jeu="Catan")

    assert "alerte_tournoi" not in client.get("/live/data").json()
