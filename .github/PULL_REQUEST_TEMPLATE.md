## Summary

<!-- What does this PR do? Keep it focused — one logical change per PR. -->

## Related issue

<!-- Closes #123, or "No related issue". -->

## Changes

<!-- Bullet list of the meaningful changes, e.g.
- New `!command` (prefix + slash) ...
- Added `data/events/...` narratives
- New `wallet.last_weekly_at` column + migration entry
-->

## Checklist

- [ ] Ran `ruff check .`
- [ ] Ran `mypy .`
- [ ] Ran `python -m compileall -q bot.py config.py cogs models services utils tests`
- [ ] Ran functional tests: `python tests/test_events.py`
- [ ] Booted the bot against a throwaway DB and verified new/changed commands register
- [ ] No `.db` files or other local artifacts committed
- [ ] Followed the code style in `CONTRIBUTING.md` (semantic embeds, palette colors only, `cooldown_notice()` timestamps, no hardcoded event text, no emojis except slots/`CURRENCY_NAME`)
- [ ] Any new database column has a matching entry in `COLUMN_MIGRATIONS` (`utils/migrations.py`)
- [ ] Money-moving code uses `lock_manager` + `Transaction` rows + a single atomic commit
- [ ] Docs updated if behavior changed (README, `UI_REDESIGN.md`, `AUDIT.md`)
- [ ] PR title and description follow the templates above

> CI runs lint, typecheck, compile, the event tests, and a boot smoke test automatically — make sure they're green before requesting review.
