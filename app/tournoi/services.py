"""
Logique métier du module « Tournois » (socle de la phase 1).

ISOLE toute la logique (état, inscriptions, participants) hors des routes, comme
`app/services.py` pour le prêt. Chaque fonction reçoit une connexion à la base
des tournois (`app/tournoi/db.py`) et ne s'occupe que des données.

PÉRIMÈTRE (socle) : CRUD tournois, machine à états brouillon↔inscriptions(+
terminé), inscription publique (pseudo + code de désinscription), gestion
manuelle des participants par le bénévole. Les modes de scoring (génération des
rencontres, classements) viendront ensuite et s'appuieront sur la table
`rencontres`.

DATES : on réutilise les helpers de `app/services.py` (stockage UTC ISO,
affichage/saisie en heure locale Europe/Paris) pour ne pas dupliquer la logique
de fuseau.

RGPD : aucune adresse e-mail n'est jamais stockée. Le code de désinscription est
le seul jeton conservé (et affiché à l'écran à l'inscription).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime

from app.services import FUSEAU_LOCAL, maintenant  # fuseau + horodatage UTC ISO partagés
from app.tournoi.models import ETATS

# Bornes de saisie.
PSEUDO_MAX = 40           # longueur maximale d'un pseudo
CODE_OCTETS = 8           # entropie du code de désinscription (token_urlsafe)

# Transitions d'état autorisées (machine à états, conception §5). Le lancement
# (passage à 'lance') se fait toujours depuis 'inscriptions', via lancer_tournoi.
TRANSITIONS = {
    "brouillon": {"inscriptions"},
    "inscriptions": {"brouillon", "lance", "termine"},
    "lance": {"termine"},
    "termine": set(),
}

# Modes de scoring disponibles (conception §6). Implémentés un par un : pour
# l'instant seul 'high_score'. Les libellés servent à l'affichage et aux menus.
MODES_SCORING = {
    "high_score": "High score (points cumulés)",
}


# ===========================================================================
# Helpers
# ===========================================================================
def _generer_code() -> str:
    """Code de désinscription aléatoire (URL-safe, ~11 caractères)."""
    return secrets.token_urlsafe(CODE_OCTETS)


def _nettoyer_pseudo(pseudo: str) -> str:
    """Nettoie un pseudo : espaces normalisés, tronqué à PSEUDO_MAX."""
    return " ".join((pseudo or "").split())[:PSEUDO_MAX]


def _row(r: sqlite3.Row | None) -> dict | None:
    """Convertit une ligne SQLite en dict (ou None)."""
    return dict(r) if r is not None else None


def iso_utc_vers_datetime_local(iso_utc: str | None) -> str:
    """
    Convertit un horodatage UTC ISO en valeur d'input HTML `datetime-local`
    ('AAAA-MM-JJTHH:MM' en heure locale), pour pré-remplir un formulaire d'édition.
    Renvoie '' si l'entrée est vide/invalide.
    """
    if not iso_utc:
        return ""
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return ""
    return dt.astimezone(FUSEAU_LOCAL).strftime("%Y-%m-%dT%H:%M")


# ===========================================================================
# CRUD tournois
# ===========================================================================
def creer_tournoi(
    conn: sqlite3.Connection,
    nom: str,
    *,
    jeu: str | None = None,
    date_heure: str | None = None,
    duree_min: int | None = None,
    nb_places: int | None = None,
    emplacement: str | None = None,
    inscription_en_ligne: bool = True,
    bo3: bool = False,
    restriction_nombre: int | None = None,
) -> int:
    """
    Crée un tournoi à l'état 'brouillon' et renvoie son id.

    `date_heure` est attendu en UTC ISO (la route convertit la saisie locale).
    """
    cur = conn.execute(
        """
        INSERT INTO tournois
            (nom, jeu, date_heure, duree_min, nb_places, emplacement,
             inscription_en_ligne, etat, bo3, restriction_nombre, date_creation)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'brouillon', ?, ?, ?)
        """,
        (nom.strip(), (jeu or "").strip() or None, date_heure, duree_min,
         nb_places, (emplacement or "").strip() or None,
         1 if inscription_en_ligne else 0, 1 if bo3 else 0,
         restriction_nombre, maintenant()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_tournoi(conn: sqlite3.Connection, id_tournoi: int) -> dict | None:
    """Retourne le tournoi (dict) ou None s'il n'existe pas."""
    return _row(
        conn.execute(
            "SELECT * FROM tournois WHERE id_tournoi = ?", (id_tournoi,)
        ).fetchone()
    )


def modifier_tournoi(conn: sqlite3.Connection, id_tournoi: int, **champs) -> None:
    """
    Met à jour les champs fournis d'un tournoi (édition bénévole).

    Seules les colonnes connues sont prises en compte ; les autres sont ignorées.
    """
    colonnes = {
        "nom", "jeu", "date_heure", "duree_min", "nb_places", "emplacement",
        "inscription_en_ligne", "bo3", "restriction_nombre",
    }
    maj = {c: v for c, v in champs.items() if c in colonnes}
    if not maj:
        return
    assignations = ", ".join(f"{c} = ?" for c in maj)
    conn.execute(
        f"UPDATE tournois SET {assignations} WHERE id_tournoi = ?",
        (*maj.values(), id_tournoi),
    )
    conn.commit()


def supprimer_tournoi(conn: sqlite3.Connection, id_tournoi: int) -> None:
    """
    Supprime un tournoi et, par cascade (FK ON DELETE CASCADE), ses inscriptions
    et rencontres. Action irréversible (la route impose une double confirmation).
    """
    conn.execute("DELETE FROM tournois WHERE id_tournoi = ?", (id_tournoi,))
    conn.commit()


def changer_etat(conn: sqlite3.Connection, id_tournoi: int, nouvel_etat: str) -> bool:
    """
    Change l'état d'un tournoi si la transition est autorisée (machine à états).

    Returns:
        True si la transition a eu lieu, False sinon (état inconnu, tournoi
        absent ou transition non permise).
    """
    if nouvel_etat not in ETATS:
        return False
    t = get_tournoi(conn, id_tournoi)
    if t is None:
        return False
    if nouvel_etat not in TRANSITIONS.get(t["etat"], set()):
        return False
    conn.execute(
        "UPDATE tournois SET etat = ? WHERE id_tournoi = ?",
        (nouvel_etat, id_tournoi),
    )
    conn.commit()
    return True


def phase(etat: str) -> str:
    """
    Range un état dans une phase d'affichage public : 'a_venir', 'en_cours' ou
    'termine'. Le brouillon est rangé en 'a_venir' (mais masqué au public).
    """
    if etat == "termine":
        return "termine"
    if etat == "lance":
        return "en_cours"
    return "a_venir"


def lister_tournois(
    conn: sqlite3.Connection, *, inclure_brouillons: bool = False
) -> list[dict]:
    """
    Liste les tournois (récents d'abord), enrichis du nombre d'inscrits.

    Args:
        inclure_brouillons: True pour la vue bénévole (voir les brouillons),
            False pour le public (brouillons masqués).
    """
    lignes = conn.execute(
        """
        SELECT t.*, (
            SELECT COUNT(*) FROM inscriptions i WHERE i.id_tournoi = t.id_tournoi
        ) AS nb_inscrits
        FROM tournois t
        ORDER BY COALESCE(t.date_heure, t.date_creation) DESC
        """
    ).fetchall()
    tournois = []
    for r in lignes:
        d = dict(r)
        if not inclure_brouillons and d["etat"] == "brouillon":
            continue
        d["phase"] = phase(d["etat"])
        d["places_restantes"] = (
            None if d["nb_places"] is None
            else max(0, d["nb_places"] - d["nb_inscrits"])
        )
        tournois.append(d)
    return tournois


# ===========================================================================
# Inscriptions / participants
# ===========================================================================
def lister_inscriptions(conn: sqlite3.Connection, id_tournoi: int) -> list[dict]:
    """Liste les participants d'un tournoi (ordre d'inscription)."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM inscriptions WHERE id_tournoi = ? ORDER BY id_inscription",
            (id_tournoi,),
        )
    ]


