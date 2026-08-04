"""
Lecture du journal d'activité en terminal.

Dans l'esprit de scripts/import_csv.py et scripts/generate_qr.py : stdlib
uniquement, exécutable par `python -m scripts.journal`. Réutilise
`app.journal.lire_dernieres_lignes` (lecture par blocs depuis la fin, jamais
tout le fichier) et `app.journal.formater_console` (même mise en forme
humaine que le StreamHandler console de l'application — un seul endroit qui
décide du format lisible).

Usage :
    python -m scripts.journal                      # les 50 dernières lignes, formatées
    python -m scripts.journal -f                    # suivi en direct (équivalent tail -f)
    python -m scripts.journal -f --module pret       # ... filtré
    python -m scripts.journal --qui admin --depuis 12:00
    python -m scripts.journal --brut | jq .          # passe-plat JSON

Le suivi (-f) n'est PAS couvert par la suite automatisée (pas de process
persistant simple à tester sous pytest, et la conception le dit explicitement
non testable de façon simple). Vérification MANUELLE documentée : lancer
`python -m scripts.journal -f` dans un terminal, produire des lignes depuis
une autre fenêtre (une action bénévole/admin une fois le lot C livré, ou un
appel direct à `journal.journaliser()` depuis un shell Python), constater
qu'elles apparaissent ; puis renommer le fichier suivi (`mv data/journal.log
data/journal.log.old`) pour simuler une rotation et écrire une nouvelle ligne
dans un fichier recréé au même chemin : le suivi doit continuer sur le
NOUVEAU fichier (inode différent), pas rester collé sur l'ancien.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from datetime import time as heure_du_jour
from pathlib import Path

# Permet « python scripts/journal.py » comme « python -m scripts.journal ».
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.journal import formater_console, lire_dernieres_lignes  # noqa: E402
from app.services import FUSEAU_LOCAL  # noqa: E402

DEFAUT_LIMITE = 50


def chemin_journal() -> Path:
    """Chemin du fichier, lu dans l'environnement (comme le reste de l'app)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - python-dotenv toujours présent en pratique
        pass
    return Path(os.getenv("JOURNAL_PATH", "data/journal.log"))


def charger_lignes(chemin: Path, limite: int = DEFAUT_LIMITE) -> list[dict]:
    """Lit les `limite` dernières lignes et les parse. Une ligne illisible
    (rotation en cours d'écriture) est ignorée en silence, comme sur l'écran
    admin."""
    lignes = []
    for texte in lire_dernieres_lignes(chemin, limite):
        try:
            lignes.append(json.loads(texte))
        except ValueError:
            continue
    return lignes


def filtrer(lignes: list[dict], module: str | None = None, qui: str | None = None,
            depuis: datetime | None = None) -> list[dict]:
    """
    Args:
        depuis: instant (datetime AVEC fuseau) à partir duquel garder les
            lignes, comparé au champ `t` de chacune (qui porte lui-même un
            décalage local explicite, §3.1 de la conception).
    """
    def _correspond(ligne: dict) -> bool:
        if module and ligne.get("module") != module:
            return False
        if qui and ligne.get("qui") != qui:
            return False
        if depuis is not None:
            try:
                if datetime.fromisoformat(ligne.get("t") or "") < depuis:
                    return False
            except ValueError:
                return False
        return True

    return [l for l in lignes if _correspond(l)]


def parser_depuis(valeur: str) -> datetime:
    """
    '12:00' -> aujourd'hui à 12:00, dans le fuseau local (même horloge que
    celle qui écrit les lignes). Lève ValueError si le format est incorrect —
    à `main()` de le traduire en message clair plutôt qu'une trace brute.
    """
    heure = heure_du_jour.fromisoformat(valeur)
    aujourdhui = datetime.now(FUSEAU_LOCAL).date()
    return datetime.combine(aujourdhui, heure, tzinfo=FUSEAU_LOCAL)


def afficher(lignes: list[dict], brut: bool) -> None:
    for ligne in lignes:
        if brut:
            # Passe-plat JSON : on réémet exactement ce qui a été lu, pour
            # que `--brut | jq .` retrouve les mêmes champs qu'en base.
            print(json.dumps(ligne, ensure_ascii=False, separators=(",", ":")))
        else:
            print(formater_console(ligne))


# ---------------------------------------------------------------------------
# Suivi en direct (-f)
# ---------------------------------------------------------------------------
def suivre(chemin: Path, module: str | None, qui: str | None,
           depuis: datetime | None, brut: bool) -> None:
    """
    Équivalent de `tail -f`, filtré. Survit à une rotation
    (RotatingFileHandler RENOMME l'ancien fichier puis en recrée un nouveau
    au même chemin — l'inode change) : on surveille `st_ino` et on rouvre dès
    qu'il diffère de celui qu'on suit.
    """
    inode = None
    fh = None
    try:
        while True:
            try:
                stat = chemin.stat()
            except OSError:
                time.sleep(1)
                continue

            if fh is None or stat.st_ino != inode:
                if fh:
                    fh.close()
                fh = open(chemin, "r", encoding="utf-8", errors="ignore")
                fh.seek(0, os.SEEK_END)  # ne montre que ce qui arrive à partir de maintenant
                inode = stat.st_ino

            position = fh.tell()
            texte = fh.readline()
            if not texte or not texte.endswith("\n"):
                # Rien de nouveau, OU une ligne encore à moitié écrite : on
                # revient au début de cette ligne et on retentera plus tard.
                fh.seek(position)
                time.sleep(0.5)
                continue

            try:
                ligne = json.loads(texte)
            except ValueError:
                continue
            if filtrer([ligne], module, qui, depuis):
                afficher([ligne], brut)
    except KeyboardInterrupt:
        pass
    finally:
        if fh:
            fh.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Lecture du journal d'activité de LudoteX.")
    p.add_argument("-n", "--nombre", type=int, default=DEFAUT_LIMITE,
                   help=f"Nombre de lignes à afficher (défaut {DEFAUT_LIMITE}).")
    p.add_argument("-f", "--suivre", action="store_true",
                   help="Suivi en direct (équivalent tail -f), survit à une rotation.")
    p.add_argument("--module", help="Ne montrer que ce module (pret, tournois, admin…).")
    p.add_argument("--qui",
                   help="Ne montrer que ce type de visiteur "
                        "(visiteur, benevole, admin, indetermine).")
    p.add_argument("--depuis", metavar="HH:MM",
                   help="Ne montrer qu'à partir de cette heure du jour.")
    p.add_argument("--brut", action="store_true",
                   help="Sortie JSON brute, une ligne par entrée (pour `| jq .`).")
    args = p.parse_args()

    depuis = None
    if args.depuis:
        try:
            depuis = parser_depuis(args.depuis)
        except ValueError:
            raise SystemExit(f"--depuis invalide : {args.depuis!r} (format attendu HH:MM).")

    chemin = chemin_journal()

    if args.suivre:
        suivre(chemin, args.module, args.qui, depuis, args.brut)
        return

    lignes = filtrer(charger_lignes(chemin, args.nombre), args.module, args.qui, depuis)
    if not lignes:
        print("Aucune activité enregistrée pour l'instant.", file=sys.stderr)
        return
    afficher(lignes, args.brut)


if __name__ == "__main__":
    main()
