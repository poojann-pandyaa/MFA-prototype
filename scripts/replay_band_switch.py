"""Replay a simulated bank through the band switch from day one, and report when each amount band earned the model and what it cost.

    AMFA_ENV=development python scripts/replay_band_switch.py [customers] [--attack=0.3] [--seed=11] [--minimums] [--no-labels] [--md=PATH]

  customers    simulated customers (default 1500), 150 days each
  --attack     share of customers attacked (default 0.3)
  --minimums   grade with the hard minimums (2,000 genuine, 50 fraud, 4 weeks) instead of the defaults, for a small bank
  --no-labels  fraud is never reported by the bank: no band should ever become eligible
  --md         also write the report as Markdown

Uses the active risk model; trains a small one first if there is none. See amfa/demo/band_replay.py for what is real and what is
simulated.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("AMFA_DATA_DIR", tempfile.mkdtemp(prefix="amfa-replay-"))      # a throwaway database
os.environ.setdefault("AMFA_ENV", "development")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amfa.demo import band_replay
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk import scenarios as S
from amfa.engine.risk import scorer, train
from amfa.engine.risk.collection_bands import label


def _arg(name, default):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def pct(v):
    return "–" if v is None else f"{v:.1%}"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")      # the report has rupee signs; a Windows console defaults to cp1252
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 1500)
    attack, seed = float(_arg("attack", 0.3)), int(_arg("seed", 11))
    minimums, no_labels, md = "--minimums" in sys.argv, "--no-labels" in sys.argv, _arg("md", None)
    try:
        m = scorer.active()
    except Exception:
        print("no active risk model: training a small one (about a minute)", flush=True)
        train.run(customers=3000, search=[train.SEARCH[0]], seeds=1, force=True)
        scorer._load.cache_clear()
        m = scorer.active()
    gate = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4) if minimums else BandGate()
    top = gate if minimums else CollectionPolicy().topGate
    policy = TenantPolicy(collection=CollectionPolicy(enabled=True, gate=gate, topGate=top))
    t0 = time.time()
    rows = S.dataset(n_customers=n, days=150, attack_rate=attack, seed=seed)
    print(f"{len(rows):,} simulated rows in {time.time() - t0:.0f}s; replaying day by day", flush=True)
    rep = band_replay.replay(rows, policy=policy, label_source="simulator" if no_labels else "chargeback",
                             progress=lambda d, r: print(f"  day {d}: {r.payments:,} payments", flush=True))
    lines = [f"# Band switch replay", "",
             f"Model `{m.version}`. {n:,} simulated customers, 150 days, {attack:.0%} attacked, seed {seed}; "
             f"{rep.payments:,} payments over {rep.days} days. Gates: "
             f"{'the hard minimums' if minimums else 'the defaults'}; fraud reports: "
             f"{'none (simulated source, never counted)' if no_labels else 'every fraud, reported by the bank'}. "
             f"Two operators approve a band the night it becomes eligible. Run {time.strftime('%Y-%m-%d %H:%M')}.", "",
             f"Payments where the switch asked for less than the switch off would have: **{rep.weaker_than_off}** (must be 0).", "",
             "| Band | Payments (fraud) | Eligible on day | On the model from day | Fell back on days | Honest customers stepped up: collecting / on model / model alone | Fraud caught: collecting / on model / model alone |",
             "|---|---|---|---|---|---|---|"]
    for b in band_replay.summary(rep):
        lines.append(f"| {label(b['band'])} | {b['genuine'] + b['fraud']:,} ({b['fraud']:,}) | {b['eligibleDay'] if b['eligibleDay'] is not None else 'never'} | "
                     f"{b['modelDay'] if b['modelDay'] is not None else 'never'} | {', '.join(map(str, b['fallbackDays'])) or '–'} | "
                     f"{pct(b['stepUpWhileCollecting'])} / {pct(b['stepUpOnModel'])} / {pct(b['modelAloneStepUp'])} | "
                     f"{pct(b['fraudCaughtWhileCollecting'])} / {pct(b['fraudCaughtOnModel'])} / {pct(b['modelAloneFraudCaught'])} |")
    lines += ["", "What the last grading said was still missing, per band:", ""]
    for b in band_replay.summary(rep):
        lines.append(f"- {label(b['band'])}: {'; '.join(b['lastMissing']) or 'nothing'}")
    lines += ["", "Stepped up = asked for a face or voice check, or held. Simulated customers: these show the mechanism works and what it "
              "costs, not how a real bank's customers or criminals behave.", f"Took {time.time() - t0:.0f}s."]
    print("\n".join(lines))
    if md:
        Path(md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if rep.weaker_than_off == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
