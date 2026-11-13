"""
Train, threshold, evaluate and package the combined payment-risk model (takeover + coached + bad payee).

    cd backend && .venv/bin/python -m ato.train --users 6000 --seed 7

Writes models/ato/{ato_full,ato_server_only}.joblib, models/ato/model_card.json and docs/ato-model-report.md.
Read the report's "What this does and does not show" first: the data is synthetic (ato/simulate.py), so the
numbers validate the pipeline and the model's behaviour under stress, not real-world accuracy.

Method, in short:
  * Three one-vs-rest heads (takeover, coached, payee) of gradient-boosted trees (native NaN handling, monotone
    constraints on features with an unambiguous risk direction); small config search per head on validation PR-AUC.
  * Each head's score is turned into a "surprise" = -log(share of legitimate validation payments scoring at least
    as high). A payment's severity comes from the largest surprise over the heads, thresholded so that at most a
    fixed share of LEGITIMATE validation payments reach each level (the friction budget). That keeps the union of
    three detectors inside one budget and does not depend on how common fraud is in production.
  * Time-based split: train days 0-90, validation 90-120, test 120-150. 15% of incidents are never reported
    (labelled legitimate) in train/validation; test uses ground truth.
  * Two variants: `full` (server facts + attested device telemetry) and `server_only` (no on-device signals).
"""
import argparse
import json
import math
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

from ato.features import DEVICE_FEATURES, FEATURE_GROUPS, FEATURES, INDEX, MONOTONE, SERVER_FEATURES
from ato.simulate import ALL_TYPES, FAMILY, build_dataset

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "ato"
REPORT_PATH = ROOT.parent / "docs" / "ato-model-report.md"
BASELINE_PATH = ROOT.parent / "docs" / "ato-baseline.json"
CACHE = Path(__file__).resolve().parent / "_cache"

HEADS = ["takeover", "coached", "payee"]
THREAT_NAME = {"takeover": "UNAUTHORISED", "coached": "COACHED", "payee": "PAYEE_RISK"}
# Friction budgets: max share of LEGITIMATE payments at or above each severity.
BUDGETS = {"MEDIUM": 0.02, "HIGH": 0.003, "CRITICAL": 0.0005}
LEVELS = ("CRITICAL", "HIGH", "MEDIUM")
CONFIGS = [
    dict(max_iter=200, learning_rate=0.10, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0, mono=False),
    dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0, mono=True),
    dict(max_iter=400, learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=100, l2_regularization=5.0, mono=True),
]
PAYEE_INTEL = ["payee_age_days", "payee_fan_in_24h", "payee_fri", "payee_complaints"]
LOAO_TYPES = ["remote_access", "digital_arrest", "collect_qr", "task_invest", "lookalike"]
TAILS = np.concatenate([np.linspace(1.0, 0.01, 60)[:-1], np.logspace(-2, -5, 120)])  # descending tail probabilities
LEGIT_KEEP = 0.25  # keep this share of legitimate training rows, weighted 1/LEGIT_KEEP, for speed


# ---- labels, model, surprise ---------------------------------------------------------------------
def head_true(d):
    return {"takeover": d["family"] == "takeover", "coached": d["family"] == "coached", "payee": d["y"] == 1}


def head_obs(d):
    obs = d["y_obs"] == 1
    return {h: (m & obs).astype(int) for h, m in head_true(d).items()}


def fit(X, y, w, features, cfg, seed=0):
    idx = [INDEX[f] for f in features]
    mono = [MONOTONE.get(f, 0) for f in features] if cfg["mono"] else None
    m = HistGradientBoostingClassifier(
        max_iter=cfg["max_iter"], learning_rate=cfg["learning_rate"], max_leaf_nodes=cfg["max_leaf_nodes"],
        min_samples_leaf=cfg["min_samples_leaf"], l2_regularization=cfg["l2_regularization"], monotonic_cst=mono,
        early_stopping=False, random_state=seed)
    m.fit(X[:, idx], y, sample_weight=w)
    return m, idx


