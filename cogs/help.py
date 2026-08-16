"""
Help cog: categorized overview + per-command detail with gameplay guides.

Replaces discord.py's default help command:

- ``!help`` lists every command grouped by category.
- ``!help <command>`` usage, aliases, description, and (for casino games) a
  step-by-step "How to play" guide, e.g. ``!help keno`` explains how to play.

The prefix is resolved at runtime (``{prefix}`` placeholders in the guides),
so it works with ``!``, ``+``, or any configured prefix.
"""

from typing import Dict, List, Optional

import discord
from discord.ext import commands

from bot import Fun2OoshBot
from utils.helpers import COLOR_ERROR, COLOR_INFO

# Cog class name -> category label shown in the overview.
CATEGORY_LABELS: Dict[str, str] = {
    "Economy": "Economy",
    "Casino": "Casino & Gambling",
    "Shop": "Shop & Items",
    "Activities": "Progression & Activities",
    "LotteryCog": "Lottery",
    "Admin": "Admin",
}

# "How to play" guides for the casino games, keyed by command name.
# `{prefix}` is replaced with the bot's actual prefix at render time.
GAME_GUIDES: Dict[str, str] = {
    "blackjack": (
        "**Goal:** beat the dealer's hand without going over 21.\n"
        "**Cards:** 2-10 are face value, J/Q/K = 10, Ace = 1 or 11. You start with 2 cards "
        "and use the buttons to **Hit** (take a card), **Stand** (keep your hand), or "
        "**Double Down** (double the bet for one more card). The dealer stands on 17+.\n"
        "**Payouts:** natural blackjack (Ace + 10) = 3:2, win = 2x, push = bet back.\n"
        "**Usage:** `{prefix}blackjack <bet>` (10s cooldown)"
    ),
    "poker": (
        "**Goal:** make the best 5-card poker hand to beat the dealer.\n"
        "**How:** Texas Hold'em vs the dealer your 2 hole cards plus shared community "
        "cards form your hand. Standard rankings apply, royal flush down to high card.\n"
        "**Payout:** wins pay up to 2x your bet.\n"
        "**Usage:** `{prefix}poker <bet>`"
    ),
    "roulette": (
        "**Goal:** predict where the ball lands (0-36).\n"
        "**How:** `{prefix}roulette <amount> <bet>` bet on `red`, `black`, `odd`, `even`, "
        "`low` (1-18), `high` (19-36), or a specific `number` (0-36). It's a **shared table**: "
        "the wheel spins 15s after the last bet (max 1 minute) and everyone in the channel "
        "can join the same round.\n"
        "**Payouts:** numbers pay 36x, all other bets pay 2x.\n"
        "**Usage:** `{prefix}roulette 100 red`"
    ),
    "slots": (
        "**Goal:** line up matching symbols.\n"
        "**How:** `{prefix}slots <bet>` 3 reels spin; match 3 of the same symbol.\n"
        "**Payouts:** three-of-a-kind by symbol diamond 50x, seven 30x, star 20x, bell 15x, "
        "grape 10x, orange 8x, lemon 5x, cherry 3x. Two matching reels pay 1/3 of that.\n"
        "**Usage:** `{prefix}slots 100`"
    ),
    "coinflip": (
        "**Goal:** call the coin.\n"
        "**How:** `{prefix}coinflip <heads|tails> <bet>` a fair 50/50 flip.\n"
        "**Payout:** win = 2x your bet.\n"
        "**Usage:** `{prefix}coinflip heads 100`"
    ),
    "dice": (
        "**Goal:** predict the total of two dice (2-12).\n"
        "**How:** `{prefix}dice <prediction> <bet>` predict `over` (8+), `under` (6-), "
        "`seven`, or an exact `number` (2-12).\n"
        "**Payouts:** over/under 2x, seven 4x, exact number 10x.\n"
        "**Usage:** `{prefix}dice over 100`"
    ),
    "crash": (
        "**Goal:** cash out before the multiplier crashes.\n"
        "**How:** `{prefix}crash <bet> <target>` set a cash-out target between 1.1x and 100x. "
        "The multiplier climbs until it crashes or your target is reached.\n"
        "**Payout:** bet x target if you cash out in time, otherwise you lose the bet.\n"
        "**Usage:** `{prefix}crash 100 2.5`"
    ),
    "russianroulette": (
        "**Goal:** survive the chamber.\n"
        "**How:** `{prefix}russianroulette <bet>` a 1-in-6 chance of losing.\n"
        "**Payout:** survive = 5x your bet. High risk, high reward!\n"
        "**Usage:** `{prefix}russianroulette 100`"
    ),
    "war": (
        "**Goal:** beat the dealer's card.\n"
        "**How:** `{prefix}war <bet>` you and the dealer each draw a card; the higher card wins.\n"
        "**Payout:** win = 2x, tie = bet returned.\n"
        "**Usage:** `{prefix}war 100`"
    ),
    "baccarat": (
        "**Goal:** bet on the hand closest to 9.\n"
        "**How:** `{prefix}baccarat <player|banker|tie> <amount>` cards are summed and only "
        "the last digit counts; 10s and face cards count as 0. Third-card rules apply.\n"
        "**Payouts:** player 2x, banker 1.95x (5% commission), tie 8x.\n"
        "**Usage:** `{prefix}baccarat banker 100`"
    ),
    "hilo": (
        "**Goal:** predict the next card.\n"
        "**How:** `{prefix}hilo <high|low> <bet>` guess whether the next card is higher or "
        "lower than the current one.\n"
        "**Payout:** win = 2x; a matching card returns your bet.\n"
        "**Usage:** `{prefix}hilo high 100`"
    ),
    "keno": (
        "**Goal:** match your numbers to the draw.\n"
        "**How:** `{prefix}keno <5 numbers 1-80> <bet>` pick exactly 5 unique numbers between "
        "1 and 80; 20 numbers are then drawn.\n"
        "**Payouts:** 5 matches = 50x, 4 = 10x, 3 = 3x, 2 = 1x (bet back).\n"
        "**Usage:** `{prefix}keno 5 12 23 45 67 100`"
    ),
    "gamble": (
        "**Goal:** double up on a coin flip.\n"
        "**How:** `{prefix}gamble <amount>` 45% chance to double your money, 55% to lose it all.\n"
        "**Usage:** `{prefix}gamble 100`"
    ),
    "lottery": (
        "**Goal:** win the server jackpot.\n"
        "**How:** `{prefix}lottery buy <n>` each ticket costs the configurable price "
        "(default 50 💎). A scheduled draw happens every `draw_interval_seconds` "
        "(default 24h) and one random ticket takes the whole pot.\n"
        "**Usage:** `{prefix}lottery` to see the pot, `{prefix}lottery buy 5` to buy 5 tickets"
    ),
    "casinoleaderboard": (
        "**Goal:** see the casino's biggest winners.\n"
        "**How:** `{prefix}casinoleaderboard` ranks users by lifetime casino wins "
        "(biggest single win, total wins, and net across all games).\n"
        "**Usage:** `{prefix}casinoleaderboard`"
    ),
}


