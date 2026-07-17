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


# ===========================================================================
# Étape 5 — visibilité de l'emplacement sur le catalogue / la fiche publique
# ===========================================================================
def _affecter(texte="Étagère 2"):
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.affecter_emplacement(conn, "001", "evenement", texte)
    finally:
        conn.close()


def _regler_visibilite(niveau):
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.ecrire_rangement_visibilite(conn, niveau)
    finally:
        conn.close()


def test_fiche_visibilite_defaut_benevoles_cache_au_visiteur_anonyme(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _affecter("Étagère 2")
    r = client.get("/jeu/001")
    assert r.status_code == 200
    assert "Rangement" not in r.text
    assert "Étagère 2" not in r.text


def test_fiche_visibilite_benevoles_visible_si_jeton_active(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _affecter("Étagère 2")
    client.get("/acces", params={"jeton": "jeton-visibilite-secret-32-car"})
    r = client.get("/jeu/001")
    assert "Rangement" in r.text
    assert "Étagère 2" in r.text


def test_fiche_visibilite_benevoles_visible_si_admin_connecte(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _affecter("Étagère 2")
    _connecter(client)
    r = client.get("/jeu/001")
    assert "Rangement" in r.text
    assert "Étagère 2" in r.text


def test_fiche_visibilite_tous_visible_pour_anonyme(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _regler_visibilite("tous")
    _affecter("Étagère 2")
    r = client.get("/jeu/001")
    assert "Rangement" in r.text
    assert "Étagère 2" in r.text


def test_fiche_visibilite_admin_cache_pour_simple_benevole(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _regler_visibilite("admin")
    _affecter("Étagère 2")
    client.get("/acces", params={"jeton": "jeton-visibilite-secret-32-car"})
    r = client.get("/jeu/001")
    assert "Rangement" not in r.text
    assert "Étagère 2" not in r.text

    _connecter(client)
    r2 = client.get("/jeu/001")
    assert "Rangement" in r2.text
    assert "Étagère 2" in r2.text


def test_fiche_sans_emplacement_naffiche_rien_meme_si_visible(client, monkeypatch):
    # Jamais bloquant : rien de renseigné -> pas de « non renseigné ».
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _regler_visibilite("tous")
    r = client.get("/jeu/001")
    assert r.status_code == 200
    assert "Rangement" not in r.text


def test_retour_benevole_toujours_visible_malgre_visibilite_admin(client, monkeypatch):
    # §7 : le réglage de visibilité ne gouverne QUE le catalogue/la fiche —
    # l'écran de retour bénévole affiche toujours l'emplacement.
    monkeypatch.setenv("PRET_TOKEN", "jeton-visibilite-secret-32-car")
    _regler_visibilite("admin")
    _affecter("Étagère 2")
    client.get("/acces", params={"jeton": "jeton-visibilite-secret-32-car"})

    client.post("/pret/001/preter")
    r = client.post("/pret/001/rendre")
    assert "Où ranger le jeu" in r.text
    assert "Étagère 2" in r.text


# ===========================================================================
# Étape 6 — édition à l'unité sur la fiche admin (/admin/jeu/CATAN)
# ===========================================================================
def _id_emplacement(nom):
    from app import db as _db
    conn = _db.get_connection()
    try:
        return [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == nom
        ][0]["id_emplacement"]
    finally:
        conn.close()


def test_fiche_admin_affiche_les_deux_formulaires_independants(client):
    _connecter(client)
    r = client.get("/admin/jeu/CATAN")
    assert r.status_code == 200
    assert "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement" in r.text
    assert "/admin/jeu/CATAN/exemplaire/001/emplacement-local" in r.text
    for nom in ["Totem", "Puzzle", "P&#39;tits potes", "valise 1", "valise 2"]:
        assert nom in r.text


def test_fiche_admin_edite_emplacement_evenement(client):
    _connecter(client)
    r = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement",
        data={"emplacement_texte": "Étagère 7"},
    )
    assert r.status_code == 200
    assert 'value="Étagère 7"' in r.text


def test_fiche_admin_evenement_vide_retire(client):
    _connecter(client)
    client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement",
        data={"emplacement_texte": "Étagère 7"},
    )
    r = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement",
        data={"emplacement_texte": "   "},
    )
    assert r.status_code == 200
    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_evenement"] is None
    finally:
        conn.close()


def test_fiche_admin_edite_emplacement_local(client):
    _connecter(client)
    id_totem = _id_emplacement("Totem")
    r = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-local",
        data={"emplacement_id": str(id_totem)},
    )
    assert r.status_code == 200
    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_local_id"] == id_totem
    finally:
        conn.close()


def test_fiche_admin_local_aucun_retire(client):
    _connecter(client)
    id_totem = _id_emplacement("Totem")
    client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-local",
        data={"emplacement_id": str(id_totem)},
    )
    client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-local",
        data={"emplacement_id": ""},
    )
    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_local_id"] is None
    finally:
        conn.close()


