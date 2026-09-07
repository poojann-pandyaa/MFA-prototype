"""
Logistic regression baseline.

Adapted from prajwalnayaka/UEBA (MIT) -- see NOTICE.

Supervised, so it needs labels that production does not have at decision
time. Included to measure the cost of the unsupervised approach, and
because coefficients are directly interpretable -- which matters in a
regulated setting where decisions must be explainable.
"""

import numpy as np
from joblib import dump, load
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from models.base import Model, ModelContext, TrainResult


class LogisticRegressionModel(Model):

    ARTIFACT = "logreg.joblib"

    def train(self, ctx: ModelContext) -> TrainResult:
        if ctx.labels is None:
            raise ValueError("logreg is supervised and requires ctx.labels")

        hp = ctx.config.get("hyperparameters", {})
        X = ctx.data[self.expected_features]

        scaler = StandardScaler().fit(X)
        model = LogisticRegression(
            class_weight=hp.get("class_weight", "balanced"),
            max_iter=hp.get("max_iter", 1000),
            random_state=hp.get("random_state", 42),
        )
        model.fit(scaler.transform(X), ctx.labels)

        ctx.artifact_dir.mkdir(parents=True, exist_ok=True)
        dump({"model": model, "scaler": scaler},
             ctx.artifact_dir / self.ARTIFACT)

        return TrainResult(self.name, self.version, len(X),
                           list(self.expected_features))

    def infer(self, ctx: ModelContext) -> np.ndarray:
        bundle = load(ctx.artifact_dir / self.ARTIFACT)
        X = bundle["scaler"].transform(ctx.data[self.expected_features])
        return bundle["model"].predict_proba(X)[:, 1]

    def coefficients(self, artifact_dir) -> dict:
        """Feature weights, for the interpretability discussion."""
        bundle = load(artifact_dir / self.ARTIFACT)
        return dict(zip(self.expected_features,
                        bundle["model"].coef_[0].round(4)))
