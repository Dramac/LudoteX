#!/usr/bin/env python3
"""
Régénère les captures d'écran du wiki de LudoteX, sur une instance jetable.

POURQUOI UN SCRIPT
------------------
Les captures prises à la main vieillissent sans que rien ne le signale : les
douze premières du wiki ont été jetées pour cette raison (lot 6a). Celles-ci
se REGÉNÈRENT : à relancer à chaque version qui change un écran.

CE QUE FAIT LE SCRIPT
---------------------
1. Il prépare une instance jetable dans un dossier temporaire : un fichier
   d'environnement à lui (trois bases, journal, jeton et mot de passe tirés au
   hasard), qu'il désigne par `LUDOTEX_ENV_FILE`. Il ne lit ni n'écrit JAMAIS
   `data/` ni le `.env` de la racine.
2. Il la peuple avec `python -m app.formation`, dans un sous-processus, après
   avoir fixé la graine du hasard : deux passes tirent les mêmes jeux.
3. Il lance `uvicorn` sur 127.0.0.1, pose l'identité FICTIVE par l'écran
   d'administration, prépare chaque état AU CLIC (un prêt par le bouton
   « Prêter », une annonce par son formulaire), puis photographie les pages.
4. Il arrête le serveur, même en cas d'erreur.

Le script est un CLIENT HTTP : il n'importe aucun module de l'application ni
aucune base. L'application ne l'importe jamais.

INSTALLATION (une fois, poste de développement seulement)
---------------------------------------------------------
    pip install -r requirements-dev.txt
    python -m playwright install chromium

USAGE
-----
    python scripts/captures_wiki.py                 # toutes les captures
    python scripts/captures_wiki.py --liste         # la liste, sans rien lancer
    python scripts/captures_wiki.py --seulement guide-benevole-pret-disponible
    python scripts/captures_wiki.py --sortie /tmp/captures --dossier /tmp/instance

Les images vont par défaut dans `wiki/images/`. Le wiki est un dépôt git
séparé : les committer là-bas. Avant le commit, ouvrir chaque image et la
relire (identité fictive, aucun jeton, aucun prénom réel) : une recherche de
texte ne trouve rien dans un PNG.

Le texte alternatif de chaque capture vit ICI, à côté d'elle. Le wiki en porte
une copie, verrouillée par `tests/test_captures_wiki.py` : modifier l'un sans
l'autre fait échouer la suite.
"""

from __future__ import annotations

import argparse
import re
import datetime as _dt
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

RACINE = Path(__file__).resolve().parent.parent
SORTIE_DEFAUT = RACINE / "wiki" / "images"
NOMS_BANNIS = RACINE / "interne" / "noms-bannis.txt"

# ---------------------------------------------------------------------------
# L'identité FICTIVE de l'instance de captures — écrite ici, et nulle part
# ailleurs. Elle apparaît dans le bandeau de chaque image publiée. Un nom
# manifestement inventé (une commune qui n'existe pas), vérifié absent de la
# liste des noms bannis avant toute capture.
# ---------------------------------------------------------------------------
NOM_ASSOCIATION_FICTIF = "Ludothèque de Valjoli-sur-Dés"
PRESENTATION_FICTIVE = (
    "Association fictive, inventée pour illustrer le guide de LudoteX. "
    "Toute ressemblance avec une association existante serait fortuite."
)
NOM_EVENEMENT_FICTIF = "Festival du jeu de Valjoli"
ANNONCE_FICTIVE = "Tirage de la tombola à 16 h, au stand d'accueil."
EMPLACEMENTS_FICTIFS = ("Étagère A", "Étagère B", "Réserve")
TIRAGE_FICTIF = ("124", "37", "302")
DESCRIPTIF_FICTIF = ("Le résumé du jeu, tel que le bureau l'écrit sur sa fiche : "
                     "le but, le matériel, l'ambiance d'une partie.")

# Graine du hasard du peuplement (`app.formation` tire ses jeux, ses prêts et
# ses tournois avec `random`) : deux passes produisent les mêmes jeux.
GRAINE = 6

# Formats de capture, mesurés au lot 6b (chiffres dans son compte rendu) :
# - téléphone : 390 × 844 points, la taille d'un téléphone courant ;
# - ordinateur : 1200 points de large (le tableau de bord d'administration
#   passe en deux colonnes au-delà de 900).
# Densité 1 pour les deux. À densité 2, une capture téléphone pèse deux fois
# plus et mesure 780 × 1688 pixels : la syntaxe d'image du wiki ne fixe pas de
# largeur, et la page l'afficherait à cette taille, sur près de deux écrans.
FORMATS = {
    "telephone": {"viewport": {"width": 390, "height": 844},
                  "device_scale_factor": 1, "is_mobile": True, "has_touch": True},
    "ordinateur": {"viewport": {"width": 1200, "height": 900},
                   "device_scale_factor": 1, "is_mobile": False, "has_touch": False},
}
PROFILS = ("visiteur", "benevole", "admin")