def score(m, idx, X):
    return m.predict_proba(X[:, idx])[:, 1]


def make_grid(legit_scores):
    return {"tails": TAILS.tolist(), "scores": np.quantile(legit_scores, 1 - TAILS).tolist()}


def surprise(grid, s):
    t = np.interp(s, np.asarray(grid["scores"]), np.asarray(grid["tails"]))
    return -np.log(np.clip(t, 1e-5, 1.0))


def train_variant(d, features, tr, va, cfgs=None, exclude=None, seed=0):
    """Fit the three heads on `tr` rows (minus `exclude`), build surprise grids and severity thresholds on `va`."""
    X, yo = d["X"], d["y_obs"]
    labels = head_obs(d)
    rng = np.random.default_rng(seed)
    trm = tr & ~exclude if exclude is not None else tr
    keep = trm & ((yo == 1) | (rng.random(len(yo)) < LEGIT_KEEP))
    rows = np.where(keep)[0]
    w = np.where(yo[rows] == 1, 1.0, 1.0 / LEGIT_KEEP)
    Xtr = X[rows].copy()
    # robustness: in 8% of training rows the payee-intelligence feeds are down (age, senders, FRI, complaints missing)
    outage = rng.random(len(rows)) < 0.08
    for f in PAYEE_INTEL:
        Xtr[outage, INDEX[f]] = np.nan
    heads, log = {}, {}
    for h in HEADS:
        best = None
        for cfg in ([cfgs[h]] if cfgs else CONFIGS):
            m, idx = fit(Xtr, labels[h][rows], w, features, cfg, seed)
            ap = average_precision_score(labels[h][va], score(m, idx, X[va])) if labels[h][va].sum() else 0.0
            log.setdefault(h, []).append((cfg, ap))
            if best is None or ap > best["val_ap"]:
                best = dict(model=m, idx=idx, cfg=cfg, val_ap=ap)
        heads[h] = best
    sv = {h: score(heads[h]["model"], heads[h]["idx"], X[va]) for h in HEADS}
    legit_val = yo[va] == 0
    for h in HEADS:
        heads[h]["grid"] = make_grid(sv[h][legit_val])
    mx = np.max([surprise(heads[h]["grid"], sv[h]) for h in HEADS], axis=0)
    thr = {lvl: float(np.quantile(mx[legit_val], 1 - b)) for lvl, b in BUDGETS.items()}
    # router: which family a flagged payment belongs to (trained on observed fraud only), so the action matches the scam
    fr = np.where(trm & (yo == 1))[0]
    fam = {"takeover": 0, "coached": 1, "payee": 2}
    idx_all = [INDEX[f] for f in features]
    rm = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.1, max_leaf_nodes=7, min_samples_leaf=20,
                                        early_stopping=False, random_state=seed)
    rm.fit(X[fr][:, idx_all], np.array([fam[f] for f in d["family"][fr]]))
    return {"heads": heads, "thr": thr, "features": features, "search": log, "router": {"model": rm, "idx": idx_all}}


def route(V, X):
    """Family (0 takeover, 1 coached, 2 payee) the router assigns to each payment."""
    r = V["router"]
    return r["model"].predict(X[:, r["idx"]])


def surprises(V, X):
    return np.stack([surprise(V["heads"][h]["grid"], score(V["heads"][h]["model"], V["heads"][h]["idx"], X)) for h in HEADS], axis=1)


# ---- metrics -----------------------------------------------------------------------------------
def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    dd = 1 + z * z / n
    c = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return (c - h, c + h)


