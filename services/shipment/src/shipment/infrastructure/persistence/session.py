"""Async SQLAlchemy session factory for the Shipment-owned database."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str) -> Engine:
    """Sync engine for Alembic migrations, accepted-fact UoW, and health probes."""
    return create_engine(database_url, pool_pre_ping=True, future=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def build_async_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, future=True)


def build_async_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ping_database(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def assert_migrations_applied(engine: Engine) -> None:
    """Fail closed when Alembic metadata is missing or empty."""
    try:
        with engine.connect() as connection:
            table = connection.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar()
            if table is None:
                msg = "database migrations are unavailable"
                raise RuntimeError(msg)
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
            if not revision:
                msg = "database migrations are unavailable"
                raise RuntimeError(msg)
    except RuntimeError:
        raise
    except Exception as exc:
        msg = "database migrations are unavailable"
        raise RuntimeError(msg) from exc
