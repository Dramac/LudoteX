"""
DOMAINE DES UID iCALENDAR — domicile unique et stabilité.

Les trois exports `.ics` (planning, tournoi, programme) terminent chaque UID
par un domaine. Il portait jusqu'ici, dans les TROIS modules, le même littéral
recopié contenant le nom de l'association SOUDÉ, sans espaces : une forme
qu'aucune recherche de ce nom ne pouvait trouver, et qui partait dans chaque
fichier téléchargé par un bénévole.

Deux familles d'assertions, complémentaires :

1. **fonctionnelle** — les `.ics` réellement produits par les routes portent le
   domaine attendu. Comme `tests/test_nom_association.py`, on ne produit ici
   que les deux `.ics` que la fixture permet d'obtenir sans donnée
   personnelle ; celui du planning exige un bénévole et son code.
2. **structurelle** — aucun module ne réintroduit un domaine en dur. C'est
   celle qui protège l'AVENIR : un quatrième export ajouté plus tard avec son
   propre littéral fait tomber ce test, alors que la première famille ne
   connaît que les routes qui existent aujourd'hui.

⚠️ CE DOMAINE EST FIGÉ. Un agenda reconnaît un événement déjà importé à son
UID : le changer transforme une mise à jour en doublon chez tous ceux qui ont
déjà importé un `.ics`. Ne pas le rendre administrable ni le dériver de
`BASE_URL` — la raison est écrite dans `app/config.py`.
"""

import re
from pathlib import Path

import pytest

from app.config import DOMAINE_UID_ICS


MOT_DE_PASSE = "secret-admin-ics"

# Les modules qui écrivent une ligne UID:. À compléter si un export s'ajoute.
MODULES_ICS = (
    "app/planning/services.py",
    "app/tournoi/services.py",
    "app/tournoi/programme.py",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Trois bases temporaires, patron de tests/test_nom_association.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _uids(texte):
    """iCalendar impose des fins de ligne CRLF, que la ligne capturée conserve."""
    return [u.rstrip("\r") for u in re.findall(r"^UID:(.+)$", texte, re.MULTILINE)]


# ---------------------------------------------------------------------------
# 1. FONCTIONNELLE — ce que les routes produisent vraiment
# ---------------------------------------------------------------------------
def test_l_ics_d_un_tournoi_porte_le_domaine_du_produit(client):
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    r = client.post("/tournoi/nouveau",
                    data={"jeu": "Catan", "date_heure": "2026-08-15T14:00"},
                    follow_redirects=False)
    tid = r.headers["location"].split("/")[2]
    client.post(f"/tournoi/{tid}/etat", data={"etat": "inscriptions"})

    uids = _uids(client.get(f"/tournoi/{tid}/agenda.ics").text)
    assert uids, "aucun UID produit : le scénario ne teste plus rien"
    for uid in uids:
        assert uid.endswith("@" + DOMAINE_UID_ICS), uid


def test_l_ics_d_un_element_de_programme_porte_le_domaine_du_produit(client):
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    client.post("/programme/nouveau",
                data={"intitule": "Initiation au go",
                      "date_heure": "2026-08-15T10:00",
                      "heure_fin": "2026-08-15T11:00"})
    from app.tournoi import db as tdb

    conn = tdb.get_connection()
    try:
        eid = conn.execute(
            "SELECT id_element FROM programme ORDER BY id_element DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()
    client.post(f"/programme/{eid}/etat", data={"etat": "publie"})

    uids = _uids(client.get(f"/programme/{eid}/agenda.ics").text)
    assert uids, "aucun UID produit : le scénario ne teste plus rien"
    for uid in uids:
        assert uid.endswith("@" + DOMAINE_UID_ICS), uid


# ---------------------------------------------------------------------------
# 2. STRUCTURELLE — celle qui protège l'avenir
# ---------------------------------------------------------------------------
def test_aucun_module_ne_code_un_domaine_d_uid_en_dur():
    """
    Toute ligne `UID:` d'un module doit finir par la constante, jamais par un
    littéral. C'est l'assertion qui aurait fait tomber la suite le jour où le
    nom de l'association a été soudé dans les trois modules.
    """
    racine = Path(__file__).resolve().parent.parent
    fautifs = []
    for chemin in MODULES_ICS:
        for ligne in (racine / chemin).read_text(encoding="utf-8").splitlines():
            if "UID:" not in ligne:
                continue
            if "{DOMAINE_UID_ICS}" not in ligne:
                fautifs.append(f"{chemin} : {ligne.strip()}")
    assert not fautifs, (
        "domaine d'UID en dur — il doit venir de config.DOMAINE_UID_ICS :\n"
        + "\n".join(fautifs)
    )


def test_le_domaine_ne_contient_que_le_nom_du_produit():
    """
    Garde-fou de neutralisation : le domaine est le nom du LOGICIEL. S'il
    devait un jour désigner un déploiement, ce serait par un réglage — et la
    stabilité des UID l'interdit (voir la docstring du module).
    """
    assert DOMAINE_UID_ICS == "ludotex"
