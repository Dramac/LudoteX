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
