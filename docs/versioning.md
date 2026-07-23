# Versionnage — marche à suivre

LudoteX suit le schéma **`MAJEUR.MINEUR.CORRECTIF`** (SemVer). Ce document dit
comment choisir le prochain numéro et quels fichiers mettre à jour à chaque
montée de version.

## Choisir le numéro

Partant de la version actuelle `MAJEUR.MINEUR.CORRECTIF`, on incrémente **un
seul** des trois nombres et on remet à zéro ceux de droite :

- **CORRECTIF** (`1.0.0` → `1.0.1`) — corrections de bugs, retouches
  d'interface, de texte ou d'accessibilité, optimisations internes. Rien de
  nouveau du point de vue de l'utilisateur.
- **MINEUR** (`1.0.1` → `1.1.0`) — une nouvelle fonctionnalité ou un nouveau
  module, sans casser l'existant (ex. double élimination des tournois, envoi
  d'e-mails, nouvelle option d'administration).
- **MAJEUR** (`1.4.2` → `2.0.0`) — une grande étape ou un changement de cap :
  refonte d'un pan de l'application, changement de fonctionnement visible et
  structurant, ou évolution qui demande une intervention à la mise à jour
  (au-delà d'un simple `update.sh`). `1.0.0` = première mise en production.

En cas de doute entre deux niveaux, prendre le plus élevé.

## Les fichiers à mettre à jour (dans le même commit)

1. **`app/version.py`** — la constante `APP_VERSION` (le numéro canonique, sans
   « v » ni date : `"1.1.0"`).
2. **`VERSION`** (racine) — le même numéro, la date et un résumé d'une ligne,
   par ex. `LudoteX 1.1.0 — 2026-09-05 — Double élimination des tournois`.
   C'est ce que lit l'écran `/admin/supervision`.
3. **`CHANGELOG.md`** — ajouter une section en haut, sous ce format :

   ```markdown
   ## 1.1.0 — 2026-09-05

   - Description d'une évolution, tournée vers l'utilisateur.
   - ...
   ```

   Les puces de la section la plus récente sont affichées telles quelles sur la
   page « À propos » (« Nouveautés de cette version ») : les garder claires,
   courtes et sans jargon technique.

Le numéro doit être **identique** dans `app/version.py`, `VERSION` et l'entête
de la section `CHANGELOG.md`.

## Poser le tag git (après le push)

Une fois le commit poussé sur GitHub :

```bash
git tag -a v1.1.0 -m "LudoteX 1.1.0"
git push origin v1.1.0
```

Le tag (préfixé `v`) crée un point de repère durable et une page « Release » sur
GitHub. Il n'est pas indispensable au fonctionnement de l'application, mais
facilite le retour à une version précise et la lecture de l'historique.

## Où la version apparaît

- Page publique **`/apropos`** : numéro + nouveautés de la version courante.
- Écran **`/admin/supervision`** : « version déployée » (lue depuis `VERSION`).
- **`INFO.txt`** de chaque sauvegarde (zip) : trace la version qui a produit la
  sauvegarde.
- Métadonnées **FastAPI** (`/docs`).

## Rappel de synchronisation

Historiquement, le numéro vivait à deux endroits (`app/version.py` et `VERSION`)
qui avaient divergé. Depuis la 1.0.0, la règle est simple : **un seul numéro,
recopié à l'identique** dans les trois fichiers ci-dessus. Ne jamais en changer
un sans les autres.
