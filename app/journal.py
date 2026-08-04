"""
Journal d'activité — socle d'écriture (lot B, étape 3 de
docs/conception-journal.md).

Ce module ne connaît AUCUN point d'appel métier : c'est le lot C qui posera
les appels depuis les routes (§5.2 — jamais depuis les services). Ici :
- le vocabulaire FERMÉ des actions (§3.2) ;
- la fonction unique `journaliser()` qui construit une ligne et l'émet ;
- la configuration du logger dédié `ludotex.journal` (fichier tournant +
  console optionnelle) ;
- la lecture des dernières lignes d'un fichier sans jamais le charger en
  entier, réutilisée à la fois par l'écran `/admin/journal` et par
  `scripts/journal.py`.

IMPÉRATIF ABSOLU (§5.1) : `journaliser()` NE LÈVE JAMAIS. Disque plein,
permissions refusées, fichier verrouillé : tout est avalé. Un journal qui
empêche un prêt est pire que pas de journal du tout.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path

from app import admin_auth, auth, services
from app.config import JOURNAL_CONSOLE
from app.db import get_connection
from app.services import FUSEAU_LOCAL

LOGGER_NAME = "ludotex.journal"

# Nom sur lequel les avertissements internes (fichier illisible, action hors
# vocabulaire…) sont émis — le logger standard d'uvicorn, déjà surveillé en
# production (voir app/main.py, qui l'utilise pour le filet 500 et l'alerte
# jeton absent).
_LOGGER_AVERTISSEMENTS = "uvicorn.error"

# ---------------------------------------------------------------------------
# Vocabulaire fermé (§3). Une seule liste de constantes : toute valeur qui
# n'y figure pas est REFUSÉE par `journaliser()` (rien n'est écrit), pas
# ignorée en silence côté avertissement — sans quoi on se retrouve avec
# `retour`, `rendu` et `rendre` dans le même fichier six mois plus tard.
#
# Construite dès cette étape à partir des tables §2.1/§2.2/§2.3 de la
# conception, alors même qu'aucun appel n'existe encore (lot C) : le test de
# vocabulaire fermé n'a de sens que si la liste est déjà celle qui sera
# utilisée en vrai, pas un sous-ensemble provisoire.
# ---------------------------------------------------------------------------
MODULES = {
    "pret", "catalogue", "tournois", "programme", "planning",
    "rangement", "live", "stats", "admin",
}

ACTIONS = {
    # --- Priorité 1 — configuration et administration (§2.1) --------------
    "jeton_reinitialise",
    "motdepasse_change",
    "connexion_reussie",
    "connexion_echouee",
    "import_csv",
    "sauvegarde_restauree",
    "cloture_prets",
    "module_modifie",
    "rangement_contexte_modifie",
    "rangement_visibilite_modifiee",
    "rangement_lot_applique",
    "annonce_posee",
    "annonce_effacee",
    "evenement_date_modifiee",
    "planning_purge",
    "formation_reinitialisee",
    "jeu_cree",
    "exemplaire_ajoute",
    # --- Priorité 2 — tournois, programme, planning (§2.2) -----------------
    "tournoi_cree",
    "tournoi_modifie",
    "tournoi_supprime",
    "tournoi_etat_change",
    "tournoi_lance",
    "tournoi_resultats_saisis",
    "participant_ajoute",
    "participant_supprime",
    "tournois_jour_ouverts",
    "programme_cree",
    "programme_modifie",
    "programme_supprime",
    "programme_etat_change",
    "programme_type_cree",
    "programme_type_modifie",
    "programme_type_supprime",
    "planning_questionnaire_ferme",
    "planning_genere",
    "planning_publie",
    "planning_case_modifiee",
    # --- Priorité 3 — prêts, malgré la redondance (§2.3) -------------------
    "pret",
    "retour",
    "re_pret",
    "sortie_tournoi",
}

# Longueurs de troncature (§3.3) : assainissement obligatoire, pas défensif.
_LONGUEUR_OBJET = 120
_LONGUEUR_DETAIL = 120
_LONGUEUR_REF = 60


def _assainir(texte: str, longueur: int) -> str:
    """Retours à la ligne -> espaces, puis troncature. `json.dumps` fait le reste."""
    return texte.replace("\r", " ").replace("\n", " ")[:longueur]


def _avertir(message: str, *args) -> None:
    """Émet un avertissement sur le logger d'uvicorn, sans jamais lever."""
    try:
        logging.getLogger(_LOGGER_AVERTISSEMENTS).warning(message, *args)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Détermination de « qui » (§5.3)
