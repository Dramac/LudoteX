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


def test_le_minuteur_tourne_chaque_nuit_a_3h_et_rattrape_un_passage_manque():
    minuteur = _directives(UNITE_MINUTEUR)
    # Même nom de base : systemd associe le minuteur au service de ce nom.
    assert UNITE_MINUTEUR.stem == UNITE_SERVICE.stem
    assert "Unit" not in minuteur["Timer"]
    # 03:xx : la preuve de fonctionnement documentée repose sur cette heure.
    assert minuteur["Timer"]["OnCalendar"] == "*-*-* 03:00:00"
    assert minuteur["Timer"]["Persistent"] == "true"
    assert minuteur["Install"]["WantedBy"] == "timers.target"


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
