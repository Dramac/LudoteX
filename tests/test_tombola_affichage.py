"""
Tombola — affichage public et statistiques (série tombola, lot 2).

Ce que ce fichier verrouille :

1. la LECTURE PUBLIQUE (`tombola.numeros_affiches`) : édition ouverte
   seulement, aucun identifiant de ligne ni drapeau de gestion, tirage vidé
   absent, marque « Nouveau » calculée par le serveur ;
2. l'ÉCRAN DE SALLE : clé `tombola` de `/live/data` absente quand il n'y a
   rien, quand le module est désactivé ou le panneau éteint ; présente dans
   les états « tous », « discret » et « bénévoles » (règle de projection) ;
   case cochée par défaut ; marqueur de version du formulaire (une page
   d'administration périmée n'éteint rien) ; durées transmises au gabarit ;
3. la PAGE PUBLIQUE `/tombola` dans les quatre états, pour un visiteur et
   pour un bénévole ;
4. le BLOC DE L'ACCUEIL, présent en « tous » seulement pour un visiteur, et
   jamais calculé quand le module est masqué ;
5. le REMPLACEMENT d'une faute de frappe dans le tirage d'origine ;
6. les STATISTIQUES et leurs EXPORTS ;
7. « SANS TOMBOLA, RIEN NE CHANGE ».

Aucun exemple ne nomme personne : des numéros, rien d'autre.
"""

import io
import json
import re
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from app import achats, exports, gouts, journal, services, tombola
from app.routes import live
from tests.test_tombola import (  # noqa: F401 — fixtures partagées
    T0, _conn, _etat, _geste, _id, _journal, _numeros, _saisir, actif, admin,
    bases, benevole, visiteur,
)

ETATS = ("tous", "discret", "benevoles", "desactive")


def _tirer(*numeros, instant=T0):
    t = _saisir(*[str(n) for n in numeros], instant=instant)
    assert t.issue == tombola.TIRAGE_ENREGISTRE
    return t


def _affiches(instant=T0):
    conn = _conn()
    try:
        return tombola.numeros_affiches(conn, instant)
    finally:
        conn.close()


def _stats(debut=None, fin=None):
    conn = _conn()
    try:
        return tombola.statistiques(conn, debut, fin)
    finally:
        conn.close()


def _remplacer(retire_id, saisie, instant):
    conn = _conn()
    try:
        return tombola.remplacer_numero(conn, retire_id, saisie, instant)
    finally:
        conn.close()


def _panneau(valeur):
    conn = _conn()
    try:
        services.ecrire_parametre(conn, live.CLES_PANNEAUX["tombola"], valeur)
    finally:
        conn.close()


# ===========================================================================
# 1. La lecture publique
# ===========================================================================
def test_lecture_publique_sans_identifiant_ni_drapeau(bases):
    _tirer(12, 7, instant=T0)
    _tirer(301, instant=T0 + timedelta(minutes=10))
    groupes = _affiches(T0 + timedelta(minutes=11))
    assert [g["numeros"] for g in groupes] == [[301], [7, 12]]   # récent d'abord
    for g in groupes:
        assert set(g) == {"tirage_id", "heure", "numeros", "nouveau"}
        assert all(isinstance(n, int) for n in g["numeros"])
    assert groupes[0]["nouveau"] is True and groupes[1]["nouveau"] is False


def test_marque_nouveau_pendant_cinq_minutes(bases):
    _tirer(5, instant=T0)
    limite = timedelta(seconds=tombola.DUREE_NOUVEAU_TIRAGE_S)
    assert _affiches(T0 + limite - timedelta(seconds=1))[0]["nouveau"] is True
    assert _affiches(T0 + limite)[0]["nouveau"] is False


def test_tirage_vide_absent_et_edition_close_ignoree(bases):
    _tirer(4, 9)
    _geste(tombola.remettre_par_numero, "4")
    _geste(tombola.remettre_par_numero, "9")
    assert _affiches() == []
    _tirer(11)
    conn = _conn()
    try:
        tombola.terminer_edition(conn, T0)
    finally:
        conn.close()
    assert _affiches() == []


