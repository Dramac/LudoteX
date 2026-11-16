"""
Pour les achats (lot bilan-5) : les jeux dont toutes les boîtes étaient
dehors en même temps, et les jeux jamais sortis.

À quoi ça sert : aider le bureau à décider quoi racheter (un exemplaire de
plus pour un jeu souvent épuisé), quoi mettre en avant et quoi retirer du
catalogue (un jeu que personne n'a emprunté). La page de `/stats` montre les
premières lignes des « tous sortis » et le NOMBRE de jamais sortis ; l'export
Excel porte les deux listes complètes (feuilles « Tous sortis » et « Jamais
sortis »).

Module à part, sur le patron d'`app/comptoir.py`, dont il reprend les heures
d'ouverture (voir plus bas). Il ne lit que la base de prêt, ne journalise rien
et ne porte aucune donnée personnelle : des titres, des codes et des durées.

TOUS SORTIS — LES DÉFINITIONS (reprises sous le tableau, `NOTE_DEFINITIONS`) :

- **Ce qui rend une boîte « dehors »** : un prêt au public
  (`services.MOTIFS_COMPTES` : `pret` et `oubli`), de sa sortie à son retour.
  - Une **erreur de prêt** (`erreur`) ne compte pas : la boîte n'est pas
    sortie.
  - Une **sortie tournoi** ne compte pas non plus. Pour un visiteur, la
    boîte manque autant ; mais le bureau choisit les jeux de tournoi, leur
    indisponibilité est voulue, et un jeu de tournoi n'est pas un jeu qu'on
    rachète pour le prêt. Conséquence : un jeu à deux boîtes, l'une en
    tournoi et l'autre prêtée, n'est PAS « tous sortis ».
  - Un **retour non scanné** (`oubli`) reste dehors jusqu'à ce qu'on s'en
    aperçoive, la seule date connue : cela SURESTIME la durée, comme la
    courbe des jeux dehors du comptoir. Gardé, et dit sous le tableau.
  - **Une sortie ne compte que dans la journée où elle a commencé** (de 6 h
    à 6 h, `comptoir.HEURE_DEBUT_JOURNEE`). Une boîte encore dehors à la
    fermeture est presque toujours un retour qu'on n'a pas scanné : la
    ludothèque ferme la nuit. On ne sait pas quand elle est revenue ; la
    compter le lendemain ferait d'un jeu que personne ne demande un jeu
    « tous sortis » toute la journée. Mesuré sur le week-end fictif de
    l'essai : sans cette règle, les huit premières lignes de l'édition
    étaient des boîtes sorties le samedi soir et fermées par la clôture du
    dimanche, chacune à « dim. 10:00 → 19:00 ». Cette règle couvre la
    **fermeture par la clôture** (pas besoin de la reconnaître, comme le
    fait `comptoir.instants_de_cloture` : sa date n'est qu'une fin de
    sortie), le prêt encore ouvert, l'oubli découvert le lendemain, et le
    vrai retour scanné le lendemain matin.
- **Seules les heures d'ouverture comptent** : celles du comptoir heure par
  heure (`comptoir.heure_par_heure`), journée par journée (de 6 h à 6 h),
  de la première à la dernière heure qui a vu un prêt ou un retour, les
  heures creuses du milieu comprises. Sans cette règle, une boîte gardée la
  nuit ferait « tous dehors » pendant huit heures. Une période qui court
  d'une journée sur la suivante fait DEUX périodes. Les fonctions du
  comptoir sont réutilisées telles quelles : on lit son résultat
  (`plages_ouverture`), on ne recopie pas son découpage.
- **Le nombre d'exemplaires est celui d'aujourd'hui** : la base ne garde pas
  l'historique du parc. Une boîte ajoutée après l'édition compte dans
  l'édition : le jeu peut alors ne jamais paraître « tous sortis ».
- **Un retour et un prêt à la même seconde** ne laissent aucun trou : la
  boîte n'a pas été disponible.
- **Les bornes de la période** coupent les durées : un prêt sorti avant le
  début ne compte qu'à partir du début (s'il est de la même journée), un
  prêt encore ouvert qu'à la fin de la dernière heure d'ouverture de sa
  journée.
- **Une date illisible** retire la ligne du calcul, sans erreur (même règle
  que le comptoir) : on ne sait pas quand la boîte était dehors.
- Les durées se calculent en UTC : le passage à l'heure d'hiver n'ajoute ni
  ne retire d'heure.
- **Durée minimale** : `DUREE_MINIMALE_S` cumulée. En dessous, ce n'est pas
  un signal d'achat ; le nombre de jeux écartés est dit sous le tableau.

DEUX LISTES (complément de calibrage, sur les données réelles de 2026) :
pour un jeu à UNE boîte, « toutes dehors » veut seulement dire « prêté ».
Sur l'édition 2026, 163 des 182 jeux « tous sortis » n'avaient qu'une boîte,
et le haut de la liste unique était fait de longs prêts uniques (un jeu
prêté une fois, dehors 10 h 20) ; les 19 jeux à plusieurs boîtes, le vrai
signal, n'atteignaient pas les vingt premières lignes. D'où :

- **les jeux à plusieurs boîtes** dont toutes étaient dehors : la
  saturation est le signal, sans autre filtre ;
- **les jeux à une seule boîte** prêtés au moins `PRETS_MIN_UNE_BOITE` fois
  sur la période : le nombre de prêts est l'indice de demande ; la durée
  dehors classe. Le nombre de jeux écartés faute de prêts est dit.

Le re-prêt rapide après un retour a été mesuré et écarté comme indice : sur
2026, 59 retours de jeux à une boîte suivis d'un prêt en moins de 10 min,
pour un écart médian de 67 min — trop peu, et indiscernable du hasard du
comptoir.

JAMAIS SORTIS : les titres qui ont au moins une boîte et AUCUN prêt au
public (`pret`, `oubli`) sorti dans la période. Un titre sorti seulement en
tournoi en fait partie, signalé par sa colonne.

PERFORMANCE : une seule lecture des prêts (tous motifs : la reconnaissance de
la clôture en a besoin) et une des titres, puis un balayage par titre en
Python. Jamais une requête par titre.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

# `_lire` : la lecture d'un horodatage du comptoir (UTC, None si illisible
# ou sans fuseau), réutilisée pour que les deux sections tiennent la même
# date pour illisible.
from app.comptoir import HEURE_DEBUT_JOURNEE, _heure, _journee, _lire
from app.categories_jeu import cle
from app.services import (FUSEAU_LOCAL, FUSEAU_UTC, MOTIFS_COMPTES,
                          format_duree, lignes_export_catalogue, pluriel)

# En dessous de cette durée CUMULÉE, un jeu n'est pas montré : quelques
# minutes où toutes les boîtes sont dehors arrivent au hasard des retours, et
# ne disent pas qu'il en manque une. Un quart d'heure, comme recommandé au
# lot ; le nombre de jeux écartés est dit sous le tableau.
DUREE_MINIMALE_S = 15 * 60

# Un jeu à une seule boîte n'est montré que s'il a été prêté au moins
# PRETS_MIN_UNE_BOITE fois sur la période (voir l'en-tête). Calibré sur 2026 :
# 76 des 163 jeux à une boîte « tous sortis » ; à 4, 58 ; à 5, 43. Trois
# prêts, c'est une boîte qui a enchaîné les tables, pas une longue partie.
PRETS_MIN_UNE_BOITE = 3

# Lignes montrées sur la page, par liste ; l'export Excel les donne toutes.
LIGNES_PAGE = 20

JOURS_COURTS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")

# --- Libellés, mot pour mot sur la page et dans l'export -------------------
TITRE_SECTION = "Pour les achats"
PHRASE_SENS = ("Pour décider quoi racheter, quoi mettre en avant et quoi "
               "retirer du catalogue.")
TITRE_TOUS_SORTIS = "Jeux à plusieurs boîtes, toutes dehors en même temps"
PHRASE_TOUS_SORTIS = ("Pendant ces durées, plus aucune boîte de ce jeu n'était "
                      "disponible au comptoir.")
ENTETES_TOUS_SORTIS = ("Jeu", "Boîtes", "Toutes dehors", "La plus longue fois")
TITRE_UNE_BOITE = "Jeux à une seule boîte, souvent prêtés"
PHRASE_UNE_BOITE = (f"Prêtés au moins {PRETS_MIN_UNE_BOITE} fois sur la "
                    "période. Pendant ces durées, la seule boîte du jeu était "
                    "prêtée.")
ENTETES_UNE_BOITE = ("Jeu", "Prêts", "Dehors", "La plus longue fois")
ENTETES_XLSX_TOUS_SORTIS = ("Jeu", "Référence", "Boîtes", "Prêts",
                            "Toutes dehors (durée)", "Toutes dehors (minutes)",
                            "La plus longue fois (durée)",
                            "La plus longue fois : début",
                            "La plus longue fois : fin")
NOTE_DEFINITIONS = (
    "Seules les heures d'ouverture comptent : chaque journée, de la première "
    "à la dernière heure de prêts ou de retours. Une boîte est dehors de son "
    "prêt à son retour, et au plus jusqu'à la fermeture du jour de son prêt : "
    "une boîte encore dehors le soir n'est pas comptée le lendemain. Un "
    "retour non scanné la laisse dehors jusqu'à ce qu'on s'en aperçoive, ce "
    "qui peut allonger la durée. Les sorties pour un tournoi ne comptent "
    "pas : le bureau choisit les jeux de tournoi. Le nombre de boîtes est "
    "celui d'aujourd'hui : un jeu dont une boîte a été ajoutée depuis peut "
    "ne pas apparaître."
)
PHRASE_AUCUN = ("Aucun jeu à plusieurs boîtes n'a eu toutes ses boîtes "
                "dehors en même temps sur la période.")
PHRASE_AUCUN_UNE_BOITE = (f"Aucun jeu à une seule boîte n'a été prêté "
                          f"{PRETS_MIN_UNE_BOITE} fois ou plus sur la période.")
TITRE_JAMAIS_SORTIS = "Les jeux jamais sortis"
FEUILLE_TOUS_SORTIS = "Tous sortis"
FEUILLE_JAMAIS_SORTIS = "Jamais sortis"
LIEN_EXCEL = "Télécharger l'Excel"
ENTETES_XLSX_JAMAIS_SORTIS = ("Jeu", "Référence", "Boîtes", "Catégories",
                              "Date d'achat", "Emplacement au local",
                              "Emplacement à l'événement",
                              "Sorti en tournoi seulement")
NOTE_JAMAIS_SORTIS = (
    "Aucun prêt au public sur la période. Un jeu sorti seulement pour un "
    "tournoi en fait partie : la dernière colonne le signale."
)


def phrase_seuil(nb: int) -> str:
    """La phrase sur les jeux écartés parce que sous `DUREE_MINIMALE_S`."""
    minutes = DUREE_MINIMALE_S // 60
    return (f"{nb} {pluriel(nb, 'autre jeu a eu toutes ses', 'autres jeux ont eu toutes leurs')} "
            f"boîtes dehors moins de {minutes} minutes en tout : "
            f"{pluriel(nb, 'il n’est pas montré', 'ils ne sont pas montrés')}.")


def phrase_peu_pretes(nb: int) -> str:
    """La phrase sur les jeux à une boîte écartés faute de prêts."""
    return (f"{nb} {pluriel(nb, 'autre jeu', 'autres jeux')} à une seule boîte "
            f"{pluriel(nb, 'a été prêté', 'ont été prêtés')} moins de "
            f"{PRETS_MIN_UNE_BOITE} fois : un long prêt ne dit pas qu'il en "
            f"faut une deuxième, {pluriel(nb, 'il n’est pas montré', 'ils ne sont pas montrés')}.")


def phrase_suite(nb: int) -> str:
    """La phrase quand un tableau de la page est coupé à `LIGNES_PAGE`."""
    return (f"Les {LIGNES_PAGE} premiers sont montrés ; les {nb} sont dans "
            f"l'export Excel, feuille « {FEUILLE_TOUS_SORTIS} ».")


def phrase_jamais_sortis(nb: int, nb_titres: int, periode_txt: str) -> str:
    """La ligne des jamais sortis sur la page : nombre, période nommée, feuille."""
    if nb_titres == 0:
        return "Le catalogue ne compte aucun jeu."
    if nb == 0:
        return ("Tous les jeux du catalogue sont sortis au moins une fois, "
                f"période : {periode_txt}.")
    return (f"{nb} {pluriel(nb, 'jeu n’est jamais sorti', 'jeux ne sont jamais sortis')} "
            f"au public, période : {periode_txt}. La liste, avec "
            "l'emplacement de chaque boîte, est dans l'export Excel, feuille "
            f"« {FEUILLE_JAMAIS_SORTIS} ».")


# ---------------------------------------------------------------------------
# Les heures d'ouverture, lues dans le comptoir
# ---------------------------------------------------------------------------
def plages_ouverture(donnees_comptoir: dict) -> list[tuple[datetime, datetime]]:
    """
    Les plages d'ouverture en UTC, une par journée du comptoir : du début de
    sa première heure active à la fin de sa dernière (`comptoir.heure_par_heure`,
    clés `jours` et `heures`). Triées, disjointes : deux journées sont
    séparées au moins par la nuit, ou se touchent à 6 h sans se chevaucher.
    """
    heures = donnees_comptoir["heures"]
    plages = []
    for j in donnees_comptoir["jours"]:
        premiere = heures[j["premier"]]["cle"]
        derniere = heures[j["premier"] + j["nb"] - 1]["cle"]
        plages.append((datetime.fromisoformat(premiere),
                       datetime.fromisoformat(derniere) + timedelta(hours=1)))
    return plages


# ---------------------------------------------------------------------------
# Le balayage
# ---------------------------------------------------------------------------
def _fusionner(intervalles: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Fusionne des intervalles qui se chevauchent OU se touchent (même seconde)."""
    out: list[list[datetime]] = []
    for d, f in sorted(intervalles):
        if out and d <= out[-1][1]:
            out[-1][1] = max(out[-1][1], f)
        else:
            out.append([d, f])
    return [(d, f) for d, f in out]


