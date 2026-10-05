"""Maintenance that isn't part of a run: out-of-scope cleanup and re-parsing the raw archive."""
import shutil
from collections import defaultdict
from pathlib import Path

from .archive import Archive
from .db import dumps
from .parse import TYPES, parse_list_page
from .pipeline import apply_detail
from .scope import in_scope


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
    by = defaultdict(int)
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
