"""
Porte d'administration (lot-6-pré-production) : récupération du mot de passe,
longueur minimale, valeur d'exemple refusée, mot de passe absent des fichiers
d'environnement, compteur de débit propre à la connexion admin.

Constats de l'audit du 2026-09-12 : DOC-01, SEC-13, SEC-16, SEC-17, SEC-03.
"""

from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path

import pytest

from app import admin_auth, auth

_RACINE = Path(__file__).resolve().parent.parent
MOT_DE_PASSE = "secret-porte-admin"


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    db.init_db()
    tdb.init_db()
    pdb.init_db()
    return tmp_path


@pytest.fixture
def client(bases, monkeypatch):
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def _hash(chemin: Path) -> str | None:
    conn = sqlite3.connect(chemin)
    try:
        ligne = conn.execute(
            "SELECT valeur FROM parametres WHERE cle = 'admin_hash'"
        ).fetchone()
    finally:
        conn.close()
    return ligne[0] if ligne else None


# ===========================================================================
# POINT 1 — DOC-01 : le script de récupération
# ===========================================================================
def _script(monkeypatch, argv, saisie=None):
    """Lance le script en mémoire ; `saisie` = ce que lirait getpass ou stdin."""
    from scripts import reinitialiser_mot_de_passe as script

    if saisie is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(saisie + "\n"))
        monkeypatch.setattr(script.getpass, "getpass", lambda _invite="": saisie)
    monkeypatch.setattr("builtins.input", lambda _invite="": "o")
    return script.main(argv)


def test_script_refuse_une_base_absente_sans_la_creer(tmp_path, monkeypatch, capsys):
    from scripts import reinitialiser_mot_de_passe as script

    absente = tmp_path / "sous-dossier" / "pret-jeux.db"
    monkeypatch.setenv("DATABASE_PATH", str(absente))
    code = _script(monkeypatch, ["--stdin"], "un-mot-de-passe-long")
    assert code == script.BASE_ABSENTE
    assert not absente.exists()
    assert not absente.parent.exists()
    sortie = capsys.readouterr()
    assert str(absente) in sortie.out
    assert "n'existe pas" in sortie.err


def test_script_nouveau_accepte_ancien_refuse_sans_redemarrage(bases, monkeypatch):
    from app.db import get_connection
    from scripts import reinitialiser_mot_de_passe as script

    monkeypatch.setenv("ADMIN_PASSWORD", "ancien-mot-de-passe")
    # Connexion ouverte AVANT, comme celle d'un serveur en marche.
    conn = get_connection()
    try:
        assert admin_auth.verifier_identifiants(conn, "ancien-mot-de-passe")
        assert _script(monkeypatch, [], "nouveau-mot-de-passe") == script.OK
        assert admin_auth.verifier_identifiants(conn, "nouveau-mot-de-passe")
        assert not admin_auth.verifier_identifiants(conn, "ancien-mot-de-passe")
    finally:
        conn.close()


def test_script_remplace_ce_que_l_ecran_de_connexion_verifie(client, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    assert client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE},
                       follow_redirects=False).status_code == 303
    assert _script(monkeypatch, [], "remplace-au-clavier") == script.OK
    client.cookies.clear()
    assert client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE}).status_code == 403
    assert client.post("/admin/login", data={"mot_de_passe": "remplace-au-clavier"},
                       follow_redirects=False).status_code == 303


def test_script_refuse_un_mot_de_passe_trop_court(bases, monkeypatch, capsys):
    from scripts import reinitialiser_mot_de_passe as script

    court = "x" * (admin_auth.LONGUEUR_MIN_MDP - 1)
    assert _script(monkeypatch, [], court) == script.REFUS
    assert _hash(bases / "test.db") is None
    assert f"{admin_auth.LONGUEUR_MIN_MDP} caractères" in capsys.readouterr().err


