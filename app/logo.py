"""
LOGO de l'application — identité LudoteX versionnée, logo d'association réglable.

DEUX LOGOS, DEUX DOMICILES, ET C'EST LE CŒUR DU MODULE
------------------------------------------------------
1. Le logo **du projet** (le meeple LudoteX) est VERSIONNÉ, dans
   `app/static/img/`. C'est ce que voit une instance qui n'a rien réglé.
2. Le logo **de l'association** qui déploie l'application est DÉPOSÉ depuis
   `/admin/identite` et vit dans `data/`, à côté des bases.

Pourquoi `data/` et pas `app/static/img/` : ce dernier est versionné, donc un
fichier déposé là serait écrasé au prochain `git pull` — silencieusement, et
précisément le jour d'une mise à jour. `data/` est le dossier des données
propres à une instance ; il est exclu de git (voir `.gitignore`).

Le dossier `data/` n'est PAS déduit d'un chemin en dur : il est pris comme le
PARENT de la base de prêt (`app.db.get_database_path()`), exactement comme
`app/sauvegarde.py` situe `data/sauvegardes/`. Une instance qui déplace ses
bases par `DATABASE_PATH` emporte donc son logo avec elles, et les tests
écrivent dans leur dossier temporaire sans rien laisser dans le dépôt.

PROVENANCE DES FICHIERS VERSIONNÉS — DUPLICATION ASSUMÉE
--------------------------------------------------------
`app/static/img/logo.svg`, `logo.png`, `favicon-192.png` et `favicon-512.png`
sont des COPIES tirées de `logo/`, le dossier d'atelier de l'identité LudoteX :

    logo.svg          <- logo/01_Symbole_sans_logotype/master/
                         LudoteX_A2_symbol_MASTER_V6.svg
    logo.png          <- logo/01_Symbole_sans_logotype/png/1024/
                         LudoteX_A2_symbol_MASTER_V6_1024px.png
    favicon-192.png   <- le même PNG, réduit à 192 x 192
    favicon-512.png   <- le même PNG, réduit à 512 x 512

Deux domiciles pour la même image, donc, et c'est voulu : `logo/` est la SOURCE
(masters, déclinaisons, `LICENCE.md`), `app/static/img/` est ce que l'application
SERT — sous des noms neutres, sans marque d'association, et sans que le dossier
d'atelier ait à être déployé sur un serveur. Le logotype complet (« LudoteX »
écrit) reste dans `logo/` et n'entre pas ici : dans une instance déployée, le
bandeau porte le nom de l'association, pas celui du logiciel.

CE QUE LE DÉPÔT D'UN FICHIER FAIT SUBIR À L'IMAGE (et pourquoi)
---------------------------------------------------------------
Un envoi de fichier est une surface d'attaque à lui seul. Dans l'ordre :

1. **Taille bornée** (`TAILLE_MAX_OCTETS`), vérifiée en lisant AU PLUS cette
   taille + 1 octet — voir `lire_borne`. Rien d'énorme n'est donc chargé en
   mémoire, ni décodé.
2. **Format déterminé par Pillow** (`Image.open` puis `verify()`), JAMAIS par
   l'extension du fichier ni par le `Content-Type` annoncé par le client : les
   deux sont écrits par la personne qui téléverse.
3. **PNG, JPEG et WebP acceptés — le SVG est REFUSÉ.** Le logo par défaut est
   pourtant un SVG, et cette asymétrie est délibérée : un SVG est du XML, il
   peut porter du `<script>`, et servi depuis NOTRE origine il s'exécute dans
   NOTRE contexte — c'est une XSS stockée. Le fichier par défaut vient de nous
   et a été relu ; un fichier téléversé ne vient de personne de confiance.
   Ne pas « corriger » ce point en ajoutant le SVG à `FORMATS_ACCEPTES`.
4. **Dimensions bornées** (`DIMENSION_MAX`), lues AVANT tout décodage : une
   image de 40 000 x 40 000 pixels tient dans quelques kilo-octets de PNG et
   demanderait plusieurs gigaoctets une fois décompressée.
5. **Ré-encodage systématique en PNG**, jamais une copie de l'octet reçu : le
   fichier écrit est reconstruit à partir des pixels, ce qui laisse dehors les
   métadonnées EXIF et toute charge utile accolée à l'image.
6. **Trois noms de sortie FIXES.** Le nom de fichier annoncé par le client
   n'entre dans aucun chemin, à aucun moment — il n'est même pas conservé.
7. **Écriture atomique** : fichier temporaire dans `data/`, puis `os.replace`.
   Une coupure en cours d'écriture ne peut pas laisser un PNG tronqué en place.
"""

