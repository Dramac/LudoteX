"""
Lot bilan-4 — le comptoir heure par heure (`app/comptoir.py`).

Ordre des familles :

1. définitions : chaque sorte de ligne comptée là où elle doit l'être, et
   nulle part ailleurs ;
2. fermetures par la clôture : la vraie clôture et les cas limites ;
3. heures montrées : zéro intérieur, nuit sautée, deux jours, heure d'hiver
   et heure d'été, date illisible, période trop longue ;
4. pic ;
5. géométrie ;
6. rendu de `/stats` ;
7. export Excel, et mêmes chiffres par l'édition et par la paire de dates.

Les libellés et les constantes sont importés, jamais recopiés.
"""

import re
from io import BytesIO

import openpyxl
import pytest
from markupsafe import escape

from app import comptoir, editions, exports, services
from tests.test_tombola import (  # noqa: F401 — fixtures partagées
    _conn, bases, benevole,
)

COULEUR_EN_DUR = re.compile(r"#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(", re.IGNORECASE)


def _e(texte):
    return str(escape(texte))


def _executer(requete, params=()):
    conn = _conn()
    try:
        conn.execute(requete, params)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def base(bases):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    for i in range(1, 21):
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
                  "VALUES (?, 'CATAN')", (f"{i:03d}",))
    return bases


def _ligne(code, sortie, retour=None, motif="pret"):
    _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
              "VALUES (?, ?, ?, ?)", (code, sortie, retour, motif))


def _cpt(debut=None, fin=None):
    conn = _conn()
    try:
        return comptoir.heure_par_heure(conn, debut, fin)
    finally:
        conn.close()


def _lignes(donnees):
    return [(h["court"], h["prets"], h["retours"], h["dehors"]) for h in donnees["heures"]]


def _section(html):
    return html.split('<section class="carte" id="comptoir">', 1)[1].split("</section>", 1)[0]


# Samedi 14 novembre 2026, heure d'hiver : locale = UTC + 1.
def _sam(heure_utc):
    return f"2026-11-14T{heure_utc}+00:00"


# ===========================================================================
# 1. Définitions
# ===========================================================================
def test_chaque_ligne_comptee_la_ou_elle_doit_l_etre(base):
    _ligne("001", _sam("09:10:00"), _sam("11:20:00"))                # rendu
    _ligne("002", _sam("09:30:00"), _sam("13:40:00"), "oubli")       # non scanné
    _ligne("003", _sam("10:00:00"), _sam("10:00:30"), "erreur")      # erreur
    _ligne("004", _sam("10:05:00"), _sam("12:00:00"), "tournoi")     # tournoi
    _ligne("005", _sam("12:15:00"))                                  # encore dehors
    d = _cpt()
    assert _lignes(d) == [
        ("10 h", 2, 0, 2),     # le prêt rendu et l'oubli sortent
        ("11 h", 0, 0, 2),     # heure creuse : montrée à zéro
        ("12 h", 0, 1, 1),     # le retour scanné ; l'oubli reste dehors
        ("13 h", 1, 0, 2),     # le prêt encore dehors sort ; l'oubli toujours là
    ]
    # Le retour de l'oubli (14 h 40) n'est ni un retour ni une heure montrée ;
    # l'erreur et le tournoi ne sont nulle part.
    assert d["total_retours"] == 1 and d["fermetures_cloture"] == 0
    conn = _conn()
    try:
        assert d["total_prets"] == services.stats_globales(conn)["total_prets"] == 3
    finally:
        conn.close()


def test_pret_sorti_avant_la_periode_compte_dehors(base):
    _ligne("001", "2026-11-13T15:00:00+00:00", _sam("10:30:00"))    # sorti la veille
    _ligne("002", _sam("09:00:00"), _sam("09:45:00"))
    debut = services.local_vers_utc_iso("2026-11-14T00:00")
    fin = services.local_vers_utc_iso("2026-11-15T00:00")
    d = _cpt(debut, fin)
    assert _lignes(d) == [("10 h", 1, 1, 1), ("11 h", 0, 1, 0)]
    assert d["total_prets"] == 1          # celui de la veille n'est pas un prêt de la période


