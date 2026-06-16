# Note de conception — Module « prêts longue durée » (comptes / membres)

**Statut :** proposition, hors périmètre v1. À valider par le bureau avant tout
développement. Ce document décrit *comment* on pourrait étendre le système ; il
ne décide pas qu'on le fait.

**Lien avec l'existant :** la conception de référence reste
`docs/specification.md` (notamment §2 « zéro donnée personnelle » et §11
« évolutions prévues »). La présente note en est une extension, à lire en
complément.

---

## 1. Objectif

Ajouter, **en parallèle** du prêt anonyme de l'événement, un mode de **prêt
longue durée nominatif** :

- enregistrer un emprunteur (nom, prénom, e-mail) ;
- fixer une **date de retour souhaitée** ;
- envoyer un **rappel automatique par e-mail** à l'approche / au dépassement de
  l'échéance ;
- enrichir progressivement la base (fiches membres réutilisables).

---

## 2. Le point structurant : deux mondes à cloisonner

Le système actuel a une qualité rare : il est **hors champ RGPD** car il ne
stocke **aucune donnée personnelle** (l'emprunteur n'est qu'un numéro de
pochette). Dès qu'on enregistre un nom et un e-mail, on entre dans un
**traitement de données personnelles**.

La règle directrice de cette évolution est donc le **cloisonnement** :

- Le **flux événementiel anonyme reste strictement inchangé** (scan → numéro de
  pochette → zéro donnée). C'est lui qui résout le goulet d'étranglement le jour
  J ; on n'y touche pas.
- Le **flux longue durée nominatif** est un **module séparé et optionnel**, avec
  ses propres tables, ses propres écrans, et son propre cadre RGPD.

Concrètement : tables distinctes (voire base ou schéma séparé), de sorte qu'une
suppression des données nominatives n'affecte jamais l'historique anonyme des
prêts ni les statistiques.

---

## 3. Périmètre fonctionnel visé

