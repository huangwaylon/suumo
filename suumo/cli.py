"""Command line.

  uv run python -m suumo run [--budget 3h]   crawl search results, fetch listing pages, clean up, export, notify
  uv run python -m suumo status              coverage per prefecture/type, queue, out-of-scope data
  uv run python -m suumo prune [--yes]       delete data for prefectures/types/areas no longer in scope.toml
  uv run python -m suumo reparse             rebuild parsed fields from the raw archive (no requests)
  uv run python -m suumo notify              post unposted events to Discord
"""
import argparse
import json
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import scope as scope_mod
from .archive import Archive
from .db import DB
from .detail import parse_detail
from .export import export
from .http import Client
from .notify import notify
from .parse import TYPES, parse_list_page
from .pipeline import Pipeline

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent.parent


def parse_budget(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", s.strip())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 90s, 3m, 2h, or 0")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def ctx(args):
    load_dotenv(ROOT / ".env")
    return (DB(ROOT / args.db), Archive(ROOT / args.archive), scope_mod.load(ROOT / args.scope))


def cmd_run(args):
    db, archive, targets = ctx(args)
    now = datetime.now(JST)
    run_id = now.strftime("%Y%m%dT%H%M%S")
    db.x("INSERT INTO runs (run_id, started) VALUES (?, ?)", run_id, now.isoformat(timespec="seconds"))
    db.commit()
    client = Client(delay=args.delay)
    t0 = time.monotonic()
    p = Pipeline(db, archive, client, targets, now, run_id)
    if not args.no_crawl:
        p.crawl_lists()
    if args.budget > 0:
        p.process_queue(args.budget)
    p.purge()
    stats = export(db, targets, ROOT / args.data, run_id)
    p.report.update(export=stats, requests=client.requests_made, seconds=round(time.monotonic() - t0),
                    mb=round(client.bytes_downloaded / 1e6, 1))
    db.x("UPDATE runs SET finished=?, report=? WHERE run_id=?",
         datetime.now(JST).isoformat(timespec="seconds"), json.dumps(p.report, ensure_ascii=False), run_id)
    db.commit()
    kinds = Counter(r[0] for r in db.x("SELECT kind FROM events WHERE run_id=?", run_id))
    print(f"\nrun {run_id}: {client.requests_made} requests, {p.report['mb']} MB, {p.report['seconds']}s; "
          f"events {dict(kinds) or 'none'}")
    if not args.no_notify:
        notify(db, now, os.getenv("DISCORD_TOKEN"), os.getenv("DISCORD_CHANNEL_ID"))
    _out_of_scope_hint(db, targets)


def _out_of_scope(db, targets):
    rows = db.x("SELECT pref, type, area_code, COUNT(*) n FROM listings GROUP BY pref, type, area_code").fetchall()
    return [r for r in rows if not scope_mod.in_scope(targets, r["pref"], r["type"], r["area_code"])]


def _out_of_scope_hint(db, targets):
    oos = _out_of_scope(db, targets)
    if oos:
        print(f"note: {sum(r['n'] for r in oos)} stored listings are no longer in scope.toml; "
              f"`python -m suumo prune` removes them")


