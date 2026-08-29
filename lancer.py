"""
Lanceur autonome de LudoteX — pour un démarrage SANS ligne de commande.

Pensé pour les bénévoles (pas de compétence technique) : un double-clic suffit à
tout démarrer.

  - Windows : `lancer.vbs` (silencieux, pas de fenêtre console) ou `lancer.bat`
    (avec console, utile pour le débogage) ;
  - macOS   : `lancer.command` ;
  - ailleurs, ou en ligne de commande : `python lancer.py` avec n'importe quel
    interpréteur — le script se remet de lui-même dans le venv du projet s'il
    n'y est pas (voir `relancer_dans_le_venv`).

CE QUE FAIT CE SCRIPT, DANS L'ORDRE
------------------------------------
1. Vérifie les prérequis (venv du projet, `cloudflared`, ports libres) — sinon
   ouvre une page HTML d'erreur claire et s'arrête (jamais de plantage brut).
2. Démarre `uvicorn` en sous-processus, caché (port 8000, sur 127.0.0.1).
3. Démarre `cloudflared tunnel --url http://localhost:8000` en sous-processus,
   lit sa sortie ligne par ligne pour en extraire l'URL publique
   (*.trycloudflare.com).
4. Génère une page HTML temporaire : QR de l'URL, URL en grand, statut
   (application / tunnel), bouton rouge « Arrêter LudoteX ».
5. Démarre un petit serveur HTTP de contrôle (port 8001, `/status` + `/stop`)
   qui permet à cette page d'afficher le statut en temps réel et de tout
   arrêter en un clic.
6. Ouvre la page dans le navigateur par défaut.
7. Reste en attente (Ctrl+C ou bouton « Arrêter ») puis ferme proprement les
   sous-processus.

OPTION « --formation »
----------------------
    python lancer.py              # démarre l'application normale (comportement historique)
    python lancer.py --formation  # démarre EN PLUS le site de formation

Avec `--formation`, une SECONDE instance de la même application est lancée en
parallèle sur le port 8100, en MODE FORMATION (bandeau + filigrane), avec ses
propres bases SQLite jetables (`data/formation-*.db`) — jamais celles de
production. Elle est accessible sur cet ordinateur à http://localhost:8100.
Le premier lancement peuple ces bases avec des données fictives (jeux d'essai,
un tournoi d'exemple) ; les lancements suivants les conservent (pour repartir
d'un état propre, utiliser le bouton « Réinitialiser les données de formation »
dans l'admin du site de formation). Le lien « 🎓 Site de formation » apparaît
alors dans le tableau de bord admin de l'instance normale (via `FORMATION_URL`).
Le site de formation n'a PAS de tunnel Cloudflare : il reste local à la machine.

AUCUNE DÉPENDANCE SUPPLÉMENTAIRE : `qrcode`/`pillow` sont déjà dans
`requirements.txt` (réutilise `app.etiquettes.image_qr_nu`, le même dessin de
QR que le reste de l'application) ; `http.server` est dans la bibliothèque
standard.

Prérequis détaillés (notamment l'installation de `cloudflared` sur Windows) :
voir `docs/lancement-local.md`.
"""

from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

PORT_APP = 8000            # uvicorn (instance normale)
PORT_CONTROLE = 8001       # serveur de contrôle (statut + arrêt)
PORT_FORMATION = 8100      # uvicorn (instance de formation, option --formation)
HOTE = "127.0.0.1"

# Bases SQLite jetables du site de formation (ne jamais confondre avec la prod).
DOSSIER_DATA = BASE_DIR / "data"
BASES_FORMATION = {
    "DATABASE_PATH": DOSSIER_DATA / "formation-pret.db",
    "TOURNOI_DATABASE_PATH": DOSSIER_DATA / "formation-tournoi.db",
    "PLANNING_DATABASE_PATH": DOSSIER_DATA / "formation-planning.db",
}

