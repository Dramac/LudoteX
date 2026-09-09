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
    {"type": "repret",           "nouveau": n, "ancien": a|None,
                                 "meme_numero": bool}     re-prêt
        `meme_numero` dit que le numéro réattribué est celui qu'on vient de
        libérer (cas fréquent : `plus_petit_numero_libre` reprend très souvent
        le numéro rendu au pot une ligne plus tôt). Calculé ICI et pas dans le
        gabarit, qui n'a pas à comparer des valeurs métier.
    {"type": "rendu",            "numero": n, "erreur": bool}
        retour enregistré ; `erreur` est vrai quand le prêt a duré moins d'une
        minute et a donc été requalifié en erreur de prêt (hors statistiques,
        voir services._marquer_erreur_si_immediat). Le geste du bénévole est
        le même dans les deux cas.
    {"type": "deja_sorti",       "numero": n}             déjà sorti (no-op)
    {"type": "deja_disponible"}                           rien à rendre (no-op)
    {"type": "occupe"}                                    conflit d'accès simultané, rien d'enregistré
    {"type": "transfert",            "numero": n, "rendu_nom": …, "meme_boite": bool,
                                     "oubli_clos": bool, "numero_libere": n|None}
        transfert réussi (docs/conception-transfert-pochette.md) : la pochette
        n'a pas bougé, seul le jeu associé change. `oubli_clos` signale que
        l'escalade a d'abord clôturé un prêt jamais scanné en retour sur la
        boîte emportée, `numero_libere` le numéro alors rendu au pot (None pour
        une sortie tournoi, qui n'en avait pas).
    {"type": "transfert_impossible", "raison": "rien_a_rendre"|"sans_pochette"}
        rien à transférer (boîte déjà rendue entre-temps, ou sortie tournoi)
    {"type": "signale"}                                    signalement envoyé
    {"type": "signalement_traite",   "categorie": nom|None}  signalement refermé
                                                             depuis la fiche
    {"type": "signalement_traite_echec"}                    rien à refermer
                                                             (inconnu ou autre boîte)

TRANSFERT DE POCHETTE (docs/conception-transfert-pochette.md)
---------------------------------------------------------------
Quatre routes dédiées, à la suite de celles-ci (« Rendre » puis « Prêter »
sans jamais faire ressortir la pièce d'identité de son casier). Voir leurs
docstrings pour le détail de chaque écran.

CARNET DE MAINTENANCE (docs/conception-signalements.md)
---------------------------------------------------------
Trois routes dédiées, à la suite du transfert : `GET .../signaler` ouvre un
écran (rien n'est encore écrit), `POST .../signaler` enregistre. Le lien est
PERMANENT en pied de carte (les trois états de la boîte), et le bandeau
d'alerte listant les signalements ouverts se voit AVANT toute action — voir
`_rendu()`, qui le calcule sans condition sur `resultat`.

Depuis le lot agora-4, un bouton par ligne de ce bandeau referme le
signalement (`POST .../signalements/<id_signalement>/traiter`) : le §2 de la
note de conception, qui réservait ce geste à l'administrateur, est devenu
faux et corrigé en conséquence. La lecture/écriture/journalisation est
factorisée dans `_signalement_a_fermer` / `_journaliser_signalement_traite`
ci-dessous, PARTAGÉES avec `routes/admin.py::signalement_traiter` (import
différé depuis ce module, patron déjà en usage pour `catalogue._entier_ou_none`).
"""

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import journal, services
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

# prefix="/pret" : toutes les routes ci-dessous commencent par /pret.
router = APIRouter(prefix="/pret", tags=["pret"])


# "transfert_impossible" et "nouveau_sorti" sont les refus PROPRES au
# transfert (voir _journaliser_transfert) : réunis ici avec les échecs
# historiques pour ne garder qu'UN SEUL endroit qui décide de ce qui compte
# comme un échec dans le journal (docs/conception-journal.md §2.3).
_ECHECS = {"deja_sorti", "deja_disponible", "occupe",
           "transfert_impossible", "nouveau_sorti"}


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


def _journaliser_transfert(request: Request, info_rendu: dict, info_nouveau: dict,
                            resultat: dict) -> None:
    """
    Une ligne pour le transfert de pochette (docs/conception-transfert-pochette.md
    §9). `_journaliser_pret` ci-dessus travaille sur UN SEUL exemplaire ; le
    transfert en manipule deux, d'où cet appel dédié plutôt que de tordre le
    helper existant pour lui faire porter une paire.

    `objet` porte les noms des DEUX jeux (« Catan → Dixit »), jamais le numéro
    de pochette (§8, D5 vient de le purger à la clôture). Les refus
    (`transfert_impossible`, `nouveau_sorti`, `occupe`…) sont journalisés au
    même titre que le succès, `detail` portant la raison précise quand elle
    existe (`resultat["raison"]`), sinon le type de refus lui-même.

    `ref` porte le titre de la boîte NOUVELLEMENT PRÊTÉE, comme l'action
    `pret` : un transfert est, du point de vue de ce qui sort, un prêt de
    plus. Filtrer le journal sur une référence donne ainsi la même chose quel
    que soit le chemin emprunté pour prêter le jeu — le titre rendu, lui,
    reste lisible dans `objet`.

    DEUX ACTIONS, choisies sur ce qui a RÉELLEMENT été écrit : la ligne porte
    `transfert_avec_cloture` quand un prêt oublié a été clos au passage (la
    seule écriture de l'application qui ferme deux prêts d'un coup), et
    `transfert` partout ailleurs — y compris quand l'escalade a été demandée
    mais que la boîte était revenue entre-temps, auquel cas rien de plus qu'un
    transfert n'a eu lieu. Un refus reste journalisé sous `transfert`, son
    motif dans `detail`.
    """
    type_ = resultat.get("type")
    echec = type_ in _ECHECS
    action = "transfert_avec_cloture" if resultat.get("oubli_clos") else "transfert"
    journal.journaliser(
        request, "pret", action,
        objet=f"{info_rendu.get('nom')} → {info_nouveau.get('nom')}",
        ref=info_nouveau.get("reference_titre"),
        ok=not echec,
        detail=(resultat.get("raison") or type_) if echec else None,
    )


def _journaliser_signalement(request: Request, info: dict, categorie_nom: str | None,
                             *, ok: bool = True, detail: str | None = None) -> None:
    """
    Une ligne pour un signalement du carnet de maintenance
    (docs/conception-signalements.md §10).

    ⚠️ JAMAIS LE TEXTE LIBRE. C'est le seul endroit de l'application de prêt
    par lequel une donnée personnelle peut entrer (§3 de la note) : la ligne
    porte le nom du jeu et le libellé de la catégorie, rien d'autre. Le
    garde-fou `tests/test_journal_interdits.py` le vérifie sur un texte
    volontairement distinctif.

    LA CATÉGORIE VA DANS `objet`, PAS DANS `detail`, contrairement à la
    lettre du §10 de la note : `journaliser()` ne conserve `detail` que
    lorsque `ok` est faux (format arrêté au §3 de
    docs/conception-journal.md), la catégorie disparaîtrait donc de toutes
    les lignes réussies — précisément celles qu'on relit. Même arbitrage que
    pour `planning_genere`, dont le bilan chiffré a rejoint `objet` pour
    cette raison. `detail` reste ce à quoi il sert : le motif d'un refus.
    """
    objet = info.get("nom")
    if categorie_nom:
        objet = f"{objet} — {categorie_nom}"
    journal.journaliser(
        request, "pret", "signalement_cree",
        objet=objet, ref=info.get("reference_titre"), ok=ok, detail=detail,
    )


# Sentinelle distinguant « calculer l'emplacement automatiquement » (défaut,
# tous les appelants historiques) de « ne rien calculer, la valeur est déjà
# connue » — cas du transfert, qui doit afficher l'emplacement de LA BOÎTE
# RENDUE alors que `_rendu` est appelée sur LA NOUVELLE (voir son 3ᵉ point).
_AUTO = object()


def _rendu(request: Request, id_exemplaire: str, resultat: dict | None = None,
           status: int = 200, emplacement_rangement=_AUTO):
    """
    Rend l'écran prêt/retour avec l'état COURANT de l'exemplaire.

    Fonction interne (préfixe `_`) factorisant le rendu commun aux routes.
    Elle relit toujours l'état frais en base, de sorte que la page reflète la
    réalité après l'action. Le `resultat` éventuel sert au bandeau de
    confirmation.

    Args:
        request: requête courante.
        id_exemplaire: identifiant concerné (c'est SA fiche qui est rendue).
        resultat: dict décrivant l'issue d'une action (voir en-tête), ou None
            pour un simple affichage (GET).
        status: code HTTP si l'exemplaire existe (404 forcé sinon).
        emplacement_rangement: par défaut, calculé automatiquement pour un
            retour (voir plus bas). Le TRANSFERT est le seul appelant qui le
            fixe explicitement : l'écran est rendu sur la boîte NOUVELLEMENT
            sortie, mais c'est la boîte RENDUE qu'il faut ranger — deviner à
            partir de `id_exemplaire` donnerait l'emplacement de la mauvaise
            boîte.

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
        if emplacement_rangement is _AUTO:
            emplacement_rangement = None
            if info and resultat and resultat.get("type") in ("rendu", "rendu_tournoi"):
                emplacement_rangement = services.emplacement_actuel(conn, id_exemplaire)
        # Signalements ouverts (docs/conception-signalements.md §6) : calculés
        # SANS CONDITION sur `resultat`, contrairement à l'emplacement de
        # rangement ci-dessus — le bandeau doit se voir dès l'ouverture de
        # l'écran (GET), AVANT toute action, pas seulement dans la confirmation
        # qui suit un retour (piège 1 du lot 2 de l'implémentation).
        signalements_ouverts = services.signalements_ouverts(conn, id_exemplaire) if info else []
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "pret.html",
        {"id_exemplaire": id_exemplaire, "info": info,
         "pret_actuel": pret_actuel, "resultat": resultat,
         "emplacement_rangement": emplacement_rangement,
         "signalements_ouverts": signalements_ouverts},
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
            return {"type": "rendu", "numero": res["numero_libere"],
                    "erreur": bool(res.get("erreur"))}

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
            ancien = res.get("ancien_numero")
            nouveau = res["nouveau_numero"]
            # Le numéro libéré par la clôture est aussitôt le plus petit
            # libre : `preter()` le reprend très souvent. Sans ce drapeau, le
            # gabarit annonçait « ancien prêt (pochette n°5) clôturé, glissez
            # la pièce d'identité dans la pochette n°5 » — deux phrases qui se
            # contredisent (série agora, lot 1, point 2).
            return {"type": "repret", "nouveau": nouveau, "ancien": ancien,
                    "meme_numero": ancien == nouveau}

        resultat = _sans_conflit(conn, id_exemplaire, ecrire)
    finally:
        conn.close()
    _journaliser_pret(request, "re_pret", info, resultat)
    return _rendu(request, id_exemplaire, resultat)


# ===========================================================================
# TRANSFERT DE POCHETTE (docs/conception-transfert-pochette.md)
# ===========================================================================
def _transfert_ou_refus(conn, id_rendu: str):
    """
    Vérifie, EN LECTURE SEULE, que `id_rendu` a une pochette à transférer —
    pour l'affichage des trois écrans GET ci-dessous. Le service, lui, refait
    ce contrôle SOUS LE VERROU d'écriture au moment d'agir (voir
    `services.transferer_pochette`) : cette fonction ne fait donc courir
    aucun risque de contrôler puis d'agir sur un état périmé, elle ne fait
    qu'éviter d'ouvrir un écran de scan pour rien.

    Returns:
        (numero, None) si transférable, ou (None, resultat_refus) sinon, où
        `resultat_refus` est prêt à passer à `_rendu` sur /pret/{id_rendu}
        (patron des trois refus du service — voir le tableau de la note de
        conception §6/l'en-tête de ce fichier).
    """
    courant = services.pret_en_cours(conn, id_rendu)
    if courant is None:
        return None, {"type": "transfert_impossible", "raison": "rien_a_rendre"}
    if courant["motif"] != "pret" or not courant["numero_pochette"]:
        return None, {"type": "transfert_impossible", "raison": "sans_pochette"}
    return courant["numero_pochette"], None


def _escalade(id_nouveau: str, nouvelle_info: dict, numero_oubli) -> dict:
    """
    Décrit, pour le gabarit, le prêt oublié qui tient la boîte que le visiteur
    veut emporter — et que le bouton d'escalade clôturera
    (docs/conception-transfert-pochette.md §6).

    UN SEUL DOMICILE pour cette forme : les deux écrans qui affichent le bloc
    (confirmation avant le refus, scan après) la construisent ici, et le
    fragment `_pochette_oubliee.html` la lit telle quelle.

    `numero` peut valoir 0 (sortie tournoi) : le fragment n'annonce alors
    aucun casier à vérifier, il n'y en a pas.
    """
    return {"id_nouveau": id_nouveau, "nom": nouvelle_info["nom"],
            "numero": numero_oubli}


@router.get("/{id_rendu}/transfert")
def transfert_ecran(request: Request, id_rendu: str, _=Depends(exiger_jeton)):
    """
    Écran de transfert (§5 de la note) : caméra embarquée pour scanner le
    NOUVEAU jeu. RIEN n'est encore écrit — c'est le scan de la seconde boîte
    (ou sa saisie manuelle) qui déclenchera l'écran de confirmation, jamais
    directement une écriture (règle générale du scan, voir §2 des trois
    pièges en tête de la note d'implémentation).
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_rendu)
        if info is None:
            return _rendu(request, id_rendu)  # 404, patron existant
        numero, refus = _transfert_ou_refus(conn, id_rendu)
        if refus:
            return _rendu(request, id_rendu, refus)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "transfert_scan.html",
        {"id_rendu": id_rendu, "info": info, "numero": numero},
    )


@router.get("/{id_rendu}/transfert/saisie")
def transfert_saisie(request: Request, id_rendu: str, code: str = "",
                      _=Depends(exiger_jeton)):
    """
    Secours clavier de l'écran de transfert (patron exact de
    `/scanner/saisie`). N'ÉCRIT RIEN : redirige (303) vers l'écran de
    confirmation si le code correspond à une boîte connue, sinon réaffiche
    l'écran de transfert avec un message et le champ prérempli.

    ⚠️ Cette route DOIT être déclarée avant `/transfert/{id_nouveau}` : sinon
    FastAPI capture « saisie » comme un `id_nouveau` (un test le verrouille).
    """
    id_nouveau = (code or "").strip()
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_rendu)
        if info is None:
            return _rendu(request, id_rendu)
        numero, refus = _transfert_ou_refus(conn, id_rendu)
        if refus:
            return _rendu(request, id_rendu, refus)

        if not id_nouveau:
            return templates.TemplateResponse(
                request, "transfert_scan.html",
                {"id_rendu": id_rendu, "info": info, "numero": numero,
                 "erreur": "Veuillez saisir un code.", "code_saisi": ""},
            )
        if services.info_exemplaire(conn, id_nouveau) is None:
            return templates.TemplateResponse(
                request, "transfert_scan.html",
                {"id_rendu": id_rendu, "info": info, "numero": numero,
                 "erreur": f"Aucune boîte ne porte le code « {id_nouveau} ». "
                           "Vérifiez et réessayez.",
                 "code_saisi": id_nouveau},
            )
    finally:
        conn.close()
    return RedirectResponse(f"/pret/{id_rendu}/transfert/{id_nouveau}", status_code=303)


@router.get("/{id_rendu}/transfert/{id_nouveau}")
def transfert_confirmation(request: Request, id_rendu: str, id_nouveau: str,
                            _=Depends(exiger_jeton)):
    """
    Écran de confirmation (§5 de la note) : les DEUX noms de jeux et le
    numéro conservé, avant d'écrire quoi que ce soit — c'est ici que le
    bénévole rattrape un scan de la mauvaise boîte.

    `id_nouveau` inconnu (URL forgée, ou navigation directe du scan caméra
    sur un QR qui ne correspond à rien) : même patron que le scan normal vers
    /pret/<id> — 404 via `_rendu`, pas un message de rattrapage (celui-ci est
    réservé à la saisie manuelle, voir `transfert_saisie`).

    AVERTISSEMENT « déjà sortie » : si la nouvelle boîte est actuellement
    prêtée, le POST la refusera (`nouveau_sorti`). Le dire ICI évite un tap
    perdu dans un geste conçu pour en économiser. Le bouton reste néanmoins en
    place, et c'est délibéré : cet écran n'est qu'un INSTANTANÉ, la boîte peut
    très bien revenir entre-temps — seul le POST fait autorité, sous le verrou
    d'écriture. Sans objet quand la nouvelle boîte est la boîte rendue
    elle-même : elle est sortie, c'est justement la situation nominale.

    L'avertissement porte, depuis la série agora, le bouton d'ESCALADE
    (« clôturer le prêt oublié ») À CÔTÉ du bouton de transfert normal, jamais
    à sa place : les deux gestes restent possibles, pour la même raison
    d'instantané. Le bloc est un fragment partagé avec l'écran de scan, où le
    refus du POST le fait réapparaître — une seule formulation, un seul
    domicile (`_pochette_oubliee.html`).
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_rendu)
        if info is None:
            return _rendu(request, id_rendu)
        numero, refus = _transfert_ou_refus(conn, id_rendu)
        if refus:
            return _rendu(request, id_rendu, refus)
        nouvelle_info = services.info_exemplaire(conn, id_nouveau)
        if nouvelle_info is None:
            return _rendu(request, id_nouveau)
        meme_boite = id_nouveau == id_rendu
        escalade = None
        if not meme_boite:
            autre = services.pret_en_cours(conn, id_nouveau)
            if autre is not None:
                escalade = _escalade(id_nouveau, nouvelle_info,
                                     autre["numero_pochette"])
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "transfert_confirmation.html",
        {"id_rendu": id_rendu, "id_nouveau": id_nouveau, "info": info,
         "nouvelle_info": nouvelle_info, "numero": numero,
         "meme_boite": meme_boite, "escalade": escalade},
    )


@router.post("/{id_rendu}/transfert/{id_nouveau}")
def transfert_confirmer(request: Request, id_rendu: str, id_nouveau: str,
                         clore_oubli: str = Form(""), _=Depends(exiger_jeton)):
    """
    L'opération de transfert (POST) : clôture du prêt en cours d'`id_rendu`
    et ouverture d'un nouveau prêt sur `id_nouveau`, SUR LE MÊME NUMÉRO de
    pochette (voir `services.transferer_pochette`, qui fait tout tenir dans
    une seule transaction).

    `clore_oubli` est le champ caché du bouton d'ESCALADE : il autorise le
    service à clôturer d'abord un prêt jamais scanné en retour sur
    `id_nouveau`. Il ne force rien — si la boîte est revenue entre l'écran et
    l'appui, le transfert est ordinaire. Un champ de formulaire plutôt qu'une
    seconde route : c'est le MÊME geste, avec une autorisation en plus, et les
    deux boutons visent donc la même URL.

    Refus possibles, aucun n'écrit rien :
    - `nouveau_sorti` : la boîte scannée est déjà sortie et l'escalade n'a pas
      été demandée -> le bénévole est au milieu de son geste, on reste sur
      l'écran de transfert, qui porte désormais le bouton d'escalade au-dessus
      de la caméra — toujours active, « scanner une autre boîte » n'a donc
      besoin d'aucun bouton (§6 de la note) ;
    - `rien_a_rendre` / `sans_pochette` : `id_rendu` n'a plus de pochette à
      transférer -> écran /pret/{id_rendu} habituel, le bouton Rendre juste
      en dessous ;
    - `occupe` (conflit d'accès simultané, voir `_sans_conflit`) : message
      existant, inchangé.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_rendu)
        if info is None:
            return _rendu(request, id_rendu)
        nouvelle_info = services.info_exemplaire(conn, id_nouveau)
        if nouvelle_info is None:
            return _rendu(request, id_nouveau)

        def ecrire():
            res = services.transferer_pochette(conn, id_rendu, id_nouveau,
                                               clore_oubli=bool(clore_oubli))
            if res.get("rien_a_rendre"):
                return {"type": "transfert_impossible", "raison": "rien_a_rendre"}
            if res.get("sans_pochette"):
                return {"type": "transfert_impossible", "raison": "sans_pochette"}
            if res.get("nouveau_sorti"):
                return {"type": "nouveau_sorti", "numero": res["numero"]}
            return {"type": "transfert", "numero": res["numero"],
                    "meme_boite": res["meme_boite"],
                    "oubli_clos": res["oubli_clos"],
                    "numero_libere": res["numero_libere"]}

        resultat = _sans_conflit(conn, id_rendu, ecrire)
        _journaliser_transfert(request, info, nouvelle_info, resultat)

        if resultat["type"] == "nouveau_sorti":
            # Rien n'a été écrit : la pochette d'id_rendu n'a pas bougé, on
            # reste sur l'écran de transfert. Le message n'est plus une fin de
            # course : le bloc d'escalade porte le bouton qui clôt le prêt
            # oublié, et la caméra reste dessous pour viser une autre boîte.
            courant = services.pret_en_cours(conn, id_rendu)
            return templates.TemplateResponse(
                request, "transfert_scan.html",
                {"id_rendu": id_rendu, "info": info,
                 "numero": courant["numero_pochette"] if courant else None,
                 "escalade": _escalade(id_nouveau, nouvelle_info,
                                       resultat["numero"])},
            )
        if resultat["type"] != "transfert":
            # transfert_impossible / occupe / deja_sorti (conflit rare, voir
            # _sans_conflit) : même écran, même patron que les autres actions.
            return _rendu(request, id_rendu, resultat)

        # Succès : « où ranger le jeu » porte sur la boîte RENDUE, lue AVANT
        # de fermer la connexion (voir le 3ᵉ point de la note d'implémentation
        # et la docstring de `_rendu`).
        emplacement_rendu = services.emplacement_actuel(conn, id_rendu)
    finally:
        conn.close()

    # L'écran de résultat est rendu sur LA NOUVELLE boîte (c'est elle qui est
    # désormais sortie), avec l'emplacement de LA BOÎTE RENDUE.
    return _rendu(
        request, id_nouveau,
        {"type": "transfert", "numero": resultat["numero"],
         "rendu_nom": info["nom"], "meme_boite": resultat["meme_boite"],
         "oubli_clos": resultat["oubli_clos"],
         "numero_libere": resultat["numero_libere"]},
        emplacement_rangement=emplacement_rendu,
    )


# ===========================================================================
# CARNET DE MAINTENANCE (docs/conception-signalements.md)
# ===========================================================================
@router.get("/{id_exemplaire}/signaler")
def signaler_ecran(request: Request, id_exemplaire: str, _=Depends(exiger_jeton)):
    """
    Écran de signalement (§5 de la note) : catégories ACTIVES uniquement, en
    boutons radio empilés. RIEN n'est encore écrit — c'est le bouton « Envoyer
    le signalement » ci-dessous qui déclenche le POST.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)  # 404, patron existant
        categories = services.categories_signalement_actives(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "pret_signaler.html",
        {"id_exemplaire": id_exemplaire, "info": info, "categories": categories,
         "longueur_max_texte": services.LONGUEUR_MAX_TEXTE_SIGNALEMENT,
         "erreur": None, "id_categorie_saisi": None, "texte_saisi": ""},
    )


