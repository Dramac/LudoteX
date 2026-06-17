"""
Authentification ADMINISTRATEUR par mot de passe (distincte du jeton bénévole).

Différences avec app/auth.py (jeton bénévole) :
- Ici il s'agit d'un vrai MOT DE PASSE, haché et stocké en base (table
  `parametres`, clé "admin_hash"), modifiable depuis l'écran d'administration.
- Après connexion réussie, on ouvre une SESSION (identifiant aléatoire en
  mémoire + cookie), avec expiration.

AMORÇAGE (premier mot de passe)
-------------------------------
Si aucun hash n'existe encore en base, on initialise à partir de la variable
d'environnement `ADMIN_PASSWORD` (lue une seule fois, puis hachée et stockée).
Ensuite, le mot de passe se change dans l'application. Si ni hash ni
`ADMIN_PASSWORD` ne sont définis, l'admin est « non configuré » (login refusé).

SÉCURITÉ
--------
- Hachage pbkdf2_hmac (bibliothèque standard, pas de dépendance externe), avec
  sel aléatoire et nombre d'itérations élevé. Comparaison en temps constant.
- Sessions en mémoire du process (suffisant pour un seul worker uvicorn ; avec
  plusieurs workers, prévoir un store partagé). Cookie HttpOnly + SameSite.
- Limitation de débit du login réutilisée depuis app/auth.trop_de_tentatives.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from fastapi import Request

# Cookie de session admin et durée de vie d'une session (8 h).
COOKIE_ADMIN = "admin_session"
DUREE_SESSION = 8 * 60 * 60

# Paramètres du hachage pbkdf2.
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


# ---------------------------------------------------------------------------
# Hachage du mot de passe (format : "pbkdf2_sha256$iters$sel_hex$hash_hex")
# ---------------------------------------------------------------------------
def hacher_mdp(mot_de_passe: str) -> str:
    """Hache un mot de passe avec un sel aléatoire ; renvoie une chaîne stockable."""
    sel = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), sel, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${sel.hex()}${dk.hex()}"


def verifier_mdp(mot_de_passe: str, stocke: str) -> bool:
    """Vérifie un mot de passe contre sa forme stockée (comparaison temps constant)."""
    try:
        algo, iters, sel_hex, hash_hex = stocke.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", mot_de_passe.encode(), bytes.fromhex(sel_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Stockage du hash en base (table parametres)
# ---------------------------------------------------------------------------
def get_admin_hash(conn: sqlite3.Connection) -> str | None:
    """Renvoie le hash du mot de passe admin stocké, ou None."""
    row = conn.execute(
        "SELECT valeur FROM parametres WHERE cle = 'admin_hash'"
    ).fetchone()
    return row[0] if row else None


def set_admin_hash(conn: sqlite3.Connection, hash_mdp: str) -> None:
    """Enregistre (ou remplace) le hash du mot de passe admin."""
    conn.execute(
        "INSERT INTO parametres (cle, valeur) VALUES ('admin_hash', ?) "
        "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (hash_mdp,),
    )
    conn.commit()


def assurer_admin_hash(conn: sqlite3.Connection) -> str | None:
    """
    Renvoie le hash courant ; l'initialise depuis ADMIN_PASSWORD si nécessaire.

    Returns:
        Le hash (str) si l'admin est configuré, sinon None.
    """
    h = get_admin_hash(conn)
    if h:
        return h
    env = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if env:
        h = hacher_mdp(env)
        set_admin_hash(conn, h)
        return h
    return None


def admin_configure(conn: sqlite3.Connection) -> bool:
    """True si un mot de passe admin est défini (en base ou via ADMIN_PASSWORD)."""
    return assurer_admin_hash(conn) is not None


def verifier_identifiants(conn: sqlite3.Connection, mot_de_passe: str) -> bool:
    """Vérifie le mot de passe admin saisi au login."""
    h = assurer_admin_hash(conn)
    return h is not None and verifier_mdp(mot_de_passe, h)


def changer_mot_de_passe(conn: sqlite3.Connection, ancien: str, nouveau: str) -> bool:
    """
    Change le mot de passe admin si l'ancien est correct et le nouveau non vide.

    Returns:
        True si le changement a eu lieu, False sinon.
    """
    if not nouveau or not nouveau.strip():
        return False
    if not verifier_identifiants(conn, ancien):
        return False
    set_admin_hash(conn, hacher_mdp(nouveau))
    return True


# ---------------------------------------------------------------------------
# Sessions admin (en mémoire : { id_session: instant_d_expiration })
# ---------------------------------------------------------------------------
_sessions: dict[str, float] = {}


def ouvrir_session() -> str:
    """Crée une session et renvoie son identifiant (à poser en cookie)."""
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = time.time() + DUREE_SESSION
    return sid


def session_valide(sid: str | None) -> bool:
    """Indique si l'identifiant de session existe et n'est pas expiré."""
    if not sid:
        return False
    expire = _sessions.get(sid)
    if expire is None:
        return False
    if time.time() > expire:           # expirée : on nettoie
        _sessions.pop(sid, None)
        return False
    return True


def fermer_session(sid: str | None) -> None:
    """Invalide une session (déconnexion)."""
    if sid:
        _sessions.pop(sid, None)


def admin_connecte(request: Request) -> bool:
    """Raccourci : la requête porte-t-elle un cookie de session admin valide ?"""
    return session_valide(request.cookies.get(COOKIE_ADMIN))
