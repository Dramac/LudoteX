"""
LE CONTRÔLE DE REPORT DIT CE QUI DIFFÈRE, ET RIEN D'AUTRE.

`scripts/controle_report.py` compare le serveur au dépôt à la fin d'une mise à
jour. Deux défauts le rendraient pire qu'inutile :
- un **faux positif** — il crie à chaque mise à jour et on cesse de le lire ;
- un **faux négatif** — il se tait sur un vrai écart, dont des permissions,
  qu'une comparaison de fichiers ne voit pas (c'est ainsi que le `0700` du
  dossier des sauvegardes est resté absent de la production).

Tout se joue sur des textes fournis par le test, jamais sur de vrais fichiers
de serveur. `deploy/update.sh`, lui, n'est pas couvert par pytest : sa
non-interruption se prouve par une exécution réelle, consignée au compte rendu
du lot.
"""

from __future__ import annotations

import getpass
import stat
from pathlib import Path

import pytest

from scripts import controle_report as cr

_RACINE = Path(__file__).resolve().parent.parent


# ===========================================================================
# Un fichier nginx modèle, et ce que certbot en fait réellement
# ===========================================================================
NGINX_DEPOT = """\
# Commentaire de tête, avec le domaine pret.example.fr cité en exemple.
map $request_uri $uri_sans_secret {
    default $request_uri;
    "~^(?<avant>.*[?&]jeton=)[^&]*(?<apres>.*)$" "${avant}[RETIRE]${apres}";
}
log_format principal '$remote_addr "$request" #pas-un-commentaire';

server {
    listen 80;
    listen [::]:80;
    server_name pret.example.fr;          # <-- À REMPLACER

    client_max_body_size 20m;
    add_header X-Frame-Options "DENY" always;

    location /static/ {
        alias /opt/ludotex/app/static/;   # <-- adapter le chemin si besoin
        expires 1y;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
    }
}
"""

# Même fichier après `sed` d'install.sh puis `certbot --nginx --redirect` :
# structure relevée sur la production le 2026-09-13 (domaine fictif ici).
NGINX_INSTALLE = """\
map $request_uri $uri_sans_secret {
    default $request_uri;
    "~^(?<avant>.*[?&]jeton=)[^&]*(?<apres>.*)$" "${avant}[RETIRE]${apres}";
}
log_format principal '$remote_addr "$request" #pas-un-commentaire';

server {
    server_name jeux.asso-fictive.test;

    client_max_body_size 20m;
    add_header X-Frame-Options "DENY" always;

    location /static/ {
        alias /opt/ludotex/app/static/;
        expires 1y;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
    }

    listen [::]:443 ssl ipv6only=on; # managed by Certbot
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/jeux.asso-fictive.test/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/jeux.asso-fictive.test/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot

}


server {
    if ($host = jeux.asso-fictive.test) {
        return 301 https://$host$request_uri;
    } # managed by Certbot


    listen 80;
    listen [::]:80;
    server_name jeux.asso-fictive.test;
    return 404; # managed by Certbot


}"""


def test_nginx_fichiers_identiques_aucune_difference():
    assert cr.comparer_nginx(NGINX_DEPOT, NGINX_DEPOT) == []


def test_nginx_seul_le_domaine_differe_aucune_difference():
    installe = NGINX_DEPOT.replace("pret.example.fr", "jeux.asso-fictive.test")
    assert cr.comparer_nginx(NGINX_DEPOT, installe) == []


def test_nginx_seules_les_lignes_de_certbot_different_aucune_difference():
    # Domaine ET certbot : c'est l'état normal d'un serveur en service.
    assert cr.comparer_nginx(NGINX_DEPOT, NGINX_INSTALLE) == []


def test_nginx_certbot_sans_le_domaine_aucune_difference():
    installe = NGINX_INSTALLE.replace("jeux.asso-fictive.test", "pret.example.fr")
    assert cr.comparer_nginx(NGINX_DEPOT, installe) == []


