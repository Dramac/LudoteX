"""
Lot bilan-7a — la synthèse de tête, le sommaire et la section « Organisation »
de `/stats` (`app/organisation.py`, `routes/stats.py::_organisation`).

Ordre des familles :

1. synthèse à quatre cases, mêmes valeurs que `stats_globales` ;
2. sommaire : un lien par section présente, aucun vers une absente ;
3. visibilité : Organisation réservée aux bénévoles, même `/stats` publique ;
4. prêts encore ouverts : avec et sans clôture, lien d'administration ;
5. signalements : comptes, « Non classé », période, sentinelle du texte ;
6. tournois et programme : comptes, sans date, sentinelle du pseudo,
   module masqué, base absente ou illisible ;
7. détail des prêts replié ; hiérarchie des titres ; CSS ; invariant des
   trois bases.

Les libellés sont importés d'`app.organisation`, jamais recopiés.
"""

import re
from pathlib import Path

import pytest
from markupsafe import escape

from app import organisation, services
from app.tournoi import db as tdb
from app.tournoi import programme as programme_services
from app.tournoi import services as tournoi_services
from tests.test_tombola import (  # noqa: F401 — fixtures partagées
    _conn, admin, bases, benevole, visiteur,
)

SENTINELLE_TEXTE = "SENTINELLE-TEXTE-7A"
SENTINELLE_PSEUDO = "SENTINELLE-PSEUDO-7A"
COULEUR_EN_DUR = re.compile(r"#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(", re.IGNORECASE)
# L'édition fictive : samedi 14 et dimanche 15 novembre 2026, période finie.
EDITION = "/stats?edition=autre&debut=2026-11-14T00:00&fin=2026-11-16T00:00"


def _e(texte):
    return str(escape(texte))


def _executer(requete, params=()):
    conn = _conn()
    try:
        conn.execute(requete, params)
        conn.commit()
    finally:
        conn.close()


def _parametre(cle, valeur):
    conn = _conn()
    try:
        services.ecrire_parametre(conn, cle, valeur)
    finally:
        conn.close()


def _tournoi_sql(requete, params=()):
    conn = tdb.get_connection()
    try:
        cur = conn.execute(requete, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _sam(heure):
    """« 10:00 » ou « 14:00:30 » le samedi 14 novembre 2026, en UTC."""
    return f"2026-11-14T{heure if heure.count(':') == 2 else heure + ':00'}+00:00"


@pytest.fixture
def catalogue(bases):
    """Trois jeux, quatre boîtes, et une édition de prêts : 3 prêts rendus,
    1 oubli, 2 erreurs, 1 sortie tournoi, 1 prêt encore ouvert."""
    for ref, codes in (("CATAN", ("001", "002")), ("DIXIT", ("003",)), ("AZUL", ("004",))):
        _executer("INSERT INTO titres (reference_titre, nom) VALUES (?, ?)", (ref, ref.title()))
        for c in codes:
            _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)", (c, ref))
    for code, sortie, retour, motif in (
        ("001", "10:00", "11:00", "pret"), ("002", "10:30", "12:00", "pret"),
        ("003", "11:00", "11:40", "pret"), ("003", "13:00", "16:00", "oubli"),
        ("004", "14:00", "14:00:30", "erreur"), ("001", "15:00", "15:00:20", "erreur"),
        ("002", "16:00", "18:00", "tournoi"), ("004", "17:00", None, "pret"),
    ):
        _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) VALUES (?, ?, ?, ?)",
                  (code, _sam(sortie), _sam(retour) if retour else None, motif))
    return bases


@pytest.fixture
def public(bases):
    """`/stats` rouverte au public par le bureau (lot bilan-2)."""
    _parametre("module_stats", "tous")
    return bases


def _section(html):
    return html.split('<section class="carte" id="organisation">', 1)[1].split("</section>", 1)[0]


def _cases(html):
    """(libellé, valeur) des cases chiffrées du bloc donné."""
    return [(re.sub(r"<[^>]+>", "", lib), val) for val, lib in re.findall(
        r'<span class="chiffre-val"[^>]*>(.*?)</span><span class="chiffre-lib">(.*?)</span>', html, re.S)]


