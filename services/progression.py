"""
Progression service: daily streaks, prestige, reputation, and achievements.

Achievements are defined in code and unlock permanently; unlock state is
persisted in ``user_achievements``.
"""

import logging
from datetime import timedelta
from typing import Dict, List, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Transaction, UserAchievement, Wallet, utcnow
from utils.cooldowns import cooldown_notice
from utils.economy_utils import EconomyUtils
from utils.helpers import unix_ts

from .items import ItemService
from .locks import lock_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Achievement definitions
# ---------------------------------------------------------------------------

ACHIEVEMENTS: Dict[str, dict] = {
    "first_steps": {
        "name": "First Steps",
        "desc": "Work for the first time",
    },
    "criminal": {
        "name": "Criminal",
        "desc": "Commit your first crime",
    },
    "master_thief": {
        "name": "Master Thief",
        "desc": "Successfully rob 10 times",
    },
    "gambler": {
        "name": "Gambler",
        "desc": "Place your first gamble",
    },
    "high_roller": {
        "name": "High Roller",
        "desc": "Gamble 10,000 💎️ in one bet",
    },
    "shopaholic": {
        "name": "Shopaholic",
        "desc": "Make your first shop purchase",
    },
    "collector": {
        "name": "Collector",
        "desc": "Own 5 different items",
    },
    "explorer": {
        "name": "Explorer",
        "desc": "Search 5 times",
    },
    "streak_3": {
        "name": "On Fire",
        "desc": "Reach a 3-day daily streak",
    },
    "streak_7": {
        "name": "Unstoppable",
        "desc": "Reach a 7-day daily streak",
    },
    "rich": {
        "name": "Rich",
        "desc": "Reach 100,000 net worth",
    },
    "millionaire": {
        "name": "Millionaire",
        "desc": "Reach 1,000,000 net worth",
    },
    "prestige_1": {
        "name": "Ascended",
        "desc": "Prestige for the first time",
    },
}


# Achievement id -> condition. `stats` is a dict of derived counters.
# The `event` is the triggering action ('work', 'crime', 'rob', 'gamble',
# 'search', 'buy', 'daily', 'prestige', 'crate', 'use').
def _condition_met(achievement_id: str, event: str, stats: dict) -> bool:
    if achievement_id == "first_steps":
        return event == "work" and stats.get("work_count", 0) >= 1
    if achievement_id == "criminal":
        return event == "crime" and stats.get("crime_count", 0) >= 1
    if achievement_id == "master_thief":
        return stats.get("rob_count", 0) >= 10
    if achievement_id == "gambler":
        return event == "gamble" and stats.get("gamble_count", 0) >= 1
    if achievement_id == "high_roller":
        return stats.get("max_gamble", 0) >= 10000
    if achievement_id == "shopaholic":
        return event == "buy" and stats.get("buy_count", 0) >= 1
    if achievement_id == "collector":
        return stats.get("distinct_items", 0) >= 5
    if achievement_id == "explorer":
        return stats.get("search_count", 0) >= 5
    if achievement_id == "streak_3":
        return stats.get("streak", 0) >= 3
    if achievement_id == "streak_7":
        return stats.get("streak", 0) >= 7
    if achievement_id == "rich":
        return stats.get("networth", 0) >= 100_000
    if achievement_id == "millionaire":
        return stats.get("networth", 0) >= 1_000_000
    if achievement_id == "prestige_1":
        return event == "prestige" and stats.get("prestige", 0) >= 1
    return False


class AchievementService:
    """Unlock checks + persistence."""

    @staticmethod
    async def unlocked_ids(session: AsyncSession, user_id: int) -> List[str]:
        return list(
            (
                await session.execute(
                    select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id)
                )
            ).scalars()
        )

    @staticmethod
    async def count(session: AsyncSession, user_id: int) -> int:
        return (
            await session.execute(
                select(func.count(UserAchievement.user_id)).where(
                    UserAchievement.user_id == user_id
                )
            )
        ).scalar() or 0

    @staticmethod
    async def check(session: AsyncSession, user_id: int, event: str) -> List[dict]:
        """Evaluate all locked achievements; unlock and return new ones."""
        unlocked = set(await AchievementService.unlocked_ids(session, user_id))
        pending = [aid for aid in ACHIEVEMENTS if aid not in unlocked]
        if not pending:
            return []

        stats = await AchievementService._stats(session, user_id)

        new: List[dict] = []
        for aid in pending:
            if _condition_met(aid, event, stats):
                session.add(UserAchievement(user_id=user_id, achievement_id=aid))
                new.append({"id": aid, **ACHIEVEMENTS[aid]})
        if new:
            await session.commit()
        return new

    @staticmethod
    async def _stats(session: AsyncSession, user_id: int) -> dict:
        wallet = await EconomyUtils.get_or_create_wallet(session, user_id)

        counts: dict = {}
        for tx_type in (
            "work",
            "crime",
            "rob",
            "gamble",
            "search",
            "buy",
            "crate",
            "item",
        ):
            counts[f"{tx_type}_count"] = (
                await session.execute(
                    select(func.count(Transaction.id)).where(
                        Transaction.user_id == user_id, Transaction.type == tx_type
                    )
                )
            ).scalar() or 0

        max_gamble = (
            await session.execute(
                select(func.max(Transaction.amount)).where(
                    Transaction.user_id == user_id, Transaction.type == "gamble"
                )
            )
        ).scalar() or 0

        return {
            **counts,
            "max_gamble": max_gamble,
            "streak": wallet.daily_streak or 0,
            "prestige": wallet.prestige or 0,
            "distinct_items": await ItemService.distinct_item_count(session, user_id),
            "networth": (wallet.balance or 0)
            + (wallet.bank or 0)
            + await ItemService.inventory_value(session, user_id),
        }


