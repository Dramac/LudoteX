"""
Logique métier du prêt — état des exemplaires, numéros de pochette, prêt/retour,
catalogue et statistiques.

POURQUOI CE MODULE EXISTE
-------------------------
On isole ici TOUTE la logique métier, séparée des routes HTTP (app/routes/*).
Avantages : ces fonctions sont testables sans serveur (voir tests/test_services.py)
et réutilisables (un futur module « prêts longue durée » pourra s'appuyer dessus).
Les routes se contentent d'appeler ces fonctions et de rendre des pages.

CONVENTIONS (valables dans tout le projet)
------------------------------------------
- Le code, les noms de variables, de fonctions et de colonnes sont en FRANÇAIS.
- `conn` : une connexion SQLite déjà ouverte (`sqlite3.Connection`). Chaque
  fonction la reçoit en paramètre plutôt que de l'ouvrir elle-même → testabilité
  et maîtrise de la transaction par l'appelant.
- Les fonctions de LECTURE ne committent pas ; les fonctions d'ÉCRITURE
  (`preter`, `rendre`, `repreter`) committent elles-mêmes.
- Les écritures qui touchent aux POCHETTES s'entourent de `transaction(conn)`
  (voir sa docstring) : le numéro doit être lu et réservé d'un seul bloc, sans
  quoi deux bénévoles simultanés repartent avec la même pochette.
- Une « ligne » SQLite est un `sqlite3.Row` (accès par nom de colonne) ; on la
  convertit en `dict` avant de la renvoyer, pour découpler l'appelant de sqlite3.
- Vocabulaire métier :
    * exemplaire  = une boîte physique unique (clé `id_exemplaire`, TEXT).
    * titre       = un jeu, regroupant ses exemplaires (clé `reference_titre`).
    * pochette    = emplacement numéroté où l'on dépose la pièce d'identité.
    * prêt        = une ligne de la table `prets` (sortie + éventuel retour).

RÈGLES MÉTIER NON NÉGOCIABLES (voir docs/specification.md §3, §5, §6)
--------------------------------------------------------------------
- L'état d'un exemplaire est DÉDUIT, jamais stocké : il est SORTI s'il existe un
  prêt avec `date_retour IS NULL`, DISPONIBLE sinon.
- Numéro de pochette : à partir de 1, on attribue toujours le PLUS PETIT numéro
  libre, recyclé au retour, et SANS PLAFOND (on ne refuse jamais un prêt).
- L'historique des prêts n'est jamais purgé : il alimente les statistiques.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Fuseau de l'événement (saisies « 20h », « 2h du matin » = heure locale FR).
# Les horodatages sont STOCKÉS en UTC ; on convertit aux frontières (filtre,
# affichage). Comme tout est en ISO 8601 UTC à offset fixe « +00:00 », la
# comparaison de bornes peut se faire par simple comparaison de chaînes.
FUSEAU_LOCAL = ZoneInfo("Europe/Paris")
FUSEAU_UTC = ZoneInfo("UTC")


def local_vers_utc_iso(saisie: str | None) -> str | None:
    """
    Convertit une saisie locale `datetime-local` ('AAAA-MM-JJTHH:MM') en chaîne
    ISO 8601 UTC ('...+00:00'), pour filtrer la colonne date_sortie.

    Returns:
        La chaîne UTC, ou None si la saisie est vide/invalide.
    """
    if not saisie:
        return None
    try:
        dt = datetime.fromisoformat(saisie)          # naïf (heure locale)
    except ValueError:
        return None
    return dt.replace(tzinfo=FUSEAU_LOCAL).astimezone(FUSEAU_UTC).isoformat(timespec="seconds")


def format_local(iso_utc: str | None) -> str:
    """Formate un horodatage UTC ('...+00:00') en heure locale 'JJ/MM/AAAA HH:MM'."""
    if not iso_utc:
        return ""
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return iso_utc
    return dt.astimezone(FUSEAU_LOCAL).strftime("%d/%m/%Y %H:%M")


def format_duree(secondes: float | None) -> str:
    """
    Met en forme une durée en secondes : « 45 min », « 2 h 05 », « 3 j 4 h ».

    Returns:
        Chaîne lisible, ou « — » si la durée est inconnue (None).
    """
    if secondes is None:
        return "—"
    secondes = int(secondes)
    jours, reste = divmod(secondes, 86400)
    heures, reste = divmod(reste, 3600)
    minutes = reste // 60
    if jours:
        return f"{jours} j {heures} h"
    if heures:
        return f"{heures} h {minutes:02d}"
    return f"{minutes} min"


def pluriel(n: int, singulier: str, pluriel: str) -> str:
    """
    Accord au singulier ou au pluriel selon `n` (grammaire FR : -1/0/1 =
    singulier, |n| >= 2 = pluriel). Enregistrée comme global Jinja
    (`app/templating.py`) et utilisable dans TOUS les gabarits sans import :
    `{{ n }} {{ pluriel(n, 'jeu', 'jeux') }}` — remplace les pluriels
    parenthésés type « jeu(x) », « prêt(s) » (voir docs/idees-ux.md Q2).

    Args:
        n: la quantité qui détermine l'accord.
        singulier: forme au singulier (ex. « jeu »).
        pluriel: forme au pluriel (ex. « jeux » — jamais déduite
            automatiquement, les pluriels irréguliers sont fréquents en FR).
    """
    return singulier if -1 <= n <= 1 else pluriel


# Un jeu rendu moins de SEUIL_ERREUR_PRET_S après sa sortie n'a pas été prêté :
# mauvaise boîte scannée, ou visiteur qui se ravise au comptoir. La ligne est
# alors requalifiée en `motif = 'erreur'` et sort des statistiques (voir
# `_marquer_erreur_si_immediat`). Seuil volontairement bas : au-delà d'une
# minute, un retour rapide reste un vrai prêt (un jeu essayé sur place).
SEUIL_ERREUR_PRET_S = 60


def _duree_secondes(sortie_iso: str, retour_iso: str | None) -> float:
    """Durée d'un prêt en secondes (jusqu'à `retour_iso`, ou jusqu'à maintenant)."""
    debut = datetime.fromisoformat(sortie_iso)
    fin = datetime.fromisoformat(retour_iso) if retour_iso else datetime.now(timezone.utc)
    return (fin - debut).total_seconds()


def _filtre_periode(colonne: str, debut: str | None, fin: str | None) -> tuple[str, list]:
    """
    Construit un fragment SQL « AND <colonne> >= ? AND <colonne> < ? » selon les
    bornes fournies (UTC ISO). Retourne (fragment, params) — fragment vide si
    aucune borne. Borne de fin EXCLUSIVE.
    """
    fragment, params = "", []
    if debut:
        fragment += f" AND {colonne} >= ?"
        params.append(debut)
    if fin:
        fragment += f" AND {colonne} < ?"
        params.append(fin)
    return fragment, params


