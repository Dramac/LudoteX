"""
L'ÉCHANGE (lot comptoir-7) : le transfert de pochette pris par le jeu qui
SORT. Le bénévole scanne d'abord le jeu que le visiteur veut (B, disponible),
puis celui qu'il rapporte (A) ; la confirmation poste vers la route de
transfert existante, `/pret/<A>/transfert/<B>`.

Le service et le POST sont testés ailleurs (`test_transfert_pochette.py`,
`test_transfert_routes.py`) et ne sont pas touchés. Ici : le bouton, les trois
écrans GET de l'échange, chaque cas limite du prompt, le bout en bout, le fait
que rien ne s'écrit avant « Confirmer », et la non-régression du sens habituel.

Fixture : 001/Catan, 002/Dixit, 003/Catan, 004/Azul.
"""

import pytest

LIBELLE_ECHANGE = "Échanger contre un jeu rendu"


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
        [("001", "CATAN"), ("002", "DIXIT"), ("003", "CATAN"), ("004", "AZUL"),
         ("E01", "AZUL")],
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _prets():
    """Toute la table `prets`, pour vérifier qu'un écran n'a rien écrit."""
    from app import db
    conn = db.get_connection()
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM prets ORDER BY rowid")]
    finally:
        conn.close()


def _en_cours(id_exemplaire):
    from app import db, services
    conn = db.get_connection()
    try:
        return services.pret_en_cours(conn, id_exemplaire)
    finally:
        conn.close()


def _retirer(id_exemplaire):
    from app import db, services
    conn = db.get_connection()
    try:
        services.retirer_exemplaire(conn, id_exemplaire)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POINT 1 — Le bouton sur l'écran « Disponible »
# ---------------------------------------------------------------------------
def test_bouton_present_sur_une_boite_disponible_entre_preter_et_tournoi(client):
    r = client.get("/pret/002")
    assert r.status_code == 200
    assert 'href="/pret/002/echange"' in r.text
    assert LIBELLE_ECHANGE in r.text
    # Prêter d'abord (ordre du DOM = ordre de tabulation), puis l'échange,
    # puis le tournoi.
    i_preter = r.text.index('action="/pret/002/preter"')
    i_echange = r.text.index('href="/pret/002/echange"')
    i_tournoi = r.text.index('action="/pret/002/tournoi"')
    assert i_preter < i_echange < i_tournoi
    # Une variante existante, secondaire : Prêter reste le seul principal.
    debut = r.text.rindex("<a", 0, i_echange)
    assert 'class="bouton bouton-secondaire"' in r.text[debut:i_echange]


