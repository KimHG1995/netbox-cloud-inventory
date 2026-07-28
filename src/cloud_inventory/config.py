from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVENTORY_", env_file=".env")

    database_url: str
    artifact_root: Path = Path("/var/lib/cloud-inventory/artifacts")
    netbox_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    netbox_token: SecretStr
    csrf_secret: SecretStr
    max_file_bytes: int = 100 * 1024 * 1024
    max_files_per_import: int = 20
    artifact_retention_days: int = 30


@lru_cache
def get_settings() -> Settings:
    # Required values are supplied by pydantic-settings from the environment.
    return Settings()  # type: ignore[call-arg]
