"""
Fiche BoardGameGeek d'un titre — DOMICILE UNIQUE de son identifiant.

Le bureau relie un jeu à sa fiche BoardGameGeek (BGG) en collant, sur la fiche
d'administration, soit le numéro de la fiche, soit l'adresse de sa page. Ce
module en extrait l'identifiant (`lire_identifiant`) et reconstruit l'adresse
du lien de vérification (`url_fiche`). L'éditeur de la fiche
(`services.lire_saisie_fiche`) et l'import du catalogue
(`scripts/import_csv.py`, colonne « Fiche BoardGameGeek ») l'appellent tous
deux : une adresse acceptée à l'écran l'est aussi dans le tableur, et
réciproquement.

La même case accepte une VALEUR RÉSERVÉE, « pas sur BGG » (`PAS_SUR_BGG`,
série « fiche », lot 3) : le bureau a cherché, BGG ne connaît pas ce jeu. Elle
pose la marque `titres.bgg_absent` à l'écran comme à l'import, et l'export
l'écrit dans la colonne : la marque survit à une réinstallation depuis le
fichier.

Il contrôle aussi l'adresse de la VIGNETTE d'une fiche (`lire_vignette`,
lot 4) : la liste des hôtes d'images acceptés a son domicile ici.

AUCUN APPEL RÉSEAU, AUCUN JETON. Ce module ne fait que lire une chaîne. Il
n'importe aucune bibliothèque réseau, et un test le verrouille
(`tests/test_fiche_jeu.py`) : le seul module qui parle à BGG est
`app/bgg_api.py`.

Un identifiant BGG ne désigne personne : ce n'est pas une donnée personnelle.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Hôtes acceptés dans une adresse collée. Ni sous-domaine exotique, ni site
# miroir : une adresse d'un autre site est refusée, même si son chemin
# ressemble à celui de BGG.
HOTES_BGG = frozenset({"boardgamegeek.com", "www.boardgamegeek.com"})

# Chemins d'une fiche de jeu sur BGG : `/boardgame/13/catan` pour un jeu,
# `/boardgameexpansion/325/…` pour une extension. Le nom qui suit le numéro est
# décoratif (BGG le recalcule), on l'ignore.
_CHEMIN_FICHE = re.compile(r"^/(?:boardgame|boardgameexpansion)/(\d+)(?:/|$)")

# Garde-fou contre une saisie démesurée : les identifiants BGG comptent
# aujourd'hui six chiffres. Dix laissent de la marge et restent bien en deçà de
# ce qu'un entier SQLite peut porter.
CHIFFRES_MAX = 10

# L'adresse du lien « Voir sur BoardGameGeek ». Construite à partir du SEUL
# identifiant : rien de ce que le bureau a collé n'est recopié dans la page.
# Le chemin `/boardgame/` vaut pour un jeu ; qu'il mène aussi à une extension
# reste à vérifier dans un navigateur (compte rendu du lot fiche-1).
URL_FICHE = "https://boardgamegeek.com/boardgame/{}"


# La valeur réservée de la case « Fiche BoardGameGeek » : « ce jeu n'est pas
# sur BGG ». Écrite telle quelle par l'export ; relue sans égard à la casse ni
# aux espaces (`est_pas_sur_bgg`). Elle ne contient aucun chiffre : jamais
# confondue avec un numéro.
PAS_SUR_BGG = "pas sur BGG"


def est_pas_sur_bgg(saisie: str | None) -> bool:
    """True si `saisie` est la valeur réservée « pas sur BGG »."""
    return " ".join((saisie or "").split()).casefold() == PAS_SUR_BGG.casefold()


def _entier_positif(chiffres: str) -> int | None:
    """`chiffres` (ASCII seulement) en entier strictement positif, sinon None."""
    if not chiffres.isascii() or not chiffres.isdigit():
        return None
    if len(chiffres) > CHIFFRES_MAX:
        return None
    valeur = int(chiffres)
    return valeur if valeur > 0 else None


def lire_identifiant(saisie: str | None) -> int | None:
    """
    L'identifiant BGG contenu dans `saisie`, ou None s'il n'y en a pas.

    Acceptés, espaces autour tolérés :
    - un numéro seul : `13` ;
    - l'adresse d'une fiche de jeu ou d'extension, avec ou sans `https://`,
      avec ou sans `www.`, avec ou sans le nom qui suit le numéro, une barre
      finale, une requête `?…` ou une ancre `#…`.

    Refusés (None) : `0`, un nombre négatif, une adresse d'un autre site, une
    page de BGG qui n'est pas une fiche de jeu, tout autre texte. La case vide
    se distingue AVANT d'appeler cette fonction : elle n'est pas une erreur.
    """
    valeur = (saisie or "").strip()
    if not valeur:
        return None
    if valeur.isdigit():
        return _entier_positif(valeur)
    if "://" not in valeur:
        valeur = "https://" + valeur
    try:
        morceaux = urlsplit(valeur)
    except ValueError:
        return None
    if morceaux.scheme.lower() not in ("http", "https"):
        return None
    if (morceaux.hostname or "").lower() not in HOTES_BGG:
        return None
    trouve = _CHEMIN_FICHE.match(morceaux.path)
    return _entier_positif(trouve.group(1)) if trouve else None


def url_fiche(bgg_id: int) -> str:
    """L'adresse de la fiche BGG de l'identifiant `bgg_id`."""
    return URL_FICHE.format(int(bgg_id))


# ---------------------------------------------------------------------------
# Vignette de la boîte (série « fiche », lot 4)
# ---------------------------------------------------------------------------
# BGG décrit chaque fiche avec l'adresse d'une petite image de la boîte
# (`<thumbnail>`, environ 200 × 150). La fiche d'administration l'affiche, le
# NAVIGATEUR la charge directement chez BGG : LudoteX ne la télécharge, ne la
# relaie et ne la stocke jamais — seule l'adresse est gardée
# (`fiches_bgg.vignette`).
#
# Une adresse venue de l'extérieur n'entre dans une page qu'après contrôle :
# `https:` seulement, hôte EXACTEMENT dans cette liste, longueur bornée.
#
# CETTE LISTE A UNE COPIE : la Content-Security-Policy des pages
# d'administration, dans `deploy/nginx-ludotex.conf` (`img-src`). Les deux
# mondes ne partagent aucun fichier ; `tests/test_nginx_configuration.py`
# vérifie qu'ils disent la même chose. Ajouter un hôte ici sans le reporter là
# donnerait des vignettes bloquées en production.
#
# Relevé le 2026-10-14 sur les 828 fiches du cache de la collecte : 821
# vignettes, toutes en `https://cf.geekdo-images.com/…`, 141 caractères au plus.
HOTES_VIGNETTE = frozenset({"cf.geekdo-images.com"})

# Trois fois la plus longue adresse relevée.
LONGUEUR_VIGNETTE_MAX = 400

# Les caractères d'une adresse (RFC 3986), sans guillemet, crochet, espace ni
# caractère de contrôle : rien qui puisse sortir d'un attribut HTML, même si
# l'échappement de Jinja venait à manquer.
_CARACTERES_ADRESSE = re.compile(r"[A-Za-z0-9\-._~:/?#@!$&()*+,;=%]+")


def lire_vignette(adresse: str | None) -> str:
    """
    L'adresse d'une vignette BGG si elle peut entrer dans une page, sinon "".

    Acceptée seulement : `https://<hôte de HOTES_VIGNETTE>/…`, sans port, sans
    nom d'utilisateur, au plus `LONGUEUR_VIGNETTE_MAX` caractères, espaces
    autour tolérés. Tout le reste — absence, `http:`, autre hôte, sous-domaine,
    adresse démesurée — rend "" : « pas de vignette », jamais une erreur.
    """
    adresse = (adresse or "").strip()
    if not adresse or len(adresse) > LONGUEUR_VIGNETTE_MAX:
        return ""
    if not _CARACTERES_ADRESSE.fullmatch(adresse):
        return ""
    try:
        morceaux = urlsplit(adresse)
    except ValueError:
        return ""
    if morceaux.scheme != "https" or morceaux.netloc not in HOTES_VIGNETTE:
        return ""
    if not morceaux.path.startswith("/"):
        return ""
    return adresse
