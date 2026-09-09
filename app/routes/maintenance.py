"""
Carnet de maintenance — écran BÉNÉVOLE (`/maintenance`).

Route sœur de `/admin/signalements` : même liste, mêmes filtres, même bouton
« Marquer traité ». Tout ce qui compte vraiment est ailleurs, dans un seul
domicile — la requête dans `services.lister_signalements`, la lecture filtrée
et les URL de filtres dans `app/carnet.py`, la fermeture dans
`services.fermer_signalement`. Ce module ne fait que brancher les trois sur
une URL ouverte aux bénévoles.

CE QUE CET ÉCRAN NE PORTE PAS (docs/conception-signalements.md §7)
-----------------------------------------------------------------
- les **exports** Excel et PDF : cette liste nomme des boîtes abîmées et porte
  le seul champ de saisie libre de l'application de prêt. En faire un fichier
  qui circule est une décision du bureau — les exports restent derrière le mot
  de passe ;
- la gestion des **catégories** (`/admin/categories-signalement`).

⚠️ DEUX GARDES, ET CE N'EST PAS UNE REDONDANCE
----------------------------------------------
`main.py` enregistre ce routeur derrière `garde_module("maintenance")` : c'est
la VISIBILITÉ, réglable par le bureau depuis `/admin/fonctionnalites`. Chaque
route porte en plus son `Depends(exiger_jeton)` : c'est l'AUTORISATION
d'accès, et elle ne se délègue pas à un réglage d'interface. Sans elle, le
jour où le module passerait en « visible par tous », le carnet s'ouvrirait au
premier visiteur venu. `app/modules.py` retire d'ailleurs cet état-là du
choix (drapeau `jamais_public`), mais un garde-fou d'interface n'est pas un
contrôle d'accès.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import carnet, services
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

PREFIXE = "/maintenance"


@router.get("")
def carnet_page(request: Request, etat: str = "ouverts",
                categorie: str | None = None, msg: str | None = None,
                _=Depends(exiger_jeton)):
    """
    Le carnet tel que le bénévole le consulte, filtrable par état et par
    catégorie comme celui du bureau. Un filtre forgé est ignoré sans erreur
    (règle « ne jamais bloquer », appliquée dans `carnet.contexte_liste`).
    """
    contexte = carnet.contexte_liste(PREFIXE, etat, categorie)
    contexte["message"] = msg
    return templates.TemplateResponse(request, "maintenance.html", contexte)


@router.post("/{id_signalement:int}/traiter")
def signalement_traiter(request: Request, id_signalement: int,
                        etat: str = Form("ouverts"), categorie: str = Form(""),
                        _=Depends(exiger_jeton)):
    """
    Referme un signalement, puis revient sur la liste AVEC LES MÊMES FILTRES
    (d'où les deux champs cachés du formulaire), exactement comme l'écran du
    bureau. Idempotent côté service : un second appui ne produit ni erreur ni
    message différent.

    Pas de contrôle de boîte ici — contrairement à la fiche de prêt, ce carnet
    n'a pas de boîte de référence : c'est la liste elle-même qui a fourni les
    identifiants.
    """
    from app.routes.catalogue import _entier_ou_none

    conn = get_connection()
    try:
        avant = services.fermer_signalement(conn, id_signalement)
    finally:
        conn.close()
    carnet.journaliser_traite(request, avant)
    etat_n = etat if etat in carnet.ETATS else "ouverts"
    return RedirectResponse(
        carnet.url_liste(PREFIXE, etat_n, _entier_ou_none(categorie),
                         "Signalement marqué traité."),
        status_code=303,
    )
