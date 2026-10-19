"""Demo Bank: a customer banking app in which every action that moves money or changes the account is
approved by AMFA first. The bank starts an AMFA transaction, the customer completes the checks AMFA asks
for, and the bank only acts after verifying and consuming a signed proof bound to that exact action."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Callable, Literal, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import AfterValidator, BaseModel, Field
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
from amfa_client import AmfaClient, AmfaError, verify_webhook

HERE = Path(__file__).resolve().parent
AMFA = AmfaClient(os.environ["AMFA_BASE_URL"], os.environ["AMFA_KEY_ID"], os.environ["AMFA_KEY_SECRET"])
TENANT = os.environ["AMFA_TENANT_ID"]
WEBHOOK_SECRET = os.getenv("AMFA_WEBHOOK_SECRET", "")
SESSION_SECRET = os.environ["BANK_SESSION_SECRET"].encode()
DEMO_TOOLS = os.getenv("BANK_DEMO_TOOLS", "1") == "1"
STATE_FILE = Path(os.environ["BANK_STATE_FILE"]) if os.getenv("BANK_STATE_FILE") else None

OPENING_BALANCE = 5_00_000_00
DEFAULT_DAILY_LIMIT = 2_00_000_00
MAX_DAILY_LIMIT = 10_00_000_00
BILLERS = {"BILL-ELEC": "City Electricity", "BILL-MOBILE": "Mobile postpaid", "BILL-GAS": "Piped gas"}
BILLERS_SINCE = 1_600_000_000.0   # long-standing billers, not new payees
FINAL = {"DONE", "DECLINED", "EXPIRED", "CANCELLED", "FAILED"}
CHECK_NAMES = {"OTP": "one-time code", "UEBA": "typing check", "FACE": "face check", "VOICE": "voice check"}

_lock = threading.RLock()
_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash(secrets.token_hex(16))


def _load() -> dict:
    data = json.loads(STATE_FILE.read_text()) if STATE_FILE and STATE_FILE.exists() else {}
    for c in data.values():   # accounts saved by an earlier version of this app
        for key, default in (("activity", []), ("devices", []), ("events", {}), ("phone", None), ("email", None),
                             ("dailyLimit", DEFAULT_DAILY_LIMIT), ("sent", {"day": "", "minor": 0})):
            c.setdefault(key, default)
    return data


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
# every action by id, for the webhook and polling; the record itself lives in its customer's activity
ACTIONS: dict[str, tuple[str, dict]] = {a["id"]: (u, a) for u, c in CUSTOMERS.items() for a in c["activity"]}

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


# ---------- sign-in ----------

def _sign(v: str) -> str:
    return v + "." + hmac.new(SESSION_SECRET, v.encode(), hashlib.sha256).hexdigest()


def _username(request: Request) -> str:
    raw = request.cookies.get("bank_session", "")
    v, _, _ = raw.rpartition(".")
    if not v or not hmac.compare_digest(_sign(v), raw) or v not in CUSTOMERS:
        raise HTTPException(401, "sign in to the bank first")
    return v


def customer(request: Request) -> dict:
    return CUSTOMERS[_username(request)]


USERNAME = r"^[a-z0-9][a-z0-9_-]{2,23}$"
DEVICE_ID = r"^[A-Za-z0-9._:-]{8,128}$"
PHONE = r"^\+?[0-9]{10,13}$"
EMAIL = r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$"
NAME = r"^[^\x00-\x1f<>]+$"


class Register(BaseModel):
    username: str = Field(pattern=USERNAME)
    name: str = Field(min_length=2, max_length=60, pattern=NAME)
    password: str = Field(min_length=8, max_length=128)
    phone: str = Field(pattern=PHONE)
    email: str = Field(max_length=254, pattern=EMAIL)
    deviceId: str = Field(pattern=DEVICE_ID)


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
        now = time.time()
        CUSTOMERS[username] = {
            "ref": f"CUST-{username}", "name": body.name.strip(), "password": _ph.hash(body.password),
            "phone": body.phone, "email": body.email.strip().lower(), "balance": OPENING_BALANCE, "amfaUser": None,
            "dailyLimit": DEFAULT_DAILY_LIMIT, "sent": {"day": "", "minor": 0},
            "payees": {"BEN-42": {"name": "Vikram Mehta", "display": "V. Mehta ••4411", "createdAt": now - 240 * 86400}},
            # the device the account was opened on is trusted; any other one must be verified first
            "devices": [body.deviceId], "events": {}, "activity": [], "createdAt": now}
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


def _mask(v: Optional[str]) -> Optional[str]:
    return None if not v else ("••" + v[-4:] if "@" not in v else v[0] + "•••@" + v.split("@", 1)[1])


def sent_today(c: dict) -> int:
    return c["sent"]["minor"] if c["sent"]["day"] == date.today().isoformat() else 0


def initials(display: str) -> str:
    words = re.findall(r"[A-Za-z]+", display)
    return "".join(w[0] for w in words[:2]).upper() or "?"


def code_contact(c: dict) -> dict:
    """Where AMFA sends this customer's one-time codes: their email, or their mobile for accounts
    opened before email was asked for."""
    return {"channel": "email", "to": c["email"]} if c.get("email") else {"channel": "sms", "to": c["phone"]}


def code_label(c: dict) -> str:
    return "Email code" if c.get("email") else "SMS code"


def code_to(c: dict) -> str:
    to = code_contact(c)["to"]
    name, _, dom = to.partition("@")
    return f"{name[:1]}***@{dom}" if dom else f"••{to[-4:]}"


# the ways a customer can confirm it's them; a one-time code (email, SMS or authenticator app) counts once
METHODS = [("code", lambda e: bool(e.get("OTP_DELIVERY") or e.get("OTP_TOTP"))),
           ("Face", lambda e: e.get("FACE") == "ENROLLED"),
           ("Voice", lambda e: e.get("VOICE") == "ENROLLED"),
           ("Typing", lambda e: e.get("UEBA") == "ENROLLED")]


def protection(enrolled: dict, code_name: str = "SMS code") -> dict:
    have = [code_name if name == "code" else name for name, on in METHODS if on(enrolled)]
    can_add = [name for name, on in METHODS[1:] if not on(enrolled)]
    if not enrolled.get("OTP_TOTP"):
        can_add.append("Authenticator app")
    level = "Strong" if len(have) >= 3 else "Good" if len(have) == 2 else "Basic"
    return {"level": level, "have": have, "canAdd": can_add}


def recent_payees(c: dict) -> list[dict]:
    refs: list[str] = []
    for a in c["activity"]:   # newest first
        ref = a.get("payeeRef")
        if a["kind"] == "transfer" and a["status"] == "DONE" and ref in c["payees"] and ref not in refs:
            refs.append(ref)
    return [{"ref": r, "display": c["payees"][r]["display"], "initials": initials(c["payees"][r]["display"])}
            for r in refs[:6]]


def _embed(url: str) -> str:
    """AMFA's pages read embed=1 from the query; the token stays in the #fragment, out of logs."""
    base, _, frag = url.partition("#")
    return base + ("&" if "?" in base else "?") + "embed=1" + ("#" + frag if frag else "")


