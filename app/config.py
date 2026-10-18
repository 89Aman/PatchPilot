from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    MASTER_ENCRYPTION_KEY: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    ADMIN_API_TOKEN: str = "super-secret-admin-token-change-me"

    GITHUB_APP_ID: str = "123456"
    GITHUB_WEBHOOK_SECRET: str = "your_github_webhook_secret_here"
    GITHUB_PRIVATE_KEY: str = ""

    # Optional GitHub OAuth App credentials
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    SESSION_SECRET: str = ""

    # Optional Clerk Authentication (clerk.com)
    CLERK_PUBLISHABLE_KEY: str = ""
    CLERK_SECRET_KEY: str = ""
    CLERK_JWKS_URL: str = ""
    CLERK_FRONTEND_API: str = ""

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
        return self.CLERK_PUBLISHABLE_KEY.strip()

    @property
    def clerk_secret_key(self) -> str:
        return self.CLERK_SECRET_KEY.strip()

    @property
    def clerk_frontend_api(self) -> str:
        return self.CLERK_FRONTEND_API.strip()

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
