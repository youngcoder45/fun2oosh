"""
Admin commands cog.
"""

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import text

from bot import Fun2OoshBot
from services.guild import SETTINGS, AuditService, GuildConfigService
from services.items import ItemService
from services.role_income import RoleIncomeService
from utils.config import Config
from utils.economy_utils import EconomyUtils
from utils.helpers import (
    COLOR_INFO,
    EmbedBuilder,
    format_coins,
    format_duration,
    parse_duration,
)
from utils.pagination import PaginationView

RESET_TABLES = (
    "bets",
    "transactions",
    "wallets",
    "inventory_items",
    "role_income",
    "role_claims",
    "user_achievements",
)


class ConfirmView(discord.ui.View):
    """Button-based yes/no confirmation (Components V2)."""

    def __init__(self, owner_id: int, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This confirmation is not for you.", ephemeral=True
            )
            return False
        return True

    def _finish(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self._finish()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self._finish()
        await interaction.response.edit_message(view=self)


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

    @commands.command(name="add_money")
    async def add_money(self, ctx: commands.Context, user: discord.User, amount: int):
        """Add money to a user (admin only)."""
        async with self.bot.get_session() as session:
            success = await EconomyUtils.add_money(
                session, user.id, amount, "admin", f"Admin added {amount} 💎️"
            )

            if success:
                await session.commit()
                await ctx.send(f"Added {amount} 💎️ to {user.mention}.")
            else:
                await ctx.send("Failed to add money.")

    @app_commands.command(name="add_money", description="Add money to a user (admin only)")
    @app_commands.describe(user="User to add money to", amount="Amount to add")
    async def add_money_slash(
        self, interaction: discord.Interaction, user: discord.User, amount: int
    ):
        """Slash command for adding money."""
        is_owner = interaction.user.id == self.config.owner_id
        is_admin = False
        if interaction.guild:
            member = interaction.guild.get_member(interaction.user.id)
            if member and member.guild_permissions.administrator:
                is_admin = True
        if not (is_owner or is_admin):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        async with self.bot.get_session() as session:
            success = await EconomyUtils.add_money(
                session, user.id, amount, "admin", f"Admin added {amount} 💎️"
            )

            if success:
                await session.commit()
                await interaction.response.send_message(f"Added {amount} 💎️ to {user.mention}.")
            else:
                await interaction.response.send_message("Failed to add money.")

    @commands.command(name="reset_economy")
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
                f"To confirm, type: `{ctx.prefix}reset_economy CONFIRM`",
            )
            await ctx.send(embed=embed)
            return

        # Confirmation dialog with buttons
        embed = EmbedBuilder.error_embed(
            "Final Warning",
            "You are about to **irreversibly delete** all economy data:\n\n"
            "• All user wallets and balances\n"
            "• All transaction history\n"
            "• All bet records\n"
            "• All inventories and role income settings\n\n"
            "This cannot be undone.",
        )
        view = ConfirmView(owner_id=ctx.author.id)
        await ctx.send(embed=embed, view=view)
        timed_out = await view.wait()

        if timed_out:
            await ctx.send("Economy reset timed out. Operation cancelled.")
            return
        if view.value is False:
            await ctx.send("Economy reset cancelled.")
            return

        # Proceed with reset
        async with self.bot.get_session() as session:
            await self._reset_all_data(session)

        embed = EmbedBuilder.success_embed(
            "Economy Reset Complete", "All economy data has been permanently deleted and reset."
        )
        await ctx.send(embed=embed)

    @app_commands.command(
        name="reset_economy", description="Reset all economy data (admin only - dangerous)"
    )
    @app_commands.describe(confirmation="Type CONFIRM to proceed")
    async def reset_economy_slash(self, interaction: discord.Interaction, confirmation: str):
        """Slash command for resetting economy."""
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        if confirmation.upper() != "CONFIRM":
            embed = EmbedBuilder.error_embed(
                "Dangerous Operation",
                "This command will **permanently delete** all economy data including:\n"
                "• All user wallets and balances\n"
                "• All transaction history\n"
                "• All bet records\n\n"
                "To confirm, type: `CONFIRM`",
            )
            await interaction.response.send_message(embed=embed)
            return

        # Simple confirmation for slash commands
        embed = EmbedBuilder.error_embed(
            "Final Warning",
            "You are about to **irreversibly delete** all economy data:\n\n"
            "• All user wallets and balances\n"
            "• All transaction history\n"
            "• All bet records\n"
            "• All inventories and role income settings\n\n"
            "This cannot be undone.",
        )
        await interaction.response.send_message(embed=embed)

        # For slash commands, we'll just proceed since they already confirmed
        async with self.bot.get_session() as session:
            await self._reset_all_data(session)

        embed = EmbedBuilder.success_embed(
            "Economy Reset Complete", "All economy data has been permanently deleted and reset."
        )
        await interaction.followup.send(embed=embed)

    # --------------------------------------------------- economy config

    @commands.group(name="econfig", aliases=["econf"], invoke_without_command=True)
    async def econfig(self, ctx: commands.Context):
        """View the current economy configuration for this server."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        async with self.bot.get_session() as session:
            row = await GuildConfigService.get(session, ctx.guild.id)
            summary = GuildConfigService.describe(row, self.config)
            embed = EmbedBuilder.info_embed("Economy Configuration", summary)
        embed.set_footer(text="Change values with: !econfig set <key> <value>")
        await ctx.send(embed=embed)

    @econfig.command(name="set")
    async def econfig_set(self, ctx: commands.Context, key: str, value: str):
        """Set an economy setting: !econfig set <key> <value>."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        async with self.bot.get_session() as session:
            ok, msg = await GuildConfigService.set(session, ctx.guild.id, key.lower(), value)
            if ok:
                await AuditService.log(
                    session,
                    ctx.author.id,
                    "econfig_set",
                    f"{key}={value}",
                    guild_id=ctx.guild.id,
                )
        await ctx.send(msg)

    @econfig.command(name="keys")
    async def econfig_keys(self, ctx: commands.Context):
        """List all configurable economy settings."""
        embed = discord.Embed(
            title="Configurable Settings",
            description="Use `!econfig set <key> <value>` to change one.",
            color=COLOR_INFO,
        )
        for key in sorted(SETTINGS):
            kind, lo, hi, label = SETTINGS[key]
            bounds = "" if lo is None else f"({lo}–{hi})" if hi is not None else f"(min {lo})"
            embed.add_field(name=key, value=f"`{kind}`{bounds}", inline=True)
        await ctx.send(embed=embed)

    # --------------------------------------------------- item management

    @commands.command(name="itemgive")
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
                session,
                ctx.author.id,
                "item_give",
                f"{item.id} x{qty} → {user.id}",
                guild_id=ctx.guild.id if ctx.guild else None,
                target_id=user.id,
            )
        embed = EmbedBuilder.success_embed(
            "Item Granted",
            f"Gave **{item.name}** x{qty} to {user.mention}.",
        )
        await ctx.send(embed=embed)

    @commands.command(name="shopadd")
    async def shopadd(
        self,
        ctx: commands.Context,
        item_id: str,
        name: str,
        price: int,
        category: str = "misc",
        sell_price: int = 0,
        emoji: str = "",
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
                        id=item_id.lower(),
                        name=name,
                        price=price,
                        sell_price=sell_price,
                        category=category.lower(),
                        emoji=emoji,
                        consumable=False,
                    )
                )
            await session.commit()
            await AuditService.log(
                session,
                ctx.author.id,
                "shop_add",
                f"{item_id.lower()} ({name}) @ {price}",
                guild_id=ctx.guild.id if ctx.guild else None,
            )
        await ctx.send(
            f"Saved item `{item_id.lower()}` - {emoji} **{name}** for {format_coins(price)}."
        )

    @commands.command(name="shopremove")
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
                session,
                ctx.author.id,
                "shop_remove",
                item_id.lower(),
                guild_id=ctx.guild.id if ctx.guild else None,
            )
        await ctx.send(f"Removed item `{item_id.lower()}` from the catalog.")

    @commands.command(name="shoplist", aliases=["itemlist"])
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
                color=COLOR_INFO,
            )
            for item in items[start : start + per_page]:
                embed.add_field(
                    name=item.name,
                    value=(
                        f"`{item.id}` • {format_coins(item.price)} • "
                        f"{item.category} • {item.rarity}" + ("• limited" if item.limited else "")
                    ),
                    inline=False,
                )
            embed.set_footer(
                text=f"Page {start // per_page + 1}/{(len(items) - 1) // per_page + 1}"
            )
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # --------------------------------------------------------- audit log

    @commands.command(name="audit", aliases=["auditlog"])
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
            color=COLOR_INFO,
        )
        for entry in logs:
            embed.add_field(
                name=f"#{entry.id} • {entry.action}",
                value=(
                    f"By <@{entry.actor_id}>"
                    + (f"→ <@{entry.target_id}>" if entry.target_id else "")
                    + f"\n{entry.details or ''}"
                    + f"\n*{entry.created_at.strftime('%Y-%m-%d %H:%M')} UTC*"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @staticmethod
    async def _reset_all_data(session) -> None:
        """Delete all economy data in FK-safe order."""
        for table in RESET_TABLES:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()

    # -------------------------------------------------------- role income

    @commands.group(name="income", aliases=["roleincome"], invoke_without_command=True)
    async def income(self, ctx: commands.Context):
        """Manage role income (amount + claim interval) for !collect."""
        await self.income_list(ctx)

    @income.command(name="add")
    async def income_add(
        self, ctx: commands.Context, role: discord.Role, amount: int, interval: str = "1h"
    ):
        """Add income for a role: !income add <role> <amount> [interval]."""
        await self._income_set(ctx, role, amount, interval)

    @income.command(name="set")
    async def income_set(
        self, ctx: commands.Context, role: discord.Role, amount: int, interval: str = "1h"
    ):
        """Edit income for a role: !income set <role> <amount> [interval]."""
        await self._income_set(ctx, role, amount, interval)

    async def _income_set(
        self, ctx: commands.Context, role: discord.Role, amount: int, interval: str = "1h"
    ) -> Optional[discord.Message]:
        """Shared add/set logic for role income."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        if amount <= 0:
            return await ctx.send("Income must be a positive number of 💎️.")
        if amount > 1_000_000:
            return await ctx.send("Income cannot exceed 1,000,000 💎️.")
        seconds = parse_duration(interval)
        if seconds is None or seconds < 60:
            return await ctx.send(
                "Invalid interval. Use a duration like `30m`, `2h`, `1d` (min 1 minute)."
            )
        if seconds > 30 * 86400:
            return await ctx.send("Interval cannot exceed 30 days.")
        async with self.bot.get_session() as session:
            await RoleIncomeService.set(
                session, ctx.guild.id, role.id, amount, claim_interval=seconds
            )
            await AuditService.log(
                session,
                ctx.author.id,
                "income_set",
                f"{role.id} ({role.name}) = {amount} every {format_duration(seconds)}",
                guild_id=ctx.guild.id,
            )
        embed = EmbedBuilder.success_embed(
            "Role Income Set",
            f"**{role.name}** pays {format_coins(amount)} every {format_duration(seconds)}.",
        )
        return await ctx.send(embed=embed)

    @income.command(name="remove")
    async def income_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove income from a role: !income remove <role>."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        async with self.bot.get_session() as session:
            removed = await RoleIncomeService.remove(session, ctx.guild.id, role.id)
            if removed:
                await AuditService.log(
                    session,
                    ctx.author.id,
                    "income_remove",
                    f"{role.id} ({role.name})",
                    guild_id=ctx.guild.id,
                )
        if removed:
            embed = EmbedBuilder.success_embed(
                "Role Income Removed",
                f"**{role.name}** no longer grants income.",
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"**{role.name}** has no configured income.")

    @income.command(name="list")
    async def income_list(self, ctx: commands.Context):
        """List all configured role incomes."""
        if ctx.guild is None:
            return await ctx.send("This command only works in servers.")
        async with self.bot.get_session() as session:
            rows = await RoleIncomeService.list_all(session, ctx.guild.id)
        if not rows:
            return await ctx.send(
                "No income roles configured. Use `!income add <role> <amount> [interval]` "
                "or `/role-income set`."
            )
        lines = []
        for row in rows:
            role = ctx.guild.get_role(row.role_id)
            label = role.name if role is not None else f"Role {row.role_id}"
            lines.append(
                f"**{label}** - {format_coins(row.amount)} "
                f"every {format_duration(row.claim_interval or 3600)}"
            )
        embed = EmbedBuilder.info_embed("Role Income", "\n".join(lines))
        embed.set_footer(text="!collect pays every income role you hold")
        await ctx.send(embed=embed)

    # -------------------------------------------------- role income (slash)

    role_income = app_commands.Group(
        name="role-income",
        description="Configure role income (admin)",
        default_permissions=discord.Permissions(administrator=True),
    )

    async def _is_admin(self, interaction: discord.Interaction) -> bool:
        """Owner or server administrator."""
        if interaction.user.id == self.config.owner_id:
            return True
        if interaction.guild is not None:
            member = interaction.guild.get_member(interaction.user.id)
            return bool(member and member.guild_permissions.administrator)
        return False

    @role_income.command(name="set")
    @app_commands.describe(
        role="Role that receives income",
        amount="Coins paid per claim",
        interval="How often it can be claimed, e.g. 2h, 30m, 1d",
    )
    async def role_income_set(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        amount: int,
        interval: str = "1h",
    ):
        """Set a role's income amount and claim interval."""
        if not await self._is_admin(interaction):
            return await interaction.response.send_message(
                "You need administrator permission to manage role income.", ephemeral=True
            )
        if interaction.guild is None:
            return await interaction.response.send_message(
                "This command only works in servers.", ephemeral=True
            )
        if amount <= 0:
            return await interaction.response.send_message(
                "Income must be a positive number of coins.", ephemeral=True
            )
        if amount > 1_000_000:
            return await interaction.response.send_message(
                "Income cannot exceed 1,000,000 💎️.", ephemeral=True
            )
        seconds = parse_duration(interval)
        if seconds is None or seconds < 60:
            return await interaction.response.send_message(
                "Invalid interval. Use a duration like `30m`, `2h`, `1d` (min 1 minute).",
                ephemeral=True,
            )
        if seconds > 30 * 86400:
            return await interaction.response.send_message(
                "Interval cannot exceed 30 days.", ephemeral=True
            )
        async with self.bot.get_session() as session:
            await RoleIncomeService.set(
                session, interaction.guild.id, role.id, amount, claim_interval=seconds
            )
            await AuditService.log(
                session,
                interaction.user.id,
                "income_set",
                f"{role.id} ({role.name}) = {amount} every {format_duration(seconds)}",
                guild_id=interaction.guild.id,
            )
        embed = EmbedBuilder.success_embed(
            "Role Income Set",
            f"**{role.name}** pays {format_coins(amount)} every {format_duration(seconds)}.",
        )
        await interaction.response.send_message(embed=embed)

    @role_income.command(name="remove")
    @app_commands.describe(role="Role to remove income from")
    async def role_income_remove(self, interaction: discord.Interaction, role: discord.Role):
        """Remove income from a role."""
        if not await self._is_admin(interaction):
            return await interaction.response.send_message(
                "You need administrator permission to manage role income.", ephemeral=True
            )
        if interaction.guild is None:
            return await interaction.response.send_message(
                "This command only works in servers.", ephemeral=True
            )
        async with self.bot.get_session() as session:
            removed = await RoleIncomeService.remove(session, interaction.guild.id, role.id)
            if removed:
                await AuditService.log(
                    session,
                    interaction.user.id,
                    "income_remove",
                    f"{role.id} ({role.name})",
                    guild_id=interaction.guild.id,
                )
        if removed:
            embed = EmbedBuilder.success_embed(
                "Role Income Removed",
                f"**{role.name}** no longer grants income.",
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"**{role.name}** has no configured income.", ephemeral=True
            )

    @role_income.command(name="list")
    async def role_income_list(self, interaction: discord.Interaction):
        """List all configured role incomes."""
        if not await self._is_admin(interaction):
            return await interaction.response.send_message(
                "You need administrator permission to manage role income.", ephemeral=True
            )
        if interaction.guild is None:
            return await interaction.response.send_message(
                "This command only works in servers.", ephemeral=True
            )
        async with self.bot.get_session() as session:
            rows = await RoleIncomeService.list_all(session, interaction.guild.id)
        if not rows:
            return await interaction.response.send_message(
                "No income roles configured. Use `/role-income set` to add one.",
                ephemeral=True,
            )
        lines = []
        for row in rows:
            role = interaction.guild.get_role(row.role_id)
            label = role.name if role is not None else f"Role {row.role_id}"
            lines.append(
                f"**{label}** - {format_coins(row.amount)} "
                f"every {format_duration(row.claim_interval or 3600)}"
            )
        embed = EmbedBuilder.info_embed("Role Income", "\n".join(lines))
        embed.set_footer(text="!collect pays every income role you hold")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    """Setup the admin cog."""
    config = bot.config
    await bot.add_cog(Admin(bot, config))
