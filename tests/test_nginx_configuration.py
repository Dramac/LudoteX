"""
CE QUE NGINX FAIT AVANT ET APRÈS L'APPLICATION (lot-10-pré-production).

nginx n'est PAS couvert par pytest : rien ici ne lance `nginx -t`, ni ne prouve
qu'une configuration se charge. L'essai réel (nginx -t sur les trois fichiers,
page d'erreur, attrape-tout, débit, journaux) est consigné au compte rendu du
lot. Ce fichier verrouille ce qui peut l'être sur le TEXTE des fichiers :

- `DOC-03` : le bloc de commandes de la formation substitue le bon motif, et le
  contrôle `server_name` accompagne chaque `grep example.fr` ;
- `PROD-07` : l'attrape-tout ferme la porte SANS certificat — un
  `ssl_certificate` ou le `snakeoil` de Debian y ferait échouer `nginx -t` sur
  un serveur sans le paquet `ssl-cert` — et sans `ipv6only`, que certbot a
  déjà posé sur le bloc de la production ;
- `PROD-08` : un journal par site, la paire conditionnelle de SEC-02(a) et ses
  formats intacts ;
- `EXP-05` : `error_page` vers une page statique, dans une `location` qui
  n'hérite pas du cache d'un an et ne pose aucun `add_header` ;
- `SEC-14` (volet nginx) : un débit devant `/admin`, pas devant le prêt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_neutralisation import _motif, _noms_bannis

RACINE = Path(__file__).resolve().parent.parent
DEPLOY = RACINE / "deploy"
PAGE = RACINE / "app" / "static" / "indisponible.html"

# Les deux sites et le préfixe de leurs noms nginx (zones, map, log_format).
SITES = {
    "nginx-ludotex.conf": "ludotex_",
    "nginx-ludotex-formation.conf": "ludotexformation_",
}


def _texte(nom: str) -> str:
    return (DEPLOY / nom).read_text(encoding="utf-8")


def _directives(texte: str) -> list[str]:
    """Les lignes utiles, commentaires et espaces en trop retirés."""
    lignes = []
    for brute in texte.splitlines():
        ligne = " ".join(brute.split("#", 1)[0].split())
        if ligne:
            lignes.append(ligne)
    return lignes


def _bloc_location(texte: str, entete: str) -> list[str]:
    """Les directives d'une `location` (sans imbrication), en-tête exact donné."""
    lignes = _directives(texte)
    debut = lignes.index(entete + " {")
    fin = lignes.index("}", debut)
    return lignes[debut + 1 : fin]


# ===========================================================================
# PROD-08 — un journal par site, format inchangé
# ===========================================================================
def _journaux(texte: str) -> list[tuple[str, str, str]]:
    return re.findall(r"^\s*access_log (\S+) (\S+) if=\$(\S+);", texte, re.MULTILINE)


@pytest.mark.parametrize("nom,prefixe", SITES.items())
def test_chaque_site_garde_la_paire_conditionnelle_dans_un_seul_fichier(nom, prefixe):
    journaux = _journaux(_texte(nom))
    assert [(fmt, cond) for _, fmt, cond in journaux] == [
        (f"{prefixe}combined", f"{prefixe}pas_secrete"),
        (f"{prefixe}combined_sans_secret", f"{prefixe}route_secrete"),
    ]
    chemins = {chemin for chemin, _, _ in journaux}
    assert len(chemins) == 1, "les deux access_log d'un site désignent le même fichier"
    assert chemins != {"/var/log/nginx/access.log"}


def test_les_deux_sites_ecrivent_dans_deux_fichiers_distincts_couverts_par_logrotate():
    chemins = [{c for c, _, _ in _journaux(_texte(nom))}.pop() for nom in SITES]
    assert len(set(chemins)) == 2
    for chemin in chemins:
        # Le logrotate de Debian couvre /var/log/nginx/*.log, rien d'autre.
        assert re.fullmatch(r"/var/log/nginx/[a-z-]+\.log", chemin), chemin
        # Et install.sh crée ce même fichier avec les bons droits.
        assert f"creer_journal_nginx {chemin}" in _texte("../deploy/install.sh")


@pytest.mark.parametrize("nom,prefixe", SITES.items())
def test_le_format_de_journal_n_a_pas_bouge(nom, prefixe):
    # SEC-02(a) : 0 fuite sur 37 367 lignes (audit). Ce lot ne change que le
    # chemin ; les deux formats doivent rester ceux-ci, caractère pour caractère.
    texte = _texte(nom)
    attendu = {
        f"{prefixe}combined": (
            "'$remote_addr - $remote_user [$time_local] ' "
            "'\"$request\" $status $body_bytes_sent ' "
            "'\"$http_referer\" \"$http_user_agent\" ' "
            "'rt=$request_time uct=$upstream_response_time'"
        ),
        f"{prefixe}combined_sans_secret": (
            "'$remote_addr - $remote_user [$time_local] ' "
            "'\"$request_method $uri $server_protocol\" $status $body_bytes_sent ' "
            "'\"$http_referer\" \"$http_user_agent\" ' "
            "'rt=$request_time uct=$upstream_response_time'"
        ),
    }
    for nom_format, corps in attendu.items():
        trouve = re.search(r"^log_format " + nom_format + r"\n(.*?);\n", texte, re.MULTILINE | re.DOTALL)
        assert trouve, nom_format
        assert " ".join(trouve.group(1).split()) == corps


