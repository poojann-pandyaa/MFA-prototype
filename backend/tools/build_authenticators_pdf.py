"""
Builds docs/authenticators-explained.pdf: every authenticator we could build in this prototype, explained simply.

    cd backend && .venv/bin/python -m tools.build_authenticators_pdf
"""
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "authenticators-explained.pdf"
NAVY, TEAL, AMBER, GRID, LIGHT, GREEN, RED = (colors.HexColor(c) for c in ("#14325c", "#1b7f8c", "#b86b00", "#c4cfdb", "#eef3f8", "#2e7d4f", "#a32020"))
ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.6, leading=13.6, spaceAfter=5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.5, spaceAfter=3)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.2, leading=10.4, spaceAfter=0)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
HEADC = ParagraphStyle("headc", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=NAVY, spaceBefore=2, spaceAfter=3, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=10.8, leading=14, textColor=TEAL, spaceBefore=7, spaceAfter=3, keepWithNext=1)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2.5)
ONE = ParagraphStyle("one", parent=BODY, fontName="Helvetica-Oblique", fontSize=10.5, leading=14.5, textColor=colors.HexColor("#33445c"), spaceAfter=6)
NOTE = ParagraphStyle("note", parent=BODY, fontSize=9, leading=12.5, textColor=colors.HexColor("#5a3a00"), backColor=colors.HexColor("#fff4de"),
                      borderColor=AMBER, borderWidth=0.6, borderPadding=6, spaceBefore=4, spaceAfter=10)
COVT = ParagraphStyle("covt", parent=BODY, fontName="Helvetica-Bold", fontSize=27, leading=32, textColor=NAVY, spaceAfter=8)
COVS = ParagraphStyle("covs", parent=BODY, fontSize=13, leading=18, textColor=TEAL, spaceAfter=14)
W = 174 * mm


def P(t, st=BODY):
    return Paragraph(t, st)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths, head=True, first_bold=False):
    data = []
    for i, r in enumerate(rows):
        data.append([c if not isinstance(c, str) else Paragraph(c, HEADC if (head and i == 0) else (CELLB if (first_bold and j == 0) else CELL)) for j, c in enumerate(r)])
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, GRID), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    st += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2 if head else 1, len(rows), 2)]
    t.setStyle(TableStyle(st))
    return t


def flow(steps, color=TEAL, h=62):
    """A strip of boxes joined by arrows. Each step is (title, small text line 1, line 2)."""
    n = len(steps)
    gap = 10
    bw = (W - gap * (n - 1)) / n
    d = Drawing(W, h + 4)
    for i, s in enumerate(steps):
        x = i * (bw + gap)
        col = color if i % 2 == 0 else NAVY
        d.add(Rect(x, 4, bw, h - 4, fillColor=col, strokeColor=col, rx=4, ry=4))
        d.add(String(x + bw / 2, h - 16, s[0], textAnchor="middle", fontName="Helvetica-Bold", fontSize=8.2, fillColor=colors.white))
        for k, line in enumerate(s[1:]):
            d.add(String(x + bw / 2, h - 30 - 10 * k, line, textAnchor="middle", fontName="Helvetica", fontSize=6.7, fillColor=colors.white))
        if i < n - 1:
            ax = x + bw + 1
            d.add(Line(ax, h / 2 + 2, ax + gap - 4, h / 2 + 2, strokeColor=colors.HexColor("#555555"), strokeWidth=1.1))
            d.add(Polygon([ax + gap - 4, h / 2 + 5, ax + gap - 4, h / 2 - 1, ax + gap - 0.5, h / 2 + 2], fillColor=colors.HexColor("#555555"), strokeColor=colors.HexColor("#555555")))
    return d


def footer(c, doc):
    c.saveState()
    c.setFont("Helvetica", 7.5); c.setFillColor(colors.HexColor("#666666"))
    c.drawString(18 * mm, 10 * mm, "Authenticators explained | pass rates and costs are ASSUMPTIONS unless marked as measured")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    c.setStrokeColor(GRID); c.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    c.restoreState()


def section(num, title, one, steps, user, protects, cannot, build, facts, watch, story=None):
    """One authenticator = one page."""
    s = [PageBreak(), P(f"{num}. {title}", H1), P(one, ONE), flow(steps), Spacer(1, 2 * mm), P("What the user sees", H2)]
    s += bullets(user)
    s += [P("What it protects against, and what it cannot stop", H2),
          table([["Protects against", "Cannot stop"], [protects, cannot]], [0.5, 0.5]), P("How we would build it here", H2)]
    s += bullets(build)
    s += [P("Quick facts", H2), table([["", ""]] + facts, [0.28, 0.72], head=False, first_bold=True) if False else table([["Fact", "Value"]] + facts, [0.28, 0.72], first_bold=True), P("Watch out for", H2)]
    s += bullets(watch)
    if story:
        s += [P(story, NOTE)]
    a = AFTER.get(num)
    if a:
        s += [PageBreak(), P(f"{num}. {title}: using it after it is built", H1), P("When the engine asks for it", H2)]
        s += bullets(a["when"])
        s += [P("Setting it up and using it day to day", H2)] + bullets(a["daily"])
        s += [P("For the bank or product team: how to run and configure it", H2), table([["Setting or task", "How"]] + a["ops"], [0.3, 0.7], first_bold=True),
              P("What to watch once it is live", H2), table([["Number to watch", "Healthy sign", "Warning sign"]] + a["monitor"], [0.32, 0.34, 0.34], first_bold=True),
              P("When something goes wrong", H2)]
        s += bullets(a["fail"])
        s += [P("Example in practice: " + a["example"], NOTE)]
    return s


