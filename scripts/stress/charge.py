"""
Générateur de charge — simule des bénévoles au stand, des visiteurs qui
consultent le catalogue et l'écran de salle qui interroge `/live/data`.

⚠️ ÉCRIT RÉELLEMENT EN BASE (prêts, retours, pochettes). À ne lancer que sur
l'INSTANCE DE FORMATION. Le script refuse de démarrer si la page d'accueil ne
porte pas le bandeau de formation, sauf `--je-sais-ce-que-je-fais`.

Usage :
    python -m scripts.stress.charge --url https://formation.example.fr \\
        --jeton "$JETON" --profil pointe --duree 300

Profils :
    nominal — le rythme d'une soirée ordinaire (8 bénévoles, une opération
              toutes les 20 à 40 s chacun) ;
    pointe  — le coup de feu de l'ouverture (8 bénévoles enchaînant toutes les
              2 à 5 s, catalogue très consulté) ;
    rupture — montée par paliers de bénévoles jusqu'à trouver le point où les
              temps de réponse décrochent ;
    soak    — rythme nominal, mais tenu longtemps (fuite mémoire, WAL).

Voir `docs/protocole-stress-test.md` pour l'interprétation des résultats.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time

import httpx

from scripts.stress.commun import (
    Mesures,
    client,
    decouvrir_exemplaires,
    lire_resultat,
)

# ---------------------------------------------------------------------------
# PROFILS — (bénévoles, pause min, pause max, visiteurs, exports par heure)
# ---------------------------------------------------------------------------
PROFILS = {
    "nominal": dict(benevoles=8, pause=(20.0, 40.0), visiteurs=15, exports_par_heure=2),
    "pointe": dict(benevoles=8, pause=(2.0, 5.0), visiteurs=30, exports_par_heure=6),
    "rupture": dict(benevoles=8, pause=(0.5, 1.5), visiteurs=40, exports_par_heure=0),
    "soak": dict(benevoles=6, pause=(25.0, 60.0), visiteurs=10, exports_par_heure=1),
}

# Recherches plausibles tapées par un visiteur dans le champ du catalogue.
RECHERCHES = ["catan", "loup", "a", "jeu", "carc", "dix", "roi", ""]


class Etat:
    """État partagé entre les tâches : pochettes vues, arrêt, mesures."""

    def __init__(self, mesures: Mesures) -> None:
        self.mesures = mesures
        self.arret = asyncio.Event()
        # numéro de pochette -> id_exemplaire qui le détient d'après le serveur.
        # Le simulateur est seul à écrire sur cette instance : deux boîtes
        # différentes portant le même numéro AU MÊME MOMENT est donc la
        # signature d'une course d'attribution (voir le protocole, § « le
        # risque n°1 »).
        self.pochettes: dict[int, str] = {}
        self.verrou = asyncio.Lock()

    @property
    def fin(self) -> bool:
        return self.arret.is_set()

    async def dormir(self, secondes: float) -> None:
        """
        Pause interruptible.

        Un simple `asyncio.sleep` obligerait à attendre la fin de la pause la
        plus longue (jusqu'à une minute en profil « soak ») avant de pouvoir
        arrêter la campagne. Ici, l'ordre d'arrêt réveille immédiatement toutes
        les tâches.
        """
        try:
            await asyncio.wait_for(self.arret.wait(), timeout=secondes)
        except asyncio.TimeoutError:
            pass

    async def prendre(self, numero: int | None, boite: str) -> None:
        if numero is None or numero == 0:  # 0 = sortie tournoi, pas de pochette
            return
        async with self.verrou:
            occupant = self.pochettes.get(numero)
            if occupant is not None and occupant != boite:
                self.mesures.incident(
                    f"POCHETTE PARTAGÉE — n°{numero} attribué à « {boite} » "
                    f"alors qu'il est déjà tenu par « {occupant} »"
                )
            self.pochettes[numero] = boite

    async def rendre(self, numero: int | None) -> None:
        if numero is None:
            return
        async with self.verrou:
            self.pochettes.pop(numero, None)


async def _appel(
    c: httpx.AsyncClient, etat: Etat, methode: str, chemin: str, operation: str
) -> httpx.Response | None:
    """Exécute une requête, chronomètre, enregistre code et incidents."""
    depart = time.monotonic()
    try:
        reponse = await c.request(methode, chemin)
    except Exception as erreur:  # coupure réseau, timeout, TLS…
        etat.mesures.relever(operation, time.monotonic() - depart, type(erreur).__name__)
        etat.mesures.incident(f"{operation} {chemin} → {type(erreur).__name__}: {erreur}")
        return None
    etat.mesures.relever(operation, time.monotonic() - depart, reponse.status_code)
    if reponse.status_code >= 500:
        etat.mesures.incident(f"{operation} {chemin} → HTTP {reponse.status_code}")
    return reponse


async def benevole(
    boites: list[str],
    base_url: str,
    jeton: str,
    etat: Etat,
    pause: tuple[float, float],
) -> None:
    """
    Un bénévole au stand : il scanne une boîte, la prête, puis (plus tard) la rend.

    Chaque bénévole simulé travaille sur son propre lot de boîtes : les
    « déjà sorti » observés viennent donc du serveur, jamais d'une collision
    entre deux tâches du script (les collisions volontaires, c'est
    `scripts/stress/course.py`).
    """
    async with client(base_url, jeton) as c:
        sorties: dict[str, int | None] = {}
        while not etat.fin:
            rendre = sorties and (len(sorties) >= 3 or random.random() < 0.45)
            if rendre:
                boite = random.choice(list(sorties))
                await _appel(c, etat, "GET", f"/pret/{boite}", "GET /pret/<id>")
                reponse = await _appel(
                    c, etat, "POST", f"/pret/{boite}/rendre", "POST rendre"
                )
                if reponse is not None and reponse.status_code == 200:
                    type_resultat, num = lire_resultat(reponse.text)
                    etat.mesures.resultats[f"rendre: {type_resultat}"] += 1
                    await etat.rendre(num if num is not None else sorties.get(boite))
                    if type_resultat not in ("rendu", "rendu_tournoi"):
                        etat.mesures.incident(
                            f"retour inattendu sur « {boite} » : {type_resultat}"
                        )
                sorties.pop(boite, None)
            else:
                boite = random.choice([b for b in boites if b not in sorties] or boites)
                await _appel(c, etat, "GET", f"/pret/{boite}", "GET /pret/<id>")
                reponse = await _appel(
                    c, etat, "POST", f"/pret/{boite}/preter", "POST preter"
                )
                if reponse is not None and reponse.status_code == 200:
                    type_resultat, num = lire_resultat(reponse.text)
                    etat.mesures.resultats[f"preter: {type_resultat}"] += 1
                    if type_resultat == "prete":
                        await etat.prendre(num, boite)
                        sorties[boite] = num
                    elif type_resultat != "deja_sorti":
                        etat.mesures.incident(
                            f"prêt inattendu sur « {boite} » : {type_resultat}"
                        )
            await etat.dormir(random.uniform(*pause))

        # Ménage : on rend ce qui est encore sorti, pour laisser la base propre.
        for boite in list(sorties):
            await _appel(c, etat, "POST", f"/pret/{boite}/rendre", "POST rendre")


async def visiteur(boites: list[str], base_url: str, jeton: str, etat: Etat) -> None:
    """Un visiteur : accueil, catalogue, recherche, fiche d'un jeu, stats."""
    async with client(base_url, "") as c:  # sans jeton : c'est du public
        while not etat.fin:
            await _appel(c, etat, "GET", "/", "GET /")
            await etat.dormir(random.uniform(1, 4))
            recherche = random.choice(RECHERCHES)
            await _appel(
                c, etat, "GET", f"/catalogue?q={recherche}", "GET /catalogue"
            )
            await etat.dormir(random.uniform(1, 5))
            await _appel(c, etat, "GET", f"/jeu/{random.choice(boites)}", "GET /jeu/<id>")
            await etat.dormir(random.uniform(2, 8))
            if random.random() < 0.3:
                await _appel(c, etat, "GET", "/stats", "GET /stats")
                await etat.dormir(random.uniform(2, 6))


async def ecran_salle(base_url: str, etat: Etat) -> None:
    """L'écran projeté en salle : un appel à `/live/data` toutes les 10 s."""
    async with client(base_url, "") as c:
        while not etat.fin:
            await _appel(c, etat, "GET", "/live/data", "GET /live/data")
            await etat.dormir(10)


async def exportateur(base_url: str, jeton: str, etat: Etat, par_heure: int) -> None:
    """
    Le bureau qui tire un export pendant l'événement.

    C'est la requête la PLUS lourde du site (reportlab / openpyxl, synchrone) :
    sur un seul worker uvicorn, elle occupe un fil du pool pendant toute sa
    durée. On veut savoir ce que les bénévoles ressentent à ce moment-là.
    """
    if par_heure <= 0:
        return
    intervalle = 3600 / par_heure
    async with client(base_url, jeton, timeout=120.0) as c:
        while not etat.fin:
            await etat.dormir(intervalle)
            if etat.fin:
                return
            await _appel(c, etat, "GET", "/stats/export.pdf", "GET export.pdf")


async def _verifier_cible(base_url: str, force: bool) -> None:
    """Refuse de tirer sur autre chose que le site de formation."""
    async with client(base_url, "") as c:
        reponse = await c.get("/")
        reponse.raise_for_status()
    formation = "SITE DE FORMATION" in reponse.text
    if formation:
        print("✓ Cible reconnue : site de FORMATION.")
        return
    if force:
        print(
            "⚠ Le bandeau de formation est ABSENT : la cible ressemble à de la\n"
            "  PRODUCTION. Poursuite forcée (--je-sais-ce-que-je-fais)."
        )
        return
    sys.exit(
        "ARRÊT : la page d'accueil ne porte pas le bandeau « SITE DE FORMATION ».\n"
        "Ce script crée de vrais prêts. Vise l'instance de formation, ou relance\n"
        "avec --je-sais-ce-que-je-fais si la cible est bien un bac à sable."
    )


async def executer(args: argparse.Namespace) -> int:
    profil = dict(PROFILS[args.profil])
    if args.benevoles:
        profil["benevoles"] = args.benevoles
    if args.visiteurs is not None:
        profil["visiteurs"] = args.visiteurs

    await _verifier_cible(args.url, args.je_sais_ce_que_je_fais)

    boites = await decouvrir_exemplaires(args.url, args.jeton)
    if len(boites) < 4:
        sys.exit(
            f"Seulement {len(boites)} exemplaire(s) trouvé(s) au catalogue. "
            "Peuple d'abord le site de formation (bouton « Réinitialiser les "
            "données de formation » au tableau de bord admin)."
        )
    print(f"✓ {len(boites)} boîtes disponibles pour le test.")

    etat = Etat(Mesures())
    # Chaque bénévole reçoit son lot : pas de collision entre tâches du script.
    lots = [boites[i :: profil["benevoles"]] for i in range(profil["benevoles"])]

    taches = [
        asyncio.create_task(benevole(lot, args.url, args.jeton, etat, profil["pause"]))
        for lot in lots
        if lot
    ]
    taches += [
        asyncio.create_task(visiteur(boites, args.url, args.jeton, etat))
        for _ in range(profil["visiteurs"])
    ]
    taches.append(asyncio.create_task(ecran_salle(args.url, etat)))
    taches.append(
        asyncio.create_task(
            exportateur(args.url, args.jeton, etat, profil["exports_par_heure"])
        )
    )

    print(
        f"▶ Profil « {args.profil} » : {len(lots)} bénévoles, "
        f"{profil['visiteurs']} visiteurs, 1 écran de salle — {args.duree} s.\n"
        "  (Ctrl+C pour arrêter plus tôt et afficher quand même le rapport.)"
    )
    try:
        await asyncio.sleep(args.duree)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        etat.arret.set()  # réveille toutes les tâches en pause
        print("\n… arrêt en cours, retours des boîtes encore sorties.")
        _, en_retard = await asyncio.wait(taches, timeout=30)
        for tache in en_retard:
            tache.cancel()
        if en_retard:
            await asyncio.gather(*en_retard, return_exceptions=True)

    print(etat.mesures.rapport())
    if etat.pochettes:
        print(
            f"⚠ {len(etat.pochettes)} pochette(s) encore marquée(s) occupée(s) "
            "côté simulateur — voir le vérificateur d'invariants."
        )
    return 1 if etat.mesures.incidents else 0


def principal() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analyseur.add_argument("--url", required=True, help="Base du site à tester (formation !)")
    analyseur.add_argument("--jeton", required=True, help="Jeton bénévole de l'instance visée")
    analyseur.add_argument("--profil", choices=sorted(PROFILS), default="nominal")
    analyseur.add_argument("--duree", type=int, default=300, help="Durée en secondes (défaut 300)")
    analyseur.add_argument("--benevoles", type=int, help="Force le nombre de bénévoles simulés")
    analyseur.add_argument("--visiteurs", type=int, help="Force le nombre de visiteurs simulés")
    analyseur.add_argument(
        "--je-sais-ce-que-je-fais",
        action="store_true",
        dest="je_sais_ce_que_je_fais",
        help="Autorise une cible sans bandeau de formation (DANGEREUX)",
    )
    args = analyseur.parse_args()
    try:
        return asyncio.run(executer(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(principal())
