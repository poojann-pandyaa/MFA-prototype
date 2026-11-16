"""Five different synthetic banks, each tested end to end. The model was trained on one population (seed 7); these are others.

    AMFA_ENV=development python scripts/evaluate_datasets.py [customers per bank] [--only=1,3] [--md=docs/dataset-evaluation.md]

For every bank (a different mix of customers, amounts, signals and attacks, built by changing the simulator's own knobs, never the model):
  1. THE MODEL AND THE DECISION PIPELINE on all its payments: fraud caught, how often genuine customers are stepped up, fairness by age and
     segment, the weakest attack types. The pipeline is the real one (the model, then the small-payment and routine reliefs, then the habit).
  2. A NEW CUSTOMER'S FIRST WEEKS: every simulated customer replayed from sign-up: how many payments until the readiness test passes, false
     alarms before and after, holds on genuine payments while learning, and the early-payment gain from the sign-up answers.
  3. PAYEE REPUTATION: a made-up population of payees (shops, utilities, friends, brand-new popular merchants, mules, reported scammers, slow mules)
     scaled to the bank, scored by the real code against a throwaway database.
  4. LIVE-PATH PARITY: some customers' payments written into a database as transactions and read back by the real history reader must give the
     model exactly the inputs the simulator gave it.
Expectations are written down before the run (EXPECT below), set from the baseline with a margin. A miss is reported, not tuned away.
"""
import contextlib
import math
import os
import random
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("AMFA_DATA_DIR", tempfile.mkdtemp(prefix="amfa-eval-"))     # a throwaway database for parts 3 and 4
os.environ.setdefault("AMFA_ENV", "development")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from amfa.engine.risk import features as F
from amfa.engine.risk import scenarios as S
from amfa.engine.risk import scorer, train
from amfa.engine.risk.readiness import Tracker
from amfa.engine.risk.threat import LEVELS, habit_level, hard_signals, level, raise_for_habit, routine_relief, small_payment_cap

DAY = 86400.0
LOW_LINE = train.LOW_LINE_RUPEES

# ---- what each bank should achieve (written before the run) -------------------------------------------------------------
EXPECT = {
    "pr_auc_min": 0.80,
    "fraud_T3_min": 0.75, "fraud_T3_min_attacked": 0.70, "fraud_rupees_T3_min": 0.90,
    "genuine_T2_max": 0.10, "genuine_T2_max_sparse_signals": 0.14,
    "scenario_floor": 0.30,                # no attack type with 40+ cases may fall below this share caught at T3+
    "senior_ratio_max": 3.3,               # known weak spot (the release gate's 1.25 fails); this only catches it getting worse
    "ready_median_payments_max": 120,
    "learning_false_hold_max": 0.02,       # genuine payments a learning customer would have held (calibrated risk 0.5 or more)
    "rep_recall_min": 0.90, "rep_reported_recall_min": 1.0, "rep_false_positive_max": 0.0, "rep_new_merchant_fpr_max": 0.35,
    "parity_max_mismatches": 0,
}


