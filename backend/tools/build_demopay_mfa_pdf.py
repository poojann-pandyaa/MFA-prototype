"""
Builds docs/demopay-11-checks.pdf: the app's flow, how fraud is triggered, and how each of the 11 checks flags, warns or reassures.

    cd backend && .venv/bin/python -m tools.build_demopay_mfa_figures && .venv/bin/python -m tools.build_demopay_mfa_pdf
Four checks are built; the other seven are designs. Plans for the seven are PROJECTED by the real decision engine with them switched on.
"""
import dataclasses
import json
import sys

from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate

from tools.pdfkit import *  # noqa: F401,F403
from tools.pdfkit import ROOT, A, W

sys.path.insert(0, str(ROOT / "demopay" / "backend"))
from authn import policy, registry  # noqa: E402
from authn.registry import METHODS  # noqa: E402

OUT = ROOT / "docs" / "demopay-11-checks.pdf"
ORDER = list(METHODS)
ABBR = {"warning": "W", "typed_confirm": "T", "payee_check": "P", "cooling_off": "C", "second_device": "2D", "face": "F", "device_key": "K", "totp": "O",
        "passkey": "PK", "trusted_contact": "TC", "call_back": "CB"}
SHORT = ["Scam warning", "Type payee name", "Payee check", "Cooling-off wait", "Second device", "Face + liveness", "Device key", "Authenticator code", "Passkey", "Trusted contact", "Bank call-back"]
AMT = {"Routine payment": 300, "New local shop": 450, "Fake refund": 9000, "Digital arrest": 20000, "Digital arrest (large)": 60000, "Investment scam": 20000, "Look-alike payee": 4000,
       "Fake seller": 6000, "Prize fee": 3000, "SIM swap + new phone": 60000, "Remote control": 30000}
M = json.load(open(A / "scenario_matrix.json"))
LOOK = {(o["scenario"], o["customer"], o["device"]): o for o in M}
CUST = ["asha", "ramesh", "kabir", "meera"]
ORIG = dict(registry.METHODS)


def project(o, all11=True, focus=0.8):
    """The real engine, with the seven unbuilt checks switched on (their numbers are assumptions)."""
    registry.METHODS.clear()
    registry.METHODS.update({k: dataclasses.replace(m, implemented=True, universal=True) for k, m in ORIG.items()} if all11 else ORIG)
    policy.FAMILY_FOCUS = focus
    prior = 1e-3 if o["scenario"] == "Look-alike payee" else 1e-4
    slow = 10000.0 if o["customer"] == "ramesh" else 50000.0
    return policy.make_plan(o["severity"], o["threat"], AMT[o["scenario"]], [], prior, policy.DEFAULT_TIME_BUDGET, slow)


def label(pl):
    return "Allow" if pl.decision == "allow" else "Block" if pl.decision == "block" else "+".join(ABBR[s.method] for s in pl.steps)


def cell_bg(txt):
    return "#dff1e5" if txt == "Allow" else "#f6d5d5" if txt == "Block" else "#fff1cf"


def what_if_table():
    head = [P("Scenario", HEADC)]
    for c in CUST:
        head += [P(f"{c.title()}<br/>4 checks", HEADCC), P(f"{c.title()}<br/>all 11", HEADCC)]
    rows, style = [head], []
    for r, name in enumerate(AMT, start=1):
        row = [P(name, CELLB)]
        for ci, c in enumerate(CUST):
            o = LOOK.get((name, c, "web-device"))
            for k, all11 in enumerate((False, True)):
                txt = "n/a" if o is None else label(project(o, all11))
                row.append(P(txt, CELLC))
                style.append(("BACKGROUND", (1 + 2 * ci + k, r), (1 + 2 * ci + k, r), colors.HexColor("#e5e9ee" if o is None else cell_bg(txt))))
        rows.append(row)
    t = Table(rows, colWidths=[W * 0.236] + [W * 0.0955] * 8, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, GRID), ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)] + style))
    return t


def story_row(title, sub, fraud, sees, today, user, proj, grade):
    rows = [[P(f"<b>{title}</b><br/><font size=7.5 color='#555555'>{sub}</font>", CELL), P(f"<b>The fraudster</b><br/>{fraud}", CELL)],
            [P(f"<b>What the app sees</b><br/>{sees}", CELL), P(f"<b>Today (4 checks built)</b><br/>{today}<br/><i>The customer sees:</i> {user}", CELL)],
            [P(f"<b>With all 11 (projected)</b><br/>{proj}", CELL), P(f"<b>Result</b><br/>{grade}", CELL)]]
    t = Table(rows, colWidths=[W * 0.5, W * 0.5])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, GRID), ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return KeepTogether([t, Spacer(1, 3 * mm)])


def pl_of(scn, cust, all11=False, focus=0.8):
    return project(LOOK[(scn, cust, "web-device")], all11, focus)


def ftable(rows, total):
    t = Table([[Paragraph(a, CELLB), Paragraph(b, CELL)] for a, b in rows], colWidths=[total * 0.22, total * 0.78])
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, GRID), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)] + [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(0, len(rows), 2)]
    t.setStyle(TableStyle(st))
    return t


def check_block(n, status, job, one, left, rows, kind):
    """One check: header strip, a one-line summary, a screenshot or mockup on the left and a fact table on the right."""
    badge = "BUILT AND WORKING" if status == "built" else "DESIGNED, NOT BUILT"
    col = GREEN if status == "built" else AMBER
    head = Table([[P(f"<font color='white'><b>{n}. {SHORT[n - 1] if n not in (1, 2) else METHODS[ORDER[n - 1]].name}</b></font>", CELL),
                   P(f"<font color='white'>{job}</font>", CELLC), P(f"<font color='white'><b>{badge}</b></font>", CELLC)]], colWidths=[W * 0.5, W * 0.17, W * 0.33])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), NAVY), ("BACKGROUND", (1, 0), (1, 0), TEAL if "PROVE" in job else AMBER), ("BACKGROUND", (2, 0), (2, 0), col), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    lw = W * (0.27 if kind == "one" else 0.36)
    body = Table([[left, ftable(rows, W - lw - 4 * mm - 6)]], colWidths=[lw + 4 * mm, W - lw - 4 * mm])
    body.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 6), ("RIGHTPADDING", (1, 0), (1, 0), 0)]))
    return KeepTogether([head, Spacer(1, 1.5 * mm), P(one, ONE), body, Spacer(1, 6 * mm)])


