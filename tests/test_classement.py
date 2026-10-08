"""
CODE DE CLASSEMENT DE L'ÉTIQUETTE (lot classement-1).

Le cadre du bas de l'étiquette porte, à côté du code de la boîte, un code qui
résume le jeu. Jusqu'ici il commençait par un placeholder `XXX` jamais résolu ;
ce lot fixe sa forme avant que le parc ne soit imprimé. Ce que ce fichier
vérifie, et pourquoi :

1. **Les deux formats**, dense (`8-2-4-30`) et lisible (`8+ · 2-4j · 30min`),
   sur tous les cas limites — une valeur absente, min = max, une seule borne,
   un zéro. Le format dense est celui d'avant, au caractère près.
2. **Jamais de lettres partielles** : la position porte le sens, deux lettres
   sur trois ne disent rien. Trois lettres valides, ou aucune.
3. **Aucun triplet n'est imprimable aujourd'hui** (listes 2 et 3 vides). Pour
   prouver que le chemin des lettres fonctionne de bout en bout malgré tout,
   les tests qui en ont besoin remplissent les listes le temps du test.
4. **Les trois producteurs d'étiquettes** reçoivent les lettres et le format :
   un producteur oublié imprimerait sans lettres, en silence.
5. **Le code ne déborde jamais de sa case**, au pire cas réel du catalogue,
   lettres comprises.
6. **Le réglage du format** : défaut lisible, valeur inconnue tolérée, champ
   absent sans effet, journal au changement seulement.
7. **L'import ne touche jamais aux lettres déjà en base** sauf pour en écrire
   de valides, et compte celles qu'il refuse.

Aucun rendu n'est comparé à une image de référence (la police varie selon la
machine) : les critères sont des égalités entre rendus, des bords blancs et
des hauteurs d'encre.
"""

import io
import re
import sqlite3
import string

import pytest
from PIL import Image, ImageDraw, ImageFont

from app import classement, etiquettes, services
from app.classement import (FORMAT_DENSE, FORMAT_DEFAUT, FORMAT_LISIBLE,
                            FORMATS, code_classement, lettres_imprimables,
                            lire_saisie, triplet_imprimable)
from app.etiquettes import (CADRE_BAS_H, CLASSEMENT_TAILLE_MAX,
                            CODE_PART_CADRE, MARGE_EXTERIEURE,
                            image_etiquette, url_fiche)

MOT_DE_PASSE = "secret-admin-classement"

JEU = {"age_min": 8, "nb_joueurs_min": 2, "nb_joueurs_max": 4, "duree_min": 30}
LETTRES = {"lettre_public": "T", "lettre_jeu": "P", "lettre_materiel": "C"}

# Les pires cas du catalogue réel (610 titres, mesurés au lot) : le plus large
# rendu, et les maxima de chaque champ réunis — plus large encore.
PIRE_CAS_REEL = {"age_min": 12, "nb_joueurs_min": 4, "nb_joueurs_max": 16,
                 "duree_min": 120}
MAXIMA = {"age_min": 18, "nb_joueurs_min": 10, "nb_joueurs_max": 50,
          "duree_min": 240}


@pytest.fixture
def listes_remplies(monkeypatch):
    """
    Remplit les listes 2 et 3 le temps d'un test.

    Seul moyen, tant que la nomenclature n'est pas arrêtée, de prouver que des
    lettres valides traversent la base, l'import, l'export et les trois
    producteurs d'étiquettes.
    """
    monkeypatch.setattr(classement, "LETTRES_JEU", {"P": "Placement"})
    monkeypatch.setattr(classement, "LETTRES_MATERIEL", {"C": "Cartes"})


# ---------------------------------------------------------------------------
# Les listes
# ---------------------------------------------------------------------------
def test_chaque_cle_des_listes_est_une_seule_lettre_majuscule():
    """Vaut pour les listes futures : une lettre par position, A à Z."""
    for liste in classement._listes():
        for lettre, libelle in liste.items():
            assert len(lettre) == 1 and lettre in string.ascii_uppercase, lettre
            assert isinstance(libelle, str) and libelle.strip()


