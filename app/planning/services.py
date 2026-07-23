"""
Logique métier du module « Planning bénévole » (socle de la phase 1).

ISOLE toute la logique (trame, collecte des souhaits, préremplissage, grille)
hors des routes, comme `app/services.py` (prêt) et `app/tournoi/services.py`.
Chaque fonction reçoit une connexion à la base du planning
(`app/planning/db.py`) et ne s'occupe que des données.

PÉRIMÈTRE (socle, docs/conception-planning.md §10) :
- Trame : postes, créneaux (postes + tâches), besoins par (créneau × poste).
- Collecte : un bénévole déclare ses disponibilités, ses préférences par poste
  (prefere/ok/si_vraiment/surtout_pas) et un plafond d'heures ; il peut rouvrir
  sa réponse via un code.
- Préremplissage « DÉGROSSI » : algorithme glouton qui respecte les contraintes
  DURES (disponibilité, « surtout pas », plafond d'heures, pas deux postes en
  même temps) et LAISSE LES TROUS plutôt que de forcer. Les contraintes molles
  (continuité, expérience, équité fine) sont reportées en phase 2.
- Grille : structure complète pour l'affichage, l'édition et les exports.

DATES : créneaux stockés en UTC ISO (helpers de `app/services.py`), durée déduite
de (fin − début). Saisies/affichage en heure locale Europe/Paris.

RGPD : base séparée, finalité unique, purge possible (`purger_evenement`).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta

from app.config import NOM_ASSOCIATION
from app.planning.models import ETATS, NIVEAUX_PREFERENCE, TYPES_CRENEAU
from app.services import FUSEAU_UTC, local_vers_utc_iso, maintenant

# Bornes de saisie.
NOM_MAX = 60       # longueur maximale d'un nom de bénévole / poste
CODE_OCTETS = 8    # entropie du code de modification (token_urlsafe)

# Transitions d'état autorisées (machine à états, conception §9).
TRANSITIONS = {
    "collecte": {"brouillon"},
    "brouillon": {"collecte", "publie"},
    "publie": {"brouillon"},
}

# Rang de priorité d'un niveau de préférence pour le préremplissage : plus petit
# = servi en premier. L'absence de préférence (None) est NEUTRE (entre « ok » et
# « si_vraiment »). « surtout_pas » est exclu en amont (contrainte dure).
_RANG_PREFERENCE = {"prefere": 0, "ok": 1, None: 2, "si_vraiment": 3}

# Phase 2 — CONTINUITÉ : « rabais » d'heures (en heures) accordé à un bénévole
# déjà placé sur le MÊME poste à un créneau CONTIGU (préférer la continuité).
# Réglé sur une durée de créneau typique : la continuité l'emporte à charge
# comparable, mais l'équité reprend le dessus si un autre bénévole est nettement
# moins chargé (écart > ce rabais). Voir prefiller().
CONTINUITE_BONUS_H = 2.0


# ===========================================================================
# Helpers
# ===========================================================================
def _row(r: sqlite3.Row | None) -> dict | None:
    """Convertit une ligne SQLite en dict (ou None)."""
    return dict(r) if r is not None else None


def _rows(rows) -> list[dict]:
    """Convertit une liste de lignes SQLite en liste de dicts."""
    return [dict(r) for r in rows]


def _generer_code() -> str:
    """Code de modification aléatoire (URL-safe, ~11 caractères)."""
    return secrets.token_urlsafe(CODE_OCTETS)


def _nettoyer(texte: str | None, maxlen: int = NOM_MAX) -> str:
    """Normalise les espaces et tronque (noms, libellés)."""
    return " ".join((texte or "").split())[:maxlen]


def duree_heures(creneau: dict | sqlite3.Row) -> float:
    """
    Durée d'un créneau en heures (décimales), déduite de (fin − début).

    Renvoie 0.0 si les bornes sont absentes ou non analysables (jamais d'erreur,
    pour ne pas bloquer un préremplissage à cause d'une saisie douteuse).
    """
    try:
        debut = datetime.fromisoformat(creneau["debut"])
        fin = datetime.fromisoformat(creneau["fin"])
    except (ValueError, KeyError, TypeError):
        return 0.0
    return max(0.0, (fin - debut).total_seconds() / 3600.0)


def _parse_max_heures(valeur) -> float | None:
    """Convertit un plafond d'heures en float positif, ou None si vide/invalide."""
    if valeur is None:
        return None
    texte = str(valeur).replace(",", ".").strip()
    if not texte:
        return None
    try:
        h = float(texte)
    except ValueError:
        return None
    return h if h > 0 else None


# ===========================================================================
# Événements (un planning par édition)
# ===========================================================================
def creer_evenement(conn: sqlite3.Connection, nom: str) -> int:
    """Crée un événement (état initial 'collecte') et renvoie son id."""
    cur = conn.execute(
        "INSERT INTO evenements (nom, etat, date_creation) VALUES (?, 'collecte', ?)",
        (_nettoyer(nom) or "Événement", maintenant()),
    )
    conn.commit()
    return cur.lastrowid


def get_evenement(conn: sqlite3.Connection, id_evenement: int) -> dict | None:
    """Renvoie un événement (dict) ou None."""
    return _row(
        conn.execute(
            "SELECT * FROM evenements WHERE id_evenement = ?", (id_evenement,)
        ).fetchone()
    )


def lister_evenements(conn: sqlite3.Connection) -> list[dict]:
    """Liste les événements, le plus récent d'abord."""
    return _rows(
        conn.execute("SELECT * FROM evenements ORDER BY id_evenement DESC")
    )


def renommer_evenement(conn: sqlite3.Connection, id_evenement: int, nom: str) -> None:
    """Renomme un événement."""
    conn.execute(
        "UPDATE evenements SET nom = ? WHERE id_evenement = ?",
        (_nettoyer(nom) or "Événement", id_evenement),
    )
    conn.commit()


def changer_etat(conn: sqlite3.Connection, id_evenement: int, etat: str) -> bool:
    """
    Applique une transition d'état si elle est autorisée (machine à états §9).

    Returns:
        True si la transition a eu lieu, False sinon (état inconnu ou interdit).
    """
    if etat not in ETATS:
        return False
    ev = get_evenement(conn, id_evenement)
    if ev is None or etat not in TRANSITIONS.get(ev["etat"], set()):
        return False
    conn.execute(
        "UPDATE evenements SET etat = ? WHERE id_evenement = ?", (etat, id_evenement)
    )
    conn.commit()
    return True


def evenement_publie(conn: sqlite3.Connection) -> dict | None:
    """Renvoie l'événement publié le plus récent (vue bénévole), ou None."""
    return _row(
        conn.execute(
            "SELECT * FROM evenements WHERE etat = 'publie' "
            "ORDER BY id_evenement DESC LIMIT 1"
        ).fetchone()
    )


def purger_evenement(conn: sqlite3.Connection, id_evenement: int) -> None:
    """
    Supprime un événement et TOUTES ses données (cascade FK) : trame, bénévoles,
    souhaits et affectations. Sert à la purge RGPD après l'événement (§4).
    """
    conn.execute("DELETE FROM evenements WHERE id_evenement = ?", (id_evenement,))
    conn.commit()


# ===========================================================================
# Trame — postes
# ===========================================================================
def ajouter_poste(
    conn: sqlite3.Connection,
    id_evenement: int,
    nom: str,
    demande_experience: bool = False,
    ordre: int | None = None,
) -> int:
    """Ajoute un poste (colonne) à un événement et renvoie son id."""
    if ordre is None:
        ordre = _prochain_ordre(conn, "postes", id_evenement)
    cur = conn.execute(
        "INSERT INTO postes (id_evenement, nom, demande_experience, ordre) "
        "VALUES (?, ?, ?, ?)",
        (id_evenement, _nettoyer(nom) or "Poste", 1 if demande_experience else 0, ordre),
    )
    conn.commit()
    return cur.lastrowid


def modifier_poste(
    conn: sqlite3.Connection,
    id_poste: int,
    nom: str | None = None,
    demande_experience: bool | None = None,
) -> None:
    """Modifie le nom et/ou l'indicateur d'expérience d'un poste."""
    if nom is not None:
        conn.execute(
            "UPDATE postes SET nom = ? WHERE id_poste = ?",
            (_nettoyer(nom) or "Poste", id_poste),
        )
    if demande_experience is not None:
        conn.execute(
            "UPDATE postes SET demande_experience = ? WHERE id_poste = ?",
            (1 if demande_experience else 0, id_poste),
        )
    conn.commit()


def supprimer_poste(conn: sqlite3.Connection, id_poste: int) -> None:
    """Supprime un poste (et, par cascade, ses besoins/préférences/affectations)."""
    conn.execute("DELETE FROM postes WHERE id_poste = ?", (id_poste,))
    conn.commit()


def lister_postes(conn: sqlite3.Connection, id_evenement: int) -> list[dict]:
    """Postes d'un événement, dans l'ordre d'affichage."""
    return _rows(
        conn.execute(
            "SELECT * FROM postes WHERE id_evenement = ? ORDER BY ordre, id_poste",
            (id_evenement,),
        )
    )


# ===========================================================================
# Trame — créneaux
# ===========================================================================
def ajouter_creneau(
    conn: sqlite3.Connection,
    id_evenement: int,
    libelle_jour: str,
    debut_local: str,
    fin_local: str,
    type_creneau: str = "poste",
    libelle: str | None = None,
    ordre: int | None = None,
) -> int | None:
    """
    Ajoute un créneau. `debut_local`/`fin_local` sont des saisies locales
    (`datetime-local`), converties en UTC ISO pour le stockage.

    Returns:
        L'id du créneau, ou None si les bornes sont invalides.
    """
    if type_creneau not in TYPES_CRENEAU:
        type_creneau = "poste"
    debut = local_vers_utc_iso(debut_local)
    fin = local_vers_utc_iso(fin_local)
    if not debut or not fin:
        return None
    if ordre is None:
        ordre = _prochain_ordre(conn, "creneaux", id_evenement)
    cur = conn.execute(
        "INSERT INTO creneaux (id_evenement, libelle_jour, debut, fin, type, "
        "libelle, ordre) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            id_evenement,
            _nettoyer(libelle_jour) or "Jour",
            debut,
            fin,
            type_creneau,
            _nettoyer(libelle) or None,
            ordre,
        ),
    )
    conn.commit()
    return cur.lastrowid


