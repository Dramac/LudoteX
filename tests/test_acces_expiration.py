"""
Accès bénévole : échéance du jeton — lot-5-pré-production (SEC-12, DOC-02).

Ce que ces tests protègent :

- un jeton EXPIRÉ ne fait plus dire « lien invalide » à une bénévole dont le
  lien est bon, ni « ouvrez le lien d'activation » à un téléphone déjà activé ;
- le 403 d'un module réservé (et la page du module désactivé) ne se confond
  pas avec l'expiration ;
- le bureau est averti avant l'échéance (`auth.SEUIL_EXPIRE_BIENTOT`) ;
- « Prolonger sans changer le lien » n'écrit que l'échéance, refuse une date
  passée, journalise l'échéance et jamais le jeton ;
- LE PIÈGE DU COOKIE : un téléphone activé, puis le jeton prolongé, puis
  l'horloge avancée au-delà de l'ancienne échéance — le téléphone écrit
  toujours, sans rouvrir le lien ;
- la réinitialisation reste la révocation, même après une prolongation.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import auth

MOT_DE_PASSE = "secret-admin"
JETON = "jeton-echeance-de-test-32-caracteres"

# Phrases du gabarit acces_refuse.html, une par motif.
PHRASE_EXPIRE = "pas votre lien"
PHRASE_INVALIDE = "n'est pas valide"
PHRASE_RESERVE = "distribué par l'association"


# ---------------------------------------------------------------------------
# Fixtures — trois bases temporaires (patron de tests/test_appareils.py)
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
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'CATAN')"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def app_test(bases, monkeypatch):
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app.main import app

    return app


@pytest.fixture
def telephone(app_test):
    """Le téléphone d'une bénévole : jamais de session admin."""
    from fastapi.testclient import TestClient

    return TestClient(app_test)


@pytest.fixture
def bureau(app_test):
    """Le poste du bureau, connecté en administration. Client SÉPARÉ : une
    session admin ouvrirait l'accès bénévole et masquerait tout ce qu'on teste."""
    from fastapi.testclient import TestClient

    c = TestClient(app_test)
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE}, follow_redirects=False)
    return c


@pytest.fixture
def conn(bases):
    from app import db

    c = db.get_connection()
    yield c
    c.close()


class Horloge:
    """
    Horloge avançable : décale À LA FOIS l'heure vue par le serveur
    (`auth._maintenant`) et celle du pot à cookies du client de test (le module
    `http.cookiejar` lit `time.time()` pour décider qu'un cookie a expiré). Sans
    ce second décalage, le test ne verrait jamais un navigateur effacer un
    cookie arrivé à échéance — exactement le piège à démontrer.
    """

    def __init__(self, monkeypatch):
        self.decalage = timedelta(0)
        vrai_time = time.time
        monkeypatch.setattr(auth, "_maintenant",
                            lambda: datetime.now(timezone.utc) + self.decalage)
        monkeypatch.setattr(time, "time",
                            lambda: vrai_time() + self.decalage.total_seconds())

    def avancer(self, **duree):
        self.decalage += timedelta(**duree)

    def maintenant(self):
        return auth._maintenant()


@pytest.fixture
def horloge(monkeypatch):
    return Horloge(monkeypatch)


def _poser_jeton(conn, expire_iso):
    """
    Installe le jeton et son échéance, comme une réinitialisation.

    La réinitialisation refuse désormais une échéance passée (lot 14) : un
    jeton EXPIRÉ se fabrique donc par l'horloge — posé une seconde avant son
    échéance, puis l'heure reprend son cours —, jamais en contournant le refus.
    """
    echeance = datetime.fromisoformat(expire_iso)
    horloge_courante = auth._maintenant
    if echeance <= horloge_courante():
        auth._maintenant = lambda: echeance - timedelta(seconds=1)
    try:
        auth.reinitialiser_jeton(conn, expire_iso)
    finally:
        auth._maintenant = horloge_courante
    conn.execute("UPDATE parametres SET valeur = ? WHERE cle = 'pret_token'", (JETON,))
    conn.commit()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _saisie_locale(dt):
    """Valeur d'un champ datetime-local (heure locale, sans fuseau)."""
    from app.services import FUSEAU_LOCAL

    return dt.astimezone(FUSEAU_LOCAL).strftime("%Y-%m-%dT%H:%M")


