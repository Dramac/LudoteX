"""
Tombola — saisir un tirage depuis la page « Annonce en salle » (série
tombola, lot 3).

Ce que ce fichier verrouille :

1. la VISIBILITÉ du bloc : absent quand le module tombola est désactivé,
   interrupteur des annonces ouvert comme fermé (la page reste celle d'avant
   le lot) ; présent sinon, interrupteur ouvert comme fermé (les deux droits
   sont indépendants) ;
2. les DEUX GARDES de `POST /live/annonce/tombola` : le jeton bénévole, et
   l'état du module tombola, vérifié à la main sous le routeur `live` ;
3. la SAISIE : tirage écrit, une ligne de journal, message dans le bloc ;
   rien d'écrit sans numéro valide, saisie réaffichée ; même jeton envoyé
   deux fois ou simultanément, un seul tirage ;
4. le DOMICILE UNIQUE « saisir + journaliser », appelé par les deux pages ;
5. la LECTURE EN SALLE, sans bouton ni identifiant de ligne ;
6. le PÉRIMÈTRE : ni remise, ni correction, ni tirage par l'application ;
7. la PAGE : identifiants HTML uniques, un seul bouton principal.

Le rendu de /tombola/gestion, inchangé par l'extraction du fragment, reste
couvert par tests/test_tombola.py et tests/test_tombola_affichage.py.

Aucun exemple ne nomme personne : des numéros, rien d'autre.
"""

import json
import re
import threading

import pytest
from markupsafe import escape

from app import services, tombola
from app.routes import live
from app.routes import tombola as routes_tombola

JETON = "jeton-test-secret-32-caracteres"
MOT_DE_PASSE = "secret-admin-123"
URL_PAGE = live.URL_ANNONCE_BENEVOLE
URL_TIRAGE = live.URL_TOMBOLA_BENEVOLE
ACTION_BLOC = f'action="{URL_TIRAGE}#{live.ANCRE_TOMBOLA}"'
TITRE_BLOC = f'<h2 id="{live.ANCRE_TOMBOLA}">Tombola</h2>'


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


def _nouveau_benevole():
    c = _client()
    c.get(f"/acces?jeton={JETON}", follow_redirects=False)
    return c


@pytest.fixture
def benevole(bases):
    return _nouveau_benevole()


def _conn():
    from app import db
    return db.get_connection()


def _etat(etat):
    conn = _conn()
    try:
        services.ecrire_parametre(conn, "module_tombola", etat)
    finally:
        conn.close()


def _interrupteur(ouvert):
    conn = _conn()
    try:
        services.ecrire_annonce_benevoles(conn, ouvert)
    finally:
        conn.close()


@pytest.fixture
def actif(bases):
    """Module tombola réglé sur « bénévoles » : le bloc est servi."""
    _etat("benevoles")
    return bases


def _sql(requete, params=()):
    conn = _conn()
    try:
        return [dict(l) for l in conn.execute(requete, params)]
    finally:
        conn.close()


def _tirages():
    return _sql("SELECT * FROM tombola_tirages ORDER BY id")


def _numeros():
    return [l["numero"] for l in _sql("SELECT numero FROM tombola_numeros ORDER BY numero")]


def _journal(chemin):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l]
    return [l for l in lignes if l["action"] == "tombola_tirage"]


def _jeton(page):
    """Le jeton caché du formulaire du bloc « Tombola »."""
    morceau = page.split(ACTION_BLOC, 1)[1]
    return morceau.split('name="jeton" value="', 1)[1].split('"', 1)[0]


def _bloc(page):
    """Le HTML du bloc « Tombola », du titre à la fin de la carte."""
    return page.split(TITRE_BLOC, 1)[1].split("</section>", 1)[0]


def _envoyer(client, *numeros, jeton=None, **kwargs):
    if jeton is None:
        jeton = _jeton(client.get(URL_PAGE).text)
    donnees = {f"numero_{i}": n for i, n in enumerate(numeros, start=1)}
    donnees["jeton"] = jeton
    return client.post(URL_TIRAGE, data=donnees, **kwargs)


def _saisir(*numeros):
    conn = _conn()
    try:
        return tombola.saisir_tirage(conn, list(numeros), tombola.nouveau_jeton())
    finally:
        conn.close()


# ===========================================================================
# 1. Visibilité du bloc
# ===========================================================================
@pytest.mark.parametrize("ouvert", [True, False])
def test_bloc_absent_module_desactive(benevole, ouvert):
    """Désactivé par défaut, puis réglé explicitement : jamais de bloc."""
    _interrupteur(ouvert)
    for _ in range(2):
        page = benevole.get(URL_PAGE).text
        assert TITRE_BLOC not in page
        assert URL_TIRAGE not in page
        assert "Gérer la tombola" not in page
        _etat("desactive")


