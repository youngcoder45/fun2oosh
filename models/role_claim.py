"""
Per-user role claim tracking.

Records the last time a user claimed a specific role's income. Combined with
`RoleIncome.claim_interval` this enforces per-role claim windows that survive
bot restarts (unlike in-memory cooldowns) and can be rendered as Discord
relative timestamps (`<t:...:R>`).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class RoleClaim(Base):
    """Last claim time for a (guild, user, income role) triple."""

    __tablename__ = 'role_claims'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return (
            f"<RoleClaim(guild_id={self.guild_id}, user_id={self.user_id}, "
            f"role_id={self.role_id}, claimed_at={self.claimed_at})>"
        )
