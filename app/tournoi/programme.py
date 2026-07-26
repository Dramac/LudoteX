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

from app.config import NOM_ASSOCIATION
from app.services import FUSEAU_LOCAL, FUSEAU_UTC, maintenant
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
# Types de programme (liste configurable, conception §4.1)
# Patron identique à `app.services` (emplacements de rangement) : liste,
# création, renommage propagé (FK, aucune duplication à gérer ailleurs),
# archivage doux, réordonnancement, suppression refusée si utilisé.
# ===========================================================================
def lister_types(conn: sqlite3.Connection, *, actifs_seulement: bool = True) -> list[dict]:
    """
    Liste des types de programme, triée par ordre d'affichage puis nom, avec
    le nombre d'éléments qui y sont rattachés (`usage_count` — sert à décider
    si la suppression dure est proposée). `actifs_seulement` (défaut True)
    exclut les types archivés ; False renvoie la liste complète (écran admin).
    """
    clause = "WHERE tp.actif = 1" if actifs_seulement else ""
    rows = conn.execute(
        f"""
        SELECT tp.id_type, tp.nom, tp.icone, tp.actif, tp.ordre,
               COUNT(p.id_element) AS usage_count
        FROM types_programme tp
        LEFT JOIN programme p ON p.id_type = tp.id_type
        {clause}
        GROUP BY tp.id_type
        ORDER BY tp.ordre, tp.nom COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_type(conn: sqlite3.Connection, id_type: int) -> dict | None:
    """Un type de programme (actif ou archivé), ou None."""
    return _row(
        conn.execute(
            "SELECT * FROM types_programme WHERE id_type = ?", (id_type,)
        ).fetchone()
    )


def creer_type(conn: sqlite3.Connection, nom: str, icone: str | None = None) -> int | None:
    """Ajoute un type en fin de liste (ordre = max + 1). None si `nom` est vide."""
    nom_normalise = _nettoyer_texte(nom)
    if not nom_normalise:
        return None
    (max_ordre,) = conn.execute(
        "SELECT COALESCE(MAX(ordre), -1) FROM types_programme"
    ).fetchone()
    cur = conn.execute(
        "INSERT INTO types_programme (nom, icone, actif, ordre) VALUES (?, ?, 1, ?)",
        (nom_normalise, _nettoyer_texte(icone), max_ordre + 1),
    )
    conn.commit()
    return int(cur.lastrowid)


def renommer_type(conn: sqlite3.Connection, id_type: int, nom: str, icone: str | None = None) -> bool:
    """
    Renomme un type (répercuté automatiquement partout via la FK) et met à
    jour son icône. False si `nom` est vide (rien n'est modifié).
    """
    nom_normalise = _nettoyer_texte(nom)
    if not nom_normalise:
        return False
    conn.execute(
        "UPDATE types_programme SET nom = ?, icone = ? WHERE id_type = ?",
        (nom_normalise, _nettoyer_texte(icone), id_type),
    )
    conn.commit()
    return True


def archiver_type(conn: sqlite3.Connection, id_type: int) -> None:
    """Retrait doux : disparaît des menus de saisie, les éléments déjà saisis gardent leur type."""
    conn.execute("UPDATE types_programme SET actif = 0 WHERE id_type = ?", (id_type,))
    conn.commit()


def reactiver_type(conn: sqlite3.Connection, id_type: int) -> None:
    """Annule un archivage : redevient proposé dans les menus de saisie."""
    conn.execute("UPDATE types_programme SET actif = 1 WHERE id_type = ?", (id_type,))
    conn.commit()


def compteur_usage_type(conn: sqlite3.Connection, id_type: int) -> int:
    """Nombre d'éléments de programme qui pointent actuellement vers ce type."""
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM programme WHERE id_type = ?", (id_type,)
    ).fetchone()
    return n


def supprimer_type(conn: sqlite3.Connection, id_type: int) -> bool:
    """
    Suppression DURE : refusée (False, rien n'est modifié) si au moins un
    élément de programme y est encore rattaché — jamais de FK orpheline.
    """
    if compteur_usage_type(conn, id_type) > 0:
        return False
    conn.execute("DELETE FROM types_programme WHERE id_type = ?", (id_type,))
    conn.commit()
    return True


def reordonner_types(conn: sqlite3.Connection, id_type: int, sens: str) -> None:
    """
    Échange la position d'un type avec son voisin immédiat dans la liste triée
    (`sens` = "haut" ou "bas"). Sans effet en bout de liste ou si `id_type` est
    inconnu. Travaille sur la POSITION dans la liste triée (pas la valeur brute
    d'`ordre`), comme `deplacer_emplacement_rangement`.
    """
    lignes = conn.execute(
        "SELECT id_type, ordre FROM types_programme ORDER BY ordre, nom COLLATE NOCASE"
    ).fetchall()
    ids = [r["id_type"] for r in lignes]
    if id_type not in ids:
        return
    idx = ids.index(id_type)
    voisin = idx - 1 if sens == "haut" else idx + 1
    if voisin < 0 or voisin >= len(ids):
        return
    a, b = lignes[idx], lignes[voisin]
    conn.execute("UPDATE types_programme SET ordre = ? WHERE id_type = ?", (b["ordre"], a["id_type"]))
    conn.execute("UPDATE types_programme SET ordre = ? WHERE id_type = ?", (a["ordre"], b["id_type"]))
    conn.commit()


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
    public_vise: str | None = None,
    jauge: int | None = None,
) -> int:
    """
    Crée un élément de programme à l'état 'brouillon' et renvoie son id.

    `date_heure` est attendu en UTC ISO (la route convertit la saisie locale,
    jalon 2). `id_type` est nullable (aucun type choisi).
    """
    cur = conn.execute(
        """
        INSERT INTO programme
            (intitule, description, id_type, date_heure, duree_min, lieu,
             public_vise, jauge, etat, date_creation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'brouillon', ?)
        """,
        (intitule.strip(), _nettoyer_texte(description), id_type, date_heure,
         duree_min, _nettoyer_texte(lieu), _nettoyer_texte(public_vise), jauge,
         maintenant()),
    )
    conn.commit()
    return int(cur.lastrowid)


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
        "lieu", "public_vise", "jauge",
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
    inclure_brouillons: bool = False,
) -> list[dict]:
    """
    Liste les éléments de programme (heure de début croissante ; ceux sans
    date en dernier), filtrable par jour (heure locale) et/ou par type.

    `inclure_brouillons` : True pour la vue bénévole (jalon 2), False pour le
    public (brouillons masqués). Les éléments ANNULÉS restent dans la liste —
    l'écran doit pouvoir les afficher barrés ; seule `imminents` les exclut par
    défaut.
    """
    lignes = conn.execute(
        "SELECT * FROM programme ORDER BY (date_heure IS NULL), date_heure, intitule COLLATE NOCASE"
    ).fetchall()
    resultat = []
    for r in lignes:
        d = dict(r)
        if not inclure_brouillons and d["etat"] == "brouillon":
            continue
        if id_type is not None and d["id_type"] != id_type:
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


