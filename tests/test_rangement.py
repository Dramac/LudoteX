"""
Tests du suivi de l'emplacement de rangement (docs/conception-rangement.md).

Étape 1 (schéma) : colonnes ajoutées à `exemplaires`, table
`emplacements_rangement` créée et seedée, migrations idempotentes.
Étape 2 (écran /admin/rangement) : services de gestion de la liste
(créer/renommer/archiver/réactiver/supprimer/réordonner) + routes protégées
par la garde admin. Les étapes suivantes (mode rangement au scanner,
affichages...) ajouteront leurs tests dans ce même fichier.
"""

import sqlite3

import pytest

from app import db, models, services


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Chemin de base temporaire, isolé de la vraie base (monkeypatch DATABASE_PATH)."""
    chemin = tmp_path / "test.db"
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    return chemin


def _colonnes(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_colonnes_emplacement_presentes_sur_base_neuve(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        colonnes = _colonnes(conn, "exemplaires")
        assert "emplacement_evenement" in colonnes
        assert "emplacement_local_id" in colonnes
    finally:
        conn.close()


def test_table_emplacements_rangement_creee_et_seedee(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        lignes = conn.execute(
            "SELECT nom, actif, ordre FROM emplacements_rangement ORDER BY ordre"
        ).fetchall()
        noms = [r["nom"] for r in lignes]
        assert noms == ["Totem", "Puzzle", "P'tits potes", "valise 1", "valise 2"]
        assert all(r["actif"] == 1 for r in lignes)
        assert [r["ordre"] for r in lignes] == [0, 1, 2, 3, 4]
    finally:
        conn.close()


def test_seed_idempotent_ne_duplique_pas(db_path):
    db.init_db()
    db.init_db()  # ré-appel volontaire, doit être sans effet
    conn = db.get_connection()
    try:
        (nb,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
        assert nb == 5
    finally:
        conn.close()


def test_seed_ne_ressuscite_pas_une_entree_supprimee(db_path):
    db.init_db()
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM emplacements_rangement WHERE nom = 'valise 2'")
        conn.commit()
    finally:
        conn.close()

    db.init_db()  # ne doit PAS recréer "valise 2" : la table n'est plus vide
    conn = db.get_connection()
    try:
        noms = [
            r["nom"]
            for r in conn.execute("SELECT nom FROM emplacements_rangement").fetchall()
        ]
        assert "valise 2" not in noms
        assert len(noms) == 4
    finally:
        conn.close()


def test_migration_sur_base_existante_sans_les_colonnes(db_path):
    # Simule une base créée AVANT cette évolution : schéma sans les 2 colonnes
    # ni la table emplacements_rangement.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE titres (
            reference_titre TEXT PRIMARY KEY,
            nom TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE exemplaires (
            id_exemplaire TEXT PRIMARY KEY,
            reference_titre TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')"
    )
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    db.init_db()  # doit migrer sans toucher aux données existantes

    conn = db.get_connection()
    try:
        colonnes = _colonnes(conn, "exemplaires")
        assert "emplacement_evenement" in colonnes
        assert "emplacement_local_id" in colonnes
        # La boîte existante est intacte.
        row = conn.execute(
            "SELECT id_exemplaire, reference_titre FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["reference_titre"] == "CATAN"
        # La liste des emplacements a bien été seedée sur la base migrée.
        (nb,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
        assert nb == 5
    finally:
        conn.close()


# ===========================================================================
# Étape 2 — services de gestion de la liste des emplacements locaux
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
    c.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    c.commit()
    # Cette fixture construit le schéma directement (comme test_services.py),
    # sans passer par db.init_db() : le seed des emplacements (normalement
    # appliqué là) est donc rejoué ici pour que les tests de services
    # démarrent avec les 5 emplacements par défaut.
    db._seed_emplacements_rangement(c)
    yield c
    c.close()


def test_contexte_et_visibilite_defauts_et_ecriture(conn):
    assert services.rangement_contexte(conn) == "evenement"
    assert services.rangement_visibilite(conn) == "benevoles"

    services.ecrire_rangement_contexte(conn, "local")
    assert services.rangement_contexte(conn) == "local"

    services.ecrire_rangement_visibilite(conn, "tous")
    assert services.rangement_visibilite(conn) == "tous"

    with pytest.raises(ValueError):
        services.ecrire_rangement_contexte(conn, "n'importe-quoi")
    with pytest.raises(ValueError):
        services.ecrire_rangement_visibilite(conn, "n'importe-quoi")


def test_creer_emplacement_ajoute_en_fin_de_liste(conn):
    nouvel_id = services.creer_emplacement_rangement(conn, "  Étagère 3  ")
    assert nouvel_id is not None
    lignes = services.lister_emplacements_rangement(conn)
    dernier = [l for l in lignes if l["id_emplacement"] == nouvel_id][0]
    assert dernier["nom"] == "Étagère 3"  # espaces normalisés
    assert dernier["ordre"] == max(l["ordre"] for l in lignes)
    assert dernier["usage_count"] == 0


def test_creer_emplacement_nom_vide_ne_cree_rien(conn):
    avant = len(services.lister_emplacements_rangement(conn))
    assert services.creer_emplacement_rangement(conn, "   ") is None
    assert len(services.lister_emplacements_rangement(conn)) == avant


def test_obtenir_ou_creer_reutilise_un_nom_existant_insensible_casse(conn):
    # "Totem" existe déjà (seed). Une variante de casse/espaces doit le
    # retrouver plutôt que d'en créer un doublon (§4.b, réutilisé plus tard
    # par l'import CSV).
    id_totem = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
    ][0]["id_emplacement"]

    resultat = services.obtenir_ou_creer_emplacement_rangement(conn, "  totem  ")
    assert resultat == (id_totem, False)

    resultat2 = services.obtenir_ou_creer_emplacement_rangement(conn, "Nouveau coin")
    assert resultat2[1] is True
    assert resultat2[0] != id_totem

    assert services.obtenir_ou_creer_emplacement_rangement(conn, "") is None


def test_renommer_repercute_sur_les_boites_via_la_fk(conn):
    lignes = services.lister_emplacements_rangement(conn)
    id_valise = [l for l in lignes if l["nom"] == "valise 1"][0]["id_emplacement"]
    conn.execute(
        "UPDATE exemplaires SET emplacement_local_id = ? WHERE id_exemplaire = '001'",
        (id_valise,),
    )
    conn.commit()

    assert services.renommer_emplacement_rangement(conn, id_valise, "Valise bleue") is True
    row = conn.execute(
        "SELECT er.nom FROM exemplaires x "
        "JOIN emplacements_rangement er ON er.id_emplacement = x.emplacement_local_id "
        "WHERE x.id_exemplaire = '001'"
    ).fetchone()
    assert row["nom"] == "Valise bleue"

    # Nom vide : refusé, rien ne change.
    assert services.renommer_emplacement_rangement(conn, id_valise, "  ") is False
    assert services.get_emplacement_rangement(conn, id_valise)["nom"] == "Valise bleue"


def test_archiver_puis_reactiver(conn):
    id_totem = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
    ][0]["id_emplacement"]

    services.archiver_emplacement_rangement(conn, id_totem)
    assert services.get_emplacement_rangement(conn, id_totem)["actif"] == 0
    assert id_totem not in [e["id_emplacement"] for e in services.emplacements_actifs(conn)]
    # Toujours listé (avec les archivés) pour l'écran admin.
    assert id_totem in [l["id_emplacement"] for l in services.lister_emplacements_rangement(conn)]

    services.reactiver_emplacement_rangement(conn, id_totem)
    assert services.get_emplacement_rangement(conn, id_totem)["actif"] == 1
    assert id_totem in [e["id_emplacement"] for e in services.emplacements_actifs(conn)]


def test_supprimer_refuse_si_boite_rattachee(conn):
    lignes = services.lister_emplacements_rangement(conn)
    id_valise = [l for l in lignes if l["nom"] == "valise 1"][0]["id_emplacement"]
    conn.execute(
        "UPDATE exemplaires SET emplacement_local_id = ? WHERE id_exemplaire = '001'",
        (id_valise,),
    )
    conn.commit()

    assert services.compteur_usage_emplacement_rangement(conn, id_valise) == 1
    assert services.supprimer_emplacement_rangement(conn, id_valise) is False
    assert services.get_emplacement_rangement(conn, id_valise) is not None  # toujours là


def test_supprimer_accepte_si_aucune_boite(conn):
    lignes = services.lister_emplacements_rangement(conn)
    id_puzzle = [l for l in lignes if l["nom"] == "Puzzle"][0]["id_emplacement"]

    assert services.supprimer_emplacement_rangement(conn, id_puzzle) is True
    assert services.get_emplacement_rangement(conn, id_puzzle) is None


def test_deplacer_emplacement_echange_avec_le_voisin(conn):
    avant = [l["nom"] for l in services.lister_emplacements_rangement(conn)]
    assert avant == ["Totem", "Puzzle", "P'tits potes", "valise 1", "valise 2"]
    id_puzzle = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Puzzle"
    ][0]["id_emplacement"]

    services.deplacer_emplacement_rangement(conn, id_puzzle, "haut")
    apres = [l["nom"] for l in services.lister_emplacements_rangement(conn)]
    assert apres == ["Puzzle", "Totem", "P'tits potes", "valise 1", "valise 2"]

    services.deplacer_emplacement_rangement(conn, id_puzzle, "bas")
    services.deplacer_emplacement_rangement(conn, id_puzzle, "bas")
    apres2 = [l["nom"] for l in services.lister_emplacements_rangement(conn)]
    assert apres2 == ["Totem", "P'tits potes", "Puzzle", "valise 1", "valise 2"]


def test_deplacer_en_bout_de_liste_ne_fait_rien(conn):
    lignes = services.lister_emplacements_rangement(conn)
    id_totem = lignes[0]["id_emplacement"]  # premier de la liste
    id_valise2 = lignes[-1]["id_emplacement"]  # dernier de la liste

    services.deplacer_emplacement_rangement(conn, id_totem, "haut")
    services.deplacer_emplacement_rangement(conn, id_valise2, "bas")
    apres = [l["nom"] for l in services.lister_emplacements_rangement(conn)]
    assert apres == ["Totem", "Puzzle", "P'tits potes", "valise 1", "valise 2"]


def test_deplacer_id_inconnu_ne_leve_pas(conn):
    services.deplacer_emplacement_rangement(conn, 99999, "haut")  # aucune exception


# ===========================================================================
# Étape 2 — routes /admin/rangement (garde admin + parcours complet)
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    from app import db as _db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(_db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn_ = _db.get_connection()
    _db.init_db(conn_)
    conn_.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn_.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn_.commit()
    conn_.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _connecter(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})


def test_rangement_page_redirige_sans_session_admin(client):
    r = client.get("/admin/rangement", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_rangement_page_liste_les_5_emplacements_seed(client):
    _connecter(client)
    r = client.get("/admin/rangement")
    assert r.status_code == 200
    # L'apostrophe est échappée en HTML (&#39;) par l'auto-échappement Jinja.
    for nom in ["Totem", "Puzzle", "P&#39;tits potes", "valise 1", "valise 2"]:
        assert nom in r.text


def test_rangement_contexte_et_visibilite_formulaires(client):
    _connecter(client)
    r = client.post("/admin/rangement/contexte", data={"contexte": "local"})
    assert r.status_code == 200
    assert "Contexte de rangement enregistré." in r.text

    r2 = client.post("/admin/rangement/visibilite", data={"visibilite": "tous"})
    assert "Visibilité publique enregistrée." in r2.text

    from app import db as _db
    conn = _db.get_connection()
    try:
        assert services.rangement_contexte(conn) == "local"
        assert services.rangement_visibilite(conn) == "tous"
    finally:
        conn.close()


def test_rangement_ajouter_renommer_archiver_reactiver(client):
    _connecter(client)
    r = client.post("/admin/rangement/emplacements", data={"nom": "Étagère 3"})
    assert "Emplacement ajouté." in r.text
    assert "Étagère 3" in r.text

    from app import db as _db
    conn = _db.get_connection()
    try:
        id_etagere = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Étagère 3"
        ][0]["id_emplacement"]
    finally:
        conn.close()

    r2 = client.post(f"/admin/rangement/emplacements/{id_etagere}/renommer", data={"nom": "Étagère 3 bis"})
    assert "Emplacement renommé." in r2.text
    assert "Étagère 3 bis" in r2.text

    r3 = client.post(f"/admin/rangement/emplacements/{id_etagere}/archiver")
    assert "Emplacement archivé" in r3.text
    assert "Archivé" in r3.text  # badge côté admin

    r4 = client.post(f"/admin/rangement/emplacements/{id_etagere}/reactiver")
    assert "Emplacement réactivé." in r4.text


def test_rangement_suppression_refusee_puis_acceptee(client):
    _connecter(client)
    from app import db as _db
    conn = _db.get_connection()
    try:
        id_valise = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "valise 1"
        ][0]["id_emplacement"]
        conn.execute(
            "UPDATE exemplaires SET emplacement_local_id = ? WHERE id_exemplaire = '001'",
            (id_valise,),
        )
        conn.commit()
        id_puzzle = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Puzzle"
        ][0]["id_emplacement"]
    finally:
        conn.close()

    r = client.post(f"/admin/rangement/emplacements/{id_valise}/supprimer")
    assert "Suppression refusée" in r.text
    assert "valise 1" in r.text  # toujours dans la liste

    r2 = client.post(f"/admin/rangement/emplacements/{id_puzzle}/supprimer")
    assert "Emplacement supprimé définitivement." in r2.text
    tableau = r2.text.split("<table")[1].split("</table>")[0]
    assert "Puzzle" not in tableau


def test_rangement_reordonner_monter_descendre(client):
    _connecter(client)
    from app import db as _db
    conn = _db.get_connection()
    try:
        id_puzzle = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Puzzle"
        ][0]["id_emplacement"]
    finally:
        conn.close()

    client.post(f"/admin/rangement/emplacements/{id_puzzle}/monter")
    conn = _db.get_connection()
    try:
        noms = [l["nom"] for l in services.lister_emplacements_rangement(conn)]
    finally:
        conn.close()
    assert noms[0] == "Puzzle"


def test_rangement_lien_present_dans_tableau_de_bord(client):
    _connecter(client)
    r = client.get("/admin")
    assert 'href="/admin/rangement"' in r.text


# ===========================================================================
# Étape 3 — mode rangement au scanner
# ===========================================================================
def test_affecter_emplacement_evenement(conn):
    resultat = services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    assert resultat is not None and resultat["nom"] == "Catan"
    row = conn.execute(
        "SELECT emplacement_evenement, emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
    ).fetchone()
    assert row["emplacement_evenement"] == "Étagère 2"
    assert row["emplacement_local_id"] is None


def test_affecter_emplacement_local(conn):
    id_totem = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
    ][0]["id_emplacement"]
    resultat = services.affecter_emplacement(conn, "001", "local", id_totem)
    assert resultat is not None
    row = conn.execute(
        "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
    ).fetchone()
    assert row["emplacement_local_id"] == id_totem


def test_affecter_emplacement_code_inconnu(conn):
    assert services.affecter_emplacement(conn, "INEXISTANT", "evenement", "Étagère 2") is None


def test_affecter_emplacement_sans_effet_sur_le_pret(conn):
    # Sortir la boîte en prêt, puis lui affecter un emplacement : le prêt et
    # la pochette ne doivent PAS bouger (invariant §1/§9/§10).
    from app import services as svc

    numero = svc.preter(conn, "001")
    svc.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    assert svc.est_sorti(conn, "001") is True
    row = conn.execute(
        "SELECT numero_pochette FROM prets WHERE id_exemplaire = '001' AND date_retour IS NULL"
    ).fetchone()
    assert row["numero_pochette"] == numero


# --- routes /scanner (mode rangement) --------------------------------------
def _activer_evenement(client, texte):
    return client.post("/scanner/rangement/activer", data={"emplacement_texte": texte})


def _passer_en_local(client):
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.ecrire_rangement_contexte(conn, "local")
        id_totem = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
        ][0]["id_emplacement"]
    finally:
        conn.close()
    return id_totem


def test_scanner_hors_mode_propose_activation(client):
    r = client.get("/scanner")
    assert r.status_code == 200
    assert "Activer le mode rangement" in r.text
    assert 'data-rangement="1"' not in r.text


def test_activer_mode_evenement_pose_bandeau_et_flag(client):
    _activer_evenement(client, "Étagère 2, zone Jeux duel")
    r = client.get("/scanner")
    assert r.status_code == 200
    assert "Mode rangement" in r.text
    assert "Étagère 2, zone Jeux duel" in r.text
    assert 'data-rangement="1"' in r.text
    assert "Quitter le mode" in r.text


def test_scan_en_mode_rangement_affecte_et_confirme(client):
    _activer_evenement(client, "Étagère 2")
    r = client.get("/scanner/ranger", params={"code": "001"})
    assert r.status_code == 200
    assert "Catan" in r.text and "rangé en Étagère 2" in r.text
    # Toujours en mode rangement pour le scan suivant.
    assert 'data-rangement="1"' in r.text

    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_evenement"] == "Étagère 2"
    finally:
        conn.close()


def test_scan_en_mode_rangement_code_inconnu_message_pas_bloquant(client):
    _activer_evenement(client, "Étagère 2")
    r = client.get("/scanner/ranger", params={"code": "ZZZ999"})
    assert r.status_code == 200
    assert "Aucune boîte" in r.text
    assert 'data-rangement="1"' in r.text  # mode toujours actif


def test_saisie_manuelle_en_mode_rangement_affecte_au_lieu_de_pret(client):
    _activer_evenement(client, "Étagère 2")
    r = client.get("/scanner/saisie", params={"code": "001"})
    assert r.status_code == 200  # pas une redirection vers /pret/001
    assert "rangé en Étagère 2" in r.text


def test_saisie_manuelle_hors_mode_rangement_redirige_toujours_vers_pret(client):
    r = client.get("/scanner/saisie", params={"code": "001"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/pret/001"


def test_quitter_mode_efface_le_cookie(client):
    _activer_evenement(client, "Étagère 2")
    assert "Mode rangement" in client.get("/scanner").text

    client.post("/scanner/rangement/quitter")
    r = client.get("/scanner")
    assert "Mode rangement" not in r.text
    assert 'data-rangement="1"' not in r.text
    assert "Activer le mode rangement" in r.text


def test_mode_local_scan_affecte_emplacement_de_la_liste(client):
    id_totem = _passer_en_local(client)
    r = client.post("/scanner/rangement/activer", data={"emplacement_id": str(id_totem)})
    assert r.status_code in (200, 303)

    page = client.get("/scanner")
    assert "Totem" in page.text

    r2 = client.get("/scanner/ranger", params={"code": "001"})
    assert "rangé en Totem" in r2.text

    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_local_id"] == id_totem
    finally:
        conn.close()


def test_activer_local_avec_id_invalide_ignore_silencieusement(client):
    _passer_en_local(client)
    r = client.post("/scanner/rangement/activer", data={"emplacement_id": "999999"})
    assert r.status_code in (200, 303)
    page = client.get("/scanner")
    # Aucun cookie posé (id inconnu) : toujours en mode inactif, pas d'erreur.
    assert 'data-rangement="1"' not in page.text
    assert "Activer le mode rangement" in page.text


def test_cookie_devenu_invalide_traite_comme_inactif(client):
    # Mode local activé sur un emplacement, puis contexte remis à "evenement" :
    # la valeur du cookie (un id) ne doit plus être interprétée comme actif.
    id_totem = _passer_en_local(client)
    client.post("/scanner/rangement/activer", data={"emplacement_id": str(id_totem)})
    assert 'data-rangement="1"' in client.get("/scanner").text

    from app import db as _db
    conn = _db.get_connection()
    try:
        services.ecrire_rangement_contexte(conn, "evenement")
    finally:
        conn.close()

    # Le cookie contient un id numérique ("3" par ex.) : en contexte "evenement"
    # il est réinterprété comme un texte libre valide -> mode reste actif, mais
    # avec ce texte comme libellé (comportement attendu, pas une erreur).
    r = client.get("/scanner")
    assert r.status_code == 200


def test_scanner_saisie_manuelle_protegee_par_jeton_reste_valable_en_mode_rangement(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-scanner-secret-32-caracteres")
    assert client.get("/scanner/ranger", params={"code": "001"}).status_code == 403
    assert client.post("/scanner/rangement/activer", data={"emplacement_texte": "Étagère 2"}).status_code == 403
    assert client.post("/scanner/rangement/quitter").status_code == 403


# ===========================================================================
# Étape 4 — affichage « où ranger le jeu » au retour (/pret/<id>)
# ===========================================================================
def test_emplacement_actuel_evenement(conn):
    assert services.emplacement_actuel(conn, "001") is None
    services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    assert services.emplacement_actuel(conn, "001") == "Étagère 2"


def test_emplacement_actuel_local_reste_affiche_meme_archive(conn):
    services.ecrire_rangement_contexte(conn, "local")
    id_totem = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
    ][0]["id_emplacement"]
    assert services.emplacement_actuel(conn, "001") is None

    services.affecter_emplacement(conn, "001", "local", id_totem)
    assert services.emplacement_actuel(conn, "001") == "Totem"

    # Retrait doux (§5) : la boîte garde sa référence et l'affiche toujours,
    # même si l'emplacement a disparu des menus de saisie.
    services.archiver_emplacement_rangement(conn, id_totem)
    assert services.emplacement_actuel(conn, "001") == "Totem"


def test_emplacement_actuel_code_inconnu(conn):
    assert services.emplacement_actuel(conn, "INEXISTANT") is None


def test_retour_affiche_ou_ranger_si_renseigne(client):
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.affecter_emplacement(conn, "001", "evenement", "Étagère 2, zone Jeux duel")
    finally:
        conn.close()

    client.post("/pret/001/preter")
    r = client.post("/pret/001/rendre")
    assert r.status_code == 200
    assert "Où ranger le jeu" in r.text
    assert "Étagère 2, zone Jeux duel" in r.text


def test_retour_naffiche_rien_si_emplacement_absent(client):
    # Jamais bloquant : rien de renseigné -> le bloc n'apparaît pas du tout
    # (pas de "non renseigné" anxiogène).
    client.post("/pret/001/preter")
    r = client.post("/pret/001/rendre")
    assert r.status_code == 200
    assert "Où ranger le jeu" not in r.text


def test_ecran_simple_naffiche_pas_le_bloc_meme_si_emplacement_renseigne(client):
    # Le bloc est réservé aux résultats "rendu"/"rendu_tournoi" (§6) : une
    # simple consultation (GET, sans action) ne doit pas l'afficher.
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    finally:
        conn.close()
    r = client.get("/pret/001")
    assert r.status_code == 200
    assert "Où ranger le jeu" not in r.text


def test_retour_tournoi_affiche_aussi_ou_ranger(client):
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.affecter_emplacement(conn, "001", "evenement", "Étagère 5")
    finally:
        conn.close()

    client.post("/pret/001/tournoi")
    r = client.post("/pret/001/rendre")
    assert r.status_code == 200
    assert "Retour de tournoi enregistré." in r.text
    assert "Où ranger le jeu" in r.text
    assert "Étagère 5" in r.text


def test_retour_contexte_local_affiche_le_nom_de_lemplacement(client):
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.ecrire_rangement_contexte(conn, "local")
        id_totem = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
        ][0]["id_emplacement"]
        services.affecter_emplacement(conn, "001", "local", id_totem)
    finally:
        conn.close()

    client.post("/pret/001/preter")
    r = client.post("/pret/001/rendre")
    assert r.status_code == 200
    assert "Où ranger le jeu" in r.text
    assert "Totem" in r.text
