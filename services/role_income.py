"""
Role-income service — per-guild income tiers with per-role claim windows,
used by `!collect`.

Design choice: when a user holds several income roles, `!collect` pays the
**highest** eligible amount rather than the combined total. Reasons:

- Predictable and easy for administrators to reason about (clean tiers:
  Member < VIP < Premium < Booster).
- No exploit incentive to stack many low roles.
- Balances naturally: a server's total payout per user is bounded by its
  most generous role, so admins can price tiers without worrying about
  combos.

Each role also carries its own claim interval (e.g. 2 hours). Claim timing
is persisted in `role_claims` so windows survive restarts and render as
Discord relative timestamps (`<t:...:R>`).
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import RoleClaim, RoleIncome
from models.base import utcnow


class RoleIncomeService:
    """CRUD for role-based income and per-user claim tracking."""

    @staticmethod
    async def set(
        session: AsyncSession,
        guild_id: int,
        role_id: int,
        amount: int,
        claim_interval: int = 3600,
    ) -> RoleIncome:
        """Create or update the income for a role (amount + claim interval)."""
        row = (
            await session.execute(
                select(RoleIncome).where(
                    RoleIncome.guild_id == guild_id, RoleIncome.role_id == role_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = RoleIncome(
                guild_id=guild_id,
                role_id=role_id,
                amount=amount,
                claim_interval=claim_interval,
            )
            session.add(row)
        else:
            row.amount = amount
            row.claim_interval = claim_interval
        await session.commit()
        return row

    @staticmethod
    async def remove(session: AsyncSession, guild_id: int, role_id: int) -> bool:
        """Remove the income for a role. Returns True if one existed."""
        existing = await session.scalar(
            select(RoleIncome.role_id).where(
                RoleIncome.guild_id == guild_id, RoleIncome.role_id == role_id
            )
        )
        if existing is None:
            return False
        await session.execute(
            delete(RoleIncome).where(
                RoleIncome.guild_id == guild_id, RoleIncome.role_id == role_id
            )
        )
        await session.commit()
        return True

    @staticmethod
    async def list_all(session: AsyncSession, guild_id: int) -> List[RoleIncome]:
        """All income rows for a guild, highest amount first."""
        return list(
            (
                await session.execute(
                    select(RoleIncome)
                    .where(RoleIncome.guild_id == guild_id)
                    .order_by(RoleIncome.amount.desc())
                )
            ).scalars()
        )

    @staticmethod
    async def highest_for(
        session: AsyncSession, guild_id: int, role_ids: List[int]
    ) -> Optional[RoleIncome]:
        """The income row for the highest-paying role the user holds."""
        if not role_ids:
            return None
        return (
            await session.execute(
                select(RoleIncome)
                .where(RoleIncome.guild_id == guild_id, RoleIncome.role_id.in_(role_ids))
                .order_by(RoleIncome.amount.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # --------------------------------------------------------------- claims

    @staticmethod
    async def last_claim(
        session: AsyncSession, guild_id: int, user_id: int, role_id: int
    ) -> Optional[RoleClaim]:
        """The last claim record for a (guild, user, role) triple, if any."""
        return (
            await session.execute(
                select(RoleClaim).where(
                    RoleClaim.guild_id == guild_id,
                    RoleClaim.user_id == user_id,
                    RoleClaim.role_id == role_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def record_claim(
        session: AsyncSession, guild_id: int, user_id: int, role_id: int
    ) -> RoleClaim:
        """Set the claim time for a (guild, user, role) to now (upsert)."""
        claim = await RoleIncomeService.last_claim(session, guild_id, user_id, role_id)
        if claim is None:
            claim = RoleClaim(
                guild_id=guild_id, user_id=user_id, role_id=role_id, claimed_at=utcnow()
            )
            session.add(claim)
        else:
            claim.claimed_at = utcnow()
        return claim

    @staticmethod
    def seconds_until_next_claim(
        claim: Optional[RoleClaim], interval: int, now: Optional[datetime] = None
    ) -> int:
        """Seconds until the user may claim again (0 if the window has passed)."""
        if claim is None:
            return 0
        now = now or utcnow()
        elapsed = (now - claim.claimed_at).total_seconds()
        return max(0, interval - int(elapsed))
