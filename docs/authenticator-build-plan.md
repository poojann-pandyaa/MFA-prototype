# Build plan: add the 11 authenticators and integrate them into the app

Status: plan only. Builds on `docs/authenticator-policy-plan.md` (the decision engine idea) and `docs/authenticators-explained.pdf` (what each authenticator is). Names below match the current code: `backend/main.py` (`/txn/risk`, `/txn/{id}/confirm`, `/login/step2`), `backend/ato/model.py` (risk model and `ACTIONS`), `backend/models.py` (`Transaction`, `TxnStepUp`, `VerificationSession`), and the React app in `frontend/src`.

## 1. The rule that keeps friction low

You do not want slow authenticators to drive users away. So the 11 are split into two tiers, and the engine treats them differently.

| Tier | Authenticators | Typical time | How the engine may use them |
|---|---|---|---|
| **Fast (default)** | own device key, behavioural biometrics, passkey, scam warning, TOTP, payee verification, second-device approval, face | invisible to about 20 seconds | Any step, any severity |
| **Slow (rare escalation)** | trusted-contact approval (about 3 min), bank call-back (about 5 min), cooling-off hold (about 30 min) | minutes | Never the first step for ordinary risk. Only when the payment is CRITICAL, or HIGH with a tricked-owner score and a large amount, or after fast steps leave too much doubt |

Built into the engine as hard limits (all settings, so a bank can change them):
- **Time budget per payment:** MEDIUM at most about 10 seconds of user time, HIGH at most about 30 seconds, CRITICAL no limit.
- **Slow steps are gated:** a step marked `slow` is allowed only above a severity and an amount, and the share of genuine payments that ever see one is capped (target at most 0.05%).
- **Escape hatch:** "I need this now" swaps a slow step for a fast stronger step when one is enrolled (for example second-device approval plus face), and the engine records that lower assurance was accepted.
- **Hold length scales:** 5 to 10 minutes for medium-large amounts, 30 minutes only for large payments to new accounts.

## 2. Architecture

```
React app (frontend/src)                         FastAPI (backend)
-------------------------                        --------------------------------------------
Pay screen ------ POST /txn/risk ------------->  ato.model (3 detectors + router)  -> severity, family, posterior
                                                 ato.policy (NEW)  <- registry of authenticators (NEW)
                                                                   <- user's enrolled methods (NEW table)
                <------ plan: ordered steps ----  returns a PLAN: steps, reasons, expected time, residual risk
StepRunner (NEW) runs the steps one by one:
  warning | payee check | device key | passkey | TOTP | face | second device | contact | hold | call-back
  each step: POST /txn/{id}/steps/{n}/start  -> challenge (bound to payee+amount+nonce+expiry)
             POST /txn/{id}/steps/{n}/answer -> result; engine updates the doubt; returns next step or "release"
POST /txn/{id}/confirm  -> refuses unless every required step passed and any hold has elapsed
PIN pad -> POST /txn/{id}/complete (demo ledger)
```

New modules (each small, with tests):

| File | Purpose |
|---|---|
| `backend/authn/registry.py` | The 11 authenticators as data: id, tier (fast or slow), channel, which doubt they answer, assumed pass rates, assumed seconds, needs (camera, second device, contact), accessibility flags, source of each number |
| `backend/authn/challenge.py` | One challenge service for all: create (bound to user, device, payment, nonce, expiry), verify once, attempt limits and lockout, audit log of outcomes |
| `backend/authn/methods/*.py` | One module per authenticator implementing the same interface: `enrol`, `start(txn)`, `verify(response)`, `seconds`, `channel` |
| `backend/ato/policy.py` | The decision engine: posterior from the model's scores, price every sequence of up to three enrolled steps, apply the time budget and slow-step gates, update after each outcome, return a plan with reasons |
| `backend/authn/routes.py` | The new endpoints (below) |
| `frontend/src/authn/StepRunner.jsx` plus one component per step | Runs a plan and shows each step |
| `frontend/src/pages/Security.jsx` | Where a user enrols and manages methods |
| `frontend/src/pages/Approve.jsx` and `Contact.jsx` | Second-device and trusted-contact screens |

## 3. Data model (new tables only; existing tables unchanged so the current SQLite file keeps working)

| Table | Holds |
|---|---|
| `UserAuthMethod` | user, method id, enrolled_at, active, label, delay_until (new methods wait before counting) |
| `AuthChallenge` | id, txn_id, step, method, nonce, payload hash (payee+amount), expires_at, attempts, status |
| `TxnStep` | txn_id, step index, method, outcome (passed, failed, abandoned, expired), seconds taken, residual risk after |
| `Approver` | user, approver device id, public details, delay_until |
| `TrustedContact` | user, contact name, contact address, status (invited, accepted), delay_until |
| `TotpSecret` | user, encrypted secret, last used time step |
| `RecoveryCode` | user, hash, used_at |
| `DeviceKey` | user, device id, public key, created_at, revoked_at |
| `WebAuthnCredential` | user, credential id, public key, sign counter, transports |
| `BehaviourTemplate` | stays on the device; server receives only a score (no table) |

