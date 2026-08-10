"""
Accès SIMULTANÉS au prêt — la course d'attribution des numéros de pochette.

CE QUE CES TESTS PROTÈGENT
--------------------------
Le test de charge du 30 juillet 2026 (docs/protocole-stress-test.md § 2) a
reproduit trois défauts qui partagent un seul motif : on lit un état, puis on
agit dessus, sans que rien n'empêche une autre requête de lire le même état
entre les deux. Huit prêts simultanés sont repartis avec la MÊME pochette n°12,
et huit prêts ont été ouverts sur une SEULE boîte — sans le moindre message
d'erreur. Traduit sur le stand : huit pièces d'identité dans un casier prévu
pour une, et une restitution qui part de travers en fin de soirée.

DEUX NIVEAUX DE TEST, VOLONTAIREMENT
-------------------------------------
- DÉTERMINISTE (`test_reservation_indivisible`) : l'entrelacement est FORCÉ,
  pas espéré. Une connexion réserve un numéro sans committer, l'autre tente
  d'écrire et doit se heurter au verrou. Ce test-là ne dépend d'aucun
  ordonnancement : c'est lui qui protège la correction dans la durée.
- STOCHASTIQUE (les tests à plusieurs fils) : plus proche du réel, mais un test
  qui compte sur le hasard de l'ordonnancement passe au vert un jour sur deux.
  Il vient EN PLUS du déterministe, jamais à sa place.

POURQUOI DES BASES SUR FICHIER
-------------------------------
Le reste de la suite travaille sur `:memory:`, ce qui est parfait pour de la
logique métier — mais inutilisable ici : deux connexions `:memory:` ouvrent
deux bases DIFFÉRENTES, donc ne se disputent aucun verrou. La concurrence ne
se teste que sur un vrai fichier.
"""

import sqlite3
import threading

import pytest

from app import db, models, services


# Délai d'attente TRÈS court pour les connexions de test : quand on veut
# constater qu'une écriture est bloquée, on ne va pas patienter les 15 s de
# production (db.TIMEOUT_ECRITURE_S).
TIMEOUT_COURT_S = 0.3