class HelpCog(commands.Cog):
    """Categorized help with per-command gameplay guides."""

    def __init__(self, bot: Fun2OoshBot):
        self.bot = bot

    # ------------------------------------------------------------ overview

    def _grouped(self) -> Dict[str, List[str]]:
        """Map category label -> sorted command names (help itself excluded)."""
        groups: Dict[str, List[str]] = {}
        for command in self.bot.commands:
            if command.name == "help" or command.hidden:
                continue
            label = CATEGORY_LABELS.get(command.cog_name or "", command.cog_name or "Other")
            groups.setdefault(label, []).append(command.name)
        for names in groups.values():
            names.sort()
        return groups

    @commands.command(name="help", aliases=["h", "commands", "cmds"])
    async def help(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Show help. `help <command>` gives usage and how-to-play, e.g. `help keno`."""
        if command is None:
            await self._overview(ctx)
            return
        await self._command_detail(ctx, command.strip())

    async def _overview(self, ctx: commands.Context) -> None:
        prefix = ctx.clean_prefix
        groups = self._grouped()
        embed = discord.Embed(
            title="Fun2Oosh Help",
            description=(
                f"Prefix: `{prefix}` most commands also work as slash commands.\n"
                f"Type `{prefix}help <command>` for usage and how to play, "
                f"e.g. `{prefix}help keno` or `{prefix}help blackjack`."
            ),
            color=COLOR_INFO,
        )
        for label, names in groups.items():
            embed.add_field(
                name=label,
                value=", ".join(f"`{name}`" for name in names),
                inline=False,
            )
        total = sum(len(names) for names in groups.values())
        embed.set_footer(text=f"{len(groups)} categories • {total} commands")
        await ctx.send(embed=embed)

    # -------------------------------------------------------------- detail

    async def _command_detail(self, ctx: commands.Context, name: str) -> None:
        prefix = ctx.clean_prefix
        command = self.bot.get_command(name)
        if command is None or command.hidden:
            embed = discord.Embed(
                description=(
                    f"Unknown command `{name}`. Try `{prefix}help` for the full list, "
                    f"or `{prefix}help <command>` for one command."
                ),
                color=COLOR_ERROR,
            )
            await ctx.send(embed=embed)
            return

        signature = f"{prefix}{command.name}"
        if command.signature:
            signature += f" {command.signature}"

        embed = discord.Embed(title=f"`{signature}`", color=COLOR_INFO)
        embed.add_field(name="Category", value=command.cog_name or "Other", inline=True)
        if command.aliases:
            embed.add_field(
                name="Aliases",
                value=", ".join(f"`{prefix}{alias}`" for alias in command.aliases),
                inline=True,
            )
        description = command.description or command.help or "No description provided."
        embed.add_field(name="Description", value=description, inline=False)

        guide = GAME_GUIDES.get(command.name)
        if guide:
            embed.add_field(
                name="How to Play",
                value=guide.format(prefix=prefix),
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: Fun2OoshBot):
    """Setup the help cog (replaces the default help command)."""
    bot.help_command = None
    await bot.add_cog(HelpCog(bot))
