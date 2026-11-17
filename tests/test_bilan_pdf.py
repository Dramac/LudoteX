"""
Lot bilan-7b — le bilan de l'édition en PDF, la section « Bilan et exports »
et la règle de visibilité des exports.

Ordre des familles :

1. sections du PDF : ordre, « moins » absent, défaut, aucune section cochée ;
2. visibilité : visiteur (`/stats` publique) sans Organisation, tombola ni
   détail, même demandés dans l'URL ; bénévole avec tout ; feuille Excel
   « Organisation » pour un bénévole seulement ;
3. cohérence : chaque chiffre du PDF est celui de la page ;
4. graphique du comptoir : présent dans les limites, phrase au-delà ;
   formes distinctes en noir et blanc ; contrastes des quatre thèmes ;
5. ne jamais bloquer : période vide, base des tournois absente, nom très
   long, caractères hors Latin-1 ; titre en métadonnée, aucun texte sous
   8 pt ;
6. la page : section en fin de page, un seul bouton principal, libellé du
   bouton, cases conditionnelles, sommaire ;
7. durée moyenne sans les fermetures par la clôture.

Les libellés sont importés des modules, jamais recopiés. Le texte du PDF
est lu par `tests/outils_pdf.py`.
"""

import re
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from markupsafe import escape

from app import achats, comptoir, editions, exports, gouts, organisation, services
from app.routes import stats as route_stats
from app.tournoi import db as tdb
from tests.outils_pdf import tailles_police, texte_pdf, titre_pdf
from tests.test_organisation import (  # noqa: F401 — fixtures partagées
    EDITION, SENTINELLE_PSEUDO, SENTINELLE_TEXTE, _conn, _executer, _parametre, _sam,
    admin, bases, benevole, carnet, catalogue, public, tournois, visiteur,
)

PDF = EDITION.replace("/stats?", "/stats/export.pdf?")
# Toutes les sections cochées, période gardée (des `params` remplaceraient
# la chaîne de requête de l'URL).
TOUT = PDF + "".join(f"&sections={s}" for s in exports.SECTIONS_PDF)
XLSX = EDITION.replace("/stats?", "/stats/export.xlsx?")
# Les quatre thèmes du lot bilan-4 : défaut, jaune pâle, presque noir, rouge vif.
THEMES = ("#2a2724", "#fff3b0", "#111111", "#e00000")


def _e(texte):
    return str(escape(texte))


def _sections_vues(client, url, monkeypatch):
    """Les sections et le drapeau `benevole` réellement transmis au PDF."""
    vus = {}
    original = exports.construire_pdf

    def espion(data, periode, sections, couleur, **kw):
        vus.update(sections=set(sections), **kw)
        return original(data, periode, sections, couleur, **kw)

    monkeypatch.setattr(exports, "construire_pdf", espion)
    r = client.get(url)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    return vus, r.content


# ===========================================================================
# 1. Sections
# ===========================================================================
def test_sections_dans_l_ordre_de_la_page():
    assert exports.SECTIONS_PDF == ("synthese", "comptoir", "achats", "gouts",
                                    "organisation", "tombola", "detail")
    assert "moins" not in exports.SECTIONS_PDF and "plus" not in exports.SECTIONS_PDF
    assert exports.SECTIONS_RESERVEES == ("organisation", "tombola", "detail")
    assert exports.SECTIONS_PDF_DEFAUT == tuple(s for s in exports.SECTIONS_PDF
                                                if s != "detail")
    assert set(exports.TITRES_SECTIONS_PDF) == set(exports.SECTIONS_PDF)
    assert exports.TITRES_SECTIONS_PDF["comptoir"] == comptoir.TITRE_SECTION


def test_ordre_des_sections_dans_le_document(catalogue, benevole):
    texte = texte_pdf(benevole.get(TOUT).content)
    titres = ["Synthèse", comptoir.TITRE_SECTION, achats.TITRE_SECTION, gouts.TITRE_SECTION,
              organisation.TITRE_SECTION, organisation.TITRE_DETAIL]
    positions = [texte.index(t) for t in titres]
    assert positions == sorted(positions)


