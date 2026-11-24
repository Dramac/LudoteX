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
"""

from fastapi import APIRouter, Depends, Request

from app import services
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
    filtre tous les blocs (nom du jeu ou code de la boîte, sans casse ni
    accents) ; vide ou absent, tout est affiché.
    """
    q = (q or "").strip()[:LONGUEUR_MAX_RECHERCHE].strip()
    conn = get_connection()
    try:
        en_cours = services.lister_prets_en_cours(conn)
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
         "seuil_heures": services.SEUIL_LONGUE_SORTIE_S // 3600},
    )
