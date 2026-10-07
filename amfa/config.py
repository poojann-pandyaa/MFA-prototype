from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("amfa.config")

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in (_env(name, default) or "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    env: str
    role: str
    public_url: str
    data_dir: Path
    models_dir: Path
    database_url: str

    master_keys: dict[str, bytes] = field(repr=False)
    active_key_version: str = "v1"
    index_key_version: str = "v1"
    internal_token: str = field(default="", repr=False)
    ip_salt: str = field(default="", repr=False)

    face_url: str = "http://127.0.0.1:8101"
    voice_url: str = "http://127.0.0.1:8102"
    ueba_url: str = "http://127.0.0.1:8103"
    factor_timeout_s: float = 20.0

    agent_backend: str = "local"
    local_model: Path = ROOT / "models" / "llm" / "qwen2.5-3b-instruct-q4_k_m.gguf"
    agent_timeout_s: float = 8.0

    redis_url: str | None = None
    geoip_db: Path | None = None
    cors_origins: list[str] = field(default_factory=list)
    trusted_proxies: list[str] = field(default_factory=list)
    allow_private_webhooks: bool = False

    session_ttl_max_s: int = 300
    proof_ttl_max_s: int = 300
    enrollment_link_ttl_max_s: int = 86_400

    admin_totp: bool = True
    otp_provider: str = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = field(default=None, repr=False)
    smtp_from: str | None = None

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def active_key(self) -> bytes:
        return self.master_keys[self.active_key_version]


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ConfigError("master key is not valid base64") from exc
    if len(key) != 32:
        raise ConfigError(f"master key must decode to 32 bytes, got {len(key)}")
    return key


def _parse_keyring(raw: str) -> tuple[dict[str, bytes], str]:
    ring: dict[str, bytes] = {}
    active = None
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            ver, b64 = part.split(":", 1)
        else:
            ver, b64 = "v1", part
        ring[ver.strip()] = _decode_key(b64.strip())
        active = active or ver.strip()
    if not ring or active is None:
        raise ConfigError("no master key configured")
    return ring, active


def _index_version(ring: dict[str, bytes]) -> str:
    v = _env("AMFA_INDEX_KEY_VERSION") or list(ring)[-1]
    if v not in ring:
        raise ConfigError(f"AMFA_INDEX_KEY_VERSION={v} is not in the master key ring")
    return v


def _dev_secrets(data_dir: Path) -> dict:
    path = data_dir / "dev-secrets.json"
    if path.exists():
        return json.loads(path.read_text())
    data_dir.mkdir(parents=True, exist_ok=True)
    values = {
        "master_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "internal_token": secrets.token_urlsafe(48),
        "ip_salt": secrets.token_urlsafe(32),
    }
    path.write_text(json.dumps(values, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    log.warning("generated development secrets at %s -- never use these in production", path)
    return values


def load() -> Settings:
    env = (_env("AMFA_ENV", "development") or "development").lower()
    if env not in ("development", "production", "test"):
        raise ConfigError(f"AMFA_ENV must be development, production or test, got {env!r}")

    data_dir = Path(_env("AMFA_DATA_DIR", str(ROOT / "data"))).resolve()
    models_dir = Path(_env("AMFA_MODELS_DIR", str(ROOT / "models"))).resolve()
    database_url = _env("AMFA_DATABASE_URL", f"sqlite:///{data_dir / 'core.db'}")

    from amfa.common.keyprovider import KeyProviderError, resolve_keyring
    try:
        raw_keys = resolve_keyring()
    except KeyProviderError as exc:
        raise ConfigError(str(exc)) from None
    internal = _env("AMFA_INTERNAL_TOKEN")
    salt = _env("AMFA_IP_SALT")

    if env != "production" and not (raw_keys and internal and salt):
        dev = _dev_secrets(data_dir)
        raw_keys = raw_keys or dev["master_key"]
        internal = internal or dev["internal_token"]
        salt = salt or dev["ip_salt"]

    if not raw_keys:
        raise ConfigError("AMFA_MASTER_KEY (or AMFA_MASTER_KEYS) is required")
    ring, active = _parse_keyring(raw_keys)

    geoip = _env("AMFA_GEOIP_DB")
    role = (_env("AMFA_ROLE", "core") or "core").lower()
    if role not in ("core", "factor"):
        raise ConfigError("AMFA_ROLE must be core or factor")
    s = Settings(
        env=env,
        role=role,
        public_url=(_env("AMFA_PUBLIC_URL", "http://127.0.0.1:8000") or "").rstrip("/"),
        data_dir=data_dir,
        models_dir=models_dir,
        database_url=database_url,
        master_keys=ring,
        active_key_version=active,
        index_key_version=_index_version(ring),
        internal_token=internal or "",
        ip_salt=salt or "",
        face_url=_env("AMFA_FACE_URL", "http://127.0.0.1:8101"),
        voice_url=_env("AMFA_VOICE_URL", "http://127.0.0.1:8102"),
        ueba_url=_env("AMFA_UEBA_URL", "http://127.0.0.1:8103"),
        factor_timeout_s=float(_env("AMFA_FACTOR_TIMEOUT_S", "20")),
        agent_backend=(_env("AMFA_AGENT_BACKEND", "local") or "local").lower(),
        local_model=Path(_env("AMFA_LOCAL_MODEL",
                              str(models_dir / "llm" / "qwen2.5-3b-instruct-q4_k_m.gguf"))),
        agent_timeout_s=float(_env("AMFA_AGENT_TIMEOUT_S", "8")),
        redis_url=_env("AMFA_REDIS_URL"),
        geoip_db=Path(geoip) if geoip else None,
        cors_origins=_list("AMFA_CORS_ORIGINS"),
        trusted_proxies=_list("AMFA_TRUSTED_PROXIES"),
        allow_private_webhooks=_bool("AMFA_ALLOW_PRIVATE_WEBHOOKS", env != "production"),
        session_ttl_max_s=int(_env("AMFA_SESSION_TTL_MAX_S", "300")),
        proof_ttl_max_s=int(_env("AMFA_PROOF_TTL_MAX_S", "300")),
        enrollment_link_ttl_max_s=int(_env("AMFA_ENROLLMENT_LINK_TTL_MAX_S", "86400")),
        admin_totp=(_env("AMFA_ADMIN_TOTP", "required") or "required").lower() != "off",
        otp_provider=(_env("AMFA_OTP_PROVIDER", "console") or "console").lower(),
        smtp_host=_env("AMFA_SMTP_HOST"),
        smtp_port=int(_env("AMFA_SMTP_PORT", "587")),
        smtp_user=_env("AMFA_SMTP_USER"),
        smtp_password=_env("AMFA_SMTP_PASSWORD"),
        smtp_from=_env("AMFA_SMTP_FROM"),
    )
    if s.production:
        _assert_production_safe(s)
    return s


def _assert_production_safe(s: Settings) -> None:
    problems: list[str] = []
    if len(s.internal_token) < 32:
        problems.append("AMFA_INTERNAL_TOKEN must be at least 32 characters")
    if not s.redis_url:
        problems.append("AMFA_REDIS_URL is required in production (shared replay and rate-limit state)")
    for ver, key in s.master_keys.items():
        if len(set(key)) < 8:
            problems.append(f"master key {ver} has implausibly low entropy")

    if s.role == "factor":
        if _env("AMFA_DATABASE_URL"):
            problems.append("factor services must not be given AMFA_DATABASE_URL (the core database)")
        if not any(_env(f"AMFA_{f}_DB_URL", "").startswith("postgresql") for f in ("FACE", "VOICE", "UEBA")):
            problems.append("set the factor service's own AMFA_<FACTOR>_DB_URL to PostgreSQL")
        if _env("AMFA_CORE_KEY_FINGERPRINT") and _env("AMFA_CORE_KEY_FINGERPRINT") == key_fingerprint(s):
            problems.append("factor services must use their own AMFA_MASTER_KEYS, not the core's")
    else:
        if not s.public_url.startswith("https://"):
            problems.append("AMFA_PUBLIC_URL must be https:// in production")
        if len(s.ip_salt) < 16:
            problems.append("AMFA_IP_SALT must be at least 16 characters")
        if s.database_url.startswith("sqlite") and not _bool("AMFA_ALLOW_SQLITE"):
            problems.append("SQLite is not supported in production; set AMFA_DATABASE_URL "
                            "to PostgreSQL (or AMFA_ALLOW_SQLITE=1 for a single-node pilot)")
        if "*" in s.cors_origins:
            problems.append("AMFA_CORS_ORIGINS may not contain a wildcard in production")
        if s.agent_backend == "simulated":
            problems.append("AMFA_AGENT_BACKEND=simulated is a test double, not a production backend")
        if s.otp_provider == "console":
            problems.append("AMFA_OTP_PROVIDER=console prints codes to the log; configure "
                            "webhook or smtp delivery (TOTP enrolment works regardless)")
    if problems:
        raise ConfigError("unsafe production configuration:\n  - " + "\n  - ".join(problems))


def key_fingerprint(s: Settings) -> str:
    import hashlib
    return hashlib.sha256(b"amfa-key-fp|" + s.active_key).hexdigest()[:16]


@lru_cache(maxsize=1)
def get() -> Settings:
    return load()


def reset_cache() -> None:
    get.cache_clear()
