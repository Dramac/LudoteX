# Brief — étude « choix de l'hébergement »

But de ce document : cadrer une **étude comparative (avec recherche web)** pour
choisir l'hébergement, à mener dans un **nouveau chat du même projet**. À lire
avec `docs/budget.md` (scénarios chiffrés) et `docs/specification.md` §10
(architecture d'hébergement). Aucune décision ici : on prépare la recherche.

## Ce qu'il faut décider

Deux **briques cloisonnées**, sous un nom de domaine unique (sous-domaines) :

1. **Brique « prêt » (technique) — un VPS.** Héberge l'app FastAPI/uvicorn (ce
   dépôt) + le catalogue public. Besoins réels :
   - Linux (Debian/Ubuntu), accès SSH, tourne à l'année.
   - Charge **très faible** (quelques écritures/minute, une poignée de bénévoles)
     → **1 vCPU / 1–2 Go RAM** largement suffisant ; SQLite (pas de SGBD lourd).
   - nginx + HTTPS Let's Encrypt (déjà outillé dans `deploy/` + `docs/deploiement.md`).
   - Sauvegardes / snapshots appréciés.
2. **Brique « site + newsletter » (éditorial) — hébergement mutualisé.** WordPress
   éditable par des bénévoles non techniciens + newsletter (outil intégré ou
   **Brevo**, palier gratuit conforme RGPD).

## Candidats déjà pré-retenus (à réévaluer)

- **Infomaniak** (Suisse, forte démarche écologique ; adéquation UE) — VPS + Web.
- **o2switch**, **PlanetHoster** (France, mutualisé ; o2switch réputé sans hausse
  au renouvellement).
- **OVHcloud**, **Ikoula**, **Scaleway** (France, VPS).
- **Hetzner** (Allemagne) si le prix prime.

## Critères de décision (à pondérer par le bureau)

- **Souveraineté / RGPD** : Europe (FR de préférence) ; Suisse couverte par une
  décision d'adéquation UE.
- **Empreinte écologique / RSE**.
- **Prix initial ET au renouvellement** (point de vigilance : certains acteurs,
  OVH notamment, augmentent au renouvellement ; Infomaniak/o2switch réputés stables).
- **Support humain en français**, simplicité pour des non-techniciens (mutualisé).
- **Budget cible** : ~110–145 €/an pour l'ensemble (voir les 3 scénarios A/B/C de
  `docs/budget.md`).

## À VÉRIFIER PAR RECHERCHE WEB (prix/offres 2026 — ne pas se fier à la mémoire)

- Tarifs et specs actuels des **VPS** (Infomaniak VPS Lite, OVH, Ikoula, Scaleway,
  Hetzner) et des offres **mutualisées WordPress** (o2switch, PlanetHoster,
  Infomaniak Web).
- **Conditions de renouvellement** (hausse éventuelle) et engagement.
- Localisation des datacenters / conformité RGPD.
- Offre **newsletter** (Brevo : limites du palier gratuit ; option intégrée
  Infomaniak).
- Prix d'un **nom de domaine** `.fr`/`.eu`.

> Rappel méthodo : les prix et offres changent — **toujours vérifier sur les pages
> tarifaires officielles des hébergeurs au moment de l'étude**, et citer les
> sources/dates.

## Livrable attendu

Un **tableau comparatif** (hébergeur × critères, avec prix datés et sources) et
une **recommandation** selon le critère prioritaire retenu par le bureau (éco /
souveraineté+budget / cloud). Idéalement un court document (`docs/` ou présentation)
exploitable en réunion de CA.

## Phrase d'amorçage du nouveau chat

> « Étude hébergement : lis `docs/etude-hebergement-brief.md`, `docs/budget.md` et
> `docs/specification.md` §10, puis fais une recherche web à jour et propose un
> comparatif + une recommandation. »