def test_module_desactive_interrupteur_ferme_page_d_avant(benevole):
    """La branche « fermée » garde son texte et son lien de retour."""
    page = benevole.get(URL_PAGE).text
    assert "rien ne peut être\n      affiché depuis cette page" in page
    assert '<a class="lien" href="/scanner">← Retour au scanner</a>' in page


@pytest.mark.parametrize("etat", ["tous", "benevoles", "discret"])
@pytest.mark.parametrize("ouvert", [True, False])
def test_bloc_present_module_visible(benevole, etat, ouvert):
    _etat(etat)
    _interrupteur(ouvert)
    page = benevole.get(URL_PAGE).text
    assert TITRE_BLOC in page
    assert ACTION_BLOC in page
    assert f'href="{routes_tombola.URL_GESTION}">Gérer la tombola</a>' in page


def test_interrupteur_ferme_le_bloc_vient_sous_l_explication(benevole, actif):
    page = benevole.get(URL_PAGE).text
    explication = page.index("Le bureau n'a pas ouvert l'annonce aux bénévoles")
    assert explication < page.index(TITRE_BLOC)
    # L'explication ne prétend plus que rien ne peut être affiché.
    assert "vous ne pouvez pas\n      poser d'annonce" in page
    assert "rien ne peut être" not in page
    # Le lien de retour reste unique, sous le bloc.
    assert page.count("← Retour au scanner") == 1
    assert page.index(TITRE_BLOC) < page.index("← Retour au scanner")


def test_interrupteur_ouvert_le_bloc_vient_apres_les_annonces(benevole, actif):
    _interrupteur(True)
    page = benevole.get(URL_PAGE).text
    assert page.index("Ajouter une annonce") < page.index("Affichées en salle")
    assert page.index("Affichées en salle") < page.index(TITRE_BLOC)
    assert page.count("Voir l'écran de la salle") == 1
    assert page.index(TITRE_BLOC) < page.index("Voir l'écran de la salle")


def test_bloc_hors_de_la_zone_de_resultat_des_annonces(benevole, actif):
    """Le message du tirage a sa propre zone `role="status"`, dans le bloc."""
    _interrupteur(True)
    page = _envoyer(benevole, "12").text
    haut, bloc = page.split(TITRE_BLOC, 1)
    assert "Numéros affichés : 12." not in haut
    assert bloc.index('<div role="status">') < bloc.index("Numéros affichés : 12.")


# ===========================================================================
# 2. Les deux gardes
# ===========================================================================
def test_sans_jeton_refuse_rien_d_ecrit(visiteur, actif, _journal_isole):
    r = visiteur.post(URL_TIRAGE, data={"numero_1": "12", "jeton": tombola.nouveau_jeton()},
                      follow_redirects=False)
    assert r.status_code in (303, 401, 403)
    assert _tirages() == [] and _journal(_journal_isole) == []


def test_sans_jeton_get_refuse(visiteur, actif):
    r = visiteur.get(URL_TIRAGE, follow_redirects=False)
    assert r.status_code in (303, 401, 403)
    assert r.headers.get("location") != URL_PAGE


