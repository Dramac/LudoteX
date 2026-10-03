"""
Activation de l'accès bénévole — pose le cookie de jeton (voir spec §8).

Le lien `/acces?jeton=<JETON>` est distribué aux bénévoles via le canal interne.
En l'ouvrant, l'appareil mémorise le jeton dans un cookie et peut ensuite
accéder à /pret et /scanner tant que le jeton est valable.

Le cookie vit `auth.DUREE_COOKIE_JETON` (400 jours), indépendamment de
l'échéance du jeton, et il est reposé à chaque requête autorisée : c'est ce qui
permet de PROLONGER le jeton sans que les téléphones déjà activés se coupent à
l'ancienne échéance (voir le commentaire de la constante). L'échéance, elle,
est vérifiée par le serveur à chaque requête.

Rotation du jeton = réinitialisation depuis /admin/jeton (ou changer
`PRET_TOKEN`) : les anciens cookies cessent d'être valides, quelle que soit
leur durée de vie.

Sécurité : limitation de débit par IP (anti-force brute) et comparaison du jeton
en temps constant. Le cookie est HttpOnly (inaccessible au JS), SameSite=Lax, et
Secure dès que la connexion est en HTTPS.

IDENTIFIANT D'APPAREIL (docs/conception-journal.md §4)
-----------------------------------------------------
L'activation pose un SECOND cookie, `appareil` : six caractères tirés au hasard
qui disent « c'est le même téléphone », jamais « c'est le téléphone de Marie ».
C'est l'un des deux seuls endroits où il est posé (l'autre est la connexion
admin) — autrement dit, uniquement pour les personnes qui écrivent. Un visiteur
qui consulte le catalogue ne reçoit rien.
"""

import os

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import auth, services
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["acces"])


def poser_cookies_benevole(reponse: Response, request: Request, jeton: str,
                           appareil: str | None) -> None:
    """
    Pose (ou repose) le cookie de jeton et, si fourni, le cookie d'appareil.

    SEUL domicile des attributs de ces deux cookies : l'activation (/acces) et
    le rafraîchissement du middleware (`app/main.py`) passent par ici, pour
    qu'un cookie reposé ne perde jamais `HttpOnly`, `SameSite=Lax` ni `Secure`.

    Reposer le cookie d'appareil avec la MÊME valeur ne change pas l'identité de
    l'appareil : seule sa durée de vie est repoussée.
    """
    securise = request.url.scheme == "https"
    reponse.set_cookie(
        auth.COOKIE_NAME, jeton,
        max_age=auth.DUREE_COOKIE_JETON, httponly=True, samesite="lax",
        secure=securise,
    )
    if appareil is not None:
        reponse.set_cookie(
            services.COOKIE_APPAREIL, appareil,
            max_age=auth.DUREE_COOKIE_JETON, httponly=True, samesite="lax",
            secure=securise,
        )


@router.get("/acces")
def acces(request: Request, jeton: str = ""):
    """
    Vérifie le jeton fourni et, s'il est correct, pose le cookie d'accès.

    Déroulé :
    1. Limitation de débit par IP : au-delà de RATE_LIMIT_PER_MINUTE tentatives
       par minute, on répond 429 (anti-force brute).
    2. Si un jeton est configuré ET correspond (comparaison temps constant) :
       on pose le cookie et on redirige vers /scanner (303 = "See Other").
    3. Sinon : page « accès réservé » avec un motif explicatif :
       - "ouvert"   : aucun jeton requis sur cette installation (mode dev).
       - "expire"   : le lien est BON, mais le jeton a dépassé son échéance
         (`auth.jeton_expire_reconnu`) — c'est au bureau de le prolonger ;
       - "invalide" : le lien/jeton est erroné.

    Args:
        request: requête (pour l'IP, le schéma http/https et le rendu).
        jeton: valeur du paramètre `?jeton=` (vide par défaut).

    Returns:
        Une redirection 303 vers /scanner (succès), ou la page acces_refuse.html
        (429 si trop de tentatives, 403 sinon).
    """
    ip = request.client.host if request.client else "inconnu"
    limite = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    if auth.trop_de_tentatives(ip, limite):
        return templates.TemplateResponse(
            request, "acces_refuse.html", {"motif": "trop"}, status_code=429
        )

    conn = get_connection()
    try:
        attendu = auth.jeton_actuel(conn)
        expire_iso = auth.expiration_jeton(conn)
        expire = auth.jeton_expire(conn)

        if attendu and not expire and auth.jetons_egaux(jeton, attendu):
            # 303 force le navigateur à faire un GET sur /scanner après l'activation.
            reponse = RedirectResponse("/scanner", status_code=303)

            # Identifiant d'appareil : POSÉ SEULEMENT S'IL EST ABSENT. Un
            # bénévole rouvre son lien d'activation plus souvent qu'on ne le
            # croit (cookie effacé, lien repartagé) ; le réécrire lui donnerait
            # une nouvelle identité à chaque fois, et la liste montrerait cinq
            # appareils là où il n'y en a qu'un.
            appareil = services.appareil_de(request)
            nouveau = appareil is None
            if nouveau:
                appareil = services.nouvel_appareil()
            poser_cookies_benevole(reponse, request, jeton,
                                   appareil if nouveau else None)
            # Le registre, lui, est mis à jour à CHAQUE activation réussie :
            # après une rotation du jeton, c'est ce qui fait repasser en actif
            # l'appareil qui vient de rouvrir le lien (voir
            # `services.enregistrer_appareil`). Une écriture par activation,
            # jamais sur un chemin chaud.
            #
            # `expire_le` porte l'échéance DU JETON (None s'il n'en a pas), et
            # non plus celle du cookie, qui vit désormais bien au-delà : c'est
            # elle qui dit jusqu'à quand l'appareil peut écrire. Une
            # prolongation la réaligne (`services.aligner_echeance_appareils`).
            services.enregistrer_appareil(
                conn, appareil, "benevole",
                expire_le=expire_iso,
                generation=services.empreinte_jeton(attendu),
            )
            return reponse

        # Échec : mode ouvert (aucun jeton), jeton expiré, ou jeton erroné.
        # Un jeton expiré avec le BON lien ne doit pas s'entendre dire « lien
        # invalide » : la bénévole partirait chercher un autre lien, qui
        # n'existe pas (SEC-12).
        if attendu is None:
            contexte = {"motif": "ouvert"}
        elif auth.jeton_expire_reconnu(conn, jeton):
            contexte = {"motif": "expire",
                        "expire_local": services.format_local(expire_iso)}
        else:
            contexte = {"motif": "invalide"}
    finally:
        conn.close()

    return templates.TemplateResponse(
        request, "acces_refuse.html", contexte, status_code=403
    )
