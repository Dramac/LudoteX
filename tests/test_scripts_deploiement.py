"""
Garde-fous de lecture sur les scripts de `deploy/`.

Ces scripts ne tournent qu'en root, sur un serveur : la suite ne les exécute
pas. Ce qu'elle peut vérifier sans les lancer, elle le vérifie ici.

Bash lit un script de haut en bas et ne connaît une fonction qu'une fois sa
définition passée. Une fonction appelée plus haut que sa définition donne
« command not found », et sous `set -e` le script s'arrête là. `bash -n` ne le
voit pas : la syntaxe est valide. C'est arrivé à `install.sh`, dont l'étape
nginx appelait `garder_https` trente lignes avant de la définir — toute
installation neuve s'arrêtait à l'étape 7.
"""
import re
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
SCRIPTS = sorted(p.name for p in DEPLOY.glob("*.sh"))

_DEFINITION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")


def _lignes_de_code(script: str) -> list[tuple[int, str]]:
    """(numéro, ligne) des lignes qui ne sont pas des commentaires entiers."""
    texte = (DEPLOY / script).read_text(encoding="utf-8")
    return [(n, ligne) for n, ligne in enumerate(texte.splitlines(), start=1)
            if not ligne.lstrip().startswith("#")]


def fonctions_utilisees_trop_tot(lignes: list[tuple[int, str]]) -> list[str]:
    """Les fonctions dont le nom apparaît dans une ligne de code avant leur définition."""
    definitions: dict[str, int] = {}
    for numero, ligne in lignes:
        trouve = _DEFINITION.match(ligne)
        if trouve:
            definitions.setdefault(trouve.group(1), numero)
    ecarts = []
    for nom, defini_en in definitions.items():
        mot = re.compile(rf"(?<![\w-]){re.escape(nom)}(?![\w-])")
        for numero, ligne in lignes:
            if numero >= defini_en:
                break
            if mot.search(ligne):
                ecarts.append(f"{nom} : utilisée ligne {numero}, définie ligne {defini_en}")
                break
    return ecarts


def test_des_scripts_sont_bien_trouves():
    assert {"install.sh", "update.sh", "sauvegarde.sh"} <= set(SCRIPTS)


@pytest.mark.parametrize("script", SCRIPTS)
def test_aucune_fonction_n_est_appelee_avant_d_etre_definie(script):
    ecarts = fonctions_utilisees_trop_tot(_lignes_de_code(script))
    assert not ecarts, f"deploy/{script} : " + " ; ".join(ecarts)


@pytest.mark.parametrize("script", SCRIPTS)
def test_la_syntaxe_du_script_est_valide(script):
    resultat = subprocess.run(["bash", "-n", str(DEPLOY / script)],
                              capture_output=True, text=True)
    assert resultat.returncode == 0, resultat.stderr


def test_le_controle_voit_un_appel_place_avant_la_definition():
    """Le défaut d'origine, réduit : sans lui, un contrôle muet passerait pour vert."""
    lignes = list(enumerate([
        'ANCIEN="$(garder_https /etc/nginx/sites-available/ludotex)"',
        "garder_https() {",
        '    echo "$1"',
        "}",
    ], start=1))
    assert fonctions_utilisees_trop_tot(lignes) == [
        "garder_https : utilisée ligne 1, définie ligne 2"]


# ===========================================================================
# Un échec de certbot dit où chercher
# ===========================================================================
def test_chaque_echec_de_certbot_nomme_le_pare_feu_de_l_hebergeur():
    """
    Le script ouvre `ufw`, pas le pare-feu que l'hébergeur place devant le
    serveur. Port 80 fermé là-bas, certbot échoue sans dire pourquoi : les deux
    demandes de certificat (production, formation) renvoient vers la cause.
    """
    texte = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    demandes = re.findall(r"if certbot --nginx[^\n]*\n(.*?)\n\s*fi\n", texte, flags=re.DOTALL)
    assert len(demandes) == 2
    for bloc in demandes:
        assert "causes_echec_certificat" in bloc.split("else", 1)[1]
    aide = texte[texte.index("causes_echec_certificat() {"):]
    aide = aide[:aide.index("\n}\n")]
    assert "pare-feu de l'hébergeur" in aide and "port 80" in aide


def test_la_documentation_annonce_les_ports_a_ouvrir_chez_l_hebergeur():
    doc = (DEPLOY.parent / "docs" / "deploiement.md").read_text(encoding="utf-8")
    prerequis = doc[doc.index("## 0. "):doc.index("## 1. ")]
    assert "ports 80 et 443 ouverts chez l'hébergeur" in prerequis


@pytest.mark.parametrize("script", SCRIPTS)
def test_aucun_tube_vers_grep_q_sous_pipefail(script):
    """`commande | grep -q` sous `set -o pipefail` : grep sort au premier
    résultat, la commande reçoit SIGPIPE et le tube rend 141, même quand le
    motif est trouvé. `update.sh` ne redémarrait ainsi jamais l'instance de
    formation, restée plusieurs versions en arrière de la production.
    Capturer la sortie d'abord, ou tester un fichier."""
    if "pipefail" not in (DEPLOY / script).read_text(encoding="utf-8"):
        pytest.skip("pas de pipefail dans ce script")
    fautifs = [f"{script}:{n}: {ligne.strip()}" for n, ligne in _lignes_de_code(script)
               if re.search(r"\|\s*grep\s+(-\w*q|--quiet)", ligne)]
    assert not fautifs, "\n".join(fautifs)


def test_update_sh_redemarre_la_formation_si_son_unite_existe():
    texte = (DEPLOY / "update.sh").read_text(encoding="utf-8")
    assert ("if [[ -f /etc/systemd/system/ludotex-formation.service ]]; then\n"
            "    systemctl restart ludotex-formation") in texte
