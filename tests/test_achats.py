"""
Lot bilan-5 — « Pour les achats » (`app/achats.py`) et le palmarès des plus
prêtés jusqu'à 50.

Ordre des familles :

1. tous sortis : chaque cas limite de la définition (un exemplaire, deux qui
   se chevauchent, même seconde, fin de journée, prêt ouvert, sorti avant la
   période, clôture, date illisible, heure d'hiver), les motifs (oubli,
   tournoi, erreur), la durée minimale, le classement ;
2. jamais sortis ;
3. palmarès : 50 au plus, 10 visibles, ex aequo, zéros écartés, bascule ;
4. rendu de `/stats`, cas vides, édition = paire de dates ;
5. export Excel ;
6. PDF inchangé (arbitrage : bilan-7 le refait).

Les libellés et les constantes sont importés, jamais recopiés.
"""

import re
from io import BytesIO

import openpyxl
import pytest
from markupsafe import escape

from app import achats, comptoir, editions, exports, services
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


def _titre(ref, nom, nb, prefixe=None):
    """Un titre et ses `nb` boîtes, codées <prefixe>1, <prefixe>2…"""
    _executer("INSERT INTO titres (reference_titre, nom) VALUES (?, ?)", (ref, nom))
    for i in range(1, nb + 1):
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                  (f"{prefixe or ref[:3]}{i}", ref))


def _ligne(code, sortie, retour=None, motif="pret"):
    _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
              "VALUES (?, ?, ?, ?)", (code, sortie, retour, motif))


# Samedi 14 et dimanche 15 novembre 2026, heure d'hiver : locale = UTC + 1.
def _sam(heure_utc):
    return f"2026-11-14T{heure_utc}+00:00"


def _dim(heure_utc):
    return f"2026-11-15T{heure_utc}+00:00"


def _calcul(debut=None, fin=None):
    conn = _conn()
    try:
        return achats.pour_les_achats(conn, debut, fin,
                                      comptoir.heure_par_heure(conn, debut, fin))
    finally:
        conn.close()


def _durees(resultat):
    """{nom: (total en minutes, plus longue en minutes)}"""
    return {r["nom"]: (round(r["total_s"] / 60), round(r["plus_long_s"] / 60))
            for r in resultat["tous_sortis"]}


def _section(html):
    return html.split('<section class="carte" id="achats">', 1)[1].split("</section>", 1)[0]


@pytest.fixture
def base(bases):
    # AZUL : un jeu à trois boîtes, qui sert à ouvrir des heures d'activité
    # sans jamais être « tous sortis ».
    _titre("AZUL", "Azul", 3)
    return bases


# ===========================================================================
# 1. Tous sortis — définition et cas limites
# ===========================================================================
def test_un_seul_exemplaire_sorti_une_fois(base):
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:10:00"), _sam("11:20:00"))
    r = _calcul()
    assert _durees(r) == {"Splendor": (130, 130)}
    ligne = r["tous_sortis"][0]
    assert ligne["total_txt"] == "2 h 10" and ligne["nb_exemplaires"] == 1
    assert ligne["plus_long_quand"] == "sam. 10:10 → 12:20"      # heure locale


def test_deux_exemplaires_qui_se_chevauchent_a_moitie(base):
    _titre("DIXIT", "Dixit", 2)
    _ligne("DIX1", _sam("09:00:00"), _sam("11:00:00"))
    _ligne("DIX2", _sam("10:00:00"), _sam("12:00:00"))
    assert _durees(_calcul()) == {"Dixit": (60, 60)}


def test_retour_et_pret_a_la_meme_seconde_pas_de_trou(base):
    # La même boîte rendue et reprêtée dans la seconde, et deux boîtes qui se
    # relaient à la seconde : une seule période, pas deux.
    _titre("SKYJO", "Skyjo", 1)
    _ligne("SKY1", _sam("09:00:00"), _sam("10:00:00"))
    _ligne("SKY1", _sam("10:00:00"), _sam("11:00:00"))
    _titre("CODES", "Codenames", 2)
    _ligne("COD1", _sam("09:00:00"), _sam("11:00:00"))
    _ligne("COD2", _sam("09:00:00"), _sam("10:00:00"))
    _ligne("COD2", _sam("10:00:00"), _sam("11:00:00"))
    assert _durees(_calcul()) == {"Skyjo": (120, 120), "Codenames": (120, 120)}


