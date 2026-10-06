"""Export state.db to git-tracked files (the site is built from them).

  data/<pref>/<type>/<area_code>.jsonl  active listings in scope, one per line, sorted by id, fixed key order
  data/<pref>/removed/<type>.jsonl      listings removed in the last PURGE_DAYS days
  data/<pref>/areas.json                area code -> name
  data/events/<run_id>.json             this run's events

One file per type and area keeps files small (a whole prefecture in one file passes GitHub's 100 MB limit) and
a day's git diff readable by ward. Files are only rewritten when their content changes.

Output is deterministic: an unchanged listing produces an identical line, so an unchanged day is an empty
git diff. Fields that change on their own (last_seen, missed) stay in the DB.
"""
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from .archive import write_atomic
from .parse import TYPES, clean
from .scope import in_scope

FIELD_ORDER = [
    "id", "type", "area_code", "area", "town", "address", "name", "title",
    "price", "price_max", "price_excludes_building", "mgmt_fee", "mgmt_form", "repair_fee", "repair_fund_once",
    "other_monthly", "other_fees", "parking",
    "stations",
    "layout", "floor_m2", "floor_m2_max", "balcony_m2", "land_m2", "land_m2_max", "building_m2", "building_m2_max",
    "site_m2", "road",
    "built", "built_planned", "floor", "floors_above", "floors_below", "structure", "total_units", "units_for_sale",
    "direction", "builder", "reform", "energy", "insulation",
    "land_rights", "land_rights_note", "zoning", "coverage_pct", "far_pct", "land_status", "build_condition",
    "land_category", "restrictions", "utilities",
    "features", "deal_type", "agent", "handover", "sale_schedule", "top_price_band",
    "url", "image", "first_seen", "removed_at", "has_detail", "dup_key",
]


def merge(row):
    """One record from the DB row: list-page fields (fresh daily) over listing-page fields."""
    lst = json.loads(row["list_json"])
    det = json.loads(row["detail_json"]) if row["detail_json"] else {}
    rec = {**det, **lst}
    if det.get("stations"):                     # the listing page lists up to 3 stations, search results 1
        rec["stations"] = det["stations"]
    rec.update(id=row["id"], type=row["type"], area_code=row["area_code"], area=row["area_name"],
               url="https://suumo.jp" + rec.pop("path"), first_seen=row["first_seen"],
               removed_at=row["removed_at"][:10] if row["removed_at"] else None,
               has_detail=bool(det) or None)
    rec["dup_key"] = dup_key(rec)
    ordered = {k: rec[k] for k in FIELD_ORDER if k in rec}
    ordered.update({k: rec[k] for k in sorted(rec) if k not in ordered})
    return clean(ordered)


def dup_key(r):
    """Same property listed by several agents: same type, building/address, size and price."""
    t = r["type"]
    if t == "used_condo":
        parts = (r.get("name"), r.get("floor_m2"), r.get("layout"), r.get("built"), r.get("price"))
    elif t in ("new_house", "used_house"):
        parts = (r.get("address"), r.get("land_m2"), r.get("building_m2"), r.get("price"))
    elif t == "land":
        parts = (r.get("address"), r.get("land_m2"), r.get("price"))
    else:
        return None
    if any(p is None for p in parts):
        return None
    return hashlib.sha1(repr((t,) + parts).encode()).hexdigest()[:12]


def _write(path, text):
    """Only when the content changed (an unchanged file keeps its mtime); atomically."""
    data = text.encode("utf-8")
    if not (path.exists() and path.read_bytes() == data):
        write_atomic(path, data)


def _write_jsonl(path, records):
    _write(path, "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records))


def _write_json(path, obj):
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _sync_dir(directory, files):
    """Write {name: records} as directory/<name>.jsonl and delete the directory's other .jsonl files."""
    for name, records in files.items():
        _write_jsonl(directory / f"{name}.jsonl", records)
    if directory.exists():
        for p in directory.glob("*.jsonl"):
            if p.stem not in files:
                p.unlink()


def export(db, targets, data_dir, run_id=None):
    data_dir = Path(data_dir)
    stats = {}
    for pref in sorted({t.pref for t in targets}):
        removed = {}
        for type_key in TYPES:
            if not any(t.pref == pref and t.type == type_key for t in targets):
                shutil.rmtree(data_dir / pref / type_key, ignore_errors=True)
                continue
            rows = db.x("SELECT * FROM listings WHERE pref=? AND type=? ORDER BY CAST(id AS INTEGER)",
                        pref, type_key).fetchall()
            rows = [r for r in rows if in_scope(targets, pref, type_key, r["area_code"])]
            by_area = defaultdict(list)
            for r in rows:
                if r["status"] == "active":
                    by_area[r["area_code"]].append(merge(r))
            _sync_dir(data_dir / pref / type_key, by_area)
            gone = [merge(r) for r in rows if r["status"] == "removed"]
            if gone:
                removed[type_key] = gone
            stats[f"{pref}/{type_key}"] = sum(len(v) for v in by_area.values())
        _sync_dir(data_dir / pref / "removed", removed)
        areas = {a["code"]: a["name"] for a in db.x("SELECT code, name FROM areas WHERE pref=? ORDER BY code", pref)}
        _write_json(data_dir / pref / "areas.json", areas)

    if run_id:
        events = [dict(e) for e in db.x("SELECT seq, kind, type, id, pref, area_code, payload FROM events "
                                        "WHERE run_id=? ORDER BY seq", run_id)]
        if events:
            for e in events:
                e["payload"] = json.loads(e["payload"])
            _write_json(data_dir / "events" / f"{run_id}.json", events)
    return stats
