# Étude « choix de l'hébergement » — comparatif et recommandation

**Pour le bureau / CA.** Donne suite au brief `docs/etude-hebergement-brief.md`,
au topo `docs/budget.md` et à la spec §10. Tous les prix ci-dessous ont été
**relevés par recherche web le 22 juin 2026**, sont **hors taxes** sauf mention,
et **doivent être reconfirmés sur les pages officielles à la commande** (les
tarifs bougent — plusieurs hausses ont eu lieu au printemps 2026, voir notes).

## Rappel du besoin (deux briques cloisonnées, un seul domaine)

1. **Brique « prêt » — un VPS.** App FastAPI/uvicorn + catalogue public. Charge
   très faible : **1 vCPU / 1–2 Go RAM** suffit largement, SQLite, Linux + SSH,
   tourne à l'année, nginx + HTTPS Let's Encrypt (déjà outillé dans `deploy/`).
   Sauvegarde déjà scriptée (`deploy/sauvegarde.sh`).
2. **Brique « site + newsletter » — mutualisé WordPress.** Éditable par des
   bénévoles non techniciens + newsletter (intégrée ou Brevo).

Critères pondérés par le bureau : souveraineté/RGPD (UE, FR de préférence),
empreinte écologique/RSE, **prix initial ET au renouvellement**, support humain
en français, simplicité pour non-techniciens, budget cible ~110–145 €/an.

## Brique VPS (application de prêt)

