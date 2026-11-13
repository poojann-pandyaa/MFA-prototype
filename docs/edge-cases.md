# Edge cases for the takeover model and its authentication tools

Scope: account takeover (class A) only, with the privacy rule "no private data stored on our side; per-user learning stays on the phone".

How to read the tags: **[sourced]** = backed by a source found in this session (links at the bottom, mostly news/bank pages, not NPCI/RBI primary text). **[reasoned]** = my own engineering reasoning or general knowledge, not verified in this session. **[measured]** = from the synthetic-data report `docs/ato-model-report.md` (so it describes the simulator, not real users).

## 1. The genuine phone switch (the case you raised)

What really happens: moving UPI to a new phone or SIM means the app is reinstalled and the user registers again; the old registration is deactivated **[sourced]**. Banks cap spending in the first period after a new registration, commonly quoted as ₹5,000, but the window and amounts differ by bank and by Android/iPhone, and one quoted figure contradicted the rest **[sourced, conflicting]**. Device binding is rate limited (Jio quotes 3 attempts a day) **[sourced]**.

Why it hurts our model: a new phone looks exactly like the start of a takeover (new device, fresh binding, PIN reset, maybe a new SIM). In the simulator, genuine phone changes are the **largest source of false friction**: 9.1% of those payments got the face challenge and 23% got at least a prompt, versus 0.3% across normal payments **[measured]**. Also, because we keep the habit profile on the phone, a new phone starts with no profile **[reasoned]**.

| Sub-case | What to do |
|---|---|
| Old phone still works (upgrade) | **Vouched transfer**: the old phone approves the new one (biometric on the old phone, plus QR/Bluetooth pairing). A vouched binding tells the model "the owner did this" and cancels most of the takeover signal. Carry the habit profile across in the same step. |
| Old phone lost, broken, or sold | **Recovery path**: face + liveness, bank/debit-card step, OTP to the registered number, then a **ramp**: low limits for 24-72h that grow with each clean payment, notification to the old channels (email, trusted contact), and a short delay for the first large payment to a brand-new payee. Normal payments to people the user already pays stay easy. |
| New phone and new SIM together (or eSIM, dual-SIM slot swap) | Treat as the recovery path, not as an attack. Two events at once are common for genuine users **[reasoned]**; the model already sees them in the simulator (5% of users replaced a SIM, 15% changed phone). |
| Repair shop / factory reset on the same phone | Same device key still exists, so no new binding is needed; only re-enter the PIN. Do not count it as a new device if the hardware key survives. |
| User is challenged on **every** payment after the switch | Add a **"trust credit"**: once one strong step-up passes on the new device, drop the extra friction for the next 72h unless something else changes (very large amount, remote access appears). Today the build scores each payment alone, so this is missing. |
| Attacker claims "I just changed phone" | The recovery path is the new weak point. Keep it strong: needs something only the owner has (old device, card, face), delays the biggest payments, and alerts the old channels. Adding a new trusted contact or second device **during** a recovery window must itself be delayed. |

Privacy-compatible way to move the profile: end-to-end encrypted backup or direct phone-to-phone transfer, with the key held by the user. We never see the contents.

## 2. Users who have only some authentication methods

Principle: **separate "how risky is this" from "how do we challenge".** The model outputs risk only. A second layer (the policy) picks the cheapest challenge that the user can actually do and that is strong enough for that risk.

| Situation | What to do |
|---|---|
| User has only phone screen lock | Allowed for low/medium risk. For high risk, add compensating controls instead of a block: lower limit for new payees, a short wait, or a trusted-contact approval. |
| No second device, no trusted contact | Offer the wait-then-confirm path and a call-back from the bank for large payments. |
| User refuses face scan | Fingerprint, passkey, or second-device approval instead. Never make one method the only way. |
| Phone cannot be attested (old/low-end Android, custom ROM) **[reasoned]** | Use the `server_only` model and tighter limits. Do not block: many genuine users are in this group. |
| Model accuracy when signals are missing | In the simulator the full model still caught 98.5% of incidents when all on-device signals were missing, and the server-only model 97.8% **[measured]**. But a missing field I forgot to send earlier made a genuine payment look slightly risky, because the model had rarely seen it missing. **Fix:** train with **random "enrolment masks"** so the model sees every realistic combination of missing signals, and add a test that dropping optional signals moves the score only slightly. Absence of an optional signal must not count as risk. |