def test_bilan_par_defaut_sans_detail(catalogue, benevole, monkeypatch):
    vus, contenu = _sections_vues(benevole, PDF, monkeypatch)
    assert vus["sections"] == set(exports.SECTIONS_PDF_DEFAUT) and vus["benevole"] is True
    texte = texte_pdf(contenu)
    assert organisation.TITRE_SECTION in texte and organisation.TITRE_DETAIL not in texte


@pytest.mark.parametrize("suite", ["", "&sections=moins", "&sections=plus",
                                   "&sections=inconnue"])
def test_aucune_section_connue_donne_le_bilan_par_defaut(catalogue, benevole, monkeypatch,
                                                         suite):
    """Un formulaire « à la carte » sans case cochée, un ancien lien vers
    « moins » ou « plus » : le bilan par défaut, jamais un PDF vide."""
    vus, _ = _sections_vues(benevole, PDF + suite, monkeypatch)
    assert vus["sections"] == set(exports.SECTIONS_PDF_DEFAUT)


def test_pdf_a_la_carte(catalogue, benevole, monkeypatch):
    vus, contenu = _sections_vues(benevole, PDF + "&sections=comptoir&sections=detail",
                                  monkeypatch)
    assert vus["sections"] == {"comptoir", "detail"}
    texte = texte_pdf(contenu)
    assert comptoir.TITRE_SECTION in texte and organisation.TITRE_DETAIL in texte
    assert "Synthèse" not in texte and achats.TITRE_SECTION not in texte


# ===========================================================================
# 2. Visibilité
# ===========================================================================
def test_visiteur_ni_organisation_ni_tombola_ni_detail(carnet, tournois, public, visiteur,
                                                       monkeypatch):
    """`/stats` rouverte au public : le PDF d'un visiteur ne porte rien de
    réservé, même toutes les cases cochées à la main dans l'URL."""
    appels = []
    monkeypatch.setattr(organisation, "signalements", lambda *a, **k: appels.append(1))
    vus, contenu = _sections_vues(visiteur, TOUT, monkeypatch)
    assert vus["sections"] == {"synthese", "comptoir", "achats", "gouts"}
    assert vus["benevole"] is False and vus["organisation_donnees"] is None
    assert vus["tombola"] is None
    assert appels == []                              # rien n'est calculé
    texte = texte_pdf(contenu)
    for absent in (organisation.TITRE_SECTION, organisation.TITRE_TOMBOLA,
                   organisation.TITRE_TOURNOIS, organisation.TITRE_DETAIL,
                   organisation.TITRE_SIGNALEMENTS, "Erreurs de prêt"):
        assert absent not in texte, absent
    assert SENTINELLE_TEXTE not in texte and SENTINELLE_PSEUDO not in texte


def test_visiteur_seul_ne_force_pas_une_section_reservee(catalogue, public, visiteur,
                                                         monkeypatch):
    vus, _ = _sections_vues(visiteur, PDF + "&sections=detail", monkeypatch)
    assert vus["sections"] == set(exports.SECTIONS_PDF_DEFAUT) - set(exports.SECTIONS_RESERVEES)


def test_benevole_et_admin_ont_tout(carnet, tournois, benevole, admin):
    for client in (benevole, admin):
        texte = texte_pdf(client.get(PDF).content)
        assert organisation.TITRE_SECTION in texte
        assert organisation.TITRE_SIGNALEMENTS in texte and organisation.TITRE_TOURNOIS in texte
        assert organisation.phrase_signalements(6, 3, 3) in texte
        # Tournois A et C (individuels, 4 + 2 inscrits), B (3 équipes).
        assert organisation.phrase_inscriptions(6, 3) in texte
        assert SENTINELLE_TEXTE not in texte and SENTINELLE_PSEUDO not in texte