# ===========================================================================
# 1. Synthèse de tête
# ===========================================================================
def test_synthese_quatre_cases_memes_valeurs(catalogue, benevole):
    html = benevole.get(EDITION).text
    tete = html.split('<div class="stats-chiffres">', 1)[1].split('<nav class="stats-sommaire"', 1)[0]
    conn = _conn()
    try:
        g = services.stats_globales(conn, services.local_vers_utc_iso("2026-11-14T00:00"),
                                    services.local_vers_utc_iso("2026-11-16T00:00"))
    finally:
        conn.close()
    cases = _cases(tete)
    assert [lib.split(",")[0] for lib, _ in cases] == [
        "prêts", "jeux différents prêtés", "jeux dehors au pic", "durée moyenne"]
    assert cases[0][1] == str(g["total_prets"]) == "5"
    assert cases[1][1] == f"{g['titres_pretes']} / {g['nb_titres']}" == "3 / 3"
    assert cases[3][1] == g["duree_moyenne"]
    # Ce qui a quitté la tête est dans Organisation, mêmes valeurs.
    orga = dict(_cases(_section(html).split("</div>\n  {#", 1)[0]))
    assert orga == {"erreurs de prêt": str(g["erreurs"]), "retour non scanné": str(g["oublis"]),
                    "prêt encore ouvert": str(g["en_cours"])}
    assert (g["erreurs"], g["oublis"], g["en_cours"]) == (2, 1, 1)


def test_infobulles_devenues_phrases(catalogue, benevole):
    html = benevole.get(EDITION).text
    section = _section(html)
    assert _e(organisation.DEF_ERREURS) in section and _e(organisation.DEF_OUBLIS) in section
    # Plus aucune infobulle de définition en tête de page.
    tete = html.split("<h1>", 1)[1].split('<nav class="stats-sommaire"', 1)[0]
    assert "erreur" not in tete and "moins d&#39;une minute" not in tete


# ===========================================================================
# 2. Sommaire
# ===========================================================================
def _sommaire(html):
    nav = re.search(r'<nav class="stats-sommaire" aria-label="([^"]+)">(.*?)</nav>', html, re.S)
    return nav.group(1), re.findall(r'href="#([^"]+)"', nav.group(2))


def test_sommaire_benevole_sans_tombola(catalogue, benevole):
    nom, liens = _sommaire(benevole.get(EDITION).text)
    assert nom == "Sommaire des statistiques"
    assert liens == ["comptoir", "achats", "gouts", "organisation", "tournois", "detail"]


def test_sommaire_chaque_lien_a_sa_cible(catalogue, benevole):
    html = benevole.get(EDITION).text
    for ancre in _sommaire(html)[1]:
        assert html.count(f'id="{ancre}"') == 1


def test_sommaire_tombola_et_modules_masques(catalogue, benevole):
    from app import tombola
    from datetime import datetime, timezone

    _parametre("module_tombola", "tous")
    conn = _conn()
    try:
        tombola.saisir_tirage(conn, ["7"], tombola.nouveau_jeton(),
                              datetime(2026, 11, 14, 15, tzinfo=timezone.utc))
    finally:
        conn.close()
    assert _sommaire(benevole.get(EDITION).text)[1] == [
        "comptoir", "achats", "gouts", "organisation", "tombola", "tournois", "detail"]
    _parametre("module_tournois", "desactive")
    _parametre("module_programme", "desactive")
    assert "tournois" not in _sommaire(benevole.get(EDITION).text)[1]


def test_sommaire_visiteur(catalogue, public, visiteur):
    assert _sommaire(visiteur.get(EDITION).text)[1] == ["comptoir", "achats", "gouts"]


def test_sommaire_cibles_44_px():
    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    assert re.search(r"\.stats-sommaire \.lien \{[^}]*min-height: 44px", css)
    assert re.search(r"\.org-volet > summary \{[^}]*padding-block: 10px", css)


