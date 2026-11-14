# Regression report

Run 2026-11-14 17:42. Friction counts a code as 1, typing as 0.5, face or voice as 2, a person's review as 4.

## Asha Rao (asha)
*Everyday small payments: groceries, milk, the odd bill. Same phone, same hours.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| tiny to usual payee | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| bigger than usual | T2 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, modest | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new device | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| other network | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, large, 3 am | T4 | 0.33 | OTP, UEBA | 1.5 | yes |
| account takeover | T4 | 1.00 | OTP, UEBA + review | 5.5 | yes |
| contact changed, then a big payment | T3 | 0.05 | OTP, UEBA + review | 5.5 | yes |
| small takeover payment | T4 | 1.00 | OTP, UEBA | 1.5 | yes |

## Rohan Das (rohan)
*Gig worker: many small payments late into the evening, two phones.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| tiny to usual payee | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| bigger than usual | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, modest | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new device | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| other network | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, large, 3 am | T4 | 0.06 | OTP, UEBA | 1.5 | yes |
| account takeover | T4 | 1.00 | OTP, UEBA + review | 5.5 | yes |
| contact changed, then a big payment | T3 | 0.00 | OTP, UEBA + review | 5.5 | yes |
| small takeover payment | T4 | 1.00 | OTP, UEBA | 1.5 | yes |

## Meera Iyer (meera)
*Salaried and careful: a few payments, and the rent to the same landlord every month.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| tiny to usual payee | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| bigger than usual | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, modest | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new device | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| other network | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, large, 3 am | T4 | 0.27 | OTP, UEBA, FACE | 3.5 | yes |
| account takeover | T4 | 1.00 | OTP, UEBA, FACE + review | 7.5 | yes |
| contact changed, then a big payment | T3 | 0.02 | OTP, UEBA, FACE + review | 7.5 | yes |
| small takeover payment | T4 | 0.93 | OTP, UEBA, FACE | 3.5 | yes |
| rent as usual | T1 | 0.00 | OTP, UEBA | 1.5 |  |

## Suresh Pillai (suresh)
*Senior, little digital banking: a few small payments, and the kind of customer a scammer coaches into a big first payment.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T1 | 0.00 | OTP + review | 5 |  |
| tiny to usual payee | T1 | 0.00 | OTP + review | 5 |  |
| bigger than usual | T2 | 0.00 | OTP + review | 5 |  |
| new payee, modest | T1 | 0.00 | OTP + review | 5 |  |
| new device | T1 | 0.00 | OTP + review | 5 |  |
| other network | T1 | 0.00 | OTP + review | 5 |  |
| new payee, large, 3 am | T4 | 0.33 | OTP + review | 5 | yes |
| account takeover | T4 | 0.69 | OTP + review | 5 | yes |
| contact changed, then a big payment | T4 | 0.19 | OTP + review | 5 | yes |
| small takeover payment | T3 | 0.05 | OTP + review | 5 | yes |
| coached scam payment | T4 | 0.06 | OTP + review | 5 | yes |

## Priya Sharma (priya)
*Small business: many payees, many payments a day, larger amounts, in working hours.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| tiny to usual payee | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| bigger than usual | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, modest | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new device | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| other network | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, large, 3 am | T3 | 0.04 | OTP, UEBA, FACE | 3.5 | yes |
| account takeover | T4 | 1.00 | OTP, UEBA, FACE + review | 7.5 | yes |
| contact changed, then a big payment | T2 | 0.00 | OTP, UEBA, FACE + review | 7.5 | yes |
| small takeover payment | T4 | 1.00 | OTP, UEBA, FACE + review | 7.5 | yes |
| routine bulk order | T0 | 0.00 | OTP, UEBA | 1.5 |  |

## Vikram Malhotra (vikram)
*High-value customer: a few large transfers to the same people.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T1 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| tiny to usual payee | T1 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| bigger than usual | T1 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| new payee, modest | T3 | 0.02 | OTP, UEBA, FACE, VOICE | 5.5 |  |
| new device | T2 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| other network | T1 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| new payee, large, 3 am | T4 | 0.18 | OTP, UEBA, FACE, VOICE | 5.5 | yes |
| account takeover | T4 | 0.99 | OTP, UEBA, FACE, VOICE + review | 9.5 | yes |
| contact changed, then a big payment | T3 | 0.04 | OTP, UEBA, FACE, VOICE + review | 9.5 | yes |
| small takeover payment | T4 | 0.77 | OTP, UEBA, FACE, VOICE + review | 9.5 | yes |
| routine large transfer | T1 | 0.00 | OTP, UEBA, FACE | 3.5 |  |

