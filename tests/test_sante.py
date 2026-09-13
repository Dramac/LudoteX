"""
/sante DIT QUELLE VERSION RÉPOND.

`deploy/update.sh` conclut chaque mise à jour en interrogeant `/sante`. Tant
que la réponse se limitait à `{"statut": "ok"}`, elle prouvait qu'un code
tournait, pas que c'était le nouveau. Aucun test ne figeait cette réponse.
"""

from __future__ import annotations

import pytest

from app.version import APP_VERSION


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_sante_repond_ok_avec_la_version_de_l_application(client):
    reponse = client.get("/sante")
    assert reponse.status_code == 200
    assert reponse.json() == {"statut": "ok", "version": APP_VERSION}
