# Regression: every way of confirming a payment, and the risk model, on customers who have a past

Three layers, from fast to realistic. Run them in this order after any change to the engine, a factor or a model.

| Layer | Command | What it proves |
|---|---|---|
| Unit and service tests | `python -m pytest` | Each factor (face, voice, typing and swipe, one-time code by email or authenticator), the engine, the risk model and the API do what they say. Voice runs with scripted models (no torch) |
| Customer matrix | `python -m amfa.demo.regression --markdown docs/regression-report.md` | Seven demo customers, each in about ten situations, through the real decision path with the trained risk model: how much each is asked for, and whether the dangerous situations cost more than normal ones |
| Live | `python -m amfa.demo.regression --seeded demo-bank` and `python -m amfa.demo.factor_regression` | The same on the customers seeded into the running demo, with the real behaviour (UEBA) service: the checks asked, and whether the typing check accepts the owner and refuses clearly different typists and a bot |

`python -m pytest tests/engine/test_persona_regression.py` runs the matrix with a small freshly trained model and the
behaviour service stood in, so it is in CI. Reports: [regression-report.md](regression-report.md) (all checks available),
[regression-report-demo.md](regression-report-demo.md) (what the live demo has: no face or voice).

## Demo customers

`python scripts/seed_demo.py --tenant demo-bank --bank-state data/demo-bank-state.json --home-ip 127.0.0.1 --device <phone id>`
gives the bank these customers (all with the password `DemoPass#2026`; restart the bank after). Each has a history in
AMFA and in the bank, the same payees and activity, and the same phone.

| Customer | Use case | What the demo shows |
|---|---|---|
| asha | everyday small payments, one phone | a code and the quiet typing check, a plain "routine check" |
| rohan | gig worker, evenings, two phones | many small payments stay light |
| meera | careful saver, monthly rent | the rent to the same landlord stays light despite 25,000 |
| suresh | senior, only a code enrolled | cannot meet the two-factor minimum, so a person reviews every payment |
| priya | small business, many payees | large routine orders stay light; a new payee or phone asks for more |
| vikram | high value, premium floor | a large routine transfer; the floor adds a check |
| dev | opened this week | no past to judge, a person reviews |

## What the regression found (and what was fixed)

* **Fixed:** the reason shown to the customer ("we need a check because this payee was added recently") came from the model's
  top attributions even for a routine payment, so it could be false (a payee paid for two years). A reason is now given only
  if the customer's own numbers support it (`rationale._holds`).
* **Risk model release gate:** the model beats the baseline (PR-AUC 0.954 against 0.65, 98.6% of fraud money caught at T3 or
  above) but **fails the fairness limit**: genuine senior customers are stepped up 2.45 times the average (limit 1.25) and
  premium 1.39 times. It is not activated by default; the demo here ran with it activated by hand (a local override) so the
  rest could be tested. The training data is simulated, so treat the numbers as a pipeline check, not accuracy.
* **By design, but worth deciding on:** a customer with only a one-time code cannot meet the two-factor minimum, so every payment
  goes to a person; a customer with no face or voice enrolled gets only a code and typing even at the highest threat level;
  a SIM swap with a small payment stays light (small-payment cap). The reports list these.
* **Typing is not an identity proof:** the check accepts the owner and refuses a typist at twice or three times their pace and
  a bot, but accepts someone with a similar pace. Touch thresholds are still provisional.
* **Not testable here:** accuracy of face, voice and liveness on real people (no labelled data), and the voice service end to
  end (its models are not downloaded).