def test_script_refuse_la_valeur_d_exemple(bases, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    assert _script(monkeypatch, [], admin_auth.MDP_EXEMPLE) == script.REFUS
    assert _hash(bases / "test.db") is None


def test_script_confirmation_differente_rien_n_est_ecrit(bases, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    reponses = iter(["premiere-saisie", "seconde-saisie"])
    monkeypatch.setattr(script.getpass, "getpass", lambda _i="": next(reponses))
    monkeypatch.setattr("builtins.input", lambda _i="": "o")
    assert script.main([]) == script.REFUS
    assert _hash(bases / "test.db") is None


def test_script_abandon_si_on_ne_confirme_pas(bases, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    monkeypatch.setattr(script.getpass, "getpass", lambda _i="": "jamais-demande")
    monkeypatch.setattr("builtins.input", lambda _i="": "")
    assert script.main([]) == script.REFUS
    assert _hash(bases / "test.db") is None


def test_script_si_absent_conserve_un_mot_de_passe_existant(bases, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    assert _script(monkeypatch, ["--stdin"], "premier-mot-de-passe") == script.OK
    avant = _hash(bases / "test.db")
    assert _script(monkeypatch, ["--stdin", "--si-absent"], "autre-mot-de-passe") == script.OK
    assert _hash(bases / "test.db") == avant


def test_script_n_affiche_jamais_le_mot_de_passe(bases, monkeypatch, capsys):
    _script(monkeypatch, [], "ne-doit-pas-sortir")
    sortie = capsys.readouterr()
    assert "ne-doit-pas-sortir" not in sortie.out + sortie.err


def test_script_vise_l_instance_du_fichier_env(tmp_path, bases, monkeypatch, capsys):
    """`--env` (fichier de la formation, lu par systemd) l'emporte sur l'environnement."""
    from scripts import reinitialiser_mot_de_passe as script

    formation = tmp_path / "formation.db"
    conn = sqlite3.connect(formation)
    conn.execute("CREATE TABLE parametres (cle TEXT PRIMARY KEY, valeur TEXT)")
    conn.commit()
    conn.close()
    env = tmp_path / "ludotex-formation.env"
    # `--env` écrit dans os.environ (override) : seule DATABASE_PATH, que la
    # fixture `bases` a posée par monkeypatch, donc restaurée après le test.
    env.write_text(f'DATABASE_PATH="{formation}"\n', encoding="utf-8")

    assert _script(monkeypatch, ["--env", str(env), "--stdin"], "mot-de-passe-formation") == script.OK
    assert str(formation) in capsys.readouterr().out
    assert _hash(formation) is not None
    assert _hash(bases / "test.db") is None


def test_script_fichier_env_introuvable(tmp_path, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    assert script.main(["--env", str(tmp_path / "absent.env"), "--stdin"]) == script.BASE_ABSENTE


def test_script_refuse_un_fichier_qui_n_est_pas_une_base_de_pret(tmp_path, monkeypatch):
    from scripts import reinitialiser_mot_de_passe as script

    autre = tmp_path / "autre.db"
    sqlite3.connect(autre).close()
    monkeypatch.setenv("DATABASE_PATH", str(autre))
    assert _script(monkeypatch, ["--stdin"], "un-mot-de-passe-long") == script.ERREUR


# ===========================================================================
# POINT 2 — SEC-13 : longueur minimale et messages distincts
# ===========================================================================
def test_la_longueur_minimale_d_install_sh_est_celle_du_code():
    texte = (_RACINE / "deploy" / "install.sh").read_text(encoding="utf-8")
    seuils = re.findall(r"\$\{#ADMIN_PASSWORD\}\s+-lt\s+(\d+)", texte)
    assert seuils, "contrôle de longueur introuvable dans install.sh"
    assert {int(n) for n in seuils} == {admin_auth.LONGUEUR_MIN_MDP}


def _connecter(client):
    r = client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE},
                    follow_redirects=False)
    assert r.status_code == 303


def test_changer_refuse_un_mot_de_passe_trop_court(client):
    _connecter(client)
    court = "a" * (admin_auth.LONGUEUR_MIN_MDP - 1)
    r = client.post("/admin/motdepasse", data={
        "ancien": MOT_DE_PASSE, "nouveau": court, "confirmation": court})
    assert "trop court" in r.text
    assert f"{admin_auth.LONGUEUR_MIN_MDP} caractères minimum" in r.text
    assert "incorrect" not in r.text
    client.cookies.clear()
    assert client.post("/admin/login", data={"mot_de_passe": court}).status_code == 403


def test_changer_ancien_incorrect_a_son_propre_message(client):
    _connecter(client)
    r = client.post("/admin/motdepasse", data={
        "ancien": "pas-le-bon", "nouveau": "tout-a-fait-valable",
        "confirmation": "tout-a-fait-valable"})
    assert "actuel est incorrect" in r.text
    assert "trop court" not in r.text


def test_changer_la_longueur_minimale_exacte_est_acceptee(client):
    _connecter(client)
    juste = "b" * admin_auth.LONGUEUR_MIN_MDP
    r = client.post("/admin/motdepasse", data={
        "ancien": MOT_DE_PASSE, "nouveau": juste, "confirmation": juste})
    assert "Mot de passe modifié" in r.text


def test_changer_refuse_la_valeur_d_exemple(client):
    _connecter(client)
    r = client.post("/admin/motdepasse", data={
        "ancien": MOT_DE_PASSE, "nouveau": admin_auth.MDP_EXEMPLE,
        "confirmation": admin_auth.MDP_EXEMPLE})
    assert "exemple" in r.text and "Mot de passe modifié" not in r.text


def test_trop_court_journalise_avec_son_propre_detail(client, _journal_isole):
    import json

    _connecter(client)
    client.post("/admin/motdepasse", data={
        "ancien": MOT_DE_PASSE, "nouveau": "court", "confirmation": "court"})
    lignes = [json.loads(l) for l in _journal_isole.read_text().splitlines()]
    ligne = [l for l in lignes if l["action"] == "motdepasse_change"][-1]
    assert ligne["ok"] is False and ligne["detail"] == "trop_court"
    assert "court" not in (ligne.get("objet") or "")


def test_le_formulaire_porte_minlength(client):
    _connecter(client)
    page = client.get("/admin/motdepasse").text
    assert page.count(f'minlength="{admin_auth.LONGUEUR_MIN_MDP}"') == 2


# ===========================================================================
# POINT 3 — SEC-16 : les valeurs d'exemple de .env.example sont refusées
# ===========================================================================
def _valeur_exemple(cle: str) -> str:
    """Valeur de `cle` dans .env.example, que la ligne soit active ou commentée."""
    texte = (_RACINE / ".env.example").read_text(encoding="utf-8")
    trouve = re.findall(rf"^#?\s*{cle}=(.*)$", texte, flags=re.MULTILINE)
    assert len(trouve) == 1, f"{cle} doit figurer une fois dans .env.example"
    return trouve[0].strip()


def test_la_valeur_d_exemple_du_mot_de_passe_est_celle_du_code():
    assert _valeur_exemple("ADMIN_PASSWORD") == admin_auth.MDP_EXEMPLE


def test_la_valeur_d_exemple_du_jeton_est_celle_du_code():
    assert _valeur_exemple("PRET_TOKEN") == auth._PLACEHOLDER


def test_la_valeur_d_exemple_n_amorce_pas_l_administration(bases, monkeypatch):
    from app.db import get_connection

    monkeypatch.setenv("ADMIN_PASSWORD", _valeur_exemple("ADMIN_PASSWORD"))
    conn = get_connection()
    try:
        assert not admin_auth.admin_configure(conn)
        assert not admin_auth.verifier_identifiants(conn, admin_auth.MDP_EXEMPLE)
        assert admin_auth.get_admin_hash(conn) is None
    finally:
        conn.close()


def test_l_ecran_de_connexion_dit_pourquoi_et_quoi_faire(bases, monkeypatch):
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setenv("ADMIN_PASSWORD", admin_auth.MDP_EXEMPLE)
    from fastapi.testclient import TestClient

    from app.main import app
    page = TestClient(app).get("/admin").text
    assert "celui de l'exemple" in page
    assert "Mot de passe admin oublié" in page
    assert 'name="mot_de_passe"' not in page


def test_sans_aucun_mot_de_passe_l_ecran_renvoie_a_la_procedure(bases, monkeypatch):
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    from fastapi.testclient import TestClient

    from app.main import app
    page = TestClient(app).get("/admin").text
    assert "Aucun mot de passe administrateur" in page
    assert "Mot de passe admin oublié" in page


# ===========================================================================
# POINT 4 — SEC-17 : plus de mot de passe en clair laissé par install.sh
# ===========================================================================
def test_install_sh_n_ecrit_le_mot_de_passe_dans_aucun_fichier():
    texte = (_RACINE / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert not re.search(r"^\s*ADMIN_PASSWORD=", texte, flags=re.MULTILINE)
    # Ni en variable d'environnement d'un sous-processus (liste des processus).
    assert 'ADMIN_PASSWORD="$ADMIN_PASSWORD"' not in texte
    # Il passe au script par l'entrée standard.
    assert '<<< "$ADMIN_PASSWORD"' in texte
    assert "reinitialiser_mot_de_passe.py\" --stdin --si-absent" in texte


def test_un_env_sans_admin_password_est_conforme_au_controle_de_report():
    from scripts import controle_report as cr

    exemple = (_RACINE / ".env.example").read_text(encoding="utf-8")
    env = "\n".join(f"{c}=x" for c in sorted(cr.cles_attendues(exemple)))
    assert "ADMIN_PASSWORD" not in env
    assert cr.cles_manquantes(exemple, env) == []


def test_la_reinitialisation_de_la_formation_conserve_le_mot_de_passe(bases, monkeypatch):
    monkeypatch.setenv("FORMATION_SOURCE_DB", str(bases / "inexistant.db"))
    monkeypatch.delenv("FORMATION_CATALOGUE_CSV", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    from app import formation
    from app.db import get_connection

    conn = get_connection()
    try:
        admin_auth.set_admin_hash(conn, admin_auth.hacher_mdp("formation-123"))
    finally:
        conn.close()
    avant = _hash(bases / "test.db")

    formation.peupler()
    formation.peupler()

    assert _hash(bases / "test.db") == avant
    conn = get_connection()
    try:
        # Sans ADMIN_PASSWORD dans l'environnement : l'instance reste ouverte.
        assert admin_auth.verifier_identifiants(conn, "formation-123")
    finally:
        conn.close()


# ===========================================================================
# POINT 5 — SEC-03 : un compteur propre à la connexion admin
# ===========================================================================
def test_soixante_activations_ne_bloquent_pas_la_connexion_admin(client):
    for _ in range(60):
        client.get("/acces?jeton=nimporte")
    r = client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE},
                    follow_redirects=False)
    assert r.status_code == 303


def test_les_essais_admin_ne_consomment_pas_le_quota_de_acces(client):
    for _ in range(admin_auth.LIMITE_CONNEXION + 5):
        client.post("/admin/login", data={"mot_de_passe": "faux"})
    assert client.get("/acces?jeton=nimporte").status_code != 429


def test_le_seuil_de_acces_ne_bouge_pas(client, monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
    codes = [client.get("/acces?jeton=nimporte").status_code for _ in range(61)]
    assert 429 not in codes[:60]
    assert codes[60] == 429


def test_seuil_admin_atteint_message_avec_delai(client):
    for _ in range(admin_auth.LIMITE_CONNEXION):
        assert client.post("/admin/login", data={"mot_de_passe": "faux"}).status_code == 403
    r = client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    assert r.status_code == 429
    delai = re.search(r"Réessayez dans\s+(\d+)\s+seconde", r.text)
    assert delai and 1 <= int(delai.group(1)) <= admin_auth.FENETRE_CONNEXION_S


def test_secondes_avant_essai(monkeypatch):
    maintenant = 1_000_000.0
    monkeypatch.setattr(auth.time, "time", lambda: maintenant)
    cle = admin_auth.cle_debit_connexion("1.2.3.4")
    assert auth.secondes_avant_essai(cle, 3, 60) == 0
    auth._tentatives[cle] = [maintenant - 50, maintenant - 30, maintenant - 10]
    # Il faut que la plus ancienne sorte de la fenêtre : 60 - 50 = 10 s.
    assert auth.secondes_avant_essai(cle, 3, 60) == 10
    auth._tentatives[cle].append(maintenant - 5)
    # Quatre essais pour un seuil de 3 : il faut que les deux plus anciens sortent.
    assert auth.secondes_avant_essai(cle, 3, 60) == 30
