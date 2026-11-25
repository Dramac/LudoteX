"""
JEUX DEMANDÉS AU COMPTOIR (série comptoir, lot 11).

Un bénévole note le jeu qu'un visiteur a demandé ; le bureau relit, fusionne,
exporte et purge. Le nom est un texte libre : une PORTE à donnée personnelle,
ouverte à cinq conditions (app/jeux_demandes.py). Ce fichier vérifie :

1. le modèle : table créée seule par `init_db`, sans colonne d'origine ;
2. la saisie : succès, vide, trop long, double envoi, jeu du catalogue,
   regroupement (casse, accents), suggestions du `datalist`, consigne ;
3. jamais public : chaque route refusée sans jeton ou sans mot de passe, le
   lien du catalogue absent pour un visiteur ;
4. le bureau : liste groupée, filtre d'édition, fusion, suppression d'un
   groupe, purge, export ;
5. effacement : purge vérifiée sur les OCTETS de la base et d'une archive
   produite après elle ; une archive d'avant la purge les garde ; clôture
   qui garde, formation qui efface ;
6. journal : jamais le nom (scénario + contrôle statique).

Catalogue de départ : « Codenames » (CODENAMES, boîtes 001 et 002, la 002
dehors) et « Dobble » (DOBBLE, boîte 003).
"""

import io
import json
import zipfile

import pytest

from app import jeux_demandes, services

MOT_DE_PASSE = "mot-de-passe-de-test"
JETON = "jeton-de-test-jeux-demandes-0123456789"
# Une demande au texte reconnaissable, telle qu'un bénévole pressé pourrait la
# taper malgré la consigne : c'est elle qu'on cherche dans les octets.
MARQUE = "Catan pour MadameTemoinZorbaq"


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.setenv("PRET_TOKEN", JETON)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
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
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CODENAMES', 'Codenames')")
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DOBBLE', 'Dobble')")
    conn.executemany("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                     [("001", "CODENAMES"), ("002", "CODENAMES"), ("003", "DOBBLE")])
    conn.commit()
    services.preter(conn, "002")
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
    """
    Connexion à la base DU TEST. Refuse toute autre base : appelée avant la
    fixture `bases`, elle écrirait dans la base locale de l'instance (c'est
    arrivé pendant l'écriture de ce fichier).
    """
    from app import db
    assert "pytest-" in str(db.get_database_path()), "fixture `bases` non demandée"
    return db.get_connection()


def _texte(r) -> str:
    """Le texte de la page, entités décodées (les apostrophes sont échappées)."""
    import html
    return html.unescape(r.text)


def _lignes():
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, nom, cle, demandee_le, jeton_formulaire FROM jeux_demandes ORDER BY id")]
    finally:
        conn.close()


def _jeton_de(page: str) -> str:
    import re
    return re.search(r'name="jeton" value="([^"]+)"', page).group(1)


def _noter(client, nom, jeton=None):
    """Envoie le formulaire comme un vrai appui : avec le jeton de la page affichée."""
    if jeton is None:
        jeton = _jeton_de(client.get("/jeux-demandes").text)
    return client.post("/jeux-demandes", data={"nom": nom, "jeton": jeton})


def _octets(chemin) -> bytes:
    """Les octets du fichier de la base, journal WAL compris s'il existe encore."""
    octets = chemin.read_bytes()
    wal = chemin.with_name(chemin.name + "-wal")
    if wal.exists():
        octets += wal.read_bytes()
    return octets


def _journal(chemin, action=None):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l]
    return [l for l in lignes if action is None or l["action"] == action]


# ---------------------------------------------------------------------------
# 1. Le modèle
# ---------------------------------------------------------------------------
def test_la_table_est_creee_par_init_db_sans_colonne_d_origine(bases):
    conn = _conn()
    try:
        colonnes = [r["name"] for r in conn.execute("PRAGMA table_info(jeux_demandes)")]
    finally:
        conn.close()
    assert colonnes == ["id", "nom", "cle", "demandee_le", "jeton_formulaire"]


def test_init_db_est_idempotent_et_garde_les_demandes(bases):
    from app import db

    conn = _conn()
    try:
        jeux_demandes.noter(conn, "Karuba", None)
        db.init_db(conn)
        db.init_db(conn)
        assert jeux_demandes.total(conn) == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. La saisie
