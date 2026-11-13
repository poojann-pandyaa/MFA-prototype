# DemoPay for Android

A native Android app (Kotlin, Jetpack Compose) for the DemoPay demo bank. It talks to the DemoPay API in `../backend` and runs the same payment flow as the web app: risk check, a plan of safety steps, a fake PIN pad, and the demo-only **Model X-ray**. It is separate from the existing Adaptive MFA app.

**Everything is simulated**: the bank, the money, the PIN and the payee directory. The phone signals are a mix: some are read for real on the phone (below), the rest are staged by the presenter.

## What is real on the phone

Settings, "Signals from this phone", lets the app tell the risk engine facts it reads itself. Only yes/no flags and the call length are sent, never numbers, contacts or audio.

| Signal | How it is read |
|---|---|
| Phone call in progress, and for how long | `TelephonyManager` call state (needs the READ_PHONE_STATE permission) |
| Screen mirroring / extra display | `DisplayManager` shows more than one display |
| Accessibility service enabled | `Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES` |
| Phone looks rooted | root helper binaries or a test-signed build (emulators are not counted) |

Not available on a phone without a trusted SDK or the telecom: SIM change, new-device binding, UPI PIN reset, typing rhythm. On the Send screen the presenter can stage these with the demo switches, which override the real readings.

## Build

Needs JDK 17 or 21 (Gradle 8.9 does not run on JDK 25) and the Android SDK with platform 34.

```bash
cd demopay/android
export JAVA_HOME=/path/to/jdk-21        # for example /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME=$HOME/Library/Android/sdk
./gradlew :app:testDebugUnitTest         # 20 unit tests, no device needed
./gradlew :app:assembleDebug             # app/build/outputs/apk/debug/app-debug.apk
```

## Run

1. Start the DemoPay API on your computer (see `../README.md`). Default address the app uses: `http://10.0.2.2:8070`, which is how the **Android emulator** reaches your computer's localhost.
2. **Emulator:** `adb install -r app/build/outputs/apk/debug/app-debug.apk`, then open DemoPay.
3. **Real phone** (USB debugging on): start the API with `--host 0.0.0.0`, put the phone on the same Wi-Fi, open Settings in the app and set the server address to `http://<your computer's address>:8070`.
4. Sign up, load a demo history on Home, pick a scenario on Send, tap Pay.

Cleartext HTTP is allowed in **debug** builds only (`src/debug/AndroidManifest.xml`), so the app can reach a plain-HTTP demo server. A release build would need HTTPS.

To try the call signal on an emulator: allow phone-call detection in Settings, then `adb emu gsm call 5551234` starts a call and `adb emu gsm cancel 5551234` ends it.

## Layout

| Path | What |
|---|---|
| `data/` | API client (HttpURLConnection behind a small `Http` interface), JSON models and parsing, settings store |
| `logic/` | Pure Kotlin that is unit tested: money formatting, step rules, signals merge, demo scenarios |
| `signals/PhoneSignals.kt` | Real readings from the phone |
| `ui/` | Compose screens: Login, Home, Send, Pay (steps, PIN pad, X-ray), Settings; `AppViewModel` holds the state |
| `src/test/` | Unit tests (API client with a fake network, JSON parsing, signals, logic) |

## Run it on an emulator (what was done to test it)

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager "emulator" "system-images;android-34;google_apis;arm64-v8a"   # about 2 GB, Apple Silicon image
echo no | $ANDROID_HOME/cmdline-tools/latest/bin/avdmanager create avd -n demopay_pixel -k "system-images;android-34;google_apis;arm64-v8a" -d pixel_6
$ANDROID_HOME/emulator/emulator -avd demopay_pixel -no-snapshot -no-audio &
$ANDROID_HOME/platform-tools/adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## Tested on a Pixel 6 emulator (Android 14)

Checked by hand through `adb` and screenshots: sign-up, loading a persona, the scenario chips, a payment with the three-step safety check (payee check, a wrong then right typed name, scam warning), the "yes to a danger question" stop, the PIN pad and the balance change, Settings and the phone-call permission, and the **real call signal**: `adb emu gsm call` made the app send `call_active: true` with the call length, and the model's X-ray showed those inputs. The Android permission dialog calls this "make and manage phone calls": that is the system's wording for the whole phone permission group; the app only reads whether a call is in progress.

## Known limits

- Tested on an emulator only, not on a physical phone, and without automated on-device (instrumented) tests. The 20 unit tests run on the JVM.
- Only the four authenticators the backend has built are supported (warning, typed name, payee check, cooling-off wait). Biometric prompt, device key (Android Keystore), second-device approval and behavioural biometrics are planned.
- No Play Integrity (needs a Play-enabled device and a cloud project); the root check is a simple heuristic.
- Screen mirroring and accessibility detection are implemented but were not exercised on the emulator.
