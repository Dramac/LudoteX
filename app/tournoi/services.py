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

import math
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta

from app.config import NOM_ASSOCIATION
from app.services import FUSEAU_LOCAL, FUSEAU_UTC, maintenant  # fuseau + horodatage UTC ISO partagés
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

# Modes de scoring disponibles (conception §6). Implémentés un par un. Les
# libellés servent à l'affichage et aux menus.
MODES_SCORING = {
    "high_score": "High score (points cumulés)",
    "ronde_suisse": "Ronde suisse",
    "elimination": "Élimination directe",
}

# Points attribués par résultat en ronde suisse (barème type échecs/jeux).
POINTS_VICTOIRE = 1.0
POINTS_NUL = 0.5
POINTS_DEFAITE = 0.0
POINTS_BYE = 1.0   # un bye (exempt) vaut une victoire


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
    age: str | None = None,
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

    `age` est une indication libre (ex. « 10+ », « tout public »).
    `date_heure` est attendu en UTC ISO (la route convertit la saisie locale).
    """
    cur = conn.execute(
        """
        INSERT INTO tournois
            (nom, jeu, age, date_heure, duree_min, nb_places, emplacement,
             inscription_en_ligne, etat, bo3, restriction_nombre, date_creation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'brouillon', ?, ?, ?)
        """,
        (nom.strip(), (jeu or "").strip() or None, (age or "").strip() or None,
         date_heure, duree_min, nb_places, (emplacement or "").strip() or None,
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


def dupliquer_tournoi(conn: sqlite3.Connection, id_tournoi: int,
                      date_heure: str | None = None) -> int | None:
    """
    Crée une COPIE d'un tournoi pour la programmer à un autre horaire.

    Recopie les caractéristiques (nom, jeu, durée, places, emplacement,
    inscription en ligne) et repart « propre » : état brouillon, AUCUN inscrit,
    mode de scoring / BO3 / nombre de rondes non définis (ils se choisissent au
    lancement de chaque créneau). Seule la date change.

    Args:
        date_heure: nouvel horaire (UTC ISO) ; None autorisé (à régler ensuite).

    Returns:
        L'id du nouveau tournoi, ou None si la source est introuvable.
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None:
        return None
    return creer_tournoi(
        conn, t["nom"],
        jeu=t["jeu"],
        age=t["age"],
        date_heure=date_heure,
        duree_min=t["duree_min"],
        nb_places=t["nb_places"],
        emplacement=t["emplacement"],
        inscription_en_ligne=bool(t["inscription_en_ligne"]),
    )


