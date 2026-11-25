# SUUMO for-sale tracker

Crawls SUUMO for-sale listings in the areas listed in `scope.toml` (all 47 prefectures), fetches each listing's own
page once for the full spec, tracks listings as they appear, change price and disappear, exports the data as
JSONL to `data/` (a snapshot on the release `suumo-data`, its pointer and the event log in git), and publishes a search site on GitHub Pages:
**https://huangwaylon.github.io/suumo/**

## The site

Plain HTML/CSS/JS (no framework, no build step), in Japanese, rebuilt by GitHub Actions on every push to `main`.

| Screen | Layout |
|---|---|
| Phone, tablet (<1024px) | the map full screen under a bottom sheet with the list: peek (the count), half or full; drag its head or tap the handle; full-screen filter sheet (side sheet from 700px) |
| Wide (≥1024px) | map · a divider (drag, or arrow keys) · the list; filters as a side sheet |
| Desktop (≥1280px) | filters rail (« hides it) · map · divider · list |

On every size the list is what's in the map's view (「この範囲 N件 / 全M件」, 「N件 / 全M件」 when narrow; 全M件 fits the map to all results),
in 1–3 columns of cards as the list's width allows. A listing opens as a tab of the list (on phones the sheet goes
full height; back returns to the list): opened listings stay open as tabs (up to 12; × or すべて閉じる), and with
two or more 比較 shows them side by side. The listing has a sticky 「お気に入り / SUUMOで見る」 bar at the bottom.

- **Search**: station, town or building name; several words narrow down; hiragana, katakana, half-width kana and
  romaji match (station readings from Wikidata). Typing a station or ward name offers it as a filter in one tap.
