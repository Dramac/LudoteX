# Système de prêt de jeux — Document de spécification

**Version :** 1.1 — base de discussion
**Objet :** remplacer la feuille de prêt papier par un système numérique multi-accès, fondé sur un QR code par exemplaire de jeu et une base de données en ligne.

---

## 1. Contexte et objectif

L'association dispose d'un parc d'environ 700 jeux et organise chaque année un événement où ces jeux sont prêtés au public dans une salle des fêtes. Le prêt fonctionne aujourd'hui contre dépôt d'une pièce d'identité (PI), consignée sur une feuille papier unique au comptoir.

Ce système est fiable mais centralisé : un seul point d'écriture et de recherche, ce qui crée un goulet d'étranglement aux heures de pointe.

**Objectif :** permettre à plusieurs bénévoles, chacun muni d'un smartphone, d'enregistrer prêts et retours en parallèle, en scannant un QR code apposé sur chaque jeu, avec une base de données en ligne partagée et synchronisée.

**Objectifs secondaires (prévus) :**
- offrir au public un accès en consultation au catalogue des jeux et à leur disponibilité, depuis chez lui et avant l'événement ;
- disposer d'un **site internet d'association** éditable par plusieurs bénévoles non techniciens, doté d'une **fonctionnalité de newsletter**.

---

## 2. Principes de conception

Ces principes guident toutes les décisions du document.

1. **Zéro donnée personnelle.** L'emprunteur n'est jamais identifié nominativement. Le seul lien prêt ↔ personne est un **numéro de pochette** physique où est glissée la PI. Le système reste donc hors du champ du RGPD.
2. **Ne jamais bloquer le bénévole en pic.** Toute incohérence est signalée et accompagnée d'une action de rattrapage en un tap, jamais d'une erreur bloquante.
3. **Simplicité maximale de l'interface de prêt.** Le bénévole confirme une action pré-sélectionnée ; on ne lui demande un choix explicite que dans les cas réellement ambigus.
4. **Séparer la lecture de l'écriture.** La consultation est publique ; les actions de prêt/retour sont réservées aux bénévoles.
5. **Souplesse du catalogue.** Le CSV pourra évoluer librement, à l'exception de deux clés non négociables (voir §3).
6. **Pas de sur-ingénierie.** On construit d'abord le système de prêt ; on se contente de ne pas se fermer la porte des évolutions futures.

---

## 3. Modèle de données

### 3.1 Les deux clés non négociables

Quelles que soient les évolutions du CSV, deux champs doivent exister et rester stables :

- **`id_exemplaire`** — identifiant unique et stable d'une **boîte physique**. C'est ce que le QR code encode. Il ne doit jamais changer une fois le QR imprimé et collé, même si l'on modifie l'éditeur, la catégorie, etc. Si le CSV n'en possède pas, on le génère et il devient la référence.
- **`reference_titre`** — clé de regroupement commune à tous les exemplaires d'un **même jeu**. Trois boîtes de Catan partagent par exemple la référence `CATAN`. C'est indispensable pour agréger correctement les statistiques par titre (voir §7).

### 3.2 Tables

**`titres`** — le catalogue, niveau « référence ».

| Champ | Description |
|---|---|
| `reference_titre` | clé primaire (ex. `CATAN`) |
| `nom` | nom affiché du jeu |
| `categorie` | catégorie pour le filtrage public (définie dans le CSV) |
| *(champs libres)* | nb de joueurs, durée, éditeur… — peuvent évoluer librement |

**`exemplaires`** — les boîtes physiques, niveau « unité prêtable ».

| Champ | Description |
|---|---|
| `id_exemplaire` | clé primaire, encodée dans le QR |
| `reference_titre` | clé étrangère → `titres` |

**`prets`** — l'historique complet de tous les prêts (jamais purgé).

| Champ | Description |
|---|---|
| `id_pret` | clé primaire |
| `id_exemplaire` | clé étrangère → `exemplaires` |
| `numero_pochette` | numéro de pochette attribué pour ce prêt |
| `date_sortie` | horodatage de sortie |
| `date_retour` | horodatage de retour ; **vide tant que le jeu est sorti** |

