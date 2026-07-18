"""
Tests du module « Tournois » (socle phase 1) : logique métier (base séparée en
mémoire) + routes (public/bénévole) via TestClient.
"""

import sqlite3
from datetime import date

import pytest

from app import services as app_services
from app.tournoi import models, services


# ===========================================================================
# Services — base des tournois en mémoire
# ===========================================================================
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        c.execute(p)
    for s in models.SCHEMA_STATEMENTS:
        c.executescript(s)
    yield c
    c.close()


def test_champ_age(conn):
    tid = services.creer_tournoi(conn, "T", age="10+")
    assert services.get_tournoi(conn, tid)["age"] == "10+"
    # Édition + duplication conservent l'âge.
    services.modifier_tournoi(conn, tid, age="tout public")
    assert services.get_tournoi(conn, tid)["age"] == "tout public"
    new = services.dupliquer_tournoi(conn, tid, None)
    assert services.get_tournoi(conn, new)["age"] == "tout public"


def test_creer_et_get(conn):
    tid = services.creer_tournoi(conn, "Carcassonne du soir", jeu="Carcassonne",
                                 nb_places=8, emplacement="Table 3")
    t = services.get_tournoi(conn, tid)
    assert t["nom"] == "Carcassonne du soir"
    assert t["jeu"] == "Carcassonne"
    assert t["etat"] == "brouillon"
    assert t["nb_places"] == 8


def test_machine_a_etats(conn):
    tid = services.creer_tournoi(conn, "T")
    # brouillon -> inscriptions OK ; brouillon -> termine refusé.
    assert services.changer_etat(conn, tid, "termine") is False
    assert services.changer_etat(conn, tid, "inscriptions") is True
    assert services.get_tournoi(conn, tid)["etat"] == "inscriptions"
    # état inconnu refusé.
    assert services.changer_etat(conn, tid, "n_importe_quoi") is False
    # inscriptions -> termine OK.
    assert services.changer_etat(conn, tid, "termine") is True


def test_inscription_publique_et_etat(conn):
    tid = services.creer_tournoi(conn, "T", nb_places=2)
    # Inscriptions fermées (brouillon) -> refus.
    assert services.inscrire(conn, tid, "Alice")["raison"] == "fermee"
    services.changer_etat(conn, tid, "inscriptions")
    r = services.inscrire(conn, tid, "Alice")
    assert r["ok"] and r["code"]
    assert services.compter_inscriptions(conn, tid) == 1
    # Pseudo vide -> refus.
    assert services.inscrire(conn, tid, "   ")["raison"] == "pseudo_vide"
    # 2e place puis complet.
    assert services.inscrire(conn, tid, "Bob")["ok"]
    assert services.inscrire(conn, tid, "Chloé")["raison"] == "complet"


def test_places_illimitees(conn):
    tid = services.creer_tournoi(conn, "T")  # nb_places None
    services.changer_etat(conn, tid, "inscriptions")
    assert services.places_restantes(conn, services.get_tournoi(conn, tid)) is None
    for nom in ("a", "b", "c"):
        assert services.inscrire(conn, tid, nom)["ok"]


def test_desinscription_par_code(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    code = services.inscrire(conn, tid, "Alice")["code"]
    assert services.desinscrire(conn, "mauvais")["ok"] is False
    res = services.desinscrire(conn, code)
    assert res["ok"] and res["pseudo"] == "Alice"
    assert services.compter_inscriptions(conn, tid) == 0


def test_ajout_manuel_ignore_plafond(conn):
    # Le bénévole peut ajouter au-delà du plafond et même hors état "inscriptions".
    tid = services.creer_tournoi(conn, "T", nb_places=1)
    assert services.ajouter_participant(conn, tid, "Alice")["ok"]
    assert services.ajouter_participant(conn, tid, "Bob")["ok"]
    assert services.compter_inscriptions(conn, tid) == 2


def test_suppression_cascade(conn):
    tid = services.creer_tournoi(conn, "T")
    services.ajouter_participant(conn, tid, "Alice")
    services.supprimer_tournoi(conn, tid)
    assert services.get_tournoi(conn, tid) is None
    # Plus aucune inscription orpheline (cascade FK).
    assert conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0] == 0


# --- High score ---
def _tournoi_lance_high_score(conn, pseudos=("Alice", "Bob", "Chloé")):
    tid = services.creer_tournoi(conn, "HS")
    services.changer_etat(conn, tid, "inscriptions")
    for p in pseudos:
        services.ajouter_participant(conn, tid, p)
    return tid


