# Proposal: AI-Driven Adaptive Authentication and Threat Management

**Project:** AMFA (Adaptive Multi-Factor Authentication) with the Demo Bank customer app
**Prepared by:** Poojan Pandya
**Date:** 19 October 2026
**Status:** Draft v1.0, for review
**Companion documents:** [flow.md](flow.md) (how it works) · [decision.md](decision.md) (why each choice was made) · [ai-approach.md](ai-approach.md) (the AI in more depth)

---

## 1. Executive summary

Banks lose money in two opposite ways. Fraud gets through when checks are too light. Customers abandon payments, and the bank, when checks are too heavy or when too many requests are queued for staff. Fixed rules ("always ask for a code above ₹10,000") cannot resolve this, because the right amount of checking depends on the situation, not only on the amount.

This proposal describes an adaptive authentication platform, **already built as a working prototype**, that uses machine learning to judge each request, chooses the lightest set of checks that is safe for that situation, and involves a person only when automated checks cannot settle the matter.

**What the system does**
- A trained **risk model** grades every sensitive request (a payment, a new payee, a limit or contact change) from T0 (ordinary) to T4 (severe).
- **Biometric and behavioural models** (face, voice, typing) verify that the person is the customer and is really present.
- A **language model** may suggest additional checks in grey areas; it can only add friction, never remove it.
- A **rules layer** keeps the regulatory minimum, applies fairness-minded reliefs for ordinary traffic, keeps a short list of situations that always go to a person, and explains every decision.
- Approvals are **signed, single-use and bound to the exact amount and payee**, so a tampered or replayed approval is worthless.
- Everything is logged in a tamper-evident audit trail, and confirmed outcomes become labels for retraining.

**What we are asking for:** agreement to move from a prototype trained on simulated data to a pilot trained and evaluated on real outcomes, with fairness and scam protection treated as release requirements. Section 12 sets out the plan, and section 13 the success measures.

**Honest status.** The prototype works end to end. Its risk model was trained on a simulator because real labelled fraud data does not yet exist, so its excellent test scores describe the simulator, not the real world. The model also did not pass its own fairness gate. Both are addressed in the plan below.

---

## 2. The problem

1. **Fraud is varied.** Account takeover, SIM swaps, stolen phones, fake faces and recordings, bots, mule accounts, and scams in which the genuine customer is tricked into paying.
2. **Friction costs money.** Each extra check loses customers. Treating a ₹500 payment to a regular payee like a ₹3 lakh transfer to a stranger drives people away or teaches them to click through warnings.
3. **People do not scale.** Sending every high-risk request to staff creates queues, slows genuine customers, and wears out reviewers.
4. **Regulation sets a floor.** For example, at least two kinds of proof with one of them dynamic. Anything adaptive must never fall below it.
5. **Customers' circumstances change.** A new phone while travelling, a real SIM change and a one-off big purchase all look like fraud. A system that treats unusual as guilty will hurt honest customers, and may do so unevenly across groups.

---

## 3. Objectives

| # | Objective | How we will know |
|---|---|---|
| 1 | Stop takeover and presentation attacks | Fraud caught at the checks, measured on real outcomes |
| 2 | Give the customer the lightest safe path | Share of genuine requests that need only a code; completion and abandonment rates |
| 3 | Keep humans for what only humans can settle | Share of requests sent to staff; time to decision |
| 4 | Protect against scams where the customer is the victim | Coached-payment detection and warning effectiveness |
| 5 | Treat customer groups fairly | Friction and error rates by group stay within agreed bounds |
| 6 | Be explainable and auditable | Every decision has reasons, inputs and a version recorded |
| 7 | Improve with use | Retraining on real, reviewed outcomes with safe release |

---

## 4. Solution overview

### 4.1 One request, five stages

```
Customer asks → Assess risk → Choose checks → Verify the person → Signed approval → Bank acts once
                     ↑              ↑               ↑                  ↑
                 risk model     rules + AI       biometric models   cryptographic
                                helper                              proof
```

