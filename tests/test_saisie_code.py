"""
SAISIE AU CLAVIER DU CODE DE LA BOÎTE (lot code-boite-2).

Le secours clavier est le seul chemin où le code n'arrive pas d'un QR : il est
recopié à l'oeil depuis une étiquette. Ce fichier verrouille les trois choses
que ce lot rend confortables — le clavier proposé et sa porte de sortie, la
tolérance de frappe, et le fait que le formulaire n'existe plus qu'une fois —
plus la garde qui compte le plus : la tolérance NE FUIT PAS hors de la saisie.

Le catalogue de test porte volontairement les trois formes réelles du parc
(`001` numérique à zéro de tête, `E018` d'extension, `A0001`) et, dans une
fixture à part, une PAIRE AMBIGUË (`042` / `42`) qui n'existe dans aucun
catalogue réel : elle est là pour exercer la garde du « une seule
correspondance », que notre instance ne sollicite jamais.
"""

import html as html_mod
import re

import pytest


def _base(tmp_path, monkeypatch):
    """Bases isolées + application, patron de tests/test_routes.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
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
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
    conn.commit()
    return conn


def _client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Catalogue aux trois formes réelles de code, sans aucune ambiguïté."""
    conn = _base(tmp_path, monkeypatch)
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("001", "CATAN"), ("002", "DIXIT"), ("E018", "CATAN"), ("A0001", "DIXIT")],
    )
    conn.commit()
    conn.close()
    return _client()


@pytest.fixture
def client_ambigu(tmp_path, monkeypatch):
    """Catalogue FABRIQUÉ où « 042 » et « 42 » coexistent."""
    conn = _base(tmp_path, monkeypatch)
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("042", "CATAN"), ("42", "DIXIT")],
    )
    conn.commit()
    conn.close()
    return _client()


def _activer_rangement(client, texte="Étagère 2"):
    """Le mode rangement s'active par ce POST, comme dans tests/test_rangement.py."""
    return client.post("/scanner/rangement/activer",
                       data={"emplacement_texte": texte})


def _lien_bascule(page: str) -> str:
    """
    L'URL du lien de bascule du clavier, DÉSÉCHAPPÉE.

    Jinja écrit « &amp; » entre deux paramètres : rejouer l'href tel quel
    enverrait « &amp;lettres=1 », que Starlette lit comme un paramètre nommé
    « amp;lettres ». Le drapeau semblerait alors ne pas survivre — un faux
    négatif qui accuserait le serveur d'un défaut du test.

    Le lien est repéré par sa CLASSE dans le bloc du clavier, et non par la
    présence de « lettres=1 » : le lien de retour au pavé numérique, lui,
    retire ce paramètre au lieu de le poser.
    """
    trouve = re.search(
        r'<p class="scanner-aide saisie-clavier">\s*(?:<!--.*?-->\s*)?'
        r'<a class="lien" href="([^"]*)"', page, re.S)
    assert trouve, "aucun lien de bascule du clavier"
    return html_mod.unescape(trouve.group(1))


def _connexion():
    from app import db
    return db.get_connection()


def _champ_code(html: str) -> str:
    """
    Extrait la balise <input> du code — on N'Y CHERCHE PAS un mot au jugé.

    Le lot 1 s'est fait prendre en cherchant « checked » dans une page dont un
    script contenait déjà le mot. Ici « text » et « numeric » se croisent
    dans `type="text"` et `inputmode="numeric"` de la même balise : seule
    l'extraction distingue les deux.
    """
    trouve = re.search(r'<input[^>]*\bid="code"[^>]*>', html)
    assert trouve, "le champ de saisie du code est absent de la page"
    return trouve.group(0)


def _inputmode(html: str) -> str:
    champ = _champ_code(html)
    trouve = re.search(r'inputmode="([^"]*)"', champ)
    assert trouve, f"aucun inputmode sur {champ}"
    return trouve.group(1)


# ===========================================================================
# LE RÉSOLVEUR — services.resoudre_code_saisi
# ===========================================================================
@pytest.mark.parametrize("tape", ["001", "01", "1", " 001 ", "0001"])
def test_zeros_de_tete_menent_a_la_meme_boite(client, tape):
    from app import services

    conn = _connexion()
    try:
        assert services.resoudre_code_saisi(conn, tape) == ("001", [])
    finally:
        conn.close()


