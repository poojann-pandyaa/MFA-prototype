"""
Keystroke dynamics.

Fixed-text approach: the user types the same phrase at enrollment and at
verification, so timings are compared like for like. Random text would mean
matching different key pairs against each other -- free-text keystroke
dynamics, a much harder problem needing far more data.

  Killourhy, K.S. & Maxion, R.A. (2009). "Comparing Anomaly-Detection
  Algorithms for Keystroke Dynamics." IEEE/IFIP DSN, 125-134.

Scaled Manhattan distance is used because that benchmark found it beat
thirteen more sophisticated alternatives.

Every attempt is scored, including wrong or empty ones. A requested
challenge that goes unanswered is evidence, not silence: the caller blends
the observed distance toward a typical impostor's distance in proportion to
how much of the phrase was actually typed (see scoring.combine.blend).
"""

from __future__ import annotations

import warnings

import numpy as np

ENROLL_PHRASE = "the quick brown fox 2026"

# Enrollment: 6 recordings, the first discarded, 5 used.
#
# The first attempt at an unfamiliar phrase is hesitant -- the user is still
# reading it. Discarding it keeps that learning phase out of the template.
# Five usable samples is where the measured error rate stops improving much
# (EER 0.088 at 5 against 0.116 at 3 on simulated typists).
TARGET_SAMPLES = 6
DISCARD_FIRST = 1
MIN_SAMPLES = 5

# An enrollment sample must be essentially the whole phrase.
MIN_ENROLL_COVERAGE = 0.95

MAX_DEVIATION = 10.0     # cap on any single position's contribution
STD_FLOOR_MS = 5.0       # below human timing resolution


def align(events: list[dict], phrase: str = ENROLL_PHRASE):
    """
    Work out which keystroke produced each character of the phrase.

    The event stream is replayed in keydown order with backspace applied,
    so the result reflects the text the user actually ended with. Simply
    dropping backspace events -- the previous behaviour -- kept the deleted
    characters, so one corrected typo shifted every later position and
    compared each key against the wrong part of the template.

    Returns (slots, backspaces, typed) where slots[i] is the event that
    produced phrase[i], or None if that position is wrong or missing.
    Alignment is positional after replay: an uncorrected extra or missing
    character shifts the rest of the phrase and those positions count as
    unmatched.
    """
    evs = sorted(
        (e for e in events if e.get("up") is not None and e.get("down") is not None),
        key=lambda e: e["down"],
    )
    buf: list[dict] = []
    backspaces = 0
    for e in evs:
        k = e.get("key", "")
        if k == "Backspace":
            backspaces += 1
            if buf:
                buf.pop()
        elif len(k) == 1:
            buf.append(e)
        # Shift, CapsLock, arrows and the like carry no position.

    slots: list[dict | None] = [None] * len(phrase)
    for i, ch in enumerate(phrase):
        if i < len(buf) and buf[i]["key"] == ch:
            slots[i] = buf[i]
    return slots, backspaces, len(buf)


def extract(events: list[dict] | None, phrase: str = ENROLL_PHRASE) -> dict:
    """
    Turn raw keydown/keyup events into per-position timings.

    Always returns a result, never None. An empty or wrong attempt has
    coverage near zero and NaN at the positions it did not produce.
    """
    slots, backspaces, typed = align(events or [], phrase)
    n = len(phrase)

    dwell = np.full(n, np.nan)
    flight = np.full(n - 1, np.nan)
    for i, e in enumerate(slots):
        if e is not None:
            dwell[i] = e["up"] - e["down"]
    for i in range(n - 1):
        a, b = slots[i], slots[i + 1]
        if a is not None and b is not None:
            # Negative flight is normal: fast typists overlap keys.
            flight[i] = b["down"] - a["up"]

    matched = [e for e in slots if e is not None]
    coverage = len(matched) / n

    summary = {"matched": len(matched), "typed": typed, "backspaces": backspaces,
               "coverage": round(coverage, 3)}
    if len(matched) >= 2:
        span = (matched[-1]["up"] - matched[0]["down"]) / 1000.0
        dig = np.diff([e["down"] for e in matched])
        summary.update(
            dwell_mean=float(np.nanmean(dwell)),
            typing_speed_cps=float(len(matched) / span) if span > 0 else 0.0,
            rhythm_variance=float(dig.std() / dig.mean()) if dig.mean() > 0 else 0.0,
            error_rate=float(backspaces / max(typed, 1)),
        )

    return {"dwell": dwell, "flight": flight, "coverage": coverage,
            "summary": summary}


def _stats(mat: np.ndarray, floor: float) -> tuple[list, list]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(mat, axis=0)
        std = np.nanstd(mat, axis=0, ddof=1)
    std = np.where(np.isnan(std), floor, np.maximum(std, floor))
    to_list = lambda a: [None if np.isnan(v) else float(v) for v in a]
    return to_list(mean), to_list(std)


def build_template(samples: list[dict], discard_first: int = DISCARD_FIRST) -> dict | None:
    """
    Mean and standard deviation per position, from the enrollment samples.

    Variance is stored, not just the mean: without it a 30 ms deviation
    cannot be told apart between a tired genuine user and an impostor.
    """
    usable = [s for s in samples if s and s["coverage"] >= MIN_ENROLL_COVERAGE]
    if discard_first and len(usable) - discard_first >= MIN_SAMPLES:
        usable = usable[discard_first:]
    if len(usable) < MIN_SAMPLES:
        return None

    dwell_mean, dwell_std = _stats(np.vstack([s["dwell"] for s in usable]), STD_FLOOR_MS)
    flight_mean, flight_std = _stats(np.vstack([s["flight"] for s in usable]), STD_FLOOR_MS)
    speeds = [s["summary"].get("typing_speed_cps", 0.0) for s in usable]

    return {
        "n_samples": len(usable),
        "phrase": ENROLL_PHRASE,
        "n_dwell": len(dwell_mean),
        "n_flight": len(flight_mean),
        "dwell_mean": dwell_mean, "dwell_std": dwell_std,
        "flight_mean": flight_mean, "flight_std": flight_std,
        "speed_mean": float(np.mean(speeds)),
    }


def _arr(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def distance(template: dict | None, sample: dict | None) -> float | None:
    """
    Scaled Manhattan distance over the positions the sample actually got right.

        d = mean( min(|x_i - mean_i| / std_i, 10) )

    Returns None if no position can be compared. How much of the phrase was
    missing is handled separately, through coverage.

    Digraph latency is not compared: it equals dwell + flight, so including
    it double-counted the same evidence (EER 0.102 with it, 0.075 without).
    """
    if template is None or sample is None:
        return None
    parts = []
    for key in ("dwell", "flight"):
        obs = np.asarray(sample[key], dtype=float)
        mean = _arr(template[f"{key}_mean"])
        std = _arr(template[f"{key}_std"])
        n = min(len(obs), len(mean))
        o, m, s = obs[:n], mean[:n], std[:n]
        ok = ~np.isnan(o) & ~np.isnan(m)
        if ok.any():
            parts.append(np.abs(o[ok] - m[ok]) / s[ok])
    if not parts:
        return None
    return float(np.clip(np.concatenate(parts), 0, MAX_DEVIATION).mean())
