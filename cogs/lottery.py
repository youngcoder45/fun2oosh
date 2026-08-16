"""
Lottery cog: per-guild jackpot.

- ``!lottery`` — show the current pot, ticket price, and next draw.
- ``!lottery buy <n>`` — buy tickets (price from ``data/config.json``).
- A background task draws every ``draw_interval_seconds``; the winner takes
  the whole pot. The pot and tickets are persisted in the database, so they
  survive restarts. Wins are recorded as transactions and audited.

Config (``data/config.json`` -> ``lottery``):

    "lottery": {
      "ticket_price": 50,
      "draw_interval_seconds": 86400
    }
"""

import asyncio
import contextlib
import logging
import random
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands
from sqlalchemy import func, select

from bot import Fun2OoshBot
from models import Lottery, LotteryTicket, Transaction, utcnow
from services.guild import AuditService
from services.locks import lock_manager
from utils.config import Config
from utils.economy_utils import EconomyUtils
from utils.helpers import EmbedBuilder, format_coins, unix_ts
from utils.runtime_config import activity as activity_config

logger = logging.getLogger(__name__)


class LotteryCog(commands.Cog):
    """Server jackpot with scheduled draws."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ config

    @staticmethod
    def _cfg(key: str, default):
        return activity_config("lottery").get(key, default)

    # ------------------------------------------------------------------- setup

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._draw_loop())

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ---------------------------------------------------------------- commands

    @commands.group(name="lottery", aliases=["jackpot"], invoke_without_command=True)
    async def lottery(self, ctx: commands.Context):
        """Show the current lottery pot and next draw."""
        if ctx.guild is None:
            return await ctx.send("Lottery only works in servers.")
        async with self.bot.get_session() as session:
            row = await LotteryCog._get(session, ctx.guild.id)
            tickets = await LotteryCog._ticket_total(session, ctx.guild.id)
            if row is None:
                pot = 0
                draw_at = None
            else:
                pot = row.pot
                draw_at = unix_ts(row.draw_at)

        price = LotteryCog._cfg("ticket_price", 50)
        interval = int(LotteryCog._cfg("draw_interval_seconds", 86400))
        embed = EmbedBuilder.info_embed(
            "🎰 Server Lottery",
            (
                f"**Pot:** {format_coins(pot)}\n"
                f"**Ticket:** {format_coins(price)} each\n"
                f"**Tickets sold:** {tickets}\n"
                f"**Next draw:** {f'<t:{draw_at}:R>' if draw_at else 'starts when someone buys a ticket'}"
                f"\n\nBuy tickets with `{ctx.prefix}lottery buy <n>` — "
                f"the winner takes the whole pot (draw every {interval // 3600}h)."
            ),
        )
        await ctx.send(embed=embed)

    @lottery.command(name="buy", aliases=["tickets", "enter"])
    async def lottery_buy(self, ctx: commands.Context, n: int = 1):
        """Buy lottery tickets: !lottery buy <count>."""
        if ctx.guild is None:
            return await ctx.send("Lottery only works in servers.")
        if n <= 0:
            return await ctx.send("You must buy at least 1 ticket.")
        if n > 100:
            return await ctx.send("You can buy at most 100 tickets at once.")

        price = int(LotteryCog._cfg("ticket_price", 50))
        total = n * price
        interval = int(LotteryCog._cfg("draw_interval_seconds", 86400))

        async with lock_manager.for_user(ctx.author.id):
            async with self.bot.get_session() as session:
                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                if wallet.balance < total:
                    return await ctx.send(
                        f"You need {format_coins(total)} for {n} ticket(s) "
                        f"but only have {format_coins(wallet.balance)}."
                    )
                wallet.balance -= total

                lottery = await LotteryCog._get(session, ctx.guild.id)
                if lottery is None:
                    lottery = Lottery(
                        guild_id=ctx.guild.id,
                        pot=0,
                        draw_at=utcnow() + timedelta(seconds=interval),
                        channel_id=ctx.channel.id,
                    )
                    session.add(lottery)
                else:
                    lottery.channel_id = ctx.channel.id
                lottery.pot += total

                ticket = await LotteryCog._get_ticket(session, ctx.guild.id, ctx.author.id)
                if ticket is None:
                    session.add(
                        LotteryTicket(guild_id=ctx.guild.id, user_id=ctx.author.id, count=n)
                    )
                else:
                    ticket.count += n

                session.add(
                    Transaction(
                        user_id=ctx.author.id,
                        type="lottery",
                        amount=-total,
                        description=f"Bought {n} lottery ticket(s)",
                    )
                )
                await session.commit()

        embed = EmbedBuilder.success_embed(
            "Tickets Bought!",
            f"You bought **{n}** ticket(s) for {format_coins(total)}. "
            f"The pot is now {format_coins(lottery.pot)}. Good luck! 🎰",
        )
        await ctx.send(embed=embed)

    # -------------------------------------------------------------------- draw

    async def _draw_loop(self) -> None:
        """Periodically check every guild for a due draw."""
        try:
            await self.bot.wait_until_ready()
            logger.info("Lottery draw loop started")
            while not self.bot.is_closed():
                try:
                    await self._check_draws()
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    logger.exception("Lottery draw check failed: %s", exc)
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Lottery draw loop stopped")
            raise

    async def _check_draws(self) -> None:
        now = utcnow()
        for guild in self.bot.guilds:
            async with self.bot.get_session() as session:
                lottery = await LotteryCog._get(session, guild.id)
                if lottery is None or lottery.draw_at is None or lottery.draw_at > now:
                    continue
                await self._run_draw(session, lottery, guild)

    async def _run_draw(self, session, lottery: Lottery, guild: discord.Guild) -> None:
        """Draw the winner (weighted by tickets) and pay the pot."""
        tickets = (
            (
                await session.execute(
                    select(LotteryTicket).where(
                        LotteryTicket.guild_id == guild.id, LotteryTicket.count > 0
                    )
                )
            )
            .scalars()
            .all()
        )
        total_tickets = sum(t.count for t in tickets)
        interval = int(LotteryCog._cfg("draw_interval_seconds", 86400))
        now = utcnow()

        if total_tickets == 0 or lottery.pot <= 0:
            # No entries — roll the draw forward, the pot carries over.
            lottery.draw_at = now + timedelta(seconds=interval)
            await session.commit()
            return

        # Weighted pick: each ticket is one entry.
        pick = random.randint(1, total_tickets)
        winner = None
        for t in tickets:
            pick -= t.count
            if pick <= 0:
                winner = t
                break
        if winner is None:
            lottery.draw_at = now + timedelta(seconds=interval)
            await session.commit()
            return

        winnings = lottery.pot
        wallet = await EconomyUtils.get_or_create_wallet(session, winner.user_id)
        wallet.balance += winnings
        session.add(
            Transaction(
                user_id=winner.user_id,
                type="lottery",
                amount=winnings,
                description=f"Lottery jackpot in {guild.name}",
            )
        )
        await AuditService.log(
            session,
            winner.user_id,
            "lottery_draw",
            f"Won {winnings} jackpot ({total_tickets} tickets entered)",
            guild_id=guild.id,
            target_id=winner.user_id,
        )

        lottery.pot = 0
        lottery.last_draw_at = now
        lottery.last_winner_id = winner.user_id
        lottery.draw_at = now + timedelta(seconds=interval)
        # Reset entries for the next round
        for t in tickets:
            await session.delete(t)
        await session.commit()

        await self._announce(guild, lottery, winner.user_id, winnings, total_tickets)

    async def _announce(
        self, guild: discord.Guild, lottery: Lottery, winner_id: int, winnings: int, entries: int
    ) -> None:
        channel = self.bot.get_channel(lottery.channel_id) if lottery.channel_id else None
        if channel is None and guild.system_channel is not None:
            channel = guild.system_channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        embed = EmbedBuilder.gold_embed(
            "🎉 Lottery Winner!",
            f"<@{winner_id}> just won the **{format_coins(winnings)}** jackpot "
            f"from {entries} ticket(s)! Congratulations! 🎰",
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ helpers

    @staticmethod
    async def _get(session, guild_id: int) -> Optional[Lottery]:
        return (
            await session.execute(select(Lottery).where(Lottery.guild_id == guild_id))
        ).scalar_one_or_none()

    @staticmethod
    async def _get_ticket(session, guild_id: int, user_id: int) -> Optional[LotteryTicket]:
        return (
            await session.execute(
                select(LotteryTicket).where(
                    LotteryTicket.guild_id == guild_id, LotteryTicket.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _ticket_total(session, guild_id: int) -> int:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(LotteryTicket.count), 0)).where(
                    LotteryTicket.guild_id == guild_id
                )
            )
        ).scalar()
        return total or 0


async def setup(bot: Fun2OoshBot):
    """Setup the lottery cog."""
    await bot.add_cog(LotteryCog(bot, bot.config))
