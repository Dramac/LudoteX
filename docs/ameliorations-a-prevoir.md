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

---

## Retours en attente de tri
_(à compléter au fil des messages du CA — idées, bugs, ajustements)_

-
