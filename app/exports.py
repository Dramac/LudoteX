"""
Génération des exports de statistiques en Excel (.xlsx) et PDF.

Les deux exports reçoivent le même dict `data` produit par
`services.collecter_stats` (les sections de la page : synthèse, comptoir,
achats, ce qui plaît, liste détaillée des prêts), et respectent donc le
filtre de période actif. Ils renvoient des octets prêts à être téléchargés
(voir routes/stats.py). Le PDF est le BILAN de l'édition (lot bilan-7b,
`construire_pdf`) ; ce qui relève de l'Organisation n'en sort que pour un
bénévole (`benevole`, voir « Ce qui ne sort que pour un bénévole »).

Dépendances : openpyxl (Excel) et reportlab (PDF, déjà utilisé pour les planches
d'étiquettes).
"""

from __future__ import annotations

import math
from io import BytesIO

# Nom de l'association : réglé en administration et lu en base (voir
# app/services.py, section « Identité de l'ASSOCIATION »). Cette fonction
# ouvre puis referme sa propre connexion et ne lève jamais — un export ne
# doit pas échouer parce que le nom n'a pas pu être lu.
#
# Couleur d'identité (lot 3c) : contrairement au nom ci-dessus, ELLE N'EST PAS
# LUE ICI. `construire_pdf` et `signalements_pdf` la reçoivent en PARAMÈTRE,
# lue et transmise par la ROUTE (app/routes/stats.py, app/routes/admin.py) —
# un choix de conception, pas une contrainte de base (les deux vivent dans la
# base de prêt, comme `nom_association`) : ça garde les fonctions d'export
# testables sans jamais toucher au réglage global. `couleur_texte_sur` et
# `nuances_theme` sont des calculs purs (aucun accès disque) : les importer
# ici n'ouvre rien. `COULEUR_ASSOCIATION_DEFAUT` est le seul domicile du
# littéral par défaut (voir app/services.py) — il n'est jamais recopié.
from app import achats, comptoir, gouts, organisation
from app.services import (COULEUR_ASSOCIATION_DEFAUT, DEF_DUREE_MOYENNE,
                          SORTIES_SANS_POCHETTE,
                          couleur_texte_sur, nom_association, nuances_theme)


def _libelle_metrique(metrique: str) -> str:
    return "par exemplaire" if metrique == "exemplaire" else "par total"


# ---------------------------------------------------------------------------
# Tombola (série tombola, lot 2) — un seul domicile pour les libellés des
# deux exports. Les données viennent de `tombola.statistiques`, lues et
# transmises par la ROUTE (comme la couleur) : None quand la section n'a pas
# lieu d'être (module masqué, aucun tirage dans la période).
# ---------------------------------------------------------------------------
def synthese_tombola(t: dict) -> list[tuple[str, object]]:
    """Les lignes de synthèse de la tombola, (libellé, valeur), mêmes pour les deux exports."""
    lignes = [
        ("Tirages", t["nb_tirages"]),
        ("Numéros tirés", t["tires"]),
        ("Lots remis", t["remis"]),
        ("Numéros encore affichés", t["affiches"]),
        ("Numéros non réclamés", t["non_reclames"]),
        ("Délai médian de remise (lots remis seulement)", t["delai_median"]),
        ("Erreurs de saisie (hors chiffres ci-dessus)", t["erreurs_saisie"]),
    ]
    if t.get("delais_ecartes"):
        lignes.append(("Délais écartés (horloge incohérente)", t["delais_ecartes"]))
    return lignes


ENTETES_TOMBOLA_TIRAGE = ["Tirage", "Mode", "Numéros", "Remis", "Délai médian"]
ENTETES_TOMBOLA_MODE = ["Mode", "Tirages", "Numéros", "Remis", "Délai médian"]
# « Session de tombola », et non « édition » (lot bilan-3) : une ÉDITION est
# désormais une période de l'événement datée par le bureau (app/editions.py).
# Une session de tombola (`tombola_editions`) s'ouvre au premier tirage et se
# ferme à la clôture : rien à voir. Même titre sur la page (stats.html, passé
# par la route) et dans les deux exports.
TITRE_TOMBOLA_SESSIONS = "Par session de tombola"
ENTETES_TOMBOLA_EDITION = ["Session ouverte le", "Close le", "Tirages", "Numéros",
                           "Remis", "Délai médian"]


def tableaux_tombola(t: dict) -> list[tuple[str, list[str], list[list]]]:
    """
    Les tableaux de la tombola, (titre, en-têtes, lignes) : par tirage, par
    mode, et par session de tombola seulement à partir de deux sessions dans
    la période.
    """
    tableaux = [
        ("Par tirage", ENTETES_TOMBOLA_TIRAGE,
         [[x["quand"], x["mode"], x["tires"], x["remis"], x["delai_median"]]
          for x in t["par_tirage"]]),
        ("Par mode de tirage", ENTETES_TOMBOLA_MODE,
         [[x["mode"], x["nb_tirages"], x["tires"], x["remis"], x["delai_median"]]
          for x in t["par_mode"]]),
    ]
    if len(t["par_edition"]) > 1:
        tableaux.append((TITRE_TOMBOLA_SESSIONS, ENTETES_TOMBOLA_EDITION,
                         [[x["ouverte"], x["close"] or "en cours", x["nb_tirages"],
                           x["tires"], x["remis"], x["delai_median"]]
                          for x in t["par_edition"]]))
    return tableaux


# ---------------------------------------------------------------------------
# Neutralisation des formules (SEC-06) — SEUL domicile
# ---------------------------------------------------------------------------
# Un tableur interprète comme une formule une cellule qui commence par l'un de
# ces caractères. Le danger n'est pas théorique : le détail d'un signalement
# est tapé au comptoir, le nom d'un bénévole dans le questionnaire public du
# planning — ce ne sont plus des données que l'admin a fournies lui-même.
#
# La neutralisation se fait À L'ÉCRITURE, et différemment selon le format :
# - Excel (.xlsx) : la cellule est TYPÉE texte, et marquée « préfixe
#   apostrophe » (`quotePrefix`) pour qu'Excel ne la réinterprète pas si on la
#   retape. La valeur stockée reste exacte : aucune apostrophe n'apparaît.
#   C'est le seul format où c'est possible.
# - CSV : un CSV n'a pas de type, la seule défense est l'apostrophe en tête
#   (recommandation OWASP). L'import du catalogue la retire
#   (`scripts/import_csv.py::lire_csv`, via `retirer_neutralisation_csv`) :
#   l'aller-retour export → import du bureau rend la valeur d'origine.
DEBUTS_FORMULE = ("=", "+", "-", "@", "\t", "\r")


def neutraliser_csv(valeur):
    """Valeur d'une case CSV, précédée d'une apostrophe si elle ouvrirait une formule."""
    if isinstance(valeur, str) and valeur.startswith(DEBUTS_FORMULE):
        return "'" + valeur
    return valeur


def retirer_neutralisation_csv(valeur: str) -> str:
    """
    Inverse exact de `neutraliser_csv`, pour l'import.

    N'ôte l'apostrophe QUE devant un début de formule : une valeur qui commence
    légitimement par une apostrophe suivie d'autre chose est rendue intacte.
    """
    if (isinstance(valeur, str) and valeur.startswith("'")
            and valeur[1:].startswith(DEBUTS_FORMULE)):
        return valeur[1:]
    return valeur


def ecrire_cellule(ws, ligne: int, colonne: int, valeur):
    """
    Écrit `valeur` dans une feuille openpyxl sans jamais produire de formule.

    openpyxl prend toute chaîne commençant par « = » pour une formule : on
    force donc le type texte de TOUTE chaîne. Renvoie la cellule (pour la mise
    en forme par l'appelant). Les nombres restent des nombres.
    """
    cellule = ws.cell(row=ligne, column=colonne, value=valeur)
    if isinstance(valeur, str):
        cellule.data_type = "s"
        if valeur.startswith(DEBUTS_FORMULE):
            cellule.quotePrefix = True
    return cellule


