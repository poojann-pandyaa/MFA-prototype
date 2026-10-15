from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from amfa.common import crypto
from amfa.common.contract import (FAILED, INSUFFICIENT_DATA, PASSED, SPOOF_DETECTED, UNAVAILABLE)
from amfa.common.web import ApiError
from amfa.core import audit, db, factors, otp, proofs, webhooks
from amfa.engine.agent import reassess
from amfa.engine.availability import available
from amfa.engine.context import Band, Context, Factor
from amfa.engine.policy import TenantPolicy

log = logging.getLogger("amfa.orchestrator")

PENDING, VERIFIED, BLOCKED, EXPIRED, REVIEW = "PENDING", "VERIFIED", "BLOCKED", "EXPIRED", "MANUAL_REVIEW"
TERMINAL_SESSION = {VERIFIED, BLOCKED, EXPIRED, REVIEW}
CHALLENGE_TTL_S = 120
MAX_OTP_SENDS = 3
PAD_FLAGS_BEFORE_BLOCK = 2
VOICE_WORDS = ["river", "maple", "orbit", "violet", "copper", "harbor", "falcon", "meadow",
               "silver", "canyon", "lantern", "summit", "willow", "ember", "island", "garden"]


def settle(checklist: list[dict]) -> str:
    statuses = [i["status"] for i in checklist]
    if any(s == SPOOF_DETECTED for s in statuses):
        return BLOCKED
    if any(s == FAILED for s in statuses):
        return BLOCKED
    if any(s == "PENDING" for s in statuses):
        return PENDING
    for item in checklist:
        if item["status"] == PASSED:
            continue
        if item["status"] in (INSUFFICIENT_DATA, UNAVAILABLE):
            sub = next((i for i in checklist if i.get("substitutes") == item["factor"]), None)
            if sub is not None and sub["status"] == PASSED:
                continue
            return REVIEW
        return BLOCKED
    return VERIFIED


def next_item(checklist: list[dict]) -> Optional[dict]:
    return next((i for i in checklist if i["status"] == "PENDING"), None)


def new_checklist(sequence: list[Factor]) -> list[dict]:
    return [{"factor": f.value, "status": "PENDING", "attempts": 0, "result": None,
             "addedBy": "plan"} for f in sequence]


def create_session(s, *, tenant: db.Tenant, txn: db.Transaction, customer: db.Customer,
                   ctx: Context, plan, policy: TenantPolicy) -> tuple[db.AuthSession, str]:
    ttl = min(policy.sessionTtlSeconds, _cfg().session_ttl_max_s)
    sess = db.AuthSession(
        id=f"sess_{uuid.uuid4().hex[:20]}", tenant_id=tenant.id, transaction_id=txn.id,
        customer_id=customer.id, plan_id=plan.planId, status=PENDING,
        checklist=new_checklist(plan.sequence()), escalations=[],
        context=ctx.model_dump(mode="json"), risk_band=plan.riskBand.value,
        expires_at=time.time() + ttl)
    if plan.escalate == "MANUAL_REVIEW":
        sess.status, sess.resolution = REVIEW, "PLAN_NOT_ASSEMBLABLE"
        for item in sess.checklist:
            item["status"] = "NOT_ATTEMPTED"
    s.add(sess)
    s.flush()
    client_token = crypto.token("ct", 32)
    s.add(db.ClientToken(id=crypto.hash_token(client_token), kind="SESSION", tenant_id=tenant.id,
                         customer_id=customer.id, session_id=sess.id, expires_at=sess.expires_at))
    if sess.status == REVIEW:
        _open_review(s, sess, txn, plan.escalateReason or "plan could not be assembled")
    return sess, client_token


def _cfg():
    from amfa import config
    return config.get()


def _ctx(sess: db.AuthSession) -> Context:
    return Context.model_validate(sess.context)


