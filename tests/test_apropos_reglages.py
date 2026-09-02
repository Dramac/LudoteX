"""
Présentation, contact et URL du dépôt (`parametres.asso_presentation`,
`asso_contact`, `asso_depot_url`) : les trois réglages qui rendent la page
« À propos » indépendante d'une association donnée.

Suite du réglage du nom (tests/test_nom_association.py), dont ils reprennent le
domicile et le patron de lecture. Ce fichier vérifie ce qui les en DISTINGUE :

1. **Trois comportements différents en l'absence de valeur.** La présentation
   et le contact peuvent ne pas exister — leur section disparaît alors en
   entier, titre compris ; l'URL du dépôt, elle, ne manque jamais : la GPL veut
   qu'on puisse atteindre la source de la version qu'on fait tourner.
2. **Les deux champs qui entrent dans un attribut d'URL** (`href`, `mailto:`)
   refusent ce qui n'en est pas une, et un refus n'enregistre rien.
3. **La présentation est du TEXTE**, rendu en paragraphes, jamais du HTML.
4. **La page répond même si la base est indisponible** : c'est la seule qui
   porte la licence et le lien vers le code source.
5. Le reste : cascade, journal (une ligne seulement si la valeur change), et le
   lien « journal des versions », propre à GitHub.

Le socle du journal est testé ailleurs (tests/test_journal.py) ; on ne vérifie
ici que les lignes propres à cet écran.
"""

import json

import pytest


MOT_DE_PASSE = "secret-admin-apropos"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Trois bases temporaires + un exemplaire, patron de tests/test_routes.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _connexion(client):
    return client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _enregistrer(client, **champs):
    """
    Poste le formulaire d'identité. Les quatre champs voyagent ensemble : ne
    passer que ceux qui comptent revient à vider les autres, exactement comme
    dans un navigateur.
    """
    _connexion(client)
    donnees = {"nom_association": "", "presentation": "", "contact": "",
               "depot_url": ""}
    donnees.update(champs)
    return client.post("/admin/identite", data=donnees)


def _lignes(chemin):
    if not chemin.exists():
        return []
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _valeur(cle):
    from app import db, services

    conn = db.get_connection()
    try:
        return services.lire_parametre(conn, cle)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. RIEN DE RÉGLÉ — le comportement diffère selon la valeur, délibérément
# ---------------------------------------------------------------------------
def test_sans_presentation_la_section_association_disparait_en_entier(client):
    """
    Titre compris : règle « ne jamais afficher une valeur absente ». Un titre
    « L'association » suivi de rien vaudrait moins que pas de titre du tout.
    """
    page = client.get("/apropos")
    assert page.status_code == 200
    assert "L'association" not in page.text


def test_sans_contact_la_section_contact_disparait_en_entier(client):
    """
    Garde aussi une régression historique : le dépôt portait autrefois une
    adresse de contact en dur, affichée même sans réglage. Sans
    `asso_contact` en base, aucun mailto: ne doit apparaître, quelle que soit
    l'adresse — reformulé sur le comportement plutôt que sur l'ancienne
    valeur littérale (lot 4 de l'ouverture publique).
    """
    page = client.get("/apropos")
    assert page.status_code == 200
    assert "<h2>Contact</h2>" not in page.text
    assert "mailto:" not in page.text


def test_sans_reglage_le_lien_vers_le_code_source_reste_affiche(client):
    """
    L'URL du dépôt, elle, ne manque JAMAIS : la GPL veut qu'un utilisateur
    puisse atteindre la source de la version qu'il fait tourner. Sans réglage,
    c'est le repli d'app/config.py qui s'affiche.
    """
    from app.config import DEPOT_URL

    page = client.get("/apropos")
    assert page.status_code == 200
    assert f'href="{DEPOT_URL}"' in page.text
    assert "GPLv3" in page.text


def test_les_champs_ne_sont_jamais_preremplis_avec_leur_repli(client):
    """
    Piège hérité de /admin/ecran-salle : un champ prérempli avec sa valeur par
    défaut se fige en réglage explicite au premier enregistrement, après quoi
    plus rien ne peut le remplacer. Vaut ici pour l'URL du dépôt, le seul de ces
    trois champs à avoir un repli.
    """
    from app.config import DEPOT_URL

    _connexion(client)
    page = client.get("/admin/identite")
    assert page.status_code == 200
    assert 'name="depot_url"' in page.text
    assert f'value="{DEPOT_URL}"' not in page.text

    # Enregistrer la page sans rien saisir ne fige donc rien.
    _enregistrer(client)
    from app import services

    assert _valeur(services.CLE_ASSOCIATION_DEPOT) is None


