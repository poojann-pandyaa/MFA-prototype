from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from amfa import config
from amfa.common import crypto
from amfa.core import db

log = logging.getLogger("amfa.webhooks")

EVENTS = {"transaction.authorized", "transaction.denied", "transaction.review_required",
          "transaction.expired", "review.resolved", "security.event", "customer.enrolled",
          "customer.factor_replaced"}
BACKOFF_S = [30, 120, 600, 3600, 6 * 3600, 12 * 3600]
MAX_ATTEMPTS = len(BACKOFF_S) + 1


class UnsafeWebhook(ValueError):
    pass


def resolve_safe(url: str) -> tuple[str, str]:
    s = config.get()
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise UnsafeWebhook("webhook URL must be an absolute http(s) URL")
    if s.production and u.scheme != "https":
        raise UnsafeWebhook("webhook URL must use https")
    if u.username or u.password:
        raise UnsafeWebhook("credentials in the webhook URL are not allowed")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhook("webhook host does not resolve") from exc
    ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not s.allow_private_webhooks and any(not ip.is_global or ip.is_multicast for ip in ips):
        raise UnsafeWebhook("webhook host resolves to a non-public address")
    return u.hostname, str(ips[0])


def check_url(url: str, resolve: bool = True) -> None:
    if resolve:
        resolve_safe(url)
    else:
        u = urlparse(url)
        if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password:
            raise UnsafeWebhook("webhook URL must be an absolute http(s) URL without credentials")


def emit(s, tenant_id: str, event_type: str, data: dict) -> None:
    assert event_type in EVENTS, event_type
    hooks = s.execute(select(db.Webhook).where(db.Webhook.tenant_id == tenant_id,
                                               db.Webhook.active.is_(True))).scalars().all()
    for h in hooks:
        if event_type in h.events or "*" in h.events:
            s.add(db.WebhookDelivery(
                id=f"whd_{uuid.uuid4().hex[:16]}", webhook_id=h.id, tenant_id=tenant_id,
                event_type=event_type,
                payload={"id": f"evt_{uuid.uuid4().hex[:16]}", "type": event_type,
                         "createdAt": int(time.time()), "tenantId": tenant_id, "data": data}))


def sign(secret: str, ts: str, body: bytes) -> str:
    return "v1=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


SEND_DEADLINE_S = 15.0
LEASE_S = 60.0
_POOL = None


def _send(payload: dict, url: str, secret: str, delivery_id: str) -> tuple[bool, Optional[int], str]:
    try:
        host, ip = resolve_safe(url)
    except UnsafeWebhook as exc:
        return False, None, str(exc)[:200]
    u = urlparse(url)
    target = u._replace(netloc=(f"[{ip}]" if ":" in ip else ip) + (f":{u.port}" if u.port else "")).geturl()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ts = str(int(time.time()))
    headers = {"content-type": "application/json", "user-agent": "amfa-webhooks/1.0", "host": host,
               "x-amfa-webhook-id": delivery_id, "x-amfa-webhook-timestamp": ts,
               "x-amfa-webhook-signature": sign(secret, ts, body)}
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0), follow_redirects=False) as c:
            with c.stream("POST", target, content=body, headers=headers,
                          extensions={"sni_hostname": host}) as r:
                code = r.status_code
        return 200 <= code < 300, code, "" if code < 300 else f"HTTP {code}"
    except httpx.HTTPError as exc:
        return False, None, type(exc).__name__


def dispatch_once(batch: int = 50) -> int:
    from concurrent.futures import ThreadPoolExecutor, wait
    global _POOL
    now = time.time()
    jobs = []
    with db.session() as s:
        q = (select(db.WebhookDelivery).where(db.WebhookDelivery.status.in_(("PENDING", "SENDING")),
                                              db.WebhookDelivery.next_attempt_at <= now)
             .order_by(db.WebhookDelivery.next_attempt_at).limit(batch))
        if db.is_postgres():
            q = q.with_for_update(skip_locked=True)
        for d in s.execute(q).scalars().all():
            hook = s.get(db.Webhook, d.webhook_id)
            if hook is None or not hook.active:
                d.status = "CANCELLED"
                continue
            d.status, d.next_attempt_at = "SENDING", now + LEASE_S
            secret = crypto.open_(hook.secret_sealed, f"webhook|{hook.id}", "webhook").decode()
            jobs.append((d.id, dict(d.payload), hook.url, secret))
    if not jobs:
        return 0
    _POOL = _POOL or ThreadPoolExecutor(max_workers=8, thread_name_prefix="amfa-wh")
    futures = {_POOL.submit(_send, p, url, sec, did): did for did, p, url, sec in jobs}
    done, _ = wait(futures, timeout=SEND_DEADLINE_S)
    results = {futures[f]: f.result() for f in done}
    sent = 0
    with db.session() as s:
        for did, *_ in jobs:
            d = s.get(db.WebhookDelivery, did)
            ok, code, err = results.get(did, (False, None, "DEADLINE_EXCEEDED"))
            d.attempts += 1
            d.last_status, d.last_error = code, err or None
            if ok:
                d.status = "DELIVERED"
                sent += 1
            elif d.attempts >= MAX_ATTEMPTS:
                d.status = "FAILED"
            else:
                d.status, d.next_attempt_at = "PENDING", time.time() + BACKOFF_S[d.attempts - 1]
    return sent


class Dispatcher(threading.Thread):
    def __init__(self, interval_s: float = 2.0):
        super().__init__(daemon=True, name="amfa-webhooks")
        self.interval_s = interval_s
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                dispatch_once()
            except Exception:
                log.exception("webhook dispatch failed")

    def stop(self) -> None:
        self._stop.set()
