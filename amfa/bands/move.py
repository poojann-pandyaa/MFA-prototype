"""Moving band data out of the core's database into the band service's own, once, when a deployment turns the service on.

    python -m amfa.cli bands-move --dry-run       counts only, changes nothing
    python -m amfa.cli bands-move                 copy, check, then delete from the core

Run it with the core stopped (or before setting AMFA_BANDS_URL), from a place that reaches both databases: AMFA_DATABASE_URL
names the core's, AMFA_BANDS_DB_URL the service's. Rows are copied by their keys, so running it twice copies nothing twice. Only
when every row is found in the service's database are they deleted from the core's; if anything is missing the core keeps all
of them and the command says what. The links stay keyed hashes: their key never leaves the core.
"""
from __future__ import annotations

from sqlalchemy import delete, func, select

from amfa import config
from amfa.bands import db as bdb
from amfa.bands.models import BandCandidate, BandGateState, LearningRow

TABLES = (BandGateState, BandCandidate, LearningRow)
BATCH = 1000


class MoveError(Exception):
    pass


def _keys(obj) -> tuple:
    return tuple(getattr(obj, c.key) for c in obj.__mapper__.primary_key)


def _copy(model, src, dst) -> tuple[int, int]:
    """Copy every row of [model] that the destination lacks. Returns (rows in the core, rows copied)."""
    cols = [c.key for c in model.__table__.columns]
    total = copied = 0
    offset = 0
    pk = list(model.__mapper__.primary_key)
    while True:
        batch = src.execute(select(model).order_by(*pk).offset(offset).limit(BATCH)).scalars().all()
        if not batch:
            break
        offset += len(batch)
        total += len(batch)
        for r in batch:
            if dst.get(model, _keys(r)) is None:
                dst.add(model(**{c: getattr(r, c) for c in cols}))
                copied += 1
        dst.flush()
    return total, copied


def _missing(model, src, dst) -> int:
    missing, offset = 0, 0
    pk = list(model.__mapper__.primary_key)
    while True:
        batch = src.execute(select(model).order_by(*pk).offset(offset).limit(BATCH)).scalars().all()
        if not batch:
            return missing
        offset += len(batch)
        missing += sum(1 for r in batch if dst.get(model, _keys(r)) is None)


def move(dry_run: bool = False) -> dict:
    from amfa.core import db
    if bdb.url() == config.get().database_url:
        raise MoveError("AMFA_BANDS_DB_URL names the core's own database; set it to the band service's database")
    bdb.migrate()
    out: dict = {"dryRun": dry_run, "tables": {}}
    with db.session() as src:
        if dry_run:
            for m in TABLES:
                out["tables"][m.__tablename__] = {"inCore": src.execute(select(func.count()).select_from(m)).scalar_one()}
            return out
        with bdb.session() as dst:
            for m in TABLES:
                total, copied = _copy(m, src, dst)
                out["tables"][m.__tablename__] = {"inCore": total, "copied": copied}
        # the service's database has committed; check every row is there before the core lets go of any
        with bdb.session() as dst:
            missing = {m.__tablename__: _missing(m, src, dst) for m in TABLES}
        if any(missing.values()):
            raise MoveError(f"rows missing from the band service's database, nothing deleted from the core: {missing}")
        for m in TABLES:
            src.execute(delete(m))
    out["deletedFromCore"] = True
    return out
