"""Loads the active risk model and scores one transaction. A model only loads if every file matches
the checksums in its manifest; anything else raises RiskModelError, which the engine treats as
'fail closed' (manual review)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from amfa import config

from .features import FEATURES, HISTORY_DAYS, vector
from .threat import level

ACTIVE = "ACTIVE"
MANIFEST = "manifest.json"


class RiskModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    score: float          # calibrated fraud probability
    raw: float            # ensemble output the cut-offs apply to
    level: str
    reasons: list[tuple[str, float]]
    version: str


def root() -> Path:
    return config.get().models_dir / "risk"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RiskModel:
    def __init__(self, path: Path):
        import lightgbm as lgb
        try:
            manifest = json.loads((path / MANIFEST).read_text())
        except (OSError, ValueError) as e:
            raise RiskModelError(f"no readable manifest in {path}") from e
        for name, digest in manifest["files"].items():
            f = path / name
            if not f.is_file() or sha256(f) != digest:
                raise RiskModelError(f"{name} failed its integrity check")
        self.card = json.loads((path / "card.json").read_text())
        if self.card["features"] != FEATURES:
            raise RiskModelError("model was trained on a different feature list")
        if self.card.get("historyDays") != HISTORY_DAYS:
            raise RiskModelError(f"model was trained on a {self.card.get('historyDays')}-day history window, "
                                 f"this build uses {HISTORY_DAYS}")
        self.version = self.card["version"]
        self.cutoffs = self.card["cutoffs"]
        cal = json.loads((path / "calibration.json").read_text())
        self.iso_x, self.iso_y = np.array(cal["x"]), np.array(cal["y"])
        self.boosters = [lgb.Booster(model_file=str(path / n)) for n in self.card["boosters"]]

    def raw(self, X: np.ndarray) -> np.ndarray:
        return np.mean([b.predict(X) for b in self.boosters], axis=0)

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        return np.interp(raw, self.iso_x, self.iso_y)

    def score(self, features: dict) -> Result:
        X = np.array([vector(features)])
        raw = float(self.raw(X)[0])
        contrib = np.mean([b.predict(X, pred_contrib=True)[0][:-1] for b in self.boosters], axis=0)
        top = sorted(((FEATURES[i], float(c)) for i, c in enumerate(contrib) if c > 0),
                     key=lambda kv: -kv[1])[:3]
        return Result(score=round(float(self.calibrate(np.array([raw]))[0]), 4), raw=raw,
                      level=level(raw, self.cutoffs), reasons=[(k, round(v, 3)) for k, v in top],
                      version=self.version)


@lru_cache(maxsize=4)
def _load(path: str) -> RiskModel:
    return RiskModel(Path(path))


def active() -> RiskModel:
    pointer = root() / ACTIVE
    try:
        version = pointer.read_text().strip()
    except OSError as e:
        raise RiskModelError("no active risk model; train one with python -m amfa.engine.risk.train") from e
    return _load(str(root() / version))
