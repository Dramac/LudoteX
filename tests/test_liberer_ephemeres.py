"""
JEUX ÉPHÉMÈRES : LIBÉRER LES QR EN FIN D'ÉVÉNEMENT (série comptoir, lot 8b).

La boîte d'un QR éphémère nommé prend un autre code (`X01` → `X01-2026`),
garde ses prêts et ses signalements, est retirée du parc (motif `passage`),
et le code imprimé redevient vierge. Ce fichier vérifie :

1. la forme du code libéré, ses collisions, la saisie et l'import ;
2. le renommage : tout recopié, tout repointé, tout ou rien ;
3. les refus (sortie, non éphémère, vierge), le double envoi, la course ;
4. les statistiques de l'édition identiques ; les périodes passées
   (`entree_le`, `condition_au_parc_pendant`) ;
5. le QR vierge de nouveau ; l'export, les étiquettes, la remise en service ;
6. l'écran du bureau, sa garde, le renvoi depuis la clôture, le journal ;
7. le motif « passage », absent du retrait ordinaire.

Catalogue de départ (fixture `bases` du lot 8a) : « Catane » (001, 002) et
« Dobble » (003).
"""

import re
import sqlite3

import pytest

from app import achats, gouts, journal, services
from tests.test_jeux_ephemeres import (_client, _conn, _journal, _lire,  # noqa: F401
                                       _nommer, _titre, admin, bases, benevole,
                                       visiteur)

LIBERER = "/admin/etiquettes/ephemeres/liberer"


def _boite(code):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM exemplaires WHERE id_exemplaire = ?",
                           (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _pret(code, sortie, retour, motif="pret"):
    conn = _conn()
    try:
        conn.execute("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
                     "VALUES (?, ?, ?, ?)", (code, sortie, retour, motif))
        conn.commit()
    finally:
        conn.close()


def _liberer(code, annee=2026):
    conn = _conn()
    try:
        return services.liberer_code_ephemere(conn, code, annee=annee)
    finally:
        conn.close()


def _nommer_en_base(code, nom, entree_le=None):
    """Nomme par le service, puis fixe la date d'entrée si demandée."""
    conn = _conn()
    try:
        services.nommer_code_ephemere(conn, code, nom)
        if entree_le:
            conn.execute("UPDATE exemplaires SET entree_le = ? WHERE id_exemplaire = ?",
                         (entree_le, code))
            conn.commit()
    finally:
        conn.close()


def _texte(page):
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


# ===========================================================================
# 1. LA FORME DU CODE LIBÉRÉ
# ===========================================================================
def test_forme_du_code_libere(bases):
    conn = _conn()
    try:
        assert services.code_libere(conn, "X01", 2026) == "X01-2026"
    finally:
        conn.close()
    assert not services.est_code_ephemere("X01-2026")
    assert services.code_ephemere_saisi("X01-2026") is None
    assert services.code_ephemere_saisi("x1-2026") is None
    assert services.cle_saisie("X01-2026") == "X1-2026"


def test_deux_liberations_la_meme_annee(bases):
    _nommer_en_base("X01", "Azul")
    assert _liberer("X01")["nouveau_code"] == "X01-2026"
    _nommer_en_base("X01", "Zombicide")
    assert _liberer("X01")["nouveau_code"] == "X01-2026-2"
    _nommer_en_base("X01", "Splendor")
    assert _liberer("X01")["nouveau_code"] == "X01-2026-3"
    assert _boite("X01") is None


def test_collision_par_la_cle_de_saisie(bases):
    """Un code « X1-2026 » importé rendrait la saisie ambiguë : on l'évite."""
    conn = _conn()
    try:
        conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
                     "VALUES ('X1-2026', 'DOBBLE')")
        conn.commit()
        assert services.code_libere(conn, "X01", 2026) == "X01-2026-2"
    finally:
        conn.close()


def test_l_annee_est_celle_de_l_heure_locale(bases):
    _nommer_en_base("X02", "Azul")
    r = _liberer("X02", annee=None)
    assert r["nouveau_code"] == f"X02-{services.annee_locale()}"


