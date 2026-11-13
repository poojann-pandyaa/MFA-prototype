# Demo app plan: a mock banking app that uses the payment-risk model during payment

Status: plan only, nothing built. Stack is what this branch already has: FastAPI backend (`backend/`) and React 19 + Vite + Tailwind 4 + axios + lucide-react + react-router frontend (`frontend/`, existing pages Login, Enroll, Dashboard, and the `ChallengeCameraCapture` face challenge).

## 1. Purpose, audience, and what it must not claim

**Purpose:** let someone sit in front of a banking-style app, make payments, and watch the model decide in real time what happens: nothing, a prompt, a warning, a wait, a face check, or a block. A second panel shows what the model saw and why.

**It must show, on screen at all times:** "DEMO. Simulated bank, simulated money, simulated phone signals." Every metric shown carries "simulated data".

**It must not claim:** that it prevents all scams, or real-world accuracy. Each scenario card shows how often the model catches that scam in the simulation, with its range, including the weak ones (fake seller 50%, romance 77%).

**Name and look:** a neutral fictional bank, "DemoPay". Do not copy any real UPI app's name, logo, or colours.

## 2. What the audience sees

Two views in one browser page:

- **Phone view (left)**: the mock banking app in a phone-sized frame.
- **Model X-ray (right)**: what the phone sent, the three risk scores, the severity, the scam family chosen, and the top reasons (demo only; a real app never shows reasons to the user because they would help attackers).

A **Scenario Picker** (top) loads a persona and a scenario, then "Play" fills in the payment so the presenter only taps through.

### 2.1 App screens (phone view)

| Screen | What it does | Backed by |
|---|---|---|
| Sign in | Existing login; presenter enrolls their own face once (needed for the real face check) | existing `/enroll`, `/login/step1`, `/login/step2` |
| Home | DemoPay balance, recent payments, "Send money", "Scan QR", "Requests" | new demo ledger |
| Send money | Payee id (or pick saved), amount, note. Shows the bank-registered name | new |
| Requests inbox | Pending **collect requests** ("X is requesting Rs 900") | new, scripted |
| Scan QR | Pick a fake QR (merchant, friend, "stranger") | new, scripted |
| Risk screen | One of the variants in 2.2, driven by the model's answer | `POST /txn/risk` |
| PIN pad | Fake 4-digit PIN (any digits). Real PIN logic stays with UPI | new |
| Result | Success, held, blocked; updated balance | `POST /txn/{id}/complete` (new) |
| Activity | Ledger with a small badge on payments that were flagged | new |

### 2.2 What the risk screen shows for each model decision

| Model action | Screen | Behaviour |
|---|---|---|
| `allow` | None | straight to PIN pad |
| `device_biometric_reprompt` | "Confirm it's you" | Platform prompt (optional real WebAuthn/Touch ID, or a one-tap simulated prompt; labelled as a soft prompt the server cannot verify) |
| `face_challenge` | Existing blink/turn-head capture | uses the `step_up` session from `/txn/risk`, posts frames to `/login/step2`, then `/txn/{id}/confirm`. Demo fallback: "simulate pass/fail" button when no camera |
| `scam_warning` | Full-screen interstitial, 5-second read before "Continue" is enabled | collect-request variant: "This will SEND money. You never enter a PIN to receive money." |
| `cooling_off_hold` | Hold screen with countdown and Cancel | real hold is 30 minutes; demo compresses to 20 seconds (`MFA_DEMO_HOLD_SECONDS`) and says so; shows "Call 1930" and a greyed "Ask a trusted contact" (not built) |
| `hold_and_verify_payee` | Payee-risk hold | same countdown; shows why the payee looks risky |
| `payee_risk_badge` | Badge on the payee card, confirm button | |
| `block_*` | Block screen | "Stopped to protect your account", helpline 1930, report button; for takeover also "UPI link locked" |

## 3. How a payment flows (what actually happens)

```
User taps Pay
  -> phone view gathers: payee, amount, channel (pay/collect/qr), device id,
     and signals (in the demo, from the Scenario Picker or the signal toggles)
  -> POST /txn/risk  (auth required)
        backend replays the user's past payments -> same featurizer as training
        3 detectors + router -> severity, family, action
        stores the transaction; creates a face-challenge session if needed
  <- {action, message, status, hold_until, step_up}
  -> phone view shows the screen for that action
  -> (face challenge)  POST /login/step2  -> backend marks the txn unlocked
  -> POST /txn/{id}/confirm   (blocked, still on hold, or not yet verified = refused)
  -> PIN pad -> POST /txn/{id}/complete  (new: moves the demo money, updates balance)
  -> Result screen
Presenter panel: GET /demo/xray/{id}  (new, demo only) -> scores, thresholds, reasons
```

