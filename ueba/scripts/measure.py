"""
Measure UEBA on real people: genuine vs impostor attempts, per input.

The team test: each person enrols (on a laptop, a phone, or both), then
verifies several times on their own account and several times on other
people's accounts, on at least two different days. Whoever is actually at
the device puts their own name in "Who is testing" on the Verify page; that
name is stored with the result as testerId. An attempt is genuine when the
tester is the account's owner, an impostor attempt otherwise. Attempts
without a tester name are ignored.

    python -m scripts.measure
    python -m scripts.measure --input touch --csv attempts.csv
    python -m scripts.measure --fit          # fit touch calibrators from the data

Error rates are averaged per person (FRR) and per tester-account pair (FAR),
not pooled over attempts: ten attempts by one person are not ten people.
With a handful of testers these are a first indication, not a guarantee.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from api.contract import THRESHOLD
from scoring.combine import COMPLETE_COVERAGE, CalibrationSet, DimensionCalibrator
from store import db

DB_PATH = os.getenv("DB_PATH", "ueba.db")
DIMS = ("keystroke", "shape")


def load(path: str) -> list[dict]:
    rows = []
    for v in db.get_verifications(None, 100000, path):
        d = v["dimensions"]
        if d.get("kind") != "factor" or not d.get("testerId"):
            continue
        score = d.get("score") if d.get("score") is not None else d.get("shadowScore")
        if score is None:          # no templates for that input: nothing was compared
            continue
        row = {"account": v["user_id"], "tester": d["testerId"],
               "genuine": d["testerId"] == v["user_id"], "input": d.get("input", "keyboard"),
               "score": float(score), "status": v["decision"],
               "day": datetime.fromtimestamp(v["decided_at"], timezone.utc).date().isoformat()}
        for k in DIMS:
            x = d.get(k) or {}
            ok = (x.get("coverage") or 0) >= COMPLETE_COVERAGE and not x.get("inputMismatch")
            row[k] = x.get("observedDistance") if ok else None
        rows.append(row)
    return rows


def eer(genuine: list[float], impostor: list[float], higher_is_genuine: bool) -> float | None:
    g, i = np.asarray(genuine, float), np.asarray(impostor, float)
    if not len(g) or not len(i):
        return None
    if higher_is_genuine:
        g, i = -g, -i
    ts = np.unique(np.concatenate([g, i]))
    far = np.array([(i <= t).mean() for t in ts])
    frr = np.array([(g > t).mean() for t in ts])
    k = int(np.argmin(np.abs(far - frr)))
    return float((far[k] + frr[k]) / 2)


def macro(groups: dict, fn) -> tuple[float | None, int]:
    vals = [fn(v) for v in groups.values() if v]
    return (float(np.mean(vals)), len(vals)) if vals else (None, 0)


def pct(x: float | None) -> str:
    return "  -  " if x is None else f"{100 * x:5.1f}%"


def report(rows: list[dict], input: str, threshold: float) -> None:
    rs = [r for r in rows if r["input"] == input]
    gen = [r for r in rs if r["genuine"]]
    imp = [r for r in rs if not r["genuine"]]
    print(f"\n=== {input} ===")
    if not rs:
        print("  no labelled attempts")
        return
    people = sorted({r["account"] for r in rs} | {r["tester"] for r in rs})
    print(f"  attempts: {len(gen)} genuine, {len(imp)} impostor; "
          f"{len(people)} people; days: {', '.join(sorted({r['day'] for r in rs}))}")

    by_person = defaultdict(list)
    for r in gen:
        by_person[r["account"]].append(r["score"] < threshold)
    by_pair = defaultdict(list)
    for r in imp:
        by_pair[(r["tester"], r["account"])].append(r["score"] >= threshold)
    frr, n_p = macro(by_person, np.mean)
    far, n_pairs = macro(by_pair, np.mean)
    print(f"  at threshold {threshold:.2f}: FRR {pct(frr)} (averaged over {n_p} people), "
          f"FAR {pct(far)} (averaged over {n_pairs} tester-account pairs)")
    print(f"  factor score EER: {pct(eer([r['score'] for r in gen], [r['score'] for r in imp], True))}")

    for k in DIMS:
        g = [r[k] for r in gen if r[k] is not None]
        i = [r[k] for r in imp if r[k] is not None]
        print(f"  {k:9s} distance EER: {pct(eer(g, i, False))}  "
              f"(complete answers: {len(g)} genuine, {len(i)} impostor)")

    # Same-day results flatter: people fail on later days (seen in live tests).
    first = {}
    for r in sorted(gen, key=lambda r: r["day"]):
        first.setdefault(r["account"], r["day"])
    same = [r["score"] < threshold for r in gen if r["day"] == first[r["account"]]]
    later = [r["score"] < threshold for r in gen if r["day"] != first[r["account"]]]
    print(f"  genuine rejected: first day {pct(np.mean(same) if same else None)} (n={len(same)}), "
          f"later days {pct(np.mean(later) if later else None)} (n={len(later)})")
    print("  per person (own account): " + ", ".join(
        f"{p} {sum(v)}/{len(v)} rejected" for p, v in sorted(by_person.items())))


def fit(rows: list[dict], inputs: list[str], artifacts: Path) -> None:
    cs = CalibrationSet.load(artifacts)
    for input in inputs:
        for k in DIMS:
            key = k if input == "keyboard" else f"{k}_{input}"
            g = [r[k] for r in rows if r["input"] == input and r["genuine"] and r[k] is not None]
            i = [r[k] for r in rows if r["input"] == input and not r["genuine"] and r[k] is not None]
            if len(g) < 5 or len(i) < 5:
                print(f"  {key}: skipped, need 5+ complete genuine and impostor answers "
                      f"(have {len(g)} and {len(i)})")
                continue
            cal = DimensionCalibrator.fit(g, i)
            cs.add(key, cal)
            warn = "  (small sample: refit as data grows)" if min(len(g), len(i)) < 30 else ""
            print(f"  {key}: fitted on {len(g)} genuine / {len(i)} impostor, "
                  f"EER {cal.eer:.3f}{warn}")
    cs.save(artifacts)
    print(f"  written to {artifacts / CalibrationSet.FILENAME}; restart the service to use it")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--input", choices=["keyboard", "touch"], action="append")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--csv", help="also write every labelled attempt to this CSV file")
    ap.add_argument("--fit", action="store_true",
                    help="fit calibrators from the labelled attempts (touch only unless --input keyboard)")
    ap.add_argument("--artifacts", default=os.getenv("ARTIFACT_DIR", "artifacts"))
    a = ap.parse_args()

    rows = load(a.db)
    print(f"{len(rows)} labelled attempts in {a.db}")
    for input in a.input or ["keyboard", "touch"]:
        report(rows, input, a.threshold)
    if a.csv and rows:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {len(rows)} rows to {a.csv}")
    if a.fit:
        print("\nfitting calibrators")
        fit(rows, a.input or ["touch"], Path(a.artifacts))


if __name__ == "__main__":
    main()
