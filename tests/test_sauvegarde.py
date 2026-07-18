"""
Tests de la sauvegarde/restauration complète des 3 bases (app/sauvegarde.py)
et des routes admin associées (/admin/sauvegarde/*).
"""

import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import pytest


# ===========================================================================
# Fixture commune : 3 bases temporaires, isolées par test.
# ===========================================================================
@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Initialise les 3 bases dans un dossier temporaire et renvoie leurs chemins."""
    chemin_pret = tmp_path / "pret-jeux.db"
    chemin_tournoi = tmp_path / "tournoi.db"
    chemin_planning = tmp_path / "planning.db"

    monkeypatch.setenv("DATABASE_PATH", str(chemin_pret))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(chemin_tournoi))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(chemin_planning))

    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: chemin_pret)
    monkeypatch.setattr(tdb, "get_database_path", lambda: chemin_tournoi)
    monkeypatch.setattr(pdb, "get_database_path", lambda: chemin_planning)

    db.init_db()
    tdb.init_db()
    pdb.init_db()

    return {"pret": chemin_pret, "tournoi": chemin_tournoi, "planning": chemin_planning}


def _inserer_titre(chemin: Path, reference: str, nom: str) -> None:
    conn = sqlite3.connect(chemin)
    try:
        conn.execute(
            "INSERT INTO titres (reference_titre, nom) VALUES (?, ?)", (reference, nom)
        )
        conn.commit()
    finally:
        conn.close()


def _lister_titres(chemin: Path) -> list[str]:
    conn = sqlite3.connect(chemin)
    try:
        return [r[0] for r in conn.execute("SELECT reference_titre FROM titres")]
    finally:
        conn.close()


# ===========================================================================
# Logique métier (app/sauvegarde.py)
# ===========================================================================
def test_creer_zip_contient_les_3_bases_et_info(bases, tmp_path):
    from app import sauvegarde

    _inserer_titre(bases["pret"], "CATAN", "Catan")

    contenu = sauvegarde.creer_zip_sauvegarde()
    with zipfile.ZipFile(BytesIO(contenu)) as zf:
        noms = set(zf.namelist())
        assert set(sauvegarde.NOMS_BASES) <= noms
        assert sauvegarde.NOM_INFO in noms
        info = zf.read(sauvegarde.NOM_INFO).decode()
        assert "Version de l'application" in info

        # Chaque base extraite est un SQLite valide et contient les bonnes tables.
        extrait = tmp_path / "extrait_pret.db"
        extrait.write_bytes(zf.read("pret-jeux.db"))
        assert _lister_titres(extrait) == ["CATAN"]


def test_valider_zip_rejette_archive_incomplete(bases, tmp_path):
    from app import sauvegarde

    # Zip valide mais auquel il manque une base (planning.db).
    chemin_zip = tmp_path / "incomplet.zip"
    with zipfile.ZipFile(chemin_zip, "w") as zf:
        zf.writestr("pret-jeux.db", Path(bases["pret"]).read_bytes())
        zf.writestr("tournoi.db", Path(bases["tournoi"]).read_bytes())

    with pytest.raises(sauvegarde.ZipInvalide, match="incomplète"):
        sauvegarde.valider_zip_sauvegarde(chemin_zip)


def test_valider_zip_rejette_base_corrompue(bases, tmp_path):
    from app import sauvegarde

    chemin_zip = tmp_path / "corrompu.zip"
    with zipfile.ZipFile(chemin_zip, "w") as zf:
        zf.writestr("pret-jeux.db", b"ceci n'est pas une base sqlite")
        zf.writestr("tournoi.db", Path(bases["tournoi"]).read_bytes())
        zf.writestr("planning.db", Path(bases["planning"]).read_bytes())

    with pytest.raises(sauvegarde.ZipInvalide, match="corrompue"):
        sauvegarde.valider_zip_sauvegarde(chemin_zip)


def test_valider_zip_rejette_fichier_non_zip(tmp_path):
    from app import sauvegarde

    chemin = tmp_path / "pasunzip.zip"
    chemin.write_bytes(b"n'importe quoi")

    with pytest.raises(sauvegarde.ZipInvalide, match="zip valide"):
        sauvegarde.valider_zip_sauvegarde(chemin)


def test_restaurer_remplace_les_donnees_et_garde_un_filet_de_securite(bases, tmp_path):
    from app import sauvegarde

    # État initial : un seul titre. On en fait une sauvegarde.
    _inserer_titre(bases["pret"], "CATAN", "Catan")
    zip_avant = sauvegarde.creer_zip_sauvegarde()
    chemin_zip = tmp_path / "sauvegarde.zip"
    chemin_zip.write_bytes(zip_avant)

    # On modifie ensuite la base courante (ajout d'un second titre).
    _inserer_titre(bases["pret"], "AZUL", "Azul")
    assert set(_lister_titres(bases["pret"])) == {"CATAN", "AZUL"}

    # Restauration : on doit revenir à l'état de la sauvegarde (CATAN seul).
    sauvegarde.restaurer_zip_sauvegarde(chemin_zip)
    assert _lister_titres(bases["pret"]) == ["CATAN"]

    # Filet de sécurité silencieux : un zip horodaté de l'état AVANT restauration
    # (donc avec CATAN + AZUL) a été conservé sous data/sauvegardes/.
    dossier_securite = bases["pret"].parent / "sauvegardes"
    fichiers = list(dossier_securite.glob("avant-restauration-*.zip"))
    assert len(fichiers) == 1
    with zipfile.ZipFile(fichiers[0]) as zf:
        extrait = tmp_path / "verif.db"
        extrait.write_bytes(zf.read("pret-jeux.db"))
    assert set(_lister_titres(extrait)) == {"CATAN", "AZUL"}


def _base_pret_schema_ancien(chemin: Path) -> None:
    """
    Écrit à `chemin` une base de PRÊT au schéma d'une version ANTÉRIEURE :
    `prets` sans la colonne `motif`, `exemplaires` sans les colonnes de
    rangement, et pas de table `emplacements_rangement`. Sert à simuler la
    restauration d'une sauvegarde ancienne.
    """
    conn = sqlite3.connect(chemin)
    try:
        conn.executescript(
            """
            CREATE TABLE titres (
                reference_titre TEXT PRIMARY KEY,
                nom TEXT NOT NULL
            );
            CREATE TABLE exemplaires (
                id_exemplaire TEXT PRIMARY KEY,
                reference_titre TEXT NOT NULL
            );
            CREATE TABLE prets (
                id_pret INTEGER PRIMARY KEY AUTOINCREMENT,
                id_exemplaire TEXT NOT NULL,
                numero_pochette INTEGER NOT NULL,
                date_sortie TEXT NOT NULL,
                date_retour TEXT
            );
            CREATE TABLE pochettes (
                numero_pochette INTEGER PRIMARY KEY,
                occupe INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE parametres (cle TEXT PRIMARY KEY, valeur TEXT);

            INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan');
            INSERT INTO exemplaires (id_exemplaire, reference_titre)
                VALUES ('00472', 'CATAN');
            INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie)
                VALUES ('00472', 7, '2026-07-18T09:00:00+00:00');
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_restaurer_rejoue_les_migrations_sur_une_sauvegarde_ancienne(bases, tmp_path):
    """
    Une sauvegarde au schéma d'époque doit être MIGRÉE à la restauration, qui se
    fait à chaud (sans redémarrage du serveur, seul moment où `init_db` tournait
    jusqu'ici). Sans ça, le code actuel écrirait dans une base amputée de ses
    colonnes récentes — panne au moment précis où l'on restaure.
    """
    from app import sauvegarde

    ancienne = tmp_path / "ancienne-pret.db"
    _base_pret_schema_ancien(ancienne)

    chemin_zip = tmp_path / "sauvegarde-ancienne.zip"
    with zipfile.ZipFile(chemin_zip, "w") as zf:
        zf.writestr("pret-jeux.db", ancienne.read_bytes())
        zf.writestr("tournoi.db", Path(bases["tournoi"]).read_bytes())
        zf.writestr("planning.db", Path(bases["planning"]).read_bytes())

    sauvegarde.restaurer_zip_sauvegarde(chemin_zip)

    conn = sqlite3.connect(bases["pret"])
    try:
        colonnes_prets = {r[1] for r in conn.execute("PRAGMA table_info(prets)")}
        colonnes_ex = {r[1] for r in conn.execute("PRAGMA table_info(exemplaires)")}
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # Les données restaurées sont intactes...
        assert _lister_titres(bases["pret"]) == ["CATAN"]
        (nb_prets,) = conn.execute("SELECT COUNT(*) FROM prets").fetchone()
        assert nb_prets == 1
    finally:
        conn.close()

    # ... et le schéma a été rattrapé (colonnes et table apparues après coup).
    assert "motif" in colonnes_prets
    assert {"emplacement_evenement", "emplacement_local_id"} <= colonnes_ex
    assert "emplacements_rangement" in tables

    # L'application fonctionne sans redémarrage : une requête métier qui touche
    # les colonnes récentes passe (elle échouerait sur la base non migrée).
    from app import db, services

    conn = db.get_connection()
    try:
        assert services.lister_prets_en_cours(conn)["pret"]
    finally:
        conn.close()


