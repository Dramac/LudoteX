"""
Page bénévole « Jeux sortis » (`/sortis`, lot bilan-1).

L'outil de comptoir qui vivait en bas de `/stats` : accès réservé, rattaché
au cœur du prêt (pas au module « stats »), plus anciennes sorties en tête,
repère des longues sorties, recherche par nom ou code (portée de l'ancien
tests/test_stats_sortis_recherche.py), anciens liens de `/stats`.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from app import services
from app.services import SEUIL_LONGUE_SORTIE_S

JETON = "jeton-test-sortis-32-caracteres-xx"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-sortis")
    monkeypatch.setenv("PRET_TOKEN", JETON)
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.executemany("INSERT INTO titres (reference_titre, nom) VALUES (?, ?)",
                     [("CATAN", "Catan"), ("DIXIT", "Dixit"), ("ECLIPSE", "Éclipse")])
    conn.executemany(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
        [("001", "CATAN"), ("002", "DIXIT"), ("003", "CATAN"), ("042", "ECLIPSE")],
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _conn():
    from app import db

    return db.get_connection()


def _benevole(client):
    client.cookies.set("jeton_pret", JETON)
    return client


def _sortie(code, il_y_a, pochette=None, motif="pret", date=None):
    """Écrit un prêt en cours, antidaté de `il_y_a` (timedelta)."""
    if date is None:
        date = (datetime.now(timezone.utc) - il_y_a).isoformat(timespec="seconds")
    if pochette is None:
        pochette = 0 if motif == "tournoi" else 1
    conn = _conn()
    conn.execute("INSERT INTO prets (id_exemplaire, numero_pochette, date_sortie, motif)"
                 " VALUES (?, ?, ?, ?)", (code, pochette, date, motif))
    conn.commit()
    conn.close()


def _noms_dans_l_ordre(html, bloc):
    """Les noms des cartes d'un bloc (« Prêtés au public » ou « En tournoi »)."""
    debut = html.index(f"<h2>{bloc}")
    fin = html.find("<h2>", debut + 4)
    return re.findall(r'class="sortie-nom">([^<]+)<', html[debut:fin if fin > 0 else None])


@pytest.fixture
def quatre_sorties(client):
    _benevole(client)
    for code in ("001", "002", "042"):
        client.post(f"/pret/{code}/preter")
    client.post("/pret/003/tournoi")
    return client


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------
def test_sans_jeton_page_acces_reserve_sans_aucun_numero(client):
    _sortie("001", timedelta(minutes=20), pochette=17)
    r = client.get("/sortis")
    assert r.status_code == 403
    assert "Accès réservé aux bénévoles" in r.text
    assert 'href="/catalogue"' in r.text            # un chemin clair, pas une impasse
    assert "<strong>17</strong>" not in r.text and "Catan" not in r.text
    assert "pochette <strong>" not in r.text


def test_avec_jeton_la_page_s_affiche(client):
    _sortie("001", timedelta(minutes=20), pochette=17)
    r = _benevole(client).get("/sortis")
    assert r.status_code == 200
    assert "pochette <strong>17</strong>" in r.text


