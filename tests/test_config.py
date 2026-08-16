"""Functional tests for the runtime JSON config system.

Covers config loading, percentage-based fines, catalog sync from
data/config.json, custom item messages, message templates, and roulette bet
parsing. Run with:

    DATABASE_URL=sqlite+aiosqlite:///test_config.db python tests/test_config.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DB = Path("test_config_run.db")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_DB}")

from bot import Fun2OoshBot  # noqa: E402
from cogs.casino import (  # noqa: E402
    RED_NUMBERS,
    parse_roulette_bet,
    roulette_color_of,
    roulette_outcome,
)
from models import Base  # noqa: E402
from services.items import ItemService  # noqa: E402
from utils.config import Config  # noqa: E402
from utils.helpers import format_template, render_item_message  # noqa: E402
from utils.migrations import run_migrations  # noqa: E402
from utils.runtime_config import activity, fine_amount, items, reload  # noqa: E402


async def main() -> None:
    reload()
    bot = Fun2OoshBot(Config())
    try:
        async with bot.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_migrations(bot.engine)

        # 1. config file loads activities + a populated item catalog
        assert activity("hunt").get("success_rate") == 0.60, activity("hunt")
        assert activity("crime").get("fine_rate") == 0.02, activity("crime")
        assert activity("mine").get("insurance_cost") == 20, activity("mine")
        assert len(items()) >= 20, len(items())
        print(f"OK config loads ({len(items())} items)")

        # 2. fine_amount: percentage of wallet, clamped to [fine_min, fine_max]
        cfg = {"fine_rate": 0.02, "fine_min": 200, "fine_max": 600}
        assert fine_amount(cfg, 5000, 200, 600) == 200  # 2% of 5000 = 100 -> min
        assert fine_amount(cfg, 50000, 200, 600) == 600  # 2% of 50000 = 1000 -> max
        assert fine_amount(cfg, 15000, 200, 600) == 300  # 2% of 15000 = 300
        assert fine_amount(cfg, 0, 200, 600) == 0  # nothing to fine
        # no rate -> flat random in range, never more than the balance
        flat = fine_amount({}, 100, 200, 600)
        assert 100 <= flat <= 100, flat
        print("OK fine_amount (% of wallet, clamped)")

        # 3. catalog sync: idempotent upsert, new columns populated
        async with bot.session_factory() as session:
            count1 = await ItemService.seed(session)
            count2 = await ItemService.seed(session)
            assert count1 == count2 == len(items())
            item = await ItemService.get(session, "lollipop")
            assert item is not None and item.giveable is True and item.consumable is True
            gift = await ItemService.get(session, "gift_card")
            assert gift is not None
            assert gift.bought_message and gift.used_message and gift.gave_message
            assert gift.sold_message is not None and gift.sold_message.startswith("[")
            rose = await ItemService.get(session, "rose")
            assert rose is not None
            assert rose.consumable and rose.giveable and not rose.limited
            assert rose.bought_message is not None
            assert "Whom are you gonna give it to" in rose.bought_message
            assert rose.used_message is not None and rose.used_message.startswith("[")
            assert rose.gave_message is not None and rose.gave_message.startswith("[")
            assert rose.sold_message is not None and rose.sold_message.startswith("[")
            cookie = await ItemService.get(session, "cookie")
            cake = await ItemService.get(session, "cake")
            assert cookie is not None and cake is not None
            assert cookie.consumable and cookie.giveable
            assert cake.consumable and cake.giveable
            assert cookie.consumed_message is not None and cookie.consumed_message.startswith("[")
            assert cookie.sold_message is not None and cookie.sold_message.startswith("[")
            assert cake.consumed_message is not None and cake.consumed_message.startswith("[")
            print("OK item sync (idempotent, custom messages + giveable + eatable)")

        # 4. message templates fill known placeholders, keep unknown ones
        assert (
            format_template("You bought {item} x{qty} for {amount}", item="Pizza", qty=2)
            == "You bought Pizza x2 for {amount}"
        )
        assert format_template("Hi {unknown} {user}", user="Bob") == "Hi {unknown} Bob"
        assert format_template(None, user="Bob") is None
        print("OK format_template")

        # 5. random message sets: list templates pick one entry per render
        async with bot.session_factory() as session:
            rose = await ItemService.get(session, "rose")
        assert rose is not None
        assert rose.used_message is not None and rose.gave_message is not None
        used_choices = json.loads(rose.used_message)
        gave_choices = json.loads(rose.gave_message)
        expected_used = {format_template(c, item="Rose") for c in used_choices}
        expected_gave = {format_template(c, sender="aditya", user="priya") for c in gave_choices}
        used_rendered = {
            m for m in (render_item_message(rose.used_message, item="Rose") for _ in range(200)) if m
        }
        assert used_rendered and used_rendered <= expected_used, used_rendered
        gave_rendered = {
            m
            for m in (
                render_item_message(rose.gave_message, sender="aditya", user="priya")
                for _ in range(200)
            )
            if m
        }
        assert gave_rendered and gave_rendered <= expected_gave, gave_rendered
        assert all("aditya" in m and "priya" in m for m in gave_rendered), gave_rendered
        single = render_item_message("You bought {item}.", item="Rose")
        assert single == "You bought Rose.", single
        assert render_item_message(None, item="x") is None
        print("OK random message sets (used/gave)")

        # 5b. consumed (eat) and sold message sets: random pick, placeholders fill
        async with bot.session_factory() as session:
            lollipop = await ItemService.get(session, "lollipop")
        assert lollipop is not None
        assert lollipop.consumed_message and lollipop.sold_message
        consumed_choices = json.loads(lollipop.consumed_message)
        sold_choices = json.loads(lollipop.sold_message)
        assert len(consumed_choices) >= 3 and len(sold_choices) >= 3
        expected_consumed = {
            format_template(c, item="Lollipop", amount="30 💎️") for c in consumed_choices
        }
        consumed_rendered = {
            m
            for m in (
                render_item_message(
                    lollipop.consumed_message, item="Lollipop", amount="30 💎️"
                )
                for _ in range(200)
            )
            if m
        }
        assert consumed_rendered and consumed_rendered <= expected_consumed, consumed_rendered
        assert all("Lollipop" in m for m in consumed_rendered)
        sold_rendered = {
            m
            for m in (
                render_item_message(
                    lollipop.sold_message, item="Lollipop", amount="15 💎️"
                )
                for _ in range(200)
            )
            if m
        }
        assert sold_rendered and sold_rendered <= {
            format_template(c, item="Lollipop", amount="15 💎️") for c in sold_choices
        }, sold_rendered
        # {amount} stays literal when the effect granted no coins
        no_amount = render_item_message("You ate {item} for {amount}.", item="Cake")
        assert no_amount is not None and "{amount}" in no_amount
        print("OK consumed/sold message sets (eat + sell flavors)")

        # 6. roulette bet parsing (new !roulette <amount> <bet> signature)
        assert parse_roulette_bet("red") == ("red", None)
        assert parse_roulette_bet("R") == ("red", None)
        assert parse_roulette_bet("black") == ("black", None)
        assert parse_roulette_bet("odd") == ("odd", None)
        assert parse_roulette_bet("even") == ("even", None)
        assert parse_roulette_bet("1-18") == ("low", None)
        assert parse_roulette_bet("1to18") == ("low", None)
        assert parse_roulette_bet("19-36") == ("high", None)
        assert parse_roulette_bet("19to36") == ("high", None)
        assert parse_roulette_bet("0") == ("number", 0)
        assert parse_roulette_bet("17") == ("number", 17)
        assert parse_roulette_bet(" 1 8 ") == ("number", 18)
        assert parse_roulette_bet("37") == (None, None)
        assert parse_roulette_bet("banana") == (None, None)
        print("OK roulette bet parsing")

        # 7. roulette outcome evaluation (17 is black in real roulette)
        assert len(RED_NUMBERS) == 18
        assert roulette_outcome(17, "black", None) == (True, 2)
        assert roulette_outcome(17, "red", None) == (False, 0)
        assert roulette_outcome(17, "odd", None) == (True, 2)
        assert roulette_outcome(2, "even", None) == (True, 2)
        assert roulette_outcome(5, "low", None) == (True, 2)
        assert roulette_outcome(30, "high", None) == (True, 2)
        assert roulette_outcome(17, "number", 17) == (True, 36)
        assert roulette_outcome(0, "number", 0) == (True, 36)
        assert roulette_outcome(0, "red", None) == (False, 0)
        assert roulette_color_of(0) == "Green"
        assert roulette_color_of(17) == "Black"
        assert roulette_color_of(1) == "Red"
        print("OK roulette outcomes (colors, 36x numbers, 0/green)")

        print("ALL CONFIG TESTS PASSED")
    finally:
        await bot.engine.dispose()
        if _DB.exists():
            _DB.unlink()


if __name__ == "__main__":
    asyncio.run(main())
