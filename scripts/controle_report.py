"""
Contrôle de report : ce que le dépôt porte est-il en place sur le serveur ?

POURQUOI CE SCRIPT EXISTE
-------------------------
`deploy/update.sh` récupère le code, migre les bases et redémarre. Il ne touche
ni aux fichiers copiés dans `/etc/` par `deploy/install.sh` (unités systemd,
configuration nginx), ni à la crontab, ni au `.env`, ni aux paquets, ni aux
permissions. Un correctif qui vit dans l'un de ces endroits peut donc être
commité, poussé, « déployé »… et rester absent du serveur sans que rien ne le
dise. C'est arrivé deux fois : la configuration nginx durcie est restée six
semaines à la porte du serveur, puis les permissions `0700` du dossier des
sauvegardes, posées par `install.sh` seul, ne sont jamais arrivées sur une
installation existante.

Ce script compare le serveur au dépôt et **nomme** ce qui diffère. Il ne
réinstalle rien : ni nginx, ni certbot, ni systemd ne doivent bouger sans une
décision humaine. Lancé à la fin d'`update.sh`, il affiche et termine toujours
en code 0 — la mise à jour ne dépend jamais de lui.

CE QU'IL VÉRIFIE
----------------
1. Unités systemd : chaque `deploy/*.service` et `deploy/*.timer` (par motif :
   un fichier ajouté au dépôt est couvert sans toucher à ce script) comparé à
   `/etc/systemd/system/`, commentaires mis de côté ; unité activée ; systemd
   rechargé depuis la dernière copie.
2. nginx : chaque `deploy/nginx-*.conf` comparé à `/etc/nginx/sites-available/`,
   après normalisation (voir `normaliser_nginx`) ; site activé.
3. `.env` : les clés attendues par `.env.example` absentes du `.env` de
   l'instance, et de `/etc/ludotex-formation.env` si la formation existe.
   **Seuls des noms de clés sortent d'ici, jamais une valeur.**
4. Tâche de sauvegarde planifiée : la ligne de crontab que pose `install.sh`,
   relue dans `install.sh` lui-même (un seul domicile).
5. Paquets système : la liste `PAQUETS_BASE` d'`install.sh`, relue de même.
6. Permissions et propriétaires qu'`install.sh` pose et qu'`update.sh` ne
   rejoue pas : dossiers des sauvegardes en `0700`, fichiers d'environnement en
   `0600`, dossier des bases au nom du service.

Ce qu'il ne vérifie pas, et pourquoi, est écrit dans
`docs/notes-de-deploiement.md`.

EXÉCUTION
---------
Bibliothèque standard uniquement : il tourne avec le Python du `.venv` comme
avec celui du système, même si l'environnement de l'application est cassé.
Prévu pour l'utilisateur du service (`pretjeux`), qui lit tout ce dont il a
besoin : `/etc/systemd/system/` et `/etc/nginx/` sont lisibles par tous, les
deux fichiers d'environnement lui appartiennent en `0600`, et sa propre
crontab se lit sans `sudo`. Il n'a pas besoin de root et ne le demande pas.

    sudo -u pretjeux /opt/ludotex/.venv/bin/python /opt/ludotex/scripts/controle_report.py
    ... --install-dir /opt/ludotex --data-dir /var/lib/ludotex

Il n'écrit rien nulle part, sauf sur la sortie standard.

La logique de comparaison est faite de fonctions pures (textes en entrée,
liste d'écarts en sortie), testées hors serveur dans
`tests/test_controle_report.py`. Seule la classe `Serveur` touche au système.
"""

from __future__ import annotations

import argparse
import difflib
import pwd
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Valeurs d'exemple du dépôt que `deploy/install.sh` remplace par des `sed` à
# l'installation. Le chemin est substitué tel quel, comme le fait install.sh ;
# le domaine, lui, est retrouvé dans chaque fichier (voir `normaliser_nginx`).
CHEMIN_EXEMPLE = "/opt/ludotex"

