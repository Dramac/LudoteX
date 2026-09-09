"""
Carnet de maintenance côté BÉNÉVOLE — `/maintenance` (lot agora-5,
docs/conception-signalements.md §7).

La liste, ses filtres et la fermeture d'un signalement sont déjà couverts par
tests/test_signalements.py (services) et tests/test_signalements_admin.py
(écran du bureau) : ici on vérifie ce qui est PROPRE à l'ouverture aux
bénévoles —

- l'accès : jeton exigé en propre, quel que soit l'état du module ;
- l'état « tous » qui ne rend PAS la page publique — le test qui verrouille
  l'arbitrage du lot ;
- ce que cet écran ne porte pas : exports et catégories ;
- que l'écran du bureau, lui, n'a rien perdu.

Fixture sur le patron de tests/test_signalements_admin.py (les deux mots de
passe y sont posés : le carnet bénévole et celui du bureau se testent côte à
côte).
"""

import pytest

JETON = "jeton-test-secret-32-caracteres"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    monkeypatch.setenv("PRET_TOKEN", JETON)
    from app import db as _db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(_db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = _db.get_connection()
    _db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'DIXIT')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _benevole(client):
    """Active l'appareil comme le ferait /acces : le jeton en cookie."""
    client.cookies.set("jeton_pret", JETON)


def _connecter_admin(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})


def _conn():
    from app import db

    return db.get_connection()


def _creer_signalement(id_exemplaire, id_categorie=None, texte=None):
    from app import services

    conn = _conn()
    try:
        if id_categorie is None:
            id_categorie = services.categories_signalement_actives(conn)[0]["id_categorie"]
        return services.creer_signalement(conn, id_exemplaire, id_categorie, texte)
    finally:
        conn.close()


def _id_categorie(nom_partiel):
    from app import services

    conn = _conn()
    try:
        return next(c["id_categorie"] for c in services.lister_categories_signalement(conn)
                    if nom_partiel.lower() in c["nom"].lower())
    finally:
        conn.close()


def _etat_signalement(id_signalement):
    from app import services

    conn = _conn()
    try:
        return services.get_signalement(conn, id_signalement)
    finally:
        conn.close()


def _forcer_etat_module(etat):
    """
    Écrit l'état du module SANS passer par `ecrire_etat_module`, qui refuse
    « tous » sur ce module : c'est ainsi qu'on vérifie que la route tient
    quand même, quelle que soit la valeur qui aurait pu se retrouver en base.
    """
    from app import services

    conn = _conn()
    try:
        services.ecrire_parametre(conn, "module_maintenance", etat)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------
def test_carnet_accessible_avec_le_jeton(client):
    _creer_signalement("001", texte="Coin de la boîte déchiré")
    _benevole(client)

    r = client.get("/maintenance")
    assert r.status_code == 200
    assert "Carnet de maintenance" in r.text
    assert "Catan" in r.text
    assert "Coin de la boîte déchiré" in r.text


def test_carnet_refuse_sans_jeton(client):
    _creer_signalement("001")

    assert client.get("/maintenance").status_code == 403


def test_carnet_accessible_a_l_admin_connecte(client):
    """`peut_ecrire` couvre les deux : le bureau n'a pas à s'activer en plus."""
    _connecter_admin(client)
    assert client.get("/maintenance").status_code == 200


def test_etat_tous_ne_rend_pas_la_page_publique(client):
    """
    LE test de l'arbitrage du lot : la visibilité de module n'est pas une
    autorisation d'accès. Même avec `module_maintenance` à « tous » en base
    — état que l'administration ne propose pas, mais qui reste écrivable par
    accident ou par une base bricolée — un visiteur sans jeton est refusé.
    """
    _creer_signalement("001", texte="Texte libre à ne pas exposer")
    _forcer_etat_module("tous")

    r = client.get("/maintenance")
    assert r.status_code == 403
    assert "Texte libre à ne pas exposer" not in r.text


def test_module_desactive_page_conviviale_et_lien_absent(client):
    from app import db, modules

    conn = db.get_connection()
    modules.ecrire_etat_module(conn, "maintenance", "desactive")
    conn.close()
    _benevole(client)

    r = client.get("/maintenance")
    # 404 assumé par le gestionnaire de `ModuleDesactive` : le module n'existe
    # pas pour ce visiteur — mais avec une page conviviale, pas une erreur brute.
    assert r.status_code == 404
    assert "Module indisponible" in r.text
    assert 'href="/maintenance"' not in client.get("/catalogue").text


