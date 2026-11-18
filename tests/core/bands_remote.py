"""The band switch running as its own service, in process, for tests: its own SQLite database (never the core's), reached by the
core over real signed requests through the service's FastAPI app."""
import dataclasses
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from amfa import config
from amfa.bands import db as bdb
from amfa.bands import remote, service

URL = "http://bands.test"


def _fresh_db(monkeypatch) -> Path:
    path = Path(tempfile.mkdtemp(prefix="amfa-bands-")) / "bands.db"
    monkeypatch.setenv("AMFA_BANDS_DB_URL", f"sqlite:///{path.as_posix()}")
    reset_db()
    bdb.migrate()
    return path


def reset_db():
    try:
        bdb.engine().dispose()
    except Exception:
        pass
    bdb.engine.cache_clear()
    bdb._factory.cache_clear()


def start(monkeypatch, **settings):
    """Point the core at the band service and give the service a database of its own. Returns the service's test client."""
    _fresh_db(monkeypatch)
    s = dataclasses.replace(config.get(), bands_url=URL, **settings)
    monkeypatch.setattr(config, "get", lambda: s)
    client = TestClient(service.app, base_url=URL)
    remote.mount(client)
    return client


def stop():
    remote.mount(None)
    reset_db()


class Down:
    """A band service that cannot be reached."""
    def __init__(self, exc=None):
        import httpx
        self.exc = exc or httpx.ConnectError("connection refused")
        self.calls = 0

    def post(self, *a, **kw):
        self.calls += 1
        raise self.exc


class Answers:
    """A band service that answers every call with [status] and [body] (raw bytes or a JSON-able value)."""
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, 0

    def post(self, *a, **kw):
        import httpx
        self.calls += 1
        if isinstance(self.body, bytes):
            return httpx.Response(self.status, content=self.body)
        return httpx.Response(self.status, json=self.body)