def dupliquer_element(
    conn: sqlite3.Connection, id_element: int, date_heure: str | None = None
) -> int | None:
    """
    Crée une COPIE d'un élément de programme pour le reprogrammer à un autre
    horaire (un atelier se rejoue souvent le dimanche à la même heure — patron
    `tournoi.services.dupliquer_tournoi`). Repart à l'état 'brouillon'. Seule
    la date change. None si la source est introuvable.
    """
    e = get_element(conn, id_element)
    if e is None:
        return None
    return creer_element(
        conn, e["intitule"],
        description=e["description"], id_type=e["id_type"], date_heure=date_heure,
        duree_min=e["duree_min"], lieu=e["lieu"], public_vise=e["public_vise"],
        jauge=e["jauge"],
    )


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
def ical_element(conn: sqlite3.Connection, id_element: int) -> str | None:
    """
    Contenu iCalendar (.ics) d'un élément de programme pour « Ajouter à mon
    agenda ». Aucune donnée personnelle. None si introuvable ou sans date.
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
    description.append(f"Programme — {NOM_ASSOCIATION}")

    lignes = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{NOM_ASSOCIATION}//Programme//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:programme-{id_element}-{_ics_horodatage(debut)}@desjeuxpleinlamanche",
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
            "public_vise": d["public_vise"],
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
    conn: sqlite3.Connection, jours: list[date], *, id_type: int | None = None
) -> list[dict]:
    """
    Construit la grille horaire des éléments de programme PUBLIÉS pour les
    `jours` donnés (heure locale), filtrable par type. Renvoie une liste de
    dicts par jour (même ordre que `jours`), même structure que
    `tournoi.services.planning` (label, blocs, vide, nb_couloirs, nb_slots,
    heures) : le gabarit `/programme` réutilise le même rendu que la frise de
    l'accueil. Les éléments sans durée occupent DUREE_DEFAUT_MIN.
    """
    clause_type = "AND p.id_type = ?" if id_type is not None else ""
    params: tuple = (id_type,) if id_type is not None else ()
    lignes = conn.execute(
        f"""
        SELECT p.*, tp.nom AS type_nom, tp.icone AS type_icone
        FROM programme p
        LEFT JOIN types_programme tp ON tp.id_type = p.id_type
        WHERE p.etat = 'publie' AND p.date_heure IS NOT NULL {clause_type}
        ORDER BY p.date_heure ASC
        """,
        params,
    ).fetchall()

    par_jour: dict[date, list] = {j: [] for j in jours}
    for r in lignes:
        bloc = _bloc_programme(r)
        if bloc and bloc["debut_dt"].date() in par_jour:
            par_jour[bloc["debut_dt"].date()].append(bloc)

    return assembler_jours(par_jour, jours, cle_tri="intitule")


def _bloc_programme(r) -> dict | None:
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
        "public_vise": r["public_vise"], "jauge": r["jauge"],
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
        for r in lignes:
            bloc = _bloc_programme(r)
            if bloc and bloc["debut_dt"].date() in par_jour:
                par_jour[bloc["debut_dt"].date()].append(bloc)

    return assembler_jours(par_jour, jours, cle_tri="nom")