def _connexion(chemin, timeout=TIMEOUT_COURT_S):
    """Ouvre une connexion configurée comme celles de l'application."""
    conn = sqlite3.connect(chemin, timeout=timeout)
    conn.row_factory = sqlite3.Row
    for pragma in models.PRAGMAS:
        conn.execute(pragma)
    return conn


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base sur fichier, schéma courant, 12 boîtes disponibles, aucun prêt."""
    chemin = tmp_path / "pret-jeux.db"
    monkeypatch.setenv("DATABASE_PATH", str(chemin))
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
        [(f"{n:03d}",) for n in range(1, 13)],
    )
    conn.commit()
    conn.close()
    return chemin


def _en_salve(nb_fils, travail):
    """
    Lance `travail(i)` dans `nb_fils` fils qui partent TOUS AU MÊME INSTANT.

    La barrière est le cœur du dispositif : sans elle, les fils démarrent en
    file indienne et ne se croisent jamais — le test passerait au vert même
    avec le défaut. Chaque fil ouvre SA connexion, comme le fait une requête
    HTTP réelle.

    Returns:
        La liste des valeurs renvoyées par `travail`, ou l'exception attrapée.
    """
    depart = threading.Barrier(nb_fils)
    resultats = [None] * nb_fils

    def executer(indice):
        depart.wait()
        try:
            resultats[indice] = travail(indice)
        except Exception as erreur:      # remontée au test, jamais avalée
            resultats[indice] = erreur

    fils = [threading.Thread(target=executer, args=(i,)) for i in range(nb_fils)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=30)
    return resultats


# ===========================================================================
# NIVEAU 1 — DÉTERMINISTE : l'entrelacement est forcé
# ===========================================================================
def test_le_verrou_est_pris_avant_toute_lecture(base):
    """
    LE test de cette correction, et le seul qui la discrimine vraiment.

    Tout le défaut tient à ceci : la LECTURE du « plus petit numéro libre »
    n'était protégée par rien. Une fois l'écriture commencée, SQLite verrouille
    de lui-même — donc un test qui n'observe que le moment de l'écriture passe
    au vert AVEC OU SANS le correctif (vérifié : neutraliser le BEGIN IMMEDIATE
    ne le fait pas tomber). La propriété à vérifier est donc plus tôt : entrer
    dans le bloc, AVANT d'avoir lu ou écrit quoi que ce soit, doit DÉJÀ
    interdire à un autre bénévole d'écrire.

    C'est exactement ce que `BEGIN IMMEDIATE` ajoute, et c'est ce qui manquait :
    sans lui, entrer dans le bloc ne fait rien, les deux lectures voient le même
    état, et les deux prêts repartent avec la même pochette.
    """
    a = _connexion(base)
    b = _connexion(base)
    try:
        with services.transaction(a):
            # Rien n'a encore été lu ni écrit dans ce bloc — et pourtant B doit
            # déjà se heurter au verrou.
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                b.execute("BEGIN IMMEDIATE")
    finally:
        a.close()
        b.close()


def test_reservation_indivisible(base):
    """
    Une réservation en cours interdit à quiconque d'obtenir le même numéro, et
    le numéro suivant est bien attribué une fois la première transaction close.

    Complément du test ci-dessus : on force ici le moment « A a réservé, B
    arrive » et on vérifie l'issue des deux côtés, plutôt que le seul verrou.
    """
    a = _connexion(base)
    b = _connexion(base)
    try:
        with services.transaction(a):
            numero_a = services.plus_petit_numero_libre(a)   # réservé, pas encore committé
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                services.preter(b, "002")
        # A a committé en sortant du bloc : B peut enfin écrire, et obtient
        # forcément un AUTRE numéro.
        numero_b = services.preter(b, "002")
        assert numero_a == 1
        assert numero_b == 2
    finally:
        a.close()
        b.close()


def test_lecture_publique_pas_genee_par_une_ecriture(base):
    """
    Le verrou d'écriture ne doit pas bloquer les LECTEURS : le catalogue, les
    fiches et l'écran de salle sont publics et ne doivent jamais attendre
    qu'un bénévole finisse d'enregistrer un prêt. C'est ce que garantit le
    mode WAL, et c'est la contrepartie à vérifier après avoir posé des verrous.
    """
    a = _connexion(base)
    lecteur = _connexion(base)
    try:
        with services.transaction(a):
            services.plus_petit_numero_libre(a)
            # Lecture pendant l'écriture : passe sans attendre, et voit encore
            # l'état d'avant le commit.
            assert lecteur.execute("SELECT COUNT(*) FROM prets").fetchone()[0] == 0
    finally:
        a.close()
        lecteur.close()


# ===========================================================================
# NIVEAU 2 — STOCHASTIQUE : des fils qui partent ensemble
# ===========================================================================
def test_salve_sur_boites_distinctes_numeros_tous_differents(base):
    """
    Huit prêts simultanés sur huit boîtes différentes : huit numéros DISTINCTS.

    C'est le scénario A du test de charge, celui qui a produit huit fois la
    pochette n°12. Répété assez de fois pour être significatif ; chaque manche
    rend les boîtes, de sorte que les numéros sont recyclés et que la manche
    suivante repart dans la configuration la plus propice au défaut (tout le
    monde vise le même « plus petit libre »).
    """
    boites = [f"{n:03d}" for n in range(1, 9)]

    def preter(indice):
        conn = db.get_connection()
        try:
            return services.preter(conn, boites[indice])
        finally:
            conn.close()

    for manche in range(12):
        numeros = _en_salve(len(boites), preter)
        erreurs = [n for n in numeros if isinstance(n, Exception)]
        assert not erreurs, f"manche {manche} : {erreurs}"
        assert len(set(numeros)) == len(boites), (
            f"manche {manche} : numéros attribués {sorted(numeros)} — "
            "une pochette a été donnée à plusieurs boîtes"
        )
        conn = db.get_connection()
        try:
            for boite in boites:
                services.rendre(conn, boite)
        finally:
            conn.close()


def test_salve_sur_une_seule_boite_un_seul_pret_ouvert(base):
    """
    Huit appuis simultanés sur LA MÊME boîte : un seul prêt ouvert, les sept
    autres repartent avec « déjà sortie ».

    C'est le scénario B, celui où le contrôle « cette boîte est-elle déjà
    sortie ? » était franchi par tout le monde. Le test de charge ouvrait huit
    prêts d'un coup — et, les deux défauts se composant, tous avec la même
    pochette.
    """
    def preter(_indice):
        conn = db.get_connection()
        try:
            return services.preter_si_disponible(conn, "001")
        finally:
            conn.close()

    resultats = _en_salve(8, preter)
    erreurs = [r for r in resultats if isinstance(r, Exception)]
    assert not erreurs, erreurs
    ouverts = [r for r in resultats if not r.get("deja_sorti")]
    assert len(ouverts) == 1, f"{len(ouverts)} prêts ouverts au lieu d'un seul"

    conn = db.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM prets WHERE id_exemplaire = '001' "
            "AND date_retour IS NULL"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_salve_a_pochettes_vides_aucune_erreur(base):
    """
    Salve sur une table `pochettes` VIDE — la configuration de l'ouverture de
    soirée, celle qui produisait des erreurs 500 plutôt que des doublons
    silencieux : toutes les requêtes calculaient le même `MAX + 1` et
    l'inséraient, la clé primaire refusait les doublons.

    On vérifie ici l'absence d'exception autant que l'unicité des numéros.
    """
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM pochettes").fetchone()[0] == 0
    finally:
        conn.close()

    boites = [f"{n:03d}" for n in range(1, 9)]

    def preter(indice):
        conn = db.get_connection()
        try:
            return services.preter(conn, boites[indice])
        finally:
            conn.close()

    numeros = _en_salve(len(boites), preter)
    assert not [n for n in numeros if isinstance(n, Exception)]
    assert sorted(numeros) == list(range(1, 9))


def test_sorties_tournoi_simultanees_partagent_le_marqueur_zero(base):
    """
    NON-RÉGRESSION. Les sorties tournoi portent toutes `numero_pochette = 0`
    (services.NUMERO_TOURNOI, « sans pochette ») : plusieurs sorties
    simultanées DOIVENT pouvoir coexister. C'est ce que garantit la clause
    `numero_pochette <> 0` de l'index UNIQUE — sans elle, le filet de sécurité
    refuserait une situation parfaitement légitime.
    """
    boites = [f"{n:03d}" for n in range(1, 7)]

    def sortir(indice):
        conn = db.get_connection()
        try:
            return services.sortir_tournoi_si_disponible(conn, boites[indice])
        finally:
            conn.close()

    resultats = _en_salve(len(boites), sortir)
    assert not [r for r in resultats if isinstance(r, Exception)]

    conn = db.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM prets WHERE motif = 'tournoi' AND date_retour IS NULL"
        ).fetchone()[0] == len(boites)
    finally:
        conn.close()


# ===========================================================================
# NON-RÉGRESSIONS — les pièges de la mise en œuvre
# ===========================================================================
def test_repreter_fonctionne_avec_une_transaction_deja_ouverte(base):
    """
    `repreter()` écrit (clôture de l'ancien prêt) PUIS appelle `preter()`. Un
    `BEGIN IMMEDIATE` posé naïvement dans `preter()` lèverait ici « cannot
    start a transaction within a transaction ». C'est le piège n°1 de cette
    correction : le gestionnaire `services.transaction` doit se greffer sur une
    transaction en cours au lieu d'en ouvrir une seconde.
    """
    conn = db.get_connection()
    try:
        ancien = services.preter(conn, "001")
        res = services.repreter(conn, "001")
        assert res["ancien_numero"] == ancien
        # `nouveau_numero` vaut ici le MÊME numéro : le re-prêt libère l'ancien,
        # que `preter` recycle aussitôt puisqu'il redevient le plus petit libre.
        # C'est la règle métier (spec §6), pas un effet de bord de la
        # transaction — ce qui compte est qu'un numéro ait bien été attribué.
        assert res["nouveau_numero"] >= 1
        # L'ancienne ligne est close et son numéro effacé (fiche D5), la
        # nouvelle est ouverte : le re-prêt n'a pas été coupé en deux.
        lignes = conn.execute(
            "SELECT numero_pochette, date_retour FROM prets "
            "WHERE id_exemplaire = '001' ORDER BY id_pret"
        ).fetchall()
        assert len(lignes) == 2
        assert lignes[0]["numero_pochette"] is None and lignes[0]["date_retour"]
        assert lignes[1]["numero_pochette"] == res["nouveau_numero"]
        assert lignes[1]["date_retour"] is None
        # Le tout a bien été COMMITTÉ : une autre connexion doit le voir.
        autre = _connexion(base)
        try:
            assert autre.execute(
                "SELECT COUNT(*) FROM prets WHERE id_exemplaire = '001' "
                "AND date_retour IS NULL"
            ).fetchone()[0] == 1
        finally:
            autre.close()
    finally:
        conn.close()


def test_rendre_affiche_le_numero_avant_de_l_effacer(base, vieillir_prets):
    """
    NON-RÉGRESSION du geste central (fiche D5) : le retour doit renvoyer le
    numéro de pochette — le bénévole en a besoin pour retrouver la pièce
    d'identité — tout en l'effaçant de la ligne close.
    """
    conn = db.get_connection()
    try:
        numero = services.preter(conn, "001")
        vieillir_prets(conn)
        res = services.rendre(conn, "001")
        assert res == {"numero_libere": numero, "motif": "pret", "erreur": False}
        assert conn.execute(
            "SELECT numero_pochette FROM prets WHERE id_exemplaire = '001'"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_cloturer_tous_les_prets_libere_tout(base):
    """NON-RÉGRESSION : la clôture de fin d'événement reste transactionnelle."""
    conn = db.get_connection()
    try:
        services.preter(conn, "001")
        services.preter(conn, "002")
        services.sortir_tournoi(conn, "003")
        assert services.cloturer_tous_les_prets(conn) == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM prets WHERE date_retour IS NULL"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM pochettes WHERE occupe = 1"
        ).fetchone()[0] == 0
    finally:
        conn.close()