class ProgressionService:
    """Streaks, prestige, and reputation."""

    # --------------------------------------------------------------- streaks

    @staticmethod
    async def apply_daily(
        session: AsyncSession, user_id: int, base_reward: int
    ) -> Tuple[bool, str, int, int]:
        """Apply the daily claim: 24h guard, streak update, bonus + prestige multiplier.

        Returns ``(success, message, final_reward, streak)``.
        """
        async with lock_manager.for_user(user_id):
            wallet = await EconomyUtils.get_or_create_wallet(session, user_id)
            now = utcnow()

            if wallet.last_daily_at and (now - wallet.last_daily_at) < timedelta(hours=24):
                ready_at = unix_ts(wallet.last_daily_at + timedelta(hours=24))
                return (
                    False,
                    cooldown_notice("Daily reward", ready_at - unix_ts(now)),
                    0,
                    wallet.daily_streak or 0,
                )

            if wallet.last_daily_at is None or (now - wallet.last_daily_at).days >= 2:
                wallet.daily_streak = 1
            else:
                wallet.daily_streak = (wallet.daily_streak or 0) + 1
            wallet.last_daily_at = now

            streak = wallet.daily_streak or 1
            bonus = min(streak, 7) * 25
            reward = int((base_reward + bonus) * ProgressionService._prestige_multiplier(wallet))

            await EconomyUtils.add_money(
                session, user_id, reward, "daily", f"Daily reward (streak {streak})"
            )
            await session.commit()
            return True, "", reward, streak

    @staticmethod
    async def apply_weekly(
        session: AsyncSession, user_id: int, base_reward: int
    ) -> Tuple[bool, str]:
        """Claim the weekly reward (DB-backed 7-day cooldown).

        Persisted via ``wallet.last_weekly_at`` so the cooldown survives bot
        restarts, exactly like daily/monthly. Returns ``(success, message)``.
        """
        async with lock_manager.for_user(user_id):
            wallet = await EconomyUtils.get_or_create_wallet(session, user_id)
            now = utcnow()
            if wallet.last_weekly_at and (now - wallet.last_weekly_at) < timedelta(days=7):
                ready_at = unix_ts(wallet.last_weekly_at + timedelta(days=7))
                return False, cooldown_notice("Weekly reward", ready_at - unix_ts(now))

            reward = int(base_reward * ProgressionService._prestige_multiplier(wallet))
            wallet.last_weekly_at = now
            await EconomyUtils.add_money(session, user_id, reward, "weekly", "Weekly reward")
            await session.commit()
            return True, f"You claimed your weekly reward of **{reward:,} 💎️**!"

    @staticmethod
    async def apply_monthly(
        session: AsyncSession, user_id: int, base_reward: int
    ) -> Tuple[bool, str]:
        """Claim the monthly reward. Returns ``(success, message)``."""
        async with lock_manager.for_user(user_id):
            wallet = await EconomyUtils.get_or_create_wallet(session, user_id)
            now = utcnow()
            if wallet.last_monthly_at and (now - wallet.last_monthly_at) < timedelta(days=30):
                ready_at = unix_ts(wallet.last_monthly_at + timedelta(days=30))
                return False, cooldown_notice("Monthly reward", ready_at - unix_ts(now))

            reward = int(base_reward * ProgressionService._prestige_multiplier(wallet))
            wallet.last_monthly_at = now
            await EconomyUtils.add_money(session, user_id, reward, "monthly", "Monthly reward")
            await session.commit()
            return True, f"You claimed your monthly reward of **{reward:,} 💎️**!"

    @staticmethod
    def _prestige_multiplier(wallet: Wallet) -> float:
        """+2% per prestige level, capped at +50%."""
        return 1.0 + min(wallet.prestige or 0, 25) * 0.02

    # ------------------------------------------------------------- prestige

    @staticmethod
    async def prestige(session: AsyncSession, user_id: int) -> Tuple[bool, str]:
        """Reset wealth for +1 prestige level (requires 1,000,000 net worth)."""
        async with lock_manager.for_user(user_id):
            wallet = await EconomyUtils.get_or_create_wallet(session, user_id)
            networth = (wallet.balance or 0) + (wallet.bank or 0)
            if networth < 1_000_000:
                return False, (
                    f"You need **1,000,000 net worth** to prestige(you have {networth:,})."
                )
            wallet.prestige = (wallet.prestige or 0) + 1
            session.add(
                Transaction(
                    user_id=user_id,
                    type="prestige",
                    amount=-networth,
                    description=f"Prestige {wallet.prestige} reset",
                )
            )
            wallet.balance = 5000
            wallet.bank = 0
            await session.commit()
            return (
                True,
                f"You reached **Prestige {wallet.prestige}**! All rewards +{int(ProgressionService._prestige_multiplier(wallet) * 100 - 100)}%",
            )

    # ---------------------------------------------------------- reputation

    @staticmethod
    async def give_reputation(session: AsyncSession, target_id: int) -> bool:
        """Increment a user's reputation counter."""
        async with lock_manager.for_user(target_id):
            wallet = await EconomyUtils.get_or_create_wallet(session, target_id)
            wallet.reputation = (wallet.reputation or 0) + 1
            await session.commit()
            return True