# ===========================================================================
# 3. Visibilité
# ===========================================================================
def test_organisation_absente_pour_un_visiteur(catalogue, public, visiteur, monkeypatch):
    appels = []
    monkeypatch.setattr(organisation, "signalements", lambda *a, **k: appels.append(1))
    monkeypatch.setattr(tournoi_services, "bilan_periode", lambda *a, **k: appels.append(2))
    r = visiteur.get(EDITION)
    assert r.status_code == 200
    assert 'id="organisation"' not in r.text and 'id="detail"' not in r.text
    assert appels == []                    # rien n'est calculé pour un visiteur


def test_organisation_presente_pour_benevole_et_admin(catalogue, benevole, admin):
    for client in (benevole, admin):
        section = _section(client.get(EDITION).text)
        assert f"<h2>{_e(organisation.TITRE_SECTION)}</h2>" in section
        assert _e(organisation.PHRASE_SENS) in section
        assert _e(organisation.NOTE_RESERVEE) in section


# ===========================================================================
# 4. Prêts encore ouverts
# ===========================================================================
def test_ouverts_periode_finie_sans_cloture(catalogue, benevole):
    section = _section(benevole.get(EDITION).text)
    assert _e(organisation.phrase_ouverts(1, True)) in section
    assert "la clôture de fin d&#39;événement n&#39;a pas été faite" in section
    assert _e(organisation.PHRASE_DEMANDER_BUREAU) in section
    assert organisation.URL_CLOTURE not in section
    # Aucun bouton d'action, aucun formulaire.
    assert "<form" not in section and "<button" not in section


def test_ouverts_lien_d_administration_pour_l_admin(catalogue, admin):
    section = _section(admin.get(EDITION).text)
    assert f'href="{organisation.URL_CLOTURE}"' in section
    assert _e(organisation.PHRASE_DEMANDER_BUREAU) not in section
    assert 'id="fin-evenement"' in admin.get("/admin").text   # la cible existe


def test_ouverts_apres_cloture(catalogue, admin):
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
    finally:
        conn.close()
    section = _section(admin.get(EDITION).text)
    assert _e(organisation.phrase_ouverts(0, True)) in section
    assert organisation.URL_CLOTURE not in section


def test_ouverts_periode_sans_fin(catalogue, benevole):
    section = _section(benevole.get("/stats?edition=tout").text)
    assert _e(organisation.phrase_ouverts(1, False)) in section


def test_phrases_ouverts():
    assert organisation.phrase_ouverts(0, False) == "Aucun prêt de la période n'est encore ouvert."
    assert "2 prêts sont encore ouverts" in organisation.phrase_ouverts(2, True)
    assert "1 prêt de la période est encore ouvert" in organisation.phrase_ouverts(1, False)


# ===========================================================================
# 5. Signalements
# ===========================================================================
@pytest.fixture
def carnet(catalogue):
    conn = _conn()
    try:
        piece = services.creer_categorie_signalement(conn, "Pièce manquante")
        boite = services.creer_categorie_signalement(conn, "Boîte abîmée")
        conn.commit()
    finally:
        conn.close()
    for code, cat, cree, traite in (
        ("001", piece, "2026-11-14T10:00:00+00:00", "2026-11-14T12:00:00+00:00"),
        ("002", piece, "2026-11-14T11:00:00+00:00", None),
        ("003", piece, "2026-11-15T09:00:00+00:00", None),
        ("004", boite, "2026-11-15T10:00:00+00:00", "2026-11-20T10:00:00+00:00"),
        ("001", None, "2026-11-15T11:00:00+00:00", None),
        ("002", None, "2026-11-15T12:00:00+00:00", "2026-11-15T13:00:00+00:00"),
        ("003", boite, "2025-11-15T10:00:00+00:00", None),   # hors période
    ):
        _executer("INSERT INTO signalements (id_exemplaire, id_categorie, texte, cree_le, traite_le) "
                  "VALUES (?, ?, ?, ?, ?)", (code, cat, f"{SENTINELLE_TEXTE} par la table 3", cree, traite))
    return catalogue


