# Exact amounts or bands: what storing only bands would cost the model

The band switch stores each payment's amount band in its anonymous records. The decision itself still uses the exact amounts the
bank sends, and the 90-day history keeps them (option A). This page measures **option B**: a system that knows every payment, the
current one and every past one, only by its band. Would the risk model be worse, and by how much?

## How it is measured

- **Same customers, same code, only the amounts differ.** One model per *amount view* is trained on the same simulated customers
  with the project's own training (`amfa/engine/risk/train.py`). The views (`amfa/engine/risk/amount_view.py`):

  | View | What the model knows about an amount |
  |---|---|
  | `exact` | the amount itself: today's system, option A |
  | `bands4` | its band among the band switch's four (₹0–2,000, ₹2,000–10,000, ₹10,000–1 lakh, ₹1 lakh+): option B |
  | `bands8` | its band among eight finer ones (₹500, 1,000, 2,000, 5,000, 10,000, 25,000, 1 lakh as edges), to see how the loss grows as bands get coarser |

- **Each band is known by one value:** the middle of the band on a log scale. So ₹1,200 and ₹1,900 look identical in `bands4`.
- **What is banded:** every amount-based input is computed from banded amounts, as a band-only system would. That covers the
  amount compared with the customer's usual and with this payee, the 24-hour total, the habit score, and comparison with the
  sign-up answers. The 24-hour total that the reliefs look at is banded too.
- **What stays exact:** the row keeps the true amount, but only for measuring fraud money caught.
- **Same test.** For each data seed, every view is tested on exactly the same held-out weeks and rows, so the comparison is paired.
  Three seeds show whether a difference is more than one run's noise.
- **Two kinds of figure:** the model alone (its own threat levels), and the levels **after the reliefs and the habit rule**, which
  is what customers actually meet. Both are given overall and per band.
