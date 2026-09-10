"""
Training loop for the PAD model.

    python train.py --manifest data/manifest.csv --epochs 30 --batch-size 128

See ml/README.md for where to get real data and how long this takes on a
free Colab T4. This has only been smoke-tested on synthetic data in this
environment (no GPU here) - see ml/README.md "What was verified".
"""
import argparse
import os

import torch
from torch.utils.data import DataLoader

from data.manifest import PadDataset, read_manifest
from model import PadModel


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight-decay", type=float, default=5e-4)
    ap.add_argument("--attack-loss-weight", type=float, default=0.5)
    ap.add_argument("--fft-loss-weight", type=float, default=0.1)
    ap.add_argument("--num-workers", type=int, default=4)
    ap.add_argument("--checkpoint-dir", default="checkpoints")
    ap.add_argument("--no-pretrained", action="store_true",
                     help="Train backbone from random init instead of ImageNet weights (not recommended, see model.py)")
    return ap.parse_args()


def run_epoch(model, loader, optimizer, device, weights, train: bool):
    model.train(mode=train)
    bce = torch.nn.BCEWithLogitsLoss()
    ce = torch.nn.CrossEntropyLoss()
    mse = torch.nn.MSELoss()

    total_loss, total_correct, total_n = 0.0, 0, 0
    torch.set_grad_enabled(train)
    for imgs, labels, attack_idx, fft_targets in loader:
        imgs = imgs.to(device)
        labels = labels.to(device)
        attack_idx = attack_idx.to(device)
        fft_targets = fft_targets.to(device)

        binary_logit, attack_logits, fft_pred = model(imgs)

        loss_binary = bce(binary_logit, labels)
        loss_attack = ce(attack_logits, attack_idx)
        loss_fft = mse(fft_pred, fft_targets)
        loss = loss_binary + weights[0] * loss_attack + weights[1] * loss_fft

        if train:
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        preds = (torch.sigmoid(binary_logit) > 0.5).float()
        total_correct += (preds == labels).sum().item()
        total_n += labels.size(0)
        total_loss += loss.item() * labels.size(0)

    return total_loss / max(total_n, 1), total_correct / max(total_n, 1)


def main():
    args = parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    train_rows = read_manifest(args.manifest, split="train")
    val_rows = read_manifest(args.manifest, split="val")
    if not train_rows:
        raise SystemExit(f"No rows with split=train in {args.manifest}")

    train_ds = PadDataset(train_rows, augment=True)
    val_ds = PadDataset(val_rows, augment=False) if val_rows else None

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                               num_workers=args.num_workers, drop_last=len(train_ds) > args.batch_size)
    val_loader = (DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                              num_workers=args.num_workers)
                  if val_ds else None)

    model = PadModel(pretrained=not args.no_pretrained).to(device)
    optimizer = torch.optim.SGD(model.parameters(), lr=args.lr,
                                 momentum=0.9, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    os.makedirs(args.checkpoint_dir, exist_ok=True)
    best_val_acc = -1.0

    for epoch in range(args.epochs):
        train_loss, train_acc = run_epoch(
            model, train_loader, optimizer, device,
            (args.attack_loss_weight, args.fft_loss_weight), train=True,
        )
        scheduler.step()

        msg = f"epoch {epoch+1}/{args.epochs} - train_loss={train_loss:.4f} train_acc={train_acc:.4f}"
        if val_loader:
            val_loss, val_acc = run_epoch(
                model, val_loader, optimizer, device,
                (args.attack_loss_weight, args.fft_loss_weight), train=False,
            )
            msg += f" - val_loss={val_loss:.4f} val_acc={val_acc:.4f}"
            if val_acc >= best_val_acc:
                best_val_acc = val_acc
                torch.save(model.state_dict(), os.path.join(args.checkpoint_dir, "best.pt"))
        print(msg)

    torch.save(model.state_dict(), os.path.join(args.checkpoint_dir, "last.pt"))
    if not val_loader:
        # No val split (e.g. tiny smoke test) - still leave a usable checkpoint.
        torch.save(model.state_dict(), os.path.join(args.checkpoint_dir, "best.pt"))
    print(f"Done. Checkpoints written to {args.checkpoint_dir}/")


if __name__ == "__main__":
    main()
