# Branch integration ledger

`integration` is a rewrite of the factor branches into the `amfa/` layout, not a git merge.
This file records, per branch, which commit was last ported and what was deliberately left out.
Re-check with `git log integration..origin/<branch>` after every port.

"Integrated" means the branch's behaviour is covered by tests in `tests/` that pass against `amfa.*`.

| Branch | Integrated up to | Status |
|---|---|---|
| `Research/face-auth` | `46244ad` | done, see below |
| `ueba-service` | `493ef30` (from the squash) | pending: `6e2a180`, `23910e1` |
| `voice-auth` | `3b9ea79` (from the squash) | pending: `14273a7`, `c706875` |

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
