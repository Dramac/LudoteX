"""
Journal d'activité — socle d'écriture (lot B, étape 3 de
docs/conception-journal.md).

Aucun point d'appel métier n'existe encore (lot C) : ces tests appellent
`journal.journaliser()` directement, avec un « faux » objet requête ne
portant que `.cookies` — c'est tout ce que `journaliser()` interroge (via
`admin_auth.admin_connecte`, `auth.acces_valide`, `services.appareil_de`),
donc inutile de construire une vraie `Request` Starlette.

La fixture autouse `_journal_isole` (tests/conftest.py) redirige déjà
JOURNAL_PATH vers un fichier temporaire propre à chaque test.
"""

import json
import sys
from datetime import datetime

import pytest


class _FauxRequest:
    """Objet minimal portant `.cookies`, suffisant pour journaliser()."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


# ---------------------------------------------------------------------------
# Fixtures — bases temporaires (patron de tests/test_appareils.py : les TROIS
# bases sont redirigées même si la plupart de ces tests ne touchent que celle
# de prêt, pour éviter tout écrit accidentel dans data/ du dépôt réel si
# app.main est importé pour la première fois depuis ce fichier).
# ---------------------------------------------------------------------------
@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
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
    conn.close()
    return tmp_path


@pytest.fixture
def conn(bases):
    from app import db

    c = db.get_connection()
    yield c
    c.close()


@pytest.fixture
def client(bases, monkeypatch):
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-journal")

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _poser_jeton(conn, jeton="jeton-de-test"):
    for cle, valeur in (("pret_token", jeton), ("pret_token_expire", None)):
        conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (cle, valeur),
        )
    conn.commit()
    return jeton


def _lire_journal(chemin):
    if not chemin.exists():
        return []
    return [l for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Ligne JSON valide et complète
# ---------------------------------------------------------------------------
def test_ligne_json_ecrite_dans_le_fichier(bases, conn, monkeypatch, tmp_path):
    from app import journal

    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)

    requete = _FauxRequest({"jeton_pret": "jeton-de-test", "appareil": "3F1A9C"})
    journal.journaliser(
        requete, "pret", "retour", objet="7 Wonders Duel", ref="7-wonders-duel", ok=True,
    )

    lignes = _lire_journal(chemin)
    assert len(lignes) == 1
    d = json.loads(lignes[0])
    assert d["qui"] == "benevole"
    assert d["appareil"] == "3F1A9C"
    assert d["module"] == "pret"
    assert d["action"] == "retour"
    assert d["objet"] == "7 Wonders Duel"
    assert d["ref"] == "7-wonders-duel"
    assert d["ok"] is True
    assert "detail" not in d
    # Horodatage ISO avec décalage local explicite (§3.1).
    assert d["t"][10] == "T"
    assert d["t"][-6] in ("+", "-")


def test_un_objet_par_ligne(bases, conn, tmp_path):
    from app import journal

    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})
    journal.journaliser(requete, "pret", "pret", objet="Catan")
    journal.journaliser(requete, "pret", "retour", objet="Catan")

    lignes = _lire_journal(chemin)
    assert len(lignes) == 2
    for l in lignes:
        json.loads(l)  # chaque ligne est un JSON complet et autonome


# ---------------------------------------------------------------------------
# journaliser() ne lève jamais, même fichier non inscriptible
# ---------------------------------------------------------------------------
def test_journaliser_n_explose_pas_si_fichier_non_inscriptible(bases, conn, tmp_path):
    from app import journal

    _poser_jeton(conn)
    # Un fichier existe déjà LÀ où un dossier est attendu : mkdir(parents=True)
    # lève NotADirectoryError/FileExistsError, avalée par configurer().
    obstacle = tmp_path / "pas_un_dossier"
    obstacle.write_text("x")
    chemin_impossible = obstacle / "journal.log"

    journal.configurer(chemin_impossible, console=False)  # ne doit pas lever

    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})
    journal.journaliser(requete, "pret", "pret", objet="Catan")  # ne doit pas lever non plus


def test_journaliser_n_explose_pas_si_module_ou_action_inconnus(bases, conn, tmp_path):
    from app import journal

    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})

    journal.journaliser(requete, "pret", "action_inexistante")
    journal.journaliser(requete, "module_inexistant", "pret")

    assert _lire_journal(chemin) == []  # rien n'a été écrit, mais rien n'a levé


# ---------------------------------------------------------------------------
# Les trois publics + le mode ouvert — admin testé AVANT bénévole (§5.3)
# ---------------------------------------------------------------------------
def test_qui_admin_prime_sur_benevole(bases, conn, tmp_path, monkeypatch):
    from app import admin_auth, journal

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)

    sid = admin_auth.ouvrir_session("B72E04")
    # Cookie de jeton bénévole ÉGALEMENT présent : un admin connecté a aussi
    # acces_valide() vrai, c'est justement ce que l'ordre doit départager.
    requete = _FauxRequest({
        admin_auth.COOKIE_ADMIN: sid,
        "jeton_pret": "jeton-de-test",
        "appareil": "B72E04",
    })
    journal.journaliser(requete, "admin", "connexion_reussie")

    d = json.loads(_lire_journal(chemin)[0])
    assert d["qui"] == "admin"


def test_qui_benevole_sans_session_admin(bases, conn, tmp_path, monkeypatch):
    from app import admin_auth, journal

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)

    requete = _FauxRequest({"jeton_pret": "jeton-de-test", "appareil": "3F1A9C"})
    journal.journaliser(requete, "pret", "pret", objet="Catan")

    d = json.loads(_lire_journal(chemin)[0])
    assert d["qui"] == "benevole"


def test_qui_visiteur_sans_cookie(bases, conn, tmp_path, monkeypatch):
    from app import admin_auth, journal

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)

    requete = _FauxRequest({})
    journal.journaliser(requete, "tournois", "participant_ajoute", objet="Catan")

    d = json.loads(_lire_journal(chemin)[0])
    assert d["qui"] == "visiteur"
    assert "appareil" not in d


def test_mode_ouvert_donne_indetermine(bases, conn, tmp_path, monkeypatch):
    """Aucun jeton configuré -> pas de mensonge « bénévole », même si
    acces_valide() renverrait vrai pour tout le monde dans ce cas."""
    from app import admin_auth, journal

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    # Pas de _poser_jeton() ici : mode ouvert.
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)

    requete = _FauxRequest({})
    journal.journaliser(requete, "pret", "pret", objet="Catan")

    d = json.loads(_lire_journal(chemin)[0])
    assert d["qui"] == "indetermine"


# ---------------------------------------------------------------------------
# Vocabulaire fermé des actions
# ---------------------------------------------------------------------------
def test_action_hors_vocabulaire_refusee(bases, conn, tmp_path):
    from app import journal

    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})

    journal.journaliser(requete, "pret", "rendu")  # jamais dans ACTIONS (piège cité §3.2)

    assert _lire_journal(chemin) == []


def test_toutes_les_actions_du_vocabulaire_sont_des_chaines_non_vides():
    from app import journal

    assert journal.ACTIONS
    for action in journal.ACTIONS:
        assert isinstance(action, str) and action


# ---------------------------------------------------------------------------
# Assainissement — retour à la ligne dans `objet`, troncature
# ---------------------------------------------------------------------------
def test_assainissement_retour_a_la_ligne_dans_objet(bases, conn, tmp_path):
    from app import journal

    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})

    journal.journaliser(
        requete, "live", "annonce_posee", objet="Tombola à 15h\nAu stand principal",
    )

    lignes = _lire_journal(chemin)
    assert len(lignes) == 1  # un seul objet JSON, pas cassé en deux lignes
    d = json.loads(lignes[0])
    assert "\n" not in d["objet"]
    assert d["objet"] == "Tombola à 15h Au stand principal"


def test_assainissement_troncature_objet_120_caracteres(bases, conn, tmp_path):
    from app import journal

    _poser_jeton(conn)
    chemin = tmp_path / "j.log"
    journal.configurer(chemin, console=False)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})

    long_texte = "x" * 500
    journal.journaliser(requete, "live", "annonce_posee", objet=long_texte)

    d = json.loads(_lire_journal(chemin)[0])
    assert len(d["objet"]) == 120


# ---------------------------------------------------------------------------
# lire_dernieres_lignes — utilisée par l'écran admin et scripts/journal.py
# ---------------------------------------------------------------------------
def test_lire_dernieres_lignes_fichier_absent(tmp_path):
    from app import journal

    assert journal.lire_dernieres_lignes(tmp_path / "absent.log") == []


def test_lire_dernieres_lignes_ordre_chronologique(tmp_path):
    from app import journal

    chemin = tmp_path / "j.log"
    chemin.write_text("\n".join(f'{{"n":{i}}}' for i in range(10)) + "\n", encoding="utf-8")

    lignes = journal.lire_dernieres_lignes(chemin, limite=5)
    valeurs = [json.loads(l)["n"] for l in lignes]
    assert valeurs == [5, 6, 7, 8, 9]  # les 5 dernières, dans l'ordre chronologique


def test_lire_dernieres_lignes_traverse_plusieurs_blocs(tmp_path):
    from app import journal

    chemin = tmp_path / "j.log"
    # Lignes assez longues pour forcer plusieurs itérations avec un petit bloc.
    contenu = "\n".join(f'{{"n":{i},"pad":"{"a" * 50}"}}' for i in range(300)) + "\n"
    chemin.write_text(contenu, encoding="utf-8")

    lignes = journal.lire_dernieres_lignes(chemin, limite=30, taille_bloc=200)
    valeurs = [json.loads(l)["n"] for l in lignes]
    assert valeurs == list(range(270, 300))


# ---------------------------------------------------------------------------
# Écran /admin/journal (étape 5 de docs/conception-journal.md)
# ---------------------------------------------------------------------------
def _connexion_admin(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-journal"})


def _ecrire_lignes(conn, cookies, n=1, module="pret", action="pret", objet="Catan"):
    """Écrit `n` lignes réelles via journaliser() (pipeline complet)."""
    from app import journal

    _poser_jeton(conn)
    requete = _FauxRequest(cookies)
    for i in range(n):
        journal.journaliser(requete, module, action, objet=f"{objet} {i}")


def test_admin_journal_garde_non_authentifie(client):
    r = client.get("/admin/journal", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"


def test_admin_journal_fichier_absent_message_clair(client):
    _connexion_admin(client)
    r = client.get("/admin/journal")
    assert r.status_code == 200
    assert "Aucune activité enregistrée pour l'instant." in r.text


def test_admin_journal_affiche_les_lignes_ecrites(client, conn, _journal_isole):
    _ecrire_lignes(conn, {"jeton_pret": "jeton-de-test"}, n=3, objet="7 Wonders Duel")
    _connexion_admin(client)
    r = client.get("/admin/journal")
    assert r.status_code == 200
    assert r.text.count("7 Wonders Duel") == 3
    assert "benevole" in r.text


def test_admin_journal_filtre_module_et_action(client, conn, _journal_isole):
    from app import journal

    _poser_jeton(conn)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})
    journal.journaliser(requete, "pret", "pret", objet="Catan")
    journal.journaliser(requete, "live", "annonce_posee", objet="Tombola")
    _connexion_admin(client)

    r = client.get("/admin/journal", params={"module": "live"})
    assert "Tombola" in r.text
    assert "Catan" not in r.text

    r2 = client.get("/admin/journal", params={"action": "pret"})
    assert "Catan" in r2.text
    assert "Tombola" not in r2.text


def test_admin_journal_recherche_texte_dans_objet(client, conn, _journal_isole):
    from app import journal

    _poser_jeton(conn)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})
    journal.journaliser(requete, "pret", "pret", objet="7 Wonders Duel")
    journal.journaliser(requete, "pret", "pret", objet="Catan")
    _connexion_admin(client)

    r = client.get("/admin/journal", params={"q": "wonders"})  # insensible à la casse
    assert "7 Wonders Duel" in r.text
    assert "Catan" not in r.text


def test_admin_journal_echec_marque_par_un_badge(client, conn, _journal_isole):
    from app import journal

    _poser_jeton(conn)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})
    journal.journaliser(requete, "pret", "pret", objet="Catan", ok=False, detail="occupe")
    _connexion_admin(client)

    r = client.get("/admin/journal")
    assert "badge-attention" in r.text
    assert "Échec" in r.text


def test_admin_journal_ligne_corrompue_ignoree_en_silence(client, conn, _journal_isole):
    """Une ligne illisible (rotation en cours d'écriture) n'est ni affichée
    comme erreur, ni ne fait planter la page — les lignes valides autour
    d'elle restent visibles."""
    from app import journal

    _poser_jeton(conn)
    requete = _FauxRequest({"jeton_pret": "jeton-de-test"})
    journal.journaliser(requete, "pret", "pret", objet="Catan")
    with open(_journal_isole, "a", encoding="utf-8") as fh:
        fh.write("ceci n'est pas du JSON\n")
    journal.journaliser(requete, "pret", "retour", objet="Dobble")

    _connexion_admin(client)
    r = client.get("/admin/journal")
    assert r.status_code == 200
    assert "Catan" in r.text
    assert "Dobble" in r.text


def test_admin_journal_200_dernieres_lignes_seulement(client, conn, _journal_isole):
    _ecrire_lignes(conn, {"jeton_pret": "jeton-de-test"}, n=250, objet="Jeu")
    _connexion_admin(client)
    r = client.get("/admin/journal")
    assert r.status_code == 200
    assert "sur 200 au maximum" in r.text
    # Les 50 lignes les plus anciennes (250 écrites, fenêtre de 200) ne
    # doivent plus apparaître ; "Jeu 0" n'est jamais un sous-texte d'un autre
    # objet de ce jeu de données ("Jeu 1", "Jeu 10"… ne le contiennent pas).
    assert ">Jeu 0<" not in r.text
    assert ">Jeu 249<" in r.text


def test_admin_journal_telecharger(client, conn, _journal_isole):
    _ecrire_lignes(conn, {"jeton_pret": "jeton-de-test"}, n=1, objet="Catan")
    _connexion_admin(client)

    r = client.get("/admin/journal/telecharger")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Catan" in r.text


def test_admin_journal_telecharger_fichier_absent_ne_leve_pas(client):
    _connexion_admin(client)
    r = client.get("/admin/journal/telecharger", follow_redirects=False)
    assert r.status_code == 303  # redirection vers l'écran, jamais une erreur brute


def test_admin_journal_lien_depuis_tableau_de_bord(client):
    _connexion_admin(client)
    r = client.get("/admin")
    assert 'href="/admin/journal"' in r.text


def test_admin_journal_aide_inline_et_section_admin_aide(client):
    _connexion_admin(client)
    r = client.get("/admin/journal")
    assert "aide-inline" in r.text
    assert "/admin/aide#probleme-journal" in r.text

    aide = client.get("/admin/aide")
    assert 'id="probleme-journal"' in aide.text


# ---------------------------------------------------------------------------
# scripts/journal.py (étape 6) — parsing, filtres, --brut. Le suivi -f n'est
# pas testé ici (process persistant, non simple sous pytest — voir la
# docstring du script pour la vérification manuelle).
# ---------------------------------------------------------------------------
def _ecrire_fichier_exemple(chemin, lignes):
    chemin.write_text(
        "\n".join(json.dumps(l, ensure_ascii=False) for l in lignes) + "\n",
        encoding="utf-8",
    )


def test_script_charger_lignes_ignore_les_lignes_corrompues(tmp_path):
    from scripts import journal as script

    chemin = tmp_path / "j.log"
    chemin.write_text(
        '{"t":"2026-08-04T10:00:00+02:00","qui":"admin","module":"pret","action":"pret","ok":true}\n'
        "pas du json\n"
        '{"t":"2026-08-04T10:01:00+02:00","qui":"benevole","module":"pret","action":"retour","ok":true}\n',
        encoding="utf-8",
    )

    lignes = script.charger_lignes(chemin)
    assert len(lignes) == 2
    assert lignes[0]["action"] == "pret" and lignes[1]["action"] == "retour"


def test_script_filtrer_module_et_qui(tmp_path):
    from scripts import journal as script

    lignes = [
        {"t": "2026-08-04T10:00:00+02:00", "qui": "admin", "module": "live", "action": "annonce_posee"},
        {"t": "2026-08-04T10:01:00+02:00", "qui": "benevole", "module": "pret", "action": "pret"},
    ]
    assert [l["module"] for l in script.filtrer(lignes, module="pret")] == ["pret"]
    assert [l["qui"] for l in script.filtrer(lignes, qui="admin")] == ["admin"]


def test_script_filtrer_depuis(tmp_path):
    from scripts import journal as script
    from app.services import FUSEAU_LOCAL

    lignes = [
        {"t": "2026-08-04T09:00:00+02:00", "qui": "admin", "module": "pret", "action": "pret"},
        {"t": "2026-08-04T14:00:00+02:00", "qui": "admin", "module": "pret", "action": "pret"},
    ]
    seuil = datetime(2026, 8, 4, 12, 0, tzinfo=FUSEAU_LOCAL)
    restant = script.filtrer(lignes, depuis=seuil)
    assert len(restant) == 1
    assert restant[0]["t"].startswith("2026-08-04T14")


def test_script_parser_depuis_forme_valide_et_invalide():
    from scripts import journal as script

    dt = script.parser_depuis("12:00")
    assert dt.hour == 12 and dt.minute == 0
    with pytest.raises(ValueError):
        script.parser_depuis("pas une heure")


def test_script_main_brut_restitue_le_json_a_lidentique(tmp_path, monkeypatch, capsys):
    from scripts import journal as script

    chemin = tmp_path / "j.log"
    ligne = {
        "t": "2026-08-04T10:00:00+02:00", "qui": "benevole", "appareil": "3F1A9C",
        "module": "pret", "action": "retour", "objet": "Catan", "ok": True,
    }
    _ecrire_fichier_exemple(chemin, [ligne])

    monkeypatch.setenv("JOURNAL_PATH", str(chemin))
    monkeypatch.setattr(sys, "argv", ["journal.py", "--brut"])
    script.main()

    sortie = capsys.readouterr().out.strip()
    assert json.loads(sortie) == ligne


def test_script_main_format_par_defaut_lisible(tmp_path, monkeypatch, capsys):
    from scripts import journal as script

    chemin = tmp_path / "j.log"
    _ecrire_fichier_exemple(chemin, [{
        "t": "2026-08-04T10:00:00+02:00", "qui": "benevole", "appareil": "3F1A9C",
        "module": "pret", "action": "retour", "objet": "Catan", "ok": True,
    }])
    monkeypatch.setenv("JOURNAL_PATH", str(chemin))
    monkeypatch.setattr(sys, "argv", ["journal.py"])
    script.main()

    sortie = capsys.readouterr().out
    assert "10:00:00" in sortie
    assert "benevole" in sortie
    assert "Catan" in sortie
    # Sortie pour l'œil, PAS du JSON.
    assert not sortie.strip().startswith("{")


def test_script_main_filtre_module_via_cli(tmp_path, monkeypatch, capsys):
    from scripts import journal as script

    chemin = tmp_path / "j.log"
    _ecrire_fichier_exemple(chemin, [
        {"t": "2026-08-04T10:00:00+02:00", "qui": "admin", "module": "live",
         "action": "annonce_posee", "objet": "Tombola", "ok": True},
        {"t": "2026-08-04T10:01:00+02:00", "qui": "benevole", "module": "pret",
         "action": "pret", "objet": "Catan", "ok": True},
    ])
    monkeypatch.setenv("JOURNAL_PATH", str(chemin))
    monkeypatch.setattr(sys, "argv", ["journal.py", "--module", "pret", "--brut"])
    script.main()

    sortie = capsys.readouterr().out.strip().splitlines()
    assert len(sortie) == 1
    assert json.loads(sortie[0])["module"] == "pret"


def test_script_main_aucune_ligne_message_sur_stderr(tmp_path, monkeypatch, capsys):
    from scripts import journal as script

    monkeypatch.setenv("JOURNAL_PATH", str(tmp_path / "absent.log"))
    monkeypatch.setattr(sys, "argv", ["journal.py"])
    script.main()

    capture = capsys.readouterr()
    assert capture.out == ""
    assert "Aucune activité enregistrée" in capture.err
