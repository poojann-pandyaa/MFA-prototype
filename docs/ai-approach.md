# The AI approach: what it is, how it works, and how to take it further

Plain language, no code. For *how a request flows* see [flow.md](flow.md); for *why each choice was made* see [decision.md](decision.md). This page is about the AI itself: what each piece does, how it was built, how far it can be trusted today, and what to build next.

---

## 1. The big idea

A payment protection system has to answer three different questions, and they need different kinds of intelligence:

| Question | Who answers it | Why this kind of tool |
|---|---|---|
| **How suspicious is this request?** | A trained **risk model** | It must weigh dozens of weak signals together and compare against patterns of fraud, which rules cannot do well. |
| **Is the person here really the customer?** | **Biometric models** (face, voice, typing) | Recognising a face, a voice or a typing rhythm is a pattern-recognition job. |
| **What should we do about it, and can anyone overrule it?** | A **rules layer** | This must be explainable, predictable and impossible to talk into a mistake. |

A small **language model** sits on the side as an experiment: it can suggest *extra* checks in grey areas.

We deliberately kept the final say in rules. AI judges and recognises; rules decide what to do with that judgement and keep the safety floors.

---

## 2. The pieces

### 2.1 The risk model: the main "AI decides" part
- **What it reads.** About 38 facts about the request and the customer: how the amount compares with what they usually pay, whether the payee, phone, network and place are familiar, how unusual the time of day is, recent failed checks, recent SIM, contact or password changes, whether the payee is on a risk list, how much history the customer has, and so on.
- **What it produces.** A fraud score, turned into one of five levels from T0 (very ordinary) to T4 (severe), plus the top reasons for the score.
- **How it's built.** An ensemble of five gradient-boosted decision-tree models whose average is calibrated into a real probability. Some features are constrained so risk can only go one way (a bigger amount can never look *safer*), which stops an attacker from lowering their score by pushing a value in the "risky" direction and keeps explanations sensible.
- **How levels are set.** Not by score alone: the cut-offs are chosen so that only a target share of *genuine* traffic lands at each level. This is a deliberate friction budget: roughly one in five genuine requests gets at least the light check, one in twenty the face check, and very few get the heaviest. The bank can see and tune the budget.
- **Every decision is logged** with its inputs, level and reasons, so it can be audited and later used for learning.

### 2.2 The biometric and behaviour models
- **Face:** finds the face, matches it to the enrolled template, and runs a separate anti-spoofing model that looks for photos, screens and replays; a random head turn makes pre-recorded video hard.
- **Voice:** matches the voice to the enrolled one, runs a deepfake detector, and uses speech recognition to confirm the customer said the random words shown.
- **Typing and behaviour:** an anomaly detector that learns how this customer types a phrase and traces a line, and flags a mismatch quietly, with no visible effort for the customer.

### 2.3 The language model (advisory, small)
- A small model that runs on the same machine, so no customer data leaves it.
- It sees only coarse flags (new payee, unfamiliar phone, odd hour), never text a customer typed.
- It may suggest *extra* checks and may never remove one. Its answer must fit a fixed format, and if it's slow or wrong, the system carries on without it.
- It is consulted only where it can help: not at low risk, not where a relief has already judged the request ordinary, and not for customers with no history (all of their signals look "unfamiliar").

### 2.4 The rules layer (not AI, on purpose)
Keeps the regulatory minimum, applies the reliefs for ordinary traffic (small payments, regular payees, new customers), keeps the short list of situations that go to a person, shapes the checks for very large first payments, locks accounts on suspected fake faces, and writes the plain-language explanation. See decision.md section 2.20.

---

## 3. How the risk model learned: the most important thing to understand

**It learned from a simulator, not from real fraud.** We have no real labelled outcomes yet, so the model was trained on 20,000 invented customers over 150 days (about 1.2 million requests, about 12,500 of them fraud). The simulator is detailed:
- 14 kinds of customer (salaried, night-shift, student, small business, traveller, retiree helped by family, and more);
- 47 *genuine* situations that look like fraud (a new phone while travelling, a real SIM change, a wedding, a big one-off purchase);
- 25 *attack* types (account takeover, SIM swap, coached scams, remote-access scams, mule accounts, bots, card testing, slow drip, bust-out, and more);
- five "data feed" variations, because banks won't all send every signal.

Training splits by time (learn from the past, test on the future) and compares the model against a simple baseline it must beat before it is released.

### What the report card says
- **On the simulated future, it looks excellent**: it catches most simulated fraud while stepping up only a few percent of genuine requests. It beats the simple baseline comfortably.
- **But that is a measure of how well it learned the simulator, not of how well it will protect real customers.** Real fraudsters adapt, and real customers do things the simulator never imagined. The model's own card says so: *retrain on real labelled outcomes before trusting these numbers.*
- **It has known weaker spots**, mostly low-and-slow patterns and some automated or testing behaviour, where the simulated catch rate was clearly lower. A real deployment should expect gaps like these.
- **"Caught" means "triggered extra friction", not "prevented".** For a coached scam, where the genuine customer is the one at the camera, extra friction does not stop the payment. See section 5.

### A finding we need to act on
- **The active model failed its own fairness gate.** In simulated tests, older customers were stepped up on genuine requests at roughly two to three times the average rate, and premium customers somewhat more. The release gate flags anything much above average, and this model was activated anyway (a deliberate choice earlier, to get the prototype running). For a real deployment this must be fixed first: it means some groups would face far more friction for behaving normally.
- A plausible cause is that simulated older customers do more unusual-looking genuine things (large one-off payments, family-assisted payments), so "unusual" became "risky" for them. It needs investigation before we change anything, and age is deliberately *not* an input to the model, so the fix is in the data and the training, not in adding age.

