"""
RATTACHER LES BOÎTES D'UN JEU À UN AUTRE JEU (série « fiche », lot 5).

Le cas de production du 2026-10-28 : des boîtes saisies sous « time line »
(référence TIME_LINE), renommé « Timelines » à l'écran, ne rejoignent pas le
« Timelines » (TIMELINES) déjà présent — la référence ne suit pas le nom. Ce
fichier vérifie :

1. **Le service** (`services.rattacher_exemplaires`) : les boîtes passent à la
   cible avec tout ce qui leur est attaché (prêt en cours, signalements,
   rangement, statistiques), le jeu vidé est supprimé après avoir complété les
   cases VIDES de la cible, sans jamais écraser une valeur de la cible ; les
   refus n'écrivent rien ; une étape qui échoue n'écrit rien.
2. **La fiche** : l'encadré « un autre jeu porte déjà ce nom » (au slug près,
   calculé à l'affichage), la recherche (celle de la liste des jeux, le jeu
   ouvert en moins), la confirmation, l'arrivée sur la cible, la garde.
3. **L'import** : l'aller-retour export → import après un rattachement ne
   change rien ; un fichier d'AVANT le rattachement le défait, comme le veut
   la règle de l'import, mais le dit.
"""

import re

import pytest

from app import journal, services, vignettes
from app.routes import admin as routes_admin
from tests.test_bgg_images import JPEG, _adresse
from tests.test_bgg_relier import _copier
from tests.test_fiche_jeu import (_conn, _executer, _exporter, _importer,  # noqa: F401
                                  _journal, _titre, anonyme, bases, client)


@pytest.fixture
def doublon(bases):
    """
    Le cas de production, renommage déjà fait : « Timelines » (TIMELINES, une
    boîte 020, âge 8, catégorie Ambiance) et « Timelines » (TIME_LINE, ex
    « time line », boîtes 010 et 011, éditeur, âge 10, catégorie
    Chronologie que nul autre jeu ne porte).
    """
    _executer(
        "INSERT INTO titres (reference_titre, nom, type_jeu, age_min) "
        "VALUES ('TIMELINES', 'Timelines', 'Jeu', 8)")
    _executer(
        "INSERT INTO titres (reference_titre, nom, editeur, age_min, duree_min, "
        "date_achat) VALUES ('TIME_LINE', 'Timelines', 'Asmodee', 10, 15, "
        "'2024-05-01')")
    for id_ex, ref in (("010", "TIME_LINE"), ("011", "TIME_LINE"),
                       ("020", "TIMELINES")):
        _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
                  "VALUES (?, ?)", (id_ex, ref))
    conn = _conn()
    try:
        services.ecrire_categories_titre(conn, "TIMELINES", "Ambiance")
        services.ecrire_categories_titre(conn, "TIME_LINE", "Chronologie")
    finally:
        conn.close()
    return bases


def _rattacher(source="TIME_LINE", cible="TIMELINES", ids=None):
    conn = _conn()
    try:
        return services.rattacher_exemplaires(conn, source, cible, ids)
    finally:
        conn.close()


def _boites(ref):
    conn = _conn()
    try:
        return [r[0] for r in conn.execute(
            "SELECT id_exemplaire FROM exemplaires WHERE reference_titre = ? "
            "ORDER BY id_exemplaire", (ref,))]
    finally:
        conn.close()


def _instantane():
    """Toute la base de prêt, table par table, pour dire « rien n'a bougé »."""
    conn = _conn()
    try:
        return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in ("titres", "exemplaires", "titres_categories",
                          "categories_jeu", "prets", "signalements")}
    finally:
        conn.close()


# ===========================================================================
# 1. LE SERVICE
# ===========================================================================
def test_le_rattachement_deplace_toutes_les_boites_et_supprime_la_source(doublon):
    resultat = _rattacher()
    assert resultat["refus"] is None
    assert resultat["boites"] == ["010", "011"]
    assert resultat["supprime"] is True
    assert _boites("TIMELINES") == ["010", "011", "020"]
    assert _titre("TIME_LINE") is None
    assert _titre("TIMELINES")["modifie_le"]
    assert resultat["apres"]["nom"] == "Timelines"


