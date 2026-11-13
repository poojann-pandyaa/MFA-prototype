from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app import config

engine = create_engine(config.DB_URL, connect_args={"check_same_thread": False} if config.DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def add_missing_columns():
    """create_all makes new tables but never alters old ones; add the few columns this version introduced to an existing database."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    wanted = {"accounts": {"number": "VARCHAR", "ifsc": "VARCHAR", "kind": "VARCHAR DEFAULT 'Savings'", "opened_at": "DATETIME"}}
    with engine.begin() as conn:
        for table, cols in wanted.items():
            have = {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else None
            for name, ddl in (cols.items() if have is not None else ()):
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