@router.post("/{id_exemplaire}/signaler")
async def signaler_confirmer(request: Request, id_exemplaire: str,
                              _=Depends(exiger_jeton)):
    """
    Enregistre le signalement (POST). Ne bloque JAMAIS le prêt (§6 de la
    note) : cette route n'a aucun effet sur les autres actions de l'écran,
    elle ne fait qu'ajouter une ligne au carnet de maintenance.

    Catégorie absente, inconnue ou archivée (URL forgée, ou catégorie
    archivée par le bureau entre l'affichage et l'envoi) : le formulaire est
    RÉAFFICHÉ avec un message et la saisie CONSERVÉE — jamais un formulaire
    vierge (patron du correctif de la collecte planning, lot B de sécurité,
    voir `planning/routes.py::collecte_post`). La validation relit la liste
    des catégories actives, elle ne fait jamais confiance à ce que le
    formulaire a envoyé (§8 de la note).
    """
    form = await request.form()
    texte = form.get("texte", "")
    id_categorie_brut = (form.get("id_categorie") or "").strip()

    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)  # 404, patron existant
        categories = services.categories_signalement_actives(conn)
        actives = {c["id_categorie"] for c in categories}

        id_categorie = int(id_categorie_brut) if id_categorie_brut.isdigit() else None
        erreur = None
        motif = None
        if id_categorie is None:
            erreur = "Choisissez une catégorie avant d'envoyer."
            motif = "categorie_manquante"
        elif id_categorie not in actives:
            erreur = "Cette catégorie n'est plus disponible. Choisissez-en une autre."
            motif = "categorie_indisponible"

        refus = None
        if erreur:
            refus = templates.TemplateResponse(
                request, "pret_signaler.html",
                {"id_exemplaire": id_exemplaire, "info": info, "categories": categories,
                 "longueur_max_texte": services.LONGUEUR_MAX_TEXTE_SIGNALEMENT,
                 "erreur": erreur, "id_categorie_saisi": id_categorie,
                 "texte_saisi": texte},
                status_code=400,
            )
            categorie_nom = None
        else:
            # `creer_signalement` borne le texte à l'enregistrement, sans jamais
            # refuser (§4/§8 de la note) : rien de plus à valider ici.
            services.creer_signalement(conn, id_exemplaire, id_categorie, texte)
            categorie_nom = next(
                (c["nom"] for c in categories if c["id_categorie"] == id_categorie), None
            )
    finally:
        conn.close()
    # Journalisé APRÈS la fermeture de la connexion, comme les quatre actions
    # de prêt ci-dessus (`journaliser` ouvre la sienne pour déterminer `qui`).
    # Le refus est journalisé au même titre que le succès : une catégorie
    # archivée entre l'affichage et l'envoi est exactement le genre de surprise
    # qu'on cherche après coup (docs/conception-journal.md §2.3).
    _journaliser_signalement(request, info, categorie_nom,
                             ok=refus is None, detail=motif)
    return refus if refus is not None else _rendu(
        request, id_exemplaire, {"type": "signale"}
    )


