"""
Routes du TRANSFERT DE POCHETTE (docs/conception-transfert-pochette.md,
étape 3 du prompt d'implémentation). Le service (`services.transferer_pochette`)
est déjà testé dans `tests/test_transfert_pochette.py` — ici on vérifie le
CÂBLAGE : protection par jeton, écrans de scan/confirmation qui n'écrivent
rien, rattrapages, et la ligne de journal produite.

Fixtures sur le modèle de `tests/test_routes.py`, avec DEUX exemplaires
(001/Catan, 002/Dixit — nécessaires à tout scénario de transfert) et un
troisième (003/Catan) pour le test « bouton absent sur une sortie tournoi ».
"""

import pytest


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
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("001", "CATAN"), ("002", "DIXIT"), ("003", "CATAN")],
    )
    # Emplacement de rangement de 001, pour vérifier que l'écran de résultat
    # du transfert affiche bien celui de la boîte RENDUE (voir le 3ᵉ piège de
    # la note d'implémentation), pas celui de la nouvelle boîte prêtée.
    conn.execute(
        "UPDATE exemplaires SET emplacement_evenement = 'Étagère A' "
        "WHERE id_exemplaire = '001'"
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _nb_prets(tmp_path):
    from app import db
    conn = db.get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Protection par jeton
# ---------------------------------------------------------------------------
def test_les_quatre_routes_refusent_sans_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")

    assert client.get("/pret/001/transfert").status_code == 403
    assert client.get("/pret/001/transfert/saisie",
                       params={"code": "002"}).status_code == 403
    assert client.get("/pret/001/transfert/002").status_code == 403
    assert client.post("/pret/001/transfert/002").status_code == 403


# ---------------------------------------------------------------------------
# Écran de scan
# ---------------------------------------------------------------------------
def test_ecran_de_scan_affiche_le_numero_et_pose_la_cible(client):
    client.post("/pret/001/preter")

    r = client.get("/pret/001/transfert")
    assert r.status_code == 200
    assert "Pochette n°1 conservée" in r.text
    assert 'data-scan-cible="/pret/001/transfert/"' in r.text


def test_ecran_de_scan_refuse_si_rien_a_rendre(client):
    # 001 n'a jamais été prêté : rien à transférer.
    r = client.get("/pret/001/transfert")
    assert r.status_code == 200
    assert "plus de pochette à transférer" in r.text


def test_ecran_de_scan_refuse_sur_sortie_tournoi(client):
    client.post("/pret/003/tournoi")

    r = client.get("/pret/003/transfert")
    assert r.status_code == 200
    assert "pas de pièce d'identité à transférer" in r.text


# ---------------------------------------------------------------------------
# Écran de confirmation — n'écrit rien
# ---------------------------------------------------------------------------
def test_confirmation_affiche_les_deux_noms_et_n_ecrit_rien(client, tmp_path):
    client.post("/pret/001/preter")
    avant = _nb_prets(tmp_path)

    r = client.get("/pret/001/transfert/002")
    assert r.status_code == 200
    assert "Catan" in r.text
    assert "Dixit" in r.text
    assert _nb_prets(tmp_path) == avant  # rien d'écrit par un simple GET


def test_confirmation_meme_boite_le_dit_explicitement(client):
    client.post("/pret/001/preter")

    r = client.get("/pret/001/transfert/001")
    assert r.status_code == 200
    assert "même jeu" in r.text.lower()
    # La boîte rendue EST sortie : c'est la situation nominale, pas un
    # avertissement (celui-ci ne vaut que pour une AUTRE boîte déjà prêtée).
    assert "déjà sortie" not in r.text.lower()


def test_confirmation_avertit_si_la_nouvelle_boite_est_deja_sortie(client, tmp_path):
    """
    Le POST refuserait (`nouveau_sorti`) : le dire dès la confirmation évite un
    tap perdu. Le bouton reste néanmoins proposé — l'écran n'est qu'un
    instantané, seul le POST fait autorité sous le verrou d'écriture.
    """
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    avant = _nb_prets(tmp_path)

    r = client.get("/pret/001/transfert/002")
    assert r.status_code == 200
    assert "déjà sortie" in r.text.lower()
    assert "Confirmer" in r.text          # jamais bloquant : le bouton reste
    assert _nb_prets(tmp_path) == avant   # et toujours rien d'écrit


def test_confirmation_avertit_sans_numero_pour_une_sortie_tournoi(client):
    """Une sortie tournoi n'a pas de pochette : aucun numéro à afficher."""
    client.post("/pret/001/preter")
    client.post("/pret/002/tournoi")

    r = client.get("/pret/001/transfert/002")
    assert "déjà sortie" in r.text.lower()
    assert "pochette n°0" not in r.text.lower()


# ---------------------------------------------------------------------------
# POST nominal
# ---------------------------------------------------------------------------
def test_transfert_nominal(client):
    from app import db, services

    client.post("/pret/001/preter")
    conn = db.get_connection()
    try:
        numero = services.pret_en_cours(conn, "001")["numero_pochette"]
    finally:
        conn.close()

    r = client.post("/pret/001/transfert/002")
    assert r.status_code == 200
    assert "Dixit" in r.text            # écran rendu sur la NOUVELLE boîte
    assert f">{numero}<" in r.text or f"n°{numero}" in r.text
    # L'emplacement affiché est celui de la boîte RENDUE (001), pas de 002
    # (qui n'en a pas).
    assert "Étagère A" in r.text

    conn = db.get_connection()
    try:
        assert services.pret_en_cours(conn, "001") is None            # dispo
        nouveau = services.pret_en_cours(conn, "002")
        assert nouveau is not None and nouveau["numero_pochette"] == numero
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Refus « nouvelle boîte déjà sortie »
# ---------------------------------------------------------------------------
def test_refus_nouvelle_boite_deja_sortie(client, tmp_path):
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    avant = _nb_prets(tmp_path)

    r = client.post("/pret/001/transfert/002")
    assert r.status_code == 200
    assert "déjà sortie" in r.text
    # On reste sur l'écran de transfert (scan), pas sur /pret/001.
    assert 'data-scan-cible="/pret/001/transfert/"' in r.text
    assert _nb_prets(tmp_path) == avant


# ---------------------------------------------------------------------------
# Escalade « clôturer le prêt oublié » (série agora, lot 1)
# ---------------------------------------------------------------------------
# Deux issues depuis le lot 13 de la série pré-production (constat UX-02).
LIBELLE_ESCALADE = "Pochette vide, je continue"
LIBELLE_PAS_VIDE = "Pochette pas vide — prévenir le bureau"


def _pret_en_cours(id_exemplaire):
    from app import db, services
    conn = db.get_connection()
    try:
        return services.pret_en_cours(conn, id_exemplaire)
    finally:
        conn.close()


def test_l_ecran_de_confirmation_propose_l_escalade_a_cote_du_transfert(client, tmp_path):
    """
    Le meilleur endroit : le bénévole n'a même pas besoin de se prendre le
    refus. Le bouton de transfert normal reste EN PLACE à côté — l'écran n'est
    qu'un instantané, la boîte peut être revenue entre-temps.
    """
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    avant = _nb_prets(tmp_path)

    r = client.get("/pret/001/transfert/002")

    assert LIBELLE_ESCALADE in r.text
    assert 'name="clore_oubli"' in r.text
    assert "Vérifiez que la pochette n°2 est bien vide" in r.text
    assert "Confirmer" in r.text            # le transfert normal reste offert
    assert _nb_prets(tmp_path) == avant     # un GET n'écrit toujours rien


def test_le_refus_du_post_porte_l_escalade_au_dessus_de_la_camera(client, tmp_path):
    """
    Second endroit imposé : le POST fait autorité, c'est lui qui découvre le
    conflit. La page de scan doit alors porter le bloc d'escalade — et garder
    la caméra active en dessous, « scanner une autre boîte » n'ayant besoin
    d'aucun bouton.
    """
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    avant = _nb_prets(tmp_path)

    r = client.post("/pret/001/transfert/002")

    assert LIBELLE_ESCALADE in r.text
    assert 'action="/pret/001/transfert/002"' in r.text
    assert 'data-scan-cible="/pret/001/transfert/"' in r.text   # caméra active
    assert _nb_prets(tmp_path) == avant                         # rien d'écrit


def test_l_escalade_clot_le_pret_oublie_et_transfere(client):
    from app import db

    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    numero_rendu = _pret_en_cours("001")["numero_pochette"]
    numero_oubli = _pret_en_cours("002")["numero_pochette"]

    r = client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})

    assert r.status_code == 200
    # L'écran dit ce qui a été clos, et quel casier redevient disponible.
    assert f"la pochette n°{numero_oubli} est rendue disponible" in r.text
    assert _pret_en_cours("001") is None
    assert _pret_en_cours("002")["numero_pochette"] == numero_rendu
    # La libération est réelle en base, pas seulement annoncée à l'écran.
    conn = db.get_connection()
    try:
        occupe = conn.execute(
            "SELECT occupe FROM pochettes WHERE numero_pochette = ?",
            (numero_oubli,),
        ).fetchone()["occupe"]
        conserve = conn.execute(
            "SELECT occupe FROM pochettes WHERE numero_pochette = ?",
            (numero_rendu,),
        ).fetchone()["occupe"]
    finally:
        conn.close()
    assert occupe == 0 and conserve == 1


