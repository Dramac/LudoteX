"""
Lecture BORNÉE d'un fichier envoyé à l'administration — une seule fonction pour
les trois envois : logo (`app/logo.py`), catalogue (`/admin/donnees/import`),
archive de sauvegarde (`/admin/sauvegarde/import`).

Chaque usage fixe sa borne là où vit son domaine (`logo.TAILLE_MAX_OCTETS`,
`scripts.import_csv.TAILLE_MAX_CATALOGUE`, `sauvegarde.TAILLE_MAX_ARCHIVE`) ;
cette fonction ne connaît que le nombre qu'on lui passe.

CE QUE LA BORNE PROTÈGE, ET CE QU'ELLE NE PROTÈGE PAS
-----------------------------------------------------
Au moment où la route appelle `lire_borne`, le corps de la requête a déjà été
reçu : nginx l'a mis en tampon (`client_max_body_size` le borne à 20 Mo), puis
Starlette l'a analysé et mis de côté (sur disque au-delà de 1 Mo). La borne
protège donc la MÉMOIRE du processus et ce qui suit la lecture (décodage d'une
image, analyse d'un CSV, extraction d'une archive) — pas la bande passante.

Ce qui empêche un visiteur NON CONNECTÉ de faire analyser son envoi, c'est
autre chose : la garde posée AVANT la lecture du corps, par la classe de route
`RouteEnvoiAdmin` d'`app/routes/admin.py` (SEC-14).

Et ce qui empêche quiconque d'adresser un envoi multipart à N'IMPORTE QUELLE
AUTRE route à formulaire, c'est le middleware `MultipartHorsEnvois` ci-dessous
(SEC-14 élargi, lot-17-pré-production).
"""

from collections.abc import Collection
from typing import BinaryIO

from starlette.requests import Request

UN_MO = 1024 * 1024


def en_mo(octets: int) -> str:
    """« 15 Mo » : une borne dite comme le bureau la lit."""
    return f"{octets // UN_MO} Mo"


class EnvoiTropLourd(Exception):
    """
    Le fichier envoyé dépasse la borne de son usage. RIEN n'a été écrit.

    `message` dit quoi faire : le cas réel n'est pas un fichier légitime trop
    gros, c'est le mauvais fichier choisi dans le sélecteur.
    """

    def __init__(self, taille_max: int):
        self.taille_max = taille_max
        self.message = (
            f"Fichier trop volumineux : {en_mo(taille_max)} au maximum. "
            f"Vérifiez qu'il s'agit du bon fichier."
        )
        super().__init__(self.message)


def lire_borne(flux: BinaryIO, taille_max: int) -> bytes:
    """
    Lit le fichier reçu, en refusant AVANT de tout charger ce qui dépasse
    `taille_max` octets ; lève `EnvoiTropLourd` dans ce cas.

    On lit `taille_max + 1` octets : s'il en revient autant, c'est qu'il en
    restait, et le fichier est refusé sans que le reste soit jamais lu. Un
    octet de plus que la borne en mémoire, pas un fichier entier.
    """
    contenu = flux.read(taille_max + 1)
    if len(contenu) > taille_max:
        raise EnvoiTropLourd(taille_max)
    return contenu


# ---------------------------------------------------------------------------
# SEC-14 élargi — le multipart n'entre que par les trois portes prévues
# ---------------------------------------------------------------------------
# Titre et explication de la page de refus. Aucun geste de l'interface ne
# produit cette requête (voir `MultipartHorsEnvois`) : le texte vise la seule
# personne qui pourrait la voir sans le vouloir — un lien ou un outil mal
# réglé — et lui dit, comme les autres pages d'erreur, que rien n'est écrit.
REFUS_MULTIPART = (
    "Cet envoi n'est pas accepté à cette adresse",
    "Cette adresse ne reçoit pas de fichier. Rien n'a été lu ni enregistré. "
    "Repartez d'un des liens ci-dessous, puis refaites le geste depuis la page.",
)