def supprimer_creneau(conn: sqlite3.Connection, id_creneau: int) -> None:
    """Supprime un créneau (et, par cascade, ses besoins/dispos/affectations)."""
    conn.execute("DELETE FROM creneaux WHERE id_creneau = ?", (id_creneau,))
    conn.commit()


def get_creneau(conn: sqlite3.Connection, id_creneau: int) -> dict | None:
    """Renvoie un créneau (dict) ou None."""
    return _row(
        conn.execute(
            "SELECT * FROM creneaux WHERE id_creneau = ?", (id_creneau,)
        ).fetchone()
    )


def modifier_creneau(
    conn: sqlite3.Connection,
    id_creneau: int,
    libelle_jour: str | None = None,
    debut_local: str | None = None,
    fin_local: str | None = None,
    libelle: str | None = None,
) -> bool:
    """
    Modifie l'horaire (donc la durée) et/ou le libellé d'un créneau. Les bornes
    sont des saisies locales (`datetime-local`) reconverties en UTC. Un argument
    laissé à None n'est pas touché.

    Returns:
        True si au moins un champ a été modifié, False si les bornes fournies
        sont invalides (rien n'est alors écrit).
    """
    champs, valeurs = [], []
    if libelle_jour is not None:
        champs.append("libelle_jour = ?")
        valeurs.append(_nettoyer(libelle_jour) or "Jour")
    if debut_local is not None:
        debut = local_vers_utc_iso(debut_local)
        if not debut:
            return False
        champs.append("debut = ?")
        valeurs.append(debut)
    if fin_local is not None:
        fin = local_vers_utc_iso(fin_local)
        if not fin:
            return False
        champs.append("fin = ?")
        valeurs.append(fin)
    if libelle is not None:
        champs.append("libelle = ?")
        valeurs.append(_nettoyer(libelle) or None)
    if not champs:
        return False
    valeurs.append(id_creneau)
    conn.execute(f"UPDATE creneaux SET {', '.join(champs)} WHERE id_creneau = ?", valeurs)
    conn.commit()
    return True


