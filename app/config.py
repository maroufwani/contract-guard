"""Configuration, read from environment variables with sensible defaults."""
import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "sqlite:///./data/contract_guard.db"
DEFAULT_WEBHOOK_URL = "http://webhook-receiver:9000"


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    webhook_url: str = DEFAULT_WEBHOOK_URL


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        webhook_url=os.environ.get("WEBHOOK_URL", DEFAULT_WEBHOOK_URL),
    )


settings = load_settings()
