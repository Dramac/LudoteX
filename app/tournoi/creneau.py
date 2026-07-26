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

from datetime import date, datetime

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
