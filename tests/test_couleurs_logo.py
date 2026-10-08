"""
COULEURS DU LOGO : suggestions de couleur du site tirées du logo déposé.

Ce que ce fichier vérifie, et pourquoi c'est ce qui compte :

1. **Les couleurs dominantes sont retrouvées**, la plus présente d'abord : une
   image fabriquée à couleurs connues doit les rendre, à l'arrondi près de la
   coupe médiane.
2. **Le fond n'est jamais proposé** : ni la transparence d'un PNG, ni le blanc
   d'un JPEG, ni un contour noir quand le logo a une vraie couleur.
3. **Un logo tout gris n'est pas laissé sans réponse**, mais le blanc pur
   n'est jamais proposé.
4. **Une aide ne casse rien** : sans logo déposé, rien n'est proposé (le
   meeple a déjà sa couleur, celle du thème par défaut) ; un fichier illisible
   donne une liste vide, jamais une erreur.
5. **L'écran propose sans enregistrer** : déposer un logo ne change pas la
   couleur du site, la page affiche les boutons, et celui de la couleur en
   vigueur est marqué choisi.

Tous les fichiers sont écrits dans un dossier temporaire (voir
`tests/test_logo.py`, dont la fixture est reprise).
"""

import io
import re

import pytest
from PIL import Image

MOT_DE_PASSE = "secret-admin-couleurs"

VIOLET = (110, 40, 150)
JAUNE = (240, 190, 30)


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
    conn.close()
    return tmp_path


@pytest.fixture
def client(bases):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _logo(bandes, *, fond=(0, 0, 0, 0), mode="RGBA"):
    """
    Logo de 200 x 100 : `fond` partout, puis des bandes verticales.

    `bandes` : liste de (couleur RVB, largeur en pixels), posées de gauche à
    droite. La largeur fixe la part de chaque couleur, donc l'ordre attendu.
    """
    image = Image.new("RGBA", (200, 100), fond)
    x = 0
    for couleur, largeur in bandes:
        image.paste((*couleur, 255), (x, 0, x + largeur, 100))
        x += largeur
    return image if mode == "RGBA" else image.convert("RGB")


def _deposer(bases, image, format_="PNG"):
    """Écrit le logo là où `app.logo` le lit, comme après un dépôt."""
    image.save(bases / "logo.png", format=format_)


def _proche(code, attendu, tolerance=12):
    valeurs = tuple(int(code[i:i + 2], 16) for i in (1, 3, 5))
    return all(abs(a - b) <= tolerance for a, b in zip(valeurs, attendu))


# ---------------------------------------------------------------------------
# 1 et 2. CE QUI EST PROPOSÉ
# ---------------------------------------------------------------------------
def test_les_couleurs_dominantes_sont_retrouvees_la_plus_presente_d_abord(bases):
    from app import logo

    _deposer(bases, _logo([(VIOLET, 120), (JAUNE, 40)]))
    couleurs = logo.couleurs_du_logo()
    assert len(couleurs) == 2
    assert _proche(couleurs[0], VIOLET)
    assert _proche(couleurs[1], JAUNE)


def test_le_fond_transparent_n_est_pas_propose(bases):
    """Le fond d'un PNG n'est pas une couleur du logo, même s'il en a une."""
    from app import logo

    _deposer(bases, _logo([(VIOLET, 30)], fond=(255, 0, 0, 0)))
    couleurs = logo.couleurs_du_logo()
    assert len(couleurs) == 1 and _proche(couleurs[0], VIOLET)


def test_le_fond_blanc_et_le_contour_noir_ne_sont_pas_proposes(bases):
    """JPEG sans transparence : le blanc couvre presque tout, il ne sort pas."""
    from app import logo

    image = _logo([((10, 10, 10), 30), (VIOLET, 20)], fond=(255, 255, 255, 255),
                  mode="RGB")
    _deposer(bases, image, "JPEG")
    couleurs = logo.couleurs_du_logo()
    assert couleurs and all(_proche(c, VIOLET, 25) for c in couleurs)


def test_deux_nuances_proches_ne_prennent_qu_une_place(bases):
    from app import logo

    _deposer(bases, _logo([(VIOLET, 60), ((120, 48, 160), 60)]))
    assert len(logo.couleurs_du_logo()) == 1


