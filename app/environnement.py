"""
Chargement du fichier d'environnement — domicile UNIQUE.

POURQUOI UN MODULE À PART
-------------------------
Cinq modules appelaient chacun `load_dotenv()` sans argument (`config`, les
trois `db.py`, `scripts/journal.py`). python-dotenv choisit alors lui-même le
fichier, et pas toujours de la même façon :

- lancé par `python -m …`, par uvicorn ou par un script, il remonte depuis le
  dossier du MODULE appelant : `app/` → la racine du code ;
- lancé par `python -c …` ou `python - <<EOF` (c'est ainsi qu'`update.sh` et
  `sauvegarde.sh` dérivent le dossier des bases), il remonte depuis le dossier
  COURANT.

Et quel que soit le cas, un processus qui tourne depuis le code installé lit le
`.env` de ce dossier. C'est ainsi que l'instance de FORMATION, qui partage le
code de la production, héritait de toutes les clés du `.env` de production que
son propre fichier ne redéfinissait pas : le jeton bénévole (le site
d'entraînement répondait 403), et toute clé qui y serait ajoutée un jour, sans
que rien ne le signale (PROD-04).

LA RÈGLE
--------
Le fichier chargé est désigné par la variable `LUDOTEX_ENV_FILE` :

- ABSENTE : le `.env` à la racine du code (le dossier parent d'`app/`), quel
  que soit le dossier courant. C'est le cas de la production, du poste de
  développement et de tous les scripts : ils lisent le même fichier qu'avant ;
- un CHEMIN : ce fichier-là, et JAMAIS le `.env` de la racine, même s'il est
  introuvable. L'unité de formation pose ainsi son propre fichier
  (`deploy/ludotex-formation.service`) ; un script vise la formation de la
  même façon (`LUDOTEX_ENV_FILE=/etc/ludotex-formation.env python -m …`) ;
- VIDE : aucun fichier ; seules comptent les variables déjà présentes.

Comme avant, une variable déjà présente dans l'environnement n'est jamais
écrasée (`override=False`) : ce que systemd ou l'appelant a posé l'emporte.

Un chemin RELATIF dans une valeur (`DATABASE_PATH=data/pret-jeux.db`, le défaut)
se lit toujours depuis le dossier courant : c'est l'affaire de
`app.db.get_database_path`, pas de ce module. D'où les `cd` qui restent dans
les scripts de `deploy/`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Nom de la variable qui désigne le fichier d'environnement de l'instance.
VARIABLE_FICHIER_ENV = "LUDOTEX_ENV_FILE"

# Racine du code : le dossier qui contient `app/`. `abspath` et NON `resolve()`,
# comme python-dotenv avant ce module : une installation dont `app/` est un lien
# symbolique lit le `.env` posé à côté du lien, pas celui du dossier pointé.
RACINE = Path(os.path.abspath(__file__)).parent.parent


def fichier_env() -> Path | None:
    """
    Le fichier d'environnement que cette instance doit lire, ou None pour aucun.

    Relu à chaque appel (jamais figé à l'import) : les tests et les scripts
    posent la variable avant de charger.
    """
    valeur = os.environ.get(VARIABLE_FICHIER_ENV)
    if valeur is None:
        return RACINE / ".env"
    valeur = valeur.strip()
    return Path(valeur) if valeur else None


def charger_env() -> Path | None:
    """
    Charge le fichier d'environnement de l'instance, sans écraser l'existant.

    Sans effet si le fichier désigné n'existe pas (tests, poste sans `.env`).
    Un fichier présent mais illisible est signalé dans les journaux et ignoré :
    il ne fait pas tomber l'application, et le `.env` de la racine ne le
    remplace pas.

    Returns:
        Le chemin effectivement chargé, ou None.
    """
    chemin = fichier_env()
    if chemin is None or not chemin.is_file():
        return None
    try:
        load_dotenv(chemin, override=False)
    except OSError as exc:
        # Le type seul : le message pourrait citer le contenu du fichier.
        logging.getLogger("uvicorn.error").warning(
            "Fichier d'environnement %s illisible (%s) : non chargé.",
            chemin, type(exc).__name__,
        )
        return None
    return chemin
