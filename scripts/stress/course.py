"""
Test de COURSE sur l'attribution des numéros de pochette.

Ce que ce script cherche
------------------------
`services.preter()` fait, sur une connexion SQLite ouverte par la requête :

    1. SELECT MIN(numero_pochette) FROM pochettes WHERE occupe = 0   (lecture)
    2. UPDATE pochettes SET occupe = 1 WHERE numero_pochette = ?      (écriture)
    3. INSERT INTO prets (…, numero_pochette, …)
    4. COMMIT

Entre 1 et 2, rien ne réserve le numéro : la lecture n'ouvre pas de transaction
en écriture. Deux bénévoles qui appuient sur « Prêter » à quelques
millisecondes d'écart, sur DEUX BOÎTES DIFFÉRENTES, peuvent donc lire le même
« plus petit numéro libre » et repartir tous les deux avec, par exemple, la
pochette n°7 — sans la moindre erreur à l'écran. Conséquence physique : deux
pièces d'identité pour une seule pochette n°7.

Le même trou existe un cran plus haut, dans la route : `action_preter` vérifie
« cette boîte est-elle déjà sortie ? » PUIS appelle `preter()`. Deux appuis
simultanés sur la même boîte peuvent passer le contrôle tous les deux et ouvrir
deux prêts sur une seule boîte (scénario B).

Enfin, la branche « aucune pochette libre » (numéro = MAX + 1) est plus franche
encore : deux INSERT du même numéro violent la clé primaire → erreur 500. C'est
la situation de l'OUVERTURE de la soirée, quand la table des pochettes est vide
et que tout le monde prête en même temps.

Ce script provoque ces situations volontairement et dit si elles se produisent
RÉELLEMENT sur la machine visée. Ne rien trouver ne prouve pas l'absence du
défaut (la fenêtre est de l'ordre de la milliseconde, et la latence réseau
disperse les tirs) ; trouver quelque chose prouve sa présence.

⚠️ ÉCRIT EN BASE. Instance de FORMATION uniquement.

Usage :
    python -m scripts.stress.course --url https://formation.example.fr \\
        --jeton "$JETON" --tireurs 8 --manches 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from scripts.stress.commun import client, decouvrir_exemplaires, lire_resultat


# ---------------------------------------------------------------------------
# TIRS SIMULTANÉS
# ---------------------------------------------------------------------------
async def _tir(c, chemin: str, depart: asyncio.Event) -> tuple[str, int | None, int | str]:
    """Attend le top, puis envoie le POST. Retourne (type, numéro, code)."""
    await depart.wait()
    try:
        reponse = await c.post(chemin)
    except Exception as erreur:
        return f"ERREUR {type(erreur).__name__}", None, str(erreur)[:60]
    if reponse.status_code != 200:
        return "HTTP", None, reponse.status_code
    type_resultat, numero = lire_resultat(reponse.text)
    return type_resultat, numero, reponse.status_code


async def _salve(clients: list, chemins: list[str]) -> list[tuple]:
    """
    Lance une salve de POST aussi simultanée que possible.

    Chaque tireur a sa propre connexion, déjà ouverte et chauffée (TCP + TLS
    établis avant le top) : le seul délai restant est la latence réseau, pas la
    poignée de main.
    """
    depart = asyncio.Event()
    taches = [
        asyncio.create_task(_tir(c, chemin, depart))
        for c, chemin in zip(clients, chemins)
    ]
    await asyncio.sleep(0.05)  # laisse toutes les tâches atteindre le wait()
    depart.set()
    return await asyncio.gather(*taches)


async def _chauffer(clients: list, boites: list[str]) -> None:
    """Ouvre les connexions à l'avance (TCP + TLS) pour resserrer les salves."""
    await asyncio.gather(
        *(c.get(f"/pret/{boite}") for c, boite in zip(clients, boites)),
        return_exceptions=True,
    )


async def _liberer(clients: list, boites: list[str]) -> None:
    """Rend toutes les boîtes indiquées (remise à zéro entre deux manches)."""
    await asyncio.gather(
        *(c.post(f"/pret/{boite}/rendre") for c, boite in zip(clients, boites)),
        return_exceptions=True,
    )