### 4.2 Who does what

| Component | Kind | Role |
|---|---|---|
| **Risk model** | Machine learning (boosted decision trees) | Judges how unusual and dangerous the request is |
| **Face check** | Computer vision (detection, recognition, anti-spoofing) | Verifies identity and liveness |
| **Voice check** | Speech models (speaker recognition, deepfake detection, speech-to-text) | Verifies identity and liveness |
| **Typing and behaviour check** | Anomaly detection | Quietly verifies how the customer types and traces |
| **AI helper** | Small language model, local | Suggests extra checks in grey areas |
| **Policy and rules layer** | Deterministic | Enforces floors, reliefs and hard holds; explains decisions |
| **Review console** | Human process | Settles what automation cannot, with four-eyes approval |
| **Audit and learning loop** | Records and labels | Evidence trail; source of retraining data |

### 4.3 Design principles
Trust the proof, not the request · Match friction to danger · When unsure, fail safe · Punish attacks, not mistakes · Evidence before judgement · Don't teach the attacker · One owner per decision · Leave a trail and learn from it. (Detail in decision.md, Part 1.)

---

## 5. The AI and ML components in detail

### 5.1 The risk model

**Purpose.** For every request, estimate how likely it is to be fraud and turn that into an action level.

**Inputs.** About 38 facts, computed identically in training and in production so the two cannot disagree. Grouped:
- *Amount:* how large compared with the customer's usual and maximum payments, and the total in the last day.
- *Payee:* has this payee been paid before, how long ago, how the amount compares with what it usually receives, whether it follows a repeating schedule, and whether it is on a risk list.
- *Device and place:* whether the phone, network and location are familiar, how old the phone is to the account, whether it has a registered key.
- *Time and pace:* how unusual the hour is for this customer, the number of recent requests, time since the last one.
- *Account events:* recent SIM change, contact change or password reset; recent sensitive actions; new payees added today.
- *Recent verification history:* failed checks and suspected fake-face attempts.
- *Customer context:* amount of history, account age, how many verification methods are set up, behavioural-check score, request type, channel and segment.
- Customer **age is deliberately not an input**; age-based decisions are a fairness and data-protection risk.

**Model.** An ensemble of five gradient-boosted decision-tree models whose average is calibrated into a genuine probability. Missing information (for example, no history) is kept as "missing" and never treated as zero. For features where risk should only move one way (a larger amount, more failed checks), the model is **constrained** so it can never decide that "more risk-like" looks safer. This keeps explanations sensible and stops an attacker from lowering their score by pushing a value further in the risky direction.

**Output.** A calibrated score, a threat level from T0 to T4, and the top contributing reasons.

**Levels are set by a friction budget, not by score alone.** Each level's cut-off is chosen so that only a target share of *genuine* traffic lands there: roughly a fifth of genuine requests at or above T1, one in twenty at or above T2, one in a hundred at or above T3, and a very small fraction at T4. The bank can see the budget and change it.

| Level | Meaning | Typical checks |
|---|---|---|
| T0 | Very ordinary | Emailed code |
| T1 | Ordinary, worth a light check | Code + quiet typing check |
| T2 | Somewhat unusual | Adds a face check; scam warning on payments |
| T3 | Unusual | Adds a voice check |
| T4 | Severe | Every check the customer has |

**Training.** From a purpose-built simulator, because real labelled fraud does not yet exist: 20,000 invented customers over 150 days, about 1.2 million requests, about 12,500 of them fraud.
- 14 customer types (salaried, night-shift, student, small business, frequent traveller, expatriate, retiree helped by family, and others);
- 47 *genuine* situations that resemble fraud (new phone while travelling, genuine SIM change, a wedding, a one-off large purchase, a dormant account returning);
- 25 *attack* types (takeover, SIM swap, coached scam, remote-access scam, mule accounts, bots, card testing, slow low-value drip, bust-out, new-account fraud, and more);
- five "data feed" variants, since banks will not all supply every signal;
- training on the past and testing on the future; a simple baseline model that the new model must beat; a model card recording data, parameters and results; and an integrity manifest so a tampered model refuses to load.

