"""
Annonce de l'écran de salle : une porte d'entrée à donnée personnelle,
refermée (constat RGPD-01 de l'audit de pré-production).

L'annonce est un texte libre tapé par le bureau et servi AU PUBLIC sur /live et
/live/data. Ce fichier verrouille les quatre gestes qui la rendent éphémère :

1. une durée d'affichage obligatoire et bornée, jamais bloquante ;
2. une consigne sous le champ ;
3. (le journal sans le texte : tests/test_journal_interdits.py et
   tests/test_journal_appels.py) ;
4. un texte EFFACÉ, pas seulement masqué — au formulaire, au démarrage, à
   chaque sauvegarde, à la clôture, à la réinitialisation de la formation —
   et absent de toute archive.

Aucun exemple ne nomme personne : les textes d'annonce sont des situations.
"""

import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from app import services

MOT_DE_PASSE = "secret-admin-123"
PANNEAUX = {"panneau_chiffres": "1", "panneau_tournois": "1",
            "panneau_programme": "1", "panneau_mouvements": "1"}


@pytest.fixture
def chemins(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    db.init_db()
    return tmp_path


@pytest.fixture
def client(chemins, monkeypatch):
    from fastapi.testclient import TestClient

    from app import admin_auth
    from app.main import app

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    c = TestClient(app)
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return c


def _conn():
    from app import db
    return db.get_connection()


def _parametre(cle):
    conn = _conn()
    try:
        ligne = conn.execute(
            "SELECT valeur FROM parametres WHERE cle = ?", (cle,)).fetchone()
    finally:
        conn.close()
    return ligne[0] if ligne else None


def _ecrire(**valeurs):
    """Écrit directement en base (annonce d'avant ce lot, échéance passée…)."""
    conn = _conn()
    try:
        for cle, valeur in valeurs.items():
            services.ecrire_parametre(conn, cle, valeur)
    finally:
        conn.close()


def _poser(client, texte, duree):
    return client.post("/admin/ecran-salle",
                       data={"annonce": texte, "annonce_duree": duree, **PANNEAUX})


def _minutes_jusqua_echeance():
    echeance = datetime.fromisoformat(_parametre(services.CLE_ANNONCE_EXPIRE))
    return round((echeance - datetime.now(timezone.utc)).total_seconds() / 60)


def _passee():
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 1. Durée obligatoire, bornée, jamais bloquante — les cinq cas limites
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("saisie, attendue, phrase", [
    ("", 30, "Aucune durée indiquée : affichage limité à 30 minutes."),
    ("0", 30, "Une annonce a toujours une durée : affichage limité à 30 minutes."),
    ("-5", 30, "Une annonce a toujours une durée : affichage limité à 30 minutes."),
    ("une heure", 30, "Durée « une heure » illisible : affichage limité à 30 minutes."),
    ("5000", 720, "Durée ramenée à 12 heures, le maximum pour une annonce."),
])
def test_duree_corrigee_jamais_refusee_jamais_illimitee(client, saisie, attendue, phrase):
    r = _poser(client, "Tombola à 15 h, stand accueil", saisie)
    assert r.status_code == 200
    # Publiée quand même (geste d'urgence) …
    assert client.get("/live/data").json()["annonce"] == "Tombola à 15 h, stand accueil"
    # … avec une échéance, jamais « sans limite » …
    assert _parametre(services.CLE_ANNONCE_EXPIRE)
    assert abs(_minutes_jusqua_echeance() - attendue) <= 1
    assert "sans limite" not in r.text
    # … et le message le dit, en « attention » pour qu'on le voie.
    assert phrase in r.text
    assert "resultat-attention" in r.text


def test_duree_valide_prise_telle_quelle(client):
    r = _poser(client, "Tombola à 15 h", "90")
    assert abs(_minutes_jusqua_echeance() - 90) <= 1
    assert "Annonce enregistrée, affichée en salle jusqu&#39;à" in r.text
    assert "resultat-ok" in r.text
    assert "limité à" not in r.text


def test_duree_maximum_acceptee_sans_correction(client):
    r = _poser(client, "Tombola à 15 h", str(services.DUREE_ANNONCE_MAX_MIN))
    assert abs(_minutes_jusqua_echeance() - services.DUREE_ANNONCE_MAX_MIN) <= 1
    assert "ramenée" not in r.text


def test_formulaire_prerempli_court_et_consigne_sous_le_champ(client):
    page = client.get("/admin/ecran-salle").text
    assert 'value="30"' in page
    assert 'max="720"' in page
    assert "placeholder=\"illimitée\"" not in page
    assert ">illimitée<" not in page
    # Patron de la consigne du signalement, avec ce qu'il faut écrire à la place.
    assert "Décrivez une situation, jamais" in page
    assert "«&nbsp;Un enfant attend ses parents à\n        l'accueil&nbsp;», sans son nom." in page
    assert 'aria-describedby="annonce-consigne"' in page


def test_formulaire_repropose_les_minutes_restantes(client):
    _poser(client, "Tombola à 15 h", "90")
    page = client.get("/admin/ecran-salle").text
    assert 'value="90"' in page or 'value="89"' in page
    assert "Affichée en salle jusqu'à" in page


def test_duree_annonce_regles():
    assert services.duree_annonce("45") == (45, None)
    assert services.duree_annonce(" 45 ")[0] == 45
    assert services.duree_annonce(None)[0] == services.DUREE_ANNONCE_DEFAUT_MIN
    assert services.duree_annonce("721")[0] == services.DUREE_ANNONCE_MAX_MIN


# ---------------------------------------------------------------------------
# Affichage : jamais sans échéance lisible, et /live/data en lecture seule
# ---------------------------------------------------------------------------
def test_annonce_d_avant_sans_echeance_n_est_plus_affichee(client):
    # Une annonce « illimitée » enregistrée avant ce lot : plus rien à l'écran.
    _ecrire(**{services.CLE_ANNONCE: "Vestiaire fermé à 18 h"})
    assert "annonce" not in client.get("/live/data").json()
    page = client.get("/admin/ecran-salle").text
    assert "Vestiaire fermé" not in page
    assert "n'avait pas de durée" in page


def test_echeance_illisible_masque_plutot_qu_afficher(client):
    _ecrire(**{services.CLE_ANNONCE: "Tombola à 15 h",
               services.CLE_ANNONCE_EXPIRE: "pas une date"})
    assert "annonce" not in client.get("/live/data").json()
    assert client.get("/live").status_code == 200


def test_live_data_n_expose_plus_un_texte_expire(client):
    _poser(client, "Tombola à 15 h", "30")
    _ecrire(**{services.CLE_ANNONCE_EXPIRE: _passee()})
    assert "annonce" not in client.get("/live/data").json()
    assert "Tombola" not in client.get("/live").text


def test_les_lectures_n_ecrivent_jamais(client):
    """
    /live, /live/data, le tableau de bord et la supervision appellent
    `annonce_active` : un texte expiré reste en base après eux. Ce n'est pas
    à un chemin de lecture de l'effacer.
    """
    _poser(client, "Tombola à 15 h", "30")
    _ecrire(**{services.CLE_ANNONCE_EXPIRE: _passee()})
    for url in ("/live", "/live/data", "/admin", "/admin/supervision",
                "/admin/ecran-salle"):
        client.get(url)
    assert _parametre(services.CLE_ANNONCE) == "Tombola à 15 h"


# ---------------------------------------------------------------------------
# 4. Effacer, pas seulement masquer
# ---------------------------------------------------------------------------
def test_enregistrer_la_page_efface_un_texte_expire(client):
    _poser(client, "Tombola à 15 h", "30")
    _ecrire(**{services.CLE_ANNONCE_EXPIRE: _passee()})
    # Le bureau revient sur la page, change un panneau : le texte expiré part.
    client.post("/admin/ecran-salle",
                data={"annonce": "", "annonce_duree": "30", **PANNEAUX})
    assert _parametre(services.CLE_ANNONCE) is None
    assert _parametre(services.CLE_ANNONCE_EXPIRE) is None


def test_effacer_supprime_texte_et_echeance(client):
    _poser(client, "Tombola à 15 h", "30")
    r = client.post("/admin/ecran-salle",
                    data={"annonce": "", "annonce_duree": "", **PANNEAUX})
    assert "Annonce effacée." in r.text
    assert _parametre(services.CLE_ANNONCE) is None
    assert _parametre(services.CLE_ANNONCE_EXPIRE) is None


def test_purger_annonce_expiree_epargne_l_annonce_en_cours(chemins):
    conn = _conn()
    try:
        services.poser_annonce(conn, "Tombola à 15 h", 30)
        assert services.purger_annonce_expiree(conn) is False
        assert services.lire_parametre(conn, services.CLE_ANNONCE) == "Tombola à 15 h"
        services.ecrire_parametre(conn, services.CLE_ANNONCE_EXPIRE, _passee())
        assert services.purger_annonce_expiree(conn) is True
        assert services.lire_parametre(conn, services.CLE_ANNONCE) is None
        assert services.purger_annonce_expiree(conn) is False  # idempotent
    finally:
        conn.close()


def test_le_demarrage_efface_l_annonce_d_avant_sans_echeance(chemins):
    """C'est le chemin de la mise à jour : `update.sh` redémarre le service."""
    from app import db

    _ecrire(**{services.CLE_ANNONCE: "Tombola à 15 h"})
    db.init_db()
    assert _parametre(services.CLE_ANNONCE) is None


def test_le_demarrage_garde_l_annonce_en_cours(chemins):
    from app import db

    conn = _conn()
    try:
        services.poser_annonce(conn, "Tombola à 15 h", 30)
    finally:
        conn.close()
    db.init_db()
    assert _parametre(services.CLE_ANNONCE) == "Tombola à 15 h"


def test_chaque_passage_de_sauvegarde_efface_un_texte_expire(chemins):
    """La borne dans le temps : 3 h et 15 h, sans geste humain."""
    from app import sauvegarde

    _ecrire(**{services.CLE_ANNONCE: "Tombola à 15 h",
               services.CLE_ANNONCE_EXPIRE: _passee()})
    sauvegarde.ecrire_archive(sauvegarde.NATURE_ROUTINE, chemins / "sauvegardes")
    assert _parametre(services.CLE_ANNONCE) is None


def test_la_sauvegarde_ne_touche_pas_une_annonce_en_cours(chemins):
    from app import sauvegarde

    conn = _conn()
    try:
        services.poser_annonce(conn, "Tombola à 15 h", 30)
    finally:
        conn.close()
    sauvegarde.ecrire_archive(sauvegarde.NATURE_ROUTINE, chemins / "sauvegardes")
    assert _parametre(services.CLE_ANNONCE) == "Tombola à 15 h"


def _base_de_pret_archivee(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return zf.read("pret-jeux.db")


def test_aucune_archive_ne_contient_l_annonce(chemins):
    """
    Une archive de routine est gardée un mois : l'annonce en cours n'y entre
    pas. Recherche OCTET PAR OCTET dans le fichier de base archivé, pas
    seulement dans la table — une ligne supprimée reste lisible dans les
    pages libres tant qu'elles ne sont pas réécrites.
    """
    from app import sauvegarde

    marque = "AnnonceDistinctiveEnCours"
    conn = _conn()
    try:
        services.poser_annonce(conn, marque, 30)
        services.ecrire_parametre(conn, "evenement_nom", "Fête du jeu")
    finally:
        conn.close()

    base = _base_de_pret_archivee(sauvegarde.creer_zip_sauvegarde())
    assert marque.encode() not in base
    # La base vivante, elle, n'est pas touchée par l'archivage.
    assert _parametre(services.CLE_ANNONCE) == marque
    # Et le reste des réglages voyage bien dans l'archive.
    assert "Fête du jeu".encode() in base


def test_aucune_archive_ne_contient_une_annonce_deja_effacee(chemins):
    """Les pages libres de la base vivante ne passent pas dans l'archive."""
    from app import sauvegarde

    marque = "AnnonceDistinctiveEffacee" * 4
    conn = _conn()
    try:
        # secure_delete désactivé ici exprès : on veut un résidu en pages
        # libres, pour prouver que l'archive ne le recopie pas.
        conn.execute("PRAGMA secure_delete = OFF")
        conn.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?)",
                     (services.CLE_ANNONCE, marque))
        conn.commit()
        conn.execute("DELETE FROM parametres WHERE cle = ?", (services.CLE_ANNONCE,))
        conn.commit()
    finally:
        conn.close()

    base = _base_de_pret_archivee(sauvegarde.creer_zip_sauvegarde())
    assert marque.encode() not in base
    # L'archive reste restaurable.
    chemin = chemins / "archive.zip"
    chemin.write_bytes(sauvegarde.creer_zip_sauvegarde())
    sauvegarde.valider_zip_sauvegarde(chemin)


