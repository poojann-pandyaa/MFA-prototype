# Report addendum: the band switch

Text for the project report (AMFA-project-report, status 16 November 2026), in its own plain style. Each part says where it goes.
The numbers come from [band-switch-replay.md](band-switch-replay.md) and the tests named in
[rbi-banded-data-collection.md](rbi-banded-data-collection.md).

---

## New section 11.5 (after "Two optional sign-up questions"): when the bank gives us no data

**The problem.** The risk model was trained on a simulator, because there was no real fraud history to learn from. Section 7.4
says it plainly: a model trained on a simulator learns the simulator. A bank that does share its past data lets the model be
checked, and retrained, against real outcomes. A bank that will not share it gives us nothing to check the model against. And the
data limits (section 2.4) mean we should keep as little about each customer as we can.

**The idea.** Do not trust the model to lower checks until real payments at this bank show it can. Measure that in amount bands,
not per person:

| Band | Payments |
|---|---|
| 0 | Rs 0 to Rs 1,999.99 |
| 1 | Rs 2,000 to Rs 9,999.99 |
| 2 | Rs 10,000 to Rs 99,999.99 |
| 3 | Rs 1 lakh and above |

The Rs 1 lakh edge was added to the professor's example of three bands after measuring where the risk is. In the simulator, almost
all fraud money (97.5%) is in payments of Rs 1 lakh or more. A single top band from Rs 10,000 to crores would have let evidence
from Rs 20,000 payments decide how Rs 50 lakh payments are checked.

**What is kept.** For every finished payment, one anonymous record:
- **Kept:** the band; whether the payee and phone were known; the part of the day; whether there was a warning sign; what the rules
  and the model would each have asked for; how the payment ended; whether it later turned out to be fraud; and the week.
- **Never kept:** the customer, the amount, the payee, the phone, the place or the exact time.
- **The link to the payment:** a keyed hash, so that a fraud report arriving weeks later can find its record. It is wiped after 90
  days, after which nothing can tie the record to anyone.
- **Nothing new from the app:** the band is worked out on the server from the amount the bank already sends.

**How a band earns the model.**
- **Until then**, a payment gets everything the model asks for and everything the rules ask for. It is never weaker than either.
- **Every night**, each band's records are graded on the same payments: how often did the rules and the model each catch fraud,
  and how often did each bother an honest customer? Only records old enough to have been reported (30 days) count.
- **A band becomes eligible** when it has, within 8 weeks:
  - 5,000 genuine payments, and 100 fraud cases reported by the bank or the reviewers (simulated fraud never counts);
  - data in 8 separate weeks;
  - a model that missed no more fraud and bothered no more honest customers than the rules, 4 weeks in a row.
- **The Rs 1 lakh band** needs twice the evidence, and no single fraud the model let through that the rules would have stopped.
- **Moving an eligible band to the model** needs two different operators. Sending it back needs one.

**Why per band and not per customer.** No single customer will ever make 5,000 payments; the band test is pooled across all of a
bank's customers. Whether the model knows an individual customer is still the readiness test of section 11.3. The model chooses a
customer's checks only when both have passed.

## Add to section 14 (Security and privacy by design), as new rows of the table

| Protection | How it works | Why |
|---|---|---|
| Anonymous band records | The band switch keeps one record per payment with no customer, amount, payee, device, place or time. Its link to the payment is a keyed hash wiped after 90 days. A test fails the build if an identifying column is ever added. | The model can be judged on real outcomes without keeping more about anyone. |
| Two people to relax | Moving a band to the model, turning the band switch off, or asking it for less evidence needs two different operators, and is refused on every other path (the bank's API key, a single operator, even a platform operator). A request is void if the settings change before it is approved. | The one change that lowers friction for many customers at once cannot be made by one person or one stolen key. |
| Fails towards the rules | A missing, unreadable or stale band state (not graded for 48 hours) is decided by the rules. A model band that loses two weekly gradings goes back on its own; the Rs 1 lakh band goes back on the first fraud the model let through. | Security that fails open is worse than none (section 8.7). |

## Add to section 17 (How it was tested), a new paragraph

**The band switch.** 229 automated tests cover it. Key ones:
- the exact band edges;
- that a split payment is judged by its day's total;
- that collecting is never weaker than the model across 7,200 generated situations;
- the two-person rule with real operator sessions;
- every fail-closed case;
- both fallbacks;
- privacy tests on the stored records.

**A simulated bank replay.** A simulated bank was replayed from its first day through the real decision code and grading. Across
91,198 payments in two runs, the switch never asked for less than the bank would have without it.

| Run | What happened |
|---|---|
| 700 customers, the lowest allowed gates | The Rs 10,000–1 lakh band earned the model on day 115 |
| 2,000 customers, the default gates | No band earned it within 150 days |

The live smoke test passes all 83 checks with the switch on and off.

## Add to section 18 (Known limits and honest findings)

- **The two smallest bands will probably stay on the rules.** In both replays the model missed more fraud than the rules in Rs 0–2,000 and Rs 2,000–10,000. That is by design on the model's side: it keeps small payments light. The rules ask for a face check for every new payee. The cost is that 27% and 39% of honest customers' payments in those bands meet a face check or more, against 6% and 14% with the model alone. A small allowance for those bands (they hold about 0.2% of the fraud money) was considered and rejected: the switch never accepts a known loss in fraud caught.
- **Collecting costs friction.** Until a band earns the model, honest customers in it are asked for more. The bank sees the figure per band.
- **Small banks may never switch the big bands.** At a realistic fraud rate, the Rs 1 lakh band of a small bank may never reach 200 reported fraud cases. It then stays on the stricter rules, which is the intended outcome.
- **No console screen yet.** The admin API and the command line show each band's state and figures; a page in the admin console is still to build.

## Change in section 19 (What comes next), step 1

Add: "Banks that give no training data use the band switch (section 11.5): the model earns each amount band on that bank's own
real outcomes before it may lower checks there."

## Add to section 20 (Glossary)

| Term | Meaning |
|---|---|
| Band switch | The rule that the model may lower checks for an amount band only after real payments at that bank show it does at least as well as the rules. |
| Collecting | A band's state until it has earned the model: the stricter of the rules and the model decides. |
| Eligible | A band that has passed its gate four weeks running and waits for two operators to move it to the model. |

## For the bank's privacy notice (not the biometric consent)

> We keep an anonymous record of the size band of each payment (for example "Rs 2,000 to Rs 10,000"), never the amount, the payee or
> who made it, to decide when our automated checks can be trusted. The link between that record and your payment is deleted after
> 90 days.

The biometric consent in the app (`CONSENT_VERSION` in `amfa/core/api_client.py`) was left unchanged on purpose. The band records
are not biometric, and changing that consent's version would make every customer consent again before their next face or voice
check.
