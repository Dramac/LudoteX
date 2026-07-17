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

from app import admin_auth, auth, exports, formation, sauvegarde, services, supervision
from app.auth import trop_de_tentatives  # limite de débit par IP (partagée)
from app.config import MODE_FORMATION, NOM_ASSOCIATION
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


def _rendre_dashboard(request: Request, message):
    """
    Rend le tableau de bord admin, avec l'état de supervision (bases, disque,
    sauvegarde, jeton, version) embarqué dans la colonne dédiée sur grand
    écran (voir `admin_dashboard.html` + `_supervision_contenu.html`, réutilisé
    par `/admin/supervision`). Centralisé ici pour ne pas dupliquer l'appel
    à `supervision.etat_supervision` dans chaque route qui réaffiche le tableau
    de bord (connexion, clôture des prêts, réinitialisation formation).
    """
    conn = get_connection()
    try:
        etat = supervision.etat_supervision(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_dashboard.html", {"message": message, "etat": etat}
    )


# ---------------------------------------------------------------------------
# Connexion / déconnexion
# ---------------------------------------------------------------------------
@router.get("")
def accueil(request: Request):
    """Page d'accueil admin : tableau de bord si connecté, sinon formulaire de connexion."""
    if admin_auth.admin_connecte(request):
        return _rendre_dashboard(request, None)
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
        # Menu déroulant de l'édition « emplacement local » par exemplaire
        # (§4.c) : mêmes emplacements actifs que partout ailleurs.
        emplacements_locaux = services.emplacements_actifs(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_fiche.html",
        {"titre": titre, "exemplaires": exemplaires, "emplacements_locaux": emplacements_locaux},
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


# ---------------------------------------------------------------------------
# Édition à l'unité de l'emplacement de rangement d'un exemplaire (§4.c).
# Les DEUX contextes sont éditables indépendamment ici (contrairement au
# scanner, qui n'agit que sur le contexte GLOBALEMENT actif) : texte libre
# pour l'événement, menu déroulant des emplacements actifs pour le local.
#
# `retour` (optionnel, sur le modèle de app/planning/routes.py::_retour) :
# permet à la page des manques (§4.d, saisie rapide en ligne) de réutiliser
# CES MÊMES routes plutôt que d'en dupliquer une variante — on revient sur
# `retour` s'il est fourni et interne (évite tout redirect ouvert), sinon sur
# la fiche admin du titre comme avant.
# ---------------------------------------------------------------------------
def _retour_emplacement(reference_titre: str, retour: str) -> str:
    if retour.startswith("/admin/"):
        return retour
    return f"/admin/jeu/{reference_titre}"


@router.post("/jeu/{reference_titre}/exemplaire/{id_exemplaire}/emplacement-evenement")
def exemplaire_emplacement_evenement(
    request: Request, reference_titre: str, id_exemplaire: str,
    emplacement_texte: str = Form(""), retour: str = Form(""),
):
    """Édite le texte libre « emplacement événement » d'un exemplaire. Vide = retire."""
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        valeur = " ".join(emplacement_texte.split()) or None
        services.affecter_emplacement(conn, id_exemplaire, "evenement", valeur)
    finally:
        conn.close()
    return RedirectResponse(_retour_emplacement(reference_titre, retour), status_code=303)


@router.post("/jeu/{reference_titre}/exemplaire/{id_exemplaire}/emplacement-local")
def exemplaire_emplacement_local(
    request: Request, reference_titre: str, id_exemplaire: str,
    emplacement_id: str = Form(""), retour: str = Form(""),
):
    """
    Édite l'emplacement LOCAL (menu déroulant) d'un exemplaire. Option
    « — aucun — » (valeur vide) retire l'emplacement. Un id invalide/inconnu
    (manipulation directe du formulaire — le <select> ne propose que des ids
    réels, actifs ou l'archivé courant) est ignoré silencieusement, jamais
    bloquant.
    """
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        brut = emplacement_id.strip()
        valeur = None
        id_valide = True
        if brut:
            try:
                valeur = int(brut)
            except ValueError:
                id_valide = False
            else:
                if services.get_emplacement_rangement(conn, valeur) is None:
                    id_valide = False
        if id_valide:
            services.affecter_emplacement(conn, id_exemplaire, "local", valeur)
    finally:
        conn.close()
    return RedirectResponse(_retour_emplacement(reference_titre, retour), status_code=303)


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
def _page_donnees(
    request: Request, message: tuple | None = None, status_code: int = 200,
    manques: int | None = None,
):
    """
    Rend la page « Données & sauvegarde » avec les compteurs actuels.

    Page UNIQUE regroupant le catalogue (import/export CSV/Excel) et la
    sauvegarde complète des 3 bases (export/import zip) — anciennement deux
    sous-menus séparés, fusionnés pour plus de cohérence côté admin.

    `manques` (rangement, §4.b/§4.d) : nombre de boîtes sans emplacement dans
    le contexte actif après un import réussi ; None sinon. Passé SÉPARÉMENT
    du `message` (texte libre échappé, potentiellement issu du CSV importé —
    ex. un nom d'emplacement créé à la volée) pour que le lien vers la page
    des manques reste un vrai lien HTML construit côté serveur, jamais une
    valeur utilisateur injectée non échappée dans le gabarit.
    """
    conn = get_connection()
    try:
        nb_titres = conn.execute("SELECT COUNT(*) FROM titres").fetchone()[0]
        nb_ex = conn.execute("SELECT COUNT(*) FROM exemplaires").fetchone()[0]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_donnees.html",
        {"nb_titres": nb_titres, "nb_ex": nb_ex, "message": message, "manques": manques},
        status_code=status_code,
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
    manques = None
    try:
        res = import_csv.importer(chemin)
        texte = (f"Import réussi : {res['exemplaires']} exemplaire(s) / "
                 f"{res['titres']} titre(s).")
        if res["ignores"]:
            texte += f" {len(res['ignores'])} ligne(s) ignorée(s)."
        # Rangement (§4.b) : un nom d'emplacement local inconnu dans la
        # colonne CSV est créé à la volée, tolérant mais JAMAIS silencieux.
        crees = res.get("emplacements_locaux_crees") or []
        if crees:
            texte += (f" {len(crees)} nouvel(aux) emplacement(s) local(-aux) "
                      f"créé(s) : {', '.join(crees)}.")
        message = ("succes", texte)
        # §4.d : lien direct vers la page des manques s'il en reste.
        conn = get_connection()
        try:
            manques = services.compter_exemplaires_sans_emplacement(conn)
        finally:
            conn.close()
    except SystemExit as exc:            # colonnes clés absentes
        message = ("erreur", str(exc))
    except Exception as exc:             # tout autre souci de lecture
        message = ("erreur", f"Import impossible : {exc}")
    finally:
        try:
            chemin.unlink()
        except OSError:
            pass
    return _page_donnees(request, message, manques=manques)


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
# Sauvegarde & restauration complète (3 bases : prêt, tournois, planning)
#
# Page fusionnée avec le catalogue (voir _page_donnees / admin_donnees.html) :
# pas de page dédiée ici, seulement les actions (téléchargement / import).
# ---------------------------------------------------------------------------
@router.get("/sauvegarde/export")
def sauvegarde_export(request: Request):
    """Télécharge une sauvegarde complète (zip des 3 bases + INFO.txt)."""
    if (garde := _garde(request)):
        return garde
    contenu = sauvegarde.creer_zip_sauvegarde()
    nom = sauvegarde.nom_fichier_zip()
    return Response(
        content=contenu, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


@router.post("/sauvegarde/import")
def sauvegarde_import(request: Request, fichier: UploadFile = File(...)):
    """
    Restaure les 3 bases depuis un zip de sauvegarde téléversé.

    Validation stricte (présence des 3 bases + intégrité SQLite) avant toute
    modification ; un filet de sécurité de l'état actuel est conservé
    automatiquement (voir `app.sauvegarde.sauvegarde_de_securite`). Jamais
    d'erreur brute : toujours réaffiché avec un message clair.
    """
    if (garde := _garde(request)):
        return garde

    import tempfile
    from pathlib import Path

    contenu = fichier.file.read()
    if not contenu:
        return _page_donnees(request, ("erreur", "Fichier vide ou absent."), status_code=400)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(contenu)
        chemin = Path(tmp.name)
    try:
        sauvegarde.restaurer_zip_sauvegarde(chemin)
        message = (
            "succes",
            "Restauration réussie : les 3 bases ont été remplacées par le "
            "contenu de la sauvegarde. L'état précédent a été conservé dans "
            "data/sauvegardes/ au cas où.",
        )
        status_code = 200
    except sauvegarde.ZipInvalide as exc:
        message = ("erreur", str(exc))
        status_code = 400
    finally:
        try:
            chemin.unlink()
        except OSError:
            pass

    return _page_donnees(request, message, status_code=status_code)


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
        message = f"Accès bénévole — {NOM_ASSOCIATION} : {lien}"
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


# ---------------------------------------------------------------------------
# Supervision légère (lecture seule) : bases, disque, sauvegarde, jeton, version
# ---------------------------------------------------------------------------
@router.get("/supervision")
def supervision_page(request: Request):
    """
    Page de supervision en LECTURE SEULE : permet à un bureau non technicien de
    vérifier en quelques secondes, le jour de l'événement, que tout va bien
    (3 bases présentes, espace disque, dernière sauvegarde, jeton bénévole,
    version déployée). Aucune action d'écriture sur cette page.
    """
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        etat = supervision.etat_supervision(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "admin_supervision.html", {"etat": etat})


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
    return _rendre_dashboard(request, message)


# ---------------------------------------------------------------------------
# Mode formation : réinitialisation des données de démonstration
# ---------------------------------------------------------------------------
@router.post("/formation/reinitialiser")
def formation_reinitialiser(request: Request):
    """
    Vide puis repeuple les bases de l'instance (jeux fictifs, prêts, tournoi
    d'exemple — voir `app.formation`). Bouton visible UNIQUEMENT si
    MODE_FORMATION=1 (voir `admin_dashboard.html`) ; on revérifie ici côté
    serveur pour ne jamais exécuter cette action sur une instance de
    production même en cas d'appel direct de la route.

    Sûr par construction : l'instance de formation ne connaît que ses propres
    bases jetables (aucun routage dynamique de connexion dans le code).
    """
    if (garde := _garde(request)):
        return garde
    if not MODE_FORMATION:
        return Response(status_code=404)
    resume = formation.peupler()
    message = (
        "succes",
        f"Données de formation réinitialisées : {resume['jeux']} jeux fictifs, "
        f"{resume['tournois']} tournoi d'exemple.",
    )
    return _rendre_dashboard(request, message)


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


# ---------------------------------------------------------------------------
# Fonctionnalités : activer / désactiver les modules
# ---------------------------------------------------------------------------
def _page_fonctionnalites(request: Request, message: tuple | None = None):
    """Rend la page de gestion des fonctionnalités (réutilisée par GET et POST)."""
    from app.modules import (
        DESCRIPTIONS_ETATS, ETATS_VALIDES, LABELS_ETATS, MODULES,
        lire_etats_modules,
    )

    conn = get_connection()
    try:
        etats = lire_etats_modules(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_fonctionnalites.html",
        {
            "modules": MODULES,
            "etats": etats,
            "etats_valides": ETATS_VALIDES,
            "labels_etats": LABELS_ETATS,
            "descriptions_etats": DESCRIPTIONS_ETATS,
            "message": message,
        },
    )


@router.get("/fonctionnalites")
def fonctionnalites_formulaire(request: Request):
    """Affiche la page de gestion de la visibilité des modules."""
    if (garde := _garde(request)):
        return garde
    message = None
    if request.query_params.get("ok") == "1":
        message = ("succes", "Réglages enregistrés.")
    return _page_fonctionnalites(request, message)


@router.post("/fonctionnalites")
async def fonctionnalites_enregistrer(request: Request):
    """
    Enregistre l'état de chaque module soumis dans le formulaire,
    puis redirige vers le GET (pattern POST-Redirect-GET).
    """
    if (garde := _garde(request)):
        return garde
    from app.modules import ETATS_VALIDES, MODULES, ecrire_etat_module

    form = await request.form()
    conn = get_connection()
    try:
        for nom in MODULES:
            etat = str(form.get(f"module_{nom}", ""))
            if etat in ETATS_VALIDES:
                ecrire_etat_module(conn, nom, etat)
    finally:
        conn.close()
    return RedirectResponse("/admin/fonctionnalites?ok=1", status_code=303)


# ---------------------------------------------------------------------------
# Rangement des boîtes : contexte actif, visibilité publique, liste des
# emplacements locaux (voir docs/conception-rangement.md §8). Toutes les
# actions structurantes (créer/renommer/archiver/réactiver/supprimer/
# réordonner) suivent le motif POST-Redirect-GET déjà utilisé par le planning
# bénévole (`app/planning/routes.py::_retour`) : un message texte simple
# transite en query string (`?msg=...`), toujours affiché en confirmation
# (pas de distinction succès/erreur — les actions invalides sont refusées
# silencieusement côté service, jamais bloquant).
# ---------------------------------------------------------------------------
def _page_rangement(request: Request, message: str | None = None):
    conn = get_connection()
    try:
        contexte = services.rangement_contexte(conn)
        visibilite = services.rangement_visibilite(conn)
        emplacements = services.lister_emplacements_rangement(conn)
        # Compteur affiché en tête d'écran + lien vers la page des manques (§4.d).
        nb_manques = services.compter_exemplaires_sans_emplacement(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "admin_rangement.html",
        {
            "contexte": contexte,
            "visibilite": visibilite,
            "emplacements": emplacements,
            "contextes": services.RANGEMENT_CONTEXTES,
            "visibilites": services.RANGEMENT_VISIBILITES,
            "message": message,
            "nb_manques": nb_manques,
        },
    )


@router.get("/rangement")
def rangement_page(request: Request):
    """Écran dédié : contexte actif, visibilité publique, liste des emplacements locaux."""
    if (garde := _garde(request)):
        return garde
    return _page_rangement(request, request.query_params.get("msg"))


@router.post("/rangement/contexte")
def rangement_contexte_enregistrer(request: Request, contexte: str = Form("")):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        if contexte in services.RANGEMENT_CONTEXTES:
            services.ecrire_rangement_contexte(conn, contexte)
    finally:
        conn.close()
    return RedirectResponse(
        "/admin/rangement?msg=" + quote("Contexte de rangement enregistré."), status_code=303
    )


@router.post("/rangement/visibilite")
def rangement_visibilite_enregistrer(request: Request, visibilite: str = Form("")):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        if visibilite in services.RANGEMENT_VISIBILITES:
            services.ecrire_rangement_visibilite(conn, visibilite)
    finally:
        conn.close()
    return RedirectResponse(
        "/admin/rangement?msg=" + quote("Visibilité publique enregistrée."), status_code=303
    )


@router.post("/rangement/emplacements")
def rangement_emplacement_creer(request: Request, nom: str = Form("")):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        resultat = services.obtenir_ou_creer_emplacement_rangement(conn, nom)
    finally:
        conn.close()
    if resultat is None:
        msg = "Nom manquant : rien n'a été ajouté."
    else:
        _id, cree = resultat
        msg = "Emplacement ajouté." if cree else "Cet emplacement existait déjà — rien de dupliqué."
    return RedirectResponse("/admin/rangement?msg=" + quote(msg), status_code=303)


@router.post("/rangement/emplacements/{id_emplacement:int}/renommer")
def rangement_emplacement_renommer(request: Request, id_emplacement: int, nom: str = Form("")):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        ok = services.renommer_emplacement_rangement(conn, id_emplacement, nom)
    finally:
        conn.close()
    msg = "Emplacement renommé." if ok else "Nom manquant : rien n'a été modifié."
    return RedirectResponse("/admin/rangement?msg=" + quote(msg), status_code=303)


@router.post("/rangement/emplacements/{id_emplacement:int}/archiver")
def rangement_emplacement_archiver(request: Request, id_emplacement: int):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        services.archiver_emplacement_rangement(conn, id_emplacement)
    finally:
        conn.close()
    msg = "Emplacement archivé — il n'apparaît plus dans les menus de saisie."
    return RedirectResponse("/admin/rangement?msg=" + quote(msg), status_code=303)


@router.post("/rangement/emplacements/{id_emplacement:int}/reactiver")
def rangement_emplacement_reactiver(request: Request, id_emplacement: int):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        services.reactiver_emplacement_rangement(conn, id_emplacement)
    finally:
        conn.close()
    return RedirectResponse(
        "/admin/rangement?msg=" + quote("Emplacement réactivé."), status_code=303
    )


@router.post("/rangement/emplacements/{id_emplacement:int}/supprimer")
def rangement_emplacement_supprimer(request: Request, id_emplacement: int):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        ok = services.supprimer_emplacement_rangement(conn, id_emplacement)
    finally:
        conn.close()
    msg = (
        "Emplacement supprimé définitivement." if ok else
        "Suppression refusée : des boîtes pointent encore vers cet emplacement."
    )
    return RedirectResponse("/admin/rangement?msg=" + quote(msg), status_code=303)


@router.post("/rangement/emplacements/{id_emplacement:int}/monter")
def rangement_emplacement_monter(request: Request, id_emplacement: int):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        services.deplacer_emplacement_rangement(conn, id_emplacement, "haut")
    finally:
        conn.close()
    return RedirectResponse("/admin/rangement", status_code=303)


@router.post("/rangement/emplacements/{id_emplacement:int}/descendre")
def rangement_emplacement_descendre(request: Request, id_emplacement: int):
    if (garde := _garde(request)):
        return garde
    conn = get_connection()
    try:
        services.deplacer_emplacement_rangement(conn, id_emplacement, "bas")
    finally:
        conn.close()
    return RedirectResponse("/admin/rangement", status_code=303)


# ---------------------------------------------------------------------------
# Page des manques (§4.d) : exemplaires SANS emplacement dans le contexte
# actif — pendant ciblé du mode rangement de masse au scanner. Filtrable
# (nom/catégorie), paginée sobrement (~700 boîtes possibles). La saisie
# rapide en ligne RÉUTILISE les routes d'édition à l'unité de la fiche admin
# (exemplaire_emplacement_evenement/local ci-dessus, via leur paramètre
# `retour`) — un seul point de maintenance pour l'écriture, pas de route
# dupliquée.
# ---------------------------------------------------------------------------
def _url_manques(categorie: str | None, q: str | None, page: int) -> str:
    """URL de /admin/rangement/manques avec filtres + page (query string minimale)."""
    from urllib.parse import urlencode

    params = {k: v for k, v in {"categorie": categorie, "q": q, "page": page}.items()
              if v not in (None, "", 1)}
    requete = urlencode(params)
    return "/admin/rangement/manques" + (f"?{requete}" if requete else "")


@router.get("/rangement/manques")
def rangement_manques(
    request: Request, categorie: str | None = None, q: str | None = None, page: int = 1,
):
    if (garde := _garde(request)):
        return garde
    q = (q or "").strip() or None
    page = max(page, 1)
    conn = get_connection()
    try:
        categories = services.lister_categories(conn)
        filtre_cat = categorie if categorie in categories else None
        total = services.compter_exemplaires_sans_emplacement(conn, filtre_cat, q)
        contexte = services.rangement_contexte(conn)
        emplacements_locaux = services.emplacements_actifs(conn) if contexte == "local" else []
        exemplaires = services.exemplaires_sans_emplacement(conn, filtre_cat, q, page)
    finally:
        conn.close()
    nb_pages = max(-(-total // services.PAR_PAGE_MANQUES), 1)  # division entière arrondie au sup.
    return templates.TemplateResponse(
        request, "admin_rangement_manques.html",
        {
            "exemplaires": exemplaires, "total": total, "categories": categories,
            "categorie": filtre_cat, "q": q, "page": page, "nb_pages": nb_pages,
            "contexte": contexte, "emplacements_locaux": emplacements_locaux,
            "url_precedente": _url_manques(filtre_cat, q, page - 1) if page > 1 else None,
            "url_suivante": _url_manques(filtre_cat, q, page + 1) if page < nb_pages else None,
        },
    )