def test_saisie_au_clavier_du_code_libere(bases):
    _nommer_en_base("X01", "Azul")
    _liberer("X01")
    conn = _conn()
    try:
        assert services.resoudre_code_saisi(conn, "x1-2026") == ("X01-2026", [])
        assert services.resoudre_code_saisi(conn, "X 01-2026") == ("X01-2026", [])
        # « x1 » ne retrouve PAS la boîte libérée : il désigne le QR vierge.
        assert services.resoudre_code_saisi(conn, "x1") == (None, [])
    finally:
        conn.close()
    assert services.code_ephemere_saisi("x1") == "X01"


def test_import_une_ligne_au_code_libere_n_est_pas_ephemere(bases, tmp_path):
    from scripts import import_csv
    _nommer_en_base("X01", "Azul")
    _liberer("X01")
    chemin = tmp_path / "catalogue.csv"
    chemin.write_text("Code jeu;Nom jeu\n001;Catane\nX01-2026;Azul\n", encoding="utf-8")
    res = import_csv.importer(chemin)
    assert res["ephemeres_ecartees"] == []                        # pas éphémère
    assert [e["code"] for e in res["retirees_ecartees"]] == ["X01-2026"]   # retirée
    assert _boite("X01-2026")["retiree_le"] is not None


# ===========================================================================
# 2. LE RENOMMAGE, EN UNE TRANSACTION
# ===========================================================================
def test_les_tables_repointees_sont_toutes_celles_qui_pointent_une_boite(bases):
    conn = _conn()
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        pointent = sorted(t for t in tables for fk in conn.execute(
            f"PRAGMA foreign_key_list({t})") if fk[2] == "exemplaires")
    finally:
        conn.close()
    assert pointent == sorted(services.TABLES_CODE_DE_BOITE)


