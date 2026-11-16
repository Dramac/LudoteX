"""
Le comptoir heure par heure (lot bilan-4) : les chiffres, puis la géométrie
des deux graphiques de `/stats`.

À quoi ça sert : dire aux bénévoles QUAND ouvrir une file réservée aux
retours, et COMBIEN de personnes placer au comptoir à chaque créneau. Deux
graphiques et un tableau, sur la même suite d'heures :

- les prêts et les retours de chaque heure (barres groupées, un seul axe) ;
- le nombre de jeux dehors à la fin de chaque heure (une ligne, second
  graphique : jamais deux échelles sur un même graphique) ;
- le tableau des mêmes chiffres, version lisible par un lecteur d'écran, et
  la feuille « Heure par heure » de l'export Excel.

Module à part, comme `app/editions.py` : `services.py` dépasse 5 500 lignes.
Il remplace `services.prets_par_heure` (un seul domicile pour « par heure »).
Il ne lit que la base de prêt, ne journalise rien et ne porte aucune donnée
personnelle : des comptes par heure.

LES DÉFINITIONS (reprises sous le graphique, `NOTE_DEFINITIONS`) :

- **Prêts** : les sorties au public (`services.MOTIFS_COMPTES` : `pret` et
  `oubli`), à l'heure de `date_sortie`, dans la période — la même règle que
  le total des prêts, dont la somme des barres redonne donc le chiffre.
- **Retours** : les prêts `pret` revenus, à l'heure de `date_retour`, dans la
  période. Deux exclusions :
  - un retour NON SCANNÉ (`oubli`) n'a pas d'heure de retour réelle : sa
    `date_retour` est le moment où l'on s'aperçoit de l'oubli, parfois des
    heures plus tard ;
  - une FERMETURE PAR LA CLÔTURE n'est pas un retour : `cloturer_evenement`
    pose la même `date_retour` sur tous les prêts encore ouverts, d'un seul
    `UPDATE`, sans changer le motif. Compter ces lignes ferait un faux pic à
    l'heure de la clôture. Voir `instants_de_cloture`.
- **Jeux dehors à la fin de l'heure** : les prêts au public sortis avant la
  fin de l'heure et pas encore revenus à cet instant, y compris ceux sortis
  AVANT le début de la période. On lit l'état de la base tel quel :
  - un oubli reste dehors jusqu'à ce qu'on s'en aperçoive (la seule date
    connue), ce qui SURESTIME la courbe ;
  - une fermeture par la clôture reste dehors jusqu'à la clôture. La clôture
    n'est pas une activité, elle n'ajoute donc aucune heure au graphique :
    la courbe s'arrête à la dernière heure de prêts ou de retours, avec les
    jeux encore dehors à ce moment-là, sans chute artificielle.
  Conséquence assumée : d'une heure à l'autre, « dehors » ne s'écrit pas
  toujours « dehors d'avant + prêts − retours » : un oubli découvert ou une
  clôture le fait baisser sans retour compté.
- Les sorties **tournoi** et les **erreurs de prêt** (`erreur`) ne sont nulle
  part : ni prêts, ni retours, ni dehors.

LES HEURES MONTRÉES : par journée (de 6 h à 6 h le lendemain, voir
`HEURE_DEBUT_JOURNEE`), de la première à la dernière heure qui a au moins un
prêt ou un retour, les heures creuses du milieu comprises (à zéro). La nuit
n'étire donc pas le graphique, même après une soirée qui passe minuit, et une heure creuse au milieu
d'une journée ne disparaît pas. Les heures sont parcourues en UTC puis
converties : le jour du passage à l'heure d'hiver a deux « 2 h », qui se
distinguent (« 2 h » et « 2 h bis ») ; le jour du passage à l'heure d'été
n'a pas de « 2 h », et ce n'est pas une heure à zéro.

LE PIC : l'heure où le plus de jeux sont dehors à la fin de l'heure. En cas
d'égalité, la PREMIÈRE de ces heures.
"""

