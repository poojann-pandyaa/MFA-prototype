# Cold start without bank data: how to build it

A customer signs up with no history and no bank data. This plan covers the first payment through to the model fully knowing
them. Nine pieces, each with what it is, the data, the server, the app, the tests and what to watch out for. The order to
build them is at the end.

The rule throughout: **sign-up and the learning phase stay light.** No face or voice setup at sign-up, and no face or voice
checks while the model is still learning the person. A new customer approves payments with a one-time code, an authenticator
code, the phone's passcode or a fingerprint. Safety in that phase comes from signals that need no history (pieces 3 and 4),
limits, and hard warning signs, not from heavier checks.

Existing code this builds on: `amfa/engine/risk/readiness.py` (the readiness test), `amfa/engine/risk/features.py` (habit
score), `amfa/core/retention.py` (90 days), `/c/v1/enrollment/device` and `/c/v1/session/device` (device keys with Play
Integrity), `/v1/transactions/{id}/feedback` (labels).

---

## 1. Learning phase with light checks, and a gradual hand-over

**What.** Every customer is in one of two phases, and the model's weight grows with evidence instead of flipping on.

| | Learning | Ready |
|---|---|---|
| Usual payment | Fingerprint or passcode (device key) | Device key, or code + typing, by the model |
| Payment outside the learning limits, or a warning sign | Device key **and** a one-time code; held if severe | Face or voice by how unusual it is |
| Face and voice | Not asked. Offered once, after the 5th payment, as optional ("set up a face check so we can approve bigger payments") | Asked when the model needs them; set up then if missing |
| Model | Scores every payment; its level is recorded and is used **only** at T4 (near-certain fraud: hold) | Decides |

**Gradual, not a switch.** The habit score already shrinks a new customer's spread towards a generic one (`habit_z`, prior
weight `k`). Piece 2 makes that generic part come from people like them instead of one global number, so the model's view of
a new customer is reasonable from the first payment and becomes theirs as payments arrive. The phase switch then only decides
which checks are allowed, and that is what readiness controls.

**Data.** New table `customer_phase`:

| Column | Meaning |
|---|---|
| `customer_id` | key |
| `phase` | `LEARNING` or `READY` |
| `ready_since` | when it last became ready |
| `evidence` | JSON: the last 20 genuine payments' false-alarm marks, the log amounts, active days (what `readiness.Tracker` holds) |
| `updated_at` | |

It holds only derived marks, not transactions. It is trimmed with the 90-day window.

**Server.**
- `orchestrator`: when a payment's proof is consumed (it is verified and genuine as far as we know), load the tracker, call
  `observe(amount, false_alarm, at)` with the model's recorded level for that payment, save, and set `phase` from `ready()`.
- `decide.py`: a new first step, `learning_plan(ctx, features)`, used while `ctx.customer.phase == LEARNING`. It returns the
  device key (or OTP when the phone has no key), adds OTP above the learning limit or on a hard signal, and escalates to a
  hold only when the model says T4. The model's own level is still written to the plan (`modelLevel`) so readiness can be
  measured.
- A fraud report on a payment (`feedback` with FRAUD) resets the customer to LEARNING and clears the evidence.
- A drift check (piece 6) can also send a customer back.

**App.** No face setup screen at sign-up. Profile shows "We're getting to know how you pay" with a progress bar driven by
`readiness.why_not()` (for example "12 more payments"). After the 5th payment, one optional card offers face setup.

**Tests.** A learning customer is never asked for face or voice; T4 in learning still holds; a customer becomes ready
exactly when the tracker says so; a fraud report sends them back.

**Watch out.** The learning phase is when new-account fraud happens. Without face checks, pieces 3 and 4 and the learning
limits are the defence, so they ship before or with this one.

---

## 2. Sign-up questions and peer groups

**What.** Fill the empty start with two things: what the customer tells us, and how people like them behave.

**Sign-up questions** (three, all optional, about 20 seconds):
1. "Roughly how much do you send in a month?" (bands: under ₹10k, ₹10-50k, ₹50k-2L, ₹2L-10L, more)
2. "What's a typical payment for you?" (₹500, ₹2k, ₹10k, ₹50k, more)
3. "Anyone you pay regularly?" Add up to three payees now: rent, family, a supplier. Each is checked against the account
   holder's registered name (bank-side name lookup) before it counts.