def pic(name, width, caption):
    return [img(name, width), P(caption, CAP)]


def cardpic(name, width, caption):
    return [_card(name, width), P(caption, CAP)]


from tools.pdfkit import _card  # noqa: E402

story = []
# ================================================================================================================= cover
story += [Spacer(1, 36 * mm), P("How the 11 checks work in DemoPay", COVT),
          P("The app's flow, how a fraud is triggered, and how each check flags it, warns the customer, or reassures them by proving it is really them", COVS),
          P("A walk through the payment app from the customer's side and the fraudster's side, with the real model's verdicts, screenshots of the checks that are built, and design mockups of the ones that are not yet.", ONE),
          Spacer(1, 6 * mm),
          P("<b>Read this first.</b> Of the 11 checks, <b>4 are built and working</b> in the app (scam warning, type the payee's name, payee check, cooling-off wait) and <b>7 are designs</b> "
            "(second device, face + liveness, device key, authenticator code, passkey, trusted contact, bank call-back). Every page says which is which. Where the document shows what the app would do "
            "with all 11, those results are <b>projections</b>: the real decision engine run with the seven unbuilt checks switched on, using our assumed numbers for them. They are not tests of working features. "
            "All customers, payees, scams and money are invented, and nothing here is a measured result on real UPI traffic.", NOTE),
          Spacer(1, 6 * mm),
          table([["Contents"], ["1. The idea: two jobs, WARN and PROVE, and the 11 at a glance"], ["2. The app and its flow: use cases from the customer's side"],
                 ["3. How fraud gets triggered: the fraudster's side"], ["4. How the app flags it: the model, the three doubts, and which check answers which"],
                 ["5. The 11 checks, one by one"], ["6. Eight fraud stories from trigger to outcome"], ["7. What changes with all 11: a projection for every scenario"],
                 ["8. What the customer reads: warnings and reassurance"], ["9. How to build the seven that are missing"], ["10. Honest limits and what to measure"]], [1.0]),
          Spacer(1, 10 * mm), P("Prepared 6 November 2026 | Poojan Pandya | prototype, not a product", SMALL)]

# ================================================================================================================= 1 idea
story += [PageBreak(), P("1. The idea: two jobs, WARN and PROVE", H1),
          P("A payment can be wrong for three reasons, and each needs a different kind of check. Mixing them up is how most payment apps fail the customer.", ONE),
          table([["The situation", "What is really happening", "The check's job", "Example checks"],
                 ["<b>The owner is being tricked</b> (coached)", "The real owner taps everything correctly, because a stranger on the phone is telling them what to do.", "<b>WARN.</b> Break the script, slow the owner down, bring in a human. A PIN or fingerprint proves nothing here: it is the owner.", "1 Scam warning, 3 Payee check, 4 Cooling-off wait, 10 Trusted contact"],
                 ["<b>Someone else is paying</b> (takeover)", "A thief has the SIM, the phone, or remote control of it. The person tapping is not the owner.", "<b>PROVE.</b> Ask for something only the real owner has, from a different channel if possible. A warning is useless: the thief clicks through it.", "5 Second device, 6 Face, 7 Device key, 8 Authenticator code, 9 Passkey, 11 Bank call-back"],
                 ["<b>The receiver is the problem</b> (bad payee)", "The owner pays a look-alike of a friend, a mule, a fake seller.", "<b>CONFIRM THE PAYEE.</b> Put the facts about the receiver in front of the owner.", "2 Type payee name, 3 Payee check"]], [0.19, 0.28, 0.33, 0.20]),
          Spacer(1, 3 * mm),
          fig("fig_journey.png", "Figure 1. Where the 11 checks sit in the payment flow. The risk model scores the payment; the decision engine sends it down one lane. Filled boxes exist in the app; dashed boxes are designs.", width=W * 0.97),
          P("The 11 at a glance", H2),
          table([["#", "Check", "Job", "Speed", "Status in the app"]] +
                [[str(i + 1), METHODS[k].name, {1: "WARN", 2: "CONFIRM PAYEE", 3: "CONFIRM PAYEE", 4: "WARN (time)", 5: "PROVE", 6: "PROVE", 7: "PROVE", 8: "PROVE", 9: "PROVE", 10: "WARN (a person)", 11: "PROVE and WARN"}[i + 1],
                  "slow, 30 min" if METHODS[k].tier == "slow" and k == "cooling_off" else ("slow" if METHODS[k].tier == "slow" else "fast"), "<b>built</b>" if METHODS[k].implemented else "designed"] for i, k in enumerate(ORDER)],
               [0.05, 0.38, 0.2, 0.15, 0.22])]

