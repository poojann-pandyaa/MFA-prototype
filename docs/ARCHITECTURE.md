# System Architecture: Voice Biometric Authentication for Adaptive MFA

## 1. Executive Summary

This repository provides the **Voice Biometric Authentication** component for an enterprise **Adaptive Multi-Factor Authentication (MFA)** system. 

Voice authentication combines physical and behavioral acoustic characteristics to verify a user's claim of identity. However, voice biometric systems are susceptible to presentation attacks—including text-to-speech (TTS) synthesis, voice conversion (VC), and recorded replays.

To address this threat model, the system decouples **Presentation Attack Detection (PAD)** from **Speaker Verification (SV)** into a sequential two-stage verification pipeline:

```
Voice Input Audio (16 kHz mono)
         │
         ▼
┌────────────────────────────────────────┐
│   RawNet2 Presentation Attack Detection │
│   (SincConv + ResBlocks + GRU)         │
└───────────────────┬────────────────────┘
                    │
           Is audio BONAFIDE?
           ├── NO (SPOOF)  ──► [REJECT AUTHENTICATION: Presentation Attack Detected]
           │                   (Early exit: prevents unnecessary downstream compute)
           └── YES (BONAFIDE)
                    │
                    ▼
┌────────────────────────────────────────┐
│     ECAPA-TDNN Speaker Verification    │
│  (speechbrain/spkrec-ecapa-voxceleb)   │
└───────────────────┬────────────────────┘
                    │
        Matches Enrolled Voiceprint?
        ├── NO (IMPOSTOR) ──► [REJECT AUTHENTICATION: Speaker Mismatch]
        └── YES (GENUINE) ──► [VOICE MFA FACTOR PASSES]
```

---

## 2. Crucial Security Boundary: Voice Factor ≠ Final Authentication

> [!IMPORTANT]
> **Authentication Scope & Authorization Boundary**:
> A successful voice factor only indicates that the **voice authentication factor passed**; it does **not** by itself constitute final user authentication or authorization.
>
> - **RawNet2 passes + ECAPA-TDNN passes $ightarrow$ Voice MFA factor passes.**
> - It does **NOT** mean the user is granted access to the underlying application or resource.
> - The broader **Adaptive MFA Risk Engine** aggregates this factor alongside device signals, IP reputation, behavioral heuristics, and time-of-day risk scores before determining the final authentication and authorization policy decision.

---

## 3. Separation of Responsibilities

| Dimension | Stage 1: Presentation Attack Detection | Stage 2: Speaker Verification | Stage 3: MFA Risk Engine |
| :--- | :--- | :--- | :--- |
| **Component** | **RawNet2** | **ECAPA-TDNN** | **Adaptive MFA Policy Engine** |
| **Core Question** | *"Is this audio genuine human speech or a synthetic presentation attack?"* | *"Does this speech match the enrolled acoustic profile of user $U$?"* | *"Given all authentication factors and risk context, should access be granted?"* |
| **Model Source** | NTU-ROSE / ASVspoof 2021 Baseline | SpeechBrain (`spkrec-ecapa-voxceleb`) | Contextual Risk Scoring Rules |
| **Input** | 64,600 raw waveform samples (16 kHz mono) | 16 kHz audio waveform | Voice Factor result + contextual signals |
| **Output** | LogSoftmax score $\in [-\infty, 0]$ (Bona Fide log-prob) | 192-d unit embedding $ightarrow$ Cosine similarity $\in [-1, 1]$ | Policy decision: `ALLOW`, `STEP_UP`, `DENY` |
| **Identity Awareness** | **Zero identity awareness** (speaker-agnostic) | **Identity-specific** (requires enrolled voiceprint) | Multi-factor correlation |
| **Failure Mode** | Classifies synthetic speech as BONAFIDE (APCER) | Falsely accepts impostor (FAR) or rejects genuine (FRR) | Security policy violation |

---

