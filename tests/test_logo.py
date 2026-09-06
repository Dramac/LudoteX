"""
LOGO : identité LudoteX versionnée, logo d'association déposé depuis
`/admin/identite` (lot 3a de l'ouverture publique).

Ce que ce fichier vérifie, et pourquoi c'est ce qui compte :

1. **Un dépôt valide produit les TROIS fichiers** dans `data/`, à partir d'un
   seul envoi — l'icône d'onglet n'est jamais demandée au bureau.
2. **Les contrôles de sécurité tiennent, un par un** : taille, format réel
   (jamais l'extension ni le `Content-Type`), dimensions — et **le SVG est
   refusé**, alors même que le logo par défaut en est un. C'est le point le
   plus contre-intuitif du lot : un SVG est du XML qui peut porter du script,
   et servi depuis notre propre origine c'est une XSS stockée.
3. **Un refus n'abîme rien** : ni un logo déjà en place, ni la saisie des
   quatre champs texte de l'écran.
4. **Le nom de fichier du client n'entre dans aucun chemin.** Les trois noms de
   sortie sont fixes ; c'est ce qui rend la traversée de répertoire impossible
   plutôt que « filtrée ».
5. **Sans réglage**, les pages ET les étiquettes servent le meeple LudoteX
   par défaut (`app.etiquettes.charger_logo`) — dans deux fichiers versionnés
   DISTINCTS (couleur pour l'écran, monochrome pour le papier). Le test des
   étiquettes vit dans `tests/test_logo.py` ; l'aplatissement sur fond blanc
   d'un logo à canal alpha (lot 3c) est vérifié séparément (point 7).
6. **La route d'image reste debout** quand `data/` est illisible.
7. **Un logo à fond transparent ne devient jamais un rectangle noir** sur une
   étiquette (lot 3c) : `Image.convert("RGB")` seul ignore l'alpha et garde des
   canaux RVB souvent noirs sous la transparence — corrigé par un
   aplatissement explicite sur fond blanc.

Tous les tests écrivent dans le `data/` d'un dossier temporaire : le dossier
est déduit du parent de la base de prêt (voir `app.logo.dossier_regle`), que la
fixture redirige. Rien n'est laissé dans le dépôt.
"""

import io
import json

import pytest
from PIL import Image


MOT_DE_PASSE = "secret-admin-logo"


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Trois bases temporaires, patron de tests/test_apropos_reglages.py."""
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


@pytest.fixture
def client(bases):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Fabriques d'images de test
# ---------------------------------------------------------------------------
def _image(format_: str, taille=(400, 300), couleur=(20, 120, 200, 255)) -> bytes:
    """Une image valide de `taille`, encodée dans `format_`."""
    mode = "RGBA" if format_ in ("PNG", "WEBP") else "RGB"
    im = Image.new(mode, taille, couleur[: 4 if mode == "RGBA" else 3])
    tampon = io.BytesIO()
    im.save(tampon, format=format_)
    return tampon.getvalue()


SVG = (
    b'<?xml version="1.0"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b'<script>alert(1)</script><rect width="10" height="10"/></svg>'
)


def _connexion(client):
    return client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _poster(client, *, fichier=None, nom_fichier="logo.png", type_mime="image/png",
            **champs):
    """
    Poste le formulaire d'identité en multipart, comme un navigateur.

    Les quatre champs texte voyagent TOUJOURS ensemble : n'en passer qu'un
    revient à vider les autres, exactement comme dans un navigateur.
    """
    _connexion(client)
    donnees = {"nom_association": "", "presentation": "", "contact": "",
               "depot_url": ""}
    donnees.update(champs)
    fichiers = None
    if fichier is not None:
        fichiers = {"logo_fichier": (nom_fichier, fichier, type_mime)}
    return client.post("/admin/identite", data=donnees, files=fichiers)


def _lignes(chemin):
    if not chemin.exists():
        return []
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ---------------------------------------------------------------------------
# 1. UN DÉPÔT VALIDE
# ---------------------------------------------------------------------------
def test_png_valide_produit_les_trois_fichiers(client, bases):
    """Un seul envoi, trois tailles : le bureau n'en prépare pas trois."""
    reponse = _poster(client, fichier=_image("PNG"))
    assert reponse.status_code == 200
    assert "Logo déposé." in reponse.text

    for nom in ("logo.png", "favicon-192.png", "favicon-512.png"):
        assert (bases / nom).is_file(), nom

    with Image.open(bases / "favicon-192.png") as im:
        assert im.size == (192, 192)      # une icône est CARRÉE, quoi qu'on dépose
        assert im.format == "PNG"
    with Image.open(bases / "favicon-512.png") as im:
        assert im.size == (512, 512)


