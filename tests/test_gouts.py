"""
Lot bilan-6 — « Ce qui plaît » (`app/gouts.py`).

Ordre des familles :

1. prêts par jeu, par catégorie : un jeu à deux catégories, le seuil des
   petits groupes, « Autres catégories », « Non renseigné » hors classement,
   la liste vient de la liaison et pas de la copie `titres.categorie` ;
2. tranches de joueurs (chevauchement, cas limites) et d'âge (bornes) ;
3. durées : médianes, oublis et erreurs écartés, prêts de nuit, groupe vide ;
4. une lecture des prêts ; même résultat par édition et par paire de dates ;
5. rendu : section, titres, barres décoratives, `th scope`, cas vides ;
6. export Excel.

Les libellés et les constantes sont importés, jamais recopiés.
"""

import re
from io import BytesIO

import openpyxl
import pytest
from markupsafe import escape

from app import editions, exports, gouts, services
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


def _titre(ref, nom=None, nb=1, categories=None, joueurs=(None, None),
           age=None, duree=None):
    """Un titre, ses `nb` boîtes (<ref>-1, <ref>-2…) et ses catégories, écrites
    par le seul chemin d'écriture de la liaison."""
    _executer("INSERT INTO titres (reference_titre, nom, nb_joueurs_min, "
              "nb_joueurs_max, age_min, duree_min) VALUES (?, ?, ?, ?, ?, ?)",
              (ref, nom or ref.title(), joueurs[0], joueurs[1], age, duree))
    for i in range(1, nb + 1):
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                  (f"{ref}-{i}", ref))
    if categories:
        conn = _conn()
        try:
            services.ecrire_categories_titre(conn, ref, categories)
            conn.commit()
        finally:
            conn.close()


def _pret(code, sortie, retour=None, motif="pret"):
    _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
              "VALUES (?, ?, ?, ?)", (code, sortie, retour, motif))


# Samedi 14 et dimanche 15 novembre 2026, heure d'hiver : locale = UTC + 1.
def _sam(h):
    return f"2026-11-14T{h}+00:00"


def _dim(h):
    return f"2026-11-15T{h}+00:00"


def _prets(code, n, duree_min=30, depart_h=9):
    """n prêts rendus de `duree_min` minutes, le samedi, à la suite."""
    for k in range(n):
        debut = depart_h * 60 + k * (duree_min + 5)
        fin = debut + duree_min
        _pret(code, _sam(f"{debut // 60:02d}:{debut % 60:02d}:00"),
              _sam(f"{fin // 60:02d}:{fin % 60:02d}:00"))


def _calcul(debut=None, fin=None):
    conn = _conn()
    try:
        return gouts.ce_qui_plait(conn, debut, fin)
    finally:
        conn.close()


def _par_nom(lignes):
    return {l["nom"]: (l["nb_titres"], l["nb_prets"], l["par_titre_txt"]) for l in lignes}


def _section(html):
    return html.split('<section class="carte" id="gouts">', 1)[1].split("</section>", 1)[0]


# ===========================================================================
# 1. Prêts par jeu, par catégorie
# ===========================================================================
@pytest.fixture
def rayons(bases):
    """« Cartes » : 6 jeux, 6 prêts (1 par jeu). « Réflexe » : 5 jeux, 20
    prêts (4 par jeu). « Bluff » : 2 jeux, 9 prêts (petite catégorie).
    Un jeu sans catégorie, prêté 3 fois. CARTE0 porte aussi « Réflexe »."""
    for i in range(6):
        _titre(f"CARTE{i}", categories="Cartes, Réflexe" if i == 0 else "Cartes")
        _prets(f"CARTE{i}-1", 1)
    for i in range(1, 5):
        _titre(f"REFLEXE{i}", categories="Réflexe")
        _prets(f"REFLEXE{i}-1", 5)
    _prets("CARTE0-1", 4, depart_h=14)             # CARTE0 : 5 prêts en tout
    _titre("BLUFF1", categories="Bluff")
    _titre("BLUFF2", categories="Bluff")
    _prets("BLUFF1-1", 9)
    _titre("NUE", "Jeu nu")
    _prets("NUE-1", 3)
    return bases