# ---------------------------------------------------------------------------
def test_la_page_porte_le_champ_la_consigne_et_le_datalist(benevole):
    r = benevole.get("/jeux-demandes")
    assert r.status_code == 200
    assert '<label for="nom-jeu">Nom du jeu</label>' in _texte(r)
    assert 'aria-describedby="nom-jeu-consigne"' in _texte(r)
    assert f'id="nom-jeu-consigne">{jeux_demandes.CONSIGNE}</p>' in _texte(r)
    assert 'list="jeux-deja-demandes"' in _texte(r) and '<datalist id="jeux-deja-demandes">' in _texte(r)
    assert f'maxlength="{jeux_demandes.LONGUEUR_MAX_NOM}"' in _texte(r)
    assert "Noter la demande" in _texte(r)
    assert "<script" not in _texte(r).split('<main')[-1].split('</main>')[0]
    assert "Aucune demande notée aujourd'hui." in _texte(r)


def test_noter_une_demande(benevole):
    r = _noter(benevole, "  Catan  ")
    assert r.status_code == 200
    assert 'role="status"' in _texte(r)
    assert "Demande notée : Catan, 1 fois en tout." in _texte(r)
    (ligne,) = _lignes()
    assert ligne["nom"] == "Catan" and ligne["cle"] == "CATAN"
    # Le champ est vidé, et la demande apparaît dans celles du jour.
    assert 'name="nom" value=""' in _texte(r)
    assert "<li>Catan <span class=\"badge\">1 fois</span></li>" in _texte(r)


def test_regroupement_casse_et_accents(benevole):
    _noter(benevole, "Catan")
    _noter(benevole, "Catan")
    r = _noter(benevole, "CATÂN")
    # Le groupe s'affiche sous sa graphie la plus fréquente.
    assert "Demande notée : Catan, 3 fois en tout." in _texte(r)
    assert {l["cle"] for l in _lignes()} == {"CATAN"}


def test_les_suggestions_proposent_un_nom_par_groupe_le_plus_frequent(benevole):
    _noter(benevole, "catan")
    _noter(benevole, "Catan")
    _noter(benevole, "Catan")
    _noter(benevole, "Karuba")
    r = benevole.get("/jeux-demandes")
    datalist = r.text.split('<datalist id="jeux-deja-demandes">')[1].split("</datalist>")[0]
    assert datalist.count("<option") == 2
    assert '<option value="Catan">' in datalist and '<option value="Karuba">' in datalist


def test_nom_vide_refuse_sans_rien_ecrire(benevole):
    r = _noter(benevole, "   ")
    assert r.status_code == 400
    assert 'role="alert"' in _texte(r) and 'aria-invalid="true"' in _texte(r)
    assert services.MESSAGES_NOM_EPHEMERE["vide"] in _texte(r)
    assert _lignes() == []


def test_nom_trop_long_revient_dans_le_champ_sans_rien_ecrire(benevole):
    long = "Catan " + "x" * jeux_demandes.LONGUEUR_MAX_NOM
    r = _noter(benevole, long)
    assert r.status_code == 400
    assert services.MESSAGES_NOM_EPHEMERE["long"] in _texte(r)
    assert f'value="{long}"' in _texte(r)
    assert _lignes() == []


def test_double_envoi_compte_une_fois(benevole):
    jeton = _jeton_de(benevole.get("/jeux-demandes").text)
    _noter(benevole, "Catan", jeton)
    r = _noter(benevole, "Catan", jeton)
    assert r.status_code == 200
    assert "Cette demande était déjà notée : Catan, 1 fois en tout." in _texte(r)
    assert len(_lignes()) == 1
    # Une SECONDE demande réelle, depuis la page affichée à neuf, compte.
    r = _noter(benevole, "Catan", _jeton_de(r.text))
    assert "Demande notée : Catan, 2 fois en tout." in _texte(r)


def test_chaque_affichage_porte_un_jeton_neuf(benevole):
    assert _jeton_de(benevole.get("/jeux-demandes").text) != \
        _jeton_de(benevole.get("/jeux-demandes").text)


def test_un_jeu_du_catalogue_est_note_quand_meme(benevole):
    r = _noter(benevole, "codenames")
    assert "Demande notée : codenames, 1 fois en tout." in _texte(r)
    assert "Ce jeu est au catalogue (2 boîtes, 1 dehors)." in _texte(r)
    assert len(_lignes()) == 1


