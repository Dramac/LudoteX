"""
LA VIGNETTE DE LA BOÎTE BOARDGAMEGEEK, EN ADMINISTRATION (lot fiche-4).

Ce que ce fichier vérifie, et pourquoi :

1. **Le contrôle de l'adresse** (`bgg.lire_vignette`) : une adresse venue de
   BGG n'entre dans une page qu'en `https:`, sur un hôte EXACTEMENT dans
   `bgg.HOTES_VIGNETTE`, bornée en longueur. Sinon : pas de vignette, jamais
   une erreur.
2. **La lecture d'une réponse `thing`** : `<thumbnail>` présente, absente,
   refusée — et `<image>` (pleine taille) jamais lue.
3. **Les trois états de `fiches_bgg.vignette`** : NULL (jamais lue), '' (lue
   sans image), adresse ; et la sélection de « Récupérer les données BGG
   manquantes » qui en découle — une copie jamais lue est reprise, une copie
   lue sans image ne l'est pas, le compte tombe à zéro.
4. **Les trois chemins qui complètent une copie** : le lot de la liste, le
   bouton d'une fiche, une recherche.
5. **L'affichage** : bloc « Données BGG du … » (la COPIE LOCALE depuis la
   série « images », lot 1 : tests/test_bgg_images.py) et cartes de
   candidats (toujours l'adresse BGG), attributs `loading`,
   `referrerpolicy`, `alt`, dimensions ; une adresse mauvaise en base ne
   sort pas ; ordre de lecture des cartes.
6. **Aucune vignette sur une page publique**, et **aucun appel réseau à
   l'affichage**.
7. **Une base d'avant** gagne la colonne vide, et rien ne s'affiche.

La CSP nginx qui laisse passer ces images (administration seulement) est
verrouillée dans tests/test_nginx_configuration.py.
"""

import re
import sqlite3

import pytest

from app import bgg, bgg_api, services, vignettes
from tests.test_bgg_relier import (XML_SEARCH_BAZAR, _copier, _ou, _texte,  # noqa: F401
                                   admin, bgg_interdit, catalogue, faux_bgg)
from tests.test_fiche_jeu import (_conn, _executer, anonyme, bases,  # noqa: F401
                                  client)

# Une vraie adresse de vignette, relevée dans le cache de la collecte du
# 2026-10-10 (hors dépôt) : la forme exacte que BGG envoie.
VIGNETTE = ("https://cf.geekdo-images.com/d6GQ1hc_oxqx0c7WFXwpcQ__small/img/"
            "AuWDIvtb2XFxxWX0lTrBWDEO17Q=/fit-in/200x150/filters:strip_icc()/pic2403582.jpg")
IMAGE = ("https://cf.geekdo-images.com/d6GQ1hc_oxqx0c7WFXwpcQ__original/img/"
         "x=/0x0/filters:format(jpeg)/pic2403582.jpg")


def _xml(*items: str) -> bytes:
    return ('<?xml version="1.0" encoding="utf-8"?><items termsofuse="x">'
            + "".join(items) + "</items>").encode()


def _item(bgg_id, nom, vignette=None, image=None):
    """Un `<item>` RÉDUIT, sur la forme d'une réponse du cache."""
    morceaux = [f'<item type="boardgame" id="{bgg_id}">']
    if vignette is not None:
        morceaux.append(f"<thumbnail>{vignette}</thumbnail>")
    if image is not None:
        morceaux.append(f"<image>{image}</image>")
    morceaux.append(f'<name type="primary" sortindex="1" value="{nom}"/>'
                    '<minplayers value="2"/><maxplayers value="4"/></item>')
    return "".join(morceaux)


def _vignette_en_base(bgg_id):
    conn = _conn()
    try:
        ligne = conn.execute("SELECT vignette FROM fiches_bgg WHERE bgg_id = ?",
                             (bgg_id,)).fetchone()
        return ligne[0] if ligne else "absente"
    finally:
        conn.close()


def _a_recuperer():
    conn = _conn()
    try:
        return services.ids_bgg_a_recuperer(conn)
    finally:
        conn.close()


def _images(page):
    return re.findall(r"<img\b[^>]*>", page, re.S)


def _vignettes(page):
    return [i for i in _images(page) if 'class="vignette-bgg"' in i]


def _jpeg():
    """Une petite image JPEG, pour les copies locales (série « images »)."""
    from tests.conftest import _jpeg_minimal
    return _jpeg_minimal()


