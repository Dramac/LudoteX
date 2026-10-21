"""
CONTRÔLE D'UNE IMAGE VENUE D'AILLEURS — domicile commun du logo et des vignettes.

Deux sortes de fichiers entrent dans LudoteX sans venir de nous : le logo que
le bureau dépose (`app/logo.py`) et l'image de la boîte d'un jeu copiée chez
BoardGameGeek (`app/vignettes.py`, série « images »). Les deux passent par le
même contrôle, dans le même ordre, avant que le moindre pixel soit décodé :

1. **Contenu vide** refusé.
2. **SVG refusé**, reconnu à son début : du XML peut porter du `<script>`, et
   servi depuis NOTRE origine il s'exécuterait dans NOTRE contexte.
3. **Format réel lu par Pillow** (`Image.open` puis `verify()`), jamais par
   l'extension ni par le type annoncé : les deux sont écrits par l'expéditeur.
   Un fichier tronqué, corrompu ou incohérent échoue ici.
4. **Format dans la liste** de l'appelant.
5. **Dimensions bornées**, lues AVANT le décodage complet : une image de
   40 000 × 40 000 pixels tient dans quelques kilo-octets et en demanderait
   des gigaoctets une fois décompressée. Pillow lève en plus
   `DecompressionBombError` au-delà de sa propre borne ; elle est rangée avec
   les fichiers illisibles.
6. **Décodage complet**, qui peut encore échouer.

Ce module ne connaît ni le bureau ni BGG : il lève `ImageRefusee` avec un
MOTIF fermé, et chaque appelant traduit ce motif dans ses propres mots — le
logo garde ses messages et ses `detail` de journal mot pour mot, la vignette
n'en montre aucun (elle est simplement « sans image »). RIEN N'EST ÉCRIT ICI.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError

# Les motifs d'un refus, dans l'ordre du contrôle. « illisible » : le fichier
# ressemble à une image mais ne se vérifie pas ; « non_decodable » : il s'est
# vérifié, mais le décodage complet a échoué. Le logo les distingue à l'écran.
MOTIFS = ("vide", "svg", "pas_une_image", "illisible", "format_refuse",
          "trop_grand", "non_decodable")


class ImageRefusee(Exception):
    """
    Un fichier n'a pas passé le contrôle. `motif` est l'un de `MOTIFS`.

    `format_reel` et `dimensions` sont renseignés dès qu'ils ont été lus : le
    logo les cite dans ses messages (« Format d'image non accepté (GIF) »,
    « trop grande (4000 x 3000 pixels) »).
    """

    def __init__(self, motif: str, *, format_reel: str | None = None,
                 dimensions: tuple[int, int] | None = None):
        super().__init__(motif)
        self.motif = motif
        self.format_reel = format_reel
        self.dimensions = dimensions


def ressemble_a_du_svg(contenu: bytes) -> bool:
    """Reconnaît un SVG (ou un fichier XML quelconque) au début de son contenu."""
    debut = contenu[:512].lstrip().lower()
    return debut.startswith(b"<?xml") or debut.startswith(b"<svg") or b"<svg" in debut


def controler(contenu: bytes, *, formats: tuple[str, ...],
              dimension_max: int) -> Image.Image:
    """
    Contrôle `contenu` et rend l'image DÉCODÉE (mode d'origine), ou lève
    `ImageRefusee`.

    Args:
        formats: noms Pillow acceptés (« PNG », « JPEG », « WEBP »).
        dimension_max: côté maximal, en pixels, vérifié avant décodage.
    """
    if not contenu:
        raise ImageRefusee("vide")
    if ressemble_a_du_svg(contenu):
        raise ImageRefusee("svg")

    # `verify()` contrôle la cohérence du fichier SANS décoder les pixels, mais
    # laisse l'objet inutilisable : d'où une seconde ouverture plus bas. C'est
    # le mode d'emploi de Pillow, pas une maladresse.
    try:
        sonde = Image.open(BytesIO(contenu))
        format_reel = sonde.format
        dimensions = sonde.size
        sonde.verify()
    except UnidentifiedImageError:
        raise ImageRefusee("pas_une_image") from None
    except Exception:  # noqa: BLE001 - fichier tronqué, corrompu, incohérent
        raise ImageRefusee("illisible") from None

    if format_reel not in formats:
        raise ImageRefusee("format_refuse", format_reel=format_reel,
                           dimensions=dimensions)

    largeur, hauteur = dimensions
    if largeur > dimension_max or hauteur > dimension_max:
        raise ImageRefusee("trop_grand", format_reel=format_reel,
                           dimensions=dimensions)

    try:
        image = Image.open(BytesIO(contenu))
        image.load()
        return image
    except Exception:  # noqa: BLE001 - le décodage complet peut encore échouer
        raise ImageRefusee("non_decodable", format_reel=format_reel,
                           dimensions=dimensions) from None
