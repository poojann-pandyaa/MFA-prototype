# Decisions and the logic behind them

This document explains **why the system is built the way it is**. It has three parts:

1. **Principles**: the handful of beliefs that every choice came back to.
2. **Feature by feature**: for each feature, the problem, how we thought about it, the options we weighed, the flow we chose and *why the steps are in that order*, what we gave up, and how our thinking changed along the way.
3. **Decision log**: the short, numbered record of every significant choice, in the order it was made.

There is no code here. For *how things work* (the walkthrough), see [flow.md](flow.md). If a decision is reversed later, don't delete it; add a new entry that says so.

---

# Part 1. Principles

These came up again and again. When two options looked equal, the one that fit these won.

1. **Trust the proof, not the request.** A request is just a claim. The bank acts only when it holds a signed proof, from a system it trusts, that this exact action was verified. Everything else follows from this.
2. **Match friction to danger.** Checking everything heavily trains customers to click through; checking nothing invites theft. The effort asked of the customer should grow with the real danger of the request.
3. **When unsure, fail safe.** If the risk model is down, a check can't run, or the evidence is incomplete, the system takes the cautious route (more checks, or a person looks), never the permissive one.
4. **Punish attacks, not mistakes.** An honest customer who mistypes a code or has a bad camera must never be treated like a thief. Locks are for suspected attacks only.
5. **Evidence before judgement.** Whenever a person has to decide, make sure the real customer has already proved they're present, so the person is deciding on facts.
6. **Don't teach the attacker.** Customers get friendly, general reasons. Detailed scores and thresholds are for staff only, and failures are worded the same way whatever caused them.
7. **Make the safe path the easy path.** Protection that is annoying gets bypassed. So set-up is progressive, check screens look like the bank, and the common case needs little effort.
8. **One owner per decision.** The bank decides *what* needs approving; AMFA decides *how* it is approved; people decide only what software shouldn't. No rule lives in two places.
9. **Leave a trail, and learn from it.** Every decision and outcome is recorded, and confirmed fraud or genuine outcomes feed back into the model.

---

# Part 2. Feature by feature

## 2.1 Opening an account and setting up protection

**The problem.** Strong security needs strong proof of who the customer is, but the first minutes in a new app are when people quit. If we ask for everything up front (code, face, voice, typing, authenticator app), most people won't finish.

**How we thought about it.** What is the *least* we need to protect a brand-new account safely? Two different kinds of proof: something they receive (a code sent to their email) and something they are (their face). Voice, typing and an authenticator app make protection stronger but aren't needed on day one.

**Options we weighed.**
- *Everything up front*: strongest from the first minute, but high drop-off.
- *Nothing up front, add later*: easy, but the first sensitive action would have no proof to compare against.
- *Progressive (chosen)*: the minimum now, with more methods offered later as optional upgrades.

**The flow we chose.** Sign up with name, mobile, **email**, username and password → the email code is ready immediately → a quick face setup → start banking. Voice, typing and an authenticator can be added in Security whenever the customer likes. The Security screen shows a "protection level" so the customer can see what each extra method buys them.

**Why this order.** The email goes in at sign-up because codes can't be sent without it. The face comes straight after because it is the strongest proof we can set up in under a minute, and it is needed for the riskier actions later. The trusted phone is registered automatically at this point because the customer has just proved themselves on it.

**What we gave up.** New accounts have fewer ways to prove themselves, so on risky actions they get asked for more, or get held more often, than a fully set-up customer. We accept this and let the Security screen sell the upgrade ("more ways means fewer stops").

**How it changed.** The first version asked for the phone number and sent SMS codes. We later added the email as the main place codes go (see 2.6), so the sign-up form grew one field and the setup screen now says plainly where codes will be sent.

---

## 2.2 Who decides what: the life of a request

**The problem.** A bank app has many sensitive actions (pay, add a payee, change a limit, and so on). If each action has its own ad-hoc security logic, rules drift apart and gaps appear.

**How we thought about it.** Treat every sensitive action the same way: the bank *describes* the action, a central security service *judges and verifies* it, and the bank *acts* only on the result. The bank knows its business (balances, payees, limits); the security service knows risk and verification.