async def _vider(c, boite: str, essais: int) -> None:
    """
    Rend une boîte jusqu'à ce qu'elle soit vraiment disponible.

    Nécessaire au scénario B : si la salve a ouvert plusieurs prêts sur la même
    boîte, un seul `rendre` n'en clôt qu'un — les manches suivantes verraient
    « déjà sorti » et ne testeraient plus rien.
    """
    for _ in range(essais):
        try:
            reponse = await c.post(f"/pret/{boite}/rendre")
        except Exception:
            return
        if reponse.status_code != 200:
            return
        if lire_resultat(reponse.text)[0] == "deja_disponible":
            return


# ---------------------------------------------------------------------------
# SCÉNARIOS
# ---------------------------------------------------------------------------
class Bilan:
    """Accumule ce qu'on a vu, pour le verdict final."""

    def __init__(self) -> None:
        self.comptes: Counter = Counter()
        self.anomalies: list[str] = []
        self.erreurs_500 = 0

    def noter(self, prefixe: str, resultats: list[tuple]) -> None:
        for type_resultat, _, code in resultats:
            self.comptes[f"{prefixe}{type_resultat}"] += 1
            if code == 500:
                self.erreurs_500 += 1


async def scenario_a(clients, lot, manches, bilan, garder_la_derniere) -> None:
    """Boîtes DIFFÉRENTES, prêts simultanés → deux fois le même numéro ?"""
    print(
        f"\n── Scénario A : {len(lot)} bénévoles prêtent {len(lot)} boîtes "
        f"DIFFÉRENTES à la même milliseconde, {manches} fois ──"
    )
    for manche in range(1, manches + 1):
        resultats = await _salve(clients, [f"/pret/{b}/preter" for b in lot])
        bilan.noter("A/", resultats)

        porteurs: dict[int, list[str]] = {}
        for (type_resultat, numero, _), boite in zip(resultats, lot):
            if type_resultat == "prete" and numero:
                porteurs.setdefault(numero, []).append(boite)
        partages = {n: b for n, b in porteurs.items() if len(b) > 1}
        for numero, boites_en_cause in partages.items():
            bilan.anomalies.append(
                f"manche A{manche} : pochette n°{numero} attribuée à "
                f"{len(boites_en_cause)} boîtes à la fois "
                f"({', '.join(boites_en_cause)})"
            )
        print(
            f"  {'✗' if partages else '·'} manche {manche:>2}/{manches} — "
            f"numéros attribués : "
            f"{sorted(n for n, b in porteurs.items() for _ in b)}"
        )
        if not (garder_la_derniere and manche == manches):
            await _liberer(clients, lot)


async def scenario_b(clients, cible, manches, bilan) -> None:
    """MÊME boîte, appuis simultanés → deux prêts ouverts sur une boîte ?"""
    print(
        f"\n── Scénario B : {len(clients)} appuis simultanés sur « Prêter » "
        f"pour LA MÊME boîte ({cible}), {manches} fois ──"
    )
    for manche in range(1, manches + 1):
        resultats = await _salve(clients, [f"/pret/{cible}/preter"] * len(clients))
        bilan.noter("B/", resultats)
        pretes = [r for r in resultats if r[0] == "prete"]
        if len(pretes) > 1:
            bilan.anomalies.append(
                f"manche B{manche} : {len(pretes)} prêts ouverts d'un coup sur "
                f"« {cible} » — pochettes {[p[1] for p in pretes]}"
            )
        print(
            f"  {'✗' if len(pretes) > 1 else '·'} manche {manche:>2}/{manches} — "
            f"{len(pretes)} prêt(s) ouvert(s), "
            f"{sum(1 for r in resultats if r[0] == 'deja_sorti')} « déjà sorti »"
        )
        # La salve a pu ouvrir plusieurs prêts : on les clôt TOUS avant la
        # manche suivante, sinon elle ne testerait plus rien.
        await _vider(clients[0], cible, len(clients) + 2)


