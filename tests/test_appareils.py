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

import re
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
# Étape 5 — purge à la clôture de fin d'événement
# ---------------------------------------------------------------------------
def _vieillir(conn, appareil, jours):
    """Recule artificiellement la dernière activation d'un appareil."""
    vieux = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE appareils SET active_le = ?, "
        "expire_le = CASE WHEN expire_le IS NULL THEN NULL ELSE ? END "
        "WHERE appareil = ?",
        (vieux, vieux, appareil),
    )
    conn.commit()


def test_la_cloture_purge_les_appareils_de_plus_dun_an(conn):
    from app import services

    generation = services.empreinte_jeton("jeton-de-test")
    services.enregistrer_appareil(conn, "VIEUX1", "benevole", _dans(3), generation)
    services.enregistrer_appareil(conn, "RECENT", "benevole", _dans(3), generation)
    services.enregistrer_appareil(conn, "ADMIN1", "admin")      # expire_le NULL
    _vieillir(conn, "VIEUX1", 400)
    _vieillir(conn, "ADMIN1", 400)
    _vieillir(conn, "RECENT", 30)

    services.cloturer_tous_les_prets(conn)

    restants = {r[0] for r in conn.execute("SELECT appareil FROM appareils")}
    # L'appareil d'administration n'a pas d'échéance : c'est sa date
    # d'activation qui sert de repère (COALESCE), sinon il ne partirait jamais.
    assert restants == {"RECENT"}


def test_la_cloture_ne_touche_pas_un_appareil_de_lannee(conn):
    """
    Un bénévole qui revient d'une édition sur l'autre garde son appareil ET
    son libellé : la purge ne vise que ce qui date de plus d'un an.
    """
    from app import services

    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), "gen00001")
    services.renommer_appareil(conn, "AAAAAA", "comptoir 2")
    _vieillir(conn, "AAAAAA", 300)

    services.cloturer_tous_les_prets(conn)

    ligne = conn.execute("SELECT * FROM appareils").fetchone()
    assert ligne["appareil"] == "AAAAAA"
    assert ligne["libelle"] == "comptoir 2"


