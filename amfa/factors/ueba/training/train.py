from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from amfa.factors.ueba.data.behaviour import (
    mouse_profile, mouse_sample, session_profile, session_sample,
    shape_profile, shape_sample, typing_profile, typing_sample,
)
from amfa.factors.ueba.data.synth import generate
from amfa.factors.ueba.features import keystroke as ks
from amfa.factors.ueba.features import mouse as ms
from amfa.factors.ueba.features import session as se
from amfa.factors.ueba.features import shape as sh
from amfa.factors.ueba.features.transactional import FEATURE_ORDER, compute_batch
from amfa.factors.ueba.models.base import ModelContext
from amfa.factors.ueba.models.calibrate import Calibrator
from amfa.factors.ueba.models.loader import load_model
from amfa.factors.ueba.scoring.combine import CalibrationSet, DimensionCalibrator

from amfa import config as _config
from amfa.common.artifacts import sha256_file

ARTIFACTS = _config.get().models_dir / "ueba"
PACKAGE = Path(__file__).resolve().parent.parent / "models" / "isoforest"
PIN_FILE = Path(__file__).resolve().parent.parent / "ARTIFACTS.sha256"


def calibrate_keystroke(n_users: int, seed: int) -> tuple[DimensionCalibrator, dict]:
    rng = np.random.default_rng(seed)
    profiles = [typing_profile(rng) for _ in range(n_users)]

    genuine, impostor = [], []
    for i, p in enumerate(profiles):
        tpl = ks.build_template(
            [ks.extract(typing_sample(p, rng)) for _ in range(8)]
        )
        if tpl is None:
            continue
        genuine += [ks.distance(tpl, ks.extract(typing_sample(p, rng)))
                    for _ in range(6)]
        others = [q for j, q in enumerate(profiles) if j != i]
        for q in rng.choice(len(others), size=min(6, len(others)), replace=False):
            impostor.append(ks.distance(tpl, ks.extract(typing_sample(others[q], rng))))

    cal = DimensionCalibrator.fit(genuine, impostor)
    return cal, {"genuine": genuine, "impostor": impostor}


def calibrate_mouse(n_users: int, seed: int) -> tuple[DimensionCalibrator, dict]:
    rng = np.random.default_rng(seed + 1)
    profiles = [mouse_profile(rng) for _ in range(n_users)]

    clock = {"t": 0.0}

    def sample(p):
        pts, clicks = mouse_sample(p, rng, t0=clock["t"])
        clock["t"] = pts[-1]["t"] + 5000.0
        return ms.extract(pts, clicks)

    genuine, impostor = [], []
    for i, p in enumerate(profiles):
        tpl = ms.build_template([sample(p) for _ in range(6)])
        if tpl is None:
            continue
        genuine += [ms.distance(tpl, sample(p)) for _ in range(6)]
        others = [q for j, q in enumerate(profiles) if j != i]
        for q in rng.choice(len(others), size=min(6, len(others)), replace=False):
            impostor.append(ms.distance(tpl, sample(others[q])))

    cal = DimensionCalibrator.fit(genuine, impostor)
    return cal, {"genuine": genuine, "impostor": impostor}


def calibrate_shape(n_users: int, seed: int) -> tuple[DimensionCalibrator, dict]:
    rng = np.random.default_rng(seed + 3)
    profiles = [shape_profile(rng) for _ in range(n_users)]

    genuine, impostor = [], []
    for i, p in enumerate(profiles):
        tpl = sh.build_template(
            [sh.extract(shape_sample(p, rng)) for _ in range(8)]
        )
        if tpl is None:
            continue
        genuine += [sh.distance(tpl, sh.extract(shape_sample(p, rng)))
                    for _ in range(6)]
        others = [q for j, q in enumerate(profiles) if j != i]
        for q in rng.choice(len(others), size=min(6, len(others)), replace=False):
            impostor.append(sh.distance(tpl, sh.extract(shape_sample(others[q], rng))))

    genuine = [d for d in genuine if d is not None]
    impostor = [d for d in impostor if d is not None]
    cal = DimensionCalibrator.fit(genuine, impostor)
    return cal, {"genuine": genuine, "impostor": impostor}


def calibrate_session(n_users: int, seed: int) -> tuple[DimensionCalibrator, dict]:
    rng = np.random.default_rng(seed + 2)
    profiles = [session_profile(rng) for _ in range(n_users)]

    genuine, impostor = [], []
    for i, p in enumerate(profiles):
        prof = se.build_profile(
            [se.extract(session_sample(p, rng)) for _ in range(10)]
        )
        if prof is None:
            continue
        genuine += [se.distance(prof, se.extract(session_sample(p, rng)))
                    for _ in range(6)]
        others = [q for j, q in enumerate(profiles) if j != i]
        for q in rng.choice(len(others), size=min(6, len(others)), replace=False):
            impostor.append(se.distance(prof, se.extract(session_sample(others[q], rng))))

    cal = DimensionCalibrator.fit(genuine, impostor)
    return cal, {"genuine": genuine, "impostor": impostor}


