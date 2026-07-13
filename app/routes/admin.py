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
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app import admin_auth, auth, exports, services
from app.auth import trop_de_tentatives  # limite de débit par IP (partagée)
from app.db import get_connection
from app.etiquettes import charger_logo, image_etiquette, planche_pdf, url_fiche
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
        return templates.TemplateResponse(request, "admin_dashboard.html", {"message": None})
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
# Impression d'étiquettes en lot
# ---------------------------------------------------------------------------
@router.get("/etiquettes")
def etiquettes_selection(request: Request, categorie: str | None = None,
                         message: str | None = None):
    """
    Page de sélection des jeux à imprimer + paramètres de planche.

    Filtre optionnel par catégorie. Chaque jeu coché imprimera les étiquettes de
    TOUS ses exemplaires.
    """
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        categories = services.lister_categories(conn)
        filtre = categorie if categorie in categories else None
        jeux = services.titres_pour_etiquettes(conn, filtre)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_etiquettes.html",
        {"jeux": jeux, "categories": categories, "filtre": filtre,
         "base_url": _base_url(request), "message": message},
    )


def _float_ou(defaut: float, valeur: str) -> float:
    """Convertit une saisie en flottant ≥ 0, ou renvoie `defaut`."""
    try:
        v = float((valeur or "").replace(",", ".").strip())
        return v if v >= 0 else defaut
    except ValueError:
        return defaut


@router.post("/etiquettes/pdf")
def etiquettes_pdf(
    request: Request,
    references: list[str] = Form(default=[]),
    lignes: int = Form(8),
    colonnes: int = Form(2),
    marge_gauche: str = Form("8"),
    marge_droite: str = Form("8"),
    marge_haut: str = Form("8"),
    marge_bas: str = Form("8"),
):
    """
    Génère et télécharge la planche PDF couleur des étiquettes des jeux cochés.

    En cas de sélection vide ou de paramètres invalides, réaffiche la page de
    sélection avec un message (jamais d'erreur brute).
    """
    if (garde := _garde(request)):
        return garde
    if not references:
        return etiquettes_selection(
            request, message="Sélectionnez au moins un jeu.")
    if lignes < 1 or colonnes < 1:
        return etiquettes_selection(
            request, message="Le nombre de lignes et de colonnes doit être ≥ 1.")

    conn = get_connection()
    try:
        exemplaires = services.exemplaires_pour_etiquettes(conn, references)
    finally:
        conn.close()
    if not exemplaires:
        return etiquettes_selection(request, message="Aucune étiquette à imprimer.")

    try:
        pdf = planche_pdf(
            exemplaires, _base_url(request), charger_logo(),
            lignes=lignes, colonnes=colonnes,
            marge_gauche_mm=_float_ou(8, marge_gauche),
            marge_droite_mm=_float_ou(8, marge_droite),
            marge_haut_mm=_float_ou(8, marge_haut),
            marge_bas_mm=_float_ou(8, marge_bas),
        )
    except ValueError as exc:
        return etiquettes_selection(request, message=str(exc))

    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="etiquettes.pdf"'},
    )


# ---------------------------------------------------------------------------
# Base de données : import d'un catalogue CSV, export CSV / Excel
# ---------------------------------------------------------------------------
def _page_donnees(request: Request, message: tuple | None = None):
    """Rend la page « Base de données » avec les compteurs actuels."""
    conn = get_connection()
    try:
        nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
        nb_ex = conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_donnees.html",
        {"nb_titres": nb_titres, "nb_ex": nb_ex, "message": message},
    )


@router.get("/donnees")
def donnees(request: Request):
    """Page d'import / export du catalogue."""
    if (garde := _garde(request)):
        return garde
    return _page_donnees(request)


@router.post("/donnees/import")
def donnees_import(request: Request, fichier: UploadFile = File(...)):
    """
    Importe un catalogue CSV téléversé (mêmes règles que scripts/import_csv :
    tolérant aux colonnes, idempotent en UPSERT). Réaffiche la page avec un
    compte rendu ; jamais d'erreur brute.
    """
    if (garde := _garde(request)):
        return garde

    import tempfile
    from pathlib import Path

    from scripts import import_csv

    contenu = fichier.file.read()
    if not contenu:
        return _page_donnees(request, ("erreur", "Fichier vide ou absent."))

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(contenu)
        chemin = Path(tmp.name)
    try:
        res = import_csv.importer(chemin)
        texte = (f"Import réussi : {res['exemplaires']} exemplaire(s) / "
                 f"{res['titres']} titre(s).")
        if res["ignores"]:
            texte += f" {len(res['ignores'])} ligne(s) ignorée(s)."
        message = ("succes", texte)
    except SystemExit as exc:            # colonnes clés absentes
        message = ("erreur", str(exc))
    except Exception as exc:             # tout autre souci de lecture
        message = ("erreur", f"Import impossible : {exc}")
    finally:
        try:
            chemin.unlink()
        except OSError:
            pass
    return _page_donnees(request, message)


@router.get("/donnees/export.csv")
def donnees_export_csv(request: Request):
    """Télécharge le catalogue au format CSV (ré-importable)."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    return Response(
        content=exports.catalogue_csv(entetes, lignes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="catalogue.csv"'},
    )


@router.get("/donnees/export.xlsx")
def donnees_export_xlsx(request: Request):
    """Télécharge le catalogue au format Excel."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    return Response(
        content=exports.catalogue_xlsx(entetes, lignes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="catalogue.xlsx"'},
    )


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
    type_jeu: str = Form("Jeu"),
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
            type_jeu=type_jeu.strip() or None,
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


