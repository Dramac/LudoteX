"""
Helpers de CRÉNEAU partagés par les modules du sous-paquet `tournoi`.

Extrait de `app/tournoi/services.py` à l'occasion du module « Programme du
week-end » (docs/conception-programme.md §3/§5) : la frise de la page
d'accueil (`tournoi.services.planning`) et la fusion `programme.imminents`
ont toutes deux besoin des mêmes notions de granularité de grille, de libellé
de jour et de répartition en couloirs. `tournoi.services` réimporte ces noms
tels quels (aucune signature publique ne change, aucun test existant n'a à
être modifié).
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from app.services import FUSEAU_LOCAL

# Sert la frise de la page d'accueil. Granularité d'une « ligne » = SLOT_MIN ;
# un tournoi/élément de programme sans durée renseignée occupe DUREE_DEFAUT_MIN.
SLOT_MIN = 30
DUREE_DEFAUT_MIN = 60

_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def label_jour(j: date) -> str:
    """Libellé lisible d'une date, ex. « samedi 13 juin »."""
    return f"{_JOURS_FR[j.weekday()]} {j.day} {_MOIS_FR[j.month]}"


def _local_naive(iso_utc: str) -> datetime:
    """Horodatage UTC ISO -> datetime local NAÏF (sans tz, pour l'arithmétique de grille)."""
    return datetime.fromisoformat(iso_utc).astimezone(FUSEAU_LOCAL).replace(tzinfo=None)


def bornes_bloc(date_heure_iso: str | None, duree_min: int | None):
    """
    Bornes locales (début, fin) d'un créneau, ou None si la date est
    absente/invalide (jamais bloquant : l'appelant ignore la ligne).

    Applique les deux règles communes à la frise des tournois et à celle du
    programme : une durée absente vaut DUREE_DEFAUT_MIN, et un créneau ne
    déborde jamais sur le lendemain (la grille est journalière).
    """
    if not date_heure_iso:
        return None
    try:
        debut = _local_naive(date_heure_iso)
    except (ValueError, TypeError):
        return None
    fin = debut + timedelta(minutes=duree_min or DUREE_DEFAUT_MIN)
    minuit_suivant = datetime.combine(debut.date(), time()) + timedelta(days=1)
    return debut, min(fin, minuit_suivant)


def assembler_jours(par_jour: dict, jours: list[date], *, cle_tri: str = "nom") -> list[dict]:
    """
    Transforme des blocs regroupés par jour en grille prête à afficher :
    tri, répartition en couloirs, coordonnées de grille (lignes/colonnes en
    pas de SLOT_MIN) et étiquettes d'heures.

    Chaque bloc doit porter `debut_dt`, `fin_dt` et la clé de tri secondaire
    (`cle_tri`, « nom » pour un tournoi, « intitulé » pour un élément de
    programme). Renvoie une liste de dicts, un par jour, dans l'ordre de
    `jours`.

    Extrait de `tournoi.services.planning` et de `programme.grille`, qui en
    portaient chacun une copie littérale : la frise fusionnée de l'accueil en
    a besoin une troisième fois. Le rendu CSS est identique pour les trois.
    """
    resultat = []
    for j in jours:
        blocs = sorted(par_jour[j], key=lambda b: (b["debut_dt"], b.get(cle_tri) or ""))
        jour = {"date": j, "label": label_jour(j), "blocs": blocs, "vide": not blocs}
        if blocs:
            jour["nb_couloirs"] = _calculer_couloirs(blocs)
            h0 = min(b["debut_dt"] for b in blocs).replace(minute=0, second=0, microsecond=0)
            fin_max = max(b["fin_dt"] for b in blocs)
            if fin_max.minute or fin_max.second:      # arrondi à l'heure supérieure
                fin_max = fin_max.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            jour["nb_slots"] = int((fin_max - h0).total_seconds() // 60 // SLOT_MIN)
            for b in blocs:
                debut_min = (b["debut_dt"] - h0).total_seconds() / 60
                duree_bloc = (b["fin_dt"] - b["debut_dt"]).total_seconds() / 60
                b["row_debut"] = int(debut_min // SLOT_MIN) + 1
                b["row_span"] = max(1, math.ceil(duree_bloc / SLOT_MIN))
                b["col"] = b["couloir"] + 2            # colonne 1 = gouttière des heures
            heures, h = [], h0
            while h < fin_max:
                heures.append({"row": int((h - h0).total_seconds() // 60 // SLOT_MIN) + 1,
                               "label": h.strftime("%Hh")})
                h += timedelta(hours=1)
            jour["heures"] = heures
        resultat.append(jour)
    return resultat


def _calculer_couloirs(blocs: list[dict]) -> int:
    """
    Affecte un couloir (colonne) à chaque bloc pour gérer les chevauchements, par
    partition d'intervalles : chaque bloc prend le premier couloir libre (dont le
    dernier bloc est terminé). `blocs` doit être trié par début. Modifie chaque
    bloc (clé 'couloir') et renvoie le nombre de couloirs utilisés.
    """
    fins_couloirs: list[datetime] = []
    for b in blocs:
        place = False
        for i, fin in enumerate(fins_couloirs):
            if b["debut_dt"] >= fin:        # ce couloir s'est libéré
                b["couloir"] = i
                fins_couloirs[i] = b["fin_dt"]
                place = True
                break
        if not place:
            b["couloir"] = len(fins_couloirs)
            fins_couloirs.append(b["fin_dt"])
    return len(fins_couloirs)