# ---------------------------------------------------------------------------
# Tableaux simples — CSV et Excel (catalogue, carnet de maintenance…)
# ---------------------------------------------------------------------------
def catalogue_csv(entetes: list[str], lignes: list[dict]) -> bytes:
    """
    Sérialise le catalogue en CSV (séparateur « ; », encodage UTF-8 avec BOM).

    Le BOM (`utf-8-sig`) fait qu'Excel ouvre correctement les accents ; le « ; »
    correspond au format des exports de l'association et est reconnu par l'import.
    Chaque valeur passe par `neutraliser_csv` (SEC-06).
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=entetes, delimiter=";",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows({cle: neutraliser_csv(val) for cle, val in ligne.items()}
                     for ligne in lignes)
    return buf.getvalue().encode("utf-8-sig")


def tableau_xlsx(entetes: list[str], lignes: list[dict],
                 titre_feuille: str = "Feuille") -> bytes:
    """
    Sérialise un tableau simple (en-têtes en gras + lignes) en classeur Excel
    d'une seule feuille. `lignes` est une liste de dicts dont les clés sont
    les en-têtes ; une clé absente donne une cellule vide.

    ANCIENNEMENT `catalogue_xlsx`. Renommée au lot 3 du carnet de maintenance
    (2026-08-10), quand elle a pris son second appelant : la fonction était
    déjà entièrement générique, seul son NOM disait l'appelant plutôt que ce
    qu'elle fait — et le titre de feuille, codé en dur, aurait intitulé
    « Catalogue » le classeur des signalements, sous les yeux du bureau.
    Deux appelants aujourd'hui, tous deux dans routes/admin.py.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = titre_feuille
    for col, entete in enumerate(entetes, start=1):
        cell = ws.cell(row=1, column=col, value=entete)
        cell.font = Font(bold=True)
    for i, ligne in enumerate(lignes, start=2):
        for col, entete in enumerate(entetes, start=1):
            ecrire_cellule(ws, i, col, ligne.get(entete, ""))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Le comptoir heure par heure (lot bilan-4) : la feuille porte les mêmes
# lignes et les mêmes colonnes que le tableau de la page (`app/comptoir.py`),
# pour la même période — TOUTES les heures, même quand la page renonce à
# dessiner une période trop longue. Le bilan PDF, lui, dessine le graphique
# des prêts et des retours (`graphique_flux`), pas le tableau des heures.
FEUILLE_HEURE_PAR_HEURE = "Heure par heure"


def feuille_heure_par_heure(wb, donnees: dict, periode_txt: str, gras):
    """Ajoute la feuille « Heure par heure » au classeur et la renvoie."""
    from app import comptoir

    wh = wb.create_sheet(FEUILLE_HEURE_PAR_HEURE)
    wh["A1"] = comptoir.TITRE_SECTION
    wh["A1"].font = gras
    wh["A2"] = f"Période : {periode_txt}"
    if not donnees["heures"]:
        wh["A4"] = comptoir.PHRASE_VIDE
        return wh
    ligne = 4
    if donnees["fermetures_cloture"]:
        wh[f"A{ligne}"] = comptoir.phrase_fermetures(donnees["fermetures_cloture"])
        ligne += 2
    for col, entete in enumerate(comptoir.ENTETES_TABLEAU, start=1):
        wh.cell(row=ligne, column=col, value=entete).font = gras
    for h in donnees["heures"]:
        ligne += 1
        wh.cell(row=ligne, column=1, value=h["libelle_table"])
        wh.cell(row=ligne, column=2, value=h["prets"])
        wh.cell(row=ligne, column=3, value=h["retours"])
        wh.cell(row=ligne, column=4, value=h["dehors"])
    return wh


# Pour les achats (lot bilan-5, `app/achats.py`). La feuille du palmarès
# s'appelait « Palmarès » et portait les plus PUIS les moins prêtés ; elle ne
# garde que les plus prêtés, d'où son nom. Les deux autres feuilles portent
# les listes complètes de la section : la page n'en montre que le début.
FEUILLE_PALMARES = "Plus prêtés"
ENTETES_PALMARES = ("Rang", "Jeu", "Prêts", "Exemplaires", "Par exempl.")


def _en_tete_feuille(feuille, titre: str, periode_txt: str, gras) -> None:
    feuille["A1"] = titre
    feuille["A1"].font = gras
    feuille["A2"] = f"Période : {periode_txt}"


def _date_heure(dt) -> str:
    from app.services import FUSEAU_LOCAL

    return dt.astimezone(FUSEAU_LOCAL).strftime("%d/%m/%Y %H:%M")


def feuille_tous_sortis(wb, achats_donnees: dict, periode_txt: str, gras):
    """Ajoute la feuille « Tous sortis » : les deux listes de la page, jeux à
    plusieurs boîtes puis jeux à une boîte souvent prêtés, TOUTES leurs
    lignes, sans la coupe aux premières, avec le début et la fin de la plus
    longue fois."""
    from app import achats

    wt = wb.create_sheet(achats.FEUILLE_TOUS_SORTIS)
    _en_tete_feuille(wt, achats.TITRE_SECTION, periode_txt, gras)
    ligne = 4
    for titre, phrase, vide, lignes in (
            (achats.TITRE_TOUS_SORTIS, achats.PHRASE_TOUS_SORTIS,
             achats.PHRASE_AUCUN, achats_donnees["plusieurs_boites"]),
            (achats.TITRE_UNE_BOITE, achats.PHRASE_UNE_BOITE,
             achats.PHRASE_AUCUN_UNE_BOITE, achats_donnees["une_boite"])):
        wt.cell(row=ligne, column=1, value=titre).font = gras
        ligne += 1
        if not lignes:
            wt[f"A{ligne}"] = vide
            ligne += 2
            continue
        wt[f"A{ligne}"] = phrase
        ligne += 1
        for col, entete in enumerate(achats.ENTETES_XLSX_TOUS_SORTIS, start=1):
            wt.cell(row=ligne, column=col, value=entete).font = gras
        for r in lignes:
            ligne += 1
            ecrire_cellule(wt, ligne, 1, r["nom"])
            ecrire_cellule(wt, ligne, 2, r["reference_titre"])
            for col, valeur in enumerate(
                    (r["nb_exemplaires"], r["nb_prets"], r["total_txt"],
                     round(r["total_s"] / 60), r["plus_long_txt"],
                     _date_heure(r["plus_long_debut"]),
                     _date_heure(r["plus_long_fin"])), start=3):
                wt.cell(row=ligne, column=col, value=valeur)
        ligne += 2
    for phrase in ([achats.phrase_peu_pretes(achats_donnees["ecartes_peu_pretes"])]
                   if achats_donnees["ecartes_peu_pretes"] else []) + (
                   [achats.phrase_seuil(achats_donnees["ecartes_seuil"])]
                   if achats_donnees["ecartes_seuil"] else []) + [achats.NOTE_DEFINITIONS]:
        wt[f"A{ligne}"] = phrase
        ligne += 1
    return wt


def feuille_jamais_sortis(wb, achats_donnees: dict, periode_txt: str, gras):
    """Ajoute la feuille « Jamais sortis » : de quoi retrouver la boîte sur
    l'étagère (`achats.detailler_jamais_sortis`, lu par la route) ; à défaut
    de ce détail, les colonnes du catalogue restent vides."""
    from app import achats

    wj = wb.create_sheet(achats.FEUILLE_JAMAIS_SORTIS)
    _en_tete_feuille(wj, achats.TITRE_JAMAIS_SORTIS, periode_txt, gras)
    lignes = achats_donnees.get("jamais_detail", achats_donnees["jamais_sortis"])
    ligne = 4
    if not lignes:
        wj[f"A{ligne}"] = achats.phrase_jamais_sortis(
            0, achats_donnees["nb_titres"], periode_txt)
        return wj
    wj[f"A{ligne}"] = achats.NOTE_JAMAIS_SORTIS
    ligne += 2
    for col, entete in enumerate(achats.ENTETES_XLSX_JAMAIS_SORTIS, start=1):
        wj.cell(row=ligne, column=col, value=entete).font = gras
    for r in lignes:
        ligne += 1
        for col, valeur in enumerate(
                (r["nom"], r["reference_titre"], r["nb_exemplaires"],
                 r.get("categories", ""), r.get("date_achat", ""),
                 r.get("emplacement_local", ""), r.get("emplacement_evenement", ""),
                 *("oui" if r[f"{m}_seulement"] else ""
                   for m in SORTIES_SANS_POCHETTE)), start=1):
            ecrire_cellule(wj, ligne, col, valeur)
    return wj


