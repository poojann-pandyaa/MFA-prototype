"""Option A against option B: how much does the risk model lose if the system knows each payment only by its amount band?

    AMFA_ENV=development python scripts/compare_amount_storage.py [customers] [--seeds=7,8,9] [--views=exact,bands4,bands8]
                                                                  [--full] [--md=docs/amount-bands-accuracy.md]

For every data seed, one model per amount view is trained on the same simulated customers (engine/risk/amount_view.py):
  exact    exact amounts, as today (option A)
  bands4   the band switch's four bands (option B)
  bands8   eight finer bands, to show how the loss grows as bands get coarser
Each is tested on the same held-out weeks with the project's own measures (engine/risk/train.evaluate), both for the model alone
and after the reliefs and the habit rule, which is what customers meet. The same seed gives the same customers, payments and test
rows to every view, so the comparison is paired.

Models are written to a throwaway folder and never activated; a model trained on bands could not be loaded by the live system
anyway. By default each model is one booster on the first grid setting (fast); --full uses the training defaults (slow).
"""
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("AMFA_DATA_DIR", tempfile.mkdtemp(prefix="amfa-compare-"))
os.environ.setdefault("AMFA_ENV", "development")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amfa.engine.risk import amount_view, train
from amfa.engine.risk.collection_bands import label

BANDS = ("0", "1", "2", "3")


def _arg(name, default):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def measures(card: dict) -> dict:
    """The numbers that are compared, from one model card."""
    m = card["metrics"]
    senior = (m.get("by_segment", {}).get("SENIOR") or {}).get("ratio_to_average")
    out = {
        "pr_auc": m["pr_auc"],
        "fraud_caught_T2": m["fraud_caught_at_or_above"]["T2"],
        "fraud_caught_T3": m["fraud_caught_at_or_above"]["T3"],
        "fraud_money_caught_T3": m["fraud_rupees_caught_T3+"],
        "genuine_stepped_up_T2": m["genuine_at_or_above"]["T2"],
        "model_alone_fraud_caught_T2": m["uncapped"]["fraud_caught_at_or_above"]["T2"],
        "model_alone_genuine_stepped_up_T2": m["uncapped"]["genuine_at_or_above"]["T2"],
        "senior_step_up_ratio": senior,
    }
    for b, v in (m.get("by_collection_band") or {}).items():
        out[f"band{b}_fraud_caught_T2"] = v["fraud_caught_T2+"]
        out[f"band{b}_genuine_stepped_up_T2"] = v["genuine_stepped_up_T2+"]
        out[f"band{b}_fraud_money_caught_T3"] = v["fraud_rupees_caught_T3+"]
    return out


def run(customers: int, seeds: list[int], views: list[str], full: bool, out_root: Path, progress=print) -> dict:
    """results[view][seed] = measures. Every view of one seed is trained on the same data."""
    results: dict[str, dict[int, dict]] = {v: {} for v in views}
    kwargs = {} if full else {"search": [train.SEARCH[0]], "seeds": 1}
    for seed in seeds:
        for view in views:
            t0 = time.time()
            out = train.run(customers=customers, days=150, seed=seed, activate=False, force=True,
                            out_root=out_root / f"{view}-{seed}", amount_view=view, **kwargs)
            card = json.loads((out / "card.json").read_text())
            assert card["amountView"] == view and card["data"]["seed"] == seed
            results[view][seed] = measures(card) | {"test_rows": card["metrics"]["fraud_rows"] + card["metrics"]["genuine_rows"]}
            progress(f"  {view} seed {seed}: PR-AUC {results[view][seed]['pr_auc']}  ({time.time() - t0:.0f}s)")
    return results


