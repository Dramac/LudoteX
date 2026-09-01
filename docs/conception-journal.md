# Conception — Journal d'activité

**Statut :** spécification proposée, issue d'un échange avec Simon (4 août 2026).
Pas encore validée, aucune ligne de code écrite.
**État du dépôt à la rédaction :** LudoteX 1.2.1, 511 tests verts.
**Décisions déjà arrêtées en discussion :** identifiant d'appareil plutôt qu'adresse
IP ; format JSON Lines ; fichier unique comme source de vérité, lu à la fois par un
écran `/admin/journal` et par un outil de terminal ; identifiant affiché sur
`/scanner` ; liste des appareils actifs consultable en administration, avec leur date
et heure d'activation.

---

## 1. Le besoin

Deux usages, énoncés par Simon :

1. **Pendant la phase de test** — voir défiler dans la console qui fait quoi et
   quand, sans ouvrir de navigateur.
2. **Le jour de l'événement et après** — un écran `/admin/journal` consultable par
   le bureau, pour comprendre ce qui s'est passé quand quelque chose paraît
   anormal.

### 1.1 Ce que ce journal n'est pas

**Ce n'est pas le module « mesure d'audience »** (chantier distinct, non encore
implémenté). Ce dernier compte des *pages vues* par un
middleware, de façon agrégée et anonyme, pour répondre à « combien de monde, quels
modules ». Le journal répond à « qui a fait quoi, quand », ligne par ligne. Deux
questions, deux outils, deux durées de vie.

Ils ne se recouvrent pas et peuvent coexister sans se gêner : l'un capte des GET
publics au middleware, l'autre capte des écritures aux points d'action. Si les deux
se font un jour, ils resteront deux systèmes distincts — la mutualisation évoquée en
discussion tombe d'elle-même dès lors que le journal n'est plus une base SQLite.

**Ce n'est pas non plus un journal serveur au sens de nginx ou systemd.** Ceux-là
existent déjà, gardent les IP complètes et les codes HTTP, et restent la bonne source
en cas de vrai besoin d'enquête technique. Le journal d'activité parle métier :
« retour de 7 Wonders Duel », pas « POST /pret/00472/rendre 303 ».

### 1.2 Un middleware ne peut pas produire ces lignes

Point tranché en discussion, rappelé ici parce qu'il conditionne tout le reste : au
niveau du middleware, FastAPI expose le motif de route (`/pret/{id_exemplaire}/rendre`)
et le code HTTP, jamais le nom du jeu ni celui du tournoi. Ces libellés ne vivent que
dans la couche service. Le journal se remplit donc par **appels explicites** aux
points d'action, pas par interception.

---

## 2. Ce qu'on journalise

Le tri qui suit est le cœur de la note. La tentation naturelle est de journaliser les
prêts ; or ce sont précisément les actions **déjà tracées**. La valeur est ailleurs.

### 2.1 Priorité 1 — les actions qui ne laissent aucune trace aujourd'hui

Configuration et administration. Rien dans les trois bases ne permet aujourd'hui de
savoir qu'elles ont eu lieu, ni quand, ni combien de fois.

| Action | Route |
|---|---|
| Jeton bénévole réinitialisé | `POST /admin/jeton/reinitialiser` |
| Mot de passe admin changé | `POST /admin/motdepasse` |
| Connexion admin (réussie **et** échouée) | `POST /admin/login` |
| Import CSV du catalogue | `POST /admin/donnees/import` |
| Restauration d'une sauvegarde | `POST /admin/sauvegarde/import` |
| Clôture des prêts de fin d'événement | `POST /admin/cloturer-prets` |
| Module activé / désactivé | `POST /admin/fonctionnalites` |
| Contexte ou visibilité de rangement changés | `POST /admin/rangement/contexte`, `.../visibilite` |
| Affectation d'emplacement en lot | `POST /admin/rangement/ranger/appliquer` |
| Annonce de l'écran de salle posée / effacée | `POST /admin/ecran-salle` |
| Date de l'événement changée | `POST /admin/evenement` |
| Purge RGPD d'une édition du planning | `POST /planning/admin/{ev}/purger` |
| Réinitialisation des données de formation | `POST /admin/formation/reinitialiser` |
| Création d'une fiche de jeu, ajout d'exemplaire | `POST /admin/jeu-nouveau`, `.../exemplaire` |

La connexion admin **échouée** mérite une mention particulière : c'est aujourd'hui le
seul signal d'une tentative d'intrusion, et il n'est visible nulle part (le compteur de
limitation de débit est en mémoire et remis à zéro à chaque redémarrage).

### 2.2 Priorité 2 — les actions dont seul l'état final est conservé