def _tous_dehors(par_boite: dict[str, list[tuple[datetime, datetime]]],
                 nb_boites: int) -> list[tuple[datetime, datetime]]:
    """
    Les intervalles où au moins `nb_boites` boîtes DISTINCTES sont dehors.

    Chaque boîte voit d'abord ses propres sorties fusionnées (un re-prêt
    ferme l'ancien prêt et rouvre le nouveau à la même seconde : une seule
    sortie, jamais deux boîtes). Puis on balaie : à un même instant, les
    sorties passent AVANT les retours, pour qu'un retour et un prêt à la même
    seconde ne laissent pas de trou.
    """
    if len(par_boite) < nb_boites:
        return []
    evenements = []
    for intervalles in par_boite.values():
        for d, f in _fusionner(intervalles):
            evenements.append((d, 0, 1))      # 0 : la sortie passe d'abord
            evenements.append((f, 1, -1))
    evenements.sort()
    out, dehors, debut = [], 0, None
    for instant, _, delta in evenements:
        dehors += delta
        if dehors >= nb_boites and debut is None:
            debut = instant
        elif dehors < nb_boites and debut is not None:
            if instant > debut:
                out.append((debut, instant))
            debut = None
    return out


def _couper(intervalles, plages) -> list[tuple[datetime, datetime]]:
    """L'intersection de deux listes triées d'intervalles disjoints : un
    morceau par plage traversée (une plage = une journée)."""
    out, i, j = [], 0, 0
    while i < len(intervalles) and j < len(plages):
        d = max(intervalles[i][0], plages[j][0])
        f = min(intervalles[i][1], plages[j][1])
        if d < f:
            out.append((d, f))
        if intervalles[i][1] < plages[j][1]:
            i += 1
        else:
            j += 1
    return out