def test_lancer_refus_sans_participant(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    assert services.lancer_tournoi(conn, tid, "high_score")["raison"] == "sans_participant"


def test_lancer_refus_mode_inconnu_et_etat(conn):
    tid = _tournoi_lance_high_score(conn)
    assert services.lancer_tournoi(conn, tid, "inexistant")["raison"] == "mode_inconnu"
    # Depuis 'brouillon', lancement interdit (transition).
    tid2 = services.creer_tournoi(conn, "T2")
    services.ajouter_participant(conn, tid2, "X")
    assert services.lancer_tournoi(conn, tid2, "high_score")["raison"] == "etat"


def test_lancer_high_score_cree_les_lignes(conn):
    tid = _tournoi_lance_high_score(conn)
    assert services.lancer_tournoi(conn, tid, "high_score")["ok"]
    t = services.get_tournoi(conn, tid)
    assert t["etat"] == "lance" and t["mode_scoring"] == "high_score"
    # Une ligne de score par participant.
    assert conn.execute("SELECT COUNT(*) FROM rencontres WHERE id_tournoi = ?",
                        (tid,)).fetchone()[0] == 3


def test_scores_et_classement_ex_aequo(conn):
    tid = _tournoi_lance_high_score(conn)
    services.lancer_tournoi(conn, tid, "high_score")
    lignes = {l["pseudo"]: l["id_inscription"] for l in services.lignes_high_score(conn, tid)}
    services.enregistrer_scores_high_score(conn, tid, {
        lignes["Alice"]: 10, lignes["Bob"]: 10, lignes["Chloé"]: 5,
    })
    cl = services.classement_high_score(conn, tid)
    # Alice et Bob ex æquo en tête (rang 1), Chloé rang 3.
    rangs = {c["pseudo"]: c["rang"] for c in cl}
    assert rangs["Alice"] == 1 and rangs["Bob"] == 1
    assert rangs["Chloé"] == 3


def test_classement_score_manquant_en_dernier(conn):
    tid = _tournoi_lance_high_score(conn, pseudos=("Alice", "Bob"))
    services.lancer_tournoi(conn, tid, "high_score")
    lignes = {l["pseudo"]: l["id_inscription"] for l in services.lignes_high_score(conn, tid)}
    services.enregistrer_scores_high_score(conn, tid, {lignes["Alice"]: 7})
    cl = services.classement_high_score(conn, tid)
    assert cl[0]["pseudo"] == "Alice" and cl[0]["rang"] == 1
    # Bob, sans score, finit sans rang.
    assert cl[-1]["pseudo"] == "Bob" and cl[-1]["rang"] is None


def test_participant_ajoute_apres_lancement_a_une_ligne(conn):
    tid = _tournoi_lance_high_score(conn, pseudos=("Alice",))
    services.lancer_tournoi(conn, tid, "high_score")
    services.ajouter_participant(conn, tid, "Tardif")
    # lignes_high_score crée paresseusement la ligne manquante.
    pseudos = {l["pseudo"] for l in services.lignes_high_score(conn, tid)}
    assert "Tardif" in pseudos


# --- Ronde suisse ---
def _tournoi_suisse(conn, pseudos, nb_rondes):
    tid = services.creer_tournoi(conn, "Suisse")
    services.changer_etat(conn, tid, "inscriptions")
    for p in pseudos:
        services.ajouter_participant(conn, tid, p)
    res = services.lancer_tournoi(conn, tid, "ronde_suisse", nb_rondes)
    assert res["ok"]
    return tid


def _ids(conn, tid):
    return {l: i for i, l in
            ((r["id_inscription"], r["pseudo"])
             for r in conn.execute(
                 "SELECT id_inscription, pseudo FROM inscriptions WHERE id_tournoi=?", (tid,)))}


def _jouer_ronde(conn, tid, ronde, gagnant="a"):
    """Saisit un résultat (par défaut A gagne) pour chaque rencontre non-bye."""
    res = {}
    for m in services.rencontres_de_ronde(conn, tid, ronde):
        if not m["bye"]:
            res[m["id_rencontre"]] = gagnant
    services.enregistrer_resultats_suisse(conn, tid, ronde, res)


def test_lancer_suisse_validations(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    services.ajouter_participant(conn, tid, "Solo")
    assert services.lancer_tournoi(conn, tid, "ronde_suisse", 3)["raison"] == "pas_assez"
    services.ajouter_participant(conn, tid, "Duo")
    assert services.lancer_tournoi(conn, tid, "ronde_suisse", 0)["raison"] == "nb_rondes"
    assert services.lancer_tournoi(conn, tid, "ronde_suisse", None)["raison"] == "nb_rondes"


def test_suisse_ronde1_pair_sans_bye(conn):
    tid = _tournoi_suisse(conn, ["A", "B", "C", "D"], 3)
    r1 = services.rencontres_de_ronde(conn, tid, 1)
    assert len(r1) == 2 and all(not m["bye"] for m in r1)


def test_suisse_bye_si_impair_et_rotation(conn):
    tid = _tournoi_suisse(conn, ["A", "B", "C"], 3)
    byes = []
    for ronde in range(1, 4):
        rencs = services.rencontres_de_ronde(conn, tid, ronde)
        bye = [m["pseudo_a"] for m in rencs if m["bye"]]
        assert len(bye) == 1            # exactement un bye par ronde (impair)
        byes.append(bye[0])
        _jouer_ronde(conn, tid, ronde)
        if ronde < 3:
            assert services.generer_ronde_suivante(conn, tid)["ok"]
    # Chaque joueur a eu le bye exactement une fois (rotation).
    assert sorted(byes) == ["A", "B", "C"]


def test_suisse_pas_de_revanche(conn):
    tid = _tournoi_suisse(conn, ["A", "B", "C", "D"], 3)
    paires_vues = set()
    for ronde in range(1, 4):
        for m in services.rencontres_de_ronde(conn, tid, ronde):
            if not m["bye"]:
                paire = frozenset((m["participant_a"], m["participant_b"]))
                assert paire not in paires_vues       # jamais deux fois
                paires_vues.add(paire)
        _jouer_ronde(conn, tid, ronde)
        if ronde < 3:
            services.generer_ronde_suivante(conn, tid)
    # 4 joueurs sur 3 rondes = round robin complet : 6 paires distinctes.
    assert len(paires_vues) == 6


def test_suisse_generation_refusee_si_incomplete(conn):
    tid = _tournoi_suisse(conn, ["A", "B", "C", "D"], 3)
    # Ronde 1 non saisie -> refus.
    assert services.generer_ronde_suivante(conn, tid)["raison"] == "incomplete"
    _jouer_ronde(conn, tid, 1)
    assert services.generer_ronde_suivante(conn, tid)["ok"]


def test_suisse_bye_donne_un_point_et_classement(conn):
    tid = _tournoi_suisse(conn, ["A", "B", "C"], 1)
    # Ronde 1 : un match (A vs B p.ex.) + un bye. A gagne son match.
    _jouer_ronde(conn, tid, 1, gagnant="a")
    pts = services.points_suisse(conn, tid)
    # Tous les points totalisent : 1 (vainqueur) + 0 (perdant) + 1 (bye) = 2.
    assert sum(pts.values()) == 2.0
    cl = services.classement_suisse(conn, tid)
    assert cl[0]["rang"] == 1
    # Le dernier a 0 point.
    assert cl[-1]["points"] == 0.0


# --- Planning ---
JOUR1 = date(2026, 6, 13)
JOUR2 = date(2026, 6, 14)


def _tournoi_planifie(conn, nom, jour, heure_min, duree=60, etat="inscriptions"):
    """Crée un tournoi avec une date/heure locale données puis le sort de brouillon."""
    iso = app_services.local_vers_utc_iso(f"{jour.isoformat()}T{heure_min}")
    tid = services.creer_tournoi(conn, nom, date_heure=iso, duree_min=duree)
    if etat != "brouillon":
        services.changer_etat(conn, tid, "inscriptions")
    return tid


def test_planning_couloirs_paralleles(conn):
    # 4 tournois au même créneau -> 4 couloirs sur le jour 1.
    for n in ("A", "B", "C", "D"):
        _tournoi_planifie(conn, n, JOUR1, "14:00", duree=60)
    plan = services.planning(conn, [JOUR1, JOUR2])
    j1, j2 = plan
    assert j1["nb_couloirs"] == 4
    assert {b["couloir"] for b in j1["blocs"]} == {0, 1, 2, 3}
    assert j2["vide"] is True


def test_planning_sans_chevauchement_un_couloir(conn):
    _tournoi_planifie(conn, "Matin", JOUR1, "10:00", duree=60)   # 10:00–11:00
    _tournoi_planifie(conn, "Suite", JOUR1, "11:00", duree=60)   # 11:00–12:00
    j1 = services.planning(conn, [JOUR1])[0]
    assert j1["nb_couloirs"] == 1
    # Deux créneaux d'une heure (2 slots) qui se suivent.
    assert [b["row_span"] for b in j1["blocs"]] == [2, 2]


def test_planning_filtre_par_jour_et_brouillon(conn):
    _tournoi_planifie(conn, "Jour2", JOUR2, "15:00")
    # Un brouillon le jour 1 ne doit pas apparaître.
    _tournoi_planifie(conn, "Cache", JOUR1, "15:00", etat="brouillon")
    plan = services.planning(conn, [JOUR1, JOUR2])
    assert plan[0]["vide"] is True
    assert [b["nom"] for b in plan[1]["blocs"]] == ["Jour2"]


def test_label_jour():
    assert services.label_jour(date(2026, 6, 13)) == "samedi 13 juin"


def test_planning_route_accueil(client, monkeypatch):
    # Règle la date d'événement dans la base de prêt.
    from app import db
    c = db.get_connection()
    app_services.ecrire_parametre(c, "evenement_date", JOUR1.isoformat())
    c.close()
    # Crée un tournoi ce jour-là (non brouillon).
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Grand Tournoi", "date_heure": f"{JOUR1.isoformat()}T14:00",
                          "duree_min": "90"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})

    page = client.get("/").text
    assert "Planning des tournois" in page
    assert "Grand Tournoi" in page
    assert "samedi 13 juin" in page.lower()


