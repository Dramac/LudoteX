"""
JEUX ÉPHÉMÈRES : IMPRIMER, NOMMER, PRÊTER (série comptoir, lot 8a).

Une feuille de QR vierges, imprimée d'avance ; au premier scan d'un QR vierge,
un bénévole nomme le jeu ; ensuite la boîte se prête comme les autres. Ce
fichier vérifie :

1. la forme du code (`est_code_ephemere`, `code_ephemere_saisi`, le fragment
   SQL), et qu'aucun code du catalogue ne s'y rapproche ;
2. la feuille à imprimer (admin seulement, bornes, PDF, réimpression) ;
3. le QR vierge : bénévole, visiteur, code inconnu non éphémère, saisie ;
4. nommer : titre existant intact, titre nouveau, refus, concurrence ;
5. corriger le nom tant que la boîte n'est jamais sortie ;
6. la boîte nommée : prêt, retour, transfert, échange, tournoi, démo, stats ;
7. ce qui l'ignore : export, étiquettes en lot ; l'import la met de côté ;
8. le journal (porte : jamais le texte refusé).

Catalogue de départ : « Catane » (CATANE, boîtes 001 et 002, catégorie
Stratégie) et « Dobble » (DOBBLE, boîte 003).
"""

import json
import re
import sqlite3

import pytest

from app import journal, services

MOT_DE_PASSE = "mot-de-passe-de-test"
JETON = "jeton-de-test-ephemeres-0123456789"
BASE_URL = "https://ludotex.test"
CONSIGNE = "Le nom du jeu seulement — jamais\n         le nom de la personne qui l'a apporté."


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.setenv("PRET_TOKEN", JETON)
    monkeypatch.setenv("BASE_URL", BASE_URL)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom, type_jeu, age_min, editeur) "
                 "VALUES ('CATANE', 'Catane', 'Jeu', 10, 'Kosmos')")
    services.ecrire_categories_titre(conn, "CATANE", "Stratégie")
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DOBBLE', 'Dobble')")
    conn.executemany("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                     [("001", "CATANE"), ("002", "CATANE"), ("003", "DOBBLE")])
    conn.commit()
    conn.close()
    return tmp_path


def _client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture
def visiteur(bases):
    return _client()


@pytest.fixture
def benevole(bases):
    c = _client()
    c.get(f"/acces?jeton={JETON}", follow_redirects=False)
    return c


@pytest.fixture
def admin(bases):
    c = _client()
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return c


def _conn():
    from app import db
    return db.get_connection()


def _lire(sql, params=()):
    conn = _conn()
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _titre(ref):
    conn = _conn()
    try:
        return services.get_titre(conn, ref)
    finally:
        conn.close()


def _nommer(client, code="X01", nom="Catane", mode="nommer"):
    return client.post(f"/pret/{code}/nom", data={"nom": nom, "mode": mode})


def _journal(chemin, action=None):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l]
    return [l for l in lignes if action is None or l["action"] == action]


# ===========================================================================
# 1. LA FORME DU CODE
# ===========================================================================
@pytest.mark.parametrize("code", ["X01", "X09", "X10", "X99"])
def test_formes_ephemeres(code):
    assert services.est_code_ephemere(code)


@pytest.mark.parametrize("code", [
    "X00", "X1", "X001", "x01", " X01", "X01 ", "X1A", "XX1", "X100",
    "E001", "E01", "A0001", "042", "001", "X01-2026-1", "", None])
def test_formes_non_ephemeres(code):
    assert not services.est_code_ephemere(code)


@pytest.mark.parametrize("saisie, attendu", [
    ("X01", "X01"), ("x1", "X01"), ("X1", "X01"), (" x 01 ", "X01"),
    ("X001", "X01"), ("x99", "X99"), ("X0", None), ("X100", None),
    ("042", None), ("E001", None), ("A0001", None), ("X01-2026-1", None), ("", None)])
def test_saisie_au_clavier(saisie, attendu):
    assert services.code_ephemere_saisi(saisie) == attendu


def test_code_imprime_et_bornes():
    assert services.code_ephemere(1) == "X01"
    assert services.code_ephemere(99) == "X99"
    for n in (0, 100, -1):
        with pytest.raises(ValueError):
            services.code_ephemere(n)


