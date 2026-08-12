"""
Configuration management for the Fun2Oosh Economy Bot using Pydantic settings.

Values can be overridden through environment variables or a `.env` file
(e.g. `DISCORD_TOKEN=...`, `DATABASE_URL=...`).
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Economy bot configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core
    discord_token: str = Field(default="demo_token")
    guild_id: Optional[int] = Field(default=None)
    database_url: str = Field(default="sqlite+aiosqlite:///fun2oosh.db")
    log_level: str = Field(default="INFO")
    owner_id: Optional[int] = Field(default=None)
    command_prefix: str = Field(default="!")

    # Currency name used by dynamic event messages (no hardcoded symbols)
    currency_name: str = Field(default="💎️")

    # Economy/Game settings
    min_bet: int = Field(default=10)
    max_bet: int = Field(default=10000)
    daily_wager_limit: int = Field(default=50000)
    work_reward: int = Field(default=100)
    daily_reward: int = Field(default=500)
    weekly_reward: int = Field(default=2000)
    monthly_reward: int = Field(default=5000)