def _activer(client):
    r = client.get("/acces", params={"jeton": JETON}, follow_redirects=False)
    assert r.status_code == 303
    return r


def _echeance_en_base(conn):
    return auth.expiration_jeton(conn)


def _jeton_en_base(conn):
    return conn.execute(
        "SELECT valeur FROM parametres WHERE cle = 'pret_token'"
    ).fetchone()[0]


# ===========================================================================
# POINT 1 — dire la vérité quand le jeton est expiré
# ===========================================================================
def test_acces_jeton_expire_avec_le_bon_lien_dit_expire(telephone, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() - timedelta(hours=1)))

    r = telephone.get("/acces", params={"jeton": JETON}, follow_redirects=False)

    assert r.status_code == 403
    assert PHRASE_EXPIRE in r.text
    assert PHRASE_INVALIDE not in r.text
    assert "Prévenez le bureau" in r.text
    assert 'href="/catalogue"' in r.text          # le lien public reste là
    assert JETON not in r.text


def test_acces_jeton_expire_avec_un_mauvais_lien_reste_invalide(telephone, conn, horloge):
    """« Ce n'est pas votre lien qui est en cause » serait faux ici."""
    _poser_jeton(conn, _iso(horloge.maintenant() - timedelta(hours=1)))

    r = telephone.get("/acces", params={"jeton": "un-autre-lien"}, follow_redirects=False)

    assert r.status_code == 403
    assert PHRASE_INVALIDE in r.text
    assert PHRASE_EXPIRE not in r.text


def test_page_du_403_dit_expire_a_un_telephone_active(telephone, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    _activer(telephone)
    assert telephone.get("/scanner").status_code == 200

    horloge.avancer(days=3)                        # échéance passée
    r = telephone.get("/scanner")

    assert r.status_code == 403
    assert PHRASE_EXPIRE in r.text
    # La branche par défaut envoyait la bénévole rouvrir son lien : c'est
    # précisément ce qui ne sert à rien.
    assert PHRASE_RESERVE not in r.text


def test_page_du_403_sans_cookie_garde_le_message_habituel(telephone, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() - timedelta(hours=1)))

    r = telephone.get("/scanner")

    assert r.status_code == 403
    assert PHRASE_RESERVE in r.text
    assert PHRASE_EXPIRE not in r.text


def test_module_reserve_sans_cookie_ne_dit_pas_expire(telephone, conn, horloge):
    """Le 403 de `garde_module` : un visiteur n'a rien activé, rien n'a expiré pour lui."""
    from app import services

    services.ecrire_parametre(conn, "module_stats", "benevoles")
    _poser_jeton(conn, _iso(horloge.maintenant() - timedelta(hours=1)))

    r = telephone.get("/stats")

    assert r.status_code == 403
    assert PHRASE_EXPIRE not in r.text
    assert PHRASE_RESERVE in r.text


def test_module_desactive_ne_dit_pas_expire(telephone, conn, horloge):
    """Un module désactivé a sa propre page, même pour un téléphone activé."""
    from app import services

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    _activer(telephone)
    services.ecrire_parametre(conn, "module_stats", "desactive")
    horloge.avancer(days=3)

    r = telephone.get("/stats")

    assert r.status_code == 404
    assert PHRASE_EXPIRE not in r.text


def test_module_reserve_pour_un_telephone_active_dit_expire(telephone, conn, horloge):
    """
    La cause, pas la garde : ce téléphone serait entré sans l'échéance. Lui
    dire « ouvrez le lien d'activation » serait le même mensonge que sur le
    scanner (le carnet de maintenance est « bénévoles » par défaut).
    """
    from app import services

    services.ecrire_parametre(conn, "module_stats", "benevoles")
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    _activer(telephone)
    horloge.avancer(days=3)

    r = telephone.get("/stats")

    assert r.status_code == 403
    assert PHRASE_EXPIRE in r.text


