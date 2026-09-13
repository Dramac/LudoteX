"""
Archives du dossier des sauvegardes (app/sauvegarde.py, « LES ARCHIVES DU
SERVEUR ») : nature lue dans le nom, rotation et purge de CHAQUE nature, et
téléchargement depuis l'administration sans traversée de chemin (EXP-06).
"""

import os
import time
import zipfile
from datetime import datetime
from io import BytesIO

import pytest


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


def _archive(dossier, nom, jours=0.0, contenu=b"zip"):
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    chemin.write_bytes(contenu)
    instant = time.time() - jours * 86400
    os.utime(chemin, (instant, instant))
    return chemin


# ===========================================================================
# Nature lue dans le nom
# ===========================================================================
def test_le_prefixe_de_la_routine_ne_change_pas():
    """Les archives déjà présentes sur les serveurs et la documentation
    d'exploitation (« backup-…-03 ») reposent sur ce préfixe."""
    from app import sauvegarde

    assert sauvegarde.PREFIXES_ARCHIVE[sauvegarde.NATURE_ROUTINE] == "ludotex-backup"


@pytest.mark.parametrize("nom, nature", [
    ("ludotex-backup-20260913-030001.zip", "routine"),
    ("ludotex-backup-2026-09-13.zip", "routine"),
    ("avant-mise-a-jour-20260913-185012.zip", "avant-mise-a-jour"),
    ("avant-restauration-20260913-101010.zip", "avant-restauration"),
    ("ludotex-backup-20260913-030001.zip.bak", None),
    ("ludotex-backup-.zip", None),
    ("ludotex-backup-../x.zip", None),
    ("notes.txt", None),
    ("autre-20260913-030001.zip", None),
])
def test_nature_archive(nom, nature):
    from app import sauvegarde

    assert sauvegarde.nature_archive(nom) == nature


def test_nom_archive_de_chaque_nature_est_reconnu():
    from app import sauvegarde

    instant = datetime(2026, 9, 13, 3, 0, 1)
    for nature in sauvegarde.PREFIXES_ARCHIVE:
        nom = sauvegarde.nom_archive(nature, instant)
        assert nom.endswith("-20260913-030001.zip")
        assert sauvegarde.nature_archive(nom) == nature
        assert nature in sauvegarde.LIBELLES_NATURE


def test_lister_archives_ecarte_ce_qui_n_est_pas_une_archive(tmp_path):
    from app import sauvegarde

    dossier = tmp_path / "sauvegardes"
    _archive(dossier, "ludotex-backup-20260912-030001.zip", jours=1)
    _archive(dossier, "avant-mise-a-jour-20260913-185012.zip")
    _archive(dossier, "notes.txt")
    (dossier / "ludotex-backup-20260101-000000.zip").mkdir()
    secret = tmp_path / "pret-jeux.db"
    secret.write_bytes(b"base")
    os.symlink(secret, dossier / "ludotex-backup-20260913-120000.zip")

    noms = [a["nom"] for a in sauvegarde.lister_archives(dossier)]
    assert noms == ["avant-mise-a-jour-20260913-185012.zip", "ludotex-backup-20260912-030001.zip"]


def test_lister_archives_dossier_absent(tmp_path):
    from app import sauvegarde

    assert sauvegarde.lister_archives(tmp_path / "absent") == []


# ===========================================================================
# Fin de vie : chaque nature a sa règle
# ===========================================================================
def test_purge_garde_les_routines_les_plus_recentes(tmp_path):
    from app import sauvegarde

    dossier = tmp_path / "sauvegardes"
    garder = sauvegarde.GARDER_ROUTINES
    for i in range(garder + 3):
        _archive(dossier, f"ludotex-backup-202608{i:02d}-030001.zip", jours=i)

    supprimees = sauvegarde.purger_archives(dossier)

    restantes = sorted(a["nom"] for a in sauvegarde.lister_archives(dossier))
    assert len(restantes) == garder
    assert sorted(supprimees) == [f"ludotex-backup-202608{i:02d}-030001.zip" for i in range(garder, garder + 3)]


