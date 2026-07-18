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


# ---------------------------------------------------------------------------
# Effacement du numéro de pochette à la clôture (fiche D5, volet 2).
# Le numéro désigne le casier d'une pièce d'identité : il n'a d'utilité que
# pendant le prêt et ne doit pas survivre dans les exports/sauvegardes.
# ---------------------------------------------------------------------------
def _numeros(conn, id_exemplaire="001"):
    """Numéros de pochette de toutes les lignes de prêt, de la plus ancienne."""
    return [
        r["numero_pochette"]
        for r in conn.execute(
            "SELECT numero_pochette FROM prets WHERE id_exemplaire = ? ORDER BY id_pret",
            (id_exemplaire,),
        )
    ]


def test_rendre_efface_le_numero_mais_l_affiche_d_abord(conn):
    """
    Le bénévole doit TOUJOURS voir le numéro pour retrouver la pièce
    d'identité — c'est le geste central de l'application. L'effacement en base
    ne doit donc pas le priver de l'information rendue à l'écran.
    """
    services.preter(conn, "001")               # n°1
    res = services.rendre(conn, "001")

    assert res["numero_libere"] == 1           # ...affiché au bénévole
    assert _numeros(conn) == [None]            # ...mais effacé en base


def test_pret_en_cours_conserve_son_numero(conn):
    """Contre-test : on ne purge pas trop large (reprise après incident)."""
    services.preter(conn, "001")
    assert _numeros(conn) == [1]


def test_retour_de_tournoi_efface_aussi_le_numero(conn):
    services.sortir_tournoi(conn, "001")       # marqueur 0
    services.rendre(conn, "001")
    assert _numeros(conn) == [None]


def test_repreter_efface_lancien_numero_pas_le_nouveau(conn):
    services.preter(conn, "001")               # n°1
    res = services.repreter(conn, "001")
    assert res["ancien_numero"] == 1           # affiché au bénévole
    # Ligne close purgée, nouveau prêt intact avec son numéro.
    assert _numeros(conn) == [None, res["nouveau_numero"]]


def test_cloturer_tous_les_prets_efface_les_numeros(conn):
    services.preter(conn, "001")
    services.preter(conn, "002")
    services.sortir_tournoi(conn, "003")

    assert services.cloturer_tous_les_prets(conn) == 3

    restants = conn.execute(
        "SELECT COUNT(*) FROM prets WHERE numero_pochette IS NOT NULL"
    ).fetchone()[0]
    assert restants == 0
    # L'historique, lui, est intact : les lignes et leurs dates sont là.
    assert conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0] == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM prets WHERE date_retour IS NOT NULL"
    ).fetchone()[0] == 3


def test_purge_sans_effet_sur_les_statistiques(conn):
    """
    Non-régression : aucune requête statistique n'agrège ni ne filtre sur
    `numero_pochette`. Totaux, palmarès et histogramme doivent être identiques
    que les lignes closes portent un numéro ou non.
    """
    services.preter(conn, "001"); services.rendre(conn, "001")
    services.preter(conn, "002"); services.rendre(conn, "002")
    services.preter(conn, "003")

    avant = (
        services.stats_globales(conn),
        services.palmares(conn, sens="desc", metrique="total"),
        services.prets_par_heure(conn),
    )

    # On remet artificiellement des numéros sur les lignes closes (état
    # d'AVANT la purge) : les mêmes chiffres doivent sortir.
    conn.execute("UPDATE prets SET numero_pochette = 9 WHERE date_retour IS NOT NULL")
    conn.commit()

    apres = (
        services.stats_globales(conn),
        services.palmares(conn, sens="desc", metrique="total"),
        services.prets_par_heure(conn),
    )
    assert avant == apres


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


def test_duree_moyenne_tiret_sans_pret_termine(conn):
    # Q9 : "—" (pas "0 min") tant qu'aucun prêt n'est terminé -- ni sans
    # aucun prêt, ni avec un prêt seulement en cours (AVG SQL sur 0 ligne
    # renvoie NULL -> None côté Python, déjà géré par stats_globales).
    assert services.stats_globales(conn)["duree_moyenne"] == "—"
    services.preter(conn, "001")
    assert services.stats_globales(conn)["duree_moyenne"] == "—"


def test_format_duree():
    assert services.format_duree(None) == "—"
    assert services.format_duree(45 * 60) == "45 min"
    assert services.format_duree(2 * 3600 + 5 * 60) == "2 h 05"
    assert services.format_duree(3 * 86400 + 4 * 3600).startswith("3 j 4 h")


def test_pluriel():
    # Grammaire FR : -1/0/1 = singulier, |n| >= 2 = pluriel (Q2, idees-ux.md).
    assert services.pluriel(0, "jeu", "jeux") == "jeu"
    assert services.pluriel(1, "jeu", "jeux") == "jeu"
    assert services.pluriel(2, "jeu", "jeux") == "jeux"
    assert services.pluriel(10, "jeu", "jeux") == "jeux"
    # Pluriel régulier aussi couvert (pas de déduction automatique en 's').
    assert services.pluriel(1, "prêt", "prêts") == "prêt"
    assert services.pluriel(3, "prêt", "prêts") == "prêts"


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


def test_prets_par_heure_conversion_locale(conn):
    # Bug M1 (docs/idees-ux.md) : un prêt à 13:00 UTC un jour d'été (Europe/
    # Paris en heure d'été, UTC+2) doit apparaître dans la barre LOCALE
    # « 15h », pas « 13h ». Un seul jour dans la période -> pas de date dans
    # le libellé.
    conn.execute(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif) "
        "VALUES ('001', 1, '2026-07-15T13:00:00+00:00', 'pret')"
    )
    conn.commit()
    heures = services.prets_par_heure(conn)
    assert heures == [{"heure": "2026-07-15T15", "label": "15h", "n": 1}]


def test_prets_par_heure_bascule_de_jour(conn):
    # 23:30 UTC -> 01:30 heure locale LE LENDEMAIN (été, UTC+2). Avec un
    # second prêt un autre jour local, la période couvre plusieurs jours : le
    # libellé inclut alors la date (pas seulement l'heure).
    conn.execute(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif) "
        "VALUES ('001', 1, '2026-07-15T23:30:00+00:00', 'pret')"
    )
    conn.execute(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif) "
        "VALUES ('002', 2, '2026-07-14T10:00:00+00:00', 'pret')"
    )
    conn.commit()
    heures = {h["heure"]: h for h in services.prets_par_heure(conn)}
    # Le prêt de 23:30 UTC bascule bien sur le jour local SUIVANT (16 juillet).
    assert heures["2026-07-16T01"] == {"heure": "2026-07-16T01", "label": "16/07 01h", "n": 1}
    assert heures["2026-07-14T12"] == {"heure": "2026-07-14T12", "label": "14/07 12h", "n": 1}