def test_avec_la_session_admin_la_page_s_affiche(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-sortis"})
    assert client.get("/sortis").status_code == 200


@pytest.mark.parametrize("etat", ["desactive", "benevoles", "discret"])
def test_la_page_ne_suit_pas_le_module_stats(client, etat):
    """Rattachée au cœur du prêt : le comptoir garde sa liste."""
    from app import modules

    conn = _conn()
    modules.ecrire_etat_module(conn, "stats", etat)
    conn.close()
    r = _benevole(client).get("/sortis")
    assert r.status_code == 200
    assert 'href="/sortis"' in r.text               # le lien du menu reste


def test_le_menu_benevole_pointe_vers_la_nouvelle_page(client):
    html = _benevole(client).get("/catalogue").text
    menu = html.split('class="menu-benevole"')[1].split("</nav>")[0]
    assert '<a href="/sortis">Jeux sortis</a>' in menu
    assert "/stats#sortis" not in html


# ---------------------------------------------------------------------------
# Ordre et repère des longues sorties
# ---------------------------------------------------------------------------
def test_les_plus_anciennes_sorties_en_tete_dans_chaque_bloc(client):
    _sortie("002", timedelta(minutes=10), pochette=2)
    _sortie("001", timedelta(hours=2), pochette=1)
    _sortie("042", timedelta(minutes=50), pochette=3)
    conn = _conn()
    conn.executemany("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES (?, ?)",
                     [("004", "DIXIT"), ("005", "ECLIPSE")])
    conn.commit()
    conn.close()
    _sortie("003", timedelta(minutes=5), motif="tournoi")
    _sortie("004", timedelta(hours=1), motif="tournoi")
    _sortie("005", timedelta(minutes=30), motif="tournoi")
    html = _benevole(client).get("/sortis").text
    assert _noms_dans_l_ordre(html, "Prêtés au public") == ["Catan", "Éclipse", "Dixit"]
    assert _noms_dans_l_ordre(html, "En tournoi") == ["Dixit", "Éclipse", "Catan"]


def _une_ligne(il_y_a=None, motif="pret", date=None):
    _sortie("001", il_y_a, motif=motif, date=date)
    instant = datetime.now(timezone.utc)
    conn = _conn()
    try:
        groupes = services.lister_prets_en_cours(conn, instant=instant)
    finally:
        conn.close()
    return groupes[motif][0]


def test_seuil_juste_en_dessous_pas_de_repere(client):
    """2 h 59 : pas de repère. Marge d'une minute contre le temps du test."""
    ligne = _une_ligne(timedelta(seconds=SEUIL_LONGUE_SORTIE_S - 60))
    assert ligne["longue"] is False


def test_seuil_atteint_le_repere_s_allume(client):
    """Borne INCLUSIVE : à la seconde du seuil, c'est une longue sortie."""
    instant = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
    sortie = instant - timedelta(seconds=SEUIL_LONGUE_SORTIE_S)
    _sortie("001", None, date=sortie.isoformat(timespec="seconds"))
    conn = _conn()
    try:
        ligne = services.lister_prets_en_cours(conn, instant=instant)["pret"][0]
        juste_avant = services.lister_prets_en_cours(
            conn, instant=instant - timedelta(seconds=1))["pret"][0]
    finally:
        conn.close()
    assert ligne["longue"] is True
    assert juste_avant["longue"] is False


def test_sortie_ouverte_depuis_la_veille(client):
    """Clôture oubliée : la boîte dehors depuis hier est longue, et sa date
    est donnée en plus de l'heure."""
    ligne = _une_ligne(timedelta(hours=26))
    assert ligne["longue"] is True
    assert ligne["duree_txt"] == "depuis 1 j 2 h"
    assert re.fullmatch(r"le \d\d/\d\d à \d\d:\d\d", ligne["sortie_txt"])


def test_sortie_du_jour_heure_seule(client):
    instant = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)   # 16:00 à Paris
    _sortie("001", None, date="2026-11-14T14:59:30+00:00")
    conn = _conn()
    try:
        ligne = services.lister_prets_en_cours(conn, instant=instant)["pret"][0]
    finally:
        conn.close()
    assert ligne["sortie_txt"] == "à 15:59"
    assert ligne["duree_txt"] == "depuis 0 min"


def test_un_tournoi_long_n_a_pas_de_repere(client):
    ligne = _une_ligne(timedelta(hours=5), motif="tournoi")
    assert ligne["longue"] is False


@pytest.mark.parametrize("date", ["pas-une-date", "2026-11-14T10:00:00"])
def test_date_illisible_ou_sans_fuseau_ne_plante_pas(client, date):
    _sortie("001", None, date=date)
    r = _benevole(client).get("/sortis")
    assert r.status_code == 200
    assert "Catan" in r.text and "durée inconnue" in r.text
    assert "sortie à une heure inconnue" in r.text
    assert "Longue sortie</span>" not in r.text


def test_le_repere_est_ecrit_pas_seulement_colore(client):
    _sortie("001", timedelta(hours=4), pochette=1)
    _sortie("002", timedelta(minutes=5), pochette=2)
    html = _benevole(client).get("/sortis").text
    assert html.count('class="sortie sortie--longue"') == 1
    assert html.count('<span class="sortie-repere">Longue sortie</span>') == 1
    heures = SEUIL_LONGUE_SORTIE_S // 3600
    assert f"Longue sortie : dehors depuis {heures} heures ou plus." in html


# ---------------------------------------------------------------------------
# Contenu et cas vides
# ---------------------------------------------------------------------------
def test_aucun_jeu_sorti(client):
    html = _benevole(client).get("/sortis").text
    assert "Aucun jeu n'est sorti en ce moment." in html
    # Prêtés au public, puis une section par sortie sans pochette (tournoi,
    # démo — lot comptoir-3).
    assert html.count("<p>Aucun.</p>") == 1 + len(services.SORTIES_SANS_POCHETTE)


def test_resume_et_carte_tournoi(quatre_sorties):
    html = quatre_sorties.get("/sortis").text
    assert "4 jeux dehors en ce moment, dont 1 en tournoi." in html
    tournoi = html.split("<h2>En tournoi")[1]
    assert 'sortie-pochette--tournoi">tournoi</span>' in tournoi
    assert "pochette <strong>" not in tournoi