AFTER = {
 1: dict(
  when=["The risk model says takeover doubt is the biggest worry (new phone, SIM change, remote control) and the user has an approver device enrolled.",
        "Preferred over a second on-device step whenever the paying phone might be compromised: a different device is a different channel.",
        "Typical trigger: a large payment to a new account soon after a SIM change or new binding."],
  daily=["Setup (one time): Settings, Security, Add approver device. Sign in on the second phone, confirm with an existing step, name it. New approvers wait a day before they count.",
         "Use: a notification arrives. The user reads the payee and amount, taps the number that matches the one on the paying phone. Takes about 15 seconds.",
         "Housekeeping: users can rename or remove approver devices; removing one is delayed and the other channels are notified."],
  ops=[["Approver count and delay", "Require one approver for full limits; delay new approvers 24 hours (planned settings)"],
       ["Challenge expiry and tries", "Expire in about 2 minutes; lock after 3 wrong picks"],
       ["Endpoints (planned)", "List pending approvals for a device; submit the chosen number; the existing confirm endpoint still refuses until approval is recorded"],
       ["Decision engine", "Register the method as channel 'other device'; tell the engine it answers identity doubt only"]],
  monitor=[["Approval rate for genuine users", "90% or more", "Under 80%: push is not arriving or UI is confusing"],
           ["Median response time", "Under 30 seconds", "Over 2 minutes: users are not seeing it"],
           ["Expiry rate", "Under 10%", "Over 15%: delivery problem"],
           ["Decline rate", "Low and steady; each decline goes to review", "A spike is an early sign of an attack wave"]],
  fail=["Second phone lost: use recovery codes or a trusted contact, then re-enrol after the delay; notify all channels.",
        "Approver offline: the engine falls back to another step instead of blocking.",
        "Approver session was created by an attacker: that is why enrolment is delayed and notified."],
  example="A user's SIM is swapped at night. A payment of Rs 80,000 shows number 47. The owner's tablet shows the payee and amount; they decline. The decline appears in the review queue and the UPI link is locked."),
 2: dict(
  when=["The risk model says the owner may be tricked (call in progress, collect request from a stranger, large payment to a new account) and the user has a contact enrolled.",
        "Usually paired with a warning and a hold; it is the step that most often defeats digital-arrest scams.",
        "Not used for takeover doubt on its own: a contact cannot tell if the person on the phone is the owner."],
  daily=["Setup (one time): add one to three contacts. Each contact accepts an invite link. Run a practice request so the contact knows what to expect.",
         "Use: the user taps 'Ask my contact'. The contact gets a link with the payee, amount and a plain question and replies Approve, Decline or Call.",
         "While waiting the payment stays on hold and the user sees tips and the helpline 1930."],
  ops=[["Contacts per user", "One to three; adding or changing a contact is delayed and notifies existing channels"],
       ["Response window", "For example 10 minutes; no answer keeps the payment on hold"],
       ["What the contact sees", "Only this payment. Never the user's other payments or balance"],
       ["Request limits", "Cap requests per day so a contact is not spammed"],
       ["Endpoints (planned)", "Invite, accept, create request, respond; the confirm endpoint refuses until the response is recorded"]],
  monitor=[["Share of users with a contact enrolled", "Rising toward the target (for example 60%)", "Flat and low: needs a nudge campaign"],
           ["Contact response time", "Median under 5 minutes", "Over 30 minutes: contact not engaged"],
           ["Declines that match later confirmed scams", "High", "Low: wording may not be working"],
           ["User abandon rate at this step", "Under 30%", "Over 40%: users avoid asking"]],
  fail=["Contact unreachable: stay on hold, offer a call-back or a second contact.",
        "User refuses to involve anyone: the engine falls back to a hold plus warning, or blocks if risk is critical.",
        "Contact is also being scammed or approves without reading: add a short question the contact must answer."],
  example="Ramesh tries to send Rs 2.4 lakh while on a long call. His daughter's phone shows 'Do you know why Ramesh is paying Rs 2.4 lakh to a new account?'. She presses Decline and calls him. The payment is cancelled and logged as a prevented scam."),
 3: dict(
  when=["Medium to high risk, especially a larger payment to a new payee, or a repeating pattern of new payees.",
        "The cheapest intent step, so the engine often uses it first and adds a stronger step only if identity doubt is also high.",
        "Not a good identity check: a thief can also wait."],
  daily=["The user sees a hold screen with a countdown, a Cancel button, a short list of what scammers say, and the helpline number.",
         "A notification arrives when the hold ends and the payment can continue.",
         "Emergency path: 'I need this now' triggers a stronger on-the-spot check (for example the second-device approval) instead of a wait."],
  ops=[["Hold length", "30 minutes in the code today (HOLD_MINUTES); consider amount bands; demo mode shortens to about 20 seconds with a visible label"],
       ["Enforcement", "Already in the repo: the confirm endpoint refuses until the release time passes (uses server time)"],
       ["Cancel", "Add a cancel endpoint (planned) and show it on the hold screen"],
       ["Reminders", "Edit the message text without code changes"]],
  monitor=[["Cancel rate on flagged payments", "A few percent, higher for confirmed scam patterns", "Zero: users may not see the cancel button"],
           ["Abandon rate of genuine payments", "Low", "Rising: hold too long or too broad"],
           ["Support tickets about delays", "Few", "Many: tune bands and the emergency path"],
           ["Scams that complete after a hold", "Falling", "Rising: scammers adapting; add a stronger step"]],
  fail=["Urgent genuine need (hospital, rent): offer the emergency path, never leave the user stuck.",
        "User changes the phone clock: not possible, the server holds the clock.",
        "Hold applied too often: lower the budget at MEDIUM and retune thresholds."],
  example="A first payment of Rs 40,000 to a new account while the user is on a call is held for 30 minutes. The user mentions it to a friend, who says it sounds like a scam. The user cancels in the countdown screen."),
 4: dict(
  when=["Medium risk, and as the companion to a hold or trusted-contact step at high risk.",
        "Collect requests from non-contacts always get the 'This will SEND money' version.",
        "Aimed at the tricked-owner family; it adds no proof of identity."],
  daily=["A user rarely sees it (the budget is about 2% of genuine payments at the prompt level). They read the message, answer one or two questions and continue or cancel.",
         "Questions are picked at random from a bank of questions so they are not memorised."],
  ops=[["Message text and translations", "Edit in one message file; no code change needed; review tone with support staff"],
       ["Read time", "5 seconds before Continue turns on; tune by A/B testing"],
       ["Question bank", "Add, retire and weight questions; retire questions nobody answers differently"],
       ["Frequency cap", "Limit how many times one user sees it per month"]],
  monitor=[["Stop rate after a warning", "Higher for confirmed scam patterns than for genuine payments", "Equal: the warning is not changing behaviour"],
           ["Time on screen", "Close to the minimum read time or longer", "Under the minimum: users are tapping through"],
           ["Repeat exposure per user", "Rare", "Frequent: warning fatigue"],
           ["Complaint rate", "Low", "High: wording sounds accusing"]],
  fail=["Warning fatigue: tighten the trigger and cap exposure.",
        "Wrong language: fall back to the user's chosen language and then English.",
        "Users coached to answer 'no': pair with a hold or a trusted contact for higher risk."],
  example="A collect request for Rs 900 arrives from a number the user does not know. The app says 'This request will SEND money from your account. You never enter a PIN to receive money.' The user declines."),
 5: dict(
  when=["Takeover doubt is high, the user has a face enrolled and a working camera, for example the first payment on a new phone or right after a SIM change.",
        "Used alongside a different-channel step when the risk is very high, because a compromised phone can feed the camera.",
        "Not useful against a tricked owner."],
  daily=["Setup (one time): the user enrols a face photo at sign-up.",
         "Use: the camera opens, a prompt asks for a blink or a head turn, a short capture runs, the result appears. A failed attempt sends the user back to step 1 for a fresh challenge."],
  ops=[["Thresholds", "MFA_LIVENESS_THRESHOLD (0.5), MFA_FACE_MATCH_THRESHOLD (0.45), blink and head-turn thresholds; re-calibrate with ml/eval.py on your own data"],
       ["Challenge variety", "Today only three challenges; add more so a pre-recorded clip rarely matches"],
       ["Attempt limits", "Add a rate limit on restarting step 1 (planned)"],
       ["Payment hook", "Already works: /txn/risk issues a session, /login/step2 verifies, /txn/{id}/confirm releases"],
       ["Privacy", "Move face matching onto the phone so the server stores no face template (planned)"]],
  monitor=[["Genuine rejection rate", "About 13% for liveness alone in the repo's calibration, steady", "Rising: lighting, camera or model drift"],
           ["Retry count and abandon rate", "Low", "High on certain phones: test those devices"],
           ["Pass rate by group (age, device price band)", "Similar across groups", "A group far below the rest: fairness issue"],
           ["Red-team results (quarterly)", "Replay and photo attacks fail", "Any success: tighten and add challenges"]],
  fail=["Camera denied or bad light: offer a non-face alternative; never lock the user out without one.",
        "Repeated failures: lock the step and alert; offer the second-device approval or a trusted contact.",
        "Face changed (injury, beard, glasses): re-enrol through the recovery flow."],
  example="A thief tries the first payment on a new phone. The camera asks for a blink; the thief holds up a photo of the owner. The liveness model rejects it and the payment stays blocked."),
 6: dict(
  when=["As a quiet identity step for medium or higher risk when the phone is the one the user normally uses.",
        "As the signature on every approved payment so the exact payee and amount are tamper-evident.",
        "At high takeover doubt it is combined with a different-channel step, because malware on the phone can still ask the key to sign."],
  daily=["Setup: the key is created the first time the user signs in on a device. Nothing to remember.",
         "Use: the user taps Approve; signing happens in the background.",
         "New phone: the key does not exist, so the user goes through re-enrolment, which the decision engine treats as a risky event."],
  ops=[["Key rotation", "For example yearly, or when the user asks"],
       ["Revocation", "Settings page lists keys per device; revoking removes the public key"],
       ["Challenge expiry and nonces", "Expire in about 60 seconds; keep a nonce store to block replay"],
       ["Browser support list", "Maintain which browsers can keep non-extractable keys"]],
  monitor=[["Signature failure rate for genuine users", "Near zero", "A spike: a bug or an attack"],
           ["Details-mismatch failures", "Near zero", "Any rise: someone is altering payee or amount (alert immediately)"],
           ["Re-enrolment volume", "Steady, tied to phone changes", "Spike: browser data clearing or an attack"],
           ["Key age", "Mostly under a year", "Old keys everywhere: rotate"]],
  fail=["Lost phone: revoke the key, use recovery codes, re-enrol after the delay.",
        "Browser data cleared: the key is gone; tell users clearly and offer re-enrolment.",
        "Suspected malware: lock the UPI link and require a different-channel step."],
  example="Malware swaps the payee after the user taps Approve. The signed details no longer match the payment, verification fails, an alert is raised and the payment is stopped."),
 7: dict(
  when=["Identity step for users who have no second device.",
        "Account recovery and sensitive settings changes.",
        "Not useful for tricked-owner scams: victims read the code out."],
  daily=["Setup: scan a QR with an authenticator app and print or store 8 to 10 recovery codes.",
         "Use: type the 6-digit code when asked. Codes change every 30 seconds.",
         "After using a recovery code it stops working; the user is told to make new ones."],
  ops=[["Time window", "Accept one step either side; refuse reuse of the same step"],
       ["Lockout", "Lock after 5 wrong tries and notify the user"],
       ["Storage", "Encrypt secrets at rest with a managed key; store recovery codes hashed"],
       ["Support", "Support staff cannot read codes; recovery is by recovery codes only"]],
  monitor=[["Failure rate for genuine users", "Under 5%", "Higher: clock drift; widen window or tell users to fix the time"],
           ["Recovery code use", "Rare", "Spike: users losing phones, or an attack"],
           ["Reuse attempts", "Near zero", "Any rise: someone is replaying codes"],
           ["Enrolment coverage", "Growing", "Flat: nudge users"]],
  fail=["Lost phone: use a recovery code, re-enrol; notify all channels.",
        "Clock drift: guide the user to sync time.",
        "Codes read out to a caller: the engine should not rely on TOTP alone when the tricked-owner score is high."],
  example="A user switches phones. They open the old recovery codes, use one to sign in, then scan a new QR on the new phone. The used code is crossed off."),
 8: dict(
  when=["As the main identity step on supported devices: lets us drop passwords and codes.",
        "As the strong step for high takeover doubt, with the payee and amount hash in the challenge.",
        "Not enough on its own for a large payment to a new payee when the tricked-owner score is high."],
  daily=["Setup: Settings, Security, Add passkey. The phone asks for fingerprint, face or screen lock.",
         "Use: a system prompt appears; the user confirms; done in a few seconds. No codes to type.",
         "Users should add a second passkey (laptop or security key) as a back-up."],
  ops=[["Relying-party id and origin", "localhost for the demo; the real domain in production"],
       ["User verification", "Require it so a thief needs the fingerprint or screen lock"],
       ["Credential store", "Public key, credential id, signature counter; check the counter for cloning"],
       ["Payment binding", "Include a hash of payee and amount in the server challenge"],
       ["Support matrix", "Maintain which browsers and OS versions work; keep a fallback"]],
  monitor=[["Registration success rate", "High", "Low on certain browsers: add guidance or fallback"],
           ["Authentication success rate", "98% or more", "Falling: browser or OS change"],
           ["Counter anomalies", "None", "Any: possible cloned credential, review"],
           ["Fallback usage", "Small", "Large: poor support or confusing flow"]],
  fail=["Lost device: use another passkey or recovery codes, then re-enrol after the delay.",
        "Unsupported browser: fall back to TOTP or second-device approval.",
        "Synced passkeys can be copied by account sync: decide the trust level in policy."],
  example="A user confirms a payment with their fingerprint on the phone. The server verifies the signed challenge for this exact payee and amount and releases the payment."),
 9: dict(
  when=["Runs silently on every payment; its score is already a model input (bio_similarity).",
        "A very low score adds a step (for example second-device approval); it never blocks on its own."],
  daily=["Invisible. The template is learned over the first sessions on the phone and kept there.",
         "On a new phone the template starts over, so the engine leans on other signals for a while."],
  ops=[["Enrolment sessions", "Set how many sessions before the score is trusted"],
       ["Score smoothing", "Average over several inputs to cut noise"],
       ["Fairness testing", "Compare score distributions across groups (tremor, one-handed use, older users)"],
       ["Kill switch", "Turn the signal off if it drifts or causes unfair results"],
       ["Privacy review", "Document what is stored (a template on the device) and what is sent (a score)"]],
  monitor=[["Score distribution drift (PSI)", "Stable (the repo has a PSI function)", "Large shift: new phones or a bug"],
           ["Low-score rate for genuine users by group", "Similar across groups", "A group far higher: unfair"],
           ["Contribution to catch rate", "Positive in ablation tests", "Zero: remove or improve"],
           ["Complaints about extra steps", "Few", "Many: threshold too tight"]],
  fail=["Injury, a different hand or a new phone: re-learn gradually and rely on other signals.",
        "Shared phone: do not treat a low score alone as fraud.",
        "Template corruption: reset it and re-learn."],
  example="A remote-control tool types the PIN at a robotic pace. The similarity score is very low, so the engine adds a second-device approval to the payment."),
 10: dict(
  when=["The payee side of the model is the most doubtful: first payment to a new payee above a threshold, look-alike ids, fake-seller patterns, accounts flagged by fraud-risk data.",
        "Also used to complete a plan when the user has few other methods."],
  daily=["The user sees the bank-registered name and confirms it matches who they mean to pay.",
         "For larger payments a tiny test credit is sent; the user confirms the amount with the payee."],
  ops=[["Directory source", "Mock in the demo; a bank or UPI directory in production"],
       ["Test-credit rules", "Amount, frequency and which payments need it"],
       ["Fraud-risk feeds", "Plug in government fraud-risk bands and complaint counts when available"],
       ["Exemptions", "Verified merchants can skip the test credit"]],
  monitor=[["Name-mismatch rate", "Low and explained", "High: stale directory or confusing wording"],
           ["Cancel rate after seeing the name", "Some, especially on risky payees", "Zero: users not reading"],
           ["Directory outages", "Rare", "Frequent: build the fallback path"],
           ["Test-credit completion", "High", "Low: too much effort for users"]],
  fail=["Directory down: fall back to a hold plus warning.",
        "Nicknames cause false mismatches: show clear wording ('registered name may differ').",
        "A matching name does not prove honesty: keep the fraud-risk signal in the model."],
  example="A user is about to pay 'ramesh.kumar1@okaxis', one character off a saved payee. The app says 'Did you mean ramesh.kumar@okaxis?' and shows the registered name. The user corrects it."),
 11: dict(
  when=["Critical risk with a large amount, or after the user fails other steps.",
        "For users who have no strong digital method enrolled.",
        "Kept rare because staff time is expensive."],
  daily=["The user sees 'Your bank will call you to confirm this payment' and the payment stays on hold.",
         "A trained agent calls the registered number, asks scripted questions and records the result."],
  ops=[["Staffing and hours", "Agent pool, call windows and service levels"],
       ["Script and questions", "Who is the payee, how did you find them, is anyone pressuring you; keep them reviewed"],
       ["Escalation", "No answer after a set time: stay on hold and try again; repeated no-answer: block and alert"],
       ["Records", "Call outcome logged with the decision; recording only with consent"],
       ["Case system", "Link call outcomes to the transaction record"]],
  monitor=[["Answer rate", "High", "Low: numbers outdated or call timing"],
           ["Confirmed-scam share among calls", "A useful share", "Near zero: triggers too loose"],
           ["Average handling time and cost", "Stable", "Rising: scripts too long"],
           ["Customer satisfaction", "Neutral or positive", "Negative: process feels heavy"]],
  fail=["Number swapped by a SIM swap: use a second channel to verify.",
        "Scammers pose as the bank: remind users the bank never asks for PIN or OTP.",
        "Staff overload: raise the trigger threshold or add self-service steps."],
  example="A critical payment of Rs 2.4 lakh is held. The agent asks who the payee is; the user says a 'police officer' told them to move money. The agent stops the payment and files a report."),
}