**Peer groups.** A peer group is a coarse bucket: declared monthly band × declared typical payment × age band × city tier ×
sign-up channel. For each group, a nightly job computes, from customers already READY in it:
- median and spread of log payment amounts,
- payments per week,
- share of payments to repeat payees,
- usual hours (24-bucket histogram).

Groups with fewer than 50 ready customers fall back to their parent group (drop city, then age).

**How it is used.**
- In `features.habit_z`, the generic prior `prior=0.9, k=5` is replaced by the customer's peer group median and spread, with
  `k` meaning "how many payments the peer group is worth": start at 10, fall towards 2 as their own payments grow. A ₹40,000
  payment on day one is then judged against "people who said they send ₹50k-2L a month", not against nothing.
- Two new model features: `amount_vs_declared` (payment ÷ declared typical payment) and `declared_gap` (their actual monthly
  rate so far ÷ declared). Someone who declared ₹10k a month and moves ₹2 lakh in week one is a signal by itself.
- Pre-registered regular payees count as known payees from day one (`KnownPayee` with `source='declared'`), so rent in month
  one is not "a new payee".

**Data.** `customer_profile` (customer_id, declared_month_band, declared_typical, age_band, city_tier, channel,
declared_at); `peer_group_stats` (group_key, n_customers, median_log, spread_log, per_week, repeat_share, hours[24],
computed_at). Both are about the customer, not transactions; peer stats are aggregates of at least 50 customers, so no one
person is visible in them.

**Training.** The simulator gets the same questions: each simulated customer answers from their true behaviour with noise,
and a share of fraudsters lie (declare low, then move a lot). Retrain so the model learns the two new features.

**Tests.** Peer prior changes a day-one decision as intended; a group under 50 falls back; a lie in the declaration raises
the level; declared payees are known.

**Watch out.** Declarations are self-reported, so they can relax the checks only a little (they shift the prior; they never
skip a hard signal), but a big mismatch can raise them.

---

## 3. Signals that need no history, from day one

**What.** Facts about the phone and the account that catch new-account fraud and takeovers before any habit exists.

| Signal | How | Status |
|---|---|---|
| Phone integrity | Play Integrity verdict (device, app, account) and a Keystore key with key attestation, at sign-up via `/c/v1/enrollment/device` | Server done; the app does not call it yet |
| SIM binding | During sign-up the app sends an SMS from the phone's own SIM, with a one-time token, to a bank long-code number; the server matches sender number and token. Proves the number belongs to the phone in hand (the UPI pattern) | New |
| Rooted phone, emulator, debugger, overlay or accessibility apps | The integrity verdict plus a few on-device checks sent as booleans | New (app) |
| Account age in minutes | Sign-up time vs first payment | The data exists |
| One phone, many accounts | Count of customers per hashed device key and per hashed SIM number in 30 days | New (query) |
| SIM changed | Android `SubscriptionManager` id or carrier change since sign-up; or the bank's telco feed | New |

**Server.** New risk features `device_integrity` (0-3 from the verdict), `sim_bound` (0/1), `accounts_on_device_30d`,
`minutes_since_signup`; `hard_signals()` gains "rooted or emulator", "SIM not bound", "more than 2 accounts on this phone".
Those hold or step up in both phases.

**App.** Keystore key with `setUserAuthenticationRequired(true)` (fingerprint or screen lock) created at sign-up and attested;
the SMS step; the integrity token request. This also gives piece 1 its fingerprint/passcode factor: each payment AMFA sends a
challenge, the key signs it after the phone's own unlock, `/c/v1/session/device` checks the signature.

