"""
Logique métier du module « Programme du week-end » (docs/conception-programme.md).

Vit dans le sous-paquet `tournoi` (même base SQLite `data/tournoi.db`, AUCUNE
FK vers la base de prêt — documenté en tête de `app/tournoi/models.py`) mais
concerne des éléments HORS tournoi : animations, ateliers, initiations, temps
forts, interventions d'associations partenaires.

PÉRIMÈTRE DE CE JALON : schéma (voir models.py/db.py), CRUD des types et des
éléments, machine à états, et la fonction de fusion `imminents` qui consomme
`tournoi.services.tournois_imminents` sans la modifier. AUCUNE route, AUCUN
gabarit : ce module ignore tout de FastAPI, des requêtes HTTP, des jetons et de
l'état des modules (`app.modules`) — cette logique se pose au jalon 2, sur les
routeurs.

RGPD : zéro donnée personnelle. `jauge` est un nombre indicatif (pas une liste
d'inscrits), comme le veut la conception.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, time, timedelta

# Repli du nom de l'association quand la route ne le transmet pas — même
# raison que dans `tournoi/services.py` : le réglage vit dans la base de PRÊT.
from app.config import DOMAINE_UID_ICS, NOM_ASSOCIATION
from app.services import FUSEAU_LOCAL, FUSEAU_UTC, maintenant, transaction
from app.tournoi.creneau import (
    DUREE_DEFAUT_MIN,
    SLOT_MIN,
    _calculer_couloirs,
    _local_naive,
    assembler_jours,
    bornes_bloc,
    label_jour,
)
from app.tournoi.models import ETATS_PROGRAMME
from app.tournoi.services import _ics_echappe, _ics_horodatage, tournois_imminents
from app.tournoi.services import planning as services_planning

# Transitions d'état autorisées (machine à états, conception §4.2). Plus
# permissive que celle des tournois (pas d'inscriptions ni de rencontres à
# protéger) : un brouillon se publie, un élément publié peut redevenir
# brouillon ou être annulé, et un élément annulé peut être réinstauré (brouillon
# ou publié directement) plutôt que de forcer une re-création.
TRANSITIONS_PROGRAMME = {
    "brouillon": {"publie"},
    "publie": {"brouillon", "annule"},
    "annule": {"brouillon", "publie"},
}


def _row(r: sqlite3.Row | None) -> dict | None:
    """Convertit une ligne SQLite en dict (ou None)."""
    return dict(r) if r is not None else None


def _nettoyer_texte(texte: str | None) -> str | None:
    """Normalise les espaces d'un champ texte facultatif ; None si vide."""
    if texte is None:
        return None
    normalise = " ".join(texte.split())
    return normalise or None


# ===========================================================================
# Les deux LISTES administrables du programme : types et publics visés
# (conception §4.1). Patron identique à `app.services` (emplacements de
# rangement) : liste, création, renommage propagé (clé, aucune duplication à
# gérer ailleurs), archivage doux, réordonnancement, suppression refusée si
# utilisée. Une seule implémentation, paramétrée par `_LISTES`, plutôt que
# deux copies qui finiraient par diverger ; les fonctions `*_type` restent
# comme raccourcis pour les appelants existants.
# ===========================================================================
_LISTES = {
    "types": {
        "table": "types_programme", "cle": "id_type", "icone": True,
        # Éléments qui pointent vers l'entrée `t` (sous-requête corrélée).
        "usage": "SELECT COUNT(*) FROM programme p WHERE p.id_type = t.id_type",
    },
    "publics": {
        "table": "publics_programme", "cle": "id_public", "icone": False,
        "usage": "SELECT COUNT(*) FROM programme_publics l WHERE l.id_public = t.id_public",
    },
}