# ===========================================================================
# 2. Fermetures par la clôture
# ===========================================================================
def test_la_vraie_cloture_n_est_pas_un_retour(base):
    _ligne("001", _sam("09:00:00"), _sam("10:05:00"))
    for code, heure in (("002", "09:10:00"), ("003", "09:20:00"), ("004", "09:25:00")):
        _ligne(code, _sam(heure))
    _ligne("005", _sam("09:30:00"), motif="tournoi")
    conn = _conn()
    try:
        assert services.cloturer_evenement(conn).prets == 4
        lignes = conn.execute("SELECT date_sortie, date_retour FROM prets").fetchall()
        assert len(comptoir.instants_de_cloture(lignes)) == 1
    finally:
        conn.close()
    d = _cpt()
    assert d["total_retours"] == 1 and d["fermetures_cloture"] == 3
    # La clôture n'ajoute aucune heure : la courbe s'arrête à la dernière
    # heure active, avec les jeux encore dehors à ce moment-là.
    assert _lignes(d) == [("10 h", 4, 0, 4), ("11 h", 0, 1, 3)]


def test_phrase_des_fermetures_sous_le_graphique(base, benevole):
    _ligne("001", _sam("09:00:00"), _sam("10:05:00"))
    _ligne("002", _sam("09:10:00"))
    _ligne("003", _sam("09:20:00"))
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
    finally:
        conn.close()
    html = benevole.get("/stats").text
    assert _e(comptoir.phrase_fermetures(2)) in _section(html)
    assert comptoir.phrase_fermetures(2).startswith(
        "2 jeux rendus à la clôture ne sont pas comptés comme retours")
    assert comptoir.phrase_fermetures(1).startswith(
        "1 jeu rendu à la clôture n’est pas compté comme retour")


def test_sans_fermeture_pas_de_phrase(base, benevole):
    _ligne("001", _sam("09:00:00"), _sam("10:05:00"))
    assert "à la clôture" not in _section(benevole.get("/stats").text)


def test_deux_retours_dans_la_meme_seconde_restent_des_retours(base):
    _ligne("001", _sam("09:00:00"), _sam("13:00:07"))
    _ligne("002", _sam("09:10:00"), _sam("13:00:07"))
    _ligne("003", _sam("09:20:00"))                       # encore dehors : pas une clôture
    d = _cpt()
    assert d["total_retours"] == 2 and d["fermetures_cloture"] == 0


def test_limite_deux_derniers_retours_dans_la_meme_seconde(base):
    # Limite assumée (CLOTURE_MIN_PRETS) : les deux derniers jeux dehors,
    # rendus dans la même seconde, ressemblent trait pour trait à une clôture.
    _ligne("001", _sam("09:00:00"), _sam("13:00:07"))
    _ligne("002", _sam("09:10:00"), _sam("13:00:07"))
    assert _cpt()["fermetures_cloture"] == 2


def test_cloture_d_un_seul_pret_compte_comme_un_retour(base):
    assert comptoir.CLOTURE_MIN_PRETS == 2
    _ligne("001", _sam("09:00:00"))
    conn = _conn()
    try:
        assert services.cloturer_evenement(conn).prets == 1
    finally:
        conn.close()
    d = _cpt()
    assert d["fermetures_cloture"] == 0 and d["total_retours"] == 1


def test_cloture_qui_ne_ferme_rien(base):
    _ligne("001", _sam("09:00:00"), _sam("10:05:00"))
    _ligne("002", _sam("09:10:00"), _sam("10:15:00"))
    avant = _cpt()
    conn = _conn()
    try:
        assert services.cloturer_evenement(conn).prets == 0
    finally:
        conn.close()
    assert _cpt() == avant and avant["fermetures_cloture"] == 0