# ================================================================================================================= 2 app flow
story += [Spacer(1, 6 * mm), P("2. The app and its flow", H1),
          P("DemoPay is a payment app (web and Android) on one backend. It has four invented customers, each with a payment history, so you can try a payment as someone with a past.", ONE),
          row_cards([("web_login.jpg", "1. Pick a customer (demo mode)."), ("web_home_asha.jpg", "2. Home: balance, history, switches for the checks."), ("web_pin.jpg", "3. A routine payment goes straight to the PIN pad."),
                     ("web_step_payee.jpg", "4. A risky one gets checks first.")], width=W * 0.215),
          P("What happens when a customer pays", H2),
          table([["Step", "Customer", "App and server"],
                 ["1", "Enters payee, amount, and whether it is a payment, a collect request or a scanned QR.", "Sends them to the server with what the phone reports (on a call? remote control? SIM or phone changed?)."],
                 ["2", "Waits a moment.", "Replays the customer's history, looks the payee up, asks the model how surprising the payment is."],
                 ["3", "Sees one of: the PIN pad, a short list of checks, or a stop message.", "The decision engine chose allow, a plan of up to three checks, or block."],
                 ["4", "Does the checks one at a time. A WARN check lets them reconsider; a PROVE check lets them show it is them.", "Each check is a one-time challenge tied to this payment. The server judges every answer."],
                 ["5", "Enters the PIN (any 4 to 6 digits in the demo).", "Money moves only if the payment is approved. The balance changes here and only here."]], [0.07, 0.43, 0.5]),
          P("Use cases the flow covers", H2),
          table([["Use case", "What happens", "Checks involved"],
                 ["Routine payment to a known friend or shop", "Allowed with no steps. The customer is not bothered.", "None"],
                 ["Payment to a new but genuine payee", "Usually allowed; at most a quick confirmation.", "None or 2, 3"],
                 ["Collect request from a stranger", "Treated as higher risk: it SENDS money when approved.", "1, 3, then others"],
                 ["Scanned QR to an unknown account", "Payee facts are checked.", "2, 3"],
                 ["Payment while on a long call with an unknown number", "The strongest sign of coaching. The call state is real on Android.", "1, 4, 10"],
                 ["First payment from a new phone, SIM just changed", "Possible takeover.", "5, 6, 7 (designed)"],
                 ["Customer answers a danger question 'Yes'", "Payment stopped at once with the helpline.", "1"],
                 ["Customer cancels or the step expires", "Payment cancelled or blocked; nothing moves.", "Any"],
                 ["A check is switched off for a customer", "The engine rebuilds its plan from what is left (demo playground).", "Any"]], [0.34, 0.46, 0.2])]

# ================================================================================================================= 3 fraud triggers
story += [PageBreak(), P("3. How fraud gets triggered: the fraudster's side", H1),
          P("A scam is a sequence of steps. Each step leaves a trace the app can see before the money moves. The table lists the 14 scam types the model was trained on and the traces each leaves.", ONE),
          table([["Family and scam", "What the fraudster does", "Traces the app can see"],
                 ["<b>Takeover</b><br/>SIM swap", "Ports the victim's number to a new SIM, resets the UPI PIN, pays out.", "SIM changed hours ago; UPI newly bound to a phone; PIN reset; touch pattern unlike the owner; account the platform never saw."],
                 ["Takeover<br/>APK binding", "Gets the victim to install a fake app that binds the account to the fraudster's phone.", "Sideloaded app; new device binding; an old device still active."],
                 ["Takeover<br/>Remote access", "Talks the victim into installing a screen-share app 'for a refund', then drives the phone.", "Remote-access flag on; accessibility or overlay abuse; unusual touch pattern."],
                 ["Takeover<br/>Physical theft", "Steals an unlocked phone and pays quickly.", "Different touch pattern and hours; PIN failures; a burst of payments."],
                 ["<b>Coached</b><br/>Digital arrest", "Poses as police or an agency on a long call; says the victim is in a crime; demands 'verification' payments and secrecy.", "Long call with an unknown number during the payment; new account with no history; large amount for this customer."],
                 ["Coached<br/>Fake customer care / collect request", "Calls about a 'refund' and sends a collect request that the victim approves.", "Collect request from a non-contact; account a few days old with complaints; call in progress."],
                 ["Coached<br/>Investment or task scam", "A chat group promises easy returns; small payouts then a big 'deposit'.", "New account in a risky category; amount far above usual; payments to many new accounts."],
                 ["Coached<br/>Prize fee, romance, relative in trouble, extortion", "Asks for a fee or an urgent transfer using fear, love or greed.", "Usually: a first-time payee, an urgent amount, sometimes a call. Many look ordinary: the hardest group."],
                 ["<b>Bad payee</b><br/>Look-alike id", "Sends 'my new number' with an id one letter off a friend's.", "Account 3 days old; id close to a saved contact; unusual senders."],
                 ["Bad payee<br/>Fake seller / mule", "An ordinary-looking seller or account that receives and forwards stolen money.", "Often nothing, until the account is reported. The model is weakest here (about half caught in the simulation)."]], [0.2, 0.38, 0.42]),
          P("The model does not read the story. It reads the traces and asks how rare that combination is among genuine payments. That is why a fraud that leaves no trace (a fake seller with a clean account) is the one the app is most likely to miss, "
            "and why the checks matter: they give the customer, a person, or the bank a way to catch what the traces could not.", WHYB)]

# ================================================================================================================= 4 flagging
story += [PageBreak(), P("4. How the app flags it", H1),
          P("The risk model turns the traces into a severity and a best guess of the fraud family. The decision engine then picks checks that answer the right doubt.", ONE),
          P("From traces to a flag", H2)]
story += bullets(["<b>Three detectors</b> (takeover, coached, bad payee) each say how surprising the payment is, compared with ordinary genuine payments.",
                  "<b>Severity</b> is LOW, MEDIUM, HIGH or CRITICAL. The cut-offs are set so only about 2%, 0.3% and 0.05% of genuine payments reach each level, so the customer-facing friction has a fixed small budget.",
                  "<b>The family</b> (the router names which detector is most likely right: 97% for takeover, 98% for coached, 43% for bad payee in the simulation) tells the engine whether to WARN, PROVE, or CONFIRM THE PAYEE."])
story += [P("Which check answers which doubt", H2),
          fig("fig_map.png", "Figure 2. For each check, the assumed chance that a scam of each kind gets past it. Green is strong (3% to 10%), amber partial, red does little. Note the pattern: the PROVE checks are green only against takeover; the WARN and payee checks do something against coached and bad-payee scams.", width=W * 0.96),
          P("Reading the figure", H2)]
story += bullets(["<b>No single check covers everything.</b> The five PROVE checks (5 to 9) are almost perfect against a thief and do nothing against a tricked owner, who passes them happily.",
                  "<b>The coached family needs people and time</b> (10 Trusted contact, 4 Cooling-off, 1 Warning). They are slow or soft, so the engine saves the slow ones for the riskiest payments.",
                  "<b>Cheap and fast first.</b> The engine adds up friction (seconds, annoyance) and leaked scam value for every combination of up to three checks and picks the cheapest one that fits a time budget (10 s for MEDIUM, 30 s for HIGH).",
                  "<b>Today only the four built checks are in play,</b> which cover coached and bad-payee scams reasonably but leave takeover unresolved: a takeover is blocked outright rather than checked."])

