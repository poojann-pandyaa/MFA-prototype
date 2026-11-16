# Five synthetic banks, tested end to end

Model under test: `risk-20261116-054239` (trained on seed 7; every bank here is a different population). Run 2026-11-16 11:21, 4,000 customers per bank, 150 days each. Reproduce with `python scripts/evaluate_datasets.py`.

## Summary

| Bank | Fraud caught (T3+) | Genuine stepped up (T2+) | PR-AUC | Ready after (median payments) | Mules flagged | Parity |
|---|---|---|---|---|---|---|
| 1. Baseline, a new population | 86.2% | 5.5% | 0.920 | 52 | 100% | 0 of 196 differ |
| 2. High-value bank | 92.4% | 5.4% | 0.907 | 69 | 96% | 0 of 184 differ |
| 3. Everyday micro-payments bank | 82.4% | 3.5% | 0.873 | 47 | 96% | 0 of 244 differ |
| 4. Senior-heavy bank | 85.3% | 5.6% | 0.895 | 44 | 100% | 0 of 188 differ |
| 5. Under attack, mostly new, few signals | 84.4% | 6.0% | 0.943 | 50 | 92% | 0 of 204 differ |

**63 of 65 checks met their expectation.** Missed: bank 3: weakest attack type (40+ cases) caught at T3+ (0.279); bank 4: new popular merchants wrongly flagged (0.353)

## Bank 1: Baseline, a new population

The training generator with a different seed: unseen customers, same kind of bank.

236,886 payments and changes, 2,612 fraud; 121s.

| Check | Measured | Expected | |
|---|---|---|---|
| PR-AUC | 0.920 | >= 0.8 | ok |
| fraud caught at T3 or above | 0.862 | >= 0.75 | ok |
| fraud money caught at T3 or above | 0.996 | >= 0.9 | ok |
| genuine payments stepped up to a face check or more | 0.055 | <= 0.1 | ok |
| weakest attack type (40+ cases) caught at T3+ | 0.526 | >= 0.3 | ok |
| senior step-up ratio (gate fails above 1.25) | 2.330 | <= 3.3 (does not get worse) | ok |
| new customers: median payments to ready | 52 | <= 120 | ok |
| genuine payments a learning customer would have held | 0.001 | <= 0.02 | ok |
| mule payees flagged | 1.000 | >= 0.9 | ok |
| reported payees flagged | 1.000 | >= 1.0 | ok |
| shops, utilities, friends wrongly flagged | 0.000 | <= 0.0 | ok |
| new popular merchants wrongly flagged | 0.200 | <= 0.35 | ok |
| live-path parity mismatches | 0 | <= 0 | ok |

- **Weakest attack types** (caught at T3+): repeat_scam 53% of 223, slow_drip 58% of 105, low_and_slow_mimic 59% of 49, bot 68% of 112
- **Genuine payments stepped up to a face check or more, by age**: 18-24 0.69x, 25-34 0.9x, 35-44 1.07x, 45-59 1.17x, 60-74 2.17x, 75+ 2.53x
- **New customers**: 68.8% ready within the run; median 52 payments, 90th percentile 105; false alarms 12.8% while learning, 4.6% after; learning-phase holds caught 65.2% of fraud.
- **Fraud met while a customer is still being learned** (fingerprint plan): held for a person 65% of cases / 87% of the money, a code added 19% of cases / 12% of the money, the fingerprint alone 16% of cases / 0% of the money.
- **Sign-up answers, first five genuine payments**: customers who answered 19.5% stepped up (of 5,634); the same population with answers hidden 24.1%.
- **Payee reputation**: friend 0/160 flagged, mule 25/25 flagged, new_merchant 6/30 flagged, reported 15/15 flagged, shop 0/65 flagged, slow_mule 0/10 flagged, utility 0/10 flagged. Slow mules caught 0.0% (expected low).
- **Live-path parity**: 196 payments compared, 0 differed.

## Bank 2: High-value bank

Premium and corporate customers; every typical payment six times larger. Tests that amounts mean nothing in themselves.

280,767 payments and changes, 2,288 fraud; 179s.

| Check | Measured | Expected | |
|---|---|---|---|
| PR-AUC | 0.907 | >= 0.8 | ok |
| fraud caught at T3 or above | 0.924 | >= 0.75 | ok |
| fraud money caught at T3 or above | 0.987 | >= 0.9 | ok |
| genuine payments stepped up to a face check or more | 0.054 | <= 0.1 | ok |
| weakest attack type (40+ cases) caught at T3+ | 0.621 | >= 0.3 | ok |
| senior step-up ratio (gate fails above 1.25) | 3.190 | <= 3.3 (does not get worse) | ok |
| new customers: median payments to ready | 69 | <= 120 | ok |
| genuine payments a learning customer would have held | 0.000 | <= 0.02 | ok |
| mule payees flagged | 0.960 | >= 0.9 | ok |
| reported payees flagged | 1.000 | >= 1.0 | ok |
| shops, utilities, friends wrongly flagged | 0.000 | <= 0.0 | ok |
| new popular merchants wrongly flagged | 0.250 | <= 0.35 | ok |
| live-path parity mismatches | 0 | <= 0 | ok |