def test_une_boite_reconnue_deux_fois_ne_fait_pas_deux_boites(base):
    # Données incohérentes : deux prêts ouverts sur la même boîte. Ils ne
    # font pas croire que les deux boîtes du jeu sont dehors.
    _titre("DIXIT", "Dixit", 2)
    _ligne("DIX1", _sam("09:00:00"), _sam("11:00:00"))
    _ligne("DIX1", _sam("09:30:00"), _sam("10:30:00"))
    assert _calcul()["tous_sortis"] == []


def test_periode_coupee_par_la_fin_de_journee_puis_reprise(base):
    # Splendor dehors samedi de 22 h à la fermeture (23 h, dernière heure
    # active), puis de nouveau dimanche de 9 h à 10 h 30 : DEUX périodes, la
    # nuit ne compte pas.
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("21:00:00"), _sam("21:59:00"))
    _ligne("SPL1", _dim("08:00:00"), _dim("09:30:00"))
    _ligne("AZU1", _sam("21:10:00"), _sam("21:40:00"))
    r = _calcul()
    assert _durees(r) == {"Splendor": (149, 90)}
    assert r["tous_sortis"][0]["plus_long_quand"] == "dim. 09:00 → 10:30"


def test_une_boite_gardee_la_nuit_ne_compte_pas_le_lendemain(base):
    # Sortie samedi 21 h 30 (locale), rendue dimanche 10 h 30 : on ne sait
    # pas quand elle est vraiment revenue. Elle compte jusqu'à la fermeture
    # du samedi (23 h), pas le dimanche matin.
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("20:30:00"), _dim("09:30:00"))
    _ligne("AZU1", _sam("21:10:00"), _sam("21:40:00"))
    _ligne("AZU1", _dim("08:10:00"), _dim("08:40:00"))
    r = _calcul()
    assert _durees(r) == {"Splendor": (90, 90)}
    assert r["tous_sortis"][0]["plus_long_quand"] == "sam. 21:30 → 23:00"


def test_fin_de_journee_a_six_heures_locales():
    from datetime import datetime
    assert achats.fin_de_journee(datetime.fromisoformat(_sam("20:30:00"))).isoformat() \
        == "2026-11-15T05:00:00+00:00"
    # 0 h 30 locale appartient encore à la journée de la veille.
    assert achats.fin_de_journee(datetime.fromisoformat(_sam("23:30:00"))).isoformat() \
        == "2026-11-15T05:00:00+00:00"
    # Nuit du passage à l'heure d'hiver : 6 h locale = 5 h UTC.
    assert achats.fin_de_journee(datetime.fromisoformat("2026-10-24T20:00:00+00:00")).isoformat() \
        == "2026-10-25T05:00:00+00:00"


def test_soiree_apres_minuit_une_seule_periode(base):
    # Une soirée qui passe minuit reste sur sa journée (comptoir) : pas de
    # coupure à minuit.
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("22:30:00"), _dim("00:20:00"))          # 23 h 30 → 1 h 20
    r = _calcul()
    assert _durees(r) == {"Splendor": (110, 110)}
    assert r["tous_sortis"][0]["plus_long_quand"] == "sam. 23:30 → dim. 01:20"


def test_pret_encore_ouvert_borne_par_la_derniere_heure_et_la_periode(base):
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("10:00:00"))                             # jamais rendu
    _ligne("AZU1", _sam("11:10:00"), _sam("11:20:00"))
    _ligne("AZU2", _sam("12:10:00"), _sam("12:40:00"))
    # Sans borne : jusqu'à la fin de la dernière heure active (13 h UTC).
    assert _durees(_calcul()) == {"Splendor": (180, 180)}
    # Avec une fin de période à 12 h 30 locale (11 h 30 UTC) : jusque-là, et
    # pas jusqu'à la fin de l'heure active.
    fin = services.local_vers_utc_iso("2026-11-14T12:30")
    assert _durees(_calcul(None, fin)) == {"Splendor": (90, 90)}