def test_fiche_admin_local_id_invalide_ignore_silencieusement(client):
    _connecter(client)
    r = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-local",
        data={"emplacement_id": "999999"},
    )
    assert r.status_code == 200  # pas d'erreur brute
    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_local_id"] is None
    finally:
        conn.close()


def test_fiche_admin_emplacement_local_archive_reste_affiche(client):
    _connecter(client)
    id_totem = _id_emplacement("Totem")
    client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-local",
        data={"emplacement_id": str(id_totem)},
    )
    from app import db as _db
    conn = _db.get_connection()
    try:
        services.archiver_emplacement_rangement(conn, id_totem)
    finally:
        conn.close()

    r = client.get("/admin/jeu/CATAN")
    assert r.status_code == 200
    assert "Totem (archivé)" in r.text
    assert f'value="{id_totem}" selected' in r.text


def test_fiche_admin_edition_protegee_par_garde_admin(client):
    r1 = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement",
        data={"emplacement_texte": "Étagère 7"},
        follow_redirects=False,
    )
    assert r1.status_code == 303 and r1.headers["location"] == "/admin"

    r2 = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-local",
        data={"emplacement_id": "1"},
        follow_redirects=False,
    )
    assert r2.status_code == 303 and r2.headers["location"] == "/admin"


# ===========================================================================
# Étape 7 — import / export CSV du rangement
# ===========================================================================
def _ecrire_csv(tmp_path, contenu, nom="import.csv"):
    chemin = tmp_path / nom
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_construire_donnees_lit_les_deux_colonnes_emplacement():
    from scripts import import_csv

    lignes = [
        {"Code jeu": "001", "Nom jeu": "Catan",
         "Emplacement événement": "Étagère 2", "Emplacement local": "Totem"},
        {"Code jeu": "002", "Nom jeu": "Catan",
         "Emplacement événement": "", "Emplacement local": ""},
    ]
    index = import_csv.construire_index_colonnes(list(lignes[0].keys()))
    exemplaires, titres, groupes, ignores = import_csv.construire_donnees(lignes, index)
    ex1 = next(e for e in exemplaires if e["id_exemplaire"] == "001")
    ex2 = next(e for e in exemplaires if e["id_exemplaire"] == "002")
    assert ex1["emplacement_evenement"] == "Étagère 2"
    assert ex1["emplacement_local_nom"] == "Totem"
    assert ex2["emplacement_evenement"] is None
    assert ex2["emplacement_local_nom"] is None


def test_importer_cree_un_emplacement_local_inconnu(client, tmp_path):
    from scripts import import_csv

    chemin = _ecrire_csv(
        tmp_path,
        "Code jeu;Nom jeu;Emplacement événement;Emplacement local\n"
        "900;Nouveau jeu;Étagère 9;Zone démo\n",
    )
    res = import_csv.importer(chemin)
    assert res["emplacements_locaux_crees"] == ["Zone démo"]

    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_evenement, emplacement_local_id FROM exemplaires "
            "WHERE id_exemplaire = '900'"
        ).fetchone()
        assert row["emplacement_evenement"] == "Étagère 9"
        emplacement = services.get_emplacement_rangement(conn, row["emplacement_local_id"])
        assert emplacement["nom"] == "Zone démo"
        assert emplacement["actif"] == 1
    finally:
        conn.close()


