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
| `!collect pays your highest eligible income role` | Contextual hint |
| `responsible_gaming_notice()` | Responsible-gambling notice on casino results |

Removed: decorative `Economy • User ID: …`, `Casino • Blackjack Table`-style
footers, and all footers on work/daily/collect/crime/deposit/withdraw results.

---

## 3. Collect System Redesign

### Old system (removed)
`collect` paid a flat `collect_reward` amount on a cooldown — identical to
`work`, no progression, no server customization, no role incentive.

### New system: role-income based
Administrators assign an hourly income to roles (`!income add <role> <amount>`).
When a user runs `collect`:

1. The cog resolves the guild config (per-guild override of `collect_reward`).
2. `RoleIncomeService.highest_for(guild, user_role_ids)` finds the highest-paying
   income row among the roles the user holds.
3. If none exists, the base `collect_reward` rate is paid (labeled `Base rate`).
4. The cooldown is enforced per user via the existing `cooldown_manager`.

**Payout rule chosen: highest eligible role.** Rationale:

- **Predictable economy.** Combined payouts stack multiplicatively with role
  count and silently double economies as servers add roles. Highest-rate keeps
  the rate ceiling visible and auditable.
- **Matches role-tier mental models.** Servers model VIP > Premium > Member;
  "highest eligible" matches the hierarchy admins intend.
- **Simpler to balance.** One number per role, one winner per collect.
- **No exploit surface.** Combined payouts reward stacking throwaway roles;
  highest-rate rewards progression into a single better role.

### Collect embed (minimal, distinct from work)
```
Passive Income              [success green]
  Income Source : VIP                Balance: 12,480 coins
  Amount Earned : 750 coins
  Next claim    : in 23m 14s
```
Exactly four fields: source role, amount earned, updated balance, next claim.
No decorative emoji, no footer, no description — passive income, not a job.

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
| `role_income` (new) | `guild_id` (BigInt, PK, indexed), `role_id` (BigInt, PK), `hourly_rate` (Int). Persists all income configuration — nothing hardcoded, survives restarts. |

Migration: `utils/migrations.py` creates `role_income` idempotently for
existing databases; `Base.metadata.create_all` handles fresh installs.
`!reset_economy` now also clears the new `role_income` table.

---

## 6. Admin Commands Added

New `!income` group (manage_permissions required):

| Command | Action |
| --- | --- |
| `!income add <role> <amount>` | Set/overwrite hourly income for a role |
| `!income set <role> <amount>` | Alias for add (edit) |
| `!income remove <role>` | Remove a role's income rate |
| `!income list` | List all configured incomes, highest first |

All changes are written to the database and recorded in the audit log
(`!audit`). Values validated (positive, ≤ 1,000,000/hour).

---

## 7. Validation

- `ruff check .` — clean
- `mypy .` — clean (35 files)
- `py_compile` all modules — clean
- Boot smoke test — 57 commands registered across 5 cogs
- Functional test — income CRUD (set/remove/list/upsert), `highest_for`
  resolution, cooldown enforcement, empty-role fallback
- CI (`ci.yml`) — unchanged job now also covers the new files via ruff/mypy
