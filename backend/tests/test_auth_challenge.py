import datetime
import config
import models
from services import auth


def _make_user(db_session):
    user = models.User(
        username="alice",
        password_hash=auth.get_password_hash("pw"),
        face_embedding="[]",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_create_verification_session_assigns_known_challenge_type(db_session):
    user = _make_user(db_session)
    for _ in range(30):
        session_id, challenge_type = auth.create_verification_session(db_session, user, "device-1")
        assert challenge_type in config.CHALLENGE_TYPES
        vs = db_session.query(models.VerificationSession).filter_by(session_id=session_id).first()
        assert vs.challenge_type == challenge_type


def test_consume_verification_session_returns_row_with_challenge_type(db_session):
    user = _make_user(db_session)
    session_id, challenge_type = auth.create_verification_session(db_session, user, "device-1")
    vs = auth.consume_verification_session(db_session, session_id, user, "device-1")
    assert vs.challenge_type == challenge_type


def test_consume_fails_after_ttl_expires(db_session, monkeypatch):
    import datetime as dt
    user = _make_user(db_session)
    session_id, _ = auth.create_verification_session(db_session, user, "device-1")

    # Simulate the burst + upload taking longer than the TTL.
    vs = db_session.query(models.VerificationSession).filter_by(session_id=session_id).first()
    vs.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
    db_session.commit()

    import fastapi
    try:
        auth.consume_verification_session(db_session, session_id, user, "device-1")
        assert False, "expected HTTPException"
    except fastapi.HTTPException as e:
        assert e.status_code == 401