def test_la_liste_du_public_suit_les_categories_de_l_as_d_or():
    assert classement.LETTRES_PUBLIC == {
        "E": "Enfant", "T": "Tout public", "I": "Initié", "X": "Expert"}


def test_aucun_triplet_n_est_imprimable_aujourd_hui():
    """
    CE TEST DOIT TOMBER, VOLONTAIREMENT, au lot qui remplira les listes de la
    façon de jouer et du matériel : il dit qu'aucune lettre ne peut encore
    sortir à l'impression. Ce jour-là, le supprimer — pas le contourner.
    """
    assert classement.LETTRES_JEU == {}, (
        "La liste « façon de jouer » n'est plus vide : supprimez ce test, "
        "il ne protégeait que l'état d'attente des listes.")
    assert classement.LETTRES_MATERIEL == {}, (
        "La liste « matériel » n'est plus vide : supprimez ce test, il ne "
        "protégeait que l'état d'attente des listes.")
    for public in classement.LETTRES_PUBLIC:
        assert not triplet_imprimable(public, "P", "C")
    assert lettres_imprimables(dict(JEU, **LETTRES)) == ""


# ---------------------------------------------------------------------------
# Les lettres : trois valides, ou aucune
# ---------------------------------------------------------------------------
def test_trois_lettres_valides_sont_imprimees(listes_remplies):
    ex = dict(JEU, **LETTRES)
    assert code_classement(ex, FORMAT_DENSE) == "TPC8-2-4-30"
    assert code_classement(ex, FORMAT_LISIBLE) == "TPC · 8+ · 2-4j · 30min"


@pytest.mark.parametrize("lettres", [
    {"lettre_public": "T", "lettre_jeu": "P", "lettre_materiel": None},
    {"lettre_public": None, "lettre_jeu": "P", "lettre_materiel": "C"},
    {"lettre_public": "T", "lettre_jeu": None, "lettre_materiel": None},
    {"lettre_public": "Z", "lettre_jeu": "P", "lettre_materiel": "C"},   # hors liste
    {"lettre_public": "T", "lettre_jeu": "C", "lettre_materiel": "P"},   # inversées
    {"lettre_public": "t", "lettre_jeu": "p", "lettre_materiel": "c"},   # pas normalisées
    {"lettre_public": "T", "lettre_jeu": "PP", "lettre_materiel": "C"},
], ids=["sans-materiel", "sans-public", "une-seule", "hors-liste",
        "inversees", "minuscules", "deux-caracteres"])
@pytest.mark.parametrize("format_code", FORMATS)
def test_lettres_partielles_ou_invalides_aucune_lettre(listes_remplies, lettres,
                                                       format_code):
    avec = code_classement(dict(JEU, **lettres), format_code)
    sans = code_classement(dict(JEU), format_code)
    assert avec == sans


# ---------------------------------------------------------------------------
# Format dense — celui d'avant, sans le XXX
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ex, attendu", [
    (JEU, "8-2-4-30"),
    (dict(JEU, age_min=None), "?-2-4-30"),
    (dict(JEU, nb_joueurs_max=None), "8-2-?-30"),
    (dict(JEU, duree_min=""), "8-2-4-?"),
    ({}, "?-?-?-?"),
    (dict(JEU, age_min=0), "0-2-4-30"),
])
def test_format_dense(ex, attendu):
    assert code_classement(ex, FORMAT_DENSE) == attendu


def test_le_placeholder_xxx_a_disparu():
    for format_code in FORMATS:
        assert "XXX" not in code_classement(JEU, format_code)


# ---------------------------------------------------------------------------
# Format lisible — une valeur absente disparaît
# ---------------------------------------------------------------------------
P = "·"


