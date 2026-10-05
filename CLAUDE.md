# CLAUDE.md

Tracks SUUMO (suumo.jp) for-sale listings for the areas in `scope.toml`: crawls search results daily, fetches each
listing's own page once, keeps lifecycle state in SQLite, exports git-tracked JSONL to `data/`, and posts changes
to Discord. A separate always-running search bot (`python -m suumo bot`) lets non-technical, Japanese-speaking
people browse `data/` with buttons, star listings and get DMs for saved searches. Both run locally on a Mac under
launchd. `README.md` is the operator guide (setup, commands, record fields, bot screens).

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
| `suumo/catalog.py` | `data/` → in-memory search index: `Query` (conditions), `Item`, `Snapshot` (bitset `mask`, reference `matches`, `search`, `count`, facets, `loosen`), `Catalog.refresh` |
| `suumo/bot/app.py` | `SuumoBot`: lifecycle, keeps the menu newest in the channel, data watcher, DM alerts, state saving |
| `suumo/bot/ui.py` | `MainMenu` (persistent) and `Session` (one person's ephemeral screens: panel, sub-screens, results, detail, favorites, saved) |
| `suumo/bot/text.py` | Every Japanese string of the bot and the embed/text formatters |
| `suumo/bot/store.py` | Per-person data (`User`, `Store`) and `DiscordStateBackend` (state.json in #state + local backup) |
| `suumo/bot/alerts.py` | Which run events go to whom (pure) |
| `local.suumo.plist` | launchd template (daily 04:00, `run --push`) |
| `local.suumo-bot.plist` | launchd template for the bot (always running) |

Imports flow one way: `cli` → `pipeline`/`maintenance`/`export`/`notify`/`gitdata` → `detail`/`parse`/`db`/`archive`/`scope`/`http`.
The bot: `cli` → `bot.app` → `bot.ui` → `bot.text`/`bot.store`/`bot.alerts` → `catalog` → `parse` (and `notify` for
shared formatters). `parse`, `detail`, `catalog` and `bot.alerts` are pure and never touch the DB or network.

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
- **All user-facing (Discord) text is Japanese** and lives in `notify.py` (feed) and `bot/text.py` (bot). Code,
  logs and docs are English.

## Search bot rules

- **Reads `data/` only**, never `state.db`, and takes no run lock: the crawl and the bot never block each other.
  `export` replaces every file atomically; an events file that doesn't parse is skipped until it does.
- **Buttons only.** No typed commands, no Message Content intent (default intents). Everything a menu button opens
  is ephemeral and edited in place: state change → redraw in the same response (Discord's 3 s), save afterwards.
- **Filters have two implementations that must agree:** `Snapshot.mask` (bitsets, used for search/counts/facets)
  and `Snapshot.matches` (per listing, used for alerts). `test_bitset_index_agrees_with_the_reference_rules`
  checks them on random data; change both together.
- Duplicates (`dup_key`) count once everywhere (counts, results, alerts). Land listed under both `new_house` and
  `land` is kept once, as land. Stations are identified by name (the same station on two lines is one choice).
- 新着 is a `new`/`relisted` event within 7 days (baselines are never 新着); 値下げ is a drop within 30 days.
- A building condition (間取り, 広さ, 築年数, 新耐震) excludes land unless the person chose 土地 explicitly.
- **Alerts reach each person once per run:** `User.last_run` advances only after their DM (or the channel fallback)
  is accepted, and is saved before the next person. A new saved search or favorite starts from the newest run.
  こだわり conditions count as met while a listing's page isn't fetched yet.
- **Menu stays the newest message in the channel:** after any message there (the crawl's feed posts arrive from
  the same bot user), `tidy_channel` deletes our old menus and posts a new one, silently. Never delete feed posts.
- `MainMenu` custom_ids (`suumo:*`) are fixed: don't rename them. Session custom_ids are `s:<sid>:<name>`;
  unknown sessions (after a restart) get 「古くなりました」 from `on_interaction`.
- Discord limits are asserted in `test_bot_ui.assert_discord_limits` (5 rows, 25 options, 100/80-char labels,
  2000-char content, 6000-char embeds); run it over any new screen.
- State in #state: `state.json`, `store.VERSION` (refuses other versions). Bump it and migrate when the shape
  changes incompatibly. `Query.from_dict` ignores unknown keys, so adding a condition needs no migration.

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
- **New search condition:** a `Query` field, its rule in both `Snapshot.mask` and `Snapshot.matches` (plus the
  random agreement test's query generator), a control on a `Session` screen, its label in `text.condition_lines`
  and `text.FIELD_JA`, and tests in `test_catalog.py` / `test_bot_ui.py`.
- **New bot screen:** a `draw_<name>` method on `Session` that adds components and returns `(content, embeds)`;
  switch to it with `self.go("<name>")`. Strings go in `bot/text.py`.
- **Tunables** are module constants: `pipeline.py` (misses, retention, suspect thresholds, priorities),
  `archive.LIST_DAYS`, `notify.PER_SECTION`/`STALE_HOURS`, `catalog.NEW_DAYS`/`DROP_DAYS`, `bot.ui` (page size,
  session timeout), `bot.store` (max saved searches/favorites), `bot.text` (price/size/age choices).

## Development

- `uv sync`, then `uv run pytest -q` (offline, ~1 s) and `uv run ruff check suumo tests` (line length 120).
- Tests: `test_lifecycle.py` (reconcile/purge/export/notify text with synthetic records), `test_parsers.py` (value
  parsers), `test_pages.py` (real pages in `tests/fixtures/`), `test_ops.py` (Discord posting, prune, reparse, lock,
  git commit), `test_catalog.py` (every filter, folding, sorts, facets, reload), `test_bot_ui.py` (screens with
  fake interactions over real listings in `tests/fixtures/data/`), `test_bot_ops.py` (alerts, store, state
  message, menu upkeep). Tests never touch the network or Discord.
- Try the bot on test data: `python -m suumo --data /tmp/x/data bot` (it uses the real channels in `.env`).
- Try changes on a copy: `--db`, `--archive`, `--data` point anywhere (`python -m suumo --db /tmp/s.db ... run`).
- Testing parser changes without requests: `reparse`, then `git diff -- data` shows exactly what changed.
- The user's shell auto-loads `.env` on `cd`; the CLI loads `.env` with `override=True` so the file always wins.
- Scheduled runs can't read `~/Documents`, `~/Desktop`, `~/Downloads` (macOS privacy); the README installs to `~/suumo`.
- Never commit `.env`, `state.db*`, `archive/`, `logs/`, `bot.lock`, `bot_state.backup.json`.