def modifier_tournoi(conn: sqlite3.Connection, id_tournoi: int, **champs) -> None:
    """
    Met à jour les champs fournis d'un tournoi (édition bénévole).

    Seules les colonnes connues sont prises en compte ; les autres sont ignorées.
    """
    colonnes = {
        "nom", "jeu", "age", "date_heure", "duree_min", "nb_places", "emplacement",
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


def ouvrir_tournois_du_jour(conn: sqlite3.Connection, jour: date) -> int:
    """
    Ouvre les inscriptions de tous les tournois EN BROUILLON dont la date (heure
    locale) tombe le `jour` donné. Gain de temps le jour de l'événement.

    Ne touche pas aux tournois déjà ouverts/lancés/terminés ni à ceux d'un autre
    jour ou sans date. Renvoie le nombre de tournois ouverts.
    """
    ids = []
    for r in conn.execute(
        "SELECT id_tournoi, date_heure FROM tournois "
        "WHERE etat = 'brouillon' AND date_heure IS NOT NULL"
    ):
        try:
            if _local_naive(r["date_heure"]).date() == jour:
                ids.append(r["id_tournoi"])
        except (ValueError, TypeError):
            continue
    for tid in ids:
        conn.execute(
            "UPDATE tournois SET etat = 'inscriptions' WHERE id_tournoi = ?", (tid,)
        )
    conn.commit()
    return len(ids)


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


def tournois_imminents(
    conn: sqlite3.Connection, fenetre_minutes: int = 60
) -> list[dict]:
    """
    Tournois publics dont le début est PROCHE : compris entre maintenant et
    maintenant + `fenetre_minutes` (1 h par défaut). Sert à la page d'accueil.

    Les brouillons sont exclus (jamais publics) ainsi que les tournois sans date
    ou déjà commencés/passés. Triés par heure de début croissante.
    """
    maintenant_dt = datetime.now(FUSEAU_UTC)
    limite = maintenant_dt + timedelta(minutes=fenetre_minutes)
    lignes = conn.execute(
        """
        SELECT t.*, (
            SELECT COUNT(*) FROM inscriptions i WHERE i.id_tournoi = t.id_tournoi
        ) AS nb_inscrits
        FROM tournois t
        WHERE t.etat != 'brouillon' AND t.date_heure IS NOT NULL
        ORDER BY t.date_heure ASC
        """
    ).fetchall()
    imminents = []
    for r in lignes:
        d = dict(r)
        try:
            dt = datetime.fromisoformat(d["date_heure"])
        except (ValueError, TypeError):
            continue
        if maintenant_dt <= dt <= limite:
            d["phase"] = phase(d["etat"])
            d["places_restantes"] = (
                None if d["nb_places"] is None
                else max(0, d["nb_places"] - d["nb_inscrits"])
            )
            imminents.append(d)
    return imminents


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
                   mode_scoring: str, nb_rondes: int | None = None,
                   bo3: bool = False) -> dict:
    """
    Lance un tournoi : passe de 'inscriptions' à 'lance' et fixe le mode de
    scoring (et l'option BO3), puis initialise les structures propres au mode.

    Args:
        nb_rondes: requis pour 'ronde_suisse' (>= 1) ; ignoré pour 'high_score'.
        bo3: best of 3 — la saisie se fait alors en MANCHES gagnées (vainqueur
            déduit). N'a de sens que pour les modes à base de matchs (ronde
            suisse, élimination) ; ignoré en high score. Choisi AU LANCEMENT.

    Returns:
        {"ok": True} en cas de succès ; sinon {"ok": False, "raison":
        "introuvable"|"mode_inconnu"|"etat"|"sans_participant"|"pas_assez"|"nb_rondes"}.
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None:
        return {"ok": False, "raison": "introuvable"}
    if mode_scoring not in MODES_SCORING:
        return {"ok": False, "raison": "mode_inconnu"}
    if "lance" not in TRANSITIONS.get(t["etat"], set()):
        return {"ok": False, "raison": "etat"}
    nb_inscrits = compter_inscriptions(conn, id_tournoi)
    if nb_inscrits == 0:
        return {"ok": False, "raison": "sans_participant"}

    if mode_scoring == "ronde_suisse":
        if nb_inscrits < 2:
            return {"ok": False, "raison": "pas_assez"}
        if not nb_rondes or nb_rondes < 1:
            return {"ok": False, "raison": "nb_rondes"}
    elif mode_scoring == "elimination":
        if nb_inscrits < 2:
            return {"ok": False, "raison": "pas_assez"}

    # nb_rondes stocke : pour le suisse, le nombre choisi ; pour l'élimination,
    # le nombre de tours de l'arbre (déduit de l'effectif).
    if mode_scoring == "ronde_suisse":
        nb_rondes_stocke = nb_rondes
    elif mode_scoring == "elimination":
        nb_rondes_stocke = _nb_tours_elimination(nb_inscrits)
    else:
        nb_rondes_stocke = None

    # Le BO3 n'a de sens que pour les modes à base de matchs.
    bo3_stocke = 1 if (bo3 and mode_scoring in ("ronde_suisse", "elimination")) else 0

    conn.execute(
        "UPDATE tournois SET etat = 'lance', mode_scoring = ?, nb_rondes = ?, bo3 = ? "
        "WHERE id_tournoi = ?",
        (mode_scoring, nb_rondes_stocke, bo3_stocke, id_tournoi),
    )
    if mode_scoring == "high_score":
        _init_high_score(conn, id_tournoi)
    elif mode_scoring == "ronde_suisse":
        _generer_ronde_suisse(conn, id_tournoi, 1)
    elif mode_scoring == "elimination":
        _generer_premier_tour_elimination(conn, id_tournoi)
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


# --- Ronde suisse ----------------------------------------------------------
# Représentation : une ligne `rencontres` par match, avec `ronde` = n° de ronde,
# `participant_a`/`participant_b` (B NULL = bye), `resultat` ∈ {'a','b','nul'}.
# Le classement agrège les points (victoire/nul/bye) sur toutes les rondes.
RESULTATS = {"a", "b", "nul"}


def format_points(points: float) -> str:
    """Affiche un total de points sans .0 superflu : 3.0 -> « 3 », 2.5 -> « 2,5 »."""
    if points == int(points):
        return str(int(points))
    return f"{points:.1f}".replace(".", ",")


def _participants(conn: sqlite3.Connection, id_tournoi: int) -> dict[int, str]:
    """{id_inscription: pseudo} des participants du tournoi."""
    return {
        r["id_inscription"]: r["pseudo"]
        for r in conn.execute(
            "SELECT id_inscription, pseudo FROM inscriptions WHERE id_tournoi = ? "
            "ORDER BY id_inscription",
            (id_tournoi,),
        )
    }


def points_suisse(conn: sqlite3.Connection, id_tournoi: int) -> dict[int, float]:
    """Points cumulés par participant (victoire 1, nul 0,5, bye 1)."""
    pts: dict[int, float] = {pid: 0.0 for pid in _participants(conn, id_tournoi)}
    for r in conn.execute(
        "SELECT participant_a, participant_b, resultat FROM rencontres WHERE id_tournoi = ?",
        (id_tournoi,),
    ):
        a, b, res = r["participant_a"], r["participant_b"], r["resultat"]
        if b is None:                      # bye : A marque comme une victoire
            if a in pts:
                pts[a] += POINTS_BYE
            continue
        if res == "a":
            if a in pts: pts[a] += POINTS_VICTOIRE
        elif res == "b":
            if b in pts: pts[b] += POINTS_VICTOIRE
        elif res == "nul":
            if a in pts: pts[a] += POINTS_NUL
            if b in pts: pts[b] += POINTS_NUL
    return pts


def _adversaires_passes(conn: sqlite3.Connection, id_tournoi: int) -> dict[int, set]:
    """{id_inscription: {adversaires déjà rencontrés}} (byes exclus)."""
    adv: dict[int, set] = {pid: set() for pid in _participants(conn, id_tournoi)}
    for r in conn.execute(
        "SELECT participant_a, participant_b FROM rencontres "
        "WHERE id_tournoi = ? AND participant_b IS NOT NULL",
        (id_tournoi,),
    ):
        a, b = r["participant_a"], r["participant_b"]
        if a in adv and b is not None:
            adv[a].add(b)
        if b in adv and a is not None:
            adv[b].add(a)
    return adv


def _ont_eu_un_bye(conn: sqlite3.Connection, id_tournoi: int) -> set:
    """Ensemble des participants ayant déjà bénéficié d'un bye."""
    return {
        r["participant_a"]
        for r in conn.execute(
            "SELECT participant_a FROM rencontres "
            "WHERE id_tournoi = ? AND participant_b IS NULL",
            (id_tournoi,),
        )
        if r["participant_a"] is not None
    }


