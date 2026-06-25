"""
Jeu de DÉMONSTRATION du planning bénévole.

Reproduit la structure du tableur Excel du bureau (postes, créneaux samedi/
dimanche, tâches d'installation/rangement) et génère une vingtaine de bénévoles
fictifs avec disponibilités et préférences, puis lance le préremplissage et
publie l'événement — afin de montrer une première ébauche fonctionnelle.

Données ENTIÈREMENT fictives (prénoms génériques). Sert uniquement à la
présentation ; un vrai événement se crée vide via l'écran d'administration.
"""

from __future__ import annotations

import sqlite3

from app.planning import services

# Postes (colonnes du tableau) + besoin de personnes par créneau de service.
_POSTES = [
    ("Accueil", 2, False),
    ("Ludothèque", 3, False),
    ("Inscription tournois", 1, False),
    ("Bar", 2, False),
    ("Explication jeux", 2, True),
    ("Partage un jeu", 1, False),
]

# Créneaux de service (heures locales). (jour, heure_debut, heure_fin).
_SAMEDI = "Samedi 12 sept."
_DIMANCHE = "Dimanche 13 sept."
_CRENEAUX = [
    (_SAMEDI, "2026-09-12T14:30", "2026-09-12T16:30"),
    (_SAMEDI, "2026-09-12T16:30", "2026-09-12T18:30"),
    (_SAMEDI, "2026-09-12T18:30", "2026-09-12T20:30"),
    (_SAMEDI, "2026-09-12T20:30", "2026-09-12T22:30"),
    (_SAMEDI, "2026-09-12T22:30", "2026-09-13T00:30"),
    (_DIMANCHE, "2026-09-13T10:00", "2026-09-13T12:00"),
    (_DIMANCHE, "2026-09-13T12:00", "2026-09-13T14:00"),
    (_DIMANCHE, "2026-09-13T14:00", "2026-09-13T16:00"),
    (_DIMANCHE, "2026-09-13T16:00", "2026-09-13T18:00"),
]

# Tâches ponctuelles (sans poste).
_TACHES = [
    ("Installation", _SAMEDI, "2026-09-12T10:00", "2026-09-12T14:00"),
    ("Rangement", _DIMANCHE, "2026-09-13T18:00", "2026-09-13T20:00"),
]

# Bénévoles fictifs (prénoms génériques).
_PRENOMS = [
    "Alice", "Bruno", "Camille", "David", "Élodie", "Farid", "Gaëlle", "Hugo",
    "Inès", "Jules", "Karim", "Léa", "Marc", "Nadia", "Omar", "Pauline",
    "Quentin", "Rachel", "Samir", "Théo", "Ursula", "Victor", "Wendy", "Yanis",
    "Zoé", "Adèle", "Bastien", "Chloé",
]


def creer_demo(conn: sqlite3.Connection) -> int:
    """
    Construit l'événement de démonstration et renvoie son id. L'événement est
    laissé en état 'publie' avec un planning prérempli.
    """
    ev = services.creer_evenement(conn, "Festival du jeu 2026 (démo)")

    # Postes.
    id_postes = [
        services.ajouter_poste(conn, ev, nom, demande_experience=exp)
        for (nom, _besoin, exp) in _POSTES
    ]

    # Créneaux de service + besoins par poste.
    id_creneaux = []
    for (jour, debut, fin) in _CRENEAUX:
        cr = services.ajouter_creneau(conn, ev, jour, debut, fin, type_creneau="poste")
        id_creneaux.append(cr)
        for (id_poste, (_nom, besoin, _exp)) in zip(id_postes, _POSTES):
            services.definir_besoin(conn, cr, id_poste, besoin)

    # Tâches ponctuelles.
    id_taches = [
        services.ajouter_creneau(conn, ev, jour, debut, fin,
                                 type_creneau="tache", libelle=libelle)
        for (libelle, jour, debut, fin) in _TACHES
    ]

    nb_creneaux = len(id_creneaux)
    nb_postes = len(id_postes)

    # Bénévoles fictifs : disponibilités larges + préférences variées.
    for i, prenom in enumerate(_PRENOMS):
        # Disponible sur ~2 créneaux sur 3 (motif déterministe pour reproductibilité).
        # Volontairement plus rare le soir (derniers créneaux) pour laisser quelques
        # trous réalistes et illustrer la détection des cases en manque.
        dispos = {
            id_creneaux[k]
            for k in range(nb_creneaux)
            if (k + i) % 3 != 0 and not (k in (3, 4) and i % 2 == 0)
        }
        prefs = {
            id_postes[i % nb_postes]: "prefere",
            id_postes[(i + 2) % nb_postes]: "surtout_pas",
        }
        max_h = 4.0 if i % 5 == 0 else None
        services.enregistrer_souhaits(
            conn, ev, prenom, contact=f"{prenom.lower()}@exemple.eu",
            max_heures=max_h, dispos=dispos, preferences=prefs,
        )

    # Préremplissage puis publication.
    services.prefiller(conn, ev)
    # Quelques bénévoles sur les tâches (manuel), pour illustrer.
    benevoles = services.lister_benevoles(conn, ev)
    for id_tache in id_taches:
        for b in benevoles[:4]:
            services.affecter(conn, id_tache, None, b["id_benevole"], origine="manuel")

    services.changer_etat(conn, ev, "brouillon")
    services.changer_etat(conn, ev, "publie")

    # Jumeau resté en COLLECTE (même trame) : permet de démontrer aussi le
    # formulaire de souhaits depuis la page publique.
    services.dupliquer_trame(conn, ev, "Festival du jeu 2026 — collecte ouverte (démo)")
    return ev


# Exécuté via « python -m app.planning.demo » : pré-remplit la base du planning
# avec le jeu de démonstration (pratique pour une présentation hors écran admin).
if __name__ == "__main__":
    from app.planning.db import get_connection, init_db

    init_db()
    conn = get_connection()
    try:
        ev = creer_demo(conn)
    finally:
        conn.close()
    print(f"Planning de démonstration créé (événement publié n°{ev}).")
    print("Ouvrez /planning pour voir la grille, /planning/admin pour la gérer.")
