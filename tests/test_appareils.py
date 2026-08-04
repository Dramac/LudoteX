"""
Registre des appareils — lot A du chantier « journal d'activité »
(docs/conception-journal.md §4, étapes 1 et 2 du §11).

Ce que ces tests protègent, dans l'ordre où les pièges se sont présentés :

- le cookie n'est JAMAIS réécrit s'il existe déjà (un bénévole qui rouvre son
  lien d'activation ne doit pas recevoir une nouvelle identité) ;
- une réinitialisation de jeton fait basculer TOUS les bénévoles en périmé
  SANS AUCUNE ÉCRITURE (le calcul se fait à la lecture) ;
- les sessions admin vivent en mémoire du process : après un redémarrage
  (simulé par un vidage du dictionnaire) elles sont vues comme fermées ;
- la page /admin/jeton n'offre AUCUN contrôle de révocation par ligne — il
  n'existe aucun moyen de couper un appareil seul, et promettre le contraire
  serait exactement l'erreur que /admin/aide a déjà eu à corriger.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Fixtures — calquées sur celles de tests/test_routes.py (trois bases
# temporaires, isolées par test).
# ---------------------------------------------------------------------------
@pytest.fixture
def bases(tmp_path, monkeypatch):
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
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def client(bases, monkeypatch):
    # Sessions admin remises à zéro : elles vivent en mémoire du process et
    # survivraient donc d'un test à l'autre.
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin")

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def conn(bases):
    from app import db

    c = db.get_connection()
    yield c
    c.close()


def _poser_jeton(conn, jeton="jeton-de-test", expire_iso=None):
    """Installe un jeton bénévole en base (comme une réinitialisation admin)."""
    for cle, valeur in (("pret_token", jeton), ("pret_token_expire", expire_iso)):
        conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (cle, valeur),
        )
    conn.commit()
    return jeton


def _dans(jours):
    return (datetime.now(timezone.utc) + timedelta(days=jours)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Étape 1 — schéma et migration
# ---------------------------------------------------------------------------
def test_init_db_deux_fois_de_suite_ne_casse_rien(bases, conn):
    from app import db

    db.init_db(conn)
    db.init_db(conn)
    colonnes = [r[1] for r in conn.execute("PRAGMA table_info(appareils)")]
    assert colonnes == [
        "appareil", "role", "active_le", "expire_le", "generation", "libelle"
    ]


def test_une_base_anterieure_au_lot_gagne_la_table(tmp_path, monkeypatch):
    """Une base créée AVANT ce lot (donc sans la table) la reçoit à l'init."""
    from app import db, models

    chemin = tmp_path / "ancienne.db"
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)

    # Base d'époque : tout le schéma SAUF la table appareils.
    vieille = sqlite3.connect(chemin)
    for statement in models.SCHEMA_STATEMENTS:
        if "appareils" not in statement:
            vieille.executescript(statement)
    vieille.commit()
    vieille.close()

    verif = sqlite3.connect(chemin)
    assert not verif.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='appareils'"
    ).fetchone()
    verif.close()

    db.init_db()

    verif = sqlite3.connect(chemin)
    assert verif.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='appareils'"
    ).fetchone()
    verif.close()


# ---------------------------------------------------------------------------
# Étape 1 — services
# ---------------------------------------------------------------------------
def test_identifiant_six_caracteres_hexadecimaux_et_distincts():
    from app import services

    tires = {services.nouvel_appareil() for _ in range(50)}
    assert len(tires) == 50            # collision quasi impossible sur 16,7 M
    for a in tires:
        assert len(a) == 6
        assert a == a.upper()
        int(a, 16)                     # lève si ce n'est pas de l'hexadécimal


def test_empreinte_de_generation_ne_contient_jamais_le_jeton():
    from app import services

    jeton = "un-jeton-bien-secret-et-assez-long"
    empreinte = services.empreinte_jeton(jeton)
    assert empreinte and len(empreinte) == 8
    assert jeton not in empreinte
    # Déterministe, et deux jetons différents donnent deux empreintes différentes.
    assert empreinte == services.empreinte_jeton(jeton)
    assert empreinte != services.empreinte_jeton(jeton + "x")
    # Mode ouvert (aucun jeton configuré) : pas d'empreinte, pas de mensonge.
    assert services.empreinte_jeton(None) is None


def test_appareil_dont_lecheance_est_passee_nest_plus_actif(conn):
    from app import services

    generation = services.empreinte_jeton("jeton-de-test")
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), generation)
    services.enregistrer_appareil(conn, "BBBBBB", "benevole", _dans(-1), generation)

    registre = services.lister_appareils(conn, generation)
    assert [a["appareil"] for a in registre["actifs"]] == ["AAAAAA"]
    assert registre["nb_benevoles_actifs"] == 1
    assert [(p["appareil"], p["motif"]) for p in registre["perimes"]] == [
        ("BBBBBB", "echeance")
    ]


