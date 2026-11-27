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
| Model | one calibrated LightGBM ensemble | three one-vs-rest detectors, "surprise" severities, router | general LightGBM boosters + a specialist for each kind of payment (signals sent or not), averaged half and half; one calibration and one set of T0-T4 cut-offs. One-way rules on the platform's inputs as before, and on DemoPay's signals except payee account age and payee fan-in (see "Whole scams caught") |
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
- **merged_before** (ablation, added with the fix below): the merged model with the two one-way rules the first design had,
  on the same data, to show what the fix alone does

Each simulator is scored separately, with the platform's yardsticks: average precision, and the share of fraud payments and of
whole scam incidents caught when only 5% / 1% / 0.2% of genuine payments may be stepped up (the T2 / T3 / T4 budgets). Every
LightGBM model gets the same choices (tree size, learning rate), picked on its own validation rows.

The design was chosen on validation data only (populations 7 and 11), and then confirmed on **three fresh simulated
populations** (seeds 17, 19, 23) nobody had looked at. For every yardstick the report gives the merged model's difference from
the better of the two original models, with a 95% bootstrap interval (genuine payments and whole incidents resampled):
"better" when the whole interval is above zero, "worse" when below, otherwise "no measurable difference".

## Results of the first run (the design as first committed, 644493c)

These are the numbers of the first design, kept as they were reported. That design lost to DemoPay on whole scams caught;
the section after this one finds out why, fixes it, and gives the current model's numbers on three further fresh populations.

On three fresh populations, the merged model is **not measurably worse than the better of the two original models on 13 of
14 yardsticks**, measurably better on 2 (both on DemoPay's scams), and measurably worse on 1:

- **On DemoPay's UPI scams it beats DemoPay's own model**: average precision 0.905 against
  0.869, and it catches more scam payments at every level. The platform's model alone
  catches only 67.2% of them at T3; the merged model 95.8%.
- **On the platform's own attacks it matches the platform's model** (every difference within noise, the means slightly ahead), so
  nothing the platform could catch is lost.
- **The one measurable loss**: counted by whole scam incidents (at least one payment of the scam flagged), DemoPay's three
  detectors catch 1.6 points more UPI incidents at T3. The guess made here at the time, that DemoPay wins because its detectors
  are trained per kind of fraud and that a per-family specialist was the next step, **was tested and is wrong**: see the next section.
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
(round 1, the earlier single-model design: `docs/merged-risk-model-results-round1.json`). Both files were written by the
first design; `python scripts/compare_merged_model.py` (about an hour on 4 cores, less on a quiet machine) now trains the
current model and reports the first design in its `merged_before` column. The comparison uses LightGBM's "intermediate" monotone constraints for every model (the shipped trainer keeps the
platform's "advanced"), which were ~16x faster here with similar validation scores.


## Whole scams caught: what the first design missed, and the fix

The first design caught fewer whole UPI scams than DemoPay's own model (above: 95.2% against 97.1% at T3). A later re-run of the
same design on seeds 29, 31, 37 came out slightly ahead instead (97.2% against 95.6%), so three populations of about 100 UPI
scams each cannot settle it. The misses were therefore studied on all nine populations already seen (seeds 7, 11, 13, 17, 19,
23, 29, 31, 37: 976 UPI scams and 582 platform incidents in the test days), none of which is used for the confirmation below.

### Which scams were missed, and why

| First design, nine populations pooled, T3 | merged (first design) | DemoPay |
|---|---|---|
| UPI scams caught at T2 / T3 / T4 | 99.3% / 95.5% / 91.1% | 98.9% / 96.1% / 91.1% |
| Scams with **one** payment (279) caught at T3 | **86.0%** | 89.6% |
| Scams with several payments (697) caught at T3 | 99.3% | 98.7% |
| First payment of the scam flagged at T3 | 89.8% | 92.0% |

- **Almost every miss is a scam with a single payment**: 39 of the 44 scams missed at T3 (the other 5 have two payments). A
  scam with several payments is caught 99% of the time by either model, so the incident yardsticks are decided by the
  one-payment scams, where there is one chance.
- **Which types**: fake seller (16 of 86 missed at T3), look-alike payee (9 of 78), collect / QR request (6 of 104), prize
  fee (6 of 53), relative in trouble (4 of 37). Takeovers and the long coached scams (digital arrest, task and investment,
  romance) are essentially always caught.
- **How far below the line**: not far. The missed scam's payment scored above 95-99% of genuine payments (median: only 2.4% of
  genuine payments scored higher; the T3 line is 1%). They were T2 payments, not invisible ones.
