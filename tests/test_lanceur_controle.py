"""
Serveur de contrôle du lanceur local (`lancer.py`, SEC-07, lot 14).

`/status` renvoie l'URL publique du tunnel. Avec `Access-Control-Allow-Origin: *`,
n'importe quelle page ouverte dans le navigateur du poste pouvait la lire, et
une simple image pointée sur `/stop` arrêtait LudoteX. La page du lanceur est
désormais servie par ce serveur (même origine) : plus aucun en-tête CORS,
`Host` vérifié, `/stop` en POST depuis la page seulement.

Le serveur est démarré pour de vrai, sur un port libre, sans uvicorn ni tunnel.
"""

from __future__ import annotations

import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

import lancer


@pytest.fixture
def serveur(monkeypatch):
    arrets = []
    monkeypatch.setattr(lancer, "arreter_tout", lambda: arrets.append(True))
    monkeypatch.setitem(lancer.page_lanceur, "html", "<!DOCTYPE html><p>page du lanceur</p>")
    monkeypatch.setitem(lancer.etat, "url", "https://secret-du-tunnel.trycloudflare.com")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lancer.Controleur)
    # Intervalle court : `shutdown` attend la fin du tour de boucle en cours.
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    port = httpd.server_address[1]
    yield port, arrets
    httpd.shutdown()
    httpd.server_close()


def _requete(port, methode, chemin, en_tetes=None):
    connexion = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        en_tetes = {"Host": f"127.0.0.1:{port}", **(en_tetes or {})}
        connexion.request(methode, chemin, headers=en_tetes)
        reponse = connexion.getresponse()
        return reponse.status, dict(reponse.getheaders()), reponse.read()
    finally:
        connexion.close()


def test_status_sans_aucun_en_tete_cors(serveur):
    port, _ = serveur
    code, en_tetes, corps = _requete(port, "GET", "/status",
                                     {"Origin": "https://site-quelconque.example"})
    assert code == 200
    assert b"secret-du-tunnel" in corps
    assert not any(cle.lower().startswith("access-control-") for cle in en_tetes)


def test_page_du_lanceur_servie_sur_la_racine(serveur):
    port, _ = serveur
    code, en_tetes, corps = _requete(port, "GET", "/")
    assert code == 200
    assert en_tetes["Content-Type"].startswith("text/html")
    assert b"page du lanceur" in corps


@pytest.mark.parametrize("hote", ["secret.attaquant.example:8001", "", "127.0.0.1:1"])
def test_hote_etranger_refuse(serveur, hote):
    """DNS rebinding : un nom de domaine résolu en 127.0.0.1 n'obtient rien."""
    port, _ = serveur
    code, _, corps = _requete(port, "GET", "/status", {"Host": hote})
    assert code == 403
    assert b"secret-du-tunnel" not in corps


def test_get_stop_n_arrete_plus_rien(serveur):
    port, arrets = serveur
    code, _, _ = _requete(port, "GET", "/stop")
    assert code == 405
    assert arrets == []


@pytest.mark.parametrize("origine", [None, "null", "https://site-quelconque.example"])
def test_post_stop_d_une_autre_origine_refuse(serveur, origine):
    port, arrets = serveur
    en_tetes = {"Origin": origine} if origine else {}
    code, _, _ = _requete(port, "POST", "/stop", en_tetes)
    assert code == 403
    assert arrets == []


@pytest.mark.parametrize("hote", ["127.0.0.1", "localhost"])
def test_post_stop_depuis_la_page_arrete(serveur, hote):
    port, arrets = serveur
    code, _, corps = _requete(port, "POST", "/stop",
                              {"Host": f"{hote}:{port}", "Origin": f"http://{hote}:{port}"})
    assert code == 200 and b"arret" in corps
    for _ in range(50):              # l'arrêt part dans un thread
        if arrets:
            break
        threading.Event().wait(0.02)
    assert arrets == [True]


def test_page_appelle_le_controle_en_meme_origine():
    assert 'const CONTROLE = "";' in lancer._PAGE_LANCEUR
    assert 'method: "POST"' in lancer._PAGE_LANCEUR
