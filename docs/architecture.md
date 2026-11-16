# Architecture: independent services, end-to-end encryption, and where each model runs

This page says what is built today, what each part can be upgraded without touching the others, and what is still to do.
It also answers one design question that came up: should the face, voice and typing models run on the phone?

## 1. The pieces

```
 phone app / web SDK                      bank backend
        |  (E2E box inside HTTPS)               |  (signed API, tenant key)
        v                                       v
   +---------------------------- edge (Caddy: TLS, rate limits) -----------------------------+
   |                                                                                          |
   |   AMFA core  --- decides the plan, owns sessions/proofs/audit ---  risk engine (LightGBM)|
   |      | signed internal calls (HMAC, per-request)                                         |
   |      +--> face service  (8101)   detection, match, liveness (PAD + custom veto)         |
   |      +--> voice service (8102)   speaker match, anti-spoof, phrase check                 |
   |      +--> ueba service  (8103)   typing, touch, line-tracing, replay guard              |
   +------------------------------------------------------------------------------------------+
   each service: own container, own Postgres/Redis (factor data is not in the core's database)
```

| Piece | Owns | Talks to | Scales by |
|---|---|---|---|
| **Core** (`amfa/core`, `amfa/engine`) | tenants, customers, sessions, plans, proofs, audit, the risk model | factor services, the bank (signed webhooks), the phone | more replicas behind the edge; Postgres + Redis |
| **Face** (`amfa/factors/face`) | face templates, liveness models | core only | more replicas (CPU-bound; stateless apart from its own store) |
| **Voice** (`amfa/factors/voice`) | voiceprints, anti-spoof models | core only | same |
| **UEBA** (`amfa/factors/ueba`) | typing / touch / shape profiles | core only | same |
| **Bank / demo bank** | accounts, money | core's tenant API | the bank's own |
| **Phone app** (`mfa-android`) | capture, coaching, encryption, the phone's own secure-hardware key | bank, core's customer API | the phones themselves |

The core never imports a factor's code. It calls it over HTTP with a signed request (`amfa/core/factors.py`) and gets back a
`FactorResult` (`amfa/common/contract.py`). That contract is the seam: anything that speaks it can replace a service.

## 2. What can be upgraded alone

| You want to change | What you touch | What you do not touch |
|---|---|---|
| A **liveness / match / voice model** | the factor service image (or its model directory) | core, the other factors, the app, the bank |
| The **risk model** | train a new version; the `active` pointer file selects it, `active()` reads the pointer on every call, so a switch (or a rollback to the old version) needs no restart | everything else |
| **Rules / thresholds / which check at which level** | the tenant policy (JSON, per bank) | code |
| **Add a factor** (say, a hardware key) | a new service that answers the same contract + one entry in the core's factor table | existing factors |
| **One bank's behaviour** | that tenant's policy (`requireE2E`, OTP methods, branding) | other banks |
| The **app** | a new APK; the server still accepts older ones because requests only add fields | the server |

Rules that keep this true (and that new code should keep):

1. **Contracts are additive.** Add fields; do not rename or remove them. A service reports its model version in its health
   answer and in each result, so the core's audit trail records which model decided.
