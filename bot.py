"""
Fun2Oosh Economy Bot — main entry point.

Run with:

    python bot.py

Requires a valid Discord bot token. Put it in a `.env` file:

    DISCORD_TOKEN=your_token_here

The database is created automatically on first startup
(default: sqlite+aiosqlite:///fun2oosh.db).
"""

import asyncio
import contextlib
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base, Transaction, Wallet, utcnow
from services.guild import GuildConfigService
from services.items import ItemService
from utils.config import Config
from utils.cooldowns import cooldown_notice
from utils.migrations import run_migrations

logger = logging.getLogger("fun2oosh")

CORE_COGS = (
    "cogs.economy",
    "cogs.casino",
    "cogs.admin_economy",
    "cogs.shop",
    "cogs.activities",
)


class Fun2OoshBot(commands.Bot):
    """The Fun2Oosh economy bot."""

    def __init__(self, config: Config):
        self.config = config

        # Async SQLAlchemy engine + session factory
        self.engine = create_async_engine(config.database_url, echo=False)
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

        intents = discord.Intents.default()
        intents.message_content = True  # Needed for prefix commands

        super().__init__(
            command_prefix=commands.when_mentioned_or(config.command_prefix),
            intents=intents,
            owner_id=config.owner_id,
        )

    def get_session(self) -> AsyncSession:
        """Return a new async session.

        Use it as an async context manager::

            async with self.bot.get_session() as session:
                ...
        """
        return self.session_factory()

    async def setup_hook(self) -> None:
        """Create tables, run migrations, seed the catalog, and load all cogs."""
        # Create tables if they don't exist yet (safe no-op when they do)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Add new columns to pre-existing tables (idempotent)
        await run_migrations(self.engine)
        logger.info("Database ready (%s)", self.config.database_url)

        # Seed the item catalog from data/items.json (no-op if already seeded)
        async with self.session_factory() as session:
            seeded = await ItemService.seed(session)
            if seeded:
                logger.info("Seeded %d items into the catalog", seeded)

        for cog in CORE_COGS:
            await self.load_extension(cog)
            logger.info("Loaded cog: %s", cog)

        # Background hourly passive-income task
        self._passive_income_task = asyncio.create_task(self.passive_income_loop())

        # Sync slash commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d slash command(s)", len(synced))
        except Exception as exc:  # noqa: BLE001 - never crash startup over sync
            logger.warning("Could not sync slash commands: %s", exc)

    async def on_ready(self) -> None:
        """Called once the bot is connected and ready."""
        if self.user is None:
            return
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Guilds: %d", len(self.guilds))
        logger.info("Prefix: %s | Slash commands available in servers.", self.command_prefix)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Friendly error messages for prefix commands."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            if ctx.command is None:
                return
            usage = f"{ctx.prefix}{ctx.command.name}"
            if ctx.command.params:
                params = [p for p in ctx.command.clean_params if p != "ctx"]
                if params:
                    usage += " " + " ".join(f"<{p}>" for p in params)
            await ctx.send(f"Missing argument: `{error.param.name}`\nUsage: `{usage}`")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"Invalid argument: {error}")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                f"You need the `{', '.join(error.missing_permissions)}` permission to do that."
            )
            return
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send(
                f"I need the `{', '.join(error.missing_permissions)}` permission to do that."
            )
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You don't have permission to use this command.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            key = ctx.command.name if ctx.command is not None else "command"
            await ctx.send(cooldown_notice(key, error.retry_after))
            return

        logger.error("Unhandled error in command '%s': %s", ctx.command, error, exc_info=error)
        await ctx.send(f"An unexpected error occurred: {error}")

    async def on_application_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Friendly error messages for slash/hybrid commands."""
        if isinstance(error, app_commands.CommandOnCooldown):
            key = interaction.command.name if interaction.command is not None else "command"
            await interaction.response.send_message(
                cooldown_notice(key, error.retry_after),
                ephemeral=True,
            )
            return
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                f"You need the `{', '.join(error.missing_permissions)}` permission to do that.",
                ephemeral=True,
            )
            return
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True,
            )
            return

        # Unwrap command-invoke errors to reach the real exception
        exc: BaseException = error
        if isinstance(error, app_commands.CommandInvokeError) and error.original is not None:
            exc = error.original

        logger.error(
            "Unhandled error in app command '%s': %s",
            interaction.command,
            exc,
            exc_info=exc,
        )
        try:
            await interaction.response.send_message(
                f"An unexpected error occurred: {exc}", ephemeral=True
            )
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ passive income

    async def passive_income_loop(self) -> None:
        """Pay hourly passive income to active wallets in guilds that enable it."""
        try:
            await self.wait_until_ready()
            logger.info("Passive income loop started (hourly)")
            while not self.is_closed():
                try:
                    await self._pay_passive_income()
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    logger.exception("Passive income payment failed: %s", exc)
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info("Passive income loop stopped")
            raise

    async def _pay_passive_income(self) -> None:
        now = utcnow()
        async with self.session_factory() as session:
            for guild in self.guilds:
                cfg = await GuildConfigService.get(session, guild.id)
                rate = cfg.passive_income or 0
                if rate <= 0:
                    continue
                member_ids = [m.id for m in guild.members if not m.bot]
                if not member_ids:
                    continue
                # Batch-load wallets for all members in one query
                wallets = {
                    w.user_id: w
                    for w in (
                        await session.execute(select(Wallet).where(Wallet.user_id.in_(member_ids)))
                    ).scalars()
                }
                for user_id in member_ids:
                    wallet = wallets.get(user_id)
                    if wallet is None:
                        continue  # only pay users who have engaged with the economy
                    if (
                        wallet.last_passive_at
                        and (now - wallet.last_passive_at).total_seconds() < 3600
                    ):
                        continue
                    wallet.balance = (wallet.balance or 0) + rate
                    wallet.last_passive_at = now
                    session.add(
                        Transaction(
                            user_id=user_id,
                            type="passive",
                            amount=rate,
                            description="Hourly passive income",
                        )
                    )
                await session.commit()
            logger.info("Passive income paid (%d guilds checked)", len(self.guilds))

    async def close(self) -> None:
        """Stop background tasks and dispose the database engine on shutdown."""
        task = getattr(self, "_passive_income_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.engine.dispose()
        await super().close()


def main() -> None:
    """Build the bot from config and run it."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    config = Config()

    token = config.discord_token.strip()
    if not token or token in ("demo_token", "your_token_here", "YOUR_TOKEN_HERE"):
        print(
            "\n No Discord bot token configured.\n"
            "1. Create a bot application at https://discord.com/developers/applications\n"
            "2. Copy its token into a `.env` file in this folder:\n"
            "DISCORD_TOKEN=your_token_here\n"
            "3. (Optional) OWNER_ID=<your discord user id> to unlock admin commands\n"
            "4. Run `python bot.py` again.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    bot = Fun2OoshBot(config)
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        logger.error(
            "Login failed -> your DISCORD_TOKEN is invalid. Double-check the token "
            "in your .env file and try again."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