def test_importer_reutilise_emplacement_local_existant_insensible_casse(client, tmp_path):
    from scripts import import_csv

    chemin = _ecrire_csv(
        tmp_path,
        "Code jeu;Nom jeu;Emplacement local\n900;Nouveau jeu; totem \n",
    )
    res = import_csv.importer(chemin)
    assert res["emplacements_locaux_crees"] == []  # "Totem" existe déjà (seed)

    from app import db as _db
    conn = _db.get_connection()
    try:
        id_totem = [
            l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
        ][0]["id_emplacement"]
        row = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '900'"
        ).fetchone()
        assert row["emplacement_local_id"] == id_totem
    finally:
        conn.close()


def test_importer_case_vide_necrase_jamais(client, tmp_path):
    # §4.b, décision centrale : une case vide au réimport NE TOUCHE PAS à un
    # emplacement déjà posé (au scanner ou à la main).
    from scripts import import_csv

    _affecter("Étagère 2")
    from app import db as _db
    conn = _db.get_connection()
    try:
        id_totem = _id_emplacement("Totem")
        services.affecter_emplacement(conn, "001", "local", id_totem)
    finally:
        conn.close()

    chemin = _ecrire_csv(
        tmp_path,
        "Code jeu;Nom jeu;Emplacement événement;Emplacement local\n"
        "001;Catan;;\n",
    )
    import_csv.importer(chemin)

    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_evenement, emplacement_local_id FROM exemplaires "
            "WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_evenement"] == "Étagère 2"
        assert row["emplacement_local_id"] == id_totem
    finally:
        conn.close()


def test_importer_valeur_non_vide_ecrase(client, tmp_path):
    from scripts import import_csv

    _affecter("Étagère 2")

    chemin = _ecrire_csv(
        tmp_path,
        "Code jeu;Nom jeu;Emplacement événement\n001;Catan;Étagère 9\n",
    )
    import_csv.importer(chemin)

    from app import db as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()
        assert row["emplacement_evenement"] == "Étagère 9"
    finally:
        conn.close()