# ===========================================================================
# LE FILET DE SÉCURITÉ ET LA BASE DÉJÀ INCOHÉRENTE
# ===========================================================================
def test_index_uniques_poses_sur_une_base_saine(base):
    conn = db.get_connection()
    try:
        noms = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )}
    finally:
        conn.close()
    assert "idx_prets_un_seul_ouvert" in noms
    assert "idx_pochettes_un_seul_pret" in noms


def test_init_db_ne_tombe_pas_sur_une_base_deja_incoherente(tmp_path, monkeypatch):
    """
    Une base qui a tourné AVANT ce correctif peut contenir deux prêts ouverts
    partageant une pochette : le `CREATE UNIQUE INDEX` refuse alors de se
    créer. Or `init_db()` tourne au DÉMARRAGE de l'application et après une
    RESTAURATION de sauvegarde — lever à cet endroit mettrait le site par terre,
    ou casserait une restauration en pleine soirée.

    Comportement attendu : on avertit, on continue, et on ne touche À AUCUNE
    donnée (aucune réparation automatique : la base ne peut pas savoir quelle
    pièce d'identité est dans quel casier).
    """
    chemin = tmp_path / "incoherente.db"
    monkeypatch.setenv("DATABASE_PATH", str(chemin))
    conn = db.get_connection()
    for instruction in models.SCHEMA_STATEMENTS:
        conn.executescript(instruction)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
        [("001",), ("002",)],
    )
    conn.executemany(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)"
        " VALUES (?, ?, '2026-07-30T20:00:00+00:00', 'pret')",
        [("001", 5), ("002", 5), ("001", 6)],   # pochette partagée ET boîte en double
    )
    conn.commit()
    conn.close()

    db.init_db()        # ne doit PAS lever

    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0] == 3
        noms = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )}
        # Le filet manque, mais l'index de requête est bien là : rien ne ralentit.
        assert "idx_prets_un_seul_ouvert" not in noms
        assert "idx_prets_retour_null" in noms
    finally:
        conn.close()


