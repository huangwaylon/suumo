# Decisions

The owner's decisions behind the rules in CLAUDE.md, newest first, with the reason at the time. A rule in CLAUDE.md
states what holds now; this file says why and since when. Change a rule only with the owner, then add an entry.

## 2026-11-25: data/<pref>/ leaves git; history reset

- `data/<pref>/` (listings, page side files, areas.json) travels as one asset, `data-<stamp>.tar.zst` on the release
  `suumo-data`, named by the committed `data/snapshot.json`; git keeps the pointer, `data/events/`, `data/fx.json`,
  `data/canary/` ("option D", chosen over rebuilding data/ from the states on every build after two independent
  reviews). Why: git grew ~150 MB a day (pack 10 GiB, GitHub reported 7.7 GB); the states plus a release asset
  keep a failed shard's prefectures as they were, which a states-only build couldn't.
- The git history was reset to one root commit the same day (release tags moved onto it). The old history is not
  on GitHub any more.
- Cloudflare R2 is out (it needs a payment card). Cloudflare work (Workers static hosting, a cron dispatcher, a
  favorites API) is on hold, not rejected; the cron dispatcher would reverse "no external scheduler" (2026-11-15).

## 2026-11-15: the crawl moves to GitHub Actions only

- No machine of ours: crawl.yml runs 20 shards (shards.toml), at most 8 at once, each with its own state on the
  release `suumo-state`. Why: the Mac crawler stopped silently for 6 days.
- GitHub cron every 30 minutes plus fixed slots, no external scheduler (GitHub delivers only ~2-11% of scheduled
  runs, hence many ticks).
- Runner budget of the account's 20 concurrent runners (Free plan): suumo 8, fitness's Kalori scraper 10, the
  fitness ITS runner 1.
- Parallel workers on disjoint prefectures, each its own state and IP, at `--delay 1.0` each (dispatches clamped
  to ≥ 1.0).
- Keep all free text (texts, env, agent, photo comments) in the page files and on the site; images are references
  only (the browser loads them from SUUMO; the service worker caches a few).

## 2026-10-29: two crawls a day, 05:00 and 15:00 JST

SUUMO publishes in two batches: ~04:00 (most price changes and removals; sometimes still landing at 04:10) and
~14:30 (15 minutes; counts dip while it lands). Nothing changes in between: a 12:00 crawl found nothing on 5 of 6
days, and 15-minute probes saw no change from 14:46 to 17:48.

## 2026-10-22: SUUMO's maintenance page counts as down (seen that day)

When every attempt gets a 5xx (SUUMO's maintenance page), `http.Unavailable`, part of `pipeline.DOWN`, stops the
crawl like a network outage instead of marking areas incomplete.

## 2026-10-14 to 10-17: request delay

- 10-14: listing pages at 0.5 s.
- 10-16: 0.3 s, after 0.5 s drew no pushback.
- 10-17: everything (search results and listing pages) at 0.3 s, after ~220k listing pages at 0.3 s drew none.
  0.3 s + up to 50% jitter is the default and the floor (`cli.MIN_DELAY`); after any 429/403 on record (90 days),
  runs use 1.0 s (`cli.run_delay`).

## 2026-10-15: other agents' copies of a property aren't fetched

Only one listing per `dup_key` group (its lead) gets its page fetched while the lead stands; the accepted cost is
that the other agents' `deal_type` (e.g. 仲介手数料なし) stays unknown.
