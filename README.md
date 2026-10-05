# SUUMO for-sale tracker

Crawls SUUMO for-sale listings in the areas listed in `scope.toml`, fetches each listing's own page once for the
full spec, tracks listings as they appear, change price and disappear, exports the data as JSONL to `data/`
(committed to git), and posts the changes to a Discord channel in Japanese. A search bot in the same channel
lets people browse the listings with buttons, keep favorites and get DMs for saved searches.

## Setting up on a new Mac

### 1. Tools

```sh
xcode-select --install                             # git (skip if `git --version` works)
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv: Python and the libraries
```

Reopen Terminal, then check `uv --version`.

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

### 3. `.env`

```sh
cp .env.example .env && chmod 600 .env && open -e .env
```

| Key | Value |
|---|---|
| `DISCORD_TOKEN` | Developer Portal → application `suumo` → Bot → Reset Token |
| `DISCORD_CHANNEL_ID` | the #suumo channel (right-click → Copy Channel ID, with Developer Mode on) |
| `DISCORD_STATE_CHANNEL_ID` | the #state channel, where the search bot keeps its data (favorites, saved searches) |

Without `DISCORD_TOKEN`/`DISCORD_CHANNEL_ID`, runs print a preview of the messages instead of posting.

### 4. State from the old machine (recommended)

`state.db` (listing history, queue) and `archive/` (raw pages) are not in git. Without them the first run starts
over: every area is baselined again and every listing page is fetched again (about 36 hours for all of Tokyo).
To carry them over, stop the scheduled job on the old Mac (`launchctl bootout gui/$(id -u)/local.suumo`), then:

```sh
rsync -a 'OLD_MAC:suumo/state.db*' OLD_MAC:suumo/archive ~/suumo/    # OLD_MAC: its ssh host name
```

### 5. First run

```sh
uv run python -m suumo run --budget 10m
uv run python -m suumo status
```

### 6. Daily schedule

```sh
sed "s#__SUUMO_DIR__#$PWD#g" local.suumo.plist > ~/Library/LaunchAgents/local.suumo.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.suumo.plist
launchctl kickstart gui/$(id -u)/local.suumo     # run once now to check
tail -f logs/run.log
```

The job runs `python -m suumo run --push` daily at 04:00 (on wake if the Mac was asleep). `--push` needs a git
remote that pushes without a prompt (an SSH key in the agent/Keychain); a failed push is logged and retried next
run. To stop it: `launchctl bootout gui/$(id -u)/local.suumo`.

### 7. Search bot

The bot is a second, always-running job beside the daily crawl. It reads `data/` and needs the three `.env` keys.

In Discord (once): the bot needs **View Channel, Send Messages, Embed Links, Read Message History** in #suumo and
**View Channel, Send Messages, Attach Files, Read Message History** in #state. No privileged intents are needed.
Optional but recommended: deny members **Send Messages** in #suumo so the menu stays at the bottom; hide #state
from members.

```sh
sed "s#__SUUMO_DIR__#$PWD#g" local.suumo-bot.plist > ~/Library/LaunchAgents/local.suumo-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.suumo-bot.plist
tail -f logs/bot.log                               # "ready as suumo#…"; the menu appears in #suumo
```

After a code update: `launchctl kickstart -k gui/$(id -u)/local.suumo-bot`. To stop it:
`launchctl bootout gui/$(id -u)/local.suumo-bot`. When moving to a new Mac, stop it on the old one first: two
copies would both answer every button.

## Commands

| Command | What it does |
|---|---|
| `uv run python -m suumo run` | crawl search results, fetch listing pages for up to `--budget` (default `3h`), clean up, export `data/`, post to Discord |
| `run --budget 30m` / `--budget 0` | smaller / no listing-page budget; the queue continues next run |
| `run --no-crawl` | only work the listing-page queue |
| `run --commit` / `--push` | commit `data/` after the run / commit and push |
| `run --no-notify` | don't post to Discord |
| `uv run python -m suumo status` | per pref/type: active listings, % with listing-page details, queue, area states |
| `uv run python -m suumo prune [--yes]` | delete data no longer in `scope.toml` (dry run without `--yes`) |
| `uv run python -m suumo reparse` | re-run the parsers over `archive/` and re-export (no requests) |
| `uv run python -m suumo notify` | post pending events to Discord |
| `uv run python -m suumo bot` | run the search bot in the foreground (what the launchd job runs) |

Only one writing command runs at a time (`state.db.lock`); a second one exits with a message.

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

