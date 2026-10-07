from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
from amfa_client import AmfaClient, AmfaError, verify_webhook

HERE = Path(__file__).resolve().parent
AMFA = AmfaClient(os.environ["AMFA_BASE_URL"], os.environ["AMFA_KEY_ID"], os.environ["AMFA_KEY_SECRET"])
TENANT = os.environ["AMFA_TENANT_ID"]
WEBHOOK_SECRET = os.getenv("AMFA_WEBHOOK_SECRET", "")
SESSION_SECRET = os.environ["BANK_SESSION_SECRET"].encode()
DAILY_LIMIT_MINOR = 10_00_000_00

_lock = threading.RLock()
STATE_FILE = Path(os.environ["BANK_STATE_FILE"]) if os.getenv("BANK_STATE_FILE") else None
OPENING_BALANCE = 5_00_000_00
_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash(secrets.token_hex(16))


def _default_payees() -> dict:
    return {"BEN-42": {"name": "Vikram Mehta", "display": "V. Mehta ••4411", "createdAt": time.time() - 240 * 86400},
            "BEN-77": {"name": "City Electricity", "display": "City Electricity", "createdAt": time.time() - 900 * 86400}}


def _load() -> dict:
    if STATE_FILE and STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save() -> None:
    if not STATE_FILE:
        return
    with _lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(CUSTOMERS, f)
        os.replace(tmp, STATE_FILE)


CUSTOMERS: dict[str, dict] = _load()
PENDING: dict[str, dict] = {}
EVENTS: list[dict] = []