story = []

# ---------------- cover ----------------
story += [Spacer(1, 32 * mm), P("Authenticators explained", COVT), P("What each one is, how it works, what it stops, and how we would build it", COVS),
          P("A plain-language guide to every authenticator we could build in this prototype (FastAPI backend, React web app), "
            "and how a decision engine chooses between them when a payment looks risky."),
          Spacer(1, 6 * mm),
          table([["What", "A guide for builders and reviewers, written without jargon. Each authenticator has two pages: what it is and how we would build it, and how it is used once it is built (when the engine asks for it, day-to-day use, how the team runs it, what to watch, and what to do when it fails)."],
                 ["Covers", "8 recommended authenticators, 3 that would be mocks for now, 4 we recommend skipping, and a chapter on running the whole set after launch."],
                 ["Status", "Face + liveness exists in the repo. Everything else is a design; none of it is built yet."]], [0.14, 0.86], head=False, first_bold=True),
          Spacer(1, 8 * mm),
          P("<b>Read this first.</b> The pass rates and friction costs in this guide are estimates, not measurements. The only measured figure is the face liveness check "
            "(8.7% of fake faces accepted, 12.7% of genuine users rejected, from the repo's own calibration on one test set). Real numbers need real logs.", NOTE)]

# ---------------- why many ----------------
story += [PageBreak(), P("Why we need more than one authenticator", H1),
          P("An authenticator is a way of checking that a payment is really what it looks like. A single check cannot cover every scam, because scams attack different things:"),
          table([["The doubt", "Plain question", "Typical scam", "Checks that help", "Checks that do not help"],
                 ["Identity", "Is this really the owner?", "SIM swap, stolen phone, malware, remote control", "second-device approval, passkey, face, device key", "SMS code (a SIM swap reads it)"],
                 ["Intent", "Is the owner acting freely and with the facts?", "digital arrest, fake customer care, investment, romance", "trusted contact, cooling-off hold, warning, call-back", "face, passkey, PIN, codes (a victim passes them or reads them out)"],
                 ["Payee", "Is the receiver legitimate?", "mule account, fake seller, look-alike payee", "payee verification, warning with payee details", "any identity check"]], [0.11, 0.2, 0.22, 0.25, 0.22]),
          P("A face check on a digital-arrest victim passes, and the money still leaves. That single fact is why one strong check is not enough.", BODY),
          P("How the decision engine chooses", H2)]