def test_nginx_directive_ajoutee_au_depot_est_signalee():
    # Le scénario du durcissement resté six semaines à la porte du serveur.
    depot = NGINX_DEPOT.replace(
        '    add_header X-Frame-Options "DENY" always;\n',
        '    add_header X-Frame-Options "DENY" always;\n    add_header X-Content-Type-Options "nosniff" always;\n',
    )
    ecarts = cr.comparer_nginx(depot, NGINX_INSTALLE)
    assert ecarts == ['dépôt seul   : add_header X-Content-Type-Options "nosniff" always;']


def test_nginx_directive_retiree_du_depot_est_signalee():
    depot = NGINX_DEPOT.replace("        expires 1y;\n", "")
    assert cr.comparer_nginx(depot, NGINX_INSTALLE) == ["serveur seul : expires 1y;"]


def test_nginx_valeur_modifiee_est_signalee_des_deux_cotes():
    depot = NGINX_DEPOT.replace("20m", "5m")
    assert cr.comparer_nginx(depot, NGINX_INSTALLE) == [
        "dépôt seul   : client_max_body_size 5m;",
        "serveur seul : client_max_body_size 20m;",
    ]


def test_nginx_directive_ajoutee_a_la_main_dans_le_bloc_de_certbot_est_signalee():
    # Le bloc de redirection n'est mis de côté que s'il ne porte QUE ce que
    # certbot y écrit : un en-tête posé là à la main doit apparaître.
    installe = NGINX_INSTALLE.replace(
        "    return 404; # managed by Certbot",
        "    add_header Strict-Transport-Security max-age=31536000;\n    return 404; # managed by Certbot",
    )
    ecarts = cr.comparer_nginx(NGINX_DEPOT, installe)
    assert "serveur seul : add_header Strict-Transport-Security max-age=31536000;" in ecarts


def test_nginx_un_diese_entre_guillemets_n_est_pas_un_commentaire():
    installe = NGINX_INSTALLE.replace("#pas-un-commentaire", "#autre-chose")
    ecarts = cr.comparer_nginx(NGINX_DEPOT, installe)
    assert len(ecarts) == 2 and all("log_format" in e for e in ecarts)


def test_nginx_le_chemin_d_installation_est_substitue_comme_le_fait_install_sh():
    installe = NGINX_INSTALLE.replace("/opt/ludotex", "/srv/jeux")
    assert cr.comparer_nginx(NGINX_DEPOT, installe, install_dir="/srv/jeux") == []
    assert cr.comparer_nginx(NGINX_DEPOT, installe) != []


def test_nginx_les_fichiers_du_depot_passent_la_normalisation_sans_rien_perdre():
    # Garde-fou sur les vrais fichiers : la normalisation ne doit pas vider un
    # bloc entier (un fichier réduit à rien comparerait toujours « conforme »).
    for fichier in sorted((_RACINE / "deploy").glob("nginx-*.conf")):
        lignes = cr.normaliser_nginx(fichier.read_text(encoding="utf-8"))
        assert any(l.startswith("proxy_pass ") for l in lignes), fichier.name
        assert "server_name <domaine>;" in lignes, fichier.name


# ===========================================================================
# Unités systemd
# ===========================================================================
UNITE = """\
# Commentaire
[Service]
User=pretjeux
WorkingDirectory=/opt/ludotex
ExecStart=/opt/ludotex/.venv/bin/uvicorn app.main:app \\
    --host 127.0.0.1 --port 8000
"""


def test_unite_seuls_les_commentaires_different_aucune_difference():
    installe = UNITE.replace("# Commentaire", "# Un autre commentaire\n; et un autre")
    assert cr.comparer_unite(UNITE, installe) == []


def test_unite_option_ajoutee_au_depot_est_signalee():
    depot = UNITE.replace("--port 8000", "--port 8000 --no-access-log")
    assert cr.comparer_unite(depot, UNITE) == [
        "dépôt seul   : --host 127.0.0.1 --port 8000 --no-access-log",
        "serveur seul : --host 127.0.0.1 --port 8000",
    ]


def test_unite_un_diese_en_milieu_de_ligne_fait_partie_de_la_valeur():
    depot = UNITE.replace("User=pretjeux", "User=pretjeux # vraiment")
    assert cr.comparer_unite(depot, UNITE) != []


# ===========================================================================
# Fichiers d'environnement : des noms, jamais des valeurs
# ===========================================================================
EXEMPLE = """\
PRET_TOKEN=remplacer_par_un_jeton
ADMIN_PASSWORD=remplacer
NOM_ASSOCIATION=
DEPOT_URL=""
# FORMATION_URL=https://formation.exemple.test
NOUVELLE_CLE=60
"""


