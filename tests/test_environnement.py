"""
QUEL FICHIER D'ENVIRONNEMENT CHAQUE PROCESSUS LIT — ET CE QUE SYSTEMD EN JOURNALISE.

CE FICHIER EXISTE POUR DEUX CONSTATS DE L'AUDIT DE PRÉ-PRODUCTION.

`PROD-04` : l'instance de FORMATION partage le code de la production. Chaque
module appelait `load_dotenv()` sans chemin, qui trouvait le `.env` de la
production ; toute clé que le fichier de la formation ne redéfinissait pas
était héritée, en silence. Le jeton bénévole l'a été : le site d'entraînement
répondait 403 à des bénévoles qui n'avaient aucun jeton à lui donner. Le
chargement a désormais un seul domicile, `app/environnement.py`, et l'unité de
formation y désigne son propre fichier (`LUDOTEX_ENV_FILE`).

`PROD-03` : sans `--no-access-log`, uvicorn journalisait chaque requête avec sa
chaîne de requête, et systemd l'envoyait dans journald — jetons d'activation et
codes personnels du planning compris.

Les tests « processus » lancent un VRAI interpréteur sur une installation
jetable : le chargement se fait à l'import, et la façon dont python-dotenv
choisissait son fichier dépendait de la manière dont Python était lancé
(`-m` : dossier du module ; `-c` et `-` : dossier COURANT). Ce sont ces
lancements-là, ceux des scripts de `deploy/`, qu'il faut prouver.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app import environnement
from scripts import controle_report as cr

RACINE = Path(__file__).resolve().parent.parent
DEPLOY = RACINE / "deploy"
UNITES_UVICORN = ("ludotex.service", "ludotex-formation.service")


def _directives(unite: Path) -> list[tuple[str, str, str]]:
    """(section, clé, valeur) de chaque directive, lignes de continuation jointes."""
    directives, section, tampon = [], None, ""
    for brute in unite.read_text(encoding="utf-8").splitlines():
        ligne = brute.strip()
        if not tampon and (not ligne or ligne.startswith(("#", ";"))):
            continue
        if ligne.endswith("\\"):
            tampon += ligne[:-1] + " "
            continue
        ligne, tampon = tampon + ligne, ""
        if ligne.startswith("[") and ligne.endswith("]"):
            section = ligne[1:-1]
            continue
        cle, _, valeur = ligne.partition("=")
        directives.append((section, cle.strip(), valeur.strip()))
    return directives


def _valeurs(unite: str, cle: str) -> list[str]:
    return [v for s, c, v in _directives(DEPLOY / unite) if s == "Service" and c == cle]


# ===========================================================================
# 1. PROD-03 — aucune ligne d'accès d'uvicorn dans journald
# ===========================================================================
@pytest.mark.parametrize("unite", UNITES_UVICORN)
def test_les_unites_uvicorn_ne_journalisent_pas_les_requetes(unite):
    (exec_start,) = _valeurs(unite, "ExecStart")
    arguments = exec_start.split()
    assert arguments[0].endswith("/uvicorn")
    assert "--no-access-log" in arguments
    assert "--access-log" not in arguments
    # Invariant déclaré dans les unités : un seul worker (sessions, limite de
    # débit et journal d'activité en mémoire d'un seul processus).
    assert not any(a.startswith("--workers") for a in arguments)


def test_la_console_du_journal_d_activite_reste_coupee_sur_le_serveur():
    """
    Le journal d'activité peut se recopier sur la console d'uvicorn, donc dans
    journald. Il ne porte ni URL ni chaîne de requête (vocabulaire fermé), mais
    les deux fichiers générés par install.sh la gardent coupée : rien de ce
    que ce lot retire ne doit revenir par là.
    """
    for gabarit in (_gabarit_env("ENV_FILE"), _gabarit_env("ENV_FORMATION")):
        assert re.search(r"^JOURNAL_CONSOLE=0$", gabarit, re.MULTILINE)


# ===========================================================================
# 2. PROD-04 — la règle, en mémoire
# ===========================================================================
def test_sans_variable_c_est_le_env_de_la_racine_du_code(monkeypatch):
    monkeypatch.delenv(environnement.VARIABLE_FICHIER_ENV, raising=False)
    assert environnement.fichier_env() == RACINE / ".env"


def test_la_variable_designe_le_fichier_de_l_instance(monkeypatch, tmp_path):
    monkeypatch.setenv(environnement.VARIABLE_FICHIER_ENV, str(tmp_path / "formation.env"))
    assert environnement.fichier_env() == tmp_path / "formation.env"


@pytest.mark.parametrize("valeur", ["", "   "])
def test_la_variable_vide_ne_charge_aucun_fichier(monkeypatch, valeur):
    monkeypatch.setenv(environnement.VARIABLE_FICHIER_ENV, valeur)
    assert environnement.fichier_env() is None
    assert environnement.charger_env() is None


def test_un_fichier_designe_mais_absent_ne_retombe_pas_sur_le_env_de_la_racine(monkeypatch, tmp_path):
    monkeypatch.setenv(environnement.VARIABLE_FICHIER_ENV, str(tmp_path / "absent.env"))
    assert environnement.charger_env() is None


def test_une_variable_deja_posee_n_est_jamais_ecrasee(monkeypatch, tmp_path):
    fichier = tmp_path / "instance.env"
    fichier.write_text("CLE_TEMOIN_ENV=du-fichier\n", encoding="utf-8")
    monkeypatch.setenv(environnement.VARIABLE_FICHIER_ENV, str(fichier))
    monkeypatch.setenv("CLE_TEMOIN_ENV", "de-systemd")
    assert environnement.charger_env() == fichier
    assert os.environ["CLE_TEMOIN_ENV"] == "de-systemd"


def test_un_seul_domicile_pour_le_chargement():
    """Aucun `load_dotenv` hors de `app/environnement.py`, sauf le `--env` du script du lot 6."""
    appels = []
    for fichier in [*RACINE.glob("app/**/*.py"), *RACINE.glob("scripts/**/*.py"), RACINE / "lancer.py"]:
        for numero, ligne in enumerate(fichier.read_text(encoding="utf-8").splitlines(), 1):
            # Une instruction qui COMMENCE par l'appel : les docstrings qui
            # racontent l'ancien `load_dotenv()` ne comptent pas.
            if re.match(r"\s*(?:\w+\.)?load_dotenv\s*\(", ligne):
                appels.append(f"{fichier.relative_to(RACINE)}:{numero}")
    assert sorted(appels) == sorted([
        next(f"app/environnement.py:{n}" for n, l in _lignes("app/environnement.py") if "load_dotenv(chemin" in l),
        next(f"scripts/reinitialiser_mot_de_passe.py:{n}" for n, l in _lignes("scripts/reinitialiser_mot_de_passe.py")
             if "load_dotenv(chemin" in l),
    ])


def _lignes(relatif: str):
    return enumerate((RACINE / relatif).read_text(encoding="utf-8").splitlines(), 1)


# ===========================================================================
# 3. PROD-04 — en processus réels, sur une installation jetable
# ===========================================================================
# Clé présente SEULEMENT dans le .env de la production : la formation ne doit
# jamais la voir. Et un .env leurre dans le dossier courant : aucun lancement
# ne doit plus le lire.
TEMOIN = "CLE_TEMOIN_PROD04"


@pytest.fixture
def installation(tmp_path):
    """Code lié (comme les tests de sauvegarde.sh), deux instances, un leurre."""
    inst = tmp_path / "installation"
    (inst / "scripts").mkdir(parents=True)
    os.symlink(RACINE / "app", inst / "app")
    # Copié, pas lié : le script calcule sa racine avec resolve(), qui suivrait
    # un lien jusqu'au vrai dépôt (et à son .env de développement).
    for script in ("reinitialiser_mot_de_passe.py", "journal.py", "__init__.py"):
        source = RACINE / "scripts" / script
        if source.exists():
            (inst / "scripts" / script).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    prod, form = tmp_path / "prod", tmp_path / "formation"
    for dossier in (prod, form):
        dossier.mkdir()
    (inst / ".env").write_text(
        f"PRET_TOKEN=jeton-de-la-production-assez-long\n"
        f"{TEMOIN}=production\n"
        f"DATABASE_PATH={prod / 'pret-jeux.db'}\n"
        f"TOURNOI_DATABASE_PATH={prod / 'tournoi.db'}\n"
        f"PLANNING_DATABASE_PATH={prod / 'planning.db'}\n"
        f"JOURNAL_PATH={prod / 'journal.log'}\n",
        encoding="utf-8",
    )
    fichier_formation = tmp_path / "ludotex-formation.env"
    fichier_formation.write_text(
        "PRET_TOKEN=\n"
        "MODE_FORMATION=1\n"
        f'DATABASE_PATH="{form / "pret-jeux.db"}"\n'
        f'TOURNOI_DATABASE_PATH="{form / "tournoi.db"}"\n'
        f'PLANNING_DATABASE_PATH="{form / "planning.db"}"\n'
        f'JOURNAL_PATH="{form / "journal.log"}"\n',
        encoding="utf-8",
    )
    ailleurs = tmp_path / "ailleurs"
    ailleurs.mkdir()
    (ailleurs / ".env").write_text(
        f"{TEMOIN}=dossier-courant\nDATABASE_PATH={tmp_path / 'leurre.db'}\n", encoding="utf-8"
    )
    return {"inst": inst, "prod": prod, "form": form, "env_formation": fichier_formation,
            "ailleurs": ailleurs, "tmp": tmp_path}


def _python(installation, *arguments, cwd, formation=False, entree=None, env_extra=None):
    # Environnement vierge, comme sous systemd ou sudo : seuls les fichiers décident.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(installation["tmp"]),
           "PYTHONPATH": str(installation["inst"]), "PYTHONDONTWRITEBYTECODE": "1"}
    if formation:
        env[environnement.VARIABLE_FICHIER_ENV] = str(installation["env_formation"])
    env.update(env_extra or {})
    resultat = subprocess.run(
        [sys.executable, *arguments], cwd=cwd, env=env, input=entree,
        capture_output=True, text=True, timeout=120,
    )
    assert resultat.returncode == 0, resultat.stderr
    return resultat.stdout


LIRE = (
    "import os; from app import config, db; from app.tournoi import db as tdb; "
    "from app.planning import db as pdb; "
    f"print(os.environ.get('{TEMOIN}'), db.get_database_path(), tdb.get_database_path(), "
    "pdb.get_database_path(), repr(os.environ.get('PRET_TOKEN')))"
)


@pytest.mark.parametrize("dossier", ["inst", "ailleurs"])
def test_la_production_lit_son_env_quel_que_soit_le_dossier_courant(installation, dossier):
    """`python -c`, la forme d'update.sh et de sauvegarde.sh : lisait le dossier COURANT."""
    sortie = _python(installation, "-c", LIRE, cwd=installation[dossier]).split()
    prod = installation["prod"]
    assert sortie == ["production", str(prod / "pret-jeux.db"), str(prod / "tournoi.db"),
                      str(prod / "planning.db"), "'jeton-de-la-production-assez-long'"]