from __future__ import annotations

import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

# Dossier des fichiers VERSIONNÉS (identité LudoteX), servi en repli.
DOSSIER_VERSIONNE = Path(__file__).resolve().parent / "static" / "img"

# Les trois noms produits par un dépôt, identiques dans `data/` et dans
# `app/static/img/` : la route de service n'a ainsi qu'un nom à connaître pour
# aller chercher l'un ou l'autre.
NOM_LOGO = "logo.png"
NOM_FAVICON_192 = "favicon-192.png"
NOM_FAVICON_512 = "favicon-512.png"
NOMS = (NOM_LOGO, NOM_FAVICON_192, NOM_FAVICON_512)

# 2 Mio : de quoi accepter très largement un logo d'association exporté sans
# précaution depuis un logiciel de dessin, sans accepter une photo brute de
# reflex. La borne est vérifiée AVANT lecture complète (voir `lire_borne`).
TAILLE_MAX_OCTETS = 2 * 1024 * 1024

# 2000 pixels de côté au plus, avant redimensionnement. Ce n'est pas une borne
# de confort : c'est la protection contre les images à taux de compression
# pathologique, dont le poids du fichier ne dit rien du coût de décodage.
DIMENSION_MAX = 2000

# Côté du logo enregistré. Le fichier versionné fait 1024 px ; au-delà, on
# stockerait des pixels que personne n'affiche (240 px sur une étiquette,
# 160 px sur l'accueil), et l'agrandissement n'est jamais fait : une image plus
# petite que cette borne est conservée telle quelle.
COTE_LOGO = 1024

# Formats acceptés à l'envoi. LIRE LE POINT 3 DE LA DOCSTRING DU MODULE avant
# d'y toucher : l'absence de SVG n'est pas un oubli.
FORMATS_ACCEPTES = ("PNG", "JPEG", "WEBP")


class LogoRefuse(Exception):
    """
    Un fichier déposé a été refusé. RIEN n'a été écrit.

    Porte deux textes distincts, comme les refus de la page « Identité » :
    - `detail` : un mot du vocabulaire du journal d'activité (pas de valeur
      saisie, pas de nom de fichier — voir docs/conception-journal.md) ;
    - `message` (celui de l'exception) : la phrase montrée au bureau, qui doit
      dire quoi faire et pas seulement ce qui ne va pas.
    """

    def __init__(self, detail: str, message: str):
        super().__init__(message)
        self.detail = detail
        self.message = message


# ---------------------------------------------------------------------------
# Où vivent les fichiers
# ---------------------------------------------------------------------------
def dossier_regle() -> Path:
    """
    Dossier `data/` de cette instance : le PARENT de la base de prêt.

    Même façon de situer `data/` que `app.sauvegarde.sauvegarde_de_securite`.
    Import fait DANS la fonction pour que le chemin soit relu à chaque appel
    (une fixture de test qui remplace `get_database_path` est donc suivie).
    """
    from app.db import get_database_path

    return get_database_path().parent


def chemin_regle(nom: str) -> Path:
    """Chemin du fichier DÉPOSÉ par l'association (peut ne pas exister)."""
    return dossier_regle() / nom