# Clés retirées de l'environnement hérité avant de lancer un sous-processus :
# toutes celles du modèle `.env.example` (actives ou commentées, la clé
# BoardGameGeek comprise), plus celles que seule l'instance de captures pose.
# `charger_env` n'écrase JAMAIS une variable déjà présente : un
# `DATABASE_PATH` exporté dans le terminal de l'appelant ferait sinon écrire
# le peuplement dans SA base. Lues dans le modèle plutôt qu'écrites ici : une
# clé ajoutée un jour au modèle est purgée sans retoucher ce script.
MODELE_ENV = RACINE / ".env.example"
CLES_PROPRES = ("FORMATION_SOURCE_DB", "LUDOTEX_ENV_FILE")


def cles_instance(modele: Path = MODELE_ENV) -> set[str]:
    """Les clés du modèle d'environnement, plus celles propres aux captures."""
    cles = set(CLES_PROPRES)
    if modele.is_file():
        for ligne in modele.read_text(encoding="utf-8").splitlines():
            trouve = re.match(r"#?\s*([A-Z][A-Z0-9_]*)=", ligne)
            if trouve:
                cles.add(trouve.group(1))
    return cles


# ---------------------------------------------------------------------------
# La liste déclarative des captures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Capture:
    """
    Une capture du wiki.

    nom          : nom du fichier, `<page>-<sujet>.png` (minuscules, sans accent).
    page         : page wiki cible (fichier `wiki/<page>.md`).
    format       : "telephone" ou "ordinateur".
    profil       : "visiteur", "benevole" ou "admin".
    url          : adresse à ouvrir, avec des champs `{…}` tirés de l'état de
                   l'instance ; None quand la préparation laisse elle-même la
                   page à photographier (réponse d'un bouton).
    alt          : texte alternatif, prêt à coller dans la page.
    priorite     : "rouge" (indispensable), "orange" (utile), "jaune" (confort).
    preparation  : fonction appelée avant, avec la session et l'onglet.
    selecteur    : élément à cadrer ; None pour la fenêtre entière.
    hauteur      : photographier la page sur cette hauteur (en points),
                   au-delà de la fenêtre ; None pour la fenêtre seule.
    masques      : sélecteurs recouverts d'un aplat (un jeton, par exemple).
    """

    nom: str
    page: str
    format: str
    profil: str
    url: str | None
    alt: str
    priorite: str
    preparation: Callable | None = None
    selecteur: str | None = None
    hauteur: int | None = None
    masques: tuple[str, ...] = ()


# --- Préparations ----------------------------------------------------------
# Chacune passe par l'INTERFACE, comme un bénévole ou le bureau : la
# préparation casse le jour où le parcours casse. Chacune se suffit à
# elle-même (sa propre boîte), pour que `--seulement` fonctionne.

def _preter(session, onglet, cle):
    """Prête la boîte `cle` par le bouton « Prêter » ; laisse l'écran de résultat."""
    onglet.goto(session.url(f"/pret/{session.etat[cle]}"))
    onglet.get_by_role("button", name="Prêter", exact=True).click()
    onglet.wait_for_load_state()


def prep_pret_numero_pochette(session, onglet):
    _preter(session, onglet, "boite_pochette")


def prep_deja_sorti(session, onglet):
    _preter(session, onglet, "boite_sortie")


def prep_transfert(session, onglet):
    _preter(session, onglet, "boite_transfert")


def prep_sortie_demo(session, onglet):
    onglet.goto(session.url(f"/pret/{session.etat['boite_demo']}"))
    onglet.get_by_role("button", name="Sortir pour une démo").click()
    onglet.wait_for_load_state()


def _reference(onglet, session, cle):
    """La référence du jeu, lue dans le lien « Gérer cette boîte » de l'écran de prêt."""
    onglet.goto(session.url(f"/pret/{session.etat[cle]}"))
    lien = onglet.get_by_role("link", name="Gérer cette boîte").get_attribute("href")
    return lien.split("/admin/jeu/")[1].split("#")[0]


def prep_reference_retrait(session, onglet):
    session.etat["ref_retrait"] = _reference(onglet, session, "boite_retrait")