# ---------------------------------------------------------------------------
def _qui(request) -> str:
    """
    admin_auth.admin_connecte(request)  -> "admin"
    (mode ouvert : aucun jeton configuré) -> "indetermine"
    auth.acces_valide(request)          -> "benevole"
    sinon                               -> "visiteur"

    L'ORDRE COMPTE : un administrateur connecté a aussi `acces_valide` vrai
    (voir `auth.peut_ecrire`) ; le tester en premier étiquetterait toute
    l'activité d'administration comme bénévole.

    Le mode ouvert doit être détecté explicitement (pas via `acces_valide`,
    qui renvoie vrai pour tout le monde dans ce cas) : sinon tout le trafic
    d'une installation de développement serait étiqueté « bénévole ».
    """
    if admin_auth.admin_connecte(request):
        return "admin"
    conn = get_connection()
    try:
        jeton_configure = auth.jeton_actuel(conn) is not None
    finally:
        conn.close()
    if not jeton_configure:
        return "indetermine"
    if auth.acces_valide(request):
        return "benevole"
    return "visiteur"


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
def journaliser(
    request,
    module: str,
    action: str,
    *,
    objet: str | None = None,
    ref: str | None = None,
    ok: bool = True,
    detail: str | None = None,
) -> None:
    """
    Construit une ligne JSON et l'émet sur le logger `ludotex.journal`.

    Args:
        request: la requête FastAPI (ou tout objet portant `.cookies`,
            suffisant pour tout ce que cette fonction interroge — c'est ce
            que les tests utilisent).
        module, action: vocabulaire FERMÉ (voir MODULES / ACTIONS). Une
            valeur inconnue -> rien n'est écrit, un avertissement est émis.
        objet: libellé lisible de ce sur quoi on a agi (assaini, tronqué).
        ref: clé technique stable (reference_titre, id_tournoi…).
        ok: True si l'action a abouti.
        detail: texte court, utile seulement quand ok est False.

    Ne lève JAMAIS : toute exception est avalée, au pire un avertissement est
    émis sur `uvicorn.error`.
    """
    try:
        if module not in MODULES:
            _avertir(
                "Journal d'activité : module hors vocabulaire ignoré (%r) — "
                "ajouter la constante dans app/journal.py avant de journaliser.",
                module,
            )
            return
        if action not in ACTIONS:
            _avertir(
                "Journal d'activité : action hors vocabulaire ignorée (%r) — "
                "ajouter la constante dans app/journal.py avant de journaliser.",
                action,
            )
            return

        qui = _qui(request)
        appareil = services.appareil_de(request) if qui != "visiteur" else None

        ligne: dict = {
            "t": datetime.now(FUSEAU_LOCAL).isoformat(timespec="seconds"),
            "qui": qui,
        }
        if appareil:
            ligne["appareil"] = appareil
        ligne["module"] = module
        ligne["action"] = action
        if objet:
            ligne["objet"] = _assainir(str(objet), _LONGUEUR_OBJET)
        if ref:
            ligne["ref"] = _assainir(str(ref), _LONGUEUR_REF)
        ligne["ok"] = bool(ok)
        if not ok and detail:
            ligne["detail"] = _assainir(str(detail), _LONGUEUR_DETAIL)

        logging.getLogger(LOGGER_NAME).info(
            json.dumps(ligne, ensure_ascii=False, separators=(",", ":"))
        )
    except Exception as exc:  # ne jamais lever — cf. docstring du module
        _avertir("Journal d'activité : écriture impossible (%s)", exc)


# ---------------------------------------------------------------------------
# Sortie formatée pour l'œil (§5.5) — PAS du JSON, réutilisée par le
# StreamHandler console ET par scripts/journal.py, pour n'avoir qu'un seul
# endroit qui décide de la mise en forme humaine.
# ---------------------------------------------------------------------------
def formater_console(ligne: dict) -> str:
    """
    '12:37:02  benevole 3F1A9C  pret       retour            7 Wonders Duel'

    Colonnes à largeur fixe, sans couleur (le terminal de destination est un
    SSH sur VPS, pas forcément un terminal riche). Échec marqué en clair.
    """
    t = ligne.get("t") or ""
    heure = t[11:19] if len(t) >= 19 else t
    qui = (ligne.get("qui") or "")[:10]
    appareil = (ligne.get("appareil") or "")[:8]
    module = (ligne.get("module") or "")[:10]
    action = (ligne.get("action") or "")[:18]
    objet = ligne.get("objet") or ""
    base = f"{heure}  {qui:<10}{appareil:<8}{module:<10} {action:<18}{objet}"
    if ligne.get("ok") is False:
        detail = ligne.get("detail")
        base += f"  [ÉCHEC{' — ' + detail if detail else ''}]"
    return base


