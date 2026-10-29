"""Command line: `uv run python -m suumo <command>` (see README)."""
import argparse
import contextlib
import json
import os
import re
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import rail as rail_mod
from . import saved as saved_mod
from . import scope as scope_mod
from .archive import Archive
from .db import DB, exclusive, locked
from .export import export, export_interrupted
from .fx import update as update_fx
from .geo import geocode
from .gitdata import commit_data, pull
from .http import Client
from .maintenance import out_of_scope, prune, reparse, seed, strip_list_fields
from .pipeline import MAX_DETAIL_ATTEMPTS, Pipeline
from .site import build as build_site
from .stations import update as update_stations

JST = ZoneInfo("Asia/Tokyo")
GEO_CACHE = "geo/towns.json"
STATIONS_CACHE = "geo/stations.json"
RAIL = "geo/rail.json"  # railway lines and stations for the map (`rail`)
SAVED = "saved.json"  # the shared saved list (updated on GitHub by the Saved workflow)
GEO_BUDGET = 600  # seconds a run spends on the address search, for towns 位置参照情報 doesn't place
MIN_DELAY = 0.3        # never faster than this between requests (the owner's decisions, 2026-10-16/17)
THROTTLED_DELAY = 1.0  # ...and never faster than this once SUUMO has pushed back (a 429/403 in any run kept)
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCOPE = "scope.toml"


def crawl_slot(now, hours):
    """The latest of the crawl hours (an int, or several) that has come: today's, or yesterday's last before then."""
    hours = sorted({hours} if isinstance(hours, int) else set(hours))
    today = [h for h in hours if h <= now.hour]
    since = now.replace(hour=today[-1] if today else hours[-1], minute=0, second=0, microsecond=0)
    return since if today else since - timedelta(days=1)


def crawled_since(db, now, hours):
    """The prefectures whose search results were crawled since the latest crawl hour (crawl_slot)."""
    since = crawl_slot(now, hours)
    return {r["pref"] for r in db.x("SELECT pref, crawled_at FROM prefs")
            if datetime.fromisoformat(r["crawled_at"]) >= since}


def crawl_due(db, now, hours, prefs):
    """SUUMO's big update lands once a day (~04:00), new listings trickle in during the day: crawl the search results
    while one of prefs (the scope's) wasn't crawled since the latest crawl hour."""
    return not set(prefs) <= crawled_since(db, now, hours)


def parse_hours(s):
    try:
        hours = sorted({int(h) for h in s.split(",")})
    except ValueError:
        raise argparse.ArgumentTypeError("use hours 0-23, e.g. 5 or 5,15") from None
    if not hours or not all(0 <= h < 24 for h in hours):
        raise argparse.ArgumentTypeError("use hours 0-23, e.g. 5 or 5,15")
    return hours


def run_delay(db, wanted):
    """A run's base delay: wanted, but THROTTLED_DELAY at least once a run on record got a 429/403 from SUUMO
    (runs are kept pipeline.EVENT_DAYS, 90; after that the faster delay is tried again)."""
    if wanted < THROTTLED_DELAY and db.x("SELECT 1 FROM runs WHERE json_extract(report, '$.pushbacks') > 0 "
                                         "LIMIT 1").fetchone():
        print(f"delay {THROTTLED_DELAY}s, not {wanted}s: SUUMO pushed back (429/403) in an earlier run")
        return THROTTLED_DELAY
    return wanted


def seconds_until_minute(now, minute):
    """Seconds from now to the next time the clock shows :minute (0 when that's under a minute away)."""
    left = (minute - now.minute) % 60 * 60 - now.second
    return max(left, 0)


def parse_budget(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", s.strip())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 90s, 3m, 2h, or 0")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def delay_arg(s):
    v = float(s)
    if v < MIN_DELAY:
        raise argparse.ArgumentTypeError(f"at least {MIN_DELAY}")
    return v


