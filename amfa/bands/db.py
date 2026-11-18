"""The band service's own database: only the band switch's tables (amfa/bands/models.py), nothing of the core's.

AMFA_BANDS_DB_URL names it (a Postgres database of its own in deployment); in development it is a SQLite file next to the core's.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from amfa import config
from amfa.bands.models import UPGRADES, BandBase


def url() -> str:
    return os.getenv("AMFA_BANDS_DB_URL") or f"sqlite:///{config.get().data_dir / 'bands.db'}"


@lru_cache(maxsize=1)
def engine():
    u = url()
    kwargs = {"pool_pre_ping": True, "future": True}
    if u.startswith("sqlite"):
        config.get().data_dir.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800)
    eng = create_engine(u, **kwargs)
    if u.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return eng


@lru_cache(maxsize=1)
def _factory() -> sessionmaker:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


@contextmanager
def session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def migrate() -> list[str]:
    """Create the band tables, and add any column an older band database lacks (its rows keep their values)."""
    eng = engine()
    BandBase.metadata.create_all(eng)
    insp = inspect(eng)
    added = []
    with eng.begin() as c:
        for _ver, table, column, ddl in UPGRADES:
            if column not in {col["name"] for col in insp.get_columns(table)}:
                c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                added.append(f"{table}.{column}")
    return added
