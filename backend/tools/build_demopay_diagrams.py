"""Draws the figures for docs/demopay-implementation.pdf into docs/demopay-assets/. Run: cd backend && .venv/bin/python -m tools.build_demopay_diagrams"""
import itertools
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demopay-assets"
sys.path.insert(0, str(ROOT / "demopay" / "backend"))
from authn import policy  # noqa: E402
from authn.registry import METHODS  # noqa: E402

NAVY, TEAL, AMBER, RED, GREEN, GREY, LIGHT = "#14325c", "#1b7f8c", "#b86b00", "#a32020", "#2e7d4f", "#6b7a8c", "#eef3f8"
plt.rcParams["font.family"] = "DejaVu Sans"


def box(ax, x, y, w, h, title, lines=(), fc=TEAL, tc="white", fs=8.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fc, ec=fc))
    ax.text(x + w / 2, y + h - 0.2, title, ha="center", va="top", color=tc, fontsize=fs, fontweight="bold")
    for i, l in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.5 - 0.26 * i, l, ha="center", va="top", color=tc, fontsize=fs - 1.7)


def arrow(ax, a, b, color="#555555", style="-|>", rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=11, color=color, lw=1.3, connectionstyle=f"arc3,rad={rad}"))


def fig_system():
    f, ax = plt.subplots(figsize=(7.4, 4.1)); ax.set_xlim(0, 10); ax.set_ylim(0, 5.6); ax.axis("off")
    box(ax, 0.1, 3.7, 2.7, 1.7, "Customer app", ["Web app (React) and", "Android app (Kotlin)", "shows steps, PIN pad,", "model X-ray (demo only)"], NAVY)
    box(ax, 3.7, 3.7, 2.6, 1.7, "DemoPay API", ["FastAPI + SQLite", "sign-in, payments,", "challenge records"], TEAL)
    box(ax, 7.2, 3.7, 2.7, 1.7, "Fake bank directory", ["who owns each payee id,", "account age, senders,", "fraud-risk band, reports"], GREY)
    box(ax, 0.1, 0.2, 2.9, 2.5, "1. Risk model", ["three detectors:", "takeover, coached, bad payee", "46 inputs from one payment", "-> severity + threat family"], AMBER)
    box(ax, 3.55, 0.2, 2.9, 2.5, "2. Decision engine", ["prices every option:", "allow, block, or 1-3 steps", "-> cheapest plan that fits", "the user's time budget"], NAVY)
    box(ax, 7.0, 0.2, 2.9, 2.5, "3. Authenticators", ["warning, typed name,", "payee check, cooling-off", "one challenge service:", "nonce, expiry, 3 tries"], GREEN)
    arrow(ax, (2.8, 4.55), (3.7, 4.55)); arrow(ax, (3.7, 4.25), (2.8, 4.25))
    arrow(ax, (6.3, 4.55), (7.2, 4.55)); arrow(ax, (7.2, 4.25), (6.3, 4.25))
    arrow(ax, (4.6, 3.7), (1.9, 2.7), rad=0.0); arrow(ax, (3.0, 1.45), (3.55, 1.45)); arrow(ax, (6.45, 1.45), (7.0, 1.45))
    arrow(ax, (5.2, 2.7), (5.2, 3.7), color=NAVY)
    ax.text(0.15, 2.85, "payment arrives", fontsize=7, color="#444444"); ax.text(5.3, 3.12, "plan back to the app", fontsize=7, color="#444444")
    f.savefig(OUT / "fig_system.png", dpi=200, bbox_inches="tight"); plt.close(f)


def fig_lifecycle():
    f, ax = plt.subplots(figsize=(7.4, 3.7)); ax.set_xlim(0, 12); ax.set_ylim(0, 6.0); ax.axis("off")
    top = [("1  Tap Pay", ["payee, amount,", "phone signals"], NAVY), ("2  Risk check", ["replay history,", "add payee facts", "and phone signals"], AMBER),
           ("3  Risk model", ["3 detectors give", "LOW, MEDIUM, HIGH", "or CRITICAL"], AMBER), ("4  Decision", ["prices allow, block", "and every plan of", "1-3 steps"], TEAL)]
    xs = [0.05, 2.55, 5.05, 7.55]
    for x, (t, ls, c) in zip(xs, top):
        box(ax, x, 2.6, 2.1, 2.2, t, ls, c, fs=8.5)
    for a_, b_ in zip(xs[:-1], xs[1:]):
        arrow(ax, (a_ + 2.1, 3.7), (b_, 3.7))
    for y, (t, l, c) in zip((5.15, 3.85, 2.55), (("allow", "no steps", GREEN), ("steps", "1 to 3 checks", AMBER), ("block", "too risky", RED))):
        box(ax, 10.3, y - 0.45, 1.65, 1.15, t, [l], c, fs=8.5)
        arrow(ax, (9.65, 3.7), (10.3, y + 0.12))
    ax.add_patch(FancyBboxPatch((0.05, 0.1), 11.9, 1.65, boxstyle="round,pad=0.02,rounding_size=0.08", fc=LIGHT, ec=GREY))
    lines = ["After the plan: steps run one at a time, each tied to this payment by a one-time code.",
             "All passed -> PIN pad -> the server re-checks that every step passed -> money moves.",
             "A danger answer, 3 wrong tries, an expired step or Cancel -> payment stopped.",
             "An allowed payment skips the steps and goes straight to the PIN pad."]
    for i, t in enumerate(lines):
        ax.text(0.3, 1.5 - 0.37 * i, t, fontsize=7.4, color=NAVY if i == 0 else "#222222", va="top", fontweight="bold" if i == 0 else "normal")
    f.savefig(OUT / "fig_lifecycle.png", dpi=200, bbox_inches="tight"); plt.close(f)


