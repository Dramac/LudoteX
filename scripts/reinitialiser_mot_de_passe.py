"""
Remplace le mot de passe de l'espace /admin — le recours à un mot de passe oublié.

POURQUOI CE SCRIPT (lot-6-pré-production, `DOC-01`)
----------------------------------------------------
Le mot de passe admin vit en base, haché (`parametres.admin_hash`).
`ADMIN_PASSWORD` n'est lue qu'une fois, quand aucun hash n'existe : la modifier
dans `.env` ne change RIEN une fois l'application démarrée. Sans ce script,
un oubli fermait `/admin` pour de bon — donc aussi le seul moyen de relancer
l'accès des bénévoles.

Aucun redémarrage n'est nécessaire : l'application relit le hash en base à
chaque connexion (`admin_auth.verifier_identifiants`). Les sessions déjà
ouvertes restent ouvertes jusqu'à leur échéance.

USAGE, SUR LE SERVEUR (sauvegarder d'abord)
-------------------------------------------
Production, depuis le dossier d'installation :

    cd /opt/ludotex
    sudo -u pretjeux .venv/bin/python scripts/reinitialiser_mot_de_passe.py

Instance de formation — son environnement est le fichier que lit systemd, pas
le `.env` du dossier, d'où l'option `--env` :

    sudo -u pretjeux .venv/bin/python scripts/reinitialiser_mot_de_passe.py \\
        --env /etc/ludotex-formation.env

Le script affiche la base qu'il va modifier et demande confirmation, puis lit
le nouveau mot de passe au clavier, SANS ÉCHO, deux fois.

`--stdin` (réservé à `deploy/install.sh`) lit le mot de passe sur l'entrée
standard, sans question ; `--si-absent` n'écrit que s'il n'existe encore aucun
mot de passe.

GARDE-FOUS
----------
- Le mot de passe n'est JAMAIS un argument de ligne de commande (historique du
  shell, liste des processus), ni affiché, ni journalisé.
- BASE FANTÔME : `sqlite3.connect` sur un chemin absent crée un fichier vide.
  Une base absente est donc REFUSÉE avant toute ouverture, et la base est
  ouverte en `mode=rw`, qui échoue au lieu de créer. Un chemin faux (mauvais
  dossier courant, `.env` introuvable) donne un message, jamais une base neuve
  à côté de la vraie.
- Même règle de longueur que l'écran (`admin_auth.LONGUEUR_MIN_MDP`), et la
  valeur d'exemple de `.env.example` est refusée.

QUELLE BASE EST VISÉE
---------------------
Celle de `DATABASE_PATH`, résolue EXACTEMENT comme l'application la résout
(`app.db.get_database_path`), après chargement :
1. du fichier `--env`, s'il est donné, qui l'EMPORTE sur tout le reste — et
   qui devient le SEUL fichier d'environnement du processus : le `.env` du
   dossier d'installation n'est pas relu derrière lui (`LUDOTEX_ENV_FILE`,
   voir `app/environnement.py`), comme pour l'instance de formation elle-même ;
2. sinon, du fichier d'environnement de l'instance selon `app/environnement.py`
   — le `.env` du dossier d'installation, quel que soit le dossier courant.
Un chemin relatif se lit, lui, depuis le dossier COURANT : d'où `cd /opt/ludotex`
dans l'usage ci-dessus, et le chemin absolu affiché avant toute écriture.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Codes de sortie, testés.
OK = 0
REFUS = 1          # saisie refusée (trop court, exemple, confirmation), abandon
BASE_ABSENTE = 2   # rien n'a été ouvert ni créé
ERREUR = 3         # base illisible ou d'un autre format


def _charger_env(fichier: str | None) -> None:
    """
    Charge `--env` en priorité (override), avant tout import d'un module d'`app`
    qui lit l'environnement, puis en fait le fichier de l'instance : sans cela,
    `app` compléterait les clés absentes avec le `.env` de la production.
    """
    if not fichier:
        return
    from dotenv import load_dotenv

    from app.environnement import VARIABLE_FICHIER_ENV

    chemin = Path(fichier).resolve()
    if not chemin.is_file():
        raise FileNotFoundError(fichier)
    load_dotenv(chemin, override=True)
    os.environ[VARIABLE_FICHIER_ENV] = str(chemin)


def _ouvrir_sans_creer(chemin: Path) -> sqlite3.Connection:
    """Ouvre une base EXISTANTE en lecture-écriture ; échoue plutôt que de créer."""
    return sqlite3.connect(f"file:{chemin}?mode=rw", uri=True)


def _lire_mdp(stdin: bool) -> str | None:
    """Lit le nouveau mot de passe ; None si la confirmation ne correspond pas."""
    if stdin:
        return sys.stdin.readline().rstrip("\n")
    premier = getpass.getpass("Nouveau mot de passe admin : ")
    second = getpass.getpass("Confirmer le nouveau mot de passe : ")
    return premier if premier == second else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remplace le mot de passe de l'espace /admin (mot de passe oublié)."
    )
    parser.add_argument(
        "--env", metavar="FICHIER",
        help="fichier d'environnement de l'instance visée "
             "(ex. /etc/ludotex-formation.env) ; l'emporte sur le .env",
    )
    parser.add_argument(
        "--stdin", action="store_true",
        help="lire le mot de passe sur l'entrée standard, sans question "
             "(usage : deploy/install.sh)",
    )
    parser.add_argument(
        "--si-absent", action="store_true",
        help="ne rien écrire si un mot de passe est déjà défini",
    )
    args = parser.parse_args(argv)

    try:
        _charger_env(args.env)
    except FileNotFoundError:
        print(f"Fichier d'environnement introuvable : {args.env}", file=sys.stderr)
        print("Rien n'a été modifié.", file=sys.stderr)
        return BASE_ABSENTE

    # Imports APRÈS `_charger_env` : `app.db` lit DATABASE_PATH à l'appel de
    # `get_database_path`, mais charge aussi le `.env` à l'import, sans écraser
    # ce qui est déjà défini — donc sans écraser `--env`.
    from app import admin_auth
    from app.db import get_database_path

    chemin = Path(get_database_path()).resolve()
    print(f"Base visée : {chemin}")
    if not chemin.is_file():
        print("Cette base n'existe pas. Rien n'a été créé ni modifié.", file=sys.stderr)
        print("Vérifier le dossier courant (cd /opt/ludotex) et, pour la formation, "
              "l'option --env.", file=sys.stderr)
        return BASE_ABSENTE

    try:
        conn = _ouvrir_sans_creer(chemin)
    except sqlite3.Error as exc:
        print(f"Base illisible ({type(exc).__name__}). Rien n'a été modifié.",
              file=sys.stderr)
        return ERREUR
    try:
        try:
            deja = admin_auth.get_admin_hash(conn) is not None
        except sqlite3.Error as exc:
            # Pas de table `parametres` : ce n'est pas une base de prêt.
            print(f"Ce fichier n'est pas une base de prêt LudoteX "
                  f"({type(exc).__name__}). Rien n'a été modifié.", file=sys.stderr)
            return ERREUR

        if args.si_absent and deja:
            print("Un mot de passe admin est déjà défini : il est conservé.")
            print("Pour le changer : relancer ce script sans --si-absent.")
            return OK

        if not args.stdin:
            etat = "sera REMPLACÉ" if deja else "sera défini"
            print(f"Le mot de passe admin de cette base {etat}.")
            if input("Continuer ? [o/N] : ").strip().lower() not in ("o", "oui"):
                print("Abandon. Rien n'a été modifié.")
                return REFUS

        nouveau = _lire_mdp(args.stdin)
        if nouveau is None:
            print("Les deux saisies ne correspondent pas. Rien n'a été modifié.",
                  file=sys.stderr)
            return REFUS
        motif = admin_auth.motif_refus_mdp(nouveau)
        if motif == "trop_court":
            print(f"Mot de passe trop court ({admin_auth.LONGUEUR_MIN_MDP} caractères "
                  "minimum). Rien n'a été modifié.", file=sys.stderr)
            return REFUS
        if motif == "exemple":
            print("Ce mot de passe est la valeur d'exemple de .env.example : refusé. "
                  "Rien n'a été modifié.", file=sys.stderr)
            return REFUS

        # `set_admin_hash` committe lui-même.
        admin_auth.set_admin_hash(conn, admin_auth.hacher_mdp(nouveau))
    finally:
        conn.close()

    print("Mot de passe admin remplacé. Aucun redémarrage nécessaire.")
    return OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbandon. Rien n'a été modifié.")
        sys.exit(REFUS)