app = FastAPI(title="Demo Bank", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers(request: Request, call_next):
    r = await call_next(request)
    origin = os.environ["AMFA_BASE_URL"].rstrip("/")
    r.headers["content-security-policy"] = (
        f"default-src 'self'; script-src 'self'; style-src 'self'; frame-src {origin}; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    r.headers["x-content-type-options"] = "nosniff"
    r.headers["referrer-policy"] = "no-referrer"
    return r


def _sign(v: str) -> str:
    return v + "." + hmac.new(SESSION_SECRET, v.encode(), hashlib.sha256).hexdigest()


def customer(request: Request) -> dict:
    raw = request.cookies.get("bank_session", "")
    v, _, sig = raw.rpartition(".")
    if not v or not hmac.compare_digest(_sign(v), raw) or v not in CUSTOMERS:
        raise HTTPException(401, "sign in to the bank first")
    return CUSTOMERS[v]


USERNAME = r"^[a-z0-9][a-z0-9_-]{2,23}$"


class Register(BaseModel):
    username: str = Field(pattern=USERNAME)
    name: str = Field(min_length=2, max_length=60, pattern=r"^[^\x00-\x1f<>]+$")
    password: str = Field(min_length=8, max_length=128)


class Login(BaseModel):
    username: str = Field(max_length=24)
    password: str = Field(max_length=128)


def _start_session(response: Response, username: str) -> None:
    response.set_cookie("bank_session", _sign(username), httponly=True, samesite="strict",
                        max_age=8 * 3600, secure=os.getenv("BANK_SECURE_COOKIES") == "1")


@app.post("/api/register", status_code=201)
def register(body: Register, response: Response):
    username = body.username.lower()
    with _lock:
        if username in CUSTOMERS:
            raise HTTPException(409, "that username is taken")
        CUSTOMERS[username] = {"ref": f"CUST-{username}", "name": body.name.strip(),
                               "password": _ph.hash(body.password), "balance": OPENING_BALANCE,
                               "amfaUser": None, "payees": _default_payees(), "sentToday": 0,
                               "createdAt": time.time()}
        _save()
    _start_session(response, username)
    return {"signedIn": True}


@app.post("/api/login")
def login(body: Login, response: Response):
    username = body.username.strip().lower()
    c = CUSTOMERS.get(username)
    try:
        _ph.verify(c["password"] if c else _DUMMY_HASH, body.password)
    except (VerifyMismatchError, InvalidHashError):
        c = None
    if c is None:
        raise HTTPException(401, "wrong username or password")
    _start_session(response, username)
    return {"signedIn": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("bank_session")
    return {"signedIn": False}


@app.get("/api/me")
def me(request: Request):
    c = customer(request)
    enrolled = None
    if c["amfaUser"]:
        enrolled = AMFA.request("GET", f"/v1/users/{c['amfaUser']}")["enrollment"]
    return {"name": c["name"], "ref": c["ref"], "balance": c["balance"], "enrolled": enrolled,
            "payees": [{"ref": k, "display": p["display"]} for k, p in c["payees"].items()]}


@app.post("/api/amfa/enrol")
def enrol(request: Request):
    c = customer(request)
    if not c["amfaUser"]:
        c["amfaUser"] = AMFA.create_user(c["ref"])["id"]
        _save()
    return {"url": AMFA.enrollment_link(c["amfaUser"])["url"]}


class Transfer(BaseModel):
    payeeRef: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$")
    amountRupees: float = Field(gt=0, le=1_00_00_000)
    remarks: str = Field(default="", max_length=140)
    deviceId: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    camera: bool = True
    microphone: bool = True
    pointer: bool = False


@app.post("/api/transfers")
def start_transfer(body: Transfer, request: Request):
    c = customer(request)
    if not c["amfaUser"]:
        raise HTTPException(409, "set up secure approvals first")
    payee = c["payees"].get(body.payeeRef)
    if payee is None:
        raise HTTPException(404, "unknown payee")
    amount = round(body.amountRupees * 100)
    tid = f"IMPS{uuid.uuid4().hex[:12].upper()}"
    try:
        txn = AMFA.initiate(
            user_id=c["amfaUser"], external_id=tid, amount_minor=amount, currency="INR",
            payee_ref=body.payeeRef, payee_display=payee["display"], payee_created_at=payee["createdAt"],
            device_id=body.deviceId, ip=request.client.host if request.client else "127.0.0.1",
            capability={"camera": body.camera, "microphone": body.microphone, "pointer": body.pointer},
            idempotency_key=f"bank-{tid}")
    except AmfaError as e:
        raise HTTPException(502, f"authentication service: {e.code}") from None
    PENDING[tid] = {"amfaTxn": txn["transactionId"], "payeeRef": body.payeeRef, "amount": amount,
                    "customer": c, "status": "AWAITING_AUTH"}
    return {"transferId": tid, "plan": txn["plan"], "challengeUrl": txn.get("challengeUrl")}


@app.post("/api/transfers/{tid}/complete")
def complete(tid: str, request: Request):
    c = customer(request)
    t = PENDING.get(tid)
    if t is None or t["customer"] is not c:
        raise HTTPException(404, "no such transfer")
    if t["status"] == "EXECUTED":
        return {"status": "EXECUTED", "balance": c["balance"]}

    status = AMFA.transaction(t["amfaTxn"])["status"]
    if status != "AUTHORIZED":
        t["status"] = f"NOT_AUTHORIZED ({status})"
        return {"status": t["status"]}

    token = AMFA.fetch_proof(t["amfaTxn"])["token"]
    binding = dict(user_ref=c["ref"], external_txn=tid, amount_minor=t["amount"], currency="INR",
                   payee_ref=t["payeeRef"])
    try:
        AMFA.verify_proof_offline(token, tenant_id=TENANT, **binding)
    except AmfaError as e:
        t["status"] = f"PROOF_REJECTED ({e.code})"
        return {"status": t["status"]}

    with _lock:
        if c["balance"] < t["amount"]:
            t["status"] = "INSUFFICIENT_FUNDS"
            return {"status": t["status"]}
        if c["sentToday"] + t["amount"] > DAILY_LIMIT_MINOR:
            t["status"] = "DAILY_LIMIT_EXCEEDED"
            return {"status": t["status"]}
        result = AMFA.verify_proof(token, **binding, consume=True)
        if not result["valid"]:
            t["status"] = f"PROOF_REJECTED ({result['reason']})"
            return {"status": t["status"]}
        c["balance"] -= t["amount"]
        c["sentToday"] += t["amount"]
        t["status"] = "EXECUTED"
        _save()
    return {"status": "EXECUTED", "balance": c["balance"], "reference": tid,
            "authorisedBy": result["claims"]["amr"]}


@app.post("/webhooks/amfa")
async def webhook(request: Request):
    body = await request.body()
    if not WEBHOOK_SECRET or not verify_webhook(WEBHOOK_SECRET, body,
                                                request.headers.get("x-amfa-webhook-timestamp", ""),
                                                request.headers.get("x-amfa-webhook-signature", "")):
        raise HTTPException(401, "bad signature")
    evt = json.loads(body)
    EVENTS.append({"type": evt["type"], "at": evt["createdAt"], "txn": evt["data"].get("externalId")})
    return {"received": True}


@app.get("/api/events")
def events(request: Request):
    customer(request)
    return {"events": EVENTS[-20:]}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/static/config.json")
def page_config():
    return {"amfaBase": os.environ["AMFA_BASE_URL"].rstrip("/"),
            "tenantSlug": os.getenv("AMFA_TENANT_SLUG", "")}


@app.get("/static/{name}")
def static(name: str):
    path = (HERE / "static" / name).resolve()
    if path.parent != (HERE / "static").resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