@pytest.mark.parametrize("tape", ["E018", "e018", "e 018", " E018"])
def test_la_casse_et_les_espaces_sont_tolerees(client, tape):
    from app import services

    conn = _connexion()
    try:
        assert services.resoudre_code_saisi(conn, tape) == ("E018", [])
    finally:
        conn.close()


def test_zeros_de_tete_apres_une_lettre(client):
    # « A0001 » : les zéros sont derrière la lettre, ils comptent tout autant.
    from app import services

    conn = _connexion()
    try:
        assert services.resoudre_code_saisi(conn, "a1")[0] == "A0001"
    finally:
        conn.close()


@pytest.mark.parametrize("tape", ["ZZZ999", "999", "E019", "", "   "])
def test_un_code_inconnu_reste_inconnu(client, tape):
    # La normalisation retrouve, elle ne rattrape pas : rien ne doit être
    # « corrigé » vers une boîte que le bénévole n'a pas demandée.
    from app import services

    conn = _connexion()
    try:
        assert services.resoudre_code_saisi(conn, tape) == (None, [])
    finally:
        conn.close()


def test_ambiguite_ne_devine_pas_et_nomme_les_candidats(client_ambigu):
    from app import services

    conn = _connexion()
    try:
        id_trouve, proches = services.resoudre_code_saisi(conn, "42")
        # « 42 » existe tel quel : l'essai EXACT est prioritaire, pas d'ambiguïté.
        assert (id_trouve, proches) == ("42", [])

        # « 0042 » n'existe pas : le repli ramène deux boîtes -> on redemande.
        id_trouve, proches = services.resoudre_code_saisi(conn, "0042")
        assert id_trouve is None
        assert proches == ["042", "42"]
    finally:
        conn.close()


def test_l_essai_exact_prime_sur_la_normalisation(client_ambigu):
    from app import services

    conn = _connexion()
    try:
        assert services.resoudre_code_saisi(conn, "042") == ("042", [])
    finally:
        conn.close()


def test_message_ambigu_liste_les_codes_au_lieu_de_nier_la_boite(client_ambigu):
    from app import services

    seul = services.message_code_introuvable("ZZZ999")
    assert "Aucune boîte" in seul

    plusieurs = services.message_code_introuvable("0042", ["042", "42"])
    assert "Aucune boîte" not in plusieurs      # ce serait faux : deux existent
    assert "042" in plusieurs and "42" in plusieurs


def test_liste_des_codes_proches_plafonnee(client):
    # Un mur de codes sur un écran de téléphone n'aide personne.
    from app import services

    trop = [f"{n:03d}" for n in range(1, 20)]
    message = services.message_code_introuvable("1", trop)
    # Un guillemet ouvrant par code listé, plus celui du code tapé.
    assert message.count("«") == services.MAX_CODES_PROCHES + 1


# ===========================================================================
# LA TOLÉRANCE NE FUIT PAS — c'est la garde qui compte
# ===========================================================================
def test_la_fiche_publique_reste_exacte(client):
    # /jeu/1 ne doit pas répondre pour la boîte 001 : cette valeur vient d'un
    # QR ou d'une URL partagée, une seconde adresse pour une même boîte
    # n'apporte rien et serait indexée deux fois.
    assert client.get("/jeu/001").status_code == 200
    assert client.get("/jeu/1").status_code == 404
    assert client.get("/jeu/e018").status_code == 404