# ===========================================================================
# POINT 2 — avertir le bureau avant l'échéance
# ===========================================================================
def test_echeance_proche_au_seuil(horloge):
    maintenant = horloge.maintenant()
    assert auth.echeance_proche(_iso(maintenant + auth.SEUIL_EXPIRE_BIENTOT - timedelta(seconds=5)))
    assert not auth.echeance_proche(_iso(maintenant + auth.SEUIL_EXPIRE_BIENTOT + timedelta(minutes=1)))
    assert not auth.echeance_proche(_iso(maintenant - timedelta(seconds=5)))   # expiré, pas « proche »
    assert not auth.echeance_proche(None)
    assert not auth.echeance_proche("pas-une-date")


def test_le_seuil_ne_met_pas_en_alerte_un_jeton_tout_neuf():
    """Un avertissement allumé dès la création apprend à ne plus le lire."""
    assert auth.SEUIL_EXPIRE_BIENTOT < timedelta(days=auth.DUREE_DEFAUT_JOURS)


def test_etat_jeton_bientot(conn, horloge):
    from app import supervision

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(hours=10)))
    etat = supervision.etat_jeton(conn)
    assert etat["bientot"] is True and etat["expire"] is False

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=30)))
    etat = supervision.etat_jeton(conn)
    assert etat["bientot"] is False and etat["expire"] is False

    _poser_jeton(conn, _iso(horloge.maintenant() - timedelta(hours=1)))
    etat = supervision.etat_jeton(conn)
    assert etat["bientot"] is False and etat["expire"] is True


def test_expire_bientot_affiche_la_ou_le_bureau_regarde(bureau, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(hours=10)))

    for url in ("/admin/supervision", "/admin", "/admin/jeton"):
        page = bureau.get(url).text
        assert "expire bientôt" in page or "expire le" in page, url
        assert "Attention :" in page, url

    # Le bloc « Jeton » ne dit pas « Ok » en même temps.
    supervision = bureau.get("/admin/supervision").text
    bloc = supervision[supervision.index("Jeton bénévole"):]
    assert "Ok :</strong> jeton valide" not in bloc


def test_jeton_lointain_pas_d_alerte_sur_le_tableau_de_bord(bureau, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=30)))

    page = bureau.get("/admin").text
    assert "Le prolonger sans changer le lien" not in page
    supervision = bureau.get("/admin/supervision").text
    assert "Ok :</strong> jeton valide, jusqu'au" in supervision


# ===========================================================================
# POINT 3 — prolonger sans changer le lien
# ===========================================================================
def test_prolonger_n_ecrit_que_l_echeance(bureau, conn, horloge, _journal_isole):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    nouvelle = horloge.maintenant() + timedelta(days=20)

    r = bureau.post("/admin/jeton/prolonger", data={"expire": _saisie_locale(nouvelle)})

    assert r.status_code == 200
    assert "prolongé" in r.text and "rien à rediffuser" in r.text
    assert _jeton_en_base(conn) == JETON                       # lien inchangé
    assert datetime.fromisoformat(_echeance_en_base(conn)) > horloge.maintenant() + timedelta(days=19)

    lignes = [json.loads(l) for l in _journal_isole.read_text().splitlines() if l]
    ligne = [l for l in lignes if l["action"] == "jeton_prolonge"][-1]
    assert ligne["module"] == "admin" and ligne["qui"] == "admin"
    assert ligne["objet"].startswith("valable jusqu'au ")
    brut = _journal_isole.read_text()
    assert JETON not in brut
    from app import services
    assert services.empreinte_jeton(JETON) not in brut


def test_prolonger_refuse_une_date_passee(bureau, conn, horloge, _journal_isole):
    echeance = _iso(horloge.maintenant() + timedelta(days=2))
    _poser_jeton(conn, echeance)

    r = bureau.post("/admin/jeton/prolonger",
                    data={"expire": _saisie_locale(horloge.maintenant() - timedelta(days=1))})

    assert r.status_code == 200
    assert "déjà passée" in r.text and "a été modifié" in r.text
    assert _echeance_en_base(conn) == echeance
    assert _jeton_en_base(conn) == JETON
    assert "jeton_prolonge" not in _journal_isole.read_text() if _journal_isole.exists() else True


