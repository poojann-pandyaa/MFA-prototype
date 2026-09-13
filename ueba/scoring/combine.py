"""
Calibration and combination.

Each behavioural dimension produces a distance on its own arbitrary scale.
A keystroke distance of 2.0 and a mouse distance of 2.0 do not mean the
same thing, so they cannot be averaged or weighted directly. That is the
same mistake as the 60/40 rule-versus-ML blend the transactional work
started with.

Instead each dimension is calibrated independently against its own
genuine and impostor distributions, producing a probability. Probabilities
are then combined in log-odds space.

  Platt, J. (1999). "Probabilistic Outputs for Support Vector Machines."
  Niculescu-Mizil, A. & Caruana, R. (2005). "Predicting Good Probabilities
  With Supervised Learning." Proc. 22nd ICML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

EPS = 1e-6


@dataclass
class DimensionCalibrator:
    """
    Maps a distance to P(impostor) via a fitted sigmoid.

        P = 1 / (1 + exp(a * distance + b))
    """

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

        # Equal error rate: the operating point where false accepts equal
        # false rejects. Recorded here so the combination can weight each
        # dimension by how well it actually separates the classes, rather
        # than by a weight someone chose.
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
        """
        How much this dimension's opinion is worth, from its measured EER.

            EER 0.00 (perfect)  -> 1.0
            EER 0.15            -> 0.7
            EER 0.50 (random)   -> 0.0

        Derived from calibration data, not chosen. A dimension that
        separates poorly contributes proportionally less.
        """
        return float(max(0.0, 1.0 - 2.0 * self.eer))

    def probability(self, distance: float) -> float:
        """P(impostor) given this distance."""
        return float(np.clip(1.0 / (1.0 + np.exp(self.a * distance + self.b)),
                             EPS, 1 - EPS))

    def separation(self) -> float:
        """
        How far apart the two classes sit, in units of the genuine mean.
        A sanity check: below about 1.0 the dimension is not discriminating.
        """
        return (self.impostor_mean - self.genuine_mean) / max(self.genuine_mean, EPS)


class CalibrationSet:
    """All dimension calibrators, persisted together."""

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
        """Measured reliability, or neutral weight if uncalibrated."""
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


# Dimensions are not conditionally independent: someone typing hesitantly
# also tends to move the pointer hesitantly, and both correlate with being
# unfamiliar with the interface. Summing log-odds as though they were
# independent compounds agreement too fast -- two moderately elevated
# signals produced 0.9976 in testing, which overstates what the evidence
# supports.
#
# Dividing the summed log-odds by sqrt(n) is a standard correction for
# correlated evidence. It preserves the ordering and the no-weights
# property while damping the compounding. The exponent is a judgement,
# not a measurement: estimating the true correlation needs more enrolled
# users than a prototype cohort provides.
CORRELATION_DAMPING = 0.5

# No single dimension may be certain. A calibrated sigmoid saturates at
# probabilities arbitrarily close to 0 or 1, and logit(1 - 1e-6) is about
# 13.8 -- enough for one dimension to override every other combined.
#
# Testing produced exactly that: keystroke 0.076, session 0.30 and
# transaction 0.79 were overridden by a single mouse reading of 1.0,
# denying a genuine user. Capping each contribution at +/-4 bounds any one
# dimension to roughly p in [0.018, 0.982], so agreement between
# dimensions decides the outcome rather than one saturated signal.
MAX_LOGIT_PER_DIMENSION = 4.0


def combine(probabilities: dict[str, float | None],
            damping: float = CORRELATION_DAMPING,
            reliability: dict[str, float] | None = None,
            ) -> tuple[float, list[str]]:
    """
    Fuse per-dimension impostor probabilities in log-odds space.

        logit(P) = sum(w_d * clip(logit(P_d))) / n**damping

    Naive Bayes combination with three corrections:

      - per-dimension clipping, so no single signal can dominate
      - reliability weighting from each dimension's measured EER, so a
        dimension that separates poorly counts for less
      - correlation damping, since the dimensions are not independent

    None of the weights is chosen by hand; all derive from calibration.

    Dimensions reporting None are skipped, not treated as zero evidence
    in either direction. Returns (combined probability, dimensions used).
    """
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

    # Normalise by weight so a low-reliability dimension does not simply
    # shrink the total toward neutral, then damp for correlation.
    total_logit = total_logit / total_weight * len(used) / len(used) ** damping

    return float(1.0 / (1.0 + np.exp(-total_logit))), used


def confidence(observed: int, full: int) -> float:
    """
    How much to trust a dimension whose baseline is still thin.

    A baseline built from the bare minimum number of observations is
    genuinely weak: statistics from a small sample are noisy, so ordinary
    variation looks anomalous. Treating a 10-event baseline as equal to a
    400-event one produces exactly the false positives it should not.

    Returns 0..1, reaching 1.0 at `full` observations.
    """
    return float(min(max(observed, 0) / max(full, 1), 1.0))


def attenuate(probability: float, confidence_: float) -> float:
    """
    Pull a probability toward 0.5 in proportion to how little is known.

    0.5 is the neutral point -- no evidence in either direction. A
    dimension with a thin baseline nudges the result rather than driving
    it, without being silenced entirely.
    """
    return 0.5 + (probability - 0.5) * confidence_


def band(probability: float) -> str:
    if probability < 0.30:
        return "LOW"
    if probability < 0.70:
        return "MEDIUM"
    return "HIGH"