**Results on the simulated future.**
- Excellent separation of fraud from genuine traffic (precision-recall about 0.95, area under the curve above 0.99), well ahead of the simple baseline.
- Calibrated probabilities (very low error).
- At the lightest step-up level the model reaches essentially all simulated fraud; at the highest it still reaches most of it, while stepping up only a small fraction of genuine traffic.
- Performance degrades gracefully when the bank omits signals (no location data, no account events, no behavioural service).

**Limits we state plainly.**
- The numbers describe the simulator. Real fraudsters adapt; real customers surprise. Retrain on real labelled outcomes before trusting them.
- Some slow, low-value and automated patterns were clearly less well caught.
- "Caught" means "triggered extra friction", not "prevented". For a coached scam the genuine customer passes every biometric check.
- **Fairness gate not passed.** In simulation, older customers (and, less so, premium customers) were stepped up on genuine requests at well above the average rate. The prototype runs this model anyway; a pilot must not (see 9 and 12).

### 5.2 Face verification

**Purpose.** Confirm the person is the enrolled customer and is physically present.

**Models (all open-licence, integrity-pinned).**
- Face detection and landmarks (YuNet) to find and align the face.
- Face recognition (SFace) producing a compact face "fingerprint" compared with the enrolled one.
- Presentation-attack detection (two MiniFASNet models) judging whether the face is live or a printed photo, screen or mask.
- A face detector (RetinaFace) used to crop for the attack models.

**How it defends.** The customer is asked for a head turn in a **random direction** so a pre-recorded video cannot anticipate it. The system checks that the same person stays in view from start to finish, that enough live frames were seen, and that the turn was real and continuous. Pictures are always fresh: a repeated frame is rejected as a replay.

**Decision handling.** The attack models act in *advisory* mode so honest customers on poor cameras are not locked out by one flag: early flags only cost a retry, and repeated flags in one request decline it and lock the account for review. Failures return the same general message regardless of cause so attackers learn nothing about which test they failed.

### 5.3 Voice verification

**Purpose.** An alternative or additional biometric for customers who cannot or will not use a camera, and an extra layer for the largest payments.

**Models.**
- Speaker recognition (ECAPA-TDNN) producing a voice fingerprint.
- Deepfake and replay detection (RawNet2, an anti-spoofing baseline from the ASVspoof research programme).
- Speech-to-text (Vosk small English model) to confirm the customer actually said the **randomly chosen words and digits** shown, which defeats pre-recorded audio.

### 5.4 Typing and behaviour check

**Purpose.** A nearly invisible second signal at low levels, and a continuing check that the person behaves like the customer.

**Approach.** The customer types a short phrase and traces a line. The system compares timing rhythm and movement shape with the enrolled pattern. An anomaly-detection model (an isolation forest) scores how unlike the customer the behaviour is. Each behavioural dimension (typing, pointer shape, session, transaction habits) is calibrated separately against its own genuine and impostor distributions, then combined carefully, since the signals are not independent. With little history the check reports "insufficient data" rather than guessing.

### 5.5 The AI helper (language model)

**Purpose.** Offer an additional opinion in grey areas.

**Model.** A small instruction-tuned language model (Qwen 2.5, 3 billion parameters, quantised) that runs on the same machine, so no customer data leaves the system.

**Safeguards.**
- It sees only coarse, structured flags (new payee, unfamiliar phone, odd hour), **never text a customer typed**, which removes the main route for prompt injection.
- Its answer must fit a fixed format and may only name checks from an allowed list.
- It can **only add** checks; if it ever tried to drop one the system treats that as an error.
- A hard time limit; on failure the system continues without it.
- It is consulted only where it can help: not at low risk, not where a relief has already judged the request ordinary, not for customers with no history.

