"""
Peuplement de données de démonstration pour le MODE FORMATION.

Voir `docs/mode-formation.md` pour le principe général : une SECONDE INSTANCE
de l'application (mêmes code, même dépôt), avec ses propres bases SQLite
jetables, exposée sur un sous-domaine séparé (ex. formation.<domaine>).

DIFFÉRENCE AVEC `app/planning/demo.py`
---------------------------------------
La démo du planning AJOUTE un événement de démonstration sans toucher à
l'existant (rejouable sans risque, pensée pour une présentation). Ce script-ci
est au contraire volontairement IDEMPOTENT AU SENS FORT : il VIDE d'abord les
données déjà présentes des bases ciblées puis repeuple à l'identique — pratique
pour repartir d'un état propre entre deux sessions de formation, sans avoir à
supprimer les fichiers de base à la main. Relancer le script plusieurs fois de
suite produit toujours exactement le même état final.

DONNÉES CRÉÉES
--------------
- ~20 jeux fictifs dans la base de PRÊT (« Jeu d'essai n°1 »…), avec quelques
  prêts en cours et quelques prêts déjà terminés (historique).
- Un tournoi d'exemple dans la base des TOURNOIS (état « inscriptions »),
  avec quelques inscrits.

Noms délibérément explicites (« … (formation) », « Jeu d'essai n°… ») pour ne
jamais pouvoir être confondus avec un vrai catalogue.

SÉCURITÉ — À LIRE AVANT DE LANCER CE SCRIPT
--------------------------------------------
Ce script écrit dans les bases pointées par les variables d'environnement
courantes (`DATABASE_PATH`, `TOURNOI_DATABASE_PATH` — voir `app.db` et
`app.tournoi.db`), et COMMENCE PAR TOUT SUPPRIMER dans ces bases. Il n'y a
AUCUNE garde technique ici : le script ne sait pas dans quel contexte il
tourne. La protection vient entièrement de l'isolation de l'instance de
formation (ses propres bases jetables, jamais celles de production) — ne
JAMAIS lancer ce script en pointant vers les bases de production.

USAGE
-----
    python -m app.formation
"""

from __future__ import annotations

import sqlite3

from app import db as pret_db
from app import services
from app.tournoi import db as tournoi_db
from app.tournoi import services as tournoi_services

NB_JEUX = 20
PREFIXE_NOM_JEU = "Jeu d'essai n°"
NB_PRETS_EN_COURS = 5
NB_PRETS_TERMINES = 5

NOM_TOURNOI = "Tournoi d'essai (formation)"
INSCRITS_TOURNOI = ["Équipe Test A", "Équipe Test B", "Joueuse Essai 1", "Joueur Essai 2"]


# ---------------------------------------------------------------------------
# Base de PRÊT
# ---------------------------------------------------------------------------
def _vider_base_pret(conn: sqlite3.Connection) -> None:
    """Supprime toutes les lignes des 4 tables de la base de prêt (schéma conservé)."""
    for table in ("prets", "pochettes", "exemplaires", "titres"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def peupler_pret(conn: sqlite3.Connection) -> dict:
    """
    Vide puis repeuple la base de PRÊT avec des jeux fictifs et quelques
    prêts (en cours et terminés).

    Returns:
        Résumé {"jeux": n, "prets_en_cours": n, "prets_termines": n}.
    """
    _vider_base_pret(conn)

    ids_exemplaires = []
    for i in range(1, NB_JEUX + 1):
        resultat = services.creer_jeu(
            conn, f"{PREFIXE_NOM_JEU}{i}",
            type_jeu="Jeu", categorie="Formation",
            nb_joueurs_min=2, nb_joueurs_max=6, duree_min=30, age_min=8,
            descriptif=("Jeu FICTIF créé par le mode formation — sans rapport "
                        "avec le vrai catalogue."),
        )
        ids_exemplaires.append(resultat["id_exemplaire"])

    # Quelques prêts EN COURS.
    for id_ex in ids_exemplaires[:NB_PRETS_EN_COURS]:
        services.preter(conn, id_ex)

    # Quelques prêts TERMINÉS (prêtés puis rendus aussitôt -> historique).
    debut_termines = NB_PRETS_EN_COURS
    fin_termines = debut_termines + NB_PRETS_TERMINES
    for id_ex in ids_exemplaires[debut_termines:fin_termines]:
        services.preter(conn, id_ex)
        services.rendre(conn, id_ex)

    return {
        "jeux": len(ids_exemplaires),
        "prets_en_cours": NB_PRETS_EN_COURS,
        "prets_termines": NB_PRETS_TERMINES,
    }


# ---------------------------------------------------------------------------
# Base des TOURNOIS
# ---------------------------------------------------------------------------
def _vider_base_tournoi(conn: sqlite3.Connection) -> None:
    """Supprime toutes les lignes des 3 tables de la base des tournois."""
    for table in ("rencontres", "inscriptions", "tournois"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def peupler_tournoi(conn: sqlite3.Connection) -> dict:
    """
    Vide puis repeuple la base des TOURNOIS avec un tournoi d'exemple ouvert
    aux inscriptions, déjà pourvu de quelques inscrits.

    Returns:
        Résumé {"tournois": n, "inscrits": n}.
    """
    _vider_base_tournoi(conn)

    id_tournoi = tournoi_services.creer_tournoi(
        conn, NOM_TOURNOI,
        jeu=f"{PREFIXE_NOM_JEU}1", age="tout public",
        nb_places=8, inscription_en_ligne=True,
    )
    tournoi_services.changer_etat(conn, id_tournoi, "inscriptions")
    for pseudo in INSCRITS_TOURNOI:
        tournoi_services.inscrire(conn, id_tournoi, pseudo)

    return {"tournois": 1, "inscrits": len(INSCRITS_TOURNOI)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def peupler() -> dict:
    """
    Vide puis repeuple les DEUX bases (prêt + tournois) de l'instance courante
    (selon les variables d'environnement actives). Idempotent.

    Returns:
        Résumé fusionné des deux étapes.
    """
    conn_pret = pret_db.get_connection()
    try:
        resume = peupler_pret(conn_pret)
    finally:
        conn_pret.close()

    conn_tournoi = tournoi_db.get_connection()
    try:
        resume.update(peupler_tournoi(conn_tournoi))
    finally:
        conn_tournoi.close()

    return resume


# Exécuté via « python -m app.formation ».
if __name__ == "__main__":
    pret_db.init_db()
    tournoi_db.init_db()
    resume = peupler()
    print("Données de formation (re)créées :")
    print(f"  - {resume['jeux']} jeux fictifs "
          f"({resume['prets_en_cours']} prêtés, {resume['prets_termines']} rendus)")
    print(f"  - {resume['tournois']} tournoi d'exemple ({resume['inscrits']} inscrits)")
    print()
    print("RAPPEL : ce script vide puis repeuple les bases pointées par .env "
          "(DATABASE_PATH / TOURNOI_DATABASE_PATH). Ne jamais le lancer en "
          "pointant vers les bases de PRODUCTION.")
