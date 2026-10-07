from __future__ import annotations

import numpy as np

from amfa.factors.ueba.features.keystroke import ENROLL_PHRASE


def typing_profile(rng: np.random.Generator) -> dict:
    return {
        "dwell_base": float(rng.uniform(65, 145)),
        "dwell_jitter": float(rng.uniform(0.08, 0.22)),
        "flight_base": float(rng.uniform(-15, 160)),
        "flight_jitter": float(rng.uniform(0.15, 0.40)),
        "position_bias": rng.normal(0, 0.18, size=len(ENROLL_PHRASE)),
        "error_prone": float(rng.uniform(0.0, 0.06)),
    }


def typing_sample(profile: dict, rng: np.random.Generator,
                  phrase: str = ENROLL_PHRASE) -> list[dict]:
    events: list[dict] = []
    t = 1000.0

    tempo = float(rng.normal(1.0, 0.10))

    for i, ch in enumerate(phrase):
        bias = 1.0 + profile["position_bias"][i % len(profile["position_bias"])]

        dwell = profile["dwell_base"] * bias * tempo
        dwell *= rng.normal(1.0, profile["dwell_jitter"])
        dwell = max(dwell, 15.0)

        events.append({"key": ch, "down": round(t, 2),
                       "up": round(t + dwell, 2)})

        flight = profile["flight_base"] * bias * tempo
        flight *= rng.normal(1.0, profile["flight_jitter"])

        if rng.random() < 0.05:
            flight += rng.uniform(120, 400)

        t += dwell + max(flight, -dwell * 0.6)

        if rng.random() < profile["error_prone"]:
            events.append({"key": "Backspace", "down": round(t, 2),
                           "up": round(t + 60, 2)})
            t += 60 + rng.uniform(80, 200)

    return events


def mouse_profile(rng: np.random.Generator) -> dict:
    return {
        "speed": float(rng.uniform(0.6, 2.6)),
        "speed_jitter": float(rng.uniform(0.15, 0.45)),
        "directness": float(rng.uniform(0.55, 0.95)),
        "tremor": float(rng.uniform(2.0, 14.0)),
        "click_dwell": float(rng.uniform(55, 160)),
    }


def mouse_sample(profile: dict, rng: np.random.Generator,
                 n_moves: int = 6, t0: float = 0.0
                 ) -> tuple[list[dict], list[dict]]:
    points: list[dict] = []
    clicks: list[dict] = []

    t = float(t0)
    x, y = float(rng.uniform(100, 900)), float(rng.uniform(100, 600))
    tempo = float(rng.normal(1.0, 0.12))

    for _ in range(n_moves):
        tx, ty = float(rng.uniform(80, 1000)), float(rng.uniform(80, 650))
        steps = int(rng.integers(12, 30))

        wobble = (1.0 - profile["directness"]) * 300
        cx = (x + tx) / 2 + rng.normal(0, wobble)
        cy = (y + ty) / 2 + rng.normal(0, wobble)

        for s in range(steps + 1):
            u = s / steps
            px = (1 - u) ** 2 * x + 2 * (1 - u) * u * cx + u**2 * tx
            py = (1 - u) ** 2 * y + 2 * (1 - u) * u * cy + u**2 * ty

            px += rng.normal(0, profile["tremor"])
            py += rng.normal(0, profile["tremor"])

            speed = profile["speed"] * tempo * rng.normal(1.0, profile["speed_jitter"])
            dt = max(abs(rng.normal(16, 4)) / max(speed, 0.1), 4.0)
            t += dt

            points.append({"x": round(px, 1), "y": round(py, 1),
                           "t": round(t, 2)})

        dwell = profile["click_dwell"] * rng.normal(1.0, 0.18)
        clicks.append({"down": round(t, 2), "up": round(t + dwell, 2)})
        t += dwell + rng.uniform(200, 900)

        x, y = tx, ty

    return points, clicks


def session_profile(rng: np.random.Generator) -> dict:
    return {
        "duration_s": float(rng.uniform(120, 1400)),
        "actions": float(rng.uniform(8, 45)),
        "pace_jitter": float(rng.uniform(0.2, 0.5)),
    }


def session_sample(profile: dict, rng: np.random.Generator,
                   start: float = 0.0) -> dict:
    duration = max(profile["duration_s"] * rng.normal(1.0, 0.25), 30.0)
    n = max(int(profile["actions"] * rng.normal(1.0, 0.30)), 2)

    times = np.sort(rng.uniform(0, duration, size=n))
    return {
        "start": start,
        "end": start + duration,
        "actions": (start + times).tolist(),
    }


def shape_profile(rng: np.random.Generator) -> dict:
    return {
        "precision": float(rng.uniform(0.008, 0.045)),
        "speed": float(rng.uniform(0.22, 0.75)),
        "speed_jitter": float(rng.uniform(0.10, 0.30)),
        "corner_bias": float(rng.uniform(-1.0, 1.0)),
        "corner_slowdown": float(rng.uniform(0.15, 0.70)),
        "side_bias": float(rng.normal(0, 0.012)),
        "consistency": float(rng.uniform(0.10, 0.30)),
    }


def shape_sample(profile: dict, rng: np.random.Generator,
                 t0: float = 0.0) -> list[dict]:
    from amfa.factors.ueba.features.shape import SHAPE

    pts = np.array(SHAPE, dtype=float)
    vecs = pts[1:] - pts[:-1]
    lengths = np.hypot(vecs[:, 0], vecs[:, 1])
    total = lengths.sum()

    tempo = float(rng.normal(1.0, profile["consistency"] * 0.5))
    tempo = max(tempo, 0.4)

    out: list[dict] = []
    t = float(t0)

    for seg in range(len(vecs)):
        v = vecs[seg]
        n_steps = max(int(lengths[seg] / total * 90), 12)
        normal = np.array([-v[1], v[0]]) / max(np.hypot(*v), 1e-9)

        for k in range(n_steps):
            u = k / n_steps
            base = pts[seg] + u * v

            near_corner = min(u, 1 - u) < 0.18
            corner_w = 1.0 if near_corner else 0.0

            dev = (
                profile["side_bias"]
                + rng.normal(0, profile["precision"])
                + corner_w * profile["corner_bias"] * profile["precision"] * 2.0
            )
            p = base + normal * dev

            step_len = lengths[seg] / n_steps
            speed = profile["speed"] * tempo
            speed *= 1.0 - corner_w * profile["corner_slowdown"]
            speed *= rng.normal(1.0, profile["speed_jitter"])
            speed = max(speed, 0.03)

            t += (step_len / total) / speed * 1000.0
            out.append({"x": round(float(p[0]), 4),
                        "y": round(float(p[1]), 4),
                        "t": round(t, 2)})

    out.append({"x": float(pts[-1][0]), "y": float(pts[-1][1]),
                "t": round(t + 20, 2)})
    return out