# ===========================================================================
# CE QUE VOIT LE BÉNÉVOLE — jamais une erreur brute
# ===========================================================================
@pytest.fixture
def client_et_base(tmp_path, monkeypatch):
    """Application complète sur bases temporaires, plus le chemin de la base
    de prêt pour pouvoir la verrouiller depuis le test."""
    chemin = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(chemin))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app), chemin


def test_route_preter_en_cas_de_conflit_message_et_jamais_500(client_et_base, monkeypatch):
    """
    Si le verrou d'écriture n'est pas obtenu à temps, le bénévole doit voir un
    MESSAGE et pouvoir réappuyer — jamais une page d'erreur (règle « ne jamais
    bloquer »). On abaisse le délai d'attente et on tient le verrou depuis une
    autre connexion pour provoquer le cas.

    `TestClient` laisse remonter les exceptions serveur par défaut : si la
    route rendait un 500, ce test lèverait au lieu d'échouer poliment — c'est
    voulu, c'est la garantie qu'on cherche.
    """
    client, chemin = client_et_base
    monkeypatch.setattr(db, "TIMEOUT_ECRITURE_S", 0.2)
    bloqueur = _connexion(chemin, timeout=5)
    bloqueur.execute("BEGIN IMMEDIATE")
    try:
        reponse = client.post("/pret/001/preter")
    finally:
        bloqueur.rollback()
        bloqueur.close()

    assert reponse.status_code == 200
    assert "Rien n'a été enregistré" in reponse.text
    # Et RIEN n'a effectivement été écrit : réappuyer ne créera pas de doublon.
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0] == 0
    finally:
        conn.close()
    # Le bouton d'action est toujours là : la reprise est à un tap.
    assert "/pret/001/preter" in reponse.text


