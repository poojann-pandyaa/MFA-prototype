"""
Shared fixtures.

Several service tests need trained artifacts -- the transactional model
and the per-dimension calibrators. Without them the service degrades
gracefully and reports INSUFFICIENT_DATA, which is correct behaviour but
makes those tests silently vacuous rather than failing loudly.

This trains a small model once per session if artifacts are missing.
"""
from pathlib import Path

import pytest

ARTIFACTS = Path("artifacts")


@pytest.fixture(scope="session", autouse=True)
def artifacts():
    needed = [ARTIFACTS / "isoforest.joblib",
              ARTIFACTS / "calibrator.joblib",
              ARTIFACTS / "calibration.json"]
    if all(f.exists() for f in needed):
        return

    from scripts.train import run
    print("\n[conftest] artifacts missing -- training a small model for tests")
    run(n_users=60, n_days=90, n_behaviour_users=25, seed=42)
