"""
Cooldown management for prefix commands.

Provides:
- `CooldownManager`: per-user, per-key cooldown tracking.
- `cooldown_manager`: global shared instance (used by the casino cog).
- `check_cooldown`: decorator for prefix commands that blocks and informs
  the user when the cooldown is still active.
"""

import functools
import time
from typing import Dict, Optional, Tuple


class CooldownManager:
    """Tracks cooldowns per (key, user_id)."""

    def __init__(self):
        self._cooldowns: Dict[Tuple[str, int], float] = {}

    def set_cooldown(self, key: str, user_id: int) -> None:
        """Record the current time as the start of the cooldown."""
        self._cooldowns[(key, user_id)] = time.monotonic()

    def _elapsed(self, key: str, user_id: int) -> Optional[float]:
        started = self._cooldowns.get((key, user_id))
        if started is None:
            return None
        return time.monotonic() - started

    def is_on_cooldown(self, key: str, user_id: int, seconds: int) -> bool:
        """Return True if the user is still on cooldown for the key."""
        elapsed = self._elapsed(key, user_id)
        if elapsed is None:
            return False
        if elapsed >= seconds:
            del self._cooldowns[(key, user_id)]
            return False
        return True

    def get_remaining_time(self, key: str, user_id: int, seconds: int) -> float:
        """Return how many seconds remain until the cooldown expires."""
        elapsed = self._elapsed(key, user_id)
        if elapsed is None:
            return 0.0
        return max(0.0, seconds - elapsed)

    def clear(self) -> None:
        """Clear all cooldowns (useful for tests or admin reset)."""
        self._cooldowns.clear()


# Global shared instance
cooldown_manager = CooldownManager()


def cooldown_notice(key: str, remaining_seconds: float) -> str:
    """Standard cooldown notice rendered with Discord timestamps.

    The expiry is computed once from ``remaining_seconds`` and passed to
    Discord as a relative (``<t:...:R>``) and an absolute (``<t:...:F>``)
    timestamp, so the client renders "in 2 hours" and keeps it live — no
    manual duration strings.
    """
    ready_at = int(time.time()) + max(0, int(remaining_seconds))
    return (
        f"{key.title()} cooldown active.\n"
        f"Try again <t:{ready_at}:R>\n"
        f"Available at <t:{ready_at}:F>"
    )


def check_cooldown(key: str, seconds: int):
    """Decorator that enforces a per-user cooldown on a prefix command.

    Usage::

        @commands.command(name='work')
        @check_cooldown('work', 1800)
        async def work(self, ctx: commands.Context):
            ...
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            user_id = ctx.author.id
            if cooldown_manager.is_on_cooldown(key, user_id, seconds):
                remaining = cooldown_manager.get_remaining_time(key, user_id, seconds)
                await ctx.send(cooldown_notice(key, remaining))
                return
            cooldown_manager.set_cooldown(key, user_id)
            return await func(self, ctx, *args, **kwargs)

        return wrapper

    return decorator