def test_pret_sorti_avant_le_debut_de_la_periode(base):
    # Période ouverte samedi à 10 h (locale) ; Splendor est sorti à 9 h 30.
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("08:30:00"), _sam("10:30:00"))
    _ligne("AZU1", _sam("09:10:00"), _sam("09:40:00"))
    # Dixit, sorti la veille et rendu samedi : la nuit l'a coupé, il ne compte pas.
    _titre("DIXIT", "Dixit", 1)
    _ligne("DIX1", "2026-11-13T15:00:00+00:00", _sam("10:31:00"))
    debut = services.local_vers_utc_iso("2026-11-14T10:00")
    fin = services.local_vers_utc_iso("2026-11-15T00:00")
    assert _durees(_calcul(debut, fin)) == {"Splendor": (90, 90)}


def test_fermeture_par_la_cloture_ne_compte_pas_la_nuit(base):
    # Deux jeux encore dehors à la fin du samedi, fermés par la VRAIE clôture
    # (aujourd'hui, des jours plus tard) : ils comptent jusqu'à la dernière
    # heure active du samedi, pas jusqu'à la clôture.
    _titre("SPLENDOR", "Splendor", 1)
    _titre("DIXIT", "Dixit", 1)
    _ligne("SPL1", _sam("09:10:00"))
    _ligne("DIX1", _sam("10:00:00"))
    _ligne("AZU1", _sam("11:00:00"), _sam("11:20:00"))
    conn = _conn()
    try:
        assert services.cloturer_evenement(conn).prets == 2
    finally:
        conn.close()
    assert _durees(_calcul()) == {"Splendor": (170, 170), "Dixit": (120, 120)}


def test_dates_illisibles_ignorees_sans_erreur(base, benevole):
    _titre("SPLENDOR", "Splendor", 1)
    _titre("DIXIT", "Dixit", 1)
    _titre("SKYJO", "Skyjo", 1)
    _ligne("SPL1", "pas une date")
    _ligne("DIX1", "2026-11-14T10:00:00")                       # sans fuseau
    _ligne("SKY1", _sam("09:00:00"), "n/a")                     # retour illisible
    _ligne("AZU1", _sam("09:10:00"), _sam("10:30:00"))
    assert _calcul()["tous_sortis"] == []
    r = benevole.get("/stats?edition=tout")
    assert r.status_code == 200 and _e(achats.PHRASE_AUCUN) in _section(r.text)


def test_passage_a_l_heure_d_hiver_calcule_en_utc(base):
    # Dimanche 25 octobre 2026 : sortie à 2 h 30 (heure d'été, 00:30 UTC),
    # retour à 3 h 30 (heure d'hiver, 02:30 UTC). Deux heures réelles, pas
    # une seule comme le dirait l'heure murale.
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", "2026-10-25T00:30:00+00:00", "2026-10-25T02:30:00+00:00")
    assert _durees(_calcul()) == {"Splendor": (120, 120)}


def test_oubli_compte_jusqu_a_sa_decouverte(base):
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:00:00"), _sam("11:00:00"), "oubli")
    _ligne("AZU1", _sam("10:30:00"), _sam("10:50:00"))
    # La découverte de l'oubli n'est pas une activité (comptoir) : sans le
    # prêt d'Azul, la journée finirait à 10 h.
    assert _durees(_calcul()) == {"Splendor": (120, 120)}


def test_tournoi_et_erreur_ne_rendent_pas_une_boite_dehors(base):
    # Deux boîtes : l'une en tournoi, l'autre prêtée. Pas « tous sortis »
    # (arbitrage : le bureau choisit les jeux de tournoi).
    _titre("DIXIT", "Dixit", 2)
    _ligne("DIX1", _sam("09:00:00"), _sam("11:00:00"), "tournoi")
    _ligne("DIX2", _sam("09:00:00"), _sam("11:00:00"))
    # Une boîte seule, sortie en tournoi, puis en erreur de prêt.
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:00:00"), _sam("10:00:00"), "tournoi")
    _ligne("SPL1", _sam("10:10:00"), _sam("10:10:30"), "erreur")
    assert _calcul()["tous_sortis"] == []


