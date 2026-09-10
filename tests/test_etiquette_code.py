"""
CODE DE LA BOÎTE SUR L'ÉTIQUETTE (lot code-boite-1).

Ce que ce fichier vérifie, et pourquoi c'est ce qui compte :

1. **Le code n'agrandit l'étiquette d'aucun pixel.** Il partage le cadre du
   bas avec le code de classement. La planche met chaque étiquette à l'échelle
   de sa cellule : toute croissance, en hauteur comme en largeur, finit par
   rétrécir le QR sur l'une des grilles réglables. Le test compare les deux
   rendus pixel à pixel : tout ce qui est au-dessus du cadre du bas doit être
   IDENTIQUE, et les dimensions inchangées.
2. **Le code imprimé est la chaîne `id_exemplaire` exacte** — zéros de tête
   conservés, casse respectée. Deux codes qui ne diffèrent que par là doivent
   donner deux images différentes.
3. **Le réglage ne change pas ce que le QR encode.** C'est l'invariant le plus
   coûteux à casser : sept cents étiquettes sont déjà collées sur des boîtes.
4. **La route PNG suit le MÊME réglage que la planche.** C'est le point qui a
   dicté de stocker le réglage plutôt que de le porter dans le formulaire
   d'export : réimprimer une étiquette abîmée ne doit pas produire une
   étiquette d'un autre genre que le reste du parc.
5. **Le code domine son voisin.** Aucun libellé imprimé ne dit lequel des deux
   codes taper — c'est la taille qui le dit, et elle est vérifiée ici.
6. **Le défaut d'une base vierge est « affiché »**, et l'aller-retour du
   formulaire rend la case dans l'état où le bureau l'a laissée.

Aucun rendu n'est comparé à une image de référence : `_police` retombe sur
DejaVu, Arial ou la police Pillow selon la machine, et un fichier témoin serait
vert ici, rouge ailleurs. Les critères retenus (égalité de deux rendus entre
eux, largeur, présence de pixels noirs) ne dépendent pas de la police installée.
"""

import pytest

from app import services
from app.etiquettes import (CADRE_BAS_H, CODE_PART_CADRE, MARGE_EXTERIEURE,
                            image_etiquette, url_fiche)

MOT_DE_PASSE = "secret-admin-etiquettes"

EXEMPLE = {
    "nom": "Catan",
    "age_min": 10,
    "nb_joueurs_min": 3,
    "nb_joueurs_max": 4,
    "duree_min": 75,
    "id_exemplaire": "001",
}


def _etiquette(code="001", afficher_code=True, nom="Catan"):
    """Rend l'étiquette d'un exemplaire, sans toucher à la base."""
    ex = dict(EXEMPLE, id_exemplaire=code, nom=nom)
    return image_etiquette(url_fiche("https://exemple.test", code), ex,
                           afficher_code=afficher_code)


def _pixels_noirs(image) -> int:
    """Compte les pixels sombres (histogramme : pas de parcours pixel à pixel)."""
    return sum(image.convert("L").histogram()[:128])


def _hauteur_encre(image) -> int:
    """Hauteur de la tache d'encre dans une image, en pixels (0 si vide)."""
    gris = image.convert("L")
    lignes = [y for y in range(gris.height)
              if any(gris.getpixel((x, y)) < 128 for x in range(gris.width))]
    return lignes[-1] - lignes[0] + 1 if lignes else 0


def _cadre_est_partage(image) -> bool:
    """
    True si le cadre du bas porte le trait de séparation du code.

    Critère plus sûr que « la case gauche est vide » : sans code, le code de
    classement est centré sur TOUTE la largeur du cadre et déborde donc dans la
    moitié gauche. Ce qui distingue les deux états, c'est le trait.
    """
    panneau_w = 400
    px = image.width - MARGE_EXTERIEURE - panneau_w
    cy = image.height - MARGE_EXTERIEURE - CADRE_BAS_H
    x = px + int(panneau_w * CODE_PART_CADRE)
    gris = image.convert("L")
    hauteurs = range(cy + 5, cy + CADRE_BAS_H - 5)
    encre = sum(1 for y in hauteurs if gris.getpixel((x, y)) < 128)
    return encre >= 0.9 * len(list(hauteurs))


def _cases_du_cadre(image):
    """
    Les deux cases INTÉRIEURES du cadre du bas : (code, classement).

    Le cadre occupe toute la largeur du panneau de droite ; le panneau commence
    là où finit le QR. Les bords (trait de 3 px) sont exclus, sans quoi on
    mesurerait l'encre du cadre lui-même.
    """
    panneau_w = 400
    px = image.width - MARGE_EXTERIEURE - panneau_w
    cy = image.height - MARGE_EXTERIEURE - CADRE_BAS_H
    part = int(panneau_w * CODE_PART_CADRE)
    marge = 5
    return (image.crop((px + marge, cy + marge,
                        px + part - marge, cy + CADRE_BAS_H - marge)),
            image.crop((px + part + marge, cy + marge,
                        px + panneau_w - marge, cy + CADRE_BAS_H - marge)))


