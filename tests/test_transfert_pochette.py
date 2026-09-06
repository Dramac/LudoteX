"""
Transfert de pochette — rendre une boîte et en prêter une autre sans déplacer
la pièce d'identité (docs/conception-transfert-pochette.md).

CE QUE CES TESTS PROTÈGENT
--------------------------
Le transfert est le SEUL endroit du code qui déroge à une règle listée comme
non négociable : « on attribue toujours le plus petit numéro libre »
(spécification §6). La dérogation est justifiée — la pochette n'est jamais
devenue libre — mais elle a toutes les apparences d'un oubli. Sans
`test_un_numero_plus_petit_libre_n_est_pas_pris`, quelqu'un « corrigera » un
jour le service en toute bonne foi, et l'application se remettra à demander au
bénévole de déplacer une pièce d'identité d'un casier à l'autre.

Le second point sensible est l'ORDRE des écritures dans la transaction (clore
avant d'insérer). Il n'est pas cosmétique : l'index UNIQUE partiel
`idx_pochettes_un_seul_pret` fait échouer l'écriture si on l'inverse. Le test
qui le démontre existe pour que le commentaire du service reste vérifiable.
L'escalade `clore_oubli` en ajoute une seconde : DEUX clôtures doivent
précéder l'unique INSERT, et l'index sanctionnerait la moindre inversion.

Le troisième est la libération du numéro : celui du prêt oublié retourne au
pot (la pièce d'identité est censée avoir quitté son casier), celui du
transfert non (elle n'a pas bougé). Une sortie tournoi, elle, n'a aucun
numéro à libérer — `liberer_numero(conn, 0)` marquerait libre un marqueur
« sans emplacement ».

FIXTURES
--------
`conn` est une base en mémoire, comme dans test_services.py, MAIS elle crée en
plus les index UNIQUE partiels (que `SCHEMA_STATEMENTS` ne porte pas : ils sont
posés par `db._creer_index_uniques`). Sans eux, ces tests passeraient au vert
sur un ordre d'écriture pourtant invalide en production.

La concurrence, elle, se teste sur FICHIER : deux connexions `:memory:` ouvrent
deux bases différentes et ne se disputent aucun verrou (même raison que dans
tests/test_concurrence_pochettes.py).
"""

import sqlite3
import threading

import pytest

from app import db, models, services


TIMEOUT_COURT_S = 0.3


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for p in models.PRAGMAS:
        c.execute(p)
    for s in models.SCHEMA_STATEMENTS:
        c.executescript(s)
    for s in models.SCHEMA_INDEXES_UNIQUES:
        c.execute(s)
    c.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    c.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
        [("001",), ("002",), ("003",)],
    )
    c.commit()
    yield c
    c.close()


def _nb_prets(conn):
    return conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]


def _occupe(conn, numero):
    ligne = conn.execute(
        "SELECT occupe FROM pochettes WHERE numero_pochette = ?", (numero,)
    ).fetchone()
    return None if ligne is None else ligne["occupe"]


# ---------------------------------------------------------------------------
# Le cœur : le numéro est conservé
# ---------------------------------------------------------------------------
def test_le_numero_est_conserve(conn):
    numero = services.preter(conn, "001")
    res = services.transferer_pochette(conn, "001", "002")

    assert res == {"transfere": True, "numero": numero, "meme_boite": False,
                   "oubli_clos": False, "numero_libere": None}
    assert services.est_sorti(conn, "001") is False
    assert services.pret_en_cours(conn, "002")["numero_pochette"] == numero


def test_un_numero_plus_petit_libre_n_est_pas_pris(conn):
    """
    LE test de l'exception (note de conception §3). Le n°1 est libre au moment
    du transfert : un prêt ordinaire le prendrait, le transfert doit garder le
    n°2, sans quoi l'écran demanderait de déplacer la pièce d'identité — le
    geste même que la fonctionnalité supprime.
    """
    services.preter(conn, "001")                      # n°1
    numero_002 = services.preter(conn, "002")         # n°2
    services.rendre(conn, "001")                      # le n°1 redevient libre
    assert _occupe(conn, 1) == 0

    res = services.transferer_pochette(conn, "002", "003")

    assert res["numero"] == numero_002 == 2
    assert services.pret_en_cours(conn, "003")["numero_pochette"] == 2
    assert _occupe(conn, 1) == 0                      # toujours libre, non pris


def test_la_pochette_reste_occupee(conn):
    numero = services.preter(conn, "001")
    assert _occupe(conn, numero) == 1
    services.transferer_pochette(conn, "001", "002")
    # La pièce d'identité n'a pas bougé : le casier n'a jamais été rendu libre.
    assert _occupe(conn, numero) == 1


