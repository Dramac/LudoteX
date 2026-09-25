"""
Ce que l'application promet de ses données, vérifié là où la promesse se tient
(lot 9 de la série pré-production).

Trois familles :

- **Les téléchargements d'archive sont journalisés** : une archive contient les
  noms et contacts des bénévoles ; savoir qu'elle est sortie est la seule trace
  possible. La ligne dit quoi et quand — jamais le contenu, jamais un chemin.
- **Une suppression promise se vérifie sur les OCTETS** (enseignement du lot 8) :
  `backup()` recopie les pages libres d'une base, un `SELECT` vide ne prouve
  rien. La purge du planning et chaque archive sont donc passées au crible sur
  le fichier lui-même.
- **Les phrases de promesse citent la durée de la constante** : collecte,
  purge, aides. Elles ne doivent pas périmer en silence au prochain changement
  de rétention.
"""

import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

MOT_DE_PASSE = "secret-admin-promesse"

# Des valeurs qu'on ne risque pas de croiser par hasard dans un fichier SQLite.
NOM_BENEVOLE = "Zorglubienne"
CONTACT_BENEVOLE = "zorglub@exemple.invalid"
PSEUDO_TOURNOI = "Quetzalcoatlix"

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret-jeux.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret-jeux.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    db.init_db()
    tdb.init_db()
    pdb.init_db()
    return tmp_path


@pytest.fixture
def client(bases, monkeypatch):
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _connexion(client):
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _lignes(chemin, action):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [l for l in lignes if l.get("action") == action]


def _octets_de_la_base(chemin: Path) -> bytes:
    """Le fichier ET son journal WAL : une page ancienne peut y dormir."""
    contenu = chemin.read_bytes()
    wal = chemin.with_name(chemin.name + "-wal")
    if wal.exists():
        contenu += wal.read_bytes()
    return contenu


def _edition_avec_benevole(nombre: int = 1) -> int:
    from app.planning import db as pdb
    from app.planning import services as ps

    conn = pdb.get_connection()
    try:
        ev = ps.creer_evenement(conn, "Édition de test")
        for i in range(nombre):
            ps.enregistrer_souhaits(
                conn, ev, f"{NOM_BENEVOLE}{i:02d}", contact=f"{i}{CONTACT_BENEVOLE}",
            )
        return ev
    finally:
        conn.close()


# ===========================================================================
# Téléchargements journalisés
# ===========================================================================
def test_telecharger_la_sauvegarde_complete_est_journalise(client, _journal_isole, bases):
    from app import sauvegarde

    _connexion(client)
    reponse = client.get("/admin/sauvegarde/export")
    assert reponse.status_code == 200

    lignes = _lignes(_journal_isole, "sauvegarde_telechargee")
    assert len(lignes) == 1
    ligne = lignes[0]
    assert ligne["module"] == "admin"
    assert ligne["qui"] == "admin"
    assert ligne["ok"] is True
    assert ligne["objet"] == sauvegarde.LIBELLE_EXPORT
    # Ni chemin, ni contenu : rien du dossier des bases dans la ligne.
    brut = json.dumps(ligne, ensure_ascii=False)
    assert str(bases) not in brut
    assert "pret-jeux.db" not in brut and "planning.db" not in brut


def test_telecharger_une_archive_du_serveur_est_journalise(client, _journal_isole):
    from app import sauvegarde

    dossier = sauvegarde.dossier_sauvegardes()
    dossier.mkdir(parents=True, exist_ok=True)
    nom = "ludotex-backup-20260925-030000.zip"
    (dossier / nom).write_bytes(b"PK-contenu-distinctif")

    _connexion(client)
    reponse = client.get(f"/admin/sauvegarde/archives/{nom}")
    assert reponse.status_code == 200

    ligne = _lignes(_journal_isole, "sauvegarde_telechargee")[-1]
    assert ligne["objet"] == sauvegarde.LIBELLES_NATURE[sauvegarde.NATURE_ROUTINE]
    assert ligne["ref"] == nom
    brut = json.dumps(ligne, ensure_ascii=False)
    assert str(dossier) not in brut
    assert "contenu-distinctif" not in brut


