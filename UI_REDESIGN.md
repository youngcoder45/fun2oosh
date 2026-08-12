# Economy UI & Collect System Redesign

Deliverables for the presentation-layer redesign of the economy system and the
replacement of the flat `collect` mechanic with role-based passive income.

---

## 1. Design System

A unified embed design system now lives in `utils/helpers.py`:

- **Semantic colors** — `COLOR_INFO` (blurple, informational), `COLOR_SUCCESS`
  (green, rewards/wins), `COLOR_ERROR` (red, failures/losses), `COLOR_WARNING`
  (yellow, cooldowns/warnings), `COLOR_GOLD` (achievements/jackpots).
  Every embed picks a color by meaning, not by mood.
- **Embed builders** — `EmbedBuilder.info_embed / success_embed / error_embed /
  warning_embed` produce consistent titles, descriptions and colors, so no two
  commands hand-roll the same look differently.
- **Shared helpers** — `format_coins`, `format_duration`, `responsible_gaming_notice`,
  `RARITY_COLORS`-style lookup helpers centralize formatting so numbers and
  durations render identically everywhere.

**Design rules enforced across the codebase:**

| Rule | Applied to |
| --- | --- |
| Semantic colors only (no arbitrary hex) | All economy, shop, casino, admin, activities embeds |
| No decorative emoji or random symbols | All embeds (verified by CI emoji sweep) |
| No decorative separator fields / walls of text | Removed all `━━━━` blocks and code-block dumps |
| Concise titles + descriptions | Every command embed rewritten |
| Footer only when it adds value | Pagination state, item IDs, cooldown hints, offer expiry, responsible-gaming notices only |
| Buttons only where interaction is meaningful | Shop, inventory, leaderboards, trades, confirmations — **not** work/daily/collect/balance |

---

## 2. Embeds Redesigned

**economy.py** (`!balance`, `!networth`, `!profile`, `!gamble`, `!leaderboard`,
`!richest`, `!transactions`): clean field grouping (Wallet / Bank / Net Worth /
Inventory), gold accent on totals, paginated leaderboard with page footers,
profile without decorative clutter.

**shop.py** (`!shop`, `!iteminfo`, `!inventory`, `!buy`, `!sell`, `!use`,
`!giveitem`, `!trade`): rarity-colored item embeds, "Stackable / Usable /
Category / Rarity / Price / Sell Price" field layout, paginated catalog and
inventory with page state in the footer.

**casino.py** (blackjack, roulette, slots, coinflip, dice, crash, russian
roulette, war, baccarat, high-low, keno, poker): unified color scheme, clean
"YOU / DEALER / WAGER / OUTCOME" fields, removed separator bars and all-caps
walls, responsible-gaming footer on every table result.

**admin_economy.py** (`!econfig`, `!income`, `!shoplist`, `!audit`,
`!reset_economy`): consistent info/success/error embeds, contextual hint
footers (e.g. `!econfig set <key> <value>`).

**activities.py** (`!hunt`, `!fish`, `!mine`, `!crime`, `!rob`, `!work`,
`!beg`, `!search`): unified success/error colors, removed decorative footers.

### Footer audit (only value-adding footers remain)

| Footer | Why it stays |
| --- | --- |
| `Page X/Y` (leaderboard, shop, inventory, catalog, transactions, audit) | Pagination state |
| `Item ID: … Buy with: !buy …` | Contextual hint for the command |
| `Offer expires in 60 seconds` | Trade deadline context |
| `!econfig set <key> <value>` | Contextual hint |
| `!collect pays every income role you hold` | Contextual hint |
| `responsible_gaming_notice()` | Responsible-gambling notice on casino results |

Removed: decorative `Economy • User ID: …`, `Casino • Blackjack Table`-style
footers, and all footers on work/daily/collect/crime/deposit/withdraw results.

---

## 3. Collect System Redesign (role income)

### Old system (removed)
`collect` paid a flat `collect_reward` amount on a global cooldown — identical
in feel to `work`, with no role incentive and no server customization.

### New system: role income with per-role claim intervals
Administrators configure income per role — **amount** and **claim interval** —
via the `/role-income` slash group (or `!income` prefix group):

```
/role-income set <role> <amount> [interval]   e.g. interval: 2h / 30m / 1d
/role-income remove <role>
/role-income list
```

When a user runs `collect`:

1. `RoleIncomeService.all_for(guild, user_role_ids)` finds **every** income
   role the user holds. No roles → clear "ask an admin" message;
   **no flat fallback payout** (the old `collect_reward` setting was removed).
