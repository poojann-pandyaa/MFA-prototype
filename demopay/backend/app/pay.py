"""Payment routes: risk check -> plan -> steps -> PIN -> money moves."""
import datetime
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import config, risk
from app.db import get_db
from app.deps import current_user, device_id
from app.models import Account, Directory, Payment, Profile, UserAuthMethod
from app.schemas import AnswerIn, PinIn, RiskIn
from authn import challenge, methods, policy
from authn.methods import HELPLINE

router = APIRouter(prefix="/pay", tags=["pay"])

BLOCK_MESSAGE = "We stopped this payment to protect your money. " + HELPLINE
FAIL_MESSAGES = {
    "scam_indicators": "You answered yes to a scam warning question, so we stopped this payment. " + HELPLINE,
    "user_declined_payee": "Payment cancelled: you said this is not who you mean to pay.",
    "no_registered_name": "We cannot confirm who owns this account, so this payment was stopped.",
}


def _own(db: Session, user, pid: int) -> Payment:
    p = db.get(Payment, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "Payment not found")
    return p


def _plan(p: Payment) -> dict:
    return json.loads(p.plan_json or "{}")


def _out(db: Session, p: Payment) -> dict:
    plan = _plan(p)
    entry = db.get(Directory, p.payee_vpa)
    return {"id": p.id, "status": p.status, "amount": p.amount, "payee_vpa": p.payee_vpa, "registered_name": entry.registered_name if entry else None,
            "channel": p.channel, "note": p.note, "severity": p.severity, "threat": p.threat, "plan": plan, "steps_done": p.steps_done,
            "current_step": p.steps_done if p.status == "pending" else None, "message": p.message,
            "created_at": p.created_at.isoformat() + "Z"}


@router.post("/risk")
def risk_check(req: RiskIn, user=Depends(current_user), dev: str = Depends(device_id), db: Session = Depends(get_db)):
    """Score the payment BEFORE the PIN screen and return what (if anything) the user must do first."""
    acct = db.query(Account).filter(Account.user_id == user.id).first()
    if req.amount > acct.balance:
        raise HTTPException(400, "Not enough balance")
    entry = db.get(Directory, req.payee_vpa)
    prior = req.base_rate if (req.base_rate and config.DEMO) else config.BASE_RATE
    res, xray = risk.assess(db, user.id, acct.balance, req, entry, dev)
    now = datetime.datetime.utcnow()
    enrolled = {m.method for m in db.query(UserAuthMethod).filter(UserAuthMethod.user_id == user.id, UserAuthMethod.active == True)  # noqa: E712
                if m.delay_until is None or m.delay_until <= now}
    switched_off = {m.method for m in db.query(UserAuthMethod).filter(UserAuthMethod.user_id == user.id, UserAuthMethod.active == False)}  # noqa: E712
    prof = db.query(Profile).filter(Profile.user_id == user.id).first()
    senior = bool(prof and prof.age >= config.SENIOR_AGE)
    plan = policy.make_plan(res.severity, res.threat, req.amount, enrolled, prior, config.TIME_BUDGET,
                            config.SENIOR_SLOW_AMOUNT if senior else config.SLOW_AMOUNT,
                            exclude=tuple(switched_off) + (() if entry else ("typed_confirm",)))
    status = {"allow": "approved", "block": "blocked", "steps": "pending"}[plan.decision]
    plan_d = {"senior_rule": senior, "decision": plan.decision, "p_scam": plan.p_scam, "base_rate": plan.base_rate, "expected_cost": round(plan.expected_cost, 2),
              "time_seconds": plan.time_seconds, "notes": plan.notes,
              "steps": [{"index": s.index, "method": s.method, "name": s.name, "tier": s.tier, "why": s.why, "est_seconds": s.est_seconds,
                         "doubt_after": round(s.doubt_after, 6)} for s in plan.steps]}
    p = Payment(user_id=user.id, payee_vpa=req.payee_vpa, amount=req.amount, channel=req.channel, note=req.note, device_id=dev, status=status,
                severity=res.severity, threat=res.threat, score=res.score, plan_json=json.dumps(plan_d), steps_done=0,
                message=BLOCK_MESSAGE if status == "blocked" else "")
    xray["plan"] = plan_d
    p.explain_json = json.dumps(xray)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(db, p)


