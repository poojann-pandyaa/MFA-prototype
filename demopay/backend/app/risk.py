"""
Adapter to the trained payment-risk model in ../../backend/ato (the existing project's model package).

DemoPay imports the model as a library and leaves the existing app untouched. If the model files are missing, a small rule
fallback is used (and the X-ray says so).
"""
import datetime
import sys
from pathlib import Path

_REPO_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_REPO_BACKEND) not in sys.path:
    sys.path.append(str(_REPO_BACKEND))      # appended, not prepended, so DemoPay's own `app` and `authn` packages always win

from ato import features as ato_features  # noqa: E402
from ato import model as ato_model        # noqa: E402

from app.models import Payment  # noqa: E402

FRI_NAMES = {0: "none", 1: "medium", 2: "high", 3: "very high"}
# What a normal, trusted phone reports when nothing unusual is going on.
NORMAL_DEVICE = dict(remote_access_active=False, bio_similarity=0.86, integrity_fail=False, overlay_accessibility=False,
                     login_to_pay_s=28.0, call_active=False, call_minutes=0.0, call_unknown_number=False)
DEVICE_KEYS = ("remote_access_active", "bio_similarity", "integrity_fail", "overlay_accessibility", "login_to_pay_s",
               "call_active", "call_minutes", "call_unknown_number", "requester_is_contact")


def _ts(dt: datetime.datetime) -> float:
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def build_state(db, user_id: int) -> "ato_features.UserState":
    """Replay the user's completed payments into the same rolling profile the model was trained with."""
    since = datetime.datetime.utcnow() - datetime.timedelta(days=90)
    rows = (db.query(Payment).filter(Payment.user_id == user_id, Payment.status == "completed", Payment.created_at >= since)
            .order_by(Payment.created_at.desc()).limit(500).all())
    st = ato_features.UserState()
    for r in reversed(rows):
        st.update(_ts(r.created_at), r.payee_vpa, r.amount, r.device_id)
    return st


def assess(db, user_id: int, balance: float, req, entry, device_id: str):
    """Score one payment. Returns (Result, xray dict). `req` is app.schemas.RiskIn; `entry` is the payee's Directory row or None."""
    now = datetime.datetime.utcnow()
    sig = req.signals.model_dump(exclude_none=True)
    fields = dict(failed_pin_attempts=0, ip_geo_km=3.0)
    if req.attested:
        fields.update(NORMAL_DEVICE)
        fields.update({k: v for k, v in sig.items() if k in NORMAL_DEVICE})
        if req.channel == "collect":
            fields["requester_is_contact"] = req.requester_is_contact
    # facts the PSP/telco would know (not device telemetry): available either way
    for k in ("device_bound_hours", "old_device_active", "sim_changed_hours", "pin_reset_hours"):
        if k in sig:
            fields[k] = sig[k]
    if entry:
        fields.update(payee_age_days=entry.age_days, payee_fan_in_24h=entry.fan_in_24h, payee_is_merchant=bool(entry.is_merchant),
                      payee_risky_category=bool(entry.risky_category), payee_fri=entry.fri, payee_complaints=entry.complaints)
    else:  # an account the platform has never seen
        fields.update(payee_age_days=0.5, payee_fan_in_24h=0, payee_is_merchant=False, payee_risky_category=False, payee_fri=None, payee_complaints=None)
    ev = ato_features.Event(ts=_ts(now), payee=req.payee_vpa, amount=req.amount, device_id=device_id, balance=balance, channel=req.channel, **fields)
    state = build_state(db, user_id)
    scorer = ato_model.get_scorer()
    res = scorer.score(ev, state, req.attested) if scorer else ato_model.fallback(ev)
    variant = "full" if req.attested else "server_only"
    thresholds = scorer.card["variants"][variant]["thresholds"] if scorer else None
    shown = ("amount", "channel", "payee_age_days", "payee_fan_in_24h", "payee_fri", "payee_complaints", "call_active", "call_minutes",
             "remote_access_active", "device_bound_hours", "sim_changed_hours", "pin_reset_hours", "bio_similarity")
    inputs = {k: getattr(ev, k) for k in shown if getattr(ev, k, None) not in (None, False, 0, 0.0)}
    inputs["history_payments"] = state.n
    xray = {"severity": res.severity, "threat": res.threat, "model": res.variant, "score": round(res.score, 3),
            "surprises": {k: round(v, 2) for k, v in (res.surprises or {}).items()},
            "thresholds": {k: round(v, 2) for k, v in thresholds.items()} if thresholds else None,
            "reasons": [{"input": f, "pushes_up_by": d} for f, d in res.reasons], "inputs": inputs}
    return res, xray