def chemin_versionne(nom: str) -> Path:
    """Chemin du fichier VERSIONNÉ (identité LudoteX)."""
    return DOSSIER_VERSIONNE / nom


def chemin_servi(nom: str) -> Path | None:
    """
    Le fichier à servir pour `nom` : celui de l'association s'il existe, sinon
    celui du dépôt, sinon None (aucun des deux n'est lisible).

    NE LÈVE JAMAIS : cette fonction est appelée par la route d'image ET par le
    calcul du paramètre de cache. Un `data/` illisible (droits, montage perdu)
    doit dégrader l'affichage vers l'identité LudoteX, jamais rendre une page
    inaccessible.
    """
    try:
        regle = chemin_regle(nom)
        if regle.is_file():
            return regle
    except OSError:
        pass
    try:
        versionne = chemin_versionne(nom)
        if versionne.is_file():
            return versionne
    except OSError:
        pass
    return None


def logo_regle() -> bool:
    """Vrai si l'association a déposé son logo (au moins le fichier principal)."""
    try:
        return chemin_regle(NOM_LOGO).is_file()
    except OSError:
        return False


def version_servie() -> int:
    """
    Entier qui change quand le logo change — paramètre `?v=` des gabarits.

    MÊME MOTIF qu'`asset_v` (app/templating.py) : la date de modification du
    fichier, en secondes. La différence est ailleurs : le logo n'est pas sous
    `app/static/` (voir CACHE ci-dessus) et se dépose depuis /admin/identite
    EN COURS DE SERVICE, sans redémarrage — d'où une fonction dédiée plutôt
    qu'un appel à `asset_v`, qui ne connaît qu'un seul dossier et aucun repli.

    Ne lève jamais : un logo dont on ne sait pas dater le fichier vaut 0, et
    l'image reste servie.
    """
    chemin = chemin_servi(NOM_LOGO)
    if chemin is None:
        return 0
    try:
        return int(chemin.stat().st_mtime)
    except OSError:
        return 0


def chemin_logo_etiquettes() -> Path | None:
    """
    Logo à imprimer sur les ÉTIQUETTES : `data/logo.png`, ou None.

    RÈGLE DIFFÉRENTE DE CELLE DE L'ÉCRAN, ET C'EST VOULU — voir le commentaire
    de `app.etiquettes.charger_logo`, qui est le domicile de ce raisonnement.
    """
    try:
        chemin = chemin_regle(NOM_LOGO)
        return chemin if chemin.is_file() else None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Dépôt d'un fichier
# ---------------------------------------------------------------------------
def lire_borne(flux: BinaryIO) -> bytes:
    """
    Lit le fichier reçu, en refusant AVANT de tout charger ce qui dépasse la
    borne.

    On lit `TAILLE_MAX_OCTETS + 1` octets : s'il en revient autant, c'est qu'il
    en restait, et le fichier est refusé sans que le reste soit jamais lu ni
    décodé. Un octet de plus que la borne, donc, et pas un fichier entier.

    À NE PAS RECOPIER DES DEUX AUTRES ENVOIS DE L'ADMINISTRATION : `/admin/
    donnees/import` et `/admin/sauvegarde/import` font `fichier.file.read()`
    sans borne. Les deux sont derrière le mot de passe administrateur, ce qui
    limite la portée, mais ce n'est pas un modèle à suivre.

    Précision pour ne rien promettre de faux : le corps de la requête a déjà
    été reçu par le serveur (Starlette le met de côté, sur disque au-delà d'un
    seuil) avant que cette fonction soit appelée. La borne protège la MÉMOIRE
    du processus et le coût de décodage, pas la bande passante.
    """
    contenu = flux.read(TAILLE_MAX_OCTETS + 1)
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise LogoRefuse(
            "logo_trop_lourd",
            f"Ce fichier est trop lourd (limite : "
            f"{TAILLE_MAX_OCTETS // (1024 * 1024)} Mo). Réduisez son poids ou "
            f"exportez-le dans une taille plus modeste, puis réessayez.",
        )
    return contenu


