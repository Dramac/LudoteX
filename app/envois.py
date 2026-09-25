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
"""

from typing import BinaryIO

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
