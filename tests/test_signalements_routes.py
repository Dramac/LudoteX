"""
Écrans bénévole du carnet de maintenance (docs/conception-signalements.md,
lot 2 de docs/prompt-impl-signalements.md). Les services (CRUD des
catégories, creer_signalement, signalements_ouverts...) sont déjà testés dans
tests/test_signalements.py — ici on vérifie le CÂBLAGE : protection par
jeton, écran qui n'écrit rien tant qu'on n'a pas appuyé sur « Envoyer »,
rattrapages sans perte de saisie, et le bandeau d'alerte qui se voit AVANT
toute action.

Fixture sur le patron de tests/test_transfert_routes.py.
"""

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.tournoi import db as tdb
    from app.planning import db as pdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _nb_signalements(tmp_path):
    from app import db
    conn = db.get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM signalements").fetchone()[0]
    finally:
        conn.close()


def _archiver(tmp_path, id_categorie):
    from app import db, services
    conn = db.get_connection()
    try:
        services.archiver_categorie_signalement(conn, id_categorie)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Protection par jeton
# ---------------------------------------------------------------------------
def test_les_deux_routes_refusent_sans_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")

    assert client.get("/pret/001/signaler").status_code == 403
    assert client.post("/pret/001/signaler").status_code == 403


# ---------------------------------------------------------------------------
# Écran de signalement — n'écrit rien, propose les catégories actives
# ---------------------------------------------------------------------------
def test_ecran_affiche_les_categories_actives(client, tmp_path):
    avant = _nb_signalements(tmp_path)

    r = client.get("/pret/001/signaler")
    assert r.status_code == 200
    for nom in ("Pièce manquante", "Règle manquante", "Boîte abîmée",
                "Matériel abîmé", "Autre"):
        assert nom in r.text
    assert _nb_signalements(tmp_path) == avant  # un simple GET n'écrit rien


def test_ecran_n_affiche_pas_une_categorie_archivee(client, tmp_path):
    _archiver(tmp_path, 1)  # « Pièce manquante »

    r = client.get("/pret/001/signaler")
    assert r.status_code == 200
    assert "Pièce manquante" not in r.text
    assert "Règle manquante" in r.text


def test_ecran_sur_boite_inconnue_est_un_404(client):
    r = client.get("/pret/999/signaler")
    assert r.status_code == 404


def test_consigne_donnee_personnelle_presente(client):
    r = client.get("/pret/001/signaler")
    assert "Décrivez la boîte, jamais une personne" in r.text


# ---------------------------------------------------------------------------
# Envoi réussi
# ---------------------------------------------------------------------------
def test_envoi_reussi_cree_le_signalement_et_confirme(client, tmp_path):
    avant = _nb_signalements(tmp_path)

    r = client.post("/pret/001/signaler",
                     data={"id_categorie": "3", "texte": "Il manque un dé rouge."})
    assert r.status_code == 200
    assert "Signalement envoyé" in r.text
    assert _nb_signalements(tmp_path) == avant + 1

    from app import db, services
    conn = db.get_connection()
    try:
        ouverts = services.signalements_ouverts(conn, "001")
    finally:
        conn.close()
    assert len(ouverts) == 1
    assert ouverts[0]["categorie_nom"] == "Boîte abîmée"
    assert ouverts[0]["texte"] == "Il manque un dé rouge."


def test_envoi_reussi_sans_texte(client, tmp_path):
    """Le détail est facultatif : la catégorie seule suffit (§3 de la note)."""
    r = client.post("/pret/001/signaler", data={"id_categorie": "1"})
    assert r.status_code == 200
    assert "Signalement envoyé" in r.text
    assert _nb_signalements(tmp_path) == 1


# ---------------------------------------------------------------------------
# Rattrapages — jamais un formulaire vierge
# ---------------------------------------------------------------------------
def test_categorie_absente_reaffiche_avec_message_et_saisie_conservee(client, tmp_path):
    avant = _nb_signalements(tmp_path)

    r = client.post("/pret/001/signaler", data={"texte": "Détail à ne pas perdre"})
    assert r.status_code == 400
    assert "Choisissez une catégorie" in r.text
    assert "Détail à ne pas perdre" in r.text  # la saisie n'est pas perdue
    assert _nb_signalements(tmp_path) == avant  # rien n'a été écrit