def _ressemble_a_du_svg(contenu: bytes) -> bool:
    """
    Reconnaît un SVG (ou un fichier XML quelconque) au début de son contenu.

    Pillow refuserait de toute façon ce fichier — il ne sait pas lire le SVG —
    mais avec un message qui parlerait d'« image illisible ». Or déposer le
    logo au format SVG est le geste le plus naturel du monde pour qui vient
    d'en recevoir un de son graphiste : il mérite une phrase qui dise quoi
    faire, pas un constat d'échec. D'où cette reconnaissance, dont l'unique
    rôle est le libellé du refus.
    """
    debut = contenu[:512].lstrip().lower()
    return debut.startswith(b"<?xml") or debut.startswith(b"<svg") or b"<svg" in debut


def controler(contenu: bytes) -> Image.Image:
    """
    Contrôle un fichier reçu et renvoie l'image décodée, ou lève `LogoRefuse`.

    Ordre volontaire : SVG (pour le message), puis format réel, puis
    dimensions, puis seulement décodage complet. RIEN N'EST ÉCRIT ICI — c'est
    ce qui permet à la route d'administration de rassembler tous les refus du
    formulaire (deux champs d'URL et le fichier) avant de décider d'écrire ou
    non, sans jamais laisser un logo à moitié remplacé derrière elle.
    """
    if not contenu:
        raise LogoRefuse(
            "logo_vide",
            "Aucun fichier n'a été reçu, ou il est vide. Choisissez une image "
            "puis enregistrez de nouveau.",
        )

    if _ressemble_a_du_svg(contenu):
        raise LogoRefuse(
            "logo_svg_refuse",
            "Les images SVG ne sont pas acceptées, pour des raisons de "
            "sécurité. Exportez votre logo en PNG (fond transparent conservé) "
            "et déposez ce fichier-là.",
        )

    # `verify()` contrôle la cohérence du fichier SANS décoder les pixels, mais
    # laisse l'objet inutilisable : d'où une seconde ouverture plus bas. C'est
    # le mode d'emploi de Pillow, pas une maladresse.
    try:
        sonde = Image.open(BytesIO(contenu))
        format_reel = sonde.format
        dimensions = sonde.size
        sonde.verify()
    except UnidentifiedImageError:
        raise LogoRefuse(
            "logo_pas_une_image",
            "Ce fichier n'est pas une image que l'application sache lire. "
            "Formats acceptés : PNG, JPEG et WebP.",
        ) from None
    except Exception:  # noqa: BLE001 - fichier tronqué, corrompu, incohérent
        raise LogoRefuse(
            "logo_illisible",
            "Ce fichier semble être une image, mais il est abîmé et n'a pas pu "
            "être lu. Réexportez-le depuis votre logiciel de dessin.",
        ) from None

    if format_reel not in FORMATS_ACCEPTES:
        raise LogoRefuse(
            "logo_format_refuse",
            f"Format d'image non accepté ({format_reel or 'inconnu'}). "
            f"Formats acceptés : PNG, JPEG et WebP.",
        )

    largeur, hauteur = dimensions
    if largeur > DIMENSION_MAX or hauteur > DIMENSION_MAX:
        raise LogoRefuse(
            "logo_trop_grand",
            f"Cette image est trop grande ({largeur} x {hauteur} pixels ; "
            f"limite : {DIMENSION_MAX} x {DIMENSION_MAX}). Réduisez-la avant "
            f"de la déposer — {COTE_LOGO} pixels de côté suffisent largement.",
        )

    try:
        image = Image.open(BytesIO(contenu))
        image.load()
        return image.convert("RGBA")
    except Exception:  # noqa: BLE001 - le décodage complet peut encore échouer
        raise LogoRefuse(
            "logo_illisible",
            "Cette image n'a pas pu être décodée en entier. Réexportez-la "
            "depuis votre logiciel de dessin, puis réessayez.",
        ) from None