- **What they have in common**: a first payment to a new payee whose account is a **rented, aged account** (a few hundred
  days old), with no complaint and no fraud-risk band yet, an ordinary amount, and no call.

The cause was not the model's shape. DemoPay's three detectors are not better because they are split by kind of fraud: adding
its takeover or its coached detector to the merged score made whole-scam recall *worse* (T4, seeds 7, 11, 13: 91.0% to 85.2%
and 88.1%), while its plain all-fraud detector helped. And a detector with DemoPay's training settings on the merged model's
own inputs beat the merged model. The difference was in one training setting: **the one-way rules**.

The platform trains with one-way ("monotone") rules: a value may only raise the risk, or only lower it. The merge had carried
DemoPay's nominal directions over for its signals, two of which are wrong for the fraud in this data, because the truth is a
hump and not a slope (share of weighted training payments that are fraud, seed 7):

| Receiving account's age (days) | 0-12 | 12-30 | 30-100 | 100-300 | 300-900 | 900-1500 | 1500+ |
|---|---|---|---|---|---|---|---|
| fraud share | 13.2% | 0.44% | 0.22% | **0.55%** | **0.46%** | 0.01% | 0.00% |

| Other people who paid this payee today | 0-2 | 3-5 | 6-9 | 10-19 | 20-39 | 40-99 | 100+ |
|---|---|---|---|---|---|---|---|
| fraud share | 0.14% | 0.14% | **4.60%** | **1.63%** | **2.81%** | 0.00% | 0.00% |

"An older account is safer" let a rented 500-day-old account hide behind every long-standing payee, and "more payers is
riskier" is false because busy merchants are paid by hundreds. With the rules on, the model could not score an aged mule above
an ordinary new payee, whatever else it saw. DemoPay's own trainer searches three configurations, one of them without any
one-way rule, and that is the one it usually picks, so in practice its detectors were never held to these two rules.

### The fix

`features.MONOTONE` no longer constrains `payee_account_age_days` and `payee_fan_in_24h` (they are listed, with the reason, in
`features.UNCONSTRAINED_SIGNALS`). Nothing else changed: the same 71 inputs, the same general boosters and specialists, the
same calibration and cut-offs, every one-way rule on the platform's own 44 inputs, and DemoPay's other 13. The shipped trainer
and the comparison both read the rules from `features.MONOTONE`, so the comparison measures what the trainer builds (a test
checks this). A caller that sends no signals has neither input, so nothing changes for it beyond an ordinary retrain; saved
models are untouched.

Chosen on validation days and the development populations only: taking each rule out in turn, only these two and the
binding-age rule moved UPI validation average precision by more than 0.01 (seeds 7, 11, 13); taking out these two gives what
taking out all of DemoPay's rules gives. In the full design, validation days of seeds 7, 11, 13: UPI scams caught at T3 / T4
95.6% / 86.3% before, 98.7% / 92.1% after (DemoPay 95.7% / 87.3%).

### Confirmation on three populations never used before (seeds 41, 43, 47)

Run once, with the final design, the unchanged protocol and default sizes
(`python scripts/compare_merged_model.py --seeds 41,43,47`; `docs/merged-risk-model-results-seeds-41-43-47.json`). "Merged
before" is the first design trained on the same data (the `merged_before` column: the two old rules back on, all else equal).
The interval compares the current merged model with the better of the two original models, as in the first run.

**DemoPay's UPI scams**, mean of seeds 41, 43, 47

| Yardstick | platform | demopay | merged before | **merged** | merged minus the better original (95% interval) |
|---|---|---|---|---|---|
| Average precision | 0.545 | 0.852 | 0.884 | **0.934** | +0.083 [+0.059, +0.113]: **better** |
| Fraud payments caught at T2 | 81.9% | 97.7% | 99.8% | **100.0%** | +2.3 pts [+1.3, +3.4]: **better** |
| Fraud payments caught at T3 | 66.7% | 92.2% | 95.3% | **97.9%** | +5.6 pts [+3.5, +8.2]: **better** |
| Fraud payments caught at T4 | 53.1% | 84.3% | 88.0% | **93.3%** | +9.2 pts [+5.8, +12.7]: **better** |
| Scam incidents caught at T2 | 78.8% | 98.4% | 99.6% | **100.0%** | +1.6 pts [+0.3, +2.9]: **better** |
| Scam incidents caught at T3 | 68.2% | 95.5% | 95.5% | **97.5%** | +2.1 pts [+0.3, +4.2]: **better** |
| Scam incidents caught at T4 | 54.5% | 89.5% | 90.0% | **93.5%** | +4.2 pts [+0.7, +7.8]: **better** |

