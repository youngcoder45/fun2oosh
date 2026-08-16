"""
Item model: the shop catalog.

Items are synced from `data/config.json` on startup (and on
`!reloadconfig`) and can be managed by server admins via the
`!shopadd` / `!shopremove` commands.
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
    giveable: Mapped[bool] = mapped_column(
        Boolean, default=True
    )  # can be gifted / traded to other users
    consumable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Custom messages (config.json) — None falls back to the built-in text.
    # Placeholders: {item}, {qty}, {amount}, {user}, {sender}.
    bought_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    used_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consumed_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # shown by !eat (food-style), random pick from a list
    gave_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sold_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # shown by !sell, random pick from a list
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
