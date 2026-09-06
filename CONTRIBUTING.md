# Contribuer à LudoteX

Merci de l'intérêt porté au projet. Cette page tient en une lecture : elle dit
comment signaler un bug utilement, comment proposer un changement, et ce qui
entre — ou non — dans le périmètre du projet.

Le fonctionnement du code (stack, structure, lancer en local, lancer les tests,
conventions) est décrit ailleurs et n'est pas répété ici :
[docs/guide-developpeur.md](docs/guide-developpeur.md) pour reprendre le code,
[docs/lancement-local.md](docs/lancement-local.md) pour le faire tourner sur son
poste.

## Signaler un bug

Les tickets se déposent dans les **issues** du dépôt. Un rapport utile tient en
quelques lignes, à condition qu'elles portent les bonnes informations :

- **la version**, telle qu'elle s'affiche en bas de la page « À propos »
  (`/apropos`) ou sur l'écran de supervision (`/admin/supervision`) ;
- **le navigateur et l'appareil** : « Chrome sur Android 14 », « Safari sur
  iPhone », « Firefox sur un PC ». Beaucoup de défauts d'affichage ou de
  scanner ne se voient que sur une combinaison précise ;
- **ce que vous attendiez**, et **ce qui s'est passé à la place**. Les deux :
  « ça ne marche pas » ne dit ni l'un ni l'autre ;
- **comment y arriver à nouveau** : la suite de gestes, depuis quel écran ;
- **le message d'erreur exact** s'il y en a un, recopié ou en capture.

Deux précautions qui comptent ici :

- **Aucune donnée personnelle dans un ticket.** L'application n'en stocke pas ;
  une capture d'écran ne doit pas en introduire. Masquez ce qui traîne à
  l'écran.
- **Aucun secret** : jeton bénévole, mot de passe d'administration, contenu du
  `.env`. Un jeton collé dans un ticket public est un jeton à renouveler
  immédiatement.

Si le défaut concerne la **sécurité**, n'ouvrez pas de ticket public : écrivez
d'abord, en privé, à la personne qui tient le dépôt.

## Proposer un changement

**Ouvrez une issue avant d'écrire du code.** Ce n'est pas une formalité : une
proposition peut être hors périmètre (voir plus bas), déjà tranchée autrement,
ou toucher un invariant qui ne se devine pas à la lecture. Mieux vaut le
découvrir avant d'écrire que dans une pull request.

Une proposition retenue devient une pull request qui :

- **traite un seul sujet** — un commit par point traité, message en français ;
- **arrive avec ses tests** : chaque changement de comportement s'accompagne des
  tests qui le décrivent, et la suite `pytest` doit rester verte ;
- **respecte les conventions du projet** : pages rendues côté serveur (pas de
  SPA), CSS mobile-first sans framework ni étape de build, **aucune dépendance
  chargée depuis un CDN**, français dans l'interface comme dans le code ;
- **ne fait entrer aucune donnée personnelle**. Si une évolution en suppose
  une, dites-le explicitement dans l'issue : c'est un choix de conception, pas
  un détail d'implémentation ;
- **met à jour la documentation** quand un écran, un libellé, une URL publique
  ou un comportement visible change.

Le code contribué est publié sous **GPLv3**, comme le reste du projet.

## Le périmètre

LudoteX vise **un usage événementiel** : on ouvre, on prête, on récupère tout,
on clôt. Ce cadre est ce qui permet à l'application de rester simple, sans
compte utilisateur ni donnée personnelle — c'est-à-dire utilisable par des
bénévoles qui la découvrent le matin même.

Toutes les idées n'y rentrent pas, et le README dit lesquelles :
[« Ce que ça ne fait pas »](README.md#ce-que-ça-ne-fait-pas). Une fonctionnalité
qui suppose un fichier d'adhérents, un prêt sur plusieurs semaines, ou un compte
par visiteur ne sera pas retenue — non parce qu'elle est mauvaise, mais parce
qu'elle appartient à un autre logiciel.

Entre en revanche volontiers dans le périmètre ce qui rend le geste du jour J
plus sûr ou plus rapide, ce qui aide un bureau non technicien à s'en sortir
seul, ce qui améliore l'accessibilité, et ce qui rend le déploiement plus facile
pour une association qui reprend l'outil.

## Réponses et délais

LudoteX est développé et maintenu par une seule personne, sur son temps libre.
Les tickets sont lus, mais **aucun délai n'est garanti** — voir la section
« Support » du [README](README.md#support). Une pull request qui reste sans
réponse quelque temps n'est pas une pull request rejetée.