@pytest.mark.parametrize("dossier", ["inst", "ailleurs"])
def test_la_formation_ne_voit_aucune_cle_du_env_de_production(installation, dossier):
    sortie = _python(installation, "-c", LIRE, cwd=installation[dossier], formation=True).split()
    form = installation["form"]
    assert sortie == ["None", str(form / "pret-jeux.db"), str(form / "tournoi.db"),
                      str(form / "planning.db"), "''"]


SCANNER = (
    "from fastapi.testclient import TestClient; from app.main import app; "
    "print(TestClient(app).get('/scanner', follow_redirects=False).status_code)"
)


def test_le_scanner_de_la_formation_est_ouvert_celui_de_la_production_ferme(installation):
    """Le symptôme de l'audit : 403 sur le site d'entraînement."""
    assert _python(installation, "-c", SCANNER, cwd=installation["inst"], formation=True).strip() == "200"
    # La formation n'a écrit que dans ses propres bases.
    assert (installation["form"] / "pret-jeux.db").exists()
    assert not (installation["prod"] / "pret-jeux.db").exists()
    assert _python(installation, "-c", SCANNER, cwd=installation["inst"]).strip() == "403"


def test_l_ancienne_formation_sans_la_variable_herite_encore_de_la_production(installation):
    """
    Tant que l'unité installée n'a pas la ligne `LUDOTEX_ENV_FILE` (geste de
    report), rien ne change : systemd injecte son fichier et le .env de
    production complète. C'est le comportement d'avant, sans casse.
    """
    valeurs = {}
    for ligne in installation["env_formation"].read_text(encoding="utf-8").splitlines():
        cle, _, valeur = ligne.partition("=")
        valeurs[cle] = valeur.strip('"')
    sortie = _python(installation, "-c", LIRE, cwd=installation["inst"], env_extra=valeurs).split()
    assert sortie[0] == "production"                       # héritée : le piège d'avant
    assert sortie[1] == str(installation["form"] / "pret-jeux.db")   # mais les bases restent les siennes