def test_signalements_comptes_par_categorie(carnet):
    conn = _conn()
    try:
        s = organisation.signalements(conn, services.local_vers_utc_iso("2026-11-14T00:00"),
                                      services.local_vers_utc_iso("2026-11-16T00:00"))
        tout = organisation.signalements(conn)
    finally:
        conn.close()
    assert (s["total"], s["traites"], s["a_traiter"]) == (6, 3, 3)
    assert s["lignes"] == [
        {"nom": "Pièce manquante", "total": 3, "traites": 1, "a_traiter": 2},
        {"nom": "Boîte abîmée", "total": 1, "traites": 1, "a_traiter": 0},
        {"nom": organisation.NON_CLASSE, "total": 2, "traites": 1, "a_traiter": 1},
    ]
    assert tout["total"] == 7           # la période décide par la date d'ouverture


def test_non_classe_toujours_en_dernier(bases):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('X', 'X')")
    _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('X1', 'X')")
    conn = _conn()
    try:
        cat = services.creer_categorie_signalement(conn, "Rare")
        conn.commit()
    finally:
        conn.close()
    for c in (None, None, None, cat):
        _executer("INSERT INTO signalements (id_exemplaire, id_categorie, cree_le) "
                  "VALUES ('X1', ?, '2026-11-14T10:00:00+00:00')", (c,))
    conn = _conn()
    try:
        noms = [l["nom"] for l in organisation.signalements(conn)["lignes"]]
    finally:
        conn.close()
    assert noms == ["Rare", organisation.NON_CLASSE]


def test_signalements_rendus_sans_texte_ni_code(carnet, benevole):
    html = benevole.get(EDITION).text
    section = _section(html)
    assert f"<h3>{_e(organisation.TITRE_SIGNALEMENTS)}</h3>" in section
    assert _e(organisation.phrase_signalements(6, 3, 3)) in section
    assert '<tr><th scope="row">Pièce manquante</th><td>3</td><td>1</td><td>2</td></tr>' in section
    assert f'<tr><th scope="row">{organisation.NON_CLASSE}</th><td>2</td><td>1</td><td>1</td></tr>' in section
    assert SENTINELLE_TEXTE not in html
    assert "table 3" not in html


def test_lien_vers_le_carnet_selon_le_module(carnet, benevole):
    lien = f'href="/maintenance">{_e(organisation.LIEN_CARNET)}'
    assert lien in benevole.get(EDITION).text
    _parametre("module_maintenance", "desactive")
    assert lien not in benevole.get(EDITION).text


def test_aucun_signalement(catalogue, benevole):
    assert _e(organisation.PHRASE_AUCUN_SIGNALEMENT) in _section(benevole.get(EDITION).text)


def test_la_requete_ne_lit_pas_le_texte():
    source = Path("app/organisation.py").read_text(encoding="utf-8")
    requete = source.split("requete = (", 1)[1].split(")\n", 1)[0]
    assert "texte" not in requete and "id_exemplaire" not in requete and "*" not in requete


