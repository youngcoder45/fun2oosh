"""
Wallet model for user economy.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class Wallet(Base):
    """Represents a user's wallet and bank balance."""

    __tablename__ = 'wallets'

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True) # Foreign key to User.id
    balance: Mapped[int] = mapped_column(Integer, default=0) # Wallet balance
    bank: Mapped[int] = mapped_column(Integer, default=0) # Bank balance
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Limits and tracking
    daily_wagered: Mapped[int] = mapped_column(Integer, default=0)
    last_daily_reset: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Progression (added via migration on existing databases)
    prestige: Mapped[int] = mapped_column(Integer, default=0)
    reputation: Mapped[int] = mapped_column(Integer, default=0)
    daily_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_daily_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_weekly_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_monthly_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_passive_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Wallet(user_id={self.user_id}, balance={self.balance}, bank={self.bank})>"
