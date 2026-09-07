# UEBA Risk Service

Behavioural risk scoring for adaptive multi-factor authentication.

Given a transaction, this service answers one question: **does this
behaviour match the user's established pattern?** It returns a calibrated
risk probability and takes no position on what should happen next — no
verdict, no factor count. Threshold and policy decisions belong to the
orchestration layer.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m scripts.train      # generate, train, calibrate, evaluate
python -m scripts.plot       # reliability diagram + PR curve
python -m pytest tests/ -q   # 18 tests

uvicorn api.service:app --reload
```

Then:

```bash
curl -X POST localhost:8000/risk/assess \
  -H 'Content-Type: application/json' \
  -d '{"userId":"u_1042","amount":50000,"deviceId":"d_88",
       "ip":"103.21.45.67","timestamp":"2026-03-14T03:15:00Z",
       "beneficiaryId":"b_9","action":"TRANSFER","channel":"MOBILE"}'
```

---

## Results

On synthetic data: 200 users, 90 days, ~14,000 transactions, 1.1% fraud.
Split by time at 70%, so no future information reaches any row.

| Model | PR-AUC | ROC-AUC |
|---|---|---|
| Isolation Forest (unsupervised) | **0.67** | 0.96 |
| Logistic Regression (supervised) | 0.57 | 0.63 |

At a threshold targeting 80% recall: precision 0.22, recall 0.81.

### Per-pattern detection

This breakdown is more informative than the aggregate.

| Fraud pattern | Recall | Features it fires |
|---|---|---|
| account_takeover | 1.00 | device, location, beneficiary, hour, amount |
| dormant_reactivation | 1.00 | dormancy, amount, beneficiary |
| velocity_burst | 0.86 | velocity |
| amount_spike | 0.36 | amount only |

Fraud that fires several features at once is caught reliably. Fraud that
moves a single feature is largely missed. That is the honest shape of the
result, and it is why the per-pattern table belongs in the report rather
than a single number.

---

## Findings

**The supervised baseline underperforms the unsupervised model.** This is
not a bug. `days_since_last_txn` is bimodal for fraud — velocity bursts
have near-zero gaps, dormant reactivations have sixty-day gaps — so a
linear model fits a coefficient that is wrong for both. Isolation Forest,
partitioning rather than fitting a boundary, handles the non-monotonicity.

**The model is over-confident.** The reliability diagram sits below the
diagonal: events predicted at 0.95 show an observed fraud rate near 0.60.
Platt scaling on a small labelled set with an extreme class ratio does not
fully correct this. Reported rather than hidden; isotonic recalibration
above ~1,000 labelled points is the stated next step.

**Precision at 80% recall is 0.22.** Roughly four false alarms per catch.
For a step-up authentication signal this may be acceptable — a false
positive means an extra challenge, not a blocked transaction — but it
would be poor for an automatic blocking system. This is one reason UEBA
belongs upstream of the decision rather than inside it.

**Legitimate anomalies matter.** An earlier version of the generator gave
new devices and new cities only to fraud, which made those two features
perfectly collinear and inflated results. Adding legitimate travel (4% of
transactions), device changes (1% of days) and new payees (6%) dropped
PR-AUC from 0.85 to 0.67. The lower number is the trustworthy one.

---

## Design decisions

**Per-user baselines, never population.** ₹50,000 is routine for one user
and an 8-sigma event for another. Every continuous feature is computed
against the individual's own history. This is why the generator is
agent-based: if all users shared one distribution, per-user features would
collapse into population features.

**Continuous scores, not hard labels.** In scikit-learn, `contamination`
only sets the threshold `predict()` uses; it does not affect training or
the ranking from `score_samples()`. The service consumes the ranking and
thresholds downstream.

**Calibration is separate from scoring.** Raw anomaly scores are ordered
but are not probabilities. Platt scaling maps them using labelled
examples; the reliability diagram is the evidence.

**Time-based split.** A random split would let the model see a user's
future when scoring their past, measuring leakage rather than detection.

**No free text in requests.** Beneficiary names and remarks are
attacker-controlled, are needed by no feature, and would open an injection
path into the decision layer. Excluded by construction, not filtered.

**No raw IP storage.** A salted hash supports "same network as before" and
a derived city supports "new location". The address is discarded.

**Insufficient data is not low risk.** Below the minimum history threshold
the service returns `INSUFFICIENT_DATA` with a HIGH band. Absence of
evidence never reads as evidence of safety. Cold start is answered before
the model is consulted, so a missing model cannot turn "new user" into an
error.

---

## Layout

```
data/synth.py           Agent-based transaction generator
features/engine.py      Seven per-user deviation features
models/base.py          Model contract: train(ctx) / infer(ctx)
models/loader.py        Manifest-driven loading with feature validation
models/isoforest/       Isolation Forest (primary)
models/logreg/          Logistic Regression (baseline)
models/calibrate.py     Platt scaling, reliability curve
api/service.py          FastAPI
store/schema.sql        Tables, indexes, INSERT-only grants
eval/metrics.py         PR-AUC, per-pattern, ablation
scripts/train.py        Full pipeline
scripts/plot.py         Evaluation figures
tests/                  18 tests
```

## Features

| Feature | Signal |
|---|---|
| `amount_zscore` | Unusually large for this user |
| `txn_velocity_1h` | Rapid-fire activity |
| `is_new_device` | Possible takeover |
| `is_new_location` | Geographic anomaly |
| `hour_unusual` | Outside their normal window (circular encoding) |
| `is_new_beneficiary` | First transfer to this payee |
| `days_since_last_txn` | Dormant reactivation |

Nothing identifying reaches the model — no user id, no city name, no rupee
amount. Only deviations.

## Adding a model

```
models/<name>/
├── MODEL.py        exactly one Model subclass
└── model.yaml      name, version, expected_features
```

`expected_features` is enforced at load time, so a stale model fails
loudly rather than scoring against shifted columns.

---

## Limitations

- **Synthetic data.** Real fraud is more varied and more adversarial than
  four generated patterns. Results indicate the pipeline works, not that
  it would perform this way in production.
- **Calibration is approximate.** Small labelled set, extreme imbalance.
- **In-memory history in the service.** `store/schema.sql` defines the
  PostgreSQL replacement; wiring it is outstanding.
- **GeoIP is stubbed.** `derive_city()` returns UNKNOWN.
- **PaySim validation not yet run.** Five of seven features are computable
  there; device and location have no equivalent.

## Compliance

Payment data is stored only in India (RBI/2017-18/153, 6 April 2018). All
processing is local; nothing is sent to external services.

Behavioural data is personal data under the DPDP Act 2023, handled under
data minimisation and purpose limitation — fraud risk assessment for
authentication, nothing else.

RBI's Authentication Directions 2025 (Para 8) permit evaluating
transactions against behavioural and contextual parameters and applying
checks beyond the two-factor minimum. That minimum is a floor: this
service can raise the authentication requirement but never remove it.

*Verify all regulatory citations against primary sources before relying on
them.*

## Attribution

Derived from [prajwalnayaka/UEBA](https://github.com/prajwalnayaka/UEBA)
(MIT). Architectural patterns informed by
[GACWR/OpenUBA](https://github.com/GACWR/OpenUBA) (Apache 2.0), no code
copied. See `NOTICE`.

Licensed MIT.
