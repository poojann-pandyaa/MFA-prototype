"""The band switch's own tables. They hold only anonymous rows and band states, never a customer, amount, payee or payment id.

They have their own table registry (BandBase), so they can live in the band service's own database (amfa/bands/service.py, with
AMFA_BANDS_URL set) or, by default, in the core's database next to everything else. The core imports these classes under their old
names (db.LearningRow, db.BandGateState, db.BandCandidate).
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> float:
    return time.time()


class BandBase(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class LearningRow(BandBase):
    """One settled payment for the band switch, by amount band and small categories only (amfa/core/learning_store.py). It holds no
    customer, account, amount, payee, device, network, place, address or exact time, and its id is random so the order rows were
    written in says nothing. `link` (a keyed hash of the transaction id) exists only so a label arriving later can find its row;
    it and `link_expires_at` are set to null after 90 days, after which nothing can tie the row to a person. The allowed columns are
    pinned by a test: adding one means changing that test on purpose."""
    __tablename__ = "learning_row"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    band: Mapped[int] = mapped_column(Integer, index=True)              # 0..3, engine/risk/collection_bands.py
    band_vs_usual: Mapped[str] = mapped_column(String(12))              # NEW, LOWER, SAME, ONE_HIGHER, TWO_HIGHER
    payee_kind: Mapped[str] = mapped_column(String(8))                  # KNOWN, NEW, FLAGGED
    device_kind: Mapped[str] = mapped_column(String(8))                 # KNOWN, NEW, HELPER
    hour_bucket: Mapped[str] = mapped_column(String(10))                # NIGHT, MORNING, AFTERNOON, EVENING
    hard_sign: Mapped[bool] = mapped_column(Boolean)
    rules_level: Mapped[str] = mapped_column(String(2))                 # T0..T4, engine/band_switch.level_of
    model_level: Mapped[str] = mapped_column(String(2))
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)   # the risk model that gave model_level
    candidate_level: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)   # what a candidate model, watching, would have asked
    candidate_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(12))                       # COLLECTING, ELIGIBLE, MODEL when it was decided
    outcome: Mapped[str] = mapped_column(String(10))                    # COMPLETED, DECLINED, CANCELLED, EXPIRED
    label: Mapped[str] = mapped_column(String(8), default="UNKNOWN")    # UNKNOWN, GENUINE, FRAUD
    label_source: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    week_start: Mapped[str] = mapped_column(String(10), index=True)     # the Monday of its week, YYYY-MM-DD
    link: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    link_expires_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)   # a whole day, never a time


class BandGateState(BandBase):
    """Where one amount band of one bank stands on its way from the rules to the model (amfa/bands/engine.py). `figures` holds only
    counts and rates from the last grading. Every change of `state` is also in the core's audit log."""
    __tablename__ = "band_gate"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    band: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(12), default="COLLECTING")     # COLLECTING, ELIGIBLE, MODEL
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # the risk model this state was graded or approved on
    streak: Mapped[int] = mapped_column(Integer, default=0)                   # weekly gradings passed in a row
    counted_week: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)   # the week whose passing grading counted last
    evaluated_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    changed_at: Mapped[float] = mapped_column(Float, default=now)
    figures: Mapped[dict] = mapped_column(JSON, default=dict)
    losing: Mapped[int] = mapped_column(Integer, default=0)                   # on MODEL: weekly gradings lost in a row
    losing_week: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)


class BandCandidate(BandBase):
    """A candidate risk model's progress in one amount band of one bank, while it watches (amfa/bands/engine.py). Separate from
    band_gate, so grading the candidate can never move the band itself. Replaced when another candidate is installed."""
    __tablename__ = "band_candidate"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    band: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(12), default="COLLECTING")     # COLLECTING, ELIGIBLE, APPROVED
    streak: Mapped[int] = mapped_column(Integer, default=0)
    counted_week: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    evaluated_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    changed_at: Mapped[float] = mapped_column(Float, default=now)
    figures: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_by: Mapped[Optional[str]] = mapped_column(String(90), nullable=True)       # both operators
    approved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


# columns added to these tables after they were first created: (schema version, table, column, SQL type)
UPGRADES = [
    (6, "learning_row", "model_version", "VARCHAR(64)"),
    (6, "band_gate", "model_version", "VARCHAR(64)"),
    (6, "learning_row", "candidate_level", "VARCHAR(2)"),
    (6, "learning_row", "candidate_version", "VARCHAR(64)"),
]