def test_jpeg_valide_est_accepte_et_reencode_en_png(client, bases):
    """
    Le fichier écrit est toujours un PNG reconstruit à partir des pixels, quel
    que soit le format déposé : c'est ce ré-encodage qui laisse dehors les
    métadonnées et toute charge utile accolée à l'image.
    """
    reponse = _poster(client, fichier=_image("JPEG"), nom_fichier="photo.jpg",
                      type_mime="image/jpeg")
    assert reponse.status_code == 200
    with Image.open(bases / "logo.png") as im:
        assert im.format == "PNG"


def test_webp_valide_est_accepte(client, bases):
    reponse = _poster(client, fichier=_image("WEBP"), nom_fichier="logo.webp",
                      type_mime="image/webp")
    assert reponse.status_code == 200
    assert (bases / "logo.png").is_file()


def test_une_image_plus_petite_que_la_borne_nest_pas_agrandie(client, bases):
    """
    Agrandir un petit logo ne crée pas de détail : ça ne fait qu'occuper du
    disque et rendre le flou plus visible. Le fichier principal garde donc sa
    taille d'origine ; seules les icônes, qui DOIVENT être carrées, sont
    recomposées.
    """
    _poster(client, fichier=_image("PNG", taille=(120, 60)))
    with Image.open(bases / "logo.png") as im:
        assert im.size == (120, 60)


def test_depot_journalise_sans_le_nom_du_fichier(client, bases, monkeypatch):
    """
    Le nom du fichier vient du poste de la personne qui dépose et peut contenir
    un prénom : le journal dit qu'un logo a été déposé, jamais lequel.
    """
    from app import journal

    _poster(client, fichier=_image("PNG"), nom_fichier="logo-marie-dupont.png")
    lignes = [l for l in _lignes(journal.chemin_journal())
              if l.get("action") == "association_logo_modifie"]
    assert len(lignes) == 1
    assert lignes[0]["objet"] == "déposé"
    assert "marie" not in json.dumps(lignes[0]).lower()


# ---------------------------------------------------------------------------
# 2. LES CONTRÔLES, UN PAR UN
# ---------------------------------------------------------------------------
def test_un_fichier_qui_nest_pas_une_image_est_refuse(client, bases):
    reponse = _poster(client, fichier=b"ceci n'est pas une image, du tout",
                      nom_fichier="catalogue.csv", type_mime="text/csv")
    assert reponse.status_code == 400
    assert "PNG" in reponse.text
    assert not (bases / "logo.png").exists()


def test_le_svg_est_refuse_meme_annonce_comme_un_png(client, bases):
    """
    LE test du lot. Un SVG est du XML qui peut porter du script ; servi depuis
    notre propre origine, il s'exécute dans notre contexte. Le logo PAR DÉFAUT
    est pourtant un SVG : celui-là vient de nous, un fichier téléversé ne vient
    de personne de confiance. Ne pas « corriger » cette asymétrie.

    Annoncé ici sous une extension et un type MIME de PNG, pour vérifier que
    c'est bien le CONTENU qui décide.
    """
    reponse = _poster(client, fichier=SVG, nom_fichier="logo.png",
                      type_mime="image/png")
    assert reponse.status_code == 400
    assert "SVG" in reponse.text
    assert not (bases / "logo.png").exists()


def test_un_fichier_trop_lourd_est_refuse(client, bases):
    from app import logo

    reponse = _poster(client, fichier=b"\x89PNG\r\n\x1a\n" +
                      b"x" * (logo.TAILLE_MAX_OCTETS + 1))
    assert reponse.status_code == 400
    assert "trop lourd" in reponse.text
    assert not (bases / "logo.png").exists()


def test_des_dimensions_hors_bornes_sont_refusees(client, bases):
    """
    Une image de très grande taille tient dans peu d'octets une fois
    compressée : la borne de POIDS ne protège pas du coût de DÉCODAGE, d'où
    cette seconde borne, appliquée avant tout décodage.
    """
    from app import logo

    cote = logo.DIMENSION_MAX + 1
    reponse = _poster(client, fichier=_image("PNG", taille=(cote, 10)))
    assert reponse.status_code == 400
    assert "trop grande" in reponse.text
    assert not (bases / "logo.png").exists()