def test_la_cible_garde_ses_valeurs_et_ne_recoit_que_ses_cases_vides(doublon):
    resultat = _rattacher()
    cible = _titre("TIMELINES")
    assert cible["age_min"] == 8                      # la sienne, pas 10
    assert cible["type_jeu"] == "Jeu"
    assert cible["editeur"] == "Asmodee"              # case vide : reprise
    assert cible["duree_min"] == 15
    assert cible["date_achat"] == "2024-05-01"
    assert cible["categorie"] == "Ambiance"           # la sienne
    assert set(resultat["reprises"]) == {"editeur", "duree_min", "date_achat"}
    assert ("Âge minimum", "10") in resultat["perdues"]
    assert ("Catégorie", "Chronologie") in resultat["perdues"]


def test_les_categories_d_une_cible_sans_categorie_sont_reprises(doublon):
    conn = _conn()
    try:
        services.ecrire_categories_titre(conn, "TIMELINES", "")
    finally:
        conn.close()
    _rattacher()
    assert _titre("TIMELINES")["categorie"] == "Chronologie"


def test_une_categorie_que_plus_aucun_jeu_ne_porte_est_purgee(doublon):
    _rattacher()
    conn = _conn()
    try:
        cles = {c["cle"] for c in services.lister_categories_jeu(conn)}
        liaisons = conn.execute("SELECT COUNT(*) FROM titres_categories "
                                "WHERE reference_titre = 'TIME_LINE'").fetchone()[0]
    finally:
        conn.close()
    assert "chronologie" not in cles
    assert "ambiance" in cles
    assert liaisons == 0


def test_les_joueurs_ne_sont_pas_repris_s_ils_se_contrediraient(doublon):
    _executer("UPDATE titres SET nb_joueurs_min = 5 WHERE reference_titre = 'TIMELINES'")
    _executer("UPDATE titres SET nb_joueurs_min = 2, nb_joueurs_max = 4 "
              "WHERE reference_titre = 'TIME_LINE'")
    resultat = _rattacher()
    cible = _titre("TIMELINES")
    assert (cible["nb_joueurs_min"], cible["nb_joueurs_max"]) == (5, None)
    assert "nb_joueurs_max" not in resultat["reprises"]


def test_les_joueurs_compatibles_completent_le_couple(doublon):
    _executer("UPDATE titres SET nb_joueurs_min = 2 WHERE reference_titre = 'TIMELINES'")
    _executer("UPDATE titres SET nb_joueurs_min = 3, nb_joueurs_max = 8 "
              "WHERE reference_titre = 'TIME_LINE'")
    _rattacher()
    cible = _titre("TIMELINES")
    assert (cible["nb_joueurs_min"], cible["nb_joueurs_max"]) == (2, 8)


def test_les_lettres_sont_reprises_ensemble_ou_pas_du_tout(doublon):
    _executer("UPDATE titres SET lettre_public = 'T' WHERE reference_titre = 'TIME_LINE'")
    _rattacher()
    cible = _titre("TIMELINES")
    assert cible["lettre_public"] == "T"


def test_les_lettres_de_la_cible_ne_sont_pas_touchees(doublon):
    _executer("UPDATE titres SET lettre_public = 'E' WHERE reference_titre = 'TIMELINES'")
    _executer("UPDATE titres SET lettre_public = 'T' WHERE reference_titre = 'TIME_LINE'")
    resultat = _rattacher()
    assert _titre("TIMELINES")["lettre_public"] == "E"
    assert "lettres" not in resultat["reprises"]


def test_la_fiche_bgg_est_reprise_par_une_cible_qui_n_en_a_pas(doublon):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'TIME_LINE'")
    _rattacher()
    assert _titre("TIMELINES")["bgg_id"] == 13


