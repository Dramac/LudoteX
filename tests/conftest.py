"""
Fixtures PARTAGÉES par toute la suite de tests.

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

import sqlite3
from datetime import datetime, timedelta

import pytest

from app import auth


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