# Noms que `deploy/install.sh` donne aux fichiers de l'instance de formation.
ENV_FORMATION = "/etc/ludotex-formation.env"
UNITE_FORMATION = "/etc/systemd/system/ludotex-formation.service"

MARQUE_CERTBOT = "managed by Certbot"

# Ce que contient le bloc de redirection que certbot ajoute pour le port 80,
# une fois le domaine normalisé. Un bloc qui porte autre chose n'est PAS mis de
# côté : ce serait une vraie directive, à montrer.
LIGNES_BLOC_REDIRECTION_CERTBOT = frozenset(
    {
        "server {",
        "if ($host = <domaine>) {",
        "return 301 https://$host$request_uri;",
        "}",
        "listen 80;",
        "listen [::]:80;",
        "server_name <domaine>;",
        "return 404;",
    }
)

# certbot retire ces deux lignes du bloc d'origine pour les déplacer dans son
# bloc de redirection : les comparer ne dirait rien d'utile.
LIGNES_ECOUTE_80 = frozenset({"listen 80;", "listen [::]:80;"})

# `systemctl is-enabled` : états qui laissent l'unité démarrer au boot, ou qui
# sont normaux pour un service lancé par un minuteur (`static`).
ETATS_ACTIVATION_ACCEPTES = frozenset({"enabled", "enabled-runtime", "static", "indirect", "alias", "generated"})

_RE_CLE_ENV = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


@dataclass
class Constat:
    """Résultat d'une famille de vérifications. Aucun écart = conforme."""

    titre: str
    ecarts: list[str] = field(default_factory=list)
    detail_conforme: str = ""


# ===========================================================================
# Comparaison de textes — fonctions pures
# ===========================================================================
def difference(depot: list[str], serveur: list[str]) -> list[str]:
    """
    Lignes qui diffèrent entre deux listes déjà normalisées, dans l'ordre.

    Chaque ligne est préfixée par le côté où elle se trouve seule, sans
    présumer du côté qui a raison.
    """
    ecarts: list[str] = []
    correspondance = difflib.SequenceMatcher(a=depot, b=serveur, autojunk=False)
    for code, a1, a2, b1, b2 in correspondance.get_opcodes():
        if code == "equal":
            continue
        ecarts.extend(f"dépôt seul   : {ligne}" for ligne in depot[a1:a2])
        ecarts.extend(f"serveur seul : {ligne}" for ligne in serveur[b1:b2])
    return ecarts


def _hors_guillemets(ligne: str) -> tuple[str, str, int]:
    """
    Sépare une ligne nginx en (contenu, commentaire, profondeur d'accolades).

    Un `#` n'ouvre un commentaire qu'en dehors des guillemets ; les accolades
    ne comptent qu'en dehors des guillemets, elles aussi.
    """
    guillemet = ""
    profondeur = 0
    for i, car in enumerate(ligne):
        if guillemet:
            if car == guillemet and (i == 0 or ligne[i - 1] != "\\"):
                guillemet = ""
        elif car in "\"'":
            guillemet = car
        elif car == "#":
            return ligne[:i], ligne[i + 1 :], profondeur
        elif car == "{":
            profondeur += 1
        elif car == "}":
            profondeur -= 1
    return ligne, "", profondeur


def _compacter(contenu: str) -> str:
    contenu = " ".join(contenu.split())
    return re.sub(r"\s+;", ";", contenu)


def _premier_domaine(lignes: list[str]) -> str | None:
    for ligne in lignes:
        if ligne.startswith("server_name "):
            noms = ligne[len("server_name ") :].rstrip(";").split()
            if noms:
                return noms[0]
    return None


