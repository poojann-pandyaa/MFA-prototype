"""
Evidence report for the shipped payment-risk model: every case, every user segment, how it predicts, and how it
behaves when the world differs from the simulator's assumptions.

    cd backend && .venv/bin/python -m ato.evaluate --users 6000 --seed 7

Writes docs/evidence/results.json, docs/evidence/*.png and docs/ato-evidence.md. It only SCORES with the saved model
(models/ato/); it never retrains. Everything is on synthetic data (ato/simulate.py). The "assumption shift" section is
the honest answer to "does this hold in the real world?": it regenerates the world with different assumptions
(weaker fraud-risk data, other user populations, stealthier scammers, ...) and re-scores the SAME model with the SAME
thresholds, so you can see which assumptions the results depend on. It cannot replace real data.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import joblib
import matplotlib
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ato import train as T  # noqa: E402
from ato.features import DEVICE_FEATURES, FEATURES, INDEX, Event, UserState  # noqa: E402
from ato.simulate import ALL_TYPES, FAMILY, build_dataset, override  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "ato"
OUT = ROOT.parent / "docs" / "evidence"
REPORT = ROOT.parent / "docs" / "ato-evidence.md"
HEADS = T.HEADS
NAVY, TEAL, AMBER, RED, GREY = "#14325c", "#1b7f8c", "#b86b00", "#a32020", "#8a97a6"

GROUPS = {
    "amount vs usual": ["log_amount", "amt_z", "amt_over_max", "amt_over_p95", "amt_over_balance"],
    "payee basics": ["new_payee", "payee_age_days", "payee_fan_in_24h", "payee_lookalike", "payee_is_merchant", "payee_risky_category"],
    "payee fraud-risk data": ["payee_fri", "payee_complaints"],
    "recent activity": ["new_payees_24h", "new_payees_72h", "escalating_to_payee", "n_txn_1h", "n_txn_24h", "outflow_24h_over_balance", "log_secs_since_last"],
    "time of day": ["hour_sin", "hour_cos", "hour_prob", "night_share_user", "is_night"],
    "history and device identity": ["hist_n", "device_known", "device_age_days"],
    "binding / SIM / PIN reset": ["device_bound_hours", "old_device_active", "sim_changed_hours", "pin_reset_hours"],
    "PIN tries and IP distance": ["failed_pin_attempts", "ip_geo_km"],
    "payment type": ["channel_collect", "channel_qr", "requester_is_contact"],
    "phone telemetry": ["remote_access_active", "bio_similarity", "integrity_fail", "overlay_accessibility", "sideload_hours", "login_to_pay_s"],
    "call in progress": ["call_active", "call_minutes", "call_unknown_number"],
}
assert sorted(sum(GROUPS.values(), [])) == sorted(FEATURES), "every feature must be in exactly one group"


# ---- loading ---------------------------------------------------------------------------------------
def load_variants():
    card = json.loads((MODEL_DIR / "model_card.json").read_text())
    V = {}
    for n in ("full", "server_only"):
        j = joblib.load(MODEL_DIR / f"ato_{n}.joblib")
        V[n] = {"heads": {h: {"model": j["heads"][h]["model"], "idx": j["heads"][h]["idx"], "grid": card["variants"][n]["grids"][h]} for h in HEADS},
                "thr": card["variants"][n]["thresholds"], "router": j["router"], "features": j["features"]}
    neutral = np.array([np.nan if v is None else v for v in card["neutral"]])
    return card, V, neutral


def get_data(users, seed, cache=True, **ov):
    tag = hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()[:8]
    p = T.CACHE / f"ev3_{users}_{seed}_{tag}.npz"
    if cache and p.exists():
        return dict(np.load(p, allow_pickle=False))
    with override(**ov):
        d = build_dataset(users, seed)
    if cache:
        T.CACHE.mkdir(exist_ok=True)
        np.savez_compressed(p, **d)
    return d


def pct(x):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.1f}%"


# ---- scoring helpers -----------------------------------------------------------------------------------
def score_rows(V, X):
    U = T.surprises(V, X)
    return U, U.max(1)


def level_rates(mx, thr, mask):
    return {lvl: float((mx[mask] > thr[lvl]).mean()) if mask.sum() else float("nan") for lvl in ("MEDIUM", "HIGH", "CRITICAL")}


def seg_row(name, mask, d, mx, thr):
    legit = mask & (d["y"] == 0)
    r = level_rates(mx, thr, legit)
    inc = T.summ(T.incident_stats(mx, thr["HIGH"], d, mask))
    return {"segment": name, "legit_n": int(legit.sum()), "legit_medium": r["MEDIUM"], "legit_high": r["HIGH"],
            "fraud_incidents": inc["incidents"], "caught": inc["caught"] if inc["incidents"] else float("nan"),
            "caught_ci": inc["caught_ci"], "first_payment": inc["first_payment_caught"] if inc["incidents"] else float("nan"),
            "rupees_saved": inc["rupees_saved"] if inc["incidents"] else float("nan")}


def explain(V, neutral, x, head, k=3):
    b = V["heads"][head]
    idx = b["idx"]
    batch = np.tile(x.astype(np.float64), (len(idx) + 1, 1))
    for r, j in enumerate(idx, start=1):
        batch[r, j] = neutral[j]
    u = T.surprise(b["grid"], b["model"].predict_proba(batch[:, idx])[:, 1])
    drops = sorted(((FEATURES[j], float(u[0] - u[r])) for r, j in enumerate(idx, start=1)), key=lambda t: -t[1])
    return [(f, round(dv, 2)) for f, dv in drops[:k] if dv > 0.3]


# ---- analyses ------------------------------------------------------------------------------------------
def reweighted_saved(types, sim_share):
    """Rupees saved if the loss mix were the reported one: 77% investment-type (task_invest), 8% digital arrest, rest as simulated."""
    saved = {r["type"]: r["overall"]["rupees_saved"] for r in types}
    fixed = {"task_invest": 0.77, "digital_arrest": 0.08}
    others = {t: v for t, v in sim_share.items() if t not in fixed}
    tot = sum(others.values())
    weights = {**fixed, **{t: 0.15 * v / tot for t, v in others.items()}}
    return sum(weights[t] * saved[t] for t in weights if saved.get(t) == saved.get(t))


def analyse_main(card, V, neutral, d_all, log):
    day = d_all["day"]
    te = day >= 120
    d = T.sub(d_all, te)
    X = d["X"]
    R = {"n_test": int(te.sum()), "n_incidents": int(len(np.unique(d["incident"][d["y"] == 1])))}
    full, srv = V["full"], V["server_only"]
    thr = full["thr"]
    U, mx = score_rows(full, X)
    Us, mxs = score_rows(srv, X)
    rules = T.rules_baseline(X)
    yo = d_all["y_obs"]
    va = (day >= 90) & (day < 120)
    rules_thr = {lvl: float(np.quantile(T.rules_baseline(d_all["X"][va])[yo[va] == 0], 1 - b)) for lvl, b in T.BUDGETS.items()}
    legit, fraud = d["y"] == 0, d["y"] == 1

    # 1. reproduction of the training report
    ev = T.evaluate(mx, thr, d)
    rep = card["test"]["full"]
    R["reproduces_card"] = bool(abs(ev["inc_HIGH"]["caught"] - rep["inc_HIGH"]["caught"]) < 1e-9 and abs(ev["fpr_HIGH"] - rep["fpr_HIGH"]) < 1e-9)
    R["headline"] = {n: T.evaluate(m, t, d) for n, m, t in [("full", mx, thr), ("server_only", mxs, srv["thr"]), ("rules", rules, rules_thr)]}

    # 2. discrimination per head
    ht = T.head_true(d)
    R["discrimination"] = {}
    for i, h in enumerate(HEADS):
        sc = T.score(full["heads"][h]["model"], full["heads"][h]["idx"], X)
        R["discrimination"][h] = {"roc_auc": float(roc_auc_score(ht[h], sc)), "pr_auc": float(average_precision_score(ht[h], sc)), "positives": int(ht[h].sum())}
    R["discrimination"]["combined"] = {"roc_auc": float(roc_auc_score(fraud, mx)), "pr_auc": float(average_precision_score(fraud, mx)), "positives": int(fraud.sum())}
    R["score_quantiles"] = {k: [float(np.quantile(mx[m], q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)] for k, m in (("legit", legit), ("fraud", fraud))}

    # 3. operating curve: how much fraud is caught for each cost to genuine users
    rates = [1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1]
    R["curve"] = {}
    for name, s in (("full", mx), ("server_only", mxs), ("rules", rules)):
        rows = []
        for r in rates:
            t = float(np.quantile(s[legit], 1 - r))
            inc = T.summ(T.incident_stats(s, t, d, np.ones(len(s), bool)))
            rows.append({"legit_alert_rate": r, "payment_recall": float((s[fraud] > t).mean()), "incidents_caught": inc["caught"],
                         "first_payment": inc["first_payment_caught"], "rupees_saved": inc["rupees_saved"]})
        R["curve"][name] = rows

    # 4. what each severity level means: share of alerts that are fraud at the simulated and at a realistic base rate
    edges = [0, 2, thr["MEDIUM"], thr["HIGH"], thr["CRITICAL"], 99]
    names = ["no alert (surprise under 2)", "quiet (2 to MEDIUM)", "MEDIUM", "HIGH", "CRITICAL"]
    R["severity_bins"] = []
    nl, nf = legit.sum(), fraud.sum()
    for lo, hi, nm in zip(edges[:-1], edges[1:], names):
        mL, mF = legit & (mx > lo) & (mx <= hi), fraud & (mx > lo) & (mx <= hi)
        fpr_b, tpr_b = mL.sum() / nl, mF.sum() / nf
        prec = lambda pi: (pi * tpr_b / (pi * tpr_b + (1 - pi) * fpr_b)) if (tpr_b + fpr_b) > 0 else float("nan")  # noqa: E731
        R["severity_bins"].append({"level": nm, "legit_share": float(fpr_b), "fraud_share": float(tpr_b), "precision_sim": float(prec(float(fraud.mean()))),
                                   "precision_1e-4": float(prec(1e-4)), "precision_1e-5": float(prec(1e-5))})

    # 5. segments
    hist = X[:, INDEX["hist_n"]]
    q = lambda a: np.quantile(a[legit], [1 / 3, 2 / 3])  # noqa: E731
    r3, m3 = q(d["user_rate"]), q(d["user_median"])
    sdk_silent = np.isnan(X[:, INDEX["call_active"]])
    segs = {
        "history: under 5 payments (new user)": hist < 5, "history: 5 to 19": (hist >= 5) & (hist < 20),
        "history: 20 to 99": (hist >= 20) & (hist < 100), "history: 100 or more": hist >= 100,
        "activity: light users": d["user_rate"] <= r3[0], "activity: medium": (d["user_rate"] > r3[0]) & (d["user_rate"] <= r3[1]), "activity: heavy users": d["user_rate"] > r3[1],
        "spend: small payments": d["user_median"] <= m3[0], "spend: medium": (d["user_median"] > m3[0]) & (d["user_median"] <= m3[1]), "spend: large payments": d["user_median"] > m3[1],
        "pays mostly by day": d["user_kind"] == "day", "night owls": d["user_kind"] == "night", "early birds": d["user_kind"] == "early",
        "phone SDK reports": ~sdk_silent, "phone SDK silent": sdk_silent,
        "new user AND SDK silent": (hist < 5) & sdk_silent,
    }
    R["segments"] = [seg_row(n, m, d, mx, thr) for n, m in segs.items()]
    R["overall"] = seg_row("everyone", np.ones(len(mx), bool), d, mx, thr)
    R["segments_server_only"] = [seg_row(n, m, d, mxs, srv["thr"]) for n, m in segs.items() if "SDK" in n or "new user" in n]

    # 6. every scam type, by stealth and by how much history the victim had
    bands = {"loud": d["stealth"] < 0.25, "mid": (d["stealth"] >= 0.25) & (d["stealth"] < 0.5), "stealthy": d["stealth"] >= 0.5}
    R["types"] = []
    for t in ALL_TYPES:
        m = d["atype"] == t
        row = {"type": t, "family": FAMILY[t], "overall": T.summ(T.incident_stats(mx, thr["HIGH"], d, m)),
               "medium": T.summ(T.incident_stats(mx, thr["MEDIUM"], d, m)), "server_only": T.summ(T.incident_stats(mxs, srv["thr"]["HIGH"], d, m))}
        row["by_stealth"] = {b: T.summ(T.incident_stats(mx, thr["HIGH"], d, m & bm)) for b, bm in bands.items()}
        row["by_history"] = {"new victim (under 20 payments)": T.summ(T.incident_stats(mx, thr["HIGH"], d, m & (hist < 20))),
                             "established victim": T.summ(T.incident_stats(mx, thr["HIGH"], d, m & (hist >= 20)))}
        R["types"].append(row)

    # 7. every genuine situation
    R["legit_situations"] = []
    for tag in sorted(set(d["tag"]) - {"attack"}):
        m = d["tag"] == tag
        R["legit_situations"].append({"situation": tag, "n": int(m.sum()), "full": level_rates(mx, thr, m), "server_only": level_rates(mxs, srv["thr"], m),
                                      "rules": level_rates(rules, rules_thr, m)})

    # 8. what the model relies on: shuffle one group of inputs at a time and see how much each detector degrades
    rng = np.random.default_rng(0)
    pick = np.where(fraud | (rng.random(len(mx)) < 0.25))[0]
    Xs = X[pick]
    base = {h: average_precision_score(ht[h][pick], T.score(full["heads"][h]["model"], full["heads"][h]["idx"], Xs)) for h in HEADS}
    R["importance"] = {"base_pr_auc": base, "drop": {}}
    for g, cols in GROUPS.items():
        Xp = Xs.copy()
        perm = rng.permutation(len(Xs))
        ci = [INDEX[c] for c in cols]
        Xp[:, ci] = Xs[perm][:, ci]
        R["importance"]["drop"][g] = {h: float(base[h] - average_precision_score(ht[h][pick], T.score(full["heads"][h]["model"], full["heads"][h]["idx"], Xp))) for h in HEADS}

    # 9. worked examples
    R["examples"] = []
    first_idx = {}
    for inc in np.unique(d["incident"][fraud]):
        rows = np.where((d["incident"] == inc) & fraud)[0]
        first_idx.setdefault(d["atype"][rows[0]], rows[np.argmin(d["day"][rows])])
    wanted = [("routine genuine payment", np.where(d["tag"] == "routine")[0][5]), ("genuine phone change", np.where(d["tag"] == "phone_change")[0][3]),
              ("genuine new payee (a shop)", np.where(d["tag"] == "new_payee")[0][7]), ("genuine collect request from a non-contact", np.where(d["tag"] == "collect_from_noncontact")[0][2])]
    wanted += [(f"scam: {t}", first_idx[t]) for t in ("sim_swap", "remote_access", "digital_arrest", "collect_qr", "task_invest", "lookalike", "fake_seller", "romance") if t in first_idx]
    show = ["amt_over_p95", "new_payee", "payee_age_days", "payee_fan_in_24h", "payee_fri", "call_active", "call_minutes", "channel_collect", "device_known",
            "device_bound_hours", "sim_changed_hours", "bio_similarity", "new_payees_72h", "payee_lookalike"]
    route = T.route(full, X)
    for label, i in wanted:
        x = X[i]
        u = U[i]
        sev = next((lvl for lvl in T.LEVELS if mx[i] > thr[lvl]), "LOW")
        fam = HEADS[int(route[i])] if sev != "LOW" else "none"
        R["examples"].append({"case": label, "inputs": {f: (None if np.isnan(x[INDEX[f]]) else round(float(x[INDEX[f]]), 2)) for f in show},
                              "surprise": {h: round(float(u[k]), 2) for k, h in enumerate(HEADS)}, "severity": sev, "family": fam,
                              "reasons": explain(full, neutral, x, HEADS[int(route[i])]) if sev != "LOW" else []})

    # 10. speed
    from ato import model as M
    sc = M.AtoScorer()
    st = UserState()
    for k in range(40):
        st.update(1.79e9 - (40 - k) * 40000, f"reg{k % 6}@ybl", 300.0 + k, "d1")
    ev_ = Event(ts=1.79e9, payee="new@ybl", amount=9000, device_id="d1", balance=20000, payee_age_days=3, payee_fan_in_24h=9, payee_fri=1, payee_complaints=0,
                call_active=True, call_minutes=40, call_unknown_number=True, remote_access_active=False, bio_similarity=0.8, failed_pin_attempts=0,
                ip_geo_km=3, integrity_fail=False, overlay_accessibility=False, login_to_pay_s=25, requester_is_contact=None)
    for att in (True, False):
        t0 = time.perf_counter()
        for _ in range(100):
            sc.score(ev_, st, att)
        R.setdefault("latency_ms", {})["full" if att else "server_only"] = (time.perf_counter() - t0) / 100 * 1000
    R["model_facts"] = {
        "trees_per_head": {h: int(full["heads"][h]["model"].n_iter_) for h in HEADS}, "features": len(FEATURES), "device_features": len(DEVICE_FEATURES),
        "training_rows": int(((day < 90)).sum()), "validation_rows": int(va.sum()), "test_rows": int(te.sum()),
        "files_kb": {p.name: round(p.stat().st_size / 1024) for p in MODEL_DIR.glob("*")}, "thresholds": thr, "budgets": T.BUDGETS}

    # 11. data in the simulated world versus public figures
    allf = d_all["y"] == 1
    amt = d_all["amount"]
    tot = float(amt[allf].sum())
    R["reconcile"] = {
        "sim_prevalence": float(d_all["y"].mean()), "rupee_share_by_type": {t: float(amt[allf & (d_all["atype"] == t)].sum() / tot) for t in ALL_TYPES},
        "rupee_share_by_family": {f: float(amt[allf & (d_all["family"] == f)].sum() / tot) for f in ("takeover", "coached", "payee")},
        "incident_share_by_family": {f: float(len(np.unique(d_all["incident"][allf & (d_all["family"] == f)])) / len(np.unique(d_all["incident"][allf]))) for f in ("takeover", "coached", "payee")},
        "unreported_share": float(1 - d_all["y_obs"][allf].sum() / allf.sum())}
    R["_arrays"] = {"mx": mx, "mxs": mxs, "rules": rules, "legit": legit, "fraud": fraud, "d": d, "thr": thr, "srv_thr": srv["thr"], "rules_thr": rules_thr}
    log(f"main analysis done: reproduces_card={R['reproduces_card']}")
    return R


def analyse_shifts(V, users, log):
    """Score the SAME model (same thresholds) on worlds generated with different assumptions."""
    scen = [
        ("same assumptions, fresh sample", {}),
        ("fraud-risk data covers half as many scam payees", {"fri_cover": 0.5}),
        ("fraud-risk data almost useless", {"fri_cover": 0.1}),
        ("half of scam payees are rented, aged accounts", {"mule_aged_boost": 0.4}),
        ("nearly all scam payees are rented, aged accounts", {"mule_aged_boost": 1.0}),
        ("aged scam payees AND fraud-risk data almost useless", {"mule_aged_boost": 1.0, "fri_cover": 0.1}),
        ("takeover attackers nearly always run the SDK", {"attacker_sdk_p": 0.95}),
        ("takeover attackers nearly never run the SDK", {"attacker_sdk_p": 0.1}),
        ("genuine users take calls while paying 4x more", {"legit_call_rate": 0.16}),
        ("phone SDK silent on 40% of genuine payments", {"sdk_ok_p": 0.6}),
        ("heavy phone churn (45% change phone)", {"phone_change_rate": 0.45}),
        ("night-heavy population (50% night owls)", {"night_owl_share": 0.5}),
        ("light users (0.3 payments/day)", {"rate_median": 0.3}),
        ("heavy users (4 payments/day)", {"rate_median": 4.0}),
        ("5x bigger payments", {"amount_scale": 5.0}),
        ("5x smaller payments", {"amount_scale": 0.2}),
        ("2x more new payees in genuine life", {"new_payee_scale": 2.0}),
        ("half as many new payees", {"new_payee_scale": 0.5}),
        ("scammers 0.3 stealthier", {"stealth_shift": 0.3}),
        ("coached-scam heavy mix", {"mix": {"digital_arrest": .2, "fake_care": .2, "collect_qr": .2, "task_invest": .2, "romance": .2}}),
        ("takeover heavy mix", {"mix": {"sim_swap": .25, "apk_binding": .25, "remote_access": .25, "physical_theft": .25}}),
        ("40% of victims never report (noisy labels)", {"unreported": 0.4}),
    ]
    full = V["full"]
    out = []
    for i, (name, ov) in enumerate(scen):
        d = get_data(users, 1000 + i, cache=False, **ov)
        X = d["X"]
        _, mx = score_rows(full, X)
        r = T.evaluate(mx, full["thr"], d)
        fam = {f: T.summ(T.incident_stats(mx, full["thr"]["HIGH"], d, d["family"] == f)) for f in ("takeover", "coached", "payee")}
        out.append({"scenario": name, "legit_medium": r["fpr_MEDIUM"], "legit_high": r["fpr_HIGH"], "legit_critical": r["fpr_CRITICAL"],
                    "caught": r["inc_HIGH"]["caught"], "first_payment": r["inc_HIGH"]["first_payment_caught"], "rupees_saved": r["inc_HIGH"]["rupees_saved"],
                    "incidents": r["inc_HIGH"]["incidents"], "family_caught": {f: v["caught"] for f, v in fam.items()}})
        log(f"  shift {i + 1}/{len(scen)}: {name}: legit>=HIGH {100 * r['fpr_HIGH']:.2f}%, caught {100 * r['inc_HIGH']['caught']:.0f}%")
    return out


# ---- figures ---------------------------------------------------------------------------------------------
def figures(R, S):
    OUT.mkdir(parents=True, exist_ok=True)
    A = R["_arrays"]
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for name, col, lab in (("full", TEAL, "Full model"), ("server_only", NAVY, "Server-only model"), ("rules", GREY, "Hand-written rules")):
        c = R["curve"][name]
        ax.plot([r["legit_alert_rate"] * 100 for r in c], [r["incidents_caught"] * 100 for r in c], "o-", color=col, label=lab)
    for v in (2, 0.3, 0.05):
        ax.axvline(v, color=AMBER, lw=0.7, ls=":")
    ax.set_xscale("log"); ax.set_xlabel("genuine payments that get an alert (%, log scale)"); ax.set_ylabel("scam incidents caught (%)")
    ax.set_title("Scams caught for each cost to genuine users (test month)", loc="left", fontsize=10, color=NAVY)
    ax.legend(frameon=False); ax.set_ylim(0, 102); fig.tight_layout(); fig.savefig(OUT / "operating_curve.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    mx, legit, fraud = A["mx"], A["legit"], A["fraud"]
    bins = np.linspace(0, 11.6, 40)
    ax.hist(np.clip(mx[legit], 0, 11.5), bins=bins, color=TEAL, alpha=0.8, label="genuine payments", density=True)
    ax.hist(np.clip(mx[fraud], 0, 11.5), bins=bins, color=RED, alpha=0.7, label="scam payments", density=True)
    for lvl, col in (("MEDIUM", AMBER), ("HIGH", "#7a3d00"), ("CRITICAL", "#000000")):
        ax.axvline(A["thr"][lvl], color=col, ls="--", lw=1); ax.text(A["thr"][lvl], ax.get_ylim()[1] * 0.92, lvl, rotation=90, va="top", ha="right", fontsize=7)
    ax.set_yscale("log"); ax.set_xlabel("risk score (surprise: how unusual versus genuine payments)"); ax.set_ylabel("share of payments (log)")
    ax.set_title("Genuine and scam payments separate on the risk score", loc="left", fontsize=10, color=NAVY, pad=18)
    ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "score_distribution.png", dpi=160); plt.close(fig)

    rows = R["types"]
    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    y = np.arange(len(rows))
    vals = [r["overall"]["caught"] * 100 for r in rows]
    err = [[(r["overall"]["caught"] - r["overall"]["caught_ci"][0]) * 100 for r in rows], [(r["overall"]["caught_ci"][1] - r["overall"]["caught"]) * 100 for r in rows]]
    ax.barh(y + 0.18, vals, 0.36, xerr=err, color=TEAL, label="caught (95% range)", ecolor=GREY, capsize=2)
    ax.barh(y - 0.18, [r["overall"]["first_payment_caught"] * 100 for r in rows], 0.36, color=NAVY, label="first payment caught")
    ax.set_yticks(y); ax.set_yticklabels([f"{r['type']} ({r['overall']['incidents']})" for r in rows]); ax.invert_yaxis(); ax.set_xlim(0, 105)
    ax.set_xlabel("%"); ax.set_title("Every scam type at the strong-action level (incidents in brackets)", loc="left", fontsize=10, color=NAVY, pad=18)
    ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "by_scam_type.png", dpi=160); plt.close(fig)

    sit = sorted(R["legit_situations"], key=lambda r: r["full"]["HIGH"])
    fig, ax = plt.subplots(figsize=(6.4, 3.9))
    y = np.arange(len(sit))
    ax.barh(y + 0.18, [r["full"]["MEDIUM"] * 100 for r in sit], 0.36, color=AMBER, label="prompt or stronger")
    ax.barh(y - 0.18, [r["full"]["HIGH"] * 100 for r in sit], 0.36, color=RED, label="strong action")
    ax.set_yticks(y); ax.set_yticklabels([f"{r['situation']} ({r['n']:,})" for r in sit]); ax.set_xlabel("% of these genuine payments")
    ax.set_title("Where genuine users get friction", loc="left", fontsize=10, color=NAVY); ax.legend(frameon=False, loc="lower right")
    fig.tight_layout(); fig.savefig(OUT / "genuine_friction.png", dpi=160); plt.close(fig)

    seg = [R["overall"]] + R["segments"]
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    y = np.arange(len(seg))
    ax.barh(y, [r["legit_high"] * 100 for r in seg], color=[NAVY if i == 0 else TEAL for i in range(len(seg))])
    ax.axvline(R["overall"]["legit_high"] * 100 * 1.5, color=RED, ls="--", lw=1, label="1.5 x overall (gate)")
    ax.set_yticks(y); ax.set_yticklabels([f"{r['segment']} ({r['legit_n']:,})" for r in seg], fontsize=7); ax.invert_yaxis()
    ax.set_xlabel("genuine payments getting a strong action (%)"); ax.set_title("Genuine-user friction by user segment", loc="left", fontsize=10, color=NAVY); ax.legend(frameon=False)
    fig.tight_layout(); fig.savefig(OUT / "segments.png", dpi=160); plt.close(fig)

    imp = R["importance"]["drop"]
    groups = list(imp)
    M = np.array([[imp[g][h] for h in HEADS] for g in groups])
    order = np.argsort(-M.max(1))
    fig, ax = plt.subplots(figsize=(6.2, 3.9))
    im = ax.imshow(M[order], cmap="Blues", aspect="auto", vmin=0)
    ax.set_xticks(range(3)); ax.set_xticklabels(["takeover", "coached", "bad payee"]); ax.set_yticks(range(len(groups))); ax.set_yticklabels([groups[i] for i in order], fontsize=8)
    for i, gi in enumerate(order):
        for j in range(3):
            ax.text(j, i, f"{M[gi, j]:.2f}", ha="center", va="center", fontsize=7, color="white" if M[gi, j] > M.max() * 0.55 else "black")
    ax.set_title("What each detector relies on (drop in PR-AUC when shuffled)", loc="left", fontsize=10, color=NAVY)
    fig.tight_layout(); fig.savefig(OUT / "importance.png", dpi=160); plt.close(fig)

    if S:
        fig, axs = plt.subplots(1, 2, figsize=(7.6, 4.8), sharey=True)
        y = np.arange(len(S))
        axs[0].barh(y, [s["legit_high"] * 100 for s in S], color=TEAL); axs[0].axvline(0.3, color=RED, ls="--", lw=1); axs[0].set_xlabel("genuine payments with strong action, % (dashed = 0.3% budget)", fontsize=7)
        axs[1].barh(y, [s["caught"] * 100 for s in S], color=NAVY); axs[1].set_xlabel("scam incidents caught, %", fontsize=7); axs[1].set_xlim(0, 105)
        axs[0].set_yticks(y); axs[0].set_yticklabels([s["scenario"] for s in S], fontsize=7); axs[0].invert_yaxis()
        fig.suptitle("Same trained model, different worlds", x=0.01, ha="left", fontsize=10, color=NAVY)
        fig.tight_layout(); fig.savefig(OUT / "shifts.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    full = R["headline"]["full"]
    tpr, fpr = full["txn_recall_HIGH"], full["fpr_HIGH"]
    pis = np.logspace(-6, -1.5, 60)
    ax.plot(pis, [100 * tpr * p / (tpr * p + fpr * (1 - p)) for p in pis], color=TEAL)
    for x_, lab in ((R["reconcile"]["sim_prevalence"], "this simulation"), (1e-5, "about 1 in 100,000 (reported UPI fraud base rate)")):
        ax.axvline(x_, color=AMBER, ls=":"); ax.text(x_, 55, lab, rotation=90, fontsize=7, va="center", ha="right")
    ax.set_xscale("log"); ax.set_xlabel("share of all payments that are scams"); ax.set_ylabel("alerts that are real scams (%)")
    ax.set_title("Alert precision depends on how rare scams really are", loc="left", fontsize=10, color=NAVY)
    fig.tight_layout(); fig.savefig(OUT / "precision_vs_prevalence.png", dpi=160); plt.close(fig)


# ---- markdown --------------------------------------------------------------------------------------------
def write_md(R, S, card):
    L = []
    w = L.append
    h = R["headline"]
    w("# Evidence report for the payment-risk model (generated by `python -m ato.evaluate`)\n")
    w("Everything here is on **synthetic data**. This report scores the saved model; it does not retrain it. Section 5 explains what this evidence can and cannot say about the real world.\n")
    w("## 1. What the model is, in numbers\n")
    mf = R["model_facts"]
    w(f"- 3 detectors (takeover, coached, bad payee) + a router, on {mf['features']} inputs ({mf['device_features']} come from the phone's app, the rest from server facts). Trees per detector: {mf['trees_per_head']}.")
    w(f"- Trained on {mf['training_rows']:,} payments (days 0 to 90), tuned on {mf['validation_rows']:,} (days 90 to 120), tested on {mf['test_rows']:,} (days 120 to 150) with {R['n_incidents']} scam incidents.")
    w(f"- Strictness (friction budget): at most {100 * T.BUDGETS['MEDIUM']:g}% / {100 * T.BUDGETS['HIGH']:g}% / {100 * T.BUDGETS['CRITICAL']:g}% of genuine payments reach prompt / strong action / block. Saved model size: {sum(mf['files_kb'].values())} KB.")
    w(f"- Speed per payment (3 detectors, router, explanation): {R['latency_ms']['full']:.0f} ms full, {R['latency_ms']['server_only']:.0f} ms server-only.")
    w(f"- This report reproduces the training report's headline exactly: **{R['reproduces_card']}**.\n")
    w("## 2. How it predicts\n")
    w("Each payment becomes numbers (amount versus this user's norm, payee age, call in progress, SIM change, and so on). Three detectors each score the payment. Each score is converted to a **surprise**: how rare a score that high is among genuine payments. The biggest surprise sets the severity; the router names the family so the action fits.\n")
    w("![score distribution](evidence/score_distribution.png)\n")
    w("| detector | ROC-AUC | PR-AUC | positives in test |\n|---|---|---|---|")
    for k, v in R["discrimination"].items():
        w(f"| {k} | {v['roc_auc']:.3f} | {v['pr_auc']:.3f} | {v['positives']} |")
    q = R["score_quantiles"]
    w(f"\nRisk-score quantiles (5/25/50/75/95%): genuine {[round(x, 2) for x in q['legit']]}; scam {[round(x, 2) for x in q['fraud']]}.\n")
    w("![operating curve](evidence/operating_curve.png)\n")
    w("| genuine payments alerted | incidents caught | first payment caught | rupees saved |\n|---|---|---|---|")
    for r in R["curve"]["full"]:
        w(f"| {100 * r['legit_alert_rate']:.2f}% | {pct(r['incidents_caught'])} | {pct(r['first_payment'])} | {pct(r['rupees_saved'])} |")
    w("\n### What each severity level means\n")
    w("| level | genuine payments here | scam payments here | alerts that are scams (this simulation) | at 1 in 10,000 | at 1 in 100,000 |\n|---|---|---|---|---|---|")
    for b in R["severity_bins"]:
        w(f"| {b['level']} | {pct(b['legit_share'])} | {pct(b['fraud_share'])} | {pct(b['precision_sim'])} | {pct(b['precision_1e-4'])} | {pct(b['precision_1e-5'])} |")
    w("\n### What it relies on\n")
    w("![importance](evidence/importance.png)\n")
    w("## 3. Headline (test month)\n")
    w("| model | genuine prompted+ | genuine strong action | incidents caught (95% range) | first payment caught | rupees saved |\n|---|---|---|---|---|---|")
    for n, lab in (("full", "Full"), ("server_only", "Server-only"), ("rules", "Hand-written rules")):
        r = h[n]; i = r["inc_HIGH"]
        w(f"| {lab} | {pct(r['fpr_MEDIUM'])} | {pct(r['fpr_HIGH'])} | {pct(i['caught'])} ({pct(i['caught_ci'][0])} to {pct(i['caught_ci'][1])}) | {pct(i['first_payment_caught'])} | {pct(i['rupees_saved'])} |")
    w("\n## 4. Every case\n")
    w("### 4a. Every scam type, by stealth and by how much history the victim had (strong action; first-payment catch in brackets)\n")
    w("![by scam type](evidence/by_scam_type.png)\n")
    w("| scam | family | incidents | caught | first payment | prompt or stronger | server-only | loud | mid | stealthy | new victim | established victim |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in R["types"]:
        bs, bh = r["by_stealth"], r["by_history"]
        f = lambda s: f"{pct(s['caught'])} ({pct(s['first_payment_caught'])}) n={s['incidents']}" if s["incidents"] else "n/a"  # noqa: E731
        w(f"| {r['type']} | {r['family']} | {r['overall']['incidents']} | {pct(r['overall']['caught'])} | {pct(r['overall']['first_payment_caught'])} | {pct(r['medium']['caught'])} | {pct(r['server_only']['caught'])} | "
          f"{f(bs['loud'])} | {f(bs['mid'])} | {f(bs['stealthy'])} | {f(bh['new victim (under 20 payments)'])} | {f(bh['established victim'])} |")
    w("\n### 4b. Every genuine situation\n")
    w("![genuine friction](evidence/genuine_friction.png)\n")
    w("| situation | payments | prompt+ (full) | strong (full) | strong (server-only) | strong (rules) |\n|---|---|---|---|---|---|")
    for r in sorted(R["legit_situations"], key=lambda r: -r["full"]["HIGH"]):
        w(f"| {r['situation']} | {r['n']:,} | {pct(r['full']['MEDIUM'])} | {pct(r['full']['HIGH'])} | {pct(r['server_only']['HIGH'])} | {pct(r['rules']['HIGH'])} |")
    w("\n### 4c. Every user segment\n")
    w("![segments](evidence/segments.png)\n")
    ov = R["overall"]
    w(f"Overall genuine payments with a strong action: {pct(ov['legit_high'])}. Gate: no segment above 1.5x that ({pct(ov['legit_high'] * 1.5)}).\n")
    w("| segment | genuine payments | prompt+ | strong action | vs overall | scam incidents | caught | first payment | rupees saved |\n|---|---|---|---|---|---|---|---|---|")
    for r in R["segments"]:
        ratio = r["legit_high"] / ov["legit_high"] if ov["legit_high"] else float("nan")
        flag = " **above gate**" if ratio > 1.5 else ""
        w(f"| {r['segment']} | {r['legit_n']:,} | {pct(r['legit_medium'])} | {pct(r['legit_high'])} | {ratio:.1f}x{flag} | {r['fraud_incidents']} | {pct(r['caught'])} | {pct(r['first_payment'])} | {pct(r['rupees_saved'])} |")
    w("\nServer-only model on the same segments:\n")
    w("| segment | genuine strong action | scam incidents | caught |\n|---|---|---|---|")
    for r in R["segments_server_only"]:
        w(f"| {r['segment']} | {pct(r['legit_high'])} | {r['fraud_incidents']} | {pct(r['caught'])} |")
    w("\n### 4d. Worked examples (real rows from the test month)\n")
    w("| case | key inputs | surprise (takeover / coached / payee) | result | main reasons |\n|---|---|---|---|---|")
    for e in R["examples"]:
        inp = ", ".join(f"{k}={v}" for k, v in e["inputs"].items() if v not in (None, 0.0))
        s = e["surprise"]
        w(f"| {e['case']} | {inp} | {s['takeover']} / {s['coached']} / {s['payee']} | {e['severity']} ({e['family']}) | {', '.join(f'{f} (+{v})' for f, v in e['reasons']) or '-'} |")
    w("\n## 5. Is this true for the real world?\n")
    w("**Honest answer: nothing here proves real-world accuracy, because no real labelled UPI fraud data exists to test on.** What this evidence does show is (a) the method works end to end, (b) which assumptions the results depend on, and (c) how much the model breaks when those assumptions are wrong.\n")
    rc = R["reconcile"]
    w("### 5a. Where the simulated world differs from reported real-world figures\n")
    w("| quantity | simulated world | reported real world | comment |\n|---|---|---|---|")
    w(f"| scams as share of all payments | {100 * rc['sim_prevalence']:.2f}% | about 0.001% (13.42 lakh UPI fraud incidents in FY24 [reported] against roughly 131 billion payments [my recollection of NPCI data, check]) | simulation is about {rc['sim_prevalence'] / 1e-5:,.0f}x too rich in scams. Friction rates are unaffected; **alert precision is not** (see 5c) |")
    w(f"| coached-scam share of incidents / rupees | {pct(rc['incident_share_by_family']['coached'])} / {pct(rc['rupee_share_by_family']['coached'])} | investment-type fraud about 77% and digital arrest about 8% of 2025 reported losses [news reports of portal data] | same direction (coached dominates); exact split not calibrated |")
    inv = rc['rupee_share_by_type']
    w(f"| investment/task + romance share of scam rupees | {pct(inv['task_invest'] + inv['romance'])} | about 77% (investment-type) | the simulation under-represents long-running investment losses |")
    w(f"| digital arrest share of scam rupees | {pct(inv['digital_arrest'])} | about 8% | the simulation over-weights digital arrest by about {inv['digital_arrest'] / 0.08:.1f}x |")
    w(f"| scam incidents never reported | {pct(rc['unreported_share'])} | over half of victims did not report [LocalCircles survey] | the real label noise is likely much worse than simulated; see shift tests |")
    rw = reweighted_saved(R["types"], rc["rupee_share_by_type"])
    w(f"\nBecause the simulated loss mix is far from the reported one, the 'rupees saved' headline is not representative. **Reweighted to the reported mix** "
      f"(investment-type 77% of rupees = task_invest, digital arrest 8%, the remaining 15% split across the other types in their simulated proportions), "
      f"the model would have saved about **{pct(rw)}** of rupees on this simulated month. This is illustrative: it assumes our per-type catch rates hold in the real world.")
    w("\n### 5b. Assumption register: what the results lean on\n")
    w("| assumption | value used | source | how much it matters (see section 6) |\n|---|---|---|---|")
    for a in [("Government fraud-risk data flags scam payees", "75% of new mule accounts, 40% of aged ones", "no public coverage figure", "large for coached scams"),
              ("Scam payees are young accounts with many senders", "80% minus stealth", "reported mule behaviour, not measured", "large (see the aged-account worlds)"),
              ("Takeover attacker's phone runs the SDK", "60%", "none", "small"),
              ("Genuine users on calls while paying", "4%", "none", "medium"),
              ("Genuine phone change", "15% of users in 5 months", "none", "medium (largest friction source)"),
              ("Scam mix", "28% takeover, 63% coached, 8% payee", "loosely from reported loss mix", "medium"),
              ("Scammers adapt late in the year", "+0.15 stealth after day 110", "reasoned", "small"),
              ("Unreported incidents", "15%", "reported: over 51% in a survey", "medium")]:
        w("| " + " | ".join(a) + " |")
    w("\n### 5c. What real rarity does to alerts\n")
    w("![precision](evidence/precision_vs_prevalence.png)\n")
    full = h["full"]
    w(f"At about 1 in 100,000 payments being a scam, a strong action would hit about {100 * full['fpr_HIGH']:.2f}% of genuine payments, so roughly "
      f"{full['fpr_HIGH'] * 1e5 / (full['txn_recall_HIGH']):,.0f} genuine payments get a strong action for every scam payment stopped. "
      "That is why the strong action has to be cheap and kind (a face check or a wait), and why MEDIUM is only a prompt.\n")
    if S:
        w("## 6. Assumption-shift tests (same model, same thresholds, different worlds)\n")
        w("Each row regenerates the world with one assumption changed and scores the **unchanged** model. Watch two things: whether genuine-user friction stays near the 0.3% budget, and whether scam catch holds.\n")
        w("![shifts](evidence/shifts.png)\n")
        w("| world | genuine prompt+ | genuine strong | critical | incidents | caught | first payment | rupees saved | takeover / coached / payee caught |\n|---|---|---|---|---|---|---|---|---|")
        for s in S:
            fc = s["family_caught"]
            w(f"| {s['scenario']} | {pct(s['legit_medium'])} | {pct(s['legit_high'])} | {pct(s['legit_critical'])} | {s['incidents']} | {pct(s['caught'])} | {pct(s['first_payment'])} | {pct(s['rupees_saved'])} | {pct(fc['takeover'])} / {pct(fc['coached'])} / {pct(fc['payee'])} |")
    w("\n## 7. What it would take to make this real\n")
    for t in ["Run in shadow mode (`MFA_ATO_SHADOW=1`) at a partner bank or UPI app: score every payment, add no friction, log outcomes.",
              "Join the scores to confirmed fraud (complaints, bank disputes) after 30 to 60 days; measure catch and false-alarm rates per segment on real labels.",
              "Re-derive the three thresholds from real genuine traffic so the friction budget holds (the budget is a property of genuine users, not of fraud).",
              "Retrain on the real labels with `ato.train` (only `build_dataset` changes). Compare against this report's synthetic numbers; large gaps show which assumptions were wrong.",
              "Check every segment in section 4c on real users, including age bands if the partner can supply them under consent."]:
        w(f"1. {t}")
    REPORT.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--shift-users", type=int, default=1200)
    ap.add_argument("--skip-shifts", action="store_true")
    ap.add_argument("--reuse-shifts", action="store_true", help="reuse the shift results already in docs/evidence/results.json")
    a = ap.parse_args()
    t0 = time.time()
    card, V, neutral = load_variants()
    d_all = get_data(a.users, a.seed)
    old = T.CACHE / f"ds2_{a.users}_{a.seed}.npz"
    if old.exists():  # determinism check: regenerated data equals what the model was trained on
        c = np.load(old, allow_pickle=False)
        same = bool(np.array_equal(np.nan_to_num(c["X"]), np.nan_to_num(d_all["X"])) and np.array_equal(c["y"], d_all["y"]))
        print(f"regenerated data identical to the training data: {same}")
    R = analyse_main(card, V, neutral, d_all, print)
    if a.reuse_shifts and (OUT / "results.json").exists():
        S = json.loads((OUT / "results.json").read_text())["shifts"]
    else:
        S = [] if a.skip_shifts else analyse_shifts(V, a.shift_users, print)
    figures(R, S)
    write_md(R, S, card)
    arrays = R.pop("_arrays")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"main": R, "shifts": S}, indent=1, default=float))
    print(f"done in {time.time() - t0:.0f}s -> {REPORT}")


if __name__ == "__main__":
    main()