def test_le_script_du_lot_6_vise_la_formation_sans_relire_la_production(installation):
    script = str(installation["inst"] / "scripts" / "reinitialiser_mot_de_passe.py")
    # Les bases doivent exister : le script refuse de créer une base fantôme.
    _python(installation, "-m", "app.db", cwd=installation["inst"])
    _python(installation, "-m", "app.db", cwd=installation["inst"], formation=True)

    sortie = _python(installation, script, "--env", str(installation["env_formation"]),
                     "--stdin", cwd=installation["ailleurs"], entree="mot-de-passe-formation\n")
    assert str(installation["form"] / "pret-jeux.db") in sortie
    sortie = _python(installation, script, "--stdin", cwd=installation["ailleurs"],
                     entree="mot-de-passe-production\n")
    assert str(installation["prod"] / "pret-jeux.db") in sortie
    assert "leurre" not in sortie and not (installation["tmp"] / "leurre.db").exists()


def test_scripts_journal_lit_le_journal_de_l_instance(installation):
    code = ("import sys; sys.argv=['journal']; from scripts.journal import chemin_journal; "
            "print(chemin_journal())")
    assert _python(installation, "-c", code, cwd=installation["ailleurs"]).strip() == \
        str(installation["prod"] / "journal.log")
    assert _python(installation, "-c", code, cwd=installation["ailleurs"], formation=True).strip() == \
        str(installation["form"] / "journal.log")


