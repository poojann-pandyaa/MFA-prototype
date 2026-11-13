"""
Builds docs/demopay-implementation.pdf: how the DemoPay prototype works (risk model, decision engine, authenticators, scenarios).

    cd backend && .venv/bin/python -m tools.build_demopay_diagrams && .venv/bin/python -m tools.build_demopay_pdf
Figures come from build_demopay_diagrams; screenshots and scenario_matrix.json are in docs/demopay-assets/.
"""
import json
import sys
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "docs" / "demopay-assets"
OUT = ROOT / "docs" / "demopay-implementation.pdf"
sys.path.insert(0, str(ROOT / "demopay" / "backend"))
from authn import policy  # noqa: E402
from authn.registry import METHODS  # noqa: E402

from tools.pdfkit import *  # noqa: F401,F403,E402
from tools.pdfkit import _card  # noqa: E402
footer = make_footer("DemoPay implementation guide | all data, scams and money are simulated; pass rates and costs are assumptions")

# ---------------------------------------------------------------------------------------------------------------- data
M = json.load(open(A / "scenario_matrix.json"))
LOOK = {(o["scenario"], o["customer"], o["device"]): o for o in M}
CUST = ["asha", "ramesh", "kabir", "meera"]
ABBR = {"warning": "W", "typed_confirm": "T", "payee_check": "P", "cooling_off": "C"}
SCEN = [  # name, payee, amount, channel, phone signals, what it imitates
    ("Routine payment", "priya.sharma@okaxis", "Rs 300", "pay", "none", "A normal payment to a friend. The model should stay quiet."),
    ("New local shop", "bakery.joy@ybl", "Rs 450", "pay", "none", "A genuine shop that is new on the platform (45 days old). Tests that newness alone does not cause friction."),
    ("Fake refund", "refund.desk@ybl", "Rs 9,000", "collect", "on a call 6 min, unknown number", "A fake customer-care 'refund': a collect request from a 2-day-old account that already has 2 complaints."),
    ("Digital arrest", "officer.verify@upi", "Rs 20,000", "pay", "on a call 25 min, unknown number", "A fake police officer keeps the victim on a long call and demands money to a new, unreported account."),
    ("Digital arrest (large)", "officer.verify@upi", "Rs 60,000", "pay", "same long call", "Same call, three times the amount."),
    ("Investment scam", "invest.profit99@ybl", "Rs 20,000", "pay", "none", "A new 'trading' account (3 days old, risky category) with no call."),
    ("Look-alike payee", "priya.sharrna@okaxis", "Rs 4,000", "pay", "none; scam rarity set to 1 in 1,000", "An id one letter off a friend's (sharma / sharrna), created 3 days ago."),
    ("Fake seller", "quickseller.deals@ybl", "Rs 6,000", "qr", "none", "An ordinary-looking 20-day-old seller account, paid by scanning a QR. Deliberately hard."),
    ("Prize fee", "lucky.draw.prize@ybl", "Rs 3,000", "pay", "none", "A small 'fee' to claim a prize, to a 30-day-old account. Deliberately hard."),
    ("SIM swap + new phone", "unknown.account@ybl", "Rs 60,000", "pay", "SIM changed 2 h ago, new phone 1 h ago, PIN reset, touch pattern unlike owner", "A thief who swapped the SIM and set up UPI on a new phone, paying an account the platform has never seen."),
    ("Remote control", "quickseller.deals@ybl", "Rs 30,000", "pay", "screen-share running, touch pattern unlike owner", "A remote-access app lets a fraudster drive the owner's phone."),
]


def cell_text(o):
    if o is None:
        return "n/a"
    if o["decision"] == "allow":
        return "Allow"
    if o["decision"] == "block":
        return "Block"
    return "+".join(ABBR[m] for m in o["steps"])


def matrix_table():
    head = [P("Scenario", HEADC)]
    for c in CUST:
        head += [P(f"{c.title()}<br/>usual phone", HEADCC), P(f"{c.title()}<br/>new phone", HEADCC)]
    rows, style = [head], []
    for r, (name, *_rest) in enumerate(SCEN, start=1):
        row = [P(name, CELLB)]
        for c_i, c in enumerate(CUST):
            for d_i, dev in enumerate(("web-device", "web-new1")):
                o = LOOK.get((name, c, dev))
                row.append(P(cell_text(o), CELLC))
                col = 1 + 2 * c_i + d_i
                bg = "#e5e9ee" if o is None else {"allow": "#dff1e5", "block": "#f6d5d5"}.get(o["decision"], "#fff1cf")
                style.append(("BACKGROUND", (col, r), (col, r), colors.HexColor(bg)))
        rows.append(row)
    t = Table(rows, colWidths=[W * 0.236] + [W * 0.0955] * 8, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, GRID), ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)] + style))
    return t


def rarity_table():
    """Same payment, scam rarity changes: what the engine does (computed here from the real decision engine)."""
    rows = [["Model severity (Rs 20,000, tricked owner)", "1 in 1,00,000 (reported UPI rate)", "1 in 10,000 (demo default)", "1 in 1,000 (high-risk channel)"]]
    for sev in ("MEDIUM", "HIGH", "CRITICAL"):
        r = [sev]
        for prior in (1e-5, 1e-4, 1e-3):
            pl = policy.make_plan(sev, "COACHED", 20000.0, [], prior, policy.DEFAULT_TIME_BUDGET, 50000.0)
            r.append({"allow": "Allow", "block": "Block"}.get(pl.decision) or "Steps: " + "+".join(ABBR[s.method] for s in pl.steps))
        rows.append(r)
    return rows


def senior_table():
    rows = [["Customer", "Slow steps allowed from", "Plan for the same HIGH, tricked-owner payment of Rs 20,000"]]
    for label, slow in (("Under 60 (Asha, Kabir, Meera)", 50000.0), ("60 or over (Ramesh)", 10000.0)):
        pl = policy.make_plan("HIGH", "COACHED", 20000.0, [], 1e-4, policy.DEFAULT_TIME_BUDGET, slow)
        rows.append([label, f"Rs {slow:,.0f}", "Block" if pl.decision == "block" else "+".join(ABBR[s.method] for s in pl.steps)])
    return rows