story += bullets(["It starts from the risk model's three scores (takeover, being tricked, bad payee) and turns them into a rough chance that this payment is a scam.",
                  "It lists every option: allow, one to three checks the user actually has set up, or block.",
                  "It prices each option: the annoyance of the steps, plus the chance a scam gets through times the amount, plus the cost of wrongly stopping a genuine payment.",
                  "It picks the cheapest option, then updates its estimate after each result. If the doubt stays too high, it blocks.",
                  "A bank sets the dials (how much risk to accept). The engine does not decide that."])
story += [P("Rules every authenticator in this guide follows", H2)]
story += bullets(["<b>Tied to the payment.</b> An approval covers one payee, one amount, one moment. It cannot be reused.",
                  "<b>Short life, single use.</b> Challenges expire quickly and work once.",
                  "<b>Limited tries.</b> Too many wrong attempts lock the step and alert the user.",
                  "<b>Logged.</b> Every outcome (passed, failed, abandoned, time taken) is recorded as counts, so real pass rates can replace our estimates later.",
                  "<b>No home-made cryptography.</b> We use standard libraries and write only the flow around them.",
                  "<b>Protected enrolment.</b> Adding a new authenticator is itself a risky event: it is delayed and the user's other channels are notified."])

# ---------------- overview ----------------
story += [PageBreak(), P("The authenticators at a glance", H1),
          table([["#", "Authenticator", "Answers", "Effort", "Server can verify?", "Verdict"],
                 ["1", "Second-device approval with number matching", "identity", "Medium", "Yes", "Build first"],
                 ["2", "Trusted-contact approval", "intent", "Medium", "Yes", "Build first"],
                 ["3", "Cooling-off hold", "intent, payee", "Small", "Yes", "Build first"],
                 ["4", "Scam warning, questions and typed confirmation", "intent", "Small", "Partly", "Build first"],
                 ["5", "Face + liveness", "identity", "Built", "Yes", "Keep, add a non-face alternative"],
                 ["6", "Own device key with payment signing", "identity, payee tampering", "Medium", "Yes", "Build second"],
                 ["7", "TOTP code and recovery codes", "identity", "Small", "Yes", "Build second"],
                 ["8", "Real passkey (WebAuthn)", "identity", "Medium", "Yes", "Build second"],
                 ["9", "Behavioural biometrics (typing and touch rhythm)", "identity (passive)", "Medium to large", "Partly", "Later"],
                 ["10", "Payee verification (name match, test credit)", "payee", "Medium", "Needs bank data", "Mock for now"],
                 ["11", "Bank call-back", "identity, intent", "Small (mock)", "Needs bank staff", "Mock for now"],
                 ["12", "SMS or email code; NFC tap; voice; knowledge questions", "mixed", "-", "-", "Skip (see page at the end)"]],
                [0.05, 0.37, 0.16, 0.12, 0.15, 0.15])]

# ---------------- individual authenticators ----------------
story += section(
    1, "Second-device approval with number matching",
    "In one sentence: the payment is approved on a second phone the user already owns, and the user must pick the right number, so nobody can approve by accident or by being worn down.",
    [("Pay", "user starts a", "payment"), ("Show number", "phone A shows", "a 2-digit number"), ("Push", "phone B gets", "the details"), ("Match", "user picks the same", "number on phone B"), ("Release", "payment moves on", "to the next step")],
    ["On the paying phone: a screen saying \"Approve on your other device\" with a two-digit number, for example 47.",
     "On the second phone: the payee, the amount, and three numbers. The user taps the one that matches 47. Tapping the wrong number or Decline stops the payment.",
     "If the second phone does not respond in about two minutes, the step expires and the payment goes back to the engine, which may choose another step."],
    "Account takeover: SIM swap, a thief with the paying phone, malware that moved UPI to another phone, remote control of the paying phone. The attacker would also need the second phone.",
    "A tricked owner (they approve themselves). An attacker who also holds the second phone. A second phone that is lost or left unlocked.",
    ["<b>Server:</b> a challenge record tied to the payment (payee, amount, nonce, expiry, the correct number). Endpoint to fetch pending approvals for the second device, and one to submit the chosen number.",
     "<b>Second device:</b> a signed-in session of the same user on another browser or phone, registered as an approver at enrolment. Polling every few seconds (or WebSocket) shows pending approvals.",
     "<b>Safety:</b> approval is single-use and tied to one payment; three wrong picks locks it; the approver session must differ from the paying device; enrolling the approver is a delayed, notified action.",
     "<b>Tables:</b> a list of the user's enrolled approver devices, and a record of each challenge and its outcome."],
    [["Answers", "Identity"], ["Channel", "A different device"], ["Server verifies", "Yes: it records which number was chosen, by which enrolled device"], ["Effort", "Medium"],
     ["Assumed pass rates", "Genuine user 95%; takeover attacker 3%; tricked owner 100%"], ["Friction (assumed)", "About 15 seconds"], ["Privacy", "Sends only the payment details to the user's own device"],
     ["Accessibility", "Works without a camera; needs a second device"]],
    ["Approval fatigue: that is why the number must be matched and not just tapped.", "Users with only one device: the engine must have another option for them.",
     "If the first phone is compromised and the same browser session approves itself: the approver must be a separate enrolled device."])

