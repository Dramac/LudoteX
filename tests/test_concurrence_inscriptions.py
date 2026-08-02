"""
Accès SIMULTANÉS aux inscriptions de tournoi — la course sur le plafond de
places.

CE QUE CES TESTS PROTÈGENT
--------------------------
`tournoi.services.inscrire` comptait les places restantes, PUIS insérait
l'inscription. Entre les deux, rien n'empêchait une autre personne de compter
la même chose : deux visiteurs validant le formulaire au même instant sur la
dernière place s'inscrivaient tous les deux. Exactement le motif de la course
d'attribution des numéros de pochette (voir tests/test_concurrence_pochettes.py
et docs/protocole-stress-test.md § 2), avec une conséquence sans commune
mesure — une chaise de trop à installer, pas une pièce d'identité rendue à la
mauvaise personne. C'est pourquoi il a été traité à part, et après.

PAS D'INDEX EN FILET, ICI
--------------------------
Le module de prêt s'appuie aussi sur des index UNIQUE partiels. Impossible ici :
un plafond de places est un COMPTAGE, et aucune contrainte de schéma n'exprime
« pas plus de N lignes ». La transaction est le seul garde-fou — d'où
l'importance de ces tests.

Base sur FICHIER : deux connexions `:memory:` ouvrent deux bases différentes et
ne se disputent aucun verrou.
"""

import sqlite3
import threading

import pytest

from app.tournoi import db as tdb
from app.tournoi import models, services


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base des tournois sur fichier, avec un tournoi ouvert à 3 places."""
    chemin = tmp_path / "tournoi.db"
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(chemin))
    monkeypatch.setattr(tdb, "get_database_path", lambda: chemin)
    tdb.init_db()
    conn = tdb.get_connection()
    id_tournoi = services.creer_tournoi(conn, "Blitz", nb_places=3)
    services.changer_etat(conn, id_tournoi, "inscriptions")
    conn.close()
    return chemin, id_tournoi


def _connexion(chemin, timeout=0.3):
    conn = sqlite3.connect(chemin, timeout=timeout)
    conn.row_factory = sqlite3.Row
    for pragma in models.PRAGMAS:
        conn.execute(pragma)
    return conn


def _en_salve(nb_fils, travail):
    """Lance `travail(i)` dans `nb_fils` fils qui partent tous au même instant."""
    depart = threading.Barrier(nb_fils)
    resultats = [None] * nb_fils

    def executer(indice):
        depart.wait()
        try:
            resultats[indice] = travail(indice)
        except Exception as erreur:
            resultats[indice] = erreur

    fils = [threading.Thread(target=executer, args=(i,)) for i in range(nb_fils)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=30)
    return resultats


def test_le_comptage_des_places_se_fait_deja_sous_verrou(base, monkeypatch):
    """
    DÉTERMINISTE. La propriété à vérifier n'est pas « deux écritures ne se
    croisent pas » — SQLite l'assure de lui-même dès la première écriture, donc
    un test formulé ainsi passe au vert AVEC OU SANS le correctif. C'est la
    LECTURE qu'il faut protéger : au moment où `inscrire` compte les places
    restantes, le verrou d'écriture doit DÉJÀ être tenu.

    On observe donc `conn.in_transaction` à l'instant précis du comptage. Sans
    le correctif il vaut False (le comptage est un simple SELECT, qui n'ouvre
    rien), et deux personnes comptent alors la même dernière place.
    """
    chemin, id_tournoi = base
    observe = {}
    vraie_fonction = services.places_restantes

    def espion(conn, tournoi):
        observe["sous_verrou"] = conn.in_transaction
        return vraie_fonction(conn, tournoi)

    monkeypatch.setattr(services, "places_restantes", espion)
    conn = tdb.get_connection()
    try:
        assert services.inscrire(conn, id_tournoi, "Visiteuse")["ok"]
    finally:
        conn.close()
    assert observe["sous_verrou"] is True, (
        "les places ont été comptées hors transaction : deux inscriptions "
        "simultanées peuvent lire la même dernière place"
    )


def test_salve_sur_la_derniere_place_ne_surreserve_pas(base):
    """
    STOCHASTIQUE. Huit inscriptions simultanées sur un tournoi de 3 places :
    exactement 3 acceptées, 5 refusées pour « complet ».
    """
    chemin, id_tournoi = base

    def inscrire(indice):
        conn = tdb.get_connection()
        try:
            return services.inscrire(conn, id_tournoi, f"joueur{indice}")
        finally:
            conn.close()

    resultats = _en_salve(8, inscrire)
    erreurs = [r for r in resultats if isinstance(r, Exception)]
    assert not erreurs, erreurs
    acceptes = [r for r in resultats if r["ok"]]
    refuses = [r for r in resultats if not r["ok"]]
    assert len(acceptes) == 3, f"{len(acceptes)} inscrits pour 3 places"
    assert all(r["raison"] == "complet" for r in refuses)

    conn = tdb.get_connection()
    try:
        assert services.compter_inscriptions(conn, id_tournoi) == 3
    finally:
        conn.close()


def test_codes_de_desinscription_tous_distincts(base):
    """
    Chaque inscription simultanée doit repartir avec SON code — c'est lui qui
    permet de se désinscrire soi-même, et deux personnes ne doivent jamais
    partager le même.
    """
    chemin, id_tournoi = base
    conn = tdb.get_connection()
    try:
        services.modifier_tournoi(conn, id_tournoi, nb_places=None)   # sans plafond
    finally:
        conn.close()

    def inscrire(indice):
        conn = tdb.get_connection()
        try:
            return services.inscrire(conn, id_tournoi, f"joueur{indice}")
        finally:
            conn.close()

    resultats = _en_salve(8, inscrire)
    assert not [r for r in resultats if isinstance(r, Exception)]
    codes = [r["code"] for r in resultats]
    assert len(set(codes)) == 8


def test_sans_plafond_toutes_les_inscriptions_passent(base):
    """NON-RÉGRESSION : un tournoi sans plafond n'en refuse aucune."""
    chemin, id_tournoi = base
    conn = tdb.get_connection()
    try:
        services.modifier_tournoi(conn, id_tournoi, nb_places=None)
    finally:
        conn.close()

    def inscrire(indice):
        conn = tdb.get_connection()
        try:
            return services.inscrire(conn, id_tournoi, f"joueur{indice}")
        finally:
            conn.close()

    resultats = _en_salve(8, inscrire)
    assert all(r["ok"] for r in resultats)
    conn = tdb.get_connection()
    try:
        assert services.compter_inscriptions(conn, id_tournoi) == 8
    finally:
        conn.close()


