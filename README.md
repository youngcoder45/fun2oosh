# Fun2Oosh Economy Bot

A standalone Discord economy & casino bot with wallets, banks, rewards, streaks,
achievements, prestige, a full item shop & inventory, trading, gambling games,
transfers, leaderboards, and anti-abuse protection.

See `AUDIT.md` for the full feature-parity audit, database changes, and security review.

## Features

### Economy (`cogs/economy.py`)
- `!balance` / `/balance` — wallet & bank overview
- `!work`, `!collect`, `!daily` (with streaks), `!weekly`, `!monthly` — recurring income
- `!beg`, `!search`, `!crime`, `!rob`, `!hunt`, `!fish`, `!mine` — risky money makers
- `!deposit` / `!withdraw` — move money between wallet and bank
- `!transfer` / `!give` — send coins to other users (per-guild transfer tax optional)
- `!gamble` — quick coinflip-style bet
- `!leaderboard` / `!richest` / `!networth` / `!profile` / `!prestige` — stats & progression
- `!rep <user>` — give reputation (12h cooldown)
- `!achievements` / `!badges` — 13 unlockable achievements
- `!transactions` / `!history` — paginated transaction log

### Shop & Inventory (`cogs/shop.py`)
- `!shop [category]`, `!buy <item> [qty]`, `!sell <item> [qty]`, `!iteminfo <item>`
- `!inventory` / `!inv`, `!giveitem <user> <item> [qty]`, `!trade <user> <item> [qty]`
- `!use <item>` — consumables (coins), **boosters** (1.5x/2x money), **crates/lootboxes**
- Tools boost rewards: fishing rod → `!fish`, pickaxe → `!mine`, rifle → `!hunt`

### Casino (`cogs/casino.py`)
- `!blackjack`, `!poker`, `!roulette`, `!slots`, `!coinflip`, `!dice`
- `!crash`, `!russianroulette`, `!war`, `!baccarat`, `!hilo`, `!keno`

### Admin (`cogs/admin_economy.py`)
- `!add_money <user> <amount>`, `!reset_economy CONFIRM`
- `!econfig` / `!econfig set <key> <value>` — rewards, bet limits, tax rate, passive income, anti-alt
- `!shopadd`, `!shopremove`, `!shoplist`, `!itemgive <user> <item> [qty]`
- `!audit [n]` — admin action log

All commands also work as slash commands where marked hybrid (e.g. `/balance`, `/shop`, `/buy`).

## Setup

1. **Install dependencies** (Python 3.10+):

   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **Get a bot token** from the [Discord Developer Portal](https://discord.com/developers/applications):

   - Create a new application → *Bot* tab → *Reset Token*
   - Enable **Message Content Intent** under *Privileged Gateway Intents* (required for `!` prefix commands)
   - Invite the bot to your server with the `applications.commands` and `bot` scopes

3. **Configure** — copy `.env.example` to `.env` and fill in:

   ```bash
   cp .env.example .env
   ```

   ```dotenv
   DISCORD_TOKEN=your_token_here
   OWNER_ID=your_discord_user_id   # optional, unlocks admin commands
   ```

4. **Run the bot**:

   ```bash
   .venv/bin/python bot.py
   ```

   The database (`fun2oosh.db`) is created/updated automatically on first start.

## Configuration

All settings live in `utils/config.py` and can be overridden via `.env`:

| Variable            | Default                    | Description                        |
|---------------------|----------------------------|------------------------------------|
| `DISCORD_TOKEN`     | —                          | Bot token (required)               |
| `OWNER_ID`          | —                          | Owner Discord ID (admin commands)  |
| `DATABASE_URL`      | `sqlite+aiosqlite:///fun2oosh.db` | SQLAlchemy async DB URL     |
| `COMMAND_PREFIX`    | `!`                        | Text command prefix                |
| `MIN_BET` / `MAX_BET` | `10` / `10000`           | Betting limits                     |
| `DAILY_WAGER_LIMIT` | `50000`                    | Max wagered per day                |
| `WORK_REWARD`       | `100`                      | Reward for `!work`                 |
| `DAILY_REWARD`      | `500`                      | Reward for `!daily`                |
| `WEEKLY_REWARD`     | `2000`                     | Reward for `!weekly`               |

## Project structure

```
bot.py                 # Entry point + Fun2OoshBot class
config.py              # Re-exports Config
cogs/
  economy.py           # Wallet / income / progression commands
  casino.py            # Gambling games
  shop.py              # Shop, inventory, trading, crates
  activities.py        # hunt/fish/mine, monthly, networth, rep, achievements
  admin_economy.py     # Admin, config, shop & item management, audit
models/                # SQLAlchemy models (incl. items, inventory, guild_config, ...)
services/
  locks.py             # Per-user asyncio locks (race-condition safety)
  economy.py           # Lock-aware money service + anti-alt guard
  items.py             # Catalog, inventory, boosters, crates
  progression.py       # Achievements, streaks, prestige, reputation
  guild.py             # Guild config overrides + audit log
utils/
  config.py            # Canonical Config (pydantic-settings)
  economy_utils.py     # DB helpers (add/transfer money, wallets)
  cooldowns.py         # Per-user cooldowns
  anti_fraud.py        # Bet/transfer fraud detection
  helpers.py           # Embeds + formatting
  migrations.py        # Idempotent schema migrations
  pagination.py        # Reusable embed pagination
```

## Development & CI

Lint and type-check before pushing — CI runs the same checks on every push/PR
(see `.github/workflows/ci.yml`):

```bash
.venv/bin/pip install -r requirements-dev.txt   # adds ruff + mypy
.venv/bin/ruff check .                          # lint
.venv/bin/mypy .                                # type check
.venv/bin/python -m compileall -q bot.py config.py cogs models services utils
```

The CI matrix runs on Python 3.10 and 3.12, then boots the bot against a
throwaway SQLite database to verify all cogs load and the core commands
register.

## Responsible gaming

This bot includes gambling mechanics. Please gamble responsibly — set limits,
take breaks, and never bet money you can't afford to lose. **18+ only.**