# ---------------------------------------------------------------------------
def verdict(bilan: Bilan) -> int:
    print("\n" + "=" * 68)
    print("  VERDICT")
    print("=" * 68)
    for cle, nombre in sorted(bilan.comptes.items()):
        print(f"    {cle:<40}{nombre:>6}")
    print()
    if bilan.erreurs_500:
        print(f"  ✗ {bilan.erreurs_500} erreur(s) HTTP 500 pendant les salves.")
    if bilan.anomalies:
        print(f"  ✗ {len(bilan.anomalies)} anomalie(s) d'attribution :")
        for ligne in bilan.anomalies[:15]:
            print(f"      {ligne}")
        if len(bilan.anomalies) > 15:
            print(f"      … et {len(bilan.anomalies) - 15} autres")
        print(
            "\n  → La course est CONFIRMÉE sur cette machine. Pistes de correctif :\n"
            "    • ouvrir la transaction en écriture AVANT la lecture\n"
            "      (BEGIN IMMEDIATE) dans services.plus_petit_numero_libre, de\n"
            "      façon que la réservation du numéro et son écriture soient un\n"
            "      seul bloc indivisible ;\n"
            "    • index UNIQUE partiel sur les prêts ouverts (une pochette ne\n"
            "      peut être détenue que par un prêt à la fois) : le défaut\n"
            "      deviendrait une erreur visible plutôt qu'un silence ;\n"
            "    • même traitement pour le contrôle « déjà sorti » de la route.\n"
            "    Voir docs/protocole-stress-test.md § « Que faire des résultats »."
        )
        return 1
    print(
        "  ✓ Aucune anomalie observée sur cet échantillon.\n"
        "    Attention : la fenêtre de course est de l'ordre de la milliseconde.\n"
        "    Ne rien voir ne prouve pas qu'elle n'existe pas — relancer avec\n"
        "    --manches 100, et depuis une machine à faible latence du serveur\n"
        "    (tirs plus serrés = course plus probable)."
    )
    return 0


async def executer(args: argparse.Namespace) -> int:
    boites = await decouvrir_exemplaires(args.url, args.jeton)
    if len(boites) < args.tireurs:
        sys.exit(
            f"{len(boites)} boîtes au catalogue, il en faut au moins "
            f"{args.tireurs}. Réduis --tireurs ou peuple davantage le site de "
            "formation."
        )
    lot = boites[: args.tireurs]
    clients = [client(args.url, args.jeton) for _ in range(args.tireurs)]
    bilan = Bilan()

    try:
        await _chauffer(clients, lot)
        await _liberer(clients, lot)  # on part de boîtes sûrement disponibles

        await scenario_a(clients, lot, args.manches, bilan, args.laisser_en_place)

        if args.laisser_en_place:
            print(
                "\n(--laisser-en-place : la dernière salve du scénario A reste\n"
                " ouverte et le scénario B est sauté, pour ne pas la perturber.\n"
                " Enchaîne sur scripts/stress/coherence.py : c'est lui qui dira\n"
                " ce que la BASE contient réellement.)"
            )
        else:
            await scenario_b(clients, lot[0], args.manches, bilan)
    finally:
        if not args.laisser_en_place:
            await _liberer(clients, lot)
        await asyncio.gather(*(c.aclose() for c in clients), return_exceptions=True)

    return verdict(bilan)


def principal() -> int:
    analyseur = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    analyseur.add_argument("--url", required=True, help="Base du site (formation !)")
    analyseur.add_argument("--jeton", required=True)
    analyseur.add_argument(
        "--tireurs", type=int, default=8, help="Bénévoles simultanés (défaut 8)"
    )
    analyseur.add_argument(
        "--manches", type=int, default=20, help="Salves par scénario (défaut 20)"
    )
    analyseur.add_argument(
        "--laisser-en-place",
        action="store_true",
        dest="laisser_en_place",
        help="Ne pas clore la dernière salve, pour inspecter la base avec "
        "scripts/stress/coherence.py (sinon le ménage masque l'anomalie)",
    )
    args = analyseur.parse_args()
    try:
        return asyncio.run(executer(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(principal())
