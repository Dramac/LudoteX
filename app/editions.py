"""
Éditions de l'événement — des périodes nommées, datées par le bureau.

Série « bilan », lot 3. Table `editions_evenement` de la base de PRÊT (voir
`app/models.py`, qui dit pourquoi ce nom et pourquoi des jours plutôt que des
heures). Gérées depuis « Gestion de l'événement » (`/admin/evenement`), lues
par la page des statistiques, qui propose « Festival du jeu 2026 — 14 et 15
novembre » au lieu de deux champs de date à retaper chaque année.

CE QU'UNE ÉDITION N'EST PAS
---------------------------
- Ni une donnée des prêts : aucun prêt n'y est rattaché. Une édition est une
  paire de bornes ; la supprimer retire un nom, jamais un prêt.
- Ni une « édition » de la tombola (`tombola_editions`) : une session de
  tirages, ouverte au premier tirage, fermée par la clôture. Les deux tables
  ne se lisent pas l'une l'autre.
- Ni la source du nom et du premier jour de l'événement (`evenement_nom`,
  `evenement_date`), qui servent l'accueil et le programme et restent réglés
  à part. Arbitrage du lot : pas de couplage automatique, seulement un
  préremplissage (voir la route `/admin/evenement`).

RÈGLES
------
- Deux éditions ne se chevauchent pas (refus à l'enregistrement, avec le nom
  de l'autre) : un prêt compterait dans les deux bilans, et la comparaison de
  deux éditions (lot bilan-8) n'aurait plus de sens. Un événement par an.
- Un premier jour postérieur au dernier est refusé ; un dernier jour laissé
  vide vaut le premier (événement d'un seul jour) ; un nom laissé vide devient
  « Édition AAAA » : ce qu'on peut deviner sans risque ne bloque jamais.
- Comme les autres services du prêt : la connexion arrive en paramètre, les
  écritures committent, aucune journalisation ici (c'est la route qui
  journalise).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from app import services

# Même borne et même normalisation que le nom de l'événement : c'est le même
# genre de texte, publié par le bureau.
LONGUEUR_NOM = services.LONGUEUR_NOM_EVENEMENT

# Un seul domicile pour les mois en français : la frise du programme les a
# déjà. Le module ne lit aucune base : l'importer ne traverse rien.
from app.tournoi.creneau import _MOIS_FR  # noqa: E402

# Messages de refus, par champ. Importés par les tests plutôt que recopiés.
MESSAGE_PREMIER_VIDE = "Indiquez le premier jour de l'édition."
MESSAGE_DATE_INVALIDE = "Date invalide (format attendu : AAAA-MM-JJ)."
MESSAGE_ORDRE = "Le dernier jour doit être le même que le premier, ou après."


def normaliser_nom(saisie: str | None) -> str:
    """Espaces réduits et longueur bornée, comme `evenement_nom`."""
    return " ".join((saisie or "").split())[:LONGUEUR_NOM]


def _jour(saisie: str) -> date | None:
    try:
        return datetime.strptime(saisie, "%Y-%m-%d").date()
    except ValueError:
        return None


def lire_saisie(nom: str | None, premier_jour: str | None,
                dernier_jour: str | None) -> tuple[dict, dict]:
    """
    Contrôle un envoi du formulaire, sans rien lire en base.

    Returns:
        (valeurs, erreurs). `valeurs` porte TOUJOURS ce qui a été tapé
        (normalisé), pour réafficher le formulaire tel quel ; `erreurs` associe
        un champ (`premier_jour`, `dernier_jour`) à son message. Sans erreur,
        `valeurs` est prête à écrire : nom par défaut posé, dernier jour
        complété.
    """
    nom = normaliser_nom(nom)
    premier = (premier_jour or "").strip()
    dernier = (dernier_jour or "").strip()
    valeurs = {"nom": nom, "premier_jour": premier, "dernier_jour": dernier}
    erreurs: dict[str, str] = {}

    jour_premier = _jour(premier) if premier else None
    if not premier:
        erreurs["premier_jour"] = MESSAGE_PREMIER_VIDE
    elif jour_premier is None:
        erreurs["premier_jour"] = MESSAGE_DATE_INVALIDE

    jour_dernier = _jour(dernier) if dernier else jour_premier
    if dernier and jour_dernier is None:
        erreurs["dernier_jour"] = MESSAGE_DATE_INVALIDE
    elif jour_premier and jour_dernier and jour_dernier < jour_premier:
        erreurs["dernier_jour"] = MESSAGE_ORDRE

    if not erreurs:
        valeurs["dernier_jour"] = jour_dernier.isoformat()
        valeurs["premier_jour"] = jour_premier.isoformat()
        if not nom:
            valeurs["nom"] = f"Édition {jour_premier.year}"
    return valeurs, erreurs


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------
def _dict(ligne: sqlite3.Row | None) -> dict | None:
    return dict(ligne) if ligne is not None else None


def lister(conn: sqlite3.Connection) -> list[dict]:
    """Toutes les éditions, de la plus récente à la plus ancienne."""
    lignes = conn.execute(
        "SELECT id, nom, premier_jour, dernier_jour FROM editions_evenement "
        "ORDER BY premier_jour DESC, id DESC"
    ).fetchall()
    return [dict(l) for l in lignes]


def lire(conn: sqlite3.Connection, id_edition: int) -> dict | None:
    """Une édition par son identifiant, ou None."""
    return _dict(conn.execute(
        "SELECT id, nom, premier_jour, dernier_jour FROM editions_evenement "
        "WHERE id = ?", (id_edition,)).fetchone())


def chevauchement(conn: sqlite3.Connection, premier_jour: str, dernier_jour: str,
                  sauf_id: int | None = None) -> dict | None:
    """
    L'édition (autre que `sauf_id`) dont les jours croisent ceux donnés, ou
    None. Dates 'AAAA-MM-JJ' : l'ordre des chaînes est celui des jours.
    """
    return _dict(conn.execute(
        "SELECT id, nom, premier_jour, dernier_jour FROM editions_evenement "
        "WHERE premier_jour <= ? AND dernier_jour >= ? AND id IS NOT ? "
        "ORDER BY premier_jour LIMIT 1",
        (dernier_jour, premier_jour, sauf_id)).fetchone())


def par_defaut(conn: sqlite3.Connection, aujourd_hui: date | None = None) -> dict | None:
    """
    L'édition que la page des statistiques montre sans paramètre : la plus
    récente dont le premier jour est arrivé (une édition en cours compte, une
    édition future non). None s'il n'y en a pas : la page montre alors toute
    la période, exactement comme avant ce lot.
    """
    jour = (aujourd_hui or datetime.now(services.FUSEAU_LOCAL).date()).isoformat()
    return _dict(conn.execute(
        "SELECT id, nom, premier_jour, dernier_jour FROM editions_evenement "
        "WHERE premier_jour <= ? ORDER BY premier_jour DESC, id DESC LIMIT 1",
        (jour,)).fetchone())


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
def enregistrer(conn: sqlite3.Connection, valeurs: dict,
                id_edition: int | None = None) -> tuple[str, dict | None]:
    """
    Crée (`id_edition` None) ou corrige une édition, `valeurs` venant de
    `lire_saisie` sans erreur.

    La lecture du chevauchement et l'écriture forment un seul bloc
    (`services.transaction`) : deux onglets qui enregistrent en même temps des
    dates voisines ne passent pas tous les deux.

    Returns:
        (issue, édition) avec issue parmi :
        - « creee », « modifiee » : l'édition telle qu'enregistrée ;
        - « inchangee » : envoi identique à ce qui est en base, rien écrit ;
        - « introuvable » : l'édition à corriger n'existe plus, None ;
        - « chevauchement » : rien écrit, l'édition qui gêne.
    """
    with services.transaction(conn):
        if id_edition is not None:
            avant = lire(conn, id_edition)
            if avant is None:
                return "introuvable", None
        autre = chevauchement(conn, valeurs["premier_jour"], valeurs["dernier_jour"],
                              sauf_id=id_edition)
        if autre is not None:
            return "chevauchement", autre
        if id_edition is None:
            curseur = conn.execute(
                "INSERT INTO editions_evenement (nom, premier_jour, dernier_jour) "
                "VALUES (?, ?, ?)",
                (valeurs["nom"], valeurs["premier_jour"], valeurs["dernier_jour"]))
            return "creee", lire(conn, curseur.lastrowid)
        if all(avant[c] == valeurs[c] for c in ("nom", "premier_jour", "dernier_jour")):
            return "inchangee", avant
        conn.execute(
            "UPDATE editions_evenement SET nom = ?, premier_jour = ?, dernier_jour = ? "
            "WHERE id = ?",
            (valeurs["nom"], valeurs["premier_jour"], valeurs["dernier_jour"], id_edition))
        return "modifiee", lire(conn, id_edition)


def supprimer(conn: sqlite3.Connection, id_edition: int) -> dict | None:
    """
    Retire une édition — et RIEN d'autre : aucun prêt n'y est rattaché.
    Renvoie l'édition retirée, ou None si elle n'existait déjà plus (double
    envoi, page restée ouverte).
    """
    with services.transaction(conn):
        edition = lire(conn, id_edition)
        if edition is not None:
            conn.execute("DELETE FROM editions_evenement WHERE id = ?", (id_edition,))
    return edition


# ---------------------------------------------------------------------------
# Période et libellés
# ---------------------------------------------------------------------------
def periode_locale(edition: dict) -> tuple[str, str]:
    """
    Les bornes de l'édition au format des champs `datetime-local` de /stats :
    le premier jour à 00:00, et le LENDEMAIN du dernier jour à 00:00 (fin
    exclusive). La conversion en UTC reste celle de toute saisie
    (`services.local_vers_utc_iso`) : l'heure d'été y est déjà traitée, et le
    dimanche du passage à l'heure d'hiver garde ses 25 heures.
    """
    lendemain = date.fromisoformat(edition["dernier_jour"]) + timedelta(days=1)
    return f"{edition['premier_jour']}T00:00", f"{lendemain.isoformat()}T00:00"


def _jour_fr(j: date, mois: bool = True, annee: bool = False) -> str:
    texte = "1er" if j.day == 1 else str(j.day)
    if mois:
        texte += f" {_MOIS_FR[j.month]}"
    if annee:
        texte += f" {j.year}"
    return texte


def dates_fr(edition: dict, annee: bool = False) -> str:
    """
    Les jours de l'édition, en français : « 14 novembre », « 14 et 15
    novembre », « du 14 au 16 novembre », « du 31 octobre au 2 novembre ».
    L'année est ajoutée sur demande, et toujours quand l'édition passe d'une
    année à l'autre.
    """
    debut = date.fromisoformat(edition["premier_jour"])
    fin = date.fromisoformat(edition["dernier_jour"])
    if debut == fin:
        return _jour_fr(debut, annee=annee)
    if debut.year != fin.year:
        return f"du {_jour_fr(debut, annee=True)} au {_jour_fr(fin, annee=True)}"
    if debut.month == fin.month:
        if fin - debut == timedelta(days=1):
            return f"{_jour_fr(debut, mois=False)} et {_jour_fr(fin, annee=annee)}"
        return f"du {_jour_fr(debut, mois=False)} au {_jour_fr(fin, annee=annee)}"
    return f"du {_jour_fr(debut)} au {_jour_fr(fin, annee=annee)}"


def libelle_menu(edition: dict) -> str:
    """
    L'option du menu « Période » : « Festival du jeu 2026 — 14 et 15
    novembre ». L'année ne s'ajoute aux dates que si le nom ne la porte pas
    déjà : sans elle, deux « Festival du jeu » se confondraient.
    """
    annee = edition["premier_jour"][:4] not in edition["nom"]
    return f"{edition['nom']} — {dates_fr(edition, annee=annee)}"


def libelle_periode(edition: dict) -> str:
    """
    Le libellé de la période sur la page et dans les deux exports : « Festival
    du jeu 2026, du 14 au 15 novembre 2026 ». Un export se relit des années
    plus tard : l'année y est toujours.
    """
    debut = date.fromisoformat(edition["premier_jour"])
    fin = date.fromisoformat(edition["dernier_jour"])
    if debut == fin:
        jours = f"le {_jour_fr(debut, annee=True)}"
    elif debut.year != fin.year:
        jours = dates_fr(edition)
    elif debut.month == fin.month:
        jours = f"du {_jour_fr(debut, mois=False)} au {_jour_fr(fin, annee=True)}"
    else:
        jours = f"du {_jour_fr(debut)} au {_jour_fr(fin, annee=True)}"
    return f"{edition['nom']}, {jours}"