def test_route_rendre_en_cas_de_conflit_message_et_jamais_500(client_et_base, monkeypatch):
    """Même garantie sur le retour, l'autre geste répété de la soirée."""
    client, chemin = client_et_base
    client.post("/pret/001/preter")          # la boîte est sortie
    monkeypatch.setattr(db, "TIMEOUT_ECRITURE_S", 0.2)
    bloqueur = _connexion(chemin, timeout=5)
    bloqueur.execute("BEGIN IMMEDIATE")
    try:
        reponse = client.post("/pret/001/rendre")
    finally:
        bloqueur.rollback()
        bloqueur.close()

    assert reponse.status_code == 200
    assert "Rien n'a été enregistré" in reponse.text
    conn = db.get_connection()
    try:
        # Le prêt est toujours ouvert : le retour n'a pas été enregistré à moitié.
        assert conn.execute(
            "SELECT COUNT(*) FROM prets WHERE date_retour IS NULL"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_creer_index_uniques_signale_les_index_refuses(tmp_path, monkeypatch, caplog):
    """La fonction rend la liste des index refusés et l'écrit au journal."""
    chemin = tmp_path / "incoherente.db"
    monkeypatch.setenv("DATABASE_PATH", str(chemin))
    conn = db.get_connection()
    for instruction in models.SCHEMA_STATEMENTS:
        conn.executescript(instruction)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.executemany(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)"
        " VALUES ('001', ?, '2026-07-30T20:00:00+00:00', 'pret')",
        [(5,), (6,)],       # deux prêts ouverts sur la même boîte
    )
    conn.commit()
    try:
        refuses = db._creer_index_uniques(conn)
    finally:
        conn.close()
    assert "idx_prets_un_seul_ouvert" in refuses
    assert "incohérents" in caplog.text
