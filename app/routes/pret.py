"""
Routes de PRÊT / RETOUR — opérations d'écriture (réservées aux bénévoles).

>>> SQUELETTE — aucune logique métier implémentée pour l'instant. <<<

Principes directeurs (voir docs/specification.md §5.1, §6 et §8) :

1. NE JAMAIS BLOQUER le bénévole en pic. Toute incohérence est signalée et
   accompagnée d'une action de rattrapage en un tap, jamais d'une erreur
   bloquante.
2. Interface minimale : le système pré-sélectionne l'action la plus probable
   selon l'état de l'exemplaire ; on ne demande un choix explicite que dans
   les cas réellement ambigus.
3. Écritures protégées par un JETON aléatoire long (≈32 car.), mémorisé côté
   appareil, + limitation de débit par IP. Pas de comptes individuels.

Logique de scan attendue (à implémenter plus tard) :

    Exemplaire DISPONIBLE  -> action unique « Prêter »
        Attribue le plus petit numéro de pochette libre et l'affiche en grand
        (« Pochette n°7 — glissez-y la pièce d'identité »).

    Exemplaire SORTI       -> deux actions
        « Rendre » (principale)     : clôt le prêt en cours, libère le numéro.
        « Le re-prêter » (secondaire, cas d'oubli de scan) : considère le
            prêt précédent comme rentré (date_retour = maintenant, ancien
            numéro libéré), puis ouvre un nouveau prêt avec un nouveau numéro.

Gestion des numéros de pochette (spec §6) :
- Numérotation à partir de 1, attribution du PLUS PETIT numéro libre.
- Recyclage immédiat au retour. AUCUN plafond : on ne refuse jamais un prêt.

Endpoints prévus (à implémenter plus tard) :

    POST /pret/{id_exemplaire}/prefer    -> attribue une pochette, ouvre un prêt
    POST /pret/{id_exemplaire}/rendre    -> clôt le prêt en cours, libère le numéro
    POST /pret/{id_exemplaire}/reprefer  -> clôt l'ancien prêt puis en rouvre un

Toutes ces routes exigeront :
- la vérification du jeton d'écriture (dépendance FastAPI à écrire) ;
- un contrôle d'état côté serveur pour gérer les conflits rares (deux
  bénévoles sur le même exemplaire) sans bloquer.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/pret", tags=["pret"])

# --- Sécurité (à implémenter) ----------------------------------------------
# def require_token(...):
#     """Vérifie le jeton d'écriture (PRET_TOKEN) + limitation de débit par IP."""
#     ...

# --- À implémenter ---------------------------------------------------------
# @router.post("/{id_exemplaire}/preter")
# def preter(id_exemplaire: str, ...):
#     ...
#
# @router.post("/{id_exemplaire}/rendre")
# def rendre(id_exemplaire: str, ...):
#     ...
#
# @router.post("/{id_exemplaire}/repreter")
# def repreter(id_exemplaire: str, ...):
#     ...