def fig_ladder():
    f, ax = plt.subplots(figsize=(7.4, 2.9)); ax.set_xlim(0, 12); ax.set_ylim(0, 4.2); ax.axis("off")
    rows = [("LOW", "about 98% of genuine payments stay here", "Allow. No steps.", "#2e7d4f"),
            ("MEDIUM", "set so only about 2% of genuine payments reach this level or higher", "Fast steps only, up to 10 s of the user's time", "#b8860b"),
            ("HIGH", "set so only about 0.3% of genuine payments reach this level or higher", "Fast steps up to 30 s; one slow step only for a tricked owner paying Rs 50,000 or more", "#c4630a"),
            ("CRITICAL", "set so only about 0.05% of genuine payments reach this level or higher", "No time limit; slow steps allowed; block if no plan is cheaper", "#a32020")]
    for i, (n, share, what, c) in enumerate(rows):
        y = 3.2 - i * 1.05
        ax.add_patch(FancyBboxPatch((0.05, y), 2.0, 0.8, boxstyle="round,pad=0.02,rounding_size=0.08", fc=c, ec=c))
        ax.text(1.05, y + 0.4, n, ha="center", va="center", color="white", fontweight="bold", fontsize=9)
        ax.text(2.3, y + 0.52, share, fontsize=7.5, color="#333333", va="center")
        ax.text(2.3, y + 0.2, what, fontsize=8.3, color=NAVY, va="center", fontweight="bold")
    f.savefig(OUT / "fig_ladder.png", dpi=200, bbox_inches="tight"); plt.close(f)


def example():
    """Ramesh, digital arrest: HIGH + COACHED, Rs 20,000, senior rule (slow steps from Rs 10,000), base rate 1 in 10,000."""
    sev, fam, amt, prior = "HIGH", "COACHED", 20000.0, 1e-4
    plan = policy.make_plan(sev, fam, amt, [], prior, policy.DEFAULT_TIME_BUDGET, 10000.0)
    p = policy.posterior(sev, prior)
    mix = policy.family_mix(fam)
    pool = [m for m in METHODS.values() if m.implemented]
    opts = []
    for n in range(1, 4):
        for seq in itertools.permutations(pool, n):
            secs = sum(m.seconds for m in seq if m.tier == "fast")
            if secs > 30:
                continue
            fr, leak, _, _, sg = policy._run(seq, p, mix)
            opts.append((fr + sg * policy.block_cost(amt) + leak * amt, "+".join(m.id.split("_")[0] for m in seq)))
    opts.sort()
    return plan, p, p * amt, (1 - p) * policy.block_cost(amt), opts


def fig_costs():
    plan, p, allow_c, block_c, opts = example()
    best = {}
    for c, n in opts:
        best.setdefault(n.count("+") + 1, (c, n))
    items = [("Allow (no steps)", allow_c, RED), ("Block", block_c, RED)] + [(f"best {k}-step plan: {n}", c, TEAL) for k, (c, n) in sorted(best.items())]
    items.sort(key=lambda t: t[1])
    f, ax = plt.subplots(figsize=(7.4, 3.2))
    names = [i[0] for i in items][::-1]; vals = [i[1] for i in items][::-1]; cols = [i[2] for i in items][::-1]
    cols = [GREEN if v == min(vals) else c for v, c in zip(vals, cols)]
    ax.barh(names, vals, color=cols)
    for i, v in enumerate(vals):
        ax.text(v + 2, i, f"Rs {v:,.0f}", va="center", fontsize=8)
    ax.set_xlabel("expected cost in rupee-equivalents (lower is better)", fontsize=8); ax.tick_params(labelsize=8)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    f.savefig(OUT / "fig_costs.png", dpi=200, bbox_inches="tight"); plt.close(f)
    print("chosen plan:", [s.method for s in plan.steps], "cost", round(plan.expected_cost, 2), "p", p, "allow", allow_c, "block", block_c)
    print(opts[:6])


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    fig_system(); fig_lifecycle(); fig_ladder(); fig_costs()
    print("ok")