# ---------------------------------------------------------------------------
# Le dessin
# ---------------------------------------------------------------------------
def test_le_code_partage_le_cadre_du_bas_sans_rien_deplacer():
    avec, sans = _etiquette(), _etiquette(afficher_code=False)

    # Pas un pixel de plus, ni en hauteur ni en largeur.
    assert avec.size == sans.size

    # Tout ce qui surplombe le cadre du bas est inchangé, au pixel près : le QR
    # ne bouge pas, ne rétrécit pas, le nom n'est pas recomposé.
    au_dessus = (0, 0, avec.width, avec.height - MARGE_EXTERIEURE - CADRE_BAS_H)
    assert avec.crop(au_dessus).tobytes() == sans.crop(au_dessus).tobytes()

    # Et le cadre, lui, a bien changé : une case de plus, du texte de plus.
    cadre = (0, avec.height - MARGE_EXTERIEURE - CADRE_BAS_H, avec.width, avec.height)
    assert _pixels_noirs(avec.crop(cadre)) > _pixels_noirs(sans.crop(cadre))


def test_le_code_est_deux_fois_plus_haut_que_le_code_de_classement():
    """
    Rien n'est imprimé pour dire lequel des deux codes taper : une mention
    tiendrait à 1,3 mm de haut une fois l'étiquette à l'échelle, et coûterait
    4 % de QR. C'est la HIÉRARCHIE DE TAILLE qui porte le message — donc elle
    se teste.
    """
    from PIL import ImageFont

    from app.etiquettes import _police

    if not isinstance(_police(24), ImageFont.FreeTypeFont):
        pytest.skip("aucune police TrueType : toutes les tailles se valent ici")

    case_code, case_classement = _cases_du_cadre(_etiquette("042"))
    assert _hauteur_encre(case_code) >= 1.7 * _hauteur_encre(case_classement)


def test_le_reglage_ne_change_pas_ce_que_le_qr_encode():
    """
    Corollaire du test précédent, énoncé à part parce que c'est l'invariant
    qui compte : le QR est bit à bit le même dans les deux rendus, il ne peut
    donc pas encoder autre chose. `url_fiche` reste la seule source de l'URL.
    """
    avec, sans = _etiquette(), _etiquette(afficher_code=False)
    coin_qr = (0, 0, 300, sans.height)

    assert avec.crop(coin_qr).tobytes() == sans.crop(coin_qr).tobytes()
    assert url_fiche("https://exemple.test", "001") == "https://exemple.test/jeu/001"


@pytest.mark.parametrize("code", ["001", "00472", "A0001", "E018", "685"])
def test_toutes_les_formes_de_code_sortent_a_la_meme_taille(code):
    """
    Les cinq formes présentes en base : trois chiffres, un zéro de tête, le
    code créé par l'admin (`A0001`), une extension. Aucune n'est tronquée —
    `_police_encre` réduit la police plutôt que de couper la chaîne — et toutes
    sortent à la MÊME hauteur d'encre, parce que c'est la hauteur du cadre qui
    limite, pas la largeur du texte. Un parc homogène, quel que soit le code.
    """
    case_code, _ = _cases_du_cadre(_etiquette(code))
    reference, _ = _cases_du_cadre(_etiquette("001"))

    assert _pixels_noirs(case_code) > 0
    assert _hauteur_encre(case_code) == _hauteur_encre(reference)


@pytest.mark.parametrize("code", ["001", "00472", "A0001", "E018"])
def test_le_code_ne_deborde_pas_de_sa_case(code):
    """
    `A0001` est le cas le plus large (deux lettres pleines). Les colonnes de
    bord de la case doivent rester blanches : sans quoi le code toucherait le
    trait de séparation ou le cadre, et deviendrait pénible à lire.
    """
    case_code, _ = _cases_du_cadre(_etiquette(code))
    bord_gauche = case_code.crop((0, 0, 3, case_code.height))
    bord_droit = case_code.crop((case_code.width - 3, 0,
                                 case_code.width, case_code.height))

    assert _pixels_noirs(bord_gauche) == 0
    assert _pixels_noirs(bord_droit) == 0


def test_un_zero_de_tete_n_est_jamais_perdu():
    """
    `id_exemplaire` est stocké en TEXT précisément pour cela. Deux codes qui
    ne diffèrent que par un zéro de tête doivent donner deux dessins
    différents — sans quoi le bénévole taperait ce qu'il lit et tomberait sur
    une autre boîte, ou sur rien.
    """
    assert _etiquette("0042").tobytes() != _etiquette("42").tobytes()


def test_la_casse_est_respectee():
    """Pas de passage en majuscules : le QR encode `a0001` tel quel."""
    assert _etiquette("A0001").tobytes() != _etiquette("a0001").tobytes()


def test_un_nom_long_ne_change_rien_a_la_largeur():
    """
    Arbitrage de mise en page : le panneau de droite garde sa largeur. Un nom
    long se replie comme avant (jusqu'à trois lignes) et fait grandir la
    HAUTEUR — comportement d'avant ce lot, inchangé.
    """
    court = _etiquette(nom="Catan")
    long_ = _etiquette(nom="Les Aventuriers du Rail Europe Edition Anniversaire")

    assert long_.width == court.width
    assert long_.height >= court.height


