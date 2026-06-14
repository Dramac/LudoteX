"""
Routes du CATALOGUE PUBLIC — consultation en lecture seule.

>>> SQUELETTE — aucune logique métier implémentée pour l'instant. <<<

Principe (voir docs/specification.md §5.2 et §8) :
- Accès public, SANS donnée personnelle, SANS bouton d'action.
- C'est la couche de lecture, conçue pour resservir telle quelle au
  catalogue public navigable (spec §11).

Endpoints prévus (à implémenter plus tard) :

    GET /jeu/{id_exemplaire}
        Fiche d'un exemplaire (lecture seule). C'est l'URL encodée dans le
        QR code. Affiche le titre, la disponibilité, etc. AUCUNE action ici :
        le contenu du QR ne donne que le niveau d'accès du catalogue public.

    GET /catalogue
        Liste des titres, navigable « en vrac » ou par catégorie
        (catégories définies dans le CSV). Affiche pour chaque titre sa
        disponibilité agrégée (nb d'exemplaires disponibles / total).

    GET /catalogue?categorie=...
        Filtrage par catégorie.

Remarques d'implémentation (pour mémoire, à ne pas coder maintenant) :
- La disponibilité d'un exemplaire se déduit de l'absence de prêt non clos
  (aucune ligne dans `prets` avec date_retour IS NULL).
- Raisonnement « catalogue d'abord » : partir de tous les titres, y rattacher
  les exemplaires/prêts, pour que les jeux jamais sortis restent visibles.
"""

from fastapi import APIRouter

router = APIRouter(tags=["catalogue"])

# --- À implémenter ---------------------------------------------------------
# @router.get("/jeu/{id_exemplaire}")
# def fiche_exemplaire(id_exemplaire: str):
#     ...
#
# @router.get("/catalogue")
# def catalogue(categorie: str | None = None):
#     ...