def ronde_courante(conn: sqlite3.Connection, id_tournoi: int) -> int:
    """Numéro de la dernière ronde générée (0 si aucune)."""
    row = conn.execute(
        "SELECT MAX(ronde) FROM rencontres WHERE id_tournoi = ?", (id_tournoi,)
    ).fetchone()
    return row[0] or 0


def ronde_complete(conn: sqlite3.Connection, id_tournoi: int, ronde: int) -> bool:
    """True si toutes les rencontres (hors byes) de la ronde ont un résultat saisi."""
    n = conn.execute(
        "SELECT COUNT(*) FROM rencontres "
        "WHERE id_tournoi = ? AND ronde = ? AND participant_b IS NOT NULL "
        "AND resultat IS NULL",
        (id_tournoi, ronde),
    ).fetchone()[0]
    return n == 0


def _apparier(ordre: list[int], adversaires: dict[int, set]) -> list[tuple[int, int]]:
    """
    Apparie une liste ORDONNÉE (par classement) en évitant les revanches.

    Algorithme glouton : pour chaque joueur encore libre, on cherche le suivant
    libre qu'il n'a pas déjà affronté ; à défaut (tous déjà joués), on accepte
    une revanche avec le suivant libre (repli). Suppose un effectif pair.
    """
    paires, utilises = [], set()
    for i, p in enumerate(ordre):
        if p in utilises:
            continue
        partenaire = None
        for q in ordre[i + 1:]:
            if q not in utilises and q not in adversaires.get(p, set()):
                partenaire = q
                break
        if partenaire is None:  # repli : revanche autorisée faute de mieux
            for q in ordre[i + 1:]:
                if q not in utilises:
                    partenaire = q
                    break
        if partenaire is not None:
            utilises.add(p)
            utilises.add(partenaire)
            paires.append((p, partenaire))
    return paires


