"""
LES CATÉGORIES DE JEU À L'ÉCRAN (série « categories », lot 2).

Ce que ce fichier vérifie, et pourquoi :

1. **Les règles pures** (`app/categories_jeu.py`) : lecture d'un filtre reçu
   dans l'adresse (découpage, clé, inconnues ignorées, doublons, borne),
   ordre alphabétique des cases avec accents, composition de la liste d'un
   titre depuis l'éditeur SANS réordonner celles qu'il porte.
2. **Le catalogue public** : filtre en ET par la clé, anciennes adresses
   compatibles, une puce par catégorie, liste vide qui offre « Retirer les
   catégories », résumé « Catégories (n) » ouvert seulement si coché.
3. **La fiche publique** : un lien par catégorie, règle « A4 » (jeu tout
   sorti → jeux disponibles), nom échappé, nom accessible complet.
4. **L'éditeur et « Créer un jeu »** : enregistrer sans rien changer
   n'écrit rien et garde l'ordre ; cocher, décocher, ajouter ; catégorie
   purgée entre-temps recréée ; saisie réaffichée après un refus ; borne du
   seul champ « Ajouter » ; le verrou de la copie après chaque chemin.
5. **Les filtres d'administration** (étiquettes, « Ranger les jeux ») : par
   appartenance ; un POST de rangement en portée « filtre » avec une
   catégorie qui n'existe plus est REFUSÉ, rien n'est écrit ; le public et
   le rangement rendent les mêmes titres pour les mêmes clés.
"""

import re
from urllib.parse import parse_qsl, urlsplit

import pytest

from app import categories_jeu, services
from app.categories_jeu import (FILTRE_CATEGORIES_MAX, cases_a_cocher, cle,
                                cles_du_filtre, composer)
from tests.test_categories_jeu import (_conn, _titre, _verrou, bases,  # noqa: F401
                                       client)


# ===========================================================================
# Outils
# ===========================================================================
def _creer(*jeux):
    """Crée des jeux (nom, case de catégories), un exemplaire chacun."""
    conn = _conn()
    try:
        for nom, case in jeux:
            services.creer_jeu(conn, nom, categorie=case)
    finally:
        conn.close()


def _id(ref):
    """L'identifiant (généré) du premier exemplaire d'un titre."""
    conn = _conn()
    try:
        return conn.execute("SELECT MIN(id_exemplaire) FROM exemplaires "
                            "WHERE reference_titre = ?", (ref,)).fetchone()[0]
    finally:
        conn.close()


def _noms_catalogue(page):
    return re.findall(r'<span class="jeu-nom">([^<]*)</span>', page)


def _bloc_categories(page):
    return re.search(r'<details class="recherche bloc-categories"[^>]*>.*?</details>',
                     page, re.S).group(0)


def _cochees(page):
    return re.findall(r'<input type="checkbox" name="categorie" value="([^"]*)" checked>',
                      page)


def _puces(page):
    return re.findall(r'<a class="chip-filtre" href="([^"]*)"[^>]*><span class="sr-only">'
                      r'Retirer le filtre </span>([^<]*?) <span', page)


def _params(url):
    return parse_qsl(urlsplit(url.replace("&amp;", "&")).query)


def _formulaire(ref, **changements):
    """Le formulaire de l'éditeur tel que l'écran l'envoie, cases du jeu cochées."""
    titre = _titre(ref)
    donnees = {c: "" if titre[c] is None else str(titre[c]) for c in services.CHAMPS_FICHE}
    donnees["categorie"] = categories_jeu.decouper(titre["categorie"])
    donnees["categorie_ajout"] = ""
    donnees.update(changements)
    return donnees


# ===========================================================================
# 1. LES RÈGLES PURES
# ===========================================================================
CONNUES = {"cartes": "Cartes", "ambiance": "Ambiance", "strategie": "Stratégie"}