# ================================================================================================================= 5 the 11
story += [PageBreak(), P("5. The 11 checks, one by one", H1),
          P("Each entry gives what the check is, when the engine asks for it, what the customer reads (a warning, or reassurance), what it catches and misses, and where it lives in the app. Numbers marked 'assumed' come from the engine's settings file, not from measurements.", ONE)]

story += [check_block(1, "built", "WARN", "A plain warning that matches the situation, plus two random questions with a 'danger' answer, and a forced pause so it cannot be tapped through.",
          cardpic("web_step_warning.jpg", W * 0.25, "Step 3 of 3 in Ramesh's plan: warning, two questions, read-timer."),
          [["How it works", "The server picks the wording from the threat family and two questions from a bank of four (call telling you to pay, told to keep secret, told you will be arrested, promised a prize or refund). The button waits 4 s and needs the 'I have read' box; the server checks the time too."],
           ["When the engine asks", "MEDIUM and above when the owner may be tricked or a collect request. Always available, no enrolment."],
           ["Customer reads", "<b>Warn:</b> 'This looks like a common scam. Scammers often ask for payment during a call or chat. Banks and government agencies never do. Call 1930 or your bank before paying.' A 'Yes' to a danger question stops the payment."],
           ["Catches", "An owner who answers honestly. Breaks the scammer's script of secrecy and urgency."],
           ["Misses", "An owner coached to answer 'No' (assumed 60% get past) and any thief (100% pass: a thief clicks through)."],
           ["Numbers (assumed)", "8 s, friction Rs 2, genuine customers pass 100%."],
           ["Where in the app", "Built. Logic: demopay/backend/authn/methods.py ('warning'). Screen: frontend/src/components/steps.jsx."]], "side")]

story += [check_block(2, "built", "CONFIRM PAYEE", "The customer types the name the bank has on record for the account. It forces a look at who is really receiving the money.",
          cardpic("web_step_typed.jpg", W * 0.25, "Step 1 of 3: the payee id is shown, the name is not."),
          [["How it works", "Capital letters and extra spaces are ignored. Three wrong tries stops the payment. Not offered if the bank has no registered name for the payee (it could never be passed)."],
           ["When the engine asks", "Look-alike and risky-payee situations, and as part of most HIGH plans."],
           ["Customer reads", "<b>Warn (by effort):</b> 'Type the registered name of the person or business you are paying.' A wrong name: 'That does not match the registered name. 2 attempt(s) left.'"],
           ["Catches", "Look-alike scams: the customer expects 'Priya Sharma', the record says 'P Sharma'."],
           ["Misses", "A customer who copies the name from the scammer's message types it correctly (assumed 85% of coached victims pass)."],
           ["Numbers (assumed)", "8 s, friction Rs 3, genuine customers pass 98%."],
           ["Where in the app", "Built. methods.py ('typed_confirm'), steps.jsx."]], "side")]

story += [PageBreak(),
          check_block(3, "built", "CONFIRM PAYEE", "One tap: shows what the bank knows about the receiving account and asks 'Is this who you mean to pay?'",
          cardpic("web_step_payee.jpg", W * 0.25, "Payee id, registered name, account age '3 day(s), very new'."),
          [["How it works", "Shows the registered name, the account age, whether it has been reported and, for a look-alike id, which genuine id it resembles. 'No, stop' ends the payment; 'Yes, continue' passes."],
           ["When the engine asks", "Risky payee, new account, or collect request. Cheap (6 s) so it appears often."],
           ["Customer reads", "<b>Warn:</b> the facts, in plain words: 'Account age: 3 day(s), very new.' If they say no: 'Payment cancelled: you said this is not who you mean to pay.'"],
           ["Catches", "A brand-new or reported account, and look-alike ids (the hint names the real contact)."],
           ["Misses", "An old, ordinary mule account shows nothing alarming (assumed 30% of bad-payee scams get past, 90% of coached)."],
           ["Numbers (assumed)", "6 s, friction Rs 4, genuine customers pass 97%."],
           ["Where in the app", "Built. methods.py ('payee_check'), steps.jsx; fake directory in app/seed.py."]], "side"),
          check_block(4, "built", "WARN (time)", "The payment sits on a timer and the customer is told to stop and talk to someone. In real life 30 minutes; the demo shortens it to 20 seconds and says so.",
          cardpic("web_step_cooling.jpg", W * 0.25, "Step 2 of 3: countdown and calm tips."),
          [["How it works", "The server stores the release time, so closing the app or changing the phone's clock does not shorten it. The customer may cancel but cannot skip."],
           ["When the engine asks", "Slow check, used rarely: at CRITICAL, or at HIGH when the owner looks tricked and the amount is Rs 50,000 or more (Rs 10,000 for customers aged 60 or over)."],
           ["Customer reads", "<b>Warn:</b> 'This payment is on a short hold.' Tips: stay on the line only if you started the call; no agency asks you to move money to a safe account; talk to someone you trust; call 1930."],
           ["Catches", "Scams that run on urgency: digital arrest, fake police, prize fees."],
           ["Misses", "A customer who waits and pays anyway; scams that do not need urgency (assumed 35% to 50% get past)."],
           ["Numbers (assumed)", "30 min (20 s in the demo), friction Rs 25, genuine customers pass 90% (some give up)."],
           ["Where in the app", "Built. methods.py ('cooling_off'), steps.jsx."]], "side")]

story += [PageBreak(), P("The seven designed checks", H2),
          P("The next seven are not in the app yet. Each page shows a mockup of the screen, how it would work, and what it would take to build. The pass rates are the engine's assumptions; they are what the projections in Section 7 use.", NOTE)]