def test_un_telechargement_refuse_n_ecrit_rien(client, _journal_isole):
    _connexion(client)
    reponse = client.get("/admin/sauvegarde/archives/ludotex-backup-20990101-000000.zip")
    assert reponse.status_code == 404
    assert _lignes(_journal_isole, "sauvegarde_telechargee") == []


def test_un_visiteur_ne_telecharge_rien_et_rien_n_est_journalise(client, _journal_isole):
    reponse = client.get("/admin/sauvegarde/export", follow_redirects=False)
    assert reponse.status_code in (302, 303, 401, 403)
    assert _lignes(_journal_isole, "sauvegarde_telechargee") == []


# ===========================================================================
# Une suppression promise se vérifie sur les octets
# ===========================================================================
def test_la_purge_du_planning_efface_les_octets_du_fichier(bases):
    """
    Sans `secure_delete`, les noms « purgés » restent lisibles dans les pages
    libres de planning.db — constaté sur trente bénévoles fictifs avant ce lot.
    """
    from app.planning import db as pdb
    from app.planning import services as ps

    ev = _edition_avec_benevole(30)
    conn = pdb.get_connection()
    try:
        ps.purger_evenement(conn, ev)
    finally:
        conn.close()

    octets = _octets_de_la_base(bases / "planning.db")
    assert NOM_BENEVOLE.encode() not in octets
    assert CONTACT_BENEVOLE.encode() not in octets


def test_une_archive_ne_contient_rien_de_ce_qui_a_ete_supprime_avant_elle(bases):
    """
    Suppressions faites SANS `secure_delete` — le cas d'une base d'avant ce lot,
    ou d'une désinscription de tournoi : le résidu est dans les pages libres.
    L'archive, compactée, ne doit pas le recopier. Non-vacuité : le résidu
    existe bien dans le fichier vivant, sinon le test ne prouverait rien.
    """
    from app import sauvegarde
    from app.planning import db as pdb
    from app.tournoi import db as tdb
    from app.tournoi import services as ts

    ev = _edition_avec_benevole(30)
    conn = pdb.get_connection()
    try:
        conn.execute("PRAGMA secure_delete = OFF")
        conn.execute("DELETE FROM evenements WHERE id_evenement = ?", (ev,))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    conn = tdb.get_connection()
    try:
        id_tournoi = ts.creer_tournoi(conn, "Tournoi de test")
        conn.execute("UPDATE tournois SET etat = 'inscriptions' WHERE id_tournoi = ?",
                     (id_tournoi,))
        conn.commit()
        for i in range(30):
            ts.inscrire(conn, id_tournoi, f"{PSEUDO_TOURNOI}{i:02d}")
        conn.execute("PRAGMA secure_delete = OFF")
        conn.execute("DELETE FROM inscriptions")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    assert NOM_BENEVOLE.encode() in _octets_de_la_base(bases / "planning.db")
    assert PSEUDO_TOURNOI.encode() in _octets_de_la_base(bases / "tournoi.db")

    archive = zipfile.ZipFile(io.BytesIO(sauvegarde.creer_zip_sauvegarde()))
    assert NOM_BENEVOLE.encode() not in archive.read("planning.db")
    assert CONTACT_BENEVOLE.encode() not in archive.read("planning.db")
    assert PSEUDO_TOURNOI.encode() not in archive.read("tournoi.db")


def test_une_archive_garde_ce_qui_n_a_pas_ete_supprime(bases, tmp_path):
    """Le compactage ne retire que les pages libres : l'archive reste restaurable."""
    from app import sauvegarde

    _edition_avec_benevole(1)
    contenu = sauvegarde.creer_zip_sauvegarde()
    assert NOM_BENEVOLE.encode() in zipfile.ZipFile(io.BytesIO(contenu)).read("planning.db")
    chemin = tmp_path / "archive.zip"
    chemin.write_bytes(contenu)
    sauvegarde.valider_zip_sauvegarde(chemin)


