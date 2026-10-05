# CLAUDE.md

Tracks SUUMO (suumo.jp) for-sale listings for the areas in `scope.toml`: crawls search results daily, fetches each
listing's own page once, keeps lifecycle state in SQLite, exports git-tracked JSONL to `data/`, and posts changes
to Discord. Runs locally on a Mac under launchd. `README.md` is the operator guide (setup, commands, record fields).

## Layout

| File | Role |
|---|---|
| `suumo/cli.py` | Entry point (`python -m suumo`). Thin: builds `Ctx` (DB, archive, scope, `.env`), takes the run lock, dispatches |
| `suumo/pipeline.py` | `Pipeline`: `crawl_lists` → `reconcile` per area → `process_queue` → `purge`. All lifecycle rules and constants live here |
| `suumo/parse.py` | `TYPES`, area-selection pages (`parse_areas`), search-result pages (`parse_list_page`), shared value parsers (prices, m², stations, town) |
| `suumo/detail.py` | Listing pages: spec table → normalized fields (`parse_detail` returns `(fields, meta)`) |
| `suumo/db.py` | SQLite schema, small query helpers, `dumps` (canonical JSON), `exclusive` (flock) |
| `suumo/export.py` | DB → `data/` JSONL; `merge` (list + detail fields), `FIELD_ORDER`, `dup_key` |
| `suumo/notify.py` | Events → Japanese Discord messages (`compose`) and REST posting (`notify`) |
| `suumo/maintenance.py` | `prune` (out-of-scope data) and `reparse` (rebuild parsed fields from `archive/`) |
| `suumo/archive.py` | Raw HTML store: daily search-result snapshots, latest copy of each listing page |
| `suumo/scope.py` | `scope.toml` → `Target(pref, type, areas)` list; `in_scope` |
| `suumo/http.py` | `Client`: one request at a time, delay + jitter, retries; 404/410 → `FileNotFoundError` |
| `suumo/gitdata.py` | `commit_data`: commit `data/` only, optionally push |
| `local.suumo.plist` | launchd template (daily 04:00, `run --push`) |

Imports flow one way: `cli` → `pipeline`/`maintenance`/`export`/`notify`/`gitdata` → `detail`/`parse`/`db`/`archive`/`scope`/`http`.
`parse` and `detail` are pure (HTML in, dicts out) and never touch the DB or network.

## Data model

- `listings` (PK `type, id`): `list_json` (search-result fields, refreshed every crawl), `detail_json` (listing-page
  fields), `status` active/removed, `first_seen`, `last_seen`, `missed`, `removed_at`, plus DB-only bookkeeping
  (`detail_fetched`, `info_date`, `next_update`).
- `areas` (PK `pref, type, code`): `slug`, `baselined`, `last_hits`, `last_status` (complete / incomplete / suspect / error).
- `queue` (PK `type, id`): `priority` 2 new / 1 changed / 0 backfill, `attempts`, `last_error` (`parse:` prefix = parser failure).
- `events`: `kind` new / price_changed / removed / relisted, `payload` (a summary snapshot so a message can be written
  after the row is gone), `posted` 0 pending / 1 posted / -1 skipped.
- `runs`: one row per run with a JSON report.
- IDs are SUUMO `nc_` numbers, per agent listing. The same property listed by several agents has several IDs;
  `dup_key` (type + building/address + size + price) groups them.
- `list_json`/`detail_json` are stored with `db.dumps` (sorted keys) so equal data is equal text.

## Invariants (keep these true)

- **Only robots.txt-allowed paths.** Search results: `/{TYPES[type]}/{pref}/{slug}/?pc=100&page=N`; area pages:
  `/{type path}/{pref}/city/`; listing pages: the `path` from the search result. `/jj/bukken/ichiran/...` and any
  `sort=` parameter are disallowed. One request at a time, `--delay` 1.5 s + up to 50% jitter.
- **Removal needs evidence.** A listing is removed only after `REMOVE_AFTER_MISSES` (2) consecutive *complete*
  crawls of its area miss it. Incomplete/error areas never count; `suspect` areas (lost >30% of ≥20) never remove.
  An area that vanishes from the area page is reconciled as complete with 0 hits (same guards apply).
