"""
RETIRER UNE BOÎTE DU PARC, SANS PERDRE SON HISTORIQUE (série comptoir, lot 6).

Retirer, jamais supprimer : une boîte perdue, abîmée, donnée ou vendue quitte
le catalogue mais garde ses prêts, qui restent comptés dans les périodes où
elle était au parc. Ce fichier vérifie :

1. la migration (base d'avant, double passage) ;
2. le service : retrait, motif fermé, double envoi, boîte sortie, remise en
   service ; `preter`, `sortir_tournoi` et le transfert refusent une boîte
   retirée AU NIVEAU DU SERVICE ;
3. les écrans : `/jeu/<id>` (le QR), `/pret/<id>`, la saisie manuelle, le
   mode rangement, la fiche d'administration, la confirmation, le journal,
   le lien « Gérer cette boîte » (administrateur seulement) ;
4. ce qui ignore une boîte retirée : catalogue, disponibilité, accueil,
   étiquettes, rangement, export ;
5. les statistiques : une boîte compte dans les périodes où elle était au
   parc — retirée avant, pendant, après la période ; même résultat par
   `edition=<id>` et par la paire de dates ;
6. l'import : jamais de remise en service, avertissement, code déjà pris.

Fixtures de tests/test_fiche_jeu.py : « Catane » (CATANE, boîtes 001 et 002)
et « Dobble » (DOBBLE, boîte 003).
"""

import html
import re
import sqlite3

import pytest

from app import achats, editions, gouts, journal, services
from app.routes import admin as routes_admin
from scripts import import_csv
from tests.test_fiche_jeu import (_conn, _executer, _exporter, _importer,  # noqa: F401
                                  _journal, anonyme, bases, client)


def _retirer(id_ex="002", motif=None):
    conn = _conn()
    try:
        return services.retirer_exemplaire(conn, id_ex, motif)
    finally:
        conn.close()


def _remettre(id_ex="002"):
    conn = _conn()
    try:
        return services.remettre_en_service(conn, id_ex)
    finally:
        conn.close()


def _boite(id_ex):
    conn = _conn()
    try:
        return dict(conn.execute("SELECT * FROM exemplaires WHERE id_exemplaire = ?",
                                 (id_ex,)).fetchone())
    finally:
        conn.close()


