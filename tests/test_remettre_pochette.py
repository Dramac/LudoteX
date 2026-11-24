"""
Remettre en service une pochette ÉCARTÉE (lot comptoir-10) :
`services.remettre_pochette_en_service`, `services.pochettes_ecartees`, le
bouton du bandeau qui suit un changement (`POST /pret/<id>/pochette/remettre`)
et le bloc « Pochettes écartées » de la page Jeux sortis
(`POST /sortis/pochettes-ecartees`).

Fixture et helpers repris de `tests/test_changer_pochette.py` : trois boîtes,
001/Catan, 002/Dixit, 003/Catan.
"""

import json

import pytest

from tests.test_changer_pochette import _ecartees, _pochette_du_pret, client  # noqa: F401

# Libellés cités mot pour mot par le wiki (Module-Pret, Jeux-Sortis).
BOUTON_BANDEAU = "Remettre le n°{} en service"
BOUTON_LISTE = "Remettre en service"
TITRE_BLOC = "Pochettes écartées"
CONFIRMATION = "La pochette n°{} sera de nouveau proposée."


def _remettre(numero):
    from app import db, services
    conn = db.get_connection()
    try:
        return services.remettre_pochette_en_service(conn, numero)
    finally:
        conn.close()


def _ecarter_le_1(client):
    """Prêt de 001 sur le 1, puis changement vers le 5 : le 1 est écarté."""
    client.post("/pret/001/preter")
    return client.post("/pret/001/pochette", data={"numero": "5"})


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
def test_service_remet_en_service_et_le_pret_suivant_le_reprend(client):
    _ecarter_le_1(client)
    assert _ecartees() == [1]
    assert _remettre(1) == "remise"
    assert _ecartees() == []
    r = client.post("/pret/002/preter")
    assert '<p class="pochette-num">1</p>' in r.text


def test_service_idempotent(client):
    _ecarter_le_1(client)
    assert _remettre(1) == "remise"
    assert _remettre(1) == "deja_en_service"
    assert _ecartees() == []


def test_service_numero_inconnu(client):
    assert _remettre(42) == "inconnu"
    from tests.test_changer_pochette import _lire
    assert _lire("SELECT * FROM pochettes") == []


def test_service_ne_touche_pas_occupe(client):
    _ecarter_le_1(client)
    _remettre(1)
    from tests.test_changer_pochette import _pochettes
    assert _pochettes() == {1: 0, 2: 0, 3: 0, 4: 0, 5: 1}


def test_liste_triee(client):
    client.post("/pret/001/preter")                          # 1
    client.post("/pret/002/preter")                          # 2
    client.post("/pret/002/pochette", data={"numero": "9"})  # 2 écarté
    client.post("/pret/001/pochette", data={"numero": "7"})  # 1 écarté
    from app import db, services
    conn = db.get_connection()
    try:
        assert services.pochettes_ecartees(conn) == [1, 2]
    finally:
        conn.close()


def test_la_cloture_reste_inchangee(client):
    from app import db, services
    _ecarter_le_1(client)
    conn = db.get_connection()
    try:
        services.cloturer_evenement(conn)
    finally:
        conn.close()
    assert _ecartees() == []
    assert _remettre(1) == "deja_en_service"


# ---------------------------------------------------------------------------
# Bandeau qui suit le changement
# ---------------------------------------------------------------------------
def test_le_bouton_suit_le_changement(client):
    r = _ecarter_le_1(client)
    assert BOUTON_BANDEAU.format(1) in r.text
    assert 'action="/pret/001/pochette/remettre"' in r.text
    assert '<input type="hidden" name="numero" value="1">' in r.text


@pytest.mark.parametrize("etape", ["pret", "refus", "affichage", "inchange"])
def test_le_bouton_n_apparait_que_sur_le_changement(client, etape):
    r = client.post("/pret/001/preter")
    if etape == "refus":
        client.post("/pret/002/preter")                      # 2
        r = client.post("/pret/001/pochette", data={"numero": "2"})
    elif etape == "affichage":
        r = client.get("/pret/001")
    elif etape == "inchange":
        r = client.post("/pret/001/pochette", data={"numero": "1"})
    assert "/pochette/remettre" not in r.text


def test_le_tap_confirme_et_garde_le_grand_numero(client):
    _ecarter_le_1(client)
    r = client.post("/pret/001/pochette/remettre", data={"numero": "1"})
    assert r.status_code == 200
    assert '<p class="pochette-num">5</p>' in r.text
    assert CONFIRMATION.format(1) in r.text
    assert 'role="status"' in r.text
    # Le geste « Utiliser une autre pochette » reste là, le bouton est parti.
    assert "Utiliser une autre pochette" in r.text
    assert BOUTON_BANDEAU.format(1) not in r.text
    assert _pochette_du_pret("001") == 5
    assert _ecartees() == []


def test_double_tap_meme_ecran(client):
    _ecarter_le_1(client)
    client.post("/pret/001/pochette/remettre", data={"numero": "1"})
    r = client.post("/pret/001/pochette/remettre", data={"numero": "1"})
    assert r.status_code == 200
    assert CONFIRMATION.format(1) in r.text
    assert '<p class="pochette-num">5</p>' in r.text