def issue_challenge(s, sess: db.AuthSession, customer: db.Customer, txn: db.Transaction,
                    tenant: db.Tenant) -> Optional[dict]:
    item = next_item(sess.checklist)
    if item is None or sess.status != PENDING:
        return None
    factor = item["factor"]
    now = time.time()
    live = s.execute(select(db.Challenge).where(
        db.Challenge.session_id == sess.id, db.Challenge.factor == factor,
        db.Challenge.consumed_at.is_(None), db.Challenge.expires_at > now + 10)).scalars().first()
    if live is not None:
        return _public_challenge(live)

    params: dict = {}
    if factor == "OTP":
        cred = s.get(db.TotpCredential, customer.id)
        if cred is not None and cred.confirmed:
            params = {"mode": "totp", "digits": otp.DIGITS}
        else:
            sends = s.execute(select(func.count()).select_from(db.OtpCode).where(
                db.OtpCode.session_id == sess.id)).scalar_one()
            if sends >= MAX_OTP_SENDS:
                raise ApiError(429, "OTP_SEND_LIMIT", "too many codes requested for this session")
            if not customer.contact_sealed:
                raise ApiError(409, "NO_OTP_CHANNEL", "no authenticator or contact enrolled")
            import json
            contact = json.loads(crypto.open_(customer.contact_sealed, f"contact|{customer.id}", "contact"))
            code = otp.new_code()
            channel = otp.deliver(contact, otp.message_for(
                code, format_amount(txn.amount_minor, txn.currency),
                txn.payee_display or mask_ref(txn.payee_ref), tenant.name))
            s.add(db.OtpCode(id=f"otp_{uuid.uuid4().hex[:16]}", session_id=sess.id,
                             code_hash=otp.code_hash(sess.id, code), channel=channel,
                             expires_at=now + otp.DELIVERED_TTL_S))
            params = {"mode": "delivered", "sentTo": otp.mask_contact(contact.get("to", "")),
                      "digits": otp.DIGITS}
    elif factor == "FACE":
        params = {"action": secrets.choice(["TURN_LEFT", "TURN_RIGHT"]), "frames": 12,
                  "intervalMs": 150}
    elif factor == "VOICE":
        words = [secrets.choice(VOICE_WORDS) for _ in range(3)]
        digits = " ".join(str(secrets.randbelow(10)) for _ in range(4))
        params = {"prompt": f"{' '.join(words)} {digits}", "minSeconds": 3, "maxSeconds": 10}
    elif factor == "UEBA":
        phrase = factors.ueba_phrase() or {}
        ctx = _ctx(sess)
        params = {"phrase": phrase.get("phrase"),
                  "shape": phrase.get("shape") if (ctx.capability.pointer and ctx.enrollment.shape) else None}
    ch = db.Challenge(id=f"ch_{uuid.uuid4().hex[:20]}", session_id=sess.id, factor=factor,
                      params=params, expires_at=now + CHALLENGE_TTL_S)
    s.add(ch)
    s.flush()
    return _public_challenge(ch)


def _public_challenge(ch: db.Challenge) -> dict:
    return {"challengeId": ch.id, "factor": ch.factor, "params": ch.params,
            "expiresAt": int(ch.expires_at)}


_DIAG_KEYS = {"FACE": {"action", "yaws", "liveFrames", "frontalFrames", "turnDelta", "inBetweenFrames",
                       "turnedFrame", "turnedIdentity", "burstIdentity", "needFrontalMax",
                       "needTurnDelta", "frameLiveness"}}


def attempt_diagnostics(factor: str, res: dict) -> Optional[dict]:
    active = (res.get("detail") or {}).get("active")
    allowed = _DIAG_KEYS.get(factor)
    if not allowed or not isinstance(active, dict):
        return None

    def clean(v):
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return round(float(v), 4)
        if isinstance(v, str) and v in ("TURN_LEFT", "TURN_RIGHT"):
            return v
        if isinstance(v, list) and len(v) <= 16:
            items = [clean(x) for x in v]
            return items if all(isinstance(x, float) for x in items) else None
        return None

    out = {k: clean(v) for k, v in active.items() if k in allowed}
    return {k: v for k, v in out.items() if v is not None} or None


