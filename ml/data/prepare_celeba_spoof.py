"""
Adapter: CelebA-Spoof directory layout -> ml/data/manifest.py schema.

Not run in this sandbox (dataset isn't downloaded here) - this documents
the exact mapping so it can be run wherever the dataset lives.

Expected CelebA-Spoof layout (per the official repo):
    CelebA_Spoof/
      Data/
        train/<id>/live/*.jpg
        train/<id>/spoof/*.jpg
        test/<id>/live/*.jpg
        test/<id>/spoof/*.jpg
      metas/intra_test/{train,test}_label.json   # per-image attack-type labels

CelebA-Spoof's own label scheme has ~10 spoof types (print x2, replay x2,
paper-cut x3, 3D mask, etc.) encoded as an integer in the metadata JSON.
This adapter collapses that into the 4 coarse classes this prototype's
model actually trains on (see ml/README.md "Known gaps" for why): live,
print, replay_screen, cutout_mask. Extend CELEBA_SPOOF_TYPE_MAP if you
want finer-grained classes later - the model architecture already
supports adding classes to the attack-type head.

Usage (once you have the dataset locally):
    python prepare_celeba_spoof.py \
        --root /path/to/CelebA_Spoof \
        --out manifest.csv \
        --sample-per-split 30000
"""
import argparse
import json
import os
import random

from manifest import ManifestRow, write_manifest

# CelebA-Spoof spoof_type values -> our coarse attack_type buckets.
# See the dataset's own readme for the full type table; unlisted/unknown
# values fall back to "print" as the most common attack in the set.
CELEBA_SPOOF_TYPE_MAP = {
    0: "live",
    1: "print", 2: "print", 3: "print",          # print attacks (photo, poster, A4)
    4: "replay_screen", 5: "replay_screen", 6: "replay_screen",  # replay on phone/pad/monitor
    7: "cutout_mask", 8: "cutout_mask", 9: "cutout_mask",        # paper-cut / mask variants
}


def build_split(root: str, split: str, sample_n: int | None) -> list[ManifestRow]:
    meta_path = os.path.join(root, "metas", "intra_test", f"{split}_label.json")
    with open(meta_path) as f:
        labels = json.load(f)  # {relative_path: [40 attributes], spoof_type is index 40}

    items = list(labels.items())
    if sample_n and len(items) > sample_n:
        random.seed(42)
        items = random.sample(items, sample_n)

    rows = []
    for rel_path, attrs in items:
        full_path = os.path.join(root, "Data", rel_path)
        spoof_type = int(attrs[40])  # per CelebA-Spoof's metadata schema
        attack_type = CELEBA_SPOOF_TYPE_MAP.get(spoof_type, "print")
        label = 1 if attack_type == "live" else 0
        # CelebA-Spoof train/ is further split here into train/val 90/10;
        # its own test/ becomes our held-out test set.
        our_split = split if split == "test" else ("val" if random.random() < 0.1 else "train")
        rows.append(ManifestRow(
            path=full_path, label=label, attack_type=attack_type,
            split=our_split, device="celeba_spoof",
        ))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Path to extracted CelebA_Spoof/ directory")
    ap.add_argument("--out", default="manifest.csv")
    ap.add_argument("--sample-per-split", type=int, default=30000,
                     help="Cap images per source split to avoid downloading/processing all 625k")
    args = ap.parse_args()

    rows = []
    for split in ["train", "test"]:
        rows.extend(build_split(args.root, split, args.sample_per_split))

    write_manifest(args.out, rows)
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