def test_erreur_de_saisie_absente_de_la_lecture_publique(bases):
    _tirer(87, 3)
    _geste(tombola.retirer_erreur_saisie, _id(87))
    assert [g["numeros"] for g in _affiches()] == [[3]]


# ===========================================================================
# 2. L'écran de salle
# ===========================================================================
def test_cle_tombola_absente_sans_numero(visiteur):
    for etat in ETATS:
        _etat(etat)
        assert "tombola" not in visiteur.get("/live/data").json()


@pytest.mark.parametrize("etat", ["tous", "discret", "benevoles"])
def test_cle_tombola_presente_hors_etat_desactive(visiteur, etat):
    _etat(etat)
    _tirer(12, 45)
    _tirer(7, instant=T0 + timedelta(minutes=3))
    d = visiteur.get("/live/data").json()
    # Ordre du défilement : du plus ancien au plus récent.
    assert [t["numeros"] for t in d["tombola"]] == [[12, 45], [7]]
    assert d["panneaux"]["tombola"] is True


def test_cle_tombola_absente_module_desactive_ou_panneau_eteint(visiteur):
    _tirer(12)
    _etat("desactive")
    d = visiteur.get("/live/data").json()
    assert "tombola" not in d and d["panneaux"]["tombola"] is False
    _etat("tous")
    _panneau("0")
    d = visiteur.get("/live/data").json()
    assert "tombola" not in d and d["panneaux"]["tombola"] is False


def test_live_data_ne_publie_ni_identifiant_ni_drapeau(visiteur):
    _etat("tous")
    _tirer(12, 45)
    tombola_json = visiteur.get("/live/data").json()["tombola"]
    for t in tombola_json:
        assert set(t) == {"tirage_id", "heure", "numeros", "nouveau"}
    texte = json.dumps(tombola_json)
    assert '"id"' not in texte and "erreur_possible" not in texte
    assert "jeton" not in texte and "mode" not in texte


def test_aucune_lecture_de_la_tombola_quand_le_panneau_est_eteint(visiteur, monkeypatch):
    appels = []
    monkeypatch.setattr(tombola, "numeros_affiches", lambda *a, **k: appels.append(1) or [])
    _etat("desactive")
    visiteur.get("/live/data")
    _etat("tous")
    _panneau("0")
    visiteur.get("/live/data")
    assert appels == []


def test_case_cochee_par_defaut_sans_ligne_en_base(admin):
    conn = _conn()
    try:
        assert live.reglages_panneaux(conn)["tombola"] is True
        assert services.lire_parametre(conn, live.CLES_PANNEAUX["tombola"]) is None
    finally:
        conn.close()
    page = admin.get("/admin/ecran-salle").text
    assert re.search(r'name="panneau_tombola" value="1"\s+checked', page)
    assert (f'name="{live.CHAMP_VERSION_PANNEAUX}" '
            f'value="{live.VERSION_PANNEAUX}"') in page


def _cases(**cases):
    return {f"panneau_{nom}": "1" for nom, cochee in cases.items() if cochee}


def test_page_admin_perimee_n_eteint_pas_la_tombola(admin):
    # Une page ouverte avant la mise à jour : quatre cases, pas de marqueur.
    admin.post("/admin/ecran-salle", data=_cases(chiffres=1, tournois=1,
                                                 programme=1, mouvements=1))
    conn = _conn()
    try:
        assert live.reglages_panneaux(conn)["tombola"] is True
        # Et un panneau ÉTEINT par le bureau le reste.
        services.ecrire_parametre(conn, live.CLES_PANNEAUX["tombola"], "0")
    finally:
        conn.close()
    admin.post("/admin/ecran-salle", data=_cases(chiffres=1))
    conn = _conn()
    try:
        assert live.reglages_panneaux(conn)["tombola"] is False
    finally:
        conn.close()


def test_formulaire_a_jour_decoche_et_coche_la_tombola(admin):
    marqueur = {live.CHAMP_VERSION_PANNEAUX: live.VERSION_PANNEAUX}
    admin.post("/admin/ecran-salle", data={**_cases(chiffres=1), **marqueur})
    conn = _conn()
    try:
        assert live.reglages_panneaux(conn)["tombola"] is False
    finally:
        conn.close()
    admin.post("/admin/ecran-salle",
               data={**_cases(chiffres=1, tombola=1), **marqueur})
    conn = _conn()
    try:
        assert live.reglages_panneaux(conn)["tombola"] is True
    finally:
        conn.close()