story += section(
    2, "Trusted-contact approval",
    "In one sentence: before a risky payment goes out, someone the user trusts is asked, \"Do you know why this payment is being made?\" and has to say yes.",
    [("Pay", "risky payment", "is flagged"), ("Ask", "a trusted person", "gets a secure link"), ("Context", "contact sees payee,", "amount, a question"), ("Decide", "approve, decline", "or call user"), ("Release", "or stop the", "payment")],
    ["The user sees \"We would like a second opinion. Ask your trusted contact.\" They choose their contact (set up earlier).",
     "The contact opens a link on their own phone and sees the amount and payee with a plain question, for example \"Do you know why Ramesh is paying Rs 2.4 lakh to a new account?\" with Approve, Decline and Call buttons.",
     "A decline or no answer in time keeps the payment on hold. The user is told, kindly, that scammers often pressure people to hide a payment from family."],
    "Being tricked: digital arrest, fake customer care, investment, romance scams. A calm third person often breaks the scammer's script. Also helps seniors and anyone who is isolated by the scammer.",
    "A user who refuses to involve anyone (scammers often say \"do not tell anyone\"). A contact who approves without reading. An attacker who controls the contact's phone.",
    ["<b>Enrolment:</b> the user adds one to three contacts; each contact confirms by opening an invite link and (optionally) creating a minimal account. Adding or changing a contact is delayed and notifies the old channels.",
     "<b>Challenge:</b> a one-time secure link with an expiry, showing only what the contact needs. Rate limits, and the contact cannot see the user's other payments.",
     "<b>Engine rule:</b> offer it mainly for intent doubt (the tricked-owner family), and never let the user's own device approve for the contact.",
     "<b>Tables:</b> contacts, invitations, challenge outcomes."],
    [["Answers", "Intent (and a little identity)"], ["Channel", "Another person"], ["Server verifies", "Yes: it records the contact's decision"], ["Effort", "Medium"],
     ["Assumed pass rates", "Genuine user 85%; takeover attacker 5%; tricked owner 25% (contacts dissuade or victims avoid asking)"], ["Friction (assumed)", "About 3 minutes"],
     ["Privacy", "The contact sees only the one payment they are asked about"], ["Accessibility", "Good for people who cannot use cameras or biometrics"]],
    ["Contacts who are slow to answer: keep an expiry and a fallback.", "Do not shame the user. The wording should be supportive.", "Minors need a guardian flow with consent; that is a separate project."])

story += section(
    3, "Cooling-off hold",
    "In one sentence: a risky payment waits for a short time before it is sent, and the user can cancel it at any point.",
    [("Pay", "risky payment", "is flagged"), ("Hold", "payment waits;", "timer starts"), ("Think", "user can cancel,", "call, or ask someone"), ("Reminder", "a message says", "what to check"), ("Release", "after the timer", "ends")],
    ["A screen: \"This payment is on hold for 30 minutes. You can cancel it at any time.\" with a countdown and a Cancel button.",
     "While waiting: a plain list of what scammers say (\"stay on the line\", \"do not tell anyone\", \"move money to a safe account\") and the helpline number 1930.",
     "When the timer ends the payment can continue, usually with the PIN step."],
    "Being tricked and fast draining. Time breaks the pressure that scammers rely on, lets other people notice, and lets reports about a payee reach the bank.",
    "A user determined to pay after the timer. A scammer who keeps the victim on the line through the whole wait. A thief who simply waits (a hold is a weak identity check).",
    ["<b>Already in the repo:</b> payments carry a <font name='Courier'>release_at</font> time; <font name='Courier'>/txn/{id}/confirm</font> refuses until it passes. The hold length is a setting (30 minutes).",
     "<b>To add:</b> a countdown screen, a cancel endpoint, a reminder message, and a shortened hold (about 20 seconds) in demo mode with a clear label.",
     "<b>Engine rule:</b> the hold is cheap, so it is a good first step for medium-large payments to new payees; it is paired with a stronger step when identity doubt is high.",
     "<b>Tables:</b> existing transaction table (no change needed)."],
    [["Answers", "Intent and payee"], ["Channel", "Time"], ["Server verifies", "Yes: the server enforces the wait"], ["Effort", "Small"],
     ["Assumed pass rates", "Genuine user 90% (some abandon); takeover attacker 40%; tricked owner 35%"], ["Friction (assumed)", "The wait itself, about 30 minutes"], ["Privacy", "None"],
     ["Accessibility", "Works for everyone; urgent cases need a fast alternative"]],
    ["Emergencies (hospital, rent): offer a stronger on-the-spot check instead of a wait.", "UPI payments cannot be undone, so the hold must happen before sending.", "A very long hold makes users abandon the app."])

story += section(
    4, "Scam warning, questions and typed confirmation",
    "In one sentence: a full-screen message that interrupts the scammer's script, followed by a couple of short questions that make the user think.",
    [("Pay", "risky payment", "is flagged"), ("Warn", "full-screen note;", "5-second read"), ("Ask", "short questions:", "\"Is someone on a call?\""), ("Type", "type payee's", "registered name"), ("Choose", "continue or", "cancel")],
    ["A screen with the exact scam pattern, for example \"Banks and government agencies never ask you to pay during a call.\" The Continue button turns on after 5 seconds.",
     "Randomised yes/no questions such as \"Did someone call you and tell you to make this payment?\" and \"Were you told to keep this secret?\". Some answers lead straight to the helpline.",
     "Optional typed confirmation: the user types the payee's bank-registered name or the last digits, which breaks autopilot."],
    "Being tricked, especially first-time victims who simply never heard the scam pattern. Costs almost nothing and works for every user.",
    "A victim who is coached to answer \"no\". Takeover (it adds no identity proof). People who click through warnings.",
    ["<b>Already in the repo:</b> the message texts for each decision are in the API (including the \"This request will SEND money\" text for collect requests).",
     "<b>To add:</b> an interstitial screen with a forced read, a small question bank with random picks, an optional typed confirmation, and a record of what was answered (counts only).",
     "<b>Engine rule:</b> use as the first light step for medium risk, and as a companion to a hold or trusted-contact step at high risk.",
     "<b>Tables:</b> answers recorded as part of the step outcome."],
    [["Answers", "Intent"], ["Channel", "Education and attention"], ["Server verifies", "Partly: it records answers, but cannot know if they were honest"], ["Effort", "Small"],
     ["Assumed pass rates", "Genuine user 100%; takeover attacker 100%; tricked owner proceeds 60% (40% stop)"], ["Friction (assumed)", "About 8 seconds"], ["Privacy", "None"],
     ["Accessibility", "Needs plain language, local-language text and readable contrast"]],
    ["Warning fatigue: use it only when the model says it is needed.", "Do not accuse the user. Keep the tone helpful.", "Translate carefully: tone matters as much as words."])