def _attribut(balise, nom):
    trouve = re.search(r"\b" + nom + r'="([^"]*)"', balise)
    return trouve.group(1) if trouve else None


# ===========================================================================
# 1. LE CONTRÔLE DE L'ADRESSE — fonction pure
# ===========================================================================
def test_une_vraie_adresse_de_bgg_est_acceptee_telle_quelle():
    assert bgg.lire_vignette(VIGNETTE) == VIGNETTE
    assert bgg.lire_vignette(f"\n  {VIGNETTE}\n ") == VIGNETTE


@pytest.mark.parametrize("adresse", [
    None, "", "   ",
    VIGNETTE.replace("https:", "http:"),                       # en clair
    VIGNETTE.replace("cf.geekdo-images.com", "images.example.org"),  # autre hôte
    VIGNETTE.replace("cf.geekdo-images.com", "x.cf.geekdo-images.com"),  # sous-domaine
    VIGNETTE.replace("cf.geekdo-images.com", "cf.geekdo-images.com.example.org"),
    VIGNETTE.replace("cf.geekdo-images.com", "cf.geekdo-images.com:8443"),  # port
    VIGNETTE.replace("https://", "https://moi@"),              # nom d'utilisateur
    "https://cf.geekdo-images.com@images.example.org/pic.jpg",
    "https://cf.geekdo-images.com",                            # aucun chemin
    "//cf.geekdo-images.com/pic.jpg",                          # sans schéma
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    'https://cf.geekdo-images.com/pic.jpg" onerror="alert(1)',  # sortie d'attribut
    "https://cf.geekdo-images.com/pic 1.jpg",                  # espace
    "https://cf.geekdo-images.com/<pic>.jpg",
    "https://cf.geekdo-images.com/" + "a" * bgg.LONGUEUR_VIGNETTE_MAX,  # démesurée
])
def test_toute_autre_adresse_donne_pas_de_vignette_sans_erreur(adresse):
    assert bgg.lire_vignette(adresse) == ""


def test_la_longueur_est_bornee_au_caractere_pres():
    debut = "https://cf.geekdo-images.com/"
    juste = debut + "a" * (bgg.LONGUEUR_VIGNETTE_MAX - len(debut))
    assert bgg.lire_vignette(juste) == juste
    assert bgg.lire_vignette(juste + "a") == ""


def test_la_borne_laisse_de_la_marge_aux_vraies_adresses():
    # 141 caractères au plus sur les 821 vignettes du cache (2026-10-14).
    assert len(VIGNETTE) <= 141 < bgg.LONGUEUR_VIGNETTE_MAX


# ===========================================================================
# 2. LA LECTURE D'UNE RÉPONSE `thing`
# ===========================================================================
def test_la_vignette_est_lue_et_la_grande_image_jamais():
    fiche = bgg_api.lire_reponse(_xml(_item(13, "CATAN", VIGNETTE, IMAGE)))[13]
    assert fiche["vignette"] == VIGNETTE
    assert IMAGE not in repr(fiche)


@pytest.mark.parametrize("vignette", [
    None,                                                      # absente
    "",                                                        # vide
    VIGNETTE.replace("https:", "http:"),
    VIGNETTE.replace("cf.geekdo-images.com", "images.example.org"),
    "https://cf.geekdo-images.com/" + "a" * 500,
])
def test_vignette_absente_ou_refusee_lue_sans_image(vignette):
    fiche = bgg_api.lire_reponse(_xml(_item(13, "CATAN", vignette)))[13]
    # Chaîne vide, jamais None : None veut dire « jamais lue » en base.
    assert fiche["vignette"] == ""
    # Le reste de la fiche est lu comme d'habitude.
    assert fiche["nom"] == "CATAN" and fiche["nb_joueurs_max"] == 4


def test_une_vignette_refusee_n_empeche_pas_les_autres_fiches():
    fiches = bgg_api.lire_reponse(_xml(
        _item(13, "CATAN", "http://cf.geekdo-images.com/a.jpg"),
        _item(14, "Dobble", VIGNETTE)))
    assert fiches[13]["vignette"] == "" and fiches[14]["vignette"] == VIGNETTE