@pytest.mark.parametrize("valeurs, attendu", [
    (["cartes"], ["cartes"]),
    (["cartes", "ambiance"], ["cartes", "ambiance"]),
    (["cartes", "ambiance", "strategie"], ["cartes", "ambiance", "strategie"]),
    (["inconnue", "cartes"], ["cartes"]),                 # inconnue ignorée
    (["cartes", "CARTES", "cartes"], ["cartes"]),         # doublons
    (["Cartes, Ambiance"], ["cartes", "ambiance"]),       # ancienne adresse
    (["STRATÉGIE"], ["strategie"]),
    (["Stratégie"], ["strategie"]),
    (["", " , "], []),
    ([], []),
])
def test_cles_du_filtre(valeurs, attendu):
    assert cles_du_filtre(valeurs, CONNUES) == attendu


def test_cles_du_filtre_est_bornee():
    connues = {f"c{i}": f"C{i}" for i in range(FILTRE_CATEGORIES_MAX + 5)}
    valeurs = [f"c{i}" for i in range(FILTRE_CATEGORIES_MAX + 5)] * 100
    assert cles_du_filtre(valeurs, connues) == [f"c{i}" for i in range(FILTRE_CATEGORIES_MAX)]


def test_cases_en_ordre_alphabetique_sans_accents():
    connues = {cle(n): n for n in ("Énigmes", "Enquête", "Adresse", "Ambiance")}
    assert [c["nom"] for c in cases_a_cocher(connues, [])] == [
        "Adresse", "Ambiance", "Énigmes", "Enquête"]


def test_cases_gardent_une_cochee_inconnue():
    cases = cases_a_cocher({"cartes": "Cartes"}, ["Zèbre", "CARTES"])
    assert cases == [{"cle": "cartes", "nom": "Cartes", "coche": True},
                     {"cle": "zebre", "nom": "Zèbre", "coche": True}]


@pytest.mark.parametrize("actuelles, cochees, ajout, attendu", [
    # Rien ne change : l'ordre du jeu, pas celui de la page.
    (["Cartes", "Ambiance"], ["Ambiance", "Cartes"], "", "Cartes, Ambiance"),
    # Décocher.
    (["Cartes", "Ambiance"], ["Ambiance"], "", "Ambiance"),
    # Cocher : la nouvelle vient après celles du jeu, dans l'ordre de la page.
    (["Cartes", "Ambiance"], ["Ambiance", "Bluff", "Cartes", "Dés"], "",
     "Cartes, Ambiance, Bluff, Dés"),
    # Ajouter, découpé à la virgule, après les cases ; doublon ignoré.
    (["Cartes"], ["Cartes"], " Plis, bluff ,, cartes", "Cartes, Plis, bluff"),
    # Une ancienne saisie arrivée en une seule valeur vaut deux cases.
    ([], ["Cartes, Ambiance"], "", "Cartes, Ambiance"),
    (["Cartes"], [], "", None),
    ([], [], "", None),
])
def test_composer(actuelles, cochees, ajout, attendu):
    assert composer(actuelles, cochees, ajout) == attendu


# ===========================================================================
# 2. LE CATALOGUE PUBLIC
# ===========================================================================
@pytest.fixture
def parc(bases):
    _creer(("Dixit", "Cartes, Imagination"),
           ("Dobble", "Ambiance, Cartes"),
           ("Skull", "Bluff, Ambiance, Cartes"),
           ("Catane", "Stratégie"),
           ("Escape", "Énigmes"))
    return bases


def test_vocabulaire_en_ordre_de_cle(parc):
    conn = _conn()
    try:
        noms = [c["nom"] for c in services.lister_categories_jeu(conn)]
    finally:
        conn.close()
    assert noms == ["Ambiance", "Bluff", "Cartes", "Énigmes", "Imagination", "Stratégie"]


@pytest.mark.parametrize("cles, attendu", [
    (["cartes"], ["Dixit", "Dobble", "Skull"]),
    (["cartes", "ambiance"], ["Dobble", "Skull"]),
    (["cartes", "ambiance", "bluff"], ["Skull"]),
    (["cartes", "strategie"], []),
    (["inconnue"], []),                     # au service : une clé inconnue ne trouve rien
])
def test_lister_catalogue_filtre_en_et(parc, cles, attendu):
    conn = _conn()
    try:
        noms = [j["nom"] for j in services.lister_catalogue(conn, cles)]
    finally:
        conn.close()
    assert noms == attendu