def _texte(page):
    """Le texte de la page : sans balises, déséchappé, espaces réduits."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def _nb_prets():
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
    finally:
        conn.close()


def _pret_passe(id_ex, sortie, retour, motif="pret"):
    _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
              "VALUES (?, ?, ?, ?)", (id_ex, sortie, retour, motif))


def _retirer_le(id_ex, quand, motif="perdue"):
    _executer("UPDATE exemplaires SET retiree_le = ?, motif_retrait = ? "
              "WHERE id_exemplaire = ?", (quand, motif, id_ex))


# ===========================================================================
# 1. MIGRATION
# ===========================================================================
def test_migration_une_base_d_avant_gagne_deux_colonnes_vides(tmp_path, monkeypatch):
    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.execute("CREATE TABLE titres (reference_titre TEXT PRIMARY KEY, "
                     "nom TEXT NOT NULL)")
    ancienne.execute("CREATE TABLE exemplaires (id_exemplaire TEXT PRIMARY KEY, "
                     "reference_titre TEXT NOT NULL)")
    ancienne.execute("INSERT INTO titres VALUES ('CATAN', 'Catan')")
    ancienne.execute("INSERT INTO exemplaires VALUES ('001', 'CATAN')")
    ancienne.commit()
    ancienne.close()

    from app import db
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        db.init_db(conn)    # idempotente
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(exemplaires)")}
        assert {"retiree_le", "motif_retrait"} <= colonnes
        boite = conn.execute("SELECT retiree_le, motif_retrait FROM exemplaires").fetchone()
        assert tuple(boite) == (None, None)
        # Une base d'avant : la boîte est au parc, au catalogue, prêtable.
        assert services.dispo_par_titre(conn, "CATAN") == (1, 1)
        assert services.compter_exemplaires_disponibles(conn) == (1, 1)
        assert services.preter(conn, "001") == 1
    finally:
        conn.close()


def test_l_import_en_simulation_lit_une_base_sans_la_colonne(tmp_path):
    """La ligne de commande ne migre pas en simulation : aucune boîte retirée."""
    conn = sqlite3.connect(tmp_path / "x.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE titres (reference_titre TEXT PRIMARY KEY, nom TEXT, "
                 "type_jeu TEXT, categorie TEXT, nb_joueurs_min INT, nb_joueurs_max INT, "
                 "duree_min INT, age_min INT, editeur TEXT, auteur TEXT, "
                 "annee_edition INT, descriptif TEXT, date_achat TEXT, modifie_le TEXT)")
    conn.execute("CREATE TABLE exemplaires (id_exemplaire TEXT, reference_titre TEXT)")
    conn.execute("INSERT INTO exemplaires VALUES ('001', 'CATAN')")
    etat = import_csv.lire_etat_base(conn)
    conn.close()
    assert etat.retirees == {} and etat.exemplaires == {"001": "CATAN"}


# ===========================================================================
# 2. LE SERVICE
# ===========================================================================
def test_retirer_pose_la_date_et_le_motif(bases):
    r = _retirer("002", "abimee")
    assert r["statut"] == "retiree" and r["reference_titre"] == "CATANE"
    boite = _boite("002")
    assert boite["retiree_le"] and boite["motif_retrait"] == "abimee"
    assert boite["reference_titre"] == "CATANE"         # rien d'autre ne bouge


def test_motif_inconnu_ou_absent_enregistre_sans_motif(bases):
    assert _retirer("002", "<b>texte libre</b>")["motif"] is None
    assert _boite("002")["motif_retrait"] is None
    assert _retirer("003")["statut"] == "retiree"
    assert _boite("003")["motif_retrait"] is None


def test_les_motifs_sont_une_liste_fermee():
    # `passage` (lot comptoir-8b) n'est posé que par la libération des QR
    # éphémères : il n'est pas proposé au retrait ordinaire.
    assert list(services.MOTIFS_RETRAIT) == [
        "perdue", "abimee", "donnee", "autre", services.MOTIF_PASSAGE]
    assert list(services.MOTIFS_RETRAIT_PROPOSES) == ["perdue", "abimee", "donnee", "autre"]
    assert services.MOTIFS_RETRAIT["donnee"] == "Donnée ou vendue"


def test_double_retrait_ne_change_rien(bases):
    _retirer("002", "perdue")
    avant = _boite("002")
    r = _retirer("002", "autre")
    assert r["statut"] == "deja_retiree" and r["motif"] == "perdue"
    assert _boite("002") == avant


def test_retrait_refuse_pour_une_boite_sortie(bases):
    conn = _conn()
    try:
        services.preter(conn, "002")
    finally:
        conn.close()
    assert _retirer("002")["statut"] == "sortie"
    assert _boite("002")["retiree_le"] is None


def test_retrait_refuse_pour_une_sortie_tournoi(bases):
    conn = _conn()
    try:
        services.sortir_tournoi(conn, "002")
    finally:
        conn.close()
    assert _retirer("002")["statut"] == "sortie"


def test_retrait_d_une_boite_inconnue(bases):
    assert _retirer("999")["statut"] == "inconnue"


def test_remettre_en_service(bases):
    _retirer("002", "perdue")
    assert _remettre("002")["statut"] == "remise"
    boite = _boite("002")
    assert boite["retiree_le"] is None and boite["motif_retrait"] is None
    assert _remettre("002")["statut"] == "deja_au_parc"
    assert _remettre("999")["statut"] == "inconnue"


@pytest.mark.parametrize("ecrire", ["preter", "sortir_tournoi", "preter_si_disponible",
                                    "sortir_tournoi_si_disponible", "repreter"])
def test_le_service_refuse_de_preter_une_boite_retiree(bases, ecrire):
    _retirer("002")
    conn = _conn()
    try:
        with pytest.raises(services.BoiteRetiree):
            getattr(services, ecrire)(conn, "002")
        assert not conn.in_transaction            # rien laissé ouvert
    finally:
        conn.close()
    assert _nb_prets() == 0
    conn = _conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM pochettes WHERE occupe = 1").fetchone()[0] == 0
    finally:
        conn.close()


def test_le_transfert_vers_une_boite_retiree_n_ecrit_rien(bases):
    conn = _conn()
    try:
        services.preter(conn, "001")
        _retirer("003")
        avant = [tuple(r) for r in conn.execute("SELECT * FROM prets")]
        assert services.transferer_pochette(conn, "001", "003") == {"nouveau_retire": True}
        assert [tuple(r) for r in conn.execute("SELECT * FROM prets")] == avant
    finally:
        conn.close()


def test_le_comptoir_ne_change_pas_pour_une_boite_au_parc(bases):
    """Une boîte retirée ailleurs ne change rien au prêt des autres."""
    _retirer("003")
    conn = _conn()
    try:
        assert services.preter_si_disponible(conn, "001") == {"numero": 1}
        assert services.transferer_pochette(conn, "001", "002")["transfere"]
        assert services.rendre(conn, "002")["numero_libere"] == 1
        assert services.sortir_tournoi_si_disponible(conn, "001") == {"sorti": True}
    finally:
        conn.close()


# ===========================================================================
# 3. LES ÉCRANS
# ===========================================================================
def test_qr_d_une_boite_retiree_le_dit_sans_erreur(anonyme):
    _retirer("002")
    r = anonyme.get("/jeu/002")
    assert r.status_code == 200
    assert "Boîte retirée" in r.text
    assert "Cette boîte ne fait plus partie du catalogue." in r.text
    assert 'href="/jeu/001">Voir « Catane » au catalogue</a>' in r.text
    assert "Prêter / rendre" not in r.text and "Disponible" not in r.text


def test_qr_du_dernier_exemplaire_retire_mene_au_catalogue(anonyme):
    _retirer("003")
    r = anonyme.get("/jeu/003")
    assert r.status_code == 200
    assert '<a class="lien" href="/catalogue">Voir le catalogue</a>' in r.text


def test_page_de_pret_d_une_boite_retiree_sans_action(anonyme):
    _retirer("002")
    r = anonyme.get("/pret/002")
    assert r.status_code == 200
    assert "Boîte retirée" in r.text
    assert "ne fait plus partie du catalogue depuis le" in r.text
    assert 'action="/pret/002/preter"' not in r.text
    assert 'action="/pret/002/tournoi"' not in r.text
    assert 'href="/catalogue">Voir le catalogue</a>' in r.text


@pytest.mark.parametrize("action", ["preter", "tournoi", "repreter"])
def test_envoi_d_un_ecran_d_avant_le_retrait(anonyme, action, _journal_isole):
    """L'écran ouvert avant le retrait offrait encore le bouton : un message."""
    _retirer("002")
    r = anonyme.post(f"/pret/002/{action}")
    assert r.status_code == 200
    assert ("Cette boîte a été retirée du catalogue entre-temps. Rien n'a été "
            "enregistré.") in _texte(r.text)
    assert _nb_prets() == 0
    ligne, = [l for l in _journal(_journal_isole, {"preter": "pret", "tournoi":
              "sortie_tournoi", "repreter": "re_pret"}[action])]
    assert ligne["ok"] is False and ligne["detail"] == "retiree"


