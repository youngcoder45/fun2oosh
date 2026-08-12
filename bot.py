"""
Fun2Oosh Economy Bot — main entry point.

Run with:

    python bot.py

Requires a valid Discord bot token. Put it in a `.env` file:

    DISCORD_TOKEN=your_token_here

The database is created automatically on first startup
(default: sqlite+aiosqlite:///fun2oosh.db).
"""

import logging
import sys

import discord
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base
from utils.config import Config

logger = logging.getLogger("fun2oosh")

CORE_COGS = (
    "cogs.economy",
    "cogs.casino",
    "cogs.admin_economy",
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
        """Create database tables and load all cogs."""
        # Create tables if they don't exist yet (safe no-op when they do)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database ready (%s)", self.config.database_url)

        for cog in CORE_COGS:
            await self.load_extension(cog)
            logger.info("Loaded cog: %s", cog)

        # Sync slash commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d slash command(s)", len(synced))
        except Exception as exc:  # noqa: BLE001 - never crash startup over sync
            logger.warning("Could not sync slash commands: %s", exc)

    async def on_ready(self) -> None:
        """Called once the bot is connected and ready."""
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Guilds: %d", len(self.guilds))
        logger.info("Prefix: %s | Slash commands available in servers.", self.command_prefix)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Friendly error messages for prefix commands."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            usage = f"{ctx.prefix}{ctx.command.name}"
            if ctx.command.params:
                params = [p for p in ctx.command.clean_params if p != 'ctx']
                if params:
                    usage += " " + " ".join(f"<{p}>" for p in params)
            await ctx.send(f"❌ Missing argument: `{error.param.name}`\nUsage: `{usage}`")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Invalid argument: {error}")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                f"❌ You need the `{', '.join(error.missing_permissions)}` permission to do that."
            )
            return
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send(
                f"❌ I need the `{', '.join(error.missing_permissions)}` permission to do that."
            )
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You don't have permission to use this command.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏰ Command on cooldown. Try again in {error.retry_after:.0f}s.")
            return

        logger.error("Unhandled error in command '%s': %s", ctx.command, error, exc_info=error)
        await ctx.send(f"❌ An unexpected error occurred: {error}")

    async def close(self) -> None:
        """Dispose the database engine on shutdown."""
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
    if not token or token in ('demo_token', 'your_token_here', 'YOUR_TOKEN_HERE'):
        print(
            "\n❌ No Discord bot token configured.\n"
            "  1. Create a bot application at https://discord.com/developers/applications\n"
            "  2. Copy its token into a `.env` file in this folder:\n"
            "       DISCORD_TOKEN=your_token_here\n"
            "  3. (Optional) OWNER_ID=<your discord user id> to unlock admin commands\n"
            "  4. Run `python bot.py` again.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    bot = Fun2OoshBot(config)
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        logger.error(
            "Login failed — your DISCORD_TOKEN is invalid. Double-check the token "
            "in your .env file and try again."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
