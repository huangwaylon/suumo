"""Build the static search site (GitHub Pages) from data/ and geo/towns.json.

  <out>/index.html, app.js, filter.js, style.css   copied from site/
  <out>/data/index.json                            every active listing, search fields only (columnar)
  <out>/data/l/<type>/<id>.json                    one listing's full record, price history, other agents

The derived search fields come from catalog.Snapshot, so the site filters by the same rules as the catalog;
tests/test_site.py runs the same queries through site/filter.js (Node) and the catalog and compares.
The build output is not committed: GitHub Actions builds and deploys it on every push.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .catalog import NEW_TYPES, QUAKE_CODE, load
from .geo import load_cache, town_of
from .parse import TYPES

JST = ZoneInfo("Asia/Tokyo")
STATIC = Path(__file__).resolve().parent.parent / "site"
IMAGE_PREFIX = "https://img01.suumo.com/jj/resizeImage?src="

# flags bitmask in index rows
LEASEHOLD, CONDITIONAL, DETAIL, POST_1981, NEW, DROPPED = 1, 2, 4, 8, 16, 32
COLUMNS = ["id", "type", "area", "price", "price_max", "rooms", "size", "land", "age", "built", "stations",
           "features", "flags", "dup", "new_date", "first_seen", "unit", "town", "name", "layout", "image"]


def _image(url):
    """Keep only SUUMO's image path (the resize URL around it is rebuilt in the browser)."""
    if not url or not url.startswith(IMAGE_PREFIX):
        return url
    return parse_qs(urlparse(url).query).get("src", [None])[0]


def _date_int(s):
    return int(s.replace("-", "")) if s else 0


class Table:
    """value -> small integer, so repeated strings are stored once."""

    def __init__(self):
        self.index, self.values = {}, []

    def __call__(self, value):
        if value not in self.index:
            self.index[value] = len(self.values)
            self.values.append(value)
        return self.index[value]


def build_index(snap, towns_cache, updated):
    types, areas, stations, lines, features, towns, dups = (Table() for _ in range(7))
    for t in TYPES:
        types(t)
    line_stations = {}
    rows = []
    for i in sorted(snap.items, key=lambda i: (i.type, i.idn)):
        r = i.rec
        flags = (LEASEHOLD * i.leasehold | CONDITIONAL * i.conditional | DETAIL * i.has_detail
                 | POST_1981 * (i.type in NEW_TYPES or bool(i.built and i.built >= QUAKE_CODE))
                 | NEW * (snap.new_dates.get(i.key, "") >= snap.new_cutoff) | DROPPED * (i.key in snap.dropped))
        st = []
        for name, line, walk in i.stations:
            st.append([stations(name), walk])
            if line:
                line_stations.setdefault(line, set()).add(stations(name))
        town = town_of(r.get("address"), "tokyo")
        rows.append([
            r["id"], types(i.type), areas(i.area), i.price_lo, i.price_hi if i.price_hi != i.price_lo else None,
            sum(1 << (min(n, 4) - 1) for n in i.rooms) if i.type != "land" else 0,
            i.size, i.land, i.age, i.built, st, sorted(features(f) for f in i.features), flags,
            dups(i.dup) if i.dup and len(snap.groups.get(i.dup, ())) > 1 else None,
            _date_int(snap.new_dates.get(i.key)), _date_int(i.first_seen),
            round(i.unit_price) if i.unit_price else None,
            towns(town) if town else None, r.get("name") or r.get("title"), r.get("layout"), _image(r.get("image")),
        ])
    for line in sorted(line_stations):
        lines(line)
    return {
        "updated": updated, "columns": COLUMNS, "rows": rows,
        "types": types.values,
        "areas": [[code, snap.areas.get(code, code)] for code in areas.values],
        "stations": stations.values,
        "lines": {line: sorted(line_stations[line]) for line in lines.values},
        "features": features.values,
        "towns": [[t, *(towns_cache.get(t) or [None, None])] for t in towns.values],
    }


def listing_record(snap, item):
    rec = item.rec
    others = [o for o in snap.groups.get(item.dup, []) if o is not item] if item.dup else []
    rec["history"] = snap.history.get(item.key, [])
    rec["others"] = [{"type": o.type, "id": o.rec["id"], "price": o.price_lo, "agent": o.rec.get("agent"),
                      "url": o.rec.get("url")} for o in others]
    rec["new_date"] = snap.new_dates.get(item.key)
    return rec


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build(data_dir, geo_cache, out_dir, today=None, log=print):
    today = today or datetime.now(JST).date()
    out = Path(out_dir)
    snap = load(data_dir, today)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(STATIC, out)
    (out / ".nojekyll").write_text("")
    updated = max((p.stat().st_mtime for p in Path(data_dir).glob("*/*/*.jsonl")), default=0)
    index = build_index(snap, load_cache(geo_cache),
                        datetime.fromtimestamp(updated, JST).strftime("%Y-%m-%d %H:%M") if updated else "")
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "index.json").write_text(_dump(index), encoding="utf-8")
    for item in snap.items:
        path = out / "data" / "l" / item.type / f"{item.rec['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(listing_record(snap, item)), encoding="utf-8")
    size = (out / "data" / "index.json").stat().st_size
    log(f"site: {len(snap.items):,} listings, index {size / 1e6:.1f} MB, written to {out}")
    return index