**The flow we chose.** Customer asks → bank does its own common-sense checks → bank describes the action to AMFA → AMFA decides the risk and the checks → customer does the checks → AMFA issues a signed approval → bank verifies the approval matches and acts once.

**Why this order.**
- *The bank's own checks come first* (frozen account, unverified phone, insufficient balance, over the daily limit, unknown payee) because it's pointless, and annoying, to make someone complete face and code checks for something the bank would refuse anyway.
- *AMFA is asked only after those pass*, so security effort is spent only on requests that could actually go ahead.
- *The bank acts last and only on the proof*, so even if something earlier is bypassed or buggy, money still can't move without a valid approval.

**What we gave up.** Every sensitive action involves a round trip to AMFA, and the bank must understand AMFA's approval. In return, there is one place where security rules live.

---

## 2.3 Deciding how much checking a request needs

**The problem.** What is "enough" checking? For a ₹200 bill to a familiar biller, a code is plenty. For a large transfer to a brand-new payee from a new phone at 3am, it isn't.

**How we thought about it.** Three different voices should be heard, and the strictest should win:
1. **A risk model**, which compares this request with the customer's own history and with known fraud patterns. It is good at spotting the *unusual*.
2. **The bank's policy**, which says that certain actions are sensitive in themselves, whatever the model says (changing a password, raising a limit, verifying a new phone).
3. **Regulation**, which sets a floor of at least two kinds of proof, at least one of them live (a code sent now, or a face or voice).

The model alone isn't enough, because a model can be wrong or be fooled. Rules alone aren't enough, because they can't see subtle patterns. Regulation can't be negotiated. Taking the strictest of the three means no single weak voice can lower protection.

**The flow we chose.** The model grades the request on a scale from T0 (very ordinary) to T4 (severe). Each grade maps to a set of checks, from just the code up to everything. Policy and regulation then raise (never lower) the set if it falls short of their floors. Checks the customer can't do are replaced or escalated (see 2.5).

**An optional AI helper, with a one-way rule.** The platform can optionally let an AI assistant suggest *extra* checks. The rule is that it may only **add** checks, never remove any the rules already committed. If it ever tried to drop one, the system treats that as an error. This lets us experiment with smarter suggestions without any risk of weakening protection. In the demo this helper is switched off.

**What if the model can't give an answer?** Principle 3: fail safe. No model, a broken model, or missing information means the request is treated as severe and goes to review, not waved through.

**What we gave up.** Some calm requests on sensitive actions get more checks than their risk score alone would need. That is the price of the policy floors.

---

## 2.4 Keeping small, normal payments light

**The problem.** If every small payment asks for a face scan, customers either abandon the app or learn to tap through security prompts, which undermines the prompts that matter.

**How we thought about it.** A tiny, ordinary payment to someone the customer pays often doesn't represent much risk, *as long as nothing else looks wrong*. So allow a lighter level for those, with strict conditions.

**The flow we chose.** For payments in the bank's lowest amount band, the lighter level (a code plus the quiet typing-behaviour check) is allowed only when **all** of these hold: the model had asked for more than that light level, the amount is not unusually large for this customer, and none of the "hard warning signs" is present (recent security incidents, a flagged payee, recent changes to account details, a burst of new payees, and similar).

**Why these conditions.** The relief has to be earned. A small amount is not safe by itself, because attackers like small test payments, so any real warning sign cancels it. Actions that aren't payments (adding a payee, changing a setting) never get it, because their danger isn't about the amount.

**What we gave up.** A patient attacker could try to stay small and look normal. The daily limit bounds the damage, and the warning signs are designed to trip on the behaviour that usually comes before real fraud.

---

## 2.5 When a needed check isn't available

**The problem.** The system may call for a face check, but the customer hasn't set up their face, or their camera is blocked, or a check service is down.

**How we thought about it.** There are three honest answers: use an equivalent, ask for a different but similarly strong check, or involve a person. What is not honest is quietly skipping.

**The flow we chose.**
1. Use a **stand-in** where one exists: face and voice cover for each other, and typing can be covered by face or voice.
2. If there is no stand-in and the risk is **high**, the request goes to **review** after the customer does the checks they can.
3. If the risk is lower, the system proceeds with what is available, as long as the regulatory floor is still met.
4. If even the floor can't be met, the request goes to review.

