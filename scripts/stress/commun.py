"""
Briques partagées par les scripts de test de charge (voir
`docs/protocole-stress-test.md`).

Aucune dépendance nouvelle : `httpx` est déjà au `requirements.txt` (il sert
au `TestClient` de la suite de tests). Ces scripts s'exécutent depuis le POSTE
DE SIMON, pas sur le serveur : le but est de mesurer ce que voit un téléphone
de bénévole, réseau compris.

⚠️ À ne lancer QUE sur l'instance de FORMATION (voir le protocole). Ces scripts
écrivent réellement en base : prêts, retours, pochettes attribuées.
"""

from __future__ import annotations

import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

from app.auth import COOKIE_NAME  # nom du cookie du jeton bénévole (un seul domicile)


# ---------------------------------------------------------------------------
# CLIENT HTTP
# ---------------------------------------------------------------------------
def client(base_url: str, jeton: str, timeout: float = 30.0) -> httpx.AsyncClient:
    """
    Ouvre un client HTTP authentifié comme un téléphone de bénévole.

    Le jeton est posé directement en cookie (plutôt que de passer par
    `/acces?jeton=…`) pour deux raisons : ne pas consommer la limitation de
    débit par IP de la page d'activation, et pouvoir ouvrir des dizaines de
    clients sans multiplier les redirections.

    `follow_redirects=False` est volontaire : une 303 inattendue est un
    RÉSULTAT (jeton refusé, module coupé), pas quelque chose à suivre
    silencieusement.
    """
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        cookies={COOKIE_NAME: jeton},
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": "LudoteX-stress/1.0 (test de charge interne)"},
    )


# ---------------------------------------------------------------------------
# DÉCOUVERTE DES EXEMPLAIRES À MALMENER
# ---------------------------------------------------------------------------
_LIEN_JEU = re.compile(r'href="/jeu/([^"/?#]+)"')


async def decouvrir_exemplaires(base_url: str, jeton: str, limite: int = 200) -> list[str]:
    """
    Récupère des `id_exemplaire` en lisant les liens `/jeu/<id>` du catalogue.

    Le catalogue est public et ne liste qu'UN exemplaire représentatif par
    titre (le plus petit id) : on obtient donc autant de boîtes DISTINCTES que
    de titres, ce qui est exactement ce qu'il faut pour tirer des prêts
    simultanés sans se marcher dessus.

    Returns:
        Liste d'identifiants, dans l'ordre du catalogue, tronquée à `limite`.
    """
    async with client(base_url, jeton) as c:
        reponse = await c.get("/catalogue")
        reponse.raise_for_status()
        vus: list[str] = []
        for identifiant in _LIEN_JEU.findall(reponse.text):
            if identifiant not in vus:
                vus.append(identifiant)
    return vus[:limite]


# ---------------------------------------------------------------------------
# LECTURE DU RÉSULTAT D'UNE ACTION PRÊT / RETOUR
# ---------------------------------------------------------------------------
# La page `pret.html` affiche le résultat dans un bloc `.resultat`. On le relit
# ici pour savoir, côté client, ce que le serveur a réellement fait — sans
# ouvrir la base. Les motifs suivent le gabarit ; s'il change, ces expressions
# sont à revoir (le protocole le rappelle).
_NUMERO = re.compile(r'class="pochette-num[^"]*"[^>]*>\s*(\d+)')
_MOTIFS = [
    ("prete", re.compile(r">Pochette n°<")),
    ("repret", re.compile(r">Nouvelle pochette n°<")),
    ("rendu", re.compile(r"pochette-num--retour")),
    ("deja_sorti", re.compile(r"était déjà sorti")),
    ("deja_disponible", re.compile(r"était déjà disponible")),
    # Conflit d'accès simultané rattrapé par la route (correctif du 2026-08-02) :
    # la transaction a été annulée, RIEN n'a été enregistré, le bénévole
    # réappuie. Ce n'est pas une erreur, mais sa fréquence est un indicateur :
    # au rythme de 8 bénévoles elle devrait être nulle ou presque.
    ("occupe", re.compile(r"Rien n'a été enregistré")),
    ("tournoi_sorti", re.compile(r"Sorti pour un tournoi")),
    ("rendu_tournoi", re.compile(r"Retour de tournoi enregistré")),
]