Les modules tournois, programme et planning stockent un état, jamais son historique.
Un tournoi supprimé ne laisse rien ; un tournoi passé de `inscriptions` à `lance` ne
dit pas quand ni par qui.

Création, modification, suppression et changement d'état d'un tournoi ; lancement avec
mode de scoring ; saisie de résultats ; ajout et suppression manuels de participants ;
ouverture groupée des tournois du jour. Idem pour les éléments de programme et les
types. Côté planning : fermeture du questionnaire, génération du planning,
publication, modification manuelle d'une case.

### 2.3 Priorité 3 — les prêts, malgré la redondance

La table `prets` conserve déjà tout l'historique et n'est jamais purgée. Journaliser
les prêts est donc un doublon — mais un doublon **utile**, pour trois raisons :

- c'est le flux que Simon veut voir défiler en console pendant les tests ;
- il porte le **nom du jeu**, quand la table `prets` ne porte qu'un `id_exemplaire` ;
- il enregistre les **échecs**, que la table ne peut pas enregistrer par construction :
  « déjà sorti », « déjà disponible », et surtout le résultat `occupe` ajouté lors de
  la correction des courses de pochettes. Un `occupe` dans le journal, c'est un
  bénévole qui a vu un message inattendu ; c'est exactement ce qu'on cherche après coup.

### 2.4 Hors périmètre

Les consultations. Aucun GET public n'est journalisé : ni `/catalogue`, ni
`/jeu/{id}`, ni `/live/data` (interrogé toutes les 10 secondes par l'écran de salle,
il noierait tout le reste). Un journal d'actions ne compte pas les visites — c'est le
travail du module d'audience s'il se fait un jour.

Deux exceptions à discuter (arbitrage §10) : l'affichage d'un écran de prêt
`GET /pret/{id}` (un scan sans action derrière est une information : le bénévole a
regardé puis renoncé) et `GET /admin/sauvegarde/export` (savoir qu'une sauvegarde a
été téléchargée, et quand).

---

## 3. Format de ligne

JSON Lines : un objet JSON complet par ligne, jamais indenté, jamais de retour à la
ligne dans une valeur.

```json
{"t":"2026-08-04T12:37:02+02:00","qui":"benevole","appareil":"3F1A9C","module":"pret","action":"retour","objet":"7 Wonders Duel","ref":"7-wonders-duel","ok":true}
{"t":"2026-08-04T13:43:11+02:00","qui":"admin","appareil":"B72E04","module":"tournois","action":"tournoi_cree","objet":"Chaussette","ref":"12","ok":true}
{"t":"2026-08-05T20:31:50+02:00","qui":"admin","appareil":"B72E04","module":"live","action":"annonce_posee","objet":"Tombola à 15 h","ok":true}
{"t":"2026-08-03T12:21:07+02:00","qui":"visiteur","module":"tournois","action":"inscription","ref":"7","objet":"Tournoi de Catan","ok":true}
```

| Champ | Obligatoire | Contenu |
|---|---|---|
| `t` | oui | Horodatage ISO 8601 **avec décalage local** (`+02:00`) |
| `qui` | oui | `visiteur` \| `benevole` \| `admin` \| `indetermine` |
| `appareil` | non | Identifiant d'appareil (§4). Absent pour un visiteur |
| `module` | oui | `pret` \| `catalogue` \| `tournois` \| `programme` \| `planning` \| `rangement` \| `live` \| `stats` \| `admin` |
| `action` | oui | Verbe court, vocabulaire fermé (§3.2) |
| `objet` | non | Libellé lisible de ce sur quoi on a agi |
| `ref` | non | Clé technique stable (`reference_titre`, `id_tournoi`…) |
| `ok` | oui | `true` si l'action a abouti |
| `detail` | non | Texte court, seulement quand `ok` est `false` (`deja_sorti`, `occupe`…) |

### 3.1 Pourquoi l'heure locale et pas UTC

Tout le projet stocke en UTC ISO, et c'est la bonne règle pour de la donnée métier
qu'on trie, compare et affiche. Le journal n'est pas de la donnée métier : c'est un
fichier qu'un humain lit au `tail` à 23 h en cherchant ce qui s'est passé à 20 h 31.
Un horodatage avec décalage explicite (`2026-08-04T12:37:02+02:00`) est non ambigu,
directement lisible, et reste triable lexicographiquement tant que le décalage ne
change pas.

Réserve honnête : le tri lexicographique casse au passage à l'heure d'hiver. Sans
conséquence pour un événement d'un week-end, et l'outil de terminal (§7) trie de toute
façon sur la date parsée.

### 3.2 Le vocabulaire des actions doit être fermé