**Why.** Substituting keeps most customers moving; escalating only at high risk keeps the cost of a missing check proportionate. Never skipping keeps the system honest.

**What we gave up.** Thin accounts (only email and face) hit review more often at high risk. That is exactly why Security encourages adding more methods.

---

## 2.6 The email code

**The problem.** We needed a "something you receive" proof that works without special hardware or a paid SMS service, and that tells the customer *what* they're approving.

**How we thought about it.** Email is available to everyone, free, and leaves a record. SMS is more familiar but needs a paid provider. An authenticator app is strongest but needs set-up first. Since the face and other checks carry the heavier weight, email is a fine basic code.

**The flow we chose.** At sign-up the customer gives their email; codes go there. Each message names the **amount and payee**, so a customer can notice if what they're approving isn't what they intended ("what you see is what you sign"). Older accounts with only a phone number continue to get SMS until they add an email, and changing the email updates where codes go. Changing the email is itself confirmed at the **old** address, so a thief who gets into the app can't quietly redirect codes to themselves.

**The code's behaviour, and why.**
- **A live countdown for the code's full life.** The timer used to freeze because it started before its screen was shown; it also showed a shorter time than the email promised. Both were fixed so the screen tells the truth.
- **A wrong code allows another try with the same code.** Before, every typo triggered a *new* email, which was confusing, because the first email still worked, and it silently used up the allowance of sends.
- **Resend is available after a short wait**, and sending a fresh code cancels the older ones. The wait and the cap on codes per request stop anyone from flooding the customer's inbox or guessing endlessly. Cancelling old codes means only one valid code exists at a time.
- **Too many wrong tries decline the request, but never lock the account** (Principle 4).

**What we gave up.** Email is weaker than a hardware-backed method. That's accepted because it is never used alone, and the risk levels pile other checks on top when danger rises.

---

## 2.7 The face check

**The problem.** A face check must tell a real, live person from a photo, a screen, or a recording, and must also be comfortable for genuine customers on ordinary phones in ordinary light.

**How we thought about it.** Two separate questions: *is it the right person?* (identity) and *is it a live person right now?* (liveness). Liveness is checked by asking the person to turn their head in a direction chosen at random, so a pre-recorded video can't predict it, and by confirming that the same person is in view from start to finish.

**What went wrong first, and what we learned.** Early testing showed three real-world problems:
- **Not enough time to turn left or right.** The turn step felt rushed. We gave it more time.
- **A slow phone camera repeated the same picture.** The system read repeated pictures as a replay attack and locked a genuine user. We changed capture so it only ever sends *fresh* pictures, and if the camera stalls it says so clearly instead of sending repeats.
- **The liveness detector missed real faces** because it looked at too small a version of the picture. We let it look at a larger one.

**Why only a third strike locks.** Liveness warnings are *advisory* signals, and genuine customers can trip them with bad light or a stalled camera. So the first two only cost a retry. A third in the same request is treated as a real attack: the request is declined, the account locked, and the dev team alerted. This balances protecting against actual attackers with not punishing honest people.

**Why all face failures read the same.** Telling someone "you failed liveness but your identity matched" teaches them which half to fix. A single, general message gives nothing away.

**What we gave up.** The check takes a little longer than a snapshot would, and attackers get a couple of retries per request. Both are bounded.

---

## 2.8 Voice and typing behaviour

**The problem.** The face needs a camera; some customers can't or won't use one. And a pure biometric at every step is heavy.

**How we thought about it.** Give the system more kinds of proof so it can choose the lightest one that is strong enough.
- **Voice:** the customer reads randomly chosen words and digits, so a recording can't be replayed.
- **Typing behaviour:** a quiet check of *how* someone types a short phrase and traces a line. It is cheap and almost invisible, so it is used as the light second check at low risk levels.

**Why these two.** They sit at different points on the effort scale: typing is nearly free, face is quick, voice is heavier. Having a ladder lets the system climb it as risk rises.

---

## 2.9 Showing the check screens inside the bank app

**The problem.** Sending a customer to an unfamiliar website to do a security check looks like phishing, and the jump feels slow and disjointed.

