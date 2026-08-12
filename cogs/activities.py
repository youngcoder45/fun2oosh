"""
Activities & progression cog.

Commands: hunt, fish, mine, monthly, networth, rep, achievements.
"""

import random
from typing import List, Optional

import discord
from discord.ext import commands

from bot import Fun2OoshBot
from services.economy import EconomyService, GuardService
from services.events import event_message
from services.guild import GuildConfigService
from services.items import ItemService
from services.progression import ACHIEVEMENTS, AchievementService, ProgressionService
from utils.config import Config
from utils.cooldowns import check_cooldown
from utils.economy_utils import EconomyUtils
from utils.helpers import (
    COLOR_ERROR,
    COLOR_INFO,
    EmbedBuilder,
    event_names,
    format_coins,
)

# activity: (success_rate, min_reward, max_reward, failure_text, tool_key, cooldown)
ACTIVITIES = {
    "hunt": (0.60, 30, 180, "The prey got away...", "hunt", 45),
    "fish": (0.55, 20, 150, "The fish escaped...", "fish", 45),
    "mine": (0.50, 40, 250, "The tunnel collapsed...", "mine", 60),
}

# activity key -> lowercase UnbelievaBoat-style author header
ACTIVITY_HEADERS = {"hunt": "hunted", "fish": "fished", "mine": "mined"}

SUCCESS_FALLBACKS = {
    "hunt": "You hunted down a wild animal and sold it for {amount} {currency}.",
    "fish": "You caught a big fish and sold it for {amount} {currency}.",
    "mine": "You struck a rich vein and mined {amount} {currency} worth of ore!",
}


