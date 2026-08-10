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
    # Idem pour le catalogue importé : par défaut AUCUN CSV, quoi qu'il y ait
    # dans l'environnement de la machine qui lance la suite. Les tests qui
    # veulent l'import le posent eux-mêmes.
    monkeypatch.delenv("FORMATION_CATALOGUE_CSV", raising=False)

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
        # `catalogue` dit d'où viennent les jeux : ici « fictif », faute de
        # FORMATION_CATALOGUE_CSV (clé ajoutée avec l'import de catalogue —
        # test adapté en connaissance de cause, l'égalité était exacte).
        assert resume == {"jeux": nb_jeux, "prets_en_cours": nb_en_cours,
                          "prets_termines": nb_termines, "catalogue": "fictif"}

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


def test_peupler_tournoi_intitules_sans_redondance(bases):
    """
    Les intitulés générés ne répètent ni le mot « Tournoi » (on est dans la
    rubrique Tournois), ni l'état, ni le mode de scoring : l'écran les affiche
    déjà. Ils valent donc le nom du jeu, sauf UN titre spécifique d'exemple.
    """
    from app import formation
    from app.tournoi.db import get_connection

    conn = get_connection()
    try:
        formation.peupler_tournoi(conn)
        lignes = conn.execute("SELECT nom, jeu FROM tournois").fetchall()
    finally:
        conn.close()

    noms = [r[0] for r in lignes]
    interdits = ("tournoi", "high score", "ronde suisse", "élimination",
                 "brouillon", "inscriptions ouvertes", "terminé", "par équipes")
    for nom in noms:
        for mot in interdits:
            assert mot not in nom.casefold(), f"{nom!r} contient {mot!r}"

    # Intitulés tous distincts (sans le suffixe de mode, deux tournois du même
    # jeu seraient indiscernables dans la liste).
    assert len(set(noms)) == len(noms)

    # Six intitulés valent exactement le nom du jeu ; un seul est un titre
    # spécifique, avec son jeu renseigné à part.
    specifiques = [(n, j) for n, j in lignes if n != j]
    assert len(specifiques) == 1
    assert specifiques[0][1]  # le jeu reste renseigné


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


def test_peupler_necrit_rien_dans_le_journal(bases, _journal_isole):
    """
    docs/conception-journal.md §5.2 : `journaliser()` est appelée depuis les
    ROUTES, jamais depuis les services. `app.formation` appelle des services
    (peupler_pret/peupler_tournoi/peupler_programme), pas des routes : le
    peuplement du site de formation ne doit produire AUCUNE ligne, même s'il
    crée des prêts, un tournoi et des éléments de programme.
    """
    from app import formation

    formation.peupler()

    assert not _journal_isole.exists() or _journal_isole.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Catalogue importé (FORMATION_CATALOGUE_CSV)
# ---------------------------------------------------------------------------
# Raison d'être de cette section : les QR collés sur les boîtes encodent
# `<domaine>/jeu/<id_exemplaire>` et le scanner embarqué n'extrait que l'id,
# sans regarder le domaine. Un QR imprimé pour la PRODUCTION scanné depuis le
# site de formation ouvre donc `/pret/<id>` de l'instance de formation — encore
# faut-il que cet identifiant existe dans sa base. C'est ce que ces tests
# vérifient (voir la section « CATALOGUE » en tête d'`app/formation.py`).

def _ecrire_csv_catalogue(chemin, boites):
    """
    Écrit un CSV au format EXACT de l'export `/admin/donnees` de la production
    (mêmes en-têtes, même séparateur) — c'est ce fichier que le bureau dépose.

    Args:
        boites: liste de couples (id_exemplaire, nom de jeu).
    """
    import csv

    from app.services import EN_TETES_CATALOGUE

    with open(chemin, "w", encoding="utf-8", newline="") as fh:
        ecrivain = csv.DictWriter(
            fh, fieldnames=EN_TETES_CATALOGUE, delimiter=";", restval=""
        )
        ecrivain.writeheader()
        for id_ex, nom in boites:
            ecrivain.writerow({"Code jeu": id_ex, "Nom jeu": nom,
                               "Type": "Jeu", "Type jeu": "Ambiance"})


