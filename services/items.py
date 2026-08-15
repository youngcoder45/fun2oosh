"""
Item service: catalog seeding, inventory management, item usage effects,
crate/lootbox rolling, and in-memory money boosters.

Inventory mutations always happen under the owning user's lock.
"""

import json
import logging
import random
import time
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import InventoryItem, Item, utcnow
from utils.economy_utils import EconomyUtils

from .locks import lock_manager

logger = logging.getLogger(__name__)

ITEM_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "items.json"


class BoosterManager:
    """In-memory money boosters (lost on restart -> acceptable for v1)."""

    def __init__(self) -> None:
        self._boosters: Dict[int, Dict[str, Tuple[float, float]]] = {}

    def set(
        self,
        user_id: int,
        booster_type: str,
        multiplier: float,
        duration_seconds: float,
    ) -> None:
        self._boosters.setdefault(user_id, {})[booster_type] = (
            multiplier,
            time.monotonic() + duration_seconds,
        )

    def get_multiplier(self, user_id: int, booster_type: str = "all") -> float:
        multiplier = 1.0
        for btype, (mult, expires) in self._boosters.get(user_id, {}).items():
            if expires <= time.monotonic():
                continue
            if btype == booster_type or btype == "all":
                multiplier = max(multiplier, mult)
        return multiplier

    def clear(self, user_id: Optional[int] = None) -> None:
        if user_id is None:
            self._boosters.clear()
        else:
            self._boosters.pop(user_id, None)


booster_manager = BoosterManager()


