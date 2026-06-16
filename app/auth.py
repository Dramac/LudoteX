"""
Authentification bénévole par jeton + limitation de débit (voir spec §8).

- Pas de comptes individuels : un unique JETON aléatoire long (`PRET_TOKEN`)
  autorise les écritures. Distribué via un lien d'activation `/acces?jeton=…`
  qui pose un cookie sur l'appareil.
- Séparation lecture/écriture : le catalogue, les fiches et les stats restent
  publics ; seuls `/pret/*` et `/scanner` exigent le jeton.
- Si aucun jeton n'est configuré (dev/local), l'accès est ouvert — un
  avertissement est émis au démarrage. EN PRODUCTION, définir `PRET_TOKEN`.
- Limitation de débit par IP sur l'activation, contre la force brute.
"""

from __future__ import annotations

import os
import secrets
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "jeton_pret"
_PLACEHOLDER = "remplacer_par_un_jeton_aleatoire_long"


def jeton_configure() -> str | None:
    """Le jeton attendu, ou None si non configuré (mode ouvert)."""
    valeur = (os.getenv("PRET_TOKEN") or "").strip()
    if not valeur or valeur == _PLACEHOLDER:
        return None
    return valeur


def acces_valide(request: Request) -> bool:
    """True si l'appareil présente un cookie correspondant au jeton (ou mode ouvert)."""
    attendu = jeton_configure()
    if attendu is None:
        return True  # aucun jeton défini -> accès ouvert (dev)
    presente = request.cookies.get(COOKIE_NAME, "")
    return bool(presente) and secrets.compare_digest(presente, attendu)


def exiger_jeton(request: Request) -> None:
    """Dépendance FastAPI : protège un écran d'écriture/bénévole."""
    if not acces_valide(request):
        raise HTTPException(status_code=403, detail="acces_reserve")


# ---------------------------------------------------------------------------
# Limitation de débit par IP (fenêtre glissante, en mémoire)
# ---------------------------------------------------------------------------
_tentatives: dict[str, list[float]] = {}


def trop_de_tentatives(ip: str, limite: int, fenetre: int = 60) -> bool:
    """
    Enregistre une tentative pour cette IP et indique si la limite (par fenêtre
    de `fenetre` secondes) est dépassée. En mémoire : suffisant pour un seul
    process uvicorn, à la charge attendue.
    """
    maintenant = time.time()
    recent = [t for t in _tentatives.get(ip, []) if maintenant - t < fenetre]
    recent.append(maintenant)
    _tentatives[ip] = recent
    return len(recent) > limite
