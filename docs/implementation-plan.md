# Implementation plan: only what we can build here AND check

Scope: account-takeover model (class A). Everything below is either (a) buildable in this repo (FastAPI backend, React web frontend, the `backend/ato/` simulator and trainer) with a test or a measured number that can fail, or (b) left **empty** on purpose because it needs things we don't have (a native phone app, telecom/bank access, real fraud data).

Honest limit: every number we can check is on **synthetic data**. The checks prove the mechanisms work and that nothing got worse. They do not prove real-world accuracy. Real accuracy needs a shadow-mode pilot (Phase 4 prepares for it).

## 1. Cases: what we do, how we check

A blank "How we tackle" cell means: parked, not forgotten. The last column says why.

| # | Case | Today | How we tackle | How we check | Why parked (blank rows only) |
|---|---|---|---|---|---|
| 1 | Genuine phone upgrade, old phone still works | Worst false-friction group: 9.1% get the face challenge, 23% get a prompt [measured] | **Vouched transfer**: old device approves the new one; model gets a `binding_vouched` input (Phase 2) | Phone-change face-challenge rate drops to 2% or less; incident catch drops by no more than 1.5 points; rerun with attackers allowed to vouch 20% of the time | |
| 2 | Genuine phone switch, old phone lost/broken | Same false friction | **Recovery path**: face + liveness, then low limits that ramp up, a short wait for the first large payment to a brand-new payee (Phase 2) | Unit tests on the ramp and wait; simulated worst-case loss per attack stays under the cap | Card/bank check, OTP and alerts to old channels need bank/telecom rails |
| 3 | User keeps getting challenged after the switch | Each payment is scored alone | **Trust credit**: one passed strong check lowers friction for 72h (`stepup_passed_hours` input) | Same gates as case 1, plus a run where attackers pass the check 2% of the time | |
| 4 | Attacker says "I just changed phone" | Not modelled | Vouching needs biometric on the old phone; adding a trusted contact or second device during recovery is delayed (Phase 2) | The attacker-vouch run above; unit test that new methods can't be added during a recovery window | |
| 5 | Owner loses phone, needs to freeze fast | No freeze | **Freeze endpoint** callable from another session; all payments blocked until recovery (Phase 2) | Endpoint tests: frozen means blocked, unfreeze needs the recovery path | One-tap freeze from the bank or by calling in needs bank systems |
| 6 | User enrolled in only some methods | One fixed face challenge | **Policy layer**: pick the cheapest enrolled method that reaches the required strength; fall back to limits + wait when none does (Phase 1) | Test over every combination of enrolled methods and every severity: always a valid plan, never an unenrolled method; model score identical whatever the user enrolled | |
| 7 | Mandatory minimum | Face only | Require face + one of (second device, trusted contact) to get full limits; otherwise a lower-limit account (Phase 1) | Enrolment tests; limits tests | Whether to force this on real users is a product decision |
| 8 | Signals missing (older phone, no SDK, no IP location) | Model catches 98.5% with all device signals gone, but a forgotten field once raised a normal payment to a prompt | **Per-user signal profiles in the simulator**, retrain, add a gate that missing optional signals must not raise flags (Phase 3) | Per-segment report: false alarms for a segment at most 1.5× the overall rate; catch at least 95% | |
| 9 | Blind, face-covered, tremor users | Face challenge only | `no_face` preference: policy never picks face; uses second device or trusted contact (Phase 1, 5) | Policy test: `no_face` never returns face; every enrolled-alternative combination still gives a plan | A blind-friendly screen-reader flow in the web UI needs real user testing |
| 10 | Seniors | Nothing special | Opt-in `assisted` mode: longer capture window, prefer trusted-family approval, lower new-payee cap (Phase 5) | Policy tests; capture settings stay within `LIVENESS_MAX_FRAMES`; no age used anywhere | Whether longer windows keep liveness accuracy for older faces needs real data |
| 11 | Cold-start (new) users | About 12% of simulated users | Cohort baselines already used; add a cold-start segment to the report and lower limits for them (Phase 3) | Segment gate (same as case 8) | |
| 12 | Too many challenges | Not capped | Cap prompts (not face challenges) per user per month (Phase 1) | Policy test; false-alarm report shows no rise | |
| 13 | Unequal error rates between groups | Not measured | Per-cohort report in the simulator (device tier, signal profile, cold start) and aggregate challenge counts in production (Phase 4) | The report itself; counts-only monitor test | Age/region audits need consented real users |
| 14 | Model and thresholds go stale | Drift function exists, not wired | Aggregate feature histograms + PSI monitor, outcome logging (Phase 4) | Monitor test on shifted synthetic data | |
| 15 | Real data arrives | Only the simulator | **Loader for real labelled history** with a CSV schema (Phase 4) | Round trip: export synthetic data to CSV, load it back, same metrics | |
| 16 | Travel, VPN, large one-off payments, new payees | 0.2%, 0.1%, 0.3%, 0.2% face-challenge rate [measured] | Nothing. Already within the 0.3% budget | Existing report | |
| 17 | Legit screen-sharing flagged | 3.9% face-challenge rate | | | Needs real telemetry to tell help-desk screen share from an attack |
| 18 | Planned big payments, seasonal bursts | Within budget | | | Low payoff, and a pre-announce flow adds an attack path |
| 19 | Shared family phone, joint accounts | Other-user lookalike: 1.1% face challenge | | | Can't tell a family member from a thief without real data |
| 20 | Moving the habit profile to a new phone privately | Server keeps history today | | | Needs a native app with secure key storage for encrypted backup |
| 21 | On-device matching of face/fingerprint/typing | Server stores face data | | | Needs a native app and the OS biometric API |
| 22 | Real device-genuine proof (Play Integrity / App Attest) | Telemetry comes from request JSON | | | Needs a native app and Google/Apple setup |
| 23 | SIM-swap and number checks from the telecom | Request field only | | | Needs operator access |
| 24 | Feature-phone / IVR users | Server-only variant exists | | | Needs telecom/NPCI integration |
| 25 | Minors and guardian accounts | Not handled | | | Needs bank delegation rails and legal review of consent |
| 26 | Fingerprint wear, ageing faces, changed faces | Not testable | | | Needs real biometric data |
| 27 | Learning across users without seeing their data | Not done | | | Federated learning with noise is a large project of its own |
| 28 | Bank call-back, NFC card tap, hardware keys | Not done | | | Operations or hardware, not code |

