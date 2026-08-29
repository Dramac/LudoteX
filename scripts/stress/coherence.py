"""
Vérificateur d'INVARIANTS de la base de prêt — à passer après un test de charge.

Le générateur de charge voit ce que le serveur AFFICHE ; ce script regarde ce
que la base CONTIENT. Les deux peuvent diverger : une course d'attribution ne
produit aucun message d'erreur à l'écran, elle laisse seulement une base
incohérente.

Les invariants vérifiés découlent directement des règles métier
(`CLAUDE.md` § « Règles métier non négociables ») :

    I1  un exemplaire n'a jamais deux prêts ouverts en même temps ;
    I2  deux prêts ouverts ne partagent jamais un numéro de pochette ;
    I3  `pochettes.occupe` reflète exactement les prêts ouverts ;
    I4  un prêt ouvert au public porte toujours un numéro de pochette ;
    I5  un prêt clos ne porte plus de numéro de pochette (décision D5) ;
    I6  aucune date de retour antérieure à la date de sortie ;
    I7  intégrité SQLite et clés étrangères ;
    I8  les index UNIQUE du correctif de concurrence sont bien en place.

Lecture SEULE : ce script ne modifie jamais rien.

Usage (sur le VPS, ou sur une copie rapatriée) :
    python -m scripts.stress.coherence /var/lib/ludotex-formation/pret-jeux.db
    python -m scripts.stress.coherence            # utilise DATABASE_PATH du .env
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Numéro « factice » des sorties tournoi : pas une vraie pochette, donc exclu
# des contrôles d'unicité (voir services.NUMERO_TOURNOI).
NUMERO_TOURNOI = 0


def ouvrir(chemin: Path) -> sqlite3.Connection:
    """Ouvre la base en lecture seule si possible, sinon normalement."""
    try:
        conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    except sqlite3.OperationalError:
        conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# CONTRÔLES
# ---------------------------------------------------------------------------
def i1_prets_doubles(conn) -> list[str]:
    lignes = conn.execute(
        """
        SELECT id_exemplaire, COUNT(*) AS n
        FROM prets WHERE date_retour IS NULL
        GROUP BY id_exemplaire HAVING n > 1
        """
    ).fetchall()
    return [
        f"la boîte « {l['id_exemplaire']} » a {l['n']} prêts ouverts simultanés"
        for l in lignes
    ]


def i2_pochettes_partagees(conn) -> list[str]:
    lignes = conn.execute(
        """
        SELECT numero_pochette, COUNT(*) AS n,
               GROUP_CONCAT(id_exemplaire, ', ') AS boites
        FROM prets
        WHERE date_retour IS NULL AND numero_pochette IS NOT NULL
          AND numero_pochette != ?
        GROUP BY numero_pochette HAVING n > 1
        """,
        (NUMERO_TOURNOI,),
    ).fetchall()
    return [
        f"la pochette n°{l['numero_pochette']} est attribuée à {l['n']} boîtes "
        f"à la fois : {l['boites']}"
        for l in lignes
    ]


def i3_occupation(conn) -> list[str]:
    problemes = []
    fantomes = conn.execute(
        """
        SELECT p.numero_pochette FROM pochettes p
        WHERE p.occupe = 1 AND NOT EXISTS (
            SELECT 1 FROM prets
            WHERE date_retour IS NULL AND numero_pochette = p.numero_pochette
        )
        """
    ).fetchall()
    problemes += [
        f"la pochette n°{l['numero_pochette']} est marquée occupée alors "
        "qu'aucun prêt ne la détient (elle ne sera jamais réattribuée)"
        for l in fantomes
    ]
    libres_utilisees = conn.execute(
        """
        SELECT p.numero_pochette, COUNT(*) AS n FROM pochettes p
        JOIN prets ON prets.numero_pochette = p.numero_pochette
                  AND prets.date_retour IS NULL
        WHERE p.occupe = 0
        GROUP BY p.numero_pochette
        """
    ).fetchall()
    problemes += [
        f"la pochette n°{l['numero_pochette']} est marquée LIBRE alors que "
        f"{l['n']} prêt(s) ouvert(s) la détiennent (elle sera donnée deux fois)"
        for l in libres_utilisees
    ]
    return problemes


def i4_pret_sans_pochette(conn) -> list[str]:
    lignes = conn.execute(
        """
        SELECT id_pret, id_exemplaire FROM prets
        WHERE date_retour IS NULL AND motif = 'pret' AND numero_pochette IS NULL
        """
    ).fetchall()
    return [
        f"le prêt {l['id_pret']} (« {l['id_exemplaire']} ») est ouvert sans "
        "numéro de pochette : la pièce d'identité est introuvable"
        for l in lignes
    ]


def i5_pochette_apres_cloture(conn) -> list[str]:
    nombre = conn.execute(
        "SELECT COUNT(*) FROM prets "
        "WHERE date_retour IS NOT NULL AND numero_pochette IS NOT NULL"
    ).fetchone()[0]
    if nombre:
        return [
            f"{nombre} prêt(s) clos conservent un numéro de pochette "
            "(la purge de clôture, décision D5, n'a pas eu lieu)"
        ]
    return []


def i6_dates(conn) -> list[str]:
    lignes = conn.execute(
        "SELECT id_pret, date_sortie, date_retour FROM prets "
        "WHERE date_retour IS NOT NULL AND date_retour < date_sortie"
    ).fetchall()
    return [
        f"le prêt {l['id_pret']} est rendu ({l['date_retour']}) avant d'être "
        f"sorti ({l['date_sortie']})"
        for l in lignes
    ]


def i7_integrite(conn) -> list[str]:
    problemes = []
    for ligne in conn.execute("PRAGMA integrity_check").fetchall():
        if ligne[0] != "ok":
            problemes.append(f"intégrité SQLite : {ligne[0]}")
    for ligne in conn.execute("PRAGMA foreign_key_check").fetchall():
        problemes.append(f"clé étrangère orpheline : {tuple(ligne)}")
    return problemes


# Filets de sécurité posés par le correctif du 2026-08-02
# (models.SCHEMA_INDEXES_UNIQUES). Leur création peut ÉCHOUER en silence — par
# conception : `db._creer_index_uniques` avertit au journal et laisse
# l'application démarrer, car lever à cet endroit casserait aussi bien un
# démarrage qu'une restauration de sauvegarde en pleine soirée. Conséquence :
# une base peut tourner sans garde-fou sans que rien ne le rappelle. D'où ce
# contrôle.
INDEX_ATTENDUS = ("idx_prets_un_seul_ouvert", "idx_pochettes_un_seul_pret")


def i8_filets(conn) -> list[str]:
    presents = {
        ligne[0]
        for ligne in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    return [
        f"le filet « {nom} » est absent : soit la base est antérieure au "
        "correctif du 2026-08-02, soit sa création a été refusée au démarrage "
        "parce que la base contenait déjà une incohérence (voir I1/I2 "
        "ci-dessus, et le journal du service)"
        for nom in INDEX_ATTENDUS
        if nom not in presents
    ]


CONTROLES = [
    ("I1  deux prêts ouverts sur la même boîte", i1_prets_doubles),
    ("I2  numéro de pochette attribué deux fois", i2_pochettes_partagees),
    ("I3  cohérence de la table des pochettes", i3_occupation),
    ("I4  prêt ouvert sans numéro de pochette", i4_pret_sans_pochette),
    ("I5  numéro conservé après clôture (D5)", i5_pochette_apres_cloture),
    ("I6  retour antérieur à la sortie", i6_dates),
    ("I7  intégrité SQLite et clés étrangères", i7_integrite),
    ("I8  filets de sécurité (index UNIQUE) en place", i8_filets),
]


def resume(conn) -> str:
    ouverts = conn.execute(
        "SELECT COUNT(*) FROM prets WHERE date_retour IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
    pochettes = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(numero_pochette), 0), "
        "COALESCE(SUM(occupe), 0) FROM pochettes"
    ).fetchone()
    return (
        f"{total} prêts enregistrés dont {ouverts} en cours ; "
        f"{pochettes[0]} pochettes connues (n° max {pochettes[1]}, "
        f"{pochettes[2]} occupée(s))"
    )


def principal() -> int:
    if len(sys.argv) > 1:
        chemin = Path(sys.argv[1])
    else:
        chemin = Path(os.getenv("DATABASE_PATH", "data/pret-jeux.db"))
    if not chemin.exists():
        sys.exit(f"Base introuvable : {chemin}")

    conn = ouvrir(chemin)
    try:
        print(f"\nBase : {chemin}")
        for suffixe in ("-wal", "-shm"):
            annexe = chemin.with_name(chemin.name + suffixe)
            if annexe.exists():
                print(f"  {annexe.name} : {annexe.stat().st_size / 1024:.0f} Kio")
        print(f"  {resume(conn)}\n")

        anomalies = 0
        for libelle, controle in CONTROLES:
            problemes = controle(conn)
            if problemes:
                anomalies += len(problemes)
                print(f"  ✗ {libelle}")
                for ligne in problemes[:10]:
                    print(f"        {ligne}")
                if len(problemes) > 10:
                    print(f"        … et {len(problemes) - 10} autres")
            else:
                print(f"  ✓ {libelle}")
    finally:
        conn.close()

    print()
    if anomalies:
        print(f"⇒ {anomalies} anomalie(s). La base n'est pas dans un état sain.")
        return 1
    print("⇒ Tous les invariants sont tenus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