story = []
# ============================================================================================================= cover
story += [Spacer(1, 38 * mm), P("DemoPay", COVT), P("How the adaptive payment-protection prototype is built and what it does", COVS),
          P("The risk model, the decision engine, the four safety checks, every scenario we ran, and what the results do and do not show. "
            "Written in plain language, with screenshots of the web and Android apps.", ONE), Spacer(1, 8 * mm),
          P("<b>Read this first.</b> Everything here runs on invented data: invented customers, invented payees, simulated money and simulated phone signals. "
            "No real fraud data was used because none is public. The numbers show that the pipeline works and how it behaves; they are not accuracy claims about real "
            "UPI traffic, and this prototype does not stop every scam. Where we know the model is weak, this document says so.", NOTE),
          Spacer(1, 10 * mm),
          table([["What is in this document"],
                 ["1. The idea in one page, and the three layers"], ["2. The playground: four registered customers"], ["3. Life of one payment"],
                 ["4. Layer 1: the risk model"], ["5. Layer 2: the decision engine"], ["6. Layer 3: the four authenticators (and the planned ones)"],
                 ["7. Every scenario and what happened"], ["8. Playing with it: switches, dials and a surprising finding"], ["9. The Android app"],
                 ["10. Security design, honest limits, next steps, glossary"]], [1.0]),
          Spacer(1, 12 * mm), P("Prepared 6 November 2026 | Poojan Pandya | prototype, not a product", SMALL)]

# ============================================================================================================= 1 idea
story += [PageBreak(), P("1. The idea in one page", H1),
          P("Pay a friend Rs 300 and nothing should get in your way. Pay Rs 20,000 to a three-day-old account while a stranger on the phone says you will be arrested, "
            "and something should. The hard part is doing that for millions of payments without annoying the many genuine ones.", ONE),
          P("What goes wrong in real life (three kinds of scam)", H2)]
story += bullets(["<b>Takeover (someone else is paying).</b> A thief has your SIM, your phone, or remote control of it. The person tapping is not you.",
                  "<b>Coached (you are paying, but you are being tricked).</b> Fake police, fake customer care, fake investment, fake prize. You do everything yourself, "
                  "so a PIN or fingerprint cannot tell it apart from a genuine payment.",
                  "<b>Bad payee (the receiving account is the problem).</b> A look-alike of a friend's id, a mule account, a fake seller."])
story += [P("What this prototype does about it", H2)]
story += bullets(["<b>Score every payment by how unusual it is</b> for this customer, this payee and this phone (the risk model).",
                  "<b>Choose the cheapest set of checks</b> that makes the doubt small enough, within a limit on how much of the customer's time we may use (the decision engine).",
                  "<b>Run those checks</b> one at a time, on the server, so the app cannot skip them (the authenticators).",
                  "Genuine, routine payments pass with no extra steps at all."])
story += [fig("fig_system.png", "Figure 1. The three layers. The app talks only to the API; the API asks the model, then the engine, then runs the chosen checks."),
          P("What DemoPay is, and is not", H2),
          table([["It is", "It is not"],
                 ["A playground: a web app and an Android app on one backend, with four ready-made customers whose history you can use, reset and test against.",
                  "A bank. There is no sign-up, no KYC, no real money, no real PIN. The UPI PIN pad accepts any 4 to 6 digits and stores nothing."],
                 ["A working end-to-end flow: score, plan, challenge, PIN, money moves, all enforced by the server.",
                  "A measured result. The model is trained on a simulator we wrote, the pass rates of the checks are our assumptions, and nothing has run on real traffic."],
                 ["A way to test ideas: switch checks on or off, change how rare scams are, send eleven scripted scams, read the model's reasons.",
                  "Proof that it 'prevents all scams'. It does not, and Section 7 lists the cases it misses."]], [0.5, 0.5])]

# ============================================================================================================= 2 playground
story += [PageBreak(), P("2. The playground: four registered customers", H1),
          P("You asked for ready-made users with a history of transactions to play with, not a bank with sign-up. On first start the API creates four invented customers. "
            "Each has a profile, an account number and a payment history shaped like their story. They sign in with one shared demo password shown on the sign-in screen.", ONE),
          table([["Customer", "Profile", "History", "Balance"],
                 ["<b>asha</b>: Asha Menon, 34, Bengaluru", "Software engineer. Busy professional, pays often, mostly by day.", "70 payments over 60 days, median about Rs 400, friends, shops, bills", "Rs 60,000"],
                 ["<b>ramesh</b>: Ramesh Iyer, 68, Pune", "Retired teacher. Pays rarely and small amounts.", "14 payments, median about Rs 250, family, kirana store, electricity", "Rs 1,50,000"],
                 ["<b>kabir</b>: Kabir Khan, 29, Mumbai", "Freelance designer. Pays late at night.", "50 payments, median about Rs 600, mostly 10pm to 2am", "Rs 80,000"],
                 ["<b>meera</b>: Meera Nair, 22, Kochi", "Student, new to digital payments.", "3 payments only: almost no history to compare against", "Rs 40,000"]], [0.24, 0.27, 0.36, 0.13]),
          Spacer(1, 3 * mm),
          row_cards([("web_login.jpg", "Sign-in in demo mode: pick a customer with one tap."), ("web_home_asha.jpg", "A customer's home: profile, balance, switches for their safety checks, and payment history (newest first).")], width=W * 0.22),
          P("What you can do here", H2)]
story += bullets(["<b>Send a scripted scam</b> (11 scenarios, Section 7) or any payment you type, then read the model's reasons in the X-ray panel.",
                  "<b>Switch a safety check off</b> for this customer and send the same payment again. The engine rebuilds its plan from what is left.",
                  "<b>Press Reset</b> to give the customer their original history and balance back.",
                  "<b>Set what the phone reports</b> (on a call, remote control, SIM swapped, new phone) to imitate an attack, because the browser cannot sense these. On the Android app a real call really does change the signal."])
story += [P("What the profile is used for", H2),
          P("Today only the <b>age</b> is used, by the decision engine: customers aged 60 or over can be given slow checks from a lower amount (Rs 10,000 instead of Rs 50,000), because older customers are "
            "targeted more often by coaching scams. That is an assumption, not a measured fact. The risk model itself does not read any profile field; it works from payment history, payee facts and phone signals.", WHYB)]