def test_ajout_par_un_benevole_est_bien_committe(base):
    """
    NON-RÉGRESSION du piège de cette correction : `_inserer_inscription` ne
    committe plus (c'est l'appelant qui délimite la transaction). Si
    `ajouter_participant` avait été oublié, l'ajout manuel par un bénévole
    serait perdu à la fermeture de la connexion — sans le moindre message.
    """
    chemin, id_tournoi = base
    conn = tdb.get_connection()
    try:
        res = services.ajouter_participant(conn, id_tournoi, "Ajouté à la main")
        assert res["ok"]
    finally:
        conn.close()

    autre = _connexion(chemin)          # connexion NEUVE : ne voit que ce qui est committé
    try:
        assert autre.execute(
            "SELECT COUNT(*) FROM inscriptions WHERE id_tournoi = ?", (id_tournoi,)
        ).fetchone()[0] == 1
    finally:
        autre.close()


def test_inscription_publique_bien_committee(base):
    """Même vérification pour l'inscription publique."""
    chemin, id_tournoi = base
    conn = tdb.get_connection()
    try:
        assert services.inscrire(conn, id_tournoi, "Visiteuse")["ok"]
    finally:
        conn.close()

    autre = _connexion(chemin)
    try:
        assert autre.execute(
            "SELECT COUNT(*) FROM inscriptions WHERE id_tournoi = ?", (id_tournoi,)
        ).fetchone()[0] == 1
    finally:
        autre.close()
