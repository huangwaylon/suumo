# Search bot: design

An interactive Discord bot for browsing the tracked listings. Users are non-technical and Japanese-speaking:
everything is buttons and dropdowns, no typed commands, no text input.

> **As built.** Changed after the design review (rules now in CLAUDE.md "Search bot rules"):
> stations are identified by name; 新着 comes from `new`/`relisted` events, not `first_seen`; land listed under
> both `new_house` and `land` is kept once as land; building conditions exclude land only when 土地 wasn't
> chosen; 新耐震 and 建築条件なし were added; filtering is a bitset index (`Snapshot.mask`) checked against a
> per-listing reference (`Snapshot.matches`); alerts treat page-only conditions as met until the page is
> fetched, send one message per person per check, and save progress after each person; the channel fallback
> for closed DMs notifies normally; sessions idle out after 6 h instead of 14 min; the detail photo uses
> SUUMO's resize URL at 600×450; the menu has no 30 s on-tap reload (a 60 s watcher reloads data); there is no
> test DM when saving: a refused DM is noted on the 🔔 screen after the first alert.

## Goals

- Find listings by the conditions house hunters use, in a few taps, on a phone.
- See a listing's full details (photo, spec, price history, other agents listing it) without leaving Discord.
- Keep a shortlist (お気に入り) and get told when a shortlisted listing changes price or disappears.
- Save a search and get a DM when a new listing matches it, so a wide scope doesn't depend on the channel feed.

Non-goals: typed search, maps, editing scope from Discord, multi-machine failover.

## Architecture

- A separate long-running process: `python -m suumo bot` (launchd `local.suumo-bot.plist`, KeepAlive). The daily
  crawl stays as it is; the two share nothing but files.
- **Reads `data/` only** (the exported JSONL, `areas.json`, `events/*.json`), never `state.db`. The bot never
  blocks or is blocked by a crawl, and `data/` is already the stable, documented record format.
- `catalog.py` (pure, no Discord): loads `data/`, keeps an in-memory index, answers queries. Reloads when any
  `data/<pref>/*.jsonl` mtime changes (checked at most every 30 s, on interaction and by a 60 s watcher).
  Loading runs in a thread and swaps the index atomically.
- `bot/` package (Discord): `app.py` (client lifecycle, menu, watcher, alerts), `ui.py` (views), `text.py`
  (every Japanese string and formatter), `store.py` (user data + persistence in `#state`).
- Imports: `cli` → `bot.app` → `bot.ui` → `bot.text`/`bot.store`/`catalog` → `notify` (shared formatters
  `man`, `TYPE_JA`) / `export`.
- Single instance per machine: flock on `bot.lock`. A second copy exits 0.

### Performance

- Index: one small `Item` per listing (`__slots__`): type, area, price min/max, rooms set, size, land size,
  built year, walk minutes per station, station keys, feature set, leasehold flag, first_seen, dup_key, plus the
  parsed record. Full Tokyo is ~50k listings; a linear filter over slotted objects is ~20 ms, well inside
  Discord's 3 s interaction deadline. No database, no per-query JSON parsing.
- Facets (area/line/station/feature counts) are computed once per load.
- Every interaction is answered by editing the same ephemeral message (`response.edit_message`), one API call.

## Data semantics

| Filter | Rule |
|---|---|
| 種別 | any of the chosen types; none chosen = all |
| エリア | any of the chosen area codes |
| 駅 | any of the listing's stations (up to 3) is a chosen station; line names are normalized (`小田急線（新宿～相模大野）` → `小田急線`); bus lines are not offered |
| 徒歩 | some station (chosen ones, if any) within N minutes on foot; bus access doesn't count |
| 価格 | the listing's lowest price (`price`) ≤ max and its highest (`price_max` or `price`) ≥ min; listings without a price only match when no price filter is set |
| 間取り | room counts parsed from `layout` (`3LDK+S` → 3, `ワンルーム` → 1, `2LDK・4LDK` → {2,4}); buckets 1 / 2 / 3 / 4以上; land never matches |
| 広さ | `floor_m2` (condos) or `building_m2` (houses), max of range ≥ N |
| 土地 | `land_m2` (houses, land), max of range ≥ N |
| 築年数 | new types count as 0 years; used: years since `built`; unknown doesn't match when set |
| こだわり | all chosen SUUMO feature tags present (only listings whose page has been fetched have tags) |
| 所有権のみ | drops listings whose `land_rights` is a leasehold category; unknown kept |
| 新着のみ | `first_seen` within 7 days |
| 値下げのみ | a price drop in the last 30 days (from `data/events/`) |

- **Duplicates:** listings sharing a `dup_key` are shown once (the lowest price, then lowest id), with
  「ほかN社も掲載」. The count shown is properties, not listings.
