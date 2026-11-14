"""Synthetic but human-like phone typing and swiping, for demo customers and for regression."""
from __future__ import annotations

import random


def typing_samples(rng: random.Random, phrase: str, n: int, tempo: float) -> list[list[dict]]:
    """Phone typing that looks like one person's: a steady tempo with natural spread (milliseconds, 3 ms taps)."""
    out = []
    for _ in range(n):
        t, sample = 1000 + rng.random() * 500, []
        for ch in phrase:
            t += max(60, rng.gauss(tempo, tempo * 0.25)) + (120 if ch == " " else 0)
            sample.append({"key": ch, "down": round(t, 2), "up": round(t + 3, 2)})
        out.append(sample)
    return out


def trace(rng: random.Random, shape: list, n: int = 60) -> list[dict]:
    pts, t0 = [], 5000 + rng.random() * 1000
    for i in range(n):
        f = i / (n - 1)
        seg = f * (len(shape) - 1)
        k = min(int(seg), len(shape) - 2)
        u = seg - k
        pts.append({"x": round(shape[k][0] + (shape[k + 1][0] - shape[k][0]) * u + rng.gauss(0, 0.004), 4),
                    "y": round(shape[k][1] + (shape[k + 1][1] - shape[k][1]) * u + rng.gauss(0, 0.004), 4),
                    "t": round(t0 + f * 900 + rng.gauss(0, 3), 2)})
    return pts


def tempo_of(username: str) -> float:
    """A person's typing pace, fixed by who they are, so a later sample can be 'the same person' or 'someone else'."""
    return 140 + random.Random(username).random() * 90