def test_une_cle_absente_est_signalee_par_son_nom():
    env = "PRET_TOKEN=abc\nADMIN_PASSWORD=def\n"
    assert cr.cles_manquantes(EXEMPLE, env) == ["NOUVELLE_CLE"]


def test_les_cles_commentees_ou_vides_de_l_exemple_sont_facultatives():
    env = "PRET_TOKEN=a\nADMIN_PASSWORD=b\nNOUVELLE_CLE=c\n"
    assert cr.cles_manquantes(EXEMPLE, env) == []


def test_une_cle_commentee_dans_le_env_compte_comme_absente():
    env = "PRET_TOKEN=a\n# ADMIN_PASSWORD=b\nexport NOUVELLE_CLE=c\n"
    assert cr.cles_manquantes(EXEMPLE, env) == ["ADMIN_PASSWORD"]


def test_une_cle_presente_mais_vide_compte_comme_presente():
    # `PRET_TOKEN=` vide est un choix explicite (instance de formation ouverte).
    env = "PRET_TOKEN=\nADMIN_PASSWORD=b\nNOUVELLE_CLE=c\n"
    assert cr.cles_manquantes(EXEMPLE, env) == []


def test_le_vrai_env_example_n_exige_pas_les_cles_facultatives():
    exemple = (_RACINE / ".env.example").read_text(encoding="utf-8")
    attendues = cr.cles_attendues(exemple)
    assert {"PRET_TOKEN", "ADMIN_PASSWORD", "DATABASE_PATH", "BASE_URL"} <= attendues
    # Documentées « vide ou absente », ou propres à l'instance de formation :
    # les exiger ferait crier le contrôle sur toute production normale.
    assert not attendues & {"NOM_ASSOCIATION", "DEPOT_URL", "MODE_FORMATION", "FORMATION_URL", "FORMATION_CATALOGUE_CSV"}


class ServeurFactice(cr.Serveur):
    """Arborescence dans tmp_path ; commandes système simulées."""

    def __init__(self, racine: Path, crontab: str = "", paquets_absents: tuple[str, ...] = ()):
        super().__init__(racine)
        self._crontab = crontab
        self._paquets_absents = paquets_absents

    def ecrire(self, absolu: str, texte: str, mode: int = 0o644) -> Path:
        chemin = self.chemin(absolu)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(texte, encoding="utf-8")
        chemin.chmod(mode)
        return chemin

    def crontab(self) -> str:
        return self._crontab

    def etat_activation(self, unite: str) -> str:
        return "enabled"

    def rechargement_attendu(self, unite: str) -> bool:
        return False

    def paquet_installe(self, paquet: str) -> bool:
        return paquet not in self._paquets_absents


def test_une_valeur_du_env_n_apparait_jamais_dans_la_sortie(tmp_path):
    secret = "jeton-ultra-secret-43-caracteres-XYZ"
    serveur = ServeurFactice(tmp_path / "srv")
    # Une clé manquante, une ligne mal formée qui contient le secret, et le
    # secret en valeur : rien de tout cela ne doit ressortir.
    serveur.ecrire("/opt/ludotex/.env", f"PRET_TOKEN={secret}\n{secret}\nADMIN_PASSWORD={secret}\n", 0o600)
    constats = cr.executer(_RACINE, serveur, "/opt/ludotex", "/var/lib/ludotex", getpass.getuser())
    sortie = cr.rendre(constats)
    assert "DATABASE_PATH" in sortie  # la clé manquante est bien nommée…
    assert secret not in sortie  # … et aucune valeur n'est affichée


def test_une_erreur_imprevue_ne_cite_jamais_son_message(tmp_path, monkeypatch):
    secret = "ADMIN_PASSWORD=mot-de-passe-en-clair"

    def explose(*args, **kwargs):
        raise ValueError(secret)

    monkeypatch.setattr(cr, "controler_env", explose)
    serveur = ServeurFactice(tmp_path / "srv")
    sortie = cr.rendre(cr.executer(_RACINE, serveur, "/opt/ludotex", "/var/lib/ludotex", "pretjeux"))
    assert "vérification impossible (ValueError)" in sortie
    assert "mot-de-passe-en-clair" not in sortie
    # Les autres familles ont été jouées malgré tout.
    assert "Paquets système" in sortie