from __future__ import annotations

import math
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from app.services import FUSEAU_LOCAL, FUSEAU_UTC, MOTIFS_COMPTES, pluriel

# Une `date_retour` partagée À LA SECONDE par au moins CLOTURE_MIN_PRETS
# lignes, à un instant où plus AUCUN autre prêt (tous motifs) n'était ouvert,
# est une fermeture par la clôture. Le nombre seul ne suffit pas : deux vrais
# retours scannés dans la même seconde sur deux téléphones arrivent (mesuré
# sur le week-end fictif de l'essai réel, voir le compte rendu du lot), alors
# que la clôture, elle, ferme TOUT ce qui est ouvert. Le second critère fait
# donc le tri, et le seuil reste au plus bas : 2.
# Limites, assumées et testées :
# - une clôture qui ne ferme qu'UN prêt est indiscernable d'un dernier retour :
#   elle compte comme un retour ;
# - deux vrais retours dans la même seconde, qui sont aussi les deux derniers
#   jeux dehors, passent pour une clôture : ils ne sont pas comptés ;
# - une clôture qui ne ferme rien ne laisse aucune trace : rien à écarter ;
# - deux clôtures dans la même période (formation, fausse manœuvre) sont
#   reconnues l'une et l'autre.
CLOTURE_MIN_PRETS = 2

# Une JOURNÉE de l'événement commence à HEURE_DEBUT_JOURNEE heures locales :
# de 0 h à 5 h, une heure appartient à la journée de la veille. Sans ce
# décalage, une soirée qui finit à 0 h 30 rattachait 0 h au dimanche, et le
# dimanche s'étirait de 0 h à 19 h, nuit comprise, en heures à zéro (trouvé
# sur le week-end fictif de l'essai réel). Une ludothèque ouverte entre 5 h
# et 7 h verrait sa nuit coupée à 6 h : sans conséquence sur les chiffres.
HEURE_DEBUT_JOURNEE = 6

# Au-delà de MAX_JOURS journées d'activité, la page ne dessine pas : quelques
# centaines de barres ne se lisent pas, et « Toute la période » sur plusieurs
# années mêle des éditions. Une phrase invite à choisir une édition ; la
# feuille Excel garde toutes les heures. Un événement dure deux ou trois
# jours : une semaine laisse de la marge.
MAX_JOURS = 7

JOURS_COURTS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")

# --- Libellés, mot pour mot sur la page et dans l'export -------------------
TITRE_SECTION = "Le comptoir heure par heure"
PHRASE_SENS = ("Pour décider quand ouvrir une file réservée aux retours, et "
               "combien de bénévoles placer au comptoir à chaque créneau.")
TITRE_FLUX = "Prêts et retours, heure par heure"
TITRE_DEHORS = "Jeux dehors à la fin de chaque heure"
TITRE_TABLEAU = "Les chiffres heure par heure"
SERIE_PRETS = "Prêts"
SERIE_RETOURS = "Retours"
ENTETES_TABLEAU = ("Heure", "Prêts", "Retours", "Jeux dehors à la fin de l'heure")
LIBELLE_PIC = "jeux dehors au pic"
NOTE_DEFINITIONS = (
    "Prêts : chaque sortie au public, à son heure. Retours : chaque retour "
    "scanné, à son heure ; un retour non scanné n'en est pas un, car l'heure "
    "où l'on s'en aperçoit n'est pas celle du retour. Jeux dehors : les jeux "
    "prêtés et pas encore rendus à la fin de l'heure, y compris ceux sortis "
    "avant la période ; un retour non scanné y reste compté jusqu'à ce qu'on "
    "s'en aperçoive, ce qui peut gonfler la courbe. Les sorties pour un "
    "tournoi et les erreurs de prêt ne sont pas comptées."
)
NOTE_HEURES = (
    "Heures locales. Chaque journée va de sa première à sa dernière heure de "
    "prêts ou de retours ; une heure creuse entre les deux est montrée à "
    "zéro, la nuit ne l'est pas. Une soirée qui passe minuit reste sur sa "
    "journée. Le pic est la première heure où le plus de jeux sont dehors."
)
PHRASE_VIDE = "Aucun prêt ni retour sur la période."