Three things are different in a demo and must be disclosed: signals such as "call in progress", "SIM changed 2 hours ago" or "remote control on" are **chosen by the presenter or scenario**, because a web page cannot read them; the hold is shortened; and the money is fictional.

## 4. Personas and scenarios

### 4.1 Personas (history loaded into the presenter's account)

Each persona's history is generated by the **same simulator code** that trained the model (`ato.simulate`: `make_user`, `gen_legit`), so the demo behaves like the model's world. Age is only story text; the model never sees age.

| Persona | Story | Simulator profile |
|---|---|---|
| Asha | busy professional, frequent phone changes | day person, heavy user, phone-change history |
| Ramesh | retiree, few payees | light user, small amounts (assisted mode is planned, not built) |
| Kabir | freelancer, pays late at night | night owl |
| Meera | brand-new user, 3 payments | cold start |

### 4.2 Scenarios (the demo script)

Each card shows: the story, which persona, the steps, what the model should do, and **its simulated catch rate with range**. Events come from the simulator's scam generators (`gen_takeover`, `gen_coached`) so inputs look like training data, not hand-tuned.

| # | Scenario | Steps | Expected | Simulated catch rate (from the evidence report) |
|---|---|---|---|---|
| 1 | Ordinary payment | Asha pays a saved friend Rs 300 | no friction | n/a (genuine) |
| 2 | New shop | pays a new merchant Rs 450 | no friction | n/a |
| 3 | Genuine new phone (shows a known weakness) | Asha signs in on a new phone and pays | extra check on the first payment; **say so on stage** | genuine users: about 5% get a face check |
| 4 | SIM swap | new SIM, new device, PIN reset, large payment to a young account | face check or block | 100% (24 incidents) |
| 5 | Remote-control takeover | screen-share on, odd touch pattern, payment to new payee | face check or block | 100% (19) |
| 6 | Digital arrest | Ramesh on a 90-minute call, pays Rs 2.4 lakh to a new account | hold or block with the interrupt message | 96% (28) |
| 7 | Fake customer care refund | collect request from a stranger while on a call | "This will SEND money" warning | 100% (34) |
| 8 | Fake QR / collect request | scan a QR to a personal id | warning or hold | 89% (44) |
| 9 | Investment/task scam | three rising payments to new accounts over "days" (replayed in one sitting) | warning, then hold | 100% (30) |
| 10 | Look-alike payee | payee id one letter off a friend's | payee hold | 74% (27) |
| 11 | Fake seller | normal-looking purchase to a flagged young merchant account | payee badge/hold | 50% (22): **may be missed; show a miss on purpose** |
| 12 | Romance scam | slow payments to one aged account | may pass | 77% (35): honest weak case |

For the weak scenarios, curate **both a hit and a miss** from held-out data and let the presenter choose. A demo that only shows hits would overstate the model.

### 4.3 Signal toggles (presenter control)

On/off switches that change what the phone "reports": call in progress (+ minutes), remote control, SIM changed (hours), new device (binding hours), PIN reset, payee flagged by the government fraud-risk list, attested SDK on/off. "SDK off" switches the model to the server-only version so the audience sees accuracy with fewer signals. A "shadow mode" switch shows the model's decision without applying it.

## 5. Build plan

### Phase 0: decisions and scaffold (S)
Pick answers in section 8. Add routes and a layout with phone frame + X-ray panel; add `MFA_DEMO=1` gating.

### Phase 1: demo backend (M)
New `backend/demo/` (loaded only when `MFA_DEMO=1`):
- `DemoAccount` table (balance) and ledger on the existing `Transaction` rows; **no changes to existing tables** (the SQLite file would not migrate).
- `POST /demo/persona/{name}`: wipe and seed the signed-in user's history from the simulator, set balance.
- `GET /demo/scenarios`, `POST /demo/scenarios/{id}/prepare`: returns a pre-filled request (payee, amount, channel, signals) and story steps. Sequence scenarios return several requests with simulated timestamps.
- `GET /demo/inbox`: pending collect requests for the scenario.
- `POST /txn/{id}/complete`: after `confirm`, moves demo money. Refuses if not approved.
- `GET /demo/xray/{txn_id}`: three surprises, thresholds, router family, top reasons, the exact inputs sent. Demo only.
- `POST /demo/reset`.
- `python -m demo.curate`: picks scenario events from held-out simulated data, runs them through the real model, and writes `scenarios.json` with expected outcomes and catch rates.

