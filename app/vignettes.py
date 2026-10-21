"""
VIGNETTES BOARDGAMEGEEK COPIÉES SUR NOTRE SERVEUR (série « images », lot 1).

La base garde l'ADRESSE de la petite image de la boîte chez BGG
(`fiches_bgg.vignette`, lot fiche-4). Ce module en garde une COPIE, contrôlée
et réencodée, dans un dossier de `data/` : la fiche d'administration l'affiche
depuis notre serveur, et le lot 2 pourra la montrer au public sans qu'aucune
adresse IP de visiteur parte chez BGG.

LE FICHIER FAIT FOI
-------------------
Aucune colonne ne dit qu'une copie existe : c'est le fichier. Les copies sont
un CACHE, hors du zip de sauvegarde (`app/sauvegarde.py` n'emporte que les
trois bases) ; une colonne pourrait donc annoncer, après une restauration,
des fichiers absents. Ici, la liste des copies à faire croise la base
(`services.vignettes_bgg_attendues` : les adresses des fiches portées par un
titre) et le dossier (`a_copier`) ; « Récupérer les données BGG manquantes »
reconstruit ce qui manque.

UN NOM PAR FICHE ET PAR ADRESSE
-------------------------------
`<bgg_id>-<empreinte>.webp`, où l'empreinte est tirée de l'ADRESSE de la
vignette (`empreinte`). Une fiche BGG peut servir plusieurs titres : la clé
est `bgg_id`, pas `reference_titre`. Si BGG change d'image, l'adresse change
à la récupération suivante de la fiche, donc le nom attendu aussi : l'ancienne
copie ne correspond plus, elle n'est plus servie, la nouvelle est copiée et
l'ancienne supprimée au même moment. L'empreinte sert aussi de numéro de
version dans l'adresse de service (`url`), comme `logo_v()` : une copie
renouvelée est rechargée par le navigateur.

`<bgg_id>-<empreinte>.refus` : un fichier VIDE qui note qu'une image n'a pas
pu être copiée pour de bon (absente chez BGG, ou refusée au contrôle). Sans
lui, la même image serait redemandée à chaque clic et le compte des copies à
faire ne tomberait jamais à zéro. « Interroger BoardGameGeek », sur la fiche
du jeu, l'efface et réessaie ; une nouvelle adresse a une nouvelle empreinte.

CE QUE SUBIT L'IMAGE AVANT D'ÊTRE ÉCRITE (`reencoder`)
------------------------------------------------------
1. Le contrôle COMMUN du logo (`app/controle_image.py`) : format réel par
   Pillow (JPEG, PNG ou WebP), SVG refusé, dimensions bornées avant décodage,
   décodage complet.
2. Réduction si elle dépasse `LARGEUR_MAX` × `HAUTEUR_MAX`, JAMAIS
   d'agrandissement.
3. Pixels RECOPIÉS dans une image neuve : rien de l'en-tête d'origine (EXIF,
   profil ICC, XMP, commentaires) ne suit.
4. Un seul format de sortie, WebP (`QUALITE_WEBP`), qui garde la
   transparence : mesuré le 2026-10-21 sur 44 vignettes réelles, il pèse
   20 % de moins que l'original et que le JPEG sur fond blanc.

Un fichier qui ne passe pas n'est PAS gardé : la fiche est « sans image ».

ÉCRITURE ATOMIQUE, MÉNAGE
-------------------------
Fichier temporaire du même dossier, puis `os.replace` : un lecteur ne voit
jamais une image à moitié écrite. Une copie qu'aucun titre ne porte plus
(`menage`) est supprimée ; la remise à zéro de la formation vide le dossier
(`vider`). Un fichier dont le nom n'est pas de ce module (ni copie, ni marque,
ni temporaire à nous) n'est NI SERVI NI SUPPRIMÉ : la route ne sert qu'un nom
calculé d'un entier et d'une empreinte, et le ménage ne touche qu'aux noms
qu'il reconnaît.

Ce module ne parle ni au réseau (`app/bgg_api.py` le fait) ni à la base (la
route lit et transmet) ; il ne lève jamais vers une page, sauf `OSError` à
l'écriture, que `copier_lot` compte.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from app import bgg_api, controle_image, logo

# Sous-dossier de `data/` (le parent de la base de prêt, `logo.dossier_regle`).
NOM_DOSSIER = "vignettes-bgg"

# Ce qui est gardé : la vignette de BGG est demandée en « fit-in/200x150 »
# (relevé sur les 765 adresses du cache de la collecte), et les 44 mesurées
# le 2026-10-21 tenaient toutes dans 200 × 150. Une image plus petite garde sa
# taille ; une plus grande (ce que BGG n'envoie pas) est réduite.
LARGEUR_MAX = 200
HAUTEUR_MAX = 150

# Côté maximal accepté AVANT décodage (protection contre les images à fort
# taux de compression, voir `controle_image`). Cinq fois la vignette : une
# image honnête n'en approche pas, et le décodage reste sous 4 Mo de pixels.
DIMENSION_MAX = 1000

FORMATS_ACCEPTES = ("JPEG", "PNG", "WEBP")

# Qualité WebP : 80 donne des vignettes de 2 à 10 Ko (mesuré le 2026-10-21),
# sans défaut visible à 200 × 150.
QUALITE_WEBP = 80

# Longueur de l'empreinte (hexadécimal). 64 bits : aucune collision possible
# entre les adresses d'une même fiche.
_LONGUEUR_EMPREINTE = 16

# Les seuls noms que ce module écrit, et donc les seuls qu'il supprime.
_NOM_COPIE = re.compile(r"([1-9][0-9]{0,9})-([0-9a-f]{%d})\.(webp|refus)" % _LONGUEUR_EMPREINTE)
_PREFIXE_TEMPORAIRE = ".vignette-"
_SUFFIXE_TEMPORAIRE = ".tmp"

# Un temporaire plus vieux que ça est le reste d'une écriture interrompue.
_AGE_TEMPORAIRE_ABANDONNE_S = 3600


class VignetteRefusee(Exception):
    """L'image téléchargée n'a pas passé le contrôle. Rien n'a été écrit."""