- **Baselines are silent.** An area's first complete crawl creates no events; its listings queue as backfill.
  New scope entries therefore never flood Discord.
- **Archive before parse.** Every fetched page is saved to `archive/` before parsing; a parser exception is logged and
  recorded in the queue, never fatal. Fix the parser, then `reparse` (no requests).
- **Deterministic export.** `data/` files are sorted by id with `FIELD_ORDER` key order and contain nothing that
  changes on its own (`last_seen`, `missed`, `info_date`, `next_update`, `detail_fetched` stay in the DB). An
  unchanged day must be an empty git diff; check this after any export change.
- **Discord posts are exactly-once per message.** `compose` returns `(text, seqs)`; events are marked posted only
  after their own message is accepted.
- **One writer.** Writing commands hold `state.db.lock`; `status` is read-only.
- **All user-facing (Discord) text is Japanese** and lives in `notify.py`. Code, logs and docs are English.

## SUUMO quirks the parsers rely on

- Area-page counts can contain commas: `(2,253)`. A count parsed as 0 would silently skip a ward.
- The `new_condo` area page lists only areas that currently have developments, in a different markup
  (`label > a` + `<span>(N)</span>`) from the other types (`js-linkSc###` ids, `searchitem-list-value`).
- `new_house` search results include land listings sold with a build condition; their `path` is under `/tochi/`
  (so `url` is stored, not derived) and their price excludes the building.
- `-` means "not stated" everywhere; `clean()` drops it. Prices: `8702万8000円`, ranges `A～B` or `A・B`, footnotes after `※`.
- Search results show one station; listing pages show up to 3 (each in its own `<div>`), sometimes including a bus
  stop (`line` containing バス → `bus_stop: true`).
- Listing-page labels end in `ヒント` (a help link) and the summary and full spec tables repeat labels; `spec_table`
  strips the suffix and keeps the first value. Deal type is `取引態様：＜…＞` in the agent block.
- `land_rights` text can be a paragraph; `_tenure` maps it to a category and keeps the text in `land_rights_note`.

## Extending

- **New field from listing pages:** parse it in `detail.parse_detail` (or `parse._standard_unit` for search results),
  add it to `export.FIELD_ORDER`, add a test against a fixture, then `reparse`. No re-crawl needed while the pages
  are in `archive/`. Document it in the README field table.
- **New prefecture/area/type:** edit `scope.toml` only. `prune --yes` removes data that leaves the scope.
- **New property type:** add it to `parse.TYPES` (path segment), check its search-result markup in `parse_list_page`,
  add `TYPE_JA` in `notify.py`.
- **New event kind:** emit it in `Pipeline.reconcile`, add a section to `notify.SECTIONS`.
- **Filters / interactive bot:** read `data/<pref>/*.jsonl` or `state.db`; keep crawling separate from the bot.
  `DISCORD_STATE_CHANNEL_ID` is reserved for bot settings (as in the gym bot: state as a message attachment).
- **Tunables** are module constants: `pipeline.py` (misses, retention, suspect thresholds, priorities),
  `archive.LIST_DAYS`, `notify.PER_SECTION`/`STALE_HOURS`.

## Development

- `uv sync`, then `uv run pytest -q` (offline, <1 s) and `uv run ruff check suumo tests` (line length 120).
- Tests: `test_lifecycle.py` (reconcile/purge/export/notify text with synthetic records), `test_parsers.py` (value
  parsers), `test_pages.py` (real pages in `tests/fixtures/`), `test_ops.py` (Discord posting, prune, reparse, lock,
  git commit). Tests never touch the network or Discord.
- Try changes on a copy: `--db`, `--archive`, `--data` point anywhere (`python -m suumo --db /tmp/s.db ... run`).
- Testing parser changes without requests: `reparse`, then `git diff -- data` shows exactly what changed.
- The user's shell auto-loads `.env` on `cd`; the CLI loads `.env` with `override=True` so the file always wins.
- Scheduled runs can't read `~/Documents`, `~/Desktop`, `~/Downloads` (macOS privacy); the README installs to `~/suumo`.
- Never commit `.env`, `state.db*`, `archive/`, `logs/`.
