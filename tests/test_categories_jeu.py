"""
LES CATÉGORIES DE JEU EN LISTE (série « categories », lot 1).

Ce que ce fichier vérifie, et pourquoi :

1. **La clé et le découpage** (`app/categories_jeu.py`), cas limite par cas
   limite : c'est d'eux que dépend tout rapprochement.
2. **La migration** d'une base d'avant : rattachement, copie canonique,
   graphie la plus fréquente et départage, `modifie_le` intact, second passage
   sans effet, archive d'avant restaurée.
3. **Un seul chemin d'écriture** : import, « Créer un jeu », éditeur de fiche.
   Création d'office, rapprochement, orphelines purgées, simulation identique
   au réel, ligne jamais refusée, faux écart de casse jamais signalé.
4. **Le verrou de la copie** : pour tout titre, `titres.categorie` est la
   jointure de sa liaison, après chacun des chemins d'écriture.
5. **L'aller-retour export → import** sous les deux séparateurs : rien de créé,
   rien de changé, aucune correction défaite.
6. **Le compte rendu d'import** : borné, accordé, échappé.
"""

import csv
import io
import sqlite3
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from app import categories_jeu, services
from app.categories_jeu import (LONGUEUR_CATEGORIE, Vocabulaire, canoniser, cle,
                                decouper, graphie_majoritaire, joindre)

MOT_DE_PASSE = "secret-admin-categories"


# ===========================================================================
# Fixtures et outils
# ===========================================================================
@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Trois bases temporaires, vides, initialisées."""
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pret-jeux.db"))
    monkeypatch.delenv("FORMATION_CATALOGUE_CSV", raising=False)
    monkeypatch.setenv("FORMATION_SOURCE_DB", str(tmp_path / "inexistant.db"))
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "pret-jeux.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    db.init_db()
    tdb.init_db()
    pdb.init_db()
    return tmp_path


@pytest.fixture
def client(bases):
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    c.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    return c


def _conn():
    from app.db import get_connection
    return get_connection()


def _verrou():
    """
    LE VERROU DE LA COPIE : pour chaque titre, `titres.categorie` est la
    jointure des graphies de sa liaison, dans l'ordre de `rang`. Rend la
    table entière (référence -> copie) pour les assertions qui suivent.
    """
    conn = _conn()
    try:
        copies = {r["reference_titre"]: r["categorie"]
                  for r in conn.execute("SELECT reference_titre, categorie FROM titres")}
        liaison: dict[str, list[str]] = {ref: [] for ref in copies}
        for r in conn.execute(
                "SELECT tc.reference_titre, c.nom FROM titres_categories tc "
                "JOIN categories_jeu c USING (id_categorie_jeu) "
                "ORDER BY tc.reference_titre, tc.rang"):
            liaison[r["reference_titre"]].append(r["nom"])
        orphelines = conn.execute(
            "SELECT nom FROM categories_jeu WHERE id_categorie_jeu NOT IN "
            "(SELECT id_categorie_jeu FROM titres_categories)").fetchall()
    finally:
        conn.close()
    for ref, copie in copies.items():
        assert copie == joindre(liaison[ref]), (ref, copie, liaison[ref])
    assert not orphelines, [r[0] for r in orphelines]
    return copies


def _vocabulaire():
    conn = _conn()
    try:
        return sorted(r[0] for r in conn.execute("SELECT nom FROM categories_jeu"))
    finally:
        conn.close()


def _titre(ref):
    conn = _conn()
    try:
        return services.get_titre(conn, ref)
    finally:
        conn.close()


def _executer(sql, params=()):
    conn = _conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


ENTETE = "Code jeu;Nom jeu;Type jeu"


def _csv(tmp_path, lignes, entete=ENTETE, nom="cat.csv"):
    chemin = tmp_path / nom
    chemin.write_text(entete + "\n" + "\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


def _importer(chemin, **options):
    from scripts import import_csv
    return import_csv.importer(chemin, **options)


def _exporter(chemin: Path, separateur=";") -> Path:
    """L'export du catalogue ; avec « , », le même tableau réécrit à la virgule."""
    from app import exports
    conn = _conn()
    try:
        entetes, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    if separateur == ";":
        chemin.write_bytes(exports.catalogue_csv(entetes, lignes))
    else:
        buf = io.StringIO()
        ecrivain = csv.DictWriter(buf, fieldnames=entetes, delimiter=separateur)
        ecrivain.writeheader()
        ecrivain.writerows(lignes)
        chemin.write_text(buf.getvalue(), encoding="utf-8")
    return chemin