def test_catalogue_filtre_en_et_par_la_route(client, parc):
    r = client.get("/catalogue", params=[("categorie", "cartes")])
    assert _noms_catalogue(r.text) == ["Dixit", "Dobble", "Skull"]
    r = client.get("/catalogue", params=[("categorie", "cartes"), ("categorie", "ambiance")])
    assert _noms_catalogue(r.text) == ["Dobble", "Skull"]
    r = client.get("/catalogue", params=[("categorie", "cartes"), ("categorie", "ambiance"),
                                         ("categorie", "bluff")])
    assert _noms_catalogue(r.text) == ["Skull"]


def test_catalogue_cle_inconnue_ignoree_et_doublons(client, parc):
    r = client.get("/catalogue", params=[("categorie", "inconnue"), ("categorie", "cartes"),
                                         ("categorie", "CARTES"), ("categorie", "cartes")])
    assert r.status_code == 200
    assert _noms_catalogue(r.text) == ["Dixit", "Dobble", "Skull"]
    assert [libelle for _, libelle in _puces(r.text)] == ["Cartes"]
    r = client.get("/catalogue", params={"categorie": "<b>x</b>"})
    assert r.status_code == 200 and len(_noms_catalogue(r.text)) == 5


def test_catalogue_borne_le_nombre_de_categories(client, bases):
    # « Tout » porte C0…C9 ; C10 n'est portée que par « Autre ». Bornée, la
    # requête ne prend que les dix premières et trouve « Tout » ; sans borne,
    # le ET sur onze catégories ne trouverait rien.
    _creer(("Tout", ", ".join(f"C{i}" for i in range(FILTRE_CATEGORIES_MAX))),
           ("Autre", f"C{FILTRE_CATEGORIES_MAX}"))
    valeurs = [("categorie", f"c{i}") for i in range(FILTRE_CATEGORIES_MAX + 1)] * 50
    r = client.get("/catalogue", params=valeurs)
    assert r.status_code == 200
    assert _noms_catalogue(r.text) == ["Tout"]
    assert len(_puces(r.text)) == FILTRE_CATEGORIES_MAX


def test_anciennes_adresses_compatibles(client, parc):
    ancienne = client.get("/catalogue", params={"categorie": "Cartes, Ambiance"}).text
    nouvelle = client.get("/catalogue", params=[("categorie", "cartes"),
                                                ("categorie", "ambiance")]).text
    assert _noms_catalogue(ancienne) == _noms_catalogue(nouvelle) == ["Dobble", "Skull"]
    assert _cochees(ancienne) == _cochees(nouvelle) == ["ambiance", "cartes"]
    r = client.get("/catalogue", params={"categorie": "STRATÉGIE"})
    assert _noms_catalogue(r.text) == ["Catane"]
    assert _cochees(r.text) == [cle("Stratégie")]


def test_une_puce_par_categorie_qui_retire_la_sienne(client, parc):
    r = client.get("/catalogue", params=[("q", "o"), ("categorie", "cartes"),
                                         ("categorie", "ambiance"), ("dispo", "1")])
    puces = {libelle: _params(url) for url, libelle in _puces(r.text)}
    assert puces["Cartes"] == [("q", "o"), ("categorie", "ambiance"), ("dispo", "1")]
    assert puces["Ambiance"] == [("q", "o"), ("categorie", "cartes"), ("dispo", "1")]
    assert puces["disponibles seulement"] == [("q", "o"), ("categorie", "cartes"),
                                              ("categorie", "ambiance")]
    assert "Réinitialiser" in r.text


def test_aucun_resultat_propose_de_retirer_les_categories(client, parc):
    r = client.get("/catalogue", params=[("categorie", "cartes"), ("categorie", "strategie")])
    assert _noms_catalogue(r.text) == []
    assert "Aucun jeu ne porte toutes ces catégories." in r.text
    assert "Aucun jeu ne correspond à ces critères." not in r.text
    assert '<a class="lien" href="/catalogue">Retirer les catégories</a>' in r.text

    # Avec d'autres filtres : le lien les garde tous ; on n'accuse pas les
    # seules catégories.
    r = client.get("/catalogue", params=[("q", "zzz"), ("categorie", "cartes"),
                                         ("age", "8"), ("dispo", "1")])
    assert "Aucun jeu ne correspond à ces critères." in r.text
    lien = re.search(r'<a class="lien" href="([^"]*)">Retirer les catégories</a>', r.text)
    assert _params(lien.group(1)) == [("q", "zzz"), ("age", "8"), ("dispo", "1")]


