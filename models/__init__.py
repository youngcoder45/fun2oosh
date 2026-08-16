"""Database models for fun2oosh bot."""

from sqlalchemy.ext.asyncio import AsyncSession

from .active_booster import ActiveBooster
from .audit_log import AuditLog
from .base import Base, utcnow
from .bet import Bet
from .guild_config import GuildConfig
from .inventory import InventoryItem
from .item import Item
from .role_claim import RoleClaim
from .role_income import RoleIncome
from .transaction import Transaction
from .user import User
from .user_achievement import UserAchievement
from .wallet import Wallet

# Type aliases for convenience
Session = AsyncSession

__all__ = [
    'Base',
    'utcnow',
    'User',
    'Wallet',
    'Transaction',
    'Bet',
    'Item',
    'RoleIncome',
    'RoleClaim',
    'InventoryItem',
    'GuildConfig',
    'AuditLog',
    'UserAchievement',
    'ActiveBooster',
    'Session',
]
