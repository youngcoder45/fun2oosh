"""
Guild configuration service: per-server economy overrides and audit logging.
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog, GuildConfig
from utils.config import Config

logger = logging.getLogger(__name__)

# key -> (python_type, min, max, friendly label)
SETTINGS: dict = {
    "work_reward": ("int", 1, 1_000_000, "work reward"),
    "daily_reward": ("int", 1, 10_000_000, "daily reward"),
    "weekly_reward": ("int", 1, 50_000_000, "weekly reward"),
    "monthly_reward": ("int", 1, 200_000_000, "monthly reward"),
    "tax_rate": ("float", 0, 0.5, "transfer tax rate"),
    "min_bet": ("int", 1, 1_000_000_000, "minimum bet"),
    "max_bet": ("int", 1, 1_000_000_000, "maximum bet"),
    "daily_wager_limit": ("int", 1, 1_000_000_000, "daily wager limit"),
    "passive_income": ("int", 0, 1_000_000, "hourly passive income"),
    "anti_alt": ("bool", None, None, "anti-alt protection"),
    "min_account_age_days": ("int", 0, 365, "minimum account age (days)"),
}


class GuildConfigService:
    """Read/write per-guild economy settings."""

    @staticmethod
    async def get(session: AsyncSession, guild_id: int) -> GuildConfig:
        """Return the guild config row, creating it if missing."""
        row = (
            await session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
        ).scalar_one_or_none()
        if row is None:
            row = GuildConfig(guild_id=guild_id)
            session.add(row)
            await session.commit()
        return row

    @staticmethod
    def effective(row: GuildConfig, defaults: Config, key: str):
        """Resolve a setting: guild override, else bot default."""
        value = getattr(row, key, None)
        if value is not None:
            return value
        return getattr(defaults, key, None)

    @staticmethod
    async def set(
        session: AsyncSession,
        guild_id: int,
        key: str,
        raw_value: str,
        defaults: Optional[Config] = None,
    ) -> Tuple[bool, str]:
        """Validate and set a setting. Returns ``(success, message)``."""
        from utils.config import Config as _Config

        if defaults is None:
            defaults = _Config()
        if key not in SETTINGS:
            return False, f"Unknown setting `{key}`. Available: {', '.join(sorted(SETTINGS))}"
        kind, lo, hi, label = SETTINGS[key]

        value: int | float
        try:
            if kind == "bool":
                value = raw_value.strip().lower() in ("1", "true", "yes", "on")
            elif kind == "float":
                value = float(raw_value)
            else:
                value = int(raw_value)
        except ValueError:
            return False, f"`{raw_value}` is not a valid {kind} value for `{key}`."

        if lo is not None and value < lo:
            return False, f"`{key}` must be at least {lo}."
        if hi is not None and value > hi:
            return False, f"`{key}` must be at most {hi}."

        row = await GuildConfigService.get(session, guild_id)
        old = getattr(row, key, None)
        setattr(row, key, value)

        # Cross-field validation: min_bet must not exceed max_bet
        if key == "min_bet":
            max_bet = row.max_bet if row.max_bet is not None else defaults.max_bet
            if value > max_bet:
                setattr(row, key, old)
                return False, f"`min_bet` cannot exceed `max_bet` ({max_bet})."
        if key == "max_bet":
            min_bet = row.min_bet if row.min_bet is not None else defaults.min_bet
            if value < min_bet:
                setattr(row, key, old)
                return False, f"`max_bet` cannot be below `min_bet` ({min_bet})."

        await session.commit()
        return True, f"Set **{label}** (`{key}`) to **{value}**."

    @staticmethod
    def describe(row: GuildConfig, defaults: Config) -> str:
        """Human-readable summary of the effective configuration."""
        lines = []
        for key in sorted(SETTINGS):
            _, _, _, label = SETTINGS[key]
            value = GuildConfigService.effective(row, defaults, key)
            if isinstance(value, bool):
                value = "on" if value else "off"
            lines.append(f"• **{label.title()}** (`{key}`): `{value}`")
        return "\n".join(lines)


class AuditService:
    """Record and retrieve admin actions."""

    @staticmethod
    async def log(
        session: AsyncSession,
        actor_id: int,
        action: str,
        details: str = "",
        guild_id: Optional[int] = None,
        target_id: Optional[int] = None,
    ) -> None:
        session.add(
            AuditLog(
                guild_id=guild_id,
                actor_id=actor_id,
                action=action,
                target_id=target_id,
                details=details[:2000],
            )
        )
        await session.commit()

    @staticmethod
    async def recent(session: AsyncSession, guild_id: int, limit: int = 10) -> List[AuditLog]:
        return list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.guild_id == guild_id)
                    .order_by(AuditLog.id.desc())
                    .limit(limit)
                )
            ).scalars()
        )
