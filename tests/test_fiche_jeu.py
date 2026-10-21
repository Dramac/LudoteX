"""
FICHE D'UN JEU CORRIGÉE À L'ÉCRAN, ET SA FICHE BOARDGAMEGEEK (lot fiche-1).

Ce que ce fichier vérifie, et pourquoi :

1. **L'éditeur** de `/admin/jeu/<référence>` : chaque cas limite (nom vidé,
   zéro, négatif, texte dans un nombre, bornes de joueurs, année, descriptif
   très long, type hors liste, titre inconnu). Un refus n'écrit rien et garde
   TOUTES les saisies ; une case vidée efface ; un enregistrement sans
   changement n'écrit rien.
2. **La fiche BoardGameGeek** : une seule fonction lit le numéro ou l'adresse,
   pour l'écran comme pour l'import ; le lien n'apparaît que dans
   l'administration ; AUCUN appel réseau.
3. **L'aller-retour export → import** laisse la base identique, y compris
   après un renommage à l'écran — et l'adresse du QR de chaque boîte ne bouge
   jamais. C'est le piège principal du lot : sans la règle de
   `import_csv.resoudre_reference`, un jeu renommé se scindait en deux titres.
4. **Un fichier plus ancien qu'une correction** le dit à l'import, sans
   bloquer ; un fichier identique, ou une base jamais corrigée, ne disent rien.
5. **L'étiquette périmée** est annoncée quand ce qui S'IMPRIME change, et
   seulement alors.
6. **Les lettres de classement** s'effacent, les trois d'un coup.
"""

import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app import bgg, categories_jeu, classement, etiquettes, services

MOT_DE_PASSE = "secret-admin-fiche"
BASE_URL = "https://exemple.test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def bases(tmp_path, monkeypatch):
    """
    Trois bases temporaires. Catalogue : « Catane » (référence CATANE, deux
    boîtes 001 et 002) et « Dobble » (DOBBLE, une boîte 003).
    """
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.setenv("BASE_URL", BASE_URL)
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
    conn.execute(
        "INSERT INTO titres (reference_titre, nom, type_jeu, categorie, "
        "nb_joueurs_min, nb_joueurs_max, duree_min, age_min, editeur, auteur, "
        "annee_edition, descriptif) VALUES ('CATANE', 'Catane', 'Jeu', "
        "'Stratégie', 3, 4, 75, 10, 'Kosmos', 'Klaus Teuber', 1995, "
        "'Colonisation d''une île.')")
    conn.execute(
        "INSERT INTO titres (reference_titre, nom, type_jeu, categorie, age_min) "
        "VALUES ('DOBBLE', 'Dobble', 'Jeu', 'Ambiance', 6)")
    for id_ex, ref in (("001", "CATANE"), ("002", "CATANE"), ("003", "DOBBLE")):
        conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
                     "VALUES (?, ?)", (id_ex, ref))
    conn.commit()
    # Catégories posées en SQL dans la copie : la migration les rattache à
    # leur liste, que lisent désormais les cases de l'éditeur.
    db.init_db(conn)
    conn.close()
    return tmp_path


@pytest.fixture
def anonyme(bases):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture
def client(anonyme):
    anonyme.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return anonyme


@pytest.fixture
def listes_remplies(monkeypatch):
    """Patron de tests/test_classement.py : listes 2 et 3 remplies le temps du test."""
    monkeypatch.setattr(classement, "LETTRES_JEU", {"P": "Placement"})
    monkeypatch.setattr(classement, "LETTRES_MATERIEL", {"C": "Cartes"})


def _conn():
    from app.db import get_connection
    return get_connection()


def _titre(ref="CATANE"):
    conn = _conn()
    try:
        return services.get_titre(conn, ref)
    finally:
        conn.close()


def _executer(sql, params=()):
    conn = _conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _formulaire(ref="CATANE", **changements):
    """Le formulaire tel que l'écran l'envoie, pré-rempli depuis la base."""
    titre = _titre(ref)
    donnees = {c: "" if titre[c] is None else str(titre[c]) for c in services.CHAMPS_FICHE}
    donnees["bgg"] = "" if titre["bgg_id"] is None else str(titre["bgg_id"])
    # Les catégories du jeu arrivent cochées, une valeur par case (le nom).
    donnees["categorie"] = categories_jeu.decouper(titre["categorie"])
    donnees.update(changements)
    return donnees


def _enregistrer(client, ref="CATANE", **changements):
    return client.post(f"/admin/jeu/{ref}/modifier",
                       data=_formulaire(ref, **changements), follow_redirects=False)


def _instantane():
    """Toute la table `titres` et toute la table `exemplaires`."""
    conn = _conn()
    try:
        titres = {r["reference_titre"]: dict(r) for r in conn.execute("SELECT * FROM titres")}
        exemplaires = {r["id_exemplaire"]: dict(r)
                       for r in conn.execute("SELECT * FROM exemplaires")}
    finally:
        conn.close()
    return titres, exemplaires


def _exporter(chemin: Path) -> Path:
    from app import exports
    conn = _conn()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    chemin.write_bytes(exports.catalogue_csv(entetes, lignes))
    return chemin


def _importer(chemin: Path, **options):
    from scripts import import_csv
    return import_csv.importer(chemin, **options)


def _journal(chemin, action):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l]
    return [l for l in lignes if l["action"] == action]


def _champ(page, ident):
    return re.search(r'<(?:input|select|textarea)[^>]*\bid="' + ident + r'"[^>]*>',
                     page, re.S).group(0)