def test_deux_clotures_dans_la_meme_periode(base):
    # Une fausse manœuvre le samedi midi, puis la vraie clôture le dimanche soir.
    _ligne("001", _sam("09:00:00"), _sam("11:00:00"))
    _ligne("002", _sam("09:30:00"), _sam("11:00:00"))
    _ligne("003", _sam("12:00:00"), "2026-11-15T18:00:00+00:00")
    _ligne("004", _sam("12:10:00"), "2026-11-15T18:00:00+00:00")
    _ligne("005", _sam("12:20:00"), "2026-11-15T18:00:00+00:00", "tournoi")
    _ligne("006", _sam("12:30:00"), _sam("14:00:00"))     # un vrai retour entre les deux
    conn = _conn()
    try:
        lignes = conn.execute("SELECT date_sortie, date_retour FROM prets").fetchall()
    finally:
        conn.close()
    assert comptoir.instants_de_cloture(lignes) == {
        _sam("11:00:00"), "2026-11-15T18:00:00+00:00"}
    d = _cpt()
    assert d["fermetures_cloture"] == 4 and d["total_retours"] == 1


# ===========================================================================
# 3. Heures montrées
# ===========================================================================
def test_nuit_sautee_et_deux_jours(base):
    _ligne("001", _sam("09:00:00"), _sam("09:30:00"))     # sam. 10 h
    _ligne("002", _sam("17:00:00"), _sam("17:40:00"))     # sam. 18 h
    _ligne("003", "2026-11-15T10:00:00+00:00")            # dim. 11 h
    d = _cpt()
    courts = [h["court"] for h in d["heures"]]
    assert courts == [f"{h} h" for h in range(10, 19)] + ["11 h"]
    assert [h["debut_jour"] for h in d["heures"]].count(True) == 2
    assert [(j["libelle"], j["premier"], j["nb"]) for j in d["jours"]] == [
        ("sam. 14/11", 0, 9), ("dim. 15/11", 9, 1)]
    # Deux jours de nom différent : le nom suffit dans la bulle et la tuile ;
    # le tableau porte toujours la date.
    assert d["heures"][-1]["libelle"] == "dim. 11 h"
    assert d["heures"][-1]["libelle_table"] == "dim. 15/11 11 h"
    assert len(comptoir.geometrie(d)["flux"]["separations"]) == 1


def test_soiree_apres_minuit_reste_sur_sa_journee(base):
    # Samedi 22 h et 0 h 30 (dimanche), puis dimanche 10 h : la nuit n'est
    # pas montrée en heures à zéro (HEURE_DEBUT_JOURNEE).
    assert comptoir.HEURE_DEBUT_JOURNEE == 6
    _ligne("001", _sam("21:10:00"), "2026-11-14T23:30:00+00:00")
    _ligne("002", "2026-11-15T09:00:00+00:00")
    d = _cpt()
    assert [(h["libelle"], h["prets"], h["retours"]) for h in d["heures"]] == [
        ("sam. 22 h", 1, 0), ("sam. 23 h", 0, 0), ("dim. 0 h", 0, 1), ("dim. 10 h", 1, 0)]
    assert [(j["libelle"], j["nb"]) for j in d["jours"]] == [("sam. 14/11", 3), ("dim. 15/11", 1)]


def test_meme_jour_de_la_semaine_deux_fois_la_date_s_ajoute(base):
    _ligne("001", _sam("09:00:00"))
    _ligne("002", "2026-11-21T09:00:00+00:00")
    assert [h["libelle"] for h in _cpt()["heures"]] == ["sam. 14/11 10 h", "sam. 21/11 10 h"]


def test_passage_a_l_heure_d_hiver_deux_heures_distinctes(base):
    # Dimanche 25 octobre 2026 : 02:30 heure d'été (00:30 UTC), puis 02:30
    # heure d'hiver (01:30 UTC). Aucune des deux n'avale l'autre.
    _ligne("001", "2026-10-25T00:30:00+00:00")
    _ligne("002", "2026-10-25T01:30:00+00:00")
    _ligne("003", "2026-10-25T01:40:00+00:00")
    d = _cpt()
    assert [(h["court"], h["libelle"], h["prets"]) for h in d["heures"]] == [
        ("2 h", "dim. 2 h (heure d'été)", 1),
        ("2 h bis", "dim. 2 h (heure d'hiver)", 2),
    ]


def test_passage_a_l_heure_d_ete_pas_de_2_h(base):
    # Dimanche 29 mars 2026 : 01:30 (00:30 UTC) puis 03:30 (01:30 UTC). Il
    # n'y a pas de « 2 h » : ce n'est pas une heure à zéro.
    _ligne("001", "2026-03-29T00:30:00+00:00")
    _ligne("002", "2026-03-29T01:30:00+00:00")
    assert [(h["court"], h["prets"]) for h in _cpt()["heures"]] == [("1 h", 1), ("3 h", 1)]