# --- Ouverture groupée du jour ---
def test_ouvrir_tournois_du_jour(conn):
    from datetime import datetime as _dt
    from app.services import FUSEAU_LOCAL
    aujourdhui = _dt.now(FUSEAU_LOCAL).date()
    iso_today_1 = app_services.local_vers_utc_iso(f"{aujourdhui.isoformat()}T10:00")
    iso_today_2 = app_services.local_vers_utc_iso(f"{aujourdhui.isoformat()}T15:00")

    t1 = services.creer_tournoi(conn, "Matin", date_heure=iso_today_1)      # brouillon, aujourd'hui
    t2 = services.creer_tournoi(conn, "Aprem", date_heure=iso_today_2)      # brouillon, aujourd'hui
    # Déjà ouvert aujourd'hui -> inchangé, non recompté.
    t3 = services.creer_tournoi(conn, "Déjà", date_heure=iso_today_1)
    services.changer_etat(conn, t3, "inscriptions")
    # Brouillon mais sans date -> ignoré.
    t4 = services.creer_tournoi(conn, "Sans date")

    n = services.ouvrir_tournois_du_jour(conn, aujourdhui)
    assert n == 2
    assert services.get_tournoi(conn, t1)["etat"] == "inscriptions"
    assert services.get_tournoi(conn, t2)["etat"] == "inscriptions"
    assert services.get_tournoi(conn, t4)["etat"] == "brouillon"


def test_ouvrir_tournois_autre_jour_ignore(conn):
    iso_passe = app_services.local_vers_utc_iso("2020-01-01T10:00")
    t = services.creer_tournoi(conn, "Vieux", date_heure=iso_passe)
    from datetime import datetime as _dt
    from app.services import FUSEAU_LOCAL
    n = services.ouvrir_tournois_du_jour(conn, _dt.now(FUSEAU_LOCAL).date())
    assert n == 0
    assert services.get_tournoi(conn, t)["etat"] == "brouillon"


def test_ouvrir_aujourdhui_route(client):
    from datetime import datetime as _dt
    from app.services import FUSEAU_LOCAL
    today = _dt.now(FUSEAU_LOCAL).date().isoformat()
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Du jour", "date_heure": f"{today}T11:00"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    res = client.post("/tournoi/ouvrir-aujourdhui")
    assert res.status_code == 200 and "ouvert(s)" in res.text
    # Le tournoi est désormais ouvert aux inscriptions.
    assert "Inscriptions ouvertes" in client.get(f"/tournoi/{tid}").text
    # M9 (docs/idees-ux.md) : confirmation reformulée, sans le jargon d'état
    # interne « en brouillon ».
    liste = client.get("/tournois").text
    assert "Ouvrir les inscriptions de tous les tournois du jour ?" in liste
    assert "en brouillon" not in liste


# --- Duplication ---
def test_dupliquer_copie_caracteristiques(conn):
    iso = app_services.local_vers_utc_iso("2026-06-13T14:00")
    src = services.creer_tournoi(conn, "Catan Cup", jeu="Catan", date_heure=iso,
                                 duree_min=90, nb_places=8, emplacement="Table 3",
                                 inscription_en_ligne=False)
    services.changer_etat(conn, src, "inscriptions")
    services.ajouter_participant(conn, src, "Alice")   # ne doit PAS être copié

    nouvel_iso = app_services.local_vers_utc_iso("2026-06-13T17:00")
    new = services.dupliquer_tournoi(conn, src, nouvel_iso)
    t = services.get_tournoi(conn, new)
    assert new != src
    assert (t["nom"], t["jeu"], t["duree_min"], t["nb_places"], t["emplacement"]) \
        == ("Catan Cup", "Catan", 90, 8, "Table 3")
    assert t["inscription_en_ligne"] == 0
    # Repart propre : brouillon, nouvel horaire, sans inscrit.
    assert t["etat"] == "brouillon"
    assert t["date_heure"] == nouvel_iso
    assert services.compter_inscriptions(conn, new) == 0