def test_aucun_resultat_sans_categorie_message_inchange(client, parc):
    r = client.get("/catalogue", params={"q": "zzz"})
    assert "Aucun jeu ne correspond à ces critères." in r.text
    assert "Retirer les catégories" not in r.text


def test_resume_et_ouverture_du_bloc(client, parc):
    bloc = _bloc_categories(client.get("/catalogue").text)
    assert bloc.startswith('<details class="recherche bloc-categories">')
    assert "<summary>Catégories</summary>" in bloc
    assert "onchange" not in bloc and "<script" not in bloc
    bloc = _bloc_categories(client.get("/catalogue", params=[
        ("categorie", "cartes"), ("categorie", "ambiance")]).text)
    assert bloc.startswith('<details class="recherche bloc-categories" open>')
    assert re.search(r'<summary>Catégories \(2<span class="sr-only"> cochées</span>\)</summary>',
                     bloc)
    assert "<legend" in bloc
    bloc = _bloc_categories(client.get("/catalogue", params={"categorie": "cartes"}).text)
    assert '(1<span class="sr-only"> cochée</span>)' in bloc


def test_cases_du_catalogue_envoient_la_cle(client, parc):
    bloc = _bloc_categories(client.get("/catalogue").text)
    cases = re.findall(r'<label class="case case-categorie"><input type="checkbox" '
                       r'name="categorie" value="([^"]*)"> ([^<]*)</label>', bloc)
    assert cases == [(cle(n), n) for n in ("Ambiance", "Bluff", "Cartes", "Énigmes",
                                            "Imagination", "Stratégie")]


def test_catalogue_sans_categorie_aucun_bloc(client, bases):
    _creer(("Sans", None))
    assert "bloc-categories" not in client.get("/catalogue").text


# ===========================================================================
# 3. LA FICHE PUBLIQUE
# ===========================================================================
def _liens_fiche(page):
    paragraphe = re.search(r'<p class="lien-fiche">.*?</p>', page, re.S).group(0)
    return re.findall(r'<a class="lien" href="(/catalogue\?[^"]*)"><span class="sr-only">'
                      r'([^<]*) </span>([^<]*)</a>', paragraphe), paragraphe


def test_fiche_un_lien_par_categorie(client, parc):
    liens, paragraphe = _liens_fiche(client.get(f"/jeu/{_id('SKULL')}").text)
    assert [(_params(u), debut, nom) for u, debut, nom in liens] == [
        ([("categorie", "bluff")], "Voir les autres jeux", "Bluff"),
        ([("categorie", "ambiance")], "Voir les autres jeux", "Ambiance"),
        ([("categorie", "cartes")], "Voir les autres jeux", "Cartes"),
    ]
    assert '<span aria-hidden="true">Voir les autres jeux :</span>' in paragraphe


def test_fiche_tout_sorti_vers_les_disponibles(client, parc):
    conn = _conn()
    try:
        services.preter(conn, _id("SKULL"))
    finally:
        conn.close()
    liens, paragraphe = _liens_fiche(client.get(f"/jeu/{_id('SKULL')}").text)
    assert [(_params(u), debut) for u, debut, _ in liens] == [
        ([("categorie", k), ("dispo", "1")], "Voir les jeux disponibles")
        for k in ("bluff", "ambiance", "cartes")]
    assert "Voir les autres jeux" not in paragraphe


def test_fiche_nom_echappe(client, bases):
    _creer(("Piège", "<b>x</b>"))
    page = client.get(f"/jeu/{_id('PIEGE')}").text
    assert "<b>x</b>" not in page
    liens, _ = _liens_fiche(page)
    assert [nom for _, _, nom in liens] == ["&lt;b&gt;x&lt;/b&gt;"]