2. Each role's **own claim interval** is checked against the user's last claim
   for that role, persisted in the `role_claims` table — windows survive bot
   restarts (in-memory cooldowns would reset them).
3. Every role whose window has passed is paid on its own timer; a user holding
   several roles collects the **combined** total. Roles still cooling down
   simply wait for their own next window.
4. If every role is cooling down, the user gets a Discord **relative timestamp**
   for the earliest claim.

**Payout rule: every eligible role (stacking).** The user asked for multiple
role rewards to stack rather than pay only the highest. Each role keeps its
configured interval (VIP every 2h, Member every hour — holding both pays both).

### Collect embed (minimal — an admin-granted claim, not a job)
```
Role Income Claim           [success green]
  Income Sources:                       Total Earned: 950 coins
    VIP — +750 coins
    Member — +200 coins
  Balance  : 12,480 coins
  Next Claim: <t:1730000000:R>   → renders "in 1 hour"
```
Single-role claims render as a simple `Income Source` field. Multi-role claims
show a per-role breakdown and total. Next claim is the soonest role window as
a Discord relative timestamp. No footer, no decorative emoji — a role perk,
not another `work`.

### Database changes

| Table | Change |
| --- | --- |
| `role_income` | `hourly_rate` renamed to `amount` (per-interval, not hourly); new `claim_interval` column (seconds, default 3600). Migration handles legacy DBs. |
| `role_claims` (new) | `(guild_id, user_id, role_id, claimed_at)` composite PK — persists per-user claim timing across restarts. |

`!reset_economy` now also clears `role_claims`.

---

## 4. Components V2 Usage

Interactive components were added only where they provide real interaction value:

| Command / Flow | Component | Why |
| --- | --- | --- |
| `!shop` | Category dropdown (All/Tool/Consumable/Booster/Crate/Collectible) + Prev/Next page buttons | Browse + filter without retyping commands |
| `!inventory` / `!shoplist` | Prev/Next page buttons | Pagination navigation |
| `!leaderboard` / `!richest` | Prev/Next page buttons | Pagination navigation |
| `!trade` | Accept / Decline / Cancel buttons | Both parties interact with the offer without reactions |
| `!reset_economy` | Confirm Reset / Cancel buttons | Replaces the **broken** empty-string reactions (`add_reaction("")` failed since emoji removal) with a proper confirmation dialog |

**Deliberately not buttoned:** `!work`, `!daily`, `!weekly`, `!collect`,
`!balance`, `!networth`, `!crime`, `!rob`, `!beg`, `!search`, and simple
success/failure responses — one-shot results with no follow-up interaction.

---

## 5. Database Changes

| Table | Change |
| --- | --- |
| `role_income` (new) | `guild_id` (BigInt, PK, indexed), `role_id` (BigInt, PK), `amount` (Int), `claim_interval` (Int seconds). Persists all income configuration — nothing hardcoded, survives restarts. |
| `role_claims` (new) | Per-user claim timing so intervals survive restarts. |

Migration: `utils/migrations.py` creates `role_income` idempotently for
existing databases (renaming the legacy `hourly_rate` column to `amount` and
adding `claim_interval`); `Base.metadata.create_all` handles fresh installs.
`!reset_economy` clears both tables.

---

## 6. Admin Commands Added

Slash group `/role-income` and prefix group `!income` (administrator
permission required):

| Command | Action |
| --- | --- |
| `/role-income set <role> <amount> [interval]` | Set/overwrite role income with claim window (e.g. `2h`, `30m`, `1d`) |
| `/role-income remove <role>` | Remove a role's income |
| `/role-income list` | List configured incomes + intervals, highest first |
| `!income add/set <role> <amount> [interval]` | Prefix equivalents |

All changes are written to the database and recorded in the audit log
(`!audit`). Values validated (positive amount ≤ 1,000,000; interval between
1 minute and 30 days).

---

## 7. Validation

- `ruff check .` — clean
- `mypy .` — clean (35 files)
- `py_compile` all modules — clean
- Boot smoke test — 57 commands registered across 5 cogs
- Functional test — income CRUD (set/remove/list/upsert), `all_for`
  resolution, per-role cooldown enforcement, combined multi-role payout
- Event tests — `tests/test_events.py` (committed) verifies pool sizes,
  placeholder rendering, custom currency, DM fallback, missing-pool
  fallback, and amount formatting; runs in CI
- CI (`ci.yml`) — ruff/mypy/compile jobs cover all new files; new
  "Event system test" step runs `tests/test_events.py`

---

## 8. Dynamic Event Messages