# Ce qui plaît (lot bilan-6, `app/gouts.py`). La feuille porte les trois
# répartitions EN ENTIER (toutes les catégories, petites comprises, sans le
# regroupement « Autres catégories » de la page) et le tableau des durées ;
# la seconde, la liste de travail des jeux sans catégorie.
def feuille_gouts(wb, gouts_donnees: dict, periode_txt: str, gras):
    """Ajoute la feuille « Ce qui plaît » au classeur et la renvoie."""
    from app import gouts

    wg = wb.create_sheet(gouts.FEUILLE_GOUTS)
    _en_tete_feuille(wg, gouts.TITRE_SECTION, periode_txt, gras)
    ligne = 4
    if not gouts_donnees["nb_titres"]:
        wg[f"A{ligne}"] = gouts.PHRASE_CATALOGUE_VIDE
        return wg
    wg[f"A{ligne}"] = gouts.NOTE_PRETS_PAR_JEU
    ligne += 2

    def repartition(titre, premier_entete, lignes, inconnus, notes):
        nonlocal ligne
        wg.cell(row=ligne, column=1, value=titre).font = gras
        ligne += 1
        for col, entete in enumerate((premier_entete, *gouts.ENTETES_XLSX_REPARTITION),
                                     start=1):
            wg.cell(row=ligne, column=col, value=entete).font = gras
        for l in lignes + ([inconnus] if inconnus else []):
            ligne += 1
            ecrire_cellule(wg, ligne, 1, l["nom"])
            wg.cell(row=ligne, column=2, value=l["nb_titres"])
            wg.cell(row=ligne, column=3, value=l["nb_prets"])
            wg.cell(row=ligne, column=4, value=(round(l["par_titre"], 1)
                                               if l["par_titre"] is not None else None))
            ecrire_cellule(wg, ligne, 5, "oui" if l["petit"] else None)
        ligne += 1
        for note in notes:
            wg[f"A{ligne}"] = note
            ligne += 1
        ligne += 1

    c = gouts_donnees["categories"]
    repartition(gouts.TITRE_CATEGORIES, gouts.ENTETES_CATEGORIES[0], c["toutes"],
                c["non_renseigne"],
                [gouts.NOTE_PLUSIEURS_CATEGORIES] + (
                    [gouts.phrase_non_renseigne(c["non_renseigne"]["nb_prets"],
                                                gouts_donnees["total_prets"])]
                    if c["non_renseigne"] else []))
    repartition(gouts.TITRE_JOUEURS, gouts.ENTETES_JOUEURS[0],
                gouts_donnees["joueurs"]["lignes"],
                gouts_donnees["joueurs"]["non_renseigne"], [gouts.NOTE_JOUEURS])
    repartition(gouts.TITRE_AGE, gouts.ENTETES_AGE[0], gouts_donnees["age"]["lignes"],
                gouts_donnees["age"]["non_renseigne"], [])

    d = gouts_donnees["durees"]
    wg.cell(row=ligne, column=1, value=gouts.TITRE_DUREES).font = gras
    ligne += 1
    for col, entete in enumerate(gouts.ENTETES_XLSX_DUREES, start=1):
        wg.cell(row=ligne, column=col, value=entete).font = gras
    for l in d["lignes"] + [x for x in (d["non_renseigne"], d["ensemble"]) if x]:
        ligne += 1
        ecrire_cellule(wg, ligne, 1, l["nom"])
        for col, valeur in enumerate(
                (l["nb_prets"], l["nb_mesures"], l["annoncee_txt"],
                 round(l["annoncee_s"] / 60) if l["annoncee_s"] is not None else None,
                 l["reelle_txt"],
                 round(l["reelle_s"] / 60) if l["reelle_s"] is not None else None),
                start=2):
            wg.cell(row=ligne, column=col, value=valeur)
    ligne += 1
    non_mesures = gouts.phrase_non_mesures(d["oublis"], d["nuit"], d["en_cours"],
                                           d["illisibles"], d["clotures"])
    for note in [gouts.NOTE_DUREES] + ([non_mesures] if non_mesures else []) + [
            gouts.NOTE_CATALOGUE]:
        wg[f"A{ligne}"] = note
        ligne += 1
    return wg


def feuille_sans_categorie(wb, gouts_donnees: dict, periode_txt: str, gras):
    """Ajoute la feuille « Sans catégorie » : la liste de travail pour
    compléter le catalogue, les jeux les plus prêtés d'abord."""
    from app import gouts

    ws = wb.create_sheet(gouts.FEUILLE_SANS_CATEGORIE)
    _en_tete_feuille(ws, gouts.FEUILLE_SANS_CATEGORIE, periode_txt, gras)
    ligne = 4
    lignes = gouts_donnees["sans_categorie"]
    if not lignes:
        ws[f"A{ligne}"] = (gouts.PHRASE_CATALOGUE_VIDE if not gouts_donnees["nb_titres"]
                           else "Tous les jeux du catalogue ont au moins une catégorie.")
        return ws
    ws[f"A{ligne}"] = gouts.NOTE_SANS_CATEGORIE
    ligne += 2
    for col, entete in enumerate(gouts.ENTETES_XLSX_SANS_CATEGORIE, start=1):
        ws.cell(row=ligne, column=col, value=entete).font = gras
    for r in lignes:
        ligne += 1
        ecrire_cellule(ws, ligne, 1, r["nom"])
        ecrire_cellule(ws, ligne, 2, r["reference_titre"])
        ws.cell(row=ligne, column=3, value=r["nb_exemplaires"])
        ws.cell(row=ligne, column=4, value=r["nb_prets"])
    return ws


# ---------------------------------------------------------------------------
# Ce qui ne sort que pour un bénévole (lot bilan-7b)
# ---------------------------------------------------------------------------
# LA RÈGLE DE VISIBILITÉ DES EXPORTS, la même que celle de la page (lot
# bilan-7a, en tête d'app/routes/stats.py) : la section « Organisation »,
# la tombola, les tournois et le programme, et la liste détaillée des prêts
# ne sortent que pour un bénévole, même quand le bureau a rouvert `/stats`
# au public. C'est la ROUTE qui décide (`auth.peut_ecrire`) et le dit par
# `benevole=True` ; par défaut, ni le classeur ni le PDF ne les portent,
# même si leurs données sont passées et même si la section est demandée.

# Les libellés des quatre chiffres de tête, mot pour mot ceux de la page
# (stats.html, synthèse à quatre cases du lot bilan-7a).
LIBELLE_PRETS = "prêts"
LIBELLE_JEUX_PRETES = "jeux différents prêtés"
LIBELLE_DUREE_MOYENNE = "durée moyenne"

# --- Le classeur Excel -----------------------------------------------------
FEUILLE_SYNTHESE = "Synthèse"
FEUILLE_ORGANISATION = "Organisation"
FEUILLE_TOMBOLA = "Tombola"
FEUILLE_DETAIL = "Détail"
# L'ORDRE DES FEUILLES, celui de la page : la tête, le comptoir, les achats,
# ce qui plaît, puis ce qui est réservé aux bénévoles. Une feuille absente
# (section sans données, visiteur) est sautée, l'ordre des autres ne bouge
# pas.
FEUILLES_XLSX = (FEUILLE_SYNTHESE, FEUILLE_HEURE_PAR_HEURE, FEUILLE_PALMARES,
                 achats.FEUILLE_TOUS_SORTIS, achats.FEUILLE_JAMAIS_SORTIS,
                 gouts.FEUILLE_GOUTS, gouts.FEUILLE_SANS_CATEGORIE,
                 FEUILLE_ORGANISATION, FEUILLE_TOMBOLA, FEUILLE_DETAIL)
# Les feuilles réservées aux bénévoles.
FEUILLES_RESERVEES = (FEUILLE_ORGANISATION, FEUILLE_TOMBOLA, FEUILLE_DETAIL)


def lignes_synthese(g: dict, pic: dict | None) -> list[tuple[str, object]]:
    """Les lignes de la synthèse, (libellé, valeur), mêmes pour les deux
    exports et les mêmes valeurs que les quatre cases de la page. Plus rien
    de l'Organisation : erreurs, retours non scannés et prêts encore ouverts
    ont leur section, réservée aux bénévoles (lot bilan-7b)."""
    return [
        ("Prêts au total", g["total_prets"]),
        ("Jeux différents prêtés", g["titres_pretes"]),
        ("Jeux au catalogue", g["nb_titres"]),
        (f"Jeux dehors au pic{', ' + pic['libelle'] if pic else ''}",
         pic["dehors"] if pic else "—"),
        ("Durée moyenne de prêt", g.get("duree_moyenne", "—")),
    ]


def lignes_organisation(g: dict) -> list[tuple[str, int]]:
    """Les trois chiffres de l'Organisation, (libellé, valeur), ceux de la
    page (`g`), mêmes pour les deux exports."""
    return [
        ("Erreurs de prêt", g.get("erreurs", 0)),
        ("Retours non scannés", g.get("oublis", 0)),
        ("Prêts encore ouverts", g.get("en_cours", 0)),
    ]