def test_jamais_plus_de_cinq_couleurs(bases):
    from app import logo

    bandes = [((200, 30, 30), 25), ((30, 160, 30), 25), ((30, 30, 200), 25),
              (JAUNE, 25), (VIOLET, 25), ((30, 170, 170), 25), ((230, 110, 20), 25)]
    _deposer(bases, _logo(bandes))
    assert len(logo.couleurs_du_logo()) == logo.SUGGESTIONS_MAX


def test_un_liseret_negligeable_n_est_pas_propose(bases):
    from app import logo

    _deposer(bases, _logo([(VIOLET, 199), (JAUNE, 1)]))
    couleurs = logo.couleurs_du_logo()
    assert len(couleurs) == 1 and _proche(couleurs[0], VIOLET)


# ---------------------------------------------------------------------------
# 3. UN LOGO SANS COULEUR
# ---------------------------------------------------------------------------
def test_un_logo_gris_propose_ses_gris_mais_jamais_le_blanc(bases):
    from app import logo

    _deposer(bases, _logo([((40, 40, 40), 60)], fond=(255, 255, 255, 255)))
    couleurs = logo.couleurs_du_logo()
    assert len(couleurs) == 1 and _proche(couleurs[0], (40, 40, 40))


def test_un_logo_tout_blanc_ne_propose_rien(bases):
    from app import logo

    _deposer(bases, _logo([], fond=(255, 255, 255, 255)))
    assert logo.couleurs_du_logo() == []


# ---------------------------------------------------------------------------
# 4. UNE AIDE NE CASSE RIEN
# ---------------------------------------------------------------------------
def test_sans_logo_depose_rien_n_est_propose(bases):
    from app import logo

    assert logo.couleurs_du_logo() == []


def test_un_fichier_illisible_donne_une_liste_vide(bases):
    from app import logo

    (bases / "logo.png").write_bytes(b"pas une image")
    assert logo.couleurs_du_logo() == []


# ---------------------------------------------------------------------------
# 5. L'ÉCRAN « IDENTITÉ »
# ---------------------------------------------------------------------------
def _connexion(client):
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _poster_logo(client, image):
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return client.post(
        "/admin/identite",
        data={"nom_association": "", "presentation": "", "contact": "",
              "depot_url": "", "couleur": ""},
        files={"logo_fichier": ("logo.png", tampon.getvalue(), "image/png")},
    )


def test_la_page_qui_suit_le_depot_propose_les_couleurs_sans_les_appliquer(client):
    from app import services
    from app.db import get_connection

    _connexion(client)
    reponse = _poster_logo(client, _logo([(VIOLET, 120)]))
    assert reponse.status_code == 200
    assert "Couleurs tirées de votre logo" in reponse.text
    assert 'class="suggestion-couleur"' in reponse.text
    assert 'role="status" id="suggestions-couleur-statut"' in reponse.text

    conn = get_connection()
    try:
        assert services.lire_couleur_association(conn) is None
    finally:
        conn.close()


def test_sans_logo_depose_la_page_ne_propose_rien(client):
    _connexion(client)
    page = client.get("/admin/identite").text
    assert "Couleurs tirées de votre logo" not in page
    assert "suggestion-couleur" not in page


def test_la_couleur_en_vigueur_est_marquee_choisie(client, bases):
    from app import logo

    _deposer(bases, _logo([(VIOLET, 120), (JAUNE, 40)]))
    premiere, seconde = logo.couleurs_du_logo()
    _connexion(client)
    client.post("/admin/identite", data={
        "nom_association": "", "presentation": "", "contact": "",
        "depot_url": "", "couleur": seconde.upper()})
    page = client.get("/admin/identite").text
    assert re.search(f'data-couleur="{seconde}"\\s+aria-pressed="true"', page)
    assert re.search(f'data-couleur="{premiere}"\\s+aria-pressed="false"', page)


def test_une_couleur_pale_annonce_sa_version_foncee(client, bases):
    """Le jaune se lit mal en texte sur fond clair : le bouton le dit."""
    from app import services

    _deposer(bases, _logo([(JAUNE, 120)]))
    _connexion(client)
    page = client.get("/admin/identite").text
    assert "pour le texte" in page
    from app import logo
    jaune = logo.couleurs_du_logo()[0]
    assert services.nuances_theme(jaune)["lisible"] in page