def test_prets_par_jeu_et_jeu_a_deux_categories(rayons):
    c = _calcul()["categories"]
    # CARTE0 compte dans Cartes ET dans Réflexe : 6 + 4 = 10, puis 25.
    assert _par_nom(c["toutes"]) == {
        "Réflexe": (5, 25, "5,0"), "Bluff": (2, 9, "4,5"), "Cartes": (6, 10, "1,7")}
    assert [l["nom"] for l in c["toutes"]] == ["Réflexe", "Bluff", "Cartes"]
    # La somme des catégories dépasse le total : c'est dit sous le tableau.
    assert sum(l["nb_prets"] for l in c["toutes"]) > _calcul()["total_prets"]


def test_seuil_des_petits_groupes_et_autres(rayons):
    c = _calcul()["categories"]
    assert [l["nom"] for l in c["lignes"]] == ["Réflexe", "Cartes"]   # Bluff : 2 jeux
    assert c["nb_petites"] == 1 and c["nb_suite"] == 0
    assert (c["autres"]["nom"], c["autres"]["nb_titres"], c["autres"]["nb_prets"]) == (
        gouts.AUTRES_CATEGORIES, 2, 9)
    assert c["autres"]["barre"] is None
    assert [l["barre"] for l in c["lignes"]] == [100, 33]
    # Le seuil est inclusif : 5 jeux sont classés.
    assert next(l for l in c["toutes"] if l["nom"] == "Réflexe")["petit"] is False
    assert gouts.SEUIL_JEUX == 5


def test_au_dela_de_dix_les_suivantes_rejoignent_autres(bases):
    for k in range(12):
        for i in range(gouts.SEUIL_JEUX):
            _titre(f"C{k:02d}J{i}", categories=f"Genre {k:02d}")
            _prets(f"C{k:02d}J{i}-1", k + 1)
    c = _calcul()["categories"]
    assert len(c["lignes"]) == gouts.LIGNES_PAGE
    assert c["lignes"][0]["nom"] == "Genre 11"
    assert c["nb_suite"] == 2 and c["nb_petites"] == 0
    assert c["autres"]["nb_titres"] == 2 * gouts.SEUIL_JEUX
    assert gouts.phrase_autres(2, 0) == (
        f"« {gouts.AUTRES_CATEGORIES} » réunit les 2 catégories suivantes. Toutes "
        f"sont dans l'export Excel, feuille « {gouts.FEUILLE_GOUTS} ».")
    assert "la catégorie de moins de 5 jeux, trop petite pour être classée" in \
        gouts.phrase_autres(0, 1)


def test_non_renseigne_hors_classement(rayons):
    resultat = _calcul()
    nr = resultat["categories"]["non_renseigne"]
    assert (nr["nom"], nr["nb_titres"], nr["nb_prets"]) == (gouts.NON_RENSEIGNE, 1, 3)
    assert nr["barre"] is None
    assert all(l["nom"] != gouts.NON_RENSEIGNE for l in resultat["categories"]["toutes"])
    total = resultat["total_prets"]
    assert total == 6 + 4 + 20 + 9 + 3
    assert gouts.phrase_non_renseigne(3, total).startswith(
        f"3 prêts sur {total}, soit 7 %, portent sur des jeux sans catégorie")
    assert resultat["sans_categorie"] == [
        {"reference_titre": "NUE", "nom": "Jeu nu", "nb_exemplaires": 1, "nb_prets": 3}]


def test_la_liste_vient_de_la_liaison_pas_de_la_copie(bases):
    _titre("FAUX", categories=None)
    _executer("UPDATE titres SET categorie = 'Cartes' WHERE reference_titre = 'FAUX'")
    c = _calcul()["categories"]
    assert c["aucune"] is True and c["non_renseigne"]["nb_titres"] == 1


