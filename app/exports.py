"""
Génération des exports de statistiques en Excel (.xlsx) et PDF.

Les deux exports reçoivent le même dict `data` produit par
`services.collecter_stats` (synthèse + palmarès + liste détaillée des prêts), et
respectent donc le filtre de période actif. Ils renvoient des octets prêts à
être téléchargés (voir routes/stats.py).

Dépendances : openpyxl (Excel) et reportlab (PDF, déjà utilisé pour les planches
d'étiquettes).
"""

from __future__ import annotations

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
from app.services import (COULEUR_ASSOCIATION_DEFAUT, LIMITE_PALMARES_PDF,
                          couleur_texte_sur, nom_association, nuances_theme)

# Prêts clos par un re-prêt ou un transfert sans que leur retour ait été
# scanné (motif `oubli`, voir app/services.py::MOTIF_OUBLI) : comptés dans les
# prêts, pas dans la durée moyenne. Même libellé dans les deux exports.
LIBELLE_OUBLIS = "Retours non scannés (dans les prêts, hors durée moyenne)"


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
# dessiner une période trop longue. Le PDF ne la reprend pas.
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
                 "oui" if r["tournoi_seulement"] else ""), start=1):
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


def construire_xlsx(data: dict, periode_txt: str, tombola: dict | None = None) -> bytes:
    """
    Construit un classeur Excel à huit feuilles : Synthèse, Plus prêtés, Tous
    sortis, Jamais sortis, Ce qui plaît, Sans catégorie, Heure par heure,
    Détail — et une neuvième, « Tombola », quand `tombola` est fourni (voir
    `synthese_tombola`).

    La feuille « Détail » ne porte PAS de colonne « numéro de pochette » : ce
    détail couvre une période, donc essentiellement des prêts clos, dont le
    numéro est effacé à la clôture (fiche D5). La colonne serait vide.

    Args:
        data: dict de services.collecter_stats.
        periode_txt: libellé lisible de la période (ou « toutes périodes »).

    Returns:
        Le contenu binaire du fichier .xlsx.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    gras = Font(bold=True)
    wb = Workbook()

    # --- Feuille 1 : Synthèse ---
    ws = wb.active
    ws.title = "Synthèse"
    g = data["globales"]
    ws["A1"] = f"Statistiques de prêt — {nom_association()}"
    ws["A1"].font = gras
    ws["A2"] = f"Période : {periode_txt}"
    lignes = [
        ("Prêts au total", g["total_prets"]),
        ("Prêts en cours", g["en_cours"]),
        ("Titres prêtés", g["titres_pretes"]),
        ("Titres au catalogue", g["nb_titres"]),
        ("Durée moyenne de prêt", g.get("duree_moyenne", "—")),
        (LIBELLE_OUBLIS, g.get("oublis", 0)),
        ("Erreurs de prêt (hors chiffres ci-dessus)", g.get("erreurs", 0)),
    ]
    for i, (lib, val) in enumerate(lignes, start=4):
        ws[f"A{i}"] = lib
        ws[f"B{i}"] = val

    # --- Feuille 2 : les plus prêtés (lot bilan-5 : jusqu'à 50, avec le
    # rang, ex aequo compris ; les moins prêtés ont quitté l'Excel, les
    # jamais sortis ont leur feuille) ---
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

    # --- Feuilles « Tous sortis » et « Jamais sortis » (lot bilan-5). Absentes
    # d'un `data` construit à la main sans la clé, comme le comptoir. ---
    feuilles_achats = []
    if "achats" in data:
        feuilles_achats = [
            (feuille_tous_sortis(wb, data["achats"], periode_txt, gras),
             [36, 14, 8, 8, 16, 14, 18, 22, 22]),
            (feuille_jamais_sortis(wb, data["achats"], periode_txt, gras),
             [36, 14, 8, 24, 14, 24, 24, 14]),
        ]
    # --- Feuilles « Ce qui plaît » et « Sans catégorie » (lot bilan-6), après
    # celles des achats. Absentes d'un `data` construit sans la clé. ---
    if "gouts" in data:
        feuilles_achats += [
            (feuille_gouts(wb, data["gouts"], periode_txt, gras),
             [30, 12, 14, 22, 22, 22, 22, 22]),
            (feuille_sans_categorie(wb, data["gouts"], periode_txt, gras),
             [40, 16, 10, 10]),
        ]

    # --- Feuille 3 : Heure par heure (lot bilan-4). Absente d'un `data`
    # construit à la main sans la clé, comme les `g.get(...)` ci-dessus. ---
    wh = (feuille_heure_par_heure(wb, data["comptoir"], periode_txt, gras)
          if "comptoir" in data else None)

    # --- Feuille 4 : Détail des prêts ---
    wd = wb.create_sheet("Détail")
    entetes_detail = ["Jeu", "Exemplaire", "Sortie", "Retour", "Durée"]
    for col, entete in enumerate(entetes_detail):
        c = wd.cell(row=1, column=1 + col, value=entete)
        c.font = gras
    for i, p in enumerate(data["prets"], start=2):
        # Nom et code viennent du catalogue importé : texte forcé (SEC-06).
        ecrire_cellule(wd, i, 1, p["nom"])
        ecrire_cellule(wd, i, 2, p["id_exemplaire"])
        wd.cell(row=i, column=3, value=p["sortie_locale"])
        wd.cell(row=i, column=4, value=p["retour_local"] or "en cours")
        wd.cell(row=i, column=5, value=p["duree_txt"])

    largeurs_feuilles = [(ws, [24, 14]), (wp, [8, 40, 10, 12, 12]),
                         *feuilles_achats, (wd, [40, 12, 18, 18, 12, 14])]
    if wh is not None:
        largeurs_feuilles.append((wh, [30, 10, 10, 30]))

    # --- Feuille 5 : Tombola (synthèse puis tableaux), si elle a lieu ---
    if tombola:
        wt = wb.create_sheet("Tombola")
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

    # Largeurs de colonnes lisibles.
    for feuille, largeurs in largeurs_feuilles:
        for idx, larg in enumerate(largeurs):
            feuille.column_dimensions[chr(65 + idx)].width = larg

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Sections possibles du PDF, dans l'ordre d'apparition. « tombola » avant le
# détail des prêts, qui peut courir sur des pages ; elle n'apparaît que si la
# route a transmis ses données.
SECTIONS_PDF = ("synthese", "plus", "moins", "tombola", "detail")


def construire_pdf(data: dict, periode_txt: str,
                   sections: "set[str] | None" = None,
                   couleur: str = COULEUR_ASSOCIATION_DEFAUT,
                   tombola: dict | None = None) -> bytes:
    """
    Construit un PDF de bilan, avec sections au choix.

    Comme pour le classeur Excel, le tableau « Détail des prêts » ne porte pas
    de colonne « numéro de pochette » (voir `construire_xlsx`).

    Args:
        data: dict de services.collecter_stats.
        periode_txt: libellé lisible de la période.
        sections: ensemble des sections à inclure parmi SECTIONS_PDF
            ("synthese", "plus", "moins", "detail"). None = toutes.
        couleur: couleur d'identité de l'association (`#rrggbb`), lue et
            transmise par la route — voir la note en tête de ce fichier.
            Défaut explicite (l'anthracite) : un appel qui l'oublie produit un
            PDF cohérent, jamais une exception.
        tombola: statistiques de la tombola (`tombola.statistiques`), lues
            et transmises par la route ; None = pas de section, même cochée.

    Returns:
        Le contenu binaire du fichier .pdf.
    """
    if sections is None:
        sections = set(SECTIONS_PDF)
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    styles = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Statistiques de prêt",
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    elements = []

    # Deux couleurs suffisent (voir docs/ui-composants.md § 18) : le fond
    # d'en-tête (la couleur d'identité elle-même) et le fond des lignes
    # alternées (sa nuance « fond »). La couleur du texte d'en-tête suit la
    # luminance, comme le bandeau du site — jamais `colors.white` en dur, qui
    # deviendrait illisible sur une couleur d'identité claire.
    couleur_texte = couleur_texte_sur(couleur)
    couleur_fond = nuances_theme(couleur)["fond"]

    def tableau(entetes, lignes, largeurs):
        t = Table([entetes] + lignes, colWidths=largeurs, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(couleur)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(couleur_texte)),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(couleur_fond)]),
        ]))
        return t

    elements.append(Paragraph(f"Statistiques de prêt — {nom_association()}",
                              styles["Title"]))
    elements.append(Paragraph(f"Période : {periode_txt}", styles["Normal"]))
    elements.append(Spacer(1, 0.4 * cm))

    # Synthèse
    if "synthese" in sections:
        g = data["globales"]
        elements.append(Paragraph("Synthèse", styles["Heading2"]))
        elements.append(tableau(
            ["Indicateur", "Valeur"],
            [["Prêts au total", str(g["total_prets"])],
             ["Prêts en cours", str(g["en_cours"])],
             ["Titres prêtés", str(g["titres_pretes"])],
             ["Titres au catalogue", str(g["nb_titres"])],
             ["Durée moyenne de prêt", g.get("duree_moyenne", "—")],
             [LIBELLE_OUBLIS, str(g.get("oublis", 0))],
             ["Erreurs de prêt (hors chiffres ci-dessus)",
              str(g.get("erreurs", 0))]],
            [8 * cm, 4 * cm]))
        elements.append(Spacer(1, 0.4 * cm))

    # Palmarès (les deux sections sont indépendantes). Le PDF garde ses 15
    # lignes et sa section « moins » jusqu'au lot bilan-7, qui le refait ;
    # la page et l'Excel portent jusqu'à 50 plus prêtés (lot bilan-5).
    palmares_sections = []
    if "plus" in sections:
        palmares_sections.append(("Les plus prêtés",
                                  data["plus"][:LIMITE_PALMARES_PDF]))
    if "moins" in sections:
        palmares_sections.append(("Les moins prêtés",
                                  data["moins"][:LIMITE_PALMARES_PDF]))
    for titre_section, jeux in palmares_sections:
        elements.append(Paragraph(
            f"{titre_section} ({_libelle_metrique(data['metrique'])})",
            styles["Heading2"]))
        lignes = [[j["nom"], str(j["nb_prets"]), str(j["nb_exemplaires"]),
                   f"{j['par_exemplaire']:.2f}"] for j in jeux]
        elements.append(tableau(["Jeu", "Prêts", "Ex.", "Par ex."],
                                lignes, [9 * cm, 2 * cm, 2 * cm, 2.5 * cm]))
        elements.append(Spacer(1, 0.4 * cm))

    # Tombola (série tombola, lot 2)
    if "tombola" in sections and tombola:
        elements.append(Paragraph("Tombola", styles["Heading2"]))
        elements.append(tableau(
            ["Indicateur", "Valeur"],
            [[lib, str(val)] for lib, val in synthese_tombola(tombola)],
            [8 * cm, 4 * cm]))
        for titre_tableau, entetes, lignes_tableau in tableaux_tombola(tombola):
            elements.append(Spacer(1, 0.25 * cm))
            elements.append(Paragraph(titre_tableau, styles["Heading3"]))
            largeur = 17 * cm / len(entetes)
            elements.append(tableau(entetes,
                                    [[str(v) for v in l] for l in lignes_tableau],
                                    [largeur] * len(entetes)))
        elements.append(Spacer(1, 0.4 * cm))

    # Détail des prêts
    if "detail" in sections:
        elements.append(Paragraph(f"Détail des prêts ({len(data['prets'])})",
                                  styles["Heading2"]))
        lignes = [[p["nom"], p["id_exemplaire"], p["sortie_locale"],
                   p["retour_local"] or "en cours", p["duree_txt"]]
                  for p in data["prets"]]
        if lignes:
            elements.append(tableau(
                ["Jeu", "Ex.", "Sortie", "Retour", "Durée"],
                lignes,
                [6.5 * cm, 2.2 * cm, 3.3 * cm, 3.3 * cm, 2.2 * cm]))
        else:
            elements.append(Paragraph("Aucun prêt sur la période.", styles["Normal"]))

    doc.build(elements)
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