**Make mandatory (small, hard-to-argue list):** (1) the app proves it is genuine (no user effort); (2) a screen lock on the phone; (3) one recovery or out-of-band contact (a second device, a trusted contact, or a call-back number). Everything else (passkey, face, behavioural check, watch/Wi-Fi proximity) stays optional, with higher limits as a reward for enrolling more. Users with fewer methods get lower first-time-payee limits, not a worse model.

## 3. Age groups

Do **not** feed age into the model **[reasoned]**: it is sensitive, may be wrong or unavailable, and invites unfair treatment. Instead offer **opt-in modes** the user or a family member chooses, and cap friction by what the person can actually complete.

| Group | Known facts | Risks for our design | What to do |
|---|---|---|---|
| Under 18 | The DPDP Act sets 18 as the age of a child and requires verifiable parental consent before processing a child's data **[sourced]**; how to verify is still unsettled in the rules **[sourced]**. UPI Circle lets a parent delegate with caps (₹5,000 per payment, ₹15,000 per month for full delegation, per press) **[sourced]**. Face recognition makes more errors at the youngest and oldest ages **[sourced]**. | Behavioural profiles and face data of minors need consent; face matching is less reliable. | Guardian-linked, delegated accounts with small limits and guardian approval. Do not build a behavioural profile without the guardian's consent. Avoid face as the only factor. |
| 18-30 | Change phones often, travel, use VPNs, are heavy night users **[reasoned]** | Most genuine "weird" behaviour comes from here (phone changes, VPN, sideloaded apps). | Silent checks, passkeys, vouched phone transfer. Keep friction very low. |
| 30-55 | Baseline pattern **[reasoned]** | Month-end salary, festival, family-event spikes. | Seasonal baselines (see section 4). |
| 60+ | Seniors filed about 103,488 cyber-fraud complaints in 2025 with about ₹4,005 crore lost (government reply, as reported); in Nagpur nearly 60% of victims were seniors **[sourced]**. A "45% of seniors can't spot fraud" figure circulates but I could not find its method **[unverified]**. | Fingerprints can be worn or hard to read (commonly reported, **not verified here**); face matching errors rise with age **[sourced]**; timed blink/head-turn tests are hard with tremor or slowness; family often helps by using the phone, which looks like "a different user" to a behavioural check **[measured: 'other user' lookalike got the face challenge 1.1% of the time]**. | **Assisted mode** (opt-in): longer timeouts, bigger text and voice prompts in the local language, choice of face OR fingerprint OR family approval, a **trusted family contact** as approver, and lower default limits for new payees that the user can raise. Do not rely on the user reading out any code. |

## 4. Other edge cases

| Edge case | Why it breaks things | What to do |
|---|---|---|
| **Accessibility**: blind or low-vision users, facial differences, face coverings, tremor | The blink/head-turn check is visual. RBI has issued accessibility guidance for payments, and a 2024 study found real screen-reader problems in major UPI apps **[sourced]**. | Every challenge needs a non-visual and a non-face alternative (fingerprint, passkey, second-device approval, trusted contact). Test with a screen reader. |
| **Feature-phone users** (UPI123Pay by IVR, missed call, sound-based; estimates range from hundreds of millions of feature-phone users) **[sourced, estimates vary]** | No app, no device signals. | Server-side model only (the `server_only` variant), telecom SIM-change checks, call-back confirmation, low caps. |
| **Shared family phone**, joint accounts, power of attorney | A second genuine user looks like a takeover. | Per-account profile on the phone; explicit "delegate" mode; do not punish a low behavioural match alone. |
| **Dual SIM, roaming, NRIs with foreign numbers, long trips** | Distance-from-home and IP-country look suspicious. | Opt-in "travel mode", roaming signal from the telecom, treat location as a weak signal. Already 2.4% of travel payments got a prompt in the simulator **[measured]**. |
| **Big genuine payments** (wedding, tuition, deposit, medical emergency) | Look like draining. | Let users **pre-announce** a planned payment. For emergencies, use a strong on-the-spot check, not a wait. |
| **Seasonal bursts** (Diwali, month-start, new job) | New payees and higher amounts at once. | Track recent-trend baselines; relax first-day-of-month and festival weeks slightly. |
| **New users / cold start** | No history to compare against. | Use the cohort model plus low limits that rise with clean use. About 10-15% of simulated users were in this state. |
| **Bad network, slow camera, step-up timeout** | A failed challenge looks like a failed attacker. | Longer retry windows; a failed challenge due to timeout is not a fraud signal; decide in advance what happens when the scorer is unreachable (small payments allowed, large ones held). |
| **Changed face or fingers** (injury, ageing, beard, glasses) | Genuine users locked out. | Re-enrolment path through the recovery flow; never lock without an alternative. |
| **Lost or stolen phone** | The genuine owner needs to freeze fast; a thief has the phone. | One-tap "freeze my UPI" from another device or the bank, plus a call-in route. Biometric (not PIN) for medium-risk payments. |
| **Attackers abusing our own exceptions** ("I'm elderly", "I changed phone", "I only have a PIN") | Every easy path is an attack path. | Easy paths get **lower limits and ramps**, not lower checks. Adding trusted contacts or new methods is itself a delayed, high-risk event. |
| **Alert fatigue** | Users who are challenged too often start bypassing or disabling. | Cap challenges per user per month; if a user passes every challenge for weeks, relax; track how many users abandon after a challenge. |
| **Unequal error rates between groups** | Some groups (older, low-end phones, certain regions) may be flagged more. | Monitor challenge rate, pass rate, abandonment and appeals per cohort using opt-in audit panels and coarse, consented cohorts. Do not store age in the model. |
| **Privacy vs recovery** | On-device profiles vanish with the phone. | End-to-end encrypted backup or direct transfer; fall back to cohort baseline if neither exists. |