# ---------------------------------------------------------------------------
# 2. LA CASCADE DE L'URL DU DÉPÔT — base -> .env -> littéral d'app/config.py
# ---------------------------------------------------------------------------
def test_cascade_depot_la_base_l_emporte(client, monkeypatch):
    from app import config, db, services

    monkeypatch.setattr(config, "DEPOT_URL", "https://exemple.test/repli")
    conn = db.get_connection()
    try:
        services.ecrire_parametre(
            conn, services.CLE_ASSOCIATION_DEPOT, "https://forge.test/asso/ludotex")
        assert services.lire_depot_url(conn) == "https://forge.test/asso/ludotex"
    finally:
        conn.close()
    assert 'href="https://forge.test/asso/ludotex"' in client.get("/apropos").text


def test_cascade_depot_sans_base_c_est_le_env(client, monkeypatch):
    from app import config, db, services

    monkeypatch.setattr(config, "DEPOT_URL", "https://exemple.test/repli")
    conn = db.get_connection()
    try:
        assert services.lire_depot_url(conn) == "https://exemple.test/repli"
    finally:
        conn.close()
    assert 'href="https://exemple.test/repli"' in client.get("/apropos").text


def test_cascade_depot_sans_rien_c_est_le_litteral_de_config(monkeypatch):
    """
    Ni base ni variable d'environnement : l'URL du dépôt d'origine. Ce littéral
    n'a QU'UN domicile, `app/config.py` — `app/services.py` ne le redéfinit pas.

    `load_dotenv` est neutralisé le temps du rechargement : sans cela, le test
    dépendrait du `.env` de la machine qui l'exécute.
    """
    import importlib

    import dotenv

    from app import config

    monkeypatch.delenv("DEPOT_URL", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
    try:
        importlib.reload(config)
        assert config.DEPOT_URL == "https://github.com/Dramac/LudoteX"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_une_variable_denv_vide_retombe_sur_le_litteral(monkeypatch):
    """
    `.env.example` livre `DEPOT_URL=` vide : une variable PRÉSENTE mais vide
    doit retomber sur le littéral. Le seul défaut d'`os.getenv` ne le fait pas
    — il ne s'applique qu'à une variable absente —, et l'URL vide se retrouverait
    dans un `href`.
    """
    import importlib

    import dotenv

    from app import config

    monkeypatch.setenv("DEPOT_URL", "   ")
    monkeypatch.setenv("NOM_ASSOCIATION", "")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
    try:
        importlib.reload(config)
        assert config.DEPOT_URL == "https://github.com/Dramac/LudoteX"
        assert config.NOM_ASSOCIATION == "LudoteX"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


# ---------------------------------------------------------------------------
# 3. LA PRÉSENTATION EST DU TEXTE — paragraphes, jamais de HTML
# ---------------------------------------------------------------------------
def test_trois_blocs_donnent_trois_paragraphes(client):
    _enregistrer(client, presentation="Premier bloc.\r\n\r\nDeuxième bloc.\r\n\r\n\r\nTroisième bloc.")
    page = client.get("/apropos")
    assert page.status_code == 200
    assert "L'association" in page.text
    for bloc in ("Premier bloc.", "Deuxième bloc.", "Troisième bloc."):
        assert f"<p>{bloc}</p>" in page.text


def test_le_html_saisi_est_echappe_et_non_interprete(client):
    """
    Pas de `|safe`, pas de markdown, aucune balise acceptée : ce qui est saisi
    s'affiche comme du texte. C'est ce qui permet d'offrir un champ multi-ligne
    sans ouvrir une injection depuis l'administration.
    """
    _enregistrer(client, presentation='<script>alert(1)</script> et <b>gras</b>')
    page = client.get("/apropos")
    assert "<script>alert(1)</script>" not in page.text
    assert "<b>gras</b>" not in page.text
    assert "&lt;script&gt;" in page.text
    assert "&lt;b&gt;gras&lt;/b&gt;" in page.text


def test_paragraphes_ignore_les_blocs_vides():
    from app import services

    assert services.paragraphes(None) == []
    assert services.paragraphes("   \n\n  ") == []
    assert services.paragraphes("Un seul bloc") == ["Un seul bloc"]
    assert services.paragraphes("A\n\n\n\nB") == ["A", "B"]


# ---------------------------------------------------------------------------
# 4. LES DEUX CHAMPS QUI ENTRENT DANS UN ATTRIBUT D'URL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "github.com/asso/ludotex",
    "ftp://exemple.test/depot",
])
def test_une_url_de_depot_au_mauvais_schema_est_refusee_sans_etre_enregistree(client, url):
    """
    Sans ce contrôle, un `javascript:` saisi depuis l'administration deviendrait
    un script exécuté chez chaque visiteur de la page « À propos » : Jinja
    échappe le CONTENU d'un attribut, il ne juge pas du schéma d'une URL.
    """
    from app import services

    reponse = _enregistrer(client, depot_url=url)
    assert reponse.status_code == 400
    assert "http://" in reponse.text and "https://" in reponse.text
    assert _valeur(services.CLE_ASSOCIATION_DEPOT) is None
    assert url not in client.get("/apropos").text