def lignes_tournois(tp: dict | None, sorties: dict | None) -> list[str]:
    """Les phrases du bloc « Tournois et programme » qui suivent le tableau
    des états, mêmes pour les deux exports et pour la page. `sorties` : les
    sorties sans pochette par motif (`organisation.sorties_sans_pochette`),
    ou None quand elles ne sont pas montrées."""
    if not tp:
        return []
    phrases = [organisation.PHRASE_BASE_ILLISIBLE] if tp["illisible"] else []
    tr = tp["tournois"]
    if tr:
        if not tr["total"]:
            phrases.append(organisation.PHRASE_AUCUN_TOURNOI)
        inscriptions = organisation.phrase_inscriptions(tr["inscrits"], tr["equipes"])
        if tr["total"] and inscriptions:
            phrases.append(inscriptions)
        if tr["sans_date"]:
            phrases.append(organisation.phrase_tournois_sans_date(tr["sans_date"]))
    if sorties is not None:
        phrases += [*organisation.phrases_sorties_sans_pochette(sorties),
                    organisation.NOTE_SORTIES_TOURNOI]
    if tp["programme"]:
        phrases.append(organisation.phrase_programme(tp["programme"]["publies"]))
        if tp["programme"]["sans_date"]:
            phrases.append(organisation.phrase_programme_sans_date(
                tp["programme"]["sans_date"]))
    return phrases


def feuille_organisation(wb, g: dict, org: dict, periode_txt: str, gras):
    """Ajoute la feuille « Organisation » : des COMPTES seulement, jamais le
    texte d'un signalement, le code d'une boîte ni un pseudo."""
    wo = wb.create_sheet(FEUILLE_ORGANISATION)
    _en_tete_feuille(wo, organisation.TITRE_SECTION, periode_txt, gras)
    ligne = 4
    for lib, val in lignes_organisation(g):
        wo.cell(row=ligne, column=1, value=lib)
        wo.cell(row=ligne, column=2, value=val)
        ligne += 1
    for phrase in (organisation.DEF_ERREURS, organisation.DEF_OUBLIS,
                   organisation.phrase_ouverts(g.get("en_cours", 0),
                                               org.get("periode_finie", False))):
        wo[f"A{ligne}"] = phrase
        ligne += 1

    ligne += 1
    wo.cell(row=ligne, column=1, value=organisation.TITRE_SIGNALEMENTS).font = gras
    ligne += 1
    sig = org.get("signalements") or {"total": 0, "lignes": []}
    if sig["total"]:
        wo[f"A{ligne}"] = organisation.phrase_signalements(
            sig["total"], sig["traites"], sig["a_traiter"])
        ligne += 1
        for col, entete in enumerate(organisation.ENTETES_SIGNALEMENTS, start=1):
            wo.cell(row=ligne, column=col, value=entete).font = gras
        for l in sig["lignes"]:
            ligne += 1
            ecrire_cellule(wo, ligne, 1, l["nom"])
            for col, valeur in enumerate((l["total"], l["traites"], l["a_traiter"]), start=2):
                wo.cell(row=ligne, column=col, value=valeur)
        ligne += 1
        wo[f"A{ligne}"] = organisation.NOTE_SIGNALEMENTS
    else:
        wo[f"A{ligne}"] = organisation.PHRASE_AUCUN_SIGNALEMENT
    ligne += 1

    tp = org.get("tournois_programme")
    if tp:
        ligne += 1
        wo.cell(row=ligne, column=1, value=organisation.TITRE_TOURNOIS).font = gras
        ligne += 1
        tr = tp["tournois"]
        if tr and tr["total"]:
            for col, entete in enumerate(organisation.ENTETES_TOURNOIS, start=1):
                wo.cell(row=ligne, column=col, value=entete).font = gras
            for etat, libelle in organisation.LIBELLES_ETATS_TOURNOI.items():
                ligne += 1
                wo.cell(row=ligne, column=1, value=libelle)
                wo.cell(row=ligne, column=2, value=tr["par_etat"][etat])
            ligne += 1
            wo.cell(row=ligne, column=1, value="Total").font = gras
            wo.cell(row=ligne, column=2, value=tr["total"])
            ligne += 1
        for phrase in lignes_tournois(tp, org.get("sorties_sans_pochette")):
            wo[f"A{ligne}"] = phrase
            ligne += 1
    return wo