def test_le_format_est_celui_du_contenu_pas_celui_du_nom(client, bases):
    """Un GIF renommé en .png reste un GIF, et n'est pas dans les formats acceptés."""
    tampon = io.BytesIO()
    Image.new("P", (40, 40)).save(tampon, format="GIF")
    reponse = _poster(client, fichier=tampon.getvalue(), nom_fichier="logo.png",
                      type_mime="image/png")
    assert reponse.status_code == 400
    assert "GIF" in reponse.text
    assert not (bases / "logo.png").exists()


def test_un_champ_de_fichier_vide_nest_pas_un_refus(client, bases):
    """
    Le cas de très loin le plus fréquent : on vient corriger l'adresse de
    contact, sans toucher au logo. Les quatre champs texte doivent continuer de
    fonctionner exactement comme avant le lot.
    """
    reponse = _poster(client, contact="contact@mon-asso.fr",
                      nom_association="Mon asso")
    assert reponse.status_code == 200
    assert "Contact enregistré." in reponse.text
    assert "Logo déposé." not in reponse.text


# ---------------------------------------------------------------------------
# 3. UN REFUS N'ABÎME RIEN
# ---------------------------------------------------------------------------
def test_un_refus_nefface_pas_le_logo_deja_en_place(client, bases):
    _poster(client, fichier=_image("PNG", taille=(200, 100)))
    avant = (bases / "logo.png").read_bytes()

    reponse = _poster(client, fichier=SVG)
    assert reponse.status_code == 400
    assert (bases / "logo.png").read_bytes() == avant


def test_un_refus_de_logo_ne_perd_pas_la_saisie_des_champs_texte(client, bases):
    """
    Patron du lot 2 (§3.3 de son compte rendu) : rien n'est enregistré, et la
    page revient avec TOUT ce qui a été tapé. Le bureau corrige le fichier, il
    ne retape pas les quatre champs.
    """
    from app import db, services

    reponse = _poster(client, fichier=SVG, nom_association="Ludo du Bocage",
                      presentation="On prête des jeux.",
                      contact="contact@bocage.test",
                      depot_url="https://github.com/exemple/ludotex")
    assert reponse.status_code == 400
    assert "Ludo du Bocage" in reponse.text
    assert "On prête des jeux." in reponse.text
    assert "contact@bocage.test" in reponse.text
    assert "https://github.com/exemple/ludotex" in reponse.text

    conn = db.get_connection()
    try:
        assert services.lire_parametre(conn, services.CLE_ASSOCIATION_NOM) is None
        assert services.lire_parametre(conn, services.CLE_ASSOCIATION_CONTACT) is None
    finally:
        conn.close()


def test_un_refus_de_champ_texte_nenregistre_pas_le_logo_pourtant_valide(client, bases):
    """
    L'inverse du test précédent, et la même règle : un seul refus, quel qu'il
    soit, n'enregistre RIEN. Enregistrer la moitié du formulaire en silence
    laisserait croire que tout est passé.
    """
    reponse = _poster(client, fichier=_image("PNG"), depot_url="javascript:alert(1)")
    assert reponse.status_code == 400
    assert not (bases / "logo.png").exists()


def test_un_refus_est_journalise_sans_la_valeur(client, bases):
    from app import journal

    _poster(client, fichier=SVG)
    lignes = [l for l in _lignes(journal.chemin_journal())
              if l.get("action") == "association_logo_modifie"]
    assert len(lignes) == 1
    assert lignes[0]["ok"] is False
    assert lignes[0]["detail"] == "logo_svg_refuse"


# ---------------------------------------------------------------------------
# 4. LE NOM DE FICHIER DU CLIENT N'ENTRE DANS AUCUN CHEMIN
# ---------------------------------------------------------------------------
def test_le_nom_de_fichier_du_client_ninfluence_aucun_chemin(client, bases):
    """
    Les trois noms de sortie sont FIXES : il n'y a pas de filtre de traversée
    de répertoire à contourner, parce qu'il n'y a pas de chemin construit à
    partir du client. On dépose sous un nom hostile et on vérifie qu'il ne
    reste, dans `data/`, que les fichiers attendus.
    """
    avant = {c.name for c in bases.iterdir()}
    reponse = _poster(client, fichier=_image("PNG"),
                      nom_fichier="../../app/static/img/logo.png")
    assert reponse.status_code == 200
    apparus = {c.name for c in bases.iterdir()} - avant
    assert apparus == {"logo.png", "favicon-192.png", "favicon-512.png"}


