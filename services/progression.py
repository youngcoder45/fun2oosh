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
    "workaholic": {
        "name": "Workaholic",
        "desc": "Work 10 times",
    },
    "career_worker": {
        "name": "Career Worker",
        "desc": "Work 50 times",
    },
    "beggar": {
        "name": "Broke Beggar",
        "desc": "Beg for the first time",
    },
    "panhandler": {
        "name": "Panhandler",
        "desc": "Beg 25 times",
    },
    "hustler": {
        "name": "Hustler",
        "desc": "Earn 50,000 💎️ in total",
    },
    "hunter": {
        "name": "Hunter",
        "desc": "Hunt for the first time",
    },
    "big_game_hunter": {
        "name": "Big Game Hunter",
        "desc": "Hunt 25 times",
    },
    "angler": {
        "name": "Angler",
        "desc": "Fish for the first time",
    },
    "master_angler": {
        "name": "Master Angler",
        "desc": "Fish 25 times",
    },
    "miner": {
        "name": "Miner",
        "desc": "Mine for the first time",
    },
    "gold_digger": {
        "name": "Gold Digger",
        "desc": "Mine 25 times",
    },
    "outdoorsman": {
        "name": "Outdoorsman",
        "desc": "Hunt, fish, and mine at least once",
    },
    "getaway_driver": {
        "name": "Getaway Driver",
        "desc": "Rob 25 times",
    },
    "legendary_thief": {
        "name": "Legendary Thief",
        "desc": "Rob 50 times",
    },
    "crime_boss": {
        "name": "Crime Boss",
        "desc": "Commit 25 crimes",
    },
    "kingpin": {
        "name": "Kingpin",
        "desc": "Commit 100 crimes",
    },
    "archaeologist": {
        "name": "Archaeologist",
        "desc": "Search 25 times",
    },
    "scavenger": {
        "name": "Scavenger",
        "desc": "Search 100 times",
    },
    "all_in": {
        "name": "All In",
        "desc": "Gamble 50,000 💎️ in one bet",
    },
    "whale": {
        "name": "Whale",
        "desc": "Gamble 250,000 💎️ in one bet",
    },
    "degenerate": {
        "name": "Degenerate",
        "desc": "Gamble 100 times",
    },
    "hoarder": {
        "name": "Hoarder",
        "desc": "Own 20 different items",
    },
    "pack_rat": {
        "name": "Pack Rat",
        "desc": "Own 50 different items",
    },
    "item_connoisseur": {
        "name": "Item Connoisseur",
        "desc": "Use 25 items",
    },
    "crate_enthusiast": {
        "name": "Crate Enthusiast",
        "desc": "Open 5 crates",
    },
    "crate_addict": {
        "name": "Crate Addict",
        "desc": "Open 25 crates",
    },
    "merchant": {
        "name": "Merchant",
        "desc": "Sell 10 items",
    },
    "tycoon": {
        "name": "Tycoon",
        "desc": "Sell 100 items",
    },
    "philanthropist": {
        "name": "Philanthropist",
        "desc": "Give 5 gifts",
    },
    "popular": {
        "name": "Popular",
        "desc": "Receive 5 gifts",
    },
    "trader": {
        "name": "Trader",
        "desc": "Complete 5 coin trades",
    },
    "provider": {
        "name": "Provider",
        "desc": "Collect role income 10 times",
    },
    "saver": {
        "name": "Saver",
        "desc": "Deposit 100,000 💎️ in total",
    },
    "banker": {
        "name": "Banker",
        "desc": "Hold 1,000,000 💎️ in the bank",
    },
    "streak_14": {
        "name": "Consistent",
        "desc": "Reach a 14-day daily streak",
    },
    "streak_30": {
        "name": "Marathon",
        "desc": "Reach a 30-day daily streak",
    },
    "tycoon_10m": {
        "name": "Ten Million Club",
        "desc": "Reach 10,000,000 net worth",
    },
    "billionaire": {
        "name": "Billionaire",
        "desc": "Reach 100,000,000 net worth",
    },
    "prestige_5": {
        "name": "Five Times Ascended",
        "desc": "Prestige 5 times",
    },
    "prestige_10": {
        "name": "Enlightened",
        "desc": "Prestige 10 times",
    },
}