# ===========================================================================
# 1. LA FICHE BOARDGAMEGEEK — lecture de la saisie, domicile unique
# ===========================================================================
@pytest.mark.parametrize("saisie", [
    "13",
    " 13 ",
    "https://boardgamegeek.com/boardgame/13/catan",
    "https://boardgamegeek.com/boardgame/13",
    "boardgamegeek.com/boardgame/13/catan",
    "www.boardgamegeek.com/boardgame/13/catan",
    "https://www.boardgamegeek.com/boardgame/13/catan",
    "https://boardgamegeek.com/boardgame/13/catan/",
    "https://boardgamegeek.com/boardgame/13/",
    "https://boardgamegeek.com/boardgame/13/catan?foo=bar",
    "https://boardgamegeek.com/boardgame/13#credits",
    "http://boardgamegeek.com/boardgame/13",
    "HTTPS://BoardGameGeek.com/boardgame/13",
    "  https://boardgamegeek.com/boardgame/13/catan  ",
])
def test_bgg_lit_le_numero_ou_l_adresse(saisie):
    assert bgg.lire_identifiant(saisie) == 13


def test_bgg_lit_une_adresse_d_extension():
    assert bgg.lire_identifiant(
        "https://boardgamegeek.com/boardgameexpansion/325/catan-seafarers") == 325


@pytest.mark.parametrize("saisie", [
    "", "   ", None,
    "0", "-13", "+13", "1.5", "13 ans", "treize",
    "https://boardgamegeek.com/boardgame/0/rien",
    "https://boardgamegeek.com/boardgame/-13",
    "https://example.com/boardgame/13/catan",
    "https://boardgamegeek.com.example.com/boardgame/13",
    "https://boardgamegeek.com/user/13",
    "https://boardgamegeek.com/boardgame/",
    "https://boardgamegeek.com/boardgame/catan",
    "ftp://boardgamegeek.com/boardgame/13",
    "javascript:alert(1)",
    "12345678901",   # plus de CHIFFRES_MAX chiffres
    "١٣",           # chiffres arabes : isdigit() les accepte, pas nous
])
def test_bgg_refuse_le_reste(saisie):
    assert bgg.lire_identifiant(saisie) is None


def test_bgg_l_adresse_du_lien_se_construit_du_seul_identifiant():
    assert bgg.url_fiche(13) == "https://boardgamegeek.com/boardgame/13"


def test_bgg_aucun_appel_reseau_possible():
    """
    Garde-fou : le module de l'identifiant BGG n'importe AUCUNE bibliothèque
    réseau. L'appel à BGG (lot fiche-2) vit dans son propre module,
    `app/bgg_api.py` ; `app/bgg.py` reste une lecture de chaîne.
    """
    arbre = ast.parse(Path("app/bgg.py").read_text(encoding="utf-8"))
    modules = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            modules |= {a.name for a in noeud.names}
        elif isinstance(noeud, ast.ImportFrom):
            modules.add(noeud.module or "")
    reseau = {"socket", "ssl", "http", "urllib.request", "requests", "httpx",
              "aiohttp", "urllib3", "ftplib", "smtplib", "asyncio"}
    assert not {m for m in modules if m in reseau or m.split(".")[0] in
                {"socket", "ssl", "http", "requests", "httpx", "aiohttp",
                 "urllib3", "ftplib", "smtplib", "asyncio"}}, modules
    assert modules <= {"__future__", "re", "urllib.parse"}, modules


def test_le_jeton_bgg_est_lu_a_un_seul_endroit():
    """
    Intention d'origine (lot fiche-1 : « le jeton n'est lu nulle part »),
    réécrite au lot fiche-2, qui l'introduit : le jeton est lu à UN SEUL
    endroit, `bgg_api._jeton`, par une seule lecture d'environnement. Qu'il
    n'apparaisse ensuite ni dans une page, ni dans une URL, ni dans le
    journal est vérifié par tests/test_bgg_donnees.py.
    """
    porteurs = [fichier for dossier in ("app", "scripts")
                for fichier in Path(dossier).rglob("*.py")
                if "BGG_TOKEN" in fichier.read_text(encoding="utf-8")]
    assert porteurs == [Path("app/bgg_api.py")], porteurs
    arbre = ast.parse(Path("app/bgg_api.py").read_text(encoding="utf-8"))
    lectures = [n for n in ast.walk(arbre)
                if isinstance(n, ast.Constant) and n.value == "BGG_TOKEN"]
    assert len(lectures) == 1
    fonctions = [f.name for f in ast.walk(arbre) if isinstance(f, ast.FunctionDef)
                 and any(n in lectures for n in ast.walk(f))]
    assert fonctions == ["_jeton"]


# ===========================================================================
# 2. MIGRATION — une base d'avant se comporte exactement comme avant
# ===========================================================================
def test_migration_une_base_ancienne_gagne_deux_colonnes_vides(tmp_path, monkeypatch):
    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.execute("""
        CREATE TABLE titres (
            reference_titre TEXT PRIMARY KEY, nom TEXT NOT NULL, type_jeu TEXT,
            categorie TEXT, nb_joueurs_min INTEGER, nb_joueurs_max INTEGER,
            duree_min INTEGER, age_min INTEGER, editeur TEXT, auteur TEXT,
            annee_edition INTEGER, descriptif TEXT, date_achat TEXT,
            lettre_public TEXT, lettre_jeu TEXT, lettre_materiel TEXT)""")
    ancienne.execute("INSERT INTO titres (reference_titre, nom, age_min) "
                     "VALUES ('CATAN', 'Catan', 10)")
    ancienne.commit()
    ancienne.close()

    from app import db
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        db.init_db(conn)    # idempotente
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(titres)")}
        assert {"bgg_id", "modifie_le"} <= colonnes
        titre = services.get_titre(conn, "CATAN")
    finally:
        conn.close()
    assert titre["bgg_id"] is None and titre["modifie_le"] is None
    assert titre["age_min"] == 10


