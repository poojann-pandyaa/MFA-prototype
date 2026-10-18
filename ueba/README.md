# UEBA — Behavioural Verification

A factor service for the adaptive MFA system. It answers two questions at two
moments in a transaction:

| When | Endpoint | Uses | Asks the user for |
|---|---|---|---|
| Before the decision | `POST /risk/preview` | Transaction and session behaviour | Nothing |
| When selected as a factor | `POST /factor/verify` | Typing rhythm and shape tracing | A typed phrase and a traced shape |

The full request and response formats are in **[CONTRACT.md](CONTRACT.md)**.

| Dimension | Observes | Enrollment |
|---|---|---|
| **Keystroke** | Key hold and gap timing on a fixed phrase | 6 recordings, first discarded, 5 used |
| **Shape trace** | Deviation and pace at 24 checkpoints along a trapezoid | 4 complete traces |
| **Mouse** | Free pointer movement | Passive; reported, not scored |
| **Session** | Session length and pacing | Builds up from approved transactions |
| **Transaction** | Amount, device, location, payee, hour, velocity | Builds up from approved transactions |

### Laptop and phone

Each user has **one profile with two template sets**, because thumb typing
on glass and finger tracing are different motor skills from a keyboard and
mouse, and one cannot verify the other:

| Set | Used on | Typing compares | Shape |
|---|---|---|---|
| `keyboard` | Laptop or desktop: physical keyboard, mouse or trackpad | Key hold times and gaps between keys | Mouse or trackpad trace |
| `touch` | Phone or tablet | Gaps between key presses only (hold time is unreliable on phone keyboards) | Finger trace |

- Enrol on whichever device you start with; enrolling the other later adds
  its set and keeps the first. Profiles enrolled before this change are the
  keyboard set and need no re-enrolment.
- Verifying on a device type with no set returns `INSUFFICIENT_DATA` (the
  decision engine substitutes another factor), never a pass.
- Typing or tracing with a different input than the challenge asked for
  fails with `INPUT_MISMATCH`. A stylus is refused for now.
- A touch template never records a press-to-press spread below 15% of that
  interval: five samples taken minutes apart underestimate it, and a too-tight
  template rejects its own owner. **Re-enrol touch** for anyone enrolled before
  this rule (their keyboard set is unaffected).
- Touch is scored with the keyboard calibration, marked *provisional*, until
  real touch measurements are fitted with `scripts/measure.py --fit`. Set
  `UEBA_TOUCH_MODE=shadow` to record touch scores without using them.

**Phone keyboards** (Android especially) report every letter as
`"Unidentified"`. The page recovers each character from the change it made
to the text box, so phone typing now lines up with the phrase; before this,
every phone sample matched nothing and enrolment failed.

**Testing on your phone.** Start the service on all interfaces, then open
your Mac's address from the phone on the same Wi-Fi:

```bash
uvicorn api.service:app --host 0.0.0.0 --port 8000
ipconfig getifaddr en0          # the Mac's address, e.g. 192.168.1.23
# on the phone: http://192.168.1.23:8000
```

**Team measurement.** Each person enrols on a laptop and/or a phone. Then,
on at least two different days, everyone verifies on their own account and
on others' accounts, entering their own user ID in *Who is testing*. Then:

```bash
python -m scripts.measure                 # rejection and acceptance rates per input
python -m scripts.measure --fit           # fit touch calibrators from the results
```

---

## Quick start

```bash
uv venv && source .venv/bin/activate        # or: python3 -m venv .venv
uv pip install -r requirements.txt          # or: pip install -r requirements.txt

python -m scripts.train      # calibrate every dimension (~60s)
python -m pytest tests/ -q   # 85 tests

uvicorn api.service:app --reload
```

Open **http://localhost:8000** and hard-refresh (Cmd+Shift+R). Enroll a user,
then run a transaction through the four steps: risk preview, challenge,
verify, record outcome.

**Re-enrol everyone after upgrading.** The shape changed from a zigzag to a
trapezoid, so old shape templates are refused, and old databases lack the
new tables:

```bash
rm -f ueba.db
```

To give a user history so the preview has something to compare against:

```bash
python -m scripts.seed <userId>
```

**Seed before demonstrating.** Without history, the only way to activate
the transactional and session dimensions is to click Submit repeatedly —
which produces a dozen transactions in minutes and correctly trips the
velocity-burst detector. The seed script writes a plausible 75-day history
instead, and prints the amount, device and beneficiary to use for a normal
transaction.

---

## How it works

```
ENROLLMENT
  Type fixed phrase × 8      → keystroke template (mean + std per position)
  Fill the form naturally    → mouse template (passive capture)
                             → session: nothing yet, accumulates

VERIFICATION
  Type the same phrase       → keystroke distance ─┐
  Fill the form              → mouse distance      ├→ calibrate → combine
  Session history            → session distance    │             → risk
  Transaction metadata       → anomaly score      ─┘
```

### Why a template, not a single vector

A single vector gives a mean. Verification needs **variance** too.

If a user's average dwell is 100ms and today's sample is 130ms, is that
them on a tired evening or an impostor? Only the standard deviation
answers that. Templates therefore store mean **and** standard deviation
per position, and enrollment collects 8 repetitions to estimate it.

### Why fixed text

The same phrase at enrollment and verification means timings compare like
for like. Free-text comparison means matching different key pairs against
each other, which is a substantially harder problem needing far more data.

### Why mouse is captured passively

Mouse behaviour is task-dependent — how you move during a tracing exercise
says little about how you move filling a transfer form. Collection happens
during the real enrollment and transaction flows, so enrollment and
verification conditions match.

### Why session behaviour has no enrollment step

It is longitudinal. "How long are your sessions" and "how do you pace
actions" cannot be captured in one sitting. Session behaviour accumulates
across use and carries the same cold-start rule as the transactional
dimension: below the minimum, it reports `INSUFFICIENT_DATA`.

---

## Scoring

Each dimension produces a distance on its own arbitrary scale. A keystroke
distance of 2.0 and a mouse distance of 2.0 do not mean the same thing, so
they are never averaged or weighted directly — that is the same mistake as
an arbitrary 60/40 blend.

Instead each dimension is **calibrated independently** against its own
genuine and impostor distributions, producing a probability. Probabilities
are then combined in log-odds space:

```
logit(P) = Σ logit(P_dimension)
```

This is naive Bayes fusion under conditional independence. It needs no
chosen weights: each dimension contributes in proportion to how confidently
its own calibration separates the classes.

**Impostor data is free.** Every other enrolled user's samples are
impostors for a given user. With *n* enrolled users you get *n* genuine
sets and *n(n−1)* impostor comparisons without collecting anything extra.

**Missing dimensions are skipped**, not treated as zero evidence in either
direction. If nothing can be assessed, the result escalates rather than
allowing — absence of evidence is not evidence of safety.

---

## What changed in 3.0

- **Contract endpoints.** `/risk/preview`, `/factor/challenge`,
  `/factor/verify`, `/profile/commit`, answering in the shared factor format.
- **Missing input is scored, never skipped.** An empty or partial typing or
  trace is blended toward the calibrated mean impostor distance by how much is
  missing. Skipping a whole part of the challenge cannot pass.
- **Backspace replay.** Corrected typos now align correctly. Before, dropping
  backspace events kept the deleted characters and shifted every later
  position against the wrong part of the template.
- **Trapezoid shape** on a pad locked to 2:1, captured with Pointer Events so
  it works as a finger drag on phones. Compared by per-checkpoint splits
  rather than cumulative time.
- **Only approved transactions update the profile**, through
  `/profile/commit`. The preview writes nothing.
- **Single-use challenges** for replay protection without changing the
  phrase.
- **Enrollment** reduced to 6 typings and 4 traces.

## Calibration results

On simulated motor profiles, 40 users:

| Dimension | AUC | EER | Notes |
|---|---|---|---|
| Keystroke | 0.976 | **0.077** | 5 usable samples (was 8): slightly higher error, shorter enrollment |
| Shape trace | 0.937 | **0.123** | Trapezoid, 4 traces (zigzag was 0.196) |
| Mouse | 0.965 | 0.098 | Reported only; did not transfer to real users |
| Session | 0.831 | 0.237 | Preview only |

All figures are on simulated motor profiles and describe the pipeline, not
real-world accuracy. The shape gain comes from three changes made together
(aspect-correct geometry, per-checkpoint splits, the new shape), so it can't
be attributed to the shape alone.

Transactional (150 users, 90 days, 1.15% fraud): PR-AUC 0.70, ROC-AUC 0.97.

The ordering matches expectations from the literature — keystroke is the
strongest signal, mouse weaker, session weakest.

---

## Layout

```
features/
  keystroke.py       dwell, flight, digraph; scaled Manhattan distance
  mouse.py           velocity, acceleration, path efficiency, curvature
  session.py         duration, pacing, idle and burst ratios
  transactional.py   the five per-user transaction dimensions
scoring/
  combine.py         per-dimension calibration, log-odds fusion
models/
  base.py            Model contract: train(ctx) / infer(ctx)
  loader.py          manifest-driven loading with feature validation
  calibrate.py       Platt scaling for the transactional score
  isoforest/         Isolation Forest package
data/
  behaviour.py       simulated motor profiles for keystroke/mouse/session
  synth.py           agent-based transaction generator
api/service.py       FastAPI: enroll, verify, history
store/
  db.py              SQLite persistence
  schema.sql         PostgreSQL equivalent for deployment
static/index.html    React frontend, native event capture
scripts/train.py     calibrate all four dimensions
scripts/measure.py   per-device FRR/FAR from team tests; --fit writes touch calibration
tests/               85 tests (test_touch.py: laptop and phone templates)
```

---

## Design decisions

**No third-party behavioural biometrics library.** Collection is one
subtraction from the browser's own timestamps:

```javascript
onKeyDown: down[key] = performance.now()
onKeyUp:   dwell = performance.now() - down[key]
```

Writing it directly is ~150 lines and avoids any licensing question. The
hard parts — feature extraction, calibration, fusion — are not what those
libraries provide anyway.

**Per-feature contributions are clipped** at 10 standard deviations. One
extreme position (a hesitation, a phone ringing mid-phrase) should raise
the distance, not dominate it. Without this, a feature that is legitimately
constant at enrollment — `idle_ratio` can be exactly zero across every
sample — drives the scaled distance toward infinity.

**Backspace timings are excluded** from the typing rhythm but counted as an
error-rate signal. A correction burst would otherwise swamp the actual
rhythm.

**Hours are circular.** 23:00 and 01:00 are two hours apart, not
twenty-two. Deviation is angular distance from the user's mean direction,
scaled by how consistently they keep a schedule — a user with no preferred
hour cannot have an unusual hour.

**No free text in requests.** Beneficiary names and remarks are
attacker-controlled, needed by no feature, and would open an injection path
into the decision layer. Excluded by construction, not filtered.

**No raw IP storage.** A salted hash supports "same network as before" and
a derived city supports "new location". The address is discarded.

**Biometric templates are derived statistics**, never raw event streams. A
stolen template yields timing distributions, not a replayable recording of
someone typing.

---

## Shape tracing — replacing free-form mouse

Free-form mouse dynamics failed in live testing. The reason is structural:
every free trace is a different path, so only aggregate statistics are
comparable, and within-person variation between sittings exceeded
between-person variation.

Shape tracing applies the constraint that makes keystroke dynamics work.
Keystroke compares position 3 at enrollment against position 3 at
verification because the phrase is fixed. Here the user traces the same
zigzag every time, so progress 40% along the path is comparable to
progress 40% along the path. What remains once the task is held constant
is the person.

Per checkpoint the template stores signed perpendicular deviation from
the ideal line and elapsed time, each with its own standard deviation. The
sign matters: cutting inside a corner and overshooting it are different
behaviours and should not cancel.