**How we thought about it.** The security vendor's pages should be a *guest* in the bank's house. They appear full-screen, in the bank's brand colour, with the amount and payee always visible at the top, and they report their progress back to the app so it can react.

**Why not a plain redirect.** A redirect loses the sense of one product and worsens the trust problem. Embedding keeps the customer in context.

**The safeguards.** The bank app can only embed AMFA's pages from approved addresses, and AMFA only talks back to approved addresses, so a malicious site can't impersonate either side.

**What we gave up.** Some extra care in connecting the two, and a few extra rules about which addresses may talk to which.

---

## 2.10 The signed approval and acting exactly once

**The problem.** After the customer passes the checks, how does the bank know, safely, that it may act, and that nobody has swapped the details?

**How we thought about it.** Think of the approval as a **tamper-evident, single-use ticket** for one specific action. It names the type of action, the amount and the payee; it is signed by AMFA; it expires within minutes; and once the bank uses it, it can't be used again.

**The flow we chose.** After the last check passes, AMFA signs a ticket. The bank checks the signature and confirms the ticket's contents match what the customer originally asked for. It acts and marks the ticket used. If anyone changed the amount or payee in between, the contents no longer match and nothing moves.

**Why this design.** It defeats the classic tricks: forging an approval (signature), changing the details afterwards (binding), replaying an old approval (single use), and sitting on an approval for later (expiry). Asking the bank to "complete" twice doesn't act twice either.

---

## 2.11 Held requests and human review

**The problem.** Some requests are too risky, or too incomplete, for software to approve alone. A person must decide, but people are slow and fallible, so the process has to give them good evidence and guard against single errors.

**How we thought about it, and how it changed.**
- *Version one:* a severe request went straight to the review queue. Problem: the customer had never been verified in that request, so reviewers had no proof the real customer was there, and the rules (rightly) forbid approving without a live check. Result: held requests could only ever be rejected, which was useless.
- *Version two (chosen):* the customer **first does every check they can**, and only then does the request go to review. Reviewers now see a request where the customer has already proved presence, so approving it is possible and meaningful.

**Further safeguards.**
- **Two different reviewers must approve.** One compromised or careless reviewer can't release a severe request alone.
- **A reviewer can only approve if a live check passed** in that request. This is a hard rule so that a person can't wave through a request the customer never proved themselves for.
- **Rejected requests are recorded as fraud examples** so the risk model improves.
- **The customer may cancel** a held request at any time, because it's their money and their decision to stop.

**What we gave up.** Held requests take longer for the customer. In exchange, the decision can actually be made, and made on evidence.

---

## 2.12 Locking and freezing

**The problem.** The account needs two emergency brakes: one the system pulls when it suspects an attack, and one the customer pulls themselves.

**How we thought about it.**
- **System lock (suspected attack).** A suspected fake face or recording means someone may be impersonating the customer. The system declines the request, locks the account, and opens a review. Crucially, a review of such a lock can **clear the lock** but can **never turn that request into an approved payment**: the system has already concluded the person at the camera wasn't real, and a human shouldn't be able to overrule that in the same request.
- **Customer freeze.** If a phone is lost or details leak, the customer needs a one-tap stop that doesn't depend on the system noticing anything. Freezing blocks everything until the customer calls to unfreeze.

**Why they're separate.** Locks are about the *system's suspicion* and need staff to examine evidence. Freezes are about the *customer's wish* and need only the customer. Mixing them would make either one harder to use.

**What we gave up.** An honest customer who is wrongly locked has to wait for staff. That is why locking is rare (three strikes, only for suspected fakes) and why failed codes never lock.

---

## 2.13 Trusted phones

**The problem.** Stolen passwords are common. If any phone could do any action, the password would be the only gate.

**How we thought about it.** Treat the phone as another factor. A phone the bank has not seen is "new" and can't be used for banking until verified with strong checks (including face). Once verified, the phone is trusted, and it also proves itself with a private key held on the device, so simply copying its identifier from somewhere else doesn't work. If a phone *claims* to be trusted but fails to prove it, that is treated as a warning sign: an extra check is added, or the request goes to review if none is left.

**Why the sign-up phone is trusted automatically.** The customer has just proved themselves on it at account creation.

**What we gave up.** Customers with a new phone face a one-time verification. A web app can only keep that key in software; hardware-backed proofs would need a native app.

