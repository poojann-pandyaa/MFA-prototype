# Band switch: collect banded data, then let the AI model decide

## Goal and scope

The bank does not give us data to train the AI model. So the model we have (trained on simulated customers) is not trusted to
**lower** checks on its own. Instead:

1. Every payment is recorded only by **amount band**: ₹0–2,000, ₹2,000–10,000, ₹10,000–1 lakh, above ₹1 lakh (section 2a says why
   four bands and not three).
2. Until a band has enough real evidence, **rules** set the checks for payments in that band. The model can still add checks.
3. Once a band passes its threshold on real data, the **AI model** decides the checks for that band.
4. User data is kept to the minimum and protected at every step. **Security comes first**: no phase may make any payment's checks
   weaker than they are today unless the threshold is passed and two people approve.

**Out of scope:** training the model on bank data, a way for banks to upload history, retraining, and any change to the Android app.

**Agreed:** what is built stays as it is; data lives on the server, not the phone; option A (the existing 90-day payment history
is untouched, and the new collection stores bands only).

---

## 1. What exists that this builds on

| Already built | Where | Used for |
|---|---|---|
| The AI model's plan (T0–T4, reliefs) | `engine/decide.select_by_model` | The plan once a band has switched |
| A rules-only plan, worked out on every request, logged, never used | `engine/rules.select_by_rules`, `plan.rulesBaseline` | The plan while a band is collecting |
| The 24-hour total of this payment plus recent ones | `TransactionCtx.exposureMinor` | Working out the band, so a split payment cannot drop a band |
| Floors, hard warning signs, payee holds, signed single-use proofs | `policy.py`, `rules.py`, `payee_reputation.py`, `proofs.py` | Applied after the switch in both modes; untouched |
| Fraud and genuine labels | `RiskLabel`, `api_tenant` feedback, reviewer verdicts | Grading the model against the rules |
| The per-customer learning phase | `core/phase.py`, `decide.learning_plan` | Unchanged; the band switch matters only once a customer is READY or learning is off |
| Sweeper, audit log, operator roles, AES-GCM key derivation | `app.py`, `audit.py`, `auth.ROLES`, `crypto.py` | Nightly gate check, recording every switch, who may approve, the label-link key |
| The bank's own amount bands (₹10k / ₹1 lakh) | `policy.amountBands` | **Not changed.** The new bands are a separate ladder, so today's floors stay as they are |

---

## 2. How it works

```
payment ──► decision path (unchanged up to here)
               model plan (select_by_model)      rules plan (select_by_rules, with the new band)
                          └────────────┬────────────┘
               band switch for this payment's band
                 COLLECTING / ELIGIBLE → the STRICTER of the two plans
                 MODEL                 → the model plan, as today
                          ▼
               floors, hard signs, payee holds, proof   (unchanged, always last)
                          ▼
               on settle: one anonymous banded row ──► nightly grading per band ──► ELIGIBLE ──► two people approve ──► MODEL
                                                                                        ▲                                 │
                                                                                        └──── automatic fallback ◄────────┘
```

- **Band of a payment** = the higher of its own band and the band of its 24-hour total.
- **Stricter of two plans** = the higher threat level; the required checks are the union of both, limited to what the customer
  has set up; never fewer checks than either plan.
- **Off by default.** With the feature off, every decision is exactly what it is today.

### 2a. The bands (decided)

| Band | Range (rupees) | In paise, half-open | Who pays this |
|---|---|---|---|
| 0 | ₹0 – ₹1,999.99 | `[0, 2,00,000)` | Everyday: groceries, recharges, small UPI |
| 1 | ₹2,000 – ₹9,999.99 | `[2,00,000, 10,00,000)` | Bills, EMIs, shopping |
| 2 | ₹10,000 – ₹99,999.99 | `[10,00,000, 1,00,00,000)` | Rent, fees, larger transfers |
| 3 | ₹1,00,000 and above | `[1,00,00,000, ∞)` | Rare, large: property, business, savings moves |

Exactly ₹2,000 is band 1, exactly ₹10,000 is band 2, exactly ₹1 lakh is band 3.

**Why the professor's example was split at ₹1 lakh.** I measured where payments and fraud fall in the project's simulator (3,000
customers, 150 days, 177,000 payments, weighted to real proportions):

| Edges | Band | Genuine payments | Fraud cases | Fraud money |
|---|---|---|---|---|
| 2k / 10k (the example) | ₹10k+ | 36.6% | 43.7% | **99.7%** |
| 2k / 10k / 1L (chosen) | ₹10k–1L | 22.1% | 30.1% | 2.2% |
| | ₹1L+ | 14.5% | 13.6% | **97.5%** |

With three bands, the top band runs from ₹10,000 to crores. Most of its evidence would come from ₹10k–₹50k payments, yet
switching it on would also hand the model the ₹50 lakh payments, where almost all the money at risk is. A separate ₹1 lakh+ band
must earn the model's trust on its own large payments, and it is the band most likely to stay on rules. That is the safe default
for the money that matters most.

The edges also line up with what already exists:
- ₹2,000 and ₹10,000 are edges of the sign-up "typical payment" answers (`declared.py`);
- ₹10,000 and ₹1 lakh are the bank's own medium and high edges (`policy.amountBands`), so a payment's band never disagrees with
  the bank floor it gets.

These are simulator numbers: they show where the risk sits, not real fraud rates.

### 2b. The threshold (decided)

The threshold is **per band, pooled across all of a bank's customers**. It is not per customer: no single customer is ever asked to
make 5,000 payments. Whether the model knows an *individual* customer is still the existing per-customer readiness test (about
25–76 of their own payments, `engine/risk/readiness.py`). The AI chooses a customer's checks only when **both** hold: the payment's
band has switched, and the customer is READY (or the bank has the learning phase off).

A band moves from rules to the AI only when **all** of the following hold. The band's evidence is graded every night over a
rolling 8-week window.

| Gate | Bands 0–2 | Band 3 (₹1L+) | Hard minimum in code | Why this number |
|---|---|---|---|---|
| Mature genuine payments | 5,000 | 10,000 | 2,000 | Pins a ~5% false-alarm rate to about ±0.6 points, enough to tell 4% from 6%. 1,000 would give only ±1.4. |
| Real fraud labels | 100 | 200 | 50 | 100 cases pin a ~85% catch rate to about ±7 points; 50 only to ±10. The big-money band needs twice the proof. Simulated fraud never counts. |
| Distinct weeks with data | 8 | 8 | 4 | Two full monthly cycles (salary, rent, EMIs), so a monthly pattern is seen twice, not once. |
| Fraud: model vs rules, on the same cases | The model misses no fraud case more than the rules do | Same, **and** no fraud the rules would have held was let through by the model with light checks | Always required | Paired: both are graded on exactly the same payments, so the comparison is fair even with few cases. |
| False alarms: model vs rules | Model's rate ≤ rules' rate | Same | Always required | The point of switching is less friction for honest customers. |
| Holds over time | 4 nightly evaluations a week apart, all passing | Same | 4 | One good week can be luck. |

