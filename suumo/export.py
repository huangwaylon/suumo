"""Export state.db to files under data/ (the site is built from them; data/<pref>/ travels as a release asset,
suumo/snapshot.py, data/events/ is in git).

  data/<pref>/<type>/<area_code>.jsonl  active listings in scope, one per line, sorted by id, fixed key order
  data/<pref>/removed/<type>.jsonl      ended listings kept because they're on the saved list (saved.json)
  data/<pref>/areas.json                area code -> name
  data/<pref>/pages/<type>/<area>.jsonl  each listing's page content (id, img, texts, env, agent, dates), from its
                                          page record; pages/removed/<type>.jsonl for the saved ended ones
  data/events/<run_id>.json             a run's events (an interrupted run's: at the start of the next one)

One file per type and area keeps files small (a whole prefecture in one file passes GitHub's 100 MB limit) and
a day's changes readable by ward. Files are only rewritten when their content changes.

Output is deterministic: an unchanged listing produces an identical line, so an unchanged day changes
nothing. Fields that change on their own (last_seen, missed) stay in the DB.
"""
import hashlib
import json
import re
import shutil
from pathlib import Path

from .parse import TYPES, clean
from .scope import PREF_BBOX, PREFS, by_key
from .util import write_atomic

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
    "url", "image", "photos", "lat", "lng", "page_date", "first_seen", "removed_at", "has_detail", "dup_key",
]
PAGE_KEYS = ("img", "texts", "env", "agent", "dates")  # a page record's parts in the page files


def merge(row):
    """One record from the DB row: list-page fields (fresh daily) over listing-page fields. has_detail: the listing
    has its page's fields, from a page record (page_json; "fetched" everywhere else) or, for a state seeded from
    data/ whose records aren't fetched again yet, its seeded listing-page fields (detail_json)."""
    lst = json.loads(row["list_json"])
    det = json.loads(row["detail_json"]) if row["detail_json"] else {}
    rec = {**det, **lst}
    if det.get("stations"):                     # the listing page lists up to 3 stations, search results 1
        rec["stations"] = det["stations"]
    rec.update(id=row["id"], type=row["type"], area_code=row["area_code"], area=row["area_name"],
               url="https://suumo.jp" + rec.pop("path"), first_seen=row["first_seen"],
               removed_at=row["removed_at"][:10] if row["removed_at"] else None,
               has_detail=bool(row["page_json"] or det) or None,
               page_date=row["fetched_at"][:10] if row["fetched_at"] else None)
    if "lat" in rec and not _in_pref(row["pref"], rec["lat"], rec["lng"]):
        del rec["lat"], rec["lng"]
    rec["dup_key"] = dup_key(rec)
    ordered = {k: rec[k] for k in FIELD_ORDER if k in rec}
    ordered.update({k: rec[k] for k in sorted(rec) if k not in ordered})
    return clean(ordered)


def _in_pref(pref, lat, lng):
    box = PREF_BBOX.get(pref)
    return box is None or (box[0] <= lat <= box[2] and box[1] <= lng <= box[3])


def page_line(row):
    """The page file's line for a listing with a page record, else None."""
    if not row["page_json"]:
        return None
    page = json.loads(row["page_json"])
    return clean({"id": row["id"], **{k: page.get(k) for k in PAGE_KEYS}})


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