def test_fiche_sans_categorie_sans_lien(client, bases):
    _creer(("Sans", None))
    liens, paragraphe = _liens_fiche(client.get(f"/jeu/{_id('SANS')}").text)
    assert liens == [] and "Voir les" not in paragraphe


# ===========================================================================
# 4. L'ÉDITEUR ET « CRÉER UN JEU »
# ===========================================================================
@pytest.fixture
def dixit(bases):
    _creer(("Dixit", "Cartes, Ambiance"), ("Skull", "Bluff"))
    return bases


def _etat_titre(ref):
    conn = _conn()
    try:
        ligne = conn.execute("SELECT categorie, modifie_le FROM titres "
                             "WHERE reference_titre = ?", (ref,)).fetchone()
        return tuple(ligne)
    finally:
        conn.close()


def test_editeur_coche_les_categories_du_jeu(client, dixit):
    page = client.get("/admin/jeu/DIXIT").text
    bloc = _bloc_categories(page)
    assert bloc.startswith('<details class="recherche bloc-categories" open>')
    assert _cochees(bloc) == ["Ambiance", "Cartes"]          # ordre de la page
    assert re.search(r'name="categorie" value="Bluff">', bloc)
    assert '<label for="categorie_ajout">Ajouter une catégorie</label>' in page
    assert 'aria-describedby="categorie_ajout-aide"' in page
    assert "<datalist" not in page


def test_editeur_enregistrer_sans_rien_changer_n_ecrit_rien(client, dixit):
    avant = _etat_titre("DIXIT")
    # Les cases arrivent dans l'ordre de la page (alphabétique) : Ambiance,
    # puis Cartes. La liste du jeu, elle, est « Cartes, Ambiance ».
    donnees = _formulaire("DIXIT", categorie=["Ambiance", "Cartes"])
    r = client.post("/admin/jeu/DIXIT/modifier", data=donnees, follow_redirects=False)
    assert r.status_code == 303 and "ok=inchange" in r.headers["location"]
    assert _etat_titre("DIXIT") == avant == ("Cartes, Ambiance", None)
    assert _verrou()["DIXIT"] == "Cartes, Ambiance"


def test_editeur_sans_changement_au_service_total_changes(dixit):
    conn = _conn()
    try:
        titre = services.get_titre(conn, "DIXIT")
        saisie = {c: "" if titre[c] is None else str(titre[c]) for c in services.CHAMPS_FICHE}
        saisie["categorie"] = composer(categories_jeu.decouper(titre["categorie"]),
                                       ["Ambiance", "Cartes"], "") or ""
        valeurs, erreurs = services.lire_saisie_fiche(saisie, titre["type_jeu"])
        assert not erreurs
        avant = conn.total_changes
        resultat = services.modifier_titre(conn, "DIXIT", valeurs)
        assert resultat["changes"] == [] and conn.total_changes == avant
    finally:
        conn.close()


def test_editeur_decocher_cocher_ajouter(client, dixit):
    r = client.post("/admin/jeu/DIXIT/modifier", follow_redirects=False,
                    data=_formulaire("DIXIT", categorie=["Ambiance", "Bluff"],
                                     categorie_ajout="Plis, imagination ,"))
    assert r.status_code == 303 and "ok=enregistre" in r.headers["location"]
    categorie, modifie_le = _etat_titre("DIXIT")
    assert categorie == "Ambiance, Bluff, Plis, imagination" and modifie_le
    assert _verrou()["DIXIT"] == categorie
    # « Cartes », décochée et portée par aucun autre jeu, est purgée.
    assert "cartes" not in services.lire_vocabulaire_categories(_conn())


def test_editeur_ajouter_une_categorie_deja_connue_la_rapproche(client, dixit):
    client.post("/admin/jeu/DIXIT/modifier", follow_redirects=False,
                data=_formulaire("DIXIT", categorie_ajout="BLUFF"))
    assert _etat_titre("DIXIT")[0] == "Cartes, Ambiance, Bluff"
    _verrou()