story += section(
    5, "Face check with liveness (already built)",
    "In one sentence: the user looks at the camera and does a random action (blink, turn left, turn right) so the system can tell a real, present face from a photo or video.",
    [("Challenge", "server picks blink", "or head turn"), ("Capture", "13 frames, one", "every 150 ms"), ("Liveness", "anti-spoof model", "scores each frame"), ("Gesture", "did the action", "really happen?"), ("Match", "face matches the", "enrolled face?")],
    ["A camera screen that says \"Look straight at the camera, press the button, then blink once\" (or turn the head).",
     "If any check fails the user sees one generic message, so an attacker cannot learn which check failed. Each attempt uses up its session and the user starts again.",
     "Success unlocks the payment (or login)."],
    "Identity takeover: a stolen phone, a new device, SIM swap with the thief holding a different face. Random challenges defeat a single printed photo.",
    "A tricked owner (their real face passes). A good deepfake or a replay that happens to match the requested action. People who cannot use a face check (face coverings, some disabilities, bad lighting).",
    ["<b>Already in the repo:</b> MediaPipe face landmarks, an anti-spoofing model served with ONNX Runtime, ArcFace face matching, gesture checks, the challenge UI, and the payment hook that unlocks a payment after step 2 passes.",
     "<b>To add:</b> a non-face alternative for users who cannot use it, more challenge types (there are only three today, so a prerecorded clip works one time in three), attempt limits on step 1, and moving face matching onto the phone (the server stores a face template today).",
     "<b>Tables:</b> existing."],
    [["Answers", "Identity"], ["Channel", "The paying device's camera"], ["Server verifies", "Yes"], ["Effort", "Built"],
     ["Measured", "Liveness model only: 8.7% of fake faces accepted and 12.7% of genuine users rejected on the repo's test set"],
     ["Assumed pass rates", "Genuine 87%; takeover attacker 9%; tricked owner 100%"], ["Friction (assumed)", "About 20 seconds"], ["Privacy", "Face template stored server-side today: should move on-device"],
     ["Accessibility", "Needs an alternative (second device or trusted contact)"]],
    ["One in eight genuine users is rejected by the liveness step alone: always offer a retry or alternative.", "Face matching is less reliable at the youngest and oldest ages (NIST demographic study).",
     "The rate limit for restarting step 1 is missing, so a clip can be retried."])

story += section(
    6, "Own device key with payment signing",
    "In one sentence: the phone holds a secret key it can use but not copy; to approve a payment it signs the payment's details, so an approval cannot be reused or changed.",
    [("Enrol", "browser makes a key;", "server keeps public half"), ("Pay", "server sends challenge", "with payee + amount"), ("Sign", "phone signs a hash", "of the details"), ("Verify", "server checks the", "signature"), ("Release", "exact payment", "only")],
    ["Mostly invisible: the user taps Approve and the phone signs in the background. If the payee or amount shown differ from what was signed, verification fails.",
     "On a new phone the key does not exist, so a new enrolment is needed (which is itself a risky event handled by the engine)."],
    "Replayed or stolen approvals, a server breach that leaks approvals, and malware that changes the payee or amount after the user approved (the signature no longer matches).",
    "Malware on the same phone that asks the key to sign (the key is usable even if not copyable). No hardware proof (a real passkey gives that). A tricked owner.",
    ["<b>Browser:</b> WebCrypto creates an ECDSA P-256 key pair marked non-extractable and stores it in IndexedDB. A sign call covers payee, amount, nonce and expiry.",
     "<b>Server:</b> stores the public key per device, issues a one-time challenge, verifies the signature with the <font name='Courier'>cryptography</font> library, and checks the signed details equal the stored payment.",
     "<b>Safety:</b> constant-time checks, nonce store to stop replay, expiry, and key rotation on re-enrolment.",
     "<b>Tables:</b> device public keys, challenge nonces."],
    [["Answers", "Identity and tamper-proofing"], ["Channel", "The paying device"], ["Server verifies", "Yes (signature)"], ["Effort", "Medium"],
     ["Assumed pass rates", "Genuine 98%; takeover attacker 5% if the attacker lacks the phone; tricked owner 100%"], ["Friction (assumed)", "Almost none"], ["Privacy", "Only a public key is stored"],
     ["Accessibility", "Good: invisible to the user"]],
    ["Do not claim hardware security: browser keys have no hardware attestation.", "Lost phones: provide recovery codes and a delayed re-enrolment.", "Combine with a different-channel step when takeover risk is high."])

story += section(
    7, "TOTP code and recovery codes",
    "In one sentence: an authenticator app shows a 6-digit code that changes every 30 seconds; recovery codes are a printed back-up for when the phone is lost.",
    [("Enrol", "scan a QR; app", "stores a secret"), ("Pay", "user is asked", "for the code"), ("Type", "6 digits from", "the app"), ("Verify", "server checks code", "for this moment"), ("Release", "if it matches", "and is unused")],
    ["One time: scan a QR with an authenticator app and print 8 to 10 recovery codes.",
     "Each time: type the 6-digit code. If it is wrong three times the step locks.",
     "Lost phone: use one recovery code, which then stops working, and re-enrol."],
    "Takeover by someone who does not have the secret: SIM swaps (it does not use SMS), a stolen password, a leaked phone number.",
    "A tricked owner who reads the code out to the scammer. Phishing pages that ask for the code in real time. Malware that reads the app.",
    ["<b>Standard:</b> RFC 6238 (HMAC-SHA1, 30-second steps, 6 digits), implementable with Python's standard library. Allow one step either side, and refuse a code that was already used.",
     "<b>Storage:</b> keep the shared secret encrypted; store recovery codes hashed and mark each used.",
     "<b>Safety:</b> constant-time compare, attempt limits and lockout, and a notification when codes are used or regenerated.",
     "<b>Tables:</b> secrets, recovery codes, last-used time step."],
    [["Answers", "Identity"], ["Channel", "An authenticator app (often on the same phone)"], ["Server verifies", "Yes"], ["Effort", "Small"],
     ["Assumed pass rates", "Genuine 96%; takeover attacker 10%; tricked owner 100% (they read it out)"], ["Friction (assumed)", "About 10 seconds"], ["Privacy", "A secret is stored; protect it"],
     ["Accessibility", "Needs a screen reader friendly flow and large digits"]],
    ["Not a defence against being tricked: scammers ask for the code and victims read it out.", "If the app is on the same phone as the banking app, it is not a second device.", "Treat recovery as a high-risk step."])

story += section(
    8, "Real passkey (WebAuthn)",
    "In one sentence: the phone or a security key proves the user is present using fingerprint, face or screen lock, and the proof only works for our site, so it cannot be phished.",
    [("Enrol", "browser creates a", "passkey for our site"), ("Pay", "server sends a", "random challenge"), ("Unlock", "user touches finger", "or looks at the phone"), ("Sign", "device signs the", "challenge"), ("Verify", "server checks it", "with the public key")],
    ["A system prompt from the phone or laptop: \"Confirm with fingerprint\" (or face or PIN). No codes are typed.",
     "The same device cannot sign on a fake website, because the passkey is bound to our site's address.",
     "A hardware security key can be used instead of the built-in sensor."],
    "Phishing, SIM swap, stolen passwords, and server-side leaks (the server only holds public keys). The user-verification step means a thief needs the fingerprint or screen lock too.",
    "A tricked owner (they approve themselves). A thief who knows the phone's screen lock. Malware that tricks the user at the moment of approval.",
    ["<b>Server:</b> the <font name='Courier'>py_webauthn</font> library for registration and authentication; store credential id, public key and counter; require user verification; set the expected origin and relying-party id.",
     "<b>Browser:</b> the standard WebAuthn calls. It works on HTTPS or on localhost, which suits a demo.",
     "<b>Payment binding:</b> include a hash of payee and amount in the server challenge so the approval is for this payment only.",
     "<b>Tables:</b> credentials, challenges."],
    [["Answers", "Identity"], ["Channel", "The device's secure sensor, or a security key"], ["Server verifies", "Yes"], ["Effort", "Medium"],
     ["Assumed pass rates", "Genuine 98%; takeover attacker 5%; tricked owner 100%"], ["Friction (assumed)", "About 4 seconds"], ["Privacy", "Only public keys stored; biometrics never leave the device"],
     ["Accessibility", "Good, with alternatives (security key, PIN)"]],
    ["Browser and device support varies: keep a fallback.", "Synced passkeys can be copied to other devices by the user's account: decide how much to trust them.", "Do not let a passkey alone clear a high-risk payment to a new payee."])

