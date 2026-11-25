# The merged risk model: the platform's model and DemoPay's, as one

The platform's risk model (`amfa/engine/risk`, LightGBM, threat levels T0-T4) and DemoPay's payment-risk model
(`backend/ato` on branch `Research/face-auth`, three detectors for takeover, coached scams and bad payees) each see something
the other cannot. This merge keeps the platform's model, its interface and everything downstream of it (reliefs, band switch,
model card, release gate), and teaches it what DemoPay knows.

## What each brought

| | Platform (`main`) | DemoPay (`Research/face-auth`) | Merged |
|---|---|---|---|
| Inputs | 44: amount habit, payee history and cycles, device, network, place, account changes, failed checks, behaviour score, segment | 46: amount habit, payee intelligence, UPI channel, SIM / PIN / binding events, and **what the phone reports**: a call in progress, an unknown caller, screen sharing, a tampered phone, an overlay | **71**: the platform's 44, DemoPay's 22 phone and network signals (all optional), and 5 worked out from history and the balance |
| Training data | 26 attack types (`scenarios.py`) | 13 UPI scam types in 3 families (`ato/simulate.py`) | both |
| Model | one calibrated LightGBM ensemble | three one-vs-rest detectors, "surprise" severities, router | general LightGBM boosters + a specialist for each kind of payment (signals sent or not), averaged half and half; one calibration and one set of T0-T4 cut-offs |
| Kind of fraud | not named | router: takeover / coached / bad payee | router: UNAUTHORISED / COACHED / PAYEE_RISK / FIRST_PARTY, in `Result.family` and `Plan.threatFamily` (explains, never changes the checks) |

## How it fits the platform

- **Nothing changes for an existing caller.** The new signals are optional (`build(..., signals=None)`, and `signals` on
  `POST /v1/transactions`). Not sent means missing, which the model treats as missing, not as zero; `signals_present` tells the
  model whether a bank sends them at all, so "not sent" is never read as "all quiet".
- **Older models still load.** A model records the inputs it was trained on (`card.json` `features`) and the scorer feeds it
  exactly those, in that order; rows built in either order are accepted (`RiskModel.raw`). A model needing an input this build
  does not compute is refused, as before (fail closed).
- **Same training command.** `python -m amfa.engine.risk.train` now also learns from DemoPay's simulator (30% as many UPI users
  as platform customers by default; `--upi-users 0` for the platform's simulator only). The model folder gains
  `expert_*.txt` and `router.txt`, all in the checksum manifest.
- **DemoPay's code is ported, not rewritten.** `amfa/engine/risk/upi/{features,simulate,train}.py` are DemoPay's files with only
  their imports changed; a test checks the ported simulator reproduces DemoPay's dataset number for number.

### The new signals (`features.SIGNALS`, `api_tenant.RiskSignals`)

| Phone (from an attested app) | Payment network / bank |
|---|---|
| `callActive`, `callMinutes`, `callUnknownNumber` (never call content) | `payeeAccountAgeDays`, `payeeFanIn24h` (different payers today) |
| `remoteAccessActive`, `overlayAccessibility` | `payeeFraudRiskBand` (government FRI, 0-3), `payeeComplaints` |
| `integrityFail`, `sideloadHours` | `payeeIsMerchant`, `payeeRiskyCategory`, `paymentKind` (pay / collect / qr) |
| `bioSimilarity` (0-1), `loginToPaySeconds` | `deviceBoundHours`, `oldDeviceActive`, `failedPinAttempts`, `ipGeoKm` |
| `requesterIsContact` (collect requests) | `balanceMinor` (used for the share-of-balance inputs, not an input itself) |

Worked out by `build()` for every payment: `escalating_to_payee` (rising amounts to a payee first paid in the last 3 days),
`new_payees_72h`, `amount_over_balance`, `outflow_24h_over_balance`, `signals_present`.

## How "better" was tested

`scripts/compare_merged_model.py` trains every model on the same simulated payments and scores it on the same held-out ones:

