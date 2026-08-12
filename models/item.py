"""
Item model: the shop catalog.

Items are seeded from `data/items.json` on startup and can be managed by
server admins via the `!shopadd` / `!shopremove` commands.
"""

from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Item(Base):
    """A purchasable / usable item definition."""

    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # e.g. 'fishing_rod'
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer, default=0)  # purchase price (0 = not purchasable)
    sell_price: Mapped[int] = mapped_column(Integer, default=0)  # resale value (0 = not sellable)
    category: Mapped[str] = mapped_column(
        String(32), default="misc"
    )  # tool|consumable|booster|crate|collectible
    stackable: Mapped[bool] = mapped_column(Boolean, default=True)
    consumable: Mapped[bool] = mapped_column(Boolean, default=False)
    limited: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # not sold in shop (crate/event only)
    rarity: Mapped[str] = mapped_column(
        String(16), default="common"
    )  # common|uncommon|rare|epic|legendary
    emoji: Mapped[str] = mapped_column(String(8), default="")
    max_stack: Mapped[int] = mapped_column(Integer, default=99)
    expires_in: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # seconds after acquire (0/None = never)
    effects: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON string of usage effects

    def __repr__(self) -> str:
        return f"<Item(id='{self.id}', name='{self.name}', price={self.price})>"