story += section(
    9, "Behavioural biometrics (typing and touch rhythm)",
    "In one sentence: the app quietly watches how the user types and taps and compares it with the user's usual rhythm, so no extra step is needed.",
    [("Enrol", "learn rhythm on", "the phone"), ("Watch", "key timing, touch", "speed and pressure"), ("Compare", "to the user's own", "template"), ("Score", "similarity from", "0 to 1"), ("Use", "feeds the risk", "model")],
    ["Nothing. It runs in the background while the user types the amount or the PIN.",
     "If the score is very low, the engine may ask for another step, for example the second-device approval."],
    "Thieves and remote control tools, which type and swipe differently. It works continuously and costs the user nothing.",
    "A careful attacker who copies the rhythm (hard). Normal variation: tired users, one hand, a new phone, a child using the phone, an older user with tremor. Short inputs give noisy scores.",
    ["<b>Browser:</b> capture key down and up times and touch or pointer events on the PIN pad and amount field, producing a small set of numbers (hold times, gaps, speed).",
     "<b>On-device template:</b> a per-user profile kept on the phone; the server sees only the similarity score, which is already an input of our risk model (<font name='Courier'>bio_similarity</font>).",
     "<b>Scorer:</b> a small one-class model (for example a robust distance to the user's template). It needs enrolment sessions before it is trusted.",
     "<b>Safety:</b> never use it as the only check; low score should add a step, not block."],
    [["Answers", "Identity (passive)"], ["Channel", "The paying device"], ["Server verifies", "Partly: it only receives a score from the device"], ["Effort", "Medium to large"],
     ["Assumed pass rates", "Genuine users with a good template about 95%; takeover attacker about 35%; tricked owner 100%"], ["Friction (assumed)", "None"], ["Privacy", "Template stays on the device; only a score is sent"],
     ["Accessibility", "Risk of unfair scores for people with tremor or one-handed use: test and keep alternatives"]],
    ["Do not use as a hard gate.", "Needs a fair-treatment check across groups.", "A web demo has a weaker signal than a native app."])

story += section(
    10, "Payee verification (mock for now)",
    "In one sentence: before money moves, the app checks that the receiving account is who the user thinks it is.",
    [("Pay", "user enters a", "payee id"), ("Look up", "bank-registered", "name appears"), ("Compare", "user confirms it", "is who they expect"), ("Optional", "tiny test credit;", "user confirms amount"), ("Release", "if it matches")],
    ["The app shows the bank-registered name (UPI apps have shown only this name since 30 June 2025, per reports) and asks \"Is this who you mean to pay?\".",
     "For higher risk, a small test credit lands in the payee account and the user confirms it with the payee, which proves the account is live and held by that person.",
     "A look-alike id (one letter off a saved payee) triggers \"Did you mean...?\"."],
    "Mule accounts, look-alike ids, fake sellers, wrong-person payments.",
    "A scammer with a genuine account under a believable name. Accounts that look fine but are rented by criminals.",
    ["<b>Here:</b> mock it. A fake directory returns a registered name for each demo payee. The look-alike check already exists inside the risk model (<font name='Courier'>payee_lookalike</font>).",
     "<b>Real world:</b> needs bank or UPI directory access and rules on the test credit; plug in government fraud-risk data if available.",
     "<b>Engine rule:</b> the preferred step when the payee head of the model is most doubtful."],
    [["Answers", "Payee"], ["Channel", "Payee side"], ["Server verifies", "Needs bank data"], ["Effort", "Medium (mock is small)"],
     ["Assumed pass rates", "Genuine 97%; a scam payee passes about 30%"], ["Friction (assumed)", "About 10 seconds"], ["Privacy", "Uses the payee's registered name"], ["Accessibility", "Good"]],
    ["A real test credit costs money and has rules.", "A match does not prove the payee is honest."])

story += section(
    11, "Bank call-back (mock for now)",
    "In one sentence: the bank calls the user on the registered number, asks a few questions, and only then releases a large risky payment.",
    [("Flag", "very risky", "payment is held"), ("Call", "bank calls the", "registered number"), ("Ask", "scripted questions:", "\"Did you start this?\""), ("Decide", "agent or script", "releases or stops"), ("Record", "outcome logged")],
    ["A message: \"Your bank will call you to confirm this payment.\" Then a call the user answers.",
     "The agent asks who the payee is, how the user found them, and whether anyone is pressuring them."],
    "Many scams: a trained person can hear coaching in the user's voice and stop the payment. Strong against takeover, because the number is the registered one.",
    "SIM swaps that redirect the call. Victims who are coached to say everything is fine. Cost and delay.",
    ["<b>Here:</b> a scripted screen in the demo that plays the call and shows the questions. No real telephony.",
     "<b>Real world:</b> needs bank staff, telephony and a record-keeping system. A separate project.",
     "<b>Engine rule:</b> reserve for critical risk and large amounts."],
    [["Answers", "Identity and intent"], ["Channel", "The bank, by phone"], ["Server verifies", "Needs bank staff"], ["Effort", "Small as a mock; large for real"],
     ["Assumed pass rates", "Genuine 90%; takeover attacker 2%; tricked owner 30%"], ["Friction (assumed)", "About 5 minutes"], ["Privacy", "Bank-held"], ["Accessibility", "Good for people who cannot use a screen"]],
    ["Calls are themselves used by scammers: tell users the bank never asks for PIN or OTP.", "Staff cost limits how often it can be used."])

# ---------------- skip page ----------------
story += [PageBreak(), P("Authenticators we recommend skipping, and why", H1),
          table([["Authenticator", "Plain explanation", "Why skip"],
                 ["SMS or email code", "A code sent by text or email that the user types in.", "A SIM swap reads it and a tricked user reads it out. Build only as the weakest baseline, if at all."],
                 ["NFC tap (card or tag)", "Hold the phone near a card or tag to confirm presence.", "Web NFC works only in Chrome on Android, so it is too narrow."],
                 ["Voice recognition", "The user speaks and the system matches the voice. (A voice-matching branch exists in the repo.)", "AI voice cloning can fake it. If used, give it very low weight."],
                 ["Knowledge questions about past payments", "\"Which of these was your last payee?\"", "Uses private payment history, is easy for family to guess, and conflicts with the privacy goal."],
                 ["Cross-device QR (later)", "Phone B scans a QR on phone A to prove the two are together.", "Overlaps with second-device approval; build only if that proves too weak."]],
                [0.25, 0.37, 0.38])]

# ---------------- combinations ----------------
story += [P("Which authenticators work together", H1),
          P("The engine picks steps for the doubt that is highest. These are example combinations, using the assumed numbers; real choices depend on the amount, the user's enrolled methods and the bank's risk settings."),
          table([["Situation", "Doubt", "Likely steps", "Why"],
                 ["Small payment, little risk", "none", "none, or a quick device prompt", "Annoying genuine users costs more than it saves"],
                 ["SIM-swap or new-device takeover, large amount", "identity", "second-device approval + payee check + short wait", "Needs a different channel from the compromised phone"],
                 ["Same, user has only a face enrolled", "identity", "face + payee check + wait", "Best available; lower assurance"],
                 ["Digital arrest, large amount", "intent", "warning + trusted contact + call-back; or block", "Identity checks pass for a victim"],
                 ["Collect request from a stranger", "intent and payee", "\"This will SEND money\" warning + hold", "Cheap and aimed at the trick"],
                 ["Investment or task scam, third payment to new account", "intent", "warning, then hold, then trusted contact", "The pattern across payments is the signal"],
                 ["Look-alike payee id", "payee", "\"Did you mean...?\" + payee verification", "Aimed at the wrong-payee risk"],
                 ["Very high doubt, nothing strong enrolled", "all", "block, tell the user how to enrol a second device or contact", "Never leave a dead end, never pretend to be certain"]],
                [0.27, 0.12, 0.33, 0.28])]

