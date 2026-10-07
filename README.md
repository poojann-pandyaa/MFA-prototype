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

.venv/bin/python -m amfa.cli serve face &
.venv/bin/python -m amfa.cli serve voice &
.venv/bin/python -m amfa.cli serve ueba &
AMFA_AGENT_BACKEND=off AMFA_PUBLIC_URL=http://127.0.0.1:8040 .venv/bin/python -m amfa.cli serve core --port 8040 &

AMFA_BOOTSTRAP_PASSWORD='choose-a-long-password' .venv/bin/python -m amfa.cli init --admin-email admin@demo.local
.venv/bin/python -m amfa.cli tenant create --name "Demo Bank" --slug demo-bank
.venv/bin/python -m amfa.cli apikey create --tenant <tn_id> --name demo-bank
```

Admin console: http://127.0.0.1:8040/admin

## Settings

| Variable | Values |
|---|---|
| `AMFA_AGENT_BACKEND` | `local` (Qwen2.5-3B on the server), `off` (rule table only) |
| `AMFA_ADMIN_TOTP` | `required` (password + authenticator), `off` (password only) |
| `AMFA_VOICE_PROMPT_CHECK` | `advisory`, `enforce`, `off` |

Per-bank policy (permitted factors, risk appetite, minimum checks, how many checks the AI may
add) is edited in the admin console. Leaving `OTP` out of the permitted factors removes the
authenticator from the customer flow; face or voice then serves as the dynamic factor.

## Layout

```
amfa/engine      decision engine: rules, AI agent, bank policy
amfa/core        platform API, orchestration, proofs, admin console, hosted pages
amfa/factors     face, voice and behaviour services
sdk/python       bank client
examples         demo bank
deploy           Dockerfile, compose files, Caddy, deploy script
scripts          model download and verification
```
