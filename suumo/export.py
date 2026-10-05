"""Export state.db to git-tracked files that the bot reads.

  data/<pref>/<type>.jsonl   active listings in scope, one per line, sorted by id, fixed key order
  data/<pref>/removed.jsonl  listings removed in the last PURGE_DAYS days
  data/<pref>/areas.json     area code -> name
  data/events/<run_id>.json  this run's events

Output is deterministic: an unchanged listing produces an identical line, so an unchanged day is an empty
git diff. Fields that change on their own (last_seen, missed, info_date, next_update, detail_fetched) stay
in the DB.
"""
import hashlib
import json
from pathlib import Path

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
    "land_rights", "land_rights_note", "zoning", "coverage_pct", "far_pct", "land_status", "build_condition", "land_category",
    "restrictions", "utilities",
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
    if det.get("price_excludes_building"):
        rec["price_excludes_building"] = True
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


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def export(db, targets, data_dir, run_id=None):
    data_dir = Path(data_dir)
    stats = {}
    for pref in sorted({t.pref for t in targets}):
        removed = []
        for type_key in TYPES:
            path = data_dir / pref / f"{type_key}.jsonl"
            if not any(t.pref == pref and t.type == type_key for t in targets):
                path.unlink(missing_ok=True)
                continue
            rows = db.x("SELECT * FROM listings WHERE pref=? AND type=? ORDER BY CAST(id AS INTEGER)",
                        pref, type_key).fetchall()
            rows = [r for r in rows if in_scope(targets, pref, type_key, r["area_code"])]
            active = [merge(r) for r in rows if r["status"] == "active"]
            removed += [merge(r) for r in rows if r["status"] == "removed"]
            _write_jsonl(path, active)
            stats[f"{pref}/{type_key}"] = len(active)
        _write_jsonl(data_dir / pref / "removed.jsonl", sorted(removed, key=lambda r: (r["type"], int(r["id"]))))
        areas = {}
        for a in db.x("SELECT code, name FROM areas WHERE pref=? ORDER BY code", pref):
            areas[a["code"]] = a["name"]
        _write_json(data_dir / pref / "areas.json", areas)

    if run_id:
        events = [dict(e) for e in db.x("SELECT seq, kind, type, id, pref, area_code, payload FROM events "
                                        "WHERE run_id=? ORDER BY seq", run_id)]
        if events:
            for e in events:
                e["payload"] = json.loads(e["payload"])
            _write_json(data_dir / "events" / f"{run_id}.json", events)
    return stats