def maintenant() -> str:
    """
    Horodatage courant au format ISO 8601, en UTC, à la seconde.

    On stocke toujours en UTC (ex. ``2026-06-16T14:01:18+00:00``) pour éviter
    toute ambiguïté de fuseau ou d'heure d'été ; la conversion en heure locale,
    si besoin, se fait à l'affichage.

    Returns:
        La date/heure courante UTC sous forme de chaîne ISO 8601.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ===========================================================================
# LECTURE / ÉTAT
# ===========================================================================
def info_exemplaire(conn: sqlite3.Connection, id_exemplaire: str) -> dict | None:
    """
    Renvoie les informations d'un exemplaire et de son titre.

    Jointure `exemplaires` → `titres` : on récupère en une fois l'identité de la
    boîte et toutes les caractéristiques du jeu (utile pour la fiche publique).

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte (TEXT, ex. "00472").

    Returns:
        Un dict des colonnes (id_exemplaire, reference_titre, nom, categorie,
        nb_joueurs_min/max, duree_min, age_min, editeur, auteur, annee_edition,
        descriptif), ou ``None`` si l'exemplaire est inconnu.
    """
    row = conn.execute(
        """
        SELECT e.id_exemplaire, t.reference_titre, t.nom, t.type_jeu, t.categorie,
               t.nb_joueurs_min, t.nb_joueurs_max, t.duree_min, t.age_min,
               t.editeur, t.auteur, t.annee_edition, t.descriptif
        FROM exemplaires e
        JOIN titres t ON t.reference_titre = e.reference_titre
        WHERE e.id_exemplaire = ?
        """,
        (id_exemplaire,),
    ).fetchone()
    return dict(row) if row else None


def pret_en_cours(conn: sqlite3.Connection, id_exemplaire: str) -> dict | None:
    """
    Renvoie le prêt NON CLOS d'un exemplaire (celui dont `date_retour IS NULL`).

    C'est la brique qui matérialise l'état « sorti » : s'il existe une telle
    ligne, l'exemplaire est dehors. On trie par `id_pret` décroissant et on
    limite à 1 par sécurité (il ne devrait jamais y en avoir plus d'un ouvert).

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte.

    Returns:
        Un dict {id_pret, numero_pochette, date_sortie} si l'exemplaire est
        sorti, sinon ``None``.
    """
    row = conn.execute(
        """
        SELECT id_pret, numero_pochette, date_sortie, motif
        FROM prets
        WHERE id_exemplaire = ? AND date_retour IS NULL
        ORDER BY id_pret DESC
        LIMIT 1
        """,
        (id_exemplaire,),
    ).fetchone()
    return dict(row) if row else None


def est_sorti(conn: sqlite3.Connection, id_exemplaire: str) -> bool:
    """Raccourci booléen : True si l'exemplaire a un prêt non clos."""
    return pret_en_cours(conn, id_exemplaire) is not None


def lister_categories(conn: sqlite3.Connection) -> list[str]:
    """
    Liste les catégories distinctes du catalogue (pour le menu de filtrage).

    Source : la colonne `titres.categorie` (issue du CSV « Type jeu »). On exclut
    les valeurs nulles/vides et on trie alphabétiquement.

    Returns:
        Liste de chaînes (catégories), triée.
    """
    rows = conn.execute(
        "SELECT DISTINCT categorie FROM titres "
        "WHERE categorie IS NOT NULL AND categorie <> '' ORDER BY categorie"
    ).fetchall()
    return [r[0] for r in rows]


def ages_disponibles(conn: sqlite3.Connection) -> list[int]:
    """
    Liste les âges minimum distincts présents (pour le menu « âge » du filtre).

    Returns:
        Liste d'entiers (âges min), triée croissant.
    """
    rows = conn.execute(
        "SELECT DISTINCT age_min FROM titres WHERE age_min IS NOT NULL ORDER BY age_min"
    ).fetchall()
    return [r[0] for r in rows]


def max_joueurs(conn: sqlite3.Connection) -> int:
    """
    Plus grand `nb_joueurs_max` du catalogue (borne haute du menu « joueurs »).

    Returns:
        Un entier (0 si aucune donnée de joueurs n'est renseignée).
    """
    val = conn.execute("SELECT MAX(nb_joueurs_max) FROM titres").fetchone()[0]
    return val or 0


def lister_catalogue(conn: sqlite3.Connection, categorie: str | None = None,
                     q: str | None = None, age: int | None = None,
                     joueurs: int | None = None,
                     dispo_seulement: bool = False) -> list[dict]:
    """
    Construit le catalogue AU NIVEAU TITRE, avec disponibilité et filtres.

    Pour chaque titre, on calcule en une requête : un exemplaire représentatif
    (le plus petit id, pour le lien vers la fiche), le nombre total
    d'exemplaires, et combien sont disponibles.

    Astuce SQL : le LEFT JOIN sur `prets` est restreint aux prêts NON CLOS
    (`date_retour IS NULL`). Pour un exemplaire disponible, aucune ligne de prêt
    ne se joint → `p.id_pret` est NULL → on le compte comme disponible via
    `SUM(CASE WHEN p.id_pret IS NULL THEN 1 ELSE 0 END)`.

    Filtres combinables (tous optionnels, ajoutés dynamiquement au WHERE) :
        categorie : égalité exacte sur la catégorie.
        q         : sous-chaîne dans le nom (LIKE, insensible à la casse ASCII).
        age       : jeux accessibles dès cet âge (`age_min <= age`).
        joueurs   : jeux jouables à ce nombre EXACT (`min <= joueurs <= max`).
    Les jeux dont l'information filtrée est absente (âge/joueurs NULL) sont
    naturellement exclus quand le filtre correspondant est actif (comparaison
    avec NULL = faux).

    `dispo_seulement` filtre sur un AGRÉGAT (`disponible`), pas une colonne de
    `titres` : il ne peut donc pas rejoindre les autres conditions dans le
    `WHERE` (évalué avant le `GROUP BY`) et va dans un `HAVING`, appliqué après
    agrégation.

    Args:
        conn: connexion SQLite ouverte.
        categorie, q, age, joueurs: filtres optionnels (voir ci-dessus).
        dispo_seulement: si True, ne garde que les titres ayant au moins un
            exemplaire disponible.

    Returns:
        Liste de dicts {reference_titre, nom, categorie, id_repr, total,
        disponible}, triée par nom (insensible à la casse).
    """
    sql = """
        SELECT t.reference_titre, t.nom, t.categorie,
               MIN(e.id_exemplaire) AS id_repr,
               COUNT(e.id_exemplaire) AS total,
               SUM(CASE WHEN p.id_pret IS NULL THEN 1 ELSE 0 END) AS disponible
        FROM titres t
        JOIN exemplaires e ON e.reference_titre = t.reference_titre
        LEFT JOIN prets p
               ON p.id_exemplaire = e.id_exemplaire AND p.date_retour IS NULL
    """
    # On accumule les conditions et leurs paramètres pour un WHERE paramétré
    # (jamais de concaténation de valeurs → pas d'injection SQL).
    conditions: list[str] = []
    params: list = []
    if categorie:
        conditions.append("t.categorie = ?")
        params.append(categorie)
    if q:
        conditions.append("t.nom LIKE ? COLLATE NOCASE")
        params.append(f"%{q}%")
    if age is not None:
        conditions.append("t.age_min IS NOT NULL AND t.age_min <= ?")
        params.append(age)
    if joueurs is not None:
        conditions.append("t.nb_joueurs_min <= ? AND t.nb_joueurs_max >= ?")
        params.extend([joueurs, joueurs])
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " GROUP BY t.reference_titre, t.nom, t.categorie"
    if dispo_seulement:
        sql += " HAVING SUM(CASE WHEN p.id_pret IS NULL THEN 1 ELSE 0 END) > 0"
    sql += " ORDER BY t.nom COLLATE NOCASE"
    return [dict(r) for r in conn.execute(sql, params)]


# ===========================================================================
# STATISTIQUES (post-événement) — fondées sur l'historique complet des prêts
# ===========================================================================
def stats_globales(conn: sqlite3.Connection, debut: str | None = None,
                   fin: str | None = None) -> dict:
    """
    Indicateurs de synthèse, éventuellement restreints à une période.

    Args:
        conn: connexion SQLite ouverte.
        debut, fin: bornes UTC ISO optionnelles (fin exclusive) sur date_sortie.

    Returns:
        dict avec total_prets, en_cours, titres_pretes, nb_titres, erreurs.
    """
    # Les sorties « tournoi » et les erreurs de prêt (retour immédiat, voir
    # `_marquer_erreur_si_immediat`) sont exclues de toutes les statistiques :
    # le filtre `motif = 'pret'` ci-dessous s'en charge partout.
    f, params = _filtre_periode("date_sortie", debut, fin)
    total_prets = conn.execute(
        f"SELECT COUNT(*) FROM prets WHERE motif = 'pret'{f}", params
    ).fetchone()[0]
    en_cours = conn.execute(
        f"SELECT COUNT(*) FROM prets WHERE date_retour IS NULL AND motif = 'pret'{f}",
        params,
    ).fetchone()[0]
    titres_pretes = conn.execute(
        f"""
        SELECT COUNT(DISTINCT e.reference_titre)
        FROM prets p JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
        WHERE p.motif = 'pret'{f}
        """,
        params,
    ).fetchone()[0]
    nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
    # Erreurs de prêt : comptées à part, jamais mêlées aux chiffres ci-dessus.
    erreurs = conn.execute(
        f"SELECT COUNT(*) FROM prets WHERE motif = 'erreur'{f}", params
    ).fetchone()[0]
    # Durée moyenne, sur les prêts TERMINÉS uniquement (hors tournoi, période incluse).
    moyenne = conn.execute(
        f"""
        SELECT AVG((julianday(date_retour) - julianday(date_sortie)) * 86400)
        FROM prets
        WHERE motif = 'pret' AND date_retour IS NOT NULL{f}
        """,
        params,
    ).fetchone()[0]
    return {
        "total_prets": total_prets,
        "en_cours": en_cours,
        "titres_pretes": titres_pretes,
        "nb_titres": nb_titres,
        "duree_moyenne": format_duree(moyenne) if moyenne is not None else "—",
        "erreurs": erreurs,
    }


def palmares(conn: sqlite3.Connection, sens: str = "desc",
             metrique: str = "total", limite: int = 15,
             debut: str | None = None, fin: str | None = None) -> list[dict]:
    """
    Palmarès des titres, agrégé sur tous leurs exemplaires.

    « Catalogue d'abord » : on part de TOUS les titres (JOIN sur exemplaires) et
    on rattache les prêts par LEFT JOIN. Ainsi un titre jamais prêté apparaît
    avec `nb_prets = 0` — indispensable pour le palmarès des MOINS prêtés.

    IMPORTANT : le filtre de période est placé dans la condition du LEFT JOIN
    (et non dans un WHERE), pour conserver les titres à zéro prêt sur la période.

    Args:
        conn: connexion SQLite ouverte.
        sens: "desc" (plus prêtés) ou "asc" (moins prêtés).
        metrique: "total" (nombre brut) ou "exemplaire" (rapporté au nombre
            d'exemplaires).
        limite: nombre de lignes renvoyées.
        debut, fin: bornes UTC ISO optionnelles (fin exclusive) sur date_sortie.

    Returns:
        Liste de dicts {reference_titre, nom, nb_exemplaires, nb_prets,
        par_exemplaire}.

    Sécurité : `metrique`/`sens` sont normalisés en amont (route) à des valeurs
    connues ; seuls les paramètres liés (bornes, limite) viennent de l'extérieur.
    """
    cle = ("CAST(COUNT(p.id_pret) AS REAL) / COUNT(DISTINCT e.id_exemplaire)"
           if metrique == "exemplaire" else "COUNT(p.id_pret)")
    direction = "ASC" if sens == "asc" else "DESC"
    f, params = _filtre_periode("p.date_sortie", debut, fin)
    rows = conn.execute(
        f"""
        SELECT t.reference_titre, t.nom,
               COUNT(DISTINCT e.id_exemplaire) AS nb_exemplaires,
               COUNT(p.id_pret) AS nb_prets,
               CAST(COUNT(p.id_pret) AS REAL) / COUNT(DISTINCT e.id_exemplaire)
                   AS par_exemplaire
        FROM titres t
        JOIN exemplaires e ON e.reference_titre = t.reference_titre
        LEFT JOIN prets p ON p.id_exemplaire = e.id_exemplaire
                          AND p.motif = 'pret'{f}
        GROUP BY t.reference_titre, t.nom
        ORDER BY {cle} {direction}, t.nom COLLATE NOCASE
        LIMIT ?
        """,
        params + [limite],
    ).fetchall()
    return [dict(r) for r in rows]


def prets_par_heure(conn: sqlite3.Connection, debut: str | None = None,
                    fin: str | None = None) -> list[dict]:
    """
    Nombre de prêts par heure LOCALE (Europe/Paris), pour l'histogramme,
    éventuellement restreint à une période.

    `date_sortie` est stocké en UTC ISO ; le regroupement par heure se fait
    APRÈS conversion en heure locale, côté Python (pas de logique de fuseau en
    SQL) — sinon un prêt fait à 15 h (heure française) atterrit dans la barre
    « 13 h » l'été, alors que le reste de la page (filtres, liste détaillée)
    est déjà en heure locale. Voir docs/idees-ux.md M1.

    Returns:
        Liste de dicts {heure: 'AAAA-MM-JJTHH' (clé locale, triable), label:
        libellé affiché ('15h', ou '17/07 15h' si la période couvre plusieurs
        jours locaux), n: int}, triée chronologiquement.
    """
    f, params = _filtre_periode("date_sortie", debut, fin)
    rows = conn.execute(
        f"SELECT date_sortie FROM prets WHERE motif = 'pret'{f}",
        params,
    ).fetchall()

    compte: dict[str, int] = {}
    for r in rows:
        try:
            dt_local = datetime.fromisoformat(r["date_sortie"]).astimezone(FUSEAU_LOCAL)
        except (ValueError, TypeError):
            continue                              # date illisible : ignorée, jamais d'erreur
        cle = dt_local.strftime("%Y-%m-%dT%H")
        compte[cle] = compte.get(cle, 0) + 1

    cles = sorted(compte)
    plusieurs_jours = len({cle[:10] for cle in cles}) > 1
    resultat = []
    for cle in cles:
        dt_local = datetime.strptime(cle, "%Y-%m-%dT%H")
        motif = "%d/%m %Hh" if plusieurs_jours else "%Hh"
        resultat.append({"heure": cle, "label": dt_local.strftime(motif), "n": compte[cle]})
    return resultat


def lister_prets_periode(conn: sqlite3.Connection, debut: str | None = None,
                         fin: str | None = None, limite: int | None = None) -> list[dict]:
    """
    Liste détaillée des prêts (un par ligne), éventuellement restreinte à une
    période, triée par date de sortie décroissante.

    Args:
        conn: connexion SQLite ouverte.
        debut, fin: bornes UTC ISO optionnelles (fin exclusive) sur date_sortie.
        limite: nombre maximal de lignes (None = toutes — utile pour l'export).

    Returns:
        Liste de dicts {date_sortie, date_retour, numero_pochette,
        id_exemplaire, nom, sortie_locale, retour_local}. Les champs *_locale
        sont préformatés en heure locale pour l'affichage et les exports.
    """
    f, params = _filtre_periode("p.date_sortie", debut, fin)
    sql = (
        f"""
        SELECT p.date_sortie, p.date_retour, p.numero_pochette,
               e.id_exemplaire, t.nom
        FROM prets p
        JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
        JOIN titres t ON t.reference_titre = e.reference_titre
        WHERE p.motif = 'pret'{f}
        ORDER BY p.date_sortie DESC
        """
    )
    if limite is not None:
        sql += " LIMIT ?"
        params = params + [limite]
    out = []
    for r in conn.execute(sql, params):
        d = dict(r)
        d["sortie_locale"] = format_local(d["date_sortie"])
        d["retour_local"] = format_local(d["date_retour"]) if d["date_retour"] else ""
        secs = _duree_secondes(d["date_sortie"], d["date_retour"])
        # Prêt clos : durée fixe ; prêt en cours : « depuis X ».
        d["duree_txt"] = (format_duree(secs) if d["date_retour"]
                          else "depuis " + format_duree(secs))
        out.append(d)
    return out


def lister_prets_en_cours(conn: sqlite3.Connection) -> dict:
    """
    Jeux actuellement sortis (prêt non clos), séparés par motif.

    Sert la vue « Jeux actuellement sortis » : contrairement aux statistiques,
    elle INCLUT les sorties tournoi (le parc doit les voir), mais dans un bloc
    distinct des prêts au public.

    Returns:
        dict {"pret": [...], "tournoi": [...]} ; chaque élément est un dict
        {id_exemplaire, nom, numero_pochette, sortie_locale, duree_txt}.
    """
    rows = conn.execute(
        """
        SELECT p.date_sortie, p.numero_pochette, p.motif, e.id_exemplaire, t.nom
        FROM prets p
        JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
        JOIN titres t ON t.reference_titre = e.reference_titre
        WHERE p.date_retour IS NULL
        ORDER BY p.date_sortie DESC
        """
    ).fetchall()
    groupes: dict = {"pret": [], "tournoi": []}
    for r in rows:
        d = dict(r)
        d["sortie_locale"] = format_local(d["date_sortie"])
        d["duree_txt"] = "depuis " + format_duree(_duree_secondes(d["date_sortie"], None))
        groupes.setdefault(d["motif"], []).append(d)
    return groupes


def collecter_stats(conn: sqlite3.Connection, metrique: str = "total",
                    debut: str | None = None, fin: str | None = None,
                    limite_palmares: int = 15,
                    limite_prets: int | None = None) -> dict:
    """
    Rassemble toutes les données de la page statistiques en une fois.

    Mutualisé entre l'affichage (/stats) et les exports (Excel/PDF) pour garantir
    qu'ils montrent exactement les mêmes chiffres, avec le même filtre de période.

    Args:
        conn: connexion SQLite ouverte.
        metrique: "total" ou "exemplaire" (pour les palmarès).
        debut, fin: bornes UTC ISO optionnelles (fin exclusive).
        limite_palmares: taille de chaque palmarès.
        limite_prets: limite de la liste détaillée (None = toutes, pour l'export).

    Returns:
        dict {globales, plus, moins, par_heure, prets, metrique}.
    """
    return {
        "globales": stats_globales(conn, debut, fin),
        "plus": palmares(conn, "desc", metrique, limite_palmares, debut, fin),
        "moins": palmares(conn, "asc", metrique, limite_palmares, debut, fin),
        "par_heure": prets_par_heure(conn, debut, fin),
        "prets": lister_prets_periode(conn, debut, fin, limite_prets),
        "metrique": metrique,
    }


def compter_exemplaires_disponibles(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Disponibilité globale du fonds (utilisée par la page d'accueil).

    Returns:
        Un tuple (total_exemplaires, exemplaires_disponibles), où « disponible »
        signifie : aucun prêt en cours (date_retour IS NULL), tous motifs
        confondus (prêt public ou sortie tournoi).
    """
    total = conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
    sortis = conn.execute(
        """
        SELECT COUNT(*) FROM exemplaires e
        WHERE EXISTS (SELECT 1 FROM prets p
                      WHERE p.id_exemplaire = e.id_exemplaire
                        AND p.date_retour IS NULL)
        """
    ).fetchone()[0]
    return total, total - sortis


def derniers_mouvements(conn: sqlite3.Connection, limite: int = 10) -> list[dict]:
    """
    Flux des derniers mouvements (prêts ET retours), du plus récent au plus ancien.

    Pour le tableau de bord temps réel `/live` : on fusionne les sorties
    (`date_sortie`) et les retours (`date_retour` non NULL) en un seul flux trié
    par instant décroissant. Chaque retour et chaque sortie est un événement
    distinct, même s'ils proviennent du même prêt. À instant égal (même seconde),
    un retour est affiché avant un prêt (tri secondaire sur `type`).

    Args:
        conn: connexion SQLite ouverte.
        limite: nombre maximal d'événements renvoyés.

    Le numéro de pochette n'est JAMAIS exposé ici : ce flux alimente l'écran
    public `/live`, et le numéro de pochette est rattaché à une pièce d'identité
    (donnée à protéger). Seuls le type d'événement, le titre et l'instant sortent.

    Returns:
        Liste de dicts {type ('pret'|'retour'), nom, motif, instant (UTC ISO),
        heure_locale ('HH:MM')}, triée par instant décroissant.
    """
    rows = conn.execute(
        """
        SELECT type, instant, motif, nom FROM (
            SELECT 'pret' AS type, p.date_sortie AS instant, p.motif, t.nom
            FROM prets p
            JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
            JOIN titres t ON t.reference_titre = e.reference_titre
            UNION ALL
            SELECT 'retour' AS type, p.date_retour AS instant, p.motif, t.nom
            FROM prets p
            JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
            JOIN titres t ON t.reference_titre = e.reference_titre
            WHERE p.date_retour IS NOT NULL
        )
        ORDER BY instant DESC, type DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        dt = format_local(d["instant"])
        # format_local renvoie 'JJ/MM/AAAA HH:MM' ; on ne garde que l'heure.
        d["heure_locale"] = dt.split(" ")[-1] if dt else ""
        out.append(d)
    return out


def _date_fr(iso: str | None) -> str:
    """Formate une date ISO 'AAAA-MM-JJ' en 'JJ/MM/AAAA' (ou '' si vide/invalide)."""
    try:
        a, m, j = iso.split("-")
        return f"{j}/{m}/{a}"
    except (ValueError, AttributeError):
        return iso or ""


# En-têtes de l'export catalogue — choisis pour être RÉ-IMPORTABLES (mêmes
# intitulés que ceux reconnus par scripts/import_csv.COLONNES).
# Les deux dernières colonnes (rangement, §4.b) sont PAR EXEMPLAIRE : on
# exporte un libellé LISIBLE (le nom de l'emplacement local, pas son id).
EN_TETES_CATALOGUE = [
    "Code jeu", "Nom jeu", "Type", "Type jeu", "Nb joueurs", "Age joueurs",
    "Temps jeu", "Marque", "Auteur", "Année édition", "Date achat", "Descriptif",
    "Emplacement événement", "Emplacement local",
]


def lignes_export_catalogue(conn: sqlite3.Connection) -> tuple[list[str], list[dict]]:
    """
    Prépare l'export du catalogue (une ligne par exemplaire), au format
    ré-importable par `scripts/import_csv.py`.

    Les valeurs normalisées en base sont re-sérialisées dans un format que les
    parseurs de l'import savent relire : joueurs « 2 - 4 », âge « 10 », durée
    « 30 », date d'achat « JJ/MM/AAAA ».

    Returns:
        (en-têtes, lignes) où chaque ligne est un dict clé=en-tête.
    """
    def joueurs(a, b):
        if a and b and b != a:
            return f"{a} - {b}"
        return str(a) if a else ""

    rows = conn.execute(
        """
        SELECT e.id_exemplaire, e.emplacement_evenement,
               er.nom AS emplacement_local_nom,
               t.nom, t.type_jeu, t.categorie,
               t.nb_joueurs_min, t.nb_joueurs_max, t.duree_min, t.age_min,
               t.editeur, t.auteur, t.annee_edition, t.descriptif, t.date_achat
        FROM exemplaires e
        JOIN titres t ON t.reference_titre = e.reference_titre
        LEFT JOIN emplacements_rangement er ON er.id_emplacement = e.emplacement_local_id
        ORDER BY t.nom COLLATE NOCASE, e.id_exemplaire
        """
    ).fetchall()
    lignes = []
    for r in rows:
        lignes.append({
            "Code jeu": r["id_exemplaire"],
            "Nom jeu": r["nom"],
            "Type": r["type_jeu"] or "",
            "Type jeu": r["categorie"] or "",
            "Nb joueurs": joueurs(r["nb_joueurs_min"], r["nb_joueurs_max"]),
            "Age joueurs": str(r["age_min"]) if r["age_min"] is not None else "",
            "Temps jeu": str(r["duree_min"]) if r["duree_min"] is not None else "",
            "Marque": r["editeur"] or "",
            "Auteur": r["auteur"] or "",
            "Année édition": str(r["annee_edition"]) if r["annee_edition"] is not None else "",
            "Date achat": _date_fr(r["date_achat"]),
            "Descriptif": r["descriptif"] or "",
            "Emplacement événement": r["emplacement_evenement"] or "",
            "Emplacement local": r["emplacement_local_nom"] or "",
        })
    return EN_TETES_CATALOGUE, lignes


def derniers_achats(conn: sqlite3.Connection, n: int = 10) -> list[dict]:
    """
    Les `n` jeux les plus récemment achetés (d'après `titres.date_achat`).

    Au niveau titre : un jeu = une ligne, date = la plus récente de ses
    exemplaires (calculée à l'import). Les titres sans date d'achat sont ignorés.

    Returns:
        Liste de dicts {reference_titre, nom, date_achat, date_achat_txt,
        id_repr} triée du plus récent au plus ancien.
    """
    rows = conn.execute(
        """
        SELECT t.reference_titre, t.nom, t.date_achat,
               MIN(e.id_exemplaire) AS id_repr
        FROM titres t
        JOIN exemplaires e ON e.reference_titre = t.reference_titre
        WHERE t.date_achat IS NOT NULL AND t.date_achat <> ''
        GROUP BY t.reference_titre, t.nom, t.date_achat
        ORDER BY t.date_achat DESC, t.nom COLLATE NOCASE
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["date_achat_txt"] = _date_fr(d["date_achat"])
        out.append(d)
    return out


def dispo_par_titre(conn: sqlite3.Connection, reference_titre: str) -> tuple[int, int]:
    """
    Disponibilité d'un titre donné (utilisé par la fiche d'un exemplaire).

    Args:
        conn: connexion SQLite ouverte.
        reference_titre: clé du titre.

    Returns:
        Un tuple (total_exemplaires, exemplaires_disponibles).
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM exemplaires WHERE reference_titre = ?",
        (reference_titre,),
    ).fetchone()[0]
    # « sortis » = exemplaires ayant AU MOINS un prêt non clos.
    sortis = conn.execute(
        """
        SELECT COUNT(*) FROM exemplaires e
        WHERE e.reference_titre = ?
          AND EXISTS (SELECT 1 FROM prets p
                      WHERE p.id_exemplaire = e.id_exemplaire
                        AND p.date_retour IS NULL)
        """,
        (reference_titre,),
    ).fetchone()[0]
    return total, total - sortis


# ===========================================================================
# TRANSACTIONS — rendre « lire puis écrire » indivisible
# ===========================================================================
@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """
    Ouvre une transaction en ÉCRITURE, et la committe à la sortie du bloc.

    POURQUOI (test de charge du 30 juillet 2026, docs/protocole-stress-test.md §2)
    -----------------------------------------------------------------------------
    Toutes les écritures de ce module procèdent en deux temps : on lit un état
    (« quel est le plus petit numéro libre ? », « cette boîte est-elle sortie ? »)
    puis on agit dessus. Or, en mode « legacy » du module `sqlite3`, un SELECT
    n'ouvre AUCUNE transaction : le verrou d'écriture n'est pris qu'au premier
    UPDATE/INSERT. Entre la lecture et l'écriture, n'importe quelle autre
    requête peut donc lire le même état. Huit prêts simultanés sont ainsi
    repartis avec la pochette n°12 — soit huit pièces d'identité pour un seul
    casier, sans le moindre message d'erreur.

    `BEGIN IMMEDIATE` prend le verrou d'écriture DÈS L'OUVERTURE, donc AVANT la
    lecture : les écritures concurrentes attendent leur tour au lieu de lire un
    état périmé. En WAL, les LECTURES (catalogue, fiches, écran de salle) ne
    sont pas gênées : elles continuent de voir l'état d'avant le commit.

    RÉENTRANCE — le point délicat
    ------------------------------
    `repreter()` écrit (clôture de l'ancien prêt) PUIS appelle `preter()`. Un
    `BEGIN IMMEDIATE` posé naïvement dans `preter()` lèverait alors « cannot
    start a transaction within a transaction ». D'où le test `in_transaction` :
    si une transaction est déjà ouverte, on s'y greffe sans rien ouvrir ni
    committer — c'est le bloc extérieur qui décide.

    Se greffer n'est jamais un pari : en mode legacy, une transaction ne
    s'ouvre implicitement QUE sur une écriture (un SELECT seul laisse
    `in_transaction` à False). Donc `in_transaction == True` implique qu'une
    écriture a déjà eu lieu, donc que le verrou d'écriture est DÉJÀ tenu.

    Ce mécanisme est volontairement préféré à `conn.isolation_level = None` :
    en mode autocommit, tous les `conn.commit()` de ce module deviendraient des
    non-opérations et le contrat « ne committe pas, c'est l'appelant qui
    committe » de `plus_petit_numero_libre`/`liberer_numero`/`_effacer_pochette`
    tomberait silencieusement.

    Raises:
        sqlite3.OperationalError: « database is locked » si le verrou n'est pas
            obtenu dans le délai de `db.get_connection()`. Les routes de prêt
            rattrapent ce cas et affichent un message de reprise en un tap
            (jamais d'erreur brute — règle « ne jamais bloquer »).
    """
    if conn.in_transaction:
        # Une écriture a déjà eu lieu sur cette connexion : le verrou est tenu,
        # et c'est l'appelant extérieur qui committera.
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


# ===========================================================================
# POCHETTES — attribution / libération des numéros
# ===========================================================================
def plus_petit_numero_libre(conn: sqlite3.Connection) -> int:
    """
    Attribue le PLUS PETIT numéro de pochette libre, et le marque occupé.

    Deux cas :
    1. Il existe une pochette libérée (occupe = 0) → on réutilise la plus petite
       (recyclage), pour garder des numéros bas et tassés.
    2. Aucune libre → on en crée une nouvelle (max + 1, ou 1 si table vide).
       AUCUN PLAFOND : on ne refuse jamais un prêt (spec §6).

    Effet de bord : modifie la table `pochettes` (mais ne committe pas ; c'est
    `preter()` qui committe l'ensemble de l'opération).

    ⚠️ À N'APPELER QUE SOUS `transaction(conn)`. La lecture du plus petit numéro
    libre et sa réservation doivent former un bloc indivisible : sans le verrou
    d'écriture pris au préalable, deux appels concurrents lisent le même numéro
    et le renvoient tous les deux (cas n°1 du § 2 du protocole de test de
    charge). La branche « aucune libre » ci-dessous est plus brutale encore :
    deux INSERT du même `MAX + 1` violent la clé primaire, donc une erreur 500.

    Returns:
        Le numéro de pochette attribué (entier ≥ 1).
    """
    libre = conn.execute(
        "SELECT MIN(numero_pochette) FROM pochettes WHERE occupe = 0"
    ).fetchone()[0]
    if libre is not None:
        conn.execute(
            "UPDATE pochettes SET occupe = 1 WHERE numero_pochette = ?", (libre,)
        )
        return libre
    nouveau = conn.execute(
        "SELECT COALESCE(MAX(numero_pochette), 0) + 1 FROM pochettes"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO pochettes (numero_pochette, occupe) VALUES (?, 1)", (nouveau,)
    )
    return nouveau


def liberer_numero(conn: sqlite3.Connection, numero_pochette: int) -> None:
    """Marque une pochette comme libre (occupe = 0) ; ne committe pas."""
    conn.execute(
        "UPDATE pochettes SET occupe = 0 WHERE numero_pochette = ?", (numero_pochette,)
    )


# ===========================================================================
# OPÉRATIONS DE PRÊT / RETOUR
# ===========================================================================
def preter(conn: sqlite3.Connection, id_exemplaire: str) -> int:
    """
    Ouvre un prêt sur un exemplaire DISPONIBLE.

    Attribue le plus petit numéro de pochette libre, enregistre la sortie
    (date_sortie = maintenant, date_retour = NULL) et committe.

    Attribution et écriture se font sous `transaction(conn)` : c'est ce qui
    empêche deux prêts simultanés de repartir avec la même pochette. Appelée
    depuis un bloc déjà ouvert (cas de `repreter`), elle s'y greffe et laisse
    l'appelant committer.

    Cette fonction ne CONTRÔLE PAS que l'exemplaire est disponible et ne refuse
    jamais rien (« ne jamais bloquer »). Pour un contrôle atomique du type
    « déjà sortie ? », voir `preter_si_disponible`, que les routes utilisent.

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte à prêter.

    Returns:
        Le numéro de pochette attribué (à afficher au bénévole).
    """
    with transaction(conn):
        numero = plus_petit_numero_libre(conn)
        conn.execute(
            """
            INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)
            VALUES (?, ?, ?, 'pret')
            """,
            (id_exemplaire, numero, maintenant()),
        )
    return numero


def preter_si_disponible(conn: sqlite3.Connection, id_exemplaire: str) -> dict:
    """
    Prête un exemplaire APRÈS avoir vérifié, de façon atomique, qu'il est bien
    disponible. C'est la porte d'entrée des routes.

    POURQUOI CETTE FONCTION EXISTE
    ------------------------------
    La route faisait « `pret_en_cours()` puis `preter()` » : deux appuis
    simultanés sur la même boîte — deux téléphones, ou un double-appui sur un
    wifi lent — passaient le contrôle tous les deux et ouvraient deux prêts
    (cas n°3 du § 2 de docs/protocole-stress-test.md ; le test de charge en a
    ouvert huit d'un coup). Le contrôle et l'action sont ici DANS la même
    transaction en écriture : le second appui lit forcément l'état déjà à jour
    et repart avec « déjà sortie ».

    Ce n'est PAS un refus au sens « ne jamais bloquer » : le bénévole obtient un
    message et l'écran garde ses boutons d'action.

    Returns:
        {"numero": n} si le prêt vient d'être ouvert, ou
        {"deja_sorti": True, "numero": n} si la boîte était déjà sortie —
        `numero` est alors celui du prêt EN COURS (0 pour une sortie tournoi).
    """
    with transaction(conn):
        courant = pret_en_cours(conn, id_exemplaire)
        if courant is not None:
            return {"deja_sorti": True, "numero": courant["numero_pochette"]}
        return {"numero": preter(conn, id_exemplaire)}


# Numéro de pochette « factice » pour les sorties tournoi (pas d'emplacement).
NUMERO_TOURNOI = 0


def sortir_tournoi(conn: sqlite3.Connection, id_exemplaire: str) -> None:
    """
    Sort un exemplaire pour un TOURNOI (pas de PI, pas d'emplacement attribué).

    Crée une ligne de prêt `motif='tournoi'` avec `numero_pochette = 0` (marqueur
    « sans emplacement »). L'exemplaire devient « sorti » (date_retour NULL) donc
    indisponible, mais ces sorties sont EXCLUES des statistiques (filtre
    `motif='pret'`). L'appelant garantit que l'exemplaire est disponible.

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte prélevée pour le tournoi.
    """
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)
            VALUES (?, ?, ?, 'tournoi')
            """,
            (id_exemplaire, NUMERO_TOURNOI, maintenant()),
        )


def sortir_tournoi_si_disponible(conn: sqlite3.Connection,
                                 id_exemplaire: str) -> dict:
    """
    Sort un exemplaire pour un tournoi APRÈS contrôle atomique de sa
    disponibilité — pendant de `preter_si_disponible`, même motif et même
    remède (la route souffrait du même écart entre la vérification et l'action).

    Returns:
        {"sorti": True} si la sortie vient d'être enregistrée, ou
        {"deja_sorti": True, "numero": n} si la boîte était déjà sortie.
    """
    with transaction(conn):
        courant = pret_en_cours(conn, id_exemplaire)
        if courant is not None:
            return {"deja_sorti": True, "numero": courant["numero_pochette"]}
        sortir_tournoi(conn, id_exemplaire)
        return {"sorti": True}


def _effacer_pochette(conn: sqlite3.Connection, id_pret: int) -> None:
    """
    Efface le numéro de pochette d'une ligne de prêt CLOSE ; ne committe pas.

    Le numéro désigne le casier où se trouve une pièce d'identité : il n'a
    d'utilité que pendant le prêt (décision Simon du 2026-07-18, voir
    docs/specification.md §3.2 et le commentaire de models.SCHEMA_PRETS). Une
    fois la pochette rendue et recyclée, le conserver ne renseignerait plus sur
    rien d'utile mais l'exposerait dans tous les exports et toutes les
    sauvegardes.

    La LIGNE, elle, n'est jamais supprimée : dates et motif restent, donc
    l'historique et les statistiques sont intacts.

    ⚠️ À appeler APRÈS avoir lu le numéro quand il doit être affiché au
    bénévole (cas de `rendre`).
    """
    conn.execute(
        "UPDATE prets SET numero_pochette = NULL WHERE id_pret = ?", (id_pret,)
    )


def _marquer_erreur_si_immediat(conn: sqlite3.Connection, courant: dict | sqlite3.Row,
                                instant_retour: str) -> bool:
    """
    Requalifie en « erreur de prêt » un prêt rendu moins d'une minute après sa
    sortie ; ne committe pas. À appeler DANS la transaction qui clôt le prêt.

    Un jeu qui revient au comptoir en moins d'une minute n'a pas été prêté : le
    bénévole s'est trompé de boîte, ou le visiteur a changé d'avis pendant qu'on
    lui prenait sa pièce d'identité. Le compter comme un prêt fausse deux fois
    les statistiques — un prêt de plus au palmarès du titre, et une durée
    ridicule dans la moyenne.

    La ligne n'est PAS supprimée : elle passe de `motif = 'pret'` à
    `motif = 'erreur'`. Toutes les requêtes de statistiques filtrant déjà
    `motif = 'pret'`, l'exclusion est acquise sans toucher une seule d'entre
    elles, et le fait reste consultable (compteur dédié sur /stats).

    Les sorties tournoi (`motif = 'tournoi'`) ne sont jamais requalifiées :
    elles sont déjà hors statistiques, et leur motif porte une information que
    « erreur » effacerait.

    Args:
        conn: connexion SQLite ouverte, déjà en transaction.
        courant: ligne du prêt qu'on vient de clore (`pret_en_cours`).
        instant_retour: horodatage UTC ISO posé en `date_retour`.

    Returns:
        True si la ligne a été requalifiée.
    """
    if courant["motif"] != "pret":
        return False
    try:
        ecart = _duree_secondes(courant["date_sortie"], instant_retour)
    except (TypeError, ValueError):
        # Horodatage illisible : on ne requalifie rien plutôt que de deviner.
        return False
    if ecart >= SEUIL_ERREUR_PRET_S:
        return False
    conn.execute(
        "UPDATE prets SET motif = 'erreur' WHERE id_pret = ?", (courant["id_pret"],)
    )
    return True


def rendre(conn: sqlite3.Connection, id_exemplaire: str) -> dict:
    """
    Enregistre le retour d'un exemplaire : clôt le prêt/sortie en cours et, s'il
    s'agissait d'un prêt au public, libère sa pochette.

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte rendue.

    Le numéro de pochette est EFFACÉ de la ligne close (voir `_effacer_pochette`)
    — mais seulement après avoir été lu : il est renvoyé à l'appelant, qui doit
    l'afficher au bénévole pour qu'il retrouve la pièce d'identité à restituer.
    C'est le geste central de l'application.

    Un retour immédiat (moins de `SEUIL_ERREUR_PRET_S`) est requalifié en erreur
    de prêt — voir `_marquer_erreur_si_immediat`. Cela ne change RIEN à ce que
    voit le bénévole (il doit toujours rendre la pièce d'identité, au même
    endroit), hormis une ligne d'information : seules les statistiques changent.

    Returns:
        {"numero_libere": n, "motif": "pret", "erreur": bool} pour un prêt au
        public, {"motif": "tournoi"} pour un retour de tournoi (pas
        d'emplacement), ou {"deja_disponible": True} si rien à clore (cas non
        bloquant).
    """
    with transaction(conn):
        # Lecture SOUS le verrou d'écriture : deux retours simultanés sur la
        # même boîte libéreraient sinon deux fois la même pochette, qui serait
        # alors réattribuée alors qu'une pièce d'identité s'y trouve encore.
        courant = pret_en_cours(conn, id_exemplaire)
        if courant is None:
            return {"deja_disponible": True}
        numero = courant["numero_pochette"]          # lu AVANT effacement
        instant = maintenant()
        conn.execute(
            "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
            (instant, courant["id_pret"]),
        )
        _effacer_pochette(conn, courant["id_pret"])
        if courant["motif"] == "tournoi":
            return {"motif": "tournoi"}
        erreur = _marquer_erreur_si_immediat(conn, courant, instant)
        # Prêt au public : on libère le numéro d'emplacement.
        liberer_numero(conn, numero)
    return {"numero_libere": numero, "motif": "pret", "erreur": erreur}


def cloturer_tous_les_prets(conn: sqlite3.Connection) -> int:
    """
    Clôture TOUS les prêts/sorties en cours et libère toutes les pochettes.

    Usage : remise à blanc en fin d'événement. On NE supprime PAS l'historique
    (les statistiques restent) : on se contente de poser `date_retour = maintenant`
    sur tout ce qui était encore ouvert, et de libérer toutes les pochettes. Cela
    couvre aussi les sorties tournoi encore ouvertes.

    Les numéros de pochette des lignes ainsi closes sont effacés dans la MÊME
    requête (voir `_effacer_pochette` pour le pourquoi) : à la fin d'un
    événement, plus aucun numéro ne subsiste en base.

    C'est aussi le moment naturel pour purger les vieilles lignes du REGISTRE
    DES APPAREILS (docs/conception-journal.md §8.1) : ce registre est
    persistant ET sauvegardé, donc il ne se vide jamais de lui-même,
    contrairement au journal dont les rotations disparaissent. La purge est
    silencieuse et ne touche que ce qui date de plus d'un an — l'appareil d'un
    bénévole qui revient d'une édition sur l'autre garde donc son libellé.

    Returns:
        Le nombre de prêts/sorties clôturés (le registre, lui, n'intéresse pas
        l'écran de fin d'événement : il n'a rien à voir avec les prêts).
    """
    with transaction(conn):
        cur = conn.execute(
            "UPDATE prets SET date_retour = ?, numero_pochette = NULL "
            "WHERE date_retour IS NULL",
            (maintenant(),),
        )
        nb = cur.rowcount
        conn.execute("UPDATE pochettes SET occupe = 0")
        purger_appareils_anciens(conn)
    return nb


def _clore_pret_oublie(conn: sqlite3.Connection, oublie: dict | sqlite3.Row) -> int | None:
    """
    Clôt un prêt qu'on n'a jamais scanné en retour, et libère son numéro de
    pochette s'il en avait un ; ne committe pas. À appeler DANS la transaction
    qui enchaîne sur l'écriture suivante.

    Le cas : une boîte est notée sortie alors qu'elle est physiquement revenue
    (retour non scanné, ou sortie tournoi jamais rentrée). Tant que ce prêt
    fantôme reste ouvert, la boîte est inempruntable — c'est l'impasse que
    l'escalade du transfert lève (voir `transferer_pochette`).

    LA LIGNE N'EST PAS REQUALIFIÉE. Sa durée est fausse (elle court jusqu'au
    moment où quelqu'un s'en aperçoit) et pollue les statistiques de durée,
    mais `repreter` a exactement le même défaut depuis toujours : le corriger
    ici seulement rendrait les deux gestes incomparables. Un motif dédié est à
    poser des DEUX côtés à la fois, dans un lot ultérieur (registre
    `interne/chantiers.md`, série agora). `_marquer_erreur_si_immediat` n'est
    pas non plus appelée : un prêt oublié n'est pas un prêt d'une minute.

    Args:
        conn: connexion SQLite ouverte, déjà en transaction.
        oublie: la ligne du prêt à clore (`pret_en_cours`).

    Returns:
        Le numéro de pochette libéré, ou None quand il n'y en avait pas à
        libérer — sortie tournoi (`motif != 'pret'`, numéro 0) ou ligne sans
        numéro. Ne JAMAIS libérer dans ce cas : le numéro 0 est un marqueur
        « sans emplacement », pas un casier.
    """
    numero = oublie["numero_pochette"]               # lu AVANT effacement (D5)
    conn.execute(
        "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
        (maintenant(), oublie["id_pret"]),
    )
    _effacer_pochette(conn, oublie["id_pret"])
    if oublie["motif"] != "pret" or not numero:
        return None
    liberer_numero(conn, numero)
    return numero


def repreter(conn: sqlite3.Connection, id_exemplaire: str) -> dict:
    """
    Re-prêt après oubli de scan (spec §5.1).

    Scénario : un exemplaire est noté « sorti » en base mais revient physiquement
    et repart aussitôt sans qu'on ait scanné le retour. On considère donc
    l'ancien prêt comme rentré (date_retour = maintenant, ancien numéro libéré),
    puis on ouvre immédiatement un nouveau prêt (nouveau numéro).

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte.

    Returns:
        {"ancien_numero": a, "nouveau_numero": n} dans le cas nominal ; ou
        {"nouveau_numero": n, "etait_disponible": True} si l'exemplaire était en
        réalité déjà disponible (on se contente alors d'un prêt simple).
    """
    # Tout le re-prêt tient dans UNE transaction : la clôture de l'ancien prêt
    # et l'ouverture du nouveau ne doivent jamais être vues séparément, et la
    # lecture d'état ci-dessous doit se faire sous le verrou d'écriture.
    # `preter()` appelée plus bas s'y greffe sans rien committer (c'est la
    # réentrance de `transaction`, voir sa docstring).
    with transaction(conn):
        courant = pret_en_cours(conn, id_exemplaire)
        if courant is None:
            # Incohérence bénigne : rien à clore, on ouvre simplement un prêt.
            return {"nouveau_numero": preter(conn, id_exemplaire),
                    "etait_disponible": True}
        # Clôture de l'ancien prêt + libération de son numéro...
        ancien = courant["numero_pochette"]          # lu AVANT effacement
        conn.execute(
            "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
            (maintenant(), courant["id_pret"]),
        )
        # ... dont on efface le numéro : il est clos. Le NOUVEAU prêt ouvert
        # juste après garde le sien, évidemment (c'est lui qui est en cours).
        _effacer_pochette(conn, courant["id_pret"])
        liberer_numero(conn, ancien)
        # ... puis ouverture d'un nouveau prêt.
        nouveau = preter(conn, id_exemplaire)
    return {"ancien_numero": ancien, "nouveau_numero": nouveau}


def transferer_pochette(conn: sqlite3.Connection, id_rendu: str,
                        id_nouveau: str, *, clore_oubli: bool = False) -> dict:
    """
    Rend une boîte et en prête une autre SANS déplacer la pochette
    (docs/conception-transfert-pochette.md).

    Le visiteur rapporte un jeu et repart aussitôt avec un autre. Enchaîner
    « Rendre » puis « Prêter » ferait sortir sa pièce d'identité du casier n°7
    pour l'y remettre dix secondes plus tard — le plus petit numéro libre étant
    justement, la plupart du temps, celui qu'on vient de libérer. Ici la pièce
    d'identité ne bouge pas : c'est le JEU rattaché au n°7 qui change.

    ⚠️ EXCEPTION ASSUMÉE À LA RÈGLE DU PLUS PETIT NUMÉRO LIBRE
    -----------------------------------------------------------
    Le nouveau prêt réutilise le numéro du prêt clos, MÊME SI un numéro plus
    petit est libre — c'est le seul point du code qui déroge à la règle de la
    spécification §6, et `plus_petit_numero_libre()` n'est donc pas appelée.
    Ce n'est pas un contournement mais une lecture plus juste de la règle : la
    pochette n'est jamais devenue libre, puisque la pièce d'identité n'a pas
    quitté son casier. Voir §3 de la note de conception ; un test verrouille le
    fait qu'un numéro plus petit disponible n'est PAS pris.

    ORDRE IMPOSÉ À L'INTÉRIEUR DE LA TRANSACTION
    ---------------------------------------------
    L'index UNIQUE partiel `idx_pochettes_un_seul_pret` interdit à deux prêts
    OUVERTS de porter le même numéro. La clôture de l'ancien prêt doit donc
    précéder l'insertion du nouveau : la ligne close sort du prédicat partiel
    (`date_retour IS NULL`) avant que la nouvelle n'y entre. Inverser les deux
    ferait échouer l'écriture. Le tout dans une SEULE transaction (`BEGIN
    IMMEDIATE`) : l'état est lu sous le verrou d'écriture, comme dans `rendre`
    et `repreter`, et personne ne peut voir la pochette n°7 sans détenteur.

    La table `pochettes` n'est pas libérée : le n°7 reste occupé du début à la
    fin. Le `UPDATE` de réaffirmation ci-dessous ne sert qu'en base déjà
    incohérente (numéro détenu mais marqué libre) — sans lui, ce numéro
    resterait attribuable à un autre prêt.

    Cette fonction ne suppose pas que les deux boîtes existent : la route le
    vérifie avant d'appeler (patron des autres actions de prêt).

    L'ESCALADE `clore_oubli` — clore un prêt oublié pour débloquer la boîte
    ------------------------------------------------------------------------
    Sans elle, une boîte notée sortie alors qu'elle est physiquement là (retour
    non scanné) ne peut PAS être transférée : le refus `nouveau_sorti` est une
    fin de course, contraire à la règle « ne jamais bloquer » (série agora,
    lot 1 ; §6 de la note de conception, mis à jour en conséquence).

    Avec `clore_oubli=True`, la même transaction clôt DEUX prêts avant
    d'insérer le nouveau :
      1. celui, oublié, de `id_nouveau` — dont le numéro est LIBÉRÉ, la pièce
         d'identité étant censée avoir quitté son casier (voir
         `_clore_pret_oublie`, qui ne libère rien pour une sortie tournoi) ;
      2. celui de `id_rendu` — dont le numéro n'est PAS libéré, c'est tout
         l'objet du transfert.
    L'ordre est imposé par `idx_pochettes_un_seul_pret` : les deux clôtures
    d'abord, l'unique INSERT ensuite.

    Le drapeau ne fait que LEVER un refus : si la boîte est revenue entre
    l'affichage de l'écran et l'appui (l'écran n'est qu'un instantané), le
    transfert est ordinaire et `oubli_clos` est faux. Il ne concerne jamais le
    cas `meme_boite`, où `id_nouveau` est justement la boîte rendue.

    Args:
        conn: connexion SQLite ouverte.
        id_rendu: boîte que le visiteur rapporte (doit être sortie).
        id_nouveau: boîte qu'il emporte. Peut être la MÊME que `id_rendu` (il
            se ravise) : le prêt est alors clos puis rouvert sur le même
            numéro, ce qui vaut mieux que `repreter` qui, lui, en change.
        clore_oubli: lever le refus `nouveau_sorti` en clôturant le prêt oublié
            de `id_nouveau` (voir ci-dessus). Le bénévole a alors la boîte en
            main et a vérifié le casier concerné.

    Returns:
        {"transfere": True, "numero": n, "meme_boite": bool,
         "oubli_clos": bool, "numero_libere": n|None} en cas de succès —
        `numero_libere` est le numéro rendu au pot par la clôture du prêt
        oublié, None quand il n'y en avait pas (sortie tournoi) ou quand
        aucune escalade n'a eu lieu ;
        {"rien_a_rendre": True} si `id_rendu` n'a aucun prêt en cours (un autre
        bénévole a pu l'enregistrer entre-temps) ;
        {"sans_pochette": True} si le prêt en cours est une sortie tournoi, qui
        n'a pas de pièce d'identité à transférer ;
        {"nouveau_sorti": True, "numero": n} si `id_nouveau` est déjà sortie et
        que `clore_oubli` est faux.
        Dans les trois cas de refus, RIEN n'est écrit : l'écran garde ses
        boutons et le retour classique reste à un tap (« ne jamais bloquer »).
    """
    with transaction(conn):
        courant = pret_en_cours(conn, id_rendu)
        if courant is None:
            return {"rien_a_rendre": True}
        numero = courant["numero_pochette"]
        # Sortie tournoi (numéro 0) ou ligne sans numéro : rien à transférer.
        if courant["motif"] != "pret" or not numero:
            return {"sans_pochette": True}
        meme_boite = id_nouveau == id_rendu
        oubli_clos = False
        numero_libere = None
        if not meme_boite:
            autre = pret_en_cours(conn, id_nouveau)
            if autre is not None:
                if not clore_oubli:
                    return {"nouveau_sorti": True,
                            "numero": autre["numero_pochette"]}
                # ESCALADE : la nouvelle boîte est devant le bénévole, mais un
                # prêt jamais scanné en retour la tient encore. On le clôt
                # AVANT d'insérer quoi que ce soit — les DEUX clôtures doivent
                # précéder l'unique INSERT, sans quoi
                # `idx_pochettes_un_seul_pret` refuse l'écriture.
                #
                # Les deux numéros ne peuvent pas être égaux : ce même index
                # interdit que deux prêts ouverts portent le même numéro, et
                # ces deux prêts-là sont ouverts en même temps. Aucun code
                # défensif ici, qui laisserait croire le contraire.
                numero_libere = _clore_pret_oublie(conn, autre)
                oubli_clos = True
        # 1. Clôture de l'ancien prêt, dont le numéro est effacé (D5) — mais
        #    la pochette n'est PAS libérée : la pièce d'identité y est encore.
        instant = maintenant()
        conn.execute(
            "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
            (instant, courant["id_pret"]),
        )
        _effacer_pochette(conn, courant["id_pret"])
        # Un transfert est un retour : si la boîte rendue n'est sortie que
        # depuis quelques secondes, c'est la mauvaise boîte qu'on avait
        # scannée, et le transfert est précisément le rattrapage. La ligne
        # close sort donc des statistiques comme n'importe quel retour
        # immédiat. Le NOUVEAU prêt, lui, n'est pas concerné : il commence.
        _marquer_erreur_si_immediat(conn, courant, instant)
        # 2. Nouveau prêt sur LE MÊME numéro (voir l'exception ci-dessus).
        conn.execute(
            """
            INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)
            VALUES (?, ?, ?, 'pret')
            """,
            (id_nouveau, numero, maintenant()),
        )
        conn.execute(
            "UPDATE pochettes SET occupe = 1 WHERE numero_pochette = ?", (numero,)
        )
    return {"transfere": True, "numero": numero, "meme_boite": meme_boite,
            "oubli_clos": oubli_clos, "numero_libere": numero_libere}


# ===========================================================================
# ADMINISTRATION — création/édition du catalogue depuis l'application
# ===========================================================================
# Préfixe des id_exemplaire créés via l'appli (≠ codes numériques du CSV et des
# codes « E… » existants), pour garantir l'absence de collision.
PREFIXE_ID_ADMIN = "A"


def slug_titre(nom: str) -> str:
    """
    Construit la clé de regroupement `reference_titre` à partir d'un nom.

    Normalisation : majuscules, sans accents, ponctuation → underscore. Deux noms
    identiques à la casse/aux accents près produisent le même slug → ils sont
    regroupés sous le même titre. Ex. 'Mr Jack' → 'MR_JACK'.

    Cette fonction est PARTAGÉE avec scripts/import_csv.py pour que l'import en
    lot et la création via l'admin produisent exactement les mêmes références.

    Args:
        nom: nom d'affichage du jeu.

    Returns:
        Le slug (clé `reference_titre`).
    """
    base = unicodedata.normalize("NFKD", nom)
    base = base.encode("ascii", "ignore").decode("ascii")  # retire les accents
    base = base.upper()
    base = re.sub(r"[^A-Z0-9]+", "_", base)                # ponctuation -> _
    return base.strip("_")


def prochain_id_exemplaire(conn: sqlite3.Connection,
                           prefixe: str = PREFIXE_ID_ADMIN) -> str:
    """
    Calcule le prochain id_exemplaire libre pour un préfixe donné (id AUTO).

    On parcourt les ids existants commençant par `prefixe` et suivis de chiffres,
    et on renvoie le suivant, formaté sur 4 chiffres (ex. 'A0001', 'A0002'…).
    Le préfixe évite toute collision avec les codes du CSV.

    Returns:
        Le nouvel identifiant (TEXT), garanti unique au moment de l'appel.
    """
    rows = conn.execute(
        "SELECT id_exemplaire FROM exemplaires WHERE id_exemplaire LIKE ?",
        (prefixe + "%",),
    ).fetchall()
    maxn = 0
    for (idex,) in rows:
        suffixe = idex[len(prefixe):]
        if suffixe.isdigit():
            maxn = max(maxn, int(suffixe))
    return f"{prefixe}{maxn + 1:04d}"


def get_titre(conn: sqlite3.Connection, reference_titre: str) -> dict | None:
    """Renvoie la ligne du titre (dict), ou None s'il n'existe pas."""
    row = conn.execute(
        "SELECT * FROM titres WHERE reference_titre = ?", (reference_titre,)
    ).fetchone()
    return dict(row) if row else None


def creer_jeu(conn: sqlite3.Connection, nom: str, **champs) -> dict:
    """
    Crée (ou complète) un titre et lui ajoute un premier exemplaire (id AUTO).

    Le `reference_titre` est dérivé du nom (slug). Si un titre de même slug
    existe déjà, ses champs sont mis à jour (UPSERT) et un exemplaire de plus lui
    est rattaché — cohérent avec la règle « même nom = même titre ».

    Args:
        conn: connexion SQLite ouverte.
        nom: nom du jeu (obligatoire).
        **champs: colonnes optionnelles de `titres` (categorie, nb_joueurs_min,
            nb_joueurs_max, duree_min, age_min, editeur, auteur, annee_edition,
            descriptif). Les clés inconnues sont ignorées.

    Returns:
        dict {reference_titre, id_exemplaire} de l'élément créé.

    Raises:
        ValueError: si le nom est vide.
    """
    nom = (nom or "").strip()
    if not nom:
        raise ValueError("Le nom du jeu est obligatoire.")
    ref = slug_titre(nom)

    colonnes_ok = ("type_jeu", "categorie", "nb_joueurs_min", "nb_joueurs_max",
                   "duree_min", "age_min", "editeur", "auteur", "annee_edition",
                   "descriptif")
    valeurs = {c: champs.get(c) for c in colonnes_ok}

    conn.execute(
        """
        INSERT INTO titres (reference_titre, nom, type_jeu, categorie,
            nb_joueurs_min, nb_joueurs_max, duree_min, age_min, editeur, auteur,
            annee_edition, descriptif)
        VALUES (:ref, :nom, :type_jeu, :categorie, :nb_joueurs_min,
            :nb_joueurs_max, :duree_min, :age_min, :editeur, :auteur,
            :annee_edition, :descriptif)
        ON CONFLICT(reference_titre) DO UPDATE SET
            nom=excluded.nom, type_jeu=excluded.type_jeu,
            categorie=excluded.categorie, nb_joueurs_min=excluded.nb_joueurs_min,
            nb_joueurs_max=excluded.nb_joueurs_max, duree_min=excluded.duree_min,
            age_min=excluded.age_min, editeur=excluded.editeur,
            auteur=excluded.auteur, annee_edition=excluded.annee_edition,
            descriptif=excluded.descriptif
        """,
        {"ref": ref, "nom": nom, **valeurs},
    )
    id_ex = prochain_id_exemplaire(conn)
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        (id_ex, ref),
    )
    conn.commit()
    return {"reference_titre": ref, "id_exemplaire": id_ex}


def ajouter_exemplaire(conn: sqlite3.Connection, reference_titre: str) -> str:
    """
    Ajoute un exemplaire (id AUTO) à un titre existant.

    Args:
        conn: connexion SQLite ouverte.
        reference_titre: titre auquel rattacher la nouvelle boîte.

    Returns:
        L'id_exemplaire créé.

    Raises:
        ValueError: si le titre n'existe pas.
    """
    if get_titre(conn, reference_titre) is None:
        raise ValueError(f"Titre inconnu : {reference_titre}")
    id_ex = prochain_id_exemplaire(conn)
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        (id_ex, reference_titre),
    )
    conn.commit()
    return id_ex


def lister_exemplaires_du_titre(conn: sqlite3.Connection,
                                reference_titre: str) -> list[dict]:
    """
    Liste les exemplaires d'un titre avec leur état (pour la fiche admin).

    Inclut les deux colonnes de rangement BRUTES (docs/conception-rangement.md
    §4.c) : `emplacement_evenement` (texte, tel quel) et `emplacement_local_id`
    + son nom résolu `emplacement_local_nom` (via jointure, qui fonctionne même
    si l'emplacement a été archivé depuis — la fiche admin doit continuer à
    l'afficher, §5). `emplacement_local_actif` distingue ce cas.

    Returns:
        Liste de dicts {id_exemplaire, sorti(bool), emplacement_evenement,
        emplacement_local_id, emplacement_local_nom, emplacement_local_actif},
        triée par id.
    """
    rows = conn.execute(
        "SELECT x.id_exemplaire, x.emplacement_evenement, x.emplacement_local_id, "
        "       er.nom AS emplacement_local_nom, er.actif AS emplacement_local_actif "
        "FROM exemplaires x "
        "LEFT JOIN emplacements_rangement er ON er.id_emplacement = x.emplacement_local_id "
        "WHERE x.reference_titre = ? "
        "ORDER BY x.id_exemplaire",
        (reference_titre,),
    ).fetchall()
    return [
        {
            "id_exemplaire": r["id_exemplaire"],
            "sorti": est_sorti(conn, r["id_exemplaire"]),
            "emplacement_evenement": r["emplacement_evenement"],
            "emplacement_local_id": r["emplacement_local_id"],
            "emplacement_local_nom": r["emplacement_local_nom"],
            "emplacement_local_actif": (
                bool(r["emplacement_local_actif"])
                if r["emplacement_local_actif"] is not None else None
            ),
        }
        for r in rows
    ]


def titres_pour_etiquettes(conn: sqlite3.Connection,
                           categorie: str | None = None) -> list[dict]:
    """
    Liste les titres (avec leur nombre d'exemplaires) pour l'écran de sélection
    d'impression d'étiquettes en lot. Filtrable par catégorie.

    Returns:
        Liste de dicts {reference_titre, nom, categorie, nb_exemplaires}, triée
        par nom.
    """
    sql = """
        SELECT t.reference_titre, t.nom, t.categorie,
               COUNT(e.id_exemplaire) AS nb_exemplaires
        FROM titres t
        JOIN exemplaires e ON e.reference_titre = t.reference_titre
    """
    params: list = []
    if categorie:
        sql += " WHERE t.categorie = ?"
        params.append(categorie)
    sql += (" GROUP BY t.reference_titre, t.nom, t.categorie "
            "ORDER BY t.nom COLLATE NOCASE")
    return [dict(r) for r in conn.execute(sql, params)]


def exemplaires_pour_etiquettes(conn: sqlite3.Connection,
                                references: list[str] | None) -> list[dict]:
    """
    Renvoie les exemplaires (avec les champs utiles à l'étiquette) des titres
    demandés, pour générer une planche d'étiquettes.

    Args:
        conn: connexion SQLite ouverte.
        references: liste de `reference_titre` à inclure ; None/[] = tout le
            catalogue.

    Returns:
        Liste de dicts {id_exemplaire, nom, categorie, age_min, nb_joueurs_min,
        nb_joueurs_max, duree_min}, triée par nom puis id (étiquettes d'un même
        jeu groupées).
    """
    sql = """
        SELECT e.id_exemplaire, t.nom, t.categorie, t.age_min,
               t.nb_joueurs_min, t.nb_joueurs_max, t.duree_min
        FROM exemplaires e
        JOIN titres t ON t.reference_titre = e.reference_titre
    """
    params: list = []
    if references:
        marques = ",".join("?" * len(references))
        sql += f" WHERE e.reference_titre IN ({marques})"
        params = list(references)
    sql += " ORDER BY t.nom COLLATE NOCASE, e.id_exemplaire"
    return [dict(r) for r in conn.execute(sql, params)]


# ===========================================================================
# Paramètres applicatifs génériques (table parametres, clé/valeur)
# ===========================================================================
def lire_parametre(conn: sqlite3.Connection, cle: str,
                   defaut: str | None = None) -> str | None:
    """Lit un réglage de la table `parametres` (ou `defaut` s'il est absent/vide)."""
    row = conn.execute(
        "SELECT valeur FROM parametres WHERE cle = ?", (cle,)
    ).fetchone()
    return row[0] if row and row[0] else defaut


def ecrire_parametre(conn: sqlite3.Connection, cle: str, valeur: str | None) -> None:
    """Écrit (ou remplace) un réglage dans la table `parametres`."""
    conn.execute(
        "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
        "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (cle, valeur),
    )
    conn.commit()


# ===========================================================================
# Étiquettes imprimées — affichage du CODE DE LA BOÎTE
# ===========================================================================
# Le code de la boîte (`id_exemplaire`, celui que le QR encode) s'imprime
# désormais sur l'étiquette : c'est lui que réclame la saisie manuelle de
# secours quand le QR ne se lit pas, et il n'était jusque-là écrit nulle part
# où un bénévole puisse le lire.
#
# ⚠️ LE RÉGLAGE EST STOCKÉ, PAS PORTÉ PAR LE FORMULAIRE D'EXPORT. Trois
# producteurs doivent rendre EXACTEMENT la même étiquette — la planche en lot
# (POST /admin/etiquettes/pdf), la réimpression d'une étiquette abîmée
# (GET /admin/etiquette/<id>.png) et `scripts/generate_qr.py` — et les deux
# derniers n'ont pas de formulaire. Une case portée par le seul export ferait
# sortir les réimpressions sans code, six mois plus tard, en silence.
#
# Défaut : le code EST imprimé, y compris sur une base qui n'a jamais vu la
# clé. C'est un écart ASSUMÉ avec la règle « une base d'avant se comporte
# exactement comme avant » : il est cosmétique (il ne change rien à ce que le
# QR encode, ni à aucune donnée), et l'inverse aurait livré la fonctionnalité
# éteinte à tout le monde.
CLE_ETIQUETTE_CODE = "etiquette_code"


def lire_etiquette_code(conn: sqlite3.Connection) -> bool:
    """
    True si le code de la boîte doit être imprimé sur les étiquettes.

    Même patron booléen que les panneaux de l'écran de salle : seule la valeur
    « 0 » éteint le réglage, tout le reste (y compris une clé absente) laisse
    le code affiché.
    """
    return lire_parametre(conn, CLE_ETIQUETTE_CODE, "1") != "0"


def ecrire_etiquette_code(conn: sqlite3.Connection, afficher: bool) -> None:
    """Enregistre le réglage d'affichage du code de la boîte."""
    ecrire_parametre(conn, CLE_ETIQUETTE_CODE, "1" if afficher else "0")


# ===========================================================================
# Identité de l'ASSOCIATION, réglée depuis /admin/identite
# ===========================================================================
# Le nom de l'association (bandeau, pied de page, « À propos », exports, .ics,
# message de partage du jeton…) était une constante de `app/config.py` lue dans
# la variable d'environnement NOM_ASSOCIATION, donc figée au démarrage. Pour un
# bureau non technicien, corriger une faute de frappe supposait d'éditer un
# fichier sur le serveur et de redémarrer le service. C'est une donnée
# ÉDITORIALE : elle vit désormais en base, comme le nom de l'événement, et se
# règle depuis un écran d'administration.
#
# Même domicile et même patron de lecture que l'identité de l'événement
# ci-dessous : le couple `lire_nom_association(conn)` / `nom_association()`.
# Le projet a déjà payé le motif « lire_parametre recopié partout » ailleurs
# (double domicile des index uniques, duplication de la logique d'expiration
# d'annonce).
#
# DEUX DIFFÉRENCES avec le nom de l'événement, et elles sont structurantes :
#
# 1. `nom_association()` ne renvoie JAMAIS None. Le nom apparaît dans le
#    <title> de toutes les pages : il faut toujours quelque chose à afficher.
#    D'où la cascade base -> NOM_ASSOCIATION (.env, repli de second rang, pour
#    les déploiements qui l'ont déjà réglé là) -> "LudoteX".
# 2. `nom_association()` ne LÈVE JAMAIS. Elle est appelée à chaque rendu de
#    page, y compris celui de la page d'erreur 500 (`erreur.html` est un
#    gabarit comme les autres) : une exception ici transformerait une panne en
#    boucle d'erreur. Toute lecture qui échoue (base absente, verrouillée,
#    schéma pas encore créé) retombe silencieusement sur le repli.
CLE_ASSOCIATION_NOM = "asso_nom"

# Longueur maximale du nom. Même borne que le nom de l'événement : c'est un
# titre, il doit tenir sur une ligne dans un bandeau et dans un onglet.
LONGUEUR_NOM_ASSOCIATION = 80

# Le dernier repli de la cascade (« LudoteX ») n'est PAS redéfini ici : c'est la
# valeur par défaut de `NOM_ASSOCIATION` dans app/config.py, et elle n'a qu'un
# domicile. Le `from app.config import NOM_ASSOCIATION` est fait DANS les deux
# fonctions ci-dessous plutôt qu'en tête de module : la valeur est ainsi relue à
# chaque appel, et aucun cycle d'import ne peut naître de ce sens de dépendance.


def lire_nom_association(conn: sqlite3.Connection) -> str:
    """
    Nom de l'association, pour les appelants qui ont déjà une connexion de PRÊT.

    Cascade : valeur en base -> variable d'environnement NOM_ASSOCIATION ->
    "LudoteX". Ne renvoie jamais None ni une chaîne vide.
    """
    from app.config import NOM_ASSOCIATION

    return lire_parametre(conn, CLE_ASSOCIATION_NOM) or NOM_ASSOCIATION


def nom_association() -> str:
    """
    Même valeur que `lire_nom_association`, pour les appelants qui n'ont PAS de
    connexion de prêt en main : le context processor des gabarits (voir
    app/templating.py), les exports, et les routes des modules tournois /
    programme / planning, qui travaillent sur une autre base.

    Même exception délibérée à la convention `conn` en paramètre que
    `nom_evenement` — et même mise en garde : ne PAS l'appeler depuis un service
    qui reçoit déjà une connexion, ni depuis un service d'un autre module
    (l'indépendance des trois bases est un invariant du projet ; c'est la ROUTE
    qui lit la valeur et la transmet en paramètre).

    NE LÈVE JAMAIS : appelée à chaque rendu de page, y compris celui de la page
    d'erreur 500. Toute erreur de lecture retombe sur le repli.
    """
    from app.config import NOM_ASSOCIATION

    try:
        from app.db import get_connection

        conn = get_connection()
        try:
            return lire_nom_association(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - repli volontairement total, voir docstring
        return NOM_ASSOCIATION


# ---------------------------------------------------------------------------
# Les trois autres réglages d'identité : présentation, contact, URL du dépôt
# ---------------------------------------------------------------------------
# Mêmes clés, même domicile et même patron de lecture que le nom ci-dessus.
# Ils ne servent qu'à la page « À propos » (route `/apropos`), qui les lit sur
# UNE connexion : ils n'ont donc pas de variante sans `conn`, et surtout ils ne
# passent PAS par le context processor d'app/templating.py — tout ce qui y
# passe est injecté dans le rendu de TOUTES les pages, page d'erreur 500
# comprise, pour trois valeurs utiles à une seule.
#
# TROIS COMPORTEMENTS DIFFÉRENTS EN L'ABSENCE DE VALEUR, et c'est délibéré :
#
# - `presentation` et `contact` peuvent valoir None, comme `nom_evenement` et
#   non comme `nom_association` : rien à afficher signifie qu'on n'affiche
#   rien, section et titre compris (règle « ne jamais afficher une valeur
#   absente », déjà appliquée au rangement et à l'annonce de l'écran de salle).
#   Aucun repli n'aurait de sens : la mission d'une association et son adresse
#   de contact ne se devinent pas.
# - `depot_url` ne vaut JAMAIS None. La GPL veut qu'un utilisateur puisse
#   atteindre la source de la version qu'il fait tourner : ce lien doit
#   toujours mener quelque part. Cascade base -> DEPOT_URL (.env) -> littéral
#   d'app/config.py, seul domicile de ce dernier repli.
CLE_ASSOCIATION_PRESENTATION = "asso_presentation"
CLE_ASSOCIATION_CONTACT = "asso_contact"
CLE_ASSOCIATION_DEPOT = "asso_depot_url"

# Bornes de saisie. La présentation est un paragraphe de présentation, pas une
# page : de quoi écrire une quinzaine de lignes. Le contact est une adresse
# e-mail (255 caractères au maximum selon la RFC, mais une adresse réelle
# d'association en fait bien moins). L'URL d'un dépôt tient très largement en
# 200 caractères.
LONGUEUR_PRESENTATION_ASSOCIATION = 1500
LONGUEUR_CONTACT_ASSOCIATION = 120
LONGUEUR_DEPOT_ASSOCIATION = 200


def lire_presentation_association(conn: sqlite3.Connection) -> str | None:
    """
    Texte de présentation de l'association, ou None s'il n'a jamais été saisi.

    None (et non un texte de repli) : la page « À propos » masque alors la
    section entière, titre compris.
    """
    return lire_parametre(conn, CLE_ASSOCIATION_PRESENTATION)


def lire_contact_association(conn: sqlite3.Connection) -> str | None:
    """
    Adresse de contact de l'association, ou None si elle n'a jamais été saisie.

    Même règle que la présentation : absente, la section « Contact » de la page
    « À propos » disparaît en entier.
    """
    return lire_parametre(conn, CLE_ASSOCIATION_CONTACT)


def lire_depot_url(conn: sqlite3.Connection) -> str:
    """
    URL du dépôt du code source. Ne renvoie jamais None ni une chaîne vide.

    Cascade : valeur en base -> variable d'environnement DEPOT_URL -> l'URL du
    dépôt d'origine (`app.config.DEPOT_URL`, seul domicile de ce littéral).

    Import fait DANS la fonction, comme pour `lire_nom_association` : la valeur
    est relue à chaque appel (un test qui la remplace est donc pris en compte)
    et aucun cycle d'import ne peut naître de ce sens de dépendance.
    """
    from app.config import DEPOT_URL

    return lire_parametre(conn, CLE_ASSOCIATION_DEPOT) or DEPOT_URL


def paragraphes(texte: str | None) -> list[str]:
    """
    Découpe un texte saisi en administration en blocs de paragraphes.

    Le gabarit émet un `<p>` par bloc et Jinja en ÉCHAPPE le contenu : c'est ce
    qui permet d'offrir un texte multi-ligne sans jamais accepter de HTML. Pas
    de `|safe`, pas de markdown, aucune balise interprétée — un `<b>` saisi ici
    s'affiche tel quel, comme du texte.

    Séparateur : la ligne vide, convention universelle du texte brut. Les sauts
    de ligne simples sont conservés à l'intérieur d'un bloc (le gabarit ne les
    rend pas visibles, mais ils ne cassent rien) ; les blocs vides sont ignorés,
    si bien que trois lignes vides d'affilée ne produisent pas de paragraphe
    fantôme. Texte absent ou entièrement blanc -> liste vide, ce que l'appelant
    interprète comme « rien à afficher ».
    """
    if not texte:
        return []
    blocs = re.split(r"\n\s*\n", texte.replace("\r\n", "\n").replace("\r", "\n"))
    return [bloc.strip() for bloc in blocs if bloc.strip()]


def normaliser_contact_association(saisie: str) -> str | None:
    """
    Normalise une adresse de contact, ou None si elle ne ressemble pas à une
    adresse e-mail.

    POURQUOI UN CONTRÔLE ICI. Cette valeur atterrit dans un `mailto:` de la
    page « À propos ». On ne cherche pas à valider une adresse au sens de la
    RFC 5322 — c'est un puits sans fond, et une adresse syntaxiquement parfaite
    peut très bien ne pas exister : on refuse ce qui ne PEUT PAS être une
    adresse, pour qu'un bureau qui se trompe de champ le voie tout de suite
    plutôt que de publier un lien mort.

    Normalisation : espaces de bord retirés, et refus s'il reste un espace ou un
    saut de ligne À L'INTÉRIEUR (une adresse n'en contient pas, et un blanc dans
    un attribut d'URL est exactement ce qui sert à en sortir). Exigences :
    exactement un `@`, quelque chose de part et d'autre, et un point dans la
    partie domaine. La casse est laissée telle quelle : la partie locale d'une
    adresse est, formellement, sensible à la casse.

    Renvoie None pour une saisie vide comme pour une saisie invalide : c'est
    l'APPELANT qui distingue les deux (une saisie vide veut dire « efface »,
    voir la route POST /admin/identite).
    """
    valeur = saisie.strip()
    if not valeur:
        return None
    if any(c.isspace() for c in valeur):
        return None
    if valeur.count("@") != 1:
        return None
    locale, _, domaine = valeur.partition("@")
    if not locale or not domaine or "." not in domaine:
        return None
    if domaine.startswith(".") or domaine.endswith("."):
        return None
    return valeur[:LONGUEUR_CONTACT_ASSOCIATION]


def normaliser_depot_url(saisie: str) -> str | None:
    """
    Normalise l'URL du dépôt, ou None si son schéma n'est pas `http://` ou
    `https://`.

    POURQUOI CE CONTRÔLE EST UNE MESURE DE SÉCURITÉ, et pas une coquetterie :
    cette valeur atterrit telle quelle dans un `href`. Sans ce filtre, un
    `javascript:...` saisi depuis l'administration deviendrait un script
    exécuté chez chaque visiteur de la page « À propos » — Jinja échappe le
    CONTENU d'un attribut, il ne juge pas du schéma d'une URL. Les autres
    schémas dangereux (`data:`, `vbscript:`) tombent par la même règle : on
    n'énumère pas ce qui est interdit, on n'autorise que deux schémas.

    Renvoie None pour une saisie vide comme pour un schéma refusé : c'est
    l'APPELANT qui distingue les deux (une saisie vide veut dire « efface,
    reprends le repli »).
    """
    valeur = saisie.strip()
    if not valeur:
        return None
    if any(c.isspace() for c in valeur):
        return None
    if not re.match(r"^https?://\S", valeur, re.IGNORECASE):
        return None
    return valeur[:LONGUEUR_DEPOT_ASSOCIATION]


def lien_journal_versions(depot_url: str) -> str | None:
    """
    Lien vers le CHANGELOG du dépôt, ou None si l'URL n'est pas un dépôt GitHub.

    `<dépôt>/blob/main/CHANGELOG.md` est une forme d'URL propre à GitHub. Tant
    que l'adresse du dépôt était en dur, la construire était sans risque ; elle
    est désormais réglable et peut désigner une autre forge (GitLab utilise
    `/-/blob/`, Gitea `/src/branch/`, un dépôt auto-hébergé fait ce qu'il veut),
    où ce lien mènerait à une page d'erreur SANS que personne s'en aperçoive.

    Retenu : n'afficher ce lien que si l'on sait le construire, c'est-à-dire
    sur github.com, et l'omettre sinon. Le lien vers le dépôt lui-même, lui,
    reste toujours affiché — c'est celui qu'exige la GPL, et le journal des
    versions se trouve depuis la page d'accueil du dépôt. Les alternatives
    écartées sont discutées dans interne/comptes-rendus/lot2-apropos.md.
    """
    from urllib.parse import urlparse

    try:
        hote = (urlparse(depot_url).hostname or "").lower()
    except ValueError:  # URL non analysable (IPv6 malformée, port illégal…)
        return None
    if hote not in ("github.com", "www.github.com"):
        return None
    return f"{depot_url.rstrip('/')}/blob/main/CHANGELOG.md"


# ---------------------------------------------------------------------------
# La couleur de thème
# ---------------------------------------------------------------------------
# Même domicile et même patron que les réglages ci-dessus, avec une différence
# de nature : ce n'est pas un texte que l'on affiche, c'est une valeur qui
# entre dans la FEUILLE DE STYLE de toutes les pages. Trois conséquences, qui
# expliquent la forme de ce qui suit.
#
# 1. UNE SEULE VALEUR EST SAISIE, LES CINQ AUTRES SONT CALCULÉES. Le thème
#    compte six variables CSS (voir le `:root` d'app/static/css/style.css).
#    Demander six couleurs à un bureau non technicien produirait tôt ou tard
#    un bandeau illisible. La dérivation passe par la teinte et la saturation
#    (HSL), CONSERVÉES, et impose la seule luminosité : c'est ce qui garde la
#    couleur choisie reconnaissable dans ses nuances. Le mélange naïf vers le
#    blanc, lui, est à proscrire — il vire au gris et perd la teinte.
# 2. LA COULEUR DU TEXTE N'EST JAMAIS UN CHOIX. Noir ou blanc, selon celui qui
#    contraste le mieux avec la couleur choisie au sens WCAG. C'est ce qui
#    garantit qu'AUCUNE saisie ne peut rendre le bandeau illisible : le pire
#    cas possible de cette règle est 4,58:1, au-dessus du minimum de 4,5:1.
# 3. LE CALCUL EST FAIT ICI, EN PYTHON, PAS EN CSS. `color-mix()` et les
#    fonctions de couleur récentes feraient le même travail en une ligne, mais
#    seulement sur un navigateur récent : sur le téléphone d'un bénévole qui ne
#    l'est pas, le bandeau perdrait ses nuances sans que personne le sache. Le
#    serveur envoie six valeurs hexadécimales, comprises partout.
CLE_ASSOCIATION_COULEUR = "asso_couleur"

# Le thème par défaut. SEUL DOMICILE de ce littéral côté Python — la valeur est
# aussi écrite dans le `:root` d'app/static/css/style.css, qui fait foi tant
# qu'aucune couleur n'est réglée (rien n'est alors injecté, et le fichier reste
# en cache). Cette duplication est inévitable — une feuille de style statique ne
# peut pas lire la base — et elle est VERROUILLÉE PAR UN TEST
# (tests/test_theme.py) qui compare les deux valeurs.
COULEUR_ASSOCIATION_DEFAUT = "#2a2724"

# Forme acceptée : exactement `#rrggbb`. Volontairement stricte — ni `#rgb`, ni
# `rgb(...)`, ni un nom de couleur CSS : cette valeur est écrite dans un
# `<style>` en ligne, et une forme libre y serait une porte d'entrée. Le refus
# est visible (message clair, rien d'enregistré), jamais silencieux.
_MOTIF_COULEUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def normaliser_couleur_association(saisie: str) -> str | None:
    """
    Normalise une couleur saisie, ou None si ce n'est pas un `#rrggbb`.

    Renvoie None pour une saisie vide comme pour une saisie refusée : c'est
    l'APPELANT qui distingue les deux (une saisie vide veut dire « efface,
    reprends le thème par défaut »), exactement comme pour le contact et l'URL
    du dépôt ci-dessus.

    La casse est ramenée en minuscules : `#AABBCC` et `#aabbcc` sont la même
    couleur, et deux écritures d'une même valeur produiraient sinon une ligne
    de journal annonçant une modification qui n'en est pas une.
    """
    valeur = saisie.strip()
    if not valeur or not _MOTIF_COULEUR.match(valeur):
        return None
    return valeur.lower()


def lire_couleur_association(conn: sqlite3.Connection) -> str | None:
    """
    Couleur de thème réglée, ou None si aucune ne l'est.

    None, et non `COULEUR_ASSOCIATION_DEFAUT` : « aucune couleur réglée » doit
    rester distinguable de « la couleur par défaut a été saisie », parce que
    c'est ce qui décide si l'on injecte quelque chose dans les pages ou non.

    La valeur relue en base repasse par `normaliser_couleur_association`. Ce
    n'est pas de la méfiance envers la route qui l'a écrite : c'est le point de
    passage obligé vers un `<style>`, et il ne doit y avoir AUCUN chemin — une
    restauration de sauvegarde, un import, une écriture directe dans le
    fichier SQLite — par lequel autre chose qu'un `#rrggbb` en ressorte.
    """
    return normaliser_couleur_association(
        lire_parametre(conn, CLE_ASSOCIATION_COULEUR) or "")


def _luminance_relative(couleur: str) -> float:
    """Luminance relative WCAG d'un `#rrggbb` (0 = noir, 1 = blanc)."""
    canaux = [int(couleur[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lineaires = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                 for c in canaux]
    return (0.2126 * lineaires[0] + 0.7152 * lineaires[1] + 0.0722 * lineaires[2])


def contraste(couleur_a: str, couleur_b: str) -> float:
    """
    Rapport de contraste WCAG entre deux `#rrggbb` (de 1:1 à 21:1).

    Sert à choisir la couleur du texte du bandeau, et à le VÉRIFIER dans les
    tests : le seuil de 4,5:1 est une promesse faite au guide administrateur
    (« aucune couleur choisie ne peut rendre le bandeau illisible »), elle doit
    être contrôlable ailleurs que dans un commentaire.
    """
    a, b = _luminance_relative(couleur_a), _luminance_relative(couleur_b)
    clair, sombre = max(a, b), min(a, b)
    return (clair + 0.05) / (sombre + 0.05)


def couleur_texte_sur(fond: str) -> str:
    """
    Noir ou blanc, celui des deux qui contraste le mieux avec `fond`.

    JAMAIS un choix de l'utilisateur : c'est la seule façon de garantir que le
    bandeau reste lisible quelle que soit la couleur réglée. Le pire cas de
    cette règle est le point où les deux contrastes s'égalent (luminance
    relative ≈ 0,179), et il vaut encore 4,58:1 — au-dessus du minimum WCAG AA
    de 4,5:1. Autrement dit : aucune saisie ne peut faire descendre le bandeau
    sous le seuil.
    """
    if contraste(fond, "#ffffff") >= contraste(fond, "#000000"):
        return "#ffffff"
    return "#000000"


def nuances_theme(couleur: str) -> dict[str, str]:
    """
    Les six valeurs du thème, dérivées de la seule couleur saisie.

    Teinte et saturation CONSERVÉES, luminosité imposée (voir l'en-tête de
    section) :

    - `survol`     : luminosité + 12 points, ou − 12 points si la couleur est
      déjà claire (au-dessus de 50 %). L'écart compte plus que son sens : le
      bouton primaire s'ÉCLAIRCIT au survol, ce qui se voit sur une couleur
      sombre là où un assombrissement ne se verrait pas — mais sur une couleur
      très claire, ajouter 12 points donnerait du blanc pur, et le bouton
      disparaîtrait au survol sur une page blanche. Le sens s'inverse alors ;
    - `clair`      : luminosité 62 %, saturation + 10 — usage DÉCORATIF
      seulement (barres de l'histogramme, bordure de survol d'une carte) : à
      cette luminosité, le contraste sur blanc tourne autour de 2,5:1, en
      dessous des 3:1 d'un élément porteur d'information ;
    - `fond`       : luminosité 94 %, saturation + 25 — aplats teintés (puces
      de filtre) ;
    - `fond_leger` : luminosité 97 %, même saturation — aplats les plus pâles.

    Le supplément de saturation des trois dernières n'est pas une coquetterie :
    sans lui, une couleur très désaturée donne des aplats parfaitement gris,
    et le thème ne se voit plus nulle part ailleurs que sur le bandeau.
    """
    import colorsys

    rouge, vert, bleu = (int(couleur[i:i + 2], 16) / 255 for i in (1, 3, 5))
    teinte, lum, sat = colorsys.rgb_to_hls(rouge, vert, bleu)

    def compose(luminosite: float, supplement_sat: float = 0.0) -> str:
        r, v, b = colorsys.hls_to_rgb(
            teinte, min(1.0, luminosite), min(1.0, sat + supplement_sat))
        return "#%02x%02x%02x" % (round(r * 255), round(v * 255), round(b * 255))

    return {
        "primaire": couleur,
        "survol": compose(lum + 0.12 if lum <= 0.5 else lum - 0.12),
        "clair": compose(0.62, 0.10),
        "fond": compose(0.94, 0.25),
        "fond_leger": compose(0.97, 0.25),
        "texte": couleur_texte_sur(couleur),
    }


def theme_association() -> dict:
    """
    Ce qu'il faut au gabarit de base pour habiller TOUTES les pages :

    - `couleur` : la couleur effective, toujours un `#rrggbb` — celle qui part
      dans le `<meta name="theme-color">`, qui ne peut pas porter une variable
      CSS et a donc besoin d'une valeur ;
    - `style`   : le contenu du `<style>` en ligne qui redéfinit les six
      variables sur `:root`, ou **None** si aucune couleur n'est réglée. None
      et non le thème par défaut : dans ce cas le `:root` d'app/static/css/
      style.css fait foi, et n'injecter RIEN évite d'alourdir chaque page d'une
      redéfinition qui ne changerait rien.

    Sans connexion en paramètre, comme `nom_association` et pour la même raison
    (seule la requête est disponible au rendu d'un gabarit), et avec la même
    mise en garde : ne PAS l'appeler depuis un service qui reçoit déjà une
    connexion, ni depuis un service d'un autre module.

    NE LÈVE JAMAIS : elle est appelée à chaque rendu de page, y compris celui
    de la page d'erreur 500, où la base peut précisément être en cause. Toute
    lecture qui échoue retombe sur le thème par défaut, donc sur « rien à
    injecter » — une page sans couleur d'association vaut mieux qu'une page
    d'erreur qui ne s'affiche pas.
    """
    couleur = None
    try:
        from app.db import get_connection

        conn = get_connection()
        try:
            couleur = lire_couleur_association(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - repli volontairement total, voir docstring
        couleur = None

    if couleur is None:
        return {"couleur": COULEUR_ASSOCIATION_DEFAUT, "style": None}

    n = nuances_theme(couleur)
    return {
        "couleur": couleur,
        "style": (
            ":root{"
            f"--primaire:{n['primaire']};"
            f"--primaire-survol:{n['survol']};"
            f"--primaire-clair:{n['clair']};"
            f"--primaire-fond:{n['fond']};"
            f"--primaire-fond-leger:{n['fond_leger']};"
            f"--primaire-texte:{n['texte']}"
            "}"
        ),
    }


# ===========================================================================
# Identité de l'événement (nom + date), réglée depuis /admin/evenement
# ===========================================================================
# Deux réglages, deux clés voisines dans `parametres` (base de PRÊT) :
# - `evenement_date` : premier jour, borne de la frise et de la grille du
#   programme (lue directement par ses appelants historiques) ;
# - `evenement_nom`  : le nom de l'édition (« Festival du Jeu 2026 »), rappelé
#   sur les surfaces publiques.
#
# UN SEUL DOMICILE POUR LA LECTURE DU NOM. Il est lu depuis les gabarits (global
# Jinja), depuis l'écran de salle, depuis le module programme et depuis les deux
# routes `.ics` : quatre appelants, dont deux n'ont pas de connexion de prêt en
# main. D'où le couple ci-dessous plutôt qu'un `lire_parametre(conn,
# "evenement_nom")` recopié partout — le projet a déjà payé ce motif ailleurs
# (double domicile des index uniques, duplication de la logique d'expiration
# d'annonce).
#
# ⚠️ Cette clé est l'AMORCE de la future table `editions` (fiche 6.4 de
# docs/idees-evolutions.md, « prérequis structurant n°1 ») : le jour où cette
# table existera, c'est ce couple de fonctions qu'il faudra faire pointer
# ailleurs, pas une dizaine d'appels dispersés.
CLE_EVENEMENT_NOM = "evenement_nom"

# Longueur maximale du nom. C'est aussi le titre affiché sur l'écran de salle
# (voir `live.titre_ecran`) : il doit rester lisible de loin sur une seule ligne.
LONGUEUR_NOM_EVENEMENT = 80


def lire_nom_evenement(conn: sqlite3.Connection) -> str | None:
    """
    Nom de l'événement, ou None s'il n'a jamais été renseigné.

    None (et non une chaîne vide ou un libellé de repli) : les appelants doivent
    pouvoir ne RIEN afficher du tout, conformément à la règle « ne jamais
    afficher une valeur absente » déjà appliquée au rangement et à l'annonce de
    l'écran de salle. Une base où la clé n'a jamais été écrite se comporte donc
    exactement comme avant l'introduction du réglage.
    """
    return lire_parametre(conn, CLE_EVENEMENT_NOM)


def nom_evenement() -> str | None:
    """
    Même valeur que `lire_nom_evenement`, pour les appelants qui n'ont PAS de
    connexion de prêt en main : le global Jinja (les gabarits ne disposent que
    de la requête) et les routes des modules tournois/programme, qui travaillent
    sur une autre base.

    Même exception délibérée à la convention `conn` en paramètre que
    `rangement_visible`/`rangement_actif`, et même parade : une connexion ouverte
    puis refermée ici. Ne PAS appeler depuis un service qui reçoit déjà une
    connexion — utiliser `lire_nom_evenement` (les services ne traversent jamais
    les bases : l'indépendance des trois bases est un invariant du projet).
    """
    from app.db import get_connection

    conn = get_connection()
    try:
        return lire_nom_evenement(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Qui peut inscrire à un tournoi (réglé depuis /admin/evenement)
# ---------------------------------------------------------------------------
# Troisième réglage de l'identité de l'événement, même domicile et même patron
# de lecture que le nom ci-dessus. Deux valeurs seulement :
#   absent / "visiteurs"  — n'importe qui s'inscrit en ligne (comportement
#                           historique, donc défaut : une base où la clé n'a
#                           jamais été écrite se comporte exactement comme
#                           avant) ;
#   "benevoles"           — l'inscription passe par un bénévole. La page d'un
#                           tournoi reste PUBLIQUE et complète (horaire, lieu,
#                           places, classement, ajout à l'agenda) : seul le
#                           bouton « S'inscrire » disparaît pour un visiteur.
#
# Le réglage est GLOBAL, et se combine avec la case « Inscription en ligne » de
# chaque tournoi : la case ferme les inscriptions d'un tournoi précis, ce
# réglage-ci décide de qui peut les prendre quand elles sont ouvertes.
#
# ⚠️ La valeur vit dans la base de PRÊT alors que les tournois ont la leur.
# C'est la ROUTE qui lit ce réglage et le transmet ; aucun service du module
# tournois n'ouvre la base de prêt (l'indépendance des trois bases est un
# invariant du projet, déjà tenue pour le nom d'événement dans les `.ics`).
CLE_INSCRIPTION_TOURNOI = "tournoi_inscription"
INSCRIPTION_TOURNOI_VALEURS = ("visiteurs", "benevoles")


def lire_inscription_tournoi(conn: sqlite3.Connection) -> str:
    """
    Qui peut s'inscrire en ligne à un tournoi : « visiteurs » ou « benevoles ».

    Toute valeur absente ou inattendue retombe sur « visiteurs » — le réglage
    ne doit jamais fermer les inscriptions par accident, et une base d'avant
    son introduction n'en porte aucune trace.
    """
    valeur = lire_parametre(conn, CLE_INSCRIPTION_TOURNOI)
    return valeur if valeur in INSCRIPTION_TOURNOI_VALEURS else "visiteurs"


def inscription_tournoi_reservee() -> bool:
    """
    True si les inscriptions aux tournois sont réservées aux bénévoles.

    Pendant de `nom_evenement` : ouvre et referme sa propre connexion, pour les
    appelants qui n'ont pas de connexion de PRÊT en main — ici les routes du
    module tournois. Même mise en garde : ne pas l'appeler depuis un service
    qui reçoit déjà une connexion.
    """
    from app.db import get_connection

    conn = get_connection()
    try:
        return lire_inscription_tournoi(conn) == "benevoles"
    finally:
        conn.close()


# ===========================================================================
# Rangement des boîtes (voir docs/conception-rangement.md)
# ===========================================================================
# Deux contextes (§2) : "evenement" (texte libre, colonne exemplaires.
# emplacement_evenement) et "local" (liste gérée ici, FK exemplaires.
# emplacement_local_id -> emplacements_rangement). Un seul réglage global
# ("rangement_contexte") détermine lequel les écrans lisent/écrivent.
# Sans effet sur la logique de prêt/pochettes : ces fonctions ne touchent
# jamais aux tables prets/pochettes.
RANGEMENT_CONTEXTES = ("evenement", "local")
RANGEMENT_CONTEXTE_DEFAUT = "evenement"

# Visibilité de l'emplacement sur le catalogue / la fiche publique (§7).
# L'écran de retour bénévole (derrière le jeton) n'est JAMAIS concerné par ce
# réglage : il affiche toujours l'emplacement.
RANGEMENT_VISIBILITES = ("tous", "benevoles", "admin")
RANGEMENT_VISIBILITE_DEFAUT = "benevoles"


def rangement_contexte(conn: sqlite3.Connection) -> str:
    """Contexte de rangement actif : "evenement" (défaut) ou "local"."""
    return lire_parametre(conn, "rangement_contexte", RANGEMENT_CONTEXTE_DEFAUT)


def ecrire_rangement_contexte(conn: sqlite3.Connection, contexte: str) -> None:
    if contexte not in RANGEMENT_CONTEXTES:
        raise ValueError(f"Contexte de rangement invalide : {contexte!r}")
    ecrire_parametre(conn, "rangement_contexte", contexte)


def rangement_visibilite(conn: sqlite3.Connection) -> str:
    """Visibilité publique de l'emplacement : "tous"/"benevoles" (défaut)/"admin"."""
    return lire_parametre(conn, "rangement_visibilite", RANGEMENT_VISIBILITE_DEFAUT)


def ecrire_rangement_visibilite(conn: sqlite3.Connection, visibilite: str) -> None:
    if visibilite not in RANGEMENT_VISIBILITES:
        raise ValueError(f"Visibilité de rangement invalide : {visibilite!r}")
    ecrire_parametre(conn, "rangement_visibilite", visibilite)


def lister_emplacements_rangement(conn: sqlite3.Connection) -> list[dict]:
    """
    Liste complète (actifs + archivés) des emplacements locaux, triée par
    ordre d'affichage puis nom, avec le nombre de boîtes qui pointent vers
    chacun (`usage_count`) — sert à décider si la suppression dure est
    proposée (§5).
    """
    rows = conn.execute(
        """
        SELECT e.id_emplacement, e.nom, e.actif, e.ordre,
               COUNT(x.id_exemplaire) AS usage_count
        FROM emplacements_rangement e
        LEFT JOIN exemplaires x ON x.emplacement_local_id = e.id_emplacement
        GROUP BY e.id_emplacement
        ORDER BY e.ordre, e.nom COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def emplacements_actifs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """
    Emplacements locaux ACTIFS, triés pour l'affichage dans un menu déroulant
    (mode rangement au scanner, fiche admin, page des manques — étapes
    suivantes). Les archivés en sont exclus (mais restent affichés là où ils
    sont déjà pointés, voir §5).
    """
    return conn.execute(
        "SELECT id_emplacement, nom FROM emplacements_rangement "
        "WHERE actif = 1 ORDER BY ordre, nom COLLATE NOCASE"
    ).fetchall()


def get_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int) -> sqlite3.Row | None:
    """Une ligne de `emplacements_rangement` (actif ou archivé), ou None."""
    return conn.execute(
        "SELECT * FROM emplacements_rangement WHERE id_emplacement = ?",
        (id_emplacement,),
    ).fetchone()


def creer_emplacement_rangement(conn: sqlite3.Connection, nom: str) -> int | None:
    """Ajoute un emplacement en fin de liste (ordre = max + 1). None si nom vide."""
    nom_normalise = " ".join(nom.split())
    if not nom_normalise:
        return None
    (max_ordre,) = conn.execute(
        "SELECT COALESCE(MAX(ordre), -1) FROM emplacements_rangement"
    ).fetchone()
    curseur = conn.execute(
        "INSERT INTO emplacements_rangement (nom, actif, ordre) VALUES (?, 1, ?)",
        (nom_normalise, max_ordre + 1),
    )
    conn.commit()
    return curseur.lastrowid


def obtenir_ou_creer_emplacement_rangement(
    conn: sqlite3.Connection, nom: str
) -> tuple[int, bool] | None:
    """
    Trouve un emplacement existant par nom (comparaison insensible à la
    casse/aux espaces, actif OU archivé) ou le crée. `(id, cree)` — `cree`
    indique si une nouvelle ligne a été ajoutée. None si `nom` est vide.

    Utilisé par l'écran admin (bouton « Ajouter », évite les doublons si on
    retape un nom déjà présent) et par l'import CSV (étape 7, §4.b : création
    tolérante d'un emplacement local inconnu, signalée dans le compte-rendu).
    """
    nom_normalise = " ".join(nom.split())
    if not nom_normalise:
        return None
    existant = conn.execute(
        "SELECT id_emplacement FROM emplacements_rangement "
        "WHERE TRIM(nom) = ? COLLATE NOCASE",
        (nom_normalise,),
    ).fetchone()
    if existant:
        return existant["id_emplacement"], False
    return creer_emplacement_rangement(conn, nom_normalise), True


def renommer_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int, nom: str) -> bool:
    """
    Renomme (répercuté automatiquement partout via la FK, §5). False si `nom`
    est vide (rien n'est modifié).
    """
    nom_normalise = " ".join(nom.split())
    if not nom_normalise:
        return False
    conn.execute(
        "UPDATE emplacements_rangement SET nom = ? WHERE id_emplacement = ?",
        (nom_normalise, id_emplacement),
    )
    conn.commit()
    return True


def archiver_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int) -> None:
    """
    Retrait doux (§5) : disparaît des menus de saisie, mais les boîtes qui le
    pointent gardent leur référence (affichée « archivé » côté admin).
    """
    conn.execute(
        "UPDATE emplacements_rangement SET actif = 0 WHERE id_emplacement = ?",
        (id_emplacement,),
    )
    conn.commit()


def reactiver_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int) -> None:
    """Annule un archivage : redevient proposé dans les menus de saisie."""
    conn.execute(
        "UPDATE emplacements_rangement SET actif = 1 WHERE id_emplacement = ?",
        (id_emplacement,),
    )
    conn.commit()


def compteur_usage_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int) -> int:
    """Nombre de boîtes qui pointent actuellement vers cet emplacement local."""
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM exemplaires WHERE emplacement_local_id = ?",
        (id_emplacement,),
    ).fetchone()
    return n


def supprimer_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int) -> bool:
    """
    Suppression DURE (§5) : refusée (False, rien n'est modifié) si au moins
    une boîte pointe encore vers cet emplacement — jamais de FK orpheline.
    """
    if compteur_usage_emplacement_rangement(conn, id_emplacement) > 0:
        return False
    conn.execute(
        "DELETE FROM emplacements_rangement WHERE id_emplacement = ?",
        (id_emplacement,),
    )
    conn.commit()
    return True


def deplacer_emplacement_rangement(conn: sqlite3.Connection, id_emplacement: int, sens: str) -> None:
    """
    Échange la position d'un emplacement avec son voisin immédiat dans la
    liste triée (`sens` = "haut" ou "bas"). Sans effet s'il est déjà en bout
    de liste ou si `id_emplacement` est inconnu. Travaille sur la POSITION
    dans la liste triée (pas la valeur brute d'`ordre`) : robuste même si deux
    lignes partagent le même `ordre`.
    """
    lignes = conn.execute(
        "SELECT id_emplacement, ordre FROM emplacements_rangement "
        "ORDER BY ordre, nom COLLATE NOCASE"
    ).fetchall()
    ids = [r["id_emplacement"] for r in lignes]
    if id_emplacement not in ids:
        return
    idx = ids.index(id_emplacement)
    voisin = idx - 1 if sens == "haut" else idx + 1
    if voisin < 0 or voisin >= len(ids):
        return
    a, b = lignes[idx], lignes[voisin]
    conn.execute(
        "UPDATE emplacements_rangement SET ordre = ? WHERE id_emplacement = ?",
        (b["ordre"], a["id_emplacement"]),
    )
    conn.execute(
        "UPDATE emplacements_rangement SET ordre = ? WHERE id_emplacement = ?",
        (a["ordre"], b["id_emplacement"]),
    )
    conn.commit()


def affecter_emplacement(
    conn: sqlite3.Connection, id_exemplaire: str, contexte: str, valeur
) -> dict | None:
    """
    Affecte l'emplacement ACTIF à une boîte — cœur du mode rangement au
    scanner (§4.a). `valeur` est le texte libre (contexte "evenement") ou
    l'`id_emplacement` (contexte "local", int).

    Permis quel que soit l'état de prêt de la boîte (une boîte sortie garde
    son étagère d'origine) : ne touche JAMAIS aux tables prets/pochettes.

    Returns:
        Les infos de la boîte (`info_exemplaire`, dont le nom du jeu — utile
        pour le message de confirmation), ou None si `id_exemplaire` est
        inconnu (rien n'est modifié, jamais bloquant).
    """
    info = info_exemplaire(conn, id_exemplaire)
    if info is None:
        return None
    colonne = "emplacement_local_id" if contexte == "local" else "emplacement_evenement"
    conn.execute(
        f"UPDATE exemplaires SET {colonne} = ? WHERE id_exemplaire = ?",
        (valeur, id_exemplaire),
    )
    conn.commit()
    return info


# Cookie d'appareil portant l'emplacement actif du mode rangement (§4.a de
# docs/conception-rangement.md). Défini ici plutôt que dans routes/scanner.py
# depuis que `base.html` doit lui aussi savoir si le mode est actif : le nom
# du cookie et sa résolution n'ont qu'un seul domicile.
COOKIE_RANGEMENT = "rangement_actif"


def etat_rangement(conn: sqlite3.Connection, request) -> dict:
    """
    Résout le cookie `rangement_actif` par rapport au CONTEXTE courant.

    Toujours résilient : une valeur devenue invalide (contexte changé,
    emplacement archivé ou supprimé entre-temps) est traitée comme « mode
    inactif », jamais comme une erreur.

    Args:
        conn: connexion SQLite ouverte.
        request: requête (pour lire le cookie).

    Returns:
        dict avec :
        - "actif": bool — un emplacement est-il sélectionné pour ce contexte ?
        - "contexte": "evenement" ou "local".
        - "label": libellé à afficher (nom d'emplacement ou texte libre), ou None.
        - "valeur": valeur à passer à `affecter_emplacement`, ou None.
    """
    contexte = rangement_contexte(conn)
    brut = request.cookies.get(COOKIE_RANGEMENT)
    inactif = {"actif": False, "contexte": contexte, "label": None, "valeur": None}
    if not brut:
        return inactif

    if contexte == "local":
        try:
            id_emplacement = int(brut)
        except ValueError:
            return inactif
        emplacement = get_emplacement_rangement(conn, id_emplacement)
        if emplacement is None:
            return inactif
        return {"actif": True, "contexte": contexte, "label": emplacement["nom"],
                "valeur": id_emplacement}

    # Contexte "evenement" : texte libre, la valeur du cookie EST le libellé.
    return {"actif": True, "contexte": contexte, "label": brut, "valeur": brut}


def rangement_actif(request) -> dict:
    """
    État du mode rangement pour l'appareil courant, à l'usage de `base.html`
    (bandeau global, fiche B1 de docs/audit-ux-2026-07-18.md).

    Le mode rangement est mémorisé dans un cookie de 12 h et change la
    signification du geste central : tant qu'il est posé, scanner un QR RANGE
    la boîte au lieu d'ouvrir l'écran de prêt. Il n'était visible que sur
    `/scanner` ; un bénévole qui rangeait le matin et revenait à l'ouverture
    pouvait croire qu'il prêtait. D'où ce bandeau sur toutes les pages.

    Même exception à la convention `conn` en paramètre que `rangement_visible`
    (global Jinja : seule la requête est disponible), et même parade — une
    connexion ouverte puis refermée ici.

    Returns:
        Le dict de `etat_rangement` ; "actif" est False pour un visiteur non
        bénévole, quel que soit son cookie (le mode n'a de sens que derrière
        le jeton, qui protège /scanner).
    """
    from app import auth
    from app.db import get_connection

    if not auth.peut_ecrire(request):
        return {"actif": False, "contexte": None, "label": None, "valeur": None}
    conn = get_connection()
    try:
        return etat_rangement(conn, request)
    finally:
        conn.close()


def rangement_visible(request) -> bool:
    """
    Le catalogue / la fiche publique doivent-ils afficher l'emplacement pour
    CE visiteur ? Trois niveaux (§7 de docs/conception-rangement.md), lus
    depuis `rangement_visibilite` : "tous", "benevoles" (défaut), "admin".

    Exception délibérée à la convention `conn` en paramètre (voir en-tête du
    module) : cette fonction est un GLOBAL JINJA (enregistré dans
    app/templating.py, appelé depuis les gabarits comme
    `rangement_visible(request)`) — seule la requête y est disponible, comme
    pour `auth.peut_ecrire`/`modules.module_visible`, qui ouvrent chacun leur
    propre connexion pour la même raison. Peut aussi être appelée directement
    depuis une route (voir routes/catalogue.py::fiche).

    L'écran de retour bénévole (/pret/<id>) n'est PAS concerné par ce réglage
    (voir `emplacement_actuel`) : déjà derrière le jeton, il affiche toujours
    l'emplacement.
    """
    from app import admin_auth, auth
    from app.db import get_connection

    conn = get_connection()
    try:
        niveau = rangement_visibilite(conn)
    finally:
        conn.close()
    if niveau == "tous":
        return True
    if niveau == "admin":
        return admin_auth.admin_connecte(request)
    return auth.peut_ecrire(request)  # "benevoles" (défaut)


def emplacement_actuel(conn: sqlite3.Connection, id_exemplaire: str) -> str | None:
    """
    Libellé d'affichage de l'emplacement de rangement ACTUEL d'une boîte,
    selon le contexte actif (§6/§9 de docs/conception-rangement.md) : texte
    libre en contexte "evenement", nom de l'emplacement local (même archivé —
    la boîte garde sa référence, §5) en contexte "local".

    Utilisé par l'écran de retour bénévole (/pret/<id>, résultats "rendu" et
    "rendu_tournoi") : None si rien n'est renseigné -> le gabarit n'affiche
    rien (jamais de "non renseigné" anxiogène, §6). Volontairement séparée de
    `info_exemplaire` (qui reste inchangée) pour ne pas faire fuiter les deux
    colonnes d'emplacement dans les gabarits publics qui réutilisent `info`.
    """
    contexte = rangement_contexte(conn)
    if contexte == "local":
        row = conn.execute(
            "SELECT er.nom FROM exemplaires x "
            "LEFT JOIN emplacements_rangement er ON er.id_emplacement = x.emplacement_local_id "
            "WHERE x.id_exemplaire = ?",
            (id_exemplaire,),
        ).fetchone()
        return row["nom"] if row and row["nom"] else None
    row = conn.execute(
        "SELECT emplacement_evenement FROM exemplaires WHERE id_exemplaire = ?",
        (id_exemplaire,),
    ).fetchone()
    return row["emplacement_evenement"] if row and row["emplacement_evenement"] else None


# ---------------------------------------------------------------------------
# Comptage des exemplaires SANS emplacement dans le contexte actif — sert au
# compteur de /admin/rangement, au message post-import de /admin/donnees, et
# (via `_clause_sans_emplacement`) à l'option « ne pas écraser » de
# l'affectation en lot (§13.4). La liste détaillée équivalente (grain
# exemplaire, ex-page des manques) a été remplacée par la vue « Ranger les
# jeux » au grain titre (§13, `rangement_par_titre` + la route
# /admin/rangement/ranger).
# ---------------------------------------------------------------------------
def _clause_sans_emplacement(contexte: str) -> str:
    """Fragment SQL (préfixe `x.`) : l'exemplaire n'a PAS d'emplacement dans ce contexte."""
    if contexte == "local":
        return "x.emplacement_local_id IS NULL"
    return "(x.emplacement_evenement IS NULL OR x.emplacement_evenement = '')"


def _filtre_manques(conn: sqlite3.Connection, categorie: str | None, q: str | None):
    """Construit (clause WHERE, params) partagée par compteur et liste des manques."""
    contexte = rangement_contexte(conn)
    conditions = [_clause_sans_emplacement(contexte)]
    params: list = []
    if categorie:
        conditions.append("t.categorie = ?")
        params.append(categorie)
    if q:
        conditions.append("t.nom LIKE ? COLLATE NOCASE")
        params.append(f"%{q}%")
    return " AND ".join(conditions), params


def compter_exemplaires_sans_emplacement(
    conn: sqlite3.Connection, categorie: str | None = None, q: str | None = None
) -> int:
    """Nombre d'exemplaires sans emplacement dans le contexte actif (filtres optionnels)."""
    where, params = _filtre_manques(conn, categorie, q)
    (n,) = conn.execute(
        f"SELECT COUNT(*) FROM exemplaires x "
        f"JOIN titres t ON t.reference_titre = x.reference_titre WHERE {where}",
        params,
    ).fetchone()
    return n


# ===========================================================================
# Affectation en lot par jeu (§13 de docs/conception-rangement.md, addendum
# post-phase 1) : la vue « Ranger les jeux » travaille au grain TITRE plutôt
# qu'exemplaire, pour équiper d'un coup toutes les boîtes d'un même jeu.
# ===========================================================================
def _resume_emplacement_titres(
    conn: sqlite3.Connection, reference_titres: list[str], contexte: str
) -> dict[str, dict]:
    """
    Pour chaque titre de `reference_titres`, résume l'état de ses boîtes dans
    le CONTEXTE donné : combien ont une valeur (`nb_avec`), combien de valeurs
    DISTINCTES parmi elles (`nb_distinct`), et la valeur elle-même si elle est
    unique (`valeur`, sinon None — non pertinente si `nb_distinct` != 1).

    En contexte "local", `valeur` est un `id_emplacement` ; ce helper résout
    aussi son libellé d'affichage (`libelle`, nom de l'emplacement même
    archivé, comme `emplacement_actuel`). En contexte "evenement", `valeur`
    EST déjà le libellé (texte libre).

    Returns:
        dict {reference_titre: {"nb_avec", "nb_distinct", "libelle"}}. Un
        titre absent du résultat (aucune boîte) est à traiter par l'appelant
        comme nb_avec=0.
    """
    if not reference_titres:
        return {}
    local = contexte == "local"
    valeur_expr = "x.emplacement_local_id" if local else "NULLIF(TRIM(x.emplacement_evenement), '')"
    placeholders = ",".join("?" * len(reference_titres))
    rows = conn.execute(
        f"""
        SELECT x.reference_titre,
               COUNT({valeur_expr}) AS nb_avec,
               COUNT(DISTINCT {valeur_expr}) AS nb_distinct,
               MIN({valeur_expr}) AS valeur
        FROM exemplaires x
        WHERE x.reference_titre IN ({placeholders})
        GROUP BY x.reference_titre
        """,
        reference_titres,
    ).fetchall()
    noms_locaux: dict[int, str] = {}
    if local:
        ids = {r["valeur"] for r in rows if r["nb_distinct"] == 1 and r["valeur"] is not None}
        if ids:
            id_placeholders = ",".join("?" * len(ids))
            noms_locaux = {
                r2["id_emplacement"]: r2["nom"]
                for r2 in conn.execute(
                    f"SELECT id_emplacement, nom FROM emplacements_rangement "
                    f"WHERE id_emplacement IN ({id_placeholders})",
                    list(ids),
                )
            }
    resultat = {}
    for r in rows:
        libelle = None
        if r["nb_distinct"] == 1 and r["valeur"] is not None:
            libelle = noms_locaux.get(r["valeur"]) if local else r["valeur"]
        resultat[r["reference_titre"]] = {
            "nb_avec": r["nb_avec"], "nb_distinct": r["nb_distinct"], "libelle": libelle,
        }
    return resultat


def rangement_par_titre(conn: sqlite3.Connection, jeux: list[dict], contexte: str) -> list[dict]:
    """
    Enrichit une liste de jeux issue de `lister_catalogue()` (chaque dict a au
    moins `reference_titre` et `total`) avec le statut de rangement dans le
    CONTEXTE actif :

    - `rangement_affichage` : le libellé si toutes les boîtes du titre ont le
      MÊME emplacement non vide ; `"mixte"` si elles ont des valeurs mais pas
      toutes identiques (ou seulement certaines) ; `"—"` si aucune n'a de
      valeur.
    - `rangement_complet` (bool) : True SEULEMENT dans le premier cas (toutes
      identiques, non vide) — c'est le sens de « jeu déjà rangé » utilisé par
      la vue « Ranger les jeux » pour le filtre « à ranger seulement » (§13.5).
      Un jeu partiellement affecté ou aux copies divergentes reste « à
      ranger », même si chaque boîte a individuellement une valeur : il reste
      du travail (compléter ou harmoniser) dessus.

    Ne modifie pas les dicts d'entrée ; retourne une nouvelle liste.
    """
    reference_titres = [j["reference_titre"] for j in jeux]
    resume = _resume_emplacement_titres(conn, reference_titres, contexte)
    resultat = []
    for j in jeux:
        r = resume.get(j["reference_titre"], {"nb_avec": 0, "nb_distinct": 0, "libelle": None})
        if r["nb_avec"] == 0:
            affichage, complet = "—", False
        elif r["nb_distinct"] == 1 and r["nb_avec"] == j["total"]:
            affichage, complet = r["libelle"], True
        else:
            affichage, complet = "mixte", False
        resultat.append({**j, "rangement_affichage": affichage, "rangement_complet": complet})
    return resultat


def affecter_emplacement_lot(
    conn: sqlite3.Connection, reference_titres: list[str], contexte: str, valeur,
    ecraser: bool = True,
) -> dict:
    """
    Affectation en lot (§13.1/13.4) : écrit `valeur` sur TOUTES les boîtes des
    titres visés, dans le CONTEXTE donné — équivalent d'une boucle appelant
    `affecter_emplacement` sur chaque exemplaire, mais en une seule requête
    (adapté à ~700 boîtes).

    Si `ecraser` est False, seules les boîtes SANS emplacement dans ce
    contexte sont modifiées (ne comble que les trous, §13.4) — réutilise
    `_clause_sans_emplacement`, la même clause que la page des manques.

    `valeur` vide (texte vide/None) n'est PAS filtrée ici : le refus d'un lot
    vide (§13.4, pas de wipe de masse) est la responsabilité de l'APPELANT
    (route), qui ne doit pas appeler cette fonction dans ce cas.

    Returns:
        {"titres": nb de titres distincts effectivement modifiés,
         "boites": nb d'exemplaires effectivement modifiés}. {0, 0} si
        `reference_titres` est vide ou si aucune boîte ne correspond (rien à
        écraser et `ecraser=False`).
    """
    if not reference_titres:
        return {"titres": 0, "boites": 0}
    colonne = "emplacement_local_id" if contexte == "local" else "emplacement_evenement"
    placeholders = ",".join("?" * len(reference_titres))
    where = f"reference_titre IN ({placeholders})"
    params: list = list(reference_titres)
    if not ecraser:
        where += " AND " + _clause_sans_emplacement(contexte).replace("x.", "")
    lignes = conn.execute(
        f"SELECT id_exemplaire, reference_titre FROM exemplaires WHERE {where}", params
    ).fetchall()
    if not lignes:
        return {"titres": 0, "boites": 0}
    ids = [r["id_exemplaire"] for r in lignes]
    titres_touches = {r["reference_titre"] for r in lignes}
    id_placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE exemplaires SET {colonne} = ? WHERE id_exemplaire IN ({id_placeholders})",
        (valeur, *ids),
    )
    conn.commit()
    return {"titres": len(titres_touches), "boites": len(ids)}


# ===========================================================================
# REGISTRE DES APPAREILS (docs/conception-journal.md §4)
# ===========================================================================
# Un identifiant tiré au hasard, posé dans un cookie aux deux seuls endroits
# qui ouvrent un accès en ÉCRITURE (activation bénévole, connexion admin), et
# consigné dans la table `appareils`. Il répond à une question simple qu'aucun
# écran ne savait poser jusqu'ici : combien de téléphones ont réellement activé
# l'accès, et depuis quand ?
#
# CE QU'IL DIT ET NE DIT PAS — voir le commentaire de models.SCHEMA_APPAREILS.
# En résumé : « c'est le même téléphone », jamais « c'est le téléphone de
# Marie ». Aucun cookie n'est posé pour le PUBLIC : un visiteur qui consulte le
# catalogue ou s'inscrit à un tournoi ne reçoit rien.

# Nom du cookie d'appareil. Même famille que COOKIE_RANGEMENT (cookie
# d'appareil, résolu ici et pas dans une route) : un seul domicile pour le nom
# et pour sa lecture, car plusieurs surfaces en ont besoin.
COOKIE_APPAREIL = "appareil"

# Longueur de l'empreinte de génération conservée (caractères de sha256).
# Assez pour distinguer deux générations de jeton, trop peu pour reconstituer
# quoi que ce soit — et de toute façon un hash n'est pas réversible.
_LONGUEUR_EMPREINTE = 8

# Au-delà de ce délai, une ligne d'appareil périmée est supprimée à la clôture
# de fin d'événement (§8.1 : le registre est persistant ET sauvegardé, donc il
# survit à la rotation du journal — il lui faut sa propre purge).
RETENTION_APPAREILS_JOURS = 365


def nouvel_appareil() -> str:
    """
    Tire un identifiant d'appareil : 6 caractères hexadécimaux majuscules.

    Six et non quatre : avec quatre (65 536 valeurs) et une trentaine
    d'appareils, la probabilité qu'au moins deux se retrouvent avec le même
    identifiant avoisine 0,7 % — assez rare pour ne jamais être testée, assez
    fréquente pour induire en erreur le jour où elle survient. Six rendent la
    collision négligeable et restent lisibles à voix haute au téléphone
    (« moi c'est 3F1A9C »).
    """
    return secrets.token_hex(3).upper()


def appareil_de(request) -> str | None:
    """Identifiant d'appareil porté par la requête, ou None si le cookie est absent."""
    return request.cookies.get(COOKIE_APPAREIL) or None


def empreinte_jeton(jeton: str | None) -> str | None:
    """
    Empreinte TRONQUÉE du jeton bénévole, pour reconnaître sa génération.

    JAMAIS le jeton lui-même (docs/conception-journal.md §8) : on n'en garde
    que les 8 premiers caractères de son sha256. Cette valeur sert uniquement à
    comparer deux générations entre elles — « ce téléphone a-t-il été activé
    avec le jeton en vigueur, ou avec le précédent ? ».

    Returns:
        L'empreinte, ou None si aucun jeton n'est configuré (mode ouvert).
    """
    if not jeton:
        return None
    return hashlib.sha256(jeton.encode()).hexdigest()[:_LONGUEUR_EMPREINTE]


def enregistrer_appareil(
    conn: sqlite3.Connection,
    appareil: str,
    role: str,
    expire_le: str | None = None,
    generation: str | None = None,
) -> None:
    """
    Consigne une activation dans le registre (une écriture, à l'activation).

    Un appareil DÉJÀ connu voit sa ligne mise à jour plutôt que dupliquée : le
    cas est réel et sans lui la liste mentirait. Deux situations :

    - **Rotation du jeton.** Après une réinitialisation, tous les bénévoles
      rouvrent le lien d'activation sur le même téléphone. Le cookie
      d'appareil, lui, n'est pas réécrit (il n'est posé que s'il est absent) :
      sans mise à jour de `generation`, l'appareil resterait affiché « périmé »
      alors qu'il vient de se réactiver, et le compteur annoncerait 0 appareil
      pendant que douze téléphones fonctionnent.
    - **Changement de rôle.** Le téléphone d'un membre du bureau active
      d'abord le jeton bénévole, puis se connecte en administration. La clé
      primaire étant l'identifiant, un appareil n'a qu'un rôle AFFICHÉ : c'est
      celui de la DERNIÈRE activation, décision prise avec Simon (le suivi par
      rôle importe moins que le fait de ne pas inventer deux lignes pour un
      seul téléphone). En revanche l'appareil peut porter les DEUX FACETTES à
      la fois (bénévole ET administration) — voir `_appareil_actif` — donc
      cette activation ne doit JAMAIS effacer ce qu'une activation précédente,
      d'un autre rôle, avait posé sur cette même ligne (correctif lot E,
      défaut constaté au contrôle post-livraison : une connexion admin
      écrasait `expire_le`/`generation` avec NULL, faisant mentir la liste dès
      le redémarrage suivant du service, alors que le cookie de jeton du
      téléphone restait valide).

    D'où `COALESCE(excluded.x, appareils.x)` sur `expire_le` et `generation` —
    **exactement le motif déjà employé par l'import CSV du catalogue** (« une
    case laissée vide n'efface jamais une valeur déjà en base ») : une
    connexion admin n'apporte ni l'un ni l'autre (elle passe `None`), elle ne
    doit donc pas effacer ceux d'une facette bénévole déjà enregistrée. Une
    RÉACTIVATION bénévole, elle, apporte toujours de vraies valeurs (jamais
    `None`) et les remplace normalement.

    `active_le` n'est PAS réécrit : la date qu'on veut lire est celle de la
    première activation de cet appareil, pas celle de sa dernière rotation.

    Cette fonction n'est appelée QUE depuis /acces et POST /admin/login, jamais
    depuis un chemin chaud (voir le commentaire de models.SCHEMA_APPAREILS).
    """
    conn.execute(
        "INSERT INTO appareils (appareil, role, active_le, expire_le, generation) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(appareil) DO UPDATE SET "
        "    role = excluded.role, "
        "    expire_le = COALESCE(excluded.expire_le, appareils.expire_le), "
        "    generation = COALESCE(excluded.generation, appareils.generation)",
        (appareil, role, maintenant(), expire_le, generation),
    )
    conn.commit()


def renommer_appareil(conn: sqlite3.Connection, appareil: str, libelle: str) -> None:
    """
    Pose (ou efface) le libellé libre d'un appareil.

    Le libellé désigne un POSTE (« comptoir 2 », « accueil »), jamais une
    personne — c'est le seul endroit du dispositif où une donnée personnelle
    pourrait entrer, la consigne est donc affichée sous le champ lui-même
    (docs/conception-journal.md, arbitrage 7). L'application ne déduit jamais
    rien de cette valeur : elle l'affiche, et c'est tout.

    Une saisie vide efface le libellé (NULL) — jamais une chaîne vide, pour que
    le gabarit n'ait qu'un seul cas d'absence à traiter.
    """
    propre = " ".join((libelle or "").split())[:40] or None
    conn.execute(
        "UPDATE appareils SET libelle = ? WHERE appareil = ?", (propre, appareil)
    )
    conn.commit()


def _motif_facette_benevole(ligne: dict, generation_courante: str | None,
                            instant: str) -> str | None:
    """
    Motif pour lequel la FACETTE BÉNÉVOLE de cet appareil n'est plus active,
    ou None si elle l'est encore.

    Ne s'applique que si l'appareil a UN JOUR activé le jeton bénévole
    (`generation` renseignée) — piège central du correctif lot E : un poste
    d'administration pur (jamais de facette bénévole) a `generation = NULL`,
    et une comparaison naïve à `generation_courante` le déclarerait à tort
    « jeton renouvelé ». Un appareil non concerné par cette facette n'est ni
    actif ni périmé de ce côté : la question ne se pose pas pour lui.
    """
    if ligne["generation"] is None:
        return None
    if ligne["generation"] != generation_courante:
        return "jeton_renouvele"
    if ligne["expire_le"] and ligne["expire_le"] < instant:
        return "echeance"
    return None


def _appareil_actif(ligne: dict, generation_courante: str | None,
                    admins_ouverts: set[str], instant: str) -> str | None:
    """
    Motif pour lequel cet appareil N'EST PLUS actif, ou None s'il l'est encore.

    « Actif » n'a pas le même sens pour les deux rôles (§4.5), et depuis le
    correctif du lot E, **un même appareil peut porter les deux facettes à la
    fois** (le téléphone du bureau : bénévole ET administration) — la décision
    « un appareil n'a qu'un rôle » ne portait que sur le nombre de LIGNES en
    base, jamais sur le nombre de facettes qu'une ligne peut représenter.
    L'appareil est actif si **l'une des deux** facettes l'est :

    - **Bénévole** — voir `_motif_facette_benevole`. Ne s'applique que si
      l'appareil a un jour activé le jeton (`generation` renseignée).
    - **Administration** — les sessions vivent dans un dictionnaire EN MÉMOIRE
      du process : un redémarrage les ferme toutes, et aucune colonne de base
      ne peut le savoir. La vérité est donc lue en mémoire (`admins_ouverts`).

    Quand aucune des deux facettes n'est active, le motif renvoyé est le plus
    parlant pour cette ligne : celui de la facette bénévole si elle est
    concernée (« jeton renouvelé » / « validité dépassée » — l'info la plus
    utile, une session fermée étant l'état par défaut au repos), sinon
    « session fermée » (poste d'administration pur, ou appareil jamais activé
    en bénévole).

    AUCUNE ÉCRITURE : tout se calcule à la lecture, comme l'expiration de
    l'annonce d'écran de salle (routes/live.py::annonce_active). Rien n'est
    jamais purgé ici — la purge a lieu à la clôture de fin d'événement, et
    seulement au-delà d'un an.
    """
    admin_actif = ligne["appareil"] in admins_ouverts
    motif_benevole = _motif_facette_benevole(ligne, generation_courante, instant)
    benevole_actif = ligne["generation"] is not None and motif_benevole is None
    if admin_actif or benevole_actif:
        return None
    return motif_benevole if ligne["generation"] is not None else "session_fermee"


def lister_appareils(
    conn: sqlite3.Connection,
    generation_courante: str | None,
    admins_ouverts: set[str] | None = None,
) -> dict:
    """
    Registre des appareils, trié par activation décroissante, séparé en deux.

    Args:
        conn: connexion SQLite ouverte.
        generation_courante: empreinte du jeton EN VIGUEUR (`empreinte_jeton`).
        admins_ouverts: identifiants des appareils dont une session admin est
            encore ouverte (`admin_auth.appareils_admin_ouverts()`).

    Returns:
        {"actifs": [...], "perimes": [...], "nb_benevoles_actifs": int}. Chaque
        entrée est un dict portant en plus `motif` (None si actif, sinon
        "echeance" / "jeton_renouvele" / "session_fermee"), ainsi que
        `benevole_actif`/`admin_actif` (bool, une facette chacun — au gabarit
        de les afficher, ex. deux badges pour un appareil qui porte les deux)
        — le service ne fabrique pas de phrase.
    """
    admins_ouverts = admins_ouverts or set()
    instant = maintenant()
    actifs, perimes = [], []
    lignes = conn.execute(
        "SELECT appareil, role, active_le, expire_le, generation, libelle "
        "FROM appareils ORDER BY active_le DESC"
    ).fetchall()
    for row in lignes:
        ligne = dict(row)
        ligne["admin_actif"] = ligne["appareil"] in admins_ouverts
        ligne["benevole_actif"] = (
            ligne["generation"] is not None
            and _motif_facette_benevole(ligne, generation_courante, instant) is None
        )
        ligne["motif"] = _appareil_actif(
            ligne, generation_courante, admins_ouverts, instant
        )
        (actifs if ligne["motif"] is None else perimes).append(ligne)
    return {
        "actifs": actifs,
        "perimes": perimes,
        # Compte la FACETTE bénévole active, pas le `role` affiché (dernière
        # activation) : un appareil du bureau, admin en dernier, dont le
        # cookie de jeton est toujours valide, doit rester compté.
        "nb_benevoles_actifs": sum(1 for a in actifs if a["benevole_actif"]),
    }


def purger_appareils_anciens(conn: sqlite3.Connection,
                             jours: int = RETENTION_APPAREILS_JOURS) -> int:
    """
    Supprime les lignes d'appareils dont la dernière activation remonte à plus
    de `jours` (§8.1 : garde-fou RGPD du registre, qui est persistant ET
    sauvegardé, donc il ne disparaît pas de lui-même comme les rotations du
    journal).

    On mesure sur `COALESCE(expire_le, active_le)` : `expire_le` est NULL pour
    un appareil d'administration, où la session en mémoire fait foi — c'est
    alors la date d'activation qui sert de repère.

    Ne committe pas : appelée depuis `cloturer_tous_les_prets`, à l'intérieur
    de sa transaction.

    Returns:
        Le nombre de lignes supprimées.
    """
    limite = (datetime.now(timezone.utc)
              - timedelta(days=jours)).isoformat(timespec="seconds")
    cur = conn.execute(
        "DELETE FROM appareils WHERE COALESCE(expire_le, active_le) < ?", (limite,)
    )
    return cur.rowcount


# ===========================================================================
# CARNET DE MAINTENANCE — catégories de signalement
# (docs/conception-signalements.md §2/§4). Liste configurable en admin, patron
# ligne à ligne de `*_emplacement_rangement` : archivage doux (`actif`),
# jamais de suppression sous un signalement déjà rattaché.
# ===========================================================================
def lister_categories_signalement(conn: sqlite3.Connection) -> list[dict]:
    """
    Liste complète (actives + archivées) des catégories de signalement, triée
    par ordre d'affichage puis nom, avec le nombre de signalements qui s'y
    rattachent (`usage_count`) — sert à décider si la suppression dure est
    proposée.
    """
    rows = conn.execute(
        """
        SELECT c.id_categorie, c.nom, c.actif, c.ordre,
               COUNT(s.id_signalement) AS usage_count
        FROM categories_signalement c
        LEFT JOIN signalements s ON s.id_categorie = c.id_categorie
        GROUP BY c.id_categorie
        ORDER BY c.ordre, c.nom COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def categories_signalement_actives(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """
    Catégories ACTIVES, triées pour l'affichage dans le formulaire bénévole
    (boutons radio, §5 de la note). Les archivées en sont exclues (mais
    restent affichées là où elles sont déjà pointées, côté admin).
    """
    return conn.execute(
        "SELECT id_categorie, nom FROM categories_signalement "
        "WHERE actif = 1 ORDER BY ordre, nom COLLATE NOCASE"
    ).fetchall()


def creer_categorie_signalement(conn: sqlite3.Connection, nom: str) -> int | None:
    """Ajoute une catégorie en fin de liste (ordre = max + 1). None si nom vide."""
    nom_normalise = " ".join(nom.split())
    if not nom_normalise:
        return None
    (max_ordre,) = conn.execute(
        "SELECT COALESCE(MAX(ordre), -1) FROM categories_signalement"
    ).fetchone()
    curseur = conn.execute(
        "INSERT INTO categories_signalement (nom, actif, ordre) VALUES (?, 1, ?)",
        (nom_normalise, max_ordre + 1),
    )
    conn.commit()
    return curseur.lastrowid


def renommer_categorie_signalement(conn: sqlite3.Connection, id_categorie: int, nom: str) -> bool:
    """
    Renomme (répercuté automatiquement sur l'historique via la FK, §4). False
    si `nom` est vide (rien n'est modifié).
    """
    nom_normalise = " ".join(nom.split())
    if not nom_normalise:
        return False
    conn.execute(
        "UPDATE categories_signalement SET nom = ? WHERE id_categorie = ?",
        (nom_normalise, id_categorie),
    )
    conn.commit()
    return True


def archiver_categorie_signalement(conn: sqlite3.Connection, id_categorie: int) -> None:
    """
    Retrait doux (§2) : disparaît du formulaire bénévole, mais les
    signalements qui la portent gardent leur référence.
    """
    conn.execute(
        "UPDATE categories_signalement SET actif = 0 WHERE id_categorie = ?",
        (id_categorie,),
    )
    conn.commit()


def reactiver_categorie_signalement(conn: sqlite3.Connection, id_categorie: int) -> None:
    """Annule un archivage : redevient proposée dans le formulaire bénévole."""
    conn.execute(
        "UPDATE categories_signalement SET actif = 1 WHERE id_categorie = ?",
        (id_categorie,),
    )
    conn.commit()


def compteur_usage_categorie_signalement(conn: sqlite3.Connection, id_categorie: int) -> int:
    """Nombre de signalements qui pointent actuellement vers cette catégorie."""
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM signalements WHERE id_categorie = ?",
        (id_categorie,),
    ).fetchone()
    return n


def supprimer_categorie_signalement(conn: sqlite3.Connection, id_categorie: int) -> bool:
    """
    Suppression DURE : refusée (False, rien n'est modifié) si au moins un
    signalement pointe encore vers cette catégorie — jamais de FK orpheline.
    """
    if compteur_usage_categorie_signalement(conn, id_categorie) > 0:
        return False
    conn.execute(
        "DELETE FROM categories_signalement WHERE id_categorie = ?",
        (id_categorie,),
    )
    conn.commit()
    return True


def deplacer_categorie_signalement(conn: sqlite3.Connection, id_categorie: int, sens: str) -> None:
    """
    Échange la position d'une catégorie avec sa voisine immédiate dans la
    liste triée (`sens` = "haut" ou "bas"). Sans effet si elle est déjà en
    bout de liste ou si `id_categorie` est inconnu. Travaille sur la POSITION
    dans la liste triée (pas la valeur brute d'`ordre`), robuste même si deux
    lignes partagent le même `ordre`.
    """
    lignes = conn.execute(
        "SELECT id_categorie, ordre FROM categories_signalement "
        "ORDER BY ordre, nom COLLATE NOCASE"
    ).fetchall()
    ids = [r["id_categorie"] for r in lignes]
    if id_categorie not in ids:
        return
    idx = ids.index(id_categorie)
    voisin = idx - 1 if sens == "haut" else idx + 1
    if voisin < 0 or voisin >= len(ids):
        return
    a, b = lignes[idx], lignes[voisin]
    conn.execute(
        "UPDATE categories_signalement SET ordre = ? WHERE id_categorie = ?",
        (b["ordre"], a["id_categorie"]),
    )
    conn.execute(
        "UPDATE categories_signalement SET ordre = ? WHERE id_categorie = ?",
        (a["ordre"], b["id_categorie"]),
    )
    conn.commit()


# ===========================================================================
# CARNET DE MAINTENANCE — signalements (docs/conception-signalements.md §3/§4).
#
# Pas de `services.transaction` ici : un signalement est un INSERT autonome
# (ni lecture-puis-écriture, ni ressource à attribuer, contrairement au prêt),
# et le passage à « traité » est un UPDATE idempotent — rien à protéger par un
# `BEGIN IMMEDIATE` (§4 de la note, piège 1 du lot 1).
#
# Volontairement séparé d'`info_exemplaire` (même raison qu'`emplacement_actuel`) :
# `info` est réutilisée par les gabarits PUBLICS, et le carnet de maintenance
# n'est jamais public (bénévoles et admin seulement, §2).
# ===========================================================================

# Bornage du détail libre à l'enregistrement, sans jamais refuser la saisie
# (patron de l'annonce d'écran de salle, routes/live.py).
LONGUEUR_MAX_TEXTE_SIGNALEMENT = 300


def creer_signalement(
    conn: sqlite3.Connection,
    id_exemplaire: str,
    id_categorie: int | None,
    texte: str | None = None,
) -> int:
    """
    Enregistre un signalement sur une boîte. `texte` est facultatif (la
    catégorie seule suffit, §3) et borné à `LONGUEUR_MAX_TEXTE_SIGNALEMENT`
    caractères plutôt que refusé.

    Ne valide PAS `id_categorie` (existence, catégorie active) : c'est la
    responsabilité de l'appelant (route), qui doit relire la liste des
    catégories actives et ne jamais faire confiance au formulaire (§8).

    Returns:
        L'identifiant du signalement créé.
    """
    texte_normalise = texte.strip()[:LONGUEUR_MAX_TEXTE_SIGNALEMENT] if texte else None
    texte_normalise = texte_normalise or None  # chaîne vide après strip -> None
    curseur = conn.execute(
        "INSERT INTO signalements (id_exemplaire, id_categorie, texte, cree_le) "
        "VALUES (?, ?, ?, ?)",
        (id_exemplaire, id_categorie, texte_normalise, maintenant()),
    )
    conn.commit()
    return curseur.lastrowid


def signalements_ouverts(conn: sqlite3.Connection, id_exemplaire: str) -> list[dict]:
    """
    Signalements OUVERTS (`traite_le IS NULL`) d'une boîte, du plus ancien au
    plus récent — sert au bandeau d'alerte de /pret/<id> (§6, « prévenir »).
    """
    rows = conn.execute(
        """
        SELECT s.id_signalement, s.texte, s.cree_le,
               c.nom AS categorie_nom
        FROM signalements s
        LEFT JOIN categories_signalement c ON c.id_categorie = s.id_categorie
        WHERE s.id_exemplaire = ? AND s.traite_le IS NULL
        ORDER BY s.cree_le
        """,
        (id_exemplaire,),
    ).fetchall()
    return [dict(r) for r in rows]


def lister_signalements(
    conn: sqlite3.Connection,
    etat: str = "ouverts",
    id_categorie: int | None = None,
) -> list[dict]:
    """
    Liste des signalements pour l'écran d'administration (§7), triée du plus
    récent au plus ancien. `etat` : "ouverts" (défaut) / "traites" / "tous".

    Ramène les DEUX emplacements — événement ET local — par jointure dans
    cette même requête (piège 3 du lot 1) : ne PAS appeler
    `emplacement_actuel`, qui choisit selon le contexte réglé en
    administration et ne convient pas ici (§7 de la note — cette liste se
    consulte souvent après l'événement, quand le contexte a changé).
    """
    conditions = []
    params: list = []
    if etat == "ouverts":
        conditions.append("s.traite_le IS NULL")
    elif etat == "traites":
        conditions.append("s.traite_le IS NOT NULL")
    if id_categorie is not None:
        conditions.append("s.id_categorie = ?")
        params.append(id_categorie)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(
        f"""
        SELECT s.id_signalement, s.id_exemplaire, s.texte, s.cree_le, s.traite_le,
               t.nom AS jeu_nom,
               c.nom AS categorie_nom,
               x.emplacement_evenement,
               el.nom AS emplacement_local_nom
        FROM signalements s
        JOIN exemplaires x ON x.id_exemplaire = s.id_exemplaire
        JOIN titres t ON t.reference_titre = x.reference_titre
        LEFT JOIN categories_signalement c ON c.id_categorie = s.id_categorie
        LEFT JOIN emplacements_rangement el ON el.id_emplacement = x.emplacement_local_id
        {where}
        ORDER BY s.cree_le DESC
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_signalement(conn: sqlite3.Connection, id_signalement: int) -> dict | None:
    """
    Un signalement et son contexte lisible, ou None s'il est inconnu.

    Existe pour la LIGNE DE JOURNAL du « Marquer traité », que l'administrateur
    ET le bénévole depuis la fiche peuvent déclencher (§6 bis) : elle doit
    nommer le jeu, que la table `signalements` ne porte pas (même besoin que
    `info_exemplaire` pour les quatre actions de prêt), et son `traite_le`
    permet à la route de ne rien écrire quand un second appui ne change
    rien — `traiter_signalement` étant idempotent, journaliser sans regarder
    produirait des lignes qui affirment un fait qui n'a pas eu lieu (même
    précaution que « annonce effacée », qui n'est écrite que s'il y avait
    bien une annonce).

    `texte` n'est délibérément PAS ramené : aucun appelant n'en a besoin, et
    c'est le seul champ de l'application de prêt par lequel une donnée
    personnelle peut entrer (docs/conception-signalements.md §3).
    """
    row = conn.execute(
        """
        SELECT s.id_signalement, s.id_exemplaire, s.cree_le, s.traite_le,
               t.nom AS jeu_nom, t.reference_titre,
               c.nom AS categorie_nom
        FROM signalements s
        JOIN exemplaires x ON x.id_exemplaire = s.id_exemplaire
        JOIN titres t ON t.reference_titre = x.reference_titre
        LEFT JOIN categories_signalement c ON c.id_categorie = s.id_categorie
        WHERE s.id_signalement = ?
        """,
        (id_signalement,),
    ).fetchone()
    return dict(row) if row else None


def traiter_signalement(conn: sqlite3.Connection, id_signalement: int) -> None:
    """
    Referme un signalement — depuis le lot agora-4, l'administrateur ET le
    bénévole depuis la fiche de la boîte (§2 et §6 bis, la note réservait ce
    geste à l'administrateur seul avant ce lot). `UPDATE ... WHERE traite_le
    IS NULL` : IDEMPOTENT, un second appel ne change rien et ne lève aucune
    erreur (piège 2 du lot 1).
    """
    conn.execute(
        "UPDATE signalements SET traite_le = ? "
        "WHERE id_signalement = ? AND traite_le IS NULL",
        (maintenant(), id_signalement),
    )
    conn.commit()


def fermer_signalement(conn: sqlite3.Connection, id_signalement: int,
                       id_exemplaire: str | None = None) -> dict | None:
    """
    Lit puis referme un signalement. Domicile UNIQUE de ce raisonnement,
    partagé par les TROIS écrans qui portent le bouton « traité » : la fiche
    de la boîte (`routes/pret.py`), le carnet bénévole (`routes/maintenance.py`)
    et le carnet d'administration (`routes/admin.py`).

    Écrite au lot agora-4 dans `routes/pret.py`, PROMUE ICI au lot agora-5 :
    à deux appelants un import différé entre modules de routes se défendait,
    à trois il fallait un domicile qui ne soit pas un module de routes.
    La décision de journalisation, elle, reste hors des services (règle du §5.2
    de docs/conception-journal.md) : voir `app.carnet.journaliser_traite`.

    Relit le signalement AVANT l'écriture (`get_signalement`) : la ligne de
    journal doit nommer le jeu, que la table `signalements` ne porte pas, et
    son `traite_le` dit si ce clic change réellement quelque chose.

    `id_exemplaire`, fourni par la route de la fiche (jamais par les deux
    carnets, qui agissent sans boîte de référence) : contrôle qu'une URL
    forgée ne referme pas le signalement d'une autre boîte. Incohérent ->
    None, RIEN n'est écrit, exactement comme un identifiant inconnu — c'est à
    l'appelant de traduire ça en message, jamais en erreur brute (règle « ne
    jamais bloquer »).

    Returns:
        Le signalement tel qu'AVANT l'écriture, ou None s'il est inconnu ou ne
        correspond pas à `id_exemplaire`.
    """
    avant = get_signalement(conn, id_signalement)
    if avant is None:
        return None
    if id_exemplaire is not None and avant["id_exemplaire"] != id_exemplaire:
        return None
    traiter_signalement(conn, id_signalement)
    return avant


def compter_signalements_ouverts(conn: sqlite3.Connection) -> int:
    """Nombre total de signalements ouverts, tous jeux confondus (compteur §7)."""
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM signalements WHERE traite_le IS NULL"
    ).fetchone()
    return n