def test_seule_la_case_tombola_cochee_n_avertit_pas(admin):
    r = admin.post("/admin/ecran-salle",
                   data={**_cases(tombola=1),
                         live.CHAMP_VERSION_PANNEAUX: live.VERSION_PANNEAUX})
    assert "aucun panneau" not in r.text.lower()


def test_note_de_precedence_quand_le_module_est_desactive(admin):
    page = " ".join(admin.get("/admin/ecran-salle").text.split())
    assert "Le panneau Tombola est coché, mais son module est désactivé" in page
    _etat("tous")
    page = " ".join(admin.get("/admin/ecran-salle").text.split())
    assert "Le panneau Tombola est coché" not in page


def test_durees_du_defilement_transmises_au_gabarit(visiteur):
    page = visiteur.get("/live").text
    assert f"const DUREE_PAGE_TOMBOLA_MS = {live.DUREE_PAGE_TOMBOLA_S} * 1000;" in page
    assert f"const DUREE_DEFILEMENT_MS = {live.DUREE_DEFILEMENT_S} * 1000;" in page
    gabarit = Path("app/templates/live.html").read_text(encoding="utf-8")
    assert "{{ duree_page_tombola_s }} * 1000" in gabarit
    assert live.DUREE_PAGE_TOMBOLA_S == 12
    assert tombola.DUREE_NOUVEAU_TIRAGE_S == 300


def test_panneau_tombola_sans_innerhtml_ni_aria_live(visiteur):
    gabarit = Path("app/templates/live.html").read_text(encoding="utf-8")
    debut = gabarit.index("// --- Défilement de la tombola")
    fin = gabarit.index("function afficher(")
    # Le code seul, commentaires retirés : ils citent le patron des annonces.
    script = re.sub(r"//[^\n]*", "", gabarit[debut:fin])
    assert "innerHTML" not in script and "textContent" in script
    panneau = gabarit[gabarit.index('id="panneau-tombola"'):]
    panneau = panneau[:panneau.index("</div>\n    </section>")]
    assert "aria-live" not in panneau
    # Le défilement de la tombola ne partage AUCUNE variable avec celui des
    # annonces, et ne touche jamais au bandeau (D8).
    for nom in ("idCourant", "annonces", "alerteVisible", "bandeau", "avancer()"):
        assert nom not in script
    assert "prefers-reduced-motion" in gabarit[gabarit.index(".page-tombola {"):]


# ===========================================================================
# 3. La page publique /tombola
# ===========================================================================
@pytest.mark.parametrize("etat,code_visiteur,code_benevole", [
    ("tous", 200, 200), ("discret", 200, 200),
    ("benevoles", 403, 200), ("desactive", 404, 404),
])
def test_page_publique_dans_les_quatre_etats(visiteur, bases, etat, code_visiteur,
                                             code_benevole):
    from tests.test_tombola import _client, JETON
    _etat(etat)
    benevole_client = _client()
    benevole_client.get(f"/acces?jeton={JETON}", follow_redirects=False)
    assert visiteur.get("/tombola").status_code == code_visiteur
    assert benevole_client.get("/tombola").status_code == code_benevole


def test_page_publique_vide(visiteur):
    _etat("tous")
    page = visiteur.get("/tombola").text
    assert "<h1>Tombola</h1>" in page
    assert "Aucun numéro en attente pour le moment." in page
    assert "Présentez votre ticket aux bénévoles pour" in " ".join(page.split())
    assert 'http-equiv="refresh"' not in page
    assert '>Actualiser</a>' in page


def test_page_publique_ordre_et_titres(visiteur):
    _etat("tous")
    _tirer(12, 45, instant=T0)
    _tirer(9999, 1001, instant=T0 + timedelta(minutes=20))
    page = visiteur.get("/tombola").text
    assert page.count("<h1") == 1
    titres = re.findall(r"<h2 class=\"tombola-public-titre\">Tirage de (\d\d:\d\d)</h2>", page)
    assert titres == [tombola.heure_locale((T0 + timedelta(minutes=20)).isoformat()),
                      tombola.heure_locale(T0.isoformat())]
    assert page.index("<li>1001</li>") < page.index("<li>12</li>")
    assert "numero_id" not in page               # aucun identifiant de ligne
    assert "Aucun numéro en attente" not in page


