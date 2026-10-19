from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import logging
from sqlalchemy import DateTime, String, Text, inspect, text
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
    fallbacks_json: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class UserAccount(Base):
    """Authenticated user profile and AES-256-GCM encrypted OAuth/PAT credentials stored in Supabase PostgreSQL."""

    __tablename__ = "users"

    login: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    clerk_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    github_username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    encrypted_github_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    auth_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="github")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


import os
import sys
from sqlalchemy.pool import NullPool, StaticPool

settings = get_settings()

is_test = (
    os.environ.get("TESTING") == "1"
    or "pytest" in sys.modules
    or bool(os.environ.get("PYTEST_CURRENT_TEST"))
)

connect_args: dict[str, object] = {}
engine_kwargs: dict[str, object] = {
    "echo": False,
    "future": True,
}

if is_test:
    db_url = "sqlite+aiosqlite:///:memory:"
    connect_args["check_same_thread"] = False
    engine_kwargs["poolclass"] = StaticPool
else:
    db_url = settings.DATABASE_URL.strip()
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if not db_url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must be a valid PostgreSQL/Supabase connection URL (postgresql://...)")

    connect_args["prepared_statement_cache_size"] = 0
    engine_kwargs["poolclass"] = NullPool

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


def _migrate_vault_columns(conn) -> None:
    """Safely migrate missing columns in PostgreSQL/Supabase."""
    try:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        if "vault_configs" in tables:
            columns = [c["name"] for c in inspector.get_columns("vault_configs")]
            if "fallbacks_json" not in columns:
                conn.execute(text("ALTER TABLE vault_configs ADD COLUMN fallbacks_json TEXT DEFAULT '[]'"))
    except Exception as exc:
        logger.warning("Vault schema column check notice: %s", exc)


async def init_db() -> None:
    """Initialize PostgreSQL/Supabase database schema asynchronously."""
    global _tables_created
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_vault_columns)
    _tables_created = True
    logger.info("PostgreSQL/Supabase tables (vault_configs, users) verified and initialized.")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async PostgreSQL database session."""
    global _tables_created
    if not _tables_created:
        await init_db()
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
