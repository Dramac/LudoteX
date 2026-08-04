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

import pytest


class _FauxRequest:
    """Objet minimal portant `.cookies`, suffisant pour journaliser()."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


# ---------------------------------------------------------------------------
# Fixtures — bases temporaires (patron de tests/test_appareils.py).
# ---------------------------------------------------------------------------
@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from app import db

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
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