def _classeur(client, url=XLSX):
    return openpyxl.load_workbook(BytesIO(client.get(url).content))


def test_excel_feuille_organisation_benevole_seulement(carnet, tournois, public, visiteur,
                                                       benevole):
    public_ = _classeur(visiteur)
    assert not set(exports.FEUILLES_RESERVEES) & set(public_.sheetnames)
    cellules = [str(v) for f in public_ for l in f.iter_rows(values_only=True) for v in l if v]
    assert not any("Erreurs de prêt" in c or "Retours non scannés" in c
                   or "Prêts encore ouverts" in c for c in cellules)

    classeur = _classeur(benevole)
    assert classeur.sheetnames == [f for f in exports.FEUILLES_XLSX if f != exports.FEUILLE_TOMBOLA]
    feuille = classeur[exports.FEUILLE_ORGANISATION]
    lignes = [l for l in feuille.iter_rows(values_only=True)]
    valeurs = {l[0]: l[1] for l in lignes if l[0]}
    assert (valeurs["Erreurs de prêt"], valeurs["Retours non scannés"],
            valeurs["Prêts encore ouverts"]) == (2, 1, 1)
    assert valeurs["Pièce manquante"] == 3 and valeurs[organisation.NON_CLASSE] == 2
    assert valeurs["Terminé"] == 2 and valeurs["Total"] == 4
    plat = [str(v) for l in lignes for v in l if v is not None]
    assert organisation.phrase_sorties_tournoi(1) in plat
    assert not any(SENTINELLE_TEXTE in c or SENTINELLE_PSEUDO in c
                   for f in classeur for l in f.iter_rows(values_only=True)
                   for c in map(str, l))


def test_construire_sans_benevole_ignore_les_donnees_reservees(catalogue):
    """Le filet dans les exports eux-mêmes : sans `benevole`, rien de
    réservé ne sort, même passé et demandé."""
    conn = _conn()
    try:
        data = services.collecter_stats(conn)
        org = {"signalements": organisation.signalements(conn), "tournois_programme": None,
               "sorties_tournoi": 0, "periode_finie": True}
    finally:
        conn.close()
    texte = texte_pdf(exports.construire_pdf(data, "tout", set(exports.SECTIONS_PDF),
                                             organisation_donnees=org))
    assert organisation.TITRE_SECTION not in texte and organisation.TITRE_DETAIL not in texte
    classeur = openpyxl.load_workbook(BytesIO(exports.construire_xlsx(
        data, "tout", organisation_donnees=org)))
    assert not set(exports.FEUILLES_RESERVEES) & set(classeur.sheetnames)


# ===========================================================================
# 3. Cohérence : les chiffres du PDF sont ceux de la page
# ===========================================================================
def _cases(html):
    return [v for v in re.findall(r'<span class="chiffre-val"[^>]*>(.*?)</span>', html, re.S)]