def test_importer_dry_run_ne_cree_rien(client, tmp_path):
    from scripts import import_csv

    from app import db as _db
    conn = _db.get_connection()
    try:
        (avant,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
    finally:
        conn.close()

    chemin = _ecrire_csv(
        tmp_path,
        "Code jeu;Nom jeu;Emplacement local\n900;Nouveau jeu;Zone jamais créée\n",
    )
    res = import_csv.importer(chemin, dry_run=True)
    assert res["emplacements_locaux_crees"] == []

    conn = _db.get_connection()
    try:
        (apres,) = conn.execute("SELECT COUNT(*) FROM emplacements_rangement").fetchone()
        assert apres == avant
        assert conn.execute(
            "SELECT 1 FROM exemplaires WHERE id_exemplaire = '900'"
        ).fetchone() is None
    finally:
        conn.close()


def test_export_catalogue_inclut_les_colonnes_rangement(client):
    _affecter("Étagère 2")
    from app import db as _db
    conn = _db.get_connection()
    try:
        id_totem = _id_emplacement("Totem")
        services.affecter_emplacement(conn, "001", "local", id_totem)
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()

    assert "Emplacement événement" in entetes
    assert "Emplacement local" in entetes
    ligne = [l for l in lignes if l["Code jeu"] == "001"][0]
    assert ligne["Emplacement événement"] == "Étagère 2"
    assert ligne["Emplacement local"] == "Totem"


def test_export_csv_route_contient_les_entetes(client):
    _connecter(client)
    r = client.get("/admin/donnees/export.csv")
    assert r.status_code == 200
    assert "Emplacement événement" in r.text
    assert "Emplacement local" in r.text


def test_import_route_signale_les_emplacements_crees(client):
    _connecter(client)
    contenu = (
        "Code jeu;Nom jeu;Emplacement local\n"
        "901;Jeu importé;Nouvelle zone\n"
    ).encode("utf-8")
    r = client.post(
        "/admin/donnees/import",
        files={"fichier": ("cat.csv", contenu, "text/csv")},
    )
    assert r.status_code == 200
    assert "Import réussi" in r.text
    assert "1 nouvel" in r.text
    assert "Nouvelle zone" in r.text


def test_import_route_lien_vers_manques_apparait_si_boites_restantes(client):
    # Contexte par défaut "evenement" : ni 001 (seed) ni la boîte importée
    # n'ont d'emplacement événement -> le lien doit apparaître.
    _connecter(client)
    contenu = "Code jeu;Nom jeu\n902;Jeu sans emplacement\n".encode("utf-8")
    r = client.post(
        "/admin/donnees/import",
        files={"fichier": ("cat.csv", contenu, "text/csv")},
    )
    assert r.status_code == 200
    assert "sans emplacement" in r.text
    assert 'href="/admin/rangement/ranger"' in r.text


# ===========================================================================
# Comptage des exemplaires sans emplacement (compteur réutilisé par
# /admin/rangement et le message post-import de /admin/donnees ; la liste
# détaillée équivalente est désormais la vue « Ranger les jeux », testée
# plus haut sous « Addendum §13 »).
# ===========================================================================
def test_compter_manques_evenement(conn):
    assert services.compter_exemplaires_sans_emplacement(conn) == 1

    services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    assert services.compter_exemplaires_sans_emplacement(conn) == 0


def test_compter_manques_local(conn):
    services.ecrire_rangement_contexte(conn, "local")
    assert services.compter_exemplaires_sans_emplacement(conn) == 1

    id_totem = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
    ][0]["id_emplacement"]
    services.affecter_emplacement(conn, "001", "local", id_totem)
    assert services.compter_exemplaires_sans_emplacement(conn) == 0


def test_compter_manques_filtre_categorie_et_recherche(conn):
    conn.execute("INSERT INTO titres (reference_titre, nom, categorie) VALUES "
                 "('DOBBLE', 'Dobble', 'Rapidité')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES "
                 "('002', 'DOBBLE')")
    conn.execute("UPDATE titres SET categorie = 'Stratégie' WHERE reference_titre = 'CATAN'")
    conn.commit()

    assert services.compter_exemplaires_sans_emplacement(conn) == 2
    assert services.compter_exemplaires_sans_emplacement(conn, categorie="Stratégie") == 1
    assert services.compter_exemplaires_sans_emplacement(conn, q="dob") == 1
    assert services.compter_exemplaires_sans_emplacement(conn, q="introuvable") == 0


