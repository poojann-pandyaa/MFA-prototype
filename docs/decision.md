# Decision log

Every significant choice made while building the Demo Bank customer app on top of AMFA, in the order it was made. Each entry says **what** was decided, **why**, **what else was possible**, and **what it costs**. No code; for how things fit together, see [flow.md](flow.md).

New decisions go at the bottom. If a decision is reversed, don't delete it; add a new entry that says so.

---

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