def submit(s, *, sess: db.AuthSession, customer: db.Customer, txn: db.Transaction,
           tenant: db.Tenant, factor: str, challenge_id: str, payload: dict) -> dict:
    policy = TenantPolicy.model_validate(tenant.policy)
    now = time.time()
    if sess.status != PENDING:
        raise ApiError(409, "SESSION_NOT_PENDING", f"session is {sess.status}")
    if now > sess.expires_at:
        _expire(s, sess, txn)
        s.commit()
        raise ApiError(409, "SESSION_EXPIRED", "session expired")
    item = next_item(sess.checklist)
    if item is None or item["factor"] != factor:
        raise ApiError(409, "OUT_OF_ORDER", "that factor is not the next one required")

    ch = s.get(db.Challenge, challenge_id)
    if (ch is None or ch.session_id != sess.id or ch.factor != factor or ch.consumed_at is not None
            or ch.expires_at < now):
        raise ApiError(409, "CHALLENGE_INVALID", "challenge missing, expired or already used")
    ch.consumed_at = now

    ctx = _ctx(sess)
    signals: list[str] = []
    device_unproven = False
    if not sess.device_checked:
        sess.device_checked = True
        if not sess.device_bound and ctx.device.deviceKnown and _has_device_key(s, customer, txn):
            device_unproven = True
            _event(s, sess, "MEDIUM", "DEVICE_KEY_UNPROVEN",
                   "transaction claimed a trusted device that did not prove possession of its key")
            listed = [i["factor"] for i in sess.checklist]
            extra = next((f for f in (Factor.FACE, Factor.VOICE, Factor.UEBA)
                          if f.value not in listed and available(f, ctx)), None)
            if extra is None:
                for other in sess.checklist:
                    if other["status"] == "PENDING":
                        other["status"] = "NOT_ATTEMPTED"
                sess.checklist = [dict(i) for i in sess.checklist]
                sess.status, sess.resolution, sess.resolved_at = REVIEW, "DEVICE_KEY_UNPROVEN", now
                txn.status = "REVIEW"
                _open_review(s, sess, txn, "trusted device did not prove possession of its key; "
                                           "no further factor available")
                return {"result": {"factor": factor, "status": "NOT_ATTEMPTED", "attempts": 0,
                                   "retry": False, "guidance": None}, "escalation": None}
            sess.checklist = [*[dict(i) for i in sess.checklist],
                              {"factor": extra.value, "status": "PENDING", "attempts": 0, "result": None,
                               "addedBy": "escalation"}]
            sess.escalations = [*sess.escalations, {"appended": [extra.value],
                                                    "reason": "trusted device did not prove possession of its registered key"}]

    if factor == "OTP":
        res = _verify_otp(s, sess, customer, ch, payload)
    else:
        pad_mode = policy.padModes.get(factor, "advisory") if factor in ("FACE", "VOICE") else "advisory"
        res = factors.verify(factor, tenant=tenant.id, user=customer.id, session=sess.id,
                             txn=txn.id, challenge=ch.params, payload=payload,
                             pad_mode=pad_mode).as_dict()

    s.add(db.Attempt(session_id=sess.id, factor=factor, status=res["status"], score=res.get("score"),
                     threshold=res.get("threshold"), liveness_score=res.get("livenessScore"),
                     pad_verdict=(res.get("pad") or {}).get("verdict"), reason=res.get("failureReason"),
                     model_version=(res.get("modelVersion") or "")[:80], latency_ms=res.get("latencyMs", 0),
                     detail=attempt_diagnostics(factor, res)))

    checklist = [dict(i) for i in sess.checklist]
    item = next(i for i in checklist if i["status"] == "PENDING" and i["factor"] == factor)
    item["attempts"] += 1
    item["result"] = {k: res.get(k) for k in ("status", "score", "threshold", "livenessScore",
                                              "failureReason", "modelVersion", "latencyMs")}
    status = res["status"]
    budget = policy.attemptBudget.get(factor, 2)
    escalation = None

    pad = res.get("pad") or {}
    pad_flag = pad.get("ran") and pad.get("verdict") == "SPOOF" and pad.get("mode") == "advisory"
    prompt = (res.get("detail") or {}).get("prompt") or {}
    prompt_flag = prompt.get("spoken") is False and prompt.get("mode") == "advisory"
    if pad_flag or prompt_flag:
        sess.pad_flags += 1
        what = " and ".join(x for x, on in (("presentation-attack verdict", pad_flag),
                                            ("prompt not spoken", prompt_flag)) if on)
        _event(s, sess, "MEDIUM", "LIVENESS_ADVISORY", f"{factor} {what} (advisory)")
        signals.append(f"{factor} {what} (advisory)")
        if sess.pad_flags >= PAD_FLAGS_BEFORE_BLOCK:
            status = SPOOF_DETECTED
            item["result"]["failureReason"] = "REPEATED_PAD_ADVISORIES"

    if status == PASSED:
        item["status"] = PASSED
        done = [i["result"] | {"factor": i["factor"]} for i in checklist if i["result"] and i["status"] == PASSED]
        required = [Factor(i["factor"]) for i in checklist]
        appended, why = reassess(ctx, required, Band(sess.risk_band), done, signals)
        escalation = _append(checklist, appended, why, "escalation")
    elif status == FAILED:
        if item["attempts"] < budget:
            item["status"] = "PENDING"
        else:
            item["status"] = FAILED
    elif status == SPOOF_DETECTED:
        item["status"] = SPOOF_DETECTED
        for other in checklist:
            if other["status"] == "PENDING":
                other["status"] = "NOT_ATTEMPTED"
    elif status in (INSUFFICIENT_DATA, UNAVAILABLE):
        item["status"] = status
        if status == UNAVAILABLE:
            _event(s, sess, "MEDIUM", "FACTOR_UNAVAILABLE",
                   f"{factor} service unavailable ({res.get('failureReason')}); substituting or referring")
        subs = [f for f in (Factor.FACE, Factor.VOICE, Factor.UEBA)
                if f.value not in [i["factor"] for i in checklist] and available(f, ctx)]
        if subs:
            checklist.append({"factor": subs[0].value, "status": "PENDING", "attempts": 0,
                              "result": None, "addedBy": "substitution", "substitutes": factor})
            escalation = {"appended": [subs[0].value], "reason": f"{factor} {status.lower()}: substituted"}
            if Band(sess.risk_band) is Band.HIGH and len(subs) > 1:
                checklist.append({"factor": subs[1].value, "status": "PENDING", "attempts": 0,
                                  "result": None, "addedBy": "escalation"})
                escalation["appended"].append(subs[1].value)
                escalation["reason"] += "; escalated at HIGH band"

    if escalation:
        sess.escalations = [*sess.escalations, escalation]
    sess.checklist = checklist

    verdict = settle(checklist)
    outcome = {"result": _client_result(res, item), "escalation": escalation}
    if verdict != PENDING:
        _resolve(s, sess, txn, customer, tenant, policy, verdict)
    try:
        s.flush()
    except StaleDataError:
        raise ApiError(409, "CONCURRENT_SUBMISSION", "another submission changed this session") from None
    return outcome