# ---------------------------------------------------------------------------
# Jeton bénévole : lien d'activation, réinitialisation, partage
# ---------------------------------------------------------------------------
@router.get("/jeton")
def jeton_page(request: Request):
    """Affiche le lien d'activation bénévole et les options de partage."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        jeton = auth.jeton_actuel(conn)
        expire_iso = auth.expiration_jeton(conn)
        expire_depasse = auth.jeton_expire(conn)
    finally:
        conn.close()

    expire_local = services.format_local(expire_iso) if expire_iso else None
    lien, partage = None, {}
    if jeton:
        lien = f"{_base_url(request)}/acces?jeton={jeton}"
        message = f"Accès bénévole — Des jeux plein la Manche : {lien}"
        partage = {
            "whatsapp": "https://wa.me/?text=" + quote(message),
            "mail": ("mailto:?subject=" + quote("Accès bénévole — prêt de jeux")
                     + "&body=" + quote(message)),
            "sms": "sms:?&body=" + quote(message),
            # Discord n'a pas de lien de partage pré-rempli : on copie le message
            # (le gabarit fournit un bouton « copier pour Discord »).
            "message": message,
        }
    return templates.TemplateResponse(
        request, "admin_jeton.html",
        {"jeton": jeton, "lien": lien, "partage": partage,
         "expire_local": expire_local, "expire_depasse": expire_depasse,
         "defaut_jours": auth.DUREE_DEFAUT_JOURS},
    )


@router.get("/evenement")
def evenement_formulaire(request: Request):
    """Réglage de la date de l'événement (jour 1 du planning public sur 2 jours)."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        valeur = services.lire_parametre(conn, "evenement_date")
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_evenement.html", {"date_evenement": valeur, "message": None}
    )


@router.post("/evenement")
def evenement_enregistrer(request: Request, date_evenement: str = Form("")):
    """
    Enregistre (ou efface si vide) la date de l'événement. Le planning public
    couvre ce jour-là et le lendemain. Format attendu : AAAA-MM-JJ.
    """
    if (garde := _garde(request)):
        return garde
    from datetime import datetime as _dt

    saisie = date_evenement.strip()
    if saisie:
        try:
            _dt.strptime(saisie, "%Y-%m-%d")
        except ValueError:
            return templates.TemplateResponse(
                request, "admin_evenement.html",
                {"date_evenement": saisie,
                 "message": ("erreur", "Date invalide (format attendu : AAAA-MM-JJ).")},
                status_code=400,
            )
    conn = get_connection()
    try:
        services.ecrire_parametre(conn, "evenement_date", saisie or None)
    finally:
        conn.close()
    message = (("succes", "Date enregistrée.") if saisie
               else ("succes", "Date effacée — le planning est masqué."))
    return templates.TemplateResponse(
        request, "admin_evenement.html",
        {"date_evenement": saisie or None, "message": message}
    )


@router.get("/ecran-salle")
def ecran_salle_formulaire(request: Request):
    """Réglage du titre affiché en haut de l'écran de salle (/live)."""
    if (garde := _garde(request)):
        return garde
    from app.routes.live import CLE_TITRE, TITRE_DEFAUT

    conn = get_connection()
    try:
        titre = services.lire_parametre(conn, CLE_TITRE, TITRE_DEFAUT)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_live.html",
        {"titre": titre, "titre_defaut": TITRE_DEFAUT, "message": None},
    )


@router.post("/ecran-salle")
def ecran_salle_enregistrer(request: Request, titre: str = Form("")):
    """
    Enregistre le titre de l'écran de salle. Vide => retour au titre par défaut.
    """
    if (garde := _garde(request)):
        return garde
    from app.routes.live import CLE_TITRE, TITRE_DEFAUT

    saisie = " ".join(titre.split())[:80]   # espaces normalisés, longueur bornée
    conn = get_connection()
    try:
        services.ecrire_parametre(conn, CLE_TITRE, saisie or None)
    finally:
        conn.close()
    message = (("succes", "Titre enregistré.") if saisie
               else ("succes", "Titre effacé — le titre par défaut est utilisé."))
    return templates.TemplateResponse(
        request, "admin_live.html",
        {"titre": saisie or TITRE_DEFAUT, "titre_defaut": TITRE_DEFAUT,
         "message": message},
    )


@router.post("/cloturer-prets")
def cloturer_prets(request: Request):
    """Clôture tous les prêts/sorties en cours (fin d'événement) ; garde l'historique."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        nb = services.cloturer_tous_les_prets(conn)
    finally:
        conn.close()
    message = ("succes", f"{nb} prêt(s)/sortie(s) clôturé(s). Tout est de nouveau disponible.")
    return templates.TemplateResponse(request, "admin_dashboard.html", {"message": message})


@router.post("/jeton/reinitialiser")
def jeton_reinitialiser(request: Request, expire: str = Form("")):
    """
    Génère un nouveau jeton bénévole (invalide les anciens) puis réaffiche la page.

    `expire` (datetime-local, heure locale) fixe la fin de validité ; vide → la
    durée par défaut (1 semaine) est appliquée par `auth.reinitialiser_jeton`.
    """
    if (garde := _garde(request)):
        return garde
    expire_utc = services.local_vers_utc_iso(expire.strip() or None)
    conn = get_connection()
    try:
        auth.reinitialiser_jeton(conn, expire_utc)
    finally:
        conn.close()
    return RedirectResponse("/admin/jeton", status_code=303)
