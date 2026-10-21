"""
L'image de la boîte sur la fiche PUBLIQUE (série « images », lot 2).

Une case de /admin/identite, décochée par défaut, ouvre au public la copie
locale de la vignette BGG (`/image/bgg/<n>.webp`) et l'affiche sur la fiche
d'un jeu relié, avec la mention « Image : BoardGameGeek ». Case décochée :
le comportement d'avant, à l'octet près pour le public.

Le lot 1 (copie, contrôle, route réservée) est couvert par
tests/test_bgg_images.py ; ce fichier n'en teste que l'ouverture.
"""

import re

import pytest

from app import journal, services, vignettes
from app.routes import images
from tests.test_bgg_images import JPEG, _adresse, _relier_catane
from tests.test_bgg_relier import _copier
from tests.test_bgg_vignette import VIGNETTE
from tests.test_fiche_jeu import _conn, _executer, _journal, anonyme, bases, client  # noqa: F401

PAGES_PUBLIQUES = ("/jeu/001", "/jeu/002", "/catalogue", "/catalogue?q=Catane",
                   "/apropos", "/")
CASE = r'<input type="checkbox" id="bgg_images_publiques"[^>]*>'
LEGENDE = "Image : BoardGameGeek"


def _cocher(valeur=True):
    conn = _conn()
    try:
        services.ecrire_bgg_images_publiques(conn, valeur)
    finally:
        conn.close()


def _catane_avec_copie(adresse=VIGNETTE):
    _relier_catane(adresse)
    vignettes.enregistrer(13, adresse, JPEG)


def _figures(page):
    return re.findall(r'<figure class="figure-bgg">.*?</figure>', page, re.S)


def _attribut(balise, nom):
    return re.search(rf'\b{nom}="([^"]*)"', balise).group(1)


# ===========================================================================
# 1. LE RÉGLAGE
# ===========================================================================
def test_la_case_est_decochee_par_defaut(client):
    conn = _conn()
    try:
        assert not services.lire_bgg_images_publiques(conn)
        assert services.lire_parametre(conn, services.CLE_BGG_IMAGES_PUBLIQUES) is None
    finally:
        conn.close()
    page = client.get("/admin/identite").text
    case = re.search(CASE, page).group(0)
    assert "checked" not in case
    assert 'aria-describedby="bgg_images_publiques-aide"' in case
    assert "Afficher l'image de la boîte sur les fiches publiques" in page
    aide = re.search(r'<small[^>]*id="bgg_images_publiques-aide"[^>]*>(.*?)</small>',
                     page, re.S).group(1)
    assert "copiée sur ce site" in aide and f"« {LEGENDE} »" in aide


def test_cocher_enregistre_rouvre_cochee_et_journalise_au_changement(client, _journal_isole):
    r = client.post("/admin/identite", data={"bgg_images_publiques": "1"})
    assert r.status_code == 200
    assert "Image de la boîte affichée sur les fiches publiques." in r.text
    assert "checked" in re.search(CASE, r.text).group(0)
    ligne, = _journal(_journal_isole, "bgg_images_publiques_modifie")
    assert ligne["objet"] == "affichées"
    # Rouvert : l'état enregistré, pas un repli.
    assert "checked" in re.search(CASE, client.get("/admin/identite").text).group(0)
    # Réenregistré sans changement : aucune ligne de plus.
    client.post("/admin/identite", data={"bgg_images_publiques": "1"})
    assert len(_journal(_journal_isole, "bgg_images_publiques_modifie")) == 1
    # Les deux cases sont indépendantes.
    conn = _conn()
    try:
        assert services.lire_bgg_images_publiques(conn)
        assert not services.lire_bgg_lien_public(conn)
    finally:
        conn.close()


