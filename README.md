# SUUMO for-sale crawler

Crawls SUUMO for-sale listings for a prefecture (Tokyo first) area by area, validates coverage, and
keeps a JSON state store that a Discord bot can load and diff.

```sh
uv run python -m suumo.crawl                                   # all types, all of Tokyo (~30 min)
uv run python -m suumo.crawl --types used_house --areas 13112  # one type, one ward
```

Uses only robots.txt-allowed listing paths (`/ms/chuko/tokyo/sc_setagaya/?pc=100&page=N`), one
request at a time with a 1.5-2.25 s delay. Raw HTML is cached gzipped under `.cache/html/<date>/`
so same-day re-runs and parser fixes don't re-download.

## Types

| key | SUUMO section | one record = |
|---|---|---|
| `used_condo` | 中古マンション | one unit |
| `new_house` | 新築一戸建て | one listing, may cover several plots (price/area ranges) |
| `used_house` | 中古一戸建て | one house |
| `land` | 土地 | one listing, may cover several plots |
| `new_condo` | 新築マンション | one development (project), not a unit |

## Data layout (`data/tokyo/`)

- `listings/<type>.json` and `.json.gz`: current state.
  `{"meta": {type, updated_at, active, total_records}, "listings": {"<id>": record}}`
- `changes/<run_id>.json`: what changed this run per type, for the bot to post:
  `new` (full records), `price_changed` (`id, old, new, name, area, url`), `removed`, `relisted`.
  The first run for a type writes `{"baseline": true, "new_count": N}` instead of N "new" records.
- `runs/<run_id>.json`: crawl report with per-area `expected` (area page count), `hits` (listing
  page count), `unique` (parsed), `status` (`complete` / `incomplete` / `error` / `no_slug` / `empty`).

### Record fields

Always: `id`, `type`, `area_code` (JIS code, e.g. `13112`), `area`, `url`, `status`
(`active`/`removed`), `first_seen`, `last_seen`. When present: `name`, `title`, `price_text`,
`price_min`, `price_max` (yen; absent when 価格未定), `address`, `access`, `line`, `station`,
`walk_min` (absent for bus access), `layout`, `floor_m2`, `balcony_m2`, `land_m2`, `building_m2`
(`*_max` when a range), `built` (`YYYY-MM`), `agent`, `agent_tel`, `image`, `extra` (unmapped
fields like 建ぺい率・容積率), `price_history` (`[[iso_ts, price_min], ...]`), `removed_at`,
`dup_key`.

### Rules

- **Removal is only inferred for areas whose crawl validated** (`unique >= hits`). If an area fails
  or comes up short, its missing listings are left as-is instead of being reported removed.
- Removed listings are kept for 30 days (so relists are detected), then pruned.
- IDs are per agent listing. The same property listed by several agents has different IDs; group by
  `dup_key` (same type + name/address + area + price) to collapse them. It's a heuristic: it misses
  duplicates listed at different prices.
# suumo
