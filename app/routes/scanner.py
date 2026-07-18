"""
Route du SCANNER caméra embarqué (outil bénévole).

Sert la page `/scanner` qui active la caméra et décode les QR côté navigateur
(toute la logique caméra est dans static/js/scanner.js). Quand un QR est lu, le
JS extrait l'id et redirige vers l'écran prêt/retour `/pret/<id>`.

En secours (étiquette arrachée, QR illisible, caméra capricieuse), un petit
formulaire sous la caméra permet de TAPER le code de la boîte (`id_exemplaire`)
et d'arriver directement sur `/pret/<id>` — sans repasser par le catalogue.

MODE RANGEMENT (docs/conception-rangement.md §4.a)
---------------------------------------------------
Un bénévole peut activer un « mode rangement » : au lieu d'ouvrir la fiche de
prêt, chaque scan (ou saisie manuelle) AFFECTE l'emplacement actif à la boîte,
sans jamais toucher à son état de prêt/pochette. L'emplacement actif est
mémorisé dans un cookie D'APPAREIL (`rangement_actif`, comme le jeton bénévole)
— chaque bénévole garde le sien, pas de réglage serveur partagé (plusieurs
personnes rangent en parallèle dans des zones différentes). Sa valeur se lit
par rapport au CONTEXTE courant (`services.rangement_contexte`, réglable en
admin) : texte libre en contexte "evenement", id d'emplacement en contexte
"local". Toujours résilient (`_etat_rangement`) : une valeur devenue invalide
(contexte changé, emplacement archivé/supprimé entre-temps) est traitée comme
"mode inactif", jamais une erreur.

Ces surfaces sont protégées par le jeton bénévole, comme /pret.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import services
from app.auth import exiger_jeton
from app.db import get_connection
from app.templating import templates

router = APIRouter(tags=["scanner"])

# Cookie d'appareil portant l'emplacement actif du mode rangement (§4.a).
# Le nom et la résolution du cookie vivent désormais dans `services` : le
# bandeau global de base.html doit les connaître aussi (fiche B1), et ils ne
# doivent avoir qu'un seul domicile. Alias conservé pour la lisibilité locale.
COOKIE_RANGEMENT = services.COOKIE_RANGEMENT
# Durée de vie du cookie : une session de rangement ne dépasse pas une
# journée d'événement ; pas besoin de survivre plus longtemps (contrairement
# au jeton bénévole, qui doit tenir plusieurs jours).
DUREE_COOKIE_RANGEMENT = 12 * 3600  # 12h


def _etat_rangement(conn, request: Request) -> dict:
    """Résout le cookie du mode rangement (voir `services.etat_rangement`)."""
    return services.etat_rangement(conn, request)


def _retour_interne(chemin: str) -> bool:
    """
    Le chemin de retour est-il une URL INTERNE sûre ?

    Refuse tout ce qui pourrait sortir du site : un chemin qui ne commence pas
    par « / », et la forme « //hote » que les navigateurs interprètent comme
    une URL absolue vers un autre domaine (redirection ouverte).
    """
    return chemin.startswith("/") and not chemin.startswith("//")


def _contexte_scanner(conn, etat: dict, **extra) -> dict:
    """Contexte commun de rendu de scanner.html (état du mode rangement + menu local)."""
    emplacements = services.emplacements_actifs(conn) if etat["contexte"] == "local" else []
    ctx = {"rangement": etat, "emplacements_locaux": emplacements}
    ctx.update(extra)
    return ctx


@router.get("/scanner")
def scanner(request: Request, _=Depends(exiger_jeton)):
    conn = get_connection()
    try:
        etat = _etat_rangement(conn, request)
        ctx = _contexte_scanner(conn, etat)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "scanner.html", ctx)


@router.get("/scanner/saisie")
def saisie_manuelle(request: Request, code: str = "", _=Depends(exiger_jeton)):
    """
    Secours clavier. Comportement inchangé hors mode rangement (ouvre
    /pret/<id>). En mode rangement, AFFECTE l'emplacement actif au lieu
    d'ouvrir la fiche de prêt — même endpoint logique que le canal caméra
    (§4.b), cohérent avec /scanner/ranger.
    """
    id_exemplaire = (code or "").strip()
    conn = get_connection()
    try:
        etat = _etat_rangement(conn, request)

        if not id_exemplaire:
            return templates.TemplateResponse(
                request, "scanner.html",
                _contexte_scanner(conn, etat, erreur="Veuillez saisir un code.", code_saisi=""),
            )

        if etat["actif"]:
            resultat = services.affecter_emplacement(
                conn, id_exemplaire, etat["contexte"], etat["valeur"]
            )
            if resultat is None:
                return templates.TemplateResponse(
                    request, "scanner.html",
                    _contexte_scanner(
                        conn, etat,
                        erreur=f"Aucune boîte ne porte le code « {id_exemplaire} ». "
                               "Vérifiez et réessayez.",
                        code_saisi=id_exemplaire,
                    ),
                )
            return templates.TemplateResponse(
                request, "scanner.html",
                _contexte_scanner(
                    conn, etat,
                    confirmation_rangement=f"{resultat['nom']} rangé en {etat['label']}.",
                ),
            )

        # Hors mode rangement : comportement historique (ouvre la fiche de prêt).
        info = services.info_exemplaire(conn, id_exemplaire)
        if info is None:
            return templates.TemplateResponse(
                request, "scanner.html",
                _contexte_scanner(
                    conn, etat,
                    erreur=f"Aucune boîte ne porte le code « {id_exemplaire} ». "
                           "Vérifiez et réessayez.",
                    code_saisi=id_exemplaire,
                ),
            )
    finally:
        conn.close()

    return RedirectResponse(f"/pret/{id_exemplaire}", status_code=303)


@router.get("/scanner/ranger")
def ranger(request: Request, code: str = "", _=Depends(exiger_jeton)):
    """
    Cible du scan caméra EN MODE RANGEMENT (scanner.js redirige ici au lieu de
    /pret/<id> tant que <body data-rangement="1"> est posé). Affecte
    l'emplacement actif à la boîte scannée puis réaffiche /scanner, caméra
    prête pour la suivante. Jamais bloquant : code inconnu -> message, pas
    d'erreur brute ; mode déjà quitté entre-temps -> écran scanner normal.
    """
    id_exemplaire = (code or "").strip()
    conn = get_connection()
    try:
        etat = _etat_rangement(conn, request)
        if not etat["actif"]:
            return templates.TemplateResponse(request, "scanner.html", _contexte_scanner(conn, etat))

        if not id_exemplaire:
            return templates.TemplateResponse(
                request, "scanner.html",
                _contexte_scanner(conn, etat, erreur="Code manquant.", code_saisi=""),
            )

        resultat = services.affecter_emplacement(conn, id_exemplaire, etat["contexte"], etat["valeur"])
        if resultat is None:
            return templates.TemplateResponse(
                request, "scanner.html",
                _contexte_scanner(
                    conn, etat,
                    erreur=f"Aucune boîte ne porte le code « {id_exemplaire} ». "
                           "Vérifiez et réessayez.",
                    code_saisi=id_exemplaire,
                ),
            )
        return templates.TemplateResponse(
            request, "scanner.html",
            _contexte_scanner(
                conn, etat,
                confirmation_rangement=f"{resultat['nom']} rangé en {etat['label']}.",
            ),
        )
    finally:
        conn.close()


@router.post("/scanner/rangement/activer")
def rangement_activer(
    request: Request,
    emplacement_texte: str = Form(""),
    emplacement_id: str = Form(""),
    _=Depends(exiger_jeton),
):
    """
    Active (ou change) l'emplacement actif du mode rangement : pose le cookie
    d'appareil et revient sur /scanner. Formulaire vide/invalide -> ignoré
    silencieusement (le <select> ne propose que des emplacements actifs ;
    n'arrive en pratique qu'en cas de manipulation directe de l'URL).
    """
    conn = get_connection()
    try:
        contexte = services.rangement_contexte(conn)
        if contexte == "local":
            valeur = emplacement_id.strip()
            if valeur:
                actifs = {str(e["id_emplacement"]) for e in services.emplacements_actifs(conn)}
                if valeur not in actifs:
                    valeur = ""
        else:
            valeur = " ".join(emplacement_texte.split())
    finally:
        conn.close()

    reponse = RedirectResponse("/scanner", status_code=303)
    if valeur:
        reponse.set_cookie(
            COOKIE_RANGEMENT, valeur, max_age=DUREE_COOKIE_RANGEMENT,
            httponly=True, samesite="lax", secure=(request.url.scheme == "https"),
        )
    return reponse


@router.post("/scanner/rangement/quitter")
def rangement_quitter(request: Request, retour: str = Form(""), _=Depends(exiger_jeton)):
    """
    Quitte le mode rangement : efface le cookie, retour au scan-vers-prêt
    normal.

    Le bandeau global (base.html, fiche B1) permet de quitter depuis
    N'IMPORTE QUELLE page : il transmet alors `retour` pour qu'on revienne là
    où l'on était plutôt que d'être téléporté au scanner. Seuls les chemins
    INTERNES sont acceptés (jamais de redirection ouverte vers un site tiers),
    avec un repli sur /scanner — comportement historique quand le champ est
    absent, ce qui reste le cas du formulaire de la page scanner elle-même.
    """
    destination = retour if _retour_interne(retour) else "/scanner"
    reponse = RedirectResponse(destination, status_code=303)
    reponse.delete_cookie(COOKIE_RANGEMENT)
    return reponse