mockw = lambda n: W * (0.26 if n == 1 else 0.40)  # noqa: E731
story += [check_block(5, "designed", "PROVE", "A second device the customer already owns (another phone, a tablet, a browser tab) approves the payment. A different channel from the paying phone, so a thief who has only the SIM or the phone cannot answer.",
          pic("mock_second_device.png", mockw(2), "Design mockup: matching a number across two devices."),
          [["How it would work", "The customer enrols an approver device (new approvers count only after a delay). At payment time the paying phone shows a 2-digit number; the approver shows the payee, the amount and three numbers; the customer picks the matching one or taps Deny."],
           ["When the engine asks", "When takeover doubt is the biggest worry (new phone, SIM changed, remote control) and an approver is enrolled."],
           ["Customer reads", "<b>Reassure:</b> 'Approve on your other device.' On success: 'Confirmed on your other device. Thanks.' On Deny: 'Payment stopped. If this was not you, new payments are paused until you verify.'"],
           ["Would catch", "A thief with the SIM or phone but not the second device (assumed 3% get past)."],
           ["Would miss", "A tricked owner who approves; an owner with no second device; approval fatigue if prompts are frequent."],
           ["Numbers (assumed)", "15 s, friction Rs 6, genuine pass 95%."],
           ["To build", "Table of approver devices, endpoints to enrol, list pending approvals and respond; a method with start and check in methods.py; a second browser tab as the demo approver. Effort: medium."]], "two")]

story += [PageBreak(),
          check_block(6, "designed", "PROVE", "A live face check with a random head turn, so a photo or video cannot pass. The original project already has face detection, matching, a liveness challenge and a spoof detector (backend/services), not yet connected to DemoPay.",
          pic("mock_face.png", mockw(1), "Design mockup: face check with a head turn."),
          [["How it would work", "The phone's front camera shows an oval. The app asks for a random gesture (turn left, turn right). Frames go to the check, which compares the face to the one enrolled and checks it is live."],
           ["When the engine asks", "Takeover doubt and no second device available. Slower and the highest-friction identity check."],
           ["Customer reads", "<b>Reassure:</b> 'Look at the camera, then turn your head to the left.' Failure: 'We could not confirm it is you. Try again in better light.'"],
           ["Would catch", "A thief who does not look like the owner (assumed 8.7% of spoof attempts pass, from the repo's calibration)."],
           ["Would miss", "Deepfakes injected into the camera feed; a tricked owner (passes). It also rejects 12.7% of genuine tries in the repo's calibration, which is why it is not the first choice."],
           ["Numbers", "20 s, friction Rs 8, genuine pass 87.3%."],
           ["To build", "Wrap the existing services as a method; use the browser camera for the web demo. Privacy: keep the face template on the device or store only a non-reversible embedding, with consent. Effort: large."]], "one"),
          check_block(7, "designed", "PROVE", "The phone holds a secret key in secure hardware and signs the exact payment after the customer's fingerprint or screen lock. The server checks the signature.",
          pic("mock_device_key.png", mockw(1), "Design mockup: sign this payment with a fingerprint."),
          [["How it would work", "At enrolment the phone creates a key pair in secure hardware (Android Keystore) that needs the fingerprint to use; the server stores only the public key. Each payment: the server sends the one-time code; the phone signs 'payee | amount | code'; the server verifies."],
           ["When the engine asks", "Whenever takeover is plausible. At 1 second and Rs 0.5 it is almost free, so the engine adds it freely."],
           ["Customer reads", "<b>Reassure:</b> 'Touch the sensor to confirm Rs 20,000 to Priya Sharma.' Nothing else to read: the best customer experience of the identity checks."],
           ["Would catch", "A SIM swap or new phone (the thief does not have the key); a changed payee or amount (the signature covers them); replay (the code is one-time)."],
           ["Would miss", "A tricked owner who signs; malware that gets the owner to tap; a stolen phone that is unlocked."],
           ["Numbers (assumed)", "1 s, friction Rs 0.5, genuine pass 98%, thief gets past 5%."],
           ["To build", "The challenge service already ties a one-time code to the payee and amount, which is exactly what is signed. Add key enrolment, a verify step, and a WebCrypto key for the web demo. Effort: medium."]], "one")]

story += [PageBreak(),
          check_block(8, "designed", "PROVE", "A 6-digit code that changes every 30 seconds from an authenticator app. Familiar, cheap, and the weakest PROVE check because a code can be talked out of a customer.",
          pic("mock_totp.png", mockw(1), "Design mockup: enter the current code."),
          [["How it would work", "At enrolment the customer scans a QR that stores a shared secret in the app. At payment the customer types the code; the server computes the same code and accepts the current and previous window."],
           ["When the engine asks", "Takeover doubt where nothing stronger is enrolled."],
           ["Customer reads", "<b>Reassure:</b> 'Open your authenticator app and type the 6-digit code for DemoPay.' Never: 'read the code to the caller.'"],
           ["Would catch", "A thief with the SIM but not the authenticator app (assumed 10% get past)."],
           ["Would miss", "Anyone who reads the code to a scammer: digital-arrest callers regularly ask for codes. Also an app on the same compromised phone."],
           ["Numbers (assumed)", "10 s, friction Rs 3, genuine pass 96%."],
           ["To build", "About 15 lines with Python's standard library (HMAC-SHA1, RFC 6238), a QR on the enrol page. Effort: small."]], "one"),
          check_block(9, "designed", "PROVE", "A passkey is a key the device holds for this app only, released by the screen lock. It cannot be typed into a fake site, so it resists phishing.",
          pic("mock_passkey.png", mockw(1), "Design mockup: the system passkey sheet."),
          [["How it would work", "Web: the WebAuthn standard. Android: Credential Manager. The challenge sent to the device is the payment's hash, so the approval covers this payee and amount."],
           ["When the engine asks", "Takeover doubt on a trusted device; very fast (about 4 s), so it competes with 7 Device key."],
           ["Customer reads", "<b>Reassure:</b> 'Use your screen lock to continue.' The system shows its own familiar sheet."],
           ["Would catch", "A thief without the device and its lock; fake look-alike sites."],
           ["Would miss", "A tricked owner who approves. Where a passkey only proves sign-in, it does not prove payment content: we would bind the challenge to the payment."],
           ["Numbers (assumed)", "4 s, friction Rs 2, genuine pass 98%, thief gets past 5%."],
           ["To build", "A WebAuthn library on the server and the browser API on the web demo. Likely the web counterpart of 7 Device key. Effort: medium."]], "one")]

