"""
Tests du module « Planning bénévole » (socle phase 1) : trame, collecte des
souhaits et préremplissage glouton (contraintes DURES), sur une base séparée en
mémoire.

Le préremplissage est « dégrossi » : on vérifie qu'il respecte les contraintes
dures (disponibilité, « surtout pas », plafond d'heures, pas deux postes en même
temps) et qu'il LAISSE LES TROUS plutôt que de forcer. Les contraintes molles
(continuité, expérience, équité fine) sont hors socle.
"""

import sqlite3

import pytest

from app.planning import models, services


# ===========================================================================
# Fixture — base du planning en mémoire
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


def _creneau(conn, ev, jour, h_debut, h_fin, **kw):
    """Crée un créneau le 12/09/2026 de h_debut à h_fin (heures locales)."""
    debut = f"2026-09-12T{h_debut:02d}:00"
    fin = f"2026-09-12T{h_fin:02d}:00"
    return services.ajouter_creneau(conn, ev, jour, debut, fin, **kw)


# ===========================================================================
# Événements & machine à états
# ===========================================================================
def test_machine_a_etats(conn):
    ev = services.creer_evenement(conn, "Festival 2026")
    assert services.get_evenement(conn, ev)["etat"] == "collecte"
    # collecte -> publie interdit (il faut passer par brouillon).
    assert services.changer_etat(conn, ev, "publie") is False
    assert services.changer_etat(conn, ev, "collecte") is False  # déjà collecte
    assert services.changer_etat(conn, ev, "brouillon") is True
    assert services.changer_etat(conn, ev, "publie") is True
    assert services.get_evenement(conn, ev)["etat"] == "publie"
    # état inconnu refusé.
    assert services.changer_etat(conn, ev, "n_importe_quoi") is False


# ===========================================================================
# Trame : postes, créneaux, besoins
# ===========================================================================
def test_trame_et_besoins(conn):
    ev = services.creer_evenement(conn, "T")
    accueil = services.ajouter_poste(conn, ev, "Accueil")
    bar = services.ajouter_poste(conn, ev, "Bar", demande_experience=True)
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    assert cr is not None
    services.definir_besoin(conn, cr, accueil, 2)
    services.definir_besoin(conn, cr, bar, 0)  # grisé
    m = services.matrice_besoins(conn, ev)
    assert m == {(cr, accueil): 2}  # le besoin 0 n'apparaît pas
    assert services.lister_postes(conn, ev)[1]["demande_experience"] == 1
    assert services.duree_heures(services.lister_creneaux(conn, ev)[0]) == 2.0


def test_creneau_bornes_invalides(conn):
    ev = services.creer_evenement(conn, "T")
    assert services.ajouter_creneau(conn, ev, "Samedi", "", "pasunedate") is None


def test_dupliquer_trame(conn):
    ev = services.creer_evenement(conn, "Edition A")
    p = services.ajouter_poste(conn, ev, "Accueil")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    services.definir_besoin(conn, cr, p, 2)
    services.changer_etat(conn, ev, "brouillon")
    services.enregistrer_souhaits(conn, ev, "Alice")  # refusée (pas en collecte)

    new = services.dupliquer_trame(conn, ev, "Edition B")
    assert services.get_evenement(conn, new)["etat"] == "collecte"
    assert len(services.lister_postes(conn, new)) == 1
    assert len(services.lister_creneaux(conn, new)) == 1
    # Les besoins sont reportés, mais aucun bénévole recopié.
    assert sum(services.matrice_besoins(conn, new).values()) == 2
    assert services.compter_reponses(conn, new) == 0


# ===========================================================================
# Collecte des souhaits
# ===========================================================================
def test_collecte_ok_et_fermee(conn):
    ev = services.creer_evenement(conn, "T")
    p = services.ajouter_poste(conn, ev, "Accueil")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    r = services.enregistrer_souhaits(
        conn, ev, "Alice", contact="a@ex.eu", max_heures="6",
        dispos={cr}, preferences={p: "prefere"},
    )
    assert r["ok"] and r["code"]
    assert services.dispos_du_benevole(conn, r["id"]) == {cr}
    assert services.prefs_du_benevole(conn, r["id"]) == {p: "prefere"}
    assert services.get_benevole(conn, r["id"])["max_heures"] == 6.0
    # Nom vide refusé.
    assert services.enregistrer_souhaits(conn, ev, "   ")["raison"] == "nom_vide"
    # Hors collecte -> fermée.
    services.changer_etat(conn, ev, "brouillon")
    assert services.enregistrer_souhaits(conn, ev, "Bob")["raison"] == "fermee"