def compter_inscriptions(conn: sqlite3.Connection, id_tournoi: int) -> int:
    """Nombre de participants inscrits à un tournoi."""
    return conn.execute(
        "SELECT COUNT(*) FROM inscriptions WHERE id_tournoi = ?", (id_tournoi,)
    ).fetchone()[0]


def places_restantes(conn: sqlite3.Connection, tournoi: dict) -> int | None:
    """Places restantes (None si pas de plafond)."""
    if tournoi["nb_places"] is None:
        return None
    return max(0, tournoi["nb_places"] - compter_inscriptions(conn, tournoi["id_tournoi"]))


def inscription_ouverte(conn: sqlite3.Connection, tournoi: dict) -> bool:
    """
    True si l'inscription publique en ligne est possible : état 'inscriptions',
    option en ligne activée et au moins une place libre.
    """
    if tournoi["etat"] != "inscriptions" or not tournoi["inscription_en_ligne"]:
        return False
    restantes = places_restantes(conn, tournoi)
    return restantes is None or restantes > 0


def _inserer_inscription(conn: sqlite3.Connection, id_tournoi: int,
                         pseudo: str) -> dict:
    """Insère une inscription (pseudo nettoyé + code) et renvoie {id, code, pseudo}."""
    code = _generer_code()
    cur = conn.execute(
        """
        INSERT INTO inscriptions (id_tournoi, pseudo, code_desinscription, date_inscription)
        VALUES (?, ?, ?, ?)
        """,
        (id_tournoi, pseudo, code, maintenant()),
    )
    conn.commit()
    return {"id_inscription": int(cur.lastrowid), "code": code, "pseudo": pseudo}


