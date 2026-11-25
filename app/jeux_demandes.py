"""
Jeux demandés au comptoir — noter, regrouper, fusionner, purger (série
comptoir, lot 11 ; conception : `docs/conception-jeux-demandes.md`).

Un visiteur demande un jeu que la ludothèque n'a pas, ou dont toutes les
boîtes sont dehors. Le bénévole le note en un geste (`/jeux-demandes`) ; le
bureau relit la liste groupée, fusionne les graphies (« Catane » → « Catan »),
exporte pour ses achats et purge après l'événement (`/admin/jeux-demandes`).

UNE PORTE À DONNÉE PERSONNELLE
------------------------------
Le nom est un texte libre tapé par n'importe quel bénévole : quelqu'un finira
par y écrire « Catan pour Mme Untel » (CLAUDE.md, « Un champ de texte libre
est une porte »). Cinq conditions, acceptées par Simon le 2026-11-18, et où
elles sont tenues :

1. champ court (`LONGUEUR_MAX_NOM`), consigne visible sous lui
   (`CONSIGNE`, gabarit `jeux_demandes.html`) ;
2. saisie et lecture par les bénévoles (jeton) et le bureau seulement, jamais
   en public : routes `routes/jeux_demandes.py`, lien du catalogue conditionné
   à `est_benevole`, aucune lecture depuis /stats, /live ni un export public ;
3. le texte n'entre jamais au journal : les routes journalisent un NOMBRE,
   jamais un nom (contrôle statique dans `tests/test_jeux_demandes.py`) ;
4. le bureau relit, fusionne et purge ; toute suppression passe par ce module,
   sous `secure_delete`, et se vérifie sur les octets du fichier ;
5. suggestions par `<datalist>`, sans JS.

INVARIANTS
----------
- Tout vit dans la base de PRÊT ; rien ici ne lit une autre base.
- Aucune colonne d'origine : on ne sait pas QUI a noté une demande.
- Aucune journalisation ici : seules les routes journalisent.
- Rien ne refuse brutalement : un nom refusé lève `NomEphemereRefuse`, dont
  le message est une phrase ; un double envoi n'écrit rien et le dit.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import NamedTuple

from app import services

# Même borne et même validation que le nom d'un jeu éphémère (lot 8a) : un
# nom de jeu tapé au comptoir, une seule règle. `valider_nom` lève
# `services.NomEphemereRefuse` (raison `vide` ou `long`).
LONGUEUR_MAX_NOM = services.LONGUEUR_MAX_NOM_EPHEMERE
valider_nom = services.valider_nom_ephemere
NomRefuse = services.NomEphemereRefuse

# La consigne sous le champ, citée mot pour mot par le wiki.
CONSIGNE = "Le nom du jeu seulement — jamais celui de la personne qui le demande."


def nouveau_jeton() -> str:
    """Valeur du champ caché d'un formulaire affiché (voir `noter`)."""
    return secrets.token_urlsafe(16)


class DemandeNotee(NamedTuple):
    """Issue de `noter` : le nom du groupe, son total, et si l'envoi était un doublon."""
    nom: str
    cle: str
    total: int
    doublon: bool


def _noms_affiches(conn: sqlite3.Connection) -> dict[str, str]:
    """
    {cle: nom affiché} : pour chaque groupe, la graphie la plus fréquente, et
    à égalité la plus récente. Sur toute la table, quelle que soit la
    période regardée : le nom d'un groupe ne change pas selon le filtre.
    """
    meilleurs: dict[str, tuple[int, int, str]] = {}
    for ligne in conn.execute(
            "SELECT cle, nom, COUNT(*) AS n, MAX(id) AS dernier "
            "FROM jeux_demandes GROUP BY cle, nom"):
        rang = (ligne["n"], ligne["dernier"], ligne["nom"])
        if ligne["cle"] not in meilleurs or rang > meilleurs[ligne["cle"]]:
            meilleurs[ligne["cle"]] = rang
    return {cle: rang[2] for cle, rang in meilleurs.items()}


