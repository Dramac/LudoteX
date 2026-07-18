"""
Migration de `prets.numero_pochette` vers NULLABLE + purge rétroactive
(app/db.py::_migrer_pochette_nullable, fiche D5).

`prets` est la table la plus sensible du projet : son historique n'est jamais
purgé et une reconstruction de table SQLite ratée perd des lignes
définitivement. Ces tests portent donc autant sur la conservation des données
que sur le résultat de la migration.
"""

import sqlite3

import pytest

from app import db, models


# Schéma de `prets` AVANT la migration : numero_pochette INTEGER NOT NULL.
_PRETS_ANCIEN = """
CREATE TABLE prets (
    id_pret          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_exemplaire    TEXT NOT NULL,
    numero_pochette  INTEGER NOT NULL,
    date_sortie      TEXT NOT NULL,
    date_retour      TEXT,
    motif            TEXT NOT NULL DEFAULT 'pret',
    FOREIGN KEY (id_exemplaire) REFERENCES exemplaires (id_exemplaire)
);
"""


@pytest.fixture
def base_ancienne():
    """
    Base au schéma d'AVANT la migration, avec un historique représentatif :
    deux prêts clos numérotés, une sortie tournoi close (marqueur 0), et un
    prêt encore EN COURS.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        c.execute(p)
    # Tout le schéma courant SAUF `prets`, remplacée par sa version d'époque.
    for s in models.SCHEMA_STATEMENTS:
        if s is models.SCHEMA_PRETS:
            c.executescript(_PRETS_ANCIEN)
        else:
            c.executescript(s)
    c.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    c.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
        [("001",), ("002",), ("003",), ("004",)],
    )
    c.executemany(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, date_retour, motif)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            ("001", 1, "2026-07-18T09:00:00+00:00", "2026-07-18T11:00:00+00:00", "pret"),
            ("002", 2, "2026-07-18T09:30:00+00:00", "2026-07-18T12:00:00+00:00", "pret"),
            ("003", 0, "2026-07-18T10:00:00+00:00", "2026-07-18T13:00:00+00:00", "tournoi"),
            ("004", 3, "2026-07-18T14:00:00+00:00", None, "pret"),
        ],
    )
    c.commit()
    yield c
    c.close()


def _colonne_nullable(conn) -> bool:
    return not next(
        r[3] for r in conn.execute("PRAGMA table_info(prets)") if r[1] == "numero_pochette"
    )


def test_migration_sans_perte_de_ligne_et_numeros_purges(base_ancienne):
    conn = base_ancienne
    (avant,) = conn.execute("SELECT COUNT(*) FROM prets").fetchone()
    assert avant == 4
    assert not _colonne_nullable(conn)   # état de départ : contrainte présente

    db.init_db(conn)

    # 1. AUCUNE LIGNE PERDUE — le point critique de cette migration.
    (apres,) = conn.execute("SELECT COUNT(*) FROM prets").fetchone()
    assert apres == avant

    # 2. La contrainte NOT NULL a bien disparu.
    assert _colonne_nullable(conn)

    lignes = {
        r["id_exemplaire"]: r
        for r in conn.execute("SELECT * FROM prets")
    }
    # 3. Purge rétroactive des lignes CLOSES (sorties tournoi comprises).
    assert lignes["001"]["numero_pochette"] is None
    assert lignes["002"]["numero_pochette"] is None
    assert lignes["003"]["numero_pochette"] is None
    # 4. Contre-test : le prêt EN COURS garde son numéro (on n'a pas purgé
    #    trop large — c'est lui qui permet la reprise après incident).
    assert lignes["004"]["numero_pochette"] == 3

    # 5. Le reste de chaque ligne est intact (dates, motif, id_pret).
    assert lignes["003"]["motif"] == "tournoi"
    assert lignes["001"]["date_sortie"] == "2026-07-18T09:00:00+00:00"
    assert lignes["001"]["date_retour"] == "2026-07-18T11:00:00+00:00"
    assert sorted(r["id_pret"] for r in lignes.values()) == [1, 2, 3, 4]


def test_migration_recree_les_index_de_prets(base_ancienne):
    """DROP TABLE emporte les index : ils doivent être reconstruits."""
    conn = base_ancienne
    db.init_db(conn)

    index = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'prets'"
        )
    }
    assert {"idx_prets_exemplaire", "idx_prets_retour_null"} <= index


def test_migration_idempotente(base_ancienne):
    """Rejouer init_db ne doit avoir aucun effet (ni perte, ni doublon)."""
    conn = base_ancienne
    db.init_db(conn)
    lignes_1 = [tuple(r) for r in conn.execute("SELECT * FROM prets ORDER BY id_pret")]

    db.init_db(conn)
    db.init_db(conn)
    lignes_2 = [tuple(r) for r in conn.execute("SELECT * FROM prets ORDER BY id_pret")]

    assert lignes_2 == lignes_1


def test_migration_conserve_la_numerotation_des_nouveaux_prets(base_ancienne):
    """
    `id_pret` est AUTOINCREMENT : après reconstruction, un nouveau prêt doit
    prendre un identifiant SUPÉRIEUR au dernier existant (pas repartir de 1,
    ce qui écraserait la lecture de l'historique).
    """
    conn = base_ancienne
    db.init_db(conn)

    from app import services

    services.preter(conn, "001")
    (nouveau,) = conn.execute("SELECT MAX(id_pret) FROM prets").fetchone()
    assert nouveau > 4


def test_migration_refuse_un_schema_inattendu(base_ancienne):
    """
    Garde-fou : si `prets` porte une colonne que la migration ne connaît pas,
    elle échoue bruyamment plutôt que de la laisser tomber silencieusement lors
    de la recopie.
    """
    conn = base_ancienne
    conn.execute("ALTER TABLE prets ADD COLUMN commentaire TEXT")
    conn.commit()

    with pytest.raises(RuntimeError, match="ne correspondent pas"):
        db.init_db(conn)

    # Rien n'a été touché : les 4 lignes sont toujours là.
    (nb,) = conn.execute("SELECT COUNT(*) FROM prets").fetchone()
    assert nb == 4


def test_base_neuve_a_deja_la_colonne_nullable():
    """Une base créée aujourd'hui n'a pas besoin de migration."""
    c = sqlite3.connect(":memory:")
    try:
        c.row_factory = sqlite3.Row
        db.init_db(c)
        assert _colonne_nullable(c)
    finally:
        c.close()