def test_l_escalade_sur_une_sortie_tournoi_ne_libere_aucun_numero(client):
    client.post("/pret/001/preter")
    client.post("/pret/002/tournoi")
    numero_rendu = _pret_en_cours("001")["numero_pochette"]

    r = client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})

    assert r.status_code == 200
    assert "rendue disponible" not in r.text     # aucun casier à annoncer
    assert _pret_en_cours("002")["numero_pochette"] == numero_rendu


def test_l_escalade_sur_une_boite_revenue_entre_temps_transfere_normalement(client,
                                                                            _journal_isole):
    """
    Concurrence : la boîte est rendue entre l'affichage du bouton et l'appui.
    Le POST fait autorité et le transfert est ordinaire — ni erreur, ni
    seconde clôture, et le journal ne doit pas annoncer une clôture qui n'a
    pas eu lieu.
    """
    import json

    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    client.post("/pret/002/rendre")               # un autre bénévole a scanné

    r = client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})

    assert r.status_code == 200
    assert "rendue disponible" not in r.text
    assert _pret_en_cours("002") is not None
    actions = [json.loads(l)["action"]
               for l in _journal_isole.read_text(encoding="utf-8").splitlines()
               if l.strip()]
    assert "transfert" in actions
    assert "transfert_avec_cloture" not in actions