def prep_fiche_visiteur(session, onglet):
    """
    Corrige la fiche du jeu par l'écran du bureau : le peuplement y écrit
    « Fiche FICTIVE créée par le mode formation » et la catégorie
    « Formation », qui n'ont rien à faire sur une fiche publique.
    """
    admin = session.onglet("admin", "ordinateur")
    try:
        ref = _reference(admin, session, "boite_dispo")
        admin.goto(session.url(f"/admin/jeu/{ref}"))
        formulaire = admin.locator(f"form[action='/admin/jeu/{ref}/modifier']")
        formulaire.locator("input[name=categorie][value=Formation]").uncheck()
        formulaire.locator("#categorie_ajout").fill("Cartes, Ambiance")
        formulaire.locator("#descriptif").fill(DESCRIPTIF_FICTIF)
        formulaire.locator("button[type=submit]").last.click()
        admin.wait_for_load_state()
    finally:
        admin.close()


# Champs `{…}` : `session.etat`, rempli par `preparer_instance`. Les numéros
# de tournoi et d'événement de planning suivent l'ordre de création
# d'`app.formation` sur une base neuve (tournoi 2 : inscriptions ouvertes ;
# 4 : high score lancé, scores saisis ; planning 1 : publié, 2 : en collecte).
# Si ce peuplement change, le contrôle des images le verra.
CAPTURES: list[Capture] = [
    # ---------------- Guide bénévole — téléphone ----------------
    Capture(
        nom="guide-benevole-activation-lien",
        page="Guide-Benevole", format="telephone", profil="visiteur",
        url="/acces?jeton={jeton}", priorite="rouge", masques=("#video",),
        alt="Écran Scanner un jeu, ouvert juste après le lien d'activation, avec le cadre de la caméra",
    ),
    Capture(
        nom="guide-benevole-pret-disponible",
        page="Guide-Benevole", format="telephone", profil="benevole",
        url="/pret/{boite_dispo}", priorite="rouge",
        alt="Écran d'un jeu disponible, avec le bouton Prêter en tête",
    ),
    Capture(
        nom="guide-benevole-pret-numero-pochette",
        page="Guide-Benevole", format="telephone", profil="benevole",
        url=None, preparation=prep_pret_numero_pochette, priorite="rouge",
        alt="Écran après un prêt : le numéro de pochette affiché en grand",
    ),
    Capture(
        nom="guide-benevole-deja-sorti",
        page="Guide-Benevole", format="telephone", profil="benevole",
        url="/pret/{boite_sortie}", preparation=prep_deja_sorti, priorite="rouge",
        alt="Écran d'un jeu déjà sorti, avec ses boutons de transfert et de retour",
    ),
    Capture(
        nom="guide-benevole-sortie-demo",
        page="Guide-Benevole", format="telephone", profil="benevole",
        url=None, preparation=prep_sortie_demo, priorite="rouge",
        alt="Écran d'un jeu sorti pour une démo, sans numéro de pochette",
    ),
    Capture(
        nom="guide-benevole-carnet-maintenance",
        page="Guide-Benevole", format="telephone", profil="benevole",
        url="/maintenance", priorite="rouge", hauteur=1000,
        alt="Carnet de maintenance sur téléphone : les boîtes signalées, en cartes",
    ),
    # ---------------- Module Prêt ----------------
    Capture(
        nom="module-pret-catalogue-filtres",
        page="Module-Pret", format="ordinateur", profil="visiteur",
        url="/catalogue?joueurs=4&dispo=1", priorite="rouge", hauteur=1500,
        alt="Catalogue public filtré sur quatre joueurs et les jeux disponibles",
    ),
    Capture(
        nom="module-pret-fiche-visiteur",
        page="Module-Pret", format="telephone", profil="visiteur",
        url="/jeu/{boite_dispo}", preparation=prep_fiche_visiteur, priorite="orange",
        alt="Fiche publique d'un jeu vue par un visiteur, sans bouton d'action",
    ),
    Capture(
        nom="module-pret-transfert",
        page="Module-Pret", format="telephone", profil="benevole",
        url="/pret/{boite_transfert}/transfert", preparation=prep_transfert,
        priorite="rouge", masques=("#video",),
        alt="Écran du transfert : scanner le nouveau jeu, la pièce d'identité reste dans sa pochette",
    ),
    Capture(
        nom="module-pret-echange",
        page="Module-Pret", format="telephone", profil="benevole",
        url="/pret/{boite_echange}/echange", priorite="rouge", masques=("#video",),
        alt="Écran de l'échange : scanner le jeu que le visiteur rend",
    ),
    Capture(
        nom="module-pret-qr-ephemere",
        page="Module-Pret", format="telephone", profil="benevole",
        url="/pret/X01", priorite="rouge",
        alt="QR éphémère pas encore nommé : le champ Nom du jeu et le bouton Nommer ce jeu",
    ),
    # ---------------- Jeux sortis ----------------
    Capture(
        nom="jeux-sortis-liste",
        page="Jeux-Sortis", format="telephone", profil="benevole",
        url="/sortis", priorite="rouge",
        alt="Liste des jeux sortis sur téléphone, avec le numéro de pochette de chacun",
    ),
    # ---------------- Guide administrateur — ordinateur ----------------
    Capture(
        nom="guide-admin-tableau-de-bord",
        page="Guide-Admin", format="ordinateur", profil="admin",
        url="/admin", priorite="rouge",
        # La colonne de droite (supervision) montre les chemins des bases et
        # l'espace disque de la machine qui lance le script : hors cadre.
        selecteur=".admin-dashboard-col-principale",
        alt="Tableau de bord de l'administration : les liens vers chaque réglage, rangés par thème",
    ),
    Capture(
        nom="guide-admin-identite",
        page="Guide-Admin", format="ordinateur", profil="admin",
        url="/admin/identite", priorite="rouge",
        alt="Écran Identité de l'association, rempli avec un nom d'association fictif",
    ),
    Capture(
        nom="guide-admin-jeton",
        page="Guide-Admin", format="ordinateur", profil="admin",
        url="/admin/jeton", priorite="rouge",
        masques=("code", "input[readonly]"),
        alt="Écran du jeton bénévole, le jeton et le lien masqués",
    ),
    Capture(
        nom="guide-admin-donnees-sauvegarde",
        page="Guide-Admin", format="ordinateur", profil="admin",
        url="/admin/donnees", priorite="rouge",
        alt="Écran Données et sauvegarde : import, export et sauvegarde",
    ),
    Capture(
        nom="guide-admin-retirer-boite",
        page="Guide-Admin", format="ordinateur", profil="admin",
        url="/admin/jeu/{ref_retrait}/exemplaire/{boite_retrait}/retirer",
        preparation=prep_reference_retrait, priorite="rouge", hauteur=1150,
        alt="Écran de retrait d'une boîte du catalogue, avec son motif facultatif",
    ),
    # ---------------- Rangement ----------------
    Capture(
        nom="module-rangement-admin-contextes",
        page="Module-Rangement", format="ordinateur", profil="admin",
        url="/admin/rangement", priorite="rouge", hauteur=1250,
        alt="Écran Rangement : le contexte actif et la liste des emplacements",
    ),
    # ---------------- Tournois ----------------
    Capture(
        nom="module-tournois-gerer-inscriptions",
        page="Module-Tournois", format="ordinateur", profil="benevole",
        url="/tournoi/2/gerer", priorite="rouge",
        selecteur="section.carte:has(h2:has-text('Participants'))",
        alt="Liste des inscrits d'un tournoi, sur son écran de gestion",
    ),
    Capture(
        nom="module-tournois-public-classement",
        page="Module-Tournois", format="ordinateur", profil="visiteur",
        url="/tournoi/4", priorite="orange",
        alt="Page publique d'un tournoi en cours, avec son classement",
    ),
    # ---------------- Planning ----------------
    Capture(
        nom="module-planning-grille-trous",
        page="Module-Planning", format="ordinateur", profil="admin",
        url="/planning/admin/1", priorite="rouge",
        selecteur="section.carte:has(h2:has-text('4. Grille'))",
        alt="Grille du planning bénévole, avec des cases en manque",
    ),
    Capture(
        nom="module-planning-collecte-preferences",
        page="Module-Planning", format="telephone", profil="visiteur",
        url="/planning/collecte/2", priorite="orange",
        alt="Formulaire de souhaits d'un bénévole sur téléphone",
    ),
    # ---------------- Statistiques ----------------
    Capture(
        nom="module-statistiques-chiffres",
        page="Module-Statistiques", format="ordinateur", profil="benevole",
        url="/stats", priorite="rouge",
        alt="Page des statistiques : les quatre chiffres et le sommaire",
    ),
    # ---------------- Programme ----------------
    Capture(
        nom="module-programme-grille-publique",
        page="Module-Programme", format="telephone", profil="visiteur",
        url="/programme", priorite="rouge",
        alt="Programme du week-end sur téléphone, avec ses filtres",
    ),
    Capture(
        nom="module-programme-gestion",
        page="Module-Programme", format="ordinateur", profil="admin",
        url="/programme/gestion", priorite="rouge",
        alt="Liste de gestion du programme, avec ses filtres et les boutons Tout publier et Tout dépublier",
    ),
    # ---------------- Tombola ----------------
    Capture(
        nom="module-tombola-page-publique",
        page="Module-Tombola", format="telephone", profil="visiteur",
        url="/tombola", priorite="rouge",
        alt="Page publique de la tombola, avec les numéros tirés",
    ),
    Capture(
        nom="module-tombola-gestion",
        page="Module-Tombola", format="telephone", profil="benevole",
        url="/tombola/gestion", priorite="rouge",
        alt="Gestion de la tombola sur téléphone : remise des lots et saisie d'un tirage",
    ),
    # ---------------- Écran de salle ----------------
    Capture(
        nom="module-ecran-salle-annonce",
        page="Module-Ecran-Salle", format="ordinateur", profil="visiteur",
        url="/live", priorite="orange",
        alt="Écran de salle avec une annonce dans le bandeau",
    ),
    # ---------------- Accueil (expliqué par la page Programme) ----------------
    Capture(
        nom="module-programme-accueil-ca-commence-bientot",
        page="Module-Programme", format="telephone", profil="visiteur",
        url="/", priorite="orange",
        selecteur="section.carte:has(h2:has-text('Ça commence bientôt'))",
        alt="Bloc Ça commence bientôt de la page d'accueil : les prochains tournois et animations",
    ),
    # ---------------- Fonctionnalités ----------------
    Capture(
        nom="fonctionnalites-quatre-etats",
        page="Fonctionnalites", format="ordinateur", profil="admin",
        url="/admin/fonctionnalites", priorite="jaune",
        alt="Écran Fonctionnalités : un module par ligne, quatre états au choix",
    ),
]