@router.get("")
def activity(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Payment).filter(Payment.user_id == user.id).order_by(Payment.created_at.desc(), Payment.id.desc()).limit(40).all()
    names = {d.vpa: d.registered_name for d in db.query(Directory).filter(Directory.vpa.in_({r.payee_vpa for r in rows}))}
    return [{"id": r.id, "status": r.status, "amount": r.amount, "payee_vpa": r.payee_vpa, "payee_name": names.get(r.payee_vpa), "severity": r.severity,
             "created_at": r.created_at.isoformat() + "Z"} for r in rows]


@router.get("/{pid}")
def get_payment(pid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, _own(db, user, pid))


def _current_step(p: Payment, n: int) -> dict:
    steps = _plan(p).get("steps", [])
    if p.status != "pending":
        raise HTTPException(409, f"This payment is {p.status}")
    if n != p.steps_done or n >= len(steps):
        raise HTTPException(409, "That is not the current step")
    return steps[n]


@router.post("/{pid}/steps/{n}/start")
def step_start(pid: int, n: int, user=Depends(current_user), db: Session = Depends(get_db)):
    p = _own(db, user, pid)
    step = _current_step(p, n)
    ch = challenge.open_for(db, p, n)
    if ch is None:
        entry = db.get(Directory, p.payee_vpa)
        data, secret = methods.start(step["method"], payment=p, entry=entry, family=p.threat, hold_seconds=config.HOLD_SECONDS, step_index=n)
        ch = challenge.create(db, p, n, step["method"], data, secret)
    return {"nonce": ch.nonce, "method": ch.method, "data": json.loads(ch.data_json), "attempts_left": config.MAX_ATTEMPTS - ch.attempts,
            "expires_at": ch.expires_at.isoformat() + "Z"}


@router.post("/{pid}/steps/{n}/answer")
def step_answer(pid: int, n: int, body: AnswerIn, user=Depends(current_user), db: Session = Depends(get_db)):
    p = _own(db, user, pid)
    step = _current_step(p, n)
    ch = challenge.open_for(db, p, n)
    if ch is None:
        raise HTTPException(409, "Start this step first")
    try:
        challenge.verify_ready(ch, p, body.nonce)
    except challenge.ChallengeError as e:
        db.commit()
        if e.code == "locked":
            challenge.close(db, ch, "failed", "locked")
            p.status, p.message = "blocked", "Too many wrong attempts, so this payment was stopped."
            db.commit()
        raise HTTPException(400, {"code": e.code, "message": e.message})
    outcome, detail = methods.check(step["method"], challenge=ch, secret=json.loads(ch.secret_json), answer=body.model_dump(exclude_none=True))
    if outcome == "wait":
        return {"passed": False, "wait": True, "message": detail, "status": p.status}
    ch.attempts += 1
    if outcome == "pass":
        challenge.close(db, ch, "passed", detail)
        p.steps_done += 1
        if p.steps_done >= len(_plan(p)["steps"]):
            p.status = "approved"
        db.commit()
        return {"passed": True, "status": p.status, "next_step": p.steps_done if p.status == "pending" else None}
    if outcome == "retry" and ch.attempts < config.MAX_ATTEMPTS:
        db.commit()
        return {"passed": False, "retry": True, "message": detail, "attempts_left": config.MAX_ATTEMPTS - ch.attempts, "status": p.status}
    challenge.close(db, ch, "failed", detail if outcome == "fail" else "too_many_attempts")
    p.status = "blocked"
    p.message = FAIL_MESSAGES.get(detail, "Too many wrong attempts, so this payment was stopped.")
    db.commit()
    return {"passed": False, "blocked": True, "message": p.message, "status": p.status}


@router.post("/{pid}/cancel")
def cancel(pid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    p = _own(db, user, pid)
    if p.status in ("completed", "blocked", "cancelled"):
        raise HTTPException(409, f"This payment is already {p.status}")
    p.status, p.message = "cancelled", "You cancelled this payment."
    db.commit()
    return _out(db, p)


@router.post("/{pid}/complete")
def complete(pid: int, body: PinIn, user=Depends(current_user), db: Session = Depends(get_db)):
    """Final step: the (fake) PIN. Only a payment whose steps have all passed can move money."""
    p = _own(db, user, pid)
    if p.status != "approved":
        raise HTTPException(409, "This payment is not ready: finish the required steps first." if p.status == "pending" else f"This payment is {p.status}")
    acct = db.query(Account).filter(Account.user_id == user.id).with_for_update().first()
    if p.amount > acct.balance:
        raise HTTPException(400, "Not enough balance")
    acct.balance -= p.amount
    p.status, p.completed_at = "completed", datetime.datetime.utcnow()
    db.commit()
    return {"status": "completed", "balance": acct.balance, "payment": _out(db, p)}