def _signalement_a_fermer(conn: sqlite3.Connection, id_signalement: int,
                          id_exemplaire: str | None = None) -> dict | None:
    """
    Lit puis referme un signalement. Domicile UNIQUE de ce raisonnement,
    partagé par la route bénévole ci-dessous et par
    `routes/admin.py::signalement_traiter` (import différé depuis ce module,
    même patron que l'emprunt de `catalogue._entier_ou_none`) — le §2 de
    `docs/conception-signalements.md`, qui réservait ce geste à
    l'administrateur, est devenu faux avec ce lot.

    Relit le signalement AVANT l'écriture (`services.get_signalement`) : la
    ligne de journal doit nommer le jeu, que la table `signalements` ne porte
    pas, et son `traite_le` dit si ce clic change réellement quelque chose —
    voir `_journaliser_signalement_traite` ci-dessous, appelée séparément
    APRÈS la fermeture de la connexion, comme les autres actions de ce
    fichier.

    `id_exemplaire`, fourni par la route bénévole (jamais par l'admin, qui
    agit sans boîte de référence) : contrôle qu'une URL forgée ne referme pas
    le signalement d'une autre boîte. Incohérent -> None, RIEN n'est écrit,
    exactement comme un identifiant inconnu — c'est à l'appelant de traduire
    ça en message, jamais en erreur brute (règle « ne jamais bloquer »).

    Returns:
        Le signalement tel qu'AVANT l'écriture, ou None s'il est inconnu ou
        ne correspond pas à `id_exemplaire`.
    """
    avant = services.get_signalement(conn, id_signalement)
    if avant is None:
        return None
    if id_exemplaire is not None and avant["id_exemplaire"] != id_exemplaire:
        return None
    services.traiter_signalement(conn, id_signalement)
    return avant