class _FormateurConsole(logging.Formatter):
    """Reçoit la ligne JSON déjà sérialisée (le message du record) et la
    restitue au format humain. Une ligne illisible retombe sur le message brut
    plutôt que de faire planter la sortie console."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:
            ligne = json.loads(message)
        except (ValueError, TypeError):
            return message
        return formater_console(ligne)


def chemin_journal() -> Path:
    """
    Chemin du fichier journal, LU EN DIRECT dans l'environnement (comme
    `_base_url` dans routes/admin.py lit `BASE_URL`) plutôt que via la
    constante `JOURNAL_PATH` importée de `app.config` (figée à l'import du
    process) — c'est ce qui permet à la fixture de test de rediriger le
    fichier sans avoir à recharger le module.
    """
    return Path(os.getenv("JOURNAL_PATH", "data/journal.log"))


# ---------------------------------------------------------------------------
# Configuration du logger (§5.4/§5.5)
# ---------------------------------------------------------------------------
def configurer(journal_path: str | Path | None = None, console: bool | None = None) -> None:
    """
    (Re)configure le logger `ludotex.journal`. Appelée une fois au démarrage
    de l'application (app/main.py, à côté des `init_db()`), et par la fixture
    de test qui redirige `JOURNAL_PATH` vers un `tmp_path` avant chaque test —
    d'où le nettoyage des anciens handlers à chaque appel (idempotent).

    N'importe quel souci d'ouverture du fichier (dossier illisible, chemin
    invalide…) est avalé : le logger reste alors sans handler de fichier, donc
    silencieux, mais `journaliser()` continue de ne jamais lever pour autant.
    """
    chemin = Path(journal_path) if journal_path is not None else chemin_journal()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # sans quoi chaque ligne serait recopiée dans les logs uvicorn

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        fichier = logging.handlers.RotatingFileHandler(
            chemin, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        fichier.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(fichier)
    except OSError as exc:
        _avertir(
            "Journal d'activité : impossible d'ouvrir %s (%s) — le journal "
            "restera silencieux.", chemin, exc,
        )

    console_actif = JOURNAL_CONSOLE if console is None else console
    if console_actif:
        flux = logging.StreamHandler()
        flux.setFormatter(_FormateurConsole())
        logger.addHandler(flux)


# ---------------------------------------------------------------------------
# Lecture des dernières lignes, par blocs depuis la fin (§6.1/§7)
# ---------------------------------------------------------------------------
def lire_dernieres_lignes(
    chemin: str | Path, limite: int = 200, taille_bloc: int = 8192
) -> list[str]:
    """
    Renvoie au plus `limite` dernières lignes non vides d'un fichier, dans
    l'ordre CHRONOLOGIQUE (la plus ancienne d'abord), sans jamais charger le
    fichier entier en mémoire — on remonte depuis la fin par blocs de
    `taille_bloc` octets.

    Fichier absent ou vide -> liste vide (pas une erreur).
    """
    chemin = Path(chemin)
    try:
        taille = chemin.stat().st_size
    except OSError:
        return []
    if taille == 0:
        return []

    morceaux: list[bytes] = []
    lignes_comptees = 0
    with open(chemin, "rb") as fh:
        reste = b""
        position = taille
        while position > 0 and lignes_comptees <= limite:
            lire = min(taille_bloc, position)
            position -= lire
            fh.seek(position)
            bloc = fh.read(lire) + reste
            parties = bloc.split(b"\n")
            reste = parties[0]
            nouvelles = parties[1:]
            morceaux[0:0] = nouvelles
            lignes_comptees += len(nouvelles)
        if position == 0 and reste:
            morceaux.insert(0, reste)

    texte = [m.decode("utf-8", errors="ignore") for m in morceaux]
    texte = [l for l in texte if l.strip()]
    return texte[-limite:]
