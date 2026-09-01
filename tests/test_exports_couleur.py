"""
Couleur d'identité dans les exports PDF (lot 3c).

Les lots 3a/3b ont rendu le logo et la couleur réglables à l'écran ; les
exports imprimés étaient restés en arrière (`#4a148c` en dur dans DEUX
fichiers, `app/exports.py` et `app/planning/exports.py` — voir le compte rendu
du lot 3b, section 2.1). Ce fichier vérifie que les trois fonctions d'export
concernées (`exports.construire_pdf`, `exports.signalements_pdf`,
`planning.exports.construire_pdf`) portent désormais la couleur réglée, avec
un défaut explicite (l'anthracite) quand elle ne l'est pas, et que la couleur
du texte d'en-tête suit toujours la luminance (jamais `colors.white` en dur).

Les exports Excel n'ont volontairement AUCUNE couleur (juste du gras) et n'en
reçoivent pas : pas de test ici pour `construire_xlsx`.

Patron d'interception déjà utilisé ailleurs (tests/test_routes.py,
tests/test_signalements_admin.py) : le contenu d'un PDF reportlab est
compressé, donc illisible dans les octets bruts. On intercepte plutôt
`TableStyle` (importée à l'intérieur des fonctions d'export, donc le mock est
bien pris en compte) pour inspecter les commandes réellement transmises.
"""

import pytest
from reportlab.lib import colors

from app import exports, services
from app.planning import exports as exports_planning

DATA = {
    "globales": {"total_prets": 3, "en_cours": 1, "titres_pretes": 2,
                 "nb_titres": 5, "duree_moyenne": "1 h 30", "erreurs": 0},
    "plus": [], "moins": [], "metrique": "total", "prets": [],
}

GRILLE = {
    "postes": [{"nom": "Accueil"}],
    "jours": [{
        "libelle": "Samedi",
        "creneaux": [{
            "creneau": {"debut": "2026-09-12T08:00:00+00:00",
                       "fin": "2026-09-12T10:00:00+00:00"},
            "cases": [{"poste": {"nom": "Accueil"}, "nb_requis": 1,
                      "affectations": []}],
        }],
    }],
    "taches": [],
}

SIGNALEMENT = [{
    "jeu_nom": "Catan", "id_exemplaire": "001", "categorie_nom": "Pièce",
    "texte": "il manque un dé", "cree_local": "01/09/2026 10:00",
    "emplacement_evenement": "", "emplacement_local_nom": "",
}]


def _capturer_styles(monkeypatch, module_platypus):
    """
    Patche `TableStyle` du module reportlab.platypus donné et renvoie la
    liste des jeux de commandes transmis, dans l'ordre de construction.
    """
    captures = []
    reelle = module_platypus.TableStyle

    def espion(cmds, *a, **k):
        captures.append(cmds)
        return reelle(cmds, *a, **k)

    monkeypatch.setattr(module_platypus, "TableStyle", espion)
    return captures


def _commande(cmds, nom):
    """La première commande `nom` d'un jeu de commandes TableStyle."""
    return next(c for c in cmds if c[0] == nom)