def test_collecte_edition_par_code(conn):
    ev = services.creer_evenement(conn, "T")
    p1 = services.ajouter_poste(conn, ev, "Accueil")
    p2 = services.ajouter_poste(conn, ev, "Bar")
    cr1 = _creneau(conn, ev, "Samedi", 14, 16)
    cr2 = _creneau(conn, ev, "Samedi", 16, 18)
    r = services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1},
                                      preferences={p1: "ok"})
    code = r["code"]
    # Réédition : remplace dispos et préférences, conserve le même id/code.
    r2 = services.enregistrer_souhaits(
        conn, ev, "Alice B", dispos={cr2}, preferences={p2: "surtout_pas"},
        code_modif=code,
    )
    assert r2["ok"] and r2["id"] == r["id"] and r2["code"] == code
    assert services.compter_reponses(conn, ev) == 1
    assert services.dispos_du_benevole(conn, r["id"]) == {cr2}
    assert services.prefs_du_benevole(conn, r["id"]) == {p2: "surtout_pas"}
    assert services.get_benevole(conn, r["id"])["nom"] == "Alice B"


# ===========================================================================
# Préremplissage glouton — contraintes DURES
# ===========================================================================
def _evenement_simple(conn):
    """Un événement avec 1 poste 'Accueil' et 2 créneaux consécutifs de 2 h."""
    ev = services.creer_evenement(conn, "T")
    poste = services.ajouter_poste(conn, ev, "Accueil")
    cr1 = _creneau(conn, ev, "Samedi", 14, 16)
    cr2 = _creneau(conn, ev, "Samedi", 16, 18)
    return ev, poste, cr1, cr2


def test_prefiller_respecte_disponibilite(conn):
    ev, poste, cr1, cr2 = _evenement_simple(conn)
    services.definir_besoin(conn, cr1, poste, 1)
    services.definir_besoin(conn, cr2, poste, 1)
    # Alice n'est dispo que sur cr1.
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1})
    bilan = services.prefiller(conn, ev)
    assert bilan["places"] == 1
    cov = services.analyser_couverture(conn, ev)
    # cr2 reste un trou (personne de dispo).
    assert any(t["id_creneau"] == cr2 for t in cov["trous"])


def test_prefiller_exclut_surtout_pas(conn):
    ev, poste, cr1, _ = _evenement_simple(conn)
    services.definir_besoin(conn, cr1, poste, 1)
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1},
                                  preferences={poste: "surtout_pas"})
    bilan = services.prefiller(conn, ev)
    assert bilan["places"] == 0  # exclue malgré sa disponibilité


def test_prefiller_respecte_plafond_heures(conn):
    ev, poste, cr1, cr2 = _evenement_simple(conn)
    services.definir_besoin(conn, cr1, poste, 1)
    services.definir_besoin(conn, cr2, poste, 1)
    # Alice dispo sur les deux mais plafonnée à 2 h => un seul créneau.
    services.enregistrer_souhaits(conn, ev, "Alice", max_heures="2",
                                  dispos={cr1, cr2})
    bilan = services.prefiller(conn, ev)
    assert bilan["places"] == 1


def test_prefiller_pas_deux_postes_meme_creneau(conn):
    ev = services.creer_evenement(conn, "T")
    accueil = services.ajouter_poste(conn, ev, "Accueil")
    bar = services.ajouter_poste(conn, ev, "Bar")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    services.definir_besoin(conn, cr, accueil, 1)
    services.definir_besoin(conn, cr, bar, 1)
    # Seule Alice est dispo : elle ne peut tenir qu'un des deux postes.
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr})
    bilan = services.prefiller(conn, ev)
    assert bilan["places"] == 1


def test_prefiller_priorite_preference(conn):
    ev = services.creer_evenement(conn, "T")
    accueil = services.ajouter_poste(conn, ev, "Accueil")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    services.definir_besoin(conn, cr, accueil, 1)
    # Bob préfère l'accueil, Alice ne fait que « si vraiment » -> Bob choisi.
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr},
                                  preferences={accueil: "si_vraiment"})
    rb = services.enregistrer_souhaits(conn, ev, "Bob", dispos={cr},
                                       preferences={accueil: "prefere"})
    services.prefiller(conn, ev)
    grille = services.construire_grille(conn, ev)
    affectes = grille["jours"][0]["creneaux"][0]["cases"][0]["affectations"]
    assert len(affectes) == 1 and affectes[0]["id_benevole"] == rb["id"]


