"""
JEUX SORTIS — page BÉNÉVOLE (`/sortis`, lot bilan-1).

L'outil de comptoir qui vivait en bas de `/stats` (« Jeux actuellement
sortis », ancre `#sortis`) : retrouver une boîte dehors et son numéro de
pochette, pendant l'événement. Les plus anciennes sorties en tête, un repère
sur les longues sorties (`services.SEUIL_LONGUE_SORTIE_S`), une recherche par
nom ou par code.

RATTACHÉE AU CŒUR DU PRÊT, PAS AU MODULE « STATS »
--------------------------------------------------
Comme `/scanner` et `/pret`, ce routeur est monté SANS `garde_module` dans
`main.py` : si le bureau désactive les statistiques, ou les réserve aux
bénévoles, le comptoir garde sa liste. L'accès, lui, est porté par la route
(`Depends(exiger_jeton)`) : le numéro de pochette dit où se trouve une pièce
d'identité, il ne s'affiche jamais sans jeton (docs/vocabulaire.md, règle 2).

Tout le calcul est dans `services.lister_prets_en_cours` et
`services.filtrer_sortis` : cette route ne fait que les brancher.

POCHETTES ÉCARTÉES (lot comptoir-10)
------------------------------------
Un bloc en bas de page, affiché seulement s'il y en a : les numéros que
« Utiliser une autre pochette » a écartés, chacun avec « Remettre en
service ». C'est ici plutôt que sur une page à part : Jeux sortis est déjà
l'outil de comptoir sur les pochettes, et le menu bénévole, partagé, est
assez long sur téléphone. La recherche `q` ne filtre PAS ce bloc : elle
porte sur des jeux, une pochette écartée n'en tient aucun.
"""

from fastapi import APIRouter, Depends, Form, Request

from app import services
from app.routes.pret import numero_pochette_saisi, remettre_en_service
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["sortis"])

# Au-delà, la recherche est tronquée : aucun nom de jeu ni code de boîte n'en
# approche, et la saisie est recopiée dans la page (« pour « … » »). Une
# adresse forgée de plusieurs kilo-octets ne doit ni planter ni s'afficher
# en entier.
LONGUEUR_MAX_RECHERCHE = 100


@router.get("/sortis")
def jeux_sortis(request: Request, q: str | None = None,
                _=Depends(exiger_jeton)):
    """
    Les jeux dehors à l'instant : prêts au public, puis un bloc par sortie
    sans pochette (tournoi, démo — `services.SORTIES_SANS_POCHETTE`). `q`
    filtre ces blocs (nom du jeu ou code de la boîte, sans casse ni
    accents) ; vide ou absent, tout est affiché. Puis les pochettes
    écartées, s'il y en a, jamais filtrées.
    """
    return _page(request, q)


@router.post("/sortis/pochettes-ecartees")
def remettre_pochette(request: Request, numero: str = Form(""),
                      q: str = Form(""), _=Depends(exiger_jeton)):
    """
    « Remettre en service » d'une ligne du bloc Pochettes écartées. Rend la
    page elle-même (pas de redirection : le numéro n'entre jamais dans une
    adresse), le formulaire visant l'ancre du bloc. La recherche en cours est
    gardée. Recharger la page refait le geste, sans effet ni erreur.
    """
    conn = get_connection()
    try:
        numero_remis = numero_pochette_saisi(numero)
        statut = remettre_en_service(request, conn, numero_remis)
    finally:
        conn.close()
    return _page(request, q, remise={"statut": statut, "numero": numero_remis})


def _page(request: Request, q: str | None, remise: dict | None = None):
    q = (q or "").strip()[:LONGUEUR_MAX_RECHERCHE].strip()
    conn = get_connection()
    try:
        en_cours = services.lister_prets_en_cours(conn)
        ecartees = services.pochettes_ecartees(conn)
    finally:
        conn.close()
    trouves = services.filtrer_sortis(en_cours, q)
    # Un bloc par sortie sans pochette, dans l'ordre de la règle : ses mots,
    # ses lignes filtrées, et son nombre AVANT filtre (le résumé).
    sans_pochette = [
        {"motif": motif, "mots": mots, "lignes": trouves[motif],
         "nb": len(en_cours[motif])}
        for motif, mots in services.SORTIES_SANS_POCHETTE.items()]
    return templates.TemplateResponse(
        request, "sortis.html",
        {"q": q,
         "sortis_pret": trouves["pret"], "sans_pochette": sans_pochette,
         "nb_sortis": len(en_cours["pret"]) + sum(b["nb"] for b in sans_pochette),
         "nb_trouves": len(trouves["pret"]) + sum(len(b["lignes"]) for b in sans_pochette),
         "seuil_heures": services.SEUIL_LONGUE_SORTIE_S // 3600,
         "ecartees": ecartees, "remise": remise},
    )