def test_duree_minimale_ecarte_et_compte(base, benevole):
    assert achats.DUREE_MINIMALE_S == 15 * 60
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:00:00"), _sam("09:14:00"))
    _titre("DIXIT", "Dixit", 1)
    _ligne("DIX1", _sam("09:00:00"), _sam("09:15:00"))          # pile au seuil : gardé
    r = _calcul()
    assert _durees(r) == {"Dixit": (15, 15)} and r["ecartes_seuil"] == 1
    section = _section(benevole.get("/stats?edition=tout").text)
    assert _e(achats.phrase_seuil(1)) in section
    assert "Splendor" not in section.split("<h3>", 2)[1]       # pas dans le tableau


def test_classement_par_duree_totale_puis_par_nom(base):
    for ref, nom, retour in (("B", "Bohnanza", "10:00:00"), ("A", "Azul mini", "10:00:00"),
                             ("C", "Carcassonne", "11:00:00")):
        _titre(ref, nom, 1, prefixe=ref)
        _ligne(f"{ref}1", _sam("09:00:00"), _sam(retour))
    assert [r["nom"] for r in _calcul()["tous_sortis"]] == ["Carcassonne", "Azul mini", "Bohnanza"]


def test_lecture_unique_des_prets(base):
    # Performance : deux requêtes en tout (titres, prêts), quel que soit le
    # nombre de titres. Jamais une requête par titre.
    for i in range(30):
        _titre(f"T{i:02d}", f"Jeu {i:02d}", 1, prefixe=f"T{i:02d}-")
        _ligne(f"T{i:02d}-1", _sam("09:00:00"), _sam("10:00:00"))
    conn = _conn()
    requetes = []
    try:
        donnees = comptoir.heure_par_heure(conn)
        conn.set_trace_callback(requetes.append)
        resultat = achats.pour_les_achats(conn, None, None, donnees)
    finally:
        conn.close()
    assert len(resultat["tous_sortis"]) == 30 and len(requetes) == 2


# ===========================================================================
# 2. Jamais sortis
# ===========================================================================
def test_jamais_sortis_definition(base):
    _titre("SPLENDOR", "Splendor", 1)
    _titre("DIXIT", "Dixit", 2)
    _titre("SKYJO", "Skyjo", 1)
    _titre("CODES", "Codenames", 1)
    _ligne("SPL1", _sam("09:00:00"), _sam("10:00:00"), "tournoi")   # tournoi seul
    _ligne("DIX1", "2026-06-01T10:00:00+00:00", "2026-06-01T11:00:00+00:00")  # hors période
    _ligne("SKY1", _sam("09:00:00"), _sam("10:00:00"), "oubli")     # sorti au public
    _ligne("COD1", _sam("09:00:00"), _sam("09:00:20"), "erreur")    # erreur : jamais sorti
    debut = services.local_vers_utc_iso("2026-11-14T00:00")
    jamais = {r["nom"]: r for r in _calcul(debut)["jamais_sortis"]}
    assert set(jamais) == {"Azul", "Splendor", "Dixit", "Codenames"}
    assert jamais["Splendor"]["tournoi_seulement"] is True
    assert jamais["Dixit"]["tournoi_seulement"] is False
    assert jamais["Dixit"]["nb_exemplaires"] == 2
    # Toute la période : le prêt de juin compte, Dixit est sorti.
    assert "Dixit" not in {r["nom"] for r in _calcul()["jamais_sortis"]}


def test_jamais_sortis_tries_comme_on_lit(base):
    for ref, nom in (("ILES", "Les Îles"), ("OMBRES", "Les Ombres"), ("ECHOS", "Échos")):
        _titre(ref, nom, 1)
    assert [r["nom"] for r in _calcul()["jamais_sortis"]] == ["Azul", "Échos", "Les Îles", "Les Ombres"]


def test_phrase_seuil_accordee():
    assert "toutes ses boîtes" in achats.phrase_seuil(1)
    assert "toutes leurs boîtes" in achats.phrase_seuil(2)


def test_titre_sans_boite_n_est_ni_jamais_sorti_ni_au_catalogue(base):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('VIDE', 'Sans boîte')")
    r = _calcul()
    assert [j["nom"] for j in r["jamais_sortis"]] == ["Azul"] and r["nb_titres"] == 1


