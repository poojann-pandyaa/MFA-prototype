from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from amfa import config

DEFAULT_MODEL = Path(config.get().local_model)

CTX_TOKENS = int(os.getenv("AMFA_LOCAL_CTX", "3072"))
THREADS = int(os.getenv("AMFA_LOCAL_THREADS", str(min(os.cpu_count() or 4, 4))))

_LLM = None
_LOAD_MS = 0

_LOCK = threading.RLock()


def model_path() -> Path:
    return DEFAULT_MODEL


def available() -> bool:
    try:
        import llama_cpp
    except ImportError:
        return False
    return DEFAULT_MODEL.exists()


def unavailable_reason() -> str:
    try:
        import llama_cpp
    except ImportError:
        return "llama-cpp-python is not installed"
    if not DEFAULT_MODEL.exists():
        return f"no model file at {DEFAULT_MODEL}"
    return ""


def load(verbose: bool = False):
    global _LLM, _LOAD_MS
    with _LOCK:
        if _LLM is not None:
            return _LLM
        return _load_locked(verbose)


def _load_locked(verbose: bool = False):
    global _LLM, _LOAD_MS
    from llama_cpp import Llama

    from amfa.common.artifacts import verify_llm
    verify_llm(DEFAULT_MODEL)
    started = time.perf_counter()
    _LLM = Llama(
        model_path=str(DEFAULT_MODEL),
        n_ctx=CTX_TOKENS,
        n_threads=THREADS,
        n_batch=512,
        n_threads_batch=THREADS,
        seed=11,
        logits_all=False,
        verbose=verbose,
    )
    from llama_cpp import LlamaRAMCache
    _LLM.set_cache(LlamaRAMCache(capacity_bytes=512 * 1024 * 1024))
    _LOAD_MS = int((time.perf_counter() - started) * 1000)
    return _LLM


def load_ms() -> int:
    return _LOAD_MS


def warm_cache(system: str, prompt: str, schema: dict) -> int:
    started = time.perf_counter()
    try:
        complete(system, prompt, schema, max_tokens=8)
    except Exception:
        pass
    return int((time.perf_counter() - started) * 1000)


def describe() -> dict:
    return {
        "file": DEFAULT_MODEL.name,
        "sizeMb": round(DEFAULT_MODEL.stat().st_size / 1e6) if DEFAULT_MODEL.exists() else 0,
        "contextTokens": CTX_TOKENS,
        "threads": THREADS,
        "loaded": _LLM is not None,
        "loadMs": _LOAD_MS,
    }


def complete(system: str, prompt: str, schema: dict,
             max_tokens: int = 120) -> tuple[str, dict]:
    llm = load()
    started = time.perf_counter()
    with _LOCK:
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object", "schema": schema},
            temperature=0.0,
            top_p=1.0,
            max_tokens=max_tokens,
        )
    ms = int((time.perf_counter() - started) * 1000)
    text = out["choices"][0]["message"]["content"]
    u = out.get("usage", {}) or {}
    return text, {
        "promptTokens": u.get("prompt_tokens", 0),
        "outputTokens": u.get("completion_tokens", 0),
        "inferenceMs": ms,
    }