def test_le_catalogue_mene_a_la_page_pre_remplie_sans_rien_noter(benevole):
    r = benevole.post("/jeux-demandes/preparer", data={"nom": "Codenames"})
    assert r.status_code == 200
    assert 'name="nom" value="Codenames"' in _texte(r)
    assert "Ce jeu est au catalogue (2 boîtes, 1 dehors) : vous pouvez noter la demande quand même." in _texte(r)
    assert _lignes() == []


# ---------------------------------------------------------------------------
# 3. Jamais public
# ---------------------------------------------------------------------------
LIEN_CATALOGUE = "Noter ce jeu comme demandé"


def test_lien_du_catalogue_pour_un_benevole_quand_la_recherche_ne_donne_rien(benevole):
    r = benevole.get("/catalogue?q=Karuba")
    assert "Aucun jeu ne correspond à ces critères." in _texte(r)
    assert LIEN_CATALOGUE in _texte(r)
    assert 'action="/jeux-demandes/preparer"' in _texte(r)
    assert '<input type="hidden" name="nom" value="Karuba">' in _texte(r)


def test_lien_du_catalogue_quand_tout_est_dehors(benevole, bases):
    conn = _conn()
    try:
        services.preter(conn, "001")
    finally:
        conn.close()
    assert LIEN_CATALOGUE in benevole.get("/catalogue?q=Codenames").text


def test_pas_de_lien_quand_une_boite_est_disponible_ou_sans_recherche(benevole):
    assert LIEN_CATALOGUE not in benevole.get("/catalogue?q=Codenames").text
    assert LIEN_CATALOGUE not in benevole.get("/catalogue").text


def test_un_visiteur_ne_voit_jamais_le_lien(visiteur):
    r = visiteur.get("/catalogue?q=Karuba")
    assert "Aucun jeu ne correspond à ces critères." in _texte(r)
    assert LIEN_CATALOGUE not in _texte(r) and "jeux-demandes" not in _texte(r)


@pytest.mark.parametrize("methode, chemin, donnees", [
    ("get", "/jeux-demandes", None),
    ("post", "/jeux-demandes", {"nom": "Catan", "jeton": "x"}),
    ("post", "/jeux-demandes/preparer", {"nom": "Catan"}),
])
def test_routes_benevoles_refusees_sans_jeton(visiteur, methode, chemin, donnees):
    r = getattr(visiteur, methode)(chemin, **({"data": donnees} if donnees else {}))
    assert r.status_code == 403
    assert _lignes() == []


@pytest.mark.parametrize("methode, chemin, donnees", [
    ("get", "/admin/jeux-demandes", None),
    ("get", "/admin/jeux-demandes/export.xlsx", None),
    ("get", "/admin/jeux-demandes/purger", None),
    ("post", "/admin/jeux-demandes/purger", {"confirmation": "oui"}),
    ("post", "/admin/jeux-demandes/supprimer", {"cle": "CATAN", "confirmation": "oui"}),
    ("post", "/admin/jeux-demandes/fusionner",
     {"source": "CATANE", "cible": "CATAN", "confirmation": "oui"}),
])
@pytest.mark.parametrize("qui", ["visiteur", "benevole"])
def test_routes_du_bureau_refusees_sans_mot_de_passe(bases, request, qui, methode, chemin,
                                                     donnees):
    conn = _conn()
    try:
        jeux_demandes.noter(conn, "Catan", None)
        jeux_demandes.noter(conn, "Catane", None)
    finally:
        conn.close()
    client = request.getfixturevalue(qui)
    r = getattr(client, methode)(chemin, follow_redirects=False,
                                 **({"data": donnees} if donnees else {}))
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert {l["cle"] for l in _lignes()} == {"CATAN", "CATANE"}


def test_rien_dans_les_pages_publiques(visiteur, bases):
    conn = _conn()
    try:
        jeux_demandes.noter(conn, MARQUE, None)
    finally:
        conn.close()
    for chemin in ("/", "/catalogue", "/catalogue?q=Catan", "/stats", "/live", "/live/data",
                   "/apropos", "/jeu/001"):
        r = visiteur.get(chemin)
        assert MARQUE not in _texte(r), chemin
        assert "demandé" not in _texte(r).lower() or chemin == "/", chemin