def test_jeu_sans_boite_ne_compte_pas(bases):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('VIDE', 'Vide')")
    assert _calcul()["nb_titres"] == 0


def test_tournoi_et_erreur_ne_comptent_pas_l_oubli_si(bases):
    _titre("AZUL", categories="Famille")
    _pret("AZUL-1", _sam("09:00:00"), _sam("10:00:00"), "tournoi")
    _pret("AZUL-1", _sam("10:00:00"), _sam("10:00:30"), "erreur")
    _pret("AZUL-1", _sam("11:00:00"), _sam("15:00:00"), "oubli")
    r = _calcul()
    assert r["total_prets"] == 1
    assert r["categories"]["toutes"][0]["nb_prets"] == 1


# ===========================================================================
# 2. Tranches
# ===========================================================================
@pytest.mark.parametrize("mn, mx, attendu", [
    (1, 4, ["seul", "deux", "trois_quatre"]),
    (2, 2, ["deux", "deux_seulement"]),
    (2, 6, ["deux", "trois_quatre", "cinq"]),
    (3, 4, ["trois_quatre"]),
    (5, 5, ["cinq"]),
    (2, 8, ["deux", "trois_quatre", "cinq", "huit"]),
    (1, 1, ["seul"]),
    (6, 2, ["deux", "trois_quatre", "cinq"]),      # bornes inversées
    (4, None, ["trois_quatre"]),                   # un nombre seul
    (None, 2, ["deux", "deux_seulement"]),
    (2, 99, ["deux", "trois_quatre", "cinq", "huit"]),  # aberrant : gardé
    (0, 4, ["trois_quatre"]),                      # borne impossible ignorée
    (None, None, []),
    (0, 0, []),
    (-3, None, []),
    ("deux", "six", []),
])
def test_groupes_de_joueurs_et_cas_limites(mn, mx, attendu):
    assert gouts.groupes_joueurs(mn, mx) == attendu


def test_toute_fourchette_entre_dans_un_groupe():
    for mn in range(1, 12):
        for mx in range(mn, 30):
            assert gouts.groupes_joueurs(mn, mx), (mn, mx)


@pytest.mark.parametrize("age, tranche", [
    (0, 0), (3, 0), (5, 0), (6, 1), (7, 1), (8, 2), (9, 2), (10, 3), (11, 3),
    (12, 4), (13, 4), (14, 5), (18, 5), (99, 5), (-1, None), (None, None), ("8", None),
])
def test_tranches_d_age_bornes_comprises(age, tranche):
    assert gouts.tranche_age(age) == tranche


def test_libelles_des_tranches():
    assert [nom for _, nom in gouts.TRANCHES_AGE] == [
        "Moins de 6 ans", "6 et 7 ans", "8 et 9 ans", "10 et 11 ans",
        "12 et 13 ans", "14 ans et plus"]
    assert [nom for _, nom in gouts.TRANCHES_DUREE] == [
        "15 min ou moins", "16 à 30 min", "31 à 60 min", "Plus de 60 min"]


@pytest.mark.parametrize("minutes, tranche", [
    (5, 0), (15, 0), (16, 1), (30, 1), (31, 2), (60, 2), (61, 3), (180, 3),
    (0, None), (-5, None), (None, None),
])
def test_tranches_de_duree(minutes, tranche):
    assert gouts.tranche_duree(minutes) == tranche


