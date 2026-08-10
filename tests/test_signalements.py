"""
Tests du carnet de maintenance — signalements d'état des boîtes
(docs/conception-signalements.md).

Lot 1 (socle) : les deux tables, l'index partiel, le seed, les services (CRUD
des catégories + creer_signalement/signalements_ouverts/lister_signalements/
traiter_signalement) et leurs tests. Aucune route, aucun gabarit à ce stade.
"""

import sqlite3

import pytest

from app import db, models, services


# ===========================================================================
# Schéma + seed (patron emplacements_rangement)
# ===========================================================================
@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Chemin de base temporaire, isolé de la vraie base (monkeypatch DATABASE_PATH)."""
    chemin = tmp_path / "test.db"
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    return chemin


def _colonnes(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_tables_creees_sur_base_neuve(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"categories_signalement", "signalements"} <= tables
        assert set(_colonnes(conn, "categories_signalement")) == {
            "id_categorie", "nom", "actif", "ordre",
        }
        assert set(_colonnes(conn, "signalements")) == {
            "id_signalement", "id_exemplaire", "id_categorie", "texte",
            "cree_le", "traite_le",
        }
    finally:
        conn.close()


def test_index_signalements_ouverts_present(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        index = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_signalements_ouverts" in index
    finally:
        conn.close()


def test_categories_signalement_seedees(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        lignes = conn.execute(
            "SELECT nom, actif, ordre FROM categories_signalement ORDER BY ordre"
        ).fetchall()
        noms = [r["nom"] for r in lignes]
        assert noms == [
            "Pièce manquante", "Règle manquante", "Boîte abîmée",
            "Matériel abîmé", "Autre",
        ]
        assert all(r["actif"] == 1 for r in lignes)
        assert [r["ordre"] for r in lignes] == [0, 1, 2, 3, 4]
    finally:
        conn.close()


def test_seed_categories_idempotent_ne_duplique_pas(db_path):
    db.init_db()
    db.init_db()  # ré-appel volontaire, doit être sans effet
    conn = db.get_connection()
    try:
        (nb,) = conn.execute("SELECT COUNT(*) FROM categories_signalement").fetchone()
        assert nb == 5
    finally:
        conn.close()


def test_seed_categories_ne_ressuscite_pas_une_entree_supprimee(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM categories_signalement WHERE nom = 'Autre'")
        conn.commit()
    finally:
        conn.close()

    db.init_db()  # ne doit PAS recréer "Autre" : la table n'est plus vide
    conn = db.get_connection()
    try:
        noms = [
            r["nom"] for r in conn.execute("SELECT nom FROM categories_signalement").fetchall()
        ]
        assert "Autre" not in noms
        assert len(noms) == 4
    finally:
        conn.close()


# ===========================================================================
# CRUD des catégories (patron *_emplacement_rangement)
# ===========================================================================
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        c.execute(p)
    for s in models.SCHEMA_STATEMENTS:
        c.executescript(s)
    c.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    c.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre, emplacement_evenement) "
        "VALUES ('001', 'CATAN', 'Table 3')"
    )
    c.commit()
    # Cette fixture construit le schéma directement (comme test_services.py),
    # sans passer par db.init_db() : le seed des catégories (normalement
    # appliqué là) est donc rejoué ici pour que les tests de services
    # démarrent avec les 5 catégories par défaut.
    db._seed_categories_signalement(c)
    yield c
    c.close()


def _id_categorie(conn: sqlite3.Connection, nom: str) -> int:
    return [
        l for l in services.lister_categories_signalement(conn) if l["nom"] == nom
    ][0]["id_categorie"]


def test_creer_categorie_ajoute_en_fin_de_liste(conn):
    nouvel_id = services.creer_categorie_signalement(conn, "  Dé perdu  ")
    assert nouvel_id is not None
    lignes = services.lister_categories_signalement(conn)
    dernier = [l for l in lignes if l["id_categorie"] == nouvel_id][0]
    assert dernier["nom"] == "Dé perdu"  # espaces normalisés
    assert dernier["ordre"] == max(l["ordre"] for l in lignes)
    assert dernier["usage_count"] == 0


def test_creer_categorie_nom_vide_ne_cree_rien(conn):
    avant = len(services.lister_categories_signalement(conn))
    assert services.creer_categorie_signalement(conn, "   ") is None
    assert len(services.lister_categories_signalement(conn)) == avant


def test_categories_actives_exclut_les_archivees(conn):
    id_autre = _id_categorie(conn, "Autre")
    assert id_autre in [c["id_categorie"] for c in services.categories_signalement_actives(conn)]

    services.archiver_categorie_signalement(conn, id_autre)
    assert id_autre not in [
        c["id_categorie"] for c in services.categories_signalement_actives(conn)
    ]
    # Toujours listée (avec les archivées) pour l'écran admin.
    assert id_autre in [
        l["id_categorie"] for l in services.lister_categories_signalement(conn)
    ]


def test_renommer_repercute_sur_lhistorique_via_la_fk(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    sid = services.creer_signalement(conn, "001", id_piece, "il manque un dé")

    assert services.renommer_categorie_signalement(conn, id_piece, "Pièce manquante (dé)") is True
    lignes = services.lister_signalements(conn, etat="tous")
    ligne = [l for l in lignes if l["id_signalement"] == sid][0]
    assert ligne["categorie_nom"] == "Pièce manquante (dé)"

    # Nom vide : refusé, rien ne change.
    assert services.renommer_categorie_signalement(conn, id_piece, "   ") is False


def test_archiver_ne_efface_rien_le_signalement_garde_sa_categorie(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    sid = services.creer_signalement(conn, "001", id_piece, None)

    services.archiver_categorie_signalement(conn, id_piece)
    ligne = [
        l for l in services.lister_signalements(conn, etat="tous")
        if l["id_signalement"] == sid
    ][0]
    assert ligne["categorie_nom"] == "Pièce manquante"  # FK sans cascade, rien perdu

    services.reactiver_categorie_signalement(conn, id_piece)
    assert id_piece in [
        c["id_categorie"] for c in services.categories_signalement_actives(conn)
    ]


def test_supprimer_refuse_si_signalement_rattache(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    services.creer_signalement(conn, "001", id_piece, None)

    assert services.compteur_usage_categorie_signalement(conn, id_piece) == 1
    assert services.supprimer_categorie_signalement(conn, id_piece) is False
    assert any(
        l["id_categorie"] == id_piece for l in services.lister_categories_signalement(conn)
    )  # toujours là


def test_supprimer_accepte_si_aucun_signalement(conn):
    id_regle = _id_categorie(conn, "Règle manquante")
    assert services.supprimer_categorie_signalement(conn, id_regle) is True
    assert not any(
        l["id_categorie"] == id_regle for l in services.lister_categories_signalement(conn)
    )


def test_deplacer_categorie_echange_avec_le_voisin(conn):
    avant = [l["nom"] for l in services.lister_categories_signalement(conn)]
    assert avant == [
        "Pièce manquante", "Règle manquante", "Boîte abîmée", "Matériel abîmé", "Autre",
    ]
    id_regle = _id_categorie(conn, "Règle manquante")

    services.deplacer_categorie_signalement(conn, id_regle, "haut")
    apres = [l["nom"] for l in services.lister_categories_signalement(conn)]
    assert apres == [
        "Règle manquante", "Pièce manquante", "Boîte abîmée", "Matériel abîmé", "Autre",
    ]


def test_deplacer_categorie_en_bout_de_liste_ne_fait_rien(conn):
    lignes = services.lister_categories_signalement(conn)
    premier = lignes[0]["id_categorie"]
    dernier = lignes[-1]["id_categorie"]

    services.deplacer_categorie_signalement(conn, premier, "haut")
    services.deplacer_categorie_signalement(conn, dernier, "bas")
    apres = [l["nom"] for l in services.lister_categories_signalement(conn)]
    assert apres == [l["nom"] for l in lignes]


def test_deplacer_categorie_id_inconnu_ne_leve_pas(conn):
    services.deplacer_categorie_signalement(conn, 99999, "haut")  # aucune exception


# ===========================================================================
# Signalements — création, lecture, traitement
# ===========================================================================
def test_creer_signalement_categorie_seule_suffit(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    sid = services.creer_signalement(conn, "001", id_piece, None)
    assert sid is not None

    ouverts = services.signalements_ouverts(conn, "001")
    assert len(ouverts) == 1
    assert ouverts[0]["categorie_nom"] == "Pièce manquante"
    assert ouverts[0]["texte"] is None


def test_signalements_ouverts_vide_si_aucun(conn):
    assert services.signalements_ouverts(conn, "001") == []


def test_creer_signalement_texte_borne_sans_refus(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    texte_trop_long = "x" * 1000
    sid = services.creer_signalement(conn, "001", id_piece, texte_trop_long)
    assert sid is not None  # jamais refusé

    ouverts = services.signalements_ouverts(conn, "001")
    assert len(ouverts[0]["texte"]) == services.LONGUEUR_MAX_TEXTE_SIGNALEMENT


def test_creer_signalement_texte_vide_devient_none(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    sid = services.creer_signalement(conn, "001", id_piece, "   ")
    ouverts = services.signalements_ouverts(conn, "001")
    assert ouverts[0]["texte"] is None


def test_traiter_signalement_idempotent(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    sid = services.creer_signalement(conn, "001", id_piece, "il manque un dé")

    assert services.compter_signalements_ouverts(conn) == 1
    services.traiter_signalement(conn, sid)
    assert services.compter_signalements_ouverts(conn) == 0
    ligne = services.lister_signalements(conn, etat="traites")[0]
    traite_le_1 = ligne["traite_le"]
    assert traite_le_1 is not None

    # Second appel : idempotent, aucune erreur, la date ne change pas de sens
    # (toujours traité, toujours un seul signalement traité).
    services.traiter_signalement(conn, sid)
    assert services.compter_signalements_ouverts(conn) == 0
    assert len(services.lister_signalements(conn, etat="traites")) == 1


def test_traiter_signalement_inconnu_ne_leve_pas(conn):
    services.traiter_signalement(conn, 99999)  # aucune exception


def test_lister_signalements_filtre_par_etat(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    sid_ouvert = services.creer_signalement(conn, "001", id_piece, "ouvert")
    sid_traite = services.creer_signalement(conn, "001", id_piece, "traité")
    services.traiter_signalement(conn, sid_traite)

    ouverts = services.lister_signalements(conn, etat="ouverts")
    assert [l["id_signalement"] for l in ouverts] == [sid_ouvert]

    traites = services.lister_signalements(conn, etat="traites")
    assert [l["id_signalement"] for l in traites] == [sid_traite]

    tous = services.lister_signalements(conn, etat="tous")
    assert {l["id_signalement"] for l in tous} == {sid_ouvert, sid_traite}


def test_lister_signalements_filtre_par_categorie(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    id_boite = _id_categorie(conn, "Boîte abîmée")
    sid_piece = services.creer_signalement(conn, "001", id_piece, None)
    services.creer_signalement(conn, "001", id_boite, None)

    filtre = services.lister_signalements(conn, etat="tous", id_categorie=id_piece)
    assert [l["id_signalement"] for l in filtre] == [sid_piece]


def test_lister_signalements_ramene_les_deux_emplacements(conn):
    id_emplacement = services.creer_emplacement_rangement(conn, "Étagère 3")
    conn.execute(
        "UPDATE exemplaires SET emplacement_local_id = ? WHERE id_exemplaire = '001'",
        (id_emplacement,),
    )
    conn.commit()

    id_piece = _id_categorie(conn, "Pièce manquante")
    services.creer_signalement(conn, "001", id_piece, None)

    ligne = services.lister_signalements(conn)[0]
    assert ligne["emplacement_evenement"] == "Table 3"  # posé par la fixture
    assert ligne["emplacement_local_nom"] == "Étagère 3"
    assert ligne["jeu_nom"] == "Catan"


def test_lister_signalements_emplacements_absents_si_rien_renseigne(conn):
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'CATAN')"
    )
    conn.commit()
    id_piece = _id_categorie(conn, "Pièce manquante")
    services.creer_signalement(conn, "002", id_piece, None)

    ligne = [l for l in services.lister_signalements(conn) if l["id_exemplaire"] == "002"][0]
    assert ligne["emplacement_evenement"] is None
    assert ligne["emplacement_local_nom"] is None


def test_compter_signalements_ouverts(conn):
    id_piece = _id_categorie(conn, "Pièce manquante")
    assert services.compter_signalements_ouverts(conn) == 0
    services.creer_signalement(conn, "001", id_piece, None)
    services.creer_signalement(conn, "001", id_piece, None)
    assert services.compter_signalements_ouverts(conn) == 2
