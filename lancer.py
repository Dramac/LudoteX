"""
Lanceur autonome de LudoteX — pour un démarrage SANS ligne de commande.

Pensé pour les bénévoles (Windows, pas de compétence technique) : double-cliquer
sur `lancer.vbs` (silencieux, pas de fenêtre console) ou `lancer.bat` (avec
console, utile pour le débogage) suffit à tout démarrer.

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

AUCUNE DÉPENDANCE SUPPLÉMENTAIRE : `qrcode`/`pillow` sont déjà dans
`requirements.txt` (réutilise `app.etiquettes.image_qr_nu`, le même dessin de
QR que le reste de l'application) ; `http.server` est dans la bibliothèque
standard.

Prérequis détaillés (notamment l'installation de `cloudflared` sur Windows) :
voir `docs/lancement-local.md`.
"""

from __future__ import annotations

import base64
import io
import json
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

PORT_APP = 8000            # uvicorn
PORT_CONTROLE = 8001       # serveur de contrôle (statut + arrêt)
HOTE = "127.0.0.1"

URL_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# --- État partagé entre threads (uvicorn / cloudflared / serveur de contrôle) ---
etat: dict[str, object] = {
    "uvicorn_ok": False,
    "cloudflared_ok": False,
    "url": None,
    "erreur": None,
}
_verrou = threading.Lock()
evenement_arret = threading.Event()

processus: dict[str, subprocess.Popen | None] = {"uvicorn": None, "cloudflared": None}
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


def verifier_prerequis() -> list[str]:
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


def demarrer_uvicorn() -> None:
    """
    Lance uvicorn en sous-processus, caché.

    `sys.executable` est déjà l'interpréteur du venv du projet (garanti par
    lancer.vbs / lancer.bat, qui invoquent explicitement
    `.venv\\Scripts\\python[w].exe lancer.py`). `cwd=BASE_DIR` assure que
    `load_dotenv()` (dans app/db.py) retrouve le `.env` à la racine.
    """
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    processus["uvicorn"] = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", HOTE, "--port", str(PORT_APP)],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
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
  .carte { max-width: 540px; }
  h1 { color: #d5342a; font-size: 1.3rem; }
  li { margin: .6rem 0; }
  code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }
</style>
</head>
<body>
  <div class="carte">
    <h1>Impossible de démarrer LudoteX</h1>
    <p>Le lanceur a rencontré un problème :</p>
    <ul>__ITEMS__</ul>
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
    html = (
        _PAGE_LANCEUR
        .replace("__QR_B64__", qr_b64)
        .replace("__URL__", url)
        .replace("__PORT_CONTROLE__", str(PORT_CONTROLE))
        .replace("__CLASSE_UVICORN__", "ok" if s["uvicorn_ok"] else "ko")
        .replace("__CLASSE_TUNNEL__", "ok" if s["cloudflared_ok"] else "ko")
    )
    return ecrire_temp_html(html, prefixe="lancer-ludotex-")


def _afficher_erreur(problemes: list[str]) -> None:
    for p in problemes:
        print("ERREUR :", p)
    items = "".join(f"<li>{p}</li>" for p in problemes)
    chemin = ecrire_temp_html(
        _PAGE_ERREUR.replace("__ITEMS__", items), prefixe="lancer-ludotex-erreur-"
    )
    webbrowser.open(chemin.as_uri())


# =====================================================================
# Orchestration
# =====================================================================

def main() -> None:
    problemes = verifier_prerequis()
    if problemes:
        _afficher_erreur(problemes)
        return

    chemin_cloudflared = trouver_cloudflared()
    assert chemin_cloudflared is not None  # déjà vérifié par verifier_prerequis()

    demarrer_serveur_controle()

    print("Démarrage de l'application (uvicorn)…")
    demarrer_uvicorn()
    if not _attendre_port(PORT_APP, timeout=30):
        _afficher_erreur([
            "L'application n'a pas démarré à temps. Relancer via lancer.bat "
            "pour voir le détail de l'erreur dans la console."
        ])
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
            "imparti. Vérifier la connexion internet, ou relancer via "
            "lancer.bat pour voir le détail de l'erreur dans la console."
        ])
        arreter_tout()
        return

    print("URL publique :", url)

    chemin_page = ecrire_page_lanceur(url)
    webbrowser.open(chemin_page.as_uri())
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
    main()
