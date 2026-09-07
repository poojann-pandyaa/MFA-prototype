"""
Reliability diagram and PR curve.

The reliability diagram is the evidence that the risk score means what it
claims: predicted probability against observed fraud rate. A well
calibrated model sits near the diagonal.

    python -m scripts.plot
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from sklearn.metrics import precision_recall_curve

from features.engine import compute_batch
from models.base import ModelContext
from models.calibrate import Calibrator, reliability_curve
from models.loader import load_model
from scripts.train import time_split

ARTIFACTS = Path("artifacts")


def main() -> None:
    raw = pd.read_csv("data/events.csv", parse_dates=["occurred_at"])
    feat = compute_batch(raw)
    _, test = time_split(feat[feat.has_history])

    model = load_model("models/isoforest")
    scores = model.infer(ModelContext(data=test, artifact_dir=ARTIFACTS))
    probs = Calibrator.load(ARTIFACTS).transform(scores)
    labels = test.is_fraud.values

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))

    centres, observed, counts = reliability_curve(probs, labels)
    ax1.plot([0, 1], [0, 1], "--", color="grey", label="perfect calibration")
    ax1.plot(centres, observed, "o-", label="our model")
    ax1.set_xlabel("predicted probability")
    ax1.set_ylabel("observed fraud rate")
    ax1.set_title("Reliability diagram")
    ax1.legend()
    ax1.grid(alpha=0.3)

    precision, recall, _ = precision_recall_curve(labels, probs)
    ax2.plot(recall, precision)
    ax2.axhline(labels.mean(), ls="--", color="grey",
                label=f"baseline ({labels.mean():.3%})")
    ax2.set_xlabel("recall")
    ax2.set_ylabel("precision")
    ax2.set_title("Precision-Recall curve")
    ax2.legend()
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    out = ARTIFACTS / "evaluation.png"
    plt.savefig(out, dpi=140)
    print(f"written {out}")


if __name__ == "__main__":
    main()