**Checks:**
- Test plays every scenario through `/txn/risk` and asserts the expected action **family** (identity check, warning/hold, payee hold, or none). Expected outcomes come from the real model, stored in `scenarios.json`, so a retrain that changes behaviour fails a test and forces a conscious update.
- `/txn/{id}/complete` cannot be called without a prior `confirm`; blocked payments cannot complete; money never goes negative.
- X-ray endpoint returns 404 when `MFA_DEMO` is off (so reasons can never leak in a real deployment).

### Phase 2: payment UI (M)
Files: `pages/Home.jsx`, `Send.jsx`, `Inbox.jsx`, `Scan.jsx`, `Activity.jsx`; components `RiskScreen.jsx` (one component, a variant per action in 2.2), `HoldCountdown.jsx`, `PinPad.jsx`, `PhoneFrame.jsx`; `api.js` wrapping axios and the token; reuse `ChallengeCameraCapture` for the face challenge.
**Checks:** drive each action variant in the built-in browser (`preview_start`): read the page to confirm the expected text and buttons, screenshot each screen, and check the console for errors. Confirm the Continue button on the warning stays disabled for 5 seconds, and that Back from a hold does not release the payment.

### Phase 3: Scenario Lab and X-ray (M)
`pages/Lab.jsx` with `ScenarioPicker.jsx`, `SignalToggles.jsx`, `XRay.jsx`. X-ray shows three bars (takeover / coached / payee surprise) against the MEDIUM/HIGH/CRITICAL lines, the family chosen, reasons in plain words, a table of inputs, and a "simulated data" ribbon. Scenario cards include catch rate and range.
**Checks:** every scenario card plays end to end without a manual edit; toggling a signal changes the X-ray result within one request; SDK-off switches the variant label.

### Phase 4: script and polish (S)
A 10 to 12 minute walkthrough in the scenario order above; an on-screen "Reset demo"; responsive layout; copy review (plain English, no jargon in the phone view; Hindi strings optional); accessibility pass on the warning and hold screens (focus, contrast, screen-reader labels).

### Phase 5, optional (S to M)
Screen recording or GIFs of each scenario; a one-command start (`launch.json` entry); deploy a read-only version with the camera step simulated.

## 6. Things the model cannot show yet (mock or leave out, say so on stage)

| Item | Status | Demo treatment |
|---|---|---|
| Trusted-contact approval | not built | greyed button labelled "planned" |
| Vouched phone transfer | not built | scenario 3 shows the current weakness honestly |
| Assisted mode for seniors | not built | persona note only |
| Real SIM-swap, call-state, remote-control signals | need native app / telecom | simulated by toggles, labelled |
| Real government fraud-risk data | not available | simulated flag |
| Real PIN and bank | out of scope | fake PIN pad, fake ledger |

## 7. Risks and how to handle them

- **Over-claiming.** Ribbon on every screen; catch rates beside every scenario including a deliberate miss; no "stops all scams" wording anywhere.
- **Camera trouble on stage.** Provide the simulate-pass fallback and test the real challenge beforehand.
- **Scenario drift after a retrain.** The scenario test in Phase 1 fails loudly; `demo.curate` regenerates expectations.
- **Leaking reasons.** X-ray gated by `MFA_DEMO`, 404 otherwise, covered by a test.
- **Looking like a real bank.** Fictional name and neutral styling; no real logos.
- **Presenter confusion between simulated and real.** Signal toggles carry a "this is chosen by you" label.

## 8. Decisions needed

1. **Layout:** phone-frame beside the X-ray panel (recommended for presenting) or full-width responsive app.
2. **Face challenge:** real camera with a simulated fallback (recommended) or simulated only.
3. **Unbuilt features:** leave greyed and labelled (recommended) or build trusted-contact approval first so the hold screen has a real second step.
4. **Languages:** English only first (recommended) or English + Hindi from the start.
5. **Audience:** bankers and technical reviewers (show the X-ray prominently) or a general audience (hide it by default, open on a click).

## 9. Prior art in this repo

The `integration` branch has a separate `examples/demo_bank` (FastAPI + static HTML) built on a different multi-service architecture (`amfa/`). It has payees with creation dates, a daily limit and pending-payment handling. I propose building the demo in this branch's stack so it runs against the model as it exists (`backend/ato/`), and borrowing ideas from that bank (payee age, daily limit) rather than porting it.
