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
    assert res == {"numero_libere": 1, "motif": "pret"}
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


def test_sortir_tournoi_hors_stats(conn):
    # Une sortie tournoi rend l'exemplaire indisponible...
    services.sortir_tournoi(conn, "001")
    assert services.est_sorti(conn, "001") is True
    # ... mais n'est PAS comptée dans les statistiques.
    g = services.stats_globales(conn)
    assert g["total_prets"] == 0 and g["en_cours"] == 0
    # Un vrai prêt, lui, compte.
    services.preter(conn, "002")
    assert services.stats_globales(conn)["total_prets"] == 1
    # Le retour de tournoi ne libère pas d'emplacement.
    res = services.rendre(conn, "001")
    assert res == {"motif": "tournoi"}
    assert services.est_sorti(conn, "001") is False


def test_duree_moyenne_et_par_pret(conn):
    services.preter(conn, "001")
    services.rendre(conn, "001")
    g = services.stats_globales(conn)
    assert g["duree_moyenne"] != "—"          # une durée est calculée
    prets = services.lister_prets_periode(conn)
    assert prets and prets[0]["duree_txt"]    # durée par prêt présente
    # Prêt en cours : libellé « depuis … »
    services.preter(conn, "002")
    en_cours = [p for p in services.lister_prets_periode(conn) if not p["date_retour"]]
    assert en_cours and en_cours[0]["duree_txt"].startswith("depuis")


def test_format_duree():
    assert services.format_duree(None) == "—"
    assert services.format_duree(45 * 60) == "45 min"
    assert services.format_duree(2 * 3600 + 5 * 60) == "2 h 05"
    assert services.format_duree(3 * 86400 + 4 * 3600).startswith("3 j 4 h")


def test_cloturer_tous_les_prets(conn):
    services.preter(conn, "001")
    services.sortir_tournoi(conn, "002")
    nb = services.cloturer_tous_les_prets(conn)
    assert nb == 2
    assert services.est_sorti(conn, "001") is False
    assert services.est_sorti(conn, "002") is False
    # Historique conservé (les lignes existent toujours, juste clôturées).
    total = conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
    assert total == 2
    # Toutes les pochettes sont libérées.
    occupees = conn.execute("SELECT COUNT(*) FROM pochettes WHERE occupe = 1").fetchone()[0]
    assert occupees == 0


def test_parse_date_achat():
    from scripts.import_csv import parse_date_achat
    assert parse_date_achat("16-sept-19") == "2019-09-16"
    assert parse_date_achat("16/09/2019") == "2019-09-16"
    assert parse_date_achat("1-déc-2020") == "2020-12-01"
    assert parse_date_achat("") is None
    assert parse_date_achat("bidon") is None


def test_derniers_achats(conn):
    conn.execute("UPDATE titres SET date_achat = '2020-05-01' WHERE reference_titre = 'CATAN'")
    d = services.derniers_achats(conn)
    assert d and d[0]["reference_titre"] == "CATAN"
    assert d[0]["date_achat_txt"] == "01/05/2020"
    assert d[0]["id_repr"] == "001"


def test_selection_etiquettes(conn):
    titres = services.titres_pour_etiquettes(conn)
    assert titres and titres[0]["reference_titre"] == "CATAN"
    assert titres[0]["nb_exemplaires"] == 3
    ex = services.exemplaires_pour_etiquettes(conn, ["CATAN"])
    assert {e["id_exemplaire"] for e in ex} == {"001", "002", "003"}
    assert len(services.exemplaires_pour_etiquettes(conn, None)) == 3   # tout


def test_prets_par_heure(conn):
    services.preter(conn, "001")
    services.preter(conn, "002")
    heures = services.prets_par_heure(conn)
    assert sum(h["n"] for h in heures) == 2