def test_ligne_des_jamais_sortis_et_lien_avec_la_periode(base, benevole):
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("AZU1", _sam("09:00:00"), _sam("10:00:00"))
    conn = _conn()
    try:
        _, edition = editions.enregistrer(
            conn, {"nom": "Festival du jeu 2026", "premier_jour": "2026-11-14",
                   "dernier_jour": "2026-11-15"})
        libelle = editions.libelle_periode(edition)
    finally:
        conn.close()
    section = _section(benevole.get(f"/stats?edition={edition['id']}").text)
    assert _e(achats.phrase_jamais_sortis(1, 2, libelle)) in section
    assert (f'<a class="lien lien-cible" href="/stats/export.xlsx?tri=total&amp;'
            f'edition={edition["id"]}">{_e(achats.LIEN_EXCEL)}</a>') in section
    assert _e(achats.FEUILLE_JAMAIS_SORTIS) in section


def test_jamais_sortis_phrases_des_cas_vides():
    assert achats.phrase_jamais_sortis(0, 0, "toutes périodes") == "Le catalogue ne compte aucun jeu."
    assert "Tous les jeux" in achats.phrase_jamais_sortis(0, 4, "toutes périodes")
    assert "toutes périodes" in achats.phrase_jamais_sortis(3, 4, "toutes périodes")


# ===========================================================================
# 3. Palmarès des plus prêtés
# ===========================================================================
@pytest.fixture
def soixante(bases):
    """60 jeux d'une boîte, prêtés 60, 59… 1 fois — sauf deux ex aequo à 50."""
    for i in range(60):
        ref = f"J{i:02d}"
        _titre(ref, f"Jeu {i:02d}", 1, prefixe=ref)
        n = 50 if i == 11 else 60 - i
        for k in range(n):
            _ligne(f"{ref}1", f"2026-11-14T{k // 60 + 8:02d}:{k % 60:02d}:00+00:00",
                   f"2026-11-14T{k // 60 + 8:02d}:{k % 60:02d}:30+00:00")
    return bases


def test_palmares_50_au_plus_et_ex_aequo(soixante):
    conn = _conn()
    try:
        plus = services.collecter_stats(conn)["plus"]
    finally:
        conn.close()
    assert len(plus) == services.LIMITE_PALMARES == 50
    # Jeu 10 et Jeu 11 ont 50 prêts : même rang, le suivant prend sa place.
    assert [(j["nom"], j["rang"], j["ex_aequo"]) for j in plus[9:13]] == [
        ("Jeu 09", 10, False), ("Jeu 10", 11, True), ("Jeu 11", 11, True), ("Jeu 12", 13, False)]


def test_classer_par_exemplaire():
    jeux = [{"par_exemplaire": 1.5, "nb_prets": 3}, {"par_exemplaire": 1.5, "nb_prets": 6},
            {"par_exemplaire": 1.0, "nb_prets": 9}]
    assert [j["rang"] for j in services.classer(jeux, "exemplaire")] == [1, 1, 3]
    assert [j["rang"] for j in services.classer(jeux, "total")] == [1, 2, 3]


def test_palmares_ecarte_les_jeux_sans_pret(base):
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:00:00"), _sam("10:00:00"))
    conn = _conn()
    try:
        assert [j["nom"] for j in services.collecter_stats(conn)["plus"]] == ["Splendor"]
    finally:
        conn.close()


def test_palmares_dix_visibles_puis_le_volet(soixante, benevole):
    section = _section(benevole.get("/stats?edition=tout").text)
    visible, suite = section.split("<summary>Voir la suite du classement</summary>", 1)
    visible = visible.split("<h3>Les plus prêtés</h3>", 1)[1]
    assert visible.count("<li ") == services.LIMITE_PALMARES_VISIBLE
    assert suite.split("</details>", 1)[0].count("<li ") == 50 - services.LIMITE_PALMARES_VISIBLE
    # Les rangs suivent, ex aequo compris ; deux listes ordonnées.
    assert '<li value="11">' in suite and suite.count('<li value="11">') == 2
    assert '<li value="12">' not in suite and '<li value="13">' in suite
    assert section.count('<ol class="palmares">') == 2
    assert "· ex aequo" in suite