def _append(checklist: list[dict], appended: list[Factor], why: str, by: str) -> Optional[dict]:
    new = [f.value for f in appended if f.value not in [i["factor"] for i in checklist]]
    for f in new:
        checklist.append({"factor": f, "status": "PENDING", "attempts": 0, "result": None, "addedBy": by})
    return {"appended": new, "reason": why} if new else None


# Once a single face is in view, every face failure (turn, liveness, identity) gets the same
# words, so a failed attempt never tells the client which check its frames got past.
FACE_UNVERIFIED = ("We couldn't verify you. Look straight at the camera, then when prompted turn "
                   "your head slowly about a quarter turn in the direction shown and hold it.")
FACE_UNIFORM_REASONS = {"HEAD_TURN_NOT_OBSERVED", "HEAD_TURN_NOT_CONTINUOUS", "NO_FRONTAL_FRAME",
                        "NOT_ENOUGH_LIVE_FRAMES", "IDENTITY_CHANGED_DURING_TURN", "FACE_MISMATCH"}


def _client_result(res: dict, item: dict) -> dict:
    reason = res.get("failureReason")
    if item["factor"] == "FACE" and reason in FACE_UNIFORM_REASONS:
        reason = "FACE_UNVERIFIED"
    guidance = {
        "NO_FACE_DETECTED": "We could not see your face. Face the camera in good light.",
        "MULTIPLE_FACES": "Only you should be in view.",
        "FACE_TOO_SMALL": "Move closer to the camera.",
        "FACE_UNVERIFIED": FACE_UNVERIFIED,
        "NOT_ENOUGH_SPEECH": "Please read the whole phrase clearly.",
        "TOO_NOISY": "Find a quieter place and try again.",
        "AUDIO_CLIPPED": "Hold the phone a little further away.",
        "CHALLENGE_INCOMPLETE": "Please type the whole phrase and trace the whole line, from the green dot to the red dot.",
        "CHALLENGE_MISSING": "Please type the phrase and trace the line as shown.",
        "OTP_INVALID": "That code is not valid. Check it and try again.",
        "OTP_EXPIRED": "That code has expired. Request a new one.",
    }.get(reason or "")
    return {"factor": item["factor"], "status": item["status"], "attempts": item["attempts"],
            "retry": item["status"] == "PENDING", "guidance": guidance}


