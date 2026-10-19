"""Train, evaluate and (if it passes the release gate) activate a risk model.

    python -m amfa.engine.risk.train                 # 20k simulated customers
    python -m amfa.engine.risk.train --customers 2000 --no-activate

Data is split by time (train on the past, test on the future). Writes models/risk/<version>/ with the
boosters, isotonic calibration, a model card (data, params, metrics, gate) and a checksum manifest."""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from .features import CATEGORICAL, FEATURES, HISTORY_DAYS, MONOTONE, vector
from .scenarios import ATTACKS, FEEDS, START, amount_band, dataset, AMOUNT_BANDS
from .scorer import ACTIVE, MANIFEST, RiskModel, root, sha256
from .threat import GENUINE_SHARE_AT_OR_ABOVE, LEVELS, level, small_payment_cap

DAY = 86400.0
TRAIN_END, VALID_END = 105, 128   # days after START; the rest of the simulation is the test set
SEARCH = [dict(num_leaves=nl, min_child_samples=mcs, lambda_l2=l2)
          for nl in (15, 31, 63) for mcs in (20, 60) for l2 in (0.0, 5.0)]
ENSEMBLE_SEEDS = 5
FAIRNESS_MAX_RATIO = 1.25
FAIRNESS_MIN_ROWS = 300


def _base_params(seed: int) -> dict:
    return dict(objective="binary", learning_rate=0.05, feature_fraction=0.8, bagging_fraction=0.8,
                bagging_freq=1, metric="average_precision", verbose=-1, seed=seed,
                monotone_constraints=[MONOTONE.get(f, 0) for f in FEATURES],
                monotone_constraints_method="advanced")


def _fit(lgb, params, tr, va):
    return lgb.train(params, tr, num_boost_round=3000, valid_sets=[va],
                     callbacks=[lgb.early_stopping(100, verbose=False)])


def _baseline_ap(Xtr, ytr, wtr, Xte, yte, wte) -> float:
    """Logistic regression on median-imputed, standardised features: the bar the model must clear."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    fill = lambda X: np.where(np.isnan(X), med, X)
    a, b = fill(Xtr), fill(Xte)
    mu, sd = a.mean(0), a.std(0) + 1e-9
    lr = LogisticRegression(max_iter=3000).fit((a - mu) / sd, ytr, sample_weight=wtr)
    return float(average_precision_score(yte, lr.predict_proba((b - mu) / sd)[:, 1], sample_weight=wte))


def wquantile(x: np.ndarray, w: np.ndarray, q: float) -> float:
    o = np.argsort(x)
    cum = np.cumsum(w[o]) / w.sum()
    return float(x[o][min(int(np.searchsorted(cum, q)), len(x) - 1)])


LOW_LINE_RUPEES = 10_000   # the bank's default LOW amount band: below this a payment is "small"


def evaluate(raw, prob, y, w, scenario, segment, feed, rank, groups) -> dict:
    """rank: threat level (0-4) after the small-payment cap; groups["rank_uncapped"]: the model's own level."""
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    gen, fraud = y == 0, y == 1
    raw_rank = groups["rank_uncapped"]

    def share(m, k, r=None):  # weighted share of the rows in m at threat level k or above
        r = rank if r is None else r
        return round(float(np.average(r[m] >= k, weights=w[m])), 4) if m.any() else None

    def rupees(m, k):  # share of the fraud money in m that is at level k or above
        a = w[m] * groups["amount"][m]
        return round(float((a * (rank[m] >= k)).sum() / a.sum()), 4) if m.any() and a.sum() else None

    out = {
        "pr_auc": round(float(average_precision_score(y, raw, sample_weight=w)), 4),
        "roc_auc": round(float(roc_auc_score(y, raw, sample_weight=w)), 4),
        "brier": round(float(brier_score_loss(y, prob, sample_weight=w)), 5),
        "fraud_rows": int(fraud.sum()), "genuine_rows": int(gen.sum()),
        "genuine_at_or_above": {l: share(gen, i) for i, l in enumerate(LEVELS) if i},
        "fraud_caught_at_or_above": {l: share(fraud, i) for i, l in enumerate(LEVELS) if i},
        "by_scenario": {sc: {"rows": int((scenario == sc).sum()), "caught_T2+": share(scenario == sc, 2),
                             "caught_T3+": share(scenario == sc, 3)}
                        for sc in ATTACKS if (scenario == sc).any()},
        # how often genuine customers in each situation are stepped up: the false-alarm view
        "by_situation": {t: {"rows": int(m.sum()), "stepped_up_T2+": share(m, 2), "T3+": share(m, 3),
                             "manual_review_T4": share(m, 4)}
                         for t in sorted(set(scenario[gen])) for m in [gen & (scenario == t)]},
        "by_feed": {f: {"fraud_caught_T3+": share(fraud & (feed == f), 3),
                        "genuine_stepped_up_T2+": share(gen & (feed == f), 2)} for f in FEEDS},
        "by_segment": {},
        # who is stepped up and who is caught, by customer age and by size of payment
        "by_age": {}, "by_amount_range": {},
        "uncapped": {"genuine_at_or_above": {l: share(gen, i, raw_rank) for i, l in enumerate(LEVELS) if i},
                     "fraud_caught_at_or_above": {l: share(fraud, i, raw_rank) for i, l in enumerate(LEVELS) if i},
                     "by_age_genuine_stepped_up_T2+": {g: share(gen & (groups["age"] == g), 2, raw_rank)
                                                       for g in sorted(set(groups["age"]))}},
        "fraud_rupees_caught_T3+": rupees(fraud, 3),
        "by_archetype": {a: {"genuine_rows": int((gen & (groups["archetype"] == a)).sum()),
                             "genuine_stepped_up_T2+": share(gen & (groups["archetype"] == a), 2)}
                         for a in sorted(set(groups["archetype"]))},
    }
    overall = share(gen, 2)
    for g in sorted(set(groups["age"])):
        m = gen & (groups["age"] == g)
        out["by_age"][g] = {"genuine_rows": int(m.sum()), "genuine_stepped_up_T2+": share(m, 2),
                            "ratio_to_average": round(share(m, 2) / overall, 2) if overall and m.any() else None,
                            "fraud_rows": int((fraud & (groups["age"] == g)).sum()),
                            "fraud_caught_T3+": share(fraud & (groups["age"] == g), 3),
                            "fraud_rupees_caught_T3+": rupees(fraud & (groups["age"] == g), 3)}
    for _, _, b in AMOUNT_BANDS:
        mg, mf = gen & (groups["band"] == b), fraud & (groups["band"] == b)
        out["by_amount_range"][b] = {"genuine_rows": int(mg.sum()), "genuine_stepped_up_T2+": share(mg, 2),
                                     "fraud_rows": int(mf.sum()), "fraud_caught_T3+": share(mf, 3)}
    for seg in CATEGORICAL["segment"]:
        m = gen & (segment == seg)
        if m.sum():
            rate = share(m, 2)
            out["by_segment"][seg] = {"genuine_rows": int(m.sum()), "genuine_stepped_up_T2+": rate,
                                      "ratio_to_average": round(rate / overall, 2) if overall else None}
    return out


