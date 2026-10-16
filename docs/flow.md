# How it all works: the flow

This is the big picture of Demo Bank and AMFA working together, in plain language. There is no code here. For the *why* behind each choice, see [decision.md](decision.md).

---

## 1. The cast

| Who | What they do |
|---|---|
| **The customer** | Uses the Demo Bank app on their phone. |
| **Demo Bank (the bank app)** | Holds the account, balance, payees and history. It decides *what* needs approving, but never decides *how* to approve it. |
| **AMFA (the security platform)** | The bank hands it every sensitive request. AMFA works out how risky the request is, picks the checks, runs them, and answers with a signed "approved" slip. |
| **The check services** | Face, voice and typing-behaviour checkers that AMFA calls. The email code is handled by AMFA itself. |
| **The dev team (reviewers)** | People who look at requests AMFA won't approve by itself. |

**The key idea:** the bank never moves money on its own say-so. It moves money only when AMFA hands back a signed approval slip for *that exact action*.

---

## 2. Becoming a customer

1. **Open an account.** The customer gives a name, mobile number, **email address**, username and password. The email matters: it is where one-time codes are sent.
2. **Set up protection.** The app is *progressive*: only what's needed to start. Today that means the email code (ready straight away) plus a quick **face setup**. Voice, typing and an authenticator app can be added later in Security, and each one means fewer interruptions.
3. **Trusted phone.** The phone used to sign up becomes the first trusted device automatically.
4. **Ready.** The customer lands on Home and can start banking.

The face photos never reach the bank. Only an encrypted template is kept, and it can be removed.

---

## 3. Everyday life in the app

- **Sign in** with username and password (there's an eye button to show or hide what's typed).
- **Home** shows balance, recent payees, and recent activity.
- **Security** shows how many ways the customer has to prove it's them (the "protection level"), lets them add more, change limits, contact details and password, or **freeze** the account in one tap.
- **Demo mode** lets a presenter peek at what AMFA decided. A real customer never sees the scoring.

---

## 4. The journey of one sensitive request

This is the heart of the system. Paying someone, paying a bill, adding a payee, changing the limit, changing contact details, changing the password, and verifying a new device all follow the same path.

```
Customer asks  →  Bank sanity checks  →  AMFA assesses risk  →  Checks are chosen
      →  Customer does the checks  →  AMFA gives a signed approval  →  Bank acts once
```

### Step 1. The customer asks
They fill in the details (who, how much) and tap send. Nothing moves yet.

### Step 2. The bank's own sanity checks
Before bothering AMFA, the bank refuses anything obviously wrong:
- the account is **frozen**;
- the phone isn't a **verified device** yet (it must be verified first);
- not enough balance, or over the **daily limit**;
- the payee isn't saved.

### Step 3. AMFA assesses the risk
The bank sends AMFA the *facts* of the request (what kind, how much, to whom, from which device, when). AMFA combines:
- a **risk model** that scores how unusual and dangerous the request looks compared to the customer's own history and known patterns of fraud, and turns the score into a **threat level from T0 (calm) to T4 (severe)**;
- the **bank's policy**, which sets a minimum for each type of action (for example, changing a password always needs more than a small bill payment);
- a **regulatory minimum**: at least two kinds of proof, and at least one of them must be a "live" one (the emailed code, face, or voice).

The strictest of these wins.

**Small payments stay light.** A small payment that looks normal for that customer is kept at a lighter level so everyday spending isn't annoying. This relief is switched off the moment there are real warning signs (recent security incidents, a risk-listed payee, recent changes to the account, and so on). Anything that isn't a payment, like adding a payee or changing a setting, never gets this relief.

### Step 4. Checks are chosen
Calmer levels mean fewer checks; severe levels mean all of them:

| Level | Meaning | Checks, roughly |
|---|---|---|
| T0 | Very ordinary | Email code |
| T1 | Ordinary but worth a light check | Email code + typing behaviour |
| T2 | Somewhat unusual | Adds a face check, plus a scam warning |
| T3 | Unusual | Adds a voice check |
| T4 | Severe | Everything, then a person must look at it |

If a check the customer *hasn't set up* is needed, AMFA tries a stand-in (face and voice can cover for each other; typing can be covered by face or voice). If no stand-in exists, see "Held requests" below.

