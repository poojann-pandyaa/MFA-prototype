"""Figures for docs/demopay-11-checks.pdf. Run: cd backend && .venv/bin/python -m tools.build_demopay_mfa_figures"""
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demopay-assets"
sys.path.insert(0, str(ROOT / "demopay" / "backend"))
from authn.registry import METHODS  # noqa: E402

NAVY, TEAL, AMBER, RED, GREEN, GREY, LIGHT = "#14325c", "#1b7f8c", "#b86b00", "#a32020", "#2e7d4f", "#6b7a8c", "#eef3f8"
ORDER = list(METHODS)          # registry order = the numbering used in the PDF
plt.rcParams["font.family"] = "DejaVu Sans"


def rbox(ax, x, y, w, h, fc, ec=None, ls="-", lw=1.0, r=0.06):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.01,rounding_size={r}", fc=fc, ec=ec or fc, ls=ls, lw=lw))


def arrow(ax, a, b, color="#555555"):
    ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="-|>", color=color, lw=1.3, mutation_scale=11))


SHORT = ["Scam warning", "Type payee name", "Payee check", "Cooling-off wait", "Second device", "Face + liveness", "Device key signing",
         "Authenticator code", "Passkey", "Trusted contact", "Bank call-back"]


def fig_journey():
    f, ax = plt.subplots(figsize=(7.4, 5.3)); ax.set_xlim(0, 12.4); ax.set_ylim(0, 7.6); ax.axis("off")
    for x, t, sub, col in ((0.0, "Customer\ntaps Pay", "payee, amount,\nphone signals", NAVY), (1.75, "Risk model", "3 detectors:\ntakeover, coached,\nbad payee", AMBER), (3.5, "Decision\nengine", "picks the\ncheapest mix\nof checks", TEAL)):
        rbox(ax, x, 3.2, 1.55, 2.5, col)
        ax.text(x + 0.775, 5.55, t.replace("\\n", "\n"), ha="center", va="top", color="white", fontsize=7.4, fontweight="bold")
        ax.text(x + 0.775, 4.55, sub.replace("\\n", "\n"), ha="center", va="top", color="white", fontsize=5.8)
    arrow(ax, (1.55, 4.45), (1.75, 4.45)); arrow(ax, (3.3, 4.45), (3.5, 4.45))
    lanes = [("ALLOW: low risk, no checks", 6.55, 7.4, GREEN, []), ("WARN: owner may be tricked; give a chance to stop", 3.8, 6.3, AMBER, [1, 2, 3, 4, 10, 11]),
             ("PROVE: someone else may be paying; owner proves it", 1.2, 3.55, TEAL, [5, 6, 7, 8, 9, 11]), ("BLOCK: too risky for any check", 0.05, 0.95, RED, [])]
    lx, lw = 5.5, 5.2
    for name, y0, y1, col, chips in lanes:
        rbox(ax, lx, y0, lw, y1 - y0, col + "1f", ec=col, lw=1.1)
        ax.text(lx + 0.12, y1 - 0.1, name, fontsize=5.8, fontweight="bold", color=col, va="top")
        arrow(ax, (5.05, 4.45), (lx, (y0 + y1) / 2), color=col)
        for i, n in enumerate(chips):
            m = METHODS[ORDER[n - 1]]
            cx = lx + 0.12 + (i % 2) * 2.5
            cy = y1 - 0.62 - 0.62 * (i // 2) - 0.45
            built = m.implemented
            rbox(ax, cx, cy + 0.08, 2.4, 0.47, col if built else "white", ec=col, ls="-" if built else (0, (3, 2)), lw=1.2, r=0.05)
            ax.text(cx + 0.1, cy + 0.315, f"{n}  {SHORT[n - 1]}", va="center", fontsize=6, color="white" if built else col, fontweight="bold")
    ax.text(lx + 0.12, 6.9, "straight to the PIN pad", fontsize=6.4, color=GREEN, va="center")
    ax.text(lx + 0.12, 0.38, "short plain message and helpline 1930", fontsize=6.4, color=RED, va="center")
    rbox(ax, 11.1, 3.5, 1.2, 1.9, NAVY); ax.text(11.7, 5.2, "PIN pad", ha="center", va="top", color="white", fontsize=7.4, fontweight="bold")
    ax.text(11.7, 4.6, "money\nmoves".replace("\\n", "\n"), ha="center", va="top", color="white", fontsize=6)
    arrow(ax, (10.6, 6.95), (11.5, 5.4), color=GREEN); arrow(ax, (10.6, 5.0), (11.3, 4.8), color=AMBER); arrow(ax, (10.6, 2.4), (11.4, 3.5), color=TEAL)
    rbox(ax, 0.05, 0.1, 0.28, 0.22, "#444444"); ax.text(0.42, 0.21, "filled = built", fontsize=6, va="center")
    rbox(ax, 1.9, 0.1, 0.28, 0.22, "white", ec="#444444", ls=(0, (3, 2))); ax.text(2.27, 0.21, "dashed = designed, not built", fontsize=6, va="center")
    f.savefig(OUT / "fig_journey.png", dpi=200, bbox_inches="tight"); plt.close(f)


def fig_map():
    f, ax = plt.subplots(figsize=(7.4, 4.6)); ax.set_xlim(0, 10); ax.set_ylim(0, 12.4); ax.axis("off")
    heads = ["Takeover\n(thief pays)", "Coached\n(owner tricked)", "Bad payee", "Genuine\ncustomer passes"]
    xs = [4.3, 5.7, 7.1, 8.5]
    ax.text(0.1, 11.9, "Check", fontsize=7.5, fontweight="bold"); ax.text(3.15, 11.9, "Time", fontsize=7.5, fontweight="bold")
    for x, h in zip(xs, heads):
        ax.text(x + 0.6, 12.1, h, ha="center", va="center", fontsize=6.8, fontweight="bold")
    ax.text(5.35, 11.35, "chance a scam of that kind gets PAST the check (lower is better; assumed)", ha="center", fontsize=6, color=GREY, style="italic")
    for i, k in enumerate(ORDER):
        m = METHODS[k]; y = 10.5 - i * 0.93
        ax.add_patch(Rectangle((0, y - 0.38), 10, 0.86, fc=LIGHT if i % 2 == 0 else "white", ec="none"))
        ax.text(0.1, y, f"{i + 1}  {SHORT[i]}" + ("" if m.implemented else "  (planned)"), va="center", fontsize=6.6, fontweight="bold" if m.implemented else "normal", color="black" if m.implemented else "#555555")
        ax.text(3.15, y, f"{m.seconds:g} s" if m.seconds < 60 else f"{m.seconds / 60:g} min", va="center", fontsize=6.6)
        for x, fam in zip(xs[:3], ("UNAUTHORISED", "COACHED", "PAYEE_RISK")):
            v = m.pass_by_family[fam]
            col = GREEN if v <= 0.15 else ("#d9a400" if v <= 0.6 else RED)
            ax.add_patch(FancyBboxPatch((x, y - 0.3), 1.2, 0.62, boxstyle="round,pad=0.01,rounding_size=0.05", fc=col, ec=col))
            ax.text(x + 0.6, y, f"{v:.0%}", ha="center", va="center", fontsize=6.6, color="white", fontweight="bold")
        ax.text(xs[3] + 0.6, y, f"{m.pass_genuine:.0%}", ha="center", va="center", fontsize=6.6)
    f.savefig(OUT / "fig_map.png", dpi=200, bbox_inches="tight"); plt.close(f)


def phone(ax, x, title, lines, buttons=(), accent=TEAL, big=None, w=2.2, h=4.4, y=0.1, label=None):
    rbox(ax, x, y, w, h, "white", ec="#333333", lw=1.4, r=0.18)
    ax.add_patch(Rectangle((x + 0.06, y + h - 0.7), w - 0.12, 0.64, fc=accent, ec="none"))
    ax.text(x + w / 2, y + h - 0.38, title, ha="center", va="center", color="white", fontsize=6.6, fontweight="bold")
    yy = y + h - 1.0
    for ln in lines:
        for part in textwrap.wrap(ln, 27):
            ax.text(x + 0.17, yy, part, va="top", fontsize=6.1, color="#222222"); yy -= 0.26
        yy -= 0.1
    if big:
        ax.text(x + w / 2, yy - 0.35, big, ha="center", va="center", fontsize=16, fontweight="bold", color=accent)
    by = y + 0.18 + 0.52 * (len(buttons) - 1)
    for t, c in buttons:
        rbox(ax, x + 0.2, by, w - 0.4, 0.42, c, r=0.05)
        ax.text(x + w / 2, by + 0.21, t, ha="center", va="center", fontsize=6.2, color="white", fontweight="bold"); by -= 0.52
    if label:
        ax.text(x + w / 2, y - 0.12, label, ha="center", va="top", fontsize=6, color=GREY)


def mock(name, phones_spec, width):
    n = len(phones_spec)
    f, ax = plt.subplots(figsize=(2.45 * n, 4.9)); ax.set_xlim(0, 2.45 * n); ax.set_ylim(-0.3, 4.6); ax.axis("off")
    for i, spec in enumerate(phones_spec):
        phone(ax, 0.12 + i * 2.45, **spec)
    f.savefig(OUT / f"mock_{name}.png", dpi=170, bbox_inches="tight"); plt.close(f)


def mockups():
    mock("second_device", [dict(title="Paying phone", lines=["Rs 80,000 to Verification Cell", "Approve on your other device. Match this number:"], big="47", accent=NAVY, buttons=[("Cancel payment", GREY)], label="the phone making the payment"),
                           dict(title="Your other device", lines=["Approve this payment?", "Rs 80,000 to Verification Cell (new account)", "Pick the number shown on the paying phone"], big="12   47   83", accent=TEAL, buttons=[("Deny, this is not me", RED)], label="the approver (a second phone, tablet or browser tab)")], 5)
    mock("face", [dict(title="Confirm it is you", lines=["Rs 20,000 to Priya Sharma", "Look at the camera, then turn your head to the left"], big="(face)", accent=TEAL, buttons=[("Cancel", GREY)], label="live face check with a random head turn")], 3)
    mock("device_key", [dict(title="Confirm payment", lines=["Rs 20,000 to Priya Sharma", "Touch the fingerprint sensor to sign this exact payment"], big="[fingerprint]", accent=NAVY, buttons=[("Cancel", GREY)], label="the phone signs payee + amount with its own key")], 3)
    mock("totp", [dict(title="Enter your code", lines=["Open your authenticator app and type the 6-digit code for DemoPay"], big="4 7 2 _ _ _", accent=TEAL, buttons=[("Confirm", TEAL)], label="code changes every 30 seconds")], 3)
    mock("passkey", [dict(title="Use your passkey", lines=["DemoPay wants to confirm this payment", "Rs 20,000 to Priya Sharma", "Use your screen lock to continue"], big="[key]", accent=NAVY, buttons=[("Continue", NAVY), ("Cancel", GREY)], label="system passkey sheet")], 3)
    mock("contact", [dict(title="Ramesh's phone", lines=["Rs 2,40,000 to a new account", "We asked your trusted contact, Meera, to confirm.", "The payment stays on hold until she answers."], big="03:12", accent=NAVY, buttons=[("Cancel payment", GREY)], label="waiting"),
                     dict(title="Meera's phone", lines=["Ramesh wants to pay Rs 2,40,000 to an account opened 3 days ago.", "Do you know why?"], accent=AMBER, buttons=[("Approve", GREEN), ("Decline and call him", RED)], label="the trusted contact sees only this payment")], 5)
    mock("callback", [dict(title="We are calling you", lines=["For your safety we are calling your registered number.", "Pick up and confirm this payment.", "We never ask for your PIN, OTP or to move money."], big="(ringing)", accent=RED, buttons=[("Cancel payment", GREY)], label="the bank calls the number on record")], 3)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    fig_journey(); fig_map(); mockups(); print("ok")