def test_purge_des_deux_filets_au_dela_du_delai(tmp_path):
    from app import sauvegarde

    dossier = tmp_path / "sauvegardes"
    limite = sauvegarde.GARDER_JOURS_FILETS
    _archive(dossier, "avant-mise-a-jour-20260701-120000.zip", jours=limite + 1)
    _archive(dossier, "avant-mise-a-jour-20260901-120000.zip", jours=limite - 1)
    _archive(dossier, "avant-restauration-20260701-120000.zip", jours=limite + 1)
    _archive(dossier, "avant-restauration-20260901-120000.zip", jours=limite - 1)
    # Une routine très ancienne mais seule : la rotation la garde (par nombre).
    _archive(dossier, "ludotex-backup-20260101-030001.zip", jours=200)
    _archive(dossier, "notes.txt", jours=400)

    supprimees = sauvegarde.purger_archives(dossier)

    assert sorted(supprimees) == [
        "avant-mise-a-jour-20260701-120000.zip",
        "avant-restauration-20260701-120000.zip",
    ]
    assert (dossier / "notes.txt").exists()
    assert (dossier / "ludotex-backup-20260101-030001.zip").exists()


def test_aucune_nature_n_echappe_a_la_purge(tmp_path):
    """Garde-fou : une nature ajoutée sans règle de fin de vie ferait
    s'accumuler pour toujours des archives qui contiennent le planning."""
    from app import sauvegarde

    dossier = tmp_path / "sauvegardes"
    for nature in sauvegarde.PREFIXES_ARCHIVE:
        for i in range(sauvegarde.GARDER_ROUTINES + 1):
            nom = sauvegarde.nom_archive(nature, datetime(2025, 1, 1, 0, 0, i))
            _archive(dossier, nom, jours=400 + i)

    sauvegarde.purger_archives(dossier)

    for nature in sauvegarde.PREFIXES_ARCHIVE:
        restantes = [a for a in sauvegarde.lister_archives(dossier) if a["nature"] == nature]
        assert len(restantes) <= sauvegarde.GARDER_ROUTINES, nature


def test_les_anciens_filets_de_mise_a_jour_tournent_avec_la_routine(tmp_path):
    """Avant ce changement, update.sh nommait ses filets comme la routine. Ils
    restent dans la rotation par nombre et en sortent quand de nouvelles
    routines arrivent : aucun n'est orphelin."""
    from app import sauvegarde

    dossier = tmp_path / "sauvegardes"
    _archive(dossier, "ludotex-backup-20260912-201919.zip", jours=40)  # ancien filet
    for i in range(sauvegarde.GARDER_ROUTINES):
        _archive(dossier, f"ludotex-backup-202609{i:02d}-030001.zip", jours=i)

    assert sauvegarde.purger_archives(dossier) == ["ludotex-backup-20260912-201919.zip"]


def test_ecrire_archive_refuse_une_nature_inconnue_sans_rien_ecrire(tmp_path):
    from app import sauvegarde

    dossier = tmp_path / "sauvegardes"
    with pytest.raises(ValueError, match="nature de sauvegarde inconnue"):
        sauvegarde.ecrire_archive("hebdomadaire", dossier)
    assert not dossier.exists()


def test_filet_de_restauration_porte_son_nom(client, tmp_path):
    from app import sauvegarde

    chemin = sauvegarde.sauvegarde_de_securite()
    assert chemin.parent == sauvegarde.dossier_sauvegardes()
    assert sauvegarde.nature_archive(chemin.name) == sauvegarde.NATURE_AVANT_RESTAURATION


# ===========================================================================
# Téléchargement depuis l'administration (EXP-06)
# ===========================================================================
def test_page_donnees_liste_les_archives(client, tmp_path):
    dossier = tmp_path / "sauvegardes"
    _archive(dossier, "ludotex-backup-20260913-030001.zip")
    _archive(dossier, "avant-mise-a-jour-20260913-185012.zip")
    _archive(dossier, "avant-restauration-20260913-101010.zip")
    _archive(dossier, "notes.txt")
    _login_admin(client)

    r = client.get("/admin/donnees")
    assert r.status_code == 200
    assert "Archives du serveur" in r.text
    for libelle in ("Sauvegarde de routine", "Filet avant mise à jour", "Filet avant restauration"):
        assert libelle in r.text
    assert "/admin/sauvegarde/archives/ludotex-backup-20260913-030001.zip" in r.text
    assert "notes.txt" not in r.text
    assert "planning bénévole" in r.text