def test_prolonger_refuse_une_date_vide(bureau, conn, horloge):
    echeance = _iso(horloge.maintenant() + timedelta(days=2))
    _poser_jeton(conn, echeance)

    r = bureau.post("/admin/jeton/prolonger", data={"expire": ""})

    assert r.status_code == 200
    assert "Choisissez la nouvelle date" in r.text
    assert _echeance_en_base(conn) == echeance


def test_prolonger_sans_jeton_ne_cree_rien(bureau, conn, horloge):
    r = bureau.post("/admin/jeton/prolonger",
                    data={"expire": _saisie_locale(horloge.maintenant() + timedelta(days=5))})

    assert r.status_code == 200
    assert "rien à prolonger" in r.text
    assert _echeance_en_base(conn) is None
    assert auth.jeton_actuel(conn) is None


def test_prolonger_exige_la_session_admin(telephone, conn, horloge):
    echeance = _iso(horloge.maintenant() + timedelta(days=2))
    _poser_jeton(conn, echeance)
    _activer(telephone)                     # même un téléphone activé

    r = telephone.post("/admin/jeton/prolonger",
                       data={"expire": _saisie_locale(horloge.maintenant() + timedelta(days=30))},
                       follow_redirects=False)

    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert _echeance_en_base(conn) == echeance


def test_le_telephone_continue_apres_l_ancienne_echeance_sans_rouvrir_le_lien(
    telephone, bureau, conn, horloge
):
    """
    LE test d'horloge du lot. Activation, prolongation, horloge avancée au-delà
    de l'ancienne échéance : le téléphone enregistre toujours un prêt, sans
    repasser par /acces. Avant le lot, le navigateur effaçait le cookie à
    l'ancienne échéance — le serveur n'y était pour rien.
    """
    ancienne = horloge.maintenant() + timedelta(days=2)
    _poser_jeton(conn, _iso(ancienne))
    _activer(telephone)

    bureau.post("/admin/jeton/prolonger",
                data={"expire": _saisie_locale(ancienne + timedelta(days=30))})

    horloge.avancer(days=10)                # bien au-delà de l'ancienne échéance
    assert telephone.cookies.get(auth.COOKIE_NAME) == JETON
    r = telephone.post("/pret/001/preter", follow_redirects=False)
    assert r.status_code in (200, 303), r.text[:300]
    assert telephone.get("/scanner").status_code == 200


def test_prolonger_apres_une_echeance_passee_rend_l_acces_sans_rouvrir_le_lien(
    telephone, app_test, conn, horloge
):
    """Le rattrapage du bureau le jour où l'échéance est passée par surprise."""
    from fastapi.testclient import TestClient

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=1)))
    _activer(telephone)

    horloge.avancer(days=2)
    assert telephone.get("/scanner").status_code == 403

    # Le bureau se connecte CE jour-là (une session ouverte deux jours plus
    # tôt aurait expiré).
    bureau = TestClient(app_test)
    bureau.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE}, follow_redirects=False)
    r = bureau.post("/admin/jeton/prolonger",
                data={"expire": _saisie_locale(horloge.maintenant() + timedelta(days=7))})
    assert "prolongé" in r.text

    assert telephone.get("/scanner").status_code == 200


def test_prolonger_realigne_le_registre_des_appareils(telephone, bureau, conn, horloge):
    from app import services

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=1)))
    _activer(telephone)
    appareil = telephone.cookies.get(services.COOKIE_APPAREIL)
    # Un appareil d'une génération précédente ne doit pas être ressuscité.
    services.enregistrer_appareil(conn, "VIEUX1", "benevole",
                                  _iso(horloge.maintenant() + timedelta(days=1)),
                                  services.empreinte_jeton("ancien-jeton"))

    nouvelle = horloge.maintenant() + timedelta(days=30)
    bureau.post("/admin/jeton/prolonger", data={"expire": _saisie_locale(nouvelle)})
    horloge.avancer(days=5)

    registre = services.lister_appareils(conn, services.empreinte_jeton(JETON))
    actifs = {a["appareil"] for a in registre["actifs"]}
    perimes = {a["appareil"]: a["motif"] for a in registre["perimes"]}
    assert appareil in actifs
    assert perimes.get("VIEUX1") == "jeton_renouvele"


