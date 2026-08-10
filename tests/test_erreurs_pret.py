"""
Erreurs de prêt : un jeu rendu en moins d'une minute n'a pas été prêté.

Mauvaise boîte scannée au comptoir, ou visiteur qui se ravise pendant qu'on lui
prend sa pièce d'identité. La ligne passe alors de `motif = 'pret'` à
`motif = 'erreur'` (services._marquer_erreur_si_immediat) et sort de toutes les
statistiques, sans jamais être supprimée.

Chaque test ci-dessous a été vérifié en injectant sa régression : seuil ignoré,
requalification appliquée aux sorties tournoi, ligne supprimée au lieu d'être
requalifiée, compteur mêlé aux prêts ordinaires.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from app import exports, models, services


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


def _motifs(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT motif FROM prets ORDER BY id_pret")]


# ---------------------------------------------------------------------------
# Requalification à la clôture
# ---------------------------------------------------------------------------
def test_retour_immediat_devient_une_erreur(conn):
    services.preter(conn, "001")
    res = services.rendre(conn, "001")

    assert res["erreur"] is True
    assert res["motif"] == "pret"          # le geste du bénévole ne change pas
    assert _motifs(conn) == ["erreur"]


def test_retour_apres_le_seuil_reste_un_pret(conn, vieillir_prets):
    services.preter(conn, "001")
    vieillir_prets(conn, services.SEUIL_ERREUR_PRET_S + 1)
    res = services.rendre(conn, "001")

    assert res["erreur"] is False
    assert _motifs(conn) == ["pret"]


def test_le_seuil_lui_meme_reste_un_pret(conn, vieillir_prets):
    """Exactement une minute : c'est déjà un prêt (comparaison stricte)."""
    services.preter(conn, "001")
    vieillir_prets(conn, services.SEUIL_ERREUR_PRET_S)

    assert services.rendre(conn, "001")["erreur"] is False


def test_la_ligne_est_requalifiee_jamais_supprimee(conn):
    services.preter(conn, "001")
    services.rendre(conn, "001")

    ligne = conn.execute("SELECT * FROM prets").fetchone()
    assert ligne is not None
    assert ligne["motif"] == "erreur"
    assert ligne["date_sortie"] and ligne["date_retour"]
    # Le numéro de pochette est effacé comme pour n'importe quel prêt clos (D5).
    assert ligne["numero_pochette"] is None


def test_la_pochette_est_bien_liberee(conn):
    """Une erreur reste un retour : le numéro redevient attribuable."""
    assert services.preter(conn, "001") == 1
    services.rendre(conn, "001")

    assert services.preter(conn, "002") == 1
    assert services.est_sorti(conn, "001") is False


def test_une_sortie_tournoi_n_est_jamais_requalifiee(conn):
    """
    Les sorties tournoi sont déjà hors statistiques, et leur motif porte une
    information que « erreur » effacerait.
    """
    services.sortir_tournoi(conn, "001")
    res = services.rendre(conn, "001")

    assert res == {"motif": "tournoi"}
    assert _motifs(conn) == ["tournoi"]


def test_horodatage_illisible_ne_requalifie_rien(conn):
    """Jamais bloquant : devant une date incompréhensible, on ne devine pas."""
    services.preter(conn, "001")
    conn.execute("UPDATE prets SET date_sortie = 'pas une date'")
    conn.commit()

    assert services.rendre(conn, "001")["erreur"] is False
    assert _motifs(conn) == ["pret"]


def test_le_transfert_requalifie_la_boite_rendue_pas_la_nouvelle(conn):
    """
    Le transfert de pochette est le rattrapage type d'une mauvaise boîte
    scannée : la boîte rendue sort des statistiques, celle qui part y entre.
    """
    services.preter(conn, "001")
    res = services.transferer_pochette(conn, "001", "002")

    assert res["transfere"] is True
    assert _motifs(conn) == ["erreur", "pret"]
    assert services.stats_globales(conn)["total_prets"] == 1


def test_un_transfert_apres_le_seuil_laisse_deux_prets(conn, vieillir_prets):
    services.preter(conn, "001")
    vieillir_prets(conn)
    services.transferer_pochette(conn, "001", "002")

    assert _motifs(conn) == ["pret", "pret"]


def test_repreter_ne_requalifie_pas(conn):
    """
    Contre-test : re-prêter n'est PAS un retour — la boîte reste sortie. La
    requalifier retirerait des statistiques un prêt bel et bien en cours.
    """
    services.preter(conn, "001")
    services.repreter(conn, "001")

    assert _motifs(conn) == ["pret", "pret"]


def test_la_cloture_de_fin_d_evenement_ne_requalifie_pas(conn):
    """
    Contre-test : une boîte encore sortie à la clôture est un prêt qu'on n'a
    pas vu revenir, pas une erreur de saisie — même si elle vient d'être
    scannée quelques secondes plus tôt.
    """
    services.preter(conn, "001")
    services.cloturer_tous_les_prets(conn)

    assert _motifs(conn) == ["pret"]


# ---------------------------------------------------------------------------
# Conséquences sur les statistiques
# ---------------------------------------------------------------------------
def test_une_erreur_sort_de_toutes_les_statistiques(conn, vieillir_prets):
    services.preter(conn, "001")           # prêt ordinaire, laissé en cours
    vieillir_prets(conn)
    services.preter(conn, "002")
    services.rendre(conn, "002")           # erreur de prêt

    g = services.stats_globales(conn)
    assert g["total_prets"] == 1
    assert g["en_cours"] == 1
    assert g["erreurs"] == 1
    # Le palmarès ne compte qu'un prêt pour le titre, et la durée moyenne
    # n'est pas tirée vers zéro par un prêt de quelques millisecondes.
    assert services.palmares(conn, sens="desc")[0]["nb_prets"] == 1
    assert g["duree_moyenne"] == "—"       # aucun prêt ORDINAIRE terminé
    assert len(services.lister_prets_periode(conn)) == 1   # l'erreur n'y est pas
    assert sum(h["n"] for h in services.prets_par_heure(conn)) == 1


def test_le_compteur_d_erreurs_respecte_la_periode(conn):
    services.preter(conn, "001")
    services.rendre(conn, "001")
    lointain = (datetime.now().astimezone()
                - timedelta(days=30)).isoformat(timespec="seconds")
    fin = (datetime.now().astimezone()
           - timedelta(days=29)).isoformat(timespec="seconds")

    assert services.stats_globales(conn)["erreurs"] == 1
    assert services.stats_globales(conn, lointain, fin)["erreurs"] == 0


def test_les_exports_montrent_les_erreurs_a_part(conn):
    services.preter(conn, "001")
    services.rendre(conn, "001")
    data = services.collecter_stats(conn)

    from io import BytesIO

    import openpyxl

    classeur = openpyxl.load_workbook(BytesIO(exports.construire_xlsx(data, "tout")))
    libelles = [c[0] for c in classeur["Synthèse"].iter_rows(values_only=True) if c[0]]
    assert any("Erreurs de prêt" in str(l) for l in libelles)
    # Le PDF se construit sans erreur et n'est pas vide.
    assert exports.construire_pdf(data, "tout", sections=("synthese",))
