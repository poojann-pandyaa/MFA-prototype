# Customer app redesign: Demo Bank as one seamless, friendly banking app

Date: 15 October 2026 · Branch: `customer-app` · Approved mockup: https://claude.ai/artifact/H1Y9w5NqgujELsaxjMswyv

## Goal

Make `examples/demo_bank` feel like a real, modern Indian banking app that a first-time user understands
without help, while every action still goes through AMFA exactly as today. The same app must also let a
presenter show what AMFA decided and why.

## Decisions (agreed with Poojan)

| Topic | Decision |
|---|---|
| Audience | A realistic customer app, plus a demo mode with a "Behind the scenes" drawer |
| Scope | The bank app and the AMFA setup and verification pages, made to look like one app |
| How AMFA pages join the app | Approach A: AMFA keeps capturing face, voice and codes on its own pages; they take the bank's name and colour, drop their own header when embedded, fill the screen, and report step progress to the bank app |
| Setting up security | Progressive: at sign-up only SMS codes (automatic from the mobile number) and face; voice, typing and an authenticator app are added later from "Add" buttons |
| Look | Clean modern neobank: light, white cards, deep teal `#0B6E63` brand colour, amber only for warnings, large numbers, rounded icons, bottom sheets; dark mode follows the phone |

## Screens and flows (as in the mockup)

```
Welcome ─► Open account (name, mobile, username, password) ─► Protect your account ─► Face setup ─► Home
                                                        Sign in ─────────────────────────────────────┘
Home ─► Pay ─► Who are you paying? ─► Amount ─► Review ─► Confirm it's you (AMFA steps, 1 of N …) ─► Sent
                       │                                        └─ held by AMFA ─► Held for review
                       └─ New payee ─► details ─► Confirm it's you ─► Payee added
Home ─► Activity ─► item ─► receipt · cancel (pending) · "This wasn't me" (completed)
Home ─► Profile ─► Security: protection level, Add voice / typing / authenticator, trusted devices,
                    daily limit, mobile/email/password, Freeze my account
Unknown device after sign-in ─► "Verify this device" (DEVICE_BINDING) before anything else
Demo mode ─► floating "Behind the scenes" button on every signed-in screen ─► drawer
```

Bottom navigation: Home, Pay, Activity, Profile. Every protected action uses the same three-part shape:
**Review** (what, to whom, which checks are coming and why, scam warning when AMFA asks for it) →
**Confirm it's you** (full screen, one AMFA step at a time with "1 of 2" progress) → **Result** (Sent,
Saved, Held for review, Not sent with a plain reason and what to do next).

Statuses shown to customers: Sent / Saved, Waiting for you, Being reviewed, Not sent (check failed),
Expired, Cancelled. Scores, threat levels and model reasons never appear outside demo mode.

## Architecture

### 1. Bank app front end (`examples/demo_bank/static/`)

Native ES modules, no build step, no framework, CSP unchanged (`script-src 'self'`):

```
static/index.html          shell: <main id="screen">, bottom nav, sheet + toast hosts
static/app.css             tokens (light + dark), layout, components
static/js/app.js           boot, hash router (#/home, #/pay, #/pay/amount, #/review/<id>, #/verify/<id>,
                           #/result/<id>, #/activity, #/security, #/welcome, #/signup, #/signin, #/setup),
                           state (me, activity), polling of pending items
static/js/api.js           fetch wrapper, error → friendly message
static/js/ui.js            components: header, list row, avatar, amount, keypad, chip, sheet, toast, empty state
static/js/amfa-frame.js    full-screen host for AMFA pages: iframe, amfa:* messages, step progress header
static/js/screens/*.js     one file per screen: welcome, signup, signin, setup, home, pay, amount, review,
                           verify, result, activity, security, newpayee, limit, contact, password, demo
```

Each screen module exports `render(container, params)` and builds DOM with `textContent` only. The amount
keypad is our own buttons (no OS keyboard jump); payee search filters client-side.

### 2. AMFA hosted pages (`amfa/core/static/`, `amfa/core/api_client.py`, `amfa/engine/policy.py`)

