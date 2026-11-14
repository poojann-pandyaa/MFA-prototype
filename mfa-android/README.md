# Adaptive MFA for Android

A standalone native Android app (Kotlin, Jetpack Compose, CameraX) for the Adaptive MFA backend in `../backend`. It does what the web app in `../frontend` does: enroll with a password and a face photo, log in with the password, and, when the server finds the login risky, pass a randomized face challenge (blink, turn left or turn right) before getting in.

All biometric work stays on the server: face detection (MediaPipe), anti-spoofing (MiniFASNet, ONNX) and face matching (ArcFace, ONNX). The app captures camera frames and sends them over the API. That keeps the APK small and means the security checks cannot be bypassed by changing the app.

## Handover: SDK and build details

| Item | Value |
|---|---|
| Application id | `com.adaptivemfa.app` |
| App name | Adaptive MFA |
| Version | 1.0.0 (versionCode 1) |
| Min SDK | 26 (Android 8.0) |
| Target SDK | 36 (Android 16) |
| Compile SDK | 37 (required by Compose 1.12) |
| Language / UI | Kotlin, Jetpack Compose (Material 3), CameraX 1.6.2 |
| Build tools | Android Gradle Plugin 9.5.1, Gradle 9.7.1 (wrapper included), JDK 17+ (Android Studio's bundled JDK works) |
| Permissions | `INTERNET`, `CAMERA` |
| Default server | `http://10.0.2.2:8000` (the emulator's address for the computer running the backend); change it in Settings |
| Debug APK | `app/build/outputs/apk/debug/app-debug.apk` |

## Login credentials

There is no built-in account. The face check compares against the face that was enrolled, so each tester creates their own account in the app:

1. Open the app, tap **No account yet? Enroll**.
2. Pick a username and password (suggested for testing: `demo` / `Demo@12345`), take a face photo, tap **Create account**.
3. Log out and log in again. The first login from the phone is **HIGH** risk (new device), so the face challenge appears. After it passes, the phone is a known device and later logins are **LOW** risk (password only).

To see the face check on every login, start the backend with `MFA_ALWAYS_REQUIRE_FACE_CHECK=1` (see `../backend/config.py`).

## Build

**Android Studio:** File, Open, choose this `mfa-android` folder, wait for Gradle sync, then Run (green triangle) on an emulator or a USB-connected phone. Build, Build App Bundle(s) / APK(s), Build APK(s) writes the APK.

**Command line (Windows):**

```powershell
cd mfa-android
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:testDebugUnitTest     # unit tests, no device needed
.\gradlew.bat :app:assembleDebug         # app\build\outputs\apk\debug\app-debug.apk
```

## Tests

| Kind | Where | Command | What it covers |
|---|---|---|---|
| Unit (JVM) | `src/test/.../ApiTest.kt`, `LogicTest.kt`, `AppViewModelTest.kt` | `gradlew :app:testDebugUnitTest` | Request/response contract for every endpoint, error messages, burst constants, data URLs, address cleanup; every ViewModel flow (low-risk login, face check pass/fail, enroll, expired token, settings, navigation) |
| Integration (JVM, real HTTP) | `src/test/.../BackendIntegrationTest.kt` | same command | The real network stack against a local server that follows `backend/main.py`'s rules: enroll, HIGH-risk login with face check, then LOW-risk login; session is single use and bound to the device; forged token refused; server down |
| UI (emulator or phone) | `src/androidTest/.../AppUiTest.kt` | `gradlew :app:connectedDebugAndroidTest` (start an emulator first) | The installed app: login button rules, enroll password check and photo requirement, settings address validation, unreachable-server message |

Last run: 37 JVM tests and 4 UI tests passed (UI tests on the Android 17 / API 37 emulator). The camera was also checked by hand on the emulator: a photo is captured, rotated and shown on the Enroll screen.

The full flow against the **real** Python backend (actual face matching) is not automated: it needs the backend running with `models/arcface/arcface.onnx` and a real face in front of the camera.

If Gradle cannot find the SDK, create `local.properties` here with `sdk.dir=C\:\\Users\\<you>\\AppData\\Local\\Android\\Sdk` (Android Studio does this on first open).

## Run against the backend

1. Start the backend from `../backend` (see the root README; it needs `models/arcface/arcface.onnx`):
   `uvicorn main:app --host 0.0.0.0 --port 8000`
2. **Emulator:** install the APK (drag it onto the emulator, or `adb install -r app-debug.apk`). The default server address already points at your computer. In the emulator's settings (AVD Manager, Edit, Advanced), set the front camera to **Webcam0** so it uses your laptop webcam.
3. **Real phone:** put the phone on the same Wi-Fi as the computer, open **Settings** in the app, enter `http://<computer's Wi-Fi IP>:8000` (find it with `ipconfig`), tap **Test connection**. Windows Firewall must allow inbound connections on port 8000 for Python.

Plain HTTP is allowed in **debug** builds only (`src/debug/AndroidManifest.xml`). A release build needs the backend behind HTTPS.

## How the face check works in the app

- `/login/step1` returns `require_step2`, the risk score, a single-use `session_id` (valid for about 90 s) and the challenge.
- The face screen shows the challenge. When the user taps **Verify identity**, it captures **13 frames, 150 ms apart** (same as the web client), as JPEG data URLs of at most 720 px, and posts them to `/login/step2` with the session id and the device id.
- Frames are **not mirrored** (the server's head-turn direction check expects unmirrored frames); only the on-screen preview is mirrored, so turning left moves your face left on screen.
- Every step-2 attempt uses up the session, so after a failure the app returns to the login screen for a new challenge.
- The device id (`android_<random>`) is generated once per install and kept in app storage. Clearing app data or reinstalling makes the phone a new device again.

## Layout

| Path | What |
|---|---|
| `data/` | `Http` (the one network class, swappable in tests), `Api` (enroll, login step 1 and 2, history, ping), `Store` (token, username, server address, device id) |
| `logic/` | Pure Kotlin, unit tested: burst constants, challenge prompts, data-URL encoding, server-address and timestamp helpers |
| `camera/FaceCamera.kt` | CameraX front-camera preview and `FrameGrabber`, which hands out one upright JPEG per request; camera permission prompt |
| `ui/` | Compose screens (Login, Enroll, Face check, Dashboard, Settings) and `AppViewModel` |
| `src/test/` | JVM unit tests for the API client and logic |

## Known limits

- The app has no on-device face detector, so the **Verify identity** button does not wait for a face to be in view (the web app uses BlazeFace for that). The server still rejects bursts without a usable face.
- Not tested on a physical phone yet. The gesture and anti-spoofing thresholds were tuned on webcam frames, so phone cameras may need calibration (`MFA_LIVENESS_THRESHOLD`, `MFA_BLINK_EAR_THRESHOLD`, `MFA_HEAD_TURN_DISPLACEMENT_THRESHOLD`).
- The token is kept in app-private storage (SharedPreferences), not the Android Keystore. No Play Integrity or hardware attestation: like the web app, the server cannot prove the frames came from a live camera.