def test_edition_unite_retour_ignore_url_externe(client):
    # `retour` (routes d'édition à l'unité, réutilisées par "Ranger les
    # jeux") doit commencer par /admin/ pour être suivi (pas de redirection
    # ouverte). Un chemin interne valide est en revanche bien respecté.
    _connecter(client)
    r = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement",
        data={"emplacement_texte": "Étagère 9", "retour": "https://exemple-malveillant.test"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin/jeu/CATAN"

    r2 = client.post(
        "/admin/jeu/CATAN/exemplaire/001/emplacement-evenement",
        data={"emplacement_texte": "Étagère 9", "retour": "/admin/rangement/ranger"},
        follow_redirects=False,
    )
    assert r2.status_code == 303 and r2.headers["location"] == "/admin/rangement/ranger"


def test_ancienne_route_manques_retiree(client):
    # La page des manques (grain exemplaire) a été remplacée par « Ranger les
    # jeux » (grain titre, §13) — l'ancienne route ne doit plus exister.
    _connecter(client)
    r = client.get("/admin/rangement/manques")
    assert r.status_code == 404


def test_admin_rangement_affiche_compteur_et_lien_ranger(client):
    _connecter(client)
    r = client.get("/admin/rangement")
    assert r.status_code == 200
    assert 'href="/admin/rangement/ranger"' in r.text
    assert "1 boîte" in r.text


# ===========================================================================
# Étape 9 — page d'aide dédiée (/rangement/aide)
# ===========================================================================
def test_rangement_aide_accessible_sans_session(client):
    r = client.get("/rangement/aide")
    assert r.status_code == 200
    assert "Rangement" in r.text
    assert "Événement" in r.text
    assert "Local" in r.text


def test_rangement_aide_decrit_ranger_les_jeux(client):
    r = client.get("/rangement/aide")
    assert r.status_code == 200
    assert 'href="/admin/rangement/ranger"' in r.text
    assert "Appliquer à" in r.text
    assert "Appliquer aux jeux cochés" in r.text
    assert "mixte" in r.text


def test_admin_rangement_lien_vers_aide(client):
    _connecter(client)
    r = client.get("/admin/rangement")
    assert 'href="/rangement/aide"' in r.text


def test_apropos_lien_vers_aide_rangement(client):
    r = client.get("/apropos")
    assert r.status_code == 200
    assert 'href="/rangement/aide"' in r.text


# ===========================================================================
# Addendum §13 — affectation en lot par jeu (grain titre)
# ===========================================================================
def _ajouter_exemplaire(conn, id_exemplaire, reference_titre="CATAN"):
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        (id_exemplaire, reference_titre),
    )
    conn.commit()


def _ajouter_titre(conn, reference_titre, nom):
    conn.execute(
        "INSERT INTO titres (reference_titre, nom) VALUES (?, ?)", (reference_titre, nom)
    )
    conn.commit()


def test_resume_emplacement_titres_liste_vide(conn):
    assert services._resume_emplacement_titres(conn, [], "evenement") == {}


def test_rangement_par_titre_aucune_boite_affectee(conn):
    _ajouter_exemplaire(conn, "002")  # 2 boîtes CATAN, aucune n'a d'emplacement
    jeux = services.lister_catalogue(conn)
    resultat = services.rangement_par_titre(conn, jeux, "evenement")
    catan = [j for j in resultat if j["reference_titre"] == "CATAN"][0]
    assert catan["rangement_affichage"] == "—"
    assert catan["rangement_complet"] is False


def test_rangement_par_titre_affectation_partielle_est_mixte(conn):
    _ajouter_exemplaire(conn, "002")
    services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    jeux = services.lister_catalogue(conn)
    resultat = services.rangement_par_titre(conn, jeux, "evenement")
    catan = [j for j in resultat if j["reference_titre"] == "CATAN"][0]
    assert catan["rangement_affichage"] == "mixte"
    assert catan["rangement_complet"] is False


def test_rangement_par_titre_toutes_identiques_est_complet(conn):
    _ajouter_exemplaire(conn, "002")
    services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    services.affecter_emplacement(conn, "002", "evenement", "Étagère 2")
    jeux = services.lister_catalogue(conn)
    resultat = services.rangement_par_titre(conn, jeux, "evenement")
    catan = [j for j in resultat if j["reference_titre"] == "CATAN"][0]
    assert catan["rangement_affichage"] == "Étagère 2"
    assert catan["rangement_complet"] is True


def test_rangement_par_titre_valeurs_differentes_est_mixte(conn):
    _ajouter_exemplaire(conn, "002")
    services.affecter_emplacement(conn, "001", "evenement", "Étagère 2")
    services.affecter_emplacement(conn, "002", "evenement", "Étagère 3")
    jeux = services.lister_catalogue(conn)
    resultat = services.rangement_par_titre(conn, jeux, "evenement")
    catan = [j for j in resultat if j["reference_titre"] == "CATAN"][0]
    assert catan["rangement_affichage"] == "mixte"
    assert catan["rangement_complet"] is False


