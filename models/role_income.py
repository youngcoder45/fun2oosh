"""
Per-guild role income configuration.

Administrators assign an hourly income to roles; `!collect` pays the
highest eligible role's rate. No values are hardcoded — everything lives
in the database and survives restarts.
"""

from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RoleIncome(Base):
    """Hourly income assigned to a role by a guild administrator."""

    __tablename__ = 'role_income'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hourly_rate: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<RoleIncome(guild_id={self.guild_id}, role_id={self.role_id}, rate={self.hourly_rate})>"
