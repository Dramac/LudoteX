"""
ENVOIS DE FICHIERS DE L'ADMINISTRATION : garde avant lecture, bornes
(lot-11-pré-production — SEC-14, ROB-03(a), ROB-06).

Trois routes reçoivent un fichier : l'import du catalogue
(`/admin/donnees/import`), la restauration (`/admin/sauvegarde/import`) et
l'identité avec logo (`/admin/identite`). Ce fichier vérifie :

1. **La garde passe AVANT la lecture du corps** (SEC-14). Sans session, pas un
   octet de l'envoi n'est tiré du serveur : un espion ASGI placé devant
   l'application compte ce qu'elle lit. La réponse visible ne change pas :
   `303` vers `/admin`.
2. **Une borne en octets par usage, une seule fonction** (ROB-03(a)) : juste
   en dessous accepté, juste au-dessus refusé avec la page de l'écran et un
   message clair, refus journalisé.
3. **La borne de l'archive reste sous `client_max_body_size`** de nginx : lu
   dans les deux fichiers de `deploy/`.
4. **Une archive « bombe »** (petite en octets, énorme décompressée) est
   refusée AVANT extraction.
5. **Un catalogue trop long** (ROB-06) est refusé sans rien écrire en base.
"""

import io
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest


MOT_DE_PASSE = "secret-admin-envois"
RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Trois bases temporaires, patron de tests/test_logo.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
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
    return tmp_path


class _Espion:
    """
    Enveloppe ASGI qui compte les octets du corps que l'application TIRE du
    serveur (messages `http.request`). C'est l'observable de SEC-14 : si la
    garde passe avant la lecture, le compte reste à zéro — rien n'a été
    analysé, rien n'a été déversé dans un fichier temporaire.
    """

    def __init__(self, app):
        self.app = app
        self.octets_lus = 0

    async def __call__(self, scope, receive, send):
        async def receive_compte():
            message = await receive()
            if message["type"] == "http.request":
                self.octets_lus += len(message.get("body", b""))
            return message

        await self.app(scope, receive_compte, send)


@pytest.fixture
def espion(bases):
    from app.main import app

    return _Espion(app)


@pytest.fixture
def client(espion):
    from fastapi.testclient import TestClient

    return TestClient(espion)


def _connexion(client):
    return client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


# Au-delà de 1 Mo, Starlette déverse la partie reçue dans un fichier
# temporaire : c'est le cas mesuré par l'audit, on le reproduit.
GROS = b"x" * (2 * 1024 * 1024)

ENVOIS = [
    ("/admin/donnees/import", {}, "fichier", "catalogue.csv", "text/csv"),
    ("/admin/sauvegarde/import", {}, "fichier", "sauvegarde.zip", "application/zip"),
    ("/admin/identite", {"nom_association": ""}, "logo_fichier", "logo.png", "image/png"),
]