# ===========================================================================
# EXP-05 — une page quand l'application est arrêtée
# ===========================================================================
@pytest.mark.parametrize("nom", SITES)
def test_les_erreurs_de_l_amont_menent_a_la_page_statique(nom):
    texte = _texte(nom)
    lignes = _directives(texte)
    assert "error_page 502 503 504 /indisponible.html;" in lignes
    # Au niveau `server` : une seule déclaration, et aucune `location` ne la
    # redéclare (elle perdrait l'héritage, comme add_header).
    assert sum(l.startswith("error_page") for l in lignes) == 1
    # Jamais interceptées : les erreurs propres à l'application gardent leur page.
    assert not any(l.startswith("proxy_intercept_errors") for l in lignes)


@pytest.mark.parametrize("nom", SITES)
def test_la_page_est_servie_hors_du_cache_long_et_garde_les_en_tetes(nom):
    location = _bloc_location(_texte(nom), "location = /indisponible.html")
    assert "internal;" in location
    # La même substitution de chemin qu'`alias` de /static/ : sed d'install.sh.
    assert "root /opt/ludotex/app/static;" in location
    # Ni `expires` (cache d'un an de /static/), ni `add_header` (il ferait
    # perdre les cinq en-têtes SEC-01, add_header n'étant pas cumulatif).
    assert not any(l.startswith(("expires", "add_header")) for l in location)
    # Les en-têtes SEC-01 du niveau server valent aussi pour une réponse 502.
    en_tetes = [l for l in _directives(_texte(nom)) if l.startswith("add_header")]
    assert len(en_tetes) == 5 and all(l.endswith(" always;") for l in en_tetes)


def test_la_page_d_indisponibilite_existe_et_dit_quoi_faire():
    html = PAGE.read_text(encoding="utf-8")
    assert '<html lang="fr">' in html
    assert 'name="viewport"' in html
    texte = re.sub(r"<[^>]+>", " ", html)
    for consigne in ("papier", "code de la", "numéro de", "pochette", "bureau", "fait foi"):
        assert consigne in texte, consigne


def test_la_page_d_indisponibilite_ne_porte_aucune_couleur_ni_ressource_externe():
    # Une page statique ne lit pas la base : aucune couleur, pour ne pas
    # contredire le thème réglable. Le navigateur choisit (`color-scheme`).
    html = PAGE.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", html)
    assert not re.search(r"\b(rgb|rgba|hsl|hsla|oklch|color-mix)\(", html)
    assert not re.search(r"\b(background|color)\s*:", html.replace("color-scheme", ""))
    # L'application est arrêtée : rien ne doit dépendre d'elle, ni d'ailleurs.
    assert not re.search(r"<(script|link|img|iframe)\b", html)
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    # Le seul lien ramène au scanner, qui répondra quand l'application reviendra.
    assert re.findall(r'href="([^"]*)"', html) == ["/scanner"]


def test_la_page_d_indisponibilite_ne_nomme_aucune_association():
    noms = _noms_bannis()
    if not noms:
        pytest.skip("interne/noms-bannis.txt absent ou vide : rien à surveiller ici")
    html = PAGE.read_text(encoding="utf-8")
    for nom in noms:
        assert not _motif(nom).search(html), "nom banni dans la page d'indisponibilité"


# ===========================================================================
# SEC-14, volet nginx — un débit devant /admin, pas devant le prêt
# ===========================================================================
@pytest.mark.parametrize("nom,prefixe", SITES.items())
def test_admin_a_sa_zone_de_debit_et_le_pret_n_en_a_aucune(nom, prefixe):
    texte = _texte(nom)
    lignes = _directives(texte)
    assert f"limit_req_zone $binary_remote_addr zone={prefixe}admin:10m rate=2r/s;" in lignes
    for entete in ("location = /admin", "location /admin/"):
        location = _bloc_location(texte, entete)
        # La même paire que les deux zones existantes.
        assert f"limit_req zone={prefixe}admin burst=40 nodelay;" in location
        assert "limit_req_status 429;" in location
        assert any(l.startswith("proxy_pass http://127.0.0.1:") for l in location)
    # Tout le reste — /pret, /scanner, /acces — passe par `location /`, sans frein.
    assert not any(l.startswith("limit_req") for l in _bloc_location(texte, "location /"))


@pytest.mark.parametrize("nom,prefixe", SITES.items())
def test_les_zones_existantes_n_ont_pas_bouge(nom, prefixe):
    lignes = _directives(_texte(nom))
    assert f"limit_req_zone $binary_remote_addr zone={prefixe}live:10m rate=2r/s;" in lignes
    assert f"limit_req_zone $binary_remote_addr zone={prefixe}stats:10m rate=6r/m;" in lignes
    assert f"limit_req zone={prefixe}live burst=20 nodelay;" in lignes
    assert f"limit_req zone={prefixe}stats burst=3 nodelay;" in lignes
    assert "client_max_body_size 20m;" in lignes
    # Écart assumé du lot agora-2 : pas de `public, immutable` sur /static/.
    assert "expires 1y;" in _bloc_location(_texte(nom), "location /static/")
    assert "immutable" not in " ".join(lignes)