## 5. What to build first (my recommendation)

1. **Policy layer**: assurance levels, enrolled-methods picker, compensating controls. Biggest accuracy-neutral win.
2. **Vouched transfer + recovery path + trust credit + limit ramp.** Targets the largest measured false-friction group.
3. **Train with enrolment masks and add the "optional signal missing" test.**
4. **Accessibility and assisted-mode alternatives** for every challenge.
5. **Per-cohort monitoring** without storing age in the model.

## Sources

- UPI re-registration, limits, device binding: [Paytm guide](https://paytm.com/blog/payments/upi/recovering-upi-access-sim-block-step-by-step/), [Jio FAQ on binding attempts](https://www.jio.com/help/faq/mobile/payments/upi-on-myjio/how-many-times-can-i-attempt-device-binding-in-a-day), [IOB UPI FAQ](https://www.iob.bank.in/en/faq-on-upi), [HDFC terms (news summary)](https://taxconcept.net/business-economy/hdfc-bank-revises-upi-terms-and-conditions/)
- Seniors: [Moneylife](https://moneylife.in/article/fraud-alert-why-seniors-are-in-the-crosshairs/80241.html), [Storyboard18 (45% figure, unverified)](https://www.storyboard18.com/digital/45-of-indian-seniors-struggle-to-identify-online-fraud-cybercrimes-on-the-rise-58331.htm), [YourStory](https://yourstory.com/2026/01/how-age-tech-can-solve-digital-fraud-targeting-seniors)
- Feature phones: [Business Today on UPI by IVR](https://www.businesstoday.in/amp/personal-finance/top-story/story/heres-how-you-can-make-upi-payments-without-internet-using-ivr-385223-2023-06-12), [Fibe guide](https://www.fibe.in/blogs/what-is-upi-123-pay/)
- Minors and consent: [S.S. Rana](https://ssrana.in/articles/ensuring-child-safety-under-the-dpdpa/), [Bar & Bench on draft rules](https://www.barandbench.com/law-firms/view-point/childs-personal-data-and-privacy-analysing-the-draft-dpdp-rules-2025)
- Face recognition and age: [NIST FRVT part 3 summary](https://www.exploreaiethics.com/reports/face-recognition-vendor-test-frvt-part-3-demographic-effects/), [arXiv 1911.06396](https://arxiv.org/pdf/1911.06396)
- Accessibility: [Inc42 on RBI accessibility guidance](https://inc42.com/buzz/make-pos-devices-accessible-to-persons-with-disabilities-rbi-to-fintechs), [IEEE Access 2024 study](https://doaj.org/article/9c55f65ecb864c5ab1ead14e43c5c18b)
- UPI Circle limits: [Razorpay](https://razorpay.com/blog/what-is-upi-circle/)

Searches that came back empty and are therefore open: fingerprint wear in older or manual-labour users, shared family phones, and the exact lost-phone UPI blocking steps.