## 2. Build order

Each phase ends with a check that can fail. Do not start the next phase until the gates in section 3 pass.

**Phase 0: freeze the baseline (small)**
Save today's numbers (catch 99.3%, rupees saved 99.7%, face challenges on 0.2% of legit payments, server-only catch 97.8%, segment numbers from the report) to a file. Add a test that fails if the shipped model card drops below the gates in section 3. Everything after this is compared to it.

**Phase 1: policy layer (medium)**
- `ato/policy.py`: a pure function from (severity, enrolled methods, preferences) to a plan: which method, what limits, what wait, what fallback.
- Methods we can really build and test: face + liveness (exists), approval from a second logged-in device, approval from a trusted-contact account, a wait. Phone fingerprint and similar are client-side, so the server can't verify them; keep them as soft prompts and say so.
- New tables: enrolled methods, account freeze/preferences. New endpoint for the second device or contact to approve.
- Check: the exhaustive test (case 6, 9, 12). Plus a simulated enrolment mix to report how many high-risk payments get a strong method versus the fallback.

**Phase 2: phone switch, the biggest measured gain (medium to large)**
- Simulator: genuine phone changes are vouched 60% of the time (an assumption), attackers vouch rarely (5% for stolen phones, 0 otherwise) and pass a step-up 2% of the time. Add `binding_vouched` and `stepup_passed_hours`. Retrain.
- Policy: limit ramp after a new binding (vouched binding gets more room), freeze endpoint, delay on adding methods during recovery.
- Check: gates 1-4 in section 3, plus a stress run where attackers vouch 20% of the time. If the stress run drops catch more than 3 points, tighten vouching (require biometric on the old phone) before shipping.

**Phase 3: missing-signal robustness (medium)**
- Simulator: each user gets a fixed signal profile (some without the SDK, some without attestation, some without IP location) instead of random per-payment dropout.
- Report catch and false-alarm rates per profile and for cold-start users. Retrain until the segment gates pass.

**Phase 4: real-data and monitoring readiness (small to medium)**
- `ato/ingest.py` plus a documented CSV schema, so a bank's labelled history trains the same pipeline.
- Log challenge outcomes (passed, failed, abandoned) and keep counts and feature histograms only, not per-user rows. A small monitor reports challenge rates and PSI.
- Check: the CSV round trip and a monitor test on shifted data.

**Phase 5: accessibility and assisted mode (small)**
- `no_face` and `assisted` preferences feed only the policy; the model never sees them.
- Check: policy tests and capture settings within the existing limits.

**Phase 6, optional and only if privacy is a hard requirement now (large)**
Port the featurizer to JavaScript, keep each user's history in the browser (a stand-in for a phone), and send only the computed features. Server stops storing payees and amounts. Check: golden-vector parity between Python and JavaScript on thousands of cases. The web demo is not a phone app, so this only simulates the real design.

## 3. Gates (all on synthetic data; a phase fails if any gate fails)

| Gate | Threshold |
|---|---|
| Legit payments getting face challenge (overall) | at most 0.3% |
| Attack incidents caught at face-challenge level | at least 97% (today 99.3%) |
| Rupees saved | at least 97% (today 99.7%) |
| Server-only model catch | at least 95% (today 97.8%) |
| Genuine phone-change face-challenge rate | at most 2% (today 9.1%) |
| Per-segment false alarms | at most 1.5× overall |
| Per-segment catch | at least 95% |
| Stress run, attackers vouch 20% | catch drops at most 3 points |
| Model score vs user enrolment | identical |

## 4. What we are deliberately not doing

- No age, gender, region or religion as a model input.
- No voice recognition.
- No loosening of checks for "easy" users; easy paths get lower limits and delays instead.
- No claims about real-world accuracy until shadow-mode data exists.