Then **two different people approve** (section 3).

**What counts as "mature genuine":** a completed payment with no fraud report 30 days after it settled. Fraud reports and
chargebacks arrive late, so a payment younger than 30 days is not yet counted either way.

**After the switch** (automatic fallback to rules):
- bands 0–2: the model loses on fraud or false alarms in **2 weekly evaluations in a row**;
- band 3: at once, after a **single** labelled fraud that the model let through with light checks where the rules would have
  held it or asked for face or voice.

**What to expect:** bands 0 and 1 fill fastest; bands 2 and 3 need far more time, because fraud labels are rare. On a small bank,
band 3 may stay on rules for good. That is intended.

---

## 3. How user data is kept safe

| What | How |
|---|---|
| **Nothing new from the phone** | The band is worked out on the server from the amount the bank already sends. The app sends nothing for this and has nothing to fake. |
| **The new record identifies no one** | `learning_row` has no customer, account, amount, payee, device, network, place, IP or exact time. Only small categories and an ISO week. A schema test fails the build if an identifying column is ever added. |
| **The label link expires** | To attach a label that arrives later, a row carries `link` = HMAC-SHA256(transaction id) with its own derived key (`crypto.derive_key("learning-link")`). At 90 days `link` is set to null. The transaction is deleted at 90 days anyway, so after that nothing can tie the row back to a person. |
| **No profiling across rows** | Rows have no shared key between payments of the same customer, so a customer's rows cannot be strung together. |
| **Small numbers are hidden** | Any figure in the admin console built from fewer than 10 rows shows as "<10". |
| **Only humans switch, and two of them** | Moving a band to MODEL needs two different operators. The bank's API key cannot do it. Every change goes to the hash-chained audit log. |
| **Switching off the feature counts as relaxing** | Turning `collection.enabled` off, or lowering a gate threshold, returns bands to the model. So it needs the same two-person approval, and the thresholds have hard minimums in code that no setting can go below. |
| **Fail closed** | Gate state missing, unreadable or more than 48 hours old ⇒ that band is treated as COLLECTING, the stricter mode. |
| **Rows cannot be flooded** | At most 10 rows per customer per day count (checked against the existing `txn` table, so no customer id is stored). The gate needs at least 8 distinct weeks and a *win* over the rules, not just volume. |
| **Labels cannot be faked** | Only labels from the bank's scoped API key and from reviewers count (existing paths). The customer's app cannot label. |
| **Storage** | The same database, with disk encryption and TLS. Deployment checklist: database and backups in an Indian region. `learning_row` retention is a bank setting (default 365 days); it is anonymous, so the bank can choose to keep it longer. |

---

## 4. Phases

Each phase leaves the system safe if we stop after it. Each phase that adds code adds its own tests, and the **full existing
test suite must pass unchanged** at the end of every phase.

### Phase 0: details (decided)

- option A as worded above;
- four bands (section 2a);
- the threshold (section 2b);
- a per-customer daily cap of 10 rows;
- `learning_row` kept for 365 days.

**Outcome:** done; these are written into this doc. **Tests:** none (no code).

### Phase 1: band ladder and settings (does nothing yet)

- `amfa/engine/risk/collection_bands.py`: the edges, `band_of(amount_minor)`, `band_for_payment(amount_minor, exposure_minor)`.
- A `collection` section in the tenant policy (`extra="forbid"`): `enabled` (default false), the gate values for bands 0–2
  (`gate`) and band 3 (`topGate`), the daily cap and retention.
- The edges are **fixed in code, not a bank setting**. Moving an edge up would put larger payments into a band the model may
  already be trusted on, and would change what every collected row means. Gate values default to section 2b and have the hard minimums there (2,000 genuine, 50 fraud, 4 weeks, 4
  passing evaluations) in code.

**Outcome:** a bank's policy can carry the new section; behaviour is unchanged.

**Tests** (`tests/engine/test_collection_bands.py`):
- the edges: ₹0, ₹1,999.99, ₹2,000, ₹9,999.99, ₹10,000, ₹99,999.99, ₹1,00,000 and ₹50 lakh land in bands 0, 0, 1, 1, 2, 2, 3, 3;
- splitting: a ₹7,500 payment with ₹7,500 already sent today is band 2; a ₹60,000 payment with ₹60,000 already sent is band 3;
- a negative or missing amount is treated as band 3 (fail strict, never as a small payment);
- an unknown 24-hour total is the top band (it cannot rule out a split payment);
- band 3 defaults to the stricter gate values (10,000 / 200) and cannot be set below band 0–2's in any field;
- policy:
  - off by default;
  - gate values below the hard minimums refused;
  - unknown fields refused, including any attempt to set the edges;
  - cannot be enabled for a non-INR bank;
  - a policy stored before this change still loads, with the feature off;
- the bank's own amount bands are unchanged;
- the existing suite, persona regression included, passes unchanged.

**Status: built.** `amfa/engine/risk/collection_bands.py`, the `collection` section in `amfa/engine/policy.py`, and 50 tests in
`tests/engine/test_collection_bands.py`.

### Phase 2: the anonymous learning store

- New table `learning_row`:

  | Column | Values |
  |---|---|
  | `tenant_id` | the bank |
  | `band` | 0 / 1 / 2 / 3 |
  | `band_vs_usual` | same / one higher / two or more higher than this customer's most common band, worked out at write time from the existing history; only the result is kept |
  | `payee_kind` | known / new / flagged |
  | `device_kind` | known / new / helper |
  | `hour_bucket` | night / morning / afternoon / evening |
  | `hard_sign` | yes / no |
  | `rules_level`, `model_level` | T0–T4 on one scale for both: T0 code, T1 code + typing, T2 one biometric, T3 both, T4 held or the model's top level. The model's level is its own call before floors (its shadow level while a customer is learning); a model that could not score counts as T4 |
  | `mode` | COLLECTING / ELIGIBLE / MODEL at the time (always COLLECTING until phase 4) |
  | `outcome` | completed / declined / cancelled / expired |
  | `label`, `label_source` | unknown / genuine / fraud; bank (`customer_report`, `chargeback`, `bank_ops`) or `review_verdict` |
  | `week_start` | the Monday of its week |
  | `link`, `link_expires_at` | expiring keyed hash (section 3); the expiry is a whole day, never a time |
  | `id` | random, so the order rows were written in says nothing |