# ===========================================================================
# Tâche de sauvegarde planifiée
# ===========================================================================
ATTENDUE = "0 3 * * * /opt/ludotex/deploy/sauvegarde.sh /opt/ludotex /var/lib/ludotex/sauvegardes >> /var/log/x.log 2>&1"


def test_cron_identique_aucune_difference():
    assert cr.ecarts_cron(f"# commentaire\nMAILTO=\"\"\n{ATTENDUE.replace(' ', '   ')}\n", ATTENDUE) == []


def test_cron_different_dit_que_les_deux_different_sans_accuser_le_serveur():
    serveur = ATTENDUE.replace("/var/log/x.log", "/var/lib/ludotex/sauvegarde.log")
    ecarts = cr.ecarts_cron(serveur, ATTENDUE)
    assert ecarts[0] == "le serveur et le dépôt diffèrent :"
    assert any("sur le serveur" in e and "/var/lib/ludotex/sauvegarde.log" in e for e in ecarts)
    assert any("dans le dépôt" in e and "/var/log/x.log" in e for e in ecarts)
    assert not any("pas à jour" in e for e in ecarts)


def test_cron_absent_est_signale():
    assert cr.ecarts_cron("", ATTENDUE) != []
    assert cr.ecarts_cron(f"# {ATTENDUE}\n", ATTENDUE) != []


def test_cron_en_double_est_signale():
    assert cr.ecarts_cron(f"{ATTENDUE}\n{ATTENDUE}\n", ATTENDUE)[0].startswith("2 tâches")


def test_la_ligne_de_cron_se_relit_dans_le_vrai_install_sh():
    # Si un lot remplace la crontab (minuteur systemd, par exemple), ce test
    # tombe : c'est le signal pour adapter la famille « tâche planifiée ».
    install_sh = (_RACINE / "deploy/install.sh").read_text(encoding="utf-8")
    ligne = cr.ligne_cron_attendue(install_sh, "/srv/app", "/srv/data")
    assert ligne.startswith("0 3 * * * /srv/app/deploy/sauvegarde.sh /srv/app /srv/data/sauvegardes")
    assert "$" not in ligne


def test_une_variable_inconnue_dans_le_modele_de_cron_est_refusee():
    with pytest.raises(ValueError):
        cr.ligne_cron_attendue('LIGNE_CRON="0 3 * * * $AUTRE/x.sh"\n', "/a", "/b")


def test_les_paquets_se_relisent_dans_le_vrai_install_sh():
    paquets = cr.paquets_attendus((_RACINE / "deploy/install.sh").read_text(encoding="utf-8"))
    assert {"nginx", "certbot", "python3-venv", "sqlite3"} <= set(paquets)
    assert not any(p.startswith("#") for p in paquets)


# ===========================================================================
# Permissions — ce qu'une comparaison de fichiers ne voit pas
# ===========================================================================
def test_dossier_en_0700_aucune_difference(tmp_path):
    dossier = tmp_path / "sauvegardes"
    dossier.mkdir()
    dossier.chmod(0o700)
    droits = cr.lire_droits(dossier)
    assert cr.ecarts_droits(str(dossier), droits, 0o700, getpass.getuser()) == []


def test_dossier_en_0775_est_signale(tmp_path):
    # L'état relevé en production le 2026-09-12, que le contrôle doit voir.
    dossier = tmp_path / "sauvegardes"
    dossier.mkdir()
    dossier.chmod(0o775)
    droits = cr.lire_droits(dossier)
    assert stat.S_IMODE(dossier.stat().st_mode) == 0o775
    assert cr.ecarts_droits(str(dossier), droits, 0o700, getpass.getuser()) == [
        f"{dossier} : droits 775, attendus 700."
    ]


def test_dossier_absent_est_signale(tmp_path):
    absent = tmp_path / "absent"
    assert cr.ecarts_droits(str(absent), cr.lire_droits(absent), 0o700, "pretjeux") != []