def test_liberer_recopie_repointe_et_retire(bases):
    _nommer_en_base("X01", "Azul")
    conn = _conn()
    try:
        id_emplacement = services.creer_emplacement_rangement(conn, "Étagère 9")
        conn.execute("UPDATE exemplaires SET emplacement_evenement = 'Table 4', "
                     "emplacement_local_id = ? WHERE id_exemplaire = 'X01'", (id_emplacement,))
        conn.commit()
        cat = services.categories_signalement_actives(conn)[0]["id_categorie"]
        services.creer_signalement(conn, "X01", cat, None)
    finally:
        conn.close()
    _pret("X01", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    _pret("X01", "2026-11-14T12:00:00+00:00", "2026-11-14T12:00:20+00:00", "erreur")
    avant = _boite("X01")

    r = _liberer("X01")

    assert r == {"statut": "liberee", "code": "X01", "nouveau_code": "X01-2026",
                 "reference_titre": "AZUL", "nom": "Azul"}
    assert _boite("X01") is None
    apres = _boite("X01-2026")
    for colonne, valeur in avant.items():
        if colonne not in ("id_exemplaire", "retiree_le", "motif_retrait"):
            assert apres[colonne] == valeur, colonne
    assert apres["entree_le"] == avant["entree_le"] is not None
    assert apres["retiree_le"] is not None and apres["motif_retrait"] == "passage"
    assert _lire("SELECT id_exemplaire, motif FROM prets ORDER BY id_pret") == [
        ("X01-2026", "pret"), ("X01-2026", "erreur")]
    assert _lire("SELECT id_exemplaire FROM signalements") == [("X01-2026",)]
    assert _titre("AZUL")["nom"] == "Azul"                       # le nom reste


def test_les_colonnes_recopiees_sont_celles_du_schema(bases, monkeypatch):
    """Une colonne ajoutée au schéma suit la boîte sans toucher au service."""
    conn = _conn()
    try:
        conn.execute("ALTER TABLE exemplaires ADD COLUMN essai_8b TEXT")
        conn.commit()
    finally:
        conn.close()
    _nommer_en_base("X01", "Azul")
    conn = _conn()
    try:
        conn.execute("UPDATE exemplaires SET essai_8b = 'valeur' WHERE id_exemplaire = 'X01'")
        conn.commit()
    finally:
        conn.close()
    _liberer("X01")
    assert _boite("X01-2026")["essai_8b"] == "valeur"


@pytest.mark.parametrize("declencheur", [
    # Étape 4 : la suppression de l'ancienne ligne échoue.
    "CREATE TRIGGER bloque BEFORE DELETE ON exemplaires "
    "BEGIN SELECT RAISE(ABORT, 'essai'); END",
    # Étape 5 : le retrait de la nouvelle ligne échoue.
    "CREATE TRIGGER bloque BEFORE UPDATE OF retiree_le ON exemplaires "
    "BEGIN SELECT RAISE(ABORT, 'essai'); END",
])
def test_tout_ou_rien(bases, declencheur):
    _nommer_en_base("X01", "Azul")
    _pret("X01", "2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+00:00")
    conn = _conn()
    try:
        conn.execute(declencheur)
        conn.commit()
        photo = [list(map(tuple, conn.execute(f"SELECT * FROM {t} ORDER BY 1")))
                 for t in ("exemplaires", "prets", "signalements", "titres")]
        with pytest.raises(sqlite3.IntegrityError):
            services.liberer_code_ephemere(conn, "X01", annee=2026)
        assert [list(map(tuple, conn.execute(f"SELECT * FROM {t} ORDER BY 1")))
                for t in ("exemplaires", "prets", "signalements", "titres")] == photo
        assert not conn.in_transaction
    finally:
        conn.close()
    assert _boite("X01")["retiree_le"] is None


# ===========================================================================
# 3. REFUS, DOUBLE ENVOI, COURSE
# ===========================================================================
@pytest.mark.parametrize("motif", ["pret", "tournoi", "demo"])
def test_refus_d_une_boite_sortie(bases, motif):
    _nommer_en_base("X01", "Azul")
    conn = _conn()
    try:
        if motif == "pret":
            services.preter(conn, "X01")
        else:
            services.sortir_sans_pochette(conn, "X01", motif)
    finally:
        conn.close()
    avant = _boite("X01")
    r = _liberer("X01")
    assert r["statut"] == "sortie" and r["nom"] == "Azul" and r["nouveau_code"] is None
    assert _boite("X01") == avant and _boite("X01-2026") is None


@pytest.mark.parametrize("code", ["001", "X1", "x01", "X01-2026", "A0001"])
def test_refus_d_un_code_non_ephemere(bases, code):
    avant = _lire("SELECT * FROM exemplaires ORDER BY 1")
    assert _liberer(code)["statut"] == "non_ephemere"
    assert _lire("SELECT * FROM exemplaires ORDER BY 1") == avant


def test_qr_vierge_ou_double_envoi(bases):
    assert _liberer("X05")["statut"] == "vierge"                 # jamais nommé
    _nommer_en_base("X01", "Azul")
    assert _liberer("X01")["statut"] == "liberee"
    avant = _lire("SELECT * FROM exemplaires ORDER BY 1")
    assert _liberer("X01")["statut"] == "vierge"                 # double envoi
    assert _lire("SELECT * FROM exemplaires ORDER BY 1") == avant


def test_une_boite_deja_retiree_garde_sa_date_et_son_motif(bases):
    _nommer_en_base("X01", "Azul")
    conn = _conn()
    try:
        services.retirer_exemplaire(conn, "X01", "perdue")
    finally:
        conn.close()
    avant = _boite("X01")
    assert _liberer("X01")["statut"] == "liberee"
    apres = _boite("X01-2026")
    assert (apres["retiree_le"], apres["motif_retrait"]) == (avant["retiree_le"], "perdue")


def test_course_avec_un_pret_jamais_au_milieu(bases):
    """Le prêt tient le verrou : la libération attend ou échoue sans rien
    écrire ; le prêt passé, elle voit la boîte sortie."""
    _nommer_en_base("X01", "Azul")
    autre = _conn()
    conn = _conn()
    try:
        autre.execute("BEGIN IMMEDIATE")
        autre.execute("INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie) "
                      "VALUES ('X01', 1, '2026-11-15T10:00:00+00:00')")
        conn.execute("PRAGMA busy_timeout = 50")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            services.liberer_code_ephemere(conn, "X01", annee=2026)
        autre.commit()
        assert services.liberer_code_ephemere(conn, "X01", annee=2026)["statut"] == "sortie"
    finally:
        autre.close()
        conn.close()
    assert _boite("X01") is not None and _boite("X01-2026") is None


# ===========================================================================
# 4. LES STATISTIQUES — l'édition ne bouge pas, les périodes passées non plus
# ===========================================================================
E2025 = ("2025-11-14T23:00:00+00:00", "2025-11-16T23:00:00+00:00")
E2026 = ("2026-11-13T23:00:00+00:00", "2026-11-15T23:00:00+00:00")


def _quatre_lecteurs(debut, fin):
    """Les quatre lecteurs de `condition_au_parc_pendant`, sur une période."""
    from app import comptoir
    conn = _conn()
    try:
        donnees = comptoir.heure_par_heure(conn, debut, fin)
        return {
            "globales": services.stats_globales(conn, debut, fin,
                                                donnees["instants_cloture"]),
            "palmares": services.palmares(conn, "desc", "total", 50, debut, fin),
            "achats": achats.pour_les_achats(conn, debut, fin, donnees),
            "gouts": gouts.ce_qui_plait(conn, debut, fin, donnees["instants_cloture"]),
        }
    finally:
        conn.close()


@pytest.fixture
def edition_peuplee(bases):
    """Deux éphémères nommés pendant l'édition 2026, prêtés, une démo, un
    signalement ouvert ; un prêt du catalogue en 2025 et en 2026."""
    _nommer_en_base("X01", "Azul", "2026-11-14T08:00:00+00:00")
    _nommer_en_base("X02", "Catane", "2026-11-14T08:30:00+00:00")   # titre du catalogue
    _pret("001", "2025-11-15T10:00:00+00:00", "2025-11-15T11:00:00+00:00")
    _pret("001", "2026-11-14T10:00:00+00:00", "2026-11-14T11:30:00+00:00")
    _pret("X01", "2026-11-14T09:00:00+00:00", "2026-11-14T10:00:00+00:00")
    _pret("X01", "2026-11-14T14:00:00+00:00", "2026-11-14T16:00:00+00:00")
    _pret("X01", "2026-11-15T09:00:00+00:00", "2026-11-15T09:20:00+00:00", "demo")
    _pret("X02", "2026-11-15T10:00:00+00:00", "2026-11-15T12:00:00+00:00")
    conn = _conn()
    try:
        cat = services.categories_signalement_actives(conn)[0]["id_categorie"]
        services.creer_signalement(conn, "X01", cat, None)
    finally:
        conn.close()
    return bases


def test_les_statistiques_de_l_edition_ne_bougent_pas(edition_peuplee):
    avant = {p: _quatre_lecteurs(*p) for p in (E2026, E2025, (None, None))}
    assert _liberer("X01")["statut"] == "liberee"
    assert _liberer("X02")["statut"] == "liberee"
    apres = {p: _quatre_lecteurs(*p) for p in (E2026, E2025, (None, None))}
    assert apres == avant
    # La règle a bien mordu : Azul compte dans 2026, pas dans 2025.
    assert any(j["nom"] == "Azul" for j in avant[E2026]["palmares"])
    assert not any(j["nom"] == "Azul" for j in avant[E2025]["palmares"])


def test_le_signalement_suit_la_boite_au_carnet(edition_peuplee, benevole):
    _liberer("X01")
    conn = _conn()
    try:
        ouverts = services.lister_signalements(conn, "ouverts")
    finally:
        conn.close()
    assert [s["id_exemplaire"] for s in ouverts] == ["X01-2026"]
    assert "X01-2026" in benevole.get("/maintenance").text


def test_une_periode_passee_ne_compte_pas_le_jeu_de_passage(edition_peuplee):
    c = _quatre_lecteurs(*E2025)
    assert c["globales"]["nb_titres"] == 2                     # Catane, Dobble
    assert [j["nom"] for j in c["achats"]["jamais_sortis"]] == ["Dobble"]
    assert {j["nom"]: j["nb_exemplaires"] for j in c["palmares"]} == {
        "Catane": 2, "Dobble": 1}                              # X02 n'y est pas
    assert c["gouts"]["nb_titres"] == 2
    t = _quatre_lecteurs(None, None)
    assert t["globales"]["nb_titres"] == 3
    assert {j["nom"]: j["nb_exemplaires"] for j in t["palmares"]}["Catane"] == 3


def test_une_periode_sans_fin_n_ecarte_personne(edition_peuplee):
    c = _quatre_lecteurs(E2025[0], None)
    assert {j["nom"]: j["nb_exemplaires"] for j in c["palmares"]} == {
        "Azul": 1, "Catane": 3, "Dobble": 1}


def test_condition_au_parc_pendant_et_ses_bornes():
    assert services.condition_au_parc_pendant(None, None) == ("1 = 1", [])
    assert services.condition_au_parc_pendant("D", None, "x") == (
        "(x.retiree_le IS NULL OR x.retiree_le > ?)", ["D"])
    assert services.condition_au_parc_pendant(None, "F", "x") == (
        "(x.entree_le IS NULL OR x.entree_le < ?)", ["F"])
    assert services.condition_au_parc_pendant("D", "F") == (
        "(e.retiree_le IS NULL OR e.retiree_le > ?) AND "
        "(e.entree_le IS NULL OR e.entree_le < ?)", ["D", "F"])


def test_nommer_pose_la_date_d_entree(bases):
    _nommer_en_base("X03", "Azul")
    assert _boite("X03")["entree_le"] is not None
    assert _boite("001")["entree_le"] is None                  # le tableur : depuis toujours


def test_migration_une_base_d_avant_gagne_entree_le_vide(tmp_path, monkeypatch):
    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.execute("CREATE TABLE titres (reference_titre TEXT PRIMARY KEY, nom TEXT NOT NULL)")
    ancienne.execute("CREATE TABLE exemplaires (id_exemplaire TEXT PRIMARY KEY, "
                     "reference_titre TEXT NOT NULL, retiree_le TEXT, motif_retrait TEXT)")
    ancienne.execute("INSERT INTO titres VALUES ('CATAN', 'Catan')")
    ancienne.execute("INSERT INTO exemplaires VALUES ('001', 'CATAN', NULL, NULL)")
    ancienne.commit()
    ancienne.close()
    from app import db
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        db.init_db(conn)                                       # idempotente
        assert "entree_le" in {r[1] for r in conn.execute("PRAGMA table_info(exemplaires)")}
        assert conn.execute("SELECT entree_le FROM exemplaires").fetchone()[0] is None
        # Une base d'avant compte sa boîte dans toute période, comme avant.
        jeux = services.palmares(conn, debut=E2025[0], fin=E2025[1])
        assert [(j["nom"], j["nb_exemplaires"]) for j in jeux] == [("Catan", 1)]
    finally:
        conn.close()


# ===========================================================================
# 5. LE QR REDEVIENT VIERGE ; CE QUI IGNORE LA BOÎTE ; LA REMISE EN SERVICE
# ===========================================================================
def test_le_qr_redevient_vierge(benevole):
    _nommer(benevole, "X01", "Azul")
    _liberer("X01")
    page = benevole.get("/pret/X01").text
    assert "QR éphémère X01" in page and "Nommer ce jeu" in page
    _nommer(benevole, "X01", "Zombicide")
    assert _boite("X01")["reference_titre"] == "ZOMBICIDE"
    assert _boite("X01-2026")["reference_titre"] == "AZUL"
    assert _boite("X01-2026")["retiree_le"] is not None


def test_le_meme_nom_l_annee_suivante_rejoint_le_titre(benevole):
    _nommer(benevole, "X01", "Azul")
    _liberer("X01")
    _nommer(benevole, "X01", "Azul")
    assert _lire("SELECT id_exemplaire, retiree_le IS NOT NULL FROM exemplaires "
                 "WHERE reference_titre = 'AZUL' ORDER BY 1") == [("X01", 0), ("X01-2026", 1)]


def test_la_page_de_l_ancien_code_dit_boite_retiree(benevole, visiteur):
    _nommer(benevole, "X01", "Azul")
    _liberer("X01")
    assert "Boîte retirée" in benevole.get("/pret/X01-2026").text
    assert visiteur.get("/jeu/X01").status_code == 404           # QR vierge
    assert "Azul" not in visiteur.get("/catalogue").text


def test_export_et_etiquettes_sans_la_boite_liberee(bases):
    _nommer_en_base("X01", "Azul")
    _liberer("X01")
    conn = _conn()
    try:
        export = services.lignes_export_catalogue(conn)
        titres = services.titres_pour_etiquettes(conn, None)
        boites = services.exemplaires_pour_etiquettes(conn, None)
    finally:
        conn.close()
    assert "X01-2026" not in str(export)
    assert "Azul" not in {t["nom"] for t in titres}
    assert [b["id_exemplaire"] for b in boites] == ["001", "002", "003"]


def test_remise_en_service_d_une_boite_liberee(bases, admin):
    _nommer_en_base("X01", "Azul")
    _liberer("X01")
    r = admin.post("/admin/jeu/AZUL/exemplaire/X01-2026/remettre")
    assert r.status_code == 200
    assert _boite("X01-2026")["retiree_le"] is None
    conn = _conn()
    try:
        assert "X01-2026" in str(services.lignes_export_catalogue(conn))
        assert "X01-2026" in [b["id_exemplaire"]
                              for b in services.exemplaires_pour_etiquettes(conn, None)]
        assert not services.nom_ephemere_corrigeable(conn, "X01-2026", par_le_bureau=True)
        assert services.dispo_par_titre(conn, "AZUL") == (1, 1)
    finally:
        conn.close()
    png = admin.get("/admin/etiquette/X01-2026.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"


# ===========================================================================
# 6. L'ÉCRAN DU BUREAU
# ===========================================================================
def test_etiquettes_proposent_la_liberation_si_un_qr_est_nomme(admin):
    assert "Libérer les QR éphémères" not in admin.get("/admin/etiquettes").text
    _nommer_en_base("X01", "Azul")
    page = admin.get("/admin/etiquettes").text
    assert f'href="{LIBERER}">Libérer les QR éphémères</a>' in page


def test_la_confirmation_est_une_lecture_et_liste_tout(admin):
    _nommer_en_base("X01", "Azul")
    _nommer_en_base("X02", "Zombicide")
    conn = _conn()
    try:
        services.preter(conn, "X02")
    finally:
        conn.close()
    avant = _lire("SELECT * FROM exemplaires ORDER BY 1")
    page = admin.get(LIBERER).text
    assert _lire("SELECT * FROM exemplaires ORDER BY 1") == avant
    texte = _texte(page)
    assert page.count("<h1>") == 1 and "<h1>Libérer les QR éphémères</h1>" in page
    assert f"X01 Azul deviendra X01-{services.annee_locale()}" in texte
    assert "X02 Zombicide sortie : ne sera pas libérée" in texte
    # Tous les codes montrés partent : la boîte sortie est refusée par le
    # service, et le résultat la nomme avec la marche à suivre.
    assert '<input type="hidden" name="codes" value="X01">' in page
    assert '<input type="hidden" name="codes" value="X02">' in page
    assert "Libérer ces QR éphémères</button>" in page
    assert "<table" not in page and 'type="text"' not in page and "<textarea" not in page


def test_liberer_depuis_l_ecran(admin, _journal_isole):
    _nommer_en_base("X01", "Azul")
    _nommer_en_base("X02", "Zombicide")
    _nommer_en_base("X03", "Splendor")
    conn = _conn()
    try:
        services.preter(conn, "X02")
    finally:
        conn.close()
    annee = services.annee_locale()
    r = admin.post(LIBERER, data={"codes": ["X01", "X03"]})
    texte = _texte(r.text)
    assert r.status_code == 200
    assert re.search(r'role="status">\s*<p>2 QR éphémères libérés', r.text)
    assert f"X01 → X01-{annee} Azul" in texte and f"X03 → X03-{annee} Splendor" in texte
    assert "X02 Zombicide sortie : ne sera pas libérée" in texte     # reste à faire
    assert _boite("X01") is None and _boite("X03") is None
    assert _boite("X02") is not None


def test_une_boite_sortie_entre_l_affichage_et_l_envoi(admin):
    _nommer_en_base("X01", "Azul")
    _nommer_en_base("X02", "Zombicide")
    conn = _conn()
    try:
        services.preter(conn, "X02")                       # après l'affichage
    finally:
        conn.close()
    r = admin.post(LIBERER, data={"codes": ["X01", "X02"]})
    assert re.search(r'role="alert">\s*<p>1 boîte est encore sortie', r.text)
    assert 'href="/pret/X02">Ouvrir sa page de prêt</a>' in r.text
    assert 'href="/admin#fin-evenement">clôturez les prêts</a>' in r.text
    assert _boite("X01") is None and _boite("X02") is not None


def test_double_envoi_de_l_ecran(admin, _journal_isole):
    _nommer_en_base("X01", "Azul")
    admin.post(LIBERER, data={"codes": ["X01"]})
    r = admin.post(LIBERER, data={"codes": ["X01"]})
    assert "Ces QR éphémères étaient déjà libérés : rien n'a changé." in _texte(r.text)
    assert "Tous les QR éphémères sont vierges" in _texte(r.text)
    assert len(_journal(_journal_isole, "ephemere_libere")) == 1


def test_un_code_non_ephemere_envoye_est_ignore(admin):
    avant = _lire("SELECT * FROM exemplaires ORDER BY 1")
    admin.post(LIBERER, data={"codes": ["001", "003"]})
    assert _lire("SELECT * FROM exemplaires ORDER BY 1") == avant


def test_un_conflit_d_acces_le_dit(admin, monkeypatch):
    _nommer_en_base("X01", "Azul")

    def occupe(conn, code, **_):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(services, "liberer_code_ephemere", occupe)
    r = admin.post(LIBERER, data={"codes": ["X01"]})
    assert "Un autre geste s'enregistrait au même moment : X01" in _texte(r.text)
    assert _boite("X01") is not None


def test_rien_a_liberer(admin):
    page = admin.get(LIBERER).text
    assert "Aucun QR éphémère n'est nommé : il n'y a rien à libérer." in _texte(page)
    assert "Libérer ces QR éphémères</button>" not in page


def test_toutes_sorties_pas_de_bouton(admin):
    _nommer_en_base("X01", "Azul")
    conn = _conn()
    try:
        services.preter(conn, "X01")
    finally:
        conn.close()
    page = admin.get(LIBERER).text
    assert "Libérer ces QR éphémères</button>" not in page
    assert "Cette boîte est sortie : aucun QR ne peut être libéré" in _texte(page)


@pytest.mark.parametrize("qui", ["visiteur", "benevole"])
def test_l_ecran_est_garde_par_le_mot_de_passe(request, qui):
    client = request.getfixturevalue(qui)
    _nommer_en_base("X01", "Azul")
    assert client.get(LIBERER, follow_redirects=False).status_code in (302, 303, 401, 403)
    client.post(LIBERER, data={"codes": ["X01"]}, follow_redirects=False)
    assert _boite("X01") is not None and _boite("X01-2026") is None


def test_la_cloture_propose_la_liberation(admin):
    page = admin.post("/admin/cloturer-prets").text
    assert LIBERER not in page                                   # rien de nommé
    _nommer_en_base("X01", "Azul")
    conn = _conn()
    try:
        services.preter(conn, "X01")
    finally:
        conn.close()
    page = admin.post("/admin/cloturer-prets").text
    assert "1 QR éphémère est encore nommé." in _texte(page)
    assert f'<a class="lien" href="{LIBERER}">Libérer les QR éphémères</a>' in page
    assert _boite("X01") is not None                           # la clôture ne libère pas


def test_le_journal_une_ligne_par_boite(admin, _journal_isole):
    _nommer_en_base("X01", "Azul")
    _nommer_en_base("X02", "Zombicide")
    conn = _conn()
    try:
        services.preter(conn, "X02")
    finally:
        conn.close()
    admin.post(LIBERER, data={"codes": ["X01", "X02"]})
    ligne, = _journal(_journal_isole, "ephemere_libere")
    annee = services.annee_locale()
    assert (ligne["module"], ligne["ref"], ligne["objet"], ligne["ok"], ligne["qui"]) == (
        "catalogue", f"X01 → X01-{annee}", "Azul", True, "admin")
    assert _journal(_journal_isole, "exemplaire_retire") == []
    assert "ephemere_libere" in journal.ACTIONS


# ===========================================================================
# 7. LE MOTIF « PASSAGE »
# ===========================================================================
def test_le_motif_passage_n_est_pas_propose_au_retrait(admin):
    page = admin.get("/admin/jeu/CATANE/exemplaire/002/retirer").text
    assert "Jeu de passage" not in page
    assert list(services.MOTIFS_RETRAIT_PROPOSES) == ["perdue", "abimee", "donnee", "autre"]
    admin.post("/admin/jeu/CATANE/exemplaire/002/retirer", data={"motif": "passage"})
    assert _boite("002")["motif_retrait"] is None            # forgé : sans motif


def test_le_motif_passage_s_affiche_sur_la_fiche(admin):
    _nommer_en_base("X01", "Azul")
    _liberer("X01")
    page = admin.get("/admin/jeu/AZUL").text
    assert "Motif : jeu de passage, reparti" in page and "X01-2026" in page
