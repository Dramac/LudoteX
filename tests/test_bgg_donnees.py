"""
LES DONNÉES BOARDGAMEGEEK SUR LA FICHE D'UN JEU (lot fiche-2).

Ce que ce fichier vérifie, et pourquoi :

1. **La lecture d'une réponse `thing`** : un jeu, une extension, les 0 que BGG
   met pour « non renseigné », un identifiant inconnu, et ce qu'on refuse
   d'analyser (DOCTYPE, XML cassé, réponse démesurée).
2. **Chaque issue d'échec** a son code et son message, et **le jeton
   n'apparaît nulle part** : ni dans le message, ni dans une page, une
   adresse de redirection, une ligne du journal — vérifié avec un jeton
   factice reconnaissable, échec par échec.
3. **La copie datée en base**, clé par identifiant BGG, et une base ou une
   archive d'avant qui la gagne vide.
4. **L'affichage « BGG : »** dans l'éditeur seulement, jamais à la création ;
   **la reprise** pré-remplit sans rien écrire, et l'enregistrement passe par
   le chemin habituel.
5. **La récupération à l'enregistrement** d'un identifiant nouveau, qu'un
   échec de BGG n'empêche jamais.
6. **Le lot borné** de la liste des jeux : borne de temps, reprise, arrêt sur
   429.
7. **Le lien public** : décoché par défaut, coché, et la mention BGG.
8. **AUCUN APPEL RÉSEAU À L'AFFICHAGE** : les pages s'ouvrent avec un client
   BGG qui lèverait s'il était appelé. (Le garde-fou de toute la suite, qui
   fait échouer une vraie connexion, vit dans tests/conftest.py.)

Le réseau n'est jamais touché : `urllib.request.urlopen` est remplacé par un
double qui rejoue des réponses écrites ici.
"""

import http.client
import io
import re
import socket
import sqlite3
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from app import bgg_api, journal, services
from tests.test_fiche_jeu import (MOT_DE_PASSE, _conn, _executer,  # noqa: F401
                                  _formulaire, _instantane, _journal,
                                  _titre, anonyme, bases, client)

# Un jeton FACTICE, reconnaissable : il ne doit ressortir nulle part.
JETON = "JETON-FACTICE-Xq7-ne-doit-jamais-sortir"

# Réponse `thing` RÉDUITE, sur le modèle d'une vraie réponse du cache de la
# collecte du 2026-10-10 (hors dépôt) : mêmes balises, mêmes valeurs
# pour ces quatre fiches réelles, sans descriptif, images ni statistiques.
# - 1927 Munchkin : un jeu, plage de durée, auteur suffixé « (I) » par BGG ;
# - 225018 : une extension, durée minimale seule, âge à 0 ;
# - 30571 Katana : âge et durées à 0 (« non renseigné ») ;
# - 28897 L'Arbre de Mako : auteur « (Uncredited) ».
XML_THING = b"""<?xml version="1.0" encoding="utf-8"?>
<items termsofuse="https://boardgamegeek.com/xmlapi/termsofuse">
<item type="boardgame" id="1927">
  <name type="primary" sortindex="1" value="Munchkin"/>
  <name type="alternate" sortindex="1" value="Munchkin 1+2"/>
  <yearpublished value="2001"/>
  <minplayers value="3"/><maxplayers value="6"/>
  <playingtime value="120"/><minplaytime value="60"/><maxplaytime value="120"/>
  <minage value="10"/>
  <link type="boardgamedesigner" id="1" value="Steve Jackson (I)"/>
  <link type="boardgamepublisher" id="2" value="Steve Jackson Games"/>
</item>
<item type="boardgameexpansion" id="225018">
  <name type="primary" sortindex="1" value="Mars Attacks: The Dice Game &#8211; Area 51"/>
  <minplayers value="3"/><maxplayers value="6"/>
  <playingtime value="0"/><minplaytime value="20"/><maxplaytime value="0"/>
  <minage value="0"/>
</item>
<item type="boardgame" id="30571">
  <name type="primary" sortindex="1" value="Katana"/>
  <minplayers value="2"/><maxplayers value="2"/>
  <playingtime value="0"/><minplaytime value="0"/><maxplaytime value="0"/>
  <minage value="0"/>
  <link type="boardgamedesigner" id="3" value="Stephen Lawrence"/>
</item>
<item type="boardgame" id="28897">
  <name type="primary" sortindex="1" value="L'Arbre de Mako"/>
  <minplayers value="2"/><maxplayers value="4"/>
  <playingtime value="20"/><minplaytime value="20"/><maxplaytime value="20"/>
  <minage value="0"/>
  <link type="boardgamedesigner" id="4" value="(Uncredited)"/>
</item>
</items>"""


def _xml_catane(age="12", maxj="4"):
    """La fiche n° 13 telle qu'un test la veut, pour comparer à CATANE."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<items><item type="boardgame" id="13">
  <name type="primary" sortindex="1" value="CATAN"/>
  <minplayers value="3"/><maxplayers value="{maxj}"/>
  <playingtime value="120"/><minplaytime value="60"/><maxplaytime value="120"/>
  <minage value="{age}"/>
  <link type="boardgamedesigner" id="11" value="Klaus Teuber"/>
</item></items>""".encode()


# ---------------------------------------------------------------------------
# Double de `urllib.request.urlopen`
# ---------------------------------------------------------------------------
class FausseReponse:
    def __init__(self, corps: bytes, status: int = 200):
        self._corps = io.BytesIO(corps)
        self.status = status

    def read(self, n=-1):
        return self._corps.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FauxBGG:
    """
    Rejoue une réponse par appel. Une réponse est : des octets (200), un
    entier (statut HTTP : 202 en réponse normale, les autres en HTTPError),
    ou une exception à lever. Garde chaque requête reçue.
    """

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.requetes: list[urllib.request.Request] = []

    def __call__(self, requete, timeout=None):
        self.requetes.append(requete)
        assert timeout == bgg_api.DELAI_APPEL_S
        reponse = self.reponses.pop(0)
        if isinstance(reponse, BaseException):
            raise reponse
        if isinstance(reponse, int):
            if reponse == 202:
                return FausseReponse(b"", 202)
            raise urllib.error.HTTPError(requete.full_url, reponse, "refus",
                                         {}, io.BytesIO(b"<html>refus</html>"))
        return FausseReponse(reponse)