**The platform's attacks**, mean of seeds 41, 43, 47

| Yardstick | platform | demopay | merged before | **merged** | merged minus the better original (95% interval) |
|---|---|---|---|---|---|
| Average precision | 0.848 | 0.122 | 0.847 | **0.847** | -0.001 [-0.005, +0.004]: no measurable difference |
| Fraud payments caught at T2 | 99.2% | 42.3% | 99.6% | **99.6%** | +0.5 pts [+0.0, +1.1]: no measurable difference |
| Fraud payments caught at T3 | 93.1% | 28.5% | 92.7% | **93.1%** | -0.3 pts [-1.8, +1.1]: no measurable difference |
| Fraud payments caught at T4 | 83.5% | 19.6% | 83.4% | **83.2%** | -0.4 pts [-1.4, +0.8]: no measurable difference |
| Scam incidents caught at T2 | 98.9% | 40.8% | 99.6% | **99.6%** | +0.7 pts [+0.0, +1.9]: no measurable difference |
| Scam incidents caught at T3 | 92.8% | 25.5% | 94.5% | **95.0%** | +1.2 pts [-1.2, +3.9]: no measurable difference |
| Scam incidents caught at T4 | 83.1% | 19.4% | 82.4% | **82.0%** | -1.2 pts [-3.6, +0.0]: no measurable difference |

Read plainly:

- **On DemoPay's UPI scams all seven yardsticks are now measurably better than DemoPay's own model**, the three whole-scam ones
  included (+1.6, +2.1 and +4.2 points), in each of the three populations. Against the first design: +2.0 points of whole
  scams at T3 and +3.5 at T4, +2.6 and +5.3 points of payments, +0.05 average precision.
- **On the platform's attacks nothing moved measurably, in either direction, and whole incidents at T4 are not better.** The
  means that are lower than the platform's own model: average precision (0.8468 against 0.8477), payments at T4 (83.2% against
  83.5%), incidents at T4 (82.0% against 83.1%, with an interval that only just reaches zero), and payments at T3 by the
  bootstrap mean (the plain means are equal). Against the first design on the same data: incidents at T3 +0.5, at T4 -0.4
  (one incident in one population each way), payments at T3 +0.4, at T4 -0.2, average precision -0.0006. Three populations
  hold about 200 platform incidents in all, so one incident is half a point.
- The router is not affected by this fix (it has no one-way rules) and is still weak on bad payees in every model. Share of
  test fraud payments named correctly:

| seed | merged, platform test | merged, DemoPay test | DemoPay's router, DemoPay test |
|---|---|---|---|
| 41 | COACHED 95%, FIRST_PARTY 93%, PAYEE_RISK 50%, UNAUTHORISED 95% | COACHED 99%, PAYEE_RISK 38%, UNAUTHORISED 87% | UNAUTHORISED 92%, COACHED 98%, PAYEE_RISK 33% |
| 43 | COACHED 92%, FIRST_PARTY 94%, PAYEE_RISK 0%, UNAUTHORISED 92% | COACHED 87%, PAYEE_RISK 47%, UNAUTHORISED 93% | UNAUTHORISED 92%, COACHED 96%, PAYEE_RISK 37% |
| 47 | COACHED 88%, FIRST_PARTY 81%, PAYEE_RISK 0%, UNAUTHORISED 96% | COACHED 95%, PAYEE_RISK 17%, UNAUTHORISED 90% | UNAUTHORISED 93%, COACHED 97%, PAYEE_RISK 33% |

On the nine development populations the fix took UPI scams caught from 95.5% to 98.5% at T3 and from 91.1% to 94.8% at T4
(DemoPay 96.1% and 91.1%), and one-payment scams at T3 from 86.0% to 95.0%.

### What is left, and why

- **UPI**: the scams still missed at T4 are again single payments: an aged account with no complaint and no risk band, an
  amount inside the customer's habit, no call, no screen sharing. Nothing in the 71 inputs separates such a payment from a
  genuine first payment to a new payee, so the remaining 5-7% at T4 is close to what these inputs can give.
- **The platform's attacks at T4**: the misses are scams repeated to the same scammer (47 of 92 missed at T4 on the
  development populations; the platform's own model misses 40) and a jump in a recurring payment (15 of 17, both models).
  Taking *all* one-way rules off, the platform's own included, did lift them (seeds 7, 11, 13, test days, in an
  experimental variant: incidents at T3 93.0% to 97.0%, at T4 86.5% to 88.5%, average precision 0.843 to 0.862). That was **not** done: those rules are the
  platform's stated defence against a fraudster steering a value to lower the score (and what lets `helper_device` only ever
  lower risk), the platform's own model keeps them, and none of them is contradicted by the data the way the two above are.
  Whether to relax any of them is a product decision, not a modelling one.