# ===========================================================================
# Les phrases de promesse citent la durée de la constante
# ===========================================================================
@pytest.fixture
def duree_factice(monkeypatch):
    """Une durée improbable : si elle s'affiche, la phrase lit bien la variable."""
    from app.templating import templates

    monkeypatch.setitem(templates.env.globals, "duree_archives_jours", 47)
    # `&nbsp;` est écrit en dur dans les gabarits : il arrive tel quel.
    return "47&nbsp;jours"


def test_la_collecte_dit_qui_efface_et_combien_de_temps(client, bases, duree_factice):
    ev = _edition_avec_benevole(0)
    page = client.get(f"/planning/collecte/{ev}").text
    assert "supprimées ensuite" not in page
    assert "Le bureau les efface une fois l'événement passé" in page
    assert duree_factice in page


def test_l_ecran_de_purge_dit_que_les_archives_restent(client, bases, duree_factice):
    ev = _edition_avec_benevole(0)
    _connexion(client)
    page = client.get(f"/planning/admin/{ev}").text
    assert "ne touche pas les archives" in page
    assert duree_factice in page


def test_les_aides_citent_la_meme_duree(client, duree_factice):
    assert duree_factice in client.get("/planning/aide").text
    _connexion(client)
    assert duree_factice in client.get("/admin/aide").text


def test_la_duree_affichee_par_defaut_est_celle_de_la_sauvegarde(client, bases):
    from app import sauvegarde

    ev = _edition_avec_benevole(0)
    page = client.get(f"/planning/collecte/{ev}").text
    assert f"{sauvegarde.DUREE_VIE_ARCHIVES_JOURS}&nbsp;jours" in page


def test_le_telechargement_direct_porte_la_meme_mention_que_les_archives(client):
    """Une seule phrase pour les deux sections : elle ne peut pas diverger."""
    _connexion(client)
    page = client.get("/admin/donnees").text
    phrase = "les noms et contacts des bénévoles y figurent"
    assert page.count(phrase) == 2
    # Rendue comme du HTML, pas échappée par le `set` du gabarit.
    assert "&lt;strong&gt;" not in page


# ===========================================================================
# Plus aucun document ne revendique « la seule » porte
# ===========================================================================
_REVENDICATION = re.compile(
    r"\bseul(?:e)?\s+(?:endroit|champ|porte)\b.{0,120}?donn[ée]e\s+personnelle",
    re.IGNORECASE | re.DOTALL,
)


def test_aucun_fichier_suivi_ne_dit_plus_le_seul_endroit_d_une_donnee_personnelle():
    """
    Trois documents revendiquaient chacun « le seul endroit » par lequel une
    donnée personnelle entre — et aucun ne citait l'annonce de l'écran de salle.
    La liste des portes a désormais un domicile unique (wiki/Rgpd.md, résumé
    dans CLAUDE.md) ; une nouvelle revendication la contredirait.
    """
    suivis = subprocess.run(
        ["git", "ls-files", "app", "docs", "tests", "README.md", "CONTRIBUTING.md", "CLAUDE.md"],
        cwd=RACINE, capture_output=True, text=True, check=True,
    ).stdout.split()
    fautifs = []
    for rel in suivis:
        if rel == "tests/test_promesse_donnees.py" or not rel.endswith((".py", ".md", ".html")):
            continue
        # Documents d'archive, hors du tri du lot 5 : ils racontent l'état d'alors.
        if rel.startswith(("docs/audit-", "docs/prompt-", "docs/idees-")):
            continue
        texte = (RACINE / rel).read_text(encoding="utf-8")
        if _REVENDICATION.search(re.sub(r"[\s#*>]+", " ", texte)):
            fautifs.append(rel)
    assert fautifs == []
