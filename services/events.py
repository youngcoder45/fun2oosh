"""
Dynamic event message engine.

Economy activity commands (work, crime, search, beg, hunt, fish, mine) pull
their narrative text from JSON pools in ``data/events/`` instead of static
strings, so every result reads like a living economy game and administrators
can add events by editing JSON — no code changes required.

Pool files
----------
Each file maps a pool name to a list of messages::

    // data/events/work.json
    {
      "work": [
        "You repaired a customer's transmission and earned {amount} {currency}.",
        ...
      ],
      "work_bonus": [...]
    }

Supported placeholders:

- ``{amount}``   — the coin amount gained (or fine paid)
- ``{currency}`` — the configured currency name (no hardcoded symbols)
- ``{user}``     — the acting user's display name
- ``{guild}``    — the guild name (falls back to the user when in DMs)

Unknown placeholders are left untouched so new ones can be added later
without breaking existing pools.
"""

import json
import logging
import random
import string
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("fun2oosh.events")

_EVENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "events"

# Pool name -> loaded messages (lazy cache)
_POOLS: Dict[str, List[str]] = {}

# Pools whose messages all live in a shared file (file -> pool prefixes).
# ``_pool_file`` maps a pool to its JSON file by its first ``_``-segment,
# but casino outcome pools all share ``casino.json``.
_SHARED_FILES: Dict[str, Tuple[str, ...]] = {
    "casino": (
        "roulette_result",
        "blackjack_win",
        "blackjack_loss",
        "slots_win",
        "slots_loss",
        "baccarat_win",
        "baccarat_loss",
        "keno_win",
        "keno_loss",
        "poker_win",
        "poker_loss",
        "poker_tie",
    ),
}


class _TolerantFormatter(string.Formatter):
    """Formatter that leaves unknown ``{placeholders}`` as-is."""

    def get_field(self, field_name: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> tuple:
        try:
            return super().get_field(field_name, args, kwargs)
        except (KeyError, IndexError):
            return "{" + field_name + "}", field_name


_formatter = _TolerantFormatter()


def _pool_file(pool: str) -> Path:
    """Map a pool name to its JSON file (pools and files share names)."""
    for file_name, prefixes in _SHARED_FILES.items():
        if pool in prefixes:
            return _EVENTS_DIR / f"{file_name}.json"
    return _EVENTS_DIR / f"{pool.split('_')[0]}.json"


def load_pool(pool: str) -> List[str]:
    """Load (and cache) the messages for a pool from its JSON file.

    Entries may be plain strings or objects with a ``message`` key.
    Returns an empty list when the pool is missing or unreadable.
    """
    if pool in _POOLS:
        return _POOLS[pool]

    path = _pool_file(pool)
    messages: List[str] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.error("Event file %s is not valid JSON.", path)
            data = {}
        entries = data.get(pool, [])
        for entry in entries:
            if isinstance(entry, str):
                messages.append(entry)
            elif isinstance(entry, dict) and entry.get("message"):
                messages.append(entry["message"])
    _POOLS[pool] = messages
    return messages


def reload() -> None:
    """Clear the cache (e.g. after an admin edits the JSON files)."""
    _POOLS.clear()


def render(
    pool: str,
    amount: Any = "",
    currency: str = "",
    user: str = "",
    guild: str = "",
    fallback: Optional[str] = None,
    **extra: Any,
) -> str:
    """Return a random event message from ``pool`` with placeholders filled.

    ``fallback`` is used when the pool is empty so commands always have text.
    ``extra`` supplies additional placeholders (e.g. ``bet``, ``profit`` for
    casino outcome lines).
    """
    messages = load_pool(pool)
    if messages:
        template = random.choice(messages)
    else:
        template = fallback or ""
    if not template:
        return template
    values = {
        "amount": amount,
        "currency": currency,
        "user": user,
        "guild": guild,
        **extra,
    }
    return _formatter.vformat(template, (), values)


def event_message(
    pool: str,
    amount: Any,
    currency: str,
    user: str,
    guild: str,
    fallback: Optional[str] = None,
) -> str:
    """Render an event for a command, formatting the amount with separators.

    Convenience wrapper around :func:`render` used by the activity cogs.
    """
    return render(
        pool,
        amount=f"{int(amount):,}" if amount not in (None, "") else "",
        currency=currency,
        user=user,
        guild=guild,
        fallback=fallback,
    )
