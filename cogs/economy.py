"""
Economy cog for wallet management and basic income commands.
"""

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import desc, func, select

from bot import Fun2OoshBot
from models import Transaction, Wallet
from services.economy import EconomyService, GuardService
from services.guild import GuildConfigService
from services.items import ItemService
from services.locks import lock_manager
from services.progression import ACHIEVEMENTS, AchievementService, ProgressionService
from utils.anti_fraud import anti_fraud
from utils.config import Config
from utils.cooldowns import check_cooldown
from utils.economy_utils import EconomyUtils
from utils.helpers import EmbedBuilder, format_coins
from utils.pagination import PaginationView


class Economy(commands.Cog):
    """Economy commands for the bot."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config

    async def cog_load(self):
        """Called when the cog is loaded."""
        pass

    @commands.command(name='balance', aliases=['bal', 'wallet'])
    async def balance(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """Check your or another user's balance."""
        target_user = user or ctx.author

        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, target_user.id)
            await session.commit() # Ensure wallet is saved

        embed = EmbedBuilder.wallet_embed(target_user, wallet.balance, wallet.bank)
        await ctx.send(embed=embed)

    @app_commands.command(name='balance', description='Check your balance')
    async def balance_slash(self, interaction: discord.Interaction):
        """Slash command for balance."""
        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, interaction.user.id)
            await session.commit() # Ensure wallet is saved

        embed = EmbedBuilder.wallet_embed(interaction.user, wallet.balance, wallet.bank)
        await interaction.response.send_message(embed=embed)

    @commands.command(name='work')
    @check_cooldown('work', 1800) # 30 minutes
    async def work(self, ctx: commands.Context):
        """Work to earn some coins."""
        reward = self.config.work_reward
        if ctx.guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                reward = GuildConfigService.effective(guild_cfg, self.config, 'work_reward')

        async with self.bot.get_session() as session:
            final = await EconomyService.reward(
                session, ctx.author.id, reward, 'work', 'Daily work reward'
            )
            new = await AchievementService.check(session, ctx.author.id, 'work')

        if final > 0:
            embed = EmbedBuilder.success_embed(
                "Work Complete!",
                f"You worked hard and earned {format_coins(final)}!"
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("An error occurred while processing your work reward.")
        await self._announce_achievements(ctx, new)

    @app_commands.command(name='work', description='Work to earn coins')
    @app_commands.checks.cooldown(1, 1800, key=lambda i: (i.guild_id, i.user.id))
    async def work_slash(self, interaction: discord.Interaction):
        """Slash command for work."""
        reward = self.config.work_reward
        if interaction.guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, interaction.guild.id)
                reward = GuildConfigService.effective(guild_cfg, self.config, 'work_reward')

        async with self.bot.get_session() as session:
            final = await EconomyService.reward(
                session, interaction.user.id, reward, 'work', 'Daily work reward'
            )
            new = await AchievementService.check(session, interaction.user.id, 'work')

        if final > 0:
            embed = EmbedBuilder.success_embed(
                "Work Complete!",
                f"You worked hard and earned {format_coins(final)}!"
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("An error occurred while processing your work reward.")
        await self._announce_achievements(ctx=interaction, new=new)

    @commands.command(name='collect', aliases=['hourly'])
    @check_cooldown('collect', 3600) # 1 hour
    async def collect(self, ctx: commands.Context):
        """Collect hourly reward."""
        reward = 50 # Fixed for now

        async with self.bot.get_session() as session:
            final = await EconomyService.reward(
                session, ctx.author.id, reward, 'collect', 'Hourly collect reward'
            )

        if final > 0:
            embed = EmbedBuilder.success_embed(
                "Collection Complete!",
                f"You collected {format_coins(final)}!"
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("An error occurred while processing your collection.")

    @app_commands.command(name='collect', description='Collect hourly reward')
    @app_commands.checks.cooldown(1, 3600, key=lambda i: (i.guild_id, i.user.id))
    async def collect_slash(self, interaction: discord.Interaction):
        """Slash command for collect."""
        reward = 50 # Fixed for now

        async with self.bot.get_session() as session:
            success = await EconomyUtils.add_money(
                session, interaction.user.id, reward, 'collect', 'Hourly collect reward'
            )

            if success:
                await session.commit()
                embed = EmbedBuilder.success_embed(
                    "Collection Complete!",
                    f"You collected {format_coins(reward)}!"
                )
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("An error occurred while processing your collection.")

    @commands.command(name='daily', aliases=['d'])
    async def daily(self, ctx: commands.Context):
        """Claim your daily reward! Build streaks for bigger bonuses."""
        async with self.bot.get_session() as session:
            base = self.config.daily_reward
            if ctx.guild is not None:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, 'daily_reward')
            ok, msg, reward, streak = await ProgressionService.apply_daily(
                session, ctx.author.id, base
            )
            new = await AchievementService.check(session, ctx.author.id, 'daily')

        if not ok:
            return await ctx.send(msg)
        embed = EmbedBuilder.success_embed(
            "Daily Reward Claimed!",
            f"You claimed your daily reward of {format_coins(reward)}!\n"
            f"Streak: **{streak}** day(s)" + ("— maximum bonus!" if streak >= 7 else ""),
        )
        await ctx.send(embed=embed)
        await self._announce_achievements(ctx, new)

    @app_commands.command(name='daily', description='Claim daily reward')
    async def daily_slash(self, interaction: discord.Interaction):
        """Slash command for daily."""
        async with self.bot.get_session() as session:
            base = self.config.daily_reward
            if interaction.guild is not None:
                guild_cfg = await GuildConfigService.get(session, interaction.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, 'daily_reward')
            ok, msg, reward, streak = await ProgressionService.apply_daily(
                session, interaction.user.id, base
            )
            new = await AchievementService.check(session, interaction.user.id, 'daily')

        if not ok:
            return await interaction.response.send_message(msg)
        embed = EmbedBuilder.success_embed(
            "Daily Reward Claimed!",
            f"You claimed your daily reward of {format_coins(reward)}!\n"
            f"Streak: **{streak}** day(s)" + ("— maximum bonus!" if streak >= 7 else ""),
        )
        await interaction.response.send_message(embed=embed)
        await self._announce_achievements(ctx=interaction, new=new)

    @commands.command(name='weekly', aliases=['week'])
    @check_cooldown('weekly', 604800) # 7 days
    async def weekly(self, ctx: commands.Context):
        """Claim your weekly reward! 7-day cooldown for big bonus."""
        base = self.config.weekly_reward
        if ctx.guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, 'weekly_reward')

        async with self.bot.get_session() as session:
            async with lock_manager.for_user(ctx.author.id):
                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                reward = int(base * ProgressionService._prestige_multiplier(wallet))
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, 'weekly', 'Weekly reward'
                )
                await session.commit()

        embed = EmbedBuilder.success_embed(
            "Weekly Reward Claimed!",
            f"You claimed your weekly reward of {format_coins(reward)}!"
        )
        await ctx.send(embed=embed)

    @app_commands.command(name='weekly', description='Claim weekly reward')
    @app_commands.checks.cooldown(1, 604800, key=lambda i: (i.guild_id, i.user.id))
    async def weekly_slash(self, interaction: discord.Interaction):
        """Slash command for weekly."""
        base = self.config.weekly_reward
        if interaction.guild is not None:
            async with self.bot.get_session() as session:
                guild_cfg = await GuildConfigService.get(session, interaction.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, 'weekly_reward')

        async with self.bot.get_session() as session:
            async with lock_manager.for_user(interaction.user.id):
                wallet = await EconomyUtils.get_or_create_wallet(session, interaction.user.id)
                reward = int(base * ProgressionService._prestige_multiplier(wallet))
                await EconomyUtils.add_money(
                    session, interaction.user.id, reward, 'weekly', 'Weekly reward'
                )
                await session.commit()

        embed = EmbedBuilder.success_embed(
            "Weekly Reward Claimed!",
            f"You claimed your weekly reward of {format_coins(reward)}!"
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name='deposit', aliases=['dep'])
    async def deposit(self, ctx: commands.Context, amount: str):
        """Deposit coins from wallet to bank. Keep your money safe from robbery and crime losses!"""
        try:
            if amount.lower() == 'all':
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
                type='deposit',
                amount=-amount_int,
                description=f'Deposited {amount_int} coins to bank'
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Deposit Successful",
            f"You deposited {format_coins(amount_int)} to your bank."
        )
        await ctx.send(embed=embed)

    @app_commands.command(name='deposit', description='Deposit coins from wallet to bank')
    @app_commands.describe(amount='Amount to deposit (number or "all")')
    async def deposit_slash(self, interaction: discord.Interaction, amount: str):
        """Slash command for deposit."""
        try:
            if amount.lower() == 'all':
                async with self.bot.get_session() as session:
                    wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
                    if wallet and wallet.balance > 0:
                        amount_int = wallet.balance
                    else:
                        await interaction.response.send_message("You don't have any coins to deposit.")
                        return
            else:
                amount_int = int(amount)
                if amount_int <= 0:
                    await interaction.response.send_message("Deposit amount must be positive.")
                    return
        except ValueError:
            await interaction.response.send_message("Invalid amount. Please provide a number or 'all'.")
            return

        async with lock_manager.for_user(interaction.user.id), self.bot.get_session() as session:
            wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
            if not wallet or wallet.balance < amount_int:
                await interaction.response.send_message("You don't have enough coins in your wallet.")
                return

            wallet.balance -= amount_int
            wallet.bank += amount_int

            tx = Transaction(
                user_id=interaction.user.id,
                type='deposit',
                amount=-amount_int,
                description=f'Deposited {amount_int} coins to bank'
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Deposit Successful",
            f"You deposited {format_coins(amount_int)} to your bank."
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name='withdraw', aliases=['wd'])
    async def withdraw(self, ctx: commands.Context, amount: str):
        """Withdraw coins from bank to wallet. Get cash for gambling and transactions!"""
        try:
            if amount.lower() == 'all':
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
                type='withdraw',
                amount=amount_int,
                description=f'Withdrew {amount_int} coins from bank'
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Withdrawal Successful",
            f"You withdrew {format_coins(amount_int)} from your bank."
        )
        await ctx.send(embed=embed)

    @app_commands.command(name='withdraw', description='Withdraw coins from bank to wallet')
    @app_commands.describe(amount='Amount to withdraw (number or "all")')
    async def withdraw_slash(self, interaction: discord.Interaction, amount: str):
        """Slash command for withdraw."""
        try:
            if amount.lower() == 'all':
                async with self.bot.get_session() as session:
                    wallet = await EconomyUtils.get_wallet(session, interaction.user.id)
                    if wallet and wallet.bank > 0:
                        amount_int = wallet.bank
                    else:
                        await interaction.response.send_message("You don't have any coins in your bank.")
                        return
            else:
                amount_int = int(amount)
                if amount_int <= 0:
                    await interaction.response.send_message("Withdraw amount must be positive.")
                    return
        except ValueError:
            await interaction.response.send_message("Invalid amount. Please provide a number or 'all'.")
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
                type='withdraw',
                amount=amount_int,
                description=f'Withdrew {amount_int} coins from bank'
            )
            session.add(tx)
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Withdrawal Successful",
            f"You withdrew {format_coins(amount_int)} from your bank."
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name='transfer', aliases=['pay'])
    async def transfer(self, ctx: commands.Context, user: discord.User, amount: int):
        """Transfer coins to another user."""
        if user == ctx.author:
            await ctx.send("You can't transfer coins to yourself.")
            return

        if amount <= 0:
            await ctx.send("Transfer amount must be positive.")
            return

        # Check for fraud
        suspicious, reason = anti_fraud.is_suspicious(ctx.author.id, amount, 'transfer')
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
                session, ctx.author.id, user.id, amount,
                f'Transfer from {ctx.author.display_name}', tax_rate=tax_rate,
            )

        if success:
            desc = f"You transferred {format_coins(amount)} to {user.mention}."
            if tax:
                desc += f"\n*Transfer tax: {format_coins(tax)}*"
            embed = EmbedBuilder.success_embed("Transfer Successful", desc)
            await ctx.send(embed=embed)
        else:
            await ctx.send("Transfer failed. Check your balance and try again.")

    @app_commands.command(name='transfer', description='Transfer coins to another user')
    @app_commands.describe(user='User to transfer to', amount='Amount to transfer')
    async def transfer_slash(self, interaction: discord.Interaction, user: discord.User, amount: int):
        """Slash command for transfer."""
        if user == interaction.user:
            await interaction.response.send_message("You can't transfer coins to yourself.")
            return

        if amount <= 0:
            await interaction.response.send_message("Transfer amount must be positive.")
            return

        # Check for fraud
        suspicious, reason = anti_fraud.is_suspicious(interaction.user.id, amount, 'transfer')
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
                session, interaction.user.id, user.id, amount,
                f'Transfer from {interaction.user.display_name}', tax_rate=tax_rate,
            )

        if success:
            desc = f"You transferred {format_coins(amount)} to {user.mention}."
            if tax:
                desc += f"\n*Transfer tax: {format_coins(tax)}*"
            embed = EmbedBuilder.success_embed("Transfer Successful", desc)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("Transfer failed. Check your balance and try again.")

    @commands.command(name='leaderboard', aliases=['lb', 'top'])
    async def leaderboard(self, ctx: commands.Context):
        """Show the top 10 richest users."""
        async with self.bot.get_session() as session:
            stmt = select(
                Wallet.user_id,
                (Wallet.balance + Wallet.bank).label('total')
            ).order_by(desc('total')).limit(10)
            result = await session.execute(stmt)
            leaderboard = [(row.user_id, row.total) for row in result.all()]

        embed = EmbedBuilder.leaderboard_embed(leaderboard, "Richest Players")
        await ctx.send(embed=embed)

    @app_commands.command(name='leaderboard', description='Show the leaderboard')
    async def leaderboard_slash(self, interaction: discord.Interaction):
        """Slash command for leaderboard."""
        async with self.bot.get_session() as session:
            stmt = select(
                Wallet.user_id,
                (Wallet.balance + Wallet.bank).label('total')
            ).order_by(desc('total')).limit(10)
            result = await session.execute(stmt)
            leaderboard = [(row.user_id, row.total) for row in result.all()]

        embed = EmbedBuilder.leaderboard_embed(leaderboard, "Richest Players")
        await interaction.response.send_message(embed=embed)

    @commands.command(name='beg', aliases=['b'])
    @check_cooldown('beg', 60) # 1 minute
    async def beg(self, ctx: commands.Context):
        """Beg for coins from strangers. 70% success rate, earn 10-100 coins. 1-minute cooldown."""
        import random

        # 70% chance to get coins
        if random.random() < 0.7:
            reward = random.randint(10, 100)

            responses = [
                f"A kind stranger gave you {format_coins(reward)}!",
                f"Someone felt generous and donated {format_coins(reward)}!",
                f"You found {format_coins(reward)} someone dropped!",
                f"A wealthy person tossed you {format_coins(reward)}!",
                f"You begged successfully and got {format_coins(reward)}!"
            ]

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, 'beg', 'Begging reward'
                )
                await session.commit()

            embed = EmbedBuilder.success_embed("Success!", random.choice(responses))
            await ctx.send(embed=embed)
        else:
            responses = [
                "Everyone ignored you... ",
                "People walked past you without looking.",
                "No one gave you anything today.",
                "The streets are empty...",
                "Someone told you to get a job!"
            ]
            embed = discord.Embed(
                title="No Luck",
                description=random.choice(responses),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    @commands.command(name='crime', aliases=['c'])
    @check_cooldown('crime', 300) # 5 minutes
    async def crime(self, ctx: commands.Context):
        """Commit a crime for big rewards! 40% success (300-2k coins), 60% fail (200-600 fine). 5-minute cooldown."""
        import random

        # 40% success rate
        success = random.random() < 0.4

        crimes = [
            ("robbed a bank", 500, 1500),
            ("hacked a corporation", 800, 2000),
            ("stole a rare painting", 600, 1800),
            ("smuggled contraband", 400, 1200),
            ("pickpocketed tourists", 300, 800),
        ]

        crime_desc, min_reward, max_reward = random.choice(crimes)

        if ctx.guild is not None:
            async with self.bot.get_session() as session:
                if guard := await self._guard_error(ctx, session):
                    return await ctx.send(guard)

        if success:
            reward = random.randint(min_reward, max_reward)

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, 'crime', f'Crime: {crime_desc}'
                )
                await session.commit()

            embed = EmbedBuilder.success_embed(
                "Crime Success!",
                f"You {crime_desc} and got away with {format_coins(reward)}!"
            )
            await ctx.send(embed=embed)
        else:
            fine = random.randint(200, 600)

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                if wallet.balance >= fine:
                    wallet.balance -= fine
                    await session.commit()
                    loss_msg = f"You were caught and fined {format_coins(fine)}!"
                else:
                    loss_msg = "You were caught but had no money to pay the fine!"

            embed = discord.Embed(
                title="Caught!",
                description=f"You tried to {crime_desc} but got caught!\n{loss_msg}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, 'crime')
        await self._announce_achievements(ctx, new)

    @commands.command(name='rob', aliases=['steal'])
    @check_cooldown('rob', 600) # 10 minutes
    async def rob(self, ctx: commands.Context, user: discord.User):
        """Try to rob another user (risky!)."""
        import random

        if user == ctx.author:
            return await ctx.send("You can't rob yourself!")

        if user.bot:
            return await ctx.send("You can't rob bots!")

        async with lock_manager.for_users(ctx.author.id, user.id), self.bot.get_session() as session:
            if guard := await self._guard_error(ctx, session):
                return await ctx.send(guard)
            robber_wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            victim_wallet = await EconomyUtils.get_or_create_wallet(session, user.id)

            # Need at least 200 coins to attempt robbery
            if robber_wallet.balance < 200:
                return await ctx.send("You need at least 200 coins to attempt a robbery!")

            # Victim needs coins to rob
            if victim_wallet.balance < 100:
                return await ctx.send(f"{user.display_name} doesn't have enough coins to rob!")

            # 35% success rate
            success = random.random() < 0.35

            if success:
                # Rob 10-30% of victim's balance
                rob_amount = int(victim_wallet.balance * random.uniform(0.1, 0.3))
                rob_amount = min(rob_amount, 5000) # Cap at 5000

                victim_wallet.balance -= rob_amount
                robber_wallet.balance += rob_amount
                session.add(
                    Transaction(user_id=ctx.author.id, type='rob', amount=rob_amount,
                                description=f'Robbed {user.display_name}')
                )
                session.add(
                    Transaction(user_id=user.id, type='rob_loss', amount=-rob_amount,
                                description=f'Robbed by {ctx.author.display_name}')
                )
                await session.commit()

                embed = EmbedBuilder.success_embed(
                    "Robbery Success!",
                    f"You robbed {format_coins(rob_amount)} from {user.mention}!"
                )
                await ctx.send(embed=embed)
            else:
                # Failed - lose 200-500 coins
                fine = random.randint(200, 500)
                fine = min(fine, robber_wallet.balance)

                robber_wallet.balance -= fine
                victim_wallet.balance += fine // 2 # Victim gets half
                session.add(
                    Transaction(user_id=ctx.author.id, type='rob_fail', amount=-fine,
                                description=f'Caught robbing {user.display_name}')
                )
                await session.commit()

                embed = discord.Embed(
                    title="Robbery Failed!",
                    description=f"You were caught trying to rob {user.mention}!\n"
                               f"You lost {format_coins(fine)} and they got {format_coins(fine // 2)}!",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, 'rob')
        await self._announce_achievements(ctx, new)

    @commands.command(name='gamble', aliases=['bet'])
    @check_cooldown('gamble', 30) # 30 seconds
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
                return await ctx.send(f"You don't have enough coins! Balance: {format_coins(wallet.balance)}")

            # Deduct bet
            wallet.balance -= amount
            session.add(
                Transaction(user_id=ctx.author.id, type='gamble', amount=amount,
                            description='Gamble wager')
            )

            # 45% win rate
            won = random.random() < 0.45

            if won:
                payout = amount * 2
                wallet.balance += payout
                session.add(
                    Transaction(user_id=ctx.author.id, type='gamble_win', amount=payout,
                                description='Gamble payout')
                )
                await session.commit()

                embed = discord.Embed(
                    title="GAMBLE",
                    description="━━━━━━━━━━━━━━━━━━━━━━",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="OUTCOME",
                    value=f"```diff\n+ WIN\n```\n**Payout:** {format_coins(payout)}\n**Profit:** +{format_coins(amount)}",
                    inline=False
                )
                embed.add_field(
                    name="BALANCE",
                    value=f"```\n{wallet.balance:,} coins\n```",
                    inline=False
                )
            else:
                await session.commit()

                embed = discord.Embed(
                    title="GAMBLE",
                    description="━━━━━━━━━━━━━━━━━━━━━━",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="OUTCOME",
                    value=f"```diff\n- LOSS\n```\n**Lost:** {format_coins(amount)}",
                    inline=False
                )
                embed.add_field(
                    name="BALANCE",
                    value=f"```\n{wallet.balance:,} coins\n```",
                    inline=False
                )

            embed.set_footer(text="Economy • Quick Gamble")
            await ctx.send(embed=embed)

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, 'gamble')
        await self._announce_achievements(ctx, new)

    @commands.command(name='richest', aliases=['top10', 'baltop'])
    async def richest(self, ctx: commands.Context):
        """Show the top 15 richest users with detailed stats."""
        async with self.bot.get_session() as session:
            stmt = select(
                Wallet.user_id,
                Wallet.balance,
                Wallet.bank,
                (Wallet.balance + Wallet.bank).label('total')
            ).order_by(desc('total')).limit(15)
            result = await session.execute(stmt)
            users = result.all()

        if not users:
            return await ctx.send("No users found in the economy!")

        embed = discord.Embed(
            title="TOP 15 RICHEST PLAYERS",
            description="━━━━━━━━━━━━━━━━━━━━━━",
            color=discord.Color.gold()
        )

        for idx, user_data in enumerate(users, 1):
            try:
                user = await self.bot.fetch_user(user_data.user_id)
                name = user.display_name
            except discord.HTTPException:
                name = f"User {user_data.user_id}"

            medal = f"#{idx}"

            embed.add_field(
                name=f"{medal} {name}",
                value=f"```\nTotal: {user_data.total:,}\nWallet: {user_data.balance:,}\nBank: {user_data.bank:,}\n```",
                inline=True
            )

        embed.set_footer(text="Economy • Leaderboard")
        await ctx.send(embed=embed)

    @commands.command(name='give', aliases=['gift'])
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

        async with lock_manager.for_users(ctx.author.id, user.id), self.bot.get_session() as session:
            sender_wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)

            if sender_wallet.balance < amount:
                return await ctx.send(f"You don't have enough coins! Balance: {format_coins(sender_wallet.balance)}")

            # Transfer
            sender_wallet.balance -= amount
            await EconomyUtils.add_money(
                session, user.id, amount, 'gift', f'Gift from {ctx.author.display_name}'
            )
            await session.commit()

        embed = EmbedBuilder.success_embed(
            "Gift Sent!",
            f"You gave {format_coins(amount)} to {user.mention}!"
        )
        await ctx.send(embed=embed)

    @commands.command(name='search', aliases=['scavenge'])
    @check_cooldown('search', 45) # 45 seconds
    async def search(self, ctx: commands.Context):
        """Search random places for coins. 80% success rate, earn 5-120 coins from 8 locations. 45-second cooldown."""
        import random

        locations = [
            ("couch cushions", 20, 80),
            ("park bench", 30, 100),
            ("parking lot", 15, 60),
            ("vending machine", 25, 90),
            ("library books", 10, 50),
            ("trash bin", 5, 40),
            ("car seats", 30, 110),
            ("beach sand", 40, 120),
        ]

        location, min_reward, max_reward = random.choice(locations)

        # 80% chance to find something
        if random.random() < 0.8:
            reward = random.randint(min_reward, max_reward)

            async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
                await EconomyUtils.add_money(
                    session, ctx.author.id, reward, 'search', f'Searched {location}'
                )
                await session.commit()

            embed = EmbedBuilder.success_embed(
                "Found!",
                f"You searched the **{location}** and found {format_coins(reward)}!"
            )
        else:
            embed = discord.Embed(
                title="Nothing Found",
                description=f"You searched the **{location}** but found nothing...",
                color=discord.Color.orange()
            )

        async with self.bot.get_session() as session:
            new = await AchievementService.check(session, ctx.author.id, 'search')
        await ctx.send(embed=embed)
        await self._announce_achievements(ctx, new)

    @commands.command(name='profile', aliases=['prof', 'stats'])
    async def profile(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """View detailed profile and economy stats."""
        target = user or ctx.author

        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, target.id)

            # Get transaction count
            tx_stmt = select(func.count(Transaction.id)).where(Transaction.user_id == target.id)
            tx_result = await session.execute(tx_stmt)
            tx_count = tx_result.scalar() or 0

            # Get total earned
            earn_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.user_id == target.id,
                Transaction.amount > 0
            )
            earn_result = await session.execute(earn_stmt)
            total_earned = earn_result.scalar() or 0
            await session.commit() # Persist wallet if it was just created

        async with self.bot.get_session() as session:
            achievement_count = await AchievementService.count(session, target.id)
            inv_rows = await ItemService.list_inventory(session, target.id)
            inv_count = sum(inv.quantity for inv, _ in inv_rows)
            inv_value = await ItemService.inventory_value(session, target.id)

        total_wealth = EconomyService.networth(wallet, inv_value)

        embed = discord.Embed(
            title=f"{target.display_name}'s Profile",
            description="━━━━━━━━━━━━━━━━━━━━━━",
            color=discord.Color.blue()
        )

        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(
            name="WEALTH",
            value=f"```\nTotal: {total_wealth:,}\nWallet: {wallet.balance:,}\nBank: {wallet.bank:,}\n```",
            inline=False
        )

        embed.add_field(
            name="STATISTICS",
            value=f"```\nTransactions: {tx_count}\nTotal Earned: {total_earned:,}\nInventory: {inv_count} items\n```",
            inline=False
        )

        embed.add_field(
            name="PROGRESSION",
            value=f"```\nPrestige: {wallet.prestige or 0}\nReputation: {wallet.reputation or 0}\nDaily Streak: {wallet.daily_streak or 0}\nAchievements: {achievement_count}/{len(ACHIEVEMENTS)}\n```",
            inline=False
        )

        embed.set_footer(text=f"Economy • User ID: {target.id}")
        await ctx.send(embed=embed)

    @commands.command(name='transactions', aliases=['history', 'tx', 'logs'])
    async def transactions(self, ctx: commands.Context, user: Optional[discord.User] = None, limit: int = 10):
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
                color=discord.Color.dark_teal(),
            )
            for tx in txs[start:start + per_page]:
                sign = "+" if tx.amount >= 0 else ""
                embed.add_field(
                    name=f"{tx.type} • {sign}{tx.amount:,}",
                    value=f"{tx.description or '—'}\n*{tx.timestamp:%Y-%m-%d %H:%M} UTC*",
                    inline=False,
                )
            embed.set_footer(text=f"Page {start // per_page + 1}/{(len(txs) - 1) // per_page + 1}")
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @commands.command(name='prestige', aliases=['prest'])
    async def prestige(self, ctx: commands.Context):
        """Reset your wealth for +1 prestige (requires 1,000,000 net worth). +2% rewards per level."""
        async with self.bot.get_session() as session:
            ok, msg = await ProgressionService.prestige(session, ctx.author.id)
            new = await AchievementService.check(session, ctx.author.id, 'prestige') if ok else []

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
        lines = "\n".join(
            f"**{a['name']}** — {a['desc']}" for a in new
        )
        embed = discord.Embed(
            title="Achievements Unlocked!",
            description=lines,
            color=discord.Color.gold(),
        )
        send = getattr(ctx, "send", None)
        if send is not None:
            await send(embed=embed)
        else:
            try:
                await ctx.followup.send(embed=embed)
            except Exception:
                await ctx.response.send_message(embed=embed)

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