# ===========================================================================
# PROD-07 — l'attrape-tout
# ===========================================================================
def test_l_attrape_tout_se_declare_par_defaut_sur_80_et_443_et_ferme_la_connexion():
    lignes = _directives(_texte("nginx-attrape-tout.conf"))
    for ecoute in (
        "listen 80 default_server;",
        "listen [::]:80 default_server;",
        "listen 443 ssl default_server;",
        "listen [::]:443 ssl default_server;",
    ):
        assert ecoute in lignes
    assert "ssl_reject_handshake on;" in lignes
    assert "return 444;" in lignes
    assert "access_log off;" in lignes


def test_l_attrape_tout_ne_depend_d_aucun_certificat_ni_d_ipv6only():
    lignes = _directives(_texte("nginx-attrape-tout.conf"))
    # Sur le serveur de référence, `snippets/snakeoil.conf` existe mais pas le
    # certificat qu'il désigne : l'inclure ferait échouer `nginx -t`.
    assert not any(l.startswith(("ssl_certificate", "include")) for l in lignes)
    assert "snakeoil" not in " ".join(lignes)
    # `ipv6only` ne peut figurer qu'une fois par adresse:port, tous fichiers
    # confondus ; certbot l'a posé sur le bloc 443 de la production.
    assert "ipv6only" not in " ".join(lignes)
    # Aucune valeur à substituer : ni domaine d'exemple, ni chemin.
    assert "example.fr" not in _texte("nginx-attrape-tout.conf")
    assert "/opt/ludotex" not in _texte("nginx-attrape-tout.conf")


def test_install_sh_desactive_default_avant_de_tester_nginx():
    install_sh = _texte("install.sh")
    etape7 = install_sh[install_sh.index("# 7. nginx + HTTPS") : install_sh.index("# 8. Sauvegarde automatique")]
    retrait = etape7.index("rm -f /etc/nginx/sites-enabled/default")
    activation = etape7.index("ln -sf /etc/nginx/sites-available/attrape-tout /etc/nginx/sites-enabled/attrape-tout")
    assert retrait < etape7.index("nginx -t")
    assert activation < etape7.index("nginx -t")


# ===========================================================================
# DOC-03 — la procédure de la formation substitue le bon motif
# ===========================================================================
def _lignes_sed_domaine(texte: str) -> list[str]:
    return [l.strip() for l in texte.splitlines() if re.search(r"sed -i .s/.*example.*\\\.fr/", l)]


@pytest.mark.parametrize("document", ["docs/deploiement.md", "deploy/install.sh", "wiki/Deploiement.md"])
def test_le_fichier_de_formation_n_est_jamais_passe_au_sed_de_la_production(document):
    chemin = RACINE / document
    if not chemin.exists():
        pytest.skip(f"{document} absent (le wiki est un dépôt séparé)")
    lignes = _lignes_sed_domaine(chemin.read_text(encoding="utf-8"))
    formation = [l for l in lignes if "ludotex-formation" in l]
    assert formation, "aucune substitution du domaine de formation trouvée"
    for ligne in formation:
        assert "s/formation\\.pret\\.example\\.fr/" in ligne, ligne
    for ligne in lignes:
        if "ludotex-formation" not in ligne:
            assert "s/pret\\.example\\.fr/" in ligne and "formation" not in ligne.split("/")[1], ligne


@pytest.mark.parametrize("document", ["docs/deploiement.md", "wiki/Deploiement.md"])
def test_chaque_controle_example_fr_s_accompagne_du_controle_server_name(document):
    chemin = RACINE / document
    if not chemin.exists():
        pytest.skip(f"{document} absent (le wiki est un dépôt séparé)")
    texte = chemin.read_text(encoding="utf-8")
    fichiers_controles = re.findall(r"grep -n 'example\\?\.fr' (\S+)", texte)
    assert fichiers_controles
    for fichier in fichiers_controles:
        assert f"grep -n 'server_name' {fichier}" in texte, fichier


def test_aucune_procedure_ne_designe_un_dossier_d_installation_de_formation():
    # « Remplacer partout ludotex par ludotex-formation » menait à
    # /opt/ludotex-formation, qui n'existe pas : la formation tourne sur le code
    # de la production. Les blocs de commandes seuls : la prose, elle, peut
    # mettre en garde contre ce chemin.
    for document in ("docs/deploiement.md", "wiki/Deploiement.md", "docs/notes-de-deploiement.md"):
        chemin = RACINE / document
        if chemin.exists():
            blocs = re.findall(r"```bash\n(.*?)```", chemin.read_text(encoding="utf-8"), re.DOTALL)
            assert blocs, document
            assert not any("/opt/ludotex-formation" in bloc for bloc in blocs), document
