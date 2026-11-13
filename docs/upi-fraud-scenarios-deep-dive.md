# Deep dive: scam scenarios and the regulatory frame

Companion to `upi-fraud-model-plan.md`. Research date 2026-11-06. Sources are news/secondary reports unless marked RBI. **Anything labelled "hypothesis" is my inference about what a payment flow would show; I found no published transaction-level data to confirm it.** Those hypotheses are what the simulator in `backend/tools/fraud_sim.py` encodes, so they are the first thing to validate against real labelled data.

## Part 1: How to read each scenario

For every scam: the attacker's stages, where the *payment system* gets a window to act, the signals that window offers, what a legitimate lookalike is (the false-positive risk), and the right response. The recurring lesson: **the attacker's job is to remove the victim's doubt before the pay screen, so the useful interventions are the ones that re-introduce doubt or delay, not extra identity proof.**

### S1. Collect-request / "refund" scam (class B, looks like A)
- **Stages:** (1) victim sells something online or expects a refund; (2) scammer says "I'm sending money, approve the request"; (3) a *collect* (pull) request arrives; (4) victim enters the UPI PIN believing it receives money; (5) money leaves.
- **Core fact:** a PIN is only ever needed to *send* money. The RBI Ombudsman booklet makes this point (reported in search results; I did not retrieve the booklet itself).
- **Window:** the instant a collect request is shown. This is the best-defined intervention point in the whole list.
- **Signals:** channel = collect, requester not a saved contact, requester never paid or was paid by this user, amount ≥ typical, request arrives right after an inbound call or chat.
- **Lookalike:** legit collect requests exist (merchant/utility, friends splitting a bill). Contact status and prior history separate them.
- **Response:** a full-screen "you are about to SEND money, not receive it" warning at medium; hold at high. A face check is useless here, the victim is the real user.
- **Caveat:** one blog reported NPCI discontinued person-to-person collect in October 2025. I could not confirm it. If true, this scenario shrinks and merchant/QR variants dominate.

### S2. Fake QR / payment links (class B)
- **Stages:** scammer sends a QR "to receive" payment; scanning it opens a pay screen; victim approves.
- **Window:** pay screen after scan.
- **Signals:** QR/link source is a chat app, payee is a personal VPA while the context claims to be a merchant, amount matches nothing in the user's history, first-time payee.
- **Lookalike:** ordinary P2P QR payments.
- **Response:** show the *bank-registered beneficiary name* prominently (NPCI rule since 30 Jun 2025), warn on scan-from-chat plus new payee.

### S3. Fake customer care (class B, can escalate to A)
- **Stages:** victim searches a helpline number, gets a fake one; "agent" asks for details or pushes a "refund" collect request or a remote-access app; funds leave.
- **Window:** (a) when the remote-access app starts, (b) pay screen while a call is active.
- **Signals:** call in progress with an unsaved number, screen-sharing/remote-access app active, new payee or collect request, long session before payment.
- **Response:** if remote access is active, block payments and show why (see S4). Otherwise call-in-progress warning.

### S4. Remote-access app takeover (hybrid A/B)
- **Stages:** victim installs AnyDesk/TeamViewer-type app (the RBI alerted on this in Feb 2019), reads out the code, attacker watches and drives the screen. The victim may enter the PIN themselves while "being guided", or the attacker navigates and the victim only supplies the PIN.
- **Window:** the instant a screen-sharing or remote-control session is active during any payment.
- **Signals:** screen-share/remote-control active (device signal), accessibility-service anomalies, overlay/capture detection, abnormal input cadence (non-human taps/typing), concurrent call.
- **Lookalike:** a user screen-sharing with family for help; rare during a payment.
- **Response:** this is the strongest single signal. High severity on its own; block at critical when combined with a new payee or large amount.
- **Spoofing:** all these signals come from the device. An attacker who owns the device can lie, which is why they must come from an attested SDK (Play Integrity-style), not from client JSON. The demo endpoint takes them as plain request fields; do not treat that as secure.

### S5. Malicious APK (fake traffic fine, wedding invite, KYC/bank app) (class A)
- **Stages:** APK arrives over WhatsApp/SMS; permissions granted; malware reads SMS/OTPs, can register the victim's UPI account on another device. CloudSEK's "Digital Lutera" report (March 2026) describes UPI registered on a different device while the SIM stays in the victim's phone, with a reported ₹30 lakh moved over two days in a single group. This is one vendor's report; I did not see the full text.
- **Window:** (a) the **device-binding event**: a UPI registration for this account on a device that is not the user's usual one, (b) the first payments from that binding.
- **Signals:** new device binding age under 24 h, binding event while the original device is active and online, sideloaded app installed shortly before, SMS-forwarding or notification-listener permission, first outgoing payments large and to new payees, odd hours.
- **Lookalike:** user genuinely changed phones. Separate by whether the old device is still active and whether the first payments look like the user's history.
- **Response:** step-up with a face + liveness check on the *binding*, and a limit on first-day outgoing value. This is exactly what the existing face-challenge in this repo is good at.