def test_base_d_avant_aucun_lien_aucun_avertissement(client, tmp_path):
    page = client.get("/admin/jeu/CATANE").text
    assert "sur BoardGameGeek<span" not in page   # aucun lien (lot fiche-2 : sous la case)
    assert "n'est plus à jour" not in page
    res = _importer(_exporter(tmp_path / "export.csv"))
    assert res["corrections_defaites"] == [] and res["bgg_refusees"] == 0


# ===========================================================================
# 3. L'ÉDITEUR — pré-remplissage, gardes, cas limites
# ===========================================================================
def test_la_fiche_est_pre_remplie(client):
    page = client.get("/admin/jeu/CATANE").text
    assert 'value="Catane"' in _champ(page, "nom")
    assert 'value="10"' in _champ(page, "age_min")
    assert 'value="Kosmos"' in _champ(page, "editeur")
    assert "Colonisation d&#39;une île." in page
    # Catégories existantes en cases (lot categories-2) : celles du jeu
    # cochées, les autres non ; plus de <datalist>.
    assert re.search(r'<input type="checkbox" name="categorie" value="Stratégie" checked>', page)
    assert re.search(r'<input type="checkbox" name="categorie" value="Ambiance">', page)
    assert 'value=""' in _champ(page, "categorie_ajout")
    assert "categories-connues" not in page


@pytest.mark.parametrize("chemin", [
    "/admin/jeu/CATANE/modifier", "/admin/jeu/CATANE/lettres/effacer"])
