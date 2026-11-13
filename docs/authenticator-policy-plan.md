# Multiple authenticators with an AI that picks which one to impose

Status: design plan, nothing built. It extends the payment-risk model (`backend/ato/`) from "pick one action" to "pick the right sequence of authenticators for this payment, this user, and this level of doubt".

## 1. The short answer, and the honest limit

Yes: build a policy engine that takes the model's risk, the amount, and the authenticators this user actually has, and chooses the cheapest sequence of checks that brings the remaining doubt below what the bank is willing to accept. After each step it updates the doubt, and stops, adds a step, or blocks.

**What it cannot do:** reach zero doubt. No authenticator is perfect, some defeat each other (a compromised phone defeats every on-device check), and the real owner can be tricked into passing every identity check. What it can do is **measure the residual doubt after each step, say what it is, and refuse to proceed when it stays too high.** Every number below is an assumption until real authenticator outcomes are logged.

## 2. The key idea: there are three different doubts

A face check answers "is this the owner?". It does nothing about "is the owner being tricked?" or "is the receiving account bad?". So each authenticator is chosen for the doubt it can actually reduce.

| Doubt | Typical scam | Authenticators that reduce it | Authenticators that do NOT help |
|---|---|---|---|
| **Identity**: is it the owner? | SIM swap, stolen phone, malware, remote control | second-device approval, passkey, face + liveness, device prompt, bank call-back | SMS code (a SIM swap reads it) |
| **Intent**: is the owner acting freely and informed? | digital arrest, fake customer care, investment, romance | trusted-contact approval, call-back, cooling-off wait, scam warning | face, passkey, PIN, SMS code (the victim passes or reads them out) |
| **Payee**: is the receiver legitimate? | mule, fake seller, look-alike payee | payee verification (name match, small test credit), government fraud-risk data, wait | any identity check |

This is why "add a face check at high risk" is not enough: for a digital-arrest victim a face check passes, and the money still goes.

## 3. The AI: a decision policy, not a bigger neural network

**Inputs:** the model's three scores, the amount, the user's enrolled authenticators and protection mode (standard, assisted), what has already been passed in this payment, and the bank's risk settings.

**Step 1, turn the model's score into a probability of scam.** The model's severity band has a measured ratio of "how common among scams versus genuine payments" (in the simulation: MEDIUM about 6x, HIGH about 107x, CRITICAL about 6,800x). Multiply the odds of the base rate by that ratio. The base rate is a setting (about 1 in 100,000 for real UPI, according to reported figures).

**Step 2, price the choices.** For every sequence of up to three enrolled authenticators (plus "allow" and "block"), compute the expected cost:

`friction cost of the steps taken + (probability a scam gets through) x amount + (probability a genuine user is stopped) x cost of wrongly stopping`

A scam gets through only if it passes every step; each authenticator has a pass rate for genuine users and a pass rate for each scam family. Pick the cheapest.