def cmd_status(args):
    db, archive, targets = ctx(args)
    print(f"{'pref/type':<22}{'active':>7}{'detail':>8}{'queued':>7}{'removed':>8}  areas")
    total_q = 0
    for t in targets:
        rows = [r for r in db.x("SELECT area_code, status, detail_json IS NOT NULL d FROM listings WHERE pref=? AND type=?",
                                t.pref, t.type) if t.includes(r["area_code"])]
        active = [r for r in rows if r["status"] == "active"]
        with_d = sum(r["d"] for r in active)
        q = db.x("""SELECT COUNT(*) FROM queue q JOIN listings l ON l.type=q.type AND l.id=q.id
                    WHERE l.pref=? AND l.type=? AND l.status='active' AND q.attempts < 3""", t.pref, t.type).fetchone()[0]
        total_q += q
        areas = Counter(r["last_status"] or "never" for r in db.x(
            "SELECT code, last_status FROM areas WHERE pref=? AND type=?", t.pref, t.type) if t.includes(r["code"]))
        pct = f"{100 * with_d / len(active):.0f}%" if active else "-"
        print(f"{t.pref + '/' + t.type:<22}{len(active):>7}{pct:>8}{q:>7}{len(rows) - len(active):>8}  {dict(areas)}")
    print(f"\nqueue: {total_q} listing pages, about {total_q * 2.1 / 60:.0f} min at the current rate")
    last = db.x("SELECT run_id, finished FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if last:
        print(f"last run: {last['run_id']} (finished {last['finished']})")
    _out_of_scope_hint(db, targets)


def cmd_prune(args):
    db, archive, targets = ctx(args)
    oos = _out_of_scope(db, targets)
    scoped_prefs = {t.pref for t in targets}
    gone_prefs = sorted({r["pref"] for r in db.x("SELECT DISTINCT pref FROM listings")} - scoped_prefs)
    by = defaultdict(int)
    for r in oos:
        by[f"{r['pref']}/{r['type']}"] += r["n"]
    if not oos:
        print("nothing out of scope")
        return
    print("out of scope:", dict(by), "| whole prefectures:", gone_prefs or "none")
    if not args.yes:
        print("dry run; re-run with --yes to delete")
        return
    n = 0
    for r in oos:
        for row in db.x("SELECT type, id, pref FROM listings WHERE pref=? AND type=? AND area_code=?",
                        r["pref"], r["type"], r["area_code"]).fetchall():
            db.x("DELETE FROM queue WHERE type=? AND id=?", row["type"], row["id"])
            archive.delete_detail(row["pref"], row["type"], row["id"])
            n += 1
        db.x("DELETE FROM listings WHERE pref=? AND type=? AND area_code=?", r["pref"], r["type"], r["area_code"])
        db.x("DELETE FROM areas WHERE pref=? AND type=? AND code=?", r["pref"], r["type"], r["area_code"])
    for pref in gone_prefs:
        db.x("DELETE FROM areas WHERE pref=?", pref)
        archive.drop_pref(pref)
        shutil.rmtree(ROOT / args.data / pref, ignore_errors=True)
    db.commit()
    export(db, targets, ROOT / args.data)
    print(f"deleted {n} listings; removed prefectures: {gone_prefs or 'none'}")


def cmd_reparse(args):
    db, archive, targets = ctx(args)
    n_list = n_detail = 0
    for pref in sorted({r["pref"] for r in db.x("SELECT DISTINCT pref FROM listings")}):
        for type_key in TYPES:
            slugs = {r["slug"]: dict(r) for r in db.x("SELECT code, name, slug FROM areas WHERE pref=? AND type=?",
                                                       pref, type_key) if r["slug"]}
            for day in archive.list_days(pref):  # oldest first, so the newest snapshot wins
                for f in archive.list_pages(pref, day, type_key):
                    area = slugs.get(f.name.split(".")[0].rsplit("_p", 1)[0])
                    if not area:
                        continue
                    _, recs = parse_list_page(Archive.read(f), type_key, area)
                    for rec in recs:
                        cur = db.x("UPDATE listings SET list_json=? WHERE type=? AND id=?",
                                   json.dumps(rec, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                                   type_key, rec["id"])
                        n_list += cur.rowcount
    for row in db.x("SELECT type, id, pref FROM listings").fetchall():
        path = archive.detail_path(row["pref"], row["type"], row["id"])
        if path.exists():
            detail, meta = parse_detail(Archive.read(path))
            db.x("UPDATE listings SET detail_json=?, info_date=?, next_update=? WHERE type=? AND id=?",
                 json.dumps(detail, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                 meta.get("info_date"), meta.get("next_update"), row["type"], row["id"])
            db.x("DELETE FROM queue WHERE type=? AND id=? AND last_error LIKE 'parse:%'", row["type"], row["id"])
            n_detail += 1
    db.commit()
    export(db, targets, ROOT / args.data)
    print(f"reparsed {n_list} list records and {n_detail} listing pages from the archive (0 requests)")


def cmd_notify(args):
    db, _, _ = ctx(args)
    notify(db, datetime.now(JST), os.getenv("DISCORD_TOKEN"), os.getenv("DISCORD_CHANNEL_ID"))


def main():
    ap = argparse.ArgumentParser(prog="suumo")
    ap.add_argument("--scope", default="scope.toml")
    ap.add_argument("--db", default="state.db")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--data", default="data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--budget", type=parse_budget, default=parse_budget("3h"), help="time for listing pages")
    r.add_argument("--delay", type=float, default=1.5)
    r.add_argument("--no-crawl", action="store_true", help="skip search results; only work the queue")
    r.add_argument("--no-notify", action="store_true")
    sub.add_parser("status")
    pr = sub.add_parser("prune")
    pr.add_argument("--yes", action="store_true")
    sub.add_parser("reparse")
    sub.add_parser("notify")
    args = ap.parse_args()
    {"run": cmd_run, "status": cmd_status, "prune": cmd_prune, "reparse": cmd_reparse, "notify": cmd_notify}[args.cmd](args)


if __name__ == "__main__":
    main()