@pytest.mark.parametrize("saisie,phrase", [
    ("42", "Aucune pochette n°42 n'est écartée : rien n'a été modifié."),
    ("abc", "Ce n'est pas un numéro de pochette : rien n'a été modifié."),
    ("", "Ce n'est pas un numéro de pochette : rien n'a été modifié."),
    ("0", "Ce n'est pas un numéro de pochette : rien n'a été modifié."),
])
def test_numero_forge_donne_une_phrase(client, saisie, phrase):
    _ecarter_le_1(client)
    r = client.post("/pret/001/pochette/remettre", data={"numero": saisie})
    assert r.status_code == 200
    assert phrase in r.text
    assert _ecartees() == [1]


def test_boite_rendue_entre_temps(client):
    _ecarter_le_1(client)
    client.post("/pret/001/rendre")
    r = client.post("/pret/001/pochette/remettre", data={"numero": "1"})
    assert r.status_code == 200
    assert CONFIRMATION.format(1) in r.text
    assert 'class="pochette-num"' not in r.text


def test_bandeau_exige_le_jeton(client, monkeypatch):
    _ecarter_le_1(client)
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    r = client.post("/pret/001/pochette/remettre", data={"numero": "1"})
    assert r.status_code == 403
    assert _ecartees() == [1]


# ---------------------------------------------------------------------------
# Page Jeux sortis
# ---------------------------------------------------------------------------
def test_bloc_absent_sans_pochette_ecartee(client):
    client.post("/pret/001/preter")
    r = client.get("/sortis")
    assert TITRE_BLOC not in r.text
    assert "pochettes-ecartees" not in r.text


def test_bloc_liste_les_numeros_ecartes(client):
    _ecarter_le_1(client)
    r = client.get("/sortis")
    assert TITRE_BLOC in r.text
    assert 'id="pochettes-ecartees"' in r.text
    assert BOUTON_LISTE in r.text
    assert 'action="/sortis/pochettes-ecartees#pochettes-ecartees"' in r.text
    # Nom accessible distinct pour chaque ligne.
    assert '<span class="sr-only"> la pochette n°1</span>' in r.text


def test_la_recherche_ne_filtre_pas_le_bloc(client):
    _ecarter_le_1(client)
    r = client.get("/sortis", params={"q": "dixit"})
    assert TITRE_BLOC in r.text
    assert '<input type="hidden" name="q" value="dixit">' in r.text


def test_liste_bouton_retire_le_numero(client):
    client.post("/pret/001/preter")                          # 1
    client.post("/pret/002/preter")                          # 2
    client.post("/pret/002/pochette", data={"numero": "9"})  # 2 écarté
    client.post("/pret/001/pochette", data={"numero": "7"})  # 1 écarté
    r = client.post("/sortis/pochettes-ecartees", data={"numero": "2"})
    assert r.status_code == 200
    assert CONFIRMATION.format(2) in r.text
    assert _ecartees() == [1]
    assert '<input type="hidden" name="numero" value="2">' not in r.text
    assert '<input type="hidden" name="numero" value="1">' in r.text


def test_liste_videe_garde_la_confirmation(client):
    _ecarter_le_1(client)
    r = client.post("/sortis/pochettes-ecartees", data={"numero": "1"})
    assert CONFIRMATION.format(1) in r.text
    assert 'id="pochettes-ecartees"' in r.text
    assert "Aucune." in r.text
    # Rechargement (même POST) : même phrase, sans erreur.
    r = client.post("/sortis/pochettes-ecartees", data={"numero": "1"})
    assert r.status_code == 200
    assert CONFIRMATION.format(1) in r.text


def test_liste_garde_la_recherche(client):
    _ecarter_le_1(client)
    r = client.post("/sortis/pochettes-ecartees", data={"numero": "1", "q": "cat"})
    assert 'value="cat"' in r.text
    assert "pour « cat »" in r.text


def test_liste_exige_le_jeton(client, monkeypatch):
    _ecarter_le_1(client)
    monkeypatch.setenv("PRET_TOKEN", "jeton-test-secret-32-caracteres")
    r = client.post("/sortis/pochettes-ecartees", data={"numero": "1"})
    assert r.status_code == 403
    assert _ecartees() == [1]


# ---------------------------------------------------------------------------
# Journal : l'action, jamais le numéro
# ---------------------------------------------------------------------------
def _lignes(chemin, action):
    return [l for l in (json.loads(x) for x in
            chemin.read_text(encoding="utf-8").splitlines() if x.strip())
            if l["action"] == action]


def test_journal_sans_numero(client, _journal_isole):
    client.post("/pret/001/preter")
    client.post("/pret/001/pochette", data={"numero": "7"})   # 1 écarté
    client.post("/pret/002/preter")                           # 2 (1 écarté)
    client.post("/pret/002/pochette", data={"numero": "8"})   # 2 écarté
    client.post("/pret/001/pochette/remettre", data={"numero": "1"})
    client.post("/pret/001/pochette/remettre", data={"numero": "1"})  # double tap
    client.post("/sortis/pochettes-ecartees", data={"numero": "2"})

    lignes = _lignes(_journal_isole, "pochette_remise_en_service")
    # Deux écarts levés, deux lignes ; le double tap n'écrit rien.
    assert len(lignes) == 2
    assert all(l["module"] == "pret" and l["ok"] for l in lignes)
    assert lignes[0]["objet"] == "Catan"
    for ligne in lignes:
        # L'horodatage et l'empreinte d'appareil peuvent contenir ces
        # chiffres par hasard : écartés.
        sans_heure = {k: v for k, v in ligne.items() if k not in ("t", "appareil")}
        texte = json.dumps(sans_heure, ensure_ascii=False)
        assert "1" not in texte and "2" not in texte
