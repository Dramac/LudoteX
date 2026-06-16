"""
Routes de PRÊT / RETOUR — opérations d'ÉCRITURE, réservées aux bénévoles.

PRINCIPES (voir docs/specification.md §5.1, §6, §8)
---------------------------------------------------
- NE JAMAIS BLOQUER : toute incohérence (exemplaire déjà sorti, ou déjà
  disponible) produit un MESSAGE, jamais une erreur. Le résultat renvoyé au
  gabarit porte un `type` que pret.html sait afficher.
- Le serveur fait foi sur l'état : avant chaque action, on relit l'état réel
  (`pret_en_cours`) pour gérer les conflits (deux bénévoles sur le même
  exemplaire) sans planter.
- L'écran est déclenché par un scan : le scanner (routes/scanner.py) redirige
  vers GET /pret/<id>.

SÉCURITÉ
--------
Toutes les routes dépendent de `exiger_jeton` (app/auth.py) : sans cookie
d'accès valide, FastAPI lève un 403 que main.py transforme en page
« accès réservé ».

DICTIONNAIRE `resultat` (passé au gabarit pret.html)
----------------------------------------------------
    {"type": "prete",            "numero": n}             prêt réussi
    {"type": "repret",           "nouveau": n, "ancien": a|None}  re-prêt
    {"type": "rendu",            "numero": n}             retour enregistré
    {"type": "deja_sorti",       "numero": n}             déjà sorti (no-op)
    {"type": "deja_disponible"}                           rien à rendre (no-op)
"""

from fastapi import APIRouter, Depends, Request

from app import services
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

# prefix="/pret" : toutes les routes ci-dessous commencent par /pret.
router = APIRouter(prefix="/pret", tags=["pret"])


def _rendu(request: Request, id_exemplaire: str, resultat: dict | None = None,
           status: int = 200):
    """
    Rend l'écran prêt/retour avec l'état COURANT de l'exemplaire.

    Fonction interne (préfixe `_`) factorisant le rendu commun aux quatre routes.
    Elle relit toujours l'état frais en base, de sorte que la page reflète la
    réalité après l'action. Le `resultat` éventuel sert au bandeau de
    confirmation.

    Args:
        request: requête courante.
        id_exemplaire: identifiant concerné.
        resultat: dict décrivant l'issue d'une action (voir en-tête), ou None
            pour un simple affichage (GET).
        status: code HTTP si l'exemplaire existe (404 forcé sinon).

    Returns:
        La page pret.html.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        pret_actuel = services.pret_en_cours(conn, id_exemplaire) if info else None
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "pret.html",
        {"id_exemplaire": id_exemplaire, "info": info,
         "pret_actuel": pret_actuel, "resultat": resultat},
        status_code=status if info else 404,
    )


@router.get("/{id_exemplaire}")
def ecran(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Affiche l'écran prêt/retour (GET).

    Le gabarit pré-sélectionne l'action probable : « Prêter » si l'exemplaire est
    disponible, « Rendre » / « Le re-prêter » s'il est sorti.

    Le paramètre `_=Depends(exiger_jeton)` applique la protection par jeton ; sa
    valeur n'est pas utilisée (d'où le nom `_`).
    """
    return _rendu(request, id_exemplaire)


@router.post("/{id_exemplaire}/preter")
def action_preter(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Prête l'exemplaire (POST). Contrôle d'état : si déjà sorti, ne duplique pas
    le prêt et renvoie un message `deja_sorti` (jamais d'erreur).
    """
    conn = get_connection()
    try:
        if services.info_exemplaire(conn, id_exemplaire) is None:
            return _rendu(request, id_exemplaire)  # exemplaire inconnu -> 404
        courant = services.pret_en_cours(conn, id_exemplaire)
        if courant is not None:  # déjà sorti : on ne ré-attribue pas de pochette
            resultat = {"type": "deja_sorti", "numero": courant["numero_pochette"]}
        else:
            resultat = {"type": "prete", "numero": services.preter(conn, id_exemplaire)}
    finally:
        conn.close()
    return _rendu(request, id_exemplaire, resultat)


@router.post("/{id_exemplaire}/rendre")
def action_rendre(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Enregistre le retour (POST). Si l'exemplaire était déjà disponible, renvoie
    `deja_disponible` sans rien modifier.
    """
    conn = get_connection()
    try:
        if services.info_exemplaire(conn, id_exemplaire) is None:
            return _rendu(request, id_exemplaire)
        res = services.rendre(conn, id_exemplaire)
        if res.get("deja_disponible"):
            resultat = {"type": "deja_disponible"}
        else:
            resultat = {"type": "rendu", "numero": res["numero_libere"]}
    finally:
        conn.close()
    return _rendu(request, id_exemplaire, resultat)


@router.post("/{id_exemplaire}/repreter")
def action_repreter(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Re-prête l'exemplaire (POST) : clôt l'ancien prêt puis en ouvre un nouveau
    (cas d'oubli de scan de retour). Voir services.repreter pour la logique.
    """
    conn = get_connection()
    try:
        if services.info_exemplaire(conn, id_exemplaire) is None:
            return _rendu(request, id_exemplaire)
        res = services.repreter(conn, id_exemplaire)
        # `ancien` peut être None si l'exemplaire était en fait déjà disponible.
        resultat = {"type": "repret", "nouveau": res["nouveau_numero"],
                    "ancien": res.get("ancien_numero")}
    finally:
        conn.close()
    return _rendu(request, id_exemplaire, resultat)