def _est_multipart(scope) -> bool:
    """
    Vrai si UN des en-têtes `Content-Type` de la requête annonce du multipart.

    Tous les en-têtes, et pas le premier seulement : Starlette ne lit que le
    premier, mais rien ne garantit qu'un intermédiaire fasse le même choix.
    Casse et blancs ignorés, parce que l'analyseur de Starlette les ignore
    (`parse_options_header` met le type en minuscules) : `Multipart/Form-Data`
    serait analysé, il doit donc être refusé. Tout `multipart/*`, pas le seul
    `multipart/form-data` : aucun usage légitime d'un autre sous-type, et une
    règle plus large ne se contourne pas par une variante d'écriture.
    """
    return any(
        nom == b"content-type" and valeur.strip().lower().startswith(b"multipart/")
        for nom, valeur in scope.get("headers", ())
    )


class MultipartHorsEnvois:
    """
    Middleware ASGI : refuse tout envoi multipart hors des routes d'envoi de
    fichier, SANS lire un octet du corps (SEC-14 élargi).

    LE DÉFAUT. FastAPI analyse le corps entier, fichiers compris, de toute
    route qui déclare un `Form(...)` — avant la moindre dépendance (lot 11).
    Un inconnu pouvait donc faire analyser et déverser sur disque jusqu'à
    20 Mo (borne de nginx) par l'UNIQUE worker, en postant un multipart à
    n'importe laquelle des routes à formulaire, `/admin/login` compris, qui
    n'exige aucune session par définition. Or trois gabarits seulement
    portent `enctype="multipart/form-data"`, pour les trois routes de
    `admin.CHEMINS_ENVOI` : aucun autre envoi multipart légitime n'existe.

    POURQUOI UN MIDDLEWARE ASGI PUR. `@app.middleware("http")` produit un
    `BaseHTTPMiddleware`, qui enveloppe la requête dans ses propres objets ;
    ici, on décide sur le seul `scope` (chemin et en-têtes, déjà reçus) et on
    répond sans jamais appeler `receive` : le corps reste chez le serveur,
    rien n'est analysé, rien n'est mis sur disque. Le test le vérifie avec un
    `receive` qui lève s'il est appelé.

    LE CODE : 415, et non la redirection 303 de l'administration. La règle
    « ne jamais bloquer » vise un geste de l'interface qui tourne mal ; aucun
    geste ne produit cette requête, ni formulaire ni script de l'application,
    et la liste blanche couvre les trois qui envoient un fichier. La cible est
    un robot ; lui renvoyer une redirection, c'est lui faire charger une page
    pour rien. Le 415 (« type de contenu non pris en charge ») est le code
    juste. La réponse reste la page d'erreur de l'application, en HTML, comme
    tous les autres codes (`probleme.html`) : jamais de texte nu.

    LA LISTE BLANCHE est celle que remplit `admin._envoi_admin`, passée par
    référence : elle vit à côté des trois routes, jamais recopiée ici.
    Comparaison sur le chemin EXACT (`scope["path"]`) : l'instance de
    formation est le même code sur un autre sous-domaine (jamais un préfixe de
    chemin, voir `deploy/nginx-ludotex-formation.conf`), et uvicorn n'y est
    lancé avec aucun `--root-path` — les chemins y sont donc les mêmes, et son
    import de catalogue passe (testé).

    CE QUE ÇA NE CHANGE PAS : derrière nginx, le corps a déjà été reçu et mis
    en tampon (20 Mo au plus) avant d'arriver ici. Ce qui est épargné, c'est
    le travail de l'application : analyse, fichier temporaire, boucle
    d'événements occupée pendant ce temps.
    """

    def __init__(self, app, chemins_autorises: Collection[str]):
        self.app = app
        self.chemins_autorises = chemins_autorises

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["path"] not in self.chemins_autorises
            and _est_multipart(scope)
        ):
            await self._refuser(scope, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuser(scope, send):
        # Import tardif : ce module est aussi chargé par l'import du catalogue
        # en LIGNE DE COMMANDE (`scripts/import_csv.py`), qui n'a que faire des
        # gabarits ni de leurs dépendances.
        from app.templating import templates

        # `Request` SANS `receive` : si quoi que ce soit tentait de lire le
        # corps pendant le rendu, Starlette lèverait au lieu de le lire.
        titre, message = REFUS_MULTIPART
        reponse = templates.TemplateResponse(
            Request(scope), "probleme.html",
            {"titre": titre, "message": message}, status_code=415,
        )
        await reponse(scope, _receive_interdit, send)


async def _receive_interdit():
    """Le refus ne lit JAMAIS le corps : un appel ici est un défaut."""
    raise RuntimeError("MultipartHorsEnvois ne doit jamais lire le corps.")