def phrase_fermetures(n: int) -> str:
    """La phrase sous le graphique quand des fermetures par la clôture ont été écartées."""
    return (f"{n} {pluriel(n, 'jeu rendu', 'jeux rendus')} à la clôture "
            f"{pluriel(n, 'n’est pas compté', 'ne sont pas comptés')} comme "
            f"{pluriel(n, 'retour', 'retours')} : "
            f"{pluriel(n, 'il reste compté', 'ils restent comptés')} dehors "
            "jusqu'à la clôture.")


def phrase_trop_long(nb_jours: int) -> str:
    """La phrase qui remplace les graphiques au-delà de `MAX_JOURS` jours d'activité."""
    return (f"La période couvre {nb_jours} jours d'activité : choisissez une "
            "édition dans le menu « Période » pour voir le comptoir heure par "
            "heure. L'export Excel donne toutes les heures, feuille « Heure "
            "par heure ».")


# ---------------------------------------------------------------------------
# Les chiffres
# ---------------------------------------------------------------------------
def _lire(valeur) -> datetime | None:
    """
    Un horodatage de la base, en UTC, ou None s'il est illisible. Un
    horodatage SANS fuseau est tenu pour illisible : on stocke toujours en
    UTC avec « +00:00 » (`services.maintenant`), et l'interpréter dans le
    fuseau de la machine serait deviner.
    """
    if not isinstance(valeur, str):
        return None
    try:
        dt = datetime.fromisoformat(valeur)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(FUSEAU_UTC)


def _heure(dt: datetime) -> datetime:
    """Le début de l'heure UTC qui contient `dt`. Paris est décalé d'heures
    entières : une heure UTC est toujours une heure locale."""
    return dt.replace(minute=0, second=0, microsecond=0)


def instants_de_cloture(lignes) -> set[str]:
    """
    Les `date_retour` (chaînes exactes) posées par une clôture de fin
    d'événement : partagées par au moins `CLOTURE_MIN_PRETS` lignes, tous
    motifs confondus, à un instant où aucune AUTRE ligne n'était ouverte —
    c'est-à-dire sortie avant et rendue après, ou jamais rendue. Voir
    `CLOTURE_MIN_PRETS` pour les cas limites.

    Args:
        lignes: lignes de `prets` avec `date_sortie` et `date_retour`, TOUS
            motifs : la clôture ferme aussi les sorties tournoi.
    """
    groupes: dict[str, int] = Counter(
        l["date_retour"] for l in lignes if isinstance(l["date_retour"], str))
    candidats = [ts for ts, n in groupes.items() if n >= CLOTURE_MIN_PRETS]
    if not candidats:
        return set()
    lues = []
    for l in lignes:
        sortie = _lire(l["date_sortie"])
        if sortie is None:
            continue
        retour_txt = l["date_retour"]
        retour = _lire(retour_txt) if retour_txt is not None else None
        if retour_txt is not None and retour is None:
            continue                     # retour illisible : on ne sait rien
        lues.append((sortie, retour, retour_txt))
    clotures = set()
    for ts in candidats:
        instant = _lire(ts)
        if instant is None:
            continue
        ouvert_ailleurs = any(
            retour_txt != ts and sortie < instant
            and (retour is None or retour > instant)
            for sortie, retour, retour_txt in lues)
        if not ouvert_ailleurs:
            clotures.add(ts)
    return clotures


