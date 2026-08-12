"""
Role-income service — per-guild hourly income tiers used by `!collect`.

Design choice: when a user holds several income roles, `!collect` pays the
**highest** eligible rate rather than the combined total. Reasons:

- Predictable and easy for administrators to reason about (clean tiers:
  Member < VIP < Premium < Booster).
- No exploit incentive to stack many low roles.
- Balances naturally: a server's total payout per user is bounded by its
  most generous role, so admins can price tiers without worrying about
  combos.
"""

from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import RoleIncome


class RoleIncomeService:
    """CRUD for role-based hourly income."""

    @staticmethod
    async def set(
        session: AsyncSession, guild_id: int, role_id: int, hourly_rate: int
    ) -> RoleIncome:
        """Create or update the income rate for a role."""
        row = (
            await session.execute(
                select(RoleIncome).where(
                    RoleIncome.guild_id == guild_id, RoleIncome.role_id == role_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = RoleIncome(guild_id=guild_id, role_id=role_id, hourly_rate=hourly_rate)
            session.add(row)
        else:
            row.hourly_rate = hourly_rate
        await session.commit()
        return row

    @staticmethod
    async def remove(session: AsyncSession, guild_id: int, role_id: int) -> bool:
        """Remove the income rate for a role. Returns True if one existed."""
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
        """All income rows for a guild, highest rate first."""
        return list(
            (
                await session.execute(
                    select(RoleIncome)
                    .where(RoleIncome.guild_id == guild_id)
                    .order_by(RoleIncome.hourly_rate.desc())
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
                .order_by(RoleIncome.hourly_rate.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