- The engine logs a coarse `bandView` on every payment's plan (`amfa/engine/band_switch.py`); it never decides anything.
- The row is written when a payment settles: completed or declined (`_resolve`), expired (`_expire`), reviewed (`review_decision`)
  or cancelled (the bank's cancel). It is written only when the feature is on, in a savepoint, so a failure can never affect the
  payment.
- A label arriving later (bank feedback, reviewer verdict) updates its row through `link`. **FRAUD sticks:** a later GENUINE
  never clears it.
- Retention (the hourly sweep): `link` set to null at 90 days, even if the bank has since turned the feature off; rows deleted
  after the bank's retention period.

**Outcome:** rows build up per band; customers see no change.

**Tests:** `tests/core/test_learning_store.py`, `tests/core/test_learning_store_e2e.py`, `tests/engine/test_band_view.py`.
- **privacy:**
  - the table's columns are exactly the allowed list;
  - no column name suggests anything identifying (customer, amount, payee, device, network, place, ip, timestamp and more);
- **privacy:** a written row contains no customer id, customer ref, transaction id, external id, payee ref or name, amount or
  timestamp;
- **privacy:** `link` is null after the retention run, also with the feature turned off; a label after expiry updates nothing;
- **privacy:** two payments by one customer share no id, link or write order;
- the 11th payment of a customer's day writes no row; another customer's payments don't use up the cap; the cap is a setting;
- labels:
  - a fraud report updates the right row;
  - unknown sources and kinds are ignored;
  - FRAUD sticks;
  - a label that arrived before settling is picked up;
- each ending (completed, consumed, declined, cancelled, expired) writes exactly one row; in-progress payments, account changes,
  and payments with no valid view write none;
- nothing is written with the feature off;
- a failure writing the row leaves the payment committed;
- end to end through the Demo Bank:
  - a completed payment writes a row;
  - a split payment is recorded in the higher band;
  - "this wasn't me" labels FRAUD;
  - a rejected review is DECLINED and FRAUD; a two-person approval is COMPLETED and GENUINE;
  - cancelled and expired payments write their rows;
  - the payment goes through even if writing the row fails;
- the engine: decisions are identical with and without the view; account changes get no view; the band comes from the 24-hour
  total; a model that could not score is never recorded as light.

**Status: built.** Labels are matched to rows by their `link`; the grading in phase 3 will tell which rows are old enough to
count by `week_start` (at least 5 weeks old, so a row has had 30 days for a fraud report to arrive).

### Phase 3: grading and the gate (still decides nothing)

- The nightly job in the sweeper grades each band over a rolling 8-week window:
  - fraud caught: fraud at T2 or above, or held;
  - false alarms: mature genuine at T2 or above;
  - both for the model and for the rules, on the same rows;
  - plus counts and distinct weeks.

  "Mature genuine" means completed with no fraud report after 30 days; younger rows are not graded.
- A band becomes ELIGIBLE when every gate in section 2b holds, for 4 weekly evaluations running.

- A `band_gate` table keeps each band's state, the last evaluation time and the figures. Every change is written to the audit log.
- The admin console shows counts and rates per band, with "<10" for small figures.

**Outcome:** a per-band report, e.g. "₹0–2k: 3,400 genuine, 61 fraud; fraud caught 88% (model) vs 81% (rules); false alarms 4% vs
11%; eligible". Every decision is still made exactly as in phase 2.

**Tests** (`tests/core/test_band_gate.py`), on fixed data:
- the arithmetic of fraud caught and false alarms per band, model and rules, paired on the same rows;
- a completed payment 29 days old is not graded; at 31 days with no report it counts as genuine; a fraud report on day 40 turns
  it into fraud;
- enough rows but the model misses one more fraud than the rules ⇒ stays COLLECTING;
- the model wins but has 99 fraud labels (band 0) or 199 (band 3) ⇒ stays COLLECTING;
- simulated labels are ignored in the counts;
- only 7 distinct weeks ⇒ stays COLLECTING;
- band 3: the model wins overall but let one fraud through lightly that the rules would have held ⇒ stays COLLECTING;
- winning 3 evaluations out of 4 ⇒ stays COLLECTING;
- everything passes ⇒ ELIGIBLE, never MODEL;
- each state change is in the audit log and the chain still verifies;
- the console's per-band figures show "<10" below 10 rows.

**Status: built.** `amfa/core/band_gate.py`, the `band_gate` table, the hourly sweeper check (each bank graded about once a night),
`GET /admin/api/band-gates` (operators with `policy:read`, counts and rates only), `python -m amfa.cli band-gates --tenant <id>`,
and 32 tests in `tests/core/test_band_gate.py`.

How the grading works:
- **Window:** the latest max(8, `minWeeks`) weeks whose every payment is at least 30 days old. Measured from today, that is up to
  about 13 weeks back.
- **Fraud:** a row labelled FRAUD by the bank or by review, whatever its outcome.
- **Genuine:** labelled GENUINE by the same, or completed and never reported.
- **Ignored:** declined, cancelled or expired rows nobody labelled, because nobody knows what they were.
- **Streak:** grading runs nightly but counts at most once a week toward the 4 passes in a row; any failing night resets it to 0.
- **Losing eligibility:** an ELIGIBLE band that starts failing goes back to COLLECTING.
- **MODEL:** a band on MODEL is graded but never moved here; that is phase 4.

Extra tests beyond the list above:
- bands and banks are graded separately;
- rows too young to have been reported are left out;
- grading every night still counts once a week;
- equal to the rules is enough for bands 0–2;
- the nightly run skips banks with the switch off and runs at most once a night;
- the console endpoint needs a signed-in operator.

### Phase 4: the switch decides

- In `decide.decide`, after the plan is built and before floors: if the feature is on and the band is not MODEL, use the stricter
  of the model plan and the rules plan. The plan records `bandGate` and `bandState`.
- Admin endpoints:
  - **request** to move an ELIGIBLE band to MODEL;
  - **approve** by a different operator with `policy:write`; the second approval switches it;
  - **revert to COLLECTING** by any single operator, at once, because that only adds safety.
- Turning the feature off or lowering a gate value goes through the same request-and-approve flow.
- Automatic fallback as in section 2b: bands 0–2 after 2 losing weekly evaluations in a row; band 3 at once on a single fraud
  let through lightly. Each fallback writes an audit entry and a security event.
- Fail closed as in section 3.

**Outcome:** a bank can move ₹0–2k to the model while ₹1 lakh+ stays on rules; every step is audited.

**Tests** (`tests/engine/test_band_switch.py`, `tests/core/test_band_gate_admin.py`):
- **never weaker (the main security test):** for every persona and scenario context in the existing test data, the plan in
  COLLECTING has a level and checks at least as strong as in MODEL;
- floors, hard warning signs, payee holds and the 2-factor minimum are identical in both modes;
- feature off ⇒ decisions identical to today's (persona regression unchanged);
- **two-person rule:** one operator cannot switch a band; the same operator approving twice cannot; the bank's API key cannot
  call the endpoints; an operator from another bank cannot;
- a band that is not ELIGIBLE cannot be moved to MODEL even with two approvals;
- turning the feature off and lowering a gate value each need two people; raising a value or reverting a band needs one;
- **fail closed:** a missing row, an unreadable row and a 49-hour-old evaluation are each treated as COLLECTING;
- automatic fallback for bands 0–2 after 2 losing evaluations (not after 1), and for band 3 after a single light-checked fraud,
  each with an audit entry;
- with band 2 on MODEL and band 3 on COLLECTING, splitting a ₹1.2 lakh payment into two ₹60,000 payments is decided by band 3's
  rules.

**Status: built.** `amfa/engine/band_switch.py` (`apply`, `merge`), `amfa/engine/decide.py`, `amfa/core/band_gate.py` (`state_for`,
fallback, requests), the `band_switch_request` table, the guard in `apply_policy`, and the admin endpoints below. 59 new tests in
`tests/engine/test_band_switch.py`, `tests/core/test_band_gate_admin.py` and `tests/core/test_learning_store_e2e.py`.

How it was built, and where it differs from the plan above:
- **Stricter, in two steps.** Rules are added to the model's plan before floors, so they go through the same availability and
  substitution as any check. Then the model-only final plan is worked out as well, and anything it has that the result lacks is
  added, along with its hold.
  - Why the second step: the never-weaker test found 230 cases where one step was not enough.
  - Example: the rules' typing check met the two-factor minimum on its own, where on the model the phone key would have filled
    it. So "collecting" asked for typing instead of the phone key, which is not a superset.
  - The test runs 3 enrolment mixes over 2,400 situations each (5 model levels, 5 bands including an unknown day total, new
    payee, device, place, hour, behaviour).
- **Learning-phase customers and account changes** are never touched by the switch.
- **Admin endpoints**, all under `/admin/api`, needing a signed-in operator and the CSRF token:

  | Endpoint | Who | What it does |
  |---|---|---|
  | `POST /band-gates/{band}/request-model` | first operator, `policy:write` | asks to move an ELIGIBLE band to MODEL |
  | `POST /band-gates/requests/{id}/approve` | a second, different operator of the same bank | approves; the band must still be ELIGIBLE, and requests expire after 72 hours |
  | `POST /band-gates/{band}/revert` | any one operator | back to the rules at once; cancels that band's pending requests |
  | `POST /band-gates/policy-request` | first operator | a change that relaxes the switch, then approved the same way |
  | `GET /band-gates/requests` | `policy:read` | lists the requests |

  Moving a band to MODEL records the week it switched.
- **The policy guard covers everyone.** The bank's API key, a tenant operator and even a platform operator get 409
  `TWO_PERSON_CHANGE` from `PUT /policy` for a change that:
  - turns the switch off;
  - lowers any gate value;
  - raises the daily cap.

  Making it stricter, turning it on, and every other policy field are unaffected.
- **Turning the switch off (after two approvals) sends every band back to COLLECTING,** so turning it on later never brings back an
  old approval.
- **Automatic fallback:**
  - bands 0–2: after 2 lost weekly gradings in a row (one lost grading counts per week);
  - band 3: at once on any lost grading, and the moment a fraud report lands on a ₹1 lakh+ payment decided by the model that it
    let through lightly where the rules would not have. That one doesn't wait for the night or for 30 days.
  - Each fallback writes an audit entry and a security event (HIGH for band 3, MEDIUM otherwise).
- **Fail closed:** no state, an unknown state, a read error, or a MODEL band not graded for 48 hours (or with an evaluation time in
  the future) is decided as COLLECTING.

### Phase 5: end-to-end check and security review

- Replay a simulated bank through the live decision code with the feature on from day one. Record how many weeks each band takes
  to become eligible, and how much friction there is before and after.
- Run the live smoke test (`scripts/smoke_demo.py`) with the feature on and off.
- Security review of the whole change (`/security-review`), plus a checklist:
  - no new data from the app;
  - nothing identifying in `learning_row`;
  - the two-person rule is enforced on the server;
  - every fail-closed path has a test;
  - nothing in logs shows a `link` or an amount.

**Outcome:** numbers for the report, and the review's findings fixed.

**Tests:** `tests/core/test_band_switch_e2e.py`: one simulated bank end to end. A band becomes eligible only after its gates pass.
Band 3 (₹1 lakh+) stays COLLECTING with no real fraud labels. Feature on versus off: on is never weaker on any payment.

**Status: done.**

**The replay.** `amfa/demo/band_replay.py`, run by `python scripts/replay_band_switch.py`; results are in
[band-switch-replay.md](band-switch-replay.md).
- Every simulated payment goes through the real `decide()` (model, reliefs, floors, band switch).
- Its learning row is written, the real grading runs every simulated night, and two simulated operators approve a band the night
  it becomes eligible.
- Simulated: the customers, the request context (built from the simulator's features, not a bank's database), and fraud reports
  arriving at once.

Results, on 700 customers over 150 days with 35% attacked and the hard-minimum gates:
- **Never weaker:** in 0 of 23,981 payments did the switch ask for less than the switch off.
- **₹10k–1 lakh** became eligible and moved to the model on day 115. The earliest possible day is 58: 30 days to mature, a week
  to close, four weekly passes.
- **₹1 lakh+ never moved.** Its rules always ask for a face check, and the model missed a few frauds the rules caught, which that
  band never tolerates.
- **₹0–2k and ₹2k–10k never moved.** Their grading says the model missed more fraud than the rules. The model keeps small
  payments light on purpose; the rules ask for a face check for every new payee. With this gate they will likely stay on the rules.
- **The cost of collecting:** honest customers asked for a face check or more rose from about 6% to 27% in the lowest band, and
  from 14% to 39% in ₹2k–10k, compared with the model alone. That is the price of not trusting the model yet. The bank sees it per
  band in the console.
- **A second run** (2,000 customers, default gates, 67,217 payments) also had 0 weaker payments. No band moved in 150 days:
  - ₹10k–1 lakh had the volume but asked more honest customers for checks than the rules in its last grading;
  - ₹1 lakh+ lacked both volume and a clean record.
- **Decided (17 November 2026): the strict rule stays for every band.** A margin for bands 0–1 was considered and rejected: the
  model may miss up to 2 points more fraud if it saves at least 10 points of step-ups, in bands that hold about 0.2% of the fraud
  money. It would be the first place the switch accepts a known loss in fraud caught. So those bands will likely stay on the rules,
  at the friction cost above.

**The live smoke test.** `scripts/smoke_demo.py` was run against a fresh local core and demo bank (seeded with `--learning`), once
per setting:
- **Switch off:** all 83 checks pass; no learning row is written and no decision carries a band state, exactly as before.
- **Switch on:** all 83 pass again:
  - every one of the 31 decisions was made under COLLECTING;
  - the rules added checks to 13 of them;
  - the new customers' 15 settled payments wrote one anonymous row each (completed, declined, by band).

**Security review.** Done by hand: the `/security-review` command expects committed changes against a remote branch, and this
work is uncommitted. Checklist:
- **No new data from the app:** the Android app is untouched; `git status` shows no change under `mfa-android/`.
- **Nothing identifying in `learning_row`:** pinned by the column test and the value test (phase 2).
- **The two-person rule is enforced on the server:** in `band_gate.approve`, by operator id. Tested with real operator sessions,
  CSRF, wrong role and wrong bank.
- **Every fail-closed path has a test:** no state, unknown state, read error, stale, future time, learning-store failures.
- **No other way to change a bank's policy bypasses the guard:** only `apply_policy` writes it after creation, and a new bank
  starts with the switch off.

Two findings, both fixed and tested:
1. **A relaxing policy request could have relaxed more than was approved.** It stored the whole new `collection` section, so a
   stricter change made between the request and its approval would have been silently undone. A request now carries the section
   it was made against, and approval is refused (`POLICY_CHANGED`) if the section changed since.
2. **A failed learning-row write logged its traceback.** A database error's text can carry the row's values, the label link among
   them. It now logs only the failure's kind; a test checks that no link, transaction id or amount reaches the log.

Not done:
- **No admin console screen.** The endpoints exist and are tested; a page in `admin.html` to show the per-band figures and the
  pending requests is still to build.
- **The live smoke test is not automated in CI.** It needs the running stack, as before.

### Phase 6: documents

Update `docs/architecture.md`, `docs/new-customer-learning.md` and the report (sections 11, 14 and 19) with what the band switch
does, what is stored, what is not, and the consent line: "We keep an anonymous record of payment size bands, not amounts, to
decide when our automated checks can be trusted."

**Outcome:** the documents match the code. **Tests:** none (documents only).

**Status: done.**
- **`docs/architecture.md`:** the three new tables in section 7, and a new section 8 on the band switch.
- **`docs/new-customer-learning.md`:** section 4b, the customer test and the band test side by side, and item 8 in the build list.
- **`README.md`:** a section on the band switch, the replay command, retention and the policy note.
- **`docs/report-addendum-band-switch.md`:** ready-to-paste text for the project report, which is a PDF outside the repository:
  - a new section 11.5;
  - rows for section 14;
  - paragraphs for sections 17 and 18;
  - a line for section 19;
  - glossary entries;
  - the privacy-notice line.
- **The consent line is privacy-notice wording, not a change to the app's biometric consent.** Changing `CONSENT_VERSION` would
  make every customer consent again before their next face or voice check, and the band records are not biometric.

---

## 5. Files changed

**New:**

| File | What it does |
|---|---|
| `amfa/engine/risk/collection_bands.py` | the four bands and which band a payment is in |
| `amfa/engine/band_switch.py` | a payment's coarse view, the rules added while collecting, and the merge with the model's own plan |
| `amfa/core/learning_store.py` | the anonymous rows: writing, labels, the daily cap, retention |
| `amfa/core/band_gate.py` | grading, the gate, fail-closed state, two-person requests, fallback |
| `amfa/demo/band_replay.py`, `scripts/replay_band_switch.py` | a simulated bank replayed through the switch |
| `docs/band-switch-replay.md` | the replay results |
| `docs/report-addendum-band-switch.md` | text for the project report |

Tests: 229 in 8 new files:
- `tests/engine/test_collection_bands.py`, `test_band_view.py`, `test_band_switch.py`;
- `tests/core/test_learning_store.py`, `test_learning_store_e2e.py`, `test_band_gate.py`, `test_band_gate_admin.py`,
  `test_band_switch_e2e.py`.

**Changed, all additive:**
- `engine/policy.py`: the `collection` section with its hard minimums;
- `engine/context.py`: the band state on the request, and the band fields on the plan;
- `engine/decide.py`: the view, the rules added while collecting, and the merge;
- `core/db.py`: the `learning_row`, `band_gate` and `band_switch_request` tables;
- `core/context_builder.py`: reads the payment's band state, fail-closed;
- `core/orchestrator.py`: writes the row when a payment settles or is reviewed;
- `core/api_tenant.py`: a cancelled payment's row, labels from bank feedback, and the policy guard;
- `core/api_admin.py`: the band endpoints;
- `core/retention.py`: link expiry and row retention;
- `core/app.py`: the nightly grading;
- `cli.py`: `band-gates`;
- `README.md`, `docs/architecture.md`, `docs/new-customer-learning.md`.

**Not touched:**
- the Android app;
- the risk model and its training;
- the factor services;
- the bank's own amount bands and floors;
- the biometric consent.

**Not built:** an admin console screen for the band figures and the pending requests. The API is there and tested.

---

## 6. Model updates: a new version earns each band before it decides

**The gap.** The band switch approved "the model" for a band without remembering which version. New weights would have been
trusted at once in every approved band, and the grading mixed records scored by old and new versions.

**The design (shadow first).**
- A new version is installed as a **candidate** next to the active one. It scores every payment silently and decides nothing; the
  active version keeps deciding everything.
- The candidate is graded per band on fresh payments it has never seen, with the same gates.
- Two operators of the bank approve it per band. Promotion then moves approved bands to it with no gap, and sends a band still on
  the old version that the candidate has not earned back to the rules. The preview shows this first.
- **Safety net:** an active model changed any other way (for example, written directly by the training script) is never trusted
  in a band that was not approved for that exact version.
- **The cost:** about 2 to 3½ months of shadowing before a new version takes over a band. Customers feel nothing meanwhile.

| Phase | What | Status |
|---|---|---|
| 1 | Record the version: the decision's `bandView.modelVersion`, `learning_row.model_version`, and `band_gate.model_version` (the version a state was graded on, or for MODEL, approved on and never overwritten by later gradings). Schema v6 adds the columns to older databases, keeping their rows. Rows without a version are never graded | **built** |
| 2 | Safety net: grade only the active version's rows; a version change sends ELIGIBLE and MODEL bands not approved for it to the rules (audit and security event); the decision trusts a MODEL band only if the version that scored the payment is the approved one; approvals carry the version (`MODEL_CHANGED`) | **built** |
| 3 | The candidate scores silently: a `CANDIDATE` pointer next to `ACTIVE`, commands to install or remove it, `train --candidate`; its level and version on each row; a failing candidate never touches a payment | **built** |
| 4 | Grade the candidate per band; two-person approval tied to the candidate's version; a new candidate starts from zero | **built** |
| 5 | Promotion: a preview per bank and band; band states first, then the pointer, failing closed if interrupted | **built** |
| 6 | End-to-end replay with a candidate partway, the demo bank flow, full suite, smoke test on and off, security review, docs | **built** (see below for what was and was not run) |

Phase 1 tests are in `tests/core/test_model_version.py`:
- the version reaches the view and the row;
- a model that could not score leaves none, and odd values are stored as none;
- decisions are unchanged;
- an older database gains the columns and keeps its rows, and running the upgrade again changes nothing;
- gradings note their version, and an approved band keeps its version through later gradings;
- the console shows each band's version.

Also updated: the privacy column list (on purpose), the view's field list, and an end-to-end check that a demo-bank payment's row
carries the scoring model's version.

**Phase 2, as built.**

`band_gate.evaluate`:
- grades only rows whose `model_version` is the version deciding now;
- does nothing at all when no model can be loaded, so a band on MODEL is then decided by the rules after 48 hours;
- first runs `model_changed` on any band that is ELIGIBLE or on MODEL for another version. That band goes to COLLECTING, its
  streak is reset and its pending requests are cancelled. This is audited (`band_gate.model_changed`, with both versions) and, for
  a band that was on MODEL, raised as a `BAND_MODEL_CHANGED` security event (HIGH for ₹1 lakh+). Going back to the old version
  later does not bring the approval back.

`band_gate.decision_state` hands the decision the band's state and, for MODEL, its approved version.
`band_switch.effective_state` then treats the band as MODEL only if the version that scored this very payment is that one. Any
other, unknown or missing version means the stricter plan. So new weights loaded directly are not trusted, even before the night's
grading. The model-only plan worked out while collecting is now computed with the switch off, rather than by claiming MODEL.

Requests store `{"modelVersion": ...}`:
- a request is refused with no loadable model (`NO_MODEL`), or for a band graded on another version (`MODEL_CHANGED`);
- a pending request for another version is replaced;
- approval is refused if the deciding version is not the one asked for (`MODEL_CHANGED`);
- the instant ₹1 lakh+ fallback counts only frauds decided by the band's approved version.

Phase 2 tests:
- `tests/core/test_model_change.py`, 24 tests:
  - grading by version, and nothing graded without a model;
  - bands dropped on a change (MODEL, ELIGIBLE, the ₹1 lakh+ event, collecting untouched, same version kept, no comeback);
  - decision-time trust only with the approved version;
  - a wrong version is never weaker than the right one, at every level;
  - every approval case.
- An end-to-end demo-bank test: new weights swapped in mid-way are not trusted on the very next payment, and the night's grading
  drops the band.
- The existing band tests now give their rows and states a model version, as real ones have.

**Phase 3, as built.**

The model store (`amfa/engine/risk/scorer.py`) gains a `CANDIDATE` pointer next to `ACTIVE`:
- `set_candidate(version)` installs one. It must be a plain folder name (letters, digits, `.`, `_`, `-`, at most 64 characters, no
  path), not the active version, and must load with every file matching its manifest. The pointer is written all at once, and a
  new candidate replaces the old.
- `clear_candidate()` removes it.
- `candidate()` returns None, and never raises, for: no pointer, a bad name, the active version, or a version that no longer
  loads.
- Commands: `python -m amfa.cli risk-candidate show|set <version>|clear` (audited), and
  `python -m amfa.engine.risk.train --candidate`, which installs the new version as the candidate and leaves `ACTIVE` alone.
  Without the flag, training behaves exactly as before.

In `decide()`, once the real plan is final, the candidate scores the same payment:
- it takes the same path the active model took (the learning plan or the model plan, with the same reliefs and policy) through
  the same functions, which now take an optional model;
- only its level and version are added to the band view (`candidateLevel`, `candidateVersion`);
- it runs only when the bank has the switch on, and only for payments;
- any failure, or a candidate whose result names another version, leaves no trace.

The learning row keeps both values or neither (`candidate_level`, `candidate_version`; schema v6). The replay writes them too.
Version names are checked with the model store's own rule wherever they are logged or stored, so a value like `../x` is never
graded.

Phase 3 tests:
- `tests/core/test_candidate.py`, 68 tests:
  - **decisions are identical with and without a candidate** on 30 combinations of active level, candidate level and band state,
    compared on every decision field and the band view;
  - its level equals what it says as the active model, at every level, for ready and learning customers;
  - a failing, unloadable or mislabelled candidate leaves no trace;
  - no scoring with the switch off or for account changes;
  - the row keeps both values or neither;
  - installing, replacing and removing; name, active-version and integrity refusals;
  - a pointer that has gone bad;
  - `train --candidate` installs and leaves `ACTIVE` alone.
- An end-to-end demo-bank test: with a candidate installed, a real payment gets the same checks and goes through, and its row
  carries the candidate's answer, equal to what it decides as the active model on the same payment.

**Phase 4, as built.** A new `band_candidate` table holds the candidate's progress per bank and band, apart from `band_gate`, so
grading the candidate can never move a band.

The nightly grading also grades the installed candidate in every band. It uses only the rows it scored (`candidate_version`), its
own answers (`candidate_level`), and the same gates, maturity and weekly streak:
- after 4 passes it is ELIGIBLE;
- two different operators make it APPROVED (`request_candidate`, then the usual `approve`, kind `CANDIDATE_FOR_BAND`, tied to the
  candidate's version);
- a candidate that starts losing goes back to COLLECTING and loses any approval;
- installing another candidate replaces the old one's progress and approvals (audited, `band_candidate.replaced`);
- removing it leaves nothing.

**One grading per version, whichever role it played.** `level_for` grades a version on its answers as the deciding model *and*
as the watching candidate. So a promoted candidate keeps the evidence it built while watching.

**Phase 5, as built.** `promotion_preview` shows, for every bank with the switch on and every band, what promoting will do:
- **MODEL:** the candidate was approved there;
- **ELIGIBLE:** it has earned the band but was not approved yet;
- **COLLECTING:** anything else.

A band on the current model that the candidate has not earned is listed as a **drop**.

`promote`:
- refuses while there are drops unless they are confirmed (`WOULD_DROP`), and refuses a candidate that no longer loads
  (`CANDIDATE_BROKEN`);
- otherwise writes every band's new state, tied to the new version and carrying the candidate's streak, and commits it. It then
  removes the candidate's progress, cancels pending requests, audits each band and the promotion, raises a security event for each
  drop, and **only then** switches the active pointer (`scorer.promote_candidate`, all at once);
- **fails closed if it stops between the two steps:** the bands name a version that is not deciding, so decisions use the stricter
  plan and the night's grading takes them back.

Entry points:
- `GET /admin/api/risk/candidate` and `POST /admin/api/risk/candidate/promote` (`{"confirmDrops": bool}`), for platform operators
  only;
- `POST /admin/api/band-gates/{band}/request-candidate`, for bank operators;
- `python -m amfa.cli risk-candidate preview|promote [--confirm-drops]`.

The per-band console view shows the candidate's progress next to the band's own.

**Phase 6, as built.** The replay takes an `on_day` hook, where a model update happens, and approves an eligible candidate the way
it approves an eligible band. Two replays with perfect stand-in models (`tests/core/test_band_switch_e2e.py`):
- **Shadow, then promote:** V1 earns the ₹10k–1 lakh band. V2 is installed as the candidate on day 5, earns the band while
  watching, and is approved. It is promoted the night it is trusted there while V1 decides it. The band never spends a day on the
  rules, and ends on V2.
- **Weights swapped in directly,** with no candidate, the day after V1 has been put on the band: the band is on the rules that very
  day, and V2 cannot earn it back within the 150 days.
- **A note on timing:** a candidate installed late (day 60) could not be approved within the replay's 150 days. It needs about 7
  weeks of its own payments for 2,000 genuine ones, plus 30 days to mature and 4 weekly passes. Promotion then correctly refused
  (`WOULD_DROP`).

Phase 4–6 tests:
- `tests/core/test_candidate_bands.py`, 27 tests:
  - candidate grading on its own answers, never moving the band;
  - the streak, losing, and replaced or removed candidates;
  - evidence carried into promotion;
  - two-person approval and its refusals;
  - every preview outcome;
  - refusals for drops, no candidate and a broken candidate;
  - promotion's band states, order and clean-up;
  - failing closed when stopped before the pointer;
  - an old version coming back needs trust again;
  - platform-only endpoints;
  - the store's own promotion.
- 5 replay tests in `tests/core/test_band_switch_e2e.py`.

**Security review of #7, by hand, no new findings:**
- **Who:** installing a candidate needs server access (the command line). Trusting it in a band needs two operators of that bank,
  tied to the candidate's version and scoped to the bank. Promotion needs a platform operator, and can only keep or tighten a band
  (drops must be confirmed).
- **Never the decision:** a candidate never reaches a decision, and its failures leave no trace.
- **Version names:** checked with one rule wherever they are installed, logged or stored.
- **Races:** promotion re-reads the candidate inside its own transaction, and the pointer switch refuses anything but that
  candidate.
- **Fail closed:** a stop between the two steps of promotion decides by the rules.
- **Logs:** carry only the version and the kind of error.

**Not run for #7:** the live smoke test against a running stack. It exercises payments with the switch on or off, which #7 does not
change; the demo-bank end-to-end tests cover #7's paths through a real in-process core.


---

## 7. Option A or B: does storing only bands cost the model accuracy?

The design keeps the exact amounts in the decision and the 90-day history (option A), and puts bands only in the anonymous records.
This measures what option B would cost: a system that knows every amount only by its band. Method, how to run it, and results are
in [amount-bands-accuracy.md](amount-bands-accuracy.md).

| Phase | What | Status |
|---|---|---|
| 1 | `amfa/engine/risk/amount_view.py` (`exact`, `bands4`, `bands8`; one log-midpoint value per band) and an optional `amount_view` in the simulator's dataset step, applied to the payment, its history and the 24-hour total; the true amount stays on the row for measuring money | built |
| 2 | `train.run(amount_view=...)`, recorded as `amountView` in the model card; training refuses to activate or install a banded model; the live scorer refuses to load one; a `by_collection_band` breakdown in the training test's measures | built |
| 3 | `scripts/compare_amount_storage.py`: per data seed, one model per view on the same data, compared on the same test rows; mean over seeds and the paired change from exact | built |
| 4 | Through the decision pipeline: covered by the training step's own test, which applies the reliefs and the habit rule to the banded inputs and banded 24-hour total. The script reports those figures next to the model-alone ones | built (no extra code needed) |
| 5 | The write-up: [amount-bands-accuracy.md](amount-bands-accuracy.md) | **built, with results** |

**Tested:** 33 tests in `tests/engine/test_amount_view.py`:
- the views, band values and the band switch's own edges;
- 8 bands never crossing the 4;
- the simulator unchanged without a view, and the same payments in the same order with one;
- inputs and the 24-hour total computed from banded amounts, the others untouched;
- the true amount kept for counting money;
- the model card's view and the per-band breakdown;
- the live scorer refusing a banded model, even made active by hand;
- training refusing to activate or install one;
- the script comparing on the same rows, with a paired summary and a full report.

The full suite passes, apart from the face-model file that fails on this Windows checkout. The comparison ran on 3 seeds × 3,000
customers.

**Result:** option B (bands only) costs about 13 points of fraud money caught at T3+, almost all of it on payments of ₹1 lakh and
above, which all look the same when only the band is known. Counts of fraud caught barely change, honest customers see slightly
more friction, and older customers are treated more evenly. **Option A stays.**


---

## 8. The band switch as its own service

The band switch's rows, band states and candidate progress are the one store the core would hand to analysts or auditors. This
moves them, and the logic that reads and writes them, out of the core into a service with a database of its own. The core keeps
the decision, the keys, the two-person approvals and the audit log.

**How it is split:**
- `amfa/bands/models.py`: the three tables (`learning_row`, `band_gate`, `band_candidate`), on their own metadata.
- `amfa/bands/engine.py`: all the band logic (grading, the gate, fallback, candidate progress, promotion's band states, rows,
  labels, link expiry, retention). It never writes the core's tables. What a change needs recorded comes back as events.
- `amfa/bands/store.py`: the one way the core reaches it.
  - `LocalStore`, the default: the engine inside the core, in the core's database and the caller's transaction. Nothing changes
    for a deployment that does not turn the service on.
  - `RemoteStore`, chosen when `AMFA_BANDS_URL` is set (`amfa/bands/remote.py`).
- `amfa/core/band_gate.py` and `amfa/core/learning_store.py`: what stays in the core. Two-person requests and approvals, the
  policy guard, the keyed-hash link (its key never leaves the core), and writing the events into the audit log.
- `amfa/bands/service.py`: the service, `python -m amfa.cli serve bands` (port 8104), with its database in `amfa/bands/db.py`
  (`AMFA_BANDS_DB_URL`).

**Security of the service:**
- **Only the core may call it.** Every request is signed with the internal token under the key id `core`, and a replayed request
  is refused (its own replay scope). It sits on the internal network only.
- **Nothing identifying can reach it.** Every request body is a strict model that refuses unknown fields. A row may carry only
  the allowed columns, each checked against its own pattern; a link must be 64 hex characters. Bodies are capped at 1 MB.
- **Its own secrets and data.** Its own Postgres database and user, its own Redis for replay state, its own master key, and its
  own network (`bandsdata`). In production it refuses to start under any role but `bands`, refuses the core's database URL, and
  requires Postgres.
- **Links expire on their own.** The service wipes links at their time, hourly and at start-up, even if the core is down.

**Failing closed when the service is slow or down:**

| Call | What happens |
|---|---|
| The payment's band state | One call, waiting at most `AMFA_BANDS_TIMEOUT_S` (0.2 s). No answer, a refusal or anything odd: COLLECTING, the stricter plan. |
| Nightly grading | Not due, nothing moves. A band on the model is then decided by the rules once its last grading is two days old. |
| A new row or label | Held in the core's `band_outbox` and sent again by the sweeper, less often each time. Dropped once its link would have expired. |
| Operator actions (approve, revert, promote, read) | Refused with 503 (`BANDS_UNAVAILABLE`). Nothing is half-applied; a promotion never reaches the pointer switch. |

**Deployment:** `deploy/docker-compose.yml` runs `bands`, `postgres-bands` and `redis-bands`, and the core points at it with
`AMFA_BANDS_URL=http://bands:8104`. `deploy/vm-deploy.sh` adds the service's key and password to an older `.env`.

**Moving existing data:** for a bank that already has band data in the core's database:
1. Stop the core.
2. `docker compose run --rm bands-move --dry-run` counts what the core holds.
3. `bands-move` copies rows and states by their keys, so a second run copies nothing twice.
4. It checks every row arrived, and only then deletes them from the core. If anything is missing, the core keeps all of them.

| Phase | What | Status |
|---|---|---|
| 1 | Band tables and logic moved into `amfa/bands` behind a store; the core writes the returned events | built |
| 2 | The service and its database, signed internal requests, strict request bodies | built |
| 3 | `RemoteStore`, the 0.2 s fail-closed decision call, the outbox, 503s for operator actions, link expiry in the service | built |
| 4 | Compose services, own Postgres, Redis and network, the `bands` role checks, `bands-move` | built |
| 5 | This section, `architecture.md`, `README.md`, the report addendum | built |
| Test | See below | **passed** |

**Tested:** 84 new tests in 2 files, and every earlier band test still passes.
- `tests/core/test_bands_service.py` (75 tests):
  - **The same behaviour inside the core and through the service.** The same 9 scenarios run in both places: grading to ELIGIBLE,
    two operators moving a band, a version change cancelling an approval, a fraud in the ₹1 lakh+ band sending it back, one row
    per payment, labels and expired links, purge, a candidate trusted and promoted, and the admin view. In the service, the rows
    are never in the core's database.
  - **Only the core can call it.**
    - Every one of the 14 endpoints refuses an unsigned request before reading its body.
    - Signed requests are refused when they come from another caller, use the wrong secret, carry an old timestamp, have their
      body changed, are replayed, or are signed for another endpoint.
  - **Nothing identifying gets in.**
    - A row is refused if it carries a customer, an amount, a payee, a device, a payment id or an IP address, and nothing is
      stored.
    - A row is refused if any field is outside its pattern.
    - An extra field next to a row, or on a label, is refused.
    - The engine itself refuses a field that is not a band row field.
    - Bodies over 1 MB are refused, and the service publishes no API description.
  - **Production settings.** The `bands` role loads only with its own Postgres database and without the core's database URL, and
    the service will not start under another role.
  - **Failing closed.**
    - In 10 kinds of bad answer (down, timeout, 500, 401, not JSON, a list, MODEL with no version, an unknown state, an empty
      answer), the payment is decided under COLLECTING.
    - The decision call waits 0.2 s. Over a real network, a service that takes 1.5 s costs the payment under 0.8 s, and the payment
      gets the stricter plan.
    - With the service down, grading is skipped, and a band on the model goes back to the rules once its grading is stale.
    - Operator actions are refused with 503 and nothing is half-applied: the request still waits for its second person and goes
      through once the service is back.
    - A promotion never reaches the pointer switch.
  - **The outbox.**
    - A row and its label held during an outage arrive once, in order.
    - A row the service took when the core saw an error is still kept once.
    - A row whose link would have expired is dropped unsent.
    - Retries back off from 30 s to 120 s.
    - Changes a held row causes when it arrives (a fallback) are recorded in the core.
    - The outbox holds only the row's fields.
  - **The move.**
    - It counts, copies 2,500 rows and the band states exactly, empties the core only afterwards, and copies nothing twice.
    - If any row is missing after the copy, the core keeps every row.
    - It refuses to copy the core into itself.
- `tests/core/test_bands_remote_e2e.py` (9 tests): the Demo Bank against a real in-process core whose band switch runs as the
  service.
  - Rows land only in the service's database.
  - Labels and cancellations reach them.
  - A payment is decided under the state the service holds.
  - With the service down, slow, or answering 503, a payment still goes through. It is decided under COLLECTING, with never
    fewer checks than with the service up, and its row arrives once the service is back, exactly once.
  - With the switch off, nothing reaches the service. The customer and the bank see no new field.

**Full suite:** 916 pass. The one failure is the face-model file that fails on this Windows checkout because of its line endings;
it failed the same way before this change.

**Found and fixed while testing:**
- **The admin band view** (`GET /admin/api/band-gates`) answered 500 instead of 503 while the service was down. Every part of the
  core now answers an unreachable band service with 503 `BANDS_UNAVAILABLE`.
- **A row's label source and a label's value** were checked only for length. They are now limited to the four real sources and
  the two real labels.
- **The upgrade test** built its old database without the band tables, which now have their own metadata.
- **The new end-to-end test** was the first to import the Demo Bank, which reads its bank once at import, so the customer-app
  tests after it ran against the wrong bank. It now removes the module again when it was the one that imported it.

**Known limit:** when the second operator approves a band, the service moves it first and the core then writes its audit entry.
If the core's database failed in that instant, the band would stand on the model (both approvals were given and checked) without
its audit entry in the core. Inside the core (the default) both are one transaction.