@pytest.fixture
def faux_bgg(monkeypatch):
    """Pose le jeton factice et installe un faux BGG : `faux_bgg(*réponses)`."""
    monkeypatch.setenv("BGG_TOKEN", JETON)

    def installer(*reponses):
        faux = FauxBGG(*reponses)
        monkeypatch.setattr(urllib.request, "urlopen", faux)
        return faux
    return installer


@pytest.fixture
def bgg_interdit(monkeypatch):
    """Jeton posé, et tout appel à BGG FAIT ÉCHOUER le test."""
    monkeypatch.setenv("BGG_TOKEN", JETON)

    def interdit(*args, **kwargs):
        raise AssertionError("appel réseau à BGG pendant un affichage")
    monkeypatch.setattr(urllib.request, "urlopen", interdit)
    monkeypatch.setattr(bgg_api, "recuperer", interdit)
    monkeypatch.setattr(bgg_api, "recuperer_lot", interdit)


def _copier(fiches):
    conn = _conn()
    try:
        return services.enregistrer_fiches_bgg(conn, fiches)
    finally:
        conn.close()


def _copie(bgg_id):
    conn = _conn()
    try:
        return services.lire_fiche_bgg(conn, bgg_id)
    finally:
        conn.close()


def _copie_catane(**changements):
    fiche = {"type": "boardgame", "nom": "CATAN", "nb_joueurs_min": 3,
             "nb_joueurs_max": 4, "age_min": 12, "duree_min": 60,
             "duree_max": 120, "auteurs": "Klaus Teuber",
             # Lue sans image (lot fiche-4) : une copie complète, que la
             # récupération par lots ne redemande pas.
             "vignette": ""}
    fiche.update(changements)
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _copier({13: fiche})


def _indication(page, champ):
    trouve = re.search(r'<p class="indication-bgg" id="' + champ + r'-bgg">(.*?)</p>',
                       page, re.S)
    return " ".join(re.sub(r"<[^>]+>", "", trouve.group(1)).split()) if trouve else None


def _attribut(page, ident, attribut):
    balise = re.search(r'<(?:input|select|textarea)[^>]*\bid="' + ident + r'"[^>]*>',
                       page, re.S).group(0)
    trouve = re.search(attribut + r'="([^"]*)"', balise)
    return trouve.group(1) if trouve else None


def _formulaire_affiche(page):
    """Ce que le navigateur posterait depuis le formulaire « Modifier la fiche »."""
    form = re.search(r'<form method="post" action="/admin/jeu/[^"]+/modifier".*?</form>',
                     page, re.S).group(0)
    donnees = {}
    for balise in re.findall(r"<input[^>]*>", form):
        nom = re.search(r'name="([^"]+)"', balise)
        if nom:
            valeur = re.search(r'value="([^"]*)"', balise)
            donnees[nom.group(1)] = valeur.group(1) if valeur else ""
    for nom, contenu in re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S):
        donnees[nom] = contenu
    for nom, options in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form, re.S):
        choisi = re.search(r'<option value="([^"]*)" selected', options)
        donnees[nom] = choisi.group(1) if choisi else ""
    return {k: v.replace("&#39;", "'").replace("&amp;", "&") for k, v in donnees.items()}


# ===========================================================================
# 1. LECTURE D'UNE RÉPONSE — fonctions pures
# ===========================================================================
def test_lecture_d_un_jeu():
    fiche = bgg_api.lire_reponse(XML_THING)[1927]
    assert fiche == {
        "bgg_id": 1927, "type": "boardgame", "nom": "Munchkin",
        "nb_joueurs_min": 3, "nb_joueurs_max": 6, "age_min": 10,
        "duree_min": 60, "duree_max": 120,
        # « (I) » distingue deux homonymes chez BGG : jamais sur une boîte.
        "auteurs": "Steve Jackson",
        # Première parution (lot fiche-3) : montrée sur les candidats.
        "annee": 2001,
        # Pas de <thumbnail> dans cette réponse : lue, sans image (lot fiche-4).
        "vignette": "",
    }


def test_lecture_d_une_extension_et_des_zeros():
    fiches = bgg_api.lire_reponse(XML_THING)
    extension = fiches[225018]
    assert extension["type"] == bgg_api.TYPE_EXTENSION
    assert extension["nom"] == "Mars Attacks: The Dice Game – Area 51"
    # minplaytime 20, maxplaytime et playingtime à 0 : seule la borne basse.
    assert (extension["duree_min"], extension["duree_max"]) == (20, None)
    assert extension["age_min"] is None and extension["auteurs"] is None
    katana = fiches[30571]
    assert (katana["age_min"], katana["duree_min"], katana["duree_max"]) == (None, None, None)
    assert katana["nb_joueurs_min"] == katana["nb_joueurs_max"] == 2


def test_uncredited_n_est_pas_un_auteur():
    assert bgg_api.lire_reponse(XML_THING)[28897]["auteurs"] is None


def test_la_duree_se_replie_sur_la_duree_moyenne():
    xml = (b'<items><item type="boardgame" id="5"><playingtime value="45"/>'
           b'<minplaytime value="0"/></item></items>')
    fiche = bgg_api.lire_reponse(xml)[5]
    assert (fiche["duree_min"], fiche["duree_max"]) == (45, 45)
    assert fiche["nom"] is None


