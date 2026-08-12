"""
Inventory model: per-user item ownership.

Stackable items are merged into a single row keyed on (user_id, item_id);
non-stackable items get individual rows so durability/expiration metadata
can be tracked per instance.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class InventoryItem(Base):
    """An item owned by a user."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    item_id: Mapped[str] = mapped_column(String(50), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    durability: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # remaining uses (None = unlimited)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    extra_data: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON string for extra state

    def __repr__(self) -> str:
        return f"<InventoryItem(user_id={self.user_id}, item_id='{self.item_id}', qty={self.quantity})>"
