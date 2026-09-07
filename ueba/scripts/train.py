"""
Training pipeline: generate -> features -> train -> calibrate -> evaluate.

Run from the repository root:
    python -m scripts.train

Splits by TIME, not randomly. A random split would let the model see a
user's future when scoring their past, and the per-user features are built
from history -- so a random split measures leakage rather than detection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.synth import generate
from eval.metrics import (
    at_threshold,
    per_pattern,
    summary,
    threshold_for_recall,
)
from features.engine import FEATURE_ORDER, compute_batch
from models.base import ModelContext
from models.calibrate import Calibrator, reliability_curve
from models.loader import load_model

ARTIFACTS = Path("artifacts")
DATA_DIR = Path("data")


def time_split(df: pd.DataFrame, frac: float = 0.7):
    """Split at a timestamp so no future information reaches the past."""
    cutoff = df.occurred_at.quantile(frac)
    return df[df.occurred_at <= cutoff], df[df.occurred_at > cutoff]


def run(n_users: int, n_days: int, fraud_rate: float, seed: int) -> dict:
    print("=" * 62)
    print("UEBA training pipeline")
    print("=" * 62)

    # ---- 1. generate ------------------------------------------------
    print("\n[1/6] generating transactions")
    raw = generate(n_users=n_users, n_days=n_days,
                   fraud_rate=fraud_rate, seed=seed)
    print(f"      {len(raw):,} events, {raw.is_fraud.sum()} fraud "
          f"({raw.is_fraud.mean():.3%})")
    for pat, n in raw.fraud_pattern.value_counts().items():
        print(f"        {pat:<24} {n}")

    DATA_DIR.mkdir(exist_ok=True)
    raw.to_csv(DATA_DIR / "events.csv", index=False)

    # ---- 2. features ------------------------------------------------
    print("\n[2/6] computing features")
    feat = compute_batch(raw)
    usable = feat[feat.has_history].copy()
    print(f"      {len(usable):,} rows with sufficient history "
          f"({len(feat) - len(usable):,} cold start)")

    # ---- 3. split ---------------------------------------------------
    train_df, test_df = time_split(usable)
    print(f"\n[3/6] time split: train {len(train_df):,} "
          f"({train_df.is_fraud.sum()} fraud) / "
          f"test {len(test_df):,} ({test_df.is_fraud.sum()} fraud)")

    if test_df.is_fraud.sum() < 5:
        print("      WARNING: too few fraud cases in test for stable metrics")

    results: dict = {}

    # ---- 4. isolation forest (unsupervised, primary) ----------------
    print("\n[4/6] training Isolation Forest (unsupervised)")
    iso = load_model("models/isoforest")
    ctx_train = ModelContext(
        data=train_df, config=iso.manifest,
        artifact_dir=ARTIFACTS, labels=train_df.is_fraud.values,
    )
    iso.validate_features(ctx_train)
    tr = iso.train(ctx_train)
    print(f"      trained on {tr.n_samples:,} non-fraud rows")

    train_scores = iso.infer(ModelContext(data=train_df,
                                          artifact_dir=ARTIFACTS))
    test_scores = iso.infer(ModelContext(data=test_df,
                                         artifact_dir=ARTIFACTS))

    # ---- 5. calibration --------------------------------------------
    print("\n[5/6] calibrating (Platt scaling)")
    cal = Calibrator.fit(train_scores, train_df.is_fraud.values)
    cal.save(ARTIFACTS)
    print(f"      a={cal.a:.4f}  b={cal.b:.4f}")

    test_probs = cal.transform(test_scores)

    centres, observed, counts = reliability_curve(test_probs,
                                                  test_df.is_fraud.values)
    print("      reliability (predicted -> observed):")
    for c, o, n in zip(centres, observed, counts):
        print(f"        {c:.2f} -> {o:.3f}   (n={n})")

    # ---- 6. evaluate ------------------------------------------------
    print("\n[6/6] evaluation")
    iso_metrics = summary(test_probs, test_df.is_fraud.values)
    thr = threshold_for_recall(test_probs, test_df.is_fraud.values, 0.80)
    op = at_threshold(test_probs, test_df.is_fraud.values, thr)

    print(f"\n  Isolation Forest (unsupervised)")
    print(f"    PR-AUC   {iso_metrics['pr_auc']:.4f}   <- primary metric")
    print(f"    ROC-AUC  {iso_metrics['roc_auc']:.4f}")
    print(f"    at threshold {thr:.4f} targeting 80% recall:")
    print(f"      precision {op['precision']:.3f}  recall {op['recall']:.3f}")
    print(f"      tp={op['tp']} fp={op['fp']} fn={op['fn']}")

    pattern_breakdown = per_pattern(
        test_probs, test_df.is_fraud.values,
        test_df.fraud_pattern.values, thr,
    )
    print("\n    per pattern:")
    for pat, s in pattern_breakdown.items():
        print(f"      {pat:<24} {s['detected']}/{s['n']}  "
              f"recall {s['recall']:.2f}")

    results["isoforest"] = {**iso_metrics, "operating_point": op,
                            "per_pattern": pattern_breakdown}

    # ---- supervised baseline ---------------------------------------
    print("\n  Logistic Regression (supervised baseline)")
    lr = load_model("models/logreg")
    ctx_lr = ModelContext(data=train_df, config=lr.manifest,
                          artifact_dir=ARTIFACTS,
                          labels=train_df.is_fraud.values)
    lr.validate_features(ctx_lr)
    lr.train(ctx_lr)
    lr_probs = lr.infer(ModelContext(data=test_df, artifact_dir=ARTIFACTS))
    lr_metrics = summary(lr_probs, test_df.is_fraud.values)
    print(f"    PR-AUC   {lr_metrics['pr_auc']:.4f}")
    print(f"    ROC-AUC  {lr_metrics['roc_auc']:.4f}")
    print("    coefficients:")
    for f, w in lr.coefficients(ARTIFACTS).items():
        print(f"      {f:<22} {w:+.3f}")

    results["logreg"] = lr_metrics

    gap = lr_metrics["pr_auc"] - iso_metrics["pr_auc"]
    print(f"\n  Cost of going unsupervised: {gap:+.4f} PR-AUC")
    print("  (production has no labels at decision time, so the")
    print("   unsupervised model is the deployable one)")

    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\nartifacts written to {ARTIFACTS}/")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--fraud-rate", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    run(a.users, a.days, a.fraud_rate, a.seed)