def _generer_ronde_suisse(conn: sqlite3.Connection, id_tournoi: int, ronde: int) -> int:
    """
    Génère les rencontres d'une ronde : tri par points décroissants, bye au
    joueur le moins bien classé n'en ayant pas encore eu, puis appariement.
    Renvoie le nombre de rencontres créées (byes inclus).
    """
    participants = _participants(conn, id_tournoi)
    pts = points_suisse(conn, id_tournoi)
    # Ordre : points décroissants, puis id croissant (déterministe ; en ronde 1
    # tous les points valent 0 -> ordre d'inscription).
    ordre = sorted(participants, key=lambda pid: (-pts.get(pid, 0.0), pid))

    bye_player = None
    if len(ordre) % 2 == 1:
        deja_bye = _ont_eu_un_bye(conn, id_tournoi)
        # Le moins bien classé sans bye antérieur (sinon, le tout dernier).
        for pid in reversed(ordre):
            if pid not in deja_bye:
                bye_player = pid
                break
        if bye_player is None:
            bye_player = ordre[-1]
        ordre = [pid for pid in ordre if pid != bye_player]

    paires = _apparier(ordre, _adversaires_passes(conn, id_tournoi))

    cree = 0
    for a, b in paires:
        conn.execute(
            "INSERT INTO rencontres (id_tournoi, ronde, participant_a, participant_b) "
            "VALUES (?, ?, ?, ?)",
            (id_tournoi, ronde, a, b),
        )
        cree += 1
    if bye_player is not None:
        # Bye = victoire automatique : resultat 'a', participant_b NULL.
        conn.execute(
            "INSERT INTO rencontres (id_tournoi, ronde, participant_a, participant_b, resultat) "
            "VALUES (?, ?, ?, NULL, 'a')",
            (id_tournoi, ronde, bye_player),
        )
        cree += 1
    return cree


def rencontres_de_ronde(conn: sqlite3.Connection, id_tournoi: int,
                        ronde: int) -> list[dict]:
    """Rencontres d'une ronde, avec pseudos résolus (B None = bye)."""
    noms = _participants(conn, id_tournoi)
    lignes = []
    for r in conn.execute(
        "SELECT * FROM rencontres WHERE id_tournoi = ? AND ronde = ? "
        "ORDER BY id_rencontre",
        (id_tournoi, ronde),
    ):
        lignes.append({
            "id_rencontre": r["id_rencontre"],
            "participant_a": r["participant_a"],
            "participant_b": r["participant_b"],
            "pseudo_a": noms.get(r["participant_a"], "?"),
            "pseudo_b": noms.get(r["participant_b"]) if r["participant_b"] else None,
            "resultat": r["resultat"],
            "score_a": r["score_a"],
            "score_b": r["score_b"],
            "bye": r["participant_b"] is None,
        })
    return lignes


def _resultat_depuis_manches(a: int | None, b: int | None,
                             autoriser_nul: bool) -> str | None:
    """
    Déduit le résultat ('a'/'b'/'nul'/None) d'un nombre de manches gagnées.

    Égalité : 'nul' si autorisé (ronde suisse), sinon None — pas de vainqueur
    désigné (élimination : la rencontre reste « à jouer »).
    """
    if a is None or b is None:
        return None
    if a > b:
        return "a"
    if b > a:
        return "b"
    return "nul" if autoriser_nul else None


