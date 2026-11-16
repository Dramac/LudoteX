"""
Lot bilan-2 — les statistiques réservées aux bénévoles par défaut.

Le module « stats » a son propre `etat_defaut` (« benevoles »), SANS
`jamais_public` : le bureau peut rouvrir la page au public. La garde est donc
`garde_module("stats")` sur tout le routeur, pas un `exiger_jeton` en propre
(le pourquoi est en commentaire de `MODULES["stats"]`).

Un jeton est configuré (fixture `bases`) : sans lui, tout appareil compte
comme bénévole et aucun refus ne serait observable.
"""

import re

import pytest

from app import modules
from tests.test_tombola import (  # noqa: F401 — fixtures partagées
    _conn, admin, bases, benevole, visiteur,
)

ETATS = ("tous", "benevoles", "discret", "desactive")


def _etat_stats(etat):
    conn = _conn()
    try:
        modules.ecrire_etat_module(conn, "stats", etat)
    finally:
        conn.close()


def _menu(html):
    return html.split('class="menu-benevole"')[1].split("</nav>")[0]


def _pied(html):
    return html.split('<footer class="pied">')[1].split("</footer>")[0]


# ---------------------------------------------------------------------------
# POINT 1 — le défaut du module
# ---------------------------------------------------------------------------
def test_defaut_du_module_stats_est_benevoles():
    assert modules.etat_defaut_module("stats") == "benevoles"


def test_base_neuve_lit_benevoles(bases):
    conn = _conn()
    try:
        assert modules.lire_etat_module(conn, "stats") == modules.etat_defaut_module("stats")
    finally:
        conn.close()


def test_tous_reste_proposable():
    """Décision 2 : pas de `jamais_public`, une autre association peut rouvrir."""
    assert "jamais_public" not in modules.MODULES["stats"]
    assert modules.etats_valides_module("stats") == modules.ETATS_VALIDES


def test_admin_affiche_benevoles_coche_et_les_quatre_etats(admin):
    html = admin.get("/admin/fonctionnalites").text
    for etat in ETATS:
        assert f'id="mod_stats_{etat}"' in html
    coches = re.findall(r'id="mod_stats_(\w+)"\s+checked', html)
    assert coches == ["benevoles"]


def test_admin_enregistre_tous(admin):
    r = admin.post("/admin/fonctionnalites", data={"module_stats": "tous"},
                   follow_redirects=False)
    assert r.status_code == 303
    conn = _conn()
    try:
        assert modules.lire_etat_module(conn, "stats") == "tous"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Accès : quatre états × visiteur / bénévole, page, export, alias
# ---------------------------------------------------------------------------
ATTENDU = {
    # état        : (visiteur, bénévole)
    "tous":       (200, 200),
    "benevoles":  (403, 200),
    "discret":    (200, 200),
    "desactive":  (404, 404),
}


@pytest.mark.parametrize("url", ["/stats", "/stats/export.xlsx",
                                 "/stats/export.pdf", "/statistiques"])
@pytest.mark.parametrize("etat", ETATS)
def test_acces_selon_etat(visiteur, benevole, etat, url):
    _etat_stats(etat)
    attendu_visiteur, attendu_benevole = ATTENDU[etat]
    # L'alias redirige vers /stats : on suit, c'est la page finale qui compte.
    assert visiteur.get(url).status_code == attendu_visiteur
    assert benevole.get(url).status_code == attendu_benevole


def test_defaut_visiteur_refuse_avec_un_chemin(visiteur):
    """Ne jamais bloquer : la page « accès réservé », jamais une erreur brute."""
    r = visiteur.get("/stats")
    assert r.status_code == 403
    assert "<h1>Accès réservé aux bénévoles</h1>" in r.text
    assert "lien d'activation" in r.text
    assert "Aller au catalogue public" in r.text
    assert "n'existe pas" not in r.text


def test_alias_refuse_sans_rediriger(visiteur):
    """La garde couvre aussi les alias : pas de détour par une redirection."""
    r = visiteur.get("/stat", follow_redirects=False)
    assert r.status_code == 403


def test_defaut_benevole_servi(benevole):
    r = benevole.get("/stats")
    assert r.status_code == 200
    assert "<h1" in r.text


