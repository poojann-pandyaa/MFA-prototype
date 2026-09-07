# RawNet2 Presentation Attack Detection (Anti-Spoofing)

## 1. Overview & Purpose
In voice biometrics, speaker verification models determine **identity** (*"Is this Person A?"*), but they are fundamentally vulnerable to **presentation attacks** (*"Is this audio from a live human or a synthetic voice / voice conversion model?"*).

RawNet2 functions as the upstream **Presentation Attack Detection (PAD)** gatekeeper:
- Classifies incoming audio strictly as **BONAFIDE** vs. **SPOOF**.
- Targets speech synthesis (TTS) and voice conversion (VC) attacks.
- Operates in a speaker-agnostic manner (zero identity awareness).

---

## 2. Upstream Provenance & Attribution
- **Repository Source**: Ported from the official NTU-ROSE RawNet2 implementation:  
  [https://github.com/NTU-ROSE/RawNet2](https://github.com/NTU-ROSE/RawNet2)
- **Baseline Alignment**: Aligned with the official ASVspoof 2021 baseline architecture:  
  [ASVspoof 2021 LA Baseline-RawNet2](https://github.com/asvspoof-challenge/2021/tree/main/LA/Baseline-RawNet2)
- **Original Authors**: Hemlata Tak (`tak@eurecom.fr`), Jose Patino, Massimiliano Todisco, Jee-weon Jung (EURECOM, 2021).
- **Academic Citation**:
  > Hemlata Tak, Jose Patino, Massimiliano Todisco, Andreas Nautsch, Nicholas Evans, Anthony Larcher.  
  > *"End-to-End anti-spoofing with RawNet2"*, IEEE ICASSP 2021, pp. 6369-6373.
- **Model Checkpoint**: `pre_trained_DF_RawNet2.pth` (17,621,410 parameters, 67.25 MB).
- **Training Protocol**: Trained on the **ASVspoof 2019 Logical Access (LA) train partition** (2,580 bona fide, 22,800 spoof utterances covering attacks A01–A06).

---

## 3. Architecture & Audio Preprocessing

### 3.1 Time-Domain Front-End
- **Input Waveform**: Direct 1D raw time-domain audio (bypassing spectrogram or MFCC/LFCC computation).
- **Sampling Rate**: Strictly **16,000 Hz mono**.
- **Fixed Sample Length**: `nb_samp = 64,600` samples (~4.0375 seconds).
- **Padding & Slicing Protocol**:
  - Waveforms shorter than 64,600 samples are repeated via `np.tile(x, repeats)[:64600]`.
  - Waveforms longer than 64,600 samples are truncated to the first 64,600 samples.

### 3.2 Network Topology
- **Sinc-Convolutional Layer**: Filter length of 1,024, 20 output channels, bandpass cutoff frequencies initialized according to Mel-scale distribution.
- **Residual Blocks**: 6 residual blocks configured with Max-Pooling and Squeeze-and-Excitation (SE) channel-wise attention.
- **Recurrent Layer**: 3-layer bidirectional GRU with hidden dimension 1,024.
- **Classification Head**: Fully connected layer (1,024 $ightarrow$ 1,024 $ightarrow$ 2) followed by `LogSoftmax(dim=1)` over classes `[0: SPOOF, 1: BONAFIDE]`.

### 3.3 Score Semantics
- **Canonical Score**: `rawnet2_score = model_output[0, 1]` (the BONAFIDE log-probability).
- **Interpretation**: Higher score = more bona fide (closer to 0.0); lower score = higher probability of spoof.
- **Decision Rule**: `BONAFIDE` if `rawnet2_score > threshold` else `SPOOF`.

---

## 4. Benchmark Dataset & Protocol

Evaluation was conducted using a deterministic, balanced benchmark subset:
- **Evaluation Dataset**: 1,000 utterances from the official **ASVspoof 2019 LA evaluation partition** (`SpeechAntiSpoofingBenchmarks/ASVspoof2019_LA`, Shard 0).
- **Composition**: Exactly **500 BONAFIDE** and **500 SPOOF** utterances.
- **Deterministic Sampling**: Seed `42`. Full manifest recorded in `results/rawnet2/evaluation_manifest.csv`.
- **Attack Category Coverage (A07–A19)**:
  - `A07` (Neural TTS): 39 samples
  - `A08` (Neural TTS): 39 samples
  - `A09` (Vocoder-based TTS): 39 samples
  - `A10` (Neural TTS): 39 samples
  - `A11` (Concatenative TTS): 39 samples
  - `A12` (Concatenative TTS): 39 samples
  - `A13` (Neural VC / VAE): 38 samples
  - `A14` (Neural VC): 38 samples
  - `A15` (Neural VC): 38 samples
  - `A16` (Waveform concatenation VC): 38 samples
  - `A17` (Spectral transfer VC - Unseen): 38 samples
  - `A18` (Neural TTS): 38 samples
  - `A19` (Neural TTS): 38 samples

---

## 5. Benchmark Results

### 5.1 PAD Error Metrics
| Metric | Description | Value |
| :--- | :--- | :---: |
| **Equal Error Rate (EER)** | Point where False Acceptance Rate = False Rejection Rate | **32.60%** |
| **Experimental EER Threshold** | Calibrated threshold at EER operating point | **`-4.4516`** |
| **ROC-AUC** | Area under Receiver Operating Characteristic curve | **`0.7339`** |
| **ACER @ EER Threshold** | Average Classification Error Rate: `(APCER + BPCER) / 2` | **`32.50%`** |
| **APCER @ EER Threshold** | Attack Presentation Classification Error Rate (Spoofs accepted) | **`32.60%`** |
| **BPCER @ EER Threshold** | Bona Fide Presentation Classification Error Rate (Bona fide rejected) | **`32.40%`** |
| **Spoof Rejection @ EER** | Percentage of presentation attacks correctly blocked | **`67.40%`** |
| **Bona Fide Detection @ EER** | Percentage of genuine speech correctly accepted | **`67.60%`** |

### 5.2 Default Argmax Threshold (`log(0.5) ≈ -0.6931`)
- **APCER**: `11.60%` (Blocks **88.40%** of all presentation attacks).
- **BPCER**: `57.40%` (Conservative bias; false rejection of bona fide utterances).
- **ACER**: `34.50%`.

### 5.3 Attack-Specific Rejection Rates (@ Experimental EER Threshold)
- **A09 (Vocoder-based TTS)**: **100.00%** Spoof Rejection (0.00% APCER)
- **A14 (Neural VC)**: **84.21%** Spoof Rejection (15.79% APCER)
- **A19 (Neural TTS)**: **84.21%** Spoof Rejection (15.79% APCER)
- **A13 (Neural VC / VAE)**: **81.58%** Spoof Rejection (18.42% APCER)
- **A17 (Spectral transfer VC - Unseen)**: **73.68%** Spoof Rejection (26.32% APCER)
- **A11 (Concatenative TTS)**: **66.67%** Spoof Rejection (33.33% APCER)
- **A07 (Neural TTS)**: **61.54%** Spoof Rejection (38.46% APCER)
- **A12 (Concatenative TTS)**: **61.54%** Spoof Rejection (38.46% APCER)
- **A08 (Neural TTS)**: **58.97%** Spoof Rejection (41.03% APCER)
- **A15 (Neural VC)**: **55.26%** Spoof Rejection (44.74% APCER)
- **A10 (Neural TTS)**: **51.28%** Spoof Rejection (48.72% APCER)
- **A18 (Neural TTS)**: **50.00%** Spoof Rejection (50.00% APCER)
- **A16 (Concatenation VC)**: **47.37%** Spoof Rejection (52.63% APCER)

### 5.4 Inference Latency Profile (NVIDIA GeForce RTX 3050 Laptop GPU)
- **Cold-Start Model Loading Time**: **0.5284 s**
- **Mean Per-Utterance Latency**: **23.57 ms**
- **Median Latency**: **22.22 ms**
- **P95 Latency**: **30.27 ms**
- **Min / Max Latency**: **19.18 ms / 68.00 ms**

---

## 6. Critical Security Qualifications & Limitations

> [!WARNING]
> **Research Benchmark Disclaimer**:
> These benchmark results are derived from a deterministic 1,000-sample subset of the ASVspoof 2019 Logical Access evaluation partition. They must **NOT** be presented as proof of production-grade anti-spoofing security.

1. **Acoustic Domain Mismatch**: RawNet2 operates directly on raw waveforms and was trained on studio-quality speech (VCTK). Sinc filters are sensitive to environmental acoustic reverberation, microphone frequency response differences, and codec compression artifacts.
2. **Channel Sensitivity**: In field deployments, background ambient noise or lossy Bluetooth/VoIP codecs can shift bona fide log-probabilities downward, causing false rejections (elevated BPCER).
3. **Operational Guidance**: In production Adaptive MFA architectures, RawNet2 should be combined with an upstream Voice Activity Detector (VAD), mild spectral noise reduction, and tiered fallback to out-of-band factors.
