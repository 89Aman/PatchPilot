from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import logging
import os
import sys
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool, StaticPool
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


class Workspace(Base):
    """Multi-tenant workspace bound to an immutable GitHub account & App installation."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    github_account_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    github_login: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, default="User")
    github_installation_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    installation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    # Hybrid key mode: 'managed' (hosted LLM pool) or 'byok' (customer encrypted vault)
    key_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="byok")

    # Subscription state (no free tier, no free trial when BILLING_ENABLED=true)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="unsubscribed")
    subscription_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Polar.sh Merchant of Record references
    polar_customer_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    polar_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    billing_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Optional customer-configured hard cap on billable active committers per cycle
    seat_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class WorkspaceMember(Base):
    """Verified membership mapping an authenticated Clerk user to a GitHub Workspace."""

    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clerk_user_id: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    github_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    github_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class WorkspaceActiveCommitter(Base):
    """Unique active human PR author per workspace billing cycle + idempotent Polar meter outbox."""

    __tablename__ = "workspace_active_committers"

    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    billing_period: Mapped[str] = mapped_column(String(32), primary_key=True)
    github_author_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    github_author_login: Mapped[str] = mapped_column(String(255), nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    managed_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meter_event_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    meter_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ReviewUsageEvent(Base):
    """Audit log and token telemetry for completed PR reviews and interactive commands."""

    __tablename__ = "review_usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    repo_full_name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    github_author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    github_author_login: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="pr_review")
    key_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="byok")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )


class BillingWebhookEvent(Base):
    """Idempotency ledger for processed Polar and Clerk webhook deliveries."""

    __tablename__ = "billing_webhook_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class VaultConfig(Base):
    """Encrypted BYOK configuration keyed by GitHub owner and optionally linked to a Workspace."""

    __tablename__ = "vault_configs"

    owner: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
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
    """Authenticated user profile and AES-256-GCM encrypted OAuth credentials stored in Supabase PostgreSQL."""

    __tablename__ = "users"

    login: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    clerk_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    github_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
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
    """Safely migrate missing columns in PostgreSQL/Supabase and SQLite."""
    try:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        if "vault_configs" in tables:
            columns = [c["name"] for c in inspector.get_columns("vault_configs")]
            if "fallbacks_json" not in columns:
                conn.execute(text("ALTER TABLE vault_configs ADD COLUMN fallbacks_json TEXT DEFAULT '[]'"))
            if "workspace_id" not in columns:
                conn.execute(text("ALTER TABLE vault_configs ADD COLUMN workspace_id VARCHAR(64)"))
        if "users" in tables:
            user_cols = [c["name"] for c in inspector.get_columns("users")]
            if "github_user_id" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN github_user_id BIGINT"))
    except Exception as exc:
        logger.warning("Schema column check notice: %s", exc)


async def init_db() -> None:
    """Initialize PostgreSQL/Supabase database schema asynchronously."""
    global _tables_created
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_vault_columns)
    _tables_created = True
    logger.info("Database tables verified and initialized.")


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