def test_decocher_masque_et_journalise(client, _journal_isole):
    client.post("/admin/identite", data={"bgg_images_publiques": "1"})
    r = client.post("/admin/identite", data={})
    assert "Image de la boîte retirée des fiches publiques." in r.text
    assert "checked" not in re.search(CASE, r.text).group(0)
    assert _journal(_journal_isole, "bgg_images_publiques_modifie")[-1]["objet"] == "masquées"
    conn = _conn()
    try:
        # Décochée = clé effacée : la base revient à l'état d'avant.
        assert services.lire_parametre(conn, services.CLE_BGG_IMAGES_PUBLIQUES) is None
    finally:
        conn.close()


def test_jamais_coche_rien_n_est_dit_ni_journalise(client, _journal_isole):
    r = client.post("/admin/identite", data={})
    assert "Image de la boîte" not in r.text
    assert _journal(_journal_isole, "bgg_images_publiques_modifie") == []


def test_un_refus_garde_la_case_sans_rien_enregistrer(client):
    r = client.post("/admin/identite",
                    data={"bgg_images_publiques": "1", "contact": "pas une adresse"})
    assert r.status_code == 400
    assert "checked" in re.search(CASE, r.text).group(0)
    conn = _conn()
    try:
        assert not services.lire_bgg_images_publiques(conn)
    finally:
        conn.close()


def test_l_action_est_au_vocabulaire_et_a_la_conception():
    from pathlib import Path
    assert "bgg_images_publiques_modifie" in journal.ACTIONS
    conception = Path("docs/conception-journal.md").read_text(encoding="utf-8")
    assert "bgg_images_publiques_modifie" in conception


# ===========================================================================
# 2. LA ROUTE
# ===========================================================================
def test_case_decochee_la_route_reste_fermee_au_public(anonyme):
    _catane_avec_copie()
    for adresse in (vignettes.url(13, VIGNETTE), "/image/bgg/13.webp"):
        r = anonyme.get(adresse)
        assert r.status_code == 404 and r.content == b"", adresse


def test_case_cochee_la_route_sert_le_public_en_cache_public(anonyme):
    _catane_avec_copie()
    _cocher()
    r = anonyme.get(vignettes.url(13, VIGNETTE))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == images.CACHE_VIGNETTE_PUBLIQUE
    assert images.CACHE_VIGNETTE_PUBLIQUE.startswith("public,")
    assert r.content == vignettes.chemin_servi(13, VIGNETTE).read_bytes()


def test_l_administration_case_decochee_garde_un_cache_prive(client):
    _catane_avec_copie()
    r = client.get(vignettes.url(13, VIGNETTE))
    assert r.status_code == 200
    assert r.headers["cache-control"] == images.CACHE_VIGNETTE
    assert images.CACHE_VIGNETTE.startswith("private,")


def test_l_administration_case_cochee_recoit_la_reponse_publique(client):
    _catane_avec_copie()
    _cocher()
    assert client.get(vignettes.url(13, VIGNETTE)).headers["cache-control"] == \
        images.CACHE_VIGNETTE_PUBLIQUE


def test_decocher_referme_la_route_aussitot(anonyme):
    _catane_avec_copie()
    _cocher()
    assert anonyme.get("/image/bgg/13.webp").status_code == 200
    _cocher(False)
    assert anonyme.get("/image/bgg/13.webp").status_code == 404


@pytest.mark.parametrize("chemin", [
    "/image/bgg/14.webp",                      # fiche sans copie
    "/image/bgg/0.webp", "/image/bgg/013.webp", "/image/bgg/abc.webp",
    "/image/bgg/-13.webp", "/image/bgg/13.png", "/image/bgg/12345678901.webp",
    "/image/bgg/13%2F..%2F..%2Ftest.db.webp",
])
def test_case_cochee_un_numero_invalide_ou_sans_copie_rend_un_404_nu(anonyme, chemin):
    _catane_avec_copie()
    _cocher()
    r = anonyme.get(chemin)
    # Deux de ces chemins ne sont même pas ceux de la route (`.png`, `%2F`) :
    # c'est la page « introuvable » du site qui répond, jamais une image.
    assert r.status_code == 404 and b"SQLite" not in r.content
    assert "image" not in r.headers.get("content-type", "")


