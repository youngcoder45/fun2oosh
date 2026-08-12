"""
Admin commands cog.
"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import text

from bot import Fun2OoshBot
from services.guild import SETTINGS, AuditService, GuildConfigService
from services.items import ItemService
from utils.config import Config
from utils.economy_utils import EconomyUtils
from utils.helpers import EmbedBuilder, format_coins
from utils.pagination import PaginationView


class Admin(commands.Cog):
    """Admin commands."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config

    async def cog_check(self, ctx: commands.Context):
        """Allow owner or users with administrator permission."""
        if ctx.author.id == self.config.owner_id:
            return True
        if ctx.guild is not None:
            member = ctx.guild.get_member(ctx.author.id)
            if member and member.guild_permissions.administrator:
                return True
        return False

    @commands.command(name='add_money')
    async def add_money(self, ctx: commands.Context, user: discord.User, amount: int):
        """Add money to a user (admin only)."""
        async with self.bot.get_session() as session:
            success = await EconomyUtils.add_money(
                session, user.id, amount, 'admin', f'Admin added {amount} coins'
            )

            if success:
                await session.commit()
                await ctx.send(f"Added {amount} coins to {user.mention}.")
            else:
                await ctx.send("Failed to add money.")

    @app_commands.command(name='add_money', description='Add money to a user (admin only)')
    @app_commands.describe(user='User to add money to', amount='Amount to add')
    async def add_money_slash(self, interaction: discord.Interaction, user: discord.User, amount: int):
        """Slash command for adding money."""
        is_owner = interaction.user.id == self.config.owner_id
        is_admin = False
        if interaction.guild:
            member = interaction.guild.get_member(interaction.user.id)
            if member and member.guild_permissions.administrator:
                is_admin = True
        if not (is_owner or is_admin):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        async with self.bot.get_session() as session:
            success = await EconomyUtils.add_money(
                session, user.id, amount, 'admin', f'Admin added {amount} coins'
            )

            if success:
                await session.commit()
                await interaction.response.send_message(f"Added {amount} coins to {user.mention}.")
            else:
                await interaction.response.send_message("Failed to add money.")

    @commands.command(name='reset_economy')
    async def reset_economy(self, ctx: commands.Context, confirmation: str = ""):
        """Reset all economy data (admin only - dangerous).

        Usage: ^reset_economy CONFIRM
        This will delete ALL wallets, transactions, and bets. Use with extreme caution!
        """
        if confirmation.upper() != "CONFIRM":
            embed = EmbedBuilder.error_embed(
                "Dangerous Operation",
                "This command will **permanently delete** all economy data including:\n"
                "• All user wallets and balances\n"
                "• All transaction history\n"
                "• All bet records\n\n"
                f"To confirm, type: `{ctx.prefix}reset_economy CONFIRM`"
            )
            await ctx.send(embed=embed)
            return

        # Double confirmation with reaction
        embed = EmbedBuilder.error_embed(
            "FINAL WARNING",
            "You are about to **IRREVERSIBLY DELETE** all economy data!\n\n"
            "React with to proceed or to cancel."
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction("")
        await message.add_reaction("")

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ["", ""]
                and reaction.message.id == message.id
            )

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)

            if str(reaction.emoji) == "":
                await ctx.send("Economy reset cancelled.")
                return

            if str(reaction.emoji) == "":
                # Proceed with reset
                async with self.bot.get_session() as session:
                    # Delete all data in correct order (respecting foreign keys)
                    await session.execute(text("DELETE FROM bets"))
                    await session.execute(text("DELETE FROM transactions"))
                    await session.execute(text("DELETE FROM wallets"))
                    await session.commit()

                embed = EmbedBuilder.success_embed(
                    "Economy Reset Complete",
                    "All economy data has been permanently deleted and reset."
                )
                await ctx.send(embed=embed)

        except TimeoutError:
            await ctx.send("Economy reset timed out. Operation cancelled.")

    @app_commands.command(name='reset_economy', description='Reset all economy data (admin only - dangerous)')
    @app_commands.describe(confirmation='Type CONFIRM to proceed')
    async def reset_economy_slash(self, interaction: discord.Interaction, confirmation: str):
        """Slash command for resetting economy."""
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if confirmation.upper() != "CONFIRM":
            embed = EmbedBuilder.error_embed(
                "Dangerous Operation",
                "This command will **permanently delete** all economy data including:\n"
                "• All user wallets and balances\n"
                "• All transaction history\n"
                "• All bet records\n\n"
                "To confirm, type: `CONFIRM`"
            )
            await interaction.response.send_message(embed=embed)
            return

        # Simple confirmation for slash commands
        embed = EmbedBuilder.error_embed(
            "FINAL WARNING",
            "You are about to **IRREVERSIBLY DELETE** all economy data!\n\n"
            "This action cannot be undone. Are you sure?"
        )
        await interaction.response.send_message(embed=embed)

        # For slash commands, we'll just proceed since they already confirmed
        async with self.bot.get_session() as session:
            # Delete all data in correct order (respecting foreign keys)
            await session.execute(text("DELETE FROM bets"))
            await session.execute(text("DELETE FROM transactions"))
            await session.execute(text("DELETE FROM wallets"))
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Economy Reset Complete",
            "All economy data has been permanently deleted and reset."
        )
        await interaction.followup.send(embed=embed)


    # --------------------------------------------------- economy config

    @commands.group(name='econfig', aliases=['econf'], invoke_without_command=True)
    async def econfig(self, ctx: commands.Context):
        """View the current economy configuration for this server."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        async with self.bot.get_session() as session:
            row = await GuildConfigService.get(session, ctx.guild.id)
            summary = GuildConfigService.describe(row, self.config)

        embed = EmbedBuilder.success_embed(
            "Economy Configuration",
            summary,
        )
        embed.set_footer(text="Change values with: !econfig set <key> <value>")
        await ctx.send(embed=embed)

    @econfig.command(name='set')
    async def econfig_set(self, ctx: commands.Context, key: str, value: str):
        """Set an economy setting: !econfig set <key> <value>."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        async with self.bot.get_session() as session:
            ok, msg = await GuildConfigService.set(session, ctx.guild.id, key.lower(), value)
            if ok:
                await AuditService.log(
                    session, ctx.author.id, 'econfig_set',
                    f'{key}={value}', guild_id=ctx.guild.id,
                )
        await ctx.send(msg)

    @econfig.command(name='keys')
    async def econfig_keys(self, ctx: commands.Context):
        """List all configurable economy settings."""
        embed = discord.Embed(
            title="Configurable Settings",
            description="Use `!econfig set <key> <value>` to change one.",
            color=discord.Color.blurple(),
        )
        for key in sorted(SETTINGS):
            kind, lo, hi, label = SETTINGS[key]
            bounds = "" if lo is None else f"({lo}–{hi})" if hi is not None else f"(min {lo})"
            embed.add_field(name=key, value=f"`{kind}`{bounds}", inline=True)
        await ctx.send(embed=embed)

    # --------------------------------------------------- item management

    @commands.command(name='itemgive')
    async def itemgive(self, ctx: commands.Context, user: discord.User, item_id: str, qty: int = 1):
        """Give items to a user (admin only)."""
        if qty <= 0:
            return await ctx.send("Quantity must be positive.")
        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`. Use `!shoplist` to list items.")
            await ItemService.grant(session, user.id, item, qty)
            await AuditService.log(
                session, ctx.author.id, 'item_give',
                f'{item.id} x{qty} → {user.id}',
                guild_id=ctx.guild.id if ctx.guild else None, target_id=user.id,
            )
        embed = EmbedBuilder.success_embed(
            "Item Granted",
            f"Gave **{item.name}** x{qty} to {user.mention}.",
        )
        await ctx.send(embed=embed)

    @commands.command(name='shopadd')
    async def shopadd(
        self, ctx: commands.Context, item_id: str, name: str, price: int,
        category: str = 'misc', sell_price: int = 0, emoji: str = '',
    ):
        """Add or update an item in the shop: !shopadd <id> <name> <price> [category] [sell_price] [emoji]."""
        if price < 0 or sell_price < 0:
            return await ctx.send("Prices cannot be negative.")
        if sell_price == 0:
            sell_price = int(price * 0.4)

        from models import Item
        async with self.bot.get_session() as session:
            existing = await ItemService.get(session, item_id.lower())
            if existing:
                existing.name = name
                existing.price = price
                existing.sell_price = sell_price
                existing.category = category.lower()
                existing.emoji = emoji
            else:
                session.add(
                    Item(
                        id=item_id.lower(), name=name, price=price, sell_price=sell_price,
                        category=category.lower(), emoji=emoji, consumable=False,
                    )
                )
            await session.commit()
            await AuditService.log(
                session, ctx.author.id, 'shop_add',
                f'{item_id.lower()} ({name}) @ {price}',
                guild_id=ctx.guild.id if ctx.guild else None,
            )
        await ctx.send(f"Saved item `{item_id.lower()}` — {emoji} **{name}** for {format_coins(price)}.")

    @commands.command(name='shopremove')
    async def shopremove(self, ctx: commands.Context, item_id: str):
        """Remove an item from the shop (admin only)."""
        from sqlalchemy import delete as sa_delete

        from models import Item
        async with self.bot.get_session() as session:
            item = await ItemService.get(session, item_id.lower())
            if item is None:
                return await ctx.send(f"Unknown item `{item_id}`.")
            await session.execute(sa_delete(Item).where(Item.id == item_id.lower()))
            await session.commit()
            await AuditService.log(
                session, ctx.author.id, 'shop_remove',
                item_id.lower(), guild_id=ctx.guild.id if ctx.guild else None,
            )
        await ctx.send(f"Removed item `{item_id.lower()}` from the catalog.")

    @commands.command(name='shoplist', aliases=['itemlist'])
    async def shoplist(self, ctx: commands.Context):
        """List every item in the catalog (admin only)."""
        async with self.bot.get_session() as session:
            items = await ItemService.get_all(session, include_limited=True)

        if not items:
            return await ctx.send("The catalog is empty. Add items with `!shopadd`.")

        pages = []
        per_page = 10
        for start in range(0, len(items), per_page):
            embed = discord.Embed(
                title="Item Catalog",
                description="All items including limited ones.",
                color=discord.Color.blurple(),
            )
            for item in items[start:start + per_page]:
                embed.add_field(
                    name=item.name,
                    value=(
                        f"`{item.id}` • {format_coins(item.price)} • "
                        f"{item.category} • {item.rarity}"
                        + ("• limited" if item.limited else "")
                    ),
                    inline=False,
                )
            embed.set_footer(text=f"Page {start // per_page + 1}/{(len(items) - 1) // per_page + 1}")
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # --------------------------------------------------------- audit log

    @commands.command(name='audit', aliases=['auditlog'])
    async def audit(self, ctx: commands.Context, limit: int = 10):
        """View recent admin actions (admin only)."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        limit = max(1, min(limit, 25))
        async with self.bot.get_session() as session:
            logs = await AuditService.recent(session, ctx.guild.id, limit)

        if not logs:
            return await ctx.send("No audit log entries yet.")

        embed = discord.Embed(
            title=f"Audit Log (last {len(logs)})",
            color=discord.Color.dark_grey(),
        )
        for entry in logs:
            embed.add_field(
                name=f"#{entry.id} • {entry.action}",
                value=(
                    f"By <@{entry.actor_id}>" + (f"→ <@{entry.target_id}>" if entry.target_id else "")
                    + f"\n{entry.details or ''}"
                    + f"\n*{entry.created_at.strftime('%Y-%m-%d %H:%M')} UTC*"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot):
    """Setup the admin cog."""
    config = bot.config
    await bot.add_cog(Admin(bot, config))
