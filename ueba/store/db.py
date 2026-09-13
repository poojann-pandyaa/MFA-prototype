"""
Persistence.

SQLite for the prototype harness -- no external service to run, which
matters when three workstreams need to start each other's components.
store/schema.sql holds the PostgreSQL equivalent for deployment; the
access patterns are identical.

Biometric templates are stored as derived statistics (means and standard
deviations), never as raw event streams. A stolen template yields timing
distributions, not a replayable recording of someone typing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("ueba.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    user_id       TEXT PRIMARY KEY,
    enrolled_at   REAL NOT NULL,
    keystroke_tpl TEXT,
    mouse_tpl     TEXT,
    shape_tpl     TEXT,
    session_prof  TEXT
);

CREATE TABLE IF NOT EXISTS security_event (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    amount         REAL,
    beneficiary_id TEXT,
    device_id      TEXT,
    ip_hash        TEXT,
    city           TEXT,
    channel        TEXT,
    occurred_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_user ON security_event(user_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS session_record (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    features   TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_user ON session_record(user_id);

CREATE TABLE IF NOT EXISTS verification (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    risk_prob     REAL,
    band          TEXT NOT NULL,
    decision      TEXT NOT NULL,
    dimensions    TEXT NOT NULL,
    model_version TEXT NOT NULL,
    latency_ms    INTEGER,
    decided_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verif_user ON verification(user_id, decided_at DESC);
"""


@contextmanager
def conn(path: Path | str = DB_PATH):
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init(path: Path | str = DB_PATH) -> None:
    with conn(path) as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ users

def save_user(user_id, keystroke_tpl, mouse_tpl, path=DB_PATH, shape_tpl=None):
    with conn(path) as c:
        c.execute(
            "INSERT OR REPLACE INTO user "
            "(user_id, enrolled_at, keystroke_tpl, mouse_tpl, shape_tpl, session_prof) "
            "VALUES (?,?,?,?,?,"
            "COALESCE((SELECT session_prof FROM user WHERE user_id=?),NULL))",
            (user_id, time.time(), json.dumps(keystroke_tpl),
             json.dumps(mouse_tpl) if mouse_tpl else None,
             json.dumps(shape_tpl) if shape_tpl else None, user_id),
        )


def get_user(user_id, path=DB_PATH):
    with conn(path) as c:
        row = c.execute("SELECT * FROM user WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "enrolled_at": row["enrolled_at"],
        "keystroke_tpl": json.loads(row["keystroke_tpl"]) if row["keystroke_tpl"] else None,
        "mouse_tpl": json.loads(row["mouse_tpl"]) if row["mouse_tpl"] else None,
        "shape_tpl": json.loads(row["shape_tpl"]) if row["shape_tpl"] else None,
        "session_prof": json.loads(row["session_prof"]) if row["session_prof"] else None,
    }


def list_users(path=DB_PATH):
    with conn(path) as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM user").fetchall()]


def save_session_profile(user_id, profile, path=DB_PATH):
    with conn(path) as c:
        c.execute("UPDATE user SET session_prof=? WHERE user_id=?",
                  (json.dumps(profile) if profile else None, user_id))


# ----------------------------------------------------------------- events

def add_event(user_id, event_type, amount, beneficiary_id, device_id,
              ip_hash, city, channel, occurred_at, path=DB_PATH):
    with conn(path) as c:
        cur = c.execute(
            "INSERT INTO security_event (user_id,event_type,amount,beneficiary_id,"
            "device_id,ip_hash,city,channel,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, event_type, amount, beneficiary_id, device_id,
             ip_hash, city, channel, occurred_at),
        )
        return cur.lastrowid


def get_events(user_id, path=DB_PATH):
    with conn(path) as c:
        rows = c.execute(
            "SELECT * FROM security_event WHERE user_id=? ORDER BY occurred_at",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------- sessions

def add_session(user_id, features, path=DB_PATH):
    with conn(path) as c:
        c.execute("INSERT INTO session_record (user_id,features,created_at) VALUES (?,?,?)",
                  (user_id, json.dumps(features), time.time()))


def get_sessions(user_id, path=DB_PATH):
    with conn(path) as c:
        rows = c.execute("SELECT features FROM session_record WHERE user_id=?",
                         (user_id,)).fetchall()
    return [json.loads(r["features"]) for r in rows]


# ---------------------------------------------------------- verifications

def add_verification(user_id, risk_prob, band, decision, dimensions,
                     model_version, latency_ms, path=DB_PATH):
    with conn(path) as c:
        c.execute(
            "INSERT INTO verification (user_id,risk_prob,band,decision,dimensions,"
            "model_version,latency_ms,decided_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, risk_prob, band, decision, json.dumps(dimensions),
             model_version, latency_ms, time.time()),
        )


def get_verifications(user_id=None, limit=50, path=DB_PATH):
    with conn(path) as c:
        if user_id:
            rows = c.execute(
                "SELECT * FROM verification WHERE user_id=? ORDER BY decided_at DESC LIMIT ?",
                (user_id, limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM verification ORDER BY decided_at DESC LIMIT ?",
                (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["dimensions"] = json.loads(d["dimensions"])
        out.append(d)
    return out