def test_case_cochee_une_copie_d_une_ancienne_adresse_n_est_pas_servie(anonyme):
    _relier_catane(_adresse(2))
    vignettes.enregistrer(13, _adresse(1), JPEG)      # copie d'une autre adresse
    _cocher()
    assert anonyme.get("/image/bgg/13.webp").status_code == 404


# ===========================================================================
# 3. LA FICHE PUBLIQUE
# ===========================================================================
def test_case_decochee_aucune_image_ni_balise(anonyme):
    _catane_avec_copie()
    page = anonyme.get("/jeu/001").text
    assert _figures(page) == []
    for trace in ("/image/bgg/", "vignette-bgg", "figure-bgg", LEGENDE, "<figure"):
        assert trace not in page, trace


def test_case_cochee_image_et_legende(anonyme):
    _catane_avec_copie()
    _cocher()
    page = anonyme.get("/jeu/001").text
    figure, = _figures(page)
    img = re.search(r"<img\b[^>]*>", figure).group(0)
    assert _attribut(img, "class") == "vignette-bgg"
    assert _attribut(img, "src").replace("&amp;", "&") == vignettes.url(13, VIGNETTE)
    # Notre nom (celui du titre de la page), pas le nom BGG.
    assert _attribut(img, "alt") == "Boîte de Catane"
    assert (_attribut(img, "width"), _attribut(img, "height")) == ("200", "150")
    assert _attribut(img, "loading") == "lazy"
    assert _attribut(img, "decoding") == "async"
    assert re.search(r'<figcaption class="stats-note">\s*Image : BoardGameGeek\s*</figcaption>',
                     figure)
    # Les deux boîtes du même titre ont la même image.
    assert _figures(anonyme.get("/jeu/002").text) == [figure]


def test_case_cochee_l_image_vient_apres_le_bouton_de_pret(client):
    """Ordre de lecture : titre, état, bouton, puis image et caractéristiques."""
    _catane_avec_copie()
    _cocher()
    page = client.get("/jeu/001").text
    assert "Prêter / rendre ce jeu" in page        # la session d'administration est bénévole
    assert (page.index("<h1>") < page.index('class="etat ')
            < page.index("Prêter / rendre ce jeu") < page.index('<figure class="figure-bgg">')
            < page.index('<dl class="caracteristiques">'))


def test_un_jeu_non_relie_n_a_pas_d_image(anonyme):
    _catane_avec_copie()
    _cocher()
    page = anonyme.get("/jeu/003").text             # Dobble, sans fiche BGG
    assert "<figure" not in page and LEGENDE not in page


def test_un_jeu_relie_sans_copie_n_a_pas_d_image(anonyme):
    _relier_catane()                                 # fiche avec adresse, pas de copie
    _cocher()
    page = anonyme.get("/jeu/001").text
    assert "<figure" not in page and "/image/bgg/" not in page and "geekdo" not in page


@pytest.mark.parametrize("vignette", [None, ""])
def test_une_fiche_sans_image_chez_bgg_n_a_pas_d_image(anonyme, vignette):
    _relier_catane(vignette)
    _cocher()
    page = anonyme.get("/jeu/001").text
    assert "<figure" not in page and LEGENDE not in page


def test_une_fiche_inconnue_de_bgg_n_a_pas_d_image(anonyme):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _copier({13: None})
    _cocher()
    assert "<figure" not in anonyme.get("/jeu/001").text


def test_une_copie_d_une_autre_adresse_n_est_pas_affichee(anonyme):
    """Empreinte périmée : BGG a changé l'adresse, la copie n'est pas encore refaite."""
    _relier_catane(_adresse(2))
    vignettes.enregistrer(13, _adresse(1), JPEG)
    _cocher()
    page = anonyme.get("/jeu/001").text
    assert "<figure" not in page and "/image/bgg/" not in page