def lire_resultat(html: str) -> tuple[str, int | None]:
    """
    Classe la réponse d'un POST prêt/retour.

    Returns:
        (type, numero_pochette) — le type vaut `"inconnu"` si aucun bloc de
        résultat n'a été reconnu (page d'erreur, redirection HTML, gabarit
        modifié). Le numéro est `None` quand l'action n'en affiche pas.
    """
    for nom, motif in _MOTIFS:
        if motif.search(html):
            trouve = _NUMERO.search(html)
            return nom, int(trouve.group(1)) if trouve else None
    return "inconnu", None


# ---------------------------------------------------------------------------
# COLLECTE DES MESURES
# ---------------------------------------------------------------------------
@dataclass
class Mesures:
    """Accumule les temps de réponse et les codes HTTP d'une campagne."""

    latences: dict[str, list[float]] = field(default_factory=dict)
    codes: Counter = field(default_factory=Counter)
    resultats: Counter = field(default_factory=Counter)
    incidents: list[str] = field(default_factory=list)
    debut: float = field(default_factory=time.monotonic)

    def relever(self, operation: str, duree: float, code: int | str) -> None:
        self.latences.setdefault(operation, []).append(duree * 1000)  # en ms
        self.codes[f"{operation} → {code}"] += 1

    def incident(self, message: str) -> None:
        """Enregistre une anomalie (au plus 200, pour ne pas noyer le rapport)."""
        if len(self.incidents) < 200:
            self.incidents.append(message)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.latences.values())

    def rapport(self) -> str:
        ecoule = time.monotonic() - self.debut
        lignes = [
            "",
            "=" * 68,
            f"  {self.total} requêtes en {ecoule:.0f} s "
            f"({self.total / max(ecoule, 1e-9):.1f} req/s)",
            "=" * 68,
            "",
            f"{'Opération':<28}{'n':>6}{'méd.':>9}{'p90':>9}{'p99':>9}{'max':>9}",
            "-" * 68,
        ]
        for operation, valeurs in sorted(self.latences.items()):
            tri = sorted(valeurs)
            lignes.append(
                f"{operation:<28}{len(tri):>6}"
                f"{statistics.median(tri):>8.0f}m"
                f"{_centile(tri, 90):>8.0f}m"
                f"{_centile(tri, 99):>8.0f}m"
                f"{tri[-1]:>8.0f}m"
            )
        lignes += ["", "Codes HTTP :"]
        for cle, nombre in sorted(self.codes.items()):
            marque = "  " if str(cle).endswith(("200", "303")) else "⚠ "
            lignes.append(f"  {marque}{cle:<44}{nombre:>6}")

        if self.resultats:
            lignes += ["", "Résultats métier :"]
            for cle, nombre in sorted(self.resultats.items()):
                lignes.append(f"    {cle:<44}{nombre:>6}")

        if self.incidents:
            lignes += ["", f"⚠ INCIDENTS ({len(self.incidents)}) :"]
            lignes += [f"    {ligne}" for ligne in self.incidents[:20]]
            if len(self.incidents) > 20:
                lignes.append(f"    … et {len(self.incidents) - 20} autres")
        else:
            lignes += ["", "Aucun incident détecté côté client."]
        lignes.append("")
        return "\n".join(lignes)


def _centile(tri: list[float], rang: int) -> float:
    """Centile simple sur une liste DÉJÀ triée (pas d'interpolation)."""
    if not tri:
        return 0.0
    indice = min(len(tri) - 1, int(round(rang / 100 * len(tri))) - 1)
    return tri[max(indice, 0)]
