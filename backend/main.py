import binascii
import datetime
import json
import logging

from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base, get_db
import models
import schemas
import config
from services import auth
from services import biometrics
from ato import features as ato_features
from ato import model as ato_model

Base.metadata.create_all(bind=engine)

logger = logging.getLogger(__name__)

# Step-2 diagnostics (services/biometrics.py's "mfa.step2" logger) at INFO.
# uvicorn's log config only configures its own loggers and the root logger
# has no handler, so give this one a stderr handler or INFO lines vanish.
_step2_log = logging.getLogger("mfa.step2")
_step2_log.setLevel(logging.INFO)
if not _step2_log.handlers:
    _step2_handler = logging.StreamHandler()
    _step2_handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    _step2_log.addHandler(_step2_handler)

_GENERIC_VERIFICATION_FAILURE = "Verification failed. Please try again."

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/enroll", response_model=schemas.Token)
def enroll(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    try:
        db_user = db.query(models.User).filter(models.User.username == user_in.username).first()
        if db_user:
            raise HTTPException(status_code=400, detail="Username already registered")

        # 1. Process biometrics: extract embedding from base64 image
        embedding_str = biometrics.extract_embedding(user_in.face_image_b64)
        if not embedding_str:
            raise HTTPException(status_code=400, detail="Could not extract face embedding. Ensure a face is clearly visible.")

        # 2. Create user
        hashed_password = auth.get_password_hash(user_in.password)
        new_user = models.User(
            username=user_in.username,
            password_hash=hashed_password,
            face_embedding=embedding_str
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        # Return token
        access_token = auth.create_access_token(data={"sub": new_user.username})
        return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Backend Error: {str(e)}")


@app.post("/login/step1")
def login_step1(login_in: schemas.UserLoginStep1, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == login_in.username).first()
    if not user or not auth.verify_password(login_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    
    # Calculate risk score
    risk_score = auth.calculate_risk_score(db, user, login_in.device_identifier)
    
    if risk_score == "LOW" and not config.ALWAYS_REQUIRE_FACE_CHECK:
        # Record success, register device if not exist
        device = db.query(models.Device).filter(models.Device.user_id == user.id, models.Device.device_identifier == login_in.device_identifier).first()
        if not device:
            db.add(models.Device(user_id=user.id, device_identifier=login_in.device_identifier))
        
        db.add(models.LoginHistory(
            user_id=user.id,
            device_identifier=login_in.device_identifier,
            risk_score=risk_score,
            face_check_triggered=False,
            success=True
        ))
        db.commit()
        access_token = auth.create_access_token(data={"sub": user.username})
        return {"require_step2": False, "access_token": access_token, "token_type": "bearer", "risk_score": risk_score}
    else:
        # Require face check. Issue a single-use, short-lived session_id
        # bound to this user+device: step 2 must present it, which is what
        # proves the password step actually happened and succeeded.
        db.add(models.LoginHistory(
            user_id=user.id,
            device_identifier=login_in.device_identifier,
            risk_score=risk_score,
            face_check_triggered=True,
            success=False # not yet successful
        ))
        db.commit()
        session_id, challenge_type = auth.create_verification_session(db, user, login_in.device_identifier)
        return {
            "require_step2": True,
            "risk_score": risk_score,
            "session_id": session_id,
            "challenge_type": challenge_type,
        }

@app.post("/login/step2")
def login_step2(login_in: schemas.UserLoginStep2, db: Session = Depends(get_db)):
    # Reject oversized bursts before touching the DB or decoding anything.
    if len(login_in.images) > config.LIVENESS_MAX_FRAMES:
        raise HTTPException(status_code=400, detail="Too many frames submitted.")

    user = db.query(models.User).filter(models.User.username == login_in.username).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # Require proof that step 1 (password) was already completed for this
    # exact user+device. Without this check, step 2 would be a full MFA
    # bypass: anyone who can pass the face check for a *known username*
    # would get a token with no password at all.
    vs = auth.consume_verification_session(db, login_in.session_id, user, login_in.device_identifier)

    # PAD, gesture, and face-match all live behind one generic failure
    # message - a client (or attacker) never learns which one failed, so
    # a captured burst can't be iteratively tuned against the checks (see
    # "Error handling & security considerations" in the design spec).
    try:
        verified = biometrics.verify_liveness_and_identity(
            login_in.images, vs.challenge_type, user.face_embedding
        )
    except biometrics.RetakeNeededError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except binascii.Error:
        # Malformed base64 in one of the frames: a client error, not a 500.
        raise HTTPException(status_code=400, detail="Invalid image data. Please retake.")
    except Exception:
        # Anything unexpected (e.g. arcface.onnx missing -> RuntimeError from
        # embed(), which only runs AFTER PAD and the gesture passed) must be
        # indistinguishable from an ordinary failed attempt: a 500 here would
        # tell the client how far its burst got. Log it server-side instead.
        # The session was already consumed above, so this can't be retried.
        logger.exception("Unexpected error during step-2 verification")
        raise HTTPException(status_code=401, detail=_GENERIC_VERIFICATION_FAILURE)

    if not verified:
        raise HTTPException(status_code=401, detail=_GENERIC_VERIFICATION_FAILURE)

    # A step-up that was issued for a payment (POST /txn/risk) unlocks that
    # payment only; it must not touch login history or register a device.
    link = db.query(models.TxnStepUp).filter(models.TxnStepUp.session_id == login_in.session_id).first()
    if link:
        db.get(models.Transaction, link.txn_id).stepup_passed = True
        db.commit()
        return {"access_token": auth.create_access_token(data={"sub": user.username}), "token_type": "bearer"}

    # If passes:
    # Update login history to success
    history = db.query(models.LoginHistory).filter(
        models.LoginHistory.user_id == user.id,
        models.LoginHistory.device_identifier == login_in.device_identifier
    ).order_by(models.LoginHistory.timestamp.desc()).first()

    if history:
        history.success = True

    device = db.query(models.Device).filter(models.Device.user_id == user.id, models.Device.device_identifier == login_in.device_identifier).first()
    if not device:
        db.add(models.Device(user_id=user.id, device_identifier=login_in.device_identifier))

    db.commit()
    access_token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/history", response_model=list[schemas.LoginHistoryResponse])
def get_history(current_user: models.User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    # Identity comes from the verified JWT, not a client-supplied path
    # param, so a user can only ever read their own history.
    history = db.query(models.LoginHistory).filter(
        models.LoginHistory.user_id == current_user.id
    ).order_by(models.LoginHistory.timestamp.desc()).all()
    return history


# ---- Payment risk: takeover / coached-scam / bad-payee model (docs/ato-model-report.md) ------------
_FRI = {"none": 0, "medium": 1, "high": 2, "very_high": 3}
_DEVICE_FIELDS = ("remote_access_active", "bio_similarity", "integrity_fail", "overlay_accessibility", "sideload_hours",
                  "login_to_pay_s", "call_active", "call_minutes", "call_unknown_number", "requester_is_contact")
_MESSAGES = {
    "device_biometric_reprompt": "For your security, please confirm it's you.",
    "face_challenge": "For your security, we need to verify it's you before this payment.",
    "block_and_lock_binding": "This payment was stopped to protect your account. Contact your bank.",
    "scam_warning": "Be careful: scammers often ask for payment during a call or chat. Banks and government agencies never do.",
    "cooling_off_hold": "This payment looks like a common scam pattern. It is on hold for {m} minutes; you can cancel it. Real agencies never ask you to pay during a call.",
    "block_and_offer_report": "We stopped this payment because it matches a known scam. If someone is pressuring you, hang up and call 1930.",
    "payee_risk_badge": "This payee has been reported before. Make sure you know and trust them.",
    "hold_and_verify_payee": "This payee looks risky. The payment is on hold for {m} minutes while we verify.",
}
_COLLECT_MESSAGE = "This request will SEND money from your account. You never enter a PIN to receive money."


def _utc_ts(dt: datetime.datetime) -> float:
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


@app.post("/txn/risk", response_model=schemas.TxnRiskResponse)
def txn_risk_assess(
    req: schemas.TxnRiskRequest,
    user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Score a payment BEFORE the UPI PIN screen and say what friction to apply."""
    T = models.Transaction
    now = datetime.datetime.utcnow()
    # Same featurizer as training: replay the user's completed payments into a UserState.
    rows = (
        db.query(T.payee_vpa, T.amount, T.created_at, T.device_identifier)
        .filter(T.user_id == user.id, T.status == "approved", T.created_at >= now - datetime.timedelta(days=90))
        .order_by(T.created_at.desc()).limit(500).all()
    )
    state = ato_features.UserState()
    for r in reversed(rows):
        state.update(_utc_ts(r.created_at), r.payee_vpa, r.amount, r.device_identifier)
    first_seen = db.query(func.min(T.created_at)).filter(T.payee_vpa == req.payee_vpa).scalar()
    payers_24h = (
        db.query(func.count(distinct(T.user_id)))
        .filter(T.payee_vpa == req.payee_vpa, T.user_id != user.id, T.created_at >= now - datetime.timedelta(hours=24))
        .scalar()
    )
    fields = req.model_dump(exclude={"payee_vpa", "amount", "device_identifier", "balance", "sdk_attested"})
    fields["payee_fri"] = _FRI.get(fields["payee_fri"]) if fields["payee_fri"] else None
    if not req.sdk_attested:  # untrusted client: never let unattested telemetry influence the score
        for k in _DEVICE_FIELDS:
            fields[k] = None
    ev = ato_features.Event(
        ts=_utc_ts(now), payee=req.payee_vpa, amount=req.amount, device_id=req.device_identifier, balance=req.balance,
        payee_age_days=(now - first_seen).total_seconds() / 86400 if first_seen else 0.0,
        payee_fan_in_24h=payers_24h or 0, **fields,
    )
    scorer = ato_model.get_scorer()
    res = scorer.score(ev, state, req.sdk_attested) if scorer else ato_model.fallback(ev)
    # Reasons are logged and stored for analysts, never returned: they would tell an attacker which signal to hide.
    logger.info("txn risk user=%s variant=%s severity=%s threat=%s score=%.2f reasons=%s",
                user.id, res.variant, res.severity, res.threat, res.score, res.reasons)

    action = res.action
    if config.ATO_SHADOW:  # score and log, never add friction (use while collecting real labels)
        action = "allow"
    hold = ato_model.HOLD_MINUTES if action in ato_model.HOLD_ACTIONS else 0
    status_ = "approved" if action == "allow" else "blocked" if action in ato_model.BLOCK_ACTIONS else "pending"
    row = T(
        user_id=user.id, payee_vpa=req.payee_vpa, amount=req.amount, channel=req.channel,
        device_identifier=req.device_identifier, created_at=now, status=status_, action=action,
        severity=res.severity, threat_type=res.threat, score=res.score,
        reasons=json.dumps([f for f, _ in res.reasons] + ([f"shadow:{res.action}"] if config.ATO_SHADOW and res.action != "allow" else [])),
        release_at=now + datetime.timedelta(minutes=hold) if hold else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    step_up = None
    if action == "face_challenge":
        session_id, challenge_type = auth.create_verification_session(db, user, req.device_identifier)
        db.add(models.TxnStepUp(session_id=session_id, txn_id=row.id))
        db.commit()
        step_up = {"session_id": session_id, "challenge_type": challenge_type}

    if action == "scam_warning" and req.channel == "collect" and req.requester_is_contact is not True:
        message = _COLLECT_MESSAGE
    else:
        message = _MESSAGES.get(action, "").format(m=ato_model.HOLD_MINUTES)
    return {
        "txn_id": row.id, "severity": res.severity, "threat_type": res.threat, "action": action, "message": message,
        "status": status_, "hold_until": row.release_at, "step_up": step_up,
    }


@app.post("/txn/{txn_id}/confirm", response_model=schemas.TxnConfirmResponse)
def txn_confirm(
    txn_id: int,
    user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Release a payment to the UPI PIN screen once its friction is cleared.

    Soft actions (warning, badge, device-biometric reprompt) are client-side UX the
    server cannot observe, so they release immediately; the server enforces only the
    hard ones: face step-up completed, hold elapsed, not blocked.
    """
    txn = db.query(models.Transaction).filter(
        models.Transaction.id == txn_id, models.Transaction.user_id == user.id
    ).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.status == "approved":
        return {"txn_id": txn.id, "status": "approved"}
    if txn.status == "blocked":
        raise HTTPException(status_code=403, detail="This payment was blocked.")
    if txn.action == "face_challenge" and not txn.stepup_passed:
        raise HTTPException(status_code=403, detail="Verification required before this payment.")
    if txn.release_at and datetime.datetime.utcnow() < txn.release_at:
        raise HTTPException(status_code=409, detail=f"Payment on hold until {txn.release_at.isoformat()}Z.")
    txn.status = "approved"
    db.commit()
    return {"txn_id": txn.id, "status": "approved"}
