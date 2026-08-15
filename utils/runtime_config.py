"""
Runtime JSON configuration (``data/config.json``).

Everything gameplay-related that used to be hardcoded — activity success
rates, reward ranges, failure fines, insurance costs, and the entire shop
catalog — now lives in one editable JSON file so a bot owner can tune the
economy without touching code:

- ``activities.<name>`` — per-command tuning (see the file for the schema)
- ``shop.items`` — the item catalog (name, price, stackable, giveable,
  consumable, custom messages, use effects, ...)

The file is loaded lazily and cached per process. After editing it, run
``!reloadconfig`` (or restart the bot) to apply the changes; ``!reloadconfig``
also re-syncs the shop catalog into the database.
"""

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("fun2oosh.config")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"

# Loaded config cache ({} until first read).
_data: Dict[str, Any] = {}


def _load() -> Dict[str, Any]:
    """Read the config file from disk. Returns {} when missing/invalid."""
    if not CONFIG_PATH.exists():
        logger.warning("Config file %s not found — using built-in defaults.", CONFIG_PATH)
        return {}
    try:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Config file %s is not valid JSON: %s", CONFIG_PATH, exc)
        return {}


def reload() -> None:
    """Reload the config file from disk (clears the cache)."""
    _data.clear()
    _data.update(_load())


def get() -> Dict[str, Any]:
    """Return the (cached) full config dict."""
    if not _data:
        reload()
    return _data


# ------------------------------------------------------------------ accessors

def activity(key: str) -> Dict[str, Any]:
    """Return the config block for an activity command (may be empty)."""
    return get().get("activities", {}).get(key, {}) or {}


def activity_value(key: str, field: str, default: Any = None) -> Any:
    """Return one field from an activity block, falling back to ``default``."""
    return activity(key).get(field, default)


def items() -> List[Dict[str, Any]]:
    """Return the shop item definitions from config."""
    return get().get("shop", {}).get("items", []) or []


def fine_amount(
    cfg: Dict[str, Any],
    balance: int,
    default_min: int,
    default_max: int,
) -> int:
    """Compute a failure fine from a config block.

    When ``fine_rate`` is set, the fine is that percentage of the user's
    current wallet balance, clamped to ``[fine_min, fine_max]``. Without a
    rate, a flat random amount in ``[fine_min, fine_max]`` is used (the
    legacy behaviour). The result never exceeds the user's balance.
    """
    rate = float(cfg.get("fine_rate") or 0.0)
    lo = int(cfg.get("fine_min") or default_min)
    hi = int(cfg.get("fine_max") or default_max)
    if rate > 0:
        amount = int((balance or 0) * rate)
        amount = max(lo, min(hi, amount))
    else:
        amount = random.randint(lo, hi)
    return max(0, min(amount, balance or 0))