def test_aucun_nom_ne_passe_par_une_adresse():
    """Les gabarits n'envoient le nom ou la clé que par POST, jamais en GET."""
    from pathlib import Path

    gabarits = Path(__file__).resolve().parent.parent / "app" / "templates"
    for nom in ("jeux_demandes.html", "admin_jeux_demandes.html",
                "admin_jeux_demandes_confirmer.html", "catalogue.html"):
        texte = (gabarits / nom).read_text(encoding="utf-8")
        assert "jeux-demandes?nom" not in texte and "?cle=" not in texte
        assert "?source=" not in texte


# ---------------------------------------------------------------------------
# 4. Le bureau
# ---------------------------------------------------------------------------
def _poser(*noms):
    conn = _conn()
    try:
        for n in noms:
            jeux_demandes.noter(conn, n, None)
    finally:
        conn.close()


def test_liste_groupee_du_bureau(admin):
    _poser("Catan", "catan", "Catane", "Codenames")
    r = admin.get("/admin/jeux-demandes")
    assert r.status_code == 200
    assert "3 jeux demandés" in _texte(r)
    groupes = [g["nom"] for g in jeux_demandes.groupes(_conn())]
    assert groupes[0] == "catan" or groupes[0] == "Catan"
    assert '<a class="lien" href="/admin/jeu/CODENAMES">Oui, 2' in _texte(r)
    assert "Tout purger (4 demandes)" in _texte(r)


def test_le_tableau_de_bord_mene_a_l_ecran(admin):
    assert 'href="/admin/jeux-demandes"' in admin.get("/admin").text


def test_filtre_par_edition(admin, bases):
    from app import editions

    conn = _conn()
    try:
        editions.enregistrer(conn, {"nom": "Édition 2025", "premier_jour": "2025-11-15",
                                    "dernier_jour": "2025-11-16"})
        conn.execute("INSERT INTO jeux_demandes (nom, cle, demandee_le) "
                     "VALUES ('Karuba', 'KARUBA', '2025-11-15T10:00:00+00:00')")
        conn.commit()
        (id_ed,) = conn.execute("SELECT id FROM editions_evenement").fetchone()
    finally:
        conn.close()
    _poser("Catan")
    r = admin.get(f"/admin/jeux-demandes?edition={id_ed}")
    assert '<td class="cartes-titre">Karuba</td>' in r.text
    assert '<td class="cartes-titre">Catan</td>' not in r.text
    r = admin.get("/admin/jeux-demandes?edition=tout")
    assert '<td class="cartes-titre">Catan</td>' in r.text
    assert admin.get("/admin/jeux-demandes?edition=9999").status_code == 200


def test_fusion_demande_confirmation_puis_reecrit_la_cle(admin):
    _poser("Catan", "Catan", "Catane")
    r = admin.post("/admin/jeux-demandes/fusionner", data={"source": "CATANE", "cible": "CATAN"})
    assert r.status_code == 200 and "Fusionner des demandes" in _texte(r)
    assert "3 demandes en tout" in _texte(r)
    assert {l["cle"] for l in _lignes()} == {"CATAN", "CATANE"}
    r = admin.post("/admin/jeux-demandes/fusionner",
                   data={"source": "CATANE", "cible": "CATAN", "confirmation": "oui"})
    assert "« Catane » fusionné dans « Catan » : 1 demande déplacée." in _texte(r)
    lignes = _lignes()
    assert len(lignes) == 3 and {(l["cle"], l["nom"]) for l in lignes} == {("CATAN", "Catan")}


def test_fusion_d_un_groupe_disparu_ne_bloque_pas(admin):
    _poser("Catan")
    r = admin.post("/admin/jeux-demandes/fusionner",
                   data={"source": "CATANE", "cible": "CATAN", "confirmation": "oui"})
    assert "Rien n'a changé" in _texte(r) and 'role="alert"' in _texte(r)
    r = admin.post("/admin/jeux-demandes/fusionner",
                   data={"source": "CATAN", "cible": "CATAN", "confirmation": "oui"})
    assert "Rien n'a changé" in _texte(r)