def _verify_otp(s, sess: db.AuthSession, customer: db.Customer, ch: db.Challenge, payload: dict) -> dict:
    code = str(payload.get("code", "")).strip()
    base = {"factor": "OTP", "threshold": 1.0, "modelVersion": "rfc6238-totp" if ch.params.get("mode") == "totp"
            else "delivered-otp", "latencyMs": 0, "source": "live"}
    if ch.params.get("mode") == "totp":
        cred = s.get(db.TotpCredential, customer.id)
        secret = crypto.open_(cred.secret_sealed, f"totp|{customer.id}", "totp").decode()
        step = otp.verify_totp(secret, code, cred.last_step)
        if step is None:
            return {**base, "status": FAILED, "score": 0.0, "failureReason": "OTP_INVALID"}
        cred.last_step = step
        return {**base, "status": PASSED, "score": 1.0}
    now = time.time()
    row = s.execute(select(db.OtpCode).where(db.OtpCode.session_id == sess.id,
                                             db.OtpCode.consumed_at.is_(None))
                    .order_by(db.OtpCode.created_at.desc())).scalars().first()
    if row is None or row.expires_at < now:
        return {**base, "status": FAILED, "score": 0.0, "failureReason": "OTP_EXPIRED"}
    if not code.isdigit() or not crypto.consteq(row.code_hash, otp.code_hash(sess.id, code)):
        return {**base, "status": FAILED, "score": 0.0, "failureReason": "OTP_INVALID"}
    row.consumed_at = now
    return {**base, "status": PASSED, "score": 1.0}


def _has_device_key(s, customer: db.Customer, txn: db.Transaction) -> bool:
    return s.execute(select(func.count()).select_from(db.Device).where(
        db.Device.customer_id == customer.id, db.Device.device_key == (txn.facts or {}).get("deviceKey"),
        db.Device.public_jwk.is_not(None))).scalar_one() > 0


