"""
Lightweight, idempotent schema migrations.

SQLAlchemy's `create_all` creates missing *tables* but never adds columns to
existing tables. SQLite has no native "ADD COLUMN IF NOT EXISTS", so this
module runs idempotent `ALTER TABLE ... ADD COLUMN` statements guarded by
`PRAGMA table_info`.

New migrations are appended to `COLUMN_MIGRATIONS` as a mapping of
table name -> [(column_name, column_ddl), ...]. Column renames go in
`RENAMED_COLUMNS` as table -> [(old_name, new_name), ...].
"""

import logging
from typing import Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("fun2oosh.migrations")

# table -> [(column, ddl), ...]
COLUMN_MIGRATIONS: Dict[str, List[Tuple[str, str]]] = {
    'wallets': [
        ('prestige', 'INTEGER DEFAULT 0'),
        ('reputation', 'INTEGER DEFAULT 0'),
        ('daily_streak', 'INTEGER DEFAULT 0'),
        ('last_daily_at', 'DATETIME'),
        ('last_weekly_at', 'DATETIME'),
        ('last_monthly_at', 'DATETIME'),
        ('last_passive_at', 'DATETIME'),
    ],
    'guild_config': [
        ('collect_reward', 'INTEGER'),
    ],
    'role_income': [
        ('claim_interval', 'INTEGER DEFAULT 3600'),
    ],
    'items': [
        ('giveable', 'BOOLEAN DEFAULT 1'),
        ('bought_message', 'TEXT'),
        ('used_message', 'TEXT'),
        ('consumed_message', 'TEXT'),
        ('gave_message', 'TEXT'),
        ('sold_message', 'TEXT'),
    ],
}

# table -> [(old_column, new_column), ...]
RENAMED_COLUMNS: Dict[str, List[Tuple[str, str]]] = {
    # collect pays the configured *amount per interval*, not an hourly rate
    'role_income': [
        ('hourly_rate', 'amount'),
    ],
}


async def run_migrations(engine: AsyncEngine) -> None:
    """Apply any pending column migrations to the database."""
    async with engine.begin() as conn:
        await conn.run_sync(_apply_sync)


def _apply_sync(sync_conn) -> None:
    applied = 0
    for table, columns in COLUMN_MIGRATIONS.items():
        try:
            existing = {
                row[1]
                for row in sync_conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
        except Exception:
            logger.warning("Migrations: table '%s' does not exist yet — skipping.", table)
            continue

        for column, ddl in columns:
            if column in existing:
                continue
            sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied += 1
            logger.info("Migration: added column %s.%s %s", table, column, ddl)

    for table, renames in RENAMED_COLUMNS.items():
        try:
            existing = {
                row[1]
                for row in sync_conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
        except Exception:
            logger.warning("Migrations: table '%s' does not exist yet — skipping.", table)
            continue
        for old_name, new_name in renames:
            if old_name not in existing or new_name in existing:
                continue
            sync_conn.exec_driver_sql(
                f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"
            )
            applied += 1
            logger.info("Migration: renamed column %s.%s -> %s", table, old_name, new_name)

    if applied == 0:
        logger.info("Migrations: schema up to date.")