def test_l_escalade_est_journalisee_sous_son_propre_nom_sans_numero(client,
                                                                     _journal_isole):
    """
    C'est la seule écriture de l'application qui ferme DEUX prêts d'un coup :
    elle porte un nom distinct dans /admin/journal, où le nom brut s'affiche.
    Le numéro de pochette, lui, n'y entre pas plus qu'ailleurs (§8 de
    docs/conception-journal.md).
    """
    import json

    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    numeros = {str(_pret_en_cours("001")["numero_pochette"]),
               str(_pret_en_cours("002")["numero_pochette"])}

    client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})

    lignes = [json.loads(l)
              for l in _journal_isole.read_text(encoding="utf-8").splitlines()
              if '"action":"transfert_avec_cloture"' in l]
    assert len(lignes) == 1
    ligne = lignes[0]
    assert ligne["objet"] == "Catan → Dixit"
    assert ligne["ref"] == "DIXIT"
    assert ligne["ok"] is True
    for champ in ("objet", "ref", "detail"):
        assert ligne.get(champ) not in numeros
    assert "pochette" not in json.dumps(ligne).lower()


def test_l_escalade_exige_le_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")

    r = client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Refus « boîte rendue entre-temps »
# ---------------------------------------------------------------------------
def test_refus_boite_rendue_entre_temps(client, tmp_path):
    # 001 n'a pas de prêt en cours : un autre bénévole l'a déjà rendue.
    avant = _nb_prets(tmp_path)

    r = client.post("/pret/001/transfert/002")
    assert r.status_code == 200
    assert "plus de pochette à transférer" in r.text
    assert _nb_prets(tmp_path) == avant


# ---------------------------------------------------------------------------
# Le bouton n'apparaît que pour un prêt au public
# ---------------------------------------------------------------------------
def test_bouton_absent_sur_sortie_tournoi_present_sur_pret_public(client):
    client.post("/pret/001/preter")
    client.post("/pret/003/tournoi")

    LIBELLE = "Rendre et prêter un nouveau jeu sans retour PI"
    assert LIBELLE in client.get("/pret/001").text
    assert LIBELLE not in client.get("/pret/003").text


# ---------------------------------------------------------------------------
# Saisie manuelle de secours
# ---------------------------------------------------------------------------
def test_saisie_manuelle_code_vide(client):
    client.post("/pret/001/preter")

    r = client.get("/pret/001/transfert/saisie", params={"code": ""})
    assert r.status_code == 200
    assert "Veuillez saisir un code" in r.text
    assert 'data-scan-cible="/pret/001/transfert/"' in r.text