- **Nothing live changes.**
  - The banding exists only inside the experiment.
  - Training refuses to activate a banded model, or install it as the candidate.
  - The live scorer refuses to load one (`RiskModel` checks the model card's `amountView`), because live inputs are exact amounts
    and such a model would misread them.

## How to run it

```
python scripts/compare_amount_storage.py 3000 --seeds=7,8,9 --md=docs/amount-bands-accuracy-results.md
```

This trains 9 models (3 views × 3 seeds), each one booster on the first grid setting, and takes roughly 20–30 minutes. `--full`
uses the training defaults instead, which is much slower.

## Results

**In one line:** knowing amounts only by band costs about **13 points of fraud money caught** at the strongest checks, almost all
of it on payments of ₹1 lakh and above. The *number* of fraud cases caught barely changes. **Option A stays the right choice.**

### What the numbers say

1. **The biggest frauds are what option B misses.**
   - Fraud money caught at face and voice (T3+) falls from **98.7% to 85.5%**. In the ₹1 lakh+ band it falls from **99.1% to
     85.3%**.
   - Above ₹1 lakh every payment is known by the same value, so a ₹50 lakh transfer looks like a ₹1.2 lakh one, and "far above
     this customer's usual" disappears for exactly the payments where most of the money is.
   - The 8-band view loses the same there (85.3%), because its top band also starts at ₹1 lakh. This is what confirms the cause:
     finer bands below ₹1 lakh do not help the top.
2. **The count of fraud caught hardly moves:** −0.1 points at T2+ and −0.9 at T3+. Banding mostly loses the large cases, not the
   many small ones. That is the worst trade, because large cases are where the money is.
3. **A little more friction:** honest customers asked for a face check or more go from 5.2% to 5.6% overall, and up 2.8 points in
   the ₹2,000–10,000 band.
4. **One effect in banding's favour: fairness.** Older customers' step-up ratio falls from 2.71× to 1.77× the average. Their
   one-off large payments (weddings, hospitals) stand out less when amounts are blurred. This is the project's known weak spot,
   and it is worth noting, but it is bought with the fraud money above.
5. **Overall accuracy (PR-AUC) cannot be called either way.** It is −0.013 for 4 bands and +0.025 for 8 bands, but the exact model
   alone ranges from 0.76 to 0.90 across the three seeds. The difference is well inside that noise.

### What to do with it

- **Keep option A** (the current design): exact amounts in the decision and the 90-day history, and bands only in the anonymous
  records.
- **If option B were ever required,** the bands above ₹1 lakh would need to be split (for example ₹1 lakh, 5 lakh, 25 lakh, 1 crore)
  to win back the large cases. That is untested here: it would be the first thing to measure.

### The figures

Simulated customers: 3,000 per seed, 150 days; seeds 7, 8, 9; one booster on the first grid setting. Test rows per model: 26,834 (the same rows for every view of a seed). Run 2026-11-18 15:48.

Each cell is the mean over seeds; for a banded view, the change from exact on the same data is in brackets.

| Measure | exact | bands4 | bands8 |
|---|---|---|---|
| Accuracy (PR-AUC) | 0.835 | 0.822 (-0.013) | 0.860 (+0.025) |
| Fraud caught at face check or more (T2+) | 84.7% | 84.6% (-0.1 pts) | 84.1% (-0.6 pts) |
| Fraud caught at face and voice (T3+) | 82.5% | 81.6% (-0.9 pts) | 80.7% (-1.8 pts) |
| Fraud money caught (T3+) | 98.7% | 85.5% (-13.2 pts) | 85.6% (-13.1 pts) |
| Honest customers asked for a face check or more | 5.2% | 5.6% (+0.4 pts) | 5.5% (+0.4 pts) |
| Model alone: fraud caught T2+ | 99.7% | 97.4% (-2.3 pts) | 98.8% (-0.8 pts) |
| Model alone: honest customers stepped up T2+ | 4.8% | 4.8% (-0.0 pts) | 4.8% (-0.0 pts) |
| Older customers stepped up, times the average | 2.707 | 1.767 (-0.940) | 2.090 (-0.617) |

By the band switch's amount bands (after the reliefs and the habit rule):

| Measure | exact | bands4 | bands8 |
|---|---|---|---|
| ₹0–2,000: fraud caught T2+ | 73.3% | 70.0% (-3.2 pts) | 72.3% (-1.0 pts) |
| ₹0–2,000: honest stepped up T2+ | 1.4% | 0.5% (-0.8 pts) | 2.2% (+0.9 pts) |
| ₹0–2,000: fraud money caught T3+ | 60.2% | 49.8% (-10.4 pts) | 57.7% (-2.4 pts) |
| ₹2,000–10,000: fraud caught T2+ | 75.1% | 82.9% (+7.8 pts) | 76.5% (+1.3 pts) |
| ₹2,000–10,000: honest stepped up T2+ | 5.7% | 8.6% (+2.8 pts) | 6.6% (+0.9 pts) |
| ₹2,000–10,000: fraud money caught T3+ | 74.6% | 74.0% (-0.6 pts) | 75.4% (+0.9 pts) |
| ₹10,000–1 lakh: fraud caught T2+ | 96.9% | 96.3% (-0.7 pts) | 96.9% (+0.0 pts) |
| ₹10,000–1 lakh: honest stepped up T2+ | 8.4% | 8.4% (+0.1 pts) | 6.6% (-1.8 pts) |
| ₹10,000–1 lakh: fraud money caught T3+ | 92.9% | 91.3% (-1.6 pts) | 94.0% (+1.1 pts) |
| ₹1 lakh and above: fraud caught T2+ | 96.6% | 92.2% (-4.4 pts) | 94.2% (-2.4 pts) |
| ₹1 lakh and above: honest stepped up T2+ | 10.6% | 12.2% (+1.6 pts) | 11.8% (+1.2 pts) |
| ₹1 lakh and above: fraud money caught T3+ | 99.1% | 85.3% (-13.7 pts) | 85.3% (-13.7 pts) |

### Limits of this run

- **Three seeds.** Each figure is a mean of three paired runs. The per-seed figures are not kept in this table, except overall
  accuracy, whose spread is given above.
- **The fast training setting:** one booster on the first grid setting, not the release training. The comparison is fair between
  views, but the absolute figures are lower than the release model's.
- **Simulated customers only.**

## What was expected before the run, and the limits

- **The expectation:** the loss would come from "unusual for this customer" **within** a band, with the ₹10,000–1 lakh band, the
  widest bounded one, losing the most.
- **The result:** the ₹1 lakh+ band lost the most, because it has no upper edge at all. The ₹10,000–1 lakh band lost little
  (−1.6 points of fraud money).
- **All on simulated customers.** The numbers show the size of the effect in the project's simulator, not in a real bank.
- **Only the risk model is measured.** The bank's own amount bands and floors use the exact amount in both views, as they would
  for a bank that still sends amounts with each payment.