def _dans(valeur: str, debut: str | None, fin: str | None) -> bool:
    """Le filtre de période, comparé en CHAÎNES comme le SQL des autres
    statistiques (`services._filtre_periode`) : mêmes bornes, mêmes prêts."""
    return (debut is None or valeur >= debut) and (fin is None or valeur < fin)


def _est_ambigue(local: datetime) -> bool:
    """Vrai pour une heure locale qui existe deux fois (passage à l'heure d'hiver)."""
    return (local.replace(fold=0).utcoffset()
            != local.replace(fold=1).utcoffset())


def _libelles_heure(local: datetime) -> tuple[str, str]:
    """(court, long) : « 15 h » ; le jour du passage à l'heure d'hiver,
    « 2 h » / « 2 h (heure d'été) » puis « 2 h bis » / « 2 h (heure d'hiver) »."""
    base = f"{local.hour} h"
    if not _est_ambigue(local):
        return base, base
    if local.fold == 0:
        return base, f"{base} (heure d'été)"
    return f"{base} bis", f"{base} (heure d'hiver)"


def _journee(h: datetime) -> date:
    """La journée de l'événement à laquelle appartient l'heure UTC `h` : la
    date locale, décalée de `HEURE_DEBUT_JOURNEE` heures (heure murale)."""
    local = h.astimezone(FUSEAU_LOCAL).replace(tzinfo=None)
    return (local - timedelta(hours=HEURE_DEBUT_JOURNEE)).date()