def normaliser_nginx(texte: str) -> list[str]:
    """
    Réduit un fichier nginx à ses directives, sans ce qui diffère légitimement
    entre le fichier du dépôt et celui qui est installé.

    Mis de côté :
    - les commentaires, les lignes vides, les écarts d'espacement ;
    - le domaine : le premier nom de `server_name` est remplacé partout par
      `<domaine>` (le dépôt porte un domaine d'exemple, install.sh le remplace) ;
    - les directives simples marquées `# managed by Certbot` (`listen 443 ssl`,
      `ssl_certificate`, `include options-ssl-nginx.conf`, `ssl_dhparam`) ;
    - le bloc de redirection du port 80 qu'ajoute certbot, **seulement** s'il
      ne contient rien d'autre que ce que certbot y écrit ;
    - `listen 80;` et `listen [::]:80;`, que certbot déplace dans ce bloc.

    Le chemin d'installation n'est pas traité ici : c'est une substitution
    connue, faite par l'appelant sur le texte du dépôt (voir `comparer_nginx`).
    """
    entrees: list[tuple[str, bool, int]] = []
    for brute in texte.splitlines():
        contenu, commentaire, delta = _hors_guillemets(brute)
        contenu = _compacter(contenu)
        if contenu:
            entrees.append((contenu, MARQUE_CERTBOT in commentaire, delta))

    domaine = _premier_domaine([c for c, _, _ in entrees])
    if domaine:
        motif = re.compile(r"(?<![A-Za-z0-9.-])" + re.escape(domaine) + r"(?![A-Za-z0-9-]|\.[A-Za-z0-9])")
        entrees = [(motif.sub("<domaine>", c), m, d) for c, m, d in entrees]

    # Regroupement par bloc de premier niveau, pour reconnaître le bloc de
    # redirection de certbot en entier plutôt que ligne à ligne.
    lignes: list[str] = []
    bloc: list[tuple[str, bool, int]] = []
    profondeur = 0
    for entree in entrees:
        if profondeur == 0 and not bloc and entree[2] <= 0:
            lignes.extend(_directives_hors_certbot([entree]))
            continue
        bloc.append(entree)
        profondeur += entree[2]
        if profondeur <= 0:
            if not _est_bloc_redirection_certbot(bloc):
                lignes.extend(_directives_hors_certbot(bloc))
            bloc, profondeur = [], 0
    lignes.extend(_directives_hors_certbot(bloc))
    return lignes


def _est_bloc_redirection_certbot(bloc: list[tuple[str, bool, int]]) -> bool:
    return (
        bloc[0][0] == "server {"
        and any(marque for _, marque, _ in bloc)
        and all(contenu in LIGNES_BLOC_REDIRECTION_CERTBOT for contenu, _, _ in bloc)
    )


def _directives_hors_certbot(entrees: list[tuple[str, bool, int]]) -> list[str]:
    return [
        contenu
        for contenu, marque, _ in entrees
        if contenu not in LIGNES_ECOUTE_80 and not (marque and contenu.endswith(";"))
    ]


def comparer_nginx(depot: str, serveur: str, install_dir: str = CHEMIN_EXEMPLE) -> list[str]:
    """Écarts entre un fichier nginx du dépôt et sa copie installée."""
    depot = depot.replace(CHEMIN_EXEMPLE, install_dir)
    return difference(normaliser_nginx(depot), normaliser_nginx(serveur))


def normaliser_unite(texte: str) -> list[str]:
    """
    Réduit une unité systemd à ses lignes utiles.

    Pour systemd, seule une ligne qui COMMENCE par `#` ou `;` est un
    commentaire : un `#` en milieu de ligne fait partie de la valeur.
    """
    lignes = []
    for brute in texte.splitlines():
        ligne = " ".join(brute.split())
        if ligne and not ligne.startswith(("#", ";")):
            lignes.append(ligne)
    return lignes


def comparer_unite(depot: str, serveur: str, install_dir: str = CHEMIN_EXEMPLE) -> list[str]:
    """Écarts entre une unité du dépôt et sa copie installée."""
    depot = depot.replace(CHEMIN_EXEMPLE, install_dir)
    return difference(normaliser_unite(depot), normaliser_unite(serveur))


# ===========================================================================
# Fichiers d'environnement — des noms, jamais des valeurs
# ===========================================================================
def cles_presentes(texte: str) -> set[str]:
    """Noms des clés définies (non commentées) dans un fichier d'environnement."""
    cles = set()
    for ligne in texte.splitlines():
        trouve = _RE_CLE_ENV.match(ligne)
        if trouve:
            cles.add(trouve.group(1))
    return cles