# ---------------------------------------------------------------------------
# Où vivent les copies
# ---------------------------------------------------------------------------
def dossier() -> Path:
    """Le dossier des copies de CETTE instance (relu à chaque appel)."""
    return logo.dossier_regle() / NOM_DOSSIER


def empreinte(adresse: str) -> str:
    """L'empreinte d'une adresse de vignette : son nom de fichier, sa version."""
    return hashlib.sha256(adresse.encode("utf-8")).hexdigest()[:_LONGUEUR_EMPREINTE]


def _nom(bgg_id: int, adresse: str, extension: str) -> str:
    return f"{int(bgg_id)}-{empreinte(adresse)}.{extension}"


def chemin_servi(bgg_id: int, adresse: str | None) -> Path | None:
    """
    Le fichier de la copie de `bgg_id` POUR CETTE ADRESSE, s'il existe ; sinon
    None. Ne lève jamais : un dossier illisible veut dire « pas de copie ».
    """
    if not adresse:
        return None
    try:
        chemin = dossier() / _nom(bgg_id, adresse, "webp")
        return chemin if chemin.is_file() else None
    except OSError:
        return None


def url(bgg_id: int, adresse: str | None) -> str | None:
    """
    L'adresse de service de la copie (route `/image/bgg/…`), versionnée par
    l'empreinte ; None s'il n'y a pas de copie — la page n'affiche alors rien.
    """
    if chemin_servi(bgg_id, adresse) is None:
        return None
    return f"/image/bgg/{int(bgg_id)}.webp?v={empreinte(adresse)}"


def _noms_presents() -> set[str]:
    try:
        return {p.name for p in dossier().iterdir()}
    except OSError:
        return set()


