"""Tests du peuplement de données du mode formation (app/formation.py)."""

import pytest


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Bases de prêt + tournois + planning temporaires, isolées par test."""
    chemin_pret = tmp_path / "pret-jeux.db"
    chemin_tournoi = tmp_path / "tournoi.db"
    chemin_planning = tmp_path / "planning.db"

    monkeypatch.setenv("DATABASE_PATH", str(chemin_pret))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(chemin_tournoi))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(chemin_planning))
    # Force le repli sur la liste intégrée (pas de lecture du vrai catalogue du
    # dépôt) : rend les tests déterministes et sans dépendance externe.
    monkeypatch.setenv("FORMATION_SOURCE_DB", str(tmp_path / "inexistant.db"))

    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: chemin_pret)
    monkeypatch.setattr(tdb, "get_database_path", lambda: chemin_tournoi)
    monkeypatch.setattr(pdb, "get_database_path", lambda: chemin_planning)

    db.init_db()
    tdb.init_db()
    pdb.init_db()

    return {"pret": chemin_pret, "tournoi": chemin_tournoi, "planning": chemin_planning}


def test_noms_jeux_replis_sur_liste_integree(bases):
    """Sans catalogue source lisible, les noms viennent de la liste de secours."""
    from app import formation

    noms = formation.noms_jeux_formation(formation.NB_JEUX)
    assert len(noms) == formation.NB_JEUX
    # Tous distincts et tirés du repli (aucun libellé numéroté n'est nécessaire
    # tant que la liste de secours est plus longue que NB_JEUX).
    assert len(set(noms)) == formation.NB_JEUX
    assert all(n in formation._NOMS_SECOURS for n in noms)


def _termines_attendus():
    from app import formation
    return (formation.NB_JEUX - formation.NB_PRETS_EN_COURS) + formation.NB_TITRES_DOUBLE_PRET


def test_peupler_pret_compte_correct(bases):
    from datetime import datetime

    from app import formation
    from app.db import get_connection

    nb_jeux = formation.NB_JEUX
    nb_en_cours = formation.NB_PRETS_EN_COURS
    nb_termines = _termines_attendus()

    conn = get_connection()
    try:
        resume = formation.peupler_pret(conn)
        assert resume == {"jeux": nb_jeux, "prets_en_cours": nb_en_cours,
                          "prets_termines": nb_termines}

        nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
        nb_ex = conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
        assert nb_titres == nb_jeux and nb_ex == nb_jeux

        sortis = conn.execute(
            "SELECT COUNT(*) FROM prets WHERE date_retour IS NULL"
        ).fetchone()[0]
        rendus = conn.execute(
            "SELECT COUNT(*) FROM prets WHERE date_retour IS NOT NULL"
        ).fetchone()[0]
        assert sortis == nb_en_cours and rendus == nb_termines

        # Prêts en cours : pochette attribuée + pochette marquée occupée.
        assert conn.execute(
            "SELECT COUNT(*) FROM prets "
            "WHERE date_retour IS NULL AND numero_pochette IS NOT NULL"
        ).fetchone()[0] == nb_en_cours
        assert conn.execute(
            "SELECT COUNT(*) FROM pochettes WHERE occupe = 1"
        ).fetchone()[0] == nb_en_cours
        # Prêts terminés : numéro de pochette effacé (règle D5).
        assert conn.execute(
            "SELECT COUNT(*) FROM prets "
            "WHERE date_retour IS NOT NULL AND numero_pochette IS NOT NULL"
        ).fetchone()[0] == 0

        # Durées variées : au moins un prêt court (<= 20 min) et un long (>= 2 h).
        durees_min = []
        for depart, retour in conn.execute(
            "SELECT date_sortie, date_retour FROM prets WHERE date_retour IS NOT NULL"
        ):
            d = (datetime.fromisoformat(retour) - datetime.fromisoformat(depart))
            durees_min.append(d.total_seconds() / 60)
        assert min(durees_min) <= 20
        assert max(durees_min) >= 120

        # Noms réels (issus du repli ici), jamais les libellés numérotés.
        noms = [r[0] for r in conn.execute("SELECT nom FROM titres").fetchall()]
        assert all(not n.startswith("Jeu d'essai n°") for n in noms)
        assert all(n in formation._NOMS_SECOURS for n in noms)
    finally:
        conn.close()


def test_peupler_pret_idempotent(bases):
    from app import formation
    from app.db import get_connection

    nb_jeux = formation.NB_JEUX
    nb_prets_total = formation.NB_PRETS_EN_COURS + _termines_attendus()

    conn = get_connection()
    try:
        formation.peupler_pret(conn)
        formation.peupler_pret(conn)

        nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
        nb_prets = conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
        # Pas d'accumulation d'une passe à l'autre : la base est vidée avant.
        assert nb_titres == nb_jeux
        assert nb_prets == nb_prets_total
    finally:
        conn.close()


def test_peupler_tournoi_couvre_etats_et_modes(bases):
    from app import formation
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        resume = formation.peupler_tournoi(conn)
        assert resume == {"tournois": 7, "inscrits": 33}

        nb_tournois = conn.execute("SELECT COUNT(*) FROM tournois").fetchone()[0]
        nb_inscrits = conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0]
        assert nb_tournois == 7
        assert nb_inscrits == 33

        # Variété des états représentés.
        etats = {r[0] for r in conn.execute("SELECT DISTINCT etat FROM tournois")}
        assert {"brouillon", "inscriptions", "lance", "termine"} <= etats

        # Variété des modes de scoring des tournois lancés/terminés.
        modes = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT mode_scoring FROM tournois "
                "WHERE mode_scoring IS NOT NULL"
            )
        }
        assert {"high_score", "ronde_suisse", "elimination"} <= modes

        # Un tournoi par équipes existe.
        nb_equipes = conn.execute(
            "SELECT COUNT(*) FROM tournois WHERE par_equipes = 1"
        ).fetchone()[0]
        assert nb_equipes == 1
    finally:
        conn.close()


def test_peupler_tournoi_idempotent(bases):
    from app import formation
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_tournoi(conn)
        formation.peupler_tournoi(conn)
        nb_tournois = conn.execute("SELECT COUNT(*) FROM tournois").fetchone()[0]
        nb_inscrits = conn.execute("SELECT COUNT(*) FROM inscriptions").fetchone()[0]
        # Pas d'accumulation : la base est vidée avant chaque passe.
        assert nb_tournois == 7
        assert nb_inscrits == 33
    finally:
        conn.close()


def test_peupler_planning(bases):
    from app import formation
    from app.planning.db import get_connection

    conn = get_connection()
    try:
        resume = formation.peupler_planning(conn)
        assert resume["planning_evenements"] == 2
        assert resume["benevoles"] > 0

        nb_ev = conn.execute("SELECT COUNT(*) FROM evenements").fetchone()[0]
        nb_aff = conn.execute("SELECT COUNT(*) FROM affectations").fetchone()[0]
        assert nb_ev == 2
        # Le préremplissage a placé des bénévoles.
        assert nb_aff > 0
    finally:
        conn.close()


def test_peupler_planning_idempotent(bases):
    from app import formation
    from app.planning.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_planning(conn)
        formation.peupler_planning(conn)
        # Pas d'accumulation : toujours 2 événements après deux passes.
        nb_ev = conn.execute("SELECT COUNT(*) FROM evenements").fetchone()[0]
        assert nb_ev == 2
    finally:
        conn.close()


def test_peupler_orchestre_les_trois_bases(bases):
    from app import formation

    resume = formation.peupler()
    assert resume["jeux"] == formation.NB_JEUX
    assert resume["prets_en_cours"] == formation.NB_PRETS_EN_COURS
    assert resume["tournois"] == 7
    assert resume["inscrits"] == 33
    assert resume["programme"] == 4
    assert resume["planning_evenements"] == 2
    assert resume["benevoles"] > 0


# ---------------------------------------------------------------------------
# Programme du week-end (même base que les tournois)
# ---------------------------------------------------------------------------
def test_peupler_programme_couvre_etats_et_surfaces(bases):
    from app import formation
    from app.tournoi import programme
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        assert formation.peupler_programme(conn) == {"programme": 4}

        lignes = conn.execute(
            "SELECT intitule, etat, date_heure, id_type FROM programme"
        ).fetchall()
        assert len(lignes) == 4
        # Les trois états sont représentés : ce que le public voit, ce qu'il ne
        # voit pas, et ce qui est annoncé mais annulé.
        assert {r["etat"] for r in lignes} == {"publie", "brouillon", "annule"}
        # Tous datés (un élément sans date n'apparaît sur aucune surface).
        assert all(r["date_heure"] for r in lignes)
        # Rattachés aux types amorcés par défaut.
        assert all(r["id_type"] is not None for r in lignes)

        # Au moins un élément publié tombe dans la fenêtre de l'accueil (1 h)
        # ET dans celle de l'écran de salle : sans lui, la nouveauté du module
        # serait invisible sur le site de formation.
        proches = [
            e for e in programme.imminents(conn, 60)
            if e["source"] == "programme"
        ]
        assert proches, "aucun élément de programme imminent : rien à montrer"
        # Un annulé est annoncé sur l'écran de salle (barré), pas ailleurs.
        with_annules = programme.imminents(conn, 120, inclure_annules=True)
        assert any(e.get("etat") == "annule" for e in with_annules)
    finally:
        conn.close()


def test_peupler_programme_idempotent(bases):
    # Le cycle complet (vider + repeupler) ne cumule pas les éléments.
    from app import formation
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_tournoi(conn)
        formation.peupler_programme(conn)
        formation.peupler_tournoi(conn)
        formation.peupler_programme(conn)
        assert conn.execute("SELECT COUNT(*) FROM programme").fetchone()[0] == 4
        # Les types (configuration amorcée, pas des données d'exemple) ne sont
        # jamais vidés ni dupliqués par le peuplement.
        assert conn.execute("SELECT COUNT(*) FROM types_programme").fetchone()[0] == 5
    finally:
        conn.close()


def test_peupler_tournoi_vide_aussi_le_programme(bases):
    # Piège d'ordre : peupler_tournoi vide la base des tournois, programme
    # compris. Si l'ordre des deux appels était inversé dans `peupler`, les
    # éléments seraient effacés juste après leur création.
    from app import formation
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_programme(conn)
        formation.peupler_tournoi(conn)
        assert conn.execute("SELECT COUNT(*) FROM programme").fetchone()[0] == 0
    finally:
        conn.close()


def test_peupler_pret_regle_la_date_evenement(bases):
    # Sans `evenement_date`, la frise de l'accueil et la page /programme sont
    # vides quoi qu'on saisisse : sur un site de formation, ce serait un écran
    # mort. (Manque préexistant, découvert en branchant le module Programme.)
    from datetime import datetime

    from app import formation, services
    from app.db import get_connection
    from app.services import FUSEAU_LOCAL

    conn = get_connection()
    try:
        formation.peupler_pret(conn)
        attendu = datetime.now(FUSEAU_LOCAL).date().isoformat()
        assert services.lire_parametre(conn, "evenement_date") == attendu
    finally:
        conn.close()