def test_repartition_joueurs_et_age(bases):
    for i in range(5):
        _titre(f"DUO{i}", joueurs=(2, 2), age=8)
        _prets(f"DUO{i}-1", 2)
    _titre("SOLO", joueurs=(1, 1), age=14)
    _prets("SOLO-1", 4)
    _titre("INCONNU")
    _prets("INCONNU-1", 1)
    r = _calcul()
    joueurs = {l["cle"]: l for l in r["joueurs"]["lignes"]}
    assert (joueurs["deux"]["nb_titres"], joueurs["deux"]["nb_prets"]) == (5, 10)
    assert joueurs["deux"]["barre"] == 100
    assert joueurs["seul"]["petit"] and joueurs["seul"]["barre"] is None
    assert joueurs["huit"]["nb_titres"] == 0 and joueurs["huit"]["par_titre_txt"] == "—"
    assert r["joueurs"]["non_renseigne"]["nb_titres"] == 1
    assert r["joueurs"]["petites"] is True
    age = [(l["nom"], l["nb_titres"]) for l in r["age"]["lignes"]]
    assert age == [("Moins de 6 ans", 0), ("6 et 7 ans", 0), ("8 et 9 ans", 5),
                   ("10 et 11 ans", 0), ("12 et 13 ans", 0), ("14 ans et plus", 1)]
    assert r["age"]["non_renseigne"]["nb_prets"] == 1


# ===========================================================================
# 3. Durées
# ===========================================================================
def _durees():
    return _calcul()["durees"]


@pytest.mark.parametrize("n, attendu", [(0, "—"), (1, "20 min"), (2, "25 min"),
                                        (3, "30 min")])
def test_mediane_zero_un_deux_n_prets(bases, n, attendu):
    _titre("JEU", duree=20)
    for k, minutes in enumerate((20, 30, 40)[:n]):
        _pret("JEU-1", _sam(f"{9 + k:02d}:00:00"), _sam(f"{9 + k:02d}:{minutes:02d}:00"))
    ligne = _durees()["lignes"][1]                          # 16 à 30 min
    assert ligne["reelle_txt"] == attendu
    assert ligne["annoncee_txt"] == ("—" if n == 0 else "20 min")
    assert ligne["nb_mesures"] == n


def test_mediane_resiste_a_un_tres_long_pret(bases):
    _titre("JEU", duree=30)
    for k, minutes in enumerate((30, 35, 40)):
        _pret("JEU-1", _sam(f"{9 + k:02d}:00:00"), _sam(f"{9 + k:02d}:{minutes:02d}:00"))
    _pret("JEU-1", _sam("13:00:00"), _sam("20:00:00"))      # 7 h, même journée
    assert _durees()["lignes"][1]["reelle_txt"] == "37 min"


def test_oublis_erreurs_tournoi_en_cours_ecartes(bases):
    _titre("JEU", duree=45)
    _pret("JEU-1", _sam("09:00:00"), _sam("09:40:00"))
    _pret("JEU-1", _sam("10:00:00"), _sam("16:00:00"), "oubli")
    _pret("JEU-1", _sam("17:00:00"), _sam("17:00:20"), "erreur")
    _pret("JEU-1", _sam("17:10:00"), _sam("18:00:00"), "tournoi")
    _pret("JEU-1", _sam("18:10:00"))                        # en cours
    d = _durees()
    assert d["lignes"][2]["nb_mesures"] == 1 and d["lignes"][2]["reelle_txt"] == "40 min"
    assert d["lignes"][2]["nb_prets"] == 3                  # pret, oubli, en cours
    assert (d["oublis"], d["en_cours"], d["nuit"]) == (1, 1, 0)
    assert gouts.phrase_non_mesures(1, 0, 1, 0) == (
        "Non mesurés : 1 retour non scanné et 1 prêt pas encore rendu.")


def test_pret_de_nuit_ecarte_et_compte(bases):
    _titre("JEU", duree=60)
    _pret("JEU-1", _sam("09:00:00"), _sam("10:00:00"))
    # Sorti samedi 22 h locales, rendu dimanche 10 h : après la fin de journée.
    _pret("JEU-1", _sam("21:00:00"), _dim("09:00:00"))
    # Sorti samedi 23 h locales, rendu à 1 h 30 : la soirée reste sur sa journée.
    _pret("JEU-1", _sam("22:00:00"), _sam("23:59:59"))
    _pret("JEU-1", _sam("22:30:00"), _dim("00:30:00"))
    d = _durees()
    assert d["nuit"] == 1
    assert d["lignes"][2]["nb_mesures"] == 3
    assert gouts.phrase_non_mesures(0, 1, 0, 0) == "Non mesuré : 1 prêt rendu après la nuit."


