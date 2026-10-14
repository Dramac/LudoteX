"""
Fixtures PARTAGÉES par toute la suite de tests.

`_aucun_reseau` (autouse) : aucun test ne parle au réseau, et le jeton
BoardGameGeek d'un `.env` local n'atteint aucun test (voir la fixture).

`_cadence_bgg_simulee` (autouse) : la cadence des appels BoardGameGeek tourne
sur une horloge simulée — aucun test n'attend les 3 s entre deux appels.

`_reinitialiser_limite_debit` (autouse) : `app.auth._tentatives` est un
dictionnaire GLOBAL au process (fenêtre glissante de limitation de débit par
IP, voir app/auth.py::trop_de_tentatives) — volontairement en mémoire, pensé
pour la durée de vie réelle du serveur, pas pour des tests qui s'exécutent en
quelques secondes. Sans remise à zéro, les très nombreux appels à
`/admin/login` et `/acces` cumulés sur TOUTE la suite (des dizaines de
fichiers, TestClient utilisant toujours la même IP factice) finissent par
dépasser la limite par défaut (60/60s) et font échouer des tests plus tard
dans la suite, dans un ORDRE qui n'a rien à voir avec leur propre logique
(constaté : ajouter des tests dans un fichier fait échouer des assertions
dans un autre, sans lien fonctionnel). Chaque test doit démarrer avec un
compteur de tentatives vierge, comme s'il tournait seul.

`_journal_isole` (autouse) : le logger `ludotex.journal` (app/journal.py) est
configuré une fois au démarrage de l'application (app/main.py), donc dès le
premier import du module pendant la collecte des tests — sans cette fixture,
toute la suite écrirait dans le vrai `data/journal.log` du dépôt. Chaque test
repart avec un fichier neuf dans un `tmp_path` dédié, JOURNAL_PATH aligné en
environnement (au cas où du code lirait la variable directement) et le logger
reconfiguré vers ce chemin.

`vieillir_prets` (sur demande) : dans un test, un prêt et son retour sont
séparés de quelques microsecondes — ce qu'un bénévole ne peut pas faire. Un tel
retour est désormais requalifié en ERREUR DE PRÊT (moins d'une minute, voir
services.SEUIL_ERREUR_PRET_S) et sort donc des statistiques. Les tests qui
portent sur un prêt ORDINAIRE doivent donc reculer la date de sortie pour
décrire une situation réelle. On ne neutralise volontairement pas le seuil
(par exemple en le mettant à zéro pour toute la suite) : cela masquerait une
régression sur le comportement lui-même.
"""

import ipaddress
import socket
import sqlite3
from datetime import datetime, timedelta

import pytest

from app import auth


class ConnexionReseauInterdite(RuntimeError):
    """Un test a tenté une vraie connexion réseau (voir `_aucun_reseau`)."""


@pytest.fixture(autouse=True)
def _aucun_reseau(monkeypatch):
    """
    AUCUN TEST NE TOUCHE LE RÉSEAU (lot fiche-2, client BoardGameGeek).

    - `BGG_TOKEN` est retiré de l'environnement : le `.env` local d'un
      développeur le porte, et chaque test qui ouvrirait une fiche reliée
      appellerait BGG pour de vrai (constaté en écrivant le lot). Un test qui
      veut un jeton le pose lui-même, factice.
    - Toute connexion IP vers une autre machine LÈVE
      `ConnexionReseauInterdite`, qui n'est pas une `OSError` : le client BGG
      ne la rattrape pas en « panne », le test échoue bruyamment. La boucle
      locale reste permise (les tests du lanceur interrogent un serveur
      qu'ils démarrent sur 127.0.0.1), comme les sockets non IP.
    """
    monkeypatch.delenv("BGG_TOKEN", raising=False)
    connecter = socket.socket.connect
    connecter_ex = socket.socket.connect_ex

    def _locale(adresse) -> bool:
        hote = adresse[0] if isinstance(adresse, tuple) and adresse else ""
        if hote == "localhost":
            return True
        try:
            return ipaddress.ip_address(hote).is_loopback
        except ValueError:
            return False

    def _filtre(origine):
        def connexion(self, adresse, *args, **kwargs):
            if (self.family in (socket.AF_INET, socket.AF_INET6)
                    and not _locale(adresse)):
                raise ConnexionReseauInterdite(f"connexion vers {adresse!r} refusée en test")
            return origine(self, adresse, *args, **kwargs)
        return connexion

    monkeypatch.setattr(socket.socket, "connect", _filtre(connecter))
    monkeypatch.setattr(socket.socket, "connect_ex", _filtre(connecter_ex))


class HorlogeSimulee:
    """Une horloge qu'on avance à la main ; `dormir` l'avance sans attendre."""

    def __init__(self, depart: float = 1000.0):
        self.t = depart
        self.sommes: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, secondes: float) -> None:
        self.sommes.append(secondes)
        self.t += secondes


@pytest.fixture(autouse=True)
def _cadence_bgg_simulee(monkeypatch):
    """
    La cadence des appels BGG (`app.bgg_api.CADENCE`, lot fiche-3) sur une
    horloge SIMULÉE : elle espace les appels de 3 s, et aucun test ne doit
    attendre pour de vrai. Neuve à chaque test, comme si le processus venait
    de démarrer. Rendue au test qui veut lire ou avancer l'heure.
    """
    from app import bgg_api

    horloge = HorlogeSimulee()
    cadence = bgg_api.Cadence(bgg_api.INTERVALLE_APPELS_S, horloge, horloge.dormir)
    monkeypatch.setattr(bgg_api, "CADENCE", cadence)
    return horloge


@pytest.fixture(autouse=True)
def _reinitialiser_limite_debit(monkeypatch):
    monkeypatch.setattr(auth, "_tentatives", {})


@pytest.fixture(autouse=True)
def _journal_isole(tmp_path, monkeypatch):
    from app import journal

    chemin = tmp_path / "journal-test.log"
    monkeypatch.setenv("JOURNAL_PATH", str(chemin))
    journal.configurer(chemin, console=False)
    yield chemin


@pytest.fixture
def vieillir_prets():
    """
    Recule la `date_sortie` des prêts EN COURS, pour qu'un retour enregistré
    dans la foulée reste un prêt ordinaire et non une erreur de prêt.

    Le décalage se calcule en Python et non en SQL : `datetime()` de SQLite
    rend une chaîne sans « T » ni décalage horaire (« 2026-08-10 11:00:00 »),
    que `datetime.fromisoformat` relit en horodatage NAÏF — le comparer à un
    horodatage aware lèverait plus loin, dans le calcul des durées.

    Returns:
        Une fonction `(conn, secondes=3600) -> None` à appeler entre le prêt
        et le retour.
    """
    def _vieillir(conn: sqlite3.Connection, secondes: int = 3600) -> None:
        lignes = conn.execute(
            "SELECT id_pret, date_sortie FROM prets WHERE date_retour IS NULL"
        ).fetchall()
        for id_pret, date_sortie in [(l[0], l[1]) for l in lignes]:
            recule = (datetime.fromisoformat(date_sortie)
                      - timedelta(seconds=secondes)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE prets SET date_sortie = ? WHERE id_pret = ?", (recule, id_pret)
            )
        conn.commit()

    return _vieillir
