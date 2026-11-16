# The customer's own "no extra checks" limit, and how the risk model treats big and small spenders

## 1. The limit

A customer can choose an amount (₹500 to ₹50,000 a day in the demo; the bank sets its own maximum). Payments within it skip the
risk-based extra checks (face, voice). It is a convenience, so it is narrow on purpose.

**It applies only when all of these hold:**
- the payment (plus everything sent in the last 24 hours) is within the limit, so splitting a payment does not help;
- the payee has been paid before, and the phone has been used before;
- there is no hard warning sign: SIM swap, password or contact change, spoof attempt, repeated failed checks, a payee on a risk list, three new payees in a day;
- the model has not said T4 (a manual review is never skipped).

**It never goes below** the regulatory minimum or the bank's own floors for that kind of payment: two factors, one dynamic, still apply.
In practice it turns "code, typing and face" into "code and typing".

**Setting it is protected**, because it is what someone who took over an account would want to change:
- it is saved only against a change-limit transaction that passed its checks, for the same amount, used once;
- raising it waits out the bank's cooling-off (`customerBypass.coolingOffHours`, 24 by default; the demo seeder sets 0);
  lowering or turning it off is immediate and needs no checks;
- the bank turns the feature on (`customerBypass.enabled`) and sets the most a customer may choose (`maxMinor`); the platform adds its own ceiling
  (`AMFA_BYPASS_CAPS`, ₹50,000 by default).

| Where | What |
|---|---|
| Engine | `customer_bypass` in `amfa/engine/risk/threat.py`, applied in `amfa/engine/decide.py` after the other reliefs; the plan records the model's own level and the reason |
| AMFA API | `GET / PUT / DELETE /v1/users/{id}/bypass-limit` (`amfa/core/bypass.py`) |
| Demo bank | action `nocheck`, `DELETE /api/nocheck-limit`, and `noCheck` in `/api/me` |
| Android | Profile, "No extra checks": set a limit (checked like a payment), see a waiting raise, turn it off |

## 2. How big and small spenders are told apart (the habit score)

Nothing here compares a payment with a fixed rupee number. Every payment is compared with that customer's own last 90 days:

1. **How far above their own habit is it?** The amount is measured in "usual spreads" above the median of their own past payments (the
   spread is their own, blended with a generic one when they have little history, so three payments cannot fake a tight habit).
   If they have paid this payee at least twice, the payee's own amounts are the habit, so a weekly lakh to the same wholesaler is routine
   and a lakh to a shop they pay a few thousand is a jump. That number is the `habit_surprise` feature.
2. **The model sees it** (with 40 other facts) and learns from it.
3. **Habit alone also sets a minimum level.** The cut-offs are percentiles of simulated genuine payments (2.5% of genuine payments reach T2
   and 0.6% reach T3 from habit alone), stored in the model card, so "unusual" means "rare among customers like this", not a number someone chose.
4. **A routine payment is kept light** even at a high amount: same payee at least three times over at least 30 days, about the usual amount,
   a phone they use, no warning sign. The bank's high-value floor is also eased for it (`routineEasesHighValue`, on by default); the legal
   minimum stays.

Measured through the whole decision path (made-up customers; the checks are the plan, before the bank's own floors):

| Customer | Payment | Checks |
|---|---|---|
| Pays ₹1-2k a day | ₹1,500 / ₹5,000 | code + typing |
| | ₹10,000 to a regular payee | + face |
| | ₹50,000 | + face + voice |
| | ₹1 lakh (regular or new payee) | + face + voice |
| Pays ₹2-5k a day and ₹1 lakh every week to one wholesaler | the usual weekly lakh (even ₹1.2 lakh) | code + typing |
| | ₹5 lakh to that wholesaler | + face + voice |
| | ₹1 lakh to a shop they pay a few thousand | + face + voice |
| | the usual lakh from a new phone at 3 am | + face + voice |
| Usually pays ₹1.5 lakh | ₹3 lakh / ₹6 lakh | code + typing |
| | ₹15 lakh | + face |

## 3. The 90-day rule

The bank may keep a customer's transactions for 90 days at most, so the system reads, and keeps, no more.
- The risk model reads the last 90 days (`HISTORY_DAYS`). A model trained on a longer window is refused at load time.
- `amfa/core/retention.py` deletes everything older, hourly and by `python -m amfa.cli purge`: transactions, decisions with their inputs,
  sessions, codes, attempts, proofs, review cases, fraud labels, webhook deliveries, security events, places and payee relationships no
  transaction explains. The behaviour service (`ueba`) reads and keeps 90 days of payment events. Customers, enrolled templates, devices
  and the audit log (ids and outcomes only) stay. `AMFA_RETENTION_DAYS` changes the window.
- Consequence: a payment that repeats less often than every 90 days (quarterly, half-yearly, yearly) is not recognised as repeating, so it looks like
  a first payment. The simulator keeps those cases as genuine hard examples. The loss in accuracy from the shorter window was PR-AUC 0.954 to 0.921,
  with the same share of fraud caught (85% at T3 or above).

## 4. Not done

- **Fairness gate still fails**: customers over 60 are stepped up 2.5 to 3 times as often as average on genuine payments (the senior segment 2.71 times, against a limit of 1.25; the
  gate stays failed). Training now reweights: genuine payments of a segment that is stepped up too often count more (up to 8x, up to 4 rounds, `fairnessBoost` in the model card).
  That brought the senior segment from 2.71 to 2.30 and PREMIUM from 1.26 to inside the limit, with the same fraud caught (T3+ 85.0% to 84.8%) and PR-AUC 0.923 to 0.914. It stops there
  because the cause is behaviour, not the segment label (forcing the segment to STANDARD does not lower it): ordinary payments by seniors (4.9% vs 1.9%), assisted payments by a family
  member's phone, wedding and one-off large payments. Closing the rest needs a lower cut-off for seniors; at T2 that costs little (fraud caught 100% to 95%), at T3 it costs a lot (94% to 75%, 89 cases). Not applied: it weakens detection for the most attacked group.
- **Family helper** ([family-helper.md](family-helper.md)): an optional way to register the family member's phone that helps a customer pay. Built end to end (server, demo bank, Android), but measured effect on the gate is small (senior ratio 2.23 to 2.21 if everyone used it), because assisted payments are only about a tenth of seniors' genuine payments.
- A small payment on a new phone at 3 am stays at the light level by design (the small-payment cap); only the bank's hard-hold rules stop it.
- No real fraud labels yet: all numbers are on simulated customers.
- The seeded demo customers have no face or voice enrolled (a face cannot be faked into a demo), so in the live demo the plan for a large
  payment shows only a code and typing until the customer sets up their face in the app. `python -m amfa.demo.regression` shows the full plan.
