"""
Tombola — tirages, remise des lots, édition (série tombola, lot 1).

Ce que ce fichier verrouille :

1. le MODULE : désactivé par défaut sans ligne en base, page « module
   indisponible », lien de menu, jeton exigé dans chacun des quatre états,
   `/tombola` laissé libre pour le lot 2 ;
2. la SAISIE MANUELLE : chaque numéro jugé seul, rien d'écrit sans numéro
   valide, saisie réaffichée ;
3. le TIRAGE PAR L'APPLICATION : {1 … max} moins les sortis, générateur
   injecté, maximum retenu ;
4. le DOUBLE APPUI : deux envois simultanés du même jeton de formulaire ne
   font qu'un tirage (leçon de la 1.32) ;
5. l'INDEX UNIQUE PARTIEL et l'édition unique ouverte, sous concurrence ;
6. la REMISE et son ANNULATION (instant injecté, jamais de `sleep`) ;
7. l'ERREUR DE SAISIE, bornée dans le temps ;
8. l'ÉDITION et la CLÔTURE de fin d'événement ;
9. la FORMATION, la MIGRATION d'une base d'avant, le JOURNAL.

Aucun exemple ne nomme personne : des numéros, rien d'autre.
"""

import ast
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import journal, models, services, tombola
from app.modules import MODULES, etat_defaut_module, lire_etat_module

JETON = "jeton-test-secret-32-caracteres"
MOT_DE_PASSE = "secret-admin-123"
LIEN_MENU = '<a href="/tombola/gestion">Tombola</a>'
T0 = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures et aides
# ---------------------------------------------------------------------------
@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.setenv("PRET_TOKEN", JETON)
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
    db.init_db()
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


@pytest.fixture
def actif(bases):
    """Module réglé sur « bénévoles » : la page de gestion est servie."""
    _etat("benevoles")
    return bases


def _conn():
    from app import db
    return db.get_connection()


def _etat(etat):
    conn = _conn()
    try:
        services.ecrire_parametre(conn, "module_tombola", etat)
    finally:
        conn.close()


def _sql(requete, params=()):
    conn = _conn()
    try:
        return [dict(l) for l in conn.execute(requete, params)]
    finally:
        conn.close()


def _numeros():
    return _sql("SELECT * FROM tombola_numeros ORDER BY id")


def _tirages():
    return _sql("SELECT * FROM tombola_tirages ORDER BY id")


def _editions():
    return _sql("SELECT * FROM tombola_editions ORDER BY id")


def _saisir(*numeros, jeton=None, instant=T0):
    conn = _conn()
    try:
        return tombola.saisir_tirage(conn, list(numeros), jeton or tombola.nouveau_jeton(),
                                     instant)
    finally:
        conn.close()


class Generateur:
    """Générateur injecté : garde la population reçue, rend les k premiers."""

    def __init__(self):
        self.population = None

    def sample(self, population, k):
        self.population = list(population)
        return list(population)[:k]


def _tirer(saisie_max, jeton=None, generateur=None, instant=T0):
    conn = _conn()
    try:
        return tombola.tirer_au_hasard(conn, saisie_max, jeton or tombola.nouveau_jeton(),
                                       instant, generateur or Generateur())
    finally:
        conn.close()


def _id(numero):
    return next(l["id"] for l in reversed(_numeros()) if l["numero"] == numero)


def _geste(fonction, *args, instant=T0):
    conn = _conn()
    try:
        return fonction(conn, *args, instant=instant)
    finally:
        conn.close()


def _parametre(cle):
    conn = _conn()
    try:
        return services.lire_parametre(conn, cle)
    finally:
        conn.close()


def _journal(chemin, prefixe="tombola_"):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l]
    return [l for l in lignes if l["action"].startswith(prefixe)]


def _jeton_de(page, nom_formulaire):
    """Le jeton caché du formulaire dont l'action est `nom_formulaire`."""
    morceau = page.split(f'action="{nom_formulaire}"', 1)[1]
    return morceau.split('name="jeton" value="', 1)[1].split('"', 1)[0]