def test_bouton_absent_sur_une_boite_sortie(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001")
    assert "/echange" not in r.text
    assert LIBELLE_ECHANGE not in r.text


def test_bouton_absent_sur_une_sortie_tournoi(client):
    client.post("/pret/003/tournoi")
    r = client.get("/pret/003")
    assert "/echange" not in r.text


def test_bouton_absent_sur_une_boite_retiree(client):
    _retirer("002")
    r = client.get("/pret/002")
    assert "Boîte retirée" in r.text
    assert "/echange" not in r.text


# ---------------------------------------------------------------------------
# POINT 2 — L'écran « scannez le jeu rendu »
# ---------------------------------------------------------------------------
def test_les_trois_routes_exigent_le_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    assert client.get("/pret/002/echange").status_code == 403
    assert client.get("/pret/002/echange/saisie",
                      params={"code": "001"}).status_code == 403
    assert client.get("/pret/002/echange/001").status_code == 403


def test_chaque_route_d_echange_porte_sa_garde():
    from fastapi.routing import APIRoute

    from app.auth import exiger_jeton
    from app.main import app

    chemins = {"/pret/{id_nouveau}/echange", "/pret/{id_nouveau}/echange/saisie",
               "/pret/{id_nouveau}/echange/{id_rendu}"}
    trouvees = [r for r in app.routes if isinstance(r, APIRoute) and r.path in chemins]
    assert {r.path for r in trouvees} == chemins
    for route in trouvees:
        assert route.methods == {"GET"}
        assert any(d.call is exiger_jeton for d in route.dependant.dependencies)


def test_ecran_d_echange_nomme_b_et_pose_la_cible(client):
    r = client.get("/pret/002/echange")
    assert r.status_code == 200
    assert r.text.count("<h1") == 1
    assert "<h1>Échanger contre un jeu rendu</h1>" in r.text
    assert "<strong>Dixit</strong> — scannez le jeu que le visiteur rapporte." in r.text
    assert 'data-scan-cible="/pret/002/echange/"' in r.text
    assert 'action="/pret/002/echange/saisie"' in r.text
    assert 'href="/pret/002">Annuler' in r.text
    # Le statut de caméra reste annoncé aux lecteurs d'écran.
    assert 'id="statut" aria-live="polite"' in r.text
    # Rien du sens habituel ne fuit.
    assert "/transfert" not in r.text


def test_ecran_d_echange_b_inconnue_404(client):
    assert client.get("/pret/999/echange").status_code == 404


def test_ecran_d_echange_b_sortie_montre_l_ecran_de_b(client):
    client.post("/pret/002/preter")
    r = client.get("/pret/002/echange")
    assert r.status_code == 200
    assert "Sorti — pochette n°" in r.text
    assert "data-scan-cible" not in r.text


def test_ecran_d_echange_b_retiree_montre_l_ecran_de_b(client):
    _retirer("002")
    r = client.get("/pret/002/echange")
    assert "Boîte retirée" in r.text
    assert "data-scan-cible" not in r.text


def test_clavier_a_lettres_sur_l_ecran_d_echange(client):
    r = client.get("/pret/002/echange", params={"lettres": "1"})
    assert 'inputmode="text"' in r.text
    assert 'name="lettres" value="1"' in r.text


# ---------------------------------------------------------------------------
# Saisie manuelle
# ---------------------------------------------------------------------------
def test_saisie_declaree_avant_la_route_a_parametre():
    from fastapi.routing import APIRoute

    from app.main import app

    chemins = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert (chemins.index("/pret/{id_nouveau}/echange/saisie")
            < chemins.index("/pret/{id_nouveau}/echange/{id_rendu}"))


def test_saisie_resout_le_code_et_redirige_sans_ecrire(client):
    client.post("/pret/001/preter")
    avant = _prets()
    r = client.get("/pret/002/echange/saisie", params={"code": " 1 "},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/pret/002/echange/001"
    assert _prets() == avant


def test_saisie_tolere_la_casse(client):
    r = client.get("/pret/002/echange/saisie", params={"code": "e01"},
                   follow_redirects=False)
    assert r.headers["location"] == "/pret/002/echange/E01"


def test_saisie_code_introuvable_reaffiche_avec_message_et_lettres(client):
    r = client.get("/pret/002/echange/saisie",
                   params={"code": "zzz", "lettres": "1"})
    assert r.status_code == 200
    assert "Aucune boîte ne porte le code « zzz »" in r.text
    assert 'value="zzz"' in r.text
    assert 'inputmode="text"' in r.text
    assert 'data-scan-cible="/pret/002/echange/"' in r.text


def test_saisie_vide(client):
    r = client.get("/pret/002/echange/saisie", params={"code": ""})
    assert "Veuillez saisir un code." in r.text


def test_saisie_garde_le_clavier_a_lettres_sur_la_redirection(client):
    r = client.get("/pret/002/echange/saisie", params={"code": "E01", "lettres": "1"},
                   follow_redirects=False)
    assert r.headers["location"] == "/pret/002/echange/E01?lettres=1"
    # E01 n'est pas sorti : l'écran d'échange se réaffiche, lettres comprises.
    r = client.get(r.headers["location"])
    assert "n'est pas noté sorti" in r.text
    assert 'inputmode="text"' in r.text


def test_saisie_sur_b_sortie_montre_l_ecran_de_b(client):
    client.post("/pret/002/preter")
    r = client.get("/pret/002/echange/saisie", params={"code": "001"},
                   follow_redirects=False)
    assert r.status_code == 200
    assert "Sorti — pochette n°" in r.text


# ---------------------------------------------------------------------------
# POINT 3 — La confirmation, dans le sens de l'échange
# ---------------------------------------------------------------------------
def test_confirmation_dans_l_ordre_du_geste(client):
    client.post("/pret/001/preter")
    numero = _en_cours("001")["numero_pochette"]
    avant = _prets()

    r = client.get("/pret/002/echange/001")
    assert r.status_code == 200
    assert "<h1>Confirmer l'échange</h1>" in r.text
    assert ("<strong>Catan</strong> (rendu) → <strong>Dixit</strong> (emporté)"
            in r.text)
    assert f"Pochette n°{numero} conservée" in r.text
    # Le formulaire vise LA route d'écriture existante, dans le sens A → B.
    assert '<form method="post" action="/pret/001/transfert/002">' in r.text
    assert 'href="/pret/002/echange">Rescanner le jeu rendu</a>' in r.text
    assert 'href="/pret/002">Annuler — rien n\'a été écrit</a>' in r.text
    assert 'href="/pret/001/transfert"' not in r.text
    assert _prets() == avant


def test_cas_a_egal_b(client):
    r = client.get("/pret/002/echange/002")
    assert r.status_code == 200
    assert "C'est le jeu que le visiteur emporte. Scannez celui qu'il rapporte." in r.text
    assert 'data-scan-cible="/pret/002/echange/"' in r.text  # caméra active
    assert "/transfert/" not in r.text


def test_cas_a_pas_sorti_propose_preter_b_et_la_camera(client):
    r = client.get("/pret/002/echange/001")
    assert r.status_code == 200
    assert ("<strong>Catan</strong> n'est pas noté sorti : il n'y a\n"
            "       pas de pochette à reprendre.") in r.text
    assert '<form method="post" action="/pret/002/preter">' in r.text
    assert "Prêter Dixit" in r.text
    assert 'data-scan-cible="/pret/002/echange/"' in r.text
    # Le rattrapage marche : un tap, B est prêtée sur le plus petit libre.
    r = client.post("/pret/002/preter")
    assert _en_cours("002")["numero_pochette"] == 1


def test_cas_a_deja_rendu_entre_temps(client):
    client.post("/pret/001/preter")
    client.post("/pret/001/rendre")
    r = client.get("/pret/002/echange/001")
    assert "n'est pas noté sorti" in r.text


def test_cas_a_sortie_tournoi(client):
    client.post("/pret/003/tournoi")
    r = client.get("/pret/002/echange/003")
    assert r.status_code == 200
    assert ("<strong>Catan</strong> est sorti pour un tournoi : il\n"
            "       n'a pas de pièce d'identité.") in r.text
    assert 'href="/pret/003"' in r.text
    assert '<form method="post" action="/pret/002/preter">' in r.text
    assert 'data-scan-cible="/pret/002/echange/"' in r.text


def test_cas_a_inconnu_par_la_camera(client):
    r = client.get("/pret/002/echange/999")
    assert r.status_code == 404
    # Pas la page « exemplaire inconnu » : l'écran d'échange, B gardée.
    assert "Aucune boîte ne porte le code « 999 »" in r.text
    assert 'data-scan-cible="/pret/002/echange/"' in r.text
    # Pas d'autofocus : arrivé par la caméra, le clavier ne doit pas surgir.
    assert "autofocus" not in r.text


def test_cas_b_inconnue_a_la_confirmation(client):
    assert client.get("/pret/999/echange/001").status_code == 404


def test_cas_a_retiree_mais_sortie_reste_transferable(client):
    from app import db

    client.post("/pret/001/preter")
    # Le retrait d'une boîte dehors est refusé (comptoir-6) : l'état n'est
    # pas atteignable par l'interface, on le force. Le service, lui, ne
    # refuse que la boîte EMPORTÉE retirée : l'échange n'élargit rien.
    conn = db.get_connection()
    conn.execute("UPDATE exemplaires SET retiree_le = '2026-11-18T10:00:00+00:00' "
                 "WHERE id_exemplaire = '001'")
    conn.commit()
    conn.close()
    r = client.get("/pret/002/echange/001")
    assert '<form method="post" action="/pret/001/transfert/002">' in r.text
    client.post("/pret/001/transfert/002")
    assert _en_cours("001") is None
    assert _en_cours("002") is not None


def test_cas_b_pretee_entre_l_ecran_et_la_confirmation(client):
    client.post("/pret/001/preter")      # n°1, la pochette du visiteur
    client.get("/pret/002/echange")       # l'écran d'échange s'ouvre…
    client.post("/pret/002/preter")      # …et un autre bénévole prête B (n°2)
    r = client.get("/pret/002/echange/001")
    assert r.status_code == 200
    assert "<strong>Dixit</strong> est déjà sortie" in r.text
    assert 'name="clore_oubli" value="1"' in r.text
    assert '<form method="post" action="/pret/001/transfert/002">' in r.text
    # L'escalade fait foi : prêt oublié clos, B part sur la pochette de A.
    client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})
    assert _en_cours("002")["numero_pochette"] == 1
    assert _en_cours("001") is None


