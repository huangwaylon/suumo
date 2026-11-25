# CLAUDE.md

Tracks SUUMO (suumo.jp) for-sale listings for the areas in `scope.toml` (all 47 prefectures): crawls search results
twice a day (05:07 and 15:07 JST, plus two queue-only runs), fetches each listing's own page once, keeps lifecycle state in SQLite, exports JSONL to
`data/` (since 2026-11-25 `data/<pref>/` is a release asset, `suumo-data`, named by the committed `data/snapshot.json`;
git keeps the pointer, `data/events/`, `fx.json`, `canary/`: `suumo/snapshot.py`), and publishes a static search site on GitHub Pages (https://huangwaylon.github.io/suumo/), rebuilt by
GitHub Actions on every push. The crawl runs on GitHub Actions in parallel workers (`crawl.yml`, `shards.toml`; since
2026-11-15, no machine of ours; README "How it runs"). No raw HTML is kept: each listing page is extracted into a page
record (suumo/extract.py) stored in the crawl state, and every field is derived from it. `README.md` is the operator guide
(setup, commands, record fields, the site). The repo is public.

## Layout

| File | Role |
|---|---|
| `suumo/cli.py` | Entry point (`python -m suumo`). Each command declares its access: `write` (state.db + run lock), `read` (`status`), `files` (`geocode`, `site`, `rail`, `saved`: files in the repo only) |
| `suumo/pipeline.py` | `Pipeline`: `crawl_lists` → `reconcile` per area → `process_queue` → `purge`. All lifecycle rules and constants live here; progress is saved to `runs.progress` for `status` |
| `suumo/parse.py` | `TYPES`, area-selection pages (`parse_areas`), search-result pages (`parse_list_page`), shared value parsers (prices, m², stations, town) |
| `suumo/extract.py` | Listing page HTML → page record (`extract`, pure, `EXTRACT_VERSION`): every spec row as written, features, deal type, coordinates, dates, all photos (`img` {b, l}: page order, numbers + caption + comment; `img_src` rebuilds a path), free text (`texts`), nearby facilities (`env`), the agent block, unrecognised labels/sections (`unknown`). Templates kr (4 types and part of new_condo), ms (new_condo top pages), mg (rare new_condo outlines) |
| `suumo/detail.py` | Page record → normalized fields (`derive`, pure, `PARSE_VERSION`): what detail_json holds (`FIELDS`) plus lat, lng, photos. A version bump re-derives every stored record at the next run's start (no requests) |
| `suumo/db.py` | SQLite schema and `DB.migrate` (additive changes), small query helpers, `dumps` (canonical JSON), `flock`/`exclusive`/`locked` (the one lock helper) |
| `suumo/export.py` | DB → `data/<pref>/<type>/<area>.jsonl` (one file per type and area, rewritten only on change); `merge` (list + detail fields), `FIELD_ORDER`, `dup_key`; `records` (the one reader of `data/*.jsonl`, for catalog, geo, stations, seed); `pages` (the one reader of the page side files `data/<pref>/pages/`, for the site build) |
| `suumo/maintenance.py` | `prune` (out-of-scope data), `seed` (rebuild a state from `data/`: last resort, it can't restore page records, so every lead is queued) |
| `suumo/migrate.py` | `migrate-state`: additive schema (page columns, `meta`) and the one-time queue priority remap |
| `suumo/util.py` | `write_atomic` (every file writer) |
| `suumo/canary.py` | `canary`: ~16 requests before the shards (area, search and listing pages per type) through the parsers; `search_ok`/`list_ok`/`reachable` for crawl.yml; its listings from `data/canary/candidates.json` (`pick`, `canary --pick` in combine: the plan job checks out only that of data/), else data/tokyo's files |
| `suumo/snapshot.py`, `.github/scripts/data.sh` | `data/<pref>/` as one release asset: `manifest` (groups `<pref>/<child>`: files, records, sha256), `pack` (deterministic tar.zst, streamed), `unpack` (size + sha256 vs the pointer, only regular files under a prefecture, then the manifest vs the pointer's groups), `check` (the gate: every prefecture with records, none of ≥200 down by >half; `--force`), the pointer `data/snapshot.json`. `snapshot pack/unpack` CLI (exit 4 unchanged). data.sh `get` (pointer → download → unpack; test runs fall back to main's pointer), `put` (unique name, never replaced, checked whole), `prune` (the pointer's + newest 8: `ci.py prune`) |
| `suumo/scope.py` | `scope.toml` → `Target(pref, type, areas)` list; `by_key` (a dict by pref and type), `in_scope` |
| `suumo/http.py` | `Client`: one request at a time, delay + jitter, retries, automatic slow-down; 404/410/redirect → `FileNotFoundError` (message: status and Location); `NetworkError` when no attempt reached SUUMO (proxy, no connection, DNS), counted in `net_errors`, not `slowdowns` |
| `suumo/catalog.py` | `data/` → `Snapshot` of `Item`s with the derived fields the site searches on (rooms, sizes, age, walk times, flags: south/top/upper/nofee/ready/tower/parking, condos' monthly `fees`), `near_minutes` (周辺環境: minutes on foot to the closest of each `NEAR` kind, from a page record's `env`), one station spelling per prefecture, duplicate groups, 新着/値下げ and price history from `data/events/` |
| `suumo/fx.py` | The day's yen-dollar rate (ECB via frankfurter.dev, no key) into `data/fx.json`, checked once a day by `run` (an unchanged rate leaves the file alone); the site shows ≈ USD next to yen prices |
| `suumo/geo.py` | Town (丁目) coordinates from MLIT's 位置参照情報 (one file per prefecture, `PLACES_URL`; a new version each spring), the rest from 国土地理院's address search; cached in `geo/towns.json` (with `_bulk`: the towns that version's files don't place, so they aren't downloaded again while the address search works through them) |
| `suumo/rail.py` | Railway lines and stations for the maps, the whole country: MLIT 国土数値情報 鉄道データ (N02, CC BY 4.0), simplified; colours and English names from ODPT's open, basic (`ODPT_TOKEN`) and challenge (`ODPT_CHALLENGE_TOKEN`, challenge licence) APIs, else carried over from the previous file; `rail` writes `geo/rail.json`. Also decides which same-named stations are really two (`site.station_prefectures`) |
| `suumo/stations.py` | Station names in kana and English from Wikidata (one query per prefecture), cached in `geo/stations.json`, per prefecture over the area its listings' stations span (`catalog.station_area`, places from rail.json); a name several prefectures return keeps the reading of the one whose listings mention it most (`stations.merge`), never overwritten with null; the site searches and shows them. `site.listing_spelling` renames rail stations to the listings' spelling (阿佐ケ谷, not 阿佐ヶ谷) |
| `suumo/site.py` | `build`: `data/` + `geo/` → `_site/`, one prefecture in memory at a time (a first pass collects what they share: the station names they use, sizes, the saved list) (static assets from `site/`, a manifest `data/index.json` with `prefs` entries `[slug, name, count, share with the listing page fetched]` and `buckets`, one search index per prefecture `data/p/<pref>.json` loaded as chosen (flag bits `FLAGS`, `fees` in ¥100; `SPARSE` columns `dlat`/`dlng` (own point, 1e-4° from the town's), `photos`, `near` (B64 minutes per `catalog.NEAR` kind) as `{at: row deltas, v: values}`), listing records in `Buckets`: `data/l/<int(id) % 8192>.json.zst` = zstd (`ZSTD_LEVEL` 15, `zstandard` pinned) of `grouped(records)` = `{keys: [sorted "type:id"], field: [value per key]}`, through per-bucket temp files, compressed in a process pool (each bucket single-threaded: deterministic); each page side line parsed once (`export.pages` returns raw lines) (`listing_record`: no id/type/dup_key/has_detail/area fields, `url` as `pref/slug` (`_url`), `image` as `_image`, `others` with `deal_type` (no-commission agents first) and URLs without `https://suumo.jp`, plus the page side files' `img`, `texts`, `env` and `agent` as `office`, and `lat`/`lng` only from `own_point`: within `OWN_KM` (3) of the town's 丁目 point, else inside `pref_box`); rail.json rebuilt at build time: sections joined into paths, plus a coarse copy for zoom < 11 (`line[5]`; the page uses `line[5] ?? line[4]`)) |
| `site/filter.js` | The search rules, the only implementation, and the store: `load` turns a prefecture's columnar index into typed-array columns (the narrowest array holding each column exactly; stations one flat array with offsets, tags bit words; `opts.places`: English area names for text search) and drops the parsed JSON. Rows are ordered by place (type, area code, town, id), not id, for a smaller gzip; `find` binary-searches a (type, id) permutation (`keyed`) unless rows are in id order (older indexes). Town and building-name tables are in text order. Sparse columns (`{at, v}`) are expanded on load. Listing records: `bucketOf(key, n)`, `decode` (by magic bytes: zstd 28 b5 2f fd through the vendored `site/fzstd.min.js` (MIT), an older build's gzip 1f 8b through DecompressionStream, else JSON; the grouped layout back to `{key: record}`), `suumoUrl` (a record's `url` back to SUUMO's); photos: `photoPath` (the port of `extract.img_src`: a gallery entry → `gazo/bukken/…`), `photoUrl(path, w, h)` (resizeImage; 120×90 strip, 360×270 cards, 640×480 viewer), `gallery(id, img)` → `[{path, caption, comment}]`. A listing is a handle (load number × 2^20 + row), lists of them Int32Arrays (`search` results, `db.items`, map views); `item(h)` makes the plain object the page shows (bounded cache), `get.<field>(h)` reads one field, `find(db, key)`, `every`, `positions`, `places`, `release` (a replaced index). `CHECKS`/`BUILDING` (one check per condition, incl. `near`: 周辺環境, every chosen kind within `q.nearWalk` minutes, `NEAR_WALK` 10 by default; unknown fails; compiled per prefecture into a row test), `search`, `count`, `spatial` (a list's listings in a box, by a grid of positions: the map's view at every pan), `counts` (q and variants `{name: patch}` in one pass), `facets` (every choice count in one pass: thresholds bucketed by binary search, tags/areas/stations in typed arrays per prefecture), text search by matching each prefecture's small tables (names, towns, areas, layouts, stations, bus stops) instead of per-item text; `suggest` (candidates for the word being typed), `parse` + `applyParse` (search-box text → conditions; dictionaries per prefecture, built on first use). Must work with an index older than itself (the service worker may serve one after a deploy): a missing column reads as none, missing flags are 0. Exports `photo`, `FLAGS` and `TSUBO` for app.js |
| `site/i18n.js` | Every user-facing string, Japanese (default) and English; `t(key, ...args)` in app.js. A test requires every Japanese key to have an English one |
| `site/app.js`, `index.html`, `style.css` | The page, map-first: the map plus a panel listing what's in its view (`m=lat,lng,zoom` in the URL, replaced on every move; the map moves only on the refit triggers), the panel's tabs (the list, opened listings, 比較 of the open tabs; `id=`, favorites' `cmp=1`), one filter panel (rail on desktop, sheet elsewhere), favorites (the shared saved list from the index's `saved` flag, plus this browser's pending requests), last search (localStorage `suumo.last`). Listings are filter.js handles everywhere (results, map points, favorites, tabs by key); objects only for what's drawn (`Filter.item`). A listing's record comes from its bucket (`record`: `fetch(cache: "no-cache")`, the last 8 buckets and 100 records kept; no key or a 404 bucket → 掲載終了, other errors → 再読み込み). The listing view: the gallery (`galleryHtml`/`showPhoto`, opening on the card's photo; chips by caption, 間取り図 first; lazy 120×90 strip), the full-screen viewer (`openViewer`: a history entry, arrows/swipe/Esc), `textsHtml` (folded), `envHtml` (周辺環境 by kind, closest first), `officeHtml`, 「掲載情報 … 時点」 (`page_date`). Map: town clusters to zoom 15 (`CLUSTER_MAX`); from `OWN_ZOOM` (16) homes with an own point get their own pin (`.pin.own`), the town's pin keeps the rest. Opening a listing, the photo viewer, the favorites view and the overlays are history entries, so back closes them. Saved searches (localStorage `suumo.searches`) keep their prefectures (`p`); one of others is labelled with them and opens there. Prefectures switched away from are released past `maxPrefs` (phones 10), oldest first. An app shell (the page never scrolls): ≥1024 (or a phone on its side, ≤500 px high: `SPLIT`) map, divider (`settings.panel`), panel; below, a bottom sheet over the map (peek/half/full, `settings.sheet`); ≥1280 the filter rail. Card columns follow the panel's width (container queries) |
| `site/supercluster.min.js` | Vendored supercluster 8.0.1 (ISC): app.js clusters the results' towns once per result set, and only the clusters and towns in view are Leaflet markers |
| `site/sw.js` | Service worker: the indexes stale-while-revalidate, the page and scripts network-first, `/data/l/` bypassed; the indexes' revalidation writes Cache Storage only when ETag/Last-Modified changed; photos (img01.suumo.com) cache-first in `suumo-photos` (kept across versions; capped by count, `PHOTO_MAX` 100 pins included, because SUUMO sends no CORS headers and each opaque response counts as megabytes of quota; LRU order persisted in the cache (`__photo-index`); refetched after `PHOTO_DAYS`; any failure falls back to `fetch`), favorites' photos pinned in `suumo-pins` (≤ `PIN_MAX`; app.js `pinPhotos`/`reconcilePins`, what was pinned per listing in localStorage `suumo.pins`) |
| `site/fzstd.min.js` | Vendored fzstd 0.1.1 (MIT): decodes the zstd listing buckets |
| `.github/workflows/pages.yml` | On push: lint, tests (incl. Node), build, deploy to Pages (actions pinned by SHA) |
| `.github/workflows/crawl.yml`, `shards.toml`, `suumo/shards.py`, `.github/scripts/state.sh` | The crawl on GitHub Actions (GitHub cron every 30 min plus fixed slots, no external scheduler; a `plan` job keeps only shards with a crawl due (after 05:00/15:00 JST) or a non-empty queue, from the state assets' labels; a `canary` gates the shards; budget 10m after a crawl, 45m on 01:xx/10:xx runs, a dispatch ≤ 60m): one job per shard, 20, at most 8 at once (`max-parallel`: the account's 20 runners are shared, suumo 8 / kalori 10 / ITS runner 1, the owner's budget 2026-11-15) (`shards.toml`: each worker owns a fixed set of prefectures, balanced by search-result pages; a test checks it covers `scope.toml`), each with its own state (`state-<n>.db.zst` on the release `suumo-state`, `-test` off main; saved with `always()`, swapped in only once whole), running `run --shard N` (its prefectures only, run ids ending `-sNN`, every events file of its own re-exported so a lost push heals) into an empty data/; then one job unpacks the last snapshot (failing closed: never the shards over an empty data/), lays the shards' data/ over it (a prefecture in no shard removed), geocodes (`geocode --after-run`), `canary --pick`, packs and uploads the new snapshot (gate; unchanged → none), commits the pointer + events + geo, pushes, prunes old snapshots and dispatches pages.yml (which unpacks the snapshot its commit names). Dispatch inputs `force` (past the gate), `bootstrap` (from an empty data/: recovery, all shards). `shard-split` / `shard-join` turn one state.db into a file per shard and back (exact). Moving a prefecture between shards: join, edit shards.toml, split |
| `.github/workflows/watch.yml` | The watchdog, every 3 h on main: no `data: run` commit for 26 h, a failed crawl.yml/pages.yml run, crawl.yml disabled or not started for 12 h (dispatches one; reported past 24 h), any failed crawl run in the last 12 h → one issue `Crawl watchdog: attention needed` (commented only when the problems change, closed on recovery); re-enables/dispatches crawl.yml itself |
| `suumo/saved.py`, `saved.json`, `.github/workflows/saved.yml` | The shared saved list: issue `save <type>:<id>` / `unsave ...` → workflow → `saved.json` → rebuild. Runs keep saved listings after they end |

Imports flow one way (no cycles): `cli` → `maintenance` → `pipeline` → `export`/`detail`; `cli` → `site` →
`stations` → `geo`, with `site` and `stations` also using `catalog` and `site` using `rail` (`simplify`); `cli` →
`saved`/`rail`/`fx`/`snapshot` (`snapshot` uses `export.prefectures`). `catalog`, `geo`, `stations`, `site` and `maintenance` read `data/` through
`export.records`/`prefectures` (`export` itself uses only the bottom layer). All of them may use the bottom layer
`parse`/`scope`/`db`/`util`/`http`/`extract`. `parse`, `extract`, `detail` and `catalog` are pure (`catalog` only reads files).

## Data model

- `listings` (PK `type, id`): `list_json` (search-result fields, refreshed every crawl), `detail_json` (listing-page
  fields: only keys in `detail.FIELDS`), `status` active/removed, `first_seen`, `last_seen`, `missed`, `removed_at`.
- `areas` (PK `pref, type, code`): `slug`, `baselined`, `last_hits` (the latest count), `last_status` (complete /
  incomplete / suspect / error), `missed_on` (the crawl day whose misses were counted).
- `queue` (PK `type, id`): `priority` 1 new / 0 backfill, `attempts`, `last_error` (`parse:` prefix = parser failure,
  `gone: <status> [<Location>]` = the page is gone; both are set to `MAX_DETAIL_ATTEMPTS`, so they aren't fetched
  again and `status`'s `queue:` count leaves them out). A `NetworkError` (SUUMO never reached) uses no attempt but
  joins the outage streak.
- `prefs` (PK `pref`): `crawled_at`, written for each prefecture once all its targets are done, including the one
  retry `crawl_lists` gives a failed target (or its failed areas) at the end, success or not. Two targets in a row
  failing because SUUMO or the network is down (`pipeline.DOWN`: a `NetworkError`, or `http.Unavailable` when every
  attempt got a 5xx, e.g. SUUMO's maintenance page, 2026-10-22) stop the crawl (`report.offline`) without marking
  prefectures crawled; `run` then skips listing pages and exits 3 (`cli.OFFLINE_EXIT`; 1 is a crash). `run
  --crawl-at 5,15` crawls while any prefecture in scope wasn't crawled since the latest of those hours (MIN, not
  MAX; `cli.crawl_slot`) and skips those that were, so a crawl that stopped resumes with the next prefecture
  (`cli.crawl_due`, `crawled_since`). Two crawls a day, at 05:00 and 15:00, by the owner's decision
  (2026-10-29): SUUMO publishes in two batches, ~04:00 (most price changes, removals; sometimes still landing at
  04:10) and ~14:30 (15 minutes, counts dip while it lands); nothing changes in between (a 12:00 crawl found nothing
  on 5 of 6 days, 15-minute probes saw no change from 14:46 to 17:48).
- `ended` (PK `type, id`): tombstones of deleted listings (`pref`, `first_seen`, `price`, `ended_at`), written by
  `reconcile` when it deletes one and by `purge` when it deletes an ended listing no longer saved (`ended_at` =
  its `removed_at`), dropped by `purge` after `TOMBSTONE_DAYS` (14).
- `events`: `kind` new / price_changed / removed / relisted, `payload` `{"price"}` (+ `"old_price"` for price
  changes), exported per run to `data/events/<run_id>.json` (a run that didn't finish: by the next run,
  `export.export_interrupted`); the site's 新着, 値下げ and price history come from them. Index `events_run`.
  (Unused columns: `queue.reason` (written as ''), and in older databases `events.posted`,
  `listings.detail_fetched/info_date/next_update`, `areas.last_crawled`.)
- `runs`: one row per run with a JSON report (written at the end, or on interrupt with `interrupted: true` and
  `finished` NULL) and `progress` (`{phase, text, at}`, the only thing written while running; older rows have it in
  the report). Ctrl-C/SIGTERM roll back the open transaction (never half an area's reconcile) and exit 130.
- Schema changes are additive and idempotent: `db.TABLES` / `db.COLUMNS`, applied by `DB.migrate()`, and one-time
  data fixes under `PRAGMA user_version` (`maintenance.strip_list_fields`). Only writing commands run them, after
  taking the lock (`status` opens the DB with `migrate=False`): old code must keep working on the result.
- IDs are SUUMO `nc_` numbers, per agent listing. The same property listed by several agents has several IDs;
  `dup_key` (type + building/address + size + price) groups them. Only one listing per group (its lead) gets its
  page fetched, decided in `Pipeline._leads` only, among the listings seen in this crawl: one whose page is stored;
  else the one already queued and not given up on (it keeps its place in line, `enqueued_at`, when SUUMO shows the
  members in another order, which it does daily for ~20% of groups); else the first on the page not given up on.
  So when the lead goes missing, ends or is gone (404), a copy is queued and fetched. Other agents' copies are never
  fetched while the lead stands, by the owner's decision (2026-10-15: so their `deal_type`, e.g. 仲介手数料なし,
  stays unknown). `_queue_order` only orders the queue (new first, then the prefecture with the most listings; a
  land twin after its queued twin).
- **A `dup_key` group in which a known `agent` appears twice is not one property** (an agent doesn't list one
  property twice), **nor is a condo group whose fetched listings have two different known `floor`s.** Their
  listings stand alone. The crawler applies the agent half when queueing (every listing of such a group gets its
  page; floors are only known after fetching); the site applies both when folding (`catalog.Snapshot`).
- The site's listing record (`data/l/<id % 8192>.json.zst`, key `type:id`): the exported record minus `id`, `type`,
  `dup_key`, `has_detail`, `area_code`, `area`, `town`; `url` as `pref/slug` (or a path), `image` compact; plus
  `history`, `new_date`, `others`, and from the page side files `img` (`{b, l}`, page order), `texts` (catch, pr,
  event, present, staff, seller, notes), `env` (`[[kind, text]]`) and `office` (the page's agent block: name, tel,
  license, address, hours, about; `agent` stays the search result's name). `lat`/`lng` only when trusted (`own_point`).
- `list_json`/`detail_json` are stored with `db.dumps` (sorted keys) so equal data is equal text.

## Invariants (keep these true)

- **Only robots.txt-allowed paths.** Search results: `/{TYPES[type]}/{pref}/{slug}/?pc=100&page=N`; area pages:
  `/{type path}/{pref}/city/`; listing pages: the `path` from the search result. `/jj/bukken/ichiran/...` and any
  `sort=` parameter are disallowed (robots.txt has no crawl-delay for `*`; its `Crawl-delay: 30` is bingbot's).
  Redirects aren't followed. One request at a time per crawler, `--delay` 0.3 s (the default and the floor,
  `cli.MIN_DELAY`) + up to 50% jitter, search results and listing pages alike, by the owner's decisions (2026-10-14:
  listing pages at 0.5 s; 2026-10-16: 0.3 s, after 0.5 s drew no pushback; 2026-10-17: everything at 0.3 s, after
  ~220k listing pages at 0.3 s drew none). Once a run on record (90 days) got a 429/403 (`Client.pushbacks` in the
  report), runs use 1.0 s (`cli.run_delay`). On GitHub Actions, one worker per shard in parallel (up to 8 of 20 at
  once, disjoint prefectures, each its own state and IP; the owner's decision, 2026-11-15), `--delay 1.0` per worker
  (crawl.yml clamps a dispatch's delay to >= 1.0). Never a second crawler on the same states.
  `http.Client` slows itself down on 429/5xx/network errors/slow responses (doubling, max 10 s, honours
  `Retry-After`) and eases back; keep that behaviour.
  Geocoding is also one request at a time (`geo.GEO_DELAY`).
- **Removal needs evidence.** A listing is removed only after `REMOVE_AFTER_MISSES` (2) consecutive *complete*
  crawls of its area miss it, on different crawl days: misses count once per area per crawl day (it starts at 04:00,
  `pipeline.DAY_STARTS`; `areas.missed_on`), so a restarted crawl can't remove anything. Then it's deleted (leaving a
  tombstone in `ended`), or kept as `removed` (ended) if it's in `saved.json`. Incomplete/error areas never count;
  a `suspect` crawl (lost >30% of ≥20) counts nothing and uses up the day, and its count becomes the reference,
  so a real drop is distrusted for one crawl day only. A search-result page without SUUMO's hit count is an error,
  never "complete with 0". An area page with no areas, or whose included areas' counts add up to less than
  (1 - `SUSPECT_DROP`) of the ≥`SUSPECT_MIN_HITS` active listings held there, is an error (`AreaPageError`)
  before anything is reconciled. An area that vanishes from the area page, or is listed as "(0)" without a link,
  is reconciled as complete with 0 hits (same guards apply; with nothing held it's a baseline, so its first listing
  is 新着); so is a new_condo area another type knows but the new_condo page doesn't list.
- **Returns are relisted.** A deleted listing seen again while its tombstone lasts (14 days) gets back its
  `first_seen`, a `relisted` event (plus `price_changed` if the price differs) and its page queued again; later
  it's `new`. An id first seen under one type that is held under another (SUUMO re-types listings, e.g. new_house →
  used_house, and lists some land under both new_house and land) keeps that row's `first_seen`, gets no `new`
  event unless the other type's got a new/relisted one in the same run, and is queued at backfill (report count
  `retyped`); the old type's row ends after its misses as usual.
- **Baselines are silent.** An area's first complete crawl creates no events (so nothing is 新着); its listings
  queue as backfill.
- **Extract, keep no HTML.** A fetched listing page becomes a page record (`extract`) stored with its fields
  (`pipeline.store_page`, the one write path: page_json, extract_v, fetched_at, detail_json = derive) in one transaction;
  a good record is never nulled by a failure. An extract error gives the listing up (`extract:` in the queue; `refetch
  --errors` after a fix); 10 in a row stop the queue without using attempts (a layout change); both, a derive error,
  or extract errors above 5 (or 1% from 200 pages) flag the run, and the shard's Healthy step goes red. A derive fix
  is always a PARSE_VERSION bump (a refetch whose derive failed keeps its old fields until then): re-derived from
  the records, no requests. An extract fix only reaches pages fetched again
  (the slow refresh takes old extract_v first). Raw HTML only in the 7-day `debug-<n>` artifact: every extract
  failure + a deterministic 0.5% sample, each with its record (`python -m suumo extract FILE`).
- **Refetches are bounded.** Queue priorities: new 2, changed 1 (a relisted lead, or a price change on a lead whose
  page is >14 days old), backfill 0; after the queue, up to 20 refresh pages per shard and run (old extract_v, then
  the oldest fetched_at over 30 days). Lead = the group member holding a page record.
- **Deterministic export.** `data/` files are sorted by id with `FIELD_ORDER` key order and contain nothing that
  changes on its own (`last_seen`, `missed` stay in the DB). An
  unchanged run must change no group in `data/snapshot.json` (so no new snapshot is uploaded) and no file in git (except what the run itself refetched: the slow refresh rewrites up to 20
  pages per shard and run, so their page_date and page files may change); check this after any export change. Files are replaced atomically. The
  site build is deterministic too: buckets sorted by key, zstd at a pinned version and level (two builds, same bytes).
- **One writer.** Writing commands hold `state.db.lock`; `status`, `geocode` and `site` don't. On Actions, one writer per
  shard state (the workflow's concurrency group), and the Mac crawler must not run on its own state.db at the same time
  (two states diverge: duplicate events).
- **One implementation of the search rules: `site/filter.js`.** Python only derives fields (`catalog.Item`,
  `site.build_index`) so the JS compares plain values; flag bits travel in the index (`site.FLAGS`). The rule tests
  in `tests/test_site.py` build a site from synthetic records and run queries through filter.js in Node.
- **Rules the site shows:** duplicates (`dup_key`) are folded at build time (`site.representatives`: the listing
  with its page fetched and most tags; the others become `others` links). Land
  listed under both `new_house` and `land` is kept once, as land. Stations are identified by name, one spelling per prefecture (`catalog.unify_station_names`: 荻窪駅 → 荻窪). A building
  condition (間取り, 広さ, 築年数, 新耐震) excludes land unless 土地 was chosen explicitly. 周辺環境 = each chosen kind (小学校, 中学校, 幼稚園・保育園, スーパー, コンビニ, ドラッグストア, 病院, 公園) within N minutes on foot (default 10) by the listing page's nearby facilities; a listing whose page lists none of that kind doesn't pass. A listing's own map point is used only within 3 km of its town's point. 新着 = a new/relisted
  event within 7 days (one within 14 days of that listing's removal is a return, not 新着: `catalog.RETURN_DAYS`),
  値下げ = a drop within 30 days, measured from the price before a rise taken back within 14 days (a typo fixed:
  `catalog.SPIKE_DAYS`), never to under 20% of the old price (a lost digit: `DROP_MIN_RATIO`). 南向き = direction 南/南東/南西 or a south tag; 最上階 = floor ==
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

- **New field from listing pages:** if the page record already holds it (spec rows as written, texts, env, ...), derive
  it in `detail.derive` and bump PARSE_VERSION: every state re-derives at its next run, no requests. If it needs
  something extract() doesn't keep, extend extract() and bump EXTRACT_VERSION (new pages get it; the slow refresh
  upgrades the rest). Search-result fields: `parse._standard_unit`.
  Add it to `detail.FIELDS` and `export.FIELD_ORDER`, a test against a fixture (tests/test_extract.py), the README
  field table; show it in `site/app.js` if useful.
- **New prefecture/area/type:** edit `scope.toml` only. `prune --yes` removes data that leaves the scope.
  Everything downstream is keyed by prefecture: `scope.PREFS` (all 47, slug → name used in addresses) feeds
  geocoding and the site; the index lists each area with its prefecture, and the filter groups areas by
  prefecture once there is more than one. English area names are `places` in `site/i18n.js` (kanji is shown
  when a name is missing).
- **New property type:** add it to `parse.TYPES` (path segment), check its search-result markup in `parse_list_page`,
  add its name to `types` in both languages in `site/i18n.js`.
- **New search condition:** a derived field on `catalog.Item` and a column in `site.build_index` if the JS needs one
  (read into a typed column in `Filter.load`, and into `item` if the page shows it);
  the rule in `site/filter.js` (`emptyQuery`, `CHECKS`/`BUILDING`; a flag also in `FLAGS`/`FLAG_BITS`, a threshold in
  `THRESHOLDS` for facets; words for it in `WORDS` or a pattern in `parse`); a control in `site/app.js`
  (`renderFilters`, URL key in `KEYS`, threshold values in `CHOICES`); its labels in `site/i18n.js`; a test in
  `tests/test_site.py` (and a case in `NL_CASES` for its words). A sparse column (few rows have it) goes in `site.SPARSE`.
- **Tunables** are module constants: `pipeline.py` (misses, retention, suspect thresholds, priorities, progress,
  checkpoints, outage pause), `http.py` (slow-down), `catalog.NEW_DAYS`/`DROP_DAYS`,
  `cli.GEO_BUDGET`, and the choice lists at the top of `site/app.js`.

## Development

- `uv sync`, then `uv run pytest -q` (offline, ~5 s; the site tests need `node`) and `uv run ruff check suumo tests`
  (line length 120). pages.yml checks the built site's size (tar): step summary, warning over 800 MB, failure over
  950 MB (Pages' limit is 1 GB). `suumo/__main__.py` runs `main()` only as `__main__` (the site build's worker
  processes import it).
- Tests: `test_lifecycle.py` (reconcile/purge/export with synthetic records: misses per crawl day, suspect,
  tombstones, one page per property, area-page cases and guards, crawl_area, retries and the outage stop),
  `test_parsers.py` (value parsers),
  `test_pages.py` (real pages in `tests/fixtures/`, `detail.FIELDS`), `test_extract.py` (extract/derive: the
  equality gate against the old HTML parser in tests/legacy_detail.py, determinism, photo round trip, templates),
  `test_records.py` (store_page, extract/derive error paths, streak, debug sample, refetch priorities, slow refresh,
  twins, re-derive, the backfilled guard, seed, migrate-state), `test_export_pages.py` (page files, page fields),
  `test_canary.py`, `test_ci.py` (crawl.yml's decisions, snapshot pruning), `test_snapshot.py` (manifest, pack
  determinism, unpack's checks, the gate, the CLI), `test_ops.py` (prune, seed, lock, the HTTP client and network errors, the queue's outage/gone paths, progress, an interrupted run, the
  `status` line the driver reads, the resumable daily crawl, events export, town names), `test_rail.py` (railway
  lines), `test_stations.py` (station names), `test_catalog.py` (derived fields), `test_site.py` (build output on
  real listings in `tests/fixtures/data/`, every search rule through filter.js). Shared helpers: `tests/helpers.py`
  (`rec`, `write`, `site_queries`, `QUIET`). Tests never touch the network.
- Look at the site locally: `uv run python -m suumo site && cd _site && python3 -m http.server`.
- Try changes on a copy: `--db`, `--data`, `--debug-dir` point anywhere (`python -m suumo --db /tmp/s.db ... run`).
- Testing derive changes without requests: bump PARSE_VERSION, run on a downloaded state copy (re-derive at start) into a data/ from the snapshot (`data.sh get`, then `git init` it, or compare `snapshot.manifest`). Extract changes: `python -m suumo extract FILE` on fixtures or a debug-<n> sample.
- Never commit `.env`, `state*.db*`, `logs/`, `_site/`, `uv.lock`.
