"""
Petits correctifs de sécurité du lot 14 (pré-production).

- SEC-15 : le chemin de retour du mode rangement se décide par LISTE BLANCHE
  (« /\\hote » passait, comme « //hote » que la spécification URL normalise
  pareil) ;
- jetons non ASCII : `secrets.compare_digest` levait `TypeError` sur deux
  `str` dont l'une n'était pas ASCII — page 500 au lieu de « lien invalide »,
  aux trois endroits qui comparent un jeton ;
- réinitialisation du jeton : une date passée (accès fermé à l'instant) et une
  date illisible (retombée en silence sur sept jours) sont refusées, avec un
  message, comme à la prolongation — et l'ancien jeton reste en vigueur.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import auth

MOT_DE_PASSE = "secret-admin-lot14"
JETON = "jeton-lot14-de-test-bien-long-et-ascii"


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.delenv("PRET_TOKEN", raising=False)
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def client(bases):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def bureau(bases):
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE}, follow_redirects=False)
    return c


def _conn():
    from app.db import get_connection

    return get_connection()


def _poser_jeton(echeance: datetime) -> None:
    """Pose JETON avec son échéance (passée ou non : par l'horloge, voir plus bas)."""
    conn = _conn()
    try:
        vrai = auth._maintenant
        auth._maintenant = lambda: echeance - timedelta(seconds=1)
        try:
            auth.reinitialiser_jeton(conn, echeance.isoformat(timespec="seconds"))
        finally:
            auth._maintenant = vrai
        conn.execute("UPDATE parametres SET valeur = ? WHERE cle = 'pret_token'", (JETON,))
        conn.commit()
    finally:
        conn.close()


def _parametres() -> dict:
    conn = _conn()
    try:
        return dict(conn.execute(
            "SELECT cle, valeur FROM parametres WHERE cle IN ('pret_token', 'pret_token_expire')"
        ).fetchall())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SEC-15 — chemin de retour du mode rangement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("retour, attendu", [
    ("/scanner", "/scanner"),
    ("/jeu/00472", "/jeu/00472"),
    ("/", "/"),
    ("//evil.com", "/scanner"),
    ("/\\evil.com", "/scanner"),
    ("\\/evil.com", "/scanner"),
    ("evil.com", "/scanner"),
    ("https://evil.com", "/scanner"),
    ("/\tevil.com", "/scanner"),
    ("", "/scanner"),
])
def test_quitter_rangement_ne_redirige_que_vers_le_site(client, retour, attendu):
    r = client.post("/scanner/rangement/quitter", data={"retour": retour},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == attendu


def test_garde_de_retour_ne_depend_pas_de_l_encodage_starlette():
    """La garde refuse elle-même « /\\hote », sans compter sur le « %5C »
    qu'ajoute aujourd'hui Starlette à l'en-tête Location."""
    from app.routes.scanner import _retour_interne

    assert not _retour_interne("/\\evil.com")
    assert not _retour_interne("//evil.com")
    assert not _retour_interne("evil.com")
    assert _retour_interne("/scanner")


# ---------------------------------------------------------------------------
# Jetons non ASCII : jamais de page 500
# ---------------------------------------------------------------------------
def test_jetons_egaux_non_ascii():
    assert auth.jetons_egaux("abc", "abc")
    assert not auth.jetons_egaux("é", "abc")
    assert not auth.jetons_egaux("abc", "é")
    assert auth.jetons_egaux("été", "été")


def test_lien_non_ascii_dit_lien_invalide(client):
    _poser_jeton(datetime.now(timezone.utc) + timedelta(days=2))
    r = client.get("/acces", params={"jeton": "é"}, follow_redirects=False)
    assert r.status_code == 403
    assert "n'est pas valide" in r.text


def test_lien_non_ascii_jeton_expire_pas_de_500(client):
    # Jeton expiré : /acces passe par `jeton_expire_reconnu`, la troisième
    # comparaison.
    _poser_jeton(datetime.now(timezone.utc) - timedelta(hours=1))
    r = client.get("/acces", params={"jeton": "é"}, follow_redirects=False)
    assert r.status_code == 403
    assert "n'est pas valide" in r.text


@pytest.mark.parametrize("expire", [False, True], ids=["jeton_valide", "jeton_expire"])
def test_cookie_non_ascii_pas_de_500(client, expire):
    decalage = timedelta(hours=-1) if expire else timedelta(days=2)
    _poser_jeton(datetime.now(timezone.utc) + decalage)
    # En-tête brut : un cookie non ASCII arrive tel quel d'un navigateur.
    r = client.get("/scanner", headers={"Cookie": f"{auth.COOKIE_NAME}=é".encode("utf-8")})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Réinitialisation : date passée et date illisible refusées, rien n'est écrit
# ---------------------------------------------------------------------------
def _saisie_locale(dt: datetime) -> str:
    from app.services import FUSEAU_LOCAL

    return dt.astimezone(FUSEAU_LOCAL).strftime("%Y-%m-%dT%H:%M")


@pytest.mark.parametrize("saisie, phrase", [
    (_saisie_locale(datetime.now(timezone.utc) - timedelta(days=1)), "déjà passée"),
    ("2000-01-01T00:00", "déjà passée"),
    ("31/12/2026 18h", "illisible"),
    ("demain", "illisible"),
])
def test_reinitialisation_refuse_date_passee_ou_illisible(bureau, saisie, phrase):
    _poser_jeton(datetime.now(timezone.utc) + timedelta(days=2))
    avant = _parametres()
    r = bureau.post("/admin/jeton/reinitialiser", data={"expire": saisie},
                    follow_redirects=False)
    assert r.status_code == 200
    assert phrase in r.text
    assert "Le jeton n&#39;a pas changé" in r.text or "Le jeton n'a pas changé" in r.text
    assert _parametres() == avant          # ni jeton ni échéance touchés


def test_reinitialisation_champ_vide_garde_la_duree_par_defaut(bureau):
    _poser_jeton(datetime.now(timezone.utc) + timedelta(days=2))
    r = bureau.post("/admin/jeton/reinitialiser", data={"expire": ""}, follow_redirects=False)
    assert r.status_code == 303
    apres = _parametres()
    assert apres["pret_token"] != JETON
    echeance = datetime.fromisoformat(apres["pret_token_expire"])
    attendu = datetime.now(timezone.utc) + timedelta(days=auth.DUREE_DEFAUT_JOURS)
    assert abs((echeance - attendu).total_seconds()) < 120


def test_reinitialisation_date_a_venir_acceptee(bureau):
    _poser_jeton(datetime.now(timezone.utc) + timedelta(days=2))
    futur = datetime.now(timezone.utc) + timedelta(days=30)
    r = bureau.post("/admin/jeton/reinitialiser", data={"expire": _saisie_locale(futur)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _parametres()["pret_token"] != JETON


def test_reinitialiser_jeton_meme_regle_que_prolonger(bases):
    """Le refus vit dans `auth`, au domicile de la règle de la prolongation."""
    conn = _conn()
    try:
        passe = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        with pytest.raises(ValueError, match="date_passee"):
            auth.reinitialiser_jeton(conn, passe)
        with pytest.raises(ValueError, match="date_absente"):
            auth.reinitialiser_jeton(conn, "pas une date")
        assert auth.jeton_actuel(conn) is None    # rien n'a été écrit
    finally:
        conn.close()
