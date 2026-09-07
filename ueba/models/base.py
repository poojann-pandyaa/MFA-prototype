"""
Model interface.

Written from the architectural pattern used by GACWR/OpenUBA (Apache 2.0).
No code copied -- see NOTICE.

Models receive data through a context object. They never open a database
connection or know where their input came from. That separation lets the
service and the model be built in parallel, and lets models be tested with
a plain DataFrame.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ModelContext:
    """Everything a model is allowed to see."""

    data: pd.DataFrame
    config: dict[str, Any] = field(default_factory=dict)
    artifact_dir: Path = Path("artifacts")
    labels: np.ndarray | None = None


@dataclass
class TrainResult:
    model_name: str
    model_version: str
    n_samples: int
    features: list[str]


class Model(ABC):
    """Base contract for every scoring model."""

    name: str = "unnamed"
    version: str = "0.0.0"
    expected_features: list[str] = []

    @abstractmethod
    def train(self, ctx: ModelContext) -> TrainResult:
        """Fit and persist to ctx.artifact_dir. Does not return the model."""

    @abstractmethod
    def infer(self, ctx: ModelContext) -> np.ndarray:
        """
        One continuous score per row. Higher means more anomalous.

        NOT a probability -- calibration is a separate step.
        """

    def validate_features(self, ctx: ModelContext) -> None:
        """
        Guard against scoring against the wrong feature set.

        When the feature engine changes, a stale model fails loudly here
        instead of silently reading values from shifted columns.
        """
        if not self.expected_features:
            return
        missing = [f for f in self.expected_features
                   if f not in ctx.data.columns]
        if missing:
            raise ValueError(
                f"{self.name} v{self.version} expects features not present "
                f"in the input: {missing}"
            )
