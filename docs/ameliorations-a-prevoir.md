# Améliorations à prévoir — backlog

Liste vivante des modifications à intégrer lors d'une prochaine session de
développement. Alimentée par les retours du CA et des bénévoles. Quand un point
est traité, le cocher (et le décrire dans `CLAUDE.md`).

Format d'un point : intitulé, besoin, décisions/notes de mise en œuvre.

---

## À faire

### 1. Bouton admin « Clôturer tous les prêts en cours » (fin d'événement)
- **Besoin** : après l'événement, remettre tout le parc « disponible » et libérer
  les numéros d'emplacement, pour repartir propre à la session suivante.
- **Décision** : on CLÔTURE les prêts en cours, on **ne supprime PAS** l'historique
  (les statistiques restent ; les stats par édition s'obtiennent via le filtre de
  période). La suppression complète de l'historique n'est pas exposée.
- **Mise en œuvre prévue** :
  - `services.cloturer_tous_les_prets(conn)` : `UPDATE prets SET date_retour =
    maintenant WHERE date_retour IS NULL` + `UPDATE pochettes SET occupe = 0` ;
    renvoie le nombre de prêts clôturés.
  - Route POST protégée `/admin/cloturer-prets` + bouton dans une section
    « Fin d'événement » du tableau de bord admin.
  - Sécurité : confirmation explicite (comme la réinitialisation du jeton),
    message de retour « X prêts clôturés », et rappel d'exporter les
    statistiques au préalable.
  - Pas de changement du modèle de données.

### 2. Vue « Jeux actuellement sortis » (depuis la page statistiques)
- **Besoin** : voir d'un coup d'œil la liste des jeux encore dehors (relancer les
  retours manquants, surtout en fin d'événement).
- **Emplacement** : accessible depuis la page **Statistiques** (section dédiée,
  par ex. en haut, ou via un lien/onglet).
- **Contenu** : nom du jeu, code (`id_exemplaire`), **numéro d'emplacement**,
  date/heure de sortie (en heure locale). Trié par date de sortie.
- **Mise en œuvre prévue** :
  - `services.lister_prets_en_cours(conn)` : `SELECT ... FROM prets p JOIN
    exemplaires e JOIN titres t WHERE p.date_retour IS NULL ORDER BY
    p.date_sortie`.
  - Affichage dans `stats.html` (réutiliser le style de la table « Détail »).
  - Bon compagnon du bouton « clôturer tous les prêts » (point 1) : on visualise
    ce qui reste sorti avant de clôturer.

---

## Retours en attente de tri
_(à compléter au fil des messages du CA — idées, bugs, ajustements)_

-