2. **One owner per datum.** Biometric templates live only in their factor service's store. The core holds ids and results.
3. **Each service ships on its own.** Own image, own CI job, own health check (`/health`, and the core's `/healthz`), own database.
   `deploy/docker-compose.yml` already runs them as separate containers; production orchestration (Kubernetes, Nomad) maps one
   to one onto it.
4. **Roll out models like code.** New model version → run it in shadow (score, do not act) → compare with the old one on the
   regression suite (`docs/regression.md`) → switch the pointer for a share of tenants → the rest. The risk model already has
   a release gate (accuracy and fairness); the face/voice models should gain the same gate before a switch.

Not done yet (and what it needs): per-service CI jobs and image tags; a model registry with signed manifests for the three
factor services (the risk model has versioned directories and a pointer, the others load a fixed directory); canary by tenant
percentage; a queue in front of the factor services for bursts (today the core calls them synchronously, which is the right
default for a check a person is waiting on, but needs a bounded queue and a timeout fallback for spikes).

## 3. End-to-end encryption (built)

HTTPS ends at the first thing that terminates it: the tunnel, a load balancer, a proxy, a CDN. Pictures, recordings and typing
should not be readable there. So the phone encrypts them to AMFA's own key first (`amfa/common/e2e.py`, `E2E.kt`):

* ECIES: P-256 ECDH with a fresh key per message, HKDF-SHA256, AES-256-GCM. Platform crypto on both sides; no new dependency.
* Each box is **bound** (as GCM associated data) to its use: `sessionId|factor|challengeId` for a check, and
  `enroll|SHA-256(enrolment token)|factor` for setup. A captured box cannot be replayed into another session, challenge,
  factor or enrolment.
* The server's key pair is derived from the master key of the active version, so key rotation follows the existing master-key
  rotation and boxes made under a still-kept older version keep opening. The public key is at `GET /c/v1/e2e-key`.
* One-time codes stay plain (they are not biometric and the code is one-use).
* A bank can set `requireE2E` in its policy: the core then refuses plain biometric submissions (`E2E_REQUIRED`).
* The app **fails closed**: if it cannot get the key, or the key's fingerprint changed since first use, it sends nothing.

What this does and does not stop:

| Threat | Stopped? |
|---|---|
| A tunnel, load balancer or CDN reading pictures/voice | Yes |
| The bank (or anyone holding the bank's API key) reading the biometric payload in transit | Yes: the bank's servers never see it |
| Capturing a box and replaying it | Yes (bound to session, challenge and factor; the core also keeps its own replay guards) |
| A box changed in flight | Yes (GCM) |
| A proxy that **rewrites the key endpoint** on the very first connection | **No, not by itself.** The app pins the fingerprint on first use, so later swaps are caught, but the first use is trusted. Fix: ship the fingerprint inside the app build (or a bank-provisioned config) so even the first connection is checked. |
| Malware on the phone | No (it can read pictures before they are encrypted). That is what device attestation is for (section 4). |

Not done: the web SDK (`amfa.js`) still sends plain payloads over HTTPS; it needs the same box using WebCrypto
(`crypto.subtle` supports ECDH P-256, HKDF and AES-GCM).

## 4. Should the models run on the phone?

Short answer: running them on the phone can make a check **feel faster**, but it does not make it **more accurate**, and it
makes it **less trustworthy**. The right split is by job.

| Job | Where | Why |
|---|---|---|
| Face-in-frame, head pose, lighting and "turn more" coaching | **Phone** (built: ML Kit, `PoseCoach`) | Instant feedback needs no network; a wrong answer only annoys, it cannot approve anything |
| Quality gates (blur, too dark, no face) before uploading | **Phone** | Saves a round trip and an upload |
| Face match, liveness, voice match, anti-spoof, typing match | **Server** | The decision must not be changeable by whoever holds the phone |
| Deciding which check to ask (the risk model, the rules) | **Server** | Needs history across devices, tenant policy and fraud signals the phone cannot see; also must not be steerable by the person being checked |

Why the verdict stays on the server:

* **The server must trust the answer.** If the phone says "face matched", an attacker with a rooted phone or a modified app
  says it too. A server-side check cannot be bypassed by changing the app. An on-device verdict is only as trustworthy as the
  device, and proving that needs hardware-backed attestation (Play Integrity or Keystore key attestation) plus a signed
  result, which is its own project.
* **Models on a phone can be copied.** An attacker can pull the model out of the APK and train attacks against it offline.
* **Upgrades slow down.** A model baked into an APK moves at app-release speed (store review, users who never update). A
  server model upgrades for everyone in minutes, and can be rolled back the same way.
* **Phones vary.** A model that is fast on a new phone is slow, hot, or wrong on a cheap one; the server's hardware is known.
* **Data stays checkable.** Server-side checks are logged and measured per model version, which is what the regression and
  fairness gates need.

Where the worry (slowness under traffic) is real, it is answered on the server, not by moving the model:

* factor services scale horizontally and are stateless apart from their store; add replicas before traffic needs them;
* the phone already compresses and caps frames; send fewer, better frames (coaching makes the first burst usable);
* keep the check off the hot path: low-risk payments take the low-touch rule and need no biometric at all;
* put a timeout and a fallback on the factor call (fall back to a code) so a slow service never leaves a person waiting;
* measure: record p50/p95 per factor and per model version, and scale on that.

If a fast path on the phone is wanted later, the safe shape is **optional and advisory**: the phone runs a small model and
sends its result with a hardware-attested signature; the server still runs its own check when the risk is not low, and uses
the phone's result only to skip a check at the lowest risk levels. That needs attestation first, so it is not built.

## 5. Order of work from here

1. Pin the server key fingerprint inside the app build; same encryption in the web SDK.
2. Per-service CI and image tags; a manifest with model version, hash and release-gate result for the face, voice and UEBA models.
3. Timeouts, fallbacks and p95 metrics on the factor calls; autoscale on them.
4. Shadow mode and percentage rollout for model switches.
5. Device attestation, then (only if measured latency still needs it) the advisory fast path in section 4.

## 6. Data kept: 90 days

Transactions and everything derived from them are deleted after 90 days (`amfa/core/retention.py`, `AMFA_RETENTION_DAYS`), and the risk model reads no more
than that. See [customer-limit.md](customer-limit.md) for what goes and what stays, and what the shorter memory costs.

## 7. What the core keeps per customer, and for how long

Besides sessions, plans, proofs and the audit log, the core keeps a few small tables that make decisions personal. None holds a
biometric, a name, an account number or an exact location; devices, payees and places are keyed hashes.

| Table | What it holds | Module |
|---|---|---|
| `customer_phase` | whether the model knows this customer yet, and the evidence behind it (marks, never transactions) | `core/phase.py`, `engine/risk/readiness.py` |
| `customer_profile` | the optional sign-up answers as bands, and up to three bank-vouched regular payees | `core/api_tenant.py`, `engine/risk/declared.py` |
| `peer_group_stat` | how customers with the same answer really pay, kept only for groups of 50 or more, refreshed nightly | `core/peers.py` |
| `known_payee` | which customers paid which payee and when: the basis of payee reputation (mule detection) | `core/payee_reputation.py` |
| `bypass_limit` | a customer's own "no extra checks up to X a day", with a raise waiting out its cooling-off | `core/bypass.py` |
| `trusted_helper` | a family member's phone the customer chose (a label and a keyed hash), with an invite code kept only as a hash | `core/helpers.py` |

Every one of these is optional per bank (`learning`, `payeeReputation`, `customerBypass`, `familyHelpers` in the tenant policy) and
off by default. Transactions and everything derived from them are deleted after 90 days (hourly, and `python -m amfa.cli purge`;
`AMFA_RETENTION_DAYS`), and the risk model refuses to load if trained on a longer window. See
[customer-limit.md](customer-limit.md) for what goes and what stays, [new-customer-learning.md](new-customer-learning.md) for the
learning phase and [family-helper.md](family-helper.md) for helpers.

The risk model is one model for everyone; what is personal is the history it is given. Its release gate (beats a logistic-regression
baseline and a per-segment fairness ratio of at most 1.25) currently fails for older customers (about 2.3), so the model in use was
activated by hand. [dataset-evaluation.md](dataset-evaluation.md) tests it on five different simulated banks.
