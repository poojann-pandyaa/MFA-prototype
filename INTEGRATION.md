# Branch integration ledger

`integration` is a rewrite of the factor branches into the `amfa/` layout, not a git merge.
This file records, per branch, which commit was last ported and what was deliberately left out.
Re-check with `git log integration..origin/<branch>` after every port.

"Integrated" means the branch's behaviour is covered by tests in `tests/` that pass against `amfa.*`.

| Branch | Integrated up to | Status |
|---|---|---|
| `Research/face-auth` | `46244ad` | done, see below |
| `ueba-service` | `23910e1` | done, see below |
| `voice-auth` | `c706875` | done, see below |

## voice-auth @ c706875

The 1:1 path (ECAPA-TDNN match at 0.25, RawNet2 PAD at log 0.5, same RawNet2 model file) was already
in the squash and is unchanged. Ported (`amfa/factors/voice/{gallery,metrics}.py`, `service.py`,
`models.py`, `amfa/factors/base.py`):

| Branch change | In integration |
|---|---|
| `14273a7` 1:N open-set identification (`SpeakerGallery`, `identify`) | `gallery.py`. The accept/reject decision is a pure function, `identify(gallery, probe, threshold, margin, top_k)`: top-1 >= 0.30 and top-1 minus top-2 >= 0.05 |
| `c706875` `top_k` must not weaken the decision | the top-2 score is always retrieved (`max(top_k, 2)`); two regression tests fail if that line is changed |
| `metrics.py` (cosine, CMC, rank-N, open-set DIR/FPIR/FNIR, effective false-match rate) | `metrics.py`, taken as-is |
| `pipeline.identify_factor` (PAD, then gallery) | `POST /factor/identify` on the voice service: PAD first (terminal by default, so a spoofed probe never searches), then a search over the tenant's enrolled voiceprints. `TemplateStore.all()` lists a tenant's sealed templates; templates made by a different embedding model are skipped |

New settings: `AMFA_VOICE_IDENT_THRESHOLD` (default 0.30, never below the 1:1 threshold) and
`AMFA_VOICE_IDENT_MARGIN` (default 0.05).

The endpoint is internal (signed by the core) and names other customers, so it must never be
exposed to a customer. **Nothing calls it yet.** The intended use is a duplicate-voice / mule-ring
check at enrolment. Whether a match should block, flag for review, or only be logged is a product
decision, so no enrolment behaviour changed. The search decrypts every template of the tenant per call;
cache the stacked matrix before using it on a large tenant.

Left out on purpose:

| Branch item | Why |
|---|---|
| RawNet2 as a hard gate in the 1:1 flow | integration made PAD `advisory` by default (a spoof verdict flags but does not reject; two flags block). This is a policy choice, so the default was not changed; set the tenant's `padModes.VOICE` to `terminal` to get the branch's behaviour |
| `VoiceBiometricPipeline` / `SpeakerVerifier` classes, demos, `docs/`, `results/` | integration's service replaces the pipeline; the branch's evaluation results are reference material |
| Branch tests for the pipeline, ECAPA and RawNet2 wrappers (`test_pipeline`, `test_ecapa`, `test_rawnet2`, `test_workspace_integrity`) | they test the branch's classes; they need the model weights, which the build sandbox cannot download |

Tests: `test_gallery.py` and `test_metrics.py` ported unchanged apart from imports;
`test_identification.py` re-expresses the branch's identification tests against `identify`;
`test_service.py` exercises `/factor/identify` with a scripted speaker model and PAD.
Not verified: a run against the real ECAPA and RawNet2 weights, and identification accuracy on real
voices (the thresholds 0.30 / 0.05 are the branch's, measured on its own data).

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