def test_dupliquer_source_absente(conn):
    assert services.dupliquer_tournoi(conn, 999, None) is None


def test_dupliquer_route(client):
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Modèle", "jeu": "Carcassonne",
                          "date_heure": "2026-06-13T14:00", "duree_min": "60",
                          "nb_places": "6"}, follow_redirects=False)
    src = r.headers["location"].split("/")[2]
    # Duplication vers un nouvel horaire -> redirige vers la gestion de la copie.
    dup = client.post(f"/tournoi/{src}/dupliquer",
                      data={"date_heure": "2026-06-13T18:00"}, follow_redirects=False)
    assert dup.status_code == 303
    new = dup.headers["location"].split("/")[2]
    assert new != src
    page = client.get(f"/tournoi/{new}/gerer").text
    assert "Modèle" in page and "Carcassonne" in page
    # Le bouton Dupliquer est présent sur la gestion de la source.
    assert "Dupliquer à un autre horaire" in client.get(f"/tournoi/{src}/gerer").text


# --- Agenda (.ics) ---
def test_ical_contenu(conn):
    iso = app_services.local_vers_utc_iso("2026-06-13T14:00")
    tid = services.creer_tournoi(conn, "Soirée, jeux", jeu="Catan",
                                 date_heure=iso, duree_min=90, emplacement="Table 3")
    ics = services.ical_tournoi(conn, tid)
    assert "BEGIN:VCALENDAR" in ics and "BEGIN:VEVENT" in ics
    # 14:00 heure locale (été = UTC+2) -> 12:00 UTC ; fin +90 min -> 13:30 UTC.
    assert "DTSTART:20260613T120000Z" in ics
    assert "DTEND:20260613T133000Z" in ics
    # La virgule du titre est échappée (RFC 5545).
    assert "SUMMARY:Soirée\\, jeux" in ics
    assert "LOCATION:Table 3" in ics


def test_ical_none_sans_date(conn):
    tid = services.creer_tournoi(conn, "Sans date")
    assert services.ical_tournoi(conn, tid) is None


def test_ical_duree_par_defaut(conn):
    iso = app_services.local_vers_utc_iso("2026-06-13T14:00")
    tid = services.creer_tournoi(conn, "T", date_heure=iso)  # pas de durée
    ics = services.ical_tournoi(conn, tid)
    # Durée par défaut 60 min -> fin 13:00 UTC.
    assert "DTEND:20260613T130000Z" in ics


def test_agenda_route_et_bouton(client):
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Avec date", "date_heure": "2026-06-13T14:00",
                          "duree_min": "60"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})

    ics = client.get(f"/tournoi/{tid}/agenda.ics")
    assert ics.status_code == 200
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in ics.text
    # Le bouton apparaît sur la page publique du tournoi.
    assert "Ajouter à mon agenda" in client.get(f"/tournoi/{tid}").text