def enregistrer_manches(conn: sqlite3.Connection, id_tournoi: int, ronde: int,
                        manches: dict[int, tuple[int | None, int | None]],
                        autoriser_nul: bool) -> None:
    """
    Enregistre les manches gagnées (mode BO3) d'une ronde/tour et en déduit le
    résultat. `manches` mappe id_rencontre -> (manches_a, manches_b). Les byes ne
    sont pas modifiables.
    """
    for id_rencontre, (a, b) in manches.items():
        resultat = _resultat_depuis_manches(a, b, autoriser_nul)
        conn.execute(
            "UPDATE rencontres SET score_a = ?, score_b = ?, resultat = ? "
            "WHERE id_rencontre = ? AND id_tournoi = ? AND ronde = ? "
            "AND participant_b IS NOT NULL",
            (a, b, resultat, id_rencontre, id_tournoi, ronde),
        )
    conn.commit()


def toutes_les_rondes(conn: sqlite3.Connection, id_tournoi: int) -> list[dict]:
    """Liste [{ronde, rencontres, complete}] pour toutes les rondes générées."""
    return [
        {"ronde": n,
         "rencontres": rencontres_de_ronde(conn, id_tournoi, n),
         "complete": ronde_complete(conn, id_tournoi, n)}
        for n in range(1, ronde_courante(conn, id_tournoi) + 1)
    ]


def enregistrer_resultats_suisse(conn: sqlite3.Connection, id_tournoi: int,
                                 ronde: int, resultats: dict[int, str | None]) -> None:
    """
    Enregistre les résultats d'une ronde. `resultats` mappe id_rencontre ->
    'a'/'b'/'nul' (ou None pour effacer). Les byes ne sont pas modifiables.
    """
    for id_rencontre, res in resultats.items():
        valeur = res if res in RESULTATS else None
        conn.execute(
            "UPDATE rencontres SET resultat = ? "
            "WHERE id_rencontre = ? AND id_tournoi = ? AND ronde = ? "
            "AND participant_b IS NOT NULL",
            (valeur, id_rencontre, id_tournoi, ronde),
        )
    conn.commit()


def generer_ronde_suivante(conn: sqlite3.Connection, id_tournoi: int) -> dict:
    """
    Génère la ronde suivante si la ronde courante est complète et que le nombre
    de rondes prévu n'est pas atteint.

    Returns:
        {"ok": True, "ronde": n} ; sinon {"ok": False, "raison":
        "mode"|"incomplete"|"terminee"}.
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None or t["mode_scoring"] != "ronde_suisse":
        return {"ok": False, "raison": "mode"}
    courante = ronde_courante(conn, id_tournoi)
    if courante and not ronde_complete(conn, id_tournoi, courante):
        return {"ok": False, "raison": "incomplete"}
    if t["nb_rondes"] and courante >= t["nb_rondes"]:
        return {"ok": False, "raison": "terminee"}
    suivante = courante + 1
    _generer_ronde_suisse(conn, id_tournoi, suivante)
    conn.commit()
    return {"ok": True, "ronde": suivante}


def classement_suisse(conn: sqlite3.Connection, id_tournoi: int) -> list[dict]:
    """
    Classement par points décroissants (ranking sportif pour les ex æquo).

    Chaque dict : {rang, pseudo, points, points_txt}.
    """
    noms = _participants(conn, id_tournoi)
    pts = points_suisse(conn, id_tournoi)
    ordonne = sorted(noms, key=lambda pid: (-pts.get(pid, 0.0),
                                            noms[pid].lower()))
    classement, position, precedent = [], 0, None
    for pid in ordonne:
        p = pts.get(pid, 0.0)
        position += 1
        rang = position if p != precedent else classement[-1]["rang"]
        precedent = p
        classement.append({
            "rang": rang, "pseudo": noms[pid],
            "points": p, "points_txt": format_points(p),
        })
    return classement


# --- Élimination directe ---------------------------------------------------
# Arbre à élimination simple. Représentation dans `rencontres` : `ronde` = n° de
# tour (1 = premier tour … T = finale), `participant_a`/`participant_b` les deux
# joueurs (B NULL = bye, resultat 'a' automatique). Le vainqueur de chaque
# rencontre (resultat 'a'/'b') accède au tour suivant.
def _puissance_de_deux_sup(n: int) -> int:
    """Plus petite puissance de 2 >= n (n >= 1)."""
    b = 1
    while b < n:
        b *= 2
    return b


def _nb_tours_elimination(nb_inscrits: int) -> int:
    """Nombre de tours de l'arbre (ex. 5–8 joueurs -> 3 tours)."""
    tours, taille = 0, _puissance_de_deux_sup(max(nb_inscrits, 1))
    while taille > 1:
        taille //= 2
        tours += 1
    return tours