def test_reinitialisation_du_jeton_perime_tout_sans_aucune_ecriture(conn):
    """
    Le point délicat du §4.5 : après une rotation, les appareils basculent
    seuls, PAR LE CALCUL. On le vérifie en comparant le contenu brut de la
    table avant et après la lecture.
    """
    from app import services

    ancienne = services.empreinte_jeton("ancien-jeton")
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), ancienne)
    services.enregistrer_appareil(conn, "BBBBBB", "benevole", _dans(3), ancienne)
    avant = conn.execute("SELECT * FROM appareils ORDER BY appareil").fetchall()

    nouvelle = services.empreinte_jeton("nouveau-jeton")
    registre = services.lister_appareils(conn, nouvelle)

    assert registre["actifs"] == []
    assert registre["nb_benevoles_actifs"] == 0
    assert {p["motif"] for p in registre["perimes"]} == {"jeton_renouvele"}
    # Rien n'a été écrit ni purgé : la table est identique, au bit près.
    apres = conn.execute("SELECT * FROM appareils ORDER BY appareil").fetchall()
    assert [tuple(r) for r in apres] == [tuple(r) for r in avant]


def test_sessions_admin_vues_comme_fermees_apres_vidage_de_la_memoire(conn, monkeypatch):
    from app import admin_auth, services

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    admin_auth.ouvrir_session("CAFE01")
    services.enregistrer_appareil(conn, "CAFE01", "admin")

    registre = services.lister_appareils(conn, None, admin_auth.appareils_admin_ouverts())
    assert [a["appareil"] for a in registre["actifs"]] == ["CAFE01"]
    # Un appareil d'administration ne compte pas dans le total BÉNÉVOLE.
    assert registre["nb_benevoles_actifs"] == 0

    # Redémarrage du service : les sessions en mémoire disparaissent.
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    registre = services.lister_appareils(conn, None, admin_auth.appareils_admin_ouverts())
    assert registre["actifs"] == []
    assert [(p["appareil"], p["motif"]) for p in registre["perimes"]] == [
        ("CAFE01", "session_fermee")
    ]


def test_fermer_la_session_retire_lappareil_des_postes_ouverts(monkeypatch):
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    sid = admin_auth.ouvrir_session("CAFE01")
    assert admin_auth.appareils_admin_ouverts() == {"CAFE01"}
    admin_auth.fermer_session(sid)
    assert admin_auth.appareils_admin_ouverts() == set()


def test_reactivation_met_a_jour_la_generation_sans_dupliquer_ni_perdre_la_date(conn):
    """
    Un bénévole rouvre le lien après une rotation : même appareil, nouvelle
    génération. Sans cette mise à jour, il resterait affiché « périmé » alors
    qu'il vient de se réactiver, et le compteur annoncerait 0.
    """
    from app import services

    ancienne = services.empreinte_jeton("ancien-jeton")
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), ancienne)
    (premiere_activation,) = conn.execute(
        "SELECT active_le FROM appareils WHERE appareil = 'AAAAAA'"
    ).fetchone()

    nouvelle = services.empreinte_jeton("nouveau-jeton")
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(7), nouvelle)

    (nb,) = conn.execute("SELECT COUNT(*) FROM appareils").fetchone()
    assert nb == 1                                  # pas de doublon
    ligne = conn.execute("SELECT * FROM appareils").fetchone()
    assert ligne["generation"] == nouvelle
    # La date affichée reste celle de la PREMIÈRE activation de cet appareil.
    assert ligne["active_le"] == premiere_activation
    assert services.lister_appareils(conn, nouvelle)["nb_benevoles_actifs"] == 1


def test_un_appareil_promu_admin_change_de_role_sans_seconde_ligne(conn):
    """Le téléphone du bureau : d'abord bénévole, puis connexion admin."""
    from app import services

    generation = services.empreinte_jeton("jeton-de-test")
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), generation)
    services.enregistrer_appareil(conn, "AAAAAA", "admin")

    lignes = conn.execute("SELECT * FROM appareils").fetchall()
    assert len(lignes) == 1
    assert lignes[0]["role"] == "admin"


# ---------------------------------------------------------------------------
# Étape 2 — pose du cookie
# ---------------------------------------------------------------------------
def test_activation_benevole_pose_le_cookie_et_cree_une_ligne(client, conn):
    from app import services

    jeton = _poser_jeton(conn)
    r = client.get(f"/acces?jeton={jeton}", follow_redirects=False)
    assert r.status_code == 303

    appareil = client.cookies.get(services.COOKIE_APPAREIL)
    assert appareil and len(appareil) == 6

    ligne = conn.execute("SELECT * FROM appareils").fetchone()
    assert ligne["appareil"] == appareil
    assert ligne["role"] == "benevole"
    assert ligne["generation"] == services.empreinte_jeton(jeton)
    assert ligne["expire_le"] > ligne["active_le"]


