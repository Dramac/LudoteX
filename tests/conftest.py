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
"""

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
