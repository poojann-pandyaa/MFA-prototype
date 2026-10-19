#!/usr/bin/env python3
"""
Browser end-to-end: the REAL hosted pages and SDK in headless Firefox, driven
over WebDriver BiDi (the W3C protocol), against a running platform.

    python scripts/browser_e2e.py --base-url http://127.0.0.1:8040 \
        --key-id ak_... --secret-env AMFA_SMOKE_SECRET --tenant-id tn_...

What runs for real: the hosted enrolment and challenge pages, every SDK capture
path (canvas JPEG frame capture from a <video> MediaStream, WebAudio PCM
capture through the AudioWorklet and in-browser WAV encoding, WebCrypto P-256
device-key generation in IndexedDB and signing, keystroke timing capture,
pointer-traced shapes), and every server-side check with the real models.

What is synthetic, stated plainly: getUserMedia is answered by a preload
script with a canvas stream of real photographs (per-frame sensor noise; the
"head" turns when the page prompts it) and a WebAudio stream of a real speech
clip. Key presses and pointer drags go through the browser's own input
pipeline (input.performActions) with human-like, jittered timing.

The synthetic camera can only turn RIGHT convincingly (the second photograph
is a real right turn; its mirror reaches yaw +0.17, under the 0.18 threshold),
so a TURN_LEFT draw fails as it should and the harness starts another
transaction.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import cv2
import httpx
import numpy as np
import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "sdk" / "python")]
from amfa_client import AmfaClient  # noqa: E402

from amfa.core.otp import totp_now  # noqa: E402

from amfa.factors.ueba.features.shape import SHAPE  # noqa: E402

A = Path(os.environ.get("AMFA_TEST_ASSETS", ROOT / "tests" / "assets"))   # obama.jpg, obama2.jpg, example1.wav


# --------------------------------------------------------------------------- #
# Synthetic devices (real media)
# --------------------------------------------------------------------------- #

def _fit(img, W=640, H=480):
    """Whole photo in frame, edges replicated: PAD needs context around the face."""
    h, w = img.shape[:2]
    s = min(W / w, H / h)
    r = cv2.resize(img, (int(w * s), int(h * s)))
    t, l = (H - r.shape[0]) // 2, (W - r.shape[1]) // 2
    return cv2.copyMakeBorder(r, t, H - r.shape[0] - t, l, W - r.shape[1] - l, cv2.BORDER_REPLICATE)


def _jpeg_data_url(img) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(
        cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1]).decode()


def _wav_data_url() -> str:
    from scipy.signal import resample_poly
    x, _ = sf.read(str(A / "example1.wav"), dtype="float32")
    y = resample_poly(np.concatenate([x, x * 0.97, x]), 3, 1).astype(np.float32)
    b = io.BytesIO()
    sf.write(b, y, 48000, format="WAV", subtype="PCM_16")
    return "data:audio/wav;base64," + base64.b64encode(b.getvalue()).decode()


def preload_script() -> str:
    o1, o2 = cv2.imread(str(A / "obama.jpg")), cv2.imread(str(A / "obama2.jpg"))
    media = {"frontal": _jpeg_data_url(_fit(o1)), "right": _jpeg_data_url(_fit(o2)),
             "left": _jpeg_data_url(_fit(cv2.flip(o2, 1))), "audio": _wav_data_url()}
    return """() => {
  const M = %s;
  const imgs = {};
  for (const k of ["frontal", "right", "left"]) { const i = new Image(); i.src = M[k]; imgs[k] = i; }
  let video = null, audio = null;
  let last = "frontal", side = "right", mix = 0;
  function pose() {
    // The synthetic "person" follows the page's own on-screen instruction,
    // and keeps a turned pose while told to hold it.
    const p = document.querySelector(".face-prompt, .camera .prompt");
    const t = p ? p.textContent : "";
    if (/to your left|slightly left/i.test(t)) last = "left";
    else if (/to your right|slightly right/i.test(t)) last = "right";
    else if (!/hold it/i.test(t)) last = "frontal";
    return last;
  }
  function videoStream() {
    if (video) return video;
    const c = document.createElement("canvas"); c.width = 640; c.height = 480;
    const g = c.getContext("2d");
    setInterval(() => {
      // turn gradually, as a head does: cross-fade towards the turned photo over ~0.6 s, so the
      // service sees in-between poses (it rejects a head that jumps straight to the turned pose)
      const target = pose();
      if (target !== "frontal") side = target;
      mix += target === "frontal" ? -0.13 : 0.13;
      mix = Math.max(0, Math.min(1, mix));
      const i = imgs.frontal, turned = imgs[side];
      if (i.complete && turned.complete) {
        // A person in front of a camera is never pixel-still: small movement,
        // scale and lighting changes every frame. Without this the product's
        // replay guard (rightly) reads repeated frames as injected media.
        const t = performance.now() / 1000, sc = 1 + 0.02 * Math.sin(t * 1.7) + 0.01 * Math.random();
        g.save();
        g.translate(320 + 5 * Math.sin(t * 2.3) + 2 * Math.random(), 240 + 4 * Math.cos(t * 1.9) + 2 * Math.random());
        g.scale(sc, sc);
        g.drawImage(i, -320, -240, 640, 480);
        if (mix > 0) { g.globalAlpha = mix; g.drawImage(turned, -320, -240, 640, 480); g.globalAlpha = 1; }
        g.restore();
        g.fillStyle = "rgba(255,255,255," + (0.04 + 0.04 * Math.random()).toFixed(3) + ")";
        g.fillRect(0, 0, 640, 480);
      }
      // Sensor noise: a real camera never produces the same frame twice.
      for (let n = 0; n < 60; n++) {
        const v = Math.floor(Math.random() * 255);
        g.fillStyle = "rgb(" + v + "," + v + "," + v + ")";
        g.fillRect(Math.random() * 640, Math.random() * 480, 1, 1);
      }
    }, 50);
    video = c.captureStream(20);
    return video;
  }
  async function audioStream() {
    if (audio) return audio;
    const ctx = new AudioContext({ sampleRate: 48000 });
    // Decoded in-script: fetch() of a data: URL is (rightly) blocked by the page's CSP.
    const bin = atob(M.audio.split(",")[1]), bytes = new Uint8Array(bin.length);
    for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
    const buf = await ctx.decodeAudioData(bytes.buffer);
    const src = ctx.createBufferSource(); src.buffer = buf; src.loop = true;
    const dst = ctx.createMediaStreamDestination(); src.connect(dst); src.start();
    audio = dst.stream;
    return audio;
  }
  const md = navigator.mediaDevices;
  md.getUserMedia = async (c) => (c && c.video)
      ? new MediaStream(videoStream().getVideoTracks().map(t => t.clone()))
      : new MediaStream((await audioStream()).getAudioTracks().map(t => t.clone()));
  md.enumerateDevices = async () => [{ kind: "videoinput", deviceId: "synthetic-cam", label: "", groupId: "g" },
                                     { kind: "audioinput", deviceId: "synthetic-mic", label: "", groupId: "g" }];
}""" % json.dumps(media)


# --------------------------------------------------------------------------- #
# Minimal WebDriver BiDi client
# --------------------------------------------------------------------------- #

def _value(rv):
    """Deserialize a BiDi RemoteValue."""
    t = rv.get("type")
    if t in ("string", "number", "boolean"):
        return rv["value"]
    if t in ("null", "undefined"):
        return None
    if t == "array":
        return [_value(x) for x in rv.get("value", [])]
    if t == "object":
        return {k: _value(v) for k, v in rv.get("value", [])}
    return rv.get("value")


class Browser:
    def __init__(self, ws):
        self.ws, self.ctx, self.n, self.pending = ws, None, 0, {}

    @classmethod
    async def launch(cls, profile: Path, port: int):
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "user.js").write_text("\n".join([
            'user_pref("media.autoplay.default", 0);',
            'user_pref("media.autoplay.block-webaudio", false);',
            'user_pref("media.navigator.permission.disabled", true);',
            'user_pref("browser.shell.checkDefaultBrowser", false);',
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);',
        ]))
        proc = subprocess.Popen(["firefox", "--headless", "--no-remote", "--profile", str(profile),
                                 f"--remote-debugging-port={port}", "--width", "1100", "--height", "1400"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        for _ in range(150):
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{port}/session", max_size=2**26)
                break
            except (OSError, websockets.exceptions.InvalidStatus, websockets.exceptions.InvalidHandshake):
                await asyncio.sleep(0.3)
        if ws is None:
            proc.terminate()
            raise RuntimeError("Firefox remote agent did not start")
        b = cls(ws)
        asyncio.get_event_loop().create_task(b._reader())
        await b.cmd("session.new", capabilities={})
        await b.cmd("session.subscribe", events=["log.entryAdded"])
        tree = await b.cmd("browsingContext.getTree")
        b.ctx = tree["contexts"][0]["context"]
        return b, proc

    async def _reader(self):
        async for msg in self.ws:
            m = json.loads(msg)
            if m.get("id") in self.pending:
                self.pending.pop(m["id"]).set_result(m)
            elif m.get("method") == "log.entryAdded" and m["params"].get("level") == "error":
                print("  !! page error:", m["params"].get("text", "")[:240])

    async def cmd(self, method: str, **params):
        self.n += 1
        fut = asyncio.get_event_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        try:
            r = await asyncio.wait_for(fut, 120)
        except TimeoutError:
            raise TimeoutError(f"BiDi command {method} got no reply in 120 s") from None
        if r.get("type") == "error":
            raise RuntimeError(f"{method}: {r.get('error')} {r.get('message', '')[:200]}")
        return r.get("result", {})

    async def js(self, expr: str):
        r = await self.cmd("script.evaluate", expression=expr, awaitPromise=True,
                           target={"context": self.ctx}, resultOwnership="none")
        if r.get("type") == "exception":
            raise RuntimeError(r["exceptionDetails"].get("text"))
        return _value(r["result"])

    async def goto(self, url: str):
        await self.cmd("browsingContext.navigate", context=self.ctx, url=url, wait="complete")

    async def wait(self, expr: str, timeout: float = 30.0):
        end = time.time() + timeout
        while time.time() < end:
            v = await self.js(expr)
            if v:
                return v
            await asyncio.sleep(0.2)
        raise TimeoutError(f"waited {timeout}s for: {expr}\n--- page shows:\n{await self.panel()}")

    async def panel(self) -> str:
        return await self.js("(document.getElementById('panel')||document.body).innerText")

    async def click(self, label: str, timeout: float = 20.0):
        # A button may be disabled while the page gets ready (e.g. the face
        # button until the camera shows a picture): wait for it, like a person.
        deadline = time.monotonic() + timeout
        while True:
            ok = await self.js(f"""(() => {{ const b=[...document.querySelectorAll('button')]
                .find(b => b.textContent.trim().startsWith({json.dumps(label)}) && !b.disabled);
                if (b) {{ b.click(); return true; }} return false; }})()""")
            if ok or time.monotonic() > deadline:
                break
            await asyncio.sleep(0.25)
        if not ok:
            raise RuntimeError(f"no enabled button {label!r}\n{await self.panel()}")

    async def type_text(self, selector: str, text: str, rng: random.Random, enter: bool = False):
        await self.js(f"document.querySelector({json.dumps(selector)}).focus()")
        acts = []
        for ch in text:
            acts += [{"type": "keyDown", "value": ch},
                     {"type": "pause", "duration": int(max(30, rng.gauss(85, 18)))},
                     {"type": "keyUp", "value": ch},
                     {"type": "pause", "duration": int(max(10, rng.gauss(75, 30)))}]
        if enter:
            acts += [{"type": "keyDown", "value": ""}, {"type": "keyUp", "value": ""}]
        await self.cmd("input.performActions", context=self.ctx,
                       actions=[{"type": "key", "id": "kbd", "actions": acts}])

    async def hold(self, selector: str, until: str, extra: float):
        """Press and hold like a person: keep holding until the page says it has enough, then a little more."""
        x, y = await self.js(f"(() => {{ const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                             f" return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; }})()")
        mouse = {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}}
        await self.cmd("input.performActions", context=self.ctx, actions=[
            dict(mouse, actions=[{"type": "pointerMove", "x": x, "y": y}, {"type": "pointerDown", "button": 0}])])
        try:
            await self.wait(until, 30)
            await asyncio.sleep(extra)
        finally:
            await self.cmd("input.performActions", context=self.ctx, actions=[
                dict(mouse, actions=[{"type": "pointerUp", "button": 0}])])

    async def trace(self, rng: random.Random):
        x0, y0, w, h = await self.js("(() => { const r=document.querySelector('canvas').getBoundingClientRect();"
                                     " return [r.left, r.top, r.width, r.height]; })()")
        pts = []
        for (ax, ay), (bx, by) in zip(SHAPE, SHAPE[1:]):
            for t in np.linspace(0, 1, 16, endpoint=False):
                pts.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        pts.append(tuple(SHAPE[-1]))

        def at(p):
            return int(x0 + p[0] * w + rng.gauss(0, 1.5)), int(y0 + p[1] * h + rng.gauss(0, 1.5))
        sx, sy = at(pts[0])
        acts = [{"type": "pointerMove", "x": sx, "y": sy}, {"type": "pointerDown", "button": 0}]
        for p in pts[1:]:
            x, y = at(p)
            acts.append({"type": "pointerMove", "x": x, "y": y, "duration": int(max(8, rng.gauss(18, 5)))})
        acts.append({"type": "pointerUp", "button": 0})
        await self.cmd("input.performActions", context=self.ctx, actions=[
            {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": acts}])


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

H2 = "(() => { const p=document.getElementById('panel'); if (p.querySelector('.result')) return 'RESULT';" \
     " const h=p.querySelector('h2'); return h ? h.textContent : ''; })()"


async def heading_changes(b: Browser, prev: str, timeout: float = 120):
    return await b.wait(f"(() => {{ const v={H2}; return v && v !== {json.dumps(prev)} ? v : ''; }})()", timeout)


async def say(b: Browser, seconds: float):
    """Hold the talk button long enough, once the page is ready for it."""
    await b.wait("(() => { const t=document.querySelector('button.talk'); return t && !t.disabled; })()", 30)
    await b.hold("button.talk", "document.querySelector('.talk-wrap').classList.contains('enough')", seconds)


async def enrol(b: Browser, url: str, rng: random.Random) -> str | None:
    await b.goto(url)
    secret = None
    state = await b.wait(H2, 30)
    for _ in range(12):
        banner = await b.js("(document.querySelector('#panel .mode')||{}).innerText || ''")
        overall = await b.js("(document.getElementById('progress-label')||{}).textContent || ''")
        if state == "Before we start":
            await b.js("document.getElementById('agree').click()")
            await b.click("I agree")
            print("  - consent given")
        elif state.startswith("Register your authenticator"):
            secret = (await b.wait("(document.querySelector('.secret')||{}).textContent")).replace(" ", "")
            await b.js(f"document.getElementById('code').value = {json.dumps(totp_now(secret))}")
            await b.click("Confirm")
            print(f"  - [{overall}] authenticator linked  ({banner.replace(chr(10), ': ')})")
        elif state == "Register your face":
            for attempt in range(3):
                await b.wait("(() => { const v=document.querySelector('video'); return v && v.videoWidth > 0; })()", 30)
                await b.click("Start" if attempt == 0 else "Try again", 40)
                nxt = await b.wait("(() => { const p=document.getElementById('panel'); const h=p.querySelector('h2');"
                                   " if (h && h.textContent !== 'Register your face') return 'next';"
                                   " const btn=[...p.querySelectorAll('button')].find(x => x.textContent === 'Try again' && !x.disabled);"
                                   " return btn ? 'retry' : ''; })()", 120)
                if nxt == "next":
                    break
            else:
                raise AssertionError("face enrolment failed 3 times:\n" + await b.panel())
            print(f"  - [{overall}] face registered: 5 pictures, per-step bar + ring  ({banner.replace(chr(10), ': ')})")
        elif state == "Register your voice":
            for n in range(3):
                await say(b, 0.9)
                await b.wait(f"(() => {{ const c=document.querySelector('.step-progress .count');"
                             f" const h=document.querySelector('#panel h2');"
                             f" return (c && c.textContent.startsWith('{n + 1} of')) || (h && h.textContent !== 'Register your voice'); }})()", 60)
            print(f"  - [{overall}] voice registered: 3 hold-to-talk recordings  ({banner.replace(chr(10), ': ')})")
        elif state == "Register your typing":
            phrase = await b.js("document.querySelector('.prompt-phrase').textContent")
            target = int((await b.js("document.querySelector('.step-progress .count').textContent")).split(" of ")[1])
            for _ in range(target):
                await b.type_text("#phrase", phrase, rng, enter=True)
                await asyncio.sleep(max(0.3, rng.gauss(0.7, 0.15)))
            print(f"  - [{overall}] typing registered: {target} phrases  ({banner.replace(chr(10), ': ')})")
        elif state == "Register your tracing":
            need = int((await b.js("document.querySelector('.step-progress .count').textContent")).split(" of ")[1])
            for _ in range(need):
                await b.trace(rng)
                await asyncio.sleep(0.4)
            print(f"  - tracing registered: {need} lines")
        elif state == "RESULT":
            text = await b.js("document.querySelector('.result').innerText")
            assert "Setup complete" in text, text
            print("  - outcome:", " | ".join(x.strip() for x in text.split("\n") if x.strip()))
            return secret
        state = await heading_changes(b, state)
    raise AssertionError("enrolment did not finish:\n" + await b.panel())


async def challenge(b: Browser, url: str, secret: str | None, rng: random.Random, otp_step: list) -> str:
    await b.goto("about:blank")      # a new link differs only after '#': without this the old page stays
    await b.goto(url)
    await b.wait("!document.getElementById('approving').classList.contains('hidden')", 30)
    shown = await b.js("document.getElementById('approving').innerText")
    print("  - customer sees:", " | ".join(x.strip() for x in shown.split("\n") if x.strip())[:110])
    state = await b.wait(H2, 60)
    for _ in range(14):
        if state == "RESULT":
            break
        banner = await b.js("(document.querySelector('#panel .mode')||{}).innerText || ''")
        if state.startswith("Verify with your authenticator") or state.startswith("Verify with the code"):
            # each code works once: wait for the authenticator's next code, as a person would
            while int(time.time() // 30) <= otp_step[0]:
                await asyncio.sleep(1)
            otp_step[0] = int(time.time() // 30)
            code = totp_now(secret, time.time())
            await b.js(f"(() => {{ const i=document.getElementById('code'); i.value={json.dumps(code)};"
                       f" i.dispatchEvent(new Event('input')); }})()")
        elif state == "Verify with your typing":
            phrase = await b.js("document.querySelector('.prompt-phrase').textContent")
            await b.type_text("#phrase", phrase, rng)
            if await b.js("document.querySelector('canvas') !== null"):
                await asyncio.sleep(0.4)
                await b.trace(rng)
            await b.click("Continue")
        elif state == "Verify with your face":
            await b.wait("(() => { const v=document.querySelector('video'); return v && v.videoWidth > 0; })()", 30)
            await b.click("Start face check", 40)
        elif state == "Verify with your voice":
            await say(b, 1.2)
        prev = state
        state = await b.wait(f"(() => {{ const v={H2}; const p=document.getElementById('panel');"
                             f" const retry = p.querySelector('.notice.bad, .notice.warn') && [...p.querySelectorAll('button')].some(x => !x.disabled);"
                             f" return v === 'RESULT' || v !== {json.dumps(prev)} || retry ? (v || 'RETRY') : ''; }})()", 150)
        steps = await b.js("[...document.querySelectorAll('#steps li')].map(l => l.textContent).join(', ')")
        note = await b.js("(document.querySelector('#panel .notice')||{}).textContent || ''")
        print(f"  - {prev}: [{steps}]  ({banner.replace(chr(10), ': ')})" + (f"  ({note})" if note else ""))
    text = await b.js("document.querySelector('.result').innerText")
    print("  - outcome:", " | ".join(x.strip() for x in text.split("\n") if x.strip()))
    return await b.js("document.querySelector('.result h2').textContent")


async def main_async(a) -> int:
    bank = AmfaClient(a.base_url, a.key_id, os.environ[a.secret_env],
                      http=httpx.Client(base_url=a.base_url, timeout=60))
    work = Path(tempfile.mkdtemp(prefix="amfa-browser-"))
    b, proc = await Browser.launch(work / "profile", 9500 + random.randint(0, 300))
    rng = random.Random(11)
    try:
        await b.cmd("script.addPreloadScript", functionDeclaration=preload_script())
        print("browser e2e (Firefox, WebDriver BiDi):", a.base_url)
        ref = f"BROWSER-{uuid.uuid4().hex[:8]}"
        user = bank.create_user(ref)
        secret = await enrol(b, bank.enrollment_link(user["id"])["url"], rng)
        device_id = await b.js("AMFA.deviceId()")
        otp_step = [0]
        for attempt in range(1, 5):
            ext = f"WEB-{uuid.uuid4().hex[:8]}"
            txn = bank.initiate(user_id=user["id"], external_id=ext, amount_minor=12_345_00, currency="INR",
                                payee_ref="BEN-900", payee_display="R. Iyer ••0042", device_id=device_id,
                                ip="49.36.10.20", capability={"camera": True, "microphone": True, "pointer": True})
            print(f"  - transaction {txn['transactionId']}: plan {txn['plan']['required']} ({txn['plan']['riskBand']})")
            outcome = await challenge(b, txn["challengeUrl"], secret, rng, otp_step)
            view = bank.transaction(txn["transactionId"])
            bound = not any("did not prove possession" in (e.get("reason") or "")
                            for e in view["session"]["escalations"])
            print(f"  - page shows {outcome!r}; platform: {view['status']}; device key proven: {bound}")
            if view["status"] == "AUTHORIZED":
                break
            print("    (a TURN_LEFT draw the synthetic camera cannot answer -- starting another transaction)")
        else:
            print("FAIL")
            return 1
        token = bank.fetch_proof(txn["transactionId"])["token"]
        args = dict(user_ref=ref, external_txn=ext, amount_minor=12_345_00, currency="INR", payee_ref="BEN-900")
        bank.verify_proof_offline(token, tenant_id=a.tenant_id, **args)
        assert bank.verify_proof(token, **args)["valid"]
        print("  - proof verified offline (JWKS) and consumed online")
        bank.erase_user(user["id"])
        print("PASS")
        return 0
    finally:
        try:
            await b.cmd("session.end")
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-id", required=True)
    ap.add_argument("--secret-env", default="AMFA_SMOKE_SECRET")
    ap.add_argument("--tenant-id", required=True)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
