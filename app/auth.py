"""
Authentification bénévole par jeton + limitation de débit (voir spec §8).

MODÈLE D'ACCÈS (volontairement simple)
--------------------------------------
- PAS de comptes individuels (ni login, ni mot de passe par personne).
- La seule distinction est : PUBLIC (lecture) vs BÉNÉVOLE (écriture).
- Un unique secret partagé, le JETON (`PRET_TOKEN`), autorise les écritures. Il
  est distribué via un lien d'activation `/acces?jeton=…` (voir routes/acces.py)
  qui pose un cookie sur l'appareil. Ensuite, l'appareil est « reconnu ».
- Ce qui est protégé : `/pret/*` et `/scanner`. Ce qui reste public : catalogue,
  fiches, statistiques.

MODE OUVERT (DÉVELOPPEMENT)
--------------------------
Si aucun jeton n'est configuré (`PRET_TOKEN` absent ou laissé au placeholder du
.env.example), `acces_valide` renvoie toujours True → accès ouvert. Pratique en
local. app/main.py émet un avertissement au démarrage dans ce cas. EN
PRODUCTION, définir impérativement `PRET_TOKEN`.

SÉCURITÉ
--------
- Comparaison en TEMPS CONSTANT (`secrets.compare_digest`) pour ne pas fuiter
  d'information par le temps de réponse.
- Limitation de débit par IP sur l'activation (voir `trop_de_tentatives`), comme
  garde-fou « ceinture et bretelles » contre la force brute.
- Rotation : changer `PRET_TOKEN` invalide tous les anciens cookies.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from app import admin_auth
from app.db import get_connection

# Durée de validité par défaut du jeton si aucune date de fin n'est choisie.
DUREE_DEFAUT_JOURS = 7

# Nom du cookie déposé sur l'appareil bénévole après activation.
COOKIE_NAME = "jeton_pret"
# Valeur d'exemple du .env.example : à considérer comme « non configuré ».
_PLACEHOLDER = "remplacer_par_un_jeton_aleatoire_long"


def jeton_actuel(conn: sqlite3.Connection) -> str | None:
    """
    Retourne le jeton bénévole en vigueur, ou None (mode ouvert).

    Priorité : la valeur stockée en base (table `parametres`, clé "pret_token",
    posée lors d'une réinitialisation depuis l'admin) ; à défaut, la variable
    d'environnement `PRET_TOKEN` (amorçage via .env). Une valeur vide ou égale au
    placeholder est ignorée → mode ouvert.

    Args:
        conn: connexion SQLite ouverte.

    Returns:
        Le jeton (str) si configuré, sinon None.
    """
    row = conn.execute(
        "SELECT valeur FROM parametres WHERE cle = 'pret_token'"
    ).fetchone()
    if row and row[0] and row[0] != _PLACEHOLDER:
        return row[0]
    env = (os.getenv("PRET_TOKEN") or "").strip()
    if env and env != _PLACEHOLDER:
        return env
    return None


def expiration_jeton(conn: sqlite3.Connection) -> str | None:
    """Date d'expiration du jeton (UTC ISO) stockée en base, ou None (pas d'expiration)."""
    row = conn.execute(
        "SELECT valeur FROM parametres WHERE cle = 'pret_token_expire'"
    ).fetchone()
    return row[0] if row and row[0] else None


def jeton_expire(conn: sqlite3.Connection) -> bool:
    """True si une date d'expiration est définie ET dépassée."""
    e = expiration_jeton(conn)
    if not e:
        return False
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(e)
    except ValueError:
        return False


