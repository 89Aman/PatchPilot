import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import logging
import os
import sys
from sqlalchemy import DateTime, String, Text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_POLICY = """# PatchPilot PR Review Guidelines
- Identify critical bugs, security vulnerabilities, memory/resource leaks, and edge-case exceptions.
- Highlight performance bottlenecks or unnecessary redundant operations.
- Flag breaking changes or deviations from repository architectural idioms.
- Keep feedback objective, constructive, and directly actionable.
"""


class Base(DeclarativeBase):
    """Base declarative model."""
    pass


class VaultConfig(Base):
    """Encrypted BYOK configuration keyed strictly by GitHub repository owner/organization."""

    __tablename__ = "vault_configs"

    owner: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="gemini")
    model: Mapped[str] = mapped_column(String(255), nullable=False, default="gemini-2.5-pro")
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    review_policy: Mapped[str] = mapped_column(Text, nullable=False, default=DEFAULT_POLICY)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


from sqlalchemy.pool import StaticPool

settings = get_settings()

db_url = settings.DATABASE_URL
connect_args: dict[str, object] = {}
engine_kwargs: dict[str, object] = {
    "echo": False,
    "future": True,
}

# Convert standard postgresql URL to asyncpg driver
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

if "asyncpg" in db_url:
    # Disable prepared statement caching for Supabase PgBouncer transaction mode compatibility
    connect_args["prepared_statement_cache_size"] = 0

# Check testing override
is_test = os.environ.get("TESTING") == "1" or "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))
if is_test:
    db_url = "sqlite+aiosqlite:///:memory:"
    connect_args = {"check_same_thread": False}
    engine_kwargs["poolclass"] = StaticPool

engine = create_async_engine(
    db_url,
    connect_args=connect_args,
    **engine_kwargs,
)

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Alias for compatibility
SessionLocal = async_session_maker

_tables_created = False


async def init_db() -> None:
    """Initialize database schema asynchronously with graceful offline fallback."""
    global engine, async_session_maker, SessionLocal, _tables_created
    try:
        async def _run_init():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        await asyncio.wait_for(_run_init(), timeout=4.0)
        _tables_created = True
        logger.info("Database tables verified and initialized successfully.")
    except Exception as exc:
        logger.warning("Remote database unavailable (%s). Initializing local SQLite fallback.", exc)
        engine = create_async_engine(
            "sqlite+aiosqlite:///./vault.db",
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
        async_session_maker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        SessionLocal = async_session_maker
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _tables_created = True


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    global _tables_created
    if not _tables_created:
        await init_db()
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