# ---------------------------------------------------------------------------
# Le réglage
# ---------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Patron de tests/test_routes.py : trois bases temporaires par test."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

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

    client = TestClient(app)
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return client


def _case_cochee(html: str) -> bool:
    """
    True si la case du réglage est cochée DANS SA BALISE.

    Chercher « checked » dans toute la page serait faux : le script du
    compteur d'étiquettes contient `c.checked = etat`.
    """
    import re

    balise = re.search(r"<input[^>]*name=\"afficher_code\"[^>]*>", html, re.S)
    assert balise, "la case du réglage a disparu de la page"
    return "checked" in balise.group(0)


def _connexion_base():
    from app.db import get_connection

    return get_connection()


def test_une_base_vierge_affiche_le_code(client):
    """
    Défaut assumé : la clé n'a jamais été écrite, le code s'imprime quand
    même. Une base d'avant ce lot voit donc ses étiquettes changer — écart
    volontaire, cosmétique, tranché avec le bureau.
    """
    conn = _connexion_base()
    try:
        assert services.lire_etiquette_code(conn) is True
    finally:
        conn.close()

    assert _case_cochee(client.get("/admin/etiquettes").text)


def test_aller_retour_du_formulaire(client):
    # Case décochée : le navigateur n'envoie RIEN pour ce champ.
    enregistre = client.post("/admin/etiquettes/code", data={}, follow_redirects=False)
    assert enregistre.status_code == 303
    assert enregistre.headers["location"].startswith("/admin/etiquettes?message=")

    conn = _connexion_base()
    try:
        assert services.lire_etiquette_code(conn) is False
    finally:
        conn.close()

    page = client.get("/admin/etiquettes")
    assert not _case_cochee(page.text)
    # L'écran ne promet plus un code qu'il n'imprime pas.
    assert "sans le code à taper" in page.text

    # Recochée : le réglage revient, et la page le confirme.
    client.post("/admin/etiquettes/code", data={"afficher_code": "1"})
    assert _case_cochee(client.get("/admin/etiquettes").text)


def test_le_changement_de_reglage_est_journalise(client, _journal_isole):
    client.post("/admin/etiquettes/code", data={})
    lignes = [l for l in _journal_isole.read_text(encoding="utf-8").splitlines()
              if "etiquette_code_modifie" in l]
    assert len(lignes) == 1 and "masqué" in lignes[0]

    # Réenregistrer la même valeur n'ajoute pas de ligne : le journal note ce
    # qui change, pas ce qu'on reclique.
    client.post("/admin/etiquettes/code", data={})
    lignes = [l for l in _journal_isole.read_text(encoding="utf-8").splitlines()
              if "etiquette_code_modifie" in l]
    assert len(lignes) == 1


def test_la_route_png_suit_le_meme_reglage_que_la_planche(client):
    """
    LE test de ce lot. La réimpression d'une étiquette abîmée n'a aucun
    formulaire : si le réglage vivait dans celui de l'export, elle sortirait
    sans le code, des mois plus tard, sans que rien ne le signale.
    """
    import io

    from PIL import Image

    avec = Image.open(io.BytesIO(client.get("/admin/etiquette/001.png").content))
    assert client.post("/admin/etiquettes/pdf",
                       data={"references": "CATAN"}).content[:4] == b"%PDF"

    client.post("/admin/etiquettes/code", data={})          # on décoche
    sans = Image.open(io.BytesIO(client.get("/admin/etiquette/001.png").content))

    # Le code ne change pas les dimensions : le cadre du bas se partage en deux
    # cases, ou reste d'un seul tenant.
    assert avec.size == sans.size
    assert _cadre_est_partage(avec) and not _cadre_est_partage(sans)
    # La planche reste générable dans les deux états (jamais d'erreur brute).
    assert client.post("/admin/etiquettes/pdf",
                       data={"references": "CATAN"}).content[:4] == b"%PDF"


def test_le_script_lit_le_reglage_et_sait_le_forcer(client, tmp_path):
    """
    Troisième producteur : `scripts/generate_qr.py`. Il lit le réglage en base
    comme les deux routes, et `--sans-code` ne force que le tirage en cours,
    sans jamais écrire en base.
    """
    from scripts.generate_qr import generer_pngs, lire_reglage_code

    assert lire_reglage_code() is True

    from PIL import Image

    exemplaires = [dict(EXEMPLE)]
    generer_pngs(exemplaires, "https://exemple.test", tmp_path / "avec", None, False)
    generer_pngs(exemplaires, "https://exemple.test", tmp_path / "sans", None, False,
                 afficher_code=False)

    with Image.open(tmp_path / "avec" / "001.png") as avec, \
            Image.open(tmp_path / "sans" / "001.png") as sans:
        assert avec.size == sans.size
        assert _cadre_est_partage(avec) and not _cadre_est_partage(sans)

    # Le forçage n'a rien écrit : le réglage du bureau est intact.
    assert lire_reglage_code() is True