- **platform**: the platform's model as on `main` (its 44 inputs, its simulator only)
- **demopay**: DemoPay's model as on `Research/face-auth` (its 46 inputs, three detectors, its simulator only), rebuilt with its
  own trainer
- **merged**: this model
- **platform_both_data** (ablation): the platform's 44 inputs on both simulators, to separate "more data" from "new inputs"

Each simulator is scored separately, with the platform's yardsticks: average precision, and the share of fraud payments and of
whole scam incidents caught when only 5% / 1% / 0.2% of genuine payments may be stepped up (the T2 / T3 / T4 budgets). Every
LightGBM model gets the same choices (tree size, learning rate), picked on its own validation rows.

The design was chosen on validation data only (populations 7 and 11), and then confirmed on **three fresh simulated
populations** (seeds 17, 19, 23) nobody had looked at. For every yardstick the report gives the merged model's difference from
the better of the two original models, with a 95% bootstrap interval (genuine payments and whole incidents resampled):
"better" when the whole interval is above zero, "worse" when below, otherwise "no measurable difference".

## Results

On three fresh populations, the merged model is **not measurably worse than the better of the two original models on 13 of
14 yardsticks**, measurably better on 2 (both on DemoPay's scams), and measurably worse on 1:

- **On DemoPay's UPI scams it beats DemoPay's own model**: average precision 0.905 against
  0.869, and it catches more scam payments at every level. The platform's model alone
  catches only 67.2% of them at T3; the merged model 95.8%.
- **On the platform's own attacks it matches the platform's model** (every difference within noise, the means slightly ahead), so
  nothing the platform could catch is lost.
- **The one measurable loss**: counted by whole scam incidents (at least one payment of the scam flagged), DemoPay's three
  detectors catch 1.6 points more UPI incidents at T3. Its detectors are
  trained per kind of fraud and flag an incident's first payment slightly more often; a per-family specialist is the obvious next step.
- **The gain is the new inputs, not just more data**: the platform's 44 inputs trained on both simulators reach only
  0.668 average precision on DemoPay's scams (the ablation column).

**DemoPay's UPI scams (its 13 scam types)**, mean of seeds 17, 19, 23

| Yardstick | platform | demopay | **merged** | platform, both data | merged minus the better original (95% interval) |
|---|---|---|---|---|---|
| Average precision | 0.550 | 0.869 | **0.905** | 0.668 | +0.035 [+0.014, +0.057]: **better** |
| Fraud payments caught at T2 | 82.0% | 97.7% | **99.2%** | 88.3% | +1.5 pts [+0.6, +2.5]: **better** |
| Fraud payments caught at T3 | 67.2% | 94.4% | **95.8%** | 74.9% | +1.4 pts [-0.2, +3.2]: no measurable difference |
| Fraud payments caught at T4 | 52.8% | 89.1% | **89.6%** | 65.8% | +1.0 pts [-1.5, +3.7]: no measurable difference |
| Scam incidents caught at T2 | 83.1% | 98.8% | **98.6%** | 89.9% | -0.1 pts [-1.0, +0.7]: no measurable difference |
| Scam incidents caught at T3 | 70.3% | 97.1% | **95.2%** | 78.2% | -1.6 pts [-3.4, -0.1]: **worse** |
| Scam incidents caught at T4 | 58.1% | 93.3% | **91.3%** | 70.0% | -1.4 pts [-4.1, +1.2]: no measurable difference |

**The platform's attacks (its 26 attack types)**, mean of seeds 17, 19, 23

| Yardstick | platform | demopay | **merged** | platform, both data | merged minus the better original (95% interval) |
|---|---|---|---|---|---|
| Average precision | 0.868 | 0.075 | **0.875** | 0.871 | +0.008 [-0.001, +0.019]: no measurable difference |
| Fraud payments caught at T2 | 99.8% | 50.4% | **99.8%** | 99.8% | +0.0 pts [+0.0, +0.0]: no measurable difference |
| Fraud payments caught at T3 | 95.8% | 26.1% | **95.9%** | 95.9% | +0.2 pts [-0.8, +1.5]: no measurable difference |
| Fraud payments caught at T4 | 87.5% | 5.9% | **88.7%** | 87.7% | +1.1 pts [-0.7, +3.4]: no measurable difference |
| Scam incidents caught at T2 | 100.0% | 54.1% | **100.0%** | 99.4% | +0.0 pts [+0.0, +0.0]: no measurable difference |
| Scam incidents caught at T3 | 97.2% | 27.4% | **97.8%** | 98.4% | +0.6 pts [-0.5, +1.9]: no measurable difference |
| Scam incidents caught at T4 | 87.5% | 10.3% | **86.9%** | 86.4% | -0.1 pts [-2.6, +1.9]: no measurable difference |

**Router** (which kind of fraud), share of test fraud payments named correctly, per seed: on DemoPay's scams the merged router and
DemoPay's are close for takeovers and coached scams; both are weak on bad payees, as in DemoPay's own report.

| seed | merged, platform test | merged, DemoPay test | DemoPay's router, DemoPay test |
|---|---|---|---|
| 17 | COACHED 81%, FIRST_PARTY 95%, UNAUTHORISED 95% | COACHED 99%, PAYEE_RISK 25%, UNAUTHORISED 81% | UNAUTHORISED 85%, COACHED 97%, PAYEE_RISK 21% |
| 19 | COACHED 75%, FIRST_PARTY 95%, PAYEE_RISK 67%, UNAUTHORISED 93% | COACHED 94%, PAYEE_RISK 19%, UNAUTHORISED 80% | UNAUTHORISED 83%, COACHED 95%, PAYEE_RISK 38% |
| 23 | COACHED 90%, FIRST_PARTY 100%, PAYEE_RISK 25%, UNAUTHORISED 93% | COACHED 100%, PAYEE_RISK 11%, UNAUTHORISED 80% | UNAUTHORISED 70%, COACHED 100%, PAYEE_RISK 7% |

**How the design was chosen.** A first round (seeds 7, 11, 13) with a single model trained on both simulators at equal weight
already beat DemoPay on its own scams but trailed the platform's model slightly at T4 on the platform's attacks. On validation
only, a quarter weight for DemoPay's rows, the balance-based and 3-day inputs, and the per-kind specialists closed that gap;
the confirmation above used seeds never looked at before. Full numbers: `docs/merged-risk-model-results.json`
(round 1, the earlier single-model design: `docs/merged-risk-model-results-round1.json`; re-run with
`python scripts/compare_merged_model.py`, about an hour on 4 cores). The comparison uses LightGBM's "intermediate" monotone constraints for every model (the shipped trainer keeps the
platform's "advanced"), which were ~16x faster here with similar validation scores.


## What this does not show

All data is simulated, by two simulators written by the two teams; both models partly confirm their own authors' assumptions.
The numbers compare methods on the same data; they are not real-world accuracy. Phone signals can be forged by a compromised
phone, so they should come from an attested app (the platform's device attestation). The router is weak on bad-payee cases in
every model tested. Before trusting any of it: shadow mode on real traffic, as the platform's band switch already supports.

## Files

| File | What |
|---|---|
| `amfa/engine/risk/features.py` | `SIGNALS`, `DERIVED_FEATURES`, `build(..., signals)`, `vector(..., names)` |
| `amfa/engine/risk/scorer.py` | per-model input list, specialists, router, `Result.family` |
| `amfa/engine/risk/train.py` | both simulators, specialists, router, `--upi-users` |
| `amfa/engine/risk/merged_data.py` | one dataset from both simulators, one time split |
| `amfa/engine/risk/upi/` | DemoPay's simulator, features and trainer (ported), and `convert.py` |
| `amfa/core/api_tenant.py`, `context_builder.py` | `signals` on `POST /v1/transactions` |
| `amfa/engine/context.py`, `decide.py` | `Plan.threatFamily` |
| `scripts/compare_merged_model.py` | the comparison; results in `docs/merged-risk-model-results.json` |
| `tests/engine/test_merged_risk.py`, `tests/core/test_risk_live_path.py` | the new behaviour |
