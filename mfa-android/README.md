# Adaptive MFA for Android

A native Android app (Kotlin, Jetpack Compose, CameraX) for the Demo Bank in `../examples/demo_bank`, with every
payment approved by AMFA (`../amfa`). The app was written by Preet against the earlier face-auth backend; it now talks
to the bank and to AMFA's customer API (`/c/v1`) instead, with no WebView: the app keeps its own CameraX capture and
sends frames to AMFA itself.

## Get the app on any phone (from GitHub)

1. Open the repository's **Releases** page and download `AdaptiveMFA-<version>.apk` from the newest `android-v*` release.
   (Every push also leaves the APK as a workflow artifact under Actions.)
2. Open the file on the phone and allow "install unknown apps" for the browser or file manager when asked.
3. Start the app, open **Settings** and enter the address of an AMFA demo bank the phone can reach, then **Test connection**.
   A phone on mobile data needs a public HTTPS address (for example a Cloudflare tunnel to the bank); a phone on the same
   Wi-Fi can use `http://<computer's Wi-Fi IP>:8050`.

To publish a build: `git tag android-v1.2.0 && git push origin android-v1.2.0` (the workflow `.github/workflows/android.yml`
builds the `demo` variant, which is not debuggable, is signed with a debug key so any phone installs it, and may use plain
http to a local demo server). To change the address the app starts with, run the workflow by hand and fill in `bank_url`, or
set the repository variable `AMFA_BANK_URL`; locally pass `-PAMFA_BANK_URL=https://bank.example.com` to Gradle.

## Look and feel

The screens follow the web bank (`examples/demo_bank/static`): the same colour tokens in light and dark (the phone's
setting picks), the same stroke icons, welcome, sign-in and sign-up, "Let's protect your account", home with the balance
card, pay again, protection and recent payments, pay (people and bills), the keypad amount screen, the review screen with
AMFA's plan and scam warning, the verify step (a face circle with a progress ring and turn arrow, or six code boxes), the
result screens, activity with filters, and a profile tab. Not ported: adding a payee, changing the limit, contact details
or password, the "Behind the scenes" demo drawer, and opening an activity item to continue or report it.

## How it works

