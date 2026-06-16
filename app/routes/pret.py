"""
Routes de PRÊT / RETOUR — opérations d'écriture (réservées aux bénévoles).

Principes (voir docs/specification.md §5.1, §6, §8) :
- NE JAMAIS BLOQUER : toute incohérence (exemplaire déjà sorti/déjà dispo) donne
  un message + une action de rattrapage, jamais une erreur bloquante.
- Le système pré-sélectionne l'action probable selon l'état (déduit côté serveur).
- Écran déclenché par un scan ; le scanner embarqué (étape 6) redirige vers
  GET /pret/<id>.

Sécurité : ces routes sont protégées par le jeton bénévole (voir app/auth.py
et la route /acces). La dépendance `exiger_jeton` renvoie une 403 (page
acces_refuse) si l'appareil n'a pas activé l'accès.
"""

from fastapi import APIRouter, Depends, Request

from app import services
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

router = APIRouter(prefix="/pret", tags=["pret"])


def _rendu(request: Request, id_exemplaire: str, resultat: dict | None = None,
           status: int = 200):
    """Rend l'écran prêt/retour avec l'état courant et un résultat optionnel."""
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
    """Écran prêt/retour : état + action pré-sélectionnée."""
    return _rendu(request, id_exemplaire)


@router.post("/{id_exemplaire}/preter")
def action_preter(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    conn = get_connection()
    try:
        if services.info_exemplaire(conn, id_exemplaire) is None:
            return _rendu(request, id_exemplaire)
        courant = services.pret_en_cours(conn, id_exemplaire)
        if courant is not None:  # contrôle d'état : déjà sorti, ne pas dupliquer
            resultat = {"type": "deja_sorti", "numero": courant["numero_pochette"]}
        else:
            resultat = {"type": "prete", "numero": services.preter(conn, id_exemplaire)}
    finally:
        conn.close()
    return _rendu(request, id_exemplaire, resultat)


@router.post("/{id_exemplaire}/rendre")
def action_rendre(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
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
    conn = get_connection()
    try:
        if services.info_exemplaire(conn, id_exemplaire) is None:
            return _rendu(request, id_exemplaire)
        res = services.repreter(conn, id_exemplaire)
        resultat = {"type": "repret", "nouveau": res["nouveau_numero"],
                    "ancien": res.get("ancien_numero")}
    finally:
        conn.close()
    return _rendu(request, id_exemplaire, resultat)