def _ordre_places(taille: int) -> list[int]:
    """
    Ordre des têtes de série (« seeds ») dans un arbre de `taille` (puissance de
    2). Renvoie la liste des numéros de seed dans l'ordre des positions, de sorte
    que les seeds opposés (1 contre le dernier, etc.) soient bien répartis et ne
    se rencontrent que le plus tard possible.
    """
    places = [1, 2]
    while len(places) < taille:
        n = len(places) * 2 + 1
        nouveau = []
        for p in places:
            nouveau.append(p)
            nouveau.append(n - p)
        places = nouveau
    return places


def _generer_premier_tour_elimination(conn: sqlite3.Connection, id_tournoi: int) -> None:
    """
    Crée les rencontres du premier tour à partir des participants (têtes de série
    = ordre d'inscription). Les byes (si l'effectif n'est pas une puissance de 2)
    vont aux mieux classés et sont répartis dans l'arbre.
    """
    joueurs = list(_participants(conn, id_tournoi))  # ids, ordre d'inscription
    n = len(joueurs)
    taille = _puissance_de_deux_sup(n)
    ordre = _ordre_places(taille)

    def joueur(seed: int):
        return joueurs[seed - 1] if seed <= n else None

    for i in range(0, taille, 2):
        a, b = joueur(ordre[i]), joueur(ordre[i + 1])
        present = a if a is not None else b
        autre = b if a is not None else a
        if present is not None and autre is not None:
            conn.execute(
                "INSERT INTO rencontres (id_tournoi, ronde, participant_a, participant_b) "
                "VALUES (?, 1, ?, ?)",
                (id_tournoi, present, autre),
            )
        elif present is not None:           # bye : victoire automatique
            conn.execute(
                "INSERT INTO rencontres (id_tournoi, ronde, participant_a, participant_b, resultat) "
                "VALUES (?, 1, ?, NULL, 'a')",
                (id_tournoi, present),
            )


def _gagnants_du_tour(conn: sqlite3.Connection, id_tournoi: int, tour: int) -> list[int]:
    """Vainqueurs d'un tour, dans l'ordre des rencontres (None si non joué)."""
    gagnants = []
    for r in conn.execute(
        "SELECT participant_a, participant_b, resultat FROM rencontres "
        "WHERE id_tournoi = ? AND ronde = ? ORDER BY id_rencontre",
        (id_tournoi, tour),
    ):
        if r["participant_b"] is None:
            gagnants.append(r["participant_a"])
        elif r["resultat"] == "a":
            gagnants.append(r["participant_a"])
        elif r["resultat"] == "b":
            gagnants.append(r["participant_b"])
        else:
            gagnants.append(None)
    return gagnants


def generer_tour_suivant(conn: sqlite3.Connection, id_tournoi: int) -> dict:
    """
    Génère le tour suivant de l'arbre en appariant les vainqueurs du tour courant.

    Returns:
        {"ok": True, "ronde": n} ; sinon {"ok": False, "raison":
        "mode"|"incomplete"|"terminee"}.
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None or t["mode_scoring"] != "elimination":
        return {"ok": False, "raison": "mode"}
    courant = ronde_courante(conn, id_tournoi)
    if courant and not ronde_complete(conn, id_tournoi, courant):
        return {"ok": False, "raison": "incomplete"}
    if t["nb_rondes"] and courant >= t["nb_rondes"]:
        return {"ok": False, "raison": "terminee"}

    gagnants = _gagnants_du_tour(conn, id_tournoi, courant)
    suivant = courant + 1
    for i in range(0, len(gagnants) - 1, 2):
        a, b = gagnants[i], gagnants[i + 1]
        conn.execute(
            "INSERT INTO rencontres (id_tournoi, ronde, participant_a, participant_b) "
            "VALUES (?, ?, ?, ?)",
            (id_tournoi, suivant, a, b),
        )
    conn.commit()
    return {"ok": True, "ronde": suivant}


def nom_tour(tour: int, total: int) -> str:
    """Nom lisible d'un tour selon sa distance à la finale."""
    reste = total - tour
    return {0: "Finale", 1: "Demi-finales", 2: "Quarts de finale",
            3: "Huitièmes de finale"}.get(reste, f"Tour {tour}")