# ============================================================================================================= 3 life of a payment
story += [PageBreak(), P("3. Life of one payment", H1),
          P("Every payment goes through the same seven moments. The app never decides anything; it shows what the server says, and the server refuses to complete a payment whose checks have not passed.", ONE),
          fig("fig_lifecycle.png", "Figure 2. The path of a payment. The three results at the right are the only outcomes of the decision engine.", width=W * 0.8),
          P("Step by step", H2),
          table([["#", "What happens", "Where in the code (plain names)"],
                 ["1", "The customer enters a payee and an amount, picks pay, collect request or scanned QR. In demo mode the presenter also sets what the phone reports.", "Send screen -> POST /pay/risk"],
                 ["2", "The server replays the customer's last 90 days of completed payments into the same rolling profile the model was trained with (typical amount, usual hours, known devices). It looks the payee up in the fake bank directory (account age, how many senders in 24 h, government fraud-risk band, complaints).", "risk.py: build_state, assess"],
                 ["3", "The model scores three 'surprise' numbers (takeover, coached, bad payee) and turns them into a severity: LOW, MEDIUM, HIGH or CRITICAL, plus the most likely threat family.", "ato/model.py"],
                 ["4", "The decision engine turns severity into a chance of scam, prices every option, and returns the cheapest plan: allow, block, or one to three checks.", "authn/policy.py"],
                 ["5", "The checks run one at a time. Each check opens a challenge tied to this payment, and the server judges the answer.", "authn/challenge.py, methods.py"],
                 ["6", "When every check has passed (or none were needed) the payment becomes 'approved' and the PIN pad appears.", "pay.py: step_answer"],
                 ["7", "The PIN step re-checks that the payment is approved and that the balance covers it, then moves the money. The PIN itself is not verified in this demo.", "pay.py: complete"]], [0.05, 0.65, 0.30]),
          P("A payment's status", H2),
          table([["Status", "Meaning"],
                 ["pending", "Checks are waiting to be done. The app is on step N of M."],
                 ["approved", "No checks needed, or all passed. Ready for the PIN."],
                 ["completed", "Money moved. Only reachable from approved."],
                 ["blocked", "The engine said block, a check ended in a danger answer or wrong tries, or a challenge expired."],
                 ["cancelled", "The customer pressed Cancel. Allowed at any point before the money moves."]], [0.18, 0.82]),
          P("A modified app or a script cannot reach 'completed' by skipping steps: asking to complete an unapproved payment is refused with 'finish the required steps first'.", SMALL)]

# ============================================================================================================= 4 model
story += [PageBreak(), P("4. Layer 1: the risk model", H1),
          P("The model does not try to recognise a scam by its story. It measures how <b>surprising</b> a payment is compared with ordinary, genuine payments, using three specialist detectors.", ONE),
          P("What the model looks at", H2),
          table([["Group", "Examples (plain words)", "Where it comes from"],
                 ["Amount and timing", "How big compared with this customer's usual; does it break their largest or 95th-percentile payment; the hour of day", "The payment, plus the replayed history"],
                 ["History and device", "How many past payments; has this phone been used before; how old is it to us", "Server's own records"],
                 ["Payee facts", "Account age; distinct senders in 24 hours; merchant or not; risky category; government fraud-risk band; complaints; whether it is a brand-new unseen account", "The bank directory (a real bank gets this from its network and the national databases)"],
                 ["Identity facts", "Hours since the phone was bound to UPI; whether an old phone is still active; hours since the SIM changed; hours since the PIN was reset", "Telecom and UPI records"],
                 ["Phone signals (attested)", "Remote-access app running; touch pattern unlike the owner; integrity failure; overlay or accessibility abuse; seconds from login to pay; on a call, how long, unknown number; requester is a saved contact", "A trusted on-device SDK. In the demo the presenter sets them; on Android the call state is real."]], [0.2, 0.5, 0.3]),
          P("In total the model reads 46 numbers per payment. They split into <i>server</i> facts and <i>device</i> facts. If a fraudster controls the phone they can lie about device facts, so a second model, <b>server-only</b>, never uses them. "
            "The app uses the full model when the phone is trusted and the server-only model when it is not (a switch in the demo).", BODY),
          P("The three detectors and what 'surprise' means", H2)]
story += bullets(["<b>Takeover detector.</b> Is this unlike the owner? New phone, SIM just swapped, odd touch pattern, unusual hour or amount.",
                  "<b>Coached detector.</b> Is the owner being talked into it? Long call with an unknown number, a collect request from a stranger, a large payment to a new account.",
                  "<b>Bad-payee detector.</b> Is the receiver suspicious? Very new account, many senders at once, reported, a look-alike of a known contact."])
story += [P("Each detector produces a score. Scores are compared with how genuine payments scored, and the result is a <b>surprise</b> number: higher means rarer among genuine payments. "
            "Example from Figure 4: a takeover surprise of 6.3, coached 7.9 and bad-payee 6.8 means all three are rarer than almost every genuine payment.", BODY),
          fig("fig_ladder.png", "Figure 3. Severity levels. Cut-offs are set on genuine payments, so friction for genuine customers has a fixed, small budget by design.", width=W * 0.92),
          P("Why 'surprise' and not 'probability of fraud'? Fraud is so rare in real life (about 1 in 100,000 UPI payments is reported) that a probability from a model trained on simulated, much more fraud-heavy data would be wrong. "
            "Surprise, measured against genuine payments only, is stable: the share of genuine payments that get friction does not depend on how much fraud there is. The decision engine later adds the real-world rarity back in (Section 5).", WHYB)]
