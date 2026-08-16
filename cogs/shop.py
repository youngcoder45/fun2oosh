"""
Shop & inventory cog.

Commands: shop, buy, sell, use, eat, inventory, giveitem, trade, iteminfo.
"""

import time
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import Fun2OoshBot
from models import Item, Transaction
from services.items import ItemService
from services.locks import lock_manager
from services.progression import AchievementService
from utils.config import Config
from utils.economy_utils import EconomyUtils
from utils.helpers import (
    COLOR_INFO,
    EmbedBuilder,
    format_coins,
    render_item_message,
)
from utils.pagination import PaginationView

RARITY_COLORS = {
    "common": 0x9C9C9C,
    "uncommon": 0x00FF00,
    "rare": 0x4169E1,
    "epic": 0x9932CC,
    "legendary": 0xFFD700,
}


class ShopView(discord.ui.View):
    """Shop browsing: category dropdown + page navigation (Components V2)."""

    def __init__(
        self,
        pages: Dict[str, List[discord.Embed]],
        owner_id: int,
        categories: List[str],
    ):
        super().__init__(timeout=180)
        self.pages = pages
        self.owner_id = owner_id
        self.categories = categories
        self.category = "all"
        self.index = 0

        options = [discord.SelectOption(label="All", value="all")] + [
            discord.SelectOption(label=cat.title(), value=cat) for cat in categories
        ]
        self.category_select: discord.ui.Select = discord.ui.Select(
            placeholder="Filter by category",
            options=options,
            row=0,
        )
        self.category_select.callback = self._on_category  # type: ignore[method-assign]
        self.add_item(self.category_select)
        self._update()

    def _current(self) -> List[discord.Embed]:
        return self.pages[self.category]

    def _update(self) -> None:
        pages = self._current()
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "You can't interact with someone else's shop.", ephemeral=True
            )
            return False
        return True

    async def _on_category(self, interaction: discord.Interaction) -> None:
        self.category = self.category_select.values[0]
        self.index = 0
        self._update()
        await interaction.response.edit_message(embed=self._current()[0], view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update()
        await interaction.response.edit_message(embed=self._current()[self.index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self._current()) - 1, self.index + 1)
        self._update()
        await interaction.response.edit_message(embed=self._current()[self.index], view=self)


class TradeOffer:
    __slots__ = ("initiator", "partner", "item_id", "qty", "price", "at", "message", "view")

    def __init__(
        self, initiator: int, partner: int, item_id: str, qty: int, price: Optional[int] = None
    ):
        self.initiator = initiator
        self.partner = partner
        self.item_id = item_id
        self.qty = qty
        self.price = price  # coins per item; None = item-for-item trade
        self.at = time.monotonic()
        self.message: Optional[discord.Message] = None
        self.view: Optional[TradeOfferView] = None


class TradeOfferView(discord.ui.View):
    """Trade offer embed: Accept/Decline for the partner, Cancel for the sender."""

    def __init__(self, cog: "Shop", offer: TradeOffer):
        super().__init__(timeout=60)
        self.cog = cog
        self.offer = offer

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.offer.initiator, self.offer.partner):
            await interaction.response.send_message("This trade is not for you.", ephemeral=True)
            return False
        return True

    def disable_all(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]

    async def on_timeout(self) -> None:
        """Expire the offer: remove it and detach the buttons."""
        if self.offer in self.cog.trades:
            self.cog.trades.remove(self.offer)
        self.disable_all()
        if self.offer.message is not None and self.offer.message.embeds:
            embed = self.offer.message.embeds[0].copy()
            embed.description = f"{embed.description or ''}\n\n*Trade offer expired.*"
            try:
                await self.offer.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

    async def _resolve(self) -> bool:
        """True while the offer is still pending."""
        return self.offer in self.cog.trades

    async def _close(self, interaction: discord.Interaction, note: str) -> None:
        """Retract the offer, disable the buttons, and annotate the embed."""
        if self.offer in self.cog.trades:
            self.cog.trades.remove(self.offer)
        self.disable_all()
        embed = None
        if interaction.message is not None and interaction.message.embeds:
            embed = interaction.message.embeds[0].copy()
            embed.description = f"{embed.description or ''}\n\n*{note}*"
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def _fail_priced(
        self, interaction: discord.Interaction, offer: TradeOffer, note: str
    ) -> None:
        """Abort a priced offer (e.g. the item vanished): remove it and annotate."""
        if offer in self.cog.trades:
            self.cog.trades.remove(offer)
        self.disable_all()
        message = interaction.message
        embed = None
        if message is not None and message.embeds:
            embed = message.embeds[0].copy()
            embed.description = f"{embed.description or ''}\n\n*{note}*"
        if message is not None:
            try:
                await message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        await interaction.followup.send(note, ephemeral=True)
        self.stop()

    async def _complete_priced(self, interaction: discord.Interaction) -> None:
        """Complete a priced offer: the recipient pays ``price * qty`` coins.

        Runs atomically under both users' locks; the item only moves after
        the payment is guaranteed.
        """
        offer = self.offer
        price = offer.price
        if price is None:
            return
        total = price * offer.qty
        await interaction.response.defer()

        async with self.cog.bot.get_session() as session:
            item = await ItemService.get(session, offer.item_id)
            if item is None:
                return await self._fail_priced(interaction, offer, "The offered item no longer exists.")
            async with lock_manager.for_users(offer.initiator, offer.partner):
                sender_inv = await ItemService._get_inv(session, offer.initiator, item.id)
                if sender_inv is None or sender_inv.quantity < offer.qty:
                    return await self._fail_priced(
                        interaction, offer, "The seller no longer has that item."
                    )
                buyer_wallet = await EconomyUtils.get_or_create_wallet(session, offer.partner)
                if buyer_wallet.balance < total:
                    return await interaction.followup.send(
                        f"You need {format_coins(total)} but only have "
                        f"{format_coins(buyer_wallet.balance)}.",
                        ephemeral=True,
                    )
                seller_wallet = await EconomyUtils.get_or_create_wallet(session, offer.initiator)

                sender_inv.quantity -= offer.qty
                if sender_inv.quantity <= 0:
                    await session.delete(sender_inv)
                await ItemService._grant_raw(session, offer.partner, item, offer.qty)
                buyer_wallet.balance -= total
                seller_wallet.balance += total
                session.add(
                    Transaction(
                        user_id=offer.partner,
                        type="trade",
                        amount=-total,
                        description=f"Bought {item.name} x{offer.qty} from <@{offer.initiator}>",
                    )
                )
                session.add(
                    Transaction(
                        user_id=offer.initiator,
                        type="trade",
                        amount=total,
                        description=f"Traded {item.name} x{offer.qty} for coins",
                    )
                )
                await session.commit()

        if offer in self.cog.trades:
            self.cog.trades.remove(offer)
        self.disable_all()
        message = interaction.message
        embed = None
        if message is not None and message.embeds:
            embed = message.embeds[0].copy()
            embed.description = (
                f"{embed.description or ''}\n\n*Trade complete — {interaction.user.mention} "
                f"paid {format_coins(total)} for **{item.name}** x{offer.qty}.*"
            )
        if message is not None:
            try:
                await message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        self.stop()
        completion = EmbedBuilder.success_embed(
            "Trade Complete!",
            f"You paid {format_coins(total)} and received **{item.name}** x{offer.qty} "
            f"from <@{offer.initiator}>.",
        )
        await interaction.followup.send(embed=completion)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.secondary)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.offer.partner:
            return await interaction.response.send_message(
                "Only the offer recipient can accept.", ephemeral=True
            )
        if not await self._resolve():
            return await interaction.response.send_message(
                "This offer has expired.", ephemeral=True
            )
        if self.offer.price:
            return await self._complete_priced(interaction)
        await self._close(
            interaction,
            f"{interaction.user.mention} accepted. Send your item with "
            f"`!trade <@{self.offer.initiator}> <item> <qty>` to complete the exchange.",
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.offer.partner:
            return await interaction.response.send_message(
                "Only the offer recipient can decline.", ephemeral=True
            )
        if not await self._resolve():
            return await interaction.response.send_message(
                "This offer has expired.", ephemeral=True
            )
        await self._close(interaction, f"{interaction.user.mention} declined the trade offer.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.offer.initiator:
            return await interaction.response.send_message(
                "Only the offer sender can cancel.", ephemeral=True
            )
        if not await self._resolve():
            return await interaction.response.send_message(
                "This offer has expired.", ephemeral=True
            )
        await self._close(interaction, "Trade offer cancelled.")


class Shop(commands.Cog):
    """Item shop, inventory, and trading."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config
        self.trades: List[TradeOffer] = []

    # ------------------------------------------------------------ catalog UI

    def _item_embed(self, item, num: int = 0) -> discord.Embed:
        embed = discord.Embed(
            title=item.name,
            description=item.description or "*No description.*",
            color=RARITY_COLORS.get(item.rarity, COLOR_INFO),
        )
        embed.add_field(name="Category", value=item.category.title(), inline=True)
        embed.add_field(name="Rarity", value=item.rarity.title(), inline=True)
        embed.add_field(name="Price", value=format_coins(item.price), inline=True)
        embed.add_field(name="Sell Price", value=format_coins(item.sell_price), inline=True)
        embed.add_field(name="Stackable", value="Yes" if item.stackable else "No", inline=True)
        embed.add_field(name="Giveable", value="Yes" if item.giveable else "No", inline=True)
        embed.add_field(name="Usable", value="Yes" if item.consumable else "No", inline=True)
        num_text = f"{num:03d}" if num else item.id
        embed.set_footer(text=f"ID: {item.id} • #{num_text} • Buy with: !buy {num_text} [qty]")
        return embed

    def _shop_pages(
        self,
        items: List[Item],
        category: str = "all",
        positions: Optional[Dict[str, int]] = None,
    ) -> List[discord.Embed]:
        """Build shop pages showing each item's 3-digit catalog number."""
        if not items:
            return [EmbedBuilder.info_embed("Shop", "No items available in this category.")]

        pages: List[discord.Embed] = []
        per_page = 8
        for start in range(0, len(items), per_page):
            chunk = items[start : start + per_page]
            embed = discord.Embed(
                title="Shop Catalog",
                description=f"Category: **{category.title()}** • `!buy <001> [qty]`",
                color=COLOR_INFO,
            )
            for item in chunk:
                num = (positions or {}).get(item.id, 0)
                embed.add_field(
                    name=item.name,
                    value=f"`{num:03d}` - {format_coins(item.price)} ({item.rarity.title()})",
                    inline=False,
                )
            embed.set_footer(
                text=f"Page {start // per_page + 1}/{(len(items) - 1) // per_page + 1}"
            )
            pages.append(embed)
        return pages

    @commands.hybrid_command(name="shop", aliases=["store"], description="Browse the item shop")
    @app_commands.describe(
        category="Filter by category (tool, consumable, booster, crate, collectible, all)"
    )
    async def shop(self, ctx: commands.Context, category: Optional[str] = None):
        """Browse the item shop."""
        async with self.bot.get_session() as session:
            items = await ItemService.get_all(session, include_limited=False)
        categories = sorted({item.category for item in items})
        positions = {item.id: index for index, item in enumerate(items, start=1)}
        pages: Dict[str, List[discord.Embed]] = {"all": self._shop_pages(items, positions=positions)}
        for cat in categories:
            pages[cat] = self._shop_pages(
                [item for item in items if item.category == cat], cat, positions
            )

        view = ShopView(pages, owner_id=ctx.author.id, categories=categories)
        if category and category.lower() in pages:
            view.category = category.lower()
            view.index = 0
            view._update()
        await ctx.send(embed=view._current()[0], view=view)

    @commands.hybrid_command(name="iteminfo", aliases=["item"], description="View item details")
    @app_commands.describe(item_id="The item ID to inspect")
    async def iteminfo(self, ctx: commands.Context, item_id: str):
        """View details about an item."""
        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
            if item is None:
                await ctx.send(f"Unknown item `{item_id}`. Use `!shop` to see the catalog.")
                return
            num = await ItemService.position(session, item.id)
        await ctx.send(embed=self._item_embed(item, num))

    # -------------------------------------------------------------- purchase

    @commands.hybrid_command(name="buy", description="Buy an item from the shop")
    @app_commands.describe(item_id="The item ID to buy", qty="Quantity (default 1)")
    async def buy(self, ctx: commands.Context, item_id: str, qty: int = 1):
        """Buy an item from the shop."""
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")
        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            if item.limited:
                return await ctx.send(f"**{item.name}** is a limited item and cannot be bought.")
            if item.price <= 0:
                return await ctx.send(f"**{item.name}** is not for sale.")
            if not item.stackable and qty != 1:
                return await ctx.send("That item is not stackable, quantity must be 1.")

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
                        type="buy",
                        amount=-total,
                        description=f"Bought {item.name} x{qty}",
                    )
                )
                await session.commit()
                description = (
                    f"You bought **{item.name}** x{qty} for {format_coins(total)}.\n"
                    f"New balance: {format_coins(wallet.balance)}"
                )
                if item.bought_message:
                    description = (
                        render_item_message(
                            item.bought_message,
                            item=item.name,
                            qty=qty,
                            amount=format_coins(total),
                            user=ctx.author.display_name,
                        )
                        or description
                    )
                embed = EmbedBuilder.activity_embed(description, user=ctx.author)
                await ctx.send(embed=embed)

            new = await AchievementService.check(session, ctx.author.id, "buy")
            await self._announce_achievements(ctx, new)

    @commands.hybrid_command(name="sell", description="Sell an item from your inventory")
    @app_commands.describe(item_id="The item ID to sell", qty="Quantity (default 1)")
    async def sell(self, ctx: commands.Context, item_id: str, qty: int = 1):
        """Sell items back for 💎️."""
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")
        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
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
                    session, ctx.author.id, total, "sell", f"Sold {item.name} x{qty}"
                )
                await session.commit()

            description = f"You sold **{item.name}** x{qty} for {format_coins(total)}."
            if item.sold_message:
                description = (
                    render_item_message(
                        item.sold_message,
                        item=item.name,
                        qty=qty,
                        amount=format_coins(total),
                        user=ctx.author.display_name,
                        sender=ctx.author.display_name,
                    )
                    or description
                )
            embed = EmbedBuilder.success_embed("Sold!", description)
            await ctx.send(embed=embed)

            new = await AchievementService.check(session, ctx.author.id, "sell")
            await self._announce_achievements(ctx, new)

    # ---------------------------------------------------------------- usage

    @commands.hybrid_command(name="use", description="Use a consumable item")
    @app_commands.describe(item_id="The item ID to use")
    async def use(self, ctx: commands.Context, item_id: str):
        """Use a consumable item (money items, role items, boosters, crates)."""
        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            ok, msg, amount = await ItemService.use_item(session, ctx.author.id, item, ctx=ctx)
            if not ok:
                return await ctx.send(msg)
            if item.used_message:
                msg = (
                    self._render_message(
                        item.used_message, item=item, amount=amount, user=ctx.author
                    )
                    or msg
                )
            embed = EmbedBuilder.success_embed(f"{item.name}", msg)
            await ctx.send(embed=embed)

            new = await AchievementService.check(session, ctx.author.id, "use")
            await self._announce_achievements(ctx, new)

    @commands.hybrid_command(name="eat", description="Eat a consumable food item")
    @app_commands.describe(item_id="The item ID to eat")
    async def eat(self, ctx: commands.Context, item_id: str):
        """Eat a consumable food item (items with a ``consumed_message``).

        Eating consumes the item for flavor only — it never pays the item's
        coin effect (use `!use` for that).
        """
        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            if not item.consumable:
                return await ctx.send(f"**{item.name}** isn't something you can eat.")
            if not item.consumed_message:
                return await ctx.send(
                    f"**{item.name}** isn't edible — try `!use {item.id}` instead."
                )
            ok, msg, amount = await ItemService.use_item(
                session, ctx.author.id, item, ctx=ctx, grant_money=False
            )
            if not ok:
                return await ctx.send(msg)
            msg = self._render_message(
                item.consumed_message, item=item, amount=amount, user=ctx.author
            ) or msg
            embed = EmbedBuilder.success_embed(f"{item.name}", msg)
            await ctx.send(embed=embed)

            new = await AchievementService.check(session, ctx.author.id, "use")
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
            embed = EmbedBuilder.info_embed(
                f"{target.display_name}'s Inventory",
                "Empty! Buy items with `!shop` / `!buy`.",
            )
            return await ctx.send(embed=embed)

        pages: List[discord.Embed] = []
        per_page = 8
        for start in range(0, len(rows), per_page):
            embed = discord.Embed(
                title=f"{target.display_name}'s Inventory",
                color=COLOR_INFO,
            )
            for inv, item in rows[start : start + per_page]:
                embed.add_field(
                    name=item.name,
                    value=(
                        f"Qty: **{inv.quantity}** • Sell: {format_coins(item.sell_price)} "
                        f"• `{item.id}`"
                    ),
                    inline=False,
                )
            embed.set_footer(text=f"Page {start // per_page + 1}/{(len(rows) - 1) // per_page + 1}")
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ------------------------------------------------------------------ gift

    @commands.command(name="giveitem", aliases=["giftitem"])
    async def giveitem(self, ctx: commands.Context, user: discord.User, item_id: str, qty: int = 1):
        """Give items to another user."""
        if user == ctx.author:
            return await ctx.send("You can't give items to yourself.")
        if user.bot:
            return await ctx.send("You can't give items to bots.")
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")

        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            if not item.giveable:
                return await ctx.send(f"**{item.name}** cannot be given away.")

            ok = await ItemService.transfer(session, ctx.author.id, user.id, item, qty)
            if not ok:
                return await ctx.send(f"You don't have {qty}x **{item.name}**.")

            description = f"You gave **{item.name}** x{qty} to {user.mention}."
            if item.gave_message:
                description = (
                    render_item_message(
                        item.gave_message,
                        item=item.name,
                        qty=qty,
                        user=user.display_name,
                        sender=ctx.author.display_name,
                        user_mention=user.mention,
                        sender_mention=ctx.author.mention,
                    )
                    or description
                )
            embed = EmbedBuilder.activity_embed(description, user=ctx.author)
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

    @commands.command(name="trade", aliases=["exchange"])
    async def trade(
        self, ctx: commands.Context, user: discord.User, item_id: str, qty: int = 1, price: int = 0
    ):
        """Trade items with another user.

        You offer an item; the other user replies with their own
        `!trade @you <item> <qty>` to complete the exchange. Add a custom
        `price` (coins per item, e.g. `!trade @you rose 1 5`) to sell the
        item for coins instead — the other user presses **Accept** to pay.
        """
        if user == ctx.author:
            return await ctx.send("You can't trade with yourself.")
        if user.bot:
            return await ctx.send("You can't trade with bots.")
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")
        if price < 0:
            return await ctx.send("Price can't be negative.")

        async with self.bot.get_session() as session:
            item = await ItemService.resolve(session, item_id)
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            if not item.giveable:
                return await ctx.send(f"**{item.name}** cannot be traded.")
            have = await ItemService.count(session, ctx.author.id, item.id)
            if have < qty:
                return await ctx.send(f"You only have {have}x **{item.name}**.")

            # Complete a pending trade from the partner?
            pending = self._pending_offer(user.id, ctx.author.id)
            if pending:
                if pending.price:
                    return await ctx.send(
                        "That offer is a coin trade — press **Accept** on it to pay "
                        "and receive the item."
                    )
                partner_item = await ItemService.get(session, pending.item_id)
                if partner_item is None:
                    self.trades.remove(pending)
                    return await ctx.send("The other offer's item no longer exists.")
                if not partner_item.giveable:
                    self.trades.remove(pending)
                    return await ctx.send(f"**{partner_item.name}** cannot be traded.")
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
                if pending.view is not None:
                    pending.view.disable_all()
                if pending.message is not None:
                    await pending.message.edit(view=None)
                embed = EmbedBuilder.success_embed(
                    "Trade Complete!",
                    f"You gave **{item.name}** x{qty} to {user.mention} "
                    f"and received **{partner_item.name}** x{pending.qty}.",
                )
                return await ctx.send(embed=embed)

            # Register a new offer with interactive buttons
            offer = TradeOffer(ctx.author.id, user.id, item.id, qty, price=price or None)
            self.trades.append(offer)
            if price:
                total = price * qty
                embed = EmbedBuilder.info_embed(
                    "Trade Offer",
                    f"**{ctx.author.display_name}** offers **{item.name}** x{qty} for "
                    f"{format_coins(total)} to {user.mention}.\n"
                    f"{user.mention}, press **Accept** to pay and receive the item.",
                )
            else:
                embed = EmbedBuilder.info_embed(
                    "Trade Offer",
                    f"**{ctx.author.display_name}** offers **{item.name}** x{qty} to "
                    f"{user.mention}.\n"
                    f"Reply with `!trade @{ctx.author.display_name} <item> <qty>` to swap items.",
                )
            embed.set_footer(text="Offer expires in 60 seconds")
            view = TradeOfferView(self, offer)
            message = await ctx.send(embed=embed, view=view)
            offer.message = message
            offer.view = view

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _render_message(template: str, *, item: Item, amount, user) -> Optional[str]:
        """Render a per-item message template with the usual placeholders.

        ``{amount}`` is only passed when the item's effect actually granted
        coins, so templates without ``{amount}`` are untouched and templates
        with it don't render a literal ``None``.
        """
        values = {
            "item": item.name,
            "user": getattr(user, "display_name", None) or str(user),
        }
        if amount is not None:
            values["amount"] = format_coins(amount)
        return render_item_message(template, **values)

    @staticmethod
    async def _announce_achievements(ctx: commands.Context, new_achievements: List[dict]) -> None:
        if not new_achievements:
            return
        lines = "\n".join(f"**{a['name']}** - {a['desc']}" for a in new_achievements)
        embed = discord.Embed(
            title="Achievements Unlocked!",
            description=lines,
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)


async def setup(bot: Fun2OoshBot):
    """Setup the shop cog."""
    await bot.add_cog(Shop(bot, bot.config))
