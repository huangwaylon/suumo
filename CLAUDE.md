# CLAUDE.md

Tracks SUUMO (suumo.jp) for-sale listings in all 47 prefectures (`scope.toml`): crawls search results twice a day
(05:07 and 15:07 JST, plus queue-only runs), fetches each listing's own page once, keeps lifecycle state in SQLite,
exports JSONL to `data/`, and publishes a static search site on GitHub Pages (https://huangwaylon.github.io/suumo/).
Everything runs on GitHub Actions: the crawl in 20 parallel shards (`crawl.yml`), the site build on every push
(`pages.yml`). No raw HTML is kept: each listing page becomes a page record (`extract.py`) stored in the shard's
state, and every field is derived from it. `data/<pref>/` is not in git: it is a snapshot asset on the release
`suumo-data`, named by the committed `data/snapshot.json`. The repo is public.

Where to look: `README.md` is the operator guide (how it runs, commands, record fields, troubleshooting);
`docs/decisions.md` holds the owner's decisions behind the rules below, with dates and reasons; each file's
header (module docstring or top comment) has its details. Keep those three in step with the code.

## Layout

| File | Role |
|---|---|
| `suumo/cli.py` | Entry point (`python -m suumo`); each command declares its access: `write` (state + lock), `read`, `files` |
| `suumo/pipeline.py` | `Pipeline`: `crawl_lists` → `reconcile` per area → `process_queue` → `purge`; all lifecycle rules and their constants |
| `suumo/parse.py` | `TYPES`, area pages, search-result pages, shared value parsers (prices, m², stations, town) |
| `suumo/extract.py` | Listing page HTML → page record (pure, `EXTRACT_VERSION`); templates kr, ms, mg |
| `suumo/detail.py` | Page record → normalized fields (`derive`, pure, `PARSE_VERSION`, `FIELDS`) |
| `suumo/db.py` | SQLite schema, additive `DB.migrate`, query helpers, `dumps` (canonical JSON), the one lock helper |
| `suumo/export.py` | DB → `data/<pref>/…` JSONL and page side files; `merge`, `FIELD_ORDER`, `dup_key`; `records`/`pages`/`prefectures`: the only readers of data/ |
| `suumo/snapshot.py` | `data/<pref>/` ⇄ one release asset: manifest, deterministic pack, checked unpack, the publish gate, the pointer |
| `suumo/maintenance.py` | `prune` (out-of-scope data), `seed` (rebuild a state from data/: last resort, no page records) |
| `suumo/migrate.py` | `migrate-state`: additive schema and one-time data fixes (marked in `meta`, e.g. `queue_v`) |
| `suumo/shards.py` | `shards.toml` → each worker's prefectures; `shard-split` / `shard-join` |
| `suumo/scope.py` | `scope.toml` → `Target`s; `PREFS` (slug → name), `PREF_BBOX`, `in_scope` |
| `suumo/http.py` | `Client`: one request at a time, delay + jitter, retries, automatic slow-down; gone → `FileNotFoundError` |
| `suumo/canary.py` | ~16 requests before the shards through the parsers; candidates from `data/canary/candidates.json` |
| `suumo/catalog.py` | data/ → `Item`s with the derived fields the site searches on; duplicate groups; 新着/値下げ/history from events |
| `suumo/site.py` | `build`: data/ + geo/ → `_site/` (manifest, per-prefecture columnar indexes, zstd listing buckets) |
| `suumo/geo.py`, `stations.py`, `rail.py`, `fx.py` | Town coordinates, station readings, railway lines (`geo/*.json`), the yen-dollar rate (`data/fx.json`) |
| `suumo/saved.py`, `saved.json` | The shared saved list (issue `save`/`unsave <type>:<id>` → saved.yml) |
| `suumo/util.py` | `write_atomic` (every file writer) |
| `site/filter.js` | The search rules (the only implementation) and the typed-array store; runs in the page and in Node tests |
| `site/app.js`, `index.html`, `style.css` | The page: map + panel, tabs, filters, favorites, gallery, history entries |
| `site/i18n.js` | Every user-facing string, Japanese and English |
| `site/sw.js` | Service worker: indexes stale-while-revalidate, code network-first, photos cache-first with a cap |
| `site/supercluster.min.js`, `fzstd.min.js` | Vendored: map clustering (ISC), zstd decoding (MIT) |
| `.github/workflows/crawl.yml` | plan → canary → shards (≤ 8 at once) → combine (snapshot in, shards over it, gate, snapshot out, commit, push) |
| `.github/workflows/pages.yml` | On push: lint, tests, snapshot unpack, build, size check, deploy |
| `.github/workflows/watch.yml` | The watchdog (every 3 h): stale data, failed runs, disabled crawl → one tracking issue |
| `.github/workflows/saved.yml` | Applies save/unsave issues to `saved.json` and rebuilds |
| `.github/scripts/ci.py`, `state.sh`, `data.sh` | crawl.yml's decisions (tested), the shard states on `suumo-state`, the data snapshot on `suumo-data` |
| `docs/decisions.md` | The owner's decisions, dated, with reasons |

Imports flow one way (no cycles): `cli` → everything; `maintenance`/`migrate` → `pipeline` → `export`/`detail`;
`site` → `stations` → `geo`, with `site` and `stations` also using `catalog`, and `site` using `rail`; `snapshot` →
`export`; `canary` → `extract`/`detail`. The bottom layer (`parse`, `scope`, `db`, `util`, `http`, `extract`) imports
nothing of ours but `parse`. `parse`, `extract`, `detail` and `catalog` are pure (`catalog` only reads files).
`catalog`, `geo`, `stations`, `site`, `maintenance` and `snapshot` read data/ only through `export`.

## Data model

- `listings` (PK `type, id`): `list_json` (search-result fields, refreshed every crawl), `detail_json` (listing-page
  fields: only keys in `detail.FIELDS`), `page_json` + `extract_v` + `fetched_at` (the page record), `status`
  active/removed, `first_seen`, `last_seen`, `missed`, `removed_at`. JSON stored with `db.dumps` (sorted keys).
- `areas` (PK `pref, type, code`): `baselined`, `last_hits`, `last_status` (complete / incomplete / suspect /
  error), `missed_on` (the crawl day whose misses were counted).
- `queue` (PK `type, id`): `priority` (new 2, changed 1, backfill 0), `attempts`, `last_error` (`extract:` = the
  extractor failed, `gone: <status> [<Location>]` = the page is gone: both set to `MAX_DETAIL_ATTEMPTS`, so never
  fetched again). A `NetworkError` uses no attempt but joins the outage streak.
- `prefs` (PK `pref`): `crawled_at`, once all its targets are done (after the one retry `crawl_lists` gives a failed
  target). Two targets in a row failing because SUUMO or the network is down (`pipeline.DOWN`) stop the crawl
  without marking prefectures crawled; `run` then skips listing pages and exits 3 (`cli.OFFLINE_EXIT`; 1 is a
  crash). `run --crawl-at 5,15` crawls while any prefecture wasn't crawled since the latest of those hours, so a
  stopped crawl resumes with the next prefecture.
- `ended` (PK `type, id`): tombstones of deleted listings, kept `TOMBSTONE_DAYS` (14).
- `events`: new / price_changed / removed / relisted, `payload` `{price, old_price?}`; kept 90 days in the DB and
  for good in `data/events/<run_id>.json` (git), from which the site's 新着, 値下げ and price history come.
- `runs`: one row per run, a JSON report and `progress`. Ctrl-C/SIGTERM roll back the open transaction (never half
  an area's reconcile) and exit 130.
- `meta`: state-wide markers (`backfilled`, `parse_v`, `queue_v`).
- Schema changes are additive and idempotent (`db.TABLES` / `db.COLUMNS`, `DB.migrate()`); one-time data fixes go in
  `migrate-state`. Only writing commands migrate, after taking the lock: old code must keep working on the result.
  Unused columns stay (`queue.reason`, and in older states `events.posted`, `listings.detail_fetched/info_date/
  next_update`, `areas.last_crawled`).
- IDs are SUUMO `nc_` numbers, one per agent listing. `dup_key` (type + building/address + size + price) groups one
  property's listings. Only the group's lead gets its page fetched (`Pipeline._leads`, among the listings seen this
  crawl): the one whose page is stored; else the one already queued and not given up on (it keeps its place in line
  when SUUMO reorders the group, which it does daily for ~20% of groups); else the first on the page. When the lead
  goes missing, ends or is gone, a copy is fetched. (docs/decisions.md 2026-10-15.)
- **A `dup_key` group in which a known `agent` appears twice is not one property, nor is a condo group whose fetched
  listings have two different known `floor`s.** Their listings stand alone: the crawler applies the agent half when
  queueing, the site applies both when folding (`catalog.Snapshot`).

## Invariants (keep these true)

- **Only robots.txt-allowed paths.** Search results `/{TYPES[type]}/{pref}/{slug}/?pc=100&page=N`, area pages
  `/{type path}/{pref}/city/`, listing pages the `path` from the search result; never `/jj/bukken/ichiran/...` or a
  `sort=` parameter. Redirects aren't followed.
- **Polite.** One request at a time per crawler, `--delay` ≥ 0.3 s (`cli.MIN_DELAY`) + up to 50% jitter; 1.0 s once
  a run on record got a 429/403 (`cli.run_delay`); on Actions 1.0 s per worker, at most 8 workers on disjoint
  prefectures. `http.Client` slows itself down on 429/5xx/network errors/slow responses (doubling, max 10 s, honours
  `Retry-After`) and eases back. Geocoding is one request at a time too. Never a second crawler on the same states.
  (docs/decisions.md 2026-10-14…17, 2026-11-15.)
- **Removal needs evidence.** A listing is removed only after `REMOVE_AFTER_MISSES` (2) consecutive *complete* crawls
  of its area miss it, on different crawl days (a day starts at 04:00, `pipeline.DAY_STARTS`; `areas.missed_on`), so
  a restarted crawl can't remove anything. Then it's deleted (tombstone in `ended`), or kept as `removed` if it's in
  `saved.json`. Incomplete/error areas never count; a `suspect` crawl (lost >30% of ≥20) counts nothing and uses up
  the day, and its count becomes the reference. A search page without SUUMO's hit count is an error, never
  "complete with 0". An area page with no areas, or whose areas' counts add up to less than (1 - `SUSPECT_DROP`) of
  the ≥`SUSPECT_MIN_HITS` listings held, is an error (`AreaPageError`) before anything is reconciled. An area that
  vanishes from the area page, or shows "(0)" without a link, is reconciled as complete with 0 hits (same guards);
  so is a new_condo area another type knows but the new_condo page doesn't list.
- **Returns are relisted.** A deleted listing seen again within its tombstone's 14 days gets back its `first_seen`,
  a `relisted` event (plus `price_changed` if the price differs) and its page queued again. An id held under another
  type (SUUMO re-types listings; some land is listed under both new_house and land) keeps that row's `first_seen`,
  gets no `new` event unless the other type's got one this run, and is queued at backfill (`retyped`).
- **Baselines are silent.** An area's first complete crawl creates no events; its listings queue as backfill.
- **Extract, keep no HTML.** A fetched page becomes a page record stored with its fields in one transaction
  (`pipeline.store_page`, the one write path); a good record is never nulled by a failure. An extract error gives
  the listing up (`refetch --errors` after a fix); 10 in a row stop the queue without using attempts (a layout
  change); extract or derive errors flag the run and turn the shard red. A derive fix is a `PARSE_VERSION` bump:
  every record is re-derived at the next run's start, no requests. An extract fix (`EXTRACT_VERSION`) only reaches
  pages fetched again. Raw HTML only in the 7-day `debug-<n>` artifact (every extract failure + a 0.5% sample).
- **Refetches are bounded.** Queue priorities new 2, changed 1 (a relisted lead, or a price change on a lead whose
  page is >14 days old), backfill 0; after the queue, up to `REFRESH_PER_RUN` (20) refresh pages per shard and run.
- **Deterministic export and build.** data/ files are sorted by id in `FIELD_ORDER` and hold nothing that changes on
  its own (`last_seen`, `missed` stay in the DB); files are replaced atomically. An unchanged run changes no group in
  `data/snapshot.json` (so no new snapshot) and no file in git, except pages the run itself refetched. Check this
  after any export change. The site build is deterministic too (sorted buckets, pinned zstd version and level).
- **The snapshot fails closed.** Combine never lays the shards over a data/ it couldn't unpack and check, and
  publishes only through the gate (every prefecture present, none of ≥200 records down by more than half; `force`
  overrides). A snapshot asset is named once and never replaced; the committed pointer says which one is current.
- **One writer.** Writing commands hold `state.db.lock`. On Actions, one writer per shard state (the workflow's
  concurrency group); never run a state locally while crawl.yml is enabled (two states diverge: duplicate events).
- **One implementation of the search rules: `site/filter.js`.** Python only derives fields (`catalog.Item`,
  `site.build_index`) so the JS compares plain values; flag bits travel in the index (`site.FLAGS`). The rule tests
  in `tests/test_site.py` run queries through filter.js in Node.
- **Rules the site shows:**
  - Duplicates (`dup_key`) fold at build time into the listing with its page fetched and most tags; the others
    become `others` links. Land listed under both new_house and land is kept once, as land.
  - Stations are identified by name, one spelling per prefecture (`catalog.unify_station_names`: 荻窪駅 → 荻窪).
  - A building condition (間取り, 広さ, 築年数, 新耐震) excludes land unless 土地 was chosen explicitly.
  - 周辺環境: each chosen kind within N minutes on foot (default 10) by the page's nearby facilities; a listing whose
    page lists none of that kind doesn't pass.
  - A listing's own map point is used only within 3 km of its town's point.
  - 新着 = a new/relisted event within 7 days (one within 14 days of its removal is a return: `catalog.RETURN_DAYS`).
    値下げ = a drop within 30 days, measured from the price before a rise taken back within 14 days
    (`catalog.SPIKE_DAYS`), never to under 20% of the old price (`DROP_MIN_RATIO`).
  - 南向き = direction 南/南東/南西 or a south tag; 最上階 = floor == floors above ground (or the tag); 2階以上 and
    タワー (20+ floors) from the floor fields; 仲介手数料なし = deal type 売主/事業主/代理 without a broker, when any
    agent of the property has it; 即入居 = handover 即… or the tag; 駐車場 = houses/land with a car space or a 駐車
    tag (condos never); 管理費等 = management + repair fund (+ other monthly costs) of condos, unknown unless both
    are stated (houses and land pass a fees limit).
  - 坪単価順 puts houses last unless only houses are chosen; 広い順 puts land last unless only land is chosen.
  - Tags a condition covers (`site.HIDDEN_TAGS`) aren't offered as tags; `Filter.upgrade` turns them into the
    conditions in old links.
- **Public repo:** nothing secret in git (no `.env`, no tokens); commits use the GitHub noreply address
  (`git config user.email` is set in this repo). `uv.lock` isn't committed (it would pin the local package mirror).
- **User-facing text lives in `site/i18n.js`**, Japanese (default) and English; never hard-coded in app.js or
  index.html (static text uses `data-t`, `data-t-label`, `data-t-placeholder`). Theme is `data-theme` on <html>, set
  before the first paint by the inline script in index.html. Code, logs and docs are English.

## SUUMO quirks the parsers rely on

- Area-page counts can contain commas: `(2,253)`. A count parsed as 0 would silently skip a ward.
- The `new_condo` area page lists only areas that currently have developments, in a different markup
  (`label > a` + `<span>(N)</span>`) from the other types (`js-linkSc###` ids, `searchitem-list-value`).
- `new_house` search results include land listings sold with a build condition; their `path` is under `/tochi/`
  (so `url` is stored, not derived) and their price excludes the building.
- Hokkaido's slug is `hokkaido_` (with the underscore); the others are plain romaji (`gumma` for 群馬).
- An ended listing's page redirects to its building's `/library/` page (200), so a redirect counts as gone.
- `-` means "not stated" everywhere; `clean()` drops it. Prices: `8702万8000円`, ranges `A～B` or `A・B`, footnotes
  after `※`.
- Search results show one station; listing pages up to 3, sometimes a bus stop (`line` containing バス →
  `bus_stop: true`). Often only the bus company or a route number is given (`しずてつジャストライン「丸子橋入口」`): the
  site counts a name as a station only when rail.json has one of that name nearby (`catalog.is_bus`); the rest are
  bus stops (バス停), not offered as stations.
- Listing-page labels end in `ヒント` and the summary and full spec tables repeat labels; `spec_table` strips the
  suffix and keeps the first value. Deal type is `取引態様：＜…＞` in the agent block.
- `land_rights` text can be a paragraph; `_tenure` maps it to a category and keeps the text in `land_rights_note`.
- Image URLs are SUUMO resize URLs (`resizeImage?src=…&w=&h=`); SUUMO pads small originals instead of enlarging
  them, so larger sizes make some photos smaller.
- Addresses mostly stop at the 丁目 (full-width digit); some add 番地 in ASCII digits, which `geo.town_of` drops.

## Extending

- **New field from listing pages:** if the page record holds it, derive it in `detail.derive` and bump
  `PARSE_VERSION` (every state re-derives, no requests); if not, extend `extract` and bump `EXTRACT_VERSION` (new
  pages get it; the slow refresh upgrades the rest). Add it to `detail.FIELDS` and `export.FIELD_ORDER`, a fixture
  test (`tests/test_extract.py`), the README field table, and `site/app.js` if useful. Search-result fields:
  `parse._standard_unit`.
- **New prefecture/area/type:** edit `scope.toml` (and `shards.toml` for a new prefecture: a test checks it covers
  the scope); combine drops prefectures no shard owns. English area names are `places` in `site/i18n.js`.
- **New property type:** `parse.TYPES` (path segment), its markup in `parse_list_page`, its name in `site/i18n.js`.
- **New search condition:** a derived field on `catalog.Item` and a column in `site.build_index` (a sparse one in
  `site.SPARSE`), read in `Filter.load` (and `item` if shown); the rule in `site/filter.js` (`emptyQuery`,
  `CHECKS`/`BUILDING`; a flag also in `FLAGS`/`FLAG_BITS`, a threshold in `THRESHOLDS`; words in `WORDS` or `parse`);
  a control in `site/app.js` (`renderFilters`, `KEYS`, `CHOICES`); labels in `site/i18n.js`; a test in
  `tests/test_site.py` (and `NL_CASES` for its words).
- **Tunables** are module constants: `pipeline.py` (misses, retention, suspect thresholds, priorities, refresh),
  `http.py` (slow-down), `catalog.NEW_DAYS`/`DROP_DAYS`, `snapshot.DROP`/`MIN_RECORDS`, `data.sh` `KEEP`,
  `cli.GEO_BUDGET`, and the choice lists at the top of `site/app.js`.

## Development

- `uv sync`, then `uv run pytest -q` (offline, ~15 s; the site tests need `node`) and `uv run ruff check suumo tests`
  (line length 120). Tests never touch the network; each `tests/test_*.py` starts with what it covers; shared helpers
  in `tests/helpers.py`; real pages and records in `tests/fixtures/`.
- The real data/: `GH_REPO=huangwaylon/suumo .github/scripts/data.sh get data` (the snapshot the pointer names).
- Look at the site locally: `uv run python -m suumo site && cd _site && python3 -m http.server`.
- Try changes on a copy: `--db`, `--data`, `--debug-dir` point anywhere. Derive changes: bump `PARSE_VERSION`, run
  on a downloaded state copy (re-derive at start) into a data/ from the snapshot, compare `snapshot.manifest`.
  Extract changes: `python -m suumo extract FILE` on fixtures or a `debug-<n>` sample.
- `suumo/__main__.py` runs `main()` only as `__main__` (the site build's worker processes import it).
- Never commit `.env`, `state*.db*`, `logs/`, `_site/`, `uv.lock`.