def test_aucun_fichier_temporaire_ne_reste_apres_un_depot(client, bases):
    _poster(client, fichier=_image("PNG"))
    assert not [c for c in bases.iterdir() if c.name.endswith(".tmp")]


# ---------------------------------------------------------------------------
# 5. SANS RÉGLAGE — deux comportements DIFFÉRENTS, et c'est voulu
# ---------------------------------------------------------------------------
def test_sans_reglage_les_pages_servent_le_logo_versionne(client, bases):
    from app import logo

    page = client.get("/")
    assert '/image/logo.png?v=' in page.text

    reponse = client.get("/image/logo.png")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "image/png"
    assert reponse.content == logo.chemin_versionne("logo.png").read_bytes()


def test_sans_reglage_les_etiquettes_prennent_le_meeple_par_defaut(client, bases):
    """
    RÈGLE QUI A CHANGÉ AU LOT 3c. Jusqu'ici, faute de logo réglé, les
    étiquettes ne recevaient RIEN (`charger_logo() is None`) et dessinaient un
    cadre placeholder « LOGO ». Ce cadre a disparu : le vrai choix n'était pas
    « le meeple ou rien », mais « le meeple ou un cadre imprimé en sept cents
    exemplaires » — voir `app.etiquettes.charger_logo`. `charger_logo()` ne
    renvoie donc plus jamais None.
    """
    from app.etiquettes import _MEEPLE_DEFAUT, charger_logo

    image = charger_logo()
    assert image is not None
    assert image.mode == "RGB"
    with Image.open(_MEEPLE_DEFAUT) as meeple:
        assert image.size == meeple.size


def test_avec_reglage_les_etiquettes_prennent_le_logo_depose(client, bases):
    _poster(client, fichier=_image("PNG", taille=(300, 200)))

    from app.etiquettes import charger_logo

    image = charger_logo()
    assert image is not None
    assert image.size == (300, 200)


def test_apres_depot_la_route_sert_le_fichier_depose(client, bases):
    from app import logo

    _poster(client, fichier=_image("PNG"))
    reponse = client.get("/image/logo.png")
    assert reponse.content == (bases / "logo.png").read_bytes()
    assert reponse.content != logo.chemin_versionne("logo.png").read_bytes()


def test_le_parametre_de_cache_change_avec_le_logo(client, bases):
    """
    Sans cela, un navigateur garderait l'ancien logo en cache et le bureau
    croirait son dépôt raté. Motif `asset_v` (app/templating.py), mais relu à
    chaque rendu : le logo change SANS redémarrage.
    """
    import os

    from app import logo

    avant = logo.version_servie()
    _poster(client, fichier=_image("PNG"))
    # Deux écritures dans la même seconde rendraient le même entier : on force
    # une date distincte plutôt que d'attendre une seconde pour rien.
    os.utime(bases / "logo.png", (avant + 10, avant + 10))
    assert logo.version_servie() != avant


# ---------------------------------------------------------------------------
# 6. RETOUR AU LOGO LUDOTEX
# ---------------------------------------------------------------------------
def test_retour_au_logo_ludotex(client, bases):
    from app import logo

    _poster(client, fichier=_image("PNG"))
    assert (bases / "logo.png").is_file()

    _connexion(client)
    reponse = client.post("/admin/identite/logo/retirer")
    assert reponse.status_code == 200
    assert "Logo retiré" in reponse.text
    for nom in ("logo.png", "favicon-192.png", "favicon-512.png"):
        assert not (bases / nom).exists(), nom

    servi = client.get("/image/logo.png")
    assert servi.content == logo.chemin_versionne("logo.png").read_bytes()


def test_retour_au_logo_ludotex_ne_touche_pas_aux_reglages_texte(client, bases):
    from app import db, services

    _poster(client, fichier=_image("PNG"), nom_association="Ludo du Bocage")
    _connexion(client)
    client.post("/admin/identite/logo/retirer")

    conn = db.get_connection()
    try:
        assert services.lire_parametre(
            conn, services.CLE_ASSOCIATION_NOM) == "Ludo du Bocage"
    finally:
        conn.close()


def test_retour_sans_logo_depose_ne_journalise_rien(client, bases):
    """Il ne s'est rien passé : le journal ne doit pas dire le contraire."""
    from app import journal

    _connexion(client)
    reponse = client.post("/admin/identite/logo/retirer")
    assert reponse.status_code == 200
    assert "rien à retirer" in reponse.text
    assert not [l for l in _lignes(journal.chemin_journal())
                if l.get("action") == "association_logo_modifie"]