def test_suppression_d_un_groupe(admin, bases):
    _poser(MARQUE, MARQUE, "Karuba")
    cle = services.slug_titre(MARQUE)
    r = admin.post("/admin/jeux-demandes/supprimer", data={"cle": cle})
    assert "C'est définitif" in _texte(r)
    assert len(_lignes()) == 3
    r = admin.post("/admin/jeux-demandes/supprimer", data={"cle": cle, "confirmation": "oui"})
    assert "2 demandes effacées définitivement." in _texte(r)
    assert [l["nom"] for l in _lignes()] == ["Karuba"]
    assert MARQUE.encode() not in _octets(bases / "test.db")


def test_purge_demande_confirmation(admin):
    _poser("Catan")
    r = admin.get("/admin/jeux-demandes/purger")
    assert "C'est définitif" in _texte(r)
    from app import sauvegarde
    assert f"{sauvegarde.DUREE_VIE_ARCHIVES_JOURS} jours au plus" in _texte(r)
    r = admin.post("/admin/jeux-demandes/purger")
    assert r.status_code == 400 and "Cochez la case" in _texte(r)
    assert len(_lignes()) == 1
    r = admin.post("/admin/jeux-demandes/purger", data={"confirmation": "oui"})
    assert "1 demande effacée définitivement." in _texte(r)
    assert _lignes() == []
    r = admin.post("/admin/jeux-demandes/purger", data={"confirmation": "oui"})
    assert "Il n'y avait aucune demande à effacer." in _texte(r)


def test_export_du_bureau(admin):
    from openpyxl import load_workbook

    _poser("Catan", "Catan", "Codenames")
    r = admin.get("/admin/jeux-demandes/export.xlsx")
    assert r.status_code == 200
    feuille = load_workbook(io.BytesIO(r.content)).active
    lignes = list(feuille.iter_rows(values_only=True))
    assert lignes[0] == tuple(__import__("app.routes.jeux_demandes", fromlist=["x"]).EN_TETES_EXPORT)
    assert lignes[1][:2] == ("Catan", 2) and lignes[1][4] == "non"
    assert lignes[2][:2] == ("Codenames", 1) and lignes[2][4:] == ("oui", 2)


# ---------------------------------------------------------------------------
# 5. Effacement, archives, clôture, formation
# ---------------------------------------------------------------------------
def _base_de_l_archive(octets_zip: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(octets_zip)) as zf:
        return zf.read("pret-jeux.db")


def test_purge_verifiee_sur_les_octets_de_la_base_et_d_une_archive_posterieure(admin, bases):
    from app import sauvegarde

    _poser(MARQUE)
    avant = _base_de_l_archive(sauvegarde.creer_zip_sauvegarde())
    # Règle retenue (précédent du planning) : les demandes partent dans les
    # archives — une restauration les ramène.
    assert MARQUE.encode() in avant
    admin.post("/admin/jeux-demandes/purger", data={"confirmation": "oui"})
    assert MARQUE.encode() not in _octets(bases / "test.db")
    apres = _base_de_l_archive(sauvegarde.creer_zip_sauvegarde())
    assert MARQUE.encode() not in apres


def test_fusion_n_en_laisse_pas_l_ancien_nom_dans_le_fichier(admin, bases):
    _poser("Catan", MARQUE)
    cle = services.slug_titre(MARQUE)
    admin.post("/admin/jeux-demandes/fusionner",
               data={"source": cle, "cible": "CATAN", "confirmation": "oui"})
    assert {l["nom"] for l in _lignes()} == {"Catan"}
    assert b"MadameTemoinZorbaq" not in _octets(bases / "test.db")


def test_la_cloture_garde_les_demandes(bases):
    _poser("Catan")
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
        assert jeux_demandes.total(conn) == 1
    finally:
        conn.close()


def test_la_reinitialisation_de_la_formation_efface_sur_les_octets(bases, monkeypatch):
    from app import formation

    monkeypatch.setattr(formation, "_chemin_catalogue_csv", lambda: None)
    _poser(MARQUE)
    conn = _conn()
    try:
        formation.peupler_pret(conn, [f"Jeu {i}" for i in range(50)])
        assert jeux_demandes.total(conn) == 0
    finally:
        conn.close()
    assert MARQUE.encode() not in _octets(bases / "test.db")


