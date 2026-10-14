# Branch integration ledger

`integration` is a rewrite of the factor branches into the `amfa/` layout, not a git merge.
This file records, per branch, which commit was last ported and what was deliberately left out.
Re-check with `git log integration..origin/<branch>` after every port.

"Integrated" means the branch's behaviour is covered by tests in `tests/` that pass against `amfa.*`.

| Branch | Integrated up to | Status |
|---|---|---|
| `Research/face-auth` | `46244ad` | done, see below |
| `ueba-service` | `23910e1` | done, see below |
| `voice-auth` | `3b9ea79` (from the squash) | pending: `14273a7`, `c706875` |

## ueba-service @ 23910e1

Ported (`amfa/factors/ueba/{features,scoring,data,training}`, new `dimensions.py`, `service.py`,
`amfa/core/static/js/enroll.js`, `amfa/core/static/css/hosted.css`):

| Branch change (`6e2a180`) | In integration |
|---|---|
| Every challenge answer is scored; partial or empty answers are blended toward a typical impostor by how much is missing | `keystroke.py`, `shape.py` and `combine.blend` taken from the branch; `dimensions.py` holds the challenge scoring |
| Typing is aligned by replaying backspaces, so one corrected typo no longer shifts every later key | `keystroke.align`. A wrong or short phrase is no longer rejected as `PHRASE_MISMATCH`; it is scored by coverage |
| Skipping part of the challenge cannot be passed on the other part | `service._verdict`: `CHALLENGE_MISSING` / `CHALLENGE_INCOMPLETE`. Shape is scored whenever the challenge asked for it (`req.challenge["shape"]`), so leaving the trace out now fails; before, it was skipped silently |
| Enrolment: 6 typings, the first discarded, 5 used; 4 shape traces; incomplete ones excluded | `build_template`; enrolment reports `PHRASE_INCOMPLETE` per rejected typing |
| New shape: 4-point trapezoid on a 2:1 pad | `sh.SHAPE` / `sh.ASPECT`; the page's trace canvas is now 2:1 and `/enroll/phrase` returns `aspectRatio` |
| Typo simulator and `train.py` fixes | `data/behaviour.py`, `training/train.py` |

Integration's own choices kept (decided with the team): factor name `UEBA`, threshold `0.5`,
reason code `BEHAVIOURAL_MISMATCH`, the `/factor/enroll` + `/factor/verify` + `/risk/preview` shape.
The replay guard now only applies to complete answers, so repeating an empty answer is not
reported as a replay.

Left out on purpose:

| Branch item | Why |
|---|---|
| `api/contract.py` typed models, `/factor/challenge`, `/profile/commit`, `CONTRACT.md` threshold 0.70 | integration has its own contract (`amfa/common/contract.py`) and issues challenges from the core |
| `store/db.py` verification table, `seed.py`, `static/index.html` demo | integration stores sealed templates and events through `store_for`; the core keeps the audit trail |
| `preview_flags` (device/location/payee seen before, hour, dormancy, `velocityHigh`) | `core/context_builder.py` computes these itself from the tenant's own history. `velocityHigh` is not used by the rules yet; candidate input for the decision model |
| Passive `score_session` / `score_transaction` in `dimensions.py` | unchanged by `6e2a180`; integration keeps its inline version because its artifacts are laid out differently |
| `test_contract.py` (30 tests of the branch's contract) | tests the branch's API; the equivalent behaviour is covered by `tests/factors/ueba/test_service.py` |

Tests: `test_features.py` ported unchanged apart from imports; `test_service.py` runs the real
service against a small model trained once per session (about 10 s).
Thresholds not re-measured on real users: all behaviour results use simulated typists and tracers.

## Research/face-auth @ 46244ad

Ported (`amfa/factors/face/gesture.py`, `service.py`, `matcher.py`, `amfa/core/orchestrator.py`,
`amfa/core/static/js/challenge.js`):

| Branch commit | In integration |
|---|---|
| `46244ad` turn must pass through in-between poses | `gesture.check_turn`: at least 2 frames 15–85 % of the way between the start and end pose |
| `2fc3979` turn measured relative to the face | `gesture.nose_offset`: nose offset from the eye midpoint along the eye axis, in inter-eye units, so it ignores translation, scale and tilt |
| `2fc3979` / `2f6f4a1` gesture and identity evidence only from PAD-live frames | `service.verify`: PAD-rejected frames never enter the turn check or the match; every live frame must match the probe |
| `2fc3979` uniform failure message | `orchestrator._client_result`: all turn, liveness and identity failures return one message |
| burst cadence | server asks for 12 frames at 150 ms and the page uses `intervalMs` (was 8 frames at 250–400 ms) |
| `1c9c887`, `2de3f69` | already equivalent (single-step flow, CSS-mirrored preview) |

Left out on purpose:

| Branch item | Why |
|---|---|
| ArcFace ONNX matcher, MediaPipe `face_landmarker.task` | integration uses YuNet + SFace; its 5-point landmarks are enough for the turn measure |
| Blink challenge | integration only issues head turns; port with a landmark model if wanted |
| `pad_custom.onnx` (MobileNetV3 PAD) | needs a new runtime wrapper and a re-measured threshold on this pipeline; no labelled data here to measure it. Integration keeps the two MiniFASNet models it shares with the branch |
| `ml/` training pipeline | only needed once the PAD model is retrained |
| `8301188` `MFA_ALWAYS_REQUIRE_FACE_CHECK` | per-tenant policy (`requireFactors`) already does this |

Not verified here: a run against the real YuNet/SFace/MiniFASNet weights. The model download was
blocked in the build sandbox (HTTP 403), so the service tests use a scripted detector and PAD.
Run `python scripts/fetch_models.py` and a demo-bank transaction on a machine with network access.
Thresholds changed in behaviour, not value: `FRONTAL_MAX_YAW` 0.12 and `TURN_MIN_DELTA` 0.18 are
now in inter-eye units of the eye-axis projection, so re-check them against real captures.
