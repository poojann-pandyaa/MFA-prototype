"""
Score calibration.

Raw anomaly scores are ordered but are not probabilities. A score of 0.7
does not mean a 70% chance of fraud. Calibration fits a mapping from score
to probability using labelled examples.

Platt scaling is the default. Niculescu-Mizil & Caruana (ICML 2005) found
sigmoid scaling performs better than isotonic regression when calibration
data is scarce, with isotonic overtaking it above roughly a thousand
labelled points. This project's labelled set is small, so Platt is safer.

  Platt, J. (1999). "Probabilistic Outputs for Support Vector Machines and
    Comparisons to Regularized Likelihood Methods." In Advances in Large
    Margin Classifiers, MIT Press, pp. 61-74.
  Niculescu-Mizil, A. & Caruana, R. (2005). "Predicting Good Probabilities
    With Supervised Learning." Proc. 22nd ICML, pp. 625-632.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from joblib import dump, load
from sklearn.linear_model import LogisticRegression

ARTIFACT = "calibrator.joblib"


@dataclass
class Calibrator:
    """P(fraud | score) = 1 / (1 + exp(a * score + b))"""

    a: float
    b: float

    @classmethod
    def fit(cls, scores: np.ndarray, labels: np.ndarray) -> "Calibrator":
        if len(np.unique(labels)) < 2:
            raise ValueError("calibration requires both classes present")
        lr = LogisticRegression(class_weight="balanced")
        lr.fit(np.asarray(scores).reshape(-1, 1), labels)
        # sklearn fits 1/(1+exp(-(w*x+c))); Platt form 1/(1+exp(a*x+b))
        # therefore a = -w, b = -c.
        return cls(a=float(-lr.coef_[0][0]), b=float(-lr.intercept_[0]))

    def transform(self, scores: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(self.a * np.asarray(scores) + self.b))

    def save(self, artifact_dir: Path) -> None:
        Path(artifact_dir).mkdir(parents=True, exist_ok=True)
        dump({"a": self.a, "b": self.b}, Path(artifact_dir) / ARTIFACT)

    @classmethod
    def load(cls, artifact_dir: Path) -> "Calibrator":
        d = load(Path(artifact_dir) / ARTIFACT)
        return cls(a=d["a"], b=d["b"])


def reliability_curve(probs, labels, n_bins: int = 10):
    """
    Data for a reliability diagram: predicted probability against observed
    fraud rate. A well-calibrated model sits near the diagonal.

    This plot is the evidence that the risk score means what it claims.
    Returns (bin_centre, observed_rate, count).
    """
    probs, labels = np.asarray(probs), np.asarray(labels)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    centres, observed, counts = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (probs >= lo) & (probs < hi)
        if mask.sum() == 0:
            continue
        centres.append((lo + hi) / 2)
        observed.append(float(labels[mask].mean()))
        counts.append(int(mask.sum()))
    return np.array(centres), np.array(observed), np.array(counts)
