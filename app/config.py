"""Runtime configuration, overridable through ``GEO_*`` environment variables or a ``.env`` file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    # SQLite by default so the project runs with zero infrastructure.
    # Any SQLAlchemy URL works, e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = "sqlite:///./data/geo.db"

    # Upload guards.
    max_upload_bytes: int = 50 * 1024 * 1024
    # Total bytes a zip may expand to (zip-bomb protection).
    max_uncompressed_bytes: int = 250 * 1024 * 1024
    max_zip_members: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