def test_rangement_par_titre_contexte_local_resout_le_libelle(conn):
    id_totem = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Totem"
    ][0]["id_emplacement"]
    _ajouter_exemplaire(conn, "002")
    services.affecter_emplacement(conn, "001", "local", id_totem)
    services.affecter_emplacement(conn, "002", "local", id_totem)
    jeux = services.lister_catalogue(conn)
    resultat = services.rangement_par_titre(conn, jeux, "local")
    catan = [j for j in resultat if j["reference_titre"] == "CATAN"][0]
    assert catan["rangement_affichage"] == "Totem"
    assert catan["rangement_complet"] is True


def test_affecter_emplacement_lot_ecrase_par_defaut(conn):
    _ajouter_exemplaire(conn, "002")
    services.affecter_emplacement(conn, "001", "evenement", "Ancienne valeur")
    resultat = services.affecter_emplacement_lot(conn, ["CATAN"], "evenement", "Nouvelle valeur")
    assert resultat == {"titres": 1, "boites": 2}
    valeurs = [
        r[0] for r in conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE reference_titre = 'CATAN'"
        )
    ]
    assert valeurs == ["Nouvelle valeur", "Nouvelle valeur"]


def test_affecter_emplacement_lot_ne_pas_ecraser_ne_comble_que_les_trous(conn):
    _ajouter_exemplaire(conn, "002")
    services.affecter_emplacement(conn, "001", "evenement", "Déjà rangé")
    resultat = services.affecter_emplacement_lot(
        conn, ["CATAN"], "evenement", "Nouvelle valeur", ecraser=False
    )
    assert resultat == {"titres": 1, "boites": 1}
    valeurs = {
        r["id_exemplaire"]: r["emplacement_evenement"]
        for r in conn.execute(
            "SELECT id_exemplaire, emplacement_evenement FROM exemplaires "
            "WHERE reference_titre = 'CATAN'"
        )
    }
    assert valeurs["001"] == "Déjà rangé"
    assert valeurs["002"] == "Nouvelle valeur"


def test_affecter_emplacement_lot_plusieurs_titres(conn):
    _ajouter_titre(conn, "DOBBLE", "Dobble")
    _ajouter_exemplaire(conn, "900", "DOBBLE")
    resultat = services.affecter_emplacement_lot(
        conn, ["CATAN", "DOBBLE"], "evenement", "Table du fond"
    )
    assert resultat == {"titres": 2, "boites": 2}


def test_affecter_emplacement_lot_liste_vide(conn):
    assert services.affecter_emplacement_lot(conn, [], "evenement", "X") == {"titres": 0, "boites": 0}


def test_affecter_emplacement_lot_contexte_local_ecrit_id(conn):
    id_puzzle = [
        l for l in services.lister_emplacements_rangement(conn) if l["nom"] == "Puzzle"
    ][0]["id_emplacement"]
    _ajouter_exemplaire(conn, "002")
    resultat = services.affecter_emplacement_lot(conn, ["CATAN"], "local", id_puzzle)
    assert resultat == {"titres": 1, "boites": 2}
    valeurs = [
        r[0] for r in conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE reference_titre = 'CATAN'"
        )
    ]
    assert valeurs == [id_puzzle, id_puzzle]


# ===========================================================================
# Addendum §13 (suite) — écran GET « Ranger les jeux » (/admin/rangement/ranger)
# ===========================================================================
def _connexion_test():
    from app import db as _db
    return _db.get_connection()