def fin_de_journee(sortie: datetime) -> datetime:
    """La fin (UTC) de la journée de l'événement où commence `sortie` : le
    lendemain à `HEURE_DEBUT_JOURNEE` heures locales (`comptoir._journee`).
    Calculée en heure locale puis convertie : juste aussi la nuit d'un
    changement d'heure."""
    jour = _journee(_heure(sortie)) + timedelta(days=1)
    return datetime.combine(jour, time(HEURE_DEBUT_JOURNEE),
                            tzinfo=FUSEAU_LOCAL).astimezone(FUSEAU_UTC)


def _quand(dt: datetime, jour_de: datetime | None = None) -> str:
    """« sam. 15:05 » ; sans le jour si `jour_de` tombe le même jour local."""
    local = dt.astimezone(FUSEAU_LOCAL)
    if jour_de is not None and jour_de.astimezone(FUSEAU_LOCAL).date() == local.date():
        return f"{local:%H:%M}"
    return f"{JOURS_COURTS[local.weekday()]} {local:%H:%M}"


def pour_les_achats(conn: sqlite3.Connection, debut: str | None,
                    fin: str | None, donnees_comptoir: dict) -> dict:
    """
    Les chiffres de la section « Pour les achats » (définitions en tête).

    Args:
        conn: connexion à la base de prêt.
        debut, fin: bornes UTC ISO optionnelles (fin exclusive), celles de
            toutes les statistiques.
        donnees_comptoir: le résultat de `comptoir.heure_par_heure` pour la
            MÊME période (ses heures d'ouverture). Passé par l'appelant, qui
            l'a déjà calculé : on ne relit pas deux fois les prêts.

    Returns:
        dict {
          "tous_sortis": [{reference_titre, nom, nb_exemplaires, nb_prets, total_s,
                           total_txt, plus_long_s, plus_long_txt,
                           plus_long_debut, plus_long_fin (datetime UTC),
                           plus_long_quand ("sam. 15:05 → 19:10"),
                           plus_long_de, plus_long_a (ses deux moitiés)}],
              au-dessus de `DUREE_MINIMALE_S`, de la plus longue durée
              totale à la plus courte, puis par nom ;
          "ecartes_seuil": jeux tous sortis moins de `DUREE_MINIMALE_S`,
          "plusieurs_boites": les lignes de `tous_sortis` à plusieurs boîtes,
          "une_boite": celles à une boîte prêtées au moins
              `PRETS_MIN_UNE_BOITE` fois, même ordre,
          "ecartes_peu_pretes": jeux à une boîte écartés faute de prêts,
          "jamais_sortis": [{reference_titre, nom, nb_exemplaires,
                             tournoi_seulement}], par nom ;
          "nb_titres": titres qui ont au moins une boîte
        }
    """
    titres = conn.execute(
        """
        SELECT t.reference_titre, t.nom, COUNT(e.id_exemplaire) AS nb
        FROM titres t
        JOIN exemplaires e ON e.reference_titre = t.reference_titre
        GROUP BY t.reference_titre, t.nom
        """
    ).fetchall()
    lignes = conn.execute(
        """
        SELECT p.id_exemplaire, p.date_sortie, p.date_retour, p.motif,
               e.reference_titre
        FROM prets p
        JOIN exemplaires e ON e.id_exemplaire = p.id_exemplaire
        """
    ).fetchall()
    plages = plages_ouverture(donnees_comptoir)
    borne_debut = _lire(debut) if debut else None
    borne_fin = _lire(fin) if fin else None

    sorties: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    pretes_public: Counter = Counter()          # prêts au public de la période
    pretes_tournoi: set[str] = set()
    for l in lignes:
        motif, ref = l["motif"], l["reference_titre"]
        dans = ((debut is None or (l["date_sortie"] or "") >= debut)
                and (fin is None or (l["date_sortie"] or "") < fin))
        if motif in MOTIFS_COMPTES and dans:
            pretes_public[ref] += 1
        elif motif == "tournoi" and dans:
            pretes_tournoi.add(ref)
        if motif not in MOTIFS_COMPTES or not plages:
            continue
        sortie = _lire(l["date_sortie"])
        if sortie is None:
            continue
        # Au plus jusqu'à la fin de la journée du prêt (voir l'en-tête) ;
        # un prêt encore ouvert, jusque-là aussi.
        retour = fin_de_journee(sortie)
        if l["date_retour"] is not None:
            lu = _lire(l["date_retour"])
            if lu is None:
                continue                       # on ne sait pas quand
            retour = min(retour, lu)
        if borne_debut is not None:
            sortie = max(sortie, borne_debut)
        if borne_fin is not None:
            retour = min(retour, borne_fin)
        if retour > sortie:
            sorties[ref][l["id_exemplaire"]].append((sortie, retour))

    tous, ecartes = [], 0
    for t in titres:
        ref = t["reference_titre"]
        if ref not in sorties:
            continue
        morceaux = _couper(_tous_dehors(sorties[ref], t["nb"]), plages)
        total = sum((f - d).total_seconds() for d, f in morceaux)
        if total <= 0:
            continue
        if total < DUREE_MINIMALE_S:
            ecartes += 1
            continue
        # La plus longue : la première en cas d'égalité.
        d_long, f_long = max(morceaux, key=lambda m: (m[1] - m[0], -m[0].timestamp()))
        plus_long = (f_long - d_long).total_seconds()
        tous.append({
            "reference_titre": ref, "nom": t["nom"], "nb_exemplaires": t["nb"],
            "nb_prets": pretes_public[ref],
            "total_s": total, "total_txt": format_duree(total),
            "plus_long_s": plus_long, "plus_long_txt": format_duree(plus_long),
            "plus_long_debut": d_long, "plus_long_fin": f_long,
            "plus_long_quand": f"{_quand(d_long)} → {_quand(f_long, d_long)}",
            # Les deux moitiés, pour que la page passe à la ligne devant la
            # flèche, jamais au milieu d'une heure (375 px).
            "plus_long_de": _quand(d_long),
            "plus_long_a": f"→ {_quand(f_long, d_long)}",
        })
    # Le nom comparé comme un humain le lit, accents pliés (« Les Îles »
    # entre « Les Fourmis » et « Les Lanternes ») : le pliage des catégories.
    tous.sort(key=lambda r: (-r["total_s"], cle(r["nom"])))

    jamais_sortis = sorted(
        ({"reference_titre": t["reference_titre"], "nom": t["nom"],
          "nb_exemplaires": t["nb"],
          "tournoi_seulement": t["reference_titre"] in pretes_tournoi}
         for t in titres if t["reference_titre"] not in pretes_public),
        key=lambda r: (cle(r["nom"]), r["reference_titre"]))

    une_boite = [r for r in tous if r["nb_exemplaires"] == 1]
    return {"tous_sortis": tous, "ecartes_seuil": ecartes,
            "plusieurs_boites": [r for r in tous if r["nb_exemplaires"] > 1],
            "une_boite": [r for r in une_boite
                          if r["nb_prets"] >= PRETS_MIN_UNE_BOITE],
            "ecartes_peu_pretes": sum(r["nb_prets"] < PRETS_MIN_UNE_BOITE
                                      for r in une_boite),
            "jamais_sortis": jamais_sortis, "nb_titres": len(titres)}


