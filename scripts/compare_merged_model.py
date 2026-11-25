"""Is the merged risk model better than both models it came from? Trains all of them on the same simulated payments and scores
them on the same held-out payments.

    python scripts/compare_merged_model.py                      # 2000 + 2000 users, three independent populations (seeds 7, 11, 13)
    python scripts/compare_merged_model.py --customers 500 --upi-users 500 --seeds 7 --out /tmp/cmp.json

Models (each gets the same small hyperparameter search on its validation rows, then an ensemble of 3 seeds; every LightGBM model
uses the same settings, with "intermediate" monotone constraints for speed):
  platform   the platform's model as it is on main: its 44 inputs, LightGBM, trained on the platform's simulator only
  demopay    DemoPay's model as it is on Research/face-auth: its 46 inputs, three detectors + surprise, trained on its simulator only
  merged     the merged model: all 71 inputs (the platform's 44, DemoPay's phone and network signals, and five worked out from
             history and the balance: rising amounts to a new payee, new payees in 3 days, share of balance, signals present),
             trained on both simulators
  platform_both_data   ablation: the platform's 44 inputs trained on both simulators, to tell the gain from more data apart
             from the gain from the new inputs

Scored on each simulator's test days separately (the platform's attacks, and DemoPay's UPI scams), with the platform's own
yardsticks: average precision, and the share of fraud caught when only 5% / 1% / 0.2% of genuine payments may be stepped up
(the T2 / T3 / T4 friction budgets). All data is simulated: this compares methods, not real-world accuracy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amfa.engine.risk import merged_data as M  # noqa: E402
from amfa.engine.risk.features import CATEGORICAL, CORE_FEATURES, FEATURES, MONOTONE, vector  # noqa: E402
from amfa.engine.risk.threat import GENUINE_SHARE_AT_OR_ABOVE  # noqa: E402
from amfa.engine.risk.upi import features as UF  # noqa: E402
from amfa.engine.risk.upi import train as UT  # noqa: E402

# the same choices for every LightGBM model: tree size and learning rate, picked on its own validation rows
GRID = [dict(num_leaves=nl, min_child_samples=60, lambda_l2=5.0, learning_rate=lr) for lr in (0.1, 0.05) for nl in (15, 31, 63)]
SEEDS = 3
BUDGETS = {"T2": GENUINE_SHARE_AT_OR_ABOVE["T2"], "T3": GENUINE_SHARE_AT_OR_ABOVE["T3"], "T4": GENUINE_SHARE_AT_OR_ABOVE["T4"]}
DP_FAMILY = {"UNAUTHORISED": "takeover", "COACHED": "coached", "PAYEE_RISK": "payee", "FIRST_PARTY": "payee"}


def lgb_model(X, y, w, tr, va, names, log):
    """The platform's trainer (train._base_params, early stopping on validation PR-AUC), restricted to [names]."""
    import lightgbm as lgb
    cols = [FEATURES.index(n) for n in names]
    cat = [i for i, n in enumerate(names) if n in CATEGORICAL]
    # the same settings for every LightGBM model here; "intermediate" monotone constraints instead of main's "advanced",
    # which is ~16x slower on this data (minutes per fit) and gave similar validation scores in a timing check
    base = dict(objective="binary", feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                metric="average_precision", verbose=-1, monotone_constraints=[MONOTONE.get(n, 0) for n in names],
                monotone_constraints_method="intermediate")
    dtr = lgb.Dataset(X[tr][:, cols], y[tr], weight=w[tr], feature_name=list(names), categorical_feature=cat, free_raw_data=False)
    dva = lgb.Dataset(X[va][:, cols], y[va], weight=w[va], reference=dtr, free_raw_data=False)
    fit = lambda p: lgb.train(p, dtr, num_boost_round=3000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])  # noqa: E731
    best = None
    for g in GRID:
        b = fit(base | g | {"seed": 7})
        ap = b.best_score["valid_0"]["average_precision"]
        log(f"    {g} -> valid AP {ap:.4f} @ {b.best_iteration} trees")
        if best is None or ap > best[0]:
            best = (ap, g, b)
    boosters = [best[2]] + [fit(base | best[1] | {"seed": 7 + k}) for k in range(1, SEEDS)]
    return lambda Xs: np.mean([b.predict(Xs[:, cols], num_iteration=b.best_iteration) for b in boosters], axis=0), best[1]