def test_page_publique_lien_de_gestion_pour_les_benevoles(visiteur, benevole):
    _etat("tous")
    assert "Gérer la tombola" not in visiteur.get("/tombola").text
    assert 'href="/tombola/gestion">Gérer la tombola' in benevole.get("/tombola").text


def test_page_de_gestion_mene_a_la_page_publique(benevole, actif):
    page = benevole.get("/tombola/gestion").text
    assert '<a class="lien" href="/tombola">Voir la page publique</a>' in page


# ===========================================================================
# 4. Le bloc de l'accueil
# ===========================================================================
TITRE_BLOC = "Tombola — numéros à retirer"


@pytest.mark.parametrize("etat,attendu", [
    ("tous", True), ("discret", False), ("benevoles", False), ("desactive", False),
])
def test_bloc_accueil_visiteur_en_tous_seulement(visiteur, etat, attendu):
    _etat(etat)
    _tirer(12, 45)
    page = visiteur.get("/").text
    assert (TITRE_BLOC in page) is attendu
    assert ("Voir la page de la tombola" in page) is attendu


@pytest.mark.parametrize("etat,attendu", [
    ("tous", True), ("discret", True), ("benevoles", True), ("desactive", False),
])
def test_bloc_accueil_benevole(benevole, etat, attendu):
    _etat(etat)
    _tirer(12)
    assert (TITRE_BLOC in benevole.get("/").text) is attendu


def test_bloc_accueil_absent_sans_numero(visiteur):
    _etat("tous")
    assert TITRE_BLOC not in visiteur.get("/").text


def test_bloc_accueil_place_et_hierarchie(visiteur):
    _etat("tous")
    _tirer(12, 45)
    page = visiteur.get("/").text
    assert page.index('class="carte accueil"') < page.index(TITRE_BLOC)
    assert re.search(r'<h2 class="dispo-titre">Tombola — numéros à retirer</h2>\s*'
                     r'<h3 class="tombola-public-titre">Tirage de', page)
    assert '<a class="lien" href="/tombola">Voir la page de la tombola</a>' in page


def test_accueil_ne_lit_pas_la_tombola_module_masque(visiteur, monkeypatch):
    appels = []
    monkeypatch.setattr(tombola, "numeros_affiches", lambda *a, **k: appels.append(1) or [])
    for etat in ("desactive", "discret", "benevoles"):
        _etat(etat)
        visiteur.get("/")
    assert appels == []


# ===========================================================================
# 5. Remplacer une faute de frappe dans le tirage d'origine
# ===========================================================================
def _tirage_avec_faute():
    t = _tirer(87, 3, instant=T0)
    g = _geste(tombola.retirer_erreur_saisie, _id(87), instant=T0 + timedelta(minutes=1))
    assert g.issue == tombola.RETRAIT_FAIT
    return t.tirage_id, g.numero_id


def test_remplacement_dans_le_meme_tirage(bases):
    tirage_id, retire_id = _tirage_avec_faute()
    instant = T0 + timedelta(minutes=2)
    r = _remplacer(retire_id, "78", instant)
    assert r.issue == tombola.REMPLACEMENT_FAIT and (r.ancien, r.nouveau) == (87, 78)
    ligne = _numeros()[-1]
    assert ligne["numero"] == 78 and ligne["tirage_id"] == tirage_id
    assert ligne["affiche_le"] == instant.isoformat(timespec="seconds")
    assert ligne["remplace_id"] == retire_id and ligne["motif"] is None
    assert [g["numeros"] for g in _affiches(instant)] == [[3, 78]]   # un seul tirage