# ===========================================================================
# 3. LES TROIS ÉTATS EN BASE, ET LA SÉLECTION DU LOT
# ===========================================================================
def test_trois_etats_distincts_en_base(bases):
    _copier({
        1: bgg_api.lire_reponse(_xml(_item(1, "Avec", VIGNETTE)))[1],
        2: bgg_api.lire_reponse(_xml(_item(2, "Sans")))[2],
        3: {"nom": "Copie d'avant le lot"},         # aucune clé « vignette »
        4: None,                                    # inconnue de BGG
    })
    assert _vignette_en_base(1) == VIGNETTE
    assert _vignette_en_base(2) == ""
    assert _vignette_en_base(3) is None
    assert _vignette_en_base(4) is None


def test_la_selection_reprend_les_copies_jamais_lues_seulement(bases):
    # Six fiches reliées, une par titre.
    for i in range(1, 7):
        _executer("INSERT INTO titres (reference_titre, nom, bgg_id) VALUES (?, ?, ?)",
                  (f"J{i}", f"Jeu {i}", i))
    _copier({
        2: {"nom": "Jamais lue"},                                       # NULL : reprise
        3: bgg_api.lire_reponse(_xml(_item(3, "Lue sans image")))[3],    # '' : non
        4: bgg_api.lire_reponse(_xml(_item(4, "Lue", VIGNETTE)))[4],     # adresse : non
        5: None,                                                         # inconnue : non
        6: {"nom": "Jamais lue aussi"},                                  # NULL : reprise
    })
    # 1 n'a aucune copie : elle passe AVANT les copies à compléter.
    assert _a_recuperer() == [1, 2, 6]


def test_deux_titres_sur_une_fiche_ne_la_comptent_qu_une_fois(bases):
    _executer("UPDATE titres SET bgg_id = 13")
    _copier({13: {"nom": "CATAN"}})
    assert _a_recuperer() == [13]


def test_le_lot_complete_les_copies_et_le_compte_tombe_a_zero(admin, faux_bgg, fausses_images,
                                                              monkeypatch):
    monkeypatch.setattr(bgg_api, "PAUSE_ENTRE_APPELS_S", 0)
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _executer("UPDATE titres SET bgg_id = 14 WHERE reference_titre = 'DOBBLE'")
    _copier({13: {"nom": "CATAN"}, 14: {"nom": "Dobble"}})      # copies d'avant
    page = _texte(admin.get("/admin/jeux").text)
    assert ("2 fiches BoardGameGeek reliées à vos jeux n'ont pas encore de données "
            "BGG, ou pas encore l'image de la boîte.") in page

    # BGG donne une image à l'une, aucune à l'autre.
    faux = faux_bgg(_xml(_item(13, "CATAN", VIGNETTE), _item(14, "Dobble")))
    images = fausses_images()
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    # Dans le même clic, l'image de la seule fiche qui en a une est copiée.
    assert _ou(r) == "/admin/jeux?bgg_lot=2&bgg_inconnues=0&bgg_images=1"
    assert "id=13,14" in faux.requetes[0].full_url
    assert _vignette_en_base(13) == VIGNETTE and _vignette_en_base(14) == ""
    assert images.adresses() == [VIGNETTE]
    page = _texte(admin.get(_ou(r)).text)
    assert "2 fiches récupérées, 1 image de boîte copiée, 0 restante." in page
    assert "Tous les jeux reliés à BoardGameGeek ont leurs données BGG." in page
    assert _a_recuperer() == []
    # La copie lue sans image n'est jamais redemandée.
    assert "Données BoardGameGeek" not in admin.get("/admin/jeux").text


def test_le_bouton_d_une_fiche_complete_sa_copie(client, faux_bgg, fausses_images):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _copier({13: {"nom": "CATAN"}})
    faux_bgg(_xml(_item(13, "CATAN", VIGNETTE)))
    fausses_images()
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert _ou(r) == "/admin/jeu/CATANE?ok=bgg_recupere&bgg=completer#modifier"
    assert _vignette_en_base(13) == VIGNETTE
    assert vignettes.url(13, VIGNETTE) is not None             # et l'image copiée


