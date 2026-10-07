from __future__ import annotations

import base64
import os

import httpx


class KeyProviderError(RuntimeError):
    pass


def vault_unwrap(ciphertext: str, *, addr: str, token: str, key: str, timeout: float = 5.0) -> str:
    r = httpx.post(f"{addr.rstrip('/')}/v1/transit/decrypt/{key}", json={"ciphertext": ciphertext},
                   headers={"X-Vault-Token": token}, timeout=timeout)
    if r.status_code != 200:
        raise KeyProviderError(f"Vault transit decrypt failed: HTTP {r.status_code}")
    plain_b64 = r.json()["data"]["plaintext"]
    raw = base64.b64decode(plain_b64)
    if len(raw) != 32:
        raise KeyProviderError("unwrapped master key is not 32 bytes")
    return base64.b64encode(raw).decode()


def vault_wrap(master_b64: str, *, addr: str, token: str, key: str, timeout: float = 5.0) -> str:
    r = httpx.post(f"{addr.rstrip('/')}/v1/transit/encrypt/{key}", json={"plaintext": master_b64},
                   headers={"X-Vault-Token": token}, timeout=timeout)
    if r.status_code != 200:
        raise KeyProviderError(f"Vault transit encrypt failed: HTTP {r.status_code}")
    return r.json()["data"]["ciphertext"]


def resolve_keyring() -> str | None:
    provider = (os.getenv("AMFA_KEY_PROVIDER") or "env").lower()
    if provider == "env":
        return os.getenv("AMFA_MASTER_KEYS") or os.getenv("AMFA_MASTER_KEY")
    if provider == "vault-transit":
        addr, token, key = os.getenv("AMFA_VAULT_ADDR"), os.getenv("AMFA_VAULT_TOKEN"), os.getenv("AMFA_VAULT_KEY")
        wrapped = os.getenv("AMFA_WRAPPED_MASTER_KEYS")
        if not (addr and token and key and wrapped):
            raise KeyProviderError("vault-transit needs AMFA_VAULT_ADDR, AMFA_VAULT_TOKEN, AMFA_VAULT_KEY "
                                   "and AMFA_WRAPPED_MASTER_KEYS")
        if os.getenv("AMFA_MASTER_KEYS"):
            raise KeyProviderError("with vault-transit, plaintext AMFA_MASTER_KEYS must not be set")
        parts = []
        for entry in wrapped.split(","):
            ver, _, ct = entry.strip().partition(":")
            if not ct.startswith("vault:"):
                raise KeyProviderError(f"wrapped key {ver} is not a Vault transit ciphertext")
            parts.append(f"{ver}:{vault_unwrap(ct, addr=addr, token=token, key=key)}")
        return ",".join(parts)
    raise KeyProviderError(f"unknown AMFA_KEY_PROVIDER {provider!r}")