def test_dates_illisibles_ignorees_sans_erreur(base, benevole):
    _ligne("001", "pas une date")
    _ligne("002", "2026-11-14T10:00:00")                  # sans fuseau
    _ligne("003", _sam("09:00:00"), "n/a")                # retour illisible
    _ligne("004", _sam("09:10:00"), _sam("10:30:00"))
    d = _cpt()
    # Le prêt au retour illisible est un prêt, pas un retour, et sort du
    # calcul des jeux dehors (on ne sait pas quand il est revenu).
    assert _lignes(d) == [("10 h", 2, 0, 1), ("11 h", 0, 1, 0)]
    # La page ne tombe pas : la liste détaillée garde ces lignes, durée inconnue.
    r = benevole.get("/stats")
    assert r.status_code == 200 and "pas une date" in r.text


def test_periode_trop_longue_se_degrade_proprement(base, benevole):
    for jour in range(1, comptoir.MAX_JOURS + 2):
        _ligne(f"{jour:03d}", f"2026-06-{jour:02d}T10:00:00+00:00",
               f"2026-06-{jour:02d}T11:00:00+00:00")
    d = _cpt()
    assert d["trop_long"] and d["nb_jours"] == comptoir.MAX_JOURS + 1
    assert comptoir.geometrie(d) is None
    section = _section(benevole.get("/stats").text)
    assert _e(comptoir.phrase_trop_long(comptoir.MAX_JOURS + 1)) in section
    assert "<svg" not in section and "<table" not in section
    classeur = openpyxl.load_workbook(BytesIO(benevole.get("/stats/export.xlsx").content))
    feuille = classeur[exports.FEUILLE_HEURE_PAR_HEURE]
    lignes = [r for r in feuille.iter_rows(values_only=True) if isinstance(r[1], int)]
    assert len(lignes) == len(d["heures"])


def test_periode_vide(base, benevole):
    d = _cpt()
    assert d["heures"] == [] and d["pic"] is None and comptoir.geometrie(d) is None
    html = benevole.get("/stats").text
    assert _e(comptoir.PHRASE_VIDE) in _section(html)
    assert f'—</span><span class="chiffre-lib">{comptoir.LIBELLE_PIC}</span>' in html


def test_edition_future_et_un_seul_pret(base, benevole):
    conn = _conn()
    try:
        _, future = editions.enregistrer(
            conn, {"nom": "Festival 2030", "premier_jour": "2030-11-16",
                   "dernier_jour": "2030-11-17"})
    finally:
        conn.close()
    _ligne("001", _sam("09:00:00"))
    assert _e(comptoir.PHRASE_VIDE) in _section(
        benevole.get(f"/stats?edition={future['id']}").text)
    section = _section(benevole.get("/stats?edition=tout").text)
    assert "<svg" in section and not re.search(r'="[^"]*nan', section, re.IGNORECASE)


# ===========================================================================
# 4. Pic
# ===========================================================================
def test_pic_valeur_heure_et_egalite(base):
    _ligne("001", _sam("09:00:00"), _sam("11:10:00"))     # 10 h
    _ligne("002", _sam("10:00:00"))                       # 11 h : 2 dehors
    _ligne("003", _sam("12:00:00"))                       # 13 h : 2 dehors à nouveau
    d = _cpt()
    assert [h["dehors"] for h in d["heures"]] == [1, 2, 1, 2]
    assert d["pic"] == {"dehors": 2, "libelle": "sam. 11 h", "index": 1}   # la première


def test_tuile_du_pic(base, benevole):
    _ligne("001", _sam("09:00:00"))
    _ligne("002", _sam("09:10:00"))
    html = benevole.get("/stats").text
    assert (f'>2</span><span class="chiffre-lib">{comptoir.LIBELLE_PIC}, sam. 10 h</span>'
            in html)