# Captures déclarées mais pas encore prises (lot 6b, voir son compte rendu) :
# - #2 du plan, le scanner caméra active : un vrai téléphone devant un vrai QR,
#   à prendre à la main ;
# - guide-benevole-retour-emplacement (orange) : demande une boîte rangée
#   avant son retour ;
# - module-rangement-admin-ranger-titres (orange), module-rangement-bandeau-actif
#   (jaune) ;
# - guide-admin-etiquettes (orange) et l'extrait de planche (orange, un PDF) ;
# - guide-admin-supervision (orange) : l'écran montre les chemins des bases et
#   l'espace disque de la machine qui lance le script, et alarme sur une
#   instance neuve (aucune sauvegarde) : rien de juste à illustrer.


# ---------------------------------------------------------------------------
# Instance jetable
# ---------------------------------------------------------------------------
def contenu_env(dossier: Path, port: int, jeton: str, mot_de_passe: str) -> str:
    """
    Le fichier d'environnement de l'instance de captures.

    Tout chemin est sous `dossier`. Ni `MODE_FORMATION` (son filigrane
    traverserait chaque image), ni clé BoardGameGeek (aucune requête externe).
    `FORMATION_SOURCE_DB` vise un chemin qui n'existe pas : le peuplement
    prend alors les noms de jeux de secours d'`app.formation`, les mêmes sur
    tous les postes, et jamais un titre du catalogue réel de la machine.
    `NOM_ASSOCIATION` reste vide : l'identité se pose par l'écran
    d'administration, comme le ferait un bureau.
    """
    dossier = Path(dossier)
    lignes = {
        "DATABASE_PATH": dossier / "pret.db",
        "TOURNOI_DATABASE_PATH": dossier / "tournoi.db",
        "PLANNING_DATABASE_PATH": dossier / "planning.db",
        "JOURNAL_PATH": dossier / "journal.log",
        "FORMATION_SOURCE_DB": dossier / "aucun-catalogue-source.db",
        "PRET_TOKEN": jeton,
        "ADMIN_PASSWORD": mot_de_passe,
        "BASE_URL": f"http://127.0.0.1:{port}",
        "NOM_ASSOCIATION": "",
        "APP_ENV": "development",
        # Le script enchaîne des dizaines d'écritures depuis 127.0.0.1 en
        # quelques secondes : la limite de débit par IP les refuserait.
        "RATE_LIMIT_PER_MINUTE": 100000,
        "JOURNAL_CONSOLE": 0,
    }
    return "".join(f"{cle}={valeur}\n" for cle, valeur in lignes.items())