def test_bascule_garde_la_periode(soixante, benevole):
    conn = _conn()
    try:
        _, edition = editions.enregistrer(
            conn, {"nom": "Festival du jeu 2026", "premier_jour": "2026-11-14",
                   "dernier_jour": "2026-11-15"})
    finally:
        conn.close()
    html = benevole.get(f"/stats?edition={edition['id']}&tri=exemplaire").text
    assert f'href="/stats?tri=total&amp;edition={edition["id"]}#achats"' in _section(html)


def test_plus_de_palmares_des_moins_pretes_sur_la_page(soixante, benevole):
    html = benevole.get("/stats?edition=tout").text
    assert "<h2>Les moins prêtés" not in html and "jamais sortis inclus" not in html
    # Le volet PDF garde sa case jusqu'au lot bilan-7.
    assert 'value="moins" checked> Palmarès des moins prêtés' in html


# ===========================================================================
# 4. Rendu de /stats
# ===========================================================================
def test_section_et_hierarchie(base, benevole):
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:10:00"), _sam("11:20:00"))
    section = _section(benevole.get("/stats?edition=tout").text)
    assert section.lstrip().startswith(f"<h2>{_e(achats.TITRE_SECTION)}</h2>")
    assert [t for t in re.findall(r"<h3>([^<]*)</h3>", section)] == [
        _e(achats.TITRE_TOUS_SORTIS), "Les plus prêtés", _e(achats.TITRE_JAMAIS_SORTIS)]
    assert all(f'<th scope="col">{_e(e)}</th>' in section for e in achats.ENTETES_TOUS_SORTIS)
    assert '<tr><th scope="row">Splendor</th><td>1</td><td>2 h 10</td>' in section
    assert _e(achats.PHRASE_TOUS_SORTIS) in section
    assert _e(achats.NOTE_DEFINITIONS) in section
    # Le libellé de la maquette affirmait ce qu'on ne sait pas.
    assert "repartait sans" not in section


def test_vingt_lignes_sur_la_page_et_la_phrase(base, benevole):
    for i in range(achats.LIGNES_PAGE + 3):
        ref = f"T{i:02d}"
        _titre(ref, f"Jeu {i:02d}", 1, prefixe=f"{ref}-")
        _ligne(f"{ref}-1", _sam("09:00:00"), _sam(f"{10 + i // 6:02d}:{i % 6 * 5:02d}:00"))
    section = _section(benevole.get("/stats?edition=tout").text)
    tableau = section.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert tableau.count("<tr>") == achats.LIGNES_PAGE
    assert _e(achats.phrase_suite(achats.LIGNES_PAGE + 3)) in section


@pytest.mark.parametrize("requete", ["/stats?edition=tout", "/stats?edition=autre&debut=2030-01-01T00:00"])
def test_cas_vides_ne_bloquent_jamais(bases, benevole, requete):
    # Catalogue vide, puis période sans rien.
    r = benevole.get(requete)
    assert r.status_code == 200
    section = _section(r.text)
    assert _e(achats.PHRASE_AUCUN) in section
    assert "Aucun prêt sur la période." in section
    assert "Le catalogue ne compte aucun jeu." in section


def test_edition_future(base, benevole):
    _ligne("AZU1", _sam("09:00:00"), _sam("10:00:00"))
    conn = _conn()
    try:
        _, future = editions.enregistrer(
            conn, {"nom": "Festival 2030", "premier_jour": "2030-11-16",
                   "dernier_jour": "2030-11-17"})
        libelle = editions.libelle_periode(future)
    finally:
        conn.close()
    section = _section(benevole.get(f"/stats?edition={future['id']}").text)
    assert _e(achats.PHRASE_AUCUN) in section
    assert _e(achats.phrase_jamais_sortis(1, 1, libelle)) in section


