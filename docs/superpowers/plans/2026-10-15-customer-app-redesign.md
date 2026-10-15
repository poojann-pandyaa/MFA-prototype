# Customer App Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Demo Bank customer app and the AMFA hosted pages into one seamless, friendly neobank-style app with progressive security setup and a presenter-only "Behind the scenes" drawer.

**Architecture:** The bank front end becomes native ES modules (router, api, ui components, one module per screen) with no build step. AMFA's hosted setup and verification pages stay on AMFA's origin, take the bank's colour from tenant policy, drop their chrome in `embed=1` mode and post `amfa:step` progress to the bank app. The bank server gains progressive enrolment, protection level, resumable verification, a private decision record for demo mode, and account freeze.

**Tech Stack:** Python 3.13, FastAPI, pydantic v2, SQLAlchemy, pytest; vanilla JS (ES2020 modules), CSS custom properties; headless Chrome over CDP for screenshots.

**Spec:** `docs/superpowers/specs/2026-10-15-customer-app-redesign-design.md` (mockup: https://claude.ai/artifact/H1Y9w5NqgujELsaxjMswyv)

## Global Constraints

- CSP stays `default-src 'self'; script-src 'self'; style-src 'self'; frame-src <AMFA origin>` — no inline scripts or styles in HTML, no external fonts or CDNs.
- All page text is set with `textContent` or `createTextNode`; never `innerHTML` with data.
- Brand colour `#0B6E63`; amber only for warnings; system font stack; tap targets ≥ 44 px; text contrast ≥ 4.5:1; layout works from 320 px wide; light and dark themes.
- Customers never see risk scores, threat levels, model reasons or thresholds outside demo mode.
- Commits as `Poojan Pandya <poojan2861@gmail.com>`, no Claude trailers.

## Review Focus

1. A customer closes the verification screen mid-way and taps Continue in Activity: they must land back in the same AMFA session, not a dead end (test in Task 3).
2. A frozen account tries any action kind: every one is refused before AMFA is called (test in Task 3).
3. Demo mode is off: the decision record is not reachable and never leaks through `public()` (test in Task 3).
4. Amount input like `0.005`, `1e5`, empty, or above balance/limit: the keypad caps two decimals and the server answers with a plain message, nothing starts (test in Task 3 for the server side).
5. A tenant policy with a malformed `branding.color` (e.g. `red;}`) is rejected, so nothing reaches the hosted pages' CSS (test in Task 1).

---

### Task 1: Tenant branding and theme for the hosted pages

**Files:**
- Modify: `amfa/engine/policy.py` (add `Branding`, `TenantPolicy.branding`)
- Modify: `amfa/core/api_client.py` (`theme` in `enrollment_state` and `_state`)
- Test: `tests/core/test_hosted_theme.py`

**Interfaces:**
- Produces: `TenantPolicy.branding.color: str` (hex `#RRGGBB`, default `#1B4FD8`); client payloads `theme: {"bank": str, "color": str}`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from pydantic import ValidationError
from amfa.engine.policy import TenantPolicy

def test_branding_colour_defaults_and_validates():
    assert TenantPolicy().branding.color == "#1B4FD8"
    assert TenantPolicy.model_validate({"branding": {"color": "#0B6E63"}}).branding.color == "#0B6E63"
    with pytest.raises(ValidationError):
        TenantPolicy.model_validate({"branding": {"color": "red;}"}})

def test_session_and_enrolment_carry_the_theme(stack, app):   # fixtures from test_customer_app
    ...
```
(The second test lives in `tests/core/test_customer_app.py`, Task 3, because it needs the running stack: after enrolling, `GET /c/v1/enrollment` with the link's token returns `theme == {"bank": "Demo Bank", "color": <policy colour>}`; a started transfer's `GET /c/v1/session` returns the same.)

- [ ] **Step 2: Run** `pytest tests/core/test_hosted_theme.py -v` → FAIL (`branding` missing).
- [ ] **Step 3: Implement**

```python
class Branding(BaseModel):
    color: str = Field(default="#1B4FD8", pattern=r"^#[0-9A-Fa-f]{6}$")
# in TenantPolicy:
    branding: Branding = Field(default_factory=Branding)
```
In `api_client.py`: `"theme": {"bank": tenant.name, "color": _policy(s, tenant.id).branding.color}` in both payloads.
- [ ] **Step 4: Run** the test → PASS; run `pytest -q` → all pass.
- [ ] **Step 5: Commit** `Hosted pages: tenant branding colour`

### Task 2: Hosted pages — embedded mode, step messages, restyle

**Files:**
- Modify: `amfa/core/static/js/ui.js` (theme + embed helpers, `UI.step()`), `enroll.js`, `challenge.js`
- Modify: `amfa/core/static/css/hosted.css` (new visual language), `pages/enroll.html`, `pages/challenge.html`

**Interfaces:**
- Consumes: `theme` from Task 1.
- Produces: URL flag `embed=1`; `postMessage({type:"amfa:step", flow:"enroll"|"session", index:int, total:int, factor:str})` to the parent's allowed origins (same channel as `amfa:status`); body class `embedded`.

- [ ] **Step 1:** `ui.js`: `UI.applyTheme(theme)` sets `--brand` and `--brand-ink` (white when the colour's relative luminance < 0.45, else `#0F1B2D`); `UI.embedded = new URLSearchParams(location.search).get("embed") === "1"` adds `document.body.classList.add("embedded")`; `UI.step(flow, index, total, factor)` posts `amfa:step` via the existing origin-checked notifier.
- [ ] **Step 2:** `enroll.js`: call `UI.applyTheme(st.theme)`; in `run()` post `UI.step("enroll", index, steps.length, name)`. `challenge.js`: apply theme from session state; on each render post `UI.step("session", doneCount, st.steps.length, current factor)`.
- [ ] **Step 3:** `hosted.css`: `.embedded .brand, .embedded #approving, .embedded .title-card {display:none}`; `.embedded .card {border:0; background:transparent; padding:0}`; buttons 56 px / 16 px radius / 700 weight; headings 24 px; camera block becomes a circle with a ring (`.camera {aspect-ratio:1; border-radius:50%}`, ring via `.ring` SVG injected by `faceStep`, segments `stroke-dasharray` updated per frame through the burst `onTick`); OTP input becomes six boxes (`.otp-boxes input` ×6, `autocomplete="one-time-code"` on the first, paste fills all, auto-advance, auto-submit at 6); expiry countdown from `ch.expiresAt`.
- [ ] **Step 4:** Verify with the CDP screenshot script (`h-*` shots, 390 px, `embed=1` and without): no header in embed mode, ring visible, digit boxes visible. Run `node --check` on the three JS files.
- [ ] **Step 5: Commit** `Hosted pages: embedded mode, step progress, new look`

### Task 3: Bank server — progressive setup, protection, resume, decision record, freeze

**Files:**
- Modify: `examples/demo_bank/bank.py`
- Test: `tests/core/test_customer_app.py`

**Interfaces:**
- Produces (JSON):
  - `POST /api/amfa/enrol {"factors"?: ["FACE"|"VOICE"|"UEBA"|"OTP"]}` → `{"url": str}` (url ends with `&embed=1`; default `["FACE"]`)
  - `GET /api/me` adds `protection: {"level": "Basic"|"Good"|"Strong", "have": [str], "canAdd": [str]}`, `recentPayees: [{"ref","display","initials"}]` (≤ 6, most recent first), `frozen: bool`, `deviceTrusted: bool` (exists)
  - action `public()` adds `payee: {"display","initials"}`, `confirmedWith: [str]`, `canContinue: bool`
  - `POST /api/actions/{id}/continue` → `{"challengeUrl": str}` while `AWAITING_VERIFICATION`, else 409
  - `GET /api/demo/last-decision` → `{"decision": {...} | null}` (404 when `BANK_DEMO_TOOLS=0`)
  - `POST /api/account/freeze` → `{"frozen": true}`; `POST /api/demo/unfreeze` → `{"frozen": false}`
  - every action start on a frozen account → 423 `"your account is frozen; call us to unfreeze it"`

- [ ] **Step 1: Write the failing tests** (append to `tests/core/test_customer_app.py`)

```python
def test_enrol_asks_only_for_what_is_requested(stack, app):
    url = app.post("/api/amfa/enrol", json={"factors": ["VOICE"]}).json()["url"]
    assert url.endswith("&embed=1")
    token = url.split("#token=", 1)[1].split("&", 1)[0]
    st = stack.core.get("/c/v1/enrollment", headers={"authorization": f"Bearer {token}"}).json()
    assert st["requested"] == ["VOICE"] and st["theme"]["bank"] == "Demo Bank"

def test_protection_level_counts_methods(stack, app):
    p = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["protection"]
    assert p["level"] == "Strong" and "Face" in p["have"] and "SMS code" in p["have"]

def test_a_closed_verification_can_be_continued(stack, app):
    aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()["action"]["id"]
    url = app.post(f"/api/actions/{aid}/continue").json()["challengeUrl"]
    verify(stack, url)
    assert app.post(f"/api/actions/{aid}/complete").json()["action"]["status"] == "DONE"
    assert app.post(f"/api/actions/{aid}/continue").status_code == 409

def test_a_frozen_account_cannot_start_anything(stack, app):
    app.post("/api/account/freeze").raise_for_status()
    for kind, body in [("transfer", {"payeeRef": "BEN-42", "amountRupees": 10}), ("bill", {"billerRef": "BILL-ELEC", "amountRupees": 10}),
                       ("payee", {"name": "Ravi Kumar", "account": "123456789012", "ifsc": "HDFC0001234"}),
                       ("limit", {"limitRupees": 5000}), ("contact", {"field": "email", "value": "a@b.co"}),
                       ("password", {"current": "long-password-1", "new": "another-password-2"}), ("device", {})]:
        assert act(app, kind, **body).status_code == 423, kind
    assert app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["frozen"]
    app.post("/api/demo/unfreeze").raise_for_status()
    assert act(app, "transfer", payeeRef="BEN-42", amountRupees=10).status_code == 201

def test_the_decision_is_for_the_presenter_only(stack, app, monkeypatch):
    LEVEL["now"] = "T2"
    action = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()["action"]
    assert "decision" not in action
    d = app.get("/api/demo/last-decision").json()["decision"]
    assert d["threatLevel"] == "T2" and d["reasons"][0]["text"]
    monkeypatch.setattr(stack.bank, "DEMO_TOOLS", False)
    assert app.get("/api/demo/last-decision").status_code == 404

def test_bad_amounts_never_start_anything(stack, app):
    for amount in (0, -5, 0.001, 10**9):
        assert act(app, "transfer", payeeRef="BEN-42", amountRupees=amount).status_code == 422, amount
    assert act(app, "transfer", payeeRef="BEN-42", amountRupees=4_99_999).status_code == 422   # over the daily limit
```
The `app` fixture enrols FACE, VOICE and UEBA, so its customer is "Strong". `0.001` must fail: add `multiple_of`-style validation (`round(x, 2) == x`).

- [ ] **Step 2: Run** → FAIL on each new endpoint/field.
- [ ] **Step 3: Implement** in `bank.py`:
  - `Enrol(BaseModel): factors: list[Literal["FACE","VOICE","UEBA","OTP"]] = ["FACE"]`; pass `factors` to `AMFA.enrollment_link`; return `url + "&embed=1"` (the hosted pages read `embed` from `location.search`, so insert before `#`: `base, _, frag = url.partition("#"); base + ("&" if "?" in base else "?") + "embed=1#" + frag`). Tests must split the token at `#token=` and `&` accordingly.
  - `protection(enrolled: dict) -> dict`: have = `["SMS code"]` if `OTP_DELIVERY` or `OTP_TOTP`, plus `Face`/`Voice`/`Typing` for `FACE`/`VOICE`/`UEBA` == `ENROLLED`; level `Basic` (≤1), `Good` (2), `Strong` (≥3); canAdd = missing of Face, Voice, Typing, plus `Authenticator app` when `OTP_TOTP` is false.
  - In `_start`: refuse when `c.get("frozen")` (423) before anything else; store `a["challengeUrl"] = txn.get("challengeUrl")` and `a["decision"] = {threatLevel, modelLevel, score: riskScore, capReason, checks: plan.required, rulesBaseline, reasons: [{"feature": k, "text": REASON_TEXT.get(k, k), "weight": w}]}` (copy `REASON_TEXT` keys used by the model into the bank as a small dict, since the bank must not import `amfa`).
  - `public()` adds `payee`, `confirmedWith` (set in `settle` from `claims["amr"]` mapped `otp→SMS code, face→Face, voice→Voice, ueba→Typing, review→Bank review`), `canContinue = status == "AWAITING_VERIFICATION" and bool(challengeUrl)`.
  - Amount validation: `Field(gt=0, le=…)` plus a validator rejecting more than 2 decimals.
  - `/continue`, `/api/demo/last-decision`, `/api/account/freeze` (`AMFA.request("PATCH", f"/v1/users/{u}", {"status": "SUSPENDED"})`, `c["frozen"] = True`), `/api/demo/unfreeze` (status `ACTIVE`).
  - `recentPayees`: walk `activity` for DONE transfers, unique payee refs in order, map to saved payees.
- [ ] **Step 4: Run** `pytest tests/core/test_customer_app.py -v` → PASS; full suite → PASS.
- [ ] **Step 5: Commit** `Bank server: progressive setup, protection level, resume, freeze, demo decision`

### Task 4: Front-end foundation — shell, tokens, router, api, components

**Files:**
- Create: `examples/demo_bank/static/app.css`, `static/js/app.js`, `static/js/api.js`, `static/js/ui.js`, `static/js/amfa-frame.js`
- Modify: `static/index.html` (shell only); Delete: `static/bank.js`, `static/bank.css`
- Modify: `bank.py` static route to serve `static/js/*.js` (subfolder, same containment check, `text/javascript`)

**Interfaces:**
- `api.js`: `api(method, path, body?) → Promise<json>`; errors are `Error` with `.status` and a friendly `.message`.
- `ui.js`: `h(tag, props?, ...children)` (props: `class`, `text`, `on:<event>`, attributes), `icon(name)` (inline SVG from a table: back, close, send, bill, payee, shield, home, activity, profile, check, clock, lock, warn, plus, chevron, eye, mic, keyboard, phone, face, bolt, drop), `avatar(initials, tone)`, `listRow({lead, title, sub, trail, onClick})`, `button(text, {variant, onClick})`, `header({title, back})`, `chip(text, tone)`, `sheet(contentNode) → {close}`, `toast(text)`, `keypad(onKey)`, `rupees(minor)`, `initials(name)`.
- `app.js`: `go(route)`, `state` (`me`, `activity`, `deviceId`, `demo`), `refresh()`, route table → `screens/<name>.js` `render(root, params)`.
- `amfa-frame.js`: `openAmfa(url, {flow, onStep, onDone})` renders a full-screen layer with a header (close, "Confirm it's you · i of n" or "Set up · i of n", progress bars) and the iframe; resolves on `amfa:status` (non-PENDING) or `amfa:enrolled`.

- [ ] **Step 1:** Write the files; `index.html` loads `app.css` and `<script type="module" src="/static/js/app.js">`.
- [ ] **Step 2:** `node --check` each file (wrap ES modules: `node --input-type=module --check < file`).
- [ ] **Step 3:** Load `/` in headless Chrome: the welcome route renders without console errors (CDP `Runtime.consoleAPICalled` / `Runtime.exceptionThrown` captured and printed).
- [ ] **Step 4: Commit** `Customer app: front-end foundation`

### Task 5: Onboarding screens — welcome, sign up, sign in, protect your account, device check

**Files:** Create `static/js/screens/welcome.js`, `signup.js`, `signin.js`, `setup.js`, `device.js`

- [ ] **Step 1:** Implement per mockup screens 1–3: welcome (two buttons), signup (name, mobile `+91` prefix, username, password with show/hide, inline validation messages), signin, setup (SMS ready row, Face row, consent checkbox, "Set up my face" → `POST /api/amfa/enrol {factors:["FACE"]}` → `openAmfa(url, {flow:"enroll"})` → home). After sign-in, if `me.deviceTrusted` is false route to `device.js` ("Verify this phone" → action `device` → verify → home).
- [ ] **Step 2:** Screenshot each at 390×844 light and dark; compare against mockup screens 1–3.
- [ ] **Step 3: Commit** `Customer app: onboarding`

### Task 6: Paying — home, pay, amount, review, verify, result

**Files:** Create `screens/home.js`, `pay.js`, `amount.js`, `review.js`, `verify.js`, `result.js`, `newpayee.js`

- [ ] **Step 1:** Home per mockup 4 (greeting by time of day, balance card with hide toggle remembered in `sessionStorage`, limit meter, quick actions, Pay again from `recentPayees`, protection card from `protection`, recent 3 activity rows, bottom nav). Pay per 5 (search filters payees and billers; New payee row → `newpayee.js` form → review → verify). Amount per 6 (keypad: digits, one `.`, two decimals max, 8 integer digits max; Continue disabled at 0 or over `limit left`). Review per 7: Continue on the amount screen starts the protected action (AMFA decides then), and the review screen shows the real checks ("1 · SMS code, 2 · Face check"), the customer reason and the scam warning from the action. Back from review cancels the action (`POST /cancel`); an abandoned one expires in AMFA after the session TTL. If AMFA held it (UNDER_REVIEW), review is skipped and `result.js` shows the held state. "Confirm and pay" → `verify.js` → `openAmfa(challengeUrl, {flow:"session"})` (header steps from `amfa:step`) → `POST /complete` → `result.js`. Account-change actions (new payee, limit, contact, password, device) use the same review → verify → result path with their own summary rows.
- [ ] **Step 2:** Result per 10 (Sent/Saved with receipt rows and `confirmedWith` chips, Share via `navigator.share` when available else copy reference) and 11 (held: timeline, Cancel, Back home), plus Not sent / Expired variants with a plain next step.
- [ ] **Step 3:** Screenshot every screen light/dark; drive one full transfer on the trial stack with OTP from the core log.
- [ ] **Step 4: Commit** `Customer app: paying flow`

### Task 7: Activity, security centre, demo drawer, frozen and error states

**Files:** Create `screens/activity.js`, `security.js`, `limit.js`, `contact.js`, `password.js`, `demo.js`, `frozen.js`

- [ ] **Step 1:** Activity per 12 (filters All / Money / Account changes; grouped Today / Yesterday / date; row tap opens a sheet with receipt, Continue when `canContinue`, Cancel when pending, "This wasn't me" when DONE and not reported; polls every 5 s while anything is pending). Security per 13 (protection meter, methods with Add → `enrol` with that factor → `openAmfa` → refresh; trusted devices; daily limit / contact / password open their small forms, each submitting a protected action through `verify.js`; Freeze with a confirm sheet → `frozen.js`).
- [ ] **Step 2:** Demo drawer per 14: a floating "Behind the scenes" button when `me.demoTools`; drawer shows `GET /api/demo/last-decision` (T0–T4 strip, probability, reasons with bars scaled to the largest weight, checks vs rules baseline, cap reason) and toggles for SIM swap, mule-list payee (select), new phone, Unfreeze, Reset.
- [ ] **Step 3:** Frozen / locked (HTTP 423) and AMFA-down (502) full-screen notices with helpline and Try again.
- [ ] **Step 4:** Screenshot every screen light/dark at 390 and 320 px wide.
- [ ] **Step 5: Commit** `Customer app: activity, security centre, demo mode`

### Task 8: Finish — docs, full verification, push

- [ ] **Step 1:** README "Customer app" section: new flow, progressive setup, demo mode, `branding.color`, `BANK_DEMO_TOOLS`.
- [ ] **Step 2:** `pytest -q` full suite; `node --check` all JS; restart the trial stack (core 8041, face 8111, bank 8051) and click through sign-up → face setup → transfer → review hold → activity → security → demo drawer in headless Chrome.
- [ ] **Step 3: Commit and push** `customer-app`.