def construire_xlsx(data: dict, periode_txt: str, tombola: dict | None = None,
                    organisation_donnees: dict | None = None,
                    benevole: bool = False) -> bytes:
    """
    Construit le classeur Excel des statistiques, ses feuilles dans l'ordre
    de `FEUILLES_XLSX` : Synthèse, Heure par heure, Plus prêtés, Tous sortis,
    Jamais sortis, Ce qui plaît, Sans catégorie, puis, pour un bénévole
    seulement (`benevole`, voir la règle plus haut), Organisation, Tombola
    (quand `tombola` est fourni) et Détail.

    La feuille « Détail » ne porte PAS de colonne « numéro de pochette » : ce
    détail couvre une période, donc essentiellement des prêts clos, dont le
    numéro est effacé à la clôture (fiche D5). La colonne serait vide.

    Args:
        data: dict de services.collecter_stats.
        periode_txt: libellé lisible de la période (ou « toutes périodes »).
        tombola: statistiques de la tombola, lues et transmises par la route.
        organisation_donnees: la section « Organisation » de la route
            (`routes.stats._organisation`) ; sans elle, la feuille ne porte
            que les trois chiffres de `data["globales"]`.
        benevole: True seulement quand la route l'a vérifié.

    Returns:
        Le contenu binaire du fichier .xlsx.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    gras = Font(bold=True)
    wb = Workbook()
    largeurs_feuilles = []

    # --- Synthèse : les quatre chiffres de tête ---
    ws = wb.active
    ws.title = FEUILLE_SYNTHESE
    g = data["globales"]
    ws["A1"] = f"Statistiques de prêt — {nom_association()}"
    ws["A1"].font = gras
    ws["A2"] = f"Période : {periode_txt}"
    lignes = lignes_synthese(g, (data.get("comptoir") or {}).get("pic"))
    for i, (lib, val) in enumerate(lignes, start=4):
        ws[f"A{i}"] = lib
        ws[f"B{i}"] = val
    ws[f"A{4 + len(lignes) + 1}"] = DEF_DUREE_MOYENNE
    largeurs_feuilles.append((ws, [34, 14]))

    # --- Heure par heure (lot bilan-4). Absente d'un `data` construit à la
    # main sans la clé. ---
    if "comptoir" in data:
        largeurs_feuilles.append((feuille_heure_par_heure(
            wb, data["comptoir"], periode_txt, gras), [30, 10, 10, 30]))

    # --- Les plus prêtés (lot bilan-5 : jusqu'à 50, avec le rang, ex aequo
    # compris ; les jamais sortis ont leur feuille) ---
    wp = wb.create_sheet(FEUILLE_PALMARES)
    wp["A1"] = f"Les plus prêtés ({_libelle_metrique(data['metrique'])})"
    wp["A1"].font = gras
    wp["A2"] = f"Période : {periode_txt}"
    ligne = 4
    for col, entete in enumerate(ENTETES_PALMARES, start=1):
        wp.cell(row=ligne, column=col, value=entete).font = gras
    for jeu in data["plus"]:
        ligne += 1
        wp.cell(row=ligne, column=1, value=jeu.get("rang"))
        ecrire_cellule(wp, ligne, 2, jeu["nom"])
        wp.cell(row=ligne, column=3, value=jeu["nb_prets"])
        wp.cell(row=ligne, column=4, value=jeu["nb_exemplaires"])
        wp.cell(row=ligne, column=5, value=round(jeu["par_exemplaire"], 2))
    largeurs_feuilles.append((wp, [8, 40, 10, 12, 12]))

    # --- « Tous sortis » et « Jamais sortis » (lot bilan-5), « Ce qui plaît »
    # et « Sans catégorie » (lot bilan-6). Absentes d'un `data` construit sans
    # la clé. ---
    if "achats" in data:
        largeurs_feuilles += [
            (feuille_tous_sortis(wb, data["achats"], periode_txt, gras),
             [36, 14, 8, 8, 16, 14, 18, 22, 22]),
            (feuille_jamais_sortis(wb, data["achats"], periode_txt, gras),
             [36, 14, 8, 24, 14, 24, 24, 14]),
        ]
    if "gouts" in data:
        largeurs_feuilles += [
            (feuille_gouts(wb, data["gouts"], periode_txt, gras),
             [30, 12, 14, 22, 22, 22, 22, 22]),
            (feuille_sans_categorie(wb, data["gouts"], periode_txt, gras),
             [40, 16, 10, 10]),
        ]

    if benevole:
        # --- Organisation (lot bilan-7b) : des comptes seulement ---
        largeurs_feuilles.append((feuille_organisation(
            wb, g, organisation_donnees or {}, periode_txt, gras), [44, 12, 12, 12]))

        # --- Tombola (synthèse puis tableaux), si elle a lieu ---
        if tombola:
            wt = wb.create_sheet(FEUILLE_TOMBOLA)
            wt["A1"] = "Tombola"
            wt["A1"].font = gras
            wt["A2"] = f"Période : {periode_txt}"
            ligne = 4
            for lib, val in synthese_tombola(tombola):
                ecrire_cellule(wt, ligne, 1, lib)
                ecrire_cellule(wt, ligne, 2, val)
                ligne += 1
            for titre_tableau, entetes, lignes_tableau in tableaux_tombola(tombola):
                ligne += 1
                wt.cell(row=ligne, column=1, value=titre_tableau).font = gras
                ligne += 1
                for col, entete in enumerate(entetes, start=1):
                    wt.cell(row=ligne, column=col, value=entete).font = gras
                ligne += 1
                for valeurs in lignes_tableau:
                    for col, val in enumerate(valeurs, start=1):
                        ecrire_cellule(wt, ligne, col, val)
                    ligne += 1
            largeurs_feuilles.append((wt, [44, 18, 12, 12, 12, 14]))

        # --- Détail des prêts ---
        wd = wb.create_sheet(FEUILLE_DETAIL)
        for col, entete in enumerate(["Jeu", "Exemplaire", "Sortie", "Retour", "Durée"],
                                     start=1):
            wd.cell(row=1, column=col, value=entete).font = gras
        for i, p in enumerate(data["prets"], start=2):
            # Nom et code viennent du catalogue importé : texte forcé (SEC-06).
            ecrire_cellule(wd, i, 1, p["nom"])
            ecrire_cellule(wd, i, 2, p["id_exemplaire"])
            wd.cell(row=i, column=3, value=p["sortie_locale"])
            wd.cell(row=i, column=4, value=p["retour_local"] or "en cours")
            wd.cell(row=i, column=5, value=p["duree_txt"])
        largeurs_feuilles.append((wd, [40, 12, 18, 18, 12, 14]))

    # Largeurs de colonnes lisibles.
    for feuille, largeurs in largeurs_feuilles:
        for idx, larg in enumerate(largeurs):
            feuille.column_dimensions[chr(65 + idx)].width = larg

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Le bilan PDF (lot bilan-7b)
# ---------------------------------------------------------------------------
# Les sections du PDF, dans l'ordre de la page. Le palmarès des moins prêtés
# a disparu (lot bilan-5 : les jamais sortis le remplacent) ; les plus prêtés
# sont dans « Pour les achats ». Les trois dernières sont réservées aux
# bénévoles (`SECTIONS_RESERVEES`, règle plus haut) ; « organisation » porte
# aussi les tournois et le programme.
SECTIONS_PDF = ("synthese", "comptoir", "achats", "gouts", "organisation",
                "tombola", "detail")
SECTIONS_RESERVEES = ("organisation", "tombola", "detail")
# Le bilan par défaut : tout, sauf la liste détaillée, qui peut courir sur
# des dizaines de pages. C'est aussi ce que donne un formulaire « à la
# carte » envoyé sans aucune case cochée.
SECTIONS_PDF_DEFAUT = tuple(s for s in SECTIONS_PDF if s != "detail")
# Le libellé de chaque case du volet « PDF à la carte » : le titre de la
# section sur la page.
TITRES_SECTIONS_PDF = {
    "synthese": "Synthèse",
    "comptoir": comptoir.TITRE_SECTION,
    "achats": achats.TITRE_SECTION,
    "gouts": gouts.TITRE_SECTION,
    "organisation": organisation.TITRE_SECTION,
    "tombola": organisation.TITRE_TOMBOLA,
    "detail": organisation.TITRE_DETAIL,
}

# La section de la page qui porte les exports (stats.html, `id="bilan"`).
TITRE_SECTION_BILAN = "Bilan et exports"
PHRASE_BILAN = ("Le bilan reprend les sections de cette page, pour la période "
                "affichée : un document à joindre à un compte rendu ou à une "
                "demande de subvention.")
NOTE_BILAN_ORGANISATION = ("Téléchargé depuis cet appareil, il porte aussi "
                           "la section « Organisation », réservée aux bénévoles.")
LIBELLE_TOUT_EXCEL = "Tout en Excel"
LIBELLE_A_LA_CARTE = "PDF à la carte"
TITRE_BILAN_EDITION = "Bilan de l'édition"
TITRE_BILAN = "Bilan"
# Le palmarès des plus prêtés, dans le PDF : les 15 premiers (la page en
# montre 10 et en replie 40, l'Excel en donne 50).
LIGNES_PALMARES_PDF = 15

# Le graphique du comptoir : sa hauteur en points, quelle que soit sa
# largeur (la géométrie de la page est mise à l'échelle en largeur pour
# tenir dans la page, et en hauteur par ce rapport), et la taille de son
# texte, jamais sous 8 pt.
ECHELLE_HAUTEUR_GRAPHIQUE = 0.8
TAILLE_TEXTE_GRAPHIQUE = 8
# Un bloc (sous-titre, phrase, tableau) dont le tableau a plus de lignes que
# cela peut se couper d'une page à l'autre ; en deçà, il reste d'un tenant.
LIGNES_BLOC_INSECABLE = 8
# Une étiquette d'heure sur deux (ou sur trois…) quand les heures sont trop
# serrées pour que « 22 h » tienne sous chacune.
ECART_MIN_ETIQUETTES = 20
ECART_HACHURES = 3


def titre_bilan(nom_edition: str | None, periode_txt: str) -> str:
    """Le titre du bilan : « Bilan de l'édition — <nom> » pour une édition,
    « Bilan — <période> » sinon. Il dit la période (décision du lot)."""
    if nom_edition:
        return f"{TITRE_BILAN_EDITION} — {nom_edition}"
    return f"{TITRE_BILAN} — {periode_txt}"


def _hachures(x: float, y: float, l: float, h: float, pas: float):
    """Les segments à 45° qui hachurent le rectangle (x, y, l, h), coupés à
    ses bords : ReportLab n'a pas de motif de remplissage."""
    segments = []
    c = -h
    while c < l:
        # La droite y' = y + (x' - x - c), pour x' de x + c à x + c + h.
        x0, y0 = x + c, y
        x1, y1 = x + c + h, y + h
        if x0 < x:
            y0 += x - x0
            x0 = x
        if x1 > x + l:
            y1 -= x1 - (x + l)
            x1 = x + l
        if x1 > x0:
            segments.append((x0, y0, x1, y1))
        c += pas
    return segments