def suggestions(conn: sqlite3.Connection) -> list[str]:
    """
    Les demandes déjà notées, un nom par groupe (le plus fréquent), dans
    l'ordre alphabétique : le `<datalist>` du champ. Choisir une suggestion
    donne la même clé, donc la demande se compte au lieu de s'écrire autrement.
    """
    return sorted(_noms_affiches(conn).values(), key=str.casefold)


def noter(conn: sqlite3.Connection, nom: str | None, jeton: str | None) -> DemandeNotee:
    """
    Note une demande. `nom` passe par `valider_nom` (lève `NomRefuse` : rien
    n'est écrit). Le groupe est `services.slug_titre(nom)` : « catan »,
    « CATAN » et « Catan » se comptent ensemble.

    DOUBLE ENVOI. Le formulaire porte un jeton aléatoire, renouvelé à chaque
    affichage (`nouveau_jeton`). Un second envoi du MÊME formulaire (double
    appui, wifi lent qui renvoie) ne crée rien : `doublon` est vrai et le
    total est celui d'après le premier envoi. Deux demandes réelles du même
    jeu, coup sur coup, passent toutes deux : chacune vient d'un formulaire
    affiché à neuf. Sans jeton (formulaire forgé), la demande est notée.
    """
    nom = valider_nom(nom)
    cle = services.slug_titre(nom)
    with services.transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO jeux_demandes (nom, cle, demandee_le, jeton_formulaire) "
            "VALUES (?, ?, ?, ?)",
            (nom, cle, services.maintenant(), (jeton or "").strip() or None))
        doublon = cur.rowcount == 0
        (total,) = conn.execute(
            "SELECT COUNT(*) FROM jeux_demandes WHERE cle = ?", (cle,)).fetchone()
    return DemandeNotee(_noms_affiches(conn).get(cle, nom), cle, total, doublon)