def test_routes_nouvelles_refusees_sans_session(anonyme, chemin):
    avant = _instantane()
    r = anonyme.post(chemin, data={"nom": "Pirate"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert _instantane() == avant


def test_titre_inconnu_404_propre(client):
    for r in (client.get("/admin/jeu/INCONNU"),
              client.post("/admin/jeu/INCONNU/modifier", data={"nom": "X"}),
              client.post("/admin/jeu/INCONNU/lettres/effacer")):
        assert r.status_code == 404
        assert "Jeu inconnu" in r.text


def test_titre_sans_exemplaire_la_fiche_s_ouvre(client):
    """
    Un titre sans aucune boîte (toutes passées sous un autre nom par un import
    de tableur) : la fiche s'ouvre, sans lien vers une fiche publique qui
    n'existe pas, et le bouton d'ajout reste là. Elle répondait 500.
    """
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('ORPHELIN', 'Orphelin')")
    r = client.get("/admin/jeu/ORPHELIN?ok=enregistre&etiquette=1")
    assert r.status_code == 200
    assert 'href="/jeu/' not in r.text
    assert "Voir la fiche publique" not in r.text
    assert 'action="/admin/jeu/ORPHELIN/exemplaire"' in r.text
    assert "Ajouter un exemplaire" in r.text
    assert "n'a plus aucune boîte" in r.text
    # Rien à réimprimer : le bandeau d'étiquette périmée se tait.
    assert "Réimprimer les étiquettes de ce jeu" not in r.text
    assert 'value="Orphelin"' in _champ(r.text, "nom")


def test_titre_avec_exemplaire_garde_son_lien_public(client):
    page = client.get("/admin/jeu/DOBBLE").text
    assert 'href="/jeu/003"' in page
    assert "n'a plus aucune boîte" not in page


def test_renommer_garde_la_reference(client):
    r = _enregistrer(client, nom="  Catan  ")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/jeu/CATANE?ok=enregistre")
    titre = _titre("CATANE")
    assert titre["nom"] == "Catan"          # espaces nettoyés comme à l'import
    assert _titre("CATAN") is None
    assert titre["modifie_le"]


def test_nom_vide_refuse_et_saisies_conservees(client):
    avant = _instantane()
    r = _enregistrer(client, nom="   ", age_min="12", editeur="Filosofia")
    assert r.status_code == 400
    assert "Le nom est obligatoire." in r.text
    assert _instantane() == avant
    # Les autres saisies sont réaffichées telles quelles.
    assert 'value="12"' in _champ(r.text, "age_min")
    assert 'value="Filosofia"' in _champ(r.text, "editeur")
    # Le refus est relié au champ, qui reprend le focus.
    champ = _champ(r.text, "nom")
    assert 'aria-invalid="true"' in champ and "autofocus" in champ
    assert 'aria-describedby="nom-erreur"' in champ and 'id="nom-erreur"' in r.text
    assert 'role="alert"' in r.text


def test_nom_au_slug_d_un_autre_titre_accepte(client):
    """« Dobble » renommé « Catane » : accepté, la référence ne bouge pas."""
    assert _enregistrer(client, "DOBBLE", nom="Catane").status_code == 303
    assert _titre("DOBBLE")["nom"] == "Catane"
    assert _titre("CATANE")["nom"] == "Catane"


def test_un_champ_vide_efface_la_valeur(client):
    _enregistrer(client, editeur="", age_min="", descriptif="")
    titre = _titre()
    assert titre["editeur"] is None and titre["age_min"] is None
    assert titre["descriptif"] is None


def test_zero(client):
    """Âge 0 : accepté. Zéro joueur, zéro minute : refusés."""
    assert _enregistrer(client, age_min="0").status_code == 303
    assert _titre()["age_min"] == 0
    for champ in ("nb_joueurs_min", "duree_min"):
        r = _enregistrer(client, **{champ: "0"})
        assert r.status_code == 400 and "Tapez un nombre entre 1 et" in r.text


@pytest.mark.parametrize("champ", ["age_min", "duree_min", "nb_joueurs_min",
                                   "annee_edition"])
def test_negatif_refuse(client, champ):
    avant = _instantane()
    r = _enregistrer(client, **{champ: "-3"})
    assert r.status_code == 400 and "Tapez un nombre entre" in r.text
    assert _instantane() == avant
    assert f'aria-describedby="{champ}-erreur"' in _champ(r.text, champ)


@pytest.mark.parametrize("champ", ["age_min", "duree_min", "nb_joueurs_max",
                                   "annee_edition"])
def test_texte_dans_un_nombre_refuse_jamais_efface(client, champ):
    """`_int_ou_none` aurait effacé l'âge en silence : ici, refus dit."""
    avant = _instantane()
    r = _enregistrer(client, **{champ: "10 ans"})
    assert r.status_code == 400
    assert "« 10 ans » n&#39;est pas un nombre" in r.text
    assert _instantane() == avant
    assert 'value="10 ans"' in _champ(r.text, champ)


def test_min_superieur_au_max_refuse(client):
    r = _enregistrer(client, nb_joueurs_min="5", nb_joueurs_max="3")
    assert r.status_code == 400
    assert "Le maximum (3) est plus petit que le minimum (5)." in r.text


def test_une_seule_borne_de_joueurs_refusee(client):
    """Une borne seule ne survivrait pas à l'export (« 2 » se relit 2 à 2)."""
    r = _enregistrer(client, nb_joueurs_max="")
    assert r.status_code == 400 and "Indiquez aussi le maximum" in r.text
    r = _enregistrer(client, nb_joueurs_min="")
    assert r.status_code == 400 and "Indiquez aussi le minimum" in r.text
    # Le même nombre aux deux bornes, ou rien, est accepté.
    assert _enregistrer(client, nb_joueurs_min="2", nb_joueurs_max="2").status_code == 303
    assert _enregistrer(client, nb_joueurs_min="", nb_joueurs_max="").status_code == 303
    assert _titre()["nb_joueurs_min"] is None


@pytest.mark.parametrize("annee, accepte", [
    ("1899", False), ("1900", True), ("2099", True), ("2100", False), ("99999", False)])
def test_annee_hors_bornes(client, annee, accepte):
    r = _enregistrer(client, annee_edition=annee)
    assert (r.status_code == 303) is accepte


def test_descriptif_tres_long(client):
    r = _enregistrer(client, descriptif="x" * (services.LONGUEUR_DESCRIPTIF + 1))
    assert r.status_code == 400 and "5000 au plus" in r.text
    assert _enregistrer(client, descriptif="x" * services.LONGUEUR_DESCRIPTIF).status_code == 303


def test_descriptif_fins_de_ligne_normalisees(client):
    _enregistrer(client, descriptif="Ligne 1\r\nLigne 2")
    assert _titre()["descriptif"] == "Ligne 1\nLigne 2"


def test_type_hors_liste_conserve(client):
    _executer("UPDATE titres SET type_jeu = 'Jeu de rôle' WHERE reference_titre = 'CATANE'")
    page = client.get("/admin/jeu/CATANE").text
    assert re.search(r'<option value="Jeu de rôle" selected>', page)
    r = _enregistrer(client, editeur="Asmodee")
    assert r.status_code == 303
    assert _titre()["type_jeu"] == "Jeu de rôle"
    # Un type forgé, hors liste et hors base, est refusé.
    r = _enregistrer(client, type_jeu="N'importe quoi")
    assert r.status_code == 400 and "Choisissez un type" in r.text
    # Le type peut être vidé.
    _enregistrer(client, type_jeu="")
    assert _titre()["type_jeu"] is None


def test_enregistrer_sans_changement_n_ecrit_rien(client, _journal_isole):
    avant = _instantane()
    r = _enregistrer(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=inchange"
    assert _instantane() == avant
    assert _titre()["modifie_le"] is None
    assert _journal(_journal_isole, "jeu_modifie") == []
    assert "Rien n&#39;a changé" in client.get(r.headers["location"]).text


def test_enregistrer_journalise_le_nom_jamais_les_valeurs(client, _journal_isole):
    _enregistrer(client, descriptif="Un texte secret qui ne doit pas sortir")
    ligne, = _journal(_journal_isole, "jeu_modifie")
    assert ligne["module"] == "catalogue" and ligne["objet"] == "Catane"
    assert ligne["ref"] == "CATANE" and ligne["ok"] is True
    assert "secret" not in _journal_isole.read_text(encoding="utf-8")


def test_message_de_succes_par_code_ferme(client):
    page = client.get("/admin/jeu/CATANE?ok=enregistre").text
    assert re.search(r'<p class="resultat resultat-ok" role="status">Fiche enregistrée.</p>', page)
    page = client.get("/admin/jeu/CATANE?ok=%3Cscript%3E").text
    assert 'class="resultat resultat-ok" role="status"' not in page
    assert "&lt;script&gt;" not in page


def test_chaque_champ_de_l_editeur_a_son_label(client):
    page = client.get("/admin/jeu/CATANE").text
    formulaire = re.search(r'<form[^>]*action="/admin/jeu/CATANE/modifier".*?</form>',
                           page, re.S).group(0)
    idents = re.findall(r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', formulaire)
    # La catégorie n'est plus un champ à identifiant : des cases, chacune
    # dans son libellé, et le champ « Ajouter une catégorie ».
    assert set(idents) >= (set(services.CHAMPS_FICHE) - {"categorie"}) | {
        "bgg", "categorie_ajout"}
    for ident in idents:
        assert f'<label for="{ident}">' in formulaire, ident
    cases = re.findall(r'(<label class="case case-categorie">)?<input type="checkbox" '
                       r'name="categorie"', formulaire)
    assert cases and all(cases)


def test_la_creation_partage_le_bloc_et_garde_la_saisie(client):
    page = client.get("/admin/jeu-nouveau").text
    assert "autofocus" in _champ(page, "nom")
    assert 'name="categorie_ajout"' in page
    assert re.search(r'<input type="checkbox" name="categorie" value="Ambiance">', page)
    r = client.post("/admin/jeu-nouveau", data={"nom": " ", "editeur": "Kosmos",
                                                "type_jeu": "Extension"})
    assert r.status_code == 400
    assert 'value="Kosmos"' in _champ(r.text, "editeur")
    assert re.search(r'<option value="Extension" selected>', r.text)
    # Le bloc n'existe qu'une fois dans les gabarits.
    gabarits = "".join(p.read_text(encoding="utf-8")
                       for p in Path("app/templates").glob("*.html"))
    assert gabarits.count('id="nb_joueurs_min"') == 1


# ===========================================================================
# 4. LA FICHE BGG DANS L'ÉDITEUR, L'EXPORT ET L'IMPORT
# ===========================================================================
def test_bgg_coller_une_adresse_pose_le_lien(client, _journal_isole):
    r = _enregistrer(client, bgg="https://boardgamegeek.com/boardgame/13/catan")
    assert r.status_code == 303
    titre = _titre()
    assert titre["bgg_id"] == 13
    # Un identifiant seul ne pose pas `modifie_le` (l'import ne l'efface pas).
    assert titre["modifie_le"] is None
    assert len(_journal(_journal_isole, "jeu_modifie")) == 1
    page = client.get("/admin/jeu/CATANE").text
    lien = re.search(r'<a [^>]*href="https://boardgamegeek.com/boardgame/13"[^>]*>', page).group(0)
    assert 'target="_blank"' in lien and 'rel="noopener noreferrer"' in lien
    # Lot fiche-2 : le lien vit sous la case, et dit ce qu'il ouvre. Sans
    # copie BGG, le nom n'est pas connu : il cite le numéro.
    assert "Ouvrir la fiche n° 13 sur BoardGameGeek" in page
    assert 'value="13"' in _champ(page, "bgg")


def test_bgg_pas_sur_la_fiche_publique_par_defaut(client):
    """Le lien public est un réglage d'instance, décoché par défaut (lot fiche-2)."""
    _enregistrer(client, bgg="13")
    assert "boardgamegeek" not in client.get("/jeu/001").text.lower()


def test_bgg_refuse_garde_les_autres_saisies(client):
    avant = _instantane()
    r = _enregistrer(client, bgg="https://example.com/boardgame/13", editeur="Filosofia")
    assert r.status_code == 400
    assert _instantane() == avant
    assert 'value="Filosofia"' in _champ(r.text, "editeur")
    champ = _champ(r.text, "bgg")
    assert 'aria-invalid="true"' in champ and "autofocus" in champ
    assert "bgg-erreur" in champ and 'id="bgg-erreur"' in r.text


def test_bgg_vider_retire_le_lien(client):
    _enregistrer(client, bgg="13")
    _enregistrer(client, bgg="")
    assert _titre()["bgg_id"] is None
    assert "sur BoardGameGeek<span" not in client.get("/admin/jeu/CATANE").text


def test_export_porte_la_fiche_bgg_en_fin_de_ligne(client):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    entetes, lignes = services.lignes_export_catalogue(_conn())
    assert entetes[-1] == "Fiche BoardGameGeek"
    assert entetes.index("Fiche BoardGameGeek") > entetes.index("Lettres classement")
    par_code = {l["Code jeu"]: l for l in lignes}
    assert par_code["001"]["Fiche BoardGameGeek"] == "13"
    assert par_code["003"]["Fiche BoardGameGeek"] == ""


ENTETE = "Code jeu;Nom jeu;Age joueurs"


def _csv(tmp_path, contenu):
    chemin = tmp_path / "cat.csv"
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_import_bgg_colonne_absente_ou_case_vide_ne_touchent_a_rien(client, tmp_path):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _importer(_csv(tmp_path, f"{ENTETE}\n001;Catane;10\n"))
    assert _titre()["bgg_id"] == 13
    res = _importer(_csv(tmp_path, f"{ENTETE};Fiche BoardGameGeek\n001;Catane;10;\n"))
    assert _titre()["bgg_id"] == 13 and res["bgg_refusees"] == 0


def test_import_bgg_numero_ou_adresse(client, tmp_path):
    _importer(_csv(tmp_path, f"{ENTETE};Fiche BoardGameGeek\n"
                             "001;Catane;10;https://boardgamegeek.com/boardgame/13/catan\n"
                             "003;Dobble;6; 63268 \n"))
    assert _titre()["bgg_id"] == 13 and _titre("DOBBLE")["bgg_id"] == 63268


def test_import_bgg_illisible_compte_et_ligne_importee(client, tmp_path):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    res = _importer(_csv(tmp_path, f"{ENTETE};Fiche BoardGameGeek\n"
                                   "001;Catane;12;https://example.com/13\n003;Dobble;6;zz\n"))
    assert res["bgg_refusees"] == 2 and res["exemplaires"] == 2
    assert _titre()["bgg_id"] == 13 and _titre()["age_min"] == 12
    assert _titre("DOBBLE")["bgg_id"] is None


def test_import_bgg_premiere_valeur_valide_du_titre(client, tmp_path):
    _importer(_csv(tmp_path, f"{ENTETE};Fiche BoardGameGeek\n"
                             "001;Catane;10;zz\n002;Catane;10;13\n"))
    assert _titre()["bgg_id"] == 13


def test_le_message_d_import_dit_les_fiches_bgg_refusees(client):
    contenu = (f"{ENTETE};Fiche BoardGameGeek\n001;Catane;10;pas une fiche\n").encode()
    r = client.post("/admin/donnees/import", files={"fichier": ("c.csv", contenu, "text/csv")})
    assert "Import réussi" in r.text
    assert "Fiche BoardGameGeek non reconnue sur 1 ligne(s)" in r.text


# ===========================================================================
# 5. L'ALLER-RETOUR EXPORT → IMPORT (POINT 3.1)
# ===========================================================================
def _urls_qr(ref="CATANE"):
    """Les adresses encodées dans le QR de chaque boîte du titre."""
    conn = _conn()
    try:
        return [etiquettes.url_fiche(BASE_URL, ex["id_exemplaire"])
                for ex in services.lister_exemplaires_du_titre(conn, ref)]
    finally:
        conn.close()


def test_aller_retour_sans_rien_changer(client, tmp_path):
    avant = _instantane()
    _importer(_exporter(tmp_path / "export.csv"))
    assert _instantane() == avant


def test_aller_retour_apres_renommage_a_l_ecran(client, tmp_path):
    """Le piège du lot : « Catane » corrigé en « Catan » ne se scinde plus."""
    _enregistrer(client, nom="Catan", bgg="13")
    avant = _instantane()
    _importer(_exporter(tmp_path / "export.csv"))
    titres, exemplaires = _instantane()
    assert (titres, exemplaires) == avant
    assert "CATAN" not in titres
    # Aucun titre sans exemplaire.
    assert set(titres) == {e["reference_titre"] for e in exemplaires.values()}


def test_l_adresse_du_qr_ne_bouge_jamais(client, tmp_path):
    """Engagement envers les étiquettes imprimées : avant, après, après ré-import."""
    avant = _urls_qr()
    assert avant == [f"{BASE_URL}/jeu/001", f"{BASE_URL}/jeu/002"]
    _enregistrer(client, nom="Catan")
    assert _urls_qr() == avant
    _importer(_exporter(tmp_path / "export.csv"))
    assert _urls_qr() == avant
    # Et la fiche publique répond toujours à ces adresses, sous le nouveau nom.
    assert "Catan" in client.get("/jeu/001").text


def test_nouvelle_boite_sous_le_nom_corrige_rejoint_le_titre(client, tmp_path):
    _enregistrer(client, nom="Catan")
    chemin = _exporter(tmp_path / "export.csv")
    texte = chemin.read_text(encoding="utf-8-sig")
    ligne = next(l for l in texte.splitlines() if l.startswith("001;"))
    chemin.write_text(texte + ligne.replace("001;", "009;", 1) + "\r\n",
                      encoding="utf-8-sig")
    _importer(chemin)
    titres, exemplaires = _instantane()
    assert exemplaires["009"]["reference_titre"] == "CATANE"
    assert "CATAN" not in titres


def test_la_casse_corrigee_dans_le_tableur_ne_scinde_pas(client, tmp_path):
    _enregistrer(client, nom="Catan")
    _importer(_csv(tmp_path, f"{ENTETE}\n001;catan;10\n002;CATAN;10\n"))
    titres, exemplaires = _instantane()
    assert "CATAN" not in titres
    assert {exemplaires[i]["reference_titre"] for i in ("001", "002")} == {"CATANE"}


def test_un_autre_nom_dans_le_tableur_reste_un_geste_volontaire(client, tmp_path):
    """Comportement d'avant, conservé : la boîte change de titre."""
    _importer(_csv(tmp_path, f"{ENTETE}\n002;Catane Junior;6\n"))
    titres, exemplaires = _instantane()
    assert exemplaires["002"]["reference_titre"] == "CATANE_JUNIOR"
    assert exemplaires["001"]["reference_titre"] == "CATANE"


def test_renommage_au_slug_d_un_autre_titre_puis_reimport(client, tmp_path):
    """
    « Dobble » renommé « Catane » (le slug de CATANE) : l'aller-retour garde
    les deux titres séparés ; une boîte NOUVELLE au nom « Catane » rejoint
    CATANE, le titre dont c'est la référence (règle 2 de resoudre_reference).
    """
    _enregistrer(client, "DOBBLE", nom="Catane")
    avant = _instantane()
    chemin = _exporter(tmp_path / "export.csv")
    _importer(chemin)
    assert _instantane() == avant
    texte = chemin.read_text(encoding="utf-8-sig")
    ligne = next(l for l in texte.splitlines() if l.startswith("003;"))
    chemin.write_text(texte + ligne.replace("003;", "010;", 1) + "\r\n",
                      encoding="utf-8-sig")
    _importer(chemin)
    assert _instantane()[1]["010"]["reference_titre"] == "CATANE"


def test_la_simulation_annonce_ce_que_fait_l_import(client, tmp_path):
    _enregistrer(client, nom="Catan")
    chemin = _exporter(tmp_path / "export.csv")
    texte = chemin.read_text(encoding="utf-8-sig")
    chemin.write_text(texte + "011;Catan;;;;;;;;;;;;;;\r\n", encoding="utf-8-sig")
    avant = _instantane()
    simulation = _importer(chemin, dry_run=True)
    assert _instantane() == avant
    reel = _importer(chemin)
    for cle in ("exemplaires", "titres", "groupes_multi", "titres_data",
                "corrections_defaites"):
        assert simulation[cle] == reel[cle], cle
    assert set(reel["titres_data"]) == {"CATANE", "DOBBLE"}


def test_la_simulation_sur_une_installation_neuve_ne_cree_pas_la_base(tmp_path, monkeypatch):
    from app import db
    neuve = tmp_path / "neuve.db"
    monkeypatch.setattr(db, "get_database_path", lambda: neuve)
    res = _importer(_csv(tmp_path, f"{ENTETE}\n001;Catan;10\n"), dry_run=True)
    assert res["titres"] == 1 and not neuve.exists()


# ===========================================================================
# 6. UN FICHIER PLUS ANCIEN QUE LA CORRECTION (POINT 3.2)
# ===========================================================================
def test_fichier_ancien_signale_ecrase_et_remet_modifie_le_a_vide(client, tmp_path,
                                                                  _journal_isole):
    ancien = _exporter(tmp_path / "ancien.csv")
    _enregistrer(client, age_min="12", editeur="Filosofia")
    contenu = ancien.read_bytes()
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("ancien.csv", contenu, "text/csv")})
    assert "Import réussi" in r.text
    # L'import s'est fait : le fichier fait foi.
    titre = _titre()
    assert titre["age_min"] == 10 and titre["editeur"] == "Kosmos"
    # Arbitrage : la correction est défaite, il n'y a plus rien à protéger.
    assert titre["modifie_le"] is None
    # Le message dit quelle fiche, quel champ, avant et fichier, avec le lien.
    assert "1 fiche corrigée" in r.text
    assert 'href="/admin/jeu/CATANE"' in r.text
    assert "Âge minimum : « 12 » → « 10 »" in r.text
    assert "Éditeur : « Filosofia » → « Kosmos »" in r.text
    # Le journal reçoit un nombre, jamais les valeurs.
    ligne, = _journal(_journal_isole, "import_corrections_defaites")
    assert ligne["objet"] == "1"
    assert "Filosofia" not in json.dumps(ligne, ensure_ascii=False)
    # Ré-importer le même fichier ne redit rien : la base lui est conforme.
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("ancien.csv", contenu, "text/csv")})
    assert "fiche corrigée" not in r.text