# ===========================================================================
# 4. Les dérivations des scripts de deploy/, relues dans les scripts
# ===========================================================================
def _commande_python_c(script: str, variable: str) -> str:
    """Le code Python passé en `-c` pour calculer `variable` dans un script de deploy/."""
    texte = (DEPLOY / script).read_text(encoding="utf-8")
    trouve = re.search(variable + r"=\"\$\(cd \"\$INSTALL_DIR\" && sudo -u \"\$SERVICE_USER\" [^\n]*?-c \\?\n?\s*'([^']+)'",
                       texte)
    assert trouve, f"dérivation de {variable} introuvable dans {script}"
    return trouve.group(1)


@pytest.mark.parametrize("script", ["update.sh", "install.sh"])
def test_la_derivation_du_dossier_des_bases_atteint_la_production(installation, script):
    code = _commande_python_c(script, "DATA_DIR")
    # Les deux scripts font `cd "$INSTALL_DIR"` d'abord ; même depuis ailleurs,
    # le .env lu est désormais celui de l'installation.
    for dossier in ("inst", "ailleurs"):
        sortie = _python(installation, "-c", code, cwd=installation[dossier]).strip()
        assert Path(sortie).resolve() == installation["prod"].resolve()


def test_sauvegarde_sh_derive_toujours_sa_destination_du_env():
    """La dérivation elle-même est exécutée par tests/test_sauvegarde_planifiee.py."""
    texte = (DEPLOY / "sauvegarde.sh").read_text(encoding="utf-8")
    assert "from app.db import get_database_path" in texte
    assert 'cd "$INSTALL_DIR"' in texte