class ItemService:
    """Shop catalog + inventory operations."""

    # ------------------------------------------------------------------ catalog

    @staticmethod
    async def seed(session: AsyncSession) -> int:
        """Seed the item catalog from ``data/items.json`` if the table is empty."""
        existing = (await session.execute(select(func.count(Item.id)))).scalar() or 0
        if existing > 0:
            return 0
        if not ITEM_DATA_PATH.exists():
            logger.warning("Item catalog %s not found -> skipping seed.", ITEM_DATA_PATH)
            return 0
        with ITEM_DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        for row in data:
            session.add(
                Item(
                    id=row["id"],
                    name=row["name"],
                    description=row.get("description"),
                    price=row.get("price", 0),
                    sell_price=row.get("sell_price", 0),
                    category=row.get("category", "misc"),
                    stackable=row.get("stackable", True),
                    consumable=row.get("consumable", False),
                    limited=row.get("limited", False),
                    rarity=row.get("rarity", "common"),
                    emoji=row.get("emoji", ""),  # ???
                    max_stack=row.get("max_stack", 99),
                    expires_in=row.get("expires_in"),
                    effects=json.dumps(row["effects"]) if row.get("effects") else None,
                )
            )
        await session.commit()
        logger.info("Seeded %d items from %s", len(data), ITEM_DATA_PATH.name)
        return len(data)

    @staticmethod
    async def get(session: AsyncSession, item_id: str) -> Optional[Item]:
        return (await session.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()

    @staticmethod
    async def get_all(session: AsyncSession, include_limited: bool = False) -> List[Item]:
        stmt = select(Item).order_by(Item.category, Item.price)
        if not include_limited:
            stmt = stmt.where(Item.limited == False)  # noqa: E712
        return list((await session.execute(stmt)).scalars())

    # ----------------------------------------------------------------- inventory

    @staticmethod
    async def _get_inv(
        session: AsyncSession, user_id: int, item_id: str
    ) -> Optional[InventoryItem]:
        return (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.user_id == user_id, InventoryItem.item_id == item_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def count(session: AsyncSession, user_id: int, item_id: str) -> int:
        total = (
            await session.execute(
                select(func.sum(InventoryItem.quantity)).where(
                    InventoryItem.user_id == user_id, InventoryItem.item_id == item_id
                )
            )
        ).scalar()
        return total or 0

    @staticmethod
    async def grant(session: AsyncSession, user_id: int, item: Item, qty: int = 1) -> bool:
        """Grant ``qty`` of an item to a user (handles stacking + expiry)."""
        if qty <= 0:
            return False
        async with lock_manager.for_user(user_id):
            await ItemService._grant_raw(session, user_id, item, qty)
            await session.commit()
            return True

    @staticmethod
    async def _grant_raw(session: AsyncSession, user_id: int, item: Item, qty: int) -> None:
        """Grant without locking -> callers must hold the user lock."""
        inv = await ItemService._get_inv(session, user_id, item.id)
        expires_at = None
        if item.expires_in:
            expires_at = utcnow() + timedelta(seconds=item.expires_in)
        if inv is not None and item.stackable:
            inv.quantity += qty
            if expires_at:
                inv.expires_at = expires_at
        else:
            session.add(
                InventoryItem(
                    user_id=user_id,
                    item_id=item.id,
                    quantity=qty if item.stackable else 1,
                    expires_at=expires_at,
                )
            )

    @staticmethod
    async def consume(session: AsyncSession, user_id: int, item_id: str, qty: int = 1) -> bool:
        """Remove ``qty`` of an item from inventory. Returns False if insufficient."""
        if qty <= 0:
            return False
        async with lock_manager.for_user(user_id):
            inv = await ItemService._get_inv(session, user_id, item_id)
            if inv is None or inv.quantity < qty:
                return False
            inv.quantity -= qty
            if inv.quantity <= 0:
                await session.delete(inv)
            await session.commit()
            return True

    @staticmethod
    async def list_inventory(
        session: AsyncSession, user_id: int
    ) -> List[Tuple[InventoryItem, Item]]:
        """Return (inventory_row, item) pairs for a user, newest first."""
        rows = (
            await session.execute(
                select(InventoryItem, Item)
                .join(Item, Item.id == InventoryItem.item_id)
                .where(InventoryItem.user_id == user_id)
                .order_by(InventoryItem.acquired_at.desc())
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    async def distinct_item_count(session: AsyncSession, user_id: int) -> int:
        return len(await ItemService.list_inventory(session, user_id))

    @staticmethod
    async def tool_multiplier(session: AsyncSession, user_id: int, tool: str) -> float:
        """Return the best reward multiplier from owned tools (e.g. 'fish')."""
        multiplier = 1.0
        for _, item in await ItemService.list_inventory(session, user_id):
            if not item.effects:
                continue
            try:
                effects = json.loads(item.effects)
            except json.JSONDecodeError:
                continue
            if effects.get("tool") == tool:
                multiplier = max(multiplier, float(effects.get("multiplier", 1.5)))
        return multiplier

    @staticmethod
    async def inventory_value(session: AsyncSession, user_id: int) -> int:
        """Total buy-price value of all inventory items."""
        total = 0
        for inv, item in await ItemService.list_inventory(session, user_id):
            total += item.price * inv.quantity
        return total

    # ------------------------------------------------------------------ usage

    @staticmethod
    async def transfer(
        session: AsyncSession, from_id: int, to_id: int, item: Item, qty: int = 1
    ) -> bool:
        """Move items between users atomically (both users locked)."""
        if qty <= 0 or from_id == to_id:
            return False
        async with lock_manager.for_users(from_id, to_id):
            sender = await ItemService._get_inv(session, from_id, item.id)
            if sender is None or sender.quantity < qty:
                return False
            sender.quantity -= qty
            if sender.quantity <= 0:
                await session.delete(sender)
            await ItemService._grant_raw(session, to_id, item, qty)
            await session.commit()
            return True

    @staticmethod
    async def use_item(session: AsyncSession, user_id: int, item: Item) -> Tuple[bool, str]:
        """Consume one unit of a consumable item and apply its effects.

        Returns ``(success, message)``. Runs entirely under the user lock.
        """
        if not item.consumable:
            return False, "That item can't be used."

        effects = {}
        if item.effects:
            try:
                effects = json.loads(item.effects)
            except json.JSONDecodeError:
                effects = {}

        async with lock_manager.for_user(user_id):
            inv = await ItemService._get_inv(session, user_id, item.id)
            if inv is None or inv.quantity < 1:
                return False, "You don't have that item."

            # --- apply effects -------------------------------------------
            if "money_min" in effects:
                amount = random.randint(effects["money_min"], effects["money_max"])
                await EconomyUtils.add_money(session, user_id, amount, "item", f"Used {item.name}")
                msg = f"You used **{item.name}** and got **{amount:,} 💎️**!"

            elif "booster" in effects:
                booster = effects["booster"]
                mult = booster.get("multiplier", 2.0)
                duration = booster.get("duration", 3600)
                booster_manager.set(user_id, booster.get("type", "all"), mult, duration)
                msg = (
                    f"You activated a **{mult}x** money booster for "
                    f"**{duration / 60:.0f} minutes**!"
                )

            elif "crate" in effects:
                msg = await ItemService._open_crate_raw(session, user_id, item, effects)

            else:
                return False, "This item has no usable effect."

            inv.quantity -= 1
            if inv.quantity <= 0:
                await session.delete(inv)
            await session.commit()
            return True, msg

    @staticmethod
    async def _open_crate_raw(
        session: AsyncSession, user_id: int, item: Item, effects: dict
    ) -> str:
        """Roll crate rewards (caller holds the user lock)."""
        lines: List[str] = []
        money_gained = 0
        if random.random() < effects.get("money_chance", 0.7):
            money_gained = random.randint(
                effects.get("money_min", 100), effects.get("money_max", 500)
            )
            await EconomyUtils.add_money(
                session, user_id, money_gained, "crate", f"Opened {item.name}"
            )
            lines.append(f"**{money_gained:,} 💎️**")

        for slot in effects.get("items", []):
            if random.random() < slot.get("chance", 0.3):
                reward_item = await ItemService.get(session, slot["id"])
                if reward_item:
                    qty = slot.get("qty", 1)
                    await ItemService._grant_raw(session, user_id, reward_item, qty)
                    lines.append(f"**{reward_item.name}** x{qty}")

        if not lines:
            lines.append("...empty! Better luck next time.")
        return f"You opened **{item.name}**!\n" + "\n".join(f"• {line}" for line in lines)
