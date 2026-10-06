# SUUMO for-sale tracker

Crawls SUUMO for-sale listings in the areas listed in `scope.toml` (all of Tokyo), fetches each listing's own
page once for the full spec, tracks listings as they appear, change price and disappear, exports the data as
JSONL to `data/` (committed to git), and publishes a search site on GitHub Pages:
**https://huangwaylon.github.io/suumo/**

## The site

Plain HTML/JS, phone first, in Japanese, rebuilt by GitHub Actions on every push to `main`.

- **Search box**: station, town or building name (several words narrow down).
- **Condition chips**: 種別, 価格, 間取り, エリア, 駅・徒歩, 広さ・築年数 (広さ, 土地面積, 築年数, 新耐震), こだわり
  (SUUMO tags, 所有権のみ, 建築条件なし, 新着のみ, 値下げのみ). Each choice shows how many listings it gives.
- **List or map**: cards with photo, price, layout, size, station, age and flags; the map (地理院タイル) groups
  listings by town (丁目) with counts. Seven sort orders.
- **Listing**: large photo, the full record, price per m²/坪, price history, other agents listing the same
  property, a 「SUUMOで見る」 link, and its town on a small map.
- **Favorites** (☆) are kept in the browser. The conditions live in the URL, so a search or a listing can be shared
  as a link.

The same property listed by several agents (same `dup_key`) counts once. 新着 = first seen in the last 7 days
(not counting the first crawl of an area), 値下げ = a price drop in the last 30 days.

The site is public (GitHub Pages) but marked `noindex`. Photos are loaded from SUUMO's image server.

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

### 5. Daily schedule

```sh
sed "s#__SUUMO_DIR__#$PWD#g" local.suumo.plist > ~/Library/LaunchAgents/local.suumo.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.suumo.plist
launchctl kickstart gui/$(id -u)/local.suumo     # run once now to check
tail -f logs/run.log
```

The job runs `python -m suumo run --push` daily at 04:00 (on wake if the Mac was asleep): ~20 minutes of search
results, listing pages of new and changed listings, geocoding of new towns, then a commit of `data/` and `geo/`
and a push, which rebuilds the site. `--push` needs a git remote that pushes without a prompt (an SSH key in the
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
| `uv run python -m suumo geocode` | look up coordinates for towns not in `geo/towns.json` yet |
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
2. **Changes**: an area's first complete crawl is a silent baseline. After that, new IDs, price changes and
   relistings become events. A listing missing from 2 consecutive complete crawls is removed. If an area
   loses more than 30% of at least 20 listings at once, it is marked `suspect` and nothing is removed.
3. **Listing pages**: queue order is new > changed > backfill, until the budget runs out. Pages are archived
   before parsing; a parse failure is logged and the page can be re-parsed later.
4. **Cleanup**: listings removed over 30 days ago are deleted (row, archived page); search-result snapshots are
   kept 14 days; event and run rows 90 days.
5. **Export**: `data/` is rewritten deterministically (an unchanged day is an empty git diff) and the run's
   events go to `data/events/<run_id>.json` (the site's 新着, 値下げ and price history come from them).
6. **Geocoding**: towns of new listings are looked up (国土地理院 address search, ≤30 min per run).

## Files

| Path | In git | Contents |
|---|---|---|
| `scope.toml` | yes | what to crawl |
| `data/<pref>/<type>/<area_code>.jsonl` | yes | active listings of one type in one municipality, one JSON object per line, sorted by id |
| `data/<pref>/removed/<type>.jsonl` | yes | listings removed in the last 30 days |
| `data/<pref>/areas.json` | yes | area code → name |
| `data/events/<run_id>.json` | yes | that run's events |
| `geo/towns.json` | yes | town (丁目) → [lat, lng], or null when not found |
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

`uv run pytest -q` (offline; parsers are also checked against real pages in `tests/fixtures/`; the site's
`filter.js` is checked against the Python catalog with Node). `uv run ruff check suumo tests` for lint.