def a_copier(attendues: dict[int, str]) -> list[tuple[int, str]]:
    """
    Les vignettes à copier : adresse attendue, ni copie ni marque de refus
    pour elle dans le dossier. Par numéro de fiche croissant.

    Args:
        attendues: {bgg_id: adresse}, lu en base par la route
            (`services.vignettes_bgg_attendues`).
    """
    presents = _noms_presents()
    return [(bgg_id, adresse) for bgg_id, adresse in sorted(attendues.items())
            if _nom(bgg_id, adresse, "webp") not in presents
            and _nom(bgg_id, adresse, "refus") not in presents]


# ---------------------------------------------------------------------------
# Contrôle et réencodage
# ---------------------------------------------------------------------------
def reencoder(contenu: bytes) -> bytes:
    """
    L'image contrôlée, réduite au besoin, réencodée en WebP sans métadonnées.

    Raises:
        VignetteRefusee: le fichier n'est pas une image acceptable, ou son
            traitement a échoué. Rien n'a été écrit.
    """
    try:
        image = controle_image.controler(
            contenu, formats=FORMATS_ACCEPTES, dimension_max=DIMENSION_MAX)
    except controle_image.ImageRefusee as refus:
        raise VignetteRefusee(refus.motif) from None
    try:
        rgba = image.convert("RGBA")
        mode = "RGBA" if rgba.getchannel("A").getextrema()[0] < 255 else "RGB"
        if mode == "RGB":
            rgba = rgba.convert("RGB")
        if rgba.width > LARGEUR_MAX or rgba.height > HAUTEUR_MAX:
            rgba.thumbnail((LARGEUR_MAX, HAUTEUR_MAX), Image.LANCZOS)
        # Une image NEUVE, faite des seuls pixels : `info` (EXIF, ICC, XMP,
        # commentaires) reste derrière.
        propre = Image.frombytes(mode, rgba.size, rgba.tobytes())
        sortie = BytesIO()
        propre.save(sortie, format="WEBP", quality=QUALITE_WEBP, method=6)
        return sortie.getvalue()
    except Exception:  # noqa: BLE001 - un traitement raté ne casse rien
        raise VignetteRefusee("traitement") from None


# ---------------------------------------------------------------------------
# Écriture, refus, ménage
# ---------------------------------------------------------------------------
def _retirer_autres(bgg_id: int, garde: str) -> None:
    """Supprime les copies et marques de `bgg_id` autres que `garde`."""
    for nom in _noms_presents():
        trouve = _NOM_COPIE.fullmatch(nom)
        if trouve and int(trouve.group(1)) == int(bgg_id) and nom != garde:
            try:
                (dossier() / nom).unlink()
            except OSError:
                pass


def enregistrer(bgg_id: int, adresse: str, contenu: bytes) -> None:
    """
    Contrôle, réencode et écrit la copie de `bgg_id` pour `adresse`, puis
    retire ses copies et marques précédentes (adresse changée).

    Raises:
        VignetteRefusee: rien n'est écrit.
        OSError: disque plein, droits — rien n'est laissé à moitié écrit.
    """
    octets = reencoder(contenu)
    cible = dossier()
    cible.mkdir(parents=True, exist_ok=True)
    nom = _nom(bgg_id, adresse, "webp")
    with tempfile.NamedTemporaryFile(dir=cible, prefix=_PREFIXE_TEMPORAIRE,
                                     suffix=_SUFFIXE_TEMPORAIRE, delete=False) as tmp:
        temporaire = Path(tmp.name)
    try:
        temporaire.write_bytes(octets)
        os.replace(temporaire, cible / nom)
    except BaseException:
        try:
            temporaire.unlink()
        except OSError:
            pass
        raise
    _retirer_autres(bgg_id, nom)


