"""
Lot bilan-3 — les éditions de l'événement, datées par le bureau.

Une table de la base de prêt (`editions_evenement`), un service
(`app/editions.py`), une section de « Gestion de l'événement » et le menu
« Période » de `/stats`. Ordre des familles :

1. migration et base d'avant (le comportement d'avant doit tenir) ;
2. conversion en période et cas limites (un jour, heure d'hiver, ordre,
   chevauchement, édition future) ;
3. écran « Événement » : ajout, correction, suppression sans perte de prêt,
   refus sans écriture, préremplissage, journal ;
4. `/stats` : même chiffres qu'avec la paire de dates, repli d'une édition
   inconnue, période par défaut, exports ;
5. libellé de la tombola, sauvegarde et restauration, formation.

Les messages et libellés sont importés, jamais recopiés.
"""

import json
import re
import sqlite3
import zipfile
from datetime import date
from io import BytesIO

import pytest
from markupsafe import escape

from app import editions, exports, journal
from app.routes import admin as routes_admin
from app.routes import stats as routes_stats
from tests.test_tombola import (  # noqa: F401 — fixtures partagées
    _conn, admin, bases, benevole, visiteur,
)

NOM = "Festival du jeu 2026"


def _e(texte):
    """Un message tel que Jinja l'écrit dans la page (apostrophes échappées)."""
    return str(escape(texte))


def _sql(requete, params=()):
    conn = _conn()
    try:
        return [dict(l) for l in conn.execute(requete, params)]
    finally:
        conn.close()


def _executer(requete, params=()):
    conn = _conn()
    try:
        conn.execute(requete, params)
        conn.commit()
    finally:
        conn.close()


def _editions():
    return _sql("SELECT nom, premier_jour, dernier_jour FROM editions_evenement ORDER BY id")


def _creer(nom, premier, dernier):
    conn = _conn()
    try:
        issue, edition = editions.enregistrer(
            conn, {"nom": nom, "premier_jour": premier, "dernier_jour": dernier})
    finally:
        conn.close()
    assert issue == "creee"
    return edition


def _catalogue():
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    for code in ("001", "002", "003"):
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
                  (code,))


def _pret(code, sortie_utc, retour_utc):
    """Un prêt clos, antidaté (aucun numéro : effacé à la clôture, D5)."""
    _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
              "VALUES (?, ?, ?, 'pret')", (code, sortie_utc, retour_utc))


def _journal(chemin):
    if not chemin.exists():
        return []
    lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l]
    return [l for l in lignes if l["action"].startswith("edition_")]


def _chiffres(html):
    """Le bloc des six chiffres de synthèse, sans le libellé de la période."""
    return html.split('<div class="stats-chiffres">', 1)[1].split("</div>\n\n", 1)[0]


def _detail(html):
    return html.split("<h2>Détail des prêts", 1)[1].split("</section>", 1)[0]