1. **Search results**: every page of every area in scope (100 listings per page, 1.5–2.25 s between requests),
   saved to `archive/`, with the parsed count checked against SUUMO's count. Short areas are retried once,
   then marked `incomplete`.
2. **Changes**: an area's first complete crawl is a silent baseline. After that, new IDs, price changes and
   relistings become events. A listing missing from 2 consecutive complete crawls is removed. If an area
   loses more than 30% of at least 20 listings at once, it is marked `suspect` and nothing is removed.
3. **Listing pages**: queue order is new > changed > backfill, until the budget runs out. Pages are archived
   before parsing; a parse failure is logged and the page can be re-parsed later.
4. **Cleanup**: listings removed over 30 days ago are deleted (row, archived page); search-result snapshots are
   kept 14 days; event and run rows 90 days.
5. **Export and notify**: `data/` is rewritten deterministically (an unchanged day is an empty git diff), and
   pending events are posted to Discord: one line per listing on a quiet day (up to 20 events), otherwise a
   summary with counts per type and per area (the listings themselves are in the bot's 🆕 / 💴). Events not
   posted within 48 hours are skipped.

## Search bot

Everything is buttons and dropdowns; nothing is typed. The menu at the bottom of #suumo has:

| Button | Opens (only the person who tapped sees it) |
|---|---|
| 🔎 物件をさがす | the search panel: 種別, 予算, 間取り, and buttons for エリア, 駅・徒歩, 広さ・築年数など (予算の下限, 広さ, 土地面積, 築年数, 新耐震), こだわり (SUUMO tags, 所有権のみ, 建築条件なし, 新着のみ, 値下げのみ). The count updates on every choice; 「N件を見る」 lists them |
| 🆕 新着 / 💴 値下げ | listings that were new in the last 7 days / dropped in price in the last 30 days |
| ⭐ お気に入り | listings starred from their detail screen, with the price change since starring |
| 🔔 保存した条件 | saved searches (5 per person) and the favorites-notification switch |
| ❓ 使い方 | help |

Results show 5 listings per page with photos; 「詳しく見る」 opens the full record (photo, price per m²/坪, fees,
stations, land rights, zoning, tags, price history, other agents listing the same property, a SUUMO link).
The same property listed by several agents (same `dup_key`) counts once. A condition that doesn't apply to a
type (間取り for 土地) doesn't exclude land the person explicitly chose.

After each crawl the bot DMs people whose saved searches have new, relisted or price-dropped matches, and whose
favorites changed price or were removed. If someone's DMs are closed it mentions them in #suumo instead.

## Files

| Path | In git | Contents |
|---|---|---|
| `scope.toml` | yes | what to crawl |
| `data/<pref>/<type>/<area_code>.jsonl` | yes | active listings of one type in one municipality, one JSON object per line, sorted by id |
| `data/<pref>/removed/<type>.jsonl` | yes | listings removed in the last 30 days |
| `data/<pref>/areas.json` | yes | area code → name |
| `data/events/<run_id>.json` | yes | that run's events |
| `state.db` | no | SQLite: listings and lifecycle, areas, queue, events, runs |
| `archive/` | no | raw HTML: search-result snapshots by day, latest copy of each listing page |
| `logs/run.log` | no | output of scheduled runs |
| `logs/bot.log` | no | output of the search bot |
| `bot_state.backup.json` | no | local copy of the bot's data; the copy in #state is the source of truth |
| `.env` | no | Discord settings |

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
| `another suumo process is running` | a run is in progress (`ps aux \| grep suumo`); the lock frees when it exits |
| Scheduled run fails with `Operation not permitted` | the repo is under Documents/Desktop/Downloads; move it to `~/suumo` |
| `discord: send failed` | token/channel in `.env`; the bot must be in the server with View Channel and Send Messages |
| `parse failures: N` in `status` | fix the parser, then `reparse` (the pages are already archived) |
| An area shows `incomplete` or `suspect` | transient on SUUMO's side; no listings are removed for it; the next run retries |
| Buttons say 「インタラクションに失敗しました」 | the bot isn't running: `tail logs/bot.log`, `launchctl kickstart -k gui/$(id -u)/local.suumo-bot` |
| 「この画面は古くなりました」 | the bot restarted since that screen was opened; open it again from the menu |
| `bot: another copy is already running` | expected when launchd starts a second copy; only one runs (`bot.lock`) |
| `bot state version … refusing` | the #state data is from a newer version of the code; update the code |

## Tests

`uv run pytest -q` (offline; parsers are also checked against real pages in `tests/fixtures/`).
`uv run ruff check suumo tests` for lint.