def test_memes_chiffres_que_la_page(catalogue, benevole):
    html = benevole.get(EDITION).text
    texte = texte_pdf(benevole.get(PDF).content)
    conn = _conn()
    try:
        data = services.collecter_stats(conn, "total",
                                        services.local_vers_utc_iso("2026-11-14T00:00"),
                                        services.local_vers_utc_iso("2026-11-16T00:00"))
    finally:
        conn.close()
    g, pic = data["globales"], data["comptoir"]["pic"]
    # Synthèse : les quatre cases de la page, dans le même ordre.
    tete = html.split('<div class="stats-chiffres">', 1)[1].split("</div>\n  </div>", 1)[0]
    cases = _cases(tete)[:4]
    assert cases == [str(g["total_prets"]), f"{g['titres_pretes']} / {g['nb_titres']}",
                     str(pic["dehors"]), g["duree_moyenne"]]
    synthese = texte[texte.index("Synthèse"):texte.index(comptoir.TITRE_SECTION)]
    for valeur, libelle in zip(cases, (exports.LIBELLE_PRETS, exports.LIBELLE_JEUX_PRETES,
                                       f"{comptoir.LIBELLE_PIC}, {pic['libelle']}",
                                       exports.LIBELLE_DUREE_MOYENNE)):
        assert f"{valeur} {libelle}" in synthese
    # Le pic, en toutes lettres dans le comptoir.
    assert comptoir.phrase_pic_et_pointes(data["comptoir"]) in texte
    assert f"Pic : {pic['dehors']} " in texte
    # Le nombre de jamais sortis : la phrase de la page, mot pour mot.
    phrase = achats.phrase_jamais_sortis(len(data["achats"]["jamais_sortis"]),
                                         data["achats"]["nb_titres"],
                                         route_stats._periode_txt("2026-11-14T00:00",
                                                                  "2026-11-16T00:00"))
    assert _e(phrase) in html and phrase in texte
    # Un chiffre de chaque section.
    premier = data["plus"][0]
    assert f"{premier['rang']} {premier['nom']} {premier['nb_prets']}" in texte
    ensemble = data["gouts"]["durees"]["ensemble"]
    assert f"{gouts.TOUTES_DUREES} {ensemble['nb_mesures']} {ensemble['annoncee_txt']} " \
           f"{ensemble['reelle_txt']}" in texte
    assert f"Erreurs de prêt {g['erreurs']}" in texte
    assert _e(organisation.phrase_ouverts(g["en_cours"], True)) in html
    assert organisation.phrase_ouverts(g["en_cours"], True) in texte


def test_excel_synthese_memes_quatre_chiffres(catalogue, visiteur, public):
    feuille = _classeur(visiteur)[exports.FEUILLE_SYNTHESE]
    valeurs = {l[0]: l[1] for l in feuille.iter_rows(values_only=True) if l[0]}
    assert valeurs["Prêts au total"] == 5 and valeurs["Jeux différents prêtés"] == 3
    assert any(k.startswith("Jeux dehors au pic, ") for k in valeurs)
    assert services.DEF_DUREE_MOYENNE in valeurs


# ===========================================================================
# 4. Le graphique du comptoir
# ===========================================================================
def test_graphique_present_dans_les_limites(catalogue, benevole):
    texte = texte_pdf(benevole.get(PDF).content)
    assert comptoir.TITRE_FLUX in texte
    assert f"{comptoir.SERIE_PRETS} (plein)" in texte
    assert f"{comptoir.SERIE_RETOURS} (hachuré)" in texte


def test_graphique_absent_au_dela_de_max_jours(bases, benevole):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    for j in range(comptoir.MAX_JOURS + 1):
        jour = f"2026-10-{j + 1:02d}"
        _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
                  "VALUES ('001', ?, ?, 'pret')",
                  (f"{jour}T10:00:00+00:00", f"{jour}T11:00:00+00:00"))
    texte = texte_pdf(benevole.get("/stats/export.pdf?edition=tout").content)
    assert comptoir.phrase_trop_long(comptoir.MAX_JOURS + 1) in texte
    assert comptoir.TITRE_FLUX not in texte


def test_graphique_reprend_la_geometrie_de_la_page(catalogue):
    """Les barres du PDF sont celles de la page, à l'échelle : une par
    barre non nulle, prêts PLEINS, retours à CONTOUR hachuré (lisibles en
    noir et blanc), d'une couleur qui atteint 3:1 sur le blanc."""
    from reportlab.graphics.shapes import Line, Rect

    conn = _conn()
    try:
        donnees = comptoir.heure_par_heure(conn)
    finally:
        conn.close()
    geo = comptoir.geometrie(donnees)["flux"]
    trait = services.nuances_theme(services.COULEUR_ASSOCIATION_DEFAUT)["lisible"]
    dessin = exports.graphique_flux(geo, 400, trait, "#cccccc")
    rects = [f for f in dessin.contents if isinstance(f, Rect)]
    non_nulles = [b for b in geo["barres"] if b["hauteur"] > 0]
    assert len(rects) == len(non_nulles)
    pleines = [r for r in rects if r.fillColor is not None]
    assert len(pleines) == sum(b["serie"] == "prets" for b in non_nulles)
    sx = min(1.0, 400 / geo["largeur"])
    for r, b in zip(rects, non_nulles):
        assert r.width == pytest.approx(b["largeur"] * sx)
        assert r.height == pytest.approx(b["hauteur"] * exports.ECHELLE_HAUTEUR_GRAPHIQUE)
    assert dessin.width <= 400
    assert len([f for f in dessin.contents if isinstance(f, Line)]) > len(geo["graduations"])