def test_categorie_inconnue_reaffiche_avec_message(client, tmp_path):
    avant = _nb_signalements(tmp_path)

    r = client.post("/pret/001/signaler", data={"id_categorie": "9999", "texte": "x"})
    assert r.status_code == 400
    assert _nb_signalements(tmp_path) == avant


def test_categorie_archivee_entre_temps_est_refusee(client, tmp_path):
    """
    La validation relit la liste des catégories ACTIVES et ne fait jamais
    confiance au formulaire (§8 de la note) : une catégorie archivée après
    l'affichage de l'écran mais avant l'envoi est refusée.
    """
    avant = _nb_signalements(tmp_path)
    _archiver(tmp_path, 2)  # « Règle manquante », archivée après coup

    r = client.post("/pret/001/signaler",
                     data={"id_categorie": "2", "texte": "manque le livret"})
    assert r.status_code == 400
    assert "Cette catégorie" in r.text and "Choisissez-en une autre" in r.text
    assert "manque le livret" in r.text  # saisie conservée
    assert _nb_signalements(tmp_path) == avant


def test_texte_non_numerique_est_traite_comme_absent(client, tmp_path):
    avant = _nb_signalements(tmp_path)

    r = client.post("/pret/001/signaler", data={"id_categorie": "abc"})
    assert r.status_code == 400
    assert _nb_signalements(tmp_path) == avant