def cles_attendues(texte_exemple: str) -> set[str]:
    """
    Clés que `.env.example` attend dans tout `.env`.

    Convention de `.env.example` : une clé **active et renseignée** est
    attendue ; une clé **commentée** ou **laissée vide** est facultative
    (« laissez la ligne vide ou absente »). La valeur n'est lue que pour savoir
    si elle est vide, puis oubliée.
    """
    cles = set()
    for ligne in texte_exemple.splitlines():
        trouve = _RE_CLE_ENV.match(ligne)
        if trouve and trouve.group(2).strip().strip("\"'").strip():
            cles.add(trouve.group(1))
    return cles


def cles_manquantes(texte_exemple: str, texte_env: str) -> list[str]:
    """Noms des clés attendues absentes du fichier d'environnement, triés."""
    return sorted(cles_attendues(texte_exemple) - cles_presentes(texte_env))


# ===========================================================================
# Ce que pose install.sh — relu dans install.sh, jamais recopié ici
# ===========================================================================
def _resoudre(modele: str, variables: dict[str, str]) -> str:
    for nom, valeur in variables.items():
        modele = modele.replace("${" + nom + "}", valeur).replace("$" + nom, valeur)
    if re.search(r"\$\{?[A-Za-z_]", modele):
        raise ValueError("variable non résolue")
    return modele


def ligne_cron_attendue(install_sh: str, install_dir: str, data_dir: str) -> str:
    """
    La ligne de crontab qu'`install.sh` pose (`LIGNE_CRON="..."`), variables
    résolues. Lève ValueError si `install.sh` n'en porte plus : le contrôle le
    dira plutôt que de se taire.
    """
    trouve = re.search(r'^\s*LIGNE_CRON="(.*)"\s*$', install_sh, re.MULTILINE)
    if not trouve:
        raise ValueError("LIGNE_CRON introuvable dans install.sh")
    return _resoudre(trouve.group(1), {"INSTALL_DIR": install_dir, "DATA_DIR": data_dir})


def _compacter_cron(ligne: str) -> str:
    return " ".join(ligne.split())


def ecarts_cron(crontab: str, attendue: str) -> list[str]:
    """Écarts entre la crontab du service et la tâche de sauvegarde attendue."""
    taches = [
        ligne.strip()
        for ligne in crontab.splitlines()
        if ligne.strip() and not ligne.strip().startswith("#") and "sauvegarde.sh" in ligne
    ]
    if not taches:
        return ["aucune tâche de sauvegarde dans la crontab du service (install.sh en pose une)."]
    if len(taches) == 1 and _compacter_cron(taches[0]) == _compacter_cron(attendue):
        return []
    ecarts = ["le serveur et le dépôt diffèrent :"]
    if len(taches) > 1:
        ecarts = [f"{len(taches)} tâches de sauvegarde planifiées, une seule attendue :"]
    ecarts.extend(f"  sur le serveur : {tache}" for tache in taches)
    ecarts.append(f"  dans le dépôt  : {attendue}   (deploy/install.sh)")
    return ecarts


def paquets_attendus(install_sh: str) -> list[str]:
    """La liste `PAQUETS_BASE=( ... )` d'`install.sh`, commentaires retirés."""
    trouve = re.search(r"^\s*PAQUETS_BASE=\((.*?)\)", install_sh, re.MULTILINE | re.DOTALL)
    if not trouve:
        raise ValueError("PAQUETS_BASE introuvable dans install.sh")
    paquets = []
    for ligne in trouve.group(1).splitlines():
        paquets.extend(ligne.split("#", 1)[0].split())
    return paquets


