# CLAUDE.md

Tracks SUUMO (suumo.jp) for-sale listings for the areas in `scope.toml` (all 47 prefectures): crawls search results
hourly, fetches each listing's own page once, keeps lifecycle state in SQLite, exports git-tracked JSONL to
`data/`, and publishes a static search site on GitHub Pages (https://huangwaylon.github.io/suumo/), rebuilt by
GitHub Actions on every push. The crawl runs locally on a Mac under launchd. `README.md` is the operator guide
(setup, commands, record fields, the site). The repo is public.

## Layout

| File | Role |
|---|---|
| `suumo/cli.py` | Entry point (`python -m suumo`). Each command declares its access: `write` (state.db + run lock), `read` (`status`), `files` (`geocode`, `site`, `rail`, `saved`: files in the repo only) |
| `suumo/pipeline.py` | `Pipeline`: `crawl_lists` → `reconcile` per area → `process_queue` → `purge`. All lifecycle rules and constants live here; progress is saved to the run's report for `status` |
| `suumo/parse.py` | `TYPES`, area-selection pages (`parse_areas`), search-result pages (`parse_list_page`), shared value parsers (prices, m², stations, town) |
| `suumo/detail.py` | Listing pages: spec table → normalized fields (`parse_detail`); `FIELDS`, every key it can return |
| `suumo/db.py` | SQLite schema and `DB.migrate` (additive changes), small query helpers, `dumps` (canonical JSON), `flock`/`exclusive`/`locked` (the one lock helper) |
| `suumo/export.py` | DB → `data/<pref>/<type>/<area>.jsonl` (one file per type and area, rewritten only on change); `merge` (list + detail fields), `FIELD_ORDER`, `dup_key` |
| `suumo/maintenance.py` | `prune` (out-of-scope data), `reparse` (rebuild parsed fields from `archive/`), `seed` (rebuild `state.db` from `data/`), `strip_list_fields` (the one-time fix of rows seeded by older code) |
| `suumo/archive.py` | Raw HTML store: daily search-result snapshots, latest copy of each listing page; `write_atomic` (used by all file writers) |
| `suumo/scope.py` | `scope.toml` → `Target(pref, type, areas)` list; `by_key` (a dict by pref and type), `in_scope` |
| `suumo/http.py` | `Client`: one request at a time, delay + jitter, retries, automatic slow-down; 404/410 → `FileNotFoundError` |
| `suumo/gitdata.py` | `commit_data`: commit `data/` and `geo/` only, optionally push |
| `suumo/catalog.py` | `data/` → `Snapshot` of `Item`s with the derived fields the site searches on (rooms, sizes, age, walk times, flags: south/top/upper/nofee/ready/tower/parking, condos' monthly `fees`), one station spelling per prefecture, duplicate groups, 新着/値下げ and price history from `data/events/` |
| `suumo/fx.py` | The day's yen-dollar rate (ECB via frankfurter.dev, no key) into `data/fx.json`, checked once a day by `run` (an unchanged rate leaves the file alone); the site shows ≈ USD next to yen prices |
| `suumo/geo.py` | Town (丁目) coordinates from MLIT's 位置参照情報 (one file per prefecture, `PLACES_URL`; a new version each spring), the rest from 国土地理院's address search; cached in `geo/towns.json` |
| `suumo/rail.py` | Railway lines and stations for the maps, the whole country: MLIT 国土数値情報 鉄道データ (N02, CC BY 4.0), simplified; colours and English names from ODPT's open, basic (`ODPT_TOKEN`) and challenge (`ODPT_CHALLENGE_TOKEN`, challenge licence) APIs, else carried over from the previous file; `rail` writes `geo/rail.json`. Also decides which same-named stations are really two (`site.station_prefectures`) |
| `suumo/stations.py` | Station names in kana and English from Wikidata (one query per prefecture), cached in `geo/stations.json`, per prefecture over the area its listings' stations span (`catalog.station_area`, places from rail.json); a name several prefectures return keeps the reading of the one whose listings mention it most (`stations.merge`), never overwritten with null; the site searches and shows them. `site.listing_spelling` renames rail stations to the listings' spelling (阿佐ケ谷, not 阿佐ヶ谷) |
| `suumo/site.py` | `build`: `data/` + `geo/` → `_site/` (static assets from `site/`, a manifest `data/index.json` with `prefs` entries `[slug, name, count, share with the listing page fetched]`, one search index per prefecture `data/p/<pref>.json` loaded as chosen (flag bits `FLAGS`, `fees` in ¥100), one JSON per listing (`others` with `deal_type`, no-commission agents first)) |
| `site/filter.js` | The search rules, the only implementation: load the columnar index, `CHECKS`/`BUILDING` (one check per condition), `search`, `count`, `counts` (q and variants `{name: patch}` in one pass), `facets` (every choice count in one pass: thresholds bucketed by binary search, tags/areas/stations in typed arrays per prefecture), text search by matching each prefecture's small tables (names, towns, areas, layouts, stations, bus stops) instead of per-item text; `suggest` (candidates for the word being typed), `parse` + `applyParse` (search-box text → conditions; dictionaries per prefecture, built on first use). Must work with an index older than itself (the service worker may serve one after a deploy): read new columns with `?.`, missing flags are 0 |
| `site/i18n.js` | Every user-facing string, Japanese (default) and English; `t(key, ...args)` in app.js. A test requires every Japanese key to have an English one |
| `site/app.js`, `index.html`, `style.css` | The page, map-first: the map plus a panel listing what's in its view (`m=lat,lng,zoom` in the URL, replaced on every move; the map moves only on the refit triggers), the panel's tabs (the list, opened listings, 比較 of the open tabs; `id=`, favorites' `cmp=1`), one filter panel (rail on desktop, sheet elsewhere), favorites (the shared saved list from the index's `saved` flag, plus this browser's pending requests), last search (localStorage `suumo.last`). Opening a listing, the favorites view and the overlays are history entries, so back closes them. An app shell (the page never scrolls): ≥1024 map, divider (`settings.panel`), panel; below, a bottom sheet over the map (peek/half/full, `settings.sheet`); ≥1280 the filter rail. Card columns follow the panel's width (container queries) |
| `.github/workflows/pages.yml` | On push: lint, tests (incl. Node), build, deploy to Pages (actions pinned by SHA) |
| `local.suumo.plist` | launchd template (hourly at :05, `run --crawl-at 4 --budget 3h --until-minute 50 --push`) |
| `~/suumo-refresh/all.sh` (not in git) | The one-off driver for the nationwide sweep and backfill (nohup + caffeinate): sweep, geocode, then 1 h listing-page runs with the stepped delay, the 04:00 crawl at 1.5 s. Replace with `local.suumo.plist` once the backfill is done |
| `suumo/saved.py`, `saved.json`, `.github/workflows/saved.yml` | The shared saved list: issue `save <type>:<id>` / `unsave ...` → workflow → `saved.json` → rebuild. Runs keep saved listings after they end |

Imports flow one way: `cli` → `pipeline`/`maintenance`/`export`/`gitdata`/`geo`/`site` →
`detail`/`parse`/`db`/`archive`/`scope`/`http`/`catalog`. `parse`, `detail` and `catalog` are pure.

## Data model

- `listings` (PK `type, id`): `list_json` (search-result fields, refreshed every crawl), `detail_json` (listing-page
  fields: only keys in `detail.FIELDS`), `status` active/removed, `first_seen`, `last_seen`, `missed`, `removed_at`.
- `areas` (PK `pref, type, code`): `slug`, `baselined`, `last_hits` (the latest count), `last_status` (complete /
  incomplete / suspect / error), `missed_on` (the crawl day whose misses were counted).
- `queue` (PK `type, id`): `priority` 1 new / 0 backfill, `attempts`, `last_error` (`parse:` prefix = parser failure,
  `gone: 404` = the page is gone; both are set to `MAX_DETAIL_ATTEMPTS`, so they aren't fetched again and `status`'s
  `queue:` count leaves them out).
- `prefs` (PK `pref`): `crawled_at`, written for each prefecture as soon as its search results are done. `run
  --crawl-at H` crawls while any prefecture in scope wasn't crawled since H:00 (MIN, not MAX) and skips those that
  were, so a daily crawl that stopped resumes with the next prefecture (`cli.crawl_due`, `crawled_since`).
- `ended` (PK `type, id`): tombstones of deleted listings (`pref`, `first_seen`, `price`, `ended_at`), written by
  `reconcile` when it deletes one, dropped by `purge` after `TOMBSTONE_DAYS` (14).
- `events`: `kind` new / price_changed / removed / relisted, `payload` `{"price"}` (+ `"old_price"` for price
  changes), exported per run to `data/events/<run_id>.json`; the site's 新着, 値下げ and price history come from them.
  (Older databases also carry unused columns: `events.posted`, `listings.detail_fetched/info_date/next_update`,
  `areas.last_crawled`.)
- `runs`: one row per run with a JSON report (`progress` while running).
- Schema changes are additive and idempotent: `db.TABLES` / `db.COLUMNS`, applied by `DB.migrate()`, and one-time
  data fixes under `PRAGMA user_version` (`maintenance.strip_list_fields`). Only writing commands run them, after
  taking the lock (`status` opens the DB with `migrate=False`): old code must keep working on the result.
- IDs are SUUMO `nc_` numbers, per agent listing. The same property listed by several agents has several IDs;
  `dup_key` (type + building/address + size + price) groups them. Only one listing per group gets its page fetched,
  decided in `Pipeline.reconcile` only: a listing seen in this crawl whose page is stored (or queued) claims the
  group, so when it goes missing or ends, a copy is queued and fetched. `_queue_order` only orders the queue (new
  first, then the prefecture with the most listings; a land twin after its queued twin).
- **A `dup_key` group in which a known `agent` appears twice is not one property** (an agent doesn't list one
  property twice), **nor is a condo group whose fetched listings have two different known `floor`s.** Their
  listings stand alone. The crawler applies the agent half when queueing (every listing of such a group gets its
  page; floors are only known after fetching); the site applies both when folding (`catalog.Snapshot`).
- `list_json`/`detail_json` are stored with `db.dumps` (sorted keys) so equal data is equal text.

## Invariants (keep these true)

- **Only robots.txt-allowed paths.** Search results: `/{TYPES[type]}/{pref}/{slug}/?pc=100&page=N`; area pages:
  `/{type path}/{pref}/city/`; listing pages: the `path` from the search result. `/jj/bukken/ichiran/...` and any
  `sort=` parameter are disallowed (robots.txt has no crawl-delay for `*`; its `Crawl-delay: 30` is bingbot's).
  Redirects aren't followed. One request at a time per crawler, `--delay` 1.5 s + up to 50% jitter for search
  results. Listing pages during a backfill (or a one-off sweep of new prefectures) may go down to 0.5 s, by the
  owner's decision (2026-10-14): the driver lowers it 0.1 s per clean run and goes back to 1.0 s for good on
  throttling (any 429/403 — `Client.pushbacks` in the run report — or 3+ slow-downs in a run); 2 slow-downs
  pause the step-down, a single stray 5xx is ignored. Never lower than 0.5 s. At most two crawlers at once, by the owner's decision: the
  hourly one and a one-off with its own `--db`/`--archive`/`--scope` and `--no-geocode` (scope-ext.toml).
  `http.Client` slows itself down on 429/5xx/network errors/slow responses (doubling, max 10 s, honours
  `Retry-After`) and eases back; keep that behaviour.
  Geocoding is also one request at a time (`geo.GEO_DELAY`).
- **Removal needs evidence.** A listing is removed only after `REMOVE_AFTER_MISSES` (2) consecutive *complete*
  crawls of its area miss it, on different crawl days: misses count once per area per crawl day (it starts at 04:00,
  `pipeline.DAY_STARTS`; `areas.missed_on`), so a restarted crawl can't remove anything. Then it's deleted (leaving a
  tombstone in `ended`), or kept as `removed` (ended) if it's in `saved.json`. Incomplete/error areas never count;
  a `suspect` crawl (lost >30% of ≥20) counts nothing and uses up the day, and its count becomes the reference,
  so a real drop is distrusted for one crawl day only. A search-result page without SUUMO's hit count is an error,
  never "complete with 0". An area that vanishes from the area page, or is listed as "(0)" without a link while
  listings are held there, is reconciled as complete with 0 hits (same guards apply).
- **Returns are relisted.** A deleted listing seen again while its tombstone lasts (14 days) gets back its
  `first_seen`, a `relisted` event (plus `price_changed` if the price differs) and its page queued again; later
  it's `new`.
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
  listed under both `new_house` and `land` is kept once, as land. Stations are identified by name, one spelling per prefecture (`catalog.unify_station_names`: 荻窪駅 → 荻窪). A building
  condition (間取り, 広さ, 築年数, 新耐震) excludes land unless 土地 was chosen explicitly. 新着 = a new/relisted
  event within 7 days, 値下げ = a drop within 30 days. 南向き = direction 南/南東/南西 or a south tag; 最上階 = floor ==
  floors above ground (or the tag); 2階以上, タワー (20+ floors) from the floor fields; 仲介手数料なし = deal type
  売主/事業主/代理 without a broker, set when any agent of the property has it; 即入居 = handover 即… or the tag;
  駐車場 = houses/land with a car space or a 駐車 tag (condos never); 管理費等 = management + repair fund (+ other
  monthly costs) of condos, unknown unless both are stated (houses and land pass a fees limit). 坪単価順 puts houses
  last unless only houses are chosen; 広い順 puts land last unless only land is chosen. Tags a condition covers
  (`site.HIDDEN_TAGS`: south tags, 最上階・上階なし, 即引渡(し)可, 建築条件なし, 土地50/100坪以上) aren't offered as tags;
  `Filter.upgrade` turns them into the conditions in old links.
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
- Hokkaido's slug is `hokkaido_` (with the underscore); the others are plain romaji (`gumma` for 群馬).
- Redirects aren't followed: an ended listing's page redirects to its building's `/library/` page (200), so a
  redirect counts as gone.
- `-` means "not stated" everywhere; `clean()` drops it. Prices: `8702万8000円`, ranges `A～B` or `A・B`, footnotes after `※`.
- Search results show one station; listing pages show up to 3 (each in its own `<div>`), sometimes including a bus
  stop (`line` containing バス → `bus_stop: true`). Often SUUMO gives only the bus company or a route number
  (`しずてつジャストライン「丸子橋入口」`): the site build counts a name as a station only when rail.json has one of that
  name in the prefecture's area (`catalog.is_bus`, `railway_near`); the rest are bus stops, shown as バス停 and not
  offered as stations.
- Listing-page labels end in `ヒント` (a help link) and the summary and full spec tables repeat labels; `spec_table`
  strips the suffix and keeps the first value. Deal type is `取引態様：＜…＞` in the agent block.
- `land_rights` text can be a paragraph; `_tenure` maps it to a category and keeps the text in `land_rights_note`.
- Image URLs are SUUMO resize URLs (`resizeImage?src=…&w=&h=`); any size works, the site asks for 360×270 (cards), 640×480 (listing) and 120×90 (map popups). SUUMO pads small originals instead of enlarging them, so larger sizes make some photos smaller.
- Addresses mostly stop at the 丁目 (full-width digit); some add 番地 in ASCII digits, which `geo.town_of` drops.

## Extending

- **New field from listing pages:** parse it in `detail.parse_detail` (or `parse._standard_unit` for search results),
  add it to `detail.FIELDS` (listing-page fields; a test checks every fixture) and `export.FIELD_ORDER`, add a test
  against a fixture, then `reparse`. No re-crawl needed while the pages
  are in `archive/`. Document it in the README field table; show it in `site/app.js` (`detailHtml`) if useful.
- **New prefecture/area/type:** edit `scope.toml` only. `prune --yes` removes data that leaves the scope.
  Everything downstream is keyed by prefecture: `scope.PREFS` (all 47, slug → name used in addresses) feeds
  geocoding and the site; the index lists each area with its prefecture, and the filter groups areas by
  prefecture once there is more than one. English area names are `places` in `site/i18n.js` (kanji is shown
  when a name is missing).
- **New property type:** add it to `parse.TYPES` (path segment), check its search-result markup in `parse_list_page`,
  add its name to `types` in both languages in `site/i18n.js`.
- **New search condition:** a derived field on `catalog.Item` and a column in `site.build_index` if the JS needs one;
  the rule in `site/filter.js` (`emptyQuery`, `CHECKS`/`BUILDING`; a flag also in `FLAGS`/`FLAG_BITS`, a threshold in
  `THRESHOLDS` for facets; words for it in `WORDS` or a pattern in `parse`); a control in `site/app.js`
  (`renderFilters`, URL key in `KEYS`, threshold values in `CHOICES`); its labels in `site/i18n.js`; a test in
  `tests/test_site.py` (and a case in `NL_CASES` for its words).
- **Tunables** are module constants: `pipeline.py` (misses, retention, suspect thresholds, priorities, progress,
  checkpoints, outage pause), `http.py` (slow-down), `archive.LIST_DAYS`, `catalog.NEW_DAYS`/`DROP_DAYS`,
  `cli.GEO_BUDGET`, and the choice lists at the top of `site/app.js`.

## Development

- `uv sync`, then `uv run pytest -q` (offline, ~2 s; the site tests need `node`) and `uv run ruff check suumo tests`
  (line length 120).
- Tests: `test_lifecycle.py` (reconcile/purge/export with synthetic records: misses per crawl day, suspect,
  tombstones, one page per property, area-page cases, crawl_area), `test_parsers.py` (value parsers),
  `test_pages.py` (real pages in `tests/fixtures/`, `detail.FIELDS`), `test_ops.py` (prune, reparse, seed and the
  seeded-row fix, lock, git commit, the HTTP client, the queue's outage/gone/parse-failure paths, progress, the
  `status` line the driver reads, the resumable daily crawl, events export, town names), `test_rail.py` (railway
  lines), `test_stations.py` (station names), `test_catalog.py` (derived fields), `test_site.py` (build output on
  real listings in `tests/fixtures/data/`, every search rule through filter.js). Shared helpers: `tests/helpers.py`
  (`rec`, `write`, `site_queries`, `QUIET`). Tests never touch the network.
- Look at the site locally: `uv run python -m suumo site && cd _site && python3 -m http.server`.
- Try changes on a copy: `--db`, `--archive`, `--data` point anywhere (`python -m suumo --db /tmp/s.db ... run`).
- Testing parser changes without requests: `reparse`, then `git diff -- data` shows exactly what changed.
- Scheduled runs can't read `~/Documents`, `~/Desktop`, `~/Downloads` (macOS privacy); the README installs to `~/suumo`.
- Never commit `.env`, `state.db*`, `archive/`, `logs/`, `_site/`, `uv.lock`.