def test_verrou_conserve_au_reprefiltrage(conn):
    ev, poste, cr1, _ = _evenement_simple(conn)
    services.definir_besoin(conn, cr1, poste, 1)
    rb = services.enregistrer_souhaits(conn, ev, "Bob", dispos={cr1})
    # Affectation manuelle verrouillée d'Alice (même si non dispo).
    ra = services.enregistrer_souhaits(conn, ev, "Alice", dispos=set())
    aff = services.affecter(conn, cr1, poste, ra["id"], origine="manuel",
                            verrouille=True)
    bilan = services.prefiller(conn, ev)
    # La case est déjà comblée par la case verrouillée -> Bob non ajouté.
    assert bilan["places"] == 0
    grille = services.construire_grille(conn, ev)
    affectes = grille["jours"][0]["creneaux"][0]["cases"][0]["affectations"]
    assert [a["id_benevole"] for a in affectes] == [ra["id"]]
    assert affectes[0]["verrouille"] is True


def test_planning_du_benevole_et_taches(conn):
    ev = services.creer_evenement(conn, "T")
    poste = services.ajouter_poste(conn, ev, "Accueil")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    tache = _creneau(conn, ev, "Samedi", 9, 12, type_creneau="tache",
                     libelle="Installation")
    services.definir_besoin(conn, cr, poste, 1)
    ra = services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr})
    services.prefiller(conn, ev)
    services.affecter(conn, tache, None, ra["id"], origine="manuel")
    mon = services.planning_du_benevole(conn, ra["id"])
    assert len(mon) == 2
    # La tâche apparaît sans poste.
    taches = [m for m in mon if m["poste"] is None]
    assert len(taches) == 1 and taches[0]["creneau"]["libelle"] == "Installation"
    # La grille expose la tâche séparément.
    grille = services.construire_grille(conn, ev)
    assert grille["taches"][0]["affectations"][0]["id_benevole"] == ra["id"]


# ===========================================================================
# Export iCalendar (.ics) — « Ajouter tout mon planning à mon agenda »
# ===========================================================================
def test_ical_planning_benevole_contenu(conn):
    ev = services.creer_evenement(conn, "T")
    poste = services.ajouter_poste(conn, ev, "Accueil")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    tache = _creneau(conn, ev, "Samedi", 9, 12, type_creneau="tache",
                     libelle="Installation")
    services.definir_besoin(conn, cr, poste, 1)
    ra = services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr})
    services.prefiller(conn, ev)
    services.affecter(conn, tache, None, ra["id"], origine="manuel")

    ics = services.ical_planning_benevole(conn, ra["id"])
    assert ics is not None
    assert ics.count("BEGIN:VEVENT") == 2
    assert ics.count("END:VEVENT") == 2
    assert "SUMMARY:Accueil" in ics
    assert "SUMMARY:Installation" in ics
    assert "Samedi" in ics
    # Le prénom du bénévole n'apparaît jamais (aucune donnée personnelle).
    assert "Alice" not in ics
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip().endswith("END:VCALENDAR")


def test_ical_planning_benevole_sans_affectation(conn):
    ev = services.creer_evenement(conn, "T")
    ra = services.enregistrer_souhaits(conn, ev, "Alice")
    assert services.ical_planning_benevole(conn, ra["id"]) is None


# ===========================================================================
# Préremplissage — continuité & équité (phase 2)
# ===========================================================================
def _qui_sur(conn, ev, id_creneau, id_poste):
    """Ensemble des id de bénévoles affectés à une case (créneau × poste)."""
    grille = services.construire_grille(conn, ev)
    for jour in grille["jours"]:
        for ligne in jour["creneaux"]:
            if ligne["creneau"]["id_creneau"] == id_creneau:
                for case in ligne["cases"]:
                    if case["poste"]["id_poste"] == id_poste:
                        return {a["id_benevole"] for a in case["affectations"]}
    return set()