story += [PageBreak(),
          check_block(10, "designed", "WARN (a person)", "A person the customer trusts (a daughter, a friend) is asked whether they know why the money is going out. A human is the best answer to coaching.",
          pic("mock_contact.png", mockw(2), "Design mockup: the owner waits, the contact decides."),
          [["How it would work", "The customer enrols one to three contacts (changes are delayed and notified). At payment the contact gets a link with only this payment, a plain question, and three buttons: Approve, Decline, Decline and call."],
           ["When the engine asks", "Coaching at CRITICAL, or HIGH for a large amount. Slow (about 3 minutes) and costly, so rare."],
           ["Customer reads", "<b>Warn:</b> 'We asked Meera to confirm. The payment stays on hold until she answers.' If declined: 'Meera declined. Call 1930 if you were asked to pay by a caller.'"],
           ["Would catch", "A tricked owner: the contact often sees the scam at once (assumed 25% get past)."],
           ["Would miss", "A contact who is also fooled; an owner who will not involve anyone; a contact who does not answer (then the engine falls back)."],
           ["Numbers (assumed)", "3 min, friction Rs 40, genuine pass 85%."],
           ["To build", "Invite and accept, request and respond endpoints, a link page, and a second browser tab as the demo contact. Privacy: the contact sees only that payment. Effort: medium."]], "two"),
          check_block(11, "designed", "PROVE and WARN", "The bank phones the number on record and confirms the payment by voice. It does both jobs: a thief has to pass a human, and a tricked owner hears a calm voice say 'we never ask for your PIN'.",
          pic("mock_callback.png", mockw(1), "Design mockup: we are calling you."),
          [["How it would work", "The payment is held. The bank calls the registered number (or a number registered long before the SIM change); an agent or IVR confirms payee and amount."],
           ["When the engine asks", "CRITICAL only. The costliest check (5 minutes, Rs 60)."],
           ["Customer reads", "<b>Reassure and warn:</b> 'For your safety we are calling your registered number. We never ask for your PIN or OTP, or to move money.'"],
           ["Would catch", "A thief who cannot answer questions only the owner knows; a tricked owner who hears a human warning."],
           ["Would miss", "After a SIM swap the call goes to the thief's SIM. The engine's assumed 2% pass rate is optimistic there; the call must go to a number captured before the change."],
           ["Numbers (assumed)", "5 min, friction Rs 60, genuine pass 90%."],
           ["To build", "Mostly operational: agents or an automated voice service. In the demo, a simulated call page. Effort: large."]], "one")]

# ================================================================================================================= 6 stories
story += [PageBreak(), P("6. Eight fraud stories, from trigger to outcome", H1),
          P("Each story follows one scam from what the fraudster does, to what the app sees, to what the customer experiences. 'Today' is the real model and engine with the four built checks; 'with all 11' is the projection (Section 7).", ONE)]
ex = lambda s, c, a=False, fo=0.8: label(pl_of(s, c, a, fo))  # noqa: E731
st = lambda s, c: LOOK[(s, c, "web-device")]  # noqa: E731
story += [
    story_row("1. Digital arrest", "Ramesh, 68, pays Rs 20,000", "Calls as 'police', says Ramesh's account is tied to a crime, keeps him on the line 25 minutes and tells him to send a 'verification' payment and tell no one.",
              "A 25-minute call from an unknown number; payee 3 days old with no history; amount well above his usual. Severity HIGH, family COACHED, chance of scam 1.06%.",
              f"Plan {ex('Digital arrest', 'ramesh')} (type name, wait, warning).", "'Type the registered name', a hold with calm tips, then two questions. A 'Yes' to 'told you will be arrested?' ends the payment with 'Call 1930'.",
              f"Plan {ex('Digital arrest', 'ramesh', True)}. The 1-second device key is added as cheap insurance; the engine still does not pick the trusted contact.", "<b>Stopped if Ramesh answers honestly.</b> Not stopped if he follows the caller's script and says No."),
    story_row("2. Fake refund (collect request)", "Asha, 34, asked to approve Rs 9,000", "Poses as customer care, promises a refund, sends a collect request and says 'approve to receive money'.",
              "A collect request from a non-contact; a 2-day-old account already reported twice; a 6-minute call. Severity CRITICAL, COACHED, chance of scam 40.6%.",
              f"{ex('Fake refund', 'asha')}.", "'We stopped this payment to protect your money. Call 1930 or your bank.'", f"{ex('Fake refund', 'asha', True)}: no change. No check can clear a payment this risky.", "<b>Stopped.</b>"),
    story_row("3. Investment or task scam", "Asha pays Rs 20,000", "A chat group shows 'profits'; after small payouts, asks for a big deposit to a 'trading' account.",
              "A 3-day-old account in a risky category; amount far above her usual. No call. Severity HIGH, COACHED.",
              f"{ex('Investment scam', 'asha')} (payee check, type name, warning).", "A card showing 'Account age: 3 day(s), very new', then the name check and the warning.", f"{ex('Investment scam', 'asha', True)}.", "<b>Flagged, checks given.</b> Kabir (only a warning) and Meera (nothing: no history to compare) fared worse."),
    story_row("4. Look-alike payee", "Asha pays Rs 4,000 (scam rarity 1 in 1,000)", "Sends 'new number' from priya.sharrna@okaxis, one letter from her friend's id.",
              "A 3-day-old account whose id is close to a saved contact. Severity HIGH, family BAD PAYEE.",
              f"{ex('Look-alike payee', 'asha')}.", "The payee check names the likely real contact; the name check shows 'P Sharma', not 'Priya Sharma'.", f"{ex('Look-alike payee', 'asha', True)}.", "<b>Flagged.</b> Ramesh and Kabir are blocked outright for the same payment."),
    story_row("5. SIM swap and new phone", "Asha, thief pays Rs 60,000", "Ports Asha's SIM, installs UPI on a new phone, resets the PIN and pays an account the platform has never seen.",
              "SIM changed 2 hours ago; new phone bound 1 hour ago; PIN reset; touch pattern unlike the owner. Severity CRITICAL, UNAUTHORISED (takeover), chance of scam 40.6%.",
              f"{ex('SIM swap + new phone', 'asha')}.", "A stop message. The real owner is not asked anything: today the app has no way to prove it is her.",
              f"{ex('SIM swap + new phone', 'asha', True)} as the engine is; {ex('SIM swap + new phone', 'asha', True, 0.97)} if the engine trusts the router (97% accurate for takeover). PROVE checks would be the right tool.",
              "<b>Stopped, but also blocks the genuine owner</b> if she is the one on the new phone."),
    story_row("6. Remote control", "Asha, Rs 30,000 driven from afar", "Gets Asha to install a screen-share app 'to fix a refund' and drives her phone.",
              "Screen-share running; touch pattern unlike the owner. Severity HIGH, UNAUTHORISED, chance of scam 1.06%.",
              f"{ex('Remote control', 'asha')}.", "A stop message.", f"{ex('Remote control', 'asha', True)}: a device-key tap the remote operator cannot give (the fingerprint is not mirrored), plus a payee check and a warning.", "<b>Stopped today; would be checked rather than blocked.</b> Kabir's case was misrouted as 'coached' and got warning-type checks that would not stop a thief."),
    story_row("7. Fake seller and prize fee", "Kabir Rs 6,000 by QR; Asha Rs 3,000", "An ordinary-looking account takes the money, with no urgency or call.",
              "Nothing unusual: an account 20 to 30 days old, no reports. Severity LOW or MEDIUM.",
              f"{ex('Fake seller', 'kabir')} and {ex('Prize fee', 'asha')}.", "Nothing: the payment goes straight to the PIN pad.", "Same: the engine never asks for a check, so none of the 11 helps.", "<b>Missed.</b> Only better payee intelligence (reports from other banks) would change this. We left these in on purpose."),
    story_row("8. Genuine payments", "Asha Rs 300; Asha Rs 450 to a new shop", "No fraud.", "A known friend; a new but genuine shop 45 days old. Severity LOW.",
              f"{ex('Routine payment', 'asha')} and {ex('New local shop', 'asha')}.", "The PIN pad, straight away. Nothing is asked.", "Same.", "<b>Reassured by silence.</b> The best protection for a genuine customer is not being interrupted."),
]

