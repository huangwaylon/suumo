# SUUMO for-sale tracker

Crawls SUUMO for-sale listings in the areas listed in `scope.toml` (all of Tokyo), fetches each listing's own
page once for the full spec, tracks listings as they appear, change price and disappear, exports the data as
JSONL to `data/` (committed to git), and publishes a search site on GitHub Pages:
**https://huangwaylon.github.io/suumo/**

## The site

Plain HTML/CSS/JS (no framework, no build step), in Japanese, rebuilt by GitHub Actions on every push to `main`.

| Screen | Layout |
|---|---|
| Phone | search bar with filter and favorites buttons; full-screen filter sheet; list or full-screen map (floating switch); listing as a full page with a fixed 「お気に入り / SUUMOで見る」 bar |
| Tablet | tile grid; filters and listing as side sheets |
| Desktop (≥1280px) | filters rail · results · live map side by side; listing as a drawer |

- **Search**: station, town or building name; several words narrow down; hiragana, katakana, half-width kana and
  romaji match (station readings from Wikidata). Typing a station or ward name offers it as a filter in one tap.
- **Filters** (one panel, every choice shows its count): 種別, 価格, 間取り（以上: 2LDK〜…）, 広さ, 土地, 築年数,
  新耐震, 駅 (search by name) and 徒歩, エリア (by prefecture and 区部/市部/町村), こだわり (所有権のみ, 建築条件なし,
  新着, 値下げ, SUUMO's tags). Active conditions show as chips under the count: the label opens the filters at that
  condition, × removes it. The last search comes back when the site is reopened; browser back closes the filter
  sheet, settings, the map view and the listing.
- **Results**: cards with photo, price, layout, size, age, nearest station, town; 8 sort orders (incl.
  値下げ率が大きい順); more load as you scroll. The map follows the results (pins by town, 丁目 level). No results →
  the conditions to drop, with counts.
- **What's new**: 新着 tags say when (3時間前, 10/6), 再掲載 marks relisted homes, 値下げ tags show the cut (−8%).
  Above the results, one-tap 新着 / 値下げ / 前回の訪問以降 chips with counts under the current conditions (a visit
  is a new data version, remembered in the browser); with 新着 or 前回の訪問以降 on, the list is grouped by day.
  A property another agent already listed doesn't count as new.
- **Saved searches** (this browser): the star saves the current conditions; each shows how many listings are new
  or cheaper since it was last opened (+N), and opening it shows those first.
- **Map area view**: 「この範囲の物件を見る」 on the map lists only what's visible (under the current conditions)
  and follows panning and zooming; desktop shows conditions | map | list, phones the map above the list.
- **Listing**: photo, key facts, 交通, 費用, 建物, 土地・法規, 特徴, 価格の推移, other agents' listings, its town on a
  map, a link to SUUMO.
- **Settings** (gear icon): language 日本語 (default) or English, and theme システム / ライト / ダーク, saved in the
  browser. In English the interface, units, prices (¥49.9M) and fixed values (land rights, deal type) are
  translated, and so are ward and station names and common tags; towns, building names and free text stay as
  SUUMO writes them.
- **Favorites** are one shared list (`saved.json`): the heart opens a pre-filled GitHub issue
  (`save used_condo:123`); submitting it (a GitHub account is needed) runs the Saved workflow, which updates the
  list and rebuilds the site in a few minutes. Saved listings are kept after they leave SUUMO, marked 掲載終了.
  「比較する」 shows them side by side (price, size, age, station, monthly costs, floor, land rights).
  Conditions live in the URL, so any search or listing can be shared.

One property listed by several agents (same `dup_key`: building/address, size and price) appears once, with
「ほかN社も掲載」. 新着 = new or relisted in the last 7 days (the first crawl of an area records its listings
silently), 値下げ = a price drop in the last 30 days. Names that are agents' ad copy aren't shown.

The site is public (GitHub Pages) but marked `noindex`. Photos load from SUUMO's image server; Leaflet loads from
unpkg (with integrity hashes) only when a map is shown.

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
all of Tokyo). To carry them over, stop the scheduled job on the old Mac
(`launchctl bootout gui/$(id -u)/local.suumo`), then:

```sh
rsync -a 'OLD_MAC:suumo/state.db*' OLD_MAC:suumo/archive ~/suumo/    # OLD_MAC: its ssh host name
```

### 4. First run (only without the old state)

A whole prefecture needs every listing page fetched once: ~64k pages for Tokyo, about a day at 1.0 s per
request. Run it in a Terminal on a Mac that stays awake and on power (`caffeinate -i` keeps it from sleeping):

```sh
uv run python -m suumo --delay 1.0 run --budget 0                    # search results only, ~20 min: all listings known
caffeinate -i uv run python -m suumo --delay 1.0 run --no-crawl --budget 40h 2>&1 | tee -a logs/backfill.log
uv run python -m suumo geocode                                       # map coordinates for every town, a few hours
```

