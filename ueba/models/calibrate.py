"""
Platt scaling for the transactional anomaly score.

Raw Isolation Forest scores are ordered but are not probabilities.
Sigmoid scaling was chosen over isotonic regression because it performs
better when calibration data is scarce (Niculescu-Mizil & Caruana, 2005).
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
    a: float
    b: float

    @classmethod
    def fit(cls, scores, labels):
        if len(np.unique(labels)) < 2:
            raise ValueError("calibration requires both classes present")
        lr = LogisticRegression(class_weight="balanced")
        lr.fit(np.asarray(scores).reshape(-1, 1), labels)
        return cls(a=float(-lr.coef_[0][0]), b=float(-lr.intercept_[0]))

    def transform(self, scores):
        return 1.0 / (1.0 + np.exp(self.a * np.asarray(scores) + self.b))

    def save(self, d):
        Path(d).mkdir(parents=True, exist_ok=True)
        dump({"a": self.a, "b": self.b}, Path(d) / ARTIFACT)

    @classmethod
    def load(cls, d):
        x = load(Path(d) / ARTIFACT)
        return cls(a=x["a"], b=x["b"])


def reliability_curve(probs, labels, n_bins=10):
    probs, labels = np.asarray(probs), np.asarray(labels)
    edges = np.linspace(0, 1, n_bins + 1)
    c, o, n = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (probs >= lo) & (probs < hi)
        if m.sum() == 0:
            continue
        c.append((lo + hi) / 2); o.append(float(labels[m].mean())); n.append(int(m.sum()))
    return np.array(c), np.array(o), np.array(n)