@pytest.mark.parametrize("ex, attendu", [
    (JEU, f"8+ {P} 2-4j {P} 30min"),
    (dict(JEU, nb_joueurs_min=4), f"8+ {P} 4j {P} 30min"),               # min = max
    (dict(JEU, nb_joueurs_max=None), f"8+ {P} 2j {P} 30min"),            # min sans max
    (dict(JEU, nb_joueurs_min=None), f"8+ {P} 4j {P} 30min"),            # max sans min
    (dict(JEU, age_min=None), f"2-4j {P} 30min"),
    (dict(JEU, nb_joueurs_min=None, nb_joueurs_max=None), f"8+ {P} 30min"),
    (dict(JEU, duree_min=None), f"8+ {P} 2-4j"),
    (dict(JEU, duree_min=240), f"8+ {P} 2-4j {P} 240min"),               # pas d'heures
    (dict(JEU, age_min=""), f"2-4j {P} 30min"),
    ({}, ""),
    ({"age_min": None, "nb_joueurs_min": None, "nb_joueurs_max": None,
      "duree_min": None}, ""),
], ids=["complet", "min-egal-max", "min-sans-max", "max-sans-min", "sans-age",
        "sans-joueurs", "sans-duree", "duree-240", "age-chaine-vide",
        "dict-vide", "tout-absent"])
def test_format_lisible(ex, attendu):
    assert code_classement(ex, FORMAT_LISIBLE) == attendu


@pytest.mark.parametrize("ex, attendu", [
    (dict(JEU, age_min=0), f"2-4j {P} 30min"),
    (dict(JEU, nb_joueurs_min=0, nb_joueurs_max=0), f"8+ {P} 30min"),
    (dict(JEU, nb_joueurs_min=0), f"8+ {P} 4j {P} 30min"),
    (dict(JEU, duree_min=0), f"8+ {P} 2-4j"),
])
def test_un_zero_vaut_absent_en_lisible(ex, attendu):
    """
    L'import peut produire un 0 (une case « 0 »), l'écran de création d'un jeu
    aussi. « 0j » ou « 0min » ne disent rien de vrai, et la future source des
    données note 0 pour « non renseigné » : en lisible, zéro disparaît.
    """
    assert code_classement(ex, FORMAT_LISIBLE) == attendu


def test_lettres_seules_en_lisible(listes_remplies):
    assert code_classement(dict(LETTRES), FORMAT_LISIBLE) == "TPC"


@pytest.mark.parametrize("valeur", [None, "", "compact", "DENSE", 3])
def test_un_format_inconnu_vaut_le_defaut(valeur):
    assert classement.format_connu(valeur) == FORMAT_DEFAUT == FORMAT_LISIBLE
    assert code_classement(JEU, valeur) == code_classement(JEU, FORMAT_LISIBLE)


# ---------------------------------------------------------------------------
# Lecture d'une saisie CSV
# ---------------------------------------------------------------------------
def test_lire_saisie_tolere_casse_et_espaces(listes_remplies):
    assert lire_saisie(" tpc ") == ("T", "P", "C")
    assert lire_saisie("TPC") == ("T", "P", "C")


@pytest.mark.parametrize("saisie", ["", None, "TP", "TPCX", "T P C", "ZPC",
                                    "TCP", "T-C", "123"])
def test_lire_saisie_refuse_tout_le_reste(listes_remplies, saisie):
    assert lire_saisie(saisie) is None


def test_lire_saisie_refuse_tout_aujourd_hui():
    """Listes 2 et 3 vides : aucune saisie n'est valide dans ce lot."""
    assert lire_saisie("TPC") is None


# ---------------------------------------------------------------------------
# Le dessin : le code tient dans sa case
# ---------------------------------------------------------------------------
def _police(taille):
    """La police de l'étiquette, lue à l'appel (une machine, une police)."""
    return etiquettes._police(taille)


def _truetype() -> bool:
    return isinstance(_police(24), ImageFont.FreeTypeFont)


def _etiquette(ex, afficher_code=True, format_code=FORMAT_LISIBLE):
    ex = dict(ex, nom="Catan", id_exemplaire="A0001")
    return image_etiquette(url_fiche("https://exemple.test", "A0001"), ex,
                           afficher_code=afficher_code, format_code=format_code)


