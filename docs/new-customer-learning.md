# New customers: learn first, then decide

There is no data from a bank. A customer arrives with nothing. Plan: for the first weeks we only learn. Every payment is
confirmed with a light check (fingerprint, passcode or code). We store what is needed to learn their habits. Once there is
enough of their own history for the model to judge them well, the model starts choosing the checks.

## 1. When is "enough"? A test on the customer's own payments, not on days

Days do not work: one customer pays twice a week, another fifteen times a day. A fixed number of payments does not work either:
a customer who always pays about the same amount to the same people is understood in a dozen payments, one whose payments swing
widely needs many more. So readiness is a test of **evidence about this customer**, checked after every payment
(`amfa/engine/risk/readiness.py`):

| Test | What it asks | Passes when |
|---|---|---|
| **Proven** | Has the model been right about this person? For every genuine, verified payment we record whether the model *would* have asked for a face check or more (a false alarm). | Over their last 20 genuine payments the false-alarm rate is low **with confidence**: the 95% upper bound (Wilson) is at most 25%. That is a clean run of about 12, or 1 miss in 20. Misses need more payments to outweigh. |
| **Known** | Is their usual amount pinned down? | The 90% interval of their typical amount is within about ±30%. A steady payer passes in a handful of payments; a widely swinging one needs dozens. |
| **Spread** | Did the evidence come from more than one sitting? | At least 3 separate days with a payment. Not calendar days: a daily payer passes in 3 days, a weekly one in 3 weeks. It stops someone farming a new account with a burst of payments in one sitting. |

The model starts choosing the checks the moment all three pass, whenever that is.

**Measured** on 7,500 simulated customers replayed from sign-up (`scripts/measure_readiness.py`; the model never saw them):

| Customer pace | Ready within 5 months | Payments it took (median, 90th pct) | Days it took (median) | False alarms: learning, then ready |
|---|---|---|---|---|
| under 3 a week | 22% | 25, 43 | 95 | 16%, then 10% |
| 3-7 a week | 88% | 48, 79 | 65 | 12%, then 6% |
| 7-20 a week | 99% | 61, 114 | 39 | 12%, then 4% |
| 20-70 a week | 100% | 76, 142 | 20 | 12%, then 4% |
| 70+ a week | 100% | 70, 127 | 5 | 8%, then 3% |

Same test, so the days it takes range from 5 to 95 depending on the person; that is the point. False alarms after readiness are
3-6% for anyone paying a few times a week or more, which is what an established customer sees.

**What this does not cover, and why it matters.** Readiness says the customer's *own* history is reliable. It says nothing
about catching fraud on them: there is no fraud on one customer to measure, so that is the shared model's job, measured across
all customers. After readiness the model flagged 78-100% of fraud for customers paying 3+ times a week, but only **65% for
those paying under 3 a week** (48 cases): with few payments the model has less to compare against. For them, two choices: keep
them on the learning-phase checks longer (most are not ready within 5 months anyway), or keep a lower daily limit for them
after readiness. The second is in section 2 as a setting.

All of this is on simulated customers. The test and its settings (window 20, bound 25%, ±30%, 3 active days) are kept in one
file and the script reruns in two minutes, so they can be checked against real customers after the first months.

## 2. The two phases

| | Learning (from sign-up until ready) | Ready |
|---|---|---|
| Who decides the check | Fixed: one quick check on the phone | The model, from the customer's own habit |
| Usual payment | Fingerprint or passcode (device key) | Code + typing, or nothing extra within their own no-check limit |
| Unusual payment | Fingerprint plus a one-time code | Face or voice, by how unusual it is |
| Hard warning signs (SIM swap, new phone, risk-listed payee, password just reset) | **Always** step up or hold, from day one | Same |
| Limits | Lower: a daily cap and a cap on the first payment to a new payee | The customer's normal limits |
| Model | Scores every payment silently; the score is stored, not used | Decides |

The learning phase is the riskiest time: new-account fraud and takeovers soon after sign-up both happen here. The model is
not trusted yet, so the guards are **limits and hard signals**, not heavier checks. Suggested defaults, all bank settings:
₹25,000 a day in total, ₹10,000 for a first payment to a new payee, and a 4-hour cool-down on a payee added in the last day.

**Fingerprint or passcode as a real factor.** The app creates a key in the phone's secure hardware (Android Keystore,
StrongBox where present) that only unlocks with the phone's fingerprint or screen lock. AMFA sends a challenge and the key
signs it. That proves this phone (possession) and the person who unlocked it (fingerprint or PIN), and counts towards the
two-factor minimum. Built: the Android app makes the key after sign-in and signs the challenge after the phone's lock screen is confirmed
(see [cold-start-implementation.md](cold-start-implementation.md), step 1). A browser cannot hold such a key, so the learning phase is a phone feature.

## 3. What we store, and how

Everything is kept for 90 days at most and deleted after (`amfa/core/retention.py`).

**Per payment (already stored, one row each):**