Une liste de constantes dans un seul module, jamais des chaînes libres écrites au fil
des appels. Sans quoi on aura `retour`, `rendu` et `rendre` dans le même fichier six
mois plus tard, et aucun filtre ne fonctionnera. Un test garde-fou vérifie que toute
valeur passée à `journaliser()` appartient à la liste.

### 3.3 L'assainissement est obligatoire, pas défensif

`objet` peut contenir du texte libre saisi en admin — le texte d'une annonce d'écran
de salle, le nom d'un tournoi. Avant écriture : retours à la ligne remplacés par des
espaces, troncature à 120 caractères. `json.dumps` gère le reste (guillemets,
caractères de contrôle, unicode) — c'est précisément la raison pour laquelle JSON a
été préféré au format à séparateurs.

---

## 4. L'identifiant d'appareil

### 4.1 Principe

Six caractères hexadécimaux (`secrets.token_hex(3)`, ~16,7 millions de valeurs),
tirés au hasard et posés dans un cookie `appareil`. HttpOnly, SameSite=Lax, Secure en
HTTPS — exactement les réglages du cookie de jeton, dont il partage la durée de vie.

Six caractères et non quatre : avec quatre (65 536 valeurs) et une trentaine
d'appareils, la probabilité qu'au moins deux se retrouvent avec le même identifiant
avoisine 0,7 % — assez rare pour ne jamais être testée, assez fréquente pour
induire en erreur le jour où elle survient. Six caractères rendent la collision
négligeable et restent lisibles à voix haute.

### 4.2 Où le cookie est posé — et où il ne l'est pas

Il est posé à **deux endroits seulement** : l'activation `/acces?jeton=` (bénévoles) et
`POST /admin/login` (administration). Autrement dit, uniquement pour les personnes qui
écrivent.

Conséquence à souligner : **aucun cookie n'est posé pour le public**. Un visiteur qui
consulte le catalogue ou s'inscrit à un tournoi ne reçoit rien de nouveau, et ses
lignes de journal portent simplement `"qui":"visiteur"` sans identifiant. La question
du bandeau de consentement ne se pose donc à aucun moment.

**Piège à ne pas manquer :** ne poser le cookie que s'il est **absent**. Un bénévole
qui rouvre son lien d'activation (ce qui arrive : le cookie expire, on repartage le
lien) recevrait sinon une nouvelle identité, et le journal montrerait deux appareils
là où il n'y en a qu'un.

### 4.3 Ce que cet identifiant dit — et ne dit pas

Il dit « c'est le même téléphone », jamais « c'est le téléphone de Marie ». C'est
exactement la propriété qui le rend acceptable au regard du RGPD, et c'est aussi sa
limite : le journal permettra de constater que deux appareils différents ont agi, sans
pouvoir les nommer.

Le rapprochement, quand il devient nécessaire en dépannage, passe donc par une voie
déclarative : l'identifiant est **affiché discrètement sur `/scanner`** (arbitrage 4,
validé), pour qu'un bénévole puisse dire « moi c'est 3F1A9C ».

### 4.4 Le registre des appareils

L'identifiant ne vit que dans un cookie : le serveur ignore quels appareils existent
tant qu'il ne les enregistre pas. Pour la liste demandée en administration (§7), une
table dans la **base de prêt** — là où vivent déjà `parametres` et le jeton :

```sql
CREATE TABLE appareils (
    appareil    TEXT PRIMARY KEY,   -- les 6 caractères du cookie
    role        TEXT NOT NULL,      -- 'benevole' | 'admin'
    active_le   TEXT NOT NULL,      -- UTC ISO, instant de la pose du cookie
    expire_le   TEXT,               -- UTC ISO, même échéance que le cookie
    generation  TEXT                -- empreinte du jeton en vigueur (§4.5)
);
```

Écriture **une seule fois par activation**, aux deux mêmes endroits que la pose du
cookie. Aucun `UPDATE` sur le chemin des requêtes : la table ne participe pas au
trafic et n'ajoute aucun écrivain SQLite aux chemins chauds — point sur lequel le
projet vient de payer cher (courses de pochettes).

`expire_le` reprend exactement le calcul de `_duree_cookie()` dans
`app/routes/acces.py`, pour que la table dise la même chose que le cookie plutôt
qu'une approximation qui divergera.

Table neuve, donc `CREATE TABLE IF NOT EXISTS` suffit à mettre à niveau une base
existante — aucune migration de colonne. Elle est dans la base de prêt, donc
**incluse dans les sauvegardes**, et `_migrer_bases_restaurees()` la recrée après
restauration d'une archive antérieure (mécanique posée au volet 3 de D5).

### 4.5 « Actif » n'a pas le même sens pour les deux rôles

C'est le point délicat de la liste demandée, et il n'a pas de réponse unique.

