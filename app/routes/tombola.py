"""
Tombola — page BÉNÉVOLE de gestion (`/tombola/gestion`), série tombola, lot 1.

Saisir un tirage (à la main, ou par l'application en secours), remettre un
lot, annuler une remise dans la minute, retirer un numéro mal tapé dans les
cinq minutes. Toute la logique est dans `app/tombola.py` ; ce module lit les
formulaires, appelle, JOURNALISE (seules les routes journalisent,
docs/conception-journal.md § 5.2) et rend la page.

`/tombola` reste LIBRE : c'est l'adresse de la page publique du lot 2.

⚠️ DEUX GARDES, COMME LE CARNET DE MAINTENANCE (`routes/maintenance.py`)
------------------------------------------------------------------------
`main.py` enregistre ce routeur derrière `garde_module("tombola")` : c'est la
VISIBILITÉ, réglée par le bureau (désactivée par défaut). Chaque route porte
en plus son `Depends(exiger_jeton)` : c'est l'AUTORISATION d'accès. Le module
n'est pas `jamais_public` (le lot 2 lui donnera une page publique) : sans
cette seconde garde, le régler sur « visible par tous » ouvrirait la saisie
des tirages au premier visiteur venu.

RENDU DIRECT APRÈS CHAQUE ENVOI, SANS REDIRECTION (arbitrage du lot)
--------------------------------------------------------------------
Le patron de `/live/annonce` est une redirection 303 avec des codes. Ici,
chaque POST rend la page directement :
- une saisie à corriger doit être RÉAFFICHÉE dans ses champs, et le message
  nomme ce qui a été écarté, saisie comprise : une adresse ne le porte pas ;
- le rechargement d'un POST, inconvénient habituel du rendu direct, est ici
  INOFFENSIF par construction : un tirage renvoyé porte le même jeton de
  formulaire (rien n'est créé, le premier résultat est réaffiché), une remise
  renvoyée tombe dans « déjà remis », une annulation ou un retrait renvoyés
  ne changent plus rien ;
- un seul chemin pour construire les messages, sans codes à relire ni
  adresse forgée à neutraliser.
Chaque adresse de POST ouverte en GET (historique, onglet restauré) revient
à la page, jamais une erreur 405.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import journal, tombola
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

router = APIRouter(prefix="/tombola", tags=["tombola"])

URL_GESTION = "/tombola/gestion"
URL_TIRAGE = "/tombola/gestion/tirage"
URL_TIRAGE_ALEATOIRE = "/tombola/gestion/tirage-aleatoire"
URL_REMETTRE = "/tombola/gestion/remettre"
URL_ANNULER = "/tombola/gestion/annuler"
URL_ERREUR = "/tombola/gestion/erreur-saisie"


def _entier(valeur: str | None) -> int | None:
    try:
        return int((valeur or "").strip())
    except ValueError:
        return None


def _page(request: Request, message: tombola.Message | None = None, *,
          saisies: list[str] | None = None, saisie_max: str | None = None,
          saisie_remise: str = "", focus_remise: bool = True):
    """
    Rendu commun : le GET et chaque envoi. Deux jetons de formulaire NEUFS à
    chaque rendu, un par formulaire de tirage — un jeton partagé ferait d'un
    tirage aléatoire, envoyé après un tirage manuel depuis la même page, le
    « double appui » du premier.

    `focus_remise` : le champ « Numéro présenté » reçoit le focus à
    l'ouverture et après chaque remise (le geste répété de la journée), pas
    après un tirage, dont on veut relire les numéros.
    """
    conn = get_connection()
    try:
        groupes = tombola.numeros_en_attente(conn)
        numero_max = tombola.lire_numero_max(conn)
    finally:
        conn.close()
    saisies = list(saisies or [])
    saisies += [""] * (tombola.NUMEROS_PAR_TIRAGE - len(saisies))
    return templates.TemplateResponse(
        request, "tombola_gestion.html",
        {"message": message,
         "groupes": groupes,
         "nb_en_attente": sum(len(g["numeros"]) for g in groupes),
         "saisies": saisies[:tombola.NUMEROS_PAR_TIRAGE],
         "saisie_max": saisie_max if saisie_max is not None
                       else (str(numero_max) if numero_max else ""),
         "saisie_remise": saisie_remise,
         "focus_remise": focus_remise,
         "jeton_manuel": tombola.nouveau_jeton(),
         "jeton_aleatoire": tombola.nouveau_jeton(),
         "numero_min": tombola.NUMERO_MIN,
         "numero_max_borne": tombola.NUMERO_MAX,
         "delai_erreur_min": tombola.DELAI_ERREUR_SAISIE_S // 60,
         "mode_aleatoire": tombola.MODE_ALEATOIRE,
         "actions": {"tirage": URL_TIRAGE, "aleatoire": URL_TIRAGE_ALEATOIRE,
                     "remettre": URL_REMETTRE, "annuler": URL_ANNULER,
                     "erreur": URL_ERREUR}},
    )


def _retour():
    return RedirectResponse(URL_GESTION, status_code=303)


@router.get("/gestion")
def gestion(request: Request, _=Depends(exiger_jeton)):
    """La page bénévole : remise en haut, numéros en attente, puis les tirages."""
    return _page(request)


def _journaliser_tirage(request: Request, t: tombola.Tirage) -> None:
    """Une ligne par tirage ÉCRIT ; rien pour un envoi rejoué ou sans effet."""
    if t.issue == tombola.TIRAGE_ENREGISTRE:
        journal.journaliser(request, "tombola", "tombola_tirage", objet=t.mode,
                            ref=" ".join(str(n) for n in t.numeros))


@router.post("/gestion/tirage")
def tirage_manuel(request: Request,
                  numero_1: str = Form(""), numero_2: str = Form(""),
                  numero_3: str = Form(""), numero_4: str = Form(""),
                  numero_5: str = Form(""), jeton: str = Form(""),
                  _=Depends(exiger_jeton)):
    """
    Saisie manuelle de 1 à 5 numéros. Sans aucun numéro valide, rien n'est
    écrit et la saisie revient dans ses champs ; sinon les champs sont vidés
    pour le tirage suivant.
    """
    saisies = [numero_1, numero_2, numero_3, numero_4, numero_5]
    conn = get_connection()
    try:
        t = tombola.saisir_tirage(conn, saisies, jeton)
    finally:
        conn.close()
    _journaliser_tirage(request, t)
    garder = t.issue in (tombola.TIRAGE_AUCUN_VALIDE, tombola.TIRAGE_VIDE)
    return _page(request, tombola.message_tirage(t),
                 saisies=[s[:20] for s in saisies] if garder else None,
                 focus_remise=False)


@router.post("/gestion/tirage-aleatoire")
def tirage_aleatoire(request: Request, numero_max: str = Form(""),
                     jeton: str = Form(""), _=Depends(exiger_jeton)):
    """
    Tirage par l'application, en secours : enregistré et affiché dès
    l'appui, sans aucun moyen de le reprendre (décision 3).
    """
    conn = get_connection()
    try:
        t = tombola.tirer_au_hasard(conn, numero_max, jeton)
    finally:
        conn.close()
    _journaliser_tirage(request, t)
    return _page(request, tombola.message_tirage(t),
                 saisie_max=numero_max[:20] if t.issue == tombola.TIRAGE_MAX_INVALIDE else None,
                 focus_remise=False)


@router.post("/gestion/remettre")
def remettre(request: Request, numero: str = Form(""), numero_id: str = Form(""),
             _=Depends(exiger_jeton)):
    """
    « Lot remis » : par le champ « Numéro présenté » (`numero`), ou par le
    bouton d'un numéro en attente (`numero_id`, l'identifiant de la ligne).
    Une saisie illisible revient dans le champ.
    """
    identifiant = _entier(numero_id)
    conn = get_connection()
    try:
        if identifiant is not None:
            g = tombola.remettre_par_id(conn, identifiant)
        else:
            g = tombola.remettre_par_numero(conn, numero)
    finally:
        conn.close()
    if g.ecrit:
        journal.journaliser(request, "tombola", "tombola_lot_remis", objet=str(g.numero))
    garder = g.issue == tombola.REMISE_ILLISIBLE and identifiant is None
    return _page(request, tombola.message_remise(g),
                 saisie_remise=numero[:20] if garder else "")


@router.post("/gestion/annuler")
def annuler(request: Request, numero_id: str = Form(""), _=Depends(exiger_jeton)):
    """Annule une remise de moins d'une minute ; vise la ligne, pas le numéro."""
    identifiant = _entier(numero_id)
    conn = get_connection()
    try:
        g = (tombola.annuler_remise(conn, identifiant) if identifiant is not None
             else tombola.Geste(tombola.ANNULATION_IMPOSSIBLE))
    finally:
        conn.close()
    if g.ecrit:
        journal.journaliser(request, "tombola", "tombola_remise_annulee",
                            objet=str(g.numero))
    return _page(request, tombola.message_annulation(g))


@router.post("/gestion/erreur-saisie")
def erreur_saisie(request: Request, numero_id: str = Form(""),
                  _=Depends(exiger_jeton)):
    """Retire un numéro mal tapé, dans les cinq minutes de son affichage."""
    identifiant = _entier(numero_id)
    conn = get_connection()
    try:
        g = (tombola.retirer_erreur_saisie(conn, identifiant) if identifiant is not None
             else tombola.Geste(tombola.RETRAIT_INCONNU))
    finally:
        conn.close()
    if g.ecrit:
        journal.journaliser(request, "tombola", "tombola_erreur_saisie",
                            objet=str(g.numero))
    return _page(request, tombola.message_retrait(g), focus_remise=False)


@router.get("/gestion/tirage")
@router.get("/gestion/tirage-aleatoire")
@router.get("/gestion/remettre")
@router.get("/gestion/annuler")
@router.get("/gestion/erreur-saisie")
def adresse_d_envoi_en_get(_=Depends(exiger_jeton)):
    """Une adresse d'envoi ouverte en GET revient à la page, jamais une 405."""
    return _retour()