def _case_classement(image, partage=True):
    """L'intérieur de la case du code de classement (traits de 3 px exclus)."""
    panneau_w = 400
    px = image.width - MARGE_EXTERIEURE - panneau_w
    cy = image.height - MARGE_EXTERIEURE - CADRE_BAS_H
    gauche = px + (int(panneau_w * CODE_PART_CADRE) if partage else 0)
    marge = 5
    return image.crop((gauche + marge, cy + marge,
                       px + panneau_w - marge, cy + CADRE_BAS_H - marge))


def _pixels_noirs(image) -> int:
    return sum(image.convert("L").histogram()[:128])


@pytest.mark.parametrize("partage", [True, False], ids=["cadre-partage", "cadre-seul"])
@pytest.mark.parametrize("avec_lettres", [False, True], ids=["sans-lettres", "TPC"])
@pytest.mark.parametrize("format_code", FORMATS)
@pytest.mark.parametrize("ex", [PIRE_CAS_REEL, MAXIMA], ids=["pire-reel", "maxima"])
def test_le_code_de_classement_ne_deborde_pas(listes_remplies, ex, format_code,
                                              avec_lettres, partage):
    """
    Au pire cas réel, et aux maxima de chaque champ réunis : les colonnes de
    bord de la case restent blanches. Sans l'ajustement, le format lisible
    avec lettres débordait du cadre partagé.
    """
    donnees = dict(ex, **LETTRES) if avec_lettres else dict(ex)
    case = _case_classement(_etiquette(donnees, afficher_code=partage,
                                       format_code=format_code), partage)
    assert _pixels_noirs(case) > 0
    for x0 in (0, case.width - 3):
        assert _pixels_noirs(case.crop((x0, 0, x0 + 3, case.height))) == 0


def _boite_encre(image):
    """Largeur et hauteur de la tache d'encre (pixels sombres) d'une image."""
    x0, y0, x1, y1 = image.convert("L").point(lambda p: 255 if p < 128 else 0).getbbox()
    return x1 - x0, y1 - y0


def _encre_a_la_taille(texte, taille):
    """L'encre de `texte` dessiné seul, à la taille donnée."""
    image = Image.new("L", (600, 80), 255)
    ImageDraw.Draw(image).text((10, 10), texte, font=_police(taille), fill=0)
    return _boite_encre(image)


@pytest.mark.parametrize("partage", [True, False], ids=["cadre-partage", "cadre-seul"])
def test_le_format_dense_sort_a_sa_taille_d_avant(partage):
    """
    L'ajustement est plafonné à la taille fixe d'avant : un code qui tenait
    sort exactement comme avant, ni plus grand ni plus petit. L'encre est
    comparée à celle du même texte dessiné à la taille d'avant — un point de
    moins la rétrécit déjà d'un pixel au moins.
    """
    if not _truetype():
        pytest.skip("aucune police TrueType : toutes les tailles se valent ici")
    texte = code_classement(MAXIMA, FORMAT_DENSE)
    case = _case_classement(_etiquette(MAXIMA, afficher_code=partage,
                                       format_code=FORMAT_DENSE), partage)
    assert _boite_encre(case) == _encre_a_la_taille(texte, CLASSEMENT_TAILLE_MAX)
    assert _encre_a_la_taille(texte, CLASSEMENT_TAILLE_MAX - 1) != \
        _encre_a_la_taille(texte, CLASSEMENT_TAILLE_MAX)


def test_le_format_lisible_se_reduit_quand_il_le_faut(listes_remplies):
    """Avec lettres, au pire cas, la police cède — c'est l'ajustement qui joue."""
    if not _truetype():
        pytest.skip("aucune police TrueType : toutes les tailles se valent ici")
    donnees = dict(PIRE_CAS_REEL, **LETTRES)
    texte = code_classement(donnees, FORMAT_LISIBLE)
    largeur, _ = _boite_encre(_case_classement(_etiquette(donnees)))
    assert largeur < _encre_a_la_taille(texte, CLASSEMENT_TAILLE_MAX)[0]