def test_fermeture_par_la_cloture_non_mesuree(bases, monkeypatch):
    _titre("JEU", nb=3, duree=30)
    _pret("JEU-1", _sam("09:00:00"), _sam("09:40:00"))
    _pret("JEU-2", _sam("15:00:00"))
    _pret("JEU-3", _sam("15:30:00"))
    monkeypatch.setattr(services, "maintenant", lambda: _sam("18:30:00"))
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
        d = services.collecter_stats(conn)["gouts"]["durees"]
    finally:
        conn.close()
    assert d["clotures"] == 2 and d["nb_mesures"] == 1
    assert d["lignes"][1]["reelle_txt"] == "40 min"
    assert gouts.phrase_non_mesures(0, 0, 0, 0, 2) == (
        "Non mesurés : 2 prêts fermés par la clôture.")


def test_dates_illisibles_ecartees_sans_erreur(bases):
    _titre("JEU", duree=60)
    _pret("JEU-1", "pas une date", _sam("10:00:00"))
    _pret("JEU-1", _sam("11:00:00"), _sam("10:00:00"))      # retour avant la sortie
    d = _durees()
    assert d["illisibles"] == 2 and d["nb_mesures"] == 0


def test_duree_non_renseignee_et_ensemble(bases):
    _titre("SANS")
    _titre("COURT", duree=10)
    _pret("SANS-1", _sam("09:00:00"), _sam("09:50:00"))
    _pret("COURT-1", _sam("09:00:00"), _sam("09:20:00"))
    d = _durees()
    assert d["non_renseigne"]["reelle_txt"] == "50 min"
    assert d["non_renseigne"]["annoncee_txt"] == "—"
    assert d["ensemble"]["nom"] == gouts.TOUTES_DUREES
    assert d["ensemble"]["nb_mesures"] == 2 and d["ensemble"]["annoncee_txt"] == "10 min"
    assert [l["reelle_txt"] for l in d["lignes"][1:]] == ["—", "—", "—"]


# ===========================================================================
# 4. Lecture unique, période
# ===========================================================================
def test_lecture_unique_des_prets(rayons):
    conn = _conn()
    requetes = []
    conn.set_trace_callback(requetes.append)
    try:
        gouts.ce_qui_plait(conn, None, None)
    finally:
        conn.close()
    assert len(requetes) == 3
    assert sum("FROM prets" in r for r in requetes) == 1


@pytest.fixture
def week_end(rayons):
    _pret("BLUFF2-1", "2026-11-20T10:00:00+00:00", "2026-11-20T11:00:00+00:00")
    return rayons


def test_edition_memes_resultats_que_la_paire_de_dates(week_end, benevole):
    conn = _conn()
    try:
        _, edition = editions.enregistrer(
            conn, {"nom": "Festival du jeu 2026", "premier_jour": "2026-11-14",
                   "dernier_jour": "2026-11-15"})
    finally:
        conn.close()
    debut = services.local_vers_utc_iso("2026-11-14T00:00")
    fin = services.local_vers_utc_iso("2026-11-16T00:00")
    attendu = _calcul(debut, fin)
    assert attendu["total_prets"] == 42                     # sans le prêt du 20
    assert _calcul()["total_prets"] == 43
    par_edition = _section(benevole.get(f"/stats?edition={edition['id']}").text)
    par_dates = _section(benevole.get(
        "/stats?edition=autre&debut=2026-11-14T00:00&fin=2026-11-16T00:00").text)
    assert par_edition == par_dates


