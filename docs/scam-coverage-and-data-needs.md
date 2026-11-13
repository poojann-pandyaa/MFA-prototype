# Scam coverage, edge-case steps, and the user information we need

Status: plan. Prototype scope: **all data is simulated by us**; nothing here is measured on real users. Detectability ratings are my engineering judgement about what a payment flow can show, not test results, unless marked "built + measured on simulated data".

## 0. How to describe this product honestly

"Prevents all types of scams" cannot be supported. No system does it, and a prototype trained on simulated data cannot show it. Claims like that (or accuracy numbers from simulated data) could be treated as misleading advertising and would fall apart in the first bank due-diligence. Some scams leave no trace in the payment at all (a person knowingly paying someone they trust for a false story).

What we can truthfully say, and it is still strong:
- "AI that scores every payment in real time for takeover, being-coached and bad-payee risk, and decides on its own what protection to apply."
- "Covers N named UPI scam patterns" (list below), "demonstrated on simulated scenarios".
- "Works without storing the user's private payment history on our servers" (only once that is actually built, see plan).
- Avoid: "all scams", "99% accurate", "RBI approved", "eliminates fraud".

The demo must show a visible "SIMULATED DATA" label on any metric.

## 1. Scams the model can predict, and what it does

Detectability: **Strong** = several distinct signals, **Partial** = some signal, easy to confuse with normal, **Weak** = looks like an ordinary payment.

### Takeover (someone else is paying)
| # | Scam | How the model sees it | Action | Detectability |
|---|---|---|---|---|
| 1 | SIM swap | SIM changed recently, new device, PIN reset, new payees, odd hour | Face + liveness, or block | Strong (built + measured on simulated data) |
| 2 | UPI moved to attacker's phone (malicious APK, OTP phishing) | New binding while old phone still online, recent app sideload, PIN reset, young payee | Face + liveness, or block | Strong (built + measured) |
| 3 | Remote-access takeover | Remote-control app active, non-human touch pattern, fast login-to-pay | Block or face + liveness | Strong (built + measured) |
| 4 | Stolen or unlocked phone | Touch pattern differs from owner, failed PIN attempts, location jump | Fingerprint/face, face + liveness | Partial to strong (built + measured) |

### Victim is tricked into paying (the real user is paying)
| # | Scam | How the model sees it | Action | Detectability |
|---|---|---|---|---|
| 5 | Digital arrest (fake police/CBI) | Long call in progress, very large amount, balance draining, first-time payee | Interrupt message, wait, trusted-contact approval, block | Strong if call-state is available |
| 6 | Fake customer care / refund | Call in progress, screen-share, collect request, new payee | Block if screen-share; otherwise warning + wait | Strong with call and screen-share signals |
| 7 | "Pay to receive" tricks: collect request, fake QR, marketplace buyer scam | Collect request from a non-contact, QR to personal VPA, amount off-pattern | "You are SENDING money" screen; hold | Strong for collect requests; Partial for QR |
| 8 | Investment / crypto / task-job scam | Several new payees in 72h, rising amounts to the same payee, crypto/gaming payee | Warning, then wait, then block | Partial to strong (needs the sequence) |
| 9 | Loan-app and sextortion extortion | Repeated payments to unfamiliar payees, late night | Support screen with helpline; no hard block | Partial |
| 10 | Prize, KYC-expiry, bill-disconnection "pay a fee" | Small to medium payment to a new young payee, urgency timing | Warning with the "banks never ask this" message | Partial |
| 11 | "Relative in trouble" / impersonation on chat | New payee, amount unusual, short time after opening chat app | Warning; optional trusted-contact check | Weak |
| 12 | Romance, matrimonial, gift scams | Gradual payments to the same new payee over weeks | Pattern warning after repeated rising payments | Weak |

### Payee side (the receiving account is the problem)
| # | Scam | How the model sees it | Action | Detectability |
|---|---|---|---|---|
| 13 | Mule account | Young account, many unrelated senders in 24h, government fraud-risk (FRI) flag | Badge, hold, block | Strong where FRI/mule data exists; simulated for the prototype |
| 14 | Look-alike payee (VPA one letter off a known payee, wrong-person payment) | New payee whose id is very close to a payee the user already pays | "Did you mean X?" prompt | Strong, cheap to compute on the phone |

**Cannot be stopped from payment data:** a calm, informed user paying a trusted-looking person for a false story, with no unusual device, amount or timing. The model can only reduce harm (warning, pattern after repeats).

## 2. Steps the model takes for every payment

1. **Collect** only the minimum signals (section 3), computed on the phone, as ratios, flags and counts.
2. **Score** three risks: takeover, being coached, bad payee.
3. **Combine** into a severity (low, medium, high, critical) using friction budgets so ordinary users are rarely bothered.
4. **Choose the action** from the user's enrolled methods and their protection mode (standard, assisted, guardian-linked):
   - low: nothing; medium: prompt or warning; high: strong check, wait or trusted-contact approval; critical: block and offer a report (helpline 1930).
5. **Apply, log the outcome** (passed, failed, abandoned, user cancelled), keep counts only, and retrain/re-threshold from outcomes.

## 3. Normal and edge cases, with the step for each