def test_la_fiche_bgg_n_est_pas_reprise_par_une_cible_pas_sur_bgg(doublon):
    """La contrainte de `titres.bgg_absent` refuserait l'identifiant."""
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'TIMELINES'")
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'TIME_LINE'")
    resultat = _rattacher()
    cible = _titre("TIMELINES")
    assert (cible["bgg_id"], cible["bgg_absent"]) == (None, 1)
    assert ("Fiche BoardGameGeek", "fiche n° 13") in resultat["perdues"]


def test_la_marque_pas_sur_bgg_de_la_source_n_est_jamais_reprise(doublon):
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'TIME_LINE'")
    _rattacher()
    assert _titre("TIMELINES")["bgg_absent"] == 0


def test_une_boite_sortie_se_rattache_et_son_pret_la_suit(doublon):
    conn = _conn()
    try:
        numero = services.preter(conn, "010")
    finally:
        conn.close()
    assert _rattacher()["sorties"] == 1
    conn = _conn()
    try:
        pret = services.pret_en_cours(conn, "010")
        info = services.info_exemplaire(conn, "010")
        assert pret["numero_pochette"] == numero
        assert info["reference_titre"] == "TIMELINES"
        services.rendre(conn, "010")
        assert services.pret_en_cours(conn, "010") is None
    finally:
        conn.close()


def test_signalements_et_emplacements_suivent_la_boite(doublon):
    conn = _conn()
    try:
        id_emp = services.creer_emplacement_rangement(conn, "Étagère A")
        conn.execute("UPDATE exemplaires SET emplacement_evenement = 'Table 3', "
                     "emplacement_local_id = ? WHERE id_exemplaire = '011'", (id_emp,))
        conn.commit()
        services.creer_signalement(conn, "011", None, "pion manquant")
    finally:
        conn.close()
    _rattacher()
    conn = _conn()
    try:
        ligne = conn.execute("SELECT * FROM exemplaires WHERE id_exemplaire = '011'").fetchone()
        assert (ligne["emplacement_evenement"], ligne["emplacement_local_id"]) == ("Table 3", id_emp)
        assert len(services.signalements_ouverts(conn, "011")) == 1
    finally:
        conn.close()


def test_les_prets_passes_comptent_pour_la_cible(doublon):
    for id_ex in ("010", "011", "020"):
        _executer("INSERT INTO prets (id_exemplaire, date_sortie, date_retour, motif) "
                  "VALUES (?, '2026-10-01T10:00:00', '2026-10-01T11:00:00', 'pret')",
                  (id_ex,))
    _rattacher()
    conn = _conn()
    try:
        palmares = {p["reference_titre"]: p for p in services.palmares(conn)}
    finally:
        conn.close()
    assert palmares["TIMELINES"]["nb_prets"] == 3
    assert palmares["TIMELINES"]["nb_exemplaires"] == 3
    assert "TIME_LINE" not in palmares


@pytest.mark.parametrize("source, cible, ids, code", [
    ("INCONNU", "TIMELINES", None, "source_inconnue"),
    ("TIME_LINE", "INCONNU", None, "cible_inconnue"),
    ("TIME_LINE", "TIME_LINE", None, "meme_jeu"),
    ("TIME_LINE", "TIMELINES", ["020"], "boite_etrangere"),
    ("TIME_LINE", "TIMELINES", [], "sans_boite"),
])
def test_les_refus_n_ecrivent_rien(doublon, source, cible, ids, code):
    avant = _instantane()
    assert _rattacher(source, cible, ids)["refus"] == code
    assert _instantane() == avant


def test_un_jeu_sans_boite_est_refuse(doublon):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('VIDE', 'Vide')")
    avant = _instantane()
    assert _rattacher("VIDE", "TIMELINES")["refus"] == "sans_boite"
    assert _instantane() == avant


def test_une_etape_qui_echoue_n_ecrit_rien(doublon, monkeypatch):
    avant = _instantane()

    def panne(conn):
        raise RuntimeError("panne au milieu")
    monkeypatch.setattr(services, "purger_categories_orphelines", panne)
    with pytest.raises(RuntimeError):
        _rattacher()
    assert _instantane() == avant


