"""
L'IMAGE DE LA BOÎTE, COPIÉE SUR NOTRE SERVEUR (série « images », lot 1).

Ce que ce fichier vérifie, et pourquoi :

1. **Le téléchargement** (`bgg_api.telecharger_vignette`) : un second hôte
   appelé par le serveur, avec les précautions du premier — hôte exact,
   `https:` seulement, AUCUNE redirection suivie, poids borné avant lecture
   complète, délai, jamais le jeton BGG, et le garde-fou `_aucun_reseau` qui
   bloque ce chemin aussi.
2. **Le contrôle et le réencodage** (`vignettes.reencoder`) : format réel,
   SVG, faux en-tête, fichier tronqué, image géante refusés ; métadonnées
   retirées ; réduction sans jamais agrandir ; WebP, transparence gardée.
3. **Le rangement** : écriture atomique, un fichier par fiche et par
   adresse, sélection des copies à faire, adresse changée.
4. **Le lot** de « Récupérer les données BGG manquantes » : fiches puis
   images, borne de temps sur horloge simulée, reprise au clic suivant,
   compteur et message, refus notés, arrêts dits.
5. **« C'est ce jeu »** et l'enregistrement d'un identifiant, qui copient
   l'image — et relient quand même si la copie échoue.
6. **La route** `/image/bgg/<n>.webp` : session d'administration exigée,
   404 nu, entier validé, version dans l'adresse.
7. **L'administration** : copie locale pour la fiche du jeu, adresse BGG
   pour les candidats.
8. **Le ménage** : délier par la case, « pas sur BGG », relier ailleurs,
   fiche partagée, formation, fichier étranger.
9. **Sans clé BGG**, rien ne se passe.

Le réseau n'est jamais touché : `bgg_api._ouvrir_image` est remplacé par un
faux serveur d'images (`fausses_images`), `urlopen` par le faux BGG.
"""

import hashlib
import http.client
import http.server
import io
import re
import socket
import threading
import urllib.error
import urllib.request

import pytest
from PIL import Image, ImageCms

from app import bgg_api, controle_image, formation, services, vignettes
from tests.test_bgg_relier import _copier, _ou, _texte, admin, catalogue, faux_bgg  # noqa: F401
from tests.test_bgg_vignette import VIGNETTE, _item, _xml
from tests.test_fiche_jeu import _conn, _executer, anonyme, bases, client  # noqa: F401
from tests.test_bgg_donnees import JETON


def _adresse(n: int) -> str:
    """Une adresse de vignette valide, distincte pour chaque `n`."""
    return VIGNETTE.replace("pic2403582", f"pic{n}")


# ===========================================================================
# Fabrique d'images de test
# ===========================================================================
def _image(format_, taille=(150, 150), mode="RGB", **options) -> bytes:
    image = Image.new(mode, taille, (200, 40, 40, 255) if mode == "RGBA" else (200, 40, 40))
    if mode == "RGBA":
        # Un coin transparent : la transparence est réelle.
        for x in range(10):
            for y in range(10):
                image.putpixel((x, y), (0, 0, 0, 0))
    sortie = io.BytesIO()
    image.save(sortie, format=format_, **options)
    return sortie.getvalue()


def _jpeg_avec_metadonnees() -> bytes:
    exif = Image.Exif()
    exif[0x010E] = "Description piegee"          # ImageDescription
    exif[0x013B] = "Auteur de la photo"          # Artist
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return _image("JPEG", exif=exif.tobytes(), icc_profile=icc,
                  comment=b"Commentaire piege")


JPEG = _image("JPEG")
PNG_TRANSPARENT = _image("PNG", (98, 150), "RGBA")


# Le faux serveur d'images (`fausses_images`, `FausseReponseImage`) vit dans
# tests/conftest.py : tests/test_bgg_vignette.py s'en sert aussi.
from tests.conftest import FausseReponseImage  # noqa: E402


def _fichiers():
    try:
        return sorted(p.name for p in vignettes.dossier().iterdir())
    except OSError:
        return []


def _copies():
    return [n for n in _fichiers() if n.endswith(".webp")]


def _relier_avec_vignettes(n, depart=1):
    """`n` titres reliés à `n` fiches BGG complètes, chacune avec son adresse."""
    for i in range(depart, depart + n):
        _executer("INSERT INTO titres (reference_titre, nom, bgg_id) VALUES (?, ?, ?)",
                  (f"J{i:04d}", f"Jeu {i}", i))
    _copier({i: {"nom": f"Jeu {i}", "vignette": _adresse(i)}
             for i in range(depart, depart + n)})


