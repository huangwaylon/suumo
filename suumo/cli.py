"""Command line: `uv run python -m suumo <command>` (see README)."""
import argparse
import fcntl
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import scope as scope_mod
from .archive import Archive
from .db import DB, exclusive
from .export import export
from .gitdata import commit_data
from .http import Client
from .maintenance import out_of_scope, prune, reparse
from .pipeline import MAX_DETAIL_ATTEMPTS, Pipeline

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent.parent


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
    c.db.x("INSERT INTO runs (run_id, started) VALUES (?, ?)", run_id, now.isoformat(timespec="seconds"))
    c.db.commit()
    client = Client(delay=a.delay)
    t0 = time.monotonic()
    p = Pipeline(c.db, c.archive, client, c.targets, now, run_id)
    if not a.no_crawl:
        p.crawl_lists()
    if a.budget > 0:
        p.process_queue(a.budget, checkpoint=lambda: export(c.db, c.targets, c.data))
    p.purge()
    p.report.update(export=export(c.db, c.targets, c.data, run_id), requests=client.requests_made,
                    seconds=round(time.monotonic() - t0), mb=round(client.bytes_downloaded / 1e6, 1))
    c.db.x("UPDATE runs SET finished=?, report=? WHERE run_id=?",
           datetime.now(JST).isoformat(timespec="seconds"), json.dumps(p.report, ensure_ascii=False), run_id)
    c.db.commit()
    kinds = Counter(r[0] for r in c.db.x("SELECT kind FROM events WHERE run_id=?", run_id))
    print(f"\nrun {run_id}: {client.requests_made} requests, {p.report['mb']} MB, {p.report['seconds']}s; "
          f"events {dict(kinds) or 'none'}")
    if a.commit or a.push:
        commit_data(ROOT, run_id, push=a.push)
    c.out_of_scope_hint()


def cmd_status(c: Ctx):
    db = c.db
    print(f"{'pref/type':<22}{'active':>7}{'detail':>8}{'queued':>7}{'removed':>8}  areas")
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
    per_page = c.args.delay * 1.25 + 0.3  # mean delay with jitter + response time
    print(f"\nqueue: {total_q} listing pages, about {total_q * per_page / 60:.0f} min")
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
    p = json.loads(run["report"] or "{}").get("progress") or {}
    state = "running" if running else "not running (interrupted; the next run continues the queue)"
    print(f"\ncurrent run: {run['run_id']} — {state}")
    if not p:
        return
    ago = datetime.now(JST) - datetime.fromisoformat(p["at"])
    print(f"  phase: {p['phase']} (updated {int(ago.total_seconds() // 60)} min ago)")
    if p["phase"] == "search results":
        print(f"  {p['target']}: area {p['areas_done'] + 1}/{p['areas']} ({p['area']})")
    elif p["phase"] == "listing pages":
        eta_h, eta_m = divmod(p["eta_seconds"] // 60, 60)
        print(f"  {p['done']:,}/{p['total']:,} ({100 * p['done'] / p['total']:.1f}%) · {p['seconds_per_page']} s/page"
              f" · ETA {eta_h}h{eta_m:02d}m · fetched {p['fetched']:,} failed {p['failed']:,} gone {p['gone']:,}"
              + (f" · delay {p['delay']:.2f}s" if p.get("delay") else ""))


def cmd_prune(c: Ctx):
    if prune(c.db, c.archive, c.targets, c.data, apply=c.args.yes):
        export(c.db, c.targets, c.data)


def cmd_reparse(c: Ctx):
    reparse(c.db, c.archive)
    export(c.db, c.targets, c.data)


COMMANDS = {"run": cmd_run, "status": cmd_status, "prune": cmd_prune, "reparse": cmd_reparse}
READ_ONLY = {"status"}


def main():
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up at once in `tee` / `tail -f`
    ap = argparse.ArgumentParser(prog="suumo")
    ap.add_argument("--scope", default="scope.toml")
    ap.add_argument("--db", default="state.db")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--data", default="data")
    ap.add_argument("--delay", type=float, default=1.5, help="base seconds between requests (+0-50%% jitter)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="crawl, fetch listing pages, clean up, export")
    r.add_argument("--budget", type=parse_budget, default=parse_budget("3h"), help="time for listing pages")
    r.add_argument("--no-crawl", action="store_true", help="skip search results; only work the queue")
    r.add_argument("--commit", action="store_true", help="git-commit data/ after the run")
    r.add_argument("--push", action="store_true", help="commit and push data/")
    sub.add_parser("status", help="coverage, queue, out-of-scope data")
    pr = sub.add_parser("prune", help="delete data no longer in scope.toml (dry run without --yes)")
    pr.add_argument("--yes", action="store_true")
    sub.add_parser("reparse", help="re-run parsers over the raw archive (no requests)")
    args = ap.parse_args()
    c = Ctx(args)
    if args.cmd in READ_ONLY:
        COMMANDS[args.cmd](c)
    else:
        with exclusive(c.lock):
            COMMANDS[args.cmd](c)


if __name__ == "__main__":
    main()
