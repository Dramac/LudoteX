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

# Identifiant d'APPAREIL associé à chaque session ({ id_session: appareil }),
# voir docs/conception-journal.md §4.5. Dictionnaire SÉPARÉ plutôt qu'une
# valeur composite dans `_sessions` : la valeur de `_sessions` est un instant
# d'expiration lu tel quel par `session_valide`, et la mêler à autre chose
# obligerait à toucher à la seule fonction qui garde la porte de l'admin.
#
# POURQUOI EN MÉMOIRE. Une session admin ne peut pas être suivie en base : elle
# vit dans ce process et un redémarrage les ferme TOUTES. Plutôt que d'afficher
# un statut faux avec une note d'excuse, la liste des postes d'administration
# encore ouverts se lit ici — et elle est exacte, y compris après un
# redémarrage, où elle est vide, ce qui est la vérité.
_appareils: dict[str, str] = {}


# Intervalle MINIMAL entre deux balayages de fond, et instant du dernier.
# Même mécanique que `app/auth.py` — voir `_balayer` ci-dessous pour le détail
# et pour la raison de la duplication.
INTERVALLE_BALAYAGE_S = 300.0
_dernier_balayage = 0.0


def _balayer(maintenant: float) -> None:
    """
    Purge les sessions expirées, et les appareils qui n'en ont plus.

    POURQUOI (ROB-05, audit du 24/07). `session_valide` ne nettoie que le
    `sid` qu'on lui présente : une session jamais rouverte — le cas normal,
    quelqu'un ferme son navigateur sans se déconnecter — reste en mémoire
    jusqu'au redémarrage du service. Fuite lente et théorique, gratuite à
    corriger.

    AMORTI, au plus une fois par `INTERVALLE_BALAYAGE_S`, à l'accès : aucune
    tâche de fond à démarrer ni à arrêter (même raisonnement que dans
    `app/auth.py`, qui détaille le choix).

    DUPLICATION ASSUMÉE avec `auth._balayer` : `app/auth.py` importe DÉJÀ ce
    module (`from app import admin_auth`), donc factoriser dans l'un ou
    l'autre créerait un cycle d'import. Les deux balayages ne partagent
    d'ailleurs que leur cadence : ici on compare des échéances stockées, là
    des horodatages de tentatives sur une fenêtre glissante. C'est le même cas
    de figure qu'`_ics_horodatage` (dupliqué entre tournoi et planning), pas
    celui de `services.transaction` (importée plutôt que recopiée).

    Aucun effet observable : ces sessions étaient déjà refusées par
    `session_valide`, on cesse seulement d'en garder la trace.
    """
    global _dernier_balayage
    if maintenant - _dernier_balayage < INTERVALLE_BALAYAGE_S:
        return
    _dernier_balayage = maintenant
    for sid in [s for s, expire in _sessions.items() if maintenant > expire]:
        _sessions.pop(sid, None)
        _appareils.pop(sid, None)
    # Filet : un appareil dont la session a disparu par un autre chemin ne
    # doit pas survivre seul (`appareils_admin_ouverts` le filtrerait, mais
    # il occuperait quand même la mémoire).
    for sid in [s for s in _appareils if s not in _sessions]:
        _appareils.pop(sid, None)


def ouvrir_session(appareil: str | None = None) -> str:
    """
    Crée une session et renvoie son identifiant (à poser en cookie).

    Args:
        appareil: identifiant d'appareil de la personne qui se connecte, s'il
            est connu (voir `services.COOKIE_APPAREIL`). Mémorisé À CÔTÉ de la
            session pour que `/admin/jeton` puisse dire quels postes
            d'administration sont encore ouverts.
    """
    maintenant = time.time()
    _balayer(maintenant)
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = maintenant + DUREE_SESSION
    if appareil:
        _appareils[sid] = appareil
    return sid


def appareils_admin_ouverts() -> set[str]:
    """
    Identifiants des appareils dont une session admin est ENCORE ouverte.

    Les sessions expirées sont ignorées (et nettoyées au passage par
    `session_valide`). Ensemble vide après un redémarrage du service : c'est le
    comportement voulu, pas une lacune.
    """
    return {
        appareil for sid, appareil in list(_appareils.items())
        if session_valide(sid)
    }


def session_valide(sid: str | None) -> bool:
    """Indique si l'identifiant de session existe et n'est pas expiré."""
    if not sid:
        return False
    maintenant = time.time()
    _balayer(maintenant)
    expire = _sessions.get(sid)
    if expire is None:
        return False
    if maintenant > expire:            # expirée : on nettoie
        _sessions.pop(sid, None)
        _appareils.pop(sid, None)
        return False
    return True


def fermer_session(sid: str | None) -> None:
    """Invalide une session (déconnexion)."""
    if sid:
        _sessions.pop(sid, None)
        _appareils.pop(sid, None)


def admin_connecte(request: Request) -> bool:
    """Raccourci : la requête porte-t-elle un cookie de session admin valide ?"""
    return session_valide(request.cookies.get(COOKIE_ADMIN))