def test_fichier_identique_ne_dit_rien_et_garde_la_correction(client, tmp_path):
    _enregistrer(client, age_min="12")
    res = _importer(_exporter(tmp_path / "recent.csv"))
    assert res["corrections_defaites"] == []
    assert _titre()["modifie_le"] is not None


def test_base_jamais_corrigee_ne_dit_rien(client, tmp_path):
    res = _importer(_csv(tmp_path, f"{ENTETE}\n001;Catane;14\n"))
    assert res["corrections_defaites"] == []
    assert _titre()["age_min"] == 14


def test_fichier_ancien_avec_le_nom_d_avant_le_renommage(client, tmp_path):
    """L'export d'avant porte « Catane » : même référence, nom remis, signalé."""
    ancien = _exporter(tmp_path / "ancien.csv")
    _enregistrer(client, nom="Catan")
    res = _importer(ancien)
    fiche, = res["corrections_defaites"]
    assert ("Nom du jeu", "Catan", "Catane") in fiche["ecarts"]
    titres, exemplaires = _instantane()
    assert titres["CATANE"]["nom"] == "Catane"
    assert {exemplaires[i]["reference_titre"] for i in ("001", "002")} == {"CATANE"}


def test_une_boite_qui_quitte_un_titre_corrige_est_signalee(client, tmp_path):
    _enregistrer(client, age_min="12")
    res = _importer(_csv(tmp_path, f"{ENTETE}\n002;Catane Junior;6\n"))
    fiche, = res["corrections_defaites"]
    assert fiche["reference_titre"] == "CATANE" and fiche["ecrase"] is False
    assert fiche["ecarts"] == [("Nom du jeu", "Catane", "Catane Junior")]
    # La fiche n'a pas été écrasée : sa correction reste protégée.
    assert _titre()["modifie_le"] is not None