def _resolve(s, sess, txn, customer, tenant, policy: TenantPolicy, verdict: str) -> None:
    now = time.time()
    sess.status, sess.resolved_at = verdict, now
    passed = [i["factor"] for i in sess.checklist if i["status"] == PASSED]
    if verdict == VERIFIED:
        txn.status, txn.updated_at = "AUTHORIZED", now
        proof = proofs.issue(tenant_id=tenant.id, customer_ref=customer.external_ref, txn=txn,
                             session_id=sess.id, plan_id=sess.plan_id, factors=passed,
                             risk_band=sess.risk_band, floor=_ctx(sess).policy.actionFloor.value,
                             ttl_s=policy.proofTtlSeconds, s=s)
        _learn(s, sess, txn, customer, tenant)
        webhooks.emit(s, tenant.id, "transaction.authorized", {
            "transactionId": txn.id, "externalId": txn.external_id, "sessionId": sess.id,
            "proof": proof["token"], "factors": passed, "expiresAt": proof["expiresAt"]})
    elif verdict == BLOCKED:
        txn.status, txn.updated_at = "DENIED", now
        spoof = any(i["status"] == SPOOF_DETECTED for i in sess.checklist)
        sess.resolution = "PRESENTATION_ATTACK" if spoof else "FACTOR_FAILED"
        if spoof:
            customer.status, customer.locked_reason = "LOCKED", "PRESENTATION_ATTACK"
            _event(s, sess, "HIGH", "PRESENTATION_ATTACK",
                   "presentation attack or replay detected; session and customer locked")
            _open_review(s, sess, txn, "presentation attack detected; customer locked pending review")
        webhooks.emit(s, tenant.id, "transaction.denied", {
            "transactionId": txn.id, "externalId": txn.external_id, "sessionId": sess.id,
            "reason": sess.resolution})
    elif verdict == REVIEW:
        txn.status, txn.updated_at = "REVIEW", now
        sess.resolution = "UNMET_WITHOUT_SUBSTITUTE"
        _open_review(s, sess, txn, "a required factor could not be assessed and nothing could stand in")


def _learn(s, sess, txn, customer, tenant) -> None:
    now = time.time()
    f = txn.facts or {}
    dev = s.execute(select(db.Device).where(db.Device.customer_id == customer.id,
                                            db.Device.device_key == f.get("deviceKey"))).scalars().first()
    if dev is None:
        s.add(db.Device(customer_id=customer.id, device_key=f.get("deviceKey"), verified_count=1))
    else:
        dev.verified_count += 1
        dev.last_seen = now
    net = s.execute(select(db.KnownNetwork).where(db.KnownNetwork.customer_id == customer.id,
                                                  db.KnownNetwork.network == f.get("network"))).scalars().first()
    if net is None:
        s.add(db.KnownNetwork(customer_id=customer.id, network=f.get("network"),
                              place_key=f.get("placeKey"), count=1))
    else:
        net.count += 1
        net.last_seen = now
    if txn.type in ("TRANSFER", "PAYMENT", "ADD_BENEFICIARY"):
        kp = s.execute(select(db.KnownPayee).where(db.KnownPayee.customer_id == customer.id,
                                                   db.KnownPayee.payee_key == f.get("payeeKey"))).scalars().first()
        if kp is None:
            s.add(db.KnownPayee(customer_id=customer.id, payee_key=f.get("payeeKey"), count=1))
        else:
            kp.count += 1
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(TenantPolicy.model_validate(tenant.policy).timezone)
    hour = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(tz).hour
    hist = list(customer.hour_histogram or [0] * 24)
    hist[hour] += 1
    customer.hour_histogram = hist
    customer.last_verified_at, customer.verified_count = now, customer.verified_count + 1
    body = {k: f[k] for k in ("tenantId", "userId", "action", "amount", "payeeKey", "deviceKey",
                              "locationKey", "channel", "occurredAt") if k in f}
    if f.get("session"):
        body["session"] = f["session"]
    if factors.ueba("/events/record", body) is None:
        log.warning("behavioural history not recorded for %s (service unavailable)", txn.id)