def test_pointes_de_retours_deux_au_plus(base):
    for i, (s, r) in enumerate([("09:00", "10:10"), ("09:01", "10:20"), ("09:02", "11:10"),
                                ("09:03", "12:10"), ("09:04", "12:20"), ("09:05", "12:30")]):
        _ligne(f"{i + 1:03d}", _sam(f"{s}:00"), _sam(f"{r}:00"))
    d = _cpt()
    assert [d["heures"][i]["retours"] for i in d["pointes_retours"]] == [2, 3]
    assert len(comptoir.geometrie(d)["flux"]["etiquettes"]) == 2


# ===========================================================================
# 5. Géométrie
# ===========================================================================
@pytest.mark.parametrize("maximum, attendu", [
    (0, [0, 1]), (1, [0, 1]), (4, [0, 1, 2, 3, 4]), (7, [0, 2, 4, 6, 8]),
    (62, [0, 20, 40, 60, 80]), (100, [0, 50, 100]), (380, [0, 100, 200, 300, 400]),
])
def test_graduations_rondes_depuis_zero(maximum, attendu):
    assert comptoir.graduations(maximum) == attendu


def test_barres_proportionnelles_axe_a_zero(base):
    for i in range(4):
        _ligne(f"{i + 1:03d}", _sam(f"09:{i:02d}:00"), _sam(f"10:{i:02d}:00"))
    for i in range(2):
        _ligne(f"{i + 5:03d}", _sam(f"11:{i:02d}:00"))
    g = comptoir.geometrie(_cpt())["flux"]
    prets = [b for b in g["barres"] if b["serie"] == "prets"]
    assert [b["hauteur"] for b in prets] == [comptoir.HAUTEUR_FLUX, 0, comptoir.HAUTEUR_FLUX / 2]
    assert all(b["hauteur"] >= 0 and b["y"] >= 0 for b in g["barres"])
    assert all(b["y"] + b["hauteur"] == g["base"] for b in g["barres"])
    assert g["graduations"][0] == {"y": g["base"], "valeur": 0}
    # Les deux graphiques partagent l'échelle horizontale.
    d = comptoir.geometrie(_cpt())["dehors"]
    assert [p["x"] for p in d["points"]] == [h["x"] for h in g["heures"]]


# ===========================================================================
# 6. Rendu de /stats
# ===========================================================================
@pytest.fixture
def week_end(base):
    _ligne("001", _sam("09:00:00"), _sam("10:10:00"))
    _ligne("002", _sam("09:20:00"), _sam("12:10:00"))
    _ligne("003", _sam("11:00:00"))
    _ligne("004", "2026-11-15T10:00:00+00:00", "2026-11-15T11:00:00+00:00")
    return base


def test_rendu_des_graphiques(week_end, benevole):
    html = benevole.get("/stats").text
    section = _section(html)
    d = _cpt()
    n = len(d["heures"])
    flux, dehors = section.split('id="comptoir-dehors-titre"')
    assert f"<h2>{comptoir.TITRE_SECTION}</h2>" in section
    assert comptoir.PHRASE_SENS in section
    # Une bulle par barre (plus le titre du SVG), une par point.
    assert flux.count("<title>") == 2 * n
    assert dehors.count("<title>") == n
    assert section.count('<title id="comptoir-') == 2
    assert section.count('<rect class="graph-survol"') == n
    # Légende en texte, les deux séries nommées.
    legende = section.split('<ul class="graph-legende">', 1)[1].split("</ul>", 1)[0]
    assert comptoir.SERIE_PRETS in legende and comptoir.SERIE_RETOURS in legende
    # Conteneurs focalisables, nommés ; SVG en image qui renvoie au tableau.
    assert section.count('class="graph-defile" tabindex="0" role="region" aria-label="') == 2
    assert section.count('role="img" aria-labelledby=') == 2
    assert section.count(f"« {comptoir.TITRE_TABLEAU} », sous les graphiques") == 2
    # Le tableau, replié, une ligne par heure.
    assert f'<details class="comptoir-tableau">\n      <summary>{comptoir.TITRE_TABLEAU}</summary>' in section
    assert section.count('<th scope="row">') == n
    for entete in comptoir.ENTETES_TABLEAU:
        assert f'<th scope="col">{_e(entete)}</th>' in section
    assert _e(comptoir.NOTE_DEFINITIONS) in section
    # Jamais un nombre sur chaque barre ; aucune couleur en dur ; aucun NaN.
    assert section.count('class="graph-etiquette"') <= 3
    assert not COULEUR_EN_DUR.search(section)
    assert not re.search(r'="[^"]*nan', section, re.IGNORECASE)
    assert "Prêts par heure" not in html and 'class="histo' not in html


