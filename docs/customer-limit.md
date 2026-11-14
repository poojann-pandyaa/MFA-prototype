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

## 2. Big and small spenders

The model compares each payment with that customer's own history (their usual amount, their largest payment, what they usually pay this payee,
their hours, devices and places). Measured on made-up customers, with the model active on 14 Nov 2026:

| Customer | Payment | Model's level |
|---|---|---|
| Pays ₹1-2k a day | ₹1,500 / ₹5,000 / ₹10,000 | T1 (code and typing) |
| | ₹50,000 | T2 (adds face) |
| | ₹1 lakh | T3 (adds voice) |
| Pays ₹2-5k a day and about ₹1 lakh a week | ₹1 lakh to a regular payee | T2 |
| | ₹5 lakh to a regular payee | T4 |
| | ₹1 lakh from a new phone at 3 am | T4 |

So the principle works in direction: the same ₹1 lakh is T3 for the small spender and T2 for the weekly big spender, and a new phone at night is T4 for anyone.

**Two weaknesses remain, and a retrain did not fix them:**
1. A jump in amount alone, with everything else familiar, scores near zero. A customer whose payments are about ₹1.5 lakh and who pays ₹15 lakh to a
   regular payee from their own phone in the afternoon gets only T1. In the simulator that pattern is usually a genuine large purchase, so the model learned it.
2. A routine large payment to a regular payee still gets T2 for the weekly big spender (face check).

A model trained with each simulated customer's spending scale stretched (`SCALE_SPREAD` in `amfa/engine/risk/scenarios.py`, 1.0) came out about the same
(risk-20261114-114801: PR-AUC 0.953 vs 0.954, the same fairness failure for seniors, ratio 2.5). It was left inactive.

**What would fix both**, as explainable rules that only add friction, since the model cannot learn what the simulator does not contain:
- raise to at least T2 when a payment is more than 1.5x the customer's largest in the last year, and to T3 above 3x;
- lower a payment the customer makes on its usual schedule, to its usual payee, at its usual amount, to T1 even in the high amount band
  (today `regular_payee_relief` stops at the medium band).

Real fraud labels would settle the rest (see `docs/ai-approach.md`, stage 1).

## 3. Not done

- Fairness gate: the active model and the candidate both fail it (customers over 60 are stepped up 2 to 3 times as often as average on genuine payments).
- The limit has no web SDK or admin-console screen yet; only the demo bank and the Android app.