---

## 2.14 Adding a payee

**The problem.** Adding a payee changes the *recipient list* but moves no money. Yet a new, thinly set-up account adding its first payee was repeatedly sent to the dev team, because the model graded it as high risk (a new phone, nothing in history), high risk asked for checks the customer hadn't set up yet, and that triggered review. That was blocking a harmless action and creating manual work.

**How we thought about it.** Where does the real danger lie? Not in saving a name, but in the **first payment** to it. So protect that moment, and let the saving go through once the customer proves themselves.

**The flow we chose.** Adding a payee always asks for the checks the customer *can* do (at least code and face), and then saves the payee, without waiting for a person. The new payee counts as "recently added" in the risk signals, so the **first payment to it is checked more strictly**, and payments at the highest risk still go to review.

**Why not loosen review for everyone.** Payments at the highest risk *should* wait for a person. The exception is narrow: one action type that moves no money. It is a policy switch on that action, so it can be changed without touching code.

**What we gave up.** A fraudster who passes the customer's checks could save a payee, but would face full scrutiny on the first payment to it.

---

## 2.15 Changing the limit, contact details or password

**The problem.** These don't move money themselves but they are the steps *before* most account takeovers: raise the limit, change the email, then drain the account.

**How we thought about it.** Treat them as sensitive by nature (a higher floor of checks than a normal payment), and also feed them back into risk: a change made recently makes the next big payment look more suspicious. That second part is why a payment shortly after a contact change gets extra checks.

**Specific choices.**
- Changing the email or mobile is confirmed at the **old** contact, so an intruder can't redirect codes silently.
- Changing the password asks for the current password too, so a person who briefly holds an open phone can't change it.
- The daily limit is enforced by the bank itself, as well as being a risk signal, so it limits damage even if everything else fails.

---

## 2.16 What the customer is told

**The problem.** Customers deserve to know *why* they're being asked for something; attackers deserve to learn nothing useful.

**How we thought about it.** Two audiences, two levels of detail.
- **Customers** get a short, friendly, general reason ("We need a quick extra check because this payee hasn't been paid before"), and a scam warning when the request resembles common scams.
- **Reviewers and demo presenters** see the full reasoning: scores, signals, and what would have made the decision different.

**Why.** Detailed explanations of why a decision went a certain way are exactly what someone probing the system wants. Uniform wording on failures and general reasons for friction deny them that. Staff, who do need the detail, get it in a place customers can't see.

---

## 2.17 Fraud reports and the learning loop

**The problem.** A risk model only stays good if it keeps seeing real outcomes, including mistakes.

**How we thought about it.** There are three honest sources of labels: reviewers' verdicts, customers saying "this wasn't me", and ordinary completed requests that were verified. Each is recorded with the full context that produced the decision.

**What the system learns, and when.** It remembers a customer's trusted phones, usual networks and known payees **only after a request has been fully verified**. A request that never passed can't teach the system that something is normal; otherwise an attacker could "train" it by repeated attempts.

**Why record everything in a tamper-evident log.** Each entry is chained to the previous one, so altering history would break the chain. That gives auditors confidence the record is real.

---

## 2.18 Running and sharing the demo

**Single address.** Everything is served from one address so a phone needs only one link and the embedded check screens avoid cross-site problems.

**A public tunnel instead of Wi-Fi.** Home Wi-Fi blocked phones from reaching the laptop, so we used a temporary public secure link. Phone cameras also require a secure connection, which the tunnel provides. The trade-off: the link changes when restarted, and anyone with it can see the sign-up page, so it is for demos only.

**Starting fresh.** One action removes every customer and all their data (accounts, face templates, transactions, reviews, audit entries) but keeps the bank's connection to AMFA, its policies and the reviewer logins. This returns the demo to a clean state without rebuilding the setup. It is one-way, so it has a dry-run mode that lists exactly what would be deleted, and it is run with the server stopped so nothing is written back.

---

## 2.19 How we worked

- **Test first.** Each behaviour change starts with a test that fails for the right reason, then the fix, then the whole suite. Several bugs here (the frozen timer, repeated emails, falsely locked users) were subtle, and tests stop them returning.
- **Record the reasoning as we go**, so the next person can see *why*, not just *what*.
- **Keep these documents high level.** Exact thresholds and detection details are left out on purpose, because these documents may be shared and precise numbers would help someone trying to get around the controls. The exact values live in the bank's policy and the code, where access is controlled.