def inscrire(conn: sqlite3.Connection, id_tournoi: int, pseudo: str) -> dict:
    """
    Inscription PUBLIQUE en ligne. Ne stocke jamais d'e-mail.

    Returns:
        {"ok": True, "code": …, "pseudo": …} en cas de succès ; sinon
        {"ok": False, "raison": "introuvable"|"fermee"|"complet"|"pseudo_vide"}.
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None:
        return {"ok": False, "raison": "introuvable"}
    pseudo = _nettoyer_pseudo(pseudo)
    if not pseudo:
        return {"ok": False, "raison": "pseudo_vide"}
    if t["etat"] != "inscriptions" or not t["inscription_en_ligne"]:
        return {"ok": False, "raison": "fermee"}
    restantes = places_restantes(conn, t)
    if restantes is not None and restantes <= 0:
        return {"ok": False, "raison": "complet"}
    res = _inserer_inscription(conn, id_tournoi, pseudo)
    return {"ok": True, **res}


def ajouter_participant(conn: sqlite3.Connection, id_tournoi: int,
                        pseudo: str) -> dict:
    """
    Ajout MANUEL d'un participant par un bénévole (jeton). Plus permissif que
    l'inscription publique : ignore l'option « en ligne » et le plafond de places
    (le bénévole décide). Refuse seulement un tournoi inexistant ou un pseudo vide.

    Returns:
        {"ok": True, "code": …, "pseudo": …} ou {"ok": False, "raison": …}.
    """
    if get_tournoi(conn, id_tournoi) is None:
        return {"ok": False, "raison": "introuvable"}
    pseudo = _nettoyer_pseudo(pseudo)
    if not pseudo:
        return {"ok": False, "raison": "pseudo_vide"}
    res = _inserer_inscription(conn, id_tournoi, pseudo)
    return {"ok": True, **res}


def supprimer_participant(conn: sqlite3.Connection, id_inscription: int) -> None:
    """Retire un participant (suppression d'inscription) — action bénévole."""
    conn.execute(
        "DELETE FROM inscriptions WHERE id_inscription = ?", (id_inscription,)
    )
    conn.commit()


def desinscrire(conn: sqlite3.Connection, code: str) -> dict:
    """
    Désinscription publique via le code de désinscription.

    Returns:
        {"ok": True, "pseudo": …, "id_tournoi": …} si une inscription a été
        supprimée ; {"ok": False} si le code est vide ou inconnu.
    """
    code = (code or "").strip()
    if not code:
        return {"ok": False}
    row = conn.execute(
        "SELECT id_inscription, id_tournoi, pseudo FROM inscriptions "
        "WHERE code_desinscription = ?",
        (code,),
    ).fetchone()
    if row is None:
        return {"ok": False}
    conn.execute(
        "DELETE FROM inscriptions WHERE id_inscription = ?", (row["id_inscription"],)
    )
    conn.commit()
    return {"ok": True, "pseudo": row["pseudo"], "id_tournoi": row["id_tournoi"]}


# ===========================================================================
# Lancement + modes de scoring
# ===========================================================================
def lancer_tournoi(conn: sqlite3.Connection, id_tournoi: int,
                   mode_scoring: str) -> dict:
    """
    Lance un tournoi : passe de 'inscriptions' à 'lance' et fixe le mode de
    scoring, puis initialise les structures propres au mode.

    Returns:
        {"ok": True} en cas de succès ; sinon {"ok": False, "raison":
        "introuvable"|"mode_inconnu"|"etat"|"sans_participant"}.
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None:
        return {"ok": False, "raison": "introuvable"}
    if mode_scoring not in MODES_SCORING:
        return {"ok": False, "raison": "mode_inconnu"}
    if "lance" not in TRANSITIONS.get(t["etat"], set()):
        return {"ok": False, "raison": "etat"}
    if compter_inscriptions(conn, id_tournoi) == 0:
        return {"ok": False, "raison": "sans_participant"}

    conn.execute(
        "UPDATE tournois SET etat = 'lance', mode_scoring = ? WHERE id_tournoi = ?",
        (mode_scoring, id_tournoi),
    )
    if mode_scoring == "high_score":
        _init_high_score(conn, id_tournoi)
    conn.commit()
    return {"ok": True}


# --- High score ------------------------------------------------------------
# Représentation : UNE ligne `rencontres` par participant (participant_a = le
# joueur, participant_b NULL, score_a = ses points, ronde NULL). Simple, et
# réutilise la table déjà prévue. Le classement = tri par score_a décroissant.
def _init_high_score(conn: sqlite3.Connection, id_tournoi: int) -> None:
    """Crée une ligne de score (score_a NULL) pour chaque participant sans ligne."""
    manquants = conn.execute(
        """
        SELECT i.id_inscription
        FROM inscriptions i
        WHERE i.id_tournoi = ?
          AND NOT EXISTS (
            SELECT 1 FROM rencontres r
            WHERE r.id_tournoi = i.id_tournoi AND r.participant_a = i.id_inscription
          )
        """,
        (id_tournoi,),
    ).fetchall()
    conn.executemany(
        "INSERT INTO rencontres (id_tournoi, participant_a) VALUES (?, ?)",
        [(id_tournoi, r["id_inscription"]) for r in manquants],
    )


def lignes_high_score(conn: sqlite3.Connection, id_tournoi: int) -> list[dict]:
    """
    Lignes de saisie des scores (un par participant), triées par pseudo.

    Chaque dict : {id_inscription, pseudo, score} (score = None si pas encore saisi).
    """
    # On s'assure d'abord qu'aucun participant ajouté après le lancement n'est
    # oublié (création paresseuse de sa ligne).
    _init_high_score(conn, id_tournoi)
    conn.commit()
    return [
        {"id_inscription": r["id_inscription"], "pseudo": r["pseudo"],
         "score": r["score_a"]}
        for r in conn.execute(
            """
            SELECT i.id_inscription, i.pseudo, r.score_a
            FROM inscriptions i
            LEFT JOIN rencontres r
              ON r.id_tournoi = i.id_tournoi AND r.participant_a = i.id_inscription
            WHERE i.id_tournoi = ?
            ORDER BY i.pseudo COLLATE NOCASE
            """,
            (id_tournoi,),
        )
    ]


def enregistrer_scores_high_score(conn: sqlite3.Connection, id_tournoi: int,
                                  scores: dict[int, int | None]) -> None:
    """
    Enregistre les scores saisis. `scores` mappe id_inscription -> points (ou
    None pour effacer). Crée la ligne si besoin avant la mise à jour.
    """
    _init_high_score(conn, id_tournoi)
    for id_inscription, points in scores.items():
        conn.execute(
            "UPDATE rencontres SET score_a = ? "
            "WHERE id_tournoi = ? AND participant_a = ?",
            (points, id_tournoi, id_inscription),
        )
    conn.commit()


def classement_high_score(conn: sqlite3.Connection, id_tournoi: int) -> list[dict]:
    """
    Classement par points décroissants. Les participants sans score saisi sont
    placés en fin de liste, sans rang.

    Chaque dict : {rang, pseudo, score}. Rang en « ranking sportif » : les ex
    æquo partagent le même rang, le suivant saute d'autant (1, 2, 2, 4…). Rang
    None pour les participants sans score.
    """
    lignes = conn.execute(
        """
        SELECT i.pseudo, r.score_a
        FROM inscriptions i
        LEFT JOIN rencontres r
          ON r.id_tournoi = i.id_tournoi AND r.participant_a = i.id_inscription
        WHERE i.id_tournoi = ?
        ORDER BY (r.score_a IS NULL), r.score_a DESC, i.pseudo COLLATE NOCASE
        """,
        (id_tournoi,),
    ).fetchall()

    classement, position, precedent = [], 0, object()
    for r in lignes:
        score = r["score_a"]
        if score is None:
            classement.append({"rang": None, "pseudo": r["pseudo"], "score": None})
            continue
        position += 1
        rang = position if score != precedent else classement[-1]["rang"]
        precedent = score
        classement.append({"rang": rang, "pseudo": r["pseudo"], "score": score})
    return classement
