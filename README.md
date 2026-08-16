# Fun2Oosh Economy Bot

A standalone Discord economy & casino bot with wallets, banks, rewards, streaks,
achievements, prestige, a full item shop & inventory, trading, gambling games,
transfers, leaderboards, and anti-abuse protection.

**New to the bot? Type `!help` for the command list — `!help <command>` shows
usage, aliases, and a step-by-step “How to play” for every casino game
(e.g. `!help keno`).**

Documentation:

- `AUDIT.md` — feature-parity audit, database changes, and security review
- `UI_REDESIGN.md` — UI/UX design system, role-income collect, event messages, cooldown timestamps
- `CONTRIBUTING.md` — how to set up a dev environment and contribute

## Features

### Help (`cogs/help.py`)
- `!help` — categorized command overview
- `!help <command>` — usage, aliases, and how-to-play guides for every casino game
  (e.g. `!help keno` explains the rules, payouts, and example usage)

### Economy (`cogs/economy.py`)
- `!balance` / `/balance` — wallet & bank overview
- `!work` (random 100–2000 💎, cooldown in `activities.work`), `!collect`, `!daily` (with streaks), `!weekly`, `!monthly` — recurring income
- `!beg`, `!search`, `!crime`, `!rob`, `!hunt`, `!fish`, `!mine` — risky money makers
- `!deposit` / `!withdraw` — move money between wallet and bank
- `!transfer` / `!give` — send coins to other users (per-guild transfer tax optional)
- `!gamble` — quick coinflip-style bet
- `!leaderboard` / `!richest` / `!networth` / `!profile` / `!prestige` — stats & progression
- `!rep <user>` — give reputation (12h cooldown)
- `!achievements` / `!badges` — 53 unlockable achievements (paginated with buttons); locked
  ones show live progress (e.g. “Panhandler — 12/25 begs”)
- `!transactions` / `!history` — paginated transaction log

### Shop & Inventory (`cogs/shop.py`)
- `!shop [category]`, `!buy <item> [qty]`, `!sell <item> [qty]`, `!iteminfo <item>`
- Every shop item has a 3-digit catalog number (`001`, `002`, …) shown in `!shop`; the number
  works everywhere the string id does — `!buy 003`, `!sell 003`, `!use 003`, `!eat 003`,
  `!giveitem @user 003`, `!trade @user 003 1 5`, `!iteminfo 003` — so `!buy 003` buys the rose
- `!inventory` / `!inv`, `!giveitem <user> <item> [qty]`, `!trade <user> <item> [qty] [price]`
  — add a custom `price` (coins per item) to sell an item for coins instead of swapping
  (e.g. `!trade @user rose 1 5` offers a rose for 5 💎; the other user presses **Accept** to pay)
- `!use <item>` — consumables (coins), **boosters** (1.5x/2x money), **crates/lootboxes**
- `!eat <item>` — eat food-style consumables (items with a `consumed_message`)
- Tools boost rewards: fishing rod → `!fish`, pickaxe → `!mine`, rifle → `!hunt`

### Casino (`cogs/casino.py`)
- `!blackjack` (with **Split** button for pairs), `!poker`, `!roulette <amount> <bet>`, `!slots`, `!coinflip`, `!dice`
- `!crash`, `!russianroulette`, `!war`, `!baccarat`, `!hilo`, `!keno`
- `!casinoleaderboard` / `!casinolb` — biggest lifetime casino wins (and totals per user)
- Roulette bets: `!roulette 100 red`, `!roulette 100 even`, `!roulette 100 1-18`,
  `!roulette 100 19-36`, `!roulette 100 0` (numbers 0-36, colors, odd/even, ranges)
- Roulette is a **shared table**: the round stays open for 15s after the last bet
  (max 1 minute), so everyone in the channel can join before the wheel spins;
  a separate result embed shows the winning number and a winners/losers summary
- Game outcome text (WIN/LOSS/PUSH lines and the roulette result block) is pulled from
  `data/events/casino.json` — edit the JSON to reword any game without touching code
  (`!reloadconfig` re-reads event pools too)

### Lottery (`cogs/lottery.py`)
- `!lottery` — current pot, ticket price, tickets sold, and next draw time
- `!lottery buy <n>` — buy tickets (price from `lottery.ticket_price` in `data/config.json`)
- A background task draws every `lottery.draw_interval_seconds` (default 24h); the winner takes
  the whole pot. Pot, tickets, and winner history are stored in the database, so they survive
  restarts; wins are recorded as transactions and audited

### Admin (`cogs/admin_economy.py`)
- `!add-money <user> <amount> [cash|bank]`, `!reset_economy CONFIRM`
- `!reloadconfig` — apply edits to `data/config.json` without a restart
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

There are two layers of configuration:

**1. `.env`** (via `utils/config.py`) — core bot settings:

| Variable            | Default                    | Description                        |
|---------------------|----------------------------|------------------------------------|
| `DISCORD_TOKEN`     | —                          | Bot token (required)               |
| `OWNER_ID`          | —                          | Owner Discord ID (admin commands)  |
| `DATABASE_URL`      | `sqlite+aiosqlite:///fun2oosh.db` | SQLAlchemy async DB URL     |
| `COMMAND_PREFIX`    | `!`                        | Text command prefix                |
| `MIN_BET` / `MAX_BET` | `10` / `0`                | Betting limits (`MAX_BET=0` = unlimited) |
| `DAILY_WAGER_LIMIT` | `50000`                    | Max wagered per day                |
| `WORK_REWARD`       | `100`                      | Legacy — `!work` is now random (100–2000) via `activities.work` in `data/config.json` |
| `DAILY_REWARD`      | `500`                      | Reward for `!daily`                |
| `WEEKLY_REWARD`     | `2000`                     | Reward for `!weekly`               |
| `MONTHLY_REWARD`    | `5000`                     | Reward for `!monthly`              |
| `CURRENCY_NAME`     | `💎`                        | Currency name used in event text   |

