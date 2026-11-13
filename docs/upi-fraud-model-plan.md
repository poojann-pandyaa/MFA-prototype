# Adaptive step-up for UPI payments: fraud landscape, features, build plan, deployment

Research date: 2026-11-06. Figures come from news/secondary reports found via web search, not from the primary RBI/NPCI/I4C documents. Re-check any number against the primary source before it goes in a paper, pitch or product claim.

## 0. The one design decision that matters

"Make the user re-verify that they are the same person" only stops **one** kind of fraud. Split the threat into three classes, because they need different responses:

| Class | Who presses "pay"? | Examples | Does face/biometric re-verification help? |
|---|---|---|---|
| **A. Unauthorised (account takeover)** | Fraudster | SIM swap, stolen/unlocked phone, malware/APK with SMS + accessibility access, remote-access apps (AnyDesk/TeamViewer) driven by the attacker, UPI re-registration on a new device | **Yes**: this is what step-up MFA is for |
| **B. Authorised but deceived (APP / coached)** | The real user, with the real PIN, on their own phone | Digital arrest, investment/crypto/"task" scams, fake customer care, "refund" collect requests, sextortion, loan-app extortion | **No.** The real person passes face match. Needs *scam-state detection* + friction (warnings, cooling-off, trusted contact), not identity checks |
| **C. Payee-side risk (mule / fraud account)** | n/a | Money lands in a mule account within minutes of the scam | Not a user-auth problem; it's a payee-risk score |

Reported loss mix (I4C/NCRP, 2025, via press): roughly 77% of reported losses were investment-type fraud and about 8% digital arrest. Both are class B. So if the product only does identity re-verification, it addresses a minority of rupees lost. The model should output **a threat type and a severity**, and the action ladder (section 4) maps both to a response.

## 1. Fraud landscape in India (what the model must recognise)

