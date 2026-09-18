"""
LA SAUVEGARDE DE NUIT DOIT TOURNER POUR DE VRAI — ET SA SORTIE NE VA JAMAIS DANS /var/log/.

CE TEST EXISTE PARCE QUE LA SAUVEGARDE QUOTIDIENNE N'A JAMAIS PRODUIT UNE SEULE
ARCHIVE en sept semaines de production. La ligne de cron posée par
`deploy/install.sh` redirigeait sa sortie vers `/var/log/…` ; l'utilisateur du
service ne peut pas y créer de fichier, la redirection échouait avant même le
lancement du script, et l'erreur partait dans un courrier que personne ne
recevait. `crontab -l` affichait pendant ce temps une tâche en apparence saine.

La sauvegarde est désormais un minuteur systemd (`deploy/ludotex-sauvegarde.*`).
Ce fichier verrouille :

1. **la classe du défaut**, dans tout le dépôt et dans le wiki s'il est présent :
   aucune redirection de sortie vers un chemin sous `/var/log/`. Dans ce
   projet, une commande documentée tourne sous l'utilisateur du service ou dans
   un shell non root (`sudo cmd >> fichier` : la redirection, elle, est faite
   par le shell de l'appelant). Un exemple recopié d'une documentation suffirait
   à faire revenir le constat ;
2. **les unités** : elles lancent bien `sauvegarde.sh`, sous l'utilisateur du
   service, avec les réglages dont dépend la preuve de fonctionnement ;
3. **la destination** que `sauvegarde.sh` dérive seul du `.env` quand on ne la
   lui donne pas — c'est ainsi que l'unité l'appelle.

`deploy/install.sh` et `deploy/update.sh` ne sont pas couverts par pytest : le
bloc qui remplace l'ancienne ligne de cron a été éprouvé par une exécution
réelle sur une crontab simulée, consignée au compte rendu du lot.
`systemd-analyze verify` n'a pas été lancé sur ces unités (indisponible sur
macOS, et aucune écriture n'est autorisée sur le serveur).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
WIKI = RACINE / "wiki"

UNITE_SERVICE = RACINE / "deploy" / "ludotex-sauvegarde.service"
UNITE_MINUTEUR = RACINE / "deploy" / "ludotex-sauvegarde.timer"

# Une redirection shell (`>` ou `>>`, éventuellement précédée d'un numéro de
# descripteur) vers /var/log/, ou une sortie systemd envoyée dans un fichier de
# /var/log/. Lire un fichier de /var/log/ (`grep … /var/log/nginx/access.log`)
# ou le désigner à nginx (`access_log /var/log/nginx/…`, ouvert par le maître
# root) n'est PAS visé.
REDIRECTION_VAR_LOG = re.compile(
    r"(?:[0-9&]?>{1,2}\s*[\"']?/var/log/)"
    r"|(?:Standard(?:Output|Error)\s*=\s*(?:file|append|truncate):/var/log/)"
)


def _fichiers_suivis(depot: Path) -> list[Path]:
    sortie = subprocess.run(["git", "ls-files"], cwd=depot, capture_output=True, text=True, check=True)
    return [depot / chemin for chemin in sortie.stdout.splitlines()]


def _redirections_vers_var_log(depot: Path) -> list[str]:
    trouvailles = []
    for fichier in _fichiers_suivis(depot):
        try:
            contenu = fichier.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for numero, ligne in enumerate(contenu.splitlines(), start=1):
            if REDIRECTION_VAR_LOG.search(ligne):
                trouvailles.append(f"{fichier.relative_to(depot)}:{numero}: {ligne.strip()}")
    return trouvailles


# ===========================================================================
# 1. La classe du défaut
# ===========================================================================
def test_le_motif_attrape_la_ligne_qui_a_casse_la_production_et_rien_d_autre():
    # Exemples écrits par morceaux : d'un bloc, ils seraient eux-mêmes des
    # trouvailles du test suivant, qui parcourt aussi ce fichier.
    cassee = "0 3 * * * /opt/ludotex/deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes " + ">>" + " /var/log/ludotex-sauvegarde.log 2>&1"
    for texte in (
        cassee,
        "sudo -u pretjeux ./deploy/sauvegarde.sh 2>" + "/var/log/x.log",
        "StandardOutput=" + "append:/var/log/ludotex-sauvegarde.log",
        "echo ok >" + ' "/var/log/x"',
    ):
        assert REDIRECTION_VAR_LOG.search(texte), texte
    for texte in (
        'sudo grep "jeton=" /var/log/nginx/access.log   # ne doit rien renvoyer',
        "access_log /var/log/nginx/access.log ludotex_combined if=$ludotex_pas_secrete;",
        "0 3 * * * /opt/ludotex/deploy/sauvegarde.sh /opt/ludotex >> /var/lib/ludotex/sauvegarde.log 2>&1",
        "# ne jamais la rediriger vers /var/log/, où l'utilisateur du service ne peut pas écrire",
    ):
        assert not REDIRECTION_VAR_LOG.search(texte), texte


def test_aucune_redirection_vers_var_log_dans_le_depot():
    trouvailles = _redirections_vers_var_log(RACINE)
    assert not trouvailles, (
        "sortie redirigée vers /var/log/ : l'utilisateur du service ne peut pas y créer "
        "de fichier, la commande échouera sans bruit :\n  " + "\n  ".join(trouvailles)
    )


def test_aucune_redirection_vers_var_log_dans_le_wiki():
    if not (WIKI / ".git").exists():
        pytest.skip("wiki/ absent : dépôt séparé, non cloné ici")
    trouvailles = _redirections_vers_var_log(WIKI)
    assert not trouvailles, "sortie redirigée vers /var/log/ dans le wiki :\n  " + "\n  ".join(trouvailles)


# ===========================================================================
# 2. Les unités
# ===========================================================================
def _directives(unite: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    courante = None
    for brute in unite.read_text(encoding="utf-8").splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith(("#", ";")):
            continue
        if ligne.startswith("[") and ligne.endswith("]"):
            courante = sections.setdefault(ligne[1:-1], {})
            continue
        cle, _, valeur = ligne.partition("=")
        assert courante is not None, f"directive hors section dans {unite.name} : {ligne}"
        courante[cle.strip()] = valeur.strip()
    return sections


def _utilisateur_du_service() -> str:
    install_sh = (RACINE / "deploy" / "install.sh").read_text(encoding="utf-8")
    trouve = re.search(r'^SERVICE_USER="([^"]+)"', install_sh, re.MULTILINE)
    assert trouve, "SERVICE_USER introuvable dans deploy/install.sh"
    return trouve.group(1)


def test_le_service_lance_sauvegarde_sh_sous_l_utilisateur_du_service():
    service = _directives(UNITE_SERVICE)["Service"]
    utilisateur = _utilisateur_du_service()
    assert service["Type"] == "oneshot"
    assert service["User"] == utilisateur and service["Group"] == utilisateur
    # Même utilisateur que l'application : c'est lui qui possède les bases.
    assert _directives(RACINE / "deploy" / "ludotex.service")["Service"]["User"] == utilisateur

    # Le chemin d'exemple /opt/ludotex, que install.sh substitue, et lui seul :
    # le script, puis le dossier d'installation en argument. PAS de dossier de
    # destination écrit ici : sauvegarde.sh le dérive du .env (voir section 3),
    # sans quoi l'unité divergerait de DATABASE_PATH.
    assert service["WorkingDirectory"] == "/opt/ludotex"
    assert service["ExecStart"].split() == ["/opt/ludotex/deploy/sauvegarde.sh", "/opt/ludotex"]
    assert (RACINE / "deploy" / "sauvegarde.sh").stat().st_mode & 0o111, "sauvegarde.sh doit rester exécutable"


def test_le_service_n_a_pas_de_section_install():
    # C'est le minuteur qui le déclenche. Avec une section [Install] jamais
    # activée, `systemctl is-enabled` répondrait « disabled » et le contrôle de
    # report le signalerait à chaque mise à jour ; sans elle, « static ».
    assert "Install" not in _directives(UNITE_SERVICE)


def _heures_de_passage() -> list[int]:
    """Les heures du minuteur, lues dans `OnCalendar=*-*-* HH[,HH…]:00:00`."""
    calendrier = _directives(UNITE_MINUTEUR)["Timer"]["OnCalendar"]
    trouve = re.fullmatch(r"\*-\*-\* ([0-9]{2}(?:,[0-9]{2})*):00:00", calendrier)
    assert trouve, f"forme inattendue : {calendrier}"
    return [int(h) for h in trouve.group(1).split(",")]


def test_le_minuteur_tourne_a_3h_et_a_15h_et_rattrape_un_passage_manque():
    minuteur = _directives(UNITE_MINUTEUR)
    # Même nom de base : systemd associe le minuteur au service de ce nom.
    assert UNITE_MINUTEUR.stem == UNITE_SERVICE.stem
    assert "Unit" not in minuteur["Timer"]
    # Deux passages par jour, tous les jours (décision du 2026-09-13) : la
    # syntaxe « 03,15 » a été vérifiée par systemd-analyze calendar sur la
    # production, qui la normalise telle quelle.
    assert minuteur["Timer"]["OnCalendar"] == "*-*-* 03,15:00:00"
    assert _heures_de_passage() == [3, 15]
    assert minuteur["Timer"]["Persistent"] == "true"
    assert minuteur["Install"]["WantedBy"] == "timers.target"


def test_la_rotation_garde_un_mois_au_rythme_du_minuteur():
    """Un passage de plus par jour, autant d'archives de plus : même profondeur en jours."""
    from app import sauvegarde

    assert sauvegarde.GARDER_ROUTINES == 30 * len(_heures_de_passage())


