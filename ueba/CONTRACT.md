# UEBA — Integration Contract

How the orchestrator and decision engine talk to the behavioural service.
The `/factor/verify` response is the shared factor contract used by every
factor service (face, voice, behavioural, OTP).

Factor name: **`BEHAVIOURAL`** (set with `UEBA_FACTOR_NAME`; must match the
decision engine's fixed enumeration).

## The flow

```
1. POST /risk/preview      decision engine input, no user interaction
2. GET  /factor/challenge  only if the engine selected BEHAVIOURAL
3. POST /factor/verify     the user's typing and trace
4. POST /profile/commit    orchestrator reports how the session ended
```

Steps 1 and 4 happen for every transaction. Steps 2 and 3 happen only when
the plan includes this factor.

---

## 1. `POST /risk/preview`

Raw identifiers stop here. The IP address and device ID reach only this
service; the decision layer receives flags.

```json
{
  "userId": "u_1042",
  "transactionId": "txn_30491",
  "amountPaise": 20000000,
  "payeeId": "b_88",
  "deviceId": "d_x91",
  "ip": "103.21.45.67",
  "timestamp": "2026-10-04T03:15:00Z",
  "channel": "MOBILE",
  "action": "TRANSFER",
  "session": { "start": 1791100000, "end": 1791100240, "actions": [1791100010] }
}
```

`amountPaise` is an integer; fractional paise are rejected (422). `session`
is optional. No free-text fields are accepted: payee names and remarks are
ignored if sent.

```json
{
  "factor": "BEHAVIOURAL",
  "kind": "RISK_PREVIEW",
  "status": "OK",
  "band": "HIGH",
  "riskScore": 0.87,
  "flags": {
    "deviceSeenBefore": false,
    "locationSeenBefore": false,
    "payeeSeenBefore": false,
    "hourUnusual": true,
    "velocityHigh": false,
    "dormant": false
  },
  "dimensionsUsed": ["transaction", "session"],
  "confidence": 0.9,
  "modelVersion": "ueba-behavioural-2.0",
  "source": "live",
  "latencyMs": 38,
  "detail": { }
}
```

| `status` | Meaning | `band` |
|---|---|---|
| `OK` | At least one dimension scored | from the score |
| `INSUFFICIENT_DATA` | No approved history yet | always `HIGH` |
| `UNAVAILABLE` | Service error | always `HIGH` |

`hourUnusual` and `dormant` are `null` until enough history exists to judge.
The preview **writes nothing** to the user's history.

## 2. `GET /factor/challenge?userId=u_1042`

```json
{
  "challengeId": "ch_8f2a91c04d7e3b15",
  "factor": "BEHAVIOURAL",
  "phrase": "the quick brown fox 2026",
  "shape": [[0.15, 0.8], [0.323, 0.2], [0.677, 0.2], [0.85, 0.8]],
  "aspectRatio": 2.0,
  "expiresAt": "2026-10-04T10:15:00Z",
  "shapeRequired": true
}
```

The phrase and shape are fixed by design. The challenge ID is single-use and
expires after five minutes.

**Client requirements:** draw the shape on a pad locked to `aspectRatio`
(2:1), and capture the trace with Pointer Events so it works with mouse,
trackpad and finger. Report `x` and `y` normalised 0–1 to the pad.

## 3. `POST /factor/verify`

```json
{
  "sessionId": "s_77",
  "userId": "u_1042",
  "transactionId": "txn_30491",
  "challengeId": "ch_8f2a91c04d7e3b15",
  "payload": {
    "keystroke":  [{ "key": "t", "down": 1000.0, "up": 1098.4 }],
    "shapeTrace": [{ "x": 0.15, "y": 0.80, "t": 2000.0 }],
    "pointer":    { "points": [], "clicks": [] },
    "capability": { "pointer": true, "touch": false }
  }
}
```

Send keystroke events including `Backspace`, exactly as captured. Do not
filter or correct them client-side.

```json
{
  "factor": "BEHAVIOURAL",
  "status": "PASSED",
  "score": 0.86,
  "threshold": 0.70,
  "livenessPassed": null,
  "livenessScore": null,
  "failureReason": null,
  "modelVersion": "ueba-behavioural-2.0",
  "source": "live",
  "latencyMs": 42,
  "detail": {
    "keystroke": { "status": "OK", "coverage": 1.0, "probability": 0.08 },
    "shape": { "status": "PARTIAL", "coverage": 0.62, "probability": 0.31 },
    "mouse": { "status": "OK", "probability": 0.91, "excludedFromScore": true },
    "dimensionsUsed": ["keystroke", "shape"],
    "impostorProbability": 0.14
  }
}
```

**Score:** `1 − P(impostor | keystroke, shape)`. Higher means more confident
it is the enrolled user. Transaction risk is not counted here; it already
shaped the plan through the preview.

**Threshold 0.70.** The orchestrator's marginal-pass rule (cleared by under
15%) reads 0.70–0.805 as marginal.

| Situation | `status` | `failureReason` |
|---|---|---|
| score ≥ threshold, nothing skipped | `PASSED` | `null` |
| score below threshold | `FAILED` | `BEHAVIOUR_MISMATCH` |
| part of the challenge partial, score below threshold | `FAILED` | `CHALLENGE_INCOMPLETE` |
| typing or trace skipped entirely | `FAILED` | `CHALLENGE_INCOMPLETE` |
| nothing submitted | `FAILED` | `CHALLENGE_MISSING` |
| user not enrolled | `INSUFFICIENT_DATA` | `NO_TEMPLATE` |
| service error | `UNAVAILABLE` | `SERVICE_ERROR` |

UEBA **never returns `SPOOF_DETECTED`** and never reports liveness: it has no
presentation-attack detector.

Per-part status in `detail`: `OK` (coverage ≥ 0.9), `PARTIAL`, `MISSING`, or
`NOT_APPLICABLE` when the device has neither pointer nor touch, in which case
typing alone decides.

**Protocol errors** are HTTP errors, not factor results:
`409 challenge rejected: USED | EXPIRED | WRONG_USER | UNKNOWN`.

## 4. `POST /profile/commit`

```json
{ "transactionId": "txn_30491", "outcome": "APPROVED" }
```

```json
{ "transactionId": "txn_30491", "outcome": "APPROVED", "written": true,
  "reason": null, "eventsNow": 47, "sessionsNow": 15 }
```

`outcome` is `APPROVED`, `BLOCKED`, `EXPIRED` or `MANUAL_REVIEW`. Only
`APPROVED` writes the transaction into the user's history, so a failed or
fraudulent attempt never becomes part of their normal behaviour. Commits are
idempotent; `404` if no preview was recorded for the transaction.

---

## What never leaves the service

Templates, per-position means and deviations, raw keystroke timings, IP
addresses and device IDs. Responses carry scores, coverage and statuses only.

## Deprecated

`POST /verify` still answers with all dimensions combined, for clients not
yet moved over. It now writes history only when its result is `ALLOW`.
