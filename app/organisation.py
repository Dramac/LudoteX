"""
Organisation (lot bilan-7a) : la dernière section de `/stats`.

À quoi ça sert : relire le travail au comptoir et l'état des boîtes après
l'événement — les erreurs de prêt, les retours non scannés, les prêts restés
ouverts, les boîtes signalées au carnet — pour préparer l'édition suivante.
Sous elle, repliés : la tombola, les tournois et le programme, le détail des
prêts.

Module à part, sur le patron d'`app/achats.py` : les chiffres et les phrases
vivent ici, le gabarit ne calcule rien. Il ne lit que la base de PRÊT et ne
journalise rien.

QUI VOIT CETTE SECTION : les seuls bénévoles (`auth.peut_ecrire`, un
administrateur connecté compris), même quand le bureau a rouvert `/stats` au
public (lot bilan-2). Elle parle du travail des bénévoles et des boîtes
abîmées, et le carnet de maintenance est `jamais_public`
(`app/modules.py`). La route ne calcule rien pour un visiteur
(`routes/stats.py::_organisation`). Les exports suivront la même règle au lot
bilan-7b.

CE QUE LA SECTION NE MONTRE JAMAIS :
- le **texte** d'un signalement (porte de donnée personnelle, `CLAUDE.md` :
  « jamais public, jamais journalisé ») : la requête ne lit pas la colonne ;
- le **code d'une boîte** signalée : des comptes par catégorie, rien d'autre ;
- un **pseudo**, un nom d'équipe ou un membre de tournoi : la base des
  tournois est lue par la route (`tournoi.services.bilan_periode`), qui
  COMPTE les inscriptions sans les lire.

LES DÉFINITIONS :
- **Erreurs de prêt** et **retours non scannés** : les chiffres de
  `services.stats_globales` (`erreurs`, `oublis`), inchangés ; leur infobulle
  d'avant ce lot devient une phrase visible (une infobulle ne se lit pas sur
  téléphone).
- **Prêts encore ouverts** : `stats_globales["en_cours"]`, inchangé — les
  prêts au public sortis pendant la période et pas encore rendus (les sorties
  tournoi n'y sont pas, comme avant). Une fois la période finie, un nombre
  non nul veut dire que la clôture de fin d'événement n'a pas été faite
  (constat sur la base réelle de 2026). Aucun bouton ici : la clôture reste
  un geste du tableau de bord de l'administration.
- **Signalements** : un signalement appartient à la période où il a été
  OUVERT (`cree_le`, en UTC, comme `date_sortie`). Il est « traité » s'il l'a
  été depuis, à n'importe quelle date, sinon « à traiter » — les mots du
  carnet (`carnet.LIBELLES_ETAT`). Sans catégorie, ou avec une catégorie
  disparue : « Non classé ». Les catégories archivées gardent leur nom.
- **Sorties tournoi** : les prêts `motif = 'tournoi'` de la période (date de
  sortie), déjà exclus de tous les autres chiffres.
"""

from __future__ import annotations

import sqlite3

from app.services import _filtre_periode, pluriel

TITRE_SECTION = "Organisation"
PHRASE_SENS = ("Le travail au comptoir et l'état des boîtes : ce qui a coincé "
               "et ce qui reste à faire, pour préparer la prochaine édition.")
NOTE_RESERVEE = ("Cette section n'est montrée qu'aux bénévoles, même quand la "
                 "page des statistiques est ouverte au public.")

# Les deux définitions, autrefois en infobulle sur la synthèse.
DEF_ERREURS = ("Erreur de prêt : jeu rendu moins d'une minute après sa sortie, "
               "parce que la mauvaise boîte a été scannée ou que le visiteur "
               "s'est ravisé. Les erreurs ne comptent dans aucun autre chiffre "
               "de la page.")
DEF_OUBLIS = ("Retour non scanné : prêt dont le retour n'a pas été scanné, "
              "clôturé plus tard par un re-prêt ou un transfert. Compté dans "
              "les prêts, mais pas dans la durée moyenne : sa durée est "
              "inconnue.")

# Prêts encore ouverts.
LIEN_CLOTURE = "Faire la clôture dans l'administration"
URL_CLOTURE = "/admin#fin-evenement"
PHRASE_DEMANDER_BUREAU = "Pour la faire, demandez au bureau."

# Signalements du carnet.
TITRE_SIGNALEMENTS = "Signalements du carnet"
ENTETES_SIGNALEMENTS = ("Catégorie", "Signalés", "Traités", "À traiter")
NON_CLASSE = "Non classé"
PHRASE_AUCUN_SIGNALEMENT = "Aucun signalement au carnet pendant la période."
NOTE_SIGNALEMENTS = ("Un signalement compte dans la période où il a été fait, "
                     "qu'il ait été traité depuis ou non. Le détail de chaque "
                     "signalement reste dans le carnet.")
LIEN_CARNET = "Ouvrir le carnet de maintenance"

# Volets repliés.
TITRE_TOMBOLA = "Tombola"
TITRE_TOURNOIS = "Tournois et programme"
TITRE_DETAIL = "Détail des prêts"
ENTETES_TOURNOIS = ("État", "Tournois")
LIBELLES_ETATS_TOURNOI = {
    "brouillon": "Brouillon", "inscriptions": "Inscriptions ouvertes",
    "lance": "En cours", "termine": "Terminé",
}
PHRASE_AUCUN_TOURNOI = "Aucun tournoi daté sur la période."
PHRASE_BASE_ILLISIBLE = ("Les tournois et le programme n'ont pas pu être "
                         "calculés : leur base est absente ou illisible. Les "
                         "autres chiffres de la page n'en dépendent pas.")