@app.get("/api/me")
def me(request: Request, deviceId: str = ""):
    c = customer(request)
    enrolled = None
    if c["amfaUser"]:
        enrolled = AMFA.request("GET", f"/v1/users/{c['amfaUser']}")["enrollment"]
    return {"name": c["name"], "ref": c["ref"], "balance": c["balance"], "enrolled": enrolled,
            "protection": protection(enrolled or {}, code_label(c)), "frozen": c.get("frozen", False),
            "codeTo": code_to(c),
            "dailyLimit": c["dailyLimit"], "sentToday": sent_today(c),
            "phone": _mask(c["phone"]), "email": _mask(c["email"]),
            "deviceTrusted": deviceId in c["devices"], "devices": len(c["devices"]), "demoTools": DEMO_TOOLS,
            "payees": [{"ref": k, "display": p["display"], "initials": initials(p["display"]),
                        "riskFlag": p.get("riskFlag", False)} for k, p in c["payees"].items()],
            "recentPayees": recent_payees(c),
            "billers": [{"ref": k, "display": v} for k, v in BILLERS.items()]}


class Enrol(BaseModel):
    factors: list[Literal["FACE", "VOICE", "UEBA", "OTP"]] = Field(default_factory=lambda: ["FACE"],
                                                                     min_length=1, max_length=4)