def test_le_nom_est_echappe_dans_l_alt(anonyme):
    _executer("UPDATE titres SET nom = '<b>x</b>\" onerror=\"alert(1)' "
              "WHERE reference_titre = 'CATANE'")
    _catane_avec_copie()
    _cocher()
    page = anonyme.get("/jeu/001").text
    img = re.search(r"<img\b[^>]*>", _figures(page)[0]).group(0)
    assert "<b>" not in img and 'onerror="' not in img
    assert "&lt;b&gt;x&lt;/b&gt;" in img


def test_un_dossier_illisible_ne_bloque_pas_la_fiche(anonyme, monkeypatch):
    _catane_avec_copie()
    _cocher()

    def illisible():
        raise PermissionError("dossier illisible")
    monkeypatch.setattr(vignettes, "dossier", illisible)
    page = anonyme.get("/jeu/001")
    assert page.status_code == 200 and "<figure" not in page.text


# ===========================================================================
# 4. GARDE-FOUS PUBLICS
# ===========================================================================
@pytest.mark.parametrize("cochee", [False, True])
def test_jamais_geekdo_sur_une_page_publique(anonyme, cochee):
    _catane_avec_copie()
    conn = _conn()
    try:
        services.ecrire_bgg_lien_public(conn, True)
    finally:
        conn.close()
    _cocher(cochee)
    for url in PAGES_PUBLIQUES:
        page = anonyme.get(url)
        assert page.status_code == 200, url
        assert "geekdo" not in page.text, url
    # Les cartes du catalogue n'ont pas d'image dans ce lot (fiche d'abord).
    for url in ("/catalogue", "/catalogue?q=Catane", "/apropos", "/"):
        assert "/image/bgg/" not in anonyme.get(url).text, url


def test_case_decochee_les_pages_publiques_sont_identiques_a_avant(anonyme):
    """Copie présente ou non, case décochée : la fiche publique ne change pas d'un octet."""
    _relier_catane()
    sans_copie = anonyme.get("/jeu/001").text
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    assert anonyme.get("/jeu/001").text == sans_copie


def test_a_propos_mentionne_bgg_avec_la_seule_case_des_images(anonyme):
    assert "Powered by BGG" not in anonyme.get("/apropos").text
    _cocher()
    assert "Powered by BGG" in anonyme.get("/apropos").text


# ===========================================================================
# 5. LA MACRO PARTAGÉE
# ===========================================================================
def test_l_administration_rend_toujours_la_copie_sans_figure(client):
    _catane_avec_copie()
    _cocher()                                       # la case ne change rien en administration
    page = client.get("/admin/jeu/CATANE").text
    img, = [i for i in re.findall(r"<img\b[^>]*>", page, re.S) if 'class="vignette-bgg"' in i]
    assert _attribut(img, "src").replace("&amp;", "&") == vignettes.url(13, VIGNETTE)
    assert _attribut(img, "alt") == "Boîte de CATAN"     # le nom BGG, comme avant
    assert "<figure" not in page and LEGENDE not in page


def test_une_seule_macro_pour_les_deux_pages():
    from pathlib import Path
    gabarits = Path("app/templates")
    definitions = [p.name for p in gabarits.glob("*.html")
                   if "macro vignette_bgg" in p.read_text(encoding="utf-8")]
    assert definitions == ["_vignette_bgg.html"]
    for nom in ("fiche.html", "admin_fiche.html"):
        assert '{% from "_vignette_bgg.html" import vignette_bgg %}' in \
            (gabarits / nom).read_text(encoding="utf-8"), nom


def test_la_figure_n_a_aucune_couleur_en_dur():
    css = open("app/static/css/style.css", encoding="utf-8").read()
    for regle in re.findall(r"\.figure-bgg[^{]*\{(.*?)\}", css, re.S):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", regle)