# ===========================================================================
# Permissions
# ===========================================================================
def ecarts_droits(
    chemin: str,
    droits: tuple[int, str] | None,
    mode_attendu: int | None,
    proprietaire_attendu: str,
) -> list[str]:
    """
    Écarts entre les droits relevés d'un chemin et ceux que pose install.sh.

    `droits` vaut (mode, propriétaire) ou None si le chemin est absent ;
    `mode_attendu` à None ne vérifie que le propriétaire.
    """
    if droits is None:
        mode_texte = f" en {mode_attendu:o}" if mode_attendu is not None else ""
        return [f"{chemin} : absent (install.sh le crée{mode_texte}, propriétaire {proprietaire_attendu})."]
    mode, proprietaire = droits
    ecarts = []
    if mode_attendu is not None and mode != mode_attendu:
        ecarts.append(f"{chemin} : droits {mode:o}, attendus {mode_attendu:o}.")
    if proprietaire != proprietaire_attendu:
        ecarts.append(f"{chemin} : propriétaire {proprietaire}, attendu {proprietaire_attendu}.")
    return ecarts


def lire_droits(chemin: Path) -> tuple[int, str] | None:
    """(mode sans le type de fichier, nom du propriétaire), ou None si absent."""
    try:
        infos = chemin.stat()
    except FileNotFoundError:
        return None
    try:
        proprietaire = pwd.getpwuid(infos.st_uid).pw_name
    except KeyError:
        proprietaire = str(infos.st_uid)
    return stat.S_IMODE(infos.st_mode), proprietaire