- **Weakest attack types** (caught at T3+): repeat_scam 62% of 203, test_then_drain 80% of 44, scam_victim 87% of 237, credential_stuffing 89% of 146
- **Genuine payments stepped up to a face check or more, by age**: 18-24 0.68x, 25-34 0.93x, 35-44 1.01x, 45-59 1.2x, 60-74 2.88x, 75+ 3.31x
- **New customers**: 68.8% ready within the run; median 69 payments, 90th percentile 124; false alarms 16.3% while learning, 4.4% after; learning-phase holds caught 62.9% of fraud.
- **Fraud met while a customer is still being learned** (fingerprint plan): held for a person 63% of cases / 75% of the money, a code added 33% of cases / 25% of the money, the fingerprint alone 4% of cases / 0% of the money.
- **Sign-up answers, first five genuine payments**: customers who answered 34.8% stepped up (of 5,494); the same population with answers hidden 41.7%.
- **Payee reputation**: friend 0/100 flagged, mule 24/25 flagged, new_merchant 5/20 flagged, reported 15/15 flagged, shop 0/41 flagged, slow_mule 0/10 flagged, utility 0/10 flagged. Slow mules caught 0.0% (expected low).
- **Live-path parity**: 184 payments compared, 0 differed.

## Bank 3: Everyday micro-payments bank

Students and gig workers; payments a quarter the size and twice as often.

545,339 payments and changes, 2,440 fraud; 585s.

| Check | Measured | Expected | |
|---|---|---|---|
| PR-AUC | 0.873 | >= 0.8 | ok |
| fraud caught at T3 or above | 0.824 | >= 0.75 | ok |
| fraud money caught at T3 or above | 0.996 | >= 0.9 | ok |
| genuine payments stepped up to a face check or more | 0.035 | <= 0.1 | ok |
| weakest attack type (40+ cases) caught at T3+ | 0.279 | >= 0.3 | **MISS** |
| senior step-up ratio (gate fails above 1.25) | 2.560 | <= 3.3 (does not get worse) | ok |
| new customers: median payments to ready | 47 | <= 120 | ok |
| genuine payments a learning customer would have held | 0.001 | <= 0.02 | ok |
| mule payees flagged | 0.960 | >= 0.9 | ok |
| reported payees flagged | 1.000 | >= 1.0 | ok |
| shops, utilities, friends wrongly flagged | 0.000 | <= 0.0 | ok |
| new popular merchants wrongly flagged | 0.200 | <= 0.35 | ok |
| live-path parity mismatches | 0 | <= 0 | ok |

- **Weakest attack types** (caught at T3+): low_and_slow_mimic 28% of 41, slow_drip 38% of 94, repeat_scam 47% of 211, bot 57% of 94
- **Genuine payments stepped up to a face check or more, by age**: 18-24 0.86x, 25-34 0.98x, 35-44 1.09x, 45-59 1.16x, 60-74 2.24x, 75+ 2.93x
- **New customers**: 79.0% ready within the run; median 47 payments, 90th percentile 94; false alarms 9.7% while learning, 3.5% after; learning-phase holds caught 69.9% of fraud.
- **Fraud met while a customer is still being learned** (fingerprint plan): held for a person 70% of cases / 98% of the money, a code added 7% of cases / 2% of the money, the fingerprint alone 23% of cases / 1% of the money.
- **Sign-up answers, first five genuine payments**: customers who answered 15.6% stepped up (of 5,625); the same population with answers hidden 20.0%.
- **Payee reputation**: friend 0/310 flagged, mule 48/50 flagged, new_merchant 11/55 flagged, reported 30/30 flagged, shop 0/125 flagged, slow_mule 0/10 flagged, utility 0/10 flagged. Slow mules caught 0.0% (expected low).
- **Live-path parity**: 244 payments compared, 0 differed.

## Bank 4: Senior-heavy bank

Nearly half the customers are seniors, many low-digital and infrequent. Stresses fairness.

224,891 payments and changes, 3,646 fraud; 120s.