def heure_par_heure(conn: sqlite3.Connection, debut: str | None = None,
                    fin: str | None = None) -> dict:
    """
    Les chiffres du comptoir, heure par heure (définitions en tête de module).

    Args:
        conn: connexion à la base de prêt.
        debut, fin: bornes UTC ISO optionnelles (fin exclusive), celles de
            toutes les statistiques. Prêts filtrés sur `date_sortie`, retours
            sur `date_retour`.

    Une date illisible est ignorée, jamais d'erreur : une sortie illisible
    ne compte nulle part ; un retour illisible ne compte pas comme retour et
    retire le prêt du calcul des jeux dehors (on ne sait pas quand il est
    revenu).

    Returns:
        dict {
          "heures": [{cle, jour, debut_jour, court, libelle, libelle_table,
                      prets, retours, dehors}],   dans l'ordre ;
          "jours": [{date, libelle, premier, nb}] ;
          "nb_jours" (journées), "trop_long" (au-delà de `MAX_JOURS`),
          "fermetures_cloture": retours écartés comme fermetures par la clôture,
          "pic": {"dehors", "libelle", "index"} ou None (aucun jeu dehors),
          "pointes_retours": index des deux plus fortes heures de retours,
          "total_prets", "total_retours"
        }
    """
    lignes = conn.execute(
        "SELECT date_sortie, date_retour, motif FROM prets").fetchall()
    clotures = instants_de_cloture(lignes)

    prets: Counter = Counter()
    retours: Counter = Counter()
    fermetures = 0
    debuts_dehors: list[datetime] = []
    fins_dehors: list[datetime] = []
    for l in lignes:
        if l["motif"] not in MOTIFS_COMPTES:
            continue                                  # tournoi, erreur
        sortie = _lire(l["date_sortie"])
        if sortie is None:
            continue
        if _dans(l["date_sortie"], debut, fin):
            prets[_heure(sortie)] += 1
        retour_txt = l["date_retour"]
        if retour_txt is None:
            debuts_dehors.append(sortie)              # encore dehors
            continue
        retour = _lire(retour_txt)
        if retour is None:
            continue
        debuts_dehors.append(sortie)
        fins_dehors.append(max(retour, sortie))
        if l["motif"] == "pret" and _dans(retour_txt, debut, fin):
            if retour_txt in clotures:
                fermetures += 1
            else:
                retours[_heure(retour)] += 1

    # Les heures montrées : par JOURNÉE (voir `HEURE_DEBUT_JOURNEE`), de la
    # première à la dernière heure active, parcourues en UTC (voir l'en-tête :
    # heure d'été / d'hiver).
    par_jour: dict[date, list[datetime]] = defaultdict(list)
    for h in set(prets) | set(retours):
        par_jour[_journee(h)].append(h)
    jours_tries = sorted(par_jour)

    debuts_dehors.sort()
    fins_dehors.sort()
    plages = []
    for j in jours_tries:
        h, derniere = min(par_jour[j]), max(par_jour[j])
        plage = []
        while h <= derniere:
            plage.append(h)
            h += timedelta(hours=1)
        plages.append((j, plage))
    # Le nom du jour suffit (« sam. ») tant que deux dates montrées ne le
    # partagent pas ; sinon on ajoute la date. Le tableau la porte toujours.
    dates = {h.astimezone(FUSEAU_LOCAL).date() for _, plage in plages for h in plage}
    avec_date = len({d.weekday() for d in dates}) < len(dates)

    heures, jours = [], []
    for j, plage in plages:
        jours.append({"date": j.isoformat(),
                      "libelle": f"{JOURS_COURTS[j.weekday()]} {j:%d/%m}",
                      "premier": len(heures), "nb": len(plage)})
        for h in plage:
            local = h.astimezone(FUSEAU_LOCAL)
            nom_jour = JOURS_COURTS[local.weekday()]
            court, long_ = _libelles_heure(local)
            fin_heure = h + timedelta(hours=1)
            heures.append({
                "cle": h.isoformat(),
                "jour": j.isoformat(),
                "debut_jour": h == plage[0],
                "court": court,
                "libelle": (f"{nom_jour} {local:%d/%m} {long_}" if avec_date
                            else f"{nom_jour} {long_}"),
                "libelle_table": f"{nom_jour} {local:%d/%m} {long_}",
                "prets": prets.get(h, 0),
                "retours": retours.get(h, 0),
                "dehors": (bisect_left(debuts_dehors, fin_heure)
                           - bisect_left(fins_dehors, fin_heure)),
            })

    pic = None
    for i, ligne in enumerate(heures):
        if ligne["dehors"] > 0 and (pic is None or ligne["dehors"] > pic["dehors"]):
            pic = {"dehors": ligne["dehors"], "libelle": ligne["libelle"], "index": i}
    pointes = sorted((i for i, l in enumerate(heures) if l["retours"] > 0),
                     key=lambda i: (-heures[i]["retours"], i))[:2]

    return {
        "heures": heures,
        "jours": jours,
        "nb_jours": len(jours),
        "trop_long": len(jours) > MAX_JOURS,
        "fermetures_cloture": fermetures,
        "pic": pic,
        "pointes_retours": sorted(pointes),
        "total_prets": sum(prets.values()),
        "total_retours": sum(retours.values()),
    }


# ---------------------------------------------------------------------------
# La géométrie des deux graphiques
# ---------------------------------------------------------------------------
# Unités du `viewBox`, rendu à sa taille naturelle (1 unité = 1 px CSS) : le
# graphique n'est jamais étiré ni écrasé, il défile à l'horizontale dans son
# conteneur sur téléphone. Les deux graphiques partagent la même échelle
# horizontale : une heure est à la même abscisse sur l'un et sur l'autre.
LARGEUR_BARRE = 9
ECART_BARRES = 3          # entre la barre des prêts et celle des retours (contour compris)
ECART_HEURES = 8          # entre deux heures
PAS_HEURE = 2 * LARGEUR_BARRE + ECART_BARRES + ECART_HEURES
ECART_JOURS = 20          # en plus, entre deux jours
MARGE_GAUCHE = 40         # graduations
MARGE_DROITE = 16
MARGE_HAUT = 24           # étiquettes au-dessus de la plus haute barre
MARGE_BAS = 40            # heures, puis jours
HAUTEUR_FLUX = 160
HAUTEUR_DEHORS = 130
NB_GRADUATIONS = 4
RAYON_POINT = 3
RAYON_PIC = 5