# ===========================================================================
# 6. Tournois et programme
# ===========================================================================
@pytest.fixture
def tournois(catalogue):
    for nom, etat, quand, equipes, n in (
        ("A", "termine", "2026-11-14T14:00:00+00:00", 0, 4),
        ("B", "termine", "2026-11-14T18:00:00+00:00", 1, 3),
        ("C", "lance", "2026-11-15T11:00:00+00:00", 0, 2),
        ("D", "brouillon", "2026-11-15T15:00:00+00:00", 0, 0),
        ("Sans date", "inscriptions", None, 0, 5),
        ("Autre", "termine", "nimporte quoi", 0, 1),
        ("2025", "termine", "2025-11-15T14:00:00+00:00", 0, 6),
    ):
        tid = _tournoi_sql("INSERT INTO tournois (nom, date_heure, etat, par_equipes, date_creation) "
                           "VALUES (?, ?, ?, ?, '2026-11-01T00:00:00+00:00')", (nom, quand, etat, equipes))
        for k in range(n):
            _tournoi_sql("INSERT INTO inscriptions (id_tournoi, pseudo, membres, code_desinscription, "
                         "date_inscription) VALUES (?, ?, ?, ?, '2026-11-01T00:00:00+00:00')",
                         (tid, f"{SENTINELLE_PSEUDO}-{k}",
                          f'["{SENTINELLE_PSEUDO}-membre"]' if equipes else None, f"c{tid}-{k}"))
    for intitule, etat, quand in (("Atelier", "publie", "2026-11-14T11:00:00+00:00"),
                                  ("Conte", "publie", "2026-11-15T10:00:00+00:00"),
                                  ("Brouillon", "brouillon", "2026-11-15T12:00:00+00:00"),
                                  ("Annulé", "annule", "2026-11-15T13:00:00+00:00"),
                                  ("Sans date", "publie", None)):
        _tournoi_sql("INSERT INTO programme (intitule, date_heure, etat, date_creation) "
                     "VALUES (?, ?, ?, '2026-11-01T00:00:00+00:00')", (intitule, quand, etat))
    return catalogue


def _volet_tournois(html):
    return html.split('<details class="org-volet" id="tournois">', 1)[1].split("</details>", 1)[0]


def test_bilan_tournois_par_etat(tournois):
    conn = tdb.get_connection()
    try:
        b = tournoi_services.bilan_periode(conn, "2026-11-13T23:00:00+00:00", "2026-11-15T23:00:00+00:00")
        p = programme_services.compter_publies(conn, "2026-11-13T23:00:00+00:00", "2026-11-15T23:00:00+00:00")
    finally:
        conn.close()
    assert b == {"par_etat": {"brouillon": 1, "inscriptions": 0, "lance": 1, "termine": 2},
                 "total": 4, "sans_date": 2, "inscrits": 6, "equipes": 3}
    assert p == {"publies": 2, "sans_date": 1}


def test_volet_tournois_rendu_sans_pseudo(tournois, benevole):
    html = benevole.get(EDITION).text
    volet = _volet_tournois(html)
    assert f"<summary><h3>{_e(organisation.TITRE_TOURNOIS)}</h3></summary>" in volet
    assert '<tr><th scope="row">Terminé</th><td>2</td></tr>' in volet
    assert '<tr><th scope="row">Inscriptions ouvertes</th><td>0</td></tr>' in volet
    assert _e(organisation.phrase_inscriptions(6, 3)) in volet
    assert _e(organisation.phrase_tournois_sans_date(2)) in volet
    assert "2 tournois sans date ne sont pas comptés." == organisation.phrase_tournois_sans_date(2)
    assert _e(organisation.phrase_sorties_tournoi(1)) in volet
    assert _e(organisation.phrase_programme(2)) in volet
    assert _e(organisation.phrase_programme_sans_date(1)) in volet
    assert SENTINELLE_PSEUDO not in html


def test_aucun_tournoi_sur_la_periode(tournois, benevole):
    volet = _volet_tournois(benevole.get(
        "/stats?edition=autre&debut=2030-01-01T00:00&fin=2030-01-02T00:00").text)
    assert _e(organisation.PHRASE_AUCUN_TOURNOI) in volet
    assert _e(organisation.phrase_programme(0)) in volet


def test_modules_masques_rien_n_est_calcule(tournois, benevole, monkeypatch):
    appels = []
    monkeypatch.setattr(tournoi_services, "bilan_periode", lambda *a, **k: appels.append("t"))
    monkeypatch.setattr(programme_services, "compter_publies", lambda *a, **k: appels.append("p"))
    monkeypatch.setattr(organisation, "sorties_tournoi", lambda *a, **k: appels.append("s"))
    _parametre("module_tournois", "desactive")
    _parametre("module_programme", "desactive")
    html = benevole.get(EDITION).text
    assert 'id="tournois"' not in html and appels == []


def test_programme_seul(tournois, benevole):
    _parametre("module_tournois", "desactive")
    volet = _volet_tournois(benevole.get(EDITION).text)
    assert "<table" not in volet and "Sorties" not in volet
    assert _e(organisation.phrase_programme(2)) in volet