def environnement_sous_processus(fichier_env: Path) -> dict:
    """L'environnement hérité, purgé des clés de l'instance, + `LUDOTEX_ENV_FILE`."""
    purgees = cles_instance()
    env = {k: v for k, v in os.environ.items() if k not in purgees}
    env["LUDOTEX_ENV_FILE"] = str(fichier_env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def verifier_nom_fictif(nom: str = NOM_ASSOCIATION_FICTIF,
                        fichier: Path = NOMS_BANNIS) -> None:
    """
    Refuse de continuer si le nom fictif reprend un nom banni, ou l'un de ses
    mots de quatre lettres ou plus (« Ludothèque » excepté : c'est le nom
    commun du métier, pas celui d'une association).
    """
    if not fichier.is_file():
        return
    mots_fictifs = set(_mots(nom))
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        if not ligne.strip() or ligne.startswith("#"):
            continue
        mots_bannis = [m for m in _mots(ligne) if len(m) >= 4 and m != "ludotheque"]
        if " ".join(_mots(ligne)) in " ".join(_mots(nom)) or \
                mots_fictifs.intersection(mots_bannis):
            raise SystemExit("Le nom fictif reprend un nom banni : changez-le.")


def _mots(texte: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _sans_accents(texte).lower())


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texte)
                   if not unicodedata.combining(c))