### Step 5. The customer does the checks
The app opens AMFA's check screens **inside the bank app**, in the bank's colours, so it feels like one app. The customer sees what they are approving (amount and payee) at the top the whole time.

- **Email code:** a 6-digit code arrives by email naming the amount and payee. It lasts 3 minutes with a visible countdown. A wrong code lets them try again with the *same* code (up to 3 tries). "Resend" appears after 30 seconds and sends a fresh code; once a new code is sent, older ones stop working. There's a cap on codes per request so nobody can flood an inbox.
- **Face:** look at the camera, then turn the head as asked. Pictures are always fresh, never repeated, so a slow phone camera can't be mistaken for a replay.
- **Voice:** read a few words and numbers shown on screen.
- **Typing:** type a short phrase and trace a line; AMFA compares how it's done with the customer's usual pattern.

If a check fails, there's a limited number of retries, then the request is declined (the account is **not** locked for ordinary failures).

### Step 6. The signed approval
When every needed check passes, AMFA issues a short-lived, single-use **approval slip**. The slip is:
- **signed**, so it can't be forged;
- **tied to the exact amount and payee**, so changing either afterwards makes it invalid;
- **valid for only a couple of minutes** and **usable once**.

### Step 7. The bank acts, once
The bank checks the slip, confirms it matches what the customer actually asked for, and only then moves the money (or saves the payee, or changes the setting). Asking twice never acts twice.

The activity list then shows the result along with what the customer confirmed it with, such as "Email code, Face check".

---

## 5. Held requests: when a person must look

Some requests are held for the dev team. They arise when:
- the threat level is **T4** (severe);
- the risk is high *and* a needed check can't run (and has no stand-in);
- the risk model itself is unavailable (AMFA plays safe);
- a check service is down with no replacement;
- a "trusted" device fails to prove it really is that device.

**Exception:** adding a payee is never held for a person. It moves no money, and the first payment to that payee is risk-checked on its own.

**How a held request proceeds:**
1. The customer first does every check they *can* do. This matters, because reviewers need proof the real customer is there.
2. The request then goes to the review queue, and the customer is told it's waiting.
3. **Two different reviewers** must approve (four-eyes). A reviewer can only approve if the customer passed a live check (the email code, face, or voice) in that request.
4. Approved: the customer continues and the request completes. Rejected: it's declined, and the case is recorded as a fraud example so the risk model learns from it.
5. The customer can cancel a held request at any time.

---

## 6. Locking and unlocking

- **Ordinary mistakes** (a wrong code, a failed voice match) never lock the account. They just use up tries.
- **Suspected fake faces or recordings** (a photo, a screen, a replayed video): the first couple of flags only cost a retry, because real customers can trip them with a stalled camera or bad lighting. Enough flags in one request decline it, **lock the account**, and alert the reviewers.
- **A lock caused by a presentation attack can never be approved into a payment.** It must be cleared by the dev team looking at the evidence first.
- **Freeze** is the customer's own panic button: nothing can be paid or changed until they call to unfreeze.

---

## 7. New phones

A phone the bank hasn't seen can't be used for banking until it's verified. Verifying needs a strong set of checks (including face), then the phone becomes trusted. Later, a trusted phone also proves it really is that phone using a secret kept on the device, so a copied-over "device ID" is not enough.

---

## 8. What the customer is told (and not told)

The customer sees a friendly one-line reason such as "We need a quick extra check because this payee is new." They never see scores, thresholds or which signal tipped the decision. Failed face checks all give the same message regardless of which part failed. This is deliberate: detailed feedback would teach a fraudster how to get past the system.

The dev team and the demo-mode presenter see the full reasoning.

---

## 9. The learning loop

- Every decision, attempt and outcome is recorded.
- Reviewer verdicts and customer "this wasn't me" reports become **labelled examples** of fraud or genuine activity.
- Those labels are what the risk model is retrained on, so it gets better with use.
- Everything important is also written to a tamper-evident audit log (each entry is chained to the one before).

---

## 10. How the pieces are connected for the demo

- **One public link** serves both the bank app and AMFA so a phone anywhere can open it. It is a temporary tunnel, so the address changes if it's restarted.
- **Email codes** are sent from a Gmail account. If email isn't configured, codes are only written to the server log.
- **The dev team console** is where reviewers approve or reject held requests and unlock customers.
- **Starting fresh** removes all customers and their data but keeps the bank's connection to AMFA, its policies, and the reviewer logins.