Scale (secondary sources, verify before citing):
- RBI Annual Report FY25: digital payment frauds (incl. UPI) were about 56.5% of banking frauds by count, with losses of about ₹520 crore ([The Logical Indian](https://thelogicalindian.com/1-in-5-indian-upi-users-hit-by-fraud-since-2022-survey-rbi-npci-launch-new-safeguards-from-june-30/)).
- UPI fraud incidents reportedly went from 7.25 lakh (FY23) to 13.42 lakh (FY24), per RBI data cited in the Lok Sabha ([summary](https://safebrowz.com/blog/upi-paytm-phonepe-google-pay-scam-india-2026)).
- National Cyber Crime Reporting Portal 2025: about ₹19,800 crore lost across 21+ lakh cheating complaints ([report](https://moneylife.in/article/fraud-alert-india-is-losing-over-22000-crore-a-year-in-cyber-scams-and-the-worst-is-yet-to-come/80612.html), [IANS](https://ianslive.in/indians-lose-over-rs-52976-crore-to-cyber-frauds-over-six-years-report--20260103154943)). Estimates elsewhere run higher; the bases differ.
- LocalCircles survey (2025, 32k respondents): 20% of UPI-using families hit since 2022, over half of victims did not report.
- Digital arrest: 1.24 lakh cases and about ₹1,935 crore lost in 2024; an estimated 45-46% of operations originate in Southeast Asia ([Inc42](https://inc42.com/buzz/indians-lost-inr-1935-cr-to-digital-arrest-scams-in-2024-govt), [The Print](https://theprint.in/india/governance/after-465-spike-in-2024-mha-data-shows-digital-arrest-scams-are-on-a-decline-in-india/2857002)).
- Roughly three of four rupees lost are never recovered, so prevention before the payment matters more than recovery.

Typology, with the behavioural fingerprint a model can actually see:

| Scam | Class | How it works | Observable signals in the payment flow |
|---|---|---|---|
| Collect-request / "refund" scam | B (looks like A) | Victim is told to approve a request to *receive* money, enters PIN, money leaves (RBI Ombudsman "BE(A)WARE" booklet, as summarised in search results; see also [Canara Bank advisory](https://canarabank.com/pages/upi-Frauds)) | Pay-on-collect to an unknown VPA, the user has never paid this VPA, request came from a non-contact, often soon after an inbound call |
| Fake QR / payment links | B | QR shown as "to receive money" authorises a debit | QR scan to new merchant/personal VPA, amount mismatch with user norm |
| Fake customer care | B | Fake helpline from search results, then remote access or collect request | Long inbound call, screen-share app active, new payee, urgency timing |
| Remote-access app takeover | A/B hybrid | AnyDesk/TeamViewer/QuickSupport installed, attacker drives the phone | Screen-share/accessibility service active, unnatural touch/typing cadence, rapid navigation |
| Malicious APK (fake bank/RTO/KYC app) | A | App reads SMS/OTP, abuses accessibility, registers UPI on attacker's device | New-device UPI registration, SMS-forwarding permission, sideloaded app list changes |
| SIM swap | A | Attacker ports number, re-binds UPI/bank app | SIM change/port event, new device binding, first txns within hours at large amounts |
| Digital arrest / fake agency | B | Hours-long video call posing as CBI/ED/police; victim moves savings to "safe account" | Active call or video call in progress, large amount far above norm, first-time payee, drained balance, repeated transfers, user hesitation/typing pauses |
| Investment / crypto / "task" / part-time job | B | Small early "profits" then escalating deposits | Escalating repeat payments to a series of new payees, Telegram/WhatsApp sourced, payee categories (crypto, gaming), same-VPA sequence of rising amounts |
| Loan-app / sextortion | B | Extortion payments under threat | Many small-to-medium payments to unfamiliar payees in a short window, late-night, distress patterns |
| Mule payee layer | C | Funds hop through rented accounts within minutes | Payee account new, high inflow/outflow velocity, many unrelated senders, cash-out soon after |

Regulatory and ecosystem context you must design around, not duplicate:
- RBI **Authentication Mechanisms for Digital Payment Transactions Directions, 2025** (issued 25 Sep 2025, effective 1 Apr 2026): two-factor baseline, and issuers may add **risk-based additional checks** (value, channel, beneficiary/customer risk, geolocation, behaviour). SMS OTP is not discontinued ([RBI press release](https://website.rbi.org.in/as/web/rbi/-/press-releases/rbi-issues-directions-on-framework-on-authentication-mechanisms-for-digital-payment-transactions), [Business Standard](https://www.business-standard.com/amp/finance/news/rbi-two-factor-authentication-digital-payments-guidelines-2026-125092501154_1.html)). This is the regulatory opening for exactly your idea. I only saw secondary summaries; read the directions' text for exemptions.
- RBIH **MuleHunter.AI** (payee/mule scoring, reported at 26-29 banks) ([Business Standard](https://www.business-standard.com/amp/finance/personal-finance/explained-rbi-has-a-new-ai-tool-mulehunter-ai-to-reduce-digital-frauds-124120900250_1.html)), NPCI's bank-facing fraud monitoring, and DoT's Financial Fraud Risk Indicator (FRI) that RBI told institutions to integrate ([Inc42](https://inc42.com/buzz/rbi-building-ai-system-to-flag-risky-online-payments)). Your model should **consume** FRI/mule signals as features, not rebuild them.
- Since 30 Jun 2025 UPI apps show only the bank-registered beneficiary name (kills payee-name spoofing).
- UPI Circle (delegated payments, ₹5,000 per-txn / ₹15,000 monthly caps for full delegation) is a ready-made rail for a "trusted contact approves" step ([Razorpay](https://razorpay.com/blog/what-is-upi-circle/)). Sources disagree on the 24-hour cooling-period limit; check the NPCI circular.
- Two claims I could not verify and left out of the design: NPCI discontinuing P2P collect requests (Oct 2025), and any first-time-payee limit.

## 2. Data characteristics needed (per user, per transaction, per payee)

Everything below needs a lawful basis and consent under the DPDP Act 2023; prefer on-device computation for phone-side signals and send only derived scores/flags upstream.

**A. Transaction (the request being scored)**
amount, amount/user-p95 ratio, amount/available-balance ratio (draining), time of day/day of week, channel (collect, pay, QR, intent), payee type (VPA, merchant category code, mobile-number-based), remark text, and the time since the user opened the app.

**B. User baseline (rolling 30/90/365-day profile)**
txn count and value distributions, usual payee set, usual hours, usual cities, typical merchant categories, balance and inflow patterns, tenure and age of account, historical dispute/fraud flags, KYC tier.

**C. Payee**
first-time payee for this user, payee age on the platform, number of distinct payers in the last 24 h, inbound/outbound velocity, share of inbound from unrelated users, complaint count against the VPA, presence on mule/suspect lists, FRI band of the payee's mobile number, bank/IFSC risk, the chain "same payee after recent inbound credit".

**D. Device and session**
device ID and binding age, **new device or new UPI registration**, SIM change/port/swap recency (from telco/DoT signal or SIM-serial change), OS integrity (root/jailbreak, emulator, Play Integrity verdict), VPN/proxy, app install source, **screen-sharing/remote-access app running**, accessibility service abuse, overlay/screen-capture detection, SMS-forwarding or notification-listener permissions, active-call state (a call in progress while paying is a top coaching signal; only the *flag* and duration, never audio or numbers), call from an unknown number shortly before, location vs. home/work, IP geography vs. GPS mismatch.

**E. Behavioural biometrics (passive)**
typing cadence, touch pressure/swipe velocity, hesitation before amount entry, copy-paste of VPA/amount (dictated or pasted amounts are suspicious), navigation speed, tilt/hand-hold. Compare against the user's own enrolled pattern.

**F. Sequence and graph context**
last N actions before the pay screen (e.g. "view balance, view history, add payee, pay" in 90 s), repeated payments of rising amounts to new payees, shared device/IP/payee across many accounts (graph features).

**G. Labels (the hard part)**
NCRP/1930 complaints (matched to the transaction), bank-confirmed fraud tickets, chargeback/dispute outcomes, mule-list confirmations, analyst decisions, and **negative labels from challenge outcomes** (user passed step-up, or abandoned, or reported). Labels arrive days to weeks late and are biased towards what was already caught. Plan for this.

## 3. How to build and train it

You won't get India UPI fraud labels from public data. Plan around that.

**Phase 0: Rules + baseline (weeks 1-2).** Hard rules for the worst combinations: new device + large amount, remote-access app active, call in progress + first-time payee + high amount, SIM change < 72 h. This is also your benchmark and your fallback if the ML service is down.

**Phase 1: Per-user anomaly layer (weeks 2-4).** Robust z-scores / quantile bands on amount, hour, payee novelty; Isolation Forest or a small autoencoder over the user-baseline features. Handles cold start (new users fall back to population-segment baselines). Good at class A, weak at class B because coached payments are "anomalous" only mildly.

**Phase 2: Supervised model (weeks 4-8).** Gradient-boosted trees (XGBoost/LightGBM), which is what the fraud literature mostly uses on tabular transaction data ([e.g. IEEE-CIS/PaySim comparisons](https://arxiv.org/pdf/2505.04204v1)). Multi-class target: `{legit, unauthorised, coached-scam, mule-payee}` so the output drives the right response. Handle imbalance (fraud is a tiny fraction of UPI volume) with class weights and threshold tuning rather than only SMOTE; calibrate probabilities (isotonic/Platt) because the thresholds map to friction levels.

Training data options, in order of usefulness:
1. A partner bank/PSP's historical data with fraud labels (best, needs an agreement and data-protection controls).
2. Your own **scenario simulator**: generate normal UPI behaviour per synthetic user, then inject each scam typology in section 1 with its fingerprint. PaySim ([Kaggle](https://www.kaggle.com/datasets/ealaxi/paysim1)) is a synthetic mobile-money log; it lacks India-specific signals and some high scores on it are known to come from leaky balance columns, so use it only for pipeline smoke tests. IEEE-CIS is card e-commerce, useful for model-engineering practice, not for UPI behaviour.
3. Public/Government statistics to *calibrate the simulator's base rates and typologies*, not as training rows.

**Phase 3: Payee/graph layer (later).** Graph features (degree, fan-in/out, shortest path to known mules, shared devices) via a feature store; GNN only if gradient-boosted trees on graph features plateau. Ingest MuleHunter/FRI scores if you're a regulated entity with access.

**Phase 4: Scam-context signals (later).** On-device classifier for scam messages/links and for fake-support apps, plus the call-in-progress/remote-access flags. This is where class-B recall comes from. Vendors report that active-call and behavioural signals help against coached payments, but those are vendor-reported trial numbers ([LexisNexis](https://risk.lexisnexis.co.uk/insights-resources/article/how-machine-learning-and-intelligent-insights-are-accelerating-the-uks-fight-against-app-scams), [BioCatch](https://www.biocatch.com/blog/fpc-faster-payments-fraud-trends)).

**Evaluation rules (don't skip):**
- **Time-based splits only** (train on earlier weeks, test on later). Random splits leak and overstate results.
- Primary metric: **recall at a fixed friction budget** (e.g. "challenge at most 1-2% of transactions"), plus PR-AUC. Accuracy is meaningless at this imbalance.
- Value-weighted recall (rupees saved), not just count.
- Report false-positive cost explicitly: every wrongly challenged payment is a user who may switch apps.
- Shadow mode first (score, don't act), then 1-5% canary, with a holdout group so you can measure real prevented loss.
- Retrain on a schedule and monitor drift; scammers change scripts quickly. Add adversarial tests (what happens if the attacker keeps amounts just under the threshold).
- Explainability: return top reason codes (SHAP) per decision. Needed for the user prompt ("you are paying a new person while on a call") and for audit.

**Latency:** UPI is real-time, so scoring must be fast. My engineering estimate is a budget of tens of milliseconds for the model call, with features precomputed in a store (Redis-style) and a rules fallback on timeout. Confirm the exact limit with whichever network or bank you integrate with.

## 4. Response ladder (this is the "adaptive MFA" part)

Risk score x threat type -> action. Escalate friction with severity, and match the action to the class:

| Severity | Class A (takeover) | Class B (coached/deceived) | Class C (bad payee) |
|---|---|---|---|
| Low | Allow | Allow | Allow |
| Medium | Silent device biometric / app lock re-prompt | **Contextual warning** naming the likely scam ("Are you on a call with someone who asked you to pay? Government agencies never ask for payment on a call") with a forced 5-10 s read, then confirm | Show payee-risk badge, ask to confirm payee |
| High | Face + liveness challenge (the existing step-2 in this repo), or OTP to a pre-registered second channel | **Cooling-off hold** (e.g. 30 min to 24 h on first-time-payee large amount, user can cancel), **trusted-contact approval** (UPI Circle-style), or call-back from the bank | Hold + payee verification, add to review queue |
| Critical | Block, lock UPI binding, notify user on a second channel | Block + offer one-tap report (1930 / NCRP) | Block, report payee to bank for freezing |

Why this matters: a face check on a scam victim is a speed bump the scammer will coach the victim through ("now do the verification we told you about"). Cooling-off, warnings that interrupt the script, and a second human (trusted contact) work against class B. Identity checks work against class A. Also keep a **fixed random challenge set larger than 3** and rate-limit retries (the README already flags this weakness for the current liveness step).

Fit with RBI's 2025 authentication directions: your risk-based step-up is an "additional factor" on top of the mandatory two factors, and does **not** replace the UPI PIN.

## 5. Where to implement and the best course of action

Who can see what:

| Layer | Sees | Good at | Barrier |
|---|---|---|---|
| **On-device (inside a UPI app / SDK)** | Device, call state, remote-access apps, behavioural biometrics, local messages (with consent) | Class A and the B-class context signals, instant friction UI | Need to be, or partner with, a TPAP (PhonePe, Google Pay, Paytm, etc.) |
| **PSP / TPAP backend** | That app's full user history across banks | User-baseline anomaly, payee velocity | Only their own users |
| **Issuer / remitter bank** | Account balance and history, debit decision | Last-mile hold/block, comply with RBI directions | Regulated, slow integration |
| **NPCI switch** | All UPI traffic, cross-bank payee graph | Mule rings, network-wide payee risk | Not open to outsiders |
| **Telecom (DoT FRI, SIM data)** | SIM swaps, call patterns | SIM-swap and spam-call signals | Government/telco access |

**Recommendation for you (an independent builder, not a bank):**
1. **Build it as a risk-scoring service + SDK-style client** that a PSP or bank would embed. You cannot change the UPI PIN flow or NPCI's switch; you can sit *before* the PIN screen in a cooperating app.
2. **Reuse this repo.** It already has the pieces for the class-A branch: a two-step login with a risk score, device binding, single-use session IDs, a randomised blink/head-turn liveness challenge, PAD anti-spoofing and ArcFace matching. I only read the README, not `services/auth.py`, so I haven't checked how rich the current risk calculation is; the README says it uses known device and login time. The next increment: add a `/txn/risk` endpoint that takes the transaction + device context, returns `{severity, threat_type, reasons, action}`, and have the frontend call `ChallengeCameraCapture` only when `action == "face_challenge"`.
3. **Build a demo with a simulated UPI app** (payee list, collect requests, simulated call/remote-access flags) and your scenario generator, show shadow-mode metrics at a fixed challenge rate, and show the response ladder per scam type. That is a credible research/hackathon/pitch artifact.
4. **To go real**, partner with a TPAP or a small bank/fintech in the RBI regulatory sandbox, start in shadow mode, and keep scores explainable. The 1 Apr 2026 directions make "risk-based additional authentication" an expected capability, so there is a buyer need.
5. **Where not to compete:** mule-account scoring (RBIH MuleHunter.AI already exists) and network-wide graph analysis (NPCI's job). Consume their outputs.

## 6. Risks and honest limits
- No model will reach high recall on class B at a tolerable false-positive rate using only transaction data; call-state, remote-access and behavioural signals plus human-in-the-loop friction are what make it work.
- Label scarcity and delay mean early metrics from simulated data will overstate real performance.
- Privacy: call-state, device telemetry and behavioural biometrics are sensitive. Minimise, process on-device, document purpose and retention.
- Accessibility/inclusion: face-based challenges fail for some users (disability, low-end cameras, lighting). Always provide a non-face fallback.
- Adversary adaptation: publish no thresholds; vary challenges; rate-limit.