@pytest.mark.parametrize("adresse", [
    "pas-une-adresse",
    "deux@arobases@exemple.test",
    "espace dans@exemple.test",
    "sans-domaine@",
    "@exemple.test",
    "contact@sanspoint",
])
def test_une_adresse_de_contact_malformee_est_refusee_sans_etre_enregistree(client, adresse):
    from app import services

    reponse = _enregistrer(client, contact=adresse)
    assert reponse.status_code == 400
    assert "Adresse de contact invalide" in reponse.text
    assert _valeur(services.CLE_ASSOCIATION_CONTACT) is None


def test_un_refus_ne_fait_pas_perdre_la_saisie_des_autres_champs(client):
    """
    Le bureau corrige le champ fautif, il ne retape pas les trois autres. Et
    RIEN n'est enregistré au passage : enregistrer les champs valides en
    silence laisserait croire que tout est passé (patron de la date invalide de
    /admin/evenement).
    """
    from app import services

    reponse = _enregistrer(
        client,
        nom_association="Ludothèque du Bocage",
        presentation="Un texte qui ne doit pas disparaître.",
        contact="contact@mon-asso.fr",
        depot_url="javascript:alert(1)",
    )
    assert reponse.status_code == 400
    assert "Ludothèque du Bocage" in reponse.text
    assert "Un texte qui ne doit pas disparaître." in reponse.text
    assert "contact@mon-asso.fr" in reponse.text
    assert _valeur(services.CLE_ASSOCIATION_NOM) is None
    assert _valeur(services.CLE_ASSOCIATION_PRESENTATION) is None
    assert _valeur(services.CLE_ASSOCIATION_CONTACT) is None


def test_une_adresse_valide_est_enregistree_et_affichee_en_mailto(client):
    from app import services

    _enregistrer(client, contact="  contact@mon-asso.fr  ")
    assert _valeur(services.CLE_ASSOCIATION_CONTACT) == "contact@mon-asso.fr"
    page = client.get("/apropos")
    assert 'href="mailto:contact@mon-asso.fr"' in page.text


def test_un_champ_vide_efface_le_reglage(client):
    from app import services

    _enregistrer(client, presentation="À effacer", contact="contact@mon-asso.fr",
                 depot_url="https://forge.test/asso")
    assert _valeur(services.CLE_ASSOCIATION_CONTACT) == "contact@mon-asso.fr"

    _enregistrer(client)
    assert _valeur(services.CLE_ASSOCIATION_PRESENTATION) is None
    assert _valeur(services.CLE_ASSOCIATION_CONTACT) is None
    assert _valeur(services.CLE_ASSOCIATION_DEPOT) is None
    page = client.get("/apropos")
    assert "L'association" not in page.text
    assert "<h2>Contact</h2>" not in page.text


# ---------------------------------------------------------------------------
# 5. LE LIEN « JOURNAL DES VERSIONS » — une forme d'URL propre à GitHub
# ---------------------------------------------------------------------------
def test_le_journal_des_versions_s_affiche_sur_un_depot_github(client):
    _enregistrer(client, depot_url="https://github.com/Asso/LudoteX/")
    page = client.get("/apropos")
    assert "journal des versions" in page.text
    assert 'href="https://github.com/Asso/LudoteX/blob/main/CHANGELOG.md"' in page.text