def test_l_etiquette_admin_reste_exacte(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-admin-de-test-32car")
    client.post("/admin/login", data={"mot_de_passe": "mot-de-passe-admin-de-test-32car"})
    assert client.get("/admin/etiquette/001.png").status_code == 200
    assert client.get("/admin/etiquette/1.png").status_code == 404


def test_le_scan_camera_reste_exact(client):
    # /scanner/ranger est la cible du QR décodé : exact par construction. Le
    # tolérer flou masquerait un vrai problème (QR d'un autre système,
    # étiquette d'une autre instance) derrière une correspondance approchée.
    _activer_rangement(client)
    r = client.get("/scanner/ranger", params={"code": "1"})
    assert r.status_code == 200
    assert "Aucune boîte" in r.text


# ===========================================================================
# LES TROIS CHEMINS OÙ UN HUMAIN TAPE
# ===========================================================================
@pytest.mark.parametrize("tape,attendu", [
    ("1", "/pret/001?saisi=1"), ("01", "/pret/001?saisi=1"),
    ("001", "/pret/001?saisi=1"), ("  001 ", "/pret/001?saisi=1"),
    ("e018", "/pret/E018?saisi=1"), ("a1", "/pret/A0001?saisi=1"),
])
def test_saisie_redirige_vers_le_code_canonique(client, tape, attendu):
    # La redirection porte le code du CATALOGUE, jamais celui qui a été tapé :
    # sinon l'URL affichée et l'historique portent un identifiant inexistant.
    # Égalité STRICTE, jamais un `startswith` : c'est elle qui verrouille le
    # code canonique, l'acquis du lot 2.
    r = client.get("/scanner/saisie", params={"code": tape}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == attendu


def test_saisie_ambigue_ne_redirige_pas(client_ambigu):
    r = client_ambigu.get("/scanner/saisie", params={"code": "0042"},
                          follow_redirects=False)
    assert r.status_code == 200
    assert "Plusieurs boîtes" in r.text
    assert "0042" in _champ_code(r.text)      # champ prérempli, prêt à corriger


def test_saisie_en_mode_rangement_tolere_aussi(client):
    # Sans cela, taper « 1 » ouvrirait la fiche mais échouerait à ranger.
    _activer_rangement(client)
    r = client.get("/scanner/saisie", params={"code": "1"})
    assert r.status_code == 200
    assert "rangé en Étagère 2" in r.text

    conn = _connexion()
    try:
        place = conn.execute(
            "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire='001'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert place == "Étagère 2"


def test_saisie_en_mode_rangement_code_inconnu_reste_non_bloquante(client):
    _activer_rangement(client)
    r = client.get("/scanner/saisie", params={"code": "ZZZ999"})
    assert r.status_code == 200
    assert "Aucune boîte" in r.text
    assert 'data-rangement="1"' in r.text          # le mode tient toujours


def test_transfert_saisie_tolere_et_redirige_vers_le_canonique(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert/saisie", params={"code": "2"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/pret/001/transfert/002"


def test_transfert_saisie_code_inconnu_reste_non_bloquant(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert/saisie", params={"code": "ZZZ999"})
    assert r.status_code == 200
    assert "Aucune boîte" in r.text
    assert "ZZZ999" in _champ_code(r.text)


# ===========================================================================
# LE CLAVIER, ET SA PORTE DE SORTIE
# ===========================================================================
def test_le_pave_numerique_est_le_defaut(client):
    # 97 % des codes sont des chiffres : c'est le clavier qui doit s'ouvrir.
    assert _inputmode(client.get("/scanner").text) == "numeric"


def test_type_text_et_jamais_number(client):
    # `type="number"` perdrait les zéros de tête, ajouterait des flèches et
    # lirait « e » comme un exposant : le pire choix pour un identifiant.
    champ = _champ_code(client.get("/scanner").text)
    assert 'type="text"' in champ
    assert 'type="number"' not in champ


def test_aucune_validation_de_forme_sur_le_champ(client):
    # `inputmode` est une SUGGESTION : un clavier matériel ou un collage
    # doivent pouvoir envoyer ce qu'ils veulent, la route ne refuse jamais.
    champ = _champ_code(client.get("/scanner").text)
    assert "pattern=" not in champ
    assert "maxlength=" not in champ


def test_la_porte_de_sortie_est_un_vrai_lien(client):
    # Pas un <span> cliquable : atteignable au clavier, compréhensible seul.
    html = client.get("/scanner").text
    trouve = re.search(r'<a class="lien" href="([^"]*lettres=1[^"]*)"[^>]*>(.*?)</a>',
                       html, re.S)
    assert trouve, "aucun lien de bascule vers le clavier complet"
    assert "lettre" in trouve.group(2)


def test_le_mode_lettres_ouvre_le_clavier_complet(client):
    html = client.get("/scanner", params={"lettres": "1"}).text
    assert _inputmode(html) == "text"


@pytest.mark.parametrize("valeur", ["0", "", "oui", "2", "true"])
def test_seule_la_valeur_1_allume_le_mode_lettres(client, valeur):
    # Même rigueur que le ?debug=1 du scanner : un « lettres=0 » collé dans une
    # barre d'adresse ne doit pas changer le clavier.
    html = client.get("/scanner", params={"lettres": valeur}).text
    assert _inputmode(html) == "numeric"


def test_le_mode_lettres_voyage_en_champ_cache(client):
    # Un formulaire GET n'envoie QUE ses champs nommés : sans ce champ caché,
    # le drapeau serait perdu à la soumission.
    html = client.get("/scanner", params={"lettres": "1"}).text
    assert re.search(r'<input type="hidden" name="lettres" value="1">', html)


# --- Le piège du lot : le drapeau survit-il au réaffichage après erreur ? ---
def _ecrans_de_reaffichage(client):
    """
    Les CINQ routes GET qui rendent un écran portant le formulaire de saisie.

    Deux l'OUVRENT (/scanner, /pret/<id>/transfert), trois le RÉAFFICHENT
    après un code refusé (/scanner/saisie, /scanner/ranger,
    /pret/<id>/transfert/saisie) : c'est sur ces dernières que le drapeau se
    perdrait, à l'instant exact où le bénévole corrige son code.

    Un SIXIÈME chemin de rendu existe, hors de cette liste parce qu'il n'est
    pas un GET : le POST /pret/<id>/transfert/<id_nouveau> qui affiche l'écran
    d'escalade « déjà sortie ». Sa bascule est testée à part
    (`test_la_bascule_depuis_l_ecran_d_escalade_ouvre_les_lettres`), le lien
    ne pouvant y être rejoué depuis l'URL courante.
    """
    client.post("/pret/001/preter")
    return [
        ("/scanner", {}),
        ("/scanner/saisie", {"code": "ZZZ999"}),
        ("/scanner/ranger", {"code": "ZZZ999"}),
        ("/pret/001/transfert", {}),
        ("/pret/001/transfert/saisie", {"code": "ZZZ999"}),
    ]


def test_le_drapeau_survit_a_l_erreur_sur_toutes_les_routes(client):
    _activer_rangement(client)
    for url, params in _ecrans_de_reaffichage(client):
        r = client.get(url, params={**params, "lettres": "1"})
        assert r.status_code == 200, url
        assert _inputmode(r.text) == "text", url
        assert 'name="lettres" value="1"' in r.text, url


def test_le_pave_numerique_est_le_defaut_sur_toutes_les_routes(client):
    _activer_rangement(client)
    for url, params in _ecrans_de_reaffichage(client):
        r = client.get(url, params=params)
        assert _inputmode(r.text) == "numeric", url


def test_la_bascule_conserve_le_code_deja_tape(client):
    # Basculer ne doit pas faire perdre ce qu'on vient de taper : c'est au
    # moment où l'on découvre qu'il faut des lettres qu'on en a le plus besoin.
    r = client.get("/scanner/saisie", params={"code": "ZZZ999"})
    suite = client.get(_lien_bascule(r.text))
    assert _inputmode(suite.text) == "text"
    assert "ZZZ999" in _champ_code(suite.text)


def test_la_bascule_du_transfert_ne_quitte_pas_l_ecran(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert/saisie", params={"code": "ZZZ999"})
    suite = client.get(_lien_bascule(r.text))
    assert suite.status_code == 200
    assert "Transfert de pochette" in suite.text
    assert _inputmode(suite.text) == "text"


# ===========================================================================
# UN SEUL DOMICILE
# ===========================================================================
def test_le_formulaire_de_saisie_n_existe_qu_une_fois():
    """
    Le garde-fou du POINT 1 : ce formulaire a vécu en double, à dix mots près,
    et les trois défauts corrigés par ce lot y avaient survécu parce qu'on ne
    corrigeait qu'une copie sur deux. Un troisième écran qui le recopierait
    doit faire rougir la suite, pas passer inaperçu.
    """
    from pathlib import Path

    gabarits = Path(__file__).resolve().parents[1] / "app" / "templates"
    porteurs = sorted(
        chemin.name for chemin in gabarits.glob("*.html")
        if 'class="saisie-manuelle"' in chemin.read_text(encoding="utf-8")
    )
    assert porteurs == ["_saisie_manuelle.html"], (
        "le formulaire de saisie doit vivre dans le seul fragment "
        f"_saisie_manuelle.html, trouvé aussi dans : {porteurs}"
    )


def test_le_placeholder_montre_une_forme_plausible(client):
    """
    « ex. 00472 » enseignait un format à cinq chiffres qui n'existe dans aucun
    catalogue réel, au moment précis où l'on apprend à lire ce code. L'exemple
    doit venir du catalogue livré (exemples/catalogue-exemple.csv).
    """
    from pathlib import Path

    champ = _champ_code(client.get("/scanner").text)
    trouve = re.search(r'placeholder="ex\. ([^"]+)"', champ)
    assert trouve, f"placeholder absent ou reformulé : {champ}"
    exemple = trouve.group(1)

    csv = Path(__file__).resolve().parents[1] / "exemples" / "catalogue-exemple.csv"
    codes = [ligne.split(";")[0] for ligne in
             csv.read_text(encoding="utf-8").splitlines()[1:] if ligne.strip()]
    assert exemple in codes, (
        f"« {exemple} » n'est pas un code du catalogue d'exemple : "
        "le placeholder enseignerait un format inventé"
    )


def test_la_bascule_depuis_l_ecran_d_escalade_ouvre_les_lettres(client):
    """
    Le sixième chemin de rendu : l'écran d'escalade « déjà sortie », affiché
    en réponse à un POST.

    `request.url.path` y désigne la route d'ÉCRITURE — la rejouer en GET
    ouvrirait l'écran de confirmation, pas celui-ci. Le lien doit donc viser
    l'écran de transfert. Le bloc d'escalade est perdu au passage (il naît du
    POST), mais le bénévole obtient ses lettres : sans cela un iPhone n'aurait
    aucun moyen de taper un code d'extension depuis cet écran, ce qui serait
    un blocage net.
    """
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    r = client.post("/pret/001/transfert/002")
    assert "déjà sortie" in r.text                    # on est bien sur l'escalade

    suite = client.get(_lien_bascule(r.text))
    assert suite.status_code == 200
    assert "Transfert de pochette" in suite.text      # écran de scan, pas la confirmation
    assert _inputmode(suite.text) == "text"


def test_la_bascule_ramene_aussi_au_pave_numerique(client):
    """
    La porte va dans les DEUX sens.

    L'écran se réaffiche depuis sa propre URL après chaque code accepté : sans
    retour, une seule boîte d'extension condamnerait au clavier complet tout
    le reste d'une séance de rangement — le pavé large étant précisément ce
    qui rend supportable le geste répété des centaines de fois.
    """
    _activer_rangement(client)
    lettres = client.get("/scanner", params={"lettres": "1"})
    assert _inputmode(lettres.text) == "text"

    retour = client.get(_lien_bascule(lettres.text))
    assert _inputmode(retour.text) == "numeric"
    assert 'name="lettres" value="1"' not in retour.text   # le champ caché part aussi


def test_la_bascule_conserve_les_autres_parametres(client):
    # Le mode diagnostic du scanner ne doit pas tomber parce qu'on a demandé
    # des lettres : la bascule ne touche QU'À son propre paramètre.
    r = client.get("/scanner", params={"debug": "1"})
    assert "debug=1" in _lien_bascule(r.text)


# ===========================================================================
# LE BANDEAU D'IDENTITÉ (lot code-boite-3)
#
# La saisie manuelle est le SEUL chemin qui puisse atterrir sur une mauvaise
# boîte EXISTANTE : un scan ne se trompe pas de code, une frappe si. Taper
# « 1 » au lieu de « 2 » ouvre un écran parfaitement valide, au nom de jeu
# plausible, dont le geste suivant est un tap sur « Prêter ». Le bandeau nomme
# la boîte avant toute action, et offre le rattrapage en un tap.
# ===========================================================================
_MARQUEUR_BANDEAU = "Code tapé au clavier"


def _bandeau_saisie(page: str) -> str:
    """
    La SECTION du bandeau d'identité, extraite — jamais un mot cherché dans la
    page entière (piège des lots 1 et 2 : `checked` vivait dans un script,
    et `resultat-info` sert à cinq autres bandeaux de cet écran).

    Renvoie "" quand le bandeau est absent.
    """
    for bloc in re.findall(
        r'<section class="resultat resultat-info">(.*?)</section>', page, re.S
    ):
        if _MARQUEUR_BANDEAU in bloc:
            return bloc
    return ""


def test_le_bandeau_apparait_sur_une_arrivee_clavier(client):
    assert _bandeau_saisie(client.get("/pret/001", params={"saisi": "1"}).text)


def test_le_bandeau_est_absent_d_une_fiche_ouverte_normalement(client):
    # Le cas du QR scanné, de loin le plus fréquent : aucun bruit ajouté.
    assert _bandeau_saisie(client.get("/pret/001").text) == ""


def test_le_bandeau_nomme_le_jeu_et_le_code(client):
    bandeau = _bandeau_saisie(client.get("/pret/002", params={"saisi": "1"}).text)
    assert "Dixit" in bandeau
    assert "002" in bandeau


def test_le_bandeau_porte_un_lien_de_rattrapage_cliquable(client):
    """
    Le rattrapage est à UN TAP, et c'est un vrai lien : atteignable au clavier,
    listé par un lecteur d'écran, et qui mène réellement quelque part.
    """
    bandeau = _bandeau_saisie(client.get("/pret/001", params={"saisi": "1"}).text)
    cible = re.search(r'<a class="lien" href="([^"]+)"', bandeau)
    assert cible, bandeau
    href = html_mod.unescape(cible.group(1))     # Jinja échappe « & » en « &amp; »
    assert client.get(href).status_code == 200


@pytest.mark.parametrize("valeur", ["0", "oui", "", "true", "01"])
def test_seule_la_valeur_1_allume_le_bandeau(client, valeur):
    # Même rigueur que `?debug=1` et `?lettres=1` : un paramètre collé dans une
    # barre d'adresse ne doit pas allumer un mode à moitié.
    page = client.get("/pret/001", params={"saisi": valeur}).text
    assert _bandeau_saisie(page) == ""


def test_le_bandeau_disparait_apres_l_action(client):
    """
    Un « code tapé » encore affiché au-dessus de « Pochette n° 7 » serait du
    bruit au moment où l'écran doit dire UNE SEULE chose.
    """
    avant = client.get("/pret/001", params={"saisi": "1"})
    assert _bandeau_saisie(avant.text)

    apres = client.post("/pret/001/preter")
    assert apres.status_code == 200
    assert "Pochette n°" in apres.text            # l'action a bien eu lieu
    assert _bandeau_saisie(apres.text) == ""


def test_le_chemin_complet_depuis_la_frappe(client):
    # De la touche au bandeau : « 2 » tapé mène à la fiche de 002, qui NOMME
    # le jeu et le code CANONIQUE — celui qui est imprimé sur la boîte tenue
    # en main, pas les touches qu'on vient d'appuyer.
    r = client.get("/scanner/saisie", params={"code": "2"})
    assert r.status_code == 200
    bandeau = _bandeau_saisie(r.text)
    assert "Dixit" in bandeau
    assert "002" in bandeau


def test_le_mode_rangement_ne_declenche_pas_le_bandeau(client):
    # Il a déjà sa confirmation nommant le jeu : « <jeu> rangé en <lieu> ».
    _activer_rangement(client)
    r = client.get("/scanner/saisie", params={"code": "1"})
    assert "rangé en Étagère 2" in r.text
    assert _MARQUEUR_BANDEAU not in r.text


def test_le_transfert_ne_declenche_pas_le_bandeau(client):
    # Le transfert a son propre écran de confirmation, qui nomme les DEUX jeux
    # et la pochette conservée : un bandeau y dirait deux fois la même chose.
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert/saisie", params={"code": "2"})
    assert r.status_code == 200
    assert _MARQUEUR_BANDEAU not in r.text


def test_les_deux_bandeaux_coexistent_et_l_identite_vient_en_premier(client):
    """
    Savoir qu'on tient la bonne boîte précède la lecture de ses
    avertissements — et une identité reléguée sous une liste de signalements
    ne serait pas lue.
    """
    conn = _connexion()
    try:
        conn.execute(
            "INSERT INTO signalements (id_exemplaire, id_categorie, texte, cree_le) "
            "VALUES ('001', NULL, 'Boîte déchirée', '2026-09-10T09:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    page = client.get("/pret/001", params={"saisi": "1"}).text
    assert _bandeau_saisie(page)
    assert "Boîte déchirée" in page
    assert page.index(_MARQUEUR_BANDEAU) < page.index("Signalements en cours")
