# DemoPay

A separate demo banking app that shows the payment-risk model and the authenticator decision engine working during a payment. It does **not** modify the existing app (`backend/`, `frontend/`); it imports the trained model package `backend/ato` as a library.

**Everything is simulated**: the bank, the money, the PIN, the payee directory, and the phone signals (call in progress, SIM change, remote control are chosen on screen). Nothing here proves real-world accuracy.

## What it does

`POST /pay/risk` scores a payment with the model (takeover, coached-scam and bad-payee detectors), then a decision engine (`backend/authn/policy.py`) prices every option (allow, one to three checks the user has, block) and returns a **plan**. The web app runs the plan step by step, then a fake PIN pad moves the demo money.

Built authenticators (Phase 0 to 2 of `docs/authenticator-build-plan.md`):

| Step | Tier | What it does |
|---|---|---|
| Scam warning and questions | fast | full-screen warning, forced read, two random questions; a "yes" to a danger question stops the payment |
| Type the payee's registered name | fast | breaks autopilot, catches wrong-payee mistakes |
| Payee check | fast | shows the registered name, account age and any look-alike or report warning |
| Cooling-off wait | slow (rare) | only for CRITICAL, or HIGH + tricked-owner + large amount; shortened to 20 s in the demo |

Planned (listed in `authn/registry.py`, ignored by the engine until built): second-device approval, face, device key, TOTP, passkey, trusted contact, bank call-back.

Rules built into the engine: a user-time budget per severity (MEDIUM 10 s, HIGH 30 s), slow steps only for rare high-stakes cases, never a step the user has not enrolled, and a payment cannot move money until every step passed.

## Pre-registered customers (the playground)

On first start the API creates four invented customers, each with a profile (name, age, city, job, account number) and a payment history:
`asha` (busy professional), `ramesh` (68, retired, pays rarely), `kabir` (freelancer, pays at night), `meera` (new, almost no history).
Password for all: `demo1234` (shown on the sign-in screen in demo mode). Pick one, then:

- send the scenario payments and read the model X-ray;
- switch this customer's safety checks on or off (Home page) and send the same payment again: the plan is rebuilt from what is left;
- press Reset to restore their original history and balance.

Only the age is used by the policy (customers 60 or over get slow checks from Rs 10,000 instead of Rs 50,000: an assumption). The risk model itself
does not read profile fields.

## Run it

```bash
# backend (needs the existing project's venv for numpy / scikit-learn; see backend/requirements.txt)
cd demopay/backend
../../backend/.venv/bin/python -m uvicorn app.main:app --port 8070

# frontend
cd demopay/frontend
npm install
npm run dev            # http://localhost:5180
```

Sign up for a demo account, load a persona on the home screen, then pick a demo scenario on the Send screen. The **Model X-ray** panel (demo only) shows the three detector scores, the chance of scam, the plan and the doubt after each step.

## Android app

`android/` is a native Android client (Kotlin, Jetpack Compose) for this same API, with real phone-call detection and other on-device signals. See `android/README.md`.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DEMOPAY_DEMO` | 1 | Demo mode: X-ray, personas, scenario signals. **Turn off in any real deployment** (the X-ray reveals the reasons behind decisions). |
| `DEMOPAY_BASE_RATE` | 1e-4 | How common scams are in this traffic (reported UPI rate is about 1e-5). Higher makes the engine act more. |
| `DEMOPAY_HOLD_SECONDS` | 20 (demo) | Length of the cooling-off wait; the real value is 1800. |
| `DEMOPAY_SLOW_AMOUNT` | 50000 | Amount above which HIGH tricked-owner payments may use slow steps. |

## Tests

```bash
cd demopay/backend && ../../backend/.venv/bin/python -m pytest
```

Covers the engine's invariants over every combination (only enrolled steps, time budget, slow-step gate, never dearer than allow or block, doubt never rises), the challenge service (replay, expiry, lockout, tied to the payment), and the API flows with the risk model stubbed and with the real model.

## Known limits

- With only the four built authenticators the engine can only allow, ask for payee/intent checks, or block. Takeover scenarios (SIM swap, remote control) end in a block because no identity check (second device, face) exists yet.
- A tricked owner who answers the warning honestly ("no") passes the warning. That is why slow steps (trusted contact, call-back) matter for high-stakes cases; they are not built yet.
- All pass rates and costs in `authn/registry.py` and the likelihood ratios in `authn/policy.py` are assumptions from the simulated evidence report.
- Two scenarios (fake seller, prize fee) are deliberately ones the model often misses.