- **Filters** (one panel, every choice shows its count): 種別, 価格, 間取り（以上: 2LDK〜…）, 広さ, 土地, 築年数,
  新耐震, 駅 (search by name) and 徒歩, 周辺環境 (an elementary or junior high school, nursery, supermarket,
  convenience store, drugstore, hospital or park within 5/10/15 minutes on foot), エリア (by prefecture and
  区部/市部/町村), こだわり (所有権のみ, 建築条件なし, 新着, 値下げ, SUUMO's tags). Active conditions show as chips under the count: the label opens the filters at that
  condition, × removes it. The last search (and where the map was) comes back when the site is reopened; browser
  back closes the filter sheet, settings, the prefecture picker and a listing's tab.
- **Results**: cards with photo (and how many the listing page has), price, layout, size, age, nearest station,
  town; 10 sort orders (incl. 写真が多い順, 値下げ率が大きい順); more load as you scroll. Pins by town (丁目 level), clusters when
  zoomed out, price tags from zoom 15; a town's pin lists its homes. From zoom 16 a home whose listing page gives
  its coordinates gets a pin of its own there (tinted; only within 3 km of its town's point), the town's pin keeps
  the rest. The map moves only when asked: a prefecture, an area or a station named (from the filters,
  the search box or a suggestion), a saved search, 全M件; filter changes keep the view. Nothing in view →
  「全体を表示」; no results at all → the conditions to drop, with counts. Results without coordinates are folded
  at the end of the list (「地図に表示できない物件」).
- **What's new**: 新着 tags say when (3時間前, 10/6), 再掲載 marks relisted homes, 値下げ tags show the cut (−8%).
  Above the results, one-tap 新着 / 値下げ / 前回の訪問以降 chips with counts under the current conditions (a visit
  is a new data version, remembered in the browser); with 新着 or 前回の訪問以降 on, the list is grouped by day.
  A property another agent already listed doesn't count as new.
- **Saved searches** (this browser): the star saves the current conditions; each shows how many listings are new
  or cheaper since it was last opened (+N), and opening it shows those first.
- **Railway lines and stations** on the map, the whole country: every operator's lines in their colours; from
  zoom 11 the stations (names from 14), each opening its lines and 「この駅の物件 N件」 (filters by it, the map
  stays). 「路線」 switches lines off, or operator by operator. A chosen station's lines are highlighted. Data: 国土数値情報（鉄道データ）（国土交通省, CC BY 4.0）; line colours and English names from
  公共交通オープンデータセンター (ODPT; the challenge datasets are licensed for the ODPT challenge only).
- **Listing**: the gallery (every photo of the listing page, opening on the card's: caption and comment, chips by
  kind with 間取り図 first, a strip of thumbnails; tapping the photo opens a full-screen viewer: arrows, swipe,
  Esc or back closes it), the agent's catch line, key facts, 価格の推移, 相場感, 交通, the agent's PR, events and
  presents, 周辺環境 (by kind, closest first), 費用, 建物, 土地・法規, 特徴, other agents' listings, the staff's
  and seller's comments and notes, 会社情報 (licence, phone, address, hours), 「掲載情報 YYYY年M月D日 時点」 (when
  the page was read), a link to SUUMO; its town is ringed on the map and 「地図で見る」 takes the map there (its own
  point when it has one). Free texts fold; a listing that couldn't be loaded offers 再読み込み.
- **Settings** (gear icon): language 日本語 (default) or English, and theme システム / ライト / ダーク, saved in the
  browser. In English the interface, units, prices (¥49.9M) and fixed values (land rights, deal type) are
  translated, and so are ward and station names and common tags; towns, building names and free text stay as
  SUUMO writes them.
- **Favorites** are one shared list (`saved.json`): the heart opens a pre-filled GitHub issue
  (`save used_condo:123`); submitting it (a GitHub account is needed) runs the Saved workflow, which updates the
  list and rebuilds the site in a few minutes. Saved listings are kept after they leave SUUMO, marked 掲載終了.
  The favorites view shows all of them (the map fits to them; back returns to the search where it was), and
  「比較する」 opens them as tabs side by side (price, size, age, station, monthly costs, floor, land rights).
  Conditions live in the URL (and the map's place, `m=lat,lng,zoom`), so any search or listing can be shared.

One property listed by several agents (same `dup_key`: building/address, size and price) appears once, with
「ほかN社も掲載」; so does a condo unit listed as both 新築 and 中古 (same 丁目, size and price; it shows under its
best-documented listing's type). 新着 = new or relisted in the last 7 days (the first crawl of an area records its listings
silently), 値下げ = a price drop in the last 30 days. Names that are agents' ad copy aren't shown.
Conditions from the listing pages (only listings whose page was fetched have them; the manifest says what share
that is per prefecture): 南向き (direction 南/南東/南西 or a south tag), 最上階, 2階以上, タワー (20+ floors),
仲介手数料なし (sold by the seller or its agent: if any agent of a property sells without commission, the property
counts, and that agent comes first among the others), 即入居, 駐車場 (houses and land), and condos' monthly fees
(管理費 + 修繕積立金 + その他). Station names are spelled one way per prefecture (荻窪駅 → 荻窪). The search box also
reads conditions from plain text (「渋谷 3LDK 6000万以下 徒歩10分 ペット可」, "2ldk under 5000万 near Shibuya",
「小学校近い」「スーパー徒歩5分」, "near a park"): `Filter.parse` in site/filter.js; what it doesn't understand stays a
text search. 周辺環境 comes from the listing page's nearby facilities (SUUMO's minutes on foot to the closest of
each kind); a home whose page lists none doesn't pass it; with no time given it means 10 minutes.

The site is public (GitHub Pages) but marked `noindex`. Photos load from SUUMO's image server (the site stores
only their paths); the service worker keeps the last 100 or so (cache first, least recently used out first,
refetched after 7 days: SUUMO sends no CORS headers, so each is an opaque response that browsers count as
megabytes of storage) and keeps the favorites' photos (their card's and the first two of the gallery, for up to
10 favorites) for good. Leaflet loads from unpkg (with integrity hashes) only when a map is shown, with the
clustering (supercluster, ISC, in `site/supercluster.min.js`); listing records are decoded with fzstd (MIT, in
`site/fzstd.min.js`).

What the build (`python -m suumo site`, suumo/site.py) writes to `_site/`:

| Path | Contents |
|---|---|
| `index.html`, `main.js` and its modules, `filter.js`, `i18n.js`, `style.css`, `sw.js` | the page, from `site/` (sw.js stamped with a version of them) |
| `data/index.json` | the manifest: prefectures `[slug, name, properties, share with the listing page fetched]`, the saved list, `buckets` (8192) and `bucketExt` (`.json.zst`), the dollar rate |
| `data/p/<pref>.json` | one prefecture's search index, by column (loaded as chosen; sparse columns as `{at: row deltas, v: values}`) |
| `data/l/<id % 8192>.json.zst` | listing records, about 50 per file (~65 KB): zstd (level 19, `zstandard` pinned) of `{"keys": ["type:id", ...], field: [its value per key], ...}`, keys sorted, so an unchanged build gives the same bytes. The page fetches the file (revalidated, never cached by the service worker), decodes it (`Filter.decode`: zstd, or an older build's gzip or JSON, by its first bytes) and reads its key; no key = 掲載終了 |
| `data/rail.json` | railway lines and stations for the map |

The build holds one prefecture in memory at a time: each listing page record is parsed once, into its listing's
record (appended to a temp file per bucket) and its 周辺環境 column; at the end the buckets are sorted and
compressed, one process per CPU. With a page record for every listing (measured on synthetic side files,
2026-11-15): a 626 MB artifact, buckets 66 KB median / 77 KB p90 (one per listing opened; 7–15 ms to decode at 4×
CPU throttling), 103 s and 1.5 GB on an 18-core Mac. pages.yml writes the size to the step summary, warns over
800 MB and fails over 950 MB (Pages' limit is 1 GB).

## How it runs: GitHub Actions (no machine of ours)

`.github/workflows/crawl.yml` does the crawl in parallel. `shards.toml` gives each of 20 workers a fixed set of
prefectures; at most 8 run at once (`max-parallel`: the account's 20 runners are shared with other repos, see below).
Schedule: GitHub's cron, every 30 minutes plus fixed slots (GitHub delivers only a fraction of scheduled runs, so
there are many ticks; no external scheduler). Every run passes `--crawl-at 5,15`: the first run after 05:00 and after
15:00 JST (just after SUUMO's two daily updates) crawls all search results; other runs only work the listing-page
queue. A run:

1. **plan** (one API call): reads each shard state's release label (`queued=N crawled=pref@time,...`) and keeps only
   shards whose crawl is due or whose queue isn't empty; none: the run ends there;
2. **canary** (~16 requests): an area page, a search page and listing pages per type through the parsers. Search
   pages broken: no shard runs (red). Listing pages broken: shards crawl search results only (red). SUUMO
   unreachable: shards run anyway (they pause on outages themselves);
3. **shards** (at most 8 at once), each: download `state-<n>.db.zst` from the release **`suumo-state`** (public like
   `data/`) and check it; `python -m suumo --shard N --debug-dir … run --crawl-at 5,15 --delay 1.0 --budget B
   --no-geocode` into an empty `data/` (B: 10m after a crawl, 45m on the 01:xx/10:xx queue runs, a dispatch's input
   up to 60m); save the state back even when the run failed or timed out (`state.sh`: WAL folded in, integrity
   check, swapped in only once whole; the previous upload kept as `state-<n>.prev.db.zst`); upload its `data/`, its
   run report and its debug sample (7 days); **Healthy**: red on an outage, incomplete areas, an extract streak,
   extract or derive errors, or low coverage (coordinates, photos, spec rows per template);
4. **combine**: unpacks the last data snapshot, lays every worker's prefectures over it (a failed worker's stay as
   they were), sums the reports (coverage, unrecognised page labels/sections) into the summary, geocodes new towns,
   updates `fx.json`, uploads the new snapshot, commits `data: run ...` (its pointer and the new events files),
   pushes, and starts the site build.

The **combine** job unpacks the last snapshot (below), lays every worker's prefectures over it (a failed worker's
prefectures stay as they were; a prefecture in no shard is removed), geocodes new towns and updates `fx.json`, uploads
the new snapshot, commits `data: run ...`, pushes, and starts the site build (`pages.yml`; a push by Actions doesn't
trigger it by itself). A worker re-exports every events file of its own runs, so if a push is ever lost the next run
puts the files back.

**The data snapshot.** `data/<pref>/` (2.8 GB of listings and page files; it grew git by ~150 MB a day) isn't in git.
It travels as one asset, `data-<JST stamp>-<run>-<attempt>.tar.zst` (~270 MB) on the release **`suumo-data`**, named
once and never replaced. `data/snapshot.json` (in git, written by combine) names it (tag, asset, size, sha256) and lists
its groups, one per `<pref>/<type>`, `<pref>/pages`, `<pref>/areas.json` with file and record counts and a hash: a
`data: run` commit's diff of it shows which groups the run changed. Git keeps `data/events/`, `data/fx.json` and
`data/canary/` (the canary's listings: the plan job checks out nothing else of data/). Combine refuses to publish
when the last snapshot doesn't download or match its pointer (it never lays the shards over an empty data/), and when
the gate refuses the new data: a prefecture with no records, or one of ≥200 down by more than half (`snapshot.check`;
Run workflow with **force** to publish it anyway). The site build (`pages.yml`) unpacks the snapshot its commit names;
a bad one fails the build and Pages keeps the last deployment. Old snapshots are pruned after each push: the
pointer's and the newest 8 stay (`data.sh`). **Recovery** from a lost snapshot: Run workflow with **bootstrap** and
every shard: combine starts from an empty data/ and every shard exports all its prefectures (the gate checks they're
all there).

**Run it by hand:** Actions → Crawl → Run workflow (inputs: delay, budget, which shards, force, bootstrap). Off main
it's a test (snapshots on `suumo-data-test`, starting from the branch's pointer, or main's once that asset is pruned): the
state comes from the release `suumo-state-test` and the result goes to the branch `crawl-test`, never to main.

**Runner budget** (the owner's decision, 2026-11-15): the GitHub account's 20 concurrent runners are shared by all
its repos (Free plan): suumo 8, fitness's Kalori scraper 10, the fitness ITS runner 1 (always on), the rest for
deploys and checks. Raise `max-parallel` in crawl.yml only together with that budget.

**Changing shards** (moving a prefecture, a new one in scope.toml, more workers): with no crawl running, download the
states from the release, join, edit `shards.toml`, split, upload:

```sh
gh release download suumo-state -p 'state-*.db.zst' -D st && for f in st/*.zst; do zstd -d --rm "$f"; done
uv run python -m suumo shard-join --out st/state.db st/state-*.db
# edit shards.toml (every prefecture in scope.toml exactly once; a test checks)
uv run python -m suumo --db st/state.db shard-split --out st/new
for f in st/new/state-*.db; do zstd -q -9 "$f"; done
gh release upload suumo-state st/new/state-*.db.zst --clobber    # replaces the same-named files
# fewer shards than before: gh release delete-asset suumo-state state-<n>.db.zst for each one beyond the new count
```

A new prefecture needs no state of its own: its first crawl baselines it silently.

**Commands that need the state** (`status`, `prune`, `seed`, `refetch --errors`): download and join the states as
above and run the command on `st/state.db` locally (`--db st/state.db`); for one that changes the state, disable
crawl.yml, split and upload it, enable it again. A derive change needs none of this: bump `detail.PARSE_VERSION` and
every shard re-derives its records at its next run.

**Pages, not HTML.** A listing page is fetched, extracted into a page record (every spec row as written, photos,
coordinates, dates, free text, nearby facilities, agent block) and stored in the shard's state; the fields in
`data/` are derived from it. No raw HTML is kept except the 7-day `debug-<n>` artifact (pages that failed extraction
and a 0.5% sample, each with its record: `uv run python -m suumo extract FILE` reproduces one locally).

**Watchdog.** `.github/workflows/watch.yml` runs every 3 hours on main and checks that the crawl is alive: a
`data: run` commit within 26 h, the last `crawl.yml` and `pages.yml` runs on main not failed, and `crawl.yml`
enabled and started within 26 h (if not, it re-enables it and/or dispatches a run itself). Any problem opens one issue,
"Crawl watchdog: attention needed" (label `watchdog`; GitHub emails the owner). It gets a comment only when the
problems change, and is closed once all checks pass. Run it by hand from the Actions tab (Watchdog → Run workflow).

## Running locally (development)

`uv sync`, `uv run pytest -q` (tests use `tests/fixtures/`, no snapshot). The real `data/` (for `site`, `geocode`,
`seed`, `rail`): `GH_REPO=huangwaylon/suumo .github/scripts/data.sh get data` (needs `gh` signed in; downloads the
snapshot `data/snapshot.json` names, ~270 MB, and unpacks it). A clone that had `data/<pref>/` in git loses those files
when it pulls the commit that untracked them: fetch the snapshot then. To try a run against real data without touching production: download one shard's
state (`gh release download suumo-state -p 'state-3.db.zst'`, `zstd -d`), then
`uv run python -m suumo --shard 3 --db state-3.db --data /tmp/d --debug-dir /tmp/dbg run --budget 5m --no-geocode`.
Never upload a state you ran locally while crawl.yml is enabled.

## Commands

| Command | What it does |
|---|---|
| `uv run python -m suumo run` | crawl search results, fetch and extract listing pages for up to `--budget` (default `3h`), clean up, export `data/`, geocode new towns (`--shard N`: one worker's prefectures) |
| `run --budget 30m` / `--budget 0` | smaller / no listing-page budget; the queue continues next run |
| `run --no-crawl` | only work the listing-page queue |
| `run --crawl-at 5,15` | crawl search results only while a prefecture in scope wasn't crawled since the latest of these hours (just after SUUMO's two daily updates), skipping those that were |
| `uv run python -m suumo status` | per pref/type: active listings, % with listing-page details, queue, area states, the current run's progress |
| `uv run python -m suumo prune [--yes]` | delete data no longer in `scope.toml` (dry run without `--yes`) |
| `uv run python -m suumo extract FILE` | a saved page (a debug sample) → its page record and derived fields |
| `uv run python -m suumo refetch --errors [--limit N]` | queue listings whose page didn't extract again (after an extractor fix) |
| `uv run python -m suumo canary` | the crawl's pre-flight check (~16 requests); `--pick`: write `data/canary/candidates.json` (no requests) |
| `uv run python -m suumo snapshot pack FILE --tag T [--force]` / `snapshot unpack FILE` | `data/<pref>/` → a snapshot and a new `data/snapshot.json` (exit 4 unchanged, 1 refused by the gate) / a downloaded snapshot → `data/<pref>/`, checked against the pointer (crawl.yml, pages.yml, `data.sh`) |
| `uv run python -m suumo migrate-state` | add the page-record schema to a state (idempotent) |
| `uv run python -m suumo seed` | rebuild a state for in-scope prefectures it holds nothing of from `data/` (no requests; last resort: page records can't be rebuilt, every lead is queued) |
| `uv run python -m suumo geocode` | look up coordinates for towns not in `geo/towns.json` yet (MLIT's 位置参照情報 files, then the address search for the rest), and station names (kana, English) for prefectures not in `geo/stations.json` |
| `uv run python -m suumo rail` | rebuild `geo/rail.json` (railway lines and stations for the map) from MLIT N02 and ODPT colours; `ODPT_TOKEN` and `ODPT_CHALLENGE_TOKEN` in `.env` (ODPT keys, never committed) |
| `uv run python -m suumo site [--out _site]` | build the site locally (`cd _site && python3 -m http.server` to view it) |

Only one writing command runs at a time (`state.db.lock`); a second one exits with a message. `geocode`, `site`
and `status` don't need the lock.

## Scope

```toml
[[scope]]
pref  = "tokyo"                      # SUUMO slug: tokyo, chiba, kanagawa, saitama, ibaraki, ...
types = ["used_condo", "used_house"] # optional; default all: used_condo new_house used_house land new_condo
areas = ["13219"]                    # optional JIS municipality codes; default the whole prefecture
```

A new entry needs no code: the next run records its current listings silently (baseline) and queues their
listing pages behind new listings elsewhere. After removing an entry, `prune --yes` deletes its data. Area codes
and names are in `data/<pref>/areas.json` after a run.

## How a run works

1. **Search results**: every page of every area in scope (100 listings per page, one request at a time),
   with the parsed count checked against SUUMO's count. Short areas are retried once,
   then marked `incomplete`; a page without SUUMO's count is an `error`. An area page with no areas, or whose
   counts add up to less than 70% of the (≥20) listings held there, is an error and nothing of it is reconciled.
   A failed area page or area is tried once more at the end of the crawl. Each prefecture is marked crawled as
   soon as it's done (retry included). Areas listed as "(0)" (and new_condo areas the other types know but the
   new_condo page doesn't list) count as crawled with 0, so their first listing is new.
2. **Changes**: the results are diffed against `state.db`. An area's first complete crawl is a silent baseline.
   After that, new IDs and price changes become events. A listing missing from 2 complete crawls on different
   days (a day starts at 04:00) is deleted (row and page record; a `removed` event), unless it's on the saved list:
   then it's kept as ended and still shown on the site in お気に入り. A deleted listing that comes back within 14
   days is `relisted` with its original first-seen date; an ID SUUMO moves to another type (e.g. 新築 → 中古)
   keeps its first-seen date and isn't new. If an area loses more than 30% of at least 20 listings
   at once, it is marked `suspect` and nothing is removed that day; the next day compares against the new count.
3. **Listing pages**: fetched for listings without a page record (new first), again when a listing is relisted or
   its price changes more than 14 days after its page was fetched, and a few of the oldest records per run (the slow
   refresh), until the budget runs out. Each page is extracted into a page record; its fields are derived from it. One listing per property (`dup_key`) gets its page, and it stays the same one when SUUMO reorders
   the results; another agent's copy only when that one goes
   missing, ends or is gone (a group where one agent appears twice is several properties: each gets its page). A page that
   is gone (404, or a redirect: status and Location are logged and kept in the queue) isn't asked for again. Search-result fields (price etc.) are refreshed every crawl. A page that doesn't extract is given
   up (in the queue as `extract:`; `refetch --errors` after a fix); 10 in a row stop the queue (a layout change).
4. **Cleanup**: ended listings no longer saved are deleted (leaving a 14-day tombstone, so a return is relisted);
   event and run rows are kept 90 days.
5. **Export**: `data/` is rewritten deterministically (an unchanged day changes no group in `data/snapshot.json`;
   no new snapshot is uploaded) and the run's
   events go to `data/events/<run_id>.json` (the site's 新着, 値下げ and price history come from them; an
   interrupted run's are written by the next run).
6. **Geocoding**: towns of new listings are placed from MLIT's 位置参照情報 (大字・町丁目 points, one download per
   prefecture), the few it doesn't know from 国土地理院's address search (one request a second, ≤10 min per run).

## Files

| Path | In git | Contents |
|---|---|---|
| `scope.toml` | yes | what to crawl |
| `data/<pref>/<type>/<area_code>.jsonl` | snapshot | active listings of one type in one municipality, one JSON object per line, sorted by id |
| `data/<pref>/removed/<type>.jsonl` | snapshot | ended listings kept because they're saved |
| `data/<pref>/pages/<type>/<area_code>.jsonl`, `pages/removed/<type>.jsonl` | snapshot | what the listing pages add, one line per listing with a page: `{id, img, texts, env, agent, dates}` (the site reads them with `export.pages`) |
| `saved.json` | yes | the shared saved list (`type:id` keys), changed only by the Saved workflow |
| `data/<pref>/areas.json` | snapshot | area code → name |
| `data/events/<run_id>.json` | yes | that run's events |
| `data/snapshot.json` | yes | the data snapshot's pointer: release tag, asset, size, sha256, and its groups (files, records, hash) |
| `data/canary/candidates.json` | yes | the canary's listings per type (the newest fetched in Tokyo), written by combine |
| `data/fx.json` | yes | yen per dollar (ECB reference rate, checked daily by `run`) for the site's USD prices |
| `geo/towns.json` | yes | town (丁目) → [lat, lng], or null when not found; `_bulk` lists the towns MLIT's 位置参照情報 files (`url`) don't place, waiting for the address search, so those files aren't downloaded again each run |
| `geo/rail.json` | yes | railway lines (colour, simplified geometry) and stations for the map, from MLIT 鉄道データ and ODPT; rebuilt with `python -m suumo rail` |
| `geo/stations.json` | yes | station → [kana, English, n] from Wikidata (CC0): a name found in several prefectures keeps the reading from the one whose listings mention it most (n times), gaps filled from the others; `_prefs` lists the prefectures fetched. Delete to refresh |
| `site/` | yes | the site's source (index.html, main.js and its modules, filter.js, i18n.js, style.css, sw.js) |
| `.github/workflows/pages.yml` | yes | tests (plus the front end's lint and smoke test), builds and deploys the site on every push |
| release `suumo-state` | no (release assets) | one SQLite state per shard: listings and lifecycle, page records, areas, queue, events, runs, crawl times, tombstones (14 days), `meta` |
| `uv.lock` | no | local lock (CI resolves from public PyPI) |

## Listing records

Types: `used_condo` 中古マンション (one unit), `new_house` 新築一戸建て (one listing, may span several plots; some are
land sold with a build condition, `price_excludes_building`), `used_house` 中古一戸建て, `land` 土地 (may span
plots), `new_condo` 新築マンション (one development).

Numbers carry their unit in the name (yen, m², minutes); labels stay as SUUMO writes them. A missing key means
SUUMO doesn't state it.

| Group | Fields |
|---|---|
| Identity | `id`, `type`, `area_code`, `area`, `town`, `address`, `name`, `title`, `url`, `image`, `agent`, `first_seen`, `removed_at`, `has_detail`, `dup_key` |
| Money (yen) | `price`, `price_max`, `price_excludes_building`, `mgmt_fee`, `mgmt_form`, `repair_fee`, `repair_fund_once`, `other_monthly`, `other_fees`, `parking {status, fee_min, fee_max}` |
| Access | `stations [{line, name, walk, bus, bus_stop}]`, up to 3 (`bus_stop` when SUUMO marks a bus line; the site also treats names with no railway station nearby as bus stops) |
| Size (m²) | `layout`, `floor_m2`, `balcony_m2`, `land_m2`, `building_m2`, `site_m2`, `*_max` for ranges, `road {dir, width_m, private_m2, setback_m2, text}` |
| Building | `built` (YYYY-MM), `built_planned`, `floor` (negative = basement), `floors_above`, `floors_below`, `structure`, `total_units`, `units_for_sale`, `direction`, `builder`, `reform {date, text}`, `energy`, `insulation` |
| Legal | `land_rights` (所有権 / 地上権 / 定期借地権 / 普通借地権 / 旧法借地権 / 借地権), `land_rights_note`, `zoning`, `coverage_pct`, `far_pct`, `land_status`, `build_condition`, `land_category`, `restrictions`, `utilities` |
| Other | `features` (SUUMO 特徴ピックアップ tags, e.g. ペット相談, 角住戸), `deal_type` (仲介 / 売主 / 代理…), `handover`, `sale_schedule`, `top_price_band` |
| Page | `lat`, `lng` (the listing page's map point, 4 decimals; omitted outside the prefecture), `photos` (gallery size), `page_date` (when its page was fetched) |
| Page files (`data/<pref>/pages/…`) | `img {b, l: [n \| [[b2, n(, ext)], caption, comment?]]}` (all photos in page order: a number in the base folder `b`, or another folder; caption = SUUMO's category; comment as written), `texts {catch, pr, event, staff, seller, present, notes}`, `env [[facility kind, "name：徒歩N分（Mｍ）"]]`, `agent {name, tel, license, address, hours, about}`, `dates {info, next, terms_until}` |

IDs are per agent listing; `dup_key` groups the same property listed by several agents.

On the site, a listing's record (`data/l/`) is this one less what its key and its index row carry (`id`, `type`,
`dup_key`, `has_detail`, `area_code`, `area`, `town`), with:

| Field | Contents |
|---|---|
| `url` | `pref/slug` for the usual `https://suumo.jp/<type path>/<pref>/<slug>/nc_<id>/` (the page rebuilds it), else the path (`/tochi/…`: land listed under new_house) |
| `image` | the card's photo: a number for the usual path, else a shortened path (`Filter.photo`) |
| `lat`, `lng` | the listing page's coordinates, only within 3 km of the town's (丁目) point (or, with no town point, inside the prefecture) |
| `img` | the gallery `{b, l}` in page order: an entry is a photo number, or `[number or [b, number, ext], caption, comment]` (`Filter.gallery` makes `{path, caption, comment}`; the path rule is `extract.img_src`'s) |
| `texts` | the agent's texts as written: `catch`, `pr`, `event`, `present`, `staff`, `seller`, `notes` |
| `env` | 周辺環境: `[[kind, "name：徒歩N分（Mｍ）"], …]` |
| `office` | the agent's business details from the page: `name`, `tel`, `license`, `address`, `hours`, `about` (`agent` stays the search result's agent name) |
| `history`, `new_date`, `others` | price changes, when it was new, the other agents' listings (`url` without `https://suumo.jp`) |

## Troubleshooting

| Symptom | Check |
|---|---|
| Issue "Crawl watchdog: attention needed" | its body lists the problems with links; open the failed Crawl run. The issue closes itself once a run is healthy |
| A Crawl run is red: a shard's `Healthy` step failed | that shard's run exited 1 (SUUMO or the network down) or >5% of its areas weren't complete; its state and data are saved (or kept as they were) and the next run continues. Repeating on one shard only: look at its `Run` log (503s on one runner were seen once and gone next run) |
| A shard's `State` step failed (`has no state-<n>.db.zst`) | the release `suumo-state` lost the file: restore it from the last good copy (a dated backup if made, or `seed` from `data/` (`data.sh get data` first) for its prefectures: `--shard N --db st/state-N.db seed`), then upload it |
| Combine: `the push lost 5 races` / `rebase failed` | another push to main touched data/ (shouldn't happen: only Crawl writes it); the next run re-exports everything (the uploaded snapshot nobody points to is pruned later) |
| Combine: `the gate refuses this data/` | a prefecture came out empty or lost half its records: look at the shard that owns it (its Run log, report). Real (SUUMO really dropped them, or a prefecture left `scope.toml`)? Run workflow with **force** |
| Combine or Site: `has no asset data-…` / `size or sha256 differ` | the snapshot `data/snapshot.json` names is gone or broken on `suumo-data`: point `data/snapshot.json` at an older asset there (its pointer is in an earlier `data: run` commit: `git log -p -- data/snapshot.json`), or Run workflow with **bootstrap** and every shard |
| Red run: the canary or `extract` errors / streak | SUUMO changed its pages: download the run's `debug-<n>` artifact, `uv run python -m suumo extract FILE` on a page, fix `suumo/extract.py` (add the page as a fixture), bump `EXTRACT_VERSION`; then `refetch --errors` on the states (README "Commands that need the state") |
| Red run: derive errors | fix `suumo/detail.py`, bump `PARSE_VERSION`: the next run re-derives every record (no requests) |
| `another suumo process is running` | a writing command holds the state's lock; it frees when it exits |
| An area shows `incomplete` or `suspect` | transient on SUUMO's side; no listings are removed for it; the next run retries |
| `ERROR area page: ...` in the log | the area page looked broken (no areas, or far fewer listings than held); nothing was reconciled; tried again at the end of the crawl, and the next day |
| The site didn't update | Actions tab on GitHub: the `Site` workflow runs tests first; a failing test blocks the deploy |
| 「地図に表示できない物件が…」 on the map | towns not geocoded yet (`uv run python -m suumo geocode`), or found by neither 位置参照情報 nor the address search |

## Tests

`uv run pytest -q` (offline; parsers are also checked against real pages in `tests/fixtures/`; the site's search
rules in `filter.js` are tested with Node). `uv run ruff check suumo tests` for lint.

The front end (pages.yml's `frontend` job runs all of these; a failure blocks the deploy):

- JS lint: `npx --yes @biomejs/biome@2.6.9 lint` (`biome.json`: correctness rules only, no formatting, on
  `site/*.js` minus the vendored `*.min.js`) and `for f in site/*.js; do node --check "$f"; done`. About 1 s.
- Smoke test in a headless browser, `tests/test_smoke.py`: builds a site from `tests/fixtures/data` (plus page side
  files from the fixture listing pages), serves it on localhost and drives it at 1280×800 and 390×844 (loads without
  console errors, cards, a suggestion, a type chip, a listing and its photo viewer, back, a `#id=` link, English,
  favorites). Offline: Leaflet comes from `tests/fixtures/leaflet/` (1.9.4, as map.js's SRI expects), tiles and photos
  are stubbed. Skipped by `uv run pytest` unless Playwright is installed. About 10 s:

  ```
  uv run --with playwright==1.65.2 playwright install --only-shell chromium   # once
  uv run --with playwright==1.65.2 pytest tests/test_smoke.py -q
  ```
- Workflows and scripts: `actionlint` (1.7.12, which also shellchecks `run:` blocks) and `shellcheck .github/scripts/*.sh`.
