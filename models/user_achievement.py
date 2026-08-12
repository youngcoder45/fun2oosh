"""
User achievements — which achievements a user has unlocked and when.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UserAchievement(Base):
    """A user's unlocked achievement."""

    __tablename__ = 'user_achievements'

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    achievement_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<UserAchievement(user_id={self.user_id}, achievement='{self.achievement_id}')>"
