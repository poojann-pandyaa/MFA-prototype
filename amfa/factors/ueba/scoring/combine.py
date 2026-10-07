from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

EPS = 1e-6


@dataclass
class DimensionCalibrator:

    a: float
    b: float
    genuine_mean: float
    impostor_mean: float
    n_genuine: int
    n_impostor: int
    eer: float = 0.5

    @classmethod
    def fit(cls, genuine: list[float], impostor: list[float]) -> "DimensionCalibrator":
        g = np.asarray([d for d in genuine if d is not None], dtype=float)
        i = np.asarray([d for d in impostor if d is not None], dtype=float)

        if len(g) < 5 or len(i) < 5:
            raise ValueError(
                f"need at least 5 samples per class, got {len(g)} genuine "
                f"and {len(i)} impostor"
            )

        x = np.concatenate([g, i]).reshape(-1, 1)
        y = np.concatenate([np.zeros(len(g)), np.ones(len(i))])

        lr = LogisticRegression(class_weight="balanced")
        lr.fit(x, y)

        thresholds = np.linspace(min(g.min(), i.min()), max(g.max(), i.max()), 400)
        far = np.array([(i <= t).mean() for t in thresholds])
        frr = np.array([(g > t).mean() for t in thresholds])
        k = int(np.argmin(np.abs(far - frr)))
        eer = float((far[k] + frr[k]) / 2)

        return cls(
            a=float(-lr.coef_[0][0]),
            b=float(-lr.intercept_[0]),
            genuine_mean=float(g.mean()),
            impostor_mean=float(i.mean()),
            n_genuine=len(g),
            n_impostor=len(i),
            eer=eer,
        )

    def reliability(self) -> float:
        return float(max(0.0, 1.0 - 2.0 * self.eer))

    def probability(self, distance: float) -> float:
        return float(np.clip(1.0 / (1.0 + np.exp(self.a * distance + self.b)),
                             EPS, 1 - EPS))

    def separation(self) -> float:
        return (self.impostor_mean - self.genuine_mean) / max(self.genuine_mean, EPS)


class CalibrationSet:

    FILENAME = "calibration.json"

    def __init__(self, calibrators: dict[str, DimensionCalibrator] | None = None):
        self.calibrators = calibrators or {}

    def add(self, dimension: str, cal: DimensionCalibrator) -> None:
        self.calibrators[dimension] = cal

    def probability(self, dimension: str, distance: float | None) -> float | None:
        if distance is None or dimension not in self.calibrators:
            return None
        return self.calibrators[dimension].probability(distance)

    def reliability(self, dimension: str) -> float:
        if dimension not in self.calibrators:
            return 1.0
        return self.calibrators[dimension].reliability()

    def save(self, directory: Path) -> None:
        Path(directory).mkdir(parents=True, exist_ok=True)
        payload = {k: asdict(v) for k, v in self.calibrators.items()}
        (Path(directory) / self.FILENAME).write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls, directory: Path) -> "CalibrationSet":
        path = Path(directory) / cls.FILENAME
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls({k: DimensionCalibrator(**v) for k, v in raw.items()})


CORRELATION_DAMPING = 0.5

MAX_LOGIT_PER_DIMENSION = 4.0


def combine(probabilities: dict[str, float | None],
            damping: float = CORRELATION_DAMPING,
            reliability: dict[str, float] | None = None,
            ) -> tuple[float, list[str]]:
    used = [d for d, p in probabilities.items() if p is not None]
    if not used:
        return 0.5, []

    reliability = reliability or {}
    total_logit = 0.0
    total_weight = 0.0

    for d in used:
        p = float(np.clip(probabilities[d], EPS, 1 - EPS))
        logit = float(np.clip(np.log(p / (1 - p)),
                              -MAX_LOGIT_PER_DIMENSION,
                              MAX_LOGIT_PER_DIMENSION))
        w = reliability.get(d, 1.0)
        total_logit += w * logit
        total_weight += w

    if total_weight == 0:
        return 0.5, used

    total_logit = total_logit / total_weight * len(used) / len(used) ** damping

    return float(1.0 / (1.0 + np.exp(-total_logit))), used


def confidence(observed: int, full: int) -> float:
    return float(min(max(observed, 0) / max(full, 1), 1.0))


def attenuate(probability: float, confidence_: float) -> float:
    return 0.5 + (probability - 0.5) * confidence_


def band(probability: float) -> str:
    if probability < 0.30:
        return "LOW"
    if probability < 0.70:
        return "MEDIUM"
    return "HIGH"