def test_toute_suppression_passe_sous_secure_delete():
    """Garde-fou de classe : toute requête de `app/` qui supprime ou réécrit
    une demande vit dans une fonction qui pose `secure_delete`."""
    import ast
    from pathlib import Path

    racine = Path(__file__).resolve().parent.parent / "app"
    vues = []
    for fichier in racine.rglob("*.py"):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for fonction in ast.walk(arbre):
            if not isinstance(fonction, ast.FunctionDef):
                continue
            chaines = [n.value for n in ast.walk(fonction)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if not any(("DELETE FROM jeux_demandes" in c or "UPDATE jeux_demandes" in c)
                       for c in chaines):
                continue
            vues.append(f"{fichier.name}:{fonction.name}")
            assert any("secure_delete = ON" in c for c in chaines), vues[-1]
    assert set(vues) == {"jeux_demandes.py:fusionner", "jeux_demandes.py:supprimer_groupe",
                         "jeux_demandes.py:purger"}


# ---------------------------------------------------------------------------
# 6. Journal
# ---------------------------------------------------------------------------
def test_le_journal_ne_porte_jamais_le_nom(admin, benevole, _journal_isole):
    _noter(benevole, MARQUE)
    _noter(benevole, "x" * 200 + MARQUE)       # refusé : trop long
    _noter(benevole, "Catan")
    cle = services.slug_titre(MARQUE)
    admin.post("/admin/jeux-demandes/fusionner",
               data={"source": cle, "cible": "CATAN", "confirmation": "oui"})
    admin.get("/admin/jeux-demandes/export.xlsx")
    _poser(MARQUE)
    admin.post("/admin/jeux-demandes/supprimer", data={"cle": cle, "confirmation": "oui"})
    admin.post("/admin/jeux-demandes/purger", data={"confirmation": "oui"})
    texte = _journal_isole.read_text(encoding="utf-8")
    for fragment in ("Zorbaq", "Catan", "CATAN", "Madame"):
        assert fragment not in texte
    actions = [l["action"] for l in _journal(_journal_isole)]
    assert actions.count("jeu_demande_notee") == 3
    for action in ("jeux_demandes_fusionnes", "jeux_demandes_exportes",
                   "jeux_demandes_supprimes", "jeux_demandes_purges"):
        assert action in actions
    refus = [l for l in _journal(_journal_isole, "jeu_demande_notee") if not l["ok"]]
    assert [l["detail"] for l in refus] == ["long"] and "objet" not in refus[0]
    assert _journal(_journal_isole, "jeux_demandes_fusionnes")[0]["objet"] == "1 demande"


def test_double_envoi_n_ecrit_rien_au_journal(benevole, _journal_isole):
    jeton = _jeton_de(benevole.get("/jeux-demandes").text)
    _noter(benevole, "Catan", jeton)
    _noter(benevole, "Catan", jeton)
    assert len(_journal(_journal_isole, "jeu_demande_notee")) == 1


# Les SEULS noms qu'une ligne de journal des jeux demandés peut citer : des
# nombres, la raison fermée d'un refus, et ce qui les met en forme. Liste
# d'autorisation (patron de test_journal_interdits.py, lot annonce-2) : un
# `objet=nom` ou `objet=g_source["nom"]` ferait tomber le test.
NOMS_JOURNALISABLES = {"fusionnees", "effacees", "donnees", "raison", "services", "len"}


def test_aucune_route_des_jeux_demandes_ne_journalise_un_nom():
    import ast
    from pathlib import Path

    fichier = Path(__file__).resolve().parent.parent / "app" / "routes" / "jeux_demandes.py"
    arbre = ast.parse(fichier.read_text(encoding="utf-8"))
    appels = [n for n in ast.walk(arbre) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "journaliser"]
    assert len(appels) >= 6
    for appel in appels:
        for mot in appel.keywords:
            assert mot.arg not in ("ref", "qui", "appareil")
            if mot.arg not in ("objet", "detail"):
                continue
            suspects = {n.id for n in ast.walk(mot.value) if isinstance(n, ast.Name)} \
                - NOMS_JOURNALISABLES
            assert not suspects, f"journalise {suspects} en `{mot.arg}`"
            # Pas d'indice de dictionnaire (g["nom"]) ni d'attribut autre que
            # `services.pluriel`.
            assert not any(isinstance(n, ast.Subscript) for n in ast.walk(mot.value))
            assert {n.attr for n in ast.walk(mot.value) if isinstance(n, ast.Attribute)} \
                <= {"pluriel"}