def test_hachures_restent_dans_leur_barre():
    for x0, y0, x1, y1 in exports._hachures(10, 20, 6, 30, exports.ECART_HACHURES):
        assert 10 <= x0 <= x1 <= 16 and 20 <= y0 <= y1 <= 50
        assert x1 - x0 == pytest.approx(y1 - y0)            # 45°


@pytest.mark.parametrize("couleur", THEMES)
def test_contrastes_des_quatre_themes(couleur):
    """En-tête de tableau à 4,5:1 sur la couleur d'identité ; barres et
    traits du graphique à 3:1 au moins sur le blanc de la page."""
    nuances = services.nuances_theme(couleur)
    assert services.contraste(services.couleur_texte_sur(couleur), couleur) >= 4.5
    assert services.contraste(nuances["lisible"], "#ffffff") >= 3


@pytest.mark.parametrize("couleur", THEMES)
def test_pdf_se_construit_pour_chaque_theme(catalogue, couleur):
    conn = _conn()
    try:
        data = services.collecter_stats(conn)
    finally:
        conn.close()
    assert exports.construire_pdf(data, "tout", couleur=couleur).startswith(b"%PDF")


# ===========================================================================
# 5. Ne jamais bloquer ; métadonnée et taille du texte
# ===========================================================================
def test_periode_vide_edition_future(bases, benevole):
    conn = _conn()
    try:
        valeurs, erreurs = editions.lire_saisie("Édition future", "2030-06-01", "2030-06-02")
        assert not erreurs
        _, edition = editions.enregistrer(conn, valeurs)
    finally:
        conn.close()
    r = benevole.get(f"/stats/export.pdf?edition={edition['id']}")
    assert r.status_code == 200
    texte = texte_pdf(r.content)
    assert comptoir.PHRASE_VIDE in texte and gouts.PHRASE_CATALOGUE_VIDE in texte
    assert titre_pdf(r.content) == exports.titre_bilan("Édition future", "")


def test_base_des_tournois_absente(catalogue, benevole, monkeypatch, tmp_path):
    absente = tmp_path / "pas-de-base" / "tournoi.db"
    monkeypatch.setattr(tdb, "get_database_path", lambda: absente)
    r = benevole.get(PDF)
    assert r.status_code == 200
    assert organisation.PHRASE_BASE_ILLISIBLE in texte_pdf(r.content)
    assert not absente.exists()


def test_noms_longs_et_caracteres_hors_latin1(catalogue, benevole):
    long = "Les Aventuriers " * 12 + "édition intégrale"
    for ref, nom in (("EMOJI", "Jeu 🎲 des dés 🐉"), ("KANJI", "囲碁 et 将棋"), ("LONG", long),
                     ("BALISE", "<b>Gras & co</b>")):
        _executer("INSERT INTO titres (reference_titre, nom) VALUES (?, ?)", (ref, nom))
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                  (f"{ref}-1", ref))
        _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
                  "VALUES (?, ?, ?, 'pret')", (f"{ref}-1", _sam("12:00"), _sam("13:00")))
    r = benevole.get(TOUT)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    texte = texte_pdf(r.content)
    # Échappé, pas interprété comme une balise (ReportLab pose les chevrons
    # en morceaux à part, que le lecteur sépare d'un espace).
    assert "Gras & co" in texte and "< b >" in texte
    assert "édition intégrale" in texte