---

## 4. How the AI and the rules work together on one request

1. The risk model scores the request and gives a level and reasons.
2. **Reliefs** can lower the level for ordinary, low-warning requests; they never apply to very large payments or to account changes.
3. The bank's policy and the regulatory minimum can **raise** the checks to their floors.
4. The language model, if consulted, may **add** a check.
5. Very large first payments get the scam warning and a second biometric where available.
6. The rules layer decides whether a person is needed (short list), or the customer simply does the checks.
7. Everything is logged with the reasoning; later outcomes become labels.

The one-way rules matter: the AI can raise friction freely, but lowering friction only ever happens through explainable rules with clear conditions.

---

## 5. What AI can and can't do here (honest limits)

- **Takeover fraud:** biometrics plus a risk score are strong. Someone else can't produce your face and voice.
- **Coached or tricked payments (scams):** the genuine customer passes every check. Detection must come from other signals (is the customer on a phone call, is a remote-access app running, is a new payee being paid a large amount in a hurry, hesitation or copy-pasting of account details), plus **friction aimed at the person, not the identity**: a tailored warning, a pause, confirming the payee's registered name. Our simulator has no call or screen-sharing signals, so the model can't learn them.
- **Slow, low-value patterns and automation:** weaker; these need per-customer and per-payee memory over time.
- **New customers:** little history means the model has little to compare against; the relief for new customers trades some detection for a much better first impression.

---

## 6. How to take it further: a staged plan

Ordered by value and by what must come first.

### Stage 0. Measure what we've already built (small, do first)
- **Evaluate the whole pipeline (model, reliefs, rules, holds) against the simulated attacks.** So far we measured the model alone; the new low-touch reliefs and the shorter admin list changed the outcome and haven't been measured. This tells us exactly how much detection we traded for friction.
- **Fix or consciously accept the fairness finding**: retrain with the groups balanced, or add a fairness constraint to training, and re-check the gate. Don't leave a model active that fails its own gate without a written reason.

### Stage 1. Close the loop with real outcomes
- Reviewer verdicts and customer "this wasn't me" reports are already saved as labelled examples. Add: disputes and chargebacks (late labels), "customer abandoned the check" (friction labels), and confirmed-genuine outcomes.
- Build a repeatable **retrain, compare, release** routine: train a challenger on real labels, run it silently beside the live model ("shadow mode"), compare, and only then switch. Keep the old model one click away.
- Keep the simulator for rare attacks that real data will take years to supply, and blend it in.

### Stage 2. Add the signals scams need
- App-side signals: is a call in progress, is a screen-sharing or remote-control app active, was the payee's account number pasted rather than typed, how long was the customer idle then rushed, were they switching between apps.
- Payee intelligence: the payee's registered name against what the customer typed, how many other customers have recently paid this payee (mule detection), and whether it is a known merchant. A shop that is known and verified is much safer than a first-time individual, and the system doesn't currently know the difference.
- Extend the simulator with these signals so the model can learn them.

### Stage 3. Make the AI choose checks by cost, not just risk
Today the level sets a fixed list of checks. A stronger approach picks the **lightest set of checks that meets the risk target for this customer**:
- Learn, per check, how often it catches fraud versus how often customers fail or give up on it, per device and situation (a noisy room makes voice a poor choice; a dark room makes face a poor one).
- Choose the combination that minimises customer effort for a required level of protection. This is a classic "pick the best action under a cost" problem, and it is where an adaptive model genuinely beats fixed rules.
- Needs friction telemetry (time taken, retries, abandonment) that we don't collect yet.

### Stage 4. Use the language model where it is actually strong
- **Review assistant:** summarise a held request, recommend approve or reject with reasons, draft the note, find similar past cases. Advisory only, run in hidden shadow mode first, with the agreement rate measured.
- **Scam-specific warnings:** write a short warning tailored to the situation ("you're paying an individual for the first time after getting a call") instead of one generic message. The model only phrases; rules choose when to show it.
- **Explanations and triage** for staff, in plain language.
- A hosted, stronger model gives better quality, but case data leaves the machine, so mask it first and keep a local model as the fallback.
- Guardrails stay: strict output format, no customer-typed text in the prompt, no authority to approve, waive or lock.

### Stage 5. Run it like a real model
- **Monitoring:** watch for drift (are today's requests unlike the training data?), the share of genuine customers stepped up, the fraud caught, and the same by group.
- **Fairness checks as a release gate** that actually blocks release.
- **Champion/challenger and a kill switch:** run a new model beside the old one, and have a simple way back.
- **Model cards and audit:** every model version has a recorded data source, results and sign-off; every decision keeps its inputs.

---

## 7. A suggested order

1. **Stage 0** (measure the whole pipeline; address the fairness result). Everything else rests on trusting the numbers.
2. **Stage 1** (real labels and safe retraining). This is what turns a prototype into a system that improves.
3. **Stage 2** (scam signals). This closes the biggest gap in what we protect against.
4. **Stage 4** (review assistant, tailored warnings), once there is real review traffic to learn from.
5. **Stage 3** (cost-based choice of checks) once friction telemetry exists.
6. **Stage 5** continuously, from the first real release.
