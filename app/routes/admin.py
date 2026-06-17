"""
Espace d'ADMINISTRATION — protégé par MOT DE PASSE (voir app/admin_auth.py).

Accessible à un bénévole non technicien pour :
- créer une nouvelle fiche de jeu (id_exemplaire attribué automatiquement) ;
- consulter la fiche d'un jeu et (ré)imprimer l'étiquette de chaque exemplaire
  (utile si une étiquette est abîmée) ;
- changer le mot de passe administrateur.

Différence avec les écrans bénévole : ceux-ci utilisent le JETON (cookie via
/acces) ; l'admin utilise un vrai mot de passe + une session. En cas d'accès non
authentifié, on REDIRIGE vers la page de connexion /admin (et non une 403).

Toutes les routes (sauf la connexion) commencent par vérifier la session via
`_garde(request)`.
"""

import os
from io import BytesIO

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app import admin_auth, services
from app.auth import trop_de_tentatives  # limite de débit par IP (partagée)
from app.db import get_connection
from app.etiquettes import charger_logo, image_etiquette, url_fiche
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _garde(request: Request):
    """
    Renvoie une redirection vers la connexion si l'admin n'est pas authentifié,
    sinon None. À appeler en tête de chaque route protégée.
    """
    if not admin_auth.admin_connecte(request):
        return RedirectResponse("/admin", status_code=303)
    return None


def _base_url(request: Request) -> str:
    """URL de base pour le QR : BASE_URL du .env, sinon l'URL courante."""
    return os.getenv("BASE_URL") or str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# Connexion / déconnexion
# ---------------------------------------------------------------------------
@router.get("")
def accueil(request: Request):
    """Page d'accueil admin : tableau de bord si connecté, sinon formulaire de connexion."""
    if admin_auth.admin_connecte(request):
        return templates.TemplateResponse(request, "admin_dashboard.html", {})
    conn = get_connection()
    try:
        configure = admin_auth.admin_configure(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_login.html", {"configure": configure, "erreur": False}
    )


@router.post("/login")
def login(request: Request, mot_de_passe: str = Form("")):
    """Vérifie le mot de passe (avec limite de débit) et ouvre une session."""
    ip = request.client.host if request.client else "inconnu"
    limite = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    if trop_de_tentatives(ip, limite):
        return templates.TemplateResponse(
            request, "admin_login.html",
            {"configure": True, "erreur": "trop"}, status_code=429,
        )

    conn = get_connection()
    try:
        ok = admin_auth.verifier_identifiants(conn, mot_de_passe)
        configure = admin_auth.admin_configure(conn)
    finally:
        conn.close()

    if not ok:
        return templates.TemplateResponse(
            request, "admin_login.html",
            {"configure": configure, "erreur": True}, status_code=403,
        )

    sid = admin_auth.ouvrir_session()
    reponse = RedirectResponse("/admin", status_code=303)
    reponse.set_cookie(
        admin_auth.COOKIE_ADMIN, sid,
        max_age=admin_auth.DUREE_SESSION, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
    )
    return reponse


@router.get("/logout")
def logout(request: Request):
    """Ferme la session admin et efface le cookie."""
    admin_auth.fermer_session(request.cookies.get(admin_auth.COOKIE_ADMIN))
    reponse = RedirectResponse("/admin", status_code=303)
    reponse.delete_cookie(admin_auth.COOKIE_ADMIN)
    return reponse


# ---------------------------------------------------------------------------
# Catalogue admin : rechercher un jeu, voir sa fiche, (ré)imprimer les étiquettes
# ---------------------------------------------------------------------------
@router.get("/jeux")
def liste_jeux(request: Request, q: str | None = None):
    """Liste/recherche des jeux pour atteindre une fiche (réimpression incluse)."""
    if (garde := _garde(request)):
        return garde
    q = (q or "").strip() or None
    conn = get_connection()
    try:
        jeux = services.lister_catalogue(conn, q=q)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_jeux.html", {"jeux": jeux, "q": q}
    )