def graphique_flux(geo: dict, largeur_dispo: float, couleur_trait: str,
                   couleur_grille: str):
    """
    Le graphique « Prêts et retours, heure par heure » en dessin ReportLab, à
    partir de la géométrie de la page (`comptoir.geometrie(...)["flux"]`) :
    mêmes barres, mêmes nombres, mêmes graduations, mises à l'échelle. Rien
    n'y est recalculé.

    Imprimable en noir et blanc, comme la page : les prêts en barres PLEINES,
    les retours en barres à CONTOUR hachuré, d'une seule couleur (la nuance
    « lisible » du thème, qui atteint 4,5:1 sur le blanc) ; un seul axe, qui
    part de 0. La légende est un texte, sous le titre.
    """
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    trait = colors.HexColor(couleur_trait)
    grille = colors.HexColor(couleur_grille)
    sx = min(1.0, largeur_dispo / geo["largeur"])
    sy = ECHELLE_HAUTEUR_GRAPHIQUE
    haut = geo["hauteur"]
    dessin = Drawing(geo["largeur"] * sx, haut * sy)

    def y(v):                     # le SVG descend, le PDF monte
        return (haut - v) * sy

    for g in geo["graduations"]:
        dessin.add(Line(geo["gauche"] * sx, y(g["y"]), geo["droite"] * sx, y(g["y"]),
                        strokeColor=grille, strokeWidth=0.4))
        dessin.add(String(geo["gauche"] * sx - 4, y(g["y"]) - 3, str(g["valeur"]),
                          fontSize=TAILLE_TEXTE_GRAPHIQUE, fontName="Helvetica",
                          textAnchor="end"))
    for s in geo["separations"]:
        dessin.add(Line(s * sx, y(geo["haut_trace"]), s * sx, y(geo["base"]),
                        strokeColor=grille, strokeWidth=0.6))
    for b in geo["barres"]:
        if b["hauteur"] <= 0:
            continue
        bx, by, bl, bh = b["x"] * sx, y(b["y"] + b["hauteur"]), b["largeur"] * sx, b["hauteur"] * sy
        if b["serie"] == "prets":
            dessin.add(Rect(bx, by, bl, bh, fillColor=trait, strokeColor=None))
        else:
            dessin.add(Rect(bx, by, bl, bh, fillColor=None, strokeColor=trait,
                            strokeWidth=0.6))
            for x0, y0, x1, y1 in _hachures(bx, by, bl, bh, ECART_HACHURES):
                dessin.add(Line(x0, y0, x1, y1, strokeColor=trait, strokeWidth=0.5))
    dessin.add(Line(geo["gauche"] * sx, y(geo["base"]), geo["droite"] * sx, y(geo["base"]),
                    strokeColor=trait, strokeWidth=0.8))
    for e in geo["etiquettes"]:
        dessin.add(String(e["x"] * sx, y(e["y"]), e["texte"],
                          fontSize=TAILLE_TEXTE_GRAPHIQUE, fontName="Helvetica",
                          textAnchor="middle"))
    pas = max(1, math.ceil(ECART_MIN_ETIQUETTES / (comptoir.PAS_HEURE * sx)))
    for i, h in enumerate(geo["heures"]):
        if i % pas == 0:
            dessin.add(String(h["x"] * sx, y(geo["base"]) - 11, h["texte"],
                              fontSize=TAILLE_TEXTE_GRAPHIQUE, fontName="Helvetica",
                          textAnchor="middle"))
    for j in geo["jours"]:
        dessin.add(String(j["x"] * sx, y(geo["base"]) - 23, j["texte"],
                          fontSize=TAILLE_TEXTE_GRAPHIQUE, fontName="Helvetica-Bold"))
    return dessin


def _legende_flux(couleur_trait: str):
    """Les deux pastilles de la légende, chacune suivie de son nom en texte."""
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    trait = colors.HexColor(couleur_trait)
    dessin = Drawing(320, 14)
    dessin.add(Rect(0, 2, 14, 10, fillColor=trait, strokeColor=None))
    dessin.add(String(19, 3, f"{comptoir.SERIE_PRETS} (plein)",
                      fontSize=TAILLE_TEXTE_GRAPHIQUE + 1, fontName="Helvetica"))
    dessin.add(Rect(110, 2, 14, 10, fillColor=None, strokeColor=trait, strokeWidth=0.6))
    for x0, y0, x1, y1 in _hachures(110, 2, 14, 10, ECART_HACHURES):
        dessin.add(Line(x0, y0, x1, y1, strokeColor=trait, strokeWidth=0.5))
    dessin.add(String(129, 3, f"{comptoir.SERIE_RETOURS} (hachuré)",
                      fontSize=TAILLE_TEXTE_GRAPHIQUE + 1, fontName="Helvetica"))
    return dessin


