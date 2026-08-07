"""
Balayage des dictionnaires en mémoire (ROB-05, audit du 24/07/2026).

CE QUE CES TESTS PROTÈGENT
--------------------------
`auth._tentatives` (limitation de débit par IP) et `admin_auth._sessions` /
`_appareils` (sessions d'administration) ne se nettoyaient qu'à la relecture
de la MÊME clé. Une adresse qui ne revient jamais, une session jamais
rouverte — le cas normal, quelqu'un ferme son navigateur — restaient en
mémoire jusqu'au redémarrage du service.

LA PROPRIÉTÉ LA PLUS IMPORTANTE N'EST PAS « ÇA PURGE »
-------------------------------------------------------
C'est « ça purge SANS RIEN CHANGER D'OBSERVABLE ». Un balayage trop zélé
effacerait le compteur d'une adresse encore surveillée, qui repartirait avec
un quota neuf : ce serait un affaiblissement silencieux de la protection
anti-force-brute, déguisé en libération de mémoire. Trois tests portent
spécifiquement là-dessus (`_reste_bloquee`, `_horizon_plus_grande_fenetre`,
`_session_valide_survit`).

PROTECTION « BEST EFFORT », RAPPEL
----------------------------------
Ces deux dispositifs vivent dans la mémoire d'UN process et supposent le
worker unique imposé par `deploy/ludotex.service`. Le balayage ne change rien
à cette limite : il empêche la mémoire de croître, il ne rend pas la
protection partagée entre workers (il faudrait un store commun, cf. Redis
évoqué dans les commentaires des deux modules).
"""

import time

import pytest

from app import admin_auth, auth


# ===========================================================================
# auth._tentatives — limitation de débit par IP
# ===========================================================================
# Note : `_tentatives` est remis à zéro avant CHAQUE test par la fixture
# autouse de tests/conftest.py. `_dernier_balayage` et `_fenetre_max`, eux, ne
# le sont pas — chaque test ci-dessous pose donc explicitement la valeur dont
# il a besoin, plutôt que d'hériter de l'état laissé par un autre.

def test_balayage_supprime_une_ip_qui_ne_revient_jamais():
    auth._fenetre_max = 60
    auth._tentatives["1.2.3.4"] = [time.time() - 10_000]   # de passage, jamais revue
    auth._dernier_balayage = 0.0                           # force le balayage

    auth.trop_de_tentatives("9.9.9.9", limite=60)

    assert "1.2.3.4" not in auth._tentatives
    assert "9.9.9.9" in auth._tentatives                   # l'appelant du moment reste


def test_balayage_epargne_une_ip_encore_dans_la_fenetre():
    auth._fenetre_max = 60
    auth._tentatives["1.2.3.4"] = [time.time() - 5]        # active il y a 5 s
    auth._dernier_balayage = 0.0

    auth.trop_de_tentatives("9.9.9.9", limite=60)

    assert "1.2.3.4" in auth._tentatives


def test_une_ip_bloquee_le_reste_apres_un_balayage():
    """
    LA propriété à ne pas casser : le balayage ne doit pas offrir un quota
    neuf à qui vient d'épuiser le sien.
    """
    auth._fenetre_max = 60
    auth._dernier_balayage = 0.0
    for _ in range(5):
        auth.trop_de_tentatives("6.6.6.6", limite=4)
    assert auth.trop_de_tentatives("6.6.6.6", limite=4) is True

    auth._dernier_balayage = 0.0                           # re-force un balayage
    assert auth.trop_de_tentatives("6.6.6.6", limite=4) is True


def test_horizon_de_purge_est_la_plus_grande_fenetre_vue():
    """
    `fenetre` est un PARAMÈTRE, et le lot D du plan d'action prévoit une
    limite dédiée au login admin, potentiellement plus longue. Purger sur la
    fenêtre de l'appel courant effacerait le compteur d'une adresse encore
    surveillée par un autre appelant.

    Ici : une entrée vieille de 300 s est périmée pour une fenêtre de 60 s,
    mais toujours vivante pour une fenêtre de 3600 s. Elle doit survivre.
    """
    auth._fenetre_max = 0
    auth._dernier_balayage = 0.0
    auth.trop_de_tentatives("admin", limite=5, fenetre=3600)   # déclare la grande fenêtre

    auth._tentatives["surveillee"] = [time.time() - 300]
    auth._dernier_balayage = 0.0
    auth.trop_de_tentatives("autre", limite=60)                # fenêtre par défaut : 60 s

    assert "surveillee" in auth._tentatives


