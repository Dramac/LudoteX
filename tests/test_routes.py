"""Tests d'intégration des routes (fiche + prêt/retour) via une base temporaire."""

import os

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Bases SQLite temporaires isolées par test (prêt + tournois, séparées).
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.tournoi import db as tdb
    from app.planning import db as pdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_page_erreur_500(client, monkeypatch):
    # On force une erreur inattendue dans une route et on vérifie que l'app
    # renvoie la page 500 conviviale (et ne « plante » pas).
    from fastapi.testclient import TestClient

    from app import services
    from app.main import app

    def boum(*a, **k):
        raise RuntimeError("erreur simulée")

    monkeypatch.setattr(services, "lister_catalogue", boum)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/catalogue")
    assert r.status_code == 500
    assert "une erreur est survenue" in r.text.lower()


def test_aide_page(client):
    r = client.get("/aide")
    assert r.status_code == 200 and "Mode d'emploi" in r.text


def test_apropos_page(client):
    r = client.get("/apropos")
    assert r.status_code == 200
    assert "À propos" in r.text
    assert "contact@djplm.fr" in r.text
    assert "GPLv3" in r.text
    # Topo des accès par niveau (visiteur / bénévole / admin).
    assert "Visiteur" in r.text
    assert "Bénévole" in r.text
    assert "Administrateur" in r.text
    from app.version import APP_VERSION
    assert APP_VERSION in r.text


def test_anti_double_soumission_script_present(client):
    # M3 : le script anti double-appui (base.html) est chargé sur TOUTES les
    # pages, pas seulement /pret -- couvre tous les formulaires du site.
    r = client.get("/catalogue")
    assert r.status_code == 200
    assert 'addEventListener("submit"' in r.text
    assert 'addEventListener("pageshow"' in r.text
    assert "Un instant…" in r.text
    # Ne désactive rien si un confirm() existant a refusé l'envoi.
    assert "e.defaultPrevented" in r.text


def test_accueil(client):
    # La racine sert la page d'accueil (plus de redirection vers /catalogue).
    r = client.get("/")
    assert r.status_code == 200
    # 1 exemplaire de test (Catan), disponible.
    assert "disponible" in r.text.lower()
    assert "/catalogue" in r.text and "/tournois" in r.text


def test_accueil_pluriel_jeux(client):
    # Avec plusieurs exemplaires disponibles, le pluriel de "jeu" est "jeux"
    # (pas "jeus" — faute corrigée, voir docs/idees-ux.md Q1).
    from app import db

    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'CATAN')"
        )
        conn.commit()
    finally:
        conn.close()
    r = client.get("/")
    assert "jeux" in r.text and "disponibles" in r.text
    assert "jeus" not in r.text.lower()


def test_accueil_tournoi_imminent(client):
    # Un tournoi publié commençant dans 30 min apparaît ; un autre dans 3 h non.
    from datetime import datetime, timedelta, timezone

    from app.tournoi import db as tdb
    from app.tournoi import services as ts

    conn = tdb.get_connection()
    try:
        proche = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        loin = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(timespec="seconds")
        idp = ts.creer_tournoi(conn, "Tournoi proche", date_heure=proche)
        ts.creer_tournoi(conn, "Tournoi lointain", date_heure=loin)
        ts.changer_etat(conn, idp, "inscriptions")
        conn.commit()
    finally:
        conn.close()
    r = client.get("/")
    assert "Tournoi proche" in r.text
    assert "Tournoi lointain" not in r.text