def incident_stats(s, thr, d, mask):
    """Incident-level outcomes at one threshold. A flagged payment at HIGH+ stops the scam (face challenge, wait,
    approval or block), so everything the incident would still have moved from that payment on is saved; payments
    before the first flag go through."""
    idx = np.where(mask & (d["y"] == 1))[0]
    out = {"n": 0, "caught": 0, "first_caught": 0, "saved": 0.0, "total": 0.0}
    for inc in np.unique(d["incident"][idx]):
        rows = idx[d["incident"][idx] == inc]
        rows = rows[np.argsort(d["day"][rows])]
        flagged = np.where(s[rows] > thr)[0]
        amt = d["amount"][rows]
        out["n"] += 1
        out["total"] += float(amt.sum())
        if len(flagged):
            out["caught"] += 1
            out["first_caught"] += int(flagged[0] == 0)
            out["saved"] += float(amt[flagged[0]:].sum())
    return out


def summ(o):
    n = max(o["n"], 1)
    lo, hi = wilson(o["caught"], o["n"])
    return {"incidents": o["n"], "caught": o["caught"] / n, "caught_ci": (lo, hi),
            "first_payment_caught": o["first_caught"] / n, "rupees_saved": o["saved"] / max(o["total"], 1e-9)}


def evaluate(mx, thr, d, U=None):
    y = d["y"]
    legit = y == 0
    res = {}
    for lvl, t in thr.items():
        res[f"fpr_{lvl}"] = float((mx[legit] > t).mean())
        res[f"inc_{lvl}"] = summ(incident_stats(mx, t, d, np.ones(len(y), bool)))
    res["txn_recall_HIGH"] = float((mx[y == 1] > thr["HIGH"]).mean())
    return res


def rules_baseline(X):
    """Hand-written rules with the weights of services/txn_risk.py, evaluated on feature vectors (max over the three kinds)."""
    c = lambda name: np.nan_to_num(X[:, INDEX[name]], nan=0.0)  # noqa: E731
    raw = lambda name: X[:, INDEX[name]]  # noqa: E731
    ratio, drain = c("amt_over_p95"), c("amt_over_balance")
    amount_ev = np.where(ratio >= 8, 0.40, np.where(ratio >= 3, 0.25, 0.0))
    new_payee = raw("new_payee") > 0.5
    call = c("call_active") > 0.5

    def noisy(terms):
        p = np.ones(len(X))
        for t in terms:
            p *= 1 - np.asarray(t, dtype=float)
        return 1 - p

    take = noisy([0.70 * (c("remote_access_active") > 0.5), 0.50 * (raw("device_bound_hours") < 24),
                  0.30 * ((c("old_device_active") > 0.5) & (raw("device_bound_hours") < 24)), 0.45 * (raw("sim_changed_hours") < 72),
                  0.30 * (raw("sideload_hours") < 48), 0.15 * new_payee, amount_ev,
                  0.20 * ((raw("is_night") > 0.5) & (raw("night_share_user") < 0.05) & new_payee)])
    coach = noisy([0.55 * ((raw("channel_collect") > 0.5) & (c("requester_is_contact") < 0.5) & new_payee), 0.30 * call,
                   0.15 * (call & (c("call_unknown_number") > 0.5)), 0.20 * (call & (c("call_minutes") >= 20)),
                   0.30 * (call & new_payee & (ratio >= 1.5)), 0.50 * (c("remote_access_active") > 0.5), 0.15 * new_payee, amount_ev,
                   np.where(drain >= 0.8, 0.40, np.where(drain >= 0.5, 0.25, 0.0)),
                   np.where(raw("new_payees_72h") >= 3, 0.50, np.where(raw("new_payees_72h") >= 2, 0.15, 0.0)),
                   0.35 * (raw("escalating_to_payee") > 0.5), 0.20 * (new_payee & (c("payee_risky_category") > 0.5))])
    fri = np.where(c("payee_fri") >= 3, 0.95, np.where(c("payee_fri") >= 2, 0.75, np.where(c("payee_fri") >= 1, 0.40, 0.0)))
    payee = noisy([fri, 0.20 * (raw("payee_age_days") <= 7), np.where(raw("payee_fan_in_24h") >= 25, 0.60, np.where(raw("payee_fan_in_24h") >= 10, 0.40, 0.0)),
                   np.where(c("payee_complaints") >= 3, 0.70, np.where(c("payee_complaints") >= 1, 0.40, 0.0)),
                   0.50 * (raw("payee_lookalike") >= 0.85)])
    return np.max([take, coach, payee], axis=0)