def test_lien_dans_le_menu_benevole(client):
    _benevole(client)
    assert 'href="/maintenance"' in client.get("/catalogue").text


def test_lien_absent_du_menu_visiteur(client):
    assert 'href="/maintenance"' not in client.get("/catalogue").text


# ---------------------------------------------------------------------------
# Ce que cet écran ne porte PAS (§7)
# ---------------------------------------------------------------------------
def test_pas_d_export_depuis_le_carnet_benevole(client):
    _creer_signalement("001")
    _benevole(client)

    page = client.get("/maintenance").text
    assert "Exporter en Excel" not in page
    assert "export.xlsx" not in page
    assert "categories-signalement" not in page

    # Et les URL d'export du bureau restent derrière le mot de passe.
    for url in ("/admin/signalements/export.xlsx", "/admin/signalements/export.pdf"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code != 200


# ---------------------------------------------------------------------------
# Filtres — mêmes règles que l'écran du bureau
# ---------------------------------------------------------------------------
def test_filtre_etat_et_categorie(client):
    id_abimee = _id_categorie("abîmée")
    id_manquante = _id_categorie("pièce")
    id_ferme = _creer_signalement("001", id_abimee)
    _creer_signalement("002", id_manquante)
    _benevole(client)
    client.post(f"/maintenance/{id_ferme}/traiter")

    ouverts = client.get("/maintenance").text
    assert "Dixit" in ouverts and "Catan" not in ouverts

    traites = client.get("/maintenance?etat=traites").text
    assert "Catan" in traites and "Dixit" not in traites

    tous = client.get("/maintenance?etat=tous").text
    assert "Catan" in tous and "Dixit" in tous

    par_categorie = client.get(f"/maintenance?etat=tous&categorie={id_abimee}").text
    assert "Catan" in par_categorie and "Dixit" not in par_categorie


def test_filtre_forge_ignore_sans_erreur(client):
    _creer_signalement("001")
    _benevole(client)

    for url in ("/maintenance?etat=nimporte-quoi",
                "/maintenance?categorie=999999",
                "/maintenance?categorie=pas-un-entier"):
        r = client.get(url)
        assert r.status_code == 200
        assert "Catan" in r.text  # la vue par défaut, jamais une erreur


def test_les_filtres_voyagent_avec_l_action(client):
    """On revient sur la vue qu'on avait, pas sur la liste par défaut."""
    id_abimee = _id_categorie("abîmée")
    id_signalement = _creer_signalement("001", id_abimee)
    _benevole(client)

    r = client.post(f"/maintenance/{id_signalement}/traiter",
                    data={"etat": "tous", "categorie": str(id_abimee)},
                    follow_redirects=False)
    assert r.status_code == 303
    cible = r.headers["location"]
    assert cible.startswith("/maintenance?")
    assert "etat=tous" in cible and f"categorie={id_abimee}" in cible


# ---------------------------------------------------------------------------
# Marquer traité
# ---------------------------------------------------------------------------
def test_benevole_referme_un_signalement(client):
    id_signalement = _creer_signalement("001")
    _benevole(client)

    r = client.post(f"/maintenance/{id_signalement}/traiter")
    assert r.status_code == 200
    assert "Signalement marqué traité." in r.text
    assert _etat_signalement(id_signalement)["traite_le"] is not None


def test_fermeture_refusee_sans_jeton(client):
    id_signalement = _creer_signalement("001")

    assert client.post(f"/maintenance/{id_signalement}/traiter").status_code == 403
    assert _etat_signalement(id_signalement)["traite_le"] is None


def test_second_appui_idempotent(client):
    id_signalement = _creer_signalement("001")
    _benevole(client)

    client.post(f"/maintenance/{id_signalement}/traiter")
    premier = _etat_signalement(id_signalement)["traite_le"]
    r = client.post(f"/maintenance/{id_signalement}/traiter")
    assert r.status_code == 200
    assert _etat_signalement(id_signalement)["traite_le"] == premier


def test_signalement_inconnu_ne_bloque_pas(client):
    _benevole(client)

    r = client.post("/maintenance/999999/traiter")
    assert r.status_code == 200
    assert "Carnet de maintenance" in r.text


def test_date_de_traitement_visible_et_non_cachee_dans_un_title(client):
    """
    Un `title` ne s'affiche jamais sur un téléphone : la date de traitement,
    qui n'existait qu'en infobulle, est devenue du texte.
    """
    id_signalement = _creer_signalement("001")
    _benevole(client)
    client.post(f"/maintenance/{id_signalement}/traiter")

    page = client.get("/maintenance?etat=traites").text
    assert 'title="Traité le' not in page
    from app import services

    jour = services.format_local(_etat_signalement(id_signalement)["traite_le"])[:10]
    assert jour in page


# ---------------------------------------------------------------------------
# Mise en cartes sous 640 px — le défaut d'affichage à l'origine du lot
# ---------------------------------------------------------------------------
def test_les_deux_carnets_optent_pour_la_variante_en_cartes(client):
    _creer_signalement("001")
    _benevole(client)
    assert "admin-table--cartes" in client.get("/maintenance").text

    _connecter_admin(client)
    assert "admin-table--cartes" in client.get("/admin/signalements").text


def test_chaque_valeur_porte_son_libelle_en_cartes(client):
    """
    Les en-têtes de colonnes disparaissent en mode cartes : chaque valeur doit
    porter son propre libellé, dans le document (et non par un `content:` de
    CSS, qu'un lecteur d'écran n'est pas tenu de lire).
    """
    _creer_signalement("001", texte="Une pièce manque")
    _benevole(client)
    page = client.get("/maintenance").text

    for libelle in ("Boîte", "Catégorie", "Détail", "Signalé le"):
        assert f'<span class="cartes-libelle">{libelle}</span>' in page


def test_word_break_toujours_present(client):
    """
    Garde-fou : le correctif anti-débordement mobile de `.admin-table` (retour
    terrain iPhone 13 mini) ne doit pas disparaître au profit de la mise en
    cartes — c'est la mise en page qu'on a traitée, pas la césure.
    """
    css = client.get("/static/css/style.css").text
    assert "word-break: break-word" in css
    assert ".admin-table--cartes" in css


# ---------------------------------------------------------------------------
# L'écran du bureau n'a rien perdu
# ---------------------------------------------------------------------------
def test_ecran_admin_conserve_exports_et_liste(client):
    _creer_signalement("001", texte="Boîte cassée")
    _connecter_admin(client)

    page = client.get("/admin/signalements").text
    assert "Boîte cassée" in page
    assert "Exporter en Excel" in page and "Liste imprimable (PDF)" in page
    assert "/admin/categories-signalement" in page
    assert client.get("/admin/signalements/export.xlsx").status_code == 200
    assert client.get("/admin/signalements/export.pdf").status_code == 200


# ---------------------------------------------------------------------------
# Administration des fonctionnalités — « Visible par tous » n'est pas proposé
# ---------------------------------------------------------------------------
def test_l_etat_tous_n_est_pas_propose_pour_ce_module(client):
    _connecter_admin(client)

    page = client.get("/admin/fonctionnalites").text
    assert 'id="mod_maintenance_benevoles"' in page
    assert 'id="mod_maintenance_tous"' not in page
    # Les autres modules, eux, le proposent toujours.
    assert 'id="mod_stats_tous"' in page


def test_formulaire_forge_ne_peut_pas_rendre_le_carnet_public(client):
    from app import db, modules

    _connecter_admin(client)
    client.post("/admin/fonctionnalites", data={"module_maintenance": "tous"})

    conn = db.get_connection()
    try:
        assert modules.lire_etat_module(conn, "maintenance") == "benevoles"
    finally:
        conn.close()


def test_etat_par_defaut_du_module_sur_une_base_existante(client):
    """
    Une base d'avant ce module n'a aucune ligne `module_maintenance` : le
    défaut GLOBAL est « tous », celui-ci doit être « bénévoles ».
    """
    from app import db, modules

    conn = db.get_connection()
    try:
        assert modules.lire_etat_module(conn, "maintenance") == "benevoles"
        assert modules.lire_etat_module(conn, "stats") == "tous"
    finally:
        conn.close()


def test_ecrire_etat_module_refuse_tous_sur_ce_module(client):
    from app import db, modules

    conn = db.get_connection()
    try:
        with pytest.raises(ValueError):
            modules.ecrire_etat_module(conn, "maintenance", "tous")
        modules.ecrire_etat_module(conn, "maintenance", "discret")
    finally:
        conn.close()
