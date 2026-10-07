from __future__ import annotations

import threading
import time
from functools import lru_cache
from typing import Protocol

from amfa import config


class KV(Protocol):
    def add_if_absent(self, key: str, ttl_s: int) -> bool: ...
    def incr(self, key: str, ttl_s: int) -> int: ...


class MemoryKV:
    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._ops = 0

    def _sweep(self, now: float) -> None:
        self._ops += 1
        if self._ops % 1024:
            return
        for k in [k for k, (_, exp) in self._data.items() if exp <= now]:
            del self._data[k]

    def add_if_absent(self, key: str, ttl_s: int) -> bool:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            cur = self._data.get(key)
            if cur and cur[1] > now:
                return False
            self._data[key] = (1, now + ttl_s)
            return True

    def incr(self, key: str, ttl_s: int) -> int:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            cur = self._data.get(key)
            if not cur or cur[1] <= now:
                self._data[key] = (1, now + ttl_s)
                return 1
            self._data[key] = (cur[0] + 1, cur[1])
            return cur[0] + 1


class RedisKV:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, socket_timeout=1.0)

    def add_if_absent(self, key: str, ttl_s: int) -> bool:
        return bool(self._r.set(f"amfa:{key}", 1, nx=True, ex=ttl_s))

    def incr(self, key: str, ttl_s: int) -> int:
        k = f"amfa:{key}"
        pipe = self._r.pipeline()
        pipe.incr(k)
        pipe.expire(k, ttl_s, nx=True)
        n, _ = pipe.execute()
        return int(n)


@lru_cache(maxsize=1)
def get() -> KV:
    url = config.get().redis_url
    return RedisKV(url) if url else MemoryKV()