def test_les_auteurs_s_arretent_au_dernier_nom_entier():
    liens = "".join(f'<link type="boardgamedesigner" id="{i}" value="Auteur numéro {i:03d} de ce jeu"/>'
                    for i in range(20)).encode()
    fiche = bgg_api.lire_reponse(b'<items><item type="boardgame" id="5">' + liens + b"</item></items>")[5]
    assert len(fiche["auteurs"]) <= bgg_api.LONGUEUR_AUTEURS
    assert fiche["auteurs"].endswith("de ce jeu")


@pytest.mark.parametrize("corps", [
    b'<?xml version="1.0"?><!DOCTYPE items [<!ENTITY a "aaaa">]><items>&a;</items>',
    b'<items><item id="1"',                         # XML coupé
    b"<html><body>Service unavailable</body></html>",
    b'<error><message>Rate limit exceeded.</message></error>',
    b"",
])
def test_reponse_illisible(corps):
    with pytest.raises(bgg_api.EchecBGG) as echec:
        bgg_api.lire_reponse(corps)
    assert echec.value.code == "illisible"


def test_un_identifiant_absent_de_la_reponse_est_inconnu(faux_bgg):
    faux_bgg(XML_THING)
    fiches = bgg_api.recuperer([1927, 999999999])
    assert fiches[999999999] is None
    assert fiches[1927]["nom"] == "Munchkin"


def test_reponse_trop_grosse_refusee_avant_analyse(faux_bgg, monkeypatch):
    monkeypatch.setattr(bgg_api, "TAILLE_MAX_OCTETS", 1000)
    faux_bgg(XML_THING)
    with pytest.raises(bgg_api.EchecBGG) as echec:
        bgg_api.recuperer([1927])
    assert echec.value.code == "illisible"


# ===========================================================================
# 2. L'APPEL, ET CHAQUE ISSUE D'ÉCHEC — sans jamais laisser sortir le jeton
# ===========================================================================
def test_le_jeton_part_en_en_tete_et_seulement_la(faux_bgg):
    faux = faux_bgg(XML_THING)
    bgg_api.recuperer([28897, 1927, 1927])
    requete, = faux.requetes
    assert requete.get_header("Authorization") == f"Bearer {JETON}"
    assert JETON not in requete.full_url
    # Des entiers triés, dédoublonnés, et rien d'autre dans l'adresse.
    assert requete.full_url == "https://boardgamegeek.com/xmlapi2/thing?id=1927,28897"
    assert "LudoteX" in requete.get_header("User-agent")


def test_plus_de_vingt_identifiants_refuses_avant_tout_appel(faux_bgg):
    faux = faux_bgg()
    with pytest.raises(ValueError):
        bgg_api.recuperer(list(range(1, 22)))
    assert faux.requetes == []