def test_titre_et_taille_minimale_du_texte(carnet, tournois, benevole):
    contenu = benevole.get(TOUT).content
    periode = route_stats._periode_txt("2026-11-14T00:00", "2026-11-16T00:00")
    assert titre_pdf(contenu) == exports.titre_bilan(None, periode) == f"Bilan — {periode}"
    tailles = tailles_police(contenu)
    assert tailles and min(tailles) >= 8


def test_titre_d_une_edition(catalogue, benevole):
    conn = _conn()
    try:
        valeurs, _ = editions.lire_saisie("Festival 2026", "2026-11-14", "2026-11-15")
        _, edition = editions.enregistrer(conn, valeurs)
    finally:
        conn.close()
    contenu = benevole.get(f"/stats/export.pdf?edition={edition['id']}").content
    assert titre_pdf(contenu) == f"{exports.TITRE_BILAN_EDITION} — Festival 2026"
    assert editions.libelle_periode(edition) in texte_pdf(contenu)


# ===========================================================================
# 6. La page : « Bilan et exports »
# ===========================================================================
def _bilan(html):
    return html.split('<section class="carte" id="bilan">', 1)[1].split("</section>", 1)[0]


def test_section_en_fin_de_page_et_seul_bouton_principal(catalogue, benevole):
    html = benevole.get(EDITION).text
    assert html.count("bouton-principal") == 1
    bilan = _bilan(html)
    assert f"<h2>{_e(exports.TITRE_SECTION_BILAN)}</h2>" in bilan
    assert 'class="bouton bouton-principal"' in bilan
    # Après Organisation, et plus rien des exports en tête de page.
    assert html.index('id="organisation"') < html.index('id="bilan"')
    tete = html.split('id="comptoir"', 1)[0]
    assert "/stats/export" not in tete and "Export PDF" not in tete


def test_liens_emportent_la_periode(catalogue, benevole):
    bilan = _bilan(benevole.get(EDITION).text)
    qs = "tri=total&amp;edition=autre&amp;debut=2026-11-14T00%3A00&amp;fin=2026-11-16T00%3A00"
    assert f'href="/stats/export.pdf?{qs}"' in bilan
    assert f'href="/stats/export.xlsx?{qs}">{_e(exports.LIBELLE_TOUT_EXCEL)}</a>' in bilan
    assert '<input type="hidden" name="edition" value="autre">' in bilan


def test_libelle_du_bouton_selon_la_periode(catalogue, benevole):
    assert route_stats.LIBELLE_BILAN in _bilan(benevole.get(EDITION).text)
    assert route_stats.LIBELLE_BILAN in _bilan(benevole.get("/stats?edition=tout").text)
    conn = _conn()
    try:
        valeurs, _ = editions.lire_saisie("Festival 2026", "2026-11-14", "2026-11-15")
        _, edition = editions.enregistrer(conn, valeurs)
    finally:
        conn.close()
    bilan = _bilan(benevole.get(f"/stats?edition={edition['id']}").text)
    assert _e(route_stats.LIBELLE_BILAN_EDITION) in bilan


def _cases_pdf(bilan):
    return re.findall(r'name="sections" value="([a-z]+)"( checked)?', bilan)


def test_cases_selon_le_visiteur(catalogue, public, visiteur, benevole):
    assert _cases_pdf(_bilan(visiteur.get(EDITION).text)) == [
        ("synthese", " checked"), ("comptoir", " checked"), ("achats", " checked"),
        ("gouts", " checked")]
    bilan = _bilan(benevole.get(EDITION).text)
    assert _cases_pdf(bilan) == [
        ("synthese", " checked"), ("comptoir", " checked"), ("achats", " checked"),
        ("gouts", " checked"), ("organisation", " checked"), ("detail", "")]
    assert f"<summary>{_e(exports.LIBELLE_A_LA_CARTE)}</summary>" in bilan
    for cle in ("synthese", "organisation", "detail"):
        assert _e(exports.TITRES_SECTIONS_PDF[cle]) in bilan
    assert _e(exports.NOTE_BILAN_ORGANISATION) in bilan
    assert _e(exports.NOTE_BILAN_ORGANISATION) not in _bilan(visiteur.get(EDITION).text)