def test_remplacement_fenetre_de_cinq_minutes_apres_le_retrait(bases):
    _, retire_id = _tirage_avec_faute()
    retrait = T0 + timedelta(minutes=1)
    limite = timedelta(seconds=tombola.DELAI_ERREUR_SAISIE_S)
    assert _remplacer(retire_id, "78", retrait + limite).issue == tombola.REMPLACEMENT_TROP_TARD
    assert _remplacer(retire_id, "78", retrait + limite - timedelta(seconds=1)).issue \
        == tombola.REMPLACEMENT_FAIT


def test_remplacement_rejoue_et_deja_fait(bases):
    _, retire_id = _tirage_avec_faute()
    instant = T0 + timedelta(minutes=2)
    assert _remplacer(retire_id, "78", instant).issue == tombola.REMPLACEMENT_FAIT
    assert _remplacer(retire_id, "78", instant).issue == tombola.REMPLACEMENT_REJOUE
    r = _remplacer(retire_id, "79", instant)
    assert r.issue == tombola.REMPLACEMENT_DEJA and r.nouveau == 78
    assert [l["numero"] for l in _numeros()].count(78) == 1
    assert 79 not in [l["numero"] for l in _numeros()]


def test_remplacement_double_appui_simultane(bases):
    _, retire_id = _tirage_avec_faute()
    instant = T0 + timedelta(minutes=2)
    issues, depart = [], threading.Barrier(4)

    def envoyer():
        depart.wait()
        issues.append(_remplacer(retire_id, "78", instant).issue)

    fils = [threading.Thread(target=envoyer) for _ in range(4)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert sorted(issues) == sorted([tombola.REMPLACEMENT_FAIT]
                                    + [tombola.REMPLACEMENT_REJOUE] * 3)
    assert len([l for l in _numeros() if l["numero"] == 78]) == 1


def test_remplacement_refuse_un_numero_deja_sorti(bases):
    _tirer(5, instant=T0)
    _, retire_id = _tirage_avec_faute()
    r = _remplacer(retire_id, "5", T0 + timedelta(minutes=2))
    assert r.issue == tombola.REMPLACEMENT_DEJA_SORTI
    assert tombola.message_remplacement(r).remplacer_id == retire_id   # on peut retaper


@pytest.mark.parametrize("saisie,issue", [
    ("", tombola.REMPLACEMENT_VIDE), ("   ", tombola.REMPLACEMENT_VIDE),
    ("12x", tombola.REMPLACEMENT_ILLISIBLE), ("10000", tombola.REMPLACEMENT_ILLISIBLE),
])
def test_remplacement_sans_saisie_valide_ne_change_rien(bases, saisie, issue):
    _, retire_id = _tirage_avec_faute()
    avant = _numeros()
    r = _remplacer(retire_id, saisie, T0 + timedelta(minutes=2))
    assert r.issue == issue and _numeros() == avant


def test_remplacement_impossible_hors_erreur_ou_edition_close(bases):
    _tirer(4, instant=T0)
    assert _remplacer(_id(4), "8", T0).issue == tombola.REMPLACEMENT_IMPOSSIBLE
    assert _remplacer(9999, "8", T0).issue == tombola.REMPLACEMENT_IMPOSSIBLE
    _, retire_id = _tirage_avec_faute()
    conn = _conn()
    try:
        tombola.terminer_edition(conn, T0 + timedelta(minutes=2))
    finally:
        conn.close()
    assert _remplacer(retire_id, "78", T0 + timedelta(minutes=2)).issue \
        == tombola.REMPLACEMENT_IMPOSSIBLE


def test_numero_remplace_se_remet_comme_les_autres(bases):
    _, retire_id = _tirage_avec_faute()
    _remplacer(retire_id, "78", T0 + timedelta(minutes=2))
    g = _geste(tombola.remettre_par_numero, "78", instant=T0 + timedelta(minutes=5))
    assert g.issue == tombola.REMISE_FAITE
    # Et le délai de remise court depuis le remplacement, pas depuis le tirage.
    assert _stats()["delai_median"] == "3 min"


def test_parcours_http_du_remplacement_et_journal(benevole, actif, _journal_isole):
    assert "tombola_numero_remplace" in journal.ACTIONS
    page = benevole.get("/tombola/gestion").text
    jeton = page.split('action="/tombola/gestion/tirage"', 1)[1] \
        .split('name="jeton" value="', 1)[1].split('"', 1)[0]
    benevole.post("/tombola/gestion/tirage",
                  data={"numero_1": "87", "numero_2": "3", "jeton": jeton})
    r = benevole.post("/tombola/gestion/erreur-saisie", data={"numero_id": str(_id(87))})
    assert "Numéro correct" in r.text and ">Remplacer</button>" in r.text
    retire = str(_id(87))
    r = benevole.post("/tombola/gestion/remplacer", data={"numero_id": retire, "numero": "abc"})
    assert "Tapez un numéro entre 1 et 9999 pour remplacer le 87." in r.text
    assert 'value="abc"' in r.text                                   # saisie remise
    r = benevole.post("/tombola/gestion/remplacer", data={"numero_id": retire, "numero": "78"})
    assert "Numéro 78 affiché à la place du 87, dans le même tirage." in r.text
    r = benevole.post("/tombola/gestion/remplacer", data={"numero_id": retire, "numero": "78"})
    assert "un seul remplacement a été enregistré" in r.text
    assert benevole.get("/tombola/gestion/remplacer", follow_redirects=False).status_code == 303
    lignes = [(l["action"], l.get("objet"), l.get("ref")) for l in _journal(_journal_isole)]
    assert lignes[-1] == ("tombola_numero_remplace", "87 → 78", "78")
    assert [a for a, _, _ in lignes].count("tombola_numero_remplace") == 1


def test_remplacement_au_vocabulaire_et_a_la_conception():
    conception = Path("docs/conception-journal.md").read_text(encoding="utf-8")
    assert "tombola_numero_remplace" in conception


def test_formation_vide_aussi_les_remplacements(bases):
    from app import formation
    _, retire_id = _tirage_avec_faute()
    _remplacer(retire_id, "78", T0 + timedelta(minutes=2))
    conn = _conn()
    try:
        formation._vider_base_pret(conn)
        assert conn.execute("SELECT COUNT(*) FROM tombola_numeros").fetchone()[0] == 0
    finally:
        conn.close()


def test_migration_ajoute_la_colonne_a_une_base_du_lot_1(bases):
    from app import db
    conn = _conn()
    try:
        conn.execute("DROP INDEX idx_tombola_un_remplacement")
        conn.execute("ALTER TABLE tombola_numeros DROP COLUMN remplace_id")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    db.init_db()
    conn = _conn()
    try:
        colonnes = [l[1] for l in conn.execute("PRAGMA table_info(tombola_numeros)")]
        index = {l[0] for l in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert "remplace_id" in colonnes and "idx_tombola_un_remplacement" in index


# ===========================================================================
# 6. Statistiques et exports
# ===========================================================================
def _remettre_apres(numero, minutes, depuis=T0):
    g = _geste(tombola.remettre_par_numero, str(numero),
               instant=depuis + timedelta(minutes=minutes))
    assert g.issue == tombola.REMISE_FAITE


def test_aucune_statistique_sans_tirage(bases):
    assert _stats() is None


def test_compteurs_et_exclusions(bases):
    _tirer(1, 2, 3, 4, 87, instant=T0)
    _geste(tombola.retirer_erreur_saisie, _id(87))
    _remettre_apres(1, 10)
    _remettre_apres(2, 20)
    conn = _conn()
    try:
        tombola.terminer_edition(conn, T0 + timedelta(hours=8))   # 3 non réclamé
    finally:
        conn.close()
    _tirer(9, instant=T0 + timedelta(hours=9))                     # nouvelle édition
    s = _stats()
    assert (s["tires"], s["remis"], s["affiches"], s["non_reclames"],
            s["erreurs_saisie"], s["nb_tirages"]) == (5, 2, 1, 2, 1, 2)
    # Médiane de 10 et 20 min ; ni le non réclamé (8 h) ni l'erreur n'y entrent.
    assert s["delai_median"] == "15 min"
    assert [e["tires"] for e in s["par_edition"]] == [4, 1]
    assert s["par_edition"][0]["close"] and s["par_edition"][1]["close"] == ""


@pytest.mark.parametrize("delais,attendu", [
    ([], "—"), ([7], "7 min"), ([10, 30], "20 min"), ([1, 2, 50, 3, 4], "3 min"),
])
def test_mediane_avec_zero_un_deux_et_n_remis(bases, delais, attendu):
    numeros = list(range(1, 6))
    _tirer(*numeros, instant=T0)
    for numero, minutes in zip(numeros, delais):
        _remettre_apres(numero, minutes)
    s = _stats()
    assert s["delai_median"] == attendu
    assert s["par_tirage"][0]["delai_median"] == attendu


def test_delai_negatif_ecarte_et_compte(bases):
    _tirer(1, 2, instant=T0)
    _remettre_apres(1, 6)
    _geste(tombola.remettre_par_numero, "2", instant=T0 - timedelta(minutes=30))
    s = _stats()
    assert s["remis"] == 2 and s["delais_ecartes"] == 1 and s["delai_median"] == "6 min"


def test_par_tirage_et_par_mode(bases):
    _tirer(1, 2, instant=T0)
    conn = _conn()
    try:
        tombola.tirer_au_hasard(conn, "50", tombola.nouveau_jeton(),
                                T0 + timedelta(hours=1))
    finally:
        conn.close()
    s = _stats()
    assert [(t["mode"], t["tires"]) for t in s["par_tirage"]] == [
        ("à la main", 2), ("par l'application", 5)]
    assert [(m["mode"], m["nb_tirages"], m["tires"]) for m in s["par_mode"]] == [
        ("à la main", 1, 2), ("par l'application", 1, 5)]
    for t in s["par_tirage"]:
        assert "tirage_id" not in t and "id" not in t


def test_filtre_de_periode_sur_l_heure_du_tirage(bases):
    _tirer(1, instant=T0)
    _tirer(2, instant=T0 + timedelta(hours=2))
    _remettre_apres(1, 180)              # remis APRÈS le début du second tirage
    debut = (T0 + timedelta(hours=1)).isoformat(timespec="seconds")
    s = _stats(debut=debut)
    assert s["tires"] == 1 and s["remis"] == 0
    s = _stats(fin=debut)
    assert s["tires"] == 1 and s["remis"] == 1
    assert _stats(debut=(T0 + timedelta(hours=5)).isoformat(timespec="seconds")) is None


@pytest.fixture
def visiteur_stats(visiteur):
    """
    Visiteur sur une page des statistiques PUBLIQUE. Depuis le lot bilan-2,
    elles sont réservées aux bénévoles par défaut : un visiteur y serait
    refusé et ces tests, qui vérifient ce que la tombola montre à un
    visiteur, deviendraient vides. On les règle sur « tous ».
    """
    conn = _conn()
    try:
        services.ecrire_parametre(conn, "module_stats", "tous")
    finally:
        conn.close()
    return visiteur


def test_section_stats_selon_module_et_tirages(visiteur_stats):
    _etat("tous")
    assert 'id="tombola"' not in visiteur_stats.get("/stats").text   # aucun tirage
    _tirer(12, 45)
    page = visiteur_stats.get("/stats").text
    assert '<section class="carte" id="tombola">' in page
    assert 'name="sections" value="tombola" checked' in page
    _etat("desactive")
    page = visiteur_stats.get("/stats").text
    assert 'id="tombola"' not in page and 'value="tombola"' not in page
    _etat("discret")                       # masqué pour un visiteur
    assert 'id="tombola"' not in visiteur_stats.get("/stats").text


def test_stats_ne_lisent_pas_la_tombola_module_masque(visiteur_stats, monkeypatch):
    appels = []
    monkeypatch.setattr(tombola, "statistiques", lambda *a, **k: appels.append(1))
    visiteur_stats.get("/stats")
    visiteur_stats.get("/stats/export.xlsx")
    visiteur_stats.get("/stats/export.pdf")
    assert appels == []


def _feuilles(contenu):
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(contenu))


def test_feuille_excel_presente_ou_absente(visiteur_stats):
    _etat("tous")
    assert "Tombola" not in _feuilles(visiteur_stats.get("/stats/export.xlsx").content).sheetnames
    _tirer(12, 45)
    _remettre_apres(12, 4)
    classeur = _feuilles(visiteur_stats.get("/stats/export.xlsx").content)
    assert classeur.sheetnames == ["Synthèse", exports.FEUILLE_PALMARES,
                                   achats.FEUILLE_TOUS_SORTIS, achats.FEUILLE_JAMAIS_SORTIS,
                                   gouts.FEUILLE_GOUTS, gouts.FEUILLE_SANS_CATEGORIE,
                                   exports.FEUILLE_HEURE_PAR_HEURE, "Détail", "Tombola"]
    valeurs = [[c.value for c in ligne] for ligne in classeur["Tombola"].iter_rows()]
    plat = [v for ligne in valeurs for v in ligne if v is not None]
    assert "Délai médian de remise (lots remis seulement)" in plat
    assert "Par tirage" in plat and "4 min" in plat
    _etat("desactive")
    assert "Tombola" not in _feuilles(visiteur_stats.get("/stats/export.xlsx").content).sheetnames


def test_cellules_de_la_feuille_tombola_neutralisees(bases):
    _tirer(12)
    s = _stats()
    s["par_tirage"][0]["mode"] = "=1+1"        # aucune saisie ne l'atteint : filet
    classeur = _feuilles(exports.construire_xlsx(
        {"globales": {"total_prets": 0, "en_cours": 0, "titres_pretes": 0, "nb_titres": 0},
         "plus": [], "moins": [], "prets": [], "metrique": "total"}, "tout", tombola=s))
    cellules = [c for ligne in classeur["Tombola"].iter_rows() for c in ligne
                if c.value == "=1+1"]
    assert cellules and all(c.data_type == "s" for c in cellules)


def test_section_pdf_cochable(visiteur_stats, monkeypatch):
    vus = []
    original = exports.construire_pdf

    def espion(data, periode, sections, couleur, tombola=None):
        vus.append((set(sections), tombola is not None))
        return original(data, periode, sections, couleur, tombola=tombola)

    monkeypatch.setattr(exports, "construire_pdf", espion)
    _etat("tous")
    visiteur_stats.get("/stats/export.pdf")
    _tirer(12, 45)
    visiteur_stats.get("/stats/export.pdf")
    r = visiteur_stats.get("/stats/export.pdf", params=[("sections", "synthese")])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    visiteur_stats.get("/stats/export.pdf", params=[("sections", "tombola")])
    assert vus == [
        ({"synthese", "plus", "moins"}, False),
        ({"synthese", "plus", "moins", "tombola"}, True),
        ({"synthese"}, True),
        ({"tombola"}, True),
    ]
    assert exports.SECTIONS_PDF == ("synthese", "plus", "moins", "tombola", "detail")


def test_pdf_tombola_se_construit_sans_couleur_en_dur(bases):
    _tirer(12, 45)
    _remettre_apres(12, 4)
    data = {"globales": {"total_prets": 0, "en_cours": 0, "titres_pretes": 0,
                         "nb_titres": 0}, "plus": [], "moins": [], "prets": [],
            "metrique": "total"}
    avec = exports.construire_pdf(data, "tout", {"tombola"}, "#123456", tombola=_stats())
    sans = exports.construire_pdf(data, "tout", {"tombola"}, "#123456", tombola=None)
    assert avec.startswith(b"%PDF") and len(avec) > len(sans)


# ===========================================================================
# 7. Sans tombola, rien ne change
# ===========================================================================
def _sans_horodatage(d):
    return {k: v for k, v in d.items() if k != "horodatage"}


def test_sans_tirage_live_data_identique_hors_reglage(visiteur):
    _etat("desactive")
    avant = _sans_horodatage(visiteur.get("/live/data").json())
    _etat("tous")
    apres = _sans_horodatage(visiteur.get("/live/data").json())
    assert avant["panneaux"].pop("tombola") is False
    assert apres["panneaux"].pop("tombola") is True
    assert avant == apres and "tombola" not in apres


def test_sans_tirage_accueil_et_stats_identiques(visiteur_stats):
    _etat("desactive")
    accueil_avant, stats_avant = visiteur_stats.get("/").text, visiteur_stats.get("/stats").text
    _etat("tous")
    assert visiteur_stats.get("/").text == accueil_avant
    assert visiteur_stats.get("/stats").text == stats_avant