def _expire(s, sess, txn) -> None:
    sess.status, sess.resolved_at, sess.resolution = EXPIRED, time.time(), "TIMEOUT"
    txn.status, txn.updated_at = "EXPIRED", time.time()
    webhooks.emit(s, sess.tenant_id, "transaction.expired",
                  {"transactionId": txn.id, "externalId": txn.external_id, "sessionId": sess.id})


def expire_stale(s) -> int:
    now = time.time()
    n = 0
    for sess in s.execute(select(db.AuthSession).where(db.AuthSession.status == PENDING,
                                                       db.AuthSession.expires_at < now).limit(200)).scalars():
        txn = s.get(db.Transaction, sess.transaction_id)
        _expire(s, sess, txn)
        n += 1
    return n


def _open_review(s, sess, txn, reason: str) -> None:
    if s.execute(select(db.ReviewCase).where(db.ReviewCase.session_id == sess.id)).scalars().first():
        return
    s.add(db.ReviewCase(id=f"rv_{uuid.uuid4().hex[:16]}", tenant_id=sess.tenant_id, session_id=sess.id,
                        transaction_id=txn.id, customer_id=sess.customer_id, reason=reason[:300]))
    txn.status = "REVIEW" if txn.status == "PENDING_AUTH" else txn.status
    _event(s, sess, "MEDIUM", "MANUAL_REVIEW", reason[:300])
    webhooks.emit(s, sess.tenant_id, "transaction.review_required",
                  {"transactionId": txn.id, "externalId": txn.external_id, "sessionId": sess.id,
                   "reason": reason[:300]})


def _event(s, sess, severity: str, kind: str, message: str) -> None:
    s.add(db.SecurityEvent(tenant_id=sess.tenant_id, customer_id=sess.customer_id, session_id=sess.id,
                           severity=severity, kind=kind, message=message[:300]))
    if severity == "HIGH":
        webhooks.emit(s, sess.tenant_id, "security.event",
                      {"kind": kind, "sessionId": sess.id, "message": message[:300]})


def device_nonce(sess_id: str) -> str:
    import hashlib
    import hmac
    return crypto.b64e(hmac.new(crypto.derive_key("device-nonce"), sess_id.encode(),
                                hashlib.sha256).digest()[:18])


def bind_device(s, sess: db.AuthSession, customer: db.Customer, device_key_: str, *,
                signature: Optional[str] = None, assertion: Optional[str] = None,
                integrity_token: Optional[str] = None, policy=None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from amfa.core import attestation as A

    dev = s.execute(select(db.Device).where(db.Device.customer_id == customer.id,
                                            db.Device.device_key == device_key_)).scalars().first()
    if dev is None or not dev.public_jwk:
        return False
    message = f"amfa-device|{sess.id}|{device_nonce(sess.id)}".encode()
    required = bool(policy and policy.attestation.required)
    try:
        if dev.platform == "ios":
            if not assertion:
                return False
            dev.sign_count = A.verify_app_assertion(assertion, message, policy.attestation.iosAppId,
                                                    dev.public_jwk, dev.sign_count)
        else:
            if required and dev.platform != "android":
                return False
            if required:
                A.verify_play_integrity(integrity_token or "", nonce=crypto.b64e(hashlib.sha256(message).digest()),
                                        package=policy.attestation.androidPackage)
            jwk = dev.public_jwk
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(crypto.b64d(jwk["x"]), "big"),
                                                int.from_bytes(crypto.b64d(jwk["y"]), "big"),
                                                ec.SECP256R1()).public_key()
            raw = crypto.b64d(signature or "")
            der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")) \
                if len(raw) == 64 else raw
            pub.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, KeyError, A.AttestationError):
        return False
    sess.device_bound = True
    return True


def format_amount(minor: int, currency: str) -> str:
    symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    major, cents = divmod(abs(int(minor)), 100)
    if currency == "INR":
        s = str(major)
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        body = ",".join(([head] if head else []) + groups + [tail]) if head or groups else tail
    else:
        body = f"{major:,}"
    return f"{symbol}{body}.{cents:02d}"