class Ctx:
    def __init__(self, args):
        self.args = args
        self.db = DB(ROOT / args.db, migrate=False)
        self.archive = Archive(ROOT / args.archive)
        self.targets = scope_mod.load(ROOT / args.scope)
        self.data = ROOT / args.data
        self.lock = ROOT / (args.db + ".lock")

    def out_of_scope_hint(self):
        """Only for the main scope: a narrower --scope (a one-off crawl of one prefecture) would suggest pruning
        everything else."""
        if self.args.scope != DEFAULT_SCOPE:
            return
        oos = out_of_scope(self.db, self.targets)
        if oos:
            print(f"note: {sum(r['n'] for r in oos)} stored listings are no longer in scope.toml; "
                  f"`python -m suumo prune` removes them")


def cmd_run(c: Ctx):
    """A run: crawl (when due), listing pages, purge, export, then geocode and commit. Ctrl-C or SIGTERM rolls back
    the open transaction (never half an area's reconcile), marks the run interrupted and exits 130; the next run
    exports its events. A crawl stopped by a network outage finishes the run without listing pages and exits 1."""
    a = c.args
    now = datetime.now(JST)
    run_id = now.strftime("%Y%m%dT%H%M%S")
    if a.push:
        pull(ROOT)  # the saved list changes on GitHub
    for rid in export_interrupted(c.db, c.data):
        print(f"run {rid} didn't finish: marked interrupted, its events exported")
    c.db.x("INSERT INTO runs (run_id, started) VALUES (?, ?)", run_id, now.isoformat(timespec="seconds"))
    c.db.commit()
    client = Client(delay=run_delay(c.db, a.delay))
    t0 = time.monotonic()
    p = Pipeline(c.db, c.archive, client, c.targets, now, run_id, saved=saved_mod.load(ROOT / SAVED))

    def stats():
        return dict(requests=client.requests_made, slowdowns=client.slowdowns, pushbacks=client.pushbacks,
                    net_errors=client.net_errors, seconds=round(time.monotonic() - t0),
                    mb=round(client.bytes_downloaded / 1e6, 1))

    online = True
    try:
        prefs = {t.pref for t in c.targets}
        if a.crawl_at is not None and not crawl_due(c.db, now, a.crawl_at, prefs):
            print(f"search results: crawled since {crawl_slot(now, a.crawl_at):%H}:00; this run works the queue")
        elif not a.no_crawl:  # with --crawl-at, a daily crawl that stopped part-way resumes with the next prefecture
            online = p.crawl_lists(skip=crawled_since(c.db, now, a.crawl_at) if a.crawl_at is not None else ())
        budget = a.budget if online else 0  # offline: no listing pages either
        if a.until_minute is not None:  # an hourly run: listing pages until then, so the next run starts on time
            budget = min(budget, seconds_until_minute(datetime.now(JST), a.until_minute))
        if budget > 0:
            p.process_queue(budget, checkpoint=lambda: export(c.db, c.targets, c.data))
        p.purge()
        p.report.update(export=export(c.db, c.targets, c.data, run_id), **stats())
        c.db.x("UPDATE runs SET finished=?, report=? WHERE run_id=?",
               datetime.now(JST).isoformat(timespec="seconds"), json.dumps(p.report, ensure_ascii=False), run_id)
        c.db.commit()
    except KeyboardInterrupt:
        c.db.conn.rollback()  # what's committed (whole areas, fetched pages) stays; the next run exports it
        p.report.update(interrupted=True, **stats())
        c.db.x("UPDATE runs SET report=? WHERE run_id=?", json.dumps(p.report, ensure_ascii=False), run_id)
        c.db.commit()
        print(f"\nrun {run_id}: interrupted after {p.report['seconds']}s; the next run exports its events")
        raise SystemExit(130) from None
    if not a.no_geocode and online:  # a second crawler (--no-geocode) leaves geo/ and fx.json to the hourly one
        try:  # towns of new listings, for the site's map; never stops the commit below
            update_geo(c.data, c.targets, budget=GEO_BUDGET)
        except Exception as e:
            print(f"geocoding failed (retried next run): {e!r}")
        try:
            update_fx(c.data / "fx.json")  # the day's yen-dollar rate, for the site's USD prices
        except Exception as e:
            print(f"exchange rate failed (retried next run): {e!r}")
    kinds = Counter(r[0] for r in c.db.x("SELECT kind FROM events WHERE run_id=?", run_id))
    print(f"\nrun {run_id}: {client.requests_made} requests, {p.report['mb']} MB, {p.report['seconds']}s; "
          f"events {dict(kinds) or 'none'}")
    if a.commit or a.push:
        commit_data(ROOT, run_id, push=a.push)
    c.out_of_scope_hint()
    if not online:  # non-zero, so a driver waits before the next try
        raise SystemExit(f"run {run_id}: SUUMO or the network is down; the search results left are crawled later")