@pytest.fixture
def week_end(base):
    for ref, nom, nb in (("SPLENDOR", "Splendor", 1), ("DIXIT", "Dixit", 2)):
        _titre(ref, nom, nb)
    _ligne("SPL1", _sam("09:10:00"), _sam("11:20:00"))
    _ligne("SPL1", _dim("09:00:00"), _dim("10:00:00"))
    _ligne("DIX1", _sam("09:00:00"), _sam("12:00:00"))
    _ligne("DIX2", _sam("10:00:00"), _sam("11:30:00"))
    _ligne("AZU1", "2026-11-20T10:00:00+00:00", "2026-11-20T12:00:00+00:00")  # hors édition
    _ligne("AZU2", "2026-11-20T10:00:00+00:00", "2026-11-20T12:00:00+00:00")
    _ligne("AZU3", "2026-11-20T10:00:00+00:00", "2026-11-20T12:00:00+00:00")
    return base


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
    assert _durees(attendu) == {"Splendor": (190, 130), "Dixit": (90, 90)}
    assert "Azul" not in _durees(attendu)                     # hors de l'édition
    par_edition = _section(benevole.get(f"/stats?edition={edition['id']}").text)
    par_dates = _section(benevole.get(
        "/stats?edition=autre&debut=2026-11-14T00:00&fin=2026-11-16T00:00").text)
    # Mêmes lignes ; seuls diffèrent le libellé de période et les liens.
    nettoyer = lambda s: re.sub(r'href="[^"]*"|période : [^.]*\.', "", s)  # noqa: E731
    assert nettoyer(par_edition) == nettoyer(par_dates)


def test_feuille_de_style_sans_couleur_en_dur():
    css = open("app/static/css/style.css", encoding="utf-8").read()
    regles = [l for l in css.splitlines()
              if "achats-" in l or "palmares-suite" in l or "lien-cible" in l]
    assert regles and not COULEUR_EN_DUR.search("\n".join(regles))


# ===========================================================================
# 5. Export Excel
# ===========================================================================
def _classeur(client, requete="/stats/export.xlsx?edition=tout"):
    return openpyxl.load_workbook(BytesIO(client.get(requete).content))


def _rangees(feuille, a_partir=1):
    return [[c.value for c in l] for l in feuille.iter_rows(min_row=a_partir)
            if any(c.value is not None for c in l)]


def test_feuilles_et_ordre(week_end, benevole):
    classeur = _classeur(benevole)
    assert classeur.sheetnames == [
        "Synthèse", exports.FEUILLE_PALMARES, achats.FEUILLE_TOUS_SORTIS,
        achats.FEUILLE_JAMAIS_SORTIS, exports.FEUILLE_HEURE_PAR_HEURE, "Détail"]
    assert "Palmarès" not in classeur.sheetnames
    assert all(c.data_type != "f" for f in classeur for l in f.iter_rows() for c in l)


def test_feuille_plus_pretes_50_et_rangs(soixante, benevole):
    feuille = _classeur(benevole)[exports.FEUILLE_PALMARES]
    rangees = _rangees(feuille, 4)
    assert tuple(rangees[0]) == exports.ENTETES_PALMARES
    assert len(rangees) - 1 == services.LIMITE_PALMARES
    assert [r[0] for r in rangees[10:14]] == [10, 11, 11, 13]
    assert "moins" not in str(_rangees(feuille)).lower()


def test_feuille_tous_sortis_toutes_les_lignes(base, benevole):
    for i in range(achats.LIGNES_PAGE + 3):
        ref = f"T{i:02d}"
        _titre(ref, f"=Jeu {i:02d}", 1, prefixe=f"{ref}-")      # nom piégé
        # Des retours à des secondes différentes : rendus tous dans la même
        # seconde, avec plus rien dehors, ils passeraient pour une clôture.
        _ligne(f"{ref}-1", _sam("09:00:00"), _sam(f"11:00:{i:02d}"))
    classeur = _classeur(benevole)
    rangees = _rangees(classeur[achats.FEUILLE_TOUS_SORTIS])
    i = [tuple(r[:len(achats.ENTETES_XLSX_TOUS_SORTIS)]) for r in rangees].index(
        achats.ENTETES_XLSX_TOUS_SORTIS)
    lignes = rangees[i + 1:i + 1 + achats.LIGNES_PAGE + 3]
    assert len(lignes) == achats.LIGNES_PAGE + 3
    assert lignes[-1][:8] == ["=Jeu 00", "T00", 1, "2 h 00", 120, "2 h 00",
                              "14/11/2026 10:00", "14/11/2026 12:00"]
    assert all(c.data_type != "f" for f in classeur for l in f.iter_rows() for c in l)