def arbre(conn: sqlite3.Connection, id_tournoi: int) -> list[dict]:
    """Liste [{tour, nom, rencontres, complete}] des tours générés."""
    total = get_tournoi(conn, id_tournoi)["nb_rondes"] or 0
    return [
        {"tour": n, "nom": nom_tour(n, total),
         "rencontres": rencontres_de_ronde(conn, id_tournoi, n),
         "complete": ronde_complete(conn, id_tournoi, n)}
        for n in range(1, ronde_courante(conn, id_tournoi) + 1)
    ]


def vainqueur(conn: sqlite3.Connection, id_tournoi: int) -> str | None:
    """Pseudo du vainqueur si la finale est jouée, sinon None."""
    t = get_tournoi(conn, id_tournoi)
    if t is None or t["mode_scoring"] != "elimination" or not t["nb_rondes"]:
        return None
    total = t["nb_rondes"]
    if ronde_courante(conn, id_tournoi) < total or not ronde_complete(conn, id_tournoi, total):
        return None
    gagnants = _gagnants_du_tour(conn, id_tournoi, total)
    if len(gagnants) == 1 and gagnants[0] is not None:
        return _participants(conn, id_tournoi).get(gagnants[0])
    return None


# ===========================================================================
# Planning de l'événement (vue 2 jours, tournois en parallèle)
# ===========================================================================
# Sert la frise de la page d'accueil. Granularité d'une « ligne » = SLOT_MIN ;
# un tournoi sans durée renseignée occupe DUREE_DEFAUT_MIN.
SLOT_MIN = 30
DUREE_DEFAUT_MIN = 60

_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def label_jour(j: date) -> str:
    """Libellé lisible d'une date, ex. « samedi 13 juin »."""
    return f"{_JOURS_FR[j.weekday()]} {j.day} {_MOIS_FR[j.month]}"


def _local_naive(iso_utc: str) -> datetime:
    """Horodatage UTC ISO -> datetime local NAÏF (sans tz, pour l'arithmétique de grille)."""
    return datetime.fromisoformat(iso_utc).astimezone(FUSEAU_LOCAL).replace(tzinfo=None)


def _calculer_couloirs(blocs: list[dict]) -> int:
    """
    Affecte un couloir (colonne) à chaque bloc pour gérer les chevauchements, par
    partition d'intervalles : chaque bloc prend le premier couloir libre (dont le
    dernier bloc est terminé). `blocs` doit être trié par début. Modifie chaque
    bloc (clé 'couloir') et renvoie le nombre de couloirs utilisés.
    """
    fins_couloirs: list[datetime] = []
    for b in blocs:
        place = False
        for i, fin in enumerate(fins_couloirs):
            if b["debut_dt"] >= fin:        # ce couloir s'est libéré
                b["couloir"] = i
                fins_couloirs[i] = b["fin_dt"]
                place = True
                break
        if not place:
            b["couloir"] = len(fins_couloirs)
            fins_couloirs.append(b["fin_dt"])
    return len(fins_couloirs)


