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
    session_prof  TEXT,
    keystroke_touch_tpl TEXT,
    shape_touch_tpl     TEXT
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

CREATE TABLE IF NOT EXISTS challenge (
    challenge_id TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    issued_at    REAL NOT NULL,
    expires_at   REAL NOT NULL,
    used_at      REAL,
    input        TEXT NOT NULL DEFAULT 'keyboard'
);

-- Context captured by /risk/preview, held until the orchestrator reports
-- how the session resolved. Only an APPROVED outcome moves it into the
-- user's history: a failed, possibly fraudulent attempt must never become
-- part of anyone's "normal".
CREATE TABLE IF NOT EXISTS pending_context (
    transaction_id TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    event          TEXT NOT NULL,
    session        TEXT,
    created_at     REAL NOT NULL,
    committed      TEXT
);

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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS leaves an
# existing table alone, so a database made by an older version gets them here.
MIGRATIONS = {
    "user": [("keystroke_touch_tpl", "TEXT"), ("shape_touch_tpl", "TEXT")],
    "challenge": [("input", "TEXT NOT NULL DEFAULT 'keyboard'")],
}

# One profile per user, one template set per kind of input. The original
# columns hold the keyboard set (physical keyboard + mouse or trackpad), so
# profiles enrolled before touch support keep working unchanged.
TEMPLATE_COLUMNS = {
    "keyboard": ("keystroke_tpl", "shape_tpl"),
    "touch": ("keystroke_touch_tpl", "shape_touch_tpl"),
}


def init(path: Path | str = DB_PATH) -> None:
    with conn(path) as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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


def save_templates(user_id, input, keystroke_tpl, shape_tpl, mouse_tpl=None, path=DB_PATH):
    """
    Store one input's template set without touching the other: adding a phone
    must not erase the laptop templates. Mouse movement is keyboard-only.
    """
    ks_col, sh_col = TEMPLATE_COLUMNS[input]
    dump = lambda t: json.dumps(t) if t else None
    with conn(path) as c:
        c.execute("INSERT OR IGNORE INTO user (user_id, enrolled_at) VALUES (?,?)",
                  (user_id, time.time()))
        c.execute(f"UPDATE user SET {ks_col}=?, {sh_col}=? WHERE user_id=?",
                  (dump(keystroke_tpl), dump(shape_tpl), user_id))
        if input == "keyboard":
            c.execute("UPDATE user SET mouse_tpl=? WHERE user_id=?", (dump(mouse_tpl), user_id))


def get_user(user_id, path=DB_PATH):
    with conn(path) as c:
        row = c.execute("SELECT * FROM user WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    load = lambda k: json.loads(row[k]) if k in row.keys() and row[k] else None
    return {
        "user_id": row["user_id"],
        "enrolled_at": row["enrolled_at"],
        "keystroke_tpl": load("keystroke_tpl"),
        "mouse_tpl": load("mouse_tpl"),
        "shape_tpl": load("shape_tpl"),
        "session_prof": load("session_prof"),
        "keystroke_touch_tpl": load("keystroke_touch_tpl"),
        "shape_touch_tpl": load("shape_touch_tpl"),
    }


def templates(user: dict | None, input: str) -> tuple[dict | None, dict | None]:
    """(keystroke, shape) templates for one input, or (None, None)."""
    if user is None:
        return None, None
    ks_col, sh_col = TEMPLATE_COLUMNS[input]
    return user.get(ks_col), user.get(sh_col)


def enrolled_inputs(user: dict | None) -> list[str]:
    return [i for i in TEMPLATE_COLUMNS if templates(user, i)[0] is not None]


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


# ------------------------------------------------------------ challenges

def issue_challenge(challenge_id, user_id, ttl_s, path=DB_PATH, input="keyboard"):
    now = time.time()
    with conn(path) as c:
        c.execute("INSERT INTO challenge (challenge_id,user_id,issued_at,expires_at,input) "
                  "VALUES (?,?,?,?,?)", (challenge_id, user_id, now, now + ttl_s, input))
    return now + ttl_s


def challenge_input(challenge_id, path=DB_PATH) -> str | None:
    with conn(path) as c:
        row = c.execute("SELECT input FROM challenge WHERE challenge_id=?",
                        (challenge_id,)).fetchone()
    return row["input"] if row else None


def consume_challenge(challenge_id, user_id, path=DB_PATH) -> str:
    """
    Mark a challenge used. Returns OK, UNKNOWN, WRONG_USER, EXPIRED or USED.

    The update is conditional on used_at being NULL, so two concurrent
    submissions of the same challenge cannot both succeed.
    """
    now = time.time()
    with conn(path) as c:
        row = c.execute("SELECT * FROM challenge WHERE challenge_id=?",
                        (challenge_id,)).fetchone()
        if row is None:
            return "UNKNOWN"
        if row["user_id"] != user_id:
            return "WRONG_USER"
        if row["used_at"] is not None:
            return "USED"
        if row["expires_at"] < now:
            return "EXPIRED"
        cur = c.execute("UPDATE challenge SET used_at=? WHERE challenge_id=? "
                        "AND used_at IS NULL", (now, challenge_id))
        return "OK" if cur.rowcount == 1 else "USED"


# ------------------------------------------------------- pending context

def put_pending(transaction_id, user_id, event, session=None, path=DB_PATH):
    with conn(path) as c:
        c.execute("INSERT OR REPLACE INTO pending_context "
                  "(transaction_id,user_id,event,session,created_at,committed) "
                  "VALUES (?,?,?,?,?,NULL)",
                  (transaction_id, user_id, json.dumps(event),
                   json.dumps(session) if session else None, time.time()))


def get_pending(transaction_id, path=DB_PATH):
    with conn(path) as c:
        row = c.execute("SELECT * FROM pending_context WHERE transaction_id=?",
                        (transaction_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["event"] = json.loads(d["event"])
    d["session"] = json.loads(d["session"]) if d["session"] else None
    return d


def mark_committed(transaction_id, outcome, path=DB_PATH):
    with conn(path) as c:
        c.execute("UPDATE pending_context SET committed=? WHERE transaction_id=?",
                  (outcome, transaction_id))