@pytest.mark.parametrize("ouvert", [True, False])
def test_module_desactive_rien_d_ecrit_ni_journalise(benevole, _journal_isole, ouvert):
    _interrupteur(ouvert)
    r = benevole.post(URL_TIRAGE, data={"numero_1": "12", "jeton": tombola.nouveau_jeton()},
                      follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == URL_PAGE
    assert _tirages() == [] and _journal(_journal_isole) == []
    assert TITRE_BLOC not in benevole.get(URL_PAGE).text


def test_adresse_du_tirage_en_get_revient_a_la_page(benevole, actif):
    r = benevole.get(URL_TIRAGE, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == URL_PAGE


# ===========================================================================
# 3. La saisie
# ===========================================================================
@pytest.mark.parametrize("ouvert", [True, False])
def test_tirage_valide_ecrit_journalise_et_affiche(benevole, actif, _journal_isole, ouvert):
    """Interrupteur fermé compris : le bloc ne dépend pas des annonces."""
    _interrupteur(ouvert)
    r = _envoyer(benevole, "12", "4321")
    assert r.status_code == 200
    assert _numeros() == [12, 4321]
    lignes = _journal(_journal_isole)
    assert len(lignes) == 1
    assert lignes[0]["module"] == "tombola"
    assert lignes[0]["objet"] == tombola.MODE_MANUEL and lignes[0]["ref"] == "12 4321"
    bloc = _bloc(r.text)
    assert "Numéros affichés : 12, 4321." in bloc
    assert '<span>12</span><span>4321</span>' in bloc
    # Les champs sont vidés pour le tirage suivant.
    assert 'value="12"' not in bloc and 'value="4321"' not in bloc


def test_numeros_ecartes_nommes_dans_le_bloc(benevole, actif):
    _saisir("7")
    bloc = _bloc(_envoyer(benevole, "12", "abc", "12", "7").text)
    assert "Numéros affichés : 12." in bloc
    for raison in tombola.RAISONS_ECART.values():
        assert str(escape(raison)) in bloc
    assert _numeros() == [7, 12]


def test_aucun_valide_saisie_reaffichee_rien_d_ecrit(benevole, actif, _journal_isole):
    bloc = _bloc(_envoyer(benevole, "abc", "0").text)
    assert "Aucun numéro affiché" in bloc
    assert 'value="abc"' in bloc and 'value="0"' in bloc
    assert _tirages() == [] and _journal(_journal_isole) == []


def test_formulaire_vide_sans_effet(benevole, actif, _journal_isole):
    bloc = _bloc(_envoyer(benevole).text)
    assert "Tapez au moins un numéro" in bloc
    assert _tirages() == [] and _journal(_journal_isole) == []


def test_meme_jeton_deux_fois_un_seul_tirage(benevole, actif, _journal_isole):
    jeton = _jeton(benevole.get(URL_PAGE).text)
    _envoyer(benevole, "12", "34", jeton=jeton)
    second = _bloc(_envoyer(benevole, "12", "34", jeton=jeton).text)
    assert len(_tirages()) == 1 and _numeros() == [12, 34]
    assert len(_journal(_journal_isole)) == 1
    assert "Le formulaire est parti deux fois" in second


def test_jeton_neuf_a_chaque_rendu(benevole, actif):
    premier = _jeton(benevole.get(URL_PAGE).text)
    apres = _envoyer(benevole, "12", jeton=premier).text
    assert _jeton(apres) != premier
    assert _jeton(benevole.get(URL_PAGE).text) != premier


def test_deux_envois_simultanes_un_seul_tirage(benevole, actif, _journal_isole):
    """Patron des tests à fils du lot 1, par la route : un client par fil."""
    jeton = _jeton(benevole.get(URL_PAGE).text)
    nb_fils = 4
    clients = [_nouveau_benevole() for _ in range(nb_fils)]
    depart = threading.Barrier(nb_fils)
    statuts = [None] * nb_fils

    def lancer(i):
        try:
            depart.wait()
            statuts[i] = _envoyer(clients[i], "11", "22", jeton=jeton).status_code
        except Exception as erreur:   # remontée au test, jamais avalée
            statuts[i] = erreur

    fils = [threading.Thread(target=lancer, args=(i,)) for i in range(nb_fils)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=30)
    assert statuts == [200] * nb_fils
    assert len(_tirages()) == 1 and _numeros() == [11, 22]
    assert len(_journal(_journal_isole)) == 1


def test_le_tirage_apparait_sur_la_page_de_gestion(benevole, actif):
    _envoyer(benevole, "12")
    page = benevole.get(routes_tombola.URL_GESTION).text
    assert '<span class="tombola-numero-valeur">12</span>' in page


# ===========================================================================
# 4. Un seul domicile « saisir + journaliser »
# ===========================================================================
def test_les_deux_routes_passent_par_le_meme_domicile(benevole, actif, monkeypatch):
    appels = []
    original = routes_tombola.enregistrer_tirage_manuel

    def espion(request, saisies, jeton):
        appels.append(request.url.path)
        return original(request, saisies, jeton)

    monkeypatch.setattr(routes_tombola, "enregistrer_tirage_manuel", espion)
    _envoyer(benevole, "12")
    page = benevole.get(routes_tombola.URL_GESTION).text
    jeton = page.split(f'action="{routes_tombola.URL_TIRAGE}"', 1)[1] \
                .split('name="jeton" value="', 1)[1].split('"', 1)[0]
    benevole.post(routes_tombola.URL_TIRAGE, data={"numero_1": "13", "jeton": jeton})
    assert appels == [URL_TIRAGE, routes_tombola.URL_TIRAGE]
    assert _numeros() == [12, 13]


def test_saisies_a_reafficher_seulement_si_rien_n_a_ete_affiche():
    saisies = ["abc", "", "", "", ""]
    for issue in (tombola.TIRAGE_AUCUN_VALIDE, tombola.TIRAGE_VIDE):
        assert routes_tombola.saisies_a_reafficher(
            tombola.Tirage(issue, tombola.MODE_MANUEL), saisies) == saisies
    for issue in (tombola.TIRAGE_ENREGISTRE, tombola.TIRAGE_REJOUE):
        assert routes_tombola.saisies_a_reafficher(
            tombola.Tirage(issue, tombola.MODE_MANUEL, (12,)), saisies) is None


# ===========================================================================
# 5. Lecture en salle
# ===========================================================================
def test_lecture_en_salle_absente_sans_numero(benevole, actif):
    bloc = _bloc(benevole.get(URL_PAGE).text)
    assert "En attente en salle" not in bloc
    assert "tombola-public-numeros" not in bloc


def test_lecture_en_salle_groupee_sans_bouton_ni_identifiant(benevole, actif):
    _saisir("12", "34")
    _saisir("5")
    bloc = _bloc(benevole.get(URL_PAGE).text)
    assert "En attente en salle (3)" in bloc
    assert bloc.count('class="tombola-public-numeros"') == 2
    assert bloc.index("<li>5</li>") < bloc.index("<li>12</li>")   # le plus récent d'abord
    assert "numero_id" not in bloc
    # Un seul bouton dans le bloc : « Afficher les numéros ».
    assert bloc.count("<button") == 1


def test_numero_remis_ne_figure_plus_en_salle(benevole, actif):
    _saisir("12", "34")
    conn = _conn()
    try:
        tombola.remettre_par_numero(conn, "12")
    finally:
        conn.close()
    bloc = _bloc(benevole.get(URL_PAGE).text)
    assert "En attente en salle (1)" in bloc
    assert "<li>12</li>" not in bloc and "<li>34</li>" in bloc


# ===========================================================================
# 6. Périmètre : la saisie seulement
# ===========================================================================
def test_ni_remise_ni_correction_ni_tirage_aleatoire(benevole, actif):
    _saisir("12")
    page = benevole.get(URL_PAGE).text
    for adresse in (routes_tombola.URL_REMETTRE, routes_tombola.URL_ANNULER,
                    routes_tombola.URL_ERREUR, routes_tombola.URL_REMPLACER,
                    routes_tombola.URL_TIRAGE_ALEATOIRE, routes_tombola.URL_TIRAGE):
        assert f'action="{adresse}"' not in page
    for libelle in ("Lot remis", "Erreur de saisie", "Remplacer", "Tirer 5 numéros"):
        assert libelle not in page


# ===========================================================================
# 7. La page
# ===========================================================================
def _ids(page):
    return re.findall(r'\sid="([^"]+)"', page)


@pytest.mark.parametrize("ouvert", [True, False])
def test_identifiants_uniques_sur_la_page_annonce(benevole, actif, ouvert):
    _interrupteur(ouvert)
    _saisir("12")
    for page in (benevole.get(URL_PAGE).text, _envoyer(benevole, "abc").text):
        ids = _ids(page)
        assert len(ids) == len(set(ids)), ids
        for i in range(1, tombola.NUMEROS_PAR_TIRAGE + 1):
            assert f'<label for="tombola-numero-{i}">Numéro {i}</label>' in page
            assert f'id="numero-{i}"' not in page


def test_identifiants_uniques_sur_la_page_de_gestion(benevole, actif):
    _saisir("12")
    page = benevole.get(routes_tombola.URL_GESTION).text
    ids = _ids(page)
    assert len(ids) == len(set(ids)), ids
    for i in range(1, tombola.NUMEROS_PAR_TIRAGE + 1):
        assert f'<label for="numero-{i}">Numéro {i}</label>' in page


def test_un_seul_bouton_principal_sur_la_page_annonce(benevole, actif):
    _interrupteur(True)
    page = benevole.get(URL_PAGE).text
    assert page.count("bouton-principal") == 1
    assert '<button type="submit" class="bouton bouton-secondaire">Afficher les numéros</button>' in page


def test_gestion_garde_son_bouton_principal(benevole, actif):
    page = benevole.get(routes_tombola.URL_GESTION).text
    assert '<button type="submit" class="bouton bouton-principal">Afficher les numéros</button>' in page


def test_cinq_cases_numeriques_dans_un_groupe_legende(benevole, actif):
    bloc = _bloc(benevole.get(URL_PAGE).text)
    assert bloc.count('inputmode="numeric"') == tombola.NUMEROS_PAR_TIRAGE
    legende = (f"<legend>Numéros tirés, de 1 à {tombola.NUMEROS_PAR_TIRAGE} "
               f"(entre {tombola.NUMERO_MIN} et {tombola.NUMERO_MAX})</legend>")
    assert legende in bloc