def test_le_numero_est_efface_de_la_ligne_close(conn):
    """D5 : le numéro disparaît de la ligne close et vit sur la nouvelle."""
    numero = services.preter(conn, "001")
    services.transferer_pochette(conn, "001", "002")

    close = conn.execute(
        "SELECT numero_pochette FROM prets WHERE id_exemplaire = '001'"
    ).fetchone()
    assert close["numero_pochette"] is None
    ouvert = conn.execute(
        "SELECT numero_pochette FROM prets "
        "WHERE id_exemplaire = '002' AND date_retour IS NULL"
    ).fetchone()
    assert ouvert["numero_pochette"] == numero


def test_meme_boite_clot_et_rouvre_sur_le_meme_numero(conn):
    """
    Le visiteur se ravise et repart avec le même jeu. Contrairement à
    `repreter`, qui change de numéro, le transfert garde le sien.
    """
    numero = services.preter(conn, "001")
    res = services.transferer_pochette(conn, "001", "001")

    assert res["meme_boite"] is True
    assert res["numero"] == numero
    assert services.pret_en_cours(conn, "001")["numero_pochette"] == numero
    assert _nb_prets(conn) == 2          # l'ancien clos + le nouveau


def test_deux_prets_comptes_dans_les_statistiques(conn, vieillir_prets):
    services.preter(conn, "001")
    vieillir_prets(conn)   # sinon la boîte rendue devient une erreur de prêt
    services.transferer_pochette(conn, "001", "002")

    stats = services.stats_globales(conn)
    assert stats["total_prets"] == 2
    assert stats["en_cours"] == 1


# ---------------------------------------------------------------------------
# Refus : un message, jamais une erreur, et RIEN d'écrit
# ---------------------------------------------------------------------------
def test_refus_si_rien_a_rendre(conn):
    res = services.transferer_pochette(conn, "001", "002")

    assert res == {"rien_a_rendre": True}
    assert _nb_prets(conn) == 0
    assert services.est_sorti(conn, "002") is False


def test_refus_si_sortie_tournoi(conn):
    """Une sortie tournoi n'a pas de pièce d'identité à transférer."""
    services.sortir_tournoi(conn, "001")
    res = services.transferer_pochette(conn, "001", "002")

    assert res == {"sans_pochette": True}
    assert _nb_prets(conn) == 1
    assert services.est_sorti(conn, "001") is True    # rien n'a été clos
    assert services.est_sorti(conn, "002") is False


def test_refus_si_la_nouvelle_boite_est_deja_sortie(conn):
    numero_001 = services.preter(conn, "001")
    numero_002 = services.preter(conn, "002")

    res = services.transferer_pochette(conn, "001", "002")

    assert res == {"nouveau_sorti": True, "numero": numero_002}
    # Le prêt de 001 est intact : le bénévole peut encore le rendre normalement.
    assert services.pret_en_cours(conn, "001")["numero_pochette"] == numero_001
    assert services.pret_en_cours(conn, "002")["numero_pochette"] == numero_002
    assert _nb_prets(conn) == 2


# ---------------------------------------------------------------------------
# Escalade : clôturer le prêt oublié qui tient la boîte emportée
# ---------------------------------------------------------------------------
def test_escalade_clot_les_deux_prets_et_ne_libere_que_l_oubli(conn):
    """
    Le cœur du lot agora-1. Le visiteur rapporte 001 (pochette n°1) et repart
    avec 002, que l'application croit sortie sur la n°2 faute d'avoir scanné
    son retour. Après l'escalade : deux prêts clos, un seul ouvert, la n°1
    conservée par le visiteur et la n°2 rendue au pot.
    """
    numero_rendu = services.preter(conn, "001")       # n°1
    numero_oubli = services.preter(conn, "002")       # n°2

    res = services.transferer_pochette(conn, "001", "002", clore_oubli=True)

    assert res == {"transfere": True, "numero": numero_rendu, "meme_boite": False,
                   "oubli_clos": True, "numero_libere": numero_oubli}
    # Le nouveau prêt porte le numéro de la boîte RENDUE, pas celui de l'oubli.
    ouverts = conn.execute(
        "SELECT id_exemplaire, numero_pochette FROM prets WHERE date_retour IS NULL"
    ).fetchall()
    assert len(ouverts) == 1
    assert ouverts[0]["id_exemplaire"] == "002"
    assert ouverts[0]["numero_pochette"] == numero_rendu
    # La pochette du visiteur n'a jamais été libérée, celle de l'oubli si.
    assert _occupe(conn, numero_rendu) == 1
    assert _occupe(conn, numero_oubli) == 0
    assert _nb_prets(conn) == 3                       # deux clos + un ouvert


