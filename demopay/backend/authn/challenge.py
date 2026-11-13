"""
One challenge service for every authenticator.

A challenge is tied to one payment (payee, amount) through a random nonce, expires, can pass once, and locks after a few wrong
attempts. Its row doubles as the step outcome log (status, attempts, seconds): counts only, no private payment details.
"""
import datetime
import hashlib
import hmac
import json
import secrets

from app import config
from app.models import AuthChallenge, Payment


class ChallengeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def same(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def payload_hash(payee: str, amount: float, nonce: str) -> str:
    return hashlib.sha256(f"{payee}|{amount:.2f}|{nonce}".encode()).hexdigest()


def create(db, payment: Payment, step_index: int, method: str, data: dict, secret: dict, ttl: int = None) -> AuthChallenge:
    nonce = secrets.token_urlsafe(16)
    now = datetime.datetime.utcnow()
    ch = AuthChallenge(
        payment_id=payment.id, step_index=step_index, method=method, nonce=nonce,
        payload_hash=payload_hash(payment.payee_vpa, payment.amount, nonce),
        data_json=json.dumps(data), secret_json=json.dumps(secret), created_at=now,
        expires_at=now + datetime.timedelta(seconds=ttl or config.CHALLENGE_TTL_SECONDS), status="open", attempts=0,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


def open_for(db, payment: Payment, step_index: int):
    """The latest still-open, unexpired challenge for this step, if any."""
    now = datetime.datetime.utcnow()
    return (db.query(AuthChallenge)
            .filter(AuthChallenge.payment_id == payment.id, AuthChallenge.step_index == step_index,
                    AuthChallenge.status == "open", AuthChallenge.expires_at > now)
            .order_by(AuthChallenge.id.desc()).first())


def verify_ready(ch: AuthChallenge, payment: Payment, nonce: str) -> None:
    """Raise ChallengeError unless this challenge can still be answered for this payment with this nonce."""
    if ch.status != "open":
        raise ChallengeError("used", "This step was already completed or closed.")
    if ch.expires_at <= datetime.datetime.utcnow():
        ch.status = "expired"
        raise ChallengeError("expired", "This step expired. Start the payment again.")
    if ch.attempts >= config.MAX_ATTEMPTS:
        ch.status = "failed"
        raise ChallengeError("locked", "Too many wrong attempts.")
    if not same(nonce, ch.nonce) or not same(payload_hash(payment.payee_vpa, payment.amount, nonce), ch.payload_hash):
        raise ChallengeError("mismatch", "This answer does not belong to this payment.")


def close(db, ch: AuthChallenge, status: str, detail: str = "") -> None:
    ch.status = status
    ch.detail = detail
    ch.seconds = (datetime.datetime.utcnow() - ch.created_at).total_seconds()
    db.commit()