def reinitialiser_jeton(conn: sqlite3.Connection,
                        expire_iso: str | None = None) -> str:
    """
    Génère un nouveau jeton aléatoire + sa date d'expiration, et le renvoie.

    Effet : invalide immédiatement tous les anciens cookies (le jeton change).
    Si `expire_iso` est None, on applique la durée par défaut (DUREE_DEFAUT_JOURS).

    Args:
        conn: connexion SQLite ouverte.
        expire_iso: date de fin de validité (UTC ISO), ou None → défaut 1 semaine.

    Returns:
        Le nouveau jeton (à diffuser via le lien d'activation).
    """
    nouveau = secrets.token_urlsafe(32)
    if not expire_iso:
        expire_iso = (datetime.now(timezone.utc)
                      + timedelta(days=DUREE_DEFAUT_JOURS)).isoformat(timespec="seconds")
    for cle, valeur in (("pret_token", nouveau), ("pret_token_expire", expire_iso)):
        conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (cle, valeur),
        )
    conn.commit()
    return nouveau


def acces_valide(request: Request) -> bool:
    """
    Indique si la requête est autorisée à accéder aux écrans bénévole.

    Règles :
    - aucun jeton configuré → accès OUVERT (mode dev) ;
    - jeton configuré mais EXPIRÉ → accès FERMÉ (refusé) ;
    - sinon, le cookie de l'appareil doit égaler le jeton (comparaison en temps
      constant).

    Args:
        request: la requête entrante (on y lit le cookie).

    Returns:
        True si l'accès est autorisé, False sinon.
    """
    conn = get_connection()
    try:
        attendu = jeton_actuel(conn)
        expire = jeton_expire(conn)
    finally:
        conn.close()
    if attendu is None:
        return True   # mode ouvert (dev)
    if expire:
        return False  # jeton expiré → fermé
    presente = request.cookies.get(COOKIE_NAME, "")
    return bool(presente) and secrets.compare_digest(presente, attendu)


def peut_ecrire(request: Request) -> bool:
    """
    Autorisé à accéder aux écrans bénévole (prêt / retour / scanner) ?

    Vrai si l'appareil a activé le **jeton bénévole**, OU si une **session admin**
    est ouverte — un administrateur connecté accède directement aux écrans de
    prêt sans avoir à activer le jeton. On teste l'admin d'abord (session en
    mémoire, sans accès base).

    Args:
        request: la requête entrante.

    Returns:
        True si l'accès est autorisé.
    """
    return admin_auth.admin_connecte(request) or acces_valide(request)


def exiger_jeton(request: Request) -> None:
    """
    Dépendance FastAPI protégeant un écran d'écriture/bénévole.

    À brancher via ``Depends(exiger_jeton)`` sur une route. Accès accordé au
    bénévole (jeton) OU à l'admin connecté (voir `peut_ecrire`). Sinon, lève une
    HTTPException 403 ; app/main.py affiche la page « accès réservé ».

    Raises:
        HTTPException: 403 si ni jeton bénévole ni session admin.
    """
    if not peut_ecrire(request):
        raise HTTPException(status_code=403, detail="acces_reserve")


# ---------------------------------------------------------------------------
# Limitation de débit par IP (fenêtre glissante, en mémoire)
# ---------------------------------------------------------------------------
# Dictionnaire { adresse_ip : [horodatages des tentatives récentes] }.
# Stocké en mémoire du process : suffisant pour un seul worker uvicorn à la
# charge attendue. Avec plusieurs workers, prévoir un store partagé (Redis…).
_tentatives: dict[str, list[float]] = {}


def trop_de_tentatives(ip: str, limite: int, fenetre: int = 60) -> bool:
    """
    Enregistre une tentative pour `ip` et dit si la limite est dépassée.

    Implémente une fenêtre glissante : on ne garde que les tentatives des
    `fenetre` dernières secondes, on ajoute la tentative courante, puis on
    compare le total à `limite`.

    Args:
        ip: adresse IP de l'appelant.
        limite: nombre maximal de tentatives autorisées dans la fenêtre.
        fenetre: durée de la fenêtre en secondes (60 par défaut).

    Returns:
        True si le nombre de tentatives dans la fenêtre dépasse `limite`.
    """
    maintenant = time.time()
    recent = [t for t in _tentatives.get(ip, []) if maintenant - t < fenetre]
    recent.append(maintenant)
    _tentatives[ip] = recent
    return len(recent) > limite