def test_le_cookie_nest_pas_reecrit_sil_existe_deja(client, conn):
    """
    Le piège annoncé : un bénévole qui rouvre son lien d'activation ne doit pas
    recevoir une nouvelle identité, sans quoi la liste montrerait cinq
    appareils là où il n'y en a qu'un.
    """
    from app import services

    jeton = _poser_jeton(conn)
    client.get(f"/acces?jeton={jeton}", follow_redirects=False)
    premier = client.cookies.get(services.COOKIE_APPAREIL)

    r = client.get(f"/acces?jeton={jeton}", follow_redirects=False)
    # Aucun nouvel identifiant n'est envoyé au navigateur...
    assert services.COOKIE_APPAREIL not in r.headers.get("set-cookie", "")
    # ... l'appareil ne change pas, et le registre n'a pas gagné de ligne.
    assert client.cookies.get(services.COOKIE_APPAREIL) == premier
    assert conn.execute("SELECT COUNT(*) FROM appareils").fetchone()[0] == 1


def test_deux_clients_obtiennent_deux_identifiants_differents(client, bases, conn):
    from fastapi.testclient import TestClient

    from app import services
    from app.main import app

    jeton = _poser_jeton(conn)
    client.get(f"/acces?jeton={jeton}", follow_redirects=False)

    autre = TestClient(app)
    autre.get(f"/acces?jeton={jeton}", follow_redirects=False)

    a = client.cookies.get(services.COOKIE_APPAREIL)
    b = autre.cookies.get(services.COOKIE_APPAREIL)
    assert a and b and a != b
    assert conn.execute("SELECT COUNT(*) FROM appareils").fetchone()[0] == 2


def test_activation_refusee_ne_pose_ni_cookie_ni_ligne(client, conn):
    from app import services

    _poser_jeton(conn)
    r = client.get("/acces?jeton=mauvais-jeton", follow_redirects=False)
    assert r.status_code == 403
    assert client.cookies.get(services.COOKIE_APPAREIL) is None
    assert conn.execute("SELECT COUNT(*) FROM appareils").fetchone()[0] == 0


def test_connexion_admin_cree_une_ligne_et_ouvre_un_poste(client, conn):
    from app import admin_auth, services

    r = client.post("/admin/login", data={"mot_de_passe": "secret-admin"},
                    follow_redirects=False)
    assert r.status_code == 303

    appareil = client.cookies.get(services.COOKIE_APPAREIL)
    assert appareil and len(appareil) == 6

    ligne = conn.execute("SELECT * FROM appareils").fetchone()
    assert ligne["appareil"] == appareil
    assert ligne["role"] == "admin"
    # Pas d'échéance en base pour un admin : la session en mémoire fait foi.
    assert ligne["expire_le"] is None
    assert admin_auth.appareils_admin_ouverts() == {appareil}


def test_connexion_admin_refusee_ne_pose_rien(client, conn):
    from app import services

    r = client.post("/admin/login", data={"mot_de_passe": "faux"},
                    follow_redirects=False)
    assert r.status_code == 403
    assert client.cookies.get(services.COOKIE_APPAREIL) is None
    assert conn.execute("SELECT COUNT(*) FROM appareils").fetchone()[0] == 0


def test_un_meme_appareil_benevole_puis_admin_reste_une_seule_ligne(client, conn):
    from app import services

    jeton = _poser_jeton(conn)
    client.get(f"/acces?jeton={jeton}", follow_redirects=False)
    appareil = client.cookies.get(services.COOKIE_APPAREIL)

    client.post("/admin/login", data={"mot_de_passe": "secret-admin"},
                follow_redirects=False)
    assert client.cookies.get(services.COOKIE_APPAREIL) == appareil

    lignes = conn.execute("SELECT * FROM appareils").fetchall()
    assert len(lignes) == 1
    assert lignes[0]["role"] == "admin"


def test_libelle_normalise_borne_et_effacable(conn):
    from app import services

    services.enregistrer_appareil(conn, "AAAAAA", "benevole", None, None)

    services.renommer_appareil(conn, "AAAAAA", "  comptoir   2  ")
    assert conn.execute("SELECT libelle FROM appareils").fetchone()[0] == "comptoir 2"

    services.renommer_appareil(conn, "AAAAAA", "x" * 100)
    assert len(conn.execute("SELECT libelle FROM appareils").fetchone()[0]) == 40

    # Saisie vide : on efface (NULL), jamais une chaîne vide — le gabarit n'a
    # ainsi qu'un seul cas d'absence à traiter.
    services.renommer_appareil(conn, "AAAAAA", "   ")
    assert conn.execute("SELECT libelle FROM appareils").fetchone()[0] is None