**Bénévoles.** Un cookie de jeton cesse d'être valide pour deux raisons : son échéance
passe, ou **le jeton est réinitialisé** — ce qui invalide instantanément *tous* les
cookies (`auth.reinitialiser_jeton`). Une liste qui ne regarderait que `expire_le`
afficherait donc comme actifs des appareils morts depuis la dernière rotation.

D'où la colonne `generation` : une empreinte courte du jeton en vigueur au moment de
l'activation (8 caractères de `sha256(jeton)` — **jamais le jeton lui-même**, cf. §8).
Un appareil est actif si son échéance n'est pas passée **et** que son empreinte
correspond au jeton courant. Après une rotation, tous les anciens basculent seuls en
« périmé — jeton renouvelé », **sans aucune écriture** : le calcul se fait à la
lecture. Même principe que l'expiration de l'annonce d'écran de salle, où rien n'est
jamais purgé en base.

**Administration.** Les sessions admin vivent dans un dictionnaire **en mémoire du
process** (`admin_auth._sessions`) : un redémarrage du service les ferme toutes, et
aucune colonne de base ne peut le savoir. Plutôt que d'afficher un statut faux avec
une note d'excuse, mémoriser l'identifiant d'appareil **à côté de la session** dans ce
même dictionnaire (`ouvrir_session(appareil)`). La liste des postes d'administration
encore ouverts se lit alors directement en mémoire, et elle est exacte — y compris
après un redémarrage, où elle est vide, ce qui est la vérité.

La table fournit l'horodatage d'activation, la mémoire fournit « encore ouverte ».

### 4.4 Pourquoi pas l'adresse IP

Simon a corrigé à juste titre l'hypothèse initiale : les bénévoles passent par leur
forfait mobile, pas par un wifi partagé, donc leurs IP sont bien distinctes. Deux
raisons subsistent néanmoins de ne pas la stocker :

- **Elle est instable.** Les IP mobiles sont derrière le CGNAT de l'opérateur et
  changent en cours de journée. Comme clé d'appareil, elle est moins fiable qu'un
  cookie.
- **Elle est personnelle.** Une IP mobile propre à une personne, conservée dans un
  fichier, est une donnée personnelle au sens plein — davantage qu'une IP de wifi
  partagé. Et nginx la journalise déjà, avec horodatage, si un vrai besoin d'enquête
  survient un jour.

---

## 5. Écriture

### 5.1 Un logger dédié, jamais bloquant

Un module `app/journal.py` exposant une fonction unique :

```python
journaliser(request, module, action, objet=None, ref=None, ok=True, detail=None)
```

Elle construit la ligne, la sérialise et l'émet sur un logger `ludotex.journal`,
**détaché du logger racine** (`propagate = False`, sans quoi chaque ligne serait aussi
recopiée dans les logs uvicorn).

Impératif absolu : **`journaliser()` ne lève jamais**. Disque plein, permissions
refusées, fichier verrouillé — tout est avalé par un `try/except Exception` qui, au
pire, écrit un avertissement unique sur `uvicorn.error`. La règle « ne jamais
bloquer » du projet s'applique ici avec une force particulière : un journal qui
empêche un prêt est pire que pas de journal du tout.

### 5.2 Appelée depuis les routes, jamais depuis les services

Les services restent ignorants de FastAPI — c'est une constante du projet, déjà
appliquée à `app/exports.py`. Trois raisons concrètes :

- l'identifiant d'appareil et le type d'utilisateur ne s'obtiennent que depuis la
  `Request` ;
- les services sont appelés par les tests, les scripts d'import et le script de
  formation, qui n'ont aucune raison de polluer le journal ;
- une route sait si l'action a abouti (elle reçoit le résultat), un service intermédiaire
  pas toujours.

Coût : environ quarante points d'appel à terme, sur les 75 routes POST du dépôt. À
répartir sur plusieurs étapes (§11).

### 5.3 Déterminer `qui`

```
admin_auth.admin_connecte(request)  → "admin"
auth.acces_valide(request)          → "benevole"
sinon                               → "visiteur"
```

**L'ordre compte** : `auth.peut_ecrire` renvoie vrai pour un administrateur aussi
(il teste `admin_connecte` en premier). Tester le jeton d'abord étiquetterait « bénévole »
toute l'activité d'administration.

**Le mode ouvert doit être détecté.** Si aucun jeton n'est configuré, `acces_valide`
renvoie vrai pour tout le monde (mode développement, avertissement au démarrage dans
`app/main.py`) : tout le trafic serait alors étiqueté bénévole. Dans ce cas, écrire
`indetermine` plutôt que de mentir.

### 5.4 Fichier, rotation, et la contrainte du worker unique

`JOURNAL_PATH`, défaut `data/journal.log`.
`RotatingFileHandler(maxBytes=5_000_000, backupCount=5)` — stdlib, aucune dépendance.

