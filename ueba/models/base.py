"""Model interface. Written from the OpenUBA pattern; no code copied."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import numpy as np, pandas as pd


@dataclass
class ModelContext:
    data: pd.DataFrame
    config: dict = field(default_factory=dict)
    artifact_dir: Path = Path("artifacts")
    labels: Any = None


@dataclass
class TrainResult:
    model_name: str
    model_version: str
    n_samples: int
    features: list


class Model(ABC):
    name = "unnamed"; version = "0.0.0"; expected_features: list = []

    @abstractmethod
    def train(self, ctx) -> TrainResult: ...

    @abstractmethod
    def infer(self, ctx) -> np.ndarray: ...

    def validate_features(self, ctx) -> None:
        if not self.expected_features:
            return
        missing = [f for f in self.expected_features if f not in ctx.data.columns]
        if missing:
            raise ValueError(f"{self.name} v{self.version} expects features "
                             f"not present in the input: {missing}")