# achievement_id -> (stat key from AchievementService._stats, target value)
# Used to show live progress on locked achievements (e.g. "Panhandler — 12/25 begs").
ACHIEVEMENT_PROGRESS: Dict[str, Tuple[str, int]] = {
    "first_steps": ("work_count", 1),
    "workaholic": ("work_count", 10),
    "career_worker": ("work_count", 50),
    "beggar": ("beg_count", 1),
    "panhandler": ("beg_count", 25),
    "hustler": ("total_earned", 50_000),
    "hunter": ("hunt_count", 1),
    "big_game_hunter": ("hunt_count", 25),
    "angler": ("fish_count", 1),
    "master_angler": ("fish_count", 25),
    "miner": ("mine_count", 1),
    "gold_digger": ("mine_count", 25),
    "outdoorsman": ("outdoorsman_count", 3),
    "criminal": ("crime_count", 1),
    "crime_boss": ("crime_count", 25),
    "kingpin": ("crime_count", 100),
    "master_thief": ("rob_count", 10),
    "getaway_driver": ("rob_count", 25),
    "legendary_thief": ("rob_count", 50),
    "explorer": ("search_count", 5),
    "archaeologist": ("search_count", 25),
    "scavenger": ("search_count", 100),
    "gambler": ("gamble_count", 1),
    "degenerate": ("gamble_count", 100),
    "high_roller": ("max_gamble", 10_000),
    "all_in": ("max_gamble", 50_000),
    "whale": ("max_gamble", 250_000),
    "shopaholic": ("buy_count", 1),
    "collector": ("distinct_items", 5),
    "hoarder": ("distinct_items", 20),
    "pack_rat": ("distinct_items", 50),
    "item_connoisseur": ("item_count", 25),
    "crate_enthusiast": ("crate_count", 5),
    "crate_addict": ("crate_count", 25),
    "merchant": ("sell_count", 10),
    "tycoon": ("sell_count", 100),
    "philanthropist": ("gifts_given", 5),
    "popular": ("gifts_received", 5),
    "trader": ("trade_count", 5),
    "provider": ("collect_count", 10),
    "saver": ("deposited_total", 100_000),
    "banker": ("bank", 1_000_000),
    "streak_3": ("streak", 3),
    "streak_7": ("streak", 7),
    "streak_14": ("streak", 14),
    "streak_30": ("streak", 30),
    "rich": ("networth", 100_000),
    "millionaire": ("networth", 1_000_000),
    "tycoon_10m": ("networth", 10_000_000),
    "billionaire": ("networth", 100_000_000),
    "prestige_1": ("prestige", 1),
    "prestige_5": ("prestige", 5),
    "prestige_10": ("prestige", 10),
}