1. **Fiches membres** : créer / rechercher / modifier un membre (nom, prénom,
   e-mail, consentement, date d'inscription).
2. **Prêt longue durée** : associer un exemplaire à un membre, avec date de
   sortie et **date de retour souhaitée**. Retour qui clôt le prêt.
3. **Tableau de bord des prêts en cours** : liste filtrable, retards en évidence.
4. **Rappels e-mail** : message automatique avant l'échéance et/ou en cas de
   retard.
5. **Droits RGPD** : pouvoir exporter et **supprimer** les données d'un membre.

---

## 4. Modèle de données

Les **deux clés stables** (`id_exemplaire`, `reference_titre`) sont **conservées
telles quelles** : le module s'y rattache sans les modifier.

### 4.1 Nouvelle table `membres`

| Champ | Description |
|---|---|
| `id_membre` | clé primaire (auto) |
| `nom`, `prenom` | identité de l'emprunteur |
| `email` | pour les rappels (optionnel si pas de rappel souhaité) |
| `consentement_rappels` | 0/1 — consentement explicite à l'envoi d'e-mails |
| `date_inscription` | horodatage |
| `notes` | champ libre optionnel |

### 4.2 Nouvelle table `prets_longue_duree` (séparée de `prets`)

On **ne mélange pas** avec la table `prets` anonyme, pour préserver l'anonymat et
les statistiques existantes.

| Champ | Description |
|---|---|
| `id_pret_ld` | clé primaire (auto) |
| `id_exemplaire` | FK → `exemplaires` |
| `id_membre` | FK → `membres` |
| `date_sortie` | horodatage |
| `date_retour_souhaitee` | échéance prévue |
| `date_retour` | NULL tant que non rendu |
| `dernier_rappel_envoye` | horodatage, pour ne pas spammer |

### 4.3 État d'un exemplaire

L'état « disponible / sorti » devra tenir compte des **deux** sources de prêt
(anonyme **et** longue durée). On adaptera la fonction d'état pour qu'un
exemplaire soit « sorti » s'il a un prêt non clos dans l'une **ou** l'autre
table. C'est le seul vrai point d'intégration côté logique métier.

---

## 5. Comptes et rôles — deux niveaux à ne pas confondre

- **Fiches membres** (recommandé pour démarrer) : de simples enregistrements
  gérés **par les bénévoles**. L'emprunteur ne se connecte pas. Léger, peu de
  surface de sécurité. Réutilise le mécanisme de jeton bénévole existant
  (éventuellement un rôle « gestion longue durée » distinct du prêt événementiel).
- **Comptes avec connexion** (emprunteur qui se connecte lui-même) : un cran
  au-dessus — mots de passe (hachage fort type argon2/bcrypt), sessions,
  réinitialisation, vérification d'e-mail. À n'envisager que si un besoin concret
  l'exige (self-service de réservation, par ex.). Non nécessaire pour des prêts
  nominatifs avec rappels.

**Reco :** commencer par les fiches membres gérées côté bénévole ; ajouter des
comptes self-service seulement plus tard, si justifié.

---

## 6. Rappels e-mail

- Une **tâche planifiée quotidienne** (cron sur le VPS, ou planificateur interne)
  recherche les prêts longue durée dont `date_retour_souhaitee` approche ou est
  dépassée, dont le membre a `consentement_rappels = 1`, et qui n'ont pas reçu de
  rappel récent (`dernier_rappel_envoye`).
- **Envoi** via un service e-mail européen, idéalement **le même que la
  newsletter** (Brevo, ou l'outil intégré d'Infomaniak — déjà prévus au budget)
  pour mutualiser l'outillage et la conformité.
- Chaque e-mail doit être sobre, identifiable (association), et l'envoi tracé
  pour audit minimal.

---

## 7. RGPD — obligations (à valider avec une personne compétente)

> Je ne suis pas juriste : les points ci-dessous donnent le cadre, ils ne
> remplacent pas un avis qualifié.

Dès lors qu'on stocke nom / prénom / e-mail :

- **Base légale et information** : informer la personne de la finalité (gestion
  du prêt + rappels), au moment de la collecte.
- **Consentement** explicite et séparé pour les **rappels e-mail** (case non
  pré-cochée), distinct d'un éventuel consentement newsletter.
- **Durée de conservation** définie et appliquée (ex. purge des fiches inactives
  après X mois/années).
- **Droits d'accès et de suppression** : pouvoir, sur demande, exporter ou
  effacer les données d'un membre — sans casser l'historique anonyme.
- **Sécurité** : accès restreint (jeton/rôle bénévole), HTTPS, sauvegardes
  chiffrées ou protégées.
- **Registre des traitements** : tenir à jour la description de ce traitement.
- **Cloisonnement** : ce traitement est distinct de la newsletter et de l'appli
  de prêt anonyme ; ne pas réutiliser les e-mails d'une finalité à l'autre.

Principe de **minimisation** : ne collecter que le strict nécessaire (un e-mail
n'est utile que si la personne veut des rappels ; sinon, nom/prénom peuvent
suffire).

---

## 8. Intégration technique

- **Réutilisé tel quel** : la couche de lecture (catalogue, fiches), les deux
  clés stables, le scanner (on pourrait scanner un exemplaire puis choisir « prêt
  événementiel » ou « prêt longue durée »).
- **Ajouté** : `app/routes/membres.py`, `app/routes/prets_longue_duree.py`,
  services dédiés, templates, et un module d'envoi e-mail.
- **Séparation des données** : a minima des tables distinctes ; idéalement un
  fichier SQLite séparé (`data/membres.db`) ou un schéma à part, pour isoler
  physiquement les données personnelles. À trancher selon le confort de
  sauvegarde/suppression.
- **Configuration** : nouveaux paramètres `.env` (SMTP/clé API e-mail, délais de
  rappel), jamais committés.

---

## 9. Étapes de mise en œuvre proposées

1. Décision de gouvernance (bureau) : on le fait ? avec quel périmètre RGPD ?
2. Table `membres` + CRUD bénévole (sans e-mail d'abord).
3. Table `prets_longue_duree` + écran de prêt/retour longue durée.
4. Adaptation de l'état d'exemplaire (prise en compte des deux sources).
5. Tableau de bord des prêts en cours + retards.
6. Intégration e-mail + tâche planifiée de rappels (avec consentement).
7. Outils RGPD : export et suppression d'un membre.
8. Page de politique de confidentialité dédiée + mise à jour du registre.

L'effort est **modéré** (extension, pas refonte) ; l'essentiel du risque est
organisationnel/juridique, pas technique.

---

## 10. Points à trancher

- [ ] Décision d'opportunité par le bureau (le besoin est-il réel et récurrent ?).
- [ ] Fiches membres seules, ou comptes avec connexion ?
- [ ] Service e-mail retenu (Brevo / Infomaniak / autre).
- [ ] Durée de conservation des fiches membres.
- [ ] Stockage : tables séparées dans la même base, ou base SQLite distincte ?
- [ ] Délais de rappel (combien de jours avant l'échéance, relances en retard ?).

---

## 11. Ce qui n'est PAS impacté

- Le **prêt événementiel anonyme** (numéro de pochette) reste identique et
  prioritaire.
- Les **deux clés stables** et le **schéma existant** ne changent pas.
- Les **statistiques** actuelles, fondées sur l'historique anonyme, restent
  valables ; on pourrait y ajouter des indicateurs longue durée séparés.
- La propriété « zéro donnée personnelle » du **cœur événementiel** est
  préservée : seules les nouvelles tables, cloisonnées, portent des données
  nominatives.
