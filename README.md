# SUUMO for-sale tracker

Crawls SUUMO for-sale listings for the areas in `scope.toml`, fetches each listing's own page once for the
full spec (fees, floor, structure, stations, features, land rights…), tracks listings as they appear, change
price and disappear, exports git-friendly JSONL, and posts changes to Discord.

```sh
uv run python -m suumo run                 # daily job: crawl, listing pages (3h budget), clean up, export, notify
uv run python -m suumo run --budget 30m    # smaller listing-page budget (the queue resumes next run)
uv run python -m suumo status              # coverage, queue size, out-of-scope data
uv run python -m suumo prune [--yes]       # delete data no longer in scope.toml
uv run python -m suumo reparse             # re-run parsers over the raw archive (no requests)
uv run python -m suumo notify              # post unposted events to Discord
uv run pytest -q                           # offline tests
```

## How a run works

1. **Search results.** For every pref/type/area in scope: fetch all result pages (100 per page, robots.txt-
   allowed paths only, 1.5–2.25 s between requests), save them to the archive, and check the parsed count
   against SUUMO's count. Short areas get a second pass; still-short areas are `incomplete`.
2. **Reconcile each area** against `state.db`:
   - an area's first complete crawl is a silent **baseline** (no events; its listings queue as backfill);
   - later, unknown IDs are `new` events and jump the queue;
   - price changes are `price_changed` events; changed listings re-queue their page;
   - a listing missing from **2 consecutive complete crawls** is `removed`; seen again it is `relisted`;
   - an area that loses more than 30% of at least 20 listings at once is `suspect`: nothing is removed;
   - an area that drops off SUUMO's area page is reconciled as empty (same rules).
3. **Listing pages.** Work the queue (new > changed > backfill) until it's empty or the budget is spent.
   Pages are archived before parsing, so a parser failure is logged, not fatal; fix it and `reparse`.
4. **Clean up.** Listings removed more than 30 days ago are purged (DB row, queue entry, archived page);
   search-result snapshots older than 14 days are deleted. Git history keeps everything.
5. **Export** `data/` and **notify** Discord.

## Scope

```toml
[[scope]]
pref  = "chiba"                       # SUUMO slug: tokyo, chiba, kanagawa, saitama, ibaraki, ...
types = ["used_condo", "used_house"]  # optional, default all five
areas = ["12218"]                     # optional JIS municipality codes, default whole prefecture
```

Adding an entry needs no code: the next run baselines it and queues its listing pages behind new listings
elsewhere. Removing one stops crawling it; `prune --yes` deletes its data.

## Files

| Path | In git | What |
|---|---|---|
| `scope.toml` | yes | what to crawl |
| `data/<pref>/<type>.jsonl` | yes | active listings, one per line, sorted by id, fixed key order |
| `data/<pref>/removed.jsonl` | yes | removed in the last 30 days |
| `data/<pref>/areas.json` | yes | area code -> name |
| `data/events/<run_id>.json` | yes | events of a run (new / price_changed / removed / relisted) |
| `state.db` | no | SQLite: listings + lifecycle, areas, queue, events, runs |
| `archive/` | no | raw HTML: daily search-result snapshots, latest copy of each listing page |
| `.env` | no | `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID` (see `.env.example`) |

Exports are deterministic and leave out fields that change on their own (last seen, miss count, SUUMO's
info/next-update dates), so a day without changes is an empty git diff.

## Types

| key | SUUMO section | one record = |
|---|---|---|
| `used_condo` | 中古マンション | one unit |
| `new_house` | 新築一戸建て | one listing, may cover several plots (`price_max`, `*_max`); some are land with a build condition (`price_excludes_building`) |
| `used_house` | 中古一戸建て | one house |
| `land` | 土地 | one listing, may cover several plots |
| `new_condo` | 新築マンション | one development |

## Record fields

Japanese labels are kept as SUUMO writes them (fixed vocabularies); numbers are parsed with the unit in the
name. Absent = not stated on SUUMO.

- **Identity:** `id`, `type`, `area_code`, `area`, `town` (address after the municipality), `address`, `name`,
  `title`, `url`, `image`, `agent`, `first_seen`, `removed_at`, `has_detail`, `dup_key`
- **Money (yen):** `price`, `price_max`, `price_excludes_building`, `mgmt_fee`, `mgmt_form`, `repair_fee`,
  `repair_fund_once`, `other_monthly` (sum of per-month extras), `other_fees` (text), `parking`
  (`status`, `fee_min`, `fee_max`)
- **Access:** `stations`: up to 3 of `{line, name, walk, bus}` (minutes)
- **Size (m²):** `layout`, `floor_m2`, `balcony_m2`, `land_m2`, `building_m2`, `site_m2`, `*_max` for ranges,
  `road` (`dir`, `width_m`, `private_m2`, `text`)
- **Building:** `built` (`YYYY-MM`), `built_planned`, `floor` (negative = basement), `floors_above`,
  `floors_below`, `structure` (RC, SRC, 木造, 鉄骨…), `total_units`, `units_for_sale`, `direction`, `builder`,
  `reform` (`date`, `text`), `energy`, `insulation`
- **Legal:** `land_rights` (所有権 / 地上権 / 定期借地権 / 普通借地権 / 旧法借地権 / 借地権),
  `land_rights_note`, `zoning`, `coverage_pct`, `far_pct`, `land_status`, `build_condition`, `land_category`,
  `restrictions`, `utilities`
- **Other:** `features` (SUUMO's 特徴ピックアップ tags, e.g. ペット相談, 角住戸, 南向き), `deal_type`
  (仲介 / 売主 / 代理), `handover`, `sale_schedule`, `top_price_band`

`dup_key` groups the same property listed by several agents (same type, building or address, size and
price); IDs are per agent listing.