@app.post("/api/amfa/enrol")
def enrol(request: Request, body: Optional[Enrol] = None):
    """Progressive setup: the first link asks for the face only (SMS codes come from the mobile number);
    the security centre asks for one more way at a time."""
    c = customer(request)
    with _lock:
        if not c["amfaUser"]:
            c["amfaUser"] = AMFA.create_user(c["ref"], contact=code_contact(c))["id"]
            _save()
    factors = (body or Enrol()).factors
    return {"url": _embed(AMFA.enrollment_link(c["amfaUser"], factors=factors)["url"])}


# ---------- protected actions ----------
#
# Each kind says what AMFA is asked to approve (type, amount, payee the proof is bound to), how it reads
# in the activity list, a check before it starts, and the effect applied only after a consumed proof.

class Device(BaseModel):
    deviceId: str = Field(pattern=DEVICE_ID)
    camera: bool = True
    microphone: bool = True
    pointer: bool = False
    touch: bool = False


def _paise(v: float) -> float:
    if round(v, 2) != v:
        raise ValueError("amounts have at most two decimal places")
    return v


class Transfer(Device):
    payeeRef: str = Field(pattern=r"^BEN-[A-Z0-9]{2,12}$")
    amountRupees: Annotated[float, Field(gt=0, le=1_00_00_000), AfterValidator(_paise)]


class Bill(Device):
    billerRef: str = Field(pattern=r"^BILL-[A-Z]{2,8}$")
    amountRupees: Annotated[float, Field(gt=0, le=10_00_000), AfterValidator(_paise)]


