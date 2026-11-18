# Adaptive MFA

Risk-based multi-factor authentication for bank transactions. For each payment the
decision engine (rule table + local AI model, add-only) chooses which checks the customer
must pass — face, voice, typing behaviour and/or an authenticator code — runs them on the
customer's own camera, microphone and keyboard, and returns a single-use signed proof bound
to the amount and payee.

## Run on a server (Docker)

Requirements: Linux, Docker, 2+ vCPU, 8 GB RAM, port 443 open.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements/base.txt
.venv/bin/python scripts/fetch_models.py
bash deploy/vm-deploy.sh            # or: bash deploy/vm-deploy.sh your.domain.example
```

The script generates secrets into `deploy/.env`, builds and starts all services behind
Caddy (HTTPS via Let's Encrypt), creates the platform admin and a demo bank, and prints:

- Demo bank: `https://bank.<domain>`
- Admin console: `https://<domain>/admin` (user `admin@demo.local`, password `ADMIN_PASSWORD` in `deploy/.env`)

## Run locally (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
.venv/bin/python scripts/fetch_models.py
.venv/bin/python -m amfa.factors.ueba.training.train
.venv/bin/python -m amfa.engine.risk.train          # risk model (macOS: brew install libomp first)

.venv/bin/python -m amfa.cli serve face &
.venv/bin/python -m amfa.cli serve voice &
.venv/bin/python -m amfa.cli serve ueba &
AMFA_AGENT_BACKEND=off AMFA_PUBLIC_URL=http://127.0.0.1:8040 .venv/bin/python -m amfa.cli serve core --port 8040 &

AMFA_BOOTSTRAP_PASSWORD='choose-a-long-password' .venv/bin/python -m amfa.cli init --admin-email admin@demo.local
.venv/bin/python -m amfa.cli tenant create --name "Demo Bank" --slug demo-bank
.venv/bin/python -m amfa.cli apikey create --tenant <tn_id> --name demo-bank
```

Admin console: http://127.0.0.1:8040/admin

Customer app (the Demo Bank): add `http://127.0.0.1:8050` to the tenant's `allowedEmbedOrigins` in the
admin console's policy editor, then

```bash
AMFA_BASE_URL=http://127.0.0.1:8040 AMFA_TENANT_ID=<tn_id> AMFA_TENANT_SLUG=demo-bank \
AMFA_KEY_ID=<ak_id> AMFA_KEY_SECRET=<secret> BANK_SESSION_SECRET=$(openssl rand -hex 32) \
BANK_STATE_FILE=data/demo-bank-state.json .venv/bin/uvicorn examples.demo_bank.bank:app --port 8050
```

and open http://127.0.0.1:8050 on a laptop or phone. Development OTP codes are printed in the core's log.

**macOS notes.** Use Python 3.12 (`brew install python@3.12`, then `python3.12 -m venv .venv`). If pip
rejects the `+cpu` torch pins, install plain `torch` and an unpinned `torchaudio` from PyPI; if
`vosk==0.3.45` has no wheel, use `vosk==0.3.44`; `llama_cpp` is only needed for `AMFA_AGENT_BACKEND=local`.
The risk model needs `brew install libomp`.

**Testing on a phone over the internet.** Open two tunnels, one for AMFA and one for the bank
(`--protocol http2` avoids QUIC, which some networks block):

```bash
cloudflared tunnel --protocol http2 --url http://127.0.0.1:8040   # -> https://<amfa>.trycloudflare.com
cloudflared tunnel --protocol http2 --url http://127.0.0.1:8050   # -> https://<bank>.trycloudflare.com
```

Then use the public addresses everywhere instead of `127.0.0.1`:

- start the core with `AMFA_PUBLIC_URL=https://<amfa>.trycloudflare.com`;
- start the bank with `AMFA_BASE_URL=https://<amfa>.trycloudflare.com`. The bank's `frame-src` comes from
  this value, so with `127.0.0.1` the phone shows "This content is blocked";
- add `https://<bank>.trycloudflare.com` to the tenant's `allowedEmbedOrigins` (the core caches this list
  for up to 60 seconds).

Quick-tunnel addresses change every time cloudflared restarts, so repeat these three steps after a restart.

## Customer app

`examples/demo_bank` is a phone-first banking app in which every action that moves money or changes the
account is approved by AMFA before the bank carries it out:

| In the app | AMFA type | The proof is bound to |
|---|---|---|
| Send money, pay a bill | `TRANSFER`, `PAYMENT` | the amount and payee or biller |
| Add a payee (usable only once approved) | `ADD_BENEFICIARY` | the new payee |
| Change the daily limit | `CHANGE_LIMIT` | the new limit |
| Change mobile, email or password | `PROFILE_CHANGE` | the change; later payments report it as `accountEvents` |
| Use a device not yet verified | `DEVICE_BINDING` | the device; other actions are refused from it until then |
| Choose "no extra checks up to ₹X a day" (optional) | `CHANGE_LIMIT` | the amount; see [docs/customer-limit.md](docs/customer-limit.md) |
| Let a family member's phone help with payments (optional) | `CHANGE_LIMIT`, payee `HELPER` | the approval, used once; see [docs/family-helper.md](docs/family-helper.md) |

The bank acts only after it verifies the proof's signature and binding and consumes it, so an approval
can never be replayed or applied to a different amount.

**The journey.** Every action has the same shape: review (what, to whom, which checks are coming and why,
and a scam warning when AMFA asks for one) → "Confirm it's you" (AMFA's own pages, full screen, one step at
a time) → result (sent, held for review with a live timeline, or not sent with what to do next). The reason
comes from `plan.customerNotice`: never the score or threat level, and never a spoof or risk-list signal.
A customer who closes the checks midway can continue from Activity (`POST /api/actions/{id}/continue`).

**Progressive setup.** Opening an account asks only for the face (SMS codes come from the mobile number),
so it takes about a minute. Voice, typing and an authenticator app are added later from Security, one at a
time (`POST /api/amfa/enrol {"factors": ["VOICE"]}`). With two ways the bank's three-check floor for limit,
contact and device changes and high-value transfers can't be met, so those go to manual review; the app says
so and offers to add a way.

**One app.** AMFA's setup and verification pages take the bank's name and colour from the tenant policy
(`branding.color`), drop their own header when opened with `embed=1`, and post `amfa:step` progress to the
bank app, which shows it in its own header. Capture stays on AMFA's origin, so the bank never handles a
frame or recording.

**Safety and labels.** "This wasn't me" calls `POST /v1/transactions/{id}/feedback`, which stores a `FRAUD`
label for retraining and raises a security event; review verdicts become labels too (`risk_label`).
"Freeze my account" suspends the customer in AMFA and blocks every action until the bank unfreezes it.

**Demo mode** (`BANK_DEMO_TOOLS=1`, the default; set `0` for a real deployment). A "Behind the scenes" button
opens AMFA's decision on the last action (threat level, fraud probability, the model's reasons, the checks
asked against the old rule table) and stages a SIM swap, a payee on a mule list, a new phone, or an unfreeze.

**Laptop and phone.** Typing and the shape trace are enrolled once per kind of device: keyboard and
mouse on a computer, touch on a phone. A phone is scored only against touch templates and a computer only
against keyboard ones; if the device in hand has none yet, behaviour is not offered and Security shows
"Add this phone" or "Add this computer". Touch scores use provisional calibration borrowed from the
keyboard until a team test is fitted (see `INTEGRATION.md`, "Behaviour on phones").

**More than one device.** A customer's first device gets its signing key at enrolment. A second trusted
device (for example a phone after a laptop) gets its own key automatically once it passes a verification
that included face or voice (claimed within 10 minutes of it, at most 5 devices); each addition raises a
`DEVICE_KEY_ADDED` security event. Enrolment links still can't replace an existing key without a
replacement link (`INTEGRATION.md`, "Device keys on more than one device").