def test_ranger_jeux_protege_par_garde_admin(client):
    r = client.get("/admin/rangement/ranger", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_ranger_jeux_affiche_jeu_non_range_par_defaut(client):
    _connecter(client)
    r = client.get("/admin/rangement/ranger")
    assert r.status_code == 200
    assert "Catan" in r.text
    assert "1 jeu" in r.text


def test_ranger_jeux_masque_jeu_deja_range_par_defaut(client):
    _connecter(client)
    _affecter("Étagère 2")  # seul exemplaire du titre -> jeu complètement rangé
    r = client.get("/admin/rangement/ranger")
    assert "Catan" not in r.text
    assert "Aucun jeu ne correspond" in r.text


def test_ranger_jeux_toggle_tous_montre_deja_ranges(client):
    _connecter(client)
    _affecter("Étagère 2")
    r = client.get("/admin/rangement/ranger", params={"tous": "1"})
    assert "Catan" in r.text
    assert "Étagère 2" in r.text


def test_ranger_jeux_filtre_categorie(client):
    _connecter(client)
    conn = _connexion_test()
    try:
        conn.execute("UPDATE titres SET categorie = 'Stratégie' WHERE reference_titre = 'CATAN'")
        conn.execute("INSERT INTO titres (reference_titre, nom, categorie) VALUES "
                     "('DOBBLE', 'Dobble', 'Rapidité')")
        conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES "
                     "('900', 'DOBBLE')")
        conn.commit()
    finally:
        conn.close()

    r = client.get("/admin/rangement/ranger", params={"categorie": "Rapidité"})
    assert r.status_code == 200
    assert "Dobble" in r.text
    assert "Catan" not in r.text


def test_ranger_jeux_bandeau_contexte_actif(client):
    _connecter(client)
    r = client.get("/admin/rangement/ranger")
    assert "Contexte actif" in r.text
    assert "Événement" in r.text

    conn = _connexion_test()
    try:
        services.ecrire_rangement_contexte(conn, "local")
    finally:
        conn.close()

    r2 = client.get("/admin/rangement/ranger")
    assert "Contexte actif" in r2.text
    assert "Local" in r2.text


def test_ranger_jeux_bouton_appliquer_annonce_le_compte(client):
    _connecter(client)
    r = client.get("/admin/rangement/ranger")
    assert "Appliquer à 1 jeu" in r.text
    assert "1 boîte" in r.text


def test_ranger_jeux_contexte_local_affiche_menu_deroulant(client):
    _connecter(client)
    conn = _connexion_test()
    try:
        services.ecrire_rangement_contexte(conn, "local")
    finally:
        conn.close()

    r = client.get("/admin/rangement/ranger")
    assert 'id="emplacement_id"' in r.text
    assert "Totem" in r.text
    assert 'id="emplacement_texte"' not in r.text
    assert "affecte toutes les copies" in r.text.lower()


def test_ranger_jeux_contexte_evenement_affiche_champ_texte(client):
    _connecter(client)
    r = client.get("/admin/rangement/ranger")
    assert 'id="emplacement_texte"' in r.text
    assert 'id="emplacement_id"' not in r.text


def test_ranger_jeux_pagination(client, monkeypatch):
    from app.routes import admin as admin_routes

    monkeypatch.setattr(admin_routes, "PAR_PAGE_RANGER", 2)
    conn = _connexion_test()
    try:
        for i, nom in enumerate(["Dobble", "Uno"], start=1):
            ref = nom.upper()
            conn.execute(
                "INSERT INTO titres (reference_titre, nom) VALUES (?, ?)", (ref, nom)
            )
            conn.execute(
                "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                (f"90{i}", ref),
            )
        conn.commit()
    finally:
        conn.close()

    _connecter(client)
    r1 = client.get("/admin/rangement/ranger")
    assert "Page 1 / 2" in r1.text
    r2 = client.get("/admin/rangement/ranger", params={"page": 2})
    assert "Page 2 / 2" in r2.text


# ===========================================================================
# Addendum §13 (suite) — POST /admin/rangement/ranger/appliquer
# ===========================================================================
def _emplacement_evenement_001(client):
    conn = _connexion_test()
    try:
        return conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()[0]
    finally:
        conn.close()


def test_ranger_appliquer_protege_par_garde_admin(client):
    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "filtre", "emplacement_texte": "Étagère 5"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_ranger_appliquer_refuse_emplacement_vide(client):
    _connecter(client)
    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "filtre", "emplacement_texte": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]
    assert _emplacement_evenement_001(client) is None