def test_un_code_vide_laisse_la_case_blanche():
    """Lisible, rien de connu : aucune encre — ni « ? », ni séparateur orphelin."""
    assert _pixels_noirs(_case_classement(_etiquette({}))) == 0
    # Le même jeu en dense, lui, imprime ses « ? ».
    assert _pixels_noirs(_case_classement(
        _etiquette({}, format_code=FORMAT_DENSE))) > 0


def test_le_format_ne_change_que_le_cadre_du_bas():
    """Ni le QR ni le nom ne bougent d'un pixel d'un format à l'autre."""
    dense = _etiquette(JEU, format_code=FORMAT_DENSE)
    lisible = _etiquette(JEU, format_code=FORMAT_LISIBLE)
    assert dense.size == lisible.size
    au_dessus = (0, 0, dense.width, dense.height - MARGE_EXTERIEURE - CADRE_BAS_H)
    assert dense.crop(au_dessus).tobytes() == lisible.crop(au_dessus).tobytes()
    assert dense.tobytes() != lisible.tobytes()


# ---------------------------------------------------------------------------
# Base, réglage, routes et script
# ---------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Patron de tests/test_etiquette_code.py : trois bases temporaires."""
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
    conn.execute(
        "INSERT INTO titres (reference_titre, nom, age_min, nb_joueurs_min, "
        "nb_joueurs_max, duree_min) VALUES ('CATAN', 'Catan', 8, 2, 4, 30)")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return client


def _conn():
    from app.db import get_connection

    return get_connection()


def _poser_lettres(ref="CATAN", lettres=("T", "P", "C")):
    conn = _conn()
    try:
        conn.execute("UPDATE titres SET lettre_public = ?, lettre_jeu = ?, "
                     "lettre_materiel = ? WHERE reference_titre = ?", (*lettres, ref))
        conn.commit()
    finally:
        conn.close()


def _lettres_en_base(ref="CATAN"):
    conn = _conn()
    try:
        r = conn.execute("SELECT lettre_public, lettre_jeu, lettre_materiel "
                         "FROM titres WHERE reference_titre = ?", (ref,)).fetchone()
        return tuple(r) if r else None
    finally:
        conn.close()


def _radio_coche(html: str) -> str | None:
    """La valeur du bouton radio coché, lue DANS les balises <input>."""
    for balise in re.findall(r"<input[^>]*name=\"format_code\"[^>]*>", html, re.S):
        if "checked" in balise:
            return re.search(r"value=\"([^\"]+)\"", balise).group(1)
    return None


def test_migration_une_base_ancienne_gagne_trois_colonnes_vides(tmp_path, monkeypatch):
    """
    Une base créée avant ce lot : `titres` sans les colonnes de lettres. Elle
    les gagne, vides, et ses étiquettes se génèrent — sans lettres.
    """
    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.execute("""
        CREATE TABLE titres (
            reference_titre TEXT PRIMARY KEY, nom TEXT NOT NULL, type_jeu TEXT,
            categorie TEXT, nb_joueurs_min INTEGER, nb_joueurs_max INTEGER,
            duree_min INTEGER, age_min INTEGER, editeur TEXT, auteur TEXT,
            annee_edition INTEGER, descriptif TEXT, date_achat TEXT)""")
    ancienne.execute("INSERT INTO titres (reference_titre, nom, age_min) "
                     "VALUES ('CATAN', 'Catan', 10)")
    ancienne.commit()
    ancienne.close()

    from app import db

    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(titres)")}
        assert set(classement.COLONNES_LETTRES) <= colonnes
        conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
                     "VALUES ('001', 'CATAN')")
        conn.commit()
        info = services.info_exemplaire(conn, "001")
        assert [info[c] for c in classement.COLONNES_LETTRES] == [None] * 3
        # Init relancée : idempotente.
        db.init_db(conn)
    finally:
        conn.close()
    assert code_classement(info, FORMAT_LISIBLE) == "10+"
    assert image_etiquette(url_fiche("https://exemple.test", "001"), info).size