def test_le_retrait_exige_une_session_admin(client, bases):
    """Sans session, on est redirigé vers la connexion — jamais exécuté."""
    _poster(client, fichier=_image("PNG"))
    client.get("/admin/logout")
    reponse = client.post("/admin/identite/logo/retirer", follow_redirects=False)
    assert reponse.status_code == 303
    assert (bases / "logo.png").is_file()


# ---------------------------------------------------------------------------
# 7. RÉSILIENCE
# ---------------------------------------------------------------------------
def test_la_route_image_repond_si_data_est_illisible(client, bases, monkeypatch):
    """
    `data/` peut devenir illisible (droits, montage perdu, disque plein). Le
    site doit alors afficher le logo LudoteX, pas une page cassée : la règle
    « ne jamais bloquer » vaut aussi pour une image.
    """
    from app import logo

    def _explose(*_args, **_kwargs):
        raise OSError("data/ illisible")

    monkeypatch.setattr(logo, "dossier_regle", _explose)
    reponse = client.get("/image/logo.png")
    assert reponse.status_code == 200
    assert reponse.content == logo.chemin_versionne("logo.png").read_bytes()


def test_les_pages_repondent_si_data_est_illisible(client, monkeypatch):
    """
    `logo_v()` est appelé dans `base.html`, donc au rendu de TOUTES les pages,
    page d'erreur 500 comprise : il ne doit jamais lever.
    """
    from app import logo

    def _explose(*_args, **_kwargs):
        raise OSError("data/ illisible")

    monkeypatch.setattr(logo, "dossier_regle", _explose)
    assert logo.version_servie() == int(
        logo.chemin_versionne("logo.png").stat().st_mtime)
    assert client.get("/").status_code == 200
    assert client.get("/catalogue").status_code == 200


def test_le_depot_exige_une_session_admin(client, bases):
    """Sans session admin, l'envoi est redirigé vers la connexion, pas traité."""
    reponse = client.post(
        "/admin/identite",
        data={"nom_association": "", "presentation": "", "contact": "",
              "depot_url": ""},
        files={"logo_fichier": ("logo.png", _image("PNG"), "image/png")},
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    assert not (bases / "logo.png").exists()


# ---------------------------------------------------------------------------
# 7. TRANSPARENCE — un logo à fond transparent n'imprime jamais de noir (3c)
# ---------------------------------------------------------------------------
def test_un_logo_depose_a_fond_transparent_ne_produit_pas_de_noir(client, bases):
    """
    `Image.open(...).convert("RGB")` SEUL rend NOIRS les pixels transparents
    d'un PNG RVBA — pas théorique, `app/static/img/logo.png` (et le meeple par
    défaut) en sont eux-mêmes. Le logo déposé est reconstruit en RGBA par
    `app.logo.enregistrer` avant d'être écrit (fond conservé) : on dépose donc
    une image RGBA à fond entièrement transparent et on vérifie que
    `charger_logo()` en tire une image dont les coins sont BLANCS, pas noirs.
    """
    from app.etiquettes import charger_logo

    tampon = io.BytesIO()
    # Carré opaque au centre sur un fond entièrement transparent.
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    for x in range(60, 140):
        for y in range(60, 140):
            im.putpixel((x, y), (10, 10, 10, 255))
    im.save(tampon, format="PNG")

    _poster(client, fichier=tampon.getvalue())

    image = charger_logo()
    assert image.mode == "RGB"
    for coin in ((0, 0), (image.width - 1, 0), (0, image.height - 1),
                (image.width - 1, image.height - 1)):
        assert image.getpixel(coin) == (255, 255, 255), coin
    # Le carré opaque, lui, est resté sombre : l'aplatissement ne délave pas
    # ce qui n'était pas transparent.
    assert image.getpixel((image.width // 2, image.height // 2))[0] < 30


def test_le_meeple_par_defaut_ne_produit_pas_de_noir(client, bases):
    """Même vérification sur le repli — le meeple par défaut est lui aussi RVBA."""
    from app.etiquettes import charger_logo

    image = charger_logo()
    for coin in ((0, 0), (image.width - 1, 0), (0, image.height - 1),
                (image.width - 1, image.height - 1)):
        assert image.getpixel(coin) == (255, 255, 255), coin
