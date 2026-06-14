# Topo budgétaire — Système de prêt de jeux et site de l'association

**À l'attention du bureau, pour validation**

---

## En une phrase

Mettre en place un système de prêt numérique (QR code + base en ligne) pour remplacer la feuille papier, ainsi qu'un site internet d'association avec newsletter, pour un coût récurrent estimé entre **110 et 120 €/an**, chez un hébergeur européen (de préférence français). Trois scénarios sont présentés ci-dessous ; le bureau choisit selon le critère qui prime (souveraineté française, budget, ou empreinte écologique).

---

## Coûts récurrents annuels — trois scénarios

L'architecture comporte **deux briques** : un *hébergement mutualisé* pour le site WordPress + la newsletter (éditable par des bénévoles non techniciens), et un *VPS* pour l'application de prêt (Python, contrôle technique). La newsletter peut être intégrée à l'hébergeur ou déléguée à un outil externe (Brevo, entreprise française, palier gratuit conforme RGPD).

| | Site + newsletter | VPS application | Domaine | Newsletter | **Total / an** |
|---|---|---|---|---|---|
| **A — Tout Infomaniak** *(éco / Suisse)* | Hébergement Web ≈ 69 € | VPS Lite ≈ 36 € | ≈ 10–15 € | incluse (0 €) | **≈ 115–120 €** |
| **B — Tout français, budget** | o2switch ≈ 100 € *(ou PlanetHoster ≈ 72 €)* | OVH/Ikoula VPS ≈ 35–45 € | inclus / ≈ 10 € | Brevo (gratuit) | **≈ 110–145 €** |
| **C — Français, cloud dev** | o2switch / PlanetHoster ≈ 72–100 € | Scaleway ≈ 60 € | ≈ 10 € | Brevo (gratuit) | **≈ 140–170 €** |

*Prix indicatifs constatés en 2026, hors taxes ; TVA selon le statut de l'association. À confirmer à la commande. Attention aux tarifs de renouvellement chez certains acteurs (OVH notamment) ; Infomaniak et o2switch n'augmentent pas au renouvellement.*

**Lecture rapide :**
- **Critère écologique prioritaire** → scénario A (Infomaniak, rigueur RSE de référence, données en Suisse — couverte par la décision d'adéquation UE).
- **Souveraineté française + support humain + simplicité** → scénario B (o2switch très réactif en français + VPS OVH ou Ikoula).
- **Orientation cloud / évolutivité** → scénario C (Scaleway côté serveur).

Dans tous les cas, on reste dans le même ordre de grandeur (~110–145 €/an pour les options raisonnables).

## Coûts ponctuels

- **Développement de l'application de prêt** : réalisé en interne (avec assistance d'un outil d'IA), sans coût de prestation externe à ce stade.
- **Impression des QR codes** à coller sur les ~700 jeux : coût matériel mineur (étiquettes), à chiffrer.

---

## Ce que le budget finance

1. **Un système de prêt sans goulet d'étranglement** : plusieurs bénévoles enregistrent prêts et retours en parallèle depuis leur smartphone, fini la file d'attente sur la feuille unique.
2. **Un suivi en temps réel** des jeux sortis / disponibles, et des **statistiques** après l'événement (jeux les plus / moins empruntés, fréquentation par heure…).
3. **Un site internet** modifiable par plusieurs bénévoles non techniciens (interface sans code), incluant le **catalogue public** des jeux.
4. **Une newsletter** pour informer le public (envois mensuels gratuits dans la limite incluse).

---

## Points d'attention pour le bureau

- **Léger dépassement d'un objectif initial à 100 €/an** : le surcoût (~15-20 €) finance le confort d'édition du site par les bénévoles et la newsletter. Une option « tout-en-un » moins chère existe mais dégraderait la fiabilité de l'outil de prêt ; elle n'est pas recommandée.
- **Engagement de maintenance** : l'application de prêt (VPS) nécessite un **référent technique** dans l'association pour les mises à jour et la sécurité. À identifier.
- **Nouvelle responsabilité RGPD liée à la newsletter** : collecter des adresses e-mail implique consentement explicite, lien de désinscription et une page de politique de confidentialité. L'application de prêt, elle, reste **totalement anonyme** (aucune donnée personnelle). Les deux sont cloisonnés.
- **Choix du fournisseur — à trancher par le bureau** selon le critère prioritaire. Tous les candidats retenus hébergent en Europe et sont conformes au RGPD : Infomaniak (Suisse, éco), o2switch et PlanetHoster (France, mutualisé), OVHcloud, Ikoula et Scaleway (France, VPS). La Suisse bénéficie d'une décision d'adéquation de l'UE.

---

## Décision demandée au bureau

- [ ] Choix du **scénario d'hébergement** (A éco / B français budget / C cloud) selon le critère prioritaire.
- [ ] Validation du **budget récurrent (~110–145 €/an selon scénario)**.
- [ ] Désignation d'un **référent technique** pour la maintenance.
- [ ] Accord pour la **réservation du nom de domaine**.