def test_affichage_borne_total_exact(client, tmp_path, monkeypatch):
    from app.routes import admin
    monkeypatch.setattr(admin, "CORRECTIONS_AFFICHEES", 1)
    ancien = _exporter(tmp_path / "ancien.csv")
    _enregistrer(client, age_min="12")
    _enregistrer(client, "DOBBLE", age_min="8")
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("a.csv", ancien.read_bytes(), "text/csv")})
    assert "2 fiches corrigées" in r.text
    assert r.text.count('href="/admin/jeu/') == 1
    assert "… et 1 autres" in r.text


# ===========================================================================
# 7. L'ÉTIQUETTE PÉRIMÉE (POINT 4)
# ===========================================================================
def _png(client, id_ex="001"):
    return client.get(f"/admin/etiquette/{id_ex}.png").content


def test_texte_imprime_suit_l_image(client):
    """
    `texte_imprime` dit ce que l'étiquette lit du titre : un champ qu'il ignore
    ne change pas l'image ; un champ qu'il lit la change.
    """
    avant = _png(client)
    _enregistrer(client, editeur="Filosofia", descriptif="Autre", categorie="Famille",
                 annee_edition="2001", auteur="Quelqu'un", type_jeu="Extension")
    assert _png(client) == avant
    _enregistrer(client, nom="Catan")
    assert _png(client) != avant