story += [PageBreak(), P("Reading the model X-ray", H2),
          P("In demo mode the right-hand panel shows exactly what the model saw. A real app never shows this to the customer, because it would teach an attacker how to stay under the thresholds.", BODY),
          fig(crop("web_step_typed.jpg", "all"), "Figure 4. X-ray for Ramesh's 'Digital arrest' scenario. Top: severity HIGH, threat COACHED. Middle: the three detectors with tick marks for MEDIUM, HIGH and CRITICAL. "
               "Below: the engine's plan, the three biggest reasons (payee age +6.52, call minutes +2.99, amount vs usual +1.39) and the exact inputs sent.", width=W * 0.56),
          P("What the model learned, and how well it did on simulated data", H2),
          P("The model was trained on a payment simulator we wrote (<font face='Courier'>ato/simulate.py</font>): 150 days of genuine customers, with 14 scam types injected (4 takeover, 8 coached, 2 bad-payee). "
            "Test slice: 272,875 payments and 347 scam incidents the model never saw. Detectors are gradient-boosted trees; a small router names the threat family.", BODY),
          table([["Result at HIGH (strong action)", "Full model", "Server-only model", "Hand-written rules"],
                 ["Scam incidents caught", "89.6%", "89.3%", "33.4%"],
                 ["Genuine payments flagged at HIGH", "0.2%", "0.2%", "0.2%"],
                 ["Rupees saved", "95.5%", "97.2%", "51.7%"],
                 ["Caught on the very first scam payment", "84.7%", "84.4%", "23.3%"]], [0.4, 0.2, 0.22, 0.18], first_bold=True),
          Spacer(1, 2 * mm),
          table([["Family (full model, HIGH)", "Incidents", "Caught"], ["Takeover (SIM swap, APK binding, remote access, theft)", "81", "100.0%"],
                 ["Coached (digital arrest, fake care, collect/QR, investment, prize, romance ...)", "217", "91.7%"], ["Bad payee (look-alike, fake seller)", "49", "63.3%"]], [0.7, 0.14, 0.16]),
          P("The two weakest scam types in this test are <b>fake seller</b> (50% caught at HIGH) and <b>look-alike</b> (74%): both can use an account that looks ordinary. "
            "Takeover is easiest (every incident caught) because a thief's behaviour differs from the owner's in many ways at once.", BODY)]
story += [P("What these numbers do and do not mean", H2)]
story += bullets(["We wrote both the scams and the detector, so the results partly confirm our own assumptions. Per-type rows rest on a few dozen incidents.",
                  "Fraud is much more common in the simulation (0.4% of payments) than in real life. At a real rate of 1 in 100,000, about 0.5% of HIGH alerts would be real fraud; the engine's cheap, mostly-warning plans exist for that reason.",
                  "When payee information is missing, takeover detection holds (100%) but overall catch drops to about 66%: payee intelligence is the most valuable input. Genuine friction is uneven: a customer who just changed phone gets MEDIUM or higher on 18% of payments, a routine payer on 0.6%."])

# ============================================================================================================= 5 engine
story += [PageBreak(), P("5. Layer 2: the decision engine", H1),
          P("The model says how unusual a payment is. The engine decides what to <i>do</i>: let it through, stop it, or ask for some checks. It does this with arithmetic you can read, not a second black box.", ONE),
          P("Step A: from severity to a chance of scam", H2),
          P("In the simulation, a MEDIUM payment was 5.7 times more common among scams than among genuine payments, HIGH was 107.2 times and CRITICAL 6,847.8 times (the 'likelihood ratios'). "
            "Multiply those by how rare scams really are (the 'base rate') and you get a chance that this payment is a scam.", BODY),
          table([["Worked example (the X-ray in Figure 4)", "Number"],
                 ["Base rate used by the demo: 1 scam in 10,000 payments", "0.0001"],
                 ["Odds before looking at the payment", "0.0001 / 0.9999 = 0.0001"],
                 ["Model said HIGH, whose ratio is 107.2", "odds become 0.0001 x 107.2 = 0.01072"],
                 ["Chance this payment is a scam", "0.01072 / 1.01072 = <b>1.061%</b>"]], [0.62, 0.38], first_bold=True),
          P("The base rate is a dial in the demo (1 in 1,00,000, 1 in 10,000, 1 in 1,000). The model never changes; only how rare we believe scams are changes, which is exactly what a bank would tune.", SMALL),
          P("Step B: price every option", H2),
          P("The engine tries: allow, block, and every sequence of one to three of the available checks. For each it adds up three costs, all in rupee-equivalents:", BODY)]
story += bullets(["<b>Friction</b> of each step the customer has to do (annoyance, time).",
                  "<b>Scam leaks:</b> the chance a scam gets past all the steps, times the amount lost.",
                  "<b>Genuine customers stopped:</b> the chance a genuine customer fails or gives up, times the cost of wrongly stopping them (assumed Rs 100 plus 0.5% of the amount)."])
story += [fig("fig_costs.png", "Figure 5. The same payment priced every way: Ramesh, Rs 20,000, HIGH, tricked owner. Allowing costs about Rs 212 of expected loss; blocking about Rs 198 of wrongly stopped genuine customers; the best 3-step plan only Rs 96.", width=W * 0.74),
          P("So here the engine neither allows nor blocks: it asks for three checks, because a small amount of friction is far cheaper than either mistake. That is the whole idea of 'adaptive' protection.", WHYB),
          P("Step C: the rules around it", H2),
          table([["Rule", "What it does", "Why"],
                 ["User-time budget", "MEDIUM: at most 10 seconds of fast steps. HIGH: 30 seconds. CRITICAL: no limit.", "Bound how much any customer can be delayed; the budget grows only when the risk does."],
                 ["Slow steps are rare", "The cooling-off wait is allowed only at CRITICAL, or at HIGH when the owner looks tricked and the amount is Rs 50,000 or more (Rs 10,000 for customers 60 or over).", "A 30-minute wait is a heavy cost; keep it for cases where time itself is the defence."],
                 ["At most 3 steps", "No plan longer than three steps.", "Beyond that, customers abandon or find workarounds."],
                 ["Block cost", "Rs 100 plus 0.5% of the amount.", "Assumed. Blocking a genuine large payment hurts more than a small one."],
                 ["Skip what cannot work", "'Type the registered name' is removed when the bank has no registered name for the payee.", "A check that cannot be passed would block genuine payments."],
                 ["Switched-off checks", "A check the customer (or presenter) has switched off is not offered.", "Lets you test the engine's choices."]], [0.2, 0.45, 0.35]),
          P("Doubt after each step", H2),
          P("The engine also reports the remaining chance of scam after each step, assuming the customer passes it. For the example above: 1.061% before any step, 0.898% after typing the name, 0.366% after the wait, 0.248% after the warning. "
            "These come from the assumed pass rates in Section 6, so they are estimates of the plan's effect, not measurements.", BODY)]