def lister_creneaux(
    conn: sqlite3.Connection, id_evenement: int, type_creneau: str | None = None
) -> list[dict]:
    """
    Créneaux d'un événement, ordonnés (jour, ordre, début). Filtrable par type
    ('poste' pour la grille de service, 'tache' pour installation/rangement).
    """
    if type_creneau is None:
        rows = conn.execute(
            "SELECT * FROM creneaux WHERE id_evenement = ? "
            "ORDER BY libelle_jour, ordre, debut",
            (id_evenement,),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM creneaux WHERE id_evenement = ? AND type = ? "
            "ORDER BY libelle_jour, ordre, debut",
            (id_evenement, type_creneau),
        )
    return _rows(rows)


# ===========================================================================
# Trame — besoins (nb de personnes par créneau × poste)
# ===========================================================================
def definir_besoin(
    conn: sqlite3.Connection, id_creneau: int, id_poste: int, nb_requis: int
) -> None:
    """Définit (upsert) le nombre de personnes requis sur une case. 0 = grisé."""
    conn.execute(
        "INSERT INTO besoins (id_creneau, id_poste, nb_requis) VALUES (?, ?, ?) "
        "ON CONFLICT(id_creneau, id_poste) DO UPDATE SET nb_requis = excluded.nb_requis",
        (id_creneau, id_poste, max(0, int(nb_requis))),
    )
    conn.commit()


def matrice_besoins(conn: sqlite3.Connection, id_evenement: int) -> dict[tuple[int, int], int]:
    """Renvoie {(id_creneau, id_poste): nb_requis} pour tout l'événement (>0)."""
    rows = conn.execute(
        "SELECT b.id_creneau, b.id_poste, b.nb_requis FROM besoins b "
        "JOIN creneaux c ON c.id_creneau = b.id_creneau "
        "WHERE c.id_evenement = ? AND b.nb_requis > 0",
        (id_evenement,),
    )
    return {(r["id_creneau"], r["id_poste"]): r["nb_requis"] for r in rows}


# ===========================================================================
# Duplication de la trame (gain de temps d'une édition à l'autre)
# ===========================================================================
def _decaler_iso(horodatage_iso: str, delta: timedelta) -> str:
    """Décale un horodatage UTC ISO d'un `delta` ; renvoie tel quel si illisible."""
    try:
        return (datetime.fromisoformat(horodatage_iso) + delta).isoformat()
    except (ValueError, TypeError):
        return horodatage_iso


def dupliquer_trame(
    conn: sqlite3.Connection, id_source: int, nom: str, decalage_jours: int = 0
) -> int:
    """
    Crée un nouvel événement (état 'collecte') en recopiant la TRAME de
    `id_source` : postes, créneaux et besoins. NE recopie PAS les bénévoles ni
    les affectations (copie indépendante repartant à zéro côté souhaits).

    `decalage_jours` (0 par défaut, comportement inchangé) décale `debut` et
    `fin` de chaque créneau copié d'un nombre ENTIER de jours — préserve donc
    le jour de semaine et l'heure locale (`libelle_jour` recopié tel quel,
    jamais recalculé). Cas limite assumé : un décalage de plusieurs semaines
    peut faire varier l'heure locale d'1h autour d'un changement d'heure
    (DST) — acceptable pour un événement annuel de même saison.

    Returns:
        L'id du nouvel événement.
    """
    nouveau = creer_evenement(conn, nom)

    # Postes : on garde la correspondance ancien->nouveau pour les besoins.
    corr_postes: dict[int, int] = {}
    for p in lister_postes(conn, id_source):
        corr_postes[p["id_poste"]] = ajouter_poste(
            conn, nouveau, p["nom"], bool(p["demande_experience"]), p["ordre"]
        )

    # Créneaux : on recopie les bornes UTC telles quelles (sauf décalage demandé).
    delta = timedelta(days=decalage_jours)
    corr_creneaux: dict[int, int] = {}
    for c in lister_creneaux(conn, id_source):
        debut, fin = c["debut"], c["fin"]
        if decalage_jours:
            debut = _decaler_iso(debut, delta)
            fin = _decaler_iso(fin, delta)
        cur = conn.execute(
            "INSERT INTO creneaux (id_evenement, libelle_jour, debut, fin, type, "
            "libelle, ordre) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nouveau, c["libelle_jour"], debut, fin, c["type"],
             c["libelle"], c["ordre"]),
        )
        corr_creneaux[c["id_creneau"]] = cur.lastrowid

    # Besoins : reportés via les correspondances.
    for (id_cr, id_po), nb in matrice_besoins(conn, id_source).items():
        conn.execute(
            "INSERT INTO besoins (id_creneau, id_poste, nb_requis) VALUES (?, ?, ?)",
            (corr_creneaux[id_cr], corr_postes[id_po], nb),
        )
    conn.commit()
    return nouveau