⚠️ **`RotatingFileHandler` n'est sûr qu'avec un seul processus écrivain.** C'est le cas
aujourd'hui (`deploy/ludotex.service` lance uvicorn sans `--workers`, donc un worker,
et le commentaire du fichier explique pourquoi : les sessions admin et le compteur de
limitation de débit sont en mémoire du process). Si un jour l'application passe à
plusieurs workers, ce choix devra être revu — à écrire en commentaire dans le service
systemd, à côté de la contrainte déjà documentée.

### 5.5 La console pendant les tests

Un `StreamHandler` sur stderr, ajouté si `JOURNAL_CONSOLE=1`. Les lignes apparaissent
alors dans le terminal uvicorn, mêlées aux logs HTTP. C'est la réponse la plus directe
au besoin de phase de test : aucun `tail` à lancer, aucune fenêtre supplémentaire.

Sortie console **formatée pour l'œil**, pas en JSON — le fichier reste la source
machine :

```
12:37:02  benevole 3F1A9C  pret       retour            7 Wonders Duel
13:43:11  admin    B72E04  tournois   tournoi_cree      Chaussette
```

---

## 6. Les écrans d'administration

### 6.1 `/admin/journal`

Page en lecture seule derrière la garde admin existante (accès non authentifié →
redirection vers `/admin`, motif `_garde`, jamais un 403).

- Lit **uniquement le fichier courant**, en remontant depuis la fin par blocs pour ne
  jamais charger 5 Mo d'un coup. Par défaut les 200 dernières lignes.
- Filtres : module, type d'utilisateur, action, appareil, période, recherche texte dans
  `objet`. Panneau `<details class="recherche">` et puces de retrait, motif du catalogue.
- Rendu en `.admin-table` sous `.contenu-large` — composants existants
  (`docs/ui-composants.md`), aucune classe CSS nouvelle.