---

# Part 3. Decision log

Every significant choice, in the order it was made. Each entry says **what** was decided, **why**, **what else was possible**, and **what it costs**. Part 2 gives the longer reasoning; this is the quick reference.

## Product and design

### D1. Build a real customer app, not just a test page
- **Decision:** a complete bank app (sign up, sign in, pay, add payees, history, security settings) that uses AMFA for every sensitive action.
- **Why:** a security platform is only convincing when you see it protecting something real. Money moving only after verification shows the value end to end.
- **Alternatives:** a bare demo page with a "verify" button.
- **Cost:** a lot more surface to maintain.

### D2. A mobile-first web app
- **Decision:** a phone-sized web app rather than a native app.
- **Why:** it opens on any phone from a link, with no installs or app-store steps, and it still has camera, microphone and touch.
- **Alternatives:** a native iOS or Android app.
- **Cost:** no hardware-backed device proofs (those need a native app); the web version relies on a software device key instead.

### D3. One seamless app, in the bank's own look
- **Decision:** AMFA's setup and check screens are shown *inside* the bank app, in the bank's brand colour and full screen, not as a separate website.
- **Why:** customers trust what looks like their bank and get nervous at a sudden redirect. Seamless also feels faster and is more attractive.
- **Alternatives:** a redirect to AMFA's own pages.
- **Cost:** the bank has to allow AMFA to be embedded safely, so the connection is locked down to approved origins only.