def test_mauvais_proprietaire_est_signale():
    assert cr.ecarts_droits("/x", (0o700, "root"), 0o700, "pretjeux") == ["/x : propriétaire root, attendu pretjeux."]


# ===========================================================================
# Bout à bout, sur une arborescence factice
# ===========================================================================
def _serveur_aligne(tmp_path: Path, crontab: str) -> ServeurFactice:
    serveur = ServeurFactice(tmp_path / "srv", crontab=crontab)
    for fichier in (_RACINE / "deploy").glob("*.service"):
        serveur.ecrire(f"/etc/systemd/system/{fichier.name}", fichier.read_text(encoding="utf-8"))
    for fichier in (_RACINE / "deploy").glob("nginx-*.conf"):
        site = fichier.stem[len("nginx-") :]
        serveur.ecrire(f"/etc/nginx/sites-available/{site}", fichier.read_text(encoding="utf-8"))
        serveur.ecrire(f"/etc/nginx/sites-enabled/{site}", "")
    cles = "\n".join(f"{c}=x" for c in sorted(cr.cles_attendues((_RACINE / ".env.example").read_text(encoding="utf-8"))))
    serveur.ecrire("/opt/ludotex/.env", cles, 0o600)
    serveur.ecrire("/etc/ludotex-formation.env", cles, 0o600)
    for dossier in ("/var/lib/ludotex/sauvegardes", "/var/lib/ludotex-formation/sauvegardes"):
        serveur.chemin(dossier).mkdir(parents=True)
        serveur.chemin(dossier).chmod(0o700)
    return serveur


def test_serveur_aligne_sur_le_depot_rien_a_examiner(tmp_path):
    install_sh = (_RACINE / "deploy/install.sh").read_text(encoding="utf-8")
    ligne = cr.ligne_cron_attendue(install_sh, "/opt/ludotex", "/var/lib/ludotex")
    serveur = _serveur_aligne(tmp_path, crontab=ligne + "\n")
    constats = cr.executer(_RACINE, serveur, "/opt/ludotex", "/var/lib/ludotex", getpass.getuser())
    assert [c.ecarts for c in constats] == [[]] * 6
    assert "Serveur aligné sur le dépôt" in cr.rendre(constats)


def test_un_minuteur_ajoute_au_depot_est_couvert_sans_modifier_le_controle(tmp_path):
    depot = tmp_path / "depot"
    (depot / "deploy").mkdir(parents=True)
    (depot / "deploy/ludotex-sauvegarde.timer").write_text("[Timer]\nOnCalendar=*-*-* 03:00\n", encoding="utf-8")
    serveur = ServeurFactice(tmp_path / "srv")
    constat = cr.controler_unites(depot, serveur, "/opt/ludotex")
    assert constat.ecarts == ["/etc/systemd/system/ludotex-sauvegarde.timer : absent du serveur (présent dans deploy/)."]


def test_sans_instance_de_formation_ses_fichiers_absents_ne_sont_pas_des_ecarts(tmp_path):
    serveur = _serveur_aligne(tmp_path, crontab="")
    for absolu in (
        "/etc/systemd/system/ludotex-formation.service",
        "/etc/nginx/sites-available/ludotex-formation",
        "/etc/ludotex-formation.env",
    ):
        serveur.chemin(absolu).unlink()
    assert cr.controler_unites(_RACINE, serveur, "/opt/ludotex").ecarts == []
    assert cr.controler_nginx(_RACINE, serveur, "/opt/ludotex").ecarts == []
    assert cr.controler_droits(serveur, "/opt/ludotex", "/var/lib/ludotex", getpass.getuser()).ecarts == []


def test_le_script_termine_en_code_zero_meme_quand_tout_diverge(tmp_path, capsys, monkeypatch):
    # Aucune commande système réelle : ni crontab, ni systemctl, ni dpkg.
    def commande_introuvable(*arguments):
        raise FileNotFoundError(arguments[0])

    monkeypatch.setattr(cr.Serveur, "_commande", staticmethod(commande_introuvable))
    vide = tmp_path / "rien"
    vide.mkdir()
    assert cr.main(["--racine", str(vide), "--data-dir", "/nulle-part"]) == 0
    sortie = capsys.readouterr().out
    assert "!!" in sortie and "n'a rien modifié" in sortie