- **Sorts:** 新着順 (default), 価格が安い順, 価格が高い順, 広い順, 駅が近い順, 築年数が浅い順, ㎡単価が安い順.
- **Price history** comes from `price_changed` events in `data/events/*.json` (git keeps all of them).

## Screens

All ephemeral (only the tapper sees them), edited in place. One persistent menu message in `#suumo`.

**Menu** (persistent, fixed custom_ids `suumo:*`, kept as the newest message in the channel):
`🔎 物件をさがす` `🆕 新着` `💴 値下げ` / `⭐ お気に入り` `🔔 保存した条件` `❓ 使い方`.

**Search panel** (`🔎`), opens with the user's last conditions:
- content: the conditions in one line each, and 該当 N件.
- row 0: 種別 (multi select) · row 1: 予算 上限 (select) · row 2: 間取り (multi select)
- row 3: `📍 エリア` `🚉 駅・徒歩` `📐 広さ・築年数` `✨ こだわり`
- row 4: `🔎 N件を見る` `💾 保存して通知` `🧹 リセット`

Sub-screens (each ends with `↩️ 戻る`): エリア (multi select, 25 per page with ◀▶ when needed), 駅・徒歩
(路線 → 駅 multi select, 徒歩 select), 広さ・築年数 (予算 下限, 広さ, 土地, 築年数), こだわり (top-25 tags multi
select; 所有権のみ / 新着のみ / 値下げのみ toggle buttons).

**Results**: 5 listings per page as embeds with the listing photo as thumbnail; the embed title (price, layout,
size) links to SUUMO. row 0: 詳しく見る (select of the 5), row 1: 並び替え, row 2: `◀` `n/N` `▶` `↩️ 条件`.

**Detail**: one embed: large photo, title linked to SUUMO, fields for price (+ ㎡単価/坪単価, monthly fees), layout,
sizes, built, floor, structure, stations, land rights, zoning, features, agent, deal type, handover, first seen,
price history, other agents. Buttons: `⭐ お気に入りに追加/外す`, `◀ 前`, `次 ▶`, `↩️ 一覧`.

**お気に入り**: the results screen over the user's starred listings, including ones that have since been removed
(shown as 掲載終了, from `removed.jsonl`), with the price change since starring.

**保存した条件**: select one of the user's saved searches (max 5) → `🔎 この条件でさがす` `🗑️ 削除`; toggle
`⭐ お気に入りの変化を通知 ON/OFF`.

Invalid or empty states get a short private hint and keep the screen open (0件 → suggests loosening).
Pickers time out after 14 min (interaction tokens last 15) and say so. A tap on a picker from before a restart
gets 「このメニューは古くなりました」 instead of Discord's generic failure: ephemeral custom_ids are
`s:<session>:<name>` and an `on_interaction` hook answers unknown sessions.

## Alerts

- After each crawl, the export writes `data/events/<run_id>.json`. The watcher sees run files newer than the
  last processed run, reloads the catalog, and for each user:
  - saved searches: `new` and `relisted` events whose listing matches; `price_changed` drops that now match.
  - favorites (if enabled): any `price_changed`, `removed`, `relisted` of a starred listing.
- One DM per user per run, same line format as the channel feed, grouped by saved search. Duplicate listings
  (same `dup_key`) are collapsed.
- `last_run` per user in state; advanced after that user's DM is accepted, so a failure retries that user next
  time and nobody gets a run twice. On first start, `last_run` = the newest existing run (no backlog).
- If a DM is refused (privacy setting), the alert is posted in `#suumo` with a mention, silent, and the save
  screen warns about DMs when the search is saved (a test DM is attempted then).

## State

- `#state` channel (`DISCORD_STATE_CHANNEL_ID`): `state.json` attachment on the bot's newest message there, text
  is a short Japanese summary; `bot_state.backup.json` locally on every save, used if the message is gone
  (the gym bot's pattern).
- Shape (`version: 1`): `{users: {uid: {name, last_query, searches: [{id, query, created}], favorites:
  [{type, id, price, added}], notify_favorites, last_run}}}`.
- Saved lazily: user actions mark it dirty; a save runs after the response is sent (never inside the 3 s window)
  and is coalesced (at most one save in flight, re-run if dirty again).

## Channel hygiene

- The crawl's daily posts arrive from the same bot user via REST. The bot sees them (`on_message`, own user,
  not the menu) and reposts the menu silently below them, debounced 5 s so a multi-message post moves it once.
- At startup the bot deletes its own old menu messages (identified by a `suumo:` custom_id), never the feed.

## Testing

- `test_catalog.py`: every filter rule above, dedupe, sorts, facets, reload on change, events/price history.
- `test_bot_ui.py`: views with fake interactions (as in the gym bot): panel counts, sub-screens, paging,
  detail, favorites toggle, save/delete searches, expired sessions.
- `test_bot_alerts.py`: which events alert whom, per-user `last_run`, DM fallback, first-start no backlog.
- `test_bot_store.py`: state round trip, version check, backup fallback.