| Check | Measured | Expected | |
|---|---|---|---|
| PR-AUC | 0.895 | >= 0.8 | ok |
| fraud caught at T3 or above | 0.853 | >= 0.75 | ok |
| fraud money caught at T3 or above | 0.958 | >= 0.9 | ok |
| genuine payments stepped up to a face check or more | 0.056 | <= 0.1 | ok |
| weakest attack type (40+ cases) caught at T3+ | 0.340 | >= 0.3 | ok |
| senior step-up ratio (gate fails above 1.25) | 2.530 | <= 3.3 (does not get worse) | ok |
| new customers: median payments to ready | 44 | <= 120 | ok |
| genuine payments a learning customer would have held | 0.001 | <= 0.02 | ok |
| mule payees flagged | 1.000 | >= 0.9 | ok |
| reported payees flagged | 1.000 | >= 1.0 | ok |
| shops, utilities, friends wrongly flagged | 0.000 | <= 0.0 | ok |
| new popular merchants wrongly flagged | 0.353 | <= 0.35 | **MISS** |
| live-path parity mismatches | 0 | <= 0 | ok |

- **Weakest attack types** (caught at T3+): slow_drip 34% of 137, repeat_scam 49% of 314, low_and_slow_mimic 53% of 82, bot 63% of 146
- **Genuine payments stepped up to a face check or more, by age**: 18-24 0.63x, 25-34 0.85x, 35-44 0.93x, 45-59 1.02x, 60-74 2.45x, 75+ 2.61x
- **New customers**: 43.3% ready within the run; median 44 payments, 90th percentile 99; false alarms 13.8% while learning, 4.5% after; learning-phase holds caught 67.1% of fraud.
- **Fraud met while a customer is still being learned** (fingerprint plan): held for a person 67% of cases / 84% of the money, a code added 18% of cases / 16% of the money, the fingerprint alone 15% of cases / 0% of the money.
- **Sign-up answers, first five genuine payments**: customers who answered 20.7% stepped up (of 7,496); the same population with answers hidden 23.7%.
- **Payee reputation**: friend 0/85 flagged, mule 25/25 flagged, new_merchant 6/17 flagged, reported 15/15 flagged, shop 0/35 flagged, slow_mule 0/10 flagged, utility 0/10 flagged. Slow mules caught 0.0% (expected low).
- **Live-path parity**: 188 payments compared, 0 differed.

## Bank 5: Under attack, mostly new, few signals

Forty percent of customers are attacked, half are new to the bank, 60% skip the sign-up questions, and the bank sends few signals.

218,704 payments and changes, 6,783 fraud; 110s.

| Check | Measured | Expected | |
|---|---|---|---|
| PR-AUC | 0.943 | >= 0.8 | ok |
| fraud caught at T3 or above | 0.844 | >= 0.7 | ok |
| fraud money caught at T3 or above | 0.979 | >= 0.9 | ok |
| genuine payments stepped up to a face check or more | 0.060 | <= 0.14 | ok |
| weakest attack type (40+ cases) caught at T3+ | 0.531 | >= 0.3 | ok |
| senior step-up ratio (gate fails above 1.25) | 2.020 | <= 3.3 (does not get worse) | ok |
| new customers: median payments to ready | 50 | <= 120 | ok |
| genuine payments a learning customer would have held | 0.001 | <= 0.02 | ok |
| mule payees flagged | 0.920 | >= 0.9 | ok |
| reported payees flagged | 1.000 | >= 1.0 | ok |
| shops, utilities, friends wrongly flagged | 0.000 | <= 0.0 | ok |
| new popular merchants wrongly flagged | 0.233 | <= 0.35 | ok |
| live-path parity mismatches | 0 | <= 0 | ok |

- **Weakest attack types** (caught at T3+): repeat_scam 53% of 559, slow_drip 54% of 253, low_and_slow_mimic 60% of 135, bot 63% of 232
- **Genuine payments stepped up to a face check or more, by age**: 18-24 0.77x, 25-34 0.98x, 35-44 1.03x, 45-59 1.14x, 60-74 1.98x, 75+ 2.24x
- **New customers**: 64.5% ready within the run; median 50 payments, 90th percentile 96; false alarms 12.7% while learning, 4.5% after; learning-phase holds caught 68.1% of fraud.
- **Fraud met while a customer is still being learned** (fingerprint plan): held for a person 68% of cases / 81% of the money, a code added 20% of cases / 19% of the money, the fingerprint alone 12% of cases / 0% of the money.
- **Sign-up answers, first five genuine payments**: customers who answered 17.5% stepped up (of 3,192); the same population with answers hidden 21.7%.
- **Payee reputation**: friend 0/160 flagged, mule 23/25 flagged, new_merchant 7/30 flagged, reported 15/15 flagged, shop 0/65 flagged, slow_mule 0/10 flagged, utility 0/10 flagged. Slow mules caught 0.0% (expected low).
- **Live-path parity**: 204 payments compared, 0 differed.