def peupler_instance(fichier_env: Path) -> None:
    """`python -m app.formation`, graine fixée, dans un sous-processus."""
    code = (
        "import random, runpy\n"
        f"random.seed({GRAINE})\n"
        "runpy.run_module('app.formation', run_name='__main__', alter_sys=True)\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=RACINE, check=True,
                   env=environnement_sous_processus(fichier_env),
                   stdout=subprocess.DEVNULL)


def lancer_serveur(fichier_env: Path, port: int, journal: Path) -> subprocess.Popen:
    sortie = open(journal, "wb")
    processus = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=RACINE, env=environnement_sous_processus(fichier_env),
        stdout=sortie, stderr=subprocess.STDOUT,
    )
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        if processus.poll() is not None:
            raise SystemExit(f"Le serveur s'est arrêté au démarrage : voir {journal}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/sante", timeout=2):
                return processus
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    processus.terminate()
    raise SystemExit(f"Le serveur ne répond pas sur /sante : voir {journal}")


def arreter_serveur(processus: subprocess.Popen) -> None:
    processus.terminate()
    try:
        processus.wait(timeout=10)
    except subprocess.TimeoutExpired:
        processus.kill()


# ---------------------------------------------------------------------------
# Session de navigation
# ---------------------------------------------------------------------------
class Session:
    """Le navigateur, un contexte par (profil, format), et l'état de l'instance."""

    def __init__(self, navigateur, port: int, jeton: str, mot_de_passe: str):
        self.navigateur = navigateur
        self.base = f"http://127.0.0.1:{port}"
        self.jeton = jeton
        self.mot_de_passe = mot_de_passe
        self.contextes: dict = {}
        self.hotes: set[str] = set()
        self.etat: dict[str, str] = {"jeton": jeton}

    def url(self, chemin: str) -> str:
        return self.base + chemin

    def _noter_requete(self, requete) -> None:
        adresse = urlparse(requete.url)
        if adresse.scheme in ("http", "https", "ws", "wss"):
            self.hotes.add(adresse.hostname or "")

    def onglet(self, profil: str, format_: str):
        """Un onglet neuf dans le contexte (profil, format), connecté au besoin."""
        cle = (profil, format_)
        if cle not in self.contextes:
            contexte = self.navigateur.new_context(
                **FORMATS[format_], locale="fr-FR", timezone_id="Europe/Paris",
                reduced_motion="reduce", color_scheme="light",
            )
            contexte.on("request", self._noter_requete)
            self.contextes[cle] = contexte
            onglet = contexte.new_page()
            if profil in ("benevole", "admin"):
                onglet.goto(self.url(f"/acces?jeton={self.jeton}"))
            if profil == "admin":
                self.connecter_admin(onglet)
            onglet.close()
        return self.contextes[cle].new_page()

    def connecter_admin(self, onglet) -> None:
        # Le formulaire s'affiche sur /admin ; /admin/login ne reçoit que le POST.
        onglet.goto(self.url("/admin"))
        onglet.fill("#mot_de_passe", self.mot_de_passe)
        onglet.get_by_role("button", name="Se connecter").click()
        onglet.wait_for_load_state()

    def fermer(self) -> None:
        for contexte in self.contextes.values():
            contexte.close()