def _catalogue_csv(tmp_path, monkeypatch, boites):
    """Dépose le CSV et pointe FORMATION_CATALOGUE_CSV dessus."""
    chemin = tmp_path / "catalogue-prod.csv"
    _ecrire_csv_catalogue(chemin, boites)
    monkeypatch.setenv("FORMATION_CATALOGUE_CSV", str(chemin))
    return chemin


def test_catalogue_csv_importe_les_vrais_identifiants(bases, tmp_path, monkeypatch):
    from app import formation
    from app.db import get_connection

    _catalogue_csv(tmp_path, monkeypatch, [
        ("00472", "Catan"),        # zéros de tête : jamais réinterprétés
        ("00473", "Catan"),        # 2e boîte du MÊME titre -> un seul titre
        ("A12", "Dixit"),
    ])

    conn = get_connection()
    try:
        resume = formation.peupler_pret(conn)
        assert resume["catalogue"] == "csv"
        assert resume["jeux"] == 3

        ids = {r[0] for r in conn.execute("SELECT id_exemplaire FROM exemplaires")}
        assert ids == {"00472", "00473", "A12"}
        # Les identifiants sont repris TELS QUELS : c'est toute la fonction du
        # dispositif. Un « 472 » au lieu de « 00472 » et le QR n'ouvre rien.
        assert conn.execute(
            "SELECT COUNT(*) FROM exemplaires WHERE id_exemplaire = '00472'"
        ).fetchone()[0] == 1
        # Regroupement par titre, comme à l'import du vrai catalogue.
        assert conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0] == 2
    finally:
        conn.close()


def test_un_qr_de_production_ouvre_bien_la_boite_sur_le_site_de_formation(
    bases, tmp_path, monkeypatch
):
    """
    Le test qui porte le besoin : la chaîne complète « QR imprimé pour la
    production -> écran prêt/retour de l'instance de formation ».

    L'extraction de l'id est refaite ici avec la MÊME expression que
    `app/static/js/scanner.js` (`/\\/jeu\\/([^/?#]+)/`) : le JS n'est pas
    exécutable sous pytest, mais la propriété qu'on veut verrouiller est bien
    que le domaine ne joue AUCUN rôle — seul l'identifiant compte.
    """
    import re

    from app import formation, services
    from app.db import get_connection

    _catalogue_csv(tmp_path, monkeypatch, [("00472", "Catan")])

    conn = get_connection()
    try:
        formation.peupler_pret(conn)

        # Ce que contient l'étiquette déjà collée sur la boîte, imprimée pour
        # le domaine de PRODUCTION.
        contenu_qr = "https://jeux.monasso.fr/jeu/00472"
        id_scanne = re.search(r"/jeu/([^/?#]+)", contenu_qr).group(1)

        # L'écran /pret/<id> de l'instance de formation trouve la boîte
        # (`info_exemplaire` est ce que la route interroge ; None -> 404
        # « boîte inconnue », le symptôme constaté en session réelle).
        assert services.info_exemplaire(conn, id_scanne) is not None
    finally:
        conn.close()


def test_les_prets_fictifs_restent_bornes_et_portent_sur_le_catalogue_importe(
    bases, tmp_path, monkeypatch
):
    from app import formation
    from app.db import get_connection

    # Catalogue plus grand que l'échantillon de prêts : cas du vrai catalogue
    # (~700 boîtes), où prêter une boîte par ligne donnerait des statistiques
    # absurdes et un peuplement inutilement long.
    boites = [(f"{i:05d}", f"Jeu réel {i}") for i in range(formation.NB_JEUX + 20)]
    _catalogue_csv(tmp_path, monkeypatch, boites)

    conn = get_connection()
    try:
        resume = formation.peupler_pret(conn)
        assert resume["jeux"] == len(boites)          # catalogue complet
        assert resume["prets_en_cours"] == formation.NB_PRETS_EN_COURS

        ids_catalogue = {id_ex for id_ex, _ in boites}
        ids_pretes = {r[0] for r in conn.execute(
            "SELECT DISTINCT id_exemplaire FROM prets")}
        assert ids_pretes <= ids_catalogue
        # Échantillon borné : jamais un prêt par boîte du catalogue.
        assert len(ids_pretes) <= formation.NB_JEUX
    finally:
        conn.close()