# ---- pipeline ----------------------------------------------------------------------------------
def load_data(users, seed):
    CACHE.mkdir(exist_ok=True)
    p = CACHE / f"ds2_{users}_{seed}.npz"
    if p.exists():
        return dict(np.load(p, allow_pickle=False))
    d = build_dataset(users, seed)
    np.savez_compressed(p, **d)
    return d


def sub(d, m):
    return {k: (v[m] if k != "features" else v) for k, v in d.items()}


def run(users=6000, seed=7, quick=False, save=True, log=print):
    t0 = time.time()
    d = load_data(users, seed)
    day, yo = d["day"], d["y_obs"]
    tr, va, te = day < 90, (day >= 90) & (day < 120), day >= 120
    dte, Xte = sub(d, te), d["X"][te]
    n_inc = len(np.unique(d["incident"][d["y"] == 1]))
    log(f"data: {len(day):,} payments, {int(d['y'].sum())} fraud payments in {n_inc} incidents; train/val/test = {tr.sum():,}/{va.sum():,}/{te.sum():,} ({time.time() - t0:.0f}s)")

    V = {"full": train_variant(d, FEATURES, tr, va), "server_only": train_variant(d, SERVER_FEATURES, tr, va)}
    for n, v in V.items():
        log(f"{n}: val PR-AUC " + ", ".join(f"{h}={v['heads'][h]['val_ap']:.3f}" for h in HEADS) + f"; thr={ {k: round(x, 2) for k, x in v['thr'].items()} }")

    R, scores = {}, {}
    for n, v in V.items():
        U = surprises(v, Xte)
        scores[n] = U
        R[n] = evaluate(U.max(1), v["thr"], dte)
    # rules baseline, thresholded to the same friction budgets on validation legit payments
    bv = rules_baseline(d["X"][va])
    bthr = {lvl: float(np.quantile(bv[yo[va] == 0], 1 - b)) for lvl, b in BUDGETS.items()}
    R["rules"] = evaluate(rules_baseline(Xte), bthr, dte)

    full, thr = V["full"], V["full"]["thr"]
    mx = scores["full"].max(1)
    E = {}
    E["by_type"], E["by_type_server"] = {}, {}
    for t in ALL_TYPES:
        m = dte["atype"] == t
        E["by_type"][t] = {"high": summ(incident_stats(mx, thr["HIGH"], dte, m)), "medium": summ(incident_stats(mx, thr["MEDIUM"], dte, m))}
        E["by_type_server"][t] = summ(incident_stats(scores["server_only"].max(1), V["server_only"]["thr"]["HIGH"], dte, m))
    E["by_family"] = {f: summ(incident_stats(mx, thr["HIGH"], dte, dte["family"] == f)) for f in ("takeover", "coached", "payee")}
    for_f = {f: summ(incident_stats(scores["server_only"].max(1), V["server_only"]["thr"]["HIGH"], dte, dte["family"] == f)) for f in ("takeover", "coached", "payee")}
    E["by_family_server"] = for_f
    bands = [("loud (<0.25)", dte["stealth"] < 0.25), ("mid (0.25-0.5)", (dte["stealth"] >= 0.25) & (dte["stealth"] < 0.5)), ("stealthy (>=0.5)", dte["stealth"] >= 0.5)]
    E["by_stealth"] = {n: summ(incident_stats(mx, thr["HIGH"], dte, m)) for n, m in bands}
    # routing: which head named the threat for flagged-at-HIGH fraud payments
    fl = (dte["y"] == 1) & (mx > thr["HIGH"])
    pred = route(full, Xte)
    argmax_pred = np.argmax(scores["full"], axis=1)
    E["routing"] = {f: [float(((pred == i) & fl & (dte["family"] == f)).sum() / max((fl & (dte["family"] == f)).sum(), 1)) for i in range(3)]
                    for f in ("takeover", "coached", "payee")}
    E["routing_argmax"] = {f: [float(((argmax_pred == i) & fl & (dte["family"] == f)).sum() / max((fl & (dte["family"] == f)).sum(), 1)) for i in range(3)]
                           for f in ("takeover", "coached", "payee")}
    # legit friction by situation
    E["fpr_by_tag"] = {}
    for tag in sorted(set(dte["tag"]) - {"attack"}):
        m = dte["tag"] == tag
        E["fpr_by_tag"][tag] = (int(m.sum()), float((mx[m] > thr["MEDIUM"]).mean()), float((mx[m] > thr["HIGH"]).mean()))
    # stress tests
    dev_idx = [INDEX[f] for f in DEVICE_FEATURES]
    Xmiss = Xte.copy(); Xmiss[:, dev_idx] = np.nan
    spoof = Xte.copy()
    tk = dte["family"] == "takeover"
    for f, val in [("remote_access_active", 0), ("bio_similarity", 0.85), ("integrity_fail", 0), ("overlay_accessibility", 0),
                   ("sideload_hours", np.nan), ("login_to_pay_s", 30), ("call_active", 0)]:
        spoof[tk, INDEX[f]] = val
    hide = Xte.copy()
    co = dte["family"] == "coached"
    for f, val in [("call_active", 0), ("call_minutes", 0), ("call_unknown_number", 0), ("remote_access_active", 0)]:
        hide[co, INDEX[f]] = val
    pay_idx = [INDEX[f] for f in ("payee_age_days", "payee_fan_in_24h", "payee_fri", "payee_complaints")]
    nopayee = Xte.copy(); nopayee[:, pay_idx] = np.nan
    clean = Xte.copy()  # every scam payee looks like an ordinary old account
    fr = dte["y"] == 1
    for f, val in [("payee_age_days", 500), ("payee_fan_in_24h", 1), ("payee_fri", 0), ("payee_complaints", 0), ("payee_is_merchant", 0)]:
        clean[fr, INDEX[f]] = val
    E["stress"] = {}
    for label, Xm, fam in [("normal", Xte, None), ("on-device signals missing for everyone", Xmiss, None),
                           ("payee intelligence unavailable (age, senders, FRI, complaints missing)", nopayee, None),
                           ("every scam payee looks like an ordinary old account", clean, None),
                           ("... same, takeover incidents only", clean, "takeover"), ("... same, coached incidents only", clean, "coached"),
                           ("takeover attacker forges on-device signals (takeover only)", spoof, "takeover"),
                           ("coached scammer hides call and screen-share signals (coached only)", hide, "coached")]:
        mm = surprises(full, Xm).max(1)
        mask = np.ones(len(mm), bool) if fam is None else dte["family"] == fam
        E["stress"][label] = summ(incident_stats(mm, thr["HIGH"], dte, mask))
    E["stress"]["server-only model (never sees on-device signals)"] = summ(incident_stats(scores["server_only"].max(1), V["server_only"]["thr"]["HIGH"], dte, np.ones(len(mx), bool)))
    # unseen scam type
    E["loao"] = {}
    if not quick:
        cfgs = {h: full["heads"][h]["cfg"] for h in HEADS}
        for t in LOAO_TYPES:
            v = train_variant(d, FEATURES, tr, va, cfgs=cfgs, exclude=(d["atype"] == t))
            m2 = surprises(v, Xte).max(1)
            E["loao"][t] = summ(incident_stats(m2, v["thr"]["HIGH"], dte, dte["atype"] == t))
    # shortcut check: can the PATTERN of missing values alone separate fraud from legit? (a high AUC means the
    # simulator, or a real data feed, leaks the label through missingness)
    nanf = np.isnan(d["X"])
    cols = np.where(nanf.any(axis=0))[0]
    km, kt = tr & ((yo == 1) | (np.random.default_rng(0).random(len(yo)) < 0.25)), te
    sm = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=7, early_stopping=False, random_state=0).fit(nanf[km][:, cols], yo[km])
    from sklearn.metrics import roc_auc_score
    E["missingness_auc"] = float(roc_auc_score(d["y"][kt], sm.predict_proba(nanf[kt][:, cols])[:, 1]))
    # latency and prevalence
    one = Xte[:1]
    t1 = time.perf_counter()
    for _ in range(100):
        for h in HEADS:
            full["heads"][h]["model"].predict_proba(one[:, full["heads"][h]["idx"]])
    E["latency_ms"] = (time.perf_counter() - t1) / 100 * 1000
    tpr, fpr = R["full"]["txn_recall_HIGH"], R["full"]["fpr_HIGH"]
    E["precision_at_prevalence"] = {pi: tpr * pi / (tpr * pi + fpr * (1 - pi)) for pi in (1e-2, 1e-3, 1e-4, 1e-5)}
    E["sim_prevalence"] = float(dte["y"].mean())

    # package
    legit_train = d["X"][tr & (yo == 0)]
    neutral = [None if np.isnan(legit_train[:, j]).mean() > 0.5 else float(np.nanmedian(legit_train[:, j])) for j in range(len(FEATURES))]
    deciles = {f: [float(q) for q in np.nanquantile(legit_train[:, j], np.linspace(0.1, 0.9, 9))]
               for j, f in enumerate(FEATURES) if not np.isnan(legit_train[:, j]).all()}
    card = {
        "version": time.strftime("%Y-%m-%d"), "trained_on": "synthetic (ato/simulate.py)", "users": users, "seed": seed,
        "features": FEATURES, "server_features": SERVER_FEATURES, "heads": HEADS, "budgets": BUDGETS,
        "variants": {n: {"thresholds": v["thr"], "grids": {h: v["heads"][h]["grid"] for h in HEADS},
                         "val_pr_auc": {h: v["heads"][h]["val_ap"] for h in HEADS}, "config": {h: v["heads"][h]["cfg"] for h in HEADS}}
                     for n, v in V.items()},
        "neutral": neutral, "feature_deciles": deciles, "test": R, "by_family": E["by_family"], "by_family_server": E["by_family_server"],
        "by_type": {t: v["high"] for t, v in E["by_type"].items()},
        "missingness_auc": E["missingness_auc"], "routing": E["routing"], "stress": {k: {"caught": v["caught"], "rupees_saved": v["rupees_saved"]} for k, v in E["stress"].items()},
        "limitations": "Synthetic data only; thresholds are friction budgets on simulated legitimate traffic and must be re-derived on real shadow-mode data before enforcement.",
    }
    if save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for n, v in V.items():
            joblib.dump({"heads": {h: {"model": v["heads"][h]["model"], "idx": v["heads"][h]["idx"]} for h in HEADS},
                         "router": v["router"], "features": v["features"]},
                        MODEL_DIR / f"ato_{n}.joblib", compress=3)
        (MODEL_DIR / "model_card.json").write_text(json.dumps(card, indent=1, default=float))
        write_report(card, R, E, V, d, te)
    log(f"done in {time.time() - t0:.0f}s")
    return card, R, E