- **Theme:** `TenantPolicy.branding = {color: "#RRGGBB"}` (validated hex, default AMFA blue).
  `GET /c/v1/enrollment` and `GET /c/v1/session` return `theme: {bank, color}`; the pages set `--brand`
  (and a computed `--brand-ink`, white or dark by contrast) at load.
- **Embedded mode:** the bank opens AMFA pages with `&embed=1`. In that mode the page hides its brand row,
  title card and "approving" card (the bank app shows those itself), drops the outer card borders, and posts
  `{type: "amfa:step", flow: "enroll"|"session", index, total, factor}` on every step change, alongside
  the existing `amfa:status`, `amfa:enrolled` and `amfa:device` messages.
- **Restyle `hosted.css`** to the mockup's language: 56 px buttons, 16 px radius, larger headings, camera as a
  circle with a segmented progress ring (setup: 5 segments, one per picture; verification: fills as frames
  are taken), turn arrow, "Picture 3 of 5". OTP: six digit boxes with `autocomplete="one-time-code"`,
  paste support and an expiry countdown. Voice: one large record button with a level meter.
- Progressive setup needs no core change: links already take a `factors` list; with SMS delivery enrolled
  (the customer's mobile), a `["FACE"]` link gives consent → face → device → done.

### 3. Bank server (`examples/demo_bank/bank.py`)

- `POST /api/register` additionally stores the name for avatars; unchanged otherwise.
- `POST /api/amfa/enrol {factors?}`: default `["FACE"]`; Security's Add buttons send `["VOICE"]`,
  `["UEBA"]` or `["OTP"]` (authenticator app). Returns the AMFA URL with `&embed=1`.
- `GET /api/me` adds `protection: {level: "Basic"|"Good"|"Strong", have: [...], canAdd: [...]}` (SMS code
  and authenticator count as one "code" method; Good = 2 methods, Strong = 3+), `recentPayees` (last paid
  first, from activity), and `devices` (count, and whether this one is trusted).
- Action records keep AMFA's plan privately (`decision`: threat level, model level, score, reasons with
  their plain text, checks, rules baseline, cap reason, latency); `public()` still never exposes it.
  The public view gains `payee` (display name, initials) and `confirmedWith` (factors from the proof's
  `amr` claim) for receipts.
- `GET /api/demo/last-decision` (demo mode only): the latest action's `decision`.
- `POST /api/account/freeze`: suspends the AMFA user (`PATCH /v1/users/{id} {status: SUSPENDED}`) and marks
  the bank account frozen, so no action can start; demo tools gain "Unfreeze" (the bank's support desk in
  real life). A frozen customer sees a "Your account is frozen" screen with the helpline.

## Error and edge states

| Situation | What the customer sees |
|---|---|
| Camera or microphone blocked | AMFA's existing guidance inside the step; if the check still can't run, AMFA adds a substitute check or holds the payment for review, as today |
| AMFA unreachable when starting | "We can't confirm payments right now. Nothing was sent." with Try again |
| Customer closes the verify screen | The item stays "Waiting for you" in Activity with Continue and Cancel |
| Held for review | Result screen with timeline; Activity polls every 5 s and moves it to Sent or Not sent |
| Locked by AMFA (spoof) or frozen | Full-screen notice with helpline; no actions offered |
| No payees yet | Empty state in Pay with "Add your first payee" |
| Small screens and large text | Layout reflows to 320 px; tap targets ≥ 44 px; focus visible; contrast ≥ 4.5:1 |

## Testing

- The 11 end-to-end tests in `tests/core/test_customer_app.py` keep passing, updated for the API changes,
  plus tests for: `enrol` with a factor list, `me.protection` levels, `last-decision` (hidden when demo mode
  is off, never in `public()`), freeze blocks every action kind.
- Core: `theme` is returned by both client endpoints; an invalid `branding.color` is rejected by policy
  validation.
- Headless Chrome screenshots of every screen at 390 px, light and dark, compared with the mockup; a real run
  on the local stack with camera, microphone and SMS codes from the core log.

## Out of scope

QR / UPI scanning, a notifications centre, several accounts, a native app, languages other than English,
custom fonts (the app uses the phone's system font; the mockup's typeface was only for the look).