def test_escalade_sur_sortie_tournoi_ne_libere_aucun_numero(conn):
    """
    Cas atteignable : une boîte sortie pour un tournoi et jamais rentrée. On la
    clôt, mais son « numéro » 0 est un marqueur « sans emplacement », pas un
    casier — le libérer inventerait une pochette n°0 disponible.
    """
    numero_rendu = services.preter(conn, "001")
    services.sortir_tournoi(conn, "002")

    res = services.transferer_pochette(conn, "001", "002", clore_oubli=True)

    assert res["transfere"] is True
    assert res["oubli_clos"] is True
    assert res["numero_libere"] is None
    assert res["numero"] == numero_rendu
    assert services.pret_en_cours(conn, "002")["numero_pochette"] == numero_rendu
    assert _occupe(conn, services.NUMERO_TOURNOI) is None   # aucune pochette n°0
    assert _occupe(conn, numero_rendu) == 1


def test_le_refus_reste_effectif_sans_le_drapeau(conn):
    """
    Contre-épreuve : l'escalade est une AUTORISATION, jamais un défaut. Sans
    le drapeau, le comportement d'origine — refuser sans rien écrire — doit
    survivre intact (c'est lui qui protège d'un double clic).
    """
    services.preter(conn, "001")
    numero_002 = services.preter(conn, "002")

    res = services.transferer_pochette(conn, "001", "002")

    assert res == {"nouveau_sorti": True, "numero": numero_002}
    assert _nb_prets(conn) == 2
    assert services.pret_en_cours(conn, "002")["numero_pochette"] == numero_002


def test_le_drapeau_ne_change_rien_au_cas_meme_boite(conn):
    """
    Le visiteur se ravise et repart avec le même jeu : `id_nouveau` est la
    boîte rendue, il n'y a aucun prêt oublié à clore. Le drapeau ne doit rien
    y changer — surtout pas clôturer deux fois le même prêt.
    """
    numero = services.preter(conn, "001")

    res = services.transferer_pochette(conn, "001", "001", clore_oubli=True)

    assert res == {"transfere": True, "numero": numero, "meme_boite": True,
                   "oubli_clos": False, "numero_libere": None}
    assert _nb_prets(conn) == 2
    assert services.pret_en_cours(conn, "001")["numero_pochette"] == numero


def test_le_drapeau_sur_une_boite_revenue_entre_temps_transfere_normalement(conn):
    """
    L'écran qui porte le bouton n'est qu'un INSTANTANÉ : la boîte peut avoir
    été rendue entre son affichage et l'appui. Le drapeau ne doit alors rien
    déclencher de plus qu'un transfert ordinaire (`oubli_clos` faux), et
    surtout ne pas libérer un numéro que le retour a déjà rendu au pot.
    """
    numero_rendu = services.preter(conn, "001")
    services.preter(conn, "002")
    services.rendre(conn, "002")                      # un autre bénévole a scanné

    res = services.transferer_pochette(conn, "001", "002", clore_oubli=True)

    assert res["transfere"] is True
    assert res["oubli_clos"] is False
    assert res["numero_libere"] is None
    assert res["numero"] == numero_rendu


def test_escalade_le_prochain_pret_recupere_le_numero_libere(conn):
    """
    Vérifie que la libération est réelle et pas seulement annoncée : le numéro
    du prêt oublié doit revenir dans le pot des numéros attribuables.
    """
    services.preter(conn, "001")                      # n°1
    numero_oubli = services.preter(conn, "002")       # n°2
    services.transferer_pochette(conn, "001", "002", clore_oubli=True)

    assert services.preter(conn, "003") == numero_oubli


# ---------------------------------------------------------------------------
# L'ordre des écritures n'est pas cosmétique
# ---------------------------------------------------------------------------
def test_inserer_avant_de_clore_violerait_l_index_unique(conn):
    """
    Démontre la contrainte que le service respecte : tant que l'ancien prêt est
    ouvert, un second prêt portant le même numéro est REFUSÉ par
    `idx_pochettes_un_seul_pret`. C'est ce qui rend obligatoire l'ordre
    « clore puis insérer » à l'intérieur de la transaction.
    """
    numero = services.preter(conn, "001")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif) "
            "VALUES ('002', ?, ?, 'pret')",
            (numero, services.maintenant()),
        )
    conn.rollback()


