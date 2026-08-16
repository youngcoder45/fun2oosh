"""
Lottery: per-guild jackpot pot and ticket ownership, persisted in the DB so
the pot and entries survive bot restarts.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class Lottery(Base):
    """A guild's lottery: current pot and when the next draw happens."""

    __tablename__ = "lotteries"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pot: Mapped[int] = mapped_column(Integer, default=0)
    draw_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_draw_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_winner_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:
        return f"<Lottery(guild_id={self.guild_id}, pot={self.pot})>"


class LotteryTicket(Base):
    """How many tickets a user holds in a guild's lottery."""

    __tablename__ = "lottery_tickets"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<LotteryTicket(guild={self.guild_id}, user={self.user_id}, count={self.count})>"