def train_transactional(n_users: int, n_days: int, seed: int) -> dict:
    raw = generate(n_users=n_users, n_days=n_days, seed=seed)
    feat = compute_batch(raw)
    usable = feat[feat.has_history].copy()

    cutoff = usable.occurred_at.quantile(0.7)
    train = usable[usable.occurred_at <= cutoff]
    test = usable[usable.occurred_at > cutoff]

    model = load_model(PACKAGE)
    ctx = ModelContext(data=train, config=model.manifest,
                       artifact_dir=ARTIFACTS, labels=train.is_fraud.values)
    model.validate_features(ctx)
    model.train(ctx)

    train_scores = model.infer(ModelContext(data=train, artifact_dir=ARTIFACTS))
    test_scores = model.infer(ModelContext(data=test, artifact_dir=ARTIFACTS))

    cal = Calibrator.fit(train_scores, train.is_fraud.values)
    cal.save(ARTIFACTS)
    probs = cal.transform(test_scores)

    return {
        "n_events": len(raw),
        "fraud_rate": float(raw.is_fraud.mean()),
        "pr_auc": float(average_precision_score(test.is_fraud.values, probs)),
        "roc_auc": float(roc_auc_score(test.is_fraud.values, probs)),
    }


def run(n_users: int, n_days: int, n_behaviour_users: int, seed: int) -> dict:
    print("=" * 64)
    print("UEBA training pipeline -- four behavioural dimensions")
    print("=" * 64)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    calset = CalibrationSet()
    results: dict = {}

    for name, fn in (("keystroke", calibrate_keystroke),
                     ("shape", calibrate_shape),
                     ("mouse", calibrate_mouse),
                     ("session", calibrate_session)):
        print(f"\n[{name}] calibrating against {n_behaviour_users} simulated users")
        cal, dists = fn(n_behaviour_users, seed)
        calset.add(name, cal)

        g = np.array(dists["genuine"]); i = np.array(dists["impostor"])
        labels = np.concatenate([np.zeros(len(g)), np.ones(len(i))])
        scores = np.concatenate([g, i])
        auc = roc_auc_score(labels, scores)

        thresholds = np.linspace(scores.min(), scores.max(), 400)
        far = [(i <= t).mean() for t in thresholds]
        frr = [(g > t).mean() for t in thresholds]
        eer_idx = int(np.argmin(np.abs(np.array(far) - np.array(frr))))
        eer = (far[eer_idx] + frr[eer_idx]) / 2

        print(f"    genuine  {g.mean():.2f} +/- {g.std():.2f}  (n={len(g)})")
        print(f"    impostor {i.mean():.2f} +/- {i.std():.2f}  (n={len(i)})")
        print(f"    AUC {auc:.4f}   EER {eer:.3f}   "
              f"separation {cal.separation():.2f}x")

        results[name] = {"auc": float(auc), "eer": float(eer),
                         "genuine_mean": float(g.mean()),
                         "impostor_mean": float(i.mean()),
                         "n_genuine": len(g), "n_impostor": len(i)}

    print(f"\n[transaction] training Isolation Forest on {n_users} users")
    tx = train_transactional(n_users, n_days, seed)
    print(f"    {tx['n_events']:,} events, {tx['fraud_rate']:.2%} fraud")
    print(f"    PR-AUC {tx['pr_auc']:.4f}   ROC-AUC {tx['roc_auc']:.4f}")
    results["transaction"] = tx

    calset.save(ARTIFACTS)
    (ARTIFACTS / "results.json").write_text(json.dumps(results, indent=2))

    pins = {f.name: sha256_file(f) for f in sorted(ARTIFACTS.iterdir())
            if f.suffix in (".joblib", ".json") and f.name != "results.json"}
    PIN_FILE.write_text("".join(f"{v}  {k}\n" for k, v in pins.items()))
    print(f"\nartifacts written to {ARTIFACTS}/ and pinned in {PIN_FILE.name}")
    print("\nNote: behavioural results use simulated motor profiles. Real")
    print("collected samples will differ; these establish the pipeline")
    print("works, not the accuracy it would achieve in deployment.")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--behaviour-users", type=int, default=40)
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    run(a.users, a.days, a.behaviour_users, a.seed)
