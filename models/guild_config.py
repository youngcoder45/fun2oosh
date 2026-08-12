"""
Per-guild economy configuration (UnbelievaBoat-style server settings).

Defaults come from `utils.config.Config`; rows here override them per server.
"""

from typing import Optional

from sqlalchemy import BigInteger, Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GuildConfig(Base):
    """Economy settings scoped to a Discord guild."""

    __tablename__ = 'guild_config'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Rewards
    work_reward: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_reward: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weekly_reward: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_reward: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Economy rules
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)        # 0.0 – 0.5 (applied to transfers)
    min_bet: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_bet: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_wager_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Passive income (coins per hour per active wallet; 0 disables)
    passive_income: Mapped[int] = mapped_column(Integer, default=0)

    # Anti-abuse
    anti_alt: Mapped[bool] = mapped_column(Boolean, default=False)
    min_account_age_days: Mapped[int] = mapped_column(Integer, default=7)

    # Flags
    currency_name: Mapped[str] = mapped_column(String(16), default='coins')

    def __repr__(self) -> str:
        return f"<GuildConfig(guild_id={self.guild_id})>"