# ===========================================================================
# 1. Le module
# ===========================================================================
def test_desactive_par_defaut_sans_ligne_en_base(bases):
    assert "tombola" in MODULES
    assert etat_defaut_module("tombola") == "desactive"
    assert not MODULES["tombola"].get("jamais_public")
    conn = _conn()
    try:
        assert lire_etat_module(conn, "tombola") == "desactive"
        assert conn.execute("SELECT COUNT(*) FROM parametres WHERE cle = 'module_tombola'"
                            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_url_du_module_ne_mene_pas_a_une_page_introuvable(bases):
    """Le champ `url` est un lien cliquable de /admin/fonctionnalites."""
    assert MODULES["tombola"]["url"] == "/tombola/gestion"


def test_module_desactive_page_indisponible(benevole):
    r = benevole.get("/tombola/gestion")
    assert r.status_code == 404
    assert "Numéro présenté" not in r.text
    r = benevole.post("/tombola/gestion/tirage", data={"numero_1": "12", "jeton": "abc"})
    assert r.status_code == 404
    assert _tirages() == []


def test_lien_de_menu_absent_puis_present(benevole):
    assert LIEN_MENU not in benevole.get("/catalogue").text
    _etat("benevoles")
    assert LIEN_MENU in benevole.get("/catalogue").text


@pytest.mark.parametrize("etat", ["tous", "benevoles", "discret", "desactive"])
def test_gestion_refusee_sans_jeton_dans_chacun_des_quatre_etats(visiteur, etat):
    _etat(etat)
    r = visiteur.get("/tombola/gestion")
    assert r.status_code in (403, 404)
    assert "Numéro présenté" not in r.text
    for adresse, donnees in (("/tombola/gestion/tirage", {"numero_1": "7"}),
                             ("/tombola/gestion/tirage-aleatoire", {"numero_max": "50"}),
                             ("/tombola/gestion/remettre", {"numero": "7"})):
        assert visiteur.post(adresse, data=donnees).status_code in (403, 404)
    assert _tirages() == [] and _parametre(tombola.CLE_NUMERO_MAX) is None


def test_tombola_reste_libre_pour_le_lot_2(benevole, actif):
    assert benevole.get("/tombola").status_code == 404


def test_page_de_gestion_ordre_et_titres(benevole, actif):
    page = benevole.get("/tombola/gestion").text
    assert page.count("<h1") == 1
    assert page.index("Numéro présenté") < page.index("Numéros en attente") \
        < page.index("Saisir un tirage") < page.index("Tirage par l'application")
    assert 'inputmode="numeric"' in page and "autofocus" in page
    assert "Afficher les numéros" in page and "Tirer 5 numéros" in page
    # Deux jetons distincts, un par formulaire de tirage.
    assert _jeton_de(page, "/tombola/gestion/tirage") != \
        _jeton_de(page, "/tombola/gestion/tirage-aleatoire")


def test_fonctionnalites_liste_la_tombola(admin):
    page = admin.get("/admin/fonctionnalites").text
    assert "Tombola" in page and 'name="module_tombola"' in page


def test_adresses_d_envoi_en_get_reviennent_a_la_page(benevole, actif):
    for suffixe in ("tirage", "tirage-aleatoire", "remettre", "annuler", "erreur-saisie"):
        r = benevole.get(f"/tombola/gestion/{suffixe}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/tombola/gestion"


# ===========================================================================
# 2. Saisie manuelle
# ===========================================================================
def test_un_a_cinq_numeros(bases):
    t = _saisir("12")
    assert t.issue == tombola.TIRAGE_ENREGISTRE and t.numeros == (12,)
    t = _saisir("1", "2", "3", "4", "5")
    assert t.numeros == (1, 2, 3, 4, 5)
    assert [l["mode"] for l in _tirages()] == ["manuel", "manuel"]
    assert all(l["numero_max"] is None for l in _tirages())


@pytest.mark.parametrize("saisie,attendu", [
    ("0045", 45), (" 45 ", 45), ("4 5", 45), ("1", 1), ("9999", 9999),
    ("0", None), ("10000", None), ("abc", None), ("-3", None), ("4,5", None),
    ("45a", None), ("", None), (None, None),
])
def test_lecture_d_un_numero(saisie, attendu):
    assert tombola.lire_numero(saisie) == attendu


def test_bornes_et_texte_illisible_ecartes_les_autres_affiches(bases):
    t = _saisir("0", "1", "9999", "10000", "abc")
    assert t.numeros == (1, 9999)
    assert t.ecartes == (("0", "illisible"), ("10000", "illisible"), ("abc", "illisible"))
    m = tombola.message_tirage(t)
    assert m.texte == "Numéros affichés : 1, 9999."
    assert "« 10000 » écarté : ce n'est pas un numéro entre 1 et 9999." in m.details


def test_doublon_dans_le_tirage_et_deja_sorti_dans_l_edition(bases):
    _saisir("45")
    t = _saisir("12", "0012", "45")
    assert t.numeros == (12,)
    assert t.ecartes == (("12", "doublon"), ("45", "deja_sorti"))
    m = tombola.message_tirage(t)
    assert "« 12 » écarté : tapé deux fois dans ce tirage." in m.details
    assert "« 45 » écarté : déjà sorti pendant cette tombola." in m.details


def test_aucun_valide_rien_d_ecrit(bases):
    t = _saisir("0", "abc")
    assert t.issue == tombola.TIRAGE_AUCUN_VALIDE
    assert _tirages() == [] and _numeros() == [] and _editions() == []


def test_aucun_valide_saisie_reaffichee_par_la_page(benevole, actif):
    page = benevole.get("/tombola/gestion").text
    r = benevole.post("/tombola/gestion/tirage", data={
        "numero_1": "10000", "numero_2": "x7", "jeton": _jeton_de(page, "/tombola/gestion/tirage")})
    assert r.status_code == 200
    assert "Aucun numéro affiché" in r.text
    assert 'name="numero_1"\n                 inputmode="numeric" autocomplete="off" maxlength="12" value="10000"' in r.text
    assert 'value="x7"' in r.text
    assert _tirages() == []


def test_formulaire_vide_sans_effet(bases):
    t = _saisir("", "  ", None)
    assert t.issue == tombola.TIRAGE_VIDE and _tirages() == []
    assert tombola.message_tirage(t).niveau == "attention"


def test_numero_en_erreur_de_saisie_peut_etre_ressaisi(bases):
    _saisir("54")
    assert _geste(tombola.retirer_erreur_saisie, _id(54)).issue == tombola.RETRAIT_FAIT
    t = _saisir("54", instant=T0 + timedelta(minutes=1))
    assert t.numeros == (54,)
    assert [l["motif"] for l in _numeros()] == ["erreur_saisie", None]


def test_tirage_manuel_par_la_page_vide_les_champs(benevole, actif):
    page = benevole.get("/tombola/gestion").text
    r = benevole.post("/tombola/gestion/tirage", data={
        "numero_1": "15", "numero_2": "0302", "jeton": _jeton_de(page, "/tombola/gestion/tirage")})
    assert "Numéros affichés : 15, 302." in r.text
    assert 'value="0302"' not in r.text
    assert "Tirage de " in r.text and "Lot remis<span class=\"sr-only\"> : numéro 302</span>" in r.text


# ===========================================================================
# 3. Tirage par l'application
# ===========================================================================
def test_aleatoire_parmi_un_a_max_moins_les_sortis(bases):
    _saisir("2", "4")
    _saisir("6")
    _geste(tombola.retirer_erreur_saisie, _id(6))   # en erreur : redevient possible
    g = Generateur()
    t = _tirer("10", generateur=g)
    assert g.population == [1, 3, 5, 6, 7, 8, 9, 10]
    assert t.numeros == (1, 3, 5, 6, 7)
    assert _tirages()[-1]["mode"] == "aleatoire" and _tirages()[-1]["numero_max"] == 10
    assert tombola.message_tirage(t).en_grand == (1, 3, 5, 6, 7)


def test_aleatoire_reste_de_moins_de_cinq(bases):
    _saisir("1", "2", "3")
    t = _tirer("5")
    assert t.numeros == (4, 5)
    assert tombola.message_tirage(t).texte == \
        "Il ne restait que 2 numéros possibles entre 1 et 5 : 4, 5."


def test_aleatoire_reste_nul_rien_d_ecrit(bases):
    _saisir("1", "2")
    avant = len(_tirages())
    t = _tirer("2")
    assert t.issue == tombola.TIRAGE_EPUISE
    assert len(_tirages()) == avant
    assert "rien n'a été tiré" in tombola.message_tirage(t).texte


def test_aleatoire_reste_nul_sans_edition_n_en_ouvre_pas(bases):
    # Impossible sans édition (au moins 1 numéro possible) : borne basse.
    t = _tirer("1")
    assert t.numeros == (1,)
    assert _tirer("1").issue == tombola.TIRAGE_EPUISE
    assert len(_editions()) == 1


@pytest.mark.parametrize("saisie", ["0", "10000", "abc", "", "  "])
def test_aleatoire_max_invalide_rien_d_ecrit(bases, saisie):
    t = _tirer(saisie)
    assert t.issue == tombola.TIRAGE_MAX_INVALIDE
    assert _tirages() == [] and _parametre(tombola.CLE_NUMERO_MAX) is None
    assert "tapez le plus grand numéro vendu, entre 1 et 9999" in tombola.message_tirage(t).texte


def test_aleatoire_max_invalide_saisie_reprise_par_la_page(benevole, actif):
    page = benevole.get("/tombola/gestion").text
    r = benevole.post("/tombola/gestion/tirage-aleatoire", data={
        "numero_max": "12x", "jeton": _jeton_de(page, "/tombola/gestion/tirage-aleatoire")})
    assert "« 12x » : tapez le plus grand numéro vendu" in r.text
    assert 'value="12x"' in r.text


def test_aleatoire_max_retenu_et_prerempli(benevole, actif):
    _tirer("0350")
    assert _parametre(tombola.CLE_NUMERO_MAX) == "350"
    page = benevole.get("/tombola/gestion").text
    assert 'name="numero_max" inputmode="numeric"\n             autocomplete="off" maxlength="12" value="350"' in page


def test_aleatoire_utilise_secrets_jamais_random():
    source = Path("app/tombola.py").read_text(encoding="utf-8")
    arbre = ast.parse(source)
    importes = {a.name for n in ast.walk(arbre) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names} | {n.module for n in ast.walk(arbre)
                                     if isinstance(n, ast.ImportFrom)}
    assert "random" not in importes
    assert "secrets.SystemRandom()" in source


def test_aleatoire_sans_generateur_injecte(bases):
    conn = _conn()
    try:
        t = tombola.tirer_au_hasard(conn, "40", tombola.nouveau_jeton())
    finally:
        conn.close()
    assert len(t.numeros) == 5 and all(1 <= n <= 40 for n in t.numeros)


# ===========================================================================
# 4. Double appui : le jeton de formulaire
# ===========================================================================
def _simultanes(nb_fils, action):
    from app import db

    depart = threading.Barrier(nb_fils)
    resultats = [None] * nb_fils

    def lancer(i):
        conn = db.get_connection()
        try:
            depart.wait()
            resultats[i] = action(conn, i)
        except Exception as erreur:   # remontée au test, jamais avalée
            resultats[i] = erreur
        finally:
            conn.close()

    fils = [threading.Thread(target=lancer, args=(i,)) for i in range(nb_fils)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=30)
    assert not [r for r in resultats if isinstance(r, Exception)], resultats
    return resultats


def test_double_appui_simultane_manuel_un_seul_tirage(bases):
    jeton = tombola.nouveau_jeton()
    resultats = _simultanes(6, lambda conn, i: tombola.saisir_tirage(
        conn, ["11", "22"], jeton, T0))
    assert len(_tirages()) == 1 and len(_numeros()) == 2
    issues = sorted(r.issue for r in resultats)
    assert issues.count(tombola.TIRAGE_ENREGISTRE) == 1
    assert all(r.numeros == (11, 22) for r in resultats)


def test_double_appui_simultane_aleatoire_cinq_numeros_pas_dix(bases):
    jeton = tombola.nouveau_jeton()
    _simultanes(6, lambda conn, i: tombola.tirer_au_hasard(conn, "100", jeton, T0))
    assert len(_tirages()) == 1 and len(_numeros()) == 5


def test_jeton_rejoue_par_la_page_reaffiche_le_premier_resultat(benevole, actif, _journal_isole):
    page = benevole.get("/tombola/gestion").text
    jeton = _jeton_de(page, "/tombola/gestion/tirage-aleatoire")
    r1 = benevole.post("/tombola/gestion/tirage-aleatoire", data={"numero_max": "60", "jeton": jeton})
    r2 = benevole.post("/tombola/gestion/tirage-aleatoire", data={"numero_max": "60", "jeton": jeton})
    numeros = ", ".join(str(l["numero"]) for l in sorted(_numeros(), key=lambda l: l["numero"]))
    assert f"Numéros tirés : {numeros}." in r1.text
    assert f"Numéros affichés : {numeros}. Le formulaire est parti deux fois" in r2.text
    assert len(_tirages()) == 1
    assert len(_journal(_journal_isole)) == 1


def test_jeton_absent_ou_forge_ne_bloque_pas(bases):
    conn = _conn()
    try:
        a = tombola.saisir_tirage(conn, ["1"], "", T0)
        b = tombola.saisir_tirage(conn, ["2"], "x" * 500, T0)
        c = tombola.saisir_tirage(conn, ["3"], "<script>", T0)
    finally:
        conn.close()
    assert {a.issue, b.issue, c.issue} == {tombola.TIRAGE_ENREGISTRE}
    assert len(_tirages()) == 3


# ===========================================================================
# 5. Index unique partiel, édition unique ouverte
# ===========================================================================
def test_meme_numero_par_deux_tirages_simultanes_un_seul_passe(bases):
    resultats = _simultanes(6, lambda conn, i: tombola.saisir_tirage(
        conn, ["77"], tombola.nouveau_jeton(), T0))
    assert [l["numero"] for l in _numeros()] == [77]
    assert sorted(r.issue for r in resultats).count(tombola.TIRAGE_ENREGISTRE) == 1
    assert len(_tirages()) == 1


def test_meme_numero_par_la_page_deux_fois_sans_erreur_500(benevole, actif):
    for _ in range(2):
        page = benevole.get("/tombola/gestion").text
        r = benevole.post("/tombola/gestion/tirage", data={
            "numero_1": "77", "jeton": _jeton_de(page, "/tombola/gestion/tirage")})
        assert r.status_code == 200
    assert "déjà sorti pendant cette tombola" in r.text
    assert len(_numeros()) == 1


def test_l_index_refuse_un_doublon_sauf_en_erreur_de_saisie(bases):
    _saisir("31")
    ligne = _numeros()[0]
    conn = _conn()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tombola_numeros (tirage_id, edition_id, numero, affiche_le) "
                         "VALUES (?, ?, 31, ?)", (ligne["tirage_id"], ligne["edition_id"], "x"))
        conn.rollback()
        conn.execute("UPDATE tombola_numeros SET motif = 'erreur_saisie'")
        conn.execute("INSERT INTO tombola_numeros (tirage_id, edition_id, numero, affiche_le) "
                     "VALUES (?, ?, 31, ?)", (ligne["tirage_id"], ligne["edition_id"], "x"))
        conn.commit()
    finally:
        conn.close()


def test_l_index_refuse_deux_editions_ouvertes(bases):
    conn = _conn()
    try:
        conn.execute("INSERT INTO tombola_editions (ouverte_le) VALUES ('a')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tombola_editions (ouverte_le) VALUES ('b')")
    finally:
        conn.close()


def test_index_tombola_avec_les_autres_filets(bases):
    noms = " ".join(models.SCHEMA_INDEXES_UNIQUES)
    assert "idx_tombola_numero_unique" in noms and "idx_tombola_une_edition_ouverte" in noms
    presents = {l["name"] for l in _sql("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_tombola_numero_unique", "idx_tombola_une_edition_ouverte"} <= presents


def test_deux_premiers_tirages_simultanes_une_seule_edition(bases):
    _simultanes(6, lambda conn, i: tombola.saisir_tirage(
        conn, [str(100 + i)], tombola.nouveau_jeton(), T0))
    assert len(_editions()) == 1 and len(_tirages()) == 6
    assert {l["edition_id"] for l in _numeros()} == {_editions()[0]["id"]}


# ===========================================================================
# 6. Remise d'un lot, et annulation
# ===========================================================================
def test_remise_numero_en_attente(bases):
    _saisir("45")
    g = _geste(tombola.remettre_par_numero, " 0045 ", instant=T0 + timedelta(minutes=10))
    assert g.issue == tombola.REMISE_FAITE and g.ecrit
    ligne = _numeros()[0]
    assert ligne["motif"] == "remis"
    assert ligne["sorti_le"] == (T0 + timedelta(minutes=10)).isoformat(timespec="seconds")
    m = tombola.message_remise(g)
    assert m.texte == "Lot remis : numéro 45." and m.annuler_id == ligne["id"]


def test_remise_deja_remis_avec_annuler_dans_la_minute(bases):
    _saisir("45")
    _geste(tombola.remettre_par_numero, "45", instant=T0)
    g = _geste(tombola.remettre_par_numero, "45", instant=T0 + timedelta(seconds=20))
    m = tombola.message_remise(g)
    assert g.issue == tombola.REMISE_DEJA and not g.ecrit
    assert m.texte == f"Le lot du numéro 45 a déjà été remis à {tombola.heure_locale(T0.isoformat())}."
    assert m.annuler_id is not None
    tard = _geste(tombola.remettre_par_numero, "45",
                  instant=T0 + timedelta(seconds=tombola.DELAI_ANNULATION_REMISE_S))
    assert tombola.message_remise(tard).annuler_id is None


def test_double_remise_ne_reecrit_pas_sorti_le(bases):
    _saisir("45")
    _geste(tombola.remettre_par_id, _id(45), instant=T0)
    avant = _numeros()[0]["sorti_le"]
    g = _geste(tombola.remettre_par_id, _id(45), instant=T0 + timedelta(seconds=5))
    assert g.issue == tombola.REMISE_DEJA and not g.ecrit
    assert _numeros()[0]["sorti_le"] == avant


def test_remise_numero_jamais_tire(bases):
    g = _geste(tombola.remettre_par_numero, "45")
    assert tombola.message_remise(g).texte == "Le numéro 45 n'a pas été tiré."
    _saisir("12")
    g = _geste(tombola.remettre_par_numero, "45")
    assert tombola.message_remise(g).texte == "Le numéro 45 n'a pas été tiré."


def test_remise_numero_retire_pour_erreur_de_saisie(bases):
    _saisir("45")
    _geste(tombola.retirer_erreur_saisie, _id(45))
    g = _geste(tombola.remettre_par_numero, "45")
    assert tombola.message_remise(g).texte == "Le numéro 45 a été retiré : erreur de saisie."
    assert _numeros()[0]["motif"] == "erreur_saisie"


@pytest.mark.parametrize("saisie", ["", "0", "10000", "abc"])
def test_remise_saisie_illisible(bases, saisie):
    g = _geste(tombola.remettre_par_numero, saisie)
    assert tombola.message_remise(g).texte == "Tapez un numéro entre 1 et 9999."


def test_remise_prefere_la_ligne_ressaisie_a_l_erreur(bases):
    _saisir("45")
    _geste(tombola.retirer_erreur_saisie, _id(45))
    _saisir("45")
    g = _geste(tombola.remettre_par_numero, "45")
    assert g.issue == tombola.REMISE_FAITE
    assert [l["motif"] for l in _numeros()] == ["erreur_saisie", "remis"]


def test_remise_par_la_page_champ_et_bouton(benevole, actif):
    _saisir("45", "46")
    r = benevole.post("/tombola/gestion/remettre", data={"numero": "45"})
    assert 'role="status"' in r.text and "Lot remis : numéro 45." in r.text
    assert "Annuler<span class=\"sr-only\"> la remise</span>" in r.text
    assert "autofocus" in r.text
    r = benevole.post("/tombola/gestion/remettre", data={"numero_id": str(_id(46))})
    assert "Lot remis : numéro 46." in r.text
    assert "Aucun numéro en attente." in r.text


def test_remise_illisible_par_la_page_saisie_conservee(benevole, actif):
    r = benevole.post("/tombola/gestion/remettre", data={"numero": "4a5"})
    assert "Tapez un numéro entre 1 et 9999." in r.text
    assert 'value="4a5"' in r.text


def test_annulation_dans_la_minute(bases):
    _saisir("45")
    _geste(tombola.remettre_par_id, _id(45), instant=T0)
    g = _geste(tombola.annuler_remise, _id(45),
               instant=T0 + timedelta(seconds=tombola.DELAI_ANNULATION_REMISE_S - 1))
    assert g.issue == tombola.ANNULATION_FAITE and g.ecrit
    assert _numeros()[0]["motif"] is None and _numeros()[0]["sorti_le"] is None
    assert tombola.message_annulation(g).texte == \
        "Remise annulée : le numéro 45 est de nouveau affiché."
    # Une seconde annulation ne change rien.
    g = _geste(tombola.annuler_remise, _id(45), instant=T0 + timedelta(seconds=2))
    assert g.issue == tombola.ANNULATION_DEJA_AFFICHE and not g.ecrit


def test_annulation_apres_une_minute_refusee(bases):
    _saisir("45")
    _geste(tombola.remettre_par_id, _id(45), instant=T0)
    g = _geste(tombola.annuler_remise, _id(45),
               instant=T0 + timedelta(seconds=tombola.DELAI_ANNULATION_REMISE_S))
    assert g.issue == tombola.ANNULATION_TROP_TARD and not g.ecrit
    assert _numeros()[0]["motif"] == "remis"
    assert "ne peut plus être annulée" in tombola.message_annulation(g).texte


def test_constante_d_annulation_distincte_du_seuil_de_pret():
    assert tombola.DELAI_ANNULATION_REMISE_S == 60
    source = Path("app/tombola.py").read_text(encoding="utf-8")
    assert "SEUIL_ERREUR_PRET_S" not in source.split("DELAI_ANNULATION_REMISE_S = ")[1].split("\n")[0]


@pytest.mark.parametrize("valeur", ["", "abc", "999999", "-1"])
def test_annulation_forgee_message_clair_rien_ecrit(benevole, actif, valeur, _journal_isole):
    _saisir("45")
    r = benevole.post("/tombola/gestion/annuler", data={"numero_id": valeur})
    assert r.status_code == 200
    assert "Cette remise ne peut pas être annulée." in r.text
    assert _numeros()[0]["motif"] is None
    assert _journal(_journal_isole) == []


def test_annulation_forgee_sur_un_numero_jamais_remis(bases):
    _saisir("45")
    g = _geste(tombola.annuler_remise, _id(45))
    assert g.issue == tombola.ANNULATION_DEJA_AFFICHE and not g.ecrit


def test_annulation_vise_la_ligne_pas_le_numero(bases):
    """Le numéro 45 ressaisi après une erreur : l'annulation ne touche que sa ligne."""
    _saisir("45")
    ancienne = _id(45)
    _geste(tombola.retirer_erreur_saisie, ancienne)
    _saisir("45")
    nouvelle = _id(45)
    _geste(tombola.remettre_par_id, nouvelle, instant=T0)
    assert _geste(tombola.annuler_remise, ancienne).issue == tombola.ANNULATION_IMPOSSIBLE
    assert [l["motif"] for l in _numeros()] == ["erreur_saisie", "remis"]


# ===========================================================================
# 7. Erreur de saisie
# ===========================================================================
def test_erreur_de_saisie_dans_les_cinq_minutes(bases):
    _saisir("45")
    g = _geste(tombola.retirer_erreur_saisie, _id(45),
               instant=T0 + timedelta(seconds=tombola.DELAI_ERREUR_SAISIE_S - 1))
    assert g.issue == tombola.RETRAIT_FAIT and g.ecrit
    assert _numeros()[0]["motif"] == "erreur_saisie"
    assert tombola.message_retrait(g).texte == \
        "Numéro 45 retiré : erreur de saisie. Il peut être saisi à nouveau."


def test_erreur_de_saisie_au_dela_refusee(bases):
    _saisir("45")
    g = _geste(tombola.retirer_erreur_saisie, _id(45),
               instant=T0 + timedelta(seconds=tombola.DELAI_ERREUR_SAISIE_S))
    assert g.issue == tombola.RETRAIT_TROP_TARD and not g.ecrit
    assert _numeros()[0]["motif"] is None
    assert f"plus de {tombola.DELAI_ERREUR_SAISIE_S // 60} minutes" in tombola.message_retrait(g).texte


def test_bouton_erreur_de_saisie_seulement_pendant_le_delai(bases):
    _saisir("45")
    conn = _conn()
    try:
        tot = tombola.numeros_en_attente(conn, T0 + timedelta(seconds=10))
        tard = tombola.numeros_en_attente(
            conn, T0 + timedelta(seconds=tombola.DELAI_ERREUR_SAISIE_S))
    finally:
        conn.close()
    assert tot[0]["numeros"][0]["erreur_possible"] is True
    assert tard[0]["numeros"][0]["erreur_possible"] is False


def test_erreur_de_saisie_sur_un_lot_remis_refusee(bases):
    _saisir("45")
    _geste(tombola.remettre_par_id, _id(45))
    g = _geste(tombola.retirer_erreur_saisie, _id(45))
    assert g.issue == tombola.RETRAIT_PLUS_AFFICHE and _numeros()[0]["motif"] == "remis"


def test_erreur_de_saisie_par_la_page(benevole, actif):
    page = benevole.get("/tombola/gestion").text
    benevole.post("/tombola/gestion/tirage", data={
        "numero_1": "45", "jeton": _jeton_de(page, "/tombola/gestion/tirage")})
    page = benevole.get("/tombola/gestion").text
    assert 'Erreur de saisie<span class="sr-only"> : numéro 45</span>' in page
    r = benevole.post("/tombola/gestion/erreur-saisie", data={"numero_id": str(_id(45))})
    assert "Numéro 45 retiré : erreur de saisie." in r.text
    assert "Aucun numéro en attente." in r.text


def test_numeros_en_attente_groupes_plus_recent_d_abord(bases):
    _saisir("30", "10", instant=T0)
    _saisir("20", instant=T0 + timedelta(minutes=60))
    conn = _conn()
    try:
        groupes = tombola.numeros_en_attente(conn, T0)
    finally:
        conn.close()
    assert [[n["numero"] for n in g["numeros"]] for g in groupes] == [[20], [10, 30]]
    assert groupes[1]["heure"] == tombola.heure_locale(T0.isoformat())


def test_noms_accessibles_distincts_pour_chaque_lot_remis(benevole, actif):
    _saisir("1", "2", "3", instant=datetime.now(timezone.utc))
    page = benevole.get("/tombola/gestion").text
    for n in (1, 2, 3):
        assert f'Lot remis<span class="sr-only"> : numéro {n}</span>' in page


# ===========================================================================
# 8. Édition et clôture
# ===========================================================================
def test_premier_tirage_ouvre_l_edition(bases):
    assert _editions() == []
    _saisir("5")
    editions = _editions()
    assert len(editions) == 1 and editions[0]["close_le"] is None
    _saisir("6")
    assert len(_editions()) == 1


def test_cloture_passe_les_numeros_affiches_en_non_reclame(bases):
    _saisir("5", "6", "7")
    _geste(tombola.remettre_par_numero, "5")
    conn = _conn()
    try:
        cloture = services.cloturer_evenement(conn)
    finally:
        conn.close()
    assert cloture.non_reclames == 2
    assert [l["motif"] for l in _numeros()] == ["remis", "non_reclame", "non_reclame"]
    assert all(l["sorti_le"] for l in _numeros())
    assert _editions()[0]["close_le"] is not None


def test_cloture_par_l_admin_dit_le_nombre_de_non_reclames(admin):
    _saisir("5", "6")
    r = admin.post("/admin/cloturer-prets")
    assert "La tombola est terminée : 2 numéros non réclamés." in r.text


def test_cloture_sans_tombola_message_inchange(admin):
    r = admin.post("/admin/cloturer-prets")
    assert "0 prêt(s)/sortie(s) clôturé(s). Tout est de nouveau disponible, et les " \
           "annonces de l&#39;écran de salle sont effacées." in r.text
    assert "tombola" not in r.text.lower().split("clôturé(s)")[1].split("</")[0]
    assert _editions() == []


def test_cloture_termine_la_tombola_meme_module_desactive(admin):
    _saisir("5")
    _etat("desactive")
    admin.post("/admin/cloturer-prets")
    assert _numeros()[0]["motif"] == "non_reclame"
    assert _editions()[0]["close_le"] is not None


def test_confirmation_de_cloture_nomme_la_tombola_seulement_si_active(admin):
    assert "La tombola sera terminée" not in admin.get("/admin").text
    _etat("benevoles")
    assert "La tombola sera terminée" in admin.get("/admin").text


def test_nouvelle_edition_apres_cloture_un_ancien_numero_peut_ressortir(bases):
    _saisir("5")
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
        assert tombola.edition_ouverte(conn) is None
    finally:
        conn.close()
    t = _saisir("5")
    assert t.numeros == (5,)
    assert len(_editions()) == 2
    assert _geste(tombola.remettre_par_numero, "5").issue == tombola.REMISE_FAITE
    assert [l["edition_id"] for l in _numeros()] == [e["id"] for e in _editions()]


def test_cloture_repetee_sans_effet(bases):
    _saisir("5")
    conn = _conn()
    try:
        assert services.cloturer_evenement(conn).non_reclames == 1
        assert services.cloturer_evenement(conn).non_reclames == 0
        assert services.cloturer_tous_les_prets(conn) == 0
    finally:
        conn.close()


def test_lectures_du_lot_2_possibles_avec_le_schema(bases):
    """Délai de remise des seuls `remis`, statistiques par tirage, mode, édition."""
    _saisir("1", "2", instant=T0)
    _tirer("50", instant=T0)
    _geste(tombola.remettre_par_numero, "1", instant=T0 + timedelta(minutes=12))
    delais = _sql(
        "SELECT (julianday(sorti_le) - julianday(affiche_le)) * 1440 AS minutes "
        "FROM tombola_numeros WHERE motif = 'remis'")
    assert [round(d["minutes"]) for d in delais] == [12]
    par_mode = _sql(
        "SELECT t.mode, COUNT(*) AS nb FROM tombola_numeros n "
        "JOIN tombola_tirages t ON t.id = n.tirage_id GROUP BY t.mode ORDER BY t.mode")
    assert [(l["mode"], l["nb"]) for l in par_mode] == [("aleatoire", 5), ("manuel", 2)]
    affiches = _sql(
        "SELECT COUNT(*) AS nb FROM tombola_numeros n JOIN tombola_editions e "
        "ON e.id = n.edition_id WHERE e.close_le IS NULL AND n.motif IS NULL")
    assert affiches[0]["nb"] == 6


# ===========================================================================
# 9. Formation, migration, journal
# ===========================================================================
def test_reinitialisation_de_formation_vide_les_trois_tables_et_le_reglage(bases):
    from app import formation

    _saisir("5")
    _tirer("80")
    conn = _conn()
    try:
        formation._vider_base_pret(conn)
    finally:
        conn.close()
    assert _numeros() == [] and _tirages() == [] and _editions() == []
    assert _parametre(tombola.CLE_NUMERO_MAX) is None


def test_base_d_avant_le_module(bases):
    """Une base sans les tables : `init_db` les crée, rien d'autre ne change."""
    from app import db

    conn = _conn()
    try:
        for index in ("idx_tombola_numero_unique", "idx_tombola_une_edition_ouverte",
                      "idx_tombola_numeros_affiches"):
            conn.execute(f"DROP INDEX {index}")
        for table in ("tombola_numeros", "tombola_tirages", "tombola_editions"):
            conn.execute(f"DROP TABLE {table}")
        conn.commit()
        avant = conn.execute("SELECT cle, valeur FROM parametres ORDER BY cle").fetchall()
    finally:
        conn.close()
    db.init_db()
    db.init_db()
    conn = _conn()
    try:
        tables = {l[0] for l in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tombola_editions", "tombola_tirages", "tombola_numeros"} <= tables
        assert conn.execute("SELECT cle, valeur FROM parametres ORDER BY cle").fetchall() == avant
        assert lire_etat_module(conn, "tombola") == "desactive"
    finally:
        conn.close()


def test_journal_une_ligne_par_geste(benevole, actif, _journal_isole):
    assert "tombola" in journal.MODULES
    for action in ("tombola_tirage", "tombola_lot_remis", "tombola_remise_annulee",
                   "tombola_erreur_saisie"):
        assert action in journal.ACTIONS
    page = benevole.get("/tombola/gestion").text
    benevole.post("/tombola/gestion/tirage", data={
        "numero_1": "8", "numero_2": "3", "jeton": _jeton_de(page, "/tombola/gestion/tirage")})
    benevole.post("/tombola/gestion/remettre", data={"numero": "8"})
    benevole.post("/tombola/gestion/remettre", data={"numero": "8"})        # sans effet
    benevole.post("/tombola/gestion/annuler", data={"numero_id": str(_id(8))})
    benevole.post("/tombola/gestion/annuler", data={"numero_id": str(_id(8))})  # sans effet
    benevole.post("/tombola/gestion/erreur-saisie", data={"numero_id": str(_id(3))})
    benevole.post("/tombola/gestion/remettre", data={"numero": "999"})      # jamais tiré
    lignes = _journal(_journal_isole)
    assert [(l["action"], l.get("objet"), l.get("ref")) for l in lignes] == [
        ("tombola_tirage", "manuel", "3 8"),
        ("tombola_lot_remis", "8", None),
        ("tombola_remise_annulee", "8", None),
        ("tombola_erreur_saisie", "3", None),
    ]
    assert all(l["module"] == "tombola" and l["qui"] == "benevole" for l in lignes)


def test_actions_au_vocabulaire_et_a_la_conception():
    conception = Path("docs/conception-journal.md").read_text(encoding="utf-8")
    for action in ("tombola_tirage", "tombola_lot_remis", "tombola_remise_annulee",
                   "tombola_erreur_saisie"):
        assert action in journal.ACTIONS and action in conception


def test_aucun_journal_depuis_le_service():
    arbre = ast.parse(Path("app/tombola.py").read_text(encoding="utf-8"))
    noms = {n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(arbre) if isinstance(n, (ast.Name, ast.Attribute))}
    assert "journaliser" not in noms and "journal" not in noms


def test_aucun_texte_libre_dans_le_schema():
    """Des numéros, des heures, des codes fermés : aucune colonne de saisie libre."""
    schema = (models.SCHEMA_TOMBOLA_EDITIONS + models.SCHEMA_TOMBOLA_TIRAGES
              + models.SCHEMA_TOMBOLA_NUMEROS)
    colonnes_texte = [l.split()[0] for l in schema.splitlines()
                      if " TEXT" in l and not l.strip().startswith("--")]
    assert sorted(colonnes_texte) == sorted(
        ["ouverte_le", "close_le", "tire_le", "mode", "jeton_formulaire",
         "affiche_le", "sorti_le", "motif"])
