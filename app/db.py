"""
Database connection and session handling.

One engine, one session factory, one FastAPI dependency. That's all.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

# SQLite needs one extra flag because FastAPI may touch the connection from
# different threads. Postgres does not need it, so we only add it for SQLite.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    # Re-check connections before using them. Neon closes idle connections,
    # and without this the first request after a quiet period would fail.
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Every model class inherits from this.
Base = declarative_base()


def get_db():
    """FastAPI dependency: opens a session for one request, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Creates any missing tables. Called once on app startup.

    We use this instead of migrations (Alembic) on purpose: the schema is new
    and this project has a 2-day build window. Documented as a known limitation.
    """
    # Importing models here (not at the top) avoids a circular import:
    # models.py imports Base from this file.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
