from __future__ import annotations

import numpy as np

ENROLL_PHRASE = "the quick brown fox 2026"

MIN_SAMPLES = 5
MAX_DEVIATION = 10.0
TARGET_SAMPLES = 10

DISCARD_FIRST = 2


def _clean(events: list[dict]) -> list[dict]:
    return [
        e
        for e in events
        if len(e.get("key", "")) == 1 and e.get("up") is not None
    ]


def extract(events: list[dict]) -> dict | None:
    keys = _clean(events)
    if len(keys) < 6:
        return None

    dwell = np.array([e["up"] - e["down"] for e in keys], dtype=float)

    flight = np.array(
        [keys[i + 1]["down"] - keys[i]["up"] for i in range(len(keys) - 1)],
        dtype=float,
    )

    digraph = np.array(
        [keys[i + 1]["down"] - keys[i]["down"] for i in range(len(keys) - 1)],
        dtype=float,
    )

    total_ms = keys[-1]["up"] - keys[0]["down"]
    n_backspace = sum(1 for e in events if e.get("key") == "Backspace")

    return {
        "dwell": dwell,
        "flight": flight,
        "digraph": digraph,
        "summary": {
            "dwell_mean": float(dwell.mean()),
            "dwell_std": float(dwell.std()),
            "flight_mean": float(flight.mean()),
            "flight_std": float(flight.std()),
            "digraph_mean": float(digraph.mean()),
            "typing_speed_cps": float(len(keys) / (total_ms / 1000.0))
            if total_ms > 0
            else 0.0,
            "rhythm_variance": float(digraph.std() / digraph.mean())
            if digraph.mean() > 0
            else 0.0,
            "error_rate": float(n_backspace / max(len(keys), 1)),
            "total_ms": float(total_ms),
        },
    }


def build_template(samples: list[dict],
                   discard_first: int = DISCARD_FIRST) -> dict | None:
    usable = [s for s in samples if s is not None]

    if discard_first and len(usable) - discard_first >= MIN_SAMPLES:
        usable = usable[discard_first:]

    if len(usable) < MIN_SAMPLES:
        return None

    n_dwell = min(len(s["dwell"]) for s in usable)
    n_flight = min(len(s["flight"]) for s in usable)

    dwell = np.vstack([s["dwell"][:n_dwell] for s in usable])
    flight = np.vstack([s["flight"][:n_flight] for s in usable])
    digraph = np.vstack([s["digraph"][:n_flight] for s in usable])

    def stats(mat: np.ndarray) -> tuple[list, list]:
        mean = mat.mean(axis=0)
        std = mat.std(axis=0, ddof=1)
        std = np.maximum(std, 5.0)
        return mean.tolist(), std.tolist()

    dwell_mean, dwell_std = stats(dwell)
    flight_mean, flight_std = stats(flight)
    digraph_mean, digraph_std = stats(digraph)

    summaries = np.array(
        [
            [
                s["summary"]["typing_speed_cps"],
                s["summary"]["rhythm_variance"],
                s["summary"]["error_rate"],
            ]
            for s in usable
        ]
    )

    return {
        "n_samples": len(usable),
        "n_dwell": n_dwell,
        "n_flight": n_flight,
        "dwell_mean": dwell_mean,
        "dwell_std": dwell_std,
        "flight_mean": flight_mean,
        "flight_std": flight_std,
        "digraph_mean": digraph_mean,
        "digraph_std": digraph_std,
        "speed_mean": float(summaries[:, 0].mean()),
        "speed_std": float(max(summaries[:, 0].std(ddof=1), 0.1)),
        "rhythm_mean": float(summaries[:, 1].mean()),
        "error_mean": float(summaries[:, 2].mean()),
    }


def distance(template: dict, sample: dict) -> float | None:
    if template is None or sample is None:
        return None

    parts: list[float] = []

    for key, n_key in (("dwell", "n_dwell"), ("flight", "n_flight")):
        n = template[n_key]
        obs = np.asarray(sample[key][:n], dtype=float)
        if len(obs) < n:
            n = len(obs)
            if n == 0:
                continue
        mean = np.asarray(template[f"{key}_mean"][:n])
        std = np.asarray(template[f"{key}_std"][:n])
        parts.append(np.abs(obs - mean) / std)

    if not parts:
        return None

    scaled = np.clip(np.concatenate(parts), 0, MAX_DEVIATION)
    return float(scaled.mean())
