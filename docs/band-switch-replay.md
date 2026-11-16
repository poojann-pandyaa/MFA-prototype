# Band switch: a simulated bank replayed from day one

Made by `python scripts/replay_band_switch.py` (see `amfa/demo/band_replay.py` for exactly what is real and what is simulated).

- **Real:** every payment goes through the real decision code, its anonymous row is written, the real nightly grading runs, and
  two simulated operators approve a band the night it becomes eligible.
- **Simulated:** the customers. Their numbers show that the mechanism works and what it costs, not how a real bank's customers or
  criminals behave.

## What the two runs show

1. **The switch never made a payment weaker.** In 91,198 payments across both runs, the switch never asked for less than
   the bank would have without it.
2. **A band moves only when it has earned it.** In the small bank with the hard-minimum gates, ₹10,000–1 lakh moved to the
   model on day 115, after four weekly passes on mature data. The earliest possible day is 58.
3. **₹1 lakh and above never moved,** in either run. Its rules always ask for a face check. The model missed a few frauds there
   that the rules caught, and that band tolerates none.
4. **₹0–2,000 and ₹2,000–10,000 never moved either, and probably never will with this model and this gate.**
   - Both grading reports say "the model missed more fraud than the rules".
   - That is by design on the model's side: it keeps small payments light (the small-payment and new-customer reliefs). The
     rules ask for a face check for every new payee.
   - So the gate "miss no more fraud than the rules" is very hard for the model to pass in the small bands.
   - In the earlier simulator measurement, payments under ₹10,000 hold about 0.2% of the fraud money.
   - The price of keeping these bands on the rules: 27% (₹0–2k) and 39% (₹2k–10k) of honest customers' payments are asked for
     a face check or more, against 6% and 14% with the model alone.
   - This was a deliberate decision, not a bug. See "Decided: no margin for the small bands" below.
5. **With the defaults (5,000 genuine, 100 fraud, 8 weeks) at 2,000 customers, no band moved in 150 days.**
   - ₹10,000–1 lakh had the volume, but in its last grading the model asked more honest customers for extra checks than the
     rules did.
   - ₹1 lakh+ lacked both volume and a clean record.

## Decided: no margin for the small bands

The gate could allow the model to miss slightly more fraud than the rules in bands 0 and 1 when it saves a lot of honest
customers' effort. For example: at most 2 percentage points more fraud missed, for at least 10 points fewer step-ups.

That would make those bands reachable. It would also be the first place where the switch accepts a known loss in fraud caught.
Decided on 17 November 2026: not adopted. The strict rule stays for every band.

## Run 1: a small bank, hard-minimum gates

Model `risk-20261116-151006`. 700 simulated customers, 150 days, 35% attacked, seed 11; 23,981 payments over 150 days. Gates: the hard minimums; fraud reports: every fraud, reported by the bank. Two operators approve a band the night it becomes eligible. Run 2026-11-16 21:09.

Payments where the switch asked for less than the switch off would have: **0** (must be 0).

| Band | Payments (fraud) | Eligible on day | On the model from day | Fell back on days | Honest customers stepped up: collecting / on model / model alone | Fraud caught: collecting / on model / model alone |
|---|---|---|---|---|---|---|
| ₹0–2,000 | 5,270 (140) | never | never | – | 26.6% / – / 5.5% | 92.1% / – / 61.4% |
| ₹2,000–10,000 | 5,286 (253) | never | never | – | 39.1% / – / 14.4% | 88.9% / – / 67.2% |
| ₹10,000–1 lakh | 6,279 (388) | 115 | 115 | – | 55.8% / 48.7% / 28.0% | 98.8% / 92.8% / 97.4% |
| ₹1 lakh and above | 7,146 (183) | never | never | – | 100.0% / – / 19.9% | 100.0% / – / 98.4% |

What the last grading said was still missing, per band:

- ₹0–2,000: needs 2000 genuine payments; the model missed more fraud than the rules
- ₹2,000–10,000: needs 2000 genuine payments; the model missed more fraud than the rules
- ₹10,000–1 lakh: nothing
- ₹1 lakh and above: the model missed more fraud than the rules; the model let through a fraud the rules would have stopped

Stepped up = asked for a face or voice check, or held. Simulated customers: these show the mechanism works and what it costs, not how a real bank's customers or criminals behave.
Took 172s.

## Run 2: a larger bank, default gates

Model `risk-20261116-151006`. 2,000 simulated customers, 150 days, 35% attacked, seed 11; 67,217 payments over 150 days. Gates: the defaults; fraud reports: every fraud, reported by the bank. Two operators approve a band the night it becomes eligible. Run 2026-11-16 21:06.

Payments where the switch asked for less than the switch off would have: **0** (must be 0).

| Band | Payments (fraud) | Eligible on day | On the model from day | Fell back on days | Honest customers stepped up: collecting / on model / model alone | Fraud caught: collecting / on model / model alone |
|---|---|---|---|---|---|---|
| ₹0–2,000 | 14,129 (286) | never | never | – | 26.7% / – / 5.5% | 91.6% / – / 59.4% |
| ₹2,000–10,000 | 14,672 (618) | never | never | – | 38.8% / – / 13.7% | 85.0% / – / 63.3% |
| ₹10,000–1 lakh | 17,373 (1,070) | never | never | – | 56.5% / – / 29.7% | 97.8% / – / 96.9% |
| ₹1 lakh and above | 21,043 (570) | never | never | – | 100.0% / – / 19.9% | 100.0% / – / 98.6% |

What the last grading said was still missing, per band:

- ₹0–2,000: the model missed more fraud than the rules
- ₹2,000–10,000: the model missed more fraud than the rules
- ₹10,000–1 lakh: the model asked more honest customers for extra checks than the rules
- ₹1 lakh and above: needs 10000 genuine payments; needs 200 real fraud reports; the model missed more fraud than the rules; the model let through a fraud the rules would have stopped

Stepped up = asked for a face or voice check, or held. Simulated customers: these show the mechanism works and what it costs, not how a real bank's customers or criminals behave.
Took 458s.
