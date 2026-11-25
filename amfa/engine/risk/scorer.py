"""Loads the active risk model and scores one transaction. A model only loads if every file matches
the checksums in its manifest; anything else raises RiskModelError, which the engine treats as
'fail closed' (manual review)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from amfa import config

from .features import FEATURES, HISTORY_DAYS, vector
from .threat import habit_level, level

ACTIVE = "ACTIVE"
CANDIDATE = "CANDIDATE"      # a version that scores every payment silently next to the active one, and decides nothing
MANIFEST = "manifest.json"
VERSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")   # a plain folder name: no path, no "..", at most 64 characters
log = logging.getLogger("amfa.risk")


class RiskModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    score: float          # calibrated fraud probability
    raw: float            # ensemble output the cut-offs apply to
    level: str
    reasons: list[tuple[str, float]]
    version: str
    habit: str = "T0"      # the level the customer's own habit alone asks for (T0 = no opinion)
    # the most likely kind of fraud if this is one (UNAUTHORISED, COACHED, PAYEE_RISK, FIRST_PARTY) and the router's
    # probability for each; None for a model trained without a router. It explains the level; it never changes it.
    family: Optional[str] = None
    family_probs: Optional[dict] = None


def root() -> Path:
    return config.get().models_dir / "risk"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RiskModel:
    def __init__(self, path: Path, allow_banded: bool = False):
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
        view = self.card.get("amountView", "exact")
        if view != "exact" and not allow_banded:
            # trained for the option A/B comparison on amounts known only by band; live inputs are exact amounts, which such a
            # model would misread. Only scripts/compare_amount_storage.py opens one, and it asks for that explicitly
            raise RiskModelError(f"model was trained on amounts known only by band ({view}); it is for the comparison only")
        # the model scores with the list it was trained on: a model from before the phone and network signals still loads
        self.features = list(self.card["features"])
        unknown = [f for f in self.features if f not in FEATURES]
        if unknown:
            raise RiskModelError(f"model was trained on features this build does not compute: {unknown[:5]}")
        if self.card.get("historyDays") != HISTORY_DAYS:
            raise RiskModelError(f"model was trained on a {self.card.get('historyDays')}-day history window, "
                                 f"this build uses {HISTORY_DAYS}")
        self.version = self.card["version"]
        self.cutoffs = self.card["cutoffs"]
        self.habit_cutoffs = self.card.get("habit", {}).get("cutoffs")
        cal = json.loads((path / "calibration.json").read_text())
        self.iso_x, self.iso_y = np.array(cal["x"]), np.array(cal["y"])
        self.boosters = [lgb.Booster(model_file=str(path / n)) for n in self.card["boosters"]]
        # specialists (merged model): for a payment where the bank sent phone/network signals, or one where it did not, the
        # matching specialists score half and the general boosters half. A model without them is the general boosters alone.
        self.experts = {k: [lgb.Booster(model_file=str(path / n)) for n in v] for k, v in self.card.get("experts", {}).items() if v}
        self._cols = [FEATURES.index(f) for f in self.features]
        self._sig = self.features.index("signals_present") if "signals_present" in self.features else None
        router = self.card.get("router")
        self.router = lgb.Booster(model_file=str(path / router["file"])) if router else None
        self.families = list(router["classes"]) if router else []

    def _model_columns(self, X) -> np.ndarray:
        """Rows in this model's input order. Accepts rows built in this build's FEATURES order (vector(f)) or already in the
        model's own order (vector(f, model.features)), so callers written for either keep working."""
        X = np.asarray(X, dtype=float)
        if X.shape[1] == len(self.features):
            return X
        if X.shape[1] == len(FEATURES):
            return X[:, self._cols]
        raise RiskModelError(f"expected {len(self.features)} or {len(FEATURES)} inputs, got {X.shape[1]}")

    def _groups(self, Xm: np.ndarray) -> list[tuple[np.ndarray, list]]:
        """(rows, specialist boosters) per kind of payment; empty boosters where the model has no specialist for it."""
        if not self.experts or self._sig is None:
            return [(np.ones(len(Xm), dtype=bool), [])]
        has = Xm[:, self._sig] == 1
        return [(has, self.experts.get("signals", [])), (~has, self.experts.get("no_signals", []))]

    def raw(self, X: np.ndarray) -> np.ndarray:
        Xm = self._model_columns(X)
        out = np.mean([b.predict(Xm) for b in self.boosters], axis=0)
        for rows, experts in self._groups(Xm):
            if experts and rows.any():
                out[rows] = (out[rows] + np.mean([b.predict(Xm[rows]) for b in experts], axis=0)) / 2
        return out

    def _contrib(self, Xm: np.ndarray) -> np.ndarray:
        """Each input's contribution to the raw score of one row, weighted as raw() weights the boosters."""
        c = np.mean([b.predict(Xm, pred_contrib=True)[0][:-1] for b in self.boosters], axis=0)
        for rows, experts in self._groups(Xm):
            if experts and rows[0]:
                c = (c + np.mean([b.predict(Xm, pred_contrib=True)[0][:-1] for b in experts], axis=0)) / 2
        return c

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        return np.interp(raw, self.iso_x, self.iso_y)

    def score(self, features: dict) -> Result:
        X = np.array([vector(features, self.features)])
        raw = float(self.raw(X)[0])
        contrib = self._contrib(X)
        top = sorted(((self.features[i], float(c)) for i, c in enumerate(contrib) if c > 0),
                     key=lambda kv: -kv[1])[:3]
        family = probs = None
        if self.router is not None:
            p = self.router.predict(X)[0]
            probs = {f: round(float(v), 4) for f, v in zip(self.families, p)}
            family = max(probs, key=probs.get)
        return Result(score=round(float(self.calibrate(np.array([raw]))[0]), 4), raw=raw,
                      level=level(raw, self.cutoffs), reasons=[(k, round(v, 3)) for k, v in top],
                      version=self.version, habit=habit_level(features.get("habit_surprise"), self.habit_cutoffs),
                      family=family, family_probs=probs)


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