**Engine change needed.** Today a signed device challenge marks the session device-bound but is not a factor. Add
`Factor.DEVICE` (possession plus the phone's fingerprint or PIN), dynamic because it signs a fresh challenge, so it counts
towards the two-factor minimum with OTP or alone with the phone's biometric. The policy file decides whether a bank accepts
DEVICE as one or two factors.

**Tests.** A failed integrity verdict is a hard signal; a bound SIM and attested key approve a learning payment with one
fingerprint; a second key on another phone still needs the bar for a new phone.

**Watch out.** Play Integrity needs a Google Cloud project and the app's package registered; the SMS step needs a bank SMS
number. Both are setup, not code.

---

## 4. Payee reputation (mule detection)

**What.** Score the account receiving the money, across all customers. A payee that suddenly receives from many new or
unrelated customers is the classic mule. This protects new customers exactly when they have no history.

**Data.** Nightly (and hourly for the last day) aggregates per hashed payee key over the last 30 days, kept inside the 90-day
window:

| Field | Meaning |
|---|---|
| `distinct_senders_7d`, `distinct_senders_30d` | how many different customers paid it |
| `new_sender_share_7d` | share of those senders in their learning phase |
| `inflow_growth` | last 7 days ÷ the 30-day weekly average |
| `first_seen` | when anyone first paid it |
| `fraud_reports_90d` | fraud reports on payments to it |
| `fan_in_burst` | most distinct senders in any 24 hours |

**Score.** Start with rules turned into features (no labels needed): new payee (first seen in the last 14 days) with 5+
distinct senders, or 3+ fraud reports, or a 24-hour burst of 10+ senders. Then features for the model:
`payee_senders_7d`, `payee_new_sender_share`, `payee_age_global_days`, `payee_reports`. A fraud report on a payment raises
the payee's score for every customer at once.

**Graph, later.** Once there is volume, a sender–payee graph finds rings: payees that share senders with a known mule, or
accounts that receive and immediately send on. Start with the aggregates; they catch most of it.

**Server.** `amfa/core/payee_reputation.py` with `refresh(s)` (the job) and `features_for(payee_key)`; called in
`context_builder.assemble`; a reported mule becomes a hard signal (already in `hard_signals` as `payee_risk_flag`).

**Simulator.** Mule fan-in already exists (`mule_fanout`); add the cross-customer view: many simulated victims paying the same
mule, so the features have something to learn.

**Tests.** A new payee receiving from 6 customers in a week raises the level for the 7th; one fraud report on a payee raises
it for all its senders; an established payee with thousands of senders (a shop, a utility) is not flagged.

**Watch out.** Shops and utilities receive from many people; `first_seen` age and a steady inflow keep them out. Payee keys
are already hashed per bank, so this works within one bank; across banks would need a shared, privacy-preserving scheme
(later, and a legal question).

---

## 5. A real learning loop

**What.** Collect the truth so the model learns from real customers, not only the simulator.

| Label source | How | Signal |
|---|---|---|
| "This wasn't me" | One tap on any payment in Activity, and a push notification after large ones | FRAUD |
| "Yes, that was me" | Silent: a payment not reported within 30 days is GENUINE | GENUINE |
| Reviewer verdict | Already stored | either |
| Bank chargeback or dispute | `/v1/transactions/{id}/feedback` | FRAUD |
| Abandoned check | Session expired or cancelled at a check | friction, not fraud |
| Failed check, then success | Attempts table | friction |

**Server.**
- `RiskLabel` already exists; add the silent GENUINE label as a nightly job for payments 30-89 days old with no report.
- A monthly retrain job: last 90 days of real labelled payments, blended with simulator rows for the rare attacks (weight
  the real rows up as they accumulate). The release gate compares challenger with the live model on the same held-out last
  2 weeks: more fraud caught at the same false alarms, and fairness within limits. Switch only on a pass; keep the old version
  one pointer away.
- Friction metrics per check: time taken, retries, abandonment, by phase and by phone model.

**App.** "Report this payment" on every activity item (the bank side already has `/api/actions/{id}/report`); a notification
after payments above the customer's usual.

**Tests.** A report creates a FRAUD label and resets readiness; the silent label appears after 30 days and not before; the
retrain refuses a challenger that loses on the held-out weeks.

**Watch out.** With the 90-day rule, labels older than 90 days are gone, so the model always learns from the last quarter.
That is fine for drift, but rare attacks must keep coming from the simulator.

---

## 6. People change: drift detection

**What.** Notice when a ready customer's habits shift (new job, move, wedding) and adjust instead of flagging everything.

**How.** For each ready customer, compare the last 14 days with the 76 before (inside the 90):
- amount: median and spread of log amounts,
- payees: share of payments to payees not seen before the 14 days,
- hours and places: distance between the two hour histograms, new cities.

If two of them move beyond a threshold together **and** there was no fraud report **and** the customer passes their usual
checks, it is a life change, not an attack: the habit window is shortened to the recent part (their new normal is learned
faster), and readiness re-checks. If the change comes with warning signs (new phone, SIM change), it is treated as a possible
takeover and the customer goes back to learning.

**Customer can tell us.** "I'm travelling until …" (new places expected), "I've moved", "Big purchase coming". Each relaxes one
signal for a time, never the hard signals, and is logged.

**Data.** `customer_phase.drift` (JSON: last scores, when). No new transaction data.

**Tests.** A simulated move (new city, new payees, same amounts) is detected and does not stay flagged; the same change with a
SIM swap sends the customer back to learning.

---

## 7. Designed against gaming

**Graduation farming.** Someone opens an account, makes 30 small payments to their own other account, becomes ready, then
drains it.
- Readiness already needs separate active days. Add: payments to accounts that receive mostly from this one customer (their
  own wallet) count at half weight; payments to a payee that later turns out to be a mule erase the evidence.
- **Ceiling after readiness:** no habit makes a payment more than 2× the customer's own largest verified payment go through
  without a strong check. Farming small payments therefore buys nothing large.

**Slow payers.** Measured fraud detection after readiness was weaker for under 3 payments a week (65%). For them the
learning-phase limits stay on after readiness (a bank setting), and the ceiling is 1.5×.

**Splitting.** Already covered by 24-hour exposure in the limit and the habit score; add a check for many payments just under a
limit in a short time.

**Probing.** Someone trying amounts to find what passes without a check: count declined or abandoned attempts on new payees in
24 hours; three or more is a hard signal.

**Tests.** A farming script (simulator scenario `graduation_farm`) becomes ready but its drain is still held; splitting just
under the limit is caught.

---

## 8. Validate with real people

**What.** Everything so far is measured on a simulator written by us. Before trusting the thresholds:

1. **Closed pilot**: 100-500 real users on the demo bank or a partner, with clear consent, for 8-12 weeks.
2. **Measure on them**: the readiness curve (`scripts/measure_readiness.py` pointed at real rows instead of the simulator),
   false alarms by phase, time to ready, abandonment, and every fraud report.
3. **Compare settings side by side**: assign users at random to two readiness settings (say a 25% and a 15% bound), and compare
   friction and reports. Change the default only on evidence.
4. **Red team**: a small group tries to farm, probe and take over test accounts, with a list of attacks from piece 7.

**Code.** The measurement script takes a `--source real` option that reads `txn` + `decision` + `risk_label` rows instead of
simulating; nothing else changes.

---

## 9. Privacy built in (India's DPDP Act)

| Requirement | What we do |
|---|---|
| Consent, specific to purpose | At sign-up, a separate, plain consent: "We learn how you usually pay to protect your account." Without it, the customer stays on light checks plus limits and is never profiled |
| Only what is needed | Payee and device as keyed hashes, city not location, typing as features not keystrokes, no names or account numbers in AMFA |
| Kept no longer than needed | 90 days (done), peer stats only for groups of 50+ |
| Right to see and erase | "What we know about you" page in Profile (phase, readiness progress, devices, declared answers); erase (exists: `DELETE /v1/users/{id}`) |
| Security | Encryption in transit end to end (done for biometrics), at rest for contact details (done); access logged (audit log) |
| Explain decisions | Every plan already carries a plain reason; the customer sees it on the review screen |

**Data inventory** (one file, `docs/data-inventory.md`): every field stored, why, where, how long, who can read it.

---

## Order to build

| Step | Pieces | Why first | Size |
|---|---|---|---|
| 1 | 3 (device key + integrity + SIM binding), 1 (phases with light checks) | Gives the light learning-phase check you asked for, and its safety net | 2-3 weeks |
| 2 | 4 (payee reputation) | Biggest protection for new customers, needs no labels | 1-2 weeks |
| 3 | 2 (sign-up questions, peer groups) + retrain | Better decisions from day one | 2 weeks |
| 4 | 5 (labels, silent genuine, monthly retrain) | Turns the simulator model into a real one | 1-2 weeks |
| 5 | 7 (anti-gaming), 6 (drift) | Hardening once the basics run | 2 weeks |
| 6 | 8 (pilot) and 9 (consent screens, data page, inventory) | 9's consent must be in place before the pilot starts | runs alongside |

Each step ends with its tests, a regression run (`python -m amfa.demo.regression`), and the readiness measurement.

---

## Status: step 1

**Built (server, tested):**
- `Factor.DEVICE`: the phone signs a fresh challenge with its secure-hardware key (`amfa/core/orchestrator._verify_device`). It
  answers a challenge, so it counts as a dynamic factor, and a bank may count it as two factors (`learning.deviceCountsAsTwoFactors`).
- `customer_phase` table and `amfa/core/phase.py`: every verified payment teaches the readiness tracker what the model would have
  asked; a customer becomes READY by the tests in `readiness.py`; a fraud report sends them back to LEARNING; customers who existed
  before it was switched on are read from their history.
- `decide.learning_plan`: while LEARNING the plan is the device signature alone, plus a one-time code above the learning limits or
  on a hard warning sign, and a hold only when the model is nearly sure (calibrated risk of 0.5 or more). No face or voice. It does
  not need the model to work.
- Settings: `learning` in the tenant policy (off by default; limits ₹25,000 a day and ₹10,000 for a first payment to a payee), and
  `DEVICE` in `permittedFactors`.
- `GET /v1/users/{id}/phase` and `learning` in the demo bank's `/api/me`, for the progress bar.

**Built (Android, tested on the JVM; not yet on a phone):**
- `DeviceKey.kt` (the signer interface and the key's format for AMFA) and `KeystoreSigner.kt` (a P-256 key made in the secure hardware,
  StrongBox when present, which signs only for 15 seconds after the phone's fingerprint or screen lock was confirmed).
- After sign-in the app makes the key once per account and gives AMFA its public half (`/c/v1/enrollment/device`). A phone with no
  screen lock makes no key and keeps using codes; any failure here is silent.
- The device step on the verify screen: it asks the phone's lock screen (Android's confirm-credential screen) and sends the signature.
  The signature is cross-checked: a key and signature made by Java verify with the server's own verification code.
- Home and Profile show "Getting to know how you pay" with a progress bar, and an optional face offer after the learning phase's
  5th verified payment.
- Seed the demo with `python scripts/seed_demo.py --learning ...` to turn the learning phase on (needs app 1.0.3 or later).

**Not built yet:**
- A prompt per payment. The key signs for 15 seconds after one confirmation; per-payment touch needs `androidx.biometric`'s
  BiometricPrompt with a CryptoObject.
- Play Integrity in the app and SIM binding (piece 3's other half).

---

## Test log: step 1 on a real Android emulator and the web bank

Run against a clean separate stack (core, behaviour service and demo bank on their own ports and data directory), with the learning
phase on, on an Android 14 emulator with a screen-lock PIN. What was done by hand, and what it found:

| Check | Result |
|---|---|
| Open an account in the app | Goes straight to Home: no "Set up my face" step. The key is handed to AMFA during sign-up |
| Pay ₹1,500 to a saved payee | Review screen shows one step, "Fingerprint or screen lock". Android's own lock screen opens with the amount in it; entering the PIN completes the payment: "Confirmed with Fingerprint or screen lock" |
| Cancel the lock screen | "That wasn't confirmed. Try again" and the session stays open; Confirm asks again; backing out says "Payment cancelled. Nothing was sent" |
| Pay ₹30,000 (above the ₹25,000 learning limit) | Two steps, code then lock screen. The review says why and shows the scam warning. Result: "Confirmed with Email code, Fingerprint or screen lock" |
| Profile | "Getting to know how you pay" with a progress bar; "Protection: Good" now counts the phone's own key |
| Seeded customers on the learning bank | Kiran and Anil (90 and 115 payments) are READY and get the normal plan; a brand-new persona is LEARNING |

Found and fixed during the run:
1. Sign-up still showed the face step, and never gave AMFA the phone's key (only sign-in did). The bank reports the learning state only once
   AMFA knows the customer, so the key is now handed over first, then the screen is chosen.
2. The review screen quoted the model's unfamiliarity reasons ("this device is new to your account") to a customer it was still learning.
   A learning plan now says what is happening, in its own words.
3. The progress text said checks would get "lighter"; once ready, unusual payments get stronger checks. Reworded to "tailor your checks".
4. A customer whose phone cannot sign (a browser, or a phone with no screen lock) in a learning bank got a code-only plan, which cannot meet
   the two-factor minimum, so every payment would have ended in review. They now keep the normal plan.
5. On the code screen the keyboard covered the Confirm button, and the Back key then cancelled the payment. The keyboard now hides after the sixth digit.
6. The phone's key was missing from the bank's "ways to confirm" list.

Left as it is:
- Seeded personas have touch-only typing templates (made for the phone), so on the web bank their large payments get a code only and wait
  for review. The same customers on the phone get code and typing.
- The web bank's sign-up still asks for a face: a browser cannot hold the phone's key, so the learning phase is a phone feature for now.
- The progress estimate can go up as well as down: a large unusual payment widens the customer's spread, so more payments are needed.
