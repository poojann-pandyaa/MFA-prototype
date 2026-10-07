from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import secrets
import smtplib
import struct
import sys
import time
import uuid
from email.message import EmailMessage
from typing import Optional

import httpx

from amfa import config
from amfa.common import crypto

log = logging.getLogger("amfa.otp")

STEP_S = 30
DIGITS = 6
DRIFT_STEPS = 1
DELIVERED_TTL_S = 180


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return f"{code:0{DIGITS}d}"


def totp_now(secret_b32: str, at: Optional[float] = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // STEP_S))


def verify_totp(secret_b32: str, code: str, last_step: int,
                at: Optional[float] = None) -> Optional[int]:
    if not (isinstance(code, str) and code.isdigit() and len(code) == DIGITS):
        return None
    cur = int((at or time.time()) // STEP_S)
    matched = None
    for step in range(cur - DRIFT_STEPS, cur + DRIFT_STEPS + 1):
        if hmac.compare_digest(_hotp(secret_b32, step), code) and step > last_step:
            matched = step
    return matched


def provisioning_uri(secret_b32: str, account: str, issuer: str) -> str:
    from urllib.parse import quote
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_S}")


def qr_svg(uri: str) -> str:
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def new_code() -> str:
    return f"{secrets.randbelow(10 ** DIGITS):0{DIGITS}d}"


def code_hash(session_id: str, code: str) -> str:
    return hmac.new(crypto.derive_key("otp-code"), f"{session_id}|{code}".encode(),
                    hashlib.sha256).hexdigest()


def message_for(code: str, amount_display: str, payee_display: str, bank: str) -> str:
    return (f"{code} is your {bank} code to approve {amount_display} to {payee_display}. "
            f"Valid 3 minutes. Never share it -- the bank will never ask for it.")


def deliver(contact: dict, message: str) -> str:
    s = config.get()
    provider = s.otp_provider
    if provider == "console":
        if s.production:
            raise RuntimeError("console OTP delivery is disabled in production")
        print(f"[amfa dev OTP -> {contact.get('channel')}] {message}", file=sys.stderr, flush=True)
        return "console"
    if provider == "webhook":
        import os
        url, secret = os.getenv("AMFA_OTP_WEBHOOK_URL"), os.getenv("AMFA_OTP_WEBHOOK_SECRET", "")
        if not url or len(secret) < 32:
            raise RuntimeError("AMFA_OTP_WEBHOOK_URL and a 32+ char AMFA_OTP_WEBHOOK_SECRET are required")
        body = crypto.b64e(message.encode())
        payload = {"id": f"otp_{uuid.uuid4().hex[:12]}", "channel": contact.get("channel", "sms"),
                   "to": contact.get("to"), "messageB64": body, "ts": int(time.time())}
        import json
        raw = json.dumps(payload, separators=(",", ":")).encode()
        sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        r = httpx.post(url, content=raw, timeout=5.0,
                       headers={"content-type": "application/json", "x-amfa-signature": sig})
        r.raise_for_status()
        return contact.get("channel", "sms")
    if provider == "smtp":
        if contact.get("channel") != "email":
            raise RuntimeError("SMTP delivery needs an e-mail contact")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.smtp_from, contact["to"], "Your approval code"
        msg.set_content(message)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password or "")
            smtp.send_message(msg)
        return "email"
    raise RuntimeError(f"unknown OTP provider {provider!r}")


def mask_contact(to: str) -> str:
    if "@" in to:
        name, _, dom = to.partition("@")
        return f"{name[:1]}***@{dom}"
    return f"******{to[-4:]}" if len(to) >= 4 else "****"
