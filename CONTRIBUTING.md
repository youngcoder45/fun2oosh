# Contributing to Fun2Oosh Economy Bot

Thanks for contributing! This guide covers setting up a dev environment,
running the validation suite, and the extension patterns the codebase uses.

## Development setup

Requirements: **Python 3.10+** (CI also runs 3.12).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt       # runtime deps
.venv/bin/pip install -r requirements-dev.txt   # ruff + mypy
cp .env.example .env                            # add your DISCORD_TOKEN
```

Run the bot:

```bash
.venv/bin/python bot.py
```

## Validation (run before every push)

CI runs the exact same checks on every push/PR (see `.github/workflows/ci.yml`):

```bash
.venv/bin/ruff check .                                     # lint
.venv/bin/mypy .                                           # type check
.venv/bin/python -m compileall -q bot.py config.py cogs models services utils tests
.venv/bin/python tests/test_events.py                      # functional tests
```

Everything must pass. If you change embed/cooldown/collect behavior, also
exercise the command with a functional test (the `tests/` dir is the home
for these) or at minimum boot the bot against a throwaway SQLite DB and
verify the command registers:

```bash
DATABASE_URL=sqlite+aiosqlite:///check.db .venv/bin/python -c \
  "import asyncio; from bot import *; ..."   # see ci.yml for the full smoke test
```

Never commit local `.db` files — use `DATABASE_URL` pointing at a throwaway
file and delete it afterwards.

## Code style

- **Embeds** — build everything through the semantic builders in
  `utils/helpers.py` (`EmbedBuilder.success_embed`, `activity_embed`,
  `set_author_from_user`, ...). No hand-rolled embed styling.
- **Colors** — only the central palette constants
  (`COLOR_SUCCESS`, `COLOR_ERROR`, `COLOR_WARNING`, `COLOR_INFO`,
  `COLOR_GOLD`). Green is `#00ff00`, red is `#ff0000`, everything else is
  fixed. Never inline hex values.
- **Cooldown messages** — use `utils.cooldowns.cooldown_notice()` (Discord
  `<t:...:R>` / `<t:...:F>` timestamps). No manual duration strings like
  "try again in 2 hours".
- **Event text** — never hardcode activity narratives; add JSON entries to
  `data/events/*.json` instead (see below).
- **No emojis in code** (except the slots game and the configured
  `CURRENCY_NAME`) — keep source ASCII-clean.

## Extending the bot

### Add a command

Prefix and slash versions are kept in sync (see `cogs/economy.py`):

```python
@commands.command(name="ping", aliases=["p"])
@check_cooldown("ping", 30)
async def ping(self, ctx: commands.Context):
    await ctx.send("pong!")

@app_commands.command(name="ping", description="Ping pong")
@app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
async def ping_slash(self, interaction: discord.Interaction):
    await interaction.response.send_message("pong!")
```

### Add an event narrative

Open `data/events/<name>.json` and append a string. Placeholders:

- `{amount}` — the coin amount (or fine paid)
- `{currency}` — the configured `CURRENCY_NAME`
- `{user}` / `{guild}` — actor / server names

The pool is cached per-process; a restart picks up new entries. No code
changes needed. Pools are plain strings or `{"message": "..."}` objects.

### Add a database column

1. Add the column to the model (e.g. `models/wallet.py`).
2. Append `(column_name, 'DDL')` to `COLUMN_MIGRATIONS` in
   `utils/migrations.py` so existing databases are upgraded idempotently.
3. Never edit `fun2oosh.db` by hand.

### Money-moving rules

Any code that credits or debits a wallet must:

- Run under `lock_manager.for_user(user_id)` (or `for_users` for two-party
  operations) to prevent race conditions and double-spending.
- Create a `Transaction` row for every balance change.
- Commit once, atomically, after all writes.

## Pull requests

1. Fork the repo and create a feature branch.
2. Make your change and run the full validation suite above.
3. Open a PR — CI runs lint, typecheck, compile, the event tests, and a boot
   smoke test automatically.
4. Keep PRs focused; one logical change per PR.

## Reporting issues

Include the bot version/commit, the full command you ran, and the relevant
log output. Security issues (economy exploits, race conditions, fraud
bypasses) take priority — please mention them in the title.