def test_annonce_quand_l_imprime_change(client):
    r = _enregistrer(client, nom="Catan")
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=enregistre&etiquette=1"
    page = client.get(r.headers["location"]).text
    assert "n'est plus à jour" in page
    bloc = re.search(r'<form method="post" action="/admin/etiquettes/pdf">.*?</form>',
                     page, re.S).group(0)
    assert 'name="references" value="CATANE"' in bloc
    assert "Réimprimer les étiquettes de ce jeu" in bloc
    # Le bouton aboutit : la planche de tous les exemplaires du jeu.
    pdf = client.post("/admin/etiquettes/pdf", data={"references": "CATANE"})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize("champs", [
    {"age_min": "12"}, {"duree_min": "60"}, {"nb_joueurs_max": "6"}])
def test_annonce_pour_chaque_champ_du_code(client, champs):
    assert _enregistrer(client, **champs).headers["location"].endswith("&etiquette=1")


def test_pas_d_annonce_quand_l_imprime_ne_change_pas(client):
    r = _enregistrer(client, editeur="Filosofia", descriptif="Autre texte")
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=enregistre"
    assert "n'est plus à jour" not in client.get(r.headers["location"]).text


def test_zero_vaut_absent_en_lisible_mais_pas_en_dense(client):
    """Âge vide -> 0 : rien ne change en lisible, `?` devient `0` en dense."""
    _enregistrer(client, age_min="")
    r = _enregistrer(client, age_min="0")
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=enregistre"
    conn = _conn()
    try:
        services.ecrire_etiquette_format(conn, classement.FORMAT_DENSE)
    finally:
        conn.close()
    _enregistrer(client, age_min="")
    assert _enregistrer(client, age_min="0").headers["location"].endswith("&etiquette=1")


