# SUUMO for-sale tracker

Crawls SUUMO for-sale listings in the areas listed in `scope.toml` (all 47 prefectures), fetches each listing's own
page once for the full spec, tracks listings as they appear, change price and disappear, exports the data as
JSONL to `data/` (committed to git), and publishes a search site on GitHub Pages:
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
  新耐震, 駅 (search by name) and 徒歩, エリア (by prefecture and 区部/市部/町村), こだわり (所有権のみ, 建築条件なし,
  新着, 値下げ, SUUMO's tags). Active conditions show as chips under the count: the label opens the filters at that
  condition, × removes it. The last search (and where the map was) comes back when the site is reopened; browser
  back closes the filter sheet, settings, the prefecture picker and a listing's tab.
- **Results**: cards with photo, price, layout, size, age, nearest station, town; 9 sort orders (incl.
  値下げ率が大きい順); more load as you scroll. Pins by town (丁目 level), clusters when zoomed out, price tags from zoom 15; a town's
  pin lists its homes. The map moves only when asked: a prefecture, an area or a station named (from the filters,
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
- **Listing**: photo, key facts, 交通, 費用, 建物, 土地・法規, 特徴, 価格の推移, 相場感, other agents' listings, a
  link to SUUMO; its town is ringed on the map and 「地図で見る」 takes the map there.
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
「ほかN社も掲載」. 新着 = new or relisted in the last 7 days (the first crawl of an area records its listings
silently), 値下げ = a price drop in the last 30 days. Names that are agents' ad copy aren't shown.
Conditions from the listing pages (only listings whose page was fetched have them; the manifest says what share
that is per prefecture): 南向き (direction 南/南東/南西 or a south tag), 最上階, 2階以上, タワー (20+ floors),
仲介手数料なし (sold by the seller or its agent: if any agent of a property sells without commission, the property
counts, and that agent comes first among the others), 即入居, 駐車場 (houses and land), and condos' monthly fees
(管理費 + 修繕積立金 + その他). Station names are spelled one way per prefecture (荻窪駅 → 荻窪). The search box also
reads conditions from plain text (「渋谷 3LDK 6000万以下 徒歩10分 ペット可」, "2ldk under 5000万 near Shibuya"):
`Filter.parse` in site/filter.js; what it doesn't understand stays a text search.

The site is public (GitHub Pages) but marked `noindex`. Photos load from SUUMO's image server; Leaflet loads from
unpkg (with integrity hashes) only when a map is shown, with the clustering (supercluster, ISC, in
`site/supercluster.min.js`).

## Setting up on a new Mac

### 1. Tools

```sh
xcode-select --install                             # git (skip if `git --version` works)
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv: Python and the libraries
```

Reopen Terminal, then check `uv --version`. Node is only needed to run the site's filter tests (`brew install node`).

### 2. Code and libraries

Clone into your home folder, **not** Documents, Desktop or Downloads: macOS blocks scheduled jobs from reading
those folders.

```sh
git clone git@github.com:huangwaylon/suumo.git ~/suumo
cd ~/suumo
uv sync                     # creates .venv with Python 3.12+ and the libraries
uv run pytest -q            # all tests should pass
mkdir -p logs
```

All later commands run from `~/suumo`.

### 3. State from the old machine (recommended)

`state.db` (listing history, queue) and `archive/` (raw pages, ~3 GB for Tokyo) are not in git. Without them the
first run starts over: every area is baselined again and every listing page is fetched again (about a day for
all of Tokyo). Without them but with `data/`, `seed` rebuilds the listings (and their listing-page fields) from
the export, so the next crawl records only what changed since. To carry them over, stop the scheduled job on the old Mac
(`launchctl bootout gui/$(id -u)/local.suumo`), then:

```sh
rsync -a 'OLD_MAC:suumo/state.db*' OLD_MAC:suumo/archive ~/suumo/    # OLD_MAC: its ssh host name
```

### 4. First run (only without the old state)

A whole prefecture needs every listing page fetched once: ~64k pages for Tokyo, about 7 hours at 0.3 s per
request (the default). Run it in a Terminal on a Mac that stays awake and on power (`caffeinate -i` keeps it from sleeping):

```sh
uv run python -m suumo run --budget 0                                # search results only, ~10 min: all listings known
caffeinate -i uv run python -m suumo run --no-crawl --budget 40h 2>&1 | tee -a logs/backfill.log
uv run python -m suumo geocode                                       # map coordinates for every town, about a minute
```

Follow it with `tail -f logs/backfill.log` (a progress line every 200 pages: done/total, s/page, ETA, current
delay) or `uv run python -m suumo status` from another Terminal (shows the current run while it holds the lock).
`data/` is exported every hour. Stopping it (Ctrl-C, or `kill`) is safe: the open transaction is rolled back, the
run is marked interrupted, and the next run exports its events and continues the queue. If SUUMO pushes
back, the delay rises on its own (up to 10 s) and comes back down; if it's unreachable, the run pauses 10 minutes
at a time without giving up on those listings (a page our own network couldn't send never counts against it).

### 5. Hourly schedule

Install the launchd job (the repo in `~/suumo`; launchd can't read `~/Documents`):

```sh
sed "s#__SUUMO_DIR__#$PWD#g" local.suumo.plist > ~/Library/LaunchAgents/local.suumo.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.suumo.plist
launchctl kickstart gui/$(id -u)/local.suumo     # run once now to check
tail -f logs/run.log
```

The job runs `python -m suumo run --crawl-at 4,12,18 --budget 3h --until-minute 50 --push` every hour at :05 (once
on wake if the Mac was asleep; a run still going makes the next one exit). SUUMO's big update lands around
03:00–04:00 (most price changes and removals), and new listings keep coming during the day (by 17:00, about 40% as
many as overnight), so the first runs after 04:00, 12:00 and 18:00 crawl all search results (~8,300 requests for
the whole country, ~1h50m), and the others only fetch listing pages of listings not stored yet, until :50. Requests are 0.3 s apart
(`--delay`, never lower; 1.0 s from the first run SUUMO answers with a 429/403, for as long as that run is on
record, 90 days). Every run pulls (for
`saved.json`), geocodes new towns, then commits `data/` and `geo/` and pushes, which rebuilds the site. A
listing missing from 2 crawls on different crawl days is removed (a day starts at 04:00; further crawls on the same
day don't count again). A crawl that stopped part-way resumes with the next prefecture; one stopped because the
network is down (two targets in a row unreachable) ends the run with an error, without listing pages. Before a
commit, a rebase left by a failed pull is aborted and a stale `.git/index.lock` (over 10 min) removed. `--push` needs a git remote that pushes without a prompt (an SSH key in the
agent/Keychain); a failed push is logged and retried next run. To stop it:
`launchctl bootout gui/$(id -u)/local.suumo`.

Without launchd (the repo still in `~/Documents`): `caffeinate -i ./local.hourly.sh &` runs the same schedule from
a terminal; restart it after a reboot. Runs resume where an interrupted one stopped.

## Commands

| Command | What it does |
|---|---|
| `uv run python -m suumo run` | crawl search results, fetch listing pages for up to `--budget` (default `3h`), clean up, export `data/`, geocode new towns |
| `run --budget 30m` / `--budget 0` | smaller / no listing-page budget; the queue continues next run |
| `run --no-crawl` | only work the listing-page queue |
| `run --crawl-at 4,12,18` | crawl search results only while a prefecture in scope wasn't crawled since the latest of these hours (04:00: SUUMO's daily update), skipping those that were |
| `run --commit` / `--push` | commit `data/` and `geo/` after the run / commit and push (rebuilds the site) |
| `uv run python -m suumo status` | per pref/type: active listings, % with listing-page details, queue, area states, the current run's progress |
| `uv run python -m suumo prune [--yes]` | delete data no longer in `scope.toml` (dry run without `--yes`) |
| `uv run python -m suumo reparse` | re-run the parsers over `archive/` and re-export (no requests) |
| `uv run python -m suumo seed` | rebuild `state.db` for in-scope prefectures it holds nothing of from `data/` (no requests) |
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
   saved to `archive/`, with the parsed count checked against SUUMO's count. Short areas are retried once,
   then marked `incomplete`; a page without SUUMO's count is an `error`. An area page with no areas, or whose
   counts add up to less than 70% of the (≥20) listings held there, is an error and nothing of it is reconciled.
   A failed area page or area is tried once more at the end of the crawl. Each prefecture is marked crawled as
   soon as it's done (retry included). Areas listed as "(0)" (and new_condo areas the other types know but the
   new_condo page doesn't list) count as crawled with 0, so their first listing is new.
2. **Changes**: the results are diffed against `state.db`. An area's first complete crawl is a silent baseline.
   After that, new IDs and price changes become events. A listing missing from 2 complete crawls on different
   days (a day starts at 04:00) is deleted (row, archived page; a `removed` event), unless it's on the saved list:
   then it's kept as ended and still shown on the site in お気に入り. A deleted listing that comes back within 14
   days is `relisted` with its original first-seen date; an ID SUUMO moves to another type (e.g. 新築 → 中古)
   keeps its first-seen date and isn't new. If an area loses more than 30% of at least 20 listings
   at once, it is marked `suspect` and nothing is removed that day; the next day compares against the new count.
3. **Listing pages**: fetched once, for listings not stored yet (new first, then backfill), until the budget
   runs out. One listing per property (`dup_key`) gets its page, and it stays the same one when SUUMO reorders
   the results; another agent's copy only when that one goes
   missing, ends or is gone (a group where one agent appears twice is several properties: each gets its page). A page that
   is gone (404, or a redirect: status and Location are logged and kept in the queue) isn't asked for again. Search-result fields (price etc.) are refreshed every crawl. Pages are archived before parsing; a
   parse failure is logged and the page can be re-parsed later.
4. **Cleanup**: ended listings no longer saved are deleted (leaving a 14-day tombstone, so a return is relisted);
   search-result snapshots are kept 14 days; event and run rows 90 days.
5. **Export**: `data/` is rewritten deterministically (an unchanged day is an empty git diff) and the run's
   events go to `data/events/<run_id>.json` (the site's 新着, 値下げ and price history come from them; an
   interrupted run's are written by the next run).
6. **Geocoding**: towns of new listings are placed from MLIT's 位置参照情報 (大字・町丁目 points, one download per
   prefecture), the few it doesn't know from 国土地理院's address search (one request a second, ≤10 min per run).

## Files

| Path | In git | Contents |
|---|---|---|
| `scope.toml` | yes | what to crawl |
| `data/<pref>/<type>/<area_code>.jsonl` | yes | active listings of one type in one municipality, one JSON object per line, sorted by id |
| `data/<pref>/removed/<type>.jsonl` | yes | ended listings kept because they're saved |
| `saved.json` | yes | the shared saved list (`type:id` keys), changed only by the Saved workflow |
| `data/<pref>/areas.json` | yes | area code → name |
| `data/events/<run_id>.json` | yes | that run's events |
| `data/fx.json` | yes | yen per dollar (ECB reference rate, checked daily by `run`) for the site's USD prices |
| `geo/towns.json` | yes | town (丁目) → [lat, lng], or null when not found; `_bulk` lists the towns MLIT's 位置参照情報 files (`url`) don't place, waiting for the address search, so those files aren't downloaded again each run |
| `geo/rail.json` | yes | railway lines (colour, simplified geometry) and stations for the map, from MLIT 鉄道データ and ODPT; rebuilt with `python -m suumo rail` |
| `geo/stations.json` | yes | station → [kana, English, n] from Wikidata (CC0): a name found in several prefectures keeps the reading from the one whose listings mention it most (n times), gaps filled from the others; `_prefs` lists the prefectures fetched. Delete to refresh |
| `site/` | yes | the site's source (index.html, app.js, filter.js, style.css) |
| `.github/workflows/pages.yml` | yes | tests, builds and deploys the site on every push |
| `state.db` | no | SQLite: listings and lifecycle, areas, queue, events, runs, per-prefecture crawl times, tombstones of deleted listings (14 days) |
| `archive/` | no | raw HTML: search-result snapshots by day, latest copy of each listing page |
| `logs/` | no | output of runs |
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

IDs are per agent listing; `dup_key` groups the same property listed by several agents.

## Troubleshooting

| Symptom | Check |
|---|---|
| `another suumo process is running` | a run is in progress (`uv run python -m suumo status` shows it); the lock frees when it exits |
| Scheduled run fails with `Operation not permitted` | the repo is under Documents/Desktop/Downloads; move it to `~/suumo` |
| `parse failures: N` in `status` | fix the parser, then `reparse` (the pages are already archived) |
| An area shows `incomplete` or `suspect` | transient on SUUMO's side; no listings are removed for it; the next run retries |
| `ERROR area page: ...` in the log | the area page looked broken (no areas, or far fewer listings than held); nothing was reconciled; tried again at the end of the crawl, and the next day |
| The site didn't update | Actions tab on GitHub: the `Site` workflow runs tests first; a failing test blocks the deploy |
| 「地図に表示できない物件が…」 on the map | towns not geocoded yet (`uv run python -m suumo geocode`), or found by neither 位置参照情報 nor the address search |

## Tests

`uv run pytest -q` (offline; parsers are also checked against real pages in `tests/fixtures/`; the site's search
rules in `filter.js` are tested with Node). `uv run ruff check suumo tests` for lint.