**Step 3, update after every outcome.** If the user passes step 1, the probability of scam falls (by that authenticator's ratio); if they fail, it rises and the engine escalates or blocks. It explains each choice ("this payment is 3x the usual amount to a new account; asking your second device because identity doubt is highest").

**Why not a neural net:** there are no logged authenticator outcomes to train one, the decision is small (a few authenticators, three steps), and a transparent cost model is something a bank's risk team can inspect and set. Once real outcomes exist the pass rates are learned from them, and a contextual bandit can tune the choices. Reinforcement learning is not needed.

## 4. Authenticator catalogue (parameters are ASSUMPTIONS, not measurements)

Pass rates are "chance the check passes". The face figures come from the repo's own liveness calibration (liveness only: 8.7% of presentation attacks accepted, 12.7% of genuine users rejected, on the LCC-FASD test split in `config.py`); everything else is my estimate.

| Authenticator | Answers | Channel | Genuine passes | Takeover passes | Coached-victim passes | Friction (rupee-equivalent) | Buildable here |
|---|---|---|---|---|---|---|---|
| Device prompt (fingerprint/PIN re-prompt) | identity (weak) | device | 97% | 35% | 100% | 1 | soft prompt only (server cannot verify) |
| Face + liveness (exists) | identity | device | 87% | 9% | 100% | 8 | yes, built |
| Second-device approval | identity | other device | 95% | 3% | 100% | 6 | yes |
| Trusted-contact approval | intent | human | 85% | 5% | 25% | 40 | yes |
| Cooling-off wait | intent, payee | time | 90% | 40% | 35% | 25 | yes |
| Bank call-back | identity, intent | bank | 90% | 2% | 30% | 60 | mock only (needs bank staff) |
| Scam warning (read and confirm) | intent | education | 100% | 100% | 60% | 2 | yes |
| Payee verification (name match, test credit) | payee | payee | 97% | 90% | 90% (scam payee passes 30%) | 4 | mock |
| Passkey (WebAuthn) | identity | device | 98% | 5% | 100% | 4 | yes in the web demo (`py_webauthn`) |
| Authenticator-app code (TOTP) | identity | other device | 96% | 10% | 100% (victim reads it out) | 6 | yes (`pyotp`) |
| SMS code | identity (weak) | SIM | 98% | 95% (SIM swap) | 100% | 3 | low value; include only as the weakest option |
| Voice | identity | device | n/a | n/a | n/a | n/a | exclude: voice cloning defeats it (the repo's voice branch exists but should carry little weight) |

The amount-based "cost of wrongly stopping a genuine payment" is also a setting: the worked example uses Rs 100 + 0.5% of the amount.

## 5. Worked example (computed with the assumptions above)

Rows show what the engine would choose. "Fraud completes" is the share of scams that still get through the chosen sequence.

**Real rarity (1 scam in 100,000 payments):**

| Alert and amount | P(scam) after model | User has face only | User has face + second device | User has everything incl. trusted contact + call-back |
|---|---|---|---|---|
| MEDIUM, Rs 400 | 0.006% | allow | allow | allow |
| HIGH, SIM-swap-like, Rs 15,000 | 0.11% | device prompt (41% of scams still complete) | same | same |
| HIGH, collect-request-like, Rs 4,000 | 0.11% | allow | allow | allow |
| CRITICAL, takeover, Rs 80,000 | 6.4% | face + payee check + wait (5% complete) | second device + payee check + wait (3%) | second device + payee check + call-back (2%) |
| CRITICAL, digital-arrest-like, Rs 2,40,000 | 6.4% | **block** | **block** | wait + trusted contact + call-back (3% complete) |

**One in 1,000 (a high-risk channel):** HIGH takeover Rs 15,000 gets face + payee check + wait (or second device + payee check + wait with that enrolled), HIGH coached Rs 4,000 gets blocked, and every CRITICAL gets blocked.

**What this shows:**
1. **At real rarity most alerts are genuine, so the engine picks light steps** unless the amount is large. That is the correct economics, but it leaves a lot of scams through at HIGH. The bank's risk appetite (loss weight and wrong-stop cost) is a dial, not something the AI can decide.
2. **Identity checks cannot clear a coached scam.** A digital-arrest-like payment with only identity authenticators ends in a block. Only human or out-of-band steps (trusted contact, call-back) bring the leak down to 3%.
3. **What the user has enrolled changes the achievable safety.** With a second device, takeover leak falls from 5% to 3% and genuine completion rises from 76% to 83%. This is the argument for a small mandatory set that includes one out-of-band channel.
4. **Blocking becomes the right answer when the posterior is high enough.** The engine should block rather than pile on steps.

## 6. Rules the engine must follow

- **Independence is limited.** A compromised phone defeats every on-device authenticator at once. Group authenticators into channels (device, other device, human, bank, time, payee) and give extra credit only for passing a different channel; within one channel count the second step at a heavy discount. When the takeover score is high, prefer other-device and out-of-band steps over a second on-device one.
- **The user never picks the weakest step.** The engine offers only options that meet the required strength; the user can choose among those (accessibility).
- **Accessibility and mode filters:** a `no_face` preference removes face; assisted mode favours trusted contact and waits, with longer windows. Age is never an input.
- **Never leave a dead end:** if nothing enrolled can reach the required strength, apply compensating controls (lower limit, split the payment, a wait, a bank call-back) before blocking, and tell the user how to enrol a stronger method.
- **Adding or changing authenticators is a high-risk event** (an attacker enrols their own). Delay and notify the old channels.
- **Explain every decision in plain words** (the demo shows it; a production app shows a short version without revealing signals).

## 7. How it fits this repo

- `backend/ato/authenticators.py`: the registry above as data (id, channel, answers, pass rates by family, friction, requirements, accessibility flags), versioned and editable by a risk team, with the source of each number.
- `backend/ato/policy.py`: pure functions `posterior(severity, family, prior)`, `plan(...) -> steps with predicted residual risk`, `update(state, step, outcome)`. No database, so it is easy to test.
- Tables (new, no changes to existing ones): `UserAuthMethod` (what the user has enrolled), `TxnStep` (step, outcome, time).
- `/txn/risk` returns a **plan** (ordered steps, each with the reason and the residual risk after it) instead of one action; a step result endpoint records outcomes; approval endpoints let a second device or trusted contact approve. The current single-action response stays as a one-step plan so nothing breaks.
- Authenticators buildable and verifiable in this web stack: face + liveness (exists), second-device approval, trusted-contact approval, cooling-off wait, scam warning, passkey (WebAuthn), authenticator-app code. Mocked: call-back, payee verification, real SIM/telecom checks.
- Demo: the X-ray panel gets an **uncertainty meter** showing the probability of scam before and after each step, and the plan with its reasons.

## 8. Build plan and checks

| Phase | What | Checks (they can fail) |
|---|---|---|
| 1 (M) | Registry + policy functions | Property tests over every combination of enrolled methods and every severity: always returns a valid plan; never uses an unenrolled method; respects `no_face`; terminates in at most three steps; blocks when the posterior is high; plan cost never exceeds "allow" or "block" cost; higher amount never gives a weaker plan; passing a step never raises the posterior |
| 2 (M) | Simulate authenticator outcomes (genuine pass, scam pass by family, abandonment) in the simulator and evaluate whole policies | For each policy (fixed ladder, "always strongest", the AI plan) report fraud that completes, genuine completion, mean friction, per scam family and per enrolment mix. Gate: the AI plan has lower expected cost than both baselines, and no enrolment mix has fraud-completion above a stated limit |
| 3 (M) | Endpoints, tables, real authenticators (second device, trusted contact, wait, warning) | API tests per step type; a blocked or unverified payment cannot be confirmed; step outcomes recorded |
| 4 (M) | Passkey (WebAuthn) and TOTP | Registration and verification tests; replay rejected |
| 5 (S) | Demo uncertainty meter and plan view | Browser checks of each plan; the meter moves after each step |
| 6 (later) | Learn pass rates and costs from logged outcomes in shadow mode; optional bandit tuning | Compare learned versus assumed rates; re-run Phase 2 on real numbers |

## 9. Caveats

- Every pass rate and cost here is an assumption. The conclusions about which steps to choose can flip with different numbers, so Phase 2 must report sensitivity, and Phase 6 replaces assumptions with logged data.
- Independence between authenticators is approximate; the channel rule is a guard, not a proof.
- Attackers adapt. If they learn which step the engine picks, they target its weak point, so the engine should vary among equal-cost options and log outcomes for review.
- A smarter policy cannot fix a weak model: the plan only reduces doubt that the model reports. At real rarity the model's HIGH band is mostly genuine users.
- Whether to block, and how much risk to accept, is a business and regulatory decision. RBI's authentication directions allow risk-based extra checks (clause 8) but do not set these dials.

## 10. Decisions needed

1. Which authenticators to build first (recommended: second device, trusted contact, wait, warning, then passkey).
2. Whether to include a mandatory minimum at enrolment (recommended: face + one out-of-band channel for full limits).
3. The bank's risk settings for the demo: base rate (1 in 100,000 or a higher-risk 1 in 1,000), cost of wrongly stopping a genuine payment, and how heavily to weigh losses.
4. Whether the demo shows the engine's reasoning to everyone or only in presenter mode.
