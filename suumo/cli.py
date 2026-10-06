"""Command line: `uv run python -m suumo <command>` (see README)."""
import argparse
import contextlib
import fcntl
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import saved as saved_mod
from . import scope as scope_mod
from .archive import Archive
from .db import DB, exclusive
from .export import export
from .geo import geocode
from .gitdata import commit_data, pull
from .http import Client
from .maintenance import out_of_scope, prune, reparse
from .pipeline import MAX_DETAIL_ATTEMPTS, Pipeline
from .site import build as build_site
from .stations import update as update_stations

JST = ZoneInfo("Asia/Tokyo")
GEO_CACHE = "geo/towns.json"
STATIONS_CACHE = "geo/stations.json"
SAVED = "saved.json"  # the shared saved list (updated on GitHub by the Saved workflow)
GEO_BUDGET = 600  # seconds a run spends geocoding new towns (a new prefecture fills over a few runs; or run `geocode`)
ROOT = Path(__file__).resolve().parent.parent


def seconds_until_minute(now, minute):
    """Seconds from now to the next time the clock shows :minute (0 when that's under a minute away)."""
    left = (minute - now.minute) % 60 * 60 - now.second
    return max(left, 0)


def parse_budget(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", s.strip())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 90s, 3m, 2h, or 0")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


class Ctx:
    def __init__(self, args):
        self.args = args
        self.db = DB(ROOT / args.db)
        self.archive = Archive(ROOT / args.archive)
        self.targets = scope_mod.load(ROOT / args.scope)
        self.data = ROOT / args.data
        self.lock = ROOT / (args.db + ".lock")

    def out_of_scope_hint(self):
        oos = out_of_scope(self.db, self.targets)
        if oos:
            print(f"note: {sum(r['n'] for r in oos)} stored listings are no longer in scope.toml; "
                  f"`python -m suumo prune` removes them")