def test_saisie_manuelle_d_une_boite_retiree(anonyme):
    _retirer("002")
    r = anonyme.get("/scanner/saisie?code=2", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pret/002?saisi=1"
    page = anonyme.get(r.headers["location"]).text
    assert "Boîte retirée" in page


def test_transfert_vers_une_boite_retiree_par_l_ecran(anonyme):
    anonyme.post("/pret/001/preter")
    _retirer("003")
    r = anonyme.get("/pret/001/transfert/003")
    assert "La boîte 003 (Dobble) a été retirée du catalogue" in _texte(r.text)
    r = anonyme.post("/pret/001/transfert/003")
    assert "La boîte 003 (Dobble) a été retirée du catalogue" in _texte(r.text)
    assert "Sorti — pochette n°1" in r.text               # rien n'a bougé


def test_mode_rangement_n_affecte_pas_une_boite_retiree(client):
    _retirer("002")
    conn = _conn()
    try:
        services.affecter_emplacement(conn, "002", "evenement", "Étagère 1")
    finally:
        conn.close()
    assert _boite("002")["emplacement_evenement"] is None
    conn = _conn()
    try:
        info = services.info_exemplaire(conn, "002")
    finally:
        conn.close()
    assert "a été retirée du catalogue" in services.message_boite_retiree(info)


def test_gerer_cette_boite_pour_un_administrateur_seulement(anonyme):
    assert "Gérer cette boîte" not in anonyme.get("/pret/001").text
    from tests.test_fiche_jeu import MOT_DE_PASSE
    anonyme.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    page = anonyme.get("/pret/001").text
    assert '<a class="lien" href="/admin/jeu/CATANE#boite-001">Gérer cette boîte</a>' in page


def test_la_fiche_admin_offre_retirer_par_boite_avec_ancre(client):
    page = client.get("/admin/jeu/CATANE").text
    assert 'id="boite-001"' in page and 'id="boite-002"' in page
    assert 'href="/admin/jeu/CATANE/exemplaire/002/retirer">Retirer' in page


def test_la_confirmation_dit_ce_que_fait_et_ne_fait_pas_le_retrait(client):
    _pret_passe("002", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    avant = _boite("002")
    page = client.get("/admin/jeu/CATANE/exemplaire/002/retirer").text
    assert _boite("002") == avant                       # une lecture
    assert "<h1>Retirer la boîte 002 — Catane</h1>" in page
    assert "<h2>Ce que fait le retrait</h2>" in page
    assert "<h2>Ce qu'il ne fait pas</h2>" in page
    assert "Ses 1 prêt passé reste compté dans les statistiques" in _texte(page)
    for libelle in services.MOTIFS_RETRAIT_PROPOSES.values():
        assert f'"> {libelle}</label>' in page
    assert services.MOTIFS_RETRAIT[services.MOTIF_PASSAGE] not in page
    assert "dernière boîte" not in page
    assert "<textarea" not in page and 'type="text"' not in page   # aucun champ libre


def test_la_confirmation_annonce_le_dernier_exemplaire(client):
    page = client.get("/admin/jeu/DOBBLE/exemplaire/003/retirer").text
    assert "dernière boîte" in page


def test_retirer_depuis_l_administration(client, _journal_isole):
    r = client.post("/admin/jeu/CATANE/exemplaire/002/retirer",
                    data={"motif": "donnee"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=retiree&boite=002#boite-002"
    page = client.get(r.headers["location"]).text
    assert "La boîte 002 est retirée du catalogue." in page
    assert "retirée le" in page and "Motif : donnée ou vendue" in page
    assert "1 exemplaire · 1 boîte retirée" in page
    assert 'action="/admin/jeu/CATANE/exemplaire/002/remettre"' in page
    ligne, = _journal(_journal_isole, "exemplaire_retire")
    assert ligne["ref"] == "002" and ligne["objet"] == "Donnée ou vendue"
    assert ligne["module"] == "catalogue"


def test_double_envoi_de_la_confirmation(client, _journal_isole):
    client.post("/admin/jeu/CATANE/exemplaire/002/retirer", data={"motif": "perdue"})
    r = client.post("/admin/jeu/CATANE/exemplaire/002/retirer", data={"motif": "autre"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/admin/jeu/CATANE?ok=deja_retiree")
    assert ("La boîte 002 était déjà retirée : rien n'a changé."
            in _texte(client.get(r.headers["location"]).text))
    assert len(_journal(_journal_isole, "exemplaire_retire")) == 1
    assert _boite("002")["motif_retrait"] == "perdue"


def test_retrait_refuse_d_une_boite_sortie_avec_le_lien(client, _journal_isole):
    client.post("/pret/002/preter")
    page = client.get("/admin/jeu/CATANE/exemplaire/002/retirer").text
    assert "elle ne peut pas être retirée tant qu'elle est dehors" in _texte(page)
    assert 'href="/pret/002">Ouvrir sa page de prêt pour la rendre</a>' in page
    assert "Retirer cette boîte</button>" not in page
    r = client.post("/admin/jeu/CATANE/exemplaire/002/retirer", data={"motif": "perdue"})
    assert r.status_code == 409
    assert 'href="/pret/002">Ouvrir sa page de prêt pour la rendre</a>' in r.text
    assert _boite("002")["retiree_le"] is None
    assert _journal(_journal_isole, "exemplaire_retire") == []


def test_boite_d_un_autre_jeu_ou_inconnue(client):
    assert client.get("/admin/jeu/CATANE/exemplaire/003/retirer").status_code == 404
    r = client.post("/admin/jeu/CATANE/exemplaire/003/retirer", data={})
    assert r.status_code == 404 and "Boîte inconnue" in r.text
    assert _boite("003")["retiree_le"] is None
    assert client.post("/admin/jeu/CATANE/exemplaire/999/remettre").status_code == 404


def test_remettre_en_service_depuis_la_fiche(client, _journal_isole):
    client.post("/admin/jeu/CATANE/exemplaire/002/retirer", data={"motif": "perdue"})
    r = client.post("/admin/jeu/CATANE/exemplaire/002/remettre", follow_redirects=False)
    assert r.headers["location"] == "/admin/jeu/CATANE?ok=remise&boite=002#boite-002"
    assert "La boîte 002 est remise en service" in client.get(r.headers["location"]).text
    assert _boite("002")["retiree_le"] is None
    ligne, = _journal(_journal_isole, "exemplaire_remis_en_service")
    assert ligne["ref"] == "002" and ligne["objet"] == "Perdue"
    r = client.post("/admin/jeu/CATANE/exemplaire/002/remettre", follow_redirects=False)
    assert "ok=deja_au_parc" in r.headers["location"]
    assert len(_journal(_journal_isole, "exemplaire_remis_en_service")) == 1
    # Prêtable de nouveau.
    assert "Prêter</button>" in client.get("/pret/002").text


def test_le_message_ne_porte_que_le_code_d_une_boite_du_jeu(client):
    page = client.get("/admin/jeu/CATANE?ok=retiree&boite=003").text
    assert "La boîte est retirée du catalogue." in page
    page = client.get("/admin/jeu/CATANE?ok=retiree&boite=%3Cb%3Ex%3C/b%3E").text
    assert "<b>x</b>" not in page


def test_les_gestes_sont_gardes(anonyme):
    assert anonyme.get("/admin/jeu/CATANE/exemplaire/002/retirer",
                       follow_redirects=False).status_code in (302, 303, 401, 403)
    anonyme.post("/admin/jeu/CATANE/exemplaire/002/retirer", data={"motif": "perdue"})
    assert _boite("002")["retiree_le"] is None
    _retirer("002")
    anonyme.post("/admin/jeu/CATANE/exemplaire/002/remettre")
    assert _boite("002")["retiree_le"] is not None


def test_les_actions_du_journal_sont_au_vocabulaire():
    assert {"exemplaire_retire", "exemplaire_remis_en_service"} <= set(journal.ACTIONS)


# ===========================================================================
# 4. CE QUI IGNORE UNE BOÎTE RETIRÉE
# ===========================================================================
def test_catalogue_et_disponibilite_ignorent_la_boite(anonyme):
    _retirer("002")
    conn = _conn()
    try:
        catane, = [j for j in services.lister_catalogue(conn) if j["reference_titre"] == "CATANE"]
        assert (catane["total"], catane["disponible"], catane["id_repr"]) == (1, 1, "001")
        assert services.dispo_par_titre(conn, "CATANE") == (1, 1)
        assert services.compter_exemplaires_disponibles(conn) == (2, 2)
        assert [b["id_exemplaire"] for b in services.exemplaires_du_titre(conn, "CATANE")] == ["001"]
    finally:
        conn.close()
    assert "1 / 1 exemplaire" in anonyme.get("/jeu/001").text


def test_dernier_exemplaire_retire_jeu_absent_du_catalogue_fiche_presente(client):
    _retirer("003")
    conn = _conn()
    try:
        assert "DOBBLE" not in [j["reference_titre"] for j in services.lister_catalogue(conn)]
        assert services.get_titre(conn, "DOBBLE") is not None
    finally:
        conn.close()
    assert "Dobble" not in client.get("/catalogue").text
    page = client.get("/admin/jeu/DOBBLE")
    assert page.status_code == 200
    assert "Toutes les boîtes de ce jeu sont retirées" in page.text
    assert "Voir la fiche publique" not in page.text


def test_un_jeu_tout_retire_reste_trouvable_en_administration(client):
    _retirer("003")
    page = client.get("/admin/jeux").text
    assert 'href="/admin/jeu/DOBBLE"' in page
    assert "toutes ses boîtes sont retirées" in page
    # La liste à relier, elle, suit son compteur : pas de jeu sans boîte.
    assert 'href="/admin/jeu/DOBBLE"' not in client.get("/admin/jeux?sans_bgg=1").text
    conn = _conn()
    try:
        dobble, = [j for j in services.lister_catalogue(conn, avec_tout_retires=True)
                   if j["reference_titre"] == "DOBBLE"]
        assert (dobble["total"], dobble["disponible"], dobble["id_repr"]) == (0, 0, None)
        assert not services.lister_catalogue(conn, q="Dobble", dispo_seulement=True,
                                             avec_tout_retires=True)
    finally:
        conn.close()


def test_etiquettes_ignorent_la_boite(bases):
    _retirer("002")
    conn = _conn()
    try:
        assert [e["id_exemplaire"] for e in services.exemplaires_pour_etiquettes(conn, None)] \
            == ["001", "003"]
        assert [e["id_exemplaire"] for e in
                services.exemplaires_pour_etiquettes(conn, ["CATANE"])] == ["001"]
        catane, = [t for t in services.titres_pour_etiquettes(conn)
                   if t["reference_titre"] == "CATANE"]
        assert catane["nb_exemplaires"] == 1
    finally:
        conn.close()


def test_rangement_ignore_la_boite(bases):
    _retirer("002")
    conn = _conn()
    try:
        assert services.compter_exemplaires_sans_emplacement(conn) == 2
        r = services.affecter_emplacement_lot(conn, ["CATANE"], "evenement", "Étagère 3")
        assert r == {"titres": 1, "boites": 1}
        jeux = services.rangement_par_titre(conn, services.lister_catalogue(conn), "evenement")
        catane, = [j for j in jeux if j["reference_titre"] == "CATANE"]
        assert catane["rangement_complet"] is True
    finally:
        conn.close()
    assert _boite("002")["emplacement_evenement"] is None


def test_export_du_catalogue_sans_les_boites_retirees(client, tmp_path):
    _retirer("002")
    conn = _conn()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
        _, toutes = services.lignes_export_catalogue(conn, avec_retirees=True)
    finally:
        conn.close()
    assert entetes == services.EN_TETES_CATALOGUE
    assert [l["Code jeu"] for l in lignes] == ["001", "003"]
    assert [l["Code jeu"] for l in toutes] == ["001", "002", "003"]
    assert "002;" not in client.get("/admin/donnees/export.csv").text


def test_a_relier_ignore_un_jeu_tout_retire(bases):
    conn = _conn()
    try:
        avant = services.compter_a_relier(conn)
        _retirer("003")
        assert services.compter_a_relier(conn) == avant - 1
    finally:
        conn.close()


def test_programme_lie_a_un_jeu_tout_retire(client):
    """La base des tournois ne porte que la référence : rien ne casse."""
    _retirer("003")
    assert client.get("/programme").status_code == 200
    assert client.get("/catalogue?q=Dobble").status_code == 200


# ===========================================================================
# 5. LES STATISTIQUES — au parc PENDANT la période
# ===========================================================================
DEBUT = "2026-11-14T00:00:00+00:00"
FIN = "2026-11-16T00:00:00+00:00"


@pytest.fixture
def periode(bases):
    """Catane prêtée (001) pendant la période ; 002 et Dobble jamais sorties."""
    _pret_passe("001", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    return bases


def _chiffres(debut=DEBUT, fin=FIN):
    conn = _conn()
    try:
        d = services.collecter_stats(conn, "total", debut, fin)
    finally:
        conn.close()
    return {
        "nb_titres": d["globales"]["nb_titres"],
        "total": d["globales"]["total_prets"],
        "plus": [(j["nom"], j["nb_exemplaires"], j["nb_prets"]) for j in d["plus"]],
        "jamais": [(j["nom"], j["nb_exemplaires"]) for j in d["achats"]["jamais_sortis"]],
        "achats_titres": d["achats"]["nb_titres"],
        "gouts_titres": d["gouts"]["nb_titres"],
    }


def test_condition_au_parc_pendant():
    assert services.condition_au_parc_pendant(None, None) == ("1 = 1", [])
    assert services.condition_au_parc_pendant(DEBUT, None, "x") == (
        "(x.retiree_le IS NULL OR x.retiree_le > ?)", [DEBUT])
    assert services.condition_au_parc("x") == "x.retiree_le IS NULL"


def test_boite_retiree_apres_la_periode_ne_change_rien(periode):
    avant = _chiffres()
    _retirer_le("002", "2027-03-01T10:00:00+00:00")
    _retirer_le("003", "2027-03-01T10:00:00+00:00")
    assert _chiffres() == avant
    assert avant["plus"] == [("Catane", 2, 1)]
    assert avant["jamais"] == [("Dobble", 1)]


def test_boite_retiree_pendant_la_periode_y_compte(periode):
    avant = _chiffres()
    _retirer_le("003", "2026-11-15T12:00:00+00:00")
    assert _chiffres() == avant


def test_boite_retiree_avant_la_periode_n_y_compte_plus(periode):
    _retirer_le("002", "2026-10-01T10:00:00+00:00")
    _retirer_le("003", "2026-10-01T10:00:00+00:00")
    c = _chiffres()
    assert c["plus"] == [("Catane", 1, 1)]               # une boîte, pas deux
    assert c["jamais"] == []                              # Dobble n'était plus là
    assert c["nb_titres"] == 1 and c["achats_titres"] == 1 and c["gouts_titres"] == 1
    assert c["total"] == 1                                # le prêt reste compté


def test_toutes_periodes_comptent_toutes_les_boites(periode):
    avant = _chiffres(None, None)
    _retirer_le("003", "2026-10-01T10:00:00+00:00")
    assert _chiffres(None, None) == avant


def test_les_prets_d_une_boite_retiree_restent_comptes(periode):
    _pret_passe("002", "2026-11-14T12:00:00+00:00", "2026-11-14T13:00:00+00:00")
    avant = _chiffres()
    _retirer_le("002", "2026-12-01T10:00:00+00:00")
    assert _chiffres() == avant and avant["total"] == 2


def test_meme_resultat_par_edition_et_par_paire_de_dates(periode, anonyme):
    _retirer_le("003", "2026-10-01T10:00:00+00:00")   # avant : hors de l'édition
    _retirer_le("002", "2027-01-01T10:00:00+00:00")   # après : dedans
    conn = _conn()
    try:
        _, edition = editions.enregistrer(
            conn, {"nom": "Édition d'essai", "premier_jour": "2026-11-14",
                   "dernier_jour": "2026-11-15"})
        debut, fin = editions.periode_locale(edition)
    finally:
        conn.close()
    par_edition = anonyme.get(f"/stats?edition={edition['id']}").text
    par_dates = anonyme.get(f"/stats?debut={debut}&fin={fin}").text
    motif = re.compile(r'<span class="chiffre-val">(\d+) / (\d+)</span>')
    assert motif.search(par_edition).groups() == motif.search(par_dates).groups() == ("1", "1")


# ===========================================================================
# 6. L'IMPORT DU CATALOGUE
# ===========================================================================
ENTETE = "Code jeu;Nom jeu;Age joueurs"


def _csv(tmp_path, contenu):
    chemin = tmp_path / "cat.csv"
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_l_import_ne_remet_jamais_en_service(client, tmp_path):
    _retirer("002", "perdue")
    res = _importer(_csv(tmp_path, f"{ENTETE}\n001;Catane;10\n002;Catane;10\n003;Dobble;6\n"))
    assert _boite("002")["retiree_le"] is not None
    assert res["exemplaires"] == 2
    assert res["retirees_ecartees"] == [{"code": "002", "nom_fichier": "Catane",
                                         "nom_base": "Catane", "meme_jeu": True}]
    phrases = import_csv.phrases_retirees(res["retirees_ecartees"])
    assert phrases == ["1 boîte retirée est encore dans le tableur : 002 (Catane). "
                       "Elle reste retirée : supprimez sa ligne du tableur."]


def test_l_import_ne_retire_jamais_une_boite_absente(client, tmp_path):
    _importer(_csv(tmp_path, f"{ENTETE}\n001;Catane;10\n"))
    assert _boite("002")["retiree_le"] is None and _boite("003")["retiree_le"] is None


def test_un_code_de_boite_retiree_donne_a_un_autre_jeu(client, tmp_path):
    _retirer("003")
    avant = _boite("003")
    res = _importer(_csv(tmp_path, f"{ENTETE}\n003;Azul;8\n"))
    assert _boite("003") == avant                          # toujours Dobble, retirée
    conn = _conn()
    try:
        assert services.get_titre(conn, "AZUL") is None      # aucun jeu fantôme
    finally:
        conn.close()
    assert import_csv.phrases_retirees(res["retirees_ecartees"]) == [
        "Le code 003 est celui d'une boîte retirée de « Dobble » : la ligne "
        "« Azul » n'a pas été importée. Donnez-lui un autre code dans le tableur."]


def test_la_simulation_annonce_les_boites_retirees(client, tmp_path):
    _retirer("002")
    chemin = _csv(tmp_path, f"{ENTETE}\n002;Catane;10\n")
    assert (_importer(chemin, dry_run=True)["retirees_ecartees"]
            == _importer(chemin)["retirees_ecartees"])


def test_l_import_depuis_l_administration_le_dit(client, tmp_path):
    _retirer("002")
    contenu = f"{ENTETE}\n001;Catane;10\n002;Catane;10\n".encode()
    r = client.post("/admin/donnees/import",
                    files={"fichier": ("cat.csv", contenu, "text/csv")})
    assert "Import réussi" in r.text
    assert "1 boîte retirée est encore dans le tableur : 002 (Catane)." in r.text


def test_l_aller_retour_export_import_ne_dit_rien(client, tmp_path):
    _retirer("002")
    res = _importer(_exporter(tmp_path / "export.csv"))
    assert res["retirees_ecartees"] == []
    assert _boite("002")["retiree_le"] is not None


def test_les_phrases_d_un_long_tableur_sont_coupees():
    ecartees = [{"code": f"{i:03}", "nom_fichier": "X", "nom_base": "X", "meme_jeu": True}
                for i in range(15)]
    phrase, = import_csv.phrases_retirees(ecartees)
    assert phrase.startswith("15 boîtes retirées sont encore dans le tableur")
    assert "et 5 autre(s)" in phrase
