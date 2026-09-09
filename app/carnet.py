"""
Carnet de maintenance — logique PARTAGÉE par ses deux écrans de liste et par
la fiche de la boîte (docs/conception-signalements.md §7).

POURQUOI CE MODULE (lot agora-5)
--------------------------------
Le carnet a désormais trois points d'appel dans trois modules de routes :

- `routes/pret.py`   — le bouton du bandeau de la fiche (lot agora-4) ;
- `routes/maintenance.py` — le carnet bénévole (`/maintenance`) ;
- `routes/admin.py`  — le carnet du bureau (`/admin/signalements`), qui seul
  porte les exports et le réglage des catégories.

Le lot agora-4 avait factorisé la fermeture d'un signalement DANS
`routes/pret.py`, que `routes/admin.py` importait en différé : défendable à
deux appelants, plus tenable à trois — trois modules de routes qui s'importent
l'un l'autre. Le raisonnement a donc été promu : la partie DONNÉES dans
`services.fermer_signalement`, la partie PRÉSENTATION et la décision de
JOURNALISATION ici.

Ce module n'est pas un service : il ne touche pas la base et reçoit la
`Request`. Il tient ce que la règle du §5.2 de `docs/conception-journal.md`
interdit de mettre dans `app/services.py` (journaliser depuis un service) et
ce qu'il serait absurde de recopier dans trois gabarits (les libellés d'états
et la fabrication des URL de filtres).

LE PRÉFIXE D'URL EST UN PARAMÈTRE
---------------------------------
Les deux carnets sont le même écran à deux adresses : `/maintenance` et
`/admin/signalements`. Les URL de filtres se fabriquent donc à partir du
préfixe de l'appelant — surtout pas en dur, sinon une puce de filtre du carnet
bénévole renverrait le bénévole vers une page qui lui demande le mot de passe.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request

from app import journal, services
from app.db import get_connection

# États du filtre. « ouverts » est la vue par défaut : c'est la question que
# se pose celui qui ouvre le carnet — qu'est-ce qui reste à faire.
ETATS = ("ouverts", "traites", "tous")
LIBELLES_ETAT = {
    "ouverts": "à traiter", "traites": "déjà traités", "tous": "tous",
}


def url_liste(prefixe: str, etat: str, id_categorie: int | None,
              msg: str | None = None) -> str:
    """URL du carnet conservant les filtres courants (POST-Redirect-GET)."""
    params: dict = {}
    if etat != "ouverts":  # vue par défaut : rien à porter
        params["etat"] = etat
    if id_categorie is not None:
        params["categorie"] = id_categorie
    if msg:
        params["msg"] = msg
    requete = urlencode(params)
    return prefixe + (f"?{requete}" if requete else "")


def puces_filtres(prefixe: str, etat: str, id_categorie: int | None,
                  nom_categorie: str | None) -> list[dict]:
    """Puces de filtres actifs, patron exact de `routes/catalogue.py::_puces_filtres`."""
    puces = []
    if etat != "ouverts":
        puces.append({
            "label": f"état : {LIBELLES_ETAT[etat]}",
            "url": url_liste(prefixe, "ouverts", id_categorie),
        })
    if id_categorie is not None:
        puces.append({
            "label": f"catégorie : {nom_categorie or id_categorie}",
            "url": url_liste(prefixe, etat, None),
        })
    return puces


def contexte_liste(prefixe: str, etat: str, categorie: str | None) -> dict:
    """
    Lit la liste filtrée et prépare les variables communes aux deux gabarits
    de carnet. Un seul domicile pour la requête (`services.lister_signalements`)
    ET pour la règle « filtre forgé ignoré, jamais d'erreur ».

    L'appelant complète : le `message`, et pour l'administration les deux URL
    d'export — qui restent derrière le mot de passe (§7).
    """
    from app.routes.catalogue import _entier_ou_none

    etat_n = etat if etat in ETATS else "ouverts"
    id_categorie = _entier_ou_none(categorie)

    conn = get_connection()
    try:
        # Toutes les catégories, ARCHIVÉES COMPRISES : un signalement déjà
        # saisi garde la sienne (FK sans cascade, §4), on doit donc pouvoir
        # filtrer dessus après son archivage.
        categories = services.lister_categories_signalement(conn)
        if id_categorie is not None and not any(
            c["id_categorie"] == id_categorie for c in categories
        ):
            id_categorie = None  # filtre forgé : ignoré, jamais d'erreur
        signalements = services.lister_signalements(conn, etat_n, id_categorie)
        nb_ouverts = services.compter_signalements_ouverts(conn)
    finally:
        conn.close()

    nom_categorie = next(
        (c["nom"] for c in categories if c["id_categorie"] == id_categorie), None
    )
    chips = puces_filtres(prefixe, etat_n, id_categorie, nom_categorie)
    return {
        "prefixe": prefixe,
        "signalements": signalements, "categories": categories,
        "etat": etat_n, "etats": ETATS, "libelles_etat": LIBELLES_ETAT,
        "id_categorie": id_categorie, "nb_ouverts": nb_ouverts,
        "chips": chips, "filtres_actifs": bool(chips),
    }


def journaliser_traite(request: Request, avant: dict | None) -> None:
    """
    Écrit la ligne `signalement_traite`, sauf si rien n'a réellement changé :
    `avant` à None (signalement inconnu ou d'une autre boîte, rien n'a été
    écrit par `services.fermer_signalement`) ou déjà traité (second appui,
    `traiter_signalement` étant idempotent) — une ligne l'affirmerait alors
    que ce n'est pas arrivé. Même précaution que « annonce effacée »
    (routes/admin.py).

    Appelée par les trois routes APRÈS fermeture de la connexion, comme les
    autres actions du prêt (`journaliser` ouvre la sienne pour déterminer
    `qui`).
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
