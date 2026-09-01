"""
Délai d'attente du verrou d'écriture — les TROIS bases, pas seulement le prêt.

CE QUE CES TESTS PROTÈGENT
--------------------------
La session du 2 août 2026 a rendu explicite, sur la base de PRÊT, la patience
d'un écrivain face au verrou SQLite : `db.TIMEOUT_ECRITURE_S = 15.0`, passée à
`sqlite3.connect`. Les deux autres bases (tournois, planning) sont restées sur
le défaut IMPLICITE du module `sqlite3` — 5 s, subi plutôt que choisi : un
oubli plutôt qu'un choix, personne n'avait harmonisé le délai des trois bases
avant ce correctif.

L'écart comptait : un tournoi qui remplit ses inscriptions le jour J
(`tournoi/services.py::inscrire` s'ouvre en `BEGIN IMMEDIATE` depuis le 2 août,
donc les écrivains s'attendent les uns les autres) abandonnait au bout de 5 s
là où un prêt patiente 15 s, sans que rien ne le signale.

DEUX NIVEAUX DE TEST, VOLONTAIREMENT
-------------------------------------
- `test_les_trois_bases_passent_le_meme_delai` constate la VALEUR réellement
  transmise à `sqlite3.connect`. C'est le seul moyen de vérifier 15 s plutôt
  que 5 s sans faire patienter la suite quinze secondes.
- `test_le_delai_est_bien_celui_de_la_constante` constate que la constante est
  effectivement CÂBLÉE à la connexion, pas seulement déclarée : en l'abaissant,
  l'abandon doit survenir tout de suite. Sans le correctif, la connexion
  ignorerait la constante et attendrait les 5 s du défaut implicite — l'écart
  de temps est ce qui rend ce test discriminant.

POURQUOI DES BASES SUR FICHIER
-------------------------------
Deux connexions `:memory:` ouvrent deux bases DIFFÉRENTES et ne se disputent
donc aucun verrou (même raison que tests/test_concurrence_pochettes.py).
"""

import sqlite3
import time

import pytest

from app import db as db_pret
from app.planning import db as db_planning
from app.tournoi import db as db_tournoi


# Délai d'attente TRÈS court pour le test de câblage : on veut constater un
# abandon, pas patienter les 15 s de production.
TIMEOUT_COURT_S = 0.3

# Les trois modules d'accès aux bases, sous la forme (libellé, module, variable
# d'environnement portant le chemin). Le libellé sert aux messages d'échec.
BASES = [
    ("prêt", db_pret, "DATABASE_PATH"),
    ("tournois", db_tournoi, "TOURNOI_DATABASE_PATH"),
    ("planning", db_planning, "PLANNING_DATABASE_PATH"),
]


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Les trois bases sur fichier, schéma courant, dans un dossier temporaire."""
    for libelle, module, variable in BASES:
        monkeypatch.setenv(variable, str(tmp_path / f"{libelle}.db"))
        module.init_db()
    return tmp_path


def test_les_trois_bases_passent_le_meme_delai(bases, monkeypatch):
    """
    Les trois `get_connection()` transmettent `db.TIMEOUT_ECRITURE_S` — la
    valeur de production, identique partout.

    Test STRUCTUREL assumé : `timeout` n'est pas relisible depuis un objet
    `sqlite3.Connection`, et le seul moyen de distinguer 15 s de 5 s par le
    comportement serait d'attendre quinze secondes. On intercepte donc l'appel.
    """
    captures: dict[int, float | None] = {}
    vrai_connect = sqlite3.connect

    def connect_espionne(*args, **kwargs):
        conn = vrai_connect(*args, **kwargs)
        captures[id(conn)] = kwargs.get("timeout")
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect_espionne)

    for libelle, module, _variable in BASES:
        conn = module.get_connection()
        try:
            assert captures[id(conn)] == db_pret.TIMEOUT_ECRITURE_S, (
                f"La base « {libelle} » n'ouvre pas ses connexions avec le délai "
                "d'attente commun : elle subirait le défaut implicite de 5 s du "
                "module sqlite3."
            )
        finally:
            conn.close()


@pytest.mark.parametrize("libelle,module", [(l, m) for l, m, _ in BASES])
def test_le_delai_est_bien_celui_de_la_constante(bases, monkeypatch, libelle, module):
    """
    Abaisser la constante d'un module raccourcit RÉELLEMENT l'attente de ses
    connexions : la valeur est câblée, pas seulement déclarée.

    ⚠️ On remplace la constante DANS LE MODULE TESTÉ, pas dans `app.db` :
    `tournoi/db.py` et `planning/db.py` font un `from app.db import
    TIMEOUT_ECRITURE_S`, qui LIE la valeur dans leur propre espace de noms à
    l'import. Patcher `app.db.TIMEOUT_ECRITURE_S` n'aurait aucun effet sur eux —
    sans conséquence en production (une constante de réglage ne change pas en
    cours d'exécution), mais c'est le genre de détail qui fait écrire un test
    qui ne teste rien.
    """
    monkeypatch.setattr(module, "TIMEOUT_ECRITURE_S", TIMEOUT_COURT_S)

    # Un premier écrivain prend le verrou et ne le rend pas.
    bloqueur = sqlite3.connect(module.get_database_path())
    bloqueur.execute("BEGIN IMMEDIATE")
    try:
        conn = module.get_connection()
        try:
            depart = time.monotonic()
            with pytest.raises(sqlite3.OperationalError) as echec:
                conn.execute("BEGIN IMMEDIATE")
            ecoule = time.monotonic() - depart
        finally:
            conn.close()
    finally:
        bloqueur.rollback()
        bloqueur.close()

    assert "lock" in str(echec.value).lower()
    # Large marge au-dessus de 0,3 s (machine chargée), mais franchement sous
    # les 5 s du défaut implicite : c'est cet écart qui rend le test discriminant.
    assert ecoule < 3.0, (
        f"La base « {libelle} » a attendu {ecoule:.1f} s alors que sa constante "
        "était abaissée à 0,3 s : le délai d'attente n'est pas transmis à "
        "sqlite3.connect."
    )