- Une ligne en échec (`ok: false`) se distingue visuellement ; réutiliser `.badge-attention`.
- Bouton de téléchargement du fichier brut.
- Fichier absent ou vide → message clair (« Aucune activité enregistrée pour
  l'instant. »), jamais une erreur.
- Une ligne illisible (fichier tronqué par une rotation en cours d'écriture) est
  ignorée en silence, pas affichée comme une erreur.
- Bloc `.aide-inline` renvoyant vers `/admin/aide`, et entrée dans la section « En cas
  de problème » de cette page.

Lien depuis le tableau de bord admin, groupe « Données & accès ».

### 6.2 La liste des appareils actifs

Demandée par Simon. Elle vit **sur `/admin/jeton`**, pas sur une page nouvelle : cette
page porte déjà le lien d'activation, l'échéance du jeton et le bouton de
réinitialisation — la liste de ceux qui ont utilisé ce lien y est à sa place, et le
projet a déjà tranché plusieurs fois dans ce sens (l'annonce d'écran de salle a rejoint
la page du titre plutôt que d'ouvrir la sienne).

Un tableau `.admin-table`, trié par activation décroissante :

| Appareil | Rôle | Activé le | Valable jusqu'au | Dernière activité |
|---|---|---|---|---|
| `3F1A9C` | Bénévole | 04/08/26 09:12 | 11/08/26 09:12 | 04/08/26 12:37 |
| `B72E04` | Administration | 04/08/26 08:40 | session en cours | 04/08/26 13:43 |

Trois précisions d'implémentation :

- **Seuls les appareils actifs sont listés** (§4.5), les périmés restent en base et
  n'apparaissent pas. Un repli sous la liste (« N appareils périmés ») permet de les
  déplier sans encombrer le cas courant.
- **« Dernière activité » se déduit du journal**, pas d'une colonne. Au rendu, on lit
  la fin du fichier et on retient l'horodatage le plus récent par appareil. Aucune
  écriture supplémentaire, et le journal était de toute façon déjà lu par
  `/admin/journal`. Contrepartie à assumer : la colonne reste vide pour un appareil
  inactif depuis assez longtemps pour être sorti de la fenêtre du fichier courant —
  afficher « — » et non « jamais ».
- **Les dates s'affichent en heure locale**, filtre Jinja existant.

Un compteur (« 12 appareils bénévoles actifs ») est l'information la plus utile au
premier coup d'œil : le jour de l'événement, il dit combien de téléphones ont
réellement activé l'accès. À placer avant le tableau.

⚠️ **Pas de bouton « révoquer ».** L'authentification bénévole compare le cookie au
jeton courant : il n'existe aucun moyen d'invalider un appareil seul. La seule action
possible est la réinitialisation du jeton, qui déconnecte **tous** les téléphones —
c'est déjà écrit dans `/admin/aide`. La page doit renvoyer vers ce bouton en le disant,
et surtout ne pas offrir un contrôle par ligne qui laisserait croire le contraire. Le
projet a déjà corrigé ce type d'erreur en écrivant `/admin/aide` (deux recours annoncés
qui n'existaient pas dans le code).

---

## 7. Lecture terminal — `scripts/journal.py`

Dans l'esprit de `scripts/import_csv.py` et `scripts/generate_qr.py` : stdlib
uniquement, exécutable par `python -m scripts.journal`.

```
python -m scripts.journal                      # les 50 dernières lignes, formatées
python -m scripts.journal -f                   # suivi en direct (équivalent tail -f)
python -m scripts.journal -f --module pret     # ... filtré
python -m scripts.journal --qui admin --depuis 12:00
python -m scripts.journal --brut | jq .        # passe-plat JSON
```

Deux détails d'implémentation à ne pas découvrir en route :

- **le suivi doit survivre à une rotation** : surveiller l'inode du fichier et rouvrir
  s'il change, sinon `-f` reste silencieusement collé sur l'ancien fichier renommé ;
- **la sortie formatée aligne les colonnes**, largeurs fixes, sans couleur (le
  terminal de destination est un SSH sur VPS, pas forcément un terminal riche).

L'existence de cet outil ne dispense pas du `tail -f data/journal.log | jq .` brut, qui
reste possible à tout moment — c'est le bénéfice du format JSON Lines.

---

## 8. Ce que le journal ne doit jamais contenir

Liste fermée, à tester automatiquement (§11, étape 2).

| Interdit | Pourquoi |
|---|---|
| **Numéro de pochette** | D5 vient précisément de le purger à la clôture ; un journal le ferait revivre indéfiniment, dans un fichier hors sauvegarde et hors purge |
| **Pseudos de tournoi, noms d'équipe, membres** | Le journal enregistre le fait (« une inscription au tournoi Catan »), `tournoi.db` garde qui — et lui seul |
| **Noms de bénévoles du planning** | Base séparée avec purge RGPD ; un journal ailleurs contournerait la purge |
| **Codes personnels** (désinscription tournoi, modification planning) | SEC-02 ; ils ne doivent pas davantage fuir ici que dans les logs nginx |
| **Jeton bénévole, mot de passe, hash** | Évident, à tester quand même |
| **Adresses IP** | §4.4 |
| **Query strings brutes** | Elles transportent `?jeton=` et `?code=` (SEC-02) |

Règle simple à retenir : **le journal enregistre l'objet, jamais la personne, jamais
le secret**.

La colonne `generation` de la table `appareils` (§4.5) mérite d'être explicitement
rattachée à cette liste : c'est une empreinte tronquée de `sha256(jeton)`, pas le
jeton. Elle sert uniquement à comparer deux générations entre elles et ne permet pas
de reconstituer la valeur d'origine.

### 8.1 Conséquence sur le statut RGPD

L'identifiant d'appareil est aléatoire, non rattaché à une personne, non recoupable
avec quoi que ce soit d'autre dans l'application. La phrase « zéro donnée personnelle »
de la brique de prêt **tient**, contrairement à ce qui était anticipé pour le module
d'audience (§3.3 de sa note, qui reposait sur un hash d'IP).

Reste que le dispositif enregistre l'activité d'une petite équipe identifiable dans le
temps, et que le registre des appareils est, lui, **persistant et sauvegardé** — il
survit donc à la rotation du journal. Trois garde-fous, à traiter comme tels et non
comme des détails :

- **rétention du journal** : les rotations au-delà de `backupCount` disparaissent
  d'elles-mêmes ; prévoir en plus une purge des rotations de plus d'un an ;
- **purge du registre** : supprimer les lignes `appareils` périmées depuis plus d'un an,
  à la clôture de fin d'événement — le geste existe déjà
  (`services.cloturer_tous_les_prets`) et c'est le moment naturel ;
- **mention sur `/apropos`**, seul endroit où l'application se décrit au public.

---

## 9. Points de contact avec l'existant

| Fichier | Ce qu'il faut y faire |
|---|---|
| `app/main.py` | Configurer le logger au démarrage, à côté des `init_db()` |
| `.env.example` | `JOURNAL_PATH`, `JOURNAL_CONSOLE` |
| `deploy/install.sh` | Poser `JOURNAL_PATH` dans le `.env` généré, sous le chemin des bases |
| `deploy/ludotex-formation.service` | `JOURNAL_PATH` distinct dans le fichier d'environnement de formation — sans quoi les deux instances écrivent dans le même fichier |
| `app/supervision.py` | Une ligne « Journal d'activité » (taille, date de la dernière action) dans le fragment `_supervision_contenu.html` |
| `.gitignore` | Ajouter `data/journal.log*` — **le cas n'est pas couvert** : les règles actuelles sont `data/*.db` et `data/*.sqlite*`, un `.log` serait suivi par git |
| `app/routes/admin.py` | Page `/admin/journal` + liste des appareils sur `/admin/jeton` + liens au tableau de bord + section dans `/admin/aide` |
| `tests/conftest.py` | Fixture autouse redirigeant `JOURNAL_PATH` vers un `tmp_path`, sinon la suite écrit dans le journal réel |
| `app/models.py`, `app/db.py` | Table `appareils` (§4.4) — `CREATE TABLE IF NOT EXISTS`, aucune migration de colonne |
| `app/admin_auth.py` | `ouvrir_session(appareil)` mémorise l'identifiant à côté de la session (§4.5) |
| `app/routes/scanner.py` | Afficher l'identifiant d'appareil, discrètement (arbitrage 4, validé) |
| `app/services.py` | Purge des appareils périmés dans `cloturer_tous_les_prets` (§8.1) |

**Ce qu'il ne faut *pas* faire :** ajouter le fichier journal à la sauvegarde.
`app/sauvegarde.py` valide qu'une archive contient **exactement** les trois bases
attendues ; y ajouter un fichier casserait la restauration de toutes les sauvegardes
déjà produites. Même arbitrage que celui rendu pour `mesure.db` (§5, point 3 de sa
note). Le journal n'est pas de la donnée métier : sa perte ne perd rien de
reconstituable.

La table `appareils`, elle, **est** sauvegardée — sans rien changer à `NOMS_BASES`,
puisqu'elle vit dans la base de prêt existante. C'est cohérent : contrairement au
journal, elle porte un état courant (qui a accès en ce moment) qu'on veut retrouver
après une restauration.

---

## 10. Arbitrages

**Déjà tranchés** (échange du 4 août) : identifiant d'appareil plutôt qu'adresse IP ;
JSON Lines ; fichier unique comme source de vérité ; **arbitrage 4 validé** (identifiant
affiché sur `/scanner`) ; liste des appareils actifs en administration, avec date et
heure d'activation.

**Restant à rendre :**

| # | Sujet | Recommandation |
|---|---|---|
| 1 | Périmètre initial : priorités 1 et 2 seulement, ou 3 aussi ? | **Les trois.** La priorité 3 est celle qui sert au dépannage le jour J et à la phase de test, et elle est bon marché (5 routes) |
| 2 | Journaliser les GET `/pret/{id}` (scan sans action) ? | **Non au départ.** Volume élevé, valeur incertaine ; à ajouter si le besoin se confirme |
| 3 | Rotation : 5 fichiers × 5 Mo | À confirmer. Ordre de grandeur : ~200 o/ligne, donc ~25 000 lignes par fichier, largement plus qu'un week-end |
| 5 | `JOURNAL_CONSOLE` actif par défaut en développement | **Non** — variable explicite, pour éviter qu'une production hérite du comportement par inadvertance |
| 6 | Mention sur `/apropos` | **Oui**, une phrase |
| 7 | Libellé libre par appareil (« comptoir 2 », « accueil ») | **Oui.** C'est ce qui transforme `3F1A9C` en information exploitable, sans que l'application ne déduise jamais rien elle-même. Voir la mise en garde ci-dessous |
| 8 | Liste des appareils visible aussi côté bénévole ? | **Non.** Rien n'en dépend pour prêter ou rendre, et c'est une surface d'information en plus sur une page très fréquentée |

**Mise en garde sur l'arbitrage 7.** Un champ libre saisi par l'administration
contournerait en une frappe tout le raisonnement du §4 si quelqu'un y écrit « téléphone
de Marie » : ce serait une donnée personnelle, entrée volontairement, dans la base de
prêt. La consigne — **désigner un poste, jamais une personne** — doit figurer sous le
champ lui-même et dans le wiki, pas seulement ici. Le nombre de bénévoles rend la
tentation réelle.

---

## 11. Plan d'action

Un commit par étape, tests à chaque fois, `CLAUDE.md` et `wiki/` tenus à jour — le
wiki dans son propre dépôt, poussé séparément.

**Étape 1 — identifiant d'appareil et registre.** Table `appareils` (`models.py`,
`db.py`), cookie posé sur `/acces` et `POST /admin/login`, `ouvrir_session(appareil)`,
services de lecture (`appareils_actifs`, empreinte de génération), affichage sur
`/scanner`. Rien n'est encore journalisé — cette étape se tient toute seule.
*Tests : le cookie n'est pas réécrit s'il existe déjà ; deux clients obtiennent deux
identifiants différents ; une ligne est créée à l'activation ; une réinitialisation de
jeton fait basculer tous les bénévoles en périmé **sans écriture** ; un appareil dont
l'échéance est passée n'est plus actif ; les sessions admin en mémoire sont vues comme
fermées après vidage du dictionnaire.*

**Étape 2 — liste des appareils sur `/admin/jeton`.** Tableau, compteur, repli des
périmés, libellé libre (arbitrage 7), renvoi vers la réinitialisation du jeton **sans**
bouton de révocation par ligne. La colonne « dernière activité » attend l'étape 4.
*Tests : garde admin ; seuls les actifs listés ; compteur juste ; libellé enregistré et
réaffiché ; aucun contrôle de révocation dans le rendu (garde-fou explicite).*

**Étape 3 — socle d'écriture du journal.** `app/journal.py` (constantes d'actions,
`journaliser`, configuration du logger), `JOURNAL_PATH`/`JOURNAL_CONSOLE`, branchement
dans `app/main.py`, fixture de test. Aucun point d'appel encore.
*Tests : ligne JSON valide et complète ; `journaliser` n'explose pas si le fichier n'est
pas inscriptible ; les trois publics correctement distingués ; le mode ouvert donne
`indetermine` ; une action inconnue est refusée (vocabulaire fermé).*