def test_liste_semantique_et_id_uniques(quatre_sorties):
    html = quatre_sorties.get("/sortis").text
    assert html.count('<ul class="sorties">') == 2
    assert "<table" not in html.split("<main")[1]
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), ids


# ---------------------------------------------------------------------------
# Recherche (portée de tests/test_stats_sortis_recherche.py, version 1.34)
# ---------------------------------------------------------------------------
def test_sans_recherche_tout_est_la(quatre_sorties):
    html = quatre_sorties.get("/sortis").text
    for nom in ("Catan", "Dixit", "Éclipse"):
        assert nom in html
    assert "trouvée" not in html


def test_recherche_par_nom_sans_accent_ni_majuscule(quatre_sorties):
    html = quatre_sorties.get("/sortis", params={"q": "ECLI"}).text
    assert "Éclipse" in html
    assert "Dixit" not in html and "Catan" not in html
    assert "1 boîte trouvée pour « ECLI »," in html
    assert "sur 4 sorties." in html
    assert 'role="status"' in html


def test_recherche_par_code_de_boite(quatre_sorties):
    html = quatre_sorties.get("/sortis", params={"q": "42"}).text
    assert "Éclipse" in html and "Dixit" not in html


def test_recherche_couvre_aussi_les_tournois(quatre_sorties):
    html = quatre_sorties.get("/sortis", params={"q": "catan"}).text
    assert "Boîte 001 " in html and "Boîte 003 " in html
    assert "2 boîtes trouvées" in html


def test_recherche_sans_resultat(quatre_sorties):
    html = quatre_sorties.get("/sortis", params={"q": "zzz"}).text
    assert "0 boîte trouvée" in html
    assert '<a class="lien" href="/sortis">Tout afficher</a>' in html


@pytest.mark.parametrize("q", ["", "   "])
def test_recherche_vide_affiche_tout(quatre_sorties, q):
    html = quatre_sorties.get("/sortis", params={"q": q}).text
    assert "Dixit" in html and "trouvée" not in html


def test_recherche_forgee_ou_tres_longue(quatre_sorties):
    longue = "<script>" + "x" * 5000
    r = quatre_sorties.get("/sortis", params={"q": longue})
    assert r.status_code == 200
    assert "<script>xxx" not in r.text                # échappée
    assert "x" * 200 not in r.text                    # tronquée
    assert "0 boîte trouvée" in r.text


# ---------------------------------------------------------------------------
# Ce que perd /stats
# ---------------------------------------------------------------------------
def test_stats_n_a_plus_la_section(quatre_sorties):
    html = quatre_sorties.get("/stats").text
    assert 'id="sortis"' not in html
    assert "Jeux actuellement sortis" not in html
    assert "Prêtés au public" not in html
    assert 'name="q"' not in html


def test_stats_lien_vers_la_page_pour_un_benevole_seulement(quatre_sorties):
    # Statistiques publiques : sinon le visiteur serait refusé (bilan-2) et
    # l'absence du lien ne prouverait rien.
    from app import modules
    conn = _conn()
    modules.ecrire_etat_module(conn, "stats", "tous")
    conn.close()
    assert 'href="/sortis"' in quatre_sorties.get("/stats").text
    quatre_sorties.cookies.delete("jeton_pret")
    r = quatre_sorties.get("/stats")
    assert r.status_code == 200
    assert 'href="/sortis"' not in r.text


# Une recherche enregistrée en 1.34 l'a été par un bénévole : depuis bilan-2,
# /stats est réservée aux bénévoles par défaut, et c'est eux qu'on redirige.
def test_stats_q_redirige_vers_la_nouvelle_page(client):
    r = _benevole(client).get("/stats", params={"q": "Éclipse", "tri": "exemplaire"},
                   follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/sortis?q=%C3%89clipse"


def test_stats_q_vide_redirige_aussi(client):
    r = _benevole(client).get("/stats?q=", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/sortis?q="


def test_stats_sans_q_ne_redirige_pas(client):
    r = _benevole(client).get("/stats", params={"tri": "exemplaire"}, follow_redirects=False)
    assert r.status_code == 200


def test_stats_q_visiteur_refuse_sans_detour(client):
    """
    Un visiteur n'est pas redirigé : la garde du module passe avant. Il
    aurait de toute façon été refusé sur /sortis ; il a la même page.
    """
    r = client.get("/stats", params={"q": "Éclipse"}, follow_redirects=False)
    assert r.status_code == 403
    assert "Accès réservé aux bénévoles" in r.text
