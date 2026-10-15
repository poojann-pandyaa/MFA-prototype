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

## Risk model

Every transaction's threat level (T0–T4) comes from the risk model in `amfa/engine/risk`, which
picks the factors: T0 OTP, T1 + behaviour, T2 + face, T3 + voice, T4 manual review. The bank's
policy floor still applies on top. With no active model, every transaction fails closed to manual
review.

**Memory.** The model looks at each customer's last 365 days, never longer. Besides usual amounts, devices
and places, it checks whether the payee has been paid before, how long ago, at about what amount and on
what schedule (weekly to yearly), so rent, premiums and bills that repeat are recognised as familiar.

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

## Settings

| Variable | Values |
|---|---|
| `AMFA_AGENT_BACKEND` | `local` (Qwen2.5-3B on the server, can only add checks), `off` (risk model only) |
| `AMFA_ADMIN_TOTP` | `required` (password + authenticator), `off` (password only) |
| `AMFA_VOICE_PROMPT_CHECK` | `advisory`, `enforce`, `off` |

Per-bank policy (permitted factors, risk appetite, minimum checks, how many checks the AI may
add) is edited in the admin console. Leaving `OTP` out of the permitted factors removes the
authenticator from the customer flow; face or voice then serves as the dynamic factor.

## Layout

```
amfa/engine      decision engine: risk model, AI agent, bank policy
amfa/core        platform API, orchestration, proofs, admin console, hosted pages
amfa/factors     face, voice and behaviour services
sdk/python       bank client
examples         demo bank
deploy           Dockerfile, compose files, Caddy, deploy script
scripts          model download and verification
```