def test_base_vierge_format_lisible(client):
    conn = _conn()
    try:
        assert services.lire_etiquette_format(conn) == FORMAT_LISIBLE
    finally:
        conn.close()
    assert _radio_coche(client.get("/admin/etiquettes").text) == FORMAT_LISIBLE


def test_valeur_inconnue_en_base_vaut_lisible(client):
    conn = _conn()
    try:
        services.ecrire_parametre(conn, services.CLE_ETIQUETTE_FORMAT, "gothique")
        assert services.lire_etiquette_format(conn) == FORMAT_LISIBLE
    finally:
        conn.close()
    page = client.get("/admin/etiquettes")
    assert page.status_code == 200
    assert _radio_coche(page.text) == FORMAT_LISIBLE


def test_les_exemples_de_l_ecran_sont_rendus_par_le_code(client):
    page = client.get("/admin/etiquettes").text
    for f in FORMATS:
        assert code_classement(classement.EXEMPLE_FORMAT, f) in page
    assert "<legend>Forme du code de classement</legend>" in page


def test_aller_retour_du_format(client):
    r = client.post("/admin/etiquettes/code",
                    data={"afficher_code": "1", "format_code": FORMAT_DENSE},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert _radio_coche(page) == FORMAT_DENSE
    # Le message dit ce qui a changé, exemple rendu à l'appui.
    assert code_classement(classement.EXEMPLE_FORMAT, FORMAT_DENSE) in page
    # Le code de la boîte n'a pas bougé, la case reste cochée.
    conn = _conn()
    try:
        assert services.lire_etiquette_code(conn) is True
        assert services.lire_etiquette_format(conn) == FORMAT_DENSE
    finally:
        conn.close()

    client.post("/admin/etiquettes/code",
                data={"afficher_code": "1", "format_code": FORMAT_LISIBLE})
    assert _radio_coche(client.get("/admin/etiquettes").text) == FORMAT_LISIBLE


@pytest.mark.parametrize("donnees", [{"afficher_code": "1"},
                                     {"afficher_code": "1", "format_code": "gothique"}],
                         ids=["champ-absent", "valeur-inconnue"])
def test_un_vieil_onglet_ne_remet_pas_le_format_au_defaut(client, donnees):
    """
    Une page ouverte avant la mise à jour n'envoie pas le format : il ne doit
    pas revenir à son défaut en silence.
    """
    client.post("/admin/etiquettes/code",
                data={"afficher_code": "1", "format_code": FORMAT_DENSE})
    client.post("/admin/etiquettes/code", data=donnees)
    conn = _conn()
    try:
        assert services.lire_etiquette_format(conn) == FORMAT_DENSE
    finally:
        conn.close()


def test_rien_ne_change_le_message_le_dit(client):
    r = client.post("/admin/etiquettes/code",
                    data={"afficher_code": "1", "format_code": FORMAT_LISIBLE},
                    follow_redirects=False)
    assert "Rien n" in client.get(r.headers["location"]).text


def test_le_changement_de_format_est_journalise(client, _journal_isole):
    def lignes():
        return [l for l in _journal_isole.read_text(encoding="utf-8").splitlines()
                if "etiquette_format_modifie" in l]

    client.post("/admin/etiquettes/code",
                data={"afficher_code": "1", "format_code": FORMAT_LISIBLE})
    assert lignes() == []          # le défaut, réenregistré : rien ne change
    client.post("/admin/etiquettes/code",
                data={"afficher_code": "1", "format_code": FORMAT_DENSE})
    assert len(lignes()) == 1 and FORMAT_DENSE in lignes()[0]
    client.post("/admin/etiquettes/code",
                data={"afficher_code": "1", "format_code": FORMAT_DENSE})
    assert len(lignes()) == 1
    # Changer le format seul n'écrit rien sur le code de la boîte.
    assert "etiquette_code_modifie" not in _journal_isole.read_text(encoding="utf-8")


@pytest.fixture
def espion(monkeypatch):
    """
    Intercepte `image_etiquette` là où chaque producteur l'appelle, et note ce
    qu'il reçoit : le dict du jeu (donc ce que sa requête SQL a lu) et le
    format. Le rendu réel est conservé.
    """
    import app.etiquettes as etiquettes
    import app.routes.admin as admin
    import scripts.generate_qr as generate_qr

    appels = []
    original = etiquettes.image_etiquette

    def image_espionnee(url, ex, *args, **kwargs):
        appels.append((dict(ex), kwargs.get("format_code")))
        return original(url, ex, *args, **kwargs)

    for module in (etiquettes, admin, generate_qr):
        monkeypatch.setattr(module, "image_etiquette", image_espionnee)
    return appels


def _verifier(appels, format_code):
    assert appels, "le producteur n'a dessiné aucune étiquette"
    for ex, fmt in appels:
        assert fmt == format_code
        assert code_classement(ex, fmt).startswith("TPC")


@pytest.mark.parametrize("format_code", FORMATS)
def test_les_trois_producteurs_transmettent_lettres_et_format(
        client, listes_remplies, espion, tmp_path, format_code):
    """
    LE test de bout en bout : un triplet posé en base sort sur l'étiquette par
    les trois producteurs, dans le format enregistré.
    """
    _poser_lettres()
    client.post("/admin/etiquettes/code",
                data={"afficher_code": "1", "format_code": format_code})

    # 1. Réimpression d'une étiquette (route PNG) — services.info_exemplaire.
    assert client.get("/admin/etiquette/001.png").status_code == 200
    _verifier(espion, format_code)

    # 2. Planche en lot (route PDF) — services.exemplaires_pour_etiquettes.
    espion.clear()
    assert client.post("/admin/etiquettes/pdf",
                       data={"references": "CATAN"}).content[:4] == b"%PDF"
    _verifier(espion, format_code)

    # 3. Script CLI — sa propre requête SQL, et le réglage lu en base.
    from scripts.generate_qr import (charger_exemplaires, generer_pngs,
                                     lire_reglage_format)

    espion.clear()
    assert lire_reglage_format() == format_code
    generer_pngs(charger_exemplaires(), "https://exemple.test", tmp_path / "qr",
                 None, False, format_code=lire_reglage_format())
    _verifier(espion, format_code)


def test_la_route_png_imprime_les_lettres(client, listes_remplies):
    """Le même rendu avec et sans lettres en base : seul le cadre du bas change."""
    sans = Image.open(io.BytesIO(client.get("/admin/etiquette/001.png").content))
    _poser_lettres()
    avec = Image.open(io.BytesIO(client.get("/admin/etiquette/001.png").content))
    assert avec.size == sans.size
    assert avec.tobytes() != sans.tobytes()
    assert _pixels_noirs(_case_classement(avec)) != _pixels_noirs(_case_classement(sans))


def test_le_script_force_un_format_sans_rien_ecrire(client, espion, tmp_path,
                                                    monkeypatch):
    import sys

    from scripts import generate_qr

    monkeypatch.setattr(sys, "argv", [
        "generate_qr", "--base-url", "https://exemple.test",
        "--out", str(tmp_path / "qr"), "--format", FORMAT_DENSE])
    generate_qr.main()
    assert espion and all(fmt == FORMAT_DENSE for _, fmt in espion)
    assert generate_qr.lire_reglage_format() == FORMAT_LISIBLE


# ---------------------------------------------------------------------------
# Import et export CSV
# ---------------------------------------------------------------------------
def _importer(tmp_path, contenu: str):
    from scripts import import_csv

    chemin = tmp_path / "cat.csv"
    chemin.write_text(contenu, encoding="utf-8")
    return import_csv.importer(chemin)


ENTETE = "Code jeu;Nom jeu;Age joueurs"


def test_import_colonne_absente_lettres_intactes(client, tmp_path):
    _poser_lettres()
    res = _importer(tmp_path, f"{ENTETE}\n001;Catan;12\n")
    assert _lettres_en_base() == ("T", "P", "C")
    assert res["lettres_refusees"] == 0


def test_import_case_vide_lettres_intactes(client, tmp_path):
    _poser_lettres()
    res = _importer(tmp_path, f"{ENTETE};Lettres classement\n001;Catan;12;\n")
    assert _lettres_en_base() == ("T", "P", "C")
    assert res["lettres_refusees"] == 0


def test_import_valeur_invalide_ligne_importee_lettres_intactes(client, tmp_path):
    """Listes 2 et 3 vides : toute valeur est invalide, c'est attendu."""
    _poser_lettres()
    res = _importer(tmp_path, f"{ENTETE};Lettres classement\n"
                              "001;Catan;12;TPC\n002;Dobble;6;zz\n")
    assert _lettres_en_base() == ("T", "P", "C")
    assert _lettres_en_base("DOBBLE") == (None, None, None)
    assert res["exemplaires"] == 2 and not res["ignores"]
    assert res["lettres_refusees"] == 2
    # La ligne est importée : son âge, lui, a bien été mis à jour.
    conn = _conn()
    try:
        assert conn.execute("SELECT age_min FROM titres WHERE reference_titre "
                            "= 'CATAN'").fetchone()[0] == 12
    finally:
        conn.close()


def test_import_valeur_valide_ecrite(client, tmp_path, listes_remplies):
    res = _importer(tmp_path, f"{ENTETE};Lettres classement\n001;Catan;12; tpc \n")
    assert _lettres_en_base() == ("T", "P", "C")
    assert res["lettres_refusees"] == 0


def test_import_un_titre_prend_un_triplet_entier(client, tmp_path, listes_remplies):
    """Deux exemplaires d'un même titre : le premier triplet valide, en entier."""
    _importer(tmp_path, f"{ENTETE};Lettres classement\n"
                        "001;Catan;12;\n002;Catan;12;EPC\n003;Catan;12;XPC\n")
    assert _lettres_en_base() == ("E", "P", "C")


def test_le_message_d_import_compte_les_lettres_refusees(client):
    contenu = ("Code jeu;Nom jeu;Lettres classement\n"
               "001;Catan;TPC\n").encode("utf-8")
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("cat.csv", contenu, "text/csv")})
    assert r.status_code == 200
    assert "Import réussi" in r.text
    assert "Lettres de classement non reconnues sur 1 ligne(s)" in r.text
    assert "normal tant que la classification automatique" in r.text


