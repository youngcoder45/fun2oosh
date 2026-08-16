"""
Active money boosters: persisted so they survive bot restarts.

Boosters (lucky charm, 2x money) used to live only in memory and were lost
on restart. Rows are written when a booster is activated and loaded back
into ``BoosterManager`` on startup; expired rows are purged during restore.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ActiveBooster(Base):
    """A user's currently active money booster."""

    __tablename__ = "active_boosters"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booster_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # 'all', ...
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ActiveBooster(user_id={self.user_id}, type='{self.booster_type}', "
            f"x{self.multiplier})>"
        )