def construire_pdf(data: dict, periode_txt: str,
                   sections: "set[str] | None" = None,
                   couleur: str = COULEUR_ASSOCIATION_DEFAUT,
                   tombola: dict | None = None,
                   organisation_donnees: dict | None = None,
                   benevole: bool = False,
                   titre: str | None = None) -> bytes:
    """
    Construit le bilan PDF (lot bilan-7b) : les sections de la page, dans son
    ordre, avec les mêmes chiffres pour la même période.

    1. En-tête : titre (il dit l'édition ou la période), association, période,
       date d'établissement du document.
    2. Synthèse : les quatre chiffres de tête.
    3. Le comptoir : le pic, les deux plus fortes heures de retours, le
       graphique des prêts et des retours (`graphique_flux`) ; au-delà de
       `comptoir.MAX_JOURS` journées, la phrase de la page.
    4. Pour les achats : les deux listes (`achats.LIGNES_PDF` premières
       lignes), les `LIGNES_PALMARES_PDF` plus prêtés, le NOMBRE de jamais
       sortis et le renvoi à l'Excel.
    5. Ce qui plaît : catégories, joueurs, âge, durées, sans les barres (le
       texte porte la valeur).
    6. Organisation, 7. Tombola, 8. Détail des prêts : pour un bénévole
       seulement (`benevole`, règle plus haut). Jamais le texte d'un
       signalement, jamais un pseudo, jamais un numéro de pochette.

    Une section commence là où finit la précédente : un saut de page n'est
    posé que si son titre resterait seul en bas de page.

    Args:
        data: dict de services.collecter_stats. Une clé absente (comptoir,
            achats, gouts) fait sauter sa section, jamais une exception.
        periode_txt: libellé lisible de la période.
        sections: parmi `SECTIONS_PDF` ; None ou vide = `SECTIONS_PDF_DEFAUT`.
        couleur: couleur d'identité (`#rrggbb`), lue et transmise par la
            route — voir la note en tête de ce fichier. Défaut explicite.
        tombola: statistiques de la tombola, lues et transmises par la route.
        organisation_donnees: la section « Organisation » de la route.
        benevole: True seulement quand la route l'a vérifié.
        titre: le titre du document (`titre_bilan`) ; défaut : la période.

    Returns:
        Le contenu binaire du fichier .pdf.
    """
    from datetime import datetime
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (CondPageBreak, KeepTogether, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    from app.services import FUSEAU_LOCAL

    sections = set(sections or SECTIONS_PDF_DEFAUT) & set(SECTIONS_PDF)
    if not benevole:
        sections -= set(SECTIONS_RESERVEES)
    if not sections:
        sections = set(SECTIONS_PDF_DEFAUT) - set(SECTIONS_RESERVEES)
    titre = titre or titre_bilan(None, periode_txt)

    styles = getSampleStyleSheet()
    normal = ParagraphStyle("corps", parent=styles["Normal"], spaceAfter=4)
    note = ParagraphStyle("note", parent=styles["Normal"], fontSize=8.5, leading=11,
                          spaceBefore=2, spaceAfter=2)
    cellule = ParagraphStyle("cellule", parent=styles["Normal"], fontSize=8, leading=9.5)
    # `TEXTCOLOR` d'un TableStyle ne s'applique pas au contenu d'un Paragraph :
    # l'en-tête a donc son propre style, dans la couleur qui se lit sur la
    # couleur d'identité (voir `signalements_pdf`).
    couleur_texte = couleur_texte_sur(couleur)
    nuances = nuances_theme(couleur)
    couleur_fond = nuances["fond"]
    entete = ParagraphStyle("entete", parent=cellule, fontName="Helvetica-Bold",
                            textColor=colors.HexColor(couleur_texte))
    chiffre = ParagraphStyle("chiffre", parent=styles["Normal"], fontName="Helvetica-Bold",
                             fontSize=16, leading=19)

    buf = BytesIO()
    marge = 2 * cm
    doc = SimpleDocTemplate(buf, pagesize=A4, title=titre, author=nom_association(),
                            subject="Statistiques de prêt",
                            leftMargin=marge, rightMargin=marge,
                            topMargin=1.5 * cm, bottomMargin=1.6 * cm)
    largeur = A4[0] - 2 * marge
    elements = []

    def p(texte, style=normal):
        return Paragraph(escape(str(texte)), style)

    titre2 = ParagraphStyle("titre2", parent=styles["Heading2"], keepWithNext=1)
    titre3 = ParagraphStyle("titre3", parent=styles["Heading3"], keepWithNext=1)

    def h2(texte):
        # Un titre de section ne reste jamais seul en bas de page : il suit sa
        # phrase de sens (`keepWithNext`), et ne commence pas dans les
        # derniers centimètres.
        elements.append(CondPageBreak(4 * cm))
        elements.append(p(texte, titre2))

    def h3(texte):
        return p(texte, titre3)

    def tableau(entetes, lignes, fractions, hors_classement=0, largeur_totale=None):
        """Un tableau : en-tête à la couleur d'identité, lignes alternées.
        Toutes les cellules sont des Paragraph : un nom de jeu très long
        passe à la ligne au lieu de déborder. Les `hors_classement` dernières
        lignes (« Autres », « Non renseigné », « Toutes durées ») sont
        séparées par un trait."""
        donnees = [[p(e, entete) for e in entetes]]
        donnees += [[c if isinstance(c, Paragraph) else p(c, cellule) for c in l]
                    for l in lignes]
        t = Table(donnees, colWidths=[(largeur_totale or largeur) * f for f in fractions],
                  repeatRows=1)
        commandes = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(couleur)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(couleur_texte)),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor(couleur_fond)]),
        ]
        if hors_classement and len(lignes) > hors_classement:
            commandes.append(("LINEABOVE", (0, -hors_classement), (-1, -hors_classement),
                              1, colors.HexColor(nuances["lisible"])))
        t.setStyle(TableStyle(commandes))
        t.hAlign = "LEFT"
        return t

    def bloc(*morceaux):
        """Un sous-titre, sa phrase et son tableau. Un petit bloc reste d'un
        seul tenant ; un grand tableau peut se couper (son en-tête se répète)
        plutôt que de laisser un bas de page vide, mais ne commence pas dans
        les derniers centimètres : jamais un sous-titre seul en bas de page."""
        morceaux = [m for m in morceaux if m is not None]
        grand = any(isinstance(m, Table) and len(m._cellvalues) > LIGNES_BLOC_INSECABLE
                    for m in morceaux)
        if grand:
            elements.append(CondPageBreak(5 * cm))
            elements.extend(morceaux)
        else:
            elements.append(KeepTogether(morceaux))

    # --- 1. En-tête ---
    elements.append(p(titre, styles["Title"]))
    elements.append(p(nom_association(), normal))
    elements.append(p(f"Période : {periode_txt}", normal))
    elements.append(p("Document établi le "
                      f"{datetime.now(FUSEAU_LOCAL):%d/%m/%Y à %H:%M}.", note))
    elements.append(Spacer(1, 0.3 * cm))

    g = data["globales"]
    donnees_comptoir = data.get("comptoir") or {}

    # --- 2. Synthèse : les quatre cases de la page ---
    if "synthese" in sections:
        h2("Synthèse")
        pic = donnees_comptoir.get("pic")
        cases = [
            (g["total_prets"], LIBELLE_PRETS),
            (f"{g['titres_pretes']} / {g['nb_titres']}", LIBELLE_JEUX_PRETES),
            (pic["dehors"] if pic else "—",
             comptoir.LIBELLE_PIC + (f", {pic['libelle']}" if pic else "")),
            (g.get("duree_moyenne", "—"), LIBELLE_DUREE_MOYENNE),
        ]
        t = Table([[[p(v, chiffre), p(l, cellule)] for v, l in cases]],
                  colWidths=[largeur / 4] * 4)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(couleur_fond)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(t)
        elements.append(p(DEF_DUREE_MOYENNE, note))

    # --- 3. Le comptoir heure par heure ---
    if "comptoir" in sections and donnees_comptoir:
        h2(comptoir.TITRE_SECTION)
        elements.append(p(comptoir.PHRASE_SENS))
        if not donnees_comptoir["heures"]:
            elements.append(p(comptoir.PHRASE_VIDE))
        elif donnees_comptoir["trop_long"]:
            elements.append(p(comptoir.phrase_trop_long(donnees_comptoir["nb_jours"])))
        else:
            phrase = comptoir.phrase_pic_et_pointes(donnees_comptoir)
            geo = comptoir.geometrie(donnees_comptoir)
            bloc(p(phrase) if phrase else None,
                 h3(comptoir.TITRE_FLUX),
                 _legende_flux(nuances["lisible"]),
                 Spacer(1, 0.15 * cm),
                 graphique_flux(geo["flux"], largeur, nuances["lisible"],
                                nuances["clair"]))
        if donnees_comptoir["heures"]:
            elements.append(p(comptoir.NOTE_DEFINITIONS, note))
            if donnees_comptoir["fermetures_cloture"]:
                elements.append(p(comptoir.phrase_fermetures(
                    donnees_comptoir["fermetures_cloture"]), note))

    # --- 4. Pour les achats ---
    if "achats" in sections and "achats" in data:
        a = data["achats"]
        h2(achats.TITRE_SECTION)
        elements.append(p(achats.PHRASE_SENS))
        for titre_liste, phrase, vide, entetes, cle, lignes in (
                (achats.TITRE_TOUS_SORTIS, achats.PHRASE_TOUS_SORTIS, achats.PHRASE_AUCUN,
                 achats.ENTETES_TOUS_SORTIS, "nb_exemplaires", a["plusieurs_boites"]),
                (achats.TITRE_UNE_BOITE, achats.PHRASE_UNE_BOITE,
                 achats.PHRASE_AUCUN_UNE_BOITE, achats.ENTETES_UNE_BOITE, "nb_prets",
                 a["une_boite"])):
            if not lignes:
                bloc(h3(titre_liste), p(vide))
                continue
            bloc(h3(titre_liste), p(phrase),
                 tableau(entetes,
                         [[r["nom"], r[cle], r["total_txt"],
                           f"{r['plus_long_txt']} ({r['plus_long_quand']})"]
                          for r in lignes[:achats.LIGNES_PDF]],
                         [0.42, 0.1, 0.16, 0.32]),
                 p(achats.phrase_suite(len(lignes), achats.LIGNES_PDF), note)
                 if len(lignes) > achats.LIGNES_PDF else None)
        if a["ecartes_peu_pretes"]:
            elements.append(p(achats.phrase_peu_pretes(a["ecartes_peu_pretes"]), note))
        if a["ecartes_seuil"]:
            elements.append(p(achats.phrase_seuil(a["ecartes_seuil"]), note))
        elements.append(p(achats.NOTE_DEFINITIONS, note))

        plus = data["plus"]
        titre_plus = h3(f"Les plus prêtés ({_libelle_metrique(data['metrique'])})")
        if plus:
            bloc(titre_plus,
                 tableau(list(ENTETES_PALMARES),
                         [[j.get("rang", ""), j["nom"], j["nb_prets"], j["nb_exemplaires"],
                           f"{j['par_exemplaire']:.2f}".replace(".", ",")]
                          for j in plus[:LIGNES_PALMARES_PDF]],
                         [0.08, 0.5, 0.1, 0.16, 0.16]),
                 p(f"Les {LIGNES_PALMARES_PDF} premiers sont montrés ; les "
                   f"{len(plus)} sont dans l'export Excel, feuille "
                   f"« {FEUILLE_PALMARES} ».", note)
                 if len(plus) > LIGNES_PALMARES_PDF else None)
        else:
            bloc(titre_plus, p("Aucun prêt sur la période."))
        bloc(h3(achats.TITRE_JAMAIS_SORTIS),
             p(achats.phrase_jamais_sortis(len(a["jamais_sortis"]), a["nb_titres"],
                                           periode_txt)))

    # --- 5. Ce qui plaît : les tableaux, sans les barres ---
    if "gouts" in sections and "gouts" in data:
        gd = data["gouts"]
        h2(gouts.TITRE_SECTION)
        elements.append(p(gouts.PHRASE_SENS))
        if not gd["nb_titres"]:
            elements.append(p(gouts.PHRASE_CATALOGUE_VIDE))
        elif not gd["total_prets"]:
            elements.append(p(gouts.PHRASE_AUCUN_PRET))
        else:
            elements.append(p(gouts.NOTE_PRETS_PAR_JEU, note))

            def repartition(entetes, lignes, hors, largeur_totale=None,
                            fractions=(0.5, 0.15, 0.15, 0.2)):
                hors = [l for l in hors if l]
                return tableau(entetes, [[l["nom"], l["nb_titres"], l["nb_prets"],
                                          l["par_titre_txt"]] for l in lignes + hors],
                               list(fractions), hors_classement=len(hors),
                               largeur_totale=largeur_totale)

            c = gd["categories"]
            if c["aucune"]:
                bloc(h3(gouts.TITRE_CATEGORIES),
                     p(gouts.PHRASE_AUCUNE_CATEGORIE))
            else:
                bloc(h3(gouts.TITRE_CATEGORIES),
                     repartition(gouts.ENTETES_CATEGORIES, c["lignes"],
                                 [c["autres"], c["non_renseigne"]]),
                     p(gouts.NOTE_PLUSIEURS_CATEGORIES, note),
                     p(gouts.phrase_autres(c["nb_suite"], c["nb_petites"]), note)
                     if c["autres"] else None)
            if c["non_renseigne"]:
                elements.append(p(gouts.phrase_non_renseigne(
                    c["non_renseigne"]["nb_prets"], gd["total_prets"]), note))
            # Joueurs et âge côte à côte : deux petits tableaux de même forme.
            demi = (largeur - 0.6 * cm) / 2
            fractions = (0.46, 0.16, 0.16, 0.22)
            cote_a_cote = Table([[
                [h3(gouts.TITRE_JOUEURS),
                 repartition(gouts.ENTETES_JOUEURS, gd["joueurs"]["lignes"],
                             [gd["joueurs"]["non_renseigne"]], demi, fractions)],
                [h3(gouts.TITRE_AGE),
                 repartition(gouts.ENTETES_AGE, gd["age"]["lignes"],
                             [gd["age"]["non_renseigne"]], demi, fractions)],
            ]], colWidths=[demi + 0.3 * cm, demi + 0.3 * cm])
            cote_a_cote.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 0.6 * cm), ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ]))
            bloc(cote_a_cote, p(gouts.NOTE_JOUEURS, note))
            d = gd["durees"]
            if d["nb_mesures"]:
                hors = [l for l in (d["non_renseigne"], d["ensemble"]) if l]
                bloc(h3(gouts.TITRE_DUREES),
                     tableau(gouts.ENTETES_DUREES,
                             [[l["nom"], l["nb_mesures"], l["annoncee_txt"], l["reelle_txt"]]
                              for l in d["lignes"] + hors],
                             [0.34, 0.22, 0.22, 0.22], hors_classement=len(hors)))
            else:
                bloc(h3(gouts.TITRE_DUREES),
                     p(gouts.PHRASE_AUCUNE_MESURE))
            elements.append(p(gouts.NOTE_DUREES, note))
            non_mesures = gouts.phrase_non_mesures(d["oublis"], d["nuit"], d["en_cours"],
                                                   d["illisibles"], d["clotures"])
            if non_mesures:
                elements.append(p(non_mesures, note))
            elements.append(p(gouts.NOTE_CATALOGUE, note))

    # --- 6. Organisation (bénévoles seulement) ---
    if "organisation" in sections:
        org = organisation_donnees or {}
        h2(organisation.TITRE_SECTION)
        elements.append(p(organisation.PHRASE_SENS))
        bloc(tableau(["Indicateur", "Valeur"],
                     [[lib, val] for lib, val in lignes_organisation(g)], [0.6, 0.2]))
        for phrase in (organisation.DEF_ERREURS, organisation.DEF_OUBLIS):
            elements.append(p(phrase, note))
        elements.append(p(organisation.phrase_ouverts(g.get("en_cours", 0),
                                                      org.get("periode_finie", False))))
        sig = org.get("signalements")
        if sig is not None:
            if sig["total"]:
                bloc(h3(organisation.TITRE_SIGNALEMENTS),
                     p(organisation.phrase_signalements(sig["total"], sig["traites"],
                                                        sig["a_traiter"])),
                     tableau(organisation.ENTETES_SIGNALEMENTS,
                             [[l["nom"], l["total"], l["traites"], l["a_traiter"]]
                              for l in sig["lignes"]], [0.46, 0.18, 0.18, 0.18]),
                     p(organisation.NOTE_SIGNALEMENTS, note))
            else:
                bloc(h3(organisation.TITRE_SIGNALEMENTS),
                     p(organisation.PHRASE_AUCUN_SIGNALEMENT))
        tp = org.get("tournois_programme")
        if tp:
            tr = tp["tournois"]
            bloc(h3(organisation.TITRE_TOURNOIS),
                 tableau(organisation.ENTETES_TOURNOIS,
                         [[libelle, tr["par_etat"][etat]] for etat, libelle
                          in organisation.LIBELLES_ETATS_TOURNOI.items()]
                         + [["Total", tr["total"]]], [0.46, 0.18], hors_classement=1)
                 if tr and tr["total"] else None,
                 *[p(phrase) for phrase in lignes_tournois(tp, org.get("sorties_sans_pochette"))])

    # --- 7. Tombola (bénévoles seulement, et si elle a eu lieu) ---
    if "tombola" in sections and tombola:
        h2(organisation.TITRE_TOMBOLA)
        bloc(tableau(["Indicateur", "Valeur"],
                     [[lib, val] for lib, val in synthese_tombola(tombola)], [0.6, 0.2]))
        for titre_tableau, entetes, lignes_tableau in tableaux_tombola(tombola):
            bloc(h3(titre_tableau),
                 tableau(entetes, lignes_tableau, [1 / len(entetes)] * len(entetes)))

    # --- 8. Détail des prêts (bénévoles seulement) ---
    if "detail" in sections:
        h2(f"{organisation.TITRE_DETAIL} ({len(data['prets'])})")
        if data["prets"]:
            elements.append(tableau(
                ["Jeu", "Ex.", "Sortie", "Retour", "Durée"],
                [[x["nom"], x["id_exemplaire"], x["sortie_locale"],
                  x["retour_local"] or "en cours", x["duree_txt"]] for x in data["prets"]],
                [0.38, 0.13, 0.19, 0.19, 0.11]))
        else:
            elements.append(p("Aucun prêt sur la période."))

    def pied(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(marge, 0.9 * cm, titre)
        canvas.drawRightString(A4[0] - marge, 0.9 * cm, f"page {document.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=pied, onLaterPages=pied)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Carnet de maintenance — PDF (docs/conception-signalements.md §7)
#
# Fonction PROPRE, et pas un paramètre de plus sur `construire_pdf` : cette
# dernière est spécifique aux statistiques (période, sections cochables,
# palmarès), et l'étendre reviendrait à y faire cohabiter deux documents qui
# n'ont ni la même source ni le même lecteur. Seul le style de tableau est
# commun, une quinzaine de lignes recopiées assumées.
#
# Ce document ne sort QUE de /admin/signalements, derrière le mot de passe :
# il nomme des boîtes abîmées et peut porter du texte libre (§7).
# ---------------------------------------------------------------------------
def signalements_pdf(lignes: list[dict], filtre_txt: str,
                     couleur: str = COULEUR_ASSOCIATION_DEFAUT) -> bytes:
    """
    Construit la liste imprimable des signalements — celle qu'on emporte au
    local pour réparer.

    Args:
        lignes: signalements tels que ramenés par `services.lister_signalements`.
        filtre_txt: libellé lisible du filtre actif (« à traiter », …).
        couleur: couleur d'identité de l'association (`#rrggbb`), lue et
            transmise par la route. Défaut explicite (l'anthracite) : un appel
            qui l'oublie produit un PDF cohérent, jamais une exception.

    Returns:
        Le contenu binaire du fichier .pdf.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    styles = getSampleStyleSheet()
    # Le détail libre et le nom du jeu doivent pouvoir passer à la ligne dans
    # leur cellule : sans Paragraph, reportlab déborde de la colonne.
    cellule = ParagraphStyle("cellule", parent=styles["Normal"], fontSize=8, leading=10)
    # Deux couleurs suffisent (voir construire_pdf ci-dessus et docs/
    # ui-composants.md § 18) : le fond d'en-tête et le fond des lignes
    # alternées. `TEXTCOLOR` d'un TableStyle ne s'applique PAS au contenu d'un
    # Paragraph : sans ce style dédié, les en-têtes resteraient noirs sur le
    # fond de la couleur d'identité.
    couleur_texte = couleur_texte_sur(couleur)
    couleur_fond = nuances_theme(couleur)["fond"]
    entete = ParagraphStyle("entete", parent=cellule,
                            textColor=colors.HexColor(couleur_texte),
                            fontName="Helvetica-Bold")

    buf = BytesIO()
    # Paysage : sept colonnes, dont deux de texte libre.
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            title="Carnet de maintenance",
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm,
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm)
    elements = [
        Paragraph(f"Carnet de maintenance — {nom_association()}", styles["Title"]),
        Paragraph(f"Signalements : {filtre_txt}", styles["Normal"]),
        Spacer(1, 0.4 * cm),
    ]

    if not lignes:
        elements.append(Paragraph("Aucun signalement.", styles["Normal"]))
        doc.build(elements)
        return buf.getvalue()

    entetes = ["Jeu", "Boîte", "Catégorie", "Détail", "Signalé le", "Salle", "Local"]
    donnees = [[Paragraph(e, entete) for e in entetes]]
    for l in lignes:
        donnees.append([
            Paragraph(l.get("jeu_nom") or "", cellule),
            Paragraph(str(l.get("id_exemplaire") or ""), cellule),
            Paragraph(l.get("categorie_nom") or "", cellule),
            Paragraph(l.get("texte") or "", cellule),
            Paragraph(l.get("cree_local") or "", cellule),
            Paragraph(l.get("emplacement_evenement") or "", cellule),
            Paragraph(l.get("emplacement_local_nom") or "", cellule),
        ])

    table = Table(
        donnees, repeatRows=1,
        colWidths=[6 * cm, 2 * cm, 3.5 * cm, 8 * cm, 3 * cm, 2.6 * cm, 2.6 * cm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(couleur)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(couleur_texte)),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(couleur_fond)]),
    ]))
    elements.append(table)
    doc.build(elements)
    return buf.getvalue()