# ===========================================================================
# Routes (/admin/sauvegarde/*), via TestClient
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret-jeux.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")

    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret-jeux.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    db.init_db()
    tdb.init_db()
    pdb.init_db()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _login_admin(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})


def test_export_refuse_sans_authentification(client):
    r = client.get("/admin/sauvegarde/export", follow_redirects=False)
    assert r.status_code == 303   # redirigé vers la connexion, pas de fuite de données


def test_export_renvoie_un_zip_valide(client):
    _login_admin(client)
    r = client.get("/admin/sauvegarde/export")
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(r.content)) as zf:
        assert zf.testzip() is None   # aucune entrée corrompue
        noms = set(zf.namelist())
        assert {"pret-jeux.db", "tournoi.db", "planning.db", "INFO.txt"} <= noms


def test_import_restaure_les_donnees(client, tmp_path):
    _login_admin(client)

    from app import db

    # Un titre au départ, sauvegardé via la route d'export.
    conn = db.get_connection()
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.commit()
    conn.close()

    zip_avant = client.get("/admin/sauvegarde/export").content

    # On modifie la base courante après la sauvegarde.
    conn = db.get_connection()
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('AZUL', 'Azul')")
    conn.commit()
    conn.close()

    r = client.post(
        "/admin/sauvegarde/import",
        files={"fichier": ("ludotex-backup.zip", zip_avant, "application/zip")},
    )
    assert r.status_code == 200
    assert "réussie" in r.text.lower()

    conn = db.get_connection()
    titres = [row[0] for row in conn.execute("SELECT reference_titre FROM titres")]
    conn.close()
    assert titres == ["CATAN"]   # AZUL a disparu : la restauration a bien remplacé les données


def test_import_refuse_zip_invalide(client):
    _login_admin(client)

    from app import db

    conn = db.get_connection()
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.commit()
    conn.close()

    r = client.post(
        "/admin/sauvegarde/import",
        files={"fichier": ("invalide.zip", b"n'importe quoi", "application/zip")},
    )
    assert r.status_code == 400
    assert "zip valide" in r.text.lower() or "invalide" in r.text.lower()

    # Les données n'ont PAS été touchées par la tentative refusée.
    conn = db.get_connection()
    titres = [row[0] for row in conn.execute("SELECT reference_titre FROM titres")]
    conn.close()
    assert titres == ["CATAN"]
