"""
Shop & inventory cog.

Commands: shop, buy, sell, use, inventory, giveitem, trade, iteminfo.
"""

import time
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import Fun2OoshBot
from models import Transaction
from services.items import ItemService
from services.locks import lock_manager
from services.progression import AchievementService
from utils.config import Config
from utils.economy_utils import EconomyUtils
from utils.helpers import EmbedBuilder, format_coins
from utils.pagination import PaginationView

RARITY_COLORS = {
    "common": 0x9C9C9C,
    "uncommon": 0x3CB371,
    "rare": 0x4169E1,
    "epic": 0x9932CC,
    "legendary": 0xFFD700,
}


class TradeOffer:
    __slots__ = ("initiator", "partner", "item_id", "qty", "at")

    def __init__(self, initiator: int, partner: int, item_id: str, qty: int):
        self.initiator = initiator
        self.partner = partner
        self.item_id = item_id
        self.qty = qty
        self.at = time.monotonic()


class Shop(commands.Cog):
    """Item shop, inventory, and trading."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config
        self.trades: List[TradeOffer] = []

    # ------------------------------------------------------------ catalog UI

    def _item_embed(self, item) -> discord.Embed:
        embed = discord.Embed(
            title=item.name,
            description=item.description or "*No description.*",
            color=RARITY_COLORS.get(item.rarity, 0x2F3136),
        )
        embed.add_field(name="Category", value=item.category.title(), inline=True)
        embed.add_field(name="Rarity", value=item.rarity.title(), inline=True)
        embed.add_field(name="Price", value=format_coins(item.price), inline=True)
        embed.add_field(name="Sell Price", value=format_coins(item.sell_price), inline=True)
        embed.add_field(name="Stackable", value="" if item.stackable else "", inline=True)
        embed.add_field(name="Usable", value="" if item.consumable else "", inline=True)
        embed.set_footer(text=f"Item ID: {item.id} • Buy with: !buy {item.id} [qty]")
        return embed

    async def _shop_pages(self, session, category: Optional[str]) -> List[discord.Embed]:
        items = await ItemService.get_all(session, include_limited=False)
        if category and category != "all":
            items = [i for i in items if i.category == category]

        if not items:
            embed = discord.Embed(
                title="Shop", description="No items available in this category.", color=0x2F3136
            )
            return [embed]

        pages: List[discord.Embed] = []
        per_page = 8
        for start in range(0, len(items), per_page):
            chunk = items[start:start + per_page]
            embed = discord.Embed(
                title="Shop Catalog",
                description=f"Category: **{category or 'all'}** • `!buy <id> [qty]`",
                color=0x2F3136,
            )
            for item in chunk:
                embed.add_field(
                    name=item.name,
                    value=f"`{item.id}` — {format_coins(item.price)} ({item.rarity.title()})",
                    inline=False,
                )
            embed.set_footer(
                text=f"Page {start // per_page + 1}/{ (len(items) - 1) // per_page + 1}"
            )
            pages.append(embed)
        return pages

    @commands.hybrid_command(name="shop", aliases=["store"], description="Browse the item shop")
    @app_commands.describe(category="Filter by category (tool, consumable, booster, crate, collectible, all)")
    async def shop(self, ctx: commands.Context, category: Optional[str] = None):
        """Browse the item shop."""
        async with self.bot.get_session() as session:
            pages = await self._shop_pages(session, category)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @commands.hybrid_command(name="iteminfo", aliases=["item"], description="View item details")
    @app_commands.describe(item_id="The item ID to inspect")
    async def iteminfo(self, ctx: commands.Context, item_id: str):
        """View details about an item."""
        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
        if item is None:
            await ctx.send(f"Unknown item `{item_id}`. Use `!shop` to see the catalog.")
            return
        await ctx.send(embed=self._item_embed(item))

    # -------------------------------------------------------------- purchase

    @commands.hybrid_command(name="buy", description="Buy an item from the shop")
    @app_commands.describe(item_id="The item ID to buy", qty="Quantity (default 1)")
    async def buy(self, ctx: commands.Context, item_id: str, qty: int = 1):
        """Buy an item from the shop."""
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")
        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            if item.limited:
                return await ctx.send(f"**{item.name}** is a limited item and cannot be bought.")
            if item.price <= 0:
                return await ctx.send(f"**{item.name}** is not for sale.")
            if not item.stackable and qty != 1:
                return await ctx.send("That item is not stackable — quantity must be 1.")

            total = item.price * qty

            async with lock_manager.for_user(ctx.author.id):
                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                if wallet.balance < total:
                    return await ctx.send(
                        f"You need {format_coins(total)} but only have {format_coins(wallet.balance)}."
                    )
                wallet.balance -= total
                await ItemService._grant_raw(session, ctx.author.id, item, qty)
                session.add(
                    Transaction(
                        user_id=ctx.author.id,
                        type='buy',
                        amount=-total,
                        description=f'Bought {item.name} x{qty}',
                    )
                )
                await session.commit()
                embed = EmbedBuilder.success_embed(
                    "Purchase Complete!",
                    f"You bought **{item.name}** x{qty} for {format_coins(total)}.\n"
                    f"New balance: {format_coins(wallet.balance)}",
                )
                await ctx.send(embed=embed)

            new = await AchievementService.check(session, ctx.author.id, 'buy')
            await self._announce_achievements(ctx, new)

    @commands.hybrid_command(name="sell", description="Sell an item from your inventory")
    @app_commands.describe(item_id="The item ID to sell", qty="Quantity (default 1)")
    async def sell(self, ctx: commands.Context, item_id: str, qty: int = 1):
        """Sell items back for coins."""
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")
        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            if item.sell_price <= 0:
                return await ctx.send(f"**{item.name}** cannot be sold.")

            async with lock_manager.for_user(ctx.author.id):
                inv = await ItemService._get_inv(session, ctx.author.id, item.id)
                if inv is None or inv.quantity < qty:
                    return await ctx.send(f"You don't have {qty}x **{item.name}**.")

                total = item.sell_price * qty
                inv.quantity -= qty
                if inv.quantity <= 0:
                    await session.delete(inv)
                await EconomyUtils.add_money(
                    session, ctx.author.id, total, 'sell', f'Sold {item.name} x{qty}'
                )
                await session.commit()

            embed = EmbedBuilder.success_embed(
                "Sold!",
                f"You sold **{item.name}** x{qty} for {format_coins(total)}.",
            )
            await ctx.send(embed=embed)

    # ---------------------------------------------------------------- usage

    @commands.hybrid_command(name="use", description="Use a consumable item")
    @app_commands.describe(item_id="The item ID to use")
    async def use(self, ctx: commands.Context, item_id: str):
        """Use a consumable item (money items, boosters, crates)."""
        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            ok, msg = await ItemService.use_item(session, ctx.author.id, item)
            if not ok:
                return await ctx.send(msg)
            embed = EmbedBuilder.success_embed(f"{item.name}", msg)
            await ctx.send(embed=embed)

            new = await AchievementService.check(session, ctx.author.id, 'use')
            await self._announce_achievements(ctx, new)

    # ------------------------------------------------------------- inventory

    @commands.hybrid_command(name="inventory", aliases=["inv"], description="View your inventory")
    @app_commands.describe(user="View another user's inventory")
    async def inventory(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """View your (or another user's) inventory."""
        target = user or ctx.author
        async with self.bot.get_session() as session:
            rows = await ItemService.list_inventory(session, target.id)

        if not rows:
            embed = discord.Embed(
                title=f"{target.display_name}'s Inventory",
                description="Empty! Buy items with `!shop` / `!buy`.",
                color=0x2F3136,
            )
            return await ctx.send(embed=embed)

        pages: List[discord.Embed] = []
        per_page = 8
        for start in range(0, len(rows), per_page):
            embed = discord.Embed(
                title=f"{target.display_name}'s Inventory",
                color=0x2F3136,
            )
            for inv, item in rows[start:start + per_page]:
                embed.add_field(
                    name=item.name,
                    value=(
                        f"Qty: **{inv.quantity}** • Sell: {format_coins(item.sell_price)} "
                        f"• `{item.id}`"
                    ),
                    inline=False,
                )
            embed.set_footer(text=f"Page {start // per_page + 1}/{ (len(rows) - 1) // per_page + 1}")
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ------------------------------------------------------------------ gift

    @commands.command(name='giveitem', aliases=['giftitem'])
    async def giveitem(self, ctx: commands.Context, user: discord.User, item_id: str, qty: int = 1):
        """Give items to another user."""
        if user == ctx.author:
            return await ctx.send("You can't give items to yourself.")
        if user.bot:
            return await ctx.send("You can't give items to bots.")
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")

        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")

            ok = await ItemService.transfer(session, ctx.author.id, user.id, item, qty)
            if not ok:
                return await ctx.send(f"You don't have {qty}x **{item.name}**.")

            embed = EmbedBuilder.success_embed(
                "Item Gifted!",
                f"You gave **{item.name}** x{qty} to {user.mention}.",
            )
            await ctx.send(embed=embed)

    # ----------------------------------------------------------------- trade

    def _pending_offer(self, initiator: int, partner: int) -> Optional[TradeOffer]:
        now = time.monotonic()
        # Drop expired offers (rebuild to avoid mutating while iterating)
        self.trades = [offer for offer in self.trades if now - offer.at <= 60]
        for offer in self.trades:
            if offer.initiator == initiator and offer.partner == partner:
                return offer
        return None

    @commands.command(name='trade', aliases=['exchange'])
    async def trade(self, ctx: commands.Context, user: discord.User, item_id: str, qty: int = 1):
        """Trade items with another user.

        You offer an item; the other user replies with their own
        `!trade @you <item> <qty>` to complete the exchange.
        """
        if user == ctx.author:
            return await ctx.send("You can't trade with yourself.")
        if user.bot:
            return await ctx.send("You can't trade with bots.")
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")

        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            have = await ItemService.count(session, ctx.author.id, item.id)
            if have < qty:
                return await ctx.send(f"You only have {have}x **{item.name}**.")

            # Complete a pending trade from the partner?
            pending = self._pending_offer(user.id, ctx.author.id)
            if pending:
                partner_item = await ItemService.get(session, pending.item_id)
                if partner_item is None:
                    self.trades.remove(pending)
                    return await ctx.send("The other offer's item no longer exists.")
                if pending.item_id == item.id:
                    return await ctx.send("Both offers can't be the same item.")

                # swap both directions atomically
                async with lock_manager.for_users(ctx.author.id, user.id):
                    my_inv = await ItemService._get_inv(session, ctx.author.id, item.id)
                    their_inv = await ItemService._get_inv(session, user.id, pending.item_id)
                    if my_inv is None or my_inv.quantity < qty:
                        return await ctx.send("You no longer have that item.")
                    if their_inv is None or their_inv.quantity < pending.qty:
                        return await ctx.send("The other user no longer has their item.")

                    my_inv.quantity -= qty
                    if my_inv.quantity <= 0:
                        await session.delete(my_inv)
                    their_inv.quantity -= pending.qty
                    if their_inv.quantity <= 0:
                        await session.delete(their_inv)

                    await ItemService._grant_raw(session, user.id, item, qty)
                    await ItemService._grant_raw(session, ctx.author.id, partner_item, pending.qty)
                    await session.commit()

                self.trades.remove(pending)
                embed = EmbedBuilder.success_embed(
                    "Trade Complete!",
                    f"You gave **{item.name}** x{qty} to {user.mention} "
                    f"and received **{partner_item.name}** x{pending.qty}.",
                )
                return await ctx.send(embed=embed)

            # Register a new offer
            self.trades.append(TradeOffer(ctx.author.id, user.id, item.id, qty))
            await ctx.send(
                f"**Trade offer sent!** You offered **{item.name}** x{qty} "
                f"to {user.mention}.\n"
                f"To accept, {user.display_name} should run:\n"
                f"`{ctx.prefix}trade {ctx.author.mention} <their-item> <qty>`\n"
                f"(offer expires in 60 seconds)"
            )

    # --------------------------------------------------------------- helpers

    @staticmethod
    async def _announce_achievements(ctx: commands.Context, new_achievements: List[dict]) -> None:
        if not new_achievements:
            return
        lines = "\n".join(
            f"**{a['name']}** — {a['desc']}" for a in new_achievements
        )
        embed = discord.Embed(
            title="Achievements Unlocked!",
            description=lines,
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)


async def setup(bot: Fun2OoshBot):
    """Setup the shop cog."""
    await bot.add_cog(Shop(bot, bot.config))