def test_une_recherche_garde_la_vignette_de_chaque_candidat(admin, faux_bgg):
    faux_bgg(XML_SEARCH_BAZAR, _xml(
        _item(83195, "Ghost Blitz", VIGNETTE),
        _item(118705, "Ghost Blitz 2.0"),
        _item(244702, "Ghost Blitz Junior", "http://cf.geekdo-images.com/x.jpg")))
    r = admin.post("/admin/jeu/BAZAR/bgg/chercher", data={"nom": "Bazar bizarre"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert _vignette_en_base(83195) == VIGNETTE
    assert _vignette_en_base(118705) == ""
    assert _vignette_en_base(244702) == ""


# ===========================================================================
# 4. L'AFFICHAGE, EN ADMINISTRATION
# ===========================================================================
def _relier_catane(vignette, *, copiee=True):
    """Catane reliée à la fiche 13 ; son image copiée si l'adresse le permet."""
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _copier({13: {"nom": "CATAN", "nb_joueurs_min": 3, "vignette": vignette}})
    if copiee and bgg.lire_vignette(vignette):
        vignettes.enregistrer(13, vignette, _jpeg())


def test_le_bloc_des_donnees_bgg_montre_la_vignette(client):
    _relier_catane(VIGNETTE)
    page = client.get("/admin/jeu/CATANE").text
    balise, = _vignettes(page)
    # La copie locale, depuis la série « images » : plus rien chez BGG.
    assert _attribut(balise, "src") == vignettes.url(13, VIGNETTE)
    assert "geekdo" not in page
    assert _attribut(balise, "loading") == "lazy"
    assert _attribut(balise, "referrerpolicy") == "no-referrer"
    assert (_attribut(balise, "width"), _attribut(balise, "height")) == ("200", "150")
    # L'alt dit quel jeu ; le nom BGG reste écrit ailleurs sur la page.
    assert _attribut(balise, "alt") == "Boîte de CATAN"
    # Dans le bloc « Données BGG du … », avant ses boutons.
    bloc = page[page.index('<div class="bloc-bgg">'):page.index('<div class="actions-bgg">',
                                                                 page.index('<div class="bloc-bgg">'))]
    assert "Données BGG du" in bloc and 'class="vignette-bgg"' in bloc


def test_un_alt_sans_nom_bgg_dit_le_numero(client):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _copier({13: {"nom": None, "vignette": VIGNETTE}})
    vignettes.enregistrer(13, VIGNETTE, _jpeg())
    balise, = _vignettes(client.get("/admin/jeu/CATANE").text)
    assert _attribut(balise, "alt") == "Boîte de la fiche n° 13"


@pytest.mark.parametrize("vignette", [None, ""])
def test_sans_vignette_aucune_image_et_la_fiche_reste_la_meme(client, vignette):
    _relier_catane(vignette)
    page = client.get("/admin/jeu/CATANE").text
    assert _vignettes(page) == [] and "geekdo" not in page
    assert "Données BGG du" in page


@pytest.mark.parametrize("mauvaise", [
    "http://cf.geekdo-images.com/pic.jpg",
    "https://images.example.org/pic.jpg",
    'https://cf.geekdo-images.com/a.jpg" onerror="alert(1)',
])
def test_une_adresse_mauvaise_en_base_ne_sort_pas(client, mauvaise):
    # Une archive restaurée, ou un hôte retiré depuis de la liste : l'adresse
    # repasse par le contrôle à la lecture.
    _relier_catane(mauvaise)
    page = client.get("/admin/jeu/CATANE").text
    assert _vignettes(page) == []
    assert "images.example.org" not in page and "onerror" not in page


def test_une_fiche_inconnue_de_bgg_n_a_pas_de_vignette(client):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _copier({13: None})
    assert _vignettes(client.get("/admin/jeu/CATANE").text) == []


def test_chaque_carte_de_candidat_montre_sa_vignette(admin):
    _copier(bgg_api.lire_reponse(_xml(
        _item(83195, "Ghost Blitz", VIGNETTE),
        _item(118705, "Ghost Blitz 2.0"))))
    page = admin.get("/admin/jeu/BAZAR?candidats=83195,118705").text
    cartes = re.findall(r'<li class="candidat-bgg">.*?</li>\s*(?=<li class="candidat-bgg">|</ol>)',
                        page, re.S)
    assert len(cartes) == 2
    balise, = _vignettes(cartes[0])
    assert _attribut(balise, "alt") == "Boîte de Ghost Blitz"
    assert _attribut(balise, "loading") == "lazy"
    assert _attribut(balise, "referrerpolicy") == "no-referrer"
    assert (_attribut(balise, "width"), _attribut(balise, "height")) == ("200", "150")
    # Ordre de lecture : le titre de la carte, puis l'image, puis le reste,
    # puis les boutons — l'image n'en déplace aucun.
    carte = cartes[0]
    assert (carte.index("<h3>") < carte.index('class="vignette-bgg"')
            < carte.index("fiche n° 83195") < carte.index("C'est ce jeu"))
    # Le candidat sans image garde sa carte, sans cadre vide.
    assert _vignettes(cartes[1]) == [] and "C'est ce jeu" in cartes[1]


def test_la_vignette_tient_dans_un_ecran_de_telephone():
    # Le cadre suit la carte : jamais plus large qu'elle, ratio fixe, même
    # quand l'image est bloquée (le cadre garde alors sa taille, pas plus).
    css = open("app/static/css/style.css", encoding="utf-8").read()
    regle = re.search(r"\.vignette-bgg\s*\{(.*?)\}", css, re.S).group(1)
    for propriete in ("max-width: 100%", "height: auto", "aspect-ratio: 4 / 3",
                      "object-fit: contain", "width: 200px"):
        assert propriete in regle, propriete
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", regle)


# ===========================================================================
# 5. AUCUNE VIGNETTE EN PUBLIC, AUCUN APPEL RÉSEAU À L'AFFICHAGE
# ===========================================================================
def test_aucune_vignette_sur_une_page_publique(anonyme):
    _relier_catane(VIGNETTE)
    conn = _conn()
    try:
        # Même avec le lien public coché : seul le lien sort, jamais l'image.
        services.ecrire_bgg_lien_public(conn, True)
    finally:
        conn.close()
    for url in ("/jeu/001", "/catalogue", "/catalogue?q=Catane", "/apropos", "/"):
        page = anonyme.get(url)
        assert page.status_code == 200, url
        assert "geekdo" not in page.text and "vignette-bgg" not in page.text, url
        assert "/image/bgg/" not in page.text, url
    # Et la copie elle-même n'est lisible qu'avec la session d'administration
    # (série « images », lot 1) : aucune réponse 200 sans elle.
    assert vignettes.url(13, VIGNETTE) is not None
    for url in (vignettes.url(13, VIGNETTE), "/image/bgg/13.webp"):
        assert anonyme.get(url).status_code == 404, url


def test_aucun_appel_reseau_pour_afficher_une_vignette(admin, bgg_interdit):
    _relier_catane(VIGNETTE)
    _copier(bgg_api.lire_reponse(_xml(_item(83195, "Ghost Blitz", VIGNETTE))))
    for url in ("/admin/jeu/CATANE", "/admin/jeu/BAZAR?candidats=83195", "/admin/jeux"):
        page = admin.get(url)
        assert page.status_code == 200, url
    assert _vignettes(admin.get("/admin/jeu/CATANE").text)


# ===========================================================================
# 6. UNE BASE D'AVANT
# ===========================================================================
def test_une_base_d_avant_gagne_la_colonne_vide(bases, client):
    from app import db
    conn = db.get_connection()
    try:
        # La table telle que le lot fiche-3 l'a laissée : sans `vignette`.
        conn.execute("DROP TABLE fiches_bgg")
        conn.execute("CREATE TABLE fiches_bgg (bgg_id INTEGER PRIMARY KEY, "
                     "trouve INTEGER NOT NULL DEFAULT 1, type TEXT, nom TEXT, "
                     "nb_joueurs_min INTEGER, nb_joueurs_max INTEGER, age_min INTEGER, "
                     "duree_min INTEGER, duree_max INTEGER, auteurs TEXT, annee INTEGER, "
                     "recupere_le TEXT NOT NULL)")
        conn.execute("INSERT INTO fiches_bgg (bgg_id, nom, recupere_le) "
                     "VALUES (13, 'CATAN', '2026-10-14T10:00:00+00:00')")
        conn.execute("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
        conn.commit()
        db.init_db(conn)
        db.init_db(conn)                                    # idempotente
        colonnes = [r[1] for r in conn.execute("PRAGMA table_info(fiches_bgg)")]
    finally:
        conn.close()
    assert "vignette" in colonnes
    assert _vignette_en_base(13) is None                    # jamais lue
    # Rien ne change à l'écran tant qu'on n'a pas récupéré…
    page = client.get("/admin/jeu/CATANE").text
    assert _vignettes(page) == [] and "Données BGG du" in page
    # … et la copie est à compléter.
    assert _a_recuperer() == [13]


def test_la_colonne_est_dans_le_schema_d_une_base_neuve(bases):
    conn = sqlite3.connect(bases / "test.db")
    try:
        colonnes = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(fiches_bgg)")}
    finally:
        conn.close()
    assert colonnes["vignette"] == "TEXT"