def cmd_run(c: Ctx):
    a = c.args
    now = datetime.now(JST)
    run_id = now.strftime("%Y%m%dT%H%M%S")
    if a.push:
        pull(ROOT)  # the saved list changes on GitHub
    c.db.x("INSERT INTO runs (run_id, started) VALUES (?, ?)", run_id, now.isoformat(timespec="seconds"))
    c.db.commit()
    client = Client(delay=a.delay)
    t0 = time.monotonic()
    p = Pipeline(c.db, c.archive, client, c.targets, now, run_id, saved=saved_mod.load(ROOT / SAVED))
    if not a.no_crawl:
        p.crawl_lists()
    budget = a.budget
    if a.until_minute is not None:  # an hourly run: listing pages until then, so the next run starts on time
        budget = min(budget, seconds_until_minute(datetime.now(JST), a.until_minute))
    if budget > 0:
        p.process_queue(budget, checkpoint=lambda: export(c.db, c.targets, c.data))
    p.purge()
    p.report.update(export=export(c.db, c.targets, c.data, run_id), requests=client.requests_made,
                    slowdowns=client.slowdowns,
                    seconds=round(time.monotonic() - t0), mb=round(client.bytes_downloaded / 1e6, 1))
    c.db.x("UPDATE runs SET finished=?, report=? WHERE run_id=?",
           datetime.now(JST).isoformat(timespec="seconds"), json.dumps(p.report, ensure_ascii=False), run_id)
    c.db.commit()
    try:  # towns of new listings, for the site's map; never stops the commit below
        geocode(c.data, ROOT / GEO_CACHE, budget_seconds=GEO_BUDGET)
        update_stations(ROOT / STATIONS_CACHE, ROOT / GEO_CACHE, {t.pref for t in c.targets})
    except Exception as e:
        print(f"geocoding failed (retried next run): {e!r}")
    kinds = Counter(r[0] for r in c.db.x("SELECT kind FROM events WHERE run_id=?", run_id))
    print(f"\nrun {run_id}: {client.requests_made} requests, {p.report['mb']} MB, {p.report['seconds']}s; "
          f"events {dict(kinds) or 'none'}")
    if a.commit or a.push:
        commit_data(ROOT, run_id, push=a.push)
    c.out_of_scope_hint()


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
    last = db.x("SELECT run_id, finished, report FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if last and last["finished"]:
        print(f"last run: {last['run_id']} (finished {last['finished']})")
    elif last:
        print_progress(last, running=is_locked(c.lock))
    c.out_of_scope_hint()


def is_locked(lock_path):
    """True while a writing command (a run) holds the lock."""
    if not lock_path.exists():
        return False
    with open(lock_path) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def print_progress(run, running):
    p = json.loads(run["report"] or "{}").get("progress")
    print(f"\ncurrent run: {run['run_id']} — "
          + ("running" if running else "not running (interrupted; the next run continues the queue)"))
    if p:
        minutes = int((datetime.now(JST) - datetime.fromisoformat(p["at"])).total_seconds() // 60)
        print(f"  {p['phase']} (updated {minutes} min ago): {p.get('text', '')}")


def cmd_prune(c: Ctx):
    if prune(c.db, c.archive, c.targets, c.data, apply=c.args.yes):
        export(c.db, c.targets, c.data)


def cmd_geocode(args):
    geocode(ROOT / args.data, ROOT / GEO_CACHE)
    update_stations(ROOT / STATIONS_CACHE, ROOT / GEO_CACHE, {t.pref for t in scope_mod.load(ROOT / args.scope)})


def cmd_site(args):
    build_site(ROOT / args.data, ROOT / GEO_CACHE, ROOT / args.out, stations_cache=ROOT / STATIONS_CACHE,
               saved=saved_mod.load(ROOT / SAVED))


def cmd_saved(args):
    message = saved_mod.apply(ROOT / SAVED, args.title)
    if message is None:
        raise SystemExit(f"not a save request: {args.title!r} (expected 'save <type>:<id>' or 'unsave <type>:<id>')")
    print(message)


def cmd_reparse(c: Ctx):
    reparse(c.db, c.archive)
    export(c.db, c.targets, c.data)


def main():
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up at once in `tee` / `tail -f`
    ap = argparse.ArgumentParser(prog="suumo")
    ap.add_argument("--scope", default="scope.toml")
    ap.add_argument("--db", default="state.db")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--data", default="data")
    ap.add_argument("--delay", type=float, default=1.5, help="base seconds between requests (+0-50%% jitter)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def command(name, func, access, help):
        """access: "write" (state.db, run lock), "read" (state.db), "files" (data/ and geo/ only)."""
        p = sub.add_parser(name, help=help)
        p.set_defaults(func=func, access=access)
        return p

    r = command("run", cmd_run, "write", "crawl, fetch listing pages, clean up, export, geocode new towns")
    r.add_argument("--budget", type=parse_budget, default=parse_budget("3h"), help="time for listing pages")
    r.add_argument("--until-minute", type=int, choices=range(60), metavar="0-59",
                   help="stop listing pages at the next :MM (hourly runs end before the next one starts)")
    r.add_argument("--no-crawl", action="store_true", help="skip search results; only work the queue")
    r.add_argument("--commit", action="store_true", help="git-commit data/ and geo/ after the run")
    r.add_argument("--push", action="store_true", help="commit and push (rebuilds the site)")
    command("status", cmd_status, "read", "coverage, queue, the current run's progress")
    command("prune", cmd_prune, "write", "delete data no longer in scope.toml (dry run without --yes)").add_argument(
        "--yes", action="store_true")
    command("reparse", cmd_reparse, "write", "re-run parsers over the raw archive (no requests)")
    command("geocode", cmd_geocode, "files", "look up coordinates of new towns and station names (geo/)")
    command("site", cmd_site, "files", "build the static search site").add_argument("--out", default="_site")
    command("saved", cmd_saved, "files", "apply 'save <type>:<id>' / 'unsave <type>:<id>' to saved.json").add_argument(
        "title")
    args = ap.parse_args()
    if args.access == "files":
        return args.func(args)
    c = Ctx(args)
    with exclusive(c.lock) if args.access == "write" else contextlib.nullcontext():
        args.func(c)


if __name__ == "__main__":
    main()