# ---------------------------------------------------------------------------
# Les jamais sortis, pour l'export : retrouver la boîte
# ---------------------------------------------------------------------------
def detailler_jamais_sortis(conn: sqlite3.Connection,
                            jamais_sortis: list[dict]) -> list[dict]:
    """
    Les colonnes de la feuille « Jamais sortis » : de quoi retrouver la boîte
    sur l'étagère et décider de la garder.

    Les catégories, la date d'achat et les emplacements sont ceux de l'export
    du catalogue (`services.lignes_export_catalogue`) : mêmes libellés, mêmes
    formats, une seule requête à tenir. Cet export a une ligne par boîte ; on
    les regroupe par jeu, emplacements distincts joints par « / ».
    """
    if not jamais_sortis:
        return []
    refs = {r["reference_titre"] for r in jamais_sortis}
    boite_vers_ref = {
        row["id_exemplaire"]: row["reference_titre"]
        for row in conn.execute("SELECT id_exemplaire, reference_titre FROM exemplaires")
        if row["reference_titre"] in refs}
    _, catalogue = lignes_export_catalogue(conn)
    par_ref: dict[str, list[dict]] = defaultdict(list)
    for ligne in catalogue:
        ref = boite_vers_ref.get(ligne["Code jeu"])
        if ref is not None:
            par_ref[ref].append(ligne)

    def distincts(boites, cle):
        vus = []
        for b in boites:
            if b[cle] and b[cle] not in vus:
                vus.append(b[cle])
        return " / ".join(vus)

    out = []
    for r in jamais_sortis:
        boites = par_ref.get(r["reference_titre"], [])
        out.append({
            **r,
            "categories": boites[0]["Type jeu"] if boites else "",
            "date_achat": boites[0]["Date achat"] if boites else "",
            "emplacement_local": distincts(boites, "Emplacement local"),
            "emplacement_evenement": distincts(boites, "Emplacement événement"),
        })
    return out
