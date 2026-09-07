"""
Isolation Forest anomaly detector.

Adapted from prajwalnayaka/UEBA (MIT) -- see NOTICE.

  Liu, F.T., Ting, K.M., Zhou, Z-H. (2008). "Isolation Forest."
  Proc. 8th IEEE ICDM, pp. 413-422. DOI: 10.1109/ICDM.2008.17
"""

import numpy as np
from joblib import dump, load
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from models.base import Model, ModelContext, TrainResult


class IsolationForestModel(Model):

    ARTIFACT = "isoforest.joblib"

    def train(self, ctx: ModelContext) -> TrainResult:
        hp = ctx.config.get("hyperparameters", {})
        X = ctx.data[self.expected_features]

        # Train on normal behaviour only. Isolation Forest is unsupervised,
        # but excluding known fraud sharpens the notion of "normal" it
        # learns. In production this is the same as training on historical
        # data with confirmed fraud removed.
        if ctx.labels is not None:
            X = X[ctx.labels == 0]

        scaler = StandardScaler().fit(X)
        model = IsolationForest(
            n_estimators=hp.get("n_estimators", 200),
            max_samples=min(hp.get("max_samples", 256), len(X)),
            contamination=hp.get("contamination", 0.01),
            random_state=hp.get("random_state", 42),
            n_jobs=-1,
        )
        model.fit(scaler.transform(X))

        ctx.artifact_dir.mkdir(parents=True, exist_ok=True)
        dump({"model": model, "scaler": scaler},
             ctx.artifact_dir / self.ARTIFACT)

        return TrainResult(self.name, self.version, len(X),
                           list(self.expected_features))

    def infer(self, ctx: ModelContext) -> np.ndarray:
        bundle = load(ctx.artifact_dir / self.ARTIFACT)
        X = bundle["scaler"].transform(ctx.data[self.expected_features])
        # score_samples: lower means more anomalous. Negated so that higher
        # means more anomalous, matching the contract in base.Model.
        return -bundle["model"].score_samples(X)
