"""
Economy service — the single, lock-aware entry point for money operations.

All methods acquire per-user locks internally so callers cannot corrupt
balances through concurrent commands. Money is only ever mutated through this
service or through `EconomyUtils` while holding the same locks.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from models import GuildConfig, Transaction, Wallet
from utils.economy_utils import EconomyUtils

from .items import booster_manager
from .locks import lock_manager

logger = logging.getLogger(__name__)


class EconomyService:
    """Transaction-safe money operations."""

    @staticmethod
    async def add(
        session: AsyncSession,
        user_id: int,
        amount: int,
        type_: str,
        description: str = "",
        game: Optional[str] = None,
    ) -> bool:
        """Add coins to a user's wallet and record a transaction."""
        if amount <= 0:
            return False
        async with lock_manager.for_user(user_id):
            ok = await EconomyUtils.add_money(session, user_id, amount, type_, description, game)
            if ok:
                await session.commit()
            return ok

    @staticmethod
    async def reward(
        session: AsyncSession,
        user_id: int,
        base_amount: int,
        type_: str,
        description: str = "",
        game: Optional[str] = None,
    ) -> int:
        """Add a reward scaled by active money boosters. Returns the final amount."""
        if base_amount <= 0:
            return 0
        amount = int(base_amount * booster_manager.get_multiplier(user_id))
        await EconomyService.add(session, user_id, amount, type_, description, game)
        return amount

    @staticmethod
    async def subtract(
        session: AsyncSession,
        user_id: int,
        amount: int,
        type_: str,
        description: str = "",
        game: Optional[str] = None,
    ) -> bool:
        """Subtract coins from a user's wallet (fails if insufficient)."""
        if amount <= 0:
            return False
        async with lock_manager.for_user(user_id):
            ok = await EconomyUtils.subtract_money(session, user_id, amount, type_, description, game)
            if ok:
                await session.commit()
            return ok

    @staticmethod
    async def transfer(
        session: AsyncSession,
        sender_id: int,
        receiver_id: int,
        amount: int,
        description: str = "",
        tax_rate: float = 0.0,
    ) -> Tuple[bool, int]:
        """Transfer coins between users, optionally applying a guild tax.

        Returns ``(success, tax_paid)``. Both users are locked; the transfer
        is committed atomically.
        """
        if amount <= 0 or sender_id == receiver_id:
            return False, 0
        if tax_rate < 0:
            tax_rate = 0.0
        elif tax_rate > 0.5:
            tax_rate = 0.5

        async with lock_manager.for_users(sender_id, receiver_id):
            sender = await EconomyUtils.get_wallet(session, sender_id)
            if sender is None or sender.balance < amount:
                return False, 0

            receiver = await EconomyUtils.get_or_create_wallet(session, receiver_id)

            tax = int(amount * tax_rate)
            received = amount - tax

            sender.balance -= amount
            receiver.balance += received

            tax_note = f"(incl. {int(tax_rate * 100)}% tax: {tax} coins)" if tax else ""
            session.add(
                Transaction(
                    user_id=sender_id,
                    type='transfer_out',
                    amount=-amount,
                    description=f"{description} → {receiver_id}{tax_note}",
                    recipient_id=receiver_id,
                )
            )
            session.add(
                Transaction(
                    user_id=receiver_id,
                    type='transfer_in',
                    amount=received,
                    description=f"{description} ← {sender_id}",
                    recipient_id=sender_id,
                )
            )

            await session.commit()
            return True, tax

    @staticmethod
    def networth(wallet: Wallet, inventory_value: int = 0) -> int:
        """Total wealth: wallet + bank + inventory resale value."""
        return (wallet.balance or 0) + (wallet.bank or 0) + inventory_value


class GuardService:
    """Anti-abuse guards (anti-alt, etc.)."""

    @staticmethod
    def account_age_days(member) -> int:
        if member is None or member.created_at is None:
            return 999
        return max(0, (datetime.now(timezone.utc) - member.created_at).days)

    @staticmethod
    def check_user_allowed(member, guild_cfg: Optional[GuildConfig]) -> Optional[str]:
        """Return an error message if the member should be blocked, else None."""
        if member is None or member.bot or guild_cfg is None:
            return None
        if guild_cfg.anti_alt:
            age = GuardService.account_age_days(member)
            if age < guild_cfg.min_account_age_days:
                return (
                    f"Anti-alt protection: your Discord account is only"
                    f"**{age} day(s)** old. Minimum required: "
                    f"**{guild_cfg.min_account_age_days} day(s)**."
                )
        return None