# ===========================================================================
# 1. LA CLÉ ET LE DÉCOUPAGE
# ===========================================================================
@pytest.mark.parametrize("graphie", ["Bluff", "bluff", " BLUFF ", "bLuFf"])
def test_cle_sans_casse_ni_bords(graphie):
    assert cle(graphie) == "bluff"


def test_cle_sans_accents_y_compris_decompose():
    nfd = unicodedata.normalize("NFD", "Stratégie")
    assert nfd != "Stratégie"
    assert {cle(g) for g in ("Stratégie", "strategie", "STRATÉGIE", nfd)} == {"strategie"}


def test_cle_espaces_internes_reduits():
    assert cle("Jeu  de \t plateau") == cle("jeu de plateau") == "jeu de plateau"


def test_cle_ne_replie_pas_la_ponctuation():
    assert cle("Question-Réponses") != cle("Question Réponses")


def test_cle_ne_decompose_pas_les_ligatures():
    """Noté au docstring, pas corrigé : aucun cas réel."""
    assert cle("Œuvre") != cle("Oeuvre")


@pytest.mark.parametrize("vide", ["", "   ", "\t", None])
def test_cle_vide_n_est_pas_une_categorie(vide):
    assert cle(vide) == ""
    assert decouper(vide) == []


@pytest.mark.parametrize("case, attendu", [
    ("Cartes, Ambiance", ["Cartes", "Ambiance"]),
    ("Ambiance, Cartes", ["Ambiance", "Cartes"]),
    ("Cartes,Coopération", ["Cartes", "Coopération"]),
    ("Cartes,, Ambiance,", ["Cartes", "Ambiance"]),
    (", Cartes", ["Cartes"]),
    ("Cartes, cartes, CARTES", ["Cartes"]),
    ("cartes, Ambiance, CARTES", ["cartes", "Ambiance"]),
    ("  Jeu   de plateau  ", ["Jeu de plateau"]),
    (",", []),
    (" , ,, ", []),
    ("", []),
    (None, []),
])
def test_decouper(case, attendu):
    assert decouper(case) == attendu


def test_decouper_stocke_en_nfc():
    nfd = unicodedata.normalize("NFD", "Stratégie, Coopération")
    assert decouper(nfd) == ["Stratégie", "Coopération"]
    assert all(unicodedata.is_normalized("NFC", n) for n in decouper(nfd))


def test_categorie_demesuree_tronquee_jamais_refusee():
    longue = "Très " * 40
    noms = decouper(f"Cartes, {longue}, Ambiance")
    assert noms[0] == "Cartes" and noms[2] == "Ambiance"
    assert len(noms[1]) <= LONGUEUR_CATEGORIE and noms[1].startswith("Très Très")
    # Déterministe : le ré-import de la copie retombe sur le même nom.
    assert decouper(joindre(noms)) == noms


def test_la_borne_laisse_passer_la_plus_longue_valeur_reelle():
    assert decouper("Question-Réponses") == ["Question-Réponses"]
    assert LONGUEUR_CATEGORIE >= 3 * len("Question-Réponses")


def test_joindre():
    assert joindre(["Cartes", "Ambiance"]) == "Cartes, Ambiance"
    assert joindre([]) is None


def test_graphie_majoritaire_la_plus_frequente():
    assert graphie_majoritaire(Counter({"bluff": 1, "Bluff": 16})) == "Bluff"
    assert graphie_majoritaire(Counter({"bluff": 3, "Bluff": 2})) == "bluff"


def test_graphie_majoritaire_egalite_initiale_majuscule_puis_alphabet():
    assert graphie_majoritaire(Counter({"imagination": 1, "Imagination": 1})) == "Imagination"
    assert graphie_majoritaire(Counter({"IMAGINATION": 1, "Imagination": 1})) == "IMAGINATION"
    # Ordre d'insertion indifférent.
    assert graphie_majoritaire(Counter({"Imagination": 1, "imagination": 1})) == "Imagination"