| Field | Why | How it is kept |
|---|---|---|
| Time, local hour | Usual hours, how often they pay | As is |
| Amount, currency, type (transfer, bill) | Their usual amounts, overall and per payee | As is |
| Payee | Who they pay and how regularly | Keyed hash only (never a name or account number) |
| Device | Their phones | Keyed hash only |
| Network and city | Where they usually are | Network id and city, never exact location |
| Outcome: verified, cancelled, declined | Only verified payments teach "usual" | Status |
| Checks asked and their results (passed, failed, spoof) | Failed checks and spoof attempts are warning signs | Per attempt |
| Model's score and level (also in learning) | Measuring readiness; later training | The decision record |

**Per payment, new (from the phone):**
- How the payment was made: time from opening the screen to confirming, whether the account number was pasted or typed, whether
  a call was in progress, whether a screen-sharing app was active. These are the signals for coached scams, which no amount
  history can catch.
- A summary of how they typed and touched (rhythm and pressure features), never the raw keystrokes or the text.

**Labels (the truth, when it arrives):** the customer's "this wasn't me", a bank or reviewer's verdict, a chargeback.
Each attaches to its payment and is used for retraining. Also kept 90 days.

**Not stored:** names, account numbers, exact location, raw keystrokes, photos or recordings beyond the enrolled templates.

**Per-customer profile.** Not a separate store. It is rebuilt from the 90 days of rows each time: usual amount and spread,
overall and per payee; payees and how regularly each is paid; devices, networks, cities; usual hours; payments a week. At
this size (at most a few hundred rows per customer) that takes milliseconds. When it stops being fast, keep running
aggregates per customer and rebuild nightly; the model inputs stay the same.

**Per-customer readiness state:** `ready_since` (null while learning), plus the evidence behind it (the last 20 genuine
payments' false-alarm marks). It is updated after every verified payment, so nothing is recomputed at decision time. A
customer whose recent payments start drawing false alarms again (the "proven" test fails) goes back to learning until it passes.

## 4. How the model gets better

- The model is **one model for everyone**. It learns patterns like "far above this person's own habit" and "new phone at
  night". What is personal is the history it is given. A new customer does not need their own model; they need enough of their
  own history.
- It is retrained monthly on the last 90 days of every customer's payments and labels, blended with the simulator for the rare
  attacks that real data will not have yet. A new version replaces the old one only if it catches as much fraud with fewer false
  alarms on the same held-out weeks.
- `scripts/measure_readiness.py` is rerun after each retrain, and the 30-payment / 45-day threshold is moved if the curve moves.

## 4b. Two tests, not one: the customer and the band

The readiness test above asks whether the model knows **this customer**. A second test, the band switch
([rbi-banded-data-collection.md](rbi-banded-data-collection.md)), asks whether the model can be trusted on **payments of this size**
at this bank. It is pooled across all customers and graded on real outcomes. It exists because the bank gives no data to train on,
so the model has only ever seen simulated customers.

| | Customer readiness (built before) | Band switch (built now) |
|---|---|---|
| Whose data | this customer's own payments | every customer's payments in one amount band, as anonymous rows |
| What it needs | about 25–76 of their payments on 3+ separate days | 5,000 genuine payments, 100 real fraud reports and 8 weeks per band (double for ₹1 lakh+), and the model beating the rules 4 weeks running |
| Until it passes | the learning plan: phone key, limits, hard signs | the stricter of the rules and the model |
| Who moves it | automatic | automatic to ELIGIBLE, then two operators |

The model lowers a customer's checks only when **both** have passed. A customer still in the learning phase keeps the learning plan
whatever their band's state. The band switch is off by default (`collection` in the tenant policy).

## 5. Build list (status)

1. Customer phase (`learning` or `ready`) driven by `readiness.Tracker` after every verified payment. Decisions in learning use the fixed
   learning-phase checks plus hard signals and limits; the model's score is still recorded. **Built.**
2. Device-key check in the app: Keystore key unlocked by fingerprint or screen lock, signing AMFA's challenge. **Built**, tested on an emulator.
3. Learning-phase limits as bank settings. **Built** (`learning` in the tenant policy).
4. The new phone signals in section 3 (call in progress, screen sharing, pasted account number, time to confirm). **Not built.**
5. A readiness report per bank: how many customers are learning, how many are ready, and the false-alarm rate of each. **Partly:**
   `scripts/measure_readiness.py` and the five-bank evaluation report it; there is no per-bank screen yet.
6. Sign-up questions and peer groups (section 2 of the implementation plan). **Built.**
7. Payee reputation (mule detection). **Built.**
8. The band switch: anonymous banded rows, nightly grading of the rules against the model per amount band, two-person approval,
   automatic fallback (section 4b). **Built**, off by default.

The customer-facing controls that came after (own no-check limit, optional family helper) are in [customer-limit.md](customer-limit.md) and
[family-helper.md](family-helper.md); the end-to-end test on five different simulated banks is [dataset-evaluation.md](dataset-evaluation.md).

Already fixed while writing this: a payment the bank had completed was not counted as the customer's history (its status
changes to CONSUMED), so a new customer's own payments would never have taught the model anything.