def test_export_lettres_imprimables_seulement(client, listes_remplies):
    _, lignes = services.lignes_export_catalogue(_conn())
    assert lignes[0]["Lettres classement"] == ""
    _poser_lettres(lettres=("T", "P", None))
    _, lignes = services.lignes_export_catalogue(_conn())
    assert lignes[0]["Lettres classement"] == ""
    _poser_lettres()
    _, lignes = services.lignes_export_catalogue(_conn())
    assert lignes[0]["Lettres classement"] == "TPC"


def test_la_colonne_d_export_est_en_fin_d_en_tete():
    assert services.EN_TETES_CATALOGUE[-1] == "Lettres classement"


def test_export_puis_import_sans_perte(client, tmp_path, listes_remplies,
                                       monkeypatch):
    """L'export se ré-importe tel quel dans une base neuve, lettres comprises."""
    from app import db, exports

    _poser_lettres()
    conn = _conn()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    chemin = tmp_path / "export.csv"
    chemin.write_bytes(exports.catalogue_csv(entetes, lignes))

    neuve = tmp_path / "neuve.db"
    monkeypatch.setattr(db, "get_database_path", lambda: neuve)
    from scripts import import_csv

    res = import_csv.importer(chemin)
    assert res["lettres_refusees"] == 0
    assert _lettres_en_base() == ("T", "P", "C")