def preparer_instance(session: Session) -> None:
    """
    L'état commun à toutes les captures, posé par les écrans du bureau.

    Appelée à chaque passe, même pour une seule capture : l'instance est neuve.
    """
    onglet = session.onglet("admin", "ordinateur")

    # Identité fictive, par l'écran du bureau. Logo et couleur : ceux par défaut.
    onglet.goto(session.url("/admin/identite"))
    onglet.fill("#nom_association", NOM_ASSOCIATION_FICTIF)
    onglet.fill("#presentation", PRESENTATION_FICTIVE)
    onglet.locator("form[action='/admin/identite'] button[type=submit]").first.click()
    onglet.wait_for_load_state()

    # Nom et date de l'événement (la frise de l'accueil en dépend).
    onglet.goto(session.url("/admin/evenement"))
    onglet.fill("#nom_evenement", NOM_EVENEMENT_FICTIF)
    onglet.fill("#date_evenement", _dt.date.today().isoformat())
    onglet.locator("form[action='/admin/evenement'] button[type=submit]").click()
    onglet.wait_for_load_state()

    # Les boîtes disponibles, dans l'ordre du catalogue : une par capture qui
    # en change l'état, pour que chacune se prépare seule.
    onglet.goto(session.url("/catalogue?dispo=1"))
    liens = onglet.locator("a[href^='/jeu/']").evaluate_all(
        "els => els.map(e => e.getAttribute('href').slice(5))")
    dispos = list(dict.fromkeys(liens))
    for cle in ("boite_dispo", "boite_pochette", "boite_sortie",
                "boite_transfert", "boite_demo", "boite_echange",
                "boite_signalee", "boite_signalee_2", "boite_retrait"):
        session.etat[cle] = dispos.pop(0)

    # La tombola est désactivée par défaut : la rendre visible de tous.
    onglet.goto(session.url("/admin/fonctionnalites"))
    onglet.check("#mod_tombola_tous")
    onglet.click("#btn-enregistrer")
    onglet.wait_for_load_state()

    # Une annonce sur l'écran de salle, par le formulaire du bureau.
    onglet.goto(session.url("/admin/ecran-salle"))
    onglet.fill("#annonce", ANNONCE_FICTIVE)
    onglet.fill("#annonce_duree", "60")
    onglet.get_by_role("button", name="Ajouter une annonce").click()
    onglet.wait_for_load_state()

    # Les emplacements de départ, semés par l'application à la création de la
    # base, sont supprimés par l'écran (aucune boîte n'y est rangée) ; trois
    # emplacements fictifs les remplacent.
    onglet.on("dialog", lambda dialogue: dialogue.accept())
    onglet.goto(session.url("/admin/rangement"))
    while onglet.get_by_role("button", name="Supprimer").count():
        onglet.get_by_role("button", name="Supprimer").first.click()
        onglet.wait_for_load_state()
    for nom in EMPLACEMENTS_FICTIFS:
        onglet.goto(session.url("/admin/rangement"))
        onglet.fill("#nom-nouveau", nom)
        onglet.locator("form[action='/admin/rangement/emplacements'] button").click()
        onglet.wait_for_load_state()

    # Deux signalements au comptoir ; le second est marqué traité.
    for cle, texte in (("boite_signalee", "Il manque deux cartes."),
                       ("boite_signalee_2", "Couvercle déchiré dans un angle.")):
        onglet.goto(session.url(f"/pret/{session.etat[cle]}/signaler"))
        onglet.locator("input[name=id_categorie]").first.check()
        onglet.fill("textarea[name=texte]", texte)
        onglet.get_by_role("button", name="Envoyer le signalement").click()
        onglet.wait_for_load_state()
    onglet.goto(session.url(f"/pret/{session.etat['boite_signalee_2']}"))
    onglet.get_by_role("button", name="comme traité").first.click()
    onglet.wait_for_load_state()

    # Un tirage de tombola saisi à la main, puis un lot remis.
    onglet.goto(session.url("/tombola/gestion"))
    for i, numero in enumerate(TIRAGE_FICTIF, start=1):
        onglet.locator(f"input[name=numero_{i}]").first.fill(numero)
    onglet.get_by_role("button", name="Afficher les numéros").first.click()
    onglet.wait_for_load_state()
    onglet.get_by_role("button", name="Lot remis").nth(1).click()
    onglet.wait_for_load_state()
    onglet.close()


