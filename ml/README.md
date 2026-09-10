# PAD training pipeline (prototype)

This is the trainable half of the "all cases of spoofing" ask: a small,
multi-task presentation-attack-detection (PAD) model you own end-to-end,
as opposed to the vendored pretrained MiniFASNet weights the backend ships
with today (`backend/anti_spoofing/`, now served via ONNX Runtime - see
`backend/services/pad_onnx.py`).

**Status: this code has not been trained on real data.** There is no GPU
and no dataset in the sandbox this was built in, so what's here has only
been smoke-tested end-to-end on a tiny synthetic batch (random noise
images) to prove the training/eval/export loop actually runs without
crashing - see "What was verified" below. Getting an actually-useful
model out of this requires real data and a GPU; both are free and
described below.

## Why one model, three heads

- **Binary live/spoof** - the decision that actually matters.
- **Attack type** (4 classes: `live / print / replay_screen / cutout_mask`)
  - forces the network to learn *why*, not just *whether*, which makes
    failures debuggable and improves the shared feature space for the
    binary head. Limited to the 4 classes free datasets actually label
    well; see "Known gaps" below.
- **FFT magnitude map** (32x32, auxiliary regression target) - print and
  replay-screen attacks leak moire patterns, halftone dots, and pixel-grid
  banding that a flat classifier tends to ignore in favor of easier
  dataset-specific color cues. Forcing the network to also predict the
  frequency-domain signature of the input is a free (no extra labels,
  just `np.fft.fft2` on the crop) way to push it toward learning that
  physical signal instead. This is the same trick already used by the
  vendored `MultiFTNet` in `backend/anti_spoofing/src/model_lib/MultiFTNet.py`
  (Fourier-map auxiliary supervision) - reused here rather than invented.

Backbone: MobileNetV3-Small (torchvision, ImageNet-pretrained), 128x128
input. ~1.5-2M params, exports to a ~1.5MB ONNX file after int8
quantization - small enough to run in a browser tab (ONNX Runtime Web,
WASM+SIMD) and on a mid-range phone (ONNX Runtime Mobile) without being a
noticeable RAM or battery cost.

## Layout

```
ml/
  data/
    manifest.py            # unified manifest schema + PyTorch Dataset
    prepare_celeba_spoof.py  # adapter: CelebA-Spoof dir layout -> manifest.csv
    make_synthetic.py      # generates a tiny synthetic dataset for smoke-testing
  model.py                 # MobileNetV3-Small + 3 heads
  train.py                 # training loop
  eval.py                  # APCER / BPCER / ACER (ISO/IEC 30107-3), per attack type
  export.py                # checkpoint -> ONNX (+ parity check)
  requirements.txt         # training-only deps, deliberately NOT in backend/requirements.txt
```

## Getting free data

| Dataset | Size | Cost | Notes |
|---|---|---|---|
| [CelebA-Spoof](https://github.com/ZhangYuanhan-AI/CelebA-Spoof) | 625k images | Free download | Largest, 10 spoof types. License is non-commercial research - fine for a prototype, check terms before anything commercial. Sample ~40-60k images rather than downloading all 76GB. |
| [LCC-FASD](https://www.kaggle.com/datasets/faber24/lcc-fasd) | ~30k | Free (Kaggle) | Permissive license. |
| [NUAA Imposter DB](http://parnec.nuaa.edu.cn/_upload/tpl/02/db/731/template731/pages/xtan/NUAAImposterDB_download.html) | ~12k | Free | Older, print attacks only, zero-friction download - good for a first smoke test on real data. |
| Your own capture | 2-5k+ | Free | 2 days with ~20-30 people on the actual devices you'll deploy to. **This matters more than any of the above** - cross-device transfer is where PAD models fail in practice, and public datasets won't cover your specific cameras/screens. |

Use `data/prepare_celeba_spoof.py` as a template adapter; write similar
one-off scripts for the other sources into the same `manifest.csv` schema
(`path,label,attack_type,split,device`).

## Training (needs a GPU - Colab's free T4 tier is enough)

```bash
cd ml
pip install -r requirements.txt
python train.py --manifest data/manifest.csv --epochs 30 --batch-size 128
```

~2-3 hours on a free Colab T4 for ~60k images / 30 epochs. Checkpoint to
Drive every epoch - free Colab sessions can disconnect.

Augmentation matters as much as architecture here: aggressive color
jitter, JPEG-quality jitter, random down/upscale, motion blur. These
simulate device variation that a small dataset won't otherwise cover.

## Evaluating

```bash
python eval.py --manifest data/manifest.csv --split test --checkpoint checkpoints/best.pt
```

Reports APCER / BPCER / ACER per attack type plus BPCER @ APCER=1%, using
ISO/IEC 30107-3 vocabulary so numbers are comparable to published results
(e.g. OULU-NPU protocol papers).

## Exporting

```bash
python export.py --checkpoint checkpoints/best.pt --out ../backend/models/pad_custom.onnx
```

Exports fp32 ONNX and runs a parity check against the PyTorch model on a
random batch before writing the file (same pattern as
`backend/anti_spoofing/export_onnx.py`). Point `backend/services/pad_onnx.py`
at the new file (or drop it into `backend/models/`, it's picked up
automatically) once you're happy with the eval numbers - and recalibrate
`MFA_LIVENESS_THRESHOLD` (see `backend/config.py`) against the new model's
score distribution rather than reusing the old model's threshold.

For int8 quantization (recommended before shipping to web/Android):
`onnxruntime.quantization.quantize_dynamic` on the exported fp32 file.

## What was verified in this sandbox

No GPU and no dataset were available here, so instead of training:

- `data/make_synthetic.py` generates ~40 tiny random-noise images with
  synthetic binary/attack-type labels.
- `train.py` was run for 1 epoch against that synthetic manifest on CPU,
  confirming the model builds, the three losses compute and backprop, and
  a checkpoint is written.
- `eval.py` was run against the same synthetic split, confirming the
  APCER/BPCER/ACER computation runs end-to-end and produces a report.
- `export.py` was run against the resulting checkpoint, confirming the
  ONNX export + parity check succeeds and the file loads in
  `onnxruntime`.

None of this means the model has learned anything useful - random noise
has no real signal, and one CPU epoch is not training. It means the code
path (data loading -> model -> losses -> checkpoint -> eval -> export) is
free of the syntax/shape/logic bugs that would otherwise only surface
after paying for GPU time on real data.

## Known gaps (see main conversation for the fuller list)

- No 3D-mask, silicone, or makeup attack classes - no free dataset covers
  them adequately.
- No deepfake/injection-attack branch - genuinely a different problem
  (FaceForensics++/Celeb-DF, different architecture), out of scope for
  this prototype pass.
- No domain-adversarial training (SSDG/SSAN) - needs more labeled domains
  than a prototype budget covers; cross-device generalization will be
  weaker than a production system without it.
- No temporal/rPPG head - needs video clips, not single frames.