def test_vocabulaire_graphie_connue_gagne_nouvelle_a_la_premiere_occurrence():
    vocab = Vocabulaire({"bluff": "Bluff"})
    assert vocab.retenir("bluff, hasard") == ["Bluff", "hasard"]
    assert vocab.retenir("HASARD, Plis") == ["hasard", "Plis"]
    assert vocab.nouvelles == ["hasard", "Plis"]


def test_canoniser_ne_retient_rien():
    connues = {"cartes": "Cartes"}
    assert canoniser("cartes,Ambiance", connues) == "Cartes, Ambiance"
    assert connues == {"cartes": "Cartes"}
    assert canoniser(",", connues) is None


def test_le_module_est_pur():
    source = Path(categories_jeu.__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in source and "import app" not in source
    assert "from app" not in source


# ===========================================================================
# 2. LA MIGRATION
# ===========================================================================
def _base_d_avant(chemin: Path):
    """Une base d'avant le lot : `titres` complète, sans les deux tables."""
    from app import models
    c = sqlite3.connect(chemin)
    c.executescript(models.SCHEMA_TITRES)
    c.executemany(
        "INSERT INTO titres (reference_titre, nom, categorie, modifie_le) VALUES (?, ?, ?, ?)",
        [("A", "Alpha", "Bluff", None), ("B", "Beta", "Cartes, Bluff", None),
         ("C", "Gamma", "Stratégie, bluff", "2026-10-01T10:00:00"),
         ("D", "Delta", "Cartes, imagination", None), ("E", "Epsilon", "Dés, Imagination", None),
         ("F", "Phi", "Cartes,Coopération", None), ("G", "Gros", None, None),
         ("H", "Hache", ",", None), ("I", "Iota", "", None)])
    c.commit()
    c.close()


def test_migration_d_une_base_d_avant(tmp_path, monkeypatch):
    from app import db
    chemin = tmp_path / "avant.db"
    _base_d_avant(chemin)
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    db.init_db()

    copies = _verrou()
    assert copies == {
        "A": "Bluff", "B": "Cartes, Bluff", "C": "Stratégie, Bluff",
        "D": "Cartes, Imagination", "E": "Dés, Imagination",
        "F": "Cartes, Coopération", "G": None, "H": None, "I": None,
    }
    assert _vocabulaire() == ["Bluff", "Cartes", "Coopération", "Dés",
                              "Imagination", "Stratégie"]
    # La migration ne pose jamais `modifie_le`, et ne retire pas celui qui est là.
    assert _titre("C")["modifie_le"] == "2026-10-01T10:00:00"
    assert _titre("A")["modifie_le"] is None


def test_migration_second_passage_sans_effet(tmp_path, monkeypatch):
    from app import db
    chemin = tmp_path / "avant.db"
    _base_d_avant(chemin)
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    db.init_db()
    conn = _conn()
    try:
        avant = (conn.execute("SELECT * FROM titres_categories ORDER BY 1, 2").fetchall(),
                 conn.execute("SELECT * FROM categories_jeu ORDER BY 1").fetchall(),
                 conn.total_changes)
        assert db._migrer_categories_jeu(conn) == 0
        db.init_db(conn)
        apres = (conn.execute("SELECT * FROM titres_categories ORDER BY 1, 2").fetchall(),
                 conn.execute("SELECT * FROM categories_jeu ORDER BY 1").fetchall())
    finally:
        conn.close()
    assert [list(map(tuple, t)) for t in apres] == [list(map(tuple, t)) for t in avant[:2]]


def test_migration_base_neuve_ou_vide_ne_fait_rien(bases):
    from app import db
    conn = _conn()
    try:
        assert db._migrer_categories_jeu(conn) == 0
        assert conn.execute("SELECT COUNT(*) FROM categories_jeu").fetchone()[0] == 0
    finally:
        conn.close()


def test_migration_rattrape_une_ligne_inseree_en_sql_brut(bases):
    from app import db
    _executer("INSERT INTO titres (reference_titre, nom, categorie) VALUES ('X', 'X', 'ambiance')")
    db.init_db()
    assert _verrou() == {"X": "ambiance"}


def test_migration_categorie_deja_connue_garde_sa_graphie(bases):
    from app import db
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dobble", categorie="Ambiance")
        conn.execute("INSERT INTO titres (reference_titre, nom, categorie) "
                     "VALUES ('X', 'X', 'AMBIANCE, ambiance, hasard')")
        conn.commit()
        db._migrer_categories_jeu(conn)
    finally:
        conn.close()
    copies = _verrou()
    assert copies["X"] == "Ambiance, hasard"


def test_restaurer_une_archive_d_avant_le_lot(bases, tmp_path):
    """L'archive d'avant n'a pas les tables : la restauration les remplit."""
    from app import sauvegarde
    _executer("INSERT INTO titres (reference_titre, nom, categorie) VALUES "
              "('A', 'Alpha', 'Cartes,bluff'), ('B', 'Beta', 'Bluff')")
    archive = sauvegarde.creer_zip_sauvegarde()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        contenus = {nom: zf.read(nom) for nom in zf.namelist()}
    pret = tmp_path / "pret-avant.db"
    pret.write_bytes(contenus["pret-jeux.db"])
    c = sqlite3.connect(pret)
    c.execute("DROP TABLE titres_categories")
    c.execute("DROP TABLE categories_jeu")
    c.commit()
    c.close()
    contenus["pret-jeux.db"] = pret.read_bytes()
    chemin_zip = tmp_path / "avant.zip"
    with zipfile.ZipFile(chemin_zip, "w") as zf:
        for nom, contenu in contenus.items():
            zf.writestr(nom, contenu)

    sauvegarde.restaurer_zip_sauvegarde(chemin_zip)

    assert _verrou() == {"A": "Cartes, Bluff", "B": "Bluff"}
    assert _vocabulaire() == ["Bluff", "Cartes"]


# ===========================================================================
# 3. LES CHEMINS D'ÉCRITURE
# ===========================================================================
def test_creer_jeu_ecrit_la_liaison_et_la_copie(bases):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes,imagination")
        services.creer_jeu(conn, "Dobble", categorie="cartes, Ambiance")
        services.creer_jeu(conn, "Sans", categorie=None)
    finally:
        conn.close()
    assert _verrou() == {"DIXIT": "Cartes, imagination", "DOBBLE": "Cartes, Ambiance",
                         "SANS": None}


def test_creer_jeu_de_meme_nom_remplace_la_categorie_et_purge(bases):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Imagination")
        services.creer_jeu(conn, "Dixit", categorie="Cartes")
    finally:
        conn.close()
    assert _verrou() == {"DIXIT": "Cartes"}
    assert _vocabulaire() == ["Cartes"]


def test_la_route_creer_un_jeu(client):
    r = client.post("/admin/jeu-nouveau", data={"nom": "Skull", "categorie": "bluff,  Ambiance"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _verrou() == {"SKULL": "bluff, Ambiance"}


def _fiche(conn, ref, **changements):
    titre = services.get_titre(conn, ref)
    saisie = {c: "" if titre[c] is None else str(titre[c]) for c in services.CHAMPS_FICHE}
    saisie.update(changements)
    valeurs, erreurs = services.lire_saisie_fiche(saisie, titre["type_jeu"])
    assert not erreurs
    return services.modifier_titre(conn, ref, valeurs)


def test_editeur_meme_liste_autrement_ecrite_n_ecrit_rien(bases):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes, Ambiance")
        avant = conn.total_changes
        resultat = _fiche(conn, "DIXIT", categorie="  cartes,AMBIANCE , ")
        assert resultat["changes"] == []
        assert conn.total_changes == avant
    finally:
        conn.close()
    assert _titre("DIXIT")["modifie_le"] is None
    assert _verrou() == {"DIXIT": "Cartes, Ambiance"}


def test_editeur_change_la_liste(bases):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes, Ambiance")
        services.creer_jeu(conn, "Skull", categorie="Bluff")
        resultat = _fiche(conn, "DIXIT", categorie="ambiance, bluff, Imagination")
    finally:
        conn.close()
    assert resultat["changes"] == ["categorie"]
    assert resultat["apres"]["categorie"] == "Ambiance, Bluff, Imagination"
    assert _titre("DIXIT")["modifie_le"] is not None
    assert _verrou() == {"DIXIT": "Ambiance, Bluff, Imagination", "SKULL": "Bluff"}
    # « Cartes » n'est plus portée par aucun jeu : elle disparaît.
    assert _vocabulaire() == ["Ambiance", "Bluff", "Imagination"]


def test_editeur_vider_la_case_retire_les_categories(bases):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes, Ambiance")
        _fiche(conn, "DIXIT", categorie="")
    finally:
        conn.close()
    assert _verrou() == {"DIXIT": None}
    assert _vocabulaire() == []


def test_editeur_par_la_route(client):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes")
    finally:
        conn.close()
    titre = _titre("DIXIT")
    donnees = {c: "" if titre[c] is None else str(titre[c]) for c in services.CHAMPS_FICHE}
    r = client.post("/admin/jeu/DIXIT/modifier", data={**donnees, "categorie": "CARTES"},
                    follow_redirects=False)
    assert "inchange" in r.headers["location"]
    r = client.post("/admin/jeu/DIXIT/modifier",
                    data={**donnees, "categorie": "cartes, Ambiance"}, follow_redirects=False)
    assert r.status_code == 303 and "inchange" not in r.headers["location"]
    assert _verrou() == {"DIXIT": "Cartes, Ambiance"}


def test_import_cree_d_office_et_rapproche(bases, tmp_path):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Skull", categorie="Bluff")
    finally:
        conn.close()
    res = _importer(_csv(tmp_path, [
        "001;Citadelles;Stratégie, bluff",
        "002;Loup-garou;BLUFF,hasard",
        "003;Yahtzee;Hasard, Dés",
        "004;Catan;",
    ]))
    assert res["categories_nouvelles"] == ["Stratégie", "hasard", "Dés"]
    copies = _verrou()
    assert copies["CITADELLES"] == "Stratégie, Bluff"
    assert copies["LOUP_GAROU"] == "Bluff, hasard"
    assert copies["YAHTZEE"] == "hasard, Dés"
    assert copies["CATAN"] is None
    # « BLUFF » ne renomme pas « Bluff ».
    assert "Bluff" in _vocabulaire() and "BLUFF" not in _vocabulaire()


def test_import_simulation_identique_au_reel(bases, tmp_path):
    conn = _conn()
    try:
        services.creer_jeu(conn, "Skull", categorie="Bluff")
        # Un titre pas encore rattaché (inséré en SQL) : la migration de
        # l'import réel le rattachera, la simulation doit le savoir.
        conn.execute("INSERT INTO titres (reference_titre, nom, categorie) "
                     "VALUES ('X', 'X', 'plis')")
        conn.commit()
    finally:
        conn.close()
    lignes = ["001;A;Hasard, Plis, bluff", "002;B;Dessin", "003;A;Autre chose",
              "004;C;hasard, Mémoire", "005;D;, ,"]
    simulation = _importer(_csv(tmp_path, lignes), dry_run=True)
    assert _vocabulaire() == ["Bluff"]
    reel = _importer(_csv(tmp_path, lignes))
    assert simulation["categories_nouvelles"] == reel["categories_nouvelles"] \
        == ["Hasard", "Dessin", "Mémoire"]
    copies = _verrou()
    assert copies["A"] == "Hasard, plis, Bluff"


def test_import_nouvelle_graphie_de_la_premiere_occurrence_retenue(bases, tmp_path):
    """Un titre dont la première ligne est vide prend la case d'une ligne plus loin."""
    res = _importer(_csv(tmp_path, ["001;A;", "002;B;Hasard", "003;A;HASARD, plis"]))
    assert res["categories_nouvelles"] == ["Hasard", "plis"]
    assert _verrou() == {"A": "Hasard, plis", "B": "Hasard"}


def test_import_purge_les_orphelines_une_fois_a_la_fin(bases, tmp_path):
    _importer(_csv(tmp_path, ["001;A;Hasard", "002;B;Plis"]))
    # A perd « Hasard », que B reçoit sous une autre graphie : pas de
    # disparition-renaissance, pas de « nouvelle catégorie ».
    res = _importer(_csv(tmp_path, ["001;A;Plis", "002;B;HASARD"]))
    assert res["categories_nouvelles"] == []
    assert _verrou() == {"A": "Plis", "B": "Hasard"}
    _importer(_csv(tmp_path, ["001;A;Plis", "002;B;Plis"]))
    assert _vocabulaire() == ["Plis"]


def test_import_case_vide_ecrase_les_categories(bases, tmp_path):
    _importer(_csv(tmp_path, ["001;A;Hasard"]))
    _importer(_csv(tmp_path, ["001;A;"]))
    assert _verrou() == {"A": None}
    assert _vocabulaire() == []


def test_import_colonne_absente_ecrase_aussi(bases, tmp_path):
    """Règle d'avant, inchangée : l'import écrase la catégorie."""
    _importer(_csv(tmp_path, ["001;A;Hasard"]))
    _importer(_csv(tmp_path, ["001;A"], entete="Code jeu;Nom jeu"))
    assert _verrou() == {"A": None}


def test_import_aucune_case_ne_fait_refuser_une_ligne(bases, tmp_path):
    folle = ", ".join(["x" * 500, "<b>x</b>", ",,,", "é" * 3, "Œuvre"])
    res = _importer(_csv(tmp_path, [f"001;A;{folle}", "002;B;,,,", "003;C;   "]))
    assert res["exemplaires"] == 3 and not res["ignores"]
    copies = _verrou()
    assert copies["B"] is None and copies["C"] is None
    assert len(res["categories_nouvelles"]) == 4
    assert all(len(n) <= LONGUEUR_CATEGORIE for n in res["categories_nouvelles"])


def test_import_faux_ecart_de_casse_jamais_signale(bases, tmp_path):
    """Une fiche corrigée « Bluff » face à un tableur « bluff » : rien à dire."""
    _importer(_csv(tmp_path, ["001;Citadelles;Stratégie, Bluff"]))
    conn = _conn()
    try:
        _fiche(conn, "CITADELLES", nom="Citadelles !", categorie="Stratégie, Bluff")
    finally:
        conn.close()
    res = _importer(_csv(tmp_path, ["001;Citadelles !;strategie,  bluff"]))
    assert res["corrections_defaites"] == []
    assert _titre("CITADELLES")["modifie_le"] is not None
    # Un vrai écart, lui, est dit.
    res = _importer(_csv(tmp_path, ["001;Citadelles !;Stratégie"]))
    ecarts = res["corrections_defaites"][0]["ecarts"]
    assert ("Catégorie", "Stratégie, Bluff", "Stratégie") in ecarts


def test_import_ligne_de_commande_affiche_la_liste_entiere(bases, tmp_path, capsys):
    from scripts import import_csv
    noms = [f"Catégorie {i}" for i in range(15)]
    res = _importer(_csv(tmp_path, [f"001;A;{', '.join(noms)}"]))
    import_csv.afficher_rapport(res, montrer_groupes=False)
    sortie = capsys.readouterr().out
    assert f"Nouvelles catégories : 15 -> {', '.join(noms)}" in sortie


# ===========================================================================
# 4. LE VERROU DE LA COPIE — après chacun des chemins, sur un même parc
# ===========================================================================
def test_verrou_apres_chaque_chemin(bases, tmp_path, monkeypatch):
    from app import formation
    _importer(_csv(tmp_path, ["001;A;Cartes,bluff", "002;B;Bluff, Stratégie",
                              "003;C;", "004;D;hasard"]))
    _verrou()
    conn = _conn()
    try:
        services.creer_jeu(conn, "E", categorie="BLUFF, Plis")
        _verrou()
        _fiche(conn, "A", categorie="plis")
        _verrou()
        _fiche(conn, "D", categorie="")
        _verrou()
        services.ecrire_categories_titre(conn, "B", "Ambiance")
        _verrou()
    finally:
        conn.close()
    _importer(_exporter(tmp_path / "export.csv"))
    _verrou()
    conn = _conn()
    try:
        formation.peupler_pret(conn)
    finally:
        conn.close()
    assert set(_verrou().values()) == {"Formation"}


# ===========================================================================
# 5. L'ALLER-RETOUR EXPORT → IMPORT
# ===========================================================================
def _etat():
    conn = _conn()
    try:
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM titres ORDER BY 1")],
            [tuple(r) for r in conn.execute("SELECT * FROM categories_jeu ORDER BY 1")],
            [tuple(r) for r in conn.execute("SELECT * FROM titres_categories ORDER BY 1, 2")],
        )
    finally:
        conn.close()


@pytest.mark.parametrize("separateur", [";", ","])
def test_aller_retour_export_import(bases, tmp_path, separateur):
    _importer(_csv(tmp_path, ["001;Dixit;Cartes,imagination", "002;Dixit;",
                              "003;Citadelles;Stratégie, Bluff", "004;Catan;"]))
    conn = _conn()
    try:
        _fiche(conn, "CITADELLES", categorie="bluff, Stratégie")   # modifie_le posé
    finally:
        conn.close()
    avant = _etat()
    fichier = _exporter(tmp_path / f"export{separateur}.csv", separateur)
    texte = fichier.read_text(encoding="utf-8-sig")
    if separateur == ",":
        # La virgule d'une case force les guillemets ; l'import les relit.
        assert '"Cartes, imagination"' in texte
    res = _importer(fichier)
    assert res["categories_nouvelles"] == []
    assert res["corrections_defaites"] == []
    assert _etat() == avant


def test_aller_retour_du_catalogue_d_exemple(bases, tmp_path):
    exemple = Path(__file__).resolve().parent.parent / "exemples" / "catalogue-exemple.csv"
    premier = _importer(exemple)
    assert premier["categories_nouvelles"]
    avant = _etat()
    res = _importer(_exporter(tmp_path / "export.csv"))
    assert res["categories_nouvelles"] == [] and res["corrections_defaites"] == []
    assert _etat() == avant


# ===========================================================================
# Formation : la remise à zéro ne bute sur aucune clé étrangère
# ===========================================================================
def test_formation_videe_sans_erreur_de_cle_etrangere(bases):
    from app import formation
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes, Imagination")
        formation._vider_base_pret(conn)
        assert conn.execute("SELECT COUNT(*) FROM titres_categories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM categories_jeu").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_une_categorie_portee_ne_se_supprime_pas(bases):
    """Pas de cascade vers `categories_jeu` : la copie ne peut pas mentir."""
    conn = _conn()
    try:
        services.creer_jeu(conn, "Dixit", categorie="Cartes")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM categories_jeu")
    finally:
        conn.close()


# ===========================================================================
# 6. LE COMPTE RENDU D'IMPORT
# ===========================================================================
def _envoyer(client, lignes):
    contenu = (ENTETE + "\n" + "\n".join(lignes) + "\n").encode()
    return client.post("/admin/donnees/import",
                       files={"fichier": ("c.csv", contenu, "text/csv")}).text


def test_compte_rendu_web_une_et_plusieurs(client):
    page = _envoyer(client, ["001;A;Hasard"])
    assert "1 nouvelle catégorie créée : Hasard." in page
    page = _envoyer(client, ["001;A;hasard, Plis, Dessin"])
    assert "2 nouvelles catégories créées : Plis, Dessin." in page
    page = _envoyer(client, ["001;A;Plis, dessin"])
    assert "nouvelle" not in page.split("Import réussi", 1)[1].split("</p>", 1)[0]


def test_compte_rendu_web_borne(client):
    from app.routes import admin
    n = admin.CATEGORIES_AFFICHEES + 3
    noms = [f"Cat{i:02d}" for i in range(n)]
    page = _envoyer(client, [f"001;A;{', '.join(noms)}"])
    attendu = (f"{n} nouvelles catégories créées : "
               f"{', '.join(noms[:admin.CATEGORIES_AFFICHEES])} et 3 autres.")
    assert attendu in page
    assert noms[admin.CATEGORIES_AFFICHEES] not in page


def test_compte_rendu_web_un_seul_de_plus(client):
    from app.routes import admin
    noms = [f"Cat{i:02d}" for i in range(admin.CATEGORIES_AFFICHEES + 1)]
    page = _envoyer(client, [f"001;A;{', '.join(noms)}"])
    assert " et 1 autre." in page


def test_compte_rendu_web_echappe(client):
    page = _envoyer(client, ["001;A;<b>x</b>"])
    assert "<b>x</b>" not in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page