@router.get("/jeu/{reference_titre}")
def fiche_admin(request: Request, reference_titre: str):
    """Fiche admin d'un jeu : ses exemplaires + l'étiquette de chacun."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        titre = services.get_titre(conn, reference_titre)
        exemplaires = services.lister_exemplaires_du_titre(conn, reference_titre) if titre else []
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_fiche.html",
        {"titre": titre, "exemplaires": exemplaires},
        status_code=200 if titre else 404,
    )


@router.post("/jeu/{reference_titre}/exemplaire")
def ajouter_exemplaire(request: Request, reference_titre: str):
    """Ajoute un exemplaire (id auto) au titre, puis revient à sa fiche."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        services.ajouter_exemplaire(conn, reference_titre)
    finally:
        conn.close()
    return RedirectResponse(f"/admin/jeu/{reference_titre}", status_code=303)


@router.get("/etiquette/{id_exemplaire}.png")
def etiquette_png(request: Request, id_exemplaire: str):
    """
    Renvoie l'étiquette (PNG) d'un exemplaire, pour affichage/impression.

    Utilise le rendu PARTAGÉ (app/etiquettes) : identique aux étiquettes générées
    en lot. C'est ce qui permet de réimprimer une étiquette abîmée sans changer
    le code du QR.
    """
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        info = services.info_exemplaire(conn, id_exemplaire)
    finally:
        conn.close()
    if info is None:
        return Response(status_code=404)
    url = url_fiche(_base_url(request), id_exemplaire)
    img = image_etiquette(url, info, charger_logo())
    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------------------------
# Création d'un jeu
# ---------------------------------------------------------------------------
def _int_ou_none(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


@router.get("/jeu-nouveau")
def nouveau_formulaire(request: Request):
    """Formulaire de création d'un jeu."""
    if (garde := _garde(request)):
        return garde
    return templates.TemplateResponse(request, "admin_jeu_nouveau.html", {"erreur": None})


@router.post("/jeu-nouveau")
def nouveau_creer(
    request: Request,
    nom: str = Form(""),
    categorie: str = Form(""),
    nb_joueurs_min: str = Form(""),
    nb_joueurs_max: str = Form(""),
    duree_min: str = Form(""),
    age_min: str = Form(""),
    editeur: str = Form(""),
    auteur: str = Form(""),
    annee_edition: str = Form(""),
    descriptif: str = Form(""),
):
    """Crée le jeu (titre + 1 exemplaire id auto) puis ouvre sa fiche admin."""
    if (garde := _garde(request)):
        return garde
    if not nom.strip():
        return templates.TemplateResponse(
            request, "admin_jeu_nouveau.html",
            {"erreur": "Le nom est obligatoire."}, status_code=400,
        )
    conn = get_connection()
    try:
        res = services.creer_jeu(
            conn, nom,
            categorie=categorie.strip() or None,
            nb_joueurs_min=_int_ou_none(nb_joueurs_min),
            nb_joueurs_max=_int_ou_none(nb_joueurs_max),
            duree_min=_int_ou_none(duree_min),
            age_min=_int_ou_none(age_min),
            editeur=editeur.strip() or None,
            auteur=auteur.strip() or None,
            annee_edition=_int_ou_none(annee_edition),
            descriptif=descriptif.strip() or None,
        )
    finally:
        conn.close()
    return RedirectResponse(f"/admin/jeu/{res['reference_titre']}", status_code=303)


# ---------------------------------------------------------------------------
# Changement de mot de passe
# ---------------------------------------------------------------------------
@router.get("/motdepasse")
def motdepasse_formulaire(request: Request):
    """Formulaire de changement de mot de passe."""
    if (garde := _garde(request)):
        return garde
    return templates.TemplateResponse(request, "admin_motdepasse.html", {"message": None})


@router.post("/motdepasse")
def motdepasse_changer(
    request: Request,
    ancien: str = Form(""),
    nouveau: str = Form(""),
    confirmation: str = Form(""),
):
    """Change le mot de passe admin après vérification de l'ancien."""
    if (garde := _garde(request)):
        return garde
    if nouveau != confirmation:
        message = ("erreur", "La confirmation ne correspond pas au nouveau mot de passe.")
    else:
        conn = get_connection()
        try:
            ok = admin_auth.changer_mot_de_passe(conn, ancien, nouveau)
        finally:
            conn.close()
        message = (("succes", "Mot de passe modifié.") if ok else
                   ("erreur", "Ancien mot de passe incorrect ou nouveau invalide."))
    return templates.TemplateResponse(request, "admin_motdepasse.html", {"message": message})