def test_agenda_route_404_sans_date(client):
    r = client.post("/tournoi/nouveau", data={"nom": "Sans date"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    assert client.get(f"/tournoi/{tid}/agenda.ics").status_code == 404


# --- Tournois par équipes ---
def test_equipe_inscription_valide_et_membres(conn):
    tid = services.creer_tournoi(conn, "Duo Cup", par_equipes=True, taille_equipe=2)
    assert services.get_tournoi(conn, tid)["par_equipes"] == 1
    services.changer_etat(conn, tid, "inscriptions")
    r = services.inscrire(conn, tid, "Les Kangourous", ["Alice", "Bob"])
    assert r["ok"]
    insc = services.lister_inscriptions(conn, tid)[0]
    assert insc["pseudo"] == "Les Kangourous"
    assert insc["membres_liste"] == ["Alice", "Bob"]


def test_equipe_membres_incomplets_refus(conn):
    tid = services.creer_tournoi(conn, "Duo", par_equipes=True, taille_equipe=2)
    services.changer_etat(conn, tid, "inscriptions")
    assert services.inscrire(conn, tid, "Solo", ["Alice"])["raison"] == "equipe_incomplete"
    assert services.inscrire(conn, tid, "Trop", ["A", "B", "C"])["raison"] == "equipe_incomplete"
    # Membres vides ignorés -> compte réel != taille.
    assert services.inscrire(conn, tid, "Vide", ["Alice", "  "])["raison"] == "equipe_incomplete"


def test_equipe_ajout_benevole_permissif(conn):
    tid = services.creer_tournoi(conn, "Duo", par_equipes=True, taille_equipe=2)
    services.changer_etat(conn, tid, "inscriptions")
    # Le bénévole peut ajouter une équipe même incomplète.
    assert services.ajouter_participant(conn, tid, "Impaire", ["Zoé"])["ok"]
    assert services.lister_inscriptions(conn, tid)[0]["membres_liste"] == ["Zoé"]


def test_equipe_duplication_conserve(conn):
    tid = services.creer_tournoi(conn, "Duo", par_equipes=True, taille_equipe=3)
    t2 = services.get_tournoi(conn, services.dupliquer_tournoi(conn, tid, None))
    assert t2["par_equipes"] == 1 and t2["taille_equipe"] == 3


def test_equipe_desinscription_par_code(conn):
    tid = services.creer_tournoi(conn, "Duo", par_equipes=True, taille_equipe=2)
    services.changer_etat(conn, tid, "inscriptions")
    code = services.inscrire(conn, tid, "Team", ["A", "B"])["code"]
    assert services.desinscrire(conn, code)["ok"]
    assert services.compter_inscriptions(conn, tid) == 0


def test_equipe_compatible_round_robin(conn):
    # Une équipe = un participant -> compatible avec tous les modes (ici round robin).
    tid = services.creer_tournoi(conn, "Champ", par_equipes=True, taille_equipe=2)
    services.changer_etat(conn, tid, "inscriptions")
    for nom, membres in [("T1", ["a", "b"]), ("T2", ["c", "d"]), ("T3", ["e", "f"])]:
        services.inscrire(conn, tid, nom, membres)
    assert services.lancer_tournoi(conn, tid, "round_robin")["ok"]
    r1 = services.rencontres_de_ronde(conn, tid, 1)
    noms = {m["pseudo_a"] for m in r1} | {m["pseudo_b"] for m in r1 if m["pseudo_b"]}
    assert noms <= {"T1", "T2", "T3"} and noms   # les rencontres opposent des équipes


def test_equipe_route_inscription(client):
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Coupe Duo", "par_equipes": "on", "taille_equipe": "2",
                          "inscription_en_ligne": "on"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    form = client.get(f"/tournoi/{tid}/inscription").text
    assert "Nom de l'équipe" in form and "membre_1" in form and "membre_2" in form
    ok = client.post(f"/tournoi/{tid}/inscription",
                     data={"pseudo": "Les Renards", "membre_1": "Alice", "membre_2": "Bob"})
    assert ok.status_code == 200 and "code de désinscription" in ok.text.lower()
    # Page publique : nom d'équipe visible, membres NON affichés publiquement.
    detail = client.get(f"/tournoi/{tid}").text
    assert "Les Renards" in detail and "Alice" not in detail
    # Côté bénévole (mode ouvert en test) : les membres sont visibles sur la gestion.
    assert "Alice" in client.get(f"/tournoi/{tid}/gerer").text


def test_equipe_inscription_incomplete_route(client):
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Trio", "par_equipes": "on", "taille_equipe": "3",
                          "inscription_en_ligne": "on"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    bad = client.post(f"/tournoi/{tid}/inscription",
                      data={"pseudo": "Bancale", "membre_1": "Alice", "membre_2": "Bob"})
    assert bad.status_code == 400 and "équipe" in bad.text.lower()


# --- Round robin ---
def _tournoi_round_robin(conn, pseudos, bo3=False):
    tid = services.creer_tournoi(conn, "RR")
    services.changer_etat(conn, tid, "inscriptions")
    for p in pseudos:
        services.ajouter_participant(conn, tid, p)
    assert services.lancer_tournoi(conn, tid, "round_robin", bo3=bo3)["ok"]
    return tid


def _paires_jouees(conn, tid):
    return [frozenset((r["participant_a"], r["participant_b"]))
            for r in conn.execute(
                "SELECT participant_a, participant_b FROM rencontres "
                "WHERE id_tournoi = ? AND participant_b IS NOT NULL", (tid,))]


def test_round_robin_refus_moins_de_3(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    services.ajouter_participant(conn, tid, "A")
    services.ajouter_participant(conn, tid, "B")
    assert services.lancer_tournoi(conn, tid, "round_robin")["raison"] == "pas_assez"


def test_round_robin_pair_toutes_les_paires_une_fois(conn):
    tid = _tournoi_round_robin(conn, ["A", "B", "C", "D"])
    assert services.get_tournoi(conn, tid)["nb_rondes"] == 3      # n-1
    paires = _paires_jouees(conn, tid)
    assert len(paires) == 6 and len(set(paires)) == 6            # C(4,2), chacune 1 fois
    byes = conn.execute("SELECT COUNT(*) FROM rencontres WHERE id_tournoi=? "
                        "AND participant_b IS NULL", (tid,)).fetchone()[0]
    assert byes == 0                                             # effectif pair : aucun repos


def test_round_robin_impair_repos_equilibres(conn):
    tid = _tournoi_round_robin(conn, ["A", "B", "C", "D", "E"])
    assert services.get_tournoi(conn, tid)["nb_rondes"] == 5      # n (impair)
    paires = _paires_jouees(conn, tid)
    assert len(paires) == 10 and len(set(paires)) == 10          # C(5,2)
    byes = [r["participant_a"] for r in conn.execute(
        "SELECT participant_a FROM rencontres WHERE id_tournoi=? AND participant_b IS NULL", (tid,))]
    assert len(byes) == 5 and len(set(byes)) == 5               # chacun exactement un repos


def test_round_robin_classement_bo3_et_confrontations(conn):
    tid = _tournoi_round_robin(conn, ["A", "B", "C"], bo3=True)
    assert services.get_tournoi(conn, tid)["bo3"] == 1
    for ronde in range(1, services.get_tournoi(conn, tid)["nb_rondes"] + 1):
        manches = {m["id_rencontre"]: (2, 0)
                   for m in services.rencontres_de_ronde(conn, tid, ronde) if not m["bye"]}
        if manches:
            services.enregistrer_manches(conn, tid, ronde, manches, autoriser_nul=True)
    cl = services.classement_round_robin(conn, tid)
    assert cl[0]["rang"] == 1
    tab = services.table_confrontations(conn, tid)
    assert len(tab["entetes"]) == 3 and len(tab["lignes"]) == 3
    # La diagonale est neutre ; hors diagonale, un score « 2–0 » ou « 0–2 » apparaît.
    textes = [c["txt"] for lg in tab["lignes"] for c in lg["cellules"]]
    assert "2–0" in textes and "0–2" in textes


def test_round_robin_route_complet(client):
    r = client.post("/tournoi/nouveau", data={"nom": "RR Route"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    for p in ("A", "B", "C", "D"):
        client.post(f"/tournoi/{tid}/participant", data={"pseudo": p})
    lance = client.post(f"/tournoi/{tid}/lancer",
                        data={"mode_scoring": "round_robin"}, follow_redirects=False)
    assert lance.status_code == 303 and lance.headers["location"].endswith("/rondes")
    assert "Round robin" in client.get(f"/tournoi/{tid}/rondes").text

    from app.tournoi import db as tdb
    c = tdb.get_connection()
    r1 = [row["id_rencontre"] for row in c.execute(
        "SELECT id_rencontre FROM rencontres WHERE id_tournoi=? AND ronde=1 "
        "AND participant_b IS NOT NULL", (tid,))]
    c.close()
    client.post(f"/tournoi/{tid}/rondes/1/resultats", data={f"res_{i}": "a" for i in r1})
    # La page publique affiche le tableau des confrontations.
    assert "Tableau des confrontations" in client.get(f"/tournoi/{tid}").text


# --- Élimination directe ---
def _tournoi_elim(conn, pseudos):
    tid = services.creer_tournoi(conn, "Elim")
    services.changer_etat(conn, tid, "inscriptions")
    for p in pseudos:
        services.ajouter_participant(conn, tid, p)
    assert services.lancer_tournoi(conn, tid, "elimination")["ok"]
    return tid


def _jouer_tour_elim(conn, tid, tour, gagnant="a"):
    res = {m["id_rencontre"]: gagnant
           for m in services.rencontres_de_ronde(conn, tid, tour) if not m["bye"]}
    services.enregistrer_resultats_suisse(conn, tid, tour, res)


def test_nb_tours_et_ordre_places():
    assert services._nb_tours_elimination(2) == 1
    assert services._nb_tours_elimination(4) == 2
    assert services._nb_tours_elimination(5) == 3
    assert services._nb_tours_elimination(8) == 3
    assert services._nb_tours_elimination(9) == 4
    assert services._ordre_places(4) == [1, 4, 2, 3]
    assert services._ordre_places(8) == [1, 8, 4, 5, 2, 7, 3, 6]


def test_elim_refus_un_seul(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    services.ajouter_participant(conn, tid, "Solo")
    assert services.lancer_tournoi(conn, tid, "elimination")["raison"] == "pas_assez"


def test_elim_puissance_de_deux(conn):
    tid = _tournoi_elim(conn, ["A", "B", "C", "D"])
    assert services.get_tournoi(conn, tid)["nb_rondes"] == 2
    r1 = services.rencontres_de_ronde(conn, tid, 1)
    assert len(r1) == 2 and all(not m["bye"] for m in r1)


def test_elim_byes_si_non_puissance_de_deux(conn):
    # 5 joueurs -> arbre de 8 -> 3 byes au 1er tour, 1 vrai match.
    tid = _tournoi_elim(conn, ["A", "B", "C", "D", "E"])
    assert services.get_tournoi(conn, tid)["nb_rondes"] == 3
    r1 = services.rencontres_de_ronde(conn, tid, 1)
    assert len(r1) == 4
    assert sum(1 for m in r1 if m["bye"]) == 3
    assert sum(1 for m in r1 if not m["bye"]) == 1


def test_elim_progression_et_vainqueur(conn):
    tid = _tournoi_elim(conn, ["A", "B", "C", "D"])
    assert services.vainqueur(conn, tid) is None
    # Tour 1 : génération du tour 2 refusée tant qu'incomplet.
    assert services.generer_tour_suivant(conn, tid)["raison"] == "incomplete"
    _jouer_tour_elim(conn, tid, 1, gagnant="a")
    assert services.generer_tour_suivant(conn, tid)["ok"]      # finale
    # Finale : 1 rencontre.
    assert len(services.rencontres_de_ronde(conn, tid, 2)) == 1
    _jouer_tour_elim(conn, tid, 2, gagnant="a")
    v = services.vainqueur(conn, tid)
    assert v is not None
    # Plus de tour à générer.
    assert services.generer_tour_suivant(conn, tid)["raison"] == "terminee"


def test_elim_bye_qualifie_automatiquement(conn):
    # 3 joueurs -> arbre de 4 : 1 bye, 1 match au tour 1.
    tid = _tournoi_elim(conn, ["A", "B", "C"])
    r1 = services.rencontres_de_ronde(conn, tid, 1)
    byes = [m for m in r1 if m["bye"]]
    assert len(byes) == 1
    # Le tour 1 est « complet » même si seul le match doit être saisi.
    _jouer_tour_elim(conn, tid, 1)
    assert services.ronde_complete(conn, tid, 1)
    assert services.generer_tour_suivant(conn, tid)["ok"]


def test_elim_route_complet(client):
    r = client.post("/tournoi/nouveau", data={"nom": "Elim Route"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    for p in ("A", "B", "C", "D"):
        client.post(f"/tournoi/{tid}/participant", data={"pseudo": p})

    lance = client.post(f"/tournoi/{tid}/lancer",
                        data={"mode_scoring": "elimination"}, follow_redirects=False)
    assert lance.status_code == 303 and lance.headers["location"].endswith("/arbre")
    assert "limination" in client.get(f"/tournoi/{tid}/arbre").text

    from app.tournoi import db as tdb
    c = tdb.get_connection()
    r1 = [row["id_rencontre"] for row in c.execute(
        "SELECT id_rencontre FROM rencontres WHERE id_tournoi=? AND ronde=1 "
        "AND participant_b IS NOT NULL", (tid,))]
    c.close()
    client.post(f"/tournoi/{tid}/arbre/1/resultats", data={f"res_{i}": "a" for i in r1})
    suiv = client.post(f"/tournoi/{tid}/arbre/suivant")
    assert "Finale" in suiv.text

    c = tdb.get_connection()
    fin = [row["id_rencontre"] for row in c.execute(
        "SELECT id_rencontre FROM rencontres WHERE id_tournoi=? AND ronde=2", (tid,))]
    c.close()
    final = client.post(f"/tournoi/{tid}/arbre/2/resultats", data={f"res_{fin[0]}": "a"})
    assert "Vainqueur" in final.text
    # La page publique annonce aussi le vainqueur.
    assert "Vainqueur" in client.get(f"/tournoi/{tid}").text


# --- BO3 (manches) ---
def test_bo3_au_lancement_pas_a_la_creation(conn):
    tid = services.creer_tournoi(conn, "T")
    # À la création, bo3 vaut 0 par défaut.
    assert services.get_tournoi(conn, tid)["bo3"] == 0
    services.changer_etat(conn, tid, "inscriptions")
    for p in ("A", "B"):
        services.ajouter_participant(conn, tid, p)
    services.lancer_tournoi(conn, tid, "ronde_suisse", 1, bo3=True)
    assert services.get_tournoi(conn, tid)["bo3"] == 1


def test_bo3_ignore_en_high_score(conn):
    tid = services.creer_tournoi(conn, "T")
    services.changer_etat(conn, tid, "inscriptions")
    services.ajouter_participant(conn, tid, "A")
    services.lancer_tournoi(conn, tid, "high_score", bo3=True)
    assert services.get_tournoi(conn, tid)["bo3"] == 0


def test_bo3_manches_deduit_le_vainqueur_suisse(conn):
    tid = _tournoi_suisse(conn, ["A", "B"], 1)  # lancé sans bo3
    # On force le mode BO3 pour le test de saisie des manches.
    conn.execute("UPDATE tournois SET bo3=1 WHERE id_tournoi=?", (tid,)); conn.commit()
    m = [x for x in services.rencontres_de_ronde(conn, tid, 1) if not x["bye"]][0]
    services.enregistrer_manches(conn, tid, 1, {m["id_rencontre"]: (2, 1)},
                                 autoriser_nul=True)
    lignes = services.rencontres_de_ronde(conn, tid, 1)
    saisi = [x for x in lignes if x["id_rencontre"] == m["id_rencontre"]][0]
    assert saisi["resultat"] == "a" and saisi["score_a"] == 2 and saisi["score_b"] == 1


def test_bo3_egalite_nul_autorise_ou_non(conn):
    assert services._resultat_depuis_manches(1, 1, autoriser_nul=True) == "nul"
    # En élimination, égalité => pas de vainqueur (None).
    assert services._resultat_depuis_manches(1, 1, autoriser_nul=False) is None
    assert services._resultat_depuis_manches(2, 0, autoriser_nul=False) == "a"
    assert services._resultat_depuis_manches(0, 2, autoriser_nul=True) == "b"


def test_bo3_route_elimination_saisie_manches(client):
    r = client.post("/tournoi/nouveau", data={"nom": "Elim BO3"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    for p in ("A", "B"):
        client.post(f"/tournoi/{tid}/participant", data={"pseudo": p})
    # Lancement en BO3.
    client.post(f"/tournoi/{tid}/lancer",
                data={"mode_scoring": "elimination", "bo3": "on"})

    from app.tournoi import db as tdb
    c = tdb.get_connection()
    rid = c.execute("SELECT id_rencontre FROM rencontres WHERE id_tournoi=? AND ronde=1",
                    (tid,)).fetchone()["id_rencontre"]
    c.close()
    # Saisie en manches : 2–1 -> A gagne la finale (arbre de 2 = 1 tour).
    fin = client.post(f"/tournoi/{tid}/arbre/1/resultats",
                      data={f"ma_{rid}": "2", f"mb_{rid}": "1"})
    assert "Vainqueur" in fin.text
    # Le score 2–1 apparaît sur la page publique.
    assert "2–1" in client.get(f"/tournoi/{tid}").text


def test_suisse_route_complet(client):
    r = client.post("/tournoi/nouveau", data={"nom": "Suisse Route"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    for p in ("A", "B", "C", "D"):
        client.post(f"/tournoi/{tid}/participant", data={"pseudo": p})

    lance = client.post(f"/tournoi/{tid}/lancer",
                        data={"mode_scoring": "ronde_suisse", "nb_rondes": "2"},
                        follow_redirects=False)
    assert lance.status_code == 303 and lance.headers["location"].endswith("/rondes")
    assert "Ronde 1" in client.get(f"/tournoi/{tid}/rondes").text

    # Saisie des résultats de la ronde 1 (champs res_<id>).
    from app.tournoi import db as tdb
    c = tdb.get_connection()
    rencs = [row["id_rencontre"] for row in c.execute(
        "SELECT id_rencontre FROM rencontres WHERE id_tournoi=? AND ronde=1 "
        "AND participant_b IS NOT NULL", (tid,))]
    c.close()
    client.post(f"/tournoi/{tid}/rondes/1/resultats",
                data={f"res_{rid}": "a" for rid in rencs})

    # Génération de la ronde 2.
    suiv = client.post(f"/tournoi/{tid}/rondes/suivante")
    assert "Ronde 2" in suiv.text
    # Le classement apparaît sur la page publique.
    assert "Classement" in client.get(f"/tournoi/{tid}").text


# ===========================================================================
# Routes — TestClient avec les DEUX bases temporaires
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))

    from app import db
    from app.tournoi import db as tdb
    from app.planning import db as pdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    db.init_db()
    tdb.init_db()
    pdb.init_db()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_liste_publique(client):
    r = client.get("/tournois")
    assert r.status_code == 200 and "Tournois" in r.text


def test_age_route_creation_et_affichage(client):
    r = client.post("/tournoi/nouveau",
                    data={"nom": "Famille", "age": "8+",
                          "date_heure": "2026-06-13T14:00"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    # L'âge apparaît sur la gestion et la page publique.
    assert "8+" in client.get(f"/tournoi/{tid}/gerer").text
    assert "8+" in client.get(f"/tournoi/{tid}").text


def test_aide_tournois(client):
    r = client.get("/tournoi/aide")
    assert r.status_code == 200
    assert "Aide" in r.text
    # Couvre les trois modes de scoring.
    assert "High score" in r.text and "Ronde suisse" in r.text and "limination directe" in r.text


def test_cycle_complet_route(client):
    # Création (mode ouvert : pas de PRET_TOKEN -> accès bénévole autorisé).
    r = client.post("/tournoi/nouveau", data={"nom": "Tournoi Test",
                    "jeu": "Catan", "nb_places": "4",
                    "inscription_en_ligne": "on"}, follow_redirects=False)
    assert r.status_code == 303
    tid = r.headers["location"].split("/")[2]

    # Brouillon : pas d'inscription publique possible.
    detail = client.get(f"/tournoi/{tid}")
    assert detail.status_code == 200 and "Tournoi Test" in detail.text
    assert "S'inscrire" not in detail.text

    # Ouvrir les inscriptions.
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    assert "S'inscrire" in client.get(f"/tournoi/{tid}").text

    # Inscription publique : le code est affiché.
    insc = client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Alice"})
    assert insc.status_code == 200 and "code de désinscription" in insc.text.lower()
    # Le pseudo apparaît dans la gestion.
    assert "Alice" in client.get(f"/tournoi/{tid}/gerer").text

    # Désinscription via le formulaire (code récupéré en base).
    from app.tournoi import db as tdb
    c = tdb.get_connection()
    code = c.execute("SELECT code_desinscription FROM inscriptions").fetchone()[0]
    c.close()
    d = client.post("/tournoi/desinscription", data={"code": code})
    assert "enregistrée" in d.text.lower()


def test_inscription_complet_route(client):
    r = client.post("/tournoi/nouveau", data={"nom": "T", "nb_places": "1",
                    "inscription_en_ligne": "on"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    assert client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Alice"}).status_code == 200
    plein = client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Bob"})
    assert plein.status_code == 400 and "complet" in plein.text.lower()


def test_inscription_bouton_copier_code(client):
    # M4 (docs/idees-ux.md) : bouton « Copier le code » sur la confirmation
    # d'inscription (motif réutilisé de /admin/jeton).
    r = client.post("/tournoi/nouveau", data={"nom": "T2", "inscription_en_ligne": "on"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    ok = client.post(f"/tournoi/{tid}/inscription", data={"pseudo": "Alice"})
    assert ok.status_code == 200
    assert 'onclick="copierCode()"' in ok.text
    assert '<span id="copie-ok" class="copie-ok" hidden>copié ✓</span>' in ok.text
    assert "navigator.clipboard.writeText(" in ok.text
    # Le code injecté dans le JS est bien celui affiché à l'écran.
    from app.tournoi import db as tdb
    conn = tdb.get_connection()
    try:
        code = conn.execute(
            "SELECT code_desinscription FROM inscriptions WHERE id_tournoi = ?", (int(tid),)
        ).fetchone()[0]
    finally:
        conn.close()
    assert code in ok.text


def test_gerer_lancement_grise_champs_inapplicables(client):
    # M5 (docs/idees-ux.md) : le formulaire de lancement grise (disabled +
    # opacity) le nombre de rondes et le BO3 selon le mode choisi, en plus de
    # la notice déjà présente (le serveur revalide tout de toute façon).
    r = client.post("/tournoi/nouveau", data={"nom": "T3"}, follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    client.post(f"/tournoi/{tid}/participant", data={"pseudo": "Alice"})
    page = client.get(f"/tournoi/{tid}/gerer").text
    assert 'id="champ_rondes"' in page
    assert 'id="champ_bo3"' in page
    assert 'mode.value === "ronde_suisse"' in page
    assert 'mode.value !== "high_score"' in page
    # La notice explicative reste présente (repli si JS indisponible).
    assert "Le nombre de rondes ne" in page
    # S4 (docs/idees-ux.md) : aide contextuelle repliée ajoutée EN PLUS de la
    # notice ci-dessus (ne la remplace pas, celle-ci sert de repli JS).
    assert 'class="aide-inline"' in page
    assert "Comment ça marche ?" in page
    assert 'href="/tournoi/aide"' in page


def test_suppression_double_confirmation(client):
    r = client.post("/tournoi/nouveau", data={"nom": "À supprimer"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    # Sans la case cochée : pas de suppression (renvoi vers la confirmation).
    sans = client.post(f"/tournoi/{tid}/supprimer", data={}, follow_redirects=False)
    assert sans.status_code == 303 and sans.headers["location"].endswith("/supprimer")
    assert client.get(f"/tournoi/{tid}").status_code == 200
    # Avec confirmation : supprimé.
    ok = client.post(f"/tournoi/{tid}/supprimer", data={"confirmation": "oui"},
                     follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/tournois"
    assert client.get(f"/tournoi/{tid}").status_code == 404


def test_high_score_route_complet(client):
    # Création + ouverture + 2 participants manuels.
    r = client.post("/tournoi/nouveau", data={"nom": "HS Route"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})
    client.post(f"/tournoi/{tid}/participant", data={"pseudo": "Alice"})
    client.post(f"/tournoi/{tid}/participant", data={"pseudo": "Bob"})

    # Lancement high score -> redirige vers la saisie des scores.
    lance = client.post(f"/tournoi/{tid}/lancer",
                        data={"mode_scoring": "high_score"}, follow_redirects=False)
    assert lance.status_code == 303 and lance.headers["location"].endswith("/scores")

    # Récupère les id d'inscription pour nommer les champs score_<id>.
    from app.tournoi import db as tdb
    c = tdb.get_connection()
    ids = {row["pseudo"]: row["id_inscription"] for row in
           c.execute("SELECT id_inscription, pseudo FROM inscriptions WHERE id_tournoi = ?", (tid,))}
    c.close()

    save = client.post(f"/tournoi/{tid}/scores", data={
        f"score_{ids['Alice']}": "12", f"score_{ids['Bob']}": "30"})
    assert save.status_code == 200 and "enregistrés" in save.text.lower()

    # Le classement public montre Bob en tête (30 > 12).
    detail = client.get(f"/tournoi/{tid}").text
    assert "Classement" in detail
    pos_bob, pos_alice = detail.index("Bob"), detail.index("Alice")
    assert pos_bob < pos_alice


def test_lancer_protege_par_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-hs-secret-123")
    assert client.post("/tournoi/1/lancer", data={"mode_scoring": "high_score"}).status_code == 403
    assert client.get("/tournoi/1/scores").status_code == 403


def test_routes_benevole_protegees(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-tournoi-secret-123")
    # Public accessible, gestion refusée sans jeton.
    assert client.get("/tournois").status_code == 200
    assert client.get("/tournoi/nouveau").status_code == 403
    r = client.post("/tournoi/nouveau", data={"nom": "X"})
    assert r.status_code == 403