def test_reinitialiser_apres_prolongation_revoque_toujours(telephone, bureau, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    _activer(telephone)
    bureau.post("/admin/jeton/prolonger",
                data={"expire": _saisie_locale(horloge.maintenant() + timedelta(days=30))})

    bureau.post("/admin/jeton/reinitialiser",
                data={"expire": _saisie_locale(horloge.maintenant() + timedelta(days=30))})

    assert _jeton_en_base(conn) != JETON
    r = telephone.get("/scanner")
    assert r.status_code == 403
    # Le jeton n'est pas expiré : c'est le lien qui a changé, pas une échéance.
    assert PHRASE_EXPIRE not in r.text
    assert telephone.post("/pret/001/preter", follow_redirects=False).status_code == 403


# ===========================================================================
# Cookies : durée découplée, rafraîchissement, attributs intacts
# ===========================================================================
def _set_cookies(reponse, nom):
    return [v for v in reponse.headers.get_list("set-cookie") if v.startswith(nom + "=")]


def test_activation_pose_des_cookies_longs_et_proteges(telephone, conn, horloge):
    from app import services

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    r = _activer(telephone)

    for nom in (auth.COOKIE_NAME, services.COOKIE_APPAREIL):
        (entete,) = _set_cookies(r, nom)
        entete = entete.lower()
        assert f"max-age={auth.DUREE_COOKIE_JETON}" in entete
        assert "httponly" in entete and "samesite=lax" in entete
        assert "secure" not in entete             # connexion http du client de test


def test_requete_autorisee_repose_le_cookie_avec_les_memes_attributs(app_test, conn, horloge):
    """Un téléphone activé AVANT le lot porte un cookie qui meurt à l'ancienne
    échéance : la première requête autorisée le repose, en HTTPS avec Secure."""
    from fastapi.testclient import TestClient

    from app import services

    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    telephone = TestClient(app_test, base_url="https://testserver")
    telephone.cookies.set(auth.COOKIE_NAME, JETON)
    telephone.cookies.set(services.COOKIE_APPAREIL, "ABC123")

    r = telephone.get("/scanner")

    assert r.status_code == 200
    (jeton,) = _set_cookies(r, auth.COOKIE_NAME)
    (appareil,) = _set_cookies(r, services.COOKIE_APPAREIL)
    assert appareil.startswith("appareil=ABC123;")          # identité inchangée
    for entete in (jeton.lower(), appareil.lower()):
        assert f"max-age={auth.DUREE_COOKIE_JETON}" in entete
        assert "httponly" in entete and "samesite=lax" in entete and "secure" in entete


def test_un_cookie_refuse_n_est_jamais_repose(telephone, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=2)))
    telephone.cookies.set(auth.COOKIE_NAME, "ancien-jeton")

    r = telephone.get("/scanner")

    assert r.status_code == 403
    assert not _set_cookies(r, auth.COOKIE_NAME)


def test_un_jeton_expire_n_est_jamais_repose(telephone, conn, horloge):
    _poser_jeton(conn, _iso(horloge.maintenant() + timedelta(days=1)))
    _activer(telephone)
    horloge.avancer(days=2)

    r = telephone.get("/scanner")

    assert r.status_code == 403
    assert not _set_cookies(r, auth.COOKIE_NAME)


def test_registre_sans_echeance_pour_un_jeton_sans_echeance(telephone, conn, monkeypatch):
    """Jeton amorcé par le .env, sans date : le registre ne s'invente pas d'échéance."""
    monkeypatch.setenv("PRET_TOKEN", JETON)

    _activer(telephone)

    ligne = conn.execute("SELECT expire_le, generation FROM appareils").fetchone()
    assert ligne["expire_le"] is None and ligne["generation"] is not None
