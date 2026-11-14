"""A bank chooses how its customers get the one-time code: email, text, an authenticator app, or any mix.
The choice decides which customers have the factor at all, how the code is made, and what they may enrol."""
import dataclasses
import json
import socketserver
import threading
import time
import uuid

import pytest

from amfa import config
from amfa.common import crypto
from amfa.core import context_builder, db, otp
from amfa.engine.policy import OtpMethod, TenantPolicy


@pytest.fixture(scope="module")
def tenant():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="Test Bank", slug=f"t{uuid.uuid4().hex[:8]}",
                      policy=TenantPolicy().model_dump(mode="json"))
        s.add(t)
    return t


def customer(tenant, channel=None, authenticator=False):
    cid = f"cu_{uuid.uuid4().hex[:12]}"
    sealed = None if channel is None else crypto.seal(
        json.dumps({"channel": channel, "to": "a@example.com" if channel == "email" else "+919812345678"}).encode(),
        f"contact|{cid}", "contact")
    with db.session() as s:
        s.add(db.Customer(id=cid, tenant_id=tenant.id, external_ref=uuid.uuid4().hex[:12], contact_sealed=sealed))
        if authenticator:
            s.add(db.TotpCredential(customer_id=cid, secret_sealed="x", confirmed=True))
            s.add(db.Enrollment(customer_id=cid, factor="OTP", detail={"method": "totp"}))
    with db.session() as s:
        return s.get(db.Customer, cid)


def policy(*methods):
    return TenantPolicy(otpMethods=list(methods))


def route(c, pol):
    with db.session() as s:
        return context_builder.otp_route(s, s.get(db.Customer, c.id), pol)


def test_a_bank_allows_every_method_unless_it_says_otherwise():
    assert set(TenantPolicy().otpMethods) == {OtpMethod.EMAIL, OtpMethod.SMS, OtpMethod.AUTHENTICATOR}
    with pytest.raises(ValueError):
        TenantPolicy(otpMethods=[])
    assert TenantPolicy(otpMethods=["email", "email"]).otpMethods == [OtpMethod.EMAIL]


@pytest.mark.parametrize("channel,methods,expected", [
    ("email", ["email"], "delivered"),
    ("email", ["sms"], None),                       # the bank texts only; an email address is of no use
    ("sms", ["sms", "authenticator"], "delivered"),
    ("sms", ["email"], None),
    ("email", ["authenticator"], None),             # authenticator only, and this customer has not set one up
    (None, ["email", "sms", "authenticator"], None),
])
def test_a_contact_is_used_only_if_the_bank_allows_its_channel(tenant, channel, methods, expected):
    assert route(customer(tenant, channel), policy(*methods)) == expected


def test_an_authenticator_wins_over_a_sent_code_when_both_are_allowed(tenant):
    c = customer(tenant, "email", authenticator=True)
    assert route(c, policy("email", "authenticator")) == "totp"
    assert route(c, policy("email")) == "delivered"      # the bank dropped authenticators: back to the emailed code
    assert route(c, policy("sms")) is None               # and a customer with only an authenticator has no code at all


def test_the_engine_sees_otp_as_available_only_when_there_is_a_route(tenant):
    c = customer(tenant, "email")
    with db.session() as s:
        cu = s.get(db.Customer, c.id)
        assert context_builder.enrollment_of(s, cu, policy("email")).otp is True
        assert context_builder.enrollment_of(s, cu, policy("authenticator")).otp is False


class _Sink(socketserver.StreamRequestHandler):
    """The smallest SMTP server that takes one message: no STARTTLS, which a local development mail catcher often lacks."""
    mail: list = []

    def handle(self):
        w = lambda line: self.wfile.write(line.encode() + b"\r\n")
        w("220 sink")
        data, body = None, []
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if data is not None:
                if line == ".":
                    _Sink.mail.append("\n".join(body))
                    data = None
                    w("250 queued")
                else:
                    body.append(line)
                continue
            cmd = line.split(" ")[0].upper()
            if cmd == "EHLO":
                w("250 sink")
            elif cmd == "DATA":
                data, body = True, []
                w("354 go")
            elif cmd == "QUIT":
                w("221 bye")
                return
            else:
                w("250 ok")


def test_a_code_really_goes_out_by_email_over_smtp(monkeypatch):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = config.get()
    smtp_settings = dataclasses.replace(base, otp_provider="smtp", smtp_host="127.0.0.1", smtp_port=server.server_address[1],
                                        smtp_from="bank@example.com")
    monkeypatch.setattr(config, "get", lambda: smtp_settings)
    _Sink.mail.clear()
    try:
        assert otp.deliver({"channel": "email", "to": "customer@example.com"}, "123456 is your Test Bank code") == "email"
    finally:
        server.shutdown()
    assert len(_Sink.mail) == 1
    assert "123456 is your Test Bank code" in _Sink.mail[0] and "customer@example.com" in _Sink.mail[0]
    assert "Your approval code" in _Sink.mail[0]


def test_email_delivery_refuses_a_phone_number(monkeypatch):
    base = config.get()
    monkeypatch.setattr(config, "get", lambda: dataclasses.replace(base, otp_provider="smtp", smtp_host="127.0.0.1", smtp_from="x@example.com"))
    with pytest.raises(RuntimeError):
        otp.deliver({"channel": "sms", "to": "+919812345678"}, "123456")


def test_an_authenticator_setup_gives_a_qr_a_link_and_a_code_that_works_once():
    secret = otp.new_secret()
    uri = otp.provisioning_uri(secret, "customer", "Test Bank")
    assert uri.startswith("otpauth://totp/") and f"secret={secret}" in uri
    assert "<svg" in otp.qr_svg(uri)                      # needs the qrcode package, which CI must install
    step = otp.verify_totp(secret, otp.totp_now(secret), 0)
    assert step is not None
    assert otp.verify_totp(secret, otp.totp_now(secret), step) is None   # the same code is refused a second time
    assert otp.verify_totp(secret, "000000", 0) is None