# ---------------------------------------------------------------------------
# Liens : menu visiteur, menu bénévole, pied de page
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("etat,visible_visiteur,visible_benevole", [
    ("benevoles", False, True),
    ("tous", True, True),
    ("discret", False, True),
    ("desactive", False, False),
])
def test_liens_selon_etat(visiteur, benevole, etat, visible_visiteur, visible_benevole):
    _etat_stats(etat)
    html_v = visiteur.get("/catalogue").text
    html_b = benevole.get("/catalogue").text
    assert ('href="/stats"' in _menu(html_v)) is visible_visiteur
    assert ('href="/stats"' in _pied(html_v)) is visible_visiteur
    assert ('href="/stats"' in _menu(html_b)) is visible_benevole
    assert ('href="/stats"' in _pied(html_b)) is visible_benevole


def test_menu_visiteur_par_defaut_sans_statistiques(visiteur):
    """Base neuve : aucun lien vers une page qui le refuserait."""
    assert 'href="/stats"' not in visiteur.get("/catalogue").text


# ---------------------------------------------------------------------------
# POINT 3 — /apropos
# ---------------------------------------------------------------------------
def _sections_apropos(html):
    visiteur_ = html.split("<h3>Visiteur</h3>")[1].split("<h3>Bénévole</h3>")[0]
    benevole_ = html.split("<h3>Bénévole</h3>")[1].split("<h3>Administrateur</h3>")[0]
    return visiteur_, benevole_


def test_apropos_statistiques_sous_benevole(visiteur):
    sec_visiteur, sec_benevole = _sections_apropos(visiteur.get("/apropos").text)
    assert "statistiques" not in sec_visiteur
    assert "statistiques" in sec_benevole
    assert "montrer à tous les visiteurs" in sec_benevole


def test_apropos_lien_seulement_si_la_page_s_ouvre(visiteur, benevole):
    assert 'href="/stats"' not in visiteur.get("/apropos").text
    assert 'href="/stats"' in benevole.get("/apropos").text
    _etat_stats("tous")
    assert 'href="/stats"' in visiteur.get("/apropos").text


def test_aucun_lien_stats_rendu_au_visiteur_par_defaut(visiteur):
    """Inventaire du POINT 3 : pages publiques courantes, état par défaut."""
    for url in ("/", "/catalogue", "/apropos", "/aide", "/tournois",
                "/programme", "/planning"):
        r = visiteur.get(url)
        assert r.status_code == 200, url
        assert 'href="/stats"' not in r.text, url


# ---------------------------------------------------------------------------
# POINT 2 — arbitrage (a) : un « tous » enregistré n'est jamais réécrit
# ---------------------------------------------------------------------------
def test_tous_enregistre_survit_a_l_initialisation(bases):
    from app import db

    _etat_stats("tous")
    db.init_db()
    db.init_db()
    conn = _conn()
    try:
        assert modules.lire_etat_module(conn, "stats") == "tous"
    finally:
        conn.close()


def test_tous_enregistre_garde_la_page_publique(visiteur):
    _etat_stats("tous")
    assert visiteur.get("/stats").status_code == 200


# ---------------------------------------------------------------------------
# Garde-fous : accueil et écran de salle ne passent pas par ce module
# ---------------------------------------------------------------------------
def test_live_data_identique_quel_que_soit_l_etat(visiteur):
    def donnees():
        d = visiteur.get("/live/data").json()
        d.pop("horodatage", None)
        return d

    _etat_stats("tous")
    avant = donnees()
    _etat_stats("benevoles")
    assert donnees() == avant
    assert visiteur.get("/live").status_code == 200


def test_accueil_ne_change_que_par_le_lien_du_pied(visiteur):
    _etat_stats("tous")
    avec = visiteur.get("/").text
    _etat_stats("benevoles")
    sans = visiteur.get("/").text
    principal = lambda h: h.split('<main')[1].split("</main>")[0]  # noqa: E731
    assert principal(avec) == principal(sans)


def test_aucun_exiger_jeton_sur_les_routes_stats():
    """Décision 3 : un `exiger_jeton` en propre fermerait la page même en « tous »."""
    from app.routes import stats

    for route in stats.router.routes:
        noms = [d.call.__name__ for d in route.dependant.dependencies]
        assert "exiger_jeton" not in noms, route.path