def _titres_par_cle(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    {clé de regroupement: titre du catalogue, avec `total` (boîtes au parc)
    et `dehors` (boîtes sorties)}. Clé = `slug_titre` du NOM du titre, et sa
    référence aussi : une référence venue du tableur peut différer du nom.
    """
    au_parc = services.condition_au_parc("e")
    titres: dict[str, dict] = {}
    for ligne in conn.execute(f"""
            SELECT t.reference_titre, t.nom,
                   SUM(CASE WHEN {au_parc} THEN 1 ELSE 0 END) AS total,
                   SUM(CASE WHEN {au_parc} AND p.id_pret IS NOT NULL THEN 1 ELSE 0 END)
                       AS dehors
            FROM titres t
            LEFT JOIN exemplaires e ON e.reference_titre = t.reference_titre
            LEFT JOIN prets p ON p.id_exemplaire = e.id_exemplaire
                              AND p.date_retour IS NULL
            GROUP BY t.reference_titre, t.nom"""):
        titre = {"reference_titre": ligne["reference_titre"], "nom": ligne["nom"],
                 "total": ligne["total"] or 0, "dehors": ligne["dehors"] or 0}
        titres.setdefault(services.slug_titre(ligne["nom"]), titre)
        titres.setdefault(ligne["reference_titre"], titre)
    return titres


def au_catalogue(conn: sqlite3.Connection, cle: str) -> dict | None:
    """Le titre du catalogue qui porte cette clé (avec `total`, `dehors`), ou None."""
    return _titres_par_cle(conn).get(cle)


def groupes(conn: sqlite3.Connection, debut_utc: str | None = None,
            fin_utc: str | None = None) -> list[dict]:
    """
    Les demandes groupées par clé, du plus demandé au moins demandé : chaque
    groupe `{cle, nom, nombre, premiere, derniere, titre}`, où `titre` est le
    jeu du catalogue de même clé ou None. `debut_utc` (inclus) et `fin_utc`
    (exclu) bornent la période (une édition) ; les dates sont en UTC ISO.
    """
    conditions, params = [], []
    if debut_utc:
        conditions.append("demandee_le >= ?")
        params.append(debut_utc)
    if fin_utc:
        conditions.append("demandee_le < ?")
        params.append(fin_utc)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    noms = _noms_affiches(conn)
    titres = _titres_par_cle(conn)
    resultat = [
        {"cle": l["cle"], "nom": noms.get(l["cle"], l["cle"]), "nombre": l["nombre"],
         "premiere": l["premiere"], "derniere": l["derniere"],
         "titre": titres.get(l["cle"])}
        for l in conn.execute(
            "SELECT cle, COUNT(*) AS nombre, MIN(demandee_le) AS premiere, "
            f"MAX(demandee_le) AS derniere FROM jeux_demandes{where} GROUP BY cle",
            params)
    ]
    resultat.sort(key=lambda g: (-g["nombre"], g["nom"].casefold()))
    return resultat


def total(conn: sqlite3.Connection) -> int:
    """Nombre de demandes en base, toutes périodes."""
    return conn.execute("SELECT COUNT(*) FROM jeux_demandes").fetchone()[0]


def fusionner(conn: sqlite3.Connection, cle_source: str, cle_cible: str) -> int:
    """
    Fond le groupe `cle_source` dans `cle_cible` : ses lignes prennent la clé
    ET le nom affiché de la cible ; dates et nombre sont gardés. Renvoie le
    nombre de lignes déplacées — 0 si l'un des deux groupes n'existe plus
    (page restée ouverte, double envoi) ou s'ils sont le même : rien n'est
    écrit, et la route le dit.

    Le nom tapé de la source est réécrit, pas conservé à côté : moins de texte
    libre en base, et le groupe fusionné s'affiche sous le nom choisi.
    `secure_delete` : l'ancien texte ne survit pas dans les pages libérées.
    """
    if not cle_source or not cle_cible or cle_source == cle_cible:
        return 0
    conn.execute("PRAGMA secure_delete = ON")
    with services.transaction(conn):
        nom_cible = _noms_affiches(conn).get(cle_cible)
        if nom_cible is None:
            return 0
        cur = conn.execute("UPDATE jeux_demandes SET cle = ?, nom = ? WHERE cle = ?",
                           (cle_cible, nom_cible, cle_source))
    return cur.rowcount


def supprimer_groupe(conn: sqlite3.Connection, cle: str) -> int:
    """
    Efface toutes les demandes du groupe `cle`, sous `secure_delete` (le texte
    est écrasé dans le fichier, pas seulement retiré de la table). Renvoie le
    nombre de lignes effacées, 0 si le groupe n'existe plus.
    """
    conn.execute("PRAGMA secure_delete = ON")
    with services.transaction(conn):
        cur = conn.execute("DELETE FROM jeux_demandes WHERE cle = ?", (cle,))
    return cur.rowcount


def purger(conn: sqlite3.Connection) -> int:
    """
    Efface TOUTES les demandes, sous `secure_delete` ; renvoie leur nombre.
    Bouton « Tout purger » du bureau, et réinitialisation du site de
    formation. Se greffe sur une transaction en cours, sinon ouvre la sienne.

    Les ARCHIVES déjà écrites gardent les demandes jusqu'à leur rotation
    (`sauvegarde.DUREE_VIE_ARCHIVES_JOURS`) ; celles écrites après la purge
    n'en portent plus un octet (`sauvegarde._compacter`, `VACUUM` des
    copies). Même règle que la purge du planning, dite dans `wiki/Rgpd.md`.
    """
    conn.execute("PRAGMA secure_delete = ON")
    with services.transaction(conn):
        cur = conn.execute("DELETE FROM jeux_demandes")
    return cur.rowcount