## 4. End-to-End Authentication Sequence

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Client Device
    participant Gateway as API Gateway / Auth Broker
    participant Pipeline as VoiceBiometricPipeline
    participant RawNet2 as RawNet2 PAD Engine
    participant ECAPA as ECAPA-TDNN Verifier
    participant Engine as Adaptive MFA Risk Engine

    User->>Gateway: Submit Voice Audio Sample (16 kHz) + User ID
    Gateway->>Pipeline: authenticate_factor(user_voiceprint, audio)
    
    rect rgb(240, 245, 255)
    Note over Pipeline,RawNet2: Stage 1: Presentation Attack Detection
    Pipeline->>RawNet2: predict(audio, threshold=pad_threshold)
    RawNet2-->>Pipeline: {predicted_label, rawnet2_score, prob_bonafide}
    
    alt Audio is SPOOF (Attack Detected)
        Pipeline-->>Gateway: {voice_factor_passed: false, reason: "PRESENTATION_ATTACK_DETECTED"}
        Gateway->>Engine: Report Security Event: Biometric Spoof Attempt
        Engine-->>User: Terminate Session / Trigger Security Alert
    end
    end

    rect rgb(245, 255, 240)
    Note over Pipeline,ECAPA: Stage 2: Speaker Verification (Only if BONAFIDE)
    Pipeline->>ECAPA: verify(enrolled_voiceprint, audio, threshold=verif_threshold)
    ECAPA-->>Pipeline: {verified: true/false, cosine_similarity, decision}
    
    alt Similarity < Threshold (Speaker Mismatch)
        Pipeline-->>Gateway: {voice_factor_passed: false, reason: "SPEAKER_IDENTITY_MISMATCH"}
        Gateway->>Engine: Report Failed Voice Factor
        Engine-->>User: Authentication Challenge Failed
    else Similarity >= Threshold (Speaker Match)
        Pipeline-->>Gateway: {voice_factor_passed: true, status: "PASSED"}
    end
    end

    rect rgb(255, 250, 240)
    Note over Gateway,Engine: Stage 3: MFA Policy Resolution
    Gateway->>Engine: Submit Factor Success + Device Signals + GeoIP
    Engine-->>User: Final MFA Decision (Granted / Step-Up Challenge)
    end
```

---

## 5. Architectural Failure Mode Analysis

### 5.1 Presentation Attack (Deepfake / TTS / Voice Conversion)
- **Mechanism**: Attacker submits high-quality neural synthetic speech cloned from the genuine user.
- **Pipeline Handling**: RawNet2 evaluates the raw 1D time-domain waveform through its sinc filters. Artifacts in phase coherence, harmonic decay, and spectral envelope cause the bona fide log-probability to drop below threshold.
- **Outcome**: Early exit. The audio is flagged as `SPOOF`, authentication is terminated, and downstream ECAPA computation is bypassed.

### 5.2 Zero-Effort Impostor Attack (Human Mimic / Wrong Caller)
- **Mechanism**: A human impostor attempts to speak the passphrase or authenticate on behalf of another user.
- **Pipeline Handling**: RawNet2 detects genuine human speech and classifies the utterance as `BONAFIDE`. The sample passes to Stage 2. ECAPA-TDNN extracts a 192-dimensional speaker embedding and compares it against the victim's enrolled voiceprint. The cosine similarity falls well below the verification threshold (mean impostor similarity: 0.1234).
- **Outcome**: The voice factor is rejected with `SPEAKER_IDENTITY_MISMATCH`.

### 5.3 Acoustic Degradation & Channel Mismatch
- **Mechanism**: Genuine user speaks in a noisy environment or through a low-bitrate microphone.
- **Pipeline Handling**: Heavy background noise or clipping may degrade RawNet2 confidence (raising BPCER) or reduce ECAPA cosine similarity.
- **Adaptive Fallback**: Because the voice factor does not execute isolated authorization, the Adaptive MFA risk engine detects the low-confidence voice factor and falls back to an alternative secondary factor (e.g., FIDO2 hardware token or push notification).

---

## 6. Component Orchestration Implementation

In accordance with architectural standards:
- `src/pipeline.py` serves strictly as the orchestration layer between `RawNet2Detector` and `SpeakerVerifier`.
- It does **not** duplicate, modify, or retrain the underlying neural architectures.
- It provides timing instrumentation, standardized failure logging, and factor-level status reporting.