class AddPayee(Device):
    name: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z][A-Za-z .'&-]+$")
    account: str = Field(pattern=r"^[0-9]{9,18}$")
    ifsc: str = Field(pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")


class Limit(Device):
    limitRupees: float = Field(ge=1000, le=MAX_DAILY_LIMIT / 100)


class Contact(Device):
    field: str = Field(pattern=r"^(phone|email)$")
    value: str = Field(max_length=254)


class Password(Device):
    current: str = Field(max_length=128)
    new: str = Field(min_length=8, max_length=128)


@dataclass(frozen=True)
class Kind:
    model: type[BaseModel]
    amfa_type: str
    start: Callable[[dict, BaseModel], dict]                # validate, describe; raises HTTPException
    apply: Callable[[dict, dict], Optional[str]]            # the effect; returns a problem or None
    needs_trusted_device: bool = True


def _minor(rupees: float) -> int:
    return round(rupees * 100)


def _check_money(c: dict, amount: int) -> None:
    if amount > c["balance"]:
        raise HTTPException(422, "not enough balance")
    if sent_today(c) + amount > c["dailyLimit"]:
        raise HTTPException(422, "this would go over your daily limit")


def _debit(c: dict, a: dict) -> Optional[str]:
    try:
        _check_money(c, a["amountMinor"])
    except HTTPException as e:
        return e.detail
    c["balance"] -= a["amountMinor"]
    c["sent"] = {"day": date.today().isoformat(), "minor": sent_today(c) + a["amountMinor"]}
    return None


def start_transfer(c: dict, b: Transfer) -> dict:
    p = c["payees"].get(b.payeeRef)
    if p is None:
        raise HTTPException(404, "unknown payee")
    amount = _minor(b.amountRupees)
    _check_money(c, amount)
    return {"title": f"Sent to {p['display']}", "amountMinor": amount, "payeeRef": b.payeeRef,
            "payeeDisplay": p["display"], "payeeCreatedAt": p["createdAt"], "payeeRiskFlag": p.get("riskFlag", False)}


def start_bill(c: dict, b: Bill) -> dict:
    if b.billerRef not in BILLERS:
        raise HTTPException(404, "unknown biller")
    amount = _minor(b.amountRupees)
    _check_money(c, amount)
    return {"title": f"Paid {BILLERS[b.billerRef]}", "amountMinor": amount, "payeeRef": b.billerRef,
            "payeeDisplay": BILLERS[b.billerRef], "payeeCreatedAt": BILLERS_SINCE}


def start_add_payee(c: dict, b: AddPayee) -> dict:
    ref = "BEN-" + hashlib.sha256(f"{b.ifsc}:{b.account}".encode()).hexdigest()[:8].upper()
    if ref in c["payees"]:
        raise HTTPException(409, "this payee is already saved")
    display = f"{b.name.strip()[:24]} ••{b.account[-4:]}"
    return {"title": f"Added payee {display}", "amountMinor": 0, "payeeRef": ref, "payeeDisplay": display,
            "params": {"name": b.name.strip()}}


def apply_add_payee(c: dict, a: dict) -> None:
    c["payees"][a["payeeRef"]] = {"name": a["params"]["name"], "display": a["payeeDisplay"], "createdAt": time.time()}


def start_limit(c: dict, b: Limit) -> dict:
    limit = _minor(b.limitRupees)
    return {"title": f"Daily limit set to ₹{b.limitRupees:,.0f}", "amountMinor": limit, "payeeRef": "LIMIT",
            "payeeDisplay": "Daily limit"}


def start_contact(c: dict, b: Contact) -> dict:
    if not re.match(PHONE if b.field == "phone" else EMAIL, b.value):
        raise HTTPException(422, f"that is not a valid {b.field}")
    return {"title": f"Changed {b.field} to {_mask(b.value)}", "amountMinor": 0,
            "payeeRef": f"PROFILE-{b.field.upper()}", "payeeDisplay": f"New {b.field}",
            "params": {"field": b.field, "value": b.value}}


def apply_contact(c: dict, a: dict) -> None:
    c[a["params"]["field"]] = a["params"]["value"]
    c["events"]["contactChangedAt"] = time.time()
    if c["amfaUser"]:   # one-time codes follow the customer to their new address
        AMFA.request("PATCH", f"/v1/users/{c['amfaUser']}", {"contact": code_contact(c)})


def start_password(c: dict, b: Password) -> dict:
    try:
        _ph.verify(c["password"], b.current)
    except (VerifyMismatchError, InvalidHashError):
        raise HTTPException(422, "your current password is not right") from None
    return {"title": "Changed password", "amountMinor": 0, "payeeRef": "PROFILE-PASSWORD",
            "payeeDisplay": "New password", "params": {"hash": _ph.hash(b.new)}}


def apply_password(c: dict, a: dict) -> None:
    c["password"] = a["params"].pop("hash")
    c["events"]["passwordResetAt"] = time.time()


def start_device(c: dict, b: Device) -> dict:
    if b.deviceId in c["devices"]:
        raise HTTPException(409, "this device is already verified")
    return {"title": "Verified a new device", "amountMinor": 0, "payeeRef": "DEVICE",
            "payeeDisplay": "This device", "params": {"deviceId": b.deviceId}}


KINDS = {
    "transfer": Kind(Transfer, "TRANSFER", start_transfer, _debit),
    "bill": Kind(Bill, "PAYMENT", start_bill, _debit),
    "payee": Kind(AddPayee, "ADD_BENEFICIARY", start_add_payee, apply_add_payee),
    "limit": Kind(Limit, "CHANGE_LIMIT", start_limit, lambda c, a: c.update(dailyLimit=a["amountMinor"])),
    "contact": Kind(Contact, "PROFILE_CHANGE", start_contact, apply_contact),
    "password": Kind(Password, "PROFILE_CHANGE", start_password, apply_password),
    "device": Kind(Device, "DEVICE_BINDING", start_device,
                   lambda c, a: c["devices"].append(a["params"]["deviceId"]), needs_trusted_device=False),
}


def public(a: dict) -> dict:
    """What the customer's app sees: never the risk score, threat level, AMFA's decision or identifiers."""
    out = {k: a.get(k) for k in ("id", "kind", "title", "amountMinor", "status", "detail", "createdAt",
                                 "checks", "reason", "scamWarning", "reviewAfter", "reported")}
    out["payee"] = {"display": a.get("payeeDisplay"), "initials": initials(a.get("payeeDisplay") or "")}
    out["confirmedWith"] = a.get("confirmedWith", [])
    out["canContinue"] = a["status"] == "AWAITING_VERIFICATION" and bool(a.get("challengeUrl"))
    return out


AMR_NAMES = {"otp": "SMS code", "face": "Face", "voice": "Voice", "ueba": "Typing", "review": "Bank review"}


def _decision(plan: dict) -> dict:
    """What AMFA decided, kept for the presenter's "Behind the scenes" drawer only."""
    reasons = [{"feature": k, "text": text, "weight": w}
               for (k, w), text in zip(plan.get("riskReasons") or [], plan.get("riskReasonText") or [])]
    return {"threatLevel": plan.get("threatLevel"), "modelLevel": plan.get("modelLevel"),
            "score": plan.get("riskScore"), "capReason": plan.get("capReason"), "reasons": reasons,
            "checks": plan.get("required") or [], "rulesBaseline": plan.get("rulesBaseline") or [],
            "escalate": plan.get("escalate"), "modelVersion": plan.get("modelVersion")}


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return "127.0.0.1"


def _account_events(c: dict, a: dict) -> dict:
    ev = {k: v for k, v in c["events"].items() if v}
    if a.get("payeeRiskFlag"):
        ev["payeeRiskFlag"] = True
    return ev


@app.post("/api/actions/{kind}", status_code=201)
async def start_action(kind: str, request: Request):
    k = KINDS.get(kind)
    if k is None:
        raise HTTPException(404, "unknown action")
    username = _username(request)
    try:
        body = k.model.model_validate(await request.json())
    except ValueError:
        raise HTTPException(422, "please check the details and try again") from None
    return await run_in_threadpool(_start, k, kind, username, body, request)


def _start(k: Kind, kind: str, username: str, body: Device, request: Request) -> dict:
    c = CUSTOMERS[username]
    if c.get("frozen"):
        raise HTTPException(423, "your account is frozen; call us to unfreeze it")
    if not c["amfaUser"]:
        raise HTTPException(409, "set up secure approvals first")
    if k.needs_trusted_device and body.deviceId not in c["devices"]:
        raise HTTPException(403, "verify this device before using it for banking")

    with _lock:
        a = {"id": f"ACT{uuid.uuid4().hex[:12].upper()}", "kind": kind, "status": "AWAITING_VERIFICATION",
             "createdAt": time.time(), "detail": None, "reported": False, "params": {}, **k.start(c, body)}
    try:
        txn = AMFA.initiate(
            user_id=c["amfaUser"], external_id=a["id"], type=k.amfa_type, amount_minor=a["amountMinor"],
            currency="INR", payee_ref=a["payeeRef"], payee_display=a["payeeDisplay"],
            payee_created_at=a.get("payeeCreatedAt"), device_id=body.deviceId,
            ip=_client_ip(request),
            capability={"camera": body.camera, "microphone": body.microphone, "pointer": body.pointer,
                        "touch": body.touch},
            account_events=_account_events(c, a) or None, idempotency_key=f"bank-{a['id']}")
    except AmfaError as e:
        if e.code == "USER_NOT_ACTIVE":
            raise HTTPException(423, "secure approvals are locked on this account; please contact the bank") from None
        raise HTTPException(502, "the approval service is not available right now") from None

    notice = txn["plan"].get("customerNotice") or {}
    a.update(amfaTxn=txn["transactionId"], checks=[CHECK_NAMES.get(f, f) for f in txn["plan"]["required"]],
             reason=notice.get("reason"), scamWarning=bool(notice.get("scamWarning")),
             reviewAfter=bool(notice.get("underReview")),
             challengeUrl=_embed(txn["challengeUrl"]) if txn.get("challengeUrl") else None,
             decision=_decision(txn["plan"]))
    if txn["status"] == "REVIEW":
        a["status"] = "UNDER_REVIEW"
    with _lock:
        c["activity"].insert(0, a)
        del c["activity"][100:]
        ACTIONS[a["id"]] = (username, a)
        _save()
    return {"action": public(a), "challengeUrl": a["challengeUrl"]}


def settle(c: dict, a: dict) -> dict:
    """Bring an action up to date with AMFA. The effect is applied once, only after the proof is verified
    against this action (signature, amount, payee, type) and consumed so it can never be replayed."""
    if a["status"] in FINAL:
        return a
    status = AMFA.transaction(a["amfaTxn"])["status"]
    outcome = {"PENDING_AUTH": "AWAITING_VERIFICATION", "REVIEW": "UNDER_REVIEW", "DENIED": "DECLINED",
               "EXPIRED": "EXPIRED", "CANCELLED": "CANCELLED"}.get(status)
    if outcome:
        a["status"] = outcome
        return a
    if status != "AUTHORIZED":
        a["status"], a["detail"] = "FAILED", f"unexpected state {status}"
        return a

    binding = dict(user_ref=c["ref"], external_txn=a["id"], amount_minor=a["amountMinor"], currency="INR",
                   payee_ref=a["payeeRef"], type=KINDS[a["kind"]].amfa_type)
    with _lock:
        if a["status"] in FINAL:   # settled by a concurrent call while we were asking AMFA
            return a
        try:
            token = AMFA.fetch_proof(a["amfaTxn"])["token"]
            AMFA.verify_proof_offline(token, tenant_id=TENANT, **binding)
        except AmfaError as e:
            a["status"], a["detail"] = "FAILED", f"approval could not be verified ({e.code})"
            return a
        result = AMFA.verify_proof(token, **binding, consume=True)
        if not result["valid"]:
            a["status"], a["detail"] = "FAILED", f"approval could not be verified ({result['reason']})"
            return a
        problem = KINDS[a["kind"]].apply(c, a)
        a["status"], a["detail"] = ("FAILED", problem) if problem else ("DONE", None)
        names = {**AMR_NAMES, "otp": code_label(c)}
        a["confirmedWith"] = [names.get(m, m) for m in result["claims"].get("amr", [])]
        _save()
    return a


def _own(request: Request, action_id: str) -> tuple[dict, dict]:
    username = _username(request)
    hit = ACTIONS.get(action_id)
    if hit is None or hit[0] != username:
        raise HTTPException(404, "no such action")
    return CUSTOMERS[username], hit[1]


@app.post("/api/actions/{action_id}/complete")
def complete(action_id: str, request: Request):
    c, a = _own(request, action_id)
    return {"action": public(settle(c, a)), "balance": c["balance"]}


@app.post("/api/actions/{action_id}/continue")
def continue_action(action_id: str, request: Request):
    """Back into the same AMFA session after the customer closed the verification screen."""
    c, a = _own(request, action_id)
    settle(c, a)
    if a["status"] != "AWAITING_VERIFICATION" or not a.get("challengeUrl"):
        raise HTTPException(409, "this request can't be continued; start it again")
    return {"challengeUrl": a["challengeUrl"]}


@app.post("/api/account/freeze")
def freeze(request: Request):
    """The customer's panic button: nothing can be started until the bank unfreezes the account."""
    c = customer(request)
    if c["amfaUser"]:
        AMFA.request("PATCH", f"/v1/users/{c['amfaUser']}", {"status": "SUSPENDED"})
    c["frozen"] = True
    _save()
    return {"frozen": True}


@app.post("/api/actions/{action_id}/cancel")
def cancel(action_id: str, request: Request):
    c, a = _own(request, action_id)
    if a["status"] in ("AWAITING_VERIFICATION", "UNDER_REVIEW"):
        try:
            AMFA.cancel(a["amfaTxn"])
            a["status"] = "CANCELLED"
        except AmfaError:
            settle(c, a)   # it moved on (approved or declined) before the cancel arrived
        _save()
    return {"action": public(a)}


class Report(BaseModel):
    reason: str = Field(default="", max_length=200, pattern=r"^[^\x00-\x1f<>]*$")


@app.post("/api/actions/{action_id}/report")
def report(action_id: str, body: Report, request: Request):
    """"This wasn't me": the bank tells AMFA, which records a fraud label for the risk model."""
    c, a = _own(request, action_id)
    if a["status"] != "DONE":
        raise HTTPException(409, "only completed actions can be reported")
    if not a["reported"]:
        AMFA.feedback(a["amfaTxn"], "FRAUD", "customer_report", body.reason or None)
        a["reported"] = True
        _save()
    return {"action": public(a)}


@app.get("/api/activity")
def activity(request: Request):
    c = customer(request)
    for a in c["activity"][:10]:   # refresh recent unfinished ones, e.g. a review an operator just decided
        if a["status"] not in FINAL:
            try:
                settle(c, a)
            except AmfaError:
                pass
    return {"activity": [public(a) for a in c["activity"]], "balance": c["balance"]}


@app.post("/webhooks/amfa")
async def webhook(request: Request):
    body = await request.body()
    if not WEBHOOK_SECRET or not verify_webhook(WEBHOOK_SECRET, body,
                                                request.headers.get("x-amfa-webhook-timestamp", ""),
                                                request.headers.get("x-amfa-webhook-signature", "")):
        raise HTTPException(401, "bad signature")
    evt = json.loads(body)
    hit = ACTIONS.get((evt.get("data") or {}).get("externalId", ""))
    if hit is not None:
        settle(CUSTOMERS[hit[0]], hit[1])
    return {"received": True}


# ---------- demo tools: stage the signals a real bank would learn from its own systems ----------

class PayeeFlag(BaseModel):
    payeeRef: str = Field(pattern=r"^BEN-[A-Z0-9]{2,12}$")
    flagged: bool


def _demo(request: Request) -> dict:
    if not DEMO_TOOLS:
        raise HTTPException(404)
    return customer(request)


@app.post("/api/demo/sim-swap")
def demo_sim_swap(request: Request):
    """As if the telecom operator reported a SIM change on the customer's number just now."""
    c = _demo(request)
    c["events"]["simChangedAt"] = time.time()
    _save()
    return {"simChangedAt": c["events"]["simChangedAt"]}


@app.post("/api/demo/payee-risk")
def demo_payee_risk(body: PayeeFlag, request: Request):
    """As if a mule-account list (for example the bank's own, or a national one) flagged this payee."""
    c = _demo(request)
    if body.payeeRef not in c["payees"]:
        raise HTTPException(404, "unknown payee")
    c["payees"][body.payeeRef]["riskFlag"] = body.flagged
    _save()
    return {"payeeRef": body.payeeRef, "flagged": body.flagged}


@app.post("/api/demo/unfreeze")
def demo_unfreeze(request: Request):
    """What the bank's support desk does after checking with the customer."""
    c = _demo(request)
    if c["amfaUser"]:
        AMFA.request("PATCH", f"/v1/users/{c['amfaUser']}", {"status": "ACTIVE"})
    c["frozen"] = False
    _save()
    return {"frozen": False}


@app.get("/api/demo/last-decision")
def demo_last_decision(request: Request):
    """AMFA's decision on the customer's latest action, for the presenter's drawer."""
    c = _demo(request)
    a = next((a for a in c["activity"] if a.get("decision")), None)
    return {"decision": a and {**a["decision"], "title": a["title"], "status": a["status"]}}


@app.post("/api/demo/reset")
def demo_reset(request: Request):
    c = _demo(request)
    c["events"].pop("simChangedAt", None)
    for p in c["payees"].values():
        p.pop("riskFlag", None)
    _save()
    return {"reset": True}


# ---------- pages ----------

@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/assets/config.json")
def page_config():
    return {"amfaBase": os.environ["AMFA_BASE_URL"].rstrip("/"),
            "tenantSlug": os.getenv("AMFA_TENANT_SLUG", "")}


STATIC = (HERE / "static").resolve()
STATIC_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
                ".json": "application/json"}


@app.get("/assets/{name:path}")
def static(name: str):
    path = (STATIC / name).resolve()
    if not path.is_relative_to(STATIC) or not path.is_file() or path.suffix not in STATIC_TYPES:
        raise HTTPException(404)
    return FileResponse(path, media_type=STATIC_TYPES[path.suffix])