# ================================================================================================================= 7 what-if
story += [PageBreak(), P("7. What changes with all 11: a projection for every scenario", H1),
          P("Same model verdicts as the real runs (usual phone), but the decision engine is also given the seven unbuilt checks, with their assumed numbers. 'All 11' columns are projections, not a working app.", ONE),
          what_if_table(),
          P("Abbreviations: W scam warning, T type name, P payee check, C cooling-off, 2D second device, F face, K device key, O authenticator code, PK passkey, TC trusted contact, CB call-back. "
            "Green: allowed with no steps. Amber: checks required. Red: blocked. n/a: the balance cannot cover the amount.", SMALL),
          P("What the projection shows", H2)]
story += bullets(["<b>Remote control moves from Block to checks</b> for Asha and Ramesh (K + P + W). The device key is the thing that makes it safe to let the genuine owner through.",
                  "<b>The 1-second device key (K) appears on most HIGH payments.</b> It is almost free (Rs 0.5), so the engine adds it as insurance against takeover. Whether to always ask a fingerprint at HIGH is a product choice, not a law of nature.",
                  "<b>SIM swap stays blocked</b> even with all 11, as the engine is set up. The engine assumes it is only 80% sure which family it faces, so it keeps a 20% worry about coached and bad-payee scams that identity checks cannot stop. "
                  "If it trusts the router (97% right for takeover and coached), it picks payee check + trusted contact + bank call-back instead of blocking. That is a tunable assumption, and it shows how much the result depends on it.",
                  "<b>Digital arrest and investment plans barely change.</b> The coached scams still rest on the same warning, name check and wait: more checks do not help a tricked owner, only people and time do.",
                  "<b>Blatant scams stay blocked, and fake sellers stay missed.</b> Checks cannot create a signal the model never saw."]) + [
    P("These are the engine's choices under assumed pass rates and costs. Change an assumption (say, the face check's genuine-rejection rate) and a different check wins. Treat the table as an illustration of how the system would behave, not a promise.", NOTE)]

# ================================================================================================================= 8 copy
story += [PageBreak(), P("8. What the customer reads: warnings and reassurance", H1),
          P("The same engine can leave a customer feeling protected or harassed. These are the rules the messages follow, and the wording used or proposed.", ONE),
          table([["Moment", "Tone", "Wording (built messages quoted; proposed ones marked)"],
                 ["LOW risk", "Silent", "Nothing is shown. The PIN pad appears."],
                 ["MEDIUM: a WARN check", "Calm, short, no accusation", "'This looks like a common scam. Scammers often ask for payment during a call or chat. Banks and government agencies never do.' (built)"],
                 ["HIGH: a PAYEE check", "Factual", "'Registered name: Profit Growth Trading. Account age: 3 day(s), very new. Is this who you mean to pay?' (built)"],
                 ["HIGH: a PROVE check", "Reassuring: it is for the customer's protection", "'We want to be sure it is you. Confirm on your other device.' (proposed). After success: 'Thanks, that is you. One last step: your PIN.' (proposed)"],
                 ["The hold", "Patient, practical", "'This payment is on a short hold.' with four tips and the helpline (built)"],
                 ["A danger answer", "Firm, kind", "'You answered yes to a scam warning question, so we stopped this payment. Call 1930 (national cyber-crime helpline) or your bank before paying.' (built)"],
                 ["Engine block", "Firm, no detail", "'We stopped this payment to protect your money. Call 1930 or your bank before paying.' (built)"],
                 ["A PROVE check fails", "Protective", "'We could not confirm it is you, so we stopped this payment. If this was not you, your account has been locked for new payments.' (proposed)"]], [0.2, 0.22, 0.58]),
          P("Rules for every message", H2)]