def test_aucun_code_du_catalogue_ne_se_rapproche_d_un_code_ephemere():
    """Chiffres seuls, « A… » et « E… » : leur clé ne commence jamais par X."""
    for code in ("001", "042", "999", "0", "A0001", "A0121", "E001", "E018"):
        assert services.code_ephemere_saisi(code) is None
    cles_eph = {services.cle_saisie(services.code_ephemere(n)) for n in range(1, 100)}
    for code in ("001", "042", "A0001", "E001"):
        assert services.cle_saisie(code) not in cles_eph


def test_l_administration_ne_fabrique_jamais_un_code_ephemere(bases):
    conn = _conn()
    try:
        assert not services.est_code_ephemere(services.prochain_id_exemplaire(conn))
    finally:
        conn.close()


def test_le_fragment_sql_s_accorde_avec_la_fonction():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE exemplaires (id_exemplaire TEXT)")
    codes = ["X00", "X01", "X55", "X99", "X1", "x01", "X001", "X01-2026-1",
             "E001", "A0001", "042", "XA1"]
    conn.executemany("INSERT INTO exemplaires VALUES (?)", [(c,) for c in codes])
    gardes = {r[0] for r in conn.execute(
        f"SELECT id_exemplaire FROM exemplaires e WHERE {services.condition_non_ephemere('e')}")}
    assert gardes == {c for c in codes if not services.est_code_ephemere(c)}


# ===========================================================================
# 2. LA FEUILLE À IMPRIMER
# ===========================================================================
def _feuille(client, premier="1", dernier="5", **extra):
    return client.post("/admin/etiquettes/ephemeres",
                       data={"premier": premier, "dernier": dernier, **extra},
                       follow_redirects=False)


def test_feuille_reservee_a_l_admin(benevole, visiteur):
    for c in (benevole, visiteur):
        r = _feuille(c)
        assert r.headers.get("content-type") != "application/pdf"
        assert r.status_code in (303, 302, 401, 403)


def test_feuille_produit_un_pdf_des_codes_demandes(admin, monkeypatch):
    from app.routes import admin as routes_admin
    vus = {}

    def planche(exemplaires, base_url, logo=None, **options):
        vus["codes"] = [e["id_exemplaire"] for e in exemplaires]
        vus["options"] = options
        return b"%PDF-factice"

    monkeypatch.setattr(routes_admin, "planche_pdf", planche)
    r = _feuille(admin, "3", "7")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "qr-ephemeres-X03-X07.pdf" in r.headers["content-disposition"]
    assert vus["codes"] == ["X03", "X04", "X05", "X06", "X07"]
    assert vus["options"]["ephemere"] is True
    # Rien n'est écrit en base : la feuille est du papier.
    assert _lire("SELECT id_exemplaire FROM exemplaires WHERE id_exemplaire LIKE 'X%'") == []


def test_feuille_vraie_planche_pdf(admin):
    r = _feuille(admin, "X01", "x5")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


@pytest.mark.parametrize("premier, dernier, message", [
    ("", "5", "Indiquez le premier et le dernier numéro"),
    ("a", "5", "Indiquez le premier et le dernier numéro"),
    ("0", "5", "Les numéros des QR éphémères vont de 1 à 99."),
    ("1", "100", "Les numéros des QR éphémères vont de 1 à 99."),
    ("8", "3", "Le premier numéro doit être plus petit que le dernier."),
])
def test_feuille_bornes_refusees_proprement(admin, premier, dernier, message):
    r = _feuille(admin, premier, dernier)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert message in r.text
    assert f'name="premier" min="1"' in r.text


def test_un_code_deja_nomme_se_reimprime(admin, monkeypatch):
    _nommer(admin, "X02", "Catane")
    page = admin.get("/admin/etiquettes").text
    assert "Déjà nommés : X02." in page
    assert "Imprimer une feuille de QR éphémères" in page
    r = _feuille(admin, "2", "2")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_etiquette_ephemere_meme_taille_qu_une_etiquette_du_catalogue():
    from app.etiquettes import image_etiquette
    vierge = image_etiquette(f"{BASE_URL}/jeu/X01", {"id_exemplaire": "X01", "nom": ""},
                             ephemere=True, afficher_code=False)
    normale = image_etiquette(f"{BASE_URL}/jeu/042", {"id_exemplaire": "042", "nom": "Catane"})
    assert vierge.size == normale.size