def test_editeur_coche_une_categorie_purgee_entre_temps(client, dixit):
    # La page de Dixit a été ouverte quand « Bluff » existait (portée par
    # Skull). Un import la retire de Skull : elle est purgée. Le bureau
    # enregistre Dixit avec « Bluff » coché : elle est recréée de son nom.
    page = client.get("/admin/jeu/DIXIT").text
    assert 'value="Bluff"' in page
    conn = _conn()
    try:
        services.ecrire_categories_titre(conn, "SKULL", None)
        conn.commit()
        assert "bluff" not in services.lire_vocabulaire_categories(conn)
    finally:
        conn.close()
    r = client.post("/admin/jeu/DIXIT/modifier", follow_redirects=False,
                    data=_formulaire("DIXIT", categorie=["Ambiance", "Bluff", "Cartes"]))
    assert r.status_code == 303
    assert _etat_titre("DIXIT")[0] == "Cartes, Ambiance, Bluff"
    assert _verrou()["DIXIT"] == "Cartes, Ambiance, Bluff"


def test_editeur_refus_reaffiche_cases_et_ajout(client, dixit):
    # « Bluff » cochée puis purgée entre-temps : la réaffichage la garde.
    conn = _conn()
    try:
        services.ecrire_categories_titre(conn, "SKULL", None)
        conn.commit()
    finally:
        conn.close()
    avant = _etat_titre("DIXIT")
    r = client.post("/admin/jeu/DIXIT/modifier", follow_redirects=False,
                    data=_formulaire("DIXIT", nom=" ", categorie=["Bluff", "Cartes"],
                                     categorie_ajout="Plis, Dés"))
    assert r.status_code == 400
    assert _cochees(_bloc_categories(r.text)) == ["Bluff", "Cartes"]
    assert re.search(r'name="categorie" value="Ambiance">', r.text)   # décochée
    assert re.search(r'id="categorie_ajout"[^>]*value="Plis, Dés"', r.text, re.S)
    assert _etat_titre("DIXIT") == avant


def test_editeur_borne_le_seul_champ_ajouter(client, dixit):
    r = client.post("/admin/jeu/DIXIT/modifier", follow_redirects=False,
                    data=_formulaire("DIXIT", categorie_ajout="x" * 201))
    assert r.status_code == 400
    assert re.search(r'<p class="resultat resultat-attention" id="categorie_ajout-erreur">'
                     r'Ce texte fait 201 caractères : 200 au plus.</p>', r.text)
    assert 'aria-describedby="categorie_ajout-erreur categorie_ajout-aide"' in r.text

    # Cocher beaucoup de cases dont la jointure dépasse 200 caractères : aucun
    # refus.
    noms = [f"Catégorie numéro {i:02d}" for i in range(20)]
    _creer(("Réserve", ", ".join(noms)))
    r = client.post("/admin/jeu/DIXIT/modifier", follow_redirects=False,
                    data=_formulaire("DIXIT", categorie=noms))
    assert r.status_code == 303
    assert len(_etat_titre("DIXIT")[0]) > 200
    _verrou()


def test_creer_un_jeu_par_la_route_avec_cases_et_champ(client, dixit):
    page = client.get("/admin/jeu-nouveau").text
    assert _cochees(_bloc_categories(page)) == []
    assert 'id="categorie_ajout"' in page
    r = client.post("/admin/jeu-nouveau", follow_redirects=False,
                    data={"nom": "Codenames", "categorie": ["Ambiance", "Cartes"],
                          "categorie_ajout": "Mots, bluff"})
    assert r.status_code == 303
    assert _etat_titre("CODENAMES")[0] == "Ambiance, Cartes, Mots, Bluff"
    _verrou()


def test_creer_un_jeu_refus_reaffiche_cases_et_champ(client, dixit):
    r = client.post("/admin/jeu-nouveau",
                    data={"nom": " ", "categorie": ["Cartes"], "categorie_ajout": "Mots"})
    assert r.status_code == 400
    assert _cochees(_bloc_categories(r.text)) == ["Cartes"]
    assert re.search(r'id="categorie_ajout"[^>]*value="Mots"', r.text, re.S)


