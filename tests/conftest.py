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
"""

import pytest

from app import auth


@pytest.fixture(autouse=True)
def _reinitialiser_limite_debit(monkeypatch):
    monkeypatch.setattr(auth, "_tentatives", {})