def merged_model(X, y, w, wb, tr, va, log):
    """The merged model as train.py builds it: general boosters on both simulators, plus a specialist for each kind of
    payment (the bank sent phone/network signals, or it did not), each scoring with the general ones half and half."""
    has = X[:, FEATURES.index("signals_present")] == 1
    log("    general (both simulators)")
    gen, g = lgb_model(X, y, wb, tr, va, FEATURES, log)
    experts = {}
    for name, m in (("signals", has), ("no_signals", ~has)):
        if (tr & m & (y == 1)).sum() >= 50 and (tr & ~m).any():
            log(f"    specialist: {name}")
            experts[name] = lgb_model(X, y, w, tr & m, va & m, FEATURES, log)[0]

    def score(Xs):
        h = Xs[:, FEATURES.index("signals_present")] == 1
        out = gen(Xs)
        for name, mask in (("signals", h), ("no_signals", ~h)):
            if name in experts and mask.any():
                out[mask] = (out[mask] + experts[name](Xs[mask])) / 2
        return out
    return score, {"general": g, "specialists": sorted(experts)}


def demopay_model(Xu, y, w, fam, tr, va, log):
    """DemoPay's trainer (upi/train.train_variant), taking already-sampled rows with their weights instead of sampling again;
    otherwise the same: three one-vs-rest detectors with DemoPay's config search, the payee-intelligence outage augmentation,
    surprise against genuine validation payments, and the router."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(0)
    dfam = np.array([DP_FAMILY.get(f, "legit") if f else "legit" for f in fam])
    labels = {"takeover": (dfam == "takeover") & (y == 1), "coached": (dfam == "coached") & (y == 1), "payee": y == 1}
    rows = np.flatnonzero(tr)
    Xtr = Xu[rows].copy()
    outage = rng.random(len(rows)) < 0.08
    for f in UT.PAYEE_INTEL:
        Xtr[outage, UF.INDEX[f]] = np.nan
    heads = {}
    for h in UT.HEADS:
        best = None
        for cfg in UT.CONFIGS:
            m, idx = UT.fit(Xtr, labels[h][rows].astype(int), w[rows], UF.FEATURES, cfg, 0)
            ap = average_precision_score(labels[h][va], UT.score(m, idx, Xu[va]), sample_weight=w[va]) if labels[h][va].sum() else 0.0
            if best is None or ap > best["val_ap"]:
                best = dict(model=m, idx=idx, cfg=cfg, val_ap=ap)
        log(f"    {h}: valid AP {best['val_ap']:.4f}")
        heads[h] = best
    legit_val = va & (y == 0)
    for h in UT.HEADS:
        heads[h]["grid"] = UT.make_grid(UT.score(heads[h]["model"], heads[h]["idx"], Xu[legit_val]))
    fr = np.flatnonzero(tr & (y == 1))
    rm = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.1, max_leaf_nodes=7, min_samples_leaf=20, early_stopping=False,
                                        random_state=0).fit(Xu[fr], np.array([["takeover", "coached", "payee"].index(dfam[i]) for i in fr]))
    V = {"heads": heads, "router": {"model": rm, "idx": list(range(len(UF.FEATURES)))}}
    return lambda Xs: UT.surprises(V, Xs).max(1), V


def wq(x, w, q):
    o = np.argsort(x)
    c = np.cumsum(w[o]) / w[o].sum()
    return float(x[o][min(int(np.searchsorted(c, q)), len(x) - 1)])


def metrics(s, y, w, fam, inc, mask):
    from sklearn.metrics import average_precision_score
    s, y, w, fam, inc = s[mask], y[mask], w[mask], fam[mask], inc[mask]
    g, f = y == 0, y == 1
    out = {"fraud_rows": int(f.sum()), "genuine_rows": int(g.sum()),
           "average_precision": round(float(average_precision_score(y, s, sample_weight=w)), 4)}
    for k, b in BUDGETS.items():
        thr = wq(s[g], w[g], 1 - b)
        hit = s > thr
        out[f"caught_at_{k}"] = round(float(np.average(hit[f], weights=w[f])), 4)
        caught = {i for i in set(inc[f]) if hit[f][inc[f] == i].any()}
        out[f"incidents_caught_at_{k}"] = round(len(caught) / max(len(set(inc[f])), 1), 4)
        if k == "T3":
            out["caught_at_T3_by_family"] = {fm: round(float(hit[f][fam[f] == fm].mean()), 4)
                                             for fm in sorted(set(fam[f])) if (fam[f] == fm).sum() >= 5}
    return out


def run_one(customers: int, upi_users: int, seed: int, log) -> dict:
    rows = M.platform_rows(customers, seed=seed, upi_inputs=True)
    log(f"seed {seed}: platform {len(rows)} rows, {sum(r.label for r in rows)} fraud")
    up = M.upi_rows(upi_users, seed=seed, upi_inputs=True)
    log(f"seed {seed}: DemoPay  {len(up)} rows, {sum(r.label for r in up)} fraud")
    rows += up
    X = np.array([vector(r.features) for r in rows], dtype=np.float64)
    Xu = np.array([r.upi_x for r in rows], dtype=np.float64)
    y = np.array([r.label for r in rows])
    w = np.array([r.weight for r in rows], dtype=np.float64)
    fam = np.array([r.family or "" for r in rows])
    inc = np.array([r.incident or "" for r in rows])
    P = np.array([r.source == "platform" for r in rows])
    U = ~P
    tr, va, te = M.split(rows)
    wb = M.balance_weights(w, U, tr)        # as train.py does: DemoPay's rows weigh a quarter of the platform's
    models, scores = {}, {}
    log("  platform model (its 44 inputs, platform data)")
    f, models["platform"] = lgb_model(X, y, w, tr & P, va & P, CORE_FEATURES, log)
    scores["platform"] = f(X)
    log("  DemoPay model (its 46 inputs, DemoPay data)")
    f, V = demopay_model(Xu, y, w, fam, tr & U, va & U, log)
    scores["demopay"] = f(Xu)
    models["demopay"] = "three detectors (DemoPay CONFIGS search)"
    log(f"  merged model ({len(FEATURES)} inputs, both simulators, general + specialists)")
    f, models["merged"] = merged_model(X, y, w, wb, tr, va, log)
    scores["merged"] = f(X)
    log("  ablation: platform's 44 inputs, both simulators")
    f, models["platform_both_data"] = lgb_model(X, y, wb, tr, va, CORE_FEATURES, log)
    scores["platform_both_data"] = f(X)
    res = {"data": {"platform_rows": int(P.sum()), "upi_rows": int(U.sum()),
                    "test_fraud_rows": {"platform": int((te & P & (y == 1)).sum()), "upi": int((te & U & (y == 1)).sum())}},
           "params": models, "results": {}, "validation": {}}
    for name, sc in scores.items():
        res["results"][name] = {"platform_test": metrics(sc, y, w, fam, inc, te & P), "upi_test": metrics(sc, y, w, fam, inc, te & U)}
        res["validation"][name] = {"platform": metrics(sc, y, w, fam, inc, va & P), "upi": metrics(sc, y, w, fam, inc, va & U)}
    res["bootstrap"] = bootstrap(scores, y, w, fam, inc, te, P, U)
    import lightgbm as lgb
    fams = sorted(set(fam[y == 1]))
    frt, fte = tr & (y == 1), te & (y == 1)
    rb = lgb.train(dict(objective="multiclass", num_class=len(fams), learning_rate=0.1, num_leaves=15, min_child_samples=10,
                        verbose=-1, seed=7), lgb.Dataset(X[frt], np.array([fams.index(x) for x in fam[frt]]),
                                                         weight=M.class_balanced(fam[frt])), num_boost_round=200)
    pred = np.array(fams)[rb.predict(X[fte]).argmax(1)]
    acc = lambda pr, truth, fm: round(float((pr[truth == fm] == fm).mean()), 4)  # noqa: E731
    res["router"] = {"merged_on_platform_test": {fm: acc(pred[P[fte]], fam[fte & P], fm) for fm in fams if (fam[fte & P] == fm).any()},
                     "merged_on_upi_test": {fm: acc(pred[U[fte]], fam[fte & U], fm) for fm in fams if (fam[fte & U] == fm).any()}}
    dp = np.array(["UNAUTHORISED", "COACHED", "PAYEE_RISK"])[UT.route(V, Xu[fte & U])]
    res["router"]["demopay_on_upi_test"] = {fm: acc(dp, fam[fte & U], fm) for fm in ("UNAUTHORISED", "COACHED", "PAYEE_RISK")
                                            if (fam[fte & U] == fm).any()}
    return res


YARDSTICKS = ["average_precision", "caught_at_T2", "caught_at_T3", "caught_at_T4", "incidents_caught_at_T2", "incidents_caught_at_T3",
              "incidents_caught_at_T4"]


def bootstrap(scores, y, w, fam, inc, te, P, U, n=200, seed=0):
    """Resampled test sets (genuine payments and whole fraud incidents drawn with replacement; an incident drawn twice counts
    twice): for each yardstick, the merged model minus the better of the two original models. Returns the per-resample
    differences, pooled over seeds by summary()."""
    rng = np.random.default_rng(seed)
    out = {}
    for src, mask in (("platform_test", te & P), ("upi_test", te & U)):
        idx = np.flatnonzero(mask)
        gen_i = idx[y[idx] == 0]
        incs = np.unique(inc[idx][y[idx] == 1])
        by_inc = {i: idx[(y[idx] == 1) & (inc[idx] == i)] for i in incs}
        diffs = {k: [] for k in YARDSTICKS}
        for _ in range(n):
            drawn = rng.choice(incs, len(incs))
            pick = np.concatenate([rng.choice(gen_i, len(gen_i))] + [by_inc[i] for i in drawn])
            ids = np.concatenate([inc[gen_i[:0]]] + [np.array([f"{i}#{k}"] * len(by_inc[i])) for k, i in enumerate(drawn)])
            inc_b = np.concatenate([np.full(len(gen_i), ""), ids])
            all_true = np.ones(len(pick), dtype=bool)
            r = {k: metrics(sc[pick], y[pick], w[pick], fam[pick], inc_b, all_true) for k, sc in scores.items()}
            for k in YARDSTICKS:
                diffs[k].append(r["merged"][k] - max(r["platform"][k], r["demopay"][k]))
        out[src] = diffs
    return out


def summary(per_seed: dict) -> dict:
    """Mean over seeds of every number, and for each yardstick whether the merged model is at least as good as both others in
    the mean and in every seed."""
    seeds = list(per_seed)
    first = per_seed[seeds[0]]["results"]
    out = {"mean": {}, "merged_at_least_as_good": {}}
    for name in first:
        out["mean"][name] = {}
        for t in ("platform_test", "upi_test"):
            out["mean"][name][t] = {k: round(float(np.mean([per_seed[s]["results"][name][t][k] for s in seeds])), 4)
                                    for k, v in first[name][t].items() if isinstance(v, float)}
    for t in ("platform_test", "upi_test"):
        for k in out["mean"]["merged"][t]:
            if k.endswith("_rows"):
                continue
            m = [per_seed[s]["results"]["merged"][t][k] for s in seeds]
            best_other = [max(per_seed[s]["results"][o][t][k] for o in ("platform", "demopay")) for s in seeds]
            out["merged_at_least_as_good"][f"{t}.{k}"] = {
                "mean": out["mean"]["merged"][t][k] >= max(out["mean"][o][t][k] for o in ("platform", "demopay")),
                "seeds": sum(a >= b for a, b in zip(m, best_other)), "of": len(seeds)}
    out["difference_ci"] = {}
    for t in ("platform_test", "upi_test"):
        for k in YARDSTICKS:
            d = np.mean([per_seed[s]["bootstrap"][t][k] for s in seeds], axis=0)     # mean over seeds, per resample
            lo, hi = np.percentile(d, [2.5, 97.5])
            out["difference_ci"][f"{t}.{k}"] = {"mean": round(float(d.mean()), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
                                                "verdict": "better" if lo > 0 else "worse" if hi < 0 else "no measurable difference"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customers", type=int, default=2000)
    ap.add_argument("--upi-users", type=int, default=2000)
    ap.add_argument("--seeds", default="17,19,23", help="independent simulated populations, comma separated")
    ap.add_argument("--out", default="docs/merged-risk-model-results.json")
    a = ap.parse_args()
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731
    per_seed = {}
    for sd in [int(x) for x in a.seeds.split(",")]:
        per_seed[str(sd)] = run_one(a.customers, a.upi_users, sd, log)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"settings": vars(a), "per_seed": per_seed}, indent=2))
    res = {"settings": vars(a), "summary": summary(per_seed), "per_seed": per_seed, "seconds": round(time.time() - t0)}
    Path(a.out).write_text(json.dumps(res, indent=2))
    print("\n| model | platform test: AP | caught @T2 | @T3 | @T4 | DemoPay test: AP | caught @T2 | @T3 | @T4 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, r in res["summary"]["mean"].items():
        p, u = r["platform_test"], r["upi_test"]
        print(f"| {name} | {p['average_precision']} | {p['caught_at_T2']} | {p['caught_at_T3']} | {p['caught_at_T4']} "
              f"| {u['average_precision']} | {u['caught_at_T2']} | {u['caught_at_T3']} | {u['caught_at_T4']} |")
    for k, v in res["summary"]["difference_ci"].items():
        print(f"{k:42s} merged - best other {v['mean']:+.4f}  95% CI [{v['ci95'][0]:+.4f}, {v['ci95'][1]:+.4f}]  {v['verdict']}")
    log(f"written {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
