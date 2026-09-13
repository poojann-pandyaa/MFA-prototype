"""
Isolation Forest. Adapted from prajwalnayaka/UEBA (MIT) -- see NOTICE.
  Liu, Ting & Zhou (2008). "Isolation Forest." Proc. 8th IEEE ICDM.
"""
import numpy as np
from joblib import dump, load
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from models.base import Model, ModelContext, TrainResult


class IsolationForestModel(Model):
    ARTIFACT = "isoforest.joblib"

    def train(self, ctx):
        hp = ctx.config.get("hyperparameters", {})
        X = ctx.data[self.expected_features]
        if ctx.labels is not None:
            X = X[np.asarray(ctx.labels) == 0]
        scaler = StandardScaler().fit(X)
        m = IsolationForest(
            n_estimators=hp.get("n_estimators",200),
            max_samples=min(hp.get("max_samples",256), len(X)),
            contamination=hp.get("contamination",0.01),
            random_state=hp.get("random_state",42), n_jobs=-1)
        m.fit(scaler.transform(X))
        ctx.artifact_dir.mkdir(parents=True, exist_ok=True)
        dump({"model": m, "scaler": scaler}, ctx.artifact_dir/self.ARTIFACT)
        return TrainResult(self.name, self.version, len(X), list(self.expected_features))

    def infer(self, ctx):
        b = load(ctx.artifact_dir/self.ARTIFACT)
        X = b["scaler"].transform(ctx.data[self.expected_features])
        # score_samples: lower = more anomalous. Negate to match the contract.
        return -b["model"].score_samples(X)