def test_cas_b_retiree_entre_temps(client):
    client.post("/pret/001/preter")
    _retirer("002")
    r = client.get("/pret/002/echange/001")
    assert "a été retirée du" in r.text
    assert "Boîte retirée" in r.text
    # Et le POST, s'il part d'un écran ouvert avant : refus existant.
    avant = _prets()
    r = client.post("/pret/001/transfert/002")
    assert "a été retirée du" in r.text
    assert _prets() == avant


# ---------------------------------------------------------------------------
# Bout en bout, rien avant Confirmer, journal
# ---------------------------------------------------------------------------
def test_bout_en_bout_la_pochette_de_a_passe_sur_b(client, _journal_isole):
    import json

    from app import db

    # A (001) sorti sur la 7, la 3 libre : si l'échange passait par le
    # prêt ordinaire, B prendrait la 3.
    conn = db.get_connection()
    conn.execute("INSERT INTO pochettes (numero_pochette, occupe) VALUES (3, 0), (7, 1)")
    conn.execute("INSERT INTO prets (id_exemplaire, numero_pochette, motif, date_sortie) "
                 "VALUES ('001', 7, 'pret', '2026-11-18T09:00:00+00:00')")
    conn.commit()
    conn.close()
    assert _en_cours("001")["numero_pochette"] == 7

    avant = _prets()
    client.get("/pret/002")
    client.get("/pret/002/echange")
    client.get("/pret/002/echange/saisie", params={"code": "001"})
    client.get("/pret/002/echange/001")
    assert _prets() == avant  # rien d'écrit avant Confirmer
    assert not _journal_isole.exists() or '"transfert"' not in _journal_isole.read_text(
        encoding="utf-8")

    r = client.post("/pret/001/transfert/002")
    assert r.status_code == 200
    assert _en_cours("002")["numero_pochette"] == 7
    assert _en_cours("001") is None
    conn = db.get_connection()
    try:
        libre = conn.execute(
            "SELECT occupe FROM pochettes WHERE numero_pochette = 3").fetchone()[0]
    finally:
        conn.close()
    assert libre == 0

    lignes = [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()]
    transferts = [l for l in lignes if l.get("action") == "transfert"]
    assert len(transferts) == 1
    assert transferts[0]["objet"] == "Catan → Dixit"
    assert transferts[0]["ok"] is True


