"""
Numéro de version de l'application — constante unique partagée.

Évite de dupliquer la chaîne de version entre `app/main.py` (métadonnées
FastAPI, visibles sur /docs) et la page publique `/apropos`. À incrémenter
manuellement à chaque évolution notable.
"""

APP_VERSION = "0.1.0"
