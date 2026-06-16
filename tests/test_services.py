"""Tests unitaires de la logique métier (app/services.py)."""

import sqlite3

import pytest

from app import models, services


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        c.execute(p)
    for s in models.SCHEMA_STATEMENTS:
        c.executescript(s)
    c.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    c.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
        [("001",), ("002",), ("003",)],
    )
    c.commit()
    yield c
    c.close()


def test_disponible_par_defaut(conn):
    assert services.est_sorti(conn, "001") is False
    total, dispo = services.dispo_par_titre(conn, "CATAN")
    assert (total, dispo) == (3, 3)


def test_preter_attribue_le_plus_petit_numero(conn):
    assert services.preter(conn, "001") == 1
    assert services.preter(conn, "002") == 2
    assert services.est_sorti(conn, "001") is True
    assert services.dispo_par_titre(conn, "CATAN") == (3, 1)


def test_rendre_libere_et_recycle_le_numero(conn):
    services.preter(conn, "001")        # n°1
    services.preter(conn, "002")        # n°2
    res = services.rendre(conn, "001")  # libère n°1
    assert res == {"numero_libere": 1}
    # le plus petit libre est de nouveau 1
    assert services.preter(conn, "003") == 1


def test_rendre_sans_pret_ne_bloque_pas(conn):
    assert services.rendre(conn, "001") == {"deja_disponible": True}


def test_repreter_clot_lancien_et_rouvre(conn):
    services.preter(conn, "001")              # n°1
    res = services.repreter(conn, "001")      # clôt n°1, en rouvre un
    assert res["ancien_numero"] == 1
    assert res["nouveau_numero"] == 1         # n°1 libéré puis réattribué (plus petit libre)
    assert services.est_sorti(conn, "001") is True


def test_repreter_sur_disponible_ouvre_simplement(conn):
    res = services.repreter(conn, "002")
    assert res == {"nouveau_numero": 1, "etait_disponible": True}


def test_historique_jamais_purge(conn):
    services.preter(conn, "001")
    services.rendre(conn, "001")
    services.preter(conn, "001")
    n = conn.execute("SELECT COUNT(*) FROM prets WHERE id_exemplaire='001'").fetchone()[0]
    assert n == 2  # deux lignes d'historique, dont une clôturée


def test_stats_globales_et_palmares(conn):
    # 001 prêté 2 fois (1 clôturé + 1 en cours), 002 une fois, 003 jamais.
    services.preter(conn, "001"); services.rendre(conn, "001"); services.preter(conn, "001")
    services.preter(conn, "002")
    g = services.stats_globales(conn)
    assert g["total_prets"] == 3
    assert g["en_cours"] == 2          # 001 et 002 encore sortis
    assert g["titres_pretes"] == 1     # un seul titre (CATAN)

    # Palmarès par titre : CATAN cumule 3 prêts sur 3 exemplaires.
    plus = services.palmares(conn, sens="desc", metrique="total")
    assert plus[0]["reference_titre"] == "CATAN"
    assert plus[0]["nb_prets"] == 3
    assert plus[0]["nb_exemplaires"] == 3


def test_prets_par_heure(conn):
    services.preter(conn, "001")
    services.preter(conn, "002")
    heures = services.prets_par_heure(conn)
    assert sum(h["n"] for h in heures) == 2