def _journaliser_signalement_traite(request: Request, avant: dict | None) -> None:
    """
    Écrit la ligne `signalement_traite`, sauf si rien n'a réellement changé :
    `avant` à None (signalement inconnu ou d'une autre boîte, rien n'a été
    écrit par `_signalement_a_fermer`) ou déjà traité (second appui,
    `traiter_signalement` étant idempotent) — une ligne l'affirmerait alors
    que ce n'est pas arrivé. Même précaution que « annonce effacée »
    (routes/admin.py).
    """
    if avant is None or avant["traite_le"] is not None:
        return
    objet = avant["jeu_nom"]
    if avant["categorie_nom"]:
        objet = f"{objet} — {avant['categorie_nom']}"
    journal.journaliser(
        request, "pret", "signalement_traite",
        objet=objet, ref=avant["reference_titre"],
    )


@router.post("/{id_exemplaire}/signalements/{id_signalement}/traiter")
def signalement_traiter(request: Request, id_exemplaire: str, id_signalement: int,
                        _=Depends(exiger_jeton)):
    """
    Referme un signalement depuis la fiche de la boîte (§2 de la note de
    conception, corrigé par ce lot : ce n'est plus l'administrateur seul qui
    referme). Bouton du bandeau des signalements ouverts de `pret.html`.

    Suit le patron des autres actions de ce fichier : pas de redirection, on
    retourne l'écran de prêt avec un `resultat` de confirmation. Le tap est
    IRRÉVERSIBLE (arbitrage de Simon, série agora : pas de « Rouvrir », en cas
    d'erreur on ouvre un nouveau signalement) mais ne bloque jamais le prêt —
    aucun bouton n'est masqué, aucune confirmation supplémentaire.
    """
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return _rendu(request, id_exemplaire)  # 404, patron existant
        avant = _signalement_a_fermer(conn, id_signalement, id_exemplaire=id_exemplaire)
    finally:
        conn.close()
    _journaliser_signalement_traite(request, avant)
    if avant is None:
        return _rendu(request, id_exemplaire, {"type": "signalement_traite_echec"})
    return _rendu(
        request, id_exemplaire,
        {"type": "signalement_traite", "categorie": avant["categorie_nom"]},
    )