def test_base_des_tournois_absente(catalogue, benevole, monkeypatch, tmp_path):
    absente = tmp_path / "pas-de-base" / "tournoi.db"
    monkeypatch.setattr(tdb, "get_database_path", lambda: absente)
    r = benevole.get(EDITION)
    assert r.status_code == 200
    assert _e(organisation.PHRASE_BASE_ILLISIBLE) in _volet_tournois(r.text)
    assert not absente.exists()            # une lecture ne crée rien


def test_base_des_tournois_illisible(catalogue, benevole, monkeypatch, tmp_path):
    abimee = tmp_path / "abimee.db"
    abimee.write_bytes(b"ceci n'est pas une base SQLite" * 100)
    monkeypatch.setattr(tdb, "get_database_path", lambda: abimee)
    r = benevole.get(EDITION)
    assert r.status_code == 200
    assert _e(organisation.PHRASE_BASE_ILLISIBLE) in _volet_tournois(r.text)


# ===========================================================================
# 7. Détail, titres, CSS, invariants
# ===========================================================================
def test_detail_replie_avec_son_nombre(catalogue, benevole):
    html = benevole.get(EDITION).text
    assert '<details class="org-volet" id="detail">' in html          # pas d'attribut open
    assert re.search(r'<summary><h3>Détail des prêts <small>\(5\)</small></h3></summary>', html)


def test_volets_dans_l_ordre(catalogue, benevole):
    html = benevole.get(EDITION).text
    assert html.index('id="gouts"') < html.index('id="organisation"') < html.index('id="tournois"') \
        < html.index('id="detail"')


def test_hierarchie_des_titres(tournois, benevole):
    html = benevole.get(EDITION).text
    niveaux = [int(n) for n in re.findall(r"<h([1-6])[ >]", html.split('<main', 1)[1])]
    assert niveaux[0] == 1
    assert all(b - a <= 1 for a, b in zip(niveaux, niveaux[1:]))
    section = _section(html)
    assert re.findall(r"<h2>([^<]*)</h2>", section) == [organisation.TITRE_SECTION]


def test_css_sans_couleur_en_dur():
    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    bloc = css.split("/* Sommaire et section « Organisation »", 1)[1].split(".stats-note {", 1)[0]
    assert not COULEUR_EN_DUR.search(bloc)


def test_les_services_des_statistiques_ignorent_la_base_des_tournois():
    for module in ("organisation", "services", "comptoir", "achats", "gouts", "exports"):
        source = Path(f"app/{module}.py").read_text(encoding="utf-8")
        assert "app.tournoi" not in source and "from app import tournoi" not in source, module


def test_exports_inchanges_et_sans_porte(carnet, tournois, benevole):
    """Le lot ne touche ni l'Excel ni le PDF (lot bilan-7b) : mêmes feuilles,
    et aucune des deux portes n'y entre."""
    from io import BytesIO

    import openpyxl

    from app import achats, exports, gouts

    xlsx = benevole.get(EDITION.replace("/stats?", "/stats/export.xlsx?")).content
    classeur = openpyxl.load_workbook(BytesIO(xlsx))
    assert classeur.sheetnames == ["Synthèse", exports.FEUILLE_PALMARES,
                                   achats.FEUILLE_TOUS_SORTIS, achats.FEUILLE_JAMAIS_SORTIS,
                                   gouts.FEUILLE_GOUTS, gouts.FEUILLE_SANS_CATEGORIE,
                                   exports.FEUILLE_HEURE_PAR_HEURE, "Détail"]
    cellules = [str(c) for f in classeur for ligne in f.iter_rows(values_only=True) for c in ligne]
    assert not any(SENTINELLE_TEXTE in c or SENTINELLE_PSEUDO in c for c in cellules)
    pdf = benevole.get(EDITION.replace("/stats?", "/stats/export.pdf?")).content
    assert pdf[:4] == b"%PDF" and SENTINELLE_TEXTE.encode() not in pdf