# ===========================================================================
# Collecte des souhaits (bénévole)
# ===========================================================================
def enregistrer_souhaits(
    conn: sqlite3.Connection,
    id_evenement: int,
    nom: str,
    contact: str | None = None,
    max_heures=None,
    note: str | None = None,
    dispos: set[int] | None = None,
    preferences: dict[int, str] | None = None,
    code_modif: str | None = None,
) -> dict:
    """
    Enregistre (ou met à jour) la réponse d'un bénévole : identité, plafond
    d'heures, disponibilités (set d'id de créneaux) et préférences par poste
    (dict {id_poste: niveau}). Si `code_modif` correspond à une réponse existante
    de cet événement, elle est MISE À JOUR ; sinon une nouvelle est créée.

    La collecte n'est acceptée que si l'événement est en état 'collecte'.

    Returns:
        {ok: bool, raison: str|None, id: int|None, code: str|None}
    """
    ev = get_evenement(conn, id_evenement)
    if ev is None:
        return {"ok": False, "raison": "introuvable", "id": None, "code": None}
    if ev["etat"] != "collecte":
        return {"ok": False, "raison": "fermee", "id": None, "code": None}

    nom = _nettoyer(nom)
    if not nom:
        return {"ok": False, "raison": "nom_vide", "id": None, "code": None}

    # Filtre les id de créneaux / postes appartenant bien à cet événement.
    creneaux_valides = {c["id_creneau"] for c in lister_creneaux(conn, id_evenement)}
    postes_valides = {p["id_poste"] for p in lister_postes(conn, id_evenement)}
    dispos = {c for c in (dispos or set()) if c in creneaux_valides}
    preferences = {
        p: n
        for p, n in (preferences or {}).items()
        if p in postes_valides and n in NIVEAUX_PREFERENCE
    }
    max_h = _parse_max_heures(max_heures)
    contact = _nettoyer(contact, 120) or None
    note = _nettoyer(note, 500) or None

    # Réponse existante (édition) ou nouvelle.
    existant = None
    if code_modif:
        existant = _row(
            conn.execute(
                "SELECT * FROM benevoles WHERE code_modif = ? AND id_evenement = ?",
                (code_modif, id_evenement),
            ).fetchone()
        )

    if existant:
        id_benevole = existant["id_benevole"]
        code = existant["code_modif"]
        conn.execute(
            "UPDATE benevoles SET nom = ?, contact = ?, max_heures = ?, note = ?, "
            "date_reponse = ? WHERE id_benevole = ?",
            (nom, contact, max_h, note, maintenant(), id_benevole),
        )
        conn.execute("DELETE FROM disponibilites WHERE id_benevole = ?", (id_benevole,))
        conn.execute("DELETE FROM preferences WHERE id_benevole = ?", (id_benevole,))
    else:
        code = _generer_code()
        cur = conn.execute(
            "INSERT INTO benevoles (id_evenement, nom, contact, max_heures, note, "
            "code_modif, date_reponse) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id_evenement, nom, contact, max_h, note, code, maintenant()),
        )
        id_benevole = cur.lastrowid

    for id_creneau in dispos:
        conn.execute(
            "INSERT INTO disponibilites (id_benevole, id_creneau, disponible) "
            "VALUES (?, ?, 1)",
            (id_benevole, id_creneau),
        )
    for id_poste, niveau in preferences.items():
        conn.execute(
            "INSERT INTO preferences (id_benevole, id_poste, niveau) VALUES (?, ?, ?)",
            (id_benevole, id_poste, niveau),
        )
    conn.commit()
    return {"ok": True, "raison": None, "id": id_benevole, "code": code}


def get_benevole(conn: sqlite3.Connection, id_benevole: int) -> dict | None:
    """Renvoie un bénévole (dict) ou None."""
    return _row(
        conn.execute(
            "SELECT * FROM benevoles WHERE id_benevole = ?", (id_benevole,)
        ).fetchone()
    )