Layout: `static/js/app.js` (router, guards), `ui.js` (components), `amfa-frame.js` (the full-screen host for
AMFA's pages), `actions.js`, and one module per screen in `static/js/screens/`. No build step.

## Risk model

Every transaction's threat level (T0–T4) comes from the risk model in `amfa/engine/risk`, which
picks the factors: T0 OTP, T1 + behaviour, T2 + face, T3 + voice, T4 manual review. The bank's
policy floor still applies on top. With no active model, every transaction fails closed to manual
review.

**Memory.** The model looks at each customer's last 90 days, never longer: the bank may not keep more, and
everything older is deleted (see "Data kept" below). Besides usual amounts, devices and places, it checks whether
the payee has been paid before, how long ago, at about what amount and on what schedule (weekly to two-monthly), so
rent, premiums and bills that repeat inside the window are recognised as familiar. A payment that repeats less
often than every 90 days looks like a first payment.

**Each customer against their own habit.** A `habit_surprise` input says how far above this customer's own usual a
payment is (for the same payee, against what they pay that payee), so a weekly ₹1 lakh to a wholesaler is routine
and ₹10,000 from someone who pays ₹1,500 is a jump. Habit alone also sets a minimum level, from percentiles of
genuine payments stored in the model card. A routine payment (same payee at least three times over at least 30
days, about the usual amount, a known phone, no warning sign) is kept light even at a high amount.
See [docs/customer-limit.md](docs/customer-limit.md).

**Small payments.** A payment is held at T1 (OTP plus the passive behaviour check, still two factors)
instead of the model's higher level when all of these hold: the money sent in the last 24 hours is in the
bank's LOW amount band (under Rs 10,000 by default), the payment is under 5x the customer's usual, and
no hard warning sign is present (spoof attempt, repeated failed checks, payee on a risk list, SIM
change within 48 h, password reset or contact change within 24 h, an account setting changed today, or
3+ new payees today). Account changes are never treated as small. The plan records both levels
(`modelLevel`, `threatLevel`) and the reason (`capReason`). Switch it off per bank with
`smallPaymentCap: false` in the tenant policy.

`python -m amfa.engine.risk.train` trains on simulated customers and attacks (`scenarios.py`),
writes `models/risk/<version>/` (model, calibration, `card.json` with metrics, checksum manifest)
and activates it only if it beats a logistic-regression baseline and passes the per-segment
fairness check (`--force` overrides). The rule table is still computed and logged as
`rulesBaseline` for comparison but no longer decides.

`python -m amfa.engine.risk.export` writes the simulated training data as `data/risk-dataset/dataset.csv`
plus `coverage.md`: 72 scenarios (14 customer types, 47 genuine situations, 25 attacks), 6 customer age
groups (18-24 to 75+), 7 payment-size ranges (under Rs 100 to over Rs 1 crore)
and 5 data feeds, with row counts for each and an age x amount grid. Age and amount are columns in the
dataset for coverage and fairness checks; the model is never given age. Add a scenario in
`scenarios.py` and its description in `SCENARIO_DOCS`; the tests fail if any documented scenario has no rows. Banks may send optional `accountEvents`
(SIM, contact or password changes; payee risk flag) with each transaction.

## New customers: learn first, then decide

A new customer has no bank history, so the model does not decide for them at first. Until a test on their own
payments says the model knows them (`amfa/engine/risk/readiness.py`: proven, known, spread over several days, not a
fixed number of days) every payment is confirmed with the phone's own fingerprint or screen lock (a key in the secure
hardware signs AMFA's challenge), plus a one-time code above the learning limits or on a hard warning sign. Limits
and hard signals guard the learning phase, not heavier checks. Two optional sign-up questions (typical payment and a
month's total, as bands) give the model a starting point; payees reported by other customers or paid by many customers
at once are flagged for everyone. Details, measurements and status: [docs/new-customer-learning.md](docs/new-customer-learning.md)
and [docs/cold-start-implementation.md](docs/cold-start-implementation.md). Turn it on per bank with `learning`,
`payeeReputation` and `DEVICE` in `permittedFactors` (`python scripts/seed_demo.py --learning` does it for the demo).

## When the bank gives no data to train on: the band switch

The risk model was trained on simulated customers. A bank that shares no data of its own can turn on `collection`.
- **What is recorded:** every settled payment becomes one anonymous row by amount band (₹0–2,000, ₹2,000–10,000, ₹10,000–1 lakh,
  ₹1 lakh and above). The row holds no customer, amount, payee, device, place or time, and the app sends nothing new.
- **Until a band earns the model:** its payments get the stricter of the rules and the model.
- **Each night:** the rules and the model are graded against each other on that band's real outcomes.
- **Earning the model:** a band that has enough evidence and wins 4 weeks running can be moved to the model by two different
  operators. It falls back to the rules on its own if the model starts losing.
- **Admin API:** `/admin/api/band-gates`. Command line: `python -m amfa.cli band-gates --tenant <id>`.
- **Model updates:** each band trusts one model version.
  - Install new weights as a candidate (`python -m amfa.engine.risk.train --candidate`, or
    `python -m amfa.cli risk-candidate set <version>`). It watches every payment and decides nothing.
  - It earns each band on its own answers, and two operators trust it there.
  - A platform operator promotes it (`python -m amfa.cli risk-candidate preview|promote`). Bands that trusted it keep the model
    with no gap.
  - Weights made active any other way are never trusted by a band that was not approved for them.
- **Replay:** `python scripts/replay_band_switch.py` replays a simulated bank through it.
- **Its own service:** by default the band switch runs inside the core. Set `AMFA_BANDS_URL` and it runs as the band service
  (`python -m amfa.cli serve bands`, port 8104), with a database of its own. Only the core may call it, over signed requests. If
  it is slow or down, payments get the stricter plan. The Docker deployment runs it this way. For a bank that already has band
  data in the core, run `python -m amfa.cli bands-move` once (`--dry-run` first).

Design, security and results: [docs/rbi-banded-data-collection.md](docs/rbi-banded-data-collection.md) and
[docs/band-switch-replay.md](docs/band-switch-replay.md).

## Customer controls (both optional, both off unless the bank turns them on)

- **No extra checks up to ₹X a day** (`customerBypass`): payments within the customer's own limit to known payees from
  a known phone skip the risk-based face and voice checks, never the legal minimum. Setting it needs a verified
  `CHANGE_LIMIT`; a raise waits out a cooling-off. [docs/customer-limit.md](docs/customer-limit.md)
- **Family helper** (`familyHelpers`): a customer who gets help paying can register a family member's phone, so that
  phone is expected instead of strange. Added only against a verified approval with a single-use code and a
  cooling-off; removed at once. [docs/family-helper.md](docs/family-helper.md)

## Android app

`mfa-android` is a native Kotlin / Jetpack Compose app for the demo bank with the same screens as the web bank plus the
phone's own key, no-extra-checks limit, sign-up questions, family helper and "is this your new phone". Every request
shows a loader and ignores taps while it runs. Build, install and test notes: [mfa-android/README.md](mfa-android/README.md).
A tag like `android-v1.0.4` publishes the APK on a GitHub Release.

## Testing and evaluation

```bash
.venv/bin/python -m pytest -q                                   # Python suites (engine, core, factors, demo bank)
.venv/bin/python -m amfa.demo.regression                        # named personas through the whole decision path
.venv/bin/python scripts/measure_readiness.py                   # how long new customers take to be known
.venv/bin/python scripts/evaluate_datasets.py 4000 --md=docs/dataset-evaluation.md   # five different dummy banks end to end
.venv/bin/python scripts/smoke_demo.py                          # a running stack: sign-up, payments, limit, helper, retention
.venv/bin/python scripts/replay_band_switch.py 2000             # a simulated bank through the band switch from day one
(cd mfa-android && ./gradlew :app:testDebugUnitTest)           # 131 Android unit tests
```

Latest five-bank result: 63 of 65 checks met ([docs/dataset-evaluation.md](docs/dataset-evaluation.md)). Known and
documented: the release fairness gate still fails for older customers (senior segment step-up ratio about 2.3 against
a limit of 1.25), so the risk model in use was activated by hand (`--force`). All numbers are on simulated customers;
there are no real fraud labels yet.

## Data kept: 90 days

Transactions and everything derived from them are deleted after 90 days (hourly, and `python -m amfa.cli purge`;
`AMFA_RETENTION_DAYS` changes the window) and the risk model reads no more than that. Customers, enrolled templates,
devices and the audit log stay. The band switch's anonymous rows lose their link to the payment at the same 90 days, and are
kept for the bank's `collection.retentionDays` (default 365).

## Settings

| Variable | Values |
|---|---|
| `AMFA_AGENT_BACKEND` | `local` (Qwen2.5-3B on the server, can only add checks), `off` (risk model only) |
| `AMFA_ADMIN_TOTP` | `required` (password + authenticator), `off` (password only) |
| `AMFA_VOICE_PROMPT_CHECK` | `advisory`, `enforce`, `off` |
| `AMFA_RETENTION_DAYS` | days of transactions kept and read by the risk model (default 90; a model trained on a longer window is refused) |
| `AMFA_BYPASS_CAPS` | the platform's ceiling for a customer's "no extra checks" limit per currency (default ₹50,000) |
| `AMFA_BANDS_URL` | where the band service listens (`http://bands:8104` in Docker); unset, the band switch runs inside the core |
| `AMFA_BANDS_TIMEOUT_S` | how long a payment waits for its band's state before taking the stricter plan (default 0.2) |
| `AMFA_BANDS_DB_URL` | the band service's own database (PostgreSQL in production; `data/bands.db` in development) |
| `AMFA_UEBA_TOUCH_MODE` | `live` (phone typing and shape count toward the decision; default), `shadow` (scored and logged only, never counted) |

Per-bank policy (permitted factors, risk appetite, minimum checks, how many checks the AI may
add, and the optional features `learning`, `payeeReputation`, `customerBypass`, `familyHelpers` and `collection`) is edited in the admin console. A change that relaxes `collection` (turning it off, asking for less evidence, more rows a day) is refused there: one operator requests it through `/admin/api/band-gates/policy-request` and a second approves it (there is no console screen for this yet). Leaving `OTP` out of the permitted factors removes the
authenticator from the customer flow; face or voice then serves as the dynamic factor.

## Layout

```
amfa/engine      decision engine: risk model, AI agent, bank policy
amfa/core        platform API, orchestration, proofs, admin console, hosted pages
amfa/factors     face, voice and behaviour services
amfa/bands       the band switch: its logic, its tables, and the service it runs as
sdk/python       bank client
examples         demo bank (web) and its tests' fixtures
mfa-android      native Android app
deploy           Dockerfile, compose files, Caddy, deploy script
scripts          model download and verification, demo seeding, readiness measurement,
                 five-bank evaluation, live-stack smoke test
docs             design notes, measurements and status (see docs/)
tests            Python test suites
```