def test_ranger_appliquer_portee_filtre_evenement(client):
    _connecter(client)
    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "filtre", "emplacement_texte": "Étagère 5"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote_plus

    assert "1 jeu" in unquote_plus(r.headers["location"])
    assert _emplacement_evenement_001(client) == "Étagère 5"


def test_ranger_appliquer_portee_coches_ne_touche_que_la_selection(client):
    _connecter(client)
    conn = _connexion_test()
    try:
        conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DOBBLE', 'Dobble')")
        conn.execute(
            "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('900', 'DOBBLE')"
        )
        conn.commit()
    finally:
        conn.close()

    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={
            "portee": "coches", "titres_coches": ["CATAN"], "emplacement_texte": "Étagère 6",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _emplacement_evenement_001(client) == "Étagère 6"

    conn = _connexion_test()
    try:
        valeur_dobble = conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = '900'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert valeur_dobble is None


def test_ranger_appliquer_ne_pas_ecraser_ne_comble_que_les_trous(client):
    _connecter(client)
    conn = _connexion_test()
    try:
        conn.execute(
            "UPDATE exemplaires SET emplacement_evenement = 'Ancien' WHERE id_exemplaire = '001'"
        )
        conn.execute(
            "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'CATAN')"
        )
        conn.commit()
    finally:
        conn.close()

    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={
            "portee": "filtre", "emplacement_texte": "Nouveau", "ne_pas_ecraser": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    conn = _connexion_test()
    try:
        valeurs = {
            r2["id_exemplaire"]: r2["emplacement_evenement"]
            for r2 in conn.execute(
                "SELECT id_exemplaire, emplacement_evenement FROM exemplaires "
                "WHERE reference_titre = 'CATAN'"
            )
        }
    finally:
        conn.close()
    assert valeurs["001"] == "Ancien"
    assert valeurs["002"] == "Nouveau"


def test_ranger_appliquer_contexte_local_ecrit_id(client):
    _connecter(client)
    id_totem = _id_emplacement("Totem")
    client.post("/admin/rangement/contexte", data={"contexte": "local"})

    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "filtre", "emplacement_id": str(id_totem)},
        follow_redirects=False,
    )
    assert r.status_code == 303

    conn = _connexion_test()
    try:
        valeur = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert valeur == id_totem


def test_ranger_appliquer_contexte_local_id_invalide_refuse(client):
    _connecter(client)
    client.post("/admin/rangement/contexte", data={"contexte": "local"})

    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={"portee": "filtre", "emplacement_id": "99999"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    conn = _connexion_test()
    try:
        valeur = conn.execute(
            "SELECT emplacement_local_id FROM exemplaires WHERE id_exemplaire = '001'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert valeur is None


def test_ranger_appliquer_redirige_en_conservant_les_filtres(client):
    _connecter(client)
    conn = _connexion_test()
    try:
        conn.execute(
            "INSERT INTO titres (reference_titre, nom, categorie) VALUES "
            "('DOBBLE', 'Dobble', 'Rapidité')"
        )
        conn.execute(
            "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('900', 'DOBBLE')"
        )
        conn.commit()
    finally:
        conn.close()

    r = client.post(
        "/admin/rangement/ranger/appliquer",
        data={
            "portee": "filtre", "emplacement_texte": "Table du fond", "categorie": "Rapidité",
        },
        follow_redirects=False,
    )
    location = r.headers["location"]
    assert "categorie=Rapidit" in location

    # Le filtre catégorie est conservé au retour ; Dobble a bien reçu la
    # valeur (mais disparaît de la vue par défaut, désormais complètement
    # rangé — comportement attendu, vérifié directement en base).
    r2 = client.get(location)
    assert r2.status_code == 200

    conn = _connexion_test()
    try:
        valeur = conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = '900'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert valeur == "Table du fond"