| Hébergeur (offre) | Specs | Prix HT relevé (06/2026) | ≈ /an | Localisation | À savoir |
|---|---|---|---|---|---|
| **Infomaniak — VPS Lite** (entrée) | 1 vCPU / 2 Go / 20 Go NVMe | ~2,60 €/mois, **−10 % en annuel** | **≈ 28–36 €** | Suisse (adéquation UE) | Pas de SLA uptime formel sur la ligne Lite ; **sauvegarde en option payante** (couverte par notre script) |
| **OVHcloud — VPS-1** | 1 vCore / 2 Go / 20 Go NVMe | ~5,52 €/mois | **≈ 66 €** | France/UE | **Sauvegardes quotidiennes incluses** ; **hausse tarifaire 2026** (+9 à +11 % sur les nouveaux déploiements, en vigueur au 1ᵉʳ avril 2026) |
| **Hetzner — CX22** | 2 vCPU / 4 Go / 40 Go | ~4,49 €/mois | **≈ 54 €** | Allemagne / Finlande (UE) | Meilleur rapport perf/prix, mais **pas de datacenter FR** ; hausses successives en 2026 (avril puis 15 juin) |
| **Scaleway — DEV1-S** | 2 vCPU / 2 Go | ~0,009 €/h ≈ 6,5 €/mois | **≈ 78 €** | France | Facturation horaire (logique cloud) ; **hausses au 1ᵉʳ juin 2026** (+2 % DEV1, jusqu'à +300 % sur Stardust) |
| **Ikoula — Flex'Server 1** | modulable (1 vCPU…) | 14,98 €/mois | ≈ 180 € | France | **Trop cher** pour le besoin ; écarté |

Pour notre charge, **Infomaniak VPS Lite** et **OVH VPS-1** sont les deux bons
candidats. Hetzner est le moins cher techniquement mais hors France. Ikoula et
Scaleway sont surdimensionnés/trop chers pour l'usage.

## Brique mutualisée WordPress + newsletter

| Hébergeur (offre) | Prix HT relevé (06/2026) | ≈ /an | Renouvellement | Newsletter | À savoir |
|---|---|---|---|---|---|
| **Infomaniak — Hébergement Web** | dès 5,75 €/mois | **≈ 69 €** | **Lissé / stable** | **Moteur newsletter intégré et inclus** | 250 Go SSD, ≥20 sites, SSL, WordPress ; éco/RSE de référence |
| **o2switch — Offre Unique Grow** | 7 €/mois | **84 €** | **Constant, sans hausse** | via Brevo | Support FR réputé très réactif ; offres « Cloud » (prix d'appel) et « Pro » (renouvellement cher) **à éviter** ici |
| **PlanetHoster — The World** | 6 €/mois | **≈ 72 €** | à vérifier | via Brevo | Multisites illimités, LiteSpeed ; offre gratuite « World Lite » (750 Mo) **insuffisante** |

**Newsletter via Brevo (France) — palier gratuit :** 300 e-mails/jour, contacts
illimités, gratuit à perpétuité, conforme RGPD. Limite : mention « Envoyé avec
Brevo » en pied d'e-mail, et 300/jour est un plafond quotidien (suffisant pour
une asso). Chez Infomaniak, le moteur newsletter **intégré** évite tout outil
tiers (une surface RGPD de moins à gérer).

**Nom de domaine :** `.fr` ≈ 4,50 €/an chez Infomaniak (renouvellement lissé) ;
fourchette marché `.fr` 5–12 €/an, `.eu` comparable. Prévoir ~5–10 €/an.

## Coût annuel par scénario (HT, prix 06/2026)

| Scénario | Site + newsletter | VPS | Domaine | Newsletter | **Total/an** |
|---|---|---|---|---|---|
| **A — Tout Infomaniak** *(éco / simplicité)* | 69 € | VPS Lite ~36 € | ~5 € | incluse (0 €) | **≈ 110 €** |
| **B — Tout français** | o2switch Grow 84 € *(ou PlanetHoster 72 €)* | OVH VPS-1 66 € | ~8 € | Brevo (0 €) | **≈ 146–158 €** |
| **C — Français, cloud** | o2switch / PlanetHoster 72–84 € | Scaleway ~78 € | ~8 € | Brevo (0 €) | **≈ 158–170 €** |

Écart avec le topo budgétaire initial : les prix 2026 ont légèrement monté côté
VPS français (hausse OVH d'avril 2026 ; o2switch « Grow » à 84 € est désormais
l'offre d'entrée la moins chère de la gamme). Le scénario A reste à ~110 € ;
les scénarios français glissent vers le haut de la fourchette (~150–160 €).

## Recommandation

**Scénario A — tout Infomaniak — recommandé**, car il coche le plus de critères
prioritaires à la fois :

- **Le moins cher** (~110 €/an) et **prix stables au renouvellement** (point de
  vigilance n°1 du bureau, où OVH et Scaleway viennent justement d'augmenter).
- **Newsletter incluse** dans l'hébergement web → **aucun outil tiers**, donc une
  surface RGPD en moins et rien à administrer côté bénévoles.
- **Fournisseur unique** → facturation, support et interface uniques : plus
  simple pour des non-techniciens et pour le référent technique.
- **RSE de référence** (critère écologique) ; **Suisse couverte par la décision
  d'adéquation UE** → conforme RGPD.
- Réserve à connaître : la ligne **VPS Lite** n'a pas de SLA d'uptime formel et
  la sauvegarde managée est en option — **sans impact réel ici** : l'événement
  est annuel, la disponibilité requise est modeste, et nos sauvegardes sont déjà
  scriptées (`deploy/sauvegarde.sh`).

**Repli si la priorité devient « tout en France strict »** : scénario B —
**o2switch (Grow)** pour le site (support FR excellent, prix constant) +
**OVH VPS-1** pour l'app (sauvegardes incluses) + **Brevo** pour la newsletter.
Compter ~150 €/an et deux fournisseurs + un outil newsletter tiers (surface RGPD
supplémentaire).

À éviter : Ikoula et Scaleway (surcoût/surdimensionnement) pour le VPS ; les
offres o2switch « Cloud » (prix d'appel) et « Pro » (renouvellement très cher)
pour le mutualisé.

## Décisions demandées au bureau

- [ ] Choix du scénario : **A (recommandé)** / B (France strict) / C (cloud).
- [ ] Validation du budget : **~110 €/an** (A) ou **~150 €/an** (B).
- [ ] Désignation du **référent technique** (maintenance VPS).
- [ ] Accord pour la **réservation du nom de domaine** (`.fr` ou `.eu`).

## Sources (consultées le 22 juin 2026)

- Infomaniak VPS Lite — prix & specs : <https://www.infomaniak.com/en/hosting/vps-cloud/prices> ; <https://www.vpsbenchmarks.com/compare/infomaniak>
- Infomaniak Hébergement Web (newsletter incluse) : <https://www.infomaniak.com/en/hosting/prices-and-characteristics> ; <https://www.journaldugeek.com/hebergeur/infomaniak/tarifs/>
- OVHcloud VPS (specs/prix + hausse 2026) : <https://www.ovhcloud.com/en/vps/cheap-vps/> ; <https://blog.ovhcloud.com/evolutions-tarifaires-de-public-cloud-bare-metal-et-vps-chez-ovhcloud/>
- Hetzner CX22 (prix + hausses 2026) : <https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/> ; <https://www.vpsbenchmarks.com/hosters/hetzner/plans/cx22>
- Scaleway DEV1-S/Stardust (hausses juin 2026) : <https://www.scaleway.com/en/pricing/virtual-instances-pricing/> ; <https://agentxcloud.com/news/scaleway-june-2026-pricing-update>
- Ikoula VPS : <https://www.ikoula.com/en/vps> ; <https://www.journaldugeek.com/hebergeur/ikoula/>
- o2switch Offre Unique (Grow/Cloud/Pro) : <https://wpmarmite.com/wordpress/hebergement/o2switch/tarifs/> ; <https://faq.o2switch.fr/espace-client/offre/>
- PlanetHoster The World / World Lite : <https://www.planethoster.com/en/World-Hosting> ; <https://www.journaldugeek.com/hebergeur/planethoster/tarifs/>
- Brevo — palier gratuit : <https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan> ; <https://www.brevo.com/pricing/>
- Prix domaines `.fr`/`.eu` : <https://www.infomaniak.com/en/domains/prices> ; <https://systalink.com/nom-de-domaine-prix-comparatif/>
