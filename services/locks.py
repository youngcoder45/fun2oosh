"""
Per-entity asyncio locks.

Every command that reads-modifies-writes economy state must run inside a lock
scoped to the affected user(s). This prevents double-spending, concurrent
withdrawals, and transfer races that would otherwise slip between awaits.

Multi-user locks are acquired in sorted key order to avoid deadlocks.
"""

import asyncio
from typing import Any, Dict, List


class LockManager:
    """Manages named asyncio.Lock instances."""

    def __init__(self) -> None:
        self._locks: Dict[Any, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _acquire(self, key: Any) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
        await lock.acquire()
        return lock

    def for_user(self, user_id: int) -> "_LockContext":
        """Lock a single user."""
        return _LockContext(self, user_id)

    def for_users(self, *user_ids: int) -> "_LockContext":
        """Lock multiple users (acquired in sorted order to avoid deadlock)."""
        return _LockContext(self, *sorted({uid for uid in user_ids if uid is not None}))


class _LockContext:
    """Async context manager that acquires one or more locks."""

    def __init__(self, manager: LockManager, *keys: Any):
        self.manager = manager
        self.keys = keys
        self._acquired: List[asyncio.Lock] = []

    async def __aenter__(self) -> "_LockContext":
        for key in self.keys:
            self._acquired.append(await self.manager._acquire(key))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        for lock in reversed(self._acquired):
            lock.release()
        self._acquired.clear()


# Global shared instance
lock_manager = LockManager()