def lister_liste(conn: sqlite3.Connection, liste: str, *, actifs_seulement: bool = True) -> list[dict]:
    """
    Entrées d'une liste (`"types"` ou `"publics"`), triées par ordre puis nom,
    avec `usage_count` (nombre d'éléments rattachés — décide si la
    suppression dure est proposée). `actifs_seulement` exclut les archivées.
    """
    cfg = _LISTES[liste]
    clause = "WHERE t.actif = 1" if actifs_seulement else ""
    rows = conn.execute(
        f"""
        SELECT t.*, ({cfg["usage"]}) AS usage_count
        FROM {cfg["table"]} t
        {clause}
        ORDER BY t.ordre, t.nom COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_entree(conn: sqlite3.Connection, liste: str, id_entree: int) -> dict | None:
    """Une entrée de liste (active ou archivée), ou None."""
    cfg = _LISTES[liste]
    return _row(conn.execute(
        f"SELECT * FROM {cfg['table']} WHERE {cfg['cle']} = ?", (id_entree,)
    ).fetchone())


def creer_entree(conn: sqlite3.Connection, liste: str, nom: str, icone: str | None = None) -> int | None:
    """Ajoute une entrée en fin de liste (ordre = max + 1). None si `nom` est vide."""
    cfg = _LISTES[liste]
    nom_normalise = _nettoyer_texte(nom)
    if not nom_normalise:
        return None
    (max_ordre,) = conn.execute(f"SELECT COALESCE(MAX(ordre), -1) FROM {cfg['table']}").fetchone()
    if cfg["icone"]:
        cur = conn.execute(
            f"INSERT INTO {cfg['table']} (nom, icone, actif, ordre) VALUES (?, ?, 1, ?)",
            (nom_normalise, _nettoyer_texte(icone), max_ordre + 1),
        )
    else:
        cur = conn.execute(
            f"INSERT INTO {cfg['table']} (nom, actif, ordre) VALUES (?, 1, ?)",
            (nom_normalise, max_ordre + 1),
        )
    conn.commit()
    return int(cur.lastrowid)


def renommer_entree(conn: sqlite3.Connection, liste: str, id_entree: int,
                    nom: str, icone: str | None = None) -> bool:
    """
    Renomme une entrée (répercuté partout, les éléments ne portent que la clé)
    et, pour les types, met à jour l'icône. False si `nom` est vide.
    """
    cfg = _LISTES[liste]
    nom_normalise = _nettoyer_texte(nom)
    if not nom_normalise:
        return False
    if cfg["icone"]:
        conn.execute(
            f"UPDATE {cfg['table']} SET nom = ?, icone = ? WHERE {cfg['cle']} = ?",
            (nom_normalise, _nettoyer_texte(icone), id_entree),
        )
    else:
        conn.execute(
            f"UPDATE {cfg['table']} SET nom = ? WHERE {cfg['cle']} = ?", (nom_normalise, id_entree)
        )
    conn.commit()
    return True


def _changer_actif(conn: sqlite3.Connection, liste: str, id_entree: int, actif: int) -> None:
    cfg = _LISTES[liste]
    conn.execute(f"UPDATE {cfg['table']} SET actif = ? WHERE {cfg['cle']} = ?", (actif, id_entree))
    conn.commit()


def archiver_entree(conn: sqlite3.Connection, liste: str, id_entree: int) -> None:
    """Retrait doux : disparaît des formulaires de saisie, les éléments déjà saisis la gardent."""
    _changer_actif(conn, liste, id_entree, 0)


def reactiver_entree(conn: sqlite3.Connection, liste: str, id_entree: int) -> None:
    """Annule un archivage : redevient proposée dans les formulaires de saisie."""
    _changer_actif(conn, liste, id_entree, 1)


def compteur_usage(conn: sqlite3.Connection, liste: str, id_entree: int) -> int:
    """Nombre d'éléments de programme actuellement rattachés à cette entrée."""
    cfg = _LISTES[liste]
    (n,) = conn.execute(
        f"SELECT ({cfg['usage']}) FROM {cfg['table']} t WHERE t.{cfg['cle']} = ?", (id_entree,)
    ).fetchone() or (0,)
    return n


def supprimer_entree(conn: sqlite3.Connection, liste: str, id_entree: int) -> bool:
    """
    Suppression DURE : refusée (False, rien n'est modifié) si au moins un
    élément de programme y est encore rattaché — jamais de lien orphelin.
    """
    cfg = _LISTES[liste]
    if compteur_usage(conn, liste, id_entree) > 0:
        return False
    conn.execute(f"DELETE FROM {cfg['table']} WHERE {cfg['cle']} = ?", (id_entree,))
    conn.commit()
    return True


def reordonner_liste(conn: sqlite3.Connection, liste: str, id_entree: int, sens: str) -> None:
    """
    Échange la position d'une entrée avec sa voisine immédiate dans la liste
    triée (`sens` = "haut" ou "bas"). Sans effet en bout de liste ou si
    l'entrée est inconnue. Travaille sur la POSITION dans la liste triée (pas
    la valeur brute d'`ordre`), comme `deplacer_emplacement_rangement`.
    """
    cfg = _LISTES[liste]
    table, cle = cfg["table"], cfg["cle"]
    lignes = conn.execute(
        f"SELECT {cle} AS id, ordre FROM {table} ORDER BY ordre, nom COLLATE NOCASE"
    ).fetchall()
    ids = [r["id"] for r in lignes]
    if id_entree not in ids:
        return
    idx = ids.index(id_entree)
    voisin = idx - 1 if sens == "haut" else idx + 1
    if voisin < 0 or voisin >= len(ids):
        return
    a, b = lignes[idx], lignes[voisin]
    conn.execute(f"UPDATE {table} SET ordre = ? WHERE {cle} = ?", (b["ordre"], a["id"]))
    conn.execute(f"UPDATE {table} SET ordre = ? WHERE {cle} = ?", (a["ordre"], b["id"]))
    conn.commit()


# Raccourcis historiques pour les types (appelants et tests existants).
def lister_types(conn: sqlite3.Connection, *, actifs_seulement: bool = True) -> list[dict]:
    return lister_liste(conn, "types", actifs_seulement=actifs_seulement)


def get_type(conn: sqlite3.Connection, id_type: int) -> dict | None:
    return get_entree(conn, "types", id_type)


def creer_type(conn: sqlite3.Connection, nom: str, icone: str | None = None) -> int | None:
    return creer_entree(conn, "types", nom, icone)


def renommer_type(conn: sqlite3.Connection, id_type: int, nom: str, icone: str | None = None) -> bool:
    return renommer_entree(conn, "types", id_type, nom, icone)


def archiver_type(conn: sqlite3.Connection, id_type: int) -> None:
    archiver_entree(conn, "types", id_type)


def reactiver_type(conn: sqlite3.Connection, id_type: int) -> None:
    reactiver_entree(conn, "types", id_type)


def compteur_usage_type(conn: sqlite3.Connection, id_type: int) -> int:
    return compteur_usage(conn, "types", id_type)


def supprimer_type(conn: sqlite3.Connection, id_type: int) -> bool:
    return supprimer_entree(conn, "types", id_type)


def reordonner_types(conn: sqlite3.Connection, id_type: int, sens: str) -> None:
    reordonner_liste(conn, "types", id_type, sens)


def lister_publics(conn: sqlite3.Connection, *, actifs_seulement: bool = True) -> list[dict]:
    return lister_liste(conn, "publics", actifs_seulement=actifs_seulement)


# ===========================================================================
# Publics d'un élément (liaison plusieurs-à-plusieurs `programme_publics`)
# ===========================================================================
def publics_par_element(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    """
    Publics de chaque élément, dans l'ordre de la liste : {id_element: [{id_public,
    nom, actif}, …]}. Une seule requête pour toute une page (grille, gestion).
    """
    resultat: dict[int, list[dict]] = {}
    for r in conn.execute(
        """
        SELECT l.id_element, pu.id_public, pu.nom, pu.actif
        FROM programme_publics l
        JOIN publics_programme pu ON pu.id_public = l.id_public
        ORDER BY pu.ordre, pu.nom COLLATE NOCASE
        """
    ):
        resultat.setdefault(r["id_element"], []).append(
            {"id_public": r["id_public"], "nom": r["nom"], "actif": r["actif"]}
        )
    return resultat


def publics_element(conn: sqlite3.Connection, id_element: int) -> list[dict]:
    """Publics d'un seul élément, dans l'ordre de la liste."""
    return publics_par_element(conn).get(id_element, [])


def libelle_publics(publics: list[dict]) -> str | None:
    """« Enfants 8 ans et plus, Familles » — None si aucun public n'est coché."""
    return ", ".join(p["nom"] for p in publics) or None


def definir_publics(conn: sqlite3.Connection, id_element: int, ids_publics) -> None:
    """
    Remplace les publics d'un élément par `ids_publics` (identifiants inconnus
    ignorés). Un public archivé déjà coché reste coché tant qu'on ne le décoche
    pas : l'archivage ne retire jamais rien d'un élément déjà saisi.
    """
    valides = {
        r[0] for r in conn.execute("SELECT id_public FROM publics_programme")
    }
    with transaction(conn):
        conn.execute("DELETE FROM programme_publics WHERE id_element = ?", (id_element,))
        conn.executemany(
            "INSERT OR IGNORE INTO programme_publics (id_element, id_public) VALUES (?, ?)",
            [(id_element, i) for i in dict.fromkeys(ids_publics or []) if i in valides],
        )


# ===========================================================================
# Jeux liés à un élément (`programme_jeux`) — références seulement : ce module
# ne lit JAMAIS la base de prêt. Les noms, l'existence du titre et la recherche
# sont l'affaire de la route.
# ===========================================================================
def jeux_element(conn: sqlite3.Connection, id_element: int) -> list[str]:
    """Références des jeux liés à un élément, dans l'ordre où ils ont été liés."""
    return [r[0] for r in conn.execute(
        "SELECT reference_titre FROM programme_jeux WHERE id_element = ? ORDER BY rowid",
        (id_element,),
    )]


def jeux_par_element(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """{id_element: [reference_titre, …]} pour toute la liste de gestion, en une requête."""
    resultat: dict[int, list[str]] = {}
    for id_element, ref in conn.execute(
        "SELECT id_element, reference_titre FROM programme_jeux ORDER BY rowid"
    ):
        resultat.setdefault(id_element, []).append(ref)
    return resultat


def lier_jeu(conn: sqlite3.Connection, id_element: int, reference_titre: str) -> bool:
    """
    Lie un jeu à un élément. False (rien n'est fait) si l'élément n'existe pas,
    si la référence est vide ou si le jeu est déjà lié. La route a vérifié que
    le titre existe dans la base de prêt.
    """
    ref = (reference_titre or "").strip()
    if not ref or get_element(conn, id_element) is None:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO programme_jeux (id_element, reference_titre) VALUES (?, ?)",
        (id_element, ref),
    )
    conn.commit()
    return cur.rowcount == 1


def delier_jeu(conn: sqlite3.Connection, id_element: int, reference_titre: str) -> bool:
    """Retire un jeu d'un élément. False si ce lien n'existait pas."""
    cur = conn.execute(
        "DELETE FROM programme_jeux WHERE id_element = ? AND reference_titre = ?",
        (id_element, reference_titre),
    )
    conn.commit()
    return cur.rowcount == 1


# ===========================================================================
# Éléments de programme (CRUD, machine à états — conception §4.2/§5)
# ===========================================================================
def creer_element(
    conn: sqlite3.Connection,
    intitule: str,
    *,
    description: str | None = None,
    id_type: int | None = None,
    date_heure: str | None = None,
    duree_min: int | None = None,
    lieu: str | None = None,
    publics: list[int] | None = None,
    jauge: int | None = None,
) -> int:
    """
    Crée un élément de programme à l'état 'brouillon' et renvoie son id.

    `date_heure` est attendu en UTC ISO (la route convertit la saisie locale,
    jalon 2). `id_type` est nullable (aucun type choisi). `publics` : les
    identifiants des publics cochés (voir `definir_publics`).
    """
    cur = conn.execute(
        """
        INSERT INTO programme
            (intitule, description, id_type, date_heure, duree_min, lieu,
             jauge, etat, date_creation)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'brouillon', ?)
        """,
        (intitule.strip(), _nettoyer_texte(description), id_type, date_heure,
         duree_min, _nettoyer_texte(lieu), jauge, maintenant()),
    )
    conn.commit()
    id_element = int(cur.lastrowid)
    if publics:
        definir_publics(conn, id_element, publics)
    return id_element


def get_element(conn: sqlite3.Connection, id_element: int) -> dict | None:
    """Retourne l'élément de programme (dict) ou None s'il n'existe pas."""
    return _row(
        conn.execute(
            "SELECT * FROM programme WHERE id_element = ?", (id_element,)
        ).fetchone()
    )


def modifier_element(conn: sqlite3.Connection, id_element: int, **champs) -> None:
    """
    Met à jour les champs fournis d'un élément de programme (édition bénévole).
    Seules les colonnes connues sont prises en compte ; les autres sont ignorées.
    """
    colonnes = {
        "intitule", "description", "id_type", "date_heure", "duree_min",
        "lieu", "jauge",
    }
    maj = {c: v for c, v in champs.items() if c in colonnes}
    if not maj:
        return
    assignations = ", ".join(f"{c} = ?" for c in maj)
    conn.execute(
        f"UPDATE programme SET {assignations} WHERE id_element = ?",
        (*maj.values(), id_element),
    )
    conn.commit()


def supprimer_element(conn: sqlite3.Connection, id_element: int) -> None:
    """Supprime un élément de programme. Action irréversible (double confirmation côté route)."""
    conn.execute("DELETE FROM programme WHERE id_element = ?", (id_element,))
    conn.commit()


def changer_etat(conn: sqlite3.Connection, id_element: int, nouvel_etat: str) -> bool:
    """
    Change l'état d'un élément si la transition est autorisée
    (`TRANSITIONS_PROGRAMME`).

    Returns:
        True si la transition a eu lieu, False sinon (état inconnu, élément
        absent ou transition non permise).
    """
    if nouvel_etat not in ETATS_PROGRAMME:
        return False
    e = get_element(conn, id_element)
    if e is None:
        return False
    if nouvel_etat not in TRANSITIONS_PROGRAMME.get(e["etat"], set()):
        return False
    conn.execute(
        "UPDATE programme SET etat = ? WHERE id_element = ?", (nouvel_etat, id_element)
    )
    conn.commit()
    return True


def lister_elements(
    conn: sqlite3.Connection,
    *,
    jour: date | None = None,
    id_type: int | None = None,
    lieu: str | None = None,
    etat: str | None = None,
    id_public: int | None = None,
    inclure_brouillons: bool = False,
) -> list[dict]:
    """
    Liste les éléments de programme (heure de début croissante ; ceux sans
    date en dernier), filtrable par jour (heure locale), par type, par lieu
    (comparé sans tenir compte de la casse ni des espaces de bord, comme le
    regroupe `lieux_distincts`), par état et par public. Chaque élément porte
    la liste de ses publics (`publics`).

    `inclure_brouillons` : True pour la vue bénévole (jalon 2), False pour le
    public (brouillons masqués). Les éléments ANNULÉS restent dans la liste —
    l'écran doit pouvoir les afficher barrés ; seule `imminents` les exclut par
    défaut.
    """
    lignes = conn.execute(
        "SELECT * FROM programme ORDER BY (date_heure IS NULL), date_heure, intitule COLLATE NOCASE"
    ).fetchall()
    publics = publics_par_element(conn)
    resultat = []
    for r in lignes:
        d = dict(r)
        d["publics"] = publics.get(d["id_element"], [])
        if id_public is not None and all(p["id_public"] != id_public for p in d["publics"]):
            continue
        if not inclure_brouillons and d["etat"] == "brouillon":
            continue
        if id_type is not None and d["id_type"] != id_type:
            continue
        if etat is not None and d["etat"] != etat:
            continue
        if lieu is not None and _cle_lieu(d["lieu"]) != _cle_lieu(lieu):
            continue
        if jour is not None:
            if not d["date_heure"]:
                continue
            try:
                if _local_naive(d["date_heure"]).date() != jour:
                    continue
            except (ValueError, TypeError):
                continue
        resultat.append(d)
    return resultat


def _cle_lieu(lieu: str | None) -> str:
    """Clé de regroupement d'un lieu : texte libre, donc casse et bords ignorés."""
    return (lieu or "").strip().casefold()


def lieux_distincts(conn: sqlite3.Connection) -> list[str]:
    """
    Lieux déjà saisis, sans doublon, triés — options du filtre de la liste de
    gestion. Le lieu est un texte libre : « Salle du fond » et « salle du
    fond » forment une seule option (la première graphie rencontrée), et le
    filtre `lister_elements(lieu=…)` les retrouve toutes les deux.
    """
    vus: dict[str, str] = {}
    for (valeur,) in conn.execute(
        "SELECT lieu FROM programme WHERE lieu IS NOT NULL ORDER BY id_element"
    ):
        cle = _cle_lieu(valeur)
        if cle and cle not in vus:
            vus[cle] = valeur.strip()
    return sorted(vus.values(), key=str.casefold)


# Changement d'état EN LOT (« Tout publier » / « Tout dépublier » de la liste
# de gestion) : état visé -> SEUL état de départ concerné. Plus étroit que
# `TRANSITIONS_PROGRAMME` à dessein : un élément ANNULÉ peut être republié un
# par un, jamais par un bouton qui balaie toute une liste — republier par
# mégarde un atelier annulé serait le pire résultat possible.
SOURCES_EN_LOT = {"publie": "brouillon", "brouillon": "publie"}


def changer_etat_en_lot(
    conn: sqlite3.Connection, ids: list[int], nouvel_etat: str
) -> list[dict]:
    """
    Fait passer à `nouvel_etat` ceux des éléments `ids` qui sont dans l'état
    de départ prévu par `SOURCES_EN_LOT` ; les autres (annulés, déjà dans
    l'état visé, disparus entre-temps) sont ignorés sans erreur.

    TOUT OU RIEN : une seule transaction (`services.transaction`, verrou pris
    avant la lecture). Une coupure ne laisse jamais la moitié du programme
    publiée.

    Returns:
        Les éléments effectivement changés (tels qu'avant le changement),
        pour que la route journalise une ligne par élément. Liste vide si
        `nouvel_etat` n'est pas un état groupable.
    """
    source = SOURCES_EN_LOT.get(nouvel_etat)
    if source is None or not ids:
        return []
    changes = []
    with transaction(conn):
        for id_element in dict.fromkeys(ids):
            r = conn.execute(
                "SELECT * FROM programme WHERE id_element = ? AND etat = ?",
                (id_element, source),
            ).fetchone()
            if r is None:
                continue
            conn.execute(
                "UPDATE programme SET etat = ? WHERE id_element = ?",
                (nouvel_etat, id_element),
            )
            changes.append(dict(r))
    return changes


def dupliquer_element(
    conn: sqlite3.Connection, id_element: int, date_heure: str | None = None
) -> int | None:
    """
    Crée une COPIE d'un élément de programme pour le reprogrammer à un autre
    horaire (un atelier se rejoue souvent le dimanche à la même heure — patron
    `tournoi.services.dupliquer_tournoi`). Repart à l'état 'brouillon'. Seule
    la date change ; publics et jeux liés suivent. None si la source est introuvable.
    """
    e = get_element(conn, id_element)
    if e is None:
        return None
    nouveau = creer_element(
        conn, e["intitule"],
        description=e["description"], id_type=e["id_type"], date_heure=date_heure,
        duree_min=e["duree_min"], lieu=e["lieu"],
        publics=[p["id_public"] for p in publics_element(conn, id_element)],
        jauge=e["jauge"],
    )
    for ref in jeux_element(conn, id_element):
        lier_jeu(conn, nouveau, ref)
    return nouveau


def duree_depuis_fin(debut_iso: str | None, fin_iso: str | None) -> int | None:
    """
    Durée en minutes déduite d'un début et d'une fin (UTC ISO) — même logique
    que `planning.services.modifier_creneau` (déduction d'une durée à partir de
    deux bornes saisies). None si une borne manque, si le format est invalide,
    ou si la fin n'est pas strictement après le début : jamais bloquant, à la
    route (jalon 2) de refuser proprement plutôt que de planter.
    """
    if not debut_iso or not fin_iso:
        return None
    try:
        debut = datetime.fromisoformat(debut_iso)
        fin = datetime.fromisoformat(fin_iso)
    except (ValueError, TypeError):
        return None
    if fin <= debut:
        return None
    return int((fin - debut).total_seconds() // 60)


def fin_iso(date_heure_iso: str | None, duree_min: int | None) -> str | None:
    """
    Horodatage de fin (UTC ISO) déduit d'un début et d'une durée en minutes —
    l'inverse de `duree_depuis_fin`. Sert (jalon 2, route) à préremplir le champ
    « heure de fin » du formulaire d'édition à partir des valeurs stockées. None
    si la date de début ou la durée manquent : jamais de durée inventée pour
    l'affichage.
    """
    if not date_heure_iso or not duree_min:
        return None
    try:
        debut = datetime.fromisoformat(date_heure_iso)
    except (ValueError, TypeError):
        return None
    return (debut + timedelta(minutes=duree_min)).isoformat(timespec="seconds")


# ===========================================================================
# Export iCalendar (.ics) — patron `tournoi.services.ical_tournoi`
# ===========================================================================
def ical_element(conn: sqlite3.Connection, id_element: int,
                 nom_evenement: str | None = None,
                 nom_association: str | None = None) -> str | None:
    """
    Contenu iCalendar (.ics) d'un élément de programme pour « Ajouter à mon
    agenda ». Aucune donnée personnelle. None si introuvable ou sans date.

    `nom_evenement` en paramètre, pour la même raison que dans `ical_tournoi`
    (voir sa docstring) : le réglage vit dans la base de PRÊT, ce module ne
    connaît que celle des TOURNOIS, et c'est la route qui fait le lien. Absent,
    la description est exactement ce qu'elle était.

    `nom_association` arrive lui aussi en paramètre, et pour la MÊME raison :
    il se règle depuis /admin/identite et vit dans la base de PRÊT. Absent, on
    retombe sur le repli de `app/config.py` — un `.ics` reste ainsi valide même
    appelé hors requête.
    """
    e = get_element(conn, id_element)
    if e is None or not e["date_heure"]:
        return None
    try:
        debut = datetime.fromisoformat(e["date_heure"])
    except (ValueError, TypeError):
        return None
    fin = debut + timedelta(minutes=e["duree_min"] or DUREE_DEFAUT_MIN)

    description = []
    if e["description"]:
        description.append(e["description"])
    if nom_evenement:
        description.append(nom_evenement)
    asso = nom_association or NOM_ASSOCIATION
    description.append(f"Programme — {asso}")

    lignes = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{asso}//Programme//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:programme-{id_element}-{_ics_horodatage(debut)}@{DOMAINE_UID_ICS}",
        f"DTSTAMP:{_ics_horodatage(datetime.now(FUSEAU_UTC))}",
        f"DTSTART:{_ics_horodatage(debut)}",
        f"DTEND:{_ics_horodatage(fin)}",
        f"SUMMARY:{_ics_echappe(e['intitule'])}",
        f"DESCRIPTION:{_ics_echappe(' — '.join(description))}",
    ]
    if e["lieu"]:
        lignes.append(f"LOCATION:{_ics_echappe(e['lieu'])}")
    lignes += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lignes) + "\r\n"


# ===========================================================================
# Fusion — le cœur du module (conception §5)
# ===========================================================================
def _heure_locale(date_heure_utc: str | None) -> str:
    """Horodatage UTC ISO -> 'HH:MM' en heure locale (chaîne vide si invalide)."""
    if not date_heure_utc:
        return ""
    try:
        dt = datetime.fromisoformat(date_heure_utc)
    except (ValueError, TypeError):
        return ""
    return dt.astimezone(FUSEAU_LOCAL).strftime("%H:%M")


def _minutes_avant(date_heure_utc: str | None, reference: datetime) -> int | None:
    """
    Minutes avant le début (arrondi supérieur, jamais négatif), calculées par
    rapport à `reference` (et non `datetime.now()` à chaque appel : toutes les
    entrées d'une même fusion `imminents` partagent le même instant de
    référence). None si la date est absente/invalide.
    """
    if not date_heure_utc:
        return None
    try:
        dt = datetime.fromisoformat(date_heure_utc)
    except (ValueError, TypeError):
        return None
    minutes = -(-int((dt - reference).total_seconds()) // 60)  # arrondi supérieur
    return max(0, minutes)


def imminents(
    conn: sqlite3.Connection, minutes: int, *, inclure_annules: bool = False
) -> list[dict]:
    """
    Fusionne tournois ET éléments de programme dont le début tombe entre
    maintenant et maintenant + `minutes`, triés par heure de début croissante
    — LA seule implémentation, consommée par les trois surfaces (accueil,
    écran de salle, page /programme) avec des fenêtres différentes (60 min /
    120 min / grille complète). `tournois_imminents` reste utilisée telle
    quelle à l'intérieur : aucune régression sur l'existant.

    Chaque entrée est un dict avec :
        source          "tournoi" ou "programme"
        id              id_tournoi ou id_element
        intitule        nom du tournoi ou intitulé de l'élément
        icone           icône du type (programme) ou None (tournoi : pas de
                         champ icône — c'est au gabarit de choisir un repli)
        lieu            emplacement/lieu (peut être None)
        etat            état brut ('lance'/'annule'/...) ; absent pour les
                         tournois (champ propre au programme, pour le barré
                         « annulé » de l'écran de salle)
        date_heure      UTC ISO brut (utile pour un lien .ics)
        heure_locale    'HH:MM'
        minutes_avant   entier >= 0

    Les BROUILLONS de programme sont TOUJOURS exclus (jamais publics, comme
    les tournois). Les éléments ANNULÉS sont exclus par défaut
    (`inclure_annules=False`), inclus sur demande (écran de salle, qui doit
    pouvoir les afficher barrés pendant leur fenêtre).
    """
    maintenant_dt = datetime.now(FUSEAU_UTC)
    limite = maintenant_dt + timedelta(minutes=minutes)

    fusion: list[tuple[datetime, str, dict]] = []

    for t in tournois_imminents(conn, minutes):
        try:
            dt = datetime.fromisoformat(t["date_heure"])
        except (ValueError, TypeError):
            continue
        entree = {
            "source": "tournoi",
            "id": t["id_tournoi"],
            "intitule": t["nom"],
            "icone": None,
            "lieu": t["emplacement"],
            "date_heure": t["date_heure"],
            "heure_locale": _heure_locale(t["date_heure"]),
            "minutes_avant": _minutes_avant(t["date_heure"], maintenant_dt),
            # Propres au tournoi (l'accueil affiche le remplissage) ; un
            # élément de programme n'a pas d'inscription, il porte au mieux
            # une jauge indicative.
            "jeu": t["jeu"],
            "nb_inscrits": t["nb_inscrits"],
            "nb_places": t["nb_places"],
            "places_restantes": t["places_restantes"],
        }
        fusion.append((dt, t["nom"], entree))

    lignes = conn.execute(
        """
        SELECT p.*, tp.icone AS type_icone
        FROM programme p
        LEFT JOIN types_programme tp ON tp.id_type = p.id_type
        WHERE p.etat != 'brouillon' AND p.date_heure IS NOT NULL
        """
    ).fetchall()
    publics = publics_par_element(conn) if lignes else {}
    for r in lignes:
        d = dict(r)
        if d["etat"] == "annule" and not inclure_annules:
            continue
        try:
            dt = datetime.fromisoformat(d["date_heure"])
        except (ValueError, TypeError):
            continue
        if not (maintenant_dt <= dt <= limite):
            continue
        entree = {
            "source": "programme",
            "id": d["id_element"],
            "intitule": d["intitule"],
            "icone": d["type_icone"],
            "lieu": d["lieu"],
            "etat": d["etat"],
            "date_heure": d["date_heure"],
            "heure_locale": _heure_locale(d["date_heure"]),
            "minutes_avant": _minutes_avant(d["date_heure"], maintenant_dt),
            "jauge": d["jauge"],
            "publics": libelle_publics(publics.get(d["id_element"], [])),
        }
        fusion.append((dt, d["intitule"], entree))

    fusion.sort(key=lambda x: (x[0], x[1]))
    return [entree for _, _, entree in fusion]


# ===========================================================================
# Grille horaire publique (jalon 2, §6.1) — patron `tournoi.services.planning`,
# mêmes mécaniques de couloirs/slots (creneau.py) donc le MÊME rendu CSS
# (grille sur grand écran, agenda empilé sous 640 px). Seuls les éléments
# PUBLIÉS apparaissent : ni les brouillons (jamais publics), ni les annulés
# (leur affichage barré est un besoin propre à l'écran de salle, hors périmètre
# de la page /programme).
# ===========================================================================
def grille(
    conn: sqlite3.Connection, jours: list[date], *, id_type: int | None = None,
    id_public: int | None = None,
) -> list[dict]:
    """
    Construit la grille horaire des éléments de programme PUBLIÉS pour les
    `jours` donnés (heure locale), filtrable par type et par public. Renvoie une liste de
    dicts par jour (même ordre que `jours`), même structure que
    `tournoi.services.planning` (label, blocs, vide, nb_couloirs, nb_slots,
    heures) : le gabarit `/programme` réutilise le même rendu que la frise de
    l'accueil. Les éléments sans durée occupent DUREE_DEFAUT_MIN.
    """
    clauses, params = "", []
    if id_type is not None:
        clauses += " AND p.id_type = ?"
        params.append(id_type)
    if id_public is not None:
        clauses += (" AND EXISTS (SELECT 1 FROM programme_publics l"
                    " WHERE l.id_element = p.id_element AND l.id_public = ?)")
        params.append(id_public)
    lignes = conn.execute(
        f"""
        SELECT p.*, tp.nom AS type_nom, tp.icone AS type_icone
        FROM programme p
        LEFT JOIN types_programme tp ON tp.id_type = p.id_type
        WHERE p.etat = 'publie' AND p.date_heure IS NOT NULL{clauses}
        ORDER BY p.date_heure ASC
        """,
        params,
    ).fetchall()

    publics = publics_par_element(conn) if lignes else {}
    par_jour: dict[date, list] = {j: [] for j in jours}
    for r in lignes:
        bloc = _bloc_programme(r, publics.get(r["id_element"], []))
        if bloc and bloc["debut_dt"].date() in par_jour:
            par_jour[bloc["debut_dt"].date()].append(bloc)

    return assembler_jours(par_jour, jours, cle_tri="intitule")


def _bloc_programme(r, publics: list[dict] | None = None) -> dict | None:
    """
    Un élément de programme sous forme de bloc de frise (ou None si sa date
    est absente/invalide). Partagé par `grille` et par la frise fusionnée de
    l'accueil, pour que les deux affichent exactement la même chose.
    """
    bornes = bornes_bloc(r["date_heure"], r["duree_min"])
    if bornes is None:
        return None
    debut, fin = bornes
    return {
        "source": "programme",
        "id_element": r["id_element"], "intitule": r["intitule"],
        # `nom` double `intitule` : la frise fusionnée trie les deux sources
        # sur la même clé.
        "nom": r["intitule"],
        "lieu": r["lieu"], "type_nom": r["type_nom"], "type_icone": r["type_icone"],
        "publics": libelle_publics(publics or []), "jauge": r["jauge"],
        "debut_dt": debut, "fin_dt": fin,
        "heure_txt": f'{debut.strftime("%H:%M")}–{fin.strftime("%H:%M")}',
    }


def planning_fusionne(
    conn: sqlite3.Connection,
    jours: list[date],
    *,
    avec_tournois: bool = True,
    avec_programme: bool = True,
) -> list[dict]:
    """
    Frise deux jours de la page d'accueil, tournois ET animations mélangés :
    les couloirs sont calculés SUR L'ENSEMBLE des blocs, sinon deux créneaux
    simultanés de sources différentes se superposeraient à l'écran.

    Chaque bloc porte `source` ("tournoi" / "programme") : le gabarit sait
    ainsi lequel est cliquable (un tournoi a une page, un élément de programme
    n'en a pas) et quelle couleur lui donner.

    Les deux sources sont activables séparément : l'accueil passe ici l'état
    de visibilité de chaque module (fiche A3 — ne jamais afficher le contenu
    d'un module masqué).
    """
    par_jour: dict[date, list] = {j: [] for j in jours}

    if avec_tournois:
        for jour in services_planning(conn, jours):
            for bloc in jour["blocs"]:
                bloc["source"] = "tournoi"
                par_jour[jour["date"]].append(bloc)

    if avec_programme:
        lignes = conn.execute(
            """
            SELECT p.*, tp.nom AS type_nom, tp.icone AS type_icone
            FROM programme p
            LEFT JOIN types_programme tp ON tp.id_type = p.id_type
            WHERE p.etat = 'publie' AND p.date_heure IS NOT NULL
            ORDER BY p.date_heure ASC
            """
        ).fetchall()
        publics = publics_par_element(conn) if lignes else {}
        for r in lignes:
            bloc = _bloc_programme(r, publics.get(r["id_element"], []))
            if bloc and bloc["debut_dt"].date() in par_jour:
                par_jour[bloc["debut_dt"].date()].append(bloc)

    return assembler_jours(par_jour, jours, cle_tri="nom")
