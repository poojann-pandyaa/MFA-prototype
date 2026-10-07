from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Optional, Protocol

from amfa import config

from .context import FRICTION_ORDER, SELECTABLE, Appetite, Band, Context, Factor, Plan
from .prompt import SYSTEM, render_prompt

SCHEMA = {
    "factors":        {"type": "array", "items": [f.value for f in SELECTABLE], "maxItems": 3},
    "order":          {"type": "array", "items": [f.value for f in Factor], "maxItems": 4, "optional": True},
    "reasoning":      {"type": "string", "maxLength": 300, "optional": True},
    "counterfactual": {"type": "string", "maxLength": 300, "optional": True},
}


def agent_timeout() -> float:
    return config.get().agent_timeout_s


class SchemaError(ValueError):
    pass


def validate(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError("top level is not an object")
    out: dict = {}
    for key, rule in SCHEMA.items():
        if key not in raw:
            if rule.get("optional"):
                continue
            raise SchemaError(f"missing required key: {key}")
        val = raw[key]
        if rule["type"] == "array":
            if not isinstance(val, list):
                raise SchemaError(f"{key} is not an array")
            if len(val) > rule["maxItems"]:
                raise SchemaError(f"{key} exceeds maxItems")
            for item in val:
                if item not in rule["items"]:
                    raise SchemaError(f"{key} contains value outside the enumeration: {item!r}")
            out[key] = val
        else:
            if not isinstance(val, str):
                raise SchemaError(f"{key} is not a string")
            out[key] = val[: rule["maxLength"]]
    return out


class AgentClient(Protocol):
    name: str
    def propose(self, system: str, prompt: str) -> Any: ...
    def system_prompt(self) -> str: ...
    def render(self, ctx, baseline) -> str: ...


class SimulatedAgent:
    def __init__(self, mode: str = "normal", seed: int = 7):
        self.mode = mode
        self.name = f"simulated:{mode}"
        self._rng = random.Random(seed)

    def system_prompt(self) -> str:
        return SYSTEM

    def render(self, ctx, baseline) -> str:
        return render_prompt(ctx, baseline)

    def propose(self, system: str, prompt: str) -> Any:
        if self.mode == "timeout":
            time.sleep(agent_timeout() + 0.5)
            return {"factors": []}
        if self.mode == "malformed":
            return "Certainly! Here is my recommendation: require face only."
        if self.mode == "outofrange":
            return {"factors": ["RETINA"], "reasoning": "invented a factor"}
        if self.mode == "empty":
            return {"factors": [], "reasoning": "no additional assurance judged necessary"}
        if self.mode == "removal":
            return {"factors": [], "remove": ["FACE", "VOICE"],
                    "reasoning": "attempting to strip the baseline"}
        facts = _parse_prompt_facts(prompt)
        factors: list[str] = []
        if facts.get("behavioural_band") in {"MEDIUM", "HIGH"}:
            factors.append("UEBA")
        if facts.get("device_known") == "false" or facts.get("location_known") == "false":
            factors.append("FACE")
        if facts.get("amount_band") == "HIGH" and facts.get("is_new_beneficiary") == "true":
            factors.append("VOICE")
        if facts.get("hour_unusual") == "true" and "UEBA" not in factors:
            factors.append("UEBA")
        order = ["OTP"] + [f for f in ("UEBA", "FACE", "VOICE") if f in factors]
        return {"factors": factors[:3], "order": order}


class LocalAgent:

    def __init__(self):
        from . import local_llm
        if not local_llm.available():
            raise RuntimeError(local_llm.unavailable_reason())
        self._llm = local_llm
        self.name = f"local:{local_llm.model_path().stem}"
        self.last_usage: dict[str, Any] = {}

    def warm(self) -> int:
        from .llm_prompt import RESPONSE_SCHEMA, SYSTEM as LLM_SYSTEM
        self._llm.load()
        self._llm.warm_cache(LLM_SYSTEM, "RISK SIGNALS PRESENT:\n  - none\n\n"
                             "BASELINE ALREADY REQUIRES: OTP\nFACTORS YOU MAY ADD: UEBA, FACE, VOICE",
                             RESPONSE_SCHEMA)
        return self._llm.load_ms()

    def system_prompt(self) -> str:
        from .llm_prompt import SYSTEM as LLM_SYSTEM
        return LLM_SYSTEM

    def render(self, ctx, baseline) -> str:
        from .llm_prompt import render as llm_render
        return llm_render(ctx, baseline)

    def propose(self, system: str, prompt: str) -> Any:
        from .llm_prompt import RESPONSE_SCHEMA
        text, usage = self._llm.complete(system, prompt, RESPONSE_SCHEMA)
        self.last_usage = usage
        return text


class ClaudeAgent:

    def __init__(self, model: Optional[str] = None):
        import os
        import anthropic
        self.client = anthropic.Anthropic(max_retries=0)
        self.model = model or os.getenv("AMFA_AGENT_MODEL", "claude-opus-5")
        self.name = f"claude:{self.model}"
        self.last_usage: dict[str, Any] = {}

    def system_prompt(self) -> str:
        from .llm_prompt import SYSTEM as LLM_SYSTEM
        return LLM_SYSTEM

    def render(self, ctx, baseline) -> str:
        from .llm_prompt import render as llm_render
        return llm_render(ctx, baseline)

    def propose(self, system: str, prompt: str) -> Any:
        from .llm_prompt import RESPONSE_SCHEMA
        response = self.client.with_options(timeout=agent_timeout()).messages.create(
            model=self.model, max_tokens=700,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        u = response.usage
        self.last_usage = {"inputTokens": u.input_tokens, "outputTokens": u.output_tokens,
                           "cacheRead": getattr(u, "cache_read_input_tokens", 0) or 0}
        return "".join(b.text for b in response.content if b.type == "text")


_AGENT_LOCK = threading.Lock()
_AGENT: dict[str, Any] = {}


def build_agent(mode: Optional[str] = None) -> Optional[AgentClient]:
    if mode not in (None, "normal"):
        return SimulatedAgent(mode)
    backend = config.get().agent_backend
    if backend == "off":
        return None
    if backend == "simulated":
        return SimulatedAgent("normal")
    with _AGENT_LOCK:
        if backend in _AGENT:
            return _AGENT[backend]
        agent: Optional[AgentClient] = None
        try:
            agent = LocalAgent() if backend == "local" else ClaudeAgent() if backend == "claude" else None
        except Exception as exc:
            _AGENT["error"] = f"{type(exc).__name__}: {exc}"[:200]
        _AGENT[backend] = agent
        return agent


def backend_status() -> dict:
    backend = config.get().agent_backend
    agent = build_agent()
    return {"requested": backend, "active": agent.name if agent else "rules-only",
            "error": _AGENT.get("error"), "timeoutS": agent_timeout()}


def select_with_agent(ctx: Context, baseline: Plan, client: AgentClient) -> Plan:
    from .availability import available
    from .rationale import counterfactual, reasoning

    started = time.perf_counter()
    try:
        raw = _call_with_deadline(client, client.system_prompt(), client.render(ctx, baseline),
                                  agent_timeout())
        parsed = validate(raw)
    except Exception as exc:
        baseline.agentError = f"{type(exc).__name__}: {exc}"[:200]
        baseline.agentName = client.name
        baseline.source = "rules"
        baseline.reasoning = reasoning(ctx, baseline)
        baseline.counterfactual = counterfactual(ctx)
        baseline.latencyMs = int((time.perf_counter() - started) * 1000)
        return baseline

    proposed = [Factor(f) for f in parsed["factors"]]
    merged = list(baseline.required)
    added: list[Factor] = []
    for f in proposed:
        if f in merged or f in added:
            continue
        if not available(f, ctx):
            continue
        if len(added) >= ctx.org.agentMaxAdditions:
            baseline.agentCapped.append(f)
            continue
        added.append(f)
    merged += added

    baseline.required = merged
    baseline.order = _safe_order(parsed.get("order"), merged)
    baseline.agentAdded = added
    baseline.source = "agent"
    baseline.agentName = client.name
    baseline.agentUsage = dict(getattr(client, "last_usage", {}) or {})
    baseline.reasoning = reasoning(ctx, baseline)
    baseline.counterfactual = counterfactual(ctx)
    baseline.latencyMs = int((time.perf_counter() - started) * 1000)
    return baseline


MARGINAL_RATIO = 1.15


def marginal(score: Optional[float], threshold: Optional[float]) -> bool:
    if score is None or threshold is None or threshold <= 0:
        return False
    return threshold <= score < threshold * MARGINAL_RATIO


def reassess(ctx: Context, required: list[Factor], risk_band: Band, results: list[dict],
             signals: Optional[list[str]] = None) -> tuple[list[Factor], str]:
    from .availability import available

    signals = list(signals or [])
    graded = [r for r in results if r.get("status") == "PASSED" and r.get("factor") != Factor.OTP.value]
    marginals = [r for r in graded if marginal(r.get("score"), r.get("threshold"))]
    need = 2 if ctx.org.appetite is not Appetite.CONSERVATIVE else 1
    if len(marginals) >= need or (marginals and risk_band is Band.HIGH):
        names = ", ".join(f"{r['factor']} {r['score']:.2f}" for r in marginals)
        signals.append(f"marginal passes ({names}) in a {risk_band.value} band")
    if not signals:
        return [], ""

    candidates = [f for f in (Factor.FACE, Factor.VOICE, Factor.UEBA)
                  if f not in required and available(f, ctx)]
    if not candidates:
        return [], "; ".join(signals) + "; no further factor available"
    pick = candidates[0]
    return [pick], "; ".join(signals) + f"; appended {pick.value}"


def _call_with_deadline(client: AgentClient, system: str, prompt: str, deadline: float) -> Any:
    box: dict[str, Any] = {}

    def run():
        try:
            box["ok"] = client.propose(system, prompt)
        except Exception as exc:
            box["err"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(deadline)
    if th.is_alive():
        raise TimeoutError(f"agent exceeded {deadline}s deadline")
    if "err" in box:
        raise box["err"]
    return box["ok"]


def _safe_order(proposed: Optional[list[str]], required: list[Factor]) -> list[Factor]:
    out: list[Factor] = []
    for name in proposed or []:
        try:
            f = Factor(name)
        except ValueError:
            continue
        if f in required and f not in out:
            out.append(f)
    for f in FRICTION_ORDER:
        if f in required and f not in out:
            out.append(f)
    if Factor.OTP in out:
        out.remove(Factor.OTP)
        out.insert(0, Factor.OTP)
    return out


def _parse_prompt_facts(prompt: str) -> dict[str, str]:
    facts = {}
    for line in prompt.splitlines():
        if ":" in line and line.startswith("  "):
            k, _, v = line.strip().partition(":")
            facts[k.strip()] = v.strip()
    return facts