Fitts's law supports expecting this to work — movement time is a function
of distance and target width, so holding those fixed leaves the
individual's motor constants as the variation. Free movement confounds
them with the task.

**Measured EER 0.196 on simulated traces.** That is worse than free-form
mouse measured on simulated data (0.098), and the comparison is not the
point: the free-form figure did not survive contact with real users. The
argument for shape tracing is structural rather than numerical, and it
remains unproven until tested on a real cohort.

Speed is excluded from the comparison — it is computed from consecutive
time values, so comparing both double-counts. Across three seeds,
deviation+time gives 0.153 against 0.154 for all three families.

## Findings from live testing

Five problems surfaced during two-person testing. Each fix and its
reasoning is below, since they are more instructive than the code.

**Enrollment learning effect.** A first-time user typing an unfamiliar
phrase is hesitant — reading it, hunting for characters. By verification,
now familiar, they type noticeably faster, so the template captures a
learning phase that no longer represents them. A genuine user scored
0.9976, 0.9983, 0.7642, 0.4497 on four consecutive attempts: improving as
fluency built, which is the signature.

*Fix:* three unrecorded practice rounds, then 10 recorded samples with the
first 2 discarded. Discarding early samples is standard practice in the
keystroke dynamics literature.

**Silent mouse enrollment failure.** One user enrolled without ever
building a mouse template — they typed and pressed Enter without touching
the trackpad, never reaching the 90-point minimum. Nothing in the UI said
so; it was only visible by querying `/user/{id}`.

*Fix:* live pointer counter during enrollment, and an explicit shortfall
report in the response and result panel.

**Mouse false positives.** Genuine users scored 0.90–0.98. The cause was
enrollment splitting a single continuous trace into three chunks — chunks
from one sitting are far more similar than genuinely separate sessions, so
the measured variance was too tight and ordinary later movement looked
anomalous.

*Fix:* one pointer segment per typing round, giving real between-round
variance, plus a wider variance floor (20% of the mean rather than 5%).
Genuine scores dropped from 0.90 to 0.54.

**Cold-start cliff.** `MIN_EVENTS = 10` answered "can I compute a feature?"
but was being used to answer "is this baseline trustworthy?" Those are
different questions. At event 9 the system said nothing; at event 10 it
was fully confident on a baseline of ten samples.

*Fix:* the threshold is now the mathematical floor only. Amount deviation
divides by the user's standard deviation, undefined below two
observations — so learning begins at the first event and scoring at the
third. Everything above that is handled by confidence weighting: the
probability is pulled toward 0.5 in proportion to how thin the baseline
is, reaching full strength at 40 events and 20 sessions. A three-event
baseline scores at 0.075 confidence and contributes almost nothing; a
forty-event one contributes fully.

**Fusion over-confidence.** Two moderately elevated signals produced
0.9976. Log-odds summing assumes conditional independence, but someone
typing hesitantly also moves the pointer hesitantly.

*Fix:* divide the summed log-odds by √n, a standard correction for
correlated evidence. Preserves ordering and the no-weights property while
damping the compounding. The exponent is a judgement, not a measurement.

**One dimension overriding all others.** A genuine user was denied on
keystroke 0.076, session 0.30, transaction 0.79 and mouse 1.0. A
calibrated sigmoid saturates arbitrarily close to 1, and logit(1 − 1e−6)
is about 13.8 — more than enough for one signal to outweigh three.

*Fix:* two changes. Each dimension's log-odds contribution is clipped at
±4, bounding any single signal to roughly p ∈ [0.018, 0.982]. And each
dimension is weighted by its **measured** equal error rate — 1 − 2·EER,
so a perfect separator counts 1.0 and a random one counts 0. The weights
come out at keystroke 0.79, mouse 0.70, session 0.50. Neither is chosen
by hand; both derive from calibration data.

The same case now scores 0.28 (ALLOW) when only mouse misreads.

**Redundant and unhelpful features.** Backward elimination validated on
five held-out seeds showed three dimensions were carrying features that
added noise rather than signal.

