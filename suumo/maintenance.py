"""Maintenance that isn't part of a run: out-of-scope cleanup, seeding from data/."""
import json
import shutil
from collections import Counter
from pathlib import Path

from .db import QUEUE_V, dumps
from .detail import FIELDS, PARSE_VERSION
from .export import dup_key, records
from .parse import TYPES
from .pipeline import P_BACKFILL
from .scope import by_key, in_scope

# exported fields that are neither search-result nor listing-page fields (page_date, lat, lng, photos come from the
# page record, which data/ doesn't hold: seed leaves them to the refetch)
EXPORT_ONLY = {"id", "type", "area_code", "area", "url", "first_seen", "removed_at", "has_detail", "dup_key",
               "page_date", "lat", "lng", "photos"}


def out_of_scope(db, targets):
    """[(pref, type, area_code, n)] of stored listings that scope.toml no longer covers."""
    rows = db.x("SELECT pref, type, area_code, COUNT(*) n FROM listings GROUP BY pref, type, area_code").fetchall()
    scope = by_key(targets)
    return [r for r in rows if not in_scope(scope, r["pref"], r["type"], r["area_code"])]


def prune(db, targets, data_dir: Path, apply=False, log=print):
    """Delete out-of-scope listings (rows with their records, queue, areas); whole prefectures lose their data
    folders too. Dry run unless apply."""
    oos = out_of_scope(db, targets)
    if not oos:
        log("nothing out of scope")
        return 0
    gone_prefs = sorted({r["pref"] for r in db.x("SELECT DISTINCT pref FROM listings")} - {t.pref for t in targets})
    by = Counter()
    for r in oos:
        by[f"{r['pref']}/{r['type']}"] += r["n"]
    log(f"out of scope: {dict(by)} | whole prefectures: {gone_prefs or 'none'}")
    if not apply:
        log("dry run; re-run with --yes to delete")
        return 0
    n = 0
    for r in oos:
        area = (r["pref"], r["type"], r["area_code"])
        db.x("DELETE FROM queue WHERE (type, id) IN (SELECT type, id FROM listings WHERE pref=? AND type=? "
             "AND area_code=?)", *area)
        n += db.x("DELETE FROM listings WHERE pref=? AND type=? AND area_code=?", *area).rowcount
        db.x("DELETE FROM areas WHERE pref=? AND type=? AND code=?", r["pref"], r["type"], r["area_code"])
    for pref in gone_prefs:
        db.x("DELETE FROM areas WHERE pref=?", pref)
        shutil.rmtree(data_dir / pref, ignore_errors=True)
    db.commit()
    log(f"deleted {n} listings; removed prefectures: {gone_prefs or 'none'}")
    return n


def seed(db, targets, data_dir: Path, ts, log=print):
    """Rebuild the DB for in-scope prefectures it holds nothing of from their data/ files (a lost or new
    state.db): listings with their first_seen and listing-page fields, and each area with listings as baselined,
    so the next crawl records what changed since that export. data/ holds no page records: every lead (one active
    listing per dup_key group, a fetched one first) is queued at P_BACKFILL. On a state that held no listings (a
    rebuild) meta backfilled/parse_v/queue_v are set so runs accept it (the refetch takes days: ~17k pages a shard);
    a state that already holds data keeps its meta (its records are still to be filled). Makes no requests."""
    held = {r["pref"] for r in db.x("SELECT DISTINCT pref FROM listings")}
    scope = by_key(targets)
    n, leads = 0, {}
    for pref in sorted({t.pref for t in targets} - held):
        names_file = data_dir / pref / "areas.json"
        names = json.loads(names_file.read_text()) if names_file.exists() else {}
        seeded = set()
        for r in records(data_dir, pref):
            if not in_scope(scope, pref, r["type"], r["area_code"]):
                continue
            body = {k: v for k, v in r.items() if k not in EXPORT_ONLY}
            lst = {**body, "path": r["url"].removeprefix("https://suumo.jp")}
            # detail_json holds listing-page fields only: a list field kept there would outlive its removal
            # from the search results (merge lays the list over it)
            detail = {k: v for k, v in body.items() if k in FIELDS} if r.get("has_detail") else None
            db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, detail_json, status,
                    first_seen, last_seen, removed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 r["type"], r["id"], pref, r["area_code"], r["area"], dumps(lst),
                 dumps(detail) if detail else None, "removed" if r.get("removed_at") else "active",
                 r["first_seen"], ts, r.get("removed_at"))
            seeded.add((r["type"], r["area_code"]))
            n += 1
            if not r.get("removed_at"):
                group = (r["type"], r.get("dup_key") or dup_key(r) or r["id"])
                rank = (not r.get("has_detail"), r["id"])
                if group not in leads or rank < leads[group][0]:
                    leads[group] = (rank, r["type"], r["id"])
        for type_key, code in seeded:
            hits = db.x("SELECT COUNT(*) FROM listings WHERE pref=? AND type=? AND area_code=? AND status='active'",
                        pref, type_key, code).fetchone()[0]
            db.x("""INSERT INTO areas (pref, type, code, name, baselined, last_hits, last_status)
                    VALUES (?,?,?,?,1,?,'complete')""", pref, type_key, code, names.get(code, code), hits)
        for code in sorted(set(names) - {code for _, code in seeded}):  # no listings: baselined by its first crawl
            db.x("INSERT INTO areas (pref, type, code, name) VALUES (?,?,?,?)",
                 pref, next(iter(TYPES)), code, names[code])
        log(f"seeded {pref} from {data_dir / pref}")
    for _, type_key, lid in leads.values():
        db.enqueue(type_key, lid, P_BACKFILL, ts)
    if not held:  # every in-scope prefecture came from data/: nothing else waits for a backfill
        db.set_meta("backfilled", ts)
        db.set_meta("parse_v", PARSE_VERSION)
        db.set_meta("queue_v", QUEUE_V)
    elif n:
        log("the state held listings before: meta backfilled not set (fill its records, or seed an empty state)")
    db.commit()
    log(f"seeded {n} listings, {len(leads)} listing pages queued (0 requests)")
    return n
