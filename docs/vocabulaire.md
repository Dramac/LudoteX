# Vocabulaire de l'interface — les mots officiels

Ce document fige le nom de chaque objet **tel qu'il apparaît à l'écran**. Il
est né de la fiche **D3** de `docs/audit-ux-2026-07-18.md` : le mot
« emplacement » désignait alors **trois objets différents**, sur des écrans
que le même bénévole enchaîne en trente secondes.

Le cas qui a déclenché l'arbitrage : au retour d'une boîte rangée, l'écran
affichait « Récupérer la pièce d'identité à l'**emplacement** n° 7 » en très
gros, puis, juste dessous, « Où ranger le jeu : **emplacement** Étagère 3 ».
Deux endroits physiques différents, à dix centimètres l'un de l'autre, sous
le même mot.

## Les trois objets et leur nom officiel

| Objet | Nom à l'écran | Où |
|---|---|---|
| Casier numéroté où est déposée la pièce d'identité pendant un prêt | **pochette** | écrans bénévole (`/pret`, `/scanner`, `/stats` § jeux sortis) et page d'aide |
| Lieu où se tient un tournoi | **lieu** (« Lieu / table ») | partout |
| Place d'une boîte de jeu, à l'événement ou au local | **rangement**, et **emplacement** pour une place nommée de la liste | module Rangement |

## Règles

1. **« Emplacement » est réservé au module Rangement.** Il n'y désigne plus
   qu'une seule chose — la place d'une boîte — donc il n'y a plus
   d'ambiguïté à lever. Ne pas le réintroduire pour parler d'une pochette ou
   du lieu d'un tournoi.
2. **« Pochette » ne s'écrit jamais à côté d'un NUMÉRO sur un écran public.**
   C'est la contrainte de sécurité d'origine, et elle reste entière : un
   numéro de pochette dit où se trouve une pièce d'identité. Concrètement :
   - `/live` (projeté en salle) n'affiche aucun numéro, et un test le
     vérifie, mot compris ;
   - sur `/stats`, page publique, la colonne « Pochette » de « Jeux
     actuellement sortis » n'est rendue que pour `est_benevole(request)` ;
   - le numéro est de toute façon **effacé à la clôture du prêt** (fiche D5,
     `docs/specification.md` §3.2).

   Le **mot** seul, lui, n'est pas sensible : le wiki public et la
   spécification l'emploient depuis toujours. C'est pourquoi la page d'aide
   `/aide`, bien que publique, dit « pochette » — elle décrit le geste du
   bénévole et doit employer les mots de l'écran qu'elle explique
   (arbitrage Simon, 2026-07-18).
3. **Ne renommer que des chaînes AFFICHÉES.** Aucun nom de colonne
   (`prets.numero_pochette`, `tournois.emplacement`), de champ de formulaire
   (`name="emplacement"`), de clé de dictionnaire, d'en-tête CSV ou de champ
   `.ics` ne doit suivre : le gain serait nul et le risque de régression réel
   (imports, exports, tests, sauvegardes restaurées).

   À noter : le renommage **rapproche** l'interface du code, où
   `numero_pochette` et la table `pochettes` sont les noms d'origine. C'est
   l'euphémisme d'affichage qui disparaît, pas une abstraction qui s'ajoute.

## Garde-fous en place

- `tests/test_rangement.py::test_d3_mot_emplacement_absent_des_ecrans_de_pret`
  — le mot ne doit pas revenir sur `/pret` ni `/aide`. Le test porte sur le
  HTML **rendu**, donc il ignore les noms de variables et de champs, qui eux
  ne bougent pas.
- `tests/test_rangement.py::test_d3_retour_dune_boite_rangee_deux_noms_distincts`
  — le scénario qui a motivé la fiche.
- `tests/test_routes.py::test_live_page` / `test_live_data` — absence du mot
  **et** du numéro sur l'écran de salle.
