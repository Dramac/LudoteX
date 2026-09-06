"""
Service des IMAGES D'IDENTITÉ — logo et icônes, réglés ou par défaut.

POURQUOI UNE ROUTE PLUTÔT QU'UN MONTAGE STATIQUE
------------------------------------------------
Le logo d'une association vit dans `data/` (voir `app/logo.py` pour le
pourquoi), et `data/` contient AUSSI les trois bases SQLite, le journal
d'activité et les sauvegardes de sécurité. Monter ce dossier avec
`StaticFiles` exposerait tout cela au premier visiteur venu. On sert donc
exactement trois fichiers, nommés en dur, par trois routes distinctes : il n'y
a aucun segment d'URL variable, donc aucune traversée de chemin possible —
même pas à corriger.

Chaque route sert le fichier DÉPOSÉ s'il existe, et retombe sinon sur le
fichier VERSIONNÉ (l'identité LudoteX) : une instance qui n'a rien réglé
affiche un logo juste, jamais un cadre vide.

CACHE
-----
Ces images ne changent que lorsqu'un administrateur dépose ou retire un logo.
Les gabarits ajoutent `?v={{ logo_v() }}` — la date de modification du fichier
servi, motif d'`asset_v` (voir app/logo.py::version_servie) : l'URL change
quand l'image change, ce qui autorise un cache long sans jamais laisser un
navigateur sur une image périmée.
"""

from fastapi import APIRouter
from fastapi.responses import Response

from app import logo

router = APIRouter(prefix="/image", tags=["identite"])

# Une journée. L'URL portant un paramètre de version, on pourrait aller
# beaucoup plus loin ; on reste modeste parce que la seule URL qu'un navigateur
# demande SANS ce paramètre est celle des icônes mises en favori ou posées sur
# un écran d'accueil, et qu'un logo changé doit finir par s'y voir.
CACHE = "public, max-age=86400"


def _servir(nom: str) -> Response:
    """
    Renvoie l'image `nom` (fichier réglé, sinon versionné), ou un 404 nu.

    404 NU, sans gabarit : ces URL sont demandées par des balises `<img>` et
    `<link rel="icon">`. Servir la page HTML « introuvable » à une balise
    d'image ne dit rien à personne et coûte un rendu complet ; le navigateur
    sait très bien afficher une image manquante. Le cas ne survient que si les
    fichiers versionnés eux-mêmes ont disparu du déploiement.
    """
    chemin = logo.chemin_servi(nom)
    if chemin is None:
        return Response(status_code=404)
    try:
        octets = chemin.read_bytes()
    except OSError:
        # Le fichier existait à l'instant du test et n'est plus lisible
        # (remplacement en cours, droits, montage perdu). Dernière chance sur
        # le fichier versionné avant d'abandonner.
        secours = logo.chemin_versionne(nom)
        try:
            octets = secours.read_bytes()
        except OSError:
            return Response(status_code=404)
    return Response(
        content=octets, media_type="image/png", headers={"Cache-Control": CACHE}
    )


@router.get("/logo.png")
def image_logo():
    """Logo affiché sur l'accueil et le catalogue."""
    return _servir(logo.NOM_LOGO)


@router.get("/favicon-192.png")
def image_favicon_192():
    """Icône d'onglet et d'écran d'accueil, 192 x 192."""
    return _servir(logo.NOM_FAVICON_192)


@router.get("/favicon-512.png")
def image_favicon_512():
    """Icône d'écran d'accueil haute densité, 512 x 512."""
    return _servir(logo.NOM_FAVICON_512)
