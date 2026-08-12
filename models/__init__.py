"""
Database models for fun2oosh bot.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from .base import Base
from .bet import Bet
from .inventory import InventoryItem
from .item import Item
from .audit_log import AuditLog
from .guild_config import GuildConfig
from .transaction import Transaction
from .user import User
from .user_achievement import UserAchievement
from .wallet import Wallet


# Type aliases for convenience
Session = AsyncSession

__all__ = [
    'Base',
    'User',
    'Wallet',
    'Transaction',
    'Bet',
    'Item',
    'InventoryItem',
    'GuildConfig',
    'AuditLog',
    'UserAchievement',
    'Session',
]