# ---- the candidate: a new version graded per amount band before it may decide (core/band_gate.py) -----------------------------

def _pointer(name: str, store: Path | None = None) -> str | None:
    try:
        v = ((store or root()) / name).read_text().strip()
    except OSError:
        return None
    return v if VERSION_NAME.match(v) else None


def candidate_version(store: Path | None = None) -> str | None:
    """The candidate's version name, if one is installed and is not the active version."""
    v = _pointer(CANDIDATE, store)
    return v if v and v != _pointer(ACTIVE, store) else None


def candidate() -> RiskModel | None:
    """The candidate model, or None: none installed, an invalid name, the same as the active version, or one that fails to load
    or its integrity check. Never raises: a candidate only ever watches, so its failure must not reach a payment."""
    v = candidate_version()
    if v is None:
        return None
    try:
        return _load(str(root() / v))
    except Exception as e:
        log.error("candidate risk model not loaded", extra={"version": v, "error": type(e).__name__})
        return None


def _write_pointer(store: Path, name: str, version: str) -> None:
    tmp = store / f".{name}.tmp"
    tmp.write_text(version)
    os.replace(tmp, store / name)          # all or nothing: a reader sees the old pointer or the new one, never half of it


def set_candidate(version: str, store: Path | None = None) -> RiskModel:
    """Install [version] as the candidate. It must be a plain folder name in the model store, not the active version, and load with
    every file matching its manifest. Replaces any earlier candidate."""
    store = store or root()
    if not isinstance(version, str) or not VERSION_NAME.match(version):
        raise RiskModelError(f"not a model version name: {version!r}")
    if version == _pointer(ACTIVE, store):
        raise RiskModelError(f"{version} is the active version already")
    folder = store / version
    if not folder.is_dir():
        raise RiskModelError(f"no model version {version} in the model store")
    model = _load(str(folder))          # raises RiskModelError on a failed integrity check or an incompatible model
    _write_pointer(store, CANDIDATE, version)
    return model


def clear_candidate(store: Path | None = None) -> bool:
    """Remove the candidate. Returns whether there was one."""
    try:
        ((store or root()) / CANDIDATE).unlink()
        return True
    except FileNotFoundError:
        return False


def promote_candidate(version: str, store: Path | None = None) -> RiskModel:
    """Make the candidate [version] the active model, and remove the candidate. Refused unless [version] is the installed candidate
    and still loads with every file matching its manifest. The band states must already have been moved (core/band_gate.promote):
    until this pointer changes they name a version that is not deciding, and the band switch treats that as the rules."""
    store = store or root()
    if candidate_version(store) != version:
        raise RiskModelError(f"{version} is not the installed candidate")
    model = _load(str(store / version))
    _write_pointer(store, ACTIVE, version)
    clear_candidate(store)
    return model