def test_saisie_manuelle_code_inconnu(client):
    client.post("/pret/001/preter")

    r = client.get("/pret/001/transfert/saisie", params={"code": "999"})
    assert r.status_code == 200
    assert "Aucune boîte ne porte le code" in r.text
    assert 'value="999"' in r.text  # champ prérempli


def test_saisie_manuelle_code_valide_redirige_vers_la_confirmation(client):
    client.post("/pret/001/preter")

    r = client.get("/pret/001/transfert/saisie", params={"code": " 002 "},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/pret/001/transfert/002"


# ---------------------------------------------------------------------------
# /transfert/saisie n'est PAS capturée comme un id_nouveau
# ---------------------------------------------------------------------------
def test_saisie_n_est_pas_capturee_comme_id_nouveau(client):
    client.post("/pret/001/preter")

    r = client.get("/pret/001/transfert/saisie", params={"code": "002"},
                    follow_redirects=False)
    # Si la route dynamique avait capturé "saisie" comme id_nouveau, ceci
    # renverrait directement l'écran de confirmation (200) au lieu d'une
    # redirection 303.
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Journal d'activité
# ---------------------------------------------------------------------------
def test_le_transfert_est_journalise_sans_numero(client, _journal_isole):
    import json

    from app import db, services

    client.post("/pret/001/preter")
    conn = db.get_connection()
    try:
        numero = str(services.pret_en_cours(conn, "001")["numero_pochette"])
    finally:
        conn.close()

    client.post("/pret/001/transfert/002")

    texte = _journal_isole.read_text(encoding="utf-8")
    lignes = [json.loads(l) for l in texte.splitlines() if '"action":"transfert"' in l]
    assert len(lignes) == 1
    ligne = lignes[0]
    assert ligne["objet"] == "Catan → Dixit"
    # `ref` = le titre NOUVELLEMENT PRÊTÉ, comme l'action `pret` : filtrer le
    # journal sur une référence doit donner la même chose quel que soit le
    # chemin emprunté pour prêter le jeu (voir `_journaliser_transfert`).
    assert ligne["ref"] == "DIXIT"
    assert ligne["ok"] is True
    # Le mot ET la valeur : ni l'un ni l'autre ne doivent apparaître, dans
    # AUCUN champ (patron de test_journal_interdits.py::test_aucun_numero_de_pochette
    # — chercher la valeur en texte libre serait un faux positif, un petit
    # entier comme "1" apparaît trivialement dans un horodatage).
    for champ in ("objet", "ref", "detail"):
        assert ligne.get(champ) != numero
    assert "pochette" not in json.dumps(ligne).lower()


# ---------------------------------------------------------------------------
# Escalade : seconde issue « pochette pas vide » (lot 13 pré-production, UX-02)
# ---------------------------------------------------------------------------
def _signalements():
    from app import db
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id_exemplaire, id_categorie, texte, traite_le FROM signalements"
        )]
    finally:
        conn.close()


def _etat_prets():
    from app import db
    conn = db.get_connection()
    try:
        prets = [dict(r) for r in conn.execute("SELECT * FROM prets ORDER BY id_pret")]
        pochettes = [dict(r) for r in conn.execute(
            "SELECT * FROM pochettes ORDER BY numero_pochette")]
        return prets, pochettes
    finally:
        conn.close()


def test_l_escalade_offre_deux_issues_sur_les_deux_ecrans(client):
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    action = 'action="/pret/001/transfert/002/pochette-non-vide"'

    confirmation = client.get("/pret/001/transfert/002")
    refus = client.post("/pret/001/transfert/002")

    for r in (confirmation, refus):
        assert LIBELLE_ESCALADE in r.text
        assert LIBELLE_PAS_VIDE in r.text
        assert action in r.text
    # Le bouton n'ouvre AUCUN champ libre : rien à taper, donc rien qui
    # puisse décrire une personne.
    assert "<textarea" not in confirmation.text


def test_une_sortie_tournoi_oubliee_n_a_qu_une_issue(client):
    """Sans pochette, rien à vérifier : la seconde issue n'aurait pas de sens."""
    client.post("/pret/001/preter")
    client.post("/pret/002/tournoi")

    r = client.get("/pret/001/transfert/002")

    assert "Clôturer la sortie oubliée et transférer" in r.text
    assert LIBELLE_PAS_VIDE not in r.text


