"""Write the simulated training dataset and a report of what it covers.

    python -m amfa.engine.risk.export                       # 5,000 customers -> data/risk-dataset/
    python -m amfa.engine.risk.export --customers 20000 --out /tmp/risk

dataset.csv   one row per decision: customer, time, scenario, label, segment, age, age_group, archetype,
              feed, weight, amount (rupees) and the model's features (empty = missing). Age and amount are
              dataset attributes for checking coverage and fairness; the model is not given age. Weight is
              how many real decisions the row stands for; use it as the sample weight.
coverage.json / coverage.md   rows per scenario, segment, customer type and data feed, and how often each
              feature is missing or extreme."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from amfa import config

from .features import FEATURES
from .scenarios import (AGE_GROUPS, AMOUNT_BANDS, ARCHETYPES, ATTACKS, FEEDS, SCENARIO_DOCS, Row, amount_band,
                        dataset)

META = ["customer", "at", "scenario", "label", "segment_name", "age", "age_group", "archetype", "feed", "weight",
        "amount"]


def coverage(rows: list[Row]) -> dict:
    by = defaultdict(lambda: {"rows": 0, "customers": set(), "labels": set(), "weight": 0.0})
    for r in rows:
        s = by[r.scenario]
        s["rows"] += 1; s["customers"].add(r.customer); s["labels"].add(r.label); s["weight"] += r.weight
    feats = {}
    for f in FEATURES:
        v = [r.features[f] for r in rows if r.features[f] is not None]
        feats[f] = {"missing": round(1 - len(v) / len(rows), 4)} | (
            {"min": round(min(v), 3), "median": round(statistics.median(v), 3), "max": round(max(v), 3)} if v else {})
    return {
        "rows": len(rows), "fraud_rows": sum(r.label for r in rows),
        "fraud_share_of_traffic": round(sum(r.weight for r in rows if r.label) / sum(r.weight for r in rows), 5),
        "missing_scenarios": [t for t in SCENARIO_DOCS if t not in by],
        "undocumented_scenarios": [t for t in by if t not in SCENARIO_DOCS],
        "scenarios": {t: {"kind": "attack" if t in ATTACKS else "genuine" if 0 in v["labels"] else "attack-part",
                          "rows": v["rows"], "customers": len(v["customers"]),
                          "traffic_share": round(v["weight"] / sum(r.weight for r in rows), 5),
                          "what": SCENARIO_DOCS.get(t, "")} for t, v in sorted(by.items())},
        "by_segment": dict(Counter(r.segment for r in rows)),
        "by_age_group": dict(Counter(r.age_group for r in rows)),
        "age_by_amount_range": grid(rows),
        "by_archetype": dict(Counter(r.archetype for r in rows)),
        "by_feed": dict(Counter(r.feed for r in rows)),
        "features": feats,
    }


def grid(rows: list[Row]) -> dict:
    """Rows per age group and amount range, genuine and fraud separately (money-moving rows only)."""
    out = {kind: {g: {b[2]: 0 for b in AMOUNT_BANDS} for g in AGE_GROUPS} for kind in ("genuine", "fraud")}
    for r in rows:
        if r.amount > 0:
            out["fraud" if r.label else "genuine"][r.age_group][amount_band(r.amount)] += 1
    return out


def markdown(c: dict) -> str:
    out = [f"# Risk dataset coverage\n\n{c['rows']:,} rows, {c['fraud_rows']:,} fraud "
           f"({c['fraud_share_of_traffic']:.2%} of simulated traffic after weighting).\n",
           f"Customer types: {len(ARCHETYPES)} · attacks: {len(ATTACKS)} · data feeds: {len(FEEDS)} · "
           f"scenarios: {len(c['scenarios'])}\n"]
    for kind, title in (("genuine", "Genuine situations (label 0)"), ("attack", "Attacks (label 1)"),
                        ("attack-part", "Parts of an attack that are labelled genuine")):
        out += [f"\n## {title}\n", "| scenario | rows | customers | what it is |", "|---|---|---|---|"]
        out += [f"| {t} | {v['rows']:,} | {v['customers']:,} | {v['what']} |"
                for t, v in c["scenarios"].items() if v["kind"] == kind]
    out += ["\n## Data feeds\n", "| feed | rows |", "|---|---|"] + [f"| {k} | {v:,} |" for k, v in c["by_feed"].items()]
    for kind in ("genuine", "fraud"):
        names = [b[2] for b in AMOUNT_BANDS]
        out += [f"\n## Age group x amount range ({kind} rows)\n", "| age | " + " | ".join(names) + " |",
                "|---|" + "---|" * len(names)]
        out += [f"| {g} | " + " | ".join(f"{c['age_by_amount_range'][kind][g][n]:,}" for n in names) + " |"
                for g in AGE_GROUPS]
    out += ["\n## Customer segments\n", "| segment | rows |", "|---|---|"] + [
        f"| {k} | {v:,} |" for k, v in c["by_segment"].items()]
    out += ["\n## Customer types\n", "| type | rows |", "|---|---|"] + [
        f"| {k} | {v:,} |" for k, v in sorted(c["by_archetype"].items(), key=lambda kv: -kv[1])]
    out += ["\n## Features\n", "| feature | missing | min | median | max |", "|---|---|---|---|---|"] + [
        f"| {f} | {v['missing']:.1%} | {v.get('min', '')} | {v.get('median', '')} | {v.get('max', '')} |"
        for f, v in c["features"].items()]
    if c["missing_scenarios"]:
        out.append(f"\n**Scenarios with no rows:** {', '.join(c['missing_scenarios'])}")
    return "\n".join(out) + "\n"


def write(rows: list[Row], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "dataset.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(META + FEATURES)
        for r in rows:
            cells = ["" if r.features[k] is None else round(r.features[k], 5) for k in FEATURES]
            w.writerow([r.customer, round(r.at), r.scenario, r.label, r.segment, r.age, r.age_group, r.archetype,
                        r.feed, round(r.weight, 4), r.amount] + cells)
    cov = coverage(rows)
    (out / "coverage.json").write_text(json.dumps(cov, indent=2))
    (out / "coverage.md").write_text(markdown(cov))
    return cov


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--customers", type=int, default=5000)
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=config.get().data_dir / "risk-dataset")
    a = ap.parse_args()
    cov = write(dataset(a.customers, a.days, seed=a.seed), a.out)
    print(f"{cov['rows']:,} rows ({cov['fraud_rows']:,} fraud), {len(cov['scenarios'])} scenarios -> {a.out}")
    if cov["missing_scenarios"]:
        print("NO ROWS FOR:", ", ".join(cov["missing_scenarios"]))


if __name__ == "__main__":
    main()
