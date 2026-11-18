from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

PORTS = {"core": 8000, "face": 8101, "voice": 8102, "ueba": 8103, "bands": 8104}
APPS = {"core": "amfa.core.app:app", "face": "amfa.factors.face.service:app",
        "voice": "amfa.factors.voice.service:app", "ueba": "amfa.factors.ueba.service:app",
        "bands": "amfa.bands.service:app"}


def _password(prompt: str) -> str:
    pw = os.getenv("AMFA_BOOTSTRAP_PASSWORD")
    if pw:
        return pw
    first = getpass.getpass(prompt)
    if first != getpass.getpass("Repeat: "):
        sys.exit("passwords do not match")
    return first


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="amfa")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init")
    i.add_argument("--admin-email", required=True)

    t = sub.add_parser("tenant").add_subparsers(dest="action", required=True)
    tc = t.add_parser("create")
    tc.add_argument("--name", required=True)
    tc.add_argument("--slug", required=True)

    o = sub.add_parser("operator").add_subparsers(dest="action", required=True)
    oc = o.add_parser("create")
    oc.add_argument("--tenant", required=True)
    oc.add_argument("--email", required=True)
    oc.add_argument("--role", required=True, choices=["tenant_admin", "compliance", "analyst", "auditor"])

    k = sub.add_parser("apikey").add_subparsers(dest="action", required=True)
    kc = k.add_parser("create")
    kc.add_argument("--tenant", required=True)
    kc.add_argument("--name", required=True)
    kc.add_argument("--scopes", default=None)

    sk = sub.add_parser("signing-key").add_subparsers(dest="action", required=True)
    sk.add_parser("rotate")

    au = sub.add_parser("audit").add_subparsers(dest="action", required=True)
    au.add_parser("verify")

    mg = sub.add_parser("migrate")
    mg.add_argument("--grant-app-role", default=None,
                    help="PostgreSQL: grant this role DML on every table, but only INSERT/SELECT on "
                         "the audit log, decisions, security events and attempts, so the running "
                         "application cannot rewrite history")

    sub.add_parser("wrap-key")
    cal = sub.add_parser("calibrate")
    cal.add_argument("--tenant", default=None)
    cal.add_argument("--target-frr", type=float, default=0.01)

    sub.add_parser("purge", help="delete transactions and what derives from them older than AMFA_RETENTION_DAYS (90)")
    rc = sub.add_parser("risk-candidate", help="the candidate risk model: it scores every payment silently and decides nothing")
    rcs = rc.add_subparsers(dest="action", required=True)
    rcs.add_parser("show")
    rcs.add_parser("set").add_argument("version")
    rcs.add_parser("clear")
    rcs.add_parser("preview")
    rcs.add_parser("promote").add_argument("--confirm-drops", action="store_true")
    bg = sub.add_parser("band-gates", help="grade every amount band of a bank now (rules against model) and print where each stands")
    bg.add_argument("--tenant", required=True)

    rs = sub.add_parser("reseal")
    rs.add_argument("--factor", choices=["face", "voice", "ueba"], default=None,
                    help="re-seal a factor service's template store (run in that service's container)")

    bm = sub.add_parser("bands-move", help="move band rows, band states and candidate progress from the core's database into "
                                           "the band service's own (AMFA_BANDS_DB_URL); once, when the service is turned on")
    bm.add_argument("--dry-run", action="store_true", help="count what the core holds, change nothing")
    sv = sub.add_parser("serve")
    sv.add_argument("service", choices=sorted(APPS))
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int)
    sv.add_argument("--workers", type=int, default=1)

    a = p.parse_args(argv)

    if a.cmd == "wrap-key":
        import base64
        import secrets
        from amfa.common.keyprovider import vault_wrap
        addr, token, key = os.getenv("AMFA_VAULT_ADDR"), os.getenv("AMFA_VAULT_TOKEN"), os.getenv("AMFA_VAULT_KEY")
        if not (addr and token and key):
            sys.exit("set AMFA_VAULT_ADDR, AMFA_VAULT_TOKEN and AMFA_VAULT_KEY")
        print(vault_wrap(base64.b64encode(secrets.token_bytes(32)).decode(), addr=addr, token=token, key=key))
        return

    if a.cmd == "reseal" and a.factor:
        print(json.dumps(_reseal_factor(a.factor)))
        return

    if a.cmd == "serve":
        import uvicorn
        from amfa import config
        config.get()
        uvicorn.run(APPS[a.service], host=a.host, port=a.port or PORTS[a.service],
                    workers=a.workers, proxy_headers=False, server_header=False,
                    date_header=False, log_config=None)
        return

    from amfa.common import logs
    from amfa.core import audit, db, proofs
    from amfa.core.api_admin import create_api_key, create_operator, create_tenant
    from amfa.core.auth import DEFAULT_SCOPES
    logs.setup("cli", "WARNING")
    db.migrate()

    if a.cmd == "reseal":
        print(json.dumps(_reseal_core()))
        return

    if a.cmd == "bands-move":
        from amfa.bands.move import MoveError, move
        try:
            print(json.dumps(move(a.dry_run), indent=2))
        except MoveError as e:
            sys.exit(str(e))
        return

    if a.cmd == "calibrate":
        from amfa.core.calibration import report
        print(json.dumps(report(a.tenant, a.target_frr), indent=2))
        return

    if a.cmd == "migrate":
        if a.grant_app_role:
            import re
            from sqlalchemy import text
            if not db.is_postgres():
                sys.exit("--grant-app-role applies to PostgreSQL only")
            if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", a.grant_app_role):
                sys.exit("invalid role name")
            r = a.grant_app_role
            with db.engine().begin() as c:
                c.execute(text(f"GRANT USAGE ON SCHEMA public TO {r}"))
                c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}"))
                c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}"))
                c.execute(text(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {r}"))
                for table in ("decision", "security_event", "attempt"):
                    c.execute(text(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {r}"))
        print(json.dumps({"migrated": True, "schemaVersion": db.SCHEMA_VERSION,
                          "appRole": a.grant_app_role}))
        return

    if a.cmd == "init":
        if not proofs.jwks()["keys"]:
            proofs.rotate_key("cli")
        from sqlalchemy import select
        with db.session() as s:
            exists = s.execute(select(db.Operator).where(db.Operator.role == "platform_admin")).scalars().first()
        if exists:
            print(json.dumps({"initialised": True, "note": "a platform admin already exists"}))
            return
        pw = _password("Platform admin password (14+ chars): ")
        if len(pw) < 14:
            sys.exit("password must be at least 14 characters")
        import uuid
        from amfa.common import crypto
        oid = f"op_{uuid.uuid4().hex[:16]}"
        with db.session() as s:
            s.add(db.Operator(id=oid, tenant_id=None, email=a.admin_email.lower(),
                              password_hash=crypto.hash_password(pw), role="platform_admin"))
        audit.record("cli", "operator.create", oid, {"role": "platform_admin"})
        print(json.dumps({"initialised": True, "platformAdmin": a.admin_email.lower(),
                          "next": "sign in at /admin and enrol an authenticator"}))
    elif a.cmd == "purge":
        from amfa.core import retention
        with db.session() as s:
            print(json.dumps(retention.purge(s), indent=2))
    elif a.cmd == "risk-candidate":
        from amfa.engine.risk import scorer
        if a.action == "set":
            try:
                scorer.set_candidate(a.version)
            except scorer.RiskModelError as e:
                raise SystemExit(f"not installed: {e}")
            audit.record("cli", "risk.candidate.set", a.version, {})
        elif a.action == "clear":
            if scorer.clear_candidate():
                audit.record("cli", "risk.candidate.clear", "-", {})
        elif a.action in ("preview", "promote"):
            from amfa.core import band_gate
            try:
                if a.action == "preview":
                    with db.session() as s:
                        print(json.dumps(band_gate.promotion_preview(s), indent=2))
                else:
                    print(json.dumps(band_gate.promote("cli", a.confirm_drops), indent=2))
            except (band_gate.GateError, scorer.RiskModelError) as e:
                raise SystemExit(f"not done: {getattr(e, 'message', e)}")
            return
        try:
            active = scorer.active().version
        except scorer.RiskModelError:
            active = None
        print(json.dumps({"active": active, "candidate": scorer.candidate_version()}, indent=2))
    elif a.cmd == "band-gates":
        from amfa.core import band_gate
        from amfa.engine.policy import TenantPolicy
        with db.session() as s:
            t = s.get(db.Tenant, a.tenant)
            if t is None:
                raise SystemExit(f"no tenant {a.tenant}")
            policy = TenantPolicy.model_validate(t.policy)
            if not policy.collection.enabled:
                raise SystemExit("the band switch is off for this bank (policy collection.enabled)")
            band_gate.evaluate(s, t.id, policy)
            print(json.dumps(band_gate.view(s, t.id, policy), indent=2))
    elif a.cmd == "tenant":
        print(json.dumps(create_tenant(a.name, a.slug, "cli"), indent=2))
    elif a.cmd == "operator":
        pw = _password(f"Password for {a.email} (14+ chars): ")
        print(json.dumps(create_operator(a.email, a.role, pw, a.tenant, "cli"), indent=2))
    elif a.cmd == "apikey":
        scopes = a.scopes.split(",") if a.scopes else list(DEFAULT_SCOPES)
        print(json.dumps(create_api_key(a.tenant, a.name, scopes, "cli"), indent=2))
    elif a.cmd == "signing-key":
        print(json.dumps({"activeKid": proofs.rotate_key("cli")}))
    elif a.cmd == "audit":
        r = audit.verify()
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["intact"] else 2)


def _core_sealed():
    from amfa.core import db
    return [
        (db.ApiKey, "secret_sealed", lambda r: f"apikey|{r.id}", "apikey"),
        (db.Webhook, "secret_sealed", lambda r: f"webhook|{r.id}", "webhook"),
        (db.Operator, "totp_sealed", lambda r: f"optotp|{r.id}", "totp"),
        (db.Customer, "contact_sealed", lambda r: f"contact|{r.id}", "contact"),
        (db.TotpCredential, "secret_sealed", lambda r: f"totp|{r.customer_id}", "totp"),
        (db.SigningKey, "private_sealed", lambda r: f"signing|{r.kid}", "signing"),
    ]


def _reseal_core() -> dict:
    from sqlalchemy import select
    from amfa.common import crypto
    from amfa.core import audit, db
    counts: dict[str, int] = {}
    with db.session() as s:
        for model, col, aad, purpose in _core_sealed():
            n = 0
            for row in s.execute(select(model)).scalars():
                val = getattr(row, col)
                if val and crypto.needs_rotation(val):
                    setattr(row, col, crypto.seal(crypto.open_(val, aad(row), purpose), aad(row), purpose))
                    n += 1
            counts[model.__tablename__] = n
    audit.record("cli", "master_key.reseal", "core", counts)
    return {"resealed": counts}


def _reseal_factor(factor: str) -> dict:
    from sqlalchemy import select, update
    from amfa.common import crypto
    from amfa.factors.base import store_for
    st = store_for(factor)
    n = 0
    with st.engine.begin() as c:
        for r in c.execute(select(st.templates)).mappings().all():
            if crypto.needs_rotation(r["sealed"]):
                aad = f"{st.factor}|{r['tenant_id']}|{r['user_id']}"
                fresh = crypto.seal(crypto.open_(r["sealed"], aad, f"template:{st.factor}"), aad,
                                    f"template:{st.factor}")
                c.execute(update(st.templates).where(st.templates.c.tenant_id == r["tenant_id"],
                                                      st.templates.c.user_id == r["user_id"])
                          .values(sealed=fresh))
                n += 1
        stale = [r.id for r in c.execute(select(st.probes.c.id, st.probes.c.vector)).all()
                 if r.vector and crypto.needs_rotation(r.vector.decode())]
        if stale:
            c.execute(st.probes.delete().where(st.probes.c.id.in_(stale)))
    return {"factor": factor, "resealedTemplates": n, "droppedProbes": len(stale)}


if __name__ == "__main__":
    main()