def test_une_partie_des_boites_seulement_ne_supprime_rien(doublon):
    """Pour un lot suivant : la source qui garde une boîte reste, intacte."""
    resultat = _rattacher(ids=["010"])
    assert resultat["supprime"] is False and resultat["reprises"] == {}
    assert _boites("TIME_LINE") == ["011"]
    assert _titre("TIMELINES")["editeur"] is None


def test_l_identifiant_des_boites_ne_change_jamais(doublon):
    avant = set(_boites("TIME_LINE")) | set(_boites("TIMELINES"))
    _rattacher()
    assert set(_boites("TIMELINES")) == avant


def test_la_suppression_ne_laisse_aucune_ligne(doublon):
    """
    Le jeu supprimé n'est pas une donnée personnelle : un SELECT suffit (pas
    de `secure_delete` à vérifier sur les octets, voir le compte rendu).
    """
    _rattacher()
    conn = _conn()
    try:
        for table in ("titres", "exemplaires", "titres_categories"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} "
                                "WHERE reference_titre = 'TIME_LINE'").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


# ===========================================================================
# 2. LES HOMONYMES
# ===========================================================================
@pytest.mark.parametrize("nom", ["Timelines", "TIMELINES", "timelinés", "Timelines !"])
def test_les_homonymes_se_trouvent_au_slug_pres(doublon, nom):
    _executer("UPDATE titres SET nom = ? WHERE reference_titre = 'TIME_LINE'", (nom,))
    conn = _conn()
    try:
        trouves = services.homonymes(conn, "TIMELINES")
    finally:
        conn.close()
    assert [h["reference_titre"] for h in trouves] == ["TIME_LINE"]
    assert trouves[0]["boites"] == 2


def test_un_nom_proche_n_est_pas_un_homonyme(doublon):
    _executer("UPDATE titres SET nom = 'time line' WHERE reference_titre = 'TIME_LINE'")
    conn = _conn()
    try:
        assert services.homonymes(conn, "TIMELINES") == []
    finally:
        conn.close()


# ===========================================================================
# 3. LA FICHE
# ===========================================================================
def _texte(page):
    """La page, espaces et sauts de ligne réduits à un espace."""
    return re.sub(r"\s+", " ", page)


def test_la_duree_reprise_change_l_etiquette_de_la_cible(client, doublon):
    """La durée entre dans le code de classement : les boîtes de la cible aussi."""
    r = client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"},
                    follow_redirects=False)
    assert "&etiquette=1" in r.headers["location"]


def _encadre(page):
    m = re.search(r'<div class="resultat resultat-attention resultat-liste" '
                  r'role="status" id="homonymes">.*?</div>', page, re.S)
    return m.group(0) if m else None


def test_l_encadre_apparait_a_l_affichage_avec_un_bouton(client, doublon):
    page = client.get("/admin/jeu/TIME_LINE").text
    encadre = _encadre(page)
    assert encadre is not None
    assert "Un autre jeu porte déjà ce nom" in encadre
    assert 'href="/admin/jeu/TIMELINES"' in encadre
    assert ("Rattacher les boîtes de ce jeu à « Timelines » (TIMELINES)</button>"
            in encadre)
    assert 'action="/admin/jeu/TIME_LINE/rattacher"' in encadre
    assert 'name="cible" value="TIMELINES"' in encadre


def test_l_encadre_est_absent_sans_homonyme(client, doublon):
    assert _encadre(client.get("/admin/jeu/CATANE").text) is None
    _executer("UPDATE titres SET nom = 'time line' WHERE reference_titre = 'TIME_LINE'")
    assert _encadre(client.get("/admin/jeu/TIME_LINE").text) is None


def test_l_encadre_apparait_apres_le_renommage_qui_cree_le_doublon(client, bases):
    """Aucun code de plus : la fiche se calcule à l'affichage."""
    from tests.test_fiche_jeu import _enregistrer
    r = _enregistrer(client, "DOBBLE", nom="Catane")
    page = client.get(r.headers["location"]).text
    assert "Rattacher les boîtes de ce jeu à « Catane » (CATANE)" in page