def test_page_donnees_sans_archive(client):
    _login_admin(client)
    r = client.get("/admin/donnees")
    assert "Aucune archive sur le serveur pour l'instant." in r.text


def test_telecharger_une_archive(client, tmp_path):
    dossier = tmp_path / "sauvegardes"
    tampon = BytesIO()
    with zipfile.ZipFile(tampon, "w") as zf:
        zf.writestr("INFO.txt", "Sauvegarde LudoteX")
    _archive(dossier, "avant-restauration-20260913-101010.zip", contenu=tampon.getvalue())
    _login_admin(client)

    r = client.get("/admin/sauvegarde/archives/avant-restauration-20260913-101010.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert "avant-restauration-20260913-101010.zip" in r.headers["content-disposition"]
    assert r.content == tampon.getvalue()


def test_telecharger_exige_l_administration(client, tmp_path):
    _archive(tmp_path / "sauvegardes", "ludotex-backup-20260913-030001.zip")
    r = client.get("/admin/sauvegarde/archives/ludotex-backup-20260913-030001.zip",
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"


@pytest.mark.parametrize("chemin", [
    "/admin/sauvegarde/archives/..%2Fpret-jeux.db",
    "/admin/sauvegarde/archives/%2E%2E%2Fpret-jeux.db",
    "/admin/sauvegarde/archives/..%252Fpret-jeux.db",
    "/admin/sauvegarde/archives/%2Fetc%2Fpasswd",
    "/admin/sauvegarde/archives/pret-jeux.db",
    "/admin/sauvegarde/archives/notes.txt",
    "/admin/sauvegarde/archives/ludotex-backup-20260913-030001.zip.bak",
    "/admin/sauvegarde/archives/ludotex-backup-20260913-120000.zip",  # lien symbolique
    "/admin/sauvegarde/archives/ludotex-backup-20990101-000000.zip",  # absente
])
def test_telecharger_refuse_tout_ce_qui_n_est_pas_une_archive_listee(client, tmp_path, chemin):
    dossier = tmp_path / "sauvegardes"
    _archive(dossier, "ludotex-backup-20260913-030001.zip")
    _archive(dossier, "notes.txt", contenu=b"secret-notes")
    _archive(dossier, "ludotex-backup-20260913-030001.zip.bak", contenu=b"secret-bak")
    os.symlink(tmp_path / "pret-jeux.db", dossier / "ludotex-backup-20260913-120000.zip")
    _login_admin(client)

    r = client.get(chemin)

    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert b"SQLite format 3" not in r.content
    assert b"secret-" not in r.content
    assert b"root:" not in r.content


def test_refus_reaffiche_la_liste_avec_un_message(client, tmp_path):
    _archive(tmp_path / "sauvegardes", "ludotex-backup-20260913-030001.zip")
    _login_admin(client)

    r = client.get("/admin/sauvegarde/archives/avant-restauration-20200101-000000.zip")
    assert r.status_code == 404
    assert "pas (ou plus) sur le serveur" in r.text
    assert "/admin/sauvegarde/archives/ludotex-backup-20260913-030001.zip" in r.text


@pytest.mark.parametrize("nom", [
    "../pret-jeux.db", "/etc/passwd", "..%2Fpret-jeux.db", "ludotex-backup-..zip",
    "ludotex-backup-20260913-030001.zip/../../pret-jeux.db", "", None,
])
def test_archive_telechargeable_refuse_les_chemins(client, tmp_path, nom):
    from app import sauvegarde

    _archive(tmp_path / "sauvegardes", "ludotex-backup-20260913-030001.zip")
    assert sauvegarde.archive_telechargeable(nom) is None


def test_restauration_reussie_indique_ou_retrouver_le_filet(client):
    from app import sauvegarde

    _login_admin(client)
    archive = sauvegarde.creer_zip_sauvegarde()
    r = client.post(
        "/admin/sauvegarde/import",
        files={"fichier": ("ludotex-backup.zip", archive, "application/zip")},
    )
    assert r.status_code == 200
    assert "Archives du serveur" in r.text
    assert "data/sauvegardes" not in r.text
    assert "Filet avant restauration" in r.text