def graduations(maximum: int, nb: int = NB_GRADUATIONS) -> list[int]:
    """
    Graduations rondes (1, 2 ou 5 × 10ⁿ) de 0 jusqu'au premier multiple qui
    couvre `maximum`, au plus `nb` intervalles. Toujours au moins [0, 1] :
    une période vide garde un axe, sans division par zéro.
    """
    if maximum <= 0:
        return [0, 1]
    puissance = 10 ** max(0, math.floor(math.log10(maximum / nb)))
    for facteur in (1, 2, 5, 10):
        pas = facteur * puissance
        if math.ceil(maximum / pas) <= nb:
            break
    haut = math.ceil(maximum / pas) * pas
    return list(range(0, haut + 1, pas))


def _r(valeur: float) -> float:
    return round(valeur, 1)


def _abscisses(heures: list[dict]) -> list[float]:
    """Le bord gauche du groupe de barres de chaque heure."""
    xs, jour = [], -1
    for i, h in enumerate(heures):
        if h["debut_jour"]:
            jour += 1
        xs.append(MARGE_GAUCHE + ECART_HEURES / 2 + i * PAS_HEURE + jour * ECART_JOURS)
    return xs


def _cadre(heures, xs, jours, hauteur, ticks) -> dict:
    """Ce que les deux graphiques partagent : largeur, graduations, heures,
    jours et séparations entre les jours."""
    largeur = (xs[-1] + PAS_HEURE - ECART_HEURES / 2 + MARGE_DROITE) if xs else (
        MARGE_GAUCHE + MARGE_DROITE)
    base = MARGE_HAUT + hauteur
    haut = ticks[-1]
    lignes_grad = [{"y": _r(base - hauteur * t / haut), "valeur": t} for t in ticks]
    centre = LARGEUR_BARRE + ECART_BARRES / 2
    etiquettes_heures = [{"x": _r(x + centre), "texte": h["court"]}
                         for x, h in zip(xs, heures)]
    etiquettes_jours = [{"x": _r(xs[j["premier"]]), "texte": j["libelle"]}
                        for j in jours]
    separations = [_r(xs[j["premier"]] - ECART_HEURES / 2 - ECART_JOURS / 2)
                   for j in jours[1:]]
    return {
        "largeur": _r(largeur),
        "hauteur": MARGE_HAUT + hauteur + MARGE_BAS,
        "base": _r(base),
        "gauche": MARGE_GAUCHE,
        "droite": _r(largeur - MARGE_DROITE),
        "graduations": lignes_grad,
        "heures": etiquettes_heures,
        "y_heures": _r(base + 14),
        "jours": etiquettes_jours,
        "y_jours": _r(base + 32),
        "separations": separations,
        "haut_trace": MARGE_HAUT,
    }