def test_reimpression_d_une_boite_ephemere_est_vierge(admin, monkeypatch):
    from app.routes import admin as routes_admin
    appels = []
    vraie = routes_admin.image_etiquette

    def espion(*a, **k):
        appels.append(k.get("ephemere"))
        return vraie(*a, **k)

    monkeypatch.setattr(routes_admin, "image_etiquette", espion)
    _nommer(admin, "X04", "Catane")
    assert admin.get("/admin/etiquette/X04.png").status_code == 200
    assert admin.get("/admin/etiquette/001.png").status_code == 200
    assert appels == [True, False]


# ===========================================================================
# 3. LE QR VIERGE
# ===========================================================================
def test_benevole_scanne_un_qr_vierge_voit_le_formulaire(benevole):
    r = benevole.get("/pret/X01")
    assert r.status_code == 200
    assert "Ce QR éphémère n'est rattaché à aucun jeu." in r.text
    assert "Nommer ce jeu" in r.text
    assert '<label for="nom-jeu">Nom du jeu</label>' in r.text
    assert 'aria-describedby="nom-jeu-consigne"' in r.text
    assert CONSIGNE in r.text
    assert '<datalist id="noms-catalogue">' in r.text
    assert '<option value="Catane">' in r.text and '<option value="Dobble">' in r.text
    assert len(re.findall(r"<h1[ >]", r.text)) == 1


def test_benevole_ouvre_le_qr_vierge_par_la_fiche(benevole):
    r = benevole.get("/jeu/X01")
    assert r.status_code == 404
    assert "Ce QR éphémère n'est rattaché à aucun jeu." in r.text
    assert 'href="/pret/X01">Nommer ce jeu</a>' in r.text


def test_visiteur_voit_une_page_simple_404_sans_formulaire(visiteur):
    r = visiteur.get("/jeu/X01")
    assert r.status_code == 404
    assert "Ce jeu n'est pas encore enregistré. Demandez à un bénévole." in r.text
    assert 'id="nom-jeu"' not in r.text and 'action="/pret/X01/nom"' not in r.text
    assert "Nommer ce jeu" not in r.text
    assert len(re.findall(r"<h1[ >]", r.text)) == 1


def test_visiteur_ne_peut_pas_nommer(visiteur):
    assert visiteur.get("/pret/X01").status_code == 403
    assert _nommer(visiteur).status_code == 403
    assert _lire("SELECT * FROM exemplaires WHERE id_exemplaire = 'X01'") == []


def test_code_inconnu_non_ephemere_inchange(benevole):
    for url in ("/jeu/999", "/jeu/X00", "/jeu/x01"):
        r = benevole.get(url)
        assert r.status_code == 404 and "Exemplaire inconnu" in r.text
        assert "Nommer ce jeu" not in r.text
    r = benevole.get("/pret/X1")
    assert r.status_code == 404 and "n'existe pas dans le catalogue" in r.text
    # Le POST d'un code non éphémère n'écrit rien.
    assert _nommer(benevole, "999").status_code == 404
    assert _lire("SELECT * FROM exemplaires WHERE id_exemplaire = '999'") == []


