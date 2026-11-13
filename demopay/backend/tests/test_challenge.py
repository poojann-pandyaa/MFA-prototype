import datetime

import pytest

from app import config
from app.db import SessionLocal
from app.models import AuthChallenge, Payment, User
from authn import challenge


@pytest.fixture()
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def payment(db):
    u = User(username="u", password_hash="x")
    db.add(u)
    db.commit()
    p = Payment(user_id=u.id, payee_vpa="a@ybl", amount=500.0, device_id="d", status="pending", plan_json="{}")
    db.add(p)
    db.commit()
    return p


def test_challenge_is_tied_to_its_payment(db, payment):
    ch = challenge.create(db, payment, 0, "warning", {}, {})
    challenge.verify_ready(ch, payment, ch.nonce)                       # right nonce, right payment: fine
    with pytest.raises(challenge.ChallengeError) as e:
        challenge.verify_ready(ch, payment, "wrong-nonce")
    assert e.value.code == "mismatch"
    payment.amount = 900.0                                              # the amount changed after the challenge was made
    with pytest.raises(challenge.ChallengeError) as e:
        challenge.verify_ready(ch, payment, ch.nonce)
    assert e.value.code == "mismatch"


def test_challenge_cannot_be_reused(db, payment):
    ch = challenge.create(db, payment, 0, "warning", {}, {})
    challenge.close(db, ch, "passed")
    with pytest.raises(challenge.ChallengeError) as e:
        challenge.verify_ready(ch, payment, ch.nonce)
    assert e.value.code == "used"
    assert challenge.open_for(db, payment, 0) is None


def test_challenge_expires(db, payment):
    ch = challenge.create(db, payment, 0, "warning", {}, {})
    ch.expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    db.commit()
    assert challenge.open_for(db, payment, 0) is None
    with pytest.raises(challenge.ChallengeError) as e:
        challenge.verify_ready(ch, payment, ch.nonce)
    assert e.value.code == "expired"


def test_challenge_locks_after_too_many_attempts(db, payment):
    ch = challenge.create(db, payment, 0, "typed_confirm", {}, {})
    ch.attempts = config.MAX_ATTEMPTS
    db.commit()
    with pytest.raises(challenge.ChallengeError) as e:
        challenge.verify_ready(ch, payment, ch.nonce)
    assert e.value.code == "locked"


def test_outcome_is_logged_without_payment_details(db, payment):
    ch = challenge.create(db, payment, 0, "warning", {"title": "t"}, {})
    challenge.close(db, ch, "passed", "ok")
    row = db.get(AuthChallenge, ch.id)
    assert row.status == "passed" and row.detail == "ok" and row.seconds is not None
    assert "500" not in (row.detail or "") and payment.payee_vpa not in (row.detail or "")