# ---- the five banks ---------------------------------------------------------------------------------------------------
@contextlib.contextmanager
def tweaked(**changes):
    """Change the simulator's own constants for one bank, and put them back."""
    saved = {k: (dict(getattr(S, k)) if isinstance(getattr(S, k), dict) else getattr(S, k)) for k in changes}
    try:
        for k, v in changes.items():
            setattr(S, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(S, k, v)


def archetypes(**weights):
    return {k: replace(a, weight=weights.get(k, a.weight)) for k, a in S.ARCHETYPES.items()}


def scaled(d, f):
    return {k: v * f for k, v in d.items()}


BANKS = [
    dict(id=1, name="Baseline, a new population", seed=101, attack=.15, scale=1.0,
         about="The training generator with a different seed: unseen customers, same kind of bank.", changes={}),
    dict(id=2, name="High-value bank", seed=202, attack=.15, scale=1.0,
         about="Premium and corporate customers; every typical payment six times larger. Tests that amounts mean nothing in themselves.",
         changes=dict(SEGMENTS={"STANDARD": .35, "PREMIUM": .30, "SENIOR": .08, "NEW_TO_BANK": .07, "CORPORATE": .20},
                      MEDIAN_AMOUNT=scaled(S.MEDIAN_AMOUNT, 6.0))),
    dict(id=3, name="Everyday micro-payments bank", seed=303, attack=.15, scale=1.0,
         about="Students and gig workers; payments a quarter the size and twice as often.",
         changes=dict(MEDIAN_AMOUNT=scaled(S.MEDIAN_AMOUNT, .25), TXNS_PER_DAY=scaled(S.TXNS_PER_DAY, 2.0),
                      ARCHETYPES=archetypes(gig_worker=.20, student=.15, salaried=.20))),
    dict(id=4, name="Senior-heavy bank", seed=404, attack=.15, scale=1.0,
         about="Nearly half the customers are seniors, many low-digital and infrequent. Stresses fairness.",
         changes=dict(SEGMENTS={"STANDARD": .35, "PREMIUM": .06, "SENIOR": .45, "NEW_TO_BANK": .08, "CORPORATE": .06},
                      ARCHETYPES=archetypes(senior_low_digital=.20, infrequent=.15, salaried=.20))),
    dict(id=5, name="Under attack, mostly new, few signals", seed=505, attack=.40, scale=1.0,
         about="Forty percent of customers are attacked, half are new to the bank, 60% skip the sign-up questions, and the bank sends few signals.",
         sparse=True, changes=dict(SEGMENTS={"STANDARD": .30, "PREMIUM": .06, "SENIOR": .10, "NEW_TO_BANK": .50, "CORPORATE": .04}, DECLARE_SKIP=.6,
                                   FEEDS={"full": .15, "no_geo": .20, "no_events": .30, "no_ueba": .15, "minimal": .20})),
]


# ---- 1. the model and the decision pipeline -----------------------------------------------------------------------------
def pipeline(rows, m):
    X = np.array([F.vector(r.features) for r in rows])
    y = np.array([r.label for r in rows])
    w = np.array([r.weight for r in rows])
    raw = m.raw(X)
    prob = m.calibrate(raw)
    mrank = np.array([LEVELS.index(level(v, m.cutoffs)) for v in raw])
    rank = []
    for k, r in zip(mrank, rows):
        lvl = LEVELS[k]
        lvl = small_payment_cap(lvl, r.features)[0] if r.exposure < LOW_LINE else routine_relief(lvl, r.features)[0]
        lvl = raise_for_habit(lvl, habit_level(r.features.get("habit_surprise"), m.habit_cutoffs))[0]
        rank.append(LEVELS.index(lvl))
    rank = np.array(rank)
    groups = {"amount": np.array([r.amount for r in rows]), "age": np.array([r.age_group for r in rows]),
              "archetype": np.array([r.archetype for r in rows]),
              "band": np.array([S.amount_band(r.amount) if r.amount > 0 else "none" for r in rows]), "rank_uncapped": mrank}
    metrics = train.evaluate(raw, prob, y, w, np.array([r.scenario for r in rows]), np.array([r.segment for r in rows]),
                             np.array([r.feed for r in rows]), rank, groups)
    metrics["n_rows"], metrics["n_fraud_rows"] = len(rows), int(y.sum())
    return metrics


# ---- 2. a new customer's first weeks ------------------------------------------------------------------------------------
def fresh_rows(n, bank, hide=False):
    out = []
    for c, sims in S.simulate(n, 150, bank["attack"], bank["seed"] + 1):
        sims = [s for s in sims if s.event.at >= S.START]
        lo = 0
        for i, s in enumerate(sims):
            e = s.event
            while sims[lo].event.at < e.at - F.HISTORY_DAYS * DAY:
                lo += 1
            if e.type not in F.MONETARY_TYPES:
                continue
            hist = [x.event for x in sims[lo:i]]
            prof = replace(s.profile, account_created_at=S.START)
            if hide:
                prof = replace(prof, declared_typical=None, declared_month=None, peer=None)
            out.append(S.Row(F.build(e, hist, prof), s.label, s.scenario, c.segment, e.at, c.cid, c.arch, c.feed, c.weight, e.amount, c.age,
                             c.age_group, F.exposure_24h(e, hist)))
    return out


def cold_start(rows, m):
    X = np.array([F.vector(r.features) for r in rows])
    raw = m.raw(X)
    score = m.calibrate(raw)
    by = defaultdict(list)
    for r, a, s in zip(rows, raw, score):
        by[r.customer].append((r, a, s))
    ready_at, days_to, first5 = [], [], [0, 0, 0, 0]
    learn_alarm, learn_n, ready_alarm, ready_n = 0, 0, 0, 0
    false_hold, genuine_learning, fraud_hold, fraud_learning = 0, 0, 0, 0
    split = {k: [0, 0.0] for k in ('held', 'code', 'fingerprint')}      # fraud while the customer is still being learned: how it was met [count, rupees]
    for cust, seq in by.items():
        seq.sort(key=lambda t: t[0].at)
        answered = seq[0][0].features.get("amount_vs_declared") is not None
        tr, got = Tracker(), None
        for i, (r, a, sc) in enumerate(seq):
            lvl = level(a, m.cutoffs)
            lvl = small_payment_cap(lvl, r.features)[0] if r.exposure < LOW_LINE else routine_relief(lvl, r.features)[0]
            lvl = raise_for_habit(lvl, habit_level(r.features.get("habit_surprise"), m.habit_cutoffs))[0]
            alarm = LEVELS.index(lvl) >= 2
            learning = not tr.ready()
            if r.label == 0:
                if learning:
                    learn_alarm += alarm
                    learn_n += 1
                    genuine_learning += 1
                    false_hold += sc >= .5
                else:
                    ready_alarm += alarm
                    ready_n += 1
                if i < 5:
                    first5[2 * (not answered)] += alarm
                    first5[2 * (not answered) + 1] += 1
                tr.observe(r.amount, alarm, r.at)
                if got is None and tr.ready():
                    got = i + 1
                    days_to.append((r.at - seq[0][0].at) / DAY)
            elif learning:
                fraud_learning += 1
                fraud_hold += sc >= .5
                stepped = r.exposure > 25_000 or (r.features.get("payee_seen") == 0 and r.exposure > 10_000) or bool(hard_signals(r.features))
                k = "held" if sc >= .5 else "code" if stepped else "fingerprint"      # the learning plan's three outcomes (decide.learning_plan)
                split[k][0] += 1
                split[k][1] += r.amount
        if got:
            ready_at.append(got)
    return {"customers": len(by), "ready_share": len(ready_at) / max(len(by), 1),
            "ready_median_payments": float(np.median(ready_at)) if ready_at else None,
            "ready_p90_payments": float(np.percentile(ready_at, 90)) if ready_at else None,
            "ready_median_days": float(np.median(days_to)) if days_to else None,
            "alarm_learning": learn_alarm / max(learn_n, 1), "alarm_ready": ready_alarm / max(ready_n, 1),
            "false_hold_learning": false_hold / max(genuine_learning, 1), "fraud_hold_learning": fraud_hold / max(fraud_learning, 1),
            "first5": first5, "fraud_split": split}


# ---- 3. payee reputation -------------------------------------------------------------------------------------------------
def reputation(bank, rng):
    from amfa.core import context_builder, db, payee_reputation
    from amfa.engine.policy import TenantPolicy
    db.migrate()
    now = time.time()
    scale = {1: 1.0, 2: .6, 3: 2.0, 4: .5, 5: 1.0}[bank["id"]]            # a bank's size: how many customers pay one payee
    pol = TenantPolicy.model_validate({**TenantPolicy().model_dump(mode="json"), "payeeReputation": {"enabled": True}})
    import uuid
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="E", slug=f"e{uuid.uuid4().hex[:8]}", policy=pol.model_dump(mode="json"))
        s.add(t)
        s.flush()
        tid = t.id
        pool = []
        for _ in range(int(900 * scale)):
            c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=tid, external_ref=uuid.uuid4().hex[:12], created_at=now - rng.uniform(10, 900) * DAY)
            s.add(c)
            pool.append(c.id)
        s.flush()
        payees = []          # (ref, label_kind, registry_age_days or None)

        def add(kind, ref, n_senders, first_ago, spread_days, reports=0, registry=None):
            key = context_builder.payee_key(tid, ref)
            for cid in rng.sample(pool, min(n_senders, len(pool))):
                s.add(db.KnownPayee(customer_id=cid, payee_key=key, first_seen=now - max(first_ago - rng.uniform(0, spread_days), 0.1) * DAY, count=1))
            for _ in range(reports):
                rep, victim = rng.sample(pool, 2)
                x = f"txn_{uuid.uuid4().hex[:12]}"
                s.add(db.Transaction(id=x, tenant_id=tid, customer_id=rep, external_id=x, idempotency_key=x, request_hash="x", type="TRANSFER",
                                     amount_minor=100000, currency="INR", payee_ref=ref, channel="MOBILE", status="CONSUMED", created_at=now - 5 * DAY))
                s.flush()
                s.add(db.RiskLabel(tenant_id=tid, transaction_id=x, label="FRAUD", source="customer_report", reported_at=now - 2 * DAY))
            payees.append((ref, kind, registry))

        for i in range(int(60 * scale) + 5):
            add("shop", f"SHOP-{i}", rng.randint(20, 200), rng.uniform(150, 600), 140, registry=rng.uniform(200, 900))
        for i in range(10):
            add("utility", f"UTIL-{i}", rng.randint(150, 500), rng.uniform(300, 900), 250, registry=rng.uniform(400, 1500))
        for i in range(int(150 * scale) + 10):
            add("friend", f"FRIEND-{i}", rng.randint(1, 4), rng.uniform(5, 300), 5)
        for i in range(int(25 * scale) + 5):          # a legitimate new merchant whose opening week was busy: the hard false-positive case
            add("new_merchant", f"NEWSHOP-{i}", rng.randint(3, 22), rng.uniform(8, 40), 30)
        for i in range(int(25 * max(scale, 1))):
            add("mule", f"MULE-{i}", rng.randint(7, 40), rng.uniform(1, 9), 2.5)
        for i in range(int(15 * max(scale, 1))):
            add("reported", f"SCAM-{i}", rng.randint(2, 5), rng.uniform(5, 40), 3, reports=rng.randint(1, 3))
        for i in range(10):                            # paid by one victim every few days: under every threshold until someone reports it
            add("slow_mule", f"SLOW-{i}", rng.randint(2, 4), rng.uniform(10, 30), 20)
        s.flush()
        out = defaultdict(lambda: [0, 0])
        for ref, kind, registry in payees:
            r = payee_reputation.evaluate(s, tid, pol, "cu_new", ref, context_builder.payee_key(tid, ref), now,
                                          now - registry * DAY if registry else None)
            out[kind][0] += r["flag"]
            out[kind][1] += 1
        s.rollback()
    return dict(out)