# ===========================================================================
# Accès au serveur — la seule partie qui touche au système
# ===========================================================================
class Serveur:
    """
    Lectures sur le serveur. `racine` permet de rejouer le contrôle sur une
    copie de l'arborescence (répétition, tests) ; les commandes externes, elles,
    interrogent toujours la machine courante.
    """

    def __init__(self, racine: Path = Path("/")):
        self.racine = racine

    def chemin(self, absolu: str) -> Path:
        return self.racine / absolu.lstrip("/")

    def existe(self, absolu: str) -> bool:
        return self.chemin(absolu).exists()

    def lire(self, absolu: str) -> str:
        return self.chemin(absolu).read_text(encoding="utf-8")

    def droits(self, absolu: str) -> tuple[int, str] | None:
        return lire_droits(self.chemin(absolu))

    @staticmethod
    def _commande(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(arguments, capture_output=True, text=True, timeout=15, check=False)

    def crontab(self) -> str:
        """La crontab de l'utilisateur courant ; vide s'il n'en a pas."""
        resultat = self._commande("crontab", "-l")
        if resultat.returncode != 0:
            if "no crontab" in resultat.stderr.lower():
                return ""
            raise OSError("crontab -l a échoué")
        return resultat.stdout

    def etat_activation(self, unite: str) -> str:
        return self._commande("systemctl", "is-enabled", unite).stdout.strip() or "inconnu"

    def rechargement_attendu(self, unite: str) -> bool:
        resultat = self._commande("systemctl", "show", "--property=NeedDaemonReload", "--value", unite)
        return resultat.stdout.strip() == "yes"

    def paquet_installe(self, paquet: str) -> bool:
        resultat = self._commande("dpkg-query", "-W", "-f=${db:Status-Status}", paquet)
        return resultat.returncode == 0 and resultat.stdout.strip() == "installed"


def _formation_installee(serveur: Serveur) -> bool:
    return serveur.existe(UNITE_FORMATION) or serveur.existe(ENV_FORMATION)


def _concerne_formation(nom: str) -> bool:
    # install.sh ne pose les fichiers « -formation » que si l'on accepte
    # l'instance de formation : leur absence n'est pas un écart sans elle.
    return "formation" in nom


# ===========================================================================
# Les six familles
# ===========================================================================
def controler_unites(depot: Path, serveur: Serveur, install_dir: str) -> Constat:
    constat = Constat("Unités systemd")
    formation = _formation_installee(serveur)
    fichiers = sorted([*depot.glob("deploy/*.service"), *depot.glob("deploy/*.timer")])
    comparees = 0
    for fichier in fichiers:
        cible = f"/etc/systemd/system/{fichier.name}"
        if not serveur.existe(cible):
            if _concerne_formation(fichier.name) and not formation:
                continue
            constat.ecarts.append(f"{cible} : absent du serveur (présent dans deploy/).")
            continue
        comparees += 1
        for ecart in comparer_unite(fichier.read_text(encoding="utf-8"), serveur.lire(cible), install_dir):
            constat.ecarts.append(f"{fichier.name} — {ecart}")
        etat = serveur.etat_activation(fichier.name)
        if etat not in ETATS_ACTIVATION_ACCEPTES:
            constat.ecarts.append(f"{fichier.name} : état « {etat} », il ne redémarrera pas avec le serveur.")
        if serveur.rechargement_attendu(fichier.name):
            constat.ecarts.append(
                f"{fichier.name} : modifié sur le disque sans rechargement de systemd (systemctl daemon-reload)."
            )
    constat.detail_conforme = f"{comparees} fichier(s) comparé(s)"
    return constat


def controler_nginx(depot: Path, serveur: Serveur, install_dir: str) -> Constat:
    constat = Constat("Configuration nginx")
    formation = _formation_installee(serveur)
    comparees = 0
    for fichier in sorted(depot.glob("deploy/nginx-*.conf")):
        site = fichier.stem[len("nginx-") :]
        cible = f"/etc/nginx/sites-available/{site}"
        if not serveur.existe(cible):
            if _concerne_formation(site) and not formation:
                continue
            constat.ecarts.append(f"{cible} : absent du serveur (présent dans deploy/).")
            continue
        comparees += 1
        for ecart in comparer_nginx(fichier.read_text(encoding="utf-8"), serveur.lire(cible), install_dir):
            constat.ecarts.append(f"{site} — {ecart}")
        if not serveur.existe(f"/etc/nginx/sites-enabled/{site}"):
            constat.ecarts.append(f"/etc/nginx/sites-enabled/{site} : absent, le site n'est pas activé.")
    constat.detail_conforme = f"{comparees} fichier(s) comparé(s), domaine et lignes de certbot mis de côté"
    return constat


def controler_env(depot: Path, serveur: Serveur, install_dir: str) -> Constat:
    constat = Constat("Fichiers d'environnement")
    exemple = (depot / ".env.example").read_text(encoding="utf-8")
    fichiers = [f"{install_dir}/.env"]
    if serveur.existe(ENV_FORMATION):
        fichiers.append(ENV_FORMATION)
    for chemin in fichiers:
        try:
            texte = serveur.lire(chemin)
        except FileNotFoundError:
            constat.ecarts.append(f"{chemin} : absent.")
            continue
        except PermissionError:
            constat.ecarts.append(f"{chemin} : illisible pour cet utilisateur, non vérifié.")
            continue
        manquantes = cles_manquantes(exemple, texte)
        if manquantes:
            constat.ecarts.append(f"{chemin} : clé(s) de .env.example absente(s) : {', '.join(manquantes)}.")
    constat.detail_conforme = f"{len(fichiers)} fichier(s), clés attendues par .env.example présentes"
    return constat


def controler_cron(depot: Path, serveur: Serveur, install_dir: str, data_dir: str) -> Constat:
    constat = Constat("Tâche de sauvegarde planifiée")
    attendue = ligne_cron_attendue((depot / "deploy/install.sh").read_text(encoding="utf-8"), install_dir, data_dir)
    constat.ecarts = ecarts_cron(serveur.crontab(), attendue)
    constat.detail_conforme = "identique à celle que pose install.sh"
    return constat


def controler_paquets(depot: Path, serveur: Serveur) -> Constat:
    constat = Constat("Paquets système")
    paquets = paquets_attendus((depot / "deploy/install.sh").read_text(encoding="utf-8"))
    absents = [paquet for paquet in paquets if not serveur.paquet_installe(paquet)]
    if absents:
        constat.ecarts.append(f"non installé(s) : {' '.join(absents)} (liste PAQUETS_BASE d'install.sh).")
    constat.detail_conforme = f"{len(paquets)} paquet(s) de install.sh installé(s)"
    return constat


def controler_droits(serveur: Serveur, install_dir: str, data_dir: str, utilisateur: str) -> Constat:
    constat = Constat("Permissions posées par install.sh")
    verifications: list[tuple[str, int | None]] = [
        (data_dir, None),
        (f"{data_dir}/sauvegardes", 0o700),
        (f"{install_dir}/.env", 0o600),
    ]
    if _formation_installee(serveur):
        # Même dérivation que install.sh : « <dossier des bases>-formation ».
        verifications += [
            (f"{data_dir}-formation", None),
            (f"{data_dir}-formation/sauvegardes", 0o700),
            (ENV_FORMATION, 0o600),
        ]
    for chemin, mode in verifications:
        constat.ecarts.extend(ecarts_droits(chemin, serveur.droits(chemin), mode, utilisateur))
    constat.detail_conforme = f"{len(verifications)} chemin(s) vérifié(s)"
    return constat


# ===========================================================================
# Affichage
# ===========================================================================
def executer(depot: Path, serveur: Serveur, install_dir: str, data_dir: str, utilisateur: str) -> list[Constat]:
    """Joue les six familles. Une famille qui échoue n'arrête pas les autres."""
    familles = [
        ("Unités systemd", lambda: controler_unites(depot, serveur, install_dir)),
        ("Configuration nginx", lambda: controler_nginx(depot, serveur, install_dir)),
        ("Fichiers d'environnement", lambda: controler_env(depot, serveur, install_dir)),
        ("Tâche de sauvegarde planifiée", lambda: controler_cron(depot, serveur, install_dir, data_dir)),
        ("Paquets système", lambda: controler_paquets(depot, serveur)),
        ("Permissions posées par install.sh", lambda: controler_droits(serveur, install_dir, data_dir, utilisateur)),
    ]
    constats = []
    for titre, famille in familles:
        try:
            constats.append(famille())
        except Exception as exc:  # noqa: BLE001 - le contrôle ne doit jamais s'interrompre
            # Le type seul, jamais le message : il pourrait citer une ligne
            # d'un fichier d'environnement.
            constats.append(Constat(titre, [f"vérification impossible ({type(exc).__name__}) : ce point n'est PAS vérifié."]))
    return constats


def rendre(constats: list[Constat]) -> str:
    lignes = []
    nb_ecarts = 0
    for constat in constats:
        if not constat.ecarts:
            lignes.append(f"    -> {constat.titre} : conforme ({constat.detail_conforme}).")
            continue
        nb_ecarts += 1
        lignes.append(f"    !! {constat.titre} : à examiner")
        lignes.extend(f"         {ecart}" for ecart in constat.ecarts)
    lignes.append("")
    if nb_ecarts:
        lignes.append(f"{nb_ecarts} point(s) où le serveur et le dépôt diffèrent. Ce contrôle n'a rien modifié,")
        lignes.append("et la mise à jour elle-même est terminée. Reporter à la main, après décision :")
        lignes.append("docs/deploiement.md § 8, et les gestes propres à chaque version dans")
        lignes.append("docs/notes-de-deploiement.md.")
    else:
        lignes.append("Serveur aligné sur le dépôt, pour tout ce que ce contrôle sait vérifier.")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare le serveur au dépôt, sans rien modifier.")
    parser.add_argument("--install-dir", default=CHEMIN_EXEMPLE, help="dossier d'installation (défaut /opt/ludotex)")
    parser.add_argument("--data-dir", default="/var/lib/ludotex", help="dossier des bases (défaut /var/lib/ludotex)")
    parser.add_argument("--utilisateur", default="pretjeux", help="utilisateur du service (défaut pretjeux)")
    parser.add_argument("--racine", default="/", help="racine de l'arborescence du serveur (répétition)")
    arguments = parser.parse_args(argv)
    depot = Path(__file__).resolve().parent.parent
    constats = executer(
        depot,
        Serveur(Path(arguments.racine)),
        arguments.install_dir.rstrip("/"),
        arguments.data_dir.rstrip("/"),
        arguments.utilisateur,
    )
    print(rendre(constats))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - jamais bloquant, même en cas de bogue ici
        print(f"    !! Contrôle de report interrompu ({type(exc).__name__}) : rien n'a été vérifié.")
        sys.exit(0)
