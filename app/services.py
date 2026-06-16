"""
Logique métier du prêt — état, pochettes, prêt/retour.

Toutes les fonctions prennent une connexion SQLite ouverte (`sqlite3.Connection`)
pour rester testables et permettre à l'appelant de gérer la transaction. Les
fonctions d'écriture committent elles-mêmes.

Règles (voir docs/specification.md §3, §5, §6) :
- L'état d'un exemplaire est DÉDUIT : il est SORTI s'il a un prêt avec
  `date_retour IS NULL`, DISPONIBLE sinon. Jamais stocké en dur.
- Numéro de pochette : à partir de 1, on attribue toujours le PLUS PETIT numéro
  libre, recyclé au retour, AUCUN plafond (on ne refuse jamais un prêt).
- L'historique des prêts n'est jamais purgé.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def maintenant() -> str:
    """Horodatage ISO 8601 en UTC (précision seconde)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Lecture / état
# ---------------------------------------------------------------------------
def info_exemplaire(conn: sqlite3.Connection, id_exemplaire: str) -> dict | None:
    """Infos de l'exemplaire + son titre, ou None si l'exemplaire est inconnu."""
    row = conn.execute(
        """
        SELECT e.id_exemplaire, t.reference_titre, t.nom, t.categorie,
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
    """Le prêt non clos de l'exemplaire (dict id_pret/numero_pochette), ou None."""
    row = conn.execute(
        """
        SELECT id_pret, numero_pochette, date_sortie
        FROM prets
        WHERE id_exemplaire = ? AND date_retour IS NULL
        ORDER BY id_pret DESC
        LIMIT 1
        """,
        (id_exemplaire,),
    ).fetchone()
    return dict(row) if row else None


def est_sorti(conn: sqlite3.Connection, id_exemplaire: str) -> bool:
    return pret_en_cours(conn, id_exemplaire) is not None


def lister_categories(conn: sqlite3.Connection) -> list[str]:
    """Catégories distinctes présentes dans le catalogue, triées."""
    rows = conn.execute(
        "SELECT DISTINCT categorie FROM titres "
        "WHERE categorie IS NOT NULL AND categorie <> '' ORDER BY categorie"
    ).fetchall()
    return [r[0] for r in rows]


def lister_catalogue(conn: sqlite3.Connection, categorie: str | None = None) -> list[dict]:
    """
    Catalogue au niveau titre : nom, catégorie, total et nombre d'exemplaires
    disponibles, et un exemplaire représentatif (le plus petit id) pour le lien
    vers la fiche. Filtrable par catégorie. Trié par nom.
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
    params: tuple = ()
    if categorie:
        sql += " WHERE t.categorie = ?"
        params = (categorie,)
    sql += " GROUP BY t.reference_titre, t.nom, t.categorie ORDER BY t.nom COLLATE NOCASE"
    return [dict(r) for r in conn.execute(sql, params)]


def dispo_par_titre(conn: sqlite3.Connection, reference_titre: str) -> tuple[int, int]:
    """(total exemplaires, exemplaires disponibles) pour un titre."""
    total = conn.execute(
        "SELECT COUNT(*) FROM exemplaires WHERE reference_titre = ?",
        (reference_titre,),
    ).fetchone()[0]
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


# ---------------------------------------------------------------------------
# Pochettes
# ---------------------------------------------------------------------------
def plus_petit_numero_libre(conn: sqlite3.Connection) -> int:
    """
    Attribue (et marque occupé) le plus petit numéro de pochette libre.
    Réutilise un numéro libéré ; sinon en crée un nouveau (max + 1). Sans plafond.
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
    conn.execute(
        "UPDATE pochettes SET occupe = 0 WHERE numero_pochette = ?", (numero_pochette,)
    )


# ---------------------------------------------------------------------------
# Opérations de prêt / retour
# ---------------------------------------------------------------------------
def preter(conn: sqlite3.Connection, id_exemplaire: str) -> int:
    """
    Ouvre un prêt : attribue le plus petit numéro de pochette libre et
    enregistre la sortie. Retourne le numéro de pochette attribué.
    L'appelant garantit que l'exemplaire est DISPONIBLE (contrôle d'état côté
    route) ; cette fonction ne refuse jamais.
    """
    numero = plus_petit_numero_libre(conn)
    conn.execute(
        """
        INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie)
        VALUES (?, ?, ?)
        """,
        (id_exemplaire, numero, maintenant()),
    )
    conn.commit()
    return numero


def rendre(conn: sqlite3.Connection, id_exemplaire: str) -> dict:
    """
    Clôt le prêt en cours et libère le numéro de pochette.
    Retourne {"numero_libere": n} ou {"deja_disponible": True} si rien à rendre.
    """
    courant = pret_en_cours(conn, id_exemplaire)
    if courant is None:
        return {"deja_disponible": True}
    conn.execute(
        "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
        (maintenant(), courant["id_pret"]),
    )
    liberer_numero(conn, courant["numero_pochette"])
    conn.commit()
    return {"numero_libere": courant["numero_pochette"]}


def repreter(conn: sqlite3.Connection, id_exemplaire: str) -> dict:
    """
    Cas d'oubli de scan : considère le prêt précédent comme rentré (retour =
    maintenant, ancien numéro libéré), puis ouvre un nouveau prêt.
    Retourne {"ancien_numero": a, "nouveau_numero": n} ; si l'exemplaire était
    en fait disponible, {"nouveau_numero": n, "etait_disponible": True}.
    """
    courant = pret_en_cours(conn, id_exemplaire)
    if courant is None:
        return {"nouveau_numero": preter(conn, id_exemplaire), "etait_disponible": True}
    conn.execute(
        "UPDATE prets SET date_retour = ? WHERE id_pret = ?",
        (maintenant(), courant["id_pret"]),
    )
    liberer_numero(conn, courant["numero_pochette"])
    # commit implicite via preter()
    nouveau = preter(conn, id_exemplaire)
    return {"ancien_numero": courant["numero_pochette"], "nouveau_numero": nouveau}
