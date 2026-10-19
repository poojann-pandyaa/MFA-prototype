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

The bank acts only after it verifies the proof's signature and binding and consumes it, so an approval
can never be replayed or applied to a different amount.

**The journey.** Every action has the same shape: review (what, to whom, which checks are coming and why,
and a scam warning when AMFA asks for one) → "Confirm it's you" (AMFA's own pages, full screen, one step at
a time) → result (sent, held for review with a live timeline, or not sent with what to do next). The reason
comes from `plan.customerNotice`: the rule table's signals in plain words, never a spoof or risk-list signal.
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
label and raises a security event; review verdicts become labels too (`risk_label`).
"Freeze my account" suspends the customer in AMFA and blocks every action until the bank unfreezes it.

**Demo mode** (`BANK_DEMO_TOOLS=1`, the default; set `0` for a real deployment). A "Behind the scenes" button
opens AMFA's decision on the last action (risk band, the reasons, the checks the rule table asked for and
anything the AI agent or bank policy added) and stages a SIM swap, a payee on a mule list, a new phone, or an unfreeze.

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

## Decision engine

Each request's checks come from the reviewed rule table (`amfa/engine/rules.py`): a routine payment from a
known device asks for a one-time code; a new payee or high value adds face and typing; an unknown device in
an unknown place adds face and voice; compound risk asks for everything. The bank's mandated checks are
added, the local AI agent may add (never remove) checks the table missed, unavailable checks are
substituted, and the regulatory floor is applied last.

**Low-touch** (`lowTouch`, on by default): a high-risk request, or one whose preferred checks the customer
hasn't set up, is answered with the strongest checks the customer can do rather than a queue. A person
reviews only what checks can't settle: a payee on a risk list, an account takeover followed by a cash-out
(SIM, contact or password changed in the last day or two, then money to a new payee), a recent suspected
fake face with repeated failed checks, a watchlisted customer, or a request whose regulatory minimum can't
be met. A large first payment to a new payee also gets the scam warning and a second biometric. These read
the account and payment signals in `amfa/engine/risk/features.py`; banks may send optional
`accountEvents` (SIM, contact or password changes; payee risk flag) with each transaction.

## Settings

| Variable | Values |
|---|---|
| `AMFA_AGENT_BACKEND` | `local` (Qwen2.5-3B on the server, can only add checks), `off` (rule table only) |
| `AMFA_ADMIN_TOTP` | `required` (password + authenticator), `off` (password only) |
| `AMFA_VOICE_PROMPT_CHECK` | `advisory`, `enforce`, `off` |
| `AMFA_UEBA_TOUCH_MODE` | `live` (phone typing and shape count toward the decision; default), `shadow` (scored and logged only, never counted) |

Per-bank policy (permitted factors, risk appetite, minimum checks, how many checks the AI may
add) is edited in the admin console. Leaving `OTP` out of the permitted factors removes the
authenticator from the customer flow; face or voice then serves as the dynamic factor.

## Layout

```
amfa/engine      decision engine: rule table, AI agent, low-touch review rules, bank policy
amfa/core        platform API, orchestration, proofs, admin console, hosted pages
amfa/factors     face, voice and behaviour services
sdk/python       bank client
examples         demo bank
deploy           Dockerfile, compose files, Caddy, deploy script
scripts          model download and verification
```