### S6. SIM swap (class A)
- **Stages:** attacker ports the number, re-binds UPI/banking app, drains account.
- **Window:** SIM-change/port event (telco signal) and the next rebind.
- **Signals:** SIM serial change or port within 72 h, new device, payments to new payees soon after.
- **Response:** face challenge plus a hold on large first-time payees; notify on a second channel (email/in-app on the old device).
- **Dependency:** needs a telecom signal. Without one, this reduces to "new device".

### S7. Digital arrest / fake agency (class B)
- **Stages (I4C's CEO as quoted in press):** select victims and gather personal information; impersonate CBI/police/etc. with uniforms and staged backgrounds; induce fear; keep the victim on prolonged video calls under "interrogation", isolated; extort payments to "settle" or move money to a "supervision/safe account". One legal blog says 56 hours of isolation; I could not verify that.
- **Scale:** about 1.24 lakh cases and ₹1,935 crore in 2024; press reports say the government has blocked tens of thousands of WhatsApp/Skype accounts and the Supreme Court ordered a CBI-led probe (Dec 2025).
- **Window:** payment screens during a long-running call, especially large transfers draining savings; later payments after an earlier warning.
- **Signals (hypotheses):** call/video call active for a long duration, amount far above norm, balance drain ratio high, new payee (often a current/"supervision" account), several large payments within hours, user hesitation or long idle then rapid entry, device unusually quiet (no other app use).
- **Lookalike:** buying a house/car, a large legitimate transfer while on a call with the seller or bank. Mitigation: ask a verification question rather than only block.
- **Response:** the best intervention is a scripted interrupt: "No government agency will ask you to pay or move money during a call. Hang up." Then a cooling-off hold and a **trusted-contact notification/approval**. A face check here only trains the victim to follow instructions. At critical, block and offer the one-tap report (1930).

### S8. Investment / crypto / "prepaid task" / part-time job (class B)
- **Stages:** contact via WhatsApp/Telegram; small early payouts (a Pune case: ₹150 for simple tasks) build trust; "prepaid tasks" and "investment" tiers escalate; withdrawals are blocked unless more is paid (I4C advisory: processing fees, membership upgrades); one case saw ₹5.7 lakh sent over three days. Targets skew to retirees, women and unemployed youth. Money flows into mule accounts and is converted to crypto.
- **Window:** the *escalation pattern*, not any single payment.
- **Signals (hypotheses):** a series of payments to different new payees within 24-72 h with rising amounts, payee categories (unknown individual, crypto on-ramp, gaming), no call but chat-app-sourced payee, user's own history has no investment payments.
- **Lookalike:** real investors, SIPs, trading apps. Merchant category and prior history separate.
- **Response:** sequence-aware warning on the 2nd-3rd new-payee payment: "You have paid 3 new people in 24 hours; this is a common pattern in task/investment scams." Cooling-off for the next. This is the scenario where a per-transaction model fails and a *sequence* feature is essential.

### S9. Loan-app and sextortion extortion (class B)
- **Stages:** predatory loan app harvests contacts/photos; threats follow; victim pays repeatedly.
- **Window:** repeated medium payments to unfamiliar payees in a short window, late night.
- **Signals (hypotheses):** several small-to-medium payments to new payees, odd hours, repetition.
- **Response:** gentle, non-accusatory support screen with helpline/report options; do not block (the victim is in distress and may escalate elsewhere). This one needs product care more than model accuracy.

### S10. Mule payee layer (class C)
- **What it is:** receiving accounts rented or opened by others; funds hop on within minutes. RBIH's MuleHunter.AI was built after analysing 19 mule-activity patterns with banks.
- **Signals the app can compute itself:** payee account new to the platform, many distinct unrelated payers in 24 h (a fan-in), complaints against the VPA.
- **Signals to consume:** DoT FRI band for the payee's mobile number; MuleHunter score if you are a bank with access.
- **Response:** badge at medium, hold-and-verify at high, block at very-high FRI.

## Part 2: Regulatory frame, read closely

### 2.1 RBI Authentication Mechanisms for Digital Payment Transactions Directions, 2025
Read from the RBI notification text (RBI/2025-26/79) in this session, not from summaries:
- **6(a):** at least two distinct factors for digital payments unless exempted. **6(b):** for non-card-present, at least one factor must be dynamic (unique to the transaction). **6(c):** compromising one factor must not weaken the other.
- **8 (Risk-based approach):** issuers *may* check behavioural/contextual parameters (location, user behaviour, device attributes, transaction history) and apply additional checks beyond the two-factor minimum; the clause also says issuers may explore DigiLocker to notify and confirm high-risk transactions. **It is permissive, not mandatory.**
- **9.1:** issuer must ensure the robustness and integrity of the mechanism. **9.3:** comply with the DPDP Act 2023.
- **9.2:** if a loss arises from transactions processed *without complying with these directions*, the issuer must compensate the customer in full "without demur". This is a liability for non-compliance with the authentication rules, **not a general reimbursement of scam losses**. So a class B scam where the real user authenticated correctly is not covered by this clause. Do not market the product as shifting that liability.
- **Annexure-1 exemptions** include small-value contactless cards, recurring e-mandates after the first, some prepaid instruments, NETC, small-value offline payments, and **tap-and-pay UPI at NFC POS**. The UPI PIN plus device binding is the existing baseline; this model adds checks on top of it (clause 8) and never replaces the PIN.
- **10:** cross-border card-not-present timelines (Oct 1, 2026); the directions state they do not apply to cross-border digital payment transactions otherwise.
- **Implication for design:** the model is a legitimate implementation of clause 8, and the "additional check" can be any factor, including biometrics, device-based credentials or a delay. Because clause 8 is optional, adoption will be driven by fraud-loss reduction and reputation, not by a compliance deadline.

### 2.2 DoT Financial Fraud Risk Indicator (FRI)
- **What:** a per-mobile-number risk label launched May 2025 inside DoT's Digital Intelligence Platform: **Medium, High, Very High**.
- **Inputs (reported):** NCRP complaints (I4C), DoT's Chakshu platform, and inputs from banks and financial institutions. Scoring criteria are described only at a high level; there is no public documentation of how scoring works or how to contest it.
- **Who consumes it:** RBI directed all scheduled commercial, small finance, payments and co-operative banks on 30 Jun 2025 to incorporate it. PhonePe, Paytm and Google Pay (over 90% of UPI) are reported to be integrating DIP alerts. PhonePe has said it is working on a proactive warning for the lowest tier.
- **Claim:** DoT says it helped prevent over ₹5,000 crore of suspected fraud in 15 months. This is a government figure; I found no independent check.
- **How this project uses it:** `payee_fri` is a request field mapped to `none/medium/high/very_high`. Medium adds payee-risk evidence, high triggers hold-and-verify, very-high blocks. In production, a bank or PSP gets it from DoT; an independent builder cannot, so the field is a stub.

### 2.3 RBIH MuleHunter.AI
- AI model for scoring mule accounts, built from 19 mule-behaviour patterns with several banks, reported at 26-29 banks. It uses transaction data, customer profile and cross-bank "ecosystem" learning. No independent performance metrics found.
- **Use:** a payee-side score for banks. We do not rebuild it; the payee-risk branch accepts a score if available and otherwise falls back to the simple platform-local signals (payee age, fan-in).
- **One unverified source** (`rbimulehunterai.pages.dev`) claims detailed internals including an I4C Suspect Registry cross-check. It is not clearly an official RBI document; I did not use it.

### 2.4 What this means for scope
| Capability | Who owns it | What this project does |
|---|---|---|
| Payee mobile-number risk | DoT FRI | Accept a band as input |
| Payee account mule score | RBIH / bank | Accept a score as input; local fan-in fallback |
| Transaction + device + behaviour scoring and the response ladder | Issuer/PSP (clause 8) | **Build this** |
| Network-wide ring detection | NPCI | Out of scope |
| Compensation for authorised scams | No clause covers it | Do not claim it |

## Part 3: What to validate first (honest unknowns)
1. Whether coached-payment signals (call state, long session, hesitation) actually separate victims from legitimate large payers on real data. Today they are hypotheses.
2. Real base rates. The simulator uses assumed rates; do not read its precision numbers as real-world precision.
3. Whether warnings and cooling-offs reduce completion of scam payments. Evidence in the search results is vendor-reported only; plan an A/B with a holdout.
4. Whether NPCI actually discontinued P2P collect (affects S1).

## Part 4: Scope decision and what was built

Scope narrowed to **class A (account takeover) only**; the coached-scam (B) and payee-risk (C) work is parked. The rule scorer in `services/txn_risk.py` and `tools/fraud_sim.py` still contain the B/C logic but `/txn/risk` no longer uses it; it survives as the baseline the ML model is compared against.

The takeover model is a trained gradient-boosted-tree model with its own pipeline in `backend/ato/`: see `docs/ato-model-report.md` for design, results and limits.