# ===========================================================================
# 5. Rendu
# ===========================================================================
def test_section_titres_barres_et_entetes(rayons, benevole):
    html = benevole.get("/stats?edition=tout").text
    # Après « Pour les achats », avant « Organisation » (lot bilan-7a), qui
    # porte le détail des prêts.
    assert (html.index('id="achats"') < html.index('id="gouts"')
            < html.index('id="organisation"') < html.index('id="detail"'))
    section = _section(html)
    assert re.findall(r"<h2>([^<]*)</h2>", section) == [_e(gouts.TITRE_SECTION)]
    assert re.findall(r"<h3>([^<]*)</h3>", section) == [
        _e(gouts.TITRE_CATEGORIES), _e(gouts.TITRE_JOUEURS), _e(gouts.TITRE_AGE),
        _e(gouts.TITRE_DUREES)]
    # Chaque barre est décorative ; chaque en-tête porte sa portée.
    barres = re.findall(r'<span class="gouts-barre"([^>]*)>', section)
    assert barres and all('aria-hidden="true"' in b for b in barres)
    assert "<th>" not in section
    assert section.count('<th scope="row">') > 0
    for e in gouts.ENTETES_CATEGORIES:
        assert f'<th scope="col">{_e(e)}</th>' in section
    assert 'style="width: 100%"' in section and 'style="width: 33%"' in section
    assert _e(gouts.NOTE_PLUSIEURS_CATEGORIES) in section
    assert _e(gouts.phrase_autres(0, 1)) in section
    assert _e(gouts.phrase_non_renseigne(3, 42)) in section
    assert _e(gouts.NOTE_CATALOGUE) in section and _e(gouts.NOTE_DUREES) in section
    # « Autres » et « Non renseigné » : en fin de tableau, sans barre.
    hors = re.findall(r'<tr class="gouts-hors-classement">(.*?)</tr>', section)
    assert any(gouts.AUTRES_CATEGORIES in h for h in hors)
    assert all("gouts-barre" not in h for h in hors)


def test_rendu_dans_l_ordre_du_service(rayons, benevole):
    section = _section(benevole.get("/stats?edition=tout").text)
    tableau_categories = section.split("<h3>", 2)[1]
    assert tableau_categories.index("Réflexe") < tableau_categories.index("Cartes") \
        < tableau_categories.index(gouts.AUTRES_CATEGORIES) \
        < tableau_categories.index(gouts.NON_RENSEIGNE)


@pytest.mark.parametrize("cas", ["vide", "sans_pret", "sans_categorie"])
def test_cas_vides_ne_bloquent_jamais(bases, benevole, cas):
    if cas != "vide":
        _titre("JEU", joueurs=(0, 0), age=-1, duree=0)
    if cas == "sans_categorie":
        _pret("JEU-1", _sam("21:00:00"), _dim("09:00:00"))
    r = benevole.get("/stats?edition=tout")
    assert r.status_code == 200
    section = _section(r.text)
    attendu = {"vide": gouts.PHRASE_CATALOGUE_VIDE, "sans_pret": gouts.PHRASE_AUCUN_PRET,
               "sans_categorie": gouts.PHRASE_AUCUNE_CATEGORIE}[cas]
    assert _e(attendu) in section
    if cas == "sans_categorie":
        assert _e(gouts.PHRASE_AUCUNE_MESURE) in section
        assert _e(gouts.phrase_non_renseigne(1, 1)) in section
    xlsx = benevole.get("/stats/export.xlsx?edition=tout")
    assert xlsx.status_code == 200


def test_feuille_de_style_sans_couleur_en_dur():
    css = open("app/static/css/style.css", encoding="utf-8").read()
    regles = [l for l in css.splitlines() if "gouts-" in l]
    assert regles and not COULEUR_EN_DUR.search("\n".join(regles))
    assert "primaire-clair" not in "\n".join(regles)
    # Le bloc des couleurs forcées est refermé : la règle qui le suit n'est
    # pas avalée par la media query (trouvé à l'essai : la note perdait sa
    # couleur).
    avant_note = css.split(".stats-note {", 1)[0]
    assert avant_note.count("{") == avant_note.count("}")