# ---------------------------------------------------------------------------
# 1. SEC-14 — la garde avant la lecture du corps
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("chemin,champs,champ_fichier,nom,type_mime", ENVOIS)
def test_sans_session_le_corps_n_est_jamais_lu(
    client, espion, chemin, champs, champ_fichier, nom, type_mime
):
    """Sans session : 303 vers /admin, et pas un octet du corps n'a été tiré."""
    reponse = client.post(
        chemin, data=champs, files={champ_fichier: (nom, GROS, type_mime)},
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/admin"
    assert espion.octets_lus == 0


@pytest.mark.parametrize("chemin,champs,champ_fichier,nom,type_mime", ENVOIS)
def test_avec_session_le_corps_est_lu(
    client, espion, chemin, champs, champ_fichier, nom, type_mime
):
    """Témoin : connecté, l'espion voit bien passer le corps (il ne ment pas)."""
    _connexion(client)
    espion.octets_lus = 0
    client.post(chemin, data=champs,
                files={champ_fichier: (nom, b"contenu", type_mime)})
    assert espion.octets_lus > 0


@pytest.mark.parametrize("chemin", ["/admin/donnees/import", "/admin/sauvegarde/import"])
def test_sans_session_import_et_restauration_redirigent(client, chemin):
    """Même comportement visible qu'`/admin/identite` (tests existants)."""
    reponse = client.post(
        chemin, files={"fichier": ("f", b"a;b\n", "text/plain")},
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/admin"


def test_seules_les_trois_routes_d_envoi_ont_la_garde_avant_lecture():
    """Les autres routes gardées ne bougent pas : `_garde` en tête, rien de plus."""
    from app.routes import admin as routes_admin

    envois = {
        (route.path, tuple(sorted(route.methods)))
        for route in routes_admin.router.routes
        if isinstance(route, routes_admin.RouteEnvoiAdmin)
    }
    assert envois == {
        ("/admin/donnees/import", ("POST",)),
        ("/admin/sauvegarde/import", ("POST",)),
        ("/admin/identite", ("POST",)),
    }


# ---------------------------------------------------------------------------
# 2. ROB-03(a) — une fonction de lecture bornée
# ---------------------------------------------------------------------------
def test_lire_borne_accepte_la_borne_exacte_et_refuse_un_octet_de_plus():
    from app import envois

    assert envois.lire_borne(io.BytesIO(b"a" * 10), 10) == b"a" * 10
    with pytest.raises(envois.EnvoiTropLourd) as refus:
        envois.lire_borne(io.BytesIO(b"a" * 11), 10)
    assert refus.value.taille_max == 10


def test_lire_borne_ne_lit_qu_un_octet_de_plus_que_la_borne():
    from app import envois

    flux = io.BytesIO(b"a" * 1000)
    with pytest.raises(envois.EnvoiTropLourd):
        envois.lire_borne(flux, 10)
    assert flux.tell() == 11


def test_message_de_refus_dit_la_borne_et_quoi_faire():
    from app import envois

    message = envois.EnvoiTropLourd(15 * 1024 * 1024).message
    assert "trop volumineux" in message
    assert "15 Mo au maximum" in message
    assert "bon fichier" in message


def _catalogue(n_lignes: int, bourrage: int = 0) -> bytes:
    lignes = ["Code jeu;Nom jeu;Descriptif"]
    lignes += [f"{900 + i:04d};Jeu {i};{'d' * bourrage}" for i in range(n_lignes)]
    return ("\n".join(lignes) + "\n").encode()


def _exemplaires(bases) -> int:
    conn = sqlite3.connect(bases / "test.db")
    try:
        return conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
    finally:
        conn.close()


def _lignes_journal(chemin_journal):
    import json

    return [json.loads(l) for l in chemin_journal.read_text().splitlines() if l.strip()]


def test_catalogue_a_la_borne_accepte_au_dessus_refuse(client, bases, monkeypatch,
                                                      _journal_isole):
    from scripts import import_csv

    contenu = _catalogue(2)
    monkeypatch.setattr(import_csv, "TAILLE_MAX_CATALOGUE", len(contenu))
    _connexion(client)

    r = client.post("/admin/donnees/import",
                    files={"fichier": ("c.csv", contenu, "text/csv")})
    assert "Import réussi : 2 exemplaire(s)" in r.text
    assert _exemplaires(bases) == 3

    r = client.post("/admin/donnees/import",
                    files={"fichier": ("c.csv", contenu + b"x", "text/csv")})
    assert r.status_code == 200
    assert "Fichier trop volumineux" in r.text
    assert _exemplaires(bases) == 3
    derniere = _lignes_journal(_journal_isole)[-1]
    assert derniere["action"] == "import_csv"
    assert derniere["ok"] is False and derniere["detail"] == "fichier_trop_lourd"


def test_archive_a_la_borne_acceptee_au_dessus_refusee(client, bases, monkeypatch,
                                                      _journal_isole):
    from app import sauvegarde

    archive = sauvegarde.creer_zip_sauvegarde()
    _connexion(client)

    monkeypatch.setattr(sauvegarde, "TAILLE_MAX_ARCHIVE", len(archive) - 1)
    r = client.post("/admin/sauvegarde/import",
                    files={"fichier": ("s.zip", archive, "application/zip")})
    assert r.status_code == 400
    assert "Fichier trop volumineux" in r.text
    assert not (bases / "sauvegardes").exists()        # aucun filet : rien n'a commencé
    derniere = _lignes_journal(_journal_isole)[-1]
    assert derniere["action"] == "sauvegarde_restauree"
    assert derniere["ok"] is False and derniere["detail"] == "fichier_trop_lourd"

    monkeypatch.setattr(sauvegarde, "TAILLE_MAX_ARCHIVE", len(archive))
    r = client.post("/admin/sauvegarde/import",
                    files={"fichier": ("s.zip", archive, "application/zip")})
    assert r.status_code == 200
    assert "Restauration réussie" in r.text


def _nginx_max_octets(nom: str) -> int:
    texte = (RACINE / "deploy" / nom).read_text()
    valeurs = re.findall(r"^\s*client_max_body_size\s+(\d+)m;", texte, re.M)
    assert len(valeurs) == 1, nom
    return int(valeurs[0]) * 1024 * 1024


@pytest.mark.parametrize("fichier_nginx",
                         ["nginx-ludotex.conf", "nginx-ludotex-formation.conf"])
def test_les_bornes_restent_sous_la_limite_de_nginx(fichier_nginx):
    """
    Chaque borne applicative, plus une marge pour l'enveloppe multipart et les
    champs texte du formulaire, reste sous `client_max_body_size` : un fichier
    trop gros reçoit la page de l'application, pas l'erreur brute de nginx.
    """
    from app import logo, sauvegarde
    from scripts import import_csv

    marge = 1024 * 1024
    limite = _nginx_max_octets(fichier_nginx)
    for borne in (sauvegarde.TAILLE_MAX_ARCHIVE, import_csv.TAILLE_MAX_CATALOGUE,
                  logo.TAILLE_MAX_OCTETS):
        assert borne + marge <= limite


# ---------------------------------------------------------------------------
# 3. Archive « bombe » : taille décompressée contrôlée avant extraction
# ---------------------------------------------------------------------------
def _archive_avec(tmp_path, contenus: dict[str, bytes | int]) -> Path:
    """
    Zip aux trois noms attendus ; un entier donne autant d'octets nuls,
    écrits par blocs (une vraie bombe ne tient jamais en mémoire ici).
    """
    from app import sauvegarde

    chemin = tmp_path / "archive.zip"
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as zf:
        for nom in sauvegarde.NOMS_BASES:
            valeur = contenus.get(nom, b"")
            if isinstance(valeur, int):
                with zf.open(nom, "w", force_zip64=True) as membre:
                    bloc = bytes(1024 * 1024)
                    for _ in range(valeur // len(bloc)):
                        membre.write(bloc)
            else:
                zf.writestr(nom, valeur)
    return chemin


def test_archive_bombe_refusee_avant_extraction(bases, tmp_path, monkeypatch):
    """À la vraie borne : 1 Mo de plus que permis, compressé en quelques centaines de Ko."""
    from app import sauvegarde

    trop = sauvegarde.TAILLE_MAX_BASES_DECOMPRESSEES + 1024 * 1024
    chemin = _archive_avec(tmp_path, {"pret-jeux.db": trop})
    assert chemin.stat().st_size < sauvegarde.TAILLE_MAX_ARCHIVE

    def _interdit(*_args, **_kwargs):
        raise AssertionError("extraction tentée malgré la taille décompressée")

    monkeypatch.setattr(sauvegarde, "_extraire", _interdit)
    with pytest.raises(sauvegarde.ZipInvalide, match="décompressées"):
        sauvegarde.valider_zip_sauvegarde(chemin)


def test_archive_juste_sous_la_borne_decompressee_passe_ce_controle(bases, tmp_path,
                                                                   monkeypatch):
    """À la borne exacte, le contrôle laisse passer (l'intégrité SQLite juge ensuite)."""
    from app import sauvegarde

    archive = sauvegarde.creer_zip_sauvegarde()
    (tmp_path / "ok.zip").write_bytes(archive)
    with zipfile.ZipFile(tmp_path / "ok.zip") as zf:
        total = sum(zf.getinfo(n).file_size for n in sauvegarde.NOMS_BASES)
    monkeypatch.setattr(sauvegarde, "TAILLE_MAX_BASES_DECOMPRESSEES", total)
    sauvegarde.valider_zip_sauvegarde(tmp_path / "ok.zip")
    monkeypatch.setattr(sauvegarde, "TAILLE_MAX_BASES_DECOMPRESSEES", total - 1)
    with pytest.raises(sauvegarde.ZipInvalide, match="décompressées"):
        sauvegarde.valider_zip_sauvegarde(tmp_path / "ok.zip")


def test_archive_qui_ment_sur_sa_taille_est_refusee(bases, tmp_path):
    """
    La taille décompressée est DÉCLARÉE par l'archive. Si elle ment à la baisse,
    `zipfile` s'arrête à la taille annoncée et le contrôle CRC échoue : refus
    en « archive corrompue », jamais une extraction de la taille réelle.
    """
    from app import sauvegarde

    chemin = _archive_avec(tmp_path, {"pret-jeux.db": bytes(1024 * 1024)})
    brut = bytearray(chemin.read_bytes())
    # Taille non compressée (4 octets, petit-boutiste) : en-tête local à +22,
    # annuaire central à +24. Seul le premier membre (pret-jeux.db) est visé.
    local = brut.find(b"PK\x03\x04")
    central = brut.find(b"PK\x01\x02")
    for position in (local + 22, central + 24):
        brut[position:position + 4] = (100).to_bytes(4, "little")
    chemin.write_bytes(bytes(brut))
    with zipfile.ZipFile(chemin) as zf:
        assert zf.getinfo("pret-jeux.db").file_size == 100

    with pytest.raises(sauvegarde.ZipInvalide, match="corrompue"):
        sauvegarde.valider_zip_sauvegarde(chemin)


# ---------------------------------------------------------------------------
# 4. ROB-06 — plafond de lignes du catalogue
# ---------------------------------------------------------------------------
def test_catalogue_au_plafond_de_lignes_accepte_au_dessus_refuse_sans_ecrire(
        client, bases, monkeypatch, _journal_isole):
    from scripts import import_csv

    monkeypatch.setattr(import_csv, "MAX_LIGNES_CATALOGUE", 3)
    _connexion(client)

    r = client.post("/admin/donnees/import",
                    files={"fichier": ("c.csv", _catalogue(3), "text/csv")})
    assert "Import réussi : 3 exemplaire(s)" in r.text
    assert _exemplaires(bases) == 4

    # 40 lignes dont 37 codes nouveaux : un import même partiel ferait monter
    # le compte.
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("c.csv", _catalogue(40), "text/csv")})
    assert r.status_code == 200
    assert "ne ressemble pas à un catalogue" in r.text
    assert _exemplaires(bases) == 4
    derniere = _lignes_journal(_journal_isole)[-1]
    assert derniere["action"] == "import_csv"
    assert derniere["ok"] is False and derniere["detail"] == "catalogue_trop_long"


def test_plafond_compte_pendant_la_lecture(tmp_path):
    """La lecture s'arrête à la première ligne de trop : jamais tout le fichier."""
    from scripts import import_csv

    chemin = tmp_path / "long.csv"
    chemin.write_bytes(_catalogue(50))
    lignes, _ = import_csv.lire_csv(chemin, max_lignes=50)
    assert len(lignes) == 50
    with pytest.raises(import_csv.CatalogueTropLong) as refus:
        import_csv.lire_csv(chemin, max_lignes=49)
    assert "49" in str(refus.value)


def test_ligne_de_commande_sans_plafond(tmp_path):
    """`python -m scripts.import_csv` n'a pas de plafond : son propre processus."""
    from scripts import import_csv

    chemin = tmp_path / "long.csv"
    chemin.write_bytes(_catalogue(import_csv.MAX_LIGNES_CATALOGUE + 1))
    lignes, _ = import_csv.lire_csv(chemin)
    assert len(lignes) == import_csv.MAX_LIGNES_CATALOGUE + 1