def test_pochette_pas_vide_n_ecrit_aucun_transfert_et_previent_le_bureau(client):
    from app import services

    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    numero_oubli = _pret_en_cours("002")["numero_pochette"]
    numero_visiteur = _pret_en_cours("001")["numero_pochette"]
    avant = _etat_prets()

    r = client.post("/pret/001/transfert/002/pochette-non-vide")

    assert r.status_code == 200
    # Aucun prêt écrit, clos ou libéré : le prêt oublié reste ouvert, sa
    # pochette reste occupée — c'est ce qui rend la consigne sûre.
    assert _etat_prets() == avant
    # Un signalement, sur la boîte du prêt oublié, rédigé par l'application.
    assert _signalements() == [{
        "id_exemplaire": "002", "id_categorie": None,
        "texte": services.TEXTES_POCHETTE_NON_VIDE[services.GESTE_TRANSFERT],
        "traite_le": None,
    }]
    # L'écran dit quoi faire de la pièce d'identité, et garde la caméra.
    assert "Le bureau est prévenu" in r.text
    assert "Laissez la pièce d'identité dans la pochette" in r.text
    assert f"n°{numero_oubli}" in r.text
    assert f"Pochette n°{numero_visiteur} conservée" in r.text
    assert 'data-scan-cible="/pret/001/transfert/"' in r.text
    # Plus d'escalade affichée : la bénévole vient de dire que la pochette
    # n'est pas vide.
    assert LIBELLE_ESCALADE not in r.text


def test_le_signalement_ne_porte_que_la_boite(client):
    """
    Garde-fou données personnelles : le texte est une constante, sans chiffre
    — pas même le numéro de pochette, que D5 efface de toute ligne close et
    qu'un signalement (jamais purgé) ferait revivre.
    """
    from app import services

    for texte in services.TEXTES_POCHETTE_NON_VIDE.values():
        assert not any(c.isdigit() for c in texte)
        assert len(texte) <= services.LONGUEUR_MAX_TEXTE_SIGNALEMENT


def test_pochette_pas_vide_sans_aucune_categorie(client):
    """
    Les catégories sont administrables : toutes archivées, voire supprimées,
    le signalement passe quand même — il n'en vise aucune.
    """
    from app import db

    conn = db.get_connection()
    try:
        conn.execute("UPDATE categories_signalement SET actif = 0")
        conn.execute("DELETE FROM categories_signalement WHERE id_categorie % 2 = 0")
        conn.commit()
    finally:
        conn.close()
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")

    r = client.post("/pret/001/transfert/002/pochette-non-vide")

    assert r.status_code == 200
    assert len(_signalements()) == 1
    # Et la fiche de la boîte l'affiche, catégorie absente comprise.
    fiche = client.get("/pret/002")
    assert "Pochette pas vide" in fiche.text


def test_pochette_pas_vide_sur_une_boite_revenue_entre_temps(client):
    """
    La boîte a été rendue entre l'écran et l'appui : celui qui l'a rendue a
    vidé la pochette. Rien à signaler — on rouvre la confirmation, désormais
    sans escalade.
    """
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    client.post("/pret/002/rendre")

    r = client.post("/pret/001/transfert/002/pochette-non-vide",
                    follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/pret/001/transfert/002"
    assert _signalements() == []


def test_pochette_pas_vide_exige_le_jeton(client, monkeypatch):
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    assert client.post("/pret/001/transfert/002/pochette-non-vide").status_code == 403
    assert _signalements() == []


def test_pochette_pas_vide_est_journalisee_comme_un_signalement(client, _journal_isole):
    import json

    from app import services

    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    client.post("/pret/001/transfert/002/pochette-non-vide")

    lignes = [json.loads(l) for l in _journal_isole.read_text(encoding="utf-8").splitlines()
              if '"signalement_cree"' in l]
    assert len(lignes) == 1
    assert lignes[0]["objet"] == f"Dixit — {services.LIBELLE_POCHETTE_NON_VIDE}"
    # Ni transfert ni clôture n'ont eu lieu : le journal ne doit pas le dire.
    assert '"transfert_avec_cloture"' not in _journal_isole.read_text(encoding="utf-8")


def test_apres_la_seconde_issue_la_premiere_reste_possible(client):
    """
    Ne jamais bloquer : une fois la pièce récupérée par le bureau, rescanner
    la boîte rouvre l'escalade, et « Pochette vide, je continue » transfère.
    """
    client.post("/pret/001/preter")
    client.post("/pret/002/preter")
    client.post("/pret/001/transfert/002/pochette-non-vide")

    assert LIBELLE_ESCALADE in client.get("/pret/001/transfert/002").text
    r = client.post("/pret/001/transfert/002", data={"clore_oubli": "1"})

    assert r.status_code == 200
    assert _pret_en_cours("001") is None