**2. `data/config.json`** — gameplay tuning, editable any time. Run
`!reloadconfig` (or restart the bot) to apply changes.

| Section | What you can change |
|---|---|
| `activities.hunt/fish/mine` | success rate, min/max reward, cooldown, **insurance cost on failure** |
| `activities.crime` | success rate, reward range, cooldown, crime list, **fine rate** (e.g. `0.02` = 2% of the user's current wallet on failure) |
| `activities.rob` | success rate, robbery %, cap, cooldown, **fine rate on failure** |
| `activities.search` | success rate, cooldown, searchable locations |
| `activities.beg` | success rate, reward range, cooldown |
| `activities.work` | min/max random reward (default 100–2000) and `cooldown_seconds` for `!work` / `/work` (a per-guild `!econfig work_reward` still pins a fixed value) |
| `activities.gamble` | `cooldown_seconds` for `!gamble` |
| `activities.rep` | `cooldown_seconds` for `!rep` |
| `shop.items` | the full item catalog — id, name, description, price, sell price, category, rarity, `stackable`, **`giveable`**, `consumable`, custom **`bought_message` / `used_message` / `consumed_message` / `gave_message` / `sold_message`**, and `effects` (use actions: random money like a gift card, role grants, boosters, crates, tool multipliers) |

Every message field is **per item** and accepts **one string or an array of
strings** — an array picks a random line every time the event happens (e.g.
four different "you used the rose…" messages). Each action has its own field,
so one item can carry a different message set for every command:

| Field | Command | Example |
|---|---|---|
| `bought_message` | `!buy` | "You bought {item}. Whom are you gonna give it to? 👀" |
| `used_message` | `!use` | "You used the rose to make a bouquet." |
| `consumed_message` | `!eat` | "You ate a cookie and it's very tasty!" |
| `gave_message` | `!giveitem` | "{sender} just gave {user} a rose." |
| `sold_message` | `!sell` | "You sold {item} for {amount}." |

Message placeholders: `{item}`, `{qty}`, `{amount}` (formatted coins),
`{user}`, `{sender}`, and `{user_mention}` / `{sender_mention}` for
`gave_message`. `{amount}` is only filled when the action actually granted
coins — `!eat` pays nothing (it's flavor only), so `consumed_message`
templates should not use `{amount}`.

```jsonc
"bought_message": [
  "You bought {item}. Whom are you gonna give it to? 👀",
  "You bought {item} — someone's about to blush. 🌹"
],
"used_message": [
  "You used the rose to make a bouquet.",
  "You sniffed the rose and it smells amazing."
],
"gave_message": [
  "{sender} just gave {user} a rose.",
  "{sender} tossed {user} a rose."
],
"sold_message": [
  "You sold {item} for {amount}. A little sad, but okay. 🌹"
]
```

A food-style consumable (like Cookie or Cake) also sets `consumed_message`
so `!eat` shows eating flavor instead of `!use`'s message. Eating **never
pays coins** — the item is consumed for flavor only; use `!use` to get the
item's coin effect:

```jsonc
"consumed_message": [
  "You ate a {item} and it's very tasty! 🍪",
  "You dunked your {item} in milk. Perfect. 🍪"
]
```

A consumable item with **no `effects`** is a flavor item — `!use` simply
consumes it and shows the random `used_message`. Item use effects live in
`effects`:

```jsonc
// Grant a role when the item is used (by name — per guild — or numeric id)
"effects": { "role": "VIP" }
// Random money like a gift card
"effects": { "money_min": 100, "money_max": 600 }
// Existing: tool multiplier, boosters, crates
"effects": { "booster": { "type": "all", "multiplier": 2.0, "duration": 1800 } }
```

## Data persistence

All player data lives in the SQLite database (`fun2oosh.db`) and survives bot
restarts:

- **Wallets & banks** — balance, bank, prestige, reputation, daily streak, last claim times
- **Transaction history** — every work/gamble/buy/sell/transfer/etc. entry
- **Inventory** — owned items, quantities, durability, and expiry
- **Achievements** — which of the 53 achievements each user has unlocked
- **Server settings** — guild config, role income & claim records, audit log
- **Active money boosters** (lucky charm, 2x booster) — written to the DB on
  activation and reloaded at startup, so paid boosters keep running through restarts
- **Lottery** — pot, tickets, and winner history per guild

The only in-memory state (reset on restart): command cooldowns, pending trade
offers (they expire after 60s anyway), and in-progress casino rounds.

## Project structure

```
bot.py                 # Entry point + Fun2OoshBot class
config.py              # Re-exports Config
cogs/
  economy.py           # Wallet / income / progression commands
  casino.py            # Gambling games
  shop.py              # Shop, inventory, trading, crates
  activities.py        # hunt/fish/mine, monthly, networth, rep, achievements
  lottery.py           # Server jackpot with scheduled draws
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
  runtime_config.py    # data/config.json loader (activities + shop items)
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

## License

Released under the [MIT License](LICENSE).