## Kiran Shah (kiran)
*Pays about a thousand or two almost every day to the same few people. A payment of ten thousand or a lakh would be a big event for Kiran.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| tiny to usual payee | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| bigger than usual | T3 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| new payee, modest | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new device | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| other network | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, large, 3 am | T4 | 0.06 | OTP, UEBA, FACE | 3.5 | yes |
| account takeover | T4 | 1.00 | OTP, UEBA, FACE + review | 7.5 | yes |
| contact changed, then a big payment | T3 | 0.02 | OTP, UEBA, FACE + review | 7.5 | yes |
| small takeover payment | T4 | 0.99 | OTP, UEBA, FACE | 3.5 | yes |
| ten thousand to a regular payee | T3 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| a lakh to a regular payee | T3 | 0.02 | OTP, UEBA, FACE | 3.5 |  |

## Anil Kapoor (anil)
*Runs a trading business: a few thousand a day to many small suppliers, and about a lakh every Monday to the same wholesaler. A lakh is a normal Monday for Anil; five lakh would not be.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| tiny to usual payee | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| bigger than usual | T2 | 0.00 | OTP, UEBA, FACE | 3.5 |  |
| new payee, modest | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| new device | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| other network | T0 | 0.00 | OTP, UEBA | 1.5 |  |
| new payee, large, 3 am | T4 | 0.06 | OTP, UEBA, FACE, VOICE | 5.5 | yes |
| account takeover | T4 | 1.00 | OTP, UEBA, FACE, VOICE + review | 9.5 | yes |
| contact changed, then a big payment | T3 | 0.02 | OTP, UEBA, FACE, VOICE + review | 9.5 | yes |
| small takeover payment | T4 | 1.00 | OTP, UEBA, FACE, VOICE | 5.5 | yes |
| the usual Monday payment | T1 | 0.00 | OTP, UEBA | 1.5 |  |
| a lakh to a small supplier | T3 | 0.00 | OTP, UEBA, FACE, VOICE | 5.5 |  |
| five lakh to the wholesaler | T3 | 0.05 | OTP, UEBA, FACE, VOICE | 5.5 |  |

## Dev Anand (dev)
*Brand-new customer: opened this week, no past to learn from.*

| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |
|---|---|---|---|---|---|
| routine | T1 | 0.02 | OTP + review | 5 |  |
| tiny to usual payee | T1 | 0.05 | OTP + review | 5 |  |
| bigger than usual | T1 | 0.02 | OTP + review | 5 |  |
| new payee, modest | T1 | 0.02 | OTP + review | 5 |  |
| new device | T1 | 0.02 | OTP + review | 5 |  |
| other network | T1 | 0.02 | OTP + review | 5 |  |
| new payee, large, 3 am | T2 | 0.68 | OTP + review | 5 | yes |
| account takeover | T4 | 0.77 | OTP + review | 5 | yes |
| contact changed, then a big payment | T4 | 0.54 | OTP + review | 5 | yes |
| small takeover payment | T3 | 0.04 | OTP + review | 5 | yes |
| first payment, small | T1 | 0.02 | OTP + review | 5 |  |
| first payment, large | T2 | 0.68 | OTP + review | 5 | yes |

## Expectations

All expectations hold.

## Findings (by design, worth knowing)

- Suresh Pillai has only a one-time code enrolled, which cannot meet the two-factor minimum, so every payment, even a routine one, goes to a person. A second factor at sign-up (the quiet typing check) removes that.
- Dev Anand has only a one-time code enrolled, which cannot meet the two-factor minimum, so every payment, even a routine one, goes to a person. A second factor at sign-up (the quiet typing check) removes that.
- The model rated these T3 or T4 but the customer has no face or voice check, so the plan is only a code and typing (the low-touch rule uses the strongest checks available instead of a person): asha / new payee, large, 3 am; asha / small takeover payment; rohan / new payee, large, 3 am; rohan / small takeover payment
- A SIM swap with a new phone but a small amount stays at a code and typing by design (small-payment cap, hard holds need an amount at or above the bank's MEDIUM line): anil, asha, kiran, meera, rohan