URL_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Sortie d'erreur des sous-processus uvicorn. Elle partait autrefois dans
# subprocess.DEVNULL : quand uvicorn refusait de démarrer, il ne restait que
# « l'application n'a pas démarré à temps », sans le moindre indice — et le
# remède proposé (relancer via la console) n'y donnait pas accès non plus,
# puisque c'est cette redirection, et non la fenêtre, qui masquait l'erreur.
JOURNAUX_UVICORN = {
    "uvicorn": DOSSIER_DATA / "uvicorn-lancer.log",
    "uvicorn_formation": DOSSIER_DATA / "uvicorn-formation-lancer.log",
}

# Marqueur posé avant de se relancer avec l'interpréteur du venv, pour qu'un
# venv cassé provoque un message clair et non une boucle de relances.
MARQUEUR_RELANCE = "LUDOTEX_LANCEUR_RELANCE"

# --- État partagé entre threads (uvicorn / cloudflared / serveur de contrôle) ---
etat: dict[str, object] = {
    "uvicorn_ok": False,
    "cloudflared_ok": False,
    "formation_ok": False,
    "url": None,
    "url_formation": None,
    "erreur": None,
}
_verrou = threading.Lock()
evenement_arret = threading.Event()

processus: dict[str, subprocess.Popen | None] = {
    "uvicorn": None, "cloudflared": None, "uvicorn_formation": None,
}
serveur_controle: dict[str, ThreadingHTTPServer | None] = {"instance": None}


# =====================================================================
# Prérequis
# =====================================================================

def chemin_python_venv() -> Path | None:
    """Chemin de l'interpréteur Python du venv du projet, selon l'OS courant."""
    if sys.platform == "win32":
        candidat = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        candidat = BASE_DIR / ".venv" / "bin" / "python"
    return candidat if candidat.exists() else None


def dans_le_venv_du_projet() -> bool:
    """
    True si l'interpréteur courant est bien celui du venv du projet.

    On compare `sys.prefix` au dossier `.venv`, et surtout PAS les chemins
    d'interpréteurs résolus : `.venv/bin/python` est un lien symbolique vers
    l'interpréteur de base (Homebrew, python.org…), si bien qu'un
    `Path(sys.executable).resolve()` rendrait le Python système et celui du
    venv indistinguables — exactement le cas qu'il s'agit de détecter.
    """
    return Path(sys.prefix) == (BASE_DIR / ".venv")


def relancer_dans_le_venv(arguments: list[str]) -> None:
    """
    Se relance avec l'interpréteur du venv si l'on n'y est pas déjà.

    POURQUOI CE DÉTOUR PLUTÔT QUE DE SEULEMENT CORRIGER LE SOUS-PROCESSUS
    ---------------------------------------------------------------------
    Sous Windows, `lancer.vbs` et `lancer.bat` appellent explicitement
    `.venv\Scripts\python[w].exe` : l'interpréteur courant EST celui du venv,
    et tout le fichier reposait sur cette garantie. Elle ne tient pas dès qu'on
    lance le script à la main — `python3 lancer.py` sous macOS ou Linux, venv
    non activé. Uvicorn était alors démarré avec le Python système, qui n'a ni
    `fastapi` ni `uvicorn` : le sous-processus mourait aussitôt, le port ne
    s'ouvrait jamais, et le lanceur concluait au bout de 30 s que
    « l'application n'a pas démarré à temps ».

    Ne corriger que l'interpréteur du sous-processus ne suffirait pas : ce
    script importe lui aussi des dépendances du projet (`app.etiquettes`, donc
    `qrcode` et `pillow`) pour dessiner le QR de la page du lanceur. L'échec
    serait simplement repoussé plus loin, sous la forme d'une trace brute.
    On repart donc du bon interpréteur, une fois pour toutes.

    `os.execv` REMPLACE le processus courant : rien n'est exécuté après lui.
    Le marqueur d'environnement interdit une seconde relance — un venv présent
    mais cassé (dépendances non installées) doit produire un message clair, pas
    une boucle.
    """
    if dans_le_venv_du_projet() or os.environ.get(MARQUEUR_RELANCE):
        return
    python_venv = chemin_python_venv()
    if python_venv is None:
        return  # absence signalée par verifier_prerequis(), avec un vrai message
    print("Relance avec l'interpréteur du projet :", python_venv)
    # `os.execv` remplace l'image du processus SANS vider les tampons de Python.
    # Sur un stdout redirigé (bloc-bufferisé), la ligne ci-dessus serait perdue
    # — constaté en test : le lanceur semblait n'avoir rien dit.
    sys.stdout.flush()
    sys.stderr.flush()
    os.environ[MARQUEUR_RELANCE] = "1"
    os.execv(str(python_venv), [str(python_venv), str(Path(__file__).resolve()),
                                *arguments])