def test_balayage_amorti_pas_a_chaque_appel():
    """Borné : deux appels rapprochés ne déclenchent qu'un seul balayage."""
    auth._fenetre_max = 60
    auth._dernier_balayage = 0.0
    auth.trop_de_tentatives("9.9.9.9", limite=60)              # balaie, et pose la date

    auth._tentatives["perimee"] = [time.time() - 10_000]
    auth.trop_de_tentatives("9.9.9.9", limite=60)              # trop tôt : ne balaie pas

    assert "perimee" in auth._tentatives


def test_balayage_suit_le_dictionnaire_rebranche_par_la_fixture(monkeypatch):
    """
    La fixture autouse de conftest.py REMPLACE `auth._tentatives` par un
    dictionnaire neuf (monkeypatch.setattr). Le balayage doit travailler sur
    le dictionnaire COURANT, pas sur une référence capturée à l'import —
    sinon il nettoierait un objet abandonné pendant que le vrai grossit, et
    la fixture deviendrait inopérante sans que rien ne le signale.
    """
    monkeypatch.setattr(auth, "_tentatives", {"ancienne": [time.time() - 10_000]})
    auth._fenetre_max = 60
    auth._dernier_balayage = 0.0

    auth.trop_de_tentatives("9.9.9.9", limite=60)

    assert "ancienne" not in auth._tentatives
    assert "9.9.9.9" in auth._tentatives


# ===========================================================================
# admin_auth._sessions / _appareils — sessions d'administration
# ===========================================================================

@pytest.fixture
def sessions_isolees():
    """
    Fait travailler le test sur des dictionnaires de session VIDES, puis rend
    leur état d'origine.

    Contrairement à `auth._tentatives`, ils n'ont pas de fixture autouse, et ce
    sont des globaux de process : le reste de la suite ouvre de vraies sessions
    admin (`_login_admin`) qui restent ouvertes ensuite. Sans le vidage,
    `appareils_admin_ouverts()` renverrait aussi leurs appareils et ce fichier
    passerait au vert seul mais échouerait dans la suite complète — c'est
    exactement ce qui s'est produit à la première rédaction. Même principe que
    la fixture autouse de conftest.py : chaque test démarre comme s'il tournait
    seul.
    """
    sessions = dict(admin_auth._sessions)
    appareils = dict(admin_auth._appareils)
    dernier = admin_auth._dernier_balayage
    admin_auth._sessions.clear()
    admin_auth._appareils.clear()
    yield
    admin_auth._sessions.clear()
    admin_auth._sessions.update(sessions)
    admin_auth._appareils.clear()
    admin_auth._appareils.update(appareils)
    admin_auth._dernier_balayage = dernier


def test_balayage_supprime_une_session_expiree(sessions_isolees):
    admin_auth._sessions["morte"] = time.time() - 1
    admin_auth._appareils["morte"] = "AB12CD"
    admin_auth._dernier_balayage = 0.0

    admin_auth.session_valide("un-sid-quelconque")

    assert "morte" not in admin_auth._sessions
    assert "morte" not in admin_auth._appareils        # l'appareil suit sa session


def test_une_session_valide_survit_au_balayage(sessions_isolees):
    """L'autre moitié de la propriété : ne jamais déconnecter qui est en train
    de travailler."""
    sid = admin_auth.ouvrir_session("AB12CD")
    admin_auth._dernier_balayage = 0.0

    assert admin_auth.session_valide(sid) is True
    assert admin_auth.appareils_admin_ouverts() == {"AB12CD"}


def test_balayage_supprime_un_appareil_orphelin(sessions_isolees):
    """Filet : un appareil dont la session a disparu par un autre chemin ne
    doit pas survivre seul en mémoire."""
    admin_auth._appareils["orphelin"] = "FF00FF"
    admin_auth._sessions.pop("orphelin", None)
    admin_auth._dernier_balayage = 0.0

    admin_auth.session_valide("un-sid-quelconque")

    assert "orphelin" not in admin_auth._appareils


def test_ouverture_de_session_balaie_aussi(sessions_isolees):
    """`ouvrir_session` est l'autre point d'accès : une administration qui se
    connecte de temps en temps, sans jamais consulter d'écran gardé, doit
    quand même déclencher le ménage."""
    admin_auth._sessions["morte"] = time.time() - 1
    admin_auth._dernier_balayage = 0.0

    sid = admin_auth.ouvrir_session()

    assert "morte" not in admin_auth._sessions
    assert admin_auth.session_valide(sid) is True
