"""Build the static search site (GitHub Pages) from data/ and geo/towns.json.

  <out>/index.html, app.js, filter.js, style.css   copied from site/
  <out>/data/index.json                            every active listing, search fields only (columnar)
  <out>/data/l/<type>/<id>.json                    one listing's full record, price history, other agents

The tricky fields (rooms, sizes, age, walk times, flags, duplicate groups) are computed here from catalog.Item,
so site/filter.js only compares values. The output is not committed: GitHub Actions builds it on every push.
"""
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .catalog import load
from .geo import load_cache, town_of
from .parse import TYPES

JST = ZoneInfo("Asia/Tokyo")
STATIC = Path(__file__).resolve().parent.parent / "site"
IMAGE_PREFIX = "https://img01.suumo.com/jj/resizeImage?src="
FLAGS = {"leasehold": 1, "conditional": 2, "post1981": 4, "new": 8, "dropped": 16}
COLUMNS = ["id", "type", "area", "price", "price_max", "rooms", "size", "land", "age", "built", "stations",
           "features", "flags", "dup", "new_date", "first_seen", "unit", "town", "name", "layout", "image"]

_image_path = re.compile(r"^gazo/bukken/([^/]+)/([^/]+)/img/([^/]+)/(\d+)/\4_([^/]+)$")


def _image(url, lid):
    """'…?src=gazo/bukken/030/N010000/img/670/<id>/<id>_0004.jpg&w=…' -> '030/N010000/670/0004.jpg' (app.js
    rebuilds it with the id); other forms keep their path, or the whole URL."""
    if not url or not url.startswith(IMAGE_PREFIX):
        return url
    path = parse_qs(urlparse(url).query).get("src", [""])[0]
    m = _image_path.match(path)
    return "/".join(m.group(1, 2, 3, 5)) if m and m.group(4) == lid else path


def _date_int(s):
    return int(s.replace("-", "")) if s else 0


class Table:
    """value -> small integer, so repeated strings are stored once."""

    def __init__(self, values=()):
        self.index, self.values = {}, []
        for v in values:
            self(v)

    def __call__(self, value):
        if value not in self.index:
            self.index[value] = len(self.values)
            self.values.append(value)
        return self.index[value]


def build_index(snap, towns_cache, updated):
    types, areas, stations, features, towns, dups = Table(TYPES), Table(), Table(), Table(), Table(), Table()
    lines = {}
    rows = []
    for i in sorted(snap.items, key=lambda i: (i.type, i.idn)):
        r = i.rec
        flags = (FLAGS["leasehold"] * i.leasehold | FLAGS["conditional"] * i.conditional
                 | FLAGS["post1981"] * i.post_1981 | FLAGS["new"] * snap.is_new(i)
                 | FLAGS["dropped"] * snap.is_dropped(i))
        for name, line, _ in i.stations:
            if line:
                lines.setdefault(line, set()).add(stations(name))
        town = town_of(r.get("address"), "tokyo")
        rows.append([
            r["id"], types(i.type), areas(i.area), i.price_lo, i.price_hi if i.price_hi != i.price_lo else None,
            sum(1 << (min(n, 4) - 1) for n in i.rooms), i.size, i.land, i.age, i.built,
            [[stations(name), walk] for name, _, walk in i.stations], [features(f) for f in i.features], flags,
            dups(i.dup) if i.dup in snap.groups else None,
            _date_int(snap.new_dates.get(i.key)), _date_int(i.first_seen), i.unit_price,
            towns(town) if town else None, r.get("name") or r.get("title"), r.get("layout"),
            _image(r.get("image"), r["id"]),
        ])
    return {
        "updated": updated, "flags": FLAGS, "columns": COLUMNS, "rows": rows, "types": types.values,
        "areas": [[code, snap.areas.get(code, code)] for code in areas.values],
        "stations": stations.values,
        "lines": {line: sorted(ids) for line, ids in sorted(lines.items())},
        "features": features.values,
        "towns": [[t, *(towns_cache.get(t) or [None, None])] for t in towns.values],
    }


def listing_record(snap, item):
    others = [o for o in snap.groups.get(item.dup, []) if o is not item]
    return {**item.rec, "history": snap.history.get(item.key, []), "new_date": snap.new_dates.get(item.key),
            "others": [{"price": o.price_lo, "agent": o.rec.get("agent"), "url": o.rec.get("url")} for o in others]}


def _updated(data_dir):
    """When the data last changed: the last commit touching data/ (file times are checkout times in CI)."""
    for args in (["--", str(data_dir)], []):
        out = subprocess.run(["git", "-C", str(data_dir), "log", "-1", "--format=%ct", *args],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return datetime.fromtimestamp(int(out.stdout), JST).strftime("%Y-%m-%d %H:%M")
    return ""


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build(data_dir, geo_cache, out_dir, today=None, log=print):
    snap = load(data_dir, today or datetime.now(JST).date())
    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(STATIC, out)
    (out / ".nojekyll").write_text("")
    index = build_index(snap, load_cache(geo_cache), _updated(Path(data_dir)))
    (out / "data").mkdir()
    (out / "data" / "index.json").write_text(_dump(index), encoding="utf-8")
    for item in snap.items:
        path = out / "data" / "l" / item.type / f"{item.rec['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(listing_record(snap, item)), encoding="utf-8")
    log(f"site: {len(snap.items):,} listings, index {(out / 'data/index.json').stat().st_size / 1e6:.1f} MB → {out}")
    return index