# ===========================================================================
# 8. EFFACER LES LETTRES DE CLASSEMENT (POINT 5)
# ===========================================================================
def _poser_lettres(ref="CATANE", lettres=("T", "P", "C")):
    _executer("UPDATE titres SET lettre_public = ?, lettre_jeu = ?, lettre_materiel = ? "
              "WHERE reference_titre = ?", (*lettres, ref))


def _lettres(ref="CATANE"):
    titre = _titre(ref)
    return tuple(titre[c] for c in classement.COLONNES_LETTRES)


def test_sans_lettre_ni_section_ni_bouton(client):
    page = client.get("/admin/jeu/CATANE").text
    assert "Lettres de classement" not in page
    assert "/lettres/effacer" not in page


def test_les_lettres_s_affichent_avec_leur_libelle(client, listes_remplies):
    _poser_lettres()
    page = client.get("/admin/jeu/CATANE").text
    assert "<h2>Lettres de classement</h2>" in page
    assert "Public : <strong>T</strong> — Tout public" in page
    assert "Façon de jouer : <strong>P</strong> — Placement" in page
    assert "Matériel : <strong>C</strong> — Cartes" in page
    # Le bouton dit ce qu'il fait, en mots : pas seulement une couleur.
    assert "Effacer les trois lettres" in page
    # Aucune saisie de lettre.
    assert not re.search(r'name="lettre', page)


def test_une_lettre_seule_suffit_a_montrer_le_bouton(client):
    _poser_lettres(lettres=("T", None, None))
    page = client.get("/admin/jeu/CATANE").text
    assert "Public : <strong>T</strong> — Tout public" in page
    assert "/admin/jeu/CATANE/lettres/effacer" in page


def test_effacer_les_trois_lettres(client, listes_remplies, _journal_isole):
    _poser_lettres()
    r = client.post("/admin/jeu/CATANE/lettres/effacer", follow_redirects=False)
    assert r.status_code == 303
    # Les lettres étaient imprimables : l'étiquette change, on le dit.
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=lettres&etiquette=1"
    assert _lettres() == (None, None, None)
    assert _titre()["modifie_le"] is None
    ligne, = _journal(_journal_isole, "lettres_effacees")
    assert ligne["objet"] == "Catane" and ligne["ref"] == "CATANE"
    page = client.get(r.headers["location"]).text
    assert "Les trois lettres de classement sont effacées." in page


def test_effacer_des_lettres_non_imprimables_n_annonce_rien(client, _journal_isole):
    """Listes 2 et 3 vides : ces lettres ne s'imprimaient pas."""
    _poser_lettres()
    r = client.post("/admin/jeu/CATANE/lettres/effacer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=lettres"
    assert _lettres() == (None, None, None)


def test_effacer_sans_lettre_n_ecrit_rien(client, _journal_isole):
    r = client.post("/admin/jeu/CATANE/lettres/effacer", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=inchange"
    assert _journal(_journal_isole, "lettres_effacees") == []


def test_l_editeur_ne_touche_pas_aux_lettres(client):
    _poser_lettres()
    _enregistrer(client, nom="Catan", age_min="12")
    assert _lettres() == ("T", "P", "C")


def test_la_fiche_s_elargit_sur_grand_ecran(client):
    """
    Retour de Simon (2026-10-14) : l'éditeur était à la largeur d'un
    téléphone. La page passe en conteneur large ; le formulaire prend ses deux
    colonnes de champs et les boîtes leur grille, toutes deux réservées aux
    écrans d'au moins 900px par la feuille de style.
    """
    page = client.get("/admin/jeu/CATANE").text
    assert re.search(r'<main class="contenu contenu-large"', page)
    assert "form-2col" in page
    assert 'class="exemplaires-grille"' in page
    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    bloc = css[css.index(".exemplaires-grille {") - 200:css.index(".exemplaires-grille {")]
    assert "@media (min-width: 900px)" in bloc


def test_la_liste_des_jeux_filtre_ceux_sans_fiche_bgg(client):
    """
    Liste de travail de l'association manuelle : la case ne garde que les
    jeux sans identifiant BGG, se rouvre cochée, et le compte le dit.
    """
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    tous = client.get("/admin/jeux").text
    assert "/admin/jeu/CATANE" in tous and "/admin/jeu/DOBBLE" in tous
    assert 'id="sans-bgg"' in tous and "checked" not in _case_sans_bgg(tous)

    filtre = client.get("/admin/jeux?sans_bgg=1").text
    assert "/admin/jeu/CATANE" not in filtre
    assert "/admin/jeu/DOBBLE" in filtre
    assert "checked" in _case_sans_bgg(filtre)
    assert "1 jeu sans fiche BoardGameGeek" in filtre

    # Combinable avec la recherche par nom. (Le lien « Relier les jeux un par
    # un », lot fiche-3, mène à DOBBLE quel que soit le filtre : on cherche
    # l'entrée de la LISTE, dont l'adresse se ferme sans ancre.)
    assert 'href="/admin/jeu/DOBBLE"' not in client.get("/admin/jeux?sans_bgg=1&q=cat").text


def test_le_filtre_bgg_n_atteint_pas_le_catalogue_public(anonyme):
    """Le catalogue public ignore le paramètre : il ne parle jamais de BGG."""
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    page = anonyme.get("/catalogue?sans_bgg=1").text
    assert "Catane" in page
    assert "BoardGameGeek" not in page


def test_le_filtre_bgg_exige_la_session(anonyme):
    r = anonyme.get("/admin/jeux?sans_bgg=1", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)
    assert "Dobble" not in r.text


def _case_sans_bgg(page):
    debut = page.index('id="sans-bgg"')
    return page[debut:page.index(">", debut)]