**Role.** Experimental and minor. We deliberately do not let a language model approve, waive or lock, because it is the component most open to being talked into an error.

### 5.6 The policy and rules layer (deliberately not AI)

Responsible for the things that must be predictable and explainable:
- the **regulatory minimum** (never given up; the bank's own higher preference bends to what the customer has set up);
- **relief for ordinary traffic**: small payments, regular payees at the usual amount, new customers with too little history to judge, and optionally the lowest-value payments to existing payees;
- **shaping for very large first payments**: a scam warning and, where set up, a second biometric;
- the **short hold list** of situations that go to a person (section 6.4);
- **explanations** for the customer (general), staff (full) and the audit log.

---

## 6. Threat management

### 6.1 Threats addressed

| Threat | What it looks like | Primary defences |
|---|---|---|
| **Account takeover** | Stolen password or session used from another phone | Risk model (new phone, network, hour), face/voice/typing, trusted-device proof |
| **SIM swap** | Phone number moved to attacker's SIM, codes intercepted | Risk model weighs recent SIM change; biometrics the attacker cannot pass; hold on takeover followed by cash-out |
| **Presentation attacks** | Photo, screen, mask, recorded video or voice, deepfake | Random-direction head turn, anti-spoofing models, random spoken prompt, replay guard |
| **Remote-access and coached scams** | Genuine customer tricked or guided into paying | Scam warning on large first payments, extra care for large new payees, payee-risk signals (see 6.5 for the gap) |
| **Mule accounts** | Stolen funds moved through new payees | Payee age and risk list, new-payee velocity, hold on risk-listed payees |
| **Bots and card testing** | Many fast, small attempts | Pace and velocity features, failed-check history |
| **Credential stuffing** | Known password, step-up checks keep failing | Failed-check features, rate limits, hold on repeated failures with fake-face history |
| **Account-setting abuse** | Add payee, raise limit, change contact before cashing out | Sensitive-action features, higher check floors, confirmation at the *old* contact, takeover-then-cash-out hold |
| **Approval tampering and replay** | Changing amount or payee after approval; reusing an approval | Signed, short-lived, single-use approval bound to type, amount and payee |
| **Reviewer error or collusion** | One person approves a bad request | Two different reviewers, live-check requirement, immutable audit log |
| **Dormant-account takeover** | Long-idle account suddenly active | Dormancy feature in context; elevated checks |
| **Attacks on the AI itself** | See 6.6 | See 6.6 |

### 6.2 Defence in depth: seven layers

1. **Assess.** The risk model grades the request using the customer's own history and fraud patterns.
2. **Choose.** Policy floors and the regulatory minimum raise the checks as required; reliefs lower them only for ordinary, low-warning requests.
3. **Verify.** Biometric and behavioural checks confirm the person and that they are live.
4. **Bind.** A signed approval is tied to the exact action and works once.
5. **Escalate.** A person looks only at the specific situations automation cannot settle, with two-person approval.
6. **Contain.** Locks (system-initiated, on suspected attacks) and freezes (customer-initiated) stop damage fast.
7. **Learn.** Every decision and outcome is recorded; verdicts and fraud reports become training labels.

### 6.3 Threat-level playbook

| Level | Automated response | Human involvement |
|---|---|---|
| T0 to T1 | Code, plus a quiet behavioural check | None |
| T2 | Adds face; scam warning on payments | None |
| T3 | Adds voice where set up | None unless a hold sign is present |
| T4 | Every check the customer has | None unless a hold sign is present |
| Any, with a **hold sign** | Customer completes the checks they can | Two-person review |
| Suspected fake face or recording | Request declined, account locked | Review; a lock tied to a suspected attack **can never be approved into a payment** |

### 6.4 When a person is involved

Least human involvement is a goal, not a goal at any price. People are kept for situations that more automated checks cannot resolve, because they concern the *situation*, not the *person at the camera*:
- a payee on a risk list;
- a takeover indicator followed by money to a new payee;
- a recent suspected fake-face attempt together with repeated failed checks;
- a customer the bank has placed on a watchlist;
- a request that cannot reach even the regulatory minimum with the checks the customer has;
- a trusted device that fails to prove itself with no other check available;
- a suspected presentation attack (which also locks the account).

The hold list is intentionally short and specific, and can be widened or narrowed from review evidence. The bank can switch the whole low-touch behaviour off to restore stricter rules.

### 6.5 The scam gap, stated honestly

Biometrics prove who is at the camera, not whether they are acting freely. In a coached scam the genuine customer passes every check. Today's defences are the scam warning, extra care and a second biometric on very large first payments, and risk signals about the payee. The simulator has no inputs for a call in progress, screen-sharing or remote-control apps, or what a scam message said, so the model cannot learn them. Closing this gap is Stage 2 of the plan.

### 6.6 Threats to the AI components, and mitigations

| Threat | Mitigation in the prototype | Planned |
|---|---|---|
| **Evasion** (attacker learns what lowers the score) | Monotone constraints; customers never see scores or thresholds; uniform failure messages; relief only with clean records | Monitor for probing patterns |
| **Data poisoning** (corrupting training labels) | Customer learning only from *verified* outcomes; reviewer verdicts need two people | Label audit; outlier checks before retraining |
| **Prompt injection** | The language model never sees customer-typed text; structured, allow-listed output; add-only authority | Same discipline for any future assistant |
| **Model tampering** | Checksummed manifests; a model that fails its integrity check will not load and the system fails safe | Signed releases |
| **Replay of biometric data** | Random challenges; fresh-frame rule; replay guard on frames and audio | Wider coverage |
| **Model drift** | Decisions logged with inputs | Drift monitoring and alerts |
| **Unfair outcomes** | Age excluded as an input; fairness measured per segment and age group | Fairness as a **blocking** release gate |
| **Over-reliance on the AI** | Rules own the final decision and the regulatory floor | Reviewer feedback on AI advice |
| **Outage of a component** | Fail safe: strongest available checks, or a person if the legal minimum cannot be met | Redundancy and health alerts |

### 6.7 Incident handling

1. **Detect.** Risk level, failed or suspect checks, and security events raised by severity.
2. **Decide.** Plan produced and recorded with its reasons.
3. **Contain.** A suspected attack declines the request and locks the account; customers can freeze their own account in one tap.
4. **Review.** A review case opens with the evidence; two reviewers decide; a rejected request becomes a fraud label.
5. **Notify.** Events can be pushed to the bank's own systems through signed webhooks (authorised, denied, review required, security event).
6. **Recover.** Staff unlock a customer after examining the evidence.
7. **Learn.** The verdict, the customer's "this wasn't me" reports, and the outcome feed retraining.

---

## 7. Data, privacy and security

- **Biometric data:** the system keeps encrypted face and voice templates (compact numerical fingerprints), not photographs or recordings. Templates are encrypted at rest and bound to the customer and bank so one cannot be used for another. The bank can erase a customer's data from every service on request; a self-service "remove my biometrics" button in the customer app is not built yet (open item).
- **Contact details** (email, phone) are also encrypted at rest.
- **Minimal exposure to AI:** the language model receives coarse flags, not personal text.
- **Local processing:** all models in the prototype run on the bank's own infrastructure.
- **Auditability:** each decision stores its inputs, reasons, level, model version and policy version; entries are chained so tampering is detectable.
- **Consent** to biometric use is requested before enrolment.
- **Access control:** reviewers need a second, different reviewer; roles gate who can decide, unlock or change policy.
- **Open items:** retention periods, data-subject requests, and a formal data-protection assessment (section 11).

---

## 8. Evidence to date

| Area | What is evidenced |
|---|---|
| **Working end to end** | A full customer app (sign-up, sign-in, payments, payees, limits, contact and password changes, freeze, fraud report), the hosted check screens, the review console, and signed approvals |
| **Test coverage** | The automated suite (about 260 tests) covers decisions, reliefs, holds, locks, one-time-code behaviour, review rules and the customer journeys |
| **Real-model journeys** | A new customer's common actions were run through the real risk model: small and regular payments need only a code, a store or a TV purchase needs code plus face, the largest first payments add voice and the scam warning, and none needed a person |
| **Model quality** | Strong on simulated data; see 5.1 for what that does and does not mean |
| **Known gaps** | Real-data validation, fairness gate, coached-scam signals, whole-pipeline measurement of the low-touch changes |

---

## 9. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Model performs worse on real fraud than on the simulator | High | High | Pilot in **shadow mode** first; retrain on real labels; keep the old rules available |
| Unequal friction across customer groups | Confirmed in simulation | High | Make fairness a blocking gate; retrain with balanced data or a fairness constraint; monitor per group |
| Low-touch reliefs let more fraud through | Medium | High | Measure the whole pipeline on simulated and then real attacks; keep hold list under review; one-switch rollback |
| Coached scams not stopped by biometrics | High | High | Stage 2 signals; cooling-off and payee-name confirmation; tailored warnings |
| Attackers probe for weak patterns | Medium | Medium | Opaque failures, monotone constraints, rate limits, probing alerts |
| A language model is talked into a mistake | Low (limited authority) | Medium | Add-only, no customer text, structured output |
| Component outage | Medium | Medium | Fail-safe design, health checks |
| Licensing of some research models | Medium | Medium | Review before commercial use (see 11) |
| Customers decline biometrics | Medium | Medium | Alternatives (voice, code, typing); progressive set-up |

---

## 10. Governance and responsible AI

- **Model card for every version:** data source, size, parameters, results, limits and release decision.
- **Release gates:** must beat a baseline *and* meet the fairness bound; a failing model needs written, time-limited sign-off to run at all.
- **Shadow, then champion/challenger:** a new model runs silently beside the live one before it can act, and the old one stays one step away.
- **Human oversight:** a person decides the specific situations in 6.4; the review console records reasons.
- **Explainability:** customers get a general reason; staff and auditors get the full reasoning.
- **Monitoring:** drift in inputs, the share of genuine customers stepped up, fraud caught, and each of those by customer group.
- **Change control:** model, policy and rules versions recorded on every decision.

---

## 11. Assumptions, dependencies and open items

- **Real labelled outcomes** are needed (fraud confirmed, genuine confirmed, disputes, "this wasn't me" reports). Their volume and delay set how fast the model can improve.
- **Friction measurements** (time taken, retries, abandonment) are needed for Stage 3.
- **App-side signals** for scams (call state, screen-sharing detection, paste detection) need app support and OS permissions.
- **Payee intelligence** (registered name, merchant verification, cross-customer payee statistics) needs bank or network data.
- **Licensing review:** the language model and the voice anti-spoofing baseline are research-oriented releases; confirm terms before commercial use, and consider commercially licensed or self-trained alternatives.
- **Data-protection assessment** for biometric and behavioural data, including retention and customer-request handling.
- **Regulatory mapping:** the prototype implements a "two factors, one dynamic" minimum and an optional low-value exemption; the bank's compliance team must confirm the profile that applies.
- **Production hardening:** a production mail or SMS provider, a production database and secrets management, hardware-backed device proofs (which need a native app).

---

## 12. Plan and milestones

| Stage | Work | Outcome |
|---|---|---|
| **0. Measure** (first) | Evaluate the *whole* pipeline (model + reliefs + holds) against the simulated attacks; investigate and address the fairness result | Know what the low-touch changes cost in detection; a model that passes its own gate |
| **1. Real outcomes** | Capture verdicts, reports, disputes and abandonment as labels; build the shadow-mode retrain, compare and release routine | A model that improves from real data, with a safe way back |
| **2. Scam signals** | Add call and remote-access signals, paste and hesitation signals, payee-name confirmation, payee reputation; extend the simulator | Real coverage for coached and remote-access scams |
| **3. Cost-aware check selection** | Learn how often each check catches fraud versus fails or loses customers, per device and situation; choose the lightest set meeting the target | Fewer, better-chosen checks |
| **4. Language-model uplift** | Review assistant (advisory, shadow first), scam-specific warnings, plain-language explanations | Faster, better-informed reviews; sharper warnings |
| **5. Operate** (continuous) | Drift, fairness and fraud monitoring; model cards; champion/challenger; incident drills | A system that stays trustworthy |

Stages 0 and 1 are prerequisites for a pilot. Stage 2 is the largest protection gap. Stages 3 and 4 add efficiency and are best done once real traffic exists.

---

## 13. Success measures

| Measure | Direction | Notes |
|---|---|---|
| Fraud loss and fraud caught at the checks | Fraud caught up; losses down | Measured on confirmed outcomes |
| Share of genuine requests needing only a code | Up | The friction budget |
| Share of requests sent to staff | Down, but never at the cost of missed fraud | Reviewed alongside misses |
| Check completion and abandonment | Completion up | Per check type and device |
| Step-up rate for genuine customers by group | Within agreed bounds | Fairness gate |
| Time to resolve a held request | Down | |
| Coached-payment losses | Down | Needs Stage 2 |
| Explanation quality | Reviewers and customers can state *why* | Sampled audit |

---

## 14. Conclusion

The prototype shows that adaptive authentication is practical: a trained model judges the request, biometric and behavioural models verify the person, a language model offers a minor second opinion, and explainable rules own the final decision, with a person only where one is needed. What remains is to replace simulated learning with real outcomes, to close the fairness result and the scam gap, and to run the system the way a regulated model should be run. We propose to proceed through Stages 0 and 1 to a shadow-mode pilot, and to treat fairness and scam protection as conditions of release rather than later improvements.

---

## Appendix A. Glossary

- **Adaptive authentication:** choosing how much checking a request needs based on its risk.
- **Anomaly detection:** finding behaviour unlike a customer's usual pattern.
- **Calibration:** turning a model's raw output into a trustworthy probability.
- **Champion/challenger:** running a new model silently beside the live one to compare before switching.
- **Four-eyes:** two different people must approve.
- **Friction budget:** the target share of genuine customers who get each level of checking.
- **Liveness / presentation attack:** proof that a real person is present, versus a photo, screen, mask or recording.
- **Mule account:** an account used to receive and move stolen money.
- **Shadow mode:** a model that makes predictions that are recorded but not acted upon.
- **Threat level (T0 to T4):** the system's five-step grading of a request's danger.

## Appendix B. Component and licence summary

| Component | Model or method | Licence note |
|---|---|---|
| Risk model | Gradient-boosted tree ensemble, own training | Own |
| Face detection | YuNet | MIT |
| Face recognition | SFace | Apache-2.0 |
| Face anti-spoofing | MiniFASNet (two models) + RetinaFace crop | Apache-2.0 |
| Speaker recognition | ECAPA-TDNN (SpeechBrain) | Open; confirm terms |
| Voice anti-spoofing | RawNet2 (ASVspoof baseline) | Research release; confirm terms |
| Speech-to-text | Vosk small English | Open; confirm terms |
| Behaviour anomaly model | Isolation forest with per-dimension calibration | Own |
| AI helper | Qwen 2.5 3B Instruct, quantised | Confirm terms for commercial use |

*Licence notes are indicative; each must be verified before any commercial deployment.*