Keystroke was comparing dwell, flight *and* digraph latency — but digraph
is arithmetically dwell + flight, so all three double-counted the same
evidence. Dropping digraph took EER from 0.102 to 0.075.

Mouse was comparing all eleven computed features; four of them
(acceleration mean, direction changes, click dwell, click interval) did
better alone, 0.188 to 0.131. **Caveat worth stating:** this selection was
made on simulated traces whose velocity carries heavy per-step jitter,
which may understate how discriminative real velocity is. The literature
treats velocity as a primary mouse feature. All eleven are still computed;
the subset should be re-derived once real traces exist.

Session dropped two features for a marginal gain within one standard
deviation — a simplification rather than a demonstrated improvement.

**Clicks were not segmented with pointer traces.** Enrollment passes one
pointer segment per typing round, but every segment was receiving the
*whole* click list, so `click_interval` measured gaps *between* rounds
rather than within one. Two of the four compared mouse features are
click-based, and this alone scored a genuine user at 0.9998.

*Fix:* clicks are matched to their segment by timestamp. Finding it also
exposed a flaw in the generator — each synthetic sample restarted its
clock at zero, so segments overlapped in time, which a browser's
monotonic `performance.now()` would never do. The generator now advances
the clock between samples.

**Mouse `path_efficiency` is task-dependent.** Enrollment measured a mean
of 0.094 — the pointer travelling ten times the direct distance. During
enrollment the user moves idly while typing and ends near where they
started; during a transaction they move purposefully toward the submit
button. Same person, structurally different movement.

*Fix, in two stages.* It was first excluded from the compared set. Then
the submit button was randomised in position for every enrollment round
and at verification, so both flows require a purposeful traversal rather
than idle drift. That made the feature comparable again — though backward
elimination subsequently dropped it anyway on measured grounds.

The randomisation earns its place regardless: ten rounds with the button
in ten places produce ten genuinely different pointer paths, which is what
the template needs to estimate variance. With a fixed button all ten
segments looked alike.

## Limitations

- **Simulated motor profiles.** The calibration data comes from a generator
  with per-person typing and pointer characteristics, not from real people.
  Results establish that the pipeline works, not the accuracy it would
  achieve in deployment. Real collected samples replace these without any
  code change.

- **Independence assumption.** Log-odds fusion assumes dimensions are
  conditionally independent. They are not exactly — someone typing quickly
  also tends to move the pointer quickly. This inflates confidence when
  several dimensions agree. Measuring the correlation needs more enrolled
  users than a prototype cohort provides.

- **Mouse dynamics do not work on touch devices** and need more data than
  keystroke dynamics to be reliable. Treated as a supporting signal.

- **Mouse is sensitive to input device.** Trackpad and mouse produce
  distinct motor signatures — trackpads involve finger drags with pauses at
  swipe boundaries, mice produce smoother continuous motion. A user
  enrolling on one and verifying with the other will register a large
  distance. The system does not distinguish input device type.
  Device-conditional templates are future work.

- **Confidence weighting mutes fraud detection on new accounts.** The
  transactional dimension is deliberately weak while the baseline is thin,
  which reduces false positives on new users and makes fraud against a new
  account harder to catch. That is the right trade for a step-up signal —
  a false positive costs a challenge, a miss costs money caught elsewhere —
  and the wrong trade for automatic blocking.

- **The transactional calibrator is fit on the synthetic generator's
  distribution.** Events arriving through the service with different
  characteristics may score higher than they should until the model is
  retrained on the real event stream.

- **GeoIP is stubbed.** `derive_city()` returns a placeholder.

- **SQLite for the harness.** `store/schema.sql` holds the PostgreSQL
  equivalent; access patterns are identical.

---

## Attribution

Transactional dimension derived from
[prajwalnayaka/UEBA](https://github.com/prajwalnayaka/UEBA) (MIT).
Architecture patterns informed by
[GACWR/OpenUBA](https://github.com/GACWR/OpenUBA) (Apache 2.0), no code
copied. Keystroke, mouse and session dimensions are original work using
native browser events. See `NOTICE`.

Licensed MIT.
