"""
Generates a tiny synthetic dataset purely to smoke-test the train/eval/
export pipeline end-to-end without a GPU or a real dataset (see
ml/README.md "What was verified in this sandbox").

This teaches the model NOTHING useful - it's random noise with random
labels. Its only job is proving data loading -> model -> loss ->
checkpoint -> eval -> export doesn't crash and shapes line up, before
spending real GPU time on real data.
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest import ATTACK_TYPES, ManifestRow, write_manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(__file__), "synthetic_images"))
    ap.add_argument("--manifest", default=os.path.join(os.path.dirname(__file__), "manifest_synthetic.csv"))
    ap.add_argument("--n-per-split", type=int, default=16)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    rng = np.random.default_rng(0)
    rows = []

    for split, n in [("train", args.n_per_split), ("val", max(4, args.n_per_split // 4)),
                      ("test", max(4, args.n_per_split // 4))]:
        for i in range(n):
            attack_type = ATTACK_TYPES[i % len(ATTACK_TYPES)]
            label = 1 if attack_type == "live" else 0
            # Slightly different noise statistics per class so the loss
            # has *something* non-degenerate to chew on during the smoke
            # test, even though this still isn't real signal.
            base = 180 if label == 1 else 90
            img = rng.normal(loc=base, scale=30, size=(128, 128, 3)).clip(0, 255).astype(np.uint8)
            fname = f"{split}_{i}_{attack_type}.jpg"
            path = os.path.join(args.out_dir, fname)
            cv2.imwrite(path, img)
            rows.append(ManifestRow(
                path=os.path.abspath(path), label=label, attack_type=attack_type,
                split=split, device="synthetic",
            ))

    write_manifest(args.manifest, rows)
    print(f"Wrote {len(rows)} synthetic images to {args.out_dir}")
    print(f"Wrote manifest to {args.manifest}")


if __name__ == "__main__":
    main()
