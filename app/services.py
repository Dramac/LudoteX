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

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
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
                     joueurs: int | None = None) -> list[dict]:
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

    Args:
        conn: connexion SQLite ouverte.
        categorie, q, age, joueurs: filtres optionnels (voir ci-dessus).

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
    sql += " GROUP BY t.reference_titre, t.nom, t.categorie ORDER BY t.nom COLLATE NOCASE"
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
        dict avec total_prets, en_cours, titres_pretes, nb_titres.
    """
    # Les sorties « tournoi » sont exclues de toutes les statistiques.
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
EN_TETES_CATALOGUE = [
    "Code jeu", "Nom jeu", "Type", "Type jeu", "Nb joueurs", "Age joueurs",
    "Temps jeu", "Marque", "Auteur", "Année édition", "Date achat", "Descriptif",
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
        SELECT e.id_exemplaire, t.nom, t.type_jeu, t.categorie,
               t.nb_joueurs_min, t.nb_joueurs_max, t.duree_min, t.age_min,
               t.editeur, t.auteur, t.annee_edition, t.descriptif, t.date_achat
        FROM exemplaires e
        JOIN titres t ON t.reference_titre = e.reference_titre
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

    L'appelant (route) garantit que l'exemplaire est bien disponible via un
    contrôle d'état préalable ; conformément à « ne jamais bloquer », cette
    fonction elle-même ne refuse jamais.

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte à prêter.

    Returns:
        Le numéro de pochette attribué (à afficher au bénévole).
    """
    numero = plus_petit_numero_libre(conn)
    conn.execute(
        """
        INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)
        VALUES (?, ?, ?, 'pret')
        """,
        (id_exemplaire, numero, maintenant()),
    )
    conn.commit()
    return numero


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
    conn.execute(
        """
        INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)
        VALUES (?, ?, ?, 'tournoi')
        """,
        (id_exemplaire, NUMERO_TOURNOI, maintenant()),
    )
    conn.commit()


def rendre(conn: sqlite3.Connection, id_exemplaire: str) -> dict:
    """
    Enregistre le retour d'un exemplaire : clôt le prêt/sortie en cours et, s'il
    s'agissait d'un prêt au public, libère sa pochette.

    Args:
        conn: connexion SQLite ouverte.
        id_exemplaire: identifiant de la boîte rendue.

    Returns:
        {"numero_libere": n, "motif": "pret"} pour un prêt au public,
        {"motif": "tournoi"} pour un retour de tournoi (pas d'emplacement), ou
        {"deja_disponible": True} si rien à clore (cas non bloquant).
    """
    courant = pret_en_cours(conn, id_exemplaire)
    if courant is None:
        return {"deja_disponible": True}
    conn.execute(
        "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
        (maintenant(), courant["id_pret"]),
    )
    if courant["motif"] == "tournoi":
        conn.commit()
        return {"motif": "tournoi"}
    # Prêt au public : on libère le numéro d'emplacement.
    liberer_numero(conn, courant["numero_pochette"])
    conn.commit()
    return {"numero_libere": courant["numero_pochette"], "motif": "pret"}


def cloturer_tous_les_prets(conn: sqlite3.Connection) -> int:
    """
    Clôture TOUS les prêts/sorties en cours et libère toutes les pochettes.

    Usage : remise à blanc en fin d'événement. On NE supprime PAS l'historique
    (les statistiques restent) : on se contente de poser `date_retour = maintenant`
    sur tout ce qui était encore ouvert, et de libérer toutes les pochettes. Cela
    couvre aussi les sorties tournoi encore ouvertes.

    Returns:
        Le nombre de prêts/sorties clôturés.
    """
    cur = conn.execute(
        "UPDATE prets SET date_retour = ? WHERE date_retour IS NULL",
        (maintenant(),),
    )
    nb = cur.rowcount
    conn.execute("UPDATE pochettes SET occupe = 0")
    conn.commit()
    return nb


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
    courant = pret_en_cours(conn, id_exemplaire)
    if courant is None:
        # Incohérence bénigne : rien à clore, on ouvre simplement un prêt.
        return {"nouveau_numero": preter(conn, id_exemplaire), "etait_disponible": True}
    # Clôture de l'ancien prêt + libération de son numéro...
    conn.execute(
        "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
        (maintenant(), courant["id_pret"]),
    )
    liberer_numero(conn, courant["numero_pochette"])
    # ... puis ouverture d'un nouveau prêt (preter() committe l'ensemble).
    nouveau = preter(conn, id_exemplaire)
    return {"ancien_numero": courant["numero_pochette"], "nouveau_numero": nouveau}


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

    Returns:
        Liste de dicts {id_exemplaire, sorti(bool)}, triée par id.
    """
    rows = conn.execute(
        "SELECT id_exemplaire FROM exemplaires WHERE reference_titre = ? "
        "ORDER BY id_exemplaire",
        (reference_titre,),
    ).fetchall()
    return [{"id_exemplaire": r[0], "sorti": est_sorti(conn, r[0])} for r in rows]


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