| Step | App | Server |
|---|---|---|
| Open an account / sign in | `BankApi.register`, `login` (the bank's `bank_session` cookie is kept in memory) | bank `/api/register`, `/api/login` |
| Face setup | `BankApi.startFaceSetup()` returns an AMFA link; then `AmfaApi` consents and sends 5 pictures | bank `/api/amfa/enrol`; AMFA `/c/v1/enrollment/{consent,face,complete}` |
| Send money | `BankApi.startTransfer` declares what the phone can do (camera and touch, no microphone); AMFA plans the checks | bank `/api/actions/transfer` |
| Face check | `AmfaApi.session()` gives the challenge (turn left or right); the app captures `params.frames` frames `params.intervalMs` apart and submits them | AMFA `/c/v1/session`, `/c/v1/session/factors/FACE` |
| Code check | a 6-digit code: emailed or texted (with "Send a new code"), or from an authenticator app set up in Profile | AMFA `/c/v1/session/factors/OTP`, `/c/v1/session/otp/resend`; setup `/c/v1/enrollment/totp/{start,confirm}` |
| Voice check | records 3 to 10 s of 16 kHz mono audio (`AudioRecord`), sends it as a base64 WAV | AMFA `/c/v1/session/factors/VOICE`; setup `/c/v1/enrollment/voice` (three sentences) |
| Typing check | types the phrase in a text box (each key is recovered from the text change, as the web SDK does for phones) and, when asked, traces a line with a finger | AMFA `/c/v1/session/factors/UEBA`; setup `/c/v1/enrollment/behaviour` |
| Settle | `BankApi.complete` | bank `/api/actions/{id}/complete`: the bank applies the payment only against AMFA's verified proof |

All biometric work (detection, liveness, matching) stays on AMFA's servers; the app only captures camera frames, so the
checks cannot be bypassed by changing the app. The phone declares a camera, a microphone and touch, so AMFA can plan
face, voice and typing checks; Profile sets each of them up.

AMFA's links point at AMFA's own public address. When that is `127.0.0.1` or `localhost` (a developer machine) the app
uses the bank's host instead (`reachableAmfaBase`), so an emulator reaching the bank at `10.0.2.2:8050` reaches AMFA at
`10.0.2.2:8040`. For a real phone, start AMFA with `AMFA_PUBLIC_URL=http://<computer's Wi-Fi IP>:8040`.

## Handover: SDK and build details

| Item | Value |
|---|---|
| Application id | `com.adaptivemfa.app` |
| App name | Adaptive MFA |
| Version | 1.0.0 (versionCode 1) |
| Min SDK | 26 (Android 8.0) |
| Target SDK | 36 (Android 16) |
| Compile SDK | 37 (required by Compose 1.12; install `platforms;android-37.0`) |
| Language / UI | Kotlin, Jetpack Compose (Material 3), CameraX 1.6.2 |
| Build tools | Android Gradle Plugin 9.5.1, Gradle 9.7.1 (wrapper included), JDK 17+ (Android Studio's bundled JDK, or JDK 25, works) |
| Permissions | `INTERNET`, `CAMERA` |
| Default bank address | `http://10.0.2.2:8050` (the emulator's address for the computer running the bank); change it in Settings |
| Debug APK | `app/build/outputs/apk/debug/app-debug.apk` |

## Run against the demo

1. Start AMFA and the demo bank as in the root README (face service on 8101, core on 8040, bank on 8050).
2. **Emulator:** install the APK; the default address already reaches your computer. Set the emulator's front camera to
   a webcam (AVD Manager, Edit, Advanced).
   **Real phone:** same Wi-Fi as the computer, start the core with `AMFA_PUBLIC_URL=http://<your Wi-Fi IP>:8040`, and
   enter `http://<your Wi-Fi IP>:8050` in Settings, then **Test connection**. The bank and core must listen on
   `0.0.0.0`.
3. Tap **Open an account**, fill in the form, and do the face setup. Then send money from the home screen: small
   payments to a known payee go through; larger or unusual ones ask for a face check or a code. In development the
   one-time code is printed in the core's log.

Plain HTTP is allowed in **debug** builds only (`src/debug/AndroidManifest.xml`). A release build needs HTTPS.

## Build and test

```bash
cd mfa-android
export JAVA_HOME=<a JDK 17+>
./gradlew :app:testDebugUnitTest      # JVM tests, no device needed
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:connectedDebugAndroidTest   # UI tests; start an emulator first
```

If Gradle cannot find the SDK, create `local.properties` here with `sdk.dir=<path to the Android SDK>`.

| Kind | Where | What it covers |
|---|---|---|
| Unit (JVM) | `src/test/.../ApiTest.kt`, `LogicTest.kt`, `AppViewModelTest.kt` | The request and response contract of the bank and AMFA clients, error text, link parsing, money and form rules, and every ViewModel flow (sign-in, face setup, payment with no check, with a face check, a failed check, a code, cancel, blocked, expired session) |
| Integration (JVM, real HTTP) | `src/test/.../BackendIntegrationTest.kt` | The real network class against an in-process server playing the bank and AMFA: the bank cookie is sent back, AMFA's token reaches AMFA only, a payment is settled only after the face check passes |
| UI (emulator or phone) | `src/androidTest/.../AppUiTest.kt` | The installed app: sign-in and sign-up form rules, settings address validation, the unreachable-server message. Compiled but not run on a device in this branch |

The same requests were replayed with a script against the running demo (register, sign in, `/api/me`, face setup
link, consent, a rejected face enrolment, a payment, the session, an OTP check, settle) and every field the app reads
matched.

## Layout

| Path | What |
|---|---|
| `data/` | `Http` (the one network class, with the bank's cookie), `Api` (`BankApi`, `AmfaApi`, link parsing), `Store` (username, bank address, device id) |
| `logic/` | Pure Kotlin: pacing of the face check and setup script, prompts, data URLs, address and money helpers, sign-up rules |
| `camera/FaceCamera.kt` | CameraX front-camera preview and `FrameGrabber`, which hands out one upright JPEG per request; camera permission prompt |
| `ui/` | Compose screens (Sign in, Open an account, Face setup, Home, Verify, Settings) and `AppViewModel` |

## Known limits

- Not run on a physical phone or against a real face in this branch. The liveness thresholds were tuned on webcam
  frames, so phone cameras may need calibration on the AMFA side.
- Voice and typing were tested on the wire (JVM tests and a script against the running demo for typing) but not on a real
  person or against the voice models.
- The app has no on-device face detector, so the buttons do not wait for a face to be in view; AMFA rejects bursts
  without a usable face.
- No device key or attestation yet (`/c/v1/session/device`): AMFA sees the phone by its install id only. Play
  Integrity or a Keystore key would add proof of the device.
- The bank's sign-in cookie is not stored, so the app asks for the password after each restart.
- AMFA's one-time-code resend is not wired up.