### D4. A realistic app, with a separate "demo mode"
- **Decision:** the app behaves like a real bank, and demo-only tools (showing AMFA's reasoning) sit behind a presenter switch.
- **Why:** a real customer should never see scores or thresholds; a presenter explaining the system does need to.
- **Cost:** a little extra code kept apart from the real flow.

### D5. Progressive setup instead of everything up front
- **Decision:** at sign-up the customer only does the email code and a face setup. Voice, typing and an authenticator app are optional extras later.
- **Why:** long set-up sequences make people quit. Protection grows as the customer opts in, and the "protection level" in Security shows what they gain.
- **Alternatives:** make everyone enrol every method on day one.
- **Cost:** new accounts with fewer methods get asked for more checks, or held more often, on risky actions (see D12).

### D6. Clean, modern "neobank" look
- **Decision:** simple cards, one clear action per screen, plain language, big touch targets.
- **Why:** the first version was functional but unattractive and hard to follow; security flows are trusted more when they feel calm and clear.

---

## How decisions are made

### D7. Approvals are signed, single-use, and tied to the exact action
- **Decision:** the bank acts only on an AMFA-signed slip that names the type, amount and payee, expires in minutes, and works once.
- **Why:** it prevents the common attacks: changing the amount or payee after approval, re-using an approval, and forging one.
- **Alternatives:** a simple "approved: yes" reply.
- **Cost:** the bank must check the slip and keep track of used ones.

### D8. A risk model sets how much checking is needed
- **Decision:** a trained model grades every request on a five-step scale (T0 to T4), and each step maps to a set of checks.
- **Why:** the same checks for every request are either too weak for dangerous ones or too annoying for everyday ones. Proportionate checking protects without constantly interrupting.
- **Alternatives:** fixed rules by amount alone.
- **Cost:** needs a model that is kept up to date, and decisions must be explainable. If the model is unavailable the system fails safe (the request goes to review).

### D9. Bank policy and regulation set a floor under the model
- **Decision:** whatever the model says, each action type has a minimum, and every request needs at least two kinds of proof with one of them "live".
- **Why:** a model can be wrong or be fooled; policy and regulation are the guarantee underneath. High-impact actions (password, limits, contact changes, new device) deserve more than their score suggests.
- **Cost:** a calm request on a sensitive action can still be asked for more than it "needs".

### D10. Small, normal-looking payments are kept light
- **Decision:** a small payment that looks ordinary for the customer is held at a light level, unless there are real warning signs. Non-payments never get this relief.
- **Why:** if every ₹200 payment asks for a face scan, customers stop using the app or learn to click through checks. Light for the safe majority keeps heavy checks meaningful.
- **Cost:** a determined attacker might aim for small amounts. Hard warning signs switch the relief off, and the daily limit caps total exposure.

### D11. Failed checks decline the request, they don't lock the account
- **Decision:** running out of tries ends *that request*. Locking is reserved for suspected fake faces or recordings.
- **Why:** an honest customer who mistypes a code three times shouldn't be locked out of their money. Locks are for suspected attacks.

### D12. A check that isn't set up is replaced or, at high risk, sent for review
- **Decision:** if a required check can't run, a stand-in is used (face and voice cover for each other; typing can be covered by either). If there's no stand-in and the risk is high, the request goes to review.
- **Why:** quietly skipping a check at high risk would defeat the system. Substituting keeps most customers moving.
- **Cost:** thin accounts hit review more. The answer is encouraging customers to add more methods.

---

## Review and locking

### D13. Held requests: the customer does their checks *first*, then a person reviews
- **Decision:** when a request is held, the customer still completes every check they can, and only then does it enter the review queue.
- **Why:** originally held requests went straight to reviewers with no proof that the real customer was present, and reviewers then couldn't approve them (the rules require a live check). Doing the checks first gives reviewers real evidence and makes approval possible.
- **Cost:** a slightly longer path for the customer in return for a decision that can actually be made.

### D14. Two reviewers must approve
- **Decision:** approval needs two different people.
- **Why:** one compromised or careless reviewer shouldn't be able to release a high-risk request alone.
- **Cost:** slower; fine for the rare, severe cases it applies to.

### D15. A presentation-attack lock can never be approved into a payment
- **Decision:** when the account is locked because of a suspected fake face or recording, the review can clear the lock but can never turn that request into an approved payment.
- **Why:** the system already decided the person at the camera wasn't real. Letting a reviewer approve it anyway would undo the whole check.

### D16. Three strikes on suspected fakes before locking
- **Decision:** the first two suspected-fake flags in a request only cost a retry; the third declines the request and locks the account.
- **Why:** a real customer on a slow phone or in odd light can trigger these flags. A genuine customer was wrongly locked during testing, so we allowed a few tries before the lock while keeping it firm against real attacks.
- **Cost:** an attacker gets a couple of extra attempts per request. The retry limit and the lock keep that bounded.

### D17. Adding a payee never waits for the dev team
- **Decision:** saving a payee goes ahead after the customer's checks pass, even if the risk is at its highest. This is a switch on the action type in the bank's policy.
- **Why:** it moves no money. Customers with only the basic set-up were being sent to review because high risk asked for checks they didn't have yet, which blocked a harmless action and ended up needing manual work. The danger is the *first payment* to that payee, and that is still risk-checked separately, with "new payee" as a risk signal.
- **Alternatives:** loosen the whole review rule for everyone (rejected: payments should still be held at the highest risk).
- **Cost:** a fraudster could save a payee after passing the customer's checks; they would still face a full check on the first payment.

---

## Face checks

### D18. Give the face check enough time and fresh pictures
- **Decision:** the face check gives more time for the head turn, always sends **fresh** pictures (never the same one twice), and reports a stalled camera clearly instead of sending repeats.
- **Why:** early on, the turn left/right step felt rushed, and a slow phone camera repeated the same picture, which the system read as a replay attack and locked a real user.
- **Cost:** a slightly longer capture.

### D19. A bigger look when finding the face
- **Decision:** the liveness check looks at the face in higher detail than before.
- **Why:** it was rejecting real users because the smaller view lost the detail it needed.
- **Cost:** a little more processing per picture.

### D20. All face failures give the same message
- **Decision:** after a face is seen, every kind of failure shows the same advice.
- **Why:** specific feedback ("your liveness score was too low") teaches an attacker which part to improve.

---

## One-time codes

### D21. Codes go to the customer's email, collected at sign-up
- **Decision:** the sign-up form asks for an email address, and one-time codes are sent there. Older accounts that only have a phone number keep working through SMS until they add an email.
- **Why:** it works with no SMS provider or per-message cost, and is easy to demo. Email also leaves a record the customer can look back at.
- **Alternatives:** SMS only (needs a paid provider); an authenticator app only (needs the customer to set it up first).
- **Cost:** email is weaker than a hardware-backed method; that's the reason for the other checks alongside it. Codes arrive only if the email service is configured.

### D22. Emails are sent from a Gmail account for the demo
- **Decision:** a Gmail app password stored in a private file readable only by the owner.
- **Why:** quickest way to real email without extra infrastructure.
- **Cost:** a personal account has a daily sending limit and the password was shared in a chat, so it should be revoked when the demo is over. A real deployment would use a proper mail service.

### D23. A code's timer, resend, and retry rules
- **Decision:** the code lasts 3 minutes with a live countdown. A wrong code allows another try with the same code (3 tries). Resend is available after 30 seconds, limited to 3 codes per request, and a new code cancels the older ones.
- **Why:**
  - The timer was stuck because it began before the screen was displayed; it now counts down properly and matches the code's real life.
  - Emailing a *new* code after every typo was confusing, since the first email still worked and used up the send allowance.
  - The limits stop inbox flooding and guessing.
- **Cost:** a few extra rules to explain; each protects the customer or the system.

---

## What the customer sees

### D24. Plain reasons for the customer, full detail for staff
- **Decision:** customers see a short friendly reason for any extra check; reviewers and presenters see the full reasoning.
- **Why:** keeps customers informed without handing out a map of how to avoid detection.

### D25. A show/hide eye on every password box
- **Decision:** sign-in, sign-up and change-password all use one shared password box with an eye button.
- **Why:** typing a password on a phone is error-prone, and the lack of a show option caused mistakes. One shared component keeps them consistent and means fixing it once fixes it everywhere.

### D26. The customer can freeze their account and report "this wasn't me"
- **Decision:** both are one tap, and a "this wasn't me" report is recorded as a fraud example.
- **Why:** customers need a way to stop things immediately if something's wrong, and those reports are the best fraud labels the model can learn from.

---

## Running and sharing the demo

### D27. One address serves everything
- **Decision:** the bank app and AMFA are served from a single address.
- **Why:** one link is much easier to share, and it avoids cross-site problems when AMFA screens are shown inside the bank app.

### D28. A public tunnel instead of the home Wi-Fi
- **Decision:** the app is shared through a temporary public secure link.
- **Why:** the home Wi-Fi blocked phones from reaching the laptop (devices on it can't talk to each other), so a phone on the same network still couldn't connect. A public link works from any network and uses proper HTTPS, which phone cameras require.
- **Cost:** the address changes each time the tunnel is restarted, and anyone with the link can open the sign-up page, so it is only for demos.

### D29. "Start fresh" removes customers, not the setup
- **Decision:** clearing the demo deletes all customers and everything about them (accounts, face templates, transactions, reviews, audit entries) but keeps the bank's connection to AMFA, its policies, and the reviewer logins.
- **Why:** this returns the demo to a clean state without needing to set up the bank all over again.
- **Cost:** it's a one-way action; the server must be stopped while it runs.

---

## Working practices

### D30. Test first, and keep the full test suite green
- **Decision:** each behaviour change starts with a test that fails for the right reason, then the fix, then the whole suite.
- **Why:** several bugs here (stuck timer, repeated emails, locked genuine users) were subtle; tests pin them so they can't quietly return.

### D31. Commits are authored as the project owner only
- **Decision:** commits are in Poojan Pandya's name, with no extra attribution lines.
- **Why:** the owner asked for it.

### D32. This documentation stays high level
- **Decision:** `flow.md` and `decision.md` explain the system and the reasoning; exact thresholds, tuning values and detection details are left out on purpose.
- **Why:** these documents may be shared widely. Precise numbers about what triggers extra checks, how many flags cause a lock, or which warning signs matter would help someone trying to get around the controls. The exact values live in the bank's policy and the code, where access is controlled.

---

## Open items and things to know

- **Existing phone-only accounts** can't receive codes until they add an email (Security → Mobile and email).
- **The mail password** should be revoked once testing is done.
- **Customers with only the basic set-up** face more extra checks on risky actions; adding voice and typing reduces this.
- **The public link is temporary** and changes when restarted.
- **Not yet done:** a production-grade SMS or email provider, and hardware-backed device proofs (which need a native app rather than a web app).