def test_le_journal_des_versions_est_omis_sur_une_autre_forge(client):
    """
    `/blob/main/CHANGELOG.md` est une forme propre à GitHub : sur une autre
    forge, le lien mènerait à une page d'erreur sans que personne s'en aperçoive.
    Le lien vers le dépôt lui-même, lui, reste affiché — c'est celui qu'exige la
    GPL.
    """
    _enregistrer(client, depot_url="https://gitlab.com/asso/ludotex")
    page = client.get("/apropos")
    assert "journal des versions" not in page.text
    assert 'href="https://gitlab.com/asso/ludotex"' in page.text


def test_lien_journal_versions_unitaire():
    from app import services

    assert services.lien_journal_versions("https://github.com/A/B") == \
        "https://github.com/A/B/blob/main/CHANGELOG.md"
    assert services.lien_journal_versions("https://gitea.exemple.test/A/B") is None
    assert services.lien_journal_versions("https://github.com.exemple.test/A/B") is None


# ---------------------------------------------------------------------------
# 6. LE JOURNAL — une ligne seulement si la valeur change
# ---------------------------------------------------------------------------
def test_une_ligne_par_cle_reellement_modifiee(client, _journal_isole):
    from app import journal

    _enregistrer(client, presentation="Un texte", contact="contact@mon-asso.fr",
                 depot_url="https://forge.test/asso")
    actions = [l["action"] for l in _lignes(journal.chemin_journal())]
    assert "association_presentation_modifiee" in actions
    assert "association_contact_modifie" in actions
    assert "association_depot_modifie" in actions
    assert "association_nom_modifie" not in actions


def test_aucune_ligne_quand_rien_ne_change(client, _journal_isole):
    """
    Réenregistrer la page sans rien toucher ne doit produire AUCUNE ligne :
    sinon le journal affirmerait quatre modifications qui n'ont pas eu lieu.
    """
    from app import journal

    _enregistrer(client, presentation="Un texte", contact="contact@mon-asso.fr",
                 depot_url="https://forge.test/asso")
    avant = len(_lignes(journal.chemin_journal()))

    _enregistrer(client, presentation="Un texte", contact="contact@mon-asso.fr",
                 depot_url="https://forge.test/asso")
    apres = _lignes(journal.chemin_journal())
    identite = [l for l in apres[avant:] if l["action"].startswith("association_")]
    assert identite == []


def test_le_journal_ne_contient_jamais_l_adresse_de_contact(client, _journal_isole):
    """
    Une adresse e-mail est une donnée PERSONNELLE dès qu'elle désigne
    quelqu'un, et le journal est conçu pour n'en contenir aucune. La ligne dit
    donc « renseigné », pas l'adresse.
    """
    from app import journal

    _enregistrer(client, contact="marie.dupont@exemple.test")
    contenu = journal.chemin_journal().read_text(encoding="utf-8")
    assert "marie.dupont@exemple.test" not in contenu
    assert "renseigné" in contenu


def test_un_refus_est_journalise_comme_un_echec(client, _journal_isole):
    from app import journal

    _enregistrer(client, depot_url="javascript:alert(1)")
    lignes = [l for l in _lignes(journal.chemin_journal())
              if l["action"] == "association_depot_modifie"]
    assert lignes and lignes[-1]["ok"] is False
    assert lignes[-1]["detail"] == "url_invalide"
    # Et surtout : l'URL refusée ne part pas en clair dans le journal.
    assert "javascript:" not in journal.chemin_journal().read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 7. ROBUSTESSE — la page répond même si la base est indisponible
# ---------------------------------------------------------------------------
def test_apropos_repond_si_la_lecture_en_base_echoue(client, monkeypatch):
    """
    C'est la seule page qui porte la licence, le crédit d'auteur et le lien vers
    le code source : elle doit rester lisible quand le reste du site ne l'est
    plus. Une lecture qui échoue retombe sur « aucun réglage », jamais sur 500.
    """
    import sqlite3

    from app.config import DEPOT_URL
    from app.routes import catalogue

    def _tombe():
        raise sqlite3.OperationalError("base indisponible")

    monkeypatch.setattr(catalogue, "get_connection", _tombe)
    page = client.get("/apropos")
    assert page.status_code == 200
    assert "GPLv3" in page.text
    assert f'href="{DEPOT_URL}"' in page.text