# ---- 4. live-path parity -----------------------------------------------------------------------------------------------
def parity(bank, n_customers=40):
    from zoneinfo import ZoneInfo

    from amfa.core import context_builder, db
    from amfa.engine.policy import TenantPolicy
    import uuid
    db.migrate()
    mism, checked, examples = 0, 0, []
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="P", slug=f"q{uuid.uuid4().hex[:8]}", policy=TenantPolicy().model_dump(mode="json"))
        s.add(t)
        s.flush()
        for c, sims in S.simulate(n_customers, 150, bank["attack"], bank["seed"] + 2):
            sims = [x for x in sims if x.event.type in ("TRANSFER", "PAYMENT", "CARD_NOT_PRESENT", "ADD_BENEFICIARY", "CHANGE_LIMIT", "PROFILE_CHANGE", "DEVICE_BINDING")]
            sims = [replace(x, event=replace(x.event, failed_factors=0, spoof=False)) for x in sims if x.event.at >= S.START - 100 * DAY]
            if len(sims) < 12:
                continue
            cust = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=t.id, external_ref=uuid.uuid4().hex[:12])
            s.add(cust)
            s.flush()
            for i, x in enumerate(sims):
                e = x.event
                if i >= len(sims) - 4 and e.at >= S.START:
                    s.flush()
                    from_db = context_builder.risk_history(s, cust, e.at, ZoneInfo("UTC"))
                    lo = sims[0].event.at
                    got = F.build(e, from_db, F.Profile())
                    want = F.build(e, [y.event for y in sims[:i]], F.Profile())
                    checked += 1
                    bad = [k for k in F.FEATURES if not (got[k] == want[k] or (got[k] is not None and want[k] is not None and
                                                                               math.isclose(got[k], want[k], rel_tol=1e-9, abs_tol=1e-9)))]
                    if bad:
                        mism += 1
                        if len(examples) < 3:
                            examples.append((x.scenario, bad[:4], [(k, got[k], want[k]) for k in bad[:2]]))
                s.add(db.Transaction(id=f"txn_{uuid.uuid4().hex[:20]}", tenant_id=t.id, customer_id=cust.id, external_id=uuid.uuid4().hex[:12],
                                     idempotency_key=uuid.uuid4().hex, request_hash="x", type=e.type, amount_minor=int(round(e.amount * 100)), currency="INR",
                                     payee_ref=e.payee, channel=e.channel, status="AUTHORIZED" if e.authorized else "DENIED",
                                     facts={"payeeKey": e.payee, "deviceKey": e.device, "network": e.network, "placeKey": e.place}, created_at=e.at))
        s.rollback()
    return {"checked": checked, "mismatches": mism, "examples": examples}


