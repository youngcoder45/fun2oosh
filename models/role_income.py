"""
Per-guild role income configuration.

Administrators assign a coin amount and a claim interval to roles; `!collect`
pays **every** eligible role's configured amount, each on its own interval
(e.g. VIP pays 750 every 2 hours, Member pays 200 every hour a user holding
both earns both). No values are hardcoded, everything lives in the database
and survives restarts.

The interval is a property of the role, not of the bot: one role can pay
every 30 minutes, another once a week. Per-user claim timing is tracked in
`role_claims` so intervals survive restarts and can be rendered as Discord
relative timestamps (`<t:...:R>`).
"""

from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RoleIncome(Base):
    """Coins + claim interval assigned to a role by a guild administrator."""

    __tablename__ = "role_income"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    claim_interval: Mapped[int] = mapped_column(Integer, default=3600)  # seconds

    def __repr__(self) -> str:
        return (
            f"<RoleIncome(guild_id={self.guild_id}, role_id={self.role_id}, "
            f"amount={self.amount}, interval={self.claim_interval}s)>"
        )
