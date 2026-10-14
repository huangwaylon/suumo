"""Maintenance that isn't part of a run: out-of-scope cleanup, re-parsing the raw archive, seeding from data/."""
import json
import shutil
from collections import Counter
from pathlib import Path

from .archive import Archive
from .db import dumps
from .parse import TYPES, parse_list_page
from .pipeline import apply_detail
from .scope import in_scope

EXPORT_ONLY = {"id", "type", "area_code", "area", "url", "first_seen", "removed_at", "has_detail", "dup_key"}


def out_of_scope(db, targets):
    """[(pref, type, area_code, n)] of stored listings that scope.toml no longer covers."""
    rows = db.x("SELECT pref, type, area_code, COUNT(*) n FROM listings GROUP BY pref, type, area_code").fetchall()
    return [r for r in rows if not in_scope(targets, r["pref"], r["type"], r["area_code"])]


def prune(db, archive: Archive, targets, data_dir: Path, apply=False, log=print):
    """Delete out-of-scope listings (rows, queue, archived pages, areas); whole prefectures lose their
    archive and data folders too. Dry run unless apply."""
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
        for row in db.area_listings(r["pref"], r["type"], r["area_code"]):
            db.dequeue(row["type"], row["id"])
            archive.delete_detail(row["pref"], row["type"], row["id"])
            n += 1
        db.x("DELETE FROM listings WHERE pref=? AND type=? AND area_code=?", r["pref"], r["type"], r["area_code"])
        db.x("DELETE FROM areas WHERE pref=? AND type=? AND code=?", r["pref"], r["type"], r["area_code"])
    for pref in gone_prefs:
        db.x("DELETE FROM areas WHERE pref=?", pref)
        archive.drop_pref(pref)
        shutil.rmtree(data_dir / pref, ignore_errors=True)
    db.commit()
    log(f"deleted {n} listings; removed prefectures: {gone_prefs or 'none'}")
    return n


def reparse(db, archive: Archive, log=print):
    """Rebuild list_json from archived search-result snapshots (newest wins) and detail_json from archived
    listing pages. Lifecycle state is untouched. Makes no requests."""
    n_list = n_detail = 0
    for pref in sorted(r["pref"] for r in db.x("SELECT DISTINCT pref FROM listings")):
        for type_key in TYPES:
            areas = {r["slug"]: dict(r) for r in db.x(
                "SELECT code, name, slug FROM areas WHERE pref=? AND type=? AND slug IS NOT NULL", pref, type_key)}
            for day in archive.list_days(pref):  # oldest first, so the newest snapshot wins
                for f in archive.list_pages(pref, day, type_key):
                    area = areas.get(f.name.split(".")[0].rsplit("_p", 1)[0])
                    if not area:
                        continue
                    _, recs = parse_list_page(Archive.read(f), type_key, area)
                    for rec in recs:
                        n_list += db.x("UPDATE listings SET list_json=? WHERE type=? AND id=?",
                                       dumps(rec), type_key, rec["id"]).rowcount
    for row in db.x("SELECT type, id, pref FROM listings").fetchall():
        path = archive.detail_path(row["pref"], row["type"], row["id"])
        if not path.exists():
            continue
        try:
            apply_detail(db, row["type"], row["id"], Archive.read(path))
        except Exception as e:
            log(f"  ! parse failed for {row['type']}/{row['id']}: {e!r}")
            continue
        db.x("DELETE FROM queue WHERE type=? AND id=? AND last_error LIKE 'parse:%'", row["type"], row["id"])
        n_detail += 1
    db.commit()
    log(f"reparsed {n_list} list records and {n_detail} listing pages from the archive (0 requests)")
    return n_list, n_detail


def seed(db, targets, data_dir: Path, ts, log=print):
    """Rebuild the DB for in-scope prefectures it holds nothing of from their data/ files (a lost or new
    state.db): listings with their first_seen and listing-page fields, and each area with listings as baselined,
    so the next crawl records what changed since that export. Makes no requests."""
    held = {r["pref"] for r in db.x("SELECT DISTINCT pref FROM listings")}
    n = 0
    for pref in sorted({t.pref for t in targets} - held):
        names_file = data_dir / pref / "areas.json"
        names = json.loads(names_file.read_text()) if names_file.exists() else {}
        seeded = set()
        for f in sorted((data_dir / pref).glob("*/*.jsonl")):
            for line in f.read_text().splitlines():
                r = json.loads(line)
                if not in_scope(targets, pref, r["type"], r["area_code"]):
                    continue
                body = {k: v for k, v in r.items() if k not in EXPORT_ONLY}
                lst = {**body, "path": r["url"].removeprefix("https://suumo.jp")}
                db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, detail_json, status,
                        first_seen, last_seen, removed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     r["type"], r["id"], pref, r["area_code"], r["area"], dumps(lst),
                     dumps(body) if r.get("has_detail") else None, "removed" if r.get("removed_at") else "active",
                     r["first_seen"], ts, r.get("removed_at"))
                seeded.add((r["type"], r["area_code"]))
                n += 1
        for type_key, code in seeded:
            hits = db.x("SELECT COUNT(*) FROM listings WHERE pref=? AND type=? AND area_code=? AND status='active'",
                        pref, type_key, code).fetchone()[0]
            db.x("""INSERT INTO areas (pref, type, code, name, baselined, last_hits, last_status)
                    VALUES (?,?,?,?,1,?,'complete')""", pref, type_key, code, names.get(code, code), hits)
        for code in sorted(set(names) - {code for _, code in seeded}):  # no listings: baselined by its first crawl
            db.x("INSERT INTO areas (pref, type, code, name) VALUES (?,?,?,?)",
                 pref, next(iter(TYPES)), code, names[code])
        log(f"seeded {pref} from {data_dir / pref}")
    db.commit()
    log(f"seeded {n} listings (0 requests)")
    return n
