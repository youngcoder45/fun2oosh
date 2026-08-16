"""
Economy cog for wallet management and basic income commands.
"""

from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import desc, func, select

from bot import Fun2OoshBot
from models import Transaction, Wallet
from models.base import utcnow
from services.economy import EconomyService, GuardService
from services.events import event_message
from services.guild import GuildConfigService
from services.items import ItemService, booster_manager
from services.locks import lock_manager
from services.progression import ACHIEVEMENTS, AchievementService, ProgressionService
from services.role_income import RoleIncomeService
from utils.anti_fraud import anti_fraud
from utils.config import Config
from utils.cooldowns import check_cooldown
from utils.economy_utils import EconomyUtils
from utils.helpers import (
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARNING,
    EmbedBuilder,
    event_names,
    format_coins,
    unix_ts,
)
from utils.pagination import PaginationView
from utils.runtime_config import activity as activity_config
from utils.runtime_config import activity_value, fine_amount


class ProfileView(discord.ui.View):
    """Tabbed profile view (Components V2): Overview / Inventory / Achievements / Transactions."""

    def __init__(self, cog: "Economy", target, owner_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.target = target
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the command author or the profile owner can flip tabs."""
        if interaction.user.id not in (self.owner_id, self.target.id):
            await interaction.response.send_message(
                "This profile is not yours to browse.", ephemeral=True
            )
            return False
        return True

    async def _render(self, interaction: discord.Interaction, tab: str) -> None:
        embed = await self.cog.profile_embed(self.target, tab)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.secondary)
    async def overview_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._render(interaction, "overview")

    @discord.ui.button(label="Inventory", style=discord.ButtonStyle.secondary)
    async def inventory_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._render(interaction, "inventory")

    @discord.ui.button(label="Achievements", style=discord.ButtonStyle.secondary)
    async def achievements_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._render(interaction, "achievements")

    @discord.ui.button(label="Transactions", style=discord.ButtonStyle.secondary)
    async def transactions_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._render(interaction, "transactions")

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class Economy(commands.Cog):
    """Economy commands for the bot."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config

    async def cog_load(self):
        """Called when the cog is loaded."""
        pass

    @staticmethod
    async def _wallet_rank(session, total: int) -> int:
        """1-based leaderboard rank: wallets strictly richer than ``total`` + 1."""
        ahead = (
            await session.execute(
                select(func.count(Wallet.user_id)).where((Wallet.balance + Wallet.bank) > total)
            )
        ).scalar() or 0
        return int(ahead) + 1

    @commands.command(name="balance", aliases=["bal", "wallet"])
    async def balance(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """Check your or another user's balance."""
        target_user = user or ctx.author

        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, target_user.id)
            await session.commit()  # Ensure wallet is saved
            total = (wallet.balance or 0) + (wallet.bank or 0)
            rank = await self._wallet_rank(session, total)

        embed = EmbedBuilder.wallet_embed(target_user, wallet.balance, wallet.bank, rank=rank)
        await ctx.send(embed=embed)

    @app_commands.command(name="balance", description="Check your balance")
    async def balance_slash(self, interaction: discord.Interaction):
        """Slash command for balance."""
        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, interaction.user.id)
            await session.commit()  # Ensure wallet is saved
            total = (wallet.balance or 0) + (wallet.bank or 0)
            rank = await self._wallet_rank(session, total)

        embed = EmbedBuilder.wallet_embed(interaction.user, wallet.balance, wallet.bank, rank=rank)
        await interaction.response.send_message(embed=embed)

    @commands.command(name="work")
    @check_cooldown("work", 1800)  # 30 minutes
    async def work(self, ctx: commands.Context):
        """Work to earn some coins (random reward)."""
        reward = await self._resolve_work_reward(ctx.guild)

        async with self.bot.get_session() as session:
            final = await EconomyService.reward(
                session, ctx.author.id, reward, "work", "Daily work reward"
            )
            new = await AchievementService.check(session, ctx.author.id, "work")

        if final > 0:
            user, guild = event_names(ctx.author, ctx.guild)
            embed = EmbedBuilder.activity_embed(
                event_message("work", final, self.config.currency_name, user, guild),
                user=ctx.author,
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("An error occurred while processing your work reward.")
        await self._announce_achievements(ctx, new)

    @app_commands.command(name="work", description="Work to earn coins")
    @app_commands.checks.cooldown(1, 1800, key=lambda i: (i.guild_id, i.user.id))
    async def work_slash(self, interaction: discord.Interaction):
        """Slash command for work."""
        reward = await self._resolve_work_reward(interaction.guild)

        async with self.bot.get_session() as session:
            final = await EconomyService.reward(
                session, interaction.user.id, reward, "work", "Daily work reward"
            )
            new = await AchievementService.check(session, interaction.user.id, "work")

        if final > 0:
            user, guild = event_names(interaction.user, interaction.guild)
            embed = EmbedBuilder.activity_embed(
                event_message("work", final, self.config.currency_name, user, guild),
                user=interaction.user,
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "An error occurred while processing your work reward."
            )
        await self._announce_achievements(ctx=interaction, new=new)

    async def _resolve_work_reward(self, guild) -> int:
        """Resolve the !work reward.

        A per-guild ``work_reward`` override (set via ``!econfig``) wins;
        otherwise the reward is random in the ``activities.work`` range from
        ``data/config.json`` (default 100-2000).
        """
        if guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, guild.id)
                if guild_cfg.work_reward is not None:
                    return guild_cfg.work_reward
        import random

        return random.randint(
            activity_value("work", "min_reward", 100),
            activity_value("work", "max_reward", 2000),
        )

    @commands.command(name="collect", aliases=["claim"])
    async def collect(self, ctx: commands.Context):
        """Claim your role income. Collects every eligible income role's payout."""
        if ctx.guild is None:
            return await ctx.send("Collect only works in servers.")
        async with self.bot.get_session() as session:
            payouts = await self._collect_payout(session, ctx.author)
        if not payouts:
            return await ctx.send(
                "No income role is configured for you in this server. "
                "An admin can set one up with `/role-income`."
            )

        # Check + payout + claim records all under the per-user lock so
        # concurrent collects cannot double-claim.
        async with lock_manager.for_user(ctx.author.id):
            async with self.bot.get_session() as session:
                earned, breakdown, next_ts = await self._resolve_collect(
                    session, ctx.author.id, ctx.guild.id, payouts
                )
        if earned <= 0:
            embed = discord.Embed(
                description=(
                    f"<:redtick:1529045360742502481> All your income roles are on cooldown.\n"
                    f"Try again <t:{next_ts}:R>\n"
                    f"Available at <t:{next_ts}:F>"
                ),
                color=COLOR_ERROR,
            )
            EmbedBuilder.set_author_from_user(embed, ctx.author)
            return await ctx.send(embed=embed)
        embed = self._collect_embed(breakdown, ctx.author)
        await ctx.send(embed=embed)

    @app_commands.command(name="collect", description="Claim your role income")
    async def collect_slash(self, interaction: discord.Interaction):
        """Slash command for collect."""
        if interaction.guild is None:
            return await interaction.response.send_message(
                "Collect only works in servers.", ephemeral=True
            )
        async with self.bot.get_session() as session:
            payouts = await self._collect_payout(session, interaction.user)
        if not payouts:
            return await interaction.response.send_message(
                "No income role is configured for you in this server. "
                "An admin can set one up with `/role-income`.",
                ephemeral=True,
            )

        # Check + payout + claim records all under the per-user lock so
        # concurrent collects cannot double-claim.
        async with lock_manager.for_user(interaction.user.id):
            async with self.bot.get_session() as session:
                earned, breakdown, next_ts = await self._resolve_collect(
                    session, interaction.user.id, interaction.guild.id, payouts
                )
        if earned <= 0:
            embed = discord.Embed(
                description=(
                    f"<:redtick:1529045360742502481> All your income roles are on cooldown.\n"
                    f"Try again <t:{next_ts}:R>\n"
                    f"Available at <t:{next_ts}:F>"
                ),
                color=COLOR_ERROR,
            )
            EmbedBuilder.set_author_from_user(embed, interaction.user)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        embed = self._collect_embed(breakdown, interaction.user)
        await interaction.response.send_message(embed=embed)

    @commands.command(name="daily", aliases=["d"])
    async def daily(self, ctx: commands.Context):
        """Claim your daily reward! Build streaks for bigger bonuses."""
        async with self.bot.get_session() as session:
            base = self.config.daily_reward
            if ctx.guild is not None:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, "daily_reward")
            ok, msg, reward, streak = await ProgressionService.apply_daily(
                session, ctx.author.id, base
            )
            new = await AchievementService.check(session, ctx.author.id, "daily")

        if not ok:
            return await ctx.send(msg)
        embed = EmbedBuilder.success_embed(
            "Daily Reward Claimed!",
            f"You claimed your daily reward of {format_coins(reward)}!\n"
            f"Streak: **{streak}** day(s)" + ("- maximum bonus!" if streak >= 7 else ""),
        )
        await ctx.send(embed=embed)
        await self._announce_achievements(ctx, new)

    @app_commands.command(name="daily", description="Claim daily reward")
    async def daily_slash(self, interaction: discord.Interaction):
        """Slash command for daily."""
        async with self.bot.get_session() as session:
            base = self.config.daily_reward
            if interaction.guild is not None:
                guild_cfg = await GuildConfigService.get(session, interaction.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, "daily_reward")
            ok, msg, reward, streak = await ProgressionService.apply_daily(
                session, interaction.user.id, base
            )
            new = await AchievementService.check(session, interaction.user.id, "daily")

        if not ok:
            return await interaction.response.send_message(msg)
        embed = EmbedBuilder.success_embed(
            "Daily Reward Claimed!",
            f"You claimed your daily reward of {format_coins(reward)}!\n"
            f"Streak: **{streak}** day(s)" + ("- maximum bonus!" if streak >= 7 else ""),
        )
        await interaction.response.send_message(embed=embed)
        await self._announce_achievements(ctx=interaction, new=new)

    @commands.command(name="weekly", aliases=["week"])
    async def weekly(self, ctx: commands.Context):
        """Claim your weekly reward! 7-day cooldown for big bonus."""
        async with self.bot.get_session() as session:
            base = self.config.weekly_reward
            if ctx.guild is not None:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, "weekly_reward")
            ok, msg = await ProgressionService.apply_weekly(session, ctx.author.id, base)
        if ok:
            embed = EmbedBuilder.success_embed("Weekly Reward Claimed!", msg)
            await ctx.send(embed=embed)
        else:
            await ctx.send(msg)

    @app_commands.command(name="weekly", description="Claim weekly reward")
    async def weekly_slash(self, interaction: discord.Interaction):
        """Slash command for weekly."""
        async with self.bot.get_session() as session:
            base = self.config.weekly_reward
            if interaction.guild is not None:
                guild_cfg = await GuildConfigService.get(session, interaction.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, "weekly_reward")
            ok, msg = await ProgressionService.apply_weekly(session, interaction.user.id, base)
        if ok:
            embed = EmbedBuilder.success_embed("Weekly Reward Claimed!", msg)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(msg)

    @commands.command(name="deposit", aliases=["dep"])
    async def deposit(self, ctx: commands.Context, amount: str):
        """Deposit coins from wallet to bank. Keep your money safe from robbery and crime losses!"""
        try:
            if amount.lower() == "all":
                async with self.bot.get_session() as session:
                    wallet = await EconomyUtils.get_wallet(session, ctx.author.id)
                    if wallet and wallet.balance > 0:
                        amount_int = wallet.balance
                    else:
                        await ctx.send("You don't have any coins to deposit.")
                        return
            else:
                amount_int = int(amount)
                if amount_int <= 0:
                    await ctx.send("Deposit amount must be positive.")
                    return
        except ValueError:
            await ctx.send("Invalid amount. Please provide a number or 'all'.")
            return

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            wallet = await EconomyUtils.get_wallet(session, ctx.author.id)
            if not wallet or wallet.balance < amount_int:
                await ctx.send("You don't have enough coins in your wallet.")
                return

            wallet.balance -= amount_int
            wallet.bank += amount_int

            tx = Transaction(
                user_id=ctx.author.id,
                type="deposit",
                amount=-amount_int,
                description=f"Deposited {amount_int} coins to bank",
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Deposit Successful", f"You deposited {format_coins(amount_int)} to your bank."
        )
        await ctx.send(embed=embed)

    @app_commands.command(name="deposit", description="Deposit coins from wallet to bank")
    @app_commands.describe(amount='Amount to deposit (number or "all")')
    async def deposit_slash(self, interaction: discord.Interaction, amount: str):
        """Slash command for deposit."""
        try:
            if amount.lower() == "all":
                async with self.bot.get_session() as session:
                    wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
                    if wallet and wallet.balance > 0:
                        amount_int = wallet.balance
                    else:
                        await interaction.response.send_message(
                            "You don't have any coins to deposit."
                        )
                        return
            else:
                amount_int = int(amount)
                if amount_int <= 0:
                    await interaction.response.send_message("Deposit amount must be positive.")
                    return
        except ValueError:
            await interaction.response.send_message(
                "Invalid amount. Please provide a number or 'all'."
            )
            return

        async with lock_manager.for_user(interaction.user.id), self.bot.get_session() as session:
            wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
            if not wallet or wallet.balance < amount_int:
                await interaction.response.send_message(
                    "You don't have enough coins in your wallet."
                )
                return

            wallet.balance -= amount_int
            wallet.bank += amount_int

            tx = Transaction(
                user_id=interaction.user.id,
                type="deposit",
                amount=-amount_int,
                description=f"Deposited {amount_int} coins to bank",
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Deposit Successful", f"You deposited {format_coins(amount_int)} to your bank."
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name="withdraw", aliases=["wd", "with"])
    async def withdraw(self, ctx: commands.Context, amount: str):
        """Withdraw coins from bank to wallet. Get cash for gambling and transactions!"""
        try:
            if amount.lower() == "all":
                async with self.bot.get_session() as session:
                    wallet = await EconomyUtils.get_wallet(session, ctx.author.id)
                    if wallet and wallet.bank > 0:
                        amount_int = wallet.bank
                    else:
                        await ctx.send("You don't have any coins in your bank.")
                        return
            else:
                amount_int = int(amount)
                if amount_int <= 0:
                    await ctx.send("Withdraw amount must be positive.")
                    return
        except ValueError:
            await ctx.send("Invalid amount. Please provide a number or 'all'.")
            return

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            wallet = await EconomyUtils.get_wallet(session, ctx.author.id)
            if not wallet or wallet.bank < amount_int:
                await ctx.send("You don't have enough coins in your bank.")
                return

            wallet.bank -= amount_int
            wallet.balance += amount_int

            tx = Transaction(
                user_id=ctx.author.id,
                type="withdraw",
                amount=amount_int,
                description=f"Withdrew {amount_int} coins from bank",
            )
            session.add(tx)
            await session.commit()

        embed = discord.Embed(
            description=f"You withdrew {format_coins(amount_int)} from your bank.",
            color=COLOR_SUCCESS,
        )
        EmbedBuilder.set_author_from_user(embed, ctx.author)
        await ctx.send(embed=embed)

    @app_commands.command(name="withdraw", description="Withdraw coins from bank to wallet")
    @app_commands.describe(amount='Amount to withdraw (number or "all")')
    async def withdraw_slash(self, interaction: discord.Interaction, amount: str):
        """Slash command for withdraw."""
        try:
            if amount.lower() == "all":
                async with self.bot.get_session() as session:
                    wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
                    if wallet and wallet.bank > 0:
                        amount_int = wallet.bank
                    else:
                        await interaction.response.send_message(
                            "You don't have any coins in your bank."
                        )
                        return
            else:
                amount_int = int(amount)
                if amount_int <= 0:
                    await interaction.response.send_message("Withdraw amount must be positive.")
                    return
        except ValueError:
            await interaction.response.send_message(
                "Invalid amount. Please provide a number or 'all'."
            )
            return

        async with lock_manager.for_user(interaction.user.id), self.bot.get_session() as session:
            wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
            if not wallet or wallet.bank < amount_int:
                await interaction.response.send_message("You don't have enough coins in your bank.")
                return

            wallet.bank -= amount_int
            wallet.balance += amount_int

            tx = Transaction(
                user_id=interaction.user.id,
                type="withdraw",
                amount=amount_int,
                description=f"Withdrew {amount_int} coins from bank",
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Withdrawal Successful", f"You withdrew {format_coins(amount_int)} from your bank."
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name="transfer", aliases=["pay"])
    async def transfer(self, ctx: commands.Context, user: discord.User, amount: int):
        """Transfer coins to another user."""
        if user == ctx.author:
            await ctx.send("You can't transfer coins to yourself.")
            return

        if amount <= 0:
            await ctx.send("Transfer amount must be positive.")
            return

        # Check for fraud
        suspicious, reason = anti_fraud.is_suspicious(ctx.author.id, amount, "transfer")
        if suspicious:
            await ctx.send(f"Transfer blocked: {reason}")
            return

        tax_rate = 0.0
        if ctx.guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                tax_rate = guild_cfg.tax_rate or 0.0

        async with self.bot.get_session() as session:
            success, tax = await EconomyService.transfer(
                session,
                ctx.author.id,
                user.id,
                amount,
                f"Transfer from {ctx.author.display_name}",
                tax_rate=tax_rate,
            )

        if success:
            desc = f"You transferred {format_coins(amount)} to {user.mention}."
            if tax:
                desc += f"\n*Transfer tax: {format_coins(tax)}*"
            embed = EmbedBuilder.activity_embed(desc, user=ctx.author)
            await ctx.send(embed=embed)
        else:
            await ctx.send("Transfer failed. Check your balance and try again.")

    @app_commands.command(name="transfer", description="Transfer coins to another user")
    @app_commands.describe(user="User to transfer to", amount="Amount to transfer")
    async def transfer_slash(
        self, interaction: discord.Interaction, user: discord.User, amount: int
    ):
        """Slash command for transfer."""
        if user == interaction.user:
            await interaction.response.send_message("You can't transfer coins to yourself.")
            return

        if amount <= 0:
            await interaction.response.send_message("Transfer amount must be positive.")
            return

        # Check for fraud
        suspicious, reason = anti_fraud.is_suspicious(interaction.user.id, amount, "transfer")
        if suspicious:
            await interaction.response.send_message(f"Transfer blocked: {reason}")
            return

        tax_rate = 0.0
        if interaction.guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, interaction.guild.id)
                tax_rate = guild_cfg.tax_rate or 0.0

        async with self.bot.get_session() as session:
            success, tax = await EconomyService.transfer(
                session,
                interaction.user.id,
                user.id,
                amount,
                f"Transfer from {interaction.user.display_name}",
                tax_rate=tax_rate,
            )

        if success:
            desc = f"You transferred {format_coins(amount)} to {user.mention}."
            if tax:
                desc += f"\n*Transfer tax: {format_coins(tax)}*"
            embed = EmbedBuilder.activity_embed(desc, user=interaction.user)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Transfer failed. Check your balance and try again."
            )

    async def _leaderboard_pages(self, limit: int = 25) -> List[discord.Embed]:
        """Build paginated leaderboard pages from the wallet totals."""
        async with self.bot.get_session() as session:
            stmt = (
                select(Wallet.user_id, (Wallet.balance + Wallet.bank).label("total"))
                .order_by(desc("total"))
                .limit(limit)
            )
            rows = [(row.user_id, row.total) for row in (await session.execute(stmt)).all()]

        pages = []
        per_page = 10
        for start in range(0, len(rows), per_page):
            chunk = rows[start : start + per_page]
            embed = EmbedBuilder.leaderboard_embed(
                chunk, "Richest Players", bot=self.bot, start_rank=start + 1
            )
            embed.set_footer(text=f"Page {start // per_page + 1}/{(len(rows) - 1) // per_page + 1}")
            pages.append(embed)
        return pages

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        """Show the richest users (paginated)."""
        pages = await self._leaderboard_pages()
        if not pages:
            return await ctx.send("No players found yet. Be the first to earn some coins!")
        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @app_commands.command(name="leaderboard", description="Show the leaderboard")
    async def leaderboard_slash(self, interaction: discord.Interaction):
        """Slash command for leaderboard."""
        pages = await self._leaderboard_pages()
        if not pages:
            return await interaction.response.send_message(
                "No players found yet. Be the first to earn some coins!"
            )
        view = PaginationView(pages, owner_id=interaction.user.id)
        await interaction.response.send_message(embed=pages[0], view=view)

    @commands.command(name="beg", aliases=["b"])
    @check_cooldown("beg", lambda: int(activity_config("beg").get("cooldown_seconds", 60)))
    async def beg(self, ctx: commands.Context):
        """Beg for coins from strangers. Success rate and reward are configurable in config.json."""
        import random

        cfg = activity_config("beg")
        user, guild = event_names(ctx.author, ctx.guild)

        # Success rate + reward range from config.json
        if random.random() < float(cfg.get("success_rate", 0.7)):
            reward = random.randint(int(cfg.get("min_reward", 10)), int(cfg.get("max_reward", 100)))

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, "beg", "Begging reward"
                )
                await session.commit()

            embed = EmbedBuilder.activity_embed(
                event_message("beg_success", reward, self.config.currency_name, user, guild),
                user=ctx.author,
            )
            await ctx.send(embed=embed)
        else:
            embed = EmbedBuilder.activity_embed(
                event_message("beg_failure", 0, self.config.currency_name, user, guild),
                color=COLOR_ERROR,
                user=ctx.author,
            )
            await ctx.send(embed=embed)

    @commands.command(name="crime", aliases=["c"])
    @check_cooldown("crime", lambda: int(activity_config("crime").get("cooldown_seconds", 300)))
    async def crime(self, ctx: commands.Context):
        """Commit a crime for big rewards! Success rate, rewards and fine are configurable in config.json."""
        import random

        cfg = activity_config("crime")
        user, guild = event_names(ctx.author, ctx.guild)

        # Success rate + crime pool from config.json
        success = random.random() < float(cfg.get("success_rate", 0.4))
        crimes = cfg.get("crimes") or [
            {"description": "robbed a bank", "min_reward": 500, "max_reward": 1500},
            {"description": "hacked a corporation", "min_reward": 800, "max_reward": 2000},
            {"description": "stole a rare painting", "min_reward": 600, "max_reward": 1800},
            {"description": "smuggled contraband", "min_reward": 400, "max_reward": 1200},
            {"description": "pickpocketed tourists", "min_reward": 300, "max_reward": 800},
        ]

        crime = random.choice(crimes)
        crime_desc = crime["description"]
        min_reward = int(crime.get("min_reward", 300))
        max_reward = int(crime.get("max_reward", 2000))

        if ctx.guild is not None:
            async with self.bot.get_session() as session:
                if guard := await self._guard_error(ctx, session):
                    return await ctx.send(guard)

        if success:
            reward = random.randint(min_reward, max_reward)

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, "crime", f"Crime: {crime_desc}"
                )
                await session.commit()

            embed = EmbedBuilder.activity_embed(
                event_message("crime_success", reward, self.config.currency_name, user, guild),
                user=ctx.author,
            )
            await ctx.send(embed=embed)
        else:
            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                fine = fine_amount(cfg, wallet.balance or 0, 200, 600)
                if fine > 0:
                    wallet.balance -= fine
                    session.add(
                        Transaction(
                            user_id=ctx.author.id,
                            type="crime_fail",
                            amount=-fine,
                            description=f"Crime fine: {crime_desc}",
                        )
                    )
                    await session.commit()
                    loss_msg = event_message(
                        "crime_failure", fine, self.config.currency_name, user, guild
                    )
                else:
                    loss_msg = "You were caught but had no money to pay the fine!"

            embed = EmbedBuilder.activity_embed(
                loss_msg,
                color=COLOR_ERROR,
                user=ctx.author,
            )
            await ctx.send(embed=embed)

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, "crime")
        await self._announce_achievements(ctx, new)

    @commands.command(name="rob", aliases=["steal"])
    @check_cooldown("rob", lambda: int(activity_config("rob").get("cooldown_seconds", 600)))
    async def rob(self, ctx: commands.Context, user: discord.User):
        """Try to rob another user (risky!). Success rate and fine are configurable in config.json."""
        import random

        cfg = activity_config("rob")
        if user == ctx.author:
            return await ctx.send("You can't rob yourself!")

        if user.bot:
            return await ctx.send("You can't rob bots!")

        async with (
            lock_manager.for_users(ctx.author.id, user.id),
            self.bot.get_session() as session,
        ):
            if guard := await self._guard_error(ctx, session):
                return await ctx.send(guard)
            robber_wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            victim_wallet = await EconomyUtils.get_or_create_wallet(session, user.id)

            min_attempt = int(cfg.get("min_wallet_to_attempt", 0))
            min_victim = int(cfg.get("min_victim_balance", 100))

            # Need coins to attempt robbery
            if (robber_wallet.balance or 0) < min_attempt:
                return await ctx.send(
                    f"You need at least {format_coins(min_attempt)} to attempt a robbery!"
                )

            # Victim needs coins to rob
            if (victim_wallet.balance or 0) < min_victim:
                return await ctx.send(f"{user.display_name} doesn't have enough coins to rob!")

            # Success rate from config.json
            success = random.random() < float(cfg.get("success_rate", 0.35))

            if success:
                # Rob a configurable % of victim's balance, capped
                rob_min = float(cfg.get("rob_min_percent", 0.1))
                rob_max = float(cfg.get("rob_max_percent", 0.3))
                rob_cap = int(cfg.get("rob_cap", 5000))
                rob_amount = int(victim_wallet.balance * random.uniform(rob_min, rob_max))
                rob_amount = min(rob_amount, rob_cap)

                victim_wallet.balance -= rob_amount
                robber_wallet.balance += rob_amount
                session.add(
                    Transaction(
                        user_id=ctx.author.id,
                        type="rob",
                        amount=rob_amount,
                        description=f"Robbed {user.display_name}",
                    )
                )
                session.add(
                    Transaction(
                        user_id=user.id,
                        type="rob_loss",
                        amount=-rob_amount,
                        description=f"Robbed by {ctx.author.display_name}",
                    )
                )
                await session.commit()

                embed = EmbedBuilder.activity_embed(
                    f"You robbed {format_coins(rob_amount)} from {user.mention}!",
                    user=ctx.author,
                )
                await ctx.send(embed=embed)
            else:
                # Failed - lose a configurable % of your wallet (clamped)
                fine = fine_amount(cfg, robber_wallet.balance or 0, 200, 500)

                robber_wallet.balance -= fine
                victim_wallet.balance += fine // 2  # Victim gets half
                session.add(
                    Transaction(
                        user_id=ctx.author.id,
                        type="rob_fail",
                        amount=-fine,
                        description=f"Caught robbing {user.display_name}",
                    )
                )
                await session.commit()

                embed = EmbedBuilder.activity_embed(
                    f"You were caught trying to rob {user.mention}!\n"
                    f"You lost {format_coins(fine)} and they got {format_coins(fine // 2)}!",
                    color=COLOR_ERROR,
                    user=ctx.author,
                )
                await ctx.send(embed=embed)

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, "rob")
        await self._announce_achievements(ctx, new)

    @commands.command(name="gamble", aliases=["bet"])
    @check_cooldown("gamble", 30)  # 30 seconds
    async def gamble(self, ctx: commands.Context, amount: int):
        """Gamble your coins! 45% chance to double, 55% chance to lose all."""
        import random

        if amount < 50:
            return await ctx.send("Minimum gamble is 50 coins!")

        if amount > 10000:
            return await ctx.send("Maximum gamble is 10,000 coins!")

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            if guard := await self._guard_error(ctx, session):
                return await ctx.send(guard)
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)

            if wallet.balance < amount:
                return await ctx.send(
                    f"You don't have enough coins! Balance: {format_coins(wallet.balance)}"
                )

            # Deduct bet
            wallet.balance -= amount
            session.add(
                Transaction(
                    user_id=ctx.author.id, type="gamble", amount=amount, description="Gamble wager"
                )
            )

            # 45% win rate
            won = random.random() < 0.45

            if won:
                payout = amount * 2
                wallet.balance += payout
                session.add(
                    Transaction(
                        user_id=ctx.author.id,
                        type="gamble_win",
                        amount=payout,
                        description="Gamble payout",
                    )
                )
                await session.commit()
            else:
                await session.commit()

            # Gambling result card: author line, OUTCOME + BALANCE sections.
            outcome_block = "```diff\n+ WIN\n```" if won else "```diff\n- LOSS\n```"
            if won:
                result_lines = f"Payout: {format_coins(payout)}\nProfit: +{format_coins(amount)}"
            else:
                result_lines = f"Payout: {format_coins(0)}\nLoss: -{format_coins(amount)}"

            embed = discord.Embed(color=COLOR_SUCCESS if won else COLOR_ERROR)
            EmbedBuilder.set_author_from_user(embed, ctx.author)
            embed.add_field(
                name="OUTCOME",
                value=f"{outcome_block}\n{result_lines}",
                inline=False,
            )
            embed.add_field(
                name="BALANCE",
                value=f"```\n{wallet.balance:,} 💎️\n```",
                inline=False,
            )
            await ctx.send(embed=embed)

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, "gamble")
        await self._announce_achievements(ctx, new)

    @commands.command(name="richest", aliases=["top10", "baltop"])
    async def richest(self, ctx: commands.Context):
        """Show the richest users with wallet breakdown (paginated)."""
        async with self.bot.get_session() as session:
            stmt = (
                select(
                    Wallet.user_id,
                    Wallet.balance,
                    Wallet.bank,
                    (Wallet.balance + Wallet.bank).label("total"),
                )
                .order_by(desc("total"))
                .limit(25)
            )
            users = (await session.execute(stmt)).all()

        if not users:
            return await ctx.send("No users found in the economy!")

        pages = []
        per_page = 10
        for start in range(0, len(users), per_page):
            embed = discord.Embed(title="Richest Players", color=COLOR_INFO)
            for idx, user_data in enumerate(users[start : start + per_page], start=start + 1):
                embed.add_field(
                    name=f"#{idx} <@{user_data.user_id}>",
                    value=(
                        f"Total: **{user_data.total:,}** coins • "
                        f"Wallet: {user_data.balance:,} • Bank: {user_data.bank:,}"
                    ),
                    inline=False,
                )
            embed.set_footer(
                text=f"Page {start // per_page + 1}/{(len(users) - 1) // per_page + 1}"
            )
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @commands.command(name="give", aliases=["gift"])
    async def give(self, ctx: commands.Context, user: discord.User, amount: int):
        """Give coins to another user (no tax)."""
        if user == ctx.author:
            return await ctx.send("You can't give coins to yourself!")

        if user.bot:
            return await ctx.send("You can't give coins to bots!")

        if amount <= 0:
            return await ctx.send("Amount must be positive!")

        if amount < 10:
            return await ctx.send("Minimum gift amount is 10 coins!")

        async with (
            lock_manager.for_users(ctx.author.id, user.id),
            self.bot.get_session() as session,
        ):
            sender_wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)

            if sender_wallet.balance < amount:
                return await ctx.send(
                    f"You don't have enough coins! Balance: {format_coins(sender_wallet.balance)}"
                )

            # Transfer
            sender_wallet.balance -= amount
            await EconomyUtils.add_money(
                session, user.id, amount, "gift", f"Gift from {ctx.author.display_name}"
            )
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Gift Sent!", f"You gave {format_coins(amount)} to {user.mention}!"
        )
        await ctx.send(embed=embed)

    @commands.command(name="search", aliases=["scavenge"])
    @check_cooldown("search", lambda: int(activity_config("search").get("cooldown_seconds", 45)))
    async def search(self, ctx: commands.Context):
        """Search random places for coins. Success rate and locations are configurable in config.json."""
        import random

        cfg = activity_config("search")
        locations = cfg.get("locations") or [
            {"name": "couch cushions", "min_reward": 20, "max_reward": 80},
            {"name": "park bench", "min_reward": 30, "max_reward": 100},
            {"name": "parking lot", "min_reward": 15, "max_reward": 60},
            {"name": "vending machine", "min_reward": 25, "max_reward": 90},
            {"name": "library books", "min_reward": 10, "max_reward": 50},
            {"name": "trash bin", "min_reward": 5, "max_reward": 40},
            {"name": "car seats", "min_reward": 30, "max_reward": 110},
            {"name": "beach sand", "min_reward": 40, "max_reward": 120},
        ]

        user, guild = event_names(ctx.author, ctx.guild)
        location = random.choice(locations)
        location_name = location["name"]
        min_reward = int(location.get("min_reward", 10))
        max_reward = int(location.get("max_reward", 100))

        # Success rate from config.json
        if random.random() < float(cfg.get("success_rate", 0.8)):
            reward = random.randint(min_reward, max_reward)

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, "search", f"Searched {location_name}"
                )
                await session.commit()

            embed = EmbedBuilder.activity_embed(
                event_message("search_success", reward, self.config.currency_name, user, guild),
                user=ctx.author,
            )
        else:
            embed = EmbedBuilder.activity_embed(
                event_message("search_failure", 0, self.config.currency_name, user, guild),
                color=COLOR_WARNING,
                user=ctx.author,
            )

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, "search")
        await ctx.send(embed=embed)
        await self._announce_achievements(ctx, new)

    # ----------------------------------------------------------------- profile

    @commands.hybrid_command(
        name="profile",
        aliases=["prof", "stats"],
        description="View a detailed profile with tabbed stats",
    )
    @app_commands.describe(user="View another user's profile")
    async def profile(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """View a detailed profile (Overview / Inventory / Achievements / Transactions)."""
        target = user or ctx.author
        embed = await self.profile_embed(target, "overview")
        view = ProfileView(self, target, owner_id=ctx.author.id)
        await ctx.send(embed=embed, view=view)

    async def profile_embed(self, target, tab: str = "overview") -> discord.Embed:
        """Build one tab of the profile embed for ``target``."""
        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, target.id)

            tx_stmt = select(func.count(Transaction.id)).where(Transaction.user_id == target.id)
            tx_count = (await session.execute(tx_stmt)).scalar() or 0

            earn_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.user_id == target.id, Transaction.amount > 0
            )
            total_earned = (await session.execute(earn_stmt)).scalar() or 0

            inv_rows = await ItemService.list_inventory(session, target.id)
            inv_count = sum(inv.quantity for inv, _ in inv_rows)
            inv_value = await ItemService.inventory_value(session, target.id)
            achievement_count = await AchievementService.count(session, target.id)

            txs: List[Transaction] = []
            if tab == "transactions":
                txs = list(
                    (
                        await session.execute(
                            select(Transaction)
                            .where(Transaction.user_id == target.id)
                            .order_by(Transaction.id.desc())
                            .limit(6)
                        )
                    ).scalars()
                )
            unlocked: set = set()
            if tab == "achievements":
                unlocked = set(await AchievementService.unlocked_ids(session, target.id))
            await session.commit()  # Persist wallet if it was just created

        total_wealth = EconomyService.networth(wallet, inv_value)
        avatar = target.display_avatar.url

        if tab == "inventory":
            embed = discord.Embed(title=f"{target.display_name}'s Inventory", color=COLOR_INFO)
            embed.set_thumbnail(url=avatar)
            if not inv_rows:
                embed.description = "Empty! Buy items with `!shop` / `!buy`."
            else:
                lines = []
                for inv, item in inv_rows[:8]:
                    lines.append(
                        f"**{item.name}** x{inv.quantity} • {format_coins(item.price * inv.quantity)}"
                    )
                if len(inv_rows) > 8:
                    lines.append(f"*…and {len(inv_rows) - 8} more*")
                embed.description = "\n".join(lines)
            embed.set_footer(text=f"{inv_count} items • worth {format_coins(inv_value)}")
            return embed

        if tab == "achievements":
            embed = discord.Embed(
                title=f"{target.display_name}'s Achievements",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=avatar)
            embed.description = f"**{len(unlocked)}/{len(ACHIEVEMENTS)}** unlocked"
            if not unlocked:
                embed.description += "\nNo achievements unlocked yet — start earning!"
            else:
                lines = []
                for aid, meta in ACHIEVEMENTS.items():
                    if aid in unlocked:
                        lines.append(f"✅ **{meta['name']}** — {meta['desc']}")
                embed.description += "\n\n" + "\n".join(lines)
            return embed

        if tab == "transactions":
            embed = discord.Embed(title=f"{target.display_name}'s Transactions", color=COLOR_INFO)
            embed.set_thumbnail(url=avatar)
            if not txs:
                embed.description = "No transactions yet."
            else:
                lines = []
                for tx in txs:
                    sign = "+" if tx.amount >= 0 else ""
                    lines.append(
                        f"**{tx.type}** {sign}{tx.amount:,} • *<t:{unix_ts(tx.timestamp)}:R>*"
                    )
                embed.description = "\n".join(lines)
            embed.set_footer(text=f"{tx_count} total transactions")
            return embed

        # ---------------------------------------------------------- overview
        embed = discord.Embed(title=f"{target.display_name}'s Profile", color=COLOR_INFO)
        embed.set_thumbnail(url=avatar)

        embed.add_field(
            name="Wealth",
            value=(
                f"**Total:** {format_coins(total_wealth)}\n"
                f"**Wallet:** {format_coins(wallet.balance)}\n"
                f"**Bank:** {format_coins(wallet.bank)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Statistics",
            value=(
                f"**Transactions:** {tx_count}\n"
                f"**Total Earned:** {format_coins(total_earned)}\n"
                f"**Inventory:** {inv_count} items • {format_coins(inv_value)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Progression",
            value=(
                f"**Prestige:** {wallet.prestige or 0}\n"
                f"**Reputation:** {wallet.reputation or 0}\n"
                f"**Daily Streak:** {wallet.daily_streak or 0}\n"
                f"**Achievements:** {achievement_count}/{len(ACHIEVEMENTS)}"
            ),
            inline=False,
        )
        account_lines = []
        if target.created_at:
            account_lines.append(f"**Account Created:** <t:{int(target.created_at.timestamp())}:D>")
        joined_at = getattr(target, "joined_at", None)
        if joined_at:
            account_lines.append(f"**Joined Server:** <t:{int(joined_at.timestamp())}:D>")
        if account_lines:
            embed.add_field(name="Account", value="\n".join(account_lines), inline=False)
        embed.set_footer(text="Browse the tabs below for more stats")
        return embed

    @commands.command(name="transactions", aliases=["history", "tx", "logs"])
    async def transactions(
        self, ctx: commands.Context, user: Optional[discord.User] = None, limit: int = 10
    ):
        """View your recent transaction history (paginated)."""
        target = user or ctx.author
        limit = max(5, min(limit, 25))
        async with self.bot.get_session() as session:
            stmt = (
                select(Transaction)
                .where(Transaction.user_id == target.id)
                .order_by(Transaction.id.desc())
                .limit(limit)
            )
            txs = list((await session.execute(stmt)).scalars())

        if not txs:
            return await ctx.send(f"No transactions found for {target.display_name}.")

        pages = []
        per_page = 6
        for start in range(0, len(txs), per_page):
            embed = discord.Embed(
                title=f"{target.display_name}'s Transactions",
                color=COLOR_INFO,
            )
            for tx in txs[start : start + per_page]:
                sign = "+" if tx.amount >= 0 else ""
                embed.add_field(
                    name=f"{tx.type} • {sign}{tx.amount:,}",
                    value=f"{tx.description or '—'}\n*<t:{unix_ts(tx.timestamp)}:R>*",
                    inline=False,
                )
            embed.set_footer(text=f"Page {start // per_page + 1}/{(len(txs) - 1) // per_page + 1}")
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @commands.command(name="prestige", aliases=["prest"])
    async def prestige(self, ctx: commands.Context):
        """Reset your wealth for +1 prestige (requires 1,000,000 net worth). +2% rewards per level."""
        async with self.bot.get_session() as session:
            ok, msg = await ProgressionService.prestige(session, ctx.author.id)
            new = await AchievementService.check(session, ctx.author.id, "prestige") if ok else []

        if ok:
            embed = EmbedBuilder.success_embed("Prestige Up!", msg)
            await ctx.send(embed=embed)
        else:
            await ctx.send(msg)
        await self._announce_achievements(ctx, new)

    # ------------------------------------------------------------------- helpers

    @staticmethod
    async def _announce_achievements(ctx, new):
        """Send an embed for newly unlocked achievements (works for ctx or interaction)."""
        if not new:
            return
        lines = "\n".join(f"**{a['name']}** - {a['desc']}" for a in new)
        embed = EmbedBuilder.gold_embed("Achievements Unlocked!", lines)
        send = getattr(ctx, "send", None)
        if send is not None:
            await send(embed=embed)
        else:
            try:
                await ctx.followup.send(embed=embed)
            except Exception:
                await ctx.response.send_message(embed=embed)

    async def _resolve_collect(
        self, session, user_id: int, guild_id: int, payouts
    ) -> Tuple[int, List[Tuple[str, int, int]], int]:
        """Claim every ready income role, atomically, under the caller's lock.

        Returns ``(earned, breakdown, next_ts)`` — breakdown entries are
        ``(role_name, earned, role_id)`` so the embed can render mentions.
        ``earned`` is 0 when every role is on cooldown; ``next_ts`` is the
        epoch of the earliest role that will become claimable again.
        """
        earned = 0
        breakdown: List[Tuple[str, int, int]] = []
        ready = []
        next_waits = []
        for amount, source, role_id, interval in payouts:
            claim = await RoleIncomeService.last_claim(session, guild_id, user_id, role_id)
            wait = RoleIncomeService.seconds_until_next_claim(claim, interval)
            if wait > 0:
                next_waits.append(wait)
                continue
            ready.append((amount, source, role_id, interval))
            next_waits.append(interval)

        if not ready:
            # Nothing claimed — report the earliest window without touching the wallet.
            return 0, [], unix_ts(utcnow()) + min(next_waits)

        wallet = await EconomyUtils.get_or_create_wallet(session, user_id)
        for amount, source, role_id, interval in ready:
            final = int(amount * booster_manager.get_multiplier(user_id))
            earned += final
            breakdown.append((source, final, role_id))
            wallet.balance += final
            session.add(
                Transaction(
                    user_id=user_id,
                    type="collect",
                    amount=final,
                    description=f"Role income: {source}",
                )
            )
            await RoleIncomeService.record_claim(session, guild_id, user_id, role_id)
        await session.commit()
        return earned, breakdown, unix_ts(utcnow()) + min(next_waits)

    async def _collect_payout(self, session, user):
        """Resolve the collect payout: every eligible income role the user holds.

        Returns a list of ``(amount, source_label, role_id, claim_interval)``
        tuples, empty when the user holds no configured income role.
        """
        guild = getattr(user, "guild", None)
        if guild is None:
            return []
        role_ids = [role.id for role in getattr(user, "roles", [])]
        incomes = await RoleIncomeService.all_for(session, guild.id, role_ids)
        payouts = []
        for income in incomes:
            if income.amount <= 0:
                continue
            role = guild.get_role(income.role_id)
            source = role.name if role is not None else f"Role {income.role_id}"
            payouts.append((income.amount, source, income.role_id, income.claim_interval or 3600))
        return payouts

    @staticmethod
    def _collect_embed(breakdown: List[Tuple[str, int, int]], user) -> discord.Embed:
        """UnbelievaBoat-style role-income embed: success line + numbered roles."""
        lines = "\n".join(
            f"{i} - <@&{role_id}> {earned:,} 💎"
            for i, (_source, earned, role_id) in enumerate(breakdown, start=1)
        )
        embed = discord.Embed(
            description=(
                f"<:greentick:1529045309081256026> Role income successfully collected!\n{lines}"
            ),
            color=COLOR_SUCCESS,
        )
        EmbedBuilder.set_author_from_user(embed, user)
        return embed

    async def _guard_error(self, ctx, session) -> Optional[str]:
        """Return a block message if anti-alt protection triggers, else None."""
        if ctx.guild is None:
            return None
        guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
        return GuardService.check_user_allowed(ctx.author, guild_cfg)


async def setup(bot):
    """Setup the economy cog."""
    config = bot.config
    await bot.add_cog(Economy(bot, config))