# ---------------------------------------------------------------------------
# Texte trop long — borné, jamais refusé
# ---------------------------------------------------------------------------
def test_texte_trop_long_est_borne_sans_etre_refuse(client, tmp_path):
    from app import services

    texte = "x" * 1000
    r = client.post("/pret/001/signaler", data={"id_categorie": "1", "texte": texte})
    assert r.status_code == 200
    assert "Signalement envoyé" in r.text

    from app import db
    conn = db.get_connection()
    try:
        enregistre = conn.execute(
            "SELECT texte FROM signalements WHERE id_exemplaire = '001'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert len(enregistre) == services.LONGUEUR_MAX_TEXTE_SIGNALEMENT


# ---------------------------------------------------------------------------
# Bandeau d'alerte — visible AVANT toute action, sur un simple GET
# ---------------------------------------------------------------------------
def test_bandeau_absent_sans_signalement_ouvert(client):
    r = client.get("/pret/001")
    assert "Signalements en cours" not in r.text


def test_bandeau_present_des_le_get_avant_toute_action(client):
    client.post("/pret/001/signaler", data={"id_categorie": "3", "texte": "coin abîmé"})

    r = client.get("/pret/001")
    assert r.status_code == 200
    assert "Signalements en cours" in r.text
    assert "Boîte abîmée" in r.text
    assert "coin abîmé" in r.text


def test_bandeau_disparait_une_fois_le_signalement_traite(client):
    client.post("/pret/001/signaler", data={"id_categorie": "3"})

    from app import db, services
    conn = db.get_connection()
    try:
        (id_signalement,) = conn.execute(
            "SELECT id_signalement FROM signalements WHERE id_exemplaire = '001'"
        ).fetchone()
        services.traiter_signalement(conn, id_signalement)
    finally:
        conn.close()

    r = client.get("/pret/001")
    assert "Signalements en cours" not in r.text


def test_signalement_n_empeche_jamais_le_pret(client):
    """
    Le signalement ne bloque jamais le prêt (§6 de la note) : le bouton
    « Prêter » reste présent et fonctionnel malgré un signalement ouvert.
    """
    client.post("/pret/001/signaler", data={"id_categorie": "1"})

    r = client.get("/pret/001")
    assert "Prêter" in r.text

    r = client.post("/pret/001/preter")
    assert r.status_code == 200
    assert "resultat-ok" in r.text  # le prêt a bien réussi


# ---------------------------------------------------------------------------
# Lien permanent — présent dans les trois états de la boîte
# ---------------------------------------------------------------------------
def test_lien_signaler_present_boite_disponible(client):
    assert "/pret/001/signaler" in client.get("/pret/001").text


def test_lien_signaler_present_boite_sortie(client):
    client.post("/pret/001/preter")
    assert "/pret/001/signaler" in client.get("/pret/001").text


def test_lien_signaler_present_sortie_tournoi(client):
    client.post("/pret/001/tournoi")
    assert "/pret/001/signaler" in client.get("/pret/001").text


# ---------------------------------------------------------------------------
# Fermeture depuis la fiche (lot agora-4) — §2 de la note, mis à jour : ce
# n'est plus l'administrateur seul qui referme. La logique est FACTORISÉE avec
# les deux carnets (`services.fermer_signalement`, promue hors des modules de
# routes au lot agora-5, et `carnet.journaliser_traite`) ; la journalisation en
# couvre l'idempotence côté tests/test_journal_appels.py.
# ---------------------------------------------------------------------------
def _creer_signalement(id_exemplaire, id_categorie=3, texte=None):
    """Crée un signalement directement en base (patron de `_archiver` plus
    haut) : le geste HTTP de création est déjà couvert par ses propres tests,
    inutile de le rejouer pour préparer ceux-ci."""
    from app import db, services

    conn = db.get_connection()
    try:
        return services.creer_signalement(conn, id_exemplaire, id_categorie, texte)
    finally:
        conn.close()


def _ajouter_deuxieme_boite():
    from app import db

    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
        conn.execute(
            "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'DIXIT')"
        )
        conn.commit()
    finally:
        conn.close()


def test_benevole_muni_du_jeton_referme_un_signalement(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    client.cookies.set("jeton_pret", "jeton-test-secret-32-caracteres")
    id_signalement = _creer_signalement("001")

    r = client.post(f"/pret/001/signalements/{id_signalement}/traiter")
    assert r.status_code == 200
    assert "Signalement refermé" in r.text
    assert "Signalements en cours" not in r.text  # le bandeau ne le liste plus

    from app import db, services
    conn = db.get_connection()
    try:
        assert services.get_signalement(conn, id_signalement)["traite_le"] is not None
    finally:
        conn.close()


def test_fermeture_refusee_sans_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    id_signalement = _creer_signalement("001")

    r = client.post(f"/pret/001/signalements/{id_signalement}/traiter")
    assert r.status_code == 403

    from app import db, services
    conn = db.get_connection()
    try:
        assert services.get_signalement(conn, id_signalement)["traite_le"] is None
    finally:
        conn.close()


def test_second_appui_reste_idempotent_sans_erreur(client):
    id_signalement = _creer_signalement("001")

    client.post(f"/pret/001/signalements/{id_signalement}/traiter")
    r = client.post(f"/pret/001/signalements/{id_signalement}/traiter")
    assert r.status_code == 200
    assert "Signalement refermé" in r.text


def test_signalement_d_une_autre_boite_est_refuse(client):
    """Une URL forgée (id_exemplaire de l'écran, id_signalement d'une autre
    boîte) ne referme rien — contrôle de cohérence exigé par le lot."""
    _ajouter_deuxieme_boite()
    id_signalement = _creer_signalement("002")

    r = client.post(f"/pret/001/signalements/{id_signalement}/traiter")
    assert r.status_code == 200
    assert "n'existe plus ou ne correspond pas à cette boîte" in r.text

    from app import db, services
    conn = db.get_connection()
    try:
        assert services.get_signalement(conn, id_signalement)["traite_le"] is None
    finally:
        conn.close()


def test_signalement_inconnu_est_refuse(client):
    r = client.post("/pret/001/signalements/999999/traiter")
    assert r.status_code == 200
    assert "n'existe plus ou ne correspond pas à cette boîte" in r.text


def test_fermeture_sur_boite_inconnue_est_un_404(client):
    id_signalement = _creer_signalement("001")
    r = client.post(f"/pret/999/signalements/{id_signalement}/traiter")
    assert r.status_code == 404