def test_trois_homonymes_donnent_un_bouton_par_destination(client, doublon):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('TIMELINES_2', 'timelines')")
    _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) "
              "VALUES ('030', 'TIMELINES_2')")
    encadre = _encadre(client.get("/admin/jeu/TIME_LINE").text)
    assert "2 autres jeux portent déjà ce nom" in encadre
    assert len(re.findall(r"<button[^>]*>Rattacher les boîtes de ce jeu à", encadre)) == 2
    assert "(TIMELINES)</button>" in encadre and "(TIMELINES_2)</button>" in encadre


def test_sans_boite_l_encadre_n_offre_que_le_lien(client, doublon):
    _executer("UPDATE exemplaires SET reference_titre = 'TIMELINES' "
              "WHERE reference_titre = 'TIME_LINE'")
    encadre = _encadre(client.get("/admin/jeu/TIME_LINE").text)
    assert 'href="/admin/jeu/TIMELINES"' in encadre
    assert "<button" not in encadre


def test_le_bouton_mene_a_la_confirmation_qui_n_ecrit_rien(client, doublon):
    avant = _instantane()
    r = client.get("/admin/jeu/TIME_LINE/rattacher?cible=TIMELINES")
    assert r.status_code == 200
    page = r.text
    assert ("Rattacher les boîtes de « Timelines » (TIME_LINE) à « Timelines » "
            "(TIMELINES)</h1>") in page
    assert "Éditeur : Asmodee" in page                   # repris
    assert "Âge minimum : 10" in page                    # perdu
    assert "<strong>supprimé</strong>" in page
    assert "Rattacher les 2 boîtes à « Timelines » (TIMELINES)</button>" in page
    assert "Son nom" not in page                       # même nom : rien de perdu
    assert 'href="/admin/jeu/TIME_LINE"' in page         # annuler
    assert _instantane() == avant


def test_la_confirmation_annonce_une_boite_sortie(client, doublon):
    conn = _conn()
    try:
        services.preter(conn, "010")
    finally:
        conn.close()
    page = client.get("/admin/jeu/TIME_LINE/rattacher?cible=TIMELINES").text
    assert "1 boîte est sortie" in page


@pytest.mark.parametrize("cible, code, statut", [
    ("TIME_LINE", "meme_jeu", 409), ("INCONNU", "cible_inconnue", 404)])
def test_la_confirmation_d_un_refus_garde_un_retour(client, doublon, cible, code, statut):
    r = client.get(f"/admin/jeu/TIME_LINE/rattacher?cible={cible}")
    assert r.status_code == statut
    assert routes_admin.MESSAGES_RATTACHEMENT[code].split(":")[0] in r.text.replace("&#39;", "'")
    assert 'role="alert"' in r.text
    assert 'href="/admin/jeu/TIME_LINE"' in r.text


def test_la_recherche_exclut_le_jeu_ouvert(client, doublon):
    page = client.get("/admin/jeu/TIME_LINE?rattacher=timel").text
    section = page[page.index('id="rattacher"'):]
    assert 'name="cible" value="TIMELINES"' in section
    assert 'name="cible" value="TIME_LINE"' not in section
    assert "Choisir « Timelines »" in section
    assert 'value="timel"' in section                    # la case garde la saisie


def test_la_recherche_reutilise_celle_de_la_liste_des_jeux(client, doublon, monkeypatch):
    vus = []
    origine = services.lister_catalogue

    def espion(conn, *args, **kwargs):
        vus.append(kwargs.get("q"))
        return origine(conn, *args, **kwargs)
    monkeypatch.setattr(services, "lister_catalogue", espion)
    client.get("/admin/jeu/TIME_LINE?rattacher=Cat")
    assert vus == ["Cat"]


def test_une_recherche_sans_resultat_le_dit(client, doublon):
    page = _texte(client.get("/admin/jeu/TIME_LINE?rattacher=zzz").text)
    assert "Aucun autre jeu ne porte un nom qui contient « zzz »" in page