# ---- report ------------------------------------------------------------------------------------
def pct(x):
    return f"{100 * x:.1f}%"


def rng_s(v):
    return f"{pct(v['caught'])} [{pct(v['caught_ci'][0])}-{pct(v['caught_ci'][1])}]"


def write_report(card, R, E, V, d, te):
    L, w = [], None
    w = L.append
    n_inc = len(np.unique(d["incident"][(d["y"] == 1) & te]))
    base = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else None
    w("# Payment-risk model report (generated by `python -m ato.train`)\n")
    w("## What this does and does not show\n")
    w("- Data is **synthetic** (`ato/simulate.py`). There is no public labelled Indian UPI fraud data. We wrote both the scams and the detector, "
      "so results partly confirm our own assumptions. These numbers show the pipeline works and how the model behaves under missing signals, "
      "scammers who hide, and unseen scam types. They are **not** real-world precision/recall.")
    w("- Fraud prevalence in the simulation is far higher than in production. Friction rates (share of *legitimate* payments challenged) do not "
      "depend on prevalence; precision does (see the prevalence table).")
    w(f"- Test slice: days 120-150, {int(te.sum()):,} payments, {n_inc} scam incidents across {len(ALL_TYPES)} types. Per-type rows have only a handful to a few dozen "
      "incidents, so read the ranges.\n")
    w("## Severity thresholds (friction budgets on legitimate validation payments)\n")
    w("| severity | max legit share |\n|---|---|")
    for lvl in ("MEDIUM", "HIGH", "CRITICAL"):
        w(f"| {lvl} | {100 * card['budgets'][lvl]:g}% |")
    w("\nThe budget is shared by all three heads (takeover, coached, payee): a payment reaches a level if ANY head is that surprised.\n")
    w("## Headline (test, HIGH = strong action: face check, wait/approval, or block)\n")
    w("| model | legit >= MEDIUM | legit >= HIGH | legit >= CRITICAL | incidents caught at HIGH [95% range] | first payment caught | rupees saved |\n|---|---|---|---|---|---|---|")
    for name, key in [("**Full ML (3 heads)**", "full"), ("Server-only ML", "server_only"), ("Hand-written rules", "rules")]:
        r = R[key]; i = r["inc_HIGH"]
        w(f"| {name} | {pct(r['fpr_MEDIUM'])} | {pct(r['fpr_HIGH'])} | {pct(r['fpr_CRITICAL'])} | {rng_s(i)} | {pct(i['first_payment_caught'])} | {pct(i['rupees_saved'])} |")
    w("\n## By family (full model, HIGH)\n")
    w("| family | incidents | caught [95% range] | first payment caught | rupees saved | server-only caught |\n|---|---|---|---|---|---|")
    for f in ("takeover", "coached", "payee"):
        v = E["by_family"][f]
        w(f"| {f} | {v['incidents']} | {rng_s(v)} | {pct(v['first_payment_caught'])} | {pct(v['rupees_saved'])} | {pct(E['by_family_server'][f]['caught'])} |")
    if base:
        tk = E["by_family"]["takeover"]
        w("\n**Regression bar (takeover-only model, frozen in `docs/ato-baseline.json`):** "
          f"caught {pct(base['full']['incidents_caught_HIGH'])}, rupees saved {pct(base['full']['rupees_saved_HIGH'])}, "
          f"legit at HIGH {pct(base['full']['legit_hit_HIGH'])}. Combined model on takeover incidents: caught {pct(tk['caught'])}, "
          f"rupees saved {pct(tk['rupees_saved'])}.")
    w("\n## By scam type (full model)\n")
    w("| scam | family | incidents | caught at MEDIUM+ | caught at HIGH [95% range] | first payment caught | rupees saved | server-only caught at HIGH |\n|---|---|---|---|---|---|---|---|")
    for t, v in E["by_type"].items():
        h = v["high"]
        w(f"| {t} | {FAMILY[t]} | {h['incidents']} | {pct(v['medium']['caught'])} | {rng_s(h)} | {pct(h['first_payment_caught'])} | {pct(h['rupees_saved'])} | {pct(E['by_type_server'][t]['caught'])} |")
    w("\n## Routing: which action family a flagged payment gets (flagged-at-HIGH fraud payments; rows = true family)\n")
    w("A small router (trained on fraud payments only) names the family, so a takeover gets an identity check and a coached scam gets a warning or hold.\n")
    w("| true family | routed takeover | routed coached | routed payee | (without router: most-surprised head: takeover / coached / payee) |\n|---|---|---|---|---|")
    for f, vals in E["routing"].items():
        a = E["routing_argmax"][f]
        w(f"| {f} | {pct(vals[0])} | {pct(vals[1])} | {pct(vals[2])} | {pct(a[0])} / {pct(a[1])} / {pct(a[2])} |")
    w("\n## By stealth (full model, HIGH)\n")
    w("Careful: stealthier incidents make more payments, and an incident counts as caught if any one payment is flagged, so the 'caught' column is not comparable across bands. Compare 'first payment caught'.\n")
    w("| group | incidents | caught [95% range] | first payment caught | rupees saved |\n|---|---|---|---|---|")
    for k, v in E["by_stealth"].items():
        w(f"| {k} | {v['incidents']} | {rng_s(v)} | {pct(v['first_payment_caught'])} | {pct(v['rupees_saved'])} |")
    w("\n## Where legitimate users get friction (test, full model)\n")
    w("| legit situation | payments | >= MEDIUM | >= HIGH |\n|---|---|---|---|")
    for k, (n, m, h) in sorted(E["fpr_by_tag"].items(), key=lambda kv: -kv[1][2]):
        w(f"| {k} | {n:,} | {pct(m)} | {pct(h)} |")
    w("\n## Stress tests (full model at HIGH)\n")
    w("| condition | incidents caught | rupees saved |\n|---|---|---|")
    for k, v in E["stress"].items():
        w(f"| {k} | {rng_s(v)} | {pct(v['rupees_saved'])} |")
    if E["loao"]:
        w("\n## Unseen scam type (trained WITHOUT that type, tested on it; HIGH)\n")
        w("| held-out type | incidents | caught | rupees saved | (seen-type result) |\n|---|---|---|---|---|")
        for t, v in E["loao"].items():
            s = E["by_type"][t]["high"]
            w(f"| {t} | {v['incidents']} | {pct(v['caught'])} | {pct(v['rupees_saved'])} | {pct(s['caught'])} / {pct(s['rupees_saved'])} |")
    w(f"\n## Shortcut check\n\nA model that sees only WHICH values are missing (not the values) reaches test ROC-AUC {E['missingness_auc']:.3f} "
      "for fraud vs legit. Attackers legitimately have some different missingness (e.g. an attacker's phone may not run the SDK), so it is not "
      "0.5, but a high value would mean the data leaks the label through missingness. An earlier simulator bug of this kind was caught by "
      "probing and fixed.")
    w("\n## Precision depends on how common fraud is (full model, HIGH)\n")
    w("| fraud share of payments | share of HIGH alerts that are real fraud |\n|---|---|")
    for pi, p in E["precision_at_prevalence"].items():
        w(f"| {pi:g} | {pct(p)} |")
    w(f"\nSimulated test prevalence: {E['sim_prevalence']:.3%}. Real UPI fraud is far rarer, so most HIGH alerts would land on genuine users unless the "
      "action is cheap. That is why the budget at HIGH is small and MEDIUM is only a prompt or warning.")
    w(f"\nModel scoring latency (three heads, one payment, model only): {E['latency_ms']:.1f} ms.\n")
    w("## Config search (validation PR-AUC per head)\n")
    for name, v in V.items():
        for h in HEADS:
            w(f"- {name}/{h}: " + "; ".join(f"{c['max_leaf_nodes']}leaves/{c['max_iter']}it/lr{c['learning_rate']}/mono={c['mono']} -> {ap:.3f}" for c, ap in v["search"][h]))
    REPORT_PATH.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--quick", action="store_true", help="skip leave-one-scam-type-out")
    a = ap.parse_args()
    run(a.users, a.seed, a.quick)