def cmd_status(c: Ctx):
    db = c.db
    print(f"{'pref/type':<22}{'active':>7}{'detail':>8}{'queued':>7}{'ended':>8}  areas")  # ended: kept, saved
    total_q = 0
    for t in c.targets:
        rows = [r for r in db.x("SELECT area_code, status, detail_json IS NOT NULL d FROM listings "
                                "WHERE pref=? AND type=?", t.pref, t.type) if t.includes(r["area_code"])]
        active = [r for r in rows if r["status"] == "active"]
        queued = sum(1 for r in db.x("""SELECT l.area_code FROM queue q JOIN listings l ON l.type=q.type AND l.id=q.id
                                        WHERE l.pref=? AND l.type=? AND l.status='active' AND q.attempts < ?""",
                                     t.pref, t.type, MAX_DETAIL_ATTEMPTS) if t.includes(r["area_code"]))
        total_q += queued
        areas = Counter(r["last_status"] or "never" for r in db.x(
            "SELECT code, last_status FROM areas WHERE pref=? AND type=?", t.pref, t.type) if t.includes(r["code"]))
        pct = f"{100 * sum(r['d'] for r in active) / len(active):.0f}%" if active else "-"
        print(f"{t.pref + '/' + t.type:<22}{len(active):>7}{pct:>8}{queued:>7}{len(rows) - len(active):>8}  "
              f"{dict(areas)}")
    print(f"\nqueue: {total_q:,} listing pages")
    parse_failures = db.x("SELECT COUNT(*) FROM queue WHERE last_error LIKE 'parse:%'").fetchone()[0]
    if parse_failures:
        print(f"parse failures: {parse_failures} (fix the parser, then `reparse`)")
    last = db.x("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if last and last["finished"]:
        print(f"last run: {last['run_id']} (finished {last['finished']})")
    elif last:
        print_progress(last, running=locked(c.lock))
    c.out_of_scope_hint()


def print_progress(run, running):
    """runs.progress (a database older than that column: the report's progress)."""
    raw = run["progress"] if "progress" in run.keys() else None  # noqa: SIM118  (a Row: `in` looks at values)
    p = json.loads(raw) if raw else json.loads(run["report"] or "{}").get("progress")
    print(f"\ncurrent run: {run['run_id']} — "
          + ("running" if running else "not running (interrupted; the next run continues the queue)"))
    if p:
        minutes = int((datetime.now(JST) - datetime.fromisoformat(p["at"])).total_seconds() // 60)
        print(f"  {p['phase']} (updated {minutes} min ago): {p.get('text', '')}")


def cmd_prune(c: Ctx):
    if prune(c.db, c.archive, c.targets, c.data, apply=c.args.yes):
        export(c.db, c.targets, c.data)


def update_geo(data, targets, budget=None):
    """Coordinates of new towns and names of the scope's stations (geo/)."""
    geocode(data, ROOT / GEO_CACHE, budget_seconds=budget)
    update_stations(ROOT / STATIONS_CACHE, ROOT / RAIL, {t.pref for t in targets}, data)


def cmd_geocode(args):
    update_geo(ROOT / args.data, scope_mod.load(ROOT / args.scope))


def cmd_site(args):
    build_site(ROOT / args.data, ROOT / GEO_CACHE, ROOT / args.out, stations_cache=ROOT / STATIONS_CACHE,
               saved=saved_mod.load(ROOT / SAVED), rail_path=ROOT / RAIL)


def env(name):
    """A setting from the environment, else from .env (KEY=value lines; never committed)."""
    if os.environ.get(name):
        return os.environ[name]
    path = ROOT / ".env"
    for line in path.read_text().splitlines() if path.exists() else []:
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def cmd_rail(args):
    rail_mod.update(ROOT / RAIL, {k: env(k) for k in ("ODPT_TOKEN", "ODPT_CHALLENGE_TOKEN")})


def cmd_saved(args):
    message = saved_mod.apply(ROOT / SAVED, args.title)
    if message is None:
        raise SystemExit(f"not a save request: {args.title!r} (expected 'save <type>:<id>' or 'unsave <type>:<id>')")
    print(message)


def cmd_reparse(c: Ctx):
    reparse(c.db, c.archive)
    export(c.db, c.targets, c.data)


def cmd_seed(c: Ctx):
    seed(c.db, c.targets, c.data, datetime.now(JST).isoformat(timespec="seconds"))


def _interrupt(signum, frame):
    raise KeyboardInterrupt  # SIGTERM (launchd, kill) ends a run like Ctrl-C: rolled back, marked interrupted


def main():
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up at once in `tee` / `tail -f`
    signal.signal(signal.SIGINT, signal.default_int_handler)  # a background start (nohup, &) may ignore SIGINT
    signal.signal(signal.SIGTERM, _interrupt)
    ap = argparse.ArgumentParser(prog="suumo")
    ap.add_argument("--scope", default=DEFAULT_SCOPE)
    ap.add_argument("--db", default="state.db")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--data", default="data")
    ap.add_argument("--delay", type=delay_arg, default=MIN_DELAY,
                    help=f"base seconds between requests (+0-50%% jitter; at least {MIN_DELAY})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def command(name, func, access, help):
        """access: "write" (state.db, run lock), "read" (state.db), "files" (data/ and geo/ only)."""
        p = sub.add_parser(name, help=help)
        p.set_defaults(func=func, access=access)
        return p

    r = command("run", cmd_run, "write", "crawl, fetch listing pages, clean up, export, geocode new towns")
    r.add_argument("--budget", type=parse_budget, default=parse_budget("3h"), help="time for listing pages")
    r.add_argument("--crawl-at", type=parse_hours, metavar="H[,H...]",
                   help="crawl search results only at the first run after each of these hours (e.g. 5,15)")
    r.add_argument("--no-geocode", action="store_true",
                   help="leave geo/ alone (a second crawler next to the hourly one; that one geocodes all of data/)")
    r.add_argument("--until-minute", type=int, choices=range(60), metavar="0-59",
                   help="stop listing pages at the next :MM (hourly runs end before the next one starts)")
    r.add_argument("--no-crawl", action="store_true", help="skip search results; only work the queue")
    r.add_argument("--commit", action="store_true", help="git-commit data/ and geo/ after the run")
    r.add_argument("--push", action="store_true", help="commit and push (rebuilds the site)")
    command("status", cmd_status, "read", "coverage, queue, the current run's progress")
    command("prune", cmd_prune, "write", "delete data no longer in scope.toml (dry run without --yes)").add_argument(
        "--yes", action="store_true")
    command("reparse", cmd_reparse, "write", "re-run parsers over the raw archive (no requests)")
    command("seed", cmd_seed, "write", "rebuild state.db for prefectures it doesn't hold from data/ (no requests)")
    command("geocode", cmd_geocode, "files", "look up coordinates of new towns and station names (geo/)")
    command("site", cmd_site, "files", "build the static search site").add_argument("--out", default="_site")
    command("rail", cmd_rail, "files", "rebuild geo/rail.json: railway lines and stations for the map (N02 + ODPT)")
    command("saved", cmd_saved, "files", "apply 'save <type>:<id>' / 'unsave <type>:<id>' to saved.json").add_argument(
        "title")
    args = ap.parse_args()
    if args.access == "files":
        return args.func(args)
    c = Ctx(args)
    with exclusive(c.lock) if args.access == "write" else contextlib.nullcontext():
        if args.access == "write":
            c.db.migrate()
            strip_list_fields(c.db)
        args.func(c)


if __name__ == "__main__":
    main()