# ---------------------------------------------------------------------------
# Clôture de fin d'événement et réinitialisation de la formation
# ---------------------------------------------------------------------------
def test_la_cloture_efface_l_annonce_et_laisse_les_panneaux(client):
    client.post("/admin/ecran-salle",
                data={"annonce": "Tombola à 15 h", "annonce_duree": "60",
                      "panneau_chiffres": "1", "panneau_mouvements": "1"})
    avant = client.get("/live/data").json()["panneaux"]

    r = client.post("/admin/cloturer-prets")
    assert "l&#39;annonce de l&#39;écran de salle est effacée" in r.text
    assert _parametre(services.CLE_ANNONCE) is None
    assert _parametre(services.CLE_ANNONCE_EXPIRE) is None
    d = client.get("/live/data").json()
    assert "annonce" not in d
    assert d["panneaux"] == avant == {"chiffres": True, "tournois": False,
                                      "programme": False, "mouvements": True}


def test_la_cloture_efface_dans_la_meme_transaction(chemins):
    conn = _conn()
    try:
        services.poser_annonce(conn, "Tombola à 15 h", 30)
        services.cloturer_tous_les_prets(conn)
        assert not conn.in_transaction
    finally:
        conn.close()
    assert _parametre(services.CLE_ANNONCE) is None


def test_la_reinitialisation_de_la_formation_efface_l_annonce(client, monkeypatch):
    from app.routes import admin as admin_routes

    monkeypatch.setattr(admin_routes, "MODE_FORMATION", True)
    monkeypatch.setenv("FORMATION_SOURCE_DB", "/formation-source-inexistante.db")
    # Un mot de passe admin changé depuis l'interface : il vit dans
    # `parametres`, comme l'annonce, et doit survivre à la remise à zéro.
    client.post("/admin/motdepasse",
                data={"ancien": MOT_DE_PASSE, "nouveau": "nouveau-secret-456",
                      "confirmation": "nouveau-secret-456"})
    empreinte = _parametre("admin_hash")
    assert empreinte
    _ecrire(evenement_nom="Fête du jeu")
    _poser(client, "Tombola à 15 h", "60")

    r = client.post("/admin/formation/reinitialiser")
    assert "réinitialisées" in r.text
    assert _parametre(services.CLE_ANNONCE) is None
    assert _parametre(services.CLE_ANNONCE_EXPIRE) is None
    assert _parametre("admin_hash") == empreinte
    assert _parametre("evenement_nom") == "Fête du jeu"
    assert "annonce" not in client.get("/live/data").json()