def mask_ref(ref: str) -> str:
    return f"••••{ref[-4:]}" if len(ref) > 4 else "••••"


def _dynamic_factors(tenant: db.Tenant) -> set[str]:
    from amfa.engine.context import dynamic_factors
    return {f.value for f in dynamic_factors(TenantPolicy.model_validate(tenant.policy).permittedFactors)}


def review_decision(s, case: db.ReviewCase, approve: bool, operator: db.Operator, note: str) -> dict:
    if case.status != "OPEN":
        raise ApiError(409, "REVIEW_CLOSED", "review already resolved")
    sess = s.get(db.AuthSession, case.session_id)
    txn = s.get(db.Transaction, case.transaction_id)
    customer = s.get(db.Customer, case.customer_id)
    tenant = s.get(db.Tenant, case.tenant_id)
    presentation_attack = customer.locked_reason == "PRESENTATION_ATTACK"
    passed = [i["factor"] for i in sess.checklist if i["status"] == PASSED]
    out: dict = {"case": case.id}

    if approve:
        if presentation_attack:
            raise ApiError(409, "ATTACK_LOCK", "a presentation attack cannot be approved into a proof")
        if not _dynamic_factors(tenant) & set(passed):
            raise ApiError(409, "NO_DYNAMIC_FACTOR", "no dynamic factor passed in this session; "
                           "reject it and have the customer start a new transaction")
        if case.first_approver is None:
            case.first_approver, case.first_approved_at = operator.id, time.time()
            audit.record(f"operator:{operator.id}", "review.approve.first", case.id,
                         {"transaction": txn.id, "note": note[:200]}, tenant.id, s=s)
            return {**out, "status": "AWAITING_SECOND_APPROVAL"}
        if case.first_approver == operator.id:
            raise ApiError(409, "FOUR_EYES", "a second, different operator must complete the approval")

    case.status = "APPROVED" if approve else "REJECTED"
    case.resolved_by, case.resolved_at, case.resolution_note = operator.id, time.time(), note[:500]
    out["status"] = case.status
    if approve and txn.status in ("REVIEW", "PENDING_AUTH"):
        policy = TenantPolicy.model_validate(tenant.policy)
        proof = proofs.issue(tenant_id=tenant.id, customer_ref=customer.external_ref, txn=txn,
                             session_id=sess.id, plan_id=sess.plan_id, factors=passed + ["review"],
                             risk_band=sess.risk_band, floor=_ctx(sess).policy.actionFloor.value,
                             ttl_s=policy.proofTtlSeconds, review_by=f"{case.first_approver},{operator.id}", s=s)
        txn.status = "AUTHORIZED"
        out["proofIssued"] = True
        webhooks.emit(s, tenant.id, "transaction.authorized", {
            "transactionId": txn.id, "externalId": txn.external_id, "sessionId": sess.id,
            "proof": proof["token"], "factors": passed + ["review"],
            "reviewedBy": [case.first_approver, operator.id], "expiresAt": proof["expiresAt"]})
    elif not approve:
        txn.status = "DENIED"
    # a rejection is the reviewer's judgement, not a confirmed fraud: training weighs labels by source
    s.add(db.RiskLabel(tenant_id=tenant.id, transaction_id=txn.id, label="GENUINE" if approve else "FRAUD",
                       source="review_verdict", reason=note[:280] or None))
    webhooks.emit(s, tenant.id, "review.resolved", {"caseId": case.id, "transactionId": txn.id,
                                                     "approved": approve})
    audit.record(f"operator:{operator.id}", "review.approve" if approve else "review.reject", case.id,
                 {"transaction": txn.id, "note": note[:200], "proofIssued": out.get("proofIssued", False),
                  "firstApprover": case.first_approver}, tenant.id, s=s)
    return out