def test_les_ecrans_d_echange_ne_journalisent_rien(client, _journal_isole):
    client.get("/pret/002/echange")
    client.get("/pret/002/echange/saisie", params={"code": "zzz"})
    client.get("/pret/002/echange/002")
    client.get("/pret/002/echange/001")
    client.get("/pret/002/echange/999")
    assert not _journal_isole.exists() or _journal_isole.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Le sens habituel ne bouge pas (le HTML a été comparé à l'octet avant/après
# le lot ; ces tests en verrouillent les marqueurs)
# ---------------------------------------------------------------------------
def test_sens_habituel_ecran_de_scan_inchange(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert")
    assert "<title>Transfert — Prêt / retour" in r.text
    assert "<h1>Transfert de pochette</h1>" in r.text
    assert "Pochette n°1 conservée — scannez le nouveau jeu." in r.text
    assert 'data-scan-cible="/pret/001/transfert/"' in r.text
    assert 'action="/pret/001/transfert/saisie"' in r.text
    assert ('<a class="lien" href="/pret/001">Annuler — rien n\'a été écrit, '
            'le retour classique reste possible</a>') in r.text
    assert "Tapez le code de la nouvelle boîte." in r.text
    assert "echange" not in r.text and "Échange" not in r.text


def test_sens_habituel_confirmation_inchangee(client):
    client.post("/pret/001/preter")
    r = client.get("/pret/001/transfert/002")
    assert "<h1>Confirmer le transfert</h1>" in r.text
    assert "<strong>Catan</strong> → <strong>Dixit</strong><br>" in r.text
    assert "(rendu)" not in r.text and "(emporté)" not in r.text
    assert ('<p class="lien-fiche">\n'
            '    <a class="lien" href="/pret/001/transfert">Rescanner</a>\n'
            '    &nbsp;·&nbsp;\n'
            '    <a class="lien" href="/pret/001">Annuler — rien n\'a été écrit</a>\n'
            '  </p>') in r.text
    assert "echange" not in r.text