# ===========================================================================
# 1. Migration et base d'avant
# ===========================================================================
def test_migration_cree_la_table_sur_une_base_qui_ne_l_a_pas(bases):
    from app import db

    _catalogue()
    _executer("DROP TABLE editions_evenement")
    tables = {l["name"] for l in _sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "editions_evenement" not in tables

    db.init_db()
    db.init_db()  # deux passages ne cassent rien
    tables = {l["name"] for l in _sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "editions_evenement" in tables
    assert _editions() == []
    assert _sql("SELECT COUNT(*) AS n FROM exemplaires")[0]["n"] == 3


def test_base_sans_edition_stats_comme_avant(benevole):
    """Aucune édition : la page montre toute la période, comme avant ce lot."""
    _catalogue()
    _pret("001", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    html = benevole.get("/stats").text
    assert "Période : toutes périodes" in html
    assert _chiffres(html) == _chiffres(benevole.get("/stats?edition=tout").text)
    assert '<span class="chiffre-val">1</span><span class="chiffre-lib">prêts</span>' in html
    # Le menu n'offre que les deux choix sans édition.
    options = re.findall(r'<option value="([^"]+)"', html)
    assert options == ["tout", "autre"]
    # Un ancien lien à dates garde son sens : période libre.
    ancien = benevole.get("/stats?debut=2026-11-15T00:00&fin=2026-11-16T00:00").text
    assert "Période : du 15/11/2026 00:00 au 16/11/2026 00:00" in ancien
    assert '<option value="autre" selected>' in ancien


# ===========================================================================
# 2. Conversion en période et cas limites
# ===========================================================================
def test_periode_un_seul_jour():
    edition = {"nom": NOM, "premier_jour": "2026-11-14", "dernier_jour": "2026-11-14"}
    assert editions.periode_locale(edition) == ("2026-11-14T00:00", "2026-11-15T00:00")
    assert editions.libelle_periode(edition) == f"{NOM}, le 14 novembre 2026"


def test_periode_deux_jours_et_libelles():
    edition = {"nom": NOM, "premier_jour": "2026-11-14", "dernier_jour": "2026-11-15"}
    assert editions.periode_locale(edition) == ("2026-11-14T00:00", "2026-11-16T00:00")
    assert editions.libelle_menu(edition) == f"{NOM} — 14 et 15 novembre"
    assert editions.libelle_periode(edition) == f"{NOM}, du 14 au 15 novembre 2026"
    sans_annee = {**edition, "nom": "Festival du jeu"}
    assert editions.libelle_menu(sans_annee) == "Festival du jeu — 14 et 15 novembre 2026"


def test_passage_a_l_heure_d_hiver_dure_25_heures(bases):
    """Le dernier dimanche d'octobre 2026 (le 25) : 22:00 UTC la veille → 23:00 UTC."""
    from app import services

    edition = {"nom": NOM, "premier_jour": "2026-10-25", "dernier_jour": "2026-10-25"}
    debut, fin = editions.periode_locale(edition)
    assert services.local_vers_utc_iso(debut) == "2026-10-24T22:00:00+00:00"
    assert services.local_vers_utc_iso(fin) == "2026-10-25T23:00:00+00:00"

    _catalogue()
    _pret("001", "2026-10-24T21:59:00+00:00", "2026-10-24T22:30:00+00:00")  # samedi 23:59
    _pret("002", "2026-10-24T22:00:00+00:00", "2026-10-24T23:00:00+00:00")  # dimanche 00:00
    _pret("003", "2026-10-25T22:30:00+00:00", "2026-10-25T23:30:00+00:00")  # dimanche 23:30 (CET)
    conn = _conn()
    try:
        g = services.collecter_stats(conn, "total", services.local_vers_utc_iso(debut),
                                     services.local_vers_utc_iso(fin))["globales"]
    finally:
        conn.close()
    assert g["total_prets"] == 2


def test_premier_jour_apres_le_dernier_refuse():
    _, erreurs = editions.lire_saisie(NOM, "2026-11-15", "2026-11-14")
    assert erreurs == {"dernier_jour": editions.MESSAGE_ORDRE}


def test_saisies_completees_sans_bloquer():
    valeurs, erreurs = editions.lire_saisie("  ", "2026-11-14", "")
    assert erreurs == {}
    assert valeurs == {"nom": "Édition 2026", "premier_jour": "2026-11-14",
                       "dernier_jour": "2026-11-14"}
    valeurs, _ = editions.lire_saisie("x" * 200, "2026-11-14", "")
    assert len(valeurs["nom"]) == editions.LONGUEUR_NOM


@pytest.mark.parametrize("premier, dernier, champ, message", [
    ("", "", "premier_jour", editions.MESSAGE_PREMIER_VIDE),
    ("2026-13-40", "", "premier_jour", editions.MESSAGE_DATE_INVALIDE),
    ("2026-11-14", "demain", "dernier_jour", editions.MESSAGE_DATE_INVALIDE),
])
def test_dates_invalides(premier, dernier, champ, message):
    _, erreurs = editions.lire_saisie(NOM, premier, dernier)
    assert erreurs[champ] == message


def test_chevauchement_refuse_et_rien_ecrit(bases):
    _creer(NOM, "2026-11-14", "2026-11-15")
    conn = _conn()
    try:
        for premier, dernier in [("2026-11-15", "2026-11-16"), ("2026-11-10", "2026-11-14"),
                                 ("2026-11-01", "2026-11-30")]:
            issue, autre = editions.enregistrer(
                conn, {"nom": "Autre", "premier_jour": premier, "dernier_jour": dernier})
            assert issue == "chevauchement" and autre["nom"] == NOM
        # Jours voisins sans recouvrement : acceptés.
        assert editions.enregistrer(conn, {"nom": "Veille", "premier_jour": "2026-11-13",
                                           "dernier_jour": "2026-11-13"})[0] == "creee"
    finally:
        conn.close()
    assert [e["nom"] for e in _editions()] == [NOM, "Veille"]


def test_corriger_ses_propres_jours_n_est_pas_un_chevauchement(bases):
    edition = _creer(NOM, "2026-11-14", "2026-11-15")
    conn = _conn()
    try:
        issue, apres = editions.enregistrer(
            conn, {"nom": NOM, "premier_jour": "2026-11-14", "dernier_jour": "2026-11-16"},
            edition["id"])
    finally:
        conn.close()
    assert issue == "modifiee" and apres["dernier_jour"] == "2026-11-16"


def test_edition_future_s_affiche_vide(benevole):
    _catalogue()
    _pret("001", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    future = _creer("Festival du jeu 2099", "2099-11-14", "2099-11-15")
    r = benevole.get(f"/stats?edition={future['id']}")
    assert r.status_code == 200
    html = r.text
    assert "Période : Festival du jeu 2099, du 14 au 15 novembre 2099" in html
    assert '<span class="chiffre-val">0</span><span class="chiffre-lib">prêts</span>' in html
    assert "Aucun prêt sur la période." in html


# ===========================================================================
# 3. Écran « Gestion de l'événement »
# ===========================================================================
def test_ecran_sans_edition(admin):
    html = admin.get("/admin/evenement").text
    assert "<h2>Éditions</h2>" in html
    assert "Aucune édition pour l'instant" in html
    assert 'for="edition_premier"' in html and 'id="edition_premier"' in html
    # Rien à préremplir tant que l'événement n'a ni nom ni date.
    assert "Créer l'édition à partir de l'événement" not in html


def test_ajout_puis_liste(admin, _journal_isole):
    r = admin.post("/admin/evenement/editions",
                   data={"nom": NOM, "premier_jour": "2026-11-14", "dernier_jour": "2026-11-15"})
    assert r.status_code == 200
    assert "Édition ajoutée : « Festival du jeu 2026 », 14 et 15 novembre 2026." in r.text
    assert _editions() == [{"nom": NOM, "premier_jour": "2026-11-14",
                            "dernier_jour": "2026-11-15"}]
    assert '<span class="edition-nom">Festival du jeu 2026</span>' in r.text
    lignes = _journal(_journal_isole)
    assert [(l["action"], l["objet"], l["qui"]) for l in lignes] == [
        ("edition_creee", f"{NOM} (2026-11-14 → 2026-11-15)", "admin")]


def test_correction_et_envoi_identique(admin, _journal_isole):
    edition = _creer(NOM, "2026-11-14", "2026-11-15")
    page = admin.get(f"/admin/evenement?edition={edition['id']}").text
    assert f"Corriger l'édition « {NOM} »" in page
    assert f'name="id_edition" value="{edition["id"]}"' in page
    assert 'value="2026-11-15"' in page

    donnees = {"id_edition": str(edition["id"]), "nom": NOM,
               "premier_jour": "2026-11-14", "dernier_jour": "2026-11-16"}
    r = admin.post("/admin/evenement/editions", data=donnees)
    assert "Édition corrigée" in r.text
    r = admin.post("/admin/evenement/editions", data=donnees)  # à l'identique
    assert "Rien à changer" in r.text
    assert [l["action"] for l in _journal(_journal_isole)] == ["edition_modifiee"]
    assert _editions()[0]["dernier_jour"] == "2026-11-16"


@pytest.mark.parametrize("donnees, champ, message, code", [
    ({"nom": NOM, "premier_jour": "", "dernier_jour": ""},
     "edition_premier", editions.MESSAGE_PREMIER_VIDE, 400),
    ({"nom": NOM, "premier_jour": "2026-11-15", "dernier_jour": "2026-11-14"},
     "edition_dernier", editions.MESSAGE_ORDRE, 400),
])
def test_saisie_invalide_rien_ecrit_valeurs_gardees(admin, _journal_isole, donnees, champ,
                                                    message, code):
    r = admin.post("/admin/evenement/editions", data=donnees)
    assert r.status_code == code
    assert _e(message) in r.text
    assert f'id="{champ}-erreur"' in r.text
    assert re.search(rf'id="{champ}"[^>]*aria-invalid="true"', r.text)
    assert f'value="{NOM}"' in r.text
    assert _editions() == []
    assert _journal(_journal_isole) == []


def test_chevauchement_a_l_ecran_propose_de_corriger_l_autre(admin, _journal_isole):
    autre = _creer(NOM, "2026-11-14", "2026-11-15")
    r = admin.post("/admin/evenement/editions",
                   data={"nom": "Doublon", "premier_jour": "2026-11-15", "dernier_jour": ""})
    assert r.status_code == 409
    assert _e(f"croisent ceux de l'édition « {NOM} »") in r.text
    assert f'href="/admin/evenement?edition={autre["id"]}#edition-form"' in r.text
    assert len(_editions()) == 1
    assert _journal(_journal_isole) == []


def test_correction_d_une_edition_disparue(admin):
    r = admin.get("/admin/evenement?edition=999")
    assert _e(routes_admin.MESSAGE_EDITION_DISPARUE) in r.text
    r = admin.post("/admin/evenement/editions",
                   data={"id_edition": "999", "nom": NOM, "premier_jour": "2026-11-14"})
    assert r.status_code == 404
    assert _e(routes_admin.MESSAGE_EDITION_DISPARUE) in r.text
    assert _editions() == []


def test_preremplissage_depuis_l_evenement_sans_rien_ecrire(admin):
    admin.post("/admin/evenement", data={"nom_evenement": NOM, "date_evenement": "2026-11-14"})
    page = admin.get("/admin/evenement").text
    assert "Créer l'édition à partir de l'événement" in page
    r = admin.get("/admin/evenement?depuis_evenement=1")
    assert _e(routes_admin.MESSAGE_EDITION_PREREMPLIE) in r.text
    formulaire = r.text.split('id="edition-form"', 1)[1]
    assert f'value="{NOM}"' in formulaire and 'value="2026-11-14"' in formulaire
    assert _editions() == []


def test_suppression_confirmee_garde_les_prets(admin, _journal_isole):
    _catalogue()
    _pret("001", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    edition = _creer(NOM, "2026-11-14", "2026-11-15")

    page = admin.get(f"/admin/evenement/editions/{edition['id']}/supprimer")
    assert page.status_code == 200
    assert "Aucun prêt n'est supprimé." in page.text
    assert 'name="confirmation" value="oui"' in page.text

    # Case non cochée : rien ne part, la page revient avec un message relié.
    r = admin.post(f"/admin/evenement/editions/{edition['id']}/supprimer")
    assert r.status_code == 400
    assert 'aria-describedby="confirmation-erreur"' in r.text
    assert len(_editions()) == 1

    r = admin.post(f"/admin/evenement/editions/{edition['id']}/supprimer",
                   data={"confirmation": "oui"})
    assert r.status_code == 200
    assert _e("Aucun prêt n'a été supprimé") in r.text
    assert _editions() == []
    assert _sql("SELECT COUNT(*) AS n FROM prets")[0]["n"] == 1
    # Double envoi : rien à retirer, rien au journal.
    r = admin.post(f"/admin/evenement/editions/{edition['id']}/supprimer",
                   data={"confirmation": "oui"})
    assert r.status_code == 404
    assert [l["action"] for l in _journal(_journal_isole)] == ["edition_supprimee"]


def test_routes_d_edition_gardees_par_le_mot_de_passe(visiteur):
    edition = _creer(NOM, "2026-11-14", "2026-11-15")
    supprimer = f"/admin/evenement/editions/{edition['id']}/supprimer"
    reponses = [
        visiteur.get(supprimer, follow_redirects=False),
        visiteur.post(supprimer, data={"confirmation": "oui"}, follow_redirects=False),
        visiteur.post("/admin/evenement/editions",
                      data={"nom": "Intrus", "premier_jour": "2030-01-01"},
                      follow_redirects=False),
    ]
    for r in reponses:
        assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert len(_editions()) == 1


def test_reglages_de_l_evenement_inchanges(admin):
    """Pas de couplage : créer une édition ne touche ni au nom ni au premier jour."""
    admin.post("/admin/evenement", data={"nom_evenement": "Nom réglé",
                                         "date_evenement": "2026-08-15"})
    admin.post("/admin/evenement/editions",
               data={"nom": NOM, "premier_jour": "2026-11-14", "dernier_jour": "2026-11-15"})
    valeurs = {l["cle"]: l["valeur"] for l in _sql(
        "SELECT cle, valeur FROM parametres WHERE cle IN ('evenement_nom', 'evenement_date')")}
    assert valeurs == {"evenement_nom": "Nom réglé", "evenement_date": "2026-08-15"}


def test_actions_au_vocabulaire_et_a_la_conception():
    from pathlib import Path

    conception = Path("docs/conception-journal.md").read_text(encoding="utf-8")
    for action in ("edition_creee", "edition_modifiee", "edition_supprimee"):
        assert action in journal.ACTIONS and action in conception


# ===========================================================================
# 4. /stats — le menu « Période »
# ===========================================================================
@pytest.fixture
def deux_editions(bases):
    """Deux éditions, des prêts dans chacune et un entre les deux."""
    _catalogue()
    _pret("001", "2025-11-15T10:00:00+00:00", "2025-11-15T11:00:00+00:00")
    _pret("002", "2026-03-01T10:00:00+00:00", "2026-03-01T11:00:00+00:00")
    _pret("001", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    _pret("003", "2026-11-15T22:30:00+00:00", "2026-11-15T23:30:00+00:00")  # 23:30 locale
    return (_creer("Festival du jeu 2025", "2025-11-15", "2025-11-16"),
            _creer(NOM, "2026-11-14", "2026-11-15"))


def test_menu_plus_recente_d_abord_puis_tout_et_autre(benevole, deux_editions):
    ancienne, recente = deux_editions
    html = benevole.get("/stats?edition=tout").text
    options = re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', html)
    assert options == [
        (str(recente["id"]), f"{NOM} — 14 et 15 novembre"),
        (str(ancienne["id"]), "Festival du jeu 2025 — 15 et 16 novembre"),
        ("tout", "Toute la période"),
        ("autre", "Autre période…"),
    ]
    assert '<label for="edition">Période</label>' in html


def test_edition_memes_chiffres_que_la_paire_de_dates(benevole, deux_editions):
    _, recente = deux_editions
    par_edition = benevole.get(f"/stats?edition={recente['id']}").text
    par_dates = benevole.get(
        "/stats?edition=autre&debut=2026-11-14T00:00&fin=2026-11-16T00:00").text
    assert _chiffres(par_edition) == _chiffres(par_dates)
    assert _detail(par_edition) == _detail(par_dates)
    assert '<span class="chiffre-val">2</span><span class="chiffre-lib">prêts</span>' in par_edition
    assert f"Période : {NOM}, du 14 au 15 novembre 2026" in par_edition


def test_edition_l_emporte_sur_les_dates(benevole, deux_editions):
    ancienne, _ = deux_editions
    html = benevole.get(f"/stats?edition={ancienne['id']}"
                        "&debut=2026-01-01T00:00&fin=2027-01-01T00:00").text
    assert "Période : Festival du jeu 2025, du 15 au 16 novembre 2025" in html
    assert '<span class="chiffre-val">1</span><span class="chiffre-lib">prêts</span>' in html


@pytest.mark.parametrize("valeur", ["999", "abc", "-1", "1 OR 1=1", "%00"])
def test_edition_inconnue_retombe_sur_toute_la_periode(benevole, deux_editions, valeur):
    r = benevole.get("/stats", params={"edition": valeur})
    assert r.status_code == 200
    assert _e(routes_stats.MESSAGE_EDITION_INCONNUE) in r.text
    assert "Période : toutes périodes" in r.text
    assert '<span class="chiffre-val">4</span><span class="chiffre-lib">prêts</span>' in r.text
    assert '<option value="tout" selected>' in r.text


def test_edition_supprimee_retombe_proprement(benevole, admin, deux_editions):
    _, recente = deux_editions
    admin.post(f"/admin/evenement/editions/{recente['id']}/supprimer",
               data={"confirmation": "oui"})
    r = benevole.get(f"/stats?edition={recente['id']}")
    assert r.status_code == 200 and _e(routes_stats.MESSAGE_EDITION_INCONNUE) in r.text


def test_periode_par_defaut(bases):
    ancienne = _creer("Festival du jeu 2025", "2025-11-15", "2025-11-16")
    recente = _creer(NOM, "2026-11-14", "2026-11-15")
    conn = _conn()
    try:
        choisir = routes_stats._choisir_periode
        assert choisir(conn, None, None, None, date(2026, 11, 13))["edition"]["id"] == ancienne["id"]
        assert choisir(conn, None, None, None, date(2026, 11, 14))["edition"]["id"] == recente["id"]
        assert choisir(conn, None, None, None, date(2027, 6, 1))["edition"]["id"] == recente["id"]
        assert choisir(conn, None, None, None, date(2025, 1, 1))["choix"] == "tout"
        assert choisir(conn, None, "2026-01-01T00:00", None)["choix"] == "autre"
    finally:
        conn.close()


def test_page_sans_parametre_montre_l_edition_par_defaut(benevole, deux_editions):
    _, recente = deux_editions  # premier jour passé à la date d'écriture du test
    html = benevole.get("/stats").text
    assert f'<option value="{recente["id"]}" selected>' in html
    assert f"Période : {NOM}" in html


def test_autre_periode_ouvre_le_volet(benevole, deux_editions):
    html = benevole.get("/stats?edition=autre").text
    assert '<details class="recherche stats-autre-periode" open>' in html
    assert 'name="edition" value="autre"' in html
    fermee = benevole.get("/stats?edition=tout").text
    assert '<details class="recherche stats-autre-periode" open>' not in fermee


def test_liens_portent_l_edition(benevole, deux_editions):
    _, recente = deux_editions
    html = benevole.get(f"/stats?edition={recente['id']}").text
    assert f'/stats/export.xlsx?tri=total&amp;edition={recente["id"]}"' in html
    assert f'<input type="hidden" name="edition" value="{recente["id"]}">' in html
    # La bascule ramène à sa section (lot bilan-5 : « Pour les achats »).
    assert f'/stats?tri=exemplaire&amp;edition={recente["id"]}#achats"' in html
    autre = benevole.get("/stats?edition=autre&debut=2026-11-14T00:00").text
    assert "/stats/export.xlsx?tri=total&amp;edition=autre&amp;debut=2026-11-14T00%3A00" in autre


def test_export_excel_porte_l_edition(benevole, deux_editions):
    from openpyxl import load_workbook

    _, recente = deux_editions
    r = benevole.get(f"/stats/export.xlsx?edition={recente['id']}")
    assert r.status_code == 200
    feuille = load_workbook(BytesIO(r.content)).active
    assert feuille["A2"].value == f"Période : {NOM}, du 14 au 15 novembre 2026"


def test_export_pdf_porte_l_edition(benevole, deux_editions, monkeypatch):
    _, recente = deux_editions
    vu = {}
    original = exports.construire_pdf

    def espion(data, periode, *args, **kwargs):
        vu["periode"], vu["total"] = periode, data["globales"]["total_prets"]
        return original(data, periode, *args, **kwargs)

    monkeypatch.setattr(exports, "construire_pdf", espion)
    r = benevole.get(f"/stats/export.pdf?edition={recente['id']}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert vu == {"periode": f"{NOM}, du 14 au 15 novembre 2026", "total": 2}
    r = benevole.get("/stats/export.pdf?edition=999")
    assert r.status_code == 200 and vu["periode"] == "toutes périodes"


# ===========================================================================
# 5. Tombola, sauvegarde, formation
# ===========================================================================
def test_libelle_des_sessions_de_tombola():
    t = {"par_tirage": [], "par_mode": [], "par_edition": [
        {"ouverte": "a", "close": "b", "nb_tirages": 1, "tires": 1, "remis": 1,
         "delai_median": "—"}] * 2}
    titres = [titre for titre, _, _ in exports.tableaux_tombola(t)]
    assert exports.TITRE_TOMBOLA_SESSIONS in titres
    assert "Par édition" not in titres
    assert exports.ENTETES_TOMBOLA_EDITION[0] == "Session ouverte le"


def test_sauvegarde_puis_restauration_garde_les_editions(bases, tmp_path):
    from app import sauvegarde

    _creer(NOM, "2026-11-14", "2026-11-15")
    chemin = tmp_path / "sauvegarde.zip"
    chemin.write_bytes(sauvegarde.creer_zip_sauvegarde())
    _executer("DELETE FROM editions_evenement")
    sauvegarde.restaurer_zip_sauvegarde(chemin)
    assert _editions() == [{"nom": NOM, "premier_jour": "2026-11-14",
                            "dernier_jour": "2026-11-15"}]


def test_restauration_d_une_sauvegarde_sans_la_table(bases, tmp_path):
    """Une archive d'avant ce lot se restaure, et la table revient vide."""
    from app import db, sauvegarde

    _executer("DROP TABLE editions_evenement")
    chemin = tmp_path / "ancienne.zip"
    chemin.write_bytes(sauvegarde.creer_zip_sauvegarde())
    with zipfile.ZipFile(chemin) as zf:
        extrait = tmp_path / "verif.db"
        extrait.write_bytes(zf.read("pret-jeux.db"))
    brute = sqlite3.connect(extrait)
    try:
        assert not brute.execute("SELECT 1 FROM sqlite_master WHERE name = "
                                 "'editions_evenement'").fetchone()
    finally:
        brute.close()
    db.init_db()
    _creer(NOM, "2026-11-14", "2026-11-15")
    sauvegarde.restaurer_zip_sauvegarde(chemin)
    assert _editions() == []


def test_formation_garde_les_editions(bases):
    from app import formation

    _creer(NOM, "2026-11-14", "2026-11-15")
    conn = _conn()
    try:
        formation._vider_base_pret(conn)
    finally:
        conn.close()
    assert len(_editions()) == 1


def test_cloture_ne_touche_pas_aux_editions(bases):
    from app import services

    _creer(NOM, "2026-11-14", "2026-11-15")
    conn = _conn()
    try:
        services.cloturer_evenement(conn)
    finally:
        conn.close()
    assert len(_editions()) == 1