def test_feuille_jamais_sortis_pour_retrouver_la_boite(base, benevole):
    _executer("INSERT INTO emplacements_rangement (nom) VALUES ('Étagère B')")
    _executer("UPDATE exemplaires SET emplacement_evenement = 'Table 3', emplacement_local_id = "
              "(SELECT id_emplacement FROM emplacements_rangement WHERE nom = 'Étagère B')")
    _executer("UPDATE titres SET categorie = 'Famille, Tactique', date_achat = '2024-03-15'")
    _titre("SPLENDOR", "Splendor", 1)
    _ligne("SPL1", _sam("09:00:00"), _sam("10:00:00"), "tournoi")
    feuille = _classeur(benevole)[achats.FEUILLE_JAMAIS_SORTIS]
    rangees = _rangees(feuille)
    i = [tuple(r) for r in rangees].index(achats.ENTETES_XLSX_JAMAIS_SORTIS)
    assert rangees[i + 1:] == [
        ["Azul", "AZUL", 3, "Famille, Tactique", "15/03/2024", "Étagère B", "Table 3", None],
        ["Splendor", "SPLENDOR", 1, None, None, None, None, "oui"],
    ]


def test_feuilles_vides(bases, benevole):
    classeur = _classeur(benevole)
    assert achats.PHRASE_AUCUN in str(_rangees(classeur[achats.FEUILLE_TOUS_SORTIS]))
    assert "Le catalogue ne compte aucun jeu." in str(
        _rangees(classeur[achats.FEUILLE_JAMAIS_SORTIS]))


def test_excel_meme_periode_que_la_page(week_end, benevole):
    conn = _conn()
    try:
        _, edition = editions.enregistrer(
            conn, {"nom": "Festival du jeu 2026", "premier_jour": "2026-11-14",
                   "dernier_jour": "2026-11-15"})
    finally:
        conn.close()
    feuille = _classeur(benevole, f"/stats/export.xlsx?edition={edition['id']}")[
        achats.FEUILLE_TOUS_SORTIS]
    noms = [r[0] for r in _rangees(feuille)]
    assert "Splendor" in noms and "Azul" not in noms


# ===========================================================================
# 6. PDF : inchangé dans ce lot (bilan-7 le refait)
# ===========================================================================
def test_pdf_garde_ses_deux_palmares_de_quinze(soixante, benevole, monkeypatch):
    assert exports.SECTIONS_PDF == ("synthese", "plus", "moins", "tombola", "detail")
    vus = {}
    original = exports.construire_pdf

    def espion(data, periode, sections, couleur, tombola=None):
        vus.update(data=data, sections=sections)
        return original(data, periode, sections, couleur, tombola=tombola)

    monkeypatch.setattr(exports, "construire_pdf", espion)
    r = benevole.get("/stats/export.pdf?edition=tout")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert vus["sections"] == {"synthese", "plus", "moins"}
    assert len(vus["data"]["moins"]) == services.LIMITE_PALMARES_PDF == 15
    # Le PDF coupe les plus prêtés à 15, comme avant ce lot : la page et
    # l'Excel en portent 50.
    assert len(vus["data"]["plus"]) == 50


def test_pdf_coupe_les_plus_pretes_a_quinze(monkeypatch):
    lignes = []

    class Table:
        def __init__(self, donnees, **_):
            lignes.append(len(donnees) - 1)

        def setStyle(self, _):
            pass

        def wrapOn(self, *a):
            return (0, 0)

    import reportlab.platypus as platypus
    monkeypatch.setattr(platypus, "Table", Table)
    monkeypatch.setattr(platypus.SimpleDocTemplate, "build", lambda self, e: None)
    jeu = {"nom": "Jeu", "nb_prets": 1, "nb_exemplaires": 1, "par_exemplaire": 1.0}
    exports.construire_pdf({"plus": [jeu] * 50, "moins": [jeu] * 50, "metrique": "total"},
                           "tout", {"plus", "moins"})
    assert lignes == [15, 15]