**Étape 4 — points d'appel priorité 1** (administration et configuration), plus la
colonne « dernière activité » de l'étape 2, qui se déduit du journal. C'est l'étape à
valeur immédiate.
*Tests : chaque action produit une ligne ; **le garde-fou d'interdiction** — un scénario
complet (prêt, retour, inscription à un tournoi, activation de jeton, restauration)
suivi d'une recherche dans le fichier produit : aucun numéro de pochette, aucun pseudo,
aucun code, aucun jeton, aucune IP ; dernière activité vide et non « jamais » quand
l'appareil est sorti de la fenêtre du fichier.*

**Étape 5 — écran `/admin/journal`.** Lecture par la fin, filtres, puces, téléchargement,
lien au tableau de bord, `.aide-inline`, section dans `/admin/aide`.
*Tests : garde admin ; fichier absent → message et pas d'erreur ; filtres ; ligne
corrompue ignorée ; les 200 dernières lignes seulement.*

**Étape 6 — outil terminal `scripts/journal.py`.** Formatage, filtres, `--brut`, `-f`
avec réouverture sur rotation.
*Tests : parsing et filtres sur un fichier d'exemple ; `--brut` restitue le JSON à
l'identique. Le suivi `-f` n'est pas testable simplement — vérification manuelle
documentée.*