story += [PageBreak(), P("What changes the engine's answer", H2),
          P("Same model severity, different world. The table is computed by the real engine for a tricked owner paying Rs 20,000 with all four checks available.", BODY),
          table(rarity_table(), [0.34, 0.22, 0.22, 0.22], first_bold=True),
          P("When scams are very rare (1 in 100,000), even a CRITICAL payment is better handled by steps than by blocking, because most CRITICAL payments are then genuine. "
            "When scams are common (1 in 1,000) the same payment is blocked. Abbreviations: W warning, T typed name, P payee check, C cooling-off wait.", BODY),
          P("The age rule", H2), table(senior_table(), [0.3, 0.2, 0.5], first_bold=True),
          P("With slow steps allowed from a lower amount, the older customer gets the cooling-off wait; a younger customer in the same position gets only fast steps. Both are tricked-owner cases. This is an assumption we made, labelled as one.", BODY),
          P("Why not just ask for everything every time?", H2)]
story += bullets(["Most payments are genuine. Even a 1% extra friction on routine payments is a lot of annoyed customers, and customers who are always challenged stop reading the challenges.",
                  "Some checks only help against some scams. A warning cannot stop a thief (a thief clicks through it), but it can stop a tricked owner. The engine uses the threat family to pick useful checks.",
                  "Not every customer has every check available. The engine picks from what exists."])

# ============================================================================================================= 6 authenticators
story += [PageBreak(), P("6. Layer 3: the safety checks (authenticators)", H1),
          P("A check is any step that helps prove a payment is genuine or gives a tricked owner a chance to stop. DemoPay has four built and working. All four work for every customer with no enrolment, and none stores private data.", ONE),
          table([["Check", "Time", "Answers which doubt", "Chance a scam gets past it (takeover / coached / bad payee)*", "Genuine customer passes"],
                 [f"<b>{METHODS['warning'].name}</b>", "about 8 s", "Is the owner being tricked?", "100% / 60% / 90%", "100%"],
                 [f"<b>{METHODS['typed_confirm'].name}</b>", "about 8 s", "Is this the right payee?", "98% / 85% / 50%", "98%"],
                 [f"<b>{METHODS['payee_check'].name}</b>", "about 6 s", "Is this the right payee?", "90% / 90% / 30%", "97%"],
                 [f"<b>{METHODS['cooling_off'].name}</b>", "30 min (20 s in the demo)", "Is the owner being rushed?", "40% / 35% / 50%", "90%"]], [0.24, 0.12, 0.2, 0.28, 0.16], extra=[]),
          P("* These are assumptions made in <font face='Courier'>authn/registry.py</font>, not measurements. They set which check the engine prefers; replace them with logged outcomes once real traffic exists. "
            "Note the pattern: none of these four stops a <i>takeover</i> well (a thief passes them), because they test intent and payee, not identity. Identity checks are planned (see the end of this section).", SMALL)]

# 6.1 warning
story += [KeepTogether([P("6.1 Scam warning and questions", H2),
          P("A short, plain warning that matches the situation, two random questions with a 'danger' answer, and a forced pause so it cannot be tapped through.", ONE),
          side([("web_step_warning.jpg", "Step 3 of 3 in Ramesh's plan: warning, two random questions, a read-timer on the button."),
                ("web_blocked_answer.jpg", "A 'Yes' to a danger question ends the payment with the helpline.")],
               bullets(["The wording follows the threat family: a collect request ('this request will SEND money'), a tricked owner, unusual activity, or a risky payee.",
                        "Two questions are drawn at random from four: call or chat telling you to pay; told to keep it secret; told you will be arrested or blocked; promised a prize, refund or profit if you pay first.",
                        "'Continue' waits 4 seconds and needs the 'I have read' box ticked. The server checks the time too, so a script cannot answer instantly.",
                        "Any 'Yes' to a danger question stops the payment. Honest 'No' answers pass."]) +
               [P("<b>Why it can work:</b> a 'digital arrest' victim is told to keep it secret and act now; hearing the same sentences from the app, in plain words, can break the script. "
                  "<b>Why it can fail:</b> a victim who answers 'No' because the scammer said so is not stopped. We assume 60% get past it; the real number is unknown.", WHYB)])])]

# 6.2 typed
story += [KeepTogether([P("6.2 Type the payee's registered name", H2),
          P("The customer types the name the bank has on record for the account. It forces a look at who is really receiving the money.", ONE),
          side([("web_step_typed.jpg", "Web: the payee id is shown, the name is not."), ("android_typed_wrong.jpg", "Android: a wrong name counts one of three tries.")],
               bullets(["Capital letters and extra spaces are ignored. Three wrong tries stops the payment.",
                        "If the bank has no registered name for the payee, this check is not offered (and if it were reached it fails safe), because it could never be passed."]) +
               [P("<b>Why it can work:</b> in a look-alike scam the customer expects 'Priya Sharma' and the record says 'P Sharma'; typing it makes the mismatch visible. "
                  "<b>Why it can fail:</b> a customer who copies the name from the scammer's message types it correctly.", WHYB)])])]

# 6.3 payee check
story += [KeepTogether([P("6.3 Payee check", H2),
          P("A one-tap confirmation that shows what the bank knows about the receiving account and asks 'Is this who you mean to pay?'", ONE),
          side([("web_step_payee.jpg", "Web: registered name, account age '3 day(s), very new'."), ("android_payee_check.jpg", "Android: the same step.")],
               bullets(["Shows the registered name, the account age, whether it has been reported and, for a look-alike id, which genuine id it resembles.",
                        "'No, stop' ends the payment ('You said this is not who you mean to pay'). 'Yes, continue' passes."]) +
               [P("<b>Why it can work:</b> it puts the most useful fact (this account is three days old) in front of the customer at the moment of decision, with no jargon. "
                  "<b>Why it can fail:</b> the account has to look suspicious already; an old, ordinary mule account shows nothing alarming.", WHYB)])])]