**`pochettes`** — l'occupation du moment (quels numéros sont actuellement utilisés).

| Champ | Description |
|---|---|
| `numero_pochette` | numéro (recyclé, voir §6) |
| `occupe` | libre / occupé |

### 3.3 Règles dérivées

- Un exemplaire est **disponible** s'il n'a aucun prêt avec `date_retour` vide ; il est **sorti** sinon.
- Le **numéro de pochette** n'est qu'une occupation du moment : il est libéré au retour et réutilisable. L'historique du prêt conserve néanmoins son `numero_pochette`, sans incidence sur les statistiques.
- L'historique des prêts n'est jamais supprimé : c'est lui qui alimente les statistiques (volume total, prêts par heure, palmarès).

---

## 4. QR codes

- **Un QR unique par exemplaire physique** (pas par titre). Deux boîtes du même jeu ont deux QR distincts.
- Le QR encode une **URL** de la forme `https://pret.example.fr/jeu/00472`.
- **Un seul format, deux modes de lecture :**
  - **Scanner embarqué dans la page** (caméra active dans l'application web) — mode principal pour le bénévole au comptoir : il enchaîne les scans sans quitter l'application, une seule autorisation caméra en début de session.
  - **Appareil photo natif du téléphone** — filet de sécurité si un modèle gère mal la caméra dans le navigateur, et support de l'usage public éventuel.
- Le contenu du QR (URL de fiche) ne comporte **aucun secret** : il donne le même niveau d'accès que le catalogue public (lecture seule).

---

## 5. Écrans et interface

### 5.1 Écran de prêt / retour (bénévole)

Déclenché par un scan. Le système connaît l'état de l'exemplaire et **pré-sélectionne l'action la plus probable** :

**Cas — exemplaire DISPONIBLE** (pas d'ambiguïté)
→ Action unique : **Prêter**. Le système attribue le plus petit numéro de pochette libre et l'affiche en grand (« Pochette n°7 — glissez-y la pièce d'identité »). Un seul tap.

**Cas — exemplaire SORTI** (ambiguïté possible, choix explicite requis)
Deux actions présentées, l'action dominante mise en avant :
- **Rendre** *(action principale)* — « Rendre — libère la pochette n°7 ». Clôt le prêt en cours, libère le numéro.
- **Le re-prêter** *(action secondaire, cas d'oubli de scan)* — considère le prêt précédent comme rentré (date de retour = maintenant, ancien numéro libéré), puis ouvre un nouveau prêt avec un nouveau numéro de pochette.

C'est le seul écran où un choix explicite est demandé, et uniquement parce que la réalité physique peut diverger de la base.

### 5.2 Catalogue public (consultation)

- Accès en **lecture seule**, sans donnée personnelle, sans bouton d'action.
- Navigation **en vrac** ou **par catégorie** (catégories définies dans le CSV).
- Affiche pour chaque jeu sa disponibilité (au niveau titre : combien d'exemplaires disponibles).
- Destiné à être consultable **à l'année**, y compris en amont de l'événement.

### 5.3 Page de statistiques (post-événement)

Voir §7.

---

## 6. Gestion des numéros de pochette

Le numéro identifie une **pochette numérotée** (ou un ticket numéroté agrafé à la PI), pas un emplacement de meuble en nombre fixe. Le principe est donc **sans plafond** : on ne refuse jamais un prêt.

- Numérotation **à partir de 1** ; à chaque nouveau prêt, attribution du **plus petit numéro libre**.
- La PI est glissée dans la pochette portant ce numéro ; les pochettes sont rangées dans l'ordre pour une récupération rapide au retour.
- Un numéro libéré au retour est immédiatement **recyclé**.
- **Aucune limite logicielle ni physique** : le stock de pochettes est extensible (coût matériel négligeable). En cas d'affluence record, le système continue d'attribuer des numéros croissants sans blocage.
- **Point essentiel** : le numéro doit rester **physiquement attaché à la PI** (pochette / ticket numéroté). Une PI mise « en vrac » sans numéro casserait le lien numéro → emplacement et réintroduirait une recherche manuelle au retour — précisément le goulet d'étranglement à supprimer.
- Le numéro identifie *la PI déposée*, pas la personne, et **un seul jeu par PI / par numéro** (règle retenue).

---

## 7. Statistiques

Toutes les statistiques s'appuient sur l'historique complet de la table `prets`.

Indicateurs prévus :

- **Nombre total de jeux prêtés** sur l'événement.
- **Palmarès des jeux les plus prêtés**, agrégé **par titre** (`reference_titre`) : les exemplaires multiples d'un même jeu sont additionnés.
- **Palmarès des jeux les moins prêtés**, y compris ceux **jamais sortis** (valeur 0). Pour cela, on part de la liste de **tous les titres du catalogue** et on y rattache les prêts (raisonnement « catalogue d'abord »), faute de quoi les jeux à zéro prêt seraient invisibles.
- **Nombre de prêts par heure** (histogramme sur la durée de l'événement), à partir des horodatages de sortie.

Option à valider : présenter le palmarès en **deux vues** — total brut par titre, et « par exemplaire » (pour ne pas avantager mécaniquement les titres présents en plusieurs boîtes).

---

## 8. Contrôle d'accès et sécurité

L'association fait confiance à ses bénévoles : **pas de comptes individuels**. La distinction nécessaire n'est pas entre bénévoles, mais entre **public (lecture)** et **bénévoles (écriture)**.

Mécanisme retenu :

- **Jeton aléatoire long** (≈ 32 caractères) plutôt qu'un mot de passe « humain ». Distribué une fois par an aux bénévoles via le canal interne (groupe de discussion, mail) sous forme d'un lien d'activation. Le téléphone le mémorise (cookie/localStorage) ; ce jeton autorise ensuite les écritures. Un tel jeton n'est pas devinable par force brute, contrairement à un mot de passe court.
- **Séparation lecture / écriture :** les fiches de consultation (`/jeu/...`) sont publiques et sans action ; les opérations de prêt/retour passent par un point d'entrée distinct, protégé par le jeton.
- **Limitation de débit** côté serveur (nombre de tentatives par minute et par IP) — mesure complémentaire « ceinture et bretelles » contre le brute-force.
- **Rotation annuelle** du jeton (révocation / régénération à chaque édition).

---

## 9. RGPD

### 9.1 Application de prêt — zéro donnée personnelle

Par conception, l'application de prêt **ne stocke aucune donnée personnelle**. L'emprunteur est représenté par un numéro de pochette ; sa pièce d'identité reste physiquement au comptoir et lui est rendue au retour du jeu. Cette partie est donc hors du champ d'application du RGPD, et cette propriété doit être préservée dans les évolutions futures (voir §11).

### 9.2 Site et newsletter — traitement de données personnelles

La fonctionnalité de **newsletter** (voir §10) introduit, elle, un traitement de données personnelles : la collecte et la conservation d'adresses e-mail. Elle est **strictement cloisonnée** de l'application de prêt (deux briques distinctes), mais impose à l'association les obligations habituelles :

- **consentement explicite** de l'abonné au moment de l'inscription (case à cocher non pré-cochée) ;
- **lien de désinscription** dans chaque envoi (géré nativement par l'outil d'emailing) ;
- **page de politique de confidentialité** sur le site (finalité, durée de conservation, droits d'accès et de suppression) ;
- **non-réutilisation** des adresses pour une autre finalité que la newsletter.

Ces obligations relèvent de la responsabilité de l'association et sont sans incidence sur l'anonymat de l'application de prêt.

---

## 10. Architecture technique et hébergement

L'ensemble repose sur **deux briques cloisonnées**, sous un **nom de domaine unique** (sous-domaines). Le fournisseur d'hébergement est à choisir par le bureau parmi des acteurs européens (de préférence français) ; candidats retenus : Infomaniak (Suisse, forte démarche écologique), o2switch et PlanetHoster (France, mutualisé), OVHcloud, Ikoula et Scaleway (France, VPS). La newsletter peut être intégrée à l'hébergeur (cas d'Infomaniak) ou déléguée à un outil externe français (Brevo, palier gratuit conforme RGPD).

### 10.1 Brique « application de prêt » — VPS Lite (technique)

- **Application web** (pas d'application native) : les bénévoles ouvrent une URL ; option « ajouter à l'écran d'accueil » (PWA) pour un lancement en un tap. Aucune installation, aucun store, compatible avec tout smartphone.
- **Backend Python** : FastAPI ou Flask.
- **Base de données SQLite** : largement suffisante pour la charge réelle (quelques écritures par minute, poignée de bénévoles) ; pas besoin de PostgreSQL.
- **Pas de temps réel complexe** (pas de websocket) : chaque scan lit l'état courant en base ; les conflits rares (deux bénévoles sur le même exemplaire) se gèrent par un contrôle d'état côté serveur.
- **Hébergement : un VPS** (Debian/Ubuntu, accès SSH root), tournant à l'année. Candidats : Infomaniak VPS Lite, OVHcloud, Ikoula, Scaleway, ou Hetzner (Allemagne) si le prix prime. Héberge l'appli de prêt **et** le catalogue public. Administré par le référent technique. Le NAS Synology peut servir d'environnement de dev/test.
- Accès attendu : `pret.<domaine>` (outil bénévole) et `<domaine>/catalogue` (consultation publique).

### 10.2 Brique « site + newsletter » — Hébergement Web (éditorial)

- **Hébergement mutualisé avec WordPress** (o2switch, PlanetHoster, ou hébergement Web Infomaniak) : site vitrine de l'association, **éditable par plusieurs bénévoles non techniciens** via une interface clic-bouton, sans code.
- **Newsletter** : soit l'outil d'emailing intégré à l'hébergeur (cas d'Infomaniak, crédits gratuits mensuels), soit un service externe français (Brevo), avec statistiques d'envoi et gestion native de la désinscription.
- Accès attendu : `www.<domaine>`.

### 10.3 Pourquoi deux briques

- L'édition du site par des non-techniciens et la newsletter sont **clés en main** sur l'hébergement Web mutualisé.
- L'application de prêt (Python persistant) demande le **contrôle d'un VPS**, mal adapté au mutualisé.
- Le cloisonnement préserve l'anonymat de l'appli de prêt face au traitement de données de la newsletter (voir §9).

---

## 11. Hors périmètre v1 — évolutions prévues

À ne pas développer maintenant, mais à ne pas compromettre :

- **Catalogue public navigable** : la couche de lecture est conçue dès la v1 pour resservir telle quelle.
- **Favoris du public** : à implémenter d'abord **en local sur l'appareil du visiteur** (localStorage), éventuellement avec un « code de liste » à noter pour la retrouver ailleurs. Objectif : zéro donnée personnelle côté serveur. Des comptes utilisateurs ne seraient qu'un ajout ultérieur, pas une refonte — et rouvriraient la question RGPD.

---

## 12. Points encore ouverts

- [ ] **Colonnes exactes du CSV** (le CSV n'est pas encore disponible et évoluera). Seules contraintes fermes : `id_exemplaire` et `reference_titre`.
- [ ] **Stock de pochettes numérotées** à prévoir (extensible, sans plafond) — remplace l'ancienne question de dimensionnement par le pic.
- [ ] **Double vue du palmarès** (par titre / par exemplaire) : à confirmer.
- [ ] **Validation du budget annuel** par le bureau (deux abonnements + domaine, voir topo budgétaire dédié).
- [ ] **Nom de domaine** à réserver.
- [ ] **Rédaction de la politique de confidentialité** du site (liée à la newsletter, §9.2).
- [ ] Liste exhaustive des **cas limites** secondaires à expliciter au moment de la spec détaillée (au-delà des deux cas déjà traités : exemplaire sorti rescanné, retour d'un jeu déjà disponible).