### Tried and not kept

| Tried (seeds 7, 11, 13) | Result | Why not kept |
|---|---|---|
| Counting every scam once in training, however many payments it has | UPI scams at T4 89.7% to 88.5%, average precision 0.874 to 0.858; platform average precision 0.839 to 0.827 | worse |
| Other ways to combine the general boosters and the specialists (maximum, geometric mean, by rank, three quarters specialist) | within a point of the half-and-half average, both ways | not the cause |
| Per-family detectors (the first report's guess) | DemoPay's takeover or coached detector added to the merged score: whole UPI scams at T4 down 3 to 6 points; its all-fraud detector added: up 2 | the split by family is not what helps |
| DemoPay's look-alike payee score as a new signal | no measurable change | no gain to justify a new input |
| Amount against the customer's own 95th percentile; how usual the hour is | within noise | no gain |
| DemoPay's "never paid this payee before" flag | UPI average precision +0.016 | a shortcut, not a signal: next to `payee_seen` it reveals that an earlier payment of the same scam was recorded as unverified (see "What this does not show") |
| Also dropping the rule on `device_bound_hours` | UPI scams at T4 +1.4 points on validation | rests on a quirk of the simulator (a genuine phone change stops reporting the binding age after 72 hours, a takeover does not) |
| Dropping every one-way rule | nothing more on UPI; better on the platform's attacks (above) | the platform's own rules are not this change's to drop |

## What this does not show

All data is simulated, by two simulators written by the two teams; both models partly confirm their own authors' assumptions.
The numbers compare methods on the same data; they are not real-world accuracy. Phone signals can be forged by a compromised
phone, so they should come from an attested app (the platform's device attestation). The router is weak on bad-payee cases in
every model tested. Before trusting any of it: shadow mode on real traffic, as the platform's band switch already supports.

Three more things the work on whole scams turned up:

- **Later payments of a scam are easier in simulation than in life, for the platform's and the merged model.** Both simulators'
  histories record a payment that is part of a fraud as "not verified" (`upi/convert.to_platform`, and the platform's own
  `scenarios.py`), and the platform's inputs can tell (a payee paid a minute ago that still counts as never paid). DemoPay's
  inputs cannot. This predates the fix and is unchanged by it; it flatters the merged and platform models on the payments
  after the first of an incident. The gain reported above sits on the part free of it: scams with a single payment, and first
  payments (flagged at T3 on the development populations: 89.8% before, 96.1% now, DemoPay 92.0%). How much the payment-level
  numbers owe to it was not measured.
- **Two inputs no longer have a guaranteed direction.** A one-way rule also stops a fraudster lowering the score by pushing a
  value further the "risky" way. For payee account age and payee fan-in the old rules pointed the wrong way for the fraud in
  this data, so they protected nothing here, but the model now gives no guarantee at all on these two. Both are facts about
  the receiving account reported by the payment network, not by the payer's phone.
- **The humps come from DemoPay's simulator** (rented mule accounts 100-900 days old; mules paid by 3-40 people, busy merchants
  by up to 200). Whether real mule accounts look like that is exactly what shadow mode has to show.

## Files

| File | What |
|---|---|
| `amfa/engine/risk/features.py` | `SIGNALS`, `DERIVED_FEATURES`, `MONOTONE` (the one-way rules), `UNCONSTRAINED_SIGNALS`, `build(..., signals)`, `vector(..., names)` |
| `amfa/engine/risk/scorer.py` | per-model input list, specialists, router, `Result.family` |
| `amfa/engine/risk/train.py` | both simulators, specialists, router, `--upi-users` |
| `amfa/engine/risk/merged_data.py` | one dataset from both simulators, one time split |
| `amfa/engine/risk/upi/` | DemoPay's simulator, features and trainer (ported), and `convert.py` |
| `amfa/core/api_tenant.py`, `context_builder.py` | `signals` on `POST /v1/transactions` |
| `amfa/engine/context.py`, `decide.py` | `Plan.threatFamily` |
| `scripts/compare_merged_model.py` | the comparison; results in `docs/merged-risk-model-results.json` (first design, seeds 17, 19, 23) and `docs/merged-risk-model-results-seeds-41-43-47.json` (current model) |
| `tests/engine/test_merged_risk.py`, `tests/core/test_risk_live_path.py` | the new behaviour |