def test_feuille_de_style_sans_couleur_en_dur():
    css = open("app/static/css/style.css", encoding="utf-8").read()
    bloc = css.split("/* Le comptoir heure par heure (lot bilan-4", 1)[1]
    bloc = bloc.split("@media (forced-colors: active)", 1)
    regles, forcees = bloc[0], bloc[1].split("\n}\n", 1)[0]
    assert not COULEUR_EN_DUR.search(regles + forcees)
    assert "--primaire-clair" not in regles.split("*/", 1)[1]
    assert ".graph-prets" in forcees and "CanvasText" in forcees


# ===========================================================================
# 7. Export Excel, édition et paire de dates
# ===========================================================================
def _feuille(contenu):
    classeur = openpyxl.load_workbook(BytesIO(contenu))
    return classeur, classeur[exports.FEUILLE_HEURE_PAR_HEURE]


def _rangees(feuille):
    rangees = list(feuille.iter_rows(values_only=True))
    debut = rangees.index(tuple(comptoir.ENTETES_TABLEAU))
    return rangees[debut + 1:]


def _tableau_page(html):
    return [tuple(int(v) if v.isdigit() else v for v in ligne)
            for ligne in re.findall(
                r'<tr><th scope="row">([^<]+)</th><td>(\d+)</td><td>(\d+)</td><td>(\d+)</td></tr>',
                _section(html))]


def test_feuille_excel_memes_lignes_que_le_tableau(week_end, benevole):
    classeur, feuille = _feuille(benevole.get("/stats/export.xlsx").content)
    assert exports.FEUILLE_HEURE_PAR_HEURE in classeur.sheetnames
    assert feuille["A1"].value == comptoir.TITRE_SECTION
    assert feuille["A2"].value == "Période : toutes périodes"
    assert _rangees(feuille) == _tableau_page(benevole.get("/stats").text)
    assert len(_rangees(feuille)) == len(_cpt()["heures"])


def test_feuille_excel_porte_les_fermetures(base, benevole):
    _ligne("001", _sam("09:00:00"), _sam("10:05:00"))
    _ligne("002", _sam("09:10:00"))
    _ligne("003", _sam("09:20:00"))
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
    finally:
        conn.close()
    _, feuille = _feuille(benevole.get("/stats/export.xlsx").content)
    assert feuille["A4"].value == comptoir.phrase_fermetures(2)


def test_feuille_excel_periode_vide(base, benevole):
    _, feuille = _feuille(benevole.get("/stats/export.xlsx").content)
    assert feuille["A4"].value == comptoir.PHRASE_VIDE


def test_edition_memes_chiffres_que_la_paire_de_dates(week_end, benevole):
    _ligne("005", "2026-11-20T10:00:00+00:00")            # hors de l'édition
    conn = _conn()
    try:
        _, edition = editions.enregistrer(
            conn, {"nom": "Festival du jeu 2026", "premier_jour": "2026-11-14",
                   "dernier_jour": "2026-11-15"})
    finally:
        conn.close()
    par_edition = benevole.get(f"/stats?edition={edition['id']}").text
    par_dates = benevole.get(
        "/stats?edition=autre&debut=2026-11-14T00:00&fin=2026-11-16T00:00").text
    assert _section(par_edition) == _section(par_dates)
    assert len(_tableau_page(par_edition)) == len(_cpt(
        services.local_vers_utc_iso("2026-11-14T00:00"),
        services.local_vers_utc_iso("2026-11-16T00:00"))["heures"])
    _, f_edition = _feuille(benevole.get(f"/stats/export.xlsx?edition={edition['id']}").content)
    _, f_dates = _feuille(benevole.get(
        "/stats/export.xlsx?edition=autre&debut=2026-11-14T00:00&fin=2026-11-16T00:00").content)
    assert _rangees(f_edition) == _rangees(f_dates) == _tableau_page(par_edition)