def test_le_seuil_de_supervision_voit_un_seul_passage_manque():
    """
    Le seuil dépasse le plus long écart entre deux passages (plus une heure :
    un changement d'heure sur un serveur qui ne serait pas en UTC), mais reste
    sous deux écarts : un seul passage manqué suffit à allumer le voyant.
    """
    from datetime import timedelta

    from app import supervision

    heures = _heures_de_passage()
    ecart = max((heures[(i + 1) % len(heures)] - h) % 24 or 24 for i, h in enumerate(heures))
    assert ecart == 12
    assert timedelta(hours=ecart + 1) < supervision.SEUIL_AGE_SAUVEGARDE < timedelta(hours=2 * ecart)


def test_install_sh_pose_les_deux_unites_et_active_le_minuteur_immediatement():
    install_sh = (RACINE / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert "for UNITE in ludotex-sauvegarde.service ludotex-sauvegarde.timer" in install_sh
    # --now : activé sans lui, le minuteur ne tournerait qu'après un redémarrage.
    assert "systemctl enable --now ludotex-sauvegarde.timer" in install_sh
    assert "retirer_ancienne_tache_cron" in install_sh


# ===========================================================================
# 3. La destination dérivée du .env
# ===========================================================================
def _python_de_l_installation(installation: Path) -> None:
    # Un relais plutôt qu'un lien : un lien vers l'interpréteur perdrait
    # l'environnement virtuel (et ses dépendances) de la suite de tests.
    python = installation / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponible")
def test_sauvegarde_sh_sans_destination_ecrit_a_cote_de_la_base_de_pret(tmp_path):
    installation = tmp_path / "installation"
    donnees = tmp_path / "donnees"
    (installation / "deploy").mkdir(parents=True)
    donnees.mkdir()
    shutil.copy2(RACINE / "deploy" / "sauvegarde.sh", installation / "deploy" / "sauvegarde.sh")
    os.symlink(RACINE / "app", installation / "app")
    _python_de_l_installation(installation)
    (installation / ".env").write_text(
        f"DATABASE_PATH={donnees / 'pret-jeux.db'}\n"
        f"TOURNOI_DATABASE_PATH={donnees / 'tournoi.db'}\n"
        f"PLANNING_DATABASE_PATH={donnees / 'planning.db'}\n"
        f"JOURNAL_PATH={donnees / 'journal.log'}\n",
        encoding="utf-8",
    )
    # Environnement vierge : seul le .env de l'installation décide, comme sous systemd.
    environnement = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)}

    resultat = subprocess.run(
        ["bash", str(installation / "deploy" / "sauvegarde.sh"), str(installation)],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert resultat.returncode == 0, resultat.stderr
    archives = sorted((donnees / "sauvegardes").glob("ludotex-backup-*.zip"))
    assert len(archives) == 1
    assert resultat.stdout.strip() == f"Sauvegarde créée : {archives[0].resolve()}"
    # Même format qu'avant : les trois bases, restaurables depuis l'administration.
    with zipfile.ZipFile(archives[0]) as archive:
        assert {"pret-jeux.db", "tournoi.db", "planning.db"} <= set(archive.namelist())
    # Rien n'a été posé dans le dossier d'installation (ancien défaut du 2e argument).
    assert not (installation / "sauvegardes").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponible")
def test_sauvegarde_sh_dit_clairement_quand_la_destination_est_introuvable(tmp_path):
    installation = tmp_path / "installation"
    (installation / "deploy").mkdir(parents=True)
    shutil.copy2(RACINE / "deploy" / "sauvegarde.sh", installation / "deploy" / "sauvegarde.sh")
    _python_de_l_installation(installation)
    # Pas de paquet `app` : la lecture de DATABASE_PATH échoue.
    resultat = subprocess.run(
        ["bash", str(installation / "deploy" / "sauvegarde.sh"), str(installation)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resultat.returncode == 1
    assert "dossier des sauvegardes introuvable" in resultat.stderr


# ===========================================================================
# 4. La nature de l'archive, choisie par l'appelant, et la purge de chaque nature
# ===========================================================================
# Le minuteur de nuit n'en passe pas : routine. `update.sh` demande
# « avant-mise-a-jour », pour que la supervision ne prenne pas son filet pour la
# sauvegarde de nuit (PROD-02). Les noms et les règles de fin de vie vivent dans
# app/sauvegarde.py ; ici, on vérifie que le script les applique réellement.
def _installation_jetable(tmp_path: Path) -> tuple[Path, Path]:
    installation = tmp_path / "installation"
    donnees = tmp_path / "donnees"
    (installation / "deploy").mkdir(parents=True)
    donnees.mkdir()
    shutil.copy2(RACINE / "deploy" / "sauvegarde.sh", installation / "deploy" / "sauvegarde.sh")
    os.symlink(RACINE / "app", installation / "app")
    _python_de_l_installation(installation)
    (installation / ".env").write_text(
        f"DATABASE_PATH={donnees / 'pret-jeux.db'}\n"
        f"TOURNOI_DATABASE_PATH={donnees / 'tournoi.db'}\n"
        f"PLANNING_DATABASE_PATH={donnees / 'planning.db'}\n"
        f"JOURNAL_PATH={donnees / 'journal.log'}\n",
        encoding="utf-8",
    )
    return installation, donnees


def _lancer_sauvegarde(installation: Path, tmp_path: Path, *arguments: str):
    return subprocess.run(
        ["bash", str(installation / "deploy" / "sauvegarde.sh"), str(installation), *arguments],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponible")
def test_sauvegarde_sh_nature_avant_mise_a_jour_dans_un_dossier_vide(tmp_path):
    """Dossier sans aucune archive de routine : la rotation ne doit pas faire
    échouer le script (un « ls ludotex-backup-*.zip » sans correspondance, sous
    pipefail, l'aurait fait après avoir créé l'archive)."""
    from app import sauvegarde

    installation, donnees = _installation_jetable(tmp_path)
    destination = donnees / "sauvegardes"

    resultat = _lancer_sauvegarde(installation, tmp_path, str(destination), "avant-mise-a-jour")

    assert resultat.returncode == 0, resultat.stderr
    archives = sorted(p.name for p in destination.iterdir())
    assert len(archives) == 1
    assert sauvegarde.nature_archive(archives[0]) == sauvegarde.NATURE_AVANT_MISE_A_JOUR
    assert resultat.stdout.strip() == f"Sauvegarde créée : {destination / archives[0]}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponible")
def test_sauvegarde_sh_sans_nature_fait_une_routine_et_purge_chaque_nature(tmp_path):
    import time

    from app import sauvegarde

    installation, donnees = _installation_jetable(tmp_path)
    destination = donnees / "sauvegardes"
    destination.mkdir()
    vieux = time.time() - (sauvegarde.GARDER_JOURS_FILETS + 2) * 86400
    for nom in ("avant-mise-a-jour-20260701-120000.zip", "avant-restauration-20260701-120000.zip"):
        (destination / nom).write_bytes(b"zip")
        os.utime(destination / nom, (vieux, vieux))
    for i in range(sauvegarde.GARDER_ROUTINES):
        nom = destination / f"ludotex-backup-20260801-{i:06d}.zip"
        nom.write_bytes(b"zip")
        os.utime(nom, (vieux - i, vieux - i))
    plus_ancienne = f"ludotex-backup-20260801-{sauvegarde.GARDER_ROUTINES - 1:06d}.zip"
    (destination / "notes.txt").write_text("à garder")

    resultat = _lancer_sauvegarde(installation, tmp_path)

    assert resultat.returncode == 0, resultat.stderr
    restantes = sauvegarde.lister_archives(destination)
    assert {a["nature"] for a in restantes} == {sauvegarde.NATURE_ROUTINE}
    assert len(restantes) == sauvegarde.GARDER_ROUTINES
    assert restantes[0]["nom"] == Path(resultat.stdout.split(" : ", 1)[1].strip()).name
    assert not (destination / plus_ancienne).exists()
    assert (destination / "notes.txt").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponible")
def test_sauvegarde_sh_refuse_une_nature_inconnue_sans_rien_ecrire(tmp_path):
    installation, donnees = _installation_jetable(tmp_path)
    destination = donnees / "sauvegardes"

    resultat = _lancer_sauvegarde(installation, tmp_path, str(destination), "hebdomadaire")

    assert resultat.returncode == 1
    assert "ERREUR : nature de sauvegarde inconnue « hebdomadaire »" in resultat.stderr
    assert "Sauvegarde créée" not in resultat.stdout
    assert not destination.exists()


def test_update_sh_demande_un_filet_avant_mise_a_jour():
    """Lu dans le texte : update.sh n'est pas exécutable hors d'un serveur."""
    from app import sauvegarde

    update_sh = (RACINE / "deploy" / "update.sh").read_text(encoding="utf-8")
    appels = [l for l in update_sh.splitlines()
              if "deploy/sauvegarde.sh" in l and not l.lstrip().startswith("#")]
    assert len(appels) == 1
    assert appels[0].rstrip().endswith(f'"$DATA_DIR/sauvegardes" {sauvegarde.NATURE_AVANT_MISE_A_JOUR}')


def test_l_unite_de_nuit_ne_passe_pas_de_nature():
    """Sans 3e argument, sauvegarde.sh fait une routine : c'est ce que la
    supervision compte comme preuve que la sauvegarde de nuit tourne."""
    unite = (RACINE / "deploy" / "ludotex-sauvegarde.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/ludotex/deploy/sauvegarde.sh /opt/ludotex\n" in unite


def test_l_aide_du_bureau_cite_le_seuil_en_vigueur():
    """L'aide écrit le seuil en toutes lettres : elle a déjà dit « 26 heures » après coup."""
    from app import supervision

    heures = int(supervision.SEUIL_AGE_SAUVEGARDE.total_seconds() // 3600)
    aide = (RACINE / "app" / "templates" / "admin_aide.html").read_text(encoding="utf-8")
    assert re.search(rf"depuis plus de {heures} heures", aide)
    assert not re.search(r"depuis plus de (?!" + str(heures) + r"\b)\d+ heures", aide)