def test_sommaire_mene_au_bilan(catalogue, public, visiteur):
    nav = visiteur.get(EDITION).text.split('<nav class="stats-sommaire"', 1)[1].split("</nav>", 1)[0]
    assert f'<a class="lien" href="#bilan">{_e(exports.TITRE_SECTION_BILAN)}</a>' in nav


def test_cibles_44_px_et_sans_couleur():
    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    regles = css.split("/* Bilan et exports (lot bilan-7b)", 1)[1].split("*/", 1)[1]
    regles = regles.split(".stats-cases-pdf .case { min-height: 44px; }", 1)[0]
    assert ".stats-a-la-carte > summary { padding-block: 10px; line-height: 24px; }" in regles
    assert not re.search(r"#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(", regles, re.I)


# ===========================================================================
# 7. Durée moyenne sans les fermetures par la clôture
# ===========================================================================
@pytest.fixture
def cloture(bases):
    """Un vrai prêt d'une heure, puis deux prêts fermés par la clôture le
    soir, à la même seconde, quand plus rien d'autre n'est ouvert."""
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    for code in ("001", "002", "003"):
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
                  (code,))
    for code, sortie, retour in (("001", "10:00", "11:00"), ("002", "12:00", "20:00"),
                                 ("003", "13:00", "20:00")):
        _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
                  "VALUES (?, ?, ?, 'pret')", (code, _sam(sortie), _sam(retour)))
    return bases


def test_cloture_hors_de_la_duree_moyenne(cloture):
    conn = _conn()
    try:
        avant = services.stats_globales(conn)                 # sans le comptoir
        data = services.collecter_stats(conn)
    finally:
        conn.close()
    assert data["comptoir"]["instants_cloture"] == {_sam("20:00")}
    assert avant["duree_moyenne"] == services.format_duree((3600 + 8 * 3600 + 7 * 3600) / 3)
    assert data["globales"]["duree_moyenne"] == services.format_duree(3600)
    # Une fermeture par la clôture reste un vrai prêt.
    assert data["globales"]["total_prets"] == avant["total_prets"] == 3


def test_un_vrai_retour_compte_toujours(cloture):
    _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('004', 'CATAN')")
    _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
              "VALUES ('004', ?, ?, 'pret')", (_sam("14:00"), _sam("16:00")))
    conn = _conn()
    try:
        data = services.collecter_stats(conn)
    finally:
        conn.close()
    # Le prêt de 14 h à 16 h était ouvert à 20 h ? Non : rendu à 16 h. La
    # clôture reste reconnue, et la moyenne prend les deux vrais retours.
    assert data["globales"]["duree_moyenne"] == services.format_duree((3600 + 7200) / 2)


def test_meme_duree_moyenne_page_pdf_excel(cloture, benevole):
    attendu = services.format_duree(3600)
    html = benevole.get("/stats?edition=tout").text
    assert f">{attendu}</span><span class=\"chiffre-lib\">durée moyenne<" in html
    assert f'title="{_e(services.DEF_DUREE_MOYENNE)}"' in html
    texte = texte_pdf(benevole.get("/stats/export.pdf?edition=tout").content)
    assert f"{attendu} {exports.LIBELLE_DUREE_MOYENNE}" in texte
    assert services.DEF_DUREE_MOYENNE in texte
    feuille = _classeur(benevole, "/stats/export.xlsx?edition=tout")[exports.FEUILLE_SYNTHESE]
    valeurs = {l[0]: l[1] for l in feuille.iter_rows(values_only=True) if l[0]}
    assert valeurs["Durée moyenne de prêt"] == attendu