def test_prefiller_continuite_creneaux_contigus(conn):
    # 2 créneaux CONTIGUS (14-16 puis 16-18), 1 poste, besoin 1, deux bénévoles
    # neutres dispos sur les deux : la même personne enchaîne (continuité).
    ev, poste, cr1, cr2 = _evenement_simple(conn)  # cr1 14-16, cr2 16-18
    services.definir_besoin(conn, cr1, poste, 1)
    services.definir_besoin(conn, cr2, poste, 1)
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1, cr2})
    services.enregistrer_souhaits(conn, ev, "Bob", dispos={cr1, cr2})
    services.prefiller(conn, ev)
    sur1 = _qui_sur(conn, ev, cr1, poste)
    sur2 = _qui_sur(conn, ev, cr2, poste)
    assert len(sur1) == 1 and len(sur2) == 1
    assert sur1 == sur2  # continuité : même bénévole sur les deux créneaux


def test_prefiller_equite_si_creneaux_non_contigus(conn):
    # 2 créneaux NON contigus (trou entre les deux) : pas de continuité possible,
    # l'équité répartit sur deux bénévoles différents.
    ev = services.creer_evenement(conn, "T")
    poste = services.ajouter_poste(conn, ev, "Accueil")
    cr1 = _creneau(conn, ev, "Samedi", 14, 16)
    cr2 = _creneau(conn, ev, "Samedi", 18, 20)  # non contigu (16 != 18)
    services.definir_besoin(conn, cr1, poste, 1)
    services.definir_besoin(conn, cr2, poste, 1)
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1, cr2})
    services.enregistrer_souhaits(conn, ev, "Bob", dispos={cr1, cr2})
    services.prefiller(conn, ev)
    sur1 = _qui_sur(conn, ev, cr1, poste)
    sur2 = _qui_sur(conn, ev, cr2, poste)
    assert len(sur1) == 1 and len(sur2) == 1
    assert sur1 != sur2  # équité : deux bénévoles différents


def test_prefiller_equite_prime_continuite_si_ecart_important(conn):
    # Créneau long (4 h) contigu à un autre : l'écart de charge (4 h) dépasse le
    # rabais de continuité (2 h), donc l'équité l'emporte (autre bénévole).
    ev = services.creer_evenement(conn, "T")
    poste = services.ajouter_poste(conn, ev, "Accueil")
    cr1 = _creneau(conn, ev, "Samedi", 12, 16)  # 4 h
    cr2 = _creneau(conn, ev, "Samedi", 16, 18)  # 2 h, contigu à cr1
    services.definir_besoin(conn, cr1, poste, 1)
    services.definir_besoin(conn, cr2, poste, 1)
    services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1, cr2})
    services.enregistrer_souhaits(conn, ev, "Bob", dispos={cr1, cr2})
    services.prefiller(conn, ev)
    # cr1 (4 h) traité en premier (cases égales -> ordre) ; sur cr2, le titulaire
    # de cr1 a 4 h, l'autre 0 h : malgré la continuité (rabais 2 h), l'autre gagne.
    assert _qui_sur(conn, ev, cr1, poste) != _qui_sur(conn, ev, cr2, poste)


# ===========================================================================
# Édition : remplacer un bénévole, modifier un créneau (durée)
# ===========================================================================
def test_remplacer_affectation(conn):
    ev, poste, cr1, _ = _evenement_simple(conn)
    services.definir_besoin(conn, cr1, poste, 1)
    ra = services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr1})
    rb = services.enregistrer_souhaits(conn, ev, "Bob", dispos={cr1})
    aff = services.affecter(conn, cr1, poste, ra["id"], origine="manuel", verrouille=True)
    new = services.remplacer_affectation(conn, aff, rb["id"])
    assert new is not None
    sur = services.affectations_de_case(conn, cr1, poste)
    assert len(sur) == 1 and sur[0]["id_benevole"] == rb["id"]
    assert sur[0]["verrouille"] == 1  # le verrouillage est conservé


def test_affecter_refuse_deux_postes_meme_creneau(conn):
    # Une personne ne peut pas tenir deux postes sur le même créneau (ajout manuel).
    ev = services.creer_evenement(conn, "T")
    accueil = services.ajouter_poste(conn, ev, "Accueil")
    bar = services.ajouter_poste(conn, ev, "Bar")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    ra = services.enregistrer_souhaits(conn, ev, "Alice")
    assert services.affecter(conn, cr, accueil, ra["id"]) is not None
    # Même créneau, autre poste -> refusé.
    assert services.affecter(conn, cr, bar, ra["id"]) is None
    assert len(services.affectations_de_case(conn, cr, bar)) == 0