def run(customers: int = 20000, days: int = 150, seed: int = 7, activate: bool = True,
        force: bool = False, out_root: Path | None = None, search: list[dict] | None = None,
        seeds: int = ENSEMBLE_SEEDS) -> Path:
    import lightgbm as lgb
    from sklearn.isotonic import IsotonicRegression

    t0 = time.time()
    rows = dataset(n_customers=customers, days=days, seed=seed)
    X = np.array([vector(r.features) for r in rows])
    y = np.array([r.label for r in rows])
    w = np.array([r.weight for r in rows])
    feed = np.array([r.feed for r in rows])
    groups = {"amount": np.array([r.amount for r in rows]), "age": np.array([r.age_group for r in rows]),
              "archetype": np.array([r.archetype for r in rows]),
              "band": np.array([amount_band(r.amount) if r.amount > 0 else "none" for r in rows])}
    day = (np.array([r.at for r in rows]) - START) / DAY
    scenario = np.array([r.scenario for r in rows])
    segment = np.array([r.segment for r in rows])
    tr, va, te = day < TRAIN_END, (day >= TRAIN_END) & (day < VALID_END), day >= VALID_END
    print(f"{len(rows)} rows ({int(y.sum())} fraud) built in {time.time() - t0:.0f}s; "
          f"train/valid/test = {tr.sum()}/{va.sum()}/{te.sum()}")

    cat = [FEATURES.index(c) for c in CATEGORICAL]
    dtr = lgb.Dataset(X[tr], y[tr], weight=w[tr], feature_name=FEATURES, categorical_feature=cat,
                      free_raw_data=False)
    dva = lgb.Dataset(X[va], y[va], weight=w[va], reference=dtr, free_raw_data=False)

    best = None
    for grid in (search or SEARCH):
        b = _fit(lgb, _base_params(seed) | grid, dtr, dva)
        ap = b.best_score["valid_0"]["average_precision"]
        print(f"  {grid} -> valid PR-AUC {ap:.4f} @ {b.best_iteration} trees")
        if best is None or ap > best[0]:
            best = (ap, grid)
    params = best[1]
    boosters = [_fit(lgb, _base_params(seed + k) | params, dtr, dva) for k in range(seeds)]

    def raw_of(m):
        return np.mean([b.predict(X[m], num_iteration=b.best_iteration) for b in boosters], axis=0)

    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw_of(va), y[va], sample_weight=w[va])
    gv = y[va] == 0
    cutoffs = [wquantile(raw_of(va)[gv], w[va][gv], 1 - GENUINE_SHARE_AT_OR_ABOVE[l]) for l in LEVELS[1:]]

    raw_te = raw_of(te)
    model_rank = np.array([LEVELS.index(level(r, cutoffs)) for r in raw_te])
    # the system's real behaviour: a small, usual payment without a hard warning sign is held at T1
    rank = np.array([LEVELS.index(small_payment_cap(LEVELS[m], r.features)[0]) if r.exposure < LOW_LINE_RUPEES else m
                     for m, r in zip(model_rank, (rows[i] for i in np.flatnonzero(te)))])
    metrics = evaluate(raw_te, iso.predict(raw_te), y[te], w[te], scenario[te], segment[te], feed[te], rank,
                       {k: v[te] for k, v in groups.items()} | {"rank_uncapped": model_rank})
    metrics["baseline_logistic_pr_auc"] = round(_baseline_ap(X[tr], y[tr], w[tr], X[te], y[te], w[te]), 4)
    unfair = {s: v["ratio_to_average"] for s, v in metrics["by_segment"].items()
              if v["genuine_rows"] >= FAIRNESS_MIN_ROWS and (v["ratio_to_average"] or 0) > FAIRNESS_MAX_RATIO}
    gate = {"beats_baseline": metrics["pr_auc"] > metrics["baseline_logistic_pr_auc"],
            "fairness_ok": not unfair, "unfair_segments": unfair}
    gate["passed"] = gate["beats_baseline"] and gate["fairness_ok"]

    version = time.strftime("risk-%Y%m%d-%H%M%S", time.gmtime())
    out = (out_root or root()) / version
    out.mkdir(parents=True)
    names = []
    for k, b in enumerate(boosters):
        names.append(f"booster_{k}.txt")
        b.save_model(str(out / names[-1]), num_iteration=b.best_iteration)
    (out / "calibration.json").write_text(json.dumps({"x": iso.X_thresholds_.tolist(),
                                                      "y": iso.y_thresholds_.tolist()}))
    card = {
        "version": version, "createdAt": int(time.time()), "features": FEATURES, "monotone": MONOTONE,
        "historyDays": HISTORY_DAYS,
        "boosters": names, "params": params, "trees": [b.best_iteration for b in boosters],
        "cutoffs": cutoffs, "frictionTargets": GENUINE_SHARE_AT_OR_ABOVE,
        "data": {"source": "synthetic (amfa.engine.risk.scenarios)", "customers": customers, "days": days,
                 "seed": seed, "rows": len(rows), "fraudRows": int(y.sum()),
                 "scenarioRows": dict(sorted(Counter(scenario).items())),
                 "split": {"trainDays": TRAIN_END, "validDays": VALID_END - TRAIN_END,
                           "testDays": days - VALID_END}},
        "metrics": metrics, "gate": gate,
        "limits": "Trained only on simulated customers and the attack types in scenarios.py. "
                  "Retrain on real labelled outcomes before trusting these numbers.",
    }
    (out / "card.json").write_text(json.dumps(card, indent=2))
    (out / MANIFEST).write_text(json.dumps({"files": {n: sha256(out / n) for n in
                                                      names + ["calibration.json", "card.json"]}}, indent=2))
    RiskModel(out)  # proves the written model loads and verifies
    if activate and (gate["passed"] or force):
        (out.parent / ACTIVE).write_text(version)
    print(json.dumps({"version": version, "gate": gate, "metrics": metrics}, indent=2))
    print(f"{'activated' if activate and (gate['passed'] or force) else 'NOT activated'}: {out}  "
          f"({time.time() - t0:.0f}s)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--customers", type=int, default=20000)
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-activate", action="store_true")
    ap.add_argument("--force", action="store_true", help="activate even if the release gate fails")
    a = ap.parse_args()
    run(a.customers, a.days, a.seed, activate=not a.no_activate, force=a.force)


if __name__ == "__main__":
    main()
