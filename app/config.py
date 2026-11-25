from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    MASTER_ENCRYPTION_KEY: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    ADMIN_API_TOKEN: str = "super-secret-admin-token-change-me"

    # Public deployment URLs & CORS (never hardcode personal/tenant domains)
    APP_BASE_URL: str = ""
    CORS_ALLOWED_ORIGINS: str = ""
    CORS_ALLOW_ORIGIN_REGEX: str = ""
    GITHUB_APP_SLUG: str = ""

    GITHUB_APP_ID: str = "123456"
    GITHUB_WEBHOOK_SECRET: str = "your_github_webhook_secret_here"
    GITHUB_PRIVATE_KEY: str = ""

    # Optional GitHub OAuth App credentials
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    SESSION_SECRET: str = ""
    ALLOW_MANUAL_PAT: bool = False

    # Clerk Authentication (clerk.com)
    CLERK_PUBLISHABLE_KEY: str = ""
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: str = ""
    CLERK_SECRET_KEY: str = ""
    CLERK_JWKS_URL: str = ""
    CLERK_FRONTEND_API: str = ""
    CLERK_ACCOUNTS_URL: str = ""
    CLERK_WEBHOOK_SIGNING_SECRET: str = ""

    # Workspace Membership Cache TTL (seconds)
    WORKSPACE_MEMBERSHIP_TTL_SECONDS: int = 900

    # Hosted Commercial Billing (Polar.sh Merchant of Record — per-active-committer)
    # Default False keeps self-hosted MIT deployments completely open.
    BILLING_ENABLED: bool = False
    POLAR_ACCESS_TOKEN: str = ""
    POLAR_WEBHOOK_SECRET: str = ""
    POLAR_SERVER: str = "sandbox"
    POLAR_PRO_PRODUCT_ID: str = ""
    POLAR_METER_EVENT_NAME: str = "pr_reviewed"
    MANAGED_MONTHLY_REVIEW_CAP_PER_SEAT: int = 300

    # Managed AI LLM Provider Pool (used by subscribed workspaces in 'managed' key mode)
    MANAGED_LLM_PRIMARY_PROVIDER: str = "gemini"
    MANAGED_LLM_PRIMARY_MODEL: str = "gemini-2.5-flash"
    MANAGED_LLM_PRIMARY_API_KEY: str = ""
    MANAGED_LLM_PRIMARY_BASE_URL: str = ""

    MANAGED_LLM_FALLBACK_PROVIDER: str = "groq"
    MANAGED_LLM_FALLBACK_MODEL: str = "llama-3.3-70b-versatile"
    MANAGED_LLM_FALLBACK_API_KEY: str = ""
    MANAGED_LLM_FALLBACK_BASE_URL: str = ""

    HOST: str = "0.0.0.0"
    PORT: int = 8080

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Convenience lowercase accessors
    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def master_encryption_key(self) -> str:
        return self.MASTER_ENCRYPTION_KEY

    @property
    def admin_api_token(self) -> str:
        return self.ADMIN_API_TOKEN

    @property
    def app_base_url(self) -> str:
        return self.APP_BASE_URL.strip().rstrip("/")

    @property
    def github_app_slug(self) -> str:
        return self.GITHUB_APP_SLUG.strip()

    @property
    def github_app_install_url(self) -> str:
        slug = self.github_app_slug
        if slug:
            return f"https://github.com/apps/{slug}/installations/new"
        return "https://github.com/settings/installations"

    @property
    def cors_origins(self) -> list[str]:
        origins = [
            "http://localhost:8080",
            "http://127.0.0.1:8080",
        ]
        if self.app_base_url and self.app_base_url not in origins:
            origins.append(self.app_base_url)
        for raw in self.CORS_ALLOWED_ORIGINS.split(","):
            cleaned = raw.strip().rstrip("/")
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
        return origins

    @property
    def github_app_id(self) -> str:
        return self.GITHUB_APP_ID

    @property
    def github_webhook_secret(self) -> str:
        return self.GITHUB_WEBHOOK_SECRET

    @property
    def github_private_key(self) -> str:
        return self.GITHUB_PRIVATE_KEY

    @property
    def github_client_id(self) -> str:
        return self.GITHUB_CLIENT_ID

    @property
    def github_client_secret(self) -> str:
        return self.GITHUB_CLIENT_SECRET

    @property
    def clerk_publishable_key(self) -> str:
        return (self.CLERK_PUBLISHABLE_KEY or self.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY).strip()

    @property
    def clerk_secret_key(self) -> str:
        return self.CLERK_SECRET_KEY.strip()

    @property
    def clerk_frontend_api(self) -> str:
        return self.CLERK_FRONTEND_API.strip()

    @property
    def clerk_accounts_url(self) -> str:
        return self.CLERK_ACCOUNTS_URL.strip().rstrip("/")

    @property
    def polar_api_base_url(self) -> str:
        if self.POLAR_SERVER.strip().lower() == "production":
            return "https://api.polar.sh"
        return "https://sandbox-api.polar.sh"

    @property
    def session_secret(self) -> str:
        return self.SESSION_SECRET or self.ADMIN_API_TOKEN or self.MASTER_ENCRYPTION_KEY[:32]

    @field_validator("MASTER_ENCRYPTION_KEY")
    @classmethod
    def validate_master_key(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) != 64:
            raise ValueError(f"MASTER_ENCRYPTION_KEY must be a 64-character hex string (got length {len(cleaned)})")
        try:
            bytes.fromhex(cleaned)
        except ValueError as err:
            raise ValueError("MASTER_ENCRYPTION_KEY must be a valid hexadecimal string") from err
        return cleaned

    @field_validator("GITHUB_PRIVATE_KEY")
    @classmethod
    def format_private_key(cls, v: str) -> str:
        if not v:
            return ""
        cleaned = v.strip().strip("'\"")
        return cleaned.replace("\\n", "\n")

    @field_validator("GITHUB_APP_ID", mode="before")
    @classmethod
    def format_app_id(cls, v: object) -> str:
        return str(v).strip()


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()


# Module-level settings export for direct import
settings = get_settings()