def test_menu_benevole_conditionnel(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-menu-xyz")
    # Visiteur non activé : le menu PUBLIC est affiché (Catalogue visible, pas de Scanner).
    html_visiteur = client.get("/catalogue").text
    assert 'class="menu-benevole"' in html_visiteur
    assert '/scanner' not in html_visiteur
    # Après activation du jeton bénévole : le menu BÉNÉVOLE prend le relais (Scanner présent).
    client.get("/acces", params={"jeton": "jeton-menu-xyz"})
    html_benevole = client.get("/catalogue").text
    assert 'class="menu-benevole"' in html_benevole
    assert '/scanner' in html_benevole


def test_menu_bandeau_replie_sur_mobile(client, monkeypatch):
    # Retour iPhone 13 mini : le menu du bandeau prenait 3 lignes au-dessus du
    # contenu. Deux rendus séparés du même menu (fragments partagés) : replié
    # dans un <details> (accordéon natif, sans JS) sous 640px, ET une copie à
    # plat pour >= 640px (CSS bascule laquelle des deux s'affiche — un seul
    # <details> "forcé ouvert" en CSS s'est révélé peu fiable sur ordinateur,
    # menu resté invisible avec certains moteurs de rendu).
    monkeypatch.setenv("PRET_TOKEN", "jeton-menu-details")
    r = client.get("/catalogue")
    assert '<details class="menu-bandeau">' in r.text
    assert "<summary>Menu</summary>" in r.text
    assert 'class="menu-bandeau-large"' in r.text
    # Le nav reste bien À L'INTÉRIEUR du <details> (juste après <summary>).
    assert r.text.index("<summary>Menu</summary>") < r.text.index('class="menu-benevole"')
    # Les DEUX copies du menu sont bien présentes (une par rendu).
    assert r.text.count('class="menu-benevole"') == 2
    # Comportement inchangé une fois bénévole activé.
    client.get("/acces", params={"jeton": "jeton-menu-details"})
    r2 = client.get("/catalogue")
    assert '<details class="menu-bandeau">' in r2.text
    assert r2.text.count("/scanner") >= 2


def test_menu_lien_administration_si_connecte(client, monkeypatch):
    # Nouveau besoin : un administrateur connecté doit pouvoir revenir à
    # /admin depuis le menu du bandeau, sur n'importe quelle page (pas
    # seulement via le tableau de bord lui-même).
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    # Pas de session admin : aucun lien.
    r = client.get("/catalogue")
    assert "Administration" not in r.text
    # Session admin ouverte : le lien apparaît, dans les DEUX rendus du menu
    # (voir test_menu_bandeau_replie_sur_mobile : replié + à plat).
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r2 = client.get("/catalogue")
    assert r2.text.count('<a href="/admin">Administration</a>') == 2
    # Après déconnexion, le lien disparaît de nouveau.
    client.get("/admin/logout")
    r3 = client.get("/catalogue")
    assert "Administration" not in r3.text


def test_catalogue_derniers_achats(client):
    from app import db
    conn = db.get_connection()
    conn.execute("UPDATE titres SET date_achat = '2021-03-04' WHERE reference_titre = 'CATAN'")
    conn.commit()
    conn.close()
    r = client.get("/catalogue")
    assert r.status_code == 200
    assert "Dernières acquisitions" in r.text and "04/03/2021" in r.text


def test_catalogue(client):
    r = client.get("/catalogue")
    assert r.status_code == 200
    assert "Catalogue" in r.text and "Catan" in r.text
    assert "1 / 1 dispo" in r.text or "1 jeu" in r.text


def test_catalogue_bouton_remonter(client):
    # M7 (docs/idees-ux.md) : bouton flottant qui ramène au panneau de
    # recherche (ancre pure CSS, pas de JS) -- 600 titres, un seul long
    # défilement sinon.
    r = client.get("/catalogue")
    assert 'id="haut"' in r.text
    assert '<a href="#haut" class="bouton-filtrer bouton-haut">↑ Recherche</a>' in r.text


def test_catalogue_pluriel_jeux(client):
    # Q2 : vrai pluriel (macro/global `pluriel`), pas de « jeu(x) » parenthésé.
    r = client.get("/catalogue")
    assert "1 jeu</p>" in r.text or "1 jeu<" in r.text
    assert "jeu(x)" not in r.text

    from app import db
    conn = db.get_connection()
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('7WONDERS', '7 Wonders')")
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', '7WONDERS')")
    conn.commit()
    conn.close()
    r2 = client.get("/catalogue")
    assert "2 jeux" in r2.text
    assert "jeu(x)" not in r2.text


def test_catalogue_recherche_par_nom(client):
    r = client.get("/catalogue", params={"q": "cat"})
    assert r.status_code == 200
    assert "Catan" in r.text


def test_catalogue_filtre_joueurs_exclut_hors_bornes(client):
    # Catan (jeu de test) n'a pas de bornes joueurs -> exclu si filtre joueurs actif
    r = client.get("/catalogue", params={"joueurs": "3"})
    assert r.status_code == 200
    assert "Catan" not in r.text


def test_racine_sert_accueil(client):
    # La racine sert désormais la page d'accueil directement (plus de redirection).
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Des jeux plein la Manche" in r.text


def test_stats_page(client):
    # Q9 : aucun prêt terminé -> « — » avec une info-bulle, pas « 0 min ».
    r0 = client.get("/stats")
    assert 'title="Aucun prêt terminé sur la période"' in r0.text
    assert "0 min" not in r0.text

    client.post("/pret/001/preter")          # un prêt pour alimenter les stats
    r = client.get("/stats")
    assert r.status_code == 200
    assert "Statistiques" in r.text
    assert "Catan" in r.text                 # apparaît dans le palmarès
    r2 = client.get("/stats", params={"tri": "exemplaire"})
    assert r2.status_code == 200
    assert "par exemplaire" in r2.text

    # Un prêt TERMINÉ : la durée moyenne est affichée sans info-bulle.
    client.post("/pret/001/rendre")
    r3 = client.get("/stats")
    assert 'title="Aucun prêt terminé sur la période"' not in r3.text


def test_stats_alias_redirige(client):
    for chemin in ("/stat", "/statistique", "/statistiques"):
        r = client.get(chemin, follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"].startswith("/stats")


def test_stats_filtre_periode(client):
    client.post("/pret/001/preter")
    # Période lointaine dans le passé -> aucun prêt.
    r = client.get("/stats", params={"debut": "2000-01-01T00:00",
                                     "fin": "2000-01-02T00:00"})
    assert r.status_code == 200
    assert "Aucun prêt sur la période." in r.text


def test_stats_exports(client):
    client.post("/pret/001/preter")
    x = client.get("/stats/export.xlsx")
    assert x.status_code == 200
    assert "spreadsheetml" in x.headers["content-type"]
    assert x.content[:2] == b"PK"            # un .xlsx est une archive ZIP
    p = client.get("/stats/export.pdf")
    assert p.status_code == 200
    assert p.headers["content-type"] == "application/pdf"
    assert p.content[:4] == b"%PDF"


def test_fiche_publique(client):
    r = client.get("/jeu/001")
    assert r.status_code == 200
    assert "Catan" in r.text
    assert "Disponible" in r.text
    # Q5 : titre d'onglet cohérent avec les autres pages (« <jeu> — asso »).
    assert "<title>Catan — Des jeux plein la Manche</title>" in r.text


def test_fiche_inconnue(client):
    r = client.get("/jeu/999")
    assert r.status_code == 404
    assert "inconnu" in r.text.lower()


def test_auth_protege_pret_et_scanner(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")

    # Sans cookie : écrans bénévole refusés (403), mais public accessible.
    assert client.get("/pret/001").status_code == 403
    assert client.get("/scanner").status_code == 403
    assert client.get("/catalogue").status_code == 200
    assert client.get("/jeu/001").status_code == 200

    # Lien d'activation invalide : pas d'accès.
    assert client.get("/acces", params={"jeton": "mauvais"}).status_code == 403

    # Activation correcte : pose le cookie, puis accès autorisé.
    r = client.get("/acces", params={"jeton": "jeton-test-secret-32-caracteres"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/scanner"
    assert client.get("/scanner").status_code == 200
    assert client.get("/pret/001").status_code == 200


def test_admin_login_autofocus(client, monkeypatch):
    # Q10 : le champ mot de passe reçoit le focus automatiquement.
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    r = client.get("/admin")
    assert '<input type="password" id="mot_de_passe" name="mot_de_passe" autofocus>' in r.text


def test_favicon_carre(client):
    # Q11 : favicons PNG carrés référencés (plus le JPEG d'origine, mal cadré
    # en petit) et effectivement servis en statique.
    r = client.get("/catalogue")
    assert 'href="/static/img/favicon-192.png"' in r.text
    assert 'href="/static/img/favicon-512.png"' in r.text
    assert "logo_djplm.jpg" not in r.text.split("<body", 1)[0]  # plus dans <head>
    for nom in ("favicon-192.png", "favicon-512.png"):
        rf = client.get(f"/static/img/{nom}")
        assert rf.status_code == 200
        assert rf.headers["content-type"] == "image/png"


def test_admin_login_et_creation_jeu(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")

    # Sans session : accès admin redirige vers la connexion.
    r = client.get("/admin/jeu-nouveau", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"

    # Mauvais mot de passe -> refus.
    assert client.post("/admin/login", data={"mot_de_passe": "faux"}).status_code == 403

    # Bon mot de passe -> session ouverte (cookie conservé par le client).
    r2 = client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"},
                     follow_redirects=False)
    assert r2.status_code == 303

    # Création d'un jeu -> redirige vers sa fiche admin (id auto, préfixe A).
    r3 = client.post("/admin/jeu-nouveau", data={"nom": "Jeu Test Admin",
                     "type_jeu": "Extension", "categorie": "Cartes",
                     "nb_joueurs_min": "2", "nb_joueurs_max": "5",
                     "age_min": "8", "duree_min": "20"},
                     follow_redirects=False)
    assert r3.status_code == 303
    ref = r3.headers["location"].rsplit("/", 1)[-1]
    assert ref == "JEU_TEST_ADMIN"

    # La fiche admin affiche un exemplaire à id auto (A0001), le type, l'étiquette.
    fiche = client.get("/admin/jeu/" + ref)
    assert fiche.status_code == 200 and "A0001" in fiche.text
    assert "Extension" in fiche.text
    # La fiche publique affiche aussi le type.
    assert "Extension" in client.get("/jeu/A0001").text
    png = client.get("/admin/etiquette/A0001.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"


def test_admin_cloture_prets(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/pret/001/preter")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r = client.post("/admin/cloturer-prets")
    assert r.status_code == 200 and "clôturé" in r.text
    # L'exemplaire est de nouveau disponible.
    assert "Disponible" in client.get("/pret/001").text


def test_admin_accede_au_pret(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-benevole-xyz")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    # Sans jeton ni session admin : écrans bénévole refusés.
    assert client.get("/scanner").status_code == 403
    assert client.get("/pret/001").status_code == 403
    # Connexion admin -> accès direct au scanner et au prêt (sans activer le jeton).
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    assert client.get("/scanner").status_code == 200
    assert client.get("/pret/001").status_code == 200


def test_admin_donnees_import_export(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})

    # Page + export CSV / Excel.
    assert client.get("/admin/donnees").status_code == 200
    csv = client.get("/admin/donnees/export.csv")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert "Code jeu" in csv.text and "Catan" in csv.text
    xlsx = client.get("/admin/donnees/export.xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"

    # Import d'un CSV téléversé -> nouveau jeu créé.
    contenu = "Code jeu;Nom jeu;Type\n900;Jeu Importé;Jeu\n".encode("utf-8")
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("cat.csv", contenu, "text/csv")})
    assert r.status_code == 200 and "Import réussi" in r.text
    assert client.get("/jeu/900").status_code == 200

    # M9 (docs/idees-ux.md) : confirmation de restauration reformulée (patron
    # « Action ? + conséquence + porte de sortie », plus de détails techniques
    # énumérés entre parenthèses).
    page = client.get("/admin/donnees").text
    assert "Remplacer TOUTES les données par cette sauvegarde ?" in page
    assert "L\\'état actuel sera d\\'abord mis de côté automatiquement." in page


def test_confirmations_reformulees_m9(client, monkeypatch):
    # M9 (docs/idees-ux.md) : quelques confirm() trop techniques réécrits sur
    # le patron « Action ? + conséquence principale + porte de sortie », sans
    # détails techniques énumérés (états internes, listes de tables...).
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    dashboard = client.get("/admin").text
    assert ("Clôturer tous les prêts et sorties en cours ? Tout redeviendra "
            "disponible, sans rien perdre de l\\'historique.") in dashboard
    # L'ancienne formulation technique (parenthèse) a bien disparu.
    assert "(L\\'historique et les statistiques sont conservés.)" not in dashboard


def test_admin_etiquettes_lot(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})

    page = client.get("/admin/etiquettes")
    assert page.status_code == 200 and "Imprimer des étiquettes" in page.text

    # Sélection vide -> message, jamais d'erreur.
    vide = client.post("/admin/etiquettes/pdf", data={})
    assert vide.status_code == 200 and "au moins un jeu" in vide.text

    # Génération PDF couleur pour un jeu (toutes ses boîtes).
    pdf = client.post("/admin/etiquettes/pdf",
                      data={"references": "CATAN", "colonnes": "2", "lignes": "8"})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"

    # Marges absurdes (> largeur A4) -> message clair (pas d'erreur 500).
    big = client.post("/admin/etiquettes/pdf",
                      data={"references": "CATAN", "marge_gauche": "300"})
    assert big.status_code == 200 and "marges" in big.text.lower()


def test_admin_supervision(client, monkeypatch):
    # Sans session : redirection vers la connexion (page en lecture seule aussi protégée).
    r = client.get("/admin/supervision", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"

    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r2 = client.get("/admin/supervision")
    assert r2.status_code == 200
    # Les 5 sections attendues.
    assert "Bases de données" in r2.text
    assert "Espace disque" in r2.text
    assert "Sauvegarde" in r2.text
    assert "Jeton bénévole" in r2.text
    assert "Version déployée" in r2.text
    # Les 3 bases sont bien listées (chemins de test isolés).
    assert "Prêt de jeux" in r2.text
    assert "Tournois" in r2.text
    assert "Planning bénévole" in r2.text
    # Retour terrain : pastille d'état compacte (badge, même famille que les
    # badges dispo/sorti du catalogue), pas la grande bannière .resultat
    # (disproportionnée dans une cellule de tableau).
    assert '<span class="badge badge-ok">Ok</span>' in r2.text
    assert "Présente" not in r2.text


def test_admin_table_css_responsive(client):
    # Retour terrain : le tableau "Bases de données" de /admin/supervision
    # débordait à droite sur iPhone (.admin-table n'avait aucune règle CSS,
    # largeur au contenu du navigateur). Vérifie que la règle existe bien.
    # Depuis S1 (docs/ui-composants.md §9), .detail et .admin-table partagent
    # les mêmes règles (même composant, deux noms) : la sélection couvre
    # les deux classes plutôt que .admin-table isolée.
    r = client.get("/static/css/style.css")
    assert r.status_code == 200
    assert ".admin-table { width: 100%;" in r.text or ".detail, .admin-table { width: 100%;" in r.text
    assert "vertical-align: top; word-break: break-word;" in r.text


def test_pas_de_bouton_sans_variante(client):
    # S1 (docs/ui-composants.md) : .bouton seul n'a pas de couleur de fond
    # (seules .bouton-principal/.bouton-secondaire en définissent une) — un
    # bouton avec la seule classe "bouton" s'affiche donc avec un texte blanc
    # sur le gris par défaut du navigateur, peu ou pas lisible. Garde-fou
    # contre la réintroduction de ce défaut (trouvé et corrigé sur 7 boutons
    # du module planning + module_desactive.html + admin_fonctionnalites.html).
    import pathlib

    templates = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"
    fautifs = []
    for fichier in templates.glob("*.html"):
        if 'class="bouton"' in fichier.read_text(encoding="utf-8"):
            fautifs.append(fichier.name)
    assert fautifs == []


def test_bouton_disabled_css(client):
    # Composant générique ajouté avec le correctif ci-dessus (voir
    # docs/ui-composants.md §10) : un bouton désactivé doit être grisé sans
    # recourir à un style inline propre à chaque gabarit.
    r = client.get("/static/css/style.css")
    assert r.status_code == 200
    assert ".bouton:disabled" in r.text


def test_admin_dashboard_supervision_embarquee(client, monkeypatch):
    # Le tableau de bord embarque désormais l'état de supervision (colonne
    # dédiée sur grand écran, masquée en CSS sous le breakpoint bureau — le
    # contenu reste dans le HTML dans les deux cas, donc testable directement).
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Supervision" in r.text
    assert "Bases de données" in r.text
    assert "Version déployée" in r.text
    assert "Prêt de jeux" in r.text
    # Menu "Gérer" toujours présent, avec ses sous-groupes.
    assert "Jeux &amp; étiquettes" in r.text or "Jeux & étiquettes" in r.text
    assert 'href="/admin/etiquettes"' in r.text
    # La supervision reste aussi disponible après clôture des prêts (rendu via
    # le même helper que la connexion).
    r2 = client.post("/admin/cloturer-prets")
    assert r2.status_code == 200 and "Bases de données" in r2.text


def test_formation_mode_inactif_par_defaut(client, monkeypatch):
    # Sans MODE_FORMATION : aucun bandeau, aucun filigrane, aucun bouton reset,
    # aucun lien (FORMATION_URL absente), route de reset fermée (404).
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})

    r_public = client.get("/catalogue")
    assert "SITE DE FORMATION" not in r_public.text
    assert "mode-formation" not in r_public.text

    r_admin = client.get("/admin")
    assert "SITE DE FORMATION" not in r_admin.text
    assert "Réinitialiser les données de formation" not in r_admin.text
    assert "Site de formation" not in r_admin.text

    assert client.post("/admin/formation/reinitialiser").status_code == 404


def test_formation_mode_actif(client, monkeypatch):
    # Bandeau + filigrane injectés via les globals Jinja (comme app.config au
    # démarrage réel) ; MODE_FORMATION revérifié côté route admin.
    from app.routes import admin as admin_routes
    from app.templating import templates

    monkeypatch.setattr(admin_routes, "MODE_FORMATION", True)
    monkeypatch.setitem(templates.env.globals, "mode_formation", True)

    # Page publique.
    r_public = client.get("/catalogue")
    assert "SITE DE FORMATION" in r_public.text
    assert "mode-formation" in r_public.text

    # Page bénévole (mode ouvert par défaut dans la fixture : pas de jeton requis).
    r_scanner = client.get("/scanner")
    assert "SITE DE FORMATION" in r_scanner.text

    # Page admin : bandeau + bouton de réinitialisation visible.
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    r_admin = client.get("/admin")
    assert "SITE DE FORMATION" in r_admin.text
    assert "Réinitialiser les données de formation" in r_admin.text
    # M9 (docs/idees-ux.md) : confirmation reformulée, sans énumération
    # technique des tables concernées entre parenthèses.
    assert ("Réinitialiser les données de formation ? Tout ce qui a été "
            "modifié pendant cette session sera perdu.") in r_admin.text
    assert "(jeux, prêts, tournoi)" not in r_admin.text

    # Bouton fonctionnel : vide + repeuple les bases de l'instance courante.
    r_reset = client.post("/admin/formation/reinitialiser")
    assert r_reset.status_code == 200
    assert "réinitialisées" in r_reset.text
    catalogue = client.get("/catalogue")
    assert "essai n°1" in catalogue.text  # apostrophe échappée en HTML (&#39;)
    assert "Catan" not in catalogue.text  # jeu de la fixture, effacé par le reset


def test_formation_lien_admin_production(client, monkeypatch):
    # Instance de PRODUCTION (MODE_FORMATION inactif) : le lien admin
    # n'apparaît que si FORMATION_URL est définie.
    from app.templating import templates

    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})

    monkeypatch.setitem(templates.env.globals, "formation_url", "https://formation.example.fr")
    r = client.get("/admin")
    assert 'href="https://formation.example.fr"' in r.text
    assert "Site de formation" in r.text


def test_admin_changement_mdp(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "initial-123")
    client.post("/admin/login", data={"mot_de_passe": "initial-123"})
    # Mauvaise confirmation -> pas de changement.
    r = client.post("/admin/motdepasse", data={"ancien": "initial-123",
                    "nouveau": "nouveau-456", "confirmation": "xxx"})
    assert "ne correspond pas" in r.text
    # Changement correct.
    r2 = client.post("/admin/motdepasse", data={"ancien": "initial-123",
                     "nouveau": "nouveau-456", "confirmation": "nouveau-456"})
    assert "modifié" in r2.text


def test_admin_jeton_reinitialisation(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    # Réinitialisation (sans date) -> jeton posé + lien + validité par défaut affichée.
    r = client.post("/admin/jeton/reinitialiser", data={"expire": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/admin/jeton")
    assert page.status_code == 200 and "/acces?jeton=" in page.text
    assert "Valable jusqu'au" in page.text


def test_jeton_expire_ferme_acces(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    # Réinitialisation avec une date de fin déjà passée -> jeton expiré.
    client.post("/admin/jeton/reinitialiser", data={"expire": "2000-01-01T00:00"})
    # On déconnecte l'admin (sinon la session admin ouvrirait l'accès) : côté
    # bénévole, le jeton expiré ferme bien l'accès.
    client.get("/admin/logout")
    assert client.get("/scanner").status_code == 403


def test_export_pdf_sections(client):
    client.post("/pret/001/preter")
    r = client.get("/stats/export.pdf", params=[("sections", "synthese")])
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_pret_tournoi_route(client):
    r = client.post("/pret/001/tournoi")
    assert r.status_code == 200 and "tournoi" in r.text.lower()
    # L'écran indique « Sorti — tournoi » (pas d'emplacement).
    assert "Sorti — tournoi" in client.get("/pret/001").text
    # Hors statistiques : aucun prêt comptabilisé.
    stats = client.get("/stats").text
    assert '<span class="chiffre-val">0<' in stats
    # Mais visible dans « Jeux actuellement sortis » (bloc tournoi).
    assert "Jeux actuellement sortis" in stats and "En tournoi" in stats


def test_retour_confirme_en_vert(client):
    # M8 : un retour (prêt ou tournoi) est un SUCCÈS, affiché en vert
    # (resultat-ok) comme un prêt -- pas en bleu (resultat-info, réservé aux
    # informations neutres comme tournoi_sorti).
    client.post("/pret/001/preter")
    r = client.post("/pret/001/rendre")
    assert 'class="resultat resultat-ok"' in r.text
    assert "Récupérer la pièce d'identité" in r.text

    # Sortie pour un tournoi : information neutre, reste en bleu.
    sortie = client.post("/pret/001/tournoi")
    assert 'class="resultat resultat-info"' in sortie.text

    # Retour de ce tournoi : succès, désormais en vert (comme un retour normal).
    rt = client.post("/pret/001/rendre")
    assert 'class="resultat resultat-ok"' in rt.text
    assert "Retour de tournoi enregistré" in rt.text


def test_stats_jeux_sortis(client):
    client.post("/pret/001/preter")
    stats = client.get("/stats").text
    assert "Jeux actuellement sortis" in stats
    assert "Prêtés au public" in stats


def test_scanner_page(client):
    r = client.get("/scanner")
    assert r.status_code == 200
    assert "<video" in r.text
    assert "/static/js/scanner.js" in r.text
    assert "/static/js/jsQR.js" in r.text   # jsQR hébergé en local
    assert "jsdelivr" not in r.text.lower() and "cdn.jsdelivr" not in r.text.lower()
    # Q7 : texte clair, sans jargon technique (« session »).
    assert "ne demandera l'autorisation caméra" in r.text
    assert "qu'une seule fois" in r.text
    assert "par session" not in r.text
    # Q8 : le statut du scanner est annoncé aux lecteurs d'écran.
    assert 'id="statut" aria-live="polite"' in r.text


def test_scanner_saisie_manuelle_lien(client):
    # Le formulaire de secours est présent sur la page du scanner.
    r = client.get("/scanner")
    assert '/scanner/saisie' in r.text
    assert 'Saisie manuelle' in r.text


def test_saisie_manuelle_code_valide_redirige(client):
    # Code existant (avec espaces autour, à tolérer) -> redirection vers /pret/<id>.
    r = client.get("/scanner/saisie", params={"code": "  001 "},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/pret/001"


def test_saisie_manuelle_code_inconnu_message(client):
    # Code inconnu -> pas d'erreur brute : retour au scanner avec un message.
    r = client.get("/scanner/saisie", params={"code": "ZZZ999"})
    assert r.status_code == 200
    assert "Aucune boîte" in r.text
    assert "ZZZ999" in r.text          # champ prérempli, prêt à corriger
    assert '/scanner/saisie' in r.text


def test_saisie_manuelle_protegee_par_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-saisie-secret-32-caracteres")
    # Sans jeton : la saisie manuelle est refusée comme le reste du scanner.
    assert client.get("/scanner/saisie", params={"code": "001"}).status_code == 403


def test_cycle_preter_puis_rendre(client):
    r = client.post("/pret/001/preter")
    assert r.status_code == 200
    assert "Emplacement" in r.text and ">1<" in r.text.replace(" ", "")

    # Re-prêter alors que déjà sorti -> message, pas d'erreur
    r2 = client.get("/pret/001")
    assert "Sorti" in r2.text

    r3 = client.post("/pret/001/rendre")
    assert r3.status_code == 200
    # Numéro d'emplacement en grand au retour (Q3), même gabarit qu'au prêt.
    assert "Récupérer la pièce d'identité à l'emplacement" in r3.text
    assert '<p class="pochette-num pochette-num--retour">1</p>' in r3.text

    # Après retour : de nouveau disponible
    r4 = client.get("/pret/001")
    assert "Disponible" in r4.text


def test_bouton_scanner_apres_action(client):
    # Q4 : après une action (résultat affiché), un vrai bouton pleine largeur
    # propose d'enchaîner sur le scan suivant.
    bouton = '<a class="bouton bouton-secondaire" href="/scanner">📷 Scanner le jeu suivant</a>'
    r = client.post("/pret/001/preter")
    assert bouton in r.text

    # Simple consultation (pas de résultat) : pas de bouton, le petit lien reste.
    r2 = client.get("/pret/001")
    assert bouton not in r2.text
    assert '<a class="lien" href="/scanner">Scanner le jeu suivant</a>' in r2.text


def test_live_page(client):
    # La page du tableau de bord salle répond et contient ses sections clés.
    r = client.get("/live")
    assert r.status_code == 200
    assert "Tableau de bord" in r.text
    assert "Jeux sortis" in r.text and "Tournois en cours" in r.text
    assert "/live/data" in r.text          # le polling JS pointe bien vers l'endpoint
    assert "Menu de l'application" in r.text   # bouton retour vers le menu
    # Sécurité : aucune mention de pochette sur l'écran public.
    assert "pochette" not in r.text.lower()


def test_live_data(client):
    # Sans aucun prêt : tout est disponible, aucun mouvement.
    d0 = client.get("/live/data").json()
    assert d0["jeux"]["total"] == 1
    assert d0["jeux"]["sortis"] == 0
    assert d0["mouvements"] == []
    # Sécurité : le numéro de pochette n'est jamais exposé dans les données.
    assert "pochette" not in client.get("/live/data").text.lower()

    # On prête l'exemplaire de test : un jeu sort, un mouvement « prêt » apparaît.
    client.post("/pret/001/preter")
    d1 = client.get("/live/data").json()
    assert d1["jeux"]["sortis"] == 1
    assert d1["jeux"]["disponibles"] == 0
    assert len(d1["mouvements"]) == 1
    assert d1["mouvements"][0]["type"] == "pret"
    assert d1["mouvements"][0]["nom"] == "Catan"
    assert "numero_pochette" not in d1["mouvements"][0]

    # Après retour : deux mouvements (prêt + retour), le plus récent en tête.
    client.post("/pret/001/rendre")
    d2 = client.get("/live/data").json()
    assert d2["jeux"]["sortis"] == 0
    assert len(d2["mouvements"]) == 2
    assert d2["mouvements"][0]["type"] == "retour"


def test_live_horodatage_sans_secondes(client):
    # L'heure est au format HH:MM (pas de secondes).
    import re
    h = client.get("/live/data").json()["horodatage"]
    assert re.fullmatch(r"\d{2}:\d{2}", h), h


def test_live_titre_configurable(client):
    # Titre par défaut quand rien n'est réglé.
    assert client.get("/live/data").json()["titre"] == "Des jeux plein la Manche"
    # Réglage du titre (comme le ferait l'admin) -> répercuté sur page et données.
    from app import db, services

    conn = db.get_connection()
    try:
        services.ecrire_parametre(conn, "live_titre", "Festival du Jeu 2026")
    finally:
        conn.close()
    assert client.get("/live/data").json()["titre"] == "Festival du Jeu 2026"


# ---------------------------------------------------------------------------
# Tests du système de fonctionnalités (activation / désactivation des modules)
# ---------------------------------------------------------------------------

def _set_module(tmp_path, nom: str, etat: str):
    """Helper : écrit l'état d'un module directement en base."""
    from app import db, services
    conn = db.get_connection()
    try:
        services.ecrire_parametre(conn, f"module_{nom}", etat)
    finally:
        conn.close()


def test_fonctionnalites_admin_page(client, monkeypatch):
    """La page /admin/fonctionnalites est accessible quand on est connecté."""
    import app.admin_auth as aa
    monkeypatch.setattr(aa, "admin_connecte", lambda r: True)
    r = client.get("/admin/fonctionnalites")
    assert r.status_code == 200
    assert "Tournois" in r.text
    assert "Statistiques" in r.text
    assert "Écran de salle" in r.text
    # S4 (docs/idees-ux.md) : la légende des états était affichée en
    # permanence en haut de page -- repliée dans une aide contextuelle.
    assert 'class="aide-inline"' in r.text
    assert "Que signifient ces états ?" in r.text


def test_module_desactive_bloque_route(client, tmp_path):
    """Un module désactivé renvoie 404 sur sa route principale."""
    _set_module(tmp_path, "stats", "desactive")
    r = client.get("/stats")
    assert r.status_code == 404
    assert "désactivé" in r.text.lower() or "indisponible" in r.text.lower()


def test_module_benevoles_bloque_visiteur(client, tmp_path, monkeypatch):
    """Un module 'benevoles' est inaccessible sans jeton (→ 403).
    On définit PRET_TOKEN pour sortir du mode ouvert (sans jeton tout le monde
    est considéré bénévole, ce qui rendrait le test sans signification).
    """
    monkeypatch.setenv("PRET_TOKEN", "jeton-module-test")
    _set_module(tmp_path, "stats", "benevoles")
    # Client sans cookie → visiteur non authentifié.
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, cookies={})
    r = c.get("/stats")
    assert r.status_code == 403


def test_module_benevoles_accessible_avec_jeton(client, tmp_path, monkeypatch):
    """Un module 'benevoles' reste accessible avec le jeton bénévole."""
    monkeypatch.setenv("PRET_TOKEN", "jeton-module-test")
    _set_module(tmp_path, "stats", "benevoles")
    # Active le cookie bénévole dans le client existant.
    client.get("/acces", params={"jeton": "jeton-module-test"})
    r = client.get("/stats")
    assert r.status_code == 200


def test_module_tous_accessible_a_tous(client, tmp_path):
    """Un module 'tous' reste accessible sans jeton (comportement par défaut)."""
    _set_module(tmp_path, "stats", "tous")
    r = client.get("/stats")
    assert r.status_code == 200


def test_fonctionnalites_enregistrer(client, tmp_path, monkeypatch):
    """POST /admin/fonctionnalites enregistre les états et redirige."""
    import app.admin_auth as aa
    monkeypatch.setattr(aa, "admin_connecte", lambda r: True)
    r = client.post(
        "/admin/fonctionnalites",
        data={"module_stats": "benevoles", "module_tournois": "desactive"},
    )
    # POST-Redirect-GET : redirection vers la page de confirmation.
    assert r.status_code in (200, 303)
    # Vérification en base.
    from app import db, services
    conn = db.get_connection()
    try:
        assert services.lire_parametre(conn, "module_stats") == "benevoles"
        assert services.lire_parametre(conn, "module_tournois") == "desactive"
    finally:
        conn.close()
