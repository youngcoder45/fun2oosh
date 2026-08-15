"""
Item service: catalog sync, inventory management, item usage effects,
crate/lootbox rolling, and in-memory money boosters.

Inventory mutations always happen under the owning user's lock.
"""

import json
import logging
import random
import time
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import discord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import InventoryItem, Item, utcnow
from utils.economy_utils import EconomyUtils
from utils.runtime_config import items as config_items

from .locks import lock_manager

logger = logging.getLogger(__name__)


def _message_field(value) -> Optional[str]:
    """Normalize a config message field for storage.

    Single strings pass through; lists of strings (random message sets) are
    stored as JSON so every entry survives into the database. ``None`` stays
    ``None``.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps([entry for entry in value if isinstance(entry, str)])
    return value


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
        """Sync the catalog from ``data/config.json`` (upsert by item id).

        Every item defined in config is inserted or updated, so editing
        ``data/config.json`` and restarting (or running ``!reloadconfig``)
        applies the new catalog. Items added at runtime via ``!shopadd`` but
        not present in config are left untouched.
        """
        rows = config_items()
        if not rows:
            logger.warning("No shop items in config -> skipping catalog sync.")
            return 0
        count = 0
        for row in rows:
            item_id = row.get("id")
            if not item_id:
                continue
            fields = dict(
                name=row.get("name", item_id),
                description=row.get("description"),
                price=row.get("price", 0),
                sell_price=row.get("sell_price", 0),
                category=row.get("category", "misc"),
                stackable=row.get("stackable", True),
                giveable=row.get("giveable", True),
                consumable=row.get("consumable", False),
                limited=row.get("limited", False),
                rarity=row.get("rarity", "common"),
                emoji=row.get("emoji", ""),
                max_stack=row.get("max_stack", 99),
                expires_in=row.get("expires_in"),
                effects=json.dumps(row["effects"]) if row.get("effects") else None,
                bought_message=_message_field(row.get("bought_message")),
                used_message=_message_field(row.get("used_message")),
                gave_message=_message_field(row.get("gave_message")),
            )
            existing = await ItemService.get(session, item_id)
            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
            else:
                session.add(Item(id=item_id, **fields))
            count += 1
        await session.commit()
        logger.info("Synced %d items from config.", count)
        return count

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
    async def use_item(
        session: AsyncSession,
        user_id: int,
        item: Item,
        ctx=None,
    ) -> Tuple[bool, str]:
        """Consume one unit of a consumable item and apply its effects.

        ``ctx`` is required for effects that need a guild (e.g. adding a
        role). Returns ``(success, message)``. Runs entirely under the user
        lock; the item is only consumed after every effect succeeded.
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

            elif "role" in effects:
                role_ok, msg = await ItemService._apply_role_effect(
                    session, user_id, item, effects, ctx
                )
                if not role_ok:
                    return False, msg

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

            elif not effects:
                # Flavor item: no mechanical effect, just consume it. The
                # command layer replaces this with the item's used_message.
                msg = f"You used **{item.name}**."

            else:
                return False, "This item has no usable effect."

            inv.quantity -= 1
            if inv.quantity <= 0:
                await session.delete(inv)
            await session.commit()
            return True, msg

    @staticmethod
    async def _apply_role_effect(
        session: AsyncSession,
        user_id: int,
        item: Item,
        effects: dict,
        ctx,
    ) -> Tuple[bool, str]:
        """Grant the configured role from ``effects["role"]``.

        The role can be configured by name (applied per-guild) or by numeric
        role id. Returns ``(success, message)``; on failure the item is not
        consumed.
        """
        if ctx is None or getattr(ctx, "guild", None) is None:
            return False, "This item must be used in a server to assign its role."
        guild = ctx.guild
        member = guild.get_member(user_id)
        if member is None:
            return False, "You must be in this server to receive the role."

        role_spec = effects["role"]
        role = None
        if isinstance(role_spec, int):
            role = guild.get_role(role_spec)
        else:
            role = discord.utils.get(guild.roles, name=str(role_spec))
            if role is None and str(role_spec).isdigit():
                role = guild.get_role(int(role_spec))
        if role is None:
            return False, "The role configured for this item no longer exists in this server."
        try:
            await member.add_roles(role, reason=f"Used {item.name}")
        except discord.Forbidden:
            return False, "I don't have permission to assign that role."
        return True, f"You used **{item.name}** and received the **{role.name}** role!"

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
