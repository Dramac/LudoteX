"""
Routes de PRÊT / RETOUR — opérations d'ÉCRITURE, réservées aux bénévoles.

PRINCIPES (voir docs/specification.md §5.1, §6, §8)
---------------------------------------------------
- NE JAMAIS BLOQUER : toute incohérence (exemplaire déjà sorti, ou déjà
  disponible) produit un MESSAGE, jamais une erreur. Le résultat renvoyé au
  gabarit porte un `type` que pret.html sait afficher.
- Le serveur fait foi sur l'état : avant chaque action, on relit l'état réel
  pour gérer les conflits (deux bénévoles sur le même exemplaire) sans planter.
  Cette relecture et l'action qui suit se font DANS UNE MÊME TRANSACTION, côté
  services (`preter_si_disponible`, `sortir_tournoi_si_disponible`) : deux
  appuis simultanés passaient sinon le contrôle tous les deux et ouvraient deux
  prêts sur une même boîte (voir docs/protocole-stress-test.md § 2).
- L'écran est déclenché par un scan : le scanner (routes/scanner.py) redirige
  vers GET /pret/<id>.

SÉCURITÉ
--------
Toutes les routes dépendent de `exiger_jeton` (app/auth.py) : sans cookie
d'accès valide, FastAPI lève un 403 que main.py transforme en page
« accès réservé ».

DICTIONNAIRE `resultat` (passé au gabarit pret.html)
----------------------------------------------------
    {"type": "prete",            "numero": n}             prêt réussi
    {"type": "repret",           "nouveau": n, "ancien": a|None}  re-prêt
    {"type": "rendu",            "numero": n}             retour enregistré
    {"type": "deja_sorti",       "numero": n}             déjà sorti (no-op)
    {"type": "deja_disponible"}                           rien à rendre (no-op)
    {"type": "occupe"}                                    conflit d'accès simultané, rien d'enregistré
"""

import sqlite3

from fastapi import APIRouter, Depends, Request

from app import journal, services
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

# prefix="/pret" : toutes les routes ci-dessous commencent par /pret.
router = APIRouter(prefix="/pret", tags=["pret"])


_ECHECS = {"deja_sorti", "deja_disponible", "occupe"}


def _journaliser_pret(request: Request, action: str, info: dict, resultat: dict) -> None:
    """
    Une ligne de journal par action de prêt (docs/conception-journal.md §2.3),
    RÉUSSIE COMME ÉCHOUÉE — un `occupe` ou un `deja_sorti` est un bénévole qui
    a vu un message inattendu, c'est exactement ce qu'on cherche après coup.

    `objet` porte le NOM du jeu (ce que la table `prets` ne porte pas) ; `ref`
    le `reference_titre`. JAMAIS le numéro de pochette (§8, D5 vient de le
    purger à la clôture — un journal le ferait revivre indéfiniment).
    """
    type_ = resultat.get("type")
    echec = type_ in _ECHECS
    journal.journaliser(
        request, "pret", action,
        objet=info.get("nom"), ref=info.get("reference_titre"),
        ok=not echec, detail=type_ if echec else None,
    )


def _rendu(request: Request, id_exemplaire: str, resultat: dict | None = None,
           status: int = 200):
    """
    Rend l'écran prêt/retour avec l'état COURANT de l'exemplaire.

    Fonction interne (préfixe `_`) factorisant le rendu commun aux quatre routes.
    Elle relit toujours l'état frais en base, de sorte que la page reflète la
    réalité après l'action. Le `resultat` éventuel sert au bandeau de
    confirmation.

    Args:
        request: requête courante.
        id_exemplaire: identifiant concerné.
        resultat: dict décrivant l'issue d'une action (voir en-tête), ou None
            pour un simple affichage (GET).
        status: code HTTP si l'exemplaire existe (404 forcé sinon).

    Returns:
        La page pret.html.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        pret_actuel = services.pret_en_cours(conn, id_exemplaire) if info else None
        # « Où ranger le jeu » (docs/conception-rangement.md §6) : uniquement
        # calculé pour un retour (jamais bloquant si rien n'est renseigné —
        # emplacement_actuel renvoie alors None, le gabarit n'affiche rien).
        # Pas de lecture inutile sur les autres écrans (prêt, sortie tournoi...).
        emplacement_rangement = None
        if info and resultat and resultat.get("type") in ("rendu", "rendu_tournoi"):
            emplacement_rangement = services.emplacement_actuel(conn, id_exemplaire)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "pret.html",
        {"id_exemplaire": id_exemplaire, "info": info,
         "pret_actuel": pret_actuel, "resultat": resultat,
         "emplacement_rangement": emplacement_rangement},
        status_code=status if info else 404,
    )


def _sans_conflit(conn, id_exemplaire: str, ecrire) -> dict:
    """
    Exécute une écriture de prêt en traduisant un conflit d'accès simultané en
    MESSAGE, jamais en erreur brute (règle « ne jamais bloquer », spec §6).

    Deux échecs sont possibles depuis que les écritures s'ouvrent en
    `BEGIN IMMEDIATE` (voir services.transaction), et aucun ne doit donner un
    écran d'erreur à un bénévole en plein coup de feu :

    - `OperationalError: database is locked` — le verrou d'écriture n'a pas été
      obtenu dans le délai de `db.TIMEOUT_ECRITURE_S`. Les transactions durant
      une fraction de milliseconde, c'est en pratique inatteignable à huit
      bénévoles ; le message existe pour que le pire cas reste lisible. La
      transaction n'a rien écrit : on peut réappuyer sans risque de doublon.
    - `IntegrityError` — le filet de sécurité du schéma (index UNIQUE partiels,
      voir models.SCHEMA_INDEXES_UNIQUES) a refusé un doublon qui aurait dû
      être arrêté plus tôt par la transaction. Inatteignable également, mais si
      cela arrive, la boîte est de fait déjà sortie : on relit l'état et on
      affiche le message habituel plutôt qu'un vague « réessayez ».

    Toute autre `OperationalError` (base illisible, disque plein…) est laissée
    remonter : ce n'est pas un conflit, et la page 500 conviviale de main.py
    avec sa trace au journal est alors la bonne réponse.
    """
    try:
        return ecrire()
    except sqlite3.OperationalError as erreur:
        if "lock" not in str(erreur).lower() and "busy" not in str(erreur).lower():
            raise
        return {"type": "occupe"}
    except sqlite3.IntegrityError:
        conn.rollback()
        courant = services.pret_en_cours(conn, id_exemplaire)
        if courant is not None:
            return {"type": "deja_sorti", "numero": courant["numero_pochette"]}
        return {"type": "occupe"}


@router.get("/{id_exemplaire}")
def ecran(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Affiche l'écran prêt/retour (GET).

    Le gabarit pré-sélectionne l'action probable : « Prêter » si l'exemplaire est
    disponible, « Rendre » / « Le re-prêter » s'il est sorti.

    Le paramètre `_=Depends(exiger_jeton)` applique la protection par jeton ; sa
    valeur n'est pas utilisée (d'où le nom `_`).
    """
    return _rendu(request, id_exemplaire)


