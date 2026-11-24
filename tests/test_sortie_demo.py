"""
SORTIR UNE BOÎTE POUR UNE DÉMO (lot comptoir-3) — et la règle unique des
sorties SANS POCHETTE (`services.SORTIES_SANS_POCHETTE`) qui la porte.

Une démo, c'est une boîte montrée à une table : ni un prêt au public, ni un
tournoi. Comme le tournoi : sans pièce d'identité, sans pochette (numéro 0),
hors statistiques. Les lecteurs du motif testés ici, un par un : écran de
prêt, retour, transfert et échange, `/sortis`, fiche, écran de salle,
journal, clôture, statistiques (comptoir, palmarès, durée, achats, goûts),
organisation et exports.

La non-régression du tournoi est double : les tests d'avant passent
inchangés (sauf un, dit dans le compte rendu), et plusieurs tests ci-dessous
comparent la démo au tournoi sur les mêmes lignes.

Fixture : 001/Catan, 002/Dixit, 003/Catan, 004/Azul.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from app import services

DEMO = services.MOTIF_DEMO
TOURNOI = services.MOTIF_TOURNOI
LIBELLE_DEMO = "Sortir pour une démo"


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    conn.executemany("INSERT INTO titres (reference_titre, nom) VALUES (?, ?)",
                     [("CATAN", "Catan"), ("DIXIT", "Dixit"), ("AZUL", "Azul")])
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("001", "CATAN"), ("002", "DIXIT"), ("003", "CATAN"), ("004", "AZUL")],
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


@pytest.fixture
def conn(client):
    from app import db
    c = db.get_connection()
    yield c
    c.close()


def _prets(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM prets ORDER BY id_pret")]


def _journal(chemin):
    if not chemin.exists():
        return []
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ===========================================================================
# La règle
# ===========================================================================
def test_la_regle_porte_le_tournoi_puis_la_demo():
    assert list(services.SORTIES_SANS_POCHETTE) == [TOURNOI, DEMO]
    assert services.sortie_sans_pochette(DEMO)["nom"] == "démo"
    for motif in ("pret", "erreur", "oubli", None):
        assert services.sortie_sans_pochette(motif) is None
    # Le repli d'un refus qui ne porte pas son motif : les mots du tournoi.
    assert services.sortie_sans_pochette(None, TOURNOI)["nom"] == "tournoi"


def test_aucune_sortie_sans_pochette_n_est_comptee():
    assert not set(services.SORTIES_SANS_POCHETTE) & set(services.MOTIFS_COMPTES)


def test_chaque_action_de_journal_est_au_vocabulaire_ferme():
    from app import journal
    for mots in services.SORTIES_SANS_POCHETTE.values():
        assert mots["action_journal"] in journal.ACTIONS
    assert "sortie_demo" in journal.ACTIONS


def test_chaque_sortie_a_sa_route_protegee_par_le_jeton():
    from app.auth import exiger_jeton
    from app.main import app
    for mots in services.SORTIES_SANS_POCHETTE.values():
        chemin = f"/pret/{{id_exemplaire}}/{mots['url']}"
        routes = [r for r in app.routes if getattr(r, "path", None) == chemin]
        assert routes and "POST" in routes[0].methods
        assert any(d.call is exiger_jeton for d in routes[0].dependant.dependencies)


def test_un_motif_inconnu_est_refuse_par_le_service(conn):
    with pytest.raises(ValueError):
        services.sortir_sans_pochette(conn, "001", "pret")
    assert _prets(conn) == []


# ===========================================================================
# Le service
# ===========================================================================
def test_sortie_demo_ligne_motif_demo_numero_zero(conn):
    assert services.sortir_sans_pochette_si_disponible(conn, "001", DEMO) == {"sorti": True}
    (ligne,) = _prets(conn)
    assert (ligne["motif"], ligne["numero_pochette"], ligne["date_retour"]) == (DEMO, 0, None)
    assert services.pret_en_cours(conn, "001") is not None


def test_sortie_demo_ne_touche_pas_aux_pochettes(conn):
    avant = [tuple(r) for r in conn.execute("SELECT * FROM pochettes")]
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    services.rendre(conn, "001")
    assert [tuple(r) for r in conn.execute("SELECT * FROM pochettes")] == avant


def test_double_sortie_demo_deja_sorti(conn):
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    assert services.sortir_sans_pochette_si_disponible(conn, "001", DEMO) == {
        "deja_sorti": True, "numero": 0}
    assert len(_prets(conn)) == 1


def test_demo_sur_une_boite_pretee_deja_sorti(conn):
    numero = services.preter(conn, "001")
    assert services.sortir_sans_pochette_si_disponible(conn, "001", DEMO) == {
        "deja_sorti": True, "numero": numero}


def test_demo_concurrente_une_seule_sortie(client, tmp_path):
    """Deux bénévoles sortent la même boîte au même instant : deux
    connexions, la seconde trouve la boîte déjà sortie (même verrou
    d'écriture que le prêt)."""
    from app import db
    a, b = db.get_connection(), db.get_connection()
    try:
        assert services.sortir_sans_pochette_si_disponible(a, "001", DEMO) == {"sorti": True}
        assert services.sortir_sans_pochette_si_disponible(b, "001", DEMO)["deja_sorti"]
        assert a.execute("SELECT COUNT(*) FROM prets").fetchone()[0] == 1
    finally:
        a.close()
        b.close()


def test_boite_retiree_refusee_par_le_service(conn):
    services.retirer_exemplaire(conn, "001")
    with pytest.raises(services.BoiteRetiree):
        services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    assert _prets(conn) == []


def test_retour_de_demo_renvoie_le_motif(conn):
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    assert services.rendre(conn, "001") == {"motif": DEMO}
    (ligne,) = _prets(conn)
    assert ligne["date_retour"] is not None and ligne["numero_pochette"] is None


def test_retour_immediat_d_une_demo_garde_son_motif(conn):
    """Rendue en moins d'une minute, une démo n'est pas « erreur »."""
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    services.rendre(conn, "001")
    assert _prets(conn)[0]["motif"] == DEMO


def test_demo_oubliee_close_par_un_re_pret_garde_son_motif(conn):
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    res = services.repreter(conn, "001")
    assert res["ancien_numero"] == 0        # comme un tournoi : aucun casier
    assert _prets(conn)[0]["motif"] == DEMO


def test_transfert_depuis_une_demo_refuse_sans_pochette(conn):
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    assert services.transferer_pochette(conn, "001", "002") == {
        "sans_pochette": True, "motif": DEMO}
    assert len(_prets(conn)) == 1


def test_cloture_ferme_les_demos(conn):
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    services.sortir_tournoi(conn, "002")
    services.preter(conn, "003")
    assert services.cloturer_tous_les_prets(conn) == 3
    assert all(l["date_retour"] for l in _prets(conn))
    assert _prets(conn)[0]["motif"] == DEMO


# ===========================================================================
# L'écran de prêt
# ===========================================================================
def test_bouton_demo_sous_le_tournoi_et_preter_d_abord(client):
    r = client.get("/pret/002")
    i_preter = r.text.index('action="/pret/002/preter"')
    i_echange = r.text.index('href="/pret/002/echange"')
    i_tournoi = r.text.index('action="/pret/002/tournoi"')
    i_demo = r.text.index('action="/pret/002/demo"')
    assert i_preter < i_echange < i_tournoi < i_demo
    bloc = r.text[i_demo:r.text.index("</form>", i_demo)]
    assert LIBELLE_DEMO in bloc
    assert '<span class="bouton-sous">sans pièce d\'identité, hors statistiques</span>' in bloc
    assert 'class="bouton bouton-secondaire"' in bloc


def test_bouton_tournoi_inchange(client):
    r = client.get("/pret/002")
    assert ('<form method="post" action="/pret/002/tournoi">\n'
            '        <button class="bouton bouton-secondaire" type="submit">\n'
            '          Sortir pour un tournoi <span class="bouton-sous">sans pièce '
            "d'identité, hors statistiques</span>") in r.text


def test_pas_de_bouton_demo_sur_une_boite_sortie_ou_retiree(client, conn):
    client.post("/pret/001/preter")
    assert LIBELLE_DEMO not in client.get("/pret/001").text
    client.post("/pret/003/demo")
    assert LIBELLE_DEMO not in client.get("/pret/003").text
    services.retirer_exemplaire(conn, "004")
    r = client.get("/pret/004")
    assert LIBELLE_DEMO not in r.text
    assert "ni sortie pour un tournoi ou une démo" in r.text


def test_sortir_pour_une_demo_puis_rendre(client):
    r = client.post("/pret/001/demo")
    assert ("Sorti pour une démo (sans pochette). Ce jeu n'est pas "
            "comptabilisé dans les statistiques.") in r.text
    assert '<p class="etat etat-sorti">Sorti — démo</p>' in r.text
    assert "Rendre <span class=\"bouton-sous\">retour de démo</span>" in r.text
    # Un seul bouton, comme le tournoi : ni transfert, ni re-prêt.
    assert "/pret/001/transfert" not in r.text and "/pret/001/repreter" not in r.text
    r = client.post("/pret/001/rendre")
    assert "Retour de démo enregistré." in r.text
    assert "pochette-num--retour" not in r.text
    assert '<p class="etat etat-dispo">Disponible</p>' in r.text


def test_messages_du_tournoi_inchanges(client):
    r = client.post("/pret/001/tournoi")
    assert ("Sorti pour un tournoi (sans pochette). Ce jeu n'est pas "
            "comptabilisé dans les statistiques.") in r.text
    assert '<p class="etat etat-sorti">Sorti — tournoi</p>' in r.text
    assert "Rendre <span class=\"bouton-sous\">retour de tournoi</span>" in r.text
    assert "Retour de tournoi enregistré." in client.post("/pret/001/rendre").text


def test_double_envoi_demo_deja_sorti(client):
    client.post("/pret/001/demo")
    r = client.post("/pret/001/demo")
    assert "Cet exemplaire était déjà sorti" in r.text
    assert r.status_code == 200


def test_demo_sur_boite_retiree_par_la_route(client, conn):
    services.retirer_exemplaire(conn, "001")
    r = client.post("/pret/001/demo")
    assert "retirée du catalogue entre-temps" in r.text
    assert _prets(conn) == []


def test_demo_boite_inconnue_404(client):
    assert client.post("/pret/999/demo").status_code == 404


def test_retour_de_demo_dit_ou_ranger(client, conn):
    conn.execute("UPDATE exemplaires SET emplacement_evenement = 'Table 7' "
                 "WHERE id_exemplaire = '001'")
    conn.commit()
    client.post("/pret/001/demo")
    r = client.post("/pret/001/rendre")
    assert "Où ranger le jeu" in r.text and "Table 7" in r.text


def test_transfert_depuis_une_demo_message(client):
    client.post("/pret/001/demo")
    r = client.post("/pret/001/transfert/002")
    assert ("Cette sortie est une démo : il n'y a pas de pièce d'identité "
            "à transférer.") in r.text
    r = client.get("/pret/001/transfert")
    assert "Cette sortie est une démo" in r.text


def test_transfert_depuis_un_tournoi_message_inchange(client):
    client.post("/pret/001/tournoi")
    r = client.post("/pret/001/transfert/002")
    assert ("Cette sortie est un prêt de tournoi : il n'y a pas de pièce "
            "d'identité à transférer.") in r.text


def test_echange_depuis_une_demo(client):
    """Le visiteur rapporte une boîte sortie en démo : même écran que pour un
    tournoi, avec le mot « démo »."""
    client.post("/pret/003/demo")
    r = client.get("/pret/002/echange/003")
    assert r.status_code == 200
    assert ("<strong>Catan</strong> est sorti pour une démo : il\n"
            "       n'a pas de pièce d'identité.") in r.text
    assert '<span class="bouton-sous">retour de démo</span>' in r.text
    assert '<form method="post" action="/pret/002/preter">' in r.text


def test_escalade_vers_une_boite_sortie_en_demo(client):
    """La boîte que le visiteur veut emporter est notée sortie en démo :
    le bloc d'escalade le dit, sans casier à vérifier."""
    client.post("/pret/003/preter")
    client.post("/pret/001/demo")
    confirmation = client.get("/pret/003/transfert/001").text
    assert "pour une démo (sans pochette)" in confirmation
    assert "pour un tournoi" not in confirmation
    r = client.post("/pret/003/transfert/001")
    assert "pour une démo (sans pochette)" in r.text
    r = client.post("/pret/003/transfert/001", data={"clore_oubli": "1"})
    assert "Pochette inchangée" in r.text


# ===========================================================================
# Jeux sortis, fiche, retrait, écran de salle
# ===========================================================================
def test_sortis_section_en_demo(client):
    client.post("/pret/001/preter")
    client.post("/pret/002/tournoi")
    client.post("/pret/003/demo")
    r = client.get("/sortis")
    assert "3 jeux dehors en ce moment, dont 1 en tournoi et 1 en démo." in r.text
    i_t = r.text.index("<h2>En tournoi <small>(1)</small></h2>")
    i_d = r.text.index("<h2>En démo <small>(1)</small></h2>")
    assert i_t < i_d
    section = r.text[i_d:]
    assert "Catan" in section and "Boîte 003" in section
    # « démo » se dit en TEXTE dans la pastille, pas par sa seule classe.
    assert '<span class="sortie-pochette sortie-pochette--tournoi">démo</span>' in section
    assert "pochette <strong>" not in section


def test_sortis_sections_vides(client):
    r = client.get("/sortis")
    assert "<h2>En tournoi <small>(0)</small></h2>" in r.text
    assert "<h2>En démo <small>(0)</small></h2>" in r.text


def test_sortis_tournoi_seul_resume_inchange(client):
    client.post("/pret/002/tournoi")
    assert "1 jeu dehors en ce moment, dont 1 en tournoi." in client.get("/sortis").text


def test_sortis_recherche_couvre_la_demo(client):
    client.post("/pret/003/demo")
    client.post("/pret/002/tournoi")
    r = client.get("/sortis", params={"q": "catan"})
    assert "1 boîte trouvée pour « catan »," in r.text
    assert "<h2>En démo <small>(1)</small></h2>" in r.text
    assert "<h2>En tournoi <small>(0)</small></h2>" in r.text


def test_une_demo_n_est_jamais_une_longue_sortie(conn):
    services.sortir_sans_pochette_si_disponible(conn, "001", DEMO)
    plus_tard = datetime.now(timezone.utc) + timedelta(hours=10)
    (ligne,) = services.lister_prets_en_cours(conn, plus_tard)[DEMO]
    assert ligne["longue"] is False


def test_fiche_benevole_en_demo_depuis(client):
    client.post("/pret/003/demo")
    r = client.get("/jeu/001")
    assert '<span class="boites-sortie">en démo depuis' in r.text


def test_fiche_benevole_tournoi_inchangee(client):
    client.post("/pret/003/tournoi")
    assert '<span class="boites-sortie">en tournoi depuis' in client.get("/jeu/001").text


def test_retrait_refuse_d_une_boite_en_demo(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-123")
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-123"})
    client.post("/pret/001/demo")
    r = client.get("/admin/jeu/CATAN/exemplaire/001/retirer")
    assert "Cette boîte est sortie pour une démo :" in r.text


def test_ecran_de_salle_dit_demo(client):
    client.post("/pret/001/demo")
    client.post("/pret/002/tournoi")
    client.post("/pret/003/preter")
    client.post("/pret/001/rendre")
    mouvements = client.get("/live/data").json()["mouvements"]
    genres = {(m["type"], m["nom"], m["genre"]) for m in mouvements}
    assert ("pret", "Catan", "démo") in genres
    assert ("pret", "Dixit", "tournoi") in genres
    assert ("pret", "Catan", "prêt") in genres
    assert ("retour", "Catan", "retour") in genres
    # Le motif reste publié tel quel, et jamais un numéro de pochette.
    assert {m["motif"] for m in mouvements} >= {DEMO, TOURNOI, "pret"}
    assert all("numero" not in json.dumps(m) for m in mouvements)


def test_ecran_de_salle_le_js_lit_le_mot_du_serveur(client):
    html = client.get("/live").text
    assert "const genre = m.genre;" in html
    assert 'm.motif === "tournoi"' not in html


# ===========================================================================
# Journal
# ===========================================================================
def test_journal_sortie_demo_sans_numero(client, _journal_isole):
    client.post("/pret/001/demo")
    client.post("/pret/001/demo")
    client.post("/pret/001/rendre")
    lignes = [l for l in _journal(_journal_isole) if l["action"] == "sortie_demo"]
    assert [(l["ok"], l.get("detail")) for l in lignes] == [(True, None), (False, "deja_sorti")]
    assert lignes[0]["objet"] == "Catan" and lignes[0]["ref"] == "CATAN"
    retour = [l for l in _journal(_journal_isole) if l["action"] == "retour"][-1]
    assert retour["ok"] is True
    texte = _journal_isole.read_text(encoding="utf-8")
    assert "pochette" not in texte and "numero" not in texte


def test_journal_tournoi_inchange(client, _journal_isole):
    client.post("/pret/001/tournoi")
    actions = [l["action"] for l in _journal(_journal_isole)]
    assert actions == ["sortie_tournoi"]


# ===========================================================================
# Statistiques : une démo ne compte nulle part
# ===========================================================================
JOUR = datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc)   # 10 h à Paris


def _iso(heures: float) -> str:
    return (JOUR + timedelta(hours=heures)).isoformat(timespec="seconds")


def _base_stats(conn, motif_hors_stats: str | None):
    """Une journée de prêts au public, plus — si `motif_hors_stats` — des
    sorties sans pochette du même motif : closes, longues, sur des jeux
    prêtés ET sur un jeu jamais prêté au public (Azul), et une encore
    ouverte."""
    lignes = [
        ("001", _iso(0), _iso(2), "pret"),
        ("002", _iso(1), _iso(1.5), "pret"),
        ("001", _iso(3), _iso(5), "pret"),
        ("003", _iso(4), None, "pret"),
    ]
    if motif_hors_stats:
        lignes += [
            ("002", _iso(2), _iso(4), motif_hors_stats),
            ("004", _iso(0.5), _iso(6), motif_hors_stats),
            ("001", _iso(6), _iso(6.01), motif_hors_stats),
            ("002", _iso(5), None, motif_hors_stats),
        ]
    conn.executemany(
        "INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, date_retour, motif) "
        "VALUES (?, NULL, ?, ?, ?)", lignes)
    conn.commit()


def _sans_drapeaux(stats: dict) -> dict:
    """`collecter_stats` sans les drapeaux « sorti seulement en … » des
    jamais sortis — le seul endroit où une sortie sans pochette DOIT se voir."""
    copie = json.loads(json.dumps(stats, default=str))
    for j in copie["achats"]["jamais_sortis"]:
        for m in services.SORTIES_SANS_POCHETTE:
            j.pop(f"{m}_seulement")
    return copie


@pytest.mark.parametrize("motif", [DEMO, TOURNOI])
def test_stats_identiques_avec_ou_sans_sorties_sans_pochette(client, conn, tmp_path, motif):
    """Chaque lecteur des statistiques — synthèse, durée, comptoir heure par
    heure, palmarès, achats, goûts, liste détaillée — rend la même chose avec
    ou sans ces sorties."""
    _base_stats(conn, None)
    sans = _sans_drapeaux(services.collecter_stats(conn))
    autre = sqlite3.connect(tmp_path / "test.db")
    autre.execute("DELETE FROM prets")
    autre.commit()
    autre.close()
    _base_stats(conn, motif)
    avec = _sans_drapeaux(services.collecter_stats(conn))
    assert avec == sans
    assert avec["globales"]["total_prets"] == 4


def test_jamais_sortis_signale_la_demo_a_part(conn):
    from app import achats, comptoir
    _base_stats(conn, DEMO)
    donnees = comptoir.heure_par_heure(conn)
    jamais = {j["reference_titre"]: j for j in
              achats.pour_les_achats(conn, None, None, donnees)["jamais_sortis"]}
    assert jamais["AZUL"]["demo_seulement"] is True
    assert jamais["AZUL"]["tournoi_seulement"] is False
    assert achats.ENTETES_XLSX_JAMAIS_SORTIS[-2:] == (
        "Sorti en tournoi seulement", "Sorti en démo seulement")


# ===========================================================================
# Organisation et exports
# ===========================================================================
def test_organisation_compte_les_demos_a_part(conn):
    from app import organisation
    _base_stats(conn, DEMO)
    assert organisation.sorties_sans_pochette(conn) == {TOURNOI: 0, DEMO: 4}
    assert organisation.sorties_sans_pochette(conn, _iso(1), _iso(5.5)) == {TOURNOI: 0, DEMO: 2}
    assert organisation.lignes_sorties_demo(4) == [
        "Sorties « démo » du prêt : 4.", organisation.NOTE_SORTIES_DEMO]
    assert "tournoi" not in organisation.NOTE_SORTIES_DEMO
    assert "démo" not in organisation.NOTE_SORTIES_TOURNOI
    assert organisation.phrase_sorties_tournoi(3) == "Sorties « tournoi » du prêt : 3."


def test_page_stats_organisation_phrase_demo(client):
    client.post("/pret/001/demo")
    client.post("/pret/002/tournoi")
    r = client.get("/stats")
    assert "<p>Sorties « tournoi » du prêt : 1.</p>" in r.text
    assert "<p>Sorties « démo » du prêt : 1.</p>" in r.text
    # La démo suit le travail du comptoir (sous les prêts encore ouverts),
    # le tournoi reste dans son volet ; chacune avec sa note.
    i_ouverts = r.text.index('<p class="org-ouverts">')
    i_demo = r.text.index("Sorties « démo »")
    i_volet = r.text.index('<details class="org-volet" id="tournois">')
    assert i_ouverts < i_demo < i_volet < r.text.index("Sorties « tournoi »")
    from app import organisation
    from markupsafe import escape
    assert r.text.count(escape(organisation.NOTE_SORTIES_DEMO)) == 1
    assert r.text.count(escape(organisation.NOTE_SORTIES_TOURNOI)) == 1


def test_page_stats_phrase_demo_meme_a_zero(client):
    client.post("/pret/002/tournoi")
    assert "<p>Sorties « démo » du prêt : 0.</p>" in client.get("/stats").text


def test_page_stats_module_tournois_masque_la_demo_reste(client, conn):
    """Une démo ne dépend pas des tournois : module masqué, la phrase de la
    démo et sa note restent, celle du tournoi disparaît."""
    from markupsafe import escape
    from app import organisation
    services.ecrire_parametre(conn, "module_tournois", "desactive")
    conn.commit()
    client.post("/pret/001/demo")
    client.post("/pret/002/tournoi")
    html = client.get("/stats").text
    assert "<p>Sorties « démo » du prêt : 1.</p>" in html
    assert escape(organisation.NOTE_SORTIES_DEMO) in html
    assert "Sorties « tournoi »" not in html
    assert escape(organisation.NOTE_SORTIES_TOURNOI) not in html


def test_page_stats_tournois_et_programme_masques_la_demo_reste(client, conn):
    services.ecrire_parametre(conn, "module_tournois", "desactive")
    services.ecrire_parametre(conn, "module_programme", "desactive")
    conn.commit()
    client.post("/pret/001/demo")
    html = client.get("/stats").text
    assert 'id="tournois"' not in html
    assert "<p>Sorties « démo » du prêt : 1.</p>" in html


def test_exports_module_tournois_masque_la_demo_reste(client, conn):
    import openpyxl
    from app import organisation
    from tests.outils_pdf import texte_pdf
    services.ecrire_parametre(conn, "module_tournois", "desactive")
    conn.commit()
    client.post("/pret/001/demo")
    client.post("/pret/002/tournoi")
    classeur = openpyxl.load_workbook(BytesIO(client.get("/stats/export.xlsx").content))
    plat = [str(v) for f in classeur for l in f.iter_rows(values_only=True)
            for v in l if v is not None]
    assert "Sorties « démo » du prêt : 1." in plat
    assert organisation.NOTE_SORTIES_DEMO in plat
    assert not any("Sorties « tournoi »" in c for c in plat)
    texte = texte_pdf(client.get("/stats/export.pdf").content)
    assert "Sorties « démo » du prêt : 1." in " ".join(texte.split())
    assert "Sorties « tournoi »" not in texte


def test_exports_portent_la_demo(client):
    import openpyxl
    from app import achats, exports
    from tests.outils_pdf import texte_pdf
    client.post("/pret/001/demo")
    client.post("/pret/004/demo")
    client.post("/pret/004/rendre")
    classeur = openpyxl.load_workbook(BytesIO(client.get("/stats/export.xlsx").content))
    plat = [str(v) for f in classeur for l in f.iter_rows(values_only=True)
            for v in l if v is not None]
    assert "Sorties « démo » du prêt : 2." in plat
    assert "Sorti en démo seulement" in plat
    feuille = classeur[achats.FEUILLE_JAMAIS_SORTIS]
    azul = [l for l in feuille.iter_rows(values_only=True) if l[0] == "Azul"][0]
    assert azul[-2:] == (None, "oui")
    texte = texte_pdf(client.get("/stats/export.pdf").content)
    assert "démo" in texte and "du prêt : 2." in texte