## 4. API additions

```
GET  /me/methods                       list enrolled methods and what is available
POST /me/methods/{id}/enrol            start enrolment (guarded, delayed, notifies other channels)
DELETE /me/methods/{id}                remove (delayed)

POST /txn/risk                         (existing) now also returns:
     plan: [{step, method, tier, why, est_seconds, residual_risk_after}], time_budget, slow_allowed
POST /txn/{id}/steps/{n}/start         creates the challenge for that step
POST /txn/{id}/steps/{n}/answer        submits the answer; returns {passed, next_step | release | block, doubt}
POST /txn/{id}/escape                  "I need this now": swap a slow step for a fast one if enrolled
GET  /approvals/pending                second device: pending approvals
POST /approvals/{id}/respond           second device: chosen number or decline
GET  /contact/{token}                  trusted contact: details of the one payment
POST /contact/{token}/respond          approve, decline, call
POST /txn/{id}/cancel                  cancel a held payment
POST /txn/{id}/confirm                 (existing) now checks every plan step passed
```

Backward compatible: `action` in the current `/txn/risk` response stays, and a payment with `allow` has an empty plan.

## 5. Per-authenticator build spec

| # | Authenticator | Tier | Backend work | Frontend work | Libraries | Size |
|---|---|---|---|---|---|---|
| 1 | Scam warning, questions, typed confirmation | fast | question bank (JSON, weighted, random), record answers as counts, translation file | interstitial with a 5-second read, questions, typed name | none | S |
| 2 | Payee verification (mock) | fast | fake directory returning registered names, look-alike hint from the existing `payee_lookalike`, optional test-credit mock | name confirmation card, "Did you mean...?" | none | S |
| 3 | Face + liveness | fast | already built; add a non-face alternative flag and a step wrapper over `/login/step2` | existing `ChallengeCameraCapture` inside the runner, "simulate pass" in demo mode | existing | S |
| 4 | Second-device approval | fast | approver enrolment, pending list, number matching (correct number plus two decoys), single use, 3 wrong picks locks | `Approve.jsx` on the second device (polling every 2 seconds), number display on the paying device | none (polling first) | M |
| 5 | Own device key + payment signing | fast | store public key, challenge includes hash of payee+amount+nonce, verify ECDSA P-256, nonce store | WebCrypto key (non-extractable) in IndexedDB, sign in the background | `cryptography` | M |
| 6 | TOTP + recovery codes | fast | standard 6-digit code, one step either side, reject reuse, encrypted secret, hashed recovery codes | enrolment QR, code input, recovery codes screen | `pyotp` (or stdlib), a QR library | S to M |
| 7 | Passkey (WebAuthn) | fast | registration and authentication with a payment hash in the challenge, require user verification, counter check | the standard browser calls | `py_webauthn` | M |
| 8 | Behavioural biometrics | fast (invisible) | accept a similarity score from the device as the existing `bio_similarity` input; no new step | capture key and touch timing on the PIN pad, template on the device, one-class scorer | none | M to L |
| 9 | Cooling-off hold | slow | exists: `release_at` and the confirm check; add scaled lengths, a cancel endpoint, release notification | countdown screen, Cancel, tips, helpline | none | S |
| 10 | Trusted-contact approval | slow | invite, accept, secure single-use link, practice request, response window, delay on new contacts | `Contact.jsx` (a mobile page the contact opens), "Ask my contact" screen | none | M |
| 11 | Bank call-back (mock) | slow | scripted outcome in demo mode; real telephony is out of scope | scripted call screen | none | S |

Security checklist applied to every step: single use, short expiry, bound to payee+amount+nonce, attempt limit with lockout and alert, constant-time comparisons, no secrets in logs, outcome recorded as counts only, enrolment delayed and notified.

## 6. How it plugs into the app, screen by screen

1. **Pay screen (existing flow):** on submit it calls `/txn/risk`. If the plan is empty, go to the PIN step as today.
2. **StepRunner:** shows one step at a time with a progress strip ("Step 1 of 2, about 15 seconds") and the reason in plain words. A failed step moves to the next option or to the block screen.
3. **Slow steps:** a clear "this will take a few minutes" screen with the escape hatch and the helpline, and a notification when done.
4. **Security page:** lists methods, shows which are enrolled, nudges the user toward the minimum set (face plus one other-channel method), and makes new methods wait a day.
5. **Second device and trusted contact:** lightweight pages that work on a phone browser.
6. **Demo hooks:** the Scenario Lab and X-ray from `docs/demo-app-plan.md` show the plan, each step's time, and the doubt before and after.