# ---------------- plumbing + order ----------------
story += [PageBreak(), P("What we build once: the shared challenge plumbing", H1),
          P("Every authenticator uses the same skeleton, so we write it once and plug each authenticator into it."),
          table([["Piece", "What it does"],
                 ["Challenge service", "Creates a challenge tied to one user, one device, one payment (payee, amount, nonce); sets an expiry; allows it to be used once."],
                 ["Attempt limits and lockout", "Counts wrong tries, locks the step, and alerts the user."],
                 ["Common interface", "Every authenticator implements: enrol, create challenge, verify response, which channel it uses, accessibility flags, whether it needs a camera or second device."],
                 ["Outcome log", "Records passed, failed, abandoned and time taken as counts. These numbers replace our assumed pass rates once real data exists."],
                 ["Enrolment guard", "Adding, removing or changing an authenticator is a risky event: delayed, notified, and rate limited."],
                 ["Decision engine hook", "Receives the list of methods the user has enrolled and returns a plan with the reason for each step."]], [0.25, 0.75]),
          P("Suggested build order", H2),
          table([["Order", "Build", "Reason"],
                 ["1", "Shared plumbing", "Everything else sits on it"],
                 ["2", "Scam warning with questions, and the cooling-off hold", "Small effort, covers the tricked-owner scams"],
                 ["3", "Second-device approval with number matching", "Best identity check we can build here"],
                 ["4", "Trusted-contact approval", "Best check against being tricked"],
                 ["5", "TOTP and recovery codes", "Quick and gives a lost-phone path"],
                 ["6", "Own device key with payment signing, then real WebAuthn", "Tamper-proof approvals"],
                 ["7", "Behavioural biometrics", "Passive signal for the model"],
                 ["8", "Mocks: payee verification, bank call-back", "Complete the demo story"]], [0.1, 0.5, 0.4]),
          PageBreak(), P("Running the authenticators after launch", H1),
          P("Once the authenticators are built, the decision engine picks among them automatically for each payment. People still have to roll them out, watch them, and keep them honest. This chapter is the playbook."),
          P("1. Roll out in stages", H2),
          table([["Stage", "What happens", "Move on when"],
                 ["0. Shadow", "Turn on MFA_ATO_SHADOW=1. The engine scores every payment and records the step it would choose, but adds no friction.", "Logs are complete; the strong-action rate on genuine payments is near the 0.3% budget"],
                 ["1. Light steps, small group", "Enforce warnings and holds for a small share of users (for example 1 to 5%). Keep a comparison group with no friction.", "Fraud completes less in the enforced group; genuine completion and support tickets stay healthy"],
                 ["2. Stronger steps", "Add second-device approval, trusted contact and face for high severity.", "Pass rates match expectations; no segment is far above the rest"],
                 ["3. Full enforcement", "All users, all severities, with the block path.", "Reviewed monthly; changes go through change control"]], [0.2, 0.5, 0.3]),
          P("2. Get people enrolled", H2)]
story += bullets(["Offer enrolment at moments of high motivation, for example right after a large payment or a phone change.",
                  "Give a reason people feel: higher payment limits for users with a second device or a trusted contact.",
                  "Keep a minimum set (face plus one other-channel method) for full limits, and always offer an alternative for people who cannot use a camera.",
                  "Help older users and their families set up together; a practice request for trusted contacts removes the fear of the first real one."])
story += [P("3. The numbers to watch every week", H2),
          table([["Number", "Why it matters"],
                 ["Scams that complete, and the rupees lost", "The result we care about"],
                 ["Genuine payments completed without a strong action", "The cost to ordinary users; compare to the 0.3% budget"],
                 ["Pass, fail and abandon rate per authenticator, with time taken", "Replaces our assumed pass rates with real ones"],
                 ["Friction per user per month, by segment", "Finds groups that are bothered too much (new users, light users, older users, low-end phones)"],
                 ["Drift of the model's inputs (PSI) and of scores", "Warns when the real world has changed"],
                 ["Declines from approvers and trusted contacts", "An early warning of attack waves"]], [0.5, 0.5]),
          P("4. The monthly tuning loop", H2)]
story += bullets(["Replace the assumed pass rates and costs with measured ones from the outcome log.",
                  "Re-run the policy simulation with the new numbers and look at what changes in the chosen steps.",
                  "Review the bank's risk dials (how much loss to accept, cost of wrongly stopping a payment) and change them only through change control.",
                  "Re-check fairness across segments before and after each change."])
story += [P("5. Support playbook", H2)]
story += bullets(["Lost phone: recovery codes or another enrolled method, then re-enrol after the delay, with all channels notified.",
                  "Locked out: never bypass checks by phone; use the alternative methods and, if needed, a branch visit.",
                  "Disputed block: show the plain-language reason and offer the call-back path; keep an appeals log.",
                  "Agents never ask for a PIN, a one-time code or a face scan over the phone, and tell users the bank never does either."])
story += [P("6. If an authenticator is compromised", H2)]
story += bullets(["Revoke the affected keys or secrets, force re-enrolment, notify users, and raise the engine's caution for that channel.",
                  "Run red-team tests each quarter (replay a recorded face challenge, try approval fatigue, try to enrol a rogue contact) and fix what passes.",
                  "Keep a decision audit: which steps were chosen and why, for explanation to users and regulators."])
story += [P("7. Privacy and compliance", H2)]
story += bullets(["Log counts and outcomes, not private payment details; set retention limits.",
                  "Keep personal templates (face, typing rhythm) on the phone once that is built; store only public keys and scores on the server.",
                  "Record consent under the data-protection law, and a way for users to see and challenge a decision."])
story += [
          P("Honest limits", H2)]
story += bullets(["All pass rates and costs are estimates. Different numbers can change which step is best, so the engine's choices must be tested for sensitivity and then replaced with real data.",
                  "Authenticators are not fully independent: a compromised phone defeats every on-device step at once. That is why the engine prefers a different channel when takeover doubt is high.",
                  "No authenticator makes a tricked owner safe. Warnings, time, and other people are the real defences there.",
                  "Which risks to accept is a business and regulatory choice, not something the AI decides. RBI's 2025 authentication directions allow risk-based extra checks but do not set these dials."])
story += [P("Glossary", H2),
          table([["Term", "Plain meaning"],
                 ["Authenticator", "Any check that helps prove a payment is genuine."],
                 ["Liveness", "Proof that a real, present person is in front of the camera, not a photo or a video."],
                 ["Passkey", "A secret stored in the device that signs in place of a password; works only on the real website."],
                 ["TOTP", "The 6-digit code that changes every 30 seconds."],
                 ["Out-of-band", "A check that travels on a different path than the payment, for example a second phone or a person."],
                 ["Nonce", "A one-time random value that makes each approval usable once."],
                 ["Friction", "How much a check annoys or delays the user."],
                 ["Mule account", "A bank account used to receive and move stolen money."]], [0.22, 0.78])]

doc = BaseDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title="Authenticators explained", author="Poojan Pandya", subject="Plain-language guide to authenticators for the payment-risk project")
doc.addPageTemplates([PageTemplate(id="m", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=footer)])
doc.build(story)
print("wrote", OUT)