def test_la_cloture_rend_toujours_le_nombre_de_prets_clotures(conn):
    """Non-régression : la purge est silencieuse, elle ne change pas le
    compte affiché à l'écran de fin d'événement."""
    from app import services

    services.enregistrer_appareil(conn, "VIEUX1", "benevole", _dans(3), "gen00001")
    _vieillir(conn, "VIEUX1", 400)
    services.preter(conn, "001")

    assert services.cloturer_tous_les_prets(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM appareils").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Étape 4 — la liste sur /admin/jeton
# ---------------------------------------------------------------------------
def _connecter_admin(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin"},
                follow_redirects=False)


def test_liste_des_appareils_protegee_par_la_garde_admin(client, conn):
    # Sans session : redirection vers la connexion (motif `_garde`), jamais 403.
    r = client.get("/admin/jeton", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"

    r = client.post("/admin/jeton/appareil/AAAAAA/libelle",
                    data={"libelle": "comptoir 2"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    # Rien n'a été écrit.
    assert conn.execute("SELECT COUNT(*) FROM appareils").fetchone()[0] == 0


def test_seuls_les_appareils_actifs_sont_listes_et_le_compteur_est_juste(client, conn):
    from app import services

    jeton = _poser_jeton(conn)
    generation = services.empreinte_jeton(jeton)
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), generation)
    services.enregistrer_appareil(conn, "BBBBBB", "benevole", _dans(3), generation)
    # Périmés, pour deux raisons différentes.
    services.enregistrer_appareil(conn, "CCCCCC", "benevole", _dans(-1), generation)
    services.enregistrer_appareil(conn, "DDDDDD", "benevole", _dans(3), "vieille01")

    _connecter_admin(client)
    r = client.get("/admin/jeton")
    assert r.status_code == 200

    principal, replies = r.text.split("appareils périmés", 1)
    # Les actifs sont dans le corps de la liste, les périmés dans le repli.
    assert "AAAAAA" in principal and "BBBBBB" in principal
    assert "CCCCCC" not in principal and "DDDDDD" not in principal
    assert "CCCCCC" in replies and "DDDDDD" in replies
    assert "Validité dépassée" in replies and "Jeton renouvelé depuis" in replies

    # Compteur : les deux bénévoles actifs, pas l'appareil admin qui consulte
    # (il est pourtant bien présent dans la liste, en rôle Administration).
    assert "<strong>2</strong>" in r.text
    assert "appareils bénévoles actifs" in r.text
    assert "Administration" in principal


def test_libelle_enregistre_et_reaffiche(client, conn):
    from app import services

    jeton = _poser_jeton(conn)
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3),
                                  services.empreinte_jeton(jeton))
    _connecter_admin(client)

    r = client.post("/admin/jeton/appareil/AAAAAA/libelle",
                    data={"libelle": "comptoir 2"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/jeton"

    r = client.get("/admin/jeton")
    assert 'value="comptoir 2"' in r.text
    # La consigne accompagne le champ, pas seulement le wiki.
    assert "jamais une personne" in r.text


def test_libelle_dun_appareil_inconnu_ne_provoque_rien(client, conn):
    _poser_jeton(conn)
    _connecter_admin(client)
    r = client.post("/admin/jeton/appareil/ZZZZZZ/libelle",
                    data={"libelle": "accueil"}, follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute(
        "SELECT COUNT(*) FROM appareils WHERE appareil = 'ZZZZZZ'"
    ).fetchone()[0] == 0


def test_aucun_controle_de_revocation_dans_le_rendu(client, conn):
    """
    Garde-fou explicite (§6.2). L'authentification bénévole compare le cookie
    au jeton COURANT : il n'existe aucun moyen de couper un appareil seul.
    Un bouton par ligne promettrait un recours que le code n'offre pas — le
    projet a déjà eu à corriger ce type d'erreur en écrivant /admin/aide.
    """
    from app import services

    jeton = _poser_jeton(conn)
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3),
                                  services.empreinte_jeton(jeton))
    _connecter_admin(client)
    page = client.get("/admin/jeton").text.lower()

    for interdit in ("révoqu", "revoqu", "bloquer cet appareil",
                     "déconnecter cet appareil", "supprimer cet appareil"):
        assert interdit not in page, interdit
    # La seule action possible SUR UN APPAREIL est de le nommer.
    par_appareil = re.findall(r'action="(/admin/jeton/appareil/[^"]*)"', page)
    assert par_appareil and all(a.endswith("/libelle") for a in par_appareil), par_appareil
    # Et la seule autre action de la page est la réinitialisation du jeton,
    # qui déconnecte tout le monde — jamais un appareil en particulier.
    autres = [a for a in re.findall(r'action="(/admin/jeton[^"]*)"', page)
              if a not in par_appareil]
    assert autres == ["/admin/jeton/reinitialiser"], autres
    # ... et la page dit explicitement quel est le seul geste possible.
    assert "déconnecte" in page and "tous" in page


def test_aucun_benevole_actif_dit_quoi_faire(client, conn):
    """
    L'appareil qui consulte la page est FORCÉMENT actif (c'est un poste
    d'administration connecté) : le message d'invite ne peut donc pas
    dépendre du tableau, il dépend du compteur BÉNÉVOLE, seul chiffre utile.
    """
    _poser_jeton(conn)
    _connecter_admin(client)
    r = client.get("/admin/jeton")
    assert "<strong>0</strong>" in r.text
    assert "appareil bénévole actif" in r.text     # singulier pour 0, grammaire FR
    assert "Diffusez le lien d'activation" in r.text


def test_apres_une_rotation_la_page_ne_dit_pas_quaucun_appareil_na_active(client, conn):
    """
    Cas très courant : juste après une réinitialisation, tout le monde est
    périmé tant que personne n'a rouvert le lien. Dire « aucun appareil n'a
    activé l'accès » serait faux — le repli en montre plusieurs juste dessous.
    """
    from app import services

    _poser_jeton(conn, "tout-nouveau-jeton")
    services.enregistrer_appareil(conn, "AAAAAA", "benevole", _dans(3), "vieille01")
    _connecter_admin(client)

    r = client.get("/admin/jeton")
    assert "Rediffusez le lien d'activation" in r.text
    assert "Diffusez le lien d'activation ci-dessus : chaque" not in r.text
    assert "AAAAAA" in r.text          # bien listé dans le repli des périmés


def test_le_poste_dadministration_apparait_comme_session_en_cours(client, conn):
    _poser_jeton(conn)
    _connecter_admin(client)
    r = client.get("/admin/jeton")
    assert "Administration" in r.text
    assert "session en cours" in r.text


# ---------------------------------------------------------------------------
# Étape 3 — l'identifiant sur /scanner
# ---------------------------------------------------------------------------
def test_scanner_affiche_lidentifiant_de_lappareil(client, conn):
    from app import services

    jeton = _poser_jeton(conn)
    client.get(f"/acces?jeton={jeton}", follow_redirects=False)
    appareil = client.cookies.get(services.COOKIE_APPAREIL)

    r = client.get("/scanner")
    assert r.status_code == 200
    assert appareil in r.text
    assert "Identifiant de cet appareil" in r.text


def test_scanner_reste_refuse_sans_jeton(client, conn):
    _poser_jeton(conn)
    r = client.get("/scanner")
    assert r.status_code == 403
    assert "Identifiant de cet appareil" not in r.text


def test_scanner_naffiche_rien_quand_aucun_cookie_dappareil(client, conn):
    """Mode ouvert (aucun jeton configuré) : pas de cookie, donc pas de ligne
    « identifiant » vide — on n'affiche jamais une valeur absente."""
    r = client.get("/scanner")
    assert r.status_code == 200
    assert "Identifiant de cet appareil" not in r.text


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