def noter_refus(bgg_id: int, adresse: str) -> None:
    """Note que l'image de cette adresse ne se copie pas. Raises: OSError."""
    cible = dossier()
    cible.mkdir(parents=True, exist_ok=True)
    nom = _nom(bgg_id, adresse, "refus")
    (cible / nom).touch()
    _retirer_autres(bgg_id, nom)


def oublier_refus(bgg_id: int, adresse: str) -> None:
    """Efface la marque de refus de cette adresse : la copie sera retentée."""
    try:
        (dossier() / _nom(bgg_id, adresse, "refus")).unlink()
    except OSError:
        pass


def menage(attendues: dict[int, str]) -> int:
    """
    Supprime les copies et marques qui ne correspondent plus à `attendues` :
    fiche qu'aucun titre ne porte plus (jeu délié, relié ailleurs), fiche
    sans image, ou adresse changée. Supprime aussi nos temporaires abandonnés
    depuis plus d'une heure. Les fichiers étrangers ne sont pas touchés.

    Returns: le nombre de copies (`.webp`) supprimées. Ne lève jamais.
    """
    gardes = {_nom(i, a, ext) for i, a in attendues.items() for ext in ("webp", "refus")}
    retirees = 0
    maintenant = time.time()
    for nom in _noms_presents():
        chemin = dossier() / nom
        try:
            if _NOM_COPIE.fullmatch(nom):
                if nom in gardes:
                    continue
                chemin.unlink()
                retirees += nom.endswith(".webp")
            elif (nom.startswith(_PREFIXE_TEMPORAIRE) and nom.endswith(_SUFFIXE_TEMPORAIRE)
                  and maintenant - chemin.stat().st_mtime > _AGE_TEMPORAIRE_ABANDONNE_S):
                chemin.unlink()
        except OSError:
            pass
    return retirees


def vider() -> int:
    """Supprime toutes les copies et marques (remise à zéro de la formation)."""
    return menage({})


# ---------------------------------------------------------------------------
# Copier un lot
# ---------------------------------------------------------------------------
def copier_lot(a_faire: list[tuple[int, str]], *, fin: float | None) -> dict:
    """
    Télécharge et range les vignettes de `a_faire`, dans l'ordre, jusqu'à
    l'échéance `fin` (horloge de `bgg_api.CADENCE_IMAGES`).

    Une image absente chez BGG ou refusée au contrôle est notée (`.refus`) et
    le lot continue. Un échec qui se répéterait sur la suivante (panne,
    blocage, « attendez », disque) arrête le lot ; ce qui est copié reste
    copié. NE LÈVE JAMAIS pour ces issues : une copie ratée n'empêche ni la
    liaison, ni l'affichage d'une fiche, ni le lot des fiches.

    Returns:
        {"copiees": n, "refusees": n, "arret": None ou un code — « panne »,
        « bloque », « attendre » (de `bgg_api.ECHECS_IMAGE`) ou « disque »}.
        L'échéance atteinte n'est pas un arrêt : le reste attend le clic
        suivant.
    """
    bilan = {"copiees": 0, "refusees": 0, "arret": None}
    for bgg_id, adresse in a_faire:
        try:
            octets = bgg_api.telecharger_vignette(adresse, fin=fin)
        except bgg_api.EchecImage as echec:
            if echec.code in ("absente", "refusee"):
                if not _refus_note(bgg_id, adresse, bilan):
                    break
                continue
            if echec.code != "trop_long":
                bilan["arret"] = echec.code
            break
        try:
            enregistrer(bgg_id, adresse, octets)
            bilan["copiees"] += 1
        except VignetteRefusee:
            if not _refus_note(bgg_id, adresse, bilan):
                break
        except OSError:
            bilan["arret"] = "disque"
            break
    return bilan


def _refus_note(bgg_id: int, adresse: str, bilan: dict) -> bool:
    """Note un refus et le compte ; False (et arrêt « disque ») si l'écriture échoue."""
    try:
        noter_refus(bgg_id, adresse)
    except OSError:
        bilan["arret"] = "disque"
        return False
    bilan["refusees"] += 1
    return True
