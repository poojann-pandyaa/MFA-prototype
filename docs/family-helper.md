# Family helper (optional)

**Problem it answers.** Customers over 60 are stepped up to a face or voice check about 2.2 times as often as everyone else on genuine
payments. One cause: a family member often helps them pay from the family member's own phone. To the model that is a new phone in a new
place, the same picture as an account takeover.

**What it does.** The customer may register that family member's phone as a *helper*. A payment from a registered helper phone is
read as expected, not strange: the model gets `helper_device = 1` (it can only lower risk, a monotone constraint) and the phone counts as a
known device in the plan. Everything else still applies: a new payee, a large amount, a hard warning sign are checked as usual.

**Optional, always.** Nothing asks for it. Banks turn it on (`familyHelpers.enabled`, off by default); a customer who never adds a helper
is unaffected. The sign-up screen only mentions it in one line, and a helper can be added or removed from Profile any time.

## How a helper is added

1. On their own phone the customer opens Profile, Family helper, types a name and taps "Get a code". This is a checked account change
   (a `CHANGE_LIMIT` for payee `HELPER`, amount 0): AMFA asks for the strong checks first, and the bank applies it only against the
   consumed proof, once (`amfa/core/helpers.py`, same rule as the customer's no-check limit).
2. They get an 8-digit code, valid for 15 minutes, usable once. Only its keyed hash is stored.
3. On the family member's phone, signed in as the customer, Profile, Family helper, "Helping someone with this phone?", enter the code.
4. The phone counts as a helper after the bank's cooling-off (`familyHelpers.coolingOffHours`, 24 by default; the demo seeder sets 0).
5. The customer can remove a helper at once, with no checks (it only lowers trust).

A fraudster who has taken over an account would love a trusted phone, so: adding one needs the strong checks, the code is short-lived and
single-use, the phone waits out the cooling-off, a bank sets the maximum (`maxHelpers`, 2 by default), and the training data contains
this attack on purpose (`patient_helper`: register own phone, wait, then drain).

## Where it is in the product

- **Server:** `amfa/core/helpers.py`, `trusted_helper`, endpoints under `/v1/users/{id}/helpers` (list, add against an approval, redeem
  from the helper's phone, remove), policy `familyHelpers` (off by default; `maxHelpers` 2, `coolingOffHours` 24, `inviteMinutes` 15).
  The context builder marks the helper phone as known and gives the model `helper_device`.
- **Demo bank:** action `helper`, `POST /api/helpers/redeem`, `DELETE /api/helpers/{id}`, and `helpers` in `/api/me`.
- **Android:** Profile, "Family helper" (list, "Get a code", and "Helping someone with this phone?"), and one optional line on the
  sign-up questions. Adding a helper asks for a code and the phone's lock screen; the code is shown once on the result screen.
- A phone the bank has not verified for the account first sees "Is this your new phone?" and verifies itself (`DEVICE_BINDING`);
  a helper phone is that same step, followed by entering the code.

## What is stored

`trusted_helper`: a label the customer typed (24 characters), a keyed hash of the helper phone, timestamps. No name, number or location.
Erased with the customer.

## What sign-up asks

Only what the model uses, all optional except what the bank needs to open an account: a typical payment size (band), a month's total
(band), up to three regular payees (the bank vouches), and the one-line helper note. Nothing else was added.

## How much it helps (measured, simulated customers)

Honest result: **little**. Assisted payments are about 10% of seniors' genuine payments, so even removing every one of their extra
step-ups moves the senior ratio only a little.

| If this share of senior households register a helper | Senior segment ratio (T2+, model only) | Assisted payments stepped up |
|---|---|---|
| nobody | 2.23 | 10.1% |
| 45% | 2.22 | 8.0% with a helper, 10.8% without |
| everyone | 2.21 | 9.1% |

The release gate (limit 1.25) still fails. The rest of the gap is not helper-shaped: ordinary payments by seniors (the amount drifts
further from their own habit and they have far fewer past payments: median 25 against 192), one-off large payments, weddings and
festivals. Binding the phone's key does not change it either (3.86 to 3.77 in a counterfactual). A planned-payment note ("I will pay
₹3 lakh to the hospital this week") would target those cases; it is not built.