# ---------------------------------------------------------------------------
# Prise des captures
# ---------------------------------------------------------------------------
def prendre(session: Session, capture: Capture, sortie: Path) -> Path:
    onglet = session.onglet(capture.profil, capture.format)
    try:
        if capture.preparation:
            capture.preparation(session, onglet)
        if capture.url is not None:
            onglet.goto(session.url(capture.url.format(**session.etat)))
        onglet.wait_for_load_state("networkidle")
        chemin = sortie / f"{capture.nom}.png"
        options = {"path": str(chemin), "animations": "disabled",
                   "mask": [onglet.locator(s) for s in capture.masques],
                   # L'aplat d'un masque : noir, comme un viseur éteint.
                   "mask_color": "black"}
        largeur = FORMATS[capture.format]["viewport"]["width"]
        if capture.selecteur:
            # Cadre pris sur la page ENTIÈRE, pas sur l'élément défilé jusqu'à
            # lui : le bandeau collant du haut le recouvrirait.
            boite = onglet.locator(capture.selecteur).first.evaluate(
                "e => { const r = e.getBoundingClientRect();"
                " return {x: r.x + scrollX, y: r.y + scrollY,"
                " width: r.width, height: r.height}; }")
            marge = 8
            onglet.screenshot(full_page=True, clip={
                "x": max(0, boite["x"] - marge), "y": max(0, boite["y"] - marge),
                "width": min(largeur, boite["width"] + 2 * marge),
                "height": boite["height"] + 2 * marge}, **options)
        elif capture.hauteur:
            onglet.screenshot(full_page=True, clip={
                "x": 0, "y": 0, "width": largeur, "height": capture.hauteur},
                **options)
        else:
            onglet.screenshot(**options)
        return chemin
    finally:
        onglet.close()


def executer(captures: list[Capture], sortie: Path, dossier: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Playwright n'est pas installé. Une fois :\n"
            "  pip install -r requirements-dev.txt\n"
            "  python -m playwright install chromium")

    verifier_nom_fictif()
    sortie.mkdir(parents=True, exist_ok=True)
    dossier.mkdir(parents=True, exist_ok=True)
    port = port_libre()
    jeton = secrets.token_urlsafe(32)
    mot_de_passe = secrets.token_urlsafe(24)
    fichier_env = dossier / "captures.env"
    fichier_env.write_text(contenu_env(dossier, port, jeton, mot_de_passe),
                           encoding="utf-8")

    debut = time.monotonic()
    peupler_instance(fichier_env)
    serveur = lancer_serveur(fichier_env, port, dossier / "serveur.log")
    try:
        with sync_playwright() as p:
            # Caméra simulée : sans elle, le scanner affiche une erreur. Son
            # image (une mire animée) est recouverte d'un aplat, voir `masques`.
            # `channel="chromium"` : le navigateur complet, sans fenêtre. La
            # version allégée ignore `--lang` sous macOS et dessine ses champs
            # natifs (fichier, date) en anglais.
            navigateur = p.chromium.launch(channel="chromium", args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                # Les champs natifs (fichier, date) parlent la langue du
                # navigateur, pas celle de la page.
                "--lang=fr-FR"])
            session = Session(navigateur, port, jeton, mot_de_passe)
            try:
                preparer_instance(session)
                for capture in captures:
                    chemin = prendre(session, capture, sortie)
                    print(f"  {chemin.name}  ({chemin.stat().st_size // 1024} Ko)")
            finally:
                session.fermer()
                navigateur.close()
    finally:
        arreter_serveur(serveur)

    etrangers = sorted(h for h in session.hotes if h != "127.0.0.1")
    print(f"{len(captures)} capture(s) en {time.monotonic() - debut:.0f} s.")
    print("Hôtes contactés par le navigateur : "
          + ", ".join(sorted(session.hotes) or ["aucun"]))
    if etrangers:
        raise SystemExit("Requête hors de 127.0.0.1 : " + ", ".join(etrangers))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--liste", action="store_true",
                        help="afficher la liste des captures sans rien lancer")
    parser.add_argument("--seulement", action="append", metavar="NOM",
                        help="ne régénérer que cette capture (répétable)")
    parser.add_argument("--sortie", type=Path, default=SORTIE_DEFAUT,
                        help="dossier des images (défaut : wiki/images/)")
    parser.add_argument("--dossier", type=Path,
                        help="dossier de l'instance jetable (défaut : temporaire)")
    args = parser.parse_args(argv)

    if args.liste:
        for c in CAPTURES:
            print(f"{c.nom:50} {c.priorite:7} {c.format:10} {c.profil:9} {c.page}")
        return

    choisies = CAPTURES
    if args.seulement:
        connus = {c.nom for c in CAPTURES}
        inconnus = [n for n in args.seulement if n.removesuffix(".png") not in connus]
        if inconnus:
            raise SystemExit("Capture inconnue : " + ", ".join(inconnus))
        voulus = {n.removesuffix(".png") for n in args.seulement}
        choisies = [c for c in CAPTURES if c.nom in voulus]

    temporaire = args.dossier is None
    dossier = Path(tempfile.mkdtemp(prefix="ludotex-captures-")) if temporaire else args.dossier
    try:
        executer(choisies, args.sortie, dossier)
    finally:
        if temporaire:
            shutil.rmtree(dossier, ignore_errors=True)


if __name__ == "__main__":
    main()
