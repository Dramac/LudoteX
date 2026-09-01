# Licence des éléments graphiques de LudoteX

Ce dossier (`logo/`) contient l'identité visuelle de LudoteX : le symbole
(un meeple contenant un rayonnage de ludothèque), le logotype, et leurs
déclinaisons.

## Domaine public — CC0 1.0 Universal

Ces fichiers sont **versés au domaine public** sous
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/deed.fr).
Vous pouvez les copier, modifier et redistribuer, y compris à des fins
commerciales, sans demander d'autorisation et sans obligation d'attribution.

**Pourquoi CC0 plutôt que la GPLv3 du code.** Le dessin d'origine a été
**produit par un outil d'intelligence artificielle générative**. Le droit
d'auteur suppose une création humaine originale : une image purement générée
n'a donc vraisemblablement aucun titulaire de droits. Placer ces fichiers sous
une licence qui concède des droits reviendrait à affirmer une titularité
incertaine. CC0 dit les choses telles qu'elles sont et n'impose rien à
personne.

Le code de l'application, lui, reste sous **GNU GPL v3** (voir `LICENSE` à la
racine du dépôt). Les deux licences coexistent sans difficulté : elles
couvrent des objets différents.

## Usage du nom et du logo

Le droit d'auteur n'est pas ce qui protège une identité — c'est le rôle d'une
marque, et LudoteX n'en a pas déposé. Ce qui suit n'est donc pas une
obligation juridique, mais une demande de courtoisie, celle qui a cours dans
le logiciel libre :

> Si vous publiez une version modifiée de LudoteX, **donnez-lui un autre nom et
> un autre logo**. Cela évite qu'un utilisateur attribue au projet d'origine un
> comportement, un défaut ou un engagement de support qui ne viennent pas de
> lui.

Utiliser le nom et le logo pour **parler** de LudoteX — un article, une
présentation, une capture d'écran, un annuaire de logiciels libres — est
naturellement libre et bienvenu.

## Ce que contient le dossier

| Chemin | Usage |
|---|---|
| `01_Symbole_sans_logotype/master/` | Source du symbole (meeple jaune, rayonnages blancs, fond transparent). Usage par défaut sur le web, sur fond clair comme sur fond sombre. |
| `01_Symbole_sans_logotype/declinaisons/svg/…_mono.svg` | Symbole d'une seule couleur, prise dans `currentColor`. Les rayonnages sont des découpes : lisible sur n'importe quel fond. Réservé aux contextes à une seule encre — l'impression des étiquettes avant tout. |
| `01_Symbole_sans_logotype/png/` | Rendus matriciels du symbole, fond transparent. |
| `02_Logotype_complet/master/` | Source éditable du logotype. Le mot y est du **texte vivant** en Poppins : à ne pas diffuser tel quel. |
| `02_Logotype_complet/declinaisons/svg/` | Logotype **vectorisé** (texte converti en tracés) : `color` pour fond clair, `sombre` pour fond sombre, `mono` pour une seule encre. Ce sont ces fichiers qu'il faut diffuser. |
| `02_Logotype_complet/png/` | Rendus matriciels du logotype, fond transparent. |

Le logotype complet sert la **communication du projet** — README, site de
présentation, image de partage, annuaires. Il n'entre pas dans l'application :
dans une instance déployée, le bandeau porte le nom de l'association qui
l'utilise, pas celui de LudoteX.

## Composition

Symbole et logotype : jaune `#f4c300`, blanc `#ffffff`, noir `#111111`.
Logotype composé en **Poppins**, distribuée sous
[SIL Open Font License 1.1](https://openfontlicense.org/) — le texte étant
vectorisé dans les fichiers diffusés, aucune police n'est redistribuée ici et
aucune n'est nécessaire à l'affichage.
