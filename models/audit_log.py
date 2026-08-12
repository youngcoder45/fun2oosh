"""
Audit log model — records admin/economy actions for accountability.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class AuditLog(Base):
    """A single audited action."""

    __tablename__ = 'audit_logs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    actor_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True) # admin who acted
    action: Mapped[str] = mapped_column(String(64), nullable=False) # e.g. 'add_money', 'shop_add'
    target_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True) # affected user (if any)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # free-form description
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action='{self.action}')>"