def test_remplacer_refuse_si_deja_sur_le_creneau(conn):
    # Remplacer par quelqu'un déjà placé ailleurs sur le créneau est refusé,
    # sans perdre l'affectation d'origine.
    ev = services.creer_evenement(conn, "T")
    accueil = services.ajouter_poste(conn, ev, "Accueil")
    bar = services.ajouter_poste(conn, ev, "Bar")
    cr = _creneau(conn, ev, "Samedi", 14, 16)
    ra = services.enregistrer_souhaits(conn, ev, "Alice")
    rb = services.enregistrer_souhaits(conn, ev, "Bob")
    aff_a = services.affecter(conn, cr, accueil, ra["id"])
    services.affecter(conn, cr, bar, rb["id"])
    # Remplacer Alice (Accueil) par Bob, déjà au Bar sur ce créneau -> refus.
    assert services.remplacer_affectation(conn, aff_a, rb["id"]) is None
    sur = services.affectations_de_case(conn, cr, accueil)
    assert len(sur) == 1 and sur[0]["id_benevole"] == ra["id"]  # Alice conservée


def test_modifier_creneau_duree(conn):
    ev = services.creer_evenement(conn, "T")
    cr = _creneau(conn, ev, "Samedi", 14, 16)  # 2 h
    assert services.duree_heures(services.get_creneau(conn, cr)) == 2.0
    ok = services.modifier_creneau(conn, cr, fin_local="2026-09-12T18:00")  # -> 4 h
    assert ok is True
    assert services.duree_heures(services.get_creneau(conn, cr)) == 4.0
    # Borne invalide -> refus, créneau inchangé.
    assert services.modifier_creneau(conn, cr, debut_local="n'importe quoi") is False
    assert services.duree_heures(services.get_creneau(conn, cr)) == 4.0


# ===========================================================================
# Routes — via TestClient, bases temporaires séparées
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")

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


def _login_admin(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})


def test_route_public_vide(client):
    r = client.get("/planning")
    assert r.status_code == 200