def test_sans_jeton_aucun_appel(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("appel sans jeton")
    monkeypatch.setattr(urllib.request, "urlopen", interdit)
    assert not bgg_api.disponible()
    with pytest.raises(bgg_api.EchecBGG) as echec:
        bgg_api.recuperer([13])
    assert echec.value.code == "sans_jeton"


ECHECS = [
    (401, "jeton_refuse"),
    (403, "bloque"),
    (202, "attendre"),
    (429, "attendre"),
    (500, "panne"),
    (503, "panne"),
    (404, "panne"),
    (urllib.error.URLError(socket.gaierror("nom introuvable")), "panne"),
    (TimeoutError("délai dépassé"), "panne"),
    (ConnectionResetError("connexion réinitialisée"), "panne"),
    (http.client.IncompleteRead(b"<items>"), "panne"),
    (b"<html>pas du XML</html>", "illisible"),
]


@pytest.mark.parametrize("reponse,code", ECHECS)
def test_chaque_issue_a_son_code_et_ne_cite_jamais_le_jeton(faux_bgg, reponse, code):
    faux_bgg(reponse)
    with pytest.raises(bgg_api.EchecBGG) as echec:
        bgg_api.recuperer([13])
    assert echec.value.code == code
    assert str(echec.value) == bgg_api.MESSAGES_ECHEC[code]
    # La chaîne d'exceptions est coupée : aucune trace ne transporte la
    # requête, son adresse ou son en-tête.
    assert echec.value.__cause__ is None
    assert echec.value.__context__ is None or echec.value.__suppress_context__
    assert JETON not in repr(echec.value) and JETON not in str(echec.value.args)


def test_aucun_message_ne_cite_le_jeton_ni_un_chemin():
    for message in bgg_api.MESSAGES_ECHEC.values():
        assert "BGG_TOKEN" not in message and "/" not in message and ".env" not in message


@pytest.mark.parametrize("reponse,code", ECHECS)
def test_bouton_recuperer_chaque_echec_sans_fuite(client, faux_bgg, _journal_isole,
                                                  reponse, code):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    faux_bgg(reponse)
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/jeu/CATANE?ok=&bgg_echec={code}"
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert bgg_api.MESSAGES_ECHEC[code].replace("'", "&#39;") in page.text
    assert 'role="alert"' in page.text
    assert _copie(13) is None                  # rien n'est écrit
    ligne, = _journal(_journal_isole, "bgg_fiche_recuperee")
    assert ligne["ok"] is False and ligne["detail"] == code
    for texte in (r.headers["location"], page.text,
                  _journal_isole.read_text(encoding="utf-8")):
        assert JETON not in texte


# ===========================================================================
# 3. LA COPIE DATÉE EN BASE
# ===========================================================================
def test_la_copie_est_datee_et_remplacee(bases):
    assert _copier({1927: bgg_api.lire_reponse(XML_THING)[1927], 5: None}) == (1, 1)
    copie = _copie(1927)
    assert copie["trouve"] == 1 and copie["nom"] == "Munchkin"
    assert copie["recupere_le"].startswith("20")
    assert _copie(5)["trouve"] == 0 and _copie(5)["nom"] is None
    # Une seconde récupération remplace, et une fiche devenue connue l'est.
    _copier({5: {"nom": "Cinq", "nb_joueurs_min": 2}})
    assert _copie(5)["trouve"] == 1 and _copie(5)["nom"] == "Cinq"


def test_deux_titres_partagent_une_copie_et_changer_d_id_ne_detruit_rien(client, faux_bgg):
    _executer("UPDATE titres SET bgg_id = 13")
    _copie_catane()
    conn = _conn()
    try:
        assert services.ids_bgg_a_recuperer(conn) == []
    finally:
        conn.close()
    faux_bgg(XML_THING)                       # 1927 n'a pas de copie : récupérée
    client.post("/admin/jeu/DOBBLE/modifier",
                data=_formulaire("DOBBLE", bgg="1927"))
    assert _copie(13) is not None and _copie(1927)["nom"] == "Munchkin"


def test_base_d_avant_gagne_la_table_vide(tmp_path, monkeypatch):
    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.execute("CREATE TABLE titres (reference_titre TEXT PRIMARY KEY, nom TEXT NOT NULL)")
    ancienne.execute("INSERT INTO titres VALUES ('CATAN', 'Catan')")
    ancienne.commit()
    ancienne.close()
    from app import db
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        db.init_db(conn)
        assert conn.execute("SELECT COUNT(*) FROM fiches_bgg").fetchone()[0] == 0
        assert not services.copies_bgg_presentes(conn)
        assert not services.lire_bgg_lien_public(conn)
    finally:
        conn.close()


def test_restaurer_une_archive_d_avant_la_table(bases, tmp_path):
    """Une archive faite avant ce lot n'a pas `fiches_bgg` : elle la gagne vide."""
    from app import sauvegarde
    _copie_catane()
    archive = sauvegarde.creer_zip_sauvegarde()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        contenus = {nom: zf.read(nom) for nom in zf.namelist()}
    pret = tmp_path / "pret-avant.db"
    pret.write_bytes(contenus["pret-jeux.db"])
    c = sqlite3.connect(pret)
    c.execute("DROP TABLE fiches_bgg")
    c.commit()
    c.close()
    contenus["pret-jeux.db"] = pret.read_bytes()
    chemin_zip = tmp_path / "avant.zip"
    with zipfile.ZipFile(chemin_zip, "w") as zf:
        for nom, contenu in contenus.items():
            zf.writestr(nom, contenu)

    sauvegarde.restaurer_zip_sauvegarde(chemin_zip)

    assert _copie(13) is None
    assert _titre()["bgg_id"] == 13 and _titre()["nom"] == "Catane"


def test_la_copie_n_entre_pas_dans_l_export_du_catalogue(bases):
    _copie_catane()
    conn = _conn()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    texte = " ".join(entetes) + " ".join(str(v) for l in lignes for v in l.values())
    assert "CATAN" not in texte.replace("Catane", "")   # le nom BGG, en capitales
    assert "120" not in texte                             # sa durée maximale


# ===========================================================================
# 4. AFFICHAGE « BGG : » ET REPRISE
# ===========================================================================
def test_aucun_appel_reseau_a_l_affichage(client, anonyme, bgg_interdit):
    _copie_catane()
    conn = _conn()
    try:
        services.ecrire_bgg_lien_public(conn, True)
    finally:
        conn.close()
    for url in ("/admin/jeu/CATANE", "/admin/jeu/CATANE?bgg=reprendre",
                "/admin/jeux", "/admin/identite", "/jeu/001", "/apropos",
                "/catalogue"):
        assert client.get(url).status_code == 200, url


def test_indications_presentes_differentes_et_plage(client):
    _copie_catane()
    page = client.get("/admin/jeu/CATANE").text
    assert _indication(page, "nb_joueurs_min") == "BGG : 3 — identique"
    assert _indication(page, "nb_joueurs_max") == "BGG : 4 — identique"
    assert _indication(page, "age_min") == "BGG : 12 — différent"
    assert _indication(page, "duree_min") == "BGG : 60 à 120 — différent"
    assert _indication(page, "auteur") == "BGG : Klaus Teuber — identique"
    # « différent » se lit sans la couleur : un mot, en gras.
    assert "<strong>différent</strong>" in page
    # Jamais pour le nom, l'année, le descriptif, l'éditeur.
    for champ in ("nom", "annee_edition", "descriptif", "editeur", "categorie"):
        assert _indication(page, champ) is None
    assert "Données BGG du " in page


def test_indication_absente_quand_bgg_se_tait(client):
    _copie_catane(nb_joueurs_max=None, auteurs=None)
    page = client.get("/admin/jeu/CATANE").text
    assert _indication(page, "nb_joueurs_max") is None
    assert _indication(page, "auteur") is None
    assert _attribut(page, "nb_joueurs_max", "aria-describedby") is None


def test_indication_case_vide(client):
    _copie_catane()
    _executer("UPDATE titres SET auteur = NULL WHERE reference_titre = 'CATANE'")
    assert _indication(client.get("/admin/jeu/CATANE").text, "auteur") == \
        "BGG : Klaus Teuber — la case est vide"


def test_sans_copie_rien_de_bgg_sous_les_cases(client):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    page = client.get("/admin/jeu/CATANE").text
    assert "indication-bgg" not in page
    assert "Copier les valeurs BGG dans les cases" not in page


def test_l_indication_est_reliee_a_sa_case_en_plus_de_l_erreur(client):
    _copie_catane()
    page = client.get("/admin/jeu/CATANE").text
    assert _attribut(page, "age_min", "aria-describedby") == "age_min-bgg"
    r = client.post("/admin/jeu/CATANE/modifier",
                    data=_formulaire("CATANE", age_min="-3"))
    assert r.status_code == 400
    assert _attribut(r.text, "age_min", "aria-describedby") == "age_min-erreur age_min-bgg"
    assert _attribut(r.text, "age_min", "aria-invalid") == "true"
    assert 'id="age_min-erreur"' in r.text and 'id="age_min-bgg"' in r.text


def test_l_ecran_de_creation_ne_montre_rien_de_bgg(client):
    _copie_catane()
    page = client.get("/admin/jeu-nouveau").text
    assert "indication-bgg" not in page and "BGG" not in page


def test_lien_lisible_sous_la_case(client):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    assert "Ouvrir la fiche n° 13 sur BoardGameGeek" in client.get("/admin/jeu/CATANE").text
    _copie_catane()
    page = client.get("/admin/jeu/CATANE").text
    lien = re.search(r'<a [^>]*href="https://boardgamegeek.com/boardgame/13"[^>]*>(.*?)</a>',
                     page, re.S)
    assert "Ouvrir <cite>CATAN</cite> sur BoardGameGeek" in lien.group(1)
    assert 'rel="noopener noreferrer"' in lien.group(0)
    assert "nouvel onglet" in lien.group(1)
    # Un seul lien vers BGG sur la page.
    assert page.count('href="https://boardgamegeek.com/boardgame/13"') == 1


def test_reprise_sans_ecriture_ni_journal(client, _journal_isole, bgg_interdit):
    _copie_catane(nb_joueurs_max=None)
    avant = _instantane()
    page = client.get("/admin/jeu/CATANE?bgg=reprendre").text
    assert _instantane() == avant
    for action in ("jeu_modifie", "bgg_fiche_recuperee"):
        assert _journal(_journal_isole, action) == []
    # Valeurs BGG à la place des nôtres pour les champs comparés...
    assert _attribut(page, "age_min", "value") == "12"
    assert _attribut(page, "duree_min", "value") == "60"          # la borne basse
    # ... une valeur BGG absente ne vide pas notre case...
    assert _attribut(page, "nb_joueurs_max", "value") == "4"
    # ... et le reste garde la base.
    assert _attribut(page, "nom", "value") == "Catane"
    assert _attribut(page, "editeur", "value") == "Kosmos"
    assert _indication(page, "age_min") == "BGG : 12 — repris de BGG, pas encore enregistré"
    assert _indication(page, "nb_joueurs_min") == "BGG : 3 — identique"
    assert "rien n'est encore enregistré" in page.replace("&#39;", "'")
    assert re.search(r'role="status">Valeurs BGG reprises', page)


def test_reprise_sans_rien_a_reprendre(client):
    _copie_catane(age_min=10, duree_min=75)
    page = client.get("/admin/jeu/CATANE?bgg=reprendre").text.replace("&#39;", "'")
    assert "Les valeurs BGG sont déjà celles de la fiche" in page


def test_reprise_sans_copie_n_affiche_rien(client):
    page = client.get("/admin/jeu/CATANE?bgg=reprendre").text
    assert _attribut(page, "age_min", "value") == "10"
    assert "repris de BGG" not in page


def test_reprise_puis_enregistrement_par_le_chemin_habituel(client, _journal_isole):
    _copie_catane()
    page = client.get("/admin/jeu/CATANE?bgg=reprendre").text
    donnees = _formulaire_affiche(page)
    assert donnees["age_min"] == "12" and donnees["bgg"] == "13"
    r = client.post("/admin/jeu/CATANE/modifier", data=donnees, follow_redirects=False)
    # L'âge et la durée sont imprimés : l'étiquette périmée est annoncée.
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=enregistre&etiquette=1"
    titre = _titre()
    assert (titre["age_min"], titre["duree_min"]) == (12, 60)
    assert titre["modifie_le"] is not None
    assert len(_journal(_journal_isole, "jeu_modifie")) == 1
    assert "n'est plus à jour" in client.get(r.headers["location"]).text


def test_boutons_selon_jeton_et_copie(client, monkeypatch):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    page = client.get("/admin/jeu/CATANE").text
    assert "Interroger BoardGameGeek" not in page        # ni jeton, ni copie
    monkeypatch.setenv("BGG_TOKEN", JETON)
    page = client.get("/admin/jeu/CATANE").text
    assert "Interroger BoardGameGeek" in page
    assert "Aucune donnée BGG n'a encore été récupérée" in page.replace("&#39;", "'")
    assert "Copier les valeurs BGG dans les cases" not in page
    assert client.get("/admin/jeu/DOBBLE").text.count("Interroger BoardGameGeek") == 0
    _copie_catane()
    monkeypatch.delenv("BGG_TOKEN")
    page = client.get("/admin/jeu/CATANE").text
    # Sans jeton, la copie reste lisible et reprenable : rien n'y est réseau.
    assert "Copier les valeurs BGG dans les cases" in page and "Interroger BoardGameGeek" not in page


# ===========================================================================
# 5. RÉCUPÉRER UNE FICHE — bouton, et dans la foulée d'un enregistrement
# ===========================================================================
def test_bouton_recuperer(client, faux_bgg, _journal_isole):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    avant = _instantane()
    faux_bgg(_xml_catane())
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=bgg_recupere&bgg=completer#modifier"
    assert _instantane() == avant                # aucune valeur du titre ne change
    assert _copie(13)["age_min"] == 12
    ligne, = _journal(_journal_isole, "bgg_fiche_recuperee")
    assert ligne["ok"] is True and ligne["ref"] == "CATANE" and ligne["objet"] == "Catane"
    page = client.get(r.headers["location"]).text
    assert "Données BGG récupérées" in page and _indication(page, "age_min")


def test_bouton_recuperer_identifiant_inconnu(client, faux_bgg, _journal_isole):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    faux_bgg(b'<?xml version="1.0"?><items termsofuse="x"></items>')
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=&bgg_echec=inconnu"
    assert _copie(13)["trouve"] == 0
    page = client.get(r.headers["location"]).text.replace("&#39;", "'")
    assert "ne connaît aucune fiche à ce numéro" in page
    assert "BoardGameGeek ne connaissait aucune fiche à ce numéro le" in page
    assert "Copier les valeurs BGG dans les cases" not in page
    assert _journal(_journal_isole, "bgg_fiche_recuperee")[0]["detail"] == "inconnu"


def test_bouton_recuperer_sans_jeton_ou_sans_identifiant(client, faux_bgg, monkeypatch):
    faux = faux_bgg()
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok="          # pas d'identifiant
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    monkeypatch.delenv("BGG_TOKEN")
    r = client.post("/admin/jeu/CATANE/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=&bgg_echec=sans_jeton"
    assert faux.requetes == []
    assert client.post("/admin/jeu/INCONNU/bgg/recuperer").status_code == 404


def test_identifiant_nouveau_recupere_a_l_enregistrement(client, faux_bgg, _journal_isole):
    faux = faux_bgg(_xml_catane())
    r = client.post("/admin/jeu/CATANE/modifier",
                    data=_formulaire("CATANE", bgg="https://boardgamegeek.com/boardgame/13/catan"),
                    follow_redirects=False)
    # Copie obtenue : ses valeurs remplissent les cases vides, comme après
    # « C'est ce jeu », sans rien écrire de plus.
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=enregistre_bgg&bgg=completer#modifier"
    assert len(faux.requetes) == 1
    assert _titre()["bgg_id"] == 13 and _copie(13)["nom"] == "CATAN"
    assert len(_journal(_journal_isole, "bgg_fiche_recuperee")) == 1
    page = client.get(r.headers["location"]).text
    assert "Fiche enregistrée, et données BGG récupérées" in page


@pytest.mark.parametrize("reponse,code", [(500, "panne"), (429, "attendre"),
                                          (401, "jeton_refuse")])
def test_un_echec_de_bgg_n_empeche_jamais_l_enregistrement(client, faux_bgg, reponse, code):
    faux_bgg(reponse)
    r = client.post("/admin/jeu/CATANE/modifier",
                    data=_formulaire("CATANE", bgg="13", age_min="12"),
                    follow_redirects=False)
    assert r.headers["location"] == f"/admin/jeu/CATANE?ok=enregistre&etiquette=1&bgg_echec={code}"
    assert _titre()["bgg_id"] == 13 and _titre()["age_min"] == 12
    assert _copie(13) is None
    page = client.get(r.headers["location"]).text
    assert "Fiche enregistrée." in page
    assert bgg_api.MESSAGES_ECHEC[code].replace("'", "&#39;") in page
    assert "Interroger BoardGameGeek" in page      # le bouton reste là
    assert JETON not in page and JETON not in r.headers["location"]


def test_pas_de_recuperation_si_l_identifiant_ne_change_pas_ou_a_deja_sa_copie(client, faux_bgg):
    faux = faux_bgg()
    _copie_catane()
    # Identifiant inchangé, autre champ modifié.
    client.post("/admin/jeu/CATANE/modifier", data=_formulaire("CATANE", bgg="13", editeur="Filosofia"))
    # Identifiant nouveau pour DOBBLE, mais copie déjà là.
    client.post("/admin/jeu/DOBBLE/modifier", data=_formulaire("DOBBLE", bgg="13"))
    # Identifiant retiré.
    client.post("/admin/jeu/CATANE/modifier", data=_formulaire("CATANE", bgg=""))
    assert faux.requetes == []


def test_sans_jeton_l_enregistrement_ne_tente_rien(client, monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("appel sans jeton")
    monkeypatch.setattr(urllib.request, "urlopen", interdit)
    r = client.post("/admin/jeu/CATANE/modifier", data=_formulaire("CATANE", bgg="13"),
                    follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=enregistre"


# ===========================================================================
# 6. LE LOT BORNÉ — liste des jeux
# ===========================================================================
class Horloge:
    def __init__(self, duree_appel=1.0):
        self.t = 0.0
        self.duree_appel = duree_appel
        self.pauses = []

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.pauses.append(s)
        self.t += s


def _xml_pour(ids):
    return ("<items>" + "".join(
        f'<item type="boardgame" id="{i}"><name type="primary" value="Jeu {i}"/>'
        f'<minplayers value="2"/></item>' for i in ids) + "</items>").encode()


def test_lot_borne_par_le_temps(faux_bgg, monkeypatch):
    horloge = Horloge()
    ids = list(range(1, 300))

    def appel(lot):
        horloge.t += horloge.duree_appel
        return {i: {"bgg_id": i} for i in lot}
    monkeypatch.setattr(bgg_api, "recuperer", appel)
    fiches, echec = bgg_api.recuperer_lot(ids, horloge=horloge, dormir=horloge.dormir)
    assert echec is None
    # Appels d'une seconde, pauses de 5 s, borne 40 s, délai 15 s : 5 appels.
    assert len(fiches) == 100 and list(fiches) == ids[:100]
    assert horloge.pauses == [bgg_api.PAUSE_ENTRE_APPELS_S] * 4
    # Dans le pire des cas, la requête finit avant la borne, loin des 60 s de nginx.
    assert horloge.t <= bgg_api.BORNE_LOT_S < 60


def test_lot_le_pire_cas_reste_sous_la_borne(monkeypatch):
    horloge = Horloge(duree_appel=bgg_api.DELAI_APPEL_S)

    def appel(lot):
        horloge.t += horloge.duree_appel
        return {i: None for i in lot}
    monkeypatch.setattr(bgg_api, "recuperer", appel)
    bgg_api.recuperer_lot(list(range(1, 300)), horloge=horloge, dormir=horloge.dormir)
    assert horloge.t <= bgg_api.BORNE_LOT_S


def test_lot_arrete_sur_429_en_gardant_l_acquis(faux_bgg):
    horloge = Horloge()
    faux_bgg(_xml_pour(range(1, 21)), 429)
    fiches, echec = bgg_api.recuperer_lot(list(range(1, 60)), horloge=horloge,
                                          dormir=horloge.dormir)
    assert echec.code == "attendre"
    assert sorted(fiches) == list(range(1, 21))


def _relier(n):
    conn = _conn()
    try:
        for i in range(n):
            ref = f"JEU{i:03d}"
            conn.execute("INSERT INTO titres (reference_titre, nom, bgg_id) VALUES (?, ?, ?)",
                         (ref, f"Jeu {i}", 1000 + i))
        conn.commit()
    finally:
        conn.close()


def test_route_du_lot_reprenable_et_arret_propre(client, faux_bgg, monkeypatch, _journal_isole):
    monkeypatch.setattr(bgg_api, "PAUSE_ENTRE_APPELS_S", 0)
    monkeypatch.setattr(bgg_api, "BORNE_LOT_S", 1000)
    _relier(45)
    page = " ".join(client.get("/admin/jeux").text.split())
    assert "45 fiches BoardGameGeek reliées à vos jeux" in page and "Récupérer les données BGG manquantes" in page

    # Premier clic : 20 fiches, puis BGG demande d'attendre.
    faux_bgg(_xml_pour(range(1000, 1019)), 429)          # 1019 inconnu de BGG
    r = client.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeux?bgg_lot=20&bgg_inconnues=1&bgg_echec=attendre"
    page = " ".join(client.get(r.headers["location"]).text.split())
    assert "20 fiches récupérées (dont 1 inconnue de BoardGameGeek), 25 restantes." in page
    assert "BoardGameGeek demande d&#39;attendre" in page
    ligne, = _journal(_journal_isole, "bgg_lot_recupere")
    assert ligne["objet"] == "20 récupérées, 25 restantes" and ligne["ok"] is False
    assert ligne["detail"] == "attendre"

    # Second clic : reprend là où on en était, sans redemander les 20 premières.
    faux = faux_bgg(_xml_pour(range(1020, 1040)), _xml_pour(range(1040, 1045)))
    r = client.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeux?bgg_lot=25&bgg_inconnues=0"
    assert "id=1020," in faux.requetes[0].full_url
    page = " ".join(client.get(r.headers["location"]).text.split())
    assert "25 fiches récupérées, 0 restante." in page
    assert "Tous les jeux reliés à BoardGameGeek ont leurs données BGG." in page
    assert "Récupérer les données BGG manquantes" not in page
    # Plus rien à faire : le bloc disparaît de la liste.
    assert "Données BoardGameGeek" not in client.get("/admin/jeux").text
    for texte in (page, _journal_isole.read_text(encoding="utf-8")):
        assert JETON not in texte


def test_lot_sans_jeton(client, monkeypatch):
    _relier(3)
    assert "Récupérer les données BGG manquantes" not in client.get("/admin/jeux").text
    r = client.post("/admin/jeux/bgg/recuperer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeux?bgg_echec=sans_jeton"


def test_parametres_d_url_inattendus_sans_erreur(client, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    assert client.get("/admin/jeux?bgg_lot=abc&bgg_echec=<script>").status_code == 200
    page = client.get("/admin/jeu/CATANE?bgg_echec=<script>&bgg=autre&ok=x").text
    assert "<script>" not in page.split("<body")[1].split("<script")[0]


# ===========================================================================
# 7. ACCÈS — les routes nouvelles sont refusées sans session
# ===========================================================================
@pytest.mark.parametrize("url", ["/admin/jeu/CATANE/bgg/recuperer", "/admin/jeux/bgg/recuperer"])
def test_routes_nouvelles_refusees_sans_session(anonyme, monkeypatch, url):
    def interdit(*a, **k):
        raise AssertionError("appel sans session")
    monkeypatch.setenv("BGG_TOKEN", JETON)
    monkeypatch.setattr(urllib.request, "urlopen", interdit)
    r = anonyme.post(url, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_reprise_refusee_sans_session(anonyme):
    r = anonyme.get("/admin/jeu/CATANE?bgg=reprendre", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


# ===========================================================================
# 8. LE LIEN PUBLIC ET LA MENTION BGG
# ===========================================================================
def _identite(client, **champs):
    return client.post("/admin/identite", data=champs)


def test_lien_public_decoche_par_defaut(client, anonyme):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    page = client.get("/admin/identite").text
    case = re.search(r'<input type="checkbox" id="bgg_lien_public"[^>]*>', page).group(0)
    assert "checked" not in case
    assert "boardgamegeek" not in anonyme.get("/jeu/001").text.lower()
    assert "Powered by BGG" not in anonyme.get("/apropos").text


def test_lien_public_coche(client, _journal_isole):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    r = _identite(client, bgg_lien_public="1")
    assert r.status_code == 200 and "Lien BoardGameGeek affiché" in r.text
    assert "checked" in re.search(r'<input type="checkbox" id="bgg_lien_public"[^>]*>', r.text).group(0)
    ligne, = _journal(_journal_isole, "bgg_lien_public_modifie")
    assert ligne["objet"] == "affiché"
    _identite(client, bgg_lien_public="1")              # inchangé : rien de plus
    assert len(_journal(_journal_isole, "bgg_lien_public_modifie")) == 1

    client.cookies.clear()
    fiche = client.get("/jeu/001").text
    lien = re.search(r'<a [^>]*href="https://boardgamegeek.com/boardgame/13"[^>]*>(.*?)</a>',
                     fiche, re.S)
    assert "Voir ce jeu sur BoardGameGeek" in lien.group(1)
    assert 'rel="noopener noreferrer"' in lien.group(0)
    # Un jeu sans fiche BGG n'a pas de lien.
    assert "boardgamegeek" not in client.get("/jeu/003").text.lower()


def test_decocher_retire_le_lien(client, _journal_isole):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _identite(client, bgg_lien_public="1")
    r = _identite(client)
    assert "Lien BoardGameGeek retiré" in r.text
    assert _journal(_journal_isole, "bgg_lien_public_modifie")[-1]["objet"] == "masqué"
    assert "boardgamegeek" not in client.get("/jeu/001").text.lower()


def test_un_refus_garde_la_case(client):
    r = _identite(client, bgg_lien_public="1", contact="pas une adresse")
    assert r.status_code == 400
    assert "checked" in re.search(r'<input type="checkbox" id="bgg_lien_public"[^>]*>', r.text).group(0)
    conn = _conn()
    try:
        assert not services.lire_bgg_lien_public(conn)   # rien n'est enregistré
    finally:
        conn.close()


@pytest.mark.parametrize("cause", ["jeton", "case", "copie"])
def test_mention_des_que_l_instance_utilise_bgg(anonyme, monkeypatch, cause):
    if cause == "jeton":
        monkeypatch.setenv("BGG_TOKEN", JETON)
    elif cause == "case":
        conn = _conn()
        try:
            services.ecrire_bgg_lien_public(conn, True)
        finally:
            conn.close()
    else:
        _copier({13: {"nom": "CATAN"}})
    page = anonyme.get("/apropos").text
    img = re.search(r'<img[^>]*powered-by-bgg\.png[^>]*>', page).group(0)
    assert 'alt="Powered by BGG' in img
    assert '<a href="https://boardgamegeek.com"' in page
    assert "<strong>BoardGameGeek</strong>" in page
    assert JETON not in page
    # Le logo est servi par l'application (la CSP refuse toute image d'ailleurs).
    r = anonyme.get("/static/img/powered-by-bgg.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_le_logo_bgg_est_signale_comme_marque():
    notice = Path("app/static/img/powered-by-bgg.NOTICE.txt").read_text(encoding="utf-8")
    assert "n'est PAS couvert par la licence GPLv3" in notice
    assert "marque" in notice


# ===========================================================================
# 9. MODE FORMATION, VOCABULAIRE, GARDE-FOU
# ===========================================================================
def test_formation_sans_jeton_n_offre_rien(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MODE_FORMATION", True)
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _relier(2)
    for url in ("/admin/jeu/CATANE", "/admin/jeux"):
        page = client.get(url)
        assert page.status_code == 200
        assert "Récupérer les données BGG" not in page.text   # ni la liste…
        assert "Interroger BoardGameGeek" not in page.text    # …ni la fiche


def test_la_reinitialisation_de_formation_vide_les_copies(bases):
    from app import formation
    _copier({13: {"nom": "CATAN"}})
    conn = _conn()
    try:
        formation._vider_base_pret(conn)
        assert not services.copies_bgg_presentes(conn)
    finally:
        conn.close()


def test_actions_nouvelles_au_vocabulaire_et_a_la_conception():
    conception = Path("docs/conception-journal.md").read_text(encoding="utf-8")
    for action in ("bgg_fiche_recuperee", "bgg_lot_recupere", "bgg_lien_public_modifie"):
        assert action in journal.ACTIONS
        assert action in conception


def test_le_garde_fou_fait_echouer_une_vraie_connexion():
    """Le filet de tests/conftest.py : une connexion sortante lève, bruyamment."""
    # Ni OSError ni URLError : le client BGG ne la confondrait pas avec une panne.
    with pytest.raises(RuntimeError, match="refusée en test") as leve:
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    assert not isinstance(leve.value, OSError)
    with pytest.raises(RuntimeError, match="refusée en test"):
        urllib.request.urlopen("https://boardgamegeek.com/xmlapi2/thing?id=13", timeout=1)
    assert "BGG_TOKEN" not in __import__("os").environ


def test_le_client_reste_le_seul_module_reseau():
    """`app/bgg.py` reste pur ; aucun autre module d'app/ n'ouvre de connexion."""
    import ast
    for fichier in Path("app").rglob("*.py"):
        if fichier == Path("app/bgg_api.py"):
            continue
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, (ast.Import, ast.ImportFrom)):
                noms = ([a.name for a in noeud.names] if isinstance(noeud, ast.Import)
                        else [noeud.module or ""])
                for nom in noms:
                    assert nom not in ("urllib.request", "http.client", "socket",
                                       "requests", "httpx"), (fichier, nom)


# ===========================================================================
# 10. ACCESSIBILITÉ — mesurée, pas supposée
# ===========================================================================
def _css():
    return Path("app/static/css/style.css").read_text(encoding="utf-8")


def test_contraste_du_texte_bgg_sur_le_fond_reel_de_la_carte():
    css = _css()
    gris = re.search(r"--gris:\s*(#[0-9a-fA-F]{6})", css).group(1)
    fond_carte = re.search(r"\.carte \{\s*background:\s*(#[0-9a-fA-F]{3,6})", css).group(1)
    fond_carte = "#ffffff" if fond_carte.lower() in ("#fff", "#ffffff") else fond_carte
    assert re.search(r"\.indication-bgg \{[^}]*color:\s*var\(--gris\)", css)
    assert services.contraste(gris, fond_carte) >= 4.5


def test_focus_des_nouveaux_boutons_et_liens():
    """
    Les boutons et liens ajoutés n'ont pas de style de focus propre : ils
    héritent du contour global en --primaire-lisible, garanti à 4,5:1 sur
    les fonds clairs (donc au-delà des 3:1 demandés), y compris sur le
    blanc de la carte.
    """
    css = _css()
    assert re.search(r"a:focus-visible,\s*button:focus-visible,[^{]*\{\s*outline: 3px solid var\(--primaire-lisible\)", css)
    for classe in ("indication-bgg", "bloc-bgg", "actions-bgg", "mention-bgg"):
        assert not re.search(r"\." + classe + r"[^{]*:focus", css)
    lisible = services.couleur_lisible_sur_clair(services.COULEUR_ASSOCIATION_DEFAUT,
                                                 ("#ffffff",))
    assert services.contraste(lisible, "#ffffff") >= 3