def python_a_utiliser() -> str:
    """
    L'interpréteur avec lequel démarrer uvicorn.

    Après `relancer_dans_le_venv`, `sys.executable` est déjà le bon ; ce repli
    explicite couvre le cas où la relance n'a pas pu avoir lieu (marqueur déjà
    posé) et évite surtout de réinstaller silencieusement l'hypothèse qui a
    causé la panne.
    """
    python_venv = chemin_python_venv()
    return str(python_venv) if python_venv else sys.executable


def trouver_cloudflared() -> str | None:
    """
    Cherche `cloudflared` dans le PATH, puis à la racine du projet (cas d'un
    exécutable téléchargé et déposé là par un bénévole, sans installation).
    """
    trouve = shutil.which("cloudflared")
    if trouve:
        return trouve
    for nom in ("cloudflared.exe", "cloudflared"):
        candidat = BASE_DIR / nom
        if candidat.exists():
            return str(candidat)
    return None


def _port_libre(port: int) -> bool:
    """True si `port` est libre sur 127.0.0.1 (aucun serveur déjà à l'écoute)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOTE, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def verifier_prerequis(formation: bool = False) -> list[str]:
    """Renvoie la liste des problèmes bloquants (liste vide = tout est prêt)."""
    problemes = []
    if chemin_python_venv() is None:
        problemes.append(
            "Environnement virtuel introuvable (dossier .venv). "
            "Installer d'abord l'application — voir docs/lancement-local.md."
        )
    if trouver_cloudflared() is None:
        problemes.append(
            "« cloudflared » est introuvable (ni dans le PATH, ni à la racine "
            "du projet). Voir docs/lancement-local.md pour l'installer."
        )
    if not _port_libre(PORT_APP) or not _port_libre(PORT_CONTROLE):
        problemes.append(
            "LudoteX semble déjà en cours d'exécution (port 8000 ou 8001 "
            "occupé). Arrêter l'instance existante (bouton « Arrêter LudoteX » "
            "de sa page, ou fermer les fenêtres/processus concernés) avant "
            "de relancer."
        )
    if formation and not _port_libre(PORT_FORMATION):
        problemes.append(
            f"Le port {PORT_FORMATION} (site de formation) est déjà occupé. "
            "Arrêter l'instance de formation existante avant de relancer."
        )
    return problemes


# =====================================================================
# Sous-processus : uvicorn
# =====================================================================

def _attendre_port(port: int, timeout: float) -> bool:
    """Attend (au plus `timeout` secondes) qu'une connexion TCP à `port` réussisse."""
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            with socket.create_connection((HOTE, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def journal_uvicorn(cle: str) -> Path:
    """Fichier où est recopiée la sortie du sous-processus uvicorn `cle`."""
    return JOURNAUX_UVICORN.get(cle, DOSSIER_DATA / f"{cle}-lancer.log")


def dernieres_lignes(chemin: Path, nombre: int = 15) -> str:
    """
    Les `nombre` dernières lignes non vides d'un journal, ou "" s'il est
    illisible ou vide. Ne lève jamais : c'est un confort de diagnostic, il ne
    doit pas devenir une seconde panne par-dessus la première.
    """
    try:
        lignes = [l.rstrip() for l in
                  chemin.read_text(encoding="utf-8", errors="replace").splitlines()
                  if l.strip()]
    except OSError:
        return ""
    return "\n".join(lignes[-nombre:])


def demarrer_uvicorn(cle: str = "uvicorn", port: int = PORT_APP,
                     env_extra: dict[str, str] | None = None) -> None:
    """
    Lance uvicorn en sous-processus, caché, et le range sous la clé `cle` de
    `processus` (pour qu'`arreter_tout` le termine aussi).

    L'interpréteur vient de `python_a_utiliser()`, JAMAIS de `sys.executable` :
    voir `relancer_dans_le_venv` pour ce que cette hypothèse-là a coûté.
    `cwd=BASE_DIR` assure que `load_dotenv()` (dans app/db.py) retrouve le
    `.env` à la racine.

    La sortie d'erreur va dans un FICHIER (`JOURNAUX_UVICORN`) et non dans
    `subprocess.DEVNULL` : c'est la seule trace disponible quand uvicorn
    refuse de démarrer, et `main()` en cite les dernières lignes dans son
    message d'échec.

    `env_extra` (optionnel) surcharge des variables d'environnement pour ce
    seul processus — utilisé pour l'instance de formation (MODE_FORMATION=1 +
    bases jetables) et pour poser FORMATION_URL sur l'instance normale. Les
    valeurs passées ici priment sur `.env` (python-dotenv n'écrase jamais une
    variable déjà présente dans l'environnement).
    """
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    env = {**os.environ, **env_extra} if env_extra else None

    # Le journal est remis à zéro à chaque démarrage : on veut la cause de
    # CETTE tentative, pas un historique à faire défiler. Si le fichier ne peut
    # pas être ouvert (dossier absent, disque plein), on retombe sur l'ancien
    # comportement plutôt que d'empêcher le démarrage — ne jamais bloquer.
    try:
        DOSSIER_DATA.mkdir(parents=True, exist_ok=True)
        sortie = open(journal_uvicorn(cle), "w", encoding="utf-8")
    except OSError:
        sortie = subprocess.DEVNULL

    processus[cle] = subprocess.Popen(
        [python_a_utiliser(), "-m", "uvicorn", "app.main:app",
         "--host", HOTE, "--port", str(port)],
        cwd=str(BASE_DIR),
        stdout=sortie,
        stderr=subprocess.STDOUT,
        env=env,
        **kwargs,
    )


def env_formation() -> dict[str, str]:
    """Variables d'environnement de l'instance de formation (bases jetables)."""
    env = {"MODE_FORMATION": "1"}
    env.update({cle: str(chemin) for cle, chemin in BASES_FORMATION.items()})
    return env


def preparer_bases_formation() -> None:
    """
    Au PREMIER lancement seulement, crée et peuple les bases de formation
    (jeux fictifs + tournoi d'exemple) via `python -m app.formation`. Si la base
    de prêt de formation existe déjà, on ne touche à rien (on ne réécrase pas
    les données de la session en cours — le bouton d'admin sert à cela).
    """
    DOSSIER_DATA.mkdir(parents=True, exist_ok=True)
    if BASES_FORMATION["DATABASE_PATH"].exists():
        return
    print("Première initialisation des bases de formation (données fictives)…")
    subprocess.run(
        [sys.executable, "-m", "app.formation"],
        cwd=str(BASE_DIR),
        env={**os.environ, **env_formation()},
        check=True,
    )


# =====================================================================
# Sous-processus : cloudflared
# =====================================================================

def demarrer_cloudflared(chemin_cloudflared: str) -> None:
    """
    Lance le tunnel Cloudflare en sous-processus, caché, et démarre un thread
    qui lit sa sortie standard d'erreur ligne par ligne pour en extraire l'URL
    publique (*.trycloudflare.com).
    """
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    processus["cloudflared"] = subprocess.Popen(
        [chemin_cloudflared, "tunnel", "--url", f"http://localhost:{PORT_APP}"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **kwargs,
    )
    threading.Thread(target=_lire_sortie_cloudflared, daemon=True).start()


def _lire_sortie_cloudflared() -> None:
    """
    Lit stderr de cloudflared ligne par ligne jusqu'à trouver l'URL du tunnel,
    puis continue à vider le tube (évite un blocage du sous-processus une fois
    son tampon de sortie plein).
    """
    proc = processus["cloudflared"]
    if proc is None or proc.stderr is None:
        return
    for ligne in proc.stderr:
        m = URL_TUNNEL_RE.search(ligne)
        if m:
            with _verrou:
                if not etat["url"]:
                    etat["url"] = m.group(0)
                    etat["cloudflared_ok"] = True
    # Le sous-processus s'est terminé (ou son tube est fermé) : si on n'a
    # jamais trouvé d'URL, c'est un échec à signaler à la page.
    with _verrou:
        if not etat["cloudflared_ok"]:
            etat["erreur"] = "Le tunnel Cloudflare s'est arrêté sans fournir d'URL."


# =====================================================================
# Serveur de contrôle (statut + arrêt), pour la page HTML
# =====================================================================

class Controleur(BaseHTTPRequestHandler):
    """Micro-API locale : GET /status (JSON d'état) et GET /stop (arrêt propre)."""

    def _repondre_json(self, data: dict, code: int = 200) -> None:
        corps = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        # Autorise l'appel depuis la page HTML ouverte en file:// (origine "null").
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self) -> None:  # noqa: N802 (nom imposé par BaseHTTPRequestHandler)
        if self.path.startswith("/status"):
            with _verrou:
                self._repondre_json(dict(etat))
        elif self.path.startswith("/stop"):
            self._repondre_json({"arret": True})
            # L'arrêt se fait dans un thread à part : répondre d'abord,
            # sans quoi le serveur se couperait avant d'avoir envoyé la réponse.
            threading.Thread(target=arreter_tout, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # Silence le journal par défaut (verbeux) sur la console.


def demarrer_serveur_controle() -> None:
    httpd = ThreadingHTTPServer((HOTE, PORT_CONTROLE), Controleur)
    serveur_controle["instance"] = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def arreter_tout() -> None:
    """Termine les sous-processus et le serveur de contrôle, puis signale l'arrêt."""
    for proc in processus.values():
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    httpd = serveur_controle.get("instance")
    if httpd is not None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    evenement_arret.set()


# =====================================================================
# Page HTML (lanceur + erreur)
# =====================================================================

_PAGE_LANCEUR = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LudoteX — Lanceur</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #ffffff; color: #1a1a1a;
    font-family: -apple-system, "Segoe UI", Arial, sans-serif; padding: 24px;
  }
  .carte { max-width: 480px; width: 100%; text-align: center; }
  h1 { font-size: 1.4rem; margin-bottom: .25rem; }
  .sous-titre { color: #555; margin-bottom: 1.5rem; font-size: .95rem; }
  .qr { width: 300px; height: 300px; border: 1px solid #e2e2e2; border-radius: 8px; padding: 12px; }
  .url {
    margin: 1.25rem 0; font-family: "Consolas", "Courier New", monospace;
    font-size: 1.1rem; word-break: break-all; user-select: all;
    background: #f5f5f5; padding: 10px 14px; border-radius: 6px;
  }
  .url a { color: #1a1a1a; text-decoration: none; }
  .statuts { display: flex; justify-content: center; gap: 20px; margin: 1rem 0 1.5rem; font-size: .9rem; }
  .puce { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; background: #ccc; }
  .puce.ok { background: #2e9e4d; }
  .puce.ko { background: #d5342a; }
  .arreter {
    background: #d5342a; color: #fff; border: none; padding: 14px 28px;
    font-size: 1.05rem; border-radius: 8px; cursor: pointer;
  }
  .arreter:hover { background: #b52a22; }
  .msg { margin-top: 1rem; font-size: .85rem; color: #777; min-height: 1.2em; }
  .formation {
    margin: 0 0 1.25rem; padding: 10px 14px; border-radius: 6px;
    background: #fff3e0; border: 1px solid #ffcc80; color: #e65100;
    font-size: .9rem; word-break: break-all;
  }
  .formation a { color: #e65100; }
</style>
</head>
<body>
  <div class="carte">
    <h1>LudoteX est prêt</h1>
    <p class="sous-titre">Scanner ce QR depuis un smartphone pour accéder à l'application.</p>
    <img class="qr" src="data:image/png;base64,__QR_B64__" alt="QR d'accès à LudoteX">
    <div class="url"><a href="__URL__" target="_blank" rel="noopener">__URL__</a></div>
    <div class="statuts">
      <span><span id="puce-uvicorn" class="puce __CLASSE_UVICORN__"></span>Application</span>
      <span><span id="puce-tunnel" class="puce __CLASSE_TUNNEL__"></span>Tunnel</span>
    </div>
    __BLOC_FORMATION__
    <button class="arreter" onclick="arreterLudoteX()">Arrêter LudoteX</button>
    <p class="msg" id="message"></p>
  </div>
<script>
  const CONTROLE = "http://127.0.0.1:__PORT_CONTROLE__";

  async function maj() {
    try {
      const r = await fetch(CONTROLE + "/status", { cache: "no-store" });
      const s = await r.json();
      document.getElementById("puce-uvicorn").className = "puce " + (s.uvicorn_ok ? "ok" : "ko");
      document.getElementById("puce-tunnel").className = "puce " + (s.cloudflared_ok ? "ok" : "ko");
      document.getElementById("message").textContent = s.erreur || "";
    } catch (e) {
      document.getElementById("message").textContent =
        "Lanceur injoignable — LudoteX est peut-être déjà arrêté.";
    }
  }
  setInterval(maj, 5000);
  maj();

  function arreterLudoteX() {
    if (!confirm("Arrêter LudoteX ? Les bénévoles n'auront plus accès à l'application.")) return;
    fetch(CONTROLE + "/stop", { cache: "no-store" }).finally(() => {
      document.getElementById("message").textContent = "LudoteX est arrêté. Vous pouvez fermer cette page.";
    });
  }
</script>
</body>
</html>
"""

_PAGE_ERREUR = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LudoteX — Impossible de démarrer</title>
<style>
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #fff; color: #1a1a1a;
    font-family: -apple-system, "Segoe UI", Arial, sans-serif; padding: 24px;
  }
  .carte { max-width: 640px; }
  h1 { color: #d5342a; font-size: 1.3rem; }
  li { margin: .6rem 0; }
  code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }
  pre {
    background: #f5f5f5; border-left: 4px solid #d5342a; padding: 12px;
    overflow-x: auto; font-size: .8rem; line-height: 1.45; white-space: pre-wrap;
  }
</style>
</head>
<body>
  <div class="carte">
    <h1>Impossible de démarrer LudoteX</h1>
    <p>Le lanceur a rencontré un problème :</p>
    <ul>__ITEMS__</ul>
    __DETAIL__
    <p>Voir <code>docs/lancement-local.md</code> pour l'installation des prérequis.</p>
  </div>
</body>
</html>
"""


def generer_qr_base64(url: str) -> str:
    """PNG du QR de `url`, encodé en base64 (pour un <img src="data:...">)."""
    # Import tardif : évite de dépendre de app/ tant que les prérequis ne sont
    # pas vérifiés (le module réutilise le MÊME dessin de QR que le reste de
    # l'application — app/etiquettes.py — plutôt que de le dupliquer).
    from app.etiquettes import image_qr_nu

    img = image_qr_nu(url, box=10)
    tampon = io.BytesIO()
    img.save(tampon, format="PNG")
    return base64.b64encode(tampon.getvalue()).decode("ascii")


def ecrire_temp_html(html: str, prefixe: str) -> Path:
    """Écrit `html` dans un fichier temporaire et renvoie son chemin."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix=prefixe, delete=False, encoding="utf-8"
    )
    try:
        f.write(html)
    finally:
        f.close()
    return Path(f.name)


def ecrire_page_lanceur(url: str) -> Path:
    qr_b64 = generer_qr_base64(url)
    with _verrou:
        s = dict(etat)
    url_formation = s.get("url_formation")
    if url_formation:
        bloc_formation = (
            f'<div class="formation">🎓 Site de formation (sur cet ordinateur) : '
            f'<a href="{url_formation}" target="_blank" rel="noopener">'
            f'{url_formation}</a></div>'
        )
    else:
        bloc_formation = ""
    html = (
        _PAGE_LANCEUR
        .replace("__QR_B64__", qr_b64)
        .replace("__URL__", url)
        .replace("__PORT_CONTROLE__", str(PORT_CONTROLE))
        .replace("__CLASSE_UVICORN__", "ok" if s["uvicorn_ok"] else "ko")
        .replace("__CLASSE_TUNNEL__", "ok" if s["cloudflared_ok"] else "ko")
        .replace("__BLOC_FORMATION__", bloc_formation)
    )
    return ecrire_temp_html(html, prefixe="lancer-ludotex-")


def _afficher_erreur(problemes: list[str], detail: str = "") -> None:
    """
    Affiche les problèmes dans la console ET dans une page HTML.

    `detail` est un extrait brut (la fin du journal d'uvicorn) rendu dans un
    bloc à part. Tout est échappé : une trace Python contient couramment des
    fragments comme `<module>`, que le navigateur prendrait pour du balisage.
    """
    for p in problemes:
        print("ERREUR :", p)
    if detail:
        print("--- Détail (fin du journal) ---")
        print(detail)
    items = "".join(f"<li>{html.escape(p)}</li>" for p in problemes)
    bloc = (f"<p>Détail technique (fin du journal) :</p>"
            f"<pre>{html.escape(detail)}</pre>") if detail else ""
    chemin = ecrire_temp_html(
        _PAGE_ERREUR.replace("__ITEMS__", items).replace("__DETAIL__", bloc),
        prefixe="lancer-ludotex-erreur-",
    )
    webbrowser.open(chemin.as_uri())


# =====================================================================
# Orchestration
# =====================================================================

def lanceur_de_la_plateforme() -> str:
    """
    Comment relancer LudoteX sur CETTE machine, en clair.

    Le message d'échec citait « lancer.bat » en dur, un fichier Windows :
    sous macOS et Linux, il désignait quelque chose qui n'existe pas — et
    envoyait donc chercher la solution du mauvais côté.
    """
    if sys.platform == "win32":
        return "lancer.bat"
    if sys.platform == "darwin":
        return "lancer.command"
    return ".venv/bin/python lancer.py"


def _echec_demarrage(cle: str, quoi: str) -> tuple[list[str], str]:
    """
    Le message d'échec d'un uvicorn qui n'a pas ouvert son port, et l'extrait
    de journal qui l'accompagne.

    Le CHEMIN COMPLET du journal est cité : c'est ce qu'on copie-colle pour
    demander de l'aide, et un chemin relatif obligerait à deviner depuis quel
    dossier le lire.
    """
    chemin = journal_uvicorn(cle)
    fin = dernieres_lignes(chemin)
    messages = [f"{quoi} n'a pas démarré à temps."]
    if fin:
        messages.append(f"Le détail de l'erreur est ci-dessous, et le journal "
                        f"complet dans : {chemin}")
    else:
        messages.append(
            f"Aucun détail n'a pu être enregistré (journal attendu : {chemin}). "
            f"Relancer avec {lanceur_de_la_plateforme()} pour voir les messages "
            f"dans la console."
        )
    return messages, fin


def demarrer_instance_formation() -> bool:
    """
    Prépare (au 1er lancement) puis démarre l'instance de formation sur
    PORT_FORMATION. Renvoie True si elle est joignable. Un échec ici n'est PAS
    bloquant pour l'instance normale : on le signale et on continue.
    """
    try:
        preparer_bases_formation()
    except subprocess.CalledProcessError:
        print("ERREUR : impossible d'initialiser les bases de formation.")
        return False

    print("Démarrage du site de formation (uvicorn)…")
    demarrer_uvicorn("uvicorn_formation", PORT_FORMATION, env_formation())
    if not _attendre_port(PORT_FORMATION, timeout=30):
        # Non bloquant pour l'instance normale : on informe, on continue.
        messages, detail = _echec_demarrage("uvicorn_formation",
                                            "Le site de formation")
        for ligne in messages:
            print("ERREUR :", ligne)
        if detail:
            print(detail)
        return False

    url_formation = f"http://localhost:{PORT_FORMATION}"
    with _verrou:
        etat["formation_ok"] = True
        etat["url_formation"] = url_formation
    print("Site de formation démarré sur", url_formation)
    return True


def main(formation: bool = False) -> None:
    problemes = verifier_prerequis(formation)
    if problemes:
        _afficher_erreur(problemes)
        return

    chemin_cloudflared = trouver_cloudflared()
    assert chemin_cloudflared is not None  # déjà vérifié par verifier_prerequis()

    demarrer_serveur_controle()

    # Si l'on veut le site de formation, on le démarre AVANT l'instance normale
    # pour lui passer FORMATION_URL (→ le lien « Site de formation » apparaît
    # dans l'admin de l'instance normale).
    env_normale: dict[str, str] | None = None
    if formation:
        if demarrer_instance_formation():
            env_normale = {"FORMATION_URL": f"http://localhost:{PORT_FORMATION}"}

    print("Démarrage de l'application (uvicorn)…")
    demarrer_uvicorn("uvicorn", PORT_APP, env_normale)
    if not _attendre_port(PORT_APP, timeout=30):
        messages, detail = _echec_demarrage("uvicorn", "L'application")
        _afficher_erreur(messages, detail)
        arreter_tout()
        return
    with _verrou:
        etat["uvicorn_ok"] = True
    print("Application démarrée sur le port", PORT_APP)

    print("Ouverture du tunnel Cloudflare…")
    demarrer_cloudflared(chemin_cloudflared)

    fin = time.time() + 45
    while time.time() < fin and not evenement_arret.is_set():
        with _verrou:
            if etat["url"]:
                break
        time.sleep(0.5)

    with _verrou:
        url = etat["url"]

    if not url:
        _afficher_erreur([
            "Le tunnel Cloudflare n'a pas fourni d'URL publique dans le délai "
            "imparti. Vérifier la connexion internet, ou relancer avec "
            f"{lanceur_de_la_plateforme()} pour voir le détail de l'erreur "
            "dans la console."
        ])
        arreter_tout()
        return

    print("URL publique :", url)

    chemin_page = ecrire_page_lanceur(url)
    webbrowser.open(chemin_page.as_uri())
    with _verrou:
        url_formation = etat.get("url_formation")
    if url_formation:
        print("Site de formation accessible sur cet ordinateur :", url_formation)
    print("LudoteX est prêt. Fermer cette console ou cliquer sur "
          "« Arrêter LudoteX » dans la page pour tout arrêter.")

    try:
        while not evenement_arret.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArrêt demandé (Ctrl+C)…")
    finally:
        arreter_tout()
        try:
            chemin_page.unlink(missing_ok=True)
        except Exception:
            pass
        print("LudoteX est arrêté.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print("Usage : python lancer.py [--formation]")
        print("  (sans option)  démarre l'application normale")
        print("  --formation    démarre EN PLUS le site de formation "
              "(port 8100, bases jetables)")
        sys.exit(0)
    # AVANT toute autre chose : si l'on n'est pas dans le venv du projet, on s'y
    # remet (voir `relancer_dans_le_venv`). `os.execv` ne rend pas la main.
    relancer_dans_le_venv(args)
    main(formation="--formation" in args)
