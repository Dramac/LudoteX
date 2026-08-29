#!/bin/bash
# Point d'entrée « double-clic » pour démarrer LudoteX sur macOS — pendant de
# lancer.bat (Windows). Le Finder ouvre le Terminal et exécute ce fichier ; la
# console reste visible, ce qui est utile si quelque chose se passe mal.
#
# Ne fait qu'appeler lancer.py avec l'interpréteur du venv du projet : toute la
# logique (vérifications, uvicorn, tunnel, page HTML, arrêt) est dans lancer.py.
#
# Si le double-clic ouvre ce fichier dans un éditeur au lieu de l'exécuter,
# c'est que le bit d'exécution a été perdu. Le remettre une fois, dans le
# Terminal :  chmod +x lancer.command

cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "Environnement virtuel introuvable (dossier .venv)."
  echo "Installer d'abord l'application — voir docs/lancement-local.md."
  read -r -p "Appuyez sur Entrée pour fermer."
  exit 1
fi

# Les arguments éventuels sont transmis (ex. depuis le Terminal :
# ./lancer.command --formation).
.venv/bin/python lancer.py "$@"

read -r -p "Appuyez sur Entrée pour fermer."