def test_catalogue_csv_idempotent(bases, tmp_path, monkeypatch):
    from app import formation
    from app.db import get_connection

    _catalogue_csv(tmp_path, monkeypatch,
                   [(f"{i:05d}", f"Jeu réel {i}") for i in range(10)])

    conn = get_connection()
    try:
        formation.peupler_pret(conn)
        resume = formation.peupler_pret(conn)
        # Le vidage précède l'import : ni exemplaires ni prêts ne s'accumulent.
        assert conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0] == 10
        en_cours, termines = _prets_attendus_pour(10)
        assert (resume["prets_en_cours"], resume["prets_termines"]) == (en_cours, termines)
        assert conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0] == (
            en_cours + termines
        )
    finally:
        conn.close()


def _prets_attendus_pour(nb_boites):
    """
    (en cours, terminés) attendus pour un catalogue de `nb_boites`
    (cf. `formation._peupler_prets_dates`). Un catalogue plus petit que
    `NB_PRETS_EN_COURS` n'a pas de quoi remplir les prêts en cours.
    """
    from app import formation

    en_cours = min(nb_boites, formation.NB_PRETS_EN_COURS)
    restants = nb_boites - en_cours
    return en_cours, restants + min(restants, formation.NB_TITRES_DOUBLE_PRET)


def test_catalogue_csv_introuvable_repli_silencieux(bases, tmp_path, monkeypatch, caplog):
    """
    Chemin mal recopié ou fichier pas encore déposé : le site de formation doit
    fonctionner quand même (jeux fictifs), mais l'écart doit être VISIBLE dans
    les journaux du serveur — sans quoi le seul symptôme serait un catalogue de
    60 jeux là où on en attendait 700.
    """
    import logging

    from app import formation
    from app.db import get_connection

    monkeypatch.setenv("FORMATION_CATALOGUE_CSV", str(tmp_path / "pas-la.csv"))

    conn = get_connection()
    try:
        with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
            resume = formation.peupler_pret(conn)
        assert resume["catalogue"] == "fictif"
        assert resume["jeux"] == formation.NB_JEUX
        assert "FORMATION_CATALOGUE_CSV" in caplog.text
    finally:
        conn.close()


def test_catalogue_csv_illisible_ne_plante_jamais(bases, tmp_path, monkeypatch, caplog):
    """
    Un fichier présent mais sans les colonnes clés fait lever `SystemExit` à
    `scripts.import_csv` (comportement voulu pour un script en ligne de
    commande). Non rattrapé, il rendrait 500 sur le bouton « Réinitialiser les
    données de formation » — et `SystemExit` n'hérite pas d'`Exception`.
    """
    import logging

    from app import formation
    from app.db import get_connection

    mauvais = tmp_path / "mauvais.csv"
    mauvais.write_text("Colonne A;Colonne B\n1;2\n", encoding="utf-8")
    monkeypatch.setenv("FORMATION_CATALOGUE_CSV", str(mauvais))

    conn = get_connection()
    try:
        with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
            resume = formation.peupler_pret(conn)
        # Repli complet : un catalogue fictif vaut mieux qu'une base vide.
        assert resume["catalogue"] == "fictif"
        assert resume["jeux"] == formation.NB_JEUX
        assert "catalogue" in caplog.text.lower()
    finally:
        conn.close()


def test_peupler_complet_tire_les_tournois_du_catalogue_importe(
    bases, tmp_path, monkeypatch
):
    """
    Les tournois et le programme portent des noms de JEUX : sur un site de
    formation dont le catalogue est celui de l'association, ils doivent en
    venir, pas de la liste de secours intégrée.
    """
    from app import formation
    from app.db import get_connection
    from app.tournoi.db import get_connection as get_connection_tournoi

    noms = [f"Jeu réel {i}" for i in range(12)]
    _catalogue_csv(tmp_path, monkeypatch,
                   [(f"{i:05d}", nom) for i, nom in enumerate(noms)])

    resume = formation.peupler()
    assert resume["catalogue"] == "csv"
    assert resume["jeux"] == 12

    conn_t = get_connection_tournoi()
    try:
        jeux = {r[0] for r in conn_t.execute(
            "SELECT jeu FROM tournois WHERE jeu IS NOT NULL")}
        assert jeux, "aucun tournoi d'exemple créé"
        assert jeux <= set(noms)
    finally:
        conn_t.close()

    conn = get_connection()
    try:
        # Et le catalogue n'a pas été rejoué au passage (peupler_pret n'est
        # appelé qu'une fois par `peupler`).
        assert conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0] == 12
    finally:
        conn.close()