Follow it with `tail -f logs/backfill.log` (a progress line every 200 pages: done/total, s/page, ETA, current
delay) or `uv run python -m suumo status` from another Terminal (shows the current run while it holds the lock).
`data/` is exported every hour. Stopping it (Ctrl-C) is safe: the next run continues the queue. If SUUMO pushes
back, the delay rises on its own (up to 10 s) and comes back down; if it's unreachable, the run pauses 10 minutes
at a time without giving up on those listings.

### 5. Hourly schedule

```sh
sed "s#__SUUMO_DIR__#$PWD#g" local.suumo.plist > ~/Library/LaunchAgents/local.suumo.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.suumo.plist
launchctl kickstart gui/$(id -u)/local.suumo     # run once now to check
tail -f logs/run.log
```

The job runs `python -m suumo run --budget 45m --push` every hour at :05 (once on wake if the Mac was asleep;
a run still going makes the next one exit): it pulls (for `saved.json`), crawls all search results (~800
requests, ~17 minutes), fetches the listing pages of listings not stored yet, geocodes new towns, then commits
`data/` and `geo/` and pushes, which rebuilds the site. `--push` needs a git remote that pushes without a prompt (an SSH key in the
agent/Keychain); a failed push is logged and retried next run. To stop it:
`launchctl bootout gui/$(id -u)/local.suumo`.

## Commands

| Command | What it does |
|---|---|
| `uv run python -m suumo run` | crawl search results, fetch listing pages for up to `--budget` (default `3h`), clean up, export `data/`, geocode new towns |
| `run --budget 30m` / `--budget 0` | smaller / no listing-page budget; the queue continues next run |
| `run --no-crawl` | only work the listing-page queue |
| `run --commit` / `--push` | commit `data/` and `geo/` after the run / commit and push (rebuilds the site) |
| `uv run python -m suumo status` | per pref/type: active listings, % with listing-page details, queue, area states, the current run's progress |
| `uv run python -m suumo prune [--yes]` | delete data no longer in `scope.toml` (dry run without `--yes`) |
| `uv run python -m suumo reparse` | re-run the parsers over `archive/` and re-export (no requests) |
| `uv run python -m suumo geocode` | look up coordinates for towns not in `geo/towns.json` yet, and station names (kana, English) for prefectures not in `geo/stations.json` |
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
   then marked `incomplete`.
2. **Changes**: the results are diffed against `state.db`. An area's first complete crawl is a silent baseline.
   After that, new IDs and price changes become events. A listing missing from 2 consecutive complete crawls
   is deleted (row, archived page; a `removed` event), unless it's on the saved list: then it's kept as ended
   and still shown on the site in お気に入り. If an area loses more than 30% of at least 20 listings at once,
   it is marked `suspect` and nothing is removed.
3. **Listing pages**: fetched once, for listings not stored yet (new first, then backfill), until the budget
   runs out. Search-result fields (price etc.) are refreshed every crawl. Pages are archived before parsing; a
   parse failure is logged and the page can be re-parsed later.
4. **Cleanup**: ended listings no longer saved are deleted; search-result snapshots are kept 14 days; event and
   run rows 90 days.
5. **Export**: `data/` is rewritten deterministically (an unchanged day is an empty git diff) and the run's
   events go to `data/events/<run_id>.json` (the site's 新着, 値下げ and price history come from them).
6. **Geocoding**: towns of new listings are looked up (国土地理院 address search, ≤30 min per run).

## Files

| Path | In git | Contents |
|---|---|---|
| `scope.toml` | yes | what to crawl |
| `data/<pref>/<type>/<area_code>.jsonl` | yes | active listings of one type in one municipality, one JSON object per line, sorted by id |
| `data/<pref>/removed/<type>.jsonl` | yes | ended listings kept because they're saved |
| `saved.json` | yes | the shared saved list (`type:id` keys), changed only by the Saved workflow |
| `data/<pref>/areas.json` | yes | area code → name |
| `data/events/<run_id>.json` | yes | that run's events |
| `geo/towns.json` | yes | town (丁目) → [lat, lng], or null when not found |
| `geo/stations.json` | yes | station → [kana, English] from Wikidata (CC0); `_prefs` lists the prefectures fetched. Delete to refresh |
| `site/` | yes | the site's source (index.html, app.js, filter.js, style.css) |
| `.github/workflows/pages.yml` | yes | tests, builds and deploys the site on every push |
| `state.db` | no | SQLite: listings and lifecycle, areas, queue, events, runs |
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
| Access | `stations [{line, name, walk, bus, bus_stop}]`, up to 3 |
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
| The site didn't update | Actions tab on GitHub: the `Site` workflow runs tests first; a failing test blocks the deploy |
| 「地図に表示できない物件が…」 on the map | towns not geocoded yet (`uv run python -m suumo geocode`), or not found by the address search |

## Tests

`uv run pytest -q` (offline; parsers are also checked against real pages in `tests/fixtures/`; the site's search
rules in `filter.js` are tested with Node). `uv run ruff check suumo tests` for lint.