# 6.4 cooling off
story += [KeepTogether([P("6.4 Cooling-off wait", H2),
          P("The payment sits on a timer and the customer is told to stop and talk to someone. In real life 30 minutes; the demo shortens it to 20 seconds and says so on screen.", ONE),
          side([("web_step_cooling.jpg", "Step 2 of 3: countdown (00:19) and calm tips. The button becomes 'Continue' at 00:00.")],
               bullets(["The server stores the release time, so closing the app or changing the phone's clock does not shorten the wait.",
                        "Tips: stay on the line only if you started the call; no agency asks you to move money to a safe account; talk to someone you trust; call 1930.",
                        "It is the only slow check, so the engine uses it rarely: at CRITICAL, or at HIGH for a tricked owner paying a large amount (lower threshold for customers 60 or over)."]) +
               [P("<b>Why it can work:</b> scams run on urgency; a wait gives time to think, for someone to call, and for the bank's systems to notice. "
                  "<b>Why it can fail:</b> the customer waits and then pays anyway, or the scam does not depend on urgency.", WHYB)])])]

# challenge service
story += [PageBreak(), P("How every check is kept honest: the challenge service", H2),
          P("All four checks share one small service. A 'challenge' is one run of one check for one payment.", BODY),
          table([["Protection", "How it works"],
                 ["Tied to this payment", "Each challenge has a random one-time code (nonce) and a fingerprint of 'payee | amount | nonce'. An answer is accepted only with the matching code, so an answer for one payment cannot be replayed on another."],
                 ["Expires", "A challenge is valid for 10 minutes. After that it is closed and the payment must be restarted."],
                 ["Three tries", "After three wrong answers the challenge locks and the payment is stopped."],
                 ["Used once", "A passed or failed challenge cannot be answered again."],
                 ["Order is enforced", "Only the current step can be answered; asking for step 3 before step 1 is refused."],
                 ["Logs counts, not secrets", "Each challenge row records status, attempts, seconds taken and a short outcome word (for example 'scam_indicators'). No typed names or answers are stored beyond the challenge itself."],
                 ["Secrets stay on the server", "The questions' 'danger' answers, the registered name, and the hold release time never go to the app."]], [0.24, 0.76], first_bold=True),
          P("What is planned but not built", H2),
          P("The engine's list also includes seven identity checks. They are in the settings file so the Security page can show them, but the engine ignores them until they exist. "
            "They matter because the four built checks cannot defeat a thief: when the model says 'someone else is paying' (takeover), only an identity check can let the real owner prove it is them.", BODY),
          table([["Planned check", "Time", "What it adds", "Assumed chance a thief gets past it"],
                 ["Second-device approval", "15 s", "A second phone approves with a matching number", "3%"], ["Face + liveness", "20 s", "Live face check; 8.7% spoof pass, 12.7% genuine rejection (repo calibration)", "8.7%"],
                 ["Device key with payment signing", "1 s", "Phone signs the exact payment with a hardware-held key", "5%"], ["Authenticator-app code", "10 s", "6-digit rolling code", "10%"],
                 ["Passkey", "4 s", "Phishing-resistant sign-in", "5%"], ["Trusted-contact approval", "3 min (slow)", "A person you trust confirms; best against coaching", "5% thief / 25% coached"],
                 ["Bank call-back", "5 min (slow)", "The bank calls you", "2% thief / 30% coached"]], [0.27, 0.12, 0.43, 0.18]),
          P("Today these cases (SIM swap, remote control) end in a block, because nothing in the current set can prove identity. That is a real gap, and the next build step.", NOTE)]

# ============================================================================================================= 7 scenarios
story += [PageBreak(), P("7. Every scenario, and what happened", H1),
          P("Eleven scripted payments cover the scam families plus two genuine ones. Each fills the Send form with one click.", ONE),
          table([["Scenario", "Payee, amount, type", "Phone situation", "What it imitates"]] +
                [[f"<b>{n}</b>", f"{p}<br/>{a}, {c}", s, d] for n, p, a, c, s, d in SCEN], [0.15, 0.25, 0.2, 0.4])]

story += [PageBreak(), P("The results: eleven scenarios, four customers", H2),
          P("Each cell is what the real model and engine decided, run on 6 November 2026 against the live API with all four checks switched on and a scam rarity of 1 in 10,000 (the Look-alike row uses 1 in 1,000). "
            "'Usual phone' means a phone the customer's history was recorded on; 'new phone' means one the model has never seen. W warning, T typed name, P payee check, C cooling-off wait; steps are shown in the order the customer does them. "
            "n/a: Meera's balance (Rs 40,000) cannot cover Rs 60,000.", BODY),
          matrix_table(),
          P("Green: allowed with no steps. Amber: checks required. Red: blocked.", SMALL), Spacer(1, 2 * mm),
          P("Reading the results", H2)]
story += bullets(["<b>Genuine payments flow.</b> Routine and new-shop payments are allowed for everyone. One exception: Ramesh's payment to Priya was scored MEDIUM (he has never paid her), but the engine still allowed it because asking would cost more than the small doubt.",
                  "<b>Blatant scams are stopped.</b> The fake refund (a collect request from a 2-day-old reported account, on a call) is blocked outright for Asha, Ramesh and Kabir. Meera gets two checks instead: with only 3 past payments the model has little to compare against, so it is less sure.",
                  "<b>Digital arrest depends on who you are.</b> At Rs 20,000 Asha and Kabir are blocked, Ramesh gets three checks including the cooling-off wait (the age rule), Meera gets one warning. At Rs 60,000 Asha, Ramesh and Kabir are all blocked on their usual phone.",
                  "<b>Investment scam is uneven.</b> Asha and Ramesh get three checks, Kabir only a warning, and Meera is allowed with nothing. A brand-new account with no history is hard to call: an honest miss.",
                  "<b>Look-alike payee</b> (at 1 in 1,000) is blocked for Ramesh and Kabir, three checks for Asha, one warning for Meera.",
                  "<b>Fake seller and prize fee are mostly missed.</b> Fake seller is allowed for every customer and phone. The prize fee is also allowed for everyone; Ramesh (both phones) and Kabir (usual phone) scored MEDIUM but the engine still allowed it. The account looks ordinary, so there is little to act on. We left these in on purpose.",
                  "<b>SIM swap is blocked</b> for Asha, Ramesh and Kabir on either phone. <b>Remote control</b> is blocked for Asha and Ramesh on their usual phone, but Kabir got three checks and Meera one warning: the model called those cases 'coached' rather than 'takeover', and warning, name and payee checks would not stop a thief running the phone. A misrouting worth fixing.",
                  "<b>Blocking is the fallback where identity checks are missing.</b> Every takeover scenario ends in a block, because the engine has no way to let a genuine owner prove it is them."])

