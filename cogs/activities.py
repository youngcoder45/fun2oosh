"""
Activities & progression cog.

Commands: hunt, fish, mine, monthly, networth, rep, achievements.

Hunt/fish/mine tuning (success rate, rewards, cooldown, insurance cost on
failure) comes from ``data/config.json`` -> ``activities``.
"""

import random
from typing import Dict, List, Optional

import discord
from discord.ext import commands

from bot import Fun2OoshBot
from models import Transaction
from services.economy import EconomyService, GuardService
from services.events import event_message
from services.guild import GuildConfigService
from services.items import ItemService
from services.locks import lock_manager
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
from utils.pagination import PaginationView
from utils.runtime_config import activity as activity_config

EMOJI_UNLOCK = "<:unlock:1538264274353393674>"
EMOJI_LOCK = "<:lock:1538264325914103938>"

# Built-in fallbacks, used only when data/config.json is missing a key.
ACTIVITY_DEFAULTS: Dict[str, dict] = {
    "hunt": {
        "success_rate": 0.60,
        "min_reward": 30,
        "max_reward": 180,
        "cooldown_seconds": 45,
        "insurance_cost": 15,
        "tool": "hunt",
        "failure_text": "The prey got away...",
    },
    "fish": {
        "success_rate": 0.55,
        "min_reward": 20,
        "max_reward": 150,
        "cooldown_seconds": 45,
        "insurance_cost": 10,
        "tool": "fish",
        "failure_text": "The fish escaped...",
    },
    "mine": {
        "success_rate": 0.50,
        "min_reward": 40,
        "max_reward": 250,
        "cooldown_seconds": 60,
        "insurance_cost": 20,
        "tool": "mine",
        "failure_text": "The tunnel collapsed...",
    },
}

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
        cfg = {**ACTIVITY_DEFAULTS[key], **activity_config(key)}
        success_rate = float(cfg["success_rate"])
        r_min = int(cfg["min_reward"])
        r_max = int(cfg["max_reward"])
        tool = cfg["tool"]
        fail_text = cfg["failure_text"]
        insurance = int(cfg.get("insurance_cost") or 0)

        user, guild = event_names(ctx.author, ctx.guild)
        async with self.bot.get_session() as session:
            tool_mult = await ItemService.tool_multiplier(session, ctx.author.id, tool)
            reward = random.randint(r_min, r_max)

            if random.random() < success_rate:
                final = await EconomyService.reward(
                    session, ctx.author.id, int(reward * tool_mult), key, f"{key.title()} reward"
                )
                embed = EmbedBuilder.activity_embed(
                    event_message(
                        key,
                        final,
                        self.config.currency_name,
                        user,
                        guild,
                        fallback=SUCCESS_FALLBACKS[key],
                    ),
                    user=ctx.author,
                )
            else:
                # Failure: pay the configured insurance/health cost if any.
                paid = 0
                if insurance > 0:
                    async with lock_manager.for_user(ctx.author.id):
                        wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                        paid = min(insurance, wallet.balance or 0)
                        if paid > 0:
                            wallet.balance -= paid
                            session.add(
                                Transaction(
                                    user_id=ctx.author.id,
                                    type=f"{key}_insurance",
                                    amount=-paid,
                                    description=f"{key.title()} insurance",
                                )
                            )
                            await session.commit()

                message = event_message(
                    f"{key}_failure",
                    0,
                    self.config.currency_name,
                    user,
                    guild,
                    fallback=fail_text,
                )
                if paid > 0:
                    message += f"\nInsurance covered the damage for {format_coins(paid)}."
                elif insurance > 0:
                    message += "\nYou had no coins to cover your insurance."
                embed = EmbedBuilder.activity_embed(
                    message,
                    color=COLOR_ERROR,
                    user=ctx.author,
                )

            new = await AchievementService.check(session, ctx.author.id, key)
        await ctx.send(embed=embed)
        await self._announce_achievements(ctx, new)

    @commands.command(name="hunt")
    @check_cooldown("hunt", lambda: int(activity_config("hunt").get("cooldown_seconds", 45)))
    async def hunt(self, ctx: commands.Context):
        """Hunt for wild game. Success rate and rewards are configurable in config.json."""
        if guard := await self._guard_error(ctx):
            return await ctx.send(guard)
        await self._run_activity(ctx, "hunt")

    @commands.command(name="fish", aliases=["fishing"])
    @check_cooldown("fish", lambda: int(activity_config("fish").get("cooldown_seconds", 45)))
    async def fish(self, ctx: commands.Context):
        """Go fishing. Success rate and rewards are configurable in config.json."""
        if guard := await self._guard_error(ctx):
            return await ctx.send(guard)
        await self._run_activity(ctx, "fish")

    @commands.command(name="mine", aliases=["mining"])
    @check_cooldown("mine", lambda: int(activity_config("mine").get("cooldown_seconds", 60)))
    async def mine(self, ctx: commands.Context):
        """Mine for ore. Success rate and rewards are configurable in config.json."""
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
        embed = discord.Embed(color=COLOR_INFO)
        EmbedBuilder.set_author_from_user(embed, target)
        embed.add_field(name="Wallet", value=format_coins(wallet.balance), inline=True)
        embed.add_field(name="Bank", value=format_coins(wallet.bank), inline=True)
        embed.add_field(name="Inventory", value=format_coins(inv_value), inline=True)
        embed.add_field(name="Total", value=f"**{format_coins(total)}**", inline=False)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------ reputation

    @commands.command(name="rep", aliases=["reputation"])
    @check_cooldown("rep", lambda: int(activity_config("rep").get("cooldown_seconds", 43200)))
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
        """View your unlocked achievements (paginated with buttons)."""
        target = user or ctx.author
        async with self.bot.get_session() as session:
            unlocked = set(await AchievementService.unlocked_ids(session, target.id))

        items = list(ACHIEVEMENTS.items())
        pages = []
        per_page = 8
        for start in range(0, len(items), per_page):
            embed = discord.Embed(
                title=f"{target.display_name}'s Achievements",
                description=f"**{len(unlocked)}/{len(ACHIEVEMENTS)}** unlocked",
                color=discord.Color.default(),
            )
            for aid, meta in items[start : start + per_page]:
                done = aid in unlocked
                embed.add_field(
                    name=f"{EMOJI_UNLOCK if done else EMOJI_LOCK} {meta['name']}",
                    value=f"{meta['desc']}" if done else f"Locked - {meta['desc']}",
                    inline=False,
                )
            embed.set_footer(
                text=f"Page {start // per_page + 1}/{(len(items) - 1) // per_page + 1}"
            )
            pages.append(embed)

        view = PaginationView(pages, owner_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

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