# ---------------------------------------------------------------------------
# 1. app.exports.construire_pdf (statistiques)
# ---------------------------------------------------------------------------
def test_construire_pdf_sans_couleur_utilise_lanthracite(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports.construire_pdf(DATA, "toutes périodes", {"synthese"})

    fond = _commande(captures[0], "BACKGROUND")[3]
    assert fond == colors.HexColor(services.COULEUR_ASSOCIATION_DEFAUT)


def test_construire_pdf_porte_la_couleur_reglee(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports.construire_pdf(DATA, "toutes périodes", {"synthese"},
                           couleur="#123456")

    cmds = captures[0]
    assert _commande(cmds, "BACKGROUND")[3] == colors.HexColor("#123456")
    # Fond des lignes alternées : la nuance « fond » de cette même couleur,
    # jamais recopiée — voir services.nuances_theme.
    fond_attendu = colors.HexColor(services.nuances_theme("#123456")["fond"])
    assert _commande(cmds, "ROWBACKGROUNDS")[3][1] == fond_attendu


def test_construire_pdf_la_couleur_du_texte_suit_la_luminance(monkeypatch):
    """
    Jamais `colors.white` en dur : sur une couleur d'identité TRÈS claire, le
    texte d'en-tête doit basculer au noir pour rester lisible.
    """
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)

    exports.construire_pdf(DATA, "toutes périodes", {"synthese"},
                           couleur="#2a2724")  # sombre
    assert _commande(captures[0], "TEXTCOLOR")[3] == colors.white

    captures.clear()
    exports.construire_pdf(DATA, "toutes périodes", {"synthese"},
                           couleur="#fffbe6")  # jaune très pâle
    assert _commande(captures[0], "TEXTCOLOR")[3] == colors.black


# ---------------------------------------------------------------------------
# 2. app.exports.signalements_pdf (carnet de maintenance)
# ---------------------------------------------------------------------------
def test_signalements_pdf_sans_couleur_utilise_lanthracite(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports.signalements_pdf(SIGNALEMENT, "à traiter")

    fond = _commande(captures[0], "BACKGROUND")[3]
    assert fond == colors.HexColor(services.COULEUR_ASSOCIATION_DEFAUT)


def test_signalements_pdf_porte_la_couleur_reglee(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports.signalements_pdf(SIGNALEMENT, "à traiter", couleur="#123456")

    cmds = captures[0]
    assert _commande(cmds, "BACKGROUND")[3] == colors.HexColor("#123456")
    fond_attendu = colors.HexColor(services.nuances_theme("#123456")["fond"])
    assert _commande(cmds, "ROWBACKGROUNDS")[3][1] == fond_attendu


def test_signalements_pdf_sans_signalement_ne_plante_pas_avec_une_couleur():
    """Jamais bloquant : une liste vide produit un PDF, quelle que soit la couleur."""
    contenu = exports.signalements_pdf([], "à traiter", couleur="#123456")
    assert contenu[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# 3. app.planning.exports.construire_pdf
# ---------------------------------------------------------------------------
def test_planning_construire_pdf_sans_couleur_utilise_lanthracite(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports_planning.construire_pdf(GRILLE, "Festival 2026")

    fond = _commande(captures[0], "BACKGROUND")[3]
    assert fond == colors.HexColor(services.COULEUR_ASSOCIATION_DEFAUT)


def test_planning_construire_pdf_porte_la_couleur_reglee(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports_planning.construire_pdf(GRILLE, "Festival 2026", couleur="#123456")

    cmds = captures[0]
    assert _commande(cmds, "BACKGROUND")[3] == colors.HexColor("#123456")
    fond_attendu = colors.HexColor(services.nuances_theme("#123456")["fond"])
    assert _commande(cmds, "ROWBACKGROUNDS")[3][1] == fond_attendu


def test_planning_construire_pdf_la_couleur_du_texte_suit_la_luminance(monkeypatch):
    import reportlab.platypus as platypus

    captures = _capturer_styles(monkeypatch, platypus)
    exports_planning.construire_pdf(GRILLE, "Festival 2026", couleur="#fffbe6")
    assert _commande(captures[0], "TEXTCOLOR")[3] == colors.black


# ---------------------------------------------------------------------------
# 4. ROBUSTESSE — la ROUTE ne doit jamais échouer pour une question de couleur
# ---------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Trois bases temporaires, patron de tests/test_theme.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-exports-couleur")

    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    db.init_db()
    tdb.init_db()
    pdb.init_db()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_stats_export_pdf_reussit_meme_si_la_lecture_de_la_couleur_echoue(
    client, monkeypatch
):
    """
    `theme_association()` ne lève jamais (voir tests/test_theme.py) : ce test
    vérifie que la ROUTE en profite bien, elle qui l'appelle pour transmettre
    la couleur à l'export — un export ne doit jamais échouer pour une question
    de couleur, même si la lecture en base échoue.
    """
    import sqlite3

    def _tombe(conn):
        raise sqlite3.OperationalError("base indisponible")

    monkeypatch.setattr(services, "lire_couleur_association", _tombe)

    r = client.get("/stats/export.pdf")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