# ===========================================================================
# 5. Les fichiers générés par install.sh, face au contrôle de report
# ===========================================================================
def _gabarit_env(variable: str) -> str:
    """Le texte que `cat > "$<variable>" <<EOF` écrit dans install.sh (variables non développées)."""
    texte = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    trouve = re.search(r'cat > "\$' + variable + r'" <<EOF\n(.*?)\nEOF\n', texte, re.DOTALL)
    assert trouve, f"génération de ${variable} introuvable dans install.sh"
    return trouve.group(1)


@pytest.mark.parametrize("variable", ["ENV_FILE", "ENV_FORMATION"])
def test_un_fichier_genere_par_install_sh_est_conforme_au_controle_de_report(variable):
    """
    La formation ne reçoit plus rien du .env de production : son fichier doit
    porter lui-même TOUTES les clés attendues par .env.example. Une clé ajoutée
    à .env.example sans être reportée dans install.sh fait tomber ce test.
    """
    exemple = (RACINE / ".env.example").read_text(encoding="utf-8")
    assert cr.cles_manquantes(exemple, _gabarit_env(variable)) == []


def test_la_formation_installee_reste_ouverte():
    gabarit = _gabarit_env("ENV_FORMATION")
    # Présente (le contrôle ne la signale pas) et vide (aucun jeton exigé).
    assert re.search(r"^PRET_TOKEN=$", gabarit, re.MULTILINE)
    assert re.search(r"^MODE_FORMATION=1$", gabarit, re.MULTILINE)


def test_le_controle_est_conforme_sur_une_formation_correctement_installee(tmp_path):
    # Cette famille ne fait que lire des fichiers : l'arborescence suffit.
    serveur = cr.Serveur(tmp_path / "srv")
    for absolu, variable in (("/opt/ludotex/.env", "ENV_FILE"), ("/etc/ludotex-formation.env", "ENV_FORMATION")):
        chemin = serveur.chemin(absolu)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(_gabarit_env(variable), encoding="utf-8")
    constat = cr.controler_env(RACINE, serveur, "/opt/ludotex")
    assert constat.ecarts == []
    assert "2 fichier(s)" in constat.detail_conforme


def test_l_unite_de_formation_designe_le_fichier_qu_elle_injecte():
    (fichier,) = _valeurs("ludotex-formation.service", "EnvironmentFile")
    assert _valeurs("ludotex-formation.service", "Environment") == [
        f"{environnement.VARIABLE_FICHIER_ENV}={fichier}"
    ]
    # Et c'est bien ce fichier qu'install.sh écrit et donne à ses initialisations.
    install_sh = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert f'ENV_FORMATION="{fichier}"' in install_sh
    assert 'env LUDOTEX_ENV_FILE="$ENV_FORMATION"' in install_sh


def test_la_production_ne_designe_aucun_fichier():
    """Sans variable, le .env de la racine : l'unité de production n'en pose pas."""
    assert _valeurs("ludotex.service", "Environment") == []
    assert _valeurs("ludotex.service", "EnvironmentFile") == []