def geometrie(donnees: dict) -> dict | None:
    """
    Les positions de tous les éléments des deux graphiques, calculées ici pour
    être testées : le gabarit ne fait que les poser. None quand il n'y a rien
    à dessiner (période vide, ou trop longue : voir `MAX_JOURS`).

    Returns:
        {"flux": {cadre…, "barres": [...], "etiquettes": [...]},
         "dehors": {cadre…, "traces": [...], "points": [...], "survols": [...],
                    "etiquettes": [...]}}
        Chaque barre : {x, y, largeur, hauteur, serie ("prets"|"retours"),
        titre}. Hauteurs proportionnelles, axe à 0, jamais négatives.
    """
    heures = donnees["heures"]
    if not heures or donnees["trop_long"]:
        return None
    xs = _abscisses(heures)
    jours = donnees["jours"]

    # --- Prêts et retours : barres groupées, un seul axe ---
    ticks = graduations(max(max(h["prets"], h["retours"]) for h in heures))
    flux = _cadre(heures, xs, jours, HAUTEUR_FLUX, ticks)
    base, haut = MARGE_HAUT + HAUTEUR_FLUX, ticks[-1]
    barres, etiquettes = [], []
    for i, (x, h) in enumerate(zip(xs, heures)):
        for decalage, serie, n, nom in (
                (0, "prets", h["prets"], pluriel(h["prets"], "prêt", "prêts")),
                (LARGEUR_BARRE + ECART_BARRES, "retours", h["retours"],
                 pluriel(h["retours"], "retour", "retours"))):
            hauteur = HAUTEUR_FLUX * n / haut
            barres.append({"x": _r(x + decalage), "y": _r(base - hauteur),
                           "largeur": LARGEUR_BARRE, "hauteur": _r(hauteur),
                           "serie": serie, "titre": f"{h['libelle']} : {n} {nom}"})
        if i in donnees["pointes_retours"]:
            # Au-dessus de la plus haute des deux barres : posée sur la seule
            # barre des retours, l'étiquette touchait la barre des prêts
            # voisine quand elle était plus haute, et se lisait comme la sienne.
            hauteur = HAUTEUR_FLUX * max(h["retours"], h["prets"]) / haut
            etiquettes.append({"x": _r(x + LARGEUR_BARRE + ECART_BARRES + LARGEUR_BARRE / 2),
                               "y": _r(base - hauteur - 5), "texte": str(h["retours"])})
    flux.update(barres=barres, etiquettes=etiquettes)

    # --- Jeux dehors : une ligne par jour (la nuit n'est pas montrée), aire
    # décorative dessous, un point par heure et une zone de survol plus large
    # que le point : toute la colonne de l'heure ---
    ticks_d = graduations(max(h["dehors"] for h in heures))
    dehors = _cadre(heures, xs, jours, HAUTEUR_DEHORS, ticks_d)
    base_d, haut_d = MARGE_HAUT + HAUTEUR_DEHORS, ticks_d[-1]
    centre = LARGEUR_BARRE + ECART_BARRES / 2
    points, survols, traces = [], [], []
    for i, (x, h) in enumerate(zip(xs, heures)):
        cx, cy = _r(x + centre), _r(base_d - HAUTEUR_DEHORS * h["dehors"] / haut_d)
        titre = (f"{h['libelle']} : {h['dehors']} "
                 f"{pluriel(h['dehors'], 'jeu dehors', 'jeux dehors')} à la fin de l'heure")
        points.append({"x": cx, "y": cy, "titre": titre})
        survols.append({"x": _r(x - ECART_HEURES / 2), "y": MARGE_HAUT,
                        "largeur": PAS_HEURE, "hauteur": HAUTEUR_DEHORS, "titre": titre})
    for j in jours:
        pts = points[j["premier"]:j["premier"] + j["nb"]]
        ligne = " ".join(f"{'M' if k == 0 else 'L'}{p['x']},{p['y']}"
                         for k, p in enumerate(pts))
        aire = (f"M{pts[0]['x']},{_r(base_d)} "
                + " ".join(f"L{p['x']},{p['y']}" for p in pts)
                + f" L{pts[-1]['x']},{_r(base_d)} Z")
        traces.append({"ligne": ligne, "aire": aire})
    etiquettes_d = []
    pic = donnees["pic"]
    if pic is not None:
        p = points[pic["index"]]
        etiquettes_d.append({"x": p["x"], "y": _r(p["y"] - RAYON_PIC - 4),
                             "texte": f"{pic['dehors']} au pic"})
    dehors.update(points=points, survols=survols, traces=traces,
                  etiquettes=etiquettes_d,
                  pic=points[pic["index"]] if pic is not None else None)
    return {"flux": flux, "dehors": dehors, "rayon_point": RAYON_POINT,
            "rayon_pic": RAYON_PIC}