@router.post("/{id_exemplaire}/preter")
def action_preter(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Prête l'exemplaire (POST). Contrôle d'état : si déjà sorti, ne duplique pas
    le prêt et renvoie un message `deja_sorti` (jamais d'erreur).
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)  # exemplaire inconnu -> 404
        # Contrôle d'état ET prêt dans une SEULE transaction (voir
        # services.preter_si_disponible) : sans cela, deux appuis simultanés
        # ouvrent deux prêts sur la même boîte.
        def ecrire():
            res = services.preter_si_disponible(conn, id_exemplaire)
            if res.get("deja_sorti"):  # on ne ré-attribue pas de pochette
                return {"type": "deja_sorti", "numero": res["numero"]}
            return {"type": "prete", "numero": res["numero"]}

        resultat = _sans_conflit(conn, id_exemplaire, ecrire)
    finally:
        conn.close()
    # Le NOM du jeu (jamais le numéro de pochette, jamais ailleurs que dans
    # `pret.html` — §8) ; les échecs (`deja_sorti`, `occupe`) sont journalisés
    # au même titre que le succès : c'est précisément ce qu'on cherche après
    # coup (docs/conception-journal.md §2.3).
    _journaliser_pret(request, "pret", info, resultat)
    return _rendu(request, id_exemplaire, resultat)


@router.post("/{id_exemplaire}/rendre")
def action_rendre(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Enregistre le retour (POST). Si l'exemplaire était déjà disponible, renvoie
    `deja_disponible` sans rien modifier.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)
        def ecrire():
            res = services.rendre(conn, id_exemplaire)
            if res.get("deja_disponible"):
                return {"type": "deja_disponible"}
            if res.get("motif") == "tournoi":
                return {"type": "rendu_tournoi"}
            return {"type": "rendu", "numero": res["numero_libere"]}

        resultat = _sans_conflit(conn, id_exemplaire, ecrire)
    finally:
        conn.close()
    _journaliser_pret(request, "retour", info, resultat)
    return _rendu(request, id_exemplaire, resultat)


@router.post("/{id_exemplaire}/tournoi")
def action_tournoi(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Sort l'exemplaire pour un TOURNOI (POST) : pas de PI, pas d'emplacement, hors
    statistiques. Si déjà sorti, message `deja_sorti` (jamais d'erreur).
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)
        # Même contrôle atomique que pour le prêt (voir action_preter).
        def ecrire():
            res = services.sortir_tournoi_si_disponible(conn, id_exemplaire)
            if res.get("deja_sorti"):
                return {"type": "deja_sorti", "numero": res["numero"]}
            return {"type": "tournoi_sorti"}

        resultat = _sans_conflit(conn, id_exemplaire, ecrire)
    finally:
        conn.close()
    _journaliser_pret(request, "sortie_tournoi", info, resultat)
    return _rendu(request, id_exemplaire, resultat)


@router.post("/{id_exemplaire}/repreter")
def action_repreter(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Re-prête l'exemplaire (POST) : clôt l'ancien prêt puis en ouvre un nouveau
    (cas d'oubli de scan de retour). Voir services.repreter pour la logique.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)
        def ecrire():
            res = services.repreter(conn, id_exemplaire)
            # `ancien` peut être None si l'exemplaire était déjà disponible.
            return {"type": "repret", "nouveau": res["nouveau_numero"],
                    "ancien": res.get("ancien_numero")}

        resultat = _sans_conflit(conn, id_exemplaire, ecrire)
    finally:
        conn.close()
    _journaliser_pret(request, "re_pret", info, resultat)
    return _rendu(request, id_exemplaire, resultat)