def planning(conn: sqlite3.Connection, jours: list[date]) -> list[dict]:
    """
    Construit le planning des `jours` donnés (heure locale).

    Pour chaque jour : la liste des tournois (non-brouillon, avec date) triés par
    début, répartis en couloirs pour les chevauchements, et toutes les
    coordonnées de grille (lignes/colonnes en pas de SLOT_MIN) + les étiquettes
    d'heures. Les tournois sans durée occupent DUREE_DEFAUT_MIN.

    Renvoie une liste de dicts par jour (même ordre que `jours`).
    """
    lignes = conn.execute(
        """
        SELECT t.*, (
            SELECT COUNT(*) FROM inscriptions i WHERE i.id_tournoi = t.id_tournoi
        ) AS nb_inscrits
        FROM tournois t
        WHERE t.etat != 'brouillon' AND t.date_heure IS NOT NULL
        ORDER BY t.date_heure ASC
        """
    ).fetchall()

    par_jour: dict[date, list] = {j: [] for j in jours}
    for r in lignes:
        try:
            debut = _local_naive(r["date_heure"])
        except (ValueError, TypeError):
            continue
        j = debut.date()
        if j not in par_jour:
            continue
        duree = r["duree_min"] or DUREE_DEFAUT_MIN
        fin = debut + timedelta(minutes=duree)
        minuit_suivant = datetime.combine(j, time()) + timedelta(days=1)
        if fin > minuit_suivant:             # on ne déborde pas sur le lendemain
            fin = minuit_suivant
        nb_places = r["nb_places"]
        par_jour[j].append({
            "id_tournoi": r["id_tournoi"], "nom": r["nom"], "jeu": r["jeu"],
            "emplacement": r["emplacement"], "etat": r["etat"],
            "nb_inscrits": r["nb_inscrits"], "nb_places": nb_places,
            "places_restantes": (None if nb_places is None
                                 else max(0, nb_places - r["nb_inscrits"])),
            "debut_dt": debut, "fin_dt": fin,
            "heure_txt": f'{debut.strftime("%H:%M")}–{fin.strftime("%H:%M")}',
        })

    resultat = []
    for j in jours:
        blocs = sorted(par_jour[j], key=lambda b: (b["debut_dt"], b["nom"]))
        jour = {"date": j, "label": label_jour(j), "blocs": blocs, "vide": not blocs}
        if blocs:
            jour["nb_couloirs"] = _calculer_couloirs(blocs)
            h0 = min(b["debut_dt"] for b in blocs).replace(minute=0, second=0, microsecond=0)
            fin_max = max(b["fin_dt"] for b in blocs)
            if fin_max.minute or fin_max.second:      # arrondi à l'heure supérieure
                fin_max = fin_max.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            jour["nb_slots"] = int((fin_max - h0).total_seconds() // 60 // SLOT_MIN)
            for b in blocs:
                debut_min = (b["debut_dt"] - h0).total_seconds() / 60
                duree_min = (b["fin_dt"] - b["debut_dt"]).total_seconds() / 60
                b["row_debut"] = int(debut_min // SLOT_MIN) + 1
                b["row_span"] = max(1, math.ceil(duree_min / SLOT_MIN))
                b["col"] = b["couloir"] + 2            # colonne 1 = gouttière des heures
            heures, h = [], h0
            while h < fin_max:
                heures.append({"row": int((h - h0).total_seconds() // 60 // SLOT_MIN) + 1,
                               "label": h.strftime("%Hh")})
                h += timedelta(hours=1)
            jour["heures"] = heures
        resultat.append(jour)
    return resultat


# ===========================================================================
# Export iCalendar (.ics) — « Ajouter à mon agenda »
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


def ical_tournoi(conn: sqlite3.Connection, id_tournoi: int) -> str | None:
    """
    Construit le contenu iCalendar (.ics) d'un tournoi pour « Ajouter à mon
    agenda ». Aucune donnée personnelle. Renvoie None si le tournoi est
    introuvable ou n'a pas de date (pas d'événement à planifier).
    """
    t = get_tournoi(conn, id_tournoi)
    if t is None or not t["date_heure"]:
        return None
    try:
        debut = datetime.fromisoformat(t["date_heure"])
    except (ValueError, TypeError):
        return None
    fin = debut + timedelta(minutes=t["duree_min"] or DUREE_DEFAUT_MIN)

    description = []
    if t["jeu"]:
        description.append(f"Jeu : {t['jeu']}")
    description.append(f"Tournoi — {NOM_ASSOCIATION}")

    lignes = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{NOM_ASSOCIATION}//Tournois//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:tournoi-{id_tournoi}-{_ics_horodatage(debut)}@desjeuxpleinlamanche",
        f"DTSTAMP:{_ics_horodatage(datetime.now(FUSEAU_UTC))}",
        f"DTSTART:{_ics_horodatage(debut)}",
        f"DTEND:{_ics_horodatage(fin)}",
        f"SUMMARY:{_ics_echappe(t['nom'])}",
        f"DESCRIPTION:{_ics_echappe(' — '.join(description))}",
    ]
    if t["emplacement"]:
        lignes.append(f"LOCATION:{_ics_echappe(t['emplacement'])}")
    lignes += ["END:VEVENT", "END:VCALENDAR"]
    # iCalendar impose des fins de ligne CRLF.
    return "\r\n".join(lignes) + "\r\n"