def test_le_transfert_passe_avec_les_index_uniques(conn):
    """Contre-épreuve du test ci-dessus : le bon ordre, lui, ne heurte rien."""
    services.preter(conn, "001")
    assert services.transferer_pochette(conn, "001", "002")["transfere"] is True


# ---------------------------------------------------------------------------
# Concurrence — base sur FICHIER
# ---------------------------------------------------------------------------
def _connexion(chemin, timeout=TIMEOUT_COURT_S):
    conn = sqlite3.connect(chemin, timeout=timeout)
    conn.row_factory = sqlite3.Row
    for pragma in models.PRAGMAS:
        conn.execute(pragma)
    return conn


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base sur fichier (schéma complet, index UNIQUE compris), 3 boîtes."""
    chemin = tmp_path / "pret-jeux.db"
    monkeypatch.setenv("DATABASE_PATH", str(chemin))
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, 'CATAN')",
        [("001",), ("002",), ("003",)],
    )
    conn.commit()
    conn.close()
    return chemin


def test_le_transfert_prend_le_verrou_avant_de_lire(base):
    """
    Test DÉTERMINISTE, sur le modèle de test_concurrence_pochettes : la seule
    propriété discriminante est qu'ENTRER dans le transfert, avant toute
    lecture d'état, bloque déjà un autre écrivain. Vérifié en neutralisant le
    `BEGIN IMMEDIATE` de `services.transaction` : le test échoue alors.
    """
    premiere = _connexion(str(base))
    services.preter(premiere, "001")

    seconde = _connexion(str(base))
    with services.transaction(premiere):
        with pytest.raises(sqlite3.OperationalError) as erreur:
            seconde.execute("BEGIN IMMEDIATE")
        assert "lock" in str(erreur.value).lower()
    seconde.close()
    premiere.close()


def test_deux_transferts_simultanes_ne_donnent_qu_un_seul_pret(base):
    """
    Deux bénévoles transfèrent la même boîte au même instant. Un seul doit
    réussir ; l'autre repart avec un refus ou un conflit — jamais deux prêts
    ouverts sur la nouvelle boîte, jamais deux pochettes pour une pièce
    d'identité.
    """
    conn = _connexion(str(base))
    services.preter(conn, "001")
    conn.close()

    depart = threading.Barrier(2)
    resultats = [None, None]

    def transferer(indice):
        c = _connexion(str(base))
        try:
            depart.wait()
            resultats[indice] = services.transferer_pochette(c, "001", "002")
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as erreur:
            resultats[indice] = erreur
        finally:
            c.close()

    fils = [threading.Thread(target=transferer, args=(i,)) for i in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    reussites = [r for r in resultats if isinstance(r, dict) and r.get("transfere")]
    assert len(reussites) == 1

    conn = _connexion(str(base))
    ouverts = conn.execute(
        "SELECT id_exemplaire, numero_pochette FROM prets WHERE date_retour IS NULL"
    ).fetchall()
    conn.close()
    assert len(ouverts) == 1
    assert ouverts[0]["id_exemplaire"] == "002"


def test_deux_escalades_simultanees_ne_donnent_qu_un_seul_pret(base):
    """
    Deux bénévoles appuient au même instant sur « clôturer le prêt oublié ».
    Un seul doit passer : jamais deux prêts ouverts sur la boîte emportée,
    jamais un numéro libéré deux fois. Même patron que le test ci-dessus, mais
    sur le chemin qui ferme DEUX prêts d'un coup.
    """
    conn = _connexion(str(base))
    services.preter(conn, "001")
    services.preter(conn, "002")          # le prêt « oublié »
    conn.close()

    depart = threading.Barrier(2)
    resultats = [None, None]

    def escalader(indice):
        c = _connexion(str(base))
        try:
            depart.wait()
            resultats[indice] = services.transferer_pochette(
                c, "001", "002", clore_oubli=True)
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as erreur:
            resultats[indice] = erreur
        finally:
            c.close()

    fils = [threading.Thread(target=escalader, args=(i,)) for i in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    reussites = [r for r in resultats if isinstance(r, dict) and r.get("transfere")]
    assert len(reussites) == 1

    conn = _connexion(str(base))
    ouverts = conn.execute(
        "SELECT id_exemplaire, numero_pochette FROM prets WHERE date_retour IS NULL"
    ).fetchall()
    libres = conn.execute(
        "SELECT COUNT(*) FROM pochettes WHERE occupe = 0"
    ).fetchone()[0]
    conn.close()
    assert len(ouverts) == 1
    assert ouverts[0]["id_exemplaire"] == "002"
    assert libres == 1                    # la pochette de l'oubli, une seule fois