Economy activity commands pull their narrative text from JSON pools instead
of static strings, so results read like a living economy game and admins can
add events by editing JSON — no code changes.

### Commands converted

| Command | Pool(s) | Events |
|---|---|---|
| `!work` / `/work` | `work` | 108 |
| `!crime` | `crime_success` / `crime_failure` | 105 / 104 |
| `!search` | `search_success` / `search_failure` | 106 / 106 |
| `!beg` | `beg_success` / `beg_failure` | 80 / 79 |
| `!hunt` | `hunt` / `hunt_failure` | 80 / 45 |
| `!fish` | `fish` / `fish_failure` | 78 / 45 |
| `!mine` | `mine` / `mine_failure` | 79 / 45 |

Not converted (per spec): gambling, daily/weekly/monthly, collect,
deposit/withdraw, balance, pay, shop, inventory.

### Engine (`services/events.py`)

- JSON pools live in `data/events/<name>.json` and are lazy-loaded + cached.
- Placeholders: `{amount}`, `{currency}`, `{user}`, `{guild}` — currency
  comes from `Config.currency_name`, never hardcoded. Unknown placeholders
  are left untouched so new ones can be added without breaking pools.
- `event_message()` formats the amount with thousands separators and fills
  placeholders; cogs pass a `fallback` so commands still have text if a pool
  file is missing or unreadable.
- `utils.helpers.event_names()` resolves `(user, guild)` names, falling back
  to the user's name in DMs.

### Extensibility

Add a line to a pool JSON and the next command run picks it up (cache is
per-process; `events.reload()` clears it). Pools support both plain strings
and objects with a `message` key for future metadata.

---

## 9. Discord Timestamp Cooldowns

All cooldown messages across the bot render through Discord timestamps
instead of manual duration strings (no more "try again in 23 hours").

### Standard format

```
Work cooldown active.
Try again <t:1735768800:R>
Available at <t:1735768800:F>
```

Discord renders the first as a live-updating relative time ("in 2 hours")
and the second as an absolute time ("January 1, 2027 5:00 PM").

### Centralized implementation

- `utils.cooldowns.cooldown_notice(key, remaining_seconds)` — computes the
  expiry epoch once and emits both `:R` and `:F` timestamps. Used by:
  - `check_cooldown` decorator (work, weekly, beg, crime, search, rob,
    gamble, hunt, fish, mine, rep)
  - `bot.py` prefix + slash `CommandOnCooldown` handlers (slash work/daily/
    weekly and any `app_commands.checks.cooldown`)
  - `cogs/casino.py` blackjack cooldown
- `services/progression.py` daily/monthly — the DB-backed cooldowns compute
  the expiry from the stored `last_daily_at`/`last_monthly_at` plus the
  interval and reuse the same notice (DB already stores claim times, so no
  schema change was needed).
- `!collect` role-income cooldown message uses the same `<t:R>`/`<t:F>` pair.

### What was removed

- Manual remaining-time math (`hours_left`, `day(s)` counters) from daily /
  monthly messages.
- "N seconds before using X again" strings from the prefix decorator, the
  slash error handlers, and blackjack.

`format_duration` remains only for admin *configuration display* (e.g.
`/role-income` listing "pays X every 2h") — a fixed interval, not a
cooldown countdown.

---

## 10. UnbelievaBoat-Style Activity Headers

Activity commands render the command as a small lowercase **author header**
instead of an embed title, with the event narrative as the description and
an optional trailing balance field:

```
worked
You repaired three motorcycles and earned 142 coins.

New Balance
12,450 coins
```

### Layout

- `embed.set_author(name=...)` — lowercase activity label, no title, no
  footer.
- Description — the generated event text (primary focus).
- Optional `New Balance` field (uses `Config.currency_name`).

### Headers

| Command | Header | Outcome colors |
|---|---|---|
| `!work` / `/work` | worked | success green |
| `!crime` | crime | success green / failure red |
| `!search` | search | success green / failure yellow |
| `!beg` | begged | success green / failure red |
| `!hunt` | hunted | success green / failure red |
| `!fish` | fished | success green / failure red |
| `!mine` | mined | success green / failure red |
| `!rob` | robbed | success green / failure red |

### Implementation

- `EmbedBuilder.activity_embed(header, description, *, color, balance,
  currency)` — the single builder behind all activity embeds.
- Balance is read from the wallet in the command's existing session (success
  paths and the failure paths that already touch the wallet — crime, rob).
- Daily/weekly/monthly/collect/casino embeds are unchanged (not activity
  commands).