def _reduit(image: Image.Image, cote: int) -> Image.Image:
    """Copie réduite pour tenir dans `cote` x `cote`, proportions conservées."""
    copie = image.copy()
    copie.thumbnail((cote, cote), Image.LANCZOS)
    return copie


def _carre(image: Image.Image, cote: int) -> Image.Image:
    """
    Vignette CARRÉE de `cote` pixels, image centrée sur un fond transparent.

    Une icône d'onglet ou d'écran d'accueil est carrée ; un logo, presque
    jamais. Deux façons de s'en sortir : recadrer, ou compléter. On complète,
    parce qu'un recadrage automatique coupe ce qu'il ne comprend pas — et sur
    un logo, ce qu'il coupe est souvent le nom.
    """
    vignette = _reduit(image, cote)
    fond = Image.new("RGBA", (cote, cote), (0, 0, 0, 0))
    fond.paste(
        vignette,
        ((cote - vignette.width) // 2, (cote - vignette.height) // 2),
        vignette,
    )
    return fond


def enregistrer(contenu: bytes) -> None:
    """
    Contrôle le fichier reçu et écrit les TROIS images de `data/`.

    Un seul envoi produit les trois tailles : demander au bureau de préparer
    puis de déposer trois fichiers serait trois occasions de se tromper, pour
    un résultat que la machine sait produire.

    Écriture atomique : chaque image est d'abord écrite dans un fichier
    temporaire du MÊME dossier (condition d'`os.replace`, qui ne franchit pas
    les systèmes de fichiers), et les trois remplacements n'ont lieu qu'une
    fois les trois rendus réussis. Une panne à mi-chemin ne laisse donc jamais
    un PNG tronqué en place.

    Raises:
        LogoRefuse: si le fichier est refusé. Dans ce cas RIEN n'est écrit, et
            un logo déjà en place reste intact.
    """
    # Recontrôlé ici même si l'appelant l'a déjà fait : c'est la SEULE
    # fonction qui écrit, elle ne doit rien devoir à la discipline de ses
    # appelants. Le surcoût est le décodage d'une image de 2 Mio au plus.
    image = controler(contenu)

    rendus = {
        NOM_LOGO: _reduit(image, COTE_LOGO),
        NOM_FAVICON_192: _carre(image, 192),
        NOM_FAVICON_512: _carre(image, 512),
    }

    dossier = dossier_regle()
    dossier.mkdir(parents=True, exist_ok=True)

    temporaires: dict[str, Path] = {}
    try:
        for nom, rendu in rendus.items():
            with tempfile.NamedTemporaryFile(
                dir=dossier, prefix=".logo-", suffix=".tmp", delete=False
            ) as tmp:
                chemin_tmp = Path(tmp.name)
            rendu.save(chemin_tmp, format="PNG", optimize=True)
            temporaires[nom] = chemin_tmp
        for nom, chemin_tmp in temporaires.items():
            os.replace(chemin_tmp, dossier / nom)
            temporaires[nom] = dossier / nom
    finally:
        # Ne reste ici que ce qui n'a PAS été déplacé : un rendu raté, ou un
        # remplacement interrompu. Les fichiers déjà en place ne sont pas
        # touchés (leur chemin a été remplacé par la destination ci-dessus).
        for nom, chemin_tmp in temporaires.items():
            if chemin_tmp != dossier / nom:
                try:
                    chemin_tmp.unlink()
                except OSError:
                    pass


def supprimer() -> bool:
    """
    Retire les trois fichiers de `data/` — retour à l'identité LudoteX.

    Returns:
        True si au moins un fichier a été retiré, False s'il n'y avait rien.
    """
    retire = False
    for nom in NOMS:
        try:
            chemin = chemin_regle(nom)
            if chemin.is_file():
                chemin.unlink()
                retire = True
        except OSError:
            pass
    return retire