| Case | Step the system takes |
|---|---|
| Ordinary payment to a known person | Nothing |
| First payment to a normal new shop or friend | Nothing unless other signals pile up |
| Genuine phone upgrade (old phone works) | Old phone approves new one (vouched). Low friction, normal limits grow |
| Old phone lost/broken | Recovery: face + liveness, low limits that rise, short wait for first large payment to a brand-new payee, one-tap freeze available |
| Attacker claims "I changed phone" | Same recovery path; adding trusted contacts/second device is delayed |
| User enrolled in few methods | Pick the best enrolled method; if none is strong enough, lower limits + wait + trusted contact instead of blocking |
| Old or low-end phone, no genuine-app proof | Use the server-only model; lower limits; never block just for this |
| New user, no history | Use cohort baseline and low limits that grow with clean use |
| Travel, VPN | Location treated as a weak signal |
| Big planned payment (wedding, fees, emergency) | Strong check on the spot rather than a wait |
| Blind, face-covered, tremor | Never require face; use second device or trusted contact |
| Feature-phone user | Server-side checks only; call-back confirmation (integration parked) |
| Shared family phone | **Parked**: can't reliably tell a family member from a thief |
| Too many prompts to one user | Cap prompts per month; if the user passes everything, relax |

**Where age matters.** Age is **not** a model input. It affects *mode defaults* that the user or a family member can change, because scams target age groups differently and people can complete different checks:

| Group | Scams they meet most | Protection mode defaults |
|---|---|---|
| Under 18 | Gaming/top-up scams, extortion, fake job tasks | Guardian-linked account: small limits, guardian approves new payees and large payments (needs consent under India's data protection law; delegation rails are parked) |
| 18-30 | Task/investment scams, loan apps, marketplace tricks, phone-change false alarms | Silent checks, passkeys, vouched phone transfer; sequence warnings for repeated new payees |
| 30-55 | Customer-care/refund, QR tricks, bill-disconnection | Standard mode; month-end/festival baselines |
| 60+ | Digital arrest, fake customer care, relative-in-trouble | Assisted mode: stronger interrupt messages, trusted-family approval for new large payees, longer challenge windows, bigger text, local-language voice prompts, choice of face OR fingerprint OR family approval |

## 4. What information of the user we need

Everything is derived on the phone as a flag, ratio or count. The server keeps only what's marked "server".

**Tier 1: absolute minimum. The first version is built on these only.**

| Information | Form used | Catches | Where it lives |
|---|---|---|---|
| Payment amount | Ratio to the user's usual and to balance | 1-14 | Phone (ratio sent) |
| Is the payee already known to this user | Yes/no | 1-14 | Phone |
| Payee age on the platform and number of other senders in 24h | Numbers | 13, 2, 8 | Server (aggregate counts per payee) |
| Time of payment | Hour, compared to the user's usual hours | 1-4, 9 | Phone |
| Is this device already known; age of the UPI link to this device | Yes/no, hours | 1, 2 | Server |
| SIM changed recently | Hours since | 1, 2 | Telecom/bank via server (simulated in prototype) |
| UPI PIN reset recently | Hours since | 1, 2 | Server |
| Recent payment counts (1h, 24h), new payees in 72h, outflow vs balance | Counts and ratio | 5, 8, 9, 12 | Phone |
| Remote-control or screen-share active | Yes/no | 3, 6 | Phone SDK |
| Phone call in progress (flag and minutes only, never numbers or audio) | Yes/no, minutes | 5, 6, 11 | Phone SDK |
| Payment type: pay, collect request, QR; is requester a contact | Type, yes/no | 7, 6 | Phone |
| Payee id is very close to a known payee | Yes/no | 14 | Phone |

**Tier 2: strongly useful, add next.**
Touch/typing similarity score; failed PIN attempts; IP-based distance from usual place; login-to-pay time; app installed from outside the store recently; phone rooted or emulated; overlay or accessibility abuse.

**Tier 3: nice to have.**
Payee category (crypto/gaming/merchant); government fraud-risk flag (FRI) and mule score; enrolled methods; trusted contact; user-chosen protection mode; planned-payment note.

**Never collect.** Message or chat content, contact lists, call audio or numbers, photos, Aadhaar or ID numbers, precise location history, payee names, age or other personal traits as a model input.

In our simulator's feature-removal test (takeover model only), dropping the payee group cost the most (PR-AUC 0.931 to 0.853) followed by on-device signals (0.860). That test is on simulated data, so it indicates which signals matter in our simulation, not in real life.

## 5. Building it with data we make ourselves

- **Scenario library:** one generator per scam in section 1 plus normal and edge personas (the age modes above, phone switch, new user, travel, few methods, no SDK). Each scenario has a story (the steps the scammer takes) so the demo can replay it.
- **Honesty rule for synthetic data:** we write both the scam and the detector, so results partly confirm our own assumptions. Keep a "hard mode" (stealthy scammers, noisy signals, missing signals) and report it next to the easy mode. Label everything simulated.
- **Demo ("Scenario Lab", in the existing React frontend):** pick a persona and a scam; watch the timeline of what the phone sees; see the model's score, the reason in plain words, and the exact screen the user gets (warning, face challenge, wait, block). A second tab replays the genuine-phone-switch and partial-enrolment cases. A scoreboard tab shows simulated results with their confidence ranges and a limits panel.
- **Gates before calling a scam "covered":** each scam type has a scenario, a test, and a detection rate at a fixed false-alarm budget, in easy and hard mode.

## 6. Order of work

1. Add the coached-scam and look-alike-payee signals (Tier 1 call state, collect type, similarity) to the features and the simulator.
2. Retrain one combined model (takeover + coached + payee) at fixed false-alarm budgets; keep the takeover results as the regression bar.
3. Policy layer with enrolled methods, protection modes, wait and trusted-contact approval.
4. Phone-switch flow (vouched transfer, recovery, freeze).
5. Scenario Lab demo with simulated-data labels.
6. Optional: move features and history to the browser to show the privacy design.