def get_benevole_par_code(
    conn: sqlite3.Connection, code: str, id_evenement: int | None = None
) -> dict | None:
    """Retrouve un bénévole par son code de modification (édition de réponse)."""
    if not code:
        return None
    if id_evenement is None:
        row = conn.execute(
            "SELECT * FROM benevoles WHERE code_modif = ?", (code,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM benevoles WHERE code_modif = ? AND id_evenement = ?",
            (code, id_evenement),
        ).fetchone()
    return _row(row)


def lister_benevoles(conn: sqlite3.Connection, id_evenement: int) -> list[dict]:
    """Bénévoles ayant répondu, par ordre alphabétique de nom."""
    return _rows(
        conn.execute(
            "SELECT * FROM benevoles WHERE id_evenement = ? "
            "ORDER BY nom COLLATE NOCASE, id_benevole",
            (id_evenement,),
        )
    )


def dispos_du_benevole(conn: sqlite3.Connection, id_benevole: int) -> set[int]:
    """Ensemble des id de créneaux où le bénévole est disponible."""
    return {
        r["id_creneau"]
        for r in conn.execute(
            "SELECT id_creneau FROM disponibilites "
            "WHERE id_benevole = ? AND disponible = 1",
            (id_benevole,),
        )
    }


def prefs_du_benevole(conn: sqlite3.Connection, id_benevole: int) -> dict[int, str]:
    """Préférences du bénévole : {id_poste: niveau}."""
    return {
        r["id_poste"]: r["niveau"]
        for r in conn.execute(
            "SELECT id_poste, niveau FROM preferences WHERE id_benevole = ?",
            (id_benevole,),
        )
    }


def supprimer_benevole(conn: sqlite3.Connection, id_benevole: int) -> None:
    """Supprime un bénévole et toutes ses données (dispos, prefs, affectations)."""
    conn.execute("DELETE FROM benevoles WHERE id_benevole = ?", (id_benevole,))
    conn.commit()


def compter_reponses(conn: sqlite3.Connection, id_evenement: int) -> int:
    """Nombre de bénévoles ayant répondu pour cet événement."""
    return conn.execute(
        "SELECT COUNT(*) FROM benevoles WHERE id_evenement = ?", (id_evenement,)
    ).fetchone()[0]


# ===========================================================================
# Affectations (édition manuelle de la grille)
# ===========================================================================
def affecter(
    conn: sqlite3.Connection,
    id_creneau: int,
    id_poste: int | None,
    id_benevole: int,
    origine: str = "manuel",
    verrouille: bool = False,
) -> int | None:
    """
    Place un bénévole sur une case (créneau × poste ; poste None = tâche).

    Contrainte DURE « pas deux postes en même temps » : on REFUSE si le bénévole
    a déjà une affectation sur ce même créneau (quel que soit le poste). Cela
    couvre aussi le doublon exact. Renvoie l'id d'affectation, ou None si l'ajout
    est refusé (déjà présent sur le créneau).
    """
    deja = conn.execute(
        "SELECT id_affectation FROM affectations "
        "WHERE id_creneau = ? AND id_benevole = ?",
        (id_creneau, id_benevole),
    ).fetchone()
    if deja:
        return None
    cur = conn.execute(
        "INSERT INTO affectations (id_creneau, id_poste, id_benevole, verrouille, "
        "origine) VALUES (?, ?, ?, ?, ?)",
        (id_creneau, id_poste, id_benevole, 1 if verrouille else 0, origine),
    )
    conn.commit()
    return cur.lastrowid


def retirer_affectation(conn: sqlite3.Connection, id_affectation: int) -> None:
    """Retire une affectation de la grille."""
    conn.execute("DELETE FROM affectations WHERE id_affectation = ?", (id_affectation,))
    conn.commit()


def basculer_verrou(conn: sqlite3.Connection, id_affectation: int) -> None:
    """Verrouille/déverrouille une affectation (case figée au re-préremplissage)."""
    conn.execute(
        "UPDATE affectations SET verrouille = 1 - verrouille WHERE id_affectation = ?",
        (id_affectation,),
    )
    conn.commit()


def get_affectation(conn: sqlite3.Connection, id_affectation: int) -> dict | None:
    """Renvoie une affectation (dict) ou None."""
    return _row(
        conn.execute(
            "SELECT * FROM affectations WHERE id_affectation = ?", (id_affectation,)
        ).fetchone()
    )


def remplacer_affectation(
    conn: sqlite3.Connection, id_affectation: int, nouveau_benevole: int
) -> int | None:
    """
    Remplace le bénévole d'une affectation par un autre, en gardant la même case
    (créneau × poste) et l'état de verrouillage. Sert au geste « remplacer Jeanne
    par Juliette » sur la grille.

    Returns:
        L'id de la nouvelle affectation, ou None (affectation introuvable, ou le
        nouveau bénévole est déjà placé sur ce créneau ailleurs).
    """
    a = get_affectation(conn, id_affectation)
    if a is None:
        return None
    # Refuse AVANT de supprimer si le nouveau est déjà sur ce créneau (sur une
    # autre case) : sinon on perdrait l'affectation d'origine sans la remplacer.
    conflit = conn.execute(
        "SELECT 1 FROM affectations WHERE id_creneau = ? AND id_benevole = ? "
        "AND id_affectation <> ?",
        (a["id_creneau"], nouveau_benevole, id_affectation),
    ).fetchone()
    if conflit:
        return None
    conn.execute("DELETE FROM affectations WHERE id_affectation = ?", (id_affectation,))
    conn.commit()
    return affecter(
        conn, a["id_creneau"], a["id_poste"], nouveau_benevole,
        origine="manuel", verrouille=bool(a["verrouille"]),
    )


def affectations_de_case(
    conn: sqlite3.Connection, id_creneau: int, id_poste: int | None
) -> list[dict]:
    """Affectations d'une case (créneau × poste ; poste None = tâche), avec nom."""
    return _rows(
        conn.execute(
            "SELECT a.*, b.nom AS nom_benevole FROM affectations a "
            "JOIN benevoles b ON b.id_benevole = a.id_benevole "
            "WHERE a.id_creneau = ? AND (a.id_poste IS ? OR a.id_poste = ?) "
            "ORDER BY b.nom COLLATE NOCASE",
            (id_creneau, id_poste, id_poste),
        )
    )


def _affectations_evenement(conn: sqlite3.Connection, id_evenement: int) -> list[dict]:
    """Toutes les affectations de l'événement (jointes au nom du bénévole)."""
    return _rows(
        conn.execute(
            "SELECT a.*, b.nom AS nom_benevole FROM affectations a "
            "JOIN benevoles b ON b.id_benevole = a.id_benevole "
            "JOIN creneaux c ON c.id_creneau = a.id_creneau "
            "WHERE c.id_evenement = ? "
            "ORDER BY b.nom COLLATE NOCASE",
            (id_evenement,),
        )
    )


# ===========================================================================
# PRÉREMPLISSAGE GLOUTON « DÉGROSSI » (cœur, conception §6)
# ===========================================================================
def prefiller(conn: sqlite3.Connection, id_evenement: int) -> dict:
    """
    Génère un brouillon de planning par un algorithme glouton.

    Principe (docs/conception-planning.md §6) :
      1. On efface les affectations NON verrouillées (les verrouillées sont
         conservées et comptées comme déjà posées).
      2. On classe les cases (créneau × poste, besoin > 0) de la plus tendue à la
         moins tendue (le moins de candidats disponibles d'abord).
      3. Pour chaque case, on complète jusqu'au besoin en piochant parmi les
         bénévoles disponibles, par ordre de préférence
         (prefere → ok → neutre → si_vraiment), en EXCLUANT « surtout_pas » et
         ceux qui dépasseraient leur plafond d'heures.
      4. À préférence égale, on arbitre par une charge « effective » mêlant
         ÉQUITÉ et CONTINUITÉ (phase 2) : on part des heures déjà affectées
         (le moins chargé d'abord = équité) et on accorde un RABAIS
         `CONTINUITE_BONUS_H` au bénévole déjà placé sur le MÊME poste à un
         créneau CONTIGU (on garde la même personne sur des créneaux qui se
         suivent). L'équité reprend le dessus si un autre bénévole est nettement
         moins chargé (écart supérieur au rabais).
      5. On LAISSE VIDE toute case qu'on ne peut pas remplir proprement.

    Contraintes DURES respectées : disponibilité, « surtout_pas », plafond
    d'heures, pas deux postes en même temps. Contraintes MOLLES prises en compte :
    continuité sur créneaux contigus + équité de répartition des heures.
    L'expérience requise reste un affinement ultérieur.

    Returns:
        {places: int, cases_completes: int, cases_total: int} (bilan rapide).
    """
    postes = lister_postes(conn, id_evenement)
    creneaux = {c["id_creneau"]: c for c in lister_creneaux(conn, id_evenement, "poste")}
    besoins = matrice_besoins(conn, id_evenement)
    benevoles = lister_benevoles(conn, id_evenement)

    # Adjacence des créneaux : deux créneaux sont CONTIGUS si la fin de l'un est
    # le début de l'autre (comparaison de chaînes ISO UTC, même format partout).
    adjacents: dict[int, set[int]] = {cid: set() for cid in creneaux}
    _liste_cr = list(creneaux.values())
    for i, a in enumerate(_liste_cr):
        for b in _liste_cr[i + 1:]:
            if a["fin"] == b["debut"] or b["fin"] == a["debut"]:
                adjacents[a["id_creneau"]].add(b["id_creneau"])
                adjacents[b["id_creneau"]].add(a["id_creneau"])

    # Souhaits chargés une fois en mémoire (perf + lisibilité de l'algorithme).
    dispo = {b["id_benevole"]: dispos_du_benevole(conn, b["id_benevole"]) for b in benevoles}
    prefs = {b["id_benevole"]: prefs_du_benevole(conn, b["id_benevole"]) for b in benevoles}
    max_h = {b["id_benevole"]: b["max_heures"] for b in benevoles}

    # 1. Efface les affectations non verrouillées de l'événement.
    conn.execute(
        "DELETE FROM affectations WHERE verrouille = 0 AND id_creneau IN "
        "(SELECT id_creneau FROM creneaux WHERE id_evenement = ?)",
        (id_evenement,),
    )
    conn.commit()

    # État courant déduit des affectations restantes (verrouillées).
    heures: dict[int, float] = {b["id_benevole"]: 0.0 for b in benevoles}
    occupe: dict[int, set[int]] = {}                 # id_creneau -> {id_benevole}
    rempli: dict[tuple[int, int], int] = {}          # (creneau, poste) -> count
    place_sur: dict[int, set[tuple[int, int]]] = {}  # id_benevole -> {(creneau, poste)}
    for a in _affectations_evenement(conn, id_evenement):
        occupe.setdefault(a["id_creneau"], set()).add(a["id_benevole"])
        if a["id_creneau"] in creneaux:
            heures[a["id_benevole"]] = heures.get(a["id_benevole"], 0.0) + duree_heures(
                creneaux[a["id_creneau"]]
            )
        if a["id_poste"] is not None:
            rempli[(a["id_creneau"], a["id_poste"])] = (
                rempli.get((a["id_creneau"], a["id_poste"]), 0) + 1
            )
            place_sur.setdefault(a["id_benevole"], set()).add(
                (a["id_creneau"], a["id_poste"])
            )

    def _continuite(bid: int, id_creneau: int, id_poste: int) -> bool:
        """Vrai si le bénévole tient déjà le MÊME poste sur un créneau contigu."""
        return any(
            (voisin, id_poste) in place_sur.get(bid, set())
            for voisin in adjacents.get(id_creneau, set())
        )

    def candidats(id_creneau: int, id_poste: int) -> list[int]:
        """Bénévoles éligibles à une case (contraintes dures), sans tri."""
        out = []
        for b in benevoles:
            bid = b["id_benevole"]
            if id_creneau not in dispo[bid]:
                continue                              # non disponible
            if prefs[bid].get(id_poste) == "surtout_pas":
                continue                              # exclusion explicite
            out.append(bid)
        return out

    # 2. Cases à pourvoir, de la plus tendue à la moins tendue.
    cases = sorted(
        besoins.keys(),
        key=lambda k: (
            len(candidats(*k)),                       # moins de candidats d'abord
            creneaux.get(k[0], {}).get("ordre", 0) if k[0] in creneaux else 0,
            k[0],
            k[1],
        ),
    )

    places = 0
    for (id_creneau, id_poste) in cases:
        if id_creneau not in creneaux:
            continue
        besoin = besoins[(id_creneau, id_poste)]
        duree = duree_heures(creneaux[id_creneau])

        def triables() -> list[int]:
            """Candidats encore plaçables sur cette case, triés par priorité."""
            libres = []
            for bid in candidats(id_creneau, id_poste):
                if bid in occupe.get(id_creneau, set()):
                    continue                          # déjà pris sur ce créneau
                plafond = max_h[bid]
                if plafond is not None and heures[bid] + duree > plafond + 1e-9:
                    continue                          # dépasserait le plafond
                libres.append(bid)
            def cle(bid: int):
                continu = _continuite(bid, id_creneau, id_poste)
                return (
                    _RANG_PREFERENCE.get(prefs[bid].get(id_poste), 2),  # préférence
                    # Charge effective : équité (moins d'heures) avec un rabais
                    # de continuité si déjà sur le même poste à un créneau contigu.
                    heures[bid] - (CONTINUITE_BONUS_H if continu else 0.0),
                    0 if continu else 1,               # à effet égal, continuité d'abord
                    bid,                               # déterministe
                )

            return sorted(libres, key=cle)

        while rempli.get((id_creneau, id_poste), 0) < besoin:
            ordre = triables()
            if not ordre:
                break                                 # on laisse le trou
            bid = ordre[0]
            affecter(conn, id_creneau, id_poste, bid, origine="auto", verrouille=False)
            occupe.setdefault(id_creneau, set()).add(bid)
            place_sur.setdefault(bid, set()).add((id_creneau, id_poste))
            heures[bid] += duree
            rempli[(id_creneau, id_poste)] = rempli.get((id_creneau, id_poste), 0) + 1
            places += 1

    cases_completes = sum(
        1 for k, n in besoins.items() if rempli.get(k, 0) >= n
    )
    return {
        "places": places,
        "cases_completes": cases_completes,
        "cases_total": len(besoins),
    }


# ===========================================================================
# Grille (affichage / édition / exports) + analyse de couverture
# ===========================================================================
def jours_chronologiques(creneaux: list[dict]) -> list[str]:
    """
    Ordre CHRONOLOGIQUE des libellés de jour (« Samedi », « Dimanche »...)
    d'une liste de créneaux : trié par l'horodatage du PREMIER créneau de
    chaque jour (MIN(debut), en UTC ISO donc triable lexicalement), et non par
    le libellé lui-même — un texte libre qui peut être alphabétiquement
    inversé par rapport à la chronologie réelle (ex. « Dimanche 13 sept. » <
    « Samedi 12 sept. »). Voir docs/idees-ux.md M2.

    Utilisée PARTOUT où les créneaux sont groupés par jour (`construire_grille`
    ci-dessous, formulaire de collecte dans `routes.py`) pour ne jamais
    dépendre à nouveau de l'ordre alphabétique. Les exports Excel/PDF partent
    de `construire_grille` : ils héritent automatiquement du même ordre.
    """
    premier_debut: dict[str, str] = {}
    for c in creneaux:
        lib = c["libelle_jour"]
        debut = c["debut"]
        if lib not in premier_debut or debut < premier_debut[lib]:
            premier_debut[lib] = debut
    return sorted(premier_debut, key=lambda lib: premier_debut[lib])


def construire_grille(conn: sqlite3.Connection, id_evenement: int) -> dict:
    """
    Construit la structure complète du planning pour l'affichage, l'édition et
    les exports : postes, créneaux groupés par jour, besoins et affectations.

    Structure renvoyée :
        {
          "postes": [poste, ...],
          "jours": [
            {"libelle": "Samedi",
             "creneaux": [
               {"creneau": {...},
                "cases": [{"poste": {...}, "nb_requis": n,
                           "affectations": [{id_affectation, id_benevole, nom,
                                             verrouille, origine}, ...]}],
               }],
            }],
          "taches": [{"creneau": {...}, "affectations": [...]}],
        }
    """
    postes = lister_postes(conn, id_evenement)
    besoins = matrice_besoins(conn, id_evenement)

    # Affectations indexées par (créneau, poste) et par créneau (tâches).
    par_case: dict[tuple[int, int], list[dict]] = {}
    par_tache: dict[int, list[dict]] = {}
    for a in _affectations_evenement(conn, id_evenement):
        item = {
            "id_affectation": a["id_affectation"],
            "id_benevole": a["id_benevole"],
            "nom": a["nom_benevole"],
            "verrouille": bool(a["verrouille"]),
            "origine": a["origine"],
        }
        if a["id_poste"] is None:
            par_tache.setdefault(a["id_creneau"], []).append(item)
        else:
            par_case.setdefault((a["id_creneau"], a["id_poste"]), []).append(item)

    # Créneaux de service, groupés par jour et triés CHRONOLOGIQUEMENT (par le
    # premier créneau de chaque jour, pas par le libellé — voir M2/idees-ux.md
    # et jours_chronologiques() ci-dessus).
    creneaux_service = lister_creneaux(conn, id_evenement, "poste")
    index_jour: dict[str, dict] = {
        lib: {"libelle": lib, "creneaux": []} for lib in jours_chronologiques(creneaux_service)
    }
    for c in creneaux_service:
        cases = []
        for p in postes:
            nb = besoins.get((c["id_creneau"], p["id_poste"]), 0)
            cases.append(
                {
                    "poste": p,
                    "nb_requis": nb,
                    "affectations": par_case.get((c["id_creneau"], p["id_poste"]), []),
                }
            )
        index_jour[c["libelle_jour"]]["creneaux"].append({"creneau": c, "cases": cases})
    jours = list(index_jour.values())

    # Tâches ponctuelles (sans poste).
    taches = [
        {"creneau": c, "affectations": par_tache.get(c["id_creneau"], [])}
        for c in lister_creneaux(conn, id_evenement, "tache")
    ]

    return {"postes": postes, "jours": jours, "taches": taches}


def analyser_couverture(conn: sqlite3.Connection, id_evenement: int) -> dict:
    """
    Compare besoins et affectations : recense les TROUS (cases sous-pourvues) et
    les sur-affectations. Sert au bilan affiché à l'admin après préremplissage.

    Returns:
        {
          "trous": [{id_creneau, libelle_jour, creneau, poste, requis, places, manque}],
          "surcharges": [...même forme, "exces"],
          "total_requis": int, "total_places": int,
        }
    """
    postes = {p["id_poste"]: p for p in lister_postes(conn, id_evenement)}
    creneaux = {c["id_creneau"]: c for c in lister_creneaux(conn, id_evenement, "poste")}
    besoins = matrice_besoins(conn, id_evenement)

    places: dict[tuple[int, int], int] = {}
    for a in _affectations_evenement(conn, id_evenement):
        if a["id_poste"] is not None:
            places[(a["id_creneau"], a["id_poste"])] = (
                places.get((a["id_creneau"], a["id_poste"]), 0) + 1
            )

    trous, surcharges = [], []
    total_requis = total_places = 0
    for (id_creneau, id_poste), requis in besoins.items():
        if id_creneau not in creneaux:
            continue
        n = places.get((id_creneau, id_poste), 0)
        total_requis += requis
        total_places += min(n, requis)
        ligne = {
            "id_creneau": id_creneau,
            "libelle_jour": creneaux[id_creneau]["libelle_jour"],
            "creneau": creneaux[id_creneau],
            "poste": postes.get(id_poste),
            "requis": requis,
            "places": n,
        }
        if n < requis:
            trous.append({**ligne, "manque": requis - n})
        elif n > requis:
            surcharges.append({**ligne, "exces": n - requis})

    return {
        "trous": trous,
        "surcharges": surcharges,
        "total_requis": total_requis,
        "total_places": total_places,
    }


def planning_du_benevole(conn: sqlite3.Connection, id_benevole: int) -> list[dict]:
    """
    « Mon planning » : les créneaux/postes affectés à un bénévole, triés
    chronologiquement. Chaque entrée : {creneau, poste (ou None), id_affectation}.
    """
    rows = conn.execute(
        "SELECT a.id_affectation, a.id_poste, c.* FROM affectations a "
        "JOIN creneaux c ON c.id_creneau = a.id_creneau "
        "WHERE a.id_benevole = ? ORDER BY c.debut, c.ordre",
        (id_benevole,),
    )
    postes = {}
    sortie = []
    for r in rows:
        d = dict(r)
        id_poste = d.pop("id_poste")
        if id_poste is not None and id_poste not in postes:
            postes[id_poste] = _row(
                conn.execute(
                    "SELECT * FROM postes WHERE id_poste = ?", (id_poste,)
                ).fetchone()
            )
        sortie.append(
            {
                "id_affectation": d.pop("id_affectation"),
                "poste": postes.get(id_poste) if id_poste is not None else None,
                "creneau": d,
            }
        )
    return sortie


# ===========================================================================
# Export iCalendar (.ics) — « Ajouter tout mon planning à mon agenda »
#
# Module `planning` totalement indépendant de `tournoi` (bases séparées, pas
# d'import croisé, cf. CLAUDE.md) : les helpers d'échappement/horodatage sont
# donc dupliqués ici plutôt que factorisés avec `tournoi.services.ical_tournoi`.
# ===========================================================================
def _ics_echappe(texte: str | None) -> str:
    """Échappe un texte pour un champ iCalendar (RFC 5545)."""
    return (
        (texte or "")
        .replace("\\", "\\\\").replace(";", "\\;")
        .replace(",", "\\,").replace("\n", "\\n")
    )


def _ics_horodatage(dt: datetime) -> str:
    """Formate un datetime aware en horodatage UTC iCalendar (AAAAMMJJTHHMMSSZ)."""
    return dt.astimezone(FUSEAU_UTC).strftime("%Y%m%dT%H%M%SZ")


def ical_planning_benevole(conn: sqlite3.Connection, id_benevole: int) -> str | None:
    """
    Construit le contenu iCalendar (.ics) — multi-VEVENT — de « mon planning »
    pour un bénévole : un événement par affectation (poste ou tâche). Aucune
    donnée personnelle dans le fichier (pas de nom). Renvoie None si le
    bénévole n'a aucune affectation.
    """
    affectations = planning_du_benevole(conn, id_benevole)
    if not affectations:
        return None

    dtstamp = _ics_horodatage(datetime.now(FUSEAU_UTC))
    lignes = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{NOM_ASSOCIATION}//Planning//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for a in affectations:
        c = a["creneau"]
        try:
            debut = datetime.fromisoformat(c["debut"])
            fin = datetime.fromisoformat(c["fin"])
        except (ValueError, TypeError):
            continue
        if a["poste"]:
            resume = a["poste"]["nom"]
        else:
            resume = c["libelle"] or "Tâche"
        description = f"{c['libelle_jour']} — {NOM_ASSOCIATION}"
        lignes += [
            "BEGIN:VEVENT",
            f"UID:planning-{a['id_affectation']}-{_ics_horodatage(debut)}@desjeuxpleinlamanche",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{_ics_horodatage(debut)}",
            f"DTEND:{_ics_horodatage(fin)}",
            f"SUMMARY:{_ics_echappe(resume)}",
            f"DESCRIPTION:{_ics_echappe(description)}",
            "END:VEVENT",
        ]
    lignes.append("END:VCALENDAR")
    # iCalendar impose des fins de ligne CRLF.
    return "\r\n".join(lignes) + "\r\n"


# ===========================================================================
# Helper interne — ordre d'affichage incrémental
# ===========================================================================
def _prochain_ordre(conn: sqlite3.Connection, table: str, id_evenement: int) -> int:
    """Renvoie le prochain `ordre` (max + 1) pour postes/creneaux d'un événement."""
    row = conn.execute(
        f"SELECT COALESCE(MAX(ordre), -1) + 1 FROM {table} WHERE id_evenement = ?",
        (id_evenement,),
    ).fetchone()
    return int(row[0])