**Étape 7 — points d'appel priorités 2 et 3.** Tournois, programme, planning, puis
prêts. Peut se découper en deux commits.
*Tests : les échecs sont journalisés (`deja_sorti`, `occupe`) ; un service appelé hors
requête HTTP (script d'import, formation) n'écrit rien.*

**Étape 8 — finitions.** Ligne de supervision, purge des rotations anciennes et des
appareils périmés à la clôture, mention `/apropos`, page wiki `Journal-Activite.md`,
`docs/vocabulaire.md` si le mot « journal » entre en collision avec un autre usage.

L'ordre a été revu par rapport à la première rédaction : les étapes 1 et 2 (appareils)
passent devant le journal parce qu'elles ne dépendent de rien, livrent une valeur
autonome — savoir combien de téléphones ont activé l'accès est déjà utile sans une
seule ligne de journal — et fournissent au journal son champ `appareil` déjà éprouvé.

**Version proposée** à l'issue des étapes 1 à 6 : **1.3.0** (nouvelle fonctionnalité
d'administration, sans casse). Les étapes 7 et 8 sont des correctifs successifs.

---

## 12. Ce que ce journal ne résoudra pas

À dire d'emblée pour éviter la déception :

- **Il ne dira jamais qui, nominativement.** Le jeton est partagé et il n'y a pas de
  comptes individuels — c'est un choix fondateur du projet. Les comptes nominatifs sont
  un autre chantier. L'identifiant d'appareil
  et son libellé libre s'en approchent, mais restent déclaratifs : l'application ne
  déduit jamais rien elle-même.
- **Il ne permettra pas de couper l'accès à un appareil.** L'authentification bénévole
  compare le cookie au jeton courant : le seul levier est la réinitialisation du jeton,
  qui déconnecte tout le monde (§6.2). La liste des appareils constate, elle n'agit pas.
- **Il ne remplace pas les sauvegardes.** Il constate qu'une action a eu lieu ; il ne
  permet pas de revenir en arrière.
- **Il ne mesure pas l'audience.** Aucune consultation n'y figure.
- **Il n'est pas une preuve.** Fichier local, modifiable par quiconque a un accès
  serveur, non signé, non horodaté par un tiers.