## 7. Phases, checks, and gates

| Phase | Build | Checks (each can fail) |
|---|---|---|
| 0 (S) | `authn` package skeleton, registry data, challenge service, outcome log, new tables | Challenge cannot be replayed, expires, locks after the attempt limit; outcome log counts only |
| 1 (S) | Scam warning, typed confirmation, payee verification mock | API tests; the answers are recorded; copy review |
| 2 (M) | `policy.py` and the plan in `/txn/risk`, the StepRunner skeleton, `/steps` endpoints, confirm enforcement | Property tests over every combination of enrolled methods and severities: always a valid plan, never an unenrolled method, `no_face` respected, at most three steps, time budget obeyed, slow steps only when allowed, block when the doubt is very high, passing a step never raises the doubt |
| 3 (M) | Second-device approval | Wrong picks lock; approver cannot be the paying session; delayed new approvers; a replay is rejected |
| 4 (S) | Face as a plan step | Existing step-2 tests still pass; non-face path works |
| 5 (M) | Own device key with payment signing | A changed payee or amount fails verification; replayed nonce fails; revoked key fails |
| 6 (S to M) | TOTP and recovery codes | Reuse of a code fails; drift window works; recovery code works once |
| 7 (M) | Passkey (WebAuthn) | Registration and authentication tests; counter regression rejected; payment hash bound |
| 8 (M) | Slow set: hold with cancel, trusted contact, call-back mock | Contact cannot see other payments; no answer keeps the hold; escape hatch swaps to a fast step; link is single-use |
| 9 (M) | Outcome simulation and policy evaluation in the simulator | Compare fixed ladder, always-strongest, and the engine on fraud that completes, genuine completion, average time, by enrolment mix. Gates below |
| 10 (M to L) | Behavioural biometrics on the PIN pad | Score drift stable; fairness across groups; never blocks alone |
| 11 (S) | Security page, enrolment nudges, demo integration, shadow rollout, dashboard | Browser walk-through of every plan; shadow logs match the engine's decisions |

**Gates (on simulated outcomes now, on shadow data later):**
- MEDIUM payments add at most 10 seconds on average, HIGH at most 30.
- A slow step is shown to at most 0.05% of genuine payments.
- The engine beats both baselines on expected cost, and no enrolment mix lets more than a stated share of scams through (set after Phase 9).
- Genuine completion rate does not fall below the current figure by more than a few points.

## 8. Test strategy

- **Unit and property tests** for the policy engine and each method (pure functions, no database).
- **API tests** with the existing `client` fixture for every endpoint and failure path.
- **Browser checks** (preview tools) of each step screen, including keyboard use and a screen reader pass.
- **Red-team tests** each quarter: replay a recorded face challenge, approval fatigue, enrolling a rogue approver or contact, tampering with payee after approval.
- **Regression:** the model report's gates and the existing 94 tests must keep passing.

## 9. Rollout

1. **Shadow:** `MFA_ATO_SHADOW=1`. The engine builds plans and logs them without showing steps.
2. **Fast steps only, small group**, with a comparison group.
3. **Add second-device approval and face** for HIGH.
4. **Add the slow escalation set** for CRITICAL only.
5. **Full rollout.** Each stage advances only when completion rate, support tickets and the friction budget look healthy.

## 10. Risks

- **Assumed numbers.** Every pass rate and time is an estimate until real outcomes are logged. The engine reads them from the registry so they can be replaced without code changes.
- **Same-channel weakness.** A compromised phone defeats every on-device step; the engine prefers other channels at high takeover doubt.
- **Enrolment shortfall.** If few users enrol a second device or contact, the engine has weaker options. Mitigate with nudges and higher limits for enrolled users.
- **Slow-step abandonment.** Mitigate with scaled holds, the escape hatch, and clear messaging.
- **Scope.** Eleven authenticators is a lot. Phases 0 to 4 give a working, useful product; later phases can follow.
- **Native-only features** (hardware attestation, real telecom signals) stay mocked or simulated.

## 11. Decisions needed

1. **Order:** confirm Phase 0 to 4 first (plumbing, warning and payee mock, policy engine, second device, face).
2. **Time budget numbers:** 10 seconds for MEDIUM and 30 for HIGH, or different.
3. **Slow-step gate:** CRITICAL only, or also HIGH with a tricked-owner score and a large amount.
4. **Delivery of approvals:** polling first (simplest) or WebSocket from the start.
5. **Passkeys:** build real WebAuthn, our own device key, or both (recommended: device key first, WebAuthn after).
