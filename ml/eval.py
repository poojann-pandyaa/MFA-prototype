"""
Evaluation: APCER / BPCER / ACER per ISO/IEC 30107-3, per attack type and
overall, plus BPCER @ APCER=1% (the operating point that actually matters -
"how many genuine users get rejected once attacks are capped at 1%
acceptance").

    python eval.py --manifest data/manifest.csv --split test --checkpoint checkpoints/best.pt

Definitions (ISO/IEC 30107-3):
  APCER = Attack Presentation Classification Error Rate
          = fraction of ATTACK samples incorrectly classified as live
            (this is the model letting spoofs through)
  BPCER = Bona-fide Presentation Classification Error Rate
          = fraction of LIVE (genuine) samples incorrectly classified as spoof
            (this is the model rejecting real users)
  ACER  = (APCER + BPCER) / 2
"""
import argparse

import numpy as np
import torch
from torch.utils.data import DataLoader

from data.manifest import ATTACK_TYPES, PadDataset, read_manifest
from model import PadModel


def compute_scores(model, loader, device):
    model.eval()
    all_scores, all_labels, all_attack_idx = [], [], []
    with torch.no_grad():
        for imgs, labels, attack_idx, _ in loader:
            imgs = imgs.to(device)
            binary_logit, _, _ = model(imgs)
            scores = torch.sigmoid(binary_logit).cpu().numpy()
            all_scores.append(scores)
            all_labels.append(labels.numpy())
            all_attack_idx.append(attack_idx.numpy())
    return (np.concatenate(all_scores), np.concatenate(all_labels).astype(int),
            np.concatenate(all_attack_idx).astype(int))


def apcer_bpcer_acer(scores, labels, threshold: float):
    preds_live = scores > threshold  # True = classified as live

    live_mask = labels == 1
    attack_mask = labels == 0

    bpcer = float(np.mean(~preds_live[live_mask])) if live_mask.any() else float("nan")
    apcer = float(np.mean(preds_live[attack_mask])) if attack_mask.any() else float("nan")
    acer = (apcer + bpcer) / 2 if not (np.isnan(apcer) or np.isnan(bpcer)) else float("nan")
    return apcer, bpcer, acer


def bpcer_at_apcer_target(scores, labels, target_apcer: float = 0.01):
    """Sweep thresholds to find BPCER at the operating point where APCER == target."""
    attack_scores = scores[labels == 0]
    live_scores = scores[labels == 1]
    if len(attack_scores) == 0 or len(live_scores) == 0:
        return float("nan"), float("nan")

    thresholds = np.unique(scores)
    best_bpcer, best_thr = float("nan"), float("nan")
    for thr in thresholds:
        apcer = float(np.mean(attack_scores > thr))
        if apcer <= target_apcer:
            bpcer = float(np.mean(live_scores <= thr))
            if np.isnan(best_bpcer) or bpcer < best_bpcer:
                best_bpcer, best_thr = bpcer, thr
    return best_bpcer, best_thr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--batch-size", type=int, default=128)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    rows = read_manifest(args.manifest, split=args.split)
    if not rows:
        raise SystemExit(f"No rows with split={args.split} in {args.manifest}")

    loader = DataLoader(PadDataset(rows, augment=False), batch_size=args.batch_size, shuffle=False)

    model = PadModel(pretrained=False).to(device)
    model.load_state_dict(torch.load(args.checkpoint, map_location=device))

    scores, labels, attack_idx = compute_scores(model, loader, device)

    print(f"\n=== Overall ({len(rows)} samples, threshold={args.threshold}) ===")
    apcer, bpcer, acer = apcer_bpcer_acer(scores, labels, args.threshold)
    print(f"APCER={apcer:.4f}  BPCER={bpcer:.4f}  ACER={acer:.4f}")

    bpcer_1pct, thr_1pct = bpcer_at_apcer_target(scores, labels, 0.01)
    print(f"BPCER @ APCER=1%: {bpcer_1pct:.4f} (threshold={thr_1pct:.4f})")

    print("\n=== Per attack type (APCER only meaningful for attack rows) ===")
    for i, attack_name in enumerate(ATTACK_TYPES):
        if attack_name == "live":
            continue
        mask = (attack_idx == i) | (labels == 1)  # this attack type + all live samples
        if mask.sum() == 0:
            continue
        a, b, c = apcer_bpcer_acer(scores[mask], labels[mask], args.threshold)
        n_attack = int(((attack_idx == i) & (labels == 0)).sum())
        print(f"  {attack_name:16s} (n={n_attack:5d})  APCER={a:.4f}  BPCER={b:.4f}  ACER={c:.4f}")


if __name__ == "__main__":
    main()