def test_la_recherche_est_bornee(client, doublon, monkeypatch):
    monkeypatch.setattr(routes_admin, "RESULTATS_RATTACHEMENT", 1)
    page = _texte(client.get("/admin/jeu/TIME_LINE?rattacher=e").text)
    section = page[page.index('id="rattacher"'):]
    assert len(re.findall(r'name="cible"', section)) == 1
    assert "3 jeux trouvés, les 1 premiers affichés : précisez le nom" in section


def test_la_recherche_ne_part_pas_au_journal(client, doublon, _journal_isole):
    client.get("/admin/jeu/TIME_LINE?rattacher=MotCherche")
    client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"})
    if _journal_isole.exists():
        assert "MotCherche" not in _journal_isole.read_text(encoding="utf-8")


def test_rattacher_mene_a_la_cible_avec_le_message(client, doublon):
    r = client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"},
                    follow_redirects=False)
    assert r.status_code == 303
    lieu = r.headers["location"]
    assert lieu.startswith("/admin/jeu/TIMELINES?ok=rattache&boites=2&depuis=TIME_LINE")
    page = client.get(lieu).text
    assert ("2 boîtes ont rejoint ce jeu. Elles venaient du jeu de référence "
            "TIME_LINE, qui est supprimé.") in page
    assert "Repris du jeu supprimé, dans les cases qui étaient vides : Durée (minutes), " \
           "Éditeur, Date d&#39;achat." in page
    assert _encadre(page) is None
    for id_ex in ("010", "011", "020"):
        assert f'src="/admin/etiquette/{id_ex}.png"' in page


def test_la_reimpression_est_proposee_quand_l_etiquette_change(client, doublon):
    _executer("UPDATE titres SET nom = 'time line' WHERE reference_titre = 'TIME_LINE'")
    r = client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"},
                    follow_redirects=False)
    assert "&etiquette=1" in r.headers["location"]
    page = client.get(r.headers["location"]).text
    assert "Réimprimer les étiquettes de ce jeu" in page
    assert 'name="references" value="TIMELINES"' in page


def test_pas_de_reimpression_quand_rien_ne_change_a_l_imprime(client, doublon):
    """Même nom, rien d'imprimé repris : l'étiquette ne change pas."""
    _executer("UPDATE titres SET duree_min = NULL, age_min = 8 "
              "WHERE reference_titre = 'TIME_LINE'")
    r = client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"},
                    follow_redirects=False)
    assert "etiquette=1" not in r.headers["location"]


@pytest.mark.parametrize("parametres, attendu", [
    ("boites=1&depuis=TIME_LINE&repris=", "1 boîte a rejoint ce jeu. Elle venait"),
    ("boites=3&depuis=TIME_LINE&repris=", "ce jeu garde toutes ses valeurs."),
    ("boites=3&depuis=<b>x</b>&repris=editeur", "3 boîtes ont rejoint ce jeu."),
    ("boites=abc&repris=inconnu", "Les boîtes ont rejoint ce jeu."),
])
def test_le_message_ne_lit_que_des_valeurs_fermees(client, doublon, parametres, attendu):
    page = client.get(f"/admin/jeu/TIMELINES?ok=rattache&{parametres}").text
    assert attendu in page
    assert "<b>x</b>" not in page and "inconnu" not in page


def test_le_rattachement_est_journalise(client, doublon, _journal_isole):
    client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"})
    ligne, = _journal(_journal_isole, "exemplaires_rattaches")
    assert ligne["module"] == "catalogue"
    assert ligne["ref"] == "TIMELINES"
    assert ligne["objet"] == "Timelines ← 2 boîtes de TIME_LINE"
    assert "exemplaires_rattaches" in journal.ACTIONS


def test_un_refus_ne_journalise_rien(client, doublon, _journal_isole):
    r = client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIME_LINE"})
    assert r.status_code == 409
    assert _journal(_journal_isole, "exemplaires_rattaches") == []


