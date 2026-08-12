# Fun2Oosh Economy Bot 🎰💰

A standalone Discord economy & casino bot with wallets, banks, daily/weekly rewards,
crimes, robberies, gambling games (blackjack, slots, roulette, poker, crash and more),
transfers, leaderboards, and anti-fraud protection.

## Features

### Economy (`cogs/economy.py`)
- `!balance` / `/balance` — wallet & bank overview
- `!work`, `!daily`, `!weekly`, `!collect` — recurring income
- `!beg`, `!search`, `!crime`, `!rob` — risky money makers
- `!deposit` / `!withdraw` — move money between wallet and bank
- `!transfer` / `!give` — send coins to other users
- `!leaderboard` / `!richest` / `!profile` — stats
- `!gamble` — quick coinflip-style bet

### Casino (`cogs/casino.py`)
- `!blackjack`, `!poker`, `!roulette`, `!slots`, `!coinflip`, `!dice`
- `!crash`, `!russianroulette`, `!war`, `!baccarat`, `!hilo`, `!keno`

### Admin (`cogs/admin_economy.py`)
- `!add_money <user> <amount>` — grant coins (owner / server admins)
- `!reset_economy CONFIRM` — wipe all economy data (owner only)

All commands also work as slash commands (e.g. `/balance`, `/blackjack`).

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
  economy.py           # Wallet / income commands
  casino.py            # Gambling games
  admin_economy.py     # Admin commands
models/
  base.py user.py wallet.py transaction.py bet.py
utils/
  config.py            # Canonical Config (pydantic-settings)
  economy_utils.py     # DB helpers (add/transfer money, wallets)
  cooldowns.py         # Per-user cooldowns
  anti_fraud.py        # Bet/transfer fraud detection
  helpers.py           # Embeds + formatting
```

## Responsible gaming

This bot includes gambling mechanics. Please gamble responsibly — set limits,
take breaks, and never bet money you can't afford to lose. **18+ only.**