# gallery of outcomes
story += [PageBreak(), P("What the customer sees: four outcomes", H2),
          row_cards([("web_pin.jpg", "Allowed: Asha's Rs 300 to Priya. No checks; goes straight to the PIN pad."), ("web_success.jpg", "After the PIN: 'Payment sent. New balance Rs 59,700.' The balance changes only here."),
                     ("web_blocked_model.jpg", "Blocked by the engine: Asha's Rs 9,000 fake refund. CRITICAL, 40.6% chance of scam; no check could clear it."),
                     ("web_blocked_answer.jpg", "Blocked by a check: Ramesh answered 'Yes' to a danger question.")], width=W * 0.21),
          P("The X-ray for the allowed payment read LOW, 0.000% chance of scam and an expected cost of Rs 0. For the blocked refund it read CRITICAL, 40.647%, decision 'block: risk is too high for any available check to clear it'. The customer sees only a short, plain message and the helpline.", SMALL),
          P("Walk-through: Ramesh's digital-arrest payment", H2),
          table([["Step", "What the customer sees", "What the server did"],
                 ["Send", "Picks 'Digital arrest' (Rs 20,000 to 'Verification Cell'); the phone reports a 25-minute call from an unknown number.", "Scored: payee only 3 days old (+6.52), 25-minute call (+2.99), amount vs usual (+1.39). Severity HIGH, family COACHED."],
                 ["Plan", "'Safety check: step 1 of 3, about 16 seconds'.", "Chance of scam 1.061%. Cheapest plan: type name, wait, warning, expected cost Rs 95.95. (Allow would cost Rs 212; block Rs 198.)"],
                 ["Step 1", "Types 'Verification Cell'.", "Name matches. Doubt falls to 0.898%."],
                 ["Step 2", "20-second hold (30 minutes in real life).", "Release time stored on the server. Doubt 0.366%."],
                 ["Step 3", "Two questions. Answers 'Yes' to one.", "Danger answer: payment blocked, helpline shown. (Honest 'No' answers would have led to the PIN pad.)"]], [0.1, 0.4, 0.5])]

# ============================================================================================================= 8 play
story += [PageBreak(), P("8. Playing with it: switches, dials, and a surprising finding", H1),
          P("Ideas you can try now, and one finding from preparing this report that you should know about.", ONE),
          P("Things to try", H2)]
story += bullets(["<b>Switch a check off.</b> On a customer's home, untick 'Scam warning' and re-send the investment scam. The engine builds a plan from what is left (or blocks if it has nothing it trusts).",
                  "<b>Turn the scam-rarity dial.</b> In 'what the phone reports', pick 1 in 1,00,000, 1 in 10,000 or 1 in 1,000 and compare (the table in Section 5).",
                  "<b>Compare customers.</b> Send the same scam as Asha, Kabir and Meera. History changes the verdict.",
                  "<b>Trust the phone, or not.</b> Untick 'Phone SDK is trusted' to use the server-only model, which ignores call and remote-control signals.",
                  "<b>Make a real call (Android).</b> On the emulator, start a call and watch the call signal reach the model without touching a switch."])
story += [P("A surprising finding: a phone the model has never seen looks <i>less</i> risky", H2),
          P("While preparing the results table, we ran every scenario twice for each customer: once from their usual phone, once from a phone the history had never seen. "
            "We expected a never-seen phone to be riskier. It was the opposite in 13 of the 14 cases where the answer changed.", BODY),
          table([["Case", "Usual phone", "Never-seen phone"],
                 ["Asha, remote control (Rs 30,000, screen-share running)", "HIGH, takeover: <b>blocked</b>", "MEDIUM, takeover: <b>allowed</b>"],
                 ["Asha, digital arrest (Rs 20,000)", "CRITICAL: blocked", "HIGH: three checks"],
                 ["Ramesh, digital arrest", "HIGH: three checks", "MEDIUM: one warning"],
                 ["Meera, digital arrest", "MEDIUM: one warning", "LOW: allowed"],
                 ["Kabir, remote control", "HIGH: three checks", "HIGH: blocked (the one case that went the other way)"]], [0.48, 0.26, 0.26], first_bold=True),
          P("We have <b>not diagnosed</b> the cause. A likely explanation is how 'surprise' is calibrated: genuine payments from unfamiliar phones are naturally more varied (people do change phones), so the same evidence "
            "counts as less surprising there. If that is right, the model is quietly more forgiving in exactly the situation where an attacker works, and it should be fixed (for example by calibrating separately and adding device-newness as a risk signal on its own). "
            "It is a finding about the simulator-trained model, not a statement about real attackers.", NOTE),
          P("What it means for the apps: the web app now defaults to the customers' usual phone (web-device), so you see the 'usual phone' column. The Android app generates its own device id, so a fresh install behaves like a never-seen phone.", BODY),
          P("Other honest observations", H2)]
story += bullets(["The same payment can get different answers on different days because the history window and time of day move. Treat single cells as examples, not as fixed results.",
                  "The engine's numbers (pass rates, costs) drive its choices. Changing an assumed pass rate can change which check is picked. A sensitivity test belongs before any real use.",
                  "Blocks are common in the table because the engine has no identity check to offer. As soon as second-device approval or a device key exists, several of those cells would become one quick step."])