story += bullets(["<b>Plain words, one idea per screen.</b> No scores, thresholds or jargon.",
                  "<b>Never reveal why in a way that teaches an attacker.</b> The model X-ray exists only in demo mode. A real app shows 'we stopped this' and the helpline, not 'the payee was 3 days old'.",
                  "<b>Always give a next step:</b> the helpline 1930 or the bank.",
                  "<b>Warn, do not accuse.</b> The customer is usually the victim, not the criminal.",
                  "<b>Never ask for a PIN, OTP or face over a phone call</b>, and say the bank never does."])

# ================================================================================================================= 9 build roadmap
story += [PageBreak(), P("9. How to build the seven that are missing", H1),
          P("The engine and the challenge service do not change. Adding a check is a small, repeatable job.", ONE),
          table([["Step", "What to do", "Where"],
                 ["1", "Write the check's <b>start</b> (what the customer sees, what the server keeps secret) and <b>check</b> (judge an answer: pass, retry or fail).", "demopay/backend/authn/methods.py"],
                 ["2", "Set <b>implemented=True</b> in its registry entry. If it needs enrolment (a second device, a contact, a key), set universal=False and enrol the customer.", "authn/registry.py; UserAuthMethod table"],
                 ["3", "Add a <b>step screen</b> to the web app and Android app.", "frontend/src/components/steps.jsx; android ui/PayScreen.kt"],
                 ["4", "Add <b>tests</b>: pass, wrong answer, expiry, three tries, a replayed answer on another payment.", "demopay/backend/tests"],
                 ["5", "Replace the <b>assumed pass rates</b> with logged outcomes (the challenge table already logs status, attempts and seconds).", "registry.py; AuthChallenge table"]], [0.07, 0.63, 0.30]),
          Spacer(1, 3 * mm),
          table([["Check", "Effort", "Needs", "Order", "Why this order"],
                 ["8 Authenticator code", "Small", "QR on an enrol page; standard library", "1", "Quickest to see an identity check working end to end; but the weakest in practice."],
                 ["5 Second device", "Medium", "Approver enrolment, delay, pending list; a second browser tab", "2", "Unblocks the takeover cases; easiest to demonstrate."],
                 ["7 Device key / 9 Passkey", "Medium", "WebCrypto or WebAuthn on web; Keystore and BiometricPrompt on Android", "3", "Nearly free friction; the best experience of the PROVE checks."],
                 ["10 Trusted contact", "Medium", "Invite flow, request page, notification", "4", "The check most likely to defeat coaching; fits older customers."],
                 ["6 Face + liveness", "Large", "Camera permission, existing services, privacy design", "5", "Heaviest and 12.7% genuine rejection; use only when no second device."],
                 ["11 Bank call-back", "Large", "Agents or voice service; a pre-SIM-change number", "6", "Operational more than technical; for CRITICAL only."]], [0.2, 0.09, 0.33, 0.07, 0.31])]

# ================================================================================================================= 10 limits
story += [PageBreak(), P("10. Honest limits and what to measure", H1),
          P("What this document does not show", H2)]
story += bullets(["<b>Only four checks work.</b> The other seven are designs. The 'all 11' results are projections from our own engine with assumed numbers.",
                  "<b>Nothing is measured on real traffic.</b> The model is trained on a simulator we wrote; the pass rates and costs of every check are our estimates. The figures only show the pipeline behaves sensibly.",
                  "<b>The model misses scams that look ordinary</b> (fake seller, prize fee, brand-new accounts with no history). No check helps if the engine is never triggered.",
                  "<b>Warnings do not stop a coached owner who lies.</b> Only a person (trusted contact), time (cooling-off), or a human voice (call-back) does.",
                  "<b>PROVE checks do not stop a tricked owner.</b> They pass happily. They are for takeover.",
                  "<b>A never-seen phone can look less risky to the model</b> (found while preparing the first guide, not yet fixed). That weakens exactly the takeover cases the PROVE checks are meant for.",
                  "<b>Bank call-back after a SIM swap</b> reaches the thief unless the number was captured earlier.",
                  "<b>Profile data</b> is barely used: only age changes the policy."])
story += [P("What to measure once there is real use", H2),
          table([["Number", "Why it matters", "Healthy sign"],
                 ["Share of genuine payments that get any check", "The friction budget", "Under 2% at MEDIUM and above"],
                 ["Pass rate of each check, for genuine customers", "Replaces the assumed 85% to 100% numbers", "Matches or beats the assumption"],
                 ["Scam attempts that got past each check", "Replaces the assumed 'gets past' numbers", "Falling over time"],
                 ["Seconds each check takes", "Time budget", "Under 10 s at MEDIUM, 30 s at HIGH"],
                 ["Blocks later confirmed genuine", "False-alarm cost", "Low, with a fast appeal path"],
                 ["Confirmed scams the model never flagged", "The misses", "Reviewed every week"]], [0.38, 0.34, 0.28]),
          P("Glossary", H2),
          table([["Term", "Plain meaning"],
                 ["WARN / PROVE", "WARN gives a tricked owner a chance to stop. PROVE makes a possible thief show they are the owner."],
                 ["Takeover / coached / bad payee", "Someone else pays / the owner is tricked / the receiving account is the problem."],
                 ["Severity", "LOW, MEDIUM, HIGH, CRITICAL: how surprising a payment is compared with genuine ones."],
                 ["Decision engine", "Picks the cheapest mix of checks for a payment, using assumed friction and pass rates."],
                 ["Challenge", "One run of one check, tied to one payment by a one-time code."],
                 ["Projection", "What the engine would choose if the unbuilt checks existed. Not a test of a working feature."],
                 ["Usual phone / new phone", "A phone the customer's history was recorded on / one the model has never seen."]], [0.28, 0.72])]

doc = BaseDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title="How the 11 checks work in DemoPay", author="Poojan Pandya", subject="Flow, fraud triggers and the 11 checks of the DemoPay prototype")
doc.addPageTemplates([PageTemplate(id="m", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")],
                                   onPage=make_footer("DemoPay: how the 11 checks work | 4 built, 7 designed | all data simulated; projections use assumed numbers"))])
doc.build(story)
print("wrote", OUT)
