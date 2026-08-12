"""Functional tests for the dynamic event message system.

Covers pool sizes, placeholder rendering, currency configuration, DM name
fallbacks, missing-pool fallbacks, and amount formatting. Run with:

    DATABASE_URL=sqlite+aiosqlite:///test_events.db python tests/test_events.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DB = Path("test_events_run.db")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_DB}")

from bot import CORE_COGS, Fun2OoshBot  # noqa: E402
from models import Base  # noqa: E402
from services.events import event_message, load_pool, render  # noqa: E402
from utils.config import Config  # noqa: E402
from utils.helpers import event_names  # noqa: E402
from utils.migrations import run_migrations  # noqa: E402

# Minimum pool sizes enforced by the feature spec.
POOL_TARGETS = {
    "work": 100,
    "crime_success": 100,
    "crime_failure": 100,
    "search_success": 100,
    "search_failure": 100,
    "beg_success": 75,
    "beg_failure": 75,
    "hunt": 75,
    "fish": 75,
    "mine": 75,
}


class FakeUser:
    display_name = "Alice"

    def __str__(self) -> str:
        return "Alice"


class FakeGuild:
    name = "Test Server"


async def main() -> None:
    bot = Fun2OoshBot(Config())
    try:
        async with bot.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_migrations(bot.engine)
        for cog in CORE_COGS:
            await bot.load_extension(cog)

        # 1. pool sizes meet spec targets
        for pool, target in POOL_TARGETS.items():
            count = len(load_pool(pool))
            assert count >= target, f"{pool}: {count} < {target}"
            print(f"OK {pool}: {count} events")

        # 2. placeholder rendering + variety
        seen = set()
        for _ in range(200):
            msg = event_message("work", 142, "coins", "Alice", "Test Server")
            assert "{" not in msg, f"unfilled placeholder: {msg}"
            assert "142" in msg, f"amount missing: {msg}"
            assert "coins" in msg, f"currency missing: {msg}"
            seen.add(msg)
        print(f"OK work renders {len(seen)} unique messages in 200 draws")

        # 3. failure pools render without an amount
        msg = event_message("beg_failure", 0, "coins", "Alice", "Test Server")
        assert "{" not in msg, f"unfilled: {msg}"
        print("OK beg_failure:", msg[:60])

        # 4. configured currency name is used, not hardcoded
        msg = event_message("mine", 77, "gems", "Bob", "Server")
        assert "gems" in msg and "77" in msg, msg
        print("OK custom currency:", msg[:60])

        # 5. event_names falls back to the user name in DMs
        user, guild = event_names(FakeUser(), None)
        assert guild == "Alice", (user, guild)
        user, guild = event_names(FakeUser(), FakeGuild())
        assert guild == "Test Server", (user, guild)
        print("OK event_names:", (user, guild))

        # 6. fallback text when a pool is missing
        msg = render(
            "nonexistent_pool",
            amount="5",
            currency="coins",
            user="u",
            guild="g",
            fallback="generic {amount} {currency}",
        )
        assert msg == "generic 5 coins", msg
        print("OK fallback:", msg)

        # 7. amount formatting uses thousands separators
        msg = event_message("work", 10000, "coins", "Alice", "Server")
        assert "10,000" in msg, msg
        print("OK comma formatting:", msg[:50])

        print("ALL EVENT TESTS PASSED")
    finally:
        await bot.engine.dispose()
        if _DB.exists():
            _DB.unlink()


if __name__ == "__main__":
    asyncio.run(main())