def _attendues():
    conn = _conn()
    try:
        return services.vignettes_bgg_attendues(conn)
    finally:
        conn.close()


def _relier_catane(adresse=VIGNETTE, bgg_id=13):
    _executer("UPDATE titres SET bgg_id = ? WHERE reference_titre = 'CATANE'", (bgg_id,))
    _copier({bgg_id: {"nom": "CATAN", "nb_joueurs_min": 3, "vignette": adresse}})


# ===========================================================================
# 1. LE TÉLÉCHARGEMENT
# ===========================================================================
def test_une_vraie_adresse_est_telechargee_sans_jeton(fausses_images, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    faux = fausses_images()
    assert bgg_api.telecharger_vignette(VIGNETTE) == JPEG
    requete, = faux.requetes
    assert requete.full_url == VIGNETTE
    entetes = {k.lower(): v for k, v in requete.header_items()}
    assert "authorization" not in entetes
    assert all(JETON not in v for v in entetes.values())
    assert entetes["user-agent"] == bgg_api.USER_AGENT
    assert faux.delais == [bgg_api.DELAI_IMAGE_S]


@pytest.mark.parametrize("adresse", [
    VIGNETTE.replace("https:", "http:"),
    VIGNETTE.replace("cf.geekdo-images.com", "images.example.org"),
    VIGNETTE.replace("cf.geekdo-images.com", "x.cf.geekdo-images.com"),
    f" {VIGNETTE}",            # pas TELLE QUELLE : l'appelant doit passer l'adresse contrôlée
    "",
])
def test_hors_de_l_hote_exact_rien_ne_part(fausses_images, adresse):
    faux = fausses_images()
    with pytest.raises(bgg_api.EchecImage) as echec:
        bgg_api.telecharger_vignette(adresse)
    assert echec.value.code == "refusee"
    assert faux.requetes == []


@pytest.mark.parametrize("reponse, code", [
    (404, "absente"), (410, "absente"), (403, "bloque"), (429, "attendre"),
    (500, "panne"), (503, "panne"), (301, "refusee"), (302, "refusee"), (307, "refusee"),
    (urllib.error.URLError(socket.gaierror("nom introuvable")), "panne"),
    (TimeoutError("délai"), "panne"),
    (ConnectionResetError("coupée"), "panne"),
    (http.client.IncompleteRead(b"x"), "panne"),
])
def test_chaque_issue_a_son_code(fausses_images, reponse, code):
    fausses_images(defaut=reponse)
    with pytest.raises(bgg_api.EchecImage) as echec:
        bgg_api.telecharger_vignette(VIGNETTE)
    assert echec.value.code == code
    # Chaîne coupée : aucune trace ne transporte la requête.
    assert echec.value.__cause__ is None and echec.value.__suppress_context__


def test_l_ouvreur_des_images_ne_suit_aucune_redirection():
    # Un vrai serveur sur la boucle locale répond 302 vers un autre hôte ;
    # l'ouvreur des images s'arrête là, sans seconde requête.
    recues = []

    class Redirige(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            recues.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://images.example.org/piege.jpg")
            self.end_headers()

        def log_message(self, *args):
            pass

    serveur = http.server.HTTPServer(("127.0.0.1", 0), Redirige)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        adresse = f"http://127.0.0.1:{serveur.server_port}/vignette.jpg"
        with pytest.raises(urllib.error.HTTPError) as erreur:
            bgg_api._OUVREUR_IMAGES.open(adresse, timeout=5)
        assert erreur.value.code == 302
        erreur.value.close()
    finally:
        serveur.shutdown()
        serveur.server_close()
    assert recues == ["/vignette.jpg"]
    # Et l'ouvreur par défaut, lui, l'aurait suivie : c'est pour ça qu'il y
    # a un ouvreur à part.
    assert not any(type(h) is urllib.request.HTTPRedirectHandler
                   for h in bgg_api._OUVREUR_IMAGES.handlers)
    assert any(isinstance(h, bgg_api._SansRedirection)
               for h in bgg_api._OUVREUR_IMAGES.handlers)


def test_borne_de_poids_lue_avant_la_lecture_complete(fausses_images):
    trop = b"\xff" * (bgg_api.TAILLE_MAX_IMAGE_OCTETS + 1)
    reponse = FausseReponseImage(trop)
    fausses_images(defaut=reponse)
    with pytest.raises(bgg_api.EchecImage) as echec:
        bgg_api.telecharger_vignette(VIGNETTE)
    assert echec.value.code == "refusee"
    # Au plus la borne + un morceau en mémoire, jamais tout le corps d'un coup.
    assert reponse.lectures <= bgg_api.TAILLE_MAX_IMAGE_OCTETS // (32 * 1024) + 1

    juste = b"\xff" * bgg_api.TAILLE_MAX_IMAGE_OCTETS
    fausses_images(defaut=juste)
    assert bgg_api.telecharger_vignette(VIGNETTE) == juste


def test_une_taille_annoncee_trop_grande_est_refusee_sans_lire(fausses_images):
    reponse = FausseReponseImage(JPEG, annonce=str(bgg_api.TAILLE_MAX_IMAGE_OCTETS + 1))
    fausses_images(defaut=reponse)
    with pytest.raises(bgg_api.EchecImage) as echec:
        bgg_api.telecharger_vignette(VIGNETTE)
    assert echec.value.code == "refusee" and reponse.lectures == 0


def test_un_telechargement_trop_lent_est_une_panne(fausses_images, monkeypatch):
    # La lecture se poursuit au-delà du délai (horloge réelle du module).
    instants = iter([0.0] + [bgg_api.DELAI_IMAGE_S + 1.0] * 10)
    monkeypatch.setattr(bgg_api.time, "monotonic", lambda: next(instants))
    fausses_images()
    with pytest.raises(bgg_api.EchecImage) as echec:
        bgg_api.telecharger_vignette(VIGNETTE)
    assert echec.value.code == "panne"


def test_l_echeance_raccourcit_le_delai_ou_n_envoie_rien(fausses_images, _cadence_bgg_simulee):
    faux = fausses_images()
    maintenant = _cadence_bgg_simulee()
    bgg_api.telecharger_vignette(VIGNETTE, fin=maintenant + 4)
    assert faux.delais[-1] <= 4
    # Moins que `DUREE_MIN_APPEL_S` avant l'échéance : rien ne part.
    with pytest.raises(bgg_api.EchecImage) as echec:
        bgg_api.telecharger_vignette(VIGNETTE, fin=_cadence_bgg_simulee() + 1)
    assert echec.value.code == "trop_long" and len(faux.requetes) == 1


def test_deux_telechargements_sont_espaces(fausses_images, _cadence_bgg_simulee):
    faux = fausses_images(duree=0)
    bgg_api.telecharger_vignette(VIGNETTE)
    bgg_api.telecharger_vignette(VIGNETTE)
    assert faux.instants[1] - faux.instants[0] == pytest.approx(bgg_api.INTERVALLE_IMAGES_S)


def test_le_garde_fou_des_tests_bloque_aussi_ce_chemin(monkeypatch):
    # Résolution de nom remplacée (aucune requête DNS) par une adresse de
    # documentation : la connexion est tentée, et refusée par `_aucun_reseau`.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))])
    with pytest.raises(Exception) as erreur:
        bgg_api.telecharger_vignette(VIGNETTE)
    assert type(erreur.value).__name__ == "ConnexionReseauInterdite"


def test_un_code_d_echec_hors_liste_est_refuse():
    with pytest.raises(ValueError):
        bgg_api.EchecImage("autre")


# ===========================================================================
# 2. LE CONTRÔLE ET LE RÉENCODAGE
# ===========================================================================
def _ouvrir(octets):
    image = Image.open(io.BytesIO(octets))
    image.load()
    return image


def test_un_vrai_jpeg_devient_un_webp_de_meme_taille():
    sortie = _ouvrir(vignettes.reencoder(_image("JPEG", (152, 150))))
    assert sortie.format == "WEBP" and sortie.size == (152, 150) and sortie.mode == "RGB"


def test_un_png_transparent_garde_sa_transparence():
    sortie = _ouvrir(vignettes.reencoder(PNG_TRANSPARENT))
    assert sortie.format == "WEBP" and sortie.mode == "RGBA"
    assert sortie.getchannel("A").getextrema()[0] == 0


def test_un_png_opaque_en_palette_devient_opaque():
    palette = Image.new("RGB", (77, 150), (10, 120, 30)).convert("P")
    sortie_png = io.BytesIO()
    palette.save(sortie_png, format="PNG")
    assert _ouvrir(vignettes.reencoder(sortie_png.getvalue())).mode == "RGB"


def test_un_webp_est_accepte():
    assert _ouvrir(vignettes.reencoder(_image("WEBP", (100, 150)))).size == (100, 150)


def test_les_metadonnees_sont_retirees():
    origine = _jpeg_avec_metadonnees()
    assert b"Description piegee" in origine and b"Commentaire piege" in origine
    sortie = vignettes.reencoder(origine)
    image = _ouvrir(sortie)
    for cle in ("exif", "icc_profile", "xmp", "comment"):
        assert not image.info.get(cle), cle
    for trace in (b"Description piegee", b"Auteur de la photo", b"Commentaire piege",
                  b"EXIF", b"ICCP", b"XMP "):
        assert trace not in sortie, trace


def test_jamais_d_agrandissement():
    assert _ouvrir(vignettes.reencoder(_image("PNG", (40, 30)))).size == (40, 30)


def test_une_image_trop_grande_est_reduite_dans_le_cadre():
    assert _ouvrir(vignettes.reencoder(_image("JPEG", (400, 300)))).size == (200, 150)
    largeur, hauteur = _ouvrir(vignettes.reencoder(_image("JPEG", (300, 600)))).size
    assert hauteur == vignettes.HAUTEUR_MAX and largeur <= vignettes.LARGEUR_MAX


@pytest.mark.parametrize("contenu, motif", [
    (b"", "vide"),
    (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "svg"),
    (b"<svg onload='x'/>", "svg"),
    (b"\xff\xd8\xff\xe0" + b"pas une image" * 20, "illisible"),        # faux en-tête JPEG
    (b"bonjour, je ne suis pas une image", "pas_une_image"),
    (JPEG[: len(JPEG) // 2], None),                                     # tronqué
    (_image("GIF"), "format_refuse"),
    (_image("PNG", (vignettes.DIMENSION_MAX + 1, 4)), "trop_grand"),    # géante
])
def test_ce_qui_n_est_pas_une_image_acceptable_est_refuse(contenu, motif):
    with pytest.raises(vignettes.VignetteRefusee) as refus:
        vignettes.reencoder(contenu)
    if motif:
        assert str(refus.value) == motif


def test_le_controle_est_celui_du_logo():
    # Un seul domicile : les deux modules appellent le contrôle commun.
    from pathlib import Path
    for fichier in ("app/logo.py", "app/vignettes.py"):
        assert "controle_image.controler(" in Path(fichier).read_text(encoding="utf-8"), fichier
    assert set(controle_image.MOTIFS) >= {"svg", "trop_grand", "format_refuse"}


# ===========================================================================
# 3. LE RANGEMENT
# ===========================================================================
def test_une_copie_par_fiche_et_par_adresse(bases):
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    empreinte = hashlib.sha256(VIGNETTE.encode()).hexdigest()[:16]
    assert _fichiers() == [f"13-{empreinte}.webp"]
    assert vignettes.dossier().parent == bases          # sous `data/`, à côté de la base
    assert vignettes.url(13, VIGNETTE) == f"/image/bgg/13.webp?v={empreinte}"
    assert vignettes.url(13, _adresse(1)) is None


def test_l_ecriture_passe_par_un_temporaire_et_ne_laisse_rien(bases, monkeypatch):
    remplacements = []
    vrai = vignettes.os.replace

    def espion(source, cible):
        remplacements.append((source.name if hasattr(source, "name") else str(source)))
        return vrai(source, cible)
    monkeypatch.setattr(vignettes.os, "replace", espion)
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    nom, = remplacements
    assert nom.startswith(".vignette-") and nom.endswith(".tmp")
    assert _fichiers() == _copies()                      # aucun temporaire restant


def test_une_ecriture_ratee_laisse_la_copie_d_avant_intacte(bases, monkeypatch):
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    avant = vignettes.chemin_servi(13, VIGNETTE).read_bytes()

    def panne(*args):
        raise OSError("disque plein")
    monkeypatch.setattr(vignettes.os, "replace", panne)
    with pytest.raises(OSError):
        vignettes.enregistrer(13, VIGNETTE, PNG_TRANSPARENT)
    assert vignettes.chemin_servi(13, VIGNETTE).read_bytes() == avant
    assert len(_fichiers()) == 1                          # temporaire retiré


def test_une_image_refusee_n_ecrit_rien(bases):
    with pytest.raises(vignettes.VignetteRefusee):
        vignettes.enregistrer(13, VIGNETTE, b"<svg/>")
    assert _fichiers() == []


def test_la_selection_croise_la_base_et_le_dossier(bases):
    for i in range(1, 8):
        _executer("INSERT INTO titres (reference_titre, nom, bgg_id) VALUES (?, ?, ?)",
                  (f"J{i}", f"Jeu {i}", i))
    _copier({
        1: {"nom": "Adresse sans fichier", "vignette": _adresse(1)},
        2: {"nom": "Fichier présent", "vignette": _adresse(2)},
        3: {"nom": "Sans image", "vignette": ""},
        4: {"nom": "Jamais lue"},
        5: None,                                                   # inconnue de BGG
        6: {"nom": "Adresse changée", "vignette": _adresse(66)},
        7: {"nom": "Adresse mauvaise", "vignette": "https://images.example.org/x.jpg"},
        8: {"nom": "Candidat qu'aucun titre ne porte", "vignette": _adresse(8)},
    })
    vignettes.enregistrer(2, _adresse(2), JPEG)
    vignettes.enregistrer(6, _adresse(6), JPEG)                  # l'ancienne adresse
    attendues = _attendues()
    assert attendues == {1: _adresse(1), 2: _adresse(2), 6: _adresse(66)}
    assert vignettes.a_copier(attendues) == [(1, _adresse(1)), (6, _adresse(66))]


def test_une_adresse_changee_remplace_l_ancienne_copie(bases):
    vignettes.enregistrer(13, _adresse(1), JPEG)
    vignettes.enregistrer(13, _adresse(2), PNG_TRANSPARENT)
    assert _fichiers() == [f"13-{vignettes.empreinte(_adresse(2))}.webp"]
    assert vignettes.url(13, _adresse(1)) is None
    assert vignettes.url(13, _adresse(2)).endswith(vignettes.empreinte(_adresse(2)))


def test_une_marque_de_refus_n_est_pas_reprise_mais_peut_etre_oubliee(bases):
    vignettes.noter_refus(13, VIGNETTE)
    assert vignettes.a_copier({13: VIGNETTE}) == []
    assert vignettes.url(13, VIGNETTE) is None
    vignettes.oublier_refus(13, VIGNETTE)
    assert vignettes.a_copier({13: VIGNETTE}) == [(13, VIGNETTE)]


def test_sans_dossier_rien_ne_casse(bases):
    assert vignettes.a_copier({13: VIGNETTE}) == [(13, VIGNETTE)]
    assert vignettes.url(13, VIGNETTE) is None
    assert vignettes.menage({}) == 0


# ===========================================================================
# 4. LE LOT
# ===========================================================================
def test_le_lot_recupere_les_fiches_puis_copie_les_images(admin, faux_bgg, fausses_images,
                                                          _cadence_bgg_simulee):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _executer("UPDATE titres SET bgg_id = 14 WHERE reference_titre = 'DOBBLE'")
    page = _texte(admin.get("/admin/jeux").text)
    assert "2 fiches BoardGameGeek reliées à vos jeux n'ont pas encore de données" in page
    api = faux_bgg(_xml(_item(13, "CATAN", _adresse(13)), _item(14, "Dobble", _adresse(14))))
    images = fausses_images()
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert _ou(r) == "/admin/jeux?bgg_lot=2&bgg_inconnues=0&bgg_images=2"
    # Les fiches d'abord : l'image ne se demande qu'une fois son adresse connue.
    assert api.instants[0] < images.instants[0]
    assert images.adresses() == [_adresse(13), _adresse(14)]
    assert len(_copies()) == 2
    page = _texte(admin.get(_ou(r)).text)
    assert "2 fiches récupérées, 2 images de boîte copiées, 0 restante." in page
    assert "Tous les jeux reliés à BoardGameGeek ont leurs données BGG." in page


def test_le_compteur_compte_les_images_a_copier(admin, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    _relier_avec_vignettes(3)
    vignettes.enregistrer(1, _adresse(1), JPEG)
    page = _texte(admin.get("/admin/jeux").text)
    assert ("2 fiches BoardGameGeek reliées à vos jeux n'ont pas encore de données BGG, "
            "ou pas encore l'image de la boîte.") in page


def test_le_lot_est_borne_dans_le_temps_et_reprend_au_clic_suivant(
        admin, faux_bgg, fausses_images, _cadence_bgg_simulee, monkeypatch):
    _relier_avec_vignettes(300)
    faux_bgg()
    images = fausses_images(duree=0.4)
    debut = _cadence_bgg_simulee()
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    copiees = int(re.search(r"bgg_images=(\d+)", _ou(r)).group(1))
    assert 0 < copiees < 300
    # Aucun téléchargement ne commence sans pouvoir finir avant la borne.
    assert images.instants[-1] + bgg_api.DUREE_MIN_APPEL_S <= debut + bgg_api.BORNE_LOT_S
    assert _cadence_bgg_simulee() <= debut + bgg_api.BORNE_LOT_S
    assert len(_copies()) == copiees
    page = _texte(admin.get(_ou(r)).text)
    assert f"{300 - copiees} restantes." in page

    # Second clic : rien n'est redemandé.
    deja = set(images.adresses())
    images2 = fausses_images(duree=0.4)
    admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert not deja & set(images2.adresses())
    assert len(_copies()) == copiees + len(images2.requetes)


def test_les_fiches_passent_d_abord_et_les_images_prennent_le_reste(
        admin, faux_bgg, fausses_images, _cadence_bgg_simulee, monkeypatch):
    # Les fiches prennent presque tout le temps du clic : les images n'ont
    # que le reste, et la borne tient pour l'ensemble.
    monkeypatch.setattr(bgg_api, "PAUSE_ENTRE_APPELS_S", 0)
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _relier_avec_vignettes(50, depart=100)
    faux_bgg(_xml(_item(13, "CATAN", _adresse(13))), duree=30)
    images = fausses_images(duree=0.4)
    debut = _cadence_bgg_simulee()
    admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert images.requetes and len(images.requetes) < 50
    assert images.instants[0] >= debut + 30
    assert images.instants[-1] + bgg_api.DUREE_MIN_APPEL_S <= debut + bgg_api.BORNE_LOT_S


def test_une_image_absente_est_notee_et_pas_redemandee(admin, faux_bgg, fausses_images):
    _relier_avec_vignettes(3)
    faux_bgg()
    fausses_images(reponses={_adresse(2): 404, _adresse(3): b"<svg/>"})
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert _ou(r) == ("/admin/jeux?bgg_lot=0&bgg_inconnues=0&bgg_images=1"
                      "&bgg_images_refusees=2")
    page = _texte(admin.get(_ou(r)).text)
    assert ("2 images n'ont pas pu être copiées (absente de BoardGameGeek, ou fichier "
            "refusé) : ces jeux s'affichent sans image.") in page
    assert "Tous les jeux reliés à BoardGameGeek ont leurs données BGG." in page
    encore = fausses_images()
    admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert encore.requetes == []


@pytest.mark.parametrize("reponse, code", [(500, "panne"), (403, "bloque"), (429, "attendre")])
def test_un_arret_est_dit_et_la_suite_attend(admin, faux_bgg, fausses_images,
                                             _journal_isole, reponse, code):
    import json
    _relier_avec_vignettes(3)
    faux_bgg()
    fausses_images(reponses={_adresse(2): reponse})
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert _ou(r).endswith(f"&bgg_images=1&bgg_images_echec={code}")
    page = admin.get(_ou(r)).text
    assert 'role="alert"' in page
    from app.routes.admin import MESSAGES_IMAGES_LOT
    assert MESSAGES_IMAGES_LOT[code] in _texte(page)
    lignes = [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()]
    ligne, = [l for l in lignes if l["action"] == "bgg_lot_recupere"]
    assert ligne["ok"] is False and ligne["detail"] == f"image_{code}"
    assert ligne["objet"] == "0 récupérées, 1 images copiées, 2 restantes"
    assert "geekdo" not in _journal_isole.read_text(encoding="utf-8")
    assert len(_copies()) == 1


def test_un_disque_plein_n_empeche_ni_le_lot_ni_la_liste(admin, faux_bgg, fausses_images,
                                                         monkeypatch):
    _relier_avec_vignettes(2)
    faux_bgg()
    fausses_images()

    def plein(*args):
        raise OSError("disque plein")
    monkeypatch.setattr(vignettes, "enregistrer", plein)
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert _ou(r).endswith("&bgg_images_echec=disque")
    page = admin.get(_ou(r))
    assert page.status_code == 200 and "disque plein, ou dossier protégé" in page.text


# ===========================================================================
# 5. « C'EST CE JEU », L'ENREGISTREMENT D'UN IDENTIFIANT, LE BOUTON
# ===========================================================================
def _candidat_bazar(adresse=VIGNETTE):
    _copier(bgg_api.lire_reponse(_xml(_item(83195, "Ghost Blitz", adresse))))


def test_c_est_ce_jeu_copie_l_image(admin, fausses_images, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    _candidat_bazar()
    images = fausses_images()
    r = admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert images.adresses() == [VIGNETTE]
    assert vignettes.url(83195, VIGNETTE) is not None


@pytest.mark.parametrize("panne", [500, 404, TimeoutError("délai"), b"pas une image"])
def test_c_est_ce_jeu_relie_meme_si_la_copie_echoue(admin, fausses_images, monkeypatch, panne):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    _candidat_bazar()
    fausses_images(defaut=panne)
    r = admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"},
                   follow_redirects=False)
    assert _ou(r) == "/admin/jeu/BAZAR?bgg=completer&enchainer=1#modifier"
    conn = _conn()
    try:
        assert services.get_titre(conn, "BAZAR")["bgg_id"] == 83195
    finally:
        conn.close()
    assert _copies() == []
    # La fiche s'ouvre, sans image et sans cadre vide.
    page = admin.get("/admin/jeu/BAZAR")
    assert page.status_code == 200 and "vignette-bgg" not in page.text


def test_un_identifiant_tape_dans_la_case_copie_l_image(admin, fausses_images, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    _candidat_bazar()
    fausses_images()
    r = admin.post("/admin/jeu/BAZAR/modifier", data={"nom": "Bazar bizarre", "bgg": "83195"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert vignettes.url(83195, VIGNETTE) is not None


def test_interroger_bgg_copie_et_retente_une_image_refusee(client, faux_bgg, fausses_images):
    _relier_catane(VIGNETTE)
    vignettes.noter_refus(13, VIGNETTE)
    faux_bgg(_xml(_item(13, "CATAN", VIGNETTE)))
    images = fausses_images()
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert "ok=bgg_recupere" in _ou(r)
    assert images.adresses() == [VIGNETTE]
    assert _fichiers() == [f"13-{vignettes.empreinte(VIGNETTE)}.webp"]


def test_une_nouvelle_adresse_chez_bgg_remplace_la_copie(client, faux_bgg, fausses_images):
    _relier_catane(_adresse(1))
    vignettes.enregistrer(13, _adresse(1), JPEG)
    ancienne = vignettes.url(13, _adresse(1))
    faux_bgg(_xml(_item(13, "CATAN", _adresse(2))))
    fausses_images(defaut=PNG_TRANSPARENT)
    client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    nouvelle = vignettes.url(13, _adresse(2))
    assert nouvelle and nouvelle != ancienne
    assert _fichiers() == [f"13-{vignettes.empreinte(_adresse(2))}.webp"]
    page = client.get("/admin/jeu/CATANE").text
    assert nouvelle in page and ancienne not in page


# ===========================================================================
# 6. LA ROUTE
# ===========================================================================
def test_la_route_sert_la_copie_a_l_administration(client):
    _relier_catane()
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    r = client.get(vignettes.url(13, VIGNETTE))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.content == vignettes.chemin_servi(13, VIGNETTE).read_bytes()


def test_la_route_refuse_sans_session_d_administration(anonyme):
    _relier_catane()
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    for adresse in (vignettes.url(13, VIGNETTE), "/image/bgg/13.webp"):
        r = anonyme.get(adresse)
        assert r.status_code == 404, adresse
        assert r.content == b"" and "image" not in r.headers.get("content-type", "")


@pytest.mark.parametrize("chemin", [
    "/image/bgg/14.webp",                      # fiche sans copie
    "/image/bgg/0.webp", "/image/bgg/013.webp", "/image/bgg/abc.webp",
    "/image/bgg/-13.webp", "/image/bgg/13.png", "/image/bgg/12345678901.webp",
    "/image/bgg/13%2F..%2F..%2Ftest.db.webp",
])
def test_la_route_rend_un_404_nu(client, chemin):
    _relier_catane()
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    r = client.get(chemin)
    assert r.status_code == 404
    assert b"SQLite" not in r.content


def test_une_copie_d_une_ancienne_adresse_n_est_pas_servie(client):
    _relier_catane(_adresse(2))
    # Un fichier de l'ancienne adresse, resté là (le lot ne l'a pas encore remplacé).
    vignettes.dossier().mkdir(parents=True, exist_ok=True)
    (vignettes.dossier() / f"13-{vignettes.empreinte(_adresse(1))}.webp").write_bytes(
        vignettes.reencoder(JPEG))
    assert client.get("/image/bgg/13.webp").status_code == 404


def test_un_fichier_etranger_n_est_ni_servi_ni_supprime(client):
    _relier_catane()
    dossier = vignettes.dossier()
    dossier.mkdir(parents=True, exist_ok=True)
    for nom in ("13.webp", "lisez-moi.txt", "13-abc.webp"):
        (dossier / nom).write_bytes(b"etranger")
    assert client.get("/image/bgg/13.webp").status_code == 404
    vignettes.menage({})
    vignettes.vider()
    assert _fichiers() == ["13-abc.webp", "13.webp", "lisez-moi.txt"]


# ===========================================================================
# 7. L'ADMINISTRATION
# ===========================================================================
def _vignettes_de(page):
    return [i for i in re.findall(r"<img\b[^>]*>", page, re.S) if 'class="vignette-bgg"' in i]


def test_la_fiche_du_jeu_affiche_la_copie_locale(client):
    _relier_catane()
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    page = client.get("/admin/jeu/CATANE").text
    balise, = _vignettes_de(page)
    assert re.search(r'src="([^"]*)"', balise).group(1).replace("&amp;", "&") == \
        vignettes.url(13, VIGNETTE)
    assert 'alt="Boîte de CATAN"' in balise
    assert "geekdo" not in page


def test_une_fiche_reliee_sans_copie_n_affiche_rien_et_le_dit(client, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    _relier_catane()
    page = client.get("/admin/jeu/CATANE").text
    assert _vignettes_de(page) == [] and "geekdo" not in page
    assert "L'image de la boîte n'est pas encore copiée sur ce site" in _texte(page)


def test_les_candidats_restent_charges_chez_bgg(admin):
    _candidat_bazar()
    page = admin.get("/admin/jeu/BAZAR?candidats=83195").text
    balise, = _vignettes_de(page)
    assert re.search(r'src="([^"]*)"', balise).group(1).replace("&amp;", "&") == VIGNETTE


def test_aucune_image_de_jeu_sur_une_page_publique(anonyme):
    _relier_catane()
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    for adresse in ("/jeu/001", "/catalogue", "/catalogue?q=Catane", "/apropos", "/"):
        page = anonyme.get(adresse)
        assert page.status_code == 200, adresse
        assert "/image/bgg/" not in page.text and "geekdo" not in page.text, adresse


# ===========================================================================
# 8. LE MÉNAGE
# ===========================================================================
def _copie_catane():
    _relier_catane()
    vignettes.enregistrer(13, VIGNETTE, JPEG)
    assert len(_copies()) == 1


def _enregistrer_catane(client, bgg):
    return client.post("/admin/jeu/CATANE/modifier",
                       data={"nom": "Catane", "type_jeu": "Jeu", "bgg": bgg},
                       follow_redirects=False)


def test_delier_par_la_case_supprime_la_copie(client):
    _copie_catane()
    assert _enregistrer_catane(client, "").status_code == 303
    assert _copies() == []


def test_pas_sur_bgg_dans_la_case_supprime_la_copie(client):
    _copie_catane()
    _enregistrer_catane(client, "pas sur BGG")
    assert _copies() == []


def test_le_bouton_pas_sur_bgg_ne_delie_pas_et_garde_la_copie(client):
    # Sur un jeu relié, le bouton est refusé (« deja_relie ») : rien n'est
    # délié, la copie reste.
    _copie_catane()
    r = client.post("/admin/jeu/CATANE/bgg/absent", follow_redirects=False)
    assert "ok=deja_relie" in _ou(r)
    assert len(_copies()) == 1


def test_relier_ailleurs_supprime_l_ancienne_copie(client):
    _copie_catane()
    _copier({14: {"nom": "Autre", "vignette": _adresse(14)}})
    _enregistrer_catane(client, "14")
    assert not any(n.startswith("13-") for n in _fichiers())


def test_une_fiche_partagee_garde_sa_copie(client):
    _copie_catane()
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'DOBBLE'")
    _enregistrer_catane(client, "")
    assert len(_copies()) == 1


def test_le_lot_fait_le_menage_des_orphelines(admin, faux_bgg, fausses_images):
    _copie_catane()
    vignettes.noter_refus(99, _adresse(99))
    _executer("UPDATE titres SET bgg_id = NULL")              # un import l'a délié
    faux_bgg()
    fausses_images()
    admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert _fichiers() == []


def test_un_temporaire_abandonne_part_au_menage(bases):
    import os
    import time
    dossier = vignettes.dossier()
    dossier.mkdir(parents=True)
    vieux, recent = dossier / ".vignette-vieux.tmp", dossier / ".vignette-recent.tmp"
    for chemin in (vieux, recent):
        chemin.write_bytes(b"x")
    il_y_a_deux_heures = time.time() - 7200
    os.utime(vieux, (il_y_a_deux_heures, il_y_a_deux_heures))
    vignettes.menage({})
    assert _fichiers() == [".vignette-recent.tmp"]


def test_la_remise_a_zero_de_la_formation_vide_les_copies(bases):
    _copie_catane()
    vignettes.noter_refus(14, _adresse(14))
    conn = _conn()
    try:
        formation._vider_base_pret(conn)
    finally:
        conn.close()
    assert _fichiers() == []


# ===========================================================================
# 9. SANS CLÉ BGG
# ===========================================================================
def test_sans_cle_rien_ne_se_passe(admin, fausses_images):
    _relier_avec_vignettes(2)
    _candidat_bazar()
    images = fausses_images()
    page = admin.get("/admin/jeux").text
    assert "Données BoardGameGeek" not in page
    r = admin.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert _ou(r) == "/admin/jeux?bgg_echec=sans_jeton"
    admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"})
    fiche = admin.get("/admin/jeu/J0001").text
    assert "pas encore copiée" not in fiche
    assert images.requetes == [] and _fichiers() == []
