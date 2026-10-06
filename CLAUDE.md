# CLAUDE.md

Tracks SUUMO (suumo.jp) for-sale listings for the areas in `scope.toml` (all of Tokyo): crawls search results
daily, fetches each listing's own page once, keeps lifecycle state in SQLite, exports git-tracked JSONL to
`data/`, and publishes a static search site on GitHub Pages (https://huangwaylon.github.io/suumo/), rebuilt by
GitHub Actions on every push. The crawl runs locally on a Mac under launchd. `README.md` is the operator guide
(setup, commands, record fields, the site). The repo is public.

## Layout

| File | Role |
|---|---|
| `suumo/cli.py` | Entry point (`python -m suumo`). Each command declares its access: `write` (state.db + run lock), `read` (`status`), `files` (`geocode`, `site`: data/ and geo/ only) |
| `suumo/pipeline.py` | `Pipeline`: `crawl_lists` → `reconcile` per area → `process_queue` → `purge`. All lifecycle rules and constants live here; progress is saved to the run's report for `status` |
| `suumo/parse.py` | `TYPES`, area-selection pages (`parse_areas`), search-result pages (`parse_list_page`), shared value parsers (prices, m², stations, town) |
| `suumo/detail.py` | Listing pages: spec table → normalized fields (`parse_detail`) |
| `suumo/db.py` | SQLite schema, small query helpers, `dumps` (canonical JSON), `exclusive` (flock) |
| `suumo/export.py` | DB → `data/<pref>/<type>/<area>.jsonl` (one file per type and area, rewritten only on change); `merge` (list + detail fields), `FIELD_ORDER`, `dup_key` |
| `suumo/maintenance.py` | `prune` (out-of-scope data) and `reparse` (rebuild parsed fields from `archive/`) |
| `suumo/archive.py` | Raw HTML store: daily search-result snapshots, latest copy of each listing page; `write_atomic` (used by all file writers) |
| `suumo/scope.py` | `scope.toml` → `Target(pref, type, areas)` list; `in_scope` |
| `suumo/http.py` | `Client`: one request at a time, delay + jitter, retries, automatic slow-down; 404/410 → `FileNotFoundError` |
| `suumo/gitdata.py` | `commit_data`: commit `data/` and `geo/` only, optionally push |
| `suumo/catalog.py` | `data/` → `Snapshot` of `Item`s with the derived fields the site searches on (rooms, sizes, age, walk times, flags), duplicate groups, 新着/値下げ and price history from `data/events/` |
| `suumo/geo.py` | Town (丁目) coordinates from 国土地理院's address search, cached in `geo/towns.json` |
| `suumo/stations.py` | Station names in kana and English from Wikidata (one query per prefecture), cached in `geo/stations.json`; the site searches and shows them |
| `suumo/site.py` | `build`: `data/` + `geo/` → `_site/` (static assets from `site/`, `data/index.json`, one JSON per listing) |
| `site/filter.js` | The search rules, the only implementation: load the columnar index, `CHECKS`/`BUILDING` (one check per condition), `search`, `count`, `facets` (every choice count in one pass) |
| `site/i18n.js` | Every user-facing string, Japanese (default) and English; `t(key, ...args)` in app.js. A test requires every Japanese key to have an English one |
| `site/app.js`, `index.html`, `style.css` | The page: URL state, one filter panel (rail on desktop, sheet elsewhere), results, lazily loaded Leaflet map, listing view, favorites (localStorage, shareable as `#ids=`). Layout by CSS breakpoints: <700 phone, <1280 tablet, desktop |
| `.github/workflows/pages.yml` | On push: lint, tests (incl. Node), build, deploy to Pages (actions pinned by SHA) |
| `local.suumo.plist` | launchd template (daily 04:00, `run --push`) |

Imports flow one way: `cli` → `pipeline`/`maintenance`/`export`/`gitdata`/`geo`/`site` →
`detail`/`parse`/`db`/`archive`/`scope`/`http`/`catalog`. `parse`, `detail` and `catalog` are pure.

## Data model

- `listings` (PK `type, id`): `list_json` (search-result fields, refreshed every crawl), `detail_json` (listing-page
  fields), `status` active/removed, `first_seen`, `last_seen`, `missed`, `removed_at`.
- `areas` (PK `pref, type, code`): `slug`, `baselined`, `last_hits`, `last_status` (complete / incomplete / suspect / error).
- `queue` (PK `type, id`): `priority` 2 new / 1 changed / 0 backfill, `attempts`, `last_error` (`parse:` prefix = parser failure).
- `events`: `kind` new / price_changed / removed / relisted, `payload` `{"price"}` (+ `"old_price"` for price
  changes), exported per run to `data/events/<run_id>.json`; the site's 新着, 値下げ and price history come from them.
  (Older databases also carry unused columns: `events.posted`, `listings.detail_fetched/info_date/next_update`,
  `areas.last_crawled`.)
- `runs`: one row per run with a JSON report (`progress` while running).
- IDs are SUUMO `nc_` numbers, per agent listing. The same property listed by several agents has several IDs;
  `dup_key` (type + building/address + size + price) groups them.
- `list_json`/`detail_json` are stored with `db.dumps` (sorted keys) so equal data is equal text.

## Invariants (keep these true)

- **Only robots.txt-allowed paths.** Search results: `/{TYPES[type]}/{pref}/{slug}/?pc=100&page=N`; area pages:
  `/{type path}/{pref}/city/`; listing pages: the `path` from the search result. `/jj/bukken/ichiran/...` and any
  `sort=` parameter are disallowed. One request at a time, `--delay` 1.5 s + up to 50% jitter (a one-off
  backfill may use 1.0 s; never lower, never parallel). `http.Client` slows itself down on 429/5xx/network
  errors/slow responses (doubling, max 10 s, honours `Retry-After`) and eases back; keep that behaviour.
  Geocoding is also one request at a time (`geo.GEO_DELAY`).
- **Removal needs evidence.** A listing is removed only after `REMOVE_AFTER_MISSES` (2) consecutive *complete*
  crawls of its area miss it. Incomplete/error areas never count; `suspect` areas (lost >30% of ≥20) never remove.
  An area that vanishes from the area page is reconciled as complete with 0 hits (same guards apply).
- **Baselines are silent.** An area's first complete crawl creates no events (so nothing is 新着); its listings
  queue as backfill.
- **Archive before parse.** Every fetched page is saved to `archive/` before parsing; a parser exception is logged and
  recorded in the queue, never fatal. Fix the parser, then `reparse` (no requests).
- **Deterministic export.** `data/` files are sorted by id with `FIELD_ORDER` key order and contain nothing that
  changes on its own (`last_seen`, `missed` stay in the DB). An
  unchanged day must be an empty git diff; check this after any export change. Files are replaced atomically.
- **One writer.** Writing commands hold `state.db.lock`; `status`, `geocode` and `site` don't.
- **One implementation of the search rules: `site/filter.js`.** Python only derives fields (`catalog.Item`,
  `site.build_index`) so the JS compares plain values; flag bits travel in the index (`site.FLAGS`). The rule tests
  in `tests/test_site.py` build a site from synthetic records and run queries through filter.js in Node.
- **Rules the site shows:** duplicates (`dup_key`) are folded at build time (`site.representatives`: the listing
  with its page fetched and most tags; the others become `others` links). Land
  listed under both `new_house` and `land` is kept once, as land. Stations are identified by name. A building
  condition (間取り, 広さ, 築年数, 新耐震) excludes land unless 土地 was chosen explicitly. 新着 = a new/relisted
  event within 7 days, 値下げ = a drop within 30 days.
- **Public repo:** nothing secret in git (no `.env`, no tokens); commits use the GitHub noreply address
  (`git config user.email` is set in this repo). `uv.lock` isn't committed (it would pin the local package mirror).
- **User-facing text lives in `site/i18n.js`**, in Japanese (default) and English; never hard-code it in app.js or
  index.html (static text uses `data-t`, `data-t-label`, `data-t-placeholder`). Theme is `data-theme` on <html>,
  set before the first paint by the inline script in index.html. Code, logs and docs are English.

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
- Image URLs are SUUMO resize URLs (`resizeImage?src=…&w=&h=`); any size works, the site asks for 360×270 (cards), 640×480 (listing) and 120×90 (map popups). SUUMO pads small originals instead of enlarging them, so larger sizes make some photos smaller.
- Addresses mostly stop at the 丁目 (full-width digit); some add 番地 in ASCII digits, which `geo.town_of` drops.

## Extending

- **New field from listing pages:** parse it in `detail.parse_detail` (or `parse._standard_unit` for search results),
  add it to `export.FIELD_ORDER`, add a test against a fixture, then `reparse`. No re-crawl needed while the pages
  are in `archive/`. Document it in the README field table; show it in `site/app.js` (`showDetail`) if useful.
- **New prefecture/area/type:** edit `scope.toml` only. `prune --yes` removes data that leaves the scope.
  Everything downstream is keyed by prefecture: `scope.PREFS` (all 47, slug → name used in addresses) feeds
  geocoding and the site; the index lists each area with its prefecture, and the filter groups areas by
  prefecture once there is more than one. English area names are `places` in `site/i18n.js` (kanji is shown
  when a name is missing). Lines from other regions may need entries in `catalog._OPERATORS`
  if SUUMO writes them with and without the operator name.
- **New property type:** add it to `parse.TYPES` (path segment), check its search-result markup in `parse_list_page`,
  add its name to `types` in both languages in `site/i18n.js`.
- **New search condition:** a derived field on `catalog.Item` and a column in `site.build_index` if the JS needs one;
  the rule in `site/filter.js` (`emptyQuery`, `matches`); a control in `site/app.js` (`renderFilters`, URL key in
  `KEYS`, threshold values in `CHOICES`); its labels in `site/i18n.js`; a test in `tests/test_site.py`.
- **Line names:** `catalog.line_name` merges spellings (NFKC, section brackets, `_OPERATORS`, `_ALIASES`);
  check `Counter(line for item...)` after adding a region.
- **Tunables** are module constants: `pipeline.py` (misses, retention, suspect thresholds, priorities, progress,
  checkpoints, outage pause), `http.py` (slow-down), `archive.LIST_DAYS`, `catalog.NEW_DAYS`/`DROP_DAYS`,
  `cli.GEO_BUDGET`, and the choice lists at the top of `site/app.js`.

## Development

- `uv sync`, then `uv run pytest -q` (offline, ~2 s; the site tests need `node`) and `uv run ruff check suumo tests`
  (line length 120).
- Tests: `test_lifecycle.py` (reconcile/purge/export with synthetic records), `test_parsers.py` (value parsers),
  `test_pages.py` (real pages in `tests/fixtures/`), `test_ops.py` (prune, reparse, lock, git commit, slow-down,
  outage pause, progress, town names), `test_catalog.py` (derived fields), `test_site.py` (build output on real
  listings in `tests/fixtures/data/`, every search rule through filter.js). Shared helpers: `tests/helpers.py`
  (`rec`, `write`, `site_queries`, `QUIET`). Tests never touch the network.
- Look at the site locally: `uv run python -m suumo site && cd _site && python3 -m http.server`.
- Try changes on a copy: `--db`, `--archive`, `--data` point anywhere (`python -m suumo --db /tmp/s.db ... run`).
- Testing parser changes without requests: `reparse`, then `git diff -- data` shows exactly what changed.
- Scheduled runs can't read `~/Documents`, `~/Desktop`, `~/Downloads` (macOS privacy); the README installs to `~/suumo`.
- Never commit `.env`, `state.db*`, `archive/`, `logs/`, `_site/`, `uv.lock`.