# ===========================================================================
# 6. Export Excel
# ===========================================================================
def _classeur(client, requete="/stats/export.xlsx?edition=tout"):
    return openpyxl.load_workbook(BytesIO(client.get(requete).content))


def _rangees(feuille, a_partir=1):
    return [[c.value for c in l] for l in feuille.iter_rows(min_row=a_partir)
            if any(c.value is not None for c in l)]


def test_feuilles_apres_les_achats(rayons, benevole):
    from app import achats
    noms = _classeur(benevole).sheetnames
    assert noms.index(achats.FEUILLE_JAMAIS_SORTIS) + 1 == noms.index(gouts.FEUILLE_GOUTS)
    assert noms.index(gouts.FEUILLE_GOUTS) + 1 == noms.index(gouts.FEUILLE_SANS_CATEGORIE)


def test_feuille_ce_qui_plait_en_entier(rayons, benevole):
    rangees = _rangees(_classeur(benevole)[gouts.FEUILLE_GOUTS])
    plat = [tuple(r[:5]) for r in rangees]
    i = plat.index((gouts.ENTETES_CATEGORIES[0], *gouts.ENTETES_XLSX_REPARTITION))
    # Toutes les catégories, petites comprises, sans « Autres ».
    assert rangees[i + 1:i + 5] == [
        ["Réflexe", 5, 25, 5.0, None, None, None],
        ["Bluff", 2, 9, 4.5, "oui", None, None],
        ["Cartes", 6, 10, 1.7, None, None, None],
        [gouts.NON_RENSEIGNE, 1, 3, 3.0, "oui", None, None],
    ]
    assert gouts.AUTRES_CATEGORIES not in str(rangees)
    for entetes in ((gouts.ENTETES_JOUEURS[0], *gouts.ENTETES_XLSX_REPARTITION),
                    (gouts.ENTETES_AGE[0], *gouts.ENTETES_XLSX_REPARTITION)):
        assert entetes in plat
    assert tuple(rangees[[tuple(r) for r in rangees].index(
        gouts.ENTETES_XLSX_DUREES)]) == gouts.ENTETES_XLSX_DUREES
    assert gouts.TOUTES_DUREES in str(rangees)


def test_feuille_sans_categorie(rayons, benevole):
    _titre("PIEGE", "=Jeu piégé", nb=2)                     # jamais prêté
    classeur = _classeur(benevole)
    rangees = _rangees(classeur[gouts.FEUILLE_SANS_CATEGORIE])
    i = [tuple(r) for r in rangees].index(gouts.ENTETES_XLSX_SANS_CATEGORIE)
    assert rangees[i + 1:] == [["Jeu nu", "NUE", 1, 3], ["=Jeu piégé", "PIEGE", 2, 0]]
    assert all(c.data_type != "f" for f in classeur for l in f.iter_rows() for c in l)


def test_feuilles_vides(bases, benevole):
    classeur = _classeur(benevole)
    assert gouts.PHRASE_CATALOGUE_VIDE in str(_rangees(classeur[gouts.FEUILLE_GOUTS]))
    assert gouts.PHRASE_CATALOGUE_VIDE in str(
        _rangees(classeur[gouts.FEUILLE_SANS_CATEGORIE]))


def test_construire_xlsx_sans_la_cle():
    data = {"globales": {"total_prets": 0, "en_cours": 0, "titres_pretes": 0,
                         "nb_titres": 0}, "plus": [], "prets": [], "metrique": "total"}
    classeur = openpyxl.load_workbook(BytesIO(exports.construire_xlsx(data, "toutes périodes")))
    assert gouts.FEUILLE_GOUTS not in classeur.sheetnames