NOTE_SORTIES_TOURNOI = ("Boîtes sorties du prêt pour un tournoi, comptées à "
                        "part : elles n'entrent dans aucun autre chiffre de "
                        "la page.")


def phrase_ouverts(nb: int, periode_finie: bool) -> str:
    """
    Ce que veut dire le nombre de prêts encore ouverts. Une période finie
    (sa fin est passée) avec des prêts ouverts : la clôture n'a pas été
    faite. Une période en cours, ou sans fin (« Toute la période ») : on ne
    sait pas si l'événement est fini, la phrase donne les deux lectures.
    """
    if nb == 0:
        return "Aucun prêt de la période n'est encore ouvert."
    prets = f"{nb} {pluriel(nb, 'prêt', 'prêts')} de la période"
    verbe = pluriel(nb, "est encore ouvert", "sont encore ouverts")
    if periode_finie:
        return (f"La période est terminée, mais {nb} {pluriel(nb, 'prêt', 'prêts')} "
                f"{verbe} : la clôture de "
                "fin d'événement n'a pas été faite. Elle les ferme sans rien "
                "perdre de l'historique.")
    return (f"{prets} {verbe}. Pendant l'événement, ce sont les jeux dehors. "
            "Après l'événement, cela veut dire que la clôture de fin "
            "d'événement n'a pas été faite.")


def phrase_signalements(total: int, traites: int, a_traiter: int) -> str:
    """« 6 signalements pendant la période : 3 traités, 3 à traiter. »"""
    return (f"{total} {pluriel(total, 'signalement', 'signalements')} pendant "
            f"la période : {traites} {pluriel(traites, 'traité', 'traités')}, "
            f"{a_traiter} à traiter.")


def phrase_inscriptions(inscrits: int, equipes: int) -> str | None:
    """Les inscriptions aux tournois de la période ; None s'il n'y en a pas."""
    morceaux = []
    if inscrits:
        morceaux.append(f"{inscrits} {pluriel(inscrits, 'joueur inscrit', 'joueurs inscrits')}")
    if equipes:
        morceaux.append(f"{equipes} {pluriel(equipes, 'équipe inscrite', 'équipes inscrites')}")
    if not morceaux:
        return None
    return "Inscriptions : " + " et ".join(morceaux) + "."


def phrase_tournois_sans_date(nb: int) -> str:
    """« 2 tournois sans date ne sont pas comptés. »"""
    if nb == 1:
        return "1 tournoi sans date n'est pas compté."
    return f"{nb} tournois sans date ne sont pas comptés."


def phrase_programme(publies: int) -> str:
    """Le nombre d'animations publiées au programme sur la période."""
    if publies == 0:
        return "Aucune animation publiée au programme sur la période."
    return (f"{publies} {pluriel(publies, 'animation publiée', 'animations publiées')} "
            "au programme sur la période.")


def phrase_programme_sans_date(nb: int) -> str:
    """« 1 animation publiée sans date n'est pas comptée. »"""
    if nb == 1:
        return "1 animation publiée sans date n'est pas comptée."
    return f"{nb} animations publiées sans date ne sont pas comptées."


def phrase_sorties_tournoi(nb: int) -> str:
    """« Sorties tournoi du prêt : 10. »"""
    return f"Sorties « tournoi » du prêt : {nb}."


def signalements(conn: sqlite3.Connection, debut: str | None = None,
                 fin: str | None = None) -> dict:
    """
    Les signalements du carnet ouverts pendant la période, en comptes.

    La requête ne lit ni `texte` ni `id_exemplaire` : rien de ce qui en
    sortirait ne peut nommer une personne ni une boîte.

    Returns:
        {"total", "traites", "a_traiter",
         "lignes": [{"nom", "total", "traites", "a_traiter"}]} — les
        catégories de la plus signalée à la moins signalée (à égalité, par
        nom), « Non classé » en dernier.
    """
    f, params = _filtre_periode("s.cree_le", debut, fin)
    requete = (
        "SELECT c.nom AS nom, s.traite_le IS NOT NULL AS traite "
        "FROM signalements s "
        "LEFT JOIN categories_signalement c ON c.id_categorie = s.id_categorie "
        f"WHERE 1 = 1{f}"
    )
    comptes: dict[str | None, dict] = {}
    for l in conn.execute(requete, params):
        ligne = comptes.setdefault(l["nom"], {"total": 0, "traites": 0})
        ligne["total"] += 1
        ligne["traites"] += 1 if l["traite"] else 0

    def _ligne(nom, c):
        return {"nom": nom, "total": c["total"], "traites": c["traites"],
                "a_traiter": c["total"] - c["traites"]}

    lignes = sorted((_ligne(nom, c) for nom, c in comptes.items() if nom is not None),
                    key=lambda l: (-l["total"], l["nom"].casefold()))
    # « Non classé » en dernier, quel que soit son nombre.
    if None in comptes:
        lignes.append(_ligne(NON_CLASSE, comptes[None]))
    total = sum(l["total"] for l in lignes)
    traites = sum(l["traites"] for l in lignes)
    return {"total": total, "traites": traites, "a_traiter": total - traites,
            "lignes": lignes}


def sorties_tournoi(conn: sqlite3.Connection, debut: str | None = None,
                    fin: str | None = None) -> int:
    """Les sorties tournoi (prêts `motif = 'tournoi'`) sorties pendant la période."""
    f, params = _filtre_periode("date_sortie", debut, fin)
    return conn.execute(
        f"SELECT COUNT(*) FROM prets WHERE motif = 'tournoi'{f}", params
    ).fetchone()[0]