class Activities(commands.Cog):
    """Risk commands, monthly rewards, reputation, and achievements."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config

    # ----------------------------------------------------------------- guards

    async def _guard_error(self, ctx: commands.Context) -> Optional[str]:
        if ctx.guild is None:
            return None
        async with self.bot.get_session() as session:
            guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
        return GuardService.check_user_allowed(ctx.author, guild_cfg)

    # ------------------------------------------------------------- activities

    async def _run_activity(self, ctx: commands.Context, key: str) -> None:
        success_rate, r_min, r_max, fail_text, tool, _cd = ACTIVITIES[key]
        user, guild = event_names(ctx.author, ctx.guild)
        header = ACTIVITY_HEADERS[key]
        async with self.bot.get_session() as session:
            tool_mult = await ItemService.tool_multiplier(session, ctx.author.id, tool)
            reward = random.randint(r_min, r_max)

            if random.random() < success_rate:
                final = await EconomyService.reward(
                    session, ctx.author.id, int(reward * tool_mult), key, f"{key.title()} reward"
                )
                embed = EmbedBuilder.activity_embed(
                    header,
                    event_message(
                        key, final, self.config.currency_name, user, guild,
                        fallback=SUCCESS_FALLBACKS[key],
                    ),
                )
            else:
                embed = EmbedBuilder.activity_embed(
                    header,
                    event_message(
                        f"{key}_failure", 0, self.config.currency_name, user, guild,
                        fallback=fail_text,
                    ),
                    color=COLOR_ERROR,
                )

            new = await AchievementService.check(session, ctx.author.id, key)
        await ctx.send(embed=embed)
        await self._announce_achievements(ctx, new)

    @commands.command(name="hunt")
    @check_cooldown("hunt", 45)
    async def hunt(self, ctx: commands.Context):
        """Hunt for wild game. 60% success, earn 30-180 coins (more with a rifle)."""
        if guard := await self._guard_error(ctx):
            return await ctx.send(guard)
        await self._run_activity(ctx, "hunt")

    @commands.command(name="fish", aliases=["fishing"])
    @check_cooldown("fish", 45)
    async def fish(self, ctx: commands.Context):
        """Go fishing. 55% success, earn 20-150 coins (more with a rod)."""
        if guard := await self._guard_error(ctx):
            return await ctx.send(guard)
        await self._run_activity(ctx, "fish")

    @commands.command(name="mine", aliases=["mining"])
    @check_cooldown("mine", 60)
    async def mine(self, ctx: commands.Context):
        """Mine for ore. 50% success, earn 40-250 coins (more with a pickaxe)."""
        if guard := await self._guard_error(ctx):
            return await ctx.send(guard)
        await self._run_activity(ctx, "mine")

    # ---------------------------------------------------------------- monthly

    @commands.command(name="monthly", aliases=["month"])
    async def monthly(self, ctx: commands.Context):
        """Claim your monthly reward! 30-day cooldown."""
        async with self.bot.get_session() as session:
            base = self.config.monthly_reward
            if ctx.guild is not None:
                guild_cfg = await GuildConfigService.get(session, ctx.guild.id)
                base = GuildConfigService.effective(guild_cfg, self.config, "monthly_reward")

            ok, msg = await ProgressionService.apply_monthly(session, ctx.author.id, base)
        if ok:
            embed = EmbedBuilder.success_embed("Monthly Reward Claimed!", msg)
            await ctx.send(embed=embed)
        else:
            await ctx.send(msg)

    # ---------------------------------------------------------------- networth

    @commands.command(name="networth", aliases=["net"])
    async def networth(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """View your total net worth (wallet + bank + inventory)."""
        target = user or ctx.author
        async with self.bot.get_session() as session:
            wallet = await EconomyUtils.get_or_create_wallet(session, target.id)
            inv_value = await ItemService.inventory_value(session, target.id)
            total = EconomyService.networth(wallet, inv_value)
            await session.commit()
        embed = discord.Embed(
            title=f"{target.display_name}'s Net Worth",
            color=COLOR_INFO,
        )
        embed.add_field(name="Wallet", value=format_coins(wallet.balance), inline=True)
        embed.add_field(name="Bank", value=format_coins(wallet.bank), inline=True)
        embed.add_field(name="Inventory", value=format_coins(inv_value), inline=True)
        embed.add_field(name="Total", value=f"**{format_coins(total)}**", inline=False)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------ reputation

    @commands.command(name="rep", aliases=["reputation"])
    @check_cooldown("rep", 43200)  # 12 hours per giver
    async def rep(self, ctx: commands.Context, user: discord.User):
        """Give reputation to a user (12h cooldown)."""
        if ctx.guild is None:
            return await ctx.send("Reputation can only be given in servers.")
        if user == ctx.author:
            return await ctx.send("You can't give reputation to yourself.")
        if user.bot:
            return await ctx.send("You can't give reputation to bots.")

        async with self.bot.get_session() as session:
            await ProgressionService.give_reputation(session, user.id)
            wallet = await EconomyUtils.get_or_create_wallet(session, user.id)
            count = wallet.reputation or 0

        embed = EmbedBuilder.success_embed(
            "Reputation Given!",
            f"You gave a reputation point to {user.mention} , they now have **{count}**.",
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------- achievements

    @commands.command(name="achievements", aliases=["ach", "badges"])
    async def achievements(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """View your unlocked achievements."""
        target = user or ctx.author
        async with self.bot.get_session() as session:
            unlocked = set(await AchievementService.unlocked_ids(session, target.id))

        embed = discord.Embed(
            title=f"{target.display_name}'s Achievements",
            description=f"**{len(unlocked)}/{len(ACHIEVEMENTS)}** unlocked",
            color=discord.Color.gold(),
        )
        for aid, meta in ACHIEVEMENTS.items():
            done = aid in unlocked
            embed.add_field(
                name=f"{'UNLOCKED' if done else 'LOCKED'} {meta['name']}",
                value=f"{meta['desc']}" if done else f"Locked - {meta['desc']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    # --------------------------------------------------------------- helpers

    @staticmethod
    async def _announce_achievements(ctx: commands.Context, new_achievements: List[dict]) -> None:
        if not new_achievements:
            return
        lines = "\n".join(f"**{a['name']}** - {a['desc']}" for a in new_achievements)
        embed = EmbedBuilder.gold_embed("Achievements Unlocked!", lines)
        await ctx.send(embed=embed)


async def setup(bot: Fun2OoshBot):
    """Setup the activities cog."""
    await bot.add_cog(Activities(bot, bot.config))