def test_saisie_manuelle_mene_au_bon_code(benevole):
    r = benevole.get("/scanner/saisie?code=x1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/pret/X01?saisi=1"
    page = benevole.get(r.headers["location"]).text
    assert "Code tapé au clavier" in page and "X01" in page


def test_saisie_d_un_code_inconnu_non_ephemere_inchangee(benevole):
    r = benevole.get("/scanner/saisie?code=999")
    assert r.status_code == 200
    assert services.message_code_introuvable("999") in r.text.replace("&#34;", '"').replace("&#39;", "'")


def test_saisie_d_un_code_ephemere_nomme(benevole):
    _nommer(benevole, "X07", "Dobble")
    r = benevole.get("/scanner/saisie?code=x7", follow_redirects=False)
    assert r.headers["location"] == "/pret/X07?saisi=1"


# ===========================================================================
# 4. NOMMER
# ===========================================================================
def test_nommer_d_apres_un_titre_existant_ne_le_modifie_pas(benevole):
    avant = _titre("CATANE")
    cats = _lire("SELECT * FROM titres_categories WHERE reference_titre = 'CATANE'")
    r = _nommer(benevole, "X01", "  catane ")
    assert r.status_code == 200
    assert _lire("SELECT reference_titre FROM exemplaires WHERE id_exemplaire = 'X01'") == [("CATANE",)]
    assert _titre("CATANE") == avant
    assert _lire("SELECT * FROM titres_categories WHERE reference_titre = 'CATANE'") == cats
    # L'écran suivant est la page de prêt : « Prêter » à un tap.
    assert "QR X01 : « Catane » enregistré." in r.text
    assert 'action="/pret/X01/preter"' in r.text
    assert "nouveau jeu au catalogue" not in r.text


def test_nommer_un_titre_nouveau_nom_seul(benevole):
    r = _nommer(benevole, "X02", "Les Aventuriers  du Rail")
    assert "nouveau jeu au catalogue" in r.text
    titre = _titre("LES_AVENTURIERS_DU_RAIL")
    assert titre["nom"] == "Les Aventuriers du Rail"
    for champ in ("categorie", "type_jeu", "age_min", "editeur", "descriptif", "bgg_id"):
        assert titre[champ] is None
    assert _lire("SELECT id_exemplaire FROM exemplaires WHERE reference_titre = ?",
                 ("LES_AVENTURIERS_DU_RAIL",)) == [("X02",)]


def test_le_titre_nouveau_est_au_catalogue_public(benevole, visiteur):
    _nommer(benevole, "X02", "Zombicide")
    assert "Zombicide" in visiteur.get("/catalogue").text
    fiche = visiteur.get("/jeu/X02")
    assert fiche.status_code == 200
    assert "Boîte de passage : elle est prêtée le temps de l'événement." in fiche.text
    assert "Boîte de passage" not in visiteur.get("/jeu/001").text


@pytest.mark.parametrize("nom, raison", [
    ("", "vide"), ("   ", "vide"), ("???", "vide"),
    ("x" * (services.LONGUEUR_MAX_NOM_EPHEMERE + 1), "long")])
def test_nom_refuse_message_et_champ_prerempli(benevole, nom, raison):
    r = _nommer(benevole, "X03", nom)
    assert r.status_code == 400
    assert services.MESSAGES_NOM_EPHEMERE[raison].replace("'", "&#39;") in r.text
    assert 'role="alert"' in r.text
    assert 'aria-invalid="true"' in r.text
    if nom.strip():
        assert f'value="{nom}"' in r.text.replace("&#39;", "'").replace("&#63;", "?")
    assert _lire("SELECT * FROM exemplaires WHERE id_exemplaire = 'X03'") == []


def test_nom_a_la_longueur_maximale_accepte(benevole):
    nom = "J" * services.LONGUEUR_MAX_NOM_EPHEMERE
    assert _nommer(benevole, "X03", nom).status_code == 200


def test_double_envoi_du_meme_nom(benevole, _journal_isole):
    _nommer(benevole, "X04", "Dobble")
    r = _nommer(benevole, "X04", "dobble")
    assert r.status_code == 200
    assert "QR X04 : « Dobble » enregistré." in r.text
    assert _lire("SELECT COUNT(*) FROM exemplaires WHERE id_exemplaire = 'X04'") == [(1,)]
    assert len(_journal(_journal_isole, "ephemere_nomme")) == 1


def test_concurrence_le_second_voit_la_boite_deja_nommee(benevole):
    _nommer(benevole, "X05", "Dobble")
    r = _nommer(benevole, "X05", "Catane")
    assert r.status_code == 200
    assert "Un autre bénévole vient de nommer ce QR « Dobble »" in r.text
    assert 'action="/pret/X05/preter"' in r.text
    assert _lire("SELECT reference_titre FROM exemplaires WHERE id_exemplaire = 'X05'") == [("DOBBLE",)]


def test_concurrence_au_niveau_du_service(bases):
    conn = _conn()
    try:
        premier = services.nommer_code_ephemere(conn, "X06", "Azul")
        second = services.nommer_code_ephemere(conn, "X06", "Catane")
    finally:
        conn.close()
    assert not premier["deja_nomme"] and premier["titre_nouveau"]
    assert second["deja_nomme"] and second["nom"] == "Azul" and not second["meme_titre"]


def test_le_service_refuse_un_code_non_ephemere(bases):
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            services.nommer_code_ephemere(conn, "A0001", "Azul")
    finally:
        conn.close()


# ===========================================================================
# 5. CORRIGER LE NOM
# ===========================================================================
def test_corriger_tant_que_jamais_sortie(benevole):
    _nommer(benevole, "X03", "Catanne")
    page = benevole.get("/pret/X03").text
    assert 'href="/pret/X03/nom">Corriger le nom</a>' in page
    ecran = benevole.get("/pret/X03/nom")
    assert ecran.status_code == 200
    assert "<h1>Corriger le nom</h1>" in ecran.text and 'value="Catanne"' in ecran.text
    r = _nommer(benevole, "X03", "Catane", mode="corriger")
    assert "Nom corrigé : « Catane »." in r.text
    assert _lire("SELECT reference_titre FROM exemplaires WHERE id_exemplaire = 'X03'") == [("CATANE",)]
    # Le titre créé par la faute de frappe, vidé, est supprimé.
    assert _titre("CATANNE") is None


def test_corriger_la_casse_d_un_titre_cree_au_comptoir(benevole):
    _nommer(benevole, "X03", "zombicide")
    r = _nommer(benevole, "X03", "Zombicide", mode="corriger")
    assert "Nom corrigé : « Zombicide »." in r.text
    assert _titre("ZOMBICIDE")["nom"] == "Zombicide"


def test_corriger_ne_renomme_jamais_un_titre_du_catalogue(benevole):
    _nommer(benevole, "X03", "Catane")
    r = _nommer(benevole, "X03", "CATANE", mode="corriger")
    assert "Rien n'a changé" in r.text
    assert _titre("CATANE")["nom"] == "Catane"
    # Quitter Catane ne le supprime pas : il garde ses boîtes.
    _nommer(benevole, "X03", "Dobble", mode="corriger")
    assert _titre("CATANE") is not None


def test_plus_de_correction_apres_une_sortie(benevole, vieillir_prets):
    _nommer(benevole, "X03", "Catanne")
    benevole.post("/pret/X03/preter")
    benevole.post("/pret/X03/rendre")
    page = benevole.get("/pret/X03").text
    assert "Corriger le nom" not in page
    r = benevole.get("/pret/X03/nom")
    assert "Le nom ne se corrige plus ici" in r.text
    r = _nommer(benevole, "X03", "Catane", mode="corriger")
    assert "Le nom ne se corrige plus ici" in r.text
    assert _lire("SELECT reference_titre FROM exemplaires WHERE id_exemplaire = 'X03'") == [("CATANNE",)]


def test_le_bureau_corrige_apres_une_sortie_la_seule_boite_ephemere(admin, vieillir_prets):
    """Le rattachement de l'administration déplace TOUTES les boîtes d'un
    jeu : une boîte éphémère rattachée par erreur à Catane n'en sortirait
    qu'avec 001 et 002. Le bureau la corrige donc au même écran."""
    _nommer(admin, "X03", "Catane")
    admin.post("/pret/X03/preter")
    assert 'href="/pret/X03/nom">Corriger le nom</a>' in admin.get("/pret/X03").text
    r = _nommer(admin, "X03", "Dobble", mode="corriger")
    assert "Nom corrigé : « Dobble »." in r.text
    assert _lire("SELECT id_exemplaire, reference_titre FROM exemplaires "
                 "WHERE id_exemplaire IN ('001', '002', 'X03') ORDER BY 1") == [
        ("001", "CATANE"), ("002", "CATANE"), ("X03", "DOBBLE")]
    # Son prêt la suit.
    assert _ouvert("X03") == [(1, "pret")]


def test_pas_de_correction_pour_une_boite_du_catalogue(benevole):
    assert "Corriger le nom" not in benevole.get("/pret/001").text
    r = benevole.get("/pret/001/nom")
    assert "Le nom ne se corrige plus ici" in r.text
    _nommer(benevole, "001", "Dobble", mode="corriger")
    assert _lire("SELECT reference_titre FROM exemplaires WHERE id_exemplaire = '001'") == [("CATANE",)]


# ===========================================================================
# 6. LA BOÎTE NOMMÉE EST UNE BOÎTE ORDINAIRE
# ===========================================================================
def _ouvert(code):
    return _lire("SELECT numero_pochette, motif FROM prets WHERE id_exemplaire = ? "
                 "AND date_retour IS NULL", (code,))


def test_pret_et_retour(benevole):
    _nommer(benevole, "X01", "Dobble")
    r = benevole.post("/pret/X01/preter")
    assert "Pochette n°" in r.text
    assert _ouvert("X01") == [(1, "pret")]
    benevole.post("/pret/X01/rendre")
    assert _ouvert("X01") == []


def test_transfert_dans_les_deux_sens(benevole):
    _nommer(benevole, "X01", "Dobble")
    benevole.post("/pret/X01/preter")
    benevole.post("/pret/X01/transfert/001")
    assert _ouvert("X01") == [] and _ouvert("001") == [(1, "pret")]
    benevole.post("/pret/001/transfert/X01")
    assert _ouvert("001") == [] and _ouvert("X01") == [(1, "pret")]


def test_echange_depuis_la_boite_ephemere(benevole):
    _nommer(benevole, "X01", "Dobble")
    benevole.post("/pret/001/preter")
    confirmation = benevole.get("/pret/X01/echange/001")
    assert confirmation.status_code == 200
    assert 'action="/pret/001/transfert/X01"' in confirmation.text
    benevole.post("/pret/001/transfert/X01")
    assert _ouvert("X01") == [(1, "pret")]


@pytest.mark.parametrize("motif", list(services.SORTIES_SANS_POCHETTE))
def test_sorties_sans_pochette(benevole, motif):
    _nommer(benevole, "X01", "Dobble")
    url = services.SORTIES_SANS_POCHETTE[motif]["url"]
    benevole.post(f"/pret/X01/{url}")
    assert _ouvert("X01") == [(services.NUMERO_TOURNOI, motif)]
    benevole.post("/pret/X01/rendre")
    assert _ouvert("X01") == []


def test_compte_dans_les_statistiques(benevole, vieillir_prets):
    _nommer(benevole, "X01", "Zombicide")
    benevole.post("/pret/X01/preter")
    conn = _conn()
    try:
        vieillir_prets(conn)
    finally:
        conn.close()
    benevole.post("/pret/X01/rendre")
    conn = _conn()
    try:
        stats = services.collecter_stats(conn)
    finally:
        conn.close()
    assert stats["globales"]["total_prets"] == 1
    assert any(j["nom"] == "Zombicide" for j in stats["plus"])


# ===========================================================================
# 7. EXPORT, ÉTIQUETTES EN LOT, IMPORT
# ===========================================================================
def test_export_sans_boites_ephemeres_ni_leur_titre(benevole):
    _nommer(benevole, "X01", "Zombicide")
    _nommer(benevole, "X02", "Catane")
    conn = _conn()
    try:
        _, lignes = services.lignes_export_catalogue(conn)
        _, toutes = services.lignes_export_catalogue(conn, avec_retirees=True)
    finally:
        conn.close()
    codes = [l["Code jeu"] for l in lignes]
    assert codes == ["001", "002", "003"]
    assert "Zombicide" not in {l["Nom jeu"] for l in lignes}
    assert {"X01", "X02"} <= {l["Code jeu"] for l in toutes}


def test_etiquettes_en_lot_sans_boites_ephemeres(benevole):
    _nommer(benevole, "X01", "Zombicide")
    _nommer(benevole, "X02", "Catane")
    conn = _conn()
    try:
        titres = services.titres_pour_etiquettes(conn)
        boites = services.exemplaires_pour_etiquettes(conn, None)
    finally:
        conn.close()
    assert {t["nom"]: t["nb_exemplaires"] for t in titres} == {"Catane": 2, "Dobble": 1}
    assert [b["id_exemplaire"] for b in boites] == ["001", "002", "003"]


def test_import_met_de_cote_les_codes_ephemeres(bases, tmp_path):
    from scripts import import_csv
    chemin = tmp_path / "catalogue.csv"
    chemin.write_text(
        "Code jeu;Nom jeu\n001;Catane\nX01;Azul\nx2;Azul\n004;Splendor\n", encoding="utf-8")
    res = import_csv.importer(chemin)
    assert [e["code"] for e in res["ephemeres_ecartees"]] == ["X01", "x2"]
    assert _lire("SELECT id_exemplaire FROM exemplaires ORDER BY id_exemplaire") == [
        ("001",), ("002",), ("003",), ("004",)]
    assert _titre("AZUL") is None
    phrase, = import_csv.phrases_ephemeres(res["ephemeres_ecartees"])
    assert phrase.startswith("2 lignes n'ont pas été importées : leurs codes (X01, x2)")
    assert "se nomment au comptoir" in phrase


def test_import_ne_touche_pas_une_boite_ephemere_nommee(benevole, bases, tmp_path):
    from scripts import import_csv
    _nommer(benevole, "X01", "Zombicide")
    chemin = tmp_path / "catalogue.csv"
    chemin.write_text("Code jeu;Nom jeu\nX01;Azul\n", encoding="utf-8")
    import_csv.importer(chemin)
    assert _lire("SELECT reference_titre FROM exemplaires WHERE id_exemplaire = 'X01'") == [("ZOMBICIDE",)]


def test_import_dans_l_ecran_d_administration(admin, tmp_path):
    contenu = "Code jeu;Nom jeu\n001;Catane\nX01;Azul\n".encode("utf-8")
    r = admin.post("/admin/donnees/import",
                   files={"fichier": ("catalogue.csv", contenu, "text/csv")})
    assert "son code (X01) est celui d&#39;un QR éphémère" in r.text


# ===========================================================================
# 8. LE JOURNAL — LA PORTE
# ===========================================================================
def test_actions_au_vocabulaire_ferme():
    assert {"ephemere_nomme", "ephemere_nom_corrige"} <= journal.ACTIONS


def test_journal_nomme_code_en_ref_nom_en_objet(benevole, _journal_isole):
    _nommer(benevole, "X01", "Zombicide")
    ligne, = _journal(_journal_isole, "ephemere_nomme")
    assert ligne["module"] == "catalogue"
    assert ligne["ref"] == "X01" and ligne["objet"] == "Zombicide" and ligne["ok"]


def test_journal_jamais_le_texte_refuse(benevole, _journal_isole):
    texte = "Jean Dupont " + "x" * services.LONGUEUR_MAX_NOM_EPHEMERE
    _nommer(benevole, "X01", texte)
    ligne, = _journal(_journal_isole, "ephemere_nomme")
    assert not ligne["ok"] and ligne["detail"] == "long"
    assert "Dupont" not in (_journal_isole).read_text(encoding="utf-8")


def test_journal_correction(benevole, _journal_isole):
    _nommer(benevole, "X01", "Catanne")
    _nommer(benevole, "X01", "Catane", mode="corriger")
    ligne, = _journal(_journal_isole, "ephemere_nom_corrige")
    assert ligne["objet"] == "Catane" and ligne["ref"] == "X01"


def test_le_pret_porte_le_nom_en_objet_comme_avant(benevole, _journal_isole):
    """La porte n'ouvre rien de neuf : les prêts portaient déjà le nom du jeu."""
    _nommer(benevole, "X01", "Zombicide")
    benevole.post("/pret/X01/preter")
    ligne = [l for l in _journal(_journal_isole) if l["module"] == "pret"][-1]
    assert ligne["objet"] == "Zombicide" and ligne["ref"] == "ZOMBICIDE"


def test_routes_d_ecriture_protegees_par_le_jeton():
    from app.auth import exiger_jeton
    from app.main import app
    for methode in ("GET", "POST"):
        route, = [r for r in app.routes if getattr(r, "path", None) == "/pret/{id_exemplaire}/nom"
                  and methode in r.methods]
        assert any(d.call is exiger_jeton for d in route.dependant.dependencies)
