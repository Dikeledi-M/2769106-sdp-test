"""Application settings, loaded from environment variables / a .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Repository Analysis Tool"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    # SQLite for local development; PostgreSQL ready via the same
    # SQLAlchemy/Alembic stack (e.g. "postgresql+psycopg://rat:rat@host/rat").
    DATABASE_URL: str = "sqlite:///./rat.db"

    # --- Authentication ---
    # Must be at least 32 bytes for HS256 (RFC 7518). Override in .env!
    SECRET_KEY: str = "dev-only-insecure-secret-key-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- CORS (comma-separated list of allowed origins) ---
    BACKEND_CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Repository storage (consumed by the ingestion services) ---
    DATA_DIR: Path = Path("data")
    UPLOAD_DIR: Path = Path("data/uploads")
    CLONE_DIR: Path = Path("data/clones")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
