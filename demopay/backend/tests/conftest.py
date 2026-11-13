import os
import tempfile

# Settings are read when the app is imported, so set them first.
os.environ["DEMOPAY_DB"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["DEMOPAY_DEMO"] = "1"
os.environ["DEMOPAY_MIN_READ_SECONDS"] = "0"
os.environ["DEMOPAY_HOLD_SECONDS"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from ato import model as ato_model  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed.seed_directory(db)
    yield


@pytest.fixture()
def client():
    return TestClient(app)


def _signup(client, name):
    r = client.post("/auth/signup", json={"username": name, "password": "secret1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Device-Id": "dev1"}


@pytest.fixture()
def auth(client):
    return _signup(client, "alice")


@pytest.fixture()
def other(client):
    return _signup(client, "bob")


class StubScorer:
    card = {"variants": {v: {"thresholds": {"MEDIUM": 4.6, "HIGH": 6.6, "CRITICAL": 8.3}} for v in ("full", "server_only")}}

    def __init__(self, severity, threat):
        self.severity, self.threat = severity, threat

    def score(self, ev, state, attested):
        return ato_model.Result(self.severity, "stub", 7.0, "full" if attested else "server_only", [("new_payee", 3.1)], self.threat,
                                {"takeover": 1.0, "coached": 7.0, "payee": 2.0})


@pytest.fixture()
def stub(monkeypatch):
    """stub('HIGH', 'COACHED') makes the risk model return that decision, so flow tests do not depend on model weights."""
    def _use(severity, threat="COACHED"):
        monkeypatch.setattr(ato_model, "get_scorer", lambda: StubScorer(severity, threat))
    return _use
