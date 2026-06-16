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
import time

from fastapi import HTTPException, Request

# Nom du cookie déposé sur l'appareil bénévole après activation.
COOKIE_NAME = "jeton_pret"
# Valeur d'exemple du .env.example : à considérer comme « non configuré ».
_PLACEHOLDER = "remplacer_par_un_jeton_aleatoire_long"


def jeton_configure() -> str | None:
    """
    Retourne le jeton attendu, ou None si aucun n'est réellement configuré.

    None déclenche le MODE OUVERT (accès non protégé). On considère comme « non
    configuré » : variable absente, vide, ou laissée à la valeur placeholder.

    Returns:
        Le jeton (str) si configuré, sinon None.
    """
    valeur = (os.getenv("PRET_TOKEN") or "").strip()
    if not valeur or valeur == _PLACEHOLDER:
        return None
    return valeur


def acces_valide(request: Request) -> bool:
    """
    Indique si la requête est autorisée à accéder aux écrans bénévole.

    Règle : en mode ouvert (pas de jeton configuré) → True. Sinon, le cookie de
    l'appareil doit être présent ET égal au jeton attendu (comparaison en temps
    constant).

    Args:
        request: la requête entrante (on y lit le cookie).

    Returns:
        True si l'accès est autorisé, False sinon.
    """
    attendu = jeton_configure()
    if attendu is None:
        return True  # mode ouvert (dev)
    presente = request.cookies.get(COOKIE_NAME, "")
    return bool(presente) and secrets.compare_digest(presente, attendu)


def exiger_jeton(request: Request) -> None:
    """
    Dépendance FastAPI protégeant un écran d'écriture/bénévole.

    À brancher via ``Depends(exiger_jeton)`` sur une route. Si l'accès n'est pas
    valide, lève une HTTPException 403 ; app/main.py intercepte ce 403 pour
    afficher la page « accès réservé » (acces_refuse.html) plutôt qu'une erreur
    brute.

    Raises:
        HTTPException: 403 si l'appareil n'a pas activé l'accès.
    """
    if not acces_valide(request):
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
