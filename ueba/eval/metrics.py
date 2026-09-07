"""
Evaluation.

PR-AUC is the primary metric. With fraud near 1%, accuracy is
uninformative -- a model predicting "never fraud" scores over 99%. ROC-AUC
can look inflated under imbalance because the large negative count makes
the false positive rate move slowly.

  Davis, J. & Goadrich, M. (2006). "The Relationship Between
    Precision-Recall and ROC Curves." Proc. 23rd ICML, pp. 233-240.
  Saito, T. & Rehmsmeier, M. (2015). "The Precision-Recall Plot Is More
    Informative than the ROC Plot When Evaluating Binary Classifiers on
    Imbalanced Datasets." PLOS ONE 10(3): e0118432.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)


def summary(probs, labels) -> dict:
    probs, labels = np.asarray(probs), np.asarray(labels)
    return {
        "pr_auc": float(average_precision_score(labels, probs)),
        "roc_auc": float(roc_auc_score(labels, probs)),
        "positive_rate": float(labels.mean()),
        "n": int(len(labels)),
    }


def at_threshold(probs, labels, threshold: float) -> dict:
    probs, labels = np.asarray(probs), np.asarray(labels)
    pred = (probs >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(labels, pred, labels=[0, 1]).ravel()
    return {
        "threshold": threshold,
        "precision": float(tp / (tp + fp)) if (tp + fp) else 0.0,
        "recall": float(tp / (tp + fn)) if (tp + fn) else 0.0,
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
    }


def threshold_for_recall(probs, labels, target_recall: float = 0.80) -> float:
    """
    Pick an operating point by the recall you need, rather than defaulting
    to 0.5. In fraud detection the cost of a miss and the cost of a false
    alarm are not symmetric, so the threshold is a policy choice.
    """
    precision, recall, thresholds = precision_recall_curve(labels, probs)
    ok = np.where(recall[:-1] >= target_recall)[0]
    if len(ok) == 0:
        return 0.5
    return float(thresholds[ok[-1]])


def per_pattern(probs, labels, patterns, threshold: float) -> dict:
    """
    Detection rate per injected fraud pattern.

    More informative than a single aggregate. "We catch account takeover
    reliably and miss gradual amount drift" is a finding; one number is not.
    """
    probs = np.asarray(probs)
    patterns = np.asarray(patterns, dtype=object)
    caught = probs >= threshold

    out = {}
    for p in sorted({x for x in patterns if x is not None and x == x}):
        mask = patterns == p
        out[p] = {
            "n": int(mask.sum()),
            "detected": int(caught[mask].sum()),
            "recall": float(caught[mask].mean()) if mask.sum() else 0.0,
        }
    return out


def ablation(train_fn, features: list[str]) -> dict:
    """
    Drop each feature, retrain, report the PR-AUC change. Shows which
    features actually contribute.
    """
    baseline = train_fn(features)["pr_auc"]
    out = {"__baseline__": round(baseline, 4)}
    for f in features:
        reduced = [x for x in features if x != f]
        out[f] = round(train_fn(reduced)["pr_auc"] - baseline, 4)
    return out