# ===========================================================================
# 5. LES FILTRES D'ADMINISTRATION
# ===========================================================================
def test_etiquettes_filtre_par_appartenance(client, parc):
    r = client.get("/admin/etiquettes", params={"categorie": "ambiance"})
    assert r.status_code == 200
    noms = re.findall(r'name="references" value="([^"]*)"', r.text)
    assert sorted(noms) == ["DOBBLE", "SKULL"]
    assert '<option value="ambiance" selected>Ambiance</option>' in r.text
    # Une graphie (ancienne valeur du menu) vaut sa clé ; une inconnue, rien.
    r = client.get("/admin/etiquettes", params={"categorie": "AMBIANCE"})
    assert sorted(re.findall(r'name="references" value="([^"]*)"', r.text)) == [
        "DOBBLE", "SKULL"]
    r = client.get("/admin/etiquettes", params={"categorie": "inconnue"})
    assert len(re.findall(r'name="references" value="([^"]*)"', r.text)) == 5


def test_service_etiquettes_par_appartenance(parc):
    conn = _conn()
    try:
        noms = [t["nom"] for t in services.titres_pour_etiquettes(conn, "cartes")]
    finally:
        conn.close()
    assert noms == ["Dixit", "Dobble", "Skull"]


def _noms_ranger(page):
    corps = re.search(r"<tbody>.*?</tbody>", page, re.S).group(0)
    return re.findall(r"<td>([^<]+)</td>\s*<td>\d+</td>", corps)


def test_ranger_filtre_par_appartenance(client, parc):
    r = client.get("/admin/rangement/ranger", params={"categorie": "ambiance", "tous": 1})
    assert r.status_code == 200
    assert _noms_ranger(r.text) == ["Dobble", "Skull"]
    assert '<option value="ambiance" selected>Ambiance</option>' in r.text
    assert '<input type="hidden" name="categorie" value="ambiance">' in r.text


@pytest.mark.parametrize("cles", [["cartes"], ["ambiance"], ["bluff"], ["strategie"]])
def test_public_et_rangement_rendent_les_memes_titres(client, parc, cles):
    public = _noms_catalogue(client.get("/catalogue", params={"categorie": cles}).text)
    rangement = _noms_ranger(client.get("/admin/rangement/ranger",
                                        params={"categorie": cles[0], "tous": 1}).text)
    assert public == rangement and public


def _emplacements():
    conn = _conn()
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT id_exemplaire, emplacement_evenement FROM exemplaires")}
    finally:
        conn.close()


def test_ranger_appliquer_filtre_inconnu_refuse(client, parc, monkeypatch):
    from app import journal
    lignes = []
    monkeypatch.setattr(journal, "journaliser",
                        lambda request, module, action, **kw: lignes.append((action, kw)))
    avant = _emplacements()
    assert set(avant.values()) == {None}
    r = client.post("/admin/rangement/ranger/appliquer", follow_redirects=False,
                    data={"portee": "filtre", "emplacement_texte": "Table du fond",
                          "categorie": "purgee-entre-temps", "tous": "1"})
    assert r.status_code == 303
    assert _emplacements() == avant                     # AUCUNE boîte touchée
    assert dict(_params(r.headers["location"]))["msg"] == "categorie_inconnue"
    assert lignes == [("rangement_lot_applique",
                       {"objet": "Table du fond", "ok": False,
                        "detail": "categorie_inconnue"})]
    page = client.get(r.headers["location"]).text
    assert "Cette catégorie n&#39;existe plus" in page


def test_ranger_appliquer_filtre_connu_applique(client, parc):
    r = client.post("/admin/rangement/ranger/appliquer", follow_redirects=False,
                    data={"portee": "filtre", "emplacement_texte": "Table du fond",
                          "categorie": "bluff", "tous": "1"})
    assert r.status_code == 303
    apres = _emplacements()
    assert [ex for ex, v in apres.items() if v] == [_id("SKULL")]   # Skull seulement


def test_ranger_appliquer_coches_ignore_la_categorie(client, parc):
    # En portée « coches », la sélection est explicite : une catégorie
    # disparue du filtre ne l'élargit pas, rien à refuser.
    r = client.post("/admin/rangement/ranger/appliquer", follow_redirects=False,
                    data={"portee": "coches", "titres_coches": ["CATANE"],
                          "emplacement_texte": "Étagère", "categorie": "purgee"})
    assert r.status_code == 303
    assert [ex for ex, v in _emplacements().items() if v] == [_id("CATANE")]