# Achievement id -> condition. `stats` is a dict of derived counters.
# The `event` is the triggering action ('work', 'crime', 'rob', 'gamble',
# 'search', 'buy', 'daily', 'prestige', 'crate', 'use').
def _condition_met(achievement_id: str, event: str, stats: dict) -> bool:
    # --- first-time unlocks (gated on their own event) ---
    if achievement_id == "first_steps":
        return event == "work" and stats.get("work_count", 0) >= 1
    if achievement_id == "criminal":
        return event == "crime" and stats.get("crime_count", 0) >= 1
    if achievement_id == "gambler":
        return event == "gamble" and stats.get("gamble_count", 0) >= 1
    if achievement_id == "shopaholic":
        return event == "buy" and stats.get("buy_count", 0) >= 1
    if achievement_id == "hunter":
        return event == "hunt" and stats.get("hunt_count", 0) >= 1
    if achievement_id == "angler":
        return event == "fish" and stats.get("fish_count", 0) >= 1
    if achievement_id == "miner":
        return event == "mine" and stats.get("mine_count", 0) >= 1
    if achievement_id == "beggar":
        return event == "beg" and stats.get("beg_count", 0) >= 1
    if achievement_id == "prestige_1":
        return event == "prestige" and stats.get("prestige", 0) >= 1
    # --- work / beg ---
    if achievement_id == "master_thief":
        return stats.get("rob_count", 0) >= 10
    if achievement_id == "workaholic":
        return stats.get("work_count", 0) >= 10
    if achievement_id == "career_worker":
        return stats.get("work_count", 0) >= 50
    if achievement_id == "panhandler":
        return stats.get("beg_count", 0) >= 25
    if achievement_id == "hustler":
        return stats.get("total_earned", 0) >= 50_000
    # --- activities ---
    if achievement_id == "big_game_hunter":
        return stats.get("hunt_count", 0) >= 25
    if achievement_id == "master_angler":
        return stats.get("fish_count", 0) >= 25
    if achievement_id == "gold_digger":
        return stats.get("mine_count", 0) >= 25
    if achievement_id == "outdoorsman":
        return (
            stats.get("hunt_count", 0) >= 1
            and stats.get("fish_count", 0) >= 1
            and stats.get("mine_count", 0) >= 1
        )
    # --- crime / rob ---
    if achievement_id == "getaway_driver":
        return stats.get("rob_count", 0) >= 25
    if achievement_id == "legendary_thief":
        return stats.get("rob_count", 0) >= 50
    if achievement_id == "crime_boss":
        return stats.get("crime_count", 0) >= 25
    if achievement_id == "kingpin":
        return stats.get("crime_count", 0) >= 100
    # --- search ---
    if achievement_id == "explorer":
        return stats.get("search_count", 0) >= 5
    if achievement_id == "archaeologist":
        return stats.get("search_count", 0) >= 25
    if achievement_id == "scavenger":
        return stats.get("search_count", 0) >= 100
    # --- gambling ---
    if achievement_id == "high_roller":
        return stats.get("max_gamble", 0) >= 10000
    if achievement_id == "all_in":
        return stats.get("max_gamble", 0) >= 50_000
    if achievement_id == "whale":
        return stats.get("max_gamble", 0) >= 250_000
    if achievement_id == "degenerate":
        return stats.get("gamble_count", 0) >= 100
    # --- items / shop ---
    if achievement_id == "collector":
        return stats.get("distinct_items", 0) >= 5
    if achievement_id == "hoarder":
        return stats.get("distinct_items", 0) >= 20
    if achievement_id == "pack_rat":
        return stats.get("distinct_items", 0) >= 50
    if achievement_id == "item_connoisseur":
        return stats.get("item_count", 0) >= 25
    if achievement_id == "crate_enthusiast":
        return stats.get("crate_count", 0) >= 5
    if achievement_id == "crate_addict":
        return stats.get("crate_count", 0) >= 25
    if achievement_id == "merchant":
        return stats.get("sell_count", 0) >= 10
    if achievement_id == "tycoon":
        return stats.get("sell_count", 0) >= 100
    # --- social ---
    if achievement_id == "philanthropist":
        return stats.get("gifts_given", 0) >= 5
    if achievement_id == "popular":
        return stats.get("gifts_received", 0) >= 5
    if achievement_id == "trader":
        return stats.get("trade_count", 0) >= 5
    if achievement_id == "provider":
        return stats.get("collect_count", 0) >= 10
    # --- banking ---
    if achievement_id == "saver":
        return stats.get("deposited_total", 0) >= 100_000
    if achievement_id == "banker":
        return stats.get("bank", 0) >= 1_000_000
    # --- streaks ---
    if achievement_id == "streak_3":
        return stats.get("streak", 0) >= 3
    if achievement_id == "streak_7":
        return stats.get("streak", 0) >= 7
    if achievement_id == "streak_14":
        return stats.get("streak", 0) >= 14
    if achievement_id == "streak_30":
        return stats.get("streak", 0) >= 30
    # --- net worth / prestige ---
    if achievement_id == "rich":
        return stats.get("networth", 0) >= 100_000
    if achievement_id == "millionaire":
        return stats.get("networth", 0) >= 1_000_000
    if achievement_id == "tycoon_10m":
        return stats.get("networth", 0) >= 10_000_000
    if achievement_id == "billionaire":
        return stats.get("networth", 0) >= 100_000_000
    if achievement_id == "prestige_5":
        return stats.get("prestige", 0) >= 5
    if achievement_id == "prestige_10":
        return stats.get("prestige", 0) >= 10
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
            "hunt",
            "fish",
            "mine",
            "beg",
            "collect",
            "sell",
            "trade",
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

        # Gifts sent (negative) vs received (positive)
        gifts_given = (
            await session.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.user_id == user_id,
                    Transaction.type == "gift",
                    Transaction.amount < 0,
                )
            )
        ).scalar() or 0
        gifts_received = (
            await session.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.user_id == user_id,
                    Transaction.type == "gift",
                    Transaction.amount > 0,
                )
            )
        ).scalar() or 0

        deposited_total = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.user_id == user_id, Transaction.type == "deposit"
                )
            )
        ).scalar() or 0

        total_earned = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.user_id == user_id, Transaction.amount > 0
                )
            )
        ).scalar() or 0

        return {
            **counts,
            "max_gamble": max_gamble,
            "gifts_given": gifts_given,
            "gifts_received": gifts_received,
            "deposited_total": deposited_total,
            "total_earned": total_earned,
            "outdoorsman_count": sum(
                1 for key in ("hunt", "fish", "mine") if counts.get(f"{key}_count", 0) >= 1
            ),
            "streak": wallet.daily_streak or 0,
            "prestige": wallet.prestige or 0,
            "distinct_items": await ItemService.distinct_item_count(session, user_id),
            "bank": wallet.bank or 0,
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