def test_route_admin_protegee(client):
    # Sans connexion admin -> redirection vers /admin.
    r = client.get("/planning/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_flux_demo_publie_et_exports(client):
    _login_admin(client)
    # Crée la démo (redirige vers la gestion de l'événement).
    r = client.post("/planning/admin/demo", follow_redirects=False)
    assert r.status_code == 303
    ev = int(r.headers["location"].rsplit("/", 1)[-1])

    # L'écran de gestion répond.
    assert client.get(f"/planning/admin/{ev}").status_code == 200
    # La page publique montre le planning publié (un nom de poste y figure).
    pub = client.get("/planning")
    assert pub.status_code == 200 and "Accueil" in pub.text
    # Exports.
    x = client.get(f"/planning/admin/{ev}/export.xlsx")
    assert x.status_code == 200 and "spreadsheet" in x.headers["content-type"]
    p = client.get(f"/planning/admin/{ev}/export.pdf")
    assert p.status_code == 200 and p.headers["content-type"] == "application/pdf"
    assert p.content[:4] == b"%PDF"


def test_flux_collecte_publique(client):
    _login_admin(client)
    # Crée un événement vide (état collecte) + une trame minimale.
    r = client.post("/planning/admin/creer", data={"nom": "Test"},
                    follow_redirects=False)
    ev = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/planning/admin/{ev}/poste", data={"nom": "Accueil"})
    client.post(f"/planning/admin/{ev}/creneau",
                data={"libelle_jour": "Samedi", "debut": "2026-09-12T14:00",
                      "fin": "2026-09-12T16:00", "type_creneau": "poste"})

    # Le formulaire public répond et l'envoi redirige vers la confirmation.
    assert client.get(f"/planning/collecte/{ev}").status_code == 200
    from app.planning import services
    from app.planning.db import get_connection
    conn = get_connection()
    try:
        cr = services.lister_creneaux(conn, ev)[0]["id_creneau"]
        po = services.lister_postes(conn, ev)[0]["id_poste"]
    finally:
        conn.close()
    rep = client.post(f"/planning/collecte/{ev}",
                      data={"nom": "Alice", "dispo": str(cr),
                            f"pref_{po}": "prefere"}, follow_redirects=False)
    assert rep.status_code == 303 and "/merci?code=" in rep.headers["location"]


def test_route_aide(client):
    r = client.get("/planning/aide")
    assert r.status_code == 200 and "mode d'emploi" in r.text


def test_route_edition_case_et_creneau(client):
    _login_admin(client)
    # Événement minimal avec une case à pourvoir.
    r = client.post("/planning/admin/creer", data={"nom": "Test"},
                    follow_redirects=False)
    ev = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/planning/admin/{ev}/poste", data={"nom": "Accueil"})
    client.post(f"/planning/admin/{ev}/creneau",
                data={"libelle_jour": "Samedi", "debut": "2026-09-12T14:00",
                      "fin": "2026-09-12T16:00", "type_creneau": "poste"})
    from app.planning import services
    from app.planning.db import get_connection
    conn = get_connection()
    try:
        cr = services.lister_creneaux(conn, ev)[0]["id_creneau"]
        po = services.lister_postes(conn, ev)[0]["id_poste"]
        services.definir_besoin(conn, cr, po, 1)
        ra = services.enregistrer_souhaits(conn, ev, "Alice")  # refusée si fermé ?
    finally:
        conn.close()

    # Page d'édition de la case répond.
    page = client.get(f"/planning/admin/{ev}/case/{cr}/{po}")
    assert page.status_code == 200 and "Accueil" in page.text

    # Page d'édition du créneau répond, et la modification d'horaire fonctionne.
    assert client.get(f"/planning/admin/{ev}/creneau/{cr}/editer").status_code == 200
    rmod = client.post(f"/planning/admin/{ev}/creneau/{cr}/editer",
                       data={"libelle_jour": "Samedi", "debut": "2026-09-12T14:00",
                             "fin": "2026-09-12T17:00"}, follow_redirects=False)
    assert rmod.status_code == 303
    conn = get_connection()
    try:
        assert services.duree_heures(services.get_creneau(conn, cr)) == 3.0
    finally:
        conn.close()


def test_route_affecter_avec_retour(client):
    _login_admin(client)
    r = client.post("/planning/admin/creer", data={"nom": "Test"},
                    follow_redirects=False)
    ev = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/planning/admin/{ev}/poste", data={"nom": "Accueil"})
    client.post(f"/planning/admin/{ev}/creneau",
                data={"libelle_jour": "Samedi", "debut": "2026-09-12T14:00",
                      "fin": "2026-09-12T16:00", "type_creneau": "poste"})
    from app.planning import services
    from app.planning.db import get_connection
    conn = get_connection()
    try:
        cr = services.lister_creneaux(conn, ev)[0]["id_creneau"]
        po = services.lister_postes(conn, ev)[0]["id_poste"]
        services.definir_besoin(conn, cr, po, 1)
        b = services.enregistrer_souhaits(conn, ev, "Alice")["id"]
    finally:
        conn.close()
    retour = f"/planning/admin/{ev}/case/{cr}/{po}"
    rep = client.post(f"/planning/admin/{ev}/affecter",
                      data={"id_creneau": cr, "id_poste": po, "id_benevole": b,
                            "retour": retour}, follow_redirects=False)
    assert rep.status_code == 303 and rep.headers["location"] == retour


def test_route_mon_planning_ics(client):
    _login_admin(client)
    r = client.post("/planning/admin/creer", data={"nom": "Test"},
                    follow_redirects=False)
    ev = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/planning/admin/{ev}/poste", data={"nom": "Accueil"})
    client.post(f"/planning/admin/{ev}/creneau",
                data={"libelle_jour": "Samedi", "debut": "2026-09-12T14:00",
                      "fin": "2026-09-12T16:00", "type_creneau": "poste"})
    from app.planning import services
    from app.planning.db import get_connection
    conn = get_connection()
    try:
        cr = services.lister_creneaux(conn, ev)[0]["id_creneau"]
        po = services.lister_postes(conn, ev)[0]["id_poste"]
        services.definir_besoin(conn, cr, po, 1)
        r = services.enregistrer_souhaits(conn, ev, "Alice", dispos={cr})
        code = r["code"]
    finally:
        conn.close()

    # Code invalide -> 404, jamais d'erreur brute.
    assert client.get("/planning/mon.ics?code=inconnu").status_code == 404
    # Aucune affectation pour l'instant -> 404 également.
    assert client.get(f"/planning/mon.ics?code={code}").status_code == 404

    conn = get_connection()
    try:
        services.prefiller(conn, ev)
    finally:
        conn.close()

    rep = client.get(f"/planning/mon.ics?code={code}")
    assert rep.status_code == 200
    assert rep.headers["content-type"].startswith("text/calendar")
    assert "attachment" in rep.headers["content-disposition"]
    assert rep.text.count("BEGIN:VEVENT") == 1