# ============================================================================================================= 9 android
story += [PageBreak(), P("9. The Android app", H1),
          P("A native Android app (Kotlin, Jetpack Compose) talks to the same API, shows the same plan and the same checks, and adds something a browser cannot: it can sense that a phone call is in progress.", ONE),
          phones([("android_home.jpg", "Home: balance, demo history loader, recent payments"), ("android_send.jpg", "Send: demo scenarios and the signals the phone reports"),
                  ("android_payee_check.jpg", "Plan step 1: payee check"), ("android_stopped.jpg", "A danger answer stops the payment")], width=W * 0.19),
          phones([("android_permission.jpg", "Android asks permission to read call state"), ("android_xray_call.jpg", "After a real emulated call: call minutes appear as model input"),
                  ("android_sent.jpg", "An allowed payment completes")], width=W * 0.19),
          P("What is real on the phone", H2)]
story += bullets(["<b>Call state.</b> With the user's permission, the app reads whether a call is in progress, how long it has lasted and whether the number is in the contacts. In the emulator we used <font face='Courier'>adb emu gsm call</font>; the app sent 'call active: true' with the call length and the X-ray showed it.",
                  "<b>Other signals</b> (screen mirroring, accessibility services, a simple root check) are written but were not exercised on the emulator. You can override any signal by hand in Settings.",
                  "<b>Permission wording.</b> Android's dialog says 'make and manage phone calls'. That is the system's wording for the whole phone group; the app only reads call state.",
                  "<b>Tested on an emulator only</b> (Pixel 6, Android 14), plus 20 unit tests, not on a physical phone."])

# ============================================================================================================= 10 security / limits
story += [PageBreak(), P("10. Security design, honest limits, next steps", H1),
          P("Security choices in the prototype", H2),
          table([["Choice", "Why"],
                 ["The server decides, the app displays", "A modified app cannot skip checks; 'complete' refuses unless the payment is approved."],
                 ["Passwords hashed with bcrypt; signed session tokens (JWT, 8 hours)", "Standard practice; demo password is shared only for the invented customers."],
                 ["X-ray and persona tools exist only in demo mode", "The X-ray teaches an attacker how to stay under thresholds. Turn DEMO off in anything real: those routes return 'not found'."],
                 ["No real PIN, no real money, no real identity data", "The UPI PIN pad accepts any 4 to 6 digits and stores nothing. Customers and payees are invented."],
                 ["Logs hold counts and outcomes", "No typed names or answers in the log; secrets never leave the server."],
                 ["Limits on cleartext", "Android allows plain HTTP only in the debug build, to reach the local server at 10.0.2.2."]], [0.4, 0.6], first_bold=True),
          P("What this prototype does not do", H2)]
story += bullets(["<b>No identity check.</b> Takeover cases are blocked, not resolved. Second-device approval or a device key is the next build.",
                  "<b>Not measured on real traffic.</b> The model, the likelihood ratios and the pass rates all come from simulation or assumption.",
                  "<b>Weak on scams that look ordinary:</b> fake sellers, prize fees, and brand-new accounts with no history.",
                  "<b>Device finding unresolved:</b> a never-seen phone can look less risky (Section 8).",
                  "<b>Coached victims can answer 'No'</b> to the warning, as scammers instruct. Only another person or time helps there (trusted contact, cooling-off).",
                  "<b>Profile fields are barely used.</b> Only age affects the policy; the model ignores profile data.",
                  "<b>Testing:</b> 34 backend tests (the engine, the challenge rules, the full payment flow, the four customers) and 20 Android unit tests pass. Nothing was tested on a physical phone or under load."])
story += [P("Suggested next steps", H2),
          table([["Priority", "Step", "Why"],
                 ["1", "Investigate and fix the new-phone behaviour", "A model that is more forgiving on never-seen phones weakens exactly the takeover cases."],
                 ["2", "Build second-device approval (a second browser tab as 'the other phone')", "Gives takeover cases a way to be resolved instead of blocked. Easy to build and demonstrate."],
                 ["3", "A scoreboard: all scenarios x all customers, logged with friction seconds", "Turns playing into numbers you can show. (The results table in Section 7 is the first version.)"],
                 ["4", "Trusted-contact approval for older customers", "The check most likely to defeat digital-arrest scams; fits Ramesh's profile."],
                 ["5", "Attacker mode: a second session that tries to defeat each check", "Shows which checks are weak before anyone relies on them."],
                 ["6", "Replace assumed pass rates with logged outcomes", "The engine's choices depend on them."]], [0.1, 0.45, 0.45]),
          P("Glossary", H2),
          table([["Term", "Plain meaning"],
                 ["Takeover / coached / bad payee", "Someone else pays / the owner is tricked / the receiving account is the problem."],
                 ["Surprise", "How rare a payment looks compared with genuine ones. Higher is rarer."],
                 ["Severity", "LOW, MEDIUM, HIGH, CRITICAL: bands of surprise, set so only a small share of genuine payments reach each."],
                 ["Base rate", "How common scams are in the traffic (for example 1 in 10,000). The engine's dial."],
                 ["Likelihood ratio", "How many times more common a severity band is among scams than among genuine payments."],
                 ["Friction", "How much a check annoys or delays the customer, priced in rupee-equivalents."],
                 ["Authenticator / check", "Any step that helps confirm a payment, or gives a tricked owner a chance to stop."],
                 ["Challenge / nonce", "One run of one check, tied to one payment by a one-time random code."],
                 ["X-ray", "The demo panel that shows the model's inputs, scores and reasons."],
                 ["Usual phone / new phone", "A phone the customer's history was recorded on / one the model has never seen."],
                 ["FRI", "The government's fraud-risk band for an account (0 none to 3 very high); the demo directory simulates it."]], [0.26, 0.74]),
          Spacer(1, 3 * mm), P("Files: the app is in <font face='Courier'>demopay/</font> (backend, frontend, android). The model is in <font face='Courier'>backend/ato/</font>. "
            "Scenario data used here: <font face='Courier'>docs/demopay-assets/scenario_matrix.json</font>.", SMALL)]

doc = BaseDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title="DemoPay implementation guide", author="Poojan Pandya", subject="How the DemoPay adaptive payment-protection prototype works")
doc.addPageTemplates([PageTemplate(id="m", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=footer)])
doc.build(story)
print("wrote", OUT)
