from __future__ import annotations

import json
import logging
import re
import sys
import time

_REDACT_KEYS = re.compile(r"(secret|token|password|otp|code|signature|authorization|"
                          r"image|frame|audio|sample|key|cookie|ip)$", re.I)
_TOKEN_SHAPES = re.compile(
    r"(ak_(live|test)_[A-Za-z0-9_-]{8,}|sk_[A-Za-z0-9_-]{16,}|ct_[A-Za-z0-9_-]{16,}|"
    r"et_[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+|"
    r"[A-Za-z0-9+/]{200,}={0,2})")

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def _scrub(value):
    if isinstance(value, str):
        return _TOKEN_SHAPES.sub("[redacted]", value)
    if isinstance(value, dict):
        return {k: ("[redacted]" if _REDACT_KEYS.search(str(k)) else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value[:50]]
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": _scrub(record.getMessage()),
        }
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                out[k] = "[redacted]" if _REDACT_KEYS.search(k) else _scrub(v)
        if record.exc_info:
            out["exc"] = _scrub(self.formatException(record.exc_info))[-4000:]
        return json.dumps(out, default=str)


def setup(service: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter(service))
    root.addHandler(h)
    root.setLevel(level)
    for noisy in ("uvicorn.access", "httpx", "httpx2", "httpcore", "httpcore2", "speechbrain", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