def summary(results: dict) -> dict:
    """Per view and measure: the mean over seeds, and the mean paired difference from exact."""
    exact = results.get(amount_view.EXACT, {})
    out = {}
    for view, by_seed in results.items():
        keys = sorted({k for m in by_seed.values() for k in m})
        out[view] = {}
        for k in keys:
            vals = [m[k] for m in by_seed.values() if m.get(k) is not None]
            diffs = [by_seed[s][k] - exact[s][k] for s in by_seed if s in exact
                     and by_seed[s].get(k) is not None and exact[s].get(k) is not None]
            out[view][k] = {"mean": round(statistics.mean(vals), 4) if vals else None,
                            "diff": round(statistics.mean(diffs), 4) if diffs and view != amount_view.EXACT else None,
                            "spread": round(max(vals) - min(vals), 4) if len(vals) > 1 else None}
    return out


def pct(v, signed=False):
    if v is None:
        return "–"
    return f"{v * 100:+.1f} pts" if signed else f"{v:.1%}"


def report(results: dict, s: dict, customers: int, seeds: list[int], views: list[str], full: bool) -> str:
    rows = [("Accuracy (PR-AUC)", "pr_auc", False), ("Fraud caught at face check or more (T2+)", "fraud_caught_T2", True),
            ("Fraud caught at face and voice (T3+)", "fraud_caught_T3", True), ("Fraud money caught (T3+)", "fraud_money_caught_T3", True),
            ("Honest customers asked for a face check or more", "genuine_stepped_up_T2", True),
            ("Model alone: fraud caught T2+", "model_alone_fraud_caught_T2", True),
            ("Model alone: honest customers stepped up T2+", "model_alone_genuine_stepped_up_T2", True),
            ("Older customers stepped up, times the average", "senior_step_up_ratio", False)]
    head = "| Measure | " + " | ".join(views) + " |"
    lines = [f"Simulated customers: {customers:,} per seed, 150 days; seeds {', '.join(map(str, seeds))}; "
             f"{'training defaults' if full else 'one booster on the first grid setting'}. "
             f"Test rows per model: {results[views[0]][seeds[0]]['test_rows']:,} (the same rows for every view of a seed). "
             f"Run {time.strftime('%Y-%m-%d %H:%M')}.", "",
             "Each cell is the mean over seeds; for a banded view, the change from exact on the same data is in brackets.", "",
             head, "|---|" + "---|" * len(views)]

    def cell(view, key, as_pct):
        v = s[view].get(key) or {}
        mean, diff = v.get("mean"), v.get("diff")
        shown = pct(mean) if as_pct else ("–" if mean is None else f"{mean:.3f}")
        if diff is not None:
            shown += f" ({pct(diff, True)})" if as_pct else f" ({diff:+.3f})"
        return shown

    for name, key, as_pct in rows:
        lines.append(f"| {name} | " + " | ".join(cell(v, key, as_pct) for v in views) + " |")
    lines += ["", "By the band switch's amount bands (after the reliefs and the habit rule):", "", head, "|---|" + "---|" * len(views)]
    for b in BANDS:
        for name, key in (("fraud caught T2+", f"band{b}_fraud_caught_T2"), ("honest stepped up T2+", f"band{b}_genuine_stepped_up_T2"),
                          ("fraud money caught T3+", f"band{b}_fraud_money_caught_T3")):
            lines.append(f"| {label(int(b))}: {name} | " + " | ".join(cell(v, key, True) for v in views) + " |")
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    customers = next((int(a) for a in sys.argv[1:] if a.isdigit()), 3000)
    seeds = [int(x) for x in _arg("seeds", "7,8,9").split(",")]
    views = _arg("views", ",".join(amount_view.VIEWS)).split(",")
    for v in views:
        amount_view.get(v)                         # refuses an unknown view before anything is trained
    if amount_view.EXACT not in views:
        views = [amount_view.EXACT] + views        # everything is measured against exact amounts
    full, md = "--full" in sys.argv, _arg("md", None)
    with tempfile.TemporaryDirectory(prefix="amfa-amount-views-") as tmp:
        results = run(customers, seeds, views, full, Path(tmp))
    text = report(results, summary(results), customers, seeds, views, full)
    print(text)
    if md:
        Path(md).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