# ---- the run -----------------------------------------------------------------------------------------------------------------
def pct(v):
    return "-" if v is None else f"{v:.1%}"


def main(n, only, md):
    m = scorer.active()
    report = [f"# Five synthetic banks, tested end to end", "",
              f"Model under test: `{m.version}` (trained on seed 7; every bank here is a different population). Run {time.strftime('%Y-%m-%d %H:%M')}, "
              f"{n:,} customers per bank, 150 days each. Reproduce with `python scripts/evaluate_datasets.py`.", ""]
    verdicts, summary = [], []
    for bank in BANKS:
        if only and bank["id"] not in only:
            continue
        t0 = time.time()
        print(f"\n=== Bank {bank['id']}: {bank['name']}", flush=True)
        sparse = bank.get("sparse", False)
        with tweaked(**bank["changes"]):
            rows = S.dataset(n_customers=n, days=150, attack_rate=bank["attack"], seed=bank["seed"])
            met = pipeline(rows, m)
            fr = fresh_rows(max(300, n // 3), bank)
            cs = cold_start(fr, m)
            cs_hidden = cold_start(fresh_rows(max(300, n // 3), bank, hide=True), m)
        rep = reputation(bank, random.Random(bank["seed"]))
        par = parity(bank)
        sc = {k: v for k, v in met["by_scenario"].items()}
        fraud_t3, genuine_t2 = met["fraud_caught_at_or_above"]["T3"], met["genuine_at_or_above"]["T2"]
        weak = sorted(((v["caught_T3+"], k, v["rows"]) for k, v in sc.items() if v["caught_T3+"] is not None and v["rows"] >= 40))[:4]
        senior = met["by_segment"].get("SENIOR", {}).get("ratio_to_average")
        r = lambda k: (rep.get(k) or [0, 0])
        mule_recall = r("mule")[0] / max(r("mule")[1], 1)
        rep_recall = r("reported")[0] / max(r("reported")[1], 1)
        fp = sum(r(k)[0] for k in ("shop", "utility", "friend")) / max(sum(r(k)[1] for k in ("shop", "utility", "friend")), 1)
        nm_fpr = r("new_merchant")[0] / max(r("new_merchant")[1], 1)
        slow = r("slow_mule")[0] / max(r("slow_mule")[1], 1)
        checks = [
            ("PR-AUC", met["pr_auc"], met["pr_auc"] >= EXPECT["pr_auc_min"], f">= {EXPECT['pr_auc_min']}"),
            ("fraud caught at T3 or above", fraud_t3, fraud_t3 >= EXPECT["fraud_T3_min_attacked" if bank["attack"] > .3 else "fraud_T3_min"],
             f">= {EXPECT['fraud_T3_min_attacked' if bank['attack'] > .3 else 'fraud_T3_min']}"),
            ("fraud money caught at T3 or above", met["fraud_rupees_caught_T3+"], (met["fraud_rupees_caught_T3+"] or 0) >= EXPECT["fraud_rupees_T3_min"], f">= {EXPECT['fraud_rupees_T3_min']}"),
            ("genuine payments stepped up to a face check or more", genuine_t2, genuine_t2 <= EXPECT["genuine_T2_max_sparse_signals" if sparse else "genuine_T2_max"],
             f"<= {EXPECT['genuine_T2_max_sparse_signals' if sparse else 'genuine_T2_max']}"),
            ("weakest attack type (40+ cases) caught at T3+", weak[0][0] if weak else None, (weak[0][0] if weak else 1) >= EXPECT["scenario_floor"], f">= {EXPECT['scenario_floor']}"),
            ("senior step-up ratio (gate fails above 1.25)", senior, senior is None or senior <= EXPECT["senior_ratio_max"], f"<= {EXPECT['senior_ratio_max']} (does not get worse)"),
            ("new customers: median payments to ready", cs["ready_median_payments"], (cs["ready_median_payments"] or 1e9) <= EXPECT["ready_median_payments_max"], f"<= {EXPECT['ready_median_payments_max']}"),
            ("genuine payments a learning customer would have held", cs["false_hold_learning"], cs["false_hold_learning"] <= EXPECT["learning_false_hold_max"], f"<= {EXPECT['learning_false_hold_max']}"),
            ("mule payees flagged", mule_recall, mule_recall >= EXPECT["rep_recall_min"], f">= {EXPECT['rep_recall_min']}"),
            ("reported payees flagged", rep_recall, rep_recall >= EXPECT["rep_reported_recall_min"], f">= {EXPECT['rep_reported_recall_min']}"),
            ("shops, utilities, friends wrongly flagged", fp, fp <= EXPECT["rep_false_positive_max"], f"<= {EXPECT['rep_false_positive_max']}"),
            ("new popular merchants wrongly flagged", nm_fpr, nm_fpr <= EXPECT["rep_new_merchant_fpr_max"], f"<= {EXPECT['rep_new_merchant_fpr_max']}"),
            ("live-path parity mismatches", par["mismatches"], par["mismatches"] <= EXPECT["parity_max_mismatches"], f"<= {EXPECT['parity_max_mismatches']}"),
        ]
        verdicts += [(bank["id"], *c) for c in checks]
        lines = [f"## Bank {bank['id']}: {bank['name']}", "", bank["about"], "",
                 f"{met['n_rows']:,} payments and changes, {met['n_fraud_rows']:,} fraud; {time.time() - t0:.0f}s.", "",
                 "| Check | Measured | Expected | |", "|---|---|---|---|"]
        for name, val, ok, exp in checks:
            shown = f"{val:.3f}" if isinstance(val, float) and val < 5 else (f"{val:.0f}" if isinstance(val, float) else str(val))
            lines.append(f"| {name} | {shown} | {exp} | {'ok' if ok else '**MISS**'} |")
        by_age = {k: v["ratio_to_average"] for k, v in met["by_age"].items()}
        f5, h5 = cs["first5"], cs_hidden["first5"]
        lines += ["", f"- **Weakest attack types** (caught at T3+): " + ", ".join(f"{k} {v:.0%} of {n_}" for v, k, n_ in weak),
                  f"- **Genuine payments stepped up to a face check or more, by age**: " + ", ".join(f"{k} {v}x" for k, v in by_age.items()),
                  f"- **New customers**: {pct(cs['ready_share'])} ready within the run; median {cs['ready_median_payments']:.0f} payments, 90th percentile "
                  f"{cs['ready_p90_payments']:.0f}; false alarms {pct(cs['alarm_learning'])} while learning, {pct(cs['alarm_ready'])} after; "
                  f"learning-phase holds caught {pct(cs['fraud_hold_learning'])} of fraud.",
                  "- **Fraud met while a customer is still being learned** (fingerprint plan): " + ", ".join(
                      f"{lab} {cs['fraud_split'][k][0] / max(sum(v[0] for v in cs['fraud_split'].values()), 1):.0%} of cases / "
                      f"{cs['fraud_split'][k][1] / max(sum(v[1] for v in cs['fraud_split'].values()), 1):.0%} of the money"
                      for k, lab in (("held", "held for a person"), ("code", "a code added"), ("fingerprint", "the fingerprint alone"))) + ".",
                  f"- **Sign-up answers, first five genuine payments**: customers who answered {pct(f5[0] / max(f5[1], 1))} stepped up (of {f5[1]:,}); "
                  f"the same population with answers hidden {pct((h5[2] + h5[0]) / max(h5[3] + h5[1], 1))}.",
                  f"- **Payee reputation**: " + ", ".join(f"{k} {v[0]}/{v[1]} flagged" for k, v in sorted(rep.items())) + f". Slow mules caught {pct(slow)} (expected low).",
                  f"- **Live-path parity**: {par['checked']} payments compared, {par['mismatches']} differed." + (f" Examples: {par['examples']}" if par["examples"] else ""), ""]
        report += lines
        summary.append((bank, met, cs, fraud_t3, genuine_t2, mule_recall, par))
        print("\n".join(lines[:5 + len(checks) + 3]), flush=True)
    misses = [v for v in verdicts if not v[3]]
    head = ["## Summary", "", "| Bank | Fraud caught (T3+) | Genuine stepped up (T2+) | PR-AUC | Ready after (median payments) | Mules flagged | Parity |", "|---|---|---|---|---|---|---|"]
    for bank, met, cs, f3, g2, mr_, par in summary:
        head.append(f"| {bank['id']}. {bank['name']} | {f3:.1%} | {g2:.1%} | {met['pr_auc']:.3f} | {cs['ready_median_payments']:.0f} | {mr_:.0%} | {par['mismatches']} of {par['checked']} differ |")
    head += ["", f"**{len(verdicts) - len(misses)} of {len(verdicts)} checks met their expectation.**" + ("" if not misses else " Missed: " + "; ".join(
        f"bank {b}: {n_} ({v if not isinstance(v, float) else round(v, 3)})" for b, n_, v, ok, e in misses)), ""]
    report[4:4] = head
    text = "\n".join(report)
    if md:
        Path(md).write_text(text)
    print("\n" + "\n".join(head), flush=True)
    return 1 if misses else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = next(({int(x) for x in a.split("=")[1].split(",")} for a in sys.argv if a.startswith("--only=")), None)
    out = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--md=")), None)
    sys.exit(main(int(args[0]) if args else 4000, only, out))