def test_sans_session_rien_ne_bouge(anonyme, doublon):
    avant = _instantane()
    r = anonyme.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"},
                     follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    r = anonyme.get("/admin/jeu/TIME_LINE/rattacher?cible=TIMELINES",
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert _instantane() == avant


def test_l_image_du_jeu_supprime_est_nettoyee(client, doublon):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'TIME_LINE'")
    _executer("UPDATE titres SET bgg_id = 14 WHERE reference_titre = 'TIMELINES'")
    _copier({13: {"nom": "Timeline", "vignette": _adresse(13)},
             14: {"nom": "Timeline 2", "vignette": _adresse(14)}})
    vignettes.enregistrer(13, _adresse(13), JPEG)
    vignettes.enregistrer(14, _adresse(14), JPEG)
    client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"})
    assert vignettes.chemin_servi(13, _adresse(13)) is None
    assert vignettes.chemin_servi(14, _adresse(14)) is not None


def test_l_image_reprise_par_la_cible_reste(client, doublon):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'TIME_LINE'")
    _copier({13: {"nom": "Timeline", "vignette": _adresse(13)}})
    vignettes.enregistrer(13, _adresse(13), JPEG)
    client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"})
    assert _titre("TIMELINES")["bgg_id"] == 13
    assert vignettes.chemin_servi(13, _adresse(13)) is not None


def test_la_fiche_publique_d_une_boite_deplacee_porte_la_cible(anonyme, doublon):
    _executer("UPDATE titres SET nom = 'time line' WHERE reference_titre = 'TIME_LINE'")
    _rattacher()
    page = anonyme.get("/jeu/010").text
    assert "Timelines" in page and "time line" not in page


# ===========================================================================
# 4. L'IMPORT
# ===========================================================================
def test_aller_retour_apres_rattachement_sans_effet(client, doublon, tmp_path):
    _rattacher()
    avant = _instantane()
    res = _importer(_exporter(tmp_path / "apres.csv"))
    assert res["corrections_defaites"] == []
    apres = _instantane()
    assert apres["exemplaires"] == avant["exemplaires"]
    assert [t[0] for t in apres["titres"]] == [t[0] for t in avant["titres"]]
    assert _titre("TIME_LINE") is None


def test_le_scenario_de_production_se_repare_par_le_rattachement(client, bases, tmp_path):
    """
    Constat 3 du prompt, rejoué : « time line » renommé « Timelines » à
    l'écran reste à part, même après un import ; le rattachement le règle, et
    l'export suivant le confirme.
    """
    from tests.test_fiche_jeu import _enregistrer
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('TIMELINES', 'Timelines')")
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('TIME_LINE', 'time line')")
    _executer("INSERT INTO exemplaires VALUES ('020', 'TIMELINES', NULL, NULL)")
    _executer("INSERT INTO exemplaires VALUES ('010', 'TIME_LINE', NULL, NULL)")
    _enregistrer(client, "TIME_LINE", nom="Timelines")
    _importer(_exporter(tmp_path / "avant.csv"))
    assert _boites("TIME_LINE") == ["010"]                 # toujours à part
    client.post("/admin/jeu/TIME_LINE/rattacher", data={"cible": "TIMELINES"})
    _importer(_exporter(tmp_path / "apres.csv"))
    assert _boites("TIMELINES") == ["010", "020"]
    assert _titre("TIME_LINE") is None


def test_un_fichier_d_avant_le_rattachement_le_dit_a_l_import(client, doublon, tmp_path):
    """
    POINT 4 : la règle de l'import ne change pas — le fichier fait foi et
    rend les boîtes à leur ancien nom — mais `modifie_le`, posé sur la cible
    au rattachement, fait signaler la boîte qui quitte la fiche.
    """
    _executer("UPDATE titres SET nom = 'time line' WHERE reference_titre = 'TIME_LINE'")
    ancien = _exporter(tmp_path / "ancien.csv")
    _rattacher()
    simulation = _importer(ancien, dry_run=True)
    fiche, = [f for f in simulation["corrections_defaites"]
              if f["reference_titre"] == "TIMELINES"]
    assert ("Nom du jeu", "Timelines", "time line") in fiche["ecarts"]
    reel = _importer(ancien)
    assert reel["corrections_defaites"] == simulation["corrections_defaites"]
    assert _boites("TIME_LINE") == ["010", "011"]          # le fichier fait foi