def _write_json(path, obj):
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _write_lines(path, records):
    _write(path, "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records))


def _sync_dir(directory, files):
    """Write {name: records} as directory/<name>.jsonl and delete the directory's other .jsonl files."""
    for name, records in files.items():
        _write_lines(directory / f"{name}.jsonl", records)
    _prune_dir(directory, files)


def _prune_dir(directory, keep):
    """Delete directory's .jsonl files whose name isn't in keep."""
    if directory.exists():
        for p in directory.glob("*.jsonl"):
            if p.stem not in keep:
                p.unlink()


def export(db, targets, data_dir, run_id=None):
    data_dir = Path(data_dir)
    scope = by_key(targets)
    stats = {}
    for pref in sorted({t.pref for t in targets}):
        removed, removed_pages = {}, {}
        for type_key in TYPES:
            target = scope.get((pref, type_key))
            if target is None:
                shutil.rmtree(data_dir / pref / type_key, ignore_errors=True)
                shutil.rmtree(data_dir / pref / "pages" / type_key, ignore_errors=True)
                continue
            # one area at a time (a ward's rows with their page records, not a whole prefecture's: memory)
            codes = [r[0] for r in db.x("SELECT DISTINCT area_code FROM listings WHERE pref=? AND type=? "
                                        "ORDER BY area_code", pref, type_key) if target.includes(r[0])]
            areas, area_pages, gone, n = set(), set(), [], 0
            for code in codes:
                recs, lines = [], []
                for r in db.x("SELECT * FROM listings WHERE pref=? AND type=? AND area_code=? "
                              "ORDER BY CAST(id AS INTEGER)", pref, type_key, code):
                    line = page_line(r)
                    if r["status"] == "active":
                        recs.append(merge(r))
                        if line:
                            lines.append(line)
                    else:
                        gone.append((int(r["id"]), merge(r), line))
                if recs:
                    areas.add(code)
                    _write_lines(data_dir / pref / type_key / f"{code}.jsonl", recs)
                if lines:
                    area_pages.add(code)
                    _write_lines(data_dir / pref / "pages" / type_key / f"{code}.jsonl", lines)
                n += len(recs)
            stats[f"{pref}/{type_key}"] = n
            _prune_dir(data_dir / pref / type_key, areas)
            _prune_dir(data_dir / pref / "pages" / type_key, area_pages)
            if gone:  # the saved ended ones: one file per type, sorted by id across areas
                gone.sort(key=lambda g: g[0])
                removed[type_key] = [rec for _, rec, _ in gone]
                if (lines := [line for _, _, line in gone if line]):
                    removed_pages[type_key] = lines
        _sync_dir(data_dir / pref / "removed", removed)
        _sync_dir(data_dir / pref / "pages" / "removed", removed_pages)
        areas = {a["code"]: a["name"] for a in db.x("SELECT code, name FROM areas WHERE pref=? ORDER BY code", pref)}
        _write_json(data_dir / pref / "areas.json", areas)

    if run_id:
        export_events(db, data_dir, run_id)
    return stats


def prefectures(data_dir):
    """The prefectures data/ holds, in name order (folders named after one: not events/, canary/)."""
    return sorted(p.name for p in Path(data_dir).iterdir() if p.is_dir() and p.name in PREFS)


def records(data_dir, pref, removed=True):
    """The records data/ holds for a prefecture, the one reader of its .jsonl files: each type's (TYPES order,
    area files sorted), then the ended ones kept because saved (removed/), unless removed=False."""
    d = Path(data_dir) / pref
    files = [p for t in TYPES for p in sorted((d / t).glob("*.jsonl"))]
    for path in files + (sorted((d / "removed").glob("*.jsonl")) if removed else []):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


_page_id = re.compile(r'(?<!\\)"id":\s*"(\d+)"')


def pages(data_dir, pref):
    """The page records data/ holds for a prefecture (data/<pref>/pages/<type>/*.jsonl and pages/removed/): "type:id"
    -> the line as written (JSON of {id, img, texts, env, agent, dates}), parsed by the caller one at a time (a
    prefecture's parsed pages would take gigabytes). The site build reads them; nothing else does."""
    d, out = Path(data_dir) / pref / "pages", {}
    for path in sorted(d.glob("*/*.jsonl")):
        type_ = path.stem if path.parent.name == "removed" else path.parent.name
        with open(path, encoding="utf-8") as f:
            for line in f:
                if m := _page_id.search(line):  # (only the top-level object has an "id" key)
                    out[f"{type_}:{m.group(1)}"] = line
    return out


def export_events(db, data_dir, run_id):
    """data/events/<run_id>.json: the run's events (no file for a run without any)."""
    events = [dict(e) for e in db.x("SELECT seq, kind, type, id, pref, area_code, payload FROM events "
                                    "WHERE run_id=? ORDER BY seq", run_id)]
    if events:
        for e in events:
            e["payload"] = json.loads(e["payload"])
        _write_json(Path(data_dir) / "events" / f"{run_id}.json", events)


def export_interrupted(db, data_dir):
    """Runs that never finished (killed, crashed, interrupted): their committed events are exported now (the site's
    新着/値下げ come from data/events/; unchanged files aren't rewritten) and their reports marked interrupted.
    Returns the runs newly marked (an interrupted run marks itself)."""
    marked = []
    for r in db.x("SELECT run_id, report FROM runs WHERE finished IS NULL ORDER BY run_id").fetchall():
        export_events(db, data_dir, r["run_id"])
        report = json.loads(r["report"] or "{}")
        if not report.get("interrupted"):
            db.x("UPDATE runs SET report=? WHERE run_id=?",
                 json.dumps({**report, "interrupted": True}, ensure_ascii=False), r["run_id"])
            marked.append(r["run_id"])
    db.commit()
    return marked